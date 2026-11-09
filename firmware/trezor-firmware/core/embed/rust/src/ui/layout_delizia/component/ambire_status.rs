//! Ambire boards: a one-line connection status in the home screen's top-right
//! corner, so the owner can tell whether the device is airgapped, and if not,
//! whether Bluetooth is off, unpaired, pairing, waiting or connected, without
//! swiping up to find out.
//!
//! Re-read once a second (and on Bluetooth events); repainted only when the
//! text changes.

use sys::time::Duration;

use super::super::fonts;
use crate::ui::component::{Event, EventCtx, Timer};
use crate::ui::constant::WIDTH;
use crate::ui::display::Color;
use crate::ui::geometry::{Alignment, Point};
use crate::ui::shape::{self, Renderer};

const REFRESH: Duration = Duration::from_millis(1000);
/// Right edge and baseline of the text
const RIGHT: i16 = WIDTH - 8;
const BASELINE: i16 = 20;

const GREEN: Color = Color::rgb(0xd7, 0xff, 0x00);
const PURPLE: Color = Color::rgb(0xaf, 0x50, 0xff);
const GREY: Color = Color::rgb(0xa0, 0xa0, 0xa0);

#[derive(Clone, Copy, PartialEq)]
enum Status {
    Airgapped,
    BluetoothOff,
    NotPaired,
    Pairing,
    Ready,
    Connected,
}

impl Status {
    fn now() -> Self {
        #[cfg(feature = "ambire_airgap")]
        if crate::trezorhal::airgap::engaged() {
            return Self::Airgapped;
        }
        #[cfg(feature = "ble")]
        {
            use crate::trezorhal::ble;
            if !ble::get_enabled() || !ble::is_started() {
                Self::BluetoothOff
            } else if ble::is_connected() {
                Self::Connected
            } else if ble::is_pairing() {
                Self::Pairing
            } else if ble::peer_count() == 0 {
                Self::NotPaired
            } else {
                Self::Ready
            }
        }
        #[cfg(not(feature = "ble"))]
        Self::BluetoothOff
    }

    fn text(self) -> &'static str {
        match self {
            Self::Airgapped => "AIR",
            Self::BluetoothOff => "BT off",
            Self::NotPaired => "BT not paired",
            Self::Pairing => "BT pairing",
            Self::Ready => "BT ready",
            Self::Connected => "BT connected",
        }
    }

    fn color(self) -> Color {
        match self {
            Self::Airgapped => PURPLE,
            Self::Connected => GREEN,
            _ => GREY,
        }
    }
}

pub struct StatusLine {
    status: Option<Status>,
    timer: Timer,
}

impl StatusLine {
    pub fn new() -> Self {
        Self {
            status: None,
            timer: Timer::new(),
        }
    }

    fn refresh(&mut self, ctx: &mut EventCtx) {
        let now = Some(Status::now());
        if now != self.status {
            self.status = now;
            ctx.request_paint();
        }
    }

    pub fn event(&mut self, ctx: &mut EventCtx, event: Event) {
        match event {
            Event::Attach(_) => {
                self.refresh(ctx);
                self.timer.start(ctx, REFRESH);
            }
            Event::Timer(_) if self.timer.expire(event) => {
                self.refresh(ctx);
                self.timer.start(ctx, REFRESH);
            }
            #[cfg(feature = "ble")]
            Event::BLE(_) => self.refresh(ctx),
            _ => {}
        }
    }

    pub fn render<'s>(&'s self, target: &mut impl Renderer<'s>) {
        if let Some(status) = self.status {
            shape::Text::new(Point::new(RIGHT, BASELINE), status.text(), fonts::FONT_SUB)
                .with_align(Alignment::End)
                .with_fg(status.color())
                .render(target);
        }
    }
}
