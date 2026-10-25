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
DECAP_MM = 3.0                       # each mm between a decoupling cap and its rail's nearest IC pin
CLUSTER_MM = 3.0                     # each mm a small-signal node's passives step apart beyond CLUSTER_STEP
CLUSTER_STEP = 2.0                   # (the owner's third pass: a feedback divider and its
                                     # feed-forward cap drawn into one cluster, 1.3-1.9 mm)
# an airwire leaving a chip's pin backwards (into or around the chip) costs this
# many mm; sideways half of it. The owner's lesson (2026-10-17): the SE050 turned so
# its I2C pins face the MCU -- airwire length and crossings cannot see which way a
# pin escapes, so a chip whose pins point away from their partners looks just as good.
FACING_MM = 4.0
TOUCH = 0.0                          # courtyards may touch (the owner packs them at a median 0.01 mm)
# Placed for a reason the ratsnest cannot see: the radio light sits under the
# south-west hole by the owner's call, its resistor beside it.
PINNED = {"D_RADIO", "R_RLED"}
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
    def __init__(self, board, locked=()):
        self.board = board
        self.locked = set(locked)
        self.parked = set()      # lifted off the board while a block is rebuilt (place_block)
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
        self._copper_cache()
        self._decap_cache()
        self._cluster_cache()

    # ── existing copper: a part may move only where its pads clear other nets' tracks/vias ──
    def _copper_cache(self):
        from shapely.geometry import LineString, Point
        from shapely.strtree import STRtree
        geoms, nets, layers = [], [], []
        for t in self.board.GetTracks():
            n = t.GetNetname()
            if t.GetClass() == "PCB_VIA":
                p = t.GetPosition()
                geoms.append(Point(mm(p.x), mm(p.y)).buffer(mm(t.GetWidth(pcbnew.F_Cu)) / 2))
                layers.append(None)
            else:
                a, b = t.GetStart(), t.GetEnd()
                geoms.append(LineString([(mm(a.x), mm(a.y)), (mm(b.x), mm(b.y))]).buffer(mm(t.GetWidth()) / 2))
                layers.append(t.GetLayer())
            nets.append(n)
        self.cu = (STRtree(geoms), geoms, nets, layers) if geoms else None

    @staticmethod
    def pad_shapes(fp):
        from shapely.geometry import Polygon
        out = []
        for pad in fp.Pads():
            layer = pcbnew.B_Cu if pad.IsOnLayer(pcbnew.B_Cu) else pcbnew.F_Cu
            poly = pad.GetEffectivePolygon(layer)
            if poly.OutlineCount() == 0:
                continue
            o = poly.Outline(0)
            pts = [(mm(o.CPoint(i).x), mm(o.CPoint(i).y)) for i in range(o.PointCount())]
            if len(pts) >= 3:
                out.append((pad, Polygon(pts)))
        return out

    def pads_ok(self, part, clearance=0.16):
        """Pad-to-pad clearance against the neighbours: courtyards may touch, but a land
        whose courtyard does not enclose all its pads (connector tabs) must still be
        cleared."""
        box = nb._box(part.fp, 1.0)
        mine = self.pad_shapes(part.fp)
        for ref, other in self.parts.items():
            if ref == part.ref or other.side != part.side or not nb._hits(box, self.boxes[ref]):
                continue
            for pa, sa in mine:
                for pb, sb in self.pad_shapes(other.fp):
                    if pa.GetNetname() and pa.GetNetname() == pb.GetNetname():
                        continue
                    if sa.distance(sb) < clearance:
                        return False
        return True

    def copper_ok(self, part, clearance=0.16):
        if self.cu is None:
            return True
        from shapely.geometry import Polygon
        tree, geoms, nets, layers = self.cu
        for pad in part.fp.Pads():
            poly = pad.GetEffectivePolygon(pad.GetLayer() if pad.GetLayer() >= 0 else pcbnew.B_Cu)
            if poly.OutlineCount() == 0:
                continue
            o = poly.Outline(0)
            pts = [(mm(o.CPoint(i).x), mm(o.CPoint(i).y)) for i in range(o.PointCount())]
            if len(pts) < 3:
                continue
            shape = Polygon(pts)
            net = pad.GetNetname()
            for idx in tree.query(shape.buffer(clearance)):
                idx = int(idx)
                if nets[idx] == net and net:
                    continue
                if layers[idx] is not None and not pad.IsOnLayer(layers[idx]):
                    continue
                if geoms[idx].distance(shape) < clearance:
                    return False
        return True

    # ── decoupling: a 2-pad cap with one pad on GND belongs at its rail's IC pin ──
    def _decap_cache(self):
        self.decaps = {}
        for ref, p in self.parts.items():
            pads = list(p.fp.Pads())
            if not ref.startswith("C") or len(pads) != 2:
                continue
            nets = [pd.GetNetname() for pd in pads]
            if "GND" not in nets:
                continue
            rail = [n for n in nets if n != "GND"]
            if not rail or not rail[0]:
                continue
            # a decap belongs at a chip's rail pin, never a connector's: the owner moved the
            # USB buck's input cap from USB-C's VBUS pin to the buck's VIN pin (2026-10-23)
            pins = [(r2, pd) for r2, q in self.parts.items() if r2 != ref and len(list(q.fp.Pads())) > 3
                    and not r2.startswith("J") for pd in q.fp.Pads() if pd.GetNetname() == rail[0]]
            if pins:
                self.decaps[ref] = (rail[0], pins)

    def _cluster_cache(self):
        """Small-signal nodes: a non-supply net of at most 4 pads with two or more two-pad
        passives on it (placementScan.py's spread-node finding, as a cost)."""
        supplies = {n for *_, nets in nb.NETCLASSES for n in nets} | NO_AIRWIRE
        self.clusters = {}
        for n, lst in self.net_pads.items():
            if n in supplies or len(lst) > 4:
                continue
            two = [(r2, pd) for r2, pd in lst if len(list(self.parts[r2].fp.Pads())) == 2
                   and not r2.startswith(("TP", "H"))]
            if len(two) >= 2:
                self.clusters[n] = two
        self.part_clusters = {}
        for n, two in self.clusters.items():
            for r2, _ in two:
                self.part_clusters.setdefault(r2, set()).add(n)

    def cluster_cost(self, ref):
        c = 0.0
        for n in self.part_clusters.get(ref, ()):
            pts = [(mm(pd.GetPosition().x), mm(pd.GetPosition().y)) for _, pd in self.clusters[n]]
            for i, j in mst_edges(pts):
                c += max(0.0, math.dist(pts[i], pts[j]) - CLUSTER_STEP) * CLUSTER_MM
        return c

    def decap_cost(self, ref):
        if ref not in self.decaps:
            return 0.0
        rail, pins = self.decaps[ref]
        own = next(pd for pd in self.parts[ref].fp.Pads() if pd.GetNetname() == rail)
        ox, oy = mm(own.GetPosition().x), mm(own.GetPosition().y)
        d = min(math.hypot(ox - mm(pd.GetPosition().x), oy - mm(pd.GetPosition().y)) for _, pd in pins)
        return DECAP_MM * d

    # ── geometry ──
    def _wires(self, net):
        pts = [(mm(pad.GetPosition().x), mm(pad.GetPosition().y)) for ref, pad in self.net_pads[net]
               if ref not in self.parked]
        return np.array([[*pts[i], *pts[j]] for i, j in mst_edges(pts)], float).reshape(-1, 4)

    def _legal_cache(self):
        b = nb
        self.forbidden = [(-999, -999, b.W + 999, 0.3), (-999, b.H - 0.3, b.W + 999, b.H + 999),
                          (-999, -999, 0.3, b.H + 999), (b.W - 0.3, -999, b.W + 999, b.H + 999)]
        for x0, x1 in ((b.TFT_TAIL[0] - b.SLOT_MARGIN, b.TFT_TAIL[0] + b.TFT_TAIL[1] + b.SLOT_MARGIN),
                       (b.CTP_TAIL[0] - b.SLOT_MARGIN, b.CTP_TAIL[0] + b.CTP_TAIL[1] + b.SLOT_MARGIN)):
            self.forbidden.append((x0 - 0.3, b.SLOT_Y0 - 0.3, x1 + 0.3, b.SLOT_Y1 + 0.3))
        self.forbidden += [(x - 2.2, y - 2.2, x + 2.2, y + 2.2) for x, y in b.HOLES.values()]
        # every drilled hole (a connector's legs and pegs, mounting holes) is in the way on
        # both faces: the same-side courtyard and pad checks do not see a back part's legs
        # under a front part (2026-10-23: a resistor was placed over USB-C's peg)
        self.holes = []
        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                if pad.HasHole():
                    bb = pad.GetBoundingBox()
                    self.holes.append((mm(bb.GetLeft()) - 0.25, mm(bb.GetTop()) - 0.25,
                                       mm(bb.GetRight()) + 0.25, mm(bb.GetBottom()) + 0.25, fp.GetReference()))
        # the antenna end as drawn from the placed module (newBoard's ANT_KEEPOUT is only the seed)
        self.ant_keepout = b.antenna_keepout(self.board)
        self.back_only = [box for _, box in b.footprint_keepouts(self.board)] + list(b.LABEL_SLOTS.values()) + \
                         [(x - b.HOLE_RING, y - b.HOLE_RING, x + b.HOLE_RING, y + b.HOLE_RING) for x, y in b.HOLES.values()]
        # the front is bare under the panel and the tails' drop: a front part lives in the
        # chin, south of the drop (checkFpc.py's tails' drop check)
        self.front_only = [(-999, -999, b.W + 999, b.SLOT_Y0 + b.TAIL_BEND + b.TAIL_CLR)]
        self.cell = b.cell_geometry(self.board)
        self.tails = b.tail_corridors(self.board)

    def legal(self, part):
        box = nb._box(part.fp, TOUCH)
        tight = nb._box(part.fp, 0.1)
        extra = self.back_only if part.side == nb.BOT else self.front_only
        ant = [] if part.ref == "U11" else [self.ant_keepout]
        if any(nb._hits(tight, o) for o in self.forbidden + extra + ant):
            return False
        room = nb.under_cell_room(part.fp, self.cell)     # under the cell: low parts only
        if room is not None and (room[0] is None or room[0] < 0):
            return False
        if nb.under_tail(part.fp, self.tails):            # under a flex tail: low parts only
            return False
        if any(nb._hits(tight, h[:4]) for h in self.holes if h[4] != part.ref):
            return False
        for ref, other in self.parts.items():
            if ref != part.ref and ref not in self.parked and other.side == part.side \
                    and nb._hits(box, self.boxes[ref]):
                return False
        return self.copper_ok(part) and self.pads_ok(part) and self.no_via_in_pad(part)

    def no_via_in_pad(self, part):
        """No via of any net under the part's SMD pads (a moved part once landed on a
        stitching via: via in pad, 2026-10-25)."""
        if not hasattr(self, "_vias"):
            self._vias = [(t.GetPosition(), t.GetWidth(pcbnew.F_Cu) // 2) for t in self.board.GetTracks()
                          if t.Type() == pcbnew.PCB_VIA_T]
        for pd in part.fp.Pads():
            if pd.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            if any(pd.HitTest(pos, r) for pos, r in self._vias):
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

    def facing_cost(self, nets):
        """FACING_MM x (1 - cos)/2 per airwire end at a pin of a chip with more than 4
        pads, where the angle is between the wire and the pin's outward side."""
        c = 0.0
        for n in nets:
            ends = [(ref, pad) for ref, pad in self.net_pads.get(n, []) if ref not in self.parked]
            if len(ends) < 2:
                continue
            pts = [(mm(pad.GetPosition().x), mm(pad.GetPosition().y)) for _, pad in ends]
            for i, j in mst_edges(pts):
                for a, b in ((i, j), (j, i)):
                    ref, pad = ends[a]
                    part = self.parts[ref]
                    if len(part.fp.Pads()) <= 4:
                        continue
                    cx, cy = part.pos()
                    ox, oy = pts[a][0] - cx, pts[a][1] - cy
                    if abs(ox) < 1e-6 and abs(oy) < 1e-6:     # exposed pad: no side
                        continue
                    nx, ny = (math.copysign(1, ox), 0.0) if abs(ox) >= abs(oy) else (0.0, math.copysign(1, oy))
                    dx, dy = pts[b][0] - pts[a][0], pts[b][1] - pts[a][1]
                    d = math.hypot(dx, dy)
                    if d > 1e-6:
                        c += FACING_MM * (1 - (dx * nx + dy * ny) / d) / 2
        return c

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
        before = self.nets_cost(nets, others, other_ids) + sum(self.decap_cost(r) for r in refs)
        undo = apply()
        if not all(self.legal(self.parts[r]) for r in refs):
            undo()
            return False
        saved = {n: self.wires[n] for n in nets}
        for n in nets:
            self.wires[n] = self._wires(n)
        after = self.nets_cost(nets, others, other_ids) + sum(self.decap_cost(r) for r in refs)
        if after < before - 1e-6:
            self.moved(*refs)
            return True
        undo()
        self.wires.update(saved)
        return False


def satellites(m, ic):
    """The 2-pad parts that belong to `ic`'s block: they share a signal net with it,
    or they decouple one of its pins and sit within 4 mm of it."""
    icp = m.parts[ic]
    cx, cy = icp.pos()
    sig = {pd.GetNetname() for pd in icp.fp.Pads()} - NO_AIRWIRE - {""}
    out = []
    for ref, p in m.parts.items():
        pads = list(p.fp.Pads())
        if ref == ic or len(pads) != 2 or ref in nb.ANCHORED or ref in m.locked:
            continue
        nets = {pd.GetNetname() for pd in pads}
        near = math.hypot(p.pos()[0] - cx, p.pos()[1] - cy) < 4.0 + max(nb._box(icp.fp)[2] - nb._box(icp.fp)[0],
                                                                        nb._box(icp.fp)[3] - nb._box(icp.fp)[1]) / 2
        decap = False
        if ref in m.decaps and near:
            # a decap belongs to the IC whose rail pin is nearest to it
            px, py = p.pos()
            owner = min(m.decaps[ref][1], key=lambda rp: math.hypot(mm(rp[1].GetPosition().x) - px,
                                                                     mm(rp[1].GetPosition().y) - py))[0]
            decap = owner == ic
        if nets & sig or decap:
            out.append(ref)
    return out


def place_block(board, ic, sats=None, rotations=(0, 90, 180, 270), radius=3.0, log=print, locked=(), force=False):
    """Rotate `ic` in place and rebuild its block around it, for each rotation; keep the
    cheapest. The owner's lesson (2026-10-17): the SE050 turned -90 deg so its I2C
    pins face the MCU, with its pull-ups re-seated inline, scored 26 where the best
    rotation of the chip alone, passives left where they were, scored 56 -- an IC's
    orientation can only be judged together with its satellites."""
    m = Model(board, locked)
    sats = list(sats) if sats is not None else satellites(m, ic)
    icp = m.parts[ic]
    x0, y0, r0 = *icp.pos(), icp.rot()
    snap = {ref: (*m.parts[ref].pos(), m.parts[ref].rot()) for ref in sats}
    # decaps first (they own the pin next to them), then the most-connected
    sats.sort(key=lambda r2: (r2 not in m.decaps, -len(m.part_nets.get(r2, set()) & m.part_nets.get(ic, set()))))
    block_nets = set().union(*(m.part_nets.get(r2, set()) for r2 in sats + [ic]))

    def block_cost():
        for n in block_nets:
            m.wires[n] = m._wires(n)
        others, ids = m.all_wires(skip=block_nets)
        return m.nets_cost(block_nets, others, ids) + sum(m.decap_cost(r2) + m.cluster_cost(r2) for r2 in sats) \
            + m.facing_cost(block_nets)

    def best_spot(ref, box, step):
        p = m.parts[ref]
        nets = m.part_nets.get(ref, set())
        others, ids = m.all_wires(skip=nets)
        best = None
        x, y0b = box[0], box[1]
        xs = [box[0] + i * step for i in range(int((box[2] - box[0]) / step) + 1)]
        ys = [box[1] + j * step for j in range(int((box[3] - box[1]) / step) + 1)]
        for cx in xs:
            for cy in ys:
                for rot in (0, 90, 180, 270):
                    p.set(cx, cy, rot)
                    if not m.legal(p):
                        continue
                    for n in nets:
                        m.wires[n] = m._wires(n)
                    c = m.nets_cost(nets, others, ids) + m.decap_cost(ref) + m.cluster_cost(ref) + m.facing_cost(nets)
                    if best is None or c < best[0]:
                        best = (c, cx, cy, rot)
        return best

    def seat(ref, spot):
        """Put ref at spot (or back where it was) and bring its airwires along."""
        p = m.parts[ref]
        if spot is not None:
            p.set(spot[1], spot[2], spot[3])
        m.moved(ref)
        for n in m.part_nets.get(ref, set()):
            m.wires[n] = m._wires(n)

    def shift_block(dx, dy):
        for ref in [ic] + sats:
            p = m.parts[ref]
            p.set(p.pos()[0] + dx, p.pos()[1] + dy, p.rot())
        for ref in [ic] + sats:
            m.moved(ref)

    results = []
    for rot in rotations:
        m.parked = set(sats)
        # in place if legal, else the nearest legal spot within 1 mm (a part placed by
        # hand may sit closer to a margin than this model's rules allow)
        spots = sorted(((x0 + i * 0.25, y0 + j * 0.25) for i in range(-4, 5) for j in range(-4, 5)),
                       key=lambda q: math.hypot(q[0] - x0, q[1] - y0))
        for qx, qy in spots:
            icp.set(qx, qy, rot)
            m.moved(ic)
            if m.legal(icp):
                break
        else:
            log(f"  {ic} @ {rot}: no legal spot within 1 mm")
            continue
        for n in block_nets:
            m.wires[n] = m._wires(n)
        bx = nb._box(icp.fp)
        area = (bx[0] - radius, bx[1] - radius, bx[2] + radius, bx[3] + radius)
        ok = True
        for ref in sats:
            m.parked.discard(ref)
            b1 = best_spot(ref, area, 0.5)
            if b1 is None:
                ok = False
                log(f"  {ic} @ {rot}: no legal spot for {ref}")
                break
            _, cx, cy, _ = b1
            seat(ref, best_spot(ref, (cx - 0.5, cy - 0.5, cx + 0.5, cy + 0.5), 0.25) or b1)
        if ok:
            for _ in range(2):          # refine: re-seat each satellite near where it is
                for ref in sats:
                    px, py = m.parts[ref].pos()
                    m.parked = {ref}
                    seat(ref, best_spot(ref, (px - 1.0, py - 1.0, px + 1.0, py + 1.0), 0.25))
                    m.parked = set()
            # the whole block may slide too (the owner moved the SE 1.5 mm as well as turning it)
            c = block_cost()
            for _ in range(6):
                improved = False
                for dx, dy in ((0.5, 0), (-0.5, 0), (0, 0.5), (0, -0.5)):
                    shift_block(dx, dy)
                    if all(m.legal(m.parts[r2]) for r2 in [ic] + sats):
                        c2 = block_cost()
                        if c2 < c - 1e-6:
                            c, improved = c2, True
                            break
                    shift_block(-dx, -dy)
                if not improved:
                    break
            m.parked = set()
            c = block_cost()
            results.append((c, rot, {r2: (*m.parts[r2].pos(), m.parts[r2].rot()) for r2 in [ic] + sats}))
            log(f"  {ic} @ {rot}: block cost {c:.1f}")
        m.parked = set()
        for ref, (sx, sy, sr) in snap.items():
            m.parts[ref].set(sx, sy, sr)
            m.moved(ref)
    icp.set(x0, y0, r0)
    m.moved(ic)
    m.parked = set()
    start = block_cost()
    if not results:
        return start, None
    c, rot, pos = min(results, key=lambda t: t[0])
    if force or c < start - 1e-6:       # force: the caller has chosen the rotation(s) to try
        for ref, (sx, sy, sr) in pos.items():
            m.parts[ref].set(sx, sy, sr)
            m.moved(ref)
        for p in m.parts.values():
            p.fp.Reference().SetPosition(p.fp.GetPosition())
    log(f"{ic}: start {start:.1f} -> best {c:.1f} at {rot} deg ({'applied' if force or c < start - 1e-6 else 'kept'})")
    return start, (c, rot)


def repair(board, locked, max_mm=1.0, log=print, only=None):
    """Locked parts that now violate against existing copper or a neighbour's pads get
    the smallest nudge (<= max_mm, rotation kept) that clears them. Returns the moves."""
    m = Model(board, locked)
    moves = []
    for ref in sorted(m.locked):
        p = m.parts.get(ref)
        if p is None or ref in nb.ANCHORED or (only is not None and ref not in only) \
                or (m.copper_ok(p, 0.15) and m.pads_ok(p, 0.15)):
            continue
        x0, y0, rot = *p.pos(), p.rot()
        best = None
        for r10 in range(1, int(max_mm / 0.05) + 1):
            rr = r10 * 0.05
            for k in range(16):
                a = 2 * math.pi * k / 16
                p.set(x0 + rr * math.cos(a), y0 + rr * math.sin(a), rot)
                ok = all(not nb._hits(nb._box(p.fp, TOUCH), m.boxes[o]) for o, q in m.parts.items()
                         if o != ref and q.side == p.side)
                if ok and m.copper_ok(p, 0.15) and m.pads_ok(p, 0.15):
                    best = (rr, *p.pos())
                    break
            if best:
                break
        if best:
            p.set(best[1], best[2], rot)
            m.moved(ref)
            moves.append((ref, round(best[0], 2)))
            log(f"repair: nudged {ref} {best[0]:.2f} mm clear of copper/pads")
        else:
            p.set(x0, y0, rot)
            log(f"repair: {ref} conflicts and no spot within {max_mm} mm clears it; left for review")
    return moves


def optimize(board, radius=4.0, passes=8, seed=1, log=print, locked=(), sides=(nb.BOT,), only=None):
    """sides: the faces whose parts may move; only: if given, just these refs."""
    random.seed(seed)
    m = Model(board, locked)
    movable = [r for r, p in m.parts.items()
               if r not in nb.ANCHORED and r not in PINNED and r not in m.locked and p.side in sides
               and (only is None or r in only) and (r in m.part_nets or r in m.decaps)]
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
            base = m.nets_cost(nets, others, other_ids) + m.decap_cost(ref) + m.cluster_cost(ref)
            saved = {n: m.wires[n] for n in nets}
            for cx, cy, cr in cands:
                p.set(cx, cy, cr)
                if not m.legal(p):
                    continue
                for n in nets:
                    m.wires[n] = m._wires(n)
                c = m.nets_cost(nets, others, other_ids) + m.decap_cost(ref) + m.cluster_cost(ref)
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
