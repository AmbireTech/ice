#!/usr/bin/env python3
"""Fix the mechanical routing defects routeDefects.py lists; changes nothing it cannot gate.

    $KICAD_PY scripts/tidyDefects.py IN.kicad_pcb OUT.kicad_pcb

Two kinds, one change at a time, each kept only if KiCad's DRC (every severity, counted by
type) gets no worse and nothing becomes unconnected:
  stub   a segment other copper of its net already covers, or of zero length: removed
  hop    two vias with a short run between them while the first layer has a way (the strict
         kind, not the "once the plane-net copper moves" kind): the run and both vias go, and
         the way is drawn on the first layer, string-pulled to as few octilinear segments as
         the board's clearances allow. A via that also carries another track on the hop's
         layer is a junction and stays; the hop is skipped only when both do
The owner judges the result by look; this only does what a tidy pass would.
Load IN beside its project (wallet.kicad_pro) or the saved OUT gets default rules.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import routeDefects as rd  # noqa: E402

M, MM = pcbnew.ToMM, pcbnew.FromMM


def drc_counts(path):
    tmp = os.path.join(tempfile.gettempdir(), "tidyDefects.drc.json")
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--severity-all", "--format", "json", "-o", tmp, path],
                   capture_output=True, check=True)
    d = json.load(open(tmp))
    counts = {}
    for v in d["violations"]:
        counts[v["type"]] = counts.get(v["type"], 0) + 1
    return counts, len(d["unconnected_items"])


def worse(before, after):
    (cb, ub), (ca, ua) = before, after
    if ua > ub:
        return f"unconnected {ub} -> {ua}"
    grew = [f"{k} {cb.get(k, 0)} -> {n}" for k, n in ca.items() if n > cb.get(k, 0)]
    return ", ".join(grew)


class Work:
    """One board, saved to a scratch file for every gate (one LoadBoard per process: no reloads)."""

    def __init__(self, src, out):
        self.dir = Path(tempfile.mkdtemp(prefix="tidyDefects."))
        for f in Path(src).parent.iterdir():
            if f.is_file() and f.suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl") or f.name == "fp-lib-table":
                shutil.copy(f, self.dir / f.name)
        self.path = str(self.dir / "wallet.kicad_pcb")
        shutil.copy(src, self.path)
        self.board = pcbnew.LoadBoard(self.path)
        self.out = out
        self.baseline = drc_counts(self.path)
        print("DRC baseline:", self.baseline[0] or "clean", "unconnected", self.baseline[1])

    def save(self, path=None):
        pcbnew.ZONE_FILLER(self.board).Fill(self.board.Zones())
        self.board.Save(path or self.path)
        if path:
            for f in self.dir.iterdir():   # the rules beside the board, so a reload reads them
                if f.suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl") and not (Path(path).parent / f.name).exists():
                    shutil.copy(f, Path(path).parent / f.name)

    def gate(self, what):
        self.save()
        now = drc_counts(self.path)
        bad = worse(self.baseline, now)
        if bad:
            print(f"  REVERTED ({bad}): {what}")
            return False
        self.baseline = now
        print(f"  kept: {what}")
        return True


def undo_stub(w, item, what):
    before = (item.GetStart(), item.GetEnd(), item.GetWidth(), item.GetLayer(), item.GetNetCode())
    w.board.Remove(item)
    rd._COPPER.clear()
    if not w.gate(f"stub removed: {what}"):
        t = pcbnew.PCB_TRACK(w.board)
        t.SetStart(before[0]); t.SetEnd(before[1]); t.SetWidth(before[2]); t.SetLayer(before[3]); t.SetNetCode(before[4])
        w.board.Add(t)
        rd._COPPER.clear()


def string_pull(w, pts, layer, width, netcode, skip):
    """Fewest straight pieces along the grid way that keep the board's clearances."""
    def seg(p, q):
        t = pcbnew.PCB_TRACK(w.board)
        t.SetStart(pcbnew.VECTOR2I(MM(p[0]), MM(p[1]))); t.SetEnd(pcbnew.VECTOR2I(MM(q[0]), MM(q[1])))
        t.SetWidth(width); t.SetLayer(layer); t.SetNetCode(netcode)
        return t
    out, i = [], 0
    while i < len(pts) - 1:
        for j in range(len(pts) - 1, i, -1):
            t = seg(pts[i], pts[j])
            if j == i + 1 or rd.fits(w.board, [t], layer, skip):
                out.append(t)
                i = j
                break
    return out


def undo_hop(w, h, what):
    a, c, chain, layer, other = h["a"], h["c"], h["chain"], h["layer"], h["other"]
    tracks = [t for t in w.board.GetTracks() if t.Type() != pcbnew.PCB_VIA_T]
    gone = []   # a via that carries nothing on the hop's layer but the run goes; a junction via stays
    for v in (a, c):
        on_layer = [t for t in tracks if t.GetLayer() == layer and (v.HitTest(t.GetStart()) or v.HitTest(t.GetEnd()))]
        if not any(all(t is not u for u in chain) for t in on_layer):
            gone.append(v)
    if not gone:
        print(f"  skipped (both vias carry more than the run): {what}")
        return
    way = rd.stay_path(w.board, chain, other, (a, c))
    if way is None:
        print(f"  skipped (no way found): {what}")
        return
    width = min(t.GetWidth() for t in chain)
    net = chain[0].GetNetCode()
    pieces = string_pull(w, way[1], other, width, net, (a, c))
    removed = []
    for item in list(chain) + gone:
        removed.append(item.Duplicate())
        w.board.Remove(item)
    for t in pieces:
        w.board.Add(t)
    rd._COPPER.clear()
    if not w.gate(f"hop undone with {len(pieces)} piece(s), {len(gone)} via(s) gone: {what}"):
        for t in pieces:
            w.board.Remove(t)
        for item in removed:
            w.board.Add(item)
        rd._COPPER.clear()


def phase_stubs(src, out):
    w = Work(src, out)
    items = rd.stub_items(w.board)      # collect first: after a Remove(), other handles can break
    for kind, pos, what, item in items:
        undo_stub(w, item, what)
    w.save(out)


def phase_hop(src, out, tried):
    """Undo the first unforced hop not in `tried`; print its key. One per process: a Remove() can
    break every other handle, so the next hop is found by a fresh load."""
    w = Work(src, out)
    for kind, pos, what, h in rd.hop_items(w.board, 6.0):
        key = what.split(";")[0]
        if key in tried:
            continue
        print("HOP", key)
        if h["moved"]:
            print(f"  left for the owner (plane-net copper in the way): {what}")
        else:
            undo_hop(w, h, what)
        w.save(out)
        return
    print("HOP none")


def main():
    if "--phase" in sys.argv:
        i = sys.argv.index("--phase")
        src, out, phase = sys.argv[1], sys.argv[2], sys.argv[i + 1]
        if phase == "stubs":
            phase_stubs(src, out)
        else:
            phase_hop(src, out, set(sys.argv[i + 2:]))
        return
    src, out = sys.argv[1], sys.argv[2]
    run = lambda *args: subprocess.run([sys.executable, __file__, *args], capture_output=True, text=True)
    r = run(src, out, "--phase", "stubs")
    print(r.stdout, end="")
    if r.returncode:
        sys.exit(r.stderr)
    tried = []
    while True:
        r = run(out, out, "--phase", "hop", *tried)
        print(r.stdout, end="")
        if r.returncode:
            sys.exit(r.stderr)
        keys = [l[4:] for l in r.stdout.splitlines() if l.startswith("HOP ")]
        if not keys or keys[0] == "none":
            break
        tried.append(keys[0])
    r = subprocess.run([sys.executable, HERE / "routeDefects.py", out], capture_output=True, text=True)
    print("\n".join(l for l in r.stdout.splitlines() if l.startswith(("stub", "hop", "drc", "unconnected", "total"))))


if __name__ == "__main__":
    main()
