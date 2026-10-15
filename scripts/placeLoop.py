#!/usr/bin/env python3
"""Placement feedback loop on a hand-worked board: optimize -> partial route -> judge.

    $KICAD_PY scripts/placeLoop.py --base REF_COMMIT [--iters 4] [--work DIR]

Every part the existing copper touches, every part moved by hand since REF_COMMIT
(the owner's placement) and the mechanical anchors are locked. The rest is
optimized (scripts/optimizePlacement.py: ratsnest crossings + length + decoupling
distance, courtyards may touch, pads clear of existing copper), then the block
wiring is routed on a copy (scripts/partialRoute.py, humanRouter, existing copper
fixed) and the result is scored. Each iteration tries a different setting; the
log (DIR/loop.json) is the record of what helped.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import optimizePlacement as op  # noqa: E402
import partialRoute as pr  # noqa: E402


def touched_by_copper(board):
    ends = []
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            ends.append((t.GetPosition(), None))
        else:
            ends += [(t.GetStart(), t.GetLayer()), (t.GetEnd(), t.GetLayer())]
    refs = set()
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if not pad.GetNetname():
                continue
            for p, layer in ends:
                if (layer is None or pad.IsOnLayer(layer)) and pad.HitTest(p):
                    refs.add(fp.GetReference())
                    break
    return refs


def moved_since(board, ref_board):
    pos = {f.GetReference(): (f.GetPosition(), f.GetOrientationDegrees()) for f in ref_board.GetFootprints()}
    out = set()
    for f in board.GetFootprints():
        r = f.GetReference()
        if r in pos:
            (p0, a0) = pos[r]
            p1 = f.GetPosition()
            if math.hypot(p1.x - p0.x, p1.y - p0.y) > 50_000 or abs((f.GetOrientationDegrees() - a0) % 360) > 0.1:
                out.add(r)
    return out


KRT = Path(__file__).resolve().parent.parent.parent.parent / "projects/KiCadRoutingTools"


def remaining_routability(routed_dir):
    """What an autorouter makes of the wiring left to the human: KiCadRoutingTools'
    route.py on a copy, every existing track read-only. Fewer failures and vias =
    an easier hand route."""
    import re
    src = Path(routed_dir) / "wallet.kicad_pcb"
    out = Path(routed_dir) / "krt"
    out.mkdir(exist_ok=True)
    for f in ("wallet.kicad_pcb", "wallet.kicad_pro", "wallet.kicad_dru"):
        shutil.copy(Path(routed_dir) / f, out / f)
    nets = subprocess.run([sys.executable if False else "python3", "-c",
                           "import sys; sys.path.insert(0,'.')\n"
                           "from kicad_parser import parse_kicad_pcb\n"
                           f"p=parse_kicad_pcb('{out / 'wallet.kicad_pcb'}')\n"
                           "print(' '.join(sorted(n.name for n in p.nets.values() if n.name and n.name!='GND' "
                           "and not n.name.startswith('NC') and len(n.pads)>1)))"],
                          cwd=KRT, capture_output=True, text=True).stdout.split()
    log = subprocess.run(["python3", "route.py", str(out / "wallet.kicad_pcb"), str(out / "routed.kicad_pcb"),
                          "--nets", *nets, "--layers", "F.Cu", "In2.Cu", "B.Cu", "--track-width", "0.15",
                          "--clearance", "0.15", "--via-size", "0.45", "--via-drill", "0.25",
                          "--keep-input-copper"], cwd=KRT, capture_output=True, text=True).stdout
    sums = [json.loads(m) for m in re.findall(r"JSON_SUMMARY: (\{.*\})", log)]
    if not sums:
        return None
    main, last = sums[0], sums[-1]
    failed = set(main["failed_single"]) | {f["net_name"] for f in main["failed_multipoint"]}
    if len(sums) > 1:
        failed = (failed - set(last.get("rescue", {}).get("recovered", []))) | set(last["failed_single"]) \
            | {f["net_name"] for f in last["failed_multipoint"]}
    return dict(failed=sorted(failed), vias=sum(s.get("total_vias", 0) for s in sums),
                iterations=sum(s.get("total_iterations", 0) for s in sums))


def drc_pad_refs(src, work):
    """Footprints whose pads KiCad's own (refilled) DRC names in a clearance violation."""
    import re
    d = work / "drc_in"
    d.mkdir(exist_ok=True)
    for ext in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
        shutil.copy(src.with_suffix(ext), d / ("wallet" + ext))
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--format", "json", "-o", str(d / "drc.json"),
                    str(d / "wallet.kicad_pcb")], capture_output=True)
    refs = set()
    for v in json.load(open(d / "drc.json"))["violations"]:
        if v["type"] in ("clearance", "hole_clearance", "shorting_items"):
            for it in v["items"]:
                m = re.match(r"Pad \S+ \[.*?\] of (\S+)", it["description"])
                if m:
                    refs.add(m.group(1))
    return refs


def score(res, xing, rest=None):
    # completion of the block wiring first, then vias, then what is left for the human
    return (len(res["unrouted"]) * 100 + res["gnd_fanouts_missing"] * 20 + res["new_vias"]
            + 2 * sum(v for k, v in res["drc"].items() if k in ("clearance", "hole_clearance", "shorting_items",
                                                                 "track_dangling", "solder_mask_bridge"))
            + 0.5 * xing
            + (0 if rest is None else 60 * len(rest["failed"]) + 0.5 * rest["vias"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", nargs="?", default=str(HERE.parent / "board/wallet.kicad_pcb"))
    ap.add_argument("--base", required=True, help="commit whose placement counts as 'not hand-moved'")
    ap.add_argument("--work", default="/tmp/placeLoop")
    ap.add_argument("--first-nets", default="USB_DP_MCU,USB_DN_MCU", help="routed before the block wiring")
    ap.add_argument("--skip-nets", default="", help="left to the human")
    ap.add_argument("--settings", default="[]", help='JSON list of {"radius":..,"decap":..,"crossing":..}')
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    src = Path(a.board)
    refb = work / "base.kicad_pcb"
    refb.write_bytes(subprocess.run(["git", "show", f"{a.base}:board/wallet.kicad_pcb"], cwd=HERE.parent,
                                    capture_output=True, check=True).stdout)
    board = pcbnew.LoadBoard(str(src))
    locked = touched_by_copper(board) | moved_since(board, pcbnew.LoadBoard(str(refb)))
    print(f"locked: {len(locked)} parts (copper-touched or hand-moved)")
    drc_refs = drc_pad_refs(src, work)
    print(f"parts in KiCad clearance violations: {sorted(drc_refs)}")
    settings = json.loads(a.settings) or [{"name": "as-is", "radius": 0}]
    log = []
    for i, st in enumerate(settings):
        d = work / f"it{i}_{st.get('name', i)}"
        if d.exists():
            shutil.rmtree(d)
        d.mkdir()
        for ext in (".kicad_pro", ".kicad_dru"):
            shutil.copy(src.with_suffix(ext), d / ("wallet" + ext))
        shutil.copy(src.parent / "fp-lib-table", d / "fp-lib-table")
        b = pcbnew.LoadBoard(str(src))
        op.DECAP_MM = st.get("decap", 3.0)
        op.CROSSING_MM = st.get("crossing", 6.0)
        op.repair(b, locked, log=print, only=drc_refs)
        if st.get("radius", 0) > 0:
            op.optimize(b, radius=st["radius"], passes=st.get("passes", 8), locked=locked, log=lambda m: None)
        length, xing = op.Model(b, locked).total()
        placed = d / "wallet.kicad_pcb"
        pcbnew.SaveBoard(str(placed), b)
        res = pr.route_blocks(str(placed), str(d / "routed"), minutes=st.get("minutes", 5), log=lambda m: None,
                              first_nets=tuple(filter(None, a.first_nets.split(","))),
                              skip_nets=tuple(filter(None, a.skip_nets.split(","))))
        rest = remaining_routability(d / "routed")
        sc = score(res, xing, rest)
        row = dict(setting=st, airwire_mm=round(length), crossings=xing, score=round(sc, 1), rest=rest,
                   **{k: res[k] for k in ("routed", "todo", "unrouted", "gnd_fanouts_missing", "new_tracks",
                                          "new_vias", "drc", "drc_unconnected", "seconds")})
        log.append(row)
        print(f"[{i}] {st.get('name', i)}: score {sc:.1f} | crossings {xing}, airwire {length:.0f} mm | "
              f"blocks {res['routed']}/{res['todo']}, unrouted {len(res['unrouted'])}, gnd missing "
              f"{res['gnd_fanouts_missing']}, +{res['new_vias']} vias | DRC {res['drc']} | rest: "
              f"{'n/a' if rest is None else str(len(rest['failed'])) + ' failed ' + str(rest['failed']) + ', ' + str(rest['vias']) + ' vias'}",
              flush=True)
        json.dump(log, open(work / "loop.json", "w"), indent=1)
    best = min(range(len(log)), key=lambda j: log[j]["score"])
    print(f"best: [{best}] {settings[best].get('name', best)}")


if __name__ == "__main__":
    main()
