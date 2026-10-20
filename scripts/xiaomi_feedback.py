"""Optional, experimental Xiaomi sensor/rumble hooks for the exact 2.07 ROM.

The 1 KiB extension ends at 0x0801C000. The stock profile allocator searches
down from 0x0801DC00 to 0x0801B000; its lower bound is raised to 0x0801C000
for this variant. Previously saved vendor profiles in the reclaimed page may
be lost. The separate E000..20000 bank is unchanged. Hardware testing required.
"""
import struct

from patch_xiaomi import BASE, END, NAME, REPORT, assemble

EXTENSION_SIZE = 1024
DECODER = END + 0x40
CAPTURE = END + 0x140
NAME_WRAPPER = END + 0x200
RUMBLE = END + 0x240
XINPUT_DISPATCH = END + 0x2c0
SWITCH_DISPATCH = END + 0x300
MOTION = END + 0x340
SENSOR = 0x2000068b


def enabled(profile):
    return profile.get('accelerometer', False) or profile.get('rumble', False)


def extend(image, original, profile, decoder):
    if not enabled(profile):
        return image, []
    if (len(image) != END-BASE or
            tuple(struct.unpack_from('<I', original, a-BASE)[0] for a in
                  (0x0800629c, 0x080062a4, 0x080062ac, 0x080062b0)) !=
            (0x0801dc00, 0x0801b000, 0x0801e000, 0x08020000)):
        raise ValueError('Carte mémoire du firmware inattendue.')
    result = bytearray(image) + bytearray(EXTENSION_SIZE)
    edits = []
    occupied = set()

    def edit(address, data, expected=None):
        offset = address-BASE
        if expected is not None and original[offset:offset+len(expected)] != expected:
            raise ValueError(f'Instruction inattendue à {address:#x}.')
        if offset < 0 or offset+len(data) > len(result):
            raise ValueError('Extension hors limites.')
        span = set(range(offset, offset+len(data)))
        if occupied & span:
            raise ValueError('Chevauchement des hooks capteurs/vibrations.')
        occupied.update(span)
        result[offset:offset+len(data)] = data
        edits.append(dict(address=hex(address), offset=hex(offset), length=len(data),
                          new=data.hex(), reason='Experimental Xiaomi accelerometer/rumble extension'))

    def code(address, source, limit):
        data = assemble(source, address)
        if len(data) > limit:
            raise ValueError(f'Hook trop grand à {address:#x} : {len(data)} > {limit}.')
        edit(address, data)

    # Keep profile allocation away from the enlarged executable image.
    edit(0x080062a4, struct.pack('<I', END+EXTENSION_SIZE), struct.pack('<I', 0x0801b000))

    if profile['accelerometer']:
        code(END, f'''push {{r4, lr}}
mov r4, r0
bl {DECODER:#x}
cmp r0, #1
bne complete
mov r0, r4
bl {CAPTURE:#x}
movs r0, #1
complete:
pop {{r4, pc}}
''', 0x40)
        code(DECODER, decoder, 0x100)
        edit(REPORT, assemble(f'b.w {END:#x}', REPORT))
        # Raw signed accelerations are repeated in the three Switch samples;
        # angular velocities remain zero. No invented gyroscope samples.
        code(CAPTURE, f'''push {{r4, r5, r6, lr}}
mov r4, r0
ldr r5, ={SENSOR:#x}
movs r6, #3
copy_sample:
ldr r0, [r4, #14]
str r0, [r5]
ldrh r0, [r4, #18]
strh r0, [r5, #4]
movs r0, #0
strh r0, [r5, #6]
str r0, [r5, #8]
adds r5, #12
subs r6, #1
bne copy_sample
ldr r5, =0x20003fc6
ldrb r0, [r5]
cbnz r0, capture_done
movs r0, #3
bl 0x080143e8
movw r1, #0xffff
cmp r0, r1
beq capture_done
ldr r1, =0x08013153
push {{r1, r6}}
mov r1, sp
movs r2, #4
bl 0x08015ef8
add sp, #8
cmp r0, #0
bne capture_done
movs r0, #1
strb r0, [r5]
capture_done:
pop {{r4, r5, r6, pc}}
.align 2
.ltorg
''', 0xc0)
        code(NAME_WRAPPER, f'''push {{r4, lr}}
bl {NAME:#x}
cmp r0, #0x18
bne name_done
ldr r1, =0x20003fc6
movs r2, #0
strb r2, [r1]
ldr r0, ={SENSOR:#x}
movs r1, #36
bl 0x080035c6
movs r0, #0x18
name_done:
pop {{r4, pc}}
.align 2
.ltorg
''', 0x40)
        edit(0x080040f4, assemble(f'b.w {NAME_WRAPPER:#x}', 0x080040f4), bytes.fromhex('2de9f047'))
        code(MOTION, f'''ldr r1, =0x20001222
ldrh r1, [r1]
cmp r1, #0x18
bne stock_motion
push {{r4, lr}}
ldr r1, =0x200005b8
ldrb r1, [r1]
cbnz r1, motion_return
mov r4, r0
add.w r0, r0, #14
ldr r1, =0x2000031c
ldrb r1, [r1]
cbz r1, clear_motion
ldr r1, ={SENSOR:#x}
movs r2, #36
bl 0x08003578
b motion_done
clear_motion:
movs r1, #36
bl 0x080035c6
motion_done:
movs r0, #0x60
strb r0, [r4, #3]
motion_return:
pop {{r4, pc}}
stock_motion:
push {{r4, lr}}
mov r4, r0
b.w 0x0800a53c
.align 2
.ltorg
''', 0xc0)
        edit(0x0800a538, assemble(f'b.w {MOTION:#x}', 0x0800a538), bytes.fromhex('10b50446'))

    if profile['rumble']:
        # SET_REPORT(feature) uses index 3, like the stock SET_REPORT(output)
        # path at 0x08008EF4 (template 52 01 at 0x0801AB85).
        # r0=large motor, r1=small motor, both 0..255, same stock dispatch ABI.
        code(RUMBLE, '''push {r4, r5, r6, lr}
mov r4, r0
mov r5, r1
movs r0, #3
bl 0x080143e8
movw r1, #0xffff
cmp r0, r1
beq rumble_done
movw r1, #0x2053
orr r1, r1, r5, lsl #16
orr r1, r1, r4, lsl #24
movs r2, #0
push {r1, r2}
mov r1, sp
movs r2, #4
bl 0x08015ef8
add sp, #8
rumble_done:
pop {r4, r5, r6, pc}
.align 2
.ltorg
''', 0x80)
        code(XINPUT_DISPATCH, f'''cmp r4, #0x18
bne stock_xinput
mov r0, r5
mov r1, r6
bl {RUMBLE:#x}
b.w 0x0800c362
stock_xinput:
cmp r4, #0xa
bne other_xinput
b.w 0x0800c2da
other_xinput:
b.w 0x0800c272
''', 0x40)
        # Amplify only Xiaomi's Switch dispatch, after the stock HD conversion.
        # Integer percentage, saturating to 255; a zero command remains zero.
        gain = profile['switch_rumble_gain']
        minimum = (profile['switch_rumble_minimum']*255+50)//100
        # Use the compact form when the floor needs space in the same 64-byte
        # slot. With a zero floor, retain existing firmware bytes exactly.
        multiply = ('muls r2, r0\nmuls r1, r0' if minimum else
                    'mul r2, r2, r0\nmul r1, r1, r0')
        # Percentages in 25-point steps give an exact division by four, using
        # only instructions already supported by this Thumb-2 ROM/toolchain.
        boost = '' if gain == 100 else f'''movs r0, #{gain//25}
{multiply}
lsrs r2, r2, #2
lsrs r1, r1, #2
usat r2, #8, r2
usat r1, #8, r1
'''
        floor = '' if not minimum else f'''cbz r1, small_done
cmp r1, #{minimum}
it lo
movlo r1, #{minimum}
small_done:
cbz r2, big_done
cmp r2, #{minimum}
it lo
movlo r2, #{minimum}
big_done:
'''
        code(SWITCH_DISPATCH, f'''cmp r0, #0x18
bne stock_switch
{boost}{floor}mov r0, r2
bl {RUMBLE:#x}
b.w 0x0800a734
stock_switch:
cmp r0, #0xa
bne other_switch
b.w 0x0800a6b4
other_switch:
b.w 0x0800a650
''', 0x40)
        edit(0x0800c26e, assemble(f'b.w {XINPUT_DISPATCH:#x}', 0x0800c26e), bytes.fromhex('0a2c33d0'))
        edit(0x0800a64c, assemble(f'b.w {SWITCH_DISPATCH:#x}', 0x0800a64c), bytes.fromhex('0a2831d0'))
    return bytes(result), edits
