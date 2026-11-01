#!/usr/bin/env python3
"""3D model of the Xinyangze YZP0058-20048-02 single SMD spring pin (LCSC C5157341), from
its drawing (2015-05-14): a 2.00 mm flange 0.50 mm thick on the board, a 1.54 mm barrel to
2.80 mm, a 1.00 mm plunger with an R0.45 tip standing to 4.30 mm free (3.30 working, 3.10
pressed); a 0.40 mm peg 0.50 mm long below the seating face.

KiCad footprint frame: origin at the pin's axis, seating face at z 0.

    ../../.venv-enclosure/bin/python pack/connModel.py   (from hardware/ambire-hw; writes pack/3d/...step)
"""
from pathlib import Path

from build123d import Cylinder, Pos, Sphere, export_step

FLANGE_D, FLANGE_T, BARREL_D, BARREL_TOP = 2.00, 0.50, 1.54, 2.80
PLUNGER_D, TIP_R, FREE, PEG_D, PEG_L = 1.00, 0.45, 4.30, 0.40, 0.50

pin = Pos(0, 0, FLANGE_T / 2) * Cylinder(FLANGE_D / 2, FLANGE_T)
pin += Pos(0, 0, (FLANGE_T + BARREL_TOP) / 2) * Cylinder(BARREL_D / 2, BARREL_TOP - FLANGE_T)
pin += Pos(0, 0, (BARREL_TOP + FREE - TIP_R) / 2) * Cylinder(PLUNGER_D / 2, FREE - TIP_R - BARREL_TOP)
pin += Pos(0, 0, FREE - TIP_R) * Sphere(TIP_R)
pin += Pos(0, 0, -PEG_L / 2 + 0.01) * Cylinder(PEG_D / 2, PEG_L + 0.02)
out = Path(__file__).resolve().parent / "3d/Xinyangze_YZP0058-20048-02.step"
export_step(pin, str(out))
print(out)
