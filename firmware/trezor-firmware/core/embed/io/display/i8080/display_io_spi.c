/*
 * This file is part of the Trezor project, https://trezor.io/
 *
 * Copyright (c) SatoshiLabs
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

// 4-wire SPI transport for the i8080 driver's ST7789 panels (STM32U5 only):
// SCK + MOSI on an SPI in simplex-transmit master mode, D/C and CS on GPIOs.
//
// The SPI runs continuously (TSIZE = 0): commands and parameters are polled
// 8-bit frames; the frame buffer goes out by GPDMA as 16-bit frames, MSB
// first, which is the ST7789's default RGB565 byte order (so the panel's
// little-endian RAMCTRL setting is never used on this transport).

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <sys/irq.h>
#include <sys/mpu.h>
#include "display_io.h"

#ifdef KERNEL_MODE

#define SPI DISPLAY_SPI_INSTANCE

// SPI configuration with the given frame size (8 or 16 bits)
#define SPI_CFG1(bits) (DISPLAY_SPI_CLK_PRESCALER | ((bits) - 1))

// Waits until every queued frame has left the shift register
static inline void spi_wait_idle(void) {
  while ((SPI->SR & SPI_SR_TXC) == 0) {
  }
}

// (Re)starts the SPI with the given CFG1 (frame size, DMA request enable).
// CFG1 is writable only while the SPI is disabled.
static void spi_restart(uint32_t cfg1) {
  SPI->CR1 &= ~SPI_CR1_SPE;
  SPI->CFG1 = cfg1;
  SPI->CR1 |= SPI_CR1_SPE;
  SPI->CR1 |= SPI_CR1_CSTART;
}

void display_io_init_gpio(void) {
  __HAL_RCC_GPIOE_CLK_ENABLE();

  GPIO_InitTypeDef GPIO_InitStructure = {0};

  // Reset low (panel held in reset), CS high, D/C high (data)
  GPIO_InitStructure.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStructure.Pull = GPIO_NOPULL;
  GPIO_InitStructure.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_WritePin(DISPLAY_RST_PORT, DISPLAY_RST_PIN, GPIO_PIN_RESET);
  GPIO_InitStructure.Pin = DISPLAY_RST_PIN;
  HAL_GPIO_Init(DISPLAY_RST_PORT, &GPIO_InitStructure);

  GPIO_InitStructure.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_WritePin(DISPLAY_CS_PORT, DISPLAY_CS_PIN, GPIO_PIN_SET);
  GPIO_InitStructure.Pin = DISPLAY_CS_PIN;
  HAL_GPIO_Init(DISPLAY_CS_PORT, &GPIO_InitStructure);

  HAL_GPIO_WritePin(DISPLAY_DC_PORT, DISPLAY_DC_PIN, GPIO_PIN_SET);
  GPIO_InitStructure.Pin = DISPLAY_DC_PIN;
  HAL_GPIO_Init(DISPLAY_DC_PORT, &GPIO_InitStructure);

#ifdef DISPLAY_TE_PIN
  GPIO_InitStructure.Mode = GPIO_MODE_INPUT;
  GPIO_InitStructure.Pull = GPIO_NOPULL;
  GPIO_InitStructure.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  GPIO_InitStructure.Pin = DISPLAY_TE_PIN;
  HAL_GPIO_Init(DISPLAY_TE_PORT, &GPIO_InitStructure);
#endif

  GPIO_InitStructure.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStructure.Pull = GPIO_NOPULL;
  GPIO_InitStructure.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  GPIO_InitStructure.Alternate = DISPLAY_SPI_PIN_AF;
  GPIO_InitStructure.Pin = DISPLAY_SPI_SCK_PIN;
  HAL_GPIO_Init(DISPLAY_SPI_SCK_PORT, &GPIO_InitStructure);
  GPIO_InitStructure.Pin = DISPLAY_SPI_MOSI_PIN;
  HAL_GPIO_Init(DISPLAY_SPI_MOSI_PORT, &GPIO_InitStructure);
}

void display_io_init_spi(void) {
  // Deselect the panel while SCK may glitch through the SPI reset
  HAL_GPIO_WritePin(DISPLAY_CS_PORT, DISPLAY_CS_PIN, GPIO_PIN_SET);

  DISPLAY_SPI_CLK_EN();
  DISPLAY_SPI_FORCE_RESET();
  DISPLAY_SPI_RELEASE_RESET();

  // Master, simplex transmit, mode 0, MSB first, software NSS (CS is a GPIO),
  // pins kept driven while the SPI is disabled between frame size changes
  SPI->CFG2 = SPI_CFG2_MASTER | SPI_CFG2_COMM_0 | SPI_CFG2_SSM |
              SPI_CFG2_AFCNTR;
  SPI->CR1 = SPI_CR1_SSI;
  SPI->CR2 = 0;  // TSIZE = 0: endless transfer, clocked while data is queued
  spi_restart(SPI_CFG1(8));

  HAL_GPIO_WritePin(DISPLAY_CS_PORT, DISPLAY_CS_PIN, GPIO_PIN_RESET);
}

void display_io_spi_cmd(uint8_t cmd) {
  spi_wait_idle();
  // A CS pulse re-synchronizes the panel's serial interface at each command
  HAL_GPIO_WritePin(DISPLAY_CS_PORT, DISPLAY_CS_PIN, GPIO_PIN_SET);
  HAL_GPIO_WritePin(DISPLAY_DC_PORT, DISPLAY_DC_PIN, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(DISPLAY_CS_PORT, DISPLAY_CS_PIN, GPIO_PIN_RESET);
  *(__IO uint8_t *)&SPI->TXDR = cmd;
  spi_wait_idle();
  HAL_GPIO_WritePin(DISPLAY_DC_PORT, DISPLAY_DC_PIN, GPIO_PIN_SET);
}

void display_io_spi_data(uint8_t data) {
  while ((SPI->SR & SPI_SR_TXP) == 0) {
  }
  *(__IO uint8_t *)&SPI->TXDR = data;
}

void display_io_init_te_interrupt(void) {
#ifdef DISPLAY_TE_INTERRUPT_HANDLER
  EXTI_HandleTypeDef EXTI_Handle = {0};
  EXTI_ConfigTypeDef EXTI_Config = {0};
  EXTI_Config.GPIOSel = DISPLAY_TE_INTERRUPT_GPIOSEL;
  EXTI_Config.Line = DISPLAY_TE_INTERRUPT_EXTI_LINE;
  EXTI_Config.Mode = EXTI_MODE_INTERRUPT;
  EXTI_Config.Trigger = EXTI_TRIGGER_RISING;
  HAL_EXTI_SetConfigLine(&EXTI_Handle, &EXTI_Config);

  // setup interrupt for tearing effect pin
  NVIC_SetPriority(DISPLAY_TE_INTERRUPT_NUM, IRQ_PRI_NORMAL);
  NVIC_EnableIRQ(DISPLAY_TE_INTERRUPT_NUM);
#endif
}

#ifndef BOARDLOADER

// GPDMA block size is limited to 64 KB; an even chunk keeps pixels whole
#define DMA_MAX_CHUNK 65534U

static DMA_HandleTypeDef g_dma;
static const uint8_t *g_dma_src;
static size_t g_dma_remaining;
static display_io_spi_callback_t g_dma_callback;

static void dma_start_chunk(void) {
  uint32_t chunk = g_dma_remaining > DMA_MAX_CHUNK ? DMA_MAX_CHUNK
                                                   : g_dma_remaining;
  const uint8_t *src = g_dma_src;
  g_dma_src += chunk;
  g_dma_remaining -= chunk;
  HAL_DMA_Start_IT(&g_dma, (uint32_t)src, (uint32_t)&SPI->TXDR, chunk);
}

// Called from the DMA interrupt when a chunk has been handed to the SPI
static void dma_chunk_done(DMA_HandleTypeDef *hdma) {
  if (g_dma_remaining > 0) {
    dma_start_chunk();
    return;
  }

  // The last frames are still in the FIFO (a few us at the SPI clock)
  spi_wait_idle();
  HAL_DMA_DeInit(&g_dma);
  NVIC_DisableIRQ(GPDMA1_Channel4_IRQn);
  g_dma.Instance = NULL;
  spi_restart(SPI_CFG1(8));

  display_io_spi_callback_t callback = g_dma_callback;
  g_dma_callback = NULL;
  if (callback != NULL) {
    callback();
  }
}

// Called from the DMA interrupt on a transfer error: drop the rest of the
// frame and finish as usual, so the frame queue keeps moving
static void dma_error(DMA_HandleTypeDef *hdma) {
  g_dma_remaining = 0;
  dma_chunk_done(hdma);
}

void display_io_spi_start_dma(const uint8_t *src, size_t size,
                              display_io_spi_callback_t callback) {
  g_dma_src = src;
  g_dma_remaining = size;
  g_dma_callback = callback;

  __HAL_RCC_GPDMA1_CLK_ENABLE();

  g_dma.Instance = GPDMA1_Channel4;
  g_dma.XferCpltCallback = dma_chunk_done;
  g_dma.XferErrorCallback = dma_error;
  g_dma.Init.Request = DISPLAY_SPI_DMA_REQUEST;
  g_dma.Init.BlkHWRequest = DMA_BREQ_SINGLE_BURST;
  g_dma.Init.Direction = DMA_MEMORY_TO_PERIPH;
  g_dma.Init.SrcInc = DMA_SINC_INCREMENTED;
  g_dma.Init.DestInc = DMA_DINC_FIXED;
  g_dma.Init.SrcDataWidth = DMA_SRC_DATAWIDTH_HALFWORD;
  g_dma.Init.DestDataWidth = DMA_DEST_DATAWIDTH_HALFWORD;
  g_dma.Init.Priority = DMA_LOW_PRIORITY_HIGH_WEIGHT;
  g_dma.Init.SrcBurstLength = 1;
  g_dma.Init.DestBurstLength = 1;
  g_dma.Init.TransferAllocatedPort =
      DMA_SRC_ALLOCATED_PORT1 | DMA_DEST_ALLOCATED_PORT0;
  g_dma.Init.TransferEventMode = DMA_TCEM_BLOCK_TRANSFER;
  g_dma.Init.Mode = DMA_NORMAL;
  HAL_DMA_Init(&g_dma);
  HAL_DMA_ConfigChannelAttributes(
      &g_dma, DMA_CHANNEL_PRIV | DMA_CHANNEL_SEC | DMA_CHANNEL_SRC_SEC |
                  DMA_CHANNEL_DEST_SEC);

  NVIC_SetPriority(GPDMA1_Channel4_IRQn, IRQ_PRI_NORMAL);
  NVIC_EnableIRQ(GPDMA1_Channel4_IRQn);

  // The window/RAMWR command before this left D/C high; switch the SPI to
  // 16-bit frames fed by the DMA request
  spi_wait_idle();
  SPI->CR1 &= ~SPI_CR1_SPE;
  SPI->CFG1 = SPI_CFG1(16) | SPI_CFG1_TXDMAEN;
  dma_start_chunk();
  SPI->CR1 |= SPI_CR1_SPE;
  SPI->CR1 |= SPI_CR1_CSTART;
}

void GPDMA1_Channel4_IRQHandler(void) {
  IRQ_LOG_ENTER();
  mpu_mode_t mpu_mode = mpu_reconfig(MPU_MODE_DEFAULT);
  HAL_DMA_IRQHandler(&g_dma);

  mpu_restore(mpu_mode);
  IRQ_LOG_EXIT();
}

#endif  // BOARDLOADER

#endif  // KERNEL_MODE
