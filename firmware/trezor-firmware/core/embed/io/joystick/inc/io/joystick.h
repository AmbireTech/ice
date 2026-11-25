/*
 * Ambire boards, [ambire_joystick]: the joystick as the firmware sees it.
 *
 * Its four directions share one ADC input (JOY_DIR): each switches its own
 * resistor to GND against a 15k pull-up (A 15k, B 27k, C 47k, D 100k), so one
 * conversion tells every direction and every pair apart. Its centre press is
 * the power key (BTN_N); a long press still switches the device off.
 */

#pragma once

#include <trezor_types.h>

// The netlist's switch contacts (src/blocks/Power.tsx); which way each one is
// on the device is mapped in the UI
#define JOYSTICK_A (1U << 0)
#define JOYSTICK_B (1U << 1)
#define JOYSTICK_C (1U << 2)
#define JOYSTICK_D (1U << 3)
#define JOYSTICK_CENTER (1U << 4)

// The contacts pressed now, JOYSTICK_* bits; 0 when nothing is pressed
uint32_t joystick_read(void);
