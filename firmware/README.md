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

With an ST-Link on the front SWD pads (MCU), and the board powered:

1. Turn on TrustZone once, with STM32CubeProgrammer, as Trezor documents
   (`trezor-firmware/docs/core/misc/trustzone.md`): option bytes **TZEN = 1**
   and **SECBOOTADD0 = 0x0C004000**.
2. Write the combined image:

   ```sh
   openocd -f interface/stlink.cfg -c "transport select hla_swd" \
     -f target/stm32u5x.cfg \
     -c "init; reset halt; flash write_image erase firmware/out/ambire_p0/combined-firmware.bin 0x0C004000; reset; exit"
   ```

On its first boot the boardloader programs the rest of the option bytes and
restarts. Development builds leave readout protection at level 0, so SWD stays
open; `--production` would set level 2, which locks SWD for good.

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

What the build drives (compiled, not yet run on a board): the display, touch,
USB, the boot chain and TrustZone. The backlight is on/off only.

What is not driven yet: the OPTIGA (not fitted on the first boards) and the
SE050, the radio (Trezor's link needs SPI and four control lines, our board
has a 2-wire UART), the camera, the charger and the battery gauge. Nothing
releases `PWR_HOLD` yet, so the firmware cannot switch the device off.

## Bring-up checks

Things only a board in hand can answer:

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

The T3T2 emulator did not build at `5fdc180`, for Trezor's own devkit either
(chip attestation and the OPTIGA certificate exist only for the T3W1
emulator).
