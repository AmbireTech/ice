# The battery pack (v4)

Every place on the device we found for the AAA cell was ugly: a bare cell on the front,
a hump on the back, or a cover over it ([cell-placement.md](cell-placement.md)). v4 takes
the cells off the device. **Two AAA cells ride in a separate pack that snaps onto the
back, high up, just below the camera**, held by magnets and connected through a
ready-made magnetic pogo connector. The device itself gets thinner, with a flat front and
back.

**Status (2026-10-28): concept.** The /v4/ page's 3D preview (`docs/v4/`, from
`enclosure/generate.py`) is built from today's board with the cell clips left off; the
generator places the connector's pads and the magnets itself. The two boards are being
laid out on their own branches for review.

## What it comes to

All numbers come from the generated model (`enclosure/generate.py`, 2026-10-28).

| | v3 (one cell on the board) | v4 |
|---|---|---|
| Device thickness | 11.5 mm | **10.73 mm** |
| At the cell | 17.1 mm open, 17.7 mm under its cover | **21.88 mm** with the pack on |
| Cells | one AAA | two AAA in series: twice the energy |
| Back face | the cell stands out | flat; the camera head stands 1.5 mm out |
| Without the cells | — | runs from USB |

The pack is 51.2 × 31.3 × 11.15 mm. It spans the back from 20 to 51 mm below the top edge
(the device is 91.2 mm tall), just south of the camera.

- **The camera sees past it.** Its north edge sits outside a 70° cone from the lens's rim,
  measured at the pack's far face. The lens is about 66°, and the stock candidate module
  68.7° diagonal; the cone is taken as full all round.
- **It is open underneath.** The cells sit in their clips with nothing below them, as v3's
  cell did, which keeps the pack to a cell's diameter plus its board. A door would add
  about 1.5 mm.

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
 device board (back)   ══ gold pads ══════════════════════════════════════
                          ▲ ▲ ▲ ▲   pins press here (4.0 mm working height)
 device back      ───┐  ┌─┴─┴─┴─┴─┐  window: the connector's body drops through
                     └──┤connector├──────────────────────────────── 0.5 mm recess
 pack board 0.8 mm  ════╧═════════╧════════ magnets ▪ ════════ ▪ ═══
 tray (frame)       ║  ( AAA )   ( AAA )  ║   cells in series, in clips, open underneath
```

- **The contacts are a ready-made magnetic connector on the pack** (owner, 2026-10-28):
  Xinyangze's 4-pin SMT magnetic male, YZR0158-20050-04025-01 (LCSC C5126843), on the
  pack board's outer face.
  - Its body is 21.2 × 6.86 mm (9.17 mm over a tab) and 2.8 mm thick, with NdFeB magnets
    in its ends.
  - Its pins stand 2.2 mm out of the body with the pack off: 5.0 mm free, 4.0 working,
    3.7 pressed, 60 ± 20 g each, 1 A.
  - Mated, the body drops through a window in the device's back. The pins press on four
    plain gold pads on the device board's back, so the device side is copper only. The
    body keys the pack in its window.
- **The pack board stands 0.4 mm into a 0.5 mm recess** in the device's back. The 4.0 mm
  working height sets that depth, and it leaves 0.7 mm of floor behind the recess.
- **Four contacts:** the pack's plus, its minus, the point between the two cells, and the
  pack's thermistor. They run in a column at 2.5 mm, with plus and minus at the ends.
- **Two pairs of magnets.** The device has two 8 × 1.5 mm N52 discs in pockets under
  0.4 mm of its back. The pack has two 6 × 2 mm discs glued to its board between the
  cells. The east and west pairs have opposite poles, so the pack goes on one way only.
  Mated, a pair is 1.3 mm apart. The connector's own magnets have nothing to pull on in
  the device: the board has no steel there, and JLC's SMT steel parts are stainless 301
  shielding clips, which magnets barely pull.
- **The pack charges in the device**, over USB, as the single cell did.

**Where it lands on the board.** The top half of the board's back is crowded with the MCU
and the touch, display and camera connectors. The generator searches the board's real
parts for the connector's place:

- nothing taller than 1.0 mm under the connector's body, which floats 1.2 mm off the board;
- bare board under its pins;
- the fewest small parts to move.

It lands at x 4.5, y 22–43 mm (beside the SE050 secure element), long axis north-south. The
SE050's supply decoupling cap (`C_SEVCC`) has to move off the pads. The magnets land at
x 16.3 and 34.05, between the cells, 38.6 mm from the boost inductor in plan.

## Why this connector, and not the others

JLC lists 13 magnetic pogo connectors, all from Xinyangze. The stock below was checked
2026-10-28; LCSC's figure is where it differs from JLC's.

| Part | What | JLC / LCSC stock |
|---|---|---|
| YZR0158-20050-04025-01 (C5126843) | 4-pin male, SMT, 4.0 mm working | 0 / **200** |
| YZP0048-20048-04025-03 (C5126845) | 4-pin male, through-hole, 3.1 mm working | 26 |
| YZ103915020T-04025-02 (C6276862) | 4-pin receptacle, SMT, 2.0 mm, 2.54 pitch | 125 |
| YZT0675-20048-04025-01 (C5126846) | 4-pin receptacle, through-hole | 0 |
| the 2-, 3- and 5-pin parts | | 0–179 |

- **Two halves stack too high.** A receptacle and a male stack 5–6 mm. The device's back
  gives 4.4 mm for free, so a full pair puts the pack board 0.7–1.6 mm outside the back:
  about +1.7 to +2.6 mm in all.
- **The device's half must be SMT.** The contacts sit under the screen, where
  through-hole tails would hit the display.
- **The male alone on the pack** pressing on pads costs +0.6 mm against plain spring
  pins, and leaves the device with no connector part at all. That is what v4 uses.

**Plain spring pins** (BAT WIRELESS BWCD-L4.0W2.0H1.0, C2826548, 16,402 in stock) on the
device with pads on the pack were the first cut. They are 0.6 mm thinner and from much
deeper stock, but with no magnets or body of their own.

## What JLC builds, and what goes in by hand

| Part | Where | JLC / LCSC | Stock (2026-10-28) |
|---|---|---|---|
| Magnetic pogo male, Xinyangze YZR0158-20050-04025-01 | pack board, outer face | C5126843 | 0 at JLC, 200 at LCSC |
| AAA clip MYOUNG MY-AAA-19-J (−) ×2 | pack board, inner face | C6937104 | 5,721 |
| AAA clip MYOUNG MY-AAA-20-J (+) ×2 | pack board, inner face | C6937107 | 4,248 |
| Thermistor Murata NCP18XH103F03RB (10 k, B 3380 K, 0603) | pack board | C13564 | 191,798 |
| Resettable fuse BHFUSE BSMD1206-075-30V (0.75 A hold, 1.5 A trip) | pack board, in the plus | C976305 | 52,970 |
| 1 k resistor, 0402 | pack board, in the mid-point line | any basic part | — |
| N52 discs: 2 × 8 × 1.5 mm (shell), 2 × 6 × 2 mm (pack board) | glued | not sold by JLC or LCSC | — |

- **The pack board is assembled on both faces:** the connector on the outer face, the
  clips and the small parts on the inner. Hand-soldering the connector keeps JLC's
  assembly one-sided.
- **The connector has to come from LCSC.** It is in LCSC's stock, not JLC's own, so JLC
  orders it in (pre-order / global sourcing).
- **The case, the tray and the joystick's cap** print at home or at JLC's 3D-printing
  service (JLC3DP: SLA resin including a transparent one, ±0.2 mm within 100 mm by its
  capabilities page; MJF nylon). The recess is only 0.5 mm deep, so on a filament printer
  its bridge is short work.

**Order flow, end to end:**

1. Device board at JLC, as today: the pads are copper, nothing to place.
2. Pack board at JLC: 2 layers, 0.8 mm, ENIG, parts on both faces (or the connector by
   hand).
3. Case, tray and joystick cap: printed at home or at JLC3DP.
4. Magnets: any magnet seller; glue them, checking each pair's poles before the glue sets.
5. Cells: the user's own (the device ships without cells).

## The pack's board

48.0 × 28.1 mm, the same width as the device's board, 0.8 mm thick. The cells' axes are
14.5 mm apart so the magnets fit between them.

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

- **Out: the two cell clips. In: four gold pads**, 2.2 mm, at x 4.50, y 28.96 (plus),
  31.46 (mid-point), 33.96 (thermistor) and 36.46 (minus), in board coordinates.
  - Under the connector's body (x 1.07–7.93, y 22.11–43.31, and its tab east to x 10.24),
    nothing may stand taller than 1.0 mm.
  - The SE050's decoupling cap moves off the pads, staying at its pin.
- **Charger (the BQ25172): set for two cells.** That means 36 kΩ on VSET, where the
  datasheet's table says 2-cell. Its over-voltage stop is 1.65–1.75 V per cell, so
  3.3–3.5 V for the pack.
- **Feed the charger from USB's 5 V, not the 3.3 V rail.** A full two-cell NiMH pack sits
  at about 2.9–3.0 V. That leaves 0.3–0.4 V of headroom from 3.3 V. The charger's own
  charge path drops more than that at 250 mA: its switch is 0.845 Ω typical and up to
  1.45 Ω, so 0.21–0.36 V. It also needs 95–175 mV above the pack to leave sleep.
  From 5 V, it burns about (5 − 2.4 V) × 0.25 A ≈ 0.65 W at the start of a charge. The
  charger regulates its own temperature at 125 °C; 200 mA is the fallback if it runs hot.
- **Thermistor input (TS):** the thermistor moves into the pack, and the board's own goes.
  With the pack off, TS floats to its 2.3–2.9 V clamp, which the charger reads as too cold:
  no charging, by construction. The transistor that holds TS low until firmware enables
  charging stays.
- **The cells' voltage:** the board reads the pack's plus and minus as before, and the
  mid-point through one more 1 M / 1 M divider into an ADC pin. Two lithium cells (3.6 V)
  read 1.8 V.
- **Boost (the LTC3539-2):** its input becomes 1.8–3.6 V instead of 0.9–1.8 V. It keeps
  regulating when its input is above 3.3 V (two lithium cells), which is why it was
  chosen. At twice the voltage it draws half the current from the cells.
- **Reverse-cell FET:** stays. The cells can still go into the pack's clips backwards.
- **Power key** (the joystick's centre press, through a PNP from the cells): unchanged.
  With the pack off, the device runs from USB only.

**Firmware:** read each cell on its own: plus minus mid-point, and mid-point minus minus.
Refuse to charge unless both cells look NiMH and match each other, so a pack with one
alkaline beside one NiMH is never charged. Taking the pack off resets the verdict; an
open thermistor line shows that the pack is off.

## Open: bench checks before anything is ordered

- **Magnets against the pins.** The four pins push back with 4 × 60 ± 20 g at working
  height. The two magnet pairs, 1.3 mm apart, must hold the pack against that with a
  margin. This was not computed; it needs a test.
- **The connector's tab and orientation.** Its drawing shows a 5.03 mm tab off one long
  side, modelled here as part of its body. Check against a sample, and check the
  connector's land against JLC's placement preview.
- **The connector's own magnets** have no partner and may tug the pack sideways in its
  window.
- **The cells are open underneath:** the device rests on them when it lies on its back.
- **Fuse resistance.** The fuse is 90 mΩ fresh and up to 0.5 Ω after it has tripped.
  At full use from a nearly empty pack (~0.4 A) that is a 40–200 mV drop on 1.8 V.
- **The thermistor** is B 3380 K; TI recommends a 103AT type (B 3435 K) on TS. Check
  that the charger's 0 °C and 45 °C points move only slightly with it.
- **The camera head** stands 1.5 mm out of the back.

## Sources

- TI, BQ25172 datasheet (SLUSDY5, June 2022): VSET table, electrical characteristics
  (VOUT_OVP per cell, RON, VSLEEP, TS clamp).
- Xinyangze drawings (via LCSC): YZR0158-20050-04025-01 (2022-03-24),
  YZP0048-20048-04025-03, YZT0675-20048-04025-01, YZ103915020T-04025-02 (2015).
- BAT WIRELESS, BWCD-L4.0W2.0H1.0, -L4.5W2.0H2.3 and -L5.0W2.0H2.5 datasheets
  (Aug 2025), via LCSC.
- JLC's parts library and LCSC's product API (stock and part numbers, 2026-10-28).
- JLC3DP, [3D printing capabilities](https://jlc3dp.com/capabilities/3d-printing-service).
