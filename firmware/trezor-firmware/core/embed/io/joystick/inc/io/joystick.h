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

#define JOYSTICK_CONTACTS 0x1FU

// For bring-up: bits 8-11 the ADC's state, bits 16-29 its last conversion
#define JOYSTICK_ADC_STATE(r) (((r) >> 8) & 0xFU)
#define JOYSTICK_ADC_CODE(r) (((r) >> 16) & 0x3FFFU)
typedef enum {
  JOYSTICK_ADC_OFF = 0,
  JOYSTICK_ADC_READY,
  JOYSTICK_ADC_FAILED_REGULATOR,
  JOYSTICK_ADC_FAILED_CALIBRATION,
  JOYSTICK_ADC_FAILED_ENABLE,
  JOYSTICK_ADC_FAILED_CONVERSION,
} joystick_adc_state_t;

// The contacts pressed now (JOYSTICK_* bits, JOYSTICK_CONTACTS masks them),
// with the ADC's state and last conversion above them
uint32_t joystick_read(void);
