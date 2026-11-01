#!/usr/bin/env python3
"""Narrow power-net runs that serve only taps (owner, 2026-11-01: a rail's class width is for
its current path, not for every tap). A run is narrowed when every piece of it (split at
every junction) is a bridge whose far side holds tap pads only: TAPS lists them per net
(pull-ups, sense dividers, gate networks, feedback, ESD clamps) with the width each gets.
Runs shared with a current-carrying pad keep their width ("partly a tap, left").

    KICAD_PY scripts/thinTaps.py BOARD            # dry run: what it would narrow
    KICAD_PY scripts/thinTaps.py BOARD --apply    # narrows, refills zones, saves BOARD (a copy: KiCad may have the board open)

Afterwards: DRC as saved and refilled, cleanCopper --check, routeDefects (necks: a tap that
met the rail only by overlapping wide copper comes apart when narrowed; join it first)."""
import sys, collections, math, pcbnew
b = pcbnew.LoadBoard(sys.argv[1]); APPLY = "--apply" in sys.argv
TAPS = {
    "BAT": {("Q_PWRJ", "2"): 0.2, ("R_PWRJBE", "1"): 0.2, ("R_CS1", "1"): 0.2, ("R_REVG", "1"): 0.2},
    "BAT_N": {("R_CNS", "1"): 0.2, ("D_PKNEG", "1"): 0.3},
    "VBUS": {("U3", "1"): 0.2, ("R_PG", "2"): 0.2, ("R_VB1", "1"): 0.2, ("D_ESD", "5"): 0.3},
    "V3V3_USB": {("U10", "5"): 0.2, ("R_FBU1", "1"): 0.2, ("R_CHGOFF", "2"): 0.2},
    "V_BOOST": {("R_FB1", "1"): 0.2, ("C_FFB", "1"): 0.2, ("D_REVG", "1"): 0.2},
}
mm = lambda v: v / 1e6
TOL = 5000
out, partial, overlap = [], [], []
for net, taps in TAPS.items():
    pads = [((f.GetReference(), p.GetNumber()), p) for f in b.GetFootprints() for p in f.Pads() if p.GetNetname() == net]
    tr = [t for t in b.GetTracks() if t.GetNetname() == net]
    segs = [t for t in tr if t.GetClass() in ("PCB_TRACK", "PCB_ARC")]
    vias = [t for t in tr if t.GetClass() == "PCB_VIA"]
    nodes = {}                                    # key -> id
    def nid(key):
        return nodes.setdefault(key, len(nodes))
    def pt_node(x, y, layer):
        for (kind, *k), i in nodes.items():
            if kind == "pt" and k[2] == layer and abs(k[0] - x) < TOL and abs(k[1] - y) < TOL:
                return i
        return nid(("pt", x, y, layer))
    pad_n = {key: nid(("pad", key)) for key, _ in pads}
    via_n = [nid(("via", i)) for i in range(len(vias))]
    edges = []                                    # (a, c, seg)
    adj_extra = []
    ovl_links = []
    for s in segs:
        L = s.GetLayer(); A, B = s.GetStart(), s.GetEnd()
        # junction points along s: other segments' ends, vias, pads touching it. Inside s
        # they split it; at (or just past) an end they join that end.
        dx, dy = B.x - A.x, B.y - A.y; n2 = dx * dx + dy * dy or 1
        tt = lambda q: ((q.x - A.x) * dx + (q.y - A.y) * dy) / n2
        inner = []
        def touch(P, item_key=None):
            t = tt(P)
            if 0.02 < t < 0.98:
                inner.append((t, P.x, P.y, item_key))
            else:
                q = A if t <= 0.5 else B
                ovl_links.append(((P.x, P.y), (q.x, q.y), L, item_key))
        for o in segs:
            if o is s or o.GetLayer() != L: continue
            for P in (o.GetStart(), o.GetEnd()):
                if s.HitTest(P, max(TOL, o.GetWidth() // 2)): touch(P)
        for j, v in enumerate(vias):
            if s.HitTest(v.GetPosition(), v.GetWidth(pcbnew.F_Cu) // 2): touch(v.GetPosition(), ("via", j))
        for key, p in pads:
            if p.IsOnLayer(L) and s.HitTest(p.GetPosition(), 0): touch(p.GetPosition(), ("pad", key))
        ids = [pt_node(A.x, A.y, L)]
        for t, x, y, ik in sorted(inner, key=lambda r: r[0]):
            i = pt_node(x, y, L)
            if ik: adj_extra.append((i, via_n[ik[1]] if ik[0] == "via" else pad_n[ik[1]]))
            if ids[-1] != i: ids.append(i)
        e = pt_node(B.x, B.y, L)
        if ids[-1] != e: ids.append(e)
        for a, c in zip(ids, ids[1:]): edges.append((a, c, s))
    for (p1, p2, L, ik) in ovl_links:
        n2_ = pt_node(p2[0], p2[1], L)
        adj_extra.append((pt_node(p1[0], p1[1], L), n2_))
        if ik: adj_extra.append((n2_, via_n[ik[1]] if ik[0] == "via" else pad_n[ik[1]]))
    # points to pads and vias they sit in
    for (kind, *k), i in list(nodes.items()):
        if kind != "pt": continue
        P = pcbnew.VECTOR2I(int(k[0]), int(k[1]))
        for key, p in pads:
            if p.IsOnLayer(k[2]) and p.HitTest(P, TOL): adj_extra.append((i, pad_n[key]))
        for j, v in enumerate(vias):
            if math.hypot(v.GetPosition().x - k[0], v.GetPosition().y - k[1]) <= v.GetWidth(pcbnew.F_Cu) / 2: adj_extra.append((i, via_n[j]))
    def comp(skip):
        adj = collections.defaultdict(set)
        for e, (a, c, s) in enumerate(edges):
            if e != skip: adj[a].add(c); adj[c].add(a)
        for a, c in adj_extra: adj[a].add(c); adj[c].add(a)
        lab = {}
        for st in range(len(nodes)):
            if st in lab: continue
            lab[st] = st; stack = [st]
            while stack:
                u = stack.pop()
                for w in adj[u]:
                    if w not in lab: lab[w] = st; stack.append(w)
        return lab
    base = comp(None)
    isl = collections.defaultdict(list)
    for key, i in pad_n.items(): isl[base[i]].append(key)
    if len(isl) != 1:
        print(f"{net}: model splits it: {list(isl.values())}; skipped"); continue
    verdict = collections.defaultdict(list)       # seg -> per-piece width or None
    for e, (a, c, s) in enumerate(edges):
        lab = comp(e)
        if lab[a] == lab[c]: verdict[id(s)].append(None); continue
        w = None
        for side in (lab[a], lab[c]):
            group = [k for k, i in pad_n.items() if lab[i] == side]
            if group and all(g in taps for g in group):
                w = min(taps[g] for g in group); grp = group
        verdict[id(s)].append((w, tuple(sorted(grp))) if w else None)
    byid = {id(s): s for s in segs}
    for sid, vs in verdict.items():
        s = byid[sid]
        if all(vs) and len({v for v in vs}) == 1:
            w, grp = vs[0]
            if mm(s.GetWidth()) > w + 1e-6:
                out.append((net, grp, round(mm(s.GetWidth()), 3), w, mm(s.GetLength()), pcbnew.LayerName(s.GetLayer())))
                if APPLY: s.SetWidth(pcbnew.FromMM(w))
        elif any(vs) and mm(s.GetWidth()) > 0.3:
            partial.append((net, round(mm(s.GetWidth()), 3), round(mm(s.GetStart().x), 2), round(mm(s.GetStart().y), 2), round(mm(s.GetEnd().x), 2), round(mm(s.GetEnd().y), 2)))
    done = {g for o in out if o[0] == net for g in o[1]}
    for t in taps:
        if t not in done: print(f"{net}: {t[0]}.{t[1]} nothing to narrow (already thin, or its run is shared)")
agg = collections.defaultdict(lambda: [0, 0.0, set()])
for net, g, w0, w, L, lay in out:
    k = (net, g, w0, w); agg[k][0] += 1; agg[k][1] += L; agg[k][2].add(lay)
for (net, g, w0, w), (n, L, lays) in sorted(agg.items()):
    print(f"{net:9} {'+'.join(r + '.' + p for r, p in g):28} {w0} -> {w} mm: {n} segments, {L:.1f} mm on {sorted(lays)}")
for p in partial: print("partly a tap, left:", p)

if APPLY:
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(sys.argv[1], b)
    print("saved", sys.argv[1])
