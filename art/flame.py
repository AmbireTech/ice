"""The Ambire flame alone (no wordmark), from the official logo, and where it sits on the back.

One source for the board's silkscreen (scripts/silkFlame.py, run by KiCad's Python) and
the concept page's 3D preview (enclosure/generate.py). Pure Python: KiCad's bundled
interpreter has no numpy.

The flame is the Purple and Violet facets of art/brand/Ambire-ver-on-dark.svg (copied
unmodified from github.com/AmbireTech/ambire-brand). It spans the back: as wide as the
board less MARGIN a side, centred, printed in one ink with a GAP of bare board between
facets so the faceting still shows.
"""
import re
from pathlib import Path

LOGO = Path(__file__).resolve().parent / "brand/Ambire-ver-on-dark.svg"
FACETS = {"#6000FF", "#AF50FF"}
MARGIN = 1.2   # mm to the board edge, each side
GAP = 0.35     # mm of bare board between facets (JLC's thinnest silkscreen line is 0.15)
CURVE_STEPS = 16


def facet_paths(svg: Path = LOGO) -> list:
    """The SVG path strings of the flame's facets, in drawing order."""
    out = []
    for tag in re.findall(r"<path\b[^>]*>", svg.read_text(encoding="utf-8")):
        fill = re.search(r'fill="(#[0-9A-Fa-f]{6})"', tag)
        if fill and fill.group(1).upper() in FACETS:
            out.append(re.search(r'\sd="([^"]+)"', tag).group(1))
    if not out:
        raise SystemExit(f"no flame facets in {svg}")
    return out


def path_polygons(d: str) -> list:
    """Closed polygons of one SVG path (M/L/H/V/C/S/Q/Z, absolute or relative; curves sampled)."""
    toks = re.findall(r"[MmLlHhVvCcSsQqZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    polys, cur_poly = [], []
    cx = cy = sx = sy = 0.0
    i, cmd = 0, None
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if cur_poly:
                    polys.append(cur_poly)
                cur_poly, cx, cy = [], sx, sy
                continue
        rel = cmd.islower()
        c = cmd.upper()
        n = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4}[c]
        a = [float(t) for t in toks[i:i + n]]
        i += n
        if c == "H":
            nx, ny = a[0] + (cx if rel else 0), cy
        elif c == "V":
            nx, ny = cx, a[0] + (cy if rel else 0)
        else:
            pts = [(a[k] + (cx if rel else 0), a[k + 1] + (cy if rel else 0)) for k in range(0, n, 2)]
            if c in "CSQ":
                ctrl = [(cx, cy)] + pts
                for s in range(1, CURVE_STEPS):
                    t = s / CURVE_STEPS
                    q = ctrl
                    while len(q) > 1:   # de Casteljau
                        q = [(q[k][0] * (1 - t) + q[k + 1][0] * t, q[k][1] * (1 - t) + q[k + 1][1] * t) for k in range(len(q) - 1)]
                    cur_poly.append(q[0])
            nx, ny = pts[-1]
        if c == "M":
            if cur_poly:
                polys.append(cur_poly)
            cur_poly, sx, sy = [], nx, ny
            cmd = "l" if cmd == "m" else "L"
        cur_poly.append((nx, ny))
        cx, cy = nx, ny
    if cur_poly:
        polys.append(cur_poly)
    return [p for p in polys if len(p) >= 3]


def facets() -> list:
    """Every facet as polygons in the logo's units: a list (per facet) of polygon lists."""
    return [path_polygons(d) for d in facet_paths()]


def box(polys_per_facet) -> tuple:
    xs = [x for f in polys_per_facet for p in f for x, _ in p]
    ys = [y for f in polys_per_facet for p in f for _, y in p]
    return min(xs), min(ys), max(xs), max(ys)


def placement(board_w: float, board_h: float, fbox) -> tuple:
    """Where the flame sits on the back, board mm (x0, y0, x1, y1): as wide as the board less
    MARGIN a side (or as tall, whichever binds), centred."""
    fx0, fy0, fx1, fy1 = fbox
    k = min((board_w - 2 * MARGIN) / (fx1 - fx0), (board_h - 2 * MARGIN) / (fy1 - fy0))
    w, h = (fx1 - fx0) * k, (fy1 - fy0) * k
    cx, cy = board_w / 2, board_h / 2
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def to_board(pt, fbox, at) -> tuple:
    """A logo point to board mm. The back is read from behind, so the drawing is mirrored
    left to right in KiCad's front-view coordinates."""
    fx0, fy0, fx1, fy1 = fbox
    x0, y0, x1, y1 = at
    k = (x1 - x0) / (fx1 - fx0)
    return x1 - (pt[0] - fx0) * k, y0 + (pt[1] - fy0) * k
