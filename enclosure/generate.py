#!/usr/bin/env python3
"""Ambire Ice enclosure (v4): a two-part see-through case generated from the board,
and the battery pack that snaps onto its back on a magnetic pogo connector.

Every feature that has to line up with the board is read from the board, not typed in:

  board/wallet.kicad_pcb    outline, slots, thickness, mounting holes, the camera
                            keep-out, the AAA clips (their shape, for the pack), the
                            joystick, USB-C (the pack stops short of it)
  kicad-cli pcb export glb  every modelled part on the back face, as real geometry:
                            the deepest part sets the back cavity (USB-C excepted: it
                            may stand into the floor), and whatever pokes past the board
                            edge (USB-C mouth, airgap lever) gets a wall opening sized from it

The rest are datasheet constants, each with its source below.

v4 takes the cell off the board (docs/pack.md): two AAA cells in series ride in a pack
high on the device's back, just below the camera. A magnetic pogo pair connects and holds
it: a receptacle on the device board's back, a male on the pack's own board, their own
magnets the only ones. The board file still carries proto 1's clips; this script leaves
them off the device and uses their models for the pack (enclosure/pack_clips.kicad_pcb), and puts the male over the board's J_PACK,
until the board has it.

Parts (Z = 0 is the board's front face, +Z toward the screen; X east, Y north):

  shell   back + walls up to the board's front face; a standoff under each board
          hole: a locating pin where the hole sits under the screen, a clearance hole
          and a counterbore (the screw head sits flush) where the front half is solid;
          USB-C opening, the airgap slider's window, an open window the camera head stands
          through, a hole over the buzzer, a groove along the north tongue; a window in
          the back the pack's connector drops through onto the receptacle
  bezel   front plate with the display window and walls down to the parting line;
          its skirt laps over a tongue on the shell, with a ridge along the north side
          that hooks into the tongue's groove. Inside: solid fills that locate the
          screen, a chin with windows for the display ribbons, one pocket over all the
          front parts (the radio module, the power parts, the joystick's housing) with a
          hole for the joystick, and a threaded boss at each screw hole
  tray    the pack's frame: the pack's board sits on its north and south walls, its
          connector standing out toward the device; the cells show underneath
  joycap  the joystick's printed cap (enclosure/joycap.py)
  airslider  the airgap switch's printed slider in the west wall (enclosure/airslider.py)

Assembly: the airgap slider into its window from inside the shell, screen onto the
board's front face on foam tape (ribbons through the slots into their connectors), board onto the shell's pins and standoffs, bezel hooked in at
the north and swung down, the M2 screws from the back through shell and board into the
bezel's bosses. The pack: its board glued onto the tray's walls, the cells pressed into
their clips from underneath.

Outputs:
  enclosure/out/{shell,bezel,tray,joycap,airslider}.{step,stl}, case.3mf   for print / quote (the 3MF
                                                      lays all five parts out for printing)
  docs/v4/enclosure/                                  the /v4/ concept page's 3D preview and configurator:
    enclosure.json    the numbers the preview draws from, the flame, the print schemes, the pack
    board.json        the populated board, from KiCad's own 3D export (without the clips)
    pack.json         the pack's board, its connector, clips and cells, packed the same way
    {shell,bezel,tray,joycap,airslider}.json  the case parts (GLB as base64: the artifact host serves no .glb)
    files/            the DIY set: the device board's order files (JLCPCB, PCBWay), the pack
                      board's (JLCPCB), the case and the pack

Run from hardware/ambire-wallet:
  ../../.venv-enclosure/bin/python enclosure/generate.py
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from build123d import (
    Axis,
    Box,
    Rectangle,
    Cone,
    Cylinder,
    Pos,
    RectangleRounded,
    Rot,
    export_gltf,
    export_step,
    export_stl,
    extrude,
    Mesher,
    Plane,
    Polygon,
    Vector,
    fillet,
)

HERE = Path(__file__).resolve().parent.parent
BOARD = Path(os.environ.get("ENCLOSURE_BOARD", HERE / "board/wallet.kicad_pcb"))   # a copy for trials
OUT = HERE / "enclosure/out"
ORDER = HERE / "production/order"            # the device board's order set (production/make_order.sh)
PACK_ORDER = HERE / "production/pack-order"  # the pack board's (pack/makeOrder.py)
WEB = HERE / "docs/v4/enclosure"   # v4 (the battery pack): the /v4/ page; docs/enclosure (proto 0), docs/v2 (proto 1, front cell) and docs/v3 (cell on the back) are frozen
sys.path.insert(0, str(HERE / "art"))
import flame as flame_art  # noqa: E402  (art/flame.py, shared with scripts/silkFlame.py)
import joycap  # noqa: E402  (enclosure/joycap.py: the joystick's printed cap)
import airslider  # noqa: E402  (enclosure/airslider.py: the airgap switch's printed slider)
KICAD_3D = os.environ.get("KICAD_3DMODEL_DIR", "/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels")

# ── Stack in front of the board ─────────────────────────────────────────────
FOAM_T = 0.3          # foam tape under the screen, on the board (newBoard.py budgets the tails with it)
# The joystick (ALPS SKRHAAE010, 7.5 x 7.5 mm housing 2.23 mm tall, its stem to 5.0 mm:
# ALPS's drawing and 3d/SW_ALPS_SKRHAAE010.step agree; EasyEDA's model said 5.08) on the front, below the cell: a pocket over its housing in
# the bezel's solid chin, and a hole wide enough for the stem to rock. The stem stops
# just under the front face: a printed cap goes on it.
JOY_REF, JOY_BODY, JOY_HOUSING_H, JOY_H, JOY_HOLE_D = "SW_JOY", 7.5, 2.23, 5.0, 6.0
JOY_CLR = 0.3        # around the housing, and over it
# Other parts on the front (proto 1 put power parts there, in the chin): one pocket in
# the bezel's chin covers them all, as deep as the tallest plus FRONT_CLR; FRONT_SKIN is
# the least bezel left over it. Each part's fab outline plus FRONT_CLR stays clear of
# the screw boss.
FRONT_CLR, FRONT_SKIN = 0.3, 0.8
# Screws holding the front half (with the north hook and the pins). Two is the design;
# one is allowed only while the west hole H3 is out (owner, 2026-10-22: the cell's west
# clip moved north over its boss). Put back 2 with H3.
MIN_SCREWS = 1
# Newhaven NHD-2.4-240320AF-CSXP-CTP mechanical drawing (12/15/2023): outline
# 42.80 x 59.91, overall 4.13 +/-0.30; viewing area 37.72 x 49.96, 2.54 in from
# each side and 2.52 down from the top edge.
PANEL_T = 4.13
PANEL_VA = (2.54, 2.52, 37.72, 49.96)  # x, y offset from the outline's top-left, w, h
PANEL_GAP = 0.1       # under the lip; the foam takes the panel's +/-0.3 tolerance

# ── Back face ───────────────────────────────────────────────────────────────
# Not modelled in KiCad: the OV2640 camera head ("5.4-5.9 mm tall on every
# drawing found", design.md) is fitted by hand and stands through an open window in
# the back, so it does not set the cavity; the others are packages whose library
# models are missing from this KiCad install. Each gets a height bound here, so a
# newly unmodelled part stops the build instead of passing unseen.
CAM_HEAD_H = 5.9
CAM_CLR = 0.4         # window around the camera head's keep-out
UNMODELLED_MAX_H = {"U1": 0.8, "U2": 0.8, "U5": 0.6, "U6": 0.6, "U10": 0.6, "U13": 1.0, "U16": 1.0,
                    "LS1": 2.0, "L_BOOST": 2.0, "L_BUCK": 1.0}
BACK_CLEAR = 0.6      # between the deepest part and the inside of the back
# Parts that do not set the back's depth (owner, 2026-10-28: USB-C may stand into the
# floor or out of it): the floor gets a pocket over them, PROTRUDE_CLR clear, and opens
# through where that would leave less than MIN_SKIN.
PROTRUDE = ("J1",)
PROTRUDE_CLR, MIN_SKIN = 0.3, 0.6

# ── Case ────────────────────────────────────────────────────────────────────
WALL = 1.8
FLOOR = 1.2
LIP = 0.8             # bezel over the glass
GAP = 0.3             # board edge to wall
# Sliding and snapping fits, for an FDM print (0.4 mm nozzle; the first print, 2026-10-26,
# at 0-0.1 mm would not go together): the tongue in the bezel's skirt and inside its
# fill, the north hook in its groove.
FIT = 0.2
TONGUE_T, TONGUE_H, TONGUE_CLR = 0.8, 1.8, FIT
BACK_FILLET, FRONT_FILLET = 1.5, 1.2
# Outer walls lean in toward the back and toward the front, widest at the parting
# line, so the two halves meet flush: the back and front faces are DRAFT_MM narrower
# (in total, both sides together) than the parting line. That softens the box and
# lets a moulded part release; both halves still print flat face down without
# supports (the walls overhang ~3 deg, far inside the ~45 deg a printer manages).
DRAFT_MM = 1.0
MAX_UNSUPPORTED = 45.0
MIN_WALL = 1.2        # thinnest the drafted wall may get, at the back and front faces
WINDOW_MARGIN = 0.3   # display window beyond the viewing area, each side
# Screws: M2 pan head, self-tapping into the bezel's bosses, from the back.
STANDOFF_R = 2.5     # shell standoff under each board hole (the board keeps parts 2.6 mm off the hole centre)
CLEAR_R = 1.2        # M2 clearance through the shell
CB_R, CB_DEPTH = 2.05, 1.7   # counterbore for the 3.8 x 1.6 mm head, flush with the back face
BOSS_R, PILOT_R = 2.2, 0.8   # bezel boss, 1.6 mm pilot for M2 self-tapping
PILOT_TOP_CLR = 0.8  # pilot stops this far under the bezel's front face
PIN_R = 1.0          # locating pin in a 2.2 mm board hole
HOOK = (30.0, 0.3, 0.8, 0.15)  # north hook: length, ridge depth, ridge height, clearance
SOUND_R = 1.0        # hole in the back over the buzzer's top port (MLT-5020: 0.8 mm port)
SCREW_LENGTHS = (6, 8, 10, 12)
MIN_ENGAGE = 2.5     # mm of thread in the boss
BOSS_PART_CLR = 0.2
M2_SHANK_R = 1.0
MIN_SHANK_CLR = 0.3  # screw shank to the nearest part; a standoff notched for a part may open on that side
SCREEN_CLR = 0.15    # bezel fills to the screen's glass edge
RIBBON_CLR = 0.3     # around the display ribbons' slots
USB_PLUG = (12.6, 6.8, 2.2)  # opening for a USB-C plug overmold (w, h, corner r); common overmolds are <= 12.35 x 6.5
LEVER_TRAVEL = 1.5   # MSK12C02 slide travel; its lever stops inside the wall, a printed slider (airslider.py) brings it out


# ── The battery pack (docs/pack.md) ─────────────────────────────────────────
# Two AAA cells in series, lying east-west across the back side by side, in the same
# MYOUNG clips the board carries today (MY-AAA-19-J / -20-J, LCSC C6937104 / C6937107),
# on the pack's own board. The pack sits high on the back, just south of the camera
# and outside its view; its bottom is open (owner, 2026-10-28: as slim as possible): the
# clips hold the cells, as v3's cell was held.
BOARD_CELL_CLIPS = ("BT_N", "BT_P")   # proto 1's clips, as that board placed them: the pack's take their models
PACK_CLIPS = HERE / "enclosure/pack_clips.kicad_pcb"   # (the device board no longer carries them)
CELL_D, CELL_L, CELL_BASE = 10.5, 44.5, 0.25   # AAA (Energizer E92/L92, eneloop BK-4MCCE), on the clips' 0.25 mm steel base
# What the male's joints must clear on the pack board is the clips' steel that lies on it,
# taken from their models: each foot is a cross, a body 5 mm across the cell (its 9 x 5 mm
# pad) the clip's length, and a tab 7 mm across but only 4 mm along it, from which the arms
# rise; nothing else of the clip comes near the board.
LOW_STEEL = 0.5       # clip steel this close to the pack board counts as lying on it
CLIP_BODY_HALF = 2.6  # the foot's body: model vertices this close to the cell's axis; the tab beyond
# A cell goes into its clips through the arms' tips, which the model has 6.9 mm apart: each
# arm swings out ~1.7 mm at its tip and ~0.9 mm at its widest (hinging at its root). The
# arms of the two cells' clips face each other, and on the east side they are the pack's
# minus and the south cell's plus, ahead of the fuse: touching while a cell goes in is a
# dead short. So the arms keep twice that swing apart at rest.
ARM_SWING = 0.9
ARM_GAP = 2 * ARM_SWING
CONN_JOINT_CLR = 0.3  # the male's through-hole joints (inner face, flat: their tails end in the board) to that steel
SMALL_CLR = 0.3       # the pack board's small parts under the cells' cans
PACK_PCB_T = 0.8      # the pack's board
PACK_LEDGE = 1.0      # its north and south edges rest on the tray's walls this far
PACK_CLR = 0.3        # the clips to the tray's walls
PACK_RIM = 1.4        # the tray's walls round the pack board, where the tray seats on the back face
PACK_R, PACK_FILLET = 4.0, 1.5
# The camera must see past the pack: a cone of CAM_FOV from the lens's rim (~66 deg lens,
# design.md; the stock candidate is 68.7 deg diagonal), taken as a full cone.
CAM_FOV, LENS_R = 70.0, 2.6
# The contacts (owner, 2026-10-28): a readymade magnetic pogo pair whose own magnets hold
# the pack, no loose magnets. On the device board's back, Xinyangze's 4-pin SMT magnetic
# receptacle YZ103915020T-04025-02 (LCSC C6276862; drawing 2015-07-08): 22.30 x 7.70 mm,
# 2.00 mm tall on feet 0.20 mm below it (2.20 mounted), flat gold contacts at 2.54 mm, two NdFeB magnets in its ends, two 5.00 x
# 1.50 x 0.50 mm locating bosses underneath (slots in the board). On the pack board's outer
# face, Xinyangze's 4-pin magnetic male YZP0048-20048-04025-03 (LCSC C5126845, through-
# hole; drawing 2022-03-24): body 21.20 x 6.86 mm with a 5.03 x 2.31 mm tab, 2.80 thick,
# pins at 2.50 mm, 4.10 free, 3.10 working; 30 +-20 g each. Two families: their pitches
# (2.54 / 2.50) and magnet spacing (~14.6 / 15.41) differ slightly, and whether the
# magnets attract is for samples to show.
CONN_DEV = {"part": "YZ103915020T-04025-02", "lcsc": "C6276862", "len": 22.3, "wid": 7.7, "h": 2.2,
            "pitch": 2.54, "boss": (5.0, 1.5, 0.5)}
CONN_PACK = {"part": "YZP0048-20048-04025-03", "lcsc": "C5126845", "len": 21.2, "wid": 6.86, "tab": (5.03, 2.31),
             "body_t": 2.8, "free": 4.1, "work": 3.1, "pitch": 2.5, "plunger_d": 0.9, "barrel_d": 1.5,
             "pad_d": 1.5}   # pack board land: 1.5 mm rings on 0.8 mm holes (pack/makePack.py)
CONN_NETS = ("BAT+", "MID", "NTC", "BAT-")   # east to west (the receptacle's pad 1 east)
# Where the pair goes: wherever the device board places the receptacle (J_PACK), long axis
# east-west, south of the MCU over the display connector (v4, 2026-10-28: the back's east
# side is the touch tail's lane). The male lies along the south cell, its pins in a row
# under the cell's south flank, clear of the clips (they hold the cells' ends); the cells
# stand side by side, as close as the clips' arms let them.
CONN_CLR = 0.2        # the male's body in its window; the receptacle to the nearest part
# The pack board's other parts on its inner face (x, y off the pins' centre; w, h, height;
# label), where pack/pack.kicad_pcb has them: in the strip south of the south cell's can,
# beside the pins' row: a resettable fuse in BAT+, the mid-point sense line's series
# resistor, the thermistor (TS).
PACK_SMALL = [(8.5, 2.5, 3.2, 1.6, 0.85, "PTC 1206"), (5.3, 2.5, 1.0, 0.5, 0.35, "R 0402"),
              (-6.2, 2.5, 1.6, 0.8, 0.8, "NTC 0603")]

# ── Back print ──────────────────────────────────────────────────────────────
# The flame itself (facets, size, placement, gap) is art/flame.py, shared with the
# board's silkscreen (scripts/silkFlame.py).
# Ink on mask. Brand colours: Purple #6000FF, Black #181A1F, Volt #D7FF00 (the
# brand's neon green). JLC's standard silkscreen is white or black only, so a
# purple flame or a Volt board is a full-colour print (PCBWay); white on black and
# black on white are an ordinary mask + silkscreen order anywhere.
PRINT_SCHEMES = [
    # offered: False keeps a scheme out of the configurator (neon is off for proto 1)
    {"id": "neon", "label": "Purple on neon", "ink": "#6000FF", "mask": "#D7FF00", "colour": True, "offered": False},
    {"id": "black", "label": "Purple on black", "ink": "#6000FF", "mask": "#181A1F", "colour": True},
    {"id": "white-black", "label": "White on black", "ink": "#F2F3F5", "mask": "#181A1F", "colour": False},
    {"id": "black-white", "label": "Black on white", "ink": "#181A1F", "mask": "#EEEEEA", "colour": False},
    {"id": "purple-white", "label": "Purple on white", "ink": "#6000FF", "mask": "#EEEEEA", "colour": True},
    {"id": "incognito", "label": "Incognito", "ink": None, "mask": "#181A1F", "colour": False},   # plain black, no print
]

CASE_README = """Ambire Ice v4 case and battery pack

case.3mf     the five parts laid out for printing: the shell on its back, the bezel face
             down, the tray on its top face, the joystick's cap on its sleeve, the airgap
             slider on its flange
shell.stl    back shell (standoffs, locating pins, the screw hole, the camera window, the
             window the pack's connector drops through)
bezel.stl    front bezel (the screen sits in it; the screw threads into its chin)
tray.stl     the battery pack's frame: the pack's board sits in its top, the cells show underneath
joycap.stl   the joystick's cap: print it standing on its sleeve, dish up; press it straight
             onto the stem (a drop of glue if it comes loose)
airslider.stl  the airgap switch's slider: print it on its flange, grip up; it goes into its
             window in the shell's west wall from inside, grip out, before the board
*.step       the same parts for CAD

Assembly: push the airgap slider into its window in the shell's west wall from inside,
grip out. Stick the screen to the board's front face with 0.3 mm foam tape, its
ribbons through the board's slots into their connectors. Lay the board in the shell (its
two top holes onto the pins). Hook the bezel's top edge under the shell's top lip, swing
it down over the screen, then the M2 pan-head screw from the back.

The pack: glue the pack board into the tray's top, its magnetic connector out. Two AAA
cells press into the clips from underneath, as marked on the board. The pack snaps onto
the back below the camera, its connector through the window onto the receptacle on the
device's board, whose magnets hold it; the cells charge over USB in the device.

Print the shell on its back, the bezel face down and the tray on its top face: none needs
supports. Units are millimetres. Print in see-through resin or PETG (ice blue, orange,
purple or clear). The fits are set for an FDM printer with a 0.4 mm nozzle (0.2 mm
between parts that slide or snap). These files have not been printed yet.
"""

MESH_TOL = 0.03


# ─────────────────────────────────────────────────────────────────── board file


def sexp(text: str):
    stack: list[list] = [[]]
    for t in re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+', text):
        if t == "(":
            stack.append([])
        elif t == ")":
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(t[1:-1] if t.startswith('"') else t)
    return stack[0][0]


def kids(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def kid(node, key):
    found = kids(node, key)
    return found[0] if found else None


def xy(node):
    return float(node[1]), float(node[2])


def read_board(path: Path) -> dict:
    root = sexp(path.read_text(encoding="utf-8"))
    thickness = float(kid(kid(root, "general"), "thickness")[1])

    edges = []
    for item in kids(root, "gr_line") + kids(root, "gr_arc"):
        if kid(item, "layer")[1] != "Edge.Cuts":
            continue
        pts = [xy(kid(item, k)) for k in ("start", "mid", "end") if kid(item, k)]
        edges.append(pts)
    allp = np.array([p for e in edges for p in e])
    x0, y0 = allp.min(0)
    x1, y1 = allp.max(0)
    if abs(x0) > 0.01 or abs(y0) > 0.01:
        raise SystemExit(f"board outline does not start at the origin: {x0:.3f}, {y0:.3f}")

    def on_outer(pts):
        return any(min(abs(px - x0), abs(px - x1), abs(py - y0), abs(py - y1)) < 0.01 for px, py in pts)

    corner = [e for e in edges if len(e) == 3 and on_outer(e)]
    radius = round(abs(corner[0][0][0] - corner[0][2][0]), 3)

    # Inner loops (the tail slots): union edges that share an endpoint.
    inner = [e for e in edges if not on_outer(e)]
    parent = list(range(len(inner)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    key = lambda p: (round(p[0], 2), round(p[1], 2))
    owner = {}
    for i, e in enumerate(inner):
        for p in (e[0], e[-1]):
            j = owner.setdefault(key(p), i)
            parent[find(i)] = find(j)
    loops: dict[int, list] = {}
    for i, e in enumerate(inner):
        loops.setdefault(find(i), []).extend(e)
    slots = []
    for pts in loops.values():
        a = np.array(pts)
        slots.append([round(v, 3) for v in (*a.min(0), *a.max(0))])
    slots.sort()

    fps = {}
    for fp in kids(root, "footprint"):
        ref = next((p[2] for p in kids(fp, "property") if p[1] == "Reference"), None)
        at = kid(fp, "at")
        attr = kid(fp, "attr") or []
        fps[ref] = {
            "lib": fp[1], "x": float(at[1]), "y": float(at[2]),
            "rot": float(at[3]) if len(at) > 3 else 0.0, "layer": kid(fp, "layer")[1], "node": fp,
            "dnp": "dnp" in attr,
        }
    holes = []
    for ref, f in sorted(fps.items()):
        if f["lib"].startswith("MountingHole:"):
            drill = float(kid(kid(f["node"], "pad"), "drill")[1])
            holes.append([f["x"], f["y"], drill])

    zones = {}
    for z in kids(root, "zone"):
        name = kid(z, "name")
        if not name:
            continue
        pts = np.array([xy(p) for p in kid(kid(z, "polygon"), "pts") if p[0] == "xy"])
        zones[name[1]] = [round(v, 3) for v in (*pts.min(0), *pts.max(0))]

    return {"w": x1 - x0, "h": y1 - y0, "r": radius, "t": thickness, "slots": slots,
            "holes": holes, "zones": zones, "fps": fps}


def panel_outline():
    """The panel outline newBoard.py lays the display tails from (x, y, w, h)."""
    src = (HERE / "scripts/newBoard.py").read_text(encoding="utf-8")
    m = re.search(r"^PANEL = \(([^)]*)\)", src, re.M)
    if not m:
        raise SystemExit("PANEL not found in scripts/newBoard.py")
    return tuple(float(v) for v in m.group(1).split(","))


def fab_world(fp: dict) -> np.ndarray:
    """Fab-layer line endpoints of a footprint in board coordinates. KiCad stores a
    back-side footprint's graphics already mirrored; world = at + R(-rot) local."""
    th = math.radians(fp["rot"])
    c, s = math.cos(th), math.sin(th)
    local = []
    for ln in kids(fp["node"], "fp_line"):
        if kid(ln, "layer")[1].endswith(".Fab"):
            local += [xy(kid(ln, k)) for k in ("start", "end")]
    for rc in kids(fp["node"], "fp_rect"):     # newer libraries draw the body as a rectangle
        if kid(rc, "layer")[1].endswith(".Fab"):
            (x0, y0), (x1, y1) = xy(kid(rc, "start")), xy(kid(rc, "end"))
            local += [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for pg in kids(fp["node"], "fp_poly"):
        if kid(pg, "layer")[1].endswith(".Fab"):
            local += [xy(p) for p in kids(kid(pg, "pts"), "xy")]
    return np.array([(fp["x"] + lx * c + ly * s, fp["y"] - lx * s + ly * c) for lx, ly in local])


# ───────────────────────────────────────────────────────────── parts geometry


def kicad_glb(board_path: Path, tmp: Path, *flags: str, drop: tuple[str, ...] = ()) -> list[tuple]:
    """Export the board with kicad-cli and flatten it: one (ref, material, vertices,
    triangles) per primitive, vertices in board mm as x, y (south +), h (height off the
    back face; parts on the back are negative). ref is the footprint reference, or None
    for the board's own layers (body, mask, pads). drop: references left off the board."""
    if not shutil.which("kicad-cli"):
        raise SystemExit("kicad-cli not on PATH (KiCad 8+)")
    text = board_path.read_text(encoding="utf-8")
    text = text.replace("${KIPRJMOD}", str(board_path.parent)).replace("${KICAD10_3DMODEL_DIR}", KICAD_3D)
    text = model_fallbacks(text, board_path.parent)
    text = without_footprints(text, drop)
    copy = tmp / "board.kicad_pcb"
    copy.write_text(text, encoding="utf-8")
    glb = tmp / "export.glb"
    run = subprocess.run(["kicad-cli", "pcb", "export", "glb", *flags, "-f", "-o", str(glb), str(copy)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run.returncode != 0 or not glb.exists():
        raise SystemExit(f"kicad-cli glb export failed:\n{run.stdout}\n{run.stderr}")
    data = glb.read_bytes()
    jl = struct.unpack("<I", data[12:16])[0]
    g = json.loads(data[20:20 + jl])
    bin_ = data[20 + jl + 8:]

    def matrix(n):
        if "matrix" in n:
            return np.array(n["matrix"]).reshape(4, 4).T
        x, y, z, w = n.get("rotation", [0, 0, 0, 1])
        m = np.eye(4)
        m[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
        m[:3, :3] *= np.array(n.get("scale", [1, 1, 1]))
        m[:3, 3] = n.get("translation", [0, 0, 0])
        return m

    def accessor(i, width, dtype):
        a = g["accessors"][i]
        bv = g["bufferViews"][a["bufferView"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        size = np.dtype(dtype).itemsize * width
        stride = bv.get("byteStride", size)
        raw = np.frombuffer(bin_, dtype=np.uint8, count=stride * (a["count"] - 1) + size, offset=off)
        rows = np.lib.stride_tricks.as_strided(raw, shape=(a["count"], size), strides=(stride, 1))
        return np.ascontiguousarray(rows).view(dtype).reshape(a["count"], width)

    index_types = {5121: "<u1", 5123: "<u2", 5125: "<u4"}
    out = []

    def walk(i, parent_m, ref):
        n = g["nodes"][i]
        m = parent_m @ matrix(n)
        if "mesh" in n:
            for p in g["meshes"][n["mesh"]]["primitives"]:
                v = accessor(p["attributes"]["POSITION"], 3, "<f4").astype(np.float64)
                v = (np.c_[v, np.ones(len(v))] @ m.T)[:, :3] * 1000.0
                if "indices" in p:
                    a = g["accessors"][p["indices"]]
                    tri = accessor(p["indices"], 1, index_types[a["componentType"]]).reshape(-1, 3).astype(np.int64)
                else:
                    tri = np.arange(len(v)).reshape(-1, 3)
                mi = p.get("material")
                out.append((ref, None if mi is None else g["materials"][mi], v[:, [0, 2, 1]], tri))  # glTF x, y-up, z -> board x, y, h
        for c in n.get("children", []):
            walk(c, m, ref)

    for r in g["scenes"][0]["nodes"]:
        root = g["nodes"][r]
        for c in root.get("children", []):
            name = g["nodes"][c].get("name") or ""
            walk(c, matrix(root), None if name.startswith("=>") else name)
    return out


def model_fallbacks(text: str, board_dir: Path) -> str:
    """Where a footprint's model file does not exist (KiCad names library models it does
    not ship), use board/models.json's model for that footprint, as applyModels.py would
    (the repo's, whichever board this is: a trial copy, the pack's clips)."""
    table = {k: v for k, v in json.loads((HERE / "board/models.json").read_text(encoding="utf-8")).items()
             if not k.startswith("_")}
    starts = [m.start() for m in re.finditer(r"\n\t\(footprint \"", text)] + [len(text)]
    out, done = [text[:starts[0]]], []
    for a, b in zip(starts, starts[1:]):
        block = text[a:b]
        lib = re.match(r'\n\t\(footprint "([^"]+)"', block).group(1)
        model = re.search(r'\(model "([^"]+)"', block)
        spec = table.get(lib)
        if model and spec and not Path(model.group(1)).exists():
            if any(spec["offset"]) or any(spec["rotate"]):
                raise SystemExit(f"{lib}: models.json has an offset/rotation; apply it with scripts/applyModels.py")
            path = spec["path"].replace("${KIPRJMOD}", str(board_dir))
            block = block.replace(model.group(0), f'(model "{path}"', 1)
            done.append(lib.split(":")[-1])
        out.append(block)
    if done:
        print(f"  models.json stands in for missing models: {', '.join(sorted(set(done)))}")
    return "".join(out)


def without_footprints(text: str, refs: tuple[str, ...]) -> str:
    """The board's text with the footprints whose reference is in refs taken out."""
    if not refs:
        return text
    out, last, gone = [], 0, []
    for m in re.finditer(r"\n\t\(footprint \"", text):
        a = m.start()
        b_ = text.find("\n\t(", a + 1)   # the next top-level item ends the footprint
        b_ = b_ if b_ != -1 else text.rindex("\n)")
        ref = re.search(r'\(property "Reference" "([^"]+)"', text[a:b_])
        if ref and ref.group(1) in refs:
            out.append(text[last:a])
            last = b_
            gone.append(ref.group(1))
    if sorted(gone) != sorted(refs):
        raise SystemExit(f"footprints to leave off not on the board: {sorted(set(refs) - set(gone))}")
    return "".join(out) + text[last:]


def board_model(board_path: Path, tmp: Path, thickness: float, drop: tuple[str, ...] = ()) -> tuple[dict, bytes]:
    """The populated board for the preview: KiCad's own export (body, soldermask, pads,
    every part model), no silkscreen (the print is drawn by the page, per scheme)."""
    groups: dict[tuple, list] = {}
    for ref, mat, v, tri in kicad_glb(board_path, tmp, "--include-soldermask", "--include-pads", "--no-dnp", drop=drop):
        pb = (mat or {}).get("pbrMetallicRoughness", {})
        r, g_, b_, a = pb.get("baseColorFactor", [0.55, 0.55, 0.55, 1.0])
        if ref is None:
            # KiCad gives only the board layers explicit metal/roughness
            role = "pads" if pb.get("metallicFactor", 0) > 0.5 else "mask" if a < 1 else "body"
        else:
            role = "part"
        key = (role, round(r, 3), round(g_, 3), round(b_, 3))
        model = np.c_[v[:, 0], -v[:, 1], v[:, 2] - thickness]
        groups.setdefault(key, []).append((model, tri))
    header, blob = pack_mesh(groups)
    header["maskBack"] = float(min(P[:, 2].min() for (role, *_), lst in groups.items() if role == "mask" for P, _ in lst))
    return header, blob


def pack_mesh(groups: dict[tuple, list]) -> tuple[dict, bytes]:
    """Meshes grouped by (role, r, g, b), packed for the page: uint16 positions quantised
    over their box, uint16 indices, in model space (X east, Y north, Z = 0 at the board's
    front face)."""
    allp = np.vstack([P for lst in groups.values() for P, _ in lst])
    lo, hi = allp.min(0), allp.max(0)
    scale = (hi - lo) / 65535.0
    chunks, blobs, offset = [], [], 0
    for (role, r, g_, b_), lst in groups.items():
        pending: list = []

        def flush():
            nonlocal offset, pending
            if not pending:
                return
            P = np.vstack([q for q, _ in pending])
            base, idx = 0, []
            for q, t in pending:
                idx.append(t + base)
                base += len(q)
            I = np.vstack(idx).astype("<u2")
            pq = np.round((P - lo) / scale).astype("<u2").tobytes()
            ib = I.tobytes()
            pad = (-len(pq)) % 4
            chunks.append({"role": role, "color": [r, g_, b_], "vo": offset, "vc": len(P),
                           "io": offset + len(pq) + pad, "ic": int(I.size)})
            blobs.extend([pq, b"\0" * pad, ib, b"\0" * ((-len(ib)) % 4)])
            offset += len(pq) + pad + len(ib) + (-len(ib)) % 4
            pending = []

        count = 0
        for P, T in lst:
            if len(P) > 65535:
                raise SystemExit(f"a {role} primitive has {len(P)} vertices; split it before packing")
            if count + len(P) > 65535:
                flush()
                count = 0
            pending.append((P, T))
            count += len(P)
        flush()
    return {"lo": lo.tolist(), "scale": scale.tolist(), "chunks": chunks}, b"".join(blobs)


# ───────────────────────────────────────────────────────────── print artwork


# ──────────────────────────────────────────────────────────────── solids


def rr_prism(x0, y0, x1, y1, r, z0, z1):
    """Rounded-rectangle prism from board coordinates (y south) to model space (Y north)."""
    sk = RectangleRounded(x1 - x0, y1 - y0, min(r, (x1 - x0) / 2 - 1e-3, (y1 - y0) / 2 - 1e-3))
    return Pos((x0 + x1) / 2, -(y0 + y1) / 2, z0) * extrude(sk, amount=z1 - z0)


def rr_prism_drafted(x0, y0, x1, y1, r, z_wide, z_narrow, draft):
    """Rounded-rectangle prism whose sides lean in by `draft` degrees from z_wide to z_narrow."""
    sk = RectangleRounded(x1 - x0, y1 - y0, min(r, (x1 - x0) / 2 - 1e-3, (y1 - y0) / 2 - 1e-3))
    part = extrude(sk, amount=abs(z_narrow - z_wide), taper=draft)   # narrows toward +Z
    if z_narrow < z_wide:
        part = Rot(180, 0, 0) * part   # the outline is symmetric, so flipping keeps it in place
    return Pos((x0 + x1) / 2, -(y0 + y1) / 2, z_wide) * part


def box(x0, y0, x1, y1, z0, z1):
    return Pos((x0 + x1) / 2, -(y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def outline(b, grow):
    return (-grow, -grow, b["w"] + grow, b["h"] + grow, b["r"] + grow)


def wall_cut(side, b, along, z, size_along, size_z, r):
    """Rounded opening through one wall. along: centre along the wall (x for N/S, y for E/W)."""
    depth = 2 * (GAP + WALL) + 2
    sk = RectangleRounded(size_along, size_z, min(r, size_along / 2 - 1e-3, size_z / 2 - 1e-3))
    if side in ("S", "N"):
        y = b["h"] + GAP + WALL / 2 if side == "S" else -(GAP + WALL / 2)
        return Pos(along, -y, z) * Rot(90, 0, 0) * extrude(sk, amount=depth / 2, both=True)
    x = -(GAP + WALL / 2) if side == "W" else b["w"] + GAP + WALL / 2
    return Pos(x, -along, z) * Rot(0, 90, 0) * Rot(0, 0, 90) * extrude(sk, amount=depth / 2, both=True)


def hull(pts):
    """Convex hull of 2D points, counter-clockwise (Andrew's monotone chain)."""
    pts = sorted(set(map(tuple, np.round(pts, 4))))
    def half(seq):
        out = []
        for q in seq:
            while len(out) >= 2 and (out[-1][0] - out[-2][0]) * (q[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (q[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(q)
        return out
    lo, hi = half(pts), half(pts[::-1])
    return np.array(lo[:-1] + hi[:-1])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    b = read_board(BOARD)
    W, H, T = b["w"], b["h"], b["t"]
    with tempfile.TemporaryDirectory() as tmp:
        prims = kicad_glb(BOARD, Path(tmp), "--no-board-body", "--no-dnp")
    # every modelled part's vertices by reference; the board's cell clips go to the pack
    grouped: dict[str, list] = {}
    for ref, _, v, _ in prims:
        if ref and ref not in BOARD_CELL_CLIPS:
            grouped.setdefault(ref, []).append(v)
    parts = {k: np.vstack(v) for k, v in grouped.items()}
    clips_b = read_board(PACK_CLIPS)
    with tempfile.TemporaryDirectory() as tmp:
        clip_prims = [pr for pr in kicad_glb(PACK_CLIPS, Path(tmp), "--no-board-body") if pr[0] in BOARD_CELL_CLIPS]
    if {pr[0] for pr in clip_prims} != set(BOARD_CELL_CLIPS):
        raise SystemExit(f"{PACK_CLIPS.name} must hold {BOARD_CELL_CLIPS} with their models")
    if "J_PACK" not in b["fps"]:
        raise SystemExit("the board has no pack receptacle (J_PACK)")

    back = {ref: v for ref, v in parts.items() if v[:, 2].min() < -0.01}
    # not fitted (DNP: the optional edge buttons) = not there; the case gets no opening for them
    unmodelled = sorted(ref for ref, f in b["fps"].items()
                        if f["layer"] == "B.Cu" and ref not in parts and not f["dnp"] and ref not in BOARD_CELL_CLIPS
                        and not f["lib"].startswith(("TestPoint", "MountingHole")))
    surprise = [r for r in unmodelled if r not in UNMODELLED_MAX_H]
    if surprise:
        raise SystemExit(f"back-side parts with no 3D model and no height bound: {surprise}")

    cam = b["zones"]["no parts: Camera module"]
    deepest_ref, deepest = max(((r, -v[:, 2].min()) for r, v in back.items() if r not in PROTRUDE), key=lambda kv: kv[1])
    bounds = {r: UNMODELLED_MAX_H[r] for r in unmodelled}
    depth = max(deepest, *bounds.values())
    driver = deepest_ref if depth == deepest else max(bounds, key=bounds.get)

    # Z levels
    z_back = -T
    z_floor_in = z_back - depth - BACK_CLEAR
    z_floor_out = z_floor_in - FLOOR
    z_split = 0.0
    z_panel = (FOAM_T, FOAM_T + PANEL_T)
    z_lip = z_panel[1] + PANEL_GAP
    z_top = z_lip + LIP
    draft_back = math.degrees(math.atan(DRAFT_MM / 2 / (z_split - z_floor_out)))
    draft_front = math.degrees(math.atan(DRAFT_MM / 2 / (z_top - z_split)))
    if max(draft_back, draft_front) >= MAX_UNSUPPORTED:
        raise SystemExit("draft: the walls would need supports when printed")
    if WALL - DRAFT_MM / 2 < MIN_WALL:
        raise SystemExit(f"draft: the wall thins to {WALL - DRAFT_MM / 2:.2f} mm at the back/front, under {MIN_WALL}")

    px0, py0, pw, ph = panel_outline()
    panel = (px0, py0, px0 + pw, py0 + ph)
    va = (px0 + PANEL_VA[0], py0 + PANEL_VA[1], px0 + PANEL_VA[0] + PANEL_VA[2], py0 + PANEL_VA[1] + PANEL_VA[3])
    win = (va[0] - WINDOW_MARGIN, va[1] - WINDOW_MARGIN, va[2] + WINDOW_MARGIN, va[3] + WINDOW_MARGIN)

    out_o = outline(b, GAP + WALL)
    in_o = outline(b, GAP)
    tongue_o = outline(b, GAP + TONGUE_T)
    skirt_o = outline(b, GAP + TONGUE_T + TONGUE_CLR)

    report = []

    # ── openings from whatever crosses the board edge ──
    openings = []
    air = None
    for ref, v in parts.items():
        beyond = {
            "W": v[v[:, 0] < -0.05], "E": v[v[:, 0] > W + 0.05],
            "N": v[v[:, 1] < -0.05], "S": v[v[:, 1] > H + 0.05],
        }
        for side, pts in beyond.items():
            if not len(pts) or (ref, side) in [(o[0], o[1]) for o in openings]:
                continue
            zlo, zhi = z_back + pts[:, 2].min(), z_back + pts[:, 2].max()
            if ref.startswith("J1"):
                mouth = v[v[:, 1] > H - 0.5]
                cx = (mouth[:, 0].min() + mouth[:, 0].max()) / 2
                cz = z_back + (mouth[:, 2].min() + mouth[:, 2].max()) / 2
                openings.append((ref, side, wall_cut(side, b, cx, cz, USB_PLUG[0], USB_PLUG[1], USB_PLUG[2])))
                report.append(f"USB-C opening {USB_PLUG[0]} x {USB_PLUG[1]} at x {cx:.2f}, Z {cz:.2f}")
            elif ref.startswith("SW_AIR"):
                if side != "W":
                    raise SystemExit(f"{ref}: the airgap slider is drawn for the west wall, the lever leaves {side}")
                x_out = lambda z: -(GAP + WALL) + DRAFT_MM / 2 * (z_split - z) / (z_split - z_floor_out)
                lever = {"tip": pts[:, 0].min(), "y": (pts[:, 1].min(), pts[:, 1].max()), "z": (zlo, zhi)}
                air = airslider.plan(lever, b["fps"][ref]["y"], LEVER_TRAVEL, GAP, x_out, z_floor_in, z_back)
                openings.append((ref, side, airslider.wall_cuts(air)))
                report += airslider.report(air)
            else:
                raise SystemExit(f"{ref} crosses the {side} board edge and has no opening rule")

    report.append(f"draft: back and front {DRAFT_MM} mm narrower than the parting line "
                  f"({draft_back:.1f} / {draft_front:.1f} deg), wall {WALL - DRAFT_MM / 2:.2f} mm at its thinnest")

    # ── shell ──
    shell = rr_prism_drafted(*out_o[:4], out_o[4], z_split, z_floor_out, draft_back)
    shell = fillet(shell.edges().group_by(Axis.Z)[0], BACK_FILLET)
    shell -= rr_prism(*in_o[:4], in_o[4], z_floor_in, z_split + 1)
    shell += rr_prism(*tongue_o[:4], tongue_o[4], z_split, z_split + TONGUE_H) - rr_prism(*in_o[:4], in_o[4], z_split - 1, z_split + TONGUE_H + 1)

    # A screw only where the front half is solid: under the screen there is no room
    # for a boss (the screen sits on the board), so a hole there locates the board on
    # a pin instead.
    glass = (panel[0] - SCREEN_CLR, panel[1] - SCREEN_CLR, panel[2] + SCREEN_CLR, panel[3] + SCREEN_CLR)
    m_ = PILOT_R + 0.8     # a pilot needs 0.8 mm of plastic round it
    under_glass = lambda hx, hy: glass[0] - m_ < hx < glass[2] + m_ and glass[1] - m_ < hy < glass[3] + m_
    screw_holes = [(hx, hy) for hx, hy, _ in b["holes"] if not under_glass(hx, hy)]
    pin_holes = [(hx, hy) for hx, hy, _ in b["holes"] if under_glass(hx, hy)]
    if len(screw_holes) < MIN_SCREWS:
        raise SystemExit(f"only {len(screw_holes)} board hole(s) clear of the screen: the front half needs {MIN_SCREWS} screw(s)")
    if len(screw_holes) < 2:
        report.append(f"ONE SCREW only (temporary, the west hole H3 is out): the front half rests on it, "
                      f"the north hook and {len(pin_holes)} pins")
    for hx, hy, _ in b["holes"]:
        boss = Pos(hx, -hy, (z_floor_in + z_back) / 2) * Cylinder(STANDOFF_R, z_back - z_floor_in + 0.02)
        nearest = math.inf
        cut_by = []
        envs = [(r, (v[:, 0].min(), v[:, 1].min(), v[:, 0].max(), v[:, 1].max())) for r, v in back.items()]
        envs.append(("camera head", tuple(cam)))
        for ref, (ex0, ey0, ex1, ey1) in envs:
            ex0, ey0, ex1, ey1 = ex0 - BOSS_PART_CLR, ey0 - BOSS_PART_CLR, ex1 + BOSS_PART_CLR, ey1 + BOSS_PART_CLR
            dx = max(ex0 - hx, 0, hx - ex1)
            dy = max(ey0 - hy, 0, hy - ey1)
            d = math.hypot(dx, dy)
            if d < STANDOFF_R:
                boss -= box(ex0, ey0, ex1, ey1, z_floor_in + 0.01, z_back + 1)   # the floor stays whole
                raw = math.hypot(max(ex0 + BOSS_PART_CLR - hx, 0, hx - ex1 + BOSS_PART_CLR),
                                 max(ey0 + BOSS_PART_CLR - hy, 0, hy - ey1 + BOSS_PART_CLR))
                nearest = min(nearest, raw - M2_SHANK_R)
                cut_by.append(ref)
        if nearest < MIN_SHANK_CLR:
            raise SystemExit(f"standoff at {hx}, {hy}: the screw passes {nearest:.2f} mm from {cut_by}")
        shell += boss
        if (hx, hy) in pin_holes:
            # a pin through the board's hole, its top just under the board's front face
            shell += Pos(hx, -hy, (z_back + z_split - 0.1) / 2) * Cylinder(PIN_R, z_split - 0.1 - z_back + 0.02)
        else:
            # clearance hole, and the head's counterbore in the back face with a 45 deg roof (prints without support)
            shell -= Pos(hx, -hy, (z_floor_out + z_back) / 2) * Cylinder(CLEAR_R, z_back - z_floor_out + 2)
            shell -= Pos(hx, -hy, z_floor_out - 0.5 + (CB_DEPTH + 0.5) / 2) * Cylinder(CB_R, CB_DEPTH + 0.5)
            shell -= Pos(hx, -hy, z_floor_out + CB_DEPTH + (CB_R - CLEAR_R) / 2) * Cone(CB_R, CLEAR_R, CB_R - CLEAR_R)
        if cut_by:
            what = "pin" if (hx, hy) in pin_holes else "screw"
            report.append(f"standoff {hx},{hy} notched for {', '.join(cut_by)}: the {what} passes {nearest:.2f} mm from it")

    # The camera head stands through an open window: tinted plastic blurs QR, and the
    # head is taller than the back cavity.
    shell -= box(cam[0] - CAM_CLR, cam[1] - CAM_CLR, cam[2] + CAM_CLR, cam[3] + CAM_CLR, z_floor_out - 1, z_floor_in + 1)
    cam_out = z_floor_out - (z_back - CAM_HEAD_H)
    report.append(f"camera head ({CAM_HEAD_H} mm) stands {cam_out:.1f} mm out of the back through a "
                  f"{cam[2] - cam[0] + 2 * CAM_CLR:.1f} x {cam[3] - cam[1] + 2 * CAM_CLR:.1f} mm window")
    # The buzzer's port is on its top, which faces the back: a hole over it.
    if "LS1" in b["fps"]:
        lx, ly = b["fps"]["LS1"]["x"], b["fps"]["LS1"]["y"]
        shell -= Pos(lx, -ly, (z_floor_in + z_floor_out) / 2) * Cylinder(SOUND_R, FLOOR + 1)
        report.append(f"buzzer port: a {2 * SOUND_R:.1f} mm hole in the back at {lx:.1f}, {ly:.1f}")
    # North hook: a groove along the shell tongue's north face takes the bezel's ridge.
    hl, hd, hh, hc = HOOK
    hz = z_split + TONGUE_H / 2
    t_out = GAP + TONGUE_T
    shell -= box(W / 2 - hl / 2 - hc, -t_out - 0.5, W / 2 + hl / 2 + hc, -t_out + hd + hc,
                 hz - hh / 2 - hc, hz + hh / 2 + hc)
    # ── parts allowed into the floor (USB-C): a pocket over each ──
    for ref in PROTRUDE:
        v = parts[ref]
        x0, y0, x1, y1 = v[:, 0].min() - PROTRUDE_CLR, v[:, 1].min() - PROTRUDE_CLR, v[:, 0].max() + PROTRUDE_CLR, v[:, 1].max() + PROTRUDE_CLR
        z_lo = z_back + v[:, 2].min() - PROTRUDE_CLR
        if z_lo >= z_floor_in:
            continue
        skin = z_lo - z_floor_out
        through = skin < MIN_SKIN
        shell -= box(x0, y0, x1, min(y1, H + GAP + 0.01), z_floor_out - 1 if through else z_lo, z_floor_in + 0.01)
        report.append(f"{ref} ({-v[:, 2].min():.2f} mm) stands into the floor: "
                      + ("opened through" if through else f"a pocket, {skin:.2f} mm of floor left under it"))

    # ── the battery pack: high on the back, just south of the camera's view ──
    clip_boxes = sorted([tuple(np.r_[fab_world(clips_b["fps"][r]).min(0), fab_world(clips_b["fps"][r]).max(0)]) for r in BOARD_CELL_CLIPS])
    cell_cx = sum((c[0] + c[2]) / 2 for c in clip_boxes) / 2
    clip_cy = sum((c[1] + c[3]) / 2 for c in clip_boxes) / 2          # the cell's axis on today's board
    clip_half = max((c[3] - c[1]) / 2 for c in clip_boxes)

    # Z: the receptacle's contacts on the device board, the male's pins at their working
    # height on them, which puts the pack board this far out; the cells hang under it.
    z_face = z_floor_out
    z_recv = z_back - CONN_DEV["h"]                   # the receptacle's contact face
    z_pcb_top = z_recv - CONN_PACK["work"]
    z_body = z_pcb_top + CONN_PACK["body_t"]          # the male's face, in the device's cavity
    if z_body >= z_recv:
        raise SystemExit("the male's body would sit on the receptacle before its pins reach working height")
    if z_recv < z_floor_in + 0.2:
        raise SystemExit("the receptacle does not fit under the back's inside")
    out = z_face - z_pcb_top                          # the pack board stands this far off the back face
    if out < 0:
        raise SystemExit("the pair is shorter than the back is deep: the pack board would sit inside the back")
    z_pcb_bot = z_pcb_top - PACK_PCB_T
    z_cell = z_pcb_bot - CELL_BASE - CELL_D / 2
    z_pack = z_pcb_bot - CELL_BASE - CELL_D          # open: the cells' lowest line is the pack's bottom

    lens = ((cam[0] + cam[2]) / 2, (cam[1] + cam[3]) / 2)
    z_lens = z_back - CAM_HEAD_H
    cam_n = max(lens[1] + LENS_R + max(z_lens - z_pack, 0) * math.tan(math.radians(CAM_FOV / 2)),
                cam[3] + CAM_CLR + PACK_RIM)
    n = len(CONN_NETS)
    cell_pitch = 2 * clip_half + ARM_GAP             # side by side, the arms ARM_GAP apart
    jp = b["fps"]["J_PACK"]
    cx, cy = jp["x"], jp["y"]                         # the receptacle as the board places it: the pins' centre
    hr = (CONN_DEV["len"] / 2, CONN_DEV["wid"] / 2)   # long axis east-west
    hm = (CONN_PACK["len"] / 2, CONN_PACK["wid"] / 2)
    tw, tl = CONN_PACK["tab"]
    recv = (cx - hr[0], cy - hr[1], cx + hr[0], cy + hr[1])
    male = (cx - hm[0], cy - hm[1], cx + hm[0], cy + hm[1])
    tab = (cx - tw / 2, male[1] - tl, cx + tw / 2, male[1])     # its tab north, over the MCU's edge
    # east to west, as the receptacle's pads lie (pad 1, the pack's plus, east)
    contacts = [(net, cx + (1.5 - i) * CONN_DEV["pitch"], cy) for i, net in enumerate(CONN_NETS)]
    pins = [(net, cx + (1.5 - i) * CONN_PACK["pitch"], cy) for i, net in enumerate(CONN_NETS)]

    # what stands on the board's back: modelled parts, and the bounded ones by their fab outline
    near = [(r, v[:, 0].min(), v[:, 1].min(), v[:, 0].max(), v[:, 1].max(), -v[:, 2].min()) for r, v in back.items()]
    for r in unmodelled:
        fw = fab_world(b["fps"][r])
        near.append((r, *fw.min(0), *fw.max(0), UNMODELLED_MAX_H[r]))
    overlaps = lambda e, o, m: e[1] - m < o[2] and e[3] + m > o[0] and e[2] - m < o[3] and e[4] + m > o[1]
    to_move = sorted({e[0] for e in near if e[0] != "J_PACK" and overlaps(e, recv, CONN_CLR)}
                     | {e[0] for e in near if e[0] != "J_PACK" and overlaps(e, tab, CONN_CLR) and e[5] > z_back - z_body - CONN_CLR})
    if to_move:
        raise SystemExit(f"the receptacle or the male's tab lands on {to_move}")

    # North edge: outside a CAM_FOV cone from the lens's rim, at the pack's farthest face.
    pack_n = cam_n
    pcb_n = pack_n + PACK_RIM
    cell_ys = (pcb_n + PACK_LEDGE + PACK_CLR + clip_half, pcb_n + PACK_LEDGE + PACK_CLR + clip_half + cell_pitch)
    small = [(cx + dx - w / 2, cy + dy - h / 2, cx + dx + w / 2, cy + dy + h / 2, t, label) for dx, dy, w, h, t, label in PACK_SMALL]
    south = max(cell_ys[1] + clip_half + PACK_CLR + PACK_LEDGE, male[3] + 0.5, max(sm[3] for sm in small) + 0.5)
    pcb = (0.0, pcb_n, W, south)
    face = outline(b, GAP + WALL - DRAFT_MM / 2)
    pack_o = (face[0], pack_n, face[2], pcb[3] + PACK_RIM)
    north_by = "the camera's 70 deg cone"

    def under_can(x0, y0, x1, y1):
        """The least gap between the cells' cans and the board over this box (inf if no can is over it)."""
        least = math.inf
        for y in cell_ys:
            d = 0.0 if y0 < y < y1 else min(abs(y0 - y), abs(y1 - y))
            if d < CELL_D / 2:
                least = min(least, CELL_BASE + CELL_D / 2 - math.sqrt((CELL_D / 2) ** 2 - d ** 2))
        return least
    for x0, y0, x1, y1, t, label in small:
        if under_can(x0, y0, x1, y1) - t < SMALL_CLR:
            raise SystemExit(f"the pack's {label} stands under a cell's can with under {SMALL_CLR} mm to spare")
    # the male's joints on the inner face: flat, clear of the clips' steel and under the cans
    steel = pack_steel(clip_prims, clip_cy, cell_cx)
    pad_r = CONN_PACK["pad_d"] / 2
    for i, y in enumerate(cell_ys):
        for x0, dy0, x1, dy1 in steel[i]:
            for net, px, py in pins:
                d = math.hypot(max(x0 - px, 0, px - x1), max(y + dy0 - py, 0, py - y - dy1)) - pad_r
                if d < CONN_JOINT_CLR:
                    raise SystemExit(f"the male's {net} joint is {d:.2f} mm from a clip's steel")
    joint_gap = min(under_can(px - pad_r, py - pad_r, px + pad_r, py + pad_r) for _, px, py in pins)
    if joint_gap < SMALL_CLR:
        raise SystemExit(f"the male's joints sit {joint_gap:.2f} mm under a can")
    if clip_boxes[0][0] < pcb[0] + PACK_CLR or clip_boxes[-1][2] > pcb[2] - PACK_CLR:
        raise SystemExit("the clips run past the pack board's ends")
    if "LS1" in b["fps"] and pack_o[1] - SOUND_R < b["fps"]["LS1"]["y"] < pack_o[3] + SOUND_R:
        raise SystemExit("the pack covers the buzzer's port")
    for r_ in (male, tab):
        if r_[0] < pcb[0] + 0.5 or r_[2] > pcb[2] - 0.5 or r_[1] < pcb[1] + 0.5 or r_[3] > pcb[3] - 0.5:
            raise SystemExit(f"the male at {cx}, {cy:.2f} runs off the pack board")

    # the device's back: the male's window (its body and tab) down into the cavity
    male_win = (male[0] - CONN_CLR, male[1] - CONN_CLR, male[2] + CONN_CLR, male[3] + CONN_CLR)
    shell -= rr_prism(*male_win, (male_win[2] - male_win[0]) / 2 - 0.01, z_face - 1, z_floor_in + 1)
    shell -= box(tab[0] - CONN_CLR, tab[1] - CONN_CLR, tab[2] + 0.01, tab[3] + CONN_CLR, z_face - 1, z_floor_in + 1)

    # ── the pack's tray (print part): a frame round the cells, open underneath; the pack
    # board sits on its north and south walls, `out` below its top
    cav = (pcb[0], pcb[1] + PACK_LEDGE, pcb[2], pcb[3] - PACK_LEDGE)
    tray = rr_prism(*pack_o, PACK_R, z_pack, z_face)
    tray = fillet(tray.edges().group_by(Axis.Z)[0], PACK_FILLET)
    tray -= box(*cav, z_pack - 1, z_face + 1)
    tray -= box(pcb[0], pcb[1] - FIT / 2, pcb[2], pcb[3] + FIT / 2, z_pcb_bot, z_face + 1)

    report.append(f"the pack {pack_o[2] - pack_o[0]:.1f} x {pack_o[3] - pack_o[1]:.1f} x {z_face - z_pack:.2f} mm, open underneath, "
                  f"y {pack_o[1]:.1f}-{pack_o[3]:.1f} ({north_by} sets its north edge): "
                  f"{z_top - z_pack:.2f} mm with the device")
    report.append(f"the pack's board {pcb[2] - pcb[0]:.1f} x {pcb[3] - pcb[1]:.1f} x {PACK_PCB_T} mm, {out:.2f} mm off the back face "
                  f"inside the tray's rim; cells at y {cell_ys[0]:.2f} and {cell_ys[1]:.2f} ({cell_pitch:.2f} apart, the arms {ARM_GAP:.1f} mm apart); "
                  f"the male's joints {joint_gap:.2f} mm under the south cell's can")
    report.append(f"contacts: receptacle {CONN_DEV['part']} ({CONN_DEV['h']} mm) on the device board at {cx}, {cy:.2f}, male "
                  f"{CONN_PACK['part']} on the pack (tab north), its face {z_recv - z_body:.2f} mm off the receptacle mated, "
                  f"pins at {CONN_PACK['work']} mm; contacts " + ", ".join(f"{n_} {x:.2f}" for n_, x, _ in contacts) + " (x)")

    for _, _, cut in openings:
        shell -= cut
    if air is None:
        raise SystemExit("no airgap switch lever crosses the board edge: nothing for the slider to move")
    shell += airslider.guards(air)
    air_slider = airslider.slider(air)

    # ── bezel ──
    bezel = rr_prism_drafted(*out_o[:4], out_o[4], z_split, z_top, draft_front)
    bezel = fillet(bezel.edges().group_by(Axis.Z)[-1], FRONT_FILLET)
    bezel -= rr_prism(*in_o[:4], in_o[4], z_split - 1, z_lip)
    bezel -= rr_prism(*skirt_o[:4], skirt_o[4], z_split - 1, z_split + TONGUE_H + TONGUE_CLR)
    bezel -= rr_prism(*win, 0.8, z_lip - 1, z_top + 1)
    # The north hook's ridge on the skirt's inner face (the bezel prints face down: the
    # ridge's underside is a 0.3 mm step, no support).
    s_in = t_out + TONGUE_CLR
    bezel += box(W / 2 - hl / 2, -s_in, W / 2 + hl / 2, -s_in + hd, hz - hh / 2, hz + hh / 2)
    # Solid where the screen is not: fills along the screen's edges locate it, the chin
    # is solid down to the board, with windows where the display ribbons drop through
    # the board's slots and a pocket over each front part.
    open_ = box(*glass, z_split - 1, z_lip + 0.01)
    for sx0, sy0, sx1, sy1 in b["slots"]:
        open_ += box(sx0 - RIBBON_CLR, glass[3] - 0.01, sx1 + RIBBON_CLR, sy1 + RIBBON_CLR, z_split - 1, z_lip + 0.01)
    bezel += rr_prism(*in_o[:4], in_o[4], z_split, z_lip + 0.01) - open_
    bezel -= (rr_prism(*in_o[:4], in_o[4], z_split - 1, z_split + TONGUE_H + FIT)
              - rr_prism(*outline(b, GAP - FIT)[:4], outline(b, GAP - FIT)[4], z_split - 2, z_split + TONGUE_H + 2))
    joy = b["fps"].get(JOY_REF)
    joy_box = None
    if joy is not None:
        if joy["layer"] != "F.Cu":
            raise SystemExit(f"{JOY_REF}: the joystick must be on the board's front face")
        h = JOY_BODY / 2 + JOY_CLR
        joy_box = (joy["x"] - h, joy["y"] - h, joy["x"] + h, joy["y"] + h)
        if JOY_HOUSING_H + JOY_CLR >= z_top - 0.8:
            raise SystemExit("no room for the joystick's housing under the bezel's front skin")
        bezel -= box(*joy_box, z_split - 1, JOY_HOUSING_H + JOY_CLR)
        bezel -= Pos(joy["x"], -joy["y"], (z_split + z_top) / 2 + 0.5) * Cylinder(JOY_HOLE_D / 2, z_top - z_split + 2)
        if JOY_HOLE_D != joycap.HOLE_D or JOY_H != joycap.STEM_TOP:
            raise SystemExit("joycap.py's hole and stem must match JOY_HOLE_D and JOY_H")
        bezel -= joycap.bezel_well(joy["x"], joy["y"], z_top)
        joy_cap = joycap.joystick_cap(joy["x"], joy["y"], z_top)
        report += joycap.cap_report(z_top)
        report.append(f"joystick at {joy['x']:.1f}, {joy['y']:.1f}: pocket {2 * h:.1f} mm square, a {JOY_HOLE_D} mm hole; "
                      f"its stem stops {z_top - JOY_H:.2f} mm under the front face, under the printed cap")
    front = sorted(ref for ref, f in b["fps"].items()
                   if f["layer"] == "F.Cu" and not f["dnp"] and ref != JOY_REF
                   and not f["lib"].startswith(("TestPoint", "MountingHole")))
    keepouts, chin_h = [], 0.0   # each front part's fab outline plus FRONT_CLR; the tallest part
    for ref in front:
        if ref in parts:
            h = parts[ref][:, 2].max() - T      # parts' z is 0 at the board's back face
        elif ref in UNMODELLED_MAX_H:
            h = UNMODELLED_MAX_H[ref]
        else:
            raise SystemExit(f"front part {ref} has no 3D model and no height bound")
        fw = fab_world(b["fps"][ref])
        if fw.size == 0:
            raise SystemExit(f"front part {ref} has no fab outline to size the chin's pocket from")
        x0, y0 = fw.min(0)[:2]
        x1, y1 = fw.max(0)[:2]
        keepouts.append((x0 - FRONT_CLR, y0 - FRONT_CLR, x1 + FRONT_CLR, y1 + FRONT_CLR))
        chin_h = max(chin_h, h)
    if front:
        # One pocket over the whole chin (owner, 2026-10-26: not one per part), from the
        # northmost part to the south wall, as deep as the tallest part plus FRONT_CLR;
        # the screw boss stays solid through it.
        depth = chin_h + FRONT_CLR
        if depth > z_top - FRONT_SKIN:
            raise SystemExit(f"the chin's tallest front part ({chin_h:.2f} mm) leaves under {FRONT_SKIN} mm of bezel over it")
        north = min(k[1] for k in keepouts + ([joy_box] if joy_box else []))
        if north < glass[3]:
            raise SystemExit(f"a front part reaches {glass[3] - north:.2f} mm north of the screen's bottom edge: "
                             "the chin's pocket would cut the screen's seat")
        bezel -= rr_prism(*in_o[:4], in_o[4], z_split - 1, depth) & box(in_o[0] - 1, north, in_o[2] + 1, in_o[3] + 1,
                                                                      z_split - 2, depth + 1)
        for hx, hy in screw_holes:
            if hy < north:
                continue
            hit = [ref for ref, (x0, y0, x1, y1) in zip(front, keepouts)
                   if math.hypot(max(x0 - hx, 0, hx - x1), max(y0 - hy, 0, hy - y1)) < BOSS_R]
            if hit:
                raise SystemExit(f"screw boss at {hx}, {hy} (r {BOSS_R}) runs into {', '.join(hit)}")
            bezel += Pos(hx, -hy, (z_split + depth) / 2) * Cylinder(BOSS_R, depth - z_split)
        report.append(f"one pocket under the bezel's chin, {depth:.2f} deep ({FRONT_CLR} over the tallest part), "
                      f"{z_top - depth:.2f} mm of bezel over it, from y {north:.1f} to the south wall, "
                      f"a boss round the screw; it covers {', '.join(front)}")

    # Screw bosses: the chin is solid from the board up to the front skin there; the
    # pilot runs up to PILOT_TOP_CLR under the front face.
    pilot_top = z_top - PILOT_TOP_CLR
    for hx, hy in screw_holes:
        wall = min(math.hypot(max(o0 - hx, 0, hx - o1), max(p0 - hy, 0, hy - p1)) for o0, p0, o1, p1 in
                   [glass] + [(sx0 - RIBBON_CLR, glass[3], sx1 + RIBBON_CLR, sy1 + RIBBON_CLR)
                                    for sx0, sy0, sx1, sy1 in b["slots"]]) - PILOT_R
        if wall < 0.8:
            raise SystemExit(f"screw boss at {hx}, {hy}: only {wall:.2f} mm of plastic beside the pilot")
        bezel -= Pos(hx, -hy, (z_split - 0.01 + pilot_top) / 2) * Cylinder(PILOT_R, pilot_top - z_split + 0.01)

    # The screw: the longest stock M2 whose tip stays in the pilot.
    seat = z_floor_out + CB_DEPTH
    fits = [L for L in SCREW_LENGTHS if seat + L <= pilot_top]
    if not fits:
        raise SystemExit("no stock M2 length fits between the counterbore and the pilot")
    screw = max(fits)
    thread = seat + screw - z_split
    if thread < MIN_ENGAGE:
        raise SystemExit(f"M2x{screw}: only {thread:.2f} mm of thread in the bezel's bosses")
    report.append(f"screws: {len(screw_holes)}x M2x{screw} pan head from the back, {thread:.1f} mm of thread "
                  f"in the bezel; locating pins at {len(pin_holes)} holes under the screen; north hook {hl:.0f} mm")
    for _, _, cut_ in openings:
        bezel -= cut_

    # ── checks that the geometry, not prose, has to satisfy ──
    if win[0] < panel[0] or win[2] > panel[2] or win[1] < panel[1] or win[3] > panel[3]:
        raise SystemExit("display window is larger than the glass")
    assert shell.is_valid and bezel.is_valid and tray.is_valid and joy_cap.is_valid and air_slider.is_valid, "invalid solid"

    size = (out_o[2] - out_o[0], out_o[3] - out_o[1], z_top - z_floor_out)   # the device; the pack adds to it
    print_3mf(shell, bezel, tray, joy_cap, air_slider, OUT / "case.3mf")
    for name, part in (("shell", shell), ("bezel", bezel), ("tray", tray), ("joycap", joy_cap), ("airslider", air_slider)):
        export_step(part, str(OUT / f"{name}.step"))
        export_stl(part, str(OUT / f"{name}.stl"), tolerance=MESH_TOL, angular_tolerance=0.2)
        glb = OUT / f"{name}.glb"
        export_gltf(part, str(glb), binary=True, linear_deflection=0.05, angular_deflection=0.3)
        gltf_json(glb, WEB / f"{name}.json")
        glb.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        header, blob = board_model(BOARD, Path(tmp), T)
    header["bin"] = base64.b64encode(blob).decode()
    (WEB / "board.json").write_text(json.dumps(header, separators=(",", ":")), encoding="utf-8")
    hw, hw_blob = pack_hardware(clip_prims, T, clip_cy, cell_cx, cell_ys, (cx, cy), z_pcb_bot, z_back, pcb, (male, tab, pins))
    hw["bin"] = base64.b64encode(hw_blob).decode()
    (WEB / "pack.json").write_text(json.dumps(hw, separators=(",", ":")), encoding="utf-8")

    fbox = flame_art.box(flame_art.facets())
    at = flame_art.placement(W, H, fbox)
    flame = {"paths": flame_art.facet_paths(), "box": [round(v, 3) for v in fbox],
             "at": [round(v, 3) for v in at], "gap": flame_art.GAP}
    fw, fh = at[2] - at[0], at[3] - at[1]
    report.append(f"flame {fw:.1f} x {fh:.1f} mm across the back, mask back face at Z {header['maskBack']:.3f}")

    downloads = write_downloads()
    r3 = lambda seq: [round(v, 3) for v in seq]
    data = {
        "generated_from": "board/wallet.kicad_pcb",
        "size_mm": [round(v, 1) for v in size],
        "board": {"w": W, "h": H, "r": b["r"], "t": T, "slots": b["slots"], "holes": b["holes"]},
        "z": {"floorOut": z_floor_out, "floorIn": z_floor_in, "boardBack": z_back, "split": z_split,
              "pack": z_pack, "panel": list(z_panel), "lip": z_lip, "top": z_top},
        "panel": list(panel), "viewingArea": list(va),
        "pack": {"outline": r3(pack_o), "r": PACK_R, "board": r3(pcb), "boardT": PACK_PCB_T,
                 "cells": {"x": r3([cell_cx - CELL_L / 2, cell_cx + CELL_L / 2]), "y": r3(cell_ys),
                           "z": round(z_cell, 3), "d": CELL_D},
                 "receptacle": {"part": CONN_DEV["part"], "lcsc": CONN_DEV["lcsc"], "outline": r3(recv), "h": CONN_DEV["h"],
                                "contacts": [{"net": n_, "x": round(x, 3), "y": round(y, 3)} for n_, x, y in contacts]},
                 "male": {"part": CONN_PACK["part"], "lcsc": CONN_PACK["lcsc"], "outline": r3(male), "tab": r3(tab)},
                 "clipSteel": [r3([x0, y + dy0, x1, y + dy1]) for i, y in enumerate(cell_ys) for x0, dy0, x1, dy1 in steel[i]],
                 "toMove": to_move,
                 "z": {"face": z_face, "boardTop": z_pcb_top, "bottom": z_pack}},
        "camera": cam,
        "depthDriver": driver,
        "parts": {"shell": "enclosure/shell.json", "bezel": "enclosure/bezel.json", "tray": "enclosure/tray.json",
                  "joycap": "enclosure/joycap.json", "airslider": "enclosure/airslider.json"},
        "board3d": "enclosure/board.json",
        "pack3d": "enclosure/pack.json",
        "screws": {"length": screw, "d": 2.0, "head": [3.8, 1.6], "seat": seat,
                   "holes": [[hx, hy] for hx, hy in screw_holes]},
        "maskBack": header["maskBack"],
        "flame": flame,
        "schemes": [sc for sc in PRINT_SCHEMES if sc.get("offered", True)],
        "downloads": downloads,
    }
    (WEB / "enclosure.json").write_text(json.dumps(data, indent=1))

    print(f"device {size[0]:.1f} x {size[1]:.1f} x {size[2]:.2f} mm, {z_top - z_pack:.2f} with the pack on "
          f"(back cavity {depth + BACK_CLEAR:.1f} mm, set by the {driver}; deepest modelled part {deepest_ref} {deepest:.2f} mm)")
    for line in report:
        print(" ", line)
    print(f"  unmodelled back parts (height bounds): {', '.join(unmodelled) or 'none'}")
    print(f"wrote {OUT.relative_to(HERE)}/ and {WEB.relative_to(HERE)}/")


def tessellated(shape, tol=0.02) -> tuple[np.ndarray, np.ndarray]:
    vs, ts = shape.tessellate(tol, 0.3)
    return np.array([[v.X, v.Y, v.Z] for v in vs]), np.array(ts, dtype=np.int64)


def pack_steel(clip_prims, clip_cy, cell_cx) -> list[list[tuple]]:
    """The clips' steel lying on the pack board, per cell (north, south), placed as
    pack_hardware places them: for each clip the foot's body and its tab, as rectangles
    (x0, dy0, x1, dy1) with dy off the cell's axis (board y, south positive)."""
    V = np.vstack([v for _, _, v, _ in clip_prims])
    V = V[V[:, 2] > -LOW_STEEL]                      # the clips stand on the board's back, z 0
    out = []
    for i in (0, 1):
        x, dy = V[:, 0], V[:, 1] - clip_cy
        if i == 0:                                   # the north cell, turned end for end
            x, dy = 2 * cell_cx - x, -dy
        rects = []
        for side in (x < cell_cx, x >= cell_cx):
            for part in (np.abs(dy) <= CLIP_BODY_HALF, np.abs(dy) > CLIP_BODY_HALF):
                m = side & part
                if m.any():
                    rects.append((x[m].min(), dy[m].min(), x[m].max(), dy[m].max()))
        out.append(rects)
    return out


def pack_hardware(clip_prims, T, clip_cy, cell_cx, cell_ys, pins_c, z_pcb_bot, z_back, pcb, conn) -> tuple[dict, bytes]:
    """The pack's board for the preview, packed like board.json: the board (its mask
    follows the print scheme), the magnetic male on its outer face with its pins at
    working height, the board's own clip models moved onto its inner face (the north
    cell turned end for end: the cells are in series), the cells and the small parts."""
    groups: dict[tuple, list] = {}
    add = lambda key, shape: groups.setdefault(key, []).append(tessellated(shape))
    dz = z_pcb_bot - z_back
    for i, y in enumerate(cell_ys):
        for _, mat, v, tri in clip_prims:
            P = np.c_[v[:, 0], -(v[:, 1] + y - clip_cy), v[:, 2] - T + dz]
            if i == 0:
                P[:, 0], P[:, 1] = 2 * cell_cx - P[:, 0], -2 * y - P[:, 1]
            r, g_, b_, _ = ((mat or {}).get("pbrMetallicRoughness", {})).get("baseColorFactor", [0.7, 0.7, 0.7, 1])
            groups.setdefault(("part", round(r, 3), round(g_, 3), round(b_, 3)), []).append((P, tri))
    z_top_ = z_pcb_bot + PACK_PCB_T
    add(("mask", 0, 0, 0), rr_prism(*pcb, 1.0, z_pcb_bot, z_top_))
    body, tab, pins = conn
    z_face_ = z_top_ + CONN_PACK["body_t"]
    add(("part", 0.09, 0.09, 0.1), rr_prism(*body, min(body[2] - body[0], body[3] - body[1]) / 2 - 0.01, z_top_, z_face_))
    add(("part", 0.09, 0.09, 0.1), box(*tab, z_top_, z_face_))
    cx, cy = (body[0] + body[2]) / 2, (body[1] + body[3]) / 2
    for s_ in (-1, 1):   # its own magnets, in its ends
        mx, my = (cx + s_ * 7.7, cy) if body[2] - body[0] > body[3] - body[1] else (cx, cy + s_ * 7.7)
        add(("part", 0.55, 0.56, 0.6), Pos(mx, -my, z_face_ + 0.01) * Cylinder(CONN_PACK["wid"] / 2 - 1.0, 0.02))
    for _, px, py in pins:
        add(("pads", 0, 0, 0), Pos(px, -py, z_face_ + 0.15) * Cylinder(CONN_PACK["barrel_d"] / 2, 0.3))
        add(("pads", 0, 0, 0), Pos(px, -py, (z_face_ + z_top_ + CONN_PACK["work"]) / 2)
            * Cylinder(CONN_PACK["plunger_d"] / 2, z_top_ + CONN_PACK["work"] - z_face_))
    z_cell = z_pcb_bot - CELL_BASE - CELL_D / 2
    for i, y in enumerate(cell_ys):
        add(("part", 0.86, 0.87, 0.9), Pos(cell_cx, -y, z_cell) * Rot(0, 90, 0) * Cylinder(CELL_D / 2, CELL_L - 1.0))
        end = cell_cx + (-1 if i == 0 else 1) * (CELL_L / 2 - 0.5)   # the + terminal: west on the north cell, east on the south
        add(("part", 0.62, 0.63, 0.66), Pos(end, -y, z_cell) * Rot(0, 90, 0) * Cylinder(1.9, 1.0))
    for dx, dy, w, h, t, label in PACK_SMALL:
        colour = (0.78, 0.62, 0.22) if label.startswith("PTC") else (0.13, 0.13, 0.15)
        add(("part", *colour), box(pins_c[0] + dx - w / 2, pins_c[1] + dy - h / 2, pins_c[0] + dx + w / 2, pins_c[1] + dy + h / 2,
                                   z_pcb_bot - t, z_pcb_bot))
    return pack_mesh(groups)


def gltf_json(glb: Path, dest: Path):
    """GLB bytes as base64 in a .json file: the artifact host serves .json but not .glb,
    and its CSP blocks fetching data: URIs, so the page parses the GLB in memory."""
    dest.write_text(json.dumps({"glb": base64.b64encode(glb.read_bytes()).decode()}))


def print_3mf(shell, bezel, tray, cap, slider, dest: Path):
    """The parts laid out for printing in a row: the shell on its back, the bezel face
    down, the tray on its top face, the joystick's cap standing on its sleeve, the airgap
    slider on its flange."""
    m = Mesher()
    x = 0.0
    for name, part in (("shell", shell), ("bezel", Rot(180, 0, 0) * bezel), ("tray", Rot(180, 0, 0) * tray), ("joycap", cap),
                       ("airslider", airslider.print_pose(slider))):
        bb = part.bounding_box()
        m.add_shape(Pos(x - bb.min.X, -bb.min.Y, -bb.min.Z) * part, linear_deflection=MESH_TOL, angular_deflection=0.2, part_number=name)
        x += bb.size.X + 8
    m.write(str(dest))


FAB_SETS = {
    "jlc": [("Gerbers", ORDER / "jlc-plain/wallet_gerbers.zip"),
            ("Bill of materials", ORDER / "jlc-plain/wallet_bom-jlc.csv"),
            ("Pick and place", ORDER / "jlc-plain/wallet_positions-jlc.csv")],
    "pcbway": [("Gerbers", ORDER / "jlc-plain/wallet_gerbers.zip"),
               ("Bill of materials", ORDER / "pcbway/wallet_bom-pcbway.csv"),
               ("Pick and place", ORDER / "pcbway/wallet_centroid-pcbway.csv")],
}
PACK_FILES = [("Gerbers", PACK_ORDER / "pack_gerbers.zip"),
              ("Bill of materials", PACK_ORDER / "pack_bom-jlc.csv"),
              ("Pick and place", PACK_ORDER / "pack_positions-jlc.csv")]


def write_downloads() -> dict:
    """The DIY files for the page. The host serves text only: CSVs go as they are,
    binaries (zip) as base64 .txt; the page rebuilds each file and hands it to the
    viewer's downloads capability."""
    files_dir = WEB / "files"
    if files_dir.exists():
        shutil.rmtree(files_dir)
    files_dir.mkdir()

    def entry(label, src: Path):
        raw = src.read_bytes()
        if src.suffix == ".csv":
            part, b64 = src.name, False
            (files_dir / part).write_bytes(raw)
        else:
            part, b64 = src.name + ".b64.txt", True
            (files_dir / part).write_text(base64.b64encode(raw).decode())
        return {"label": label, "filename": src.name, "bytes": len(raw), "part": f"enclosure/files/{part}", "b64": b64}

    # The viewer saves only allowlisted types (zip, csv, ...), so the case goes as one zip.
    case_zip = OUT / "ambire-ice-case.zip"
    with zipfile.ZipFile(case_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("case.3mf", "shell.stl", "bezel.stl", "tray.stl", "joycap.stl", "airslider.stl",
                     "shell.step", "bezel.step", "tray.step", "joycap.step", "airslider.step"):
            z.write(OUT / name, name)
        z.writestr("README.txt", CASE_README)
    return {"fabs": {fab: [entry(l, p) for l, p in files] for fab, files in FAB_SETS.items()},
            "pack": [entry(l, p) for l, p in PACK_FILES],
            "case": [entry("Case and battery pack: 3MF, STL and STEP (zip)", case_zip)]}

if __name__ == "__main__":
    sys.exit(main())
