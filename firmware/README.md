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
firmware/swd.py flash out/ambire_p0/bootloader.bin --at 0x0c016000 --live
                            # the bootloader only, into a running device: halts it
                            # instead of resetting
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
| `rust/src/translations/ambire_strings.rs` (new), `translations/{mod,translated_string}.rs` | `[ambire_branding]`: the 24 English strings Delizia shows that name Trezor, Trezor Suite or trezor.io, reworded for Ambire ("the Ambire app", ambire.com). Looked up before the built-in English and any translation blob; `en.json` and the generated tables stay as upstream has them. After a sync, `branding/check_strings.py` fails on a new string naming Trezor (and, given `out/ambire_p0`, lists what the binaries still say) |
| `rust/.../layout_delizia/bootloader/{welcome,menu,mod}.rs`, `res/hs_flame.toif` | `[ambire_branding]`: the bootloader's own texts (welcome "with your Ambire at ambire.com", "REBOOT AMBIRE", "Resetting Ambire", the reset results); the home screen flame redrawn with the fixed pixel order |
| `boards/ambire_p0.h`, `io/usb/usb_config.c`, `io/usb/stm32/usb.c`, `io/gfx/rsod.c`, `sec/rsod/rsod_special.c` | `MODEL_USB_INTERFACE` ("Ambire Interface"), `MODEL_WEBUSB_URL` (the landing page a bootloader without firmware offers: ambire.com) and `MODEL_SUPPORT_FOOTER` ("Please visit ambire.com" on error screens) in the board header, each beside Trezor's default |
| `rust/.../component/ambire_game.rs` (new), `homescreen.rs` | `[ambire_game]`: a raycaster maze behind 7 quick taps on the home screen |
| `projects/kernel/ambire_power.c` (new), `main.c`, `build.rs`, `Cargo.toml`, `project.toml` | `[ambire_power]` (proto 0's power latch): hold the power key 3 s and the device switches off (screen dark at once, power gone ~1 s after the key is let go, USB in or not). The press that switched it on does not count. If it is still running 3 s after the key is let go, it restarts |
| `projects/kernel/ambire_radio.c` (new), `main.c`, `build.rs` | `[ambire_radio]`: the kernel allows the radio's power (RADIO_OFF low; the airgap switch still decides) and says hello on USART3 at boot. Off since `[ble]`: one owner of USART3 (a build with both stops with an error) |
| `io/nrf/stm32u5/nrf_uart_link.c` (new), `io/nrf/build.rs`, `io/Cargo.toml` | `[nrf] link = "io/nrf_uart_link"`: Trezor's BLE stack talks to the radio over the 2-wire UART instead of SPI with handshake lines (see [Bluetooth](#bluetooth)) |
| `xtask/src/config.rs` | `exclude_from = [...]` in a board file keeps a peripheral out of the named projects: Bluetooth stays out of the bootloader and prodtest |
| `projects/firmware/src/stm32/main.c` | a production build skips the radio's pairing-secret check on the UART link (there is no secret) |
| `rust/.../layout_delizia/ui_firmware.rs`, `component/homescreen.rs`, `component_msg_obj.rs` | Delizia's Bluetooth pairing screens (device name, pairing code, waiting), and a swipe up on the home screen |
| `src/apps/homescreen/__init__.py`, `src/apps/management/ble/pair_from_homescreen.py` (new), `src/ble.py` | the swipe up starts pairing; the boot waits 0.5 s, not 5 s, for a radio that the airgap switch may have off |
| `src/apps/homescreen/__init__.py` | firmware signed with a "DO NOT USE" vendor header (Trezor's dev keys) says so on the home screen: "Backup needed; dev", or "Dev firmware" when there is no other notice |
| `io/camera/` (new: `stm32u5/camera.c`, `qr/`, `inc/io/camera.h`, `inc/io/qr_scan.h`), `io/build.rs`, `io/Cargo.toml`, `sys/syscall/` (numbers, dispatch, verifiers, stubs), `rust/build.rs`, `rust/trezorhal.h`, `rust/src/trezorhal/camera.rs` (new) | `[ambire_camera]`: the QR camera. The kernel drives it (power, XCLK, SCCB, DCMI snapshots by GPDMA into a buffer the app owns); the app decodes with quirc (vendored in `io/camera/qr/quirc`, see its `README.ambire`). See [QR camera test](#qr-camera-test) |
| `rust/.../component/ambire_camera.rs` (new), `shape/utils/imagebuf.rs` | `[ambire_camera]`: the camera test screen, in the airgap menu |
| `io/airgap/` (new), `sys/syscall/`, `rust/src/trezorhal/airgap.rs` (new), `homescreen.rs` | `[ambire_airgap]`: the airgap switch's position (AIRGAP_SENSE) for the UI: the swipe up opens the airgap menu on AIR, Bluetooth pairing off AIR |
| `rust/.../component/ambire_ur.rs` (new) | `[ambire_camera]`: BC-UR without a heap: minimal bytewords and CRC32, one-part and animated (fountain-coded) URs, as the Ambire app's @ngraveio/bc-ur writes them; strict, every fault an error |
| `rust/.../component/ambire_airgap.rs` (new), `component/mod.rs`, `homescreen.rs`, `component_msg_obj.rs`, `ui/api/firmware_micropython.rs`, `librust_qstr.h`, `mocks/generated/trezorui_api.pyi` | `[ambire_camera]`: the QR scan screen (camera, quirc, UR assembly in the image buffer), the screen showing a UR as a QR code, the camera test as a screen of its own; `trezorui_api.ambire_scan_ur`, `ambire_show_ur`, `ambire_camera_test`; the home screen's swipe up on AIR returns `"ambire_airgap"` |
| `src/apps/ambire_airgap/` (new), `src/apps/homescreen/__init__.py`, `upymod/{build.rs,Cargo.toml,qstrdefsport.h}`, `projects/firmware/Cargo.toml` | `[ambire_camera]`: airgapped signing with the Ambire app (see [Airgapped signing](#airgapped-signing)): the menu, connecting, the strict parsing of requests, and Trezor's own Ethereum signing run on them |

**Trezor's name, where it stays** (none of it is shown on the device's
screens; `branding/check_strings.py out/ambire_p0` accounts for each):
`Features.vendor` "trezor.io" (trezorlib checks it), the FIDO attestation
certificate ("Trezor FIDO Root CA"), the "Trezor Vendor Header" hash domain and
the dev vendor header itself, the names of the third-party "Trezor Steakhouse"
Ethereum vaults (the contracts' own names), error messages sent to the host,
and the USB VID/PID. Debug-only text (the MicroPython REPL banner, debuglink
errors) and code for features this board does not build (the bootloader's
Bluetooth, the SD card, prodtest) keep it too.

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
SE050 beyond its random number generator, the camera (beyond airgapped
signing and its test screen below), the charger and the
battery gauge. On proto 0 the power key switches the device off only from the
firmware: in the bootloader, unplug the battery.

## Bluetooth

The radio runs Trezor's Bluetooth firmware stack; the main chip talks to it
over the board's two-wire serial line (1 Mbaud, no flow control) instead of
Trezor's SPI bus and handshake lines. Messages are Trezor's own, framed with a
start byte, a length and a checksum; a damaged message is dropped, not
retried.

- **Pairing:** on the home screen, with the airgap switch off AIR, swipe up. If Bluetooth is off, the device
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

## Airgapped signing

`[ambire_camera]` in the board file builds in signing with the Ambire app over
QR codes, with no cable and no radio. **The swipe up on the home screen
follows the airgap switch** (`[ambire_airgap]`): on AIR it opens the airgap
menu, off AIR Bluetooth pairing. The menu has three entries; the cross at the
top closes it.

- **Connect to Ambire** shows a QR code: in the Ambire app, add a hardware
  wallet that connects by QR code and scan it. The app then lists this
  wallet's addresses. It needs to be done once per wallet (and again for each
  passphrase).
- **Scan request**: when the app shows a QR code to sign (still or animated),
  point the camera at it. The bar under the picture fills as the parts of an
  animated code come in; the picture's frame flashes green for each one taken.
  Tap the picture to change the zoom; the top-left corner cancels. The device
  then shows what is to be signed, with the same screens and warnings as when
  signing over USB, and after you confirm it shows the signature as a QR code:
  scan it with the app, then tap Done.
- **Camera test**: the camera test below, for bring-up.

What can be signed: transactions (legacy and EIP-1559), messages
(personal_sign) and typed data (EIP-712, including Safe and Ambire smart
account approvals), for any of the wallet's first million addresses.

The device refuses a request, and says why, when:

- it is for another wallet (another seed, or another passphrase),
- the address in it is not this wallet's at the path in it,
- the transaction's network is not the one the request names,
- it is malformed in any way, or larger than 16 KiB (the app warns before it
  sends one that large),
- the QR codes scanned belong to different requests, or are not a request at
  all (scan again).

With a passphrase set, the device asks for it at every connect and every
request. Nothing is remembered between requests.

Not available: signing several requests at once (Keystone's batch request),
EIP-7702 authorizations, and requests for paths other than
m/44'/60'/0'/0/i (Ledger Live style accounts). Only the OV2640 path has
scanned codes on a board so far; the airgapped signing itself has not run on a
board yet (see `tests/airgap/` for what the host tests cover).

`tests/airgap/run.sh` runs the host tests: the request streams are made with
the Ambire app's own libraries (all four kinds, still and animated, in order,
shuffled, with lost and repeated parts, from mixed parts only), malformed and
inconsistent streams, the digests the device would sign (computed by Trezor's
own Ethereum code on the parsed requests, compared with ethers' and
MetaMask's), and the device's own QR codes read back by the app's decoders.

## QR camera test

The camera test is the third entry of the airgap menu.

- The top of the screen shows the camera's live picture in gray, 240 x 240.
  A QR code in view is decoded on the device; while one decodes, the picture
  gets a green frame, its corners are marked and its text is shown below.
- **Tap the picture** to step through three zoom levels (the middle one first).
  Wider views see more; narrower ones resolve smaller or farther codes.
- **The top-left corner** closes the test and switches the camera off.
- The lines below the picture: the sensor found, the zoom and frame rate;
  frames captured and failed, and the capture and decode times; the last code
  decoded, or what was seen.
- Before the first frame, the second line shows what the camera's sync lines
  did after it started (VSYNC and HREF: share of time high per mille / changes
  seen; PCLK: changes seen). All zero means the sensor is not streaming.
- A frame that comes in wrong is still shown, with a red border and "FAIL",
  the bytes that arrived and flags (`0x100` more data than a frame, `0x400`
  no frame end in time, and the camera interface's own: `0x002` overrun). A
  sheared picture means lines of the wrong length; a partial one, a frame cut
  short.
- If the camera does not start, the screen says why: nothing answered at the
  sensor's address, an unknown chip id, or the register write that failed,
  and which bus addresses did answer. Below that: the camera bus lines'
  resting levels (`SCL 1 SDA 1`: pulled up, nothing holds them down), the bus
  error of the last chip-id read (`0x04`: nothing acknowledged, `0x20`: timed
  out) and whether the camera clock leaves the chip (`XCLK` level changes
  seen; 0: none). When nothing answers, the camera stays powered and clocked
  on that screen, so its supplies (2.8 V, 1.3 V) and clock can be measured,
  until the test is closed. Tap to retry.

Supported sensors: the OmniVision OV2640 (the module proto 0 was drawn for)
and OV5640 and the GalaxyCore GC2145 modules in the ESP32-CAM pinout, told
apart by their bus addresses and chip ids. On proto 0 the module's pin 1 (its
own numbering: NC) goes at the end of the connector nearer the board's top
edge. Each zoom level is a
sensor mode from Espressif's esp32-camera driver (the register tables in
`io/camera/stm32u5/camera_sensors.h` are generated from it by
`bringup/camera/gen_sensor_tables.py` and, for the OV2640,
`bringup/camera/dump_ov2640_tables.c`). Works on proto 0 with its OV2640
(2026-11-09); the OV5640 and GC2145 paths are not checked on a board yet.

## On proto 0 (2026-10-30)

Proto 0 boots to the home screen, pairs with `trezorctl` over USB (THP, code
entry), creates a wallet and signs: the first signed transaction and its check
are in `bringup/proto0-first-signed-tx.json`.

- Firmware updates go over USB: hold no button, but tap the screen repeatedly
  right after pressing power to stay in the bootloader ("Waiting for host"),
  then `trezorctl firmware update -f out/ambire_p0/firmware.bin`. A running
  Trezor Bridge (`trezord`) or Trezor Suite on the same computer takes the
  device over mid-pairing: stop them first.
- **No brown-out on USB plug or unplug** (owner, 2026-11-03): running on the
  cell, plugging USB-C in and pulling it out leaves the device running; it does
  not reset or switch off.
- **No way to switch it off** (owner, 2026-11-03): nothing released the power
  latch, and the power key was not read at all. Since then `[ambire_power]`
  switches it off on a 3 s hold of the power key (see below); works on the
  board (owner, 2026-11-03).
- **USB does not switch it on**: plugging USB-C in charges the cell, but the
  device starts only from the power key, with or without the cell. As
  designed: the 3.3 V regulator's enable listens only to the key and the
  latch.
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
  Open (2026-10-31): with the same wiring, on USB power, reads are correct while
  the core is halted, held in reset or running the bootloader, and come back
  corrupted (the reply shifted by one bit, at every clock speed) while the
  firmware runs. Not explained yet. The firmware does not touch the SWD pins or
  the debug registers and never enters deep sleep (STOP2 is not linked in).
  `bringup/swdprobe/run.sh debian@<beagle> 20 GPIO22` reads only the ID, which
  is safe while the firmware runs.
- Reading memory over SWD while the firmware runs can crash it: an `info`
  during normal use ended in "internal error IA" (the TrustZone controller's
  illegal-access fault, reported at whatever the CPU was doing, here the idle
  wait). Halt first (`state`, `flash --live`), or read while the bootloader runs.
- A plain `flash` resets the chip first and the screen goes dark until the new
  code runs: that is the core held in reset, not the board switching off.
- A USB firmware update replaces the bootloader only when the bundled one has a
  newer version: a rebuilt bootloader of the same version (the branded one)
  goes on over SWD.

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
