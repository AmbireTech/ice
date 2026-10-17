#!/usr/bin/env python3
"""Build a placement variant of a board for a routing comparison.

    $KICAD_PY scripts/placeVariant.py in.kicad_pcb out.kicad_pcb SPEC.json

SPEC = {"seed":   [[ref, x, y, rot_or_null], ...],          # put parts here first (mm, deg)
        "blocks": [[ic, [satellite, ...] or null], ...],    # then rebuild each block (place_block)
        "radius": 3.0}
A block's chip is tried at every rotation with its satellites re-seated around it
(optimizePlacement.place_block); null satellites = the ones satellites() finds.
Copper is not touched: strip or re-route what the moves invalidate, then judge
the result with scripts/routeGauge.py.
"""
import json
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import optimizePlacement as op  # noqa: E402


def main():
    src, dst, spec = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))
    board = pcbnew.LoadBoard(src)
    for ref, x, y, rot in spec.get("seed", []):
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            raise SystemExit(f"{ref}: not on the board")
        fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        if rot is not None:
            fp.SetOrientationDegrees(rot)
        fp.Reference().SetPosition(fp.GetPosition())
    for ic, sats in spec.get("blocks", []):
        op.place_block(board, ic, sats, radius=spec.get("radius", 3.0))
    board.Save(dst)
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
