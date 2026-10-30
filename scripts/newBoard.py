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

# 87 tall: the routing pass gave the chin 3 mm (proto 0, 85), and proto 1 another 2
# for the joystick below the AAA cell, beside USB-C.
W, H, R = 48.0, 87.0, 4.0
PANEL = (2.6, 1.0, 42.80, 59.91)              # x, y, w, h (Newhaven rev 4 drawing)
PANEL_S = PANEL[1] + PANEL[3]                 # 60.91: the panel's south edge
TFT_TAIL = (PANEL[0] + 9.25, 20.50)           # x of the tail's west edge, width
CTP_TAIL = (PANEL[0] + PANEL[2] - 1.90 - 7.00, 7.00)
# 1.6 mm wide slots, 3.0 mm south of the panel: with no battery between the panel and
# the board the tails drop 4 mm less, and the slots moved 2 mm south take that length
# back (each mm south costs a tail 2 mm), so both tails land as before.
SLOT_Y0, SLOT_Y1 = PANEL_S + 3.0, PANEL_S + 4.6
# v4 (2026-10-28): the TFT connector moved 5.5 mm south to make room for the battery
# pack's receptacle between it and the MCU; its slot moved 2.5 mm south with it (the
# touch slot stayed), which leaves the TFT tail 3.0 mm of slack. The radio module's
# pads (y 68.39) stop the slot going further.
TFT_SLOT_DY = 2.5
# The touch flex is 7 mm wide only for its last 32 mm. From the panel edge to 23.6 mm below
# it, it is 14.55 mm wide, flush with the tail's east edge: the root off the touch glass and
# the stiffened block that carries the touch controller. Newhaven's drawing does not dimension
# that part; these are its outline in the PDF's vectors, scaled by the dimensioned 7.00 tail
# and 55.85 length beside it. The block goes through the touch slot when the tail is threaded,
# so the slot is sized by the flex, not by the tail (proto 0: the bridge between the two slots
# stopped it). The touch slot so reaches into the TFT slot's x range, and the two are one
# cutout (slot_outline()).
CTP_FLEX = (CTP_TAIL[0] + CTP_TAIL[1] - 14.55, 14.55)
CTP_FLEX_LEN = 23.6
# Tail-to-slot clearance at each end. The panel is located by the bezel and the tails
# by the panel: placement ~0.2 mm plus tail width +-0.1 is the lateral error, so 0.45
# keeps the tail off the slot's end. Was 0.75 until 2026-10-23: every 0.1 mm here is
# routing room on the three bridges all chin-to-MCU lines cross (the centre bridge's
# copper went 2.01 -> 2.71 mm with this and the 0.25 mm edge clearance).
SLOT_MARGIN = 0.45
# Each tail comes off the panel's south edge and drops through the north half of its
# slot. Front parts (the cell's clips) and the cell may stand over the slot's south
# half, never over the tail's drop: north of SLOT_Y0 + TAIL_BEND + TAIL_CLR, across the
# tail's width plus SLOT_MARGIN, is the tails' alone (scripts/checkFpc.py checks it).
TAIL_BEND, TAIL_CLR = 0.8, 0.2
# The north pair sits under the panel's corners and only locates the board on
# pins in the back shell: with the panel straight on the board there is no room
# for a boss there. The screw goes where the front half is solid: H4 in the
# south-east corner. The south-west corner belongs to the radio antenna.
# H3 (3.2, 63.0), the second screw in the strip between the panel and the cell, is
# out for now (2026-10-22): the cell's west clip moved north over its boss. Keyed by
# reference so a removed hole does not renumber the others.
HOLES = {"H1": (3.2, 3.2), "H2": (44.8, 3.2), "H4": (44.8, H - 3.2)}
HOLE_RING = 2.6      # no parts within this of a hole centre: an M2 head is ~3.8 mm across
# E73 antenna end, all layers: the module points it at the south edge, clear of the
# AAA cell on the front (a metal can over a chip antenna detunes it); keepouts()
# redraws it from the placed module, this is the seed.
ANT_KEEPOUT = (0.0, H - 3.7, 20.0, H)

# Tail length budget (Newhaven rev 4 drawing: TFT tail 30.00 and touch tail 55.85
# below the panel outline). Each tail runs in-plane to the slot, drops through
# the foam + board, and runs north on the back face until its stiffened end sits
# over the connector's contact row. The touch flex leaves from the touch glass,
# which sits on top of the TFT, so it drops further. scripts/checkFpc.py checks
# the placed connectors against this budget. The panel sits on foam tape straight
# on the board's front face (the cell is an AAA in the chin, not under the panel).
ADHESIVE_T, BOARD_T = 0.3, 1.0                # foam tape, PCB
TFT_LEN, CTP_LEN = 30.00, 55.85
CTP_ABOVE_TFT = 2.55 + 0.175                  # TFT module + adhesive under the touch glass
SLOT_C = (SLOT_Y0 + SLOT_Y1) / 2
TFT_DROP = ADHESIVE_T + BOARD_T
CTP_DROP = TFT_DROP + CTP_ABOVE_TFT
TFT_SLOT_C = SLOT_C + TFT_SLOT_DY
TFT_END_Y = TFT_SLOT_C - (TFT_LEN - (TFT_SLOT_C - PANEL_S) - TFT_DROP)
CTP_END_Y = SLOT_C - (CTP_LEN - (SLOT_C - PANEL_S) - CTP_DROP)
CTP_FLEX_END_Y = SLOT_C - (CTP_FLEX_LEN - (SLOT_C - PANEL_S) - CTP_DROP)   # north end of the wide part, on the back
TFT_ROW, CTP_ROW = 1.35, 1.05                 # connector origin to its contact row (north of origin)
CTP_SLACK = 1.5      # mm of touch tail slack: a nominal-length tail landing exactly on the row left no margin for stack/tail tolerance (pre-order audit)
TFT_SLACK = 3.0      # mm of TFT tail left as a gentle bow (checkFpc allows 3): v4's receptacle needs the room south of the MCU
GAP = 0.5            # free board kept between courtyards: a via, or two 0.15 mm tracks
OPT_RADIUS = 7.0     # how far the ratsnest pass may move a part from its seed (mm)
# Footprint keep-outs on the back face (KiCad rule areas: DRC flags any part moved
# into one, and legalize() never puts a part there). What lies over each:
#   display tails  the two flex tails run flat over the back face from slot to connector
#   camera         the module head (lens out through the case) and its flex to J_CAM
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
# Room kept for printed labels that name a part (the UV art restyles them in
# place): the placer and the ratsnest pass treat these as occupied. Not KiCad
# rule areas; they only stop the generator from filling the spot.
LABEL_SLOTS = {"SECURE ELEMENT": (1.2, 36.3, 12.2, 37.7)}


def style_text(fp, side):
    """The board's text convention: the reference on the fab layer, 0.5 mm, on the
    part's centre; no value; library silk legends (test-point names) off."""
    ref_text = fp.Reference()
    ref_text.SetLayer(pcbnew.B_Fab if side == BOT else pcbnew.F_Fab)
    ref_text.SetTextSize(pcbnew.VECTOR2I_MM(0.5, 0.5))
    ref_text.SetTextThickness(pcbnew.FromMM(0.08))
    ref_text.SetPosition(fp.GetPosition())
    fp.Value().SetVisible(False)
    for t in fp.GraphicalItems():
        if isinstance(t, pcbnew.PCB_TEXT) and t.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
            t.SetVisible(False)


def footprint_keepouts(board):
    """The keep-out boxes, starting where each placed connector's courtyard ends,
    so a connector never sits in its own tail's keep-out."""
    fp = board.FindFootprintByReference
    cam = _box(fp("J_CAM"))
    # The display tails are NOT keep-outs: low parts (passives, SOT/QFN ICs) may
    # sit under the flex; scripts/checkFpc.py fails a tall part in a corridor.
    return [
        ("Camera module", CAMERA_HEAD),
        ("Camera flex", (CAMERA_HEAD[2], 2.2, cam[0] - 0.1, 15.3)),
        ("Camera fan-in", (cam[2], 0.3, cam[2] + CAM_FANIN, 15.5)),
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
place("C_VBAT", 32.0, 21.9, 90)                # pin 6
place("C_VDD1", 34.4, 29.8, 90)                # pin 11
place("C_NRST", 32.0, 29.2, 90)                # pin 14
place("C_VDDA1", 32.0, 31.7, 90)               # pin 21
place("C_VDDA2", 34.4, 32.3, 90)
place("C_VDDB", 34.6, 37.6, 90)
# North: pins 94 (BOOT0), 98 (VDD11), 100 (VDD) between the package and J_CAM.
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
place("C_VDD2", 28.3, 38.8, 90)                # pin 27
place("C_TFT1", 29.8, 39.3, 90)
place("C_TFT2", 31.3, 39.3, 90)
# Programming pads go on the FRONT face: reachable by pogo pins before the cell
# and panel go on, then buried under them for good. Nothing to probe on the face
# people see. MCU SWD in a row west of the MCU, radio SWD over the module.
for i, tp in enumerate(["TP_3V3", "TP_SWDIO", "TP_SWCLK", "TP_NRST", "TP_GND"]):
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

# ── Camera, north-west: module glued lens-out, tail east into J_CAM ─────────
place("J_CAM", 19.6, 9.0, 90)
place("U12", 25.6, 4.2, 90)
place("U14", 25.6, 10.4, 90)
place("C_AVIN", 28.8, 2.6, 90)
place("C_AVOUT", 28.8, 5.6, 90)
place("C_DVOUT", 28.8, 11.8, 90)
place("R_CAMEN", 30.8, 2.6, 90)
place("R_CSCL", 32.8, 5.6, 90)
place("R_CSDA", 32.8, 8.8, 90)
place("C_DOVDD", 24.6, 14.8, 0)

# ── Display: TFT connector north of the slot, touch connector at the far end
#    of its 56 mm tail, backlight pump west of the TFT connector ─────────────
place("J_TFT", TFT_TAIL[0] + TFT_TAIL[1] / 2, TFT_END_Y + TFT_ROW + TFT_SLACK, 180)
place("J_CTP", CTP_TAIL[0] + CTP_TAIL[1] / 2, CTP_END_Y + CTP_ROW + CTP_SLACK, 180)
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

# ── Airgap switch + radio LED on the west edge; the soft latch on the east ──
place("SW_AIR", 1.75, 51.0, 90)
place("R_AIRPU", 6.0, 48.6, 90)
place("R_AIRSER", 6.0, 51.4, 90)
place("R_AIRS", 7.6, 48.6, 90)
# The radio's light sits by the radio, directly under the south-west hole: in
# the 1.6 mm strip between the screw-head ring and the antenna keep-out, so the
# west bridge into the chin (x 6.5 to the slot) stays open for routing.
# East strip, between the touch connector (to y 22.75) and the button: the soft
# latch. Below the button: its sense FET, then the battery gauge.
place("C_EN", 45.8, 24.5, 90)
place("R_EN", 45.8, 27.0, 90)
place("D_OR", 45.6, 30.6, 90)
place("R_HOLD", 45.8, 34.1, 90)
place("R_BTN", 45.8, 44.3, 90)
place("Q_BTN", 45.6, 47.6, 90)

# ── Chin and proto 1 additions: seeded from the routed board (2026-10-22) ──
# Chin. Radio module, antenna south at the board edge clear of the AAA cell; its UART translator on the west bridge.
place("U11", 12.50, 75.90, 180)
place("U9", 8.30, 61.10, 0)
place("C_U9A", 6.97, 58.80, 90)
place("C_U9B", 9.72, 58.80, 90)
place("C_RADIO1", 21.10, 69.70, 90)
place("C_RADIO2", 21.30, 66.55, 0)
place("D_RADIO", 2.40, 78.30, 90)
place("R_RLED", 2.40, 81.60, 90)
# USB-C, its CC pull-downs, ESD, VBUS sense and the USB data switch.
place("J1", 27.40, 83.40, 180)
place("R_CC1", 28.62, 76.98, 90)
place("R_CC2", 25.66, 76.87, 90)
place("D_ESD", 25.70, 70.30, 0)
place("C_VBUS", 36.41, 79.91, 0)
place("R_VB1", 23.30, 77.50, 180)
place("R_VB2", 23.30, 74.40, 0)
place("U6", 23.80, 67.25, -90)
place("C_USBSW", 22.80, 69.50, -90)
# The AAA cell's clips on the front, across the chin; the reverse-cell FET and its gate diode at the - clip.
place("BT_N", 5.50, 71.80, 0, TOP)
place("BT_P", 42.50, 71.80, 180, TOP)
place("Q_REV", 2.50, 67.90, 180)
place("D_REVG", 2.50, 73.00, 90)
place("R_CNS", 6.53, 63.41, 90)
place("C_CNS", 9.53, 62.91, 180)
# Boost under the + clip, with the cell sense divider.
place("U2", 41.60, 70.40, 0)
place("L_BOOST", 45.26, 68.61, 90)
place("C_IN_BOOST", 38.51, 69.11, 180)
place("C_BOOST", 38.51, 71.86, 180)
place("R_FB1", 40.01, 73.86, 90)
place("R_FB2", 37.51, 73.61, 0)
place("R_REVG", 33.76, 68.36, 0)
place("R_CS1", 37.51, 67.11, 90)
place("R_CS2", 38.76, 67.11, 90)
place("C_CS", 36.01, 66.61, 0)
# NiMH charger in the middle of the chin, its NTC under the cell; its dual FET beside the MCU, north of the slots.
place("U1", 30.40, 69.40, 90)
place("C_CHGIN", 31.85, 72.00, -90)
place("C_BAT", 33.60, 70.00, 0)
place("R_ISET", 36.10, 68.25, 90)
place("R_ISETH", 34.10, 71.75, 0)
place("R_TMR", 26.86, 72.75, 0)
place("R_VSET", 29.36, 66.50, 0)
place("R_STAT", 26.11, 60.50, 0)
place("RT_CHG", 29.25, 71.90, 180)
place("Q_CHG", 17.00, 60.20, -90)
place("R_CHGHI", 18.61, 52.95, 90)
place("R_CHGOFF", 17.86, 57.95, 0)
# USB buck and the rail mux, between USB-C and the south-east hole.
place("U3", 36.40, 83.00, -90)
place("L_BUCK", 39.91, 82.41, 0)
place("C_USB3V3", 37.16, 77.91, 180)
place("R_FBU1", 33.41, 80.91, 90)
place("R_FBU2", 33.91, 84.66, 90)
place("R_PG", 37.91, 85.66, 0)
place("U10", 40.30, 79.60, 90)
place("C_3V3", 43.45, 78.73, 0)
# Joystick on the front below the cell; its resistor ladder and the power-key PNP on the back.
place("SW_JOY", 37.40, 82.15, 0, TOP)
place("R_JA", 29.05, 74.31, 0)
place("R_JB", 34.80, 76.06, 90)
place("R_JC", 35.55, 85.81, 0)
place("R_JD", 36.80, 74.81, 0)
place("R_JPU", 30.80, 73.31, 90)
place("Q_PWRJ", 43.50, 75.00, 90)
place("R_PWRJBE", 46.01, 73.53, 90)
place("R_PWRJB", 42.76, 80.28, 0)
# Buzzer in the north-east corner (the old UV-artwork panel).
place("LS1", 39.00, 9.80, -90)
place("Q_BUZ", 43.95, 11.36, 90)
place("R_BUZG", 43.70, 15.86, 0)
place("R_BUZPD", 42.20, 13.86, 0)
place("D_BUZ", 43.20, 7.61, 90)
place("C_BUZ", 32.45, 7.86, 0)
place("TP_RSWDIO", 7.00, 81.40, 0, TOP)
place("TP_RSWCLK", 9.70, 81.40, 0, TOP)
place("TP_RGND", 12.40, 81.40, 0, TOP)
place("TP_R3V3", 15.10, 81.40, 0, TOP)


NETCLASSES = [
    # name, track, clearance, via dia, via drill, nets. A class width is where a
    # track starts (KiCad's default when routing that net); GND is poured, V3V3 is
    # the In1 plane. humanizedKicadAutoRouter treats only "Power" and "Rail" as power.
    ("Default", 0.15, 0.15, 0.45, 0.25, []),
    # The cell path: at 1.0 V the cell delivers ~1 A for a busy device (camera, backlight,
    # radio), and the boost's switch node peaks higher. Every 10 mOhm here costs 1 % of
    # the cell's voltage: short, wide, two vias per layer change.
    ("Power", 0.80, 0.15, 0.6, 0.3, ["BAT", "BAT_N", "SW_BOOST"]),
    # The 3.3-5 V rails, each <= 0.5 A: USB in, the USB buck's switch node and output,
    # the boost's output, and the 3.3 V plane's fanouts (0.4, capped at the pad's width).
    ("Rail", 0.40, 0.15, 0.6, 0.3, ["VBUS", "SW_USB", "V3V3_USB", "V_BOOST", "V3V3"]),
    # Small local supplies, tens of mA.
    ("Lite", 0.25, 0.15, 0.45, 0.25, ["RADIO_3V3", "VDD11", "VBAT_RTC", "SE_VCC", "CAM_AVDD",
                                      "CAM_DVDD", "BL_A", "BL_K", "BUZ_SW"]),
    # USB full speed (the STM32U585 has no high-speed PHY): 12 Mb/s over a few cm cares
    # little for impedance; 0.15 mm keeps the pair slim through the bridges (owner, 2026-10-23)
    ("USB", 0.15, 0.15, 0.45, 0.25, ["USB_DP", "USB_DN", "USB_MCU_DP", "USB_MCU_DN"]),
]


# Parts that stay exactly where they are put; everything else may be nudged by
# legalize() to the nearest spot where its courtyard is clear.
ANCHORED = {"U4", "U5", "U11", "U13", "J1", "J_TFT", "J_CTP", "J_CAM", "SW_AIR", "U1", "U2", "BT_N", "BT_P", "SW_JOY"}


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


# The AAA cell between its clips (MY-AAA-19-J/20-J): it rests on the clips' bases,
# its underside CELL_BASE above the board along the axis and rising as a circle away
# from it. A part under it must stay UNDER_CELL_CLR below that underside at the
# part's point nearest the axis. The largest AAA (10.5 mm) sits flattest: the worst case.
CELL_BASE, CELL_R_MAX, UNDER_CELL_CLR = 0.25, 10.5 / 2, 0.3
PART_H = {"R_0402": 0.40, "C_0402": 0.55, "R_0603": 0.50, "C_0603": 0.90, "R_0805": 0.60, "C_0805": 1.35}


# Under the display's flex tails, on the back between each connector and its slot, only
# low parts: the tail lies flat on them. Chip passives and small SMD IC packages.
LOW_LIBS = ("Capacitor_SMD:", "Resistor_SMD:", "Inductor_SMD:", "LED_SMD:", "Package_TO_SOT_SMD:",
            "Package_DFN_QFN:", "Package_SON:", "wallet:Texas_", "wallet:NXP_", "Crystal:")


def tail_corridors(board):
    """{name: (x0, y0, x1, y1)}: each tail from its connector's south edge to its slot, and
    the touch flex's wide part (CTP_FLEX), which lies on the back from the slot north."""
    out = {}
    for name, ref, (tx0, tw), dy in (("TFT tail", "J_TFT", TFT_TAIL, TFT_SLOT_DY), ("touch tail", "J_CTP", CTP_TAIL, 0.0)):
        y0 = pcbnew.ToMM(board.FindFootprintByReference(ref).GetBoundingBox(False).GetBottom())
        out[name] = (tx0, y0, tx0 + tw, SLOT_Y0 + dy)
    out["touch flex block"] = (CTP_FLEX[0], CTP_FLEX_END_Y, sum(CTP_FLEX), SLOT_Y0)
    return out


def under_tail(fp, corridors):
    """The tails a back part that is not low sits under."""
    if not fp.IsFlipped() or fp.GetReference() in ("J_TFT", "J_CTP") or fp.GetFPIDAsString().startswith(LOW_LIBS):
        return []
    bb = fp.GetBoundingBox(False)
    x0, y0, x1, y1 = (pcbnew.ToMM(v) for v in (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()))
    return [n for n, (cx0, cy0, cx1, cy1) in corridors.items() if x0 < cx1 and cx0 < x1 and y0 < cy1 and cy0 < y1]


def cell_geometry(board):
    """(face, axis y, x0, x1) of the cell from its clips' pads: the face the clips are
    on (TOP/BOT) and the span between the clips' pad centres. None with no clips on the
    board (v4: the cells are in a pack)."""
    pads = {}
    for ref in ("BT_N", "BT_P"):
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            return None
        ps = list(fp.Pads())
        pads[ref] = (sum(pcbnew.ToMM(p.GetPosition().x) for p in ps) / len(ps),
                     sum(pcbnew.ToMM(p.GetPosition().y) for p in ps) / len(ps), BOT if fp.IsFlipped() else TOP)
    (nx, ny, nside), (px, py, pside) = pads["BT_N"], pads["BT_P"]
    if nside != pside:
        raise SystemExit("the cell clips BT_N/BT_P must be on the same face")
    return nside, (ny + py) / 2, min(nx, px), max(nx, px)


def under_cell_room(fp, cell):
    """(mm left under the cell, package) for a part on the cell's face within its
    footprint; None when the part is not under the cell. The mm left is None for a
    package with no height bound in PART_H: it may not go there."""
    if cell is None:
        return None
    side, axis, cx0, cx1 = cell
    if (BOT if fp.IsFlipped() else TOP) != side or fp.GetReference() in ("BT_N", "BT_P"):
        return None
    fab = [g.GetBoundingBox() for g in fp.GraphicalItems()
           if g.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab)]
    if fab:
        fx0, fy0 = min(pcbnew.ToMM(b.GetLeft()) for b in fab), min(pcbnew.ToMM(b.GetTop()) for b in fab)
        fx1, fy1 = max(pcbnew.ToMM(b.GetRight()) for b in fab), max(pcbnew.ToMM(b.GetBottom()) for b in fab)
    else:
        fx0, fy0, fx1, fy1 = _box(fp)
    d = 0.0 if fy0 <= axis <= fy1 else min(abs(fy0 - axis), abs(fy1 - axis))
    if not (fx0 < cx1 and cx0 < fx1) or d >= CELL_R_MAX:
        return None
    under_h = CELL_BASE + CELL_R_MAX - (CELL_R_MAX ** 2 - d ** 2) ** 0.5
    pkg = fp.GetFPID().GetLibItemName().wx_str().split("_1005")[0].split("_1608")[0].split("_2012")[0]
    h = PART_H.get(pkg)
    return (None if h is None else under_h - h - UNDER_CELL_CLR), pkg


def legalize(board):
    """Move each non-anchored part to the closest position (spiral search, 0.2 mm
    steps) where its courtyard clears every placed courtyard, the board edge, the
    slots, the mounting-hole pads and the antenna keep-out. Order = PLACE order,
    so parts listed first keep their intended spot."""
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    forbidden = [(-999, -999, W + 999, 0.3), (-999, H - 0.3, W + 999, H + 999), (-999, -999, 0.3, H + 999),
                 (W - 0.3, -999, W + 999, H + 999)]
    for x0, y0, x1, y1 in slot_boxes().values():
        forbidden.append((x0 - 0.3, y0 - 0.3, x1 + 0.3, y1 + 0.3))
    forbidden += [(x - 2.2, y - 2.2, x + 2.2, y + 2.2) for x, y in HOLES.values()]
    # Back-face-only keep-outs (the front face carries only the programming pads).
    back_only = [box for _, box in footprint_keepouts(board)] + list(LABEL_SLOTS.values()) + \
        [(x - HOLE_RING, y - HOLE_RING, x + HOLE_RING, y + HOLE_RING) for x, y in HOLES.values()]
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


def slot_boxes():
    """{name: (x0, y0, x1, y1)} of the two tail slots: each flex's widest part through
    it plus SLOT_MARGIN at both ends; the TFT slot TFT_SLOT_DY south of the touch slot."""
    return {"TFT": (TFT_TAIL[0] - SLOT_MARGIN, SLOT_Y0 + TFT_SLOT_DY,
                    TFT_TAIL[0] + TFT_TAIL[1] + SLOT_MARGIN, SLOT_Y1 + TFT_SLOT_DY),
            "touch": (CTP_FLEX[0] - SLOT_MARGIN, SLOT_Y0, CTP_TAIL[0] + CTP_TAIL[1] + SLOT_MARGIN, SLOT_Y1)}


def slot_outline(board):
    """The tail slots as one cutout: the touch slot (north, east) reaches west over the TFT
    slot's east end (south), and the gap between them in that overlap is cut too, since a
    0.9 mm web there would be the board's weakest point. Ends fully rounded (a router bit
    leaves those anyway); the two inside corners of the step are sharp, board material."""
    tx0, ty0, tx1, ty1 = slot_boxes()["TFT"]
    cx0, cy0, cx1, cy1 = slot_boxes()["touch"]
    assert cy1 < ty0 and tx0 < cx0 < tx1 < cx1, "slot_outline() assumes the touch slot north-east of the TFT slot, overlapping it in x"
    r = (SLOT_Y1 - SLOT_Y0) / 2
    seg(board, (cx0 + r, cy0), (cx1 - r, cy0)); arc(board, (cx1 - r, cy0 + r), r, -90, 90)
    seg(board, (cx1 - r, cy1), (tx1, cy1)); seg(board, (tx1, cy1), (tx1, ty1 - r))
    arc(board, (tx1 - r, ty1 - r), r, 0, 90); seg(board, (tx1 - r, ty1), (tx0 + r, ty1))
    arc(board, (tx0 + r, ty0 + r), r, 90, 270); seg(board, (tx0 + r, ty0), (cx0, ty0))
    seg(board, (cx0, ty0), (cx0, cy0 + r)); arc(board, (cx0 + r, cy0 + r), r, 180, 270)


def drawing(board, x0, y0, x1, y1, layer, width=0.1):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetStart(pcbnew.VECTOR2I_MM(x0, y0))
    s.SetEnd(pcbnew.VECTOR2I_MM(x1, y1))
    s.SetLayer(layer)
    s.SetWidth(pcbnew.FromMM(width))
    board.Add(s)


def segment(board, x0, y0, x1, y1, layer, width=0.1):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
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


def gnd_pour(board, layer, net="GND"):
    z = pcbnew.ZONE(board)
    ls = pcbnew.LSET()
    ls.AddLayer(layer)
    z.SetLayerSet(ls)     # ZONE.SetLayer() silently leaves the zone on F.Cu
    z.SetNet(board.FindNet(net))
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
    for ref, (x, y) in HOLES.items():
        ring = [(x + HOLE_RING * math.cos(math.radians(a)), y + HOLE_RING * math.sin(math.radians(a)))
                for a in range(0, 360, 30)]
        rule_area(board, f"no parts: hole {ref}", ring, [pcbnew.B_Cu], footprints=True)
    u11 = board.FindFootprintByReference("U11")
    xs = [pcbnew.ToMM(p.GetPosition().x) for p in u11.Pads()]
    ys = [pcbnew.ToMM(p.GetPosition().y) for p in u11.Pads()]
    rule_area(board, "no tracks/vias: under the radio module",
              rect(min(xs) + 1.3, min(ys) + 1.3, max(xs) - 1.3, max(ys) - 1.3),
              [pcbnew.B_Cu if u11.IsFlipped() else pcbnew.F_Cu], tracks=True, vias=True)
    keepout(board, *antenna_keepout(board), "E73 antenna")


def antenna_keepout(board):
    """No copper on any layer from the E73's antenna-end pad row (pads 1/43) to the
    board edge the antenna faces, over the module's width plus 1.5 mm each side."""
    u11 = board.FindFootprintByReference("U11")
    mm = pcbnew.ToMM
    pads = {p.GetNumber(): (mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in u11.Pads()}
    xs = [x for x, _ in pads.values()]
    ys = [y for _, y in pads.values()]
    ax = (pads["1"][0] + pads["43"][0]) / 2 - (pads["11"][0] + pads["25"][0]) / 2
    ay = (pads["1"][1] + pads["43"][1]) / 2 - (pads["11"][1] + pads["25"][1]) / 2
    row = 0.6   # half the antenna-end pads' length
    if abs(ay) > abs(ax):
        x0, x1 = max(min(xs) - 1.5, 0.0), min(max(xs) + 1.5, W)
        return (x0, pads["1"][1] + row, x1, H) if ay > 0 else (x0, 0.0, x1, pads["1"][1] - row)
    y0, y1 = max(min(ys) - 1.5, 0.0), min(max(ys) + 1.5, H)
    return (pads["1"][0] + row, y0, W, y1) if ax > 0 else (0.0, y0, pads["1"][0] - row, y1)


def mounting_holes(board):
    io = pcbnew.PCB_IO_KICAD_SEXPR()
    lib = str(syncBoard.KICAD_LIBS / "MountingHole.pretty")
    for ref, (x, y) in HOLES.items():
        fp = io.FootprintLoad(lib, "MountingHole_2.2mm_M2_Pad_Via")
        fp.SetFPID(pcbnew.LIB_ID("MountingHole", "MountingHole_2.2mm_M2_Pad_Via"))
        fp.SetReference(ref)
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
    drawing(board, CTP_FLEX[0], CTP_FLEX_END_Y, sum(CTP_FLEX), SLOT_Y0, d)
    text(board, "TOUCH FLEX BLOCK", CTP_FLEX[0] + 3.0, (CTP_FLEX_END_Y + SLOT_Y0) / 2, d, 0.7, 0.1, 90)
    # Camera module (OV2640 ESP32-CAM class, ~8.5 x 8.5 mm head) and its lens.
    drawing(board, 4.0, 4.5, 12.5, 13.0, d)
    text(board, "CAMERA", 8.25, 8.75, d, 0.7, 0.1)
    # the camera head on the silk, its corner notched clear of mounting hole H1's ring
    n = 2.0
    for a, c in (((4.0 + n, 4.5), (12.5, 4.5)), ((12.5, 4.5), (12.5, 13.0)), ((12.5, 13.0), (4.0, 13.0)),
                 ((4.0, 13.0), (4.0, 4.5 + n))):
        segment(board, *a, *c, pcbnew.B_SilkS, 0.15)
    text(board, "QR CAMERA", 9.0, 2.6, pcbnew.B_SilkS, 0.8, 0.15)
    # Antenna keep-out marker.
    ax0, ay0, ax1, ay1 = ANT_KEEPOUT
    text(board, "ANT", (ax0 + ax1) / 2, (ay0 + ay1) / 2, pcbnew.B_SilkS, 0.8, 0.15)
    # Switch and light legends: the two things a user must be able to read.
    # Which lever end is AIRGAP is a bring-up check (docs/design.md), so the
    # legend names the switch, not a position.
    text(board, "AIRGAP", 5.2, 50.4, pcbnew.B_SilkS, 0.8, 0.15, 90)
    text(board, "RADIO", 2.6, 64.4, pcbnew.B_SilkS, 0.8, 0.15)
    lx0, ly0, lx1, ly1 = LABEL_SLOTS["SECURE ELEMENT"]
    text(board, "SECURE ELEMENT", (lx0 + lx1) / 2, (ly0 + ly1) / 2, pcbnew.B_SilkS, 0.8, 0.15)
    text(board, "AMBIRE", 40.0, 9.0, pcbnew.B_SilkS, 2.0, 0.3)
    text(board, "Ambire signer · proto 0", 13.0, H - 1.6, pcbnew.B_SilkS, 0.7, 0.15)


def write_netclasses(pro):
    """NETCLASSES into a loaded .kicad_pro (dict): classes, net patterns, width presets."""
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
    ds["track_widths"] = [0.0, 0.15, 0.2, 0.25, 0.4, 0.6, 0.8, 1.0]
    ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.45, "drill": 0.25},
                            {"diameter": 0.6, "drill": 0.3}]


def write_project():
    tmpl = HERE.parent / "nano/board/nano.kicad_pro"
    pro = json.load(open(PRO if PRO.exists() else tmpl))
    pro["meta"]["filename"] = PRO.name
    write_netclasses(pro)
    ds = pro["board"]["design_settings"]
    # JLC 4-layer standard capability: 0.09 mm track/space, 0.2 mm drill, 0.45 mm via.
    ds["rules"].update(min_clearance=0.1, min_track_width=0.1, min_via_diameter=0.45,
                       min_through_hole_diameter=0.2, min_hole_to_hole=0.25,
                       min_copper_edge_clearance=0.25,   # JLC: 0.2 to a routed edge min_hole_clearance=0.2,
                       min_text_height=0.7, min_text_thickness=0.1)
    ds["rule_severities"].update(silk_over_copper="warning", text_height="warning", text_thickness="warning",
                                 starved_thermal="warning")
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
    tb.SetTitle("Ambire Ice")
    tb.SetRevision("proto 0")
    rounded_rect(board, 0, 0, W, H, R)
    slot_outline(board)

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
        style_text(fp, side)

    legalize(board)
    if "--no-optimize" not in sys.argv:
        # Ratsnest pass: rotate / nudge / swap parts to cut crossings and airwire
        # length, inside the same legality rules (scripts/optimizePlacement.py).
        import optimizePlacement
        optimizePlacement.optimize(board, radius=OPT_RADIUS, passes=10)
    mounting_holes(board)
    # Stack-up roles: every part sits on B.Cu, whose return currents run in the plane
    # next to it, In2 -- so In2 is the solid GND. In1 is the 3.3 V plane (every V3V3
    # pin and decap drops a via to it); the front copper, the free routing layer for
    # the highways between blocks, references it. Outer layers are GND-poured.
    for layer in (pcbnew.F_Cu, pcbnew.In2_Cu, pcbnew.B_Cu):
        gnd_pour(board, layer)
    gnd_pour(board, pcbnew.In1_Cu, "V3V3")
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
