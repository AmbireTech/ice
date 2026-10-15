#!/usr/bin/env python3
"""Fail unless every MCU pin assignment is a function ST lists for that pin.

    python3 scripts/checkMcuAf.py

src/parts/mcuAssign.json says which net sits on which STM32U585 pin and which
peripheral signal firmware will mux onto it. A DCMI data line on a pin without
that alternate function is invisible to every copper gate and only shows up when
the camera stays dark on the bench, so this checks each row against ST's pin
database (scripts/ref/STM32U585VITxQ.xml, the file CubeMX reads).

It also fails when the exported netlist (board/netlist.circuit.json, which the
board is synced from) puts a net on a different pin than the table: an edit to
the table that was never exported leaves the board on the old pins while every
other gate still passes (2026-10-14: the radio UART sat on USART3 on the board
for a day while this table said USART1).
"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
root = ET.parse(HERE / "ref/STM32U585VITxQ.xml").getroot()
tag = lambda e: e.tag.split("}")[1]
pins, position = {}, {}
for p in (e for e in root if tag(e) == "Pin"):
    if p.attrib["Type"] != "I/O":
        continue
    port = p.attrib["Name"].split("-")[0].split(" ")[0]
    pins[port] = {s.attrib["Name"] for s in p if tag(s) == "Signal"}
    position[port] = p.attrib["Position"]

assign = {k: v for k, v in json.load(open(HERE.parent / "src/parts/mcuAssign.json")).items() if not k.startswith("_")}
errors = []
nets = {}
for port, (net, signal) in assign.items():
    if port not in pins:
        errors.append(f"{port}: not an I/O pin of the LQFP100 package")
        continue
    if signal not in ("GPIO", "ANALOG") and signal not in pins[port]:
        errors.append(f"{port} ({net}): ST lists no {signal} on this pin; it has {sorted(pins[port])}")
    if net in nets:
        errors.append(f"{net} is on both {nets[net]} and {port}")
    nets[net] = port
# A peripheral signal used twice means two pins fight over one function.
sigs = [s for _, s in assign.values() if s not in ("GPIO", "ANALOG")]
for s in sorted({s for s in sigs if sigs.count(s) > 1}):
    errors.append(f"{s} assigned to more than one pin")
# the exported netlist must carry the same nets on the same pins
sys.path.insert(0, str(HERE))
import netlist  # noqa: E402
exported = netlist.read(HERE.parent / "board/netlist.circuit.json")["U4"].pads
want = {position[port]: net for port, (net, _) in assign.items() if port in position}
signal_nets = set(want.values())
for pad in sorted(set(want) | {k for k, n in exported.items() if n in signal_nets}, key=int):
    if want.get(pad) != exported.get(pad):
        errors.append(f"U4 pin {pad}: table says {want.get(pad) or '(nothing)'}, exported netlist has "
                      f"{exported.get(pad) or '(nothing)'}; re-export the netlist (README, Board flow)")
for e in errors:
    print("ERROR", e)
print(f"{len(assign)} assignments checked against ST's pin database and the exported netlist: "
      f"{'FAIL' if errors else 'ok'}")
sys.exit(1 if errors else 0)
