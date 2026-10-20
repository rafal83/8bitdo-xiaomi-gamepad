// Only type 0x18: stretch Mi at USB poll time; keep the initial button mapping.
// Private state: 8 bytes removed from the top of the descending main stack.
push {r4, r5, r6, lr}
ldr r4, =0x20001222
ldrh r4, [r4]
cmp r4, #0x18
bne original_encoder
ldr r4, =0x20003fc0
ldrb r5, [r4, #4]
cmp r5, #1
bne original_encoder
ldr r5, =0x20001164
ldr r5, [r5]
ldr r6, [r4]
subs r5, r5, r6
cmp r5, #120
bhs pulse_expired
orr r0, r0, #0x20000
b original_encoder
pulse_expired:
movs r5, #0
strb r5, [r4, #4]
original_encoder:
pop.w {r4, r5, r6, lr}
push.w {r0, r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, lr}
b.w 0x08007208
.align 2
.ltorg
