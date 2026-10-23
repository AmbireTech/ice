#!/usr/bin/env python3
"""Redraw the two display-tail slots from newBoard.py's constants (SLOT_MARGIN, SLOT_Y0/1).

    $KICAD_PY scripts/resizeSlots.py BOARD [--apply]

The slots are the board's only inner Edge.Cuts outlines: this removes the Edge.Cuts
items lying inside the slots' band and draws the slots again. Dry run by default: it
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
    band = (nb.SLOT_Y0 - 0.1, nb.SLOT_Y1 + 0.1)
    old = [d for d in b.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts
           and band[0] <= M(d.GetBoundingBox().GetTop()) and M(d.GetBoundingBox().GetBottom()) <= band[1]]
    if len(old) != 12:    # two stadiums: 2 lines + 4 quarter arcs each (newBoard.rounded_rect)
        raise SystemExit(f"expected the slots' 12 Edge.Cuts items in y {band}, found {len(old)}")
    xs = sorted((M(d.GetBoundingBox().GetLeft()), M(d.GetBoundingBox().GetRight())) for d in old)
    print("old slot items x:", [f"{a:.2f}..{c:.2f}" for a, c in xs])
    for d in old:
        b.Remove(d)
    nb.slot(b, nb.TFT_TAIL[0] - nb.SLOT_MARGIN, nb.TFT_TAIL[0] + nb.TFT_TAIL[1] + nb.SLOT_MARGIN)
    nb.slot(b, nb.CTP_TAIL[0] - nb.SLOT_MARGIN, nb.CTP_TAIL[0] + nb.CTP_TAIL[1] + nb.SLOT_MARGIN)
    print(f"new slots (margin {nb.SLOT_MARGIN}): TFT x {nb.TFT_TAIL[0] - nb.SLOT_MARGIN:.2f}..{sum(nb.TFT_TAIL) + nb.SLOT_MARGIN:.2f}, "
          f"touch x {nb.CTP_TAIL[0] - nb.SLOT_MARGIN:.2f}..{sum(nb.CTP_TAIL) + nb.SLOT_MARGIN:.2f}")
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
