#!/usr/bin/env python3
"""Create board/wallet.kicad_pcb from scratch: outline, display-tail slots,
4 copper layers, GND pours, net classes, the radio antenna keep-out, every
footprint from the netlist, and the starting placement.

    $KICAD_PY scripts/newBoard.py [--force]

One-shot. Once the board has been hand-placed or routed, use syncBoard.py for
netlist changes; this refuses to overwrite an existing board without --force.

Coordinates are KiCad's (x right, y DOWN), mm, board corner at (0, 0), seen from
the FRONT (the display side). Every part is on B.Cu: the back face is the face
the clear case shows, and the front face carries only the pouch cell and, above
it, the display.

STACK (front to back): display glass / Newhaven NHD-2.4 panel (4.13 mm) / pouch
cell (~4 mm) / board / parts / clear back.

OUTLINE 48 x 85 mm. The panel (42.80 x 59.91) covers y 1.0..60.91 with 2.6 mm
each side. Its two tails leave the panel's south edge, drop through two slots
just south of it and turn north under the board into connectors on the back:
the derivation of contact faces and pin order is in docs/design.md and is
re-checked on the placed board by scripts/checkFpc.py. South of the slots is the
chin: USB-C, the radio module (antenna west, clear of the display's metal back),
the charger and the cell connector.
"""
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import netlist  # noqa: E402
import syncBoard  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
PRO = HERE / "board/wallet.kicad_pro"

# 85 tall since the routing pass: the chin got 3 mm so the buck-boost, charger
# and USB parts keep routing gaps between the module, USB-C and the cell connector.
W, H, R = 48.0, 85.0, 4.0
PANEL = (2.6, 1.0, 42.80, 59.91)              # x, y, w, h (Newhaven rev 4 drawing)
PANEL_S = PANEL[1] + PANEL[3]                 # 60.91: the panel's south edge
TFT_TAIL = (PANEL[0] + 9.25, 20.50)           # x of the tail's west edge, width
CTP_TAIL = (PANEL[0] + PANEL[2] - 1.90 - 7.00, 7.00)
SLOT_Y0, SLOT_Y1 = PANEL_S + 1.0, PANEL_S + 2.6   # 1.6 mm wide slots
SLOT_MARGIN = 0.75                            # tail-to-slot clearance each side
# The south-west corner belongs to the radio antenna, so the west pair sits at
# the panel's corners instead of the board's.
HOLES = [(3.2, 3.2), (44.8, 3.2), (3.2, 59.3), (44.8, H - 3.2)]
HOLE_RING = 2.6      # no parts within this of a hole centre: an M2 head is ~3.8 mm across
ANT_KEEPOUT = (0.0, SLOT_Y1, 3.7, H)          # E73 antenna end up to its first pad row, all layers

# Tail length budget (Newhaven rev 4 drawing: TFT tail 30.00 and touch tail 55.85
# below the panel outline). Each tail runs in-plane to the slot, drops through
# the cell + board, and runs north on the back face until its stiffened end sits
# over the connector's contact row. The touch flex leaves from the touch glass,
# which sits on top of the TFT, so it drops further. scripts/checkFpc.py checks
# the placed connectors against this budget.
CELL_T, ADHESIVE_T, BOARD_T = 4.0, 0.3, 1.0   # pouch (e.g. 403040), foam tape, PCB
TFT_LEN, CTP_LEN = 30.00, 55.85
CTP_ABOVE_TFT = 2.55 + 0.175                  # TFT module + adhesive under the touch glass
SLOT_C = (SLOT_Y0 + SLOT_Y1) / 2
TFT_DROP = CELL_T + ADHESIVE_T + BOARD_T
CTP_DROP = TFT_DROP + CTP_ABOVE_TFT
TFT_END_Y = SLOT_C - (TFT_LEN - (SLOT_C - PANEL_S) - TFT_DROP)
CTP_END_Y = SLOT_C - (CTP_LEN - (SLOT_C - PANEL_S) - CTP_DROP)
TFT_ROW, CTP_ROW = 1.35, 1.05                 # connector origin to its contact row (north of origin)
TFT_SLACK = 2.5      # mm of TFT tail left as a gentle bow, buying a 4.6 mm routing channel south of the MCU (checkFpc allows 3)
GAP = 0.5            # free board kept between courtyards: a via, or two 0.15 mm tracks
OPT_RADIUS = 7.0     # how far the ratsnest pass may move a part from its seed (mm)
# Footprint keep-outs on the back face (KiCad rule areas: DRC flags any part moved
# into one, and legalize() never puts a part there). What lies over each:
#   display tails  the two flex tails run flat over the back face from slot to connector
#   camera         the module head (lens out through the case) and its flex to J_CAM
#   UV artwork     the full-colour brand panel
#   holes          screw heads / bosses, 3 mm round each mounting hole
CAMERA_HEAD = (4.0, 4.5, 12.5, 13.0)
# The camera tail runs from the head's east edge into J_CAM. Stock OV2640 modules
# are 20.5-21 mm overall with an 8.5 mm head (Union ZY-XSF3M2640B drawing, Arducam
# M0031), so 12.0-12.5 mm of flex; J_CAM takes ~7.8, so a stock module bows ~4.5 mm
# (a ~16.5 mm module, a custom FPC length, lies flat). J_CAM is NOT moved east to
# take a stock tail flat: its 18.7 mm body would then sit over the MCU's north pin
# row, and the camera pins there (XCLK, VSYNC, D6, D7, RST) that now run straight
# north into the fan-in would each need a via. CAM_END_DX: how far east of J_CAM's
# origin the inserted flex end lands, an estimate from the AFC07 fab outline (check
# it with the module in hand). scripts/checkFpc.py holds J_CAM to this budget.
CAM_TAIL = (12.0, 12.5)
CAM_MAX_BOW = 5.0
CAM_END_DX = 0.65
# Bare board kept east of J_CAM's contacts so all 24 lines can fan in (a via
# column or two and the tracks between them): no parts there.
CAM_FANIN = 3.1
UV_PANEL = (34.0, 4.5, 46.0, 13.5)
# Room kept for printed labels that name a part (the UV art restyles them in
# place): the placer and the ratsnest pass treat these as occupied. Not KiCad
# rule areas; they only stop the generator from filling the spot.
LABEL_SLOTS = {"SECURE ELEMENT": (1.2, 36.3, 12.2, 37.7)}


def footprint_keepouts(board):
    """The keep-out boxes, starting where each placed connector's courtyard ends,
    so a connector never sits in its own tail's keep-out."""
    fp = board.FindFootprintByReference
    tft, ctp, cam = (_box(fp(r)) for r in ("J_TFT", "J_CTP", "J_CAM"))
    # The camera / SE pins on the MCU's west side (51-67) leave west and drop to the
    # front copper right outside their pads: that strip holds their escape vias, so
    # no parts (the VDD decaps sit just outside it, at pins 51 and 73/75).
    u4 = fp("U4")
    west = [p for p in u4.Pads() if p.GetNumber().isdigit() and 52 <= int(p.GetNumber()) <= 67]
    edge = _box(u4)[0]          # the MCU courtyard's west edge, just outside the pad tips
    wy = [pcbnew.ToMM(p.GetPosition().y) for p in west]
    # The display tails are NOT keep-outs: low parts (passives, SOT/QFN ICs) may
    # sit under the flex; scripts/checkFpc.py fails a tall part in a corridor.
    return [
        ("Camera module", CAMERA_HEAD),
        ("Camera flex", (CAMERA_HEAD[2], 2.2, cam[0] - 0.1, 15.3)),
        ("Camera fan-in", (cam[2], 0.3, cam[2] + CAM_FANIN, 15.5)),
        ("MCU west escape", (edge - 2.4, min(wy) - 0.3, edge, max(wy) + 0.1)),
        ("UV artwork", UV_PANEL),
    ]

TOP, BOT = "top", "bottom"
PLACE = {}


def place(ref, x, y, rot=0, side=BOT):
    PLACE[ref] = (x, y, rot, side)


# Seed placement only: legalize() keeps GAP between courtyards so the first
# trial route has room. The owner's hand placement packs related parts with
# courtyards touching, and that is the reference now; routability is judged by
# a trial route, never by spacing. The front copper carries no parts at all and
# is the free routing layer.

# ── MCU, centre of the back face ────────────────────────────────────────────
# KiCad flips a footprint to the back by mirroring it top-to-bottom (y), then
# applies the rotation. Flipped and turned 180 deg, the LQFP100 has pins 1-25
# down the EAST side (pin 1 north-east), 26-50 along the SOUTH (east to west:
# display SPI 37-43 faces the TFT connector), 51-75 up the WEST side (secure
# element I2C4 at 58-60, radio USART1 at 68/69), 76-100 along the NORTH
# (camera). scripts/checkFpc.py asserts the connector orientations relied on.
MX, MY = 22.0, 28.0
place("U4", MX, MY, 180)
# East: two columns of 0402s (x 32.0 / 34.4) between the package and the touch
# corridor, each part opposite its pin (pin k at y = MY - 6 + 0.5 (k - 1)).
place("Y1", 32.4, 25.75, 90)                   # pins 8/9
place("C_LSE1", 34.4, 24.2, 90)
place("C_LSE2", 34.4, 27.3, 90)
place("C_VBAT", 32.0, 21.9, 90)                # pin 6
place("C_VDD1", 34.4, 29.8, 90)                # pin 11
place("C_NRST", 32.0, 29.2, 90)                # pin 14
place("C_VDDA1", 32.0, 31.7, 90)               # pin 21
place("C_VREF1", 32.0, 34.2, 90)               # pin 20
place("C_VDDA2", 34.4, 32.3, 90)
place("C_VREF2", 34.4, 34.8, 90)
place("C_VDDB", 34.6, 37.6, 90)
# North: pins 94 (BOOT0), 98 (VDD11), 100 (VDD) between the package and J_CAM.
place("R_BOOT0", 23.4, 17.0, 90)
place("C_VDD11D", 25.6, 18.0, 0)
place("C_VDD5", 28.2, 18.0, 0)
# West: VDD 51/75, VDDUSB 73.
place("C_VDD4", 12.0, 21.4, 90)
place("C_VUSB", 12.0, 23.9, 90)
place("C_VDD3", 12.0, 34.6, 90)
# South channel, 4.6 mm between the package and the TFT connector (moved south
# by TFT_SLACK): VDD11 caps under the south-west corner (pin 49), the VDD 27 cap,
# the panel supply caps east. x 16-27 stays EMPTY: the display SPI, reset and TE
# drop straight through it to the connector.
place("C_VDD11A", 12.9, 38.8, 90)
place("C_VDD11B", 14.4, 38.8, 90)
place("C_VDD11C", 15.9, 38.8, 90)
place("C_VDD2", 28.3, 38.8, 90)                # pin 27
place("C_TFT1", 29.8, 39.3, 90)
place("C_TFT2", 31.3, 39.3, 90)
# Programming pads go on the FRONT face: reachable by pogo pins before the cell
# and panel go on, then buried under them for good. Nothing to probe on the face
# people see. MCU SWD in a row west of the MCU, radio SWD over the module.
for i, tp in enumerate(["TP_3V3", "TP_SWDIO", "TP_SWCLK", "TP_SWO", "TP_NRST", "TP_BOOT0", "TP_GND"]):
    place(tp, 4.0, 15.0 + i * 2.54, 0, TOP)

# ── Secure element, west of the MCU at its I2C4 pins (58-60) ───────────────
SX, SY = 8.4, 30.6
place("U5", SX, SY)
place("C_SEIN", SX - 3.4, SY - 1.2, 90)
place("C_SEVCC", SX - 3.4, SY + 1.3, 90)
place("R_SESCL", SX + 1.5, SY + 4.4, 90)
place("R_SESDA", SX, SY + 4.4, 90)
place("R_SEENA", SX - 1.5, SY + 4.4, 90)

# ── Radio gate, north-west quadrant: NOR + load switch + UART isolation ────
# Beside the MCU's USART1 (west pins 68/69). RADIO_3V3 and the two radio-side
# UART lines run down the west edge to the module in the chin; AIRGAP comes up
# from the switch on the same edge.
# Two rows of ICs with a row of their passives under each (courtyards: SOT-23-5/6
# 4.2 x 3.5, 0402 on end 1.0 x 1.9; 0.5 mm between all).
place("U8", 3.1, 15.8, 0)                      # TPS22917 load switch
place("U7", 7.8, 15.8, 0)                      # NOR
place("C_RIN", 1.5, 19.1, 90)
place("C_RCT", 3.0, 19.1, 90)
place("C_NOR", 6.3, 19.1, 90)
place("R_ROFF", 7.8, 19.1, 90)
place("U9", 3.1, 22.6, 0)                      # TX buffer
place("U10", 7.8, 22.6, 0)                     # RX buffer
place("C_U9A", 1.5, 26.0, 90)
place("C_U9B", 3.0, 26.0, 90)
place("R_RTXPU", 4.5, 26.0, 90)
place("R_RMTX", 6.0, 26.0, 90)
place("C_U10A", 7.5, 26.0, 90)
place("C_U10B", 9.0, 26.0, 90)
place("R_RRXPU", 10.5, 26.0, 90)

# ── Camera, north-west: module glued lens-out, tail east into J_CAM ─────────
place("J_CAM", 19.6, 9.0, 90)
place("U12", 25.6, 4.2, 90)
place("U14", 25.6, 10.4, 90)
place("C_AVIN", 28.8, 2.6, 90)
place("C_AVOUT", 28.8, 5.6, 90)
place("C_DVIN", 28.8, 8.8, 90)
place("C_DVOUT", 28.8, 11.8, 90)
place("R_DV1", 30.8, 8.8, 90)
place("R_DV2", 30.8, 11.8, 90)
place("R_CAMEN", 30.8, 2.6, 90)
place("R_CSCL", 32.8, 5.6, 90)
place("R_CSDA", 32.8, 8.8, 90)
place("C_DOVDD", 24.6, 14.8, 0)
place("R_PWDN", 27.4, 14.8, 0)
place("R_CRST", 30.2, 14.8, 0)

# ── Display: TFT connector north of the slot, touch connector at the far end
#    of its 56 mm tail, backlight pump west of the TFT connector ─────────────
place("J_TFT", TFT_TAIL[0] + TFT_TAIL[1] / 2, TFT_END_Y + TFT_ROW + TFT_SLACK, 180)
place("J_CTP", CTP_TAIL[0] + CTP_TAIL[1] / 2, CTP_END_Y + CTP_ROW, 180)
place("R_CTPRST", 32.6, 17.2, 0)
# Touch/gauge I2C3 pull-ups, between the MCU's east pins 15/16 and J_CTP.
place("R_SCL", 32.6, 15.2, 0)
place("R_SDA", 32.6, 13.4, 0)
# Backlight pump west of the TFT connector. The panel's LED pins are at the
# connector's east end, but east of the MCU there is no 4 mm square left between
# the decoupling columns and the touch-tail corridor; BL_A/BL_K are two slow nets
# (~115 mA) that run along under the connector.
BX, BY = 6.6, 41.6
place("U13", BX, BY)
place("C_BLIN", BX - 3.6, BY - 1.2, 90)
place("C_BLOUT", BX - 3.6, BY + 1.6, 90)
place("C_BLF1", BX, BY - 3.4, 0)
place("C_BLF2", BX, BY + 3.4, 0)
place("R_BLSET", BX + 3.6, BY - 1.2, 90)
place("R_BLPD", BX + 3.6, BY + 1.4, 90)
place("R_BLEN", BX + 3.6, BY + 4.0, 90)

# ── Airgap switch + radio LED on the west edge; power button on the east ────
place("SW_AIR", 1.75, 51.0, 90)
place("R_AIRPU", 6.0, 48.6, 90)
place("R_AIRSER", 6.0, 51.4, 90)
place("R_AIRS", 7.6, 48.6, 90)
# The radio's light sits by the radio, directly under the south-west hole: in
# the 1.6 mm strip between the screw-head ring and the antenna keep-out, so the
# west bridge into the chin (x 6.5 to the slot) stays open for routing.
place("D_RADIO", 2.15, 62.7, 0)
place("R_RLED", 5.0, 62.7, 0)
place("SW_PWR", 46.0, 40.0, -90)
# East strip, between the touch connector (to y 22.75) and the button: the soft
# latch. Below the button: its sense FET, then the battery gauge.
place("C_EN", 45.8, 24.5, 90)
place("R_EN", 45.8, 27.0, 90)
place("D_OR", 45.6, 30.6, 90)
place("R_HOLD", 45.8, 34.1, 90)
place("R_BTN", 45.8, 44.3, 90)
place("Q_BTN", 45.6, 47.6, 90)
place("R_BTNN", 45.8, 51.0, 90)

# ── Chin: radio module (antenna west), USB, power ──────────────────────────
place("U11", 11.3, 74.3, 90)
# Module decoupling at its VDD pad (19) and USB-C's CC pull-downs claim their
# spots first, in the strip between the module and the receptacle.
place("C_RADIO1", 21.0, 75.0, 90)
place("C_RADIO2", 21.0, 71.2, 90)
place("R_CC2", 21.0, 78.6, 90)
place("R_CC1", 21.0, 81.2, 90)
for i, tp in enumerate(["TP_RSWDIO", "TP_RSWCLK", "TP_RRESET", "TP_R3V3"]):
    place(tp, 7.0 + i * 2.54, 78.0, 0, TOP)
place("J1", 27.4, H - 3.6, 180)
# Row of charger set/status resistors under the slot, USB protection row below.
PX, PY = 35.2, 66.4
place("U1", PX, PY)
for i, ref in enumerate(["R_ILIM", "R_ISET", "R_TS", "R_CE", "R_STAT1", "R_STAT2"]):
    place(ref, 22.8 + i * 1.55, 65.8, 90)
place("C_VBUS", 32.2, 66.0, 90)
place("D_ESD", 24.6, 69.6, 0)
place("U6", 29.0, 69.6, 0)
place("C_USBSW", 31.4, 69.6, 90)
place("R_VB1", 23.6, 74.0, 90)
place("R_VB2", 25.1, 74.0, 90)
place("C_SYS", 35.2, 70.2, 0)
place("C_BAT", 35.2, 72.9, 0)
place("J_BAT", 43.0, 68.6, -90)
# Buck-boost between USB-C, the cell connector and the corner hole.
place("U2", 37.6, 77.6, 0)
place("C_IN_BUCK", 34.4, 77.6, 90)
place("L_BUCK", 41.0, 75.2, 90)
place("C_3V3", 38.0, 81.0, 0)
place("R_FB1", 34.8, 81.4, 0)
place("R_FB2", 34.8, 83.0, 0)
# Battery gauge in the east strip beside the touch slot: it needs only the cell
# (BAT, a few uA) and the I2C bus.
place("U15", 45.8, 56.4, 90)
place("C_GAUGE", 45.8, 59.4, 90)
place("R_ALRT", 32.6, 19.0, 0)                 # GAUGE_ALRT pull-up, at the MCU end (pin 3)
# Always-on LDO for the MCU's VBAT domain in the chin, next to the cell
# connector it feeds from; VBAT_RTC is one slow net up to pin 6.
place("U3", 30.6, 73.8, 0)
place("C_RTCIN", 27.1, 74.0, 90)
place("C_RTC", 33.6, 75.6, 90)

NETCLASSES = [
    # name, track, clearance, via dia, via drill, nets. A class width is where a
    # track starts; widen power runs by hand where the current flows. GND is poured.
    ("Default", 0.15, 0.15, 0.45, 0.25, []),
    ("Power", 0.40, 0.15, 0.6, 0.3, ["VBUS", "SYS", "BAT", "V3V3", "L1_3V3", "L2_3V3"]),
    ("Rail", 0.25, 0.15, 0.45, 0.25, ["RADIO_3V3", "VDD11", "VBAT_RTC", "SE_VCC", "CAM_AVDD",
                                      "CAM_DVDD", "BL_A", "BL_K"]),
    ("USB", 0.20, 0.15, 0.45, 0.25, ["USB_DP", "USB_DN", "USB_DP_MCU", "USB_DN_MCU"]),
]


# Parts that stay exactly where they are put; everything else may be nudged by
# legalize() to the nearest spot where its courtyard is clear.
ANCHORED = {"U4", "U5", "U11", "U13", "J1", "J_BAT", "J_TFT", "J_CTP", "J_CAM", "SW_AIR", "SW_PWR", "U1", "U2"}


def _side(fp):
    return BOT if fp.IsFlipped() else TOP


def _box(fp, margin=0.0):
    fp.BuildCourtyardCaches()
    cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
    if cy.OutlineCount():
        bb = cy.BBox()
        x0, y0, x1, y1 = (pcbnew.ToMM(v) for v in (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()))
    else:   # test pads have no courtyard: use the copper
        bb = fp.GetBoundingBox(False)
        x0, y0, x1, y1 = (pcbnew.ToMM(v) for v in (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()))
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def _hits(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def legalize(board):
    """Move each non-anchored part to the closest position (spiral search, 0.2 mm
    steps) where its courtyard clears every placed courtyard, the board edge, the
    slots, the mounting-hole pads and the antenna keep-out. Order = PLACE order,
    so parts listed first keep their intended spot."""
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    forbidden = [(-999, -999, W + 999, 0.3), (-999, H - 0.3, W + 999, H + 999), (-999, -999, 0.3, H + 999),
                 (W - 0.3, -999, W + 999, H + 999)]
    for x0, x1 in ((TFT_TAIL[0] - SLOT_MARGIN, TFT_TAIL[0] + TFT_TAIL[1] + SLOT_MARGIN),
                   (CTP_TAIL[0] - SLOT_MARGIN, CTP_TAIL[0] + CTP_TAIL[1] + SLOT_MARGIN)):
        forbidden.append((x0 - 0.3, SLOT_Y0 - 0.3, x1 + 0.3, SLOT_Y1 + 0.3))
    forbidden += [(x - 2.2, y - 2.2, x + 2.2, y + 2.2) for x, y in HOLES]
    # Back-face-only keep-outs (the front face carries only the programming pads).
    back_only = [box for _, box in footprint_keepouts(board)] + list(LABEL_SLOTS.values()) + \
        [(x - HOLE_RING, y - HOLE_RING, x + HOLE_RING, y + HOLE_RING) for x, y in HOLES]
    # Anchored parts first, wherever they appear in PLACE: a passive must never
    # be put where a connector lands later.
    placed = [(_side(fps[r]), _box(fps[r])) for r in PLACE if r in ANCHORED and r in fps]
    # Board-edge, slot and keep-out clearances stay at their own values; GAP is
    # only between parts.
    for ref in PLACE:
        fp = fps.get(ref)
        if fp is None or ref in ANCHORED:
            continue
        x, y = PLACE[ref][0], PLACE[ref][1]
        ant = [] if ref == "U11" else [ANT_KEEPOUT]
        best = None
        for r10 in range(0, 75):
            r = r10 * 0.2
            n = 1 if r == 0 else max(8, int(2 * math.pi * r / 0.2))
            for k in range(n):
                a = 2 * math.pi * k / n
                fp.SetPosition(pcbnew.VECTOR2I_MM(x + r * math.cos(a), y + r * math.sin(a)))
                b = _box(fp, GAP)
                side = _side(fp)
                extra = back_only if side == BOT else []
                tight = _box(fp, 0.1)   # edges, slots and keep-outs carry their own clearance
                if any(_hits(b, o) for sd, o in placed if sd == side) or any(_hits(tight, o) for o in ant + forbidden + extra):
                    continue
                best = (x + r * math.cos(a), y + r * math.sin(a))
                break
            if best:
                break
        if best is None:
            print(f"legalize: no free spot within 12 mm of {ref}; left overlapping")
            fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        elif (best[0] - x) ** 2 + (best[1] - y) ** 2 > 1.5 ** 2:
            print(f"legalize: {ref} moved {math.hypot(best[0] - x, best[1] - y):.1f} mm")
        fp.Reference().SetPosition(fp.GetPosition())
        placed.append((_side(fp), _box(fp)))


def edge(board, shape):
    shape.SetLayer(pcbnew.Edge_Cuts)
    shape.SetWidth(pcbnew.FromMM(0.05))
    board.Add(shape)


def arc(board, c, r, a0, a1):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_ARC)
    pt = lambda a: pcbnew.VECTOR2I_MM(c[0] + r * math.cos(math.radians(a)), c[1] + r * math.sin(math.radians(a)))
    s.SetArcGeometry(pt(a0), pt((a0 + a1) / 2), pt(a1))
    edge(board, s)


def seg(board, a, b):
    if math.dist(a, b) < 1e-6:
        return      # a fully rounded end has no straight run; a zero-length edge breaks the outline
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(pcbnew.VECTOR2I_MM(*a))
    s.SetEnd(pcbnew.VECTOR2I_MM(*b))
    edge(board, s)


def rounded_rect(board, x0, y0, x1, y1, r):
    seg(board, (x0 + r, y0), (x1 - r, y0)); arc(board, (x1 - r, y0 + r), r, -90, 0)
    seg(board, (x1, y0 + r), (x1, y1 - r)); arc(board, (x1 - r, y1 - r), r, 0, 90)
    seg(board, (x1 - r, y1), (x0 + r, y1)); arc(board, (x0 + r, y1 - r), r, 90, 180)
    seg(board, (x0, y1 - r), (x0, y0 + r)); arc(board, (x0 + r, y0 + r), r, 180, 270)


def slot(board, x0, x1):
    """A tail slot with fully rounded ends (a router bit leaves those anyway)."""
    r = (SLOT_Y1 - SLOT_Y0) / 2
    rounded_rect(board, x0, SLOT_Y0, x1, SLOT_Y1, r)


def drawing(board, x0, y0, x1, y1, layer, width=0.1):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetStart(pcbnew.VECTOR2I_MM(x0, y0))
    s.SetEnd(pcbnew.VECTOR2I_MM(x1, y1))
    s.SetLayer(layer)
    s.SetWidth(pcbnew.FromMM(width))
    board.Add(s)


def text(board, t, x, y, layer, size=1.0, thick=0.15, rot=0):
    p = pcbnew.PCB_TEXT(board)
    p.SetText(t)
    p.SetPosition(pcbnew.VECTOR2I_MM(x, y))
    p.SetLayer(layer)
    p.SetTextSize(pcbnew.VECTOR2I_MM(size, size))
    p.SetTextThickness(pcbnew.FromMM(thick))
    p.SetTextAngleDegrees(rot)
    if layer in (pcbnew.B_SilkS, pcbnew.B_Fab):
        p.SetMirrored(True)
    board.Add(p)


def gnd_pour(board, layer):
    z = pcbnew.ZONE(board)
    ls = pcbnew.LSET()
    ls.AddLayer(layer)
    z.SetLayerSet(ls)     # ZONE.SetLayer() silently leaves the zone on F.Cu
    z.SetNet(board.FindNet("GND"))
    poly = z.Outline()
    poly.NewOutline()
    for x, y in ((0, 0), (W, 0), (W, H), (0, H)):
        poly.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    z.SetLocalClearance(pcbnew.FromMM(0.25))
    z.SetMinThickness(pcbnew.FromMM(0.2))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(pcbnew.FromMM(0.25))
    z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.35))
    board.Add(z)


def keepout(board, x0, y0, x1, y1, name):
    """No copper of any kind on any layer: the E73's chip antenna end."""
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetLayerSet(pcbnew.LSET.AllCuMask())
    z.SetDoNotAllowTracks(True)
    z.SetDoNotAllowVias(True)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowZoneFills(True)
    z.SetDoNotAllowFootprints(False)
    z.SetZoneName(name)
    poly = z.Outline()
    poly.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        poly.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    board.Add(z)


def rule_area(board, name, pts, layers, footprints=False, tracks=False, vias=False, fills=False):
    """A KiCad rule area: DRC reports anything of the disallowed kinds inside it."""
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    ls = pcbnew.LSET()
    for l in layers:
        ls.AddLayer(l)
    z.SetLayerSet(ls)
    z.SetDoNotAllowFootprints(footprints)
    z.SetDoNotAllowTracks(tracks)
    z.SetDoNotAllowVias(vias)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowZoneFills(fills)
    z.SetZoneName(name)
    poly = z.Outline()
    poly.NewOutline()
    for x, y in pts:
        poly.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    board.Add(z)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def keepouts(board):
    """Every footprint keep-out as a back-face rule area, plus the copper keep-out
    under the radio module (Ebyte: no routing under the module on the layer it
    sits on; GND fill is fine and is what pad 24 uses)."""
    for name, box in footprint_keepouts(board):
        rule_area(board, f"no parts: {name}", rect(*box), [pcbnew.B_Cu], footprints=True)
    for i, (x, y) in enumerate(HOLES):
        ring = [(x + HOLE_RING * math.cos(math.radians(a)), y + HOLE_RING * math.sin(math.radians(a)))
                for a in range(0, 360, 30)]
        rule_area(board, f"no parts: hole H{i + 1}", ring, [pcbnew.B_Cu], footprints=True)
    u11 = board.FindFootprintByReference("U11")
    xs = [pcbnew.ToMM(p.GetPosition().x) for p in u11.Pads()]
    ys = [pcbnew.ToMM(p.GetPosition().y) for p in u11.Pads()]
    rule_area(board, "no tracks/vias: under the radio module",
              rect(min(xs) + 1.3, min(ys) + 1.3, max(xs) - 1.3, max(ys) - 1.3), [pcbnew.B_Cu], tracks=True, vias=True)


def mounting_holes(board):
    io = pcbnew.PCB_IO_KICAD_SEXPR()
    lib = str(syncBoard.KICAD_LIBS / "MountingHole.pretty")
    for i, (x, y) in enumerate(HOLES):
        fp = io.FootprintLoad(lib, "MountingHole_2.2mm_M2_Pad_Via")
        fp.SetFPID(pcbnew.LIB_ID("MountingHole", "MountingHole_2.2mm_M2_Pad_Via"))
        fp.SetReference(f"H{i + 1}")
        fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM
                         | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
        for p in fp.Pads():
            p.SetNet(board.FindNet("GND"))
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        board.Add(fp)


def artwork(board):
    """Guides on User.Drawings (what lies over/under the board) and the back-face
    silkscreen people will see through the clear case."""
    d = pcbnew.Dwgs_User
    x, y, w, h = PANEL
    drawing(board, x, y, x + w, y + h, d)
    text(board, "DISPLAY PANEL (front face, over the cell)", x + w / 2, y + 3, d, 0.8, 0.1)
    # The tails as they lie on the back face after the slot.
    tx, tw = TFT_TAIL
    drawing(board, tx, PLACE["J_TFT"][1] + 3.0, tx + tw, SLOT_Y0, d)
    text(board, "TFT TAIL", tx + tw / 2, SLOT_Y0 - 1.5, d, 0.7, 0.1)
    cx, cw = CTP_TAIL
    drawing(board, cx, PLACE["J_CTP"][1] + 2.0, cx + cw, SLOT_Y0, d)
    text(board, "TOUCH TAIL", cx + cw / 2, 40.0, d, 0.7, 0.1, 90)
    # Camera module (OV2640 ESP32-CAM class, ~8.5 x 8.5 mm head) and its lens.
    drawing(board, 4.0, 4.5, 12.5, 13.0, d)
    text(board, "CAMERA", 8.25, 8.75, d, 0.7, 0.1)
    drawing(board, 4.0, 4.5, 12.5, 13.0, pcbnew.B_SilkS, 0.15)
    text(board, "QR CAMERA", 9.0, 2.6, pcbnew.B_SilkS, 0.8, 0.12)
    # Antenna keep-out marker.
    ax0, ay0, ax1, ay1 = ANT_KEEPOUT
    text(board, "ANT", (ax0 + ax1) / 2 + 0.6, (ay0 + ay1) / 2, pcbnew.B_SilkS, 0.8, 0.12, 90)
    # Switch and light legends: the two things a user must be able to read.
    # Which lever end is AIRGAP is a bring-up check (docs/design.md), so the
    # legend names the switch, not a position.
    text(board, "AIRGAP", 5.2, 51.0, pcbnew.B_SilkS, 0.8, 0.12, 90)
    text(board, "RADIO", 2.6, 64.4, pcbnew.B_SilkS, 0.8, 0.12)
    lx0, ly0, lx1, ly1 = LABEL_SLOTS["SECURE ELEMENT"]
    text(board, "SECURE ELEMENT", (lx0 + lx1) / 2, (ly0 + ly1) / 2, pcbnew.B_SilkS, 0.8, 0.12)
    drawing(board, SX - 2.0, SY - 2.0, SX + 2.0, SY + 2.0, pcbnew.B_SilkS, 0.15)
    # Brand: placeholder for the full-colour UV-printed Ambire artwork.
    drawing(board, 34.0, 4.5, 46.0, 13.5, pcbnew.Dwgs_User)
    text(board, "UV ARTWORK", (UV_PANEL[0] + UV_PANEL[2]) / 2, 12.6, pcbnew.Dwgs_User, 0.6, 0.08)
    text(board, "AMBIRE", 40.0, 9.0, pcbnew.B_SilkS, 2.0, 0.3)
    text(board, "Ambire signer · proto 0", 13.0, H - 1.6, pcbnew.B_SilkS, 0.7, 0.12)


def write_project():
    tmpl = HERE.parent / "nano/board/nano.kicad_pro"
    pro = json.load(open(PRO if PRO.exists() else tmpl))
    pro["meta"]["filename"] = PRO.name
    ns = pro["net_settings"]
    base = dict(ns["classes"][0])
    classes, patterns = [], []
    for i, (name, track, clr, vdia, vdrill, nets) in enumerate(NETCLASSES):
        c = dict(base)
        c.update(name=name, track_width=track, clearance=clr, via_diameter=vdia, via_drill=vdrill,
                 priority=2147483647 if name == "Default" else i)
        classes.append(c)
        patterns += [{"netclass": name, "pattern": n} for n in nets]
    ns["classes"] = classes
    ns["netclass_patterns"] = patterns
    ns.pop("netclass_assignments", None)
    ds = pro["board"]["design_settings"]
    # JLC 4-layer standard capability: 0.09 mm track/space, 0.2 mm drill, 0.45 mm via.
    ds["rules"].update(min_clearance=0.1, min_track_width=0.1, min_via_diameter=0.45,
                       min_through_hole_diameter=0.2, min_hole_to_hole=0.25,
                       min_copper_edge_clearance=0.3, min_hole_clearance=0.2,
                       min_text_height=0.7, min_text_thickness=0.1)
    ds["rule_severities"].update(silk_over_copper="warning", text_height="warning", text_thickness="warning",
                                 starved_thermal="warning")
    ds["track_widths"] = [0.0, 0.15, 0.2, 0.25, 0.4, 0.6]
    ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.45, "drill": 0.25},
                            {"diameter": 0.6, "drill": 0.3}]
    json.dump(pro, open(PRO, "w"), indent=2)
    (HERE / "board/wallet.kicad_dru").write_text(
        "(version 1)\n\n"
        "# In1 (L2) is solid GND: the reference plane under the back face, where every\n"
        "# part sits. Nothing but GND routes on it.\n"
        "(rule \"In1: GND only\"\n"
        "  (condition \"A.Type == 'Track' && A.NetName != 'GND'\")\n"
        "  (layer \"In1.Cu\")\n"
        "  (constraint disallow track))\n\n"
        "# Fine-pitch lands from the vendors' own drawings: the SE050's 0.4 mm HX2QFN and\n"
        "# the BQ25185's DLH0010A put pads closer than the board default.\n"
        "(rule \"Fine-pitch lands\"\n"
        "  (condition \"A.Type == 'Pad' && B.Type == 'Pad' && (A.memberOfFootprint('U5') || A.memberOfFootprint('U1'))\")\n"
        "  (constraint clearance (min 0.09mm)))\n")


def main():
    if syncBoard.BOARD.exists() and "--force" not in sys.argv and "--out" not in sys.argv:
        raise SystemExit(f"{syncBoard.BOARD} exists; it may hold hand work. --force replaces it.")
    board = pcbnew.CreateEmptyBoard()
    board.SetCopperLayerCount(4)
    board.GetDesignSettings().SetBoardThickness(pcbnew.FromMM(1.0))
    tb = board.GetTitleBlock()
    tb.SetTitle("Ambire hardware signer")
    tb.SetRevision("proto 0")
    rounded_rect(board, 0, 0, W, H, R)
    slot(board, TFT_TAIL[0] - SLOT_MARGIN, TFT_TAIL[0] + TFT_TAIL[1] + SLOT_MARGIN)
    slot(board, CTP_TAIL[0] - SLOT_MARGIN, CTP_TAIL[0] + CTP_TAIL[1] + SLOT_MARGIN)

    parts = netlist.read(syncBoard.NETLIST)
    fpmap = {k: v for k, v in json.load(open(syncBoard.FPMAP)).items() if not k.startswith("_")}
    changes, errors = syncBoard.sync(board, parts, fpmap)
    if errors:
        raise SystemExit("\n".join(errors))
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref not in PLACE:
            print(f"no placement for {ref}; left parked")
            continue
        x, y, rot, side = PLACE[ref]
        if side == BOT and not fp.IsFlipped():
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        fp.SetOrientationDegrees(rot)
        ref_text = fp.Reference()
        ref_text.SetLayer(pcbnew.B_Fab if side == BOT else pcbnew.F_Fab)
        ref_text.SetTextSize(pcbnew.VECTOR2I_MM(0.5, 0.5))
        ref_text.SetTextThickness(pcbnew.FromMM(0.08))
        ref_text.SetPosition(fp.GetPosition())
        fp.Value().SetVisible(False)
        for t in fp.GraphicalItems():   # library silk legends (test-point names) off
            if isinstance(t, pcbnew.PCB_TEXT) and t.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                t.SetVisible(False)

    legalize(board)
    if "--no-optimize" not in sys.argv:
        # Ratsnest pass: rotate / nudge / swap parts to cut crossings and airwire
        # length, inside the same legality rules (scripts/optimizePlacement.py).
        import optimizePlacement
        optimizePlacement.optimize(board, radius=OPT_RADIUS, passes=10)
    mounting_holes(board)
    for layer in (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu):
        gnd_pour(board, layer)
    keepout(board, *ANT_KEEPOUT, "E73 antenna")
    keepouts(board)
    artwork(board)

    # --out DIR writes a candidate board elsewhere (e.g. while the real one is open in KiCad).
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else HERE / "board"
    out.mkdir(parents=True, exist_ok=True)
    target = out / syncBoard.BOARD.name
    pcbnew.SaveBoard(str(target), board)
    write_project()
    if out != HERE / "board":
        for f in ("wallet.kicad_pro", "wallet.kicad_dru", "fp-lib-table"):
            shutil.copy(HERE / "board" / f, out / f)
        (out / "wallet.pretty").exists() or shutil.copytree(HERE / "board/wallet.pretty", out / "wallet.pretty")
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--save-board", "--format", "json",
                    "-o", str(out / "drc.json"), str(target)], check=True, stdout=subprocess.DEVNULL)
    print(f"wrote {target}: {W:g} x {H:g} mm, {len(board.GetFootprints())} footprints")


if __name__ == "__main__":
    main()
