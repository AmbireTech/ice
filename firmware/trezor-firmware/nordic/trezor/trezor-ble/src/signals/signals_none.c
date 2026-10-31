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

/*
 * Signals for boards whose radio has no signal lines to the MCU, only the two
 * UART wires (CONFIG_TREZOR_UART_LINK): there is no stay-in-bootloader input
 * (and no bootloader to stay in), and the reserved output exists only as the
 * value reported in the management info message.
 */

#include <stdbool.h>

#include <signals/signals.h>

static bool out_reserved = false;

bool signals_init(void) { return true; }

bool signals_is_stay_in_bootloader(void) { return false; }

void signals_set_reserved(bool set) { out_reserved = set; }

bool signals_out_get_reserved(void) { return out_reserved; }
