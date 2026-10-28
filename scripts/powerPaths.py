#!/usr/bin/env python3
"""Current capacity of the board's power paths: the narrowest piece on each path and the
whole net's parallel capacity, against the current each path must carry.

    $KICAD_PY scripts/powerPaths.py [board.kicad_pcb] [--rise 10]

Copper is rated with IPC-2221 (I = k * dT^0.44 * A^0.725, A in mil^2; k = 0.048 outer,
0.024 inner): outer copper 1 oz (35 um), via barrels 18 um plating rated as inner copper
(conservative), JLC's 4-layer defaults. Each path is a source pad set and a sink pad set
on one net:
  narrowest  the widest single route from source to sink, rated by its weakest piece
             (track, via or thermal spoke): conservative, ignores parallel routes
  parallel   max-flow over every route with each piece at its own rating: optimistic,
             real sharing follows resistance, not rating
A path passes when the narrowest route carries its current. GND returns go through the
pour and planes; for the pads that sink a power path's return, the vias that tie them
(directly or within VIA_MM through the pour: the return spreads to every via
nearby) are counted and rated instead.
"""
import argparse
import math
from collections import defaultdict, deque

import pcbnew
from shapely.geometry import LineString, Point, Polygon

M = pcbnew.ToMM
OZ1 = 0.035
PLATING = 0.018
VIA_MM = 5.0

# (name, net, sources, sinks, amps, why); sources/sinks are REF.PAD, or REF for every pad of the net
PATHS = [
    ("pack + to boost inductor", "BAT", ["J_PACK.1"], ["L_BOOST.1"], 0.6,
     "full use 0.64 W / 85 % at a 1.8-2.0 V two-cell pack: half the one-cell ~1.2 A (docs/power-aaa.md)"),
    ("pack + to boost input cap", "BAT", ["J_PACK.1"], ["C_IN_BOOST.1"], 0.6, "the inductor's ripple and start-up"),
    ("charger to pack +", "BAT", ["U1.8"], ["J_PACK.1"], 0.25, "fast charge, 0.3 C (ISET 3.6 k || 1.8 k = 1.2 k)"),
    ("pack - to reverse FET", "BAT_N", ["J_PACK.4"], ["Q_REV.3"], 0.6, "the pack's whole current, charge and discharge"),
    ("boost switch node", "SW_BOOST", ["L_BOOST.2"], ["U2.1"], 0.65, "inductor RMS at 0.6 A in from two cells (2 A switch peak)"),
    ("boost output to mux", "V_BOOST", ["U2.7", "U2.8"], ["U10.6"], 0.2, "0.64 W at 3.3 V"),
    ("USB-C to buck", "VBUS", ["J1"], ["U3.1", "U3.4"], 0.5, "buck input: 1.5 W out / 90 % at 5 V = 0.33 A, margin"),
    ("buck switch node", "SW_USB", ["U3.3"], ["L_BUCK.1"], 0.6, "0.45 A out plus ripple"),
    ("buck output to mux", "V3V3_USB", ["L_BUCK.2"], ["U10.3", "U10.5"], 0.25, "system load on USB, 0.2 A"),
    ("USB-C to charger", "VBUS", ["J1"], ["U1.1"], 0.25, "charge current is the charger's input current (from VBUS since v4)"),
]
# pads whose return current must reach the GND planes, and how much
GND_SINKS = [("Q_REV.2", 0.6, "the pack's return"), ("C_IN_BOOST.2", 0.6, "boost input loop"),
             ("U2.2", 0.65, "boost power ground"), ("U2.9", 0.65, "boost exposed pad"),
             ("U1.4", 0.25, "charger ground"), ("U1.9", 0.25, "charger exposed pad (heat too)"),
             ("U3.2", 0.5, "buck ground"), ("C_USB3V3.2", 0.45, "buck output cap")]


def ipc(area_mm2, rise, outer=True):
    mil2 = area_mm2 / (0.0254 ** 2)
    return (0.048 if outer else 0.024) * rise ** 0.44 * mil2 ** 0.725


def items_of(board, net, rise, spoke_w):
    out = []   # (kind, label, layers, shape, amps)
    for t in board.GetTracks():
        if t.GetNetname() != net:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            p = (M(t.GetPosition().x), M(t.GetPosition().y))
            drill = M(t.GetDrillValue())
            out.append(("via", f"via {drill:.2f} mm at ({p[0]:.2f}, {p[1]:.2f})", {pcbnew.F_Cu, pcbnew.B_Cu},
                        Point(p).buffer(M(t.GetWidth(pcbnew.F_Cu)) / 2), ipc(math.pi * drill * PLATING, rise, False)))
        else:
            a = (M(t.GetStart().x), M(t.GetStart().y))
            b = (M(t.GetEnd().x), M(t.GetEnd().y))
            w = M(t.GetWidth())
            out.append(("track", f"{w:.2f} mm track at ({a[0]:.2f}, {a[1]:.2f})-({b[0]:.2f}, {b[1]:.2f})",
                        {t.GetLayer()}, LineString([a, b]).buffer(w / 2) if a != b else Point(a).buffer(w / 2),
                        ipc(w * OZ1, rise)))
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != net:
                continue
            layers = {L for L in (pcbnew.F_Cu, pcbnew.B_Cu) if p.IsOnLayer(L)}
            L = next(iter(layers))
            poly = p.GetEffectivePolygon(L)
            o = poly.Outline(0)
            shp = Polygon([(M(o.CPoint(i).x), M(o.CPoint(i).y)) for i in range(o.PointCount())])
            out.append(("pad", f"{fp.GetReference()}.{p.GetNumber()}", layers, shp, float("inf")))
    return out


def graph(items):
    adj = defaultdict(set)
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            if a[2] & b[2] and a[3].intersects(b[3]):
                adj[i].add(j)
                adj[j].add(i)
    return adj


def pads_named(items, names):
    out = set()
    for i, it in enumerate(items):
        if it[0] == "pad" and any(it[1] == n or it[1].split(".")[0] == n for n in names):
            out.add(i)
    return out


def widest(items, adj, src, dst):
    """Widest single route (max over routes of the min rating); returns (amps, weakest item)."""
    import heapq
    best = {s: (float("inf"), None) for s in src}
    heap = [(-float("inf"), s, None) for s in src]
    while heap:
        negcap, i, weak = heapq.heappop(heap)
        cap = -negcap
        if i in dst:
            return cap, weak
        if cap < best.get(i, (-1, None))[0]:
            continue
        for j in adj[i]:
            c = min(cap, items[j][4])
            w = weak if items[j][4] >= cap else j
            if c > best.get(j, (-1, None))[0]:
                best[j] = (c, w)
                heapq.heappush(heap, (-c, j, w))
    return 0.0, None


def maxflow(items, adj, src, dst):
    """Node-capacitated max-flow (Edmonds-Karp on split nodes)."""
    n = len(items)
    S, T = 2 * n, 2 * n + 1
    cap = defaultdict(float)
    nbr = defaultdict(set)

    def add(u, v, c):
        cap[(u, v)] += c
        nbr[u].add(v)
        nbr[v].add(u)
    big = 1e9
    for i, it in enumerate(items):
        add(2 * i, 2 * i + 1, min(it[4], big))
        for j in adj[i]:
            add(2 * i + 1, 2 * j, big)
    for s in src:
        add(S, 2 * s, big)
    for d in dst:
        add(2 * d + 1, T, big)
    flow = 0.0
    while True:
        par = {S: None}
        q = deque([S])
        while q and T not in par:
            u = q.popleft()
            for v in nbr[u]:
                if v not in par and cap[(u, v)] > 1e-9:
                    par[v] = u
                    q.append(v)
        if T not in par:
            return flow
        f, v = big, T
        while par[v] is not None:
            f = min(f, cap[(par[v], v)])
            v = par[v]
        v = T
        while par[v] is not None:
            cap[(par[v], v)] -= f
            cap[(v, par[v])] += f
            v = par[v]
        flow += f
        if flow > 1e6:
            return flow


def gnd_ties(board, rise):
    """GND pads that sink a power return: their vias, directly on the pad or by track, or
    within VIA_MM through the pour (thermal spokes rated as tracks)."""
    out = []
    vias = [v for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T and v.GetNetname() == "GND"]
    for name, amps, why in GND_SINKS:
        ref, num = name.split(".")
        fp = board.FindFootprintByReference(ref)
        pad = [p for p in fp.Pads() if p.GetNumber() == num][0]
        c = (M(pad.GetPosition().x), M(pad.GetPosition().y))
        near = [v for v in vias if math.dist(c, (M(v.GetPosition().x), M(v.GetPosition().y))) <= VIA_MM +
                M(max(pad.GetSize(pcbnew.F_Cu).x, pad.GetSize(pcbnew.F_Cu).y)) / 2]
        cap = sum(ipc(math.pi * M(v.GetDrillValue()) * PLATING, rise, False) for v in near)
        out.append((name, amps, why, len(near), cap))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board", nargs="?", default="board/wallet.kicad_pcb")
    ap.add_argument("--rise", type=float, default=10.0)
    a = ap.parse_args()
    board = pcbnew.LoadBoard(a.board)
    spoke = None
    for z in board.Zones():
        if z.GetNetname() == "GND" and not z.GetIsRuleArea() and z.IsOnLayer(pcbnew.B_Cu):
            spoke = (M(z.GetThermalReliefSpokeWidth()), z.GetPadConnection())
    print(f"IPC-2221, {a.rise:.0f} C rise; outer 1 oz, vias {PLATING * 1000:.0f} um plating (rated as inner)")
    print(f"GND pour pad connection: {spoke}\n")
    cache = {}
    worst = []
    for name, net, srcs, dsts, amps, why in PATHS:
        if net not in cache:
            its = items_of(board, net, a.rise, spoke[0] if spoke else 0.5)
            cache[net] = (its, graph(its))
        its, adj = cache[net]
        src, dst = pads_named(its, srcs), pads_named(its, dsts)
        if not src or not dst:
            print(f"{name}: pads not found ({srcs} -> {dsts})")
            continue
        cap, weak = widest(its, adj, src, dst)
        par = maxflow(its, adj, src, dst)
        ok = "ok  " if cap >= amps else ("PAR " if par >= amps else "LOW ")
        print(f"{ok}{name} ({net}): needs {amps:.2f} A ({why})")
        print(f"      narrowest route {cap:.2f} A, weakest: {its[weak][1] if weak is not None else '-'}; "
              f"all routes {par:.2f} A")
        worst.append(((cap if cap >= amps else par) / amps, name))   # parallel vias count once they pass
    print()
    for name, amps, why, n, cap in gnd_ties(board, a.rise):
        ok = "ok  " if cap >= amps else "LOW "
        print(f"{ok}{name} to the GND planes: needs {amps:.2f} A ({why}); {n} via(s) within reach, {cap:.2f} A")
    print("\nlowest margins:", ", ".join(f"{n} {r:.1f}x" for r, n in sorted(worst)[:4]))


if __name__ == "__main__":
    main()
