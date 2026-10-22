#!/usr/bin/env python3
"""Fail unless the placed connectors face the way the mechanical design needs.

    $KICAD_PY scripts/checkFpc.py [board/wallet.kicad_pcb]

No copper check sees this: a flex connector rotated 180 degrees is DRC-clean and
the display stays dark. The rules here are the conclusions of docs/design.md
("Display tails"), re-derived from the placed geometry on every run:

  - J_TFT, J_CTP on the back face, opening SOUTH toward the slots (the opening is
    the side of the land's mounting tabs on the 54132 and the HC-FPC).
  - J_TFT: the pad carrying panel pin 1 (pad 40, pad = 41 - panel pin) is the
    WEST-most signal pad, because the tail is never twisted and panel pin 1 is at
    the panel's west end seen from the front.
  - J_CTP: touch pin 1 (pad 1) is the WEST-most signal pad.
  - J_CAM opens WEST toward the camera; J1 (USB-C) and the airgap switch lever face
    the board edge they sit on; the E73 antenna end is SOUTH, at the board edge,
    clear of the AAA cell on the front.
"""
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent.parent
board = pcbnew.LoadBoard(sys.argv[1] if len(sys.argv) > 1 else str(HERE / "board/wallet.kicad_pcb"))
errors = []


def pads(fp, nums):
    return [p for p in fp.Pads() if p.GetNumber() in nums]


def centre(ps):
    xs = [pcbnew.ToMM(p.GetPosition().x) for p in ps]
    ys = [pcbnew.ToMM(p.GetPosition().y) for p in ps]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def facing(fp, signal, tabs):
    """Direction from the signal-pad row to the mounting tabs, i.e. the opening."""
    sx, sy = centre(pads(fp, signal))
    tx, ty = centre(pads(fp, tabs))
    dx, dy = tx - sx, ty - sy
    if abs(dx) > abs(dy):
        return "east" if dx > 0 else "west"
    return "south" if dy > 0 else "north"


def expect(cond, msg):
    if not cond:
        errors.append(msg)


fp = {f.GetReference(): f for f in board.GetFootprints()}
sig40 = {str(i) for i in range(1, 41)}
for ref, want_side in (("J_TFT", True), ("J_CTP", True), ("J_CAM", True), ("U11", True), ("J1", True)):
    expect(fp[ref].IsFlipped() == want_side, f"{ref} must be on the back face")

expect(facing(fp["J_TFT"], sig40, {"41", "42"}) == "south", "J_TFT must open south, toward the TFT slot")
west = min(pads(fp["J_TFT"], sig40), key=lambda p: p.GetPosition().x).GetNumber()
expect(west == "40", f"J_TFT west-most pad is {west}; panel pin 1 is west and must land on pad 40")

expect(facing(fp["J_CTP"], {str(i) for i in range(1, 7)}, {"7", "8"}) == "south",
       "J_CTP must open south, toward the touch slot")
west = min(pads(fp["J_CTP"], {str(i) for i in range(1, 7)}), key=lambda p: p.GetPosition().x).GetNumber()
expect(west == "1", f"J_CTP west-most pad is {west}; touch pin 1 is west and must land on pad 1")

expect(facing(fp["J_CAM"], {str(i) for i in range(1, 25)}, {"MP"}) == "west", "J_CAM must open west, toward the camera")

# USB-C: the signal row sits inboard of the shield legs' far pair, mouth on the south edge.
sx, sy = centre(pads(fp["J1"], {"A1", "A4", "A5", "A6", "A7", "A8", "A9", "A12"}))
expect(sy < pcbnew.ToMM(fp["J1"].GetPosition().y), "J1's contact row must be north of its centre (mouth south)")

# Airgap lever: the switch's three terminals are inboard (east) of its body centre.
tx, _ = centre(pads(fp["SW_AIR"], {"1", "2", "3"}))
expect(tx > pcbnew.ToMM(fp["SW_AIR"].GetPosition().x), "SW_AIR terminals must be east of the body (lever out of the west edge)")


# E73: the pad-free antenna end (beyond pad 1/43's row) is south of the pad field.
_, ey = centre(pads(fp["U11"], {"1", "43"}))
_, fy = centre(pads(fp["U11"], {"11", "25"}))
expect(ey > fy, "U11 antenna end must be south (pads 1/43 nearer the south edge than 11/25)")

# The AAA cell lies across the front between its clips; its metal can must not
# sit over the antenna keep-out on the other side of the board. The cell is
# 10.5 mm across, centred on the clips' pads (MY-AAA-19-J/20-J).
CELL_R = 10.5 / 2 + 0.25
ant = next((z for z in board.Zones() if z.GetIsRuleArea() and z.GetZoneName() == "E73 antenna"), None)
if ant is None:
    errors.append("no 'E73 antenna' rule area on the board (scripts/refreshKeepouts.py draws it)")
else:
    ab = ant.GetBoundingBox()
    ax0, ay0, ax1, ay1 = (pcbnew.ToMM(v) for v in (ab.GetLeft(), ab.GetTop(), ab.GetRight(), ab.GetBottom()))
    (nx, ny), (px, py) = centre(list(fp["BT_N"].Pads())), centre(list(fp["BT_P"].Pads()))
    expect(fp["BT_N"].GetLayer() == pcbnew.F_Cu and fp["BT_P"].GetLayer() == pcbnew.F_Cu,
           "the cell clips BT_N/BT_P must be on the front face")
    cy0, cy1 = min(ny, py) - CELL_R, max(ny, py) + CELL_R
    cx0, cx1 = min(nx, px), max(nx, px)
    expect(not (cx0 < ax1 and ax0 < cx1 and cy0 < ay1 and ay0 < cy1),
           f"the cell (y {cy0:.1f}..{cy1:.1f}) lies over the antenna keep-out (y {ay0:.1f}..{ay1:.1f})")
    print(f"cell y {cy0:.1f}..{cy1:.1f}, antenna keep-out y {ay0:.1f}..{ay1:.1f}: {max(ay0 - cy1, cy0 - ay1):.1f} mm apart")
    # The charger's NTC must sit under the cell (on the back, through the board): its
    # 0-45 C cut-off and the MCU's dT/dt both read the cell's temperature through it.
    tx, ty = centre(list(fp["RT_CHG"].Pads()))
    expect(cx0 < tx < cx1 and ny - 10.5 / 2 < ty < ny + 10.5 / 2,
           f"RT_CHG at ({tx:.1f}, {ty:.1f}) is not under the cell (x {cx0:.1f}..{cx1:.1f}, y {cy0:.1f}..{cy1:.1f})")

# Tail length: where each placed connector's contact row is against where the
# tail's end lands (newBoard.py's budget). Short = the tail cannot reach; more
# than ~3 mm of slack = a loop of flex with nowhere to go.
sys.path.insert(0, str(Path(__file__).parent))
import newBoard as nb  # noqa: E402

for ref, end_y, row in (("J_TFT", nb.TFT_END_Y, nb.TFT_ROW), ("J_CTP", nb.CTP_END_Y, nb.CTP_ROW)):
    row_y = pcbnew.ToMM(fp[ref].GetPosition().y) - row
    slack = end_y - row_y      # positive: the tail end would sit south of the row
    if slack > 0.5:
        errors.append(f"{ref}: tail ends {slack:.1f} mm short of the contact row; move the connector south")
    elif slack < -3.0:
        errors.append(f"{ref}: {-slack:.1f} mm of tail slack; move the connector north")
    else:
        print(f"{ref}: tail end within {abs(slack):.1f} mm of the contact row")

# Camera: the tail runs east from the head's edge into J_CAM; a stock module must
# neither be stretched short of the contacts nor bow more than CAM_MAX_BOW.
avail = pcbnew.ToMM(fp["J_CAM"].GetPosition().x) + nb.CAM_END_DX - nb.CAMERA_HEAD[2]
for tail in nb.CAM_TAIL:
    slack = tail - avail
    if slack < -0.3:
        errors.append(f"J_CAM: a {tail:.1f} mm camera tail ends {-slack:.1f} mm short of the contacts; move J_CAM west")
    elif slack > nb.CAM_MAX_BOW:
        errors.append(f"J_CAM: a {tail:.1f} mm camera tail bows {slack:.1f} mm; move J_CAM east")
    else:
        print(f"J_CAM: a {tail:.1f} mm camera tail lands with {slack:.1f} mm of bow")

# Under the flex tails only low parts: the tail lies flat on them. Anything
# whose land is not a chip passive or a small SMD IC package fails.
LOW = ("Capacitor_SMD:", "Resistor_SMD:", "Inductor_SMD:", "LED_SMD:", "Package_TO_SOT_SMD:",
       "Package_DFN_QFN:", "Package_SON:", "wallet:Texas_", "wallet:NXP_", "Crystal:")
fp_by_ref = fp
tft_y0 = pcbnew.ToMM(fp["J_TFT"].GetBoundingBox(False).GetBottom())
ctp_y0 = pcbnew.ToMM(fp["J_CTP"].GetBoundingBox(False).GetBottom())
corridors = {"TFT tail": (nb.TFT_TAIL[0], tft_y0, nb.TFT_TAIL[0] + nb.TFT_TAIL[1], nb.SLOT_Y0),
             "touch tail": (nb.CTP_TAIL[0], ctp_y0, nb.CTP_TAIL[0] + nb.CTP_TAIL[1], nb.SLOT_Y0)}
for f in board.GetFootprints():
    if not f.IsFlipped() or f.GetReference() in ("J_TFT", "J_CTP"):
        continue
    b = f.GetBoundingBox(False)
    x0, y0, x1, y1 = (pcbnew.ToMM(v) for v in (b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom()))
    for name, (cx0, cy0, cx1, cy1) in corridors.items():
        if x0 < cx1 and cx0 < x1 and y0 < cy1 and cy0 < y1 and not f.GetFPIDAsString().startswith(LOW):
            errors.append(f"{f.GetReference()} ({f.GetFPIDAsString()}) is under the {name}: only low parts may sit there")

for e in errors:
    print("ERROR", e)
print(f"connector orientation: {'FAIL' if errors else 'ok'}")
sys.exit(1 if errors else 0)
