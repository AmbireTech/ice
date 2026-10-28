#!/usr/bin/env python3
"""3D model of the Xinyangze YZP0048-20048-04025-03 magnetic pogo male (LCSC C5126845),
from its drawing (2022-03-24): body 21.20 x 6.86 mm (stadium), 2.80 thick, a 5.03 mm
tab 2.31 mm out from one long side, magnets 15.41 mm apart in its ends; four pins at
2.50 mm, 0.90 mm plungers standing to 4.10 mm free (3.10 working, 2.90 pressed); tails
0.60 mm across, 0.70 mm long below the seating face (through-hole: they end inside a
0.8 mm board). EasyEDA's model for it is drawn but its land is not usable (see the
footprint's descr), so the board uses this model and our own land.

KiCad footprint frame: origin at the body's centre, long axis X, tab toward +Y (screen
down), which is -Y in model space.

    ../../.venv-enclosure/bin/python pack/connModel.py   (from hardware/ambire-hw; writes pack/3d/...step)
"""
from pathlib import Path

from build123d import Box, Cylinder, Pos, SlotOverall, export_step, extrude

L, W, T = 21.20, 6.86, 2.80
TAB_W, TAB_L, TAB_T = 5.03, 2.31, 1.00
PITCH, PLUNGER_D, FREE = 2.50, 0.90, 4.10
TAIL_D, TAIL_L = 0.60, 0.70

body = extrude(SlotOverall(L, W), amount=T)
body += Pos(0, -(W / 2 + TAB_L / 2 - 0.01), TAB_T / 2) * Box(TAB_W, TAB_L + 0.02, TAB_T)
for i in range(4):
    x = (i - 1.5) * PITCH
    body += Pos(x, 0, T + (FREE - T) / 2) * Cylinder(PLUNGER_D / 2, FREE - T)
    body += Pos(x, 0, -TAIL_L / 2 + 0.01) * Cylinder(TAIL_D / 2, TAIL_L + 0.02)
out = Path(__file__).resolve().parent / "3d/Xinyangze_YZP0048-20048-04025-03.step"
export_step(body, str(out))
print(out)
