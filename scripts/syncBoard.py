#!/usr/bin/env python3
"""Push the tscircuit netlist into board/wallet.kicad_pcb. Placement and copper are never touched.

    npx tsci export index.circuit.tsx -f circuit-json -o board/netlist.circuit.json
    $KICAD_PY scripts/syncBoard.py            # dry run: prints what would change
    $KICAD_PY scripts/syncBoard.py --apply    # writes the board (close it in KiCad first)
    $KICAD_PY scripts/syncBoard.py --apply --refresh U2,J1   # also re-instantiate these from their library
    $KICAD_PY scripts/syncBoard.py --board COPY.kicad_pcb ...  # work on another board file

KICAD_PY is KiCad's bundled interpreter, the only one with `pcbnew`:
    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3

What it does:
  - pad nets: every pad gets exactly the net the netlist gives it, or no net;
    an entry in board/footprints.json may carry a "pads" map from netlist pad
    names to the footprint's (one to many: USB-C A1+B12, a shield named SH);
  - a part in the netlist but not on the board is added, parked east of the
    outline for you to place;
  - a part whose footprint differs from board/footprints.json is swapped in place,
    keeping position, rotation and side; --refresh does the same for a part whose
    library footprint was edited (a board copy that differs from its library is a
    DRC lib_footprint_mismatch);
  - value, the LCSC property and the DNP flag (no JLC number = DNP) follow the netlist;
  - a part on the board but not in the netlist is reported, and removed only with
    --prune; footprints marked "Not in schematic" (the mounting holes) are skipped.
Tracks keep their old nets on purpose: a pad whose net changed shows up in DRC
as a track connecting the wrong net, which is what needs re-routing.

It refuses to write if a netlist pad does not exist on its footprint, because
that is a connection that would silently vanish.
"""
import json
import os
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent.parent
BOARD = HERE / "board/wallet.kicad_pcb"
NETLIST = HERE / "board/netlist.circuit.json"
FPMAP = HERE / "board/footprints.json"
PROJECT_LIB = HERE / "board/wallet.pretty"
KICAD_LIBS = Path(os.environ.get(
    "KICAD_FOOTPRINTS", "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"))

sys.path.insert(0, str(Path(__file__).parent))
import netlist  # noqa: E402
import applyModels  # noqa: E402
import newBoard as nb  # noqa: E402

_io = pcbnew.PCB_IO_KICAD_SEXPR()


def load_footprint(spec):
    lib, name = spec.split(":", 1)
    path = PROJECT_LIB if lib == "wallet" else KICAD_LIBS / f"{lib}.pretty"
    fp = _io.FootprintLoad(str(path), name)
    if fp is None:
        raise SystemExit(f"footprint {spec} not found in {path}")
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    spec = applyModels.table().get(f"{lib}:{name}")
    if spec:   # board/models.json: the KiCad library's model is absent here or misaligned
        applyModels.apply(fp, spec)
    return fp


def ensure_net(board, name):
    if not name:
        return board.FindNet(0)
    net = board.FindNet(name)
    if net is None:
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
    return net


def sync(board, parts, fpmap, prune=False, park_x=None, refresh=()):
    """Mutates `board`. Returns (changes, errors) as lists of strings."""
    changes, errors = [], []
    # board-only footprints (mounting holes) are not in the netlist by design
    on_board = {fp.GetReference(): fp for fp in board.GetFootprints()
                if not fp.GetAttributes() & pcbnew.FP_BOARD_ONLY}
    edge = board.GetBoardEdgesBoundingBox()
    park_x = park_x if park_x is not None else edge.GetRight() + pcbnew.FromMM(10)
    park_y = edge.GetTop()

    for ref, part in sorted(parts.items()):
        entry = fpmap.get(ref)
        spec = entry if isinstance(entry, str) or entry is None else entry["fp"]
        padmap = {} if isinstance(entry, str) or entry is None else entry.get("pads", {})
        want_by_pad = {}
        for name, net in part.pads.items():
            for target in padmap.get(name, [name]):
                prev = want_by_pad.get(target)
                if prev is not None and prev != net:
                    errors.append(f"{ref}: footprint pad {target} gets two nets, {prev} and {net}")
                want_by_pad[target] = net
        if spec is None:
            errors.append(f"{ref}: no footprint in board/footprints.json")
            continue
        fp = on_board.get(ref)
        if fp is None:
            fp = load_footprint(spec)
            fp.SetReference(ref)
            fp.SetPosition(pcbnew.VECTOR2I(park_x, park_y))
            park_y += pcbnew.FromMM(6)
            board.Add(fp)
            # every part sits on the back face; flip after Add (Flip() on a footprint
            # that has no board segfaults pcbnew)
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
            nb.style_text(fp, nb.BOT)
            changes.append(f"{ref}: added ({spec}) on the back, parked east of the board")
        elif fp.GetFPIDAsString() != spec or ref in refresh:
            new = load_footprint(spec)
            new.SetReference(ref)
            # add before flipping: Flip() on a footprint that has no board segfaults pcbnew
            board.Add(new)
            new.SetPosition(fp.GetPosition())
            if fp.IsFlipped():
                new.Flip(new.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
            new.SetOrientation(fp.GetOrientation())
            nb.style_text(new, nb.BOT if new.IsFlipped() else nb.TOP)
            # a pad's own settings survive the swap (the router sets solid pour
            # connections on GND pads the thermals starve)
            old_pads = {(p.GetNumber(), p.GetPosition().x, p.GetPosition().y): p for p in fp.Pads()}
            for p in new.Pads():
                o = old_pads.get((p.GetNumber(), p.GetPosition().x, p.GetPosition().y))
                if o is not None:
                    p.SetLocalZoneConnection(o.GetLocalZoneConnection())
                    p.SetLocalClearance(o.GetLocalClearance())
            board.Remove(fp)
            changes.append(f"{ref}: footprint {fp.GetFPIDAsString()} -> {spec} (position kept)")
            fp = new

        if fp.GetValue() != part.value:
            changes.append(f"{ref}: value {fp.GetValue()!r} -> {part.value!r}")
            fp.SetValue(part.value)
        if fp.IsDNP() == part.fitted:
            fp.SetDNP(not part.fitted)
            fp.SetExcludedFromBOM(not part.fitted)
            changes.append(f"{ref}: {'DNP' if not part.fitted else 'fitted'}")
        if part.jlc and (not fp.HasField("LCSC") or fp.GetFieldText("LCSC") != part.jlc):
            fp.SetField("LCSC", part.jlc)
            field = fp.GetField("LCSC")
            field.SetVisible(False)
            field.SetLayer(pcbnew.F_Fab)
            changes.append(f"{ref}: LCSC -> {part.jlc}")
        elif not part.jlc and fp.HasField("LCSC"):
            # A part that left JLC's BOM must not keep the old number: checkParts
            # and anyone reading the board would take it for the part on the land.
            fp.Remove(fp.GetField("LCSC"))
            changes.append(f"{ref}: LCSC removed (not JLC-fitted)")

        pad_numbers = {p.GetNumber() for p in fp.Pads()}
        missing = sorted(set(want_by_pad) - pad_numbers, key=lambda s: int(s) if s.isdigit() else 0)
        if missing:
            errors.append(f"{ref}: netlist pads {missing} are not on footprint {spec}")
        for pad in fp.Pads():
            if not pad.GetNumber():
                continue   # NPTH / mechanical
            want = want_by_pad.get(pad.GetNumber(), "")
            if pad.GetNetname() != want:
                changes.append(f"{ref}.{pad.GetNumber()}: {pad.GetNetname() or '(none)'} -> {want or '(none)'}")
                pad.SetNet(ensure_net(board, want))

    for ref, fp in sorted(on_board.items()):
        if ref not in parts:
            if prune:
                board.Remove(fp)
                changes.append(f"{ref}: removed (not in the netlist)")
            else:
                changes.append(f"{ref}: on the board but not in the netlist (kept; --prune removes it)")
    return changes, errors


def main():
    apply_ = "--apply" in sys.argv
    prune = "--prune" in sys.argv
    arg = lambda flag: sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None
    refresh = set((arg("--refresh") or "").split(",")) - {""}
    board_path = Path(arg("--board") or BOARD)
    parts = netlist.read(NETLIST)
    fpmap = {k: v for k, v in json.load(open(FPMAP)).items() if not k.startswith("_")}
    board = pcbnew.LoadBoard(str(board_path))
    changes, errors = sync(board, parts, fpmap, prune=prune, refresh=refresh)
    for c in changes:
        print(c)
    for e in errors:
        print("ERROR", e)
    if errors:
        print("not written: fix the errors above first")
        return 1
    if not changes:
        print("board already matches the netlist")
        return 0
    if apply_:
        board.BuildConnectivity()
        pcbnew.SaveBoard(str(board_path), board)
        print(f"wrote {board_path} ({len(changes)} change(s))")
    else:
        print(f"dry run: {len(changes)} change(s); --apply writes them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
