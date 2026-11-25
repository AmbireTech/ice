/*
 * Ambire boards, [ambire_joystick]: the emulator has no joystick.
 */

#include <io/joystick.h>

uint32_t joystick_read(void) { return 0; }
