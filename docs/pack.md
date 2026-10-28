# The battery pack (v4)

Every place on the device we found for the AAA cell was ugly: a bare cell on the front,
a hump on the back, or a cover over it ([cell-placement.md](cell-placement.md)). v4 takes
the cells off the device. **Two AAA cells ride in a separate pack that snaps onto the
back with magnets**, and four spring pins in the back carry the power. The device itself
gets thinner, with a flat front and back.

**Status (2026-10-28): concept.** The /v4/ page's 3D preview (`docs/v4/`, from
`enclosure/generate.py`) is built from today's board with the cell clips left off, and
the pins and the device's magnets placed where this page says. Neither board is laid out
for it yet.

## What it comes to

All numbers come from the generated model (`enclosure/generate.py`, 2026-10-28).

| | v3 (one cell on the board) | v4 |
|---|---|---|
| Device thickness | 11.5 mm | **10.73 mm** |
| At the cell | 17.1 mm open, 17.7 mm under its cover | **22.98 mm** with the pack on |
| Cells | one AAA | two AAA in series: twice the energy |
| Back face | the cell stands out | flat; the camera head stands 1.5 mm out |
| Without the cells | — | runs from USB |

The pack is 51.2 × 31.3 × 12.25 mm. It covers the lower middle of the back, from 9 to
41 mm above the bottom edge (the device is 91.2 mm tall), which leaves USB-C and the screw
uncovered.

**Why the device only gets 0.8 mm thinner.** USB-C (3.4 mm tall) set the depth of the
back. It may now stand into the floor: a pocket over it leaves 0.7 mm of floor, and the
plug's opening cuts through the back edge. The next tallest part sets the depth now: the
touch panel's connector, at 2.6 mm. After that come the display and camera connectors
at 2.09 mm and the buzzer at 2.0 mm. A 2.0 mm-tall touch connector would take off
another ~0.5 mm. That connector is a part swap on the board, not done here.

**The camera head.** The OV2640 heads we have found are 5.4–5.9 mm tall. On the thinner
back, the head stands 1.5 mm out (0.7 mm in v3). It is the only thing standing out of
the back. A flat back needs a lower camera module.

**The total is thicker than v3's.** The pack is a full cell's diameter plus the pack's
own board and door (12.25 mm). It sits on top of a 10.73 mm device: 23 mm across the
pack, against 17.7 mm across v3's covered cell. In return the device is thin and flat
on its own, and carries twice the energy when the pack is on.

## How it goes together

```
 device back face  ───────────┐   ┌───────────────────────────────┐   ┌──────────
                              │   │ recess 1.1 mm, pins 0.4 mm    │   │
                              └───┘ under the back face           └───┘
 pack board (1.0 mm,  ═══════════ gold pads up ════════════════════════
   stands in the recess)   magnets ▪           ▪
 tray walls ║   ( AAA )  ( AAA )   ║   cells in series, in the board's clips
 door       ╚══════════════════════╝   slides in from the side, like a remote's
```

- **The pack's own board is its contact face.** It is 1.0 mm thick with gold (ENIG)
  pads on its outer face. The cell clips, a fuse and a thermistor sit on its inner face.
  It stands 1.0 mm proud of the tray and nests in a 1.1 mm recess in the device's back.
  The recess stops the pack sliding, and keeps the pins below the back face when the
  pack is off.
- **Spring pins on the device's board, gold pads on the pack.** The device's board sits
  4.4 mm under its back face. A spring pin spans that gap for free. A flat contact there
  would need a tall solid part. The pins stand 0.4 mm under the back face and compress
  0.6 mm when the pack is on.
- **Four contacts:** the pack's plus, its minus, the point between the two cells, and
  the pack's thermistor. They sit in a 2 × 2 grid at 4 mm, with plus and minus on the
  diagonal.
- **Two pairs of magnets** (N52 discs, 6 × 2 mm). The device's sit in pockets under
  0.4 mm of its back; the pack's are glued to its board between the cells. The east and
  west pairs have opposite poles, so the pack goes on one way round and pushes off the
  other. Mated, a pair is 1.5 mm apart: 0.4 mm of plastic, 0.1 mm of air, 1.0 mm of board.
- **The door** slides in from the east in grooves in the tray's north and south walls,
  over a lip, and stops against the west wall. The cells go in through it.
- **The pack charges in the device**, over USB, as the single cell did.

## Why these parts: what JLC can build

The aim was a pack and a device that JLC assembles, with nothing exotic by hand.

**Ready-made magnetic pogo connectors were rejected.** JLC does stock magnetic
connector pairs from Xinyangze, reflowable (a PA46 body with an NdFeB magnet). But the
stock is tiny. Checked in JLC's own library on 2026-10-28:

- the 4-pin receptacle YZ103915020T-04025-02 (C6276862): 125 in stock;
- the 4-pin male YZP0048-20048-04025-03 (C5126845): 26 in stock;
- the rest: 0–179.

The male and female are from different series (2.54 mm against 2.5 mm pitch). Each
comes in fixed heights of 2–4.8 mm, and its built-in magnets are small. That is enough
for a handful of boards from one supplier, not for a run.

**What we use instead.** Every soldered part comes from deep stock, and the magnets
are separate:

| Part | Where | JLC / LCSC | Stock (2026-10-28) |
|---|---|---|---|
| Spring pin BAT WIRELESS BWCD-L4.0W2.0H1.0 ×4 (gold, 1.5 A, 100 ± 15 g) | device board, back | C2826548 | 16,402 |
| AAA clip MYOUNG MY-AAA-19-J (−) ×2 | pack board, inner face | C6937104 | 5,721 |
| AAA clip MYOUNG MY-AAA-20-J (+) ×2 | pack board, inner face | C6937107 | 4,248 |
| Thermistor Murata NCP18XH103F03RB (10 k, B 3380 K, 0603) | pack board | C13564 | 191,798 |
| Resettable fuse BHFUSE BSMD1206-075-30V (0.75 A hold, 1.5 A trip) | pack board, in the plus | C976305 | 52,970 |
| 1 k resistor, 0402 | pack board, in the mid-point line | any basic part | — |
| N52 disc magnet, 6 × 2 mm ×4 | glued: 2 in the shell, 2 on the pack board | not sold by JLC or LCSC | — |

The same clips are on today's board, so the pack's footprints and 3D models already exist.
The pins' longer siblings, BWCD-L4.5 (C2826546) and BWCD-L5.0 (C2826549, 49,348 in
stock), fit if the back's depth changes. JLC lists C2826548's package as "-", so check
it in JLC's placement preview.

**The case and the pack's tray and door** print at home, or at JLC's 3D-printing
service (JLC3DP). It offers SLA resin, including a transparent one (8001), at ±0.2 mm
within 100 mm by its capabilities page, and MJF nylon. On a filament printer the recess
in the shell's back prints as a ~28 mm bridge. A resin print does not have that problem.

**Order flow, end to end:**

1. Device board at JLC, as today. The spring pins are SMT parts placed with everything
   else.
2. Pack board at JLC: 2 layers, 1.0 mm, ENIG, parts on one face.
3. Case, tray and door: printed at home, or at JLC3DP.
4. Magnets: any magnet seller; glue them in, checking each pair's poles before the glue
   sets.
5. Cells: the user's own (the device ships without cells).

## The pack's board

48.0 × 28.1 mm, the same width as the device's board. The cells' axes are 14.5 mm apart
so the magnets fit between them.

- The south cell's plus goes through the fuse to the plus pad.
- The south cell's minus joins the north cell's plus at the west end: the mid-point.
  It reaches its pad through the 1 k resistor.
- The north cell's minus goes to the minus pad.
- The thermistor sits between the thermistor pad and the minus.

**A coin across the pads cannot short a cell hard.** Plus to minus goes through the
fuse. Any short to the mid-point goes through the 1 k resistor. The thermistor pad is
10 k from the minus.

The board is glued onto the tray's north and south walls (1.0 mm of ledge each) and
never comes out: the cells change through the door.

## What changes on the device's board

The owner lays this out. The positions are where the case model puts the parts; the
area is free on today's board.

- **Out: the two cell clips. In: four spring pins**, at (22.0, 62.2), (26.0, 62.2),
  (22.0, 66.2) and (26.0, 66.2) mm, in board coordinates. In order: plus, mid-point,
  thermistor, minus.
- **Magnets** in the shell at (14.5, 64.2) and (33.5, 64.2). The board under each
  magnet's boss carries only 0402 parts; the model checks their heights.
- **Charger (the BQ25172): set for two cells.** That means 36 kΩ on VSET, where the
  datasheet's table says 2-cell. Its over-voltage stop is 1.65–1.75 V per cell, so
  3.3–3.5 V for the pack.
- **Feed the charger from USB's 5 V, not the 3.3 V rail.** A full two-cell NiMH pack sits
  at about 2.9–3.0 V. That leaves 0.3–0.4 V of headroom from 3.3 V. The charger's own
  charge path drops more than that at 250 mA: its switch is 0.845 Ω typical and up to
  1.45 Ω, so 0.21–0.36 V. It also needs 95–175 mV above the pack to leave sleep.
  From 5 V, it burns about (5 − 2.4 V) × 0.25 A ≈ 0.65 W at the start of a charge. The
  charger regulates its own temperature at 125 °C; 200 mA is the fallback if it runs hot.
- **Thermistor input (TS):** the thermistor moves into the pack. With the pack off, TS
  floats to its 2.3–2.9 V clamp, which the charger reads as too cold: no charging, by
  construction. The transistor that holds TS low until firmware enables charging stays.
- **The cells' voltage:** today the board reads it at both clips. v4 reads the pack's plus
  and minus at the pins, plus the mid-point, through one more divider. Two lithium cells
  (3.6 V) read 1.8 V through today's 1 M / 1 M divider.
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

- **Pin stroke.** The pins' drawing gives the free length (4.0 mm), the barrel (3.0 mm)
  and the plunger (1.0 mm out of the barrel), but no stroke or working height. The model
  compresses them 0.6 mm when mated; check that on a sample.
- **Magnets against pins.** The four pins push back with about 4 × 100 g. The two magnet
  pairs, 1.5 mm apart, must hold the pack against that with a margin. This was not
  computed; it needs a test. 8 × 2 mm discs are the fallback (each boss grows by 2 mm).
- **Magnets near the boost inductor.** The nearer magnet is 11.6 mm from the boost's
  inductor, measured in plan. Measure the inductor with the pack on.
- **Fuse resistance.** The fuse is 90 mΩ fresh and up to 0.5 Ω after it has tripped.
  At full use from a nearly empty pack (~0.4 A) that is a 40–200 mV drop on 1.8 V.
- **The door** may need a detent to stay shut.
- **The thermistor** is B 3380 K; TI recommends a 103AT type (B 3435 K) on TS. Check
  that the charger's 0 °C and 45 °C points move only slightly with it.
- **The camera head** stands 1.5 mm out of the back.

## Sources

- TI, BQ25172 datasheet (SLUSDY5, June 2022): VSET table, electrical characteristics
  (VOUT_OVP per cell, RON, VSLEEP, TS clamp).
- BAT WIRELESS, BWCD-L4.0W2.0H1.0, -L4.5W2.0H2.3 and -L5.0W2.0H2.5 datasheets
  (Aug 2025), via LCSC.
- Xinyangze, YZ103915020T-04025-02 specification (2021), via LCSC.
- JLC's parts library (stock and part numbers, 2026-10-28), queried the way
  `scripts/jlcStock.py` does.
- JLC3DP, [3D printing capabilities](https://jlc3dp.com/capabilities/3d-printing-service).
