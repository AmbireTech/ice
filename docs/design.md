# Ambire hardware signer — proto 0 design

Why this architecture: [research.md](research.md). This file is what the board
is and the decisions behind each block.

## The device

A Safe-owner signing device. No seed phrase: the signing keys are generated
inside a secure element and never leave it; recovery is the Safe's other owners.

```
 front ─┬─ glass: Newhaven 2.4" IPS 240x320, capacitive touch (same panel as the GridCore Nano)
        ├─ 1S LiPo pouch, ~500 mAh, on the board's plain face
        ├─ PCB 48 x 85 mm, 4 layers, 1.0 mm  — every part on the back face
 back  ─┴─ clear case: the back face, the camera lens and the radio light are what you see
```

| Block | Part | JLC |
|---|---|---|
| MCU | STM32U585VIT6Q, LQFP100 (Cortex-M33, TrustZone, 2 MB / 786 KB, AES+SAES+PKA+HASH+TRNG, DCMI, USB FS) | C5270986 — 54 in stock |
| Secure element | NXP SE050E2HQ1 (secp256k1 + P-256 + Ed25519 keys generated inside, PIN objects with retry counters), own I2C bus. Fallback on the same land: SE051C2 | C5190917 (594 at JLC; DigiKey ~14k) |
| Radio | Ebyte E73-2G4M08S1C (nRF52840 module, BLE only), behind the airgap gate | C356849 |
| Airgap | MSK12C02 slide switch → TS3USB221A (USB data) + 74LVC1G02 / TPS22917 (radio power) + TXU0202 (radio UART isolation) | all JLC |
| Display | NHD-2.4-240320AF-CSXP-CTP on Molex 54132-4062 + 6-pin 1.0 mm touch FPC | connectors JLC, panel hand-fitted |
| Camera | OV2640 24-pin DVP module, ~66° lens refocused to ~15 cm, on a Jushuo AFC07 FPC connector; AVDD 2.8 V / DVDD 1.3 V (fixed TLV70313) / DOVDD 3.3 V | connector JLC, module hand-fitted |
| Power | USB-C → BQ25185 charger (250 mA) → TPS63802 3.3 V buck-boost; MAX17048 battery gauge; soft-latch power button | all JLC, BQ25185/TPS63802/XC6206/USB-C/ESD as on the Nano |

Overlap with GridCore: charger, buck-boost, always-on LDO, USB-C and ESD, the
display panel with its connector and backlight pump, the slide switch, the
battery connector, the netlist → KiCad flow and every gate script.

## The airgap switch is the authority, not the firmware

One net, `AIRGAP`, runs from the slide switch straight to the two places the
outside world can reach the device:

- **USB data**: the TS3USB221A's /OE. Airgapped = D+/D- disconnected from the
  MCU. Charging is untouched.
- **Radio power**: a NOR gate drives the TPS22917 load switch that feeds the
  whole BLE module. The MCU's input to the NOR can switch the radio *off*, never
  on. The load switch's output discharge drops the rail promptly.

What makes it hold even against hostile firmware:

- The MCU reads the switch through 1M (`AIRGAP_SENSE`). In the AIR detent the
  switch holds the net through 1k; caught between detents only the 100k pull-up
  holds it, and a pin driven low against 1M still leaves it at 3.0 V. (A 10k
  here let firmware pull a floating switch to 0.3 V; found in review.)
- The 1k in the switch's AIR throw also covers a switch that makes before it
  breaks: its other throw is GND.
- The UART to the radio passes a TXU0202 translator whose radio side, and its
  enable, are the radio's own rail. With that rail at 0 V both ports are
  high-impedance (TI SCES942A 9.3.4) and every pin holds Ioff, so firmware cannot
  back-power the module through its I/O.
- A 100k pull-up makes "airgapped" the state of a switch caught between
  positions.
- The **RADIO light hangs on the radio's rail itself**: it is lit exactly when
  the radio has power.

In airgap mode the device talks by animated QR both ways: it scans the request
with the camera and shows the signature as a QR.

What the airgap does **not** cover: it cuts USB data and radio power. A hostile
MCU on a malicious charger could still signal by switching the 250 mA charge
current on and off (`CHG_CE`), and the display and camera are optical channels
by design. Firmware integrity (secure boot, TrustZone, signed updates) is what
keeps the MCU honest; the switch guarantees the radio and USB data paths.

## Secure element wiring

SE050 on I2C4, which nothing else shares. VIN = 3.3 V; VOUT tied to VCC (the
deep-power-down wiring, AN12395 Fig. 6), so the MCU can power the secure core
down and up with ENA, which is a full reset if the I2C link ever wedges.
Contactless antenna pins and ISO7816 RST_N to ground, ISO7816 IO/CLK open, as
the data sheet recommends for unused pins.

Firmware note: the E2 runs applet 7.2 (AN13014): keep its I2C at or below
1 MHz, encode PINs as 4+ bytes, and use the E2's own default SCP03 keys
(AN12436 Table 5). Read the variant from the chip rather than assuming.

## Display tails (why the connectors face the way they do)

The panel's TFT tail (40 pins, 0.5 mm, 30 mm long) and touch tail (6 pins,
1.0 mm, 55.85 mm long) both leave the panel's south edge. Each drops through a
slot just south of the panel and turns north under the board into a connector
on the back face.

- Newhaven puts the TFT contacts on the tail's rear face. Two 90° bends (down,
  then north) turn that face toward the board, so the connector must be
  bottom-contact: the 54132-4062 is. The tail is never twisted, so panel pin 1
  stays at the west end, which on the placed connector is pad 40: **pad = 41 −
  panel pin**, the same rule as on the Nano.
- The touch tail's contacts face the other way (the Nano's analysis, which
  needed a top-contact part for the same reason). After the same two bends they
  face away from the board: the HC-FPC-1.0-6P is top-contact. Pin 1 west →
  **pad = pin**.
- `scripts/checkFpc.py` re-derives all of this from the placed board: openings,
  which pad is west-most, lever and plunger directions, antenna end. A connector
  rotated 180° fails it.

Connector positions come from the tail lengths, not from the picture:
`newBoard.py` runs each tail from the panel edge to its slot, down through the
cell (4.0 mm), foam (0.3 mm) and board (1.0 mm) — the touch flex leaves from the
touch glass, 2.7 mm higher — and north to the connector's contact row.
`checkFpc.py` fails if a placed connector leaves the tail short or with more
than 3 mm of slack. Change `CELL_T` there if the chosen pouch is thicker.

The touch tail's length is the one awkward thing: it lies along the back
face's east side (x 36.5–43.5) up to its connector. Only low parts sit under it.
For production, ask Newhaven for a shorter touch tail or choose a panel whose
touch flex is merged into the TFT flex.

## Board

- 48 × 85 mm, 4 layers, 1.0 mm (the chin grew 3 mm in the routing pass).
  **Stack-up: F.Cu / In1 = 3.3 V plane / In2 = solid GND / B.Cu.** Every part
  sits on B.Cu, whose return currents run in the plane next to it, so In2 is the
  unbroken GND; every 3.3 V pin and decap drops a via to In1, and the front, the
  free layer for the buses between blocks, references the 3.3 V plane.
  `board/wallet.kicad_dru` enforces both (In2: GND only; In1: 3.3 V, plus the
  MCU's 1.1 V core link between pins 49 and 98).
- **All parts on the back face (B.Cu).** The front face carries only the cell
  and the panel, plus the **programming pads** (MCU SWD and radio SWD). They are
  reachable by pogo pins before assembly and buried under the cell afterwards.
- The chin (south of the slots) holds USB-C (mouth south), the radio module
  (antenna west, with a copper keep-out on every layer), the charger, buck-boost
  and the cell connector (cable out the east edge).
- The airgap switch's lever exits the west edge next to the RADIO light. The
  power button plunger exits the east edge; its land is KiCad's ALPS SKRT land
  with pad 1 narrowed and the tab pads trimmed 0.05 mm, because the stock land
  leaves 0.1 mm between copper and the locating holes (JLC wants 0.2 mm).
- No 32 kHz crystal and no RTC backup supply: nothing on the device needs the
  time across power-off (a Safe transaction carries no timestamp; the PIN retry
  counter lives in the SE050). USB runs on the internal 48 MHz oscillator trimmed
  from the USB frames, timeouts on the internal 32 kHz one.
- Four M2 holes: two at the north corners, one at the panel's south-west corner
  (the board's south-west corner belongs to the antenna), one in the south-east
  corner.
- Brand: the back face is UV-printed (JLC multi-colour silkscreen) with the
  artwork in `art/` ("the only way in is light": Ambire Black with brand-colour
  beams from the camera window, the official logo in the reserved 12 × 9 mm
  panel). JLC's colour print needs **white solder mask and ENIG**, and is
  ordered through EasyEDA Pro. `art/build_uv.py` rebuilds the print file from
  the current board and refuses to put ink on any pad or hole. If the board is
  ordered with colour print, delete the plain-silk duplicates (AMBIRE,
  QR CAMERA, SECURE ELEMENT, AIRGAP, RADIO) so they don't print twice.

## Placement for routing

- **The placement is the owner's, by hand**, with targeted fixes where the
  routing showed a problem. Related parts sit with courtyards touching and
  decoupling caps against their pins; there is no spacing rule.
- **Routability is judged by routing, never by a proxy.** A crossing-count
  quench of the parts not hand-placed made the remainder *harder* to route in
  every setting tried: the proxy ignores existing copper. Placement changes on a
  hand-worked board are targeted fixes, each checked by routing.
- **Keep-outs the routing needs are KiCad rule areas** (`scripts/refreshKeepouts.py`
  redraws them from `newBoard.py` after a connector moves):
  - **camera fan-in**: 3.1 mm of bare board east of J_CAM's contacts, where all 24
    lines enter (the contacts face east; the camera LDOs used to fill it);
  - **MCU west escape**: the strip outside pins 52–67, where the camera and SE
    lines drop to the front layer.
- **Blocks are pre-routed; the buses between them are the human's.**
  `scripts/partialRoute.py` routes the wiring inside each block on the back
  copper (the parts' side), a via to the 3.3 V plane for every 3.3 V pin and
  decap, and a GND via for every cap and IC (0.25 mm clear of all pads), on top
  of the hand-routed copper, which it never rips. A link longer than 7 mm (a
  pull-up at the far end of a bus, a test pad across the board) or a power link
  longer than 10 mm is left with its bus.
- **Gauges on what is left** (2026-10-16): KiCadRoutingTools routes every
  remaining net (0 failed, 119 vias); the full humanRouter finish routes them
  all too (0 unconnected). Both are only a measure of how easy the hand route
  is; their copper is not kept.
- **Camera pins** are ordered on the north side as J_CAM orders them: XCLK comes
  from timer TIM3 on PB5 (not the clock-output pin in the west-side knot) and
  RST sits east of VSYNC, D6 and D7. PWDN is tied low at the connector.
- **J_CAM is not where a stock camera tail lies flat.** A stock 20.5–21 mm module
  bows 4.2–4.8 mm (`checkFpc.py` reports it); a ~16.5 mm custom flex lies flat.
  Moving J_CAM 4.5 mm east instead would put its body over the MCU's north pins
  and cost every north camera pin a via.
- Small parts may sit under the display flex tails; `checkFpc.py` fails any tall
  part there.
- MCU pins are chosen so each bus leaves the package facing its destination
  (flipped and turned 180°: pins 1–25 east, 26–50 south, 51–75 west, 76–100
  north): display SPI south to its connector, touch and gauge I2C east, camera
  and its SCCB north and west, secure element west, radio UART (USART3) at the
  south-west corner, charger and button east. Moving the radio UART to the west
  side (USART1) was trial-routed and came out worse: the two extra pins land in
  the camera bus's escape.
- **The channel between the MCU's south pins and the TFT connector is a routing
  lane** (4.6 mm, by leaving 2.5 mm of the TFT tail as a gentle bow): only
  decoupling at its two ends, x 16–27 empty for the SPI, reset and TE drops.
- **The MCU's core runs from its internal LDO.** VDDSMPS and VLXSMPS are tied
  to GND as the datasheet asks when the SMPS is unused (DS13086 3.9.1). The SMPS
  would save a few mA at full speed, small next to the ~115 mA backlight, but
  its inductor and input cap had to sit in that lane. The 33 Ω SPI series
  resistors are gone for the same reason.
- The RADIO light sits directly under the south-west hole, next to the radio
  module, in the strip between the screw head and the antenna keep-out, so the
  west bridge into the chin stays open.
- **The radio gate** (NOR, load switch and their passives) sits in the
  north-west quadrant. The UART translator sits between the TFT slot and the
  radio module, right over the module's two north-edge UART pads, so no radio
  line runs under the module; the two MCU-side lines come over the west bridge.
- The front copper has no parts at all: it is the layer for the buses between
  blocks (drop a via at the pin).
- Signals reach the chin over three bridges between the slots and the edges:
  west (≈ 6 mm usable beside the hole), middle (2.6 mm) and east (3.7 mm).
- **Keep-outs are KiCad rule areas** (DRC flags any part moved into them): both
  display-tail corridors, the camera head and its flex run, the UV-artwork
  panel, a 2.6 mm ring around each mounting hole (all back-face, no parts), the
  antenna end (no copper on any layer) and the area under the radio module (no
  tracks or vias on the back copper, GND fill only).

## Power on and off

The power button pulls the 3.3 V buck-boost's enable up through one diode; the
MCU holds it through the other (`PWR_HOLD`). 1 µF on the enable with its 1M
pull-down keeps the rail up for ~1 s through any MCU reset (watchdog, brown-out,
firmware update, debugger), so a reset never turns the device off. Firmware
powers off by releasing `PWR_HOLD` and waiting ~2 s. Before that it puts the
battery gauge in hibernate (~3 µA, still gauging). With USB plugged in and the
device off, the cell charges and nothing else runs; there is no charge light.

## Bring-up checks that need parts in hand

1. **Airgap switch direction**: confirm with a meter which lever position joins
   the common pin to pin 1 (3.3 V = AIRGAP). If it is the opposite of the silk,
   swap the AIR/ONLINE ends in `src/blocks/Radio.tsx`.
2. **Camera connector contact face**: vendors number the 24-pin DVP flex from
   either end (Ai-Thinker vs Espressif, pin N ↔ 25 − N), but that is numbering
   only; the signal order along the flex is the same. This board follows
   Ai-Thinker's numbering, which matches the modules checked so far once the
   lens faces away from the board. What to check on a sample is which face of
   the flex carries the contacts (the AFC07 is bottom-contact).
3. **Camera choice and focus**: OV2640, ~66° lens, focus set at ~150 mm (stock
   modules are focused far and read phone screens poorly). Best-documented
   module: Union Image ZY-XSF3M2640B (8.5 mm head, 68.7°); a stock 20.5–21 mm
   flex bows ~4.5 mm here, a ~16.5 mm custom flex (SincereFirst offers it) lies
   flat. The head is 5.4–5.9 mm tall on every drawing found: check the case.
   Bench it against an SC031GS global-shutter module on an animated UR before
   committing; the SC031GS would need 1.8 V I/O and level translators (a board
   change). Check the module flex's contact face against the bottom-contact
   connector on a sample.
4. **Touch tail contact face**: inherited from the Nano's analysis; confirm on
   the first panel before reflowing the touch connector.
5. **Radio UART pins**: P0.31 (module TX, pad 9) and P0.30 (module RX, pad 10).
   The TXU0202's inputs carry 5 MΩ pull-downs, so both lines idle low until
   driven: discard one framing error at start-up, and keep the MCU's RX pin
   pull-up on while the radio is off.
7. **Firmware settings the hardware relies on**: BOOT0 is tied to GND, so the
   ROM bootloader is entered through the option bytes over SWD; the power
   button's BTN_N uses PA0's internal pull-up (kept through Standby/Shutdown);
   XCLK for the camera is TIM3_CH2 PWM on PB5 (~20 MHz).
6. **Secure element generation**: the board carries the current SE050E2
   (the C1 is "previous generation" in AN12436). Same land as the C1 and the
   SE051C2 fallback. Firmware must use applet 7.x behaviour: I2C at or below
   1 MHz (no clock stretching), PINs of 4+ bytes, the E2's SCP03 keys. JLC
   holds 594, enough for prototypes; for 1,000 units consign from DigiKey
   (~14,000 in stock, 16-week lead) or use JLC Global Sourcing.

## Flow

Same as the GridCore Nano:

```sh
cd hardware/ambire-wallet
KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
npx tsci export index.circuit.tsx -f circuit-json -o board/netlist.circuit.json
python3 scripts/checkNets.py            # every named net reaches two pads
python3 scripts/checkMcuAf.py           # every MCU signal is an alternate function ST lists for that pin
$KICAD_PY scripts/syncBoard.py          # dry run; --apply writes (close KiCad first)
$KICAD_PY scripts/checkPinNets.py       # IC pads vs datasheet pin tables, on the board copper
$KICAD_PY scripts/checkFpc.py           # connector / lever / antenna orientation, tail lengths
scripts/trialRoute.sh                   # autoroute a copy: failed nets / vias (placement arbiter)
$KICAD_PY scripts/refreshKeepouts.py        # redraw the keep-out rule areas after a connector moves
$KICAD_PY scripts/partialRoute.py --out DIR  # block wiring on a copy, hand copper kept
$KICAD_PY scripts/placeLoop.py --base COMMIT   # optimize unlocked parts -> partial route -> judge, logged
$KICAD_PY scripts/checkParts.py board/wallet.kicad_pcb   # each land vs the land JLC places that part on
python3 scripts/jlcStock.py             # live JLC stock for every part number
```

`scripts/newBoard.py --force` regenerates the board from scratch (outline,
slots, stack-up, pours, keep-out, placement) and **destroys hand work**. After
the first hand edit, use `syncBoard.py` only.
