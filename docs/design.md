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
| Airgap | MSK12C02 slide switch → TS3USB221A (USB data) + 74LVC1G02 / TPS22917 (radio power) + 2× SN74LVC1T45 | all JLC |
| Display | NHD-2.4-240320AF-CSXP-CTP on Molex 54132-4062 + 6-pin 1.0 mm touch FPC | connectors JLC, panel hand-fitted |
| Camera | OV2640 24-pin DVP module, ~66° lens refocused to ~15 cm, on a Jushuo AFC07 FPC connector; AVDD 2.8 V / DVDD 1.3 V (adjustable) / DOVDD 3.3 V | connector JLC, module hand-fitted |
| Power | USB-C → BQ25185 charger (250 mA) → TPS63802 3.3 V buck-boost; MAX17048 battery gauge; XC6206 always-on for the STM32 RTC domain; soft-latch power button | all JLC, BQ25185/TPS63802/XC6206/USB-C/ESD as on the Nano |

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
- The UART to the radio passes two SN74LVC1T45 buffers whose radio side is the
  radio's own rail. With that rail at 0 V both ports are high-impedance (TI
  SCES515N 7.3.6), so firmware cannot back-power the module through its I/O.
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

- 48 × 85 mm, 4 layers, 1.0 mm (the chin grew 3 mm in the routing pass). In1 is solid GND (enforced by
  `board/wallet.kicad_dru`); In2 is GND poured around power runs; signals on
  the outer layers.
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
- The 32 kHz crystal is a 6 pF FC-135 on 8 pF caps, as ST asks of the LSE
  oscillator, within 2 mm of its pins.
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

- **Every part keeps 0.5 mm of free board to its neighbours' courtyards** (a via,
  or two 0.15 mm tracks). `newBoard.py` places that way and
  `scripts/checkDensity.py` fails the board below 0.4 mm. The first placement
  had 182 pairs under that; KiCad's DRC passed it anyway.
- MCU pins are chosen so each bus leaves the package facing its destination
  (flipped and turned 180°: pins 1–25 east, 26–50 south, 51–75 west, 76–100
  north): display SPI south to its connector, touch and gauge I2C east, camera
  and its SCCB north and west, secure element west, radio USART1 west to the
  radio gate, charger and button east.
- **The channel between the MCU's south pins and the TFT connector is a routing
  lane** (4.6 mm, by leaving 2.5 mm of the TFT tail as a gentle bow): only
  decoupling at its two ends, x 16–27 empty for the SPI, reset and TE drops.
- **The MCU's core runs from its internal LDO.** VDDSMPS and VLXSMPS are tied
  to GND as the datasheet asks when the SMPS is unused (DS13086 3.9.1). The SMPS
  would save a few mA at full speed, small next to the ~115 mA backlight, but
  its inductor and input cap had to sit in that lane. The 33 Ω SPI series
  resistors are gone for the same reason.
- **The radio gate** (NOR, load switch, two UART buffers and their passives)
  sits in its own north-west quadrant next to the MCU's USART1. RADIO_3V3 and
  the two radio-side UART lines run down the west edge to the module.
- The front copper has no parts at all: use it as the free routing layer (drop
  a via at the pin). In1 is GND only; In2 carries power.
- Signals reach the chin over three bridges between the slots and the edges:
  west (≈ 6 mm usable beside the hole), middle (2.6 mm) and east (3.7 mm).
- **Keep-outs are KiCad rule areas** (DRC flags any part moved into them): both
  display-tail corridors, the camera head and its flex run, the UV-artwork
  panel, a 3 mm ring around each mounting hole (all back-face, no parts), the
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
2. **Camera connector pad order**: the 24-pin DVP modules are numbered from
   both ends by different vendors (Ai-Thinker vs Espressif, pin N ↔ 25 − N).
   This board uses Ai-Thinker's. Check the chosen module's flex against pads 1
   and 24 before the first power-on; if reversed, change `J_CAM`'s pad map in
   `board/footprints.json` to pad = 25 − pin.
3. **Camera choice and focus**: OV2640, ~66° lens, 21 mm flex, focus set at
   ~150 mm (stock modules are focused far and read phone screens poorly).
   Bench it against an SC031GS global-shutter module on an animated UR before
   committing; the SC031GS would need 1.8 V I/O and level translators (a board
   change). Check the module flex's contact face against the bottom-contact
   connector on a sample.
4. **Touch tail contact face**: inherited from the Nano's analysis; confirm on
   the first panel before reflowing the touch connector.
5. **Radio UART pins**: P0.13 (module TX) and P0.24 (module RX). The module
   manual marks P1.10/P1.11 as low-frequency I/O, so they are not used.
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
$KICAD_PY scripts/checkDensity.py       # 0.4 mm routing gap between every pair of parts
$KICAD_PY scripts/checkParts.py board/wallet.kicad_pcb   # each land vs the land JLC places that part on
python3 scripts/jlcStock.py             # live JLC stock for every part number
```

`scripts/newBoard.py --force` regenerates the board from scratch (outline,
slots, stack-up, pours, keep-out, placement) and **destroys hand work**. After
the first hand edit, use `syncBoard.py` only.
