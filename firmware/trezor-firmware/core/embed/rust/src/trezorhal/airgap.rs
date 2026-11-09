//! Ambire boards, [ambire_airgap]: the airgap switch's position (io/airgap.h)

use super::ffi;

/// True while the switch is on AIR (USB data and the radio cut off)
pub fn engaged() -> bool {
    unsafe { ffi::airgap_engaged() }
}
