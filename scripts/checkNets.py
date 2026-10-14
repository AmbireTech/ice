#!/usr/bin/env python3
"""Netlist sanity gate: every named net reaches at least two pads.

    python3 scripts/checkNets.py

A net with a single pad is a typo'd net name or a forgotten connection (the
MCU pin is wired to CAM_D5 but the connector to CAM_DS). NC_* nets are the
deliberate single-pad ones. Also fails on tscircuit's auto-named nets, which
mean a trace went pin-to-pin without a <net> name the board can show.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import netlist  # noqa: E402

parts = netlist.read(Path(__file__).parent.parent / "board/netlist.circuit.json")
pads = defaultdict(list)
for p in parts.values():
    for pad, net in p.pads.items():
        pads[net].append(f"{p.ref}.{pad}")
errors = []
for net, where in sorted(pads.items()):
    if net.startswith("Net-"):
        errors.append(f"{net}: unnamed net on {where}")
    elif len(where) < 2 and not net.startswith("NC"):
        errors.append(f"{net}: only one pad ({where[0]})")
    elif len(where) > 1 and net.startswith("NC_"):
        errors.append(f"{net}: marked no-connect but joins {where}")
for e in errors:
    print("ERROR", e)
print(f"{len(pads)} nets, {sum(map(len, pads.values()))} pad connections: {'FAIL' if errors else 'ok'}")
sys.exit(1 if errors else 0)
