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
//! Memory ([`ScanMemory`]): the frame is the UI's one full-screen image buffer
//! (150 KB, which the screen holds); the decoder's state (and the QR scan's
//! text and UR work buffer) a loan from the MicroPython heap while the screen
//! lives. Without the loan, everything shares the image buffer and the frame
//! is smaller. The DMA writes each frame straight into the decoder's image;
//! the frame is painted first, then decoded (which thresholds it in place),
//! then the next one captured. Bring-up only: nothing leaves the device.

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
/// The frame's side with the heap loan (the image buffer holds 384 x 384),
/// and without it (frame, decoder and UR all in the image buffer)
pub(super) const SIDE_LARGE: usize = 384;
pub(super) const SIDE_SMALL: usize = 288;
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
    /// 0: the largest the memory allows
    pub side: usize,
    pub clock: u32,
    pub tune: u32,
}

/// The middle zoom first (see io/camera/stm32u5/camera.c)
static mut SETTINGS: Settings = Settings {
    zoom: 1,
    side: 0,
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

/// Where a scanner's frame, decoder state and extra buffers (the QR scan's
/// text and UR work) are, for one frame side
pub(super) struct Regions {
    pub image: *mut u8,
    pub side: usize,
    pub state: *mut u8,
    pub state_len: usize,
    pub extra: *mut u8,
    pub extra_len: usize,
}

/// The memory a scanner runs in: the UI's image buffer (held locked) for the
/// frame, and a loan from the MicroPython heap for the decoder's state and
/// the extra buffers, if the heap had it.
pub(super) struct ScanMemory {
    _buffer: ImageBuffer<Rgb565Canvas<'static>>,
    image: *mut u8,
    image_len: usize,
    /// Null without a loan. Not freed by hand: the screen (and the pointer in
    /// it) lives in the heap, and the next collection after the screen is
    /// gone frees the loan with it.
    loan: *mut u8,
    loan_len: usize,
}

impl ScanMemory {
    /// Locks the image buffer and asks the heap for the decoder's state at
    /// SIDE_LARGE plus `extra_want` bytes
    pub fn new(extra_want: usize) -> Result<Self, Error> {
        // The size is nominal: the scanner takes the whole buffer's bytes
        let mut buffer = ImageBuffer::<Rgb565Canvas<'static>>::new(Offset::new(1, 1))?;
        let bytes = buffer.raw_bytes();
        let (image, image_len) = (bytes.as_mut_ptr(), bytes.len());
        let want = camera::qr_state_size(SIDE_LARGE).next_multiple_of(16) + extra_want;
        // SAFETY: gc_alloc() returns null when the heap has no such block (it
        // collects once first) instead of raising
        let mut loan = unsafe { upy::gc_alloc(want, 0) } as *mut u8;
        if !loan.is_null() && (loan as usize) % 8 != 0 {
            // The heap's blocks are 16-byte aligned; anything else is not
            // used (left to the collector)
            loan = core::ptr::null_mut();
        }
        Ok(Self {
            _buffer: buffer,
            image,
            image_len,
            loan,
            loan_len: if loan.is_null() { 0 } else { want },
        })
    }

    pub fn has_loan(&self) -> bool {
        !self.loan.is_null()
    }

    /// The frame sides this memory runs, largest first
    pub fn sides(&self) -> &'static [usize] {
        if self.has_loan() {
            &[SIDE_LARGE, SIDE_SMALL, 240]
        } else {
            &[SIDE_SMALL, 240]
        }
    }

    /// The regions for a side x side frame with at least `extra_min` extra
    /// bytes; None if they do not fit
    pub fn regions(&self, side: usize, extra_min: usize) -> Option<Regions> {
        let frame = side * side;
        let state_len = camera::qr_state_size(side).next_multiple_of(16);
        if frame > self.image_len {
            return None;
        }
        // The frame first in the image buffer (16-byte aligned); the state
        // and the extra after it, or in the loan
        let (rest, rest_len) = if self.has_loan() {
            (self.loan, self.loan_len)
        } else {
            let at = frame.next_multiple_of(16);
            if at > self.image_len {
                return None;
            }
            // SAFETY: within the image buffer
            (unsafe { self.image.add(at) }, self.image_len - at)
        };
        if rest_len < state_len + extra_min {
            return None;
        }
        Some(Regions {
            image: self.image,
            side,
            state: rest,
            state_len,
            // SAFETY: within the loan or the image buffer (checked above)
            extra: unsafe { rest.add(state_len) },
            extra_len: rest_len - state_len,
        })
    }

    /// The side to run: `wanted` if this memory runs it, else the largest
    pub fn side_for(&self, wanted: usize) -> usize {
        let sides = self.sides();
        if sides.contains(&wanted) {
            wanted
        } else {
            sides[0]
        }
    }
}

// --- Preview ----------------------------------------------------------------------

/// Draws a side x side grayscale frame scaled to `area` (VIEW x VIEW), as
/// white over what is beneath (black): the RGB565 screen can blend MONO8,
/// not copy it. Each preview pixel averages the 2x2 frame pixels at its
/// position (a low-pass, so the preview shows no aliasing the decoder does not
/// see). Band by band through a small buffer on the stack, only the bands the
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
                for j in 0..rows {
                    let sy = (y0 + j) * side / rows_out;
                    let sy1 = (sy + 1).min(side - 1);
                    // SAFETY: rows sy and sy1 lie in the side x side frame,
                    // which the screen keeps unchanged while it is painted
                    let (r0, r1) = unsafe {
                        (
                            core::slice::from_raw_parts(self.src.add(sy * side), side),
                            core::slice::from_raw_parts(self.src.add(sy1 * side), side),
                        )
                    };
                    let dst = &mut buf[j * out..(j + 1) * out];
                    for (x, d) in dst.iter_mut().enumerate() {
                        let sx = x * side / out;
                        let sx1 = (sx + 1).min(side - 1);
                        let sum = r0[sx] as u32 + r0[sx1] as u32 + r1[sx] as u32 + r1[sx1] as u32;
                        *d = (sum / 4) as u8;
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

/// quirc_decode_error_t, short
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

pub(super) fn zoom_name(zoom: u32) -> &'static str {
    match zoom {
        0 => "wide",
        1 => "2:1",
        _ => "native",
    }
}

fn tune_name(tune: u32) -> &'static str {
    match tune {
        0 => "auto exp",
        1 => "sens AE",
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
    mem: ScanMemory,
    settings: Settings,
    /// The decoder's image, which the camera captures into (null before the
    /// decoder is built), and its side
    image: *mut u8,
    side: usize,
    /// A frame (whole or not) is in the image to show
    have_frame: bool,
    capturing: bool,
    /// The frame in the image has been painted, not decoded yet
    scan_pending: bool,
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
            mem: ScanMemory::new(0)?,
            settings: settings(),
            image: core::ptr::null_mut(),
            side: 0,
            have_frame: false,
            capturing: false,
            scan_pending: false,
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
        self.have_frame = false;
        self.capturing = false;
        self.scan_pending = false;
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
        self.active = false;
        self.capturing = false;
        self.image = core::ptr::null_mut();
        ctx.request_paint();
    }

    fn mode(&self) -> camera::Mode {
        camera::Mode {
            zoom: self.settings.zoom,
            side: self.side,
            clock: self.settings.clock,
            tune: self.settings.tune,
        }
    }

    /// Builds the decoder for the settings' side (or the largest the memory
    /// runs); false with the failure lines set
    fn build_decoder(&mut self) -> bool {
        let side = self.mem.side_for(self.settings.side);
        let ok = match self.mem.regions(side, 0) {
            // SAFETY: the regions are this screen's (the image buffer it holds
            // locked, the loan it holds), reserved for the decoder while it
            // runs
            Some(r) => unsafe { camera::qr_init(r.image, side, r.state, r.state_len) },
            None => false,
        };
        if !ok {
            self.image = core::ptr::null_mut();
            self.set_failure(&["QR decoder setup failed", "", "", ""]);
            return false;
        }
        self.image = self.mem.regions(side, 0).map_or(core::ptr::null_mut(), |r| r.image);
        self.side = side;
        true
    }

    fn open(&mut self) {
        if !self.build_decoder() {
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

    fn start_capture(&mut self) {
        // SAFETY: the decoder's image (16-byte aligned, side x side), untouched
        // until the capture is done or failed: step() paints and decodes it
        // only then
        self.capturing = unsafe { camera::capture(self.image, self.side * self.side) };
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
                            let sides = self.mem.sides();
                            let at = sides.iter().position(|&v| v == self.side).unwrap_or(0);
                            s.side = sides[(at + 1) % sides.len()];
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
        let new_side = self.mem.side_for(s.side);
        self.settings = Settings { side: new_side, ..s };
        set_settings(self.settings);
        // Stops a running capture before the decoder's memory changes
        camera::close();
        self.reset_counters();
        if new_side != self.side && !self.build_decoder() {
            self.phase = Phase::Failed;
            ctx.request_paint();
            return;
        }
        // Reopened rather than switched: a size change needs the DCMI and
        // DMA set up again, and a reopen is the path known to work
        self.begin_open(ctx);
    }

    fn step(&mut self, ctx: &mut EventCtx) {
        match self.phase {
            Phase::Starting => {
                self.open();
                ctx.request_paint();
            }
            Phase::Running => {
                if !self.capturing {
                    // The last frame has been painted: decode it (in place),
                    // then capture the next into the same image
                    if self.scan_pending {
                        self.scan_pending = false;
                        self.scan();
                    }
                    self.start_capture();
                    return;
                }
                self.status = camera::status();
                match self.status.state {
                    camera::STATE_BUSY => return,
                    camera::STATE_DONE => {
                        self.have_frame = true;
                        self.scan_pending = true;
                        self.count_frame();
                        self.expire_decoded();
                    }
                    camera::STATE_FAILED => {
                        // Show what arrived anyway: a sheared or partial
                        // frame tells a line length or sync fault apart
                        self.have_frame = self.status.last_bytes > 0;
                        self.expire_decoded();
                    }
                    _ => {
                        // Off or idle while capturing: the capture was lost
                    }
                }
                self.capturing = false;
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

    fn scan(&mut self) {
        let mut payload = [0u8; PAYLOAD_MAX];
        // SAFETY: called between captures only (step())
        let result = unsafe { camera::qr_scan(&mut payload) };
        self.qr = result;
        self.history = (self.history << 1) | (result.decoded != 0) as u16;
        self.history_len = (self.history_len + 1).min(16);
        if result.decoded != 0 {
            self.payload = payload;
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
        push_u32(l0, st.side);
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
        // The threshold the frame was cut with (0 Otsu, 1 Otsu x1.1, 2 local)
        push_trunc(l1, " t");
        push_u32(l1, qr.threshold.max(0) as u32);

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
            // Exposure and focus of the frame, and the times
            push_trunc(l2, "Y");
            push_u32(l2, qr.mean_y.max(0) as u32);
            push_trunc(l2, " clip ");
            push_u32(l2, (qr.clipped_pm.max(0) as u32).div_ceil(10));
            push_trunc(l2, "% edge ");
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

        // Repaints come right after a capture ends (step()), before the next
        // one starts; one forced while the DMA writes would show it half done
        let view = Rect::new(Point::zero(), Point::new(VIEW, VIEW));
        if self.have_frame && !self.image.is_null() {
            GrayPreview {
                area: view,
                src: self.image,
                side: self.side,
            }
            .render(target);
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
        if self.qr.grids > 0 && self.side > 0 && self.phase == Phase::Running {
            let color = if self.qr.decoded != 0 { GREEN } else { PURPLE };
            let c = &self.qr.corners;
            for i in 0..4 {
                let x = (c[2 * i] as i32 * VIEW as i32 / self.side as i32) as i16;
                let y = (c[2 * i + 1] as i32 * VIEW as i32 / self.side as i32) as i16;
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
            push_u32(&mut size, self.side as u32);
            push_trunc(&mut size, "px");
            Self::render_button(target, 0, size.as_str());
            Self::render_button(
                target,
                1,
                if self.settings.clock == 0 { "clk std" } else { "clk fast" },
            );
            Self::render_button(target, 2, tune_name(self.settings.tune));
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
