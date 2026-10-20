"""Execute generated Thumb code, with real runtime SP and mocked transport only."""
from collections import Counter
import random
import struct

from firmware_emu import *
from unicorn import UC_HOOK_CODE
from patch_xiaomi_home import STATE, TICK, build as build_v2
from patch_xiaomi import stock_controller_types
from rebuild_dat import load_original


def validate_image(image, profile):
    from custom_firmware import TARGET_BITS, BUTTON_BITS, TRIGGER_OFFSETS, TRIGGER_THRESHOLD, triggers_remapped
    digital_triggers = triggers_remapped(profile)
    checks = Counter()
    def check(condition, label):
        if not condition:
            raise AssertionError(f'Test ARM echoue : {label}')
        checks[label] += 1

    original = load_original()
    baseline, _ = build_v2(original)
    reference = FirmwareEmu(baseline)
    candidate = FirmwareEmu(image)
    fixture = bytes(reference.uc.mem_read(0x20000000, 0x4000))
    check(bytes(candidate.uc.mem_read(0x20000000, 0x4000)) == fixture, 'initial_RAM_identical')
    check(image[:0xec] == reference.image[:0xec], 'vectors_identical')
    check(len(image) == len(reference.image), 'image_length_identical')
    check(struct.unpack_from('<I', image, 0xfc)[0] == STATE, 'runtime_stack_reserved')

    def reset(e, kind=24, mode=0x30):
        e.uc.mem_write(0x20000000, fixture)
        e.uc.mem_write(0x20001222, struct.pack('<HH', kind, kind))
        e.uc.mem_write(0x20000000, b'\0')
        e.uc.mem_write(0x20000044, bytes([mode]))

    def call(e, address, *args):
        started = False
        def stack(uc, pc, size, data):
            nonlocal started
            if not started:
                uc.reg_write(UC_ARM_REG_SP, STATE)
                started = True
        h = e.uc.hook_add(UC_HOOK_CODE, stack)
        try:
            result = e.call(address, *args)
        finally:
            e.uc.hook_del(h)
        check(e.saved_ok and e.uc.reg_read(UC_ARM_REG_SP) == STATE, 'ABI_real_runtime_stack')
        return result

    def decode(e, packet, length=None):
        e.uc.mem_write(0x20010000, bytes(packet) + bytes(256-len(packet)))
        e.uc.mem_write(0x20010400, b'\xdd' * 32)
        result = call(e, 0x0800db1c, 0x20010000, len(packet) if length is None else length, 0x20010400)
        return result, bytes(e.uc.mem_read(0x20010400, 32))

    def encode(e, state, mode=0x30):
        e.uc.mem_write(0x20000044, bytes([mode]))
        e.uc.mem_write(0x20010800, bytes(128))
        result = call(e, 0x08007204, *state, 0x20010800)
        return result, bytes(e.uc.mem_read(0x20010800, 14))

    def tick(e, value):
        e.uc.mem_write(TICK, struct.pack('<I', value & 0xffffffff))

    neutral = bytearray(22)
    neutral[:2] = b'\xa1\x04'
    neutral[5] = 15
    neutral[6:10] = b'\x80' * 4
    rng = random.Random(0x434647)
    packets = [neutral.copy()]
    for bit in range(16):
        packet = neutral.copy()
        packet[2:4] = struct.pack('<H', 1 << bit)
        packets.append(packet)
    # Every pair catches accidental overwrites and overlapping destinations.
    for first in range(16):
        for second in range(first, 16):
            packet = neutral.copy()
            packet[2:4] = struct.pack('<H', (1 << first) | (1 << second))
            packets.append(packet)
    for _ in range(100):
        packet = bytearray(rng.randbytes(22))
        packet[:2] = b'\xa1\x04'
        packets.append(packet)
    for axes in [bytes(4), b'\xff' * 4, b'\x00\xff\x7f\x80', b'\x01\x23\x45\x67']:
        packet = neutral.copy(); packet[6:10] = axes; packets.append(packet)
    for mi in range(256):
        packet = neutral.copy(); packet[21] = mi; packets.append(packet)
    if digital_triggers:
        levels = (0, 1, 84, 85, 86, 127, 128, 254, 255)
        for left in levels:
            for right in levels:
                for held in (0, 0xc3):
                    packet = neutral.copy()
                    packet[2] = held
                    packet[12:14] = bytes((left, right))
                    packets.append(packet)

    for packet in packets:
        reset(reference); reset(candidate)
        tick(candidate, 1000)
        _, stock = decode(reference, packet)
        result, output = decode(candidate, packet)
        physical = struct.unpack('<H', packet[2:4])[0]
        expected_buttons = 0
        for name, bit in BUTTON_BITS.items():
            dest = TARGET_BITS[profile['buttons'][name]]
            if physical & (1 << bit) and dest is not None:
                expected_buttons |= 1 << dest
        mi_dest = TARGET_BITS[profile['buttons']['Mi']]
        if digital_triggers:
            for name, offset in TRIGGER_OFFSETS.items():
                dest = TARGET_BITS[profile['buttons'][name]]
                if packet[offset] >= TRIGGER_THRESHOLD and dest is not None:
                    expected_buttons |= 1 << dest
        if packet[21] & 1 and mi_dest is not None:
            expected_buttons |= 1 << mi_dest
        hats = [0x200, 0x240, 0x40, 0x140, 0x100, 0x180, 0x80, 0x280]
        if packet[5] < 8:
            expected_buttons |= hats[packet[5]]
        axes = bytearray(packet[6:10])
        if profile['swap_sticks']:
            axes = axes[2:] + axes[:2]
        for i, axis in enumerate(('LX', 'LY', 'RX', 'RY')):
            if axis in profile['invert_axes']:
                axes[i] = 255 - axes[i]
        triggers = bytes(2) if digital_triggers else packet[12:14]
        expected = struct.pack('<I', expected_buttons) + axes + triggers + b'\xdd' * 22
        check(result == 1 and output == expected, 'physical_buttons_axes_triggers')
        check(output[8:10] == triggers if digital_triggers else output[8:10] == stock[8:10],
              'digital_triggers_no_original_command' if digital_triggers else 'analog_triggers_preserved')
        private = bytes(candidate.uc.mem_read(STATE, 8))
        check(private[5] == (packet[20] + 1 if packet[20] <= 100 else 0), 'battery_cache')
        if digital_triggers:
            # Execute the real encoder in both Switch formats. The known stock
            # caller only ORs additional bits from analog pressure; zero bytes
            # preserve the remapped bits and cannot reintroduce ZL/ZR.
            state = struct.unpack('<IIH', output[:10])
            for mode in (0x30, 0x3f):
                reset(reference)
                expected_state = (expected_buttons, int.from_bytes(axes, 'little'), 0)
                actual_usb = encode(candidate, state, mode)
                check(actual_usb == encode(reference, expected_state, mode), 'trigger_mapping_Switch_USB')
                if mode == 0x30:
                    raw = actual_usb[1]
                    check(bool(raw[6] & 0x80) == bool(expected_buttons & (1 << 14)), 'Switch_ZL_bit')
                    check(bool(raw[4] & 0x80) == bool(expected_buttons & (1 << 15)), 'Switch_ZR_bit')

    for length in range(22):
        reset(candidate)
        check(decode(candidate, neutral, length) == (2, b'\xdd' * 32), 'short_packet_ignored')
        check(bytes(candidate.uc.mem_read(STATE, 8)) == bytes(8), 'short_packet_no_state_write')
    for index, value in [(0, 0), (1, 3), (1, 5)]:
        packet = neutral.copy(); packet[index] = value
        reset(candidate)
        check(decode(candidate, packet) == (2, b'\xdd' * 32), 'malformed_packet_ignored')

    # Test the real Switch encoder after Mi press + immediate release, with
    # wraparound and exact boundary. Compare all 14 output bytes to stock.
    mi_dest = TARGET_BITS[profile['buttons']['Mi']]
    for start in (0, 1000, 0xfffffff0):
        for mode in (0x30, 0x3f):
            reset(candidate)
            pressed = neutral.copy(); pressed[21] = 1
            tick(candidate, start); decode(candidate, pressed)
            tick(candidate, start + 1)
            _, raw = decode(candidate, neutral)
            state = struct.unpack('<IIH', raw[:10])
            duration = profile['mi_pulse_ms']
            for elapsed in (1, duration-1, duration, duration+1, 2000):
                tick(candidate, start + elapsed)
                mask = 0
                if profile['mi_pulse'] and mi_dest is not None and elapsed < duration:
                    mask = 1 << mi_dest
                reset(reference)
                expected_state = (mask, state[1], state[2])
                check(encode(candidate, state, mode) == encode(reference, expected_state, mode), 'Mi_pulse_USB_output_expiry')
            # Raw held buttons remain held even after the pulse has expired.
            reset(reference)
            mask = (1 << mi_dest) if mi_dest is not None else 0
            check(encode(candidate, (mask, state[1], 0), mode) == encode(reference, (mask, state[1], 0), mode), 'held_button_after_pulse')

    kinds = sorted(set(stock_controller_types(original)) - {24})
    for kind in kinds:
        for mode in (0x30, 0x3f):
            reset(reference, kind); reset(candidate, kind)
            candidate.uc.mem_write(STATE, struct.pack('<IBB', 0, 1, 101))
            tick(candidate, 20)
            state = (rng.getrandbits(23), 0x7f807f80, rng.getrandbits(16))
            check(encode(candidate, state, mode) == encode(reference, state, mode), 'other_controller_USB_unchanged')
        reset(reference, kind); reset(candidate, kind)
        packet = b'\xa1\x04' + rng.randbytes(126)
        check(decode(candidate, packet) == decode(reference, packet), 'other_controller_decoder_unchanged')

    # Execute final USB sender. Only transport, logging and unrelated remapping
    # calls are stubbed; battery instruction and its call site execute normally.
    transport = {0x080143e8, 0x08015ef8, 0x08005fd0, 0x0800ba24, 0x08009f10, 0x08007aac}
    def battery(e, percent, kind=24):
        reset(e, kind)
        if percent is not None:
            e.uc.mem_write(STATE+5, bytes([percent+1]))
        e.uc.mem_write(0x200010b6, b'\x03\x02')
        e.uc.mem_write(0x200005b8, b'\x01')
        e.uc.mem_write(0x200005bc, b'\x01')
        e.uc.mem_write(0x200005bf, b'\x01')
        e.uc.mem_write(0x20000319, b'\0')
        e.uc.mem_write(0x20010800, b'\0\x30' + bytes(126))
        sent = []
        def stub(uc, pc, size, data):
            if pc in transport:
                if pc == 0x0800ba24:
                    sent.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R0), 64)))
                uc.reg_write(UC_ARM_REG_R0, 0)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        h = e.uc.hook_add(UC_HOOK_CODE, stub)
        try:
            call(e, 0x0800a74c, 0x20010800, 1, 0)
        finally:
            e.uc.hook_del(h)
        check(len(sent) == 1, 'battery_transport_reached')
        return sent[0][2]
    for percent in (None, 0, 1, 25, 26, 50, 51, 75, 76, 100):
        expected = ((percent+24)//25)*32 if profile['battery'] and percent is not None else 0x60
        check(battery(candidate, percent) == expected, 'battery_option_final_USB')
    check(battery(candidate, 100, kind=1) == 0x60, 'other_controller_battery_unchanged')

    def reconnect(e, kind):
        reset(e, kind)
        e.uc.mem_write(0x2000121a, bytes.fromhex('1c965a010203'))
        e.uc.mem_write(0x2000122a, bytes(16))
        def skip_log(uc, pc, size, data):
            if pc == 0x08007aa6:
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        h = e.uc.hook_add(UC_HOOK_CODE, skip_log)
        try:
            return call(e, 0x0801017c)
        finally:
            e.uc.hook_del(h)
    check(reconnect(candidate, 24) == (0 if profile['reconnect'] else 1), 'reconnect_option')
    for kind in kinds:
        check(reconnect(candidate, kind) == reconnect(reference, kind), 'other_controller_reconnect_unchanged')
    return {'status': 'offline_passed', 'hardware_validated': False,
            'total_checks': sum(checks.values()), 'checks': dict(checks),
            'limits': 'ARM emulation with mocked USB transport. Hardware profile, radio timing and battery discharge not validated.'}
