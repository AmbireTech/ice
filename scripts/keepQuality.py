#!/usr/bin/env python3
"""Keep only the autorouted nets that pass the owner's acceptance test; give back the rest.

    $KICAD_PY scripts/keepQuality.py BEFORE.kicad_pcb ROUTED.kicad_pcb OUT.kicad_pcb \
        [--gauge DIR/gauge.json] [--leave NET,NET] [--max-ratio 1.35] [--short-mm 5] [--short-ratio 1.6]

The owner's rule (CLAUDE.md): accept autorouted nets one at a time, near-shortest length,
no detour, hairpin or extra via; otherwise rip them and leave them to the owner. For every
net whose copper the router added or changed (BEFORE vs ROUTED):
  - its copper, against the minimum spanning tree of its pads, at most --max-ratio
    (--short-ratio for nets whose tree is under --short-mm);
  - its vias at most one more than its pads' faces need (pads on both faces: one);
  - no craft finding (detour, wiggle, hairpin) on it in the gauge's audit;
  - half of a differential pair: coupled to its partner over at least 70 % of its length;
  - none of the designer's copper gone unreplaced: the router's tidy pass drops track it
    finds dangling, and once took 3.4 mm of the owner's 2 mm BAT_N run that was still
    being drawn. Removals are kept only as part of a nudge or merge (added copper at
    least half the removed length) or as crumbs under --crumb-mm;
  - not listed in --leave.
A net that fails gets BEFORE's copper back exactly (the designer's, a nudged trace
included) and is listed as left to the owner. GND and V3V3 (pour and plane) keep the
router's stitching vias, but any of the designer's copper it removed is put back (its tidy
pass once took 2.2 mm of the owner's 0.5 mm V3V3 run), and every GND track it added goes,
with the vias it served, and so does a return via beside a signal via of a net given back: the owner's rule is pour plus stitching, ties only where listed
in stripGndTies.py (it once tied R_VSET's ground to a via and bridged two kept ties). OUT is the result; run KiCad's DRC on it: a kept net may need room a restored
nudge took back, which DRC reports.
"""
import argparse
import json
import math
import sys
from collections import defaultdict

import pcbnew

M = pcbnew.ToMM
PLANES = {"GND", "V3V3"}
RETURN_MM = 2.0     # the router's return vias sit this close to the signal via they serve


def copper(board):
    out = defaultdict(list)
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            key = ("via", round(M(t.GetPosition().x), 3), round(M(t.GetPosition().y), 3))
        else:
            key = ("track", t.GetLayer(), round(M(t.GetStart().x), 3), round(M(t.GetStart().y), 3),
                   round(M(t.GetEnd().x), 3), round(M(t.GetEnd().y), 3), round(M(t.GetWidth()), 3))
        out[t.GetNetname()].append((key, t))
    return out


def mst(pts):
    if len(pts) < 2:
        return 0.0
    inside, total = {0}, 0.0
    while len(inside) < len(pts):
        d, j = min((math.dist(pts[i], pts[k]), k) for i in inside for k in range(len(pts)) if k not in inside)
        inside.add(j)
        total += d
    return total


def islands(board, net):
    """The net's pads grouped by what BOARD's copper already joins: [[(x, y, back), ...], ...]."""
    pads = [p for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname() == net]
    items = [t for t in board.GetTracks() if t.GetNetname() == net]
    parent = list(range(len(pads) + len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(i, j):
        parent[find(i)] = find(j)
    ends = []
    for k, t in enumerate(items):
        pts = [t.GetPosition()] if t.Type() == pcbnew.PCB_VIA_T else [t.GetStart(), t.GetEnd()]
        ends.append((len(pads) + k, t, pts))
    for i, p in enumerate(pads):
        for k, t, pts in ends:
            if any(p.HitTest(q) for q in pts) or (t.Type() == pcbnew.PCB_VIA_T and t.HitTest(p.GetPosition())):
                join(i, k)
    for a in range(len(ends)):
        for b in range(a + 1, len(ends)):
            ka, ta, pa = ends[a]
            kb, tb, pb = ends[b]
            if any(ta.HitTest(q) for q in pb) or any(tb.HitTest(q) for q in pa):
                join(ka, kb)
    groups = defaultdict(list)
    for i, p in enumerate(pads):
        groups[find(i)].append((M(p.GetPosition().x), M(p.GetPosition().y), p.GetParentFootprint().IsFlipped()))
    return list(groups.values())


def gaps(groups):
    """Shortest links that join the islands (minimum spanning tree, nearest pads) and how
    many of them join pads on opposite faces (a via each): (mm, vias)."""
    if len(groups) < 2:
        return 0.0, 0
    inside, total, vias = {0}, 0.0, 0
    while len(inside) < len(groups):
        best = None
        for i in inside:
            for k in range(len(groups)):
                if k in inside:
                    continue
                for a in groups[i]:
                    for b in groups[k]:
                        d = math.dist(a[:2], b[:2])
                        if best is None or d < best[0]:
                            best = (d, k, a[2] != b[2])
        inside.add(best[1])
        total += best[0]
        vias += best[2]
    return total, vias


def coupled(mine, theirs, reach=0.35):
    """Share of mine's track length within reach (edge to edge) of theirs, same layer."""
    total = near = 0.0
    for t in mine:
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        a, b = (M(t.GetStart().x), M(t.GetStart().y)), (M(t.GetEnd().x), M(t.GetEnd().y))
        L = math.dist(a, b)
        total += L
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        for u in theirs:
            if u.Type() == pcbnew.PCB_VIA_T or u.GetLayer() != t.GetLayer():
                continue
            seg = pcbnew.SEG(u.GetStart(), u.GetEnd())
            d = M(seg.Distance(pcbnew.VECTOR2I(pcbnew.FromMM(mid[0]), pcbnew.FromMM(mid[1]))))
            if d - M(u.GetWidth()) / 2 - M(t.GetWidth()) / 2 <= reach:
                near += L
                break
    return near / total if total else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("routed")
    ap.add_argument("out")
    ap.add_argument("--gauge")
    ap.add_argument("--leave", default="")
    ap.add_argument("--max-ratio", type=float, default=1.35)
    ap.add_argument("--short-mm", type=float, default=5.0)
    ap.add_argument("--short-ratio", type=float, default=1.6)
    ap.add_argument("--crumb-mm", type=float, default=0.5)
    a = ap.parse_args()
    bboard = pcbnew.LoadBoard(a.before)
    before = copper(bboard)
    board = pcbnew.LoadBoard(a.routed)
    after = copper(board)
    craft = defaultdict(list)
    if a.gauge:
        for c in json.load(open(a.gauge)).get("craft", []):
            kind, net = c.split(":")[:2]
            craft[net].append(kind)
    leave = set(a.leave.split(",")) - {""}
    pads = defaultdict(list)
    for fp in board.GetFootprints():
        for p in fp.Pads():
            pads[p.GetNetname()].append((M(p.GetPosition().x), M(p.GetPosition().y), fp.IsFlipped()))
    changed = sorted(n for n in set(after) | set(before) if n and n not in PLANES
                     and sorted(k for k, _ in after.get(n, [])) != sorted(k for k, _ in before.get(n, [])))
    pairs = {}
    for n in changed:
        for p, q in (("_DP", "_DN"), ("_DN", "_DP"), ("_P", "_N"), ("_N", "_P")):
            if n.endswith(p) and n[:-len(p)] + q in after:
                pairs[n] = n[:-len(p)] + q
    rejected, kept = {}, []
    for n in changed:
        items = [t for _, t in after.get(n, [])]
        # judge what the router added against the gaps it closed: the islands the
        # designer's copper already joined, linked by the shortest pad-to-pad steps
        old = {k for k, _ in before.get(n, [])}
        added = [t for k, t in after.get(n, []) if k not in old]
        new = {k for k, _ in after.get(n, [])}
        removed = sum(M((t.GetEnd() - t.GetStart()).EuclideanNorm()) for k, t in before.get(n, [])
                      if k not in new and t.Type() != pcbnew.PCB_VIA_T)
        tree, need = gaps(islands(bboard, n))
        length = sum(M((t.GetEnd() - t.GetStart()).EuclideanNorm()) for t in added if t.Type() != pcbnew.PCB_VIA_T)
        vias = sum(1 for t in added if t.Type() == pcbnew.PCB_VIA_T)
        limit = a.short_ratio if tree < a.short_mm else a.max_ratio
        why = []
        if n in leave:
            why.append("left to the owner")
        if tree and length > tree * limit:
            why.append(f"{length / tree:.2f}x the gaps it closed")
        if vias > need + 1:
            why.append(f"{vias} new vias (needs {need})")
        if removed > a.crumb_mm and length < removed / 2:
            why.append(f"{removed:.1f} mm of the designer's copper removed")
        if craft.get(n):
            why.append("/".join(sorted(set(craft[n]))))
        if n in pairs:
            cp = coupled(items, [t for _, t in after.get(pairs[n], [])])
            if cp < 0.7:
                why.append(f"pair coupled {cp:.0%}")
        if why:
            rejected[n] = why
        else:
            kept.append((n, length, tree, vias))
    # a pair goes back or stays together
    for n, m in pairs.items():
        if n in rejected and m not in rejected and m in changed:
            rejected[m] = [f"its partner {n} goes back"]
            kept = [k for k in kept if k[0] != m]
    before_items = defaultdict(list)
    bsrc = pcbnew.LoadBoard(a.before)
    restored = defaultdict(float)
    for n in PLANES:
        new = {k for k, _ in after.get(n, [])}
        for k, t in before.get(n, []):
            if k not in new:
                before_items[n].append(t)
                restored[n] += M((t.GetEnd() - t.GetStart()).EuclideanNorm()) if t.Type() != pcbnew.PCB_VIA_T else 0
    for t in bsrc.GetTracks():
        if t.GetNetname() in rejected:
            before_items[t.GetNetname()].append(t)
    old = {k for k, _ in before.get("GND", [])}
    # the router's new GND tracks, and the new vias joined to them end to end
    fresh = [t for k, t in after.get("GND", []) if k not in old]
    ends = lambda t: [t.GetPosition()] if t.Type() == pcbnew.PCB_VIA_T else [t.GetStart(), t.GetEnd()]
    ties = [t for t in fresh if t.Type() != pcbnew.PCB_VIA_T]
    grow = list(ties)
    while grow:
        t = grow.pop()
        for u in fresh:
            if all(u is not v for v in ties) and (any(u.HitTest(q) for q in ends(t)) or any(t.HitTest(q) for q in ends(u))):
                ties.append(u)
                grow.append(u)
    # a return via the router set beside a signal via of a net that goes back has nothing
    # to return for any more: it goes too (the router places them within RETURN_MM)
    pos = lambda t: (M(t.GetPosition().x), M(t.GetPosition().y))
    sig_vias = lambda nets: [pos(t) for n in nets for k, t in after.get(n, [])
                             if t.Type() == pcbnew.PCB_VIA_T and k not in {k2 for k2, _ in before.get(n, [])}]
    dropped, kept_vias = sig_vias(rejected), sig_vias([k[0] for k in kept])
    returns = [t for t in fresh if t.Type() == pcbnew.PCB_VIA_T and all(t is not u for u in ties)
               and any(math.dist(pos(t), q) <= RETURN_MM for q in dropped)
               and not any(math.dist(pos(t), q) <= RETURN_MM for q in kept_vias)]
    gone = [t for n in rejected for _, t in after.get(n, [])] + ties + returns
    for t in gone:
        board.Remove(t)
    netinfo = {n: board.FindNet(n) for n in set(rejected) | PLANES}
    for n, ts in before_items.items():
        for t in ts:
            if t.Type() == pcbnew.PCB_VIA_T:
                c = pcbnew.PCB_VIA(board)
                c.SetPosition(t.GetPosition())
                c.SetViaType(t.GetViaType())
                c.SetWidth(t.GetWidth(pcbnew.F_Cu))
                c.SetDrill(t.GetDrillValue())
                c.SetLayerPair(t.TopLayer(), t.BottomLayer())
            else:
                c = pcbnew.PCB_TRACK(board)
                c.SetStart(t.GetStart())
                c.SetEnd(t.GetEnd())
                c.SetWidth(t.GetWidth())
                c.SetLayer(t.GetLayer())
            c.SetNet(netinfo[n])
            board.Add(c)
    board.Save(a.out)
    for n, length, tree, vias in kept:
        print(f"keep  {n:16s} {length:6.1f} mm added (gaps {tree:5.1f}), {vias} new via(s)")
    for n, why in sorted(rejected.items()):
        print(f"back  {n:16s} {'; '.join(why)}")
    for n, mm in restored.items():
        print(f"back  {n:16s} {mm:.1f} mm of the designer's copper the router removed")
    if returns:
        print(f"strip {len(returns)} return via(s) beside signal vias of nets given back")
    if ties:
        print(f"strip GND tracks the router added: {sum(1 for t in ties if t.Type() != pcbnew.PCB_VIA_T)} track(s), "
              f"{sum(1 for t in ties if t.Type() == pcbnew.PCB_VIA_T)} via(s)")
    print(f"{len(kept)} nets kept as routed, {len(rejected)} given back; wrote {a.out}")


if __name__ == "__main__":
    main()
