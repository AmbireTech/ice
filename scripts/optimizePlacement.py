#!/usr/bin/env python3
"""Placement optimizer: fewer ratsnest crossings and shorter airwires, inside
this board's own legality rules.

    $KICAD_PY scripts/optimizePlacement.py [in.kicad_pcb] [out.kicad_pcb] [--radius 4] [--passes 8]

Greedy quench in the style of KiCadRoutingTools' place_optimize.py (whose
write-up found refinement of a structured seed worthwhile only when judged by a
trial route, and crossings the best cheap proxy), but legal by this board's
rules rather than a bounding rectangle: newBoard.py's courtyard GAP between
same-side parts, the board edge, the tail slots, the hole rings, the camera /
artwork keep-outs and the antenna end.

Cost = airwire length (per-net minimum spanning tree over pad centres) +
CROSSING_MM per pair of crossing airwires of different nets. GND is poured and
the 3.3 V rail lives on In2, so they score no airwires. Moves, per part, in
order of how much each part's nets cost: rotate (0/90/180/270), nudge on a
0.5 mm grid within --radius of where newBoard put it, and swap with another part
of the same footprint. A move is kept only if it lowers the cost.

Anchored parts (connectors, modules, the MCU, switches) never move: their
places come from mechanics. Everything is reported per pass.
"""
import math
import random
import sys
from pathlib import Path

import numpy as np
import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import newBoard as nb  # noqa: E402

CROSSING_MM = 6.0                    # one crossing costs as much as 6 mm of airwire
NO_AIRWIRE = {"GND", "V3V3"}         # poured / plane nets
LOW_WEIGHT = {"SYS": 0.4, "BAT": 0.4, "VBUS": 0.6}   # wide power runs, routed once
STEP = 0.5


def mm(v):
    return pcbnew.ToMM(v)


class Part:
    def __init__(self, fp):
        self.fp = fp
        self.ref = fp.GetReference()
        self.side = nb.BOT if fp.IsFlipped() else nb.TOP
        self.seed = (mm(fp.GetPosition().x), mm(fp.GetPosition().y))
        self.fpid = fp.GetFPIDAsString()

    def pos(self):
        return mm(self.fp.GetPosition().x), mm(self.fp.GetPosition().y)

    def set(self, x, y, rot):
        self.fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
        self.fp.SetOrientationDegrees(rot)

    def rot(self):
        return self.fp.GetOrientationDegrees()


def mst_edges(points):
    """Prim's MST over a handful of points: list of (i, j)."""
    n = len(points)
    if n < 2:
        return []
    pts = np.array(points)
    in_tree = np.zeros(n, bool)
    in_tree[0] = True
    best = np.hypot(*(pts - pts[0]).T)
    parent = np.zeros(n, int)
    edges = []
    for _ in range(n - 1):
        cand = np.where(in_tree, np.inf, best)
        j = int(np.argmin(cand))
        edges.append((int(parent[j]), j))
        in_tree[j] = True
        d = np.hypot(*(pts - pts[j]).T)
        upd = (~in_tree) & (d < best)
        best[upd] = d[upd]
        parent[upd] = j
    return edges


def crossings(a, b, a_net, b_net):
    """Proper crossings between segment sets a (N,4) and b (M,4), skipping
    same-net pairs and pairs that share an endpoint."""
    if len(a) == 0 or len(b) == 0:
        return 0
    p, r = a[:, None, 0:2], a[:, None, 2:4] - a[:, None, 0:2]
    q, s = b[None, :, 0:2], b[None, :, 2:4] - b[None, :, 0:2]
    rxs = r[..., 0] * s[..., 1] - r[..., 1] * s[..., 0]
    qp = q - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[..., 0] * s[..., 1] - qp[..., 1] * s[..., 0]) / rxs
        u = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / rxs
    hit = (np.abs(rxs) > 1e-9) & (t > 1e-6) & (t < 1 - 1e-6) & (u > 1e-6) & (u < 1 - 1e-6)
    hit &= a_net[:, None] != b_net[None, :]
    return int(np.count_nonzero(hit))


class Model:
    def __init__(self, board):
        self.board = board
        self.parts = {fp.GetReference(): Part(fp) for fp in board.GetFootprints()
                      if not fp.GetAttributes() & pcbnew.FP_BOARD_ONLY}
        self.net_pads = {}
        for ref, p in self.parts.items():
            for pad in p.fp.Pads():
                net = pad.GetNetname()
                if net and net not in NO_AIRWIRE and not net.startswith("NC"):
                    self.net_pads.setdefault(net, []).append((ref, pad))
        self.net_pads = {n: v for n, v in self.net_pads.items() if len({r for r, _ in v}) > 1}
        self.net_ids = {n: i for i, n in enumerate(sorted(self.net_pads))}
        self.part_nets = {}
        for n, lst in self.net_pads.items():
            for ref, _ in lst:
                self.part_nets.setdefault(ref, set()).add(n)
        self.wires = {n: self._wires(n) for n in self.net_pads}
        self.boxes = {ref: nb._box(p.fp) for ref, p in self.parts.items()}
        self._legal_cache()

    # ── geometry ──
    def _wires(self, net):
        pts = [(mm(pad.GetPosition().x), mm(pad.GetPosition().y)) for _, pad in self.net_pads[net]]
        return np.array([[*pts[i], *pts[j]] for i, j in mst_edges(pts)], float).reshape(-1, 4)

    def _legal_cache(self):
        b = nb
        self.forbidden = [(-999, -999, b.W + 999, 0.3), (-999, b.H - 0.3, b.W + 999, b.H + 999),
                          (-999, -999, 0.3, b.H + 999), (b.W - 0.3, -999, b.W + 999, b.H + 999)]
        for x0, x1 in ((b.TFT_TAIL[0] - b.SLOT_MARGIN, b.TFT_TAIL[0] + b.TFT_TAIL[1] + b.SLOT_MARGIN),
                       (b.CTP_TAIL[0] - b.SLOT_MARGIN, b.CTP_TAIL[0] + b.CTP_TAIL[1] + b.SLOT_MARGIN)):
            self.forbidden.append((x0 - 0.3, b.SLOT_Y0 - 0.3, x1 + 0.3, b.SLOT_Y1 + 0.3))
        self.forbidden += [(x - 2.2, y - 2.2, x + 2.2, y + 2.2) for x, y in b.HOLES]
        self.back_only = [box for _, box in b.footprint_keepouts(self.board)] + \
                         [(x - 3.0, y - 3.0, x + 3.0, y + 3.0) for x, y in b.HOLES]

    def legal(self, part):
        box = nb._box(part.fp, nb.GAP)
        tight = nb._box(part.fp, 0.1)
        extra = self.back_only if part.side == nb.BOT else []
        ant = [] if part.ref == "U11" else [nb.ANT_KEEPOUT]
        if any(nb._hits(tight, o) for o in self.forbidden + extra + ant):
            return False
        for ref, other in self.parts.items():
            if ref != part.ref and other.side == part.side and nb._hits(box, self.boxes[ref]):
                return False
        return True

    def moved(self, *refs):
        for r in refs:
            self.boxes[r] = nb._box(self.parts[r].fp)

    # ── cost ──
    def all_wires(self, skip=()):
        arrs, ids = [], []
        for n, w in self.wires.items():
            if n in skip or len(w) == 0:
                continue
            arrs.append(w)
            ids.append(np.full(len(w), self.net_ids[n]))
        if not arrs:
            return np.zeros((0, 4)), np.zeros(0, int)
        return np.vstack(arrs), np.concatenate(ids)

    def nets_cost(self, nets, others, other_ids):
        own = [self.wires[n] for n in nets if len(self.wires[n])]
        if not own:
            return 0.0
        own_ids = np.concatenate([np.full(len(self.wires[n]), self.net_ids[n]) for n in nets if len(self.wires[n])])
        own = np.vstack(own)
        length = sum(LOW_WEIGHT.get(n, 1.0) * float(np.hypot(self.wires[n][:, 2] - self.wires[n][:, 0],
                                                              self.wires[n][:, 3] - self.wires[n][:, 1]).sum())
                     for n in nets)
        x = crossings(own, others, own_ids, other_ids) + crossings(own, own, own_ids, own_ids) // 2
        return length + CROSSING_MM * x

    def total(self):
        allw, ids = self.all_wires()
        length = sum(LOW_WEIGHT.get(n, 1.0) * float(np.hypot(w[:, 2] - w[:, 0], w[:, 3] - w[:, 1]).sum())
                     for n, w in self.wires.items())
        x = crossings(allw, allw, ids, ids) // 2
        return length, x

    def try_state(self, refs, apply):
        """Cost of the nets touching `refs` before and after apply(); keeps the
        new state only if legal and cheaper. apply() returns an undo()."""
        nets = set().union(*(self.part_nets.get(r, set()) for r in refs))
        others, other_ids = self.all_wires(skip=nets)
        before = self.nets_cost(nets, others, other_ids)
        undo = apply()
        if not all(self.legal(self.parts[r]) for r in refs):
            undo()
            return False
        saved = {n: self.wires[n] for n in nets}
        for n in nets:
            self.wires[n] = self._wires(n)
        after = self.nets_cost(nets, others, other_ids)
        if after < before - 1e-6:
            self.moved(*refs)
            return True
        undo()
        self.wires.update(saved)
        return False


def optimize(board, radius=4.0, passes=8, seed=1, log=print):
    random.seed(seed)
    m = Model(board)
    movable = [r for r, p in m.parts.items()
               if r not in nb.ANCHORED and p.side == nb.BOT and r in m.part_nets]
    length, x = m.total()
    log(f"start: airwire {length:.0f} mm, {x} crossings, {len(movable)} movable parts")
    by_fp = {}
    for r in movable:
        by_fp.setdefault(m.parts[r].fpid, []).append(r)
    for it in range(passes):
        moved = 0
        # Worst parts first: the ones whose nets carry the most crossings and length.
        order = sorted(movable, key=lambda r: -len(m.part_nets.get(r, ())))
        random.shuffle(order)
        for ref in order:
            p = m.parts[ref]
            sx, sy = p.seed
            x0, y0, r0 = *p.pos(), p.rot()
            # Rotations in place first, then nudges with every rotation.
            cands = [(x0, y0, rot) for rot in (0, 90, 180, 270) if rot != r0 % 360]
            k = int(radius / STEP)
            for dx in range(-k, k + 1):
                for dy in range(-k, k + 1):
                    nx, ny = sx + dx * STEP, sy + dy * STEP
                    if (dx or dy) and math.hypot(nx - sx, ny - sy) <= radius and (nx, ny) != (x0, y0):
                        for rot in (0, 90, 180, 270):
                            cands.append((nx, ny, rot))
            random.shuffle(cands)
            best = None
            nets = m.part_nets.get(ref, set())
            others, other_ids = m.all_wires(skip=nets)
            base = m.nets_cost(nets, others, other_ids)
            saved = {n: m.wires[n] for n in nets}
            for cx, cy, cr in cands:
                p.set(cx, cy, cr)
                if not m.legal(p):
                    continue
                for n in nets:
                    m.wires[n] = m._wires(n)
                c = m.nets_cost(nets, others, other_ids)
                if c < base - 1e-6 and (best is None or c < best[0]):
                    best = (c, cx, cy, cr)
            if best:
                p.set(best[1], best[2], best[3])
                for n in nets:
                    m.wires[n] = m._wires(n)
                m.moved(ref)
                moved += 1
            else:
                p.set(x0, y0, r0)
                m.wires.update(saved)
        # Same-footprint swaps.
        swaps = 0
        for fpid, refs in by_fp.items():
            for i, a in enumerate(refs):
                for b in refs[i + 1:]:
                    pa, pb = m.parts[a], m.parts[b]
                    (ax, ay), (bx, by) = pa.pos(), pb.pos()
                    if math.hypot(ax - bx, ay - by) > 2 * radius + 4:
                        continue
                    ra, rb = pa.rot(), pb.rot()

                    def apply(pa=pa, pb=pb, ax=ax, ay=ay, bx=bx, by=by, ra=ra, rb=rb):
                        pa.set(bx, by, rb)
                        pb.set(ax, ay, ra)
                        return lambda: (pa.set(ax, ay, ra), pb.set(bx, by, rb))
                    if m.try_state([a, b], apply):
                        swaps += 1
        length, x = m.total()
        log(f"pass {it + 1}: {moved} parts moved/rotated, {swaps} swaps -> airwire {length:.0f} mm, {x} crossings")
        if moved == 0 and swaps == 0:
            break
    for p in m.parts.values():
        p.fp.Reference().SetPosition(p.fp.GetPosition())
    return m.total()


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="?", default=str(nb.syncBoard.BOARD))
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--radius", type=float, default=4.0, help="max mm a part moves from where newBoard put it")
    ap.add_argument("--passes", type=int, default=8)
    a = ap.parse_args()
    board = pcbnew.LoadBoard(a.src)
    optimize(board, a.radius, a.passes)
    pcbnew.SaveBoard(a.dst or a.src, board)
    print(f"wrote {a.dst or a.src}")


if __name__ == "__main__":
    main()
