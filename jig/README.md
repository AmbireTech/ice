# Programming jig

A fixture that programs a bare, assembled device board over SWD, before the screen and
the case go on. One USB-C cable to a computer; no separate debug probe.

- **The probe board** sits over the board's front. It is a USB debug probe (an RP2040
  running Raspberry Pi's open debugprobe firmware, which speaks CMSIS-DAP) with five spring
  pins underneath that land on the board's programming pads: 3.3 V, ground, SWDIO, SWCLK
  and reset. It also powers the board while it is programmed.
- **The base** holds the board front up, on three standoffs through its corner holes. Two
  spring pins rise from it into the radio's programming pads on the board's back; a short
  cable takes them to the probe board.

The repo's SWD tool works with it as it is: `firmware/swd.py --probe cmsis-dap …`.

## What it is made of

| Part | How many | Where from |
|---|---|---|
| Probe board, 66 × 28 mm, 4 layers, 1.6 mm, assembled on both faces | 1 | JLC: `production/jig-order/probe/` |
| Base board, 66 × 95 mm, 2 layers, 1.6 mm, assembled on the top face | 1 | JLC: `production/jig-order/base/` |
| M2 hex standoff, male–female, 5 mm body, 3 mm thread | 5 | any hardware shop |
| M2 hex standoff, male–female, 6 mm body, 3 mm thread | 2 | " |
| M2 × 4 mm screw | 5 | " |
| M2 nut (a knurled thumb nut is nicer) | 2 | " |
| JST SH 4-pin cable, 50–100 mm, the same pin at both ends (sold as Qwiic or STEMMA QT) | 1 | " |
| M3 feet (rubber or standoffs) under the base, optional | 4 | " |

Every electronic part on both boards is JLC stock. The spring pins on both boards are the
same part (Xinyangze YZP0063-20068-01), working at 5.0 mm, 25 g each.

## Putting it together

1. **The board's standoffs:** three 5 mm standoffs on the base's holes marked by the
   device outline (its top corners and its bottom-right hole), thread **up**, each held by
   an M2 × 4 screw from under the base. Their threads are the board's locating pins.
2. **The posts:** on the two holes outside the outline, a 5 mm standoff (thread up,
   screwed from below) with a 6 mm standoff screwed on top of it, thread up. The probe
   board rests on them at 11 mm.
3. **The cable:** base socket (TO PROBE) to probe socket (TO BASE).
4. **Feet** under the base if you like: the screw heads stand below it.

## First use: load the probe's firmware

1. Download `debugprobe_on_pico.uf2` from Raspberry Pi's debugprobe releases
   (github.com/raspberrypi/debugprobe). The jig is wired exactly as that build expects.
2. Plug the probe board into a computer. A new probe has an empty flash and shows up as a
   USB drive called RPI-RP2. Copy the file onto it. The drive disappears and the green USB
   light comes on: the probe is ready.
3. To load firmware again later, bridge the two BOOT pads (tweezers) while plugging in.

## Programming a board

1. Put the board on the base, **front up**, its three corner holes over the standoffs.
2. Lower the probe board onto the posts and do up the two nuts finger-tight.
3. Slide **TARGET 3V3** to ON. The red light shows the board is powered.
4. **The MCU:** selector to **MCU**, then for example

   ```sh
   firmware/swd.py --probe cmsis-dap info
   firmware/swd.py --probe cmsis-dap flash
   ```

5. **The radio** (after the MCU's firmware runs: the MCU switches the radio's power on):
   selector to **RADIO**, then `firmware/swd.py --probe cmsis-dap radio-info` and
   `radio-flash`.
6. TARGET 3V3 to OFF before taking the board out.

Keep TARGET 3V3 **off** whenever the board runs from its own battery pack or its own USB.

## Check once, on the first jig

- **Switch directions.** The labels assume a slide switch connects the side its knob is
  on. With the probe unplugged, check with a meter: selector at MCU, the SWDIO spring pin
  connects to the selector's middle pin; TARGET 3V3 at ON, the 3.3 V spring pin reads 3.3 V
  with USB in.
- **Registration.** Each spring pin's tip should land in the middle of its pad; look from
  the side before the first flash.
- **SWD speed.** `swd.py` defaults to 1 MHz for flying leads; on the jig a faster clock
  (`--khz 4000`) should work, untried.

## Making the boards

```sh
KICAD_PY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
$KICAD_PY jig/makeJig.py base                         # placed and routed by script
$KICAD_PY jig/makeJig.py probe                        # placed, planes drawn, not routed
$KICAD_PY ../../tools/humanRouter/run.py jig/probe.kicad_pcb --out /tmp/jigroute   # route it
$KICAD_PY scripts/tidyDefects.py /tmp/jigroute/probe.kicad_pcb jig/probe.kicad_pcb  # stubs out
kicad-cli pcb drc --severity-all jig/probe.kicad_pcb  # as saved, and again with --refill-zones
$KICAD_PY production/boardOrder.py jig/probe.kicad_pcb production/jig-order/probe jig-probe 5
$KICAD_PY production/boardOrder.py jig/base.kicad_pcb production/jig-order/base jig-base 5
```

`makeJig.py` holds the jig's whole design: where the board's pads and holes are, the
probe's parts and nets, the heights. It overwrites the boards.

## Ordering at JLC

- **Probe:** 4 layers, 1.6 mm, any colour; assembly on **both** faces (the five spring
  pins are on the bottom).
- **Base:** 2 layers, 1.6 mm; assembly on the top face.
- Check every part's rotation in JLC's placement preview before paying: the RP2040's pin 1,
  the crystal, the two regulators, the USB-C, the ESD clamp, the two LEDs' polarity, both
  switches and both JST sockets.
- The spring pins: JLC had 401 on 2026-11-01; one jig takes seven.
