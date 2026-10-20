"""Build Xiaomi revision 2: Mi pulse, battery, experimental reconnect policy.

Offline only. Keeps the initial experimental firmware and all vectors unchanged.
"""
import struct
from patch_xiaomi import ROOT, BASE, NAME, REPORT, END, assemble, build as initial_build, digest, MD
from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB, KS_MODE_V8

HOME = REPORT + 0xd0
BATTERY = NAME + 104
RECONNECT = HOME + 0x40
STATE = 0x20003fc0
TICK = 0x20001164
PULSE_TICKS = 120
STOCK_ENCODER_PUSH = bytes.fromhex('2de9ff5f')


def report_source():
    """V2 decoder source, also used by the offline profile builder."""
    source = (ROOT/'scripts/firmware/xiaomi_report_hook.s').read_text()
    marker = 'orr r3, r3, r4, lsl #17'
    if source.count(marker) != 1:
        raise ValueError('Unknown initial report decoder source')
    source = source.replace(marker, marker + '''
ldr r6, =0x20003fc0
cmp r4, #0
beq update_battery
ldr r5, =0x20001164
ldr r5, [r5]
str r5, [r6]
movs r5, #1
strb r5, [r6, #4]
update_battery:
ldrb r5, [r0, #20]
cmp r5, #100
bhi no_battery_value
adds r5, #1
strb r5, [r6, #5]
no_battery_value:
''')
    return source


def build(original):
    _, initial = initial_build(original)
    if original[0x08007204-BASE:0x08007208-BASE] != STOCK_ENCODER_PUSH:
        raise ValueError('Unexpected Switch encoder prologue')
    if original[0x0800a7b2-BASE:0x0800a7b6-BASE] != bytes.fromhex('6020e070'):
        raise ValueError('Unexpected final battery write')
    if original[0x0801017c-BASE:0x08010180-BASE] != bytes.fromhex('10b51022'):
        raise ValueError('Unexpected reconnect-policy prologue')
    if struct.unpack_from('<I', original, 0x080034fc-BASE)[0] != STATE + 8:
        raise ValueError('Unexpected startup stack top')
    if original[0x080034ec-BASE:0x080034f0-BASE] != bytes.fromhex('dff80cd0'):
        raise ValueError('Unexpected startup stack load')
    # The only absolute pointers to the reserved interval/old stack top are the
    # initial vector and its startup literal; zero initialization reaches its end.
    refs = [(BASE+off, struct.unpack_from('<I', original, off)[0])
            for off in range(len(original)-3)
            if STATE <= struct.unpack_from('<I', original, off)[0] <= STATE+8]
    if refs != [(BASE, STATE+8), (0x080034fc, STATE+8)]:
        raise ValueError('Unexpected reference to reserved stack interval')
    source = report_source()
    report = assemble(source, REPORT)
    home = assemble((ROOT/'scripts/firmware/xiaomi_switch_home_hook.s').read_text(), HOME)
    # Keystone needs this feature flag to emit UDIV. The resulting Thumb-2
    # instruction is also used throughout the original Cortex-M firmware.
    battery_source = '\n'.join(line.split('//')[0] for line in (ROOT/'scripts/firmware/xiaomi_battery_hook.s').read_text().splitlines())
    battery = bytes(Ks(KS_ARCH_ARM, KS_MODE_THUMB | KS_MODE_V8).asm(battery_source, BATTERY)[0])
    if not any(i.mnemonic == 'udiv' and i.op_str == 'r0, r0, r1' for i in MD.disasm(battery, BATTERY)):
        raise ValueError('Battery divide instruction did not roundtrip')
    reconnect = assemble((ROOT/'scripts/firmware/xiaomi_reconnect_hook.s').read_text(), RECONNECT)
    if (BATTERY+len(battery) > REPORT or REPORT+len(report) > HOME or
        HOME+len(home) > RECONNECT or RECONNECT+len(reconnect) > END):
        raise ValueError(f'Code cave exhausted: battery={len(battery)}, report={len(report)}, home={len(home)}, reconnect={len(reconnect)}')
    if [(i.mnemonic, i.op_str) for i in MD.disasm(home, HOME) if i.mnemonic == 'b.w'] != [('b.w', '#0x8007208')]:
        raise ValueError('Unexpected encoder fallback branch')
    edits = [(int(e['address'], 16), bytes.fromhex(e['new']), e['reason'])
             for e in initial['edits'] if int(e['address'], 16) != REPORT]
    edits += [(REPORT, report, 'Type 0x18 decoder; record Mi event timestamp without delaying HID processing'),
              (0x08007204, assemble(f'b.w {HOME:#x}', 0x08007204), 'Switch encoder entry wrapper, Xiaomi only'),
              (HOME, home, 'Xiaomi Switch 120-tick Mi pulse; initial button mapping unchanged'),
              (0x0800a7b2, assemble(f'bl {BATTERY:#x}', 0x0800a7b2), 'Replace hardcoded Switch battery at final write, Xiaomi only'),
              (BATTERY, battery, 'Convert cached battery percent to Switch level; stock default for other types/unknown data'),
              (0x0801017c, assemble(f'b.w {RECONNECT:#x}', 0x0801017c), 'Xiaomi reconnect policy entry wrapper'),
              (RECONNECT, reconnect, 'Experimental known-controller passive reconnect policy for type 0x18 only'),
              (0x080034fc, struct.pack('<I', STATE), 'Reserve 8 bytes above descending runtime stack; vector and instructions unchanged')]
    patched = bytearray(original)
    journal = []
    occupied = set()
    for address, new, reason in edits:
        off = address - BASE
        span = set(range(off, off+len(new)))
        if span & occupied:
            raise ValueError('Overlapping edits')
        occupied |= span
        old = original[off:off+len(new)]
        patched[off:off+len(new)] = new
        journal.append({'address': hex(address), 'offset': hex(off), 'length': len(new),
                        'old': old.hex(), 'new': new.hex(), 'reason': reason})
    if len(patched) != len(original) or patched[:0xec] != original[:0xec]:
        raise ValueError('Image length/vector integrity failed')
    info = {'status': 'experimental_revision2_unflashed', 'original_sha256': digest(original),
            'patched_sha256': digest(patched), 'length': len(patched),
            'changed_byte_count': sum(a != b for a, b in zip(original, patched)),
            'injected_lengths': {'name': initial['injected_lengths']['name'], 'report': len(report), 'switch_home': len(home), 'battery': len(battery), 'reconnect': len(reconnect)},
            'private_ram': {'start': hex(STATE), 'length': 8, 'startup_stack_top': hex(STATE),
                            'vector_stack_top_unchanged': hex(STATE+8), 'zero_initialized': True,
                            'layout': 'uint32 Mi timestamp; uint8 active; uint8 battery percent+1 (0 unknown); 2 spare bytes'},
            'home_ticks': PULSE_TICKS, 'tick_address': hex(TICK), 'edits': journal}
    return bytes(patched), info


