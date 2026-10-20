// Experimental: use the existing known-controller passive reconnect policy.
// Does not bypass authentication or change stored addresses/keys.
ldr r3, =0x20001222
ldrh r3, [r3]
cmp r3, #0x18
beq known_xiaomi
push {r4, lr}
movs r2, #0x10
b.w 0x08010180
known_xiaomi:
movs r0, #0
bx lr
.align 2
.ltorg
