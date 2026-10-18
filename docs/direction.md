# Product direction (owner, 2026-10-17; status updated 2026-10-18)

Three decisions, and what each one costs. The firmware findings were checked
against trezor-firmware at commit `5fdc180` (2026-10-16); paths below are in that
repo under `core/embed/models/` unless they say otherwise.

## 1. Fully open-source hardware

Everything needed to build one is published: schematic and board (KiCad), the
fab files for JLCPCB and PCBWay (gerbers, BOM, pick-and-place), and the case
(3MF, STL, STEP). The concept page's configurator offers them under **DIY**.
The repo is private today, so the page serves the files itself. Before going
public, pick a hardware licence (CERN-OHL-S fits a GPLv3 firmware). This is
still open.

## 2. Trezor-compatible: start from Trezor's firmware

The owner prefers to start from Trezor's firmware, or a fork of it, ideally
from Model T. GPLv3 is acceptable.

**Status (2026-10-18):** the fork exists. `firmware/trezor-firmware/` is
trezor-firmware at `5fdc180`, built as T3T2 with our own board definition
(`ambire_p0`): pin map, power latch, display over SPI, touch, both I2C buses,
development keys. It is step 2 of the suggested order below, without the radio,
the secure elements, the charger, the battery gauge or the camera so far. The
changes, build and flashing: [firmware/README.md](../firmware/README.md).

**Model T is the wrong base.** It runs an STM32F427 (no TrustZone) with a
240×240 parallel display and no secure element. Trezor still ships fixes for it
but builds new work on the STM32U5. The closest base is **T3T2**, a model added in
2026 (`T3T2/boards/devkit.toml`, `devkit.h`). Its devkit uses:

- STM32U58x
- a 240×320 ST7789 panel (DEM240320B1)
- the ft6x36 touch driver
- the OPTIGA Trust M
- an nRF radio

The Safe 5 (T3T1) shares our MCU and OPTIGA but has a 240×240 screen.

| | MCU | Display | Touch | Secure element | Radio |
|---|---|---|---|---|---|
| Model T (T2T1) | STM32F427 | 240×240, parallel | ft6x36 | none | none |
| Safe 5 (T3T1) | STM32U58x | 240×240, parallel | ft6x36 | OPTIGA | none |
| Safe 7 (T3W1) | STM32U5G | 380×520, MIPI-DSI | FT3168 | OPTIGA + TROPIC01 | nRF52832 |
| **T3T2 devkit** | STM32U58x | **240×320**, parallel | ft6x36 | OPTIGA | nRF54LS05 |
| **Ours** | STM32U585 | 240×320, **SPI** | FT5426 | OPTIGA + SE050 | nRF52840, **UART only** |

**What actually stands in the way:**

1. **The radio link.** In Trezor's design the nRF is the SPI master, with four
   control lines (ready, request, reset, stay-in-bootloader) and a UART with
   RTS/CTS for its firmware updates (`T3T2/boards/devkit.h`,
   `io/nrf/stm32u5/`). Our board has only a 2-wire UART through the airgap's
   level translator. Either the board changes (SPI plus four GPIOs, still
   behind the airgap switch) or we write a UART-only transport on both chips.
   The driver also assumes the nRF is always powered, so "radio off" from the
   airgap switch needs handling.
2. **The key model.** Trezor firmware is a seed-phrase wallet. The seed is
   encrypted in MCU flash, and the OPTIGA only slows PIN guessing and proves
   the device genuine (`docs/core/misc/optiga.md`). Our concept had keys
   generated inside two secure elements with no seed. With Trezor firmware the
   SE050 sits unused; keeping our key model is a deep fork of seed and storage.
3. **It is our own device, not a Trezor.** We sign with our own boot keys and
   use our own model ID and USB PID. Trezor's 0x1209:0x53C0/0x53C1 are theirs
   (`io/usb/usb_config.c`). We also provision the OPTIGA ourselves: pairing
   secret, device certificate and lock, as `projects/prodtest/README.md` does.
   That needs our own certificate authority. Trezor Suite's authenticity check
   (an OPTIGA certificate from Trezor's factory) can never pass. Suite would need
   changes to recognise a new model, or we ship our own companion app. "Works
   with Trezor Suite" is therefore not a given.

**What fits as it is:**

- The UI layouts read the screen size from the panel definition. T3T2 runs the
  Safe 5 layout at 240×320, and there is an emulator build (`T3T2/boards/unix.h`).
- The ft6x36 touch driver should take our FocalTech FT5426 with panel-mapping
  changes only.
- The MCU start-up already supports running from the internal oscillator only,
  which matches our board's missing crystal.
- Ethereum EIP-712 signing exists. ERC-7730 clear signing exists for
  transaction calldata, but not inside EIP-712. A Safe transaction signed as
  EIP-712 would show its inner call as raw bytes, so Safe decoding is our work
  either way.

**Porting work, roughly by size:**

| Small | Medium | Large |
|---|---|---|
| model + board definition, pin map, clocks, memory | SPI transport for the ST7789 at 240×320 | keeping keys inside the secure elements (SE050) |
| FT5426 touch mapping | boot/vendor keys, signing, model ID, USB PID | QR camera (nothing exists) |
| OPTIGA wiring on the shared I2C bus | BQ25185 charger + MAX17048 battery gauge (only an nPM1300 driver exists), soft-latch power | Suite/Connect support, or our own companion app |
| nRF52840 board definition for Trezor's BLE app | radio link: board change, or a UART-only transport | OPTIGA provisioning station + our own CA |
| | Safe clear signing inside EIP-712 | |

**Suggested order:**

1. Run the T3T2 emulator to judge the UI at 240×320.
2. Port to our board without the radio: display over SPI, touch, OPTIGA, power,
   USB. *(Started: display, touch and power latch are in; the OPTIGA is not fitted
   on the first boards.)*
3. Decide the radio question before the next board revision. Moving to SPI
   plus four GPIOs is cheap now and expensive later.

**Licence notes (not legal advice).**

- `core/` is GPLv3. GPLv3 §6 requires "Installation Information" on a consumer
  device. Trezor's own model satisfies it: unofficial firmware runs after a
  warning, with the secrets wiped.
- Trezor's product terms say it creates no artificial barriers to third-party
  firmware.
- The nRF app links Nordic's SDK, which has its own licence; check that pairing.
- We found no Trezor trademark policy. Describe the device as running firmware
  based on Trezor's, rather than using "Trezor" in its name.

Sources:

- https://github.com/trezor/trezor-firmware
- https://trezor.io/learn/a/trezor-safe-device-authentication-check
- https://trezor.io/support/logistics/orders-payments/support-for-older-models
- https://trezor.io/documents/product_terms_of_use.pdf
- https://www.gnu.org/licenses/gpl-3.0.html#section6

## 3. A configurator: order one, or build it yourself

The concept page carries the first version, next to the 3D model.

- **Order to a PO box.** Choose the board print, the case and the address.
  - The name can be anything.
  - Case options: purple, clear, or none. Choosing none ships a bare board and
    shows the case files for printing it yourself.
  - The Order button stays disabled until ordering opens.
- **DIY.** Pick the board maker (JLCPCB is preselected, or PCBWay) and download
  everything:
  - gerbers (zip)
  - bill of materials and pick-and-place (CSV)
  - the case: one zip holding the 3MF, STL and STEP files

  The board files are the next run's, with the flame in white silkscreen. The
  first three boards were ordered before the flame and carry the old wordmark.

### Board print

The back carries only the Ambire flame from the official logo, with no wordmark
and the word "Ambire" nowhere. It is the whole back: as wide as the board less a
thin margin, printed between the parts. Its facets keep a 0.35 mm hairline of
mask between them, so one ink still shows the faceting. The schemes and what
each one takes to make:

| Scheme | Mask | Flame | How it is made |
|---|---|---|---|
| Purple on neon | Volt #D7FF00 | Purple #6000FF | full-colour print (PCBWay) |
| Purple on black | black | Purple | full-colour print (PCBWay) |
| White on black | black | white | ordinary mask + silkscreen, any maker |
| Black on white | white | black | ordinary mask + silkscreen, any maker |
| Purple on white | white | Purple | full-colour print (PCBWay) |
| Incognito | black | none | plain black board, any maker |

JLC's standard silkscreen is white or black only, and no maker offers a Volt
mask, so the three purple schemes need a colour print. JLC accepts colour print
only for files from its own design tool, so those go through PCBWay.

**Done:** the flame is in the board file as ordinary silkscreen
(`scripts/silkFlame.py`), so the white-on-black and black-on-white schemes need
nothing more. **Not done yet:** the colour-print image. The PCBWay print file
(`art/build_uv.py`) still carries the old "light beams" artwork with the
wordmark, so the purple schemes cannot be ordered as shown.
