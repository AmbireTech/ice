//! Ambire boards, [ambire_joystick]: the joystick (io/joystick.h)

use super::ffi;

/// The netlist's switch contacts, as io/joystick.h numbers them
pub const A: u32 = 1 << 0;
pub const B: u32 = 1 << 1;
pub const C: u32 = 1 << 2;
pub const D: u32 = 1 << 3;
/// The centre press (the power key)
pub const CENTER: u32 = 1 << 4;

/// The contacts pressed now
pub fn read() -> u32 {
    unsafe { ffi::joystick_read() }
}
