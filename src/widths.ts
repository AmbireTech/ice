/**
 * Starting track widths (1 oz outer copper). The worst continuous load on the
 * board is ~0.6 A: the 3.3 V buck-boost input with the backlight at full
 * brightness, the radio transmitting and the camera streaming. 0.4 mm carries
 * ~1.2 A at a 20 C rise (IPC-2152), twice that.
 */
export const W_PWR = "0.4mm" // SYS, BAT, VBUS, the 3.3 V rail
export const W_PWR_LITE = "0.25mm" // sub-rails (radio, camera, backlight), straps
export const W_SIG = "0.15mm"
