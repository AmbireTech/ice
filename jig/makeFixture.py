#!/usr/bin/env python3
"""The jig's printed base and lid.

    ../../.venv-enclosure/bin/python jig/makeFixture.py            # jig/out/: base and lid, STL + STEP
    ../../.venv-enclosure/bin/python jig/makeFixture.py --check    # also: overlaps with the jig and the device

(from hardware/ambire-hw). The numbers are jig/geometry.py's, shared with the jig's board.

The base is a plate with four poles. On each pole's 8 mm foot rests the jig, parts face up;
its 4 mm section passes through the jig's hole and stands GAP above the jig's parts face,
where the device rests face down on three of them; their 2 mm tips go through the device's
holes H1, H2, H4 and up into the lid. The fourth pole only holds the jig, on its strip.
The lid rests on the device's three hole rings, clear of the parts on its back; a weight on
the lid (about 300 g) holds the device down against the spring pins' 7 x 25 g.

The model is the physical stack seen from above: the jig and the device both lie flipped
east-west against KiCad's front view, so a point at (x, y) on either board is at (-x, -y)
here (Y north, Z up from the base's top face). --check exports both boards from KiCad as
STEP, places them so and reports every overlap; the pins' tips touch their pads by design.
"""
import os
import subprocess
import sys
import tempfile

from build123d import (Cone, Cylinder, Pos, RectangleRounded, Rot, export_step, export_stl, extrude,
                       import_step)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import geometry as G  # noqa: E402

OUT = os.path.join(HERE, "out")
KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
DEVICE_PCB = os.path.join(os.path.dirname(HERE), "board", "wallet.kicad_pcb")
JIG_PCB = os.path.join(HERE, "jig.kicad_pcb")

JIG_UNDER = G.FOOT_H                       # the jig's plain face rests on the feet
JIG_TOP = JIG_UNDER + G.JIG_T               # its parts face
DEV_FRONT = JIG_TOP + G.GAP                 # the device's front, face down on the 4 mm shoulders
DEV_BACK = DEV_FRONT + G.DEVICE_T
LID_UNDER = DEV_BACK + G.BOSS_H


def at(x, y):
    """A board point (device coordinates, y south) in the model, seen from above."""
    return -x, -y


def plate(outline, z0, z1, margin=0.0):
    x0, y0, x1, y1, r = outline
    x0, y0, x1, y1, r = x0 - margin, y0 - margin, x1 + margin, y1 + margin, r + margin
    sk = RectangleRounded(x1 - x0, y1 - y0, r)
    cx, cy = at((x0 + x1) / 2, (y0 + y1) / 2)
    return Pos(cx, cy, z0) * extrude(sk, amount=z1 - z0)


def pole(x, y, device):
    """Foot, 4 mm shank and (for the device's holes) a 2 mm tip with a chamfered end."""
    X, Y = at(x, y)
    shank_top = DEV_FRONT if device else JIG_TOP + 2.0
    p = Pos(X, Y, 0) * Cylinder(G.FOOT_D / 2, G.FOOT_H, align=(1, 1, 0))
    p += Pos(X, Y, G.FOOT_H) * Cylinder(G.SHANK_D / 2, shank_top - G.FOOT_H, align=(1, 1, 0))
    if device:
        tip = G.DEVICE_T + G.TIP_ABOVE
        p += Pos(X, Y, DEV_FRONT) * Cylinder(G.TIP_D / 2, tip - 0.4, align=(1, 1, 0))
        p += Pos(X, Y, DEV_FRONT + tip - 0.4) * Cone(G.TIP_D / 2, G.TIP_D / 2 - 0.4, 0.4, align=(1, 1, 0))
    return p


def base():
    b = plate(G.JIG_OUTLINE, -G.BASE_T, 0.0)
    for x, y in G.HOLES.values():
        b += pole(x, y, device=True)
    b += pole(*G.EXTRA_POLE, device=False)
    return b


def lid():
    """In place: bosses down onto the hole rings, the plate above."""
    lo = plate(G.DEVICE_OUTLINE, LID_UNDER, LID_UNDER + G.LID_T, margin=2.0)
    for x, y in G.HOLES.values():
        X, Y = at(x, y)
        lo += Pos(X, Y, DEV_BACK) * Cylinder(G.BOSS_D / 2, G.BOSS_H, align=(1, 1, 0))
        lo -= Pos(X, Y, DEV_BACK - 1) * Cylinder(G.LID_HOLE_D / 2, G.BOSS_H + G.LID_T + 2, align=(1, 1, 0))
    return lo


def board_step(pcb, tmp):
    out = os.path.join(tmp, os.path.basename(pcb).replace(".kicad_pcb", ".step"))
    subprocess.run([KICAD_CLI, "pcb", "export", "step", "--force", "--subst-models", "-o", out, pcb],
                   check=True, capture_output=True)
    return import_step(out)


def check(base_, lid_):
    """Overlaps between the printed parts and the two boards, placed as they sit."""
    with tempfile.TemporaryDirectory() as tmp:
        # KiCad's STEP: X = x, Y = -y, the plain (B) face at z 0, the board up to its thickness.
        # Turned over about Y (x -> -x), so the B face (the jig's parts, the device's back) faces up.
        jig = Pos(0, 0, JIG_UNDER + G.JIG_T) * (Rot(0, 180, 0) * board_step(JIG_PCB, tmp))
        dev = Pos(0, 0, DEV_FRONT + G.DEVICE_T) * (Rot(0, 180, 0) * board_step(DEVICE_PCB, tmp))
    worst = []
    for a_name, a, b_name, b in [("base", base_, "jig", jig), ("base", base_, "device", dev),
                                 ("lid", lid_, "device", dev), ("lid", lid_, "jig", jig),
                                 ("jig", jig, "device", dev)]:
        v = (a & b).volume
        print(f"  {a_name} / {b_name}: {v:.3f} mm3 overlap")
        worst.append((v, a_name, b_name))
    return worst


def main():
    os.makedirs(OUT, exist_ok=True)
    b, lo = base(), lid()
    export_step(b, os.path.join(OUT, "base.step"))
    export_stl(b, os.path.join(OUT, "base.stl"))
    export_step(lo, os.path.join(OUT, "lid.step"))
    # printed plate down: turned over, its top face on the bed
    export_stl(Pos(0, 0, LID_UNDER + G.LID_T) * (Rot(180, 0, 0) * lo), os.path.join(OUT, "lid.stl"))
    print(f"base: plate {G.BASE_T} mm, poles to z {DEV_FRONT + G.DEVICE_T + G.TIP_ABOVE:.1f}; jig at z "
          f"{JIG_UNDER:.1f}-{JIG_TOP:.1f}, device front at {DEV_FRONT:.1f}, back at {DEV_BACK:.1f}; "
          f"lid {G.LID_T} mm on {G.BOSS_H} mm bosses -> {OUT}")
    if "--check" in sys.argv:
        check(b, lo)


if __name__ == "__main__":
    main()
