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

## What it prints

    QR OK v11 4.2 px/module (frame 381, decode 120 ms)
    UR:ETH-SIGN-REQUEST/...

once per code (again only when the code changes, or after 3 s), and every
4 s one status line:

    scanning [6.9 fps, decode 142 ms]: 0 decoded; no code in view (exposure 84 gain 30)
    scanning [6.8 fps, decode 150 ms]: 0 decoded; last code seen not decoded (data ECC, v11, 2.1 px/module, ...)

"px/module" below about 3 means the code is too small in the view: hold it
closer.

## How it sets the camera up

- Grayscale at 400x296, the whole field of view: the sensor's fast mode,
  about 25 frames a second; the decoder keeps up with 6-7 a second. 800x600
  was measured too: 2.2 frames a second at best and 600 ms a decode, too slow
  to scan by hand.
- The sensor clock at XCLK/2 where frames still arrive whole (checked at
  start; Espressif's driver runs it at XCLK/8 on the plain ESP32).
- Exposure set from the frames, as on the signer: the brightest part of the
  view's centre held below clipping, the exposure at most about 10 ms against
  hand blur, gain making up the rest.
- A frame that shows a code but does not decode is tried with each of the
  decoder's thresholds.
- The sensor is powered down before each start: the board sometimes reads
  no valid chip id after a reset otherwise.
