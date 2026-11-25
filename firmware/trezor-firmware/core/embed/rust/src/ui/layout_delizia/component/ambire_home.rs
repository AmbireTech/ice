//! Ambire boards, [ambire_joystick]: the home screen as a 2003 colour phone
//! ("Colour grid", docs: the home screen designs of 2026-11-23).
//!
//! A 3x2 grid of pixel tiles (Wallet, Bluetooth, Air-gap, Games, Settings and
//! the side switch) on a dithered wallpaper, with a status bar, the selected
//! tile's name and a soft key. Everything is drawn on a 120x160 grid at 2x,
//! with a dot-matrix gap over it. The airgap switch decides which links are
//! live: off AIR the wallet (USB) and Bluetooth, on AIR the QR codes.
//!
//! The joystick moves the cursor and its centre press selects (a long press
//! still switches the device off); a tap selects too. Games (the 3D maze and
//! snake) and Settings run inside this component: only Bluetooth pairing, the
//! airgap menu and locking go back to the firmware.

use sys::time::{Duration, Instant};

#[cfg(feature = "ambire_game")]
use super::ambire_game::Game;
use crate::strutil::TString;
use crate::trezorhal::joystick;
use crate::ui::component::{Event, EventCtx, Timer};
use crate::ui::display::Color;
use crate::ui::event::TouchEvent;
use crate::ui::geometry::{Offset, Point, Rect};
use crate::ui::notification::{Notification, NotificationLevel};
use crate::ui::shape::{self, BasicCanvas, Renderer};

/// What the home screen hands back to the firmware
pub enum HomeAction {
    Bluetooth,
    Airgap,
    Lock,
    /// On the lock screen: a tile that needs the PIN was selected
    Unlock,
}

// ---------------------------------------------------------------- joystick

/// Which way each of the netlist's contacts points on the device (the ALPS
/// drawing's A/B/C/D, docs/design.md bring-up item 18), read on a v4 board
/// (2026-11-25): left A, down C, right D, so up B.
const UP: u32 = joystick::B;
const RIGHT: u32 = joystick::D;
const DOWN: u32 = joystick::C;
const LEFT: u32 = joystick::A;
const DIRS: u32 = UP | RIGHT | DOWN | LEFT;

const POLL_MS: u32 = 25;
/// A held direction repeats in lists after this long, then this often
const REPEAT_DELAY_MS: u32 = 380;
const REPEAT_MS: u32 = 140;
/// A centre press longer than this selects nothing: it is on its way to the
/// 3 s power-off
const SELECT_MAX_MS: u32 = 900;
const OVERLAY_MS: u32 = 2200;

#[derive(Clone, Copy, PartialEq)]
enum Key {
    Up,
    Down,
    Left,
    Right,
    Select,
}

// ---------------------------------------------------------------- drawing

/// Logical pixels: 120 x 160, each 2 x 2 on the panel
const S: i16 = 2;
const LW: i16 = 120;
const LH: i16 = 160;

const fn rgb(c: u32) -> Color {
    Color::rgb((c >> 16) as u8, (c >> 8) as u8, c as u8)
}

const WHITE: Color = rgb(0xffffff);
const VOLT: Color = rgb(0xd7ff00);
const ORANGE_HI: Color = rgb(0xffb52e);
const ORANGE: Color = rgb(0xff7a12);
const RED: Color = rgb(0xff3b3b);
const OFF_RED: Color = rgb(0xff9a8a);
const SUB_BLUE: Color = rgb(0xb9c7ff);
const SHADOW: Color = rgb(0x0a0f30);
const BAR_LIGHT: Color = rgb(0x3d72f2);
const BAR_DARK: Color = rgb(0x173096);

fn logical(x: i16, y: i16, w: i16, h: i16) -> Rect {
    Rect::from_top_left_and_size(Point::new(x * S, y * S), Offset::new(w * S, h * S))
}

fn px<'s>(t: &mut impl Renderer<'s>, x: i16, y: i16, w: i16, h: i16, c: Color) {
    shape::Bar::new(logical(x, y, w, h)).with_bg(c).render(t);
}

fn px_alpha<'s>(t: &mut impl Renderer<'s>, x: i16, y: i16, w: i16, h: i16, c: Color, a: u8) {
    shape::Bar::new(logical(x, y, w, h))
        .with_bg(c)
        .with_alpha(a)
        .render(t);
}

/// A box with its corner pixels cut, in three bars that don't overlap (so a
/// translucent one is even)
fn round_rect<'s>(t: &mut impl Renderer<'s>, x: i16, y: i16, w: i16, h: i16, c: Color, a: u8) {
    px_alpha(t, x + 1, y, w - 2, 1, c, a);
    px_alpha(t, x, y + 1, w, h - 2, c, a);
    px_alpha(t, x + 1, y + h - 1, w - 2, 1, c, a);
}

/// The dot-matrix gap: the panel's pixel between two logical ones, darkened
fn dot_matrix<'s>(t: &mut impl Renderer<'s>) {
    const GAP_ALPHA: u8 = 97; // 0.38
    for x in 0..LW {
        shape::Bar::new(Rect::from_top_left_and_size(
            Point::new(x * S + 1, 0),
            Offset::new(1, LH * S),
        ))
        .with_bg(Color::black())
        .with_alpha(GAP_ALPHA)
        .render(t);
    }
    for y in 0..LH {
        shape::Bar::new(Rect::from_top_left_and_size(
            Point::new(0, y * S + 1),
            Offset::new(LW * S, 1),
        ))
        .with_bg(Color::black())
        .with_alpha(GAP_ALPHA)
        .render(t);
    }
}

const BAYER: [[u8; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];

/// Paints the wallpaper into the home screen's image buffer once: a dithered
/// night-blue gradient with a soft diagonal aurora, the blue status and soft
/// key bars.
pub fn paint_wallpaper(canvas: &mut impl BasicCanvas) {
    const STOPS: [(f32, f32, f32); 4] = [
        (13.0, 46.0, 74.0),
        (22.0, 34.0, 92.0),
        (44.0, 20.0, 88.0),
        (18.0, 10.0, 40.0),
    ];
    let mut dot = |x: i16, y: i16, c: Color| {
        canvas.fill_rect(logical(x, y, 1, 1), c, 255);
    };
    for y in 0..LH {
        for x in 0..LW {
            let b = BAYER[(y & 3) as usize][(x & 3) as usize];
            let c = if y < 11 || y >= 147 {
                // the bars: two blues, dithered across their height
                let f = if y < 11 { y * 16 / 11 } else { (y - 147) * 16 / 13 };
                let lighter = (f as u8 > b) == (y < 11);
                if lighter { BAR_DARK } else { BAR_LIGHT }
            } else {
                let f = y as f32 / LH as f32 * 3.0;
                let i = (f as usize).min(2);
                let k = f - i as f32;
                let thr = (b as f32 + 0.5) / 16.0;
                let d = (x as f32 - 20.0) * 0.6 - (y as f32 - 70.0);
                let d = if d < 0.0 { -d } else { d };
                let glow = (1.0 - d / 38.0).max(0.0) * 0.35;
                let s = STOPS[if k + glow * 0.3 > thr { i + 1 } else { i }];
                let lift = if glow > thr * 0.7 { 26.0 } else { 0.0 };
                Color::rgb(
                    (s.0 + lift * 0.2) as u8,
                    (s.1 + lift) as u8,
                    (s.2 + lift * 0.8) as u8,
                )
            };
            dot(x, y, c);
        }
    }
    let rule = rgb(0x8fb0ff);
    canvas.fill_rect(logical(0, 11, LW, 1), rule, 255);
    canvas.fill_rect(logical(0, 146, LW, 1), rule, 255);
}

// ---------------------------------------------------------------- pixel font

/// A 5-pixel-high caps font in the spirit of Silkscreen: (char, width, rows),
/// each row's bits right-aligned to the width, the leftmost column highest
const GLYPHS: &[(u8, u8, [u8; 5])] = &[
    (b' ', 2, [0, 0, 0, 0, 0]),
    (b'A', 4, [0b0110, 0b1001, 0b1111, 0b1001, 0b1001]),
    (b'B', 4, [0b1110, 0b1001, 0b1110, 0b1001, 0b1110]),
    (b'C', 4, [0b0111, 0b1000, 0b1000, 0b1000, 0b0111]),
    (b'D', 4, [0b1110, 0b1001, 0b1001, 0b1001, 0b1110]),
    (b'E', 4, [0b1111, 0b1000, 0b1110, 0b1000, 0b1111]),
    (b'F', 4, [0b1111, 0b1000, 0b1110, 0b1000, 0b1000]),
    (b'G', 4, [0b0111, 0b1000, 0b1011, 0b1001, 0b0111]),
    (b'H', 4, [0b1001, 0b1001, 0b1111, 0b1001, 0b1001]),
    (b'I', 3, [0b111, 0b010, 0b010, 0b010, 0b111]),
    (b'J', 4, [0b0001, 0b0001, 0b0001, 0b1001, 0b0110]),
    (b'K', 4, [0b1001, 0b1010, 0b1100, 0b1010, 0b1001]),
    (b'L', 4, [0b1000, 0b1000, 0b1000, 0b1000, 0b1111]),
    (b'M', 5, [0b10001, 0b11011, 0b10101, 0b10001, 0b10001]),
    (b'N', 4, [0b1001, 0b1101, 0b1011, 0b1001, 0b1001]),
    (b'O', 4, [0b0110, 0b1001, 0b1001, 0b1001, 0b0110]),
    (b'P', 4, [0b1110, 0b1001, 0b1110, 0b1000, 0b1000]),
    (b'Q', 4, [0b0110, 0b1001, 0b1001, 0b1011, 0b0111]),
    (b'R', 4, [0b1110, 0b1001, 0b1110, 0b1010, 0b1001]),
    (b'S', 4, [0b0111, 0b1000, 0b0110, 0b0001, 0b1110]),
    (b'T', 5, [0b11111, 0b00100, 0b00100, 0b00100, 0b00100]),
    (b'U', 4, [0b1001, 0b1001, 0b1001, 0b1001, 0b0110]),
    (b'V', 5, [0b10001, 0b10001, 0b01010, 0b01010, 0b00100]),
    (b'W', 5, [0b10001, 0b10001, 0b10101, 0b11011, 0b10001]),
    (b'X', 5, [0b10001, 0b01010, 0b00100, 0b01010, 0b10001]),
    (b'Y', 5, [0b10001, 0b01010, 0b00100, 0b00100, 0b00100]),
    (b'Z', 4, [0b1111, 0b0010, 0b0100, 0b1000, 0b1111]),
    (b'0', 4, [0b0110, 0b1011, 0b1101, 0b1001, 0b0110]),
    (b'1', 3, [0b010, 0b110, 0b010, 0b010, 0b111]),
    (b'2', 4, [0b1110, 0b0001, 0b0110, 0b1000, 0b1111]),
    (b'3', 4, [0b1110, 0b0001, 0b0110, 0b0001, 0b1110]),
    (b'4', 4, [0b1001, 0b1001, 0b1111, 0b0001, 0b0001]),
    (b'5', 4, [0b1111, 0b1000, 0b1110, 0b0001, 0b1110]),
    (b'6', 4, [0b0110, 0b1000, 0b1110, 0b1001, 0b0110]),
    (b'7', 4, [0b1111, 0b0001, 0b0010, 0b0100, 0b0100]),
    (b'8', 4, [0b0110, 0b1001, 0b0110, 0b1001, 0b0110]),
    (b'9', 4, [0b0110, 0b1001, 0b0111, 0b0001, 0b0110]),
    (b':', 1, [0, 1, 0, 1, 0]),
    (b';', 2, [0b00, 0b01, 0b00, 0b01, 0b10]),
    (b'.', 1, [0, 0, 0, 0, 1]),
    (b',', 2, [0b00, 0b00, 0b00, 0b01, 0b10]),
    (b'-', 3, [0, 0, 0b111, 0, 0]),
    (b'+', 3, [0, 0b010, 0b111, 0b010, 0]),
    (b'/', 3, [0b001, 0b001, 0b010, 0b100, 0b100]),
    (b'!', 1, [1, 1, 1, 0, 1]),
    (b'?', 4, [0b1110, 0b0001, 0b0110, 0b0000, 0b0100]),
    (b'(', 2, [0b01, 0b10, 0b10, 0b10, 0b01]),
    (b')', 2, [0b10, 0b01, 0b01, 0b01, 0b10]),
];

fn glyph(ch: u8) -> (u8, [u8; 5]) {
    let ch = ch.to_ascii_uppercase();
    for &(c, w, rows) in GLYPHS {
        if c == ch {
            return (w, rows);
        }
    }
    // '?' for anything else
    (4, [0b1110, 0b0001, 0b0110, 0b0000, 0b0100])
}

fn text_width(s: &str, scale: i16) -> i16 {
    let w: i16 = s.bytes().map(|c| glyph(c).0 as i16 + 1).sum();
    (w - 1).max(0) * scale
}

/// Draws `s` with its capitals' top at `y`, centred on `x`
fn text_centered<'s>(t: &mut impl Renderer<'s>, s: &str, x: i16, y: i16, c: Color, scale: i16) {
    text(t, s, x - text_width(s, scale) / 2, y, c, scale);
}

fn text<'s>(t: &mut impl Renderer<'s>, s: &str, x: i16, y: i16, c: Color, scale: i16) {
    let mut cx = x;
    for ch in s.bytes() {
        let (w, rows) = glyph(ch);
        for (row, bits) in rows.iter().enumerate() {
            for col in 0..w {
                if bits & (1 << (w - 1 - col)) != 0 {
                    px(t, cx + col as i16 * scale, y + row as i16 * scale, scale, scale, c);
                }
            }
        }
        cx += (w as i16 + 1) * scale;
    }
}

/// Up to 5 digits, without core::fmt
fn number(n: u32, buf: &mut [u8; 5]) -> &str {
    let mut n = n.min(99999);
    let mut i = buf.len();
    loop {
        i -= 1;
        buf[i] = b'0' + (n % 10) as u8;
        n /= 10;
        if n == 0 || i == 0 {
            break;
        }
    }
    // SAFETY: only ASCII digits were written
    unsafe { core::str::from_utf8_unchecked(&buf[i..]) }
}

// ---------------------------------------------------------------- icons

/// 13 x 13 icons: '#' ink, 'a' fill, 'b'/'d' highlights, 'c' backdrop
type Icon = [&'static [u8; 13]; 13];

const ICON_WALLET: Icon = [
    b".............",
    b"..#########..",
    b".#bbbbbbbbb#.",
    b"#############",
    b"#aaaaaaaaaaa#",
    b"#aaaaaaa#####",
    b"#aaaaaa#bbbb#",
    b"#aaaaaa#b##b#",
    b"#aaaaaa#bbbb#",
    b"#aaaaaaa#####",
    b"#aaaaaaaaaaa#",
    b"#############",
    b".............",
];
const ICON_BT: Icon = [
    b"....ccccc....",
    b"...ccc#ccc...",
    b"..cccc##ccc..",
    b".ccc#c#c#ccc.",
    b"ccccc##cc#ccc",
    b"cccccc#c#cccc",
    b"cccccc##ccccc",
    b"cccccc#c#cccc",
    b"ccccc##cc#ccc",
    b".ccc#c#c#ccc.",
    b"..cccc##ccc..",
    b"...ccc#ccc...",
    b"....ccccc....",
];
const ICON_AIR: Icon = [
    b"#####c#c#####",
    b"#ccc#ccc#ccc#",
    b"#c#c#c#c#c#c#",
    b"#ccc#c#c#ccc#",
    b"#####ccc#####",
    b"ccccccccccccc",
    b"#c##cc#ccc#c#",
    b"ccccccc#ccccc",
    b"#####cccc##c#",
    b"#ccc#c#cccccc",
    b"#c#c#ccc##c#c",
    b"#ccc#c#ccccc#",
    b"#####ccc#c#c#",
];
const ICON_GAMES: Icon = [
    b".............",
    b".............",
    b".............",
    b"..#########..",
    b".#aaaaaaaaa#.",
    b"#aaabaaaaaaa#",
    b"#aabbbaadada#",
    b"#aaabaaaaaaa#",
    b"#aaaaaaaaaaa#",
    b"#aaa#...#aaa#",
    b".###.....###.",
    b".............",
    b".............",
];
const ICON_SETTINGS: Icon = [
    b"......#......",
    b"......#......",
    b"..##.###.##..",
    b"..#########..",
    b"...#######...",
    b"..###...###..",
    b"#####...#####",
    b"..###...###..",
    b"...#######...",
    b"..#########..",
    b"..##.###.##..",
    b"......#......",
    b"......#......",
];

const ICON_MAZE: Icon = [
    b"#############",
    b"#ccc#ccccccc#",
    b"#c#c#c#####c#",
    b"#c#ccc#ccc#c#",
    b"#c#####c#c#c#",
    b"#ccccc#c#ccc#",
    b"#####c#c#####",
    b"#ccc#c#ccccc#",
    b"#c#c#c#####c#",
    b"#c#ccc#ccc#c#",
    b"#c#####c#c#c#",
    b"#ccccccc#cdd#",
    b"#############",
];
const ICON_SNAKE: Icon = [
    b".............",
    b".aaaaaaaaa...",
    b".aaaaaaaaa...",
    b".aa..........",
    b".aa..........",
    b".aaaaaaaaaa..",
    b".aaaaaaaaaa..",
    b".........aa..",
    b"..d......aa..",
    b".........aa..",
    b".bbaaaaaaaa..",
    b".bbaaaaaaaa..",
    b".............",
];
const ICON_BACK: Icon = [
    b".............",
    b"......#......",
    b".....##......",
    b"....###......",
    b"...#########.",
    b"..##########.",
    b".###########.",
    b"..##########.",
    b"...#########.",
    b"....###......",
    b".....##......",
    b"......#......",
    b".............",
];

/// 7 x 7 status icons and a 5 x 5 padlock
const MINI_BT: [&[u8; 7]; 7] = [
    b"...#...", b"...##..", b".#.#.#.", b"..###..", b".#.#.#.", b"...##..", b"...#...",
];
const MINI_AIR: [&[u8; 7]; 7] = [
    b"...#...", b".#.#.#.", b"..###..", b"#######", b"..###..", b".#.#.#.", b"...#...",
];
const MINI_LOCK: [&[u8; 5]; 5] = [b".###.", b"#...#", b"#####", b"##.##", b"#####"];

/// An icon's palette: ink, fill, highlight, red highlight, backdrop
struct Palette([u32; 5]);

const PAL_WALLET: Palette = Palette([0x3b1a06, 0xe0862e, 0xffd98f, 0, 0]);
const PAL_BT: Palette = Palette([0xffffff, 0, 0, 0, 0x2f6bff]);
const PAL_AIR: Palette = Palette([0x062833, 0, 0, 0, 0xa8f2ff]);
const PAL_GAMES: Palette = Palette([0x1d1f2e, 0xcfd3e0, 0x1d1f2e, 0xff3b3b, 0]);
const PAL_SETTINGS: Palette = Palette([0xcdd5e6, 0, 0, 0, 0]);
const PAL_MAZE: Palette = Palette([0xaf50ff, 0, 0, 0xd7ff00, 0x1a0d40]);
const PAL_SNAKE: Palette = Palette([0, 0xd7ff00, 0xffb52e, 0xff3b3b, 0]);

fn draw_icon<'s>(t: &mut impl Renderer<'s>, icon: &Icon, pal: &Palette, x: i16, y: i16, grey: bool) {
    // backdrop first, ink last
    for pass in [b'c', b'a', b'b', b'd', b'#'] {
        let slot = match pass {
            b'#' => 0,
            b'a' => 1,
            b'b' => 2,
            b'd' => 3,
            _ => 4,
        };
        let mut c = pal.0[slot];
        if c == 0 && pass != b'#' {
            continue;
        }
        if grey {
            let (r, g, b) = ((c >> 16) & 255, (c >> 8) & 255, c & 255);
            let l = (r * 30 + g * 59 + b * 11) * 45 / 10000 + 30;
            c = (l << 16) | (l << 8) | (l + 8).min(255);
        }
        let color = rgb(c);
        for (row, line) in icon.iter().enumerate() {
            for (col, &ch) in line.iter().enumerate() {
                if ch == pass {
                    px(t, x + col as i16 * 2, y + row as i16 * 2, 2, 2, color);
                }
            }
        }
    }
}

fn draw_mini<'s, const N: usize>(t: &mut impl Renderer<'s>, rows: &[&[u8; N]; N], x: i16, y: i16, c: Color) {
    for (row, line) in rows.iter().enumerate() {
        for (col, &ch) in line.iter().enumerate() {
            if ch == b'#' {
                px(t, x + col as i16, y + row as i16, 1, 1, c);
            }
        }
    }
}

// ---------------------------------------------------------------- model

#[derive(Clone, Copy, PartialEq)]
enum Tile {
    Wallet,
    Bluetooth,
    Air,
    Games,
    Settings,
    Switch,
}

const TILES: [Tile; 6] = [
    Tile::Wallet,
    Tile::Bluetooth,
    Tile::Air,
    Tile::Games,
    Tile::Settings,
    Tile::Switch,
];

impl Tile {
    fn name(self) -> &'static str {
        match self {
            Tile::Wallet => "WALLET",
            Tile::Bluetooth => "BLUETOOTH",
            Tile::Air => "AIR-GAP",
            Tile::Games => "GAMES",
            Tile::Settings => "SETTINGS",
            Tile::Switch => "SWITCH",
        }
    }

    /// Needs the device unlocked: the links and the settings (the games and
    /// the switch hold nothing)
    fn needs_pin(self) -> bool {
        matches!(self, Tile::Wallet | Tile::Bluetooth | Tile::Air | Tile::Settings)
    }

    /// A link the airgap switch turns on or off: Some(live)
    fn link(self, airgapped: bool) -> Option<bool> {
        match self {
            Tile::Wallet | Tile::Bluetooth => Some(!airgapped),
            Tile::Air => Some(airgapped),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Screen {
    Grid,
    Games,
    Settings,
    Snake,
    #[cfg(feature = "ambire_game")]
    Maze,
}

struct ListItem {
    name: &'static str,
    sub: &'static str,
}

/// The games grid: one row of tiles
const GAMES_ROW_Y: i16 = 37;
const GAMES_LIST: [ListItem; 3] = [
    ListItem { name: "MAZE", sub: "3D. FIND THE EXIT" },
    ListItem { name: "SNAKE", sub: "EAT AND GROW" },
    ListItem { name: "BACK", sub: "TO THE HOME SCREEN" },
];
const SETTINGS_LIST: [ListItem; 2] = [
    ListItem { name: "LOCK", sub: "LOCK THE DEVICE" },
    ListItem { name: "BACK", sub: "TO THE HOME SCREEN" },
];

// ---------------------------------------------------------------- snake

const SNAKE_COLS: i16 = 28;
const SNAKE_ROWS: i16 = 32;
const SNAKE_X: i16 = 4;
const SNAKE_Y: i16 = 13;
const SNAKE_MAX: usize = 256;

struct Snake {
    /// A ring of cells, `head` the newest
    body: [(i8, i8); SNAKE_MAX],
    head: usize,
    len: usize,
    dir: (i8, i8),
    next_dir: (i8, i8),
    food: (i8, i8),
    score: u32,
    last_step: Instant,
    over: bool,
    paused: bool,
    seed: u32,
}

impl Snake {
    fn new() -> Self {
        Self {
            body: [(0, 0); SNAKE_MAX],
            head: 0,
            len: 0,
            dir: (1, 0),
            next_dir: (1, 0),
            food: (0, 0),
            score: 0,
            last_step: Instant::now(),
            over: false,
            paused: false,
            seed: 1,
        }
    }

    fn random(&mut self) -> u32 {
        // xorshift32
        let mut x = self.seed;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        self.seed = x;
        x
    }

    fn start(&mut self) {
        let now = Instant::now();
        // the time of the press is the only entropy a game needs
        self.seed = (sys::time::ticks_us() as u32) | 1;
        self.len = 4;
        self.head = 3;
        for i in 0..4 {
            self.body[i] = (4 + i as i8, (SNAKE_ROWS / 2) as i8);
        }
        self.dir = (1, 0);
        self.next_dir = (1, 0);
        self.score = 0;
        self.over = false;
        self.paused = false;
        self.last_step = now;
        self.place_food();
    }

    fn cell(&self, i: usize) -> (i8, i8) {
        self.body[(self.head + SNAKE_MAX - i) % SNAKE_MAX]
    }

    fn occupied(&self, c: (i8, i8)) -> bool {
        (0..self.len).any(|i| self.cell(i) == c)
    }

    fn place_food(&mut self) {
        for _ in 0..64 {
            let x = (self.random() % SNAKE_COLS as u32) as i8;
            let y = (self.random() % SNAKE_ROWS as u32) as i8;
            if !self.occupied((x, y)) {
                self.food = (x, y);
                return;
            }
        }
    }

    fn steer(&mut self, d: (i8, i8)) {
        // never straight back into the neck
        if (d.0 + self.dir.0, d.1 + self.dir.1) != (0, 0) {
            self.next_dir = d;
        }
    }

    fn step_ms(&self) -> u32 {
        160u32.saturating_sub(self.score * 4).max(70)
    }

    /// Moves when it is time; true when something changed
    fn tick(&mut self, now: Instant) -> bool {
        if self.over || self.paused {
            return false;
        }
        if now.saturating_duration_since(self.last_step).to_millis() < self.step_ms() {
            return false;
        }
        self.last_step = now;
        self.dir = self.next_dir;
        let (hx, hy) = self.cell(0);
        // the field wraps round, like the phones' later snakes
        let next = (
            (hx as i16 + self.dir.0 as i16).rem_euclid(SNAKE_COLS) as i8,
            (hy as i16 + self.dir.1 as i16).rem_euclid(SNAKE_ROWS) as i8,
        );
        let eats = next == self.food;
        // the tail moves away this step, unless it grows
        let body_len = if eats { self.len } else { self.len - 1 };
        if (0..body_len).any(|i| self.cell(i) == next) {
            self.over = true;
            return true;
        }
        self.head = (self.head + 1) % SNAKE_MAX;
        self.body[self.head] = next;
        if eats {
            self.len = (self.len + 1).min(SNAKE_MAX);
            self.score += 1;
            self.place_food();
        }
        true
    }

    fn render<'s>(&self, t: &mut impl Renderer<'s>, now: Instant) {
        // the field: dark, framed in the bars' blue
        px(t, SNAKE_X - 1, SNAKE_Y - 1, SNAKE_COLS * 4 + 2, SNAKE_ROWS * 4 + 2, BAR_LIGHT);
        px(t, SNAKE_X, SNAKE_Y, SNAKE_COLS * 4, SNAKE_ROWS * 4, rgb(0x0a1030));
        let cell = |t: &mut _, (x, y): (i8, i8), c: Color| {
            px(t, SNAKE_X + x as i16 * 4, SNAKE_Y + y as i16 * 4, 3, 3, c);
        };
        let blink = (now.saturating_duration_since(self.last_step).to_millis() / 250) % 2 == 0;
        if blink || self.paused || self.over {
            cell(t, self.food, RED);
        }
        for i in (0..self.len).rev() {
            cell(t, self.cell(i), if i == 0 { ORANGE_HI } else { VOLT });
        }
    }
}

// ---------------------------------------------------------------- screen parts

fn tile_pos(i: usize) -> (i16, i16) {
    (3 + (i % 3) as i16 * 39, 15 + (i / 3) as i16 * 44)
}

/// What the status bar says beside the live link's icon
enum StatusText<'a> {
    Title(&'a str),
    /// The device's name, or the notification when there is one
    Home(TString<'static>, Option<&'a Notification>),
}

fn draw_status<'s>(t: &mut impl Renderer<'s>, airgapped: bool, text: StatusText) {
    if airgapped {
        draw_mini(t, &MINI_AIR, 2, 2, WHITE);
    } else {
        draw_mini(t, &MINI_BT, 2, 2, WHITE);
        for i in 0..4 {
            px(t, 11 + i * 2, 7 - i, 1, i + 1, WHITE);
        }
    }
    match text {
        StatusText::Title(s) => text_centered(t, s, 60, 3, WHITE, 1),
        StatusText::Home(_, Some(n)) => {
            let c = match n.level {
                NotificationLevel::Alert => OFF_RED,
                NotificationLevel::Warning => ORANGE_HI,
                _ => VOLT,
            };
            n.text.map(|s| text_centered(t, s, 64, 3, c, 1));
        }
        StatusText::Home(label, None) => label.map(|s| text_centered(t, s, 60, 3, WHITE, 1)),
    }
}

fn draw_soft_key<'s>(t: &mut impl Renderer<'s>, s: &str) {
    text_centered(t, s, 60, 151, WHITE, 1);
}

/// The label band: a name at 2x with a shadow, a coloured line under it
fn draw_name<'s>(t: &mut impl Renderer<'s>, name: &str, sub: &str, sub_c: Color) {
    text_centered(t, name, 61, 109, SHADOW, 2);
    text_centered(t, name, 60, 108, WHITE, 2);
    text_centered(t, sub, 60, 126, sub_c, 1);
}

/// The orange cursor
fn draw_cursor<'s>(t: &mut impl Renderer<'s>, x: i16, y: i16, w: i16, h: i16) {
    round_rect(t, x, y, w, h, rgb(0xfff1c2), 255);
    px(t, x + 1, y + 1, w - 2, (h - 2) / 2, ORANGE_HI);
    px(t, x + 1, y + 1 + (h - 2) / 2, w - 2, h - 2 - (h - 2) / 2, ORANGE);
    px(t, x + 2, y + 1, w - 4, 1, rgb(0xffe08a));
}

/// The home screen's six tiles; `locked` greys and padlocks the ones that
/// need the PIN
fn draw_tiles<'s>(t: &mut impl Renderer<'s>, airgapped: bool, sel: Option<usize>, locked: bool) {
    for (i, &tile) in TILES.iter().enumerate() {
        let (tx, ty) = tile_pos(i);
        let on = sel == Some(i);
        if on {
            draw_cursor(t, tx, ty, 36, 40);
        } else {
            round_rect(t, tx, ty, 36, 40, WHITE, 18);
        }
        if tile == Tile::Switch {
            // the side switch: off AIR on the left, AIR on the right
            round_rect(t, tx + 5, ty + 10, 26, 10, rgb(0x8a90b0), 255);
            round_rect(t, tx + 6, ty + 11, 24, 8, rgb(0x141626), 255);
            let pos = airgapped as i16;
            round_rect(t, tx + 7 + pos * 11, ty + 12, 11, 6, VOLT, 255);
            let mini_c = |live: bool| match (live, on) {
                (true, true) => rgb(0x1b0d00),
                (true, false) => VOLT,
                (false, true) => rgb(0xa85a10),
                (false, false) => rgb(0x6a7090),
            };
            draw_mini(t, &MINI_BT, tx + 9, ty + 24, mini_c(!airgapped));
            draw_mini(t, &MINI_AIR, tx + 20, ty + 24, mini_c(airgapped));
            continue;
        }
        let (icon, pal) = match tile {
            Tile::Wallet => (&ICON_WALLET, &PAL_WALLET),
            Tile::Bluetooth => (&ICON_BT, &PAL_BT),
            Tile::Air => (&ICON_AIR, &PAL_AIR),
            Tile::Games => (&ICON_GAMES, &PAL_GAMES),
            _ => (&ICON_SETTINGS, &PAL_SETTINGS),
        };
        let link = tile.link(airgapped);
        let off = (locked && tile.needs_pin()) || link == Some(false);
        draw_icon(t, icon, pal, tx + 5, ty + 6, off);
        if off {
            let c = if on { rgb(0x3b1a06) } else { rgb(0xb9bfd4) };
            draw_mini(t, &MINI_LOCK, tx + 28, ty + 32, c);
        }
        if link == Some(true) && !locked {
            px(t, tx + 15, ty + 35, 6, 2, VOLT);
        }
    }
}

// ---------------------------------------------------------------- home

pub struct Home {
    /// The lock screen: the tiles that need the PIN ask for it
    locked: bool,
    /// A PIN is set, so Lock can lock
    lockable: bool,
    screen: Screen,
    sel: usize,
    list_sel: usize,
    overlay: Option<(&'static str, &'static str, Instant)>,
    airgapped: bool,
    label: TString<'static>,
    notification: Option<Notification>,
    poll: Timer,
    held: u32,
    /// A held direction repeats: since when, and after how long
    repeat: Option<(Instant, u32)>,
    center_down: Option<Instant>,
    /// The centre press counts only once it was seen released here (not the
    /// press that woke the device, nor one that began on another screen)
    center_armed: bool,
    touch_start: Option<Point>,
    snake: Snake,
    #[cfg(feature = "ambire_game")]
    maze: Game,
}

impl Home {
    pub fn new(
        label: TString<'static>,
        notification: Option<Notification>,
        locked: bool,
        lockable: bool,
    ) -> Self {
        Self {
            locked,
            lockable,
            screen: Screen::Grid,
            sel: 1,
            list_sel: 0,
            overlay: None,
            airgapped: Self::read_airgap(),
            label,
            notification,
            poll: Timer::new(),
            held: 0,
            repeat: None,
            center_down: None,
            center_armed: false,
            touch_start: None,
            snake: Snake::new(),
            #[cfg(feature = "ambire_game")]
            maze: Game::new(),
        }
    }

    fn read_airgap() -> bool {
        #[cfg(feature = "ambire_airgap")]
        return crate::trezorhal::airgap::engaged();
        #[cfg(not(feature = "ambire_airgap"))]
        return false;
    }

    /// True while the 3D maze has the whole screen
    pub fn draws_own_background(&self) -> bool {
        #[cfg(feature = "ambire_game")]
        return self.screen == Screen::Maze;
        #[cfg(not(feature = "ambire_game"))]
        return false;
    }

    fn show(&mut self, ctx: &mut EventCtx, title: &'static str, sub: &'static str) {
        self.overlay = Some((title, sub, Instant::now()));
        ctx.request_paint();
    }

    fn go(&mut self, ctx: &mut EventCtx, screen: Screen) {
        self.screen = screen;
        self.list_sel = 0;
        self.overlay = None;
        ctx.request_paint();
    }

    pub fn event(&mut self, ctx: &mut EventCtx, event: Event) -> Option<HomeAction> {
        #[cfg(feature = "ambire_game")]
        if self.screen == Screen::Maze {
            // the maze takes its touches and animation frames; it ends itself
            // on its exit corner
            self.maze.event(ctx, event);
            if !self.maze.is_active() {
                self.go(ctx, Screen::Games);
            }
        }

        match event {
            Event::Attach(_) => {
                self.poll.start(ctx, Duration::from_millis(POLL_MS));
                ctx.request_paint();
                None
            }
            Event::Timer(_) if self.poll.expire(event) => {
                self.poll.start(ctx, Duration::from_millis(POLL_MS));
                self.tick(ctx)
            }
            Event::Touch(TouchEvent::TouchStart(p)) => {
                self.touch_start = Some(p);
                None
            }
            Event::Touch(TouchEvent::TouchEnd(_)) => match self.touch_start.take() {
                Some(p) => self.tap(ctx, Point::new(p.x / S, p.y / S)),
                None => None,
            },
            _ => None,
        }
    }

    /// Polls the joystick and the airgap switch; runs the snake
    fn tick(&mut self, ctx: &mut EventCtx) -> Option<HomeAction> {
        let now = Instant::now();

        let airgapped = Self::read_airgap();
        if airgapped != self.airgapped {
            self.airgapped = airgapped;
            ctx.request_paint();
        }
        if let Some((_, _, at)) = self.overlay {
            if now.saturating_duration_since(at).to_millis() > OVERLAY_MS {
                self.overlay = None;
                ctx.request_paint();
            }
        }

        let contacts = joystick::read() & joystick::CONTACTS;
        let mut action = None;

        // the centre press selects on its release, if it was short
        if contacts & joystick::CENTER != 0 {
            if self.center_down.is_none() && self.center_armed {
                self.center_down = Some(now);
            }
        } else {
            if let Some(t) = self.center_down.take() {
                if now.saturating_duration_since(t).to_millis() <= SELECT_MAX_MS {
                    action = self.key(ctx, Key::Select);
                }
            }
            self.center_armed = true;
        }

        let dirs = contacts & DIRS;
        let pressed = dirs & !self.held;
        let repeats = dirs != 0
            && pressed == 0
            && self
                .repeat
                .is_some_and(|(since, wait)| now.saturating_duration_since(since).to_millis() >= wait);
        #[cfg(feature = "ambire_game")]
        if self.screen == Screen::Maze && dirs != self.held {
            self.maze
                .steer(dirs & UP != 0, dirs & DOWN != 0, dirs & LEFT != 0, dirs & RIGHT != 0);
        }
        self.held = dirs;
        if dirs == 0 {
            self.repeat = None;
        } else if pressed != 0 || repeats {
            let wait = if pressed != 0 { REPEAT_DELAY_MS } else { REPEAT_MS };
            self.repeat = Some((now, wait));
            let bits = if pressed != 0 { pressed } else { dirs };
            for (bit, key) in [(UP, Key::Up), (DOWN, Key::Down), (LEFT, Key::Left), (RIGHT, Key::Right)] {
                if bits & bit != 0 {
                    action = action.or(self.key(ctx, key));
                    break;
                }
            }
        }

        if self.screen == Screen::Snake && self.snake.tick(now) {
            ctx.request_paint();
        }
        if self.screen == Screen::Snake && !self.snake.paused && !self.snake.over {
            // the food blinks
            ctx.request_paint();
        }
        action
    }

    fn key(&mut self, ctx: &mut EventCtx, key: Key) -> Option<HomeAction> {
        if self.overlay.is_some() && self.screen != Screen::Snake {
            self.overlay = None;
            ctx.request_paint();
            return None;
        }
        match self.screen {
            Screen::Grid => {
                let (mut c, mut r) = (self.sel % 3, self.sel / 3);
                match key {
                    Key::Left => c = (c + 2) % 3,
                    Key::Right => c = (c + 1) % 3,
                    Key::Up | Key::Down => r = 1 - r,
                    Key::Select => return self.select_tile(ctx),
                }
                self.sel = r * 3 + c;
                ctx.request_paint();
                None
            }
            Screen::Games => {
                match key {
                    Key::Left => self.list_sel = (self.list_sel + 2) % 3,
                    Key::Right => self.list_sel = (self.list_sel + 1) % 3,
                    Key::Up | Key::Down => {}
                    Key::Select => return self.select_item(ctx),
                }
                ctx.request_paint();
                None
            }
            Screen::Settings => {
                let n = SETTINGS_LIST.len();
                match key {
                    Key::Up => self.list_sel = (self.list_sel + n - 1) % n,
                    Key::Down => self.list_sel = (self.list_sel + 1) % n,
                    Key::Left => self.go(ctx, Screen::Grid),
                    Key::Right => {}
                    Key::Select => return self.select_item(ctx),
                }
                ctx.request_paint();
                None
            }
            Screen::Snake => {
                let stopped = self.snake.paused || self.snake.over;
                let s = &mut self.snake;
                match key {
                    Key::Up => s.steer((0, -1)),
                    Key::Down => s.steer((0, 1)),
                    Key::Left if stopped => {
                        self.go(ctx, Screen::Games);
                        self.list_sel = 1;
                        return None;
                    }
                    Key::Left => s.steer((-1, 0)),
                    Key::Right => s.steer((1, 0)),
                    Key::Select if s.over => s.start(),
                    Key::Select => {
                        s.paused = !s.paused;
                        // a resumed game takes its next step a full step later
                        s.last_step = Instant::now();
                    }
                }
                ctx.request_paint();
                None
            }
            #[cfg(feature = "ambire_game")]
            Screen::Maze => {
                if key == Key::Select {
                    self.maze.stop(ctx);
                    self.go(ctx, Screen::Games);
                }
                None
            }
        }
    }

    fn select_tile(&mut self, ctx: &mut EventCtx) -> Option<HomeAction> {
        let tile = TILES[self.sel];
        if self.locked && tile.needs_pin() {
            return Some(HomeAction::Unlock);
        }
        match (tile, tile.link(self.airgapped)) {
            (_, Some(false)) if self.airgapped => {
                self.show(ctx, "AIR-GAPPED", "SLIDE THE SWITCH OFF AIR")
            }
            (_, Some(false)) => self.show(ctx, "CONNECTED", "SLIDE THE SWITCH TO AIR"),
            (Tile::Wallet, _) => self.show(ctx, "WALLET", "OPEN AMBIRE, USB OR BT"),
            (Tile::Bluetooth, _) => return Some(HomeAction::Bluetooth),
            (Tile::Air, _) => return Some(HomeAction::Airgap),
            (Tile::Games, _) => self.go(ctx, Screen::Games),
            (Tile::Settings, _) => self.go(ctx, Screen::Settings),
            (Tile::Switch, _) => self.show(ctx, "SIDE SWITCH", "AIR CUTS USB AND BT"),
        }
        None
    }

    fn select_item(&mut self, ctx: &mut EventCtx) -> Option<HomeAction> {
        match (self.screen, self.list_sel) {
            #[cfg(feature = "ambire_game")]
            (Screen::Games, 0) => {
                self.go(ctx, Screen::Maze);
                self.maze.start(ctx);
            }
            #[cfg(not(feature = "ambire_game"))]
            (Screen::Games, 0) => self.show(ctx, "MAZE", "NOT IN THIS BUILD"),
            (Screen::Games, 1) => {
                self.go(ctx, Screen::Snake);
                self.snake.start();
            }
            // without a PIN the firmware cannot lock, and would just come back
            (Screen::Settings, 0) if !self.lockable => self.show(ctx, "NO PIN SET", "SET A PIN TO LOCK"),
            (Screen::Settings, 0) => return Some(HomeAction::Lock),
            (Screen::Games, _) => {
                self.go(ctx, Screen::Grid);
                self.sel = 3;
            }
            _ => {
                self.go(ctx, Screen::Grid);
                self.sel = 4;
            }
        }
        None
    }

    fn tap(&mut self, ctx: &mut EventCtx, p: Point) -> Option<HomeAction> {
        if self.overlay.is_some() && self.screen != Screen::Snake {
            return self.key(ctx, Key::Select);
        }
        match self.screen {
            Screen::Grid => {
                for i in 0..6 {
                    let (tx, ty) = tile_pos(i);
                    if p.x >= tx && p.x < tx + 36 && p.y >= ty && p.y < ty + 40 {
                        self.sel = i;
                        ctx.request_paint();
                        return self.select_tile(ctx);
                    }
                }
                None
            }
            Screen::Games => {
                for i in 0..3 {
                    let (tx, _) = tile_pos(i);
                    if p.x >= tx && p.x < tx + 36 && p.y >= GAMES_ROW_Y && p.y < GAMES_ROW_Y + 40 {
                        self.list_sel = i;
                        ctx.request_paint();
                        return self.select_item(ctx);
                    }
                }
                None
            }
            Screen::Settings => {
                for i in 0..SETTINGS_LIST.len() {
                    let y = Self::row_y(i);
                    if p.y >= y && p.y < y + 26 {
                        self.list_sel = i;
                        return self.select_item(ctx);
                    }
                }
                None
            }
            Screen::Snake => {
                // the top-left corner leaves, anything else pauses
                if p.x < 24 && p.y < 12 {
                    self.go(ctx, Screen::Games);
                    None
                } else {
                    self.key(ctx, Key::Select)
                }
            }
            #[cfg(feature = "ambire_game")]
            Screen::Maze => None,
        }
    }

    fn row_y(i: usize) -> i16 {
        16 + i as i16 * 30
    }

    // ------------------------------------------------------------ render

    /// Draws everything over the wallpaper (the caller blits it first, unless
    /// `draws_own_background`)
    pub fn render<'s>(&'s self, t: &mut impl Renderer<'s>) {
        let now = Instant::now();
        match self.screen {
            #[cfg(feature = "ambire_game")]
            Screen::Maze => {
                self.maze.render(t);
                return;
            }
            Screen::Grid => self.render_grid(t),
            Screen::Games => self.render_games(t),
            Screen::Settings => self.render_list(t, "SETTINGS", &SETTINGS_LIST),
            Screen::Snake => self.render_snake(t, now),
        }
        if let Some((title, sub, _)) = self.overlay {
            round_rect(t, 6, 58, 108, 40, BAR_LIGHT, 255);
            round_rect(t, 8, 60, 104, 36, rgb(0xf4f6ff), 255);
            text_centered(t, title, 60, 67, rgb(0x0a1440), 1);
            text_centered(t, sub, 60, 81, rgb(0x3d4a80), 1);
        }
        dot_matrix(t);
    }

    fn render_status<'s>(&self, t: &mut impl Renderer<'s>, title: Option<&str>) {
        let title = match title {
            Some(title) => StatusText::Title(title),
            None => StatusText::Home(self.label, self.notification.as_ref()),
        };
        draw_status(t, self.airgapped, title);
    }

    fn render_soft_key<'s>(&self, t: &mut impl Renderer<'s>, s: &str) {
        draw_soft_key(t, s);
    }

    fn render_name<'s>(&self, t: &mut impl Renderer<'s>, name: &str, sub: &str, sub_c: Color) {
        draw_name(t, name, sub, sub_c);
    }

    fn render_cursor<'s>(&self, t: &mut impl Renderer<'s>, x: i16, y: i16, w: i16, h: i16) {
        draw_cursor(t, x, y, w, h);
    }

    fn render_grid<'s>(&self, t: &mut impl Renderer<'s>) {
        self.render_status(t, None);
        draw_tiles(t, self.airgapped, Some(self.sel), self.locked);

        let tile = TILES[self.sel];
        let link = tile.link(self.airgapped);
        if self.locked && tile.needs_pin() {
            self.render_name(t, tile.name(), "LOCKED: SELECT FOR THE PIN", OFF_RED);
            self.render_soft_key(t, "UNLOCK");
            return;
        }
        let (sub, sub_c) = match (tile, link) {
            (_, Some(false)) if self.airgapped => ("OFF: AIR-GAPPED", OFF_RED),
            (_, Some(false)) => ("OFF: CONNECTED", OFF_RED),
            (Tile::Wallet, _) => ("LIVE: USB", VOLT),
            (_, Some(true)) => ("LIVE", VOLT),
            (Tile::Switch, _) if self.airgapped => ("AIR-GAPPED", VOLT),
            (Tile::Switch, _) => ("CONNECTED", VOLT),
            (Tile::Games, _) => ("MAZE, SNAKE", SUB_BLUE),
            _ => ("DEVICE", SUB_BLUE),
        };
        self.render_name(t, tile.name(), sub, sub_c);
        let soft = if tile == Tile::Switch || link == Some(false) { "SLIDE" } else { "SELECT" };
        self.render_soft_key(t, soft);
    }

    fn render_list<'s>(&self, t: &mut impl Renderer<'s>, title: &str, items: &[ListItem]) {
        self.render_status(t, Some(title));
        for (i, item) in items.iter().enumerate() {
            let y = Self::row_y(i);
            if i == self.list_sel {
                self.render_cursor(t, 6, y, 108, 26);
            } else {
                round_rect(t, 6, y, 108, 26, WHITE, 18);
            }
            text(t, item.name, 14, y + 8, WHITE, 2);
        }
        let item = &items[self.list_sel];
        self.render_name(t, item.name, item.sub, SUB_BLUE);
        self.render_soft_key(t, "SELECT");
    }

    fn render_games<'s>(&self, t: &mut impl Renderer<'s>) {
        self.render_status(t, Some("GAMES"));
        let icons: [(&Icon, &Palette); 3] =
            [(&ICON_MAZE, &PAL_MAZE), (&ICON_SNAKE, &PAL_SNAKE), (&ICON_BACK, &PAL_SETTINGS)];
        for (i, (icon, pal)) in icons.iter().enumerate() {
            let (tx, _) = tile_pos(i);
            if i == self.list_sel {
                self.render_cursor(t, tx, GAMES_ROW_Y, 36, 40);
            } else {
                round_rect(t, tx, GAMES_ROW_Y, 36, 40, WHITE, 18);
            }
            draw_icon(t, icon, pal, tx + 5, GAMES_ROW_Y + 7, false);
        }
        let item = &GAMES_LIST[self.list_sel];
        self.render_name(t, item.name, item.sub, SUB_BLUE);
        self.render_soft_key(t, "SELECT");
    }

    fn render_snake<'s>(&self, t: &mut impl Renderer<'s>, now: Instant) {
        let mut buf = [0u8; 5];
        let score = number(self.snake.score, &mut buf);
        text(t, "X SNAKE", 3, 3, WHITE, 1);
        text(t, score, LW - 3 - text_width(score, 1), 3, VOLT, 1);
        self.snake.render(t, now);
        let soft = if self.snake.over {
            "CENTRE: AGAIN  LEFT: QUIT"
        } else if self.snake.paused {
            "CENTRE: PLAY  LEFT: QUIT"
        } else {
            "CENTRE: PAUSE"
        };
        self.render_soft_key(t, soft);
        if self.snake.over || self.snake.paused {
            round_rect(t, 22, 64, 76, 24, BAR_LIGHT, 255);
            round_rect(t, 24, 66, 72, 20, rgb(0xf4f6ff), 255);
            let title = if self.snake.over { "GAME OVER" } else { "PAUSED" };
            text_centered(t, title, 60, 73, rgb(0x0a1440), 1);
        }
    }
}
