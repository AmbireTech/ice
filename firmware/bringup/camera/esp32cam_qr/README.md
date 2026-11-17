# ESP32-CAM QR scanner

Scans QR codes with an OV2640 module on an Ai-Thinker ESP32-CAM and prints
what it reads as text on the USB-serial port. It decodes with the signer's
own video QR decoder, so a code that scans here and not on the signer points
at the signer's camera setup, not at the decoder.

## Use

Flash (the ESP32-CAM-MB handles the boot pins; on a bare board hold IO0 to
GND while it resets):

    ~/.platformio/penv/bin/pio run -t upload --upload-port /dev/cu.usbserial-1120

Watch the result:

    ~/.platformio/penv/bin/pio device monitor -d firmware/bringup/camera/esp32cam_qr

Opening the port restarts the board; it scans a few seconds later. Show it
the code: it reads from about 2.5 camera pixels a module, so a version 7 code
(45 modules) need only fill about a third of the view's width.

## Saved frames

Frames that showed a code (its finder patterns at least) but did not decode
(one every 2 s, the last 12) and a few decoded ones are kept in the board's
flash, so they survive the restart that opening the port causes. Pull them, with the
board's verdict and the host decoder's on each:

    ~/.platformio/penv/bin/python firmware/bringup/camera/esp32cam_qr/pull_frames.py [--erase]

They land in `firmware/out/esp32qr/<time>/`.

A serial `T` replays the saved frames through the board's decoder and
times each: once on its own, with the time of every stage, and once more as
the next frame of a video (tracked, no search of the frame).

## What it prints

    QR OK v7 3.9 px/module, tracked (frame 381, decode 34 ms, 120 ms after it came into view)
    UR:ETH-SIGN-REQUEST/...

once per code (again only when the code changes, or after 3 s), and every
4 s one status line:

    scanning [25.0 fps, decode 31 ms]: 212 decoded (18.5 a second, 71 tracked frames), last code: v7, 3.9 px/module, 6 of its alignment patterns, timing 97 % (exposure 31 gain 0, 9 codes read)
    scanning [25.0 fps, decode 22 ms]: 212 decoded (0.0 a second, 0 tracked frames), no code in view (exposure 84 gain 30, 9 codes read)

"found" instead of "tracked" means the frame was searched for the code;
"tracked" that the code was picked up where the previous frame had it.
"N frames summed" means no single frame was readable and the code was read
from several frames of it together.

## Camera modes

Frames are always 400x296; the mode says which of the sensor's pixels make
them. Change it with a serial `R <mode>` (the board restarts the camera and
reports the frame rate it gets):

| Mode | View | Sensor pixels behind a frame pixel |
|---|---|---|
| `cif` (default) | whole | 1 of 16 (the sensor skips the rest): the fastest frames |
| `half` | whole | 4 of 16, averaged by the sensor |
| `full` | whole | all 16, averaged by the sensor: the sensor's slowest frames |
| `zoom` | the centre half each way | 1 of 4, unscaled: twice the pixels a module |

Only `cif` has been measured (25 frames a second); the other three are there
to be compared by hand: the status line shows the frame rate and the decodes
a second of each.

## How it sets the camera up

- The sensor clock at XCLK/2 where frames still arrive whole (checked at
  start; Espressif's driver runs it at XCLK/8 on the plain ESP32).
- Exposure set from the frames, as on the signer: the brightest part of the
  code's area (the view's centre until a code is seen) held below clipping,
  the exposure at most about 10 ms against hand blur, gain making up the rest.
- Frames are decoded where the camera driver put them; the decoder never
  changes a frame and keeps its own state (about 70 kB, printed at start) in internal RAM.
- The decoder runs on the second core, so the first core's idle task (which
  the watchdog watches) always gets to run.
- The sensor is powered down before each start: the board sometimes reads
  no valid chip id after a reset otherwise.
