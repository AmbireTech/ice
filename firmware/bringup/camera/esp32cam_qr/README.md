# ESP32-CAM QR scanner

Scans QR codes with an OV2640 module on an Ai-Thinker ESP32-CAM and prints
what it reads as text on the USB-serial port. It decodes with the signer's
own QR decoder, so a code that scans here and not on the signer points at
the signer's camera setup, not at the decoder.

## Use

Flash (the ESP32-CAM-MB handles the boot pins; on a bare board hold IO0 to
GND while it resets):

    ~/.platformio/penv/bin/pio run -t upload --upload-port /dev/cu.usbserial-1120

Watch the result:

    ~/.platformio/penv/bin/pio device monitor -d firmware/bringup/camera/esp32cam_qr

Opening the port restarts the board; it scans a few seconds later. Hold the
code so it fills most of the view (at least half its width), steady.

## Saved frames

Frames that showed a code but did not decode (at least 2.5 px a module, one
every 2 s) and a few decoded ones are kept in the board's flash, so they
survive the restart that opening the port causes. Pull them, with the
board's verdict and the host decoder's on each:

    ~/.platformio/penv/bin/python firmware/bringup/camera/esp32cam_qr/pull_frames.py [--erase]

They land in `firmware/out/esp32qr/<time>/`.

A serial `T` replays the saved frames through the board's decoder and
times each look.

## What it prints

    QR OK v7 3.9 px/module, read at 1.5x (frame 381, decode 1150 ms)
    UR:ETH-SIGN-REQUEST/...

once per code (again only when the code changes, or after 3 s), and every
4 s one status line:

    scanning [6.9 fps, decode 142 ms]: 0 decoded; no code in view (exposure 84 gain 30)
    scanning [6.8 fps, decode 150 ms]: 0 decoded; last code seen not decoded (data ECC, v11, 2.1 px/module, ...)

"px/module" below about 3 means the code is too small in the view: hold it
closer.

## How it sets the camera up

- Grayscale at 400x296, the whole field of view: the sensor's fast mode,
  about 25 frames a second; the decoder keeps up with about 10 a second. 800x600
  was measured too: 2.2 frames a second at best and 600 ms a decode, too slow
  to scan by hand.
- The sensor clock at XCLK/2 where frames still arrive whole (checked at
  start; Espressif's driver runs it at XCLK/8 on the plain ESP32).
- Exposure set from the frames, as on the signer: the brightest part of the
  view's centre held below clipping, the exposure at most about 10 ms against
  hand blur, gain making up the rest.
- A frame that shows a code but does not decode is tried with the decoder's
  other thresholds (up to 0.3 s), then, if finder patterns are in view, read
  once more upscaled (1.5x, 2x, 1.5x in turn, the local-mean threshold): the
  decoder misses most codes of 3-4 px a module at 1x. On the first 12 near
  misses saved, 0 decoded at 1x, 8 with these looks (1.5x: ~0.9 s on the
  board). The decoder stops a frame's grid retries after 300 ms.
- Built at -O2 with the PSRAM at 80 MHz: the upscaled look runs from PSRAM.
- The decoder runs on the second core, so the first core's idle task (which
  the watchdog watches) always gets to run.
- The sensor is powered down before each start: the board sometimes reads
  no valid chip id after a reset otherwise.
