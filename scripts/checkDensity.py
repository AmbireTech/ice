#!/usr/bin/env python3
"""Routability gate: every part keeps a routing gap to its neighbours.

    $KICAD_PY scripts/checkDensity.py [board.kicad_pcb] [--gap 0.4]

DRC passes a board whose courtyards touch, and such a board cannot be routed:
with 0.1 mm between courtyards there is no room for a via (0.45 mm) or for a
track between two parts, so every net in a packed cluster has to leave through
the cluster's edge. This measures the gap between each pair of same-side
courtyards and fails below --gap (default 0.4 mm: a via or two 0.15 mm tracks
fit between the two lands once the courtyards' own ~0.25 mm is counted).
Exempt: pairs that a vendor land puts together on purpose (none yet).
"""
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent.parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
gap = float(sys.argv[sys.argv.index("--gap") + 1]) if "--gap" in sys.argv else 0.4
if "--gap" in sys.argv:
    args = [a for a in args if a != sys.argv[sys.argv.index("--gap") + 1]]
board = pcbnew.LoadBoard(args[0] if args else str(HERE / "board/wallet.kicad_pcb"))

boxes = []
for fp in board.GetFootprints():
    if fp.GetAttributes() & pcbnew.FP_BOARD_ONLY or fp.GetReference().startswith(("TP_", "H")):
        continue
    fp.BuildCourtyardCaches()
    cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
    if not cy.OutlineCount():
        continue
    b = cy.BBox()
    boxes.append((fp.GetReference(), fp.IsFlipped(),
                  tuple(pcbnew.ToMM(v) for v in (b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom()))))


def dist(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return (dx * dx + dy * dy) ** 0.5


tight = []
for i, (ra, sa, a) in enumerate(boxes):
    for rb, sb, b in boxes[i + 1:]:
        if sa == sb:
            d = dist(a, b)
            if d < gap:
                tight.append((d, ra, rb))
tight.sort()
for d, ra, rb in tight:
    print(f"TIGHT {d:.2f} mm  {ra} - {rb}")
print(f"{len(boxes)} parts, {len(tight)} pairs closer than {gap} mm: {'FAIL' if tight else 'ok'}")
sys.exit(1 if tight else 0)
