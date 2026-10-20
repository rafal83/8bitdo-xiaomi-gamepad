"""Controller protocols, motor stop lifecycle and feedback ARM hooks. No real I/O."""
import ctypes
import json
from copy import deepcopy
from pathlib import Path
import struct
import sys
import tempfile
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import controller_io as io
import custom_firmware as firmware
from configure_xiaomi import Configurator
from validate_custom_firmware import validate_image


class ProtocolTests(unittest.TestCase):
    def test_xiaomi_signed_sensors_and_buttons(self):
        packet = bytearray(21)
        packet[0] = 4; packet[1] = 1; packet[4] = 7
        packet[5:9] = bytes((0, 128, 255, 128))
        packet[11:13] = bytes((85, 84)); packet[19] = 73; packet[20] = 1
        struct.pack_into('<hhh', packet, 13, -32768, 32767, -1234)
        sample = io.parse_xiaomi(packet)
        self.assertEqual(sample['accel'], (-32768, 32767, -1234))
        self.assertEqual(set(sample['buttons']), {'A', 'Haut', 'Gauche', 'ZL', 'Home'})
        self.assertEqual(sample['battery'], 73)
        self.assertEqual(sample['axes'][:2], [-1, 0])
        self.assertIsNone(io.parse_xiaomi(packet[:20]))
        packet[0] = 0x31
        self.assertIsNone(io.parse_xiaomi(packet))

    def test_switch_reports_and_control_replies(self):
        report = bytearray(64); report[0] = 0x30
        report[3:6] = bytes((8, 0x10, 0x80))  # A, Home, ZL.
        report[6:12] = bytes.fromhex('008880008880')
        struct.pack_into('<hhh', report, 13, -1, 4096, -8192)
        sample = io.parse_switch(report)
        self.assertEqual(set(sample['buttons']), {'A', 'Home', 'ZL'})
        self.assertEqual(sample['accel'], (-1, 4096, -8192))
        self.assertEqual(sample['axes'], [0, -8/2048, 0, -8/2048])
        self.assertEqual(sample['triggers'], [1, 0])
        report[0] = 0x21
        self.assertIsNone(io.parse_switch(report)['accel'])
        for report_id in (0x81, 0x04, 0x10):
            report[0] = report_id
            self.assertIsNone(io.parse_switch(report))

    def test_switch_outputs_and_counter(self):
        command = io.switch_command(17, 0x40, 1)
        self.assertEqual(command.hex(), '010100014040000140404001')
        self.assertEqual(io.switch_rumble(16, 0, 0).hex(), '10000001404000014040')
        self.assertNotEqual(io.switch_rumble(1, 0.25, 0)[6:10], bytes.fromhex('00014040'))
        self.assertEqual(io.switch_rumble(1, 0.25, 0)[2:6], bytes.fromhex('00014040'))
        for value in (-32768, -32767, 0, 32767):
            self.assertTrue(-1 <= io.signed_axis(value) <= 1)

    def test_windows_structure_sizes(self):
        self.assertEqual(ctypes.sizeof(io.Caps), 64)
        self.assertEqual(ctypes.sizeof(io.Attributes), 12)
        self.assertEqual(ctypes.sizeof(io.Interface), 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(ctypes.sizeof(io.Overlapped), 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20)

    def test_hid_timeout_cancels_and_drains_without_retry(self):
        calls = []
        class Kernel:
            def CreateEventW(self, *args): return 12
            def ReadFile(self, *args):
                calls.append('read'); ctypes.set_last_error(997); return False
            WriteFile = ReadFile
            def WaitForSingleObject(self, *args): return 258
            def CancelIoEx(self, *args): calls.append('cancel'); return True
            def GetOverlappedResult(self, handle, ov, count, wait):
                self.waited = wait; calls.append('drain'); return False
            def CloseHandle(self, *args): calls.append('close'); return True
        device = io.HidController.__new__(io.HidController)
        device.api = SimpleNamespace(k=Kernel()); device.handle = 9
        self.assertIsNone(device.transfer(bytes(64), False))
        self.assertEqual(calls, ['read', 'cancel', 'drain', 'close'])
        self.assertTrue(device.api.k.waited)
        calls.clear()
        with self.assertRaises(OSError):
            device.transfer(bytes(64), True)
        self.assertEqual(calls, ['read', 'cancel', 'drain', 'close'])

    def test_hid_complete_read_preserves_report_id(self):
        sample = bytes([0x30])+bytes(range(1, 64))
        class Kernel:
            def CreateEventW(self, *args): return 12
            def ReadFile(self, handle, buffer, size, count, ov):
                ctypes.memmove(buffer, sample, len(sample)); return True
            def GetOverlappedResult(self, handle, ov, count, wait):
                count._obj.value = len(sample); return True
            def CloseHandle(self, *args): return True
        device = io.HidController.__new__(io.HidController)
        device.api = SimpleNamespace(k=Kernel()); device.handle = 9
        self.assertEqual(device.transfer(bytes(64), False), sample)


class FirmwareTests(unittest.TestCase):
    def test_switch_gain_range_and_validation(self):
        for invalid in (99, 301, 199, 200.0, True, '200', None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                firmware.validate_profile(dict(firmware.DEFAULT_PROFILE, switch_rumble_gain=invalid))
        for gain in range(100, 301, 25):
            with self.subTest(gain=gain):
                profile = dict(firmware.DEFAULT_PROFILE, accelerometer=False, switch_rumble_gain=gain)
                image, _, _ = firmware.compile_profile(profile)
                validation = validate_image(image, profile)
                self.assertEqual(validation['status'], 'offline_passed')
                if gain != 100:
                    self.assertEqual(validation['checks']['Switch_gain_all_levels_independent_and_saturated'], 256)
                    self.assertEqual(validation['checks']['Switch_gain_other_controllers_unchanged'], 5)

    def test_profile_without_gain_retains_original_strength_and_validates(self):
        profile = firmware.load_profile(firmware.ROOT / 'profiles/xiaomi_lettres.json')
        profile.pop('switch_rumble_gain')
        self.assertEqual(firmware.validate_profile(profile)['switch_rumble_gain'], 100)
        with tempfile.TemporaryDirectory(prefix='8bitdo_old_feedback_') as directory:
            with patch.object(firmware, 'BUNDLE_ROOT', Path(directory)):
                folder, validation = firmware.generate_bundle(profile)
                self.assertEqual((folder / 'firmware.dat').read_bytes(),
                                 firmware.compile_profile(dict(profile, switch_rumble_gain=100))[1])
                manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                manifest['profile'].pop('switch_rumble_gain')
                firmware.write_json(folder / 'manifest.json', manifest)
                firmware.write_json(folder / 'profile.json', profile)
                self.assertEqual(firmware.verify_bundle(folder / 'manifest.json')['status'], 'verified')

    def test_legacy_profile_and_default_hash_unchanged(self):
        self.assertTrue(firmware.DEFAULT_PROFILE['accelerometer'])
        self.assertTrue(firmware.DEFAULT_PROFILE['rumble'])
        for filename in ('xiaomi_v2.json', 'xiaomi_lettres.json'):
            example = firmware.load_profile(firmware.ROOT / 'profiles' / filename)
            self.assertTrue(example['accelerometer'] and example['rumble'])
        profile = firmware.load_profile(firmware.ROOT / 'profiles/xiaomi_lettres.json')
        profile.pop('accelerometer'); profile.pop('rumble')
        legacy = firmware.validate_profile(profile)
        self.assertFalse(legacy['accelerometer'] or legacy['rumble'])
        _, dat, _ = firmware.compile_profile(profile)
        self.assertEqual(firmware.digest(dat), '87c30630799b873073d7b80bfc9c2949408f3f4bce427dedcc18bbc9b93ff278')
        for name in ('accelerometer', 'rumble'):
            invalid = dict(firmware.DEFAULT_PROFILE, **{name: 1})
            with self.assertRaises(ValueError):
                firmware.validate_profile(invalid)

    def test_all_feedback_combinations_and_advanced_remap(self):
        for accel, rumble, advanced in ((True, False, False), (False, True, False),
                                        (True, True, False), (True, True, True)):
            with self.subTest(accel=accel, rumble=rumble, advanced=advanced):
                profile = deepcopy(firmware.DEFAULT_PROFILE)
                profile.update(accelerometer=accel, rumble=rumble)
                if advanced:
                    profile['buttons'].update(ZL='Home', ZR='A', Mi='Capture')
                    profile.update(invert_axes=list(firmware.AXES), swap_sticks=True,
                                   mi_pulse_ms=513, battery=False, reconnect=False)
                image, dat, journal = firmware.compile_profile(profile)
                spans = set()
                for entry in journal['edits']:
                    offset = int(entry['offset'], 16)
                    span = set(range(offset, offset+entry['length']))
                    self.assertFalse(spans & span)
                    self.assertEqual(image[offset:offset+entry['length']].hex(), entry['new'])
                    spans |= span
                self.assertEqual((len(image), len(dat)), (101376, 101404))
                self.assertEqual(struct.unpack_from('<III', dat), (207, 0x08003400, len(image)))
                validation = validate_image(image, profile)
                self.assertEqual(validation['status'], 'offline_passed')
                self.assertFalse(validation['hardware_validated'])
                print(f"ARM feedback {accel}/{rumble}/{advanced}: {validation['total_checks']} checks", flush=True)

    def test_extended_bundle_verified_and_tamper_rejected(self):
        with tempfile.TemporaryDirectory(prefix='8bitdo_feedback_') as directory:
            with patch.object(firmware, 'BUNDLE_ROOT', Path(directory)):
                profile = dict(firmware.DEFAULT_PROFILE, accelerometer=True, rumble=True)
                folder, _ = firmware.generate_bundle(profile)
                self.assertEqual(firmware.verify_bundle(folder / 'manifest.json')['status'], 'verified')
                self.assertIn('reglages memorises', (folder / 'README.txt').read_text(encoding='utf-8'))
                data = bytearray((folder / 'firmware.dat').read_bytes()); data[-1] ^= 1
                (folder / 'firmware.dat').write_bytes(data)
                with self.assertRaises(ValueError):
                    firmware.verify_bundle(folder / 'manifest.json')


class FakeController:
    def __init__(self):
        self.commands = []
        self.closed = False
        self.disconnected = False

    def read(self):
        if self.disconnected:
            raise OSError('disconnected')
        return dict(buttons=['A', 'ZL'], axes=[-1, 0, 0.5, 0], triggers=[1, 0],
                    accel=(-1, 4096, 7), battery=80, raw='')

    def rumble(self, small, big):
        self.commands.append((small, big))

    def close(self):
        self.commands.append((0, 0)); self.closed = True


class TesterUITests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk(); self.root.withdraw()
        self.app = Configurator(self.root)
        self.panel = self.app.tester
        self.fake = FakeController()
        self.spawn = patch('controller_test_ui.open_controller', return_value=self.fake).start()
        self.scan = patch('controller_test_ui.enumerate_controllers', return_value=[dict(kind='switch', label='Mock HID')]).start()
        self.panel.refresh()

    def tearDown(self):
        self.app.close()
        patch.stopall()

    def test_render_real_input_not_profile_preview(self):
        self.app.buttons['A'].set('B')
        self.app.show_page(4); self.panel.start()
        self.assertEqual(self.panel.button_labels['A']['bg'], '#087F72')
        self.assertNotEqual(self.panel.button_labels['B']['bg'], '#087F72')
        self.assertIn('+4096', self.panel.sensor.get())
        self.assertEqual(self.fake.commands, [])
        self.assertIsNone(self.app.output_folder)

    def test_motor_stops_on_timer(self):
        self.panel.start(); self.panel.pulse('small')
        self.assertEqual(self.fake.commands, [(0.25, 0)])
        deadline = time.monotonic()+1.5
        while self.panel.motor_id is not None and time.monotonic() < deadline:
            self.root.update(); time.sleep(0.01)
        self.assertIsNone(self.panel.motor_id)
        self.assertEqual(self.fake.commands[-1], (0, 0))
        self.assertIsNotNone(self.panel.session)

    def test_page_change_and_firmware_job_close_device(self):
        self.panel.start(); self.panel.pulse('both')
        self.app.show_page(0)
        self.assertTrue(self.fake.closed)
        self.assertIsNone(self.panel.poll_id)
        self.assertIsNone(self.panel.motor_id)
        self.assertEqual(self.fake.commands[-1], (0, 0))
        self.fake.closed = False
        self.panel.start()
        self.app.start_job('generation')
        self.assertTrue(self.fake.closed)
        self.assertIsNone(self.panel.session)
        self.panel.start()
        self.assertIsNone(self.panel.session)
        self.app.finish_job()

    def test_disconnect_and_invalid_intensity(self):
        self.panel.start(); self.panel.pulse('big')
        self.fake.disconnected = True
        self.root.after_cancel(self.panel.poll_id)
        self.panel.poll()
        self.assertTrue(self.fake.closed)
        self.assertIn('disconnected', self.panel.info.get())
        self.assertIsNone(self.panel.motor_id)
        self.fake.disconnected = False
        self.panel.start(); self.panel.force.set(51)
        count = len(self.fake.commands)
        self.panel.pulse('both')
        self.assertIsNone(self.panel.session)
        self.assertTrue(all(command == (0, 0) for command in self.fake.commands[count:]))

    def test_feedback_options_roundtrip(self):
        self.assertTrue(self.app.accelerometer.get())
        self.assertTrue(self.app.rumble.get())
        self.app.accelerometer.set(False); self.app.rumble.set(False)
        self.assertEqual(self.app.get_profile()['switch_rumble_gain'], 200)
        self.app.switch_rumble_gain.set('300')
        profile = self.app.get_profile()
        self.app.set_profile(firmware.DEFAULT_PROFILE)
        self.app.set_profile(profile)
        self.assertFalse(self.app.accelerometer.get() or self.app.rumble.get())
        self.assertEqual(self.app.get_profile()['switch_rumble_gain'], 300)
        legacy = deepcopy(profile); legacy.pop('switch_rumble_gain')
        self.app.set_profile(legacy)
        self.assertEqual(self.app.get_profile()['switch_rumble_gain'], 100)
        for invalid in ('', 'no', '99', '301', '199'):
            self.app.switch_rumble_gain.set(invalid)
            with self.assertRaises(ValueError):
                self.app.get_profile()


if __name__ == '__main__':
    unittest.main(verbosity=2)
