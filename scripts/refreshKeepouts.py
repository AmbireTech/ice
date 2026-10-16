#!/usr/bin/env python3
"""Redraw the keep-out rule areas and the UV-panel guide on a hand-worked board.

    $KICAD_PY scripts/refreshKeepouts.py [board.kicad_pcb]

newBoard.py derives the keep-outs from the placed connectors (the camera flex and
its fan-in follow J_CAM) and from its constants (UV_PANEL). After a connector
moves or a constant changes, this replaces every "no parts: ..." and "no
tracks/vias: ..." rule area with newBoard.keepouts() and moves the Dwgs.User
"UV ARTWORK" rectangle and label (which art/build_uv.py reads) to UV_PANEL.
Placement and copper are not touched. Close the board in KiCad first.
"""
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import newBoard as nb  # noqa: E402


def refresh(board, log=print):
    old = [z for z in board.Zones() if z.GetIsRuleArea()
           and z.GetZoneName().startswith(("no parts:", "no tracks/vias:"))]
    for z in old:
        board.Remove(z)
    nb.keepouts(board)
    log(f"rule areas: {len(old)} replaced by {sum(1 for z in board.Zones() if z.GetIsRuleArea())}")
    # the UV ARTWORK guide: the Dwgs.User rectangle whose label says so
    label = next((d for d in board.GetDrawings() if d.GetClass() == "PCB_TEXT" and d.GetText() == "UV ARTWORK"),
                 None)
    if label is None:
        raise SystemExit("no Dwgs.User 'UV ARTWORK' label on the board")
    lp = label.GetPosition()
    # the innermost Dwgs.User rectangle around the label (the display-panel outline encloses it too)
    rects = [d for d in board.GetDrawings() if d.GetClass() == "PCB_SHAPE" and d.GetLayer() == pcbnew.Dwgs_User
             and d.GetShape() == pcbnew.SHAPE_T_RECT and d.GetBoundingBox().Contains(lp)]
    if not rects:
        raise SystemExit("no Dwgs.User rectangle around the UV ARTWORK label")
    rects.sort(key=lambda d: d.GetBoundingBox().GetArea())
    x0, y0, x1, y1 = nb.UV_PANEL
    rects[0].SetStart(pcbnew.VECTOR2I_MM(x0, y0))
    rects[0].SetEnd(pcbnew.VECTOR2I_MM(x1, y1))
    label.SetPosition(pcbnew.VECTOR2I_MM((x0 + x1) / 2, y1 - 0.9))
    log(f"UV ARTWORK panel -> {nb.UV_PANEL}")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else str(nb.syncBoard.BOARD)
    board = pcbnew.LoadBoard(path)
    refresh(board)
    board.Save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
