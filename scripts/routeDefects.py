#!/usr/bin/env python3
"""List real routing defects for the owner (or a fixer) to act on; changes nothing.

    $KICAD_PY scripts/routeDefects.py [board.kicad_pcb] [--since COMMIT] [--all]

A finding is listed for what the copper is, never for who drew it (owner, 2026-10-25:
"whether something stays or not shouldn't depend on whether I did it, but on whether
it's actually good"), and only when it is a real defect: the first version flagged
turns at pads, justified crossings and legal via spacing, and wasted the owner's time.
Each finding has a place (mm) and a reason, sorted north to south:
  drc         every KiCad DRC finding, all severities (silk, footprint-vs-library too)
  unconnected what KiCad's DRC reports unconnected
  neck        a power or rail track much narrower than its class where wide copper of the net
              meets both of its ends, so the current passes through it (2026-10-25: the cell's
              return ran 0.8 mm -> 0.15 mm -> 0.8 mm through the cell-sense resistor's pad).
              A thin tap into a pad that goes nowhere else is fine
  hop         a net drops through a via, runs at most --hop-mm on the other layer and comes
              back through a second via, while it continues on the first layer at both
              ends, AND the first layer offers a way between the two vias at most
              DETOUR_X times the run plus DETOUR_MM (an octilinear grid search keeping the
              board's clearances): two vias that buy nothing. A forced crossing is not listed
  acute       two segments meeting at under 80 degrees in open copper (not at a pad or via)
  stub        copper that does nothing: a segment that other copper of its net covers
              tracks (overlapping or doubled-back pieces; a piece inside a pad or via is a
              normal pad entry), a zero-length one, a dangling end.
              Safe to remove when DRC agrees (tidy, not for the owner)
  thin-fanout a 3.3 V plane fanout (pad to via) thinner than min(0.4 mm, the pad's narrow
              side): the owner's power-fanout rule; widenFanouts.py fixes it
  gnd-track   a GND track that ends in pour fill (touches no pad or via) where the pour
              would reach the pad without it (DRC decides), or runs longer than
              GND_RUN_MM (stripGndTies' tie length): ground as a trace. A short pad-to-via
              tie is good practice
  via-in-pad  a via under an SMD pad (solder wicks down it); chips' exposed pads excepted
With --all, also the noisy ones: via-near-pad (legal spacing under DRC's clearance),
jump (a track between a two-pad part's pads; DRC judges its clearance), craft
(detours and wiggles from hardware/carrier/.tools/audit_craft.py).
--since marks hops, GND tracks and vias in pads not on the board at COMMIT with "NEW".
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import newBoard as nb  # noqa: E402
import stripGndTies as sg  # noqa: E402

M = pcbnew.ToMM
PLANES = {"GND", "V3V3"}


def xy(p):
    return (round(M(p.x), 2), round(M(p.y), 2))


def hops(board, max_mm):
    out = []
    by_net = {}
    for t in board.GetTracks():
        by_net.setdefault(t.GetNetname(), []).append(t)
    for net, items in by_net.items():
        if not net or net in PLANES:
            continue
        vias = [t for t in items if t.Type() == pcbnew.PCB_VIA_T]
        tracks = [t for t in items if t.Type() != pcbnew.PCB_VIA_T]
        seen = set()
        for a in vias:
            for c in vias:
                if a is c:
                    continue
                for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                    seg = [t for t in tracks if t.GetLayer() == layer]
                    start = [t for t in seg if a.HitTest(t.GetStart()) or a.HitTest(t.GetEnd())]
                    if len(start) != 1:
                        continue
                    chain, t = [start[0]], start[0]
                    cur = t.GetEnd() if a.HitTest(t.GetStart()) else t.GetStart()
                    done = False
                    for _ in range(30):
                        if c.HitTest(cur):
                            done = True
                            break
                        nxt = [u for u in seg if all(u is not v for v in chain) and u.HitTest(cur)]
                        if len(nxt) != 1:
                            break
                        u = nxt[0]
                        chain.append(u)
                        cur = u.GetEnd() if math.dist(xy(u.GetStart()), xy(cur)) < 0.01 else u.GetStart()
                    if not done:
                        continue
                    length = sum(M(u.GetLength()) for u in chain)
                    if length > max_mm:
                        continue
                    other = pcbnew.B_Cu if layer == pcbnew.F_Cu else pcbnew.F_Cu
                    if not any(t.GetLayer() == other and (a.HitTest(t.GetStart()) or a.HitTest(t.GetEnd())) for t in tracks):
                        continue
                    if not any(t.GetLayer() == other and (c.HitTest(t.GetStart()) or c.HitTest(t.GetEnd())) for t in tracks):
                        continue
                    alt = stay_length(board, chain, other, (a, c))
                    if alt is None or alt > DETOUR_X * length + DETOUR_MM:
                        continue
                    pa, pc = xy(a.GetPosition()), xy(c.GetPosition())
                    # walked from either via: a via that branches on this layer only walks one way
                    if frozenset((pa, pc, layer)) in seen:
                        continue
                    seen.add(frozenset((pa, pc, layer)))
                    out.append(("hop", pa, f"{net}: two vias {pa} and {pc}, {length:.1f} mm on "
                                           f"{board.GetLayerName(layer)} between them; "
                                           f"{board.GetLayerName(other)} has a {alt:.1f} mm way"))
    return out


_COPPER = {}


def copper(board):
    """Per outer layer: [(netcode, shape, item)] of tracks, pads and vias; edges; keepouts."""
    if _COPPER:
        return _COPPER
    from shapely.geometry import LineString, Point, Polygon
    lay = {pcbnew.F_Cu: [], pcbnew.B_Cu: []}
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            shp = Point(xy(t.GetPosition())).buffer(M(t.GetWidth(pcbnew.F_Cu)) / 2)
            for L in lay:
                lay[L].append((t.GetNetCode(), shp, t))
        elif t.GetLayer() in lay:
            lay[t.GetLayer()].append((t.GetNetCode(), LineString([xy(t.GetStart()), xy(t.GetEnd())])
                                      .buffer(M(t.GetWidth()) / 2), t))
    for fp in board.GetFootprints():
        for p in fp.Pads():
            for L in lay:
                if not p.IsOnLayer(L):
                    continue
                poly = p.GetEffectivePolygon(L)
                if poly.OutlineCount():
                    o = poly.Outline(0)
                    pts = [xy(o.CPoint(i)) for i in range(o.PointCount())]
                    if len(pts) >= 3:
                        lay[L].append((p.GetNetCode(), Polygon(pts), p))
    edges = [LineString([xy(d.GetStart()), xy(d.GetEnd())]) for d in board.GetDrawings()
             if d.GetLayer() == pcbnew.Edge_Cuts and d.GetClass() == "PCB_SHAPE" and d.GetShape() == pcbnew.SHAPE_T_SEGMENT]
    keep = {L: [] for L in lay}
    for z in board.Zones():
        if z.GetIsRuleArea() and z.GetDoNotAllowTracks():
            o = z.Outline().Outline(0)
            poly = Polygon([xy(o.CPoint(i)) for i in range(o.PointCount())])
            for L in lay:
                if z.IsOnLayer(L):
                    keep[L].append(poly)
    _COPPER.update(lay=lay, edges=edges, keep=keep)
    return _COPPER


def fits(board, chain, layer, skip):
    """Would these tracks lie on `layer` with the board's clearances (vias in `skip` gone)?"""
    from shapely.geometry import LineString
    c = copper(board)
    ns = board.GetDesignSettings().m_NetSettings
    edge_clr = M(board.GetDesignSettings().m_CopperEdgeClearance)
    for t in chain:
        clr = M(ns.GetEffectiveNetClass(t.GetNetname()).GetClearance())
        line = LineString([xy(t.GetStart()), xy(t.GetEnd())])
        w = M(t.GetWidth()) / 2
        for net, shp, item in c["lay"][layer]:
            if net == t.GetNetCode() or any(item is k for k in skip):
                continue
            if shp.distance(line) < w + clr - 1e-4:
                return False
        if any(e.distance(line) < w + edge_clr for e in c["edges"]):
            return False
        if any(k.intersects(line) for k in c["keep"][layer]):
            return False
    return True


GND_RUN_MM = sg.TIE_MM
DETOUR_X, DETOUR_MM = 1.3, 1.0   # a hop is avoidable when its own layer offers a way this short
GRID = 0.05


def stay_length(board, chain, layer, skip):
    """Length (mm) of the shortest octilinear way between the chain's ends on `layer`, keeping the
    board's clearance to other nets, edges and keepouts (vias in `skip` gone); None if none
    within a window around the chain."""
    import heapq
    from shapely.geometry import Point, box
    from shapely.ops import unary_union
    from shapely.prepared import prep
    c = copper(board)
    t0 = chain[0]
    ns = board.GetDesignSettings().m_NetSettings
    clr = M(ns.GetEffectiveNetClass(t0.GetNetname()).GetClearance())
    w = min(M(t.GetWidth()) for t in chain) / 2
    pts = [xy(t.GetStart()) for t in chain] + [xy(t.GetEnd()) for t in chain]
    ends = [xy(v.GetPosition()) for v in skip]
    x0, y0 = min(p[0] for p in pts) - 2.5, min(p[1] for p in pts) - 2.5
    x1, y1 = max(p[0] for p in pts) + 2.5, max(p[1] for p in pts) + 2.5
    win = box(x0, y0, x1, y1)
    obst = [shp.buffer(w + clr - 1e-3) for net, shp, item in c["lay"][layer]
            if net != t0.GetNetCode() and not any(item is k for k in skip) and shp.intersects(win.buffer(1))]
    edge_clr = M(board.GetDesignSettings().m_CopperEdgeClearance)
    obst += [e.buffer(w + edge_clr) for e in c["edges"] if e.intersects(win.buffer(1))]
    obst += [k.buffer(w) for k in c["keep"][layer]]
    blocked = prep(unary_union(obst)) if obst else None
    nx, ny = int((x1 - x0) / GRID) + 1, int((y1 - y0) / GRID) + 1
    cell = lambda p: (int(round((p[0] - x0) / GRID)), int(round((p[1] - y0) / GRID)))
    free = {}

    def ok(i, j):
        if (i, j) not in free:
            free[(i, j)] = 0 <= i < nx and 0 <= j < ny and not (
                blocked and blocked.contains(Point(x0 + i * GRID, y0 + j * GRID)))
        return free[(i, j)]
    s, g = cell(ends[0]), cell(ends[1])
    free[s] = free[g] = True
    dist, todo = {s: 0.0}, [(0.0, s)]
    steps = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1), (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]
    while todo:
        d, (i, j) = heapq.heappop(todo)
        if (i, j) == g:
            return d * GRID
        if d > dist.get((i, j), 1e18):
            continue
        for di, dj, cost in steps:
            n = (i + di, j + dj)
            if ok(*n) and d + cost < dist.get(n, 1e18):
                dist[n] = d + cost
                heapq.heappush(todo, (d + cost, n))
    return None


def gnd_tracks(board):
    out = []
    pads = [(f"{fp.GetReference()}.{p.GetNumber()}", p) for fp in board.GetFootprints() for p in fp.Pads()
            if p.GetNetname() == "GND"]
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() == "GND"]
    tracks = [t for t in board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T and t.GetNetname() == "GND"]
    for t in tracks:
        ends = []
        for p in (t.GetStart(), t.GetEnd()):
            hit = [r for r, pd in pads if pd.HitTest(p)]
            if hit:
                ends.append(hit[0])
            elif any(v.HitTest(p) for v in vias):
                ends.append("via")
            elif any(u is not t and u.HitTest(p) for u in tracks):
                ends.append("track")
            else:
                ends.append("nothing")
        if "nothing" in ends:
            out.append(("gnd-track", xy(t.GetStart()), f"GND into pour: {M(t.GetLength()):.2f} mm from "
                                                       f"{xy(t.GetStart())} to {xy(t.GetEnd())} ({ends[0]} - {ends[1]})",
                        t.m_Uuid.AsString()))
    # ground run as a trace: a connected piece of GND track longer than GND_RUN_MM
    for names, mm, g in sg.ties(board):
        if mm > GND_RUN_MM:
            out.append(("gnd-track", xy(g[0].GetPosition() if g[0].Type() == pcbnew.PCB_VIA_T else g[0].GetStart()),
                        f"GND run as a trace: {mm:.1f} mm of track ({', '.join(names)})"))
    return out


def needed(board_path, uid):
    """Does DRC report more unconnected items once this track is gone? (a child process
    loads the board: one LoadBoard per process)"""
    tmp = tempfile.mkdtemp()
    src = Path(board_path).parent
    for f in ("wallet.kicad_pro", "wallet.kicad_dru"):
        if (src / f).exists():
            (Path(tmp) / f).write_text((src / f).read_text())
    code = ("import sys, pcbnew; b = pcbnew.LoadBoard(sys.argv[1]); "
            "t = [t for t in b.GetTracks() if t.m_Uuid.AsString() == sys.argv[3]]; "
            "b.Remove(t[0]); pcbnew.SaveBoard(sys.argv[2], b)")
    out = os.path.join(tmp, "wallet.kicad_pcb")
    subprocess.run([sys.executable, "-c", code, board_path, out, uid], capture_output=True)

    def unconnected(path):
        j = os.path.join(tmp, "u.json")
        subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--format", "json", "-o", j, path],
                       capture_output=True)
        return len(json.load(open(j))["unconnected_items"])
    return unconnected(out) > unconnected(board_path)


def vias_in_pads(board):
    out = []
    smd = [(f"{fp.GetReference()}.{p.GetNumber()}", p, fp) for fp in board.GetFootprints() for p in fp.Pads()
           if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and p.GetNumber()
           and fp.GetReference() not in ("BT_N", "BT_P")]
    for v in board.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T:
            continue
        for name, p, fp in smd:
            big = p.GetNetname() == "GND" and len(list(fp.Pads())) > 4 and \
                min(M(p.GetSize(pcbnew.F_Cu).x), M(p.GetSize(pcbnew.F_Cu).y)) > 1.0
            if not big and p.HitTest(v.GetPosition(), v.GetWidth(pcbnew.F_Cu) // 2):
                out.append(("via-in-pad", xy(v.GetPosition()), f"{v.GetNetname()} via in {name} [{p.GetNetname()}]"))
    return out


def pad_shapes(board):
    from shapely.geometry import Polygon
    out = []
    for fp in board.GetFootprints():
        if fp.GetReference() in ("BT_N", "BT_P"):
            continue
        for p in fp.Pads():
            if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD or not p.GetNumber():
                continue
            layer = pcbnew.B_Cu if fp.IsFlipped() else pcbnew.F_Cu
            poly = p.GetEffectivePolygon(layer)
            if poly.OutlineCount() == 0:
                continue
            o = poly.Outline(0)
            pts = [(M(o.CPoint(i).x), M(o.CPoint(i).y)) for i in range(o.PointCount())]
            if len(pts) >= 3:
                out.append((f"{fp.GetReference()}.{p.GetNumber()}", p.GetNetname(), layer, Polygon(pts), fp))
    return out


def vias_near_pads(board, shapes, near):
    from shapely.geometry import Point
    out = []
    for v in board.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T:
            continue
        c = Point(M(v.GetPosition().x), M(v.GetPosition().y)).buffer(M(v.GetWidth(pcbnew.F_Cu)) / 2)
        for name, net, layer, poly, fp in shapes:
            d = poly.distance(c)
            if 0 < d < near:
                out.append(("via-near-pad", xy(v.GetPosition()),
                            f"{v.GetNetname()} via at {xy(v.GetPosition())} {d:.2f} mm from {name} [{net}]"))
    return out


def jumps(board, shapes):
    from shapely.geometry import LineString, MultiPoint
    out = []
    two = {}
    for name, net, layer, poly, fp in shapes:
        if len(list(fp.Pads())) == 2:
            two.setdefault(fp.GetReference(), []).append((net, layer, poly))
    gaps = []
    for ref, pads in two.items():
        if len(pads) != 2:
            continue
        (n1, l1, a), (n2, l2, b) = pads
        gap = MultiPoint(list(a.exterior.coords) + list(b.exterior.coords)).convex_hull.difference(a).difference(b)
        gaps.append((ref, {n1, n2}, l1, gap))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        line = LineString([(M(t.GetStart().x), M(t.GetStart().y)), (M(t.GetEnd().x), M(t.GetEnd().y))])
        for ref, nets, layer, gap in gaps:
            if t.GetLayer() == layer and t.GetNetname() not in nets and line.intersects(gap) \
                    and line.intersection(gap).length > 0.2:
                out.append(("jump", xy(t.GetStart()), f"{t.GetNetname()} passes between the pads of {ref}"))
    return out


def acute_turns(board):
    out = []
    ends = {}
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        for p, q in ((t.GetStart(), t.GetEnd()), (t.GetEnd(), t.GetStart())):
            ends.setdefault((t.GetNetname(), t.GetLayer(), xy(p)), []).append((xy(p), xy(q), t))
    vias = [v for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T]
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    for (net, layer, at), segs in ends.items():
        if len(segs) != 2:
            continue
        P = pcbnew.VECTOR2I(pcbnew.FromMM(at[0]), pcbnew.FromMM(at[1]))
        if any(v.HitTest(P) for v in vias) or any(p.IsOnLayer(layer) and p.HitTest(P) for p in pads):
            continue
        (p, q1, a), (_, q2, b) = segs
        if M(a.GetLength()) < 0.05 or M(b.GetLength()) < 0.05:
            continue
        v1 = (q1[0] - p[0], q1[1] - p[1])
        v2 = (q2[0] - p[0], q2[1] - p[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        if 2.0 < angle < 80.0:
            out.append(("acute", at, f"{net} turns back at {angle:.0f} deg on {board.GetLayerName(layer)}"))
    return out


def stubs(board):
    """Segments that carry nothing: covered by other copper of the net on their layer, or zero length."""
    from shapely.ops import unary_union
    from shapely.geometry import LineString
    out = []
    c = copper(board)
    for L, items in c["lay"].items():
        by = {}
        for net, shp, item in items:
            by.setdefault(net, []).append((shp, item))
        for net, group in by.items():
            for shp, item in group:
                if item.Type() in (pcbnew.PCB_VIA_T, pcbnew.PCB_PAD_T):
                    continue
                if item.GetLength() == 0:
                    out.append(("stub", xy(item.GetStart()), f"{item.GetNetname()}: zero-length track"))
                    continue
                # under other tracks only: a pad-entry piece inside its pad or via is normal
                others = [s2 for s2, i2 in group if i2 is not item and i2.Type() == pcbnew.PCB_TRACE_T]
                core = LineString([xy(item.GetStart()), xy(item.GetEnd())]).buffer(M(item.GetWidth()) / 2 - 0.005)
                if others and core.difference(unary_union(others)).area < 1e-4:
                    out.append(("stub", xy(item.GetStart()), f"{item.GetNetname()}: {M(item.GetLength()):.2f} mm on "
                                                            f"{board.GetLayerName(L)} lies under other copper of the net"))
    return out


def thin_fanouts(board):
    out = []
    vias = [v for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T and v.GetNetname() == "V3V3"]
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != "V3V3" or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            L = pcbnew.B_Cu if fp.IsFlipped() else pcbnew.F_Cu
            want = min(0.4, M(min(p.GetSize(L).x, p.GetSize(L).y)))
            for t in board.GetTracks():
                if t.Type() == pcbnew.PCB_VIA_T or t.GetNetname() != "V3V3" or t.GetLayer() != L:
                    continue
                a, b = t.GetStart(), t.GetEnd()
                if (p.HitTest(a) and any(v.HitTest(b) for v in vias)) or (p.HitTest(b) and any(v.HitTest(a) for v in vias)):
                    if M(t.GetWidth()) < want - 0.01:
                        out.append(("thin-fanout", xy(a), f"V3V3 {fp.GetReference()}.{p.GetNumber()}: "
                                                          f"{M(t.GetWidth()):.2f} mm, the pad takes {want:.2f}"))
    return out


def necks(board):
    out = []
    tracks = [t for t in board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T]
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]

    ns = board.GetDesignSettings().m_NetSettings
    widths = {}

    def cls_w(t):
        net = t.GetNetname()
        if net not in widths:
            widths[net] = M(ns.GetEffectiveNetClass(net).GetTrackWidth())
        return widths[net]

    def wide_at(t, p):
        """Wide copper of t's net meets point p: a wide track ending there, or in the pad or via there."""
        net, w = t.GetNetCode(), cls_w(t)
        hubs = [h for h in pads + vias if h.GetNetCode() == net and h.HitTest(p)]
        for u in tracks:
            if u is t or u.GetNetCode() != net or M(u.GetWidth()) < 0.9 * w:
                continue
            for e in (u.GetStart(), u.GetEnd()):
                if (u.GetLayer() == t.GetLayer() and math.dist(xy(e), xy(p)) < 0.05) or \
                        any(h.HitTest(e) for h in hubs):
                    return True
        return False
    def buried(t):
        """A thin piece lying under the net's wide copper carries nothing of its own."""
        from shapely.geometry import LineString, Polygon
        from shapely.ops import unary_union
        line = lambda u: LineString([xy(u.GetStart()), xy(u.GetEnd())]).buffer(M(u.GetWidth()) / 2)
        wide = [line(u) for u in tracks if u is not t and u.GetNetCode() == t.GetNetCode()
                and u.GetLayer() == t.GetLayer() and M(u.GetWidth()) >= 0.9 * cls_w(t)]
        for p in pads:
            if p.GetNetCode() == t.GetNetCode() and p.IsOnLayer(t.GetLayer()):
                poly = p.GetEffectivePolygon(t.GetLayer())
                if poly.OutlineCount():
                    o = poly.Outline(0)
                    pts = [xy(o.CPoint(i)) for i in range(o.PointCount())]
                    if len(pts) >= 3:
                        wide.append(Polygon(pts))
        mine = line(t)
        return bool(wide) and mine.difference(unary_union(wide)).area < 0.1 * mine.area
    for t in tracks:
        w = cls_w(t)
        if w < 0.3 or M(t.GetWidth()) >= 0.6 * w:
            continue
        if wide_at(t, t.GetStart()) and wide_at(t, t.GetEnd()) and not buried(t):
            out.append(("neck", xy(t.GetStart()), f"{t.GetNetname()}: {M(t.GetWidth()):.2f} mm from {xy(t.GetStart())} "
                                                  f"to {xy(t.GetEnd())} between {w:.2f} mm copper"))
    return out


def drc(board_path):
    out = []
    tmp = os.path.join(tempfile.gettempdir(), "routeDefects.drc.json")
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--severity-all", "--format", "json",
                    "-o", tmp, board_path], capture_output=True)
    d = json.load(open(tmp))
    for v in d["violations"]:
        i = v["items"][0]
        kind = "stub" if v["type"] == "track_dangling" else "drc"
        out.append((kind, (round(i["pos"]["x"], 2), round(i["pos"]["y"], 2)),
                    f"{v['type']}: " + " <-> ".join(j["description"][:60] for j in v["items"])))
    for u in d["unconnected_items"]:
        a = u["items"][0]
        out.append(("unconnected", (round(a["pos"]["x"], 2), round(a["pos"]["y"], 2)),
                    " <-> ".join(i["description"][:50] for i in u["items"])))
    return out


def craft(board_path):
    tool = HERE.parent.parent / "carrier/.tools/audit_craft.py"
    if not tool.exists():
        return []
    ledger = os.path.join(tempfile.gettempdir(), "routeDefects.ledger.json")
    r = subprocess.run([sys.executable, str(tool), board_path, "--ledger", ledger], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("NEW: "):
            kind, net, rest = line[5:].split(":", 2)
            at = rest.split("@(")[1].rstrip(")").split(",")
            out.append(("craft", (float(at[0]), float(at[1])), f"{kind} on {net} ({rest.split('@')[0]})"))
    return out


def old_copper(commit):
    """Via positions and GND track ends on the board at COMMIT (a second LoadBoard in
    this process would break the first, so a child process reads it)."""
    tmp = tempfile.mkdtemp()
    src = subprocess.run(["git", "show", f"{commit}:board/wallet.kicad_pcb"], cwd=HERE.parent,
                         capture_output=True, text=True, check=True).stdout
    open(os.path.join(tmp, "wallet.kicad_pcb"), "w").write(src)
    code = ("import sys, json, pcbnew; M = pcbnew.ToMM; b = pcbnew.LoadBoard(sys.argv[1]); "
            "r = lambda p: (round(M(p.x), 2), round(M(p.y), 2)); "
            "print(json.dumps([['v', *r(t.GetPosition())] if t.Type() == pcbnew.PCB_VIA_T else "
            "['t', *r(t.GetStart()), *r(t.GetEnd())] for t in b.GetTracks()]))")
    out = subprocess.run([sys.executable, "-c", code, os.path.join(tmp, "wallet.kicad_pcb")],
                         capture_output=True, text=True).stdout.strip().splitlines()[-1]
    items = json.loads(out)
    return ({tuple(i[1:]) for i in items if i[0] == "v"}, {tuple(i[1:]) for i in items if i[0] == "t"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board", nargs="?", default=str(nb.HERE / "board/wallet.kicad_pcb"))
    ap.add_argument("--hop-mm", type=float, default=6.0)
    ap.add_argument("--since")
    ap.add_argument("--near-mm", type=float, default=0.25)
    ap.add_argument("--all", action="store_true", help="also the noisy checks: via-near-pad, jump, craft")
    a = ap.parse_args()
    old = old_copper(a.since) if a.since else None
    board = pcbnew.LoadBoard(a.board)
    shapes = pad_shapes(board)
    # a track into the fill that is a pad's only link to ground is not slop
    found = [g[:3] for g in gnd_tracks(board) if len(g) < 4 or not needed(a.board, g[3])]
    found += hops(board, a.hop_mm) + vias_in_pads(board) + acute_turns(board)
    found += stubs(board) + thin_fanouts(board) + necks(board) + drc(a.board)
    if a.all:
        found += vias_near_pads(board, shapes, a.near_mm) + jumps(board, shapes) + craft(a.board)

    def new(kind, what):
        if old is None or kind not in ("hop", "gnd-track", "via-in-pad", "via-near-pad"):
            return ""
        pts = [tuple(float(v) for v in m.split(", ")) for m in
               __import__("re").findall(r"\(([-\d.]+, [-\d.]+)\)", what)]
        if kind == "gnd-track":
            seg = (*pts[0], *pts[1]) if len(pts) >= 2 else None
            known = seg in old[1] or (seg and (*seg[2:], *seg[:2]) in old[1])
        else:
            known = all(p in old[0] for p in pts[:2] if p)
        return "" if known else "NEW "
    for kind, (x, y), what in sorted(found, key=lambda f: (f[1][1], f[1][0])):
        print(f"{kind:11s} ({x:5.1f}, {y:5.1f})  {new(kind, what)}{what}")
    counts = {}
    for kind, _, _ in found:
        counts[kind] = counts.get(kind, 0) + 1
    print("total:", ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
