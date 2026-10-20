// Replaces MOVS r0,#0x60; STRB r0,[r4,#3] at 0x0800A7B2.
// At this exact site: r4=Switch report, r8=0x200010a5. Preserve live r1/LR.
push {r1, lr}
ldrh.w r0, [r8, #0x17d]
cmp r0, #0x18
bne default_level
ldr r0, =0x20003fc5
ldrb r0, [r0]
cbz r0, default_level
// Cached value is percent+1; 0 means unavailable. ceil(percent/25)*0x20.
adds r0, #23
movs r1, #25
udiv r0, r0, r1
lsls r0, r0, #5
b store_level
default_level:
movs r0, #0x60
store_level:
strb r0, [r4, #3]
pop {r1, pc}
.align 2
.ltorg
