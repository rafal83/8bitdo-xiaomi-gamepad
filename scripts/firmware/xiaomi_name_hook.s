// Linked at 0x0801BA40, replacing only the classifier entry PUSH.W.
// r0: NUL-terminated remote name, r1: Bluetooth address (stock convention).
push {r0, r1, r4, lr}
mov r4, r0
adr.w r1, name_zh
bl 0x08003616
cbz r0, matched
mov r0, r4
adr.w r1, name_en
bl 0x08003616
cbz r0, matched
pop.w {r0, r1, r4, lr}
push.w {r4, r5, r6, r7, r8, r9, r10, lr}
b.w 0x080040F8
matched:
ldr r0, =0x20001224
movs r1, #0x18
strh r1, [r0]
add sp, #8
movs r0, #0x18
pop {r4, pc}
.align 2
name_zh:
.byte 0xe5,0xb0,0x8f,0xe7,0xb1,0xb3,0xe8,0x93,0x9d,0xe7,0x89,0x99,0xe6,0x89,0x8b,0xe6,0x9f,0x84,0
name_en:
.asciz "Xiaomi Mi Bluetooth Gamepad"
.align 2
.ltorg
