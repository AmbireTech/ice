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

// The joystick's four directions on one ADC input, JOY_DIR (a resistor ladder,
// io/joystick.h); its centre press is the power key (POWER_KEY_*)
#define JOYSTICK_ADC_PORT GPIOA
#define JOYSTICK_ADC_PIN GPIO_PIN_2
#define JOYSTICK_ADC_CLK_EN __HAL_RCC_GPIOA_CLK_ENABLE
#define JOYSTICK_ADC_CHANNEL LL_ADC_CHANNEL_7
// The ADC needs VDDA marked valid, which only the secure start-up can do
// (sys/startup/stm32u5/startup_init.c)
#define AMBIRE_VDDA_VALID 1

// The camera's core supply (DVDD) set per sensor (io/camera/stm32u5/camera.c):
// a TLV75801P (VFB 0.55 V) with RT 33k from OUT to FB, RB 21k from FB to GND,
// and R_INJ 33k from FB to the RC node (1 uF), fed through R_PWM 10k by TIM4_CH1
// (src/blocks/Camera.tsx). PB6 is unconnected on boards built before it.
#define CAMERA_DVDD_PWM_PORT GPIOB
#define CAMERA_DVDD_PWM_PIN GPIO_PIN_6
#define CAMERA_DVDD_PWM_AF GPIO_AF2_TIM4
#define CAMERA_DVDD_PWM_TIM TIM4
#define CAMERA_DVDD_PWM_CLK_EN __HAL_RCC_TIM4_CLK_ENABLE
#define CAMERA_DVDD_VFB_UV 550000
#define CAMERA_DVDD_R_TOP 33
#define CAMERA_DVDD_R_BOTTOM 21
#define CAMERA_DVDD_R_INJ 33
#define CAMERA_DVDD_R_PWM 10
#define CAMERA_DVDD_VDD_UV 3300000

// The camera's I/O supply (DOVDD) is 2.8 V on boards from 2026-11-19, from the AVDD
// LDO, switched by CAM_EN with the other rails (was the always-on 3.3 V); XCLK and
// RESET reach the sensor through an SN74LVC2G34 on that rail, the SCCB pull-ups go
// to it. No pin changes: camera_open() holds RESET and XCLK low while CAM_EN rises.
