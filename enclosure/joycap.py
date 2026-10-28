#!/usr/bin/env python3
"""The joystick's cap: a printed thumb pad that presses onto the ALPS SKRHAAE010's stem,
and the conical well round it in the bezel's front face.

Model space as in generate.py: mm, X east, Y north, Z toward the screen, Z = 0 at the
board's front face (the switch's mounting face). Board coordinates are y south-positive:
model Y = -y.

The stem (ALPS SKRH series drawing, SKRHAAE010 page 2018-03-21, and the switch's STEP in
3d/SW_ALPS_SKRHAAE010.step, which agree):
  - a 3.0 mm round collar from 2.15 to 3.0 mm, then a square post 1.95 +-0.05 mm with
    R0.5 corners from 3.0 to 5.0 mm, turned 45 deg to the body (its corners point along
    X and Y). The drawing allows a keytop 1.7 mm of the post at most ("keytop fitting area").
  - it pivots 0.49 mm above the mounting face ("center of stem rotation 0.49");
  - 4-direction travel 0.25 mm measured 4.3 mm above the mounting face, i.e. it switches
    at atan(0.25 / (4.3 - 0.49)) = 3.8 deg; centre push 0.15 mm;
  - operating force 1.23 N (4-direction), 2.35 N (push); actuator strength 29.4 N.
  The drawing gives no mechanical tilt limit beyond the operating travel.

The cap: a sleeve with a square socket over the post (crush ribs on its four flats take
up the print's tolerance, resin or filament), flaring at 45 deg into a 8 mm pad with a
shallow dish and a centre dot for the thumb. It nests in a 45 deg countersink in the
bezel's front face, parallel to the flare, so the bezel stops a tilt at about twice the
switching angle instead of the switch's own stem. It prints standing on its sleeve, dish
up, with no supports (every overhang is 45 deg; the socket's roof is a 2 mm bridge).
"""

from __future__ import annotations

import math

import numpy as np
from build123d import (
    Box, Cone, Cylinder, Plane, Polyline, Pos, Rot, RectangleRounded, Sphere, Vector,
                       Axis, extrude, make_face, revolve)

# ── the stem (sources above) ────────────────────────────────────────────────
STEM_TOP = 5.0          # the post's top above the board's front face
POST = 1.95             # square post, across flats (+-0.05)
POST_TOL = 0.05
POST_R = 0.5            # its corner radius
POST_TURN = 45.0        # its flats face the four operating directions (a-d, the diagonals)
FIT_MAX = 1.7           # the keytop may grip this much of the post
PIVOT_Z = 0.49
TILT_SWITCH = math.degrees(math.atan(0.25 / (4.3 - PIVOT_Z)))
PUSH_TRAVEL = 0.15

# ── the cap ─────────────────────────────────────────────────────────────────
SOCKET_DEPTH = 1.6      # inside FIT_MAX
SOCKET = POST + 0.12    # the socket across flats: clear of the post at its largest...
RIB_W, RIB_TO = 0.5, POST - 0.1   # ...but four crush ribs, one per flat, bring it to 1.85: 0.05-0.15 mm of grip
SLEEVE_R = 2.2          # the sleeve inside the bezel's hole
HEAD_R = 4.0            # the pad: 8 mm across
HEAD_BAND = 0.5         # its straight edge above the flare
DISH_DEPTH = 0.35       # the dish in the top, inside RIM
RIM = 0.7               # flat land round the dish
EDGE_R = 0.4            # rounded outer edge
DOT_R, DOT_H = 0.8, 0.15   # a centre dot to find the push by feel
MIN_WALL = 0.8          # two 0.4 mm perimeters

# ── the bezel's well round it ───────────────────────────────────────────────
HOLE_D = 6.0            # generate.py's JOY_HOLE_D: the straight hole through the bezel
WELL_DEPTH = 0.8        # a 45 deg countersink into the front face: 6.0 -> 7.6 mm at the face
GAP = 0.8               # between the cap's flare and the well, square to both
BEZEL_CLR = 0.1         # the least the tilt check allows
STOP_MIN = 1.5          # the bezel may stop the tilt only past this many times the switching angle
STOP_MAX = 3.0          # ...and must stop it before this many (it protects the stem)


def _levels(z_top: float) -> dict:
    """Heights of the cap's features for a bezel whose front face is at z_top."""
    z_well = z_top - WELL_DEPTH                     # where the countersink meets the straight hole
    off = GAP * math.sqrt(2)                        # the flare runs parallel to the well, GAP off it
    r_at = lambda z: HOLE_D / 2 - off + (z - z_well)   # the flare's radius at height z
    z_flare0 = z_well + (SLEEVE_R - (HOLE_D / 2 - off))
    z_flare1 = z_well + (HEAD_R - (HOLE_D / 2 - off))
    z_rim = z_flare1 + HEAD_BAND
    return {"z_well": z_well, "z_flare0": z_flare0, "z_flare1": z_flare1, "z_rim": z_rim,
            "z_bottom": STEM_TOP - SOCKET_DEPTH, "r_at": r_at}


def _profile(z_top: float) -> np.ndarray:
    """The cap's outline in (r, z), bottom up, without the dish (cut later)."""
    L = _levels(z_top)
    arc = [(HEAD_R - EDGE_R + EDGE_R * math.cos(a), L["z_rim"] - EDGE_R + EDGE_R * math.sin(a))
           for a in np.linspace(0, math.pi / 2, 7)]
    pts = [(0.0, L["z_bottom"]), (SLEEVE_R, L["z_bottom"]), (SLEEVE_R, L["z_flare0"]),
           (HEAD_R, L["z_flare1"]), *arc, (0.0, L["z_rim"])]
    return np.array(pts)


def bezel_well(x: float, y: float, z_top: float):
    """The countersink to cut into the bezel round the joystick's hole (the straight hole
    stays generate.py's)."""
    return Pos(x, -y, z_top - WELL_DEPTH / 2 + 0.005) * Cone(HOLE_D / 2, HOLE_D / 2 + WELL_DEPTH + 0.01, WELL_DEPTH + 0.01)


def joystick_cap(x: float, y: float, z_top: float):
    """The cap, seated on the stem of a joystick at board (x, y), for a bezel face at z_top."""
    check_cap(z_top)
    L = _levels(z_top)
    face = make_face(Plane.XZ * Polyline(*[tuple(p) for p in _profile(z_top)], close=True))
    cap = revolve(face, Axis.Z)
    # the dish: a sphere cap DISH_DEPTH deep inside the rim, and the dot in its middle
    r_dish = HEAD_R - RIM
    rs = (r_dish ** 2 + DISH_DEPTH ** 2) / (2 * DISH_DEPTH)
    cap -= Pos(0, 0, L["z_rim"] - DISH_DEPTH + rs) * Sphere(rs)
    rd = (DOT_R ** 2 + DOT_H ** 2) / (2 * DOT_H)
    z_dish = L["z_rim"] - DISH_DEPTH
    cap += (Pos(0, 0, z_dish + DOT_H - rd) * Sphere(rd)) & (Pos(0, 0, z_dish) * Cylinder(DOT_R, 2 * DOT_H + 0.4))
    # the socket, turned like the post, with a crush rib on each flat
    socket = Rot(0, 0, POST_TURN) * extrude(RectangleRounded(SOCKET, SOCKET, POST_R), amount=SOCKET_DEPTH)
    cap -= Pos(0, 0, L["z_bottom"] - 0.01) * socket
    rib_depth = (SOCKET - RIB_TO) / 2
    for k in range(4):
        rib = Pos(RIB_TO / 2 + rib_depth / 2, 0, L["z_bottom"] + SOCKET_DEPTH / 2) * Box(rib_depth + 0.02, RIB_W, SOCKET_DEPTH)
        cap += Rot(0, 0, POST_TURN + 90 * k) * rib   # square to each flat
    if not cap.is_valid:
        raise SystemExit("joystick cap: invalid solid")
    # baked into the geometry, not kept as a location: export_gltf writes a location as a
    # node translation it does not turn to glTF's Y-up, and the part lands elsewhere
    return cap.translate(Vector(x, -y, 0))


def _tilt_contact(z_top: float) -> float:
    """The tilt (deg) at which the cap first comes within BEZEL_CLR of the bezel: the
    straight hole from the joystick's pocket up to the well, the well, and the face."""
    prof = _profile(z_top)
    dense = []
    for (r0, z0), (r1, z1) in zip(prof[:-1], prof[1:]):
        for t in np.linspace(0, 1, 40, endpoint=False):
            dense.append((r0 + (r1 - r0) * t, z0 + (z1 - z0) * t))
    pts = np.array(dense)
    pts = np.r_[pts, pts * [-1, 1]]                       # both sides of the section
    z_well = z_top - WELL_DEPTH

    def bezel_r(z):                                       # the bezel's opening at height z (inf above the face)
        if z >= z_top + BEZEL_CLR:
            return math.inf
        if z >= z_well:
            return HOLE_D / 2 + (min(z, z_top) - z_well)
        return HOLE_D / 2

    def hits(deg):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        x = pts[:, 0] * c - (pts[:, 1] - PIVOT_Z) * s
        z = pts[:, 0] * s + (pts[:, 1] - PIVOT_Z) * c + PIVOT_Z
        return any(abs(xi) > bezel_r(zi) - BEZEL_CLR for xi, zi in zip(x, z))

    if hits(0.0):
        raise SystemExit("joystick cap: touches the bezel at rest")
    lo, hi = 0.0, 30.0
    if not hits(hi):
        return math.inf
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if hits(mid) else (mid, hi)
    return lo


def check_cap(z_top: float) -> dict:
    """Every geometry check; SystemExit on the first that fails."""
    L = _levels(z_top)
    socket_corner = SOCKET / 2 * math.sqrt(2) - POST_R * (math.sqrt(2) - 1)
    wall = SLEEVE_R - socket_corner
    roof = L["z_rim"] - DISH_DEPTH - STEM_TOP
    stop = _tilt_contact(z_top)
    push_gap = GAP * math.sqrt(2)                         # straight down onto a 45 deg well
    if SOCKET_DEPTH > FIT_MAX:
        raise SystemExit(f"joystick cap: socket {SOCKET_DEPTH} mm deep, the post allows {FIT_MAX}")
    if L["z_bottom"] < 3.0 + 0.2:
        raise SystemExit("joystick cap: the sleeve reaches the stem's collar")
    if wall < MIN_WALL or roof < MIN_WALL:
        raise SystemExit(f"joystick cap: wall {wall:.2f} / roof {roof:.2f} mm, under {MIN_WALL}")
    if not L["z_well"] < L["z_flare0"]:
        raise SystemExit("joystick cap: the flare starts below the well")
    if push_gap < PUSH_TRAVEL + BEZEL_CLR:
        raise SystemExit(f"joystick cap: {push_gap:.2f} mm under the flare, the push needs {PUSH_TRAVEL}")
    if not STOP_MIN * TILT_SWITCH <= stop <= STOP_MAX * TILT_SWITCH:
        raise SystemExit(f"joystick cap: the bezel stops the tilt at {stop:.1f} deg; the switch needs "
                         f"{TILT_SWITCH:.1f}, the stop belongs within {STOP_MIN}-{STOP_MAX}x that")
    return {"wall": wall, "roof": roof, "stop": stop, "push_gap": push_gap, **L}


def cap_report(z_top: float) -> list[str]:
    c = check_cap(z_top)
    return [f"joystick cap: 8 mm pad {c['z_rim'] - z_top:.2f} mm proud of the front face at its rim "
            f"({c['z_rim'] - DISH_DEPTH - z_top:.2f} at the dish), nested in a 45 deg well "
            f"({HOLE_D:.1f} -> {HOLE_D + 2 * WELL_DEPTH:.1f} mm, {WELL_DEPTH} deep)",
            f"joystick cap: switches at {TILT_SWITCH:.1f} deg, the bezel stops it at {c['stop']:.1f} deg; "
            f"{c['push_gap']:.2f} mm free under the flare for the {PUSH_TRAVEL} mm push",
            f"joystick cap: socket {SOCKET:.2f} mm over a {POST}+-{POST_TOL} post, ribs to {RIB_TO:.2f}, "
            f"{SOCKET_DEPTH} deep (post allows {FIT_MAX}); walls {c['wall']:.2f}, roof {c['roof']:.2f} mm"]


if __name__ == "__main__":
    import sys
    from build123d import export_stl
    z_top = float(sys.argv[1]) if len(sys.argv) > 1 else 5.33
    out = sys.argv[2] if len(sys.argv) > 2 else "joycap.stl"
    cap = joystick_cap(0, 0, z_top)
    print("\n".join(cap_report(z_top)))
    bb = cap.bounding_box()
    print(f"bbox {bb.size.X:.2f} x {bb.size.Y:.2f} x {bb.size.Z:.2f}, Z {bb.min.Z:.2f}..{bb.max.Z:.2f}, volume {cap.volume:.1f} mm3")
    export_stl(cap, out, tolerance=0.01, angular_tolerance=0.1)
