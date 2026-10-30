/*
 * Ambire Ice: NXP SE050 secure element, random number generator only.
 * Not part of Trezor's tree; GPL-3.0-or-later like the rest of it.
 */

#pragma once

#include <trezor_types.h>

#ifdef SECURE_MODE

/**
 * @brief Fills the buffer with random bytes from the SE050's own TRNG.
 *
 * The first call (and any call after a failed exchange) power-cycles the
 * SE050 through its ENA pin, resynchronises the T=1 link and selects the
 * IoT applet. A failed exchange is retried once from a fresh power-up.
 *
 * Marks RNG_TYPE_SE050 as used on success.
 *
 * @return true on success, false if the SE050 did not deliver all bytes.
 */
bool __wur se050_random_buffer(uint8_t *dest, size_t size);

#endif  // SECURE_MODE
