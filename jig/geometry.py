"""The jig's numbers shared by its board (makeJig.py, KiCad's Python) and its printed
base and lid (makeFixture.py, build123d): plain Python, no imports.

Coordinates are the device board's (x east, y south, mm, seen from its front). Heights
are from the base's top face. The device lies face down on the poles' 4 mm shoulders,
GAP above the jig's parts face: the spring pins' working height.
"""
# the device board's holes (2.2 mm, plated) and its outline
HOLES = {"H1": (3.20, 3.20), "H2": (44.80, 3.20), "H4": (44.80, 83.80)}
DEVICE_OUTLINE = (0.0, 0.0, 48.0, 87.0, 4.0)
DEVICE_T = 1.0
# the jig: its outline, thickness and the fourth pole (jig only, on the strip south of the selector)
JIG_OUTLINE = (-13.0, -3.0, 49.0, 90.0, 2.0)
JIG_T = 1.6
EXTRA_POLE = (-8.5, 66.0)
POLE_HOLE_D = 4.2            # the jig's holes for the poles' 4 mm sections
GAP = 5.0                    # jig parts face to device front: the pins' 5.0 mm working height
# the poles: an 8 mm foot the jig rests on, a 4 mm section through the jig and up to the
# device's front, a 2 mm tip through the device's hole and into the lid
FOOT_D, FOOT_H = 8.0, 5.0
SHANK_D = 4.0
TIP_D, TIP_ABOVE = 2.0, 3.0  # the tip stands this far above the device's back
# the lid: it rests on the device's three hole rings (bosses), clear of the back's parts
LID_T = 2.5
BOSS_D, BOSS_H = 5.0, 4.5    # the back's tallest part is the USB-C, ~3.2 mm
LID_HOLE_D = 2.6
BASE_T = 3.0
