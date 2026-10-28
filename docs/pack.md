# The battery pack (v4)

Every place on the device we found for the AAA cell was ugly: a bare cell on the front,
a hump on the back, or a cover over it ([cell-placement.md](cell-placement.md)). v4 takes
the cells off the device. **Two AAA cells ride in a separate pack that snaps onto the
back, high up, just below the camera.** A ready-made magnetic pogo connector pair joins
and holds it: no loose magnets (owner, 2026-10-28). The device itself gets thinner, with
a flat front and back.

**Status (2026-10-28): concept.** The /v4/ page's 3D preview (`docs/v4/`, from
`enclosure/generate.py`) is built from today's board with the cell clips left off; the
generator puts the connector at `CONN_X`, midway between the cells. The pack board is
`pack/` (built by `pack/makePack.py`, order set in `production/pack-order/`); the device
board is being re-laid for it.

## What it comes to

All numbers come from the generated model (`enclosure/generate.py`, 2026-10-28).

| | v3 (one cell on the board) | v4 |
|---|---|---|
| Device thickness | 11.5 mm | **10.73 mm** |
| At the cell | 17.1 mm open, 17.7 mm under its cover | **22.98 mm** with the pack on |
| Cells | one AAA | two AAA in series: twice the energy |
| Back face | the cell stands out | flat; a window for the connector; the camera head stands 1.5 mm out |
| Without the cells | — | runs from USB |

The pack is 51.2 × 37.5 × 12.25 mm. It spans the back from 21 to 58.5 mm below the top
edge (the device is 91.2 mm tall), just south of the camera. Its two cells are 21.1 mm
apart, axis to axis. The male's through-hole joints on the pack board's inner face pass
between the east clips' arms, which sets that spacing. With the cells closer, the pins
would land on the clips' steel bases.

- **The camera sees past it.** Its north edge sits outside a 70° cone from the lens's rim,
  measured at the pack's far face. The lens is about 66°, and the stock candidate module
  68.7° diagonal; the cone is taken as full all round.
- **It is open underneath.** The cells sit in their clips with nothing below them, as v3's
  cell did, which keeps the pack to a cell's diameter plus its board and the connector's
  stack. A door would add about 1.5 mm.

**Why the device only gets 0.8 mm thinner.** USB-C (3.4 mm tall) set the depth of the
back. It may now stand into the floor: a pocket over it leaves 0.7 mm of floor, and the
plug's opening cuts through the back edge. The next tallest part now sets the depth: the
touch panel's connector, at 2.6 mm. After that come the display and camera connectors at
2.09 mm and the buzzer at 2.0 mm. A 2.0 mm-tall touch connector would take off another
~0.5 mm.

**The camera head.** The OV2640 heads we have found are 5.4–5.9 mm tall. On the thinner
back, the head stands 1.5 mm out (0.7 mm in v3). It is the only thing standing out of the
back; a flat back needs a lower camera module.

## How it goes together

```
 device board (back)  ═══ receptacle (2.0 mm, its own magnets) ════════════
                            ▲ ▲ ▲ ▲   0.3 mm gap; pins at 3.1 mm working height
 device back      ───┐  ┌───┴─┴─┴─┴───┐  window: the male's body drops through
                     └──┤  male body  ├──────────────────────────── back face
 pack board 0.8 mm  ════╧═════════════╧══ 0.7 mm off the back face, inside the tray's rim
 tray (frame)       ║  ( AAA )   ( AAA )  ║   cells in series, in clips, open underneath
```

- **The device's half: Xinyangze's 4-pin SMT magnetic receptacle**,
  YZ103915020T-04025-02 (LCSC C6276862), on the board's back.
  - It is 22.3 × 7.7 mm and 2.0 mm tall, with flat gold contacts at 2.54 mm.
  - It has NdFeB magnets in its ends and two locating bosses underneath, which need slots
    in the board.
  - It sits east of the MCU at (43.6, 37.6), long axis north-south.
- **The pack's half: Xinyangze's 4-pin magnetic male**, YZP0048-20048-04025-03 (LCSC
  C5126845, through-hole), on the pack board's outer face.
  - Its body is 21.2 × 6.86 mm with a 5.03 mm tab, 2.8 mm thick, with magnets in its ends.
  - Its pins are 4.1 mm free and 3.1 mm working, 30 ± 20 g each.
  - It drops through a window in the device's back. Mated, its face stops 0.3 mm short of
    the receptacle's, the pins at working height. Its body keys the pack in the window.
- **The two halves' magnets hold the pack.** There are no loose magnets.
- **The pair sets the thickness.** The receptacle (2.0 mm) plus the male's working height
  (3.1 mm) put the pack board 0.7 mm outside the device's back face. The tray's rim takes
  up that gap. The device's back has no recess, just the window.
- **Four contacts:** the pack's plus, its minus, the point between the two cells, and the
  pack's thermistor. They run in a column, plus at the north end and minus at the south.
- **The pack charges in the device**, over USB, as the single cell did.

**Where it lands on the board.** The receptacle needs a bare 22.3 × 7.7 mm patch of the
board's back. The first site, west of the MCU beside the SE050, could not work. The SE050's
tied VOUT/VCC pins clash with the contacts there. The pack's plus and minus also could not
leave that column on either layer without ripping far more than locally. East of the MCU
is clear except for the power-key network: two SOT-23s (D_OR, Q_BTN) and five passives.
The cell clips are gone, so the chin's back is empty, and that network moves there. From
the east, plus and minus have a near-empty run down to the charger and the boost.

## Why this pair

JLC lists 13 magnetic pogo connectors, all from Xinyangze. The stock below was checked
2026-10-28; LCSC's figure is where it differs from JLC's.

| Part | What | JLC / LCSC stock |
|---|---|---|
| YZ103915020T-04025-02 (C6276862) | 4-pin receptacle, SMT, 2.0 mm, 2.54 pitch | **125** (device) |
| YZP0048-20048-04025-03 (C5126845) | 4-pin male, through-hole, 3.1 mm working | **26** (pack) |
| YZR0158-20050-04025-01 (C5126843) | 4-pin male, SMT, 4.0 mm working | 0 / 200 |
| YZT0675-20048-04025-01 (C5126846) | 4-pin receptacle, through-hole (the male's own family) | 0 |
| the 2-, 3- and 5-pin parts | | 0–179 |

- **The device's half must be SMT.** The contacts sit under the screen, where
  through-hole tails would hit the display. That rules out the male's own family's
  receptacle.
- **So the pair crosses two families.** Their pitches differ (2.54 against 2.50 mm: the
  outer pins land 0.06 mm off their contacts). Their magnets are about 14.6 and 15.4 mm
  apart. Whether those magnets line up and attract is for samples to show, before
  anything is ordered.
- **The other designs considered** (2026-10-28):
  - the SMT male alone on the pack, onto gold pads on the device board: 0.6 mm thinner,
    but its magnets had no partner, so loose magnets were needed;
  - plain SMT spring pins (BAT WIRELESS BWCD-L4.0W2.0H1.0, C2826548, 16,402 in stock)
    onto gold pads on the pack: 1.1 mm thinner, with loose magnets.

  The owner's goal was no loose magnets.

## What JLC builds

| Part | Where | JLC / LCSC | Stock (2026-10-28) |
|---|---|---|---|
| Magnetic receptacle, Xinyangze YZ103915020T-04025-02 | device board, back | C6276862 | 125 |
| Magnetic male, Xinyangze YZP0048-20048-04025-03 (through-hole) | pack board, outer face | C5126845 | 26 |
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

1. Device board at JLC, as today, with the receptacle placed like any other part.
2. Pack board at JLC: 2 layers, 0.8 mm, ENIG; SMT on the inner face, the male through-hole.
3. Case, tray and joystick cap: printed at home or at JLC3DP.
4. Cells: the user's own (the device ships without cells).

## The pack's board

48.0 × 28.1 mm, the same width as the device's board, 0.8 mm thick.

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

- **Out: the two cell clips. In: the magnetic receptacle**, east of the MCU, and slots for
  its locating bosses. The power-key network (D_OR, Q_BTN and its passives) moves off its
  land.
- **Charger (the BQ25172): set for two cells.** That means 36 kΩ on VSET, where the
  datasheet's table says 2-cell. Its over-voltage stop is 1.65–1.75 V per cell, so
  3.3–3.5 V for the pack.
- **Feed the charger from USB's 5 V, not the 3.3 V rail.** A full two-cell NiMH pack sits
  at about 2.9–3.0 V. That leaves 0.3–0.4 V of headroom from 3.3 V. The charger's own
  charge path drops more than that at 250 mA: its switch is 0.845 Ω typical and up to
  1.45 Ω, so 0.21–0.36 V. It also needs 95–175 mV above the pack to leave sleep.
  From 5 V, it burns about (5 − 2.4 V) × 0.25 A ≈ 0.65 W at the start of a charge. The
  charger regulates its own temperature at 125 °C; 200 mA is the fallback if it runs hot.
  The pull-up that holds charging off until firmware allows it moves to 5 V too, so a pack
  can't see a short charge before the 3.3 V rail comes up.
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
- **Power key** (the joystick's centre press, through a PNP from the cells): unchanged in
  function; its parts move. With the pack off, the device runs from USB only.

**Firmware:** read each cell on its own: plus minus mid-point, and mid-point minus minus.
Refuse to charge unless both cells look NiMH and match each other, so a pack with one
alkaline beside one NiMH is never charged. Taking the pack off resets the verdict; an
open thermistor line shows that the pack is off.

## Open: bench checks before anything is ordered

- **Samples of the pair.** Check that the two families' magnets line up and attract, and
  that they hold the pack firmly against four pins at 30 ± 20 g each. Check the outer
  pins against their contacts (0.06 mm off by the drawings).
- **Stock.** The male has 26 at JLC; a run needs more, or a direct order from Xinyangze.
- **The cells are open underneath:** the device rests on them when it lies on its back.
- **Fuse resistance.** The fuse is 90 mΩ fresh and up to 0.5 Ω after it has tripped.
  At full use from a nearly empty pack (~0.4 A) that is a 40–200 mV drop on 1.8 V.
- **The thermistor** is B 3380 K; TI recommends a 103AT type (B 3435 K) on TS. Check
  that the charger's 0 °C and 45 °C points move only slightly with it.
- **The camera head** stands 1.5 mm out of the back.

## Sources

- TI, BQ25172 datasheet (SLUSDY5, June 2022): VSET table, electrical characteristics
  (VOUT_OVP per cell, RON, VSLEEP, TS clamp).
- Xinyangze drawings (via LCSC): YZ103915020T-04025-02 (2015-07-08),
  YZP0048-20048-04025-03 and YZR0158-20050-04025-01 (2022-03-24), YZT0675-20048-04025-01.
- BAT WIRELESS, BWCD-L4.0W2.0H1.0 datasheet (Aug 2025), via LCSC.
- JLC's parts library and LCSC's product API (stock and part numbers, 2026-10-28).
- JLC3DP, [3D printing capabilities](https://jlc3dp.com/capabilities/3d-printing-service).
