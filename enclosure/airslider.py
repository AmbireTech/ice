#!/usr/bin/env python3
"""The airgap switch's slider: a printed thumb slide in the west wall that moves the
MSK12C02's lever, so the switch is worked from outside the case.

Model space as in generate.py: mm, X east, Y north, Z toward the screen, Z = 0 at the
board's front face. Board coordinates are y south-positive: model Y = -y. The lever's
geometry comes from the switch's own model on the board (generate.py hands it in), the
travel from the datasheet (generate.py's LEVER_TRAVEL).

The lever stops about 0.8 mm into the wall. The slider is a small block in a window
through the wall:
  - inside, a flange larger than the window sits in a pocket in the wall's inner face:
    it cannot come out of the case. Pushed inward it stops on the board's edge, GAP
    away, before the slot's floor reaches the lever's tip;
  - its inner end is a fork open toward the screen: the lever drops into it as the
    board goes into the shell, and its tines push the lever along the wall;
  - outside, a ribbed grip stands GRIP_PROUD out of the wall, between two guard ridges
    on the shell that stand as far out, so it does not catch in a pocket.
The window lets it go the lever's travel plus SLIDE_CLR each way: the switch's own
stops end the travel, the window only just after them.

The slider goes in from inside the shell before the board; it prints on its flange
(inner face down, grip up) with no supports.
"""

from __future__ import annotations

from build123d import Box, Plane, Polyline, Pos, Rot, extrude, make_face

FORK_CLR = 0.1       # the lever in the fork's slot, each side and under it
TINE = 0.8           # each tine: two 0.4 mm perimeters
FLOOR_T = 0.6        # the block under the slot
TINE_UP = 0.6        # the tines above the lever's top
FLANGE_T, LIP = 0.5, 0.8   # the inner flange: thickness, and how far it overlaps the window all round...
LIP_BELOW = 0.6      # ...but under it at most what the shell's floor leaves room for, and none
MIN_LIP = 0.4        # under one perimeter (it would not print)
SLIDE_CLR = 0.15     # the slider in its window and pocket, each side along the wall
FACE_CLR = 0.1       # the flange's outer face to the pocket's back (it rides on it)
GRIP_PROUD = 0.6     # the grip (and the guard ridges) out of the wall
RIB_W, RIB_D = 0.4, 0.25   # grooves across the grip's face, for a thumb
GUARD_H = 0.2        # each guard ridge's flat face, as proud as the grip (a 45 deg underside under it)
MIN_SKIN = 0.8       # the wall left round the pocket, and the block behind the slot under a groove


def _box(x0, y0, x1, y1, z0, z1):
    """A box from board coordinates (y south) into model space (Y north)."""
    return Pos((x0 + x1) / 2, -(y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def plan(lever: dict, sw_y: float, travel: float, gap: float, x_out, z_floor_in: float, z_board_back: float) -> dict:
    """Every dimension, from the lever as modelled (tip x, y0..y1, z0..z1 in model Z; the
    model shows it at one end of its travel), the switch's centre along the wall, the wall's
    inner face at x = -gap and its outer face x_out(z), the board's back face at z_board_back
    (its front face at 0). SystemExit on the first check that fails."""
    tip, (ly0, ly1), (lz0, lz1) = lever["tip"], lever["y"], lever["z"]
    ly = (ly0 + ly1) / 2
    toward = 1.0 if sw_y > ly else -1.0      # the lever's other end of travel
    if abs(abs(sw_y - ly) - travel / 2) > 0.3:
        raise SystemExit(f"airgap slider: the modelled lever ({ly:.2f}) is not at an end of its "
                         f"{travel} mm travel round the switch's centre ({sw_y:.2f})")
    x_in = -gap
    if tip > x_in - 0.3:
        raise SystemExit(f"airgap slider: the lever reaches only {x_in - tip:.2f} mm into the wall")
    half = (ly1 - ly0) / 2 + FORK_CLR + TINE
    blk = (ly - half, ly + half, lz0 - FORK_CLR - FLOOR_T, lz1 + TINE_UP)          # y0, y1, z0, z1
    room = blk[2] - SLIDE_CLR - (z_floor_in + 0.2)
    below = min(LIP_BELOW, room) if room >= MIN_LIP else 0.0
    fl = (blk[0] - LIP, blk[1] + LIP, blk[2] - below, blk[3] + LIP)
    x_flange = x_in - FLANGE_T
    x_pocket = x_flange - FACE_CLR
    x_wall_out = max(x_out(z) for z in (fl[2], fl[3]))      # the wall at its thinnest over the pocket
    skin = x_pocket - x_wall_out
    if skin < MIN_SKIN:
        raise SystemExit(f"airgap slider: {skin:.2f} mm of wall left behind the pocket, under {MIN_SKIN}")
    x_grip = min(x_out(z) for z in (blk[2], blk[3])) - GRIP_PROUD
    # pushed in, the flange meets the board's edge (x = 0) after `gap`: the slot's floor
    # must still be short of the lever's tip then
    slot_floor = tip - gap - 0.1
    behind = slot_floor - (x_grip + RIB_D)
    if behind < MIN_SKIN:
        raise SystemExit(f"airgap slider: {behind:.2f} mm of block between the slot and a groove, under {MIN_SKIN}")
    if not fl[3] > z_board_back + 0.3:
        raise SystemExit("airgap slider: the flange does not reach up beside the board's edge, which stops it pushed in")
    if fl[2] - SLIDE_CLR < z_floor_in + 0.2:
        raise SystemExit(f"airgap slider: the pocket ({fl[2] - SLIDE_CLR:.2f}) runs into the shell's floor ({z_floor_in:.2f})")
    if fl[3] + SLIDE_CLR > -0.2:
        raise SystemExit("airgap slider: the pocket reaches the parting line")
    y_lo = min(0.0, toward * travel)
    y_hi = max(0.0, toward * travel)
    return {"tip": tip, "x_in": x_in, "x_flange": x_flange, "x_pocket": x_pocket, "x_grip": x_grip, "slot_floor": slot_floor,
            "x_out": x_out, "lever": (ly0, ly1, lz0, lz1), "blk": blk, "fl": fl, "travel": travel, "toward": toward,
            "y_lo": y_lo, "y_hi": y_hi, "skin": skin, "sw_y": sw_y}


def slider(p: dict):
    """The slider, at the lever's modelled position."""
    (by0, by1, bz0, bz1), (fy0, fy1, fz0, fz1) = p["blk"], p["fl"]
    ly0, ly1, lz0, _ = p["lever"]
    part = _box(p["x_flange"], fy0, p["x_in"], fy1, fz0, fz1)
    part += _box(p["x_grip"], by0, p["x_flange"] + 0.01, by1, bz0, bz1)
    part -= _box(p["slot_floor"], ly0 - FORK_CLR, p["x_in"] + 1, ly1 + FORK_CLR, lz0 - FORK_CLR, fz1 + 1)
    for k in (-1, 1):   # two grooves across the grip, running toward the screen
        yc = (by0 + by1) / 2 + k * (by1 - by0) / 4
        part -= _box(p["x_grip"] - 1, yc - RIB_W / 2, p["x_grip"] + RIB_D, yc + RIB_W / 2, bz0 - 1, bz1 + 1)
    if not part.is_valid:
        raise SystemExit("airgap slider: invalid solid")
    return part


def wall_cuts(p: dict):
    """What comes out of the shell: the pocket in the wall's inner face, the window through it."""
    (by0, by1, bz0, bz1), (fy0, fy1, fz0, fz1) = p["blk"], p["fl"]
    c = SLIDE_CLR
    pocket = _box(p["x_pocket"], fy0 + p["y_lo"] - c, p["x_in"] + 0.5, fy1 + p["y_hi"] + c, fz0 - c, fz1 + c)
    window = _box(p["x_grip"] - 3, by0 + p["y_lo"] - c, p["x_pocket"] + 0.01, by1 + p["y_hi"] + c, bz0 - c, bz1 + c)
    return pocket + window


def guards(p: dict):
    """Two ridges on the wall's outside, above and below the window, as proud as the grip;
    each has a 45 deg underside (the shell prints on its back) and runs a mm past the window.
    Above the window the slope starts at its edge; below it the ridge's top is the edge."""
    by0, by1, bz0, bz1 = p["blk"]
    y0, y1 = by0 + p["y_lo"] - SLIDE_CLR - 1.0, by1 + p["y_hi"] + SLIDE_CLR + 1.0
    x_f = p["x_grip"]
    out = []
    for z_edge, upward in ((bz1 + SLIDE_CLR, True), (bz0 - SLIDE_CLR, False)):
        # the underside's rise: the ridge's reach from the wall where the slope starts (below the
        # window that is lower, where the drafted wall stands further in; 1 mm covers it: <= 45 deg)
        run = p["x_out"](z_edge if upward else z_edge - GUARD_H - 1.0) - x_f
        zb, zt = (z_edge, z_edge + run + GUARD_H) if upward else (z_edge - GUARD_H - run, z_edge)
        if zt > -0.1:
            raise SystemExit("airgap slider: the upper guard ridge reaches the parting line")
        x_w = max(p["x_out"](zb), p["x_out"](zt)) + 0.05     # rooted in the wall over its whole height
        pts = [(x_w, zb), (p["x_out"](zb), zb), (x_f, zb + run), (x_f, zt), (x_w, zt)]
        face = make_face(Plane.XZ * Polyline(*pts, close=True))
        ridge = extrude(face, amount=y1 - y0)               # Plane.XZ extrudes toward -Y
        ridge = Pos(0, -y0 - ridge.bounding_box().max.Y, 0) * ridge
        out.append(ridge)
    return out[0] + out[1]


def print_pose(part):
    """On its flange: the inner face (+X) down, the grip up."""
    return Rot(0, -90, 0) * part


def report(p: dict) -> list[str]:
    by0, by1, bz0, bz1 = p["blk"]
    fy0, fy1, fz0, fz1 = p["fl"]
    return [f"airgap slider: grip {by1 - by0:.1f} x {bz1 - bz0:.1f} mm, {GRIP_PROUD} mm out of the wall between two guard "
            f"ridges; slides {p['travel']} mm (+{SLIDE_CLR} to its window's ends) "
            f"{'south' if p['toward'] > 0 else 'north'} from the lever's modelled end",
            f"airgap slider: flange {fy1 - fy0:.1f} x {fz1 - fz0:.1f} x {FLANGE_T} mm in a pocket in the wall's inner face, "
            f"{p['skin']:.2f} mm of wall behind it; the fork holds the lever's last {p['x_in'] - p['tip']:.2f} mm"]
