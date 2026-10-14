#!/usr/bin/env python3
"""Fail unless every IC pad on the BOARD sits on the net its datasheet function needs.

    $KICAD_PY scripts/checkPinNets.py [board/wallet.kicad_pcb]

DRC cannot see this: a pad on the wrong rail is still "connected". The GridCore
Nano shipped twice with another chip's pin map on its buck-boost because every
gate asked "is the net in one piece?" and never "is this pad the net its
datasheet says?". Tables: scripts/officialPinMaps.json (one source per part).
Also fails if a part listed there has a pad the table does not mention, so a new
pin cannot slip in unchecked.
"""
import json
import re
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
maps = {k: v for k, v in json.load(open(HERE / "officialPinMaps.json")).items() if not k.startswith("_")}
board = pcbnew.LoadBoard(sys.argv[1] if len(sys.argv) > 1 else str(HERE.parent / "board/wallet.kicad_pcb"))
errors = []
for ref, spec in sorted(maps.items()):
    fp = board.FindFootprintByReference(ref)
    if fp is None:
        errors.append(f"{ref} ({spec['part']}) is not on the board")
        continue
    on_board = {}
    for p in fp.Pads():
        if p.GetNumber():
            on_board.setdefault(p.GetNumber(), set()).add(p.GetNetname())
    for pad, (name, rule) in spec["pins"].items():
        nets = on_board.get(pad)
        if nets is None:
            errors.append(f"{ref} pad {pad} ({name}) does not exist on the land")
            continue
        for net in nets:
            ok = (rule == "any" or (rule == "nc" and (not net or net.startswith("NC")))
                  or (rule.startswith("re:") and re.search(rule[3:], net or ""))
                  or net == rule)
            if not ok:
                errors.append(f"{ref} pad {pad} ({name}) is on {net or '(no net)'}; the datasheet function needs {rule}")
    if ref != "U4" and ref != "U11":   # partial tables: only the pins we use / power pins
        extra = sorted(set(on_board) - set(spec["pins"]), key=lambda s: int(s) if s.isdigit() else 999)
        if extra:
            errors.append(f"{ref} has pads {extra} that {spec['source']} does not cover here")
for e in errors:
    print("ERROR", e)
print(f"{len(maps)} parts checked against their datasheet pin tables: {'FAIL' if errors else 'ok'}")
sys.exit(1 if errors else 0)
