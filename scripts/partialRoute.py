#!/usr/bin/env python3
"""Partial autoroute: route the wiring INSIDE functional blocks and leave the rest,
and every track already on the board, to the person routing it.

    $KICAD_PY scripts/partialRoute.py [board.kicad_pcb] --out DIR [--blocks U4,U5 ...] [--minutes 6]

Drives humanizedKicadAutoRouter (tools/humanRouter in the malaka-chrono repo, or
HUMAN_ROUTER=/path) without its rip-everything start:
  - its block plan: every multi-pin part anchors a block, each passive joins the
    block it is most tied to; a connection inside one block is block wiring, one
    between blocks belongs to a bundle (left to the human);
  - every existing track and via is loaded as FIXED copper: nothing may cross
    it and negotiation can never rip it; same-net copper stays joinable;
  - a connection the existing copper already makes is skipped (own union-find
    over tracks, vias and pads), so only what is missing gets routed;
  - GND pads in the routed blocks get their fanout via to the plane, unless they
    already reach a GND via.
Writes DIR/wallet.kicad_pcb = the input board + the new copper, zones refilled,
then KiCad's DRC. The input board is never modified.
"""
import argparse
import json
import math
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
HR = Path(os.environ.get("HUMAN_ROUTER", HERE.parent.parent / "tools/humanRouter"))
sys.path.insert(0, str(HR))

import pcbnew  # noqa: E402
from shapely.geometry import LineString, Point, box  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402

from boardModel import B, F, IN1, IN2, boardPlanes, innerPlaneFills, loadModel, ripCopper  # noqa: E402
from emit import runDrc, writeItem  # noqa: E402
from router import Knobs, Router  # noqa: E402
from run import layersFor, negotiate, viaMmFor, winPadsFor  # noqa: E402
from strategy import followLeaders, phaseOrder, plan  # noqa: E402

NM = 1e6
LAYER_OF = {"F.Cu": F, "In1.Cu": IN1, "In2.Cu": IN2, "B.Cu": B}


def existing_copper(board):
    tracks, vias = [], []
    for t in board.GetTracks():
        net = t.GetNetname()
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition()
            vias.append((net, (p.x / NM, p.y / NM), t.GetWidth(pcbnew.F_Cu) / NM, t.GetDrill() / NM))
        elif t.GetClass() in ("PCB_TRACK", "PCB_ARC"):
            a, b = t.GetStart(), t.GetEnd()
            tracks.append((net, LAYER_OF[t.GetLayerName()], (a.x / NM, a.y / NM), (b.x / NM, b.y / NM),
                           t.GetWidth() / NM))
    return tracks, vias


def load_fixed(router, tracks, vias):
    """Existing copper into the router's space as fixed (never rippable) copper."""
    sp = router.sp
    n = 0
    for net, layer, p1, p2, w in tracks:
        it = router.trackItem(net, layer, p1, p2, w, router.rules[net].clearance, 0)
        it.tag = "existing"
        _fix(sp, sp.add(it))
        router.netItems.setdefault(it.net, set()).add(it.iid)
        n += 1
    for net, p, dia, drill in vias:
        rule = router.rules[net]
        it = router.viaItem(net, p, rule, 0)
        it.width, it.drill = dia, drill
        it.tag = "existing"
        _fix(sp, sp.add(it))
        router.netItems.setdefault(it.net, set()).add(it.iid)
        n += 1
    return n


def _fix(sp, item):
    for l, (i0, j0, i1, j1), mask in item.stamped:
        sub = sp.fixed[l, i0:i1, j0:j1]
        sub[mask & (sub == 0)] = item.net


class Joined:
    """Which pads the existing copper already joins (and which reach a via)."""

    def __init__(self, model, tracks, vias):
        self.parent = {}
        pads = model.pads
        self.pad_tree = {l: [] for l in (F, IN1, IN2, B)}
        for p in pads:
            for l in p.layers:
                self.pad_tree[l].append(p)
        trees = {l: (STRtree([p.polys.get(l, p.poly) for p in ps]), ps) for l, ps in self.pad_tree.items() if ps}
        ends = []          # (key, layer, point)
        for i, (net, layer, a, b, w) in enumerate(tracks):
            self.union(("t", i), ("t", i))
            ends += [(("t", i), layer, a), (("t", i), layer, b)]
        for i, (net, p, dia, drill) in enumerate(vias):
            self.via_nodes = None
            ends += [(("v", i), l, p) for l in (F, IN1, IN2, B)]
        self.has_via = set()
        # endpoints that meet
        grid = {}
        for key, layer, p in ends:
            grid.setdefault((layer, round(p[0], 2), round(p[1], 2)), []).append(key)
        for keys in grid.values():
            for k in keys[1:]:
                self.union(keys[0], k)
        # track ends / vias inside pads
        for key, layer, p in ends:
            if layer not in trees:
                continue
            tree, ps = trees[layer]
            for idx in tree.query(Point(p).buffer(0.02)):
                pad = ps[int(idx)]
                if pad.polys.get(layer, pad.poly).distance(Point(p)) < 0.02:
                    self.union(("p", pad.pid), key)
        # mid-track junctions (a via or track end landing on a track's middle)
        for i, (net, layer, a, b, w) in enumerate(tracks):
            seg = None
            for key, l2, p in ends:
                if l2 != layer or key == ("t", i):
                    continue
                if seg is None:
                    seg = LineString([a, b])
                if seg.distance(Point(p)) < w / 2:
                    self.union(("t", i), key)
        roots_with_via = {self.find(("v", i)) for i in range(len(vias))}
        self.via_roots = roots_with_via

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb

    def joined(self, pa, pb):
        return self.find(("p", pa.pid)) == self.find(("p", pb.pid))

    def reaches_via(self, pad):
        return self.find(("p", pad.pid)) in self.via_roots


def route_blocks(src, out, blocks=None, minutes=6.0, knobs=(), log=print, skip_nets=(), first_nets=(), max_mm=None, leave_under=()):
    t0 = time.perf_counter()
    os.makedirs(out, exist_ok=True)
    name = Path(src).stem
    for ext in (".kicad_pro", ".kicad_dru"):
        s = Path(src).with_suffix(ext)
        if s.exists():
            shutil.copy(s, Path(out) / (name + ext))
    lib = Path(src).parent / "fp-lib-table"
    if lib.exists():
        rel = os.path.relpath(Path(src).parent, Path(out).resolve())
        (Path(out) / "fp-lib-table").write_text(lib.read_text().replace("${KIPRJMOD}", "${KIPRJMOD}/" + rel))

    board = pcbnew.LoadBoard(str(src))          # gets the new copper on top of what is there
    tracks, vias = existing_copper(board)
    old_ids = {t.m_Uuid.AsString() for t in board.GetTracks()}
    work = pcbnew.LoadBoard(str(src))           # the model: pads, rules, planes only
    zones = boardPlanes(work)
    ripCopper(work, keep=zones)
    model = loadModel(work, str(Path(src).with_suffix(".kicad_pro")))
    model.planes = innerPlaneFills(work, zones, model.outline) if zones else {"GND": None}
    k = Knobs()
    for kv in knobs:
        key, value = kv.split("=", 1)
        setattr(k, key, type(getattr(k, key))(value))
    router = Router(model, k, lambda m: None)
    nfix = load_fixed(router, tracks, vias)
    conns, block, bundles = plan(model, router, lambda m: None, [])
    have = Joined(model, tracks, vias)
    want_blocks = set(blocks) if blocks else None
    # inside the pad ring of a large part (the free copper under its body), where
    # the bus that crosses the part runs: a block link straight across it is
    # left to the human, who lays it with that bus
    bodies = []
    for ref in leave_under:
        pts = [p.center for p in model.pads if p.ref == ref]
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        bodies.append(box(min(xs) + 1.0, min(ys) + 1.0, max(xs) - 1.0, max(ys) - 1.0))
    todo, early, skipped = [], [], Counter()
    for c in conns:
        if c.net in first_nets and c.kind != "gnd":
            # a critical net between blocks (e.g. a USB pair) goes down before the block
            # wiring takes the room around its pins
            if c.b is None or not have.joined(c.a, c.b):
                early.append(c)
            continue
        if c.bundle:
            skipped["between blocks"] += 1
            continue
        if want_blocks is not None and c.block not in want_blocks:
            skipped["other block"] += 1
            continue
        if c.net in skip_nets:
            skipped["net left to the human"] += 1
            continue
        if bodies and c.b is not None and c.kind != "gnd" and c.a.ref != c.b.ref \
                and any(LineString([c.a.center, c.b.center]).intersects(b) for b in bodies):
            skipped["crosses under " + "/".join(leave_under)] += 1
            continue
        if max_mm and c.b is not None and math.dist(c.a.center, c.b.center) > max_mm:
            # a long link inside a block is a bus run in all but name: it crosses
            # the channels the between-block wiring needs, so the human lays it
            skipped["longer than max_mm"] += 1
            continue
        if c.kind == "gnd":
            if have.reaches_via(c.a):
                skipped["already done"] += 1
                continue
        elif c.b is not None and have.joined(c.a, c.b):
            skipped["already done"] += 1
            continue
        todo.append(c)
    log(f"existing copper: {len(tracks)} tracks, {len(vias)} vias ({nfix} fixed items)")
    log(f"connections: {len(conns)}; routing {len(early)} first ({', '.join(sorted(set(first_nets)))}), "
        f"{len(todo)} inside blocks; skipped {dict(skipped)}")
    failed = []
    for c in followLeaders(early) + phaseOrder(todo):
        st, _ = router.route(c, layersFor(c), winPadsFor(c), viaMm=viaMmFor(c, k))
        if st != "ok":
            failed.append(c)
    nfail1 = len(failed)
    if failed:
        negotiate(router, [c for c in failed if c.kind != "gnd"], lambda m: None, maxRounds=12,
                  deadline=t0 + minutes * 60)
    todo = early + todo
    unrouted = [c for c in todo if not router.connected(c) and c.kind != "gnd"]
    gnd_left = [c for c in todo if c.kind == "gnd" and not router.connected(c)]
    nets = board.GetNetsByName()
    count = Counter()
    for r in router.routes.values():
        for iid in r.items:
            it = router.sp.items[iid]
            if writeItem(board, nets[r.conn.net], it) is not None:
                count[it.kind] += 1
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    outBoard = str(Path(out) / (name + ".kicad_pcb"))
    board.Save(outBoard)
    drc = runDrc(outBoard, str(Path(out) / "drc.json"))
    # a stub of new copper that ends in nothing (a fanout whose via was refused) is
    # removed; a dangling track that was already on the board is the human's
    stubs = {it["uuid"] for v in drc["violations"] if v["type"] == "track_dangling" for it in v["items"]} - old_ids
    if stubs:
        for t in list(board.GetTracks()):
            if t.m_Uuid.AsString() in stubs:
                board.Remove(t)
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(outBoard)
        drc = runDrc(outBoard, str(Path(out) / "drc.json"))
        log(f"removed {len(stubs)} dangling stub(s) of new copper")
    viol = Counter(v["type"] for v in drc["violations"])
    res = dict(routed=len(todo) - len(unrouted) - len(gnd_left), todo=len(todo), first_pass_failed=nfail1,
               unrouted=[f"{c.net}:{c.a.ref}.{c.a.num}->{c.b.ref}.{c.b.num}" for c in unrouted],
               gnd_fanouts_missing=len(gnd_left), new_tracks=count["track"], new_vias=count["via"],
               drc=dict(viol), drc_unconnected=len(drc["unconnected_items"]),
               seconds=round(time.perf_counter() - t0, 1))
    json.dump(res, open(Path(out) / "partial.json", "w"), indent=1)
    log(f"routed {res['routed']}/{len(todo)} block connections (+{count['track']} tracks, "
        f"{count['via']} vias), {len(unrouted)} unrouted, {len(gnd_left)} GND fanouts missing; "
        f"DRC {sum(viol.values())} {dict(viol)}, {res['drc_unconnected']} unconnected (incl. the "
        f"between-block wiring left to the human) in {res['seconds']} s")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", nargs="?", default=str(HERE / "board/wallet.kicad_pcb"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--blocks", help="comma-separated anchor refs (default: every block)")
    ap.add_argument("--minutes", type=float, default=6.0)
    ap.add_argument("--knob", action="append", default=[])
    ap.add_argument("--first-nets", default="", help="comma-separated nets routed before everything else, "
                    "even between blocks (critical pairs)")
    ap.add_argument("--leave-under", default="", help="comma-separated refs: block links straight across "
                    "under their body are left to the human")
    ap.add_argument("--max-mm", type=float, help="leave block connections longer than this (pad to pad)")
    ap.add_argument("--skip-nets", default="", help="comma-separated nets left to the human")
    a = ap.parse_args()
    split = lambda v: tuple(n for n in v.split(",") if n)
    route_blocks(a.board, a.out, a.blocks.split(",") if a.blocks else None, a.minutes, a.knob,
                 skip_nets=split(a.skip_nets), first_nets=split(a.first_nets), max_mm=a.max_mm,
                 leave_under=split(a.leave_under))


if __name__ == "__main__":
    main()
