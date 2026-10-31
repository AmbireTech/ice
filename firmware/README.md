# Firmware

The signer runs Trezor's firmware, ported to this board. `trezor-firmware/` is
[trezor/trezor-firmware](https://github.com/trezor/trezor-firmware) with our
changes on top, committed here without its `.git`. The first line of
`vendor-pins.txt` names the upstream commit it is based on (`5fdc180`,
2026-10-16, when the port started); a weekly sync moves it forward (see
[Keeping up with Trezor](#keeping-up-with-trezor)). The submodules are not
committed: `vendor-pins.txt` pins each one and `fetch-vendor.sh` checks them
out.

The board is a new board of Trezor's T3T2 model (the Safe 5 family at
240×320): `T3T2/boards/ambire_p0`. The firmware therefore reports itself as a
T3T2, and is signed with Trezor's public **development** keys. It is for the
bench, never for funds.

## Build

Needs Docker (on macOS: colima or Docker Desktop). The first run builds the
toolchain image from Trezor's own Nix setup (~11 GB, about 20 minutes).

```sh
firmware/docker-build.sh            # boardloader, bootloader, firmware + combined image
firmware/docker-build.sh firmware   # one stage
firmware/docker-build.sh -- 'xtask build firmware -m T3T2 -b ambire_p0 --bootloader-devel -p dev'
BOARD=devkit firmware/docker-build.sh   # Trezor's own T3T2 devkit, for comparison
```

The binaries land in `firmware/out/<board>/`. `combined-firmware.bin` is the
whole chain (boardloader, bootloader, secure monitor, firmware) for a blank
chip.

## Flash a blank board

`swd.py` drives OpenOCD and gdb (`brew install open-ocd arm-none-eabi-gdb`).
It takes an ST-Link by default (`--probe cmsis-dap|jlink` for others).

```sh
firmware/swd.py info        # the chip, its option bytes, what is in flash, the boot command
firmware/swd.py trustzone   # once per chip: TZEN = 1, SECBOOTADD0 = 0x0C004000
firmware/swd.py flash       # out/ambire_p0/combined-firmware.bin, written and verified
firmware/swd.py state       # halt: where it runs (symbolised), fault registers,
                            # backtrace, the error behind a red screen; then resumes
firmware/swd.py gdb         # interactive, with every stage's symbols
```

**No probe: a Raspberry Pi is one.** OpenOCD on the Pi drives SWD from two
GPIO pins (any model, Pi 5 included); `swd.py` runs it over SSH and points gdb
at it over the network. Wire the Pi's header to the board:

| Pi header pin | Pi signal | Board pad |
|---|---|---|
| 22 | GPIO25 | `TP_SWCLK` |
| 18 | GPIO24 | `TP_SWDIO` |
| 20 | GND | `TP_GND` |
| 12 | GPIO18 | `TP_NRST` (optional, for `--under-reset`) |

A BeagleY-AI has the Pi's header layout: wire it as a Pi and use `--probe rpi`
(`setup` checks that its Linux names the lines as a Pi does). A BeagleBone Black or Green works the same way (`--probe beagle`), on its P9
header: P9_12 → `TP_SWCLK`, P9_15 → `TP_SWDIO`, P9_1 (GND) → `TP_GND`, P9_23 →
`TP_NRST` (optional). The pins are found by name on the board, so the model's
gpiochip numbering does not matter.

Leave the probe board's 3.3 V pin off the board. Then, with `SWD_PROBE=rpi`
(or `beagle`) and `SWD_HOST=user@<address>` set (or `--probe … --host …`), run
`setup` once (it installs OpenOCD and gpiod there; it needs OpenOCD 0.12 or
newer) and the commands above. Bit-banged SWD
is slower: writing the full image takes minutes, not seconds.

`trustzone` is what Trezor does in STM32CubeProgrammer
(`trezor-firmware/docs/core/misc/trustzone.md`). If OpenOCD cannot set the
option bytes, the script says so; set them there instead. On its first boot
the boardloader programs the rest of the option bytes and restarts.
Development builds leave readout protection at level 0, so SWD stays open;
`--production` would set level 2, which closes SWD for good.

On proto 0:

- **Wires before the pouch.** The SWD pads (`TP_SWDIO`, `TP_SWCLK`, `TP_NRST`,
  `TP_GND`, `TP_3V3`) are on the front, under where the pouch goes. Solder
  thin wires to them first, so SWD stays reachable for the whole bring-up.
  `--under-reset` needs `TP_NRST` wired; it recovers a chip whose code turns
  SWD off.
- **Hold the button while flashing a blank chip.** The button lifts the
  3.3 V buck-boost's enable; only our boardloader keeps it up (`PWR_HOLD`).
  A blank chip, or one held in reset, loses power about 1 s after the button
  is released. The button needs USB-C (or the pouch) for power. Bridge the
  button's pads for the session if holding it is awkward; once the
  boardloader runs, the latch holds by itself (bring-up check 1).
- **USB data needs the airgap switch off AIR.** In AIR the USB switch's
  enable is high and D+/D- are cut from the MCU (charging still works).
  `swd.py info` prints the switch position (`AIRGAP_SENSE`), USB power
  (`VBUS_SENSE`) and whether the latch holds.

## Keeping up with Trezor

The sync runs one way, Trezor → us: it pulls Trezor's new commits into
`trezor-firmware/` here and keeps our changes on top. Nothing is ever sent to
Trezor.

`sync-upstream.sh` moves the tree to a newer upstream commit and keeps our
changes. It re-applies them as one commit on top of the target in a scratch
checkout (`firmware/.upstream/`, not committed), so a clash with upstream comes
out as an ordinary git conflict, and then copies the result back and rewrites
`vendor-pins.txt`.

```sh
firmware/sync-upstream.sh              # to Trezor's main
firmware/sync-upstream.sh core/v2.12.5  # to a release tag, or any commit
firmware/docker-build.sh               # check it still builds
git commit                             # or pass --commit to the sync
```

It only replays what is committed, so commit firmware changes first. On a
conflict it stops and lists the files: fix them in `firmware/.upstream/work`,
`git add` them there, then `firmware/sync-upstream.sh --continue` (or
`--abort`, which leaves the tree untouched).

**Every Monday** the workflow `.github/workflows/firmware-upstream-sync.yml`
does this on GitHub against Trezor's `main`. It builds our board and Trezor's
devkit, and opens (or updates) one PR from the branch `firmware/upstream-sync`.
A failing build makes the PR a draft with the end of the log. A conflict opens
an issue, "Firmware: upstream sync conflicts", with the commands to resolve it
locally. It can also be run by hand from the Actions tab, with any upstream ref.

For the PR to be opened, the repository (or the organisation) must allow it:
Settings → Actions → General → "Allow GitHub Actions to create and approve pull
requests". Without it the sync branch is still pushed, and the PR can be opened
from it by hand.

Keep our changes easy to replay: add files rather than edit Trezor's, put
switches beside upstream code rather than rewriting it, and never reformat or
move upstream code. Every upstream line we touch can conflict at each sync.

## What the port changes

Against the upstream base:

| Where (`core/embed/`) | What |
|---|---|
| `models/T3T2/boards/ambire_p0.{toml,h}` | the board: pins from `src/parts/mcuAssign.json`, drivers |
| `io/display/i8080/display_io_spi.c` (new) | ST7789 over 4-wire SPI: polled commands, frame buffer by GPDMA as 16-bit frames |
| `io/display/i8080/display_io.h`, `display_driver.c`, `display_fb.c`, `display_panel.c` | `DISPLAY_SPI` switches beside the parallel bus |
| `io/display/build.rs`, `io/Cargo.toml` | the `display_spi` driver |
| `io/touch/build.rs`, `io/Cargo.toml` | the `touch_panel_nhd240320af` mapping (identity for now) |
| `projects/boardloader/main.c` | the power latch: `PWR_HOLD` high as the first thing the chip does |
| `models/T3T2/model.toml`, `boards/devkit.toml` | SMP (the nRF's management link) moved from the model to the devkit board, so boards without the nRF build |
| `sec/se050/` (new), `sec/rng/rng_strong.c`, `sys/rng/inc/sys/rng_use_flags.h`, `sec/build.rs`, `sec/Cargo.toml`, `projects/secmon/{Cargo,project}.toml` | the SE050's random number generator, mixed into Trezor's strong RNG beside the MCU's (and the OPTIGA's, once fitted); `[se050]` in the board file switches it on |
| `sec/trustzone/stm32u5/tz_init.c` | with `[se050]`, the SE050's I2C bus, its pins, its enable line and its interrupts belong to the secure monitor (Trezor does this only for the OPTIGA) |
| `io/display/i8080/display_io_spi.c` | SSI set before master mode (the other order is a mode fault: the SPI never started) |
| `io/display/i8080/panels/dem240320b1.c` | TEON when the board wires TE; `DISPLAY_PANEL_MIRROR_X` (the Newhaven panel's columns run the other way) |
| `sys/mpu/stm32u5/mpu.c` | the secure monitor's MPU covers AUX2 too (the app's stack: its random-number requests faulted) |
| `sec/se050/stm32/se050.c` | GetRandom reads long-form TLV lengths (the SE050 answers `41 82 00 20` + data) |
| `rust/src/ui/shape/utils/imagebuf.rs`, `models/T3T2/memory_secmon.{h,ld}` | the UI image buffer holds a whole 240x320 screen (the home screen never appeared); AUX1 takes 24 kB from AUX2 for it |
| `common/defs/support.json` + regenerated coin/network/token tables | T3T2 added as a copy of T3T1 (every coin lookup failed) |
| `src/trezor/wire/thp/ui.py` | Delizia has no THP pairing screen: the generic confirmation, Eckhart's wording |
| `src/trezor/wire/message_handler.py` | Ambire boards report the exception behind "Firmware error" |
| `models/T3T2/model_T3T2.h`, `boards/ambire_p0.{h,toml}`, `rust/.../theme/mod.rs`, `welcome_screen.rs`, `src/boot.py` | branding: the board names the product "Ambire"; `[ambire_branding]` puts the flame on the welcome screen (held 3 s) |
| `rust/.../component/ambire_game.rs` (new), `homescreen.rs` | `[ambire_game]`: a raycaster maze behind 7 quick taps on the home screen |
| `projects/kernel/ambire_radio.c` (new), `main.c`, `build.rs` | `[ambire_radio]`: the kernel allows the radio's power (RADIO_OFF low; the airgap switch still decides) and says hello on USART3 at boot. Off since `[ble]`: one owner of USART3 (a build with both stops with an error) |
| `io/nrf/stm32u5/nrf_uart_link.c` (new), `io/nrf/build.rs`, `io/Cargo.toml` | `[nrf] link = "io/nrf_uart_link"`: Trezor's BLE stack talks to the radio over the 2-wire UART instead of SPI with handshake lines (see [Bluetooth](#bluetooth)) |
| `xtask/src/config.rs` | `exclude_from = [...]` in a board file keeps a peripheral out of the named projects: Bluetooth stays out of the bootloader and prodtest |
| `projects/firmware/src/stm32/main.c` | a production build skips the radio's pairing-secret check on the UART link (there is no secret) |
| `rust/.../layout_delizia/ui_firmware.rs`, `component/homescreen.rs`, `component_msg_obj.rs` | Delizia's Bluetooth pairing screens (device name, pairing code, waiting), and a swipe up on the home screen |
| `src/apps/homescreen/__init__.py`, `src/apps/management/ble/pair_from_homescreen.py` (new), `src/ble.py` | the swipe up starts pairing; the boot waits 0.5 s, not 5 s, for a radio that the airgap switch may have off |

What the build drives (compiled, not yet run on a board): the display, touch,
USB, the boot chain and TrustZone. The backlight is on/off only.

New seeds take their randomness from the MCU's generator and the SE050's
together (Trezor's "strong" random path, which until now combined the MCU with
the OPTIGA or Tropic); the host's entropy is hashed in on top, as on every
Trezor. If the SE050 does not answer, the device stops with "SE050 entropy
source failed" rather than continue on the MCU alone. The link to the SE050 is
neither authenticated nor encrypted, so someone probing its I2C lines while a
seed is created sees its share; they still do not see the MCU's.

The device's own keys do not use the SE050 yet:

- The boot hardware key, which binds the PIN-protected storage to the chip,
  comes from the MCU's generator alone (Trezor's boardloader and bootloader
  make it).
- The master key (secret slot 0, behind the storage salt and the device's
  attestation keys) is written once by Trezor's factory test firmware
  (prodtest), which this port does not build, and whose random path has no
  SE050. Until boards are provisioned, development builds run with an all-zero
  storage salt; a production build stops with "Failed to get storage salt".
  Provisioning, with the SE050 in prodtest's random path, is still to do.

Without the OPTIGA, nothing in hardware limits PIN guesses: the PIN key is
stretched in software and bound to the chip by the boot hardware key, so
someone who gets code running in the chip's secure world can try PINs at the
chip's speed.

What is not driven yet: the OPTIGA (not fitted on the first boards), the
SE050 beyond its random number generator, the camera, the charger and the
battery gauge. Nothing
releases `PWR_HOLD` yet, so the firmware cannot switch the device off.

## Bluetooth

The radio runs Trezor's Bluetooth firmware stack; the main chip talks to it
over the board's two-wire serial line (1 Mbaud, no flow control) instead of
Trezor's SPI bus and handshake lines. Messages are Trezor's own, framed with a
start byte, a length and a checksum; a damaged message is dropped, not
retried.

- **Pairing:** on the home screen, swipe up. If Bluetooth is off, the device
  offers to turn it on. It then shows its Bluetooth name: select it on the
  phone or computer, check that both show the same six-digit code, and swipe
  up (or tap) to confirm.
- **Airgap switch:** on AIR the radio has no power, and the device works as
  before without it. Within half a second of the radio going quiet the device
  treats Bluetooth as disconnected; when the switch goes off AIR, the radio
  starts fresh and is picked up again without a restart. Pairing with the
  switch on AIR says the radio is off.
- **Not available:** updating the radio's firmware from the device (flash it
  over the radio's own SWD pads), and the check that the radio is the one
  paired with this device at the factory (Trezor's boards keep a shared secret
  in both chips; ours have none). The bootloader has no Bluetooth: firmware
  updates go over USB.

None of this has run on a board yet: the radio side and the timing (the
radio's start-up, the power-off time between restarts) are unchecked.

## On proto 0 (2026-10-30)

Proto 0 boots to the home screen, pairs with `trezorctl` over USB (THP, code
entry), creates a wallet and signs: the first signed transaction and its check
are in `bringup/proto0-first-signed-tx.json`.

- Firmware updates go over USB: hold no button, but tap the screen repeatedly
  right after pressing power to stay in the bootloader ("Waiting for host"),
  then `trezorctl firmware update -f out/ambire_p0/firmware.bin`. A running
  Trezor Bridge (`trezord`) or Trezor Suite on the same computer takes the
  device over mid-pairing: stop them first.
- The power latch holds, the display runs (mirrored until fixed), touch works
  with the touch tail folded into the connector (the fitted connector's contacts
  are on the other side from the tail's), the SE050 answers and supplies
  randomness.
- SWD from the BeagleY-AI flashed the boot chain, then started returning
  corrupted replies (the first bits of the chip's ID right, then wrong). The
  cause was the probe's SWDIO pin: BeagleY-AI header pin 18 (GPIO24) is the
  AM67A's wake-up-domain UART pad (WKUP_UART0_TXD). With SWDIO on header pin 15
  (GPIO22, a plain main-domain GPIO) every read is correct, at any clock speed
  (`bringup/swdprobe`: 80 of 80). Use `swd.py --probe beagley`.

## Bring-up checks

Things only a board in hand can answer:

0. **Rail and chip, before any code**: with USB-C in and the button held,
   `TP_3V3` reads 3.3 V and `swd.py info` reads DEV_ID 0x482 (STM32U575/585),
   RDP level 0, 2048 KB of flash.
1. **Power latch**: the device stays on after the button is released.
2. **Display**: the panel init is Trezor's DEM240320B1 sequence (same ST7789,
   same resolution), not Newhaven's. Check colours, inversion, gamma and
   orientation. The SPI runs at 40 MHz; a full frame takes ~31 ms, longer
   than one panel refresh, so expect some tearing on full-screen changes.
3. **Touch**: the FT5426 may need longer than the driver's 5 ms after reset
   before it answers on I2C (FocalTech parts often quote up to 300 ms). Check
   the axis mapping against the identity mapping.
4. **Backlight**: the on state is the pin's internal pull-up, which puts about
   2.3 V on the TPS60230's enable through the board's 1 k / 100 k divider.
5. **SE050 random numbers**: the driver was written from NXP's Plug & Trust
   middleware and has never talked to a chip. On the first boot that needs
   strong randomness (first boot, seed creation), check that the device does
   not stop with "SE050 entropy source failed". If it does, look at the I2C4
   traffic: the first frame out is `5A C0 00 FF FC` (resynchronise), the
   SE050 must answer `A5 E0 00 ..`.

The T3T2 emulator did not build at `5fdc180`, for Trezor's own devkit either
(chip attestation and the OPTIGA certificate exist only for the T3W1
emulator).
