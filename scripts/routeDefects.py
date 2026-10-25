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
  via-near-pad a via whose ring comes within --near-mm of an SMD pad's edge without
              touching it (any net): legal, but crowds the pad
  jump        a track passing between the two pads of a two-pad part on the same face
  acute       two segments of a net meeting at less than 90 degrees, nothing else there
              (a V-turn: CELL_SENSE leaving an MCU pin and doubling back, 2026-10-25)
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


def pad_shapes(board):
    from shapely.geometry import Polygon
    out = []
    for fp in board.GetFootprints():
        if fp.GetReference() in ("BT_N", "BT_P"):
            continue
        for p in fp.Pads():
            if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD or not p.GetNumber():
                continue
            layer = pcbnew.B_Cu if fp.IsFlipped() else pcbnew.F_Cu
            poly = p.GetEffectivePolygon(layer)
            if poly.OutlineCount() == 0:
                continue
            o = poly.Outline(0)
            pts = [(M(o.CPoint(i).x), M(o.CPoint(i).y)) for i in range(o.PointCount())]
            if len(pts) >= 3:
                out.append((f"{fp.GetReference()}.{p.GetNumber()}", p.GetNetname(), layer, Polygon(pts), fp))
    return out


def vias_near_pads(board, shapes, near):
    from shapely.geometry import Point
    out = []
    for v in board.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T:
            continue
        c = Point(M(v.GetPosition().x), M(v.GetPosition().y)).buffer(M(v.GetWidth(pcbnew.F_Cu)) / 2)
        for name, net, layer, poly, fp in shapes:
            d = poly.distance(c)
            if 0 < d < near:
                out.append(("via-near-pad", xy(v.GetPosition()),
                            f"{v.GetNetname()} via at {xy(v.GetPosition())} {d:.2f} mm from {name} [{net}]"))
    return out


def jumps(board, shapes):
    from shapely.geometry import LineString, MultiPoint
    out = []
    two = {}
    for name, net, layer, poly, fp in shapes:
        if len(list(fp.Pads())) == 2:
            two.setdefault(fp.GetReference(), []).append((net, layer, poly))
    gaps = []
    for ref, pads in two.items():
        if len(pads) != 2:
            continue
        (n1, l1, a), (n2, l2, b) = pads
        gap = MultiPoint(list(a.exterior.coords) + list(b.exterior.coords)).convex_hull.difference(a).difference(b)
        gaps.append((ref, {n1, n2}, l1, gap))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        line = LineString([(M(t.GetStart().x), M(t.GetStart().y)), (M(t.GetEnd().x), M(t.GetEnd().y))])
        for ref, nets, layer, gap in gaps:
            if t.GetLayer() == layer and t.GetNetname() not in nets and line.intersects(gap) \
                    and line.intersection(gap).length > 0.2:
                out.append(("jump", xy(t.GetStart()), f"{t.GetNetname()} passes between the pads of {ref}"))
    return out


def acute_turns(board):
    out = []
    ends = {}
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        for p, q in ((t.GetStart(), t.GetEnd()), (t.GetEnd(), t.GetStart())):
            ends.setdefault((t.GetNetname(), t.GetLayer(), xy(p)), []).append((xy(p), xy(q), t))
    vias = {xy(v.GetPosition()) for v in board.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
    for (net, layer, at), segs in ends.items():
        if len(segs) != 2 or at in vias:
            continue
        (p, q1, a), (_, q2, b) = segs
        if M(a.GetLength()) < 0.05 or M(b.GetLength()) < 0.05:
            continue
        v1 = (q1[0] - p[0], q1[1] - p[1])
        v2 = (q2[0] - p[0], q2[1] - p[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        if angle < 89.0:
            out.append(("acute", at, f"{net} turns back at {angle:.0f} deg on {board.GetLayerName(layer)}"))
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
    ap.add_argument("--near-mm", type=float, default=0.25)
    a = ap.parse_args()
    old = old_copper(a.since) if a.since else None
    board = pcbnew.LoadBoard(a.board)
    shapes = pad_shapes(board)
    found = hops(board, a.hop_mm) + gnd_tracks(board) + vias_in_pads(board)
    found += vias_near_pads(board, shapes, a.near_mm) + jumps(board, shapes) + acute_turns(board)
    found += drc(a.board) + craft(a.board)

    def new(kind, what):
        if old is None or kind not in ("hop", "gnd-track", "via-in-pad", "via-near-pad"):
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
