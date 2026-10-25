# One AAA cell: cells, runtime, charging

Proto 1 drops the 1S LiPo pouch for a single AAA cell held in two clips. Since v3
the clips are on the board's back, under a hump in the case
([cell-placement.md](cell-placement.md)). The cell can be rechargeable (NiMH) or not
(lithium or alkaline). This is the research behind
that choice and the power circuit that follows from it. The circuit itself:
[design.md](design.md#power-one-aaa-cell); the netlist: `src/blocks/Power.tsx`.

Where the cell sits is decided in [cell-placement.md](cell-placement.md): from proto 1's
front clips it moves to the back, inside the case (2026-10-25, not yet on the board).

Numbers come from manufacturer datasheets and application manuals and from
lygte-info.dk's discharge tables (constant current, one sample per model, 2013–2019).
Where a value was read off a plotted curve it says so. Sources are at the end.

## Which cells

| | Cell | Why |
|---|---|---|
| Rechargeable | **eneloop pro BK-4HCDE** or **Fujitsu HR-4UTHC** (~930–950 mAh) for runtime; standard **eneloop BK-4MCDE** (~750–800 mAh) for 2000+ cycles and 10-year shelf | LSD NiMH: ~40–70 mΩ, flat 1.2 V plateau. The board is designed to the standard eneloop as the worst case. |
| Primary | **Energizer Ultimate Lithium L92** (Li-FeS2) | At our currents it delivers 2.3–3.3× the energy of the best alkaline (1.5–1.7 Wh vs 0.5–0.7 Wh at 0.5 A), 4 g lighter, works to −40 °C, 20+ year shelf, no hydrogen. Its 1.79–1.83 V resting voltage also makes it the easiest cell to refuse to charge. |
| Works, not recommended | Alkaline | 150–300 mΩ fresh and rising: at ~0.6 A it gives a third of its 0.2 A energy, and once half used it cannot deliver the full load at all (a source gives at most Voc²/4R: ~0.65 W for a half-used AAA, against ~0.8 W drawn). Fine for idle use. |
| Works, not recommended | 10440 Li-ion (3.7 V) | No more energy than an eneloop pro (1.05–1.18 Wh), never charged by this board, and lengths spread from 42.9 mm (flat top) to ~46.5 mm (protected), which end clips tolerate poorly. The boost keeps regulating with it, so it cannot harm the board. |

Every candidate measures 10.1–10.3 mm across and 44.1–44.3 mm long (alkaline per
Energizer E92: 43.3–44.5 mm).

## How long it runs

Loads at 3.3 V (estimates, not measurements): **full use** (backlight full,
camera scanning, radio on) ~0.64 W; **signing** (backlight full, secure
elements busy) ~0.50 W; **idle UI** (backlight half) ~0.28 W. Drawn from the cell
through the boost at ~85 % (~88 % for the L92, which sits higher under load); the
cell's energy is lygte's table interpolated at the resulting current.

| Cell | Full use | Signing | Idle UI |
|---|---|---|---|
| eneloop BK-4MCCE (750 mAh) | 1.2 h | 1.5 h | 2.9 h |
| eneloop pro BK-4HCDE | 1.5 h | 1.9 h | 3.5 h |
| Fujitsu HR-4UTHC | 1.4 h | 1.85 h | 3.4 h |
| Energizer L92 | 2.0–2.25 h | 2.7–3.0 h | 5.4 h |
| Best alkaline (Duracell Plus Power) | 0.8 h | 1.1 h | 2.85 h |
| Weakest alkaline measured | 0.6 h | 0.8 h | 2.2 h |

About ±20 %: one sample per cell, constant-current tables against a
constant-power load (optimistic for alkaline, whose voltage slopes), boost
efficiency at 0.9–1.1 V likely under 85 %, and every 10 mΩ in the clips and the
inductor costs ~1 % at ~1.2 A. Cold: NiMH and alkaline fall off below 0 °C; the
L92 barely changes.

The backlight dominates. Its pump runs from 3.3 V, too close to the LEDs' ~3.1 V
for its 1x mode at full current, so at full brightness it draws ~1.5× the LED
current (~170 mA). Firmware dimming is the biggest runtime lever there is.

## Power circuit in short

- **Boost: LTC3539-2.** Starts at 0.88 V (max), runs to 0.5 V, 2 A switch, true
  output disconnect, and keeps regulating when the cell is *above* 3.3 V. No common
  boost does the last: a TPS61021A or MAX1722x passes a 4.2 V Li-ion straight onto
  the 3.3 V rail (the STM32U585's absolute maximum is 4.0 V). Nothing has to trip,
  so no threshold has to be right. $3.33, one source (ADI), 2,400 at JLC.
- **USB: TLV62569P buck to 3.3 V**, and a **TPS2116** that picks USB first and the
  boosted cell otherwise, switching on the buck's power-good, whose pull-up is a
  divider from VBUS (a pull-up to the buck's own output would switch the TPS2116 back
  to the dead USB input after an unplug). Both sit at 3.3 V; the TPS2116 blocks
  reverse current into either.
- **Reverse cell: AO3416 in the cell's low side.** A clip does not stop a cell
  going in backwards, and the boost's input is rated to −0.3 V. The FET's gate
  follows the cell for start-up (its threshold is 0.4–1.1 V; start-up at 1.05 V is a bench check) and then the boost's
  output (~3 V, ~25 mΩ).
- **Charger: BQ25172**, NiMH-only, from the USB 3.3 V rail (0.48 W at 250 mA
  instead of 0.9 W from 5 V). Off by default: a transistor holds its TS pin low
  until the MCU enables it, and again whenever the MCU is in reset.
- **No battery gauge.** The MAX17048 is a Li-ion gauge. Firmware reads the cell's
  voltage at both clips and its temperature.

## Charging only a NiMH cell

No single electrical test reliably separates a part-used alkaline cell from a
NiMH cell, and nobody publishes false-accept rates (not the DS2711's maker, not the
patents, not commercial "alkaline-rejecting" chargers). So the rule is: **refuse
to charge unless the cell is positively identified as NiMH**, by layers.

What the hardware guarantees on its own:

- The charger starts only on a cell resting **below ~1.33 V** (its VRECHG) and
  stops on any cell **above 1.65–1.75 V** (VOUT_OVP). An L92 (1.79–1.83 V), a fresh
  alkaline (~1.6 V) or any Li-ion is never charged.
- It never runs while the MCU is off or in reset (TS held low by default).
- **4 h safety timer** at the fast rate (~125 % of an 800 mAh cell).
- Its own NTC on the cell suspends charging outside **0–45 °C**.
- With charging off it leaks 0.35 µA into the cell; the board's own sense and gate
  resistors add ~1 µA. Energizer allows a lithium primary 2 µA of reverse current.

What firmware adds before enabling it:

1. **Resting voltage ≤ ~1.45 V** (a NiMH just off a charger reads up to ~1.45 V;
   fresh alkaline ~1.6 V; L92 1.7–1.83 V).
2. **Internal resistance** from a charge-current step (83 → 250 mA while on USB,
   with the boost idle, so the cell carries nothing else): NiMH reads ~40–100 mΩ
   (more when aged), primaries ≥ 140 mΩ. The gap is only 1.5–2× against an L92,
   and clip resistance eats into it: measure at both clips (the board senses the
   cell at the + clip and at the − clip, not through the FET) and calibrate the
   instrument with a solid metal dummy cell before trusting any threshold.
3. **A short trial charge** at 83 mA, watching both the voltage rise and its rate
   (the strongest single test in the literature: NiMH rises slowly, alkaline fast).
4. Optionally, **ask the user** once per cell.
5. **Any cell removal resets the verdict.** With no cell, the charger's output
   climbs straight into its over-voltage stop; firmware sees the jump.

Charging itself: either 0.1 C (83 mA) for 15–16 h, which is how Panasonic rates
eneloop and what an LSD NiMH tolerates as overcharge, or 0.3 C (250 mA, ~3.5 h)
ended by firmware on a voltage peak/plateau and dT/dt, with the timer and the NTC
behind it. Panasonic's −ΔV method starts at 0.5 C; at 0.3 C the dip is weak,
which is why the fast rate leans on temperature and the timer.

## Standby: off is the resting state

**Off** (the boost shut down), the board draws about 1 µA from a 1.2 V cell (≤ 2.5 µA
at datasheet maxima; ~3.5 µA with a 3.7 V 10440, whose sense divider alone takes
2.1 µA): the 1M/1M sense divider (~0.6 µA), the charger's quiescent draw on its output
(0.35 µA typ), the boost in shutdown (0.01 µA typ). The reverse-FET gate's diode from
the boost's output leaks picoamps, the joystick's power-on transistor nanoamps. That would
take more than 35 years to empty any of these cells, so off is limited by the cell's
own self-discharge (standard eneloop: 70 % left after 10 years, ~2.6 µA average;
eneloop pro ~16 µA in year one; L92 25-year shelf life) — not by the board.

The power key is the **joystick's centre press, held**: a PNP from the cell pulls
the boost's enable up exactly like a power button did, and the MCU reads the same
press as BTN_N. There is no separate power button.

**Asleep** (boost on, MCU in Stop) would last only 1–3 months as drawn, and about
6–9 months on a standard eneloop (14–23 on an L92) with these changes, none applied:
the 100k pull-down on PWR_HOLD to 1–10 M (with SHDN's 1M to ~10M and its 1 µF to
~0.33 µF); the SE050's ENA pull-up turned into a pull-down; the OPTIGA (70 µA asleep)
and the display/touch (~62 µA) on switched rails; the airgap sense resistors scaled
up. The boost's own no-load input current (~66 µA at 1.2 V, its data sheet's curve)
then dominates. So firmware should use "asleep" only for a short window after use
(10–30 min) and then switch fully off.

**IMU wake on motion** ("pick up to start") was looked at and left out of proto 1:
with the boost off it needs its own always-on supply (a MAX17220 nanopower boost:
the only accelerometer that runs from the cell directly, the ADXL367, is unbuyable
at JLC and quits at 1.1 V), each wake is a 1–2.5 s cold boot, and a pocket or bag
would wake it 10–100 times a day. If wanted later: a LIS2DW12 (JLC, ~20k in stock,
$1, ~0.4 µA) on the 3.3 V rail for pick-up-to-wake during the asleep window.

## The other parts this revision looked at

- **Clips**: MYOUNG MY-AAA-19-J (−, spring) and MY-AAA-20-J (+, leaf). SMT, they grip
  the cell body (it cannot shake out sideways), 4–6k at JLC. With the maker's land
  (pads 28 mm apart) the bases span 47 mm, inside the 48 mm board. No catalogue AAA
  contact pair in stock is shorter. Every body-gripping clip touches the cell's can
  through its label: a torn label on an alkaline cell (whose can is +) can short it
  through the − clip.
- **Sound**: a true MEMS microspeaker needs a 15–30 V drive amplifier and none is
  stocked at JLC. A magnetic transducer driven by one MOSFET from a timer pin does
  the beeps: the Huaneng MLT-5020 (5.0 × 5.5 × 2.0 mm, 75 dB at 10 cm, port on
  top, 58k at JLC). The louder 7.5 mm parts (KLJ-7525-3627 or MLT-7525, 80–85 dB)
  have a 9 mm land, which no free spot on the board takes.
- **Joystick**: every reflowable four-way-plus-push part at JLC is 7.5 mm or more;
  the ALPS SKRHAAE010 (pure SMD, no holes through the board) sits on the front below
  the cell. It is digital (on/off contacts), not an analog stick: the analog ones at
  JLC (ALPS RKJXV) are 13 mm through-hole parts.

## Open risks

1. The detection thresholds above are from literature on 2004-era cells and other
   holders. They must be set on real cells in these clips.
2. The LTC3539's output current with a 10440 (input above output) is only a graph
   in the datasheet: measure it, and the rail when a cell is inserted.
3. The USB-to-cell handover was not measured: the rail may dip to ~3.0 V for the
   few microseconds the TPS2116 takes.
4. The clips' installed length (47–48 mm) was computed from drawings: check with a
   sample and a 44.5 mm cell before the order.
5. The runtime table is an estimate built on estimated loads; measure the board.

## Sources

- lygte-info.dk battery reviews (`lygte-info.dk/review/batteries2012/`): Eneloop AAA
  BK-4MCCE 2019, Eneloop pro AAA BK-4HCDE, Fujitsu AAA HR-4UTHC and HR-4UTC, Ikea
  Ladda AAA 900, Duracell Plus Power / Ultra Power AAA, Varta Energy AAA, Panasonic
  Pro Power AAA, Energizer Ultimate Lithium AAA (2013, 2019), Vapcell INR10440,
  Soshine 10440, Efest IMR10440.
- Energizer: [L92](https://data.energizer.com/pdfs/l92.pdf),
  [E92](https://data.energizer.com/pdfs/e92.pdf), and the
  [lithium](https://data.energizer.com/pdfs/lithiuml91l92_appman.pdf),
  [alkaline](https://data.energizer.com/pdfs/alkaline_appman.pdf) and
  [NiMH](https://data.energizer.com/pdfs/nickelmetalhydride_appman.pdf) application manuals.
- Panasonic: [BK-4MCC datasheet](https://eneloop101.com/wp-content/uploads/BK-4MCC-AE.pdf),
  [Ni-MH handbook](https://energy.panasonic.com/dam/master/pdf/eu/material/Panasonic_Ni-MH_Batteries_Handbook.pdf).
- ADI/Maxim: DS2711/DS2712 datasheet and AN3388, *Detecting Primary Cells with the
  DS2711/12* (impedance of 2004-era AAA cells at 500 mA).
- Patents: US6191551 (RIM, trial-pulse rejection), US6404164 (HP, in-use
  classification), US9184603.
- TI: BQ25172 (SLUSDY5), TPS2116 (SLVSFG1A), TLV62569, TPS61021A, TPS63802;
  ADI: LTC3539/LTC3539-2, MAX17220–MAX17225.
- MYOUNG MY-AAA-19-J / MY-AAA-20-J drawings; KELIKING KLJ-7525-3627 datasheet (LCSC).
