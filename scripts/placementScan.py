#!/usr/bin/env python3
"""Placement findings the owner fixes by hand, found before they are routed around.

    $KICAD_PY scripts/placementScan.py [board.kicad_pcb] [--y-min 64]

From the owner's edits of 2026-10-23 on the chin:
  - a supply decap far from its chip's rail pin. The USB buck's input cap sat on
    USB-C's VBUS pin (the other face) instead of the buck's VIN pin; decaps belong at
    a chip's pin, never a connector's (optimizePlacement now scores them so);
  - a two-pad part with a net whose every other pad is on the other face: a via per
    such net. The joystick's ladder sat on the back, its four direction lines each
    dropping through a via to the joystick on the front; the owner put the ladder on
    the front, in a row between the joystick and the cell.
Prints the findings in the region south of --y-min (default: the chin); exit 0 always,
it is a review aid, not a gate. scripts/checkFpc.py checks parts under the cell.
"""
import argparse
import math
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import newBoard as nb  # noqa: E402
import optimizePlacement as op  # noqa: E402

DECAP_FAR_MM = 2.0
SUPPLIES = {n for _, _, _, _, _, nets in nb.NETCLASSES for n in nets} | {"V3V3"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board", nargs="?", default=str(nb.HERE / "board/wallet.kicad_pcb"))
    ap.add_argument("--y-min", type=float, default=64.0)
    a = ap.parse_args()
    m = op.Model(pcbnew.LoadBoard(a.board))
    region = {r for r, p in m.parts.items() if p.pos()[1] > a.y_min and 0 < p.pos()[0] < nb.W}

    print(f"supply decaps more than {DECAP_FAR_MM} mm from their chip's rail pin:")
    for r in sorted(region & set(m.decaps)):
        rail = m.decaps[r][0]
        d = m.decap_cost(r) / op.DECAP_MM
        if rail in SUPPLIES and d > DECAP_FAR_MM:
            print(f"  {r:10s} {rail:10s} {d:4.1f} mm")

    print("two-pad parts with a net that only reaches the other face (a via each):")
    for r in sorted(region):
        p = m.parts[r]
        pads = list(p.fp.Pads())
        if len(pads) != 2 or r.startswith(("TP", "H")):
            continue
        forced = []
        for pd in pads:
            n = pd.GetNetname()
            if not n or n in ("GND", "V3V3"):
                continue
            sides = {o.side for ref, o in m.parts.items() if ref != r and any(q.GetNetname() == n for q in o.fp.Pads())}
            if sides and p.side not in sides:
                forced.append(n)
        if forced:
            print(f"  {r:10s} on the {p.side}: {', '.join(forced)}")


if __name__ == "__main__":
    main()
