use xbuild::{CLibrary, Result, bail_unsupported};

// Ambire boards, [ambire_airgap]: the airgap switch's position
pub fn def_module(lib: &mut CLibrary) -> Result<()> {
    lib.add_include("airgap/inc");

    lib.add_define("USE_AMBIRE_AIRGAP", Some("1"));

    if cfg!(feature = "emulator") {
        lib.add_source("airgap/unix/airgap.c");
    } else if cfg!(feature = "mcu_stm32u5") {
        lib.add_source("airgap/stm32u5/airgap.c");
    } else {
        bail_unsupported!();
    }

    Ok(())
}
