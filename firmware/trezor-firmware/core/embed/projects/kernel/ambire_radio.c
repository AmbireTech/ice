/*
 * Ambire boards, [ambire_radio]: radio bring-up from the kernel.
 *
 * Proto 0's nRF52840 module (Ebyte E73) is not Trezor's radio: Trezor's T3T2
 * link is an nRF54L on SPI with handshake lines, ours a 2-wire UART behind
 * the airgap switch. This brings up our side only:
 *
 * - RADIO_OFF low allows the radio's power. The airgap switch still decides:
 *   a NOR of the switch and RADIO_OFF drives the radio's load switch, so the
 *   radio (and the RADIO light on its rail) is on exactly when the switch is
 *   off AIR. Firmware can switch the radio off, never on against the switch.
 * - USART3 at 115200 8N1 to the radio (through the TXU0202 translator), with
 *   one hello line at boot: once the radio runs a UART bridge, it is the first
 *   thing it hears from the main MCU.
 *
 * With [ble] and [nrf] in the board file, Trezor's BLE stack owns the radio
 * instead, over the framed UART link in io/nrf/stm32u5/nrf_uart_link.c (power
 * gate included); this file is then left out of the board file.
 */

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#ifdef USE_NRF
#error "[ambire_radio] and [nrf] would both drive USART3 and RADIO_OFF: drop [ambire_radio] from the board file"
#endif

#ifdef KERNEL_MODE

#define RADIO_BAUD 115200U
// Per byte; generous at 115200 baud (87 us per byte)
#define RADIO_TX_SPINS 100000U

static void radio_uart_write(const char *text) {
  for (; *text != '\0'; text++) {
    uint32_t spins = RADIO_TX_SPINS;
    while ((RADIO_UART->ISR & USART_ISR_TXE_TXFNF) == 0) {
      if (--spins == 0) {
        // The transmitter never became ready: leave the hello unsent rather
        // than hold up the kernel's start (a bring-up test, nothing waits on it)
        return;
      }
    }
    RADIO_UART->TDR = (uint8_t)*text;
  }
}

void ambire_radio_init(void) {
  GPIO_InitTypeDef gpio = {0};

  RADIO_OFF_CLK_EN();
  HAL_GPIO_WritePin(RADIO_OFF_PORT, RADIO_OFF_PIN, GPIO_PIN_RESET);
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  gpio.Pin = RADIO_OFF_PIN;
  HAL_GPIO_Init(RADIO_OFF_PORT, &gpio);

  RADIO_UART_CLK_EN();
  RADIO_UART_PINS_CLK_EN();
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Alternate = RADIO_UART_PIN_AF;
  gpio.Pin = RADIO_UART_TX_PIN;
  HAL_GPIO_Init(RADIO_UART_TX_PORT, &gpio);
  // The translator pulls its outputs low while the radio is off: the pull-up
  // keeps RX idle-high so an unpowered radio does not read as a break
  gpio.Pull = GPIO_PULLUP;
  gpio.Pin = RADIO_UART_RX_PIN;
  HAL_GPIO_Init(RADIO_UART_RX_PORT, &gpio);

  RADIO_UART->CR1 = 0;
  // USART3 runs on PCLK1 (its reset clock source), which Trezor's startup sets
  // equal to the core clock (every APB prescaler 1, startup_init.c); the RCC
  // HAL belongs to the secure monitor, not the kernel
  RADIO_UART->BRR = SystemCoreClock / RADIO_BAUD;
  RADIO_UART->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;

  radio_uart_write("Ambire Ice: main MCU up, radio UART link test\r\n");
}

#endif  // KERNEL_MODE
