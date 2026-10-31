# Radio firmware (proto 0)

Trezor's Bluetooth firmware, built for our radio: the Ebyte E73-2G4M08S1C
module (nRF52840) on proto 0. Trezor's own boards talk to their radio over SPI
with extra handshake lines; ours has only a two-wire serial link, so this build
carries every message over that link instead. The Bluetooth side is Trezor's,
unchanged.

**Status: builds, not yet tried on hardware.**

## Build

```sh
firmware/radio/build.sh            # -> firmware/radio/out/ambire_p0/zephyr.hex
DEBUG=1 firmware/radio/build.sh    # with logging over SWD (RTT) -> out/ambire_p0-debug/
firmware/radio/linktest/run.sh     # test of the serial framing on this computer
```

Needs Docker. The first build downloads the nRF Connect SDK version Trezor pins
(several GB, kept in the Docker volume `ambire-radio-ncs`); later builds take
about a minute.

## Pins

| Module pad | nRF52840 pin | Signal | Goes to |
|---|---|---|---|
| 17 | P1.09 | radio transmit | the STM32's receive, through the level translator |
| 15 | P0.05 | radio receive | the STM32's transmit, through the level translator |
| 37 / 39 | SWDIO / SWDCLK | programming | test pads |

The link runs at 1,000,000 baud, 8 data bits, no parity, 1 stop bit, no flow
control. The radio has no reset line: the STM32 switching its power is the
reset. The low-frequency clock comes from the chip's internal oscillator (the
module has no 32.768 kHz crystal).

## The link

Every message is one frame:

| Byte | Meaning |
|---|---|
| 1 | `0xA0` + service number (0 Bluetooth data, 1 Bluetooth control, 2 management, 3 production test) |
| 2 | payload length, 0 to 251 |
| 3 ... | payload |
| last | CRC-8 (polynomial 0x07, start 0x00, not reflected) of every byte before it |

A receiver that sees a wrong first byte, a length over 251 or a bad CRC drops
one byte and looks again from the next. A frame that stays incomplete for
20 ms is given up the same way. There are no acknowledgements; either side may
be off. After power-up the radio sends a Bluetooth control status message on
its own.

## What this build leaves out

- **The bootloader (MCUboot) and firmware updates through the STM32.** The app
  is a plain image from address 0, flashed over SWD. Because of that, the
  firmware fingerprint the radio reports to the STM32 is all zeros.
- **Trezor's serial echo test mode**: the serial port is the link itself, so
  the "start/stop UART" management commands do nothing.
- **The handshake and signal lines** (ready, request, stay-in-bootloader,
  reserved output): proto 0 does not have them.

## Flashing over SWD

Connect a SWD probe to the radio's SWDIO and SWDCLK pads, with GND and the
3.3 V reference from the main board's test pads. The STM32 firmware must have
the radio switched on (the airgap switch must allow it).

```sh
openocd -f interface/cmsis-dap.cfg -f target/nrf52.cfg \
  -c "init; halt; nrf5 mass_erase; program firmware/radio/out/ambire_p0/zephyr.hex verify reset; exit"
```

Use your probe's interface file in place of `cmsis-dap.cfg`. Newer nRF52840
chips ship with debug access locked (APPROTECT). If OpenOCD cannot halt the
chip, unlock it first with an erase-all through the CTRL-AP; this wipes the
chip:

```sh
openocd -f interface/cmsis-dap.cfg -f target/nrf52.cfg \
  -c "init; nrf52_recover; exit"
```

`nrf52_recover` exists in recent OpenOCD releases; with a J-Link,
`nrfutil device recover` does the same. An erase-all leaves the lock armed for
the next power-up. To keep debug access open on those chips, also write `0x5A`
to the debug-lock word of the chip's user configuration (UICR.APPROTECT,
address `0x10001208`) when flashing; this firmware then opens the debug port at
every start (an image built with Trezor's production settings would lock it):

```sh
openocd -f interface/cmsis-dap.cfg -f target/nrf52.cfg \
  -c "init; halt; nrf5 mass_erase; program firmware/radio/out/ambire_p0/zephyr.hex verify; flash fillw 0x10001208 0x5A 1; reset; exit"
```

None of these commands has been tried on our board yet.
