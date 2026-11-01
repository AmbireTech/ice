# The battery pack (v4)

Every place on the device we found for the AAA cell was ugly: a bare cell on the front,
a hump on the back, or a cover over it ([cell-placement.md](cell-placement.md)). v4 takes
the cells off the device. **Two AAA cells ride in a separate pack that snaps onto the
back, high up, just below the camera.** A magnetic pogo male on the pack lands on
commodity parts on the device board: gold target discs under its pins, steel spacers
under its magnets. No loose magnets (owner, 2026-10-28). The device itself gets thinner,
with a flat front and back.

**Status (2026-10-31):** the device board carries the production contacts (`board/`, the
order set in `production/order/`), the pack board is `pack/` (built by `pack/makePack.py`,
order set in `production/pack-order/`), and the /v4/ page's 3D preview (`docs/v4/`, from
`enclosure/generate.py`) is built from both. The next batch tests these exact parts.

## What it comes to

All numbers come from the generated model (`enclosure/generate.py`, 2026-10-31).

| | v3 (one cell on the board) | v4 |
|---|---|---|
| Device thickness | 11.5 mm | **10.22 mm** |
| At the cell | 17.1 mm open, 17.7 mm under its cover | **21.68 mm** with the pack on |
| Cells | one AAA | two AAA in series: twice the energy |
| Back face | the cell stands out | flat; a shallow bay and a window for the pack; the camera head stands 2.0 mm out |
| Without the cells | — | runs from USB |

The pack is 51.2 × 29.6 × 11.46 mm. It spans the back from 18.0 to 47.6 mm below the top
edge (the device is 91.2 mm tall), just south of the camera. Its two cells lie side by
side, 12.8 mm apart axis to axis, which leaves 1.8 mm between the arms of neighbouring
clips. A cell goes in through its arms' tips (6.9 mm apart in the clip's model), so each
arm swings out about 0.9 mm at its widest as it does; on the east side the neighbouring
arms are the pack's minus and the south cell's plus, ahead of the fuse, and must not touch. The connector lies
east-west along the south cell, its four pins in a row under the cell's south flank,
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
 device board (back)  ═ steel ═══ disc disc disc disc ═══ steel ═══════════
                        1.0 mm     ▲    ▲    ▲    ▲  0.635 mm   pins pressed 0.93 mm
 device back      ───┐  ┌──────────┴────┴────┴────┴──────────┐  window: the male's body drops through
                     └──┤ magnet         male body     magnet ├─── back face, a 0.29 mm bay over the pack board
 pack board 0.8 mm  ════╧═════════════════════════════════════╧══ inside the tray's rim
 tray (frame)       ║        ( AAA )            ( AAA )          ║  cells in series, in clips, open underneath
```

- **The device's half: commodity parts, no connector.** All of them sit on the board's
  back, south of the MCU, centred at (22.0, 42.28), in a row east-west.
  - **Four gold target discs**, Mill-Max 1578-0-57-15-00-00-03-0. Each is 1.98 mm across
    and 0.635 mm tall, brass with 10 µ" hard gold over nickel, on Mill-Max's 2.235 mm round
    pad, 2.5 mm apart. They come on tape and reel, 7,200 to a reel. JLC places them; they
    come in through JLC Global Sourcing from DigiKey (117,259 in stock, MOQ 1, 2026-10-31).
  - **Two steel spacers**, Würth WA-SMSI 9774010243R: M2, 1.0 mm tall, 4.35 mm across,
    tin-plated steel. They sit 15.41 mm apart, under the male's magnets, on Würth's 5.3 mm
    land, without the screw hole. JLC C5360192 (883 at JLC, 45,272 at DigiKey). Their only
    job is to be steel for the magnets; they are on ground so they do not float.
- **The pack's half: Xinyangze's 4-pin magnetic male**, YZP0048-20048-04025-03 (LCSC
  C5126845, through-hole), on the pack board's outer face.
  - Its body is 21.2 × 6.86 mm with a 5.03 mm tab, 2.8 mm thick. Its NdFeB magnets
    (3.0 mm across, 2.0 mm long, 15.41 mm apart) sit flush with its face.
  - Its pins are 2.5 mm apart: 4.1 mm free, 3.1 mm working, 2.9 mm fully pressed, 30 g ± 20 %
    each at working height.
  - It drops through a window in the device's back. Mated, its face rests on the steel
    spacers, and its pins press 0.93 mm onto the discs, just short of working height.
- **The male's magnets hold the pack, on the steel.** There are no loose magnets. Steel
  takes any magnet either way round, so **any brand of 4-pin magnetic male** at 2.5 mm
  pitch with its magnets about 15.4 mm apart is a second source.
- **The pack goes on only one way round.** The male's tab and its notch in the window
  stop a pack turned 180°.
- **The stack sets the thickness.** The steel (1.0 mm) plus the male's body (2.8 mm) put
  the pack board 3.8 mm below the board's back, 0.1 mm higher than the back's outer face.
  The back gets a 0.29 mm bay over the pack board (0.91 mm of back left there), so the
  board never bears on the case: the male on the steel holds the pack, and the tray's rim
  sits on the back face round it.
- **Four contacts:** the pack's plus, its minus, the point between the two cells, and the
  pack's thermistor. They run in a row, plus at the east end and minus at the west.
- **The pack charges in the device**, over USB, as the single cell did.

**Where it lands on the board.** The contacts need a bare 19.8 × 4.4 mm strip of the
board's back (the male's body must also clear the parts beside it), and the back has none
to spare. West of the MCU sit the secure elements and
the backlight pump. East of it lies the touch panel's tail, flat along the back (x
36.5–43.5) up to its connector, which rules the east out (an earlier layout put the
contacts there, on top of the tail). South of the MCU, between it and the display
connector, the board had a 4.6 mm routing channel. The display connector moved 5.5 mm
south and its tail's slot 2.5 mm, which keeps the tail within its slack (3.0 mm, the
most `checkFpc.py` allows) and the slot clear of the radio module's pads. The contacts
fill the channel. The MCU's south-side signals now pass them on the front copper.

## Why these parts

**At least one half commodity** (owner, 2026-10-31). JLC lists 13 magnetic pogo
connectors, all from Xinyangze, with stock in the tens and hundreds. A Xinyangze
receptacle (C6276862, 125 in stock) mated with the male would have been from another
family (2.54 against 2.50 mm pitch, magnets about 14.6 against 15.4 mm apart), never mated
before, and single-source on both halves. So the device's half is commodity parts, and
the male any brand.

- **Discs, not flat pads.** A pogo pin wears gold off a flat ENIG pad, and pads put the
  male's face 0.6 mm closer to the board. Its pins then reach working height only on a
  0.5 mm spacer, and the pack board would sit 0.6 mm inside the back. The discs give hard
  gold over nickel, made as a pogo target, and the height.
- **1.0 mm spacers, not 3.0 mm.** The male's magnets are flush with its face, and its pins
  stand 1.3 mm proud of it: the steel goes right under the face.
- **Ruled out:** Mill-Max's larger 1593 disc (2.36 mm: 0.14 mm between neighbours at
  2.5 mm pitch), Mill-Max's own spring-pin and target families 872/873 and 878/879 (a
  stack of 10.9 mm or more), and EDAC 685/686 (through-hole, 6 mm and more).

**What the next batch must show** (bring-up):

1. **Hold.** The magnets' pull on the steel must beat the pins' push (4 × 30 g ± 20 %, up
   to 144 g) and hold the pack in a drop. The spacers are steel round an M2 thread, so the
   magnet sees an annulus, not a full disc: measure it.
2. **Contact.** Resistance per contact, and after a few hundred matings.

The receptacle pair is in git history (before 2026-10-31) if the spacers' hold falls
short.

## What JLC builds

| Part | Where | JLC / LCSC | Stock (2026-10-28) |
|---|---|---|---|
| Target disc, Mill-Max 1578-0-57-15-00-00-03-0 ×4 | device board, back | C21905480 (Global Sourcing) | 117,259 at DigiKey (2026-10-31) |
| Steel spacer, Würth WA-SMSI 9774010243R ×2 | device board, back | C5360192 | 883 (2026-10-31) |
| Magnetic male, Xinyangze YZP0048-20048-04025-03 (through-hole) | pack board, outer face | C5126845 | **20** (2026-11-01): short, see below |
| AAA clip MYOUNG MY-AAA-19-J (−) ×2 | pack board, inner face | C6937104 | 5,721 |
| AAA clip MYOUNG MY-AAA-20-J (+) ×2 | pack board, inner face | C6937107 | 4,248 |
| Thermistor Murata NCP18XH103F03RB (10 k, B 3380 K, 0603) | pack board | C13564 | 191,798 |
| Resettable fuse BHFUSE BSMD1206-075-30V (0.75 A hold, 1.5 A trip) | pack board, in the plus | C976305 | 52,970 |
| 1 k resistor, 0402 | pack board, in the mid-point line | C11702 | basic |

- **Nothing goes in by hand** except the printed parts and the cells.
- **The pack board has two kinds of assembly:** the male is through-hole on the outer face,
  and the clips and small parts are SMT on the inner face.
- **The case, the tray and the joystick's cap** print at home or at JLC's 3D-printing
  service (JLC3DP: SLA resin including a transparent one, ±0.2 mm within 100 mm by its
  capabilities page; MJF nylon).

**Order flow, end to end:**

1. Device board at JLC, as today; buy the discs into the order through Global Sourcing first.
2. Pack board at JLC: 2 layers, 0.8 mm, ENIG; SMT on the inner face, the male through-hole.
3. Case, tray and joystick cap: printed at home or at JLC3DP.
4. Cells: the user's own (the device ships without cells).

## The pack's board

48.0 × 26.8 mm, the same width as the device's board, 0.8 mm thick.

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

- **Out: the two cell clips. In: the pack's contacts** (four discs, two steel spacers),
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

- **Hold and contact** of the male on the steel and the discs (above).
- **Stock: the male is the supply gate.** 20 at JLC (2026-11-01), and LCSC's own stock is
  the same 20. DigiKey and Mouser don't carry Xinyangze. No other maker has a 4-pin magnetic
  male in stock at JLC: the best is 5. The only magnetic 4-pin at DigiKey/Mouser, Mill-Max's
  Maxnetic 873, stands 7.6 mm tall, against this male's 2.8 mm. A run needs a direct order
  from Xinyangze (www.xyzpogopin.com, +86 755 2877 3277), sent to JLC as consigned parts.
  Its magnet-less sibling, YZP0048-20048-04025-02 (C5200680, 216 in stock), has the same
  pins and heights but no magnets, so nothing holds the pack on.
- **The cells are open underneath:** the device rests on them when it lies on its back.
- **Fuse resistance.** The fuse is 90 mΩ fresh and up to 0.5 Ω after it has tripped.
  At full use from a nearly empty pack (~0.4 A) that is a 40–200 mV drop on 1.8 V.
- **The thermistor** is B 3380 K; TI recommends a 103AT type (B 3435 K) on TS. Check
  that the charger's 0 °C and 45 °C points move only slightly with it.
- **The camera head** stands 2.0 mm out of the back.

## Sources

- TI, BQ25172 datasheet (SLUSDY5, June 2022): VSET table, electrical characteristics
  (VOUT_OVP per cell, RON, VSLEEP, TS clamp).
- Xinyangze drawings (via LCSC): YZ103915020T-04025-02 (2015-07-08),
  YZP0048-20048-04025-03 and YZR0158-20050-04025-01 (2022-03-24), YZT0675-20048-04025-01.
- Mill-Max, 1578 target disc drawing (1578-0-57-15-00-00-03-0, SMT pad 0.088 in).
- Würth Elektronik, WA-SMSI 9774010243R datasheet (land 5.3 mm, stencil annulus).
- BAT WIRELESS, BWCD-L4.0W2.0H1.0 datasheet (Aug 2025), via LCSC.
- JLC's parts library and LCSC's product API (stock and part numbers, 2026-10-28).
- JLC3DP, [3D printing capabilities](https://jlc3dp.com/capabilities/3d-printing-service).
