use super::super::fonts;
use super::theme;
use crate::ui::component::{Component, Event, EventCtx, Never};
use crate::ui::geometry::{Alignment, Alignment2D, Offset, Rect};
use crate::ui::shape::{self, Renderer};

#[cfg(not(feature = "ambire_branding"))]
const TEXT_BOTTOM_MARGIN: i16 = 54;
#[cfg(not(feature = "ambire_branding"))]
const ICON_TOP_MARGIN: i16 = 48;
// The Ambire flame is 150 px tall: flame and name centred as one group, the
// name's baseline 40 px under the flame (about 24 px of gap above the capitals)
#[cfg(feature = "ambire_branding")]
const ICON_TOP_MARGIN: i16 = 60;
#[cfg(feature = "ambire_branding")]
const TEXT_BOTTOM_MARGIN: i16 = 70;

use crate::trezorhal::model;

pub struct WelcomeScreen {
    area: Rect,
}

impl WelcomeScreen {
    pub fn new() -> Self {
        Self { area: Rect::zero() }
    }
}

impl Component for WelcomeScreen {
    type Msg = Never;

    fn place(&mut self, bounds: Rect) -> Rect {
        self.area = bounds;
        self.area
    }

    fn event(&mut self, _ctx: &mut EventCtx, _event: Event) -> Option<Self::Msg> {
        None
    }

    fn render<'s>(&'s self, target: &mut impl Renderer<'s>) {
        shape::ToifImage::new(
            self.area.top_center() + Offset::y(ICON_TOP_MARGIN),
            theme::ICON_LOGO.toif,
        )
        .with_align(Alignment2D::TOP_CENTER)
        .with_fg(theme::FG)
        .with_bg(theme::BG)
        .render(target);

        shape::Text::new(
            self.area.bottom_center() - Offset::y(TEXT_BOTTOM_MARGIN),
            model::FULL_NAME,
            fonts::FONT_DEMIBOLD,
        )
        .with_align(Alignment::Center)
        .with_fg(theme::FG)
        .render(target);
    }
}

#[cfg(feature = "ui_debug")]
impl crate::trace::Trace for WelcomeScreen {
    fn trace(&self, t: &mut dyn crate::trace::Tracer) {
        t.component("WelcomeScreen");
        #[cfg(not(feature = "bootloader"))]
        t.string("model", model::FULL_NAME.into());
    }
}
