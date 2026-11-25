use xbuild::{CLibrary, Result, bail_unsupported};

// Ambire boards, [ambire_joystick]: the joystick's directions and centre press
pub fn def_module(lib: &mut CLibrary) -> Result<()> {
    lib.add_include("joystick/inc");

    lib.add_define("USE_AMBIRE_JOYSTICK", Some("1"));

    if cfg!(feature = "emulator") {
        lib.add_source("joystick/unix/joystick.c");
    } else if cfg!(feature = "mcu_stm32u5") {
        lib.add_source("joystick/stm32u5/joystick.c");
    } else {
        bail_unsupported!();
    }

    Ok(())
}
