# Programming jig

A fixture that programs a bare, assembled device board over SWD, before the screen and
the case go on. One board, one USB-C cable to a computer; no separate debug probe.

The jig lies on the table with its parts facing up. The device board goes on it **face
down**: three screws through its corner holes locate it, and seven spring pins press up
into its programming pads (3.3 V, ground, SWDIO, SWCLK and reset for the MCU; SWDIO and
SWCLK for the radio). The jig is a USB debug probe: an RP2040 running Raspberry Pi's open
debugprobe firmware, which speaks CMSIS-DAP. It also powers the board while it is
programmed. The USB-C, the two switches and the two lights sit on a strip beside the
board, where they stay in reach and in sight.

The repo's SWD tool works with it as it is: `firmware/swd.py --probe cmsis-dap …`.

## What it is made of

| Part | How many | Where from |
|---|---|---|
| Jig board, 62 × 93 mm, 4 layers, 1.6 mm, parts on one face | 1 | JLC: `production/jig-order/` |
| M2 hex standoff, female–female, 5 mm, threaded through | 3 | any hardware shop |
| M2 × 10 mm screw | 3 | " |
| M2 nut (a knurled thumb nut is nicer) | 3 | " |
| Stick-on rubber feet, 2 mm or taller | 4 | " |

Every electronic part is JLC stock. The seven spring pins (Xinyangze YZP0063-20068-01)
work at 5.0 mm, 25 g each.

## Putting it together

1. Push the three screws up through the three holes marked by the device outline, from
   the plain face, and thread a standoff onto each from the parts face. Tighten. About
   3.4 mm of each screw sticks out above its standoff: those are the locating pins.
2. Stick the feet on the plain face: the screw heads stand below it.

## First use: load the probe's firmware

1. Download `debugprobe_on_pico.uf2` from Raspberry Pi's debugprobe releases
   (github.com/raspberrypi/debugprobe). The jig is wired exactly as that build expects.
2. Plug the jig into a computer. A new jig has an empty flash and shows up as a USB drive
   called RPI-RP2. Copy the file onto it. The drive disappears and the green USB light
   comes on: the probe is ready.
3. To load firmware again later, bridge the two BOOT pads (tweezers) while plugging in.
   Do it with no board on the jig: the pads are under it.

## Programming a board

1. Lay the board **face down** on the jig, its three corner holes over the three screws,
   and let it sit on the standoffs. Put a nut on each screw and do them up finger-tight.
2. Slide **TARGET 3V3** to ON. The red light shows the board is powered.
3. **The MCU:** selector to **MCU**, then for example

   ```sh
   firmware/swd.py --probe cmsis-dap info
   firmware/swd.py --probe cmsis-dap flash
   ```

4. **The radio** (after the MCU's firmware runs: the MCU switches the radio's power on):
   selector to **RADIO**, then `firmware/swd.py --probe cmsis-dap radio-info` and
   `radio-flash`.
5. TARGET 3V3 to OFF, nuts off, board out.

Keep TARGET 3V3 **off** whenever the board runs from its own battery pack or its own USB.

## Check once, on the first jig

- **Switch directions.** The labels assume a slide switch connects the side its knob is
  on. With the jig unplugged, check with a meter: selector at MCU, the SWDIO spring pin
  connects to the selector's middle pin; TARGET 3V3 at ON, the 3.3 V spring pin reads 3.3 V
  with USB in.
- **Registration.** Each spring pin's tip should land in the middle of its pad: look at
  the marks they leave on a first board.
- **SWD speed.** `swd.py` defaults to 1 MHz for flying leads; on the jig a faster clock
  (`--khz 4000`) should work, untried.

## Making the board

```sh
KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
$KICAD_PY jig/makeJig.py --check                           # do the device board's pads and holes still match?
$KICAD_PY jig/makeJig.py                                   # placed, planes drawn, not routed
$KICAD_PY ../../tools/humanRouter/run.py jig/jig.kicad_pcb --out /tmp/jigroute     # route it
$KICAD_PY scripts/tidyDefects.py /tmp/jigroute/jig.kicad_pcb jig/jig.kicad_pcb     # stubs out
kicad-cli pcb drc --severity-all jig/jig.kicad_pcb         # as saved, and again with --refill-zones
$KICAD_PY production/boardOrder.py jig/jig.kicad_pcb production/jig-order jig 5
```

`makeJig.py` holds the jig's whole design: where the device board's pads and holes are,
the probe's parts and nets, the heights. It overwrites the board. It first reads the
device board and refuses to build when a programming pad or one of the three holes has
moved, or when two pads sit closer than 2.5 mm (the pins' flanges): change the jig's
numbers to the board's, rebuild, route, and recut its order files.

## Ordering at JLC

- 4 layers, 1.6 mm, any colour; assembly on **one** face: every part is on the bottom
  layer (the parts face).
- Check every part's rotation in JLC's placement preview before paying: the RP2040's pin 1,
  the crystal, the two regulators, the USB-C, the ESD clamp, the two LEDs' polarity and
  both switches.
- The spring pins: JLC had 401 on 2026-11-01; one jig takes seven.
