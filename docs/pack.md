# The battery pack (v4)

Every place on the device we found for the AAA cell was ugly: a bare cell on the front,
a hump on the back, or a cover over it ([cell-placement.md](cell-placement.md)). v4 takes
the cells off the device. **Two AAA cells ride in a separate pack that snaps onto the
back, high up, just below the camera.** Xinyangze's 4-pin magnetic pogo male on the pack
lands on the device board's back: its four spring pins on plain gold pads, its two magnets
on steel spacers. The device itself gets thinner, with a flat front and back.

**Status (2026-11-19):** the pack side is Xinyangze's magnetic male again
(YZP0048-20048-04025-03), now in the owner's JLC parts library through Global Sourcing; it
replaces the single pins, loose magnets and printed frame used since 2026-11-01. The device
board keeps its routed pads and its steel, the steel back to 1.0 mm so the male's pins
reach the pads. The matching receptacle would hold harder (magnet to magnet) but needs
2.7 mm more room south of the MCU: the display connector back south and that area re-routed
(owner: not now). The device board is in `board/`, with its order set in
`production/order/`. The pack board is `pack/` (built by `pack/makePack.py`), with its
order set in `production/pack-order/`. The /v4/ page's 3D preview (`docs/v4/`, from
`enclosure/generate.py`) is built from both.

## What it comes to

All numbers come from the generated model (`enclosure/generate.py`, 2026-11-19).

| | v3 (one cell on the board) | v4 |
|---|---|---|
| Device thickness | 11.5 mm | **10.22 mm** |
| At the cell | 17.1 mm open, 17.7 mm under its cover | **21.68 mm** with the pack on |
| Cells | one AAA | two AAA in series: twice the energy |
| Back face | the cell stands out | flat; a window for the pack's contacts; the camera head stands 2.0 mm out |
| Without the cells | — | runs from USB |

The pack is 52.2 × 29.2 × 11.46 mm. It spans the back from 18.0 to 47.2 mm below the
board's top edge (the device is 92.8 mm tall), just south of the camera. Its two cells lie side by
side, 12.8 mm apart axis to axis, which leaves 1.8 mm between the arms of neighbouring
clips. A cell goes in through its arms' tips (6.9 mm apart in the clip's model), so each
arm swings out about 0.9 mm at its widest as it does; on the east side the neighbouring
arms are the pack's minus and the south cell's plus, ahead of the fuse, and must not touch. The contacts lie
east-west along the south cell, the four pins in a row under the cell's south flank,
clear of the clips, which hold only the cells' ends. The male's through-hole joints on
the pack board's inner face stay clear of the clips' steel and 0.39 mm under the south cell. The fuse, the resistor and the
thermistor sit in the strip south of the south cell's can.

- **The camera sees past it.** Its north edge sits outside a 70° cone from the lens's rim,
  measured at the pack's far face. The lens is about 66°, and the stock candidate module
  68.7° diagonal; the cone is taken as full all round.
- **It is open underneath.** The cells sit in their clips with nothing below them, as v3's
  cell did, which keeps the pack to a cell's diameter plus its board and the connector's
  stack. A door would add about 1.5 mm.

**What sets the depth of the back.** USB-C (3.4 mm tall) may stand into the floor: a
pocket over it leaves 0.7 mm of floor, and the plug's opening cuts through the back edge.
The touch connector is now a 2.0 mm part (it was 2.6 mm), so the display and camera
connectors (2.09 mm) set the depth, with the buzzer at 2.0 mm.

**The camera head.** The heads we have found are 5.4–5.9 mm tall. On the thinner back,
the head stands 2.0 mm out (0.7 mm in v3). It is the only thing standing out of the
back; a flat back needs a lower camera module.

## How it goes together

```
 device board (back)  ═ steel ═══ pad  pad  pad  pad ═══ steel ═══════════
                        1.0 mm     ▲    ▲    ▲    ▲   (bare ENIG)  pins pressed 0.30 of 1.2 mm
 device back      ───┐  ┌──────────┴────┴────┴────┴──────────┐  window: the male drops through
                     └──┤ magnet   magnetic male 2.8   magnet├─── the pack board in a 0.29 mm bay in the back
 pack board 0.8 mm  ════╧═════════════════════════════════════╧══ inside the tray's rim
 tray               ║        ( AAA )            ( AAA )          ║  cells in series, in clips, open underneath
```

- **The device's half: commodity parts, no connector.** All of them sit on the board's
  back, south of the MCU, centred at (22.0, 40.93), in a row east-west.
  - **Four plain pads**, 2.235 mm round, ENIG like the rest of the board, 2.5 mm apart:
    the pins' landings (J_PK1-4). No part: nothing to place, nothing to source.
  - **Two steel spacers**, Würth WA-SMSI 9774010243R: M2, 1.0 mm tall, 4.35 mm across,
    tin-plated steel. They sit 15.41 mm apart, under the male's magnets, on Würth's land: a
    5.3 mm pad round a 3.0 mm unplated hole for the spacer's spigot. JLC C5360192 (823 at
    JLC, 2026-11-19). Their only job is to be steel for the magnets and to set the gap;
    they are on ground so they do not float.
- **The pack's half: Xinyangze's magnetic male**, YZP0048-20048-04025-03 (LCSC C5126845;
  drawing 2022-03-24), through-hole on the pack board's outer face, tab north. Body
  21.2 × 6.86 mm, 2.8 mm thick, with a 5.03 × 2.31 mm tab; four pins at 2.5 mm standing
  4.1 mm free, 3.1 mm working, 2.9 mm pressed (30 g ± 20 g each); two NdFeB magnets
  (3.0 × 2.0 mm) flush with its face, 15.41 mm apart. Its tab is a 0.9 mm flange at the
  pack board (Xinyangze's 3D model), so mated it stands 2.9–3.8 mm off the device board,
  over the MCU's edge (1.58 mm tall) with room to spare.
- **The magnets hold the pack, on the steel.** Steel takes a magnet either way round.
- **The pack goes on only one way round.** The male's tab and its notch in the window stop
  a pack turned 180°, which would put the plus on the minus.
- **The stack sets the thickness.** The male's face rests on the 1.0 mm steel, so the pack
  board sits 3.8 mm below the board's back, in a 0.29 mm bay in the back's outer face
  (0.91 mm of back left over it). The pins stand 3.8 mm on the bare pads: 0.30 mm into
  their 1.2 mm stroke, well short of their 1.0 mm working point, so each presses with
  about a third of its rated force (owner, 2026-11-19: accepted, against re-routing the
  board for the receptacle). The pack board never bears on the case.
- **Four contacts:** the pack's plus, its minus, the point between the two cells, and the
  pack's thermistor. They run in a row, plus at the east end and minus at the west.
- **The pack charges in the device**, over USB, as the single cell did.

**Where it lands on the board.** The contacts need a bare 19.8 × 4.4 mm strip of the
board's back (the male must also clear the parts beside it), and the back has none
to spare. West of the MCU sit the secure elements and
the backlight pump. East of it lies the touch panel's tail, flat along the back (x
36.5–43.5) up to its connector, which rules the east out (an earlier layout put the
contacts there, on top of the tail). South of the MCU, between it and the display
connector, the board had a 4.6 mm routing channel. The display connector moved 5.5 mm
south and its tail's slot 2.5 mm, which keeps the tail within its slack (3.0 mm, the
most `checkFpc.py` allows) and the slot clear of the radio module's pads. The contacts
fill the channel. The MCU's south-side signals now pass them on the front copper.

## Why these parts

**The magnetic male, from the owner's own stock** (2026-11-19). Xinyangze's 4-pin male has
14 at JLC and 1 at LCSC; the owner bought it through JLC Global Sourcing into the JLC parts
library. Between 2026-11-01 and 2026-11-19 the pack used single pins, loose disc magnets and
a printed frame in the male's outline instead; the owner expects the male to hold better.

- **Its matching receptacle** (YZ103915020T-04025-02, C6276862: flat contacts and magnets
  of its own, magnet to magnet) is 7.7 mm across, with two 5.4 × 1.9 mm slots through the
  board for its locating bosses. It fits only with the display connector 2.7 mm further
  south and the area between the MCU and that connector re-routed. Not done.
- **Flat pads, not discs.** The Mill-Max 1578 disc (0.635 mm, hard gold) would put the
  pins 0.93 mm into their stroke with 1.0 mm steel, but it shows no JLC stock (Global
  Sourcing only, about two weeks). The pads are free and always there; the price is soft
  gold (ENIG wears through after some hundreds of matings) and a light press.

**What the next batch must show** (bring-up):

1. **Hold.** The male's magnets on the 1.0 mm steel must beat the four pins' push and
   hold the pack in a drop. The spacers are steel round an M2 thread, so each magnet sees
   an annulus: measure it.
2. **Contact.** Resistance per contact on the bare ENIG pads at 0.30 mm of pin travel,
   fresh, after a few hundred matings, and with the pack rocked: the pins press lightly.

## What JLC builds

| Part | Where | JLC / LCSC | Stock |
|---|---|---|---|
| Pin landing: plain 2.235 mm ENIG pad ×4 | device board, back | none (a pad) | — |
| Steel spacer, Würth WA-SMSI 9774010243R (M2 × 1.0) ×2 | device board, back | C5360192 | 823 (2026-11-19) |
| Magnetic pogo male, Xinyangze YZP0048-20048-04025-03 (through-hole) | pack board, outer face | C5126845 | 14 at JLC, 1 at LCSC (2026-11-19); the owner's own stock in the JLC parts library |
| AAA clip MYOUNG MY-AAA-19-J (−) ×2 | pack board, inner face | C6937104 | 5,721 |
| AAA clip MYOUNG MY-AAA-20-J (+) ×2 | pack board, inner face | C6937107 | **12 at JLC, 10 at LCSC (2026-11-19): 6 packs. Short** |
| Thermistor Murata NCP18XH103F03RB (10 k, B 3380 K, 0603) | pack board | C13564 | 191,798 |
| Resettable fuse BHFUSE BSMD1206-075-30V (0.75 A hold, 1.5 A trip) | pack board, in the plus | C976305 | 52,970 |
| 1 k resistor, 0402 | pack board, in the mid-point line | C11702 | basic |

- **By hand:** the printed parts and the cells.
- **The pack board is SMT on its inner face plus one through-hole part:** the male on the
  outer face (JLC's through-hole assembly), the clips and small parts on the inner face.
- **The case, the tray and the joystick's cap** print at home or at JLC's 3D-printing
  service (JLC3DP: SLA resin including a transparent one, ±0.2 mm within 100 mm by its
  capabilities page; MJF nylon).

**Order flow, end to end:**

1. Device board at JLC, as today.
2. Pack board at JLC: 2 layers, 0.8 mm, ENIG; SMT on the inner face, the male through-hole,
   from the owner's own stock in the JLC parts library.
3. Case, tray and the joystick cap: printed at home or at JLC3DP.
4. Cells: the user's own (the device ships without cells).

## The pack's board

48.0 × 26.4 mm, the same width as the device’s board, 0.8 mm thick.

- The south cell's plus goes through the fuse to the plus pin.
- The south cell's minus joins the north cell's plus at the west end: the mid-point. It
  reaches its pin through the 1 k resistor.
- The north cell's minus goes to the minus pin.
- The thermistor sits between the thermistor pin and the minus.

**A coin across the pins cannot short a cell hard.** Plus to minus goes through the fuse.
Any short to the mid-point goes through the 1 k resistor. The thermistor pin is 10 k from
the minus.

The board is glued into the tray's top, on its north and south walls. The cells press into
the clips from underneath.

## What changes on the device's board

- **Out: the two cell clips. In: the pack's contacts** (four pads, two steel spacers),
  south of the MCU, an ESD diode on the minus, mid-point and thermistor lines. The display connector and its tail's slot move south for it; a
  few small parts move with them (the radio's power switch and its timing cap, the
  charger's switch and its two resistors, the display's two decaps, the airgap's series
  resistor).
- **Charger (the BQ25172): set for two cells.** That means 36 kΩ on VSET, where the
  datasheet's table says 2-cell. Its over-voltage stop is 1.65–1.75 V per cell, so
  3.3–3.5 V for the pack.
- **Feed the charger from USB's 5 V, not the 3.3 V rail.** A full two-cell NiMH pack sits
  at about 2.9–3.0 V. That leaves 0.3–0.4 V of headroom from 3.3 V. The charger's own
  charge path drops more than that at 250 mA: its switch is 0.845 Ω typical and up to
  1.45 Ω, so 0.21–0.36 V. It also needs 95–175 mV above the pack to leave sleep.
  From 5 V, it burns about (5 − 2.4 V) × 0.25 A ≈ 0.65 W at the start of a charge. The
  charger regulates its own temperature at 125 °C; 200 mA is the fallback if it runs hot.
  The pull-up that holds charging off stays on the USB 3.3 V rail: it also sits on an MCU
  pin, which with the MCU unpowered is rated only to 4 V. So from plug-in until the USB
  buck's output rises (milliseconds), charging is not held off; at most the charger's
  83 mA base current flows that long.
- **Thermistor input (TS):** the thermistor moves into the pack, and the board's own goes.
  With the pack off, TS floats to its 2.3–2.9 V clamp, which the charger reads as too cold:
  no charging, by construction.
- **The cells' voltage:** the board reads the pack's plus and minus as before, and the
  mid-point through one more 1 M / 1 M divider into an ADC pin. Two lithium cells (3.6 V)
  read 1.8 V.
- **Boost (the LTC3539-2):** its input becomes 1.8–3.6 V instead of 0.9–1.8 V. It keeps
  regulating when its input is above 3.3 V (two lithium cells), which is why it was
  chosen. At twice the voltage it draws half the current from the cells.
- **Reverse-cell FET:** stays. The cells can still go into the pack's clips backwards.
- **Power key** (the joystick's centre press, through a PNP from the cells): unchanged.
  With the pack off, the device runs from USB only: it stays on while plugged in, and the
  centre press does nothing (no select, no power off; unplug to turn it off). Intended.

**Firmware:** read each cell on its own: plus minus mid-point, and mid-point minus minus.
Refuse to charge unless both cells look NiMH and match each other, so a pack with one
alkaline beside one NiMH is never charged. Taking the pack off resets the verdict; an
open thermistor line shows that the pack is off.

## Open: bench checks before anything is ordered

- **Hold and contact** of the male on the steel and the pads (above).
- **Stock:** the + clip MY-AAA-20-J has 12 at JLC (6 packs); source it (maker direct or
  Global Sourcing, consigned to JLC) or pick a stocked clip pair. The male is the owner's
  own stock.
- **The cells are open underneath:** the device rests on them when it lies on its back.
- **Fuse resistance.** The fuse is 90 mΩ fresh and up to 0.5 Ω after it has tripped.
  At full use from a nearly empty pack (~0.4 A) that is a 40–200 mV drop on 1.8 V.
- **The thermistor** is B 3380 K; TI recommends a 103AT type (B 3435 K) on TS. Check
  that the charger's 0 °C and 45 °C points move only slightly with it.
- **The camera head** stands 2.0 mm out of the back.

## Sources

- TI, BQ25172 datasheet (SLUSDY5, June 2022): VSET table, electrical characteristics
  (VOUT_OVP per cell, RON, VSLEEP, TS clamp).
- Xinyangze drawings (via LCSC): YZP0058-20048-02 (2015-05-14), YZP0048-20048-04025-02
  (2020-10-27), YZ103915020T-04025-02 (2015-07-08),
  YZP0048-20048-04025-03 and YZR0158-20050-04025-01 (2022-03-24), YZT0675-20048-04025-01.
- Mill-Max, 1578 target disc drawing (1578-0-57-15-00-00-03-0, SMT pad 0.088 in: the pads' land).
- Würth Elektronik, WA-SMSI datasheet (9774010243R, M2 × 1.0; land 5.3 mm round a 3.0 mm hole, stencil annulus).
- BAT WIRELESS, BWCD-L4.0W2.0H1.0 datasheet (Aug 2025), and YIYUAN YTC1P series drawing, via LCSC.
- JLC's parts library and LCSC's product API (stock and part numbers, 2026-10-28).
- JLC3DP, [3D printing capabilities](https://jlc3dp.com/capabilities/3d-printing-service).
