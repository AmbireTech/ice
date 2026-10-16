#!/usr/bin/env python3
"""Partial autoroute: route the wiring INSIDE functional blocks and leave the rest,
and every track already on the board, to the person routing it.

    $KICAD_PY scripts/partialRoute.py [board.kicad_pcb] --out DIR [--blocks U4,U5 ...]
              [--first-nets A,B] [--skip-nets A,B] [--minutes 6] [--knob NAME=VALUE ...]

Drives humanizedKicadAutoRouter (tools/humanRouter in the malaka-chrono repo, or
HUMAN_ROUTER=/path) on top of the existing copper:
  - every existing track and via is fixed copper (router.loadExisting): nothing may
    cross it, it is never ripped, and the router knows which pads it already joins;
  - the router's block plan: every multi-pin part anchors a block, each passive joins
    the block it is most tied to; a connection inside one block is block wiring, one
    between blocks belongs to a bus (a "highway"), left to the human;
  - layer plan: block wiring on the back copper, where the parts are; the front is
    kept for the highways. Power has no tracks of its own on the planes: V3V3 pins
    and decaps drop a via to the In1 plane, GND pads of caps and ICs a via to In2
    (other GND pads take the pour). INNER_POWER names the one exception;
  - a block link longer than HIGHWAY_MM (a pull-up at the far end of a bus, a test pad
    across the board), or a power link longer than POWER_HIGHWAY_MM, is routed with
    its bus by the human.
Writes DIR/<name>.kicad_pcb = the input board + the new copper, zones refilled, then
KiCad's DRC; removes any stub of new copper DRC calls dangling. The input board is
never modified.
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

import run as hr_run  # noqa: E402
from boardModel import B, F, IN1, boardPlanes, existingCopper, innerPlaneFills, loadModel, ripCopper  # noqa: E402
from emit import runDrc, writeItem  # noqa: E402
from router import Knobs, Router  # noqa: E402
from run import negotiate, viaMmFor, winPadsFor  # noqa: E402
from strategy import followLeaders, phaseOrder, plan  # noqa: E402

HIGHWAY_MM = 7.0
POWER_HIGHWAY_MM = 10.0     # a power link this long (SYS across the board) is a highway too
# The MCU's 1.1 V core rail joins pin 49 to pin 98 across the package: on In1 (the
# 3.3 V plane, which board/wallet.kicad_dru lets it cross) it never meets a highway.
INNER_POWER = {"VDD11": [B, IN1]}


def route_blocks(src, out, blocks=None, minutes=6.0, knobs=(), log=print, skip_nets=(), first_nets=(),
                 highway_mm=HIGHWAY_MM):
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
    tracks, vias, old_ids = existingCopper(board)
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
    router.loadExisting(tracks, vias)
    conns, block, bundles = plan(model, router, lambda m: None, [])
    want_blocks = set(blocks) if blocks else None
    on_back = lambda c: all(pd is None or B in pd.layers for pd in (c.a, c.b))
    todo, early, skipped = [], [], Counter()
    for c in conns:
        if router.connected(c):
            skipped["already done"] += 1
            continue
        if c.net in first_nets and c.kind != "gnd":
            # a critical net between blocks (e.g. a USB pair) goes down before the
            # block wiring takes the room around its pins
            early.append(c)
            continue
        if c.deferred:
            skipped["GND pad left to the pour"] += 1
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
        if c.kind in ("sig", "pwr") and c.b is not None and c.net not in INNER_POWER \
                and math.dist(c.a.center, c.b.center) > (highway_mm if c.kind == "sig" else POWER_HIGHWAY_MM):
            skipped["highway member (long link)"] += 1
            continue
        todo.append(c)
    early_ids = {id(c) for c in early}

    def layers_for(c):
        if c.kind == "gnd" or id(c) in early_ids:
            return [c.a.side] if c.kind == "gnd" else [F, B]
        if c.net in INNER_POWER:
            return INNER_POWER[c.net]
        return [B] if on_back(c) else [F, B]    # a front test pad needs the front
    hr_run.layersFor = layers_for                # negotiate() asks the same question
    log(f"existing copper: {len(tracks)} tracks, {len(vias)} vias")
    log(f"connections: {len(conns)}; routing {len(early)} first ({', '.join(sorted(set(first_nets)))}), "
        f"{len(todo)} inside blocks; skipped {dict(skipped)}")
    failed = []
    for c in followLeaders(early) + phaseOrder(todo):
        st, _ = router.route(c, layers_for(c), winPadsFor(c), viaMm=viaMmFor(c, k))
        if st != "ok":
            failed.append(c)
    nfail1 = len(failed)
    if failed:
        negotiate(router, [c for c in failed if c.kind != "gnd"], lambda m: None, maxRounds=12,
                  deadline=t0 + minutes * 60)
    todo = early + todo
    unrouted = [c for c in todo if not router.connected(c) and c.kind != "gnd"]
    fanout_left = [c for c in todo if c.kind == "gnd" and not router.connected(c)]
    nets = board.GetNetsByName()
    count, written = Counter(), set()
    outBoard = str(Path(out) / (name + ".kicad_pcb"))

    def write_and_check():
        for rid, r in router.routes.items():
            if rid in written:
                continue
            written.add(rid)
            for iid in r.items:
                it = router.sp.items[iid]
                if writeItem(board, nets[r.conn.net], it) is not None:
                    count[it.kind] += 1
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(outBoard)
        return runDrc(outBoard, str(Path(out) / "drc.json"))
    drc = write_and_check()
    # a GND pad left to the pour that the pour starves (thermal spokes cut by wiring)
    # gets its own fanout after all
    starved = {(it["description"].split(" of ")[1].split()[0], it["description"].split()[1])
               for v in drc["violations"] if v["type"] == "starved_thermal" for it in v["items"]
               if it["description"].startswith("Pad ")}
    repair = [c for c in conns if c.kind == "gnd" and c.deferred and (c.a.ref, c.a.num) in starved]
    if repair:
        for c in repair:
            router.route(c, layers_for(c), winPadsFor(c), viaMm=viaMmFor(c, k))
        fixed = sum(1 for c in repair if router.connected(c))
        drc = write_and_check()
        log(f"starved GND pads: {fixed}/{len(repair)} given their own via")
    # a stub of new copper that ends in nothing is removed; a dangling track that was
    # already on the board is the human's
    stubs = {it["uuid"] for v in drc["violations"] if v["type"] in ("track_dangling", "via_dangling")
             for it in v["items"]} - old_ids
    if stubs:
        for t in list(board.GetTracks()):
            if t.m_Uuid.AsString() in stubs:
                board.Remove(t)
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(outBoard)
        drc = runDrc(outBoard, str(Path(out) / "drc.json"))
        log(f"removed {len(stubs)} dangling stub(s) of new copper")
    viol = Counter(v["type"] for v in drc["violations"])
    fmt = lambda c: f"{c.net}:{c.a.ref}.{c.a.num}->{(c.b.ref + '.' + c.b.num) if c.b else 'plane'}"
    res = dict(routed=len(todo) - len(unrouted) - len(fanout_left), todo=len(todo), first_pass_failed=nfail1,
               unrouted=[fmt(c) for c in unrouted], fanouts_missing=[fmt(c) for c in fanout_left],
               new_tracks=count["track"], new_vias=count["via"], drc=dict(viol),
               drc_unconnected=len(drc["unconnected_items"]), seconds=round(time.perf_counter() - t0, 1))
    json.dump(res, open(Path(out) / "partial.json", "w"), indent=1)
    log(f"routed {res['routed']}/{len(todo)} block connections (+{count['track']} tracks, "
        f"{count['via']} vias), {len(unrouted)} unrouted, {len(fanout_left)} plane fanouts missing; "
        f"DRC {sum(viol.values())} {dict(viol)}, {res['drc_unconnected']} unconnected (incl. the "
        f"highways left to the human) in {res['seconds']} s")
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
    ap.add_argument("--skip-nets", default="", help="comma-separated nets left to the human")
    ap.add_argument("--highway-mm", type=float, default=HIGHWAY_MM)
    a = ap.parse_args()
    split = lambda v: tuple(n for n in v.split(",") if n)
    route_blocks(a.board, a.out, split(a.blocks or "") or None, a.minutes, a.knob,
                 skip_nets=split(a.skip_nets), first_nets=split(a.first_nets), highway_mm=a.highway_mm)


if __name__ == "__main__":
    main()
