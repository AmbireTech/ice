//! Ambire boards, [ambire_camera]: the screens of the airgapped QR flow.
//!
//! - [`AirgapScan`] runs the camera and the QR decoder and assembles a UR
//!   (one code, or an animated sequence) from what it reads. Everything it
//!   reads is hostile: the UR layer (ambire_ur.rs) checks it, and any fault
//!   ends the scan with a message for the user. It returns the UR's type and
//!   its CBOR to MicroPython, which parses the CBOR.
//! - [`AirgapQr`] shows a one-part UR as a QR code (the device's answers).
//! - [`CameraTestScreen`] is the camera bring-up test as a screen of its own.
//!
//! Memory: no heap. The scan takes the UI's image buffer (a whole screen,
//! 150 KB): the QR decoder and its frame first, then the QR text, then the UR
//! assembler's work buffer (the message and its pool of mixed parts).

use sys::time::Instant;

use super::super::fonts;
use super::ambire_camera::{CameraTest, GrayImage};
use super::ambire_ur::{self, Assembler, Status};
use crate::micropython::{Error, Obj};
use crate::strutil::TString;
use crate::trezorhal::camera;
use crate::ui::component::qr_code::Qr;
use crate::ui::component::{Component, Event, EventCtx};
use crate::ui::constant::{HEIGHT, WIDTH};
use crate::ui::display::Color;
use crate::ui::event::TouchEvent;
use crate::ui::geometry::{Alignment, Offset, Point, Rect};
use crate::ui::layout::obj::ComponentMsgObj;
use crate::ui::layout::result::{CANCELLED, CONFIRMED};
use crate::ui::shape::{self, Bitmap, BitmapFormat, ImageBuffer, Renderer, Rgb565Canvas};

/// trezor-crypto's hashes for the UR layer
pub struct FirmwareHasher;

impl ambire_ur::Hasher for FirmwareHasher {
    fn sha256(data: &[u8]) -> [u8; 32] {
        crypto::sha256::Sha256::digest(data)
    }

    fn crc32(data: &[u8]) -> u32 {
        u32::from_be_bytes(crypto::crc32::digest(data))
    }
}

const GREEN: Color = Color::rgb(0xd7, 0xff, 0x00);
const GREY: Color = Color::rgb(0xa0, 0xa0, 0xa0);
const DARK_GREY: Color = Color::rgb(0x30, 0x30, 0x30);

/// The camera frame is drawn 1:1 at the top of the screen
const VIEW: i16 = camera::FRAME_W as i16;
const EXIT_SIZE: i16 = 48;
const MARGIN: i16 = 8;
/// The longest QR text taken: the app's parts are under 500 characters
const TEXT_MAX: usize = 2048;
/// The frame stays green this long after a part was taken
const ACCEPTED_SHOW_MS: u32 = 400;
/// The middle zoom first (see io/camera/stm32u5/camera.c)
const START_ZOOM: u32 = 1;

fn image_buffer() -> Result<ImageBuffer<Rgb565Canvas<'static>>, Error> {
    // The size is nominal: the screens take the whole buffer's bytes
    Ok(ImageBuffer::new(Offset::new(1, 1))?)
}

/// Draws the exit corner (an "x" in a box), as the camera test does
fn render_exit<'s>(target: &mut impl Renderer<'s>) {
    shape::Bar::new(Rect::new(Point::new(6, 6), Point::new(EXIT_SIZE - 6, EXIT_SIZE - 6)))
        .with_bg(Color::black())
        .with_fg(GREEN)
        .with_thickness(2)
        .render(target);
    shape::Text::new(Point::new(EXIT_SIZE / 2, EXIT_SIZE / 2 + 7), "x", fonts::FONT_DEMIBOLD)
        .with_align(Alignment::Center)
        .with_fg(GREEN)
        .render(target);
}

fn in_exit(p: Point) -> bool {
    p.x < EXIT_SIZE && p.y < EXIT_SIZE
}

// --- Scanning ------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq)]
enum Phase {
    /// Painted "starting"; the next frame tick opens the camera
    Starting,
    Running,
    /// Returned its result; the camera is off
    Finished,
}

pub enum ScanMsg {
    /// The whole UR is in the work buffer
    Complete,
    Failed(&'static str),
    Cancelled,
}

pub struct AirgapScan {
    /// Holds the image buffer's lock while the scan uses its bytes
    _buffer: ImageBuffer<Rgb565Canvas<'static>>,
    /// The image buffer: the decoder's arena, then the QR text, then the work
    /// buffer
    arena: *mut u8,
    arena_len: usize,
    qr_len: usize,
    /// The decoder's image, which the camera captures into
    image: *mut u8,
    frame: Option<Bitmap<'static>>,
    phase: Phase,
    camera_on: bool,
    capturing: bool,
    have_frame: bool,
    /// The frame in the image has been painted, not decoded yet
    scan_pending: bool,
    zoom: u32,
    asm: Assembler,
    accepted_at: Option<Instant>,
}

impl AirgapScan {
    pub fn new() -> Result<Self, Error> {
        let mut buffer = image_buffer()?;
        let bytes = buffer.raw_bytes();
        let (arena, arena_len) = (bytes.as_mut_ptr(), bytes.len());
        // 16-byte steps keep the decoder's 8-byte alignment
        let qr_len = camera::qr_arena_size().next_multiple_of(16);
        if arena_len < qr_len + TEXT_MAX + ambire_ur::MESSAGE_AREA + ambire_ur::MIN_POOL {
            return Err(Error::RuntimeError(c"Image buffer too small for the QR scan"));
        }
        Ok(Self {
            _buffer: buffer,
            arena,
            arena_len,
            qr_len,
            image: core::ptr::null_mut(),
            frame: None,
            phase: Phase::Starting,
            camera_on: false,
            capturing: false,
            have_frame: false,
            scan_pending: false,
            zoom: START_ZOOM,
            asm: Assembler::new(),
            accepted_at: None,
        })
    }

    /// The QR text buffer and the UR work buffer, borrowed apart from `self`
    /// (the assembler, a field, works on them)
    ///
    /// # Safety
    ///
    /// The slices must not outlive the screen, nor be taken twice at a time.
    unsafe fn text_and_work<'a>(&self) -> (&'a mut [u8], &'a mut [u8]) {
        // SAFETY: both lie in the image buffer after the decoder's arena,
        // which this screen holds locked; nothing else uses them, and the
        // camera's DMA writes only the decoder's image
        let rest = unsafe {
            core::slice::from_raw_parts_mut(self.arena.add(self.qr_len), self.arena_len - self.qr_len)
        };
        rest.split_at_mut(TEXT_MAX)
    }

    fn work(&self) -> &[u8] {
        let at = self.qr_len + TEXT_MAX;
        // SAFETY: as in text_and_work()
        unsafe { core::slice::from_raw_parts(self.arena.add(at), self.arena_len - at) }
    }

    fn open(&mut self) -> Result<(), &'static str> {
        // SAFETY: the arena is the image buffer's start (16-byte aligned),
        // reserved for the decoder while this screen lives
        let ok = unsafe { camera::qr_init(self.arena, self.qr_len) };
        self.image = camera::qr_image();
        if !ok || self.image.is_null() {
            return Err("The QR decoder did not start.");
        }
        // SAFETY: the decoder's image, FRAME_SIZE bytes; painted only between
        // a finished capture and the next (step()); dropped with the screen
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
        self.camera_on = true;
        if !camera::open(self.zoom) {
            return Err("The camera did not start. Run the camera test for details.");
        }
        Ok(())
    }

    fn close(&mut self) {
        if self.camera_on {
            camera::close();
            self.camera_on = false;
        }
        self.capturing = false;
        self.phase = Phase::Finished;
    }

    fn finish(&mut self, ctx: &mut EventCtx, msg: ScanMsg) -> Option<ScanMsg> {
        self.close();
        ctx.request_paint();
        Some(msg)
    }

    fn start_capture(&mut self) {
        // SAFETY: the decoder's image (8-byte aligned), untouched until the
        // capture is done or failed: step() paints and decodes it only then
        self.capturing = unsafe { camera::capture(self.image) };
    }

    /// Decodes the painted frame and hands a code's text to the assembler
    fn scan(&mut self) -> Option<ScanMsg> {
        // SAFETY: used within this call only
        let (text, work) = unsafe { self.text_and_work() };
        // SAFETY: called between captures only (step())
        let qr = unsafe { camera::qr_scan(text) };
        if qr.decoded == 0 {
            return None;
        }
        let len = qr.payload_len.max(0) as usize;
        if len > TEXT_MAX {
            return Some(ScanMsg::Failed("The code holds too much data for a request."));
        }
        match self.asm.receive::<FirmwareHasher>(work, &mut text[..len]) {
            Ok(Status::Pending) => {
                self.accepted_at = Some(Instant::now());
                None
            }
            Ok(Status::Complete) => Some(ScanMsg::Complete),
            Err(e) => Some(ScanMsg::Failed(e.message())),
        }
    }

    fn step(&mut self, ctx: &mut EventCtx) -> Option<ScanMsg> {
        match self.phase {
            Phase::Starting => {
                self.phase = Phase::Running;
                ctx.request_paint();
                if let Err(msg) = self.open() {
                    return self.finish(ctx, ScanMsg::Failed(msg));
                }
                None
            }
            Phase::Running => {
                if !self.capturing {
                    // The last frame has been painted: decode it (in place),
                    // then capture the next into the same image
                    if self.scan_pending {
                        self.scan_pending = false;
                        if let Some(msg) = self.scan() {
                            return self.finish(ctx, msg);
                        }
                    }
                    self.start_capture();
                    return None;
                }
                match camera::status().state {
                    camera::STATE_BUSY => return None,
                    camera::STATE_DONE => {
                        self.have_frame = true;
                        self.scan_pending = true;
                    }
                    // A failed or lost capture: the next one retries
                    _ => {}
                }
                self.capturing = false;
                ctx.request_paint();
                None
            }
            Phase::Finished => None,
        }
    }

    fn next_zoom(&mut self, ctx: &mut EventCtx) -> Option<ScanMsg> {
        self.zoom = (self.zoom + 1) % camera::ZOOM_LEVELS;
        // Stops a running capture; blocks while the sensor settles
        let ok = camera::set_zoom(self.zoom);
        self.capturing = false;
        // The image may be decoded already (thresholded): black until the
        // next frame
        self.have_frame = false;
        self.scan_pending = false;
        ctx.request_paint();
        if !ok {
            return self.finish(ctx, ScanMsg::Failed("The camera did not take the zoom change."));
        }
        None
    }

    /// What the strip under the picture says, and the fragments in hand of all
    fn status(&self) -> (&'static str, u32, u32) {
        let (have, all) = self.asm.progress();
        let text = match self.phase {
            Phase::Starting => "Starting the camera...",
            _ if all > 1 => "Receiving the request...",
            _ => "Scan the request in the app",
        };
        (text, have, all)
    }
}

impl Component for AirgapScan {
    type Msg = ScanMsg;

    fn place(&mut self, bounds: Rect) -> Rect {
        bounds
    }

    fn event(&mut self, ctx: &mut EventCtx, event: Event) -> Option<Self::Msg> {
        match event {
            Event::Attach(_) => {
                ctx.request_paint();
                ctx.request_anim_frame();
            }
            Event::Touch(TouchEvent::TouchStart(p)) if self.phase != Phase::Finished => {
                if in_exit(p) {
                    return self.finish(ctx, ScanMsg::Cancelled);
                }
                if self.phase == Phase::Running && p.y < VIEW {
                    return self.next_zoom(ctx);
                }
            }
            _ if EventCtx::is_anim_frame(event) && self.phase != Phase::Finished => {
                let msg = self.step(ctx);
                if msg.is_none() {
                    ctx.request_anim_frame();
                }
                if let Some(t) = self.accepted_at {
                    if Instant::now().saturating_duration_since(t).to_millis() > ACCEPTED_SHOW_MS {
                        self.accepted_at = None;
                        ctx.request_paint();
                    }
                }
                return msg;
            }
            _ => {}
        }
        None
    }

    fn render<'s>(&'s self, target: &mut impl Renderer<'s>) {
        shape::Bar::new(Rect::new(Point::zero(), Point::new(WIDTH, HEIGHT)))
            .with_bg(Color::black())
            .render(target);

        // Repaints come right after a capture ends (step()), before the next
        // one starts
        let view = Rect::new(Point::zero(), Point::new(VIEW, VIEW));
        if let (true, Some(frame)) = (self.have_frame, &self.frame) {
            GrayImage {
                area: view,
                view: frame.view().with_fg(Color::white()),
            }
            .render(target);
        }
        if self.accepted_at.is_some() {
            shape::Bar::new(view).with_fg(GREEN).with_thickness(3).render(target);
        }
        render_exit(target);

        let (line, have, all) = self.status();
        shape::Text::new(Point::new(MARGIN, VIEW + 24), line, fonts::FONT_SUB)
            .with_fg(Color::white())
            .render(target);

        // Progress: fragments in hand of all
        let bar = Rect::new(Point::new(MARGIN, VIEW + 36), Point::new(WIDTH - MARGIN, VIEW + 44));
        shape::Bar::new(bar).with_bg(DARK_GREY).with_radius(3).render(target);
        if all > 0 {
            let w = (i32::from(bar.width()) * have as i32 / all as i32) as i16;
            if w > 0 {
                shape::Bar::new(Rect::from_top_left_and_size(bar.top_left(), Offset::new(w, bar.height())))
                    .with_bg(GREEN)
                    .with_radius(3)
                    .render(target);
            }
        }

        let hint = match self.zoom {
            0 => "Tap the picture to zoom (wide)",
            1 => "Tap the picture to zoom (middle)",
            _ => "Tap the picture to zoom (close)",
        };
        shape::Text::new(Point::new(MARGIN, VIEW + 68), hint, fonts::FONT_SUB)
            .with_fg(GREY)
            .render(target);
    }
}

impl ComponentMsgObj for AirgapScan {
    fn msg_try_into_obj(&self, msg: Self::Msg) -> Result<Obj, Error> {
        match msg {
            ScanMsg::Complete => {
                let ty = core::str::from_utf8(self.asm.ur_type())
                    .map_err(|_| Error::RuntimeError(c"UR type"))?;
                let cbor = self
                    .asm
                    .message(self.work())
                    .ok_or(Error::RuntimeError(c"UR incomplete"))?;
                (Obj::try_from(ty)?, Obj::try_from(cbor)?).try_into()
            }
            ScanMsg::Failed(why) => why.try_into(),
            ScanMsg::Cancelled => Ok(CANCELLED.as_obj()),
        }
    }
}

impl Drop for AirgapScan {
    fn drop(&mut self) {
        // Another layout took over mid-scan: the DMA must not keep writing a
        // buffer that is no longer ours
        if self.camera_on {
            camera::close();
        }
    }
}

#[cfg(feature = "ui_debug")]
impl crate::trace::Trace for AirgapScan {
    fn trace(&self, t: &mut dyn crate::trace::Tracer) {
        t.component("AirgapScan");
        let (have, all) = self.asm.progress();
        t.int("fragments", i64::from(have));
        t.int("of", i64::from(all));
    }
}

// --- Showing a UR --------------------------------------------------------------

/// Longest text the QR component holds (version 10, alphanumeric)
const QR_TEXT_MAX: usize = 311;
const QR_TOP: i16 = 32;
const DONE_TOP: i16 = HEIGHT - 44;

pub struct AirgapQr {
    title: TString<'static>,
    qr: Qr,
}

impl AirgapQr {
    /// Encodes `cbor` as a one-part UR of `ur_type`
    pub fn new(title: TString<'static>, ur_type: &str, cbor: &[u8]) -> Result<Self, Error> {
        let mut text = [0u8; QR_TEXT_MAX + 1];
        let len = ambire_ur::encode_single::<FirmwareHasher>(ur_type, cbor, &mut text)
            .map_err(|_| Error::ValueError(c"Too much data for one QR code"))?;
        if len > QR_TEXT_MAX {
            return Err(Error::ValueError(c"Too much data for one QR code"));
        }
        // ASCII by construction
        let text = core::str::from_utf8(&text[..len]).map_err(|_| Error::TypeError)?;
        let qr = Qr::new(text, false)?.with_border(6);
        Ok(Self { title, qr })
    }
}

impl Component for AirgapQr {
    type Msg = ();

    fn place(&mut self, bounds: Rect) -> Rect {
        self.qr
            .place(Rect::new(Point::new(0, QR_TOP), Point::new(WIDTH, QR_TOP + WIDTH)));
        bounds
    }

    fn event(&mut self, _ctx: &mut EventCtx, event: Event) -> Option<Self::Msg> {
        match event {
            Event::Touch(TouchEvent::TouchEnd(p)) if p.y >= DONE_TOP => Some(()),
            _ => None,
        }
    }

    fn render<'s>(&'s self, target: &mut impl Renderer<'s>) {
        shape::Bar::new(Rect::new(Point::zero(), Point::new(WIDTH, HEIGHT)))
            .with_bg(Color::black())
            .render(target);
        self.title.map(|t| {
            shape::Text::new(Point::new(WIDTH / 2, 24), t, fonts::FONT_DEMIBOLD)
                .with_align(Alignment::Center)
                .with_fg(Color::white())
                .render(target);
        });
        self.qr.render(target);
        let done = Rect::new(Point::new(MARGIN, DONE_TOP + 4), Point::new(WIDTH - MARGIN, HEIGHT - 4));
        shape::Bar::new(done).with_bg(DARK_GREY).with_radius(8).render(target);
        shape::Text::new(done.center() + Offset::y(7), "Done", fonts::FONT_DEMIBOLD)
            .with_align(Alignment::Center)
            .with_fg(GREEN)
            .render(target);
    }
}

impl ComponentMsgObj for AirgapQr {
    fn msg_try_into_obj(&self, _msg: Self::Msg) -> Result<Obj, Error> {
        Ok(CONFIRMED.as_obj())
    }
}

#[cfg(feature = "ui_debug")]
impl crate::trace::Trace for AirgapQr {
    fn trace(&self, t: &mut dyn crate::trace::Tracer) {
        t.component("AirgapQr");
        t.string("title", self.title);
    }
}

// --- The camera test, on its own ---------------------------------------------------

pub struct CameraTestScreen {
    buffer: ImageBuffer<Rgb565Canvas<'static>>,
    test: CameraTest,
    started: bool,
}

impl CameraTestScreen {
    pub fn new() -> Result<Self, Error> {
        Ok(Self {
            buffer: image_buffer()?,
            test: CameraTest::new(),
            started: false,
        })
    }
}

impl Component for CameraTestScreen {
    type Msg = ();

    fn place(&mut self, bounds: Rect) -> Rect {
        bounds
    }

    fn event(&mut self, ctx: &mut EventCtx, event: Event) -> Option<Self::Msg> {
        if !self.started {
            if let Event::Attach(_) = event {
                // The test borrows the buffer this screen holds locked
                self.test.start(ctx, self.buffer.raw_bytes());
                self.started = true;
            }
            return None;
        }
        self.test.event(ctx, event);
        (!self.test.is_active()).then_some(())
    }

    fn render<'s>(&'s self, target: &mut impl Renderer<'s>) {
        self.test.render(target);
    }
}

impl ComponentMsgObj for CameraTestScreen {
    fn msg_try_into_obj(&self, _msg: Self::Msg) -> Result<Obj, Error> {
        Ok(CANCELLED.as_obj())
    }
}

#[cfg(feature = "ui_debug")]
impl crate::trace::Trace for CameraTestScreen {
    fn trace(&self, t: &mut dyn crate::trace::Tracer) {
        t.component("CameraTestScreen");
    }
}

// --- MicroPython --------------------------------------------------------------------

pub mod micropython {
    use super::{AirgapQr, AirgapScan, CameraTestScreen};
    use crate::micropython::buffer::get_buffer;
    use crate::micropython::map::Map;
    use crate::micropython::qstr::Qstr;
    use crate::micropython::{util, Error, Obj};
    use crate::strutil::TString;
    use crate::ui::layout::obj::{LayoutObj, RootComponent};

    pub fn scan_ur(_args: &[Obj], _kwargs: &Map) -> Result<Obj, Error> {
        let layout = RootComponent::new(AirgapScan::new()?);
        Ok(LayoutObj::new_root(layout)?.into())
    }

    pub fn show_ur(_args: &[Obj], kwargs: &Map) -> Result<Obj, Error> {
        let title: TString = kwargs.get(Qstr::MP_QSTR_title)?.try_into()?;
        let ur_type: TString = kwargs.get(Qstr::MP_QSTR_ur_type)?.try_into()?;
        let cbor_obj = kwargs.get(Qstr::MP_QSTR_cbor)?;
        // SAFETY: the bytes are read here, before anything can change them
        let cbor = unsafe { get_buffer(cbor_obj)? };
        let screen = ur_type.map(|ty| AirgapQr::new(title, ty, cbor))?;
        Ok(LayoutObj::new_root(RootComponent::new(screen))?.into())
    }

    pub fn camera_test(_args: &[Obj], _kwargs: &Map) -> Result<Obj, Error> {
        let layout = RootComponent::new(CameraTestScreen::new()?);
        Ok(LayoutObj::new_root(layout)?.into())
    }

    pub fn call(
        n_args: usize,
        args: *const Obj,
        kwargs: *mut Map,
        f: fn(&[Obj], &Map) -> Result<Obj, Error>,
    ) -> Obj {
        // SAFETY: as every layout constructor of firmware_micropython.rs
        unsafe { util::try_with_args_and_kwargs(n_args, args, kwargs, f) }
    }
}
