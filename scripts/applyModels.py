#!/usr/bin/env python3
"""Put the 3D models from board/models.json on the board's footprints. Touches nothing else.

    $KICAD_PY scripts/applyModels.py              # dry run: lists what would change
    $KICAD_PY scripts/applyModels.py --apply      # writes board/wallet.kicad_pcb (close it in KiCad first)

Only each listed footprint's model list is replaced; placement, copper, nets and
fields are left exactly as they are, so it is safe on a board that is being routed.
"""
import json
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent.parent
BOARD = HERE / "board/wallet.kicad_pcb"
MODELS = HERE / "board/models.json"


def table():
    return {k: v for k, v in json.load(open(MODELS)).items() if not k.startswith("_")}


def apply(fp, spec):
    """Replace fp's models with spec. Returns True if anything changed."""
    want = (spec["path"], tuple(spec["offset"]), tuple(spec["rotate"]))
    have = [(m.m_Filename, (m.m_Offset.x, m.m_Offset.y, m.m_Offset.z), (m.m_Rotation.x, m.m_Rotation.y, m.m_Rotation.z))
            for m in fp.Models()]
    if have == [want]:
        return False
    fp.Models().clear()
    m = pcbnew.FP_3DMODEL()
    m.m_Filename = spec["path"]
    m.m_Offset = pcbnew.VECTOR3D(*spec["offset"])
    m.m_Rotation = pcbnew.VECTOR3D(*spec["rotate"])
    m.m_Scale = pcbnew.VECTOR3D(1, 1, 1)
    fp.Models().append(m)
    return True


def main():
    board = pcbnew.LoadBoard(str(BOARD))
    models = table()
    changed = [fp.GetReference() for fp in board.GetFootprints()
               if fp.GetFPIDAsString() in models and apply(fp, models[fp.GetFPIDAsString()])]
    print(f"{len(changed)} footprint(s): {', '.join(sorted(changed)) or '-'}")
    if changed and "--apply" in sys.argv:
        pcbnew.SaveBoard(str(BOARD), board)
        print(f"wrote {BOARD}")
    elif changed:
        print("dry run; --apply writes them")


if __name__ == "__main__":
    main()
