"""Read the tscircuit netlist (circuit JSON) into plain data.

    parts = read(path)   # {ref: Part}

Each Part carries its value, JLC part number, "fitted" flag and {pad number: net}.
A part is fitted at assembly exactly when it has a JLC part number: tscircuit
accepts `doNotPlace` but does not write it into the circuit JSON, so the part
number is the only population signal that survives the export.
Pad numbers are tscircuit pin numbers, which every footprint in
board/footprints.json is checked to share (syncBoard.py refuses a part whose
netlist pads are not all on its footprint).
"""
import json
from dataclasses import dataclass, field


@dataclass
class Part:
    ref: str
    value: str
    fitted: bool
    jlc: str
    pads: dict = field(default_factory=dict)   # pad number (str) -> net name


def _value(c):
    for k in ("display_resistance", "display_capacitance"):
        if c.get(k):
            return c[k].replace("Ω", "").replace("Ω", "")
    return c.get("manufacturer_part_number") or c["name"]


def read(path):
    elems = json.load(open(path))
    nets = {e["subcircuit_connectivity_map_key"]: e["name"]
            for e in elems if e["type"] == "source_net" and e.get("subcircuit_connectivity_map_key")}
    parts = {}
    for c in elems:
        if c["type"] != "source_component":
            continue
        parts[c["source_component_id"]] = Part(
            ref=c["name"],
            value=_value(c),
            fitted=False,
            jlc=(c.get("supplier_part_numbers") or {}).get("jlcpcb", [""])[0],
        )
        parts[c["source_component_id"]].fitted = bool(parts[c["source_component_id"]].jlc)
    unnamed = {}
    ports = {p["source_port_id"]: p for p in elems if p["type"] == "source_port"}

    def net_of(p):
        key = p.get("subcircuit_connectivity_map_key")
        if key is None:
            return None
        net = nets.get(key)
        if net is None:
            net = unnamed.setdefault(key, f"Net-{parts[p['source_component_id']].ref}-{p.get('pin_number', p['name'])}")
        return net

    # Pad numbers come from the physical pads, the way tscircuit's KiCad export
    # numbers them: the port's pin number, or for a pad on a port that has none
    # (a second shield slot, a pad tscircuit split off a pin) its own name, e.g.
    # "A8" or "SH1". Going by port hints instead merged those pads with the pin
    # they were split from.
    pcb_ports = {e["pcb_port_id"]: e for e in elems if e["type"] == "pcb_port"}
    seen = set()
    for pad in elems:
        if pad["type"] not in ("pcb_smtpad", "pcb_plated_hole") or not pad.get("pcb_port_id"):
            continue
        sp = ports[pcb_ports[pad["pcb_port_id"]]["source_port_id"]]
        if sp["source_component_id"] not in parts:
            continue
        seen.add(sp["source_port_id"])
        if "pin_number" in sp:
            name = str(sp["pin_number"])
        else:
            name = next(h for h in pad.get("port_hints", []) if not h.startswith("unnamed"))
        net = net_of(sp)
        if net is not None:
            parts[sp["source_component_id"]].pads[name] = net
    # Ports with no physical pad (a part whose footprint tscircuit did not draw).
    for p in ports.values():
        if p["source_port_id"] in seen or p["source_component_id"] not in parts or "pin_number" not in p:
            continue
        net = net_of(p)
        if net is not None:
            parts[p["source_component_id"]].pads[str(p["pin_number"])] = net
    if unnamed:
        print(f"note: {len(unnamed)} net(s) without a <net> name: {sorted(unnamed.values())}")
    return {p.ref: p for p in parts.values()}


if __name__ == "__main__":
    import sys
    for ref, p in sorted(read(sys.argv[1]).items()):
        print(f"{ref:5} {p.value:14} {'    ' if p.fitted else 'DNP '}{p.jlc:9} {p.pads}")
