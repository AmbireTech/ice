# Ambire Ice — proto 1 design

Why this architecture: [research.md](research.md). This file is what the board
is and the decisions behind each block.

## The device

A Safe-owner signing device. The hardware is built for keys generated inside
the secure elements that never leave them, with no seed phrase and the Safe's
other owners as recovery. The firmware starts from Trezor's, which is a
seed-phrase wallet; how the two fit is open ([direction.md](direction.md)).

```
 front ─┬─ glass: Newhaven 2.4" IPS 240x320, capacitive touch (same panel as the GridCore Nano),
        │  on foam tape straight on the board
        ├─ PCB 48 x 87 mm, 4 layers, 1.0 mm  — every part on the back face but the chin's
        │  power parts, the radio and the joystick
 back  ─┴─ clear case: the back face, the radio light and the camera head (standing out
           through an open window) are what you see; the battery pack (two AAA) snaps on
           high on the back, on a magnetic pogo connector
```

Proto 0 (the first three boards, ordered) had a 1S LiPo pouch under the screen.
Proto 1 replaces it with one AAA cell of any chemistry, which makes the device
6.5 mm thinner (11.5 mm without the cell and camera). The research behind it:
[power-aaa.md](power-aaa.md). v4 moves the cells off the board into a two-cell pack on
the back ([pack.md](pack.md)).

| Block | Part | JLC (stock on 2026-10-18) |
|---|---|---|
| MCU | STM32U585VIT6Q, LQFP100 (Cortex-M33, TrustZone, 2 MB / 786 KB, AES+SAES+PKA+HASH+TRNG, DCMI, USB FS) | C5270986 (43) |
| Secure element | NXP SE050E2HQ1 (secp256k1 + P-256 + Ed25519 keys generated inside, PIN objects with retry counters), own I2C bus. Fallback on the same land: SE051C2 | C5190917 (585; DigiKey ~14k) |
| Second secure element | Infineon OPTIGA Trust M V3 SLS32AIA010MK (CC EAL6+ hardware, P-256 key generated inside), on the same I2C bus. Fallback on the same land: Trust M V1 SLS32AIA010MH | C1518668 (0, and JLC/LCSC list it as no longer manufactured): not fitted on the first boards; V1 C539250 |
| Radio | Ebyte E73-2G4M08S1C (nRF52840 module, BLE only), behind the airgap gate | C356849 (1176) |
| Airgap | MSK12C02 slide switch → TS3USB221A (USB data) + 74LVC1G02 / TPS22917 (radio power) + TXU0202 (radio UART isolation) | all JLC |
| Display | NHD-2.4-240320AF-CSXP-CTP on Molex 54132-4062 + 6-pin 1.0 mm touch FPC | connectors JLC, panel hand-fitted |
| Camera | GC2145 24-pin DVP module in the ESP32-CAM pinout (Camemake CM_DVP_GC2145_UNIVERSAL; an OV2640 module fits too), 115° lens read through the sensor's centre crop, no refocus, on a Jushuo AFC07 FPC connector; AVDD 2.8 V / DVDD 1.2 V (fixed TLV70312) / DOVDD 3.3 V | connector JLC, module hand-fitted (DigiKey, JLC Global Sourcing) |
| Power | Two AAA cells in series (NiMH, lithium or alkaline) in a pack on the back, on a Xinyangze magnetic pogo receptacle (YZ103915020T-04025-02, C6276862) → AO3416 reverse-cell blocker → LTC3539-2 boost to 3.3 V; USB-C → TLV62569P buck → TPS2116 picks USB first; BQ25172 NiMH charger set for two cells, fed from VBUS, MCU-gated; soft latch on the joystick's centre press | all JLC (LTC3539-2: 2,400; the receptacle: 125) |
| Sound | Huaneng MLT-5020 magnetic transducer (5 × 5.5 × 2 mm, 75 dB at 10 cm), AO3416 low-side, LPTIM3 PWM | JLC |
| Joystick | ALPS SKRHAAE010: four directions + centre push, 7.5 mm, on the front, centred in the chin | JLC C202423 (521) |
| IMU | Bosch BMI270 (accelerometer + gyroscope, LGA-14 2.5 × 3 mm, 3.5 µA in suspend), the GridCore Nano's part, on the touch controller's I2C bus (0x68), interrupt on PC15 | C2836813 (26,396 on 2026-10-30) |

Overlap with GridCore: USB-C and ESD, the display panel with its connector and
backlight pump, the slide switch, the netlist → KiCad flow and every gate script.

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
MCU on a malicious charger could still signal by switching the NiMH charge current
on and off (`CHG_OFF`, `CHG_HI`), and the display, camera and buzzer are optical and
acoustic channels by design. Firmware integrity (secure boot, TrustZone, signed updates) is what
keeps the MCU honest; the switch guarantees the radio and USB data paths.

## Secure element wiring

Two secure elements from two vendors, so one vendor's silicon break does not
hand over the device's signing authority: each holds its own key, generated
inside it, and the account counts the device as both keys together (see
research.md). They share I2C4, which nothing else uses: SE050 at 0x48, OPTIGA at
0x30, each with its own reset (SE050 ENA, OPTIGA RST from PD14, pulled high).

OPTIGA Trust M (data sheet Rev 3.10): pin 1 GND, 3 SDA, 8 SCL, 9 RST, 10 VCC,
100 nF at VCC (Figure 2); the centre pad is not in the contact table, so it is
soldered but left unconnected. A warm reset holds RST low at least 10 us; the
first transfer waits 15 ms after release (Table 14).

SE050 on I2C4. VIN = 3.3 V; VOUT tied to VCC (the
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
- The touch tail's contacts are on the same face as the TFT tail's (Newhaven's
  side view marks "stiffener / contact side" the same way round on both), so they
  too face the board after the two bends and the connector is bottom-contact: the
  HC-FPC-1.0-6P-CXH25. Proto 0 had the top-contact CSH25 (the Nano's analysis,
  which was wrong for this panel) and its touch made no contact. Pin 1 west →
  **pad = pin**; the CXH25's land and pad numbering are the CSH25's, so the pads
  and copper stayed as they were.
- `scripts/checkFpc.py` re-derives all of this from the placed board: openings,
  which pad is west-most, lever and plunger directions, antenna end. A connector
  rotated 180° fails it.

**What goes through the board is the TFT flex's root, not its tail.** Newhaven's
drawing, read from its vectors (2026-10-31):

- The TFT flex leaves the panel as a **39.00 mm wide**, EMI-shielded root, 1.90 mm in
  from each outline edge (x 4.5–43.5 on the board), from 2.2 to 13.2 mm below the
  panel; only then does it narrow to the 20.5 mm tail.
- The tail is **centred** on the panel: 11.15 mm from both outline edges, x
  13.75–34.25. The drawing's 9.25 mm is measured from the root's edge, not the
  outline; read from the outline it had put the tail, the TFT connector and the slot
  1.9 mm west. The connector now sits at x 24.0, under the tail.
- The tail is too short to reach the back unless it folds within the root, so the
  root folds through the board, and **the cutout is sized by it**: x 4.05–43.95 (0.45
  past the root), y 63.41–68.01, one straight-edged rectangle. On the owner's printed
  test board the old slots had to be cut out to x ~4 before the panel would fold.
- The touch flex leaves the panel above the TFT flex and across its whole width, so
  it folds around the **outside** of the TFT fold, further south. The drawing's side
  view puts the flexes' exits 1.26 mm (TFT) and 3.22 mm (touch) above the module's
  back.

Connector positions come from these lengths, not from the picture. `newBoard.py`
runs each flex from the panel edge to its fold (TFT at y 66.8, touch 0.4 mm
outside it), down through foam (0.3 mm) and board (1.0 mm) from its exit height, and
north to the connector's contact row. `checkFpc.py` fails a placed connector that
leaves its tail short or with more than 3 mm of slack. The TFT tail lands 2.5 mm
past its contacts and the touch tail 1.5 mm, which moved the touch connector 5.5 mm
south. The cutout is 4.6 mm tall so the folds can move: folding both further north
gives the TFT tail more bow and the touch tail less.

The touch flex is 7 mm wide only for its last 32 mm. For its first 23.6 mm below
the panel it is 14.55 mm wide, flush with the tail's east edge: the root off the
touch glass and a stiffened block that carries the touch controller under a
shield. Newhaven does not dimension that part; the numbers are its outline in the
drawing's vectors, scaled by the dimensioned tail beside it. On the back that block
lies from the cutout north to about y 54.4 (x 28.95–43.5), and the TFT root's last
1.3 mm from the cutout to y 62.1 across x 4.5–43.5; the narrow tails continue to
their connectors. Only low parts sit under any of them (`checkFpc.py` checks all).

## Board

- 48 × 87 mm, 4 layers, 1.0 mm (the chin grew 3 mm in proto 0's routing pass, and
  2 mm in proto 1 for the joystick below the cell).
  **Stack-up: F.Cu / In1 = 3.3 V plane / In2 = solid GND / B.Cu.** Every part
  sits on B.Cu, whose return currents run in the plane next to it, so In2 is the
  unbroken GND; every 3.3 V pin and decap drops a via to In1, and the front, the
  free layer for the buses between blocks, references the 3.3 V plane, also
  unbroken. `board/wallet.kicad_dru` enforces both (In2: GND only; In1: 3.3 V
  only). The MCU's 1.1 V core link (pin 49 to pin 98) runs on the back copper
  under the package, inside its pin ring.
- **Parts on both faces** (double-sided assembly since proto 1). Under the screen
  the front is bare: the panel sits on it. Since the cell moved to the back
  ([cell-placement.md](cell-placement.md)), the front's chin carries the radio module,
  the joystick and its resistor ladder, the boost (LTC3539 with its inductor and caps),
  the reverse-cell FET, the power-key transistor and the **USB power path** (the
  TLV62569P buck between USB-C's shell legs and the TPS2116 mux), under one pocket in
  the flat bezel's chin. The front's chin face stays flat: the tallest part there is
  the 3.0 mm radio module, under a 5.33 mm screen stack.
  The **programming pads** (MCU SWD under the panel, the radio's SWDIO/SWCLK on the
  back beside the module's SWD pins) are reachable by pogo pins before assembly.
- **The pack's receptacle** (v4) lies east-west on the back, south of the MCU and over
  the display connector, which moved 5.5 mm south for it (its tail's slot 2.5 mm, so
  the tail keeps 3.0 mm of slack). The MCU's south-side signals pass it on the front
  copper. The east side of the back is the touch tail's lane, which rules it out there.
- **The chin's back** holds USB-C (mouth south) with the USB switch and ESD clamp
  beside it and the CC pull-downs (until v3 the AAA cell lay across it in two clips).
  North of the cutout, under the touch flex (low parts only), sits the charger with
  its control transistors. Since the bridge between the tails went (2026-10-31) the
  cell's + reaches the chin over the east bridge only: from the pack's trunk it runs
  on the back between the TFT connector and the charger (y 52.5) to the east edge.
  The two sense dividers moved north with it, so neither sense line crosses: the
  cell-sense divider sits beside the pack's mid-cell divider, by the MCU's ADC line,
  and the VBUS-sense divider north of the cutout's west end (of two placements
  trial-routed with the human router, the one with the least new copper: 73 mm,
  6 signal vias). The v3 placement was chosen the same way: with the USB block beside
  USB-C and the charger beside the then middle bridge it routed in 1230 mm and 87
  signal vias, against 1383 mm and 100 for the first seed.
- **Trace widths** come from the net classes (KiCad's default when routing a net):
  the **cell path at 0.8 mm** (BAT, BAT_N, SW_BOOST: ~1 A from a 1.0 V cell, every
  10 mΩ is 1 % of its voltage; two vias per layer change), the 3.3–5 V rails at
  0.4 mm (VBUS, SW_USB, V3V3_USB, V_BOOST, the 3.3 V plane's fanouts), small supplies
  at 0.25, the USB pairs at 0.15 (coupled, 0.25–0.3 mm apart), signals at 0.15. Both
  USB links are full speed (the STM32U585 has no high-speed PHY): 12 Mb/s with edges of
  4 ns or more over 8 cm of trace is electrically short, so the pair's impedance (an
  estimated 110–125 Ω differential, against USB's 90 Ω; the fab's stackup is not
  confirmed) does not matter. Keeping the two lines together does; a 1.5 mm length
  difference is about 10 ps.
- **The radio's antenna points south**, at the board edge in the south-west corner,
  with a copper keep-out on every layer. Proto 0's antenna pointed west from a
  module across the chin; with a metal cell over the chin's front that would have
  sat under the can, which detunes a chip antenna. `checkFpc.py` fails if the cell
  (between its clips, 10.5 mm across) overlaps the antenna keep-out. Since the cell
  went to the back the module sits on the front, its antenna end 6 mm south of the cell
  and on the other face (the same relation as before, mirrored); its pad row faces
  north, and its UART translator stays on the back's west bridge.
- The airgap switch's lever exits the west edge next to the RADIO light. There is
  no power button on the edge: the joystick's centre press is the power key.
- No 32 kHz crystal and no RTC backup supply: nothing on the device needs the
  time across power-off (a Safe transaction carries no timestamp; the PIN retry
  counter lives in the SE050). USB runs on the internal 48 MHz oscillator trimmed
  from the USB frames, timeouts on the internal 32 kHz one.
- Three M2 holes: two at the north corners (under the panel: locating pins in the
  case) and one in the south-east corner, the screw. The fourth, west of the display cutout
  between the panel and the cell, is out for now: the cell's west clip moved north
  over its boss to free the chin for the joystick. The south-west corner belongs to
  the antenna.
- **The front is incognito.** No brand mark on the front (bezel, chin or case): a
  signer should not advertise what it is. The brand lives only on the back, behind
  the case.
- Brand: the back carries the Ambire flame alone, in ordinary silkscreen: as
  wide as the board less 1.2 mm a side, one ink with a 0.35 mm gap between facets,
  cut back from pads, holes, vias, the edge, footprint silkscreen and the
  human-readable labels (QR CAMERA, SECURE ELEMENT, AIRGAP, RADIO, ANT), which
  stay as clean islands. The word "Ambire" is nowhere on the board.
  `scripts/silkFlame.py` draws it (group FLAME, replaced on a rerun, DRC checked
  on a copy before it installs); `art/flame.py` is the geometry, shared with the
  concept page's preview. A full-colour version (purple on neon and the like)
  needs PCBWay's colour print; `art/build_uv.py` builds it from the same flame,
  and the PCBWay set leaves the white flame out of its silkscreen. The first three boards (ordered before the flame)
  carry the old wordmark in silkscreen.

## Placement for routing

- **The placement is the owner's, by hand**, with targeted fixes where the
  routing showed a problem. Related parts sit with courtyards touching and
  decoupling caps against their pins; there is no spacing rule.
- **Routability is judged by routing, never by a proxy.** A crossing-count
  quench of the parts not hand-placed made the remainder *harder* to route in
  every setting tried: the proxy ignores existing copper. Placement changes on a
  hand-worked board are targeted fixes, each checked by routing.
- **A chip faces its partner.** The owner turned the secure element −90° so its
  I2C/ENA pins face the MCU, with the pull-ups inline between the two: airwire
  length and crossings rate that no better than a chip whose pins point away, so
  `optimizePlacement.place_block()` scores an IC together with its satellites,
  for every rotation, with a pin-facing term (an airwire leaving a pin backwards
  costs 4 mm). From the old layout it re-derives the owner's orientation.
- **No GND via beside a GND pad.** GND pads take the outer pours; stitching vias
  (every ~4 mm, 0.5 mm clear of pads) tie the pours to In2, and a signal via gets
  a GND return via beside it. Only 3.3 V pins and decaps get a via (to the In1
  plane). A pad the pour cannot reach is joined to the pour by a track, or gets a
  via inside its exposed pad or pin ring. Exceptions, where the part asks for a
  short path to the plane: the buck-boost's ground and its input/output caps, the
  backlight charge pump, the charger's exposed pad, the radio module's ground pads
  and the USB ESD clamp. `scripts/stripGndTies.py` removes the other ties.
- **Keep-outs are KiCad rule areas** (DRC flags any part moved into them;
  `scripts/refreshKeepouts.py` redraws them from `newBoard.py` after a connector
  moves). No parts on the back in: the **camera fan-in**, 3.1 mm of bare board
  east of the camera connector's contacts, where all 24 lines enter (the contacts
  face east; the camera LDOs used to fill it); the camera head and its flex run;
  a ring around each mounting hole. The battery plug's and the old UV-artwork
  panel's keep-outs went with proto 0: the buzzer sits in that north-east corner. No copper on any layer at the antenna end, and no tracks or vias on the
  back under the radio module (GND fill only). An "MCU west escape" strip was
  tried and dropped: it forbade the SE pull-ups exactly where they belong, inline
  between the SE and the MCU.
- **Blocks are pre-routed; the buses between them are the human's.**
  `scripts/partialRoute.py` routes the wiring inside each block on the back
  copper (the parts' side) and a via to the 3.3 V plane for every 3.3 V pin and
  decap, on top of the hand-routed copper, which it never rips. A link longer than 7 mm (a
  pull-up at the far end of a bus, a test pad across the board) or a power link
  longer than 10 mm is left with its bus. A 3.3 V pad with no room for its own
  via is joined to a neighbouring pin of its net (`--link C_RCT.1:C_RIN.1`).
- **Power fanouts are as thick as the pad allows** (0.4 mm, capped at the pad's
  narrow side): a big plane via on a thin trace is ugly and buys nothing. The
  router does this itself now; `scripts/widenFanouts.py` widens what older or
  hand copper still has thin.
- **No stale copper.** A plane via or stub that touches no pad of its net is
  left over from a part that moved, not a fanout waiting for routing; a GND track
  that reaches no pad is litter (a lone GND via is stitching). KiCad's interactive
  router shoves free stitching vias, and a moved free via takes the net of the
  plane it lands in: a "3.3 V via" connected to nothing is usually such a GND via.
  `scripts/cleanCopper.py` removes all of these; `--check` must pass before a commit.
- **Autorouted nets are accepted one by one.** A net is kept only if its length
  is close to its shortest tree and it adds no detour, hairpin or extra via;
  the rest (the USB pair, the radio UART, AIRGAP) are ripped and left for the
  hand, since a bad route there costs more to fix than to draw.
- **The gauge is the deterministic humanRouter full finish** (`scripts/routeGauge.py`,
  on a copy, with no GND fanouts and the pours stitched): the same board gives the
  same numbers to within ~0.1 % (1395 vs 1396 mm), so placements compare run against run. KiCadRoutingTools varied
  2 vs 6 failed nets on near-identical boards. The autorouted copper is a measure,
  never the deliverable.
- **Bus return paths**: the back copper returns in In2 (GND), the front in In1
  (3.3 V). A bus that changes layer changes reference, so its return hops through
  the nearest decap: keep the camera PCLK and XCLK on one layer end to end, or put
  a 100 nF 3.3 V-to-GND cap within ~2 mm of each layer-change via cluster. This
  holds for JLC's 4-layer stacks, where the outer prepreg is the thin layer.
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
  the camera bus's escape. v4 moved two single pins: the airgap sense to PB15 on
  the west side, beside its series resistor, and the charger's status to PA3 at the
  south-east corner, because the pack's receptacle south of the MCU sends the
  display lines' fan across the south side.
- **South of the MCU sits the battery pack's receptacle** (v4), in what was a 4.6 mm
  routing channel to the TFT connector; the connector moved 5.5 mm south for it
  (its tail keeps 3.0 mm of slack, the slot moved 2.5 mm). The MCU's south-side
  signals pass the receptacle on the front copper; its pads, boss slots and magnets
  leave only a few lanes under it on the back. Moving the slot walled off the radio
  module's north-row exits: RADIO_RX/TX leave its pads through the 0.65 mm lane
  between its two pad rows on the front.
- **The MCU's core runs from its internal LDO.** VDDSMPS and VLXSMPS are tied
  to GND as the datasheet asks when the SMPS is unused (DS13086 3.9.1). The SMPS
  would save a few mA at full speed, small next to the ~115 mA backlight, but
  its inductor and input cap had to sit in that lane. The 33 Ω SPI series
  resistors are gone for the same reason.
- The RADIO light sits in the strip west of the radio module, under the cell's −
  clip on the other side.
- **The radio gate** (NOR, load switch and their passives) sits beside the
  airgap switch on the west edge, so AIRGAP and the radio's supply are a few
  millimetres each instead of 30–40 mm down the west side
  ([experiments](../board/experiments/README.md)). The UART translator sits between the display cutout and the
  radio module, right over the module's two north-edge UART pads, so no radio
  line runs under the module; the two MCU-side lines come over the west bridge.
- Under the screen the front copper has no parts: it is the layer for the buses
  between blocks (drop a via at the pin).
- Signals reach the chin over two 4 mm bridges between the cutout and the edges.
  The west one carries the USB pair, AIRGAP and the radio's TX, RX and 3.3 V as one
  bundle on the back. Over the radio module the cutout's south edge steps 0.41 mm
  north (x 4–17.8), leaving a strip above the module's top pad row: 3.3 V runs along it
  from the west to pins 23 and 19, RX from the east to pin 15. The gap between the pad
  rows carries TX from the east and pin 21's ground tie to the west. The radio's decaps
  sit on the west bridge, 6.5–8 mm from its supply pins (the module has its own).
  The east bridge carries the battery and boost rails, the enable and button lines and
  VBUS. Copper keeps 0.25 mm from every edge (JLC: 0.2). The chin hangs on the two
  bridges: ask JLC to keep the panel rails on, and fit the case before relying on them.

## Power: two AAA cells in a pack

Why these parts, which cells and how long they last: [power-aaa.md](power-aaa.md).

```
 USB-C VBUS -> TLV62569P buck -> V3V3_USB --VIN1--+
        |                          | PG -> PR1    | TPS2116 (USB first) -> V3V3
        \-> BQ25172 (2 cells) ---------------------+-> BAT (NiMH only, MCU-gated)
 pack: BAT -> LTC3539-2 boost, SHDN = soft latch -> V_BOOST --VIN2--+
       BAT_N -> AO3416 (reverse-cell blocker) -> GND
```

- **The boost runs from every cell that fits**: it starts at 0.88 V (max), runs to
  0.5 V, and keeps regulating when the cell is *above* 3.3 V, so a 10440 Li-ion at
  4.2 V never reaches the 3.3 V rail (the STM32's absolute maximum is 4.0 V). No
  Schottky across it: that would defeat exactly this. Firmware applies the
  chemistry's own cut-off (NiMH or alkaline ~1.0 V under load, Li-ion 3.0 V).
- **A reversed cell** is blocked by an AO3416 in the cell's low side: its body
  diode conducts a correctly inserted cell from the first instant, its gate follows
  the cell (threshold 0.4–1.1 V) and then the boost's own output (~3 V, ~25 mΩ).
  Driving the gate from the 3.3 V rail instead would connect a reversed cell
  whenever USB is plugged in.
- **USB powers the board**, and the TPS2116 hands over to the cell (switching on
  the buck's power-good, at 90 % of 3.3 V) without a gap when the cable comes out,
  as long as firmware holds the boost on. Power-good's pull-up is a 150k/100k divider
  from VBUS: the buck lets go of power-good once VBUS is under 1.4 V, and a pull-up to
  the buck's slower-decaying output would then switch the TPS2116 back to the dead USB
  input and sag the rail.
- **The charger is NiMH-only and off by default.** The BQ25172 charges constant
  current only, never starts on a cell resting above ~1.33 V, stops above 1.65–
  1.75 V a cell (VSET 36k: two cells), has a 4 h safety timer and the pack's NTC
  (0–45 °C; with the pack off, TS floats to its clamp and reads too cold: no
  charging). A transistor holds its TS pin low until the MCU, having identified both
  cells as NiMH, lets go; a reset or a dead MCU puts it back. 83 mA (0.1 C, the eneloop
  standard charge) or 250 mA (0.3 C, ended by firmware on the pack's own signs). Fed
  from VBUS: a full two-cell pack (2.9–3.0 V) leaves too little headroom under the
  3.3 V rail. It burns ~0.65 W at the start of a 250 mA charge and regulates its own die
  at 125 °C. The pull-up holding it off stays on the USB 3.3 V rail (5 V would exceed
  the MCU pin's rating while the MCU is unpowered).
- **The MCU reads the pack** at the receptacle's + contact (1M/1M), its − contact (so
  the reverse FET is not part of the reading) and its mid-point (1M/1M, PA6), so
  firmware reads each cell on its own; and its temperature through the pack's NTC
  (`CHG_TS`), which is biased only while USB is in. No battery gauge: the MAX17048 was a Li-ion gauge.
- **The backlight pump runs from V3V3**: with one AAA there is no battery-level
  rail. 3.3 V is too close to the LEDs' ~3.1 V for the pump's 1x mode at full
  current, so expect ~1.5× the LED current at full brightness: the biggest load.

## Power on and off

The power key is the joystick's centre press, held. The press grounds `JOY_CEN`,
which turns on a PNP (MMBT3906, 1M base-emitter, 100k to the contact) from the cell
into `BTN`; `BTN` pulls the LTC3539's SHDN up to the cell through one diode of a
BAT54C; the MCU holds it through the other (`PWR_HOLD`). SHDN's guaranteed high is
0.88 V, so the button starts the board from a cell resting at ≥ ~1.05 V. 1 µF on
SHDN with 1M (and the pin's own 4M) pulling it down keeps the boost on ~0.6–0.7 s (the
0402 1 µF keeps ~0.5–0.7 µF at 3 V) through
any MCU reset (watchdog, brown-out, firmware update, debugger); from a button press
on a 1.2 V cell it holds ~0.25 s, which the boardloader beats by far. An NPN, not a
FET (it must switch from a 0.9 V cell), turns `BTN` into `BTN_N`, so while running the
MCU reads the same centre press (a short press selects, a hold powers off). Off,
the board draws ~1 µA from the cell (≤ 2.5 µA at datasheet maxima), less than the cell's own self-discharge
(power-aaa.md#standby-off-is-the-resting-state). There is no side power button. Firmware
powers off by releasing `PWR_HOLD` and waiting ~2 s. With USB plugged in the board
is powered whatever the latch says; firmware keeps the screen dark and charges a
NiMH cell. There is no charge light.

**On USB with the pack off, the centre press does nothing.** The device is on for as long as
USB is plugged in and cannot be switched off; unplug it to turn it off. The press
reaches the board only through the cells, so with no pack it neither selects nor powers off:
the touch screen and the joystick's four directions still work. This is intended.

## Buzzer and joystick

- **Buzzer**: a 5 × 5.5 × 2 mm magnetic transducer (Huaneng MLT-5020, 75 dB at
  10 cm, 4 kHz) in the north-east corner, its sound port facing a 2 mm hole in the
  back of the case, driven by an AO3416 from LPTIM3 (TIM3 already clocks the camera).
  A MEMS microspeaker needs a 15–30 V driver and none is stocked at JLC; the louder
  7.5 mm transducers have a 9 mm land that no free spot takes.
- **Joystick**: an ALPS SKRHAAE010 (four directions and a centre push, 7.5 × 7.5 mm,
  SMD, reflowed by JLC) on the front, centred below the cell. The board grew 2 mm at
  the south for it. USB-C moved east of centre (its shell legs go through the board
  where the joystick's land is), and the joystick sits 0.2 mm north of the radio
  antenna's keep-out, as close under the cell as the land allows; the cell's clips
  went 0.25 mm north, as far as the touch tail's slot lets them. Its stem stands through a hole in the case and stops 0.25 mm under the front
  face, so a printed cap goes on it. Every line between the chin and the MCU crosses
  one of the two bridges beside the display cutout, so the four directions
  share one ADC line: each switches its own resistor (15k/27k/47k/100k) from a node
  pulled up by 15k to GND. The ADC, referenced to the same 3.3 V, tells every single
  direction and every pair (diagonals) apart: 110 mV between neighbouring codes,
  39 mV of margin with all the 1 % resistors at their limits. The push has its own
  line.

## IMU

The Bosch BMI270 sits on the back north of the touch connector, turned so its I2C
lands face east and its interrupt south-west, with a 100 nF decap at each supply pin
(VDDIO west of the part, VDD north of it). It shares I2C3 and its 4.7k pull-ups with
the touch controller (0x38): SDO strapped to GND gives 0x68, CSB tied to 3.3 V selects
I2C. The unused secondary/OIS pins are left open, as the data sheet allows
(BST-BMI270-DS000-08 Table 22; ASDx/ASCx must never go to GND).

- **What for**: pick-up-to-wake while the device is asleep (its any-motion
  interrupt wakes the MCU from Stop), and orientation. It runs from the 3.3 V rail,
  so it is off with the device; it does not wake the device from off.
- **Interrupt on PC15**: the east-side pin with a straight run to the part. With no
  32 kHz crystal PC15 is a plain GPIO; as one of the backup-domain pins it is limited
  to 3 mA and 2 MHz as an output, which an input does not care about.
- **The spot east of the MCU** (between the reset cap and the touch connector) fits
  the part but only one of its decaps, and that one sits in the interrupt's path; the
  touch tail's lane rules out anything further east.
- **No vias under the part**: a rule area keeps tracks and vias out from inside its
  ring of lands (a router once dropped the CSB via there).

## Enclosure

A two-part case: a back **shell** with the walls up to the board's front face,
and a front **bezel** with the display window whose skirt laps over a tongue on
the shell. Plastic: always see-through, in four colours: ice blue, orange and
purple (all transparent tints) and clear.

- **Built from the board, not typed in.** The generator reads the outline, slots,
  mounting holes, camera keep-out, the cell's clips and the joystick
  from the board file, and every fitted, modelled part from KiCad's own 3D export.
  The deepest part sets the back cavity; whatever crosses the board edge (the USB-C
  mouth, the airgap lever) gets a wall opening sized from its geometry, and a part
  crossing the edge with no opening rule stops the build. Parts with no 3D model
  each carry a height bound, so a new unmodelled part also stops it. Parts not
  fitted are left out.
- **Thickness, front to back:** bezel lip 0.8, panel 4.13 (Newhaven drawing) on
  0.3 foam, board 1.0, back cavity 4.0, back 1.2: **11.5 mm** (case 52.2 × 91.2 mm), 6.5 mm less than
  proto 0's 18.0. The back cavity is set by the USB-C receptacle (3.40 mm). Two things stand
  out of it on purpose: the **camera head** (5.9 mm, through an open window in the
  back, 0.7 mm proud) and the **AAA cell** with its clips (through an opening in the
  bezel's chin, 5.4 mm proud of the front).
- **Fastening:** the screen sits straight on the board, so the two holes under its
  corners have no room for a boss: the shell's standoffs carry **locating pins**
  there. The **screw** (M2×8 pan head from the back, flush in a counterbore; one since
  the west hole went) goes into the bezel's solid chin at the south-east hole, 3.5 mm of thread
  (the generator refuses less than 2.5). The north edge is held by a **hook**: a
  30 mm ridge inside the bezel's skirt that drops into a groove in the shell's
  tongue.
- **Assembly:** screen onto the board on foam tape, ribbons through the slots into
  their connectors; board into the shell onto the pins and standoffs; bezel hooked
  in at the north and swung down; the screw; the cell into its clips from the
  front.
- **Standoff at the camera corner** is notched for the camera head; the pin there
  passes 0.53 mm from the head.
- **Draft:** both halves are 1 mm narrower at the back and front faces than at
  the parting line (about 3°): softer, mouldable, still printable without
  supports (the shell on its back, the bezel face down).
- **Airgap switch:** its lever stops about 1 mm inside the wall, so a printed
  slider works it from outside. The slider sits in a window in the left wall,
  held in by a flange on the inside, and its fork takes the lever as the board
  goes in. Its ribbed grip stands 0.6 mm out of the wall between two guard
  ridges just as tall: a thumbnail moves it, a pocket does not. The window lets
  it go only just past the switch's own end stops.
- **Joystick:** a pocket over its housing in the solid chin and a 6 mm hole its
  stem rocks in; the stem stops 0.25 mm under the front face, so a printed cap goes
  on it. Between the clips the cell opening is only the cell's width plus 0.3 mm,
  which leaves 0.7 mm of bezel between it and the joystick hole; the build stops
  if that strip gets under 0.6 mm.
- **Untested:** nothing is printed yet. The thread in the bosses, the north hook's
  fit, the lever reach, the cell opening, the joystick cap and how loud the buzzer is
  through its hole all need a print and a board in hand.

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
3. **Camera choice**: the GC2145 module (Camemake CM_DVP_GC2145_UNIVERSAL), the
   commodity successor to the end-of-life OV2640, which no big distributor stocks.
   Camemake's open CM_ESP_P4_C5 board feeds it 2.8 / 1.2 / 3.3 V on the same pins
   as here. Its 115° lens is read through the sensor's 640×480 centre crop (about
   48° across), which needs no refocus: a simulated phone QR decoded as well as
   through an OV2640 refocused to 150 mm. On 10 samples, measure the flex length
   (a stock 20.5–21 mm flex bows ~4.5 mm here) and the head height against the
   case, check the flex's contact face against the bottom-contact connector, and
   time an animated UR: the sensor has a rolling shutter (Keycard Shell moved to a
   global-shutter SC031GS for that reason; it would need 1.8 V I/O, a board change).
4. **Touch tail contact face**: settled on proto 0 and by Newhaven's side view:
   the contacts face the board, so the connector is bottom-contact (CXH25).
5. **Radio UART pins**: P1.09 (module TX, pad 17) and P0.05 (module RX, pad 15),
   full-speed I/O. Every GPIO on the module's north edge is "low frequency I/O
   only" in the nRF52840 pin table (up to 10 kHz), so none carries the UART. The
   TXU0202's inputs carry 5 MΩ pull-downs, so both lines idle low until driven:
   discard one framing error at start-up, and keep the MCU's RX pin pull-up on
   while the radio is off.
6. **Secure element generation**: the board carries the current SE050E2
   (the C1 is "previous generation" in AN12436). Same land as the C1 and the
   SE051C2 fallback. Firmware must use applet 7.x behaviour: I2C at or below
   1 MHz (no clock stretching), PINs of 4+ bytes, the E2's SCP03 keys. JLC
   holds 585 (2026-10-18), enough for prototypes; for 1,000 units consign from DigiKey
   (~14,000 in stock, 16-week lead) or use JLC Global Sourcing. The OPTIGA is not
   fitted on the first boards; firmware must run without it.
7. **Firmware settings the hardware relies on**:
   - BOOT0 (PH3) is tied to GND: the ROM bootloader is entered through the option
     bytes (nSWBOOT0/nBOOT0) over SWD; PH3 must never be made an output. Recovery
     of a locked-up part is connect-under-reset on TP_NRST.
   - The power key's BTN_N (the joystick's centre press) uses PA0's internal pull-up, kept through
     Standby/Shutdown with PWR_PUCRA + APC (check in RM0456 at bring-up).
   - Camera XCLK is TIM3_CH2 PWM on PB5 at 160/8 = 20 MHz (the OV2640 wants a
     45–55 % duty cycle, so no odd divider). Hold CAM_RST (PE0) low while CAM_EN
     rises, then release it; the TLV70312 has no output discharge.
   - VREF+ shares VDDA's caps: keep the internal VREFBUF off.
   - The touch controller's RST has no pull: pulse it low (≥ 100 µs, Newhaven's
     power-up sequence) after boot before talking to it.
8. **Camera core supply 1.2 V** matches what Camemake's reference host and the
   Ai-Thinker ESP32-CAM feed a 24-pin DVP module. An OV2640 module runs there too,
   though its data sheet v2.2 asks 1.24–1.36 V (v1.6: 1.14–1.26 V); the TLV70313
   (1.3 V) fits the same land if an OV2640 ever needs it.
9. **Device-bound PIN KDF** (research.md): measure the SAES rate keyed by the
   DHUK, pick the chain length for about one second, and confirm in our
   TrustZone/RDP configuration that software cannot read the DHUK.
10. **AAA clips**: the pair's installed length (47–48 mm) is computed from the
    drawings; fit a sample pair and a 44.5 mm cell (and a 42.9–46.5 mm 10440)
    before the order. Check that the − clip's arms do not cut a cell's label.
11. **Cell identification**: measure the + and − clip readings against a solid
    metal dummy cell first (the clips' own resistance), then set the resting-voltage,
    resistance-step and trial-charge thresholds on real NiMH, alkaline and lithium
    cells in these clips (power-aaa.md). Until then, firmware should ask the user.
12. **Boost**: rail ripple and transient at 0.9–1.5 V in and at full load; the
    rail with a 10440 at 4.2 V (it must stay at 3.3 V) and when a cell is inserted;
    the USB-to-cell handover dip (expected ~3.0 V for microseconds). The output caps are
    two 47 µF 6.3 V 0805s (CL21A476MQYNNNE): their real capacitance at 3.3 V is not
    published, and the LTC3539 wants ≥ 22 µF real; a load step that rings says add one.
13. **Reverse cell**: insert a cell backwards with and without USB: nothing may
    warm, the rail must stay up on USB, and firmware should read the cell negative.
14. **Charger**: confirm the charger stays off while the MCU is in reset, that its
    TS NTC (on the back, under the cell) tracks the cell, and the charge time at 83
    and 250 mA.
15. **USB unplug handover**: scope V3V3 for 1 s after pulling the cable, with the
    boost held on: it must not dip (power-good's pull-up comes from VBUS for this).
16. **Start-up from a low cell**: start from 1.05 V with several AO3416s; the FET's
    threshold reaches 1.1 V, and start-up current partly through its body diode can
    pull the boost's input under 0.88 V. A FET with a lower threshold fixes it.
17. **Programming order**: the radio is flashed after the MCU (the MCU's firmware
    switches RADIO_3V3 on; the jig takes GND and 3.3 V from the MCU's pads), so the
    radio's own SWD pads are only SWDIO and SWCLK.
18. **Joystick directions**: the ALPS drawing's A/B/C/D (pins 1/4/3/6) are the
    netlist's; LCSC's symbol swaps B and D. Confirm each direction's ADC code.
19. **IMU**: read the BMI270's chip ID (0x24) at 0x68 on the touch bus, load its
   config file, and check that an any-motion interrupt on PC15 wakes the MCU from Stop.
   Measure the asleep current with it in suspend (data sheet: 3.5 µA).
20. **Display flex fold**: fit the first panel and check that the TFT root folds in
    the cutout near y 66.8, the touch flex around it, and that both tails reach their
    connectors with a gentle bow (computed: TFT 2.5 mm, touch 1.5 mm past the
    contacts). If the touch tail is short, fold both further north. Check the touch
    flex's stiffened block lying on the back (x 29–43.5, to y ~54) against the case.
21. **Pack contacts ESD**: zap the bare pack contacts (IEC 61000-4-2, ±8 kV contact)
    with the pack off; the charger, the ADC readings and the reverse-cell FET must
    survive.


## Flow

The commands that build and check the board, and the order set:
[board-flow.md](board-flow.md).
