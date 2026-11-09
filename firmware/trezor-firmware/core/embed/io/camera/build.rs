use xbuild::{CLibrary, Result, bail_unsupported};

// Ambire boards, [ambire_camera]: the QR camera. The kernel drives the
// hardware; the app (the firmware's coreapp) decodes the frames.
pub fn def_module(lib: &mut CLibrary) -> Result<()> {
    lib.add_include("camera/inc");

    lib.add_define("USE_AMBIRE_CAMERA", Some("1"));

    if cfg!(feature = "emulator") {
        lib.add_source("camera/unix/camera.c");
    } else if cfg!(feature = "mcu_stm32u5") {
        lib.add_source("camera/stm32u5/camera.c");
    } else {
        bail_unsupported!();
    }

    if cfg!(not(feature = "kernel_mode")) {
        lib.add_sources([
            "camera/qr/qr_scan.c",
            "camera/qr/quirc_decode.c",
            "camera/qr/quirc_identify.c",
            "camera/qr/quirc_version_db.c",
        ]);
    }

    Ok(())
}
