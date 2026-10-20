"""Live Windows controller diagnostics. Uses only Windows HID/XInput APIs.

No bootloader access, firmware upload, pairing or calibration commands.
Protocol facts: irungentoo/Xiaomi_gamepad and dekuNukem/Nintendo_Switch_Reverse_Engineering.
"""
import ctypes as c
from ctypes import wintypes as w
import os
import struct


XINPUT_BUTTONS = {0x1: 'Haut', 0x2: 'Bas', 0x4: 'Gauche', 0x8: 'Droite',
                 0x10: 'Plus', 0x20: 'Moins', 0x40: 'L3', 0x80: 'R3',
                 0x100: 'L', 0x200: 'R', 0x1000: 'A', 0x2000: 'B', 0x4000: 'X', 0x8000: 'Y'}
SWITCH_BUTTONS = {0: 'Y', 1: 'X', 2: 'B', 3: 'A', 6: 'R', 7: 'ZR',
                  8: 'Moins', 9: 'Plus', 10: 'R3', 11: 'L3', 12: 'Home', 13: 'Capture',
                  16: 'Bas', 17: 'Haut', 18: 'Droite', 19: 'Gauche', 22: 'L', 23: 'ZL'}
HATS = (('Haut',), ('Haut', 'Droite'), ('Droite',), ('Droite', 'Bas'),
        ('Bas',), ('Bas', 'Gauche'), ('Gauche',), ('Gauche', 'Haut'))


def signed_axis(value):
    return value / (32768 if value < 0 else 32767)


def parse_xiaomi(report):
    if len(report) < 21 or report[0] != 4:
        return None
    bits = int.from_bytes(report[1:3], 'little')
    names = {0: 'A', 1: 'B', 3: 'X', 4: 'Y', 6: 'L', 7: 'R',
             10: 'Moins', 11: 'Plus', 13: 'L3', 14: 'R3'}
    buttons = [name for bit, name in names.items() if bits & (1 << bit)]
    if report[4] < 8:
        buttons.extend(HATS[report[4]])
    if report[20] & 1:
        buttons.append('Home')
    triggers = [report[11] / 255, report[12] / 255]
    buttons.extend(name for name, v in zip(('ZL', 'ZR'), triggers) if v >= 85/255)
    return dict(buttons=buttons, axes=[(v-128)/128 for v in report[5:9]],
                triggers=triggers, accel=struct.unpack_from('<hhh', report, 13),
                battery=report[19] if report[19] <= 100 else None, raw=report.hex(' '))


def parse_switch(report):
    if len(report) < 12 or report[0] not in (0x30, 0x21):
        return None  # Handshake/subcommand replies never fabricate input samples.
    bits = int.from_bytes(report[3:6], 'little')
    buttons = [name for bit, name in SWITCH_BUTTONS.items() if bits & (1 << bit)]
    axes = []
    for offset in (6, 9):
        x = report[offset] | (report[offset+1] & 15) << 8
        y = (report[offset+1] >> 4) | report[offset+2] << 4
        axes.extend(((x-2048)/2048, -(y-2048)/2048))
    return dict(buttons=buttons, axes=axes, triggers=[float('ZL' in buttons), float('ZR' in buttons)],
                accel=struct.unpack_from('<hhh', report, 13) if report[0] == 0x30 and len(report) >= 49 else None,
                battery=None, raw=report.hex(' '))


def switch_command(counter, command, value):
    return bytes([1, counter & 15]) + bytes.fromhex('0001404000014040') + bytes([command, value])


def switch_rumble(counter, small, big):
    # Fixed carrier, variable HD-rumble amplitude. This tests the adapter's
    # conversion to ordinary motors; Xiaomi does not have HD-rumble actuators.
    def motor(level):
        amplitude = round(max(0, min(1, level)) * 100) * 2
        return bytes((0, 1 + amplitude, 0x40, 0x40))
    return bytes([0x10, counter & 15]) + motor(big) + motor(small)


class Gamepad(c.Structure):
    _fields_ = [('buttons', w.WORD), ('lt', c.c_ubyte), ('rt', c.c_ubyte),
                ('lx', c.c_short), ('ly', c.c_short), ('rx', c.c_short), ('ry', c.c_short)]


class XState(c.Structure):
    _fields_ = [('packet', w.DWORD), ('pad', Gamepad)]


class Vibration(c.Structure):
    _fields_ = [('left', w.WORD), ('right', w.WORD)]


class XInput:
    def __init__(self, index):
        self.index = index
        self.api = c.WinDLL('xinput1_4.dll')
        self.api.XInputGetState.argtypes = [w.DWORD, c.POINTER(XState)]
        self.api.XInputSetState.argtypes = [w.DWORD, c.POINTER(Vibration)]

    def read(self):
        state = XState()
        if self.api.XInputGetState(self.index, c.byref(state)) != 0:
            raise OSError('Manette XInput déconnectée : allumez-la et connectez-la à l’adaptateur.')
        p = state.pad
        buttons = [name for bit, name in XINPUT_BUTTONS.items() if p.buttons & bit]
        buttons.extend(name for name, v in (('ZL', p.lt), ('ZR', p.rt)) if v >= 85)
        return dict(buttons=buttons, axes=[signed_axis(p.lx), -signed_axis(p.ly),
                    signed_axis(p.rx), -signed_axis(p.ry)], triggers=[p.lt/255, p.rt/255],
                    accel=None, battery=None, raw='')

    def rumble(self, small, big):
        value = Vibration(round(big*65535), round(small*65535))
        if self.api.XInputSetState(self.index, c.byref(value)) != 0:
            raise OSError('Commande de vibration refusée : manette XInput déconnectée.')

    def close(self):
        self.rumble(0, 0)


class Guid(c.Structure):
    _fields_ = [('a', w.DWORD), ('b', w.WORD), ('d', w.WORD), ('data', c.c_ubyte*8)]


class Interface(c.Structure):
    _fields_ = [('size', w.DWORD), ('guid', Guid), ('flags', w.DWORD), ('reserved', c.c_size_t)]


class Attributes(c.Structure):
    _fields_ = [('size', w.DWORD), ('vid', w.WORD), ('pid', w.WORD), ('version', w.WORD)]


class Caps(c.Structure):
    _fields_ = [('usage', w.WORD), ('page', w.WORD), ('input', w.WORD), ('output', w.WORD),
                ('feature', w.WORD), ('reserved', w.WORD*17), ('counts', w.WORD*10)]


class Overlapped(c.Structure):
    _fields_ = [('internal', c.c_size_t), ('high', c.c_size_t), ('offset', w.DWORD),
                ('offset_high', w.DWORD), ('event', w.HANDLE)]


class HidApi:
    def __init__(self):
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.h = c.WinDLL('hid', use_last_error=True)
        self.s = c.WinDLL('setupapi', use_last_error=True)
        def signature(lib, name, args, result=w.BOOL):
            f = getattr(lib, name); f.argtypes = args; f.restype = result
        p = c.c_void_p
        signature(self.k, 'CreateFileW', [w.LPCWSTR, w.DWORD, w.DWORD, p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE)
        signature(self.k, 'CloseHandle', [w.HANDLE])
        signature(self.k, 'CreateEventW', [p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE)
        signature(self.k, 'ReadFile', [w.HANDLE, p, w.DWORD, p, c.POINTER(Overlapped)])
        signature(self.k, 'WriteFile', [w.HANDLE, p, w.DWORD, p, c.POINTER(Overlapped)])
        signature(self.k, 'WaitForSingleObject', [w.HANDLE, w.DWORD], w.DWORD)
        signature(self.k, 'GetOverlappedResult', [w.HANDLE, c.POINTER(Overlapped), c.POINTER(w.DWORD), w.BOOL])
        signature(self.k, 'CancelIoEx', [w.HANDLE, c.POINTER(Overlapped)])
        signature(self.h, 'HidD_GetHidGuid', [c.POINTER(Guid)], None)
        signature(self.h, 'HidD_GetAttributes', [w.HANDLE, c.POINTER(Attributes)], c.c_ubyte)
        signature(self.h, 'HidD_GetPreparsedData', [w.HANDLE, c.POINTER(p)], c.c_ubyte)
        signature(self.h, 'HidD_FreePreparsedData', [p], c.c_ubyte)
        signature(self.h, 'HidP_GetCaps', [p, c.POINTER(Caps)], w.LONG)
        signature(self.h, 'HidD_GetManufacturerString', [w.HANDLE, p, w.DWORD], c.c_ubyte)
        signature(self.h, 'HidD_SetFeature', [w.HANDLE, p, w.DWORD], c.c_ubyte)
        signature(self.s, 'SetupDiGetClassDevsW', [c.POINTER(Guid), p, p, w.DWORD], w.HANDLE)
        signature(self.s, 'SetupDiEnumDeviceInterfaces', [w.HANDLE, p, c.POINTER(Guid), w.DWORD, c.POINTER(Interface)])
        signature(self.s, 'SetupDiGetDeviceInterfaceDetailW', [w.HANDLE, c.POINTER(Interface), p, w.DWORD, c.POINTER(w.DWORD), p])
        signature(self.s, 'SetupDiDestroyDeviceInfoList', [w.HANDLE])

    def open(self, path, access):
        handle = self.k.CreateFileW(path, access, 3, None, 3, 0x40000000 if access else 0, None)
        if handle == w.HANDLE(-1).value:
            raise c.WinError(c.get_last_error())
        return handle

    def metadata(self, handle):
        attrs, caps, data = Attributes(c.sizeof(Attributes)), Caps(), c.c_void_p()
        if not self.h.HidD_GetAttributes(handle, c.byref(attrs)):
            raise c.WinError(c.get_last_error())
        if not self.h.HidD_GetPreparsedData(handle, c.byref(data)):
            raise c.WinError(c.get_last_error())
        try:
            if self.h.HidP_GetCaps(data, c.byref(caps)) != 0x110000:
                raise OSError('Descripteur HID illisible.')
        finally:
            self.h.HidD_FreePreparsedData(data)
        vendor = c.create_unicode_buffer(128)
        self.h.HidD_GetManufacturerString(handle, vendor, c.sizeof(vendor))
        return attrs, caps, vendor.value

    def devices(self):
        guid = Guid(); self.h.HidD_GetHidGuid(c.byref(guid))
        handle = self.s.SetupDiGetClassDevsW(c.byref(guid), None, None, 0x12)
        if handle == w.HANDLE(-1).value:
            raise c.WinError(c.get_last_error())
        found = []
        try:
            index = 0
            while True:
                interface = Interface(c.sizeof(Interface))
                if not self.s.SetupDiEnumDeviceInterfaces(handle, None, c.byref(guid), index, c.byref(interface)):
                    if c.get_last_error() != 259:
                        raise c.WinError(c.get_last_error())
                    break
                index += 1
                size = w.DWORD()
                self.s.SetupDiGetDeviceInterfaceDetailW(handle, c.byref(interface), None, 0, c.byref(size), None)
                if not 6 <= size.value <= 65536:
                    continue
                detail = c.create_string_buffer(size.value)
                c.cast(detail, c.POINTER(w.DWORD))[0] = 8 if c.sizeof(c.c_void_p) == 8 else 6
                if not self.s.SetupDiGetDeviceInterfaceDetailW(handle, c.byref(interface), detail, size, c.byref(size), None):
                    continue
                path = c.wstring_at(c.addressof(detail)+4)
                if not any(token in path.lower() for token in ('2717', '2dc8', 'vid_057e&pid_2009', 'vid_0483&pid_5750')):
                    continue
                try:
                    device = self.open(path, 0)
                    try:
                        attrs, caps, manufacturer = self.metadata(device)
                    finally:
                        self.k.CloseHandle(device)
                    direct = (attrs.vid, attrs.pid) == (0x2717, 0x3144)
                    nintendo = (attrs.vid, attrs.pid) == (0x057e, 0x2009)
                    # Switch mode can use Nintendo's USB identity and strings.
                    adapter = nintendo or ((attrs.vid == 0x2dc8 or '8bitdo' in manufacturer.lower())
                                           and caps.input >= 49 and caps.output >= 12)
                    if (direct or adapter) and caps.page == 1 and caps.usage in (4, 5) and 14 <= caps.input <= 128:
                        found.append(dict(kind='xiaomi' if direct else 'switch', path=path,
                            label='Xiaomi Bluetooth · données brutes' if direct else 'Switch Pro / 8BitDo · HID Switch',
                            vid=attrs.vid, pid=attrs.pid, input=caps.input, output=caps.output, feature=caps.feature))
                except OSError:
                    continue
        finally:
            self.s.SetupDiDestroyDeviceInfoList(handle)
        return found


class HidController:
    def __init__(self, descriptor):
        self.api = HidApi()
        self.descriptor = descriptor
        self.counter = 0
        self.handle = self.api.open(descriptor['path'], 0xc0000000)
        try:
            attrs, caps, _ = self.api.metadata(self.handle)
            if (attrs.vid, attrs.pid, caps.input, caps.output, caps.feature) != tuple(
                    descriptor[k] for k in ('vid', 'pid', 'input', 'output', 'feature')):
                raise OSError('Le périphérique a changé. Actualisez la liste.')
            if descriptor['kind'] == 'xiaomi':
                self.feature(bytes((0x31, 1, 8)), tolerate_failure=True)
            else:
                for command in (2, 3, 4):
                    self.write(bytes((0x80, command)))
                for command, value in ((3, 0x30), (0x40, 1), (0x48, 1)):
                    self.command(command, value)
        except Exception:
            self.api.k.CloseHandle(self.handle)
            self.handle = None
            raise

    def transfer(self, data, writing, timeout=10):
        buffer = c.create_string_buffer(data, len(data))
        event = self.api.k.CreateEventW(None, True, False, None)
        if not event:
            raise c.WinError(c.get_last_error())
        ov = Overlapped(event=event)
        pending = False
        try:
            function = self.api.k.WriteFile if writing else self.api.k.ReadFile
            done = function(self.handle, buffer, len(data), None, c.byref(ov))
            if not done and c.get_last_error() != 997:
                raise c.WinError(c.get_last_error())
            pending = not done
            count = w.DWORD()
            if pending and self.api.k.WaitForSingleObject(event, timeout) != 0:
                self.api.k.CancelIoEx(self.handle, c.byref(ov))
                self.api.k.GetOverlappedResult(self.handle, c.byref(ov), c.byref(count), True)
                pending = False
                if writing:
                    raise OSError('Délai d’envoi HID dépassé.')
                return None
            success = self.api.k.GetOverlappedResult(self.handle, c.byref(ov), c.byref(count), True)
            pending = False
            if not success:
                raise c.WinError(c.get_last_error())
            if writing and count.value != len(data):
                raise OSError('Rapport HID incomplet.')
            return buffer.raw[:count.value]
        finally:
            if pending:
                self.api.k.CancelIoEx(self.handle, c.byref(ov))
                count = w.DWORD()
                self.api.k.GetOverlappedResult(self.handle, c.byref(ov), c.byref(count), True)
            self.api.k.CloseHandle(event)

    def write(self, report):
        size = self.descriptor['output']
        if len(report) > size or size > 128:
            raise OSError('Rapport de sortie incompatible avec cet adaptateur.')
        self.transfer(report.ljust(size, b'\0'), True, timeout=500)

    def feature(self, report, tolerate_failure=False):
        buffer = c.create_string_buffer(report, len(report))
        if not self.api.h.HidD_SetFeature(self.handle, buffer, len(report)) and not tolerate_failure:
            raise c.WinError(c.get_last_error())

    def command(self, command, value):
        self.write(switch_command(self.counter, command, value))
        self.counter = (self.counter+1) & 15

    def read(self):
        report = self.transfer(bytes(self.descriptor['input']), False)
        if report is None:
            return None
        return (parse_xiaomi if self.descriptor['kind'] == 'xiaomi' else parse_switch)(report)

    def rumble(self, small, big):
        if self.descriptor['kind'] == 'xiaomi':
            self.feature(bytes((0x20, round(small*255), round(big*255))))
        else:
            self.write(switch_rumble(self.counter, small, big))
            self.counter = (self.counter+1) & 15

    def close(self):
        if self.handle is None:
            return
        try:
            self.rumble(0, 0)
            if self.descriptor['kind'] == 'xiaomi':
                self.feature(bytes((0x31, 0, 0)), tolerate_failure=True)
            else:
                self.command(0x40, 0)
        finally:
            self.api.k.CloseHandle(self.handle)
            self.handle = None


def enumerate_controllers():
    if os.name != 'nt':
        return []
    found = []
    for index in range(4):
        try:
            XInput(index).read()
            found.append(dict(kind='xinput', index=index, label=f'XInput {index+1} · commandes Xbox'))
        except OSError:
            pass
    found.extend(HidApi().devices())
    return found


def open_controller(descriptor):
    return XInput(descriptor['index']) if descriptor['kind'] == 'xinput' else HidController(descriptor)
