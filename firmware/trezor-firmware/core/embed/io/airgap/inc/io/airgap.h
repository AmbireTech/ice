/*
 * Ambire boards, [ambire_airgap]: the airgap switch as the firmware sees it.
 *
 * The switch itself cuts USB data and the radio's power in hardware; this
 * only reads its position (AIRGAP_SENSE), so the UI can follow it: QR codes
 * when the device is airgapped, Bluetooth when it is not.
 */

#pragma once

#include <trezor_types.h>

// True while the switch is on AIR (USB data and the radio cut off)
bool airgap_engaged(void);
