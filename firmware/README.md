# Firmware

The signer runs Trezor's firmware, ported to this board. `trezor-firmware/` is
[trezor/trezor-firmware](https://github.com/trezor/trezor-firmware) `main` at
`5fdc180` (2026-10-16), committed here without its `.git`. Its submodules are
not committed: `vendor-pins.txt` pins each one and `fetch-vendor.sh` checks
them out.

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

## What the port changes

Against upstream `5fdc180`:

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

The T3T2 emulator does not build at `5fdc180`, for Trezor's own devkit either
(chip attestation and the OPTIGA certificate exist only for the T3W1
emulator).
