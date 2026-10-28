#!/usr/bin/env python3
"""Build the full-colour UV print for the back (B.*) face of the Ambire Ice board:
the Ambire flame in Purple (art/flame.py, the same flame scripts/silkFlame.py puts in the
silkscreen) over the base of one of the purple schemes, with the functional labels.

Everything placement-dependent is re-derived from the CURRENT board on every run:
  * board outline + slots    <- Edge.Cuts in the .kicad_pcb
  * no-ink areas             <- B.Mask openings (kicad-cli SVG) + every drill (kicad-cli Excellon) + the CAMERA window
  * camera / flex tails      <- labelled Dwgs.User rectangles ("CAMERA", "TFT TAIL", "TOUCH TAIL")
  * label positions          <- the board-level B.SilkS texts "AIRGAP", "RADIO", ...
The board file is only read; previews render a patched temporary copy.

Requires: kicad-cli (KiCad 10), rsvg-convert, Python 3 with Pillow + numpy.
Run:  python3 build_uv.py                  (1200 DPI print files + previews, purple on neon)
      python3 build_uv.py --scheme black   (purple on black; or white)
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
from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import flame  # noqa: E402  (art/flame.py)

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

# Board-level B.SilkS texts the print replaces with restyled versions
# (production/export_fab.py --colour leaves them, and the silkscreen flame, out of the Gerbers).
RESTYLED_LABELS = ("AIRGAP", "RADIO", "SECURE ELEMENT", "QR CAMERA", "ANT")
FLAME_GROUP = "FLAME"  # scripts/silkFlame.py's group: the white silkscreen flame


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
    silk_texts: dict[str, tuple[float, float, float, float]]  # B.SilkS board text -> (x, y, size, rotation)
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
            silk[n[1]] = (float(at[1]), float(at[2]), size, float(at[3]) if len(at) > 3 else 0.0)
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
    for need in ("CAMERA",):
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

# The purple schemes of docs/direction.md "Board print": (base ink under the flame or None for
# bare white mask, label colour). The flame itself is always Ambire Purple.
SCHEMES = {
    "neon": (C["volt"], C["black"]),
    "black": (C["black"], C["white"]),
    "white": (None, C["black"]),
}
LABEL_CLEAR = 0.5  # mm of flame-free base around each label (scripts/silkFlame.py does the same)


def flame_raster(g: Grid) -> np.ndarray:
    """The flame (art/flame.py, the geometry scripts/silkFlame.py puts in the silkscreen), front
    view: the facets filled, minus a flame.GAP-wide band along every facet outline."""
    facets = flame.facets()
    fbox = flame.box(facets)
    at = flame.placement(g.wmm, g.hmm, fbox)
    m = Image.new("L", (g.w, g.h), 0)
    d = ImageDraw.Draw(m)
    polys = [[g.px_front(g.x0 + x, g.y0 + y) for x, y in (flame.to_board(pt, fbox, at) for pt in p)]
             for f in facets for p in f]
    for p in polys:
        d.polygon(p, fill=255)
    for p in polys:
        d.line(p + p[:1], fill=0, width=max(1, round(flame.GAP * g.ppm)), joint="curve")
    return np.array(m) > 0


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


def place_labels(board: Board, g: Grid, color: str) -> list:
    """The functional labels in Poppins, where the owner put the plain B.SilkS text: same
    centre, cap height and angle. Returns (centre px, image) per label, back view."""
    placed = []
    for label in RESTYLED_LABELS:
        if label not in board.silk_texts:
            continue
        x, y, size, rot = board.silk_texts[label]
        lab = label_image(label, size, g, color)
        if rot:  # a mirrored bottom text turned +rot in KiCad reads turned -rot from behind
            lab = lab.rotate(-rot, expand=True)
        placed.append((g.back_x(x) * g.ppm, (y - g.y0) * g.ppm, lab))
    return placed


def build_art(scheme: str, board: Board, g: Grid) -> Image.Image:
    base, label_col = SCHEMES[scheme]
    ink = flame_raster(g)[:, ::-1]  # back view
    placed = place_labels(board, g, label_col)
    # Each label sits on an island of plain base, LABEL_CLEAR around its ink box.
    island = Image.new("L", (g.w, g.h), 0)
    di = ImageDraw.Draw(island)
    pad = LABEL_CLEAR * g.ppm
    for bx, by, lab in placed:
        di.rectangle([bx - lab.width / 2 - pad, by - lab.height / 2 - pad, bx + lab.width / 2 + pad, by + lab.height / 2 + pad], fill=255)
    ink &= ~(np.array(island) > 0)
    a = np.zeros((g.h, g.w, 4), np.uint8)
    if base:
        a[...] = (*(rgb(base) * 255 + 0.5).astype(np.uint8), 255)
    a[ink] = (*(rgb(C["purple"]) * 255 + 0.5).astype(np.uint8), 255)
    img = Image.fromarray(a)
    for bx, by, lab in placed:
        img.alpha_composite(lab, (int(round(bx - lab.width / 2)), int(round(by - lab.height / 2))))
    return img


# --------------------------------------------------------------------------- previews


def keyed_board_copy(board_path: Path, tmp: Path) -> Path:
    """Temporary copy: magenta B.Mask / cyan B.SilkS keys, absolute 3D paths, replaced labels and the
    silkscreen flame removed."""
    text = board_path.read_text()
    text = text.replace("${KIPRJMOD}", str(board_path.parent))
    stack = ('(setup\n\t\t(stackup\n'
             '\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (color "#FF00FFFF") (thickness 0.01))\n'
             '\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen") (color "#00FFFFFF"))\n\t\t)')
    text = text.replace("(setup", stack, 1)
    for label in RESTYLED_LABELS:  # drop the plain silk labels the print replaces
        text = re.sub(r'\n\t\(gr_text "' + re.escape(label) + r'"\n(?:\t\t.*\n)*?\t\)', "", text)
    group = re.search(r'\n\t\(group "' + FLAME_GROUP + r'"\n(?:\t\t.*\n)*?\t\)', text)
    if group:  # and the white silkscreen flame the print replaces
        for uuid in re.findall(r'"([0-9a-f-]{36})"', group[0])[1:]:  # [0] is the group's own uuid
            text = re.sub(r'\n\t\(gr_poly\n(?:\t\t.*\n)*?\t\t\(uuid "' + uuid + r'"\)\n\t\)', "", text)
        text = text.replace(group[0], "")
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


def build(board_path: Path, scheme: str, dpi: int, pad_margin: float, edge_margin: float, tmp: Path):
    board = load_board(board_path)
    g = Grid(board, dpi)
    inside = np.array(board_mask(board, g, edge_margin)) > 0
    # No ink: mask openings (pads, exposed copper, NPTH), every drill, and the camera window.
    keepout_raw = raster_layer(board_path, "B.Mask", g, tmp) | drill_holes(board_path, g, tmp) \
        | zone_raster(board, g, ["CAMERA"])
    keepout = np.array(disk_dilate(Image.fromarray(keepout_raw.astype(np.uint8) * 255), pad_margin * g.ppm)) > 0
    allowed = (inside & ~keepout)[:, ::-1]  # back view
    art = build_art(scheme, board, g)
    a = np.array(art)
    a[..., 3] = np.where(allowed, a[..., 3], 0)
    # Self-check: no ink on any raw opening/hole/window, nothing outside the inset outline.
    leak = (a[..., 3] > 0) & (keepout_raw[:, ::-1] | ~inside[:, ::-1])
    if leak.any():
        raise RuntimeError(f"ink lands on {int(leak.sum())} forbidden pixels")
    return board, g, Image.fromarray(a), keepout_raw[:, ::-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    ap.add_argument("--scheme", choices=list(SCHEMES), default="neon", help="purple on neon, black or white (docs/direction.md)")
    ap.add_argument("--dpi", type=int, default=1200, help="print raster resolution (JLC's UV printer: up to 1200 DPI)")
    ap.add_argument("--pad-margin", type=float, default=0.10, help="mm of bare mask kept around every opening/hole")
    ap.add_argument("--edge-margin", type=float, default=0.30, help="mm kept clear of the outline and slots")
    ap.add_argument("--schemes", action="store_true", help="also render the comparison sheet of the schemes")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()
    for tool in ("kicad-cli", "rsvg-convert"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on PATH")
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uvart_") as td:
        tmp = Path(td)
        board, g, art, keepout_back = build(args.board, args.scheme, args.dpi, args.pad_margin, args.edge_margin, tmp)
        back = OUT / "uv_back_seen_from_behind.png"
        front = OUT / "uv_back_kicad_front_view.png"
        art.save(back, dpi=(args.dpi, args.dpi))
        ImageOps.mirror(art).save(front, dpi=(args.dpi, args.dpi))
        print(f"{back.name}: purple on {args.scheme}, {art.width}x{art.height}px @ {args.dpi} DPI = {g.wmm:.2f} x {g.hmm:.2f} mm")
        print(f"{front.name}: mirrored copy in KiCad (front-view) coordinates")
        # Check sheet: art over white mask, raw no-ink areas in red (red must never touch colour).
        chk = Image.new("RGBA", art.size, (242, 242, 238, 255))
        chk.alpha_composite(art)
        red = np.zeros((g.h, g.w, 4), np.uint8)
        red[keepout_back] = (255, 0, 0, 255)
        chk.alpha_composite(Image.fromarray(red))
        chk.convert("RGB").resize((g.w // 2, g.h // 2), Image.LANCZOS).save(OUT / "check_no_ink.png")
        if args.no_preview and not args.schemes:
            return
        keyed = render_keyed(args.board, tmp)
        if not args.no_preview:
            composite(keyed, art, board, assembled=False).save(HERE / "preview_back.png")
            composite(keyed, art, board, assembled=True).save(HERE / "preview_back_assembled.png")
            print("preview_back.png, preview_back_assembled.png")
        if args.schemes:
            tiles = []
            for name in SCHEMES:
                _, _, a, _ = build(args.board, name, 300, args.pad_margin, args.edge_margin, tmp)
                t = composite(keyed, a, board, assembled=True)
                t = t.resize((460, round(460 * t.height / t.width)), Image.LANCZOS)
                tiles.append((name, t))
            th = tiles[0][1].height
            sheet = Image.new("RGB", (len(tiles) * 480 + 20, th + 70), (246, 246, 248))
            font = ImageFont.truetype(str(BRAND / "Poppins-SemiBold.ttf"), 26)
            d = ImageDraw.Draw(sheet)
            for i, (name, t) in enumerate(tiles):
                sheet.paste(t, (20 + i * 480, 56))
                d.text((20 + i * 480, 14), f"purple on {name}", font=font, fill=C["black"])
            sheet.save(HERE / "schemes.png")
            print("schemes.png")


if __name__ == "__main__":
    main()
