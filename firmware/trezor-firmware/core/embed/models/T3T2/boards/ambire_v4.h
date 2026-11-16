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
