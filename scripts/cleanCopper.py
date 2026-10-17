#!/usr/bin/env python3
"""Remove copper that connects nothing, checked against KiCad's own connectivity.

    $KICAD_PY scripts/cleanCopper.py in.kicad_pcb [out.kicad_pcb]

What goes (the owner's cleanup of 2026-10-17, made a tool):
  - stale copper: a piece of a net's tracks and vias (joined where ends meet, an
    end lands on a track or a via) that touches none of the net's pads. For a plane
    net that is a plane via left behind where a part used to be, with or without a
    stub; for a signal, an orphan. GND is never touched: a pad-less GND via is
    stitching.
  - crumbs: a track KiCad's DRC calls dangling that is shorter than 0.2 mm.
Everything removed is listed. The board is re-checked by KiCad's DRC: if the
unconnected count or the copper violations get worse, nothing is written.
"""
import json
import math
import subprocess
import sys
from pathlib import Path

import pcbnew

NM = 1e6
CRUMB_MM = 0.2


def drc(path):
    out = str(path) + ".clean.json"
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--format", "json", "-o", out, str(path)],
                   capture_output=True)
    d = json.load(open(out))
    copper = sorted(v["type"] for v in d["violations"] if not v["type"].startswith(("silk", "lib_")))
    return len(d["unconnected_items"]), copper, d


def pieces(board):
    """Per net: the pad-less pieces of its tracks and vias (zones ignored)."""
    items = [t for t in board.GetTracks()]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def ends(t):
        return [t.GetPosition()] if t.GetClass() == "PCB_VIA" else [t.GetStart(), t.GetEnd()]

    def touches(t, p):
        if t.GetClass() == "PCB_VIA":
            return math.dist((p.x, p.y), (t.GetPosition().x, t.GetPosition().y)) <= t.GetWidth(pcbnew.F_Cu) / 2 + 1000
        return pcbnew.SEG(t.GetStart(), t.GetEnd()).Distance(p) <= t.GetWidth() / 2 + 1000

    by_net = {}
    for i, t in enumerate(items):
        by_net.setdefault(t.GetNetname(), []).append(i)
    for net, idx in by_net.items():
        for a in idx:
            for b in idx:
                if a < b and find(a) != find(b):
                    ta, tb = items[a], items[b]
                    same_layer = ta.GetClass() == "PCB_VIA" or tb.GetClass() == "PCB_VIA" or ta.GetLayer() == tb.GetLayer()
                    if same_layer and (any(touches(tb, p) for p in ends(ta)) or any(touches(ta, p) for p in ends(tb))):
                        parent[find(a)] = find(b)
    pads = {}
    for fp in board.GetFootprints():
        for pd in fp.Pads():
            pads.setdefault(pd.GetNetname(), []).append(pd)
    groups = {}
    for i, t in enumerate(items):
        groups.setdefault(find(i), []).append(t)
    stale = []
    for g in groups.values():
        net = g[0].GetNetname()
        if net == "GND" or not net:
            continue
        if not any(pd.HitTest(p) for t in g for p in ends(t) for pd in pads.get(net, [])):
            stale += g
    return stale


def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src
    unc0, cu0, d0 = drc(src)
    board = pcbnew.LoadBoard(str(src))
    stale = {t.m_Uuid.AsString(): t for t in pieces(board)}
    crumbs = {it["uuid"] for v in d0["violations"] if v["type"] == "track_dangling" for it in v["items"]}
    for t in board.GetTracks():
        u = t.m_Uuid.AsString()
        if u in crumbs and t.GetClass() != "PCB_VIA" and t.GetLength() / NM < CRUMB_MM:
            stale[u] = t
    for u, t in stale.items():
        where = t.GetPosition() if t.GetClass() == "PCB_VIA" else t.GetStart()
        print(f"  {t.GetNetname():12s} {t.GetClass()[4:]:5s} at ({where.x / NM:.2f}, {where.y / NM:.2f})"
              + ("" if t.GetClass() == "PCB_VIA" else f" {t.GetLength() / NM:.2f} mm"))
    for t in list(stale.values()):
        board.Remove(t)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    tmp = dst.with_name(dst.stem + "_clean.kicad_pcb")
    for ext in (".kicad_pro", ".kicad_dru"):
        if src.with_suffix(ext).exists() and not tmp.with_suffix(ext).exists():
            tmp.with_suffix(ext).write_bytes(src.with_suffix(ext).read_bytes())
    board.Save(str(tmp))
    unc1, cu1, _ = drc(tmp)
    worse = [v for v in set(cu1) if cu1.count(v) > cu0.count(v)]
    if unc1 > unc0 or worse:
        raise SystemExit(f"not written: unconnected {unc0} -> {unc1}, new copper violations {worse}")
    tmp.replace(dst)
    for ext in (".kicad_pro", ".kicad_dru"):
        if tmp.with_suffix(ext).exists() and tmp.with_suffix(ext) != src.with_suffix(ext):
            tmp.with_suffix(ext).unlink()
    print(f"removed {len(stale)} item(s); unconnected {unc0} -> {unc1}; wrote {dst}")


if __name__ == "__main__":
    main()
