//! Ambire boards, [ambire_camera]: a QR camera test behind the swipe up on
//! the home screen (in place of Bluetooth pairing while it is built in).
//!
//! The top 240x240 shows the camera's live grayscale frame; the strip below
//! the sensor, the frame rate and counters, and the last QR code decoded.
//! Tap the frame to step the zoom; the top-left corner exits. When the camera
//! does not start, the screen says why (what answered on its bus, which
//! register write failed); a tap retries.
//!
//! Memory: the home screen holds the UI's one full-screen image buffer but
//! never draws it, so the test borrows it while it runs, as the QR decoder's
//! arena. The DMA writes each frame straight into the decoder's image; the
//! frame is painted first, then decoded (which thresholds it in place), then
//! the next one captured. Bring-up only: nothing leaves the device.

use heapless::String;
use without_alloc::alloc::LocalAllocLeakExt;

use sys::time::Instant;

use super::super::fonts;
use crate::trezorhal::camera;
use crate::ui::component::{Event, EventCtx};
use crate::ui::constant::{HEIGHT, WIDTH};
use crate::ui::display::Color;
use crate::ui::event::TouchEvent;
use crate::ui::geometry::{Alignment, Offset, Point, Rect};
use crate::ui::shape::{
    self, Bitmap, BitmapFormat, BitmapView, Canvas, DrawingCache, Renderer, Shape, ShapeClone,
};

/// The frame is drawn 1:1 at the top of the screen
const VIEW: i16 = camera::FRAME_W as i16;
const EXIT_SIZE: i16 = 48;
const LINE_H: i16 = 19;
const MARGIN: i16 = 4;
/// A decoded code stays on screen this long after it was last seen
const DECODED_SHOW_MS: u32 = 1500;
/// Zoom the test starts at: the middle one (see io/camera/stm32u5/camera.c)
const START_ZOOM: u32 = 1;
const PAYLOAD_MAX: usize = 120;

const GREEN: Color = Color::rgb(0xd7, 0xff, 0x00);
const PURPLE: Color = Color::rgb(0xaf, 0x50, 0xff);
const RED: Color = Color::rgb(0xff, 0x40, 0x40);
const GREY: Color = Color::rgb(0xa0, 0xa0, 0xa0);

type Line = String<64>;

#[derive(Clone, Copy, PartialEq)]
enum Phase {
    /// Painted "starting"; the next frame tick opens the camera
    Starting,
    Running,
    /// The camera did not start (the status says why)
    Failed,
}

/// Draws an 8-bit grayscale bitmap as white over what is beneath (black):
/// the RGB565 screen can blend MONO8, not copy it
struct GrayImage<'a> {
    area: Rect,
    view: BitmapView<'a>,
}

impl<'a> GrayImage<'a> {
    fn render(self, renderer: &mut impl Renderer<'a>) {
        renderer.render_shape(self);
    }
}

impl<'a> Shape<'a> for GrayImage<'a> {
    fn bounds(&self) -> Rect {
        self.area
    }

    fn cleanup(&mut self, _cache: &DrawingCache<'a>) {}

    fn draw(&mut self, canvas: &mut dyn Canvas, _cache: &DrawingCache<'a>) {
        canvas.blend_bitmap(self.area, self.view);
    }
}

impl<'a> ShapeClone<'a> for GrayImage<'a> {
    fn clone_at_bump<T>(self, bump: &'a T) -> Option<&'a mut dyn Shape<'a>>
    where
        T: LocalAllocLeakExt<'a>,
    {
        let clone = bump.alloc_t()?;
        Some(clone.uninit.init(GrayImage { ..self }))
    }
}

/// Appends `text`; a line longer than its buffer is cut (it would not fit on
/// the screen either)
fn push_trunc(line: &mut Line, text: &str) {
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

fn push_u32(line: &mut Line, mut v: u32) {
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
fn push_tenths(line: &mut Line, tenths: u32) {
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

pub struct CameraTest {
    active: bool,
    phase: Phase,
    /// The borrowed image buffer: the QR decoder's arena
    scratch: *mut u8,
    scratch_len: usize,
    /// The decoder's image, which the camera captures into
    image: *mut u8,
    frame: Option<Bitmap<'static>>,
    /// A frame (whole or not) is in the image to show
    have_frame: bool,
    capturing: bool,
    /// The frame in the image has been painted, not decoded yet
    scan_pending: bool,
    zoom: u32,
    status: camera::Status,
    qr: camera::QrResult,
    payload: [u8; PAYLOAD_MAX],
    payload_shown: usize,
    decoded_at: Option<Instant>,
    decodes: u32,
    fps_since: Option<Instant>,
    fps_frames: u32,
    fps_tenths: u32,
    lines: [Line; 4],
    line_colors: [Color; 4],
}

impl CameraTest {
    pub fn new() -> Self {
        Self {
            active: false,
            phase: Phase::Starting,
            scratch: core::ptr::null_mut(),
            scratch_len: 0,
            image: core::ptr::null_mut(),
            frame: None,
            have_frame: false,
            capturing: false,
            scan_pending: false,
            zoom: START_ZOOM,
            // SAFETY: plain C structs of integers; all zero is valid
            status: unsafe { core::mem::zeroed() },
            qr: unsafe { core::mem::zeroed() },
            payload: [0; PAYLOAD_MAX],
            payload_shown: 0,
            decoded_at: None,
            decodes: 0,
            fps_since: None,
            fps_frames: 0,
            fps_tenths: 0,
            lines: [Line::new(), Line::new(), Line::new(), Line::new()],
            line_colors: [Color::white(); 4],
        }
    }

    pub fn is_active(&self) -> bool {
        self.active
    }

    /// Starts the test. `scratch` is the home screen's image buffer: it must
    /// stay untouched by anything else while the test runs.
    pub fn start(&mut self, ctx: &mut EventCtx, scratch: &mut [u8]) {
        self.scratch = scratch.as_mut_ptr();
        self.scratch_len = scratch.len();
        self.active = true;
        self.have_frame = false;
        self.capturing = false;
        self.scan_pending = false;
        self.decoded_at = None;
        self.decodes = 0;
        self.fps_since = None;
        self.fps_frames = 0;
        self.fps_tenths = 0;
        self.begin_open(ctx);
    }

    fn begin_open(&mut self, ctx: &mut EventCtx) {
        self.phase = Phase::Starting;
        self.set_lines_text(&["Starting the camera...", "", "", ""], Color::white());
        ctx.request_paint();
        ctx.request_anim_frame();
    }

    fn stop(&mut self, ctx: &mut EventCtx) {
        camera::close();
        self.active = false;
        self.capturing = false;
        self.frame = None;
        self.image = core::ptr::null_mut();
        self.scratch = core::ptr::null_mut();
        self.scratch_len = 0;
        ctx.request_paint();
    }

    fn open(&mut self) {
        if self.scratch.is_null() || self.scratch_len < camera::qr_arena_size() {
            self.phase = Phase::Failed;
            self.set_lines_text(&["Image buffer too small", "for the decoder", "", ""], RED);
            return;
        }

        if self.image.is_null() {
            // SAFETY: the image buffer is 16-byte aligned, and only the
            // decoder (and the camera, through its image) uses it while the
            // test runs
            let arena_ok = unsafe { camera::qr_init(self.scratch, self.scratch_len) };
            self.image = camera::qr_image();
            if !arena_ok || self.image.is_null() {
                self.phase = Phase::Failed;
                self.set_lines_text(&["QR decoder setup failed", "", "", ""], RED);
                return;
            }
        }

        if self.frame.is_none() {
            // SAFETY: the decoder's image, FRAME_SIZE bytes; painted only
            // between a finished capture and the next (step()); the bitmap
            // is dropped in stop()
            let bytes: &'static [u8] =
                unsafe { core::slice::from_raw_parts(self.image, camera::FRAME_SIZE) };
            self.frame = Bitmap::new(
                BitmapFormat::MONO8,
                None,
                Offset::new(VIEW, camera::FRAME_H as i16),
                None,
                bytes,
            )
            .ok();
        }

        let opened = camera::open(self.zoom);
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
        // SAFETY: the decoder's image (8-byte aligned), untouched until the
        // capture is done or failed: step() paints and decodes it only then
        self.capturing = unsafe { camera::capture(self.image) };
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
                    self.next_zoom(ctx);
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

    fn next_zoom(&mut self, ctx: &mut EventCtx) {
        self.zoom = (self.zoom + 1) % camera::ZOOM_LEVELS;
        // Stops a running capture; blocks while the sensor settles
        let ok = camera::set_zoom(self.zoom);
        self.capturing = false;
        // The image may be decoded already (thresholded): show black until
        // the next frame
        self.have_frame = false;
        self.scan_pending = false;
        self.status = camera::status();
        if !ok {
            self.phase = Phase::Failed;
            self.failure_lines();
        } else {
            self.update_lines();
        }
        self.fps_since = None;
        ctx.request_paint();
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
        if result.decoded != 0 {
            self.payload = payload;
            self.payload_shown = (result.payload_len.max(0) as usize).min(PAYLOAD_MAX);
            self.decoded_at = Some(Instant::now());
            self.decodes += 1;
        }
    }

    fn set_lines_text(&mut self, texts: &[&str; 4], color: Color) {
        for (line, text) in self.lines.iter_mut().zip(texts.iter()) {
            line.clear();
            push_trunc(line, text);
        }
        self.line_colors = [color; 4];
    }

    /// The status strip while running
    fn update_lines(&mut self) {
        let st = self.status;
        let [l0, l1, l2, l3] = &mut self.lines;
        for l in [&mut *l0, &mut *l1, &mut *l2, &mut *l3] {
            l.clear();
        }
        self.line_colors = [Color::white(), GREY, GREEN, GREEN];

        push_trunc(l0, sensor_name(st.sensor_id));
        push_trunc(l0, "  zoom ");
        push_u32(l0, st.zoom + 1);
        push_trunc(l0, "  ");
        push_tenths(l0, self.fps_tenths);
        push_trunc(l0, " fps");

        if st.state == camera::STATE_FAILED {
            push_trunc(l1, "FAIL ");
            push_u32(l1, st.last_bytes);
            push_trunc(l1, " B fl ");
            push_hex(l1, st.last_flags, 3);
            push_trunc(l1, " #");
            push_u32(l1, st.failures);
            self.line_colors[1] = RED;
        } else if st.frames == 0 {
            // No frame yet: what the sync pins did after the sensor started
            push_trunc(l1, "VS ");
            push_u32(l1, st.vsync_high_pm);
            push_trunc(l1, "/");
            push_u32(l1, st.vsync_edges);
            push_trunc(l1, " HR ");
            push_u32(l1, st.href_high_pm);
            push_trunc(l1, "/");
            push_u32(l1, st.href_edges);
            push_trunc(l1, " PC ");
            push_u32(l1, st.pclk_edges);
        } else {
            push_trunc(l1, "fr ");
            push_u32(l1, st.frames);
            push_trunc(l1, " fail ");
            push_u32(l1, st.failures);
            push_trunc(l1, " ");
            push_u32(l1, st.last_capture_ms);
            push_trunc(l1, "+");
            push_u32(l1, self.qr.identify_ms + self.qr.decode_ms);
            push_trunc(l1, " ms");
        }

        if self.decoded_at.is_some() {
            self.payload_lines();
        } else if self.qr.grids > 0 {
            push_trunc(l2, "QR seen, no decode (err ");
            push_u32(l2, self.qr.error as u32);
            push_trunc(l2, ")");
            push_trunc(l3, "decoded so far: ");
            push_u32(l3, self.decodes);
            self.line_colors[2] = PURPLE;
            self.line_colors[3] = GREY;
        } else {
            push_trunc(l2, "No QR code in view");
            push_trunc(l3, "decoded so far: ");
            push_u32(l3, self.decodes);
            self.line_colors[2] = GREY;
            self.line_colors[3] = GREY;
        }
    }

    /// The decoded payload over the last two lines, printable ASCII, cut to
    /// the screen width
    fn payload_lines(&mut self) {
        let max_w = WIDTH - 2 * MARGIN;
        let payload = &self.payload[..self.payload_shown];
        let [_, _, l2, l3] = &mut self.lines;
        let mut next = 0;
        for line in [l2, l3] {
            line.clear();
            while next < payload.len() {
                let b = payload[next];
                let c = if (0x20..0x7f).contains(&b) { b as char } else { '.' };
                if line.push(c).is_err() {
                    break;
                }
                if fonts::FONT_SUB.text_width(line.as_str()) > max_w {
                    // Does not fit: it starts the next line
                    line.pop();
                    break;
                }
                next += 1;
            }
        }
    }

    /// Why the camera did not start
    fn failure_lines(&mut self) {
        let st = self.status;
        let [l0, l1, l2, l3] = &mut self.lines;
        for l in [&mut *l0, &mut *l1, &mut *l2, &mut *l3] {
            l.clear();
        }
        self.line_colors = [RED, Color::white(), Color::white(), GREY];

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

    pub fn render<'s>(&'s self, target: &mut impl Renderer<'s>) {
        shape::Bar::new(Rect::new(Point::zero(), Point::new(WIDTH, HEIGHT)))
            .with_bg(Color::black())
            .render(target);

        // Repaints come right after a capture ends (step()), before the next
        // one starts; one forced while the DMA writes would show it half done
        let view = Rect::new(Point::zero(), Point::new(VIEW, VIEW));
        if let (true, Some(frame)) = (self.have_frame, &self.frame) {
            GrayImage {
                area: view,
                view: frame.view().with_fg(Color::white()),
            }
            .render(target);
        }

        if self.decoded_at.is_some() {
            shape::Bar::new(view)
                .with_fg(GREEN)
                .with_thickness(3)
                .render(target);
            let c = &self.qr.corners;
            for i in 0..4 {
                let p = Point::new(c[2 * i] as i16, c[2 * i + 1] as i16);
                shape::Bar::new(Rect::new(p - Offset::new(3, 3), p + Offset::new(4, 4)))
                    .with_bg(GREEN)
                    .render(target);
            }
        } else if self.status.state == camera::STATE_FAILED && self.phase == Phase::Running {
            shape::Bar::new(view)
                .with_fg(RED)
                .with_thickness(2)
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

        // Status strip; a failed start uses the whole screen below the corner
        let top = if self.phase == Phase::Running {
            VIEW
        } else {
            VIEW / 2
        };
        for (i, line) in self.lines.iter().enumerate() {
            shape::Text::new(
                Point::new(MARGIN, top + LINE_H * (i as i16 + 1)),
                line.as_str(),
                fonts::FONT_SUB,
            )
            .with_fg(self.line_colors[i])
            .render(target);
        }
    }
}

impl Drop for CameraTest {
    fn drop(&mut self) {
        // The home screen is going away (another layout took over): the DMA
        // must not keep writing a buffer that is no longer ours
        if self.active {
            camera::close();
        }
    }
}
