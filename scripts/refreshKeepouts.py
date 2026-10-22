#!/usr/bin/env python3
"""Redraw the keep-out rule areas on a hand-worked board.

    $KICAD_PY scripts/refreshKeepouts.py [board.kicad_pcb]

newBoard.py derives the keep-outs from the placed parts (the camera flex and its
fan-in follow J_CAM, the antenna keep-out follows the radio module) and from its
constants. After a part moves or a constant changes, this replaces every "no
parts: ...", "no tracks/vias: ..." and the "E73 antenna" rule area with
newBoard.keepouts(). Placement and copper are not touched. Close the board in
KiCad first.
"""
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import newBoard as nb  # noqa: E402


def refresh(board, log=print):
    old = [z for z in board.Zones() if z.GetIsRuleArea()
           and (z.GetZoneName().startswith(("no parts:", "no tracks/vias:")) or z.GetZoneName() == "E73 antenna")]
    for z in old:
        board.Remove(z)
    nb.keepouts(board)
    log(f"rule areas: {len(old)} replaced by {sum(1 for z in board.Zones() if z.GetIsRuleArea())}")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else str(nb.syncBoard.BOARD)
    board = pcbnew.LoadBoard(path)
    refresh(board)
    board.Save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
