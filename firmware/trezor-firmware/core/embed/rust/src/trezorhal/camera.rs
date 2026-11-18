//! Ambire boards, [ambire_camera]: the QR camera (io/camera.h) and the QR
//! decoder (io/qr_scan.h).

use super::ffi;

pub const SIDE_MIN: usize = ffi::CAMERA_SIDE_MIN as usize;
pub const SIDE_MAX: usize = ffi::CAMERA_SIDE_MAX as usize;
pub const ZOOM_LEVELS: u32 = ffi::CAMERA_ZOOM_LEVELS;
pub const CLOCKS: u32 = ffi::CAMERA_CLOCKS;
pub const TUNES: u32 = ffi::CAMERA_TUNES;

pub const SENSOR_OV5640: u32 = ffi::CAMERA_SENSOR_OV5640;
pub const SENSOR_GC2145: u32 = ffi::CAMERA_SENSOR_GC2145;
pub const SENSOR_OV2640: u32 = ffi::CAMERA_SENSOR_OV2640;

pub const STATE_BUSY: u32 = ffi::CAMERA_STATE_BUSY;
pub const STATE_DONE: u32 = ffi::CAMERA_STATE_DONE;
pub const STATE_FAILED: u32 = ffi::CAMERA_STATE_FAILED;

pub const ERR_NO_SENSOR: u32 = ffi::CAMERA_ERR_NO_SENSOR;
pub const ERR_UNKNOWN_ID: u32 = ffi::CAMERA_ERR_UNKNOWN_ID;
pub const ERR_SCCB_WRITE: u32 = ffi::CAMERA_ERR_SCCB_WRITE;
pub const ERR_BAD_MODE: u32 = ffi::CAMERA_ERR_BAD_MODE;

pub type Status = ffi::camera_status_t;
pub type QrResult = ffi::qr_scan_result_t;

/// What the camera captures: the zoom level, the frame's side (a multiple of
/// 8 from SIDE_MIN to SIDE_MAX), the sensor clock and the image tuning
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Mode {
    pub zoom: u32,
    pub side: usize,
    pub clock: u32,
    pub tune: u32,
}

impl Mode {
    /// The packed word of io/camera.h (CAMERA_MODE())
    fn word(self) -> u32 {
        self.zoom | ((self.side as u32 / 8) << 8) | (self.clock << 16) | (self.tune << 24)
    }

    pub fn frame_bytes(self) -> usize {
        self.side * self.side
    }
}

/// Powers the camera up and starts the sensor (blocks up to ~1 s)
pub fn open(mode: Mode) -> bool {
    unsafe { ffi::camera_open(mode.word()) }
}

pub fn close() {
    unsafe { ffi::camera_close() }
}

/// Changes the mode (blocks while the sensor settles)
pub fn set_mode(mode: Mode) -> bool {
    unsafe { ffi::camera_set_mode(mode.word()) }
}

/// Starts capturing a frame of the running mode into `buf`.
///
/// # Safety
///
/// `buf` must point to `len` writable bytes, 4-byte aligned, at least the
/// running mode's frame, that nothing reads or writes until `status()`
/// reports the capture done or failed, or `close()` is called: the DMA
/// writes them in the meantime.
pub unsafe fn capture(buf: *mut u8, len: usize) -> bool {
    unsafe { ffi::camera_capture(buf, len) }
}

pub fn status() -> Status {
    // SAFETY: a plain C struct of integers; all zero is a valid value
    let mut status: Status = unsafe { core::mem::zeroed() };
    unsafe { ffi::camera_get_status(&mut status) };
    status
}

/// Tells the camera where the code is in its frames (x0, y0, x1, y1), for
/// the exposure; None: no code in view
pub fn set_meter(area: Option<[i32; 4]>) {
    let pack = |x: i32, y: i32| (x.clamp(0, 0xFFFF) as u32) | ((y.clamp(0, 0xFFFF) as u32) << 16);
    let (a, b) = match area {
        Some(b) => (pack(b[0], b[1]), pack(b[2], b[3])),
        None => (0, 0),
    };
    unsafe { ffi::camera_set_meter(a, b) }
}

/// Bytes of decoder state the QR decoder needs for side x side frames
pub fn qr_state_size(side: usize) -> usize {
    unsafe { ffi::qr_scan_state_size(side as _, side as _) }
}

/// Builds the QR decoder for side x side frames, its state in `state`.
///
/// # Safety
///
/// `state` must point to `state_len` writable bytes, 8-byte aligned, reserved
/// for the decoder for as long as it is used.
pub unsafe fn qr_init(side: usize, state: *mut u8, state_len: usize) -> bool {
    unsafe { ffi::qr_scan_init(side as _, side as _, state, state_len) }
}

/// Looks for a QR code in the frame at `image` (read, not modified); a code
/// that decodes goes to `payload`. Frames are taken as a video: the decoder
/// keeps the last frame's grid and the sum of frames that did not decode.
///
/// # Safety
///
/// `image` must point to a whole frame of the decoder's size that no
/// capture is writing.
pub unsafe fn qr_scan(image: *const u8, payload: &mut [u8]) -> QrResult {
    // SAFETY: as in status()
    let mut result: QrResult = unsafe { core::mem::zeroed() };
    unsafe { ffi::qr_scan(image, payload.as_mut_ptr(), payload.len(), &mut result) };
    result
}

/// The next frame is unrelated to the last one (the mode changed)
pub fn qr_reset() {
    unsafe { ffi::qr_scan_reset() }
}

/// The area to meter the light on after a frame (x0, y0, x1, y1): where the
/// decoder made out a code in the last few frames; None: the frame's centre
pub fn qr_meter_area() -> Option<[i32; 4]> {
    let mut area = [0i32; 4];
    unsafe { ffi::qr_scan_meter_area(area.as_mut_ptr()) }.then_some(area)
}
