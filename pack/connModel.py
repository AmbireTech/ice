#!/usr/bin/env python3
"""3D model of the Xinyangze YZR0158-20050-04025-01 magnetic pogo male, from its drawing
(2022-03-24): body 21.20 x 6.86 mm (obround), 2.80 thick, a 5.03 mm tab 2.31 mm out from
one long side, the pins at 2.50 mm, 0.90 mm plungers standing to 5.00 mm free. EasyEDA's
model for C5126843 is the YZ92015035T receptacle's, so the board uses this one.

KiCad footprint frame: origin at the body's centre, long axis X, tab toward +Y (screen
down), which is -Y in model space.

    ../../.venv-enclosure/bin/python pack/connModel.py   (from hardware/ambire-hw; writes pack/3d/...step)
"""
from pathlib import Path

from build123d import Cylinder, Pos, Box, SlotOverall, export_step, extrude

L, W, T = 21.20, 6.86, 2.80
TAB_W, TAB_L, TAB_T = 5.03, 2.31, 1.00
PITCH, PLUNGER_D, FREE = 2.50, 0.90, 5.00

body = extrude(SlotOverall(L, W), amount=T)
body += Pos(0, -(W / 2 + TAB_L / 2 - 0.01), TAB_T / 2) * Box(TAB_W, TAB_L + 0.02, TAB_T)
for i in range(4):
    x = (i - 1.5) * PITCH
    body += Pos(x, 0, T + (FREE - T) / 2) * Cylinder(PLUNGER_D / 2, FREE - T)
out = Path(__file__).resolve().parent / "3d/Xinyangze_YZR0158-20050-04025-01.step"
export_step(body, str(out))
print(out)
