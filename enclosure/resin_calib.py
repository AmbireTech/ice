#!/usr/bin/env python3
"""Measure a resin's shrink and bleed, for slice_resin.py's --shrink and --bleed.

The method is J. Mrazek's (blog.honzamrazek.cz, 2022-06, "Fighting resin shrinkage and
exposure bleeding"): a printed length x comes out as s*x + 2e between two outer faces and
s*x - 2e between two inner faces (s: the shrink, e: each edge's growth past its outline,
from light bleeding past the pixels). Six caliper readings over three lengths each way fit
both by least squares.

  python3 enclosure/resin_calib.py model    ->  enclosure/out/resin_calib.stl
  python3 enclosure/slice_resin.py --stl enclosure/out/resin_calib.stl --shrink 0 [--exposure S ...]
  python3 enclosure/resin_calib.py solve --x O20 O50 O100 I30 I60 I80 [--y O20 O50 O100 I30 I60 I80]

The model is two bars, one along the printer's X and one along its Y (the M7 Pro's pixels
are not square: 16.8 x 24.8 um). Each bar carries two rows of blocks:

  outer row  block pairs whose OUTER faces are 20, 50 and 100 mm apart (the 100 pair are
             the bar's ends); measure across them with the caliper's big jaws
  inner row  block pairs whose INNER faces are 30, 60 and 80 mm apart; measure between them
             with the small jaws

Slice it with --shrink 0 and no --bleed, at the exposure the real print will use, and wash
and cure it as the real print. It keeps shrinking for a few days; a cure at 50 C for an hour
settles it sooner. Measure near the blocks' tops (the bottom layers are over-exposed).

Printed at two exposures, it also tells over-exposure: the same bleed at both means the
shorter one is enough; less bleed at the shorter one means the longer one over-exposes.

Needs numpy, trimesh and manifold3d.
"""
import argparse
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh

HERE = Path(__file__).resolve().parent
OUTER = (20.0, 50.0, 100.0)      # outer faces this far apart
INNER = (30.0, 60.0, 80.0)       # inner faces this far apart
BAR_L, BAR_W, BAR_T = 100.0, 14.0, 2.0
ROW_W, ROW_GAP = 6.0, 2.0        # each row's blocks across the bar; between the rows
BLOCK_T, BLOCK_H = 3.0, 5.0      # along the bar; above it
BAR_SPACING = 20.0               # between the two bars


def box(x0, x1, y0, y1, z0, z1):
    return mf.Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def bar():
    """One bar along x, centred on the origin, on z 0."""
    solid = box(-BAR_L / 2, BAR_L / 2, -BAR_W / 2, BAR_W / 2, 0, BAR_T)
    top = BAR_T + BLOCK_H
    spans = []
    for L in OUTER:      # the outer face at L/2, the block inward of it
        spans += [(L / 2 - BLOCK_T, L / 2, ROW_GAP / 2, ROW_GAP / 2 + ROW_W)]
    for L in INNER:      # the inner face at L/2, the block outward of it
        spans += [(L / 2, L / 2 + BLOCK_T, -ROW_GAP / 2 - ROW_W, -ROW_GAP / 2)]
    edges = sorted((a, b) for a, b, _, _ in spans)
    for (a0, a1), (b0, _) in zip(edges, edges[1:]):
        if b0 - a1 < 3.0:
            raise SystemExit(f"blocks at {a0:g}-{a1:g} and {b0:g} leave no room for a caliper's jaw")
    if max(b for _, b, _, _ in spans) > BAR_L / 2 + 1e-9:
        raise SystemExit("a block runs past the bar's end")
    for u0, u1, y0, y1 in spans:
        for sgn in (1, -1):
            lo, hi = sorted((sgn * u0, sgn * u1))
            solid += box(lo, hi, y0, y1, BAR_T - 0.01, top)
    return solid


def model(out):
    b = bar()
    along_x = b.translate([0, BAR_L / 2 + BAR_SPACING / 2 + BAR_W / 2, 0])
    along_y = b.rotate([0, 0, 90]).translate([BAR_L / 2 + BAR_SPACING / 2 + BAR_W / 2, 0, 0])
    m = (along_x + along_y).to_mesh()
    mesh = trimesh.Trimesh(m.vert_properties[:, :3], m.tri_verts, process=True)
    if not mesh.is_watertight:
        raise SystemExit("the calibration bars' mesh is not watertight")
    mesh.export(out)
    print(f"{out}: two {BAR_L:g} mm bars, along X and along Y; outer pairs {OUTER}, inner pairs {INNER} mm")


def fit(readings):
    """s, e and the residuals from six readings: outer pairs then inner pairs."""
    x = np.array(OUTER + INNER)
    sign = np.array([1.0] * len(OUTER) + [-1.0] * len(INNER))
    A = np.stack([x, 2 * sign], axis=1)
    y = np.asarray(readings, float)
    (s, e), *_ = np.linalg.lstsq(A, y, rcond=None)
    return s, e, y - A @ np.array([s, e])


def solve(axes):
    shrink, bleed = [], []
    for name, readings in axes:
        s, e, res = fit(readings)
        shrink.append((1 - s) * 100)
        bleed.append(e / s)       # eroded before the part shrinks
        print(f"{name}: shrink {(1 - s) * 100:.3f} %, bleed {e * 1000:.0f} um; residuals "
              + " ".join(f"{r * 1000:+.0f}" for r in res) + " um (a reading off by more than ~30 um: measure it again)")
    sh = ",".join(f"{v:.2f}" for v in shrink)
    bl = max(0.0, float(np.mean(bleed)))
    print(f"slice with: --shrink {sh} --bleed {bl:.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("model", help="write the calibration bars' STL")
    m.add_argument("--out", type=Path, default=HERE / "out/resin_calib.stl")
    s = sub.add_parser("solve", help="shrink and bleed from the readings, mm")
    for axis in ("x", "y"):
        s.add_argument(f"--{axis}", type=float, nargs=6, metavar=("O20", "O50", "O100", "I30", "I60", "I80"),
                       help=f"the bar along {axis.upper()}: outer pairs, then inner pairs")
    args = ap.parse_args()
    if args.cmd == "model":
        model(args.out)
        return
    axes = [(n, v) for n, v in (("X", args.x), ("Y", args.y)) if v]
    if not axes:
        raise SystemExit("give --x and/or --y readings")
    solve(axes)


if __name__ == "__main__":
    main()
