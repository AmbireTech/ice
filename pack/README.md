# The battery pack's board (v4)

The v4 signer runs from two AAA cells in a pack that snaps onto its back with magnets
(why and how: [../docs/pack.md](../docs/pack.md)). This is the pack's own board: the
cells' clips on one face, the magnetic contacts on the other. It is the pack's face
toward the device.

48.0 × 28.1 mm, 2 layers, 0.8 mm thick, ENIG (the contacts' pads), routed.

<img src="render-outer.jpg" width="430" alt="The board's outer face: the magnetic connector at the left edge, its four pins labelled BAT+, MID, NTC and BAT-">
<img src="render-cells.jpg" width="430" alt="The board's inner face: four AAA clips holding two cells in series, the fuse between two rings marked MAGNET">

*Rendered by KiCad: the outer face (toward the device), and the inner face seen from
behind, mirrored.*

## What is on it

| Part | Face | JLC / LCSC |
|---|---|---|
| Xinyangze YZR0158-20050-04025-01, 4-pin magnetic pogo male (1 A per pin) | outer | C5126843 |
| MYOUNG MY-AAA-19-J, AAA clip (−, spring) ×2 | inner | C6937104 |
| MYOUNG MY-AAA-20-J, AAA clip (+) ×2 | inner | C6937107 |
| BHFUSE BSMD1206-075-30V, resettable fuse, 0.75 A hold | inner | C976305 |
| Murata NCP18XH103F03RB, 10 k thermistor (B 3380 K), 0603 | inner | C13564 |
| 1 k resistor, 0402 | inner | C11702 |

The two cells are in series: the south cell's plus goes through the fuse to the BAT+
pin; its minus meets the north cell's plus at the west clips (the mid-point, MID),
which reaches its pin through the 1 k resistor; the north cell's minus is BAT−. The
thermistor sits between the NTC pin and BAT−, between the cells, for the charger's
temperature input. A coin across the pins never shorts a cell hard: plus to minus runs
through the fuse, any short to MID through the 1 k resistor.

The outer face carries only the connector, the BAT+ run to its pin and a BAT− pour,
so it lies flat in the device's recess. Two rings on the inner face mark where the
6 × 2 mm magnets are glued; the copper under them is covered by solder mask.

## Files

- `pack.kicad_pcb`, `pack.kicad_pro`: the board and its rules (Power class 0.8 mm for the
  cells, the fuse and the pins; 0.25 mm for the sense lines).
- `makePack.py`: builds the board from scratch (placement and routing by script, from the
  numbers the case generator fixes). After a hand edit, edit the board instead.
- `makeOrder.py`: writes `../production/pack-order/` (Gerbers and drill zip, JLC BOM and
  CPL, `stock.txt` with JLC's live stock).
- `pack.pretty/`: the connector's land, EasyEDA's for C5126843, checked against
  Xinyangze's drawing (4 × Ø2.20 pads at 2.50 mm, body 21.20 × 6.86 mm, tab 5.03 mm).
  `3d/` holds its model, drawn from that drawing by `connModel.py`: EasyEDA ships the
  YZ92015035T receptacle's model for this part (3.8 mm tall; the male's pins stand to
  5.0 mm).

```sh
KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
$KICAD_PY pack/makePack.py                                  # the board (overwrites it)
kicad-cli pcb drc --severity-all pack/pack.kicad_pcb        # as saved, and again with --refill-zones
$KICAD_PY pack/makeOrder.py                                 # the order files
```

## Ordering at JLC

- **Assembly is two-sided:** the connector on the outer face, everything else on the
  inner. Or order one-sided (the inner face) and solder the connector by hand: four
  round pads and a plastic body.
- **The connector is not in JLC's own stock** (0 on 2026-10-28; 200 at LCSC). Order it
  through JLC's global sourcing / parts pre-order before the board, or hand-solder it.
- **Check every part's rotation in JLC's placement preview** before paying. The clips
  carry the same rotation as the device board's (the south cell's clips exactly so, the
  north cell's turned 180°), and the connector's tab must point east, toward the middle
  of the board. The rotations in the CPL are KiCad's, not confirmed by JLC.
- The board is 0.8 mm thick and ENIG: pick both in the order form.

What the checks cover: DRC at every severity (none found, with and without a zone
refill) and full connectivity. What they do not: the clips holding cells on a 0.8 mm
board, the pins' contact on the pads, the fuse's heating at full load, the fit in the
tray. These files have not been ordered yet.
