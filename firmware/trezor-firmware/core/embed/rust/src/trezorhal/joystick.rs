//! Ambire boards, [ambire_joystick]: the joystick (io/joystick.h)

use super::ffi;

/// The netlist's switch contacts, as io/joystick.h numbers them
pub const A: u32 = 1 << 0;
pub const B: u32 = 1 << 1;
pub const C: u32 = 1 << 2;
pub const D: u32 = 1 << 3;
/// The centre press (the power key)
pub const CENTER: u32 = 1 << 4;

/// The contact bits of `read()`
pub const CONTACTS: u32 = 0x1f;

/// The contacts pressed now (`CONTACTS` masks them); above them, for
/// bring-up, the ADC's state (`adc_state`) and last conversion (`adc_code`)
pub fn read() -> u32 {
    unsafe { ffi::joystick_read() }
}

/// 0 not started, 1 ready, 2-5 failed at the regulator, the calibration,
/// the enable or a conversion (io/joystick.h)
pub fn adc_state(r: u32) -> u32 {
    (r >> 8) & 0xf
}

/// The last conversion, 14 bits: 16383 is the 3.3 V rail
pub fn adc_code(r: u32) -> u32 {
    (r >> 16) & 0x3fff
}
