// Ambire Ice: the SE050's random number generator, mixed into the strong
// RNG (sec/rng/rng_strong.c). Hardware only.
use xbuild::{CLibrary, Result, bail_unsupported};

pub fn def_module(lib: &mut CLibrary) -> Result<()> {
    lib.add_include("se050/inc");

    lib.add_define("USE_SE050", Some("1"));

    if cfg!(feature = "mcu_stm32") {
        lib.add_sources(["se050/stm32/se050.c"]);
    } else {
        bail_unsupported!();
    }

    Ok(())
}
