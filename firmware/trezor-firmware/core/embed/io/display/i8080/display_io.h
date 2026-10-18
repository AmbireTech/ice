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

#ifndef TREZORHAL_DISPLAY_IO_H
#define TREZORHAL_DISPLAY_IO_H

#include <trezor_bsp.h>
#include <trezor_types.h>

void display_io_init_gpio(void);
void display_io_init_te_interrupt(void);

#ifdef DISPLAY_SPI

// 4-wire SPI transport (display_io_spi.c). Write-only: no panel reads.

#if !defined(DISPLAY_RST_PORT) || !defined(DISPLAY_RST_PIN) || \
    !defined(DISPLAY_DC_PORT) || !defined(DISPLAY_DC_PIN) ||   \
    !defined(DISPLAY_CS_PORT) || !defined(DISPLAY_CS_PIN)
#error \
    "Board header must define DISPLAY_RST_PORT/PIN, DISPLAY_DC_PORT/PIN and DISPLAY_CS_PORT/PIN"
#endif

#ifdef DISPLAY_IDENTIFY
#error "DISPLAY_IDENTIFY reads the panel, which the SPI transport cannot do"
#endif

void display_io_init_spi(void);
void display_io_spi_cmd(uint8_t cmd);
void display_io_spi_data(uint8_t data);

typedef void (*display_io_spi_callback_t)(void);

// Sends `size` bytes of RGB565 pixels by DMA, after the RAMWR command, and
// calls `callback` from the interrupt context when done
void display_io_spi_start_dma(const uint8_t *src, size_t size,
                              display_io_spi_callback_t callback);

#define ISSUE_CMD_BYTE(X) display_io_spi_cmd(X)
#define ISSUE_DATA_BYTE(X) display_io_spi_data(X)
#define ISSUE_PIXEL_DATA(X)          \
  display_io_spi_data((X) >> 8);     \
  display_io_spi_data((X) & 0xFF)

#else  // DISPLAY_SPI

void display_io_init_fmc(void);

#ifndef FMC_BANK1
#define FMC_BANK1 0x60000000U
#endif

// The board header must provide the display reset and data/command (RS) pins.
// DISPLAY_DC_PORT/PIN is the GPIO routed to an FMC address line and
// DISPLAY_MEMORY_PIN selects which external FMC address line (A<n>) drives D/C.
#if !defined(DISPLAY_RST_PORT) || !defined(DISPLAY_RST_PIN) || \
    !defined(DISPLAY_DC_PORT) || !defined(DISPLAY_DC_PIN) ||   \
    !defined(DISPLAY_MEMORY_PIN)
#error \
    "Board header must define DISPLAY_RST_PORT/PIN, DISPLAY_DC_PORT/PIN and DISPLAY_MEMORY_PIN"
#endif

#define DISPLAY_MEMORY_BASE FMC_BANK1

#ifdef DISPLAY_I8080_16BIT_DW
#define DISPLAY_ADDR_SHIFT 2
#define DISP_MEM_TYPE uint16_t
#elif DISPLAY_I8080_8BIT_DW
#define DISPLAY_ADDR_SHIFT 1
#define DISP_MEM_TYPE uint8_t
#else
#error "Unsupported display interface"
#endif

/*#define DISPLAY_CMD_ADDRESS ((__IO DISP_MEM_TYPE *)(DISPLAY_MEMORY_BASE))
#define DISPLAY_DATA_ADDRESS                    \
  ((__IO DISP_MEM_TYPE *)(DISPLAY_MEMORY_BASE | \
                          (DISPLAY_ADDR_SHIFT << DISPLAY_MEMORY_PIN)))
*/

extern __IO DISP_MEM_TYPE *const DISPLAY_CMD_ADDRESS;
extern __IO DISP_MEM_TYPE *const DISPLAY_DATA_ADDRESS;

#define ISSUE_CMD_BYTE(X) (*(DISPLAY_CMD_ADDRESS) = (X))
#define ISSUE_DATA_BYTE(X) (*(DISPLAY_DATA_ADDRESS) = (X))

#ifdef DISPLAY_I8080_16BIT_DW
#define ISSUE_PIXEL_DATA(X) ISSUE_DATA_BYTE(X)
#elif DISPLAY_I8080_8BIT_DW
#define ISSUE_PIXEL_DATA(X)    \
  ISSUE_DATA_BYTE((X) & 0xFF); \
  ISSUE_DATA_BYTE((X) >> 8)
#endif

#endif  // DISPLAY_SPI

#endif  // TREZORHAL_DISPLAY_IO_H
