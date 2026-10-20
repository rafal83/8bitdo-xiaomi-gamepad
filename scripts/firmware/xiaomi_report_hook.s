// Linked at 0x0801BAD0; stock ABI r0=HIDP packet, r1=length, r2=output.
// Output: u32 canonical buttons, four u8 axes, two u8 triggers.
ldr r3, =0x20001222
ldrh r3, [r3]
cmp r3, #0x18
beq xiaomi
push.w {r3, r4, r5, r6, r7, r8, r9, r10, r11, lr}
b.w 0x0800DB20
xiaomi:
push {r4, r5, r6, lr}
cmp r1, #22
blo ignored
ldrb r3, [r0]
cmp r3, #0xa1
bne ignored
ldrb r3, [r0, #1]
cmp r3, #4
bne ignored
ldrb r4, [r0, #2]
and r3, r4, #3
lsl r3, r3, #12
and r5, r4, #8
orr r3, r3, r5, lsl #2
and r5, r4, #16
orr r3, r3, r5
and r5, r4, #0xc0
orr r3, r3, r5, lsl #4
ldrb r4, [r0, #3]
and r5, r4, #0x60
orr r3, r3, r5, lsr #4
and r5, r4, #4
orr r3, r3, r5, lsl #1
and r5, r4, #8
orr r3, r3, r5, lsr #3
ldrb r4, [r0, #21]
and r4, r4, #1
orr r3, r3, r4, lsl #17
ldrb r5, [r0, #5]
cmp r5, #8
bhs no_hat
adr.w r6, hat
ldrh.w r4, [r6, r5, lsl #1]
orr r3, r3, r4
no_hat:
str r3, [r2]
ldr r3, [r0, #6]
str r3, [r2, #4]
ldrh r3, [r0, #12]
strh r3, [r2, #8]
movs r0, #1
pop {r4, r5, r6, pc}
ignored:
movs r0, #2
pop {r4, r5, r6, pc}
.align 2
hat:
.hword 0x0200,0x0240,0x0040,0x0140,0x0100,0x0180,0x0080,0x0280
.align 2
.ltorg
