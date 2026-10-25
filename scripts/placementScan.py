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
    the front, in a row between the joystick and the cell;
  - a small-signal node spread out (third pass): the boost's feedback divider and its
    feed-forward cap had drifted 2.3-3.0 mm apart, the cell-sense filter cap sat 3.5 mm
    from its resistor; the owner drew each node into one cluster (1.3-1.9 mm). Flagged:
    two-pad passives on one non-supply net of at most --node-pads pads whose pads link
    (minimum spanning tree) by a step over NODE_STEP_MM.
  - a via in an SMD pad (2026-10-25): a part moved onto a stitching via, or a route
    dropped through a pad; exposed thermal pads, whose vias are meant, excepted.
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
NODE_STEP_MM = 2.0
SUPPLIES = {n for _, _, _, _, _, nets in nb.NETCLASSES for n in nets} | {"V3V3"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board", nargs="?", default=str(nb.HERE / "board/wallet.kicad_pcb"))
    ap.add_argument("--y-min", type=float, default=64.0)
    ap.add_argument("--node-pads", type=int, default=4)
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

    print("vias in SMD pads (not thermal pads):")
    smd = [(ref, pd) for ref in region for pd in m.parts[ref].fp.Pads()
           if pd.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and pd.GetNumber() and len(list(m.parts[ref].fp.Pads())) < 40
           and not (pd.GetNetname() == "GND" and len(list(m.parts[ref].fp.Pads())) > 4
                    and min(pcbnew.ToMM(pd.GetSize(pd.GetLayer() if pd.GetLayer() >= 0 else pcbnew.F_Cu).x),
                            pcbnew.ToMM(pd.GetSize(pd.GetLayer() if pd.GetLayer() >= 0 else pcbnew.F_Cu).y)) > 1.0)]
    for v in m.board.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T:
            continue
        hit = [f"{ref}.{pd.GetNumber()}" for ref, pd in smd if pd.HitTest(v.GetPosition(), v.GetWidth(pcbnew.F_Cu) // 2)]
        if hit and not any(h.startswith(("BT_N", "BT_P")) for h in hit):
            print(f"  {v.GetNetname():10s} via at ({pcbnew.ToMM(v.GetPosition().x):.2f}, {pcbnew.ToMM(v.GetPosition().y):.2f}) in {', '.join(hit)}")

    print(f"small-signal nodes whose passives step more than {NODE_STEP_MM} mm apart:")
    by_net = {}
    for ref, o in m.parts.items():
        for pd in o.fp.Pads():
            by_net.setdefault(pd.GetNetname(), []).append((ref, len(list(o.fp.Pads())), pd))
    for n, pads in sorted(by_net.items()):
        if not n or n in SUPPLIES or n == "GND" or len(pads) > a.node_pads:
            continue
        mine = [(ref, pd) for ref, k, pd in pads if k == 2 and not ref.startswith(("TP", "H"))]
        if len(mine) < 2 or not any(ref in region for ref, _ in mine):
            continue
        pts = [(pcbnew.ToMM(pd.GetPosition().x), pcbnew.ToMM(pd.GetPosition().y)) for _, pd in mine]
        inside, steps = {0}, []
        while len(inside) < len(pts):
            d, j = min((math.dist(pts[i], pts[k]), k) for i in inside for k in range(len(pts)) if k not in inside)
            inside.add(j)
            steps.append(d)
        if max(steps) > NODE_STEP_MM:
            print(f"  {n:14s} {', '.join(ref for ref, _ in mine)}: steps {' '.join(f'{d:.1f}' for d in steps)} mm")


if __name__ == "__main__":
    main()
