#!/usr/bin/env python3
"""Print JLC's own stock for every JLC part number in the netlist.

    python3 scripts/jlcStock.py [board/netlist.circuit.json]

JLC's cart API is the stock authority (jlcsearch.tscircuit.com is a third-party
mirror that has disagreed with it 40x). Flags parts with fewer than 50 in stock
or whose JLC record does not exist.
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import netlist  # noqa: E402

API = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"


def lookup(code):
    req = urllib.request.Request(API, data=json.dumps({"keyword": code, "currentPage": 1, "pageSize": 5}).encode(),
                                 headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=20))
    for c in ((d.get("data") or {}).get("componentPageInfo") or {}).get("list") or []:
        if c.get("componentCode") == code:
            return c
    return None


path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent.parent / "board/netlist.circuit.json")
parts = netlist.read(path)
by_code = {}
for p in parts.values():
    if p.jlc:
        by_code.setdefault(p.jlc, []).append(p)
bad = 0
for code, ps in sorted(by_code.items()):
    c = lookup(code)
    refs = ",".join(sorted(p.ref for p in ps))
    if c is None:
        print(f"MISSING {code:10} {ps[0].value:22} {refs}")
        bad += 1
        continue
    stock = c.get("stockCount") or 0
    flag = "LOW    " if stock < 50 else "       "
    bad += stock < 50
    print(f"{flag}{code:10} stock={stock:<8} {c.get('componentLibraryType'):6} {c.get('componentModelEn')[:28]:28} "
          f"{(c.get('componentSpecificationEn') or '')[:18]:18} want={ps[0].value:12} {refs}")
print(f"{len(by_code)} part numbers, {bad} missing or low")
