#!/usr/bin/env python3
"""Build the full-colour UV artwork for the back (B.*) face of the Ambire signer board.

Everything placement-dependent is re-derived from the CURRENT board on every run:
  * board outline + slots           <- Edge.Cuts in the .kicad_pcb
  * no-ink areas                    <- B.Mask openings (kicad-cli SVG) + every drill (kicad-cli Excellon)
  * logo zone / camera / flex tails <- labelled Dwgs.User rectangles ("UV ARTWORK", "CAMERA", "TFT TAIL", "TOUCH TAIL")
  * label positions                 <- the board-level B.SilkS texts "AIRGAP", "RADIO", ...
The board file is only read; previews render a patched temporary copy.

Requires: kicad-cli (KiCad 10), rsvg-convert, Python 3 with Pillow + numpy.
Run:  python3 build_uv.py            (full 1200 DPI print files + previews)
      python3 build_uv.py --directions   (also the 3-direction comparison sheet)
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
DEFAULT_BOARD = HERE.parent / "board" / "wallet.kicad_pcb"
BRAND = HERE / "brand"
OUT = HERE / "out"

# Ambire Brand Basics v2.0 (Aug 2025), pp. 49 and 51.
C = {
    "purple": "#6000FF",  # Ambire Purple (primary)
    "white": "#DCE2F2",   # Ambire White (primary)
    "black": "#181A1F",   # Ambire Black (primary)
    "violet": "#AF50FF",  # Ambire Violet (secondary)
    "volt": "#D7FF00",    # Ambire Volt (secondary)
    "blue": "#3871FF",    # Ambire Signal Blue (secondary)
    "heat": "#FE4648",    # Ambire Heat (secondary)
}

# Board-level B.SilkS texts the artwork replaces with restyled versions.
RESTYLED_LABELS = ("AIRGAP", "RADIO", "SECURE ELEMENT", "QR CAMERA")
# Board-level B.SilkS texts the artwork replaces outright (the logo takes their place).
REPLACED_TEXTS = RESTYLED_LABELS + ("AMBIRE",)


def rgb(hexstr: str) -> np.ndarray:
    h = hexstr.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


def run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"command failed ({res.returncode}): {' '.join(cmd)}\n{res.stdout}\n{res.stderr}")


# --------------------------------------------------------------------------- board parsing

TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


def parse_sexpr(text: str) -> list:
    stack: list[list] = [[]]
    for tok in TOKEN.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            node = stack.pop()
            stack[-1].append(node)
        elif tok.startswith('"'):
            stack[-1].append(tok[1:-1].replace('\\"', '"'))
        else:
            stack[-1].append(tok)
    return stack[0][0]


def child(node: list, name: str):
    for c in node[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def xy(node: list, name: str) -> tuple[float, float]:
    c = child(node, name)
    return float(c[1]), float(c[2])


@dataclass
class Board:
    loops: list[list[tuple[float, float]]]  # closed polylines, KiCad mm (front view)
    bbox: tuple[float, float, float, float]  # minx, miny, maxx, maxy
    zones: dict[str, tuple[float, float, float, float]]  # Dwgs.User rect label -> (x0, y0, x1, y1)
    silk_texts: dict[str, tuple[float, float, float]]  # B.SilkS board text -> (x, y, size)
    sexpr: list = field(repr=False, default_factory=list)


def arc_points(s, m, e, n=48):
    (x1, y1), (x2, y2), (x3, y3) = s, m, e
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(p[1] - uy, p[0] - ux) for p in (s, m, e))
    sweep = (a3 - a1) % (2 * math.pi)
    if (a2 - a1) % (2 * math.pi) > sweep:  # mid not on the ccw path -> go the other way
        sweep -= 2 * math.pi
    return [(ux + r * math.cos(a1 + sweep * i / n), uy + r * math.sin(a1 + sweep * i / n)) for i in range(n + 1)]


def chain_segments(segs: list[list[tuple[float, float]]], tol=0.02) -> list[list[tuple[float, float]]]:
    segs = [list(s) for s in segs]
    loops = []
    close = lambda a, b: abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol
    while segs:
        loop = segs.pop(0)
        while not close(loop[0], loop[-1]):
            for i, s in enumerate(segs):
                if close(s[0], loop[-1]):
                    loop += s[1:]
                elif close(s[-1], loop[-1]):
                    loop += s[::-1][1:]
                else:
                    continue
                segs.pop(i)
                break
            else:
                raise RuntimeError(f"Edge.Cuts outline is not closed near {loop[-1]}")
        loops.append(loop)
    return loops


def poly_area(p):
    return 0.5 * abs(sum(p[i][0] * p[i - 1][1] - p[i - 1][0] * p[i][1] for i in range(len(p))))


def load_board(path: Path) -> Board:
    root = parse_sexpr(path.read_text())
    segs, closed = [], []
    rects, dw_texts, silk = [], [], {}
    for n in root[1:]:
        if not isinstance(n, list):
            continue
        layer = child(n, "layer")
        layer = layer[1] if layer else None
        if layer == "Edge.Cuts":
            if n[0] == "gr_line":
                segs.append([xy(n, "start"), xy(n, "end")])
            elif n[0] == "gr_arc":
                segs.append(arc_points(xy(n, "start"), xy(n, "mid"), xy(n, "end")))
            elif n[0] == "gr_rect":
                (x0, y0), (x1, y1) = xy(n, "start"), xy(n, "end")
                closed.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])
            elif n[0] == "gr_circle":
                (cx, cy), (ex, ey) = xy(n, "center"), xy(n, "end")
                r = math.hypot(ex - cx, ey - cy)
                closed.append([(cx + r * math.cos(t / 32 * math.pi), cy + r * math.sin(t / 32 * math.pi)) for t in range(65)])
            elif n[0] == "gr_poly":
                pts = [(float(p[1]), float(p[2])) for p in child(n, "pts")[1:] if p[0] == "xy"]
                closed.append(pts + pts[:1])
            else:
                raise RuntimeError(f"unhandled Edge.Cuts item {n[0]}")
        elif layer == "Dwgs.User" and n[0] == "gr_rect":
            rects.append((*xy(n, "start"), *xy(n, "end")))
        elif layer == "Dwgs.User" and n[0] == "gr_text":
            dw_texts.append((n[1], *xy(n, "at")[:2]))
        elif layer == "B.SilkS" and n[0] == "gr_text":
            at = child(n, "at")
            size = float(child(child(child(n, "effects"), "font"), "size")[1])
            silk[n[1]] = (float(at[1]), float(at[2]), size)
    loops = chain_segments(segs) + closed
    loops.sort(key=poly_area, reverse=True)
    xs = [p[0] for p in loops[0]]
    ys = [p[1] for p in loops[0]]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    # A Dwgs.User rectangle is named by the smallest rectangle containing a Dwgs.User text.
    zones = {}
    for label, tx, ty in dw_texts:
        inside = [r for r in rects if min(r[0], r[2]) <= tx <= max(r[0], r[2]) and min(r[1], r[3]) <= ty <= max(r[1], r[3])]
        if inside:
            r = min(inside, key=lambda r: abs((r[2] - r[0]) * (r[3] - r[1])))
            zones[label] = (min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3]))
    for need in ("UV ARTWORK", "CAMERA"):
        if need not in zones:
            raise RuntimeError(f'Dwgs.User rectangle labelled "{need}" not found on the board')
    return Board(loops, bbox, zones, silk, root)


# --------------------------------------------------------------------------- geometry rasters


class Grid:
    """Pixel grid over the board bbox. Front = KiCad view; back = mirrored (as seen from behind)."""

    def __init__(self, board: Board, dpi: int):
        self.dpi = dpi
        self.ppm = dpi / 25.4
        self.x0, self.y0, x1, y1 = board.bbox
        self.wmm, self.hmm = x1 - self.x0, y1 - self.y0
        self.w = int(round(self.wmm * self.ppm))
        self.h = int(round(self.hmm * self.ppm))

    def px_front(self, x, y):
        return ((x - self.x0) * self.ppm, (y - self.y0) * self.ppm)

    def back_x(self, x):
        """KiCad x -> mm from the left edge as seen from behind."""
        return self.wmm - (x - self.x0)

    def back_rect(self, r):
        x0, y0, x1, y1 = r
        return (self.back_x(x1), y0 - self.y0, self.back_x(x0), y1 - self.y0)

    def mm_grid(self):
        xs = (np.arange(self.w) + 0.5) / self.ppm
        ys = (np.arange(self.h) + 0.5) / self.ppm
        return np.meshgrid(xs, ys)


def disk_dilate(mask: Image.Image, radius_px: float) -> Image.Image:
    """Binary dilation by a square of half-width ceil(radius_px): a superset of the disk, so always safe."""
    k = int(math.ceil(radius_px))
    m = np.array(mask) > 0
    if k <= 0:
        return mask
    for axis in (0, 1):
        c = np.cumsum(np.pad(m, [(k + 1, k) if a == axis else (0, 0) for a in (0, 1)]).astype(np.int32), axis=axis)
        hi = np.take(c, np.arange(2 * k + 1, c.shape[axis]), axis=axis)
        lo = np.take(c, np.arange(0, c.shape[axis] - 2 * k - 1), axis=axis)
        m = (hi - lo) > 0
    return Image.fromarray((m * 255).astype(np.uint8))


def board_mask(board: Board, g: Grid, inset_mm: float) -> Image.Image:
    """255 inside the board outline minus cutouts, eroded by inset_mm (front view)."""
    m = Image.new("L", (g.w, g.h), 0)
    d = ImageDraw.Draw(m)
    d.polygon([g.px_front(*p) for p in board.loops[0]], fill=255)
    for loop in board.loops[1:]:
        d.polygon([g.px_front(*p) for p in loop], fill=0)
    edge = Image.new("L", (g.w, g.h), 0)
    de = ImageDraw.Draw(edge)
    for loop in board.loops:
        de.line([g.px_front(*p) for p in loop], fill=255, width=1)
    edge = disk_dilate(edge, inset_mm * g.ppm)
    return Image.fromarray(np.where(np.array(edge) > 0, 0, np.array(m)).astype(np.uint8))


def raster_layer(board_path: Path, layer: str, g: Grid, tmp: Path, margin_mm: float = 0.0) -> np.ndarray:
    """Boolean raster (front view) of everything kicad-cli plots on one layer.
    margin_mm widens the window on every side (the result is then larger than the grid)."""
    mp = int(round(margin_mm * g.ppm))
    W, H = g.w + 2 * mp, g.h + 2 * mp
    m = mp / g.ppm
    svg = tmp / f"{layer}.svg"
    run(["kicad-cli", "pcb", "export", "svg", "--layers", layer, "--mode-single",
         "--exclude-drawing-sheet", "--black-and-white", "-o", str(svg), str(board_path)])
    text = svg.read_text()
    # Page mode: SVG user units are KiCad mm. Re-window the root element onto the board bbox.
    text, n = re.subn(r'width="[^"]*"\s+height="[^"]*"\s+viewBox="[^"]*"',
                      f'width="{W}" height="{H}" viewBox="{g.x0 - m} {g.y0 - m} {W / g.ppm} {H / g.ppm}"', text, count=1)
    if n != 1:
        raise RuntimeError(f"unexpected SVG header from kicad-cli for {layer}")
    svg.write_text(re.sub(r"(fill|stroke)-opacity:[0-9.]+", r"\1-opacity:1", text))
    png = tmp / f"{layer}.png"
    run(["rsvg-convert", "-w", str(W), "-h", str(H), "-o", str(png), str(svg)])
    return np.array(Image.open(png).convert("RGBA"))[:, :, 3] > 0


def part_bodies(board_path: Path, g: Grid, tmp: Path) -> np.ndarray:
    """Courtyard outlines filled solid. Rendered with a margin so courtyards that overhang
    the board edge (connectors) still close before the flood fill."""
    mp = int(round(6.0 * g.ppm))
    filled = fill_closed(raster_layer(board_path, "B.CrtYd", g, tmp, margin_mm=6.0))
    return filled[mp:mp + g.h, mp:mp + g.w]


def fill_closed(lines: np.ndarray) -> np.ndarray:
    """Outline raster -> outline plus everything it encloses (flood the outside from the border)."""
    padded = Image.fromarray(np.pad(lines, 2).astype(np.uint8) * 255).copy()
    ImageDraw.floodfill(padded, (0, 0), 128)
    return (np.array(padded) != 128)[2:-2, 2:-2]


def drill_holes(board_path: Path, g: Grid, tmp: Path) -> np.ndarray:
    """255 over every drilled hole and slot (front view), from kicad-cli's Excellon output."""
    ddir = Path(tempfile.mkdtemp(prefix="drill_", dir=tmp))
    run(["kicad-cli", "pcb", "export", "drill", "--format", "excellon", "--excellon-units", "mm",
         "--excellon-zeros-format", "decimal", "--excellon-oval-format", "alternate",
         "--drill-origin", "absolute", "-o", str(ddir) + "/", str(board_path)])
    m = Image.new("L", (g.w, g.h), 0)
    d = ImageDraw.Draw(m)
    num = r"(-?[\d.]+)"
    for f in ddir.glob("*.drl"):
        tools, cur = {}, 0.0
        for line in f.read_text().splitlines():
            t = re.fullmatch(r"T(\d+)C([\d.]+)", line)
            if t:
                tools[t[1]] = float(t[2])
                continue
            t = re.fullmatch(r"T(\d+)", line)
            if t:
                cur = tools.get(t[1], 0.0)
                continue
            s = re.fullmatch(f"X{num}Y{num}G85X{num}Y{num}", line)
            h = re.fullmatch(f"X{num}Y{num}", line)
            if s:
                p1 = g.px_front(float(s[1]), -float(s[2]))
                p2 = g.px_front(float(s[3]), -float(s[4]))
                wpx = cur * g.ppm
                d.line([p1, p2], fill=255, width=max(1, int(round(wpx))))
                for p in (p1, p2):
                    d.ellipse([p[0] - wpx / 2, p[1] - wpx / 2, p[0] + wpx / 2, p[1] + wpx / 2], fill=255)
            elif h:
                p = g.px_front(float(h[1]), -float(h[2]))
                r = cur * g.ppm / 2
                d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=255)
    return np.array(m) > 0


# --------------------------------------------------------------------------- artwork (back view, mm)


def colormap(t: np.ndarray, stops: list[tuple[float, str]]) -> np.ndarray:
    pos = np.array([s[0] for s in stops])
    cols = np.array([rgb(s[1]) for s in stops])
    out = np.empty(t.shape + (3,))
    for ch in range(3):
        out[..., ch] = np.interp(t, pos, cols[:, ch])
    return out


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def grain(shape, amount, seed=7):
    return np.random.default_rng(seed).normal(0, amount, shape)[..., None]


def calm_field(X, Y, rect, pad):
    """1 well away from rect, 0 inside it: used to keep the logo's clear space quiet."""
    x0, y0, x1, y1 = rect
    dx = np.maximum(np.maximum(x0 - X, X - x1), 0)
    dy = np.maximum(np.maximum(y0 - Y, Y - y1), 0)
    return smoothstep(0, pad, np.hypot(dx, dy))


@dataclass
class Ctx:
    g: Grid
    X: np.ndarray
    Y: np.ndarray
    logo_zone: tuple  # back-view mm
    cam: tuple        # back-view mm


# Beams leaving the camera window: (angle deg, 90 = straight down / 180 = left as seen from behind,
# half-width deg, strength). Aimed at where bare board stays visible around the parts.
FLARE_BEAMS = [(91, 4.0, 1.00), (103, 2.2, 0.75), (116, 5.5, 0.90), (130, 2.6, 0.70), (146, 6.5, 0.55), (163, 2.8, 0.45)]


def art_flare(c: Ctx) -> np.ndarray:
    """Light enters through the camera window and fans across the board (Ambire 'Flare')."""
    X, Y = c.X, c.Y
    cx, cy = (c.cam[0] + c.cam[2]) / 2, (c.cam[1] + c.cam[3]) / 2
    dx, dy = X - cx, Y - cy
    r = np.hypot(dx, dy)
    th = np.degrees(np.arctan2(dy, dx))
    bend = th - 0.10 * r + 0.0009 * r * r  # rays curve as they travel: liquid, not laser
    reach = (0.35 + 0.65 * np.exp(-r / 45.0)) * smoothstep(5.0, 12.0, r)
    light = np.zeros_like(X)
    fringe = np.zeros_like(X)
    for ang, half, k in FLARE_BEAMS:
        d = (bend - ang) / half
        light += k * np.exp(-d * d)
        fringe += k * np.exp(-((d - 1.1) / 0.45) ** 2)  # Signal Blue on one flank
    calm = calm_field(X, Y, c.logo_zone, 7.0)
    haze = 0.20 * np.exp(-r / 30.0) + 0.10 * smoothstep(40, 80, Y)
    t = np.clip((light * reach + haze) * calm, 0, 1.0)
    col = colormap(t, [(0, C["black"]), (0.16, "#22095A"), (0.38, C["purple"]), (0.62, C["violet"]),
                       (0.84, "#E4F59A"), (1.0, C["volt"])])
    f = np.clip(fringe * reach * calm * 0.55, 0, 0.55)[..., None]
    col = col * (1 - f) + rgb(C["blue"]) * f
    return np.clip(col + grain(X.shape, 0.018), 0, 1)


def art_liquid(c: Ctx) -> np.ndarray:
    """Ambire 'Liquid' background: warped marble in purple / violet with one Volt streak."""
    X, Y = c.X / 48.0, c.Y / 48.0
    u, v = X.copy(), Y.copy()
    for k, (a, f, ph) in enumerate([(0.28, 2.1, 0.3), (0.19, 3.7, 1.9), (0.11, 6.3, 4.2)]):
        u, v = u + a * np.sin(f * v + ph), v + a * np.cos(f * u + ph * 1.3)
    t = 0.5 + 0.5 * np.sin(5.2 * u + 2.4 * v)
    streak = np.exp(-((np.sin(3.1 * u - 1.7 * v + 0.8)) ** 2) / 0.012)
    col = colormap(t, [(0, C["black"]), (0.3, "#2A0C78"), (0.55, C["purple"]), (0.8, C["violet"]), (1, "#E6B8FF")])
    col = col * (1 - 0.85 * streak[..., None]) + rgb(C["volt"]) * 0.85 * streak[..., None]
    calm = 1 - 0.85 * (1 - calm_field(c.X, c.Y, c.logo_zone, 6.0))  # darken behind the logo
    return np.clip(col * calm[..., None] + grain(X.shape, 0.03), 0, 1)


def art_facets(c: Ctx) -> np.ndarray:
    """Ambire 'Modern Bold': Purple field, layered facets at the flame's beam angles."""
    X, Y = c.X, c.Y
    base = colormap(np.clip((X * 0.4 + Y) / 100.0, 0, 1), [(0, C["purple"]), (1, C["blue"])])
    img = base.copy()
    # Facet slopes follow the logo's beams (steep ~ -62 deg and shallow ~ +32 deg).
    facets = [(-1.9, 70, 7.0, 0.32), (-1.9, 98, 10.0, 0.24), (0.62, -2, 9.0, 0.22), (0.62, 30, 6.0, 0.30),
              (-1.9, 126, 5.0, 0.36), (0.62, 54, 12.0, 0.18)]
    for slope, off, width, alpha in facets:
        d = (Y - slope * X - off) / math.hypot(1, slope)
        band = smoothstep(-width / 2 - 0.4, -width / 2, d) * (1 - smoothstep(width / 2, width / 2 + 0.4, d))
        img = img * (1 - alpha * band[..., None]) + rgb(C["violet"]) * alpha * band[..., None]
    return np.clip(img + grain(X.shape, 0.02), 0, 1)


DIRECTIONS = {
    "flare": (art_flare, "Ambire-ver-on-dark.svg", C["volt"]),
    "liquid": (art_liquid, "logo-ver-simplified-white.svg", C["volt"]),
    "facets": (art_facets, "logo-ver-simplified-white.svg", "#FFFFFF"),
}


def render_logo(svg: Path, box_px: tuple[int, int], tmp: Path) -> Image.Image:
    """Official logo file, rasterised, cropped to its ink, scaled uniformly to fit box_px."""
    big = tmp / f"logo_{svg.stem}.png"
    run(["rsvg-convert", "-w", "4000", "-o", str(big), str(svg)])
    im = Image.open(big).convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox())
    s = min(box_px[0] / im.width, box_px[1] / im.height)
    return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)


def label_image(label: str, cap_mm: float, g: Grid, color: str) -> Image.Image:
    """Label in Poppins SemiBold with +8 % tracking, cropped to its ink box."""
    font_px = round(cap_mm / 0.70 * g.ppm)  # Poppins cap height ~ 0.70 em
    font = ImageFont.truetype(str(BRAND / "Poppins-SemiBold.ttf"), font_px)
    spacing = 0.08 * font_px
    widths = [font.getlength(ch) for ch in label]
    im = Image.new("RGBA", (int(sum(widths) + spacing * len(label)) + 8, font_px * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    px = 4.0
    for ch, w in zip(label, widths):
        d.text((px, font_px), ch, font=font, fill=color, anchor="ls")
        px += w + spacing
    return im.crop(im.getchannel("A").getbbox())


def place_labels(img: Image.Image, board: Board, g: Grid, color: str, occupied: np.ndarray,
                 search_mm=6.0, clear_mm=0.25) -> list[str]:
    """Restyled functional labels, as close as possible to where the owner put the plain
    B.SilkS text, on the nearest spot whose box is clear of parts, no-ink areas and the
    zones the flex tails hide (occupied, back view). Tries 0.85 mm caps horizontal, then
    0.80 mm (JLC's colour-text minimum), then vertical. Returns notes for the build log."""
    integ = np.pad(occupied.astype(np.int64).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    cl = int(round(clear_mm * g.ppm))
    step = max(1, int(round(0.1 * g.ppm)))
    rng = int(search_mm * g.ppm)
    notes = []
    for label in RESTYLED_LABELS:
        if label not in board.silk_texts:
            continue
        x, y, _ = board.silk_texts[label]
        ox, oy = g.back_x(x) * g.ppm, (y - g.y0) * g.ppm
        best = None
        for penalty, cap, rot in ((0.0, 0.85, 0), (0.6, 0.80, 0), (2.0, 0.85, 90), (2.4, 0.80, 90)):
            lab = label_image(label, cap, g, color)
            if rot:
                lab = lab.rotate(rot, expand=True)
            hw, hh = lab.width / 2 + cl, lab.height / 2 + cl
            for dy in range(-rng, rng + 1, step):
                for dx in range(-rng, rng + 1, step):
                    score = math.hypot(dx, dy) / g.ppm + penalty
                    if dx * dx + dy * dy > rng * rng or (best and score >= best[0]):
                        continue
                    x0, x1 = int(ox + dx - hw), int(ox + dx + hw)
                    y0, y1 = int(oy + dy - hh), int(oy + dy + hh)
                    if x0 < 0 or y0 < 0 or x1 >= g.w or y1 >= g.h:
                        continue
                    if integ[y1, x1] - integ[y0, x1] - integ[y1, x0] + integ[y0, x0] == 0:
                        best = (score, ox + dx, oy + dy, lab, cap, rot)
        if best is None:
            notes.append(f'label "{label}": no clear spot within {search_mm} mm -- LEFT OUT, keep it as plain silk')
            continue
        _, bx, by, lab, cap, rot = best
        # Quiet the background under the label (the art may run a bright beam through it).
        shade = Image.new("L", img.size, 0)
        pad = 0.45 * g.ppm
        ImageDraw.Draw(shade).rounded_rectangle(
            [bx - lab.width / 2 - pad, by - lab.height / 2 - pad, bx + lab.width / 2 + pad, by + lab.height / 2 + pad],
            radius=pad, fill=210)
        shade = shade.filter(ImageFilter.GaussianBlur(pad / 2))
        dark = Image.new("RGBA", img.size, C["black"])
        dark.putalpha(shade)
        img.alpha_composite(dark)
        img.alpha_composite(lab, (int(round(bx - lab.width / 2)), int(round(by - lab.height / 2))))
        moved = math.hypot(bx - ox, by - oy) / g.ppm
        if moved > 0.05 or cap != 0.85 or rot:
            notes.append(f'label "{label}": {cap} mm caps{", vertical" if rot else ""}, moved {moved:.1f} mm to clear parts')
    return notes


def build_art(direction: str, board: Board, g: Grid, tmp: Path, occupied: np.ndarray) -> tuple[Image.Image, list[str]]:
    fn, logo_file, label_col = DIRECTIONS[direction]
    X, Y = g.mm_grid()
    lz = g.back_rect(board.zones["UV ARTWORK"])
    cam = g.back_rect(board.zones["CAMERA"])
    rgbf = fn(Ctx(g, X, Y, lz, cam))
    img = Image.fromarray((rgbf * 255 + 0.5).astype(np.uint8)).convert("RGBA")
    # Logo: clear space of ~1.3 mm on every side of the reserved zone (brand p.37 clearspace).
    pad = 1.3
    box = ((lz[2] - lz[0] - 2 * pad) * g.ppm, (lz[3] - lz[1] - 2 * pad) * g.ppm)
    logo = render_logo(BRAND / logo_file, (int(box[0]), int(box[1])), tmp)
    cx, cy = (lz[0] + lz[2]) / 2 * g.ppm, (lz[1] + lz[3]) / 2 * g.ppm
    img.alpha_composite(logo, (int(round(cx - logo.width / 2)), int(round(cy - logo.height / 2))))
    notes = place_labels(img, board, g, label_col, occupied)
    return img, notes


# --------------------------------------------------------------------------- previews


def keyed_board_copy(board_path: Path, tmp: Path) -> Path:
    """Temporary copy: magenta B.Mask / cyan B.SilkS keys, absolute 3D paths, replaced texts removed."""
    text = board_path.read_text()
    text = text.replace("${KIPRJMOD}", str(board_path.parent))
    stack = ('(setup\n\t\t(stackup\n'
             '\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (color "#FF00FFFF") (thickness 0.01))\n'
             '\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen") (color "#00FFFFFF"))\n\t\t)')
    text = text.replace("(setup", stack, 1)
    for label in REPLACED_TEXTS:  # drop the plain silk labels the artwork replaces
        text = re.sub(r'\n\t\(gr_text "' + re.escape(label) + r'"\n(?:\t\t.*\n)*?\t\)', "", text)
    out = tmp / "keyed.kicad_pcb"
    out.write_text(text)
    return out


def render_keyed(board_path: Path, tmp: Path, width=1100) -> Path:
    keyed = keyed_board_copy(board_path, tmp)
    png = tmp / "keyed.png"
    run(["kicad-cli", "pcb", "render", "--side", "bottom", "-w", str(width), "-h", str(int(width * 1.72)),
         "--background", "transparent", "--quality", "high", "-o", str(png), str(keyed)])
    return png


def composite(render_png: Path, art: Image.Image, board: Board, assembled: bool) -> Image.Image:
    r = np.array(Image.open(render_png).convert("RGBA")).astype(np.float64) / 255
    R, G, B, A = r[..., 0], r[..., 1], r[..., 2], r[..., 3]
    mx = np.maximum(np.maximum(R, G), B) + 1e-6
    is_mask = (A > 0.5) & (R / mx > 0.55) & (B / mx > 0.55) & (G / mx < 0.45)
    is_silk = (A > 0.5) & (G / mx > 0.7) & (B / mx > 0.7) & (R / mx < 0.45)
    ys, xs = np.nonzero(is_mask)
    px0, px1, py0, py1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    bw, bh = px1 - px0, py1 - py0
    art_s = np.array(art.resize((bw, bh), Image.LANCZOS)).astype(np.float64) / 255
    canvas = np.zeros((r.shape[0], r.shape[1], 3))
    canvas[py0:py1, px0:px1] = art_s[..., :3]
    ink = np.zeros(r.shape[:2])
    ink[py0:py1, px0:px1] = art_s[..., 3]
    shade = np.clip(((R + B) / 2) / np.median(((R + B) / 2)[is_mask]), 0.6, 1.25)[..., None]
    white_mask = np.array([0.95, 0.95, 0.93])
    surf = canvas * ink[..., None] + white_mask * (1 - ink[..., None])
    out = r[..., :3].copy()
    out[is_mask] = (surf * shade)[is_mask]
    out[is_silk] = (np.array([0.93, 0.93, 0.93]) * shade)[is_silk]
    img = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))
    img.putalpha(Image.fromarray((A * 255).astype(np.uint8)))
    if assembled:
        g_ppm = bw / (board.bbox[2] - board.bbox[0])
        to_px = lambda xmm, ymm: (px0 + (board.bbox[2] - xmm) * g_ppm, py0 + (ymm - board.bbox[1]) * g_ppm)
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        for name in ("TFT TAIL", "TOUCH TAIL"):
            if name in board.zones:
                x0, y0, x1, y1 = board.zones[name]
                a, b = to_px(x1, y0), to_px(x0, y1)
                d.rounded_rectangle([a, b], radius=0.6 * g_ppm, fill=(196, 126, 38, 235), outline=(150, 92, 22, 255), width=2)
        x0, y0, x1, y1 = board.zones["CAMERA"]
        a, b = to_px(x1, y0), to_px(x0, y1)
        d.rounded_rectangle([a, b], radius=0.5 * g_ppm, fill=(38, 40, 46, 255))
        cxp, cyp = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        for rr, col in ((2.6, (12, 12, 16, 255)), (1.6, (28, 30, 60, 255)), (0.5, (120, 130, 200, 200))):
            d.ellipse([cxp - rr * g_ppm, cyp - rr * g_ppm, cxp + rr * g_ppm, cyp + rr * g_ppm], fill=col)
        img = Image.alpha_composite(img, ov)
    bg = Image.new("RGBA", img.size, (232, 232, 236, 255))
    return Image.alpha_composite(bg, img).convert("RGB")


# --------------------------------------------------------------------------- main


def zone_raster(board: Board, g: Grid, names, grow_mm=0.0) -> np.ndarray:
    m = Image.new("L", (g.w, g.h), 0)
    d = ImageDraw.Draw(m)
    for n in names:
        if n in board.zones:
            x0, y0, x1, y1 = board.zones[n]
            d.rectangle([g.px_front(x0 - grow_mm, y0 - grow_mm), g.px_front(x1 + grow_mm, y1 + grow_mm)], fill=255)
    return np.array(m) > 0


def build(board_path: Path, direction: str, dpi: int, pad_margin: float, edge_margin: float, tmp: Path):
    board = load_board(board_path)
    g = Grid(board, dpi)
    inside = np.array(board_mask(board, g, edge_margin)) > 0
    # No ink: mask openings (pads, exposed copper, NPTH), every drill, and the camera window.
    keepout_raw = raster_layer(board_path, "B.Mask", g, tmp) | drill_holes(board_path, g, tmp) \
        | zone_raster(board, g, ["CAMERA"])
    keepout = np.array(disk_dilate(Image.fromarray(keepout_raw.astype(np.uint8) * 255), pad_margin * g.ppm)) > 0
    allowed = (inside & ~keepout)[:, ::-1]  # back view
    # Where a label must not sit: part bodies (courtyards), no-ink areas, and areas hidden by the flex tails.
    bodies = part_bodies(board_path, g, tmp)
    occupied = (bodies | keepout | ~inside | zone_raster(board, g, ["TFT TAIL", "TOUCH TAIL"]))[:, ::-1]
    art, notes = build_art(direction, board, g, tmp, occupied)
    a = np.array(art)
    a[..., 3] = np.where(allowed, a[..., 3], 0)
    # Self-check: no ink on any raw opening/hole/window, nothing outside the inset outline.
    leak = (a[..., 3] > 0) & (keepout_raw[:, ::-1] | ~inside[:, ::-1])
    if leak.any():
        raise RuntimeError(f"ink lands on {int(leak.sum())} forbidden pixels")
    return board, g, Image.fromarray(a), notes, keepout_raw[:, ::-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    ap.add_argument("--direction", choices=sorted(DIRECTIONS), default="flare")
    ap.add_argument("--dpi", type=int, default=1200, help="print raster resolution (JLC's UV printer: up to 1200 DPI)")
    ap.add_argument("--pad-margin", type=float, default=0.10, help="mm of bare mask kept around every opening/hole")
    ap.add_argument("--edge-margin", type=float, default=0.30, help="mm kept clear of the outline and slots")
    ap.add_argument("--directions", action="store_true", help="also render the 3-direction comparison sheet")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()
    for tool in ("kicad-cli", "rsvg-convert"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on PATH")
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uvart_") as td:
        tmp = Path(td)
        board, g, art, notes, keepout_back = build(args.board, args.direction, args.dpi, args.pad_margin, args.edge_margin, tmp)
        for n in notes:
            print("note:", n)
        back = OUT / "uv_back_seen_from_behind.png"
        front = OUT / "uv_back_kicad_front_view.png"
        art.save(back, dpi=(args.dpi, args.dpi))
        ImageOps.mirror(art).save(front, dpi=(args.dpi, args.dpi))
        print(f"{back.name}: {art.width}x{art.height}px @ {args.dpi} DPI = {g.wmm:.2f} x {g.hmm:.2f} mm")
        print(f"{front.name}: mirrored copy in KiCad (front-view) coordinates")
        # Check sheet: art over white mask, raw no-ink areas in red (red must never touch colour).
        chk = Image.new("RGBA", art.size, (242, 242, 238, 255))
        chk.alpha_composite(art)
        red = np.zeros((g.h, g.w, 4), np.uint8)
        red[keepout_back] = (255, 0, 0, 255)
        chk.alpha_composite(Image.fromarray(red))
        chk.convert("RGB").resize((g.w // 2, g.h // 2), Image.LANCZOS).save(OUT / "check_no_ink.png")
        if args.no_preview and not args.directions:
            return
        keyed = render_keyed(args.board, tmp)
        if not args.no_preview:
            composite(keyed, art, board, assembled=False).save(HERE / "preview_back.png")
            composite(keyed, art, board, assembled=True).save(HERE / "preview_back_assembled.png")
            print("preview_back.png, preview_back_assembled.png")
        if args.directions:
            tiles = []
            for name in DIRECTIONS:
                _, _, a, _, _ = build(args.board, name, 300, args.pad_margin, args.edge_margin, tmp)
                t = composite(keyed, a, board, assembled=True)
                t = t.resize((460, round(460 * t.height / t.width)), Image.LANCZOS)
                tiles.append((name, t))
            th = tiles[0][1].height
            sheet = Image.new("RGB", (len(tiles) * 480 + 20, th + 70), (246, 246, 248))
            font = ImageFont.truetype(str(BRAND / "Poppins-SemiBold.ttf"), 26)
            d = ImageDraw.Draw(sheet)
            for i, (name, t) in enumerate(tiles):
                sheet.paste(t, (20 + i * 480, 56))
                d.text((20 + i * 480, 14), f"{chr(65 + i)} · {name}", font=font, fill=C["black"])
            sheet.save(HERE / "directions.png")
            print("directions.png")


if __name__ == "__main__":
    main()
