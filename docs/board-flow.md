# Board flow: netlist → board → order

How the board is made and checked. What it is and why: [design.md](design.md).

## The model

Copied from the GridCore Nano:

- `index.circuit.tsx` and `src/blocks/` are the **netlist** (tscircuit), with no
  placement.
- `board/wallet.kicad_pcb` is the board. It was placed by script once
  (`scripts/newBoard.py`); since then the placement and the routing are the
  owner's, by hand in KiCad. Netlist changes reach it through `syncBoard.py`.
- The routing is by hand. Placement is checked and tuned by routing it:
  [humanizedKicadAutoRouter](https://github.com/Ivshti/humanizedKicadAutoRouter)
  pre-routes the wiring inside each block on a copy (`partialRoute.py`) and
  routes the whole board on a copy as the routability gauge (`routeGauge.py`);
  [KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools) gives a
  quick second opinion (`trialRoute.sh`). Autorouted copper is a measure, never
  the deliverable; nets are kept one by one only when they look hand-drawn.
- `scripts/newBoard.py --force` regenerates the board from scratch (outline,
  slots, stack-up, pours, keep-outs, placement, then the ratsnest pass of
  `scripts/optimizePlacement.py`) and **destroys hand work**. After the first
  hand edit, use `syncBoard.py` only.

## Commands

From this directory:

```sh
npm install
KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3

# netlist
npx tsci export index.circuit.tsx -f circuit-json -o board/netlist.circuit.json
python3 scripts/checkNets.py             # every named net reaches two pads
python3 scripts/checkMcuAf.py            # every MCU signal is an alternate function ST lists for that pin
python3 scripts/jlcStock.py              # live JLC stock for every part number

# board (close KiCad before anything that writes board/wallet.kicad_pcb)
$KICAD_PY scripts/syncBoard.py           # dry run; --apply writes; --refresh REF after editing its footprint
$KICAD_PY scripts/checkPinNets.py        # IC pads vs datasheet pin tables, on the board copper
$KICAD_PY scripts/checkFpc.py            # connector / lever / antenna orientation, tail lengths
$KICAD_PY scripts/checkParts.py board/wallet.kicad_pcb   # each land vs the land JLC places that part on
$KICAD_PY scripts/refreshKeepouts.py     # redraw the keep-out rule areas after a connector moves

# routing aids (all work on a copy)
$KICAD_PY scripts/partialRoute.py --out DIR          # block wiring, hand copper kept
$KICAD_PY scripts/routeGauge.py BOARD --out DIR      # full autoroute, owner conventions: how routable is it
scripts/trialRoute.sh                                # KiCadRoutingTools: failed nets / vias
$KICAD_PY scripts/placeVariant.py IN OUT SPEC.json   # a placement variant (see board/experiments/)

# copper hygiene (cleanCopper --check is a gate before every commit)
$KICAD_PY scripts/cleanCopper.py IN [OUT]            # drop stale (pad-less) copper; refuses if DRC gets worse
$KICAD_PY scripts/cleanCopper.py BOARD --check       # exit 1 if anything stale is left
$KICAD_PY scripts/widenFanouts.py IN [OUT]           # thicken 3.3 V pad-to-via links up to 0.4 mm
$KICAD_PY scripts/stripGndTies.py IN [OUT]           # drop GND pad-to-via ties (pour + stitching instead)

# back silkscreen: the flame (rerun after moving parts, pads or labels on the back)
$KICAD_PY scripts/silkFlame.py           # dry run on a copy, with DRC; --apply installs it (close KiCad first)

# order set (refill zones and save in KiCad first: the export plots the fills as saved)
production/make_order.sh                 # print image, JLC + PCBWay sets, pre-fab checks -> production/order/

# case and pictures
../../.venv-enclosure/bin/python enclosure/generate.py   # case from the board: enclosure/out/ (STEP, STL) + docs/enclosure/ (page preview)
enclosure/render_hero.sh                 # the concept page's hero image from the 3D model (needs Chrome)
```

The routing aids find the router through `../../tools/humanRouter` (or
`HUMAN_ROUTER=/path`) and `../../projects/KiCadRoutingTools` (or `KRT=/path`).
Run one router job at a time: each takes about 2 GB of RAM.

## The order set

`production/make_order.sh` writes `production/order/`:

| Path | What it is |
|---|---|
| `jlc-plain/wallet_gerbers.zip` | JLCPCB fab files, ordinary silkscreen (the flame in white) |
| `wallet_bom-jlc.csv`, `wallet_positions-jlc.csv` | JLC assembly, bottom side |
| `pcbway/` | PCBWay: fab zip with the full-colour back print (from `art/build_uv.py`, see [art/README.md](../art/README.md)) and its positioning image, turnkey BOM (manufacturer + MPN), centroid, `ORDER-NOTES.txt` |
| `PREFAB-CHECKS.txt` | every gate above, run on the board as saved, plus BOM/placement parity, drill census and stock |
| `stock.csv`, `rotation-review.csv` | JLC stock per line; rotations to check in JLC's placement preview |

`COLOUR=1 production/make_order.sh` also builds a JLC colour zip. JLC does not
offer its colour option for files that EasyEDA didn't write, so that zip is kept
for reference only; colour goes through PCBWay.

At order time, by hand:

- check the parts `PREFAB-CHECKS.txt` lists in JLC's placement preview (rotation
  offsets come only from that preview, never from a guess);
- do not fit the parts it lists as out of stock (today the OPTIGA) in JLC's order
  page;
- ask for rails with fiducials.

What the checks do not cover: impedance, IR drop and heat, enclosure fit, and
power-up behaviour. Those need a board in hand ([design.md](design.md#bring-up-checks-that-need-parts-in-hand)).

## Where things live

- `board/footprints.json` picks each part's footprint, with pad maps where the
  netlist and the land disagree (USB-C, switches, FPC tabs).
- `board/wallet.pretty/` holds the lands KiCad does not ship: the SE050's NXP
  SOT1969-1 land, the E73 module, the hole-clear ALPS SKRT button, and the
  Nano's BQ25185 / TPS63802 / Molex 54132 / touch-FPC lands.
- `board/wallet.kicad_dru` holds the board's own rules: In2 GND only, In1 the
  3.3 V plane, fine-pitch lands.
- `src/parts/stm32u585Pins.ts` is **generated** from ST's pin database by
  `scripts/genStm32Pins.py`; the MCU's signal pins are assigned in
  `src/parts/mcuAssign.json`. After editing assignments, re-export the netlist:
  `checkMcuAf.py` compares the two.
- A part is assembled by the board maker exactly when it carries a JLC part
  number. The display panel, camera module and cell are fitted by hand.
- `board/experiments/` holds placement candidates and their gauge results.
