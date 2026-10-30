#pragma once

// Ambire signer, proto 0: STM32U585VIT6Q (LQFP100), hardware in
// hardware/ambire-wallet of the AmbireTech/secure-signer repo. Pins follow
// src/parts/mcuAssign.json there, which the board copper matches.
//
// LQFP100 has ports A-E and PH0/PH1/PH3 only: nothing here may sit on a
// port F/G pin (the T3T2 devkit's pin map is a larger package).
//
// Not driven by this board file yet: the OPTIGA secure element (I2C4; not
// fitted on the first boards), the SE050 beyond its random number generator
// (below), the nRF52840 radio (USART3 2-wire, behind the airgap switch), the
// camera (DCMI), the BQ25185 charger and the MAX17048 battery gauge.

#define VDD_3V3 1

// Power latch. The power button lifts the 3.3 V buck-boost's enable; the MCU
// keeps it up by driving PWR_HOLD high. An RC on the enable holds the rail for
// about 1 s, so the boardloader must set this before anything slow (option
// byte programming included), and every reset is bridged by the RC.
#define POWER_HOLD_PORT GPIOE
#define POWER_HOLD_PIN GPIO_PIN_5
#define POWER_HOLD_CLK_EN __HAL_RCC_GPIOE_CLK_ENABLE

// Newhaven NHD-2.4-240320AF-CSXP: ST7789Vi, 240x320, strapped for 4-wire SPI
// (IM2..0 = 110). Write-only: the panel's SDO is not wired, so no
// DISPLAY_IDENTIFY. SPI1 at 160 MHz / 4 = 40 MHz (ST7789 write cycle >= 16 ns).
#define DISPLAY_SPI 1
#define DISPLAY_SPI_INSTANCE SPI1
#define DISPLAY_SPI_CLK_EN __HAL_RCC_SPI1_CLK_ENABLE
#define DISPLAY_SPI_FORCE_RESET __HAL_RCC_SPI1_FORCE_RESET
#define DISPLAY_SPI_RELEASE_RESET __HAL_RCC_SPI1_RELEASE_RESET
#define DISPLAY_SPI_CLK_PRESCALER SPI_CFG1_MBR_0  // /4
#define DISPLAY_SPI_PIN_AF GPIO_AF5_SPI1
#define DISPLAY_SPI_DMA_REQUEST GPDMA1_REQUEST_SPI1_TX
#define DISPLAY_SPI_SCK_PORT GPIOE
#define DISPLAY_SPI_SCK_PIN GPIO_PIN_13
#define DISPLAY_SPI_MOSI_PORT GPIOE
#define DISPLAY_SPI_MOSI_PIN GPIO_PIN_15
#define DISPLAY_CS_PORT GPIOE
#define DISPLAY_CS_PIN GPIO_PIN_14
#define DISPLAY_DC_PORT GPIOE
#define DISPLAY_DC_PIN GPIO_PIN_12
#define DISPLAY_RST_PORT GPIOE
#define DISPLAY_RST_PIN GPIO_PIN_11

#define DISPLAY_TE_PORT GPIOE
#define DISPLAY_TE_PIN GPIO_PIN_10
#define DISPLAY_TE_INTERRUPT_HANDLER EXTI10_IRQHandler
#define DISPLAY_TE_INTERRUPT_NUM EXTI10_IRQn
#define DISPLAY_TE_INTERRUPT_GPIOSEL EXTI_GPIOE
#define DISPLAY_TE_INTERRUPT_EXTI_LINE EXTI_LINE_10

// The Newhaven panel's columns run opposite to the DEM240320B1's: without
// this the image is mirrored left to right (seen on proto 0)
#define DISPLAY_PANEL_MIRROR_X 1

// One 150 KB frame buffer; a second does not fit next to the firmware's RAM.
#define FRAME_BUFFER_COUNT 1

// Backlight: TPS60230 charge pump, EN1/EN2 tied together on TFT_BL through
// 1 k, with 100 k to GND. On/off only for now: the on state is the pin's
// internal pull-up (~40 k), which leaves EN at ~2.3 V. PWM dimming on
// TIM1_CH1 waits for the TPS60230's EN timing to be checked.
#define BACKLIGHT_PIN_PORT GPIOE
#define BACKLIGHT_PIN_PIN GPIO_PIN_9
#define BACKLIGHT_PIN_CLK_ENABLE() __HAL_RCC_GPIOE_CLK_ENABLE()

#define I2C_COUNT 2

// I2C3: touch controller and battery gauge
#define I2C_INSTANCE_0 I2C3
#define I2C_INSTANCE_0_CLK_EN __HAL_RCC_I2C3_CLK_ENABLE
#define I2C_INSTANCE_0_CLK_DIS __HAL_RCC_I2C3_CLK_DISABLE
#define I2C_INSTANCE_0_PIN_AF GPIO_AF4_I2C3
#define I2C_INSTANCE_0_SDA_PORT GPIOC
#define I2C_INSTANCE_0_SDA_PIN GPIO_PIN_1
#define I2C_INSTANCE_0_SDA_CLK_EN __HAL_RCC_GPIOC_CLK_ENABLE
#define I2C_INSTANCE_0_SCL_PORT GPIOC
#define I2C_INSTANCE_0_SCL_PIN GPIO_PIN_0
#define I2C_INSTANCE_0_SCL_CLK_EN __HAL_RCC_GPIOC_CLK_ENABLE
#define I2C_INSTANCE_0_RESET_REG &RCC->APB3RSTR
#define I2C_INSTANCE_0_RESET_BIT RCC_APB3RSTR_I2C3RST
#define I2C_INSTANCE_0_EV_IRQHandler I2C3_EV_IRQHandler
#define I2C_INSTANCE_0_ER_IRQHandler I2C3_ER_IRQHandler
#define I2C_INSTANCE_0_EV_IRQn I2C3_EV_IRQn
#define I2C_INSTANCE_0_ER_IRQn I2C3_ER_IRQn
#define I2C_INSTANCE_0_GUARD_TIME 0

// I2C4: the secure elements (SE050 at 0x48, OPTIGA at 0x30)
#define I2C_INSTANCE_1 I2C4
#define I2C_INSTANCE_1_CLK_EN __HAL_RCC_I2C4_CLK_ENABLE
#define I2C_INSTANCE_1_CLK_DIS __HAL_RCC_I2C4_CLK_DISABLE
#define I2C_INSTANCE_1_PIN_AF GPIO_AF4_I2C4
#define I2C_INSTANCE_1_SDA_PORT GPIOD
#define I2C_INSTANCE_1_SDA_PIN GPIO_PIN_13
#define I2C_INSTANCE_1_SDA_CLK_EN __HAL_RCC_GPIOD_CLK_ENABLE
#define I2C_INSTANCE_1_SCL_PORT GPIOD
#define I2C_INSTANCE_1_SCL_PIN GPIO_PIN_12
#define I2C_INSTANCE_1_SCL_CLK_EN __HAL_RCC_GPIOD_CLK_ENABLE
#define I2C_INSTANCE_1_RESET_REG &RCC->APB1RSTR2
#define I2C_INSTANCE_1_RESET_BIT RCC_APB1RSTR2_I2C4RST
#define I2C_INSTANCE_1_EV_IRQHandler I2C4_EV_IRQHandler
#define I2C_INSTANCE_1_ER_IRQHandler I2C4_ER_IRQHandler
#define I2C_INSTANCE_1_EV_IRQn I2C4_EV_IRQn
#define I2C_INSTANCE_1_ER_IRQn I2C4_ER_IRQn
#define I2C_INSTANCE_1_GUARD_TIME 50  // OPTIGA requires 50 us guard time
#define I2C_INSTANCE_1_GTZC_PERIPH GTZC_PERIPH_I2C4

// FocalTech FT5426 on the panel's touch flex, same register map as the
// FT6x36 (address 0x38). RST has no pull on the board: the driver holds it.
#define TOUCH_SENSITIVITY 0x40
#define TOUCH_I2C_INSTANCE 0
#define TOUCH_INT_PORT GPIOE
#define TOUCH_INT_PIN GPIO_PIN_2
#define TOUCH_RST_PORT GPIOE
#define TOUCH_RST_PIN GPIO_PIN_3

// OPTIGA Trust M: used once [optiga] is enabled in ambire_p0.toml (the part
// is not fitted on the first boards). No power switch, only a reset line.
#define OPTIGA_I2C_INSTANCE 1
#define OPTIGA_RST_PORT GPIOD
#define OPTIGA_RST_PIN GPIO_PIN_14
#define OPTIGA_RST_CLK_EN __HAL_RCC_GPIOD_CLK_ENABLE

// NXP SE050: [se050] in ambire_p0.toml mixes its random number generator into
// the strong RNG. ENA high runs the chip, low is deep power-down (the board
// pulls it high with 100 k); the driver pulses it low to start from a clean
// power-up.
#define SE050_I2C_INSTANCE 1
#define SE050_ENA_PORT GPIOD
#define SE050_ENA_PIN GPIO_PIN_11
#define SE050_ENA_CLK_EN __HAL_RCC_GPIOD_CLK_ENABLE

// No revision straps on proto 0: four unconnected pins with pull-downs read
// revision 0. A later board can strap them high.
#define HW_REVISION_PUPD GPIO_PULLDOWN
#define HW_REVISION_0_PIN GPIO_PIN_4
#define HW_REVISION_0_PORT GPIOD
#define HW_REVISION_0_CLOCK_ENABLE() __HAL_RCC_GPIOD_CLK_ENABLE()
#define HW_REVISION_1_PIN GPIO_PIN_5
#define HW_REVISION_1_PORT GPIOD
#define HW_REVISION_1_CLOCK_ENABLE() __HAL_RCC_GPIOD_CLK_ENABLE()
#define HW_REVISION_2_PIN GPIO_PIN_6
#define HW_REVISION_2_PORT GPIOD
#define HW_REVISION_2_CLOCK_ENABLE() __HAL_RCC_GPIOD_CLK_ENABLE()
#define HW_REVISION_3_PIN GPIO_PIN_7
#define HW_REVISION_3_PORT GPIOD
#define HW_REVISION_3_CLOCK_ENABLE() __HAL_RCC_GPIOD_CLK_ENABLE()
