//! Ambire boards: a small raycaster maze ("Doom-like") behind 7 quick taps on
//! the home screen.
//!
//! The home screen hands it every event and lets it paint the whole screen
//! while it runs; a tap on the exit corner (top left) hands the screen back.
//! It holds no secrets and calls nothing outside the UI: a game, not an app.
//!
//! Controls: hold the left or right third to turn, the middle third's upper
//! half to walk forward and its lower half to walk back. Reach the green
//! block to win.

use sys::time::Instant;

use super::super::fonts;
use super::theme;
use crate::ui::component::{Event, EventCtx};
use crate::ui::constant::{HEIGHT, WIDTH};
use crate::ui::display::Color;
use crate::ui::event::TouchEvent;
use crate::ui::geometry::{Alignment, Alignment2D, Offset, Point, Rect};
use crate::ui::shape::{self, Renderer};

/// Taps in a row that start the game, each short and soon after the previous
const TAPS_TO_START: u8 = 7;
const TAP_MAX_MS: u32 = 350;
const TAP_GAP_MAX_MS: u32 = 600;

/// The 3D view fills the screen above the bottom bar
const VIEW_H: i16 = HEIGHT - 40;
/// Wall slices: 2 px wide each
const COLS: i16 = WIDTH / 2;
/// Exit corner (top left)
const EXIT_SIZE: i16 = 48;

const MOVE_SPEED: f32 = 2.2; // tiles per second
const TURN_SPEED: f32 = 2.4; // radians per second
const RADIUS: f32 = 0.22; // player's distance kept from walls, in tiles
const FOV_PLANE: f32 = 0.66; // camera plane length: ~66 degree field of view

// Ambire brand colours (AmbireTech/ambire-brand README)
const PURPLE: (u8, u8, u8) = (0x60, 0x00, 0xff);
const LIGHT_PURPLE: (u8, u8, u8) = (0xaf, 0x50, 0xff);
const GREEN: (u8, u8, u8) = (0xd7, 0xff, 0x00);
const BLUE: (u8, u8, u8) = (0x38, 0x71, 0xff);

/// 16 x 16 maze: '#' wall, 'o' pillar, 'X' the exit, '.' floor; the player
/// starts at (1.5, 1.5) facing east.
const MAP: [&[u8; 16]; 16] = [
    b"################",
    b"#....#.........#",
    b"#.##.#.######..#",
    b"#.#..#......#..#",
    b"#.#.####.##.#.##",
    b"#.#......#..#..#",
    b"#.####.#.#.###.#",
    b"#....#.#.#...#.#",
    b"####.#.#o###.#.#",
    b"#....#.#.....#.#",
    b"#.####.#####.#.#",
    b"#.#....#...#.#.#",
    b"#.#.##.#.#.#.#.#",
    b"#...#....#...#X#",
    b"#o#...##...#...#",
    b"################",
];

fn cell(x: i32, y: i32) -> u8 {
    if x < 0 || y < 0 || x >= 16 || y >= 16 {
        return b'#';
    }
    MAP[y as usize][x as usize]
}

fn absf(x: f32) -> f32 {
    if x < 0.0 {
        -x
    } else {
        x
    }
}

/// sin and cos of a small angle (one frame's turn is well under 0.5 rad)
fn small_sin_cos(a: f32) -> (f32, f32) {
    let a2 = a * a;
    let sin = a * (1.0 - a2 / 6.0 * (1.0 - a2 / 20.0));
    let cos = 1.0 - a2 / 2.0 * (1.0 - a2 / 12.0);
    (sin, cos)
}

/// 1/sqrt(x) for x near 1 (renormalising the direction after a turn)
fn inv_sqrt_near_one(x: f32) -> f32 {
    let mut y = 1.0;
    for _ in 0..3 {
        y *= 1.5 - 0.5 * x * y * y;
    }
    y
}

fn shade(rgb: (u8, u8, u8), factor: f32) -> Color {
    let f = if factor > 1.0 { 1.0 } else { factor };
    Color::rgb(
        (rgb.0 as f32 * f) as u8,
        (rgb.1 as f32 * f) as u8,
        (rgb.2 as f32 * f) as u8,
    )
}

#[derive(Clone, Copy, PartialEq)]
enum Input {
    None,
    Left,
    Right,
    Forward,
    Back,
}

pub struct Game {
    active: bool,
    // tap counting while inactive
    taps: u8,
    touch_start: Option<Instant>,
    last_tap: Option<Instant>,
    // player
    x: f32,
    y: f32,
    dir_x: f32,
    dir_y: f32,
    input: Input,
    last_frame: Option<Instant>,
    won_at: Option<Instant>,
}

impl Game {
    pub fn new() -> Self {
        Self {
            active: false,
            taps: 0,
            touch_start: None,
            last_tap: None,
            x: 1.5,
            y: 1.5,
            dir_x: 1.0,
            dir_y: 0.0,
            input: Input::None,
            last_frame: None,
            won_at: None,
        }
    }

    pub fn is_active(&self) -> bool {
        self.active
    }

    fn reset_player(&mut self) {
        self.x = 1.5;
        self.y = 1.5;
        self.dir_x = 1.0;
        self.dir_y = 0.0;
        self.input = Input::None;
        self.won_at = None;
    }

    fn start(&mut self, ctx: &mut EventCtx) {
        self.active = true;
        self.taps = 0;
        self.reset_player();
        self.last_frame = Some(Instant::now());
        ctx.request_anim_frame();
        ctx.request_paint();
    }

    fn stop(&mut self, ctx: &mut EventCtx) {
        self.active = false;
        self.input = Input::None;
        ctx.request_paint();
    }

    /// Counts taps while the home screen runs; returns true when the event
    /// belongs to the game (it is running, or this tap started it).
    pub fn event(&mut self, ctx: &mut EventCtx, event: Event) -> bool {
        if !self.active {
            self.count_taps(ctx, event)
        } else {
            self.play(ctx, event);
            true
        }
    }

    fn count_taps(&mut self, ctx: &mut EventCtx, event: Event) -> bool {
        match event {
            Event::Touch(TouchEvent::TouchStart(_)) => {
                self.touch_start = Some(Instant::now());
            }
            Event::Touch(TouchEvent::TouchEnd(_)) => {
                let now = Instant::now();
                let short = self
                    .touch_start
                    .take()
                    .is_some_and(|t| now.saturating_duration_since(t).to_millis() <= TAP_MAX_MS);
                if !short {
                    self.taps = 0;
                    return false;
                }
                let follows = self
                    .last_tap
                    .is_some_and(|t| now.saturating_duration_since(t).to_millis() <= TAP_GAP_MAX_MS);
                self.taps = if follows { self.taps + 1 } else { 1 };
                self.last_tap = Some(now);
                if self.taps >= TAPS_TO_START {
                    self.start(ctx);
                    return true;
                }
            }
            _ => {}
        }
        false
    }

    fn input_at(p: Point) -> Input {
        let third = WIDTH / 3;
        if p.x < third {
            Input::Left
        } else if p.x >= WIDTH - third {
            Input::Right
        } else if p.y < VIEW_H / 2 {
            Input::Forward
        } else {
            Input::Back
        }
    }

    fn play(&mut self, ctx: &mut EventCtx, event: Event) {
        match event {
            Event::Touch(TouchEvent::TouchStart(p)) => {
                if p.x < EXIT_SIZE && p.y < EXIT_SIZE {
                    self.stop(ctx);
                    return;
                }
                self.input = Self::input_at(p);
            }
            Event::Touch(TouchEvent::TouchMove(p)) => {
                self.input = Self::input_at(p);
            }
            Event::Touch(TouchEvent::TouchEnd(_)) => {
                self.input = Input::None;
            }
            _ if EventCtx::is_anim_frame(event) => {
                self.step();
                ctx.request_paint();
                ctx.request_anim_frame();
            }
            _ => {}
        }
    }

    fn free(x: f32, y: f32) -> bool {
        let solid = |dx: f32, dy: f32| {
            let c = cell((x + dx) as i32, (y + dy) as i32);
            c == b'#' || c == b'o'
        };
        !(solid(-RADIUS, -RADIUS)
            || solid(RADIUS, -RADIUS)
            || solid(-RADIUS, RADIUS)
            || solid(RADIUS, RADIUS))
    }

    fn step(&mut self) {
        let now = Instant::now();
        let dt_ms = self
            .last_frame
            .map_or(0, |t| now.saturating_duration_since(t).to_millis())
            .min(100);
        self.last_frame = Some(now);
        let dt = dt_ms as f32 / 1000.0;

        if let Some(t) = self.won_at {
            if now.saturating_duration_since(t).to_millis() > 2500 {
                self.reset_player();
            }
            return;
        }

        match self.input {
            Input::Left | Input::Right => {
                let a = if self.input == Input::Left { -TURN_SPEED } else { TURN_SPEED } * dt;
                let (s, c) = small_sin_cos(a);
                let (dx, dy) = (self.dir_x * c - self.dir_y * s, self.dir_x * s + self.dir_y * c);
                let n = inv_sqrt_near_one(dx * dx + dy * dy);
                self.dir_x = dx * n;
                self.dir_y = dy * n;
            }
            Input::Forward | Input::Back => {
                let v = if self.input == Input::Forward { MOVE_SPEED } else { -MOVE_SPEED } * dt;
                let nx = self.x + self.dir_x * v;
                let ny = self.y + self.dir_y * v;
                // slide along walls: each axis on its own
                if Self::free(nx, self.y) {
                    self.x = nx;
                }
                if Self::free(self.x, ny) {
                    self.y = ny;
                }
            }
            Input::None => {}
        }

        if cell(self.x as i32, self.y as i32) == b'X' {
            self.won_at = Some(now);
        }
    }

    pub fn render<'s>(&'s self, target: &mut impl Renderer<'s>) {
        // ceiling and floor, banded to fake depth
        const BANDS: i16 = 8;
        let half = VIEW_H / 2;
        for i in 0..BANDS {
            let y0 = half * i / BANDS;
            let y1 = half * (i + 1) / BANDS;
            let k = 0.08 + 0.10 * (BANDS - i) as f32 / BANDS as f32;
            shape::Bar::new(Rect::new(Point::new(0, y0), Point::new(WIDTH, y1)))
                .with_bg(shade(LIGHT_PURPLE, k))
                .render(target);
            let k = 0.06 + 0.22 * (i + 1) as f32 / BANDS as f32;
            shape::Bar::new(Rect::new(
                Point::new(0, half + y0),
                Point::new(WIDTH, half + y1),
            ))
            .with_bg(shade((0x40, 0x40, 0x48), k * 2.0))
            .render(target);
        }

        // walls: one ray per 2 px slice (DDA through the grid)
        let (plane_x, plane_y) = (-self.dir_y * FOV_PLANE, self.dir_x * FOV_PLANE);
        for col in 0..COLS {
            let cam = 2.0 * (col as f32 + 0.5) / COLS as f32 - 1.0;
            let rx = self.dir_x + plane_x * cam;
            let ry = self.dir_y + plane_y * cam;
            let (mut mx, mut my) = (self.x as i32, self.y as i32);
            let ddx = if rx == 0.0 { 1e30 } else { absf(1.0 / rx) };
            let ddy = if ry == 0.0 { 1e30 } else { absf(1.0 / ry) };
            let (step_x, mut side_x) = if rx < 0.0 {
                (-1, (self.x - mx as f32) * ddx)
            } else {
                (1, (mx as f32 + 1.0 - self.x) * ddx)
            };
            let (step_y, mut side_y) = if ry < 0.0 {
                (-1, (self.y - my as f32) * ddy)
            } else {
                (1, (my as f32 + 1.0 - self.y) * ddy)
            };
            let mut ns_side = false;
            let mut hit = b'#';
            for _ in 0..48 {
                if side_x < side_y {
                    side_x += ddx;
                    mx += step_x;
                    ns_side = false;
                } else {
                    side_y += ddy;
                    my += step_y;
                    ns_side = true;
                }
                hit = cell(mx, my);
                if hit != b'.' {
                    break;
                }
            }
            let dist = if ns_side { side_y - ddy } else { side_x - ddx };
            let dist = if dist < 0.05 { 0.05 } else { dist };
            let h = ((VIEW_H as f32 / dist) as i32).min(VIEW_H as i32 * 4) as i16;
            let top = (half - h / 2).max(0);
            let bottom = (half + h / 2).min(VIEW_H);
            let base = match hit {
                b'X' => GREEN,
                b'o' => BLUE,
                _ => {
                    if ns_side {
                        PURPLE
                    } else {
                        LIGHT_PURPLE
                    }
                }
            };
            let light = 1.0 / (1.0 + dist * dist * 0.04);
            let light = if ns_side { light * 0.75 } else { light };
            shape::Bar::new(Rect::new(
                Point::new(col * 2, top),
                Point::new(col * 2 + 2, bottom),
            ))
            .with_bg(shade(base, light))
            .render(target);
        }

        // crosshair
        let c = Point::new(WIDTH / 2, half);
        let cross = Color::rgb(GREEN.0, GREEN.1, GREEN.2);
        shape::Bar::new(Rect::new(c - Offset::new(6, 0), c + Offset::new(7, 1)))
            .with_bg(cross)
            .render(target);
        shape::Bar::new(Rect::new(c - Offset::new(0, 6), c + Offset::new(1, 7)))
            .with_bg(cross)
            .render(target);

        // the "weapon": the Ambire flame rising from the bottom of the view
        shape::ToifImage::new(Point::new(WIDTH / 2, VIEW_H + 70), theme::ICON_LOGO.toif)
            .with_align(Alignment2D::BOTTOM_CENTER)
            .with_fg(Color::rgb(LIGHT_PURPLE.0, LIGHT_PURPLE.1, LIGHT_PURPLE.2))
            .render(target);

        // bottom bar
        shape::Bar::new(Rect::new(Point::new(0, VIEW_H), Point::new(WIDTH, HEIGHT)))
            .with_bg(Color::rgb(0, 0, 0))
            .render(target);
        let bar_text = if self.won_at.is_some() {
            "YOU ESCAPED"
        } else {
            "turn  |  walk  |  turn"
        };
        shape::Text::new(Point::new(WIDTH / 2, HEIGHT - 12), bar_text, fonts::FONT_SUB)
            .with_align(Alignment::Center)
            .with_fg(Color::rgb(GREEN.0, GREEN.1, GREEN.2))
            .render(target);

        // exit corner
        shape::Bar::new(Rect::new(Point::new(6, 6), Point::new(EXIT_SIZE - 6, EXIT_SIZE - 6)))
            .with_bg(Color::rgb(0, 0, 0))
            .with_fg(Color::rgb(GREEN.0, GREEN.1, GREEN.2))
            .with_thickness(2)
            .render(target);
        shape::Text::new(Point::new(EXIT_SIZE / 2, EXIT_SIZE / 2 + 7), "x", fonts::FONT_DEMIBOLD)
            .with_align(Alignment::Center)
            .with_fg(Color::rgb(GREEN.0, GREEN.1, GREEN.2))
            .render(target);
    }
}
