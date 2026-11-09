/*
 * Ambire boards, [ambire_airgap]: reads the airgap switch's sense line.
 * AIRGAP_SENSE is driven by the switch through a series resistor (high on
 * AIR), so the pin is a plain input; it is set up on first use.
 */

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <io/airgap.h>

#ifdef KERNEL_MODE

static bool g_airgap_pin_ready;

bool airgap_engaged(void) {
  if (!g_airgap_pin_ready) {
    AIRGAP_SENSE_CLK_EN();
    GPIO_InitTypeDef gpio = {0};
    gpio.Pin = AIRGAP_SENSE_PIN;
    gpio.Mode = GPIO_MODE_INPUT;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(AIRGAP_SENSE_PORT, &gpio);
    g_airgap_pin_ready = true;
  }
  return HAL_GPIO_ReadPin(AIRGAP_SENSE_PORT, AIRGAP_SENSE_PIN) == GPIO_PIN_SET;
}

#endif  // KERNEL_MODE
