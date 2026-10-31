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
# (the radio may sit on either face: the front since the cell went to the back, 2026-10-25)
for ref, want_side in (("J_TFT", True), ("J_CTP", True), ("J_CAM", True), ("J1", True)):
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

# The AAA cell lies across the chin between its clips (front in proto 1 as built, the
# back since 2026-10-25: docs/cell-placement.md); its metal can must not sit over the
# antenna keep-out. The cell is 10.5 mm across, centred on the clips' pads
# (MY-AAA-19-J/20-J).
CELL_R = 10.5 / 2 + 0.25
ant = next((z for z in board.Zones() if z.GetIsRuleArea() and z.GetZoneName() == "E73 antenna"), None)
if ant is None:
    errors.append("no 'E73 antenna' rule area on the board (scripts/refreshKeepouts.py draws it)")
else:
    ab = ant.GetBoundingBox()
    ax0, ay0, ax1, ay1 = (pcbnew.ToMM(v) for v in (ab.GetLeft(), ab.GetTop(), ab.GetRight(), ab.GetBottom()))
has_cell = ant is not None and "BT_N" in fp and "BT_P" in fp    # v4: the cells are in a pack, off the board
cell_front = False
cy0 = None
if has_cell:
    (nx, ny), (px, py) = centre(list(fp["BT_N"].Pads())), centre(list(fp["BT_P"].Pads()))
    expect(fp["BT_N"].GetLayer() == fp["BT_P"].GetLayer(), "the cell clips BT_N/BT_P must be on the same face")
    cell_front = fp["BT_N"].GetLayer() == pcbnew.F_Cu
    cy0, cy1 = min(ny, py) - CELL_R, max(ny, py) + CELL_R
    cx0, cx1 = min(nx, px), max(nx, px)
    expect(not (cx0 < ax1 and ax0 < cx1 and cy0 < ay1 and ay0 < cy1),
           f"the cell (y {cy0:.1f}..{cy1:.1f}) lies over the antenna keep-out (y {ay0:.1f}..{ay1:.1f})")
    print(f"cell y {cy0:.1f}..{cy1:.1f}, antenna keep-out y {ay0:.1f}..{ay1:.1f}: {max(ay0 - cy1, cy0 - ay1):.1f} mm apart")
    # The charger's NTC must sit under the cell (on either face): its 0-45 C cut-off
    # and the MCU's dT/dt both read the cell's temperature through it.
    tx, ty = centre(list(fp["RT_CHG"].Pads()))
    expect(cx0 < tx < cx1 and ny - 10.5 / 2 < ty < ny + 10.5 / 2,
           f"RT_CHG at ({tx:.1f}, {ty:.1f}) is not under the cell (x {cx0:.1f}..{cx1:.1f}, y {cy0:.1f}..{cy1:.1f})")

# Tail length: where each placed connector's contact row is against where the
# tail's end lands (newBoard.py's budget). Short = the tail cannot reach; more
# than ~3 mm of slack = a loop of flex with nowhere to go.
sys.path.insert(0, str(Path(__file__).parent))
import newBoard as nb  # noqa: E402

# The flexes' run on the front: from the panel's south edge to their folds in the
# cutout. No front part's body (its fab outline) and not the cell may reach into it.
for name, (tx0, tw), fold in (("TFT flex root", nb.TFT_ROOT, nb.TFT_FOLD_Y), ("touch flex", nb.CTP_FLEX, nb.CTP_FOLD_Y)):
    drop_s = fold + nb.TAIL_CLR
    x0, x1 = tx0 - nb.SLOT_MARGIN, tx0 + tw + nb.SLOT_MARGIN
    bodies = []
    for f in board.GetFootprints():
        if f.GetLayer() != pcbnew.F_Cu:
            continue
        fab = [g.GetBoundingBox() for g in f.GraphicalItems() if g.GetLayer() == pcbnew.F_Fab]
        if fab:
            bodies.append((f.GetReference(), min(pcbnew.ToMM(b.GetLeft()) for b in fab),
                           min(pcbnew.ToMM(b.GetTop()) for b in fab), max(pcbnew.ToMM(b.GetRight()) for b in fab),
                           max(pcbnew.ToMM(b.GetBottom()) for b in fab)))
    if has_cell and cell_front:
        bodies.append(("the cell", cx0, cy0, cx1, cy1))
    for ref, bx0, by0, bx1, by1 in bodies:
        expect(not (bx0 < x1 and x0 < bx1 and by0 < drop_s and nb.PANEL_S < by1),
               f"{ref} (y {by0:.2f}..{by1:.2f}) stands in the {name}'s drop (y {nb.PANEL_S:.2f}..{drop_s:.2f}, "
               f"x {x0:.1f}..{x1:.1f})")
print(f"tails' drop from y {nb.PANEL_S:.2f}: clear of the front parts" + (f" and the cell (north edge {cy0:.2f})" if has_cell else ""))

# Parts under the cell, on the clips' face (newBoard.under_cell_room): the owner's
# ladder row under the front cell (2026-10-23), the chin's back parts under the back one.
if has_cell:
    cell = nb.cell_geometry(board)
    under = []
    for f in board.GetFootprints():
        room = nb.under_cell_room(f, cell)
        if room is None:
            continue
        left, pkg = room
        if left is None:
            errors.append(f"{f.GetReference()} stands under the cell and has no height bound ({pkg})")
            continue
        expect(left >= 0, f"{f.GetReference()} under the cell: {-left:.2f} mm too tall for the cell's underside there")
        under.append(f"{f.GetReference()} {left + nb.UNDER_CELL_CLR:.2f}")
    if under:
        print("under the cell (mm to spare): " + ", ".join(under))

for ref, end_y, row in (("J_TFT", nb.TFT_END_Y, nb.TFT_ROW), ("J_CTP", nb.CTP_END_Y, nb.CTP_ROW)):
    row_y = pcbnew.ToMM(fp[ref].GetPosition().y) - row
    slack = end_y - row_y      # positive: the tail end would sit south of the row
    if slack > 0.5:
        errors.append(f"{ref}: tail ends {slack:.1f} mm short of the contact row; move the connector south")
    elif slack < -3.0 - 1e-6:
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

# Under the flex tails only low parts (newBoard.tail_corridors / LOW_LIBS).
for f in board.GetFootprints():
    for name in nb.under_tail(f, nb.tail_corridors(board)):
        errors.append(f"{f.GetReference()} ({f.GetFPIDAsString()}) is under the {name}: only low parts may sit there")

for e in errors:
    print("ERROR", e)
print(f"connector orientation: {'FAIL' if errors else 'ok'}")
sys.exit(1 if errors else 0)
