#!/usr/bin/env python3
"""Give power fanouts the width of the copper they join.

    $KICAD_PY scripts/widenFanouts.py in.kicad_pcb [out.kicad_pcb] [--nets V3V3,RADIO_3V3] [--width 0.4]

The owner's rule (2026-10-17): a big plane via joined to its pad by a thin trace
is ugly and buys nothing. Every track of the listed nets that is part of a short
link (at most LINK_MM) from a pad to a via is widened to the target width, capped
at the pad's narrow side (a 0.3 mm MCU pin keeps a 0.3 mm trace). Each widening
that makes KiCad's DRC worse is undone; the rest are kept. Lists what changed.
"""
import argparse
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

import pcbnew

NM = 1e6
LINK_MM = 2.5


def drc_items(path):
    out = os.path.join(tempfile.gettempdir(), Path(path).name + ".widen.json")   # never beside the board
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--format", "json", "-o", out, str(path)],
                   capture_output=True)
    d = json.load(open(out))
    bad = [v for v in d["violations"] if v["type"] in ("clearance", "hole_clearance", "shorting_items",
                                                      "copper_edge_clearance", "solder_mask_bridge")]
    return len(d["unconnected_items"]), bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--nets", default="V3V3,RADIO_3V3")
    ap.add_argument("--width", type=float, default=0.4)
    a = ap.parse_args()
    src = Path(a.src)
    dst = Path(a.dst) if a.dst else src
    nets = set(a.nets.split(","))
    unc0, bad0 = drc_items(src)
    base = {(v["type"], tuple(i["description"] for i in v["items"])) for v in bad0}
    board = pcbnew.LoadBoard(str(src))
    tracks = [t for t in board.GetTracks() if t.GetNetname() in nets and t.GetClass() == "PCB_TRACK"]
    vias = [t for t in board.GetTracks() if t.GetNetname() in nets and t.GetClass() == "PCB_VIA"]
    pads = [pd for fp in board.GetFootprints() for pd in fp.Pads() if pd.GetNetname() in nets]
    at = lambda p, q: abs(p.x - q.x) < 2000 and abs(p.y - q.y) < 2000
    # chains of tracks from a pad to a via, at most LINK_MM long, no branching
    changed = {}
    for pd in pads:
        for start in [t for t in tracks if pd.HitTest(t.GetStart()) or pd.HitTest(t.GetEnd())]:
            chain, cur = [start], start.GetEnd() if pd.HitTest(start.GetStart()) else start.GetStart()
            length = start.GetLength() / NM
            while length <= LINK_MM and not any(at(cur, v.GetPosition()) for v in vias):
                nxt = [t for t in tracks if t not in chain and (at(t.GetStart(), cur) or at(t.GetEnd(), cur))]
                if len(nxt) != 1:
                    break
                t = nxt[0]
                chain.append(t)
                cur = t.GetEnd() if at(t.GetStart(), cur) else t.GetStart()
                length += t.GetLength() / NM
            if length > LINK_MM or not any(at(cur, v.GetPosition()) for v in vias):
                continue
            bb = pd.GetBoundingBox()
            cap = min(bb.GetWidth(), bb.GetHeight()) / NM
            w = min(a.width, max(cap, 0.15))
            for t in chain:
                if t.GetWidth() / NM < w - 1e-3:
                    changed[t.m_Uuid.AsString()] = (t, t.GetWidth(), int(w * NM))
    for t, old, new in changed.values():
        t.SetWidth(new)
    tmp = dst.with_name(dst.stem + "_widen.kicad_pcb")
    for ext in (".kicad_pro", ".kicad_dru"):
        if src.with_suffix(ext).exists():
            tmp.with_suffix(ext).write_bytes(src.with_suffix(ext).read_bytes())
    for _ in range(4):                      # undo the widenings DRC objects to, then re-check
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(str(tmp))
        unc, bad = drc_items(tmp)
        new_bad = [v for v in bad if (v["type"], tuple(i["description"] for i in v["items"])) not in base]
        if not new_bad and unc <= unc0:
            break
        uuids = {i["uuid"] for v in new_bad for i in v["items"]}
        undo = [u for u in changed if u in uuids] or list(changed)
        for u in undo:
            t, old, _ = changed.pop(u)
            t.SetWidth(old)
    else:
        raise SystemExit("not written: could not clear the DRC findings the widening caused")
    for t, old, new in changed.values():
        p = t.GetStart()
        print(f"  {t.GetNetname():10s} ({p.x / NM:.2f}, {p.y / NM:.2f}) {old / NM:.2f} -> {new / NM:.2f} mm")
    tmp.replace(dst)
    for ext in (".kicad_pro", ".kicad_dru"):
        tmp.with_suffix(ext).unlink(missing_ok=True)
    print(f"widened {len(changed)} track(s); wrote {dst}")


if __name__ == "__main__":
    main()
