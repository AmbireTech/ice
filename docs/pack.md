# The battery pack (v4)

Every place on the device we found for the AAA cell was ugly: a bare cell on the front,
a hump on the back, or a cover over it ([cell-placement.md](cell-placement.md)). v4 takes
the cells off the device. **Two AAA cells ride in a separate pack that snaps onto the
back, high up, just below the camera.** Four spring pins and two magnets on the pack land
on the device board's back: plain gold pads under the pins, steel spacers under the
magnets. Every part on both halves is JLC stock. The device itself gets thinner, with a
flat front and back.

**Status (2026-11-01):** both halves carry the production contacts. The device board is in
`board/`, with its order set in `production/order/`. The pack board is `pack/` (built by
`pack/makePack.py`), with its order set in `production/pack-order/`. The /v4/ page's 3D
preview (`docs/v4/`, from `enclosure/generate.py`) is built from both. The next batch tests
these exact parts. On 2026-11-01 the pack side changed from Xinyangze's magnetic male (20
in stock anywhere) to single pins and separate magnets; later that day the gold target
discs went from the device board (the only part that came through JLC Global Sourcing,
two weeks on every order): the pins now land on plain ENIG pads, and the steel under the
magnets grew 0.5 mm with a taller pin, so the pins work at the same point of their stroke.

## What it comes to

All numbers come from the generated model (`enclosure/generate.py`, 2026-11-01).

| | v3 (one cell on the board) | v4 |
|---|---|---|
| Device thickness | 11.5 mm | **10.22 mm** |
| At the cell | 17.1 mm open, 17.7 mm under its cover | **22.05 mm** with the pack on |
| Cells | one AAA | two AAA in series: twice the energy |
| Back face | the cell stands out | flat; a window for the pack's contacts; the camera head stands 2.0 mm out |
| Without the cells | — | runs from USB |

The pack is 51.2 × 29.4 × 11.83 mm. It spans the back from 18.2 to 47.6 mm below the top
edge (the device is 91.2 mm tall), just south of the camera. Its two cells lie side by
side, 12.8 mm apart axis to axis, which leaves 1.8 mm between the arms of neighbouring
clips. A cell goes in through its arms' tips (6.9 mm apart in the clip's model), so each
arm swings out about 0.9 mm at its widest as it does; on the east side the neighbouring
arms are the pack's minus and the south cell's plus, ahead of the fuse, and must not touch. The contacts lie
east-west along the south cell, the four pins in a row under the cell's south flank,
clear of the clips, which hold only the cells' ends. The fuse, the resistor and the
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
                        1.5 mm     ▲    ▲    ▲    ▲   (bare ENIG)  pins pressed 0.83 of 1.2 mm
 device back      ───┐  ┌──────────┴────┴────┴────┴──────────┐  window: the frame drops through
                     └──┤ magnet    frame (print)       magnet├─── back face, 0.78 mm above the pack board
 pack board 0.8 mm  ════╧═════════════════════════════════════╧══ inside the tray's rim
 tray               ║        ( AAA )            ( AAA )          ║  cells in series, in clips, open underneath
```

- **The device's half: commodity parts, no connector.** All of them sit on the board's
  back, south of the MCU, centred at (22.0, 42.28), in a row east-west.
  - **Four plain pads**, 2.235 mm round, ENIG like the rest of the board, 2.5 mm apart:
    the pins' landings (J_PK1-4). No part: nothing to place, nothing to source. The land is
    Mill-Max's for its 1578-0-57 hard-gold target disc, which still fits if a pad ever wears
    through (ENIG is a tenth of a micron of soft gold over nickel; the pack is rarely taken
    off, and bring-up measures the contacts after a few hundred matings).
  - **Two steel spacers**, Würth WA-SMSI 9774015243R: M2, 1.5 mm tall, 4.35 mm across,
    tin-plated steel. They sit 15.41 mm apart, under the pack's magnets, on Würth's land: a
    5.3 mm pad round a 3.0 mm unplated hole. The hole is not for a screw: each spacer has a
    2.8 mm spigot, 1.4 mm long, that drops into it, so the flange sits on the pad. On the
    1.0 mm board the spigot stands about 0.4 mm out of the front, under the screen's 0.3 mm
    foam tape. JLC C22431324 (1,418 at JLC, 2026-11-01). Their only
    job is to be steel for the magnets, and to set the gap; they are on ground so they do
    not float.
- **The pack's half: four single spring pins and two magnets**, on the pack board's outer face.
  - **Pins:** Xinyangze YZP0118-20063-02 (LCSC C5157302, 879 at JLC on 2026-11-01:
    219 packs). They are SMD with a 0.7 mm peg, 2.5 mm apart, on JLC's own land (a 2.1 mm
    pad round a 0.9 mm plated hole), and JLC places them. Each has a 2.0 mm flange, a
    1.5 mm barrel to 4.0 mm, a 0.9 mm plunger, and stands 5.5 mm free, 4.5 mm at working
    height (25 g ± 20 %) and 4.3 mm fully pressed. The shorter YZP0058-20048-02 (4.3 free,
    30 g) that the magnetic male used needed the 0.635 mm disc under it to reach.
  - **Magnets:** two NdFeB N40 discs, Radial Magnets 8063, 3.18 mm across and 3.18 mm long
    (DigiKey 469-8063-ND, 3,794 in stock on 2026-11-01, $0.35). They have the same diameter
    as the old male's magnets (3.0 × 2.0 mm) and about 1.6 times the volume. They stand on
    the board over the steel spacers, glued.
  - **The frame** is a printed part with the old male's outline: 21.2 × 6.86 mm, a 5.03 mm
    tab east, 2.98 mm thick. Two pegs locate it in two holes in the pack board, and it is
    glued down. A pocket holds each magnet, and a slot clears the pins. Its face stands
    0.2 mm short of the magnets', so only the magnets touch the steel.
- **The magnets hold the pack, on the steel.** Steel takes a magnet either way round, so
  either pole can face out.
- **The pack goes on only one way round.** The frame's tab and its notch in the window stop
  a pack turned 180°, which would put the plus on the minus.
- **The stack sets the thickness.** The steel (1.5 mm) plus the magnets (3.18 mm) put the
  pack board 4.68 mm below the board's back, 0.78 mm below the back's outer face. The pins
  stand 4.68 mm on the pads: 0.83 mm into their 1.2 mm stroke, 0.38 mm short of bottoming,
  0.18 mm above their 4.5 mm working height (about 20 g each). The 1.0 mm steel of the
  first design, with the discs, made the gap 4.18 mm; the pack sits 0.5 mm further out now.
  The pack board never bears on the case: the magnets on the steel hold the pack, and the
  tray's rim sits on the back face round it.
- **Four contacts:** the pack's plus, its minus, the point between the two cells, and the
  pack's thermistor. They run in a row, plus at the east end and minus at the west.
- **The pack charges in the device**, over USB, as the single cell did.

**Where it lands on the board.** The contacts need a bare 19.8 × 4.4 mm strip of the
board's back (the frame must also clear the parts beside it), and the back has none
to spare. West of the MCU sit the secure elements and
the backlight pump. East of it lies the touch panel's tail, flat along the back (x
36.5–43.5) up to its connector, which rules the east out (an earlier layout put the
contacts there, on top of the tail). South of the MCU, between it and the display
connector, the board had a 4.6 mm routing channel. The display connector moved 5.5 mm
south and its tail's slot 2.5 mm, which keeps the tail within its slack (3.0 mm, the
most `checkFpc.py` allows) and the slot clear of the radio module's pads. The contacts
fill the channel. The MCU's south-side signals now pass them on the front copper.

## Why these parts

**Every contact part stocked by a distributor** (owner, 2026-10-31 and 2026-11-01). JLC
lists 13 magnetic pogo connectors, all from Xinyangze, with stock in the tens and hundreds.
The 4-pin male the pack first used (YZP0048-20048-04025-03) had 20 at JLC, the same 20 at
LCSC, and none at any distributor behind JLC Global Sourcing. The only magnetic 4-pin at
DigiKey/Mouser, Mill-Max's Maxnetic 873, stands 7.6 mm tall. So the pack's half is split
into its parts: single pins (thousands at JLC across makers) and disc magnets (thousands
at DigiKey), with a printed frame doing what the male's body did.

- **Flat pads, not discs** (owner, 2026-11-01). The Mill-Max 1578 disc gave hard gold and
  0.635 mm of height, and was the one part that came through JLC Global Sourcing: two
  weeks on every device order. A plain ENIG pad is free and always there; the height
  comes from 0.5 mm more steel and a pin 1.2 mm taller. The price is soft gold: ENIG
  wears through to nickel after some hundreds of matings, against thousands for the disc.
  The pack charges in place and is rarely taken off; bring-up measures the contact
  resistance after a few hundred matings, and the disc still fits the pad if it is needed.
- **25-30 g pins.** JLC's high-stock single pins from YIYUAN (100 g) and BAT Wireless (75 g)
  push three times as hard, as does Xinyangze's 3 A YZ29115060P-02 (120 g), the one pin at
  exactly 4.2 mm working height. Four of them would need several times the magnets' pull.
- **3.18 mm magnets set the stack.** They are the only magnets near the old male's 3 mm
  with real stock at DigiKey (the 3 × 1 mm Radial 8015 is too short to reach the steel).
  The pins sit at 69 % of their stroke on them.
- **Ruled out:** Mill-Max's larger 1593 disc (2.36 mm: 0.14 mm between neighbours at
  2.5 mm pitch), Mill-Max's own spring-pin and target families 872/873 and 878/879 (a
  stack of 10.9 mm or more), and EDAC 685/686 (through-hole, 6 mm and more).

**What the next batch must show** (bring-up):

1. **Hold.** The magnets' pull on the steel must beat the pins' push (4 × about 20 g at
   4.68 mm, up to 25 g each) and hold the pack in a drop. The spacers are steel round an
   M2 thread, so the magnet sees an annulus, not a full disc: measure it.
2. **Contact.** Resistance per contact on the bare ENIG pads, fresh and after a few hundred
   matings: the pads are soft gold, and this is what decides whether the discs come back.
3. **The glue:** the magnets' and the frame's after a few hundred matings.

## What JLC builds

| Part | Where | JLC / LCSC | Stock (2026-10-28) |
|---|---|---|---|
| Pin landing: plain 2.235 mm ENIG pad ×4 | device board, back | none (a pad) | — |
| Steel spacer, Würth WA-SMSI 9774015243R (M2 × 1.5) ×2 | device board, back | C22431324 | 1,418 (2026-11-01) |
| Spring pin, Xinyangze YZP0118-20063-02 (SMD with a peg) ×4 | pack board, outer face | C5157302 | 879 (2026-11-01): 219 packs |
| Magnet, Radial Magnets 8063 (NdFeB N40, 3.18 × 3.18 mm) ×2 | pack board, outer face, glued | DigiKey 469-8063-ND (not JLC) | 3,794 at DigiKey (2026-11-01) |
| AAA clip MYOUNG MY-AAA-19-J (−) ×2 | pack board, inner face | C6937104 | 5,721 |
| AAA clip MYOUNG MY-AAA-20-J (+) ×2 | pack board, inner face | C6937107 | 4,248 |
| Thermistor Murata NCP18XH103F03RB (10 k, B 3380 K, 0603) | pack board | C13564 | 191,798 |
| Resettable fuse BHFUSE BSMD1206-075-30V (0.75 A hold, 1.5 A trip) | pack board, in the plus | C976305 | 52,970 |
| 1 k resistor, 0402 | pack board, in the mid-point line | C11702 | basic |

- **By hand:** the printed parts, the two magnets (glued into the frame's pockets) and the
  cells.
- **The pack board is SMT on both faces:** the pins on the outer face, the clips and small
  parts on the inner face.
- **The case, the tray and the joystick's cap** print at home or at JLC's 3D-printing
  service (JLC3DP: SLA resin including a transparent one, ±0.2 mm within 100 mm by its
  capabilities page; MJF nylon).

**Order flow, end to end:**

1. Device board at JLC, as today: every part is JLC stock.
2. Pack board at JLC: 2 layers, 0.8 mm, ENIG; SMT on both faces.
3. Magnets from DigiKey (469-8063-ND), two per pack.
4. Case, tray, the pack's frame and the joystick cap: printed at home or at JLC3DP.
5. Cells: the user's own (the device ships without cells).

## The pack's board

48.0 × 26.6 mm, the same width as the device's board, 0.8 mm thick.

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

- **Hold, contact and glue** of the magnets on the steel and the pins on the pads (above).
- **Stock for a run beyond 300 packs:** the pins have 1,216 at JLC (304 packs). Xinyangze
  makes them (www.xyzpogopin.com); other makers' 2.0 mm-flange 30 g pins fit the same land.
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
- Xinyangze, YZP0118-20063-02 drawing (2017-09-16: 5.50 free, 4.50 working at 25 g, 4.30 pressed) and YZP0058-20048-02 (2015-05-14).
- Würth Elektronik, WA-SMSI 9774015243R datasheet (M2 × 1.5; land 5.3 mm, stencil annulus).
- BAT WIRELESS, BWCD-L4.0W2.0H1.0 datasheet (Aug 2025), and YIYUAN YTC1P series drawing, via LCSC.
- DigiKey, Radial Magnets 8063 / 8003 listings (stock, 2026-11-01).
- JLC's parts library and LCSC's product API (stock and part numbers, 2026-10-28).
- JLC3DP, [3D printing capabilities](https://jlc3dp.com/capabilities/3d-printing-service).
