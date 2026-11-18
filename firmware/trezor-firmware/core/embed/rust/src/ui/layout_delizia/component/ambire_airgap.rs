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
//! Memory and the scan loop ([`Scanner`], ambire_camera.rs): the frames are in
//! the UI's image buffer (a whole screen, 150 KB) and a loan from the
//! MicroPython heap, as are the QR decoder's state, the QR text and the UR
//! assembler's work buffer (the message and its pool of mixed parts); with
//! no loan, a smaller frame and all of these share the image buffer.

use sys::time::Instant;

use super::super::fonts;
use super::ambire_camera::{
    push_code, push_plan, push_trunc, settings, zoom_name, CameraTest, GrayPreview, Scanned,
    Scanner, VIEW,
};
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
use crate::ui::shape::{self, Renderer};

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

const EXIT_SIZE: i16 = 48;
const MARGIN: i16 = 8;
/// The longest QR text taken: the app's parts are under 500 characters
const TEXT_MAX: usize = 2048;
/// The frame stays green this long after a part was taken
const ACCEPTED_SHOW_MS: u32 = 400;
/// The UR pool asked for beyond its minimum: room for more mixed parts
const POOL_WANT: usize = 8 * 1024;

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
    /// The camera's frames through the decoder; its extra buffer is the QR
    /// text buffer, then the UR work buffer
    scanner: Scanner,
    phase: Phase,
    camera_on: bool,
    zoom: u32,
    asm: Assembler,
    accepted_at: Option<Instant>,
    /// The decoder's last view of a code (for the diagnostic line)
    qr: camera::QrResult,
}

impl AirgapScan {
    pub fn new() -> Result<Self, Error> {
        let mut scanner = Scanner::new(
            TEXT_MAX + ambire_ur::MESSAGE_AREA + ambire_ur::MIN_POOL,
            TEXT_MAX + ambire_ur::MESSAGE_AREA + POOL_WANT,
        )?;
        // Built here (and again when the camera opens): the UR work buffer's
        // place depends on the plan
        if !scanner.build(settings().plan) {
            return Err(Error::RuntimeError(c"No memory for the QR scan"));
        }
        Ok(Self {
            scanner,
            phase: Phase::Starting,
            camera_on: false,
            zoom: settings().zoom,
            asm: Assembler::new(),
            accepted_at: None,
            // SAFETY: a plain C struct of integers; all zero is valid
            qr: unsafe { core::mem::zeroed() },
        })
    }

    /// The QR text buffer and the UR work buffer, borrowed apart from `self`
    /// (the assembler, a field, works on them)
    ///
    /// # Safety
    ///
    /// The slices must not outlive the screen, nor be taken twice at a time.
    unsafe fn text_and_work<'a>(&self) -> (&'a mut [u8], &'a mut [u8]) {
        let (extra, extra_len) = self.scanner.extra();
        // SAFETY: the scanner's extra region (in the loan or the image
        // buffer), at least TEXT_MAX + MESSAGE_AREA + MIN_POOL bytes, which
        // this screen holds; nothing else uses it, and the camera's DMA
        // writes only the frames
        let rest = unsafe { core::slice::from_raw_parts_mut(extra, extra_len) };
        rest.split_at_mut(TEXT_MAX)
    }

    fn work(&self) -> &[u8] {
        let (extra, extra_len) = self.scanner.extra();
        // SAFETY: as in text_and_work()
        unsafe { core::slice::from_raw_parts(extra.add(TEXT_MAX), extra_len - TEXT_MAX) }
    }

    fn mode(&self) -> camera::Mode {
        let s = settings();
        camera::Mode {
            zoom: self.zoom,
            side: self.scanner.side(),
            clock: s.clock,
            tune: s.tune,
        }
    }

    fn open(&mut self) -> Result<(), &'static str> {
        // The same plan as in new(): the work buffers stay where they are
        if !self.scanner.build(Some(self.scanner.plan())) {
            return Err("The QR decoder did not start.");
        }
        self.camera_on = true;
        if !camera::open(self.mode()) {
            return Err("The camera did not start. Run the camera test for details.");
        }
        Ok(())
    }

    fn close(&mut self) {
        if self.camera_on {
            camera::close();
            self.camera_on = false;
        }
        self.phase = Phase::Finished;
    }

    fn finish(&mut self, ctx: &mut EventCtx, msg: ScanMsg) -> Option<ScanMsg> {
        self.close();
        ctx.request_paint();
        Some(msg)
    }

    /// Takes the next frame, if one is in, through the decoder and hands a
    /// code's text to the assembler. The decoder reads the frames as a video
    /// (the code followed from frame to frame, frames of one part summed),
    /// so the same part can come many times: the assembler takes a repeat as
    /// such.
    fn scan(&mut self, ctx: &mut EventCtx) -> Option<ScanMsg> {
        // SAFETY: used within this call only
        let (text, work) = unsafe { self.text_and_work() };
        let qr = match self.scanner.step(text) {
            Scanned::Nothing => return None,
            Scanned::Failed => {
                // A failed capture: the next one retries
                ctx.request_paint();
                return None;
            }
            Scanned::Frame(qr) => qr,
        };
        ctx.request_paint();
        self.qr = qr;
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
            Phase::Running => match self.scan(ctx) {
                Some(msg) => self.finish(ctx, msg),
                None => None,
            },
            Phase::Finished => None,
        }
    }

    fn next_zoom(&mut self, ctx: &mut EventCtx) -> Option<ScanMsg> {
        self.zoom = (self.zoom + 1) % camera::ZOOM_LEVELS;
        // Stops a running capture; blocks while the sensor settles
        let ok = camera::set_mode(self.mode());
        // The frames in hand are the old zoom's: black until the next one,
        // and the decoder starts afresh
        self.scanner.restart();
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

        // The last frame that came in (the camera is not writing it:
        // Scanner)
        let view = Rect::new(Point::zero(), Point::new(VIEW, VIEW));
        if let Some(src) = self.scanner.image() {
            GrayPreview {
                area: view,
                src,
                side: self.scanner.side(),
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
        shape::Text::new(Point::new(MARGIN, VIEW + 60), hint, fonts::FONT_SUB)
            .with_fg(GREY)
            .render(target);

        // Diagnostics: the frame and zoom, and the decoder's last view of a
        // code (version, pixels per module)
        let mut diag: heapless::String<64> = heapless::String::new();
        push_plan(&mut diag, self.scanner.plan());
        push_trunc(&mut diag, "px ");
        push_trunc(&mut diag, zoom_name(self.zoom));
        if self.qr.grids > 0 {
            push_trunc(&mut diag, "  ");
            push_code(&mut diag, &self.qr);
        }
        shape::Text::new(Point::new(MARGIN, VIEW + 78), diag.as_str(), fonts::FONT_SUB)
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
    test: CameraTest,
    started: bool,
}

impl CameraTestScreen {
    pub fn new() -> Result<Self, Error> {
        Ok(Self {
            test: CameraTest::new()?,
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
                self.test.start(ctx);
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
