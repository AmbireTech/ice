#pragma once

// Ambire signer, v4 (cells in a pack on the back): proto 0's pins, which v4
// keeps (src/parts/mcuAssign.json), except the airgap switch's sense line,
// moved to PB15 on the MCU's west side. Keep ambire_v4.toml's features in
// step with ambire_p0.toml.
#include "ambire_p0.h"

#undef AIRGAP_SENSE_PORT
#undef AIRGAP_SENSE_PIN
#undef AIRGAP_SENSE_CLK_EN
#define AIRGAP_SENSE_PORT GPIOB
#define AIRGAP_SENSE_PIN GPIO_PIN_15
#define AIRGAP_SENSE_CLK_EN __HAL_RCC_GPIOB_CLK_ENABLE

// VDDA and VREF+ are the 3.3 V rail: the secure monitor's SystemInit marks
// VDDA valid (PWR ASV), without which the ADC is isolated from it
#define USE_VDDA 1

// The battery pack (two AAA in series), read by [ambire_power]'s cell monitor
// on ADC1, each line on a 100 nF cap: the pack's plus (BAT) halved by 1M/1M,
// the point between the cells (CELL_MID) halved by 1M/1M, and the pack's minus
// (BAT_N, above GND by the reverse-cell FET) through 100 k, unscaled.
#define CELLS_PORT GPIOA
#define CELLS_CLK_EN __HAL_RCC_GPIOA_CLK_ENABLE
#define CELL_PLUS_PIN GPIO_PIN_4  // ADC1_IN9
#define CELL_PLUS_ADC_CHANNEL ADC_CHANNEL_9
#define CELL_MINUS_PIN GPIO_PIN_5  // ADC1_IN10
#define CELL_MINUS_ADC_CHANNEL ADC_CHANNEL_10
#define CELL_MID_PIN GPIO_PIN_6  // ADC1_IN11
#define CELL_MID_ADC_CHANNEL ADC_CHANNEL_11

// USB present: VBUS through 47k/100k (3.4 V), on a plain GPIO
#define VBUS_SENSE_PORT GPIOA
#define VBUS_SENSE_PIN GPIO_PIN_9
#define VBUS_SENSE_CLK_EN __HAL_RCC_GPIOA_CLK_ENABLE
