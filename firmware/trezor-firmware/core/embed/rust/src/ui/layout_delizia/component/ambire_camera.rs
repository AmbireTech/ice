//! Ambire boards, [ambire_camera]: a QR camera test, in the airgap menu
//! (the swipe up on the home screen while the airgap switch is on AIR), and
//! what it shares with the QR scan (ambire_airgap.rs): the memory a scanner
//! runs in, the preview, and the camera settings chosen here.
//!
//! The top 240x240 shows the camera's live grayscale frame, scaled down from
//! the frame's side; the strip below the sensor and mode, the decoder's view
//! of the code (version, pixels per module, decode rate), and the frame's
//! exposure and focus. Tap the frame to step the zoom, the buttons at the
//! bottom to change the frame size, the sensor clock and the tuning; the
//! top-left corner exits. When the camera does not start, the screen says why
//! (what answered on its bus, which register write failed); a tap retries.
//!
//! Memory ([`ScanMemory`]): the frames are in the UI's one full-screen image
//! buffer (150 KB, which the screen holds) and a loan from the MicroPython
//! heap while the screen lives, as is the decoder's state (and the QR scan's
//! text and UR work buffer). Which goes where is a [`Plan`]: two 320 x 320
//! frames when the heap lends the second, else one 384 x 384 or two
//! 272 x 272, else (no loan) one 288 x 288 with everything in the image
//! buffer. The DMA writes each frame straight into its buffer and the decoder
//! reads it there ([`Scanner`]). Bring-up only: nothing leaves the device.

use heapless::String;
use without_alloc::alloc::LocalAllocLeakExt;

use sys::time::Instant;

use super::super::fonts;
use crate::micropython::{ffi as upy, Error};
use crate::trezorhal::{bitblt, camera};
use crate::ui::component::{Event, EventCtx};
use crate::ui::constant::{HEIGHT, WIDTH};
use crate::ui::display::Color;
use crate::ui::event::TouchEvent;
use crate::ui::geometry::{Alignment, Offset, Point, Rect};
use crate::ui::shape::{
    self, Bitmap, BitmapFormat, Canvas, DrawingCache, ImageBuffer, Renderer, Rgb565Canvas, Shape,
    ShapeClone,
};

/// The frame is drawn scaled to this square at the top of the screen
pub(super) const VIEW: i16 = 240;
const EXIT_SIZE: i16 = 48;
const LINE_H: i16 = 18;
const MARGIN: i16 = 4;
/// The buttons along the bottom: frame size, clock, tuning
const BUTTON_TOP: i16 = HEIGHT - 24;
const BUTTON_W: i16 = WIDTH / 3;
/// A decoded code stays on screen this long after it was last seen
const DECODED_SHOW_MS: u32 = 1500;
const PAYLOAD_MAX: usize = 120;

const GREEN: Color = Color::rgb(0xd7, 0xff, 0x00);
const PURPLE: Color = Color::rgb(0xaf, 0x50, 0xff);
const RED: Color = Color::rgb(0xff, 0x40, 0x40);
const GREY: Color = Color::rgb(0xa0, 0xa0, 0xa0);
const DARK_GREY: Color = Color::rgb(0x30, 0x30, 0x30);

type Line = String<64>;

// --- Settings shared with the QR scan ---------------------------------------------

/// The camera settings last chosen in the test; the QR scan starts with them
#[derive(Clone, Copy)]
pub(super) struct Settings {
    pub zoom: u32,
    /// None: the best the memory runs
    pub plan: Option<Plan>,
    pub clock: u32,
    pub tune: u32,
}

/// The wide zoom first (see io/camera/stm32u5/camera.c): the whole field of
/// view, so a code is held close and fills it, as other wallets with this
/// module scan. A 61-module code filling 70 % of a 320 px frame gets 3.4 px
/// a module; the 2:1 window made the user hold the code twice as far away.
static mut SETTINGS: Settings = Settings {
    zoom: 0,
    plan: None,
    // The fast clock: more frames, shorter exposures (the driver falls back
    // to the stock clock where captures fail)
    clock: 1,
    tune: 0,
};

pub(super) fn settings() -> Settings {
    // SAFETY: the app is single-threaded; a plain copy
    unsafe { SETTINGS }
}

fn set_settings(s: Settings) {
    // SAFETY: as in settings()
    unsafe { SETTINGS = s }
}

// --- Memory ---------------------------------------------------------------------

/// A way to run the scanner: the frame's side, and how many frames are held
/// (two: the camera captures the next frame while this one is decoded)
#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) struct Plan {
    pub side: usize,
    pub buffers: usize,
}

/// The plans, the one to scan with first; a scanner runs the first that its
/// memory holds. Two frames before a larger one: on the host model of the
/// scan loop (firmware/bringup/camera/qrdecode/scansim) a second buffer
/// keeps every other sensor frame whatever the decoder takes, where one
/// buffer falls to every third or fourth, and a code is lost to blur before
/// it is lost to 3.4 instead of 4.1 pixels a module.
///
/// 320 x 2: a frame and the decoder's state in the image buffer, the second
/// frame in the loan. 384 and 272 x 2 fill the image buffer with frames; the
/// state is in the loan. 288 and 240 need no loan.
const PLANS: [Plan; 5] = [
    Plan { side: 320, buffers: 2 },
    Plan { side: 384, buffers: 1 },
    Plan { side: 272, buffers: 2 },
    Plan { side: 288, buffers: 1 },
    Plan { side: 240, buffers: 1 },
];

/// A loan is taken only if the heap still has a block of this size after it
/// (the screens run MicroPython code while the scan does)
const HEAP_HEADROOM: usize = 24 * 1024;

/// Where a plan's frames, decoder state and extra buffers (the QR scan's
/// text and UR work) are
#[derive(Clone, Copy)]
pub(super) struct Regions {
    /// The second is null with one buffer
    pub frames: [*mut u8; 2],
    pub state: *mut u8,
    pub state_len: usize,
    pub extra: *mut u8,
    pub extra_len: usize,
}

/// A stretch of memory handed out from its start in 16-byte steps
#[derive(Clone, Copy)]
struct Arena {
    at: *mut u8,
    left: usize,
}

impl Arena {
    fn take(&mut self, len: usize) -> Option<*mut u8> {
        let len = len.next_multiple_of(16);
        if len > self.left {
            return None;
        }
        let p = self.at;
        // Within the arena (or one past its end); never dereferenced here
        self.at = self.at.wrapping_add(len);
        self.left -= len;
        Some(p)
    }
}

/// Lays a plan out in the image buffer and the loan: the frames and the
/// state in the image buffer as far as they fit, the extra buffer the rest
/// of whichever has more left. Returns the regions and the bytes of the loan
/// used before the extra buffer; None if they do not fit.
fn lay_out(mut image: Arena, mut loan: Arena, plan: Plan, extra_min: usize) -> Option<(Regions, usize)> {
    let loan_len = loan.left;
    let frame = plan.side * plan.side;
    let state_len = camera::qr_state_size(plan.side).next_multiple_of(16);
    let first = image.take(frame)?;
    let second = if plan.buffers > 1 {
        image.take(frame).or_else(|| loan.take(frame))?
    } else {
        core::ptr::null_mut()
    };
    let state = image.take(state_len).or_else(|| loan.take(state_len))?;
    let loan_used = loan_len - loan.left;
    let rest = if loan.left > image.left { loan } else { image };
    if rest.left < extra_min {
        return None;
    }
    Some((
        Regions {
            frames: [first, second],
            state,
            state_len,
            extra: rest.at,
            extra_len: rest.left,
        },
        loan_used,
    ))
}

/// The memory a scanner runs in: the UI's image buffer (held locked), and a
/// loan from the MicroPython heap, if the heap had it.
pub(super) struct ScanMemory {
    _buffer: ImageBuffer<Rgb565Canvas<'static>>,
    image: Arena,
    /// Null without a loan. Not freed by hand: the screen (and the pointer in
    /// it) lives in the heap, and the next collection after the screen is
    /// gone frees the loan with it.
    loan: Arena,
    extra_min: usize,
}

impl ScanMemory {
    /// Locks the image buffer and asks the heap for what the best plan needs
    /// beside it (with `extra_want` extra bytes; a plan runs with
    /// `extra_min`), then for what the next one does, until one fits
    pub fn new(extra_min: usize, extra_want: usize) -> Result<Self, Error> {
        // The size is nominal: the scanner takes the whole buffer's bytes
        let mut buffer = ImageBuffer::<Rgb565Canvas<'static>>::new(Offset::new(1, 1))?;
        let bytes = buffer.raw_bytes();
        let image = Arena {
            at: bytes.as_mut_ptr(),
            left: bytes.len(),
        };
        let none = Arena {
            at: core::ptr::null_mut(),
            left: 0,
        };
        let any = Arena {
            at: core::ptr::null_mut(),
            left: usize::MAX / 2,
        };
        let mut loan = none;
        for plan in PLANS {
            if lay_out(image, none, plan, extra_min).is_some() {
                break;
            }
            let Some((_, used)) = lay_out(image, any, plan, 0) else {
                continue;
            };
            let want = used + extra_want.next_multiple_of(16);
            let at = Self::borrow(want);
            if !at.is_null() {
                loan = Arena { at, left: want };
                break;
            }
        }
        Ok(Self {
            _buffer: buffer,
            image,
            loan,
            extra_min,
        })
    }

    /// `len` bytes of the MicroPython heap, 16-byte aligned, if that leaves
    /// the heap HEAP_HEADROOM; else null
    fn borrow(len: usize) -> *mut u8 {
        // SAFETY: gc_alloc() returns null when the heap has no such block (it
        // collects once first) instead of raising
        let at = unsafe { upy::gc_alloc(len, 0) } as *mut u8;
        if at.is_null() {
            return at;
        }
        // SAFETY: as above
        let spare = unsafe { upy::gc_alloc(HEAP_HEADROOM, 0) };
        let ok = !spare.is_null() && (at as usize) % 16 == 0;
        // SAFETY: blocks just taken from gc_alloc(), referenced nowhere else
        unsafe {
            if !spare.is_null() {
                upy::gc_free(spare);
            }
            if !ok {
                upy::gc_free(at.cast());
            }
        }
        if ok {
            at
        } else {
            core::ptr::null_mut()
        }
    }

    /// The regions of a plan; None if it does not fit this memory
    pub fn regions(&self, plan: Plan) -> Option<Regions> {
        lay_out(self.image, self.loan, plan, self.extra_min).map(|(r, _)| r)
    }

    /// The plans this memory runs, best first
    pub fn plans(&self) -> impl Iterator<Item = Plan> + '_ {
        PLANS.into_iter().filter(|&p| self.regions(p).is_some())
    }

    /// `wanted` if this memory runs it, else the best plan it runs
    pub fn plan_for(&self, wanted: Option<Plan>) -> Option<Plan> {
        wanted
            .filter(|&p| self.regions(p).is_some())
            .or_else(|| self.plans().next())
    }
}

// --- The scan loop ----------------------------------------------------------------

/// What a step of the scan loop brought
pub(super) enum Scanned {
    /// No new frame yet
    Nothing,
    /// A whole frame, decoded or not (the result says)
    Frame(camera::QrResult),
    /// The capture failed; what arrived of the frame can still be shown
    Failed,
}

/// The camera's frames through the decoder, for the QR scan and the camera
/// test: the memory, which buffer the camera writes and which holds the
/// frame to show; after each frame the camera is told where the code is,
/// for its exposure.
///
/// With two buffers the next capture starts the moment a frame is in, into
/// the other buffer, and the frame is decoded and painted meanwhile. With
/// one, the frame is decoded, then painted, and the next capture starts at
/// the tick after (a paint must not show a frame the DMA is writing).
pub(super) struct Scanner {
    mem: ScanMemory,
    plan: Plan,
    regions: Option<Regions>,
    capturing: Option<usize>,
    shown: Option<usize>,
    /// Captures failed in a row
    fail_run: u8,
    pub status: camera::Status,
}

impl Scanner {
    pub fn new(extra_min: usize, extra_want: usize) -> Result<Self, Error> {
        Ok(Self {
            mem: ScanMemory::new(extra_min, extra_want)?,
            plan: PLANS[PLANS.len() - 1],
            regions: None,
            capturing: None,
            shown: None,
            fail_run: 0,
            // SAFETY: a plain C struct of integers; all zero is valid
            status: unsafe { core::mem::zeroed() },
        })
    }

    pub fn memory(&self) -> &ScanMemory {
        &self.mem
    }

    pub fn plan(&self) -> Plan {
        self.plan
    }

    pub fn side(&self) -> usize {
        self.plan.side
    }

    /// The extra buffer of the plan built (null before)
    pub fn extra(&self) -> (*mut u8, usize) {
        self.regions
            .map_or((core::ptr::null_mut(), 0), |r| (r.extra, r.extra_len))
    }

    /// Builds the decoder for `wanted` (or the best plan the memory runs).
    /// Call with the camera closed. False if no plan fits or the decoder
    /// refused its memory.
    pub fn build(&mut self, wanted: Option<Plan>) -> bool {
        self.regions = None;
        self.restart();
        let Some(plan) = self.mem.plan_for(wanted) else {
            return false;
        };
        let Some(r) = self.mem.regions(plan) else {
            return false;
        };
        // SAFETY: the state region is this scanner's (the image buffer it
        // holds locked, or the loan it holds), apart from the frames and the
        // extra buffer, reserved for the decoder while the scanner lives
        if !unsafe { camera::qr_init(plan.side, r.state, r.state_len) } {
            return false;
        }
        self.plan = plan;
        self.regions = Some(r);
        true
    }

    /// Forgets the frames in hand: after the camera was opened, closed or
    /// changed its mode (which stops a running capture)
    pub fn restart(&mut self) {
        self.capturing = None;
        self.shown = None;
        self.fail_run = 0;
        camera::qr_reset();
        camera::set_meter(None);
    }

    /// The frame to show: the last one that came in (whole or not)
    pub fn image(&self) -> Option<*const u8> {
        let r = self.regions.as_ref()?;
        self.shown.map(|i| r.frames[i] as *const u8)
    }

    fn start_capture(&mut self, r: &Regions) {
        // Two buffers: not the one being shown and decoded
        let into = match self.shown {
            Some(i) if self.plan.buffers > 1 => 1 - i,
            _ => 0,
        };
        // SAFETY: a frame region of the plan built (16-byte aligned, side x
        // side), which nothing reads until the capture is done or failed:
        // image() and the decoder take the other buffer, or (one buffer) the
        // capture starts only after the frame was decoded and painted
        let started = unsafe { camera::capture(r.frames[into], self.plan.side * self.plan.side) };
        self.capturing = started.then_some(into);
    }

    /// One tick of the scan loop: starts a capture if none runs, takes a
    /// finished frame through the decoder (a decoded code's bytes go to
    /// `payload`) and points the exposure at the code
    pub fn step(&mut self, payload: &mut [u8]) -> Scanned {
        let Some(r) = self.regions else {
            return Scanned::Nothing;
        };
        let Some(at) = self.capturing else {
            self.start_capture(&r);
            return Scanned::Nothing;
        };
        self.status = camera::status();
        match self.status.state {
            camera::STATE_BUSY => Scanned::Nothing,
            camera::STATE_DONE => {
                self.shown = Some(at);
                self.capturing = None;
                self.fail_run = 0;
                if self.plan.buffers > 1 {
                    self.start_capture(&r);
                }
                // SAFETY: a whole frame of the decoder's size; the capture
                // into it is done, and the next one writes the other buffer
                // (or has not started)
                let qr = unsafe { camera::qr_scan(r.frames[at], payload) };
                camera::set_meter(camera::qr_meter_area());
                Scanned::Frame(qr)
            }
            camera::STATE_FAILED => {
                // Shown once captures keep failing: a sheared or partial
                // frame tells a line length or sync fault apart. Not a lone
                // failure (a sensor's first frame after a start or a mode
                // change): its buffer is part frame, part stale memory
                self.fail_run = self.fail_run.saturating_add(1);
                if self.status.last_bytes > 0 && self.fail_run >= FAILS_SHOWN {
                    self.shown = Some(at);
                }
                self.capturing = None;
                Scanned::Failed
            }
            _ => {
                // Off or idle while capturing: the capture was lost
                self.capturing = None;
                Scanned::Nothing
            }
        }
    }
}

/// Failed captures in a row before their partial frames are shown
const FAILS_SHOWN: u8 = 3;

// --- Preview ----------------------------------------------------------------------

/// The camera module sits turned on the board: the preview turns the frame
/// back by this many quarter turns clockwise (then mirrors it if
/// PREVIEW_MIRROR), so that what is up in front of the device is up on the
/// screen. Only the preview and its markers turn: the decoder reads codes at
/// any angle.
const PREVIEW_QUARTERS: u8 = 3;
const PREVIEW_MIRROR: bool = false;

/// The frame pixel shown at preview position (x, y), both in frame pixels
fn preview_to_frame(x: usize, y: usize, side: usize) -> (usize, usize) {
    let m = side - 1;
    let x = if PREVIEW_MIRROR { m - x } else { x };
    match PREVIEW_QUARTERS & 3 {
        0 => (x, y),
        1 => (y, m - x),
        2 => (m - x, m - y),
        _ => (m - y, x),
    }
}

/// Where frame pixel (x, y) shows in the preview, in frame pixels
pub(super) fn frame_to_preview(x: usize, y: usize, side: usize) -> (usize, usize) {
    let m = side - 1;
    let (px, py) = match PREVIEW_QUARTERS & 3 {
        0 => (x, y),
        1 => (m - y, x),
        2 => (m - x, m - y),
        _ => (y, m - x),
    };
    (if PREVIEW_MIRROR { m - px } else { px }, py)
}

/// Draws a side x side grayscale frame scaled to `area` (VIEW x VIEW), as
/// white over what is beneath (black): the RGB565 screen can blend MONO8,
/// not copy it. Each preview pixel is the frame pixel at its position (no
/// averaging: the preview is as sharp as the frame). Band by band through a small buffer on the stack, only the bands the
/// canvas shows.
pub(super) struct GrayPreview {
    pub area: Rect,
    pub src: *const u8,
    pub side: usize,
}

impl GrayPreview {
    pub(super) fn render<'a>(self, renderer: &mut impl Renderer<'a>) {
        renderer.render_shape(self);
    }
}

const BAND: usize = 8;

impl<'a> Shape<'a> for GrayPreview {
    fn bounds(&self) -> Rect {
        self.area
    }

    fn cleanup(&mut self, _cache: &DrawingCache<'a>) {}

    fn draw(&mut self, canvas: &mut dyn Canvas, _cache: &DrawingCache<'a>) {
        let out = self.area.width().max(0) as usize;
        let rows_out = self.area.height().max(0) as usize;
        let side = self.side;
        if self.src.is_null() || side == 0 || out == 0 || out > VIEW as usize {
            return;
        }
        let viewport = canvas.viewport();
        let mut buf = [0u8; VIEW as usize * BAND];
        let mut y0 = 0;
        while y0 < rows_out {
            let rows = BAND.min(rows_out - y0);
            let band = Rect::from_top_left_and_size(
                self.area.top_left() + Offset::y(y0 as i16),
                Offset::new(out as i16, rows as i16),
            );
            if viewport.contains(band) {
                // SAFETY: the side x side frame, which the screen keeps
                // unchanged while it is painted
                let frame = unsafe { core::slice::from_raw_parts(self.src, side * side) };
                for j in 0..rows {
                    let py = (y0 + j) * side / rows_out;
                    let dst = &mut buf[j * out..(j + 1) * out];
                    for (x, d) in dst.iter_mut().enumerate() {
                        let (sx, sy) = preview_to_frame(x * side / out, py, side);
                        *d = frame[sy * side + sx];
                    }
                }
                if let Ok(bitmap) = Bitmap::new(
                    BitmapFormat::MONO8,
                    None,
                    Offset::new(out as i16, rows as i16),
                    None,
                    &buf[..out * rows],
                ) {
                    canvas.blend_bitmap(band, bitmap.view().with_fg(Color::white()));
                    // The blend may run on the DMA2D: done before the band
                    // buffer is reused or goes away
                    bitblt::wait_for_transfer();
                }
            }
            y0 += rows;
        }
    }
}

impl<'a> ShapeClone<'a> for GrayPreview {
    fn clone_at_bump<T>(self, bump: &'a T) -> Option<&'a mut dyn Shape<'a>>
    where
        T: LocalAllocLeakExt<'a>,
    {
        let clone = bump.alloc_t()?;
        Some(clone.uninit.init(GrayPreview { ..self }))
    }
}

// --- Text helpers -----------------------------------------------------------------

/// Appends `text`; a line longer than its buffer is cut (it would not fit on
/// the screen either)
pub(super) fn push_trunc(line: &mut Line, text: &str) {
    for c in text.chars() {
        if line.push(c).is_err() {
            return;
        }
    }
}

fn push_char(line: &mut Line, c: char) {
    let mut buf = [0u8; 4];
    push_trunc(line, c.encode_utf8(&mut buf));
}

pub(super) fn push_u32(line: &mut Line, mut v: u32) {
    let mut digits = [0u8; 10];
    let mut n = 0;
    loop {
        digits[n] = b'0' + (v % 10) as u8;
        n += 1;
        v /= 10;
        if v == 0 {
            break;
        }
    }
    for i in (0..n).rev() {
        push_char(line, digits[i] as char);
    }
}

fn push_hex(line: &mut Line, v: u32, digits: u32) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    push_trunc(line, "0x");
    for i in (0..digits).rev() {
        push_char(line, HEX[((v >> (4 * i)) & 0xF) as usize] as char);
    }
}

/// One decimal place from a value in tenths
pub(super) fn push_tenths(line: &mut Line, tenths: u32) {
    push_u32(line, tenths / 10);
    push_trunc(line, ".");
    push_u32(line, tenths % 10);
}

fn sensor_name(id: u32) -> &'static str {
    match id {
        camera::SENSOR_OV5640 => "OV5640",
        camera::SENSOR_GC2145 => "GC2145",
        camera::SENSOR_OV2640 => "OV2640",
        _ => "?",
    }
}

/// Why a grid did not decode (io/qr_scan.h numbers them as quirc did), short
fn qr_error_name(err: i32) -> &'static str {
    match err {
        1 => "GRID SIZE",
        2 => "VERSION",
        3 => "FORMAT ECC",
        4 => "DATA ECC",
        5 => "DATA TYPE",
        6 => "OVERFLOW",
        7 => "UNDERFLOW",
        _ => "?",
    }
}

/// "v13 3.4px/m" for the code the decoder reported (decoded or not); the
/// version from the grid it measured when it did not decode
pub(super) fn push_code(line: &mut Line, qr: &camera::QrResult) {
    let version = if qr.decoded != 0 {
        qr.version
    } else if qr.grid_size >= 21 {
        (qr.grid_size - 17) / 4
    } else {
        0
    };
    push_trunc(line, "v");
    push_u32(line, version.max(0) as u32);
    if qr.decoded == 0 {
        push_trunc(line, "?");
    }
    push_trunc(line, " ");
    push_tenths(line, qr.px_per_module_x10.max(0) as u32);
    push_trunc(line, "px/m");
}

/// "320x2": the frame's side and, with two, the buffers
pub(super) fn push_plan(line: &mut Line, plan: Plan) {
    push_u32(line, plan.side as u32);
    if plan.buffers > 1 {
        push_trunc(line, "x");
        push_u32(line, plan.buffers as u32);
    }
}

pub(super) fn zoom_name(zoom: u32) -> &'static str {
    match zoom {
        0 => "wide",
        1 => "2:1",
        _ => "native",
    }
}

/// The tuning button: what each setting is on the sensor running
/// (io/camera.h)
fn tune_name(sensor: u32, tune: u32) -> &'static str {
    match (sensor, tune) {
        (camera::SENSOR_GC2145, 0) => "skip",
        (camera::SENSOR_GC2145, 1) => "scaler",
        (camera::SENSOR_GC2145, _) => "screen",
        (_, 0) => "auto exp",
        (_, 1) => "sens AE",
        _ => "scr AE",
    }
}

// --- The test ---------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq)]
enum Phase {
    /// Painted "starting"; the next frame tick opens the camera
    Starting,
    Running,
    /// The camera did not start (the status says why)
    Failed,
}

pub struct CameraTest {
    active: bool,
    phase: Phase,
    scanner: Scanner,
    settings: Settings,
    status: camera::Status,
    qr: camera::QrResult,
    payload: [u8; PAYLOAD_MAX],
    payload_shown: usize,
    decoded_at: Option<Instant>,
    decodes: u32,
    /// The last frames' decode results, one bit each (1: decoded), newest in
    /// bit 0, and how many of the 16 are filled
    history: u16,
    history_len: u32,
    fps_since: Option<Instant>,
    fps_frames: u32,
    fps_tenths: u32,
    lines: [Line; 3],
    line_colors: [Color; 3],
    failure: [Line; 4],
}

impl CameraTest {
    pub fn new() -> Result<Self, Error> {
        Ok(Self {
            active: false,
            phase: Phase::Starting,
            scanner: Scanner::new(0, 0)?,
            settings: settings(),
            // SAFETY: plain C structs of integers; all zero is valid
            status: unsafe { core::mem::zeroed() },
            qr: unsafe { core::mem::zeroed() },
            payload: [0; PAYLOAD_MAX],
            payload_shown: 0,
            decoded_at: None,
            decodes: 0,
            history: 0,
            history_len: 0,
            fps_since: None,
            fps_frames: 0,
            fps_tenths: 0,
            lines: [Line::new(), Line::new(), Line::new()],
            line_colors: [Color::white(); 3],
            failure: [Line::new(), Line::new(), Line::new(), Line::new()],
        })
    }

    pub fn is_active(&self) -> bool {
        self.active
    }

    pub fn start(&mut self, ctx: &mut EventCtx) {
        self.active = true;
        self.decodes = 0;
        self.begin_open(ctx);
    }

    fn begin_open(&mut self, ctx: &mut EventCtx) {
        self.phase = Phase::Starting;
        self.reset_counters();
        for l in self.lines.iter_mut() {
            l.clear();
        }
        push_trunc(&mut self.lines[0], "Starting the camera...");
        self.line_colors = [Color::white(); 3];
        ctx.request_paint();
        ctx.request_anim_frame();
    }

    fn reset_counters(&mut self) {
        self.decoded_at = None;
        self.history = 0;
        self.history_len = 0;
        self.fps_since = None;
        self.fps_frames = 0;
        self.fps_tenths = 0;
        // SAFETY: as in new()
        self.qr = unsafe { core::mem::zeroed() };
    }

    fn stop(&mut self, ctx: &mut EventCtx) {
        camera::close();
        self.scanner.restart();
        self.active = false;
        ctx.request_paint();
    }

    fn mode(&self) -> camera::Mode {
        camera::Mode {
            zoom: self.settings.zoom,
            side: self.scanner.side(),
            clock: self.settings.clock,
            tune: self.settings.tune,
        }
    }

    fn open(&mut self) {
        if !self.scanner.build(self.settings.plan) {
            self.set_failure(&["QR decoder setup failed", "", "", ""]);
            self.phase = Phase::Failed;
            return;
        }
        let opened = camera::open(self.mode());
        self.status = camera::status();
        if opened {
            self.phase = Phase::Running;
            self.update_lines();
        } else {
            self.phase = Phase::Failed;
            self.failure_lines();
        }
    }

    /// Returns true when the event belongs to the test (it always does while
    /// the test runs).
    pub fn event(&mut self, ctx: &mut EventCtx, event: Event) -> bool {
        if !self.active {
            return false;
        }
        match event {
            Event::Touch(TouchEvent::TouchStart(p)) => {
                if p.x < EXIT_SIZE && p.y < EXIT_SIZE {
                    self.stop(ctx);
                } else if self.phase == Phase::Failed {
                    self.begin_open(ctx);
                } else if self.phase == Phase::Running && p.y < VIEW {
                    let mut s = self.settings;
                    s.zoom = (s.zoom + 1) % camera::ZOOM_LEVELS;
                    self.change(ctx, s);
                } else if self.phase == Phase::Running && p.y >= BUTTON_TOP {
                    let mut s = self.settings;
                    match p.x / BUTTON_W {
                        0 => {
                            // The plan after this one, of those the memory runs
                            let plans: heapless::Vec<Plan, { PLANS.len() }> =
                                self.scanner.memory().plans().collect();
                            let plan = self.scanner.plan();
                            let at = plans.iter().position(|&p| p == plan).unwrap_or(0);
                            s.plan = plans.get((at + 1) % plans.len().max(1)).copied();
                        }
                        1 => s.clock = (s.clock + 1) % camera::CLOCKS,
                        _ => s.tune = (s.tune + 1) % camera::TUNES,
                    }
                    self.change(ctx, s);
                }
            }
            _ if EventCtx::is_anim_frame(event) => {
                self.step(ctx);
                ctx.request_anim_frame();
            }
            _ => {}
        }
        true
    }

    /// Switches to new settings (kept for the QR scan too); blocks while the
    /// sensor settles
    fn change(&mut self, ctx: &mut EventCtx, s: Settings) {
        self.settings = s;
        set_settings(s);
        // Closed, then reopened by the next tick rather than switched (which
        // also rebuilds the decoder for the plan): a size change needs the
        // DCMI and DMA set up again, and a reopen is the path known to work
        camera::close();
        self.scanner.restart();
        self.begin_open(ctx);
    }

    fn step(&mut self, ctx: &mut EventCtx) {
        match self.phase {
            Phase::Starting => {
                self.open();
                ctx.request_paint();
            }
            Phase::Running => {
                let mut payload = [0u8; PAYLOAD_MAX];
                match self.scanner.step(&mut payload) {
                    Scanned::Nothing => return,
                    Scanned::Frame(qr) => {
                        self.count_frame();
                        self.expire_decoded();
                        self.took(qr, &payload);
                    }
                    Scanned::Failed => self.expire_decoded(),
                }
                self.status = self.scanner.status;
                self.update_lines();
                ctx.request_paint();
            }
            Phase::Failed => {}
        }
    }

    fn count_frame(&mut self) {
        let now = Instant::now();
        match self.fps_since {
            None => {
                self.fps_since = Some(now);
                self.fps_frames = 0;
            }
            Some(t) => {
                self.fps_frames += 1;
                let ms = now.saturating_duration_since(t).to_millis();
                if ms >= 1000 {
                    self.fps_tenths = self.fps_frames * 10_000 / ms;
                    self.fps_since = Some(now);
                    self.fps_frames = 0;
                }
            }
        }
    }

    fn expire_decoded(&mut self) {
        if let Some(t) = self.decoded_at {
            if Instant::now().saturating_duration_since(t).to_millis() > DECODED_SHOW_MS {
                self.decoded_at = None;
            }
        }
    }

    /// A frame's result, for the strip and the picture's markers
    fn took(&mut self, result: camera::QrResult, payload: &[u8; PAYLOAD_MAX]) {
        self.qr = result;
        self.history = (self.history << 1) | (result.decoded != 0) as u16;
        self.history_len = (self.history_len + 1).min(16);
        if result.decoded != 0 {
            self.payload = *payload;
            self.payload_shown = (result.payload_len.max(0) as usize).min(PAYLOAD_MAX);
            self.decoded_at = Some(Instant::now());
            self.decodes += 1;
        }
    }

    fn set_failure(&mut self, texts: &[&str; 4]) {
        for (line, text) in self.failure.iter_mut().zip(texts.iter()) {
            line.clear();
            push_trunc(line, text);
        }
    }

    /// The status strip while running
    fn update_lines(&mut self) {
        let st = self.status;
        let qr = self.qr;
        let [l0, l1, l2] = &mut self.lines;
        for l in [&mut *l0, &mut *l1, &mut *l2] {
            l.clear();
        }
        self.line_colors = [Color::white(), GREY, GREY];

        // Sensor and mode
        push_trunc(l0, sensor_name(st.sensor_id));
        push_trunc(l0, " ");
        push_plan(l0, self.scanner.plan());
        push_trunc(l0, " ");
        push_trunc(l0, zoom_name(st.zoom));
        push_trunc(l0, " ");
        push_tenths(l0, st.scale_x10);
        push_trunc(l0, "x ");
        push_tenths(l0, self.fps_tenths);
        push_trunc(l0, "fps");

        // The decoder's view: the code, the result, the decode rate
        let rate = (self.history & (((1u32 << self.history_len) - 1) as u16)).count_ones();
        if qr.grids > 0 {
            push_code(l1, &qr);
            push_trunc(l1, " ");
            if qr.decoded != 0 {
                push_trunc(l1, "OK");
                self.line_colors[1] = GREEN;
            } else {
                push_trunc(l1, qr_error_name(qr.error));
                self.line_colors[1] = PURPLE;
            }
        } else {
            push_trunc(l1, "no QR");
        }
        push_trunc(l1, " ");
        push_u32(l1, rate);
        push_trunc(l1, "/");
        push_u32(l1, self.history_len);
        // Frames the decoder summed for this result (1: this frame alone)
        push_trunc(l1, " f");
        push_u32(l1, qr.fused.max(0) as u32);
        if qr.cut != 0 {
            // The search for a code ran into its time budget
            push_trunc(l1, " cut");
        }

        if st.state == camera::STATE_FAILED {
            push_trunc(l2, "FAIL ");
            push_u32(l2, st.last_bytes);
            push_trunc(l2, "B fl ");
            push_hex(l2, st.last_flags, 3);
            push_trunc(l2, " #");
            push_u32(l2, st.failures);
            self.line_colors[2] = RED;
        } else if st.frames == 0 {
            // No frame yet: what the sync pins did after the sensor started
            push_trunc(l2, "VS ");
            push_u32(l2, st.vsync_high_pm);
            push_trunc(l2, "/");
            push_u32(l2, st.vsync_edges);
            push_trunc(l2, " HR ");
            push_u32(l2, st.href_high_pm);
            push_trunc(l2, "/");
            push_u32(l2, st.href_edges);
            push_trunc(l2, " PC ");
            push_u32(l2, st.pclk_edges);
        } else {
            // Exposure and focus of the frame, and the times. With the
            // exposure set from the frames: its length, the gain, and "*"
            // when the light was metered on the code; else the mean
            // brightness.
            if st.ae_exposure_us > 0 {
                push_tenths(l2, st.ae_exposure_us / 100);
                push_trunc(l2, "ms x");
                push_tenths(l2, st.ae_gain16 * 10 / 16);
                push_trunc(l2, if st.ae_on_code != 0 { "*" } else { "" });
            } else {
                push_trunc(l2, "Y");
                push_u32(l2, qr.mean_y.max(0) as u32);
            }
            push_trunc(l2, " clip");
            push_u32(l2, (qr.clipped_pm.max(0) as u32).div_ceil(10));
            push_trunc(l2, "% edge");
            push_tenths(l2, qr.edge_x10.max(0) as u32);
            push_trunc(l2, " ");
            push_u32(l2, st.last_capture_ms);
            push_trunc(l2, "+");
            push_u32(l2, qr.identify_ms + qr.decode_ms);
            push_trunc(l2, "ms");
        }
    }

    /// Why the camera did not start
    fn failure_lines(&mut self) {
        let st = self.status;
        let [l0, l1, l2, l3] = &mut self.failure;
        for l in [&mut *l0, &mut *l1, &mut *l2, &mut *l3] {
            l.clear();
        }

        match st.open_error {
            camera::ERR_NO_SENSOR => push_trunc(l0, "No camera at SCCB 0x30/0x3C"),
            camera::ERR_UNKNOWN_ID => {
                push_trunc(l0, "Unknown sensor id ");
                push_hex(l0, st.raw_id, 4);
            }
            camera::ERR_SCCB_WRITE => {
                push_trunc(l0, sensor_name(st.sensor_id));
                push_trunc(l0, ": write ");
                push_hex(l0, st.error_reg, 4);
                push_trunc(l0, " failed");
            }
            camera::ERR_BAD_MODE => push_trunc(l0, "Mode out of range"),
            _ => push_trunc(l0, "Camera did not start"),
        }

        push_trunc(l1, "bus acks:");
        let mut any = false;
        for addr in 0..128u32 {
            if (st.i2c_acks[(addr / 8) as usize] >> (addr % 8)) & 1 != 0 {
                push_trunc(l1, " ");
                push_hex(l1, addr, 2);
                any = true;
            }
        }
        if !any {
            push_trunc(l1, " none");
        }

        // The bus lines' idle levels, the I2C error, whether XCLK runs
        push_trunc(l2, "SCL ");
        push_u32(l2, st.i2c_lines & 1);
        push_trunc(l2, " SDA ");
        push_u32(l2, (st.i2c_lines >> 1) & 1);
        push_trunc(l2, " err ");
        push_hex(l2, st.i2c_error, 2);
        push_trunc(l3, "XCLK ");
        push_u32(l3, st.xclk_edges);
        push_trunc(l3, "  tap: retry");
    }

    /// The payload's printable start, as wide as the view
    fn payload_line(&self) -> Line {
        let mut line = Line::new();
        let max_w = VIEW - 2 * MARGIN;
        for &b in &self.payload[..self.payload_shown] {
            let c = if (0x20..0x7f).contains(&b) { b as char } else { '.' };
            if line.push(c).is_err() {
                break;
            }
            if fonts::FONT_SUB.text_width(line.as_str()) > max_w {
                line.pop();
                break;
            }
        }
        line
    }

    fn render_button<'s>(target: &mut impl Renderer<'s>, i: i16, text: &str) {
        let r = Rect::new(
            Point::new(i * BUTTON_W + 2, BUTTON_TOP + 1),
            Point::new((i + 1) * BUTTON_W - 2, HEIGHT - 1),
        );
        shape::Bar::new(r).with_bg(DARK_GREY).with_radius(4).render(target);
        shape::Text::new(r.center() + Offset::y(6), text, fonts::FONT_SUB)
            .with_align(Alignment::Center)
            .with_fg(Color::white())
            .render(target);
    }

    pub fn render<'s>(&'s self, target: &mut impl Renderer<'s>) {
        shape::Bar::new(Rect::new(Point::zero(), Point::new(WIDTH, HEIGHT)))
            .with_bg(Color::black())
            .render(target);

        // The last frame that came in: the camera is writing the other
        // buffer, or (one buffer) starts the next capture only at the tick
        // after this paint
        let view = Rect::new(Point::zero(), Point::new(VIEW, VIEW));
        let side = self.scanner.side();
        if let Some(src) = self.scanner.image() {
            GrayPreview { area: view, src, side }.render(target);
        }

        if self.decoded_at.is_some() {
            shape::Bar::new(view)
                .with_fg(GREEN)
                .with_thickness(3)
                .render(target);
        } else if self.status.state == camera::STATE_FAILED && self.phase == Phase::Running {
            shape::Bar::new(view)
                .with_fg(RED)
                .with_thickness(2)
                .render(target);
        }
        // The code's corners, scaled to the view: green decoded, purple not
        if self.qr.grids > 0 && side > 0 && self.phase == Phase::Running {
            let color = if self.qr.decoded != 0 { GREEN } else { PURPLE };
            let c = &self.qr.corners;
            for i in 0..4 {
                let clamp = |v: i32| v.clamp(0, side as i32 - 1) as usize;
                let (fx, fy) = frame_to_preview(clamp(c[2 * i]), clamp(c[2 * i + 1]), side);
                let x = (fx * VIEW as usize / side) as i16;
                let y = (fy * VIEW as usize / side) as i16;
                let p = Point::new(x, y);
                shape::Bar::new(Rect::new(p - Offset::new(3, 3), p + Offset::new(4, 4)))
                    .with_bg(color)
                    .render(target);
            }
        }
        if self.decoded_at.is_some() {
            let strip = Rect::new(Point::new(0, VIEW - 22), Point::new(VIEW, VIEW));
            shape::Bar::new(strip).with_bg(Color::black()).render(target);
            shape::Text::new(Point::new(MARGIN, VIEW - 5), self.payload_line().as_str(), fonts::FONT_SUB)
                .with_fg(GREEN)
                .render(target);
        }

        // Exit corner
        shape::Bar::new(Rect::new(Point::new(6, 6), Point::new(EXIT_SIZE - 6, EXIT_SIZE - 6)))
            .with_bg(Color::black())
            .with_fg(GREEN)
            .with_thickness(2)
            .render(target);
        shape::Text::new(
            Point::new(EXIT_SIZE / 2, EXIT_SIZE / 2 + 7),
            "x",
            fonts::FONT_DEMIBOLD,
        )
        .with_align(Alignment::Center)
        .with_fg(GREEN)
        .render(target);

        if self.phase == Phase::Failed {
            // A failed start uses the screen below the corner
            let colors = [RED, Color::white(), Color::white(), GREY];
            for (i, line) in self.failure.iter().enumerate() {
                shape::Text::new(
                    Point::new(MARGIN, VIEW / 2 + LINE_H * (i as i16 + 1)),
                    line.as_str(),
                    fonts::FONT_SUB,
                )
                .with_fg(colors[i])
                .render(target);
            }
            return;
        }

        for (i, line) in self.lines.iter().enumerate() {
            shape::Text::new(
                Point::new(MARGIN, VIEW + LINE_H * (i as i16 + 1) - 1),
                line.as_str(),
                fonts::FONT_SUB,
            )
            .with_fg(self.line_colors[i])
            .render(target);
        }

        if self.phase == Phase::Running {
            let mut size = Line::new();
            push_plan(&mut size, self.scanner.plan());
            Self::render_button(target, 0, size.as_str());
            Self::render_button(
                target,
                1,
                if self.settings.clock == 0 { "clk std" } else { "clk fast" },
            );
            Self::render_button(
                target,
                2,
                tune_name(self.status.sensor_id, self.settings.tune),
            );
        }
    }
}

impl Drop for CameraTest {
    fn drop(&mut self) {
        // The screen is going away (another layout took over): the DMA must
        // not keep writing a buffer that is no longer ours
        if self.active {
            camera::close();
        }
    }
}
