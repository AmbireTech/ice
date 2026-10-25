#!/usr/bin/env python3
"""List routing defects for the owner to fix by hand; changes nothing.

    $KICAD_PY scripts/routeDefects.py [board.kicad_pcb] [--hop-mm 6] [--since COMMIT]

--since marks each hop, GND track and via-in-pad whose copper is not on the board at
COMMIT with "NEW" (what the last pass added; the rest was there before).

The owner (2026-10-25): "from now on I don't want you to try to fix it, I want you to
IDENTIFY these cases and I'll fix them manually." Each finding has a place (mm) and a
reason, sorted north to south:
  hop         a net drops through a via, runs at most --hop-mm on the other layer and comes
              straight back through a second via, while it continues on the first layer
              at both ends (a crossing that two vias paid for, or one whose reason left)
  gnd-track   a GND track that is not one of the ties stripGndTies.py keeps; "into pour"
              when an end touches neither a pad nor a via (it only reaches the fill)
  via-in-pad  a via under an SMD pad (thermal pads of chips excepted)
  dangling    a track end KiCad calls dangling
  unconnected what KiCad's DRC still reports unconnected
  craft       detours, wiggles, hairpins (hardware/carrier/.tools/audit_craft.py)
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
        for i, a in enumerate(vias):
            for c in vias[i + 1:]:
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
                    pa, pc = xy(a.GetPosition()), xy(c.GetPosition())
                    out.append(("hop", pa, f"{net}: two vias {pa} and {pc}, {length:.1f} mm on "
                                           f"{board.GetLayerName(layer)} between them"))
    return out


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
        padrefs = {e.split(".")[0] + "." + e.split(".")[1] for e in ends if "." in e}
        if padrefs and all(p in sg.KEEP for p in padrefs):
            continue
        tag = "into pour" if "nothing" in ends else "tie"
        out.append(("gnd-track", xy(t.GetStart()), f"GND {tag}: {M(t.GetLength()):.2f} mm from {xy(t.GetStart())} "
                                                   f"to {xy(t.GetEnd())} ({ends[0]} - {ends[1]})"))
    return out


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


def drc(board_path):
    out = []
    tmp = os.path.join(tempfile.gettempdir(), "routeDefects.drc.json")
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--format", "json", "-o", tmp, board_path],
                   capture_output=True)
    d = json.load(open(tmp))
    for v in d["violations"]:
        if v["type"] == "track_dangling":
            i = v["items"][0]
            out.append(("dangling", (round(i["pos"]["x"], 2), round(i["pos"]["y"], 2)), i["description"]))
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
    a = ap.parse_args()
    old = old_copper(a.since) if a.since else None
    board = pcbnew.LoadBoard(a.board)
    found = hops(board, a.hop_mm) + gnd_tracks(board) + vias_in_pads(board)
    found += drc(a.board) + craft(a.board)

    def new(kind, what):
        if old is None or kind not in ("hop", "gnd-track", "via-in-pad"):
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
