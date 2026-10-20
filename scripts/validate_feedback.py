"""Execute the optional hooks and their real call sites with Bluetooth mocked."""
from collections import Counter
import struct

from firmware_emu import *
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ
from xiaomi_feedback import SENSOR


def validate_feedback(image, profile):
    checks = Counter()
    def check(value, label):
        if not value:
            raise AssertionError('Hook capteur/vibration : '+label)
        checks[label] += 1
    e = FirmwareEmu(image)
    fixture = bytes(e.uc.mem_read(0x20000000, 0x4000))
    packets = []
    channel = 0x51
    send_error = 0
    def transport(uc, pc, size, data):
        if pc == 0x080143e8:
            check(uc.reg_read(UC_ARM_REG_R0) == 3, 'Bluetooth_HID_control_channel')
            uc.reg_write(UC_ARM_REG_R0, channel)
        elif pc == 0x08015ef8:
            check(uc.reg_read(UC_ARM_REG_R0) == channel, 'Bluetooth_CID_preserved')
            pointer, length = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
            packets.append(bytes(uc.mem_read(pointer, length)))
            uc.reg_write(UC_ARM_REG_R0, send_error)
        else:
            return
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    bt = e.uc.hook_add(UC_HOOK_CODE, transport)

    def reset(kind=24):
        e.uc.mem_write(0x20000000, fixture)
        e.uc.mem_write(0x20001222, struct.pack('<HH', kind, kind))
        packets.clear()

    def call(address, *args):
        result = e.call(address, *args)
        check(e.saved_ok and e.stack_ok, 'feedback_ABI')
        return result

    try:
        # Independently establish the control-channel index from a real stock
        # SET_REPORT(output) transaction, rather than mirroring our hook constant.
        from rebuild_dat import load_original
        stock_control = FirmwareEmu(load_original())
        stock_control.uc.mem_write(0x200010b7, b'\x02')
        control_calls, control_frames = [], []
        def original_control(uc, pc, size, data):
            if pc == 0x08014990:
                uc.reg_write(UC_ARM_REG_R0, 2)
            elif pc == 0x080143e8:
                control_calls.append(uc.reg_read(UC_ARM_REG_R0))
                uc.reg_write(UC_ARM_REG_R0, 0x51)
            elif pc == 0x08015ef8:
                control_frames.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2))))
                uc.reg_write(UC_ARM_REG_R0, 0)
            else:
                return
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        original_hook = stock_control.uc.hook_add(UC_HOOK_CODE, original_control)
        try:
            stock_control.call(0x08008ef4, 96, 32, 0)
        finally:
            stock_control.uc.hook_del(original_hook)
        check(control_calls == [3] and len(control_frames) == 1 and control_frames[0][:2] == b'\x52\x01',
              'control_channel_verified_against_stock_SET_REPORT')
        # The enlarged code must be excluded from the profile allocator bank.
        floor = struct.unpack_from('<I', image, 0x080062a4-BASE)[0]
        check(floor == BASE+len(image) == 0x0801c000, 'profile_bank_begins_after_code')
        check(struct.unpack_from('<I', image, 0x0800629c-BASE)[0] == 0x0801dc00, 'profile_bank_upper_page_unchanged')
        check(struct.unpack_from('<II', image, 0x080062ac-BASE) == (0x0801e000, 0x08020000), 'second_bank_unchanged')
        # Fill every first-bank page with a used-page marker, execute the real
        # allocator, and watch that its descending scan never enters code.
        for address in range(0x0801c000, 0x0801e000, 0x400):
            e.uc.mem_write(address, struct.pack('<II', 0xaabbccdd, 0x12345678))
        reads = []
        def bank_reads(uc, access, address, size, value, data):
            if 0x0801b000 <= address < 0x0801c000:
                reads.append(address)
        watch = e.uc.hook_add(UC_HOOK_MEM_READ, bank_reads)
        try:
            allocated = call(0x080061e0)
        finally:
            e.uc.hook_del(watch)
        check(allocated >= floor and not reads, 'real_allocator_excludes_entire_code_range')
        if profile['accelerometer']:
            reset()
            e.uc.mem_write(0x20003fc6, b'\x01')
            result, kind = e.name(b'Xiaomi Mi Bluetooth Gamepad')
            check(result == kind == 24 and bytes(e.uc.mem_read(0x20003fc6, 1)) == b'\0', 'sensor_enable_reset_on_classification')
            check(bytes(e.uc.mem_read(SENSOR, 36)) == bytes(36), 'sensor_cache_cleared_on_classification')
            packet = bytearray(22)
            packet[:2] = b'\xa1\x04'; packet[5] = 15; packet[6:10] = b'\x80'*4
            for values in ((0, 0, 0), (32767, -32768, -1), (-2100, 700, 4096)):
                packet[14:20] = struct.pack('<hhh', *values)
                e.uc.mem_write(0x20010000, bytes(packet))
                check(call(0x0800db1c, 0x20010000, 22, 0x20010400) == 1, 'sensor_packet_decoded')
                check(bytes(e.uc.mem_read(SENSOR, 36)) == (packet[14:20]+bytes(6))*3, 'signed_accel_three_samples_zero_gyro')
                for imu in (0, 1):
                    e.uc.mem_write(0x2000031c, bytes([imu]))
                    e.uc.mem_write(0x20010800, b'\xdd'*64)
                    call(0x0800a538, 0x20010800)
                    expected = (packet[14:20]+bytes(6))*3 if imu else bytes(36)
                    check(bytes(e.uc.mem_read(0x2001080e, 36)) == expected, 'Switch_IMU_data_or_disabled_zero')
                    check(bytes(e.uc.mem_read(0x20010832, 14)) == b'\xdd'*14, 'motion_buffer_bound')
            check(packets == [bytes.fromhex('53310108')], 'one_sensor_enable_per_connection')
            # Execute the real final USB sender with IMU enabled, capture the
            # actual 64-byte buffer instead of only testing the copy helper.
            reset()
            payload = struct.pack('<hhh', -1024, 2048, 4096)+bytes(6)
            e.uc.mem_write(SENSOR, payload*3)
            e.uc.mem_write(0x200010b6, b'\x03\x02')
            e.uc.mem_write(0x200005bc, b'\x01')
            e.uc.mem_write(0x200005bf, b'\x01')
            e.uc.mem_write(0x2000031c, b'\x01')
            e.uc.mem_write(0x20000044, b'\x30')
            e.uc.mem_write(0x20010800, b'\0\x30'+bytes(126))
            sent = []
            def usb(uc, pc, size, data):
                if pc in (0x08005fd0, 0x0800ba24, 0x08009f10, 0x08007aac):
                    if pc == 0x0800ba24:
                        sent.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R0), 64)))
                    uc.reg_write(UC_ARM_REG_R0, 0)
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            hook = e.uc.hook_add(UC_HOOK_CODE, usb)
            try:
                call(0x0800a74c, 0x20010800, 1, 0)
            finally:
                e.uc.hook_del(hook)
            check(len(sent) == 1 and sent[0][0] == 0x30 and sent[0][13:49] == payload*3, 'accelerometer_real_USB_transport')
            e.uc.mem_write(0x200005b8, b'\x01')
            e.uc.mem_write(0x20010800, b'\xdd'*64)
            call(0x0800a538, 0x20010800)
            check(bytes(e.uc.mem_read(0x20010800, 64)) == b'\xdd'*64, 'motion_USB_busy_guard_preserved')
            for malformed in (b'\xa1\x04'+bytes(19), b'\xa1\x03'+bytes(20)):
                reset()
                e.uc.mem_write(0x20010000, malformed)
                call(0x0800db1c, 0x20010000, len(malformed), 0x20010400)
                check(not packets and bytes(e.uc.mem_read(0x20003fc6, 1)) == b'\0', 'invalid_sensor_packet_no_command')
            for cid, error in ((0xffff, 0), (0x51, 0x57)):
                reset(); channel, send_error = cid, error
                e.uc.mem_write(0x20010000, bytes(packet))
                call(0x0800db1c, 0x20010000, 22, 0x20010400)
                check(bytes(e.uc.mem_read(0x20003fc6, 1)) == b'\0', 'sensor_failed_enable_retry_allowed')
                check(len(packets) == (0 if cid == 0xffff else 1), 'sensor_no_invalid_channel_send')
            channel, send_error = 0x51, 0
            # Original motion function remains exact for other controller types.
            from rebuild_dat import load_original
            stock = FirmwareEmu(load_original())
            for kind in (1, 10, 11, 15, 23, 0x91):
                for imu in (0, 1):
                    reset(kind)
                    stock.uc.mem_write(0x20000000, bytes(e.uc.mem_read(0x20000000, 0x4000)))
                    for device in (stock, e):
                        device.uc.mem_write(0x2000031c, bytes([imu]))
                        device.uc.mem_write(0x20010800, b'\xdd'*64)
                        device.call(0x0800a538, 0x20010800)
                    check(bytes(e.uc.mem_read(0x20010800, 64)) == bytes(stock.uc.mem_read(0x20010800, 64)), 'other_controller_motion_unchanged')

        if profile['rumble']:
            from xiaomi_feedback import RUMBLE
            for big in (0, 1, 64, 127, 128, 192, 255):
                for small in (0, 1, 64, 127, 128, 192, 255):
                    reset()
                    call(RUMBLE, big, small)
                    check(packets == [bytes((0x53, 0x20, small, big))], 'both_motor_strengths_and_zero_stop')
            reset(); channel = 0xffff
            call(RUMBLE, 128, 64)
            check(not packets, 'rumble_no_invalid_channel_send')
            channel = 0x51
            # Execute the real XInput rumble poller, including its pending flag.
            reset()
            e.uc.mem_write(0x200010b7, b'\x02')
            e.uc.mem_write(0x2000066a, struct.pack('<I', 0x80004281))
            def connected(uc, pc, size, data):
                if pc in (0x08010728, 0x08014990):
                    uc.reg_write(UC_ARM_REG_R0, 24 if pc == 0x08010728 else 2)
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            hook = e.uc.hook_add(UC_HOOK_CODE, connected)
            try:
                call(0x0800c21c)
            finally:
                e.uc.hook_del(hook)
            check(packets == [bytes.fromhex('53204281')], 'XInput_real_dispatch_to_Xiaomi')
            check(bytes(e.uc.mem_read(0x2000066a, 4)) == bytes(4), 'XInput_pending_flag_cleared')
            # Keep the real poller's saved-register/stack behavior while
            # observing the original dispatch destinations for other devices.
            stock_targets = []
            current_kind = 1
            def xinput_stock(uc, pc, size, data):
                if pc in (0x08010728, 0x08014990):
                    uc.reg_write(UC_ARM_REG_R0, current_kind if pc == 0x08010728 else 2)
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
                elif pc in (0x0800c272, 0x0800c2da):
                    stock_targets.append((pc, uc.reg_read(UC_ARM_REG_R4),
                                          uc.reg_read(UC_ARM_REG_R5), uc.reg_read(UC_ARM_REG_R6)))
                    uc.reg_write(UC_ARM_REG_PC, 0x0800c363)
            hook = e.uc.hook_add(UC_HOOK_CODE, xinput_stock)
            try:
                for current_kind in (1, 10, 11, 23, 15):
                    reset(current_kind); stock_targets.clear()
                    e.uc.mem_write(0x200010b7, b'\x02')
                    e.uc.mem_write(0x2000066a, struct.pack('<I', 0x80004281))
                    call(0x0800c21c)
                    target = 0x0800c2da if current_kind == 10 else 0x0800c272
                    check(not packets and stock_targets == [(target, current_kind, 0x81, 0x42)],
                          'XInput_other_controller_dispatch_preserved')
            finally:
                e.uc.hook_del(hook)
            # Enter the Switch dispatch with its live ABI, stop at continuation.
            reset()
            def continuation(uc, pc, size, data):
                if pc == 0x0800a734:
                    uc.reg_write(UC_ARM_REG_PC, STOP)
            hook = e.uc.hook_add(UC_HOOK_CODE, continuation)
            try:
                call(0x0800a64c, 24, 0x39, 0xa1)
            finally:
                e.uc.hook_del(hook)
            gain = profile['switch_rumble_gain']
            strength = lambda value: min(255, value*gain//100)
            check(packets == [bytes((0x53, 0x20, strength(0x39), strength(0xa1)))], 'Switch_live_dispatch_to_Xiaomi')
            if gain != 100:
                # Exercise the actual dispatch for every input level, with
                # opposing motor strengths: zero, independent saturation and
                # monotonic response must all survive the machine code.
                hook = e.uc.hook_add(UC_HOOK_CODE, continuation)
                try:
                    for big in range(256):
                        small = 255-big
                        reset()
                        call(0x0800a64c, 24, small, big)
                        check(packets == [bytes((0x53, 0x20, strength(small), strength(big)))],
                              'Switch_gain_all_levels_independent_and_saturated')
                    reset()
                    call(0x0800a64c, 24, 0, 0)
                    check(packets == [bytes.fromhex('53200000')], 'Switch_gain_zero_stops_both_motors')
                finally:
                    e.uc.hook_del(hook)
            # Controllers other than Xiaomi must reach their original
            # dispatch with all three arguments intact, without a send.
            stock_targets = []
            def stock_dispatch(uc, pc, size, data):
                if pc in (0x0800a650, 0x0800a6b4):
                    stock_targets.append((pc, uc.reg_read(UC_ARM_REG_R0),
                                          uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)))
                    uc.reg_write(UC_ARM_REG_PC, STOP)
            hook = e.uc.hook_add(UC_HOOK_CODE, stock_dispatch)
            try:
                for kind in (1, 10, 11, 23, 0x91):
                    reset(kind); stock_targets.clear()
                    call(0x0800a64c, kind, 0x39, 0xa1)
                    target = 0x0800a6b4 if kind == 10 else 0x0800a650
                    check(not packets and stock_targets == [(target, kind, 0x39, 0xa1)],
                          'Switch_gain_other_controllers_unchanged')
            finally:
                e.uc.hook_del(hook)
            # Decode the tester's actual HD-rumble packets through the original
            # USB parser. Each motor must survive conversion independently.
            from controller_io import switch_rumble
            for small, big in ((0, 0), (0.25, 0), (0, 0.25), (0.5, 0.5)):
                reset()
                e.uc.mem_write(0x200010b7, b'\x02')
                e.uc.mem_write(0x20001164, struct.pack('<I', 1000))
                report = switch_rumble(1, small, big)
                e.uc.mem_write(0x20010000, report+bytes(128-len(report)))
                call(0x0800a8f8, 0x20010000, len(report), 1)
                large, little = e.uc.mem_read(0x20000668, 2)
                check(bool(large) == bool(big) and bool(little) == bool(small), 'tester_Switch_rumble_real_parser')
    finally:
        e.uc.hook_del(bt)
    return checks
