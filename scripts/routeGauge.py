#!/usr/bin/env python3
"""How routable is this board? Autoroute everything left on a copy and measure it.

    $KICAD_PY scripts/routeGauge.py [board.kicad_pcb] --out DIR [--knob NAME=VALUE ...] [--minutes 12]

Runs humanizedKicadAutoRouter's full finish (tools/humanRouter, or HUMAN_ROUTER=/path)
with --keep-copper, so every existing track and via stays, and with the owner's
conventions: no GND via beside a GND pad (fanoutParts=none), the outer pours
stitched to the GND plane. The router repeats itself to within ~0.1 % (1395 vs 1396 mm
on the same board), so two placements can be compared run against run (KiCadRoutingTools, the earlier gauge, gave 2 vs 6 failed
nets on near-identical boards).

Writes DIR/routed/<board> (the autorouted copy, a reference, never the deliverable)
and DIR/gauge.json:
  unconnected, drc       KiCad's verdict on the routed copy
  new_mm                 added copper per layer; the front is the highway layer
  vias                   added signal vias / GND (stitching + return) vias
  craft                  detours, wiggles, hairpins (hardware/carrier/.tools/audit_craft.py)
  facing                 per chip, how well its pins face their partners (+1 best)
  unreached_gnd          GND pads no pour reaches (each needs a connection by hand)
The input board is never modified.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent.parent
HR = Path(os.environ.get("HUMAN_ROUTER", HERE.parent.parent / "tools/humanRouter"))
CRAFT = HERE.parent / "carrier/.tools/audit_craft.py"
CHIPS = ["U1", "U2", "U4", "U5", "U6", "U7", "U8", "U9", "U12", "U13", "U14", "U15"]
OWNER_KNOBS = ["fanoutParts=none"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", nargs="?", default=str(HERE / "board/wallet.kicad_pcb"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--knob", action="append", default=[])
    ap.add_argument("--minutes", type=float, default=12.0)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    work = out / "in"
    work.mkdir(parents=True, exist_ok=True)
    src = Path(a.board).resolve()
    for ext in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
        if src.with_suffix(ext).exists():
            shutil.copy(src.with_suffix(ext), work / ("wallet" + ext))
    cmd = [sys.executable, "run.py", str(work / "wallet.kicad_pcb"), "--out", str(out / "routed"), "--keep-copper",
           "--minutes", str(a.minutes)]
    for kv in OWNER_KNOBS + a.knob:
        cmd += ["--knob", kv]
    log = subprocess.run(cmd, cwd=HR, capture_output=True, text=True)
    (out / "route.log").write_text(log.stdout + log.stderr)
    if log.returncode != 0:
        raise SystemExit(f"router failed (exit {log.returncode}); see {out / 'route.log'}")
    text = log.stdout
    routed = out / "routed" / "wallet.kicad_pcb"
    before = {t.m_Uuid.AsString() for t in pcbnew.LoadBoard(str(work / "wallet.kicad_pcb")).GetTracks()}
    board = pcbnew.LoadBoard(str(routed))
    new_mm, vias = defaultdict(float), defaultdict(int)
    for t in board.GetTracks():
        if t.m_Uuid.AsString() in before:
            continue
        if t.GetClass() == "PCB_VIA":
            vias["gnd" if t.GetNetname() == "GND" else "signal"] += 1
        else:
            a_, b_ = t.GetStart(), t.GetEnd()
            new_mm[t.GetLayerName()] += math.dist((a_.x, a_.y), (b_.x, b_.y)) / 1e6
    drc = re.findall(r"DRC: (\d+) violations (\{.*?\}), (\d+) unconnected", text)
    unreached = re.findall(r"GND pads the pour does not reach: (.*)", text)
    res = dict(board=str(src), unconnected=int(drc[-1][2]) if drc else None, drc=drc[-1][1] if drc else None,
               new_mm={k: round(v) for k, v in sorted(new_mm.items())}, total_mm=round(sum(new_mm.values())),
               vias=dict(vias), unreached_gnd=unreached[-1].split(", ") if unreached else [])
    if CRAFT.exists():
        c = subprocess.run([sys.executable, str(CRAFT), str(routed)], capture_output=True, text=True)
        res["craft"] = [l.strip()[5:] for l in c.stdout.splitlines() if l.strip().startswith("NEW:")]
    sys.path.insert(0, str(HR))
    from analyze import pinFacing
    res["facing"] = {r: round(v["score"], 2) for r, v in pinFacing(pcbnew.LoadBoard(str(work / "wallet.kicad_pcb")),
                                                                   CHIPS).items()}
    json.dump(res, open(out / "gauge.json", "w"), indent=1)
    print(f"unconnected {res['unconnected']}, +{res['total_mm']} mm {res['new_mm']}, vias {res['vias']}, "
          f"craft {len(res.get('craft', []))}, unreached GND pads {len(res['unreached_gnd'])}")


if __name__ == "__main__":
    main()
