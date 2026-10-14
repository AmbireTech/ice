#!/usr/bin/env python3
"""MCU pin-assignment optimizer: re-pick which STM32 pin carries each signal so
the ratsnest out of the package crosses itself less.

    $KICAD_PY scripts/optimizePins.py [board.kicad_pcb] [--apply]

Reads src/parts/mcuAssign.json and the placed board, then greedily moves each
signal to another pin, or swaps it with the signal on that pin, whenever that
lowers optimizePlacement's cost (airwire length + crossings). A signal may only
go to a pin ST lists for its peripheral function (scripts/ref/STM32U585VITxQ.xml),
and a plain GPIO to any free I/O pin. Fixed: USB, SWD/SWO, the 32 kHz crystal,
BOOT0, the wake-up button (PA0 = WKUP1). Interrupt inputs keep distinct EXTI
line numbers (EXTIn is shared by every port's pin n).

Without --apply it only reports. With --apply it rewrites mcuAssign.json; then
re-export the netlist and regenerate the board (README, "Board flow").
"""
import collections
import json
import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import optimizePlacement as op  # noqa: E402

ASSIGN = HERE.parent / "src/parts/mcuAssign.json"
XML = HERE / "ref/STM32U585VITxQ.xml"
FIXED = {"PA11", "PA12", "PA13", "PA14", "PB3", "PC14", "PC15", "PH3", "PA0"}
NEVER_GPIO = FIXED | {"PH0", "PH1", "PC13"}       # HSE pins kept free; PC13 is current-limited
INTERRUPTS = {"CTP_INT", "GAUGE_ALRT", "BTN_N", "TFT_TE", "AIRGAP_SENSE", "CHG_STAT1"}


def st_pins():
    root = ET.parse(XML).getroot()
    tag = lambda e: e.tag.split("}")[1]
    pins = {}
    for p in (e for e in root if tag(e) == "Pin"):
        if p.attrib["Type"] != "I/O":
            continue
        port = p.attrib["Name"].split("-")[0].split(" ")[0]
        pins[port] = (p.attrib["Position"], {s.attrib["Name"] for s in p if tag(s) == "Signal"})
    return pins


def exti(port):
    return int("".join(c for c in port[2:] if c.isdigit()))


def main():
    apply = "--apply" in sys.argv
    out_board = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != out_board]
    board = pcbnew.LoadBoard(args[0] if args else str(op.nb.syncBoard.BOARD))
    pins = st_pins()
    assign = json.load(open(ASSIGN), object_pairs_hook=collections.OrderedDict)
    comment = assign.pop("_comment", None)
    m = op.Model(board)
    u4 = board.FindFootprintByReference("U4")
    pad_of = {port: u4.FindPadByNumber(pins[port][0]) for port in pins}

    def allowed(port, net, signal):
        if port in FIXED:
            return {port}
        if signal in ("GPIO", "ANALOG"):
            return {p for p in pins if p not in NEVER_GPIO}
        return {p for p, (_, sig) in pins.items() if signal in sig and p not in FIXED}

    def set_net(port, net):
        pad = pad_of[port]
        old = pad.GetNetname()
        target = board.FindNet(net) if net else board.FindNet(0)
        if net and target is None:
            target = pcbnew.NETINFO_ITEM(board, net)
            board.Add(target)
        pad.SetNet(target)
        for n in (old, net):
            if n in m.net_pads:
                m.net_pads[n] = [(r, pd) for r, pd in m.net_pads[n] if pd is not pad]
        if net in m.net_pads:
            m.net_pads[net].append(("U4", pad))
        return old

    def cost(nets):
        nets = {n for n in nets if n in m.net_pads}
        for n in nets:
            m.wires[n] = m._wires(n)
        others, ids = m.all_wires(skip=nets)
        return m.nets_cost(nets, others, ids)

    def exti_ok(a):
        lines = [exti(p) for p, (net, _) in a.items() if net in INTERRUPTS]
        return len(lines) == len(set(lines))

    l0, x0 = m.total()
    print(f"start: airwire {l0:.0f} mm, {x0} crossings, {len(assign)} assigned pins")
    random.seed(2)
    changes = 0
    for it in range(6):
        improved = 0
        order = list(assign.items())
        random.shuffle(order)
        for port, (net, signal) in order:
            if port not in assign or assign[port][0] != net or port in FIXED:
                continue
            best = None
            for cand in sorted(allowed(port, net, signal)):
                if cand == port:
                    continue
                other = assign.get(cand)
                if other and port not in allowed(cand, *other):
                    continue
                trial = dict(assign)
                trial[cand] = (net, signal)
                if other:
                    trial[port] = other
                else:
                    trial.pop(port)
                if not exti_ok(trial):
                    continue
                nets = {net} | ({other[0]} if other else set())
                before = cost(nets)
                set_net(cand, net)
                set_net(port, other[0] if other else "")
                after = cost(nets)
                set_net(port, net)
                set_net(cand, other[0] if other else "")
                cost(nets)
                if after < before - 1e-6 and (best is None or before - after > best[0]):
                    best = (before - after, cand, other)
            if best:
                _, cand, other = best
                set_net(cand, net)
                set_net(port, other[0] if other else "")
                cost({net} | ({other[0]} if other else set()))
                assign[cand] = (net, signal)
                if other:
                    assign[port] = other
                else:
                    assign.pop(port)
                improved += 1
                changes += 1
        l1, x1 = m.total()
        print(f"pass {it + 1}: {improved} reassignments -> airwire {l1:.0f} mm, {x1} crossings")
        if not improved:
            break
    if out_board:
        pcbnew.SaveBoard(out_board, board)
        print(f"wrote {out_board} (pads re-netted for a trial route; the netlist is unchanged)")
    if apply and changes:
        out = collections.OrderedDict()
        if comment:
            out["_comment"] = comment
        for port in sorted(assign, key=lambda p: (p[1], exti(p))):
            out[port] = list(assign[port])
        ASSIGN.write_text(json.dumps(out, indent=2) + "\n")
        print(f"wrote {ASSIGN} ({changes} reassignments)")
    elif changes:
        print("dry run: --apply writes mcuAssign.json")


if __name__ == "__main__":
    main()
