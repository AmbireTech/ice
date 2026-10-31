#!/usr/bin/env python3
"""Redraw the display-tail cutout from newBoard.py's constants (newBoard.slot_outline()).

    $KICAD_PY scripts/resizeSlots.py BOARD [--apply]

The slots are the board's only inner Edge.Cuts outlines: this removes the Edge.Cuts
items lying inside the slots' band and draws the cutout again. Dry run by default: it
reports the old and new extents and the DRC before/after on a copy; --apply writes BOARD
only if DRC is no worse. Close the board in KiCad first.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import newBoard as nb  # noqa: E402

M = pcbnew.ToMM


def drc(path):
    out = Path(path).with_suffix(".drc.json")
    subprocess.run(["kicad-cli", "pcb", "drc", "--refill-zones", "--format", "json", "--severity-error",
                    "--severity-warning", "-o", str(out), str(path)], capture_output=True, text=True)
    if not out.exists():
        raise SystemExit(f"kicad-cli drc gave no report for {path}")
    d = json.loads(out.read_text())
    return len(d["violations"]), len(d["unconnected_items"])


def main():
    path, apply_ = Path(sys.argv[1]), "--apply" in sys.argv
    b = pcbnew.LoadBoard(str(path))
    band = (nb.SLOT_Y0 - 1.0, nb.SLOT_Y1 + 0.1)   # wide enough for the old two-slot cutout too
    old = [d for d in b.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts
           and band[0] <= M(d.GetBoundingBox().GetTop()) and M(d.GetBoundingBox().GetBottom()) <= band[1]]
    if not old or any(M(d.GetBoundingBox().GetLeft()) < 1 or M(d.GetBoundingBox().GetRight()) > nb.W - 1 for d in old):
        raise SystemExit(f"expected only the slots' Edge.Cuts items in y {band}, found {len(old)}")
    xs = sorted((M(d.GetBoundingBox().GetLeft()), M(d.GetBoundingBox().GetRight()),
                 M(d.GetBoundingBox().GetTop()), M(d.GetBoundingBox().GetBottom())) for d in old)
    print(f"old slot items ({len(old)}):", [f"x {a:.2f}..{c:.2f} y {t:.2f}..{u:.2f}" for a, c, t, u in xs])
    for d in old:
        b.Remove(d)
    nb.slot_outline(b)
    for name, (x0, y0, x1, y1) in nb.slot_boxes().items():
        print(f"new {name} slot (margin {nb.SLOT_MARGIN}): x {x0:.2f}..{x1:.2f} y {y0:.2f}..{y1:.2f}")
    with tempfile.TemporaryDirectory() as tmp:
        for f in path.parent.glob("*.kicad_pro"):
            shutil.copy(f, tmp)
        for f in path.parent.glob("*.kicad_dru"):
            shutil.copy(f, tmp)
        for f in ("fp-lib-table",):
            if (path.parent / f).exists():
                shutil.copy(path.parent / f, tmp)
        for lib in path.parent.glob("*.pretty"):
            (Path(tmp) / lib.name).symlink_to(lib.resolve())
        before = Path(tmp) / "before" / path.name
        before.parent.mkdir()
        for f in Path(tmp).iterdir():
            if f.is_file() or f.is_symlink():
                (before.parent / f.name).symlink_to(f.resolve()) if f.is_symlink() else shutil.copy(f, before.parent)
        shutil.copy(path, before)
        after = Path(tmp) / path.name
        b.Save(str(after))
        v0, u0 = drc(before)
        v1, u1 = drc(after)
        print(f"DRC: {v0} violations / {u0} unconnected before, {v1} / {u1} after")
        if v1 > v0 or u1 > u0:
            raise SystemExit("not written: DRC got worse")
        if apply_:
            shutil.copy(after, path)
            print(f"wrote {path}")
        else:
            print("dry run: --apply writes it")


if __name__ == "__main__":
    main()
