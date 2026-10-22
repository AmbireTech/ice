#!/usr/bin/env python3
"""Remove GND pads' short ties to a via beside them; the pour and stitching ground them.

    $KICAD_PY scripts/stripGndTies.py in.kicad_pcb [out.kicad_pcb] [--list]

The owner's rule (2026-10-17): a GND pad joined by a short track to a GND via
right beside it is clutter -- the outer pour takes the pad and stitching vias tie
the pour to the In2 plane. A tie is a piece of GND tracks and vias (joined where
an end touches copper) that reaches a GND pad and is at most TIE_MM of track.
Kept, because the part asks for a short path to the plane:
  - the LTC3539 boost's grounds and its input/output caps, and the TLV62569 USB
    buck's ground and its caps (switching loops);
  - the AO3416 reverse-cell FET's source: the whole cell current returns there;
  - the TPS60230 backlight charge pump (switching, exposed pad);
  - the BQ25172 charger's exposed pad (heat while charging);
  - the E73 radio module's ground pads (RF ground);
  - the USBLC6 ESD clamp's ground (surge current).
Afterwards the board is left with pads the pour may not reach and pour pieces
without vias: run humanRouter with `--keep-copper --nets GND --knob
fanoutParts=none`, which stitches and joins those pads to the pour.
"""
import argparse
import math
from collections import defaultdict

import pcbnew

NM = 1e6
TIE_MM = 4.5
KEEP = {"U2.2", "U2.3", "U2.9", "C_IN_BOOST.2", "C_BOOST.2", "U3.2", "C_USB3V3.2", "C_VBUS.2",
        "Q_REV.2", "U13.7", "U13.14", "U13.17", "U1.4", "U1.9", "U11.5", "U11.21", "U11.24", "D_ESD.2"}


def touches(t, p):
    if t.GetClass() == "PCB_VIA":
        return math.dist((p.x, p.y), (t.GetPosition().x, t.GetPosition().y)) <= t.GetWidth(pcbnew.F_Cu) / 2 + 1000
    return pcbnew.SEG(t.GetStart(), t.GetEnd()).Distance(p) <= t.GetWidth() / 2 + 1000


def ends(t):
    return [t.GetPosition()] if t.GetClass() == "PCB_VIA" else [t.GetStart(), t.GetEnd()]


def ties(board):
    """[(pad names, track mm, items)] for every GND piece with a via, a pad and <= TIE_MM of track."""
    items = [t for t in board.GetTracks() if t.GetNetname() == "GND"]
    pads = [p for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname() == "GND"]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            c = items[j]
            if a.GetClass() == c.GetClass() == "PCB_TRACK" and a.GetLayer() != c.GetLayer():
                continue
            if any(touches(c, p) for p in ends(a)) or any(touches(a, q) for q in ends(c)):
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i, t in enumerate(items):
        groups[find(i)].append(t)
    out = []
    for g in groups.values():
        tracks = [t for t in g if t.GetClass() == "PCB_TRACK"]
        if not tracks or len(tracks) == len(g):
            continue
        names = {f"{p.GetParentFootprint().GetReference()}.{p.GetNumber()}"
                 for t in tracks for e in ends(t) for p in pads if p.HitTest(e)}
        mm = sum(t.GetLength() for t in tracks) / NM
        if names and mm <= TIE_MM:
            out.append((sorted(names), mm, g))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--list", action="store_true", help="only list the ties")
    a = ap.parse_args()
    board = pcbnew.LoadBoard(a.src)
    found = ties(board)
    removed = 0
    for names, mm, g in sorted(found, key=lambda x: x[0]):
        keep = bool(set(names) & KEEP)
        print(f"  {'keep' if keep else 'drop'} {','.join(names)}: {mm:.2f} mm, "
              f"{sum(1 for t in g if t.GetClass() == 'PCB_VIA')} via(s)")
        if not keep and not a.list:
            for t in g:
                board.Remove(t)
            removed += 1
    if a.list:
        return
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(a.dst or a.src)
    print(f"removed {removed} tie(s), kept {len(found) - removed}; wrote {a.dst or a.src}")


if __name__ == "__main__":
    main()
