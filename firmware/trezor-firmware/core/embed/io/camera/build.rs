use xbuild::{CLibrary, Result, bail_unsupported};

// Ambire boards, [ambire_camera]: the QR camera. The kernel drives the
// hardware; the app (the firmware's coreapp) decodes the frames.
pub fn def_module(lib: &mut CLibrary) -> Result<()> {
    lib.add_include("camera/inc");

    lib.add_define("USE_AMBIRE_CAMERA", Some("1"));

    if cfg!(feature = "emulator") {
        lib.add_source("camera/unix/camera.c");
    } else if cfg!(feature = "mcu_stm32u5") {
        lib.add_sources(["camera/stm32u5/camera.c", "camera/stm32u5/camera_ae.c"]);
    } else {
        bail_unsupported!();
    }

    if cfg!(not(feature = "kernel_mode")) {
        // The signer's requests are version 7 to 11; up to 13 the grid has
        // the same alignment patterns and the decoder's state stays the size
        // quirc's was
        lib.add_define("FQR_MAX_VERSION", Some("13"));
        lib.add_sources(["camera/qr/qr_scan.c", "camera/qr/fqr.c"]);
    }

    Ok(())
}
