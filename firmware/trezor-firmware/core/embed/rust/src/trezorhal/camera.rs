//! Ambire boards, [ambire_camera]: the QR camera (io/camera.h) and the QR
//! decoder (io/qr_scan.h).

use super::ffi;

pub const FRAME_W: usize = ffi::CAMERA_FRAME_W as usize;
pub const FRAME_H: usize = ffi::CAMERA_FRAME_H as usize;
pub const FRAME_SIZE: usize = FRAME_W * FRAME_H;
pub const ZOOM_LEVELS: u32 = ffi::CAMERA_ZOOM_LEVELS;

pub const SENSOR_OV5640: u32 = ffi::CAMERA_SENSOR_OV5640;
pub const SENSOR_GC2145: u32 = ffi::CAMERA_SENSOR_GC2145;
pub const SENSOR_OV2640: u32 = ffi::CAMERA_SENSOR_OV2640;

pub const STATE_BUSY: u32 = ffi::CAMERA_STATE_BUSY;
pub const STATE_DONE: u32 = ffi::CAMERA_STATE_DONE;
pub const STATE_FAILED: u32 = ffi::CAMERA_STATE_FAILED;

pub const ERR_NO_SENSOR: u32 = ffi::CAMERA_ERR_NO_SENSOR;
pub const ERR_UNKNOWN_ID: u32 = ffi::CAMERA_ERR_UNKNOWN_ID;
pub const ERR_SCCB_WRITE: u32 = ffi::CAMERA_ERR_SCCB_WRITE;

pub type Status = ffi::camera_status_t;
pub type QrResult = ffi::qr_scan_result_t;

/// Powers the camera up and starts the sensor (blocks up to ~1 s)
pub fn open(zoom: u32) -> bool {
    unsafe { ffi::camera_open(zoom) }
}

pub fn close() {
    unsafe { ffi::camera_close() }
}

pub fn set_zoom(zoom: u32) -> bool {
    unsafe { ffi::camera_set_zoom(zoom) }
}

/// Starts capturing a frame into `buf`.
///
/// # Safety
///
/// `buf` must point to `FRAME_SIZE` writable bytes, 4-byte aligned, that
/// nothing reads or writes until `status()` reports the capture done or
/// failed, or `close()` is called: the DMA writes them in the meantime.
pub unsafe fn capture(buf: *mut u8) -> bool {
    unsafe { ffi::camera_capture(buf, FRAME_SIZE) }
}

pub fn status() -> Status {
    // SAFETY: a plain C struct of integers; all zero is a valid value
    let mut status: Status = unsafe { core::mem::zeroed() };
    unsafe { ffi::camera_get_status(&mut status) };
    status
}

/// Bytes of arena the QR decoder needs for a frame (its image included)
pub fn qr_arena_size() -> usize {
    unsafe { ffi::qr_scan_arena_size(FRAME_W as _, FRAME_H as _) }
}

/// Builds the QR decoder in `arena`.
///
/// # Safety
///
/// `arena` must point to `size` writable bytes, 8-byte aligned, that stay
/// reserved for the decoder for as long as it is used.
pub unsafe fn qr_init(arena: *mut u8, size: usize) -> bool {
    unsafe { ffi::qr_scan_init(arena, size, FRAME_W as _, FRAME_H as _) }
}

/// The decoder's image (FRAME_SIZE bytes in its arena): the camera captures
/// into it, `qr_scan()` reads it. Null before `qr_init()`.
pub fn qr_image() -> *mut u8 {
    unsafe { ffi::qr_scan_image() }
}

/// Looks for QR codes in the decoder's image, which it uses up (thresholds
/// in place); the first code that decodes goes to `payload`.
///
/// # Safety
///
/// No capture may be writing the image.
pub unsafe fn qr_scan(payload: &mut [u8]) -> QrResult {
    // SAFETY: as in status()
    let mut result: QrResult = unsafe { core::mem::zeroed() };
    unsafe { ffi::qr_scan(payload.as_mut_ptr(), payload.len(), &mut result) };
    result
}
