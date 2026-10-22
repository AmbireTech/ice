#!/usr/bin/env python3
"""Print the Ambire flame across the back silkscreen, in ordinary (single-ink) silkscreen.

    $KICAD_PY scripts/silkFlame.py             # dry run: writes a copy, runs DRC on it, reports
    $KICAD_PY scripts/silkFlame.py --apply     # installs the copy over board/wallet.kicad_pcb (close KiCad first)
    SILKFLAME_BOARD=copy.kicad_pcb ...         # work on a copy instead (--apply then writes the copy)

The flame (art/flame.py, the same geometry the concept page draws) spans the back: one
ink, with a hairline of bare board between facets so the faceting still shows. It is cut
back wherever silkscreen cannot or should not go:
  pads and drilled holes (JLC_PAD_CLEAR), the board edge and slots (EDGE_CLEAR),
  the human-readable labels (LABEL_CLEAR: the label stays an island of clean board),
  footprint silkscreen (outlines, pin-1 marks).
The wordmark texts are removed: the back says "Ambire" nowhere. The flame lives in the
group FLAME, which a rerun replaces, so the script is safe to run again after edits.
"""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent.parent
BOARD = Path(os.environ.get("SILKFLAME_BOARD", HERE / "board/wallet.kicad_pcb"))   # a copy for trials
sys.path.insert(0, str(HERE / "art"))
import flame  # noqa: E402  (art/flame.py)

REMOVE_TEXTS = {"AMBIRE", "Ambire signer · proto 0"}
GROUP = "FLAME"
JLC_PAD_CLEAR = 0.15     # silkscreen to exposed copper
EDGE_CLEAR = 0.3
LABEL_CLEAR = 0.5
FP_SILK_CLEAR = 0.15
MAX_ERR = pcbnew.FromMM(0.005)
MM = pcbnew.FromMM


def poly(points_mm) -> pcbnew.SHAPE_POLY_SET:
    s = pcbnew.SHAPE_POLY_SET()
    s.NewOutline()
    for x, y in points_mm:
        s.Append(MM(x), MM(y))
    return s


def grown(s: pcbnew.SHAPE_POLY_SET, mm: float) -> pcbnew.SHAPE_POLY_SET:
    out = s.CloneDropTriangulation()
    out.Inflate(MM(mm), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, MAX_ERR)
    return out


def flame_ink(board_w, board_h) -> pcbnew.SHAPE_POLY_SET:
    """Union of the facets, minus a GAP-wide band along every facet outline."""
    facets = flame.facets()
    fbox = flame.box(facets)
    at = flame.placement(board_w, board_h, fbox)
    ink, bands = pcbnew.SHAPE_POLY_SET(), pcbnew.SHAPE_POLY_SET()
    for f in facets:
        for p in f:
            shape = poly([flame.to_board(pt, fbox, at) for pt in p])
            shape.Simplify()
            ink.BooleanAdd(shape)
            outer, inner = grown(shape, flame.GAP / 2), grown(shape, -flame.GAP / 2)
            outer.BooleanSubtract(inner)
            bands.BooleanAdd(outer)
    ink.BooleanSubtract(bands)
    return ink, at


def keepouts(board, drawings) -> pcbnew.SHAPE_POLY_SET:
    k = pcbnew.SHAPE_POLY_SET()
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.IsOnLayer(pcbnew.B_Cu) or pad.IsOnLayer(pcbnew.B_Mask):
                pad.TransformShapeToPolygon(k, pcbnew.B_Cu, MM(JLC_PAD_CLEAR), MAX_ERR, pcbnew.ERROR_OUTSIDE)
            if pad.HasHole():
                pad.TransformHoleToPolygon(k, MM(JLC_PAD_CLEAR), MAX_ERR, pcbnew.ERROR_OUTSIDE)
        for g in fp.GraphicalItems():
            if g.GetLayer() == pcbnew.B_SilkS and isinstance(g, pcbnew.PCB_SHAPE):
                g.TransformShapeToPolygon(k, pcbnew.B_SilkS, MM(FP_SILK_CLEAR), MAX_ERR, pcbnew.ERROR_OUTSIDE)
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:   # tented, but no ink over the drilled hole
            k.AddOutline(_circle(t.GetPosition(), t.GetDrillValue() // 2 + MM(JLC_PAD_CLEAR)))
    for d in drawings:
        if d.GetLayer() != pcbnew.B_SilkS:
            continue
        if isinstance(d, pcbnew.PCB_TEXT):
            if d.GetText() in REMOVE_TEXTS:
                continue
            bb = d.GetBoundingBox()
            m = MM(LABEL_CLEAR)
            k.BooleanAdd(_rect(bb.GetX() - m, bb.GetY() - m, bb.GetRight() + m, bb.GetBottom() + m))
        elif isinstance(d, pcbnew.PCB_SHAPE):   # loose silkscreen lines (the flame group is gone by now)
            d.TransformShapeToPolygon(k, pcbnew.B_SilkS, MM(FP_SILK_CLEAR), MAX_ERR, pcbnew.ERROR_OUTSIDE)
    k.Simplify()
    return k


def _rect(x0, y0, x1, y1) -> pcbnew.SHAPE_POLY_SET:
    s = pcbnew.SHAPE_POLY_SET()
    s.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        s.Append(x, y)
    return s


def _circle(c, r, n=24) -> pcbnew.SHAPE_LINE_CHAIN:
    chain = pcbnew.SHAPE_LINE_CHAIN()
    for i in range(n):
        a = 2 * math.pi * i / n
        chain.Append(int(c.x + r * math.cos(a)), int(c.y + r * math.sin(a)))
    chain.SetClosed(True)
    return chain


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def drc(board_path: Path) -> tuple:
    """(violations, unconnected) from kicad-cli, errors and warnings."""
    report = board_path.parent / (board_path.stem + ".drc.json")
    run = subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-error", "--severity-warning",
                          "-o", str(report), str(board_path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not report.exists():
        raise SystemExit(f"kicad-cli drc failed:\n{run.stdout}\n{run.stderr}")
    data = json.loads(report.read_text(encoding="utf-8"))
    return data.get("violations", []), data.get("unconnected_items", [])


def drc_of_original() -> tuple:
    with tempfile.TemporaryDirectory() as tmp:
        return drc(stage_copy(Path(tmp), BOARD.read_bytes()))


def stage_copy(tmp: Path, board_bytes: bytes) -> Path:
    """The board and its project/rule files in a scratch folder, so DRC never touches the original."""
    for src in (BOARD.with_suffix(".kicad_pro"), BOARD.with_suffix(".kicad_dru"), BOARD.parent / "fp-lib-table"):
        if src.exists():
            shutil.copy(src, tmp / src.name)
    for lib in BOARD.parent.glob("*.pretty"):   # fp-lib-table points at ${KIPRJMOD}/wallet.pretty
        (tmp / lib.name).symlink_to(lib)
    copy = tmp / BOARD.name
    copy.write_bytes(board_bytes)
    return copy


def main():
    apply = "--apply" in sys.argv
    before = md5(BOARD)
    board = pcbnew.LoadBoard(str(BOARD))
    outline = pcbnew.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(outline, False):
        raise SystemExit("board outline is not closed")
    bb = board.GetBoardEdgesBoundingBox()
    w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())

    # pcbnew (10.0) hands back a broken Drawings() wrapper after any Remove(), and a
    # removed item must not be touched again: sort the drawings out first, remove after.
    flame_items, removed, kept = [], [], []
    for g in list(board.Groups()):
        if g.GetName() == GROUP:
            flame_items += [g] + list(g.GetItems())
    flame_ids = {i.m_Uuid.AsString() for i in flame_items}
    for d in board.GetDrawings():
        if isinstance(d, pcbnew.PCB_TEXT) and d.GetText() in REMOVE_TEXTS:
            removed.append(d)
        elif d.m_Uuid.AsString() not in flame_ids:
            kept.append(d)
    # a rerun replaces the flame; the wordmark texts go
    for item in flame_items + removed:
        board.Remove(item)
    removed = [d.GetText() for d in removed]

    ink, at = flame_ink(round(w, 3), round(h, 3))
    area_full = ink.Area()
    inside = outline.CloneDropTriangulation()
    inside.Inflate(-MM(EDGE_CLEAR), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, MAX_ERR)
    ink.BooleanIntersection(inside)
    ink.BooleanSubtract(keepouts(board, kept))
    ink.Fracture()

    group = pcbnew.PCB_GROUP(board)
    group.SetName(GROUP)
    board.Add(group)
    n = 0
    for i in range(ink.OutlineCount()):
        o = ink.Outline(i)
        if abs(o.Area()) < MM(0.05) * MM(0.05):   # crumbs a printer cannot resolve
            continue
        shape = pcbnew.PCB_SHAPE(board)
        shape.SetShape(pcbnew.SHAPE_T_POLY)
        sp = pcbnew.SHAPE_POLY_SET()
        sp.AddOutline(o)
        shape.SetPolyShape(sp)
        shape.SetFilled(True)
        shape.SetWidth(0)
        shape.SetLayer(pcbnew.B_SilkS)
        board.Add(shape)
        group.AddItem(shape)
        n += 1
    printed = sum(abs(ink.Outline(i).Area()) for i in range(ink.OutlineCount()))
    print(f"flame {at[2] - at[0]:.1f} x {at[3] - at[1]:.1f} mm at x {at[0]:.2f}..{at[2]:.2f}, y {at[1]:.2f}..{at[3]:.2f}: "
          f"{n} silkscreen polygons, {printed / 1e12:.0f} of {area_full / 1e12:.0f} mm2 of ink after clearances")
    print(f"removed texts: {', '.join(removed) or 'none (already gone)'}")

    base_viol, base_unconn = drc_of_original()
    with tempfile.TemporaryDirectory() as tmp:
        copy = stage_copy(Path(tmp), b"")
        board.Save(str(copy))
        viol, unconn = drc(copy)
        print(f"DRC: {len(base_viol)} violations / {len(base_unconn)} unconnected before, "
              f"{len(viol)} / {len(unconn)} with the flame")
        key = lambda v: (v.get("type"), v.get("description"))
        new = [v for v in viol if key(v) not in {key(b) for b in base_viol}] if len(viol) > len(base_viol) else []
        for v in (new or viol)[:12]:
            print("  ", v.get("type"), "-", v.get("description"), [i.get("description") for i in v.get("items", [])][:2])
        if len(viol) > len(base_viol) or len(unconn) > len(base_unconn):
            raise SystemExit("not installed: the flame added DRC findings")
        if not apply:
            keep = os.environ.get("SILKFLAME_KEEP")
            if keep:
                shutil.copy(copy, keep)
            print(f"dry run: {BOARD} untouched (--apply installs it)" + (f"; copy kept at {keep}" if keep else ""))
            return
        if md5(BOARD) != before:
            raise SystemExit(f"{BOARD} changed while this ran (KiCad open?): not installed")
        shutil.copy(copy, BOARD)
        print(f"installed {BOARD}")


if __name__ == "__main__":
    main()
