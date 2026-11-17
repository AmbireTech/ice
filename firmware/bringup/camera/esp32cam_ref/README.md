# OV2640 reference on an ESP32-CAM

Firmware for an Ai-Thinker ESP32-CAM that shows what an OV2640 module really
delivers when Espressif's own driver runs it. Move the module from our device
onto the ESP32-CAM, join its Wi-Fi, and look at dense QR codes on a phone or
laptop screen: live preview, the driver's controls, and raw frames you can
download for decoding on the Mac.

Nothing here has run on hardware yet: it builds, that is all.

## Versions

- **esp32-camera at b3f7fe8** (tag v2.1.9), pinned in `src/idf_component.yml`.
  Our device's register tables were generated from that commit, so this is
  the same driver code. Its `esp_jpeg` dependency comes from Espressif's
  registry (1.3.2). `dependencies.lock` records both.
- **ESP-IDF 5.5.5**, through PlatformIO with pioarduino's platform
  `platform-espressif32` 55.03.312 (the commit needs IDF 5.1 or newer). Not
  the Arduino framework: arduino-esp32 ships its own prebuilt copy of
  esp32-camera, which can't be pinned to a commit.
- Built with PlatformIO Core 6.2.0 from `~/.platformio/penv/bin/pio`. The
  `pio` in `~/Library/Python/3.9/bin` refuses this platform (it needs Python
  3.10 or newer).

Size: app 994 KB of a 1.5 MB partition, 48 KB static RAM. Frame buffers sit
in the board's 4 MB PSRAM.

## Fitting the module

Power off. Lift the ESP32-CAM connector's latch, take the stock camera out,
insert our module the same way round (gold contacts toward the board), and
close the latch.

## Building and flashing

```sh
cd firmware/bringup/camera/esp32cam_ref
~/.platformio/penv/bin/pio run                        # build only
~/.platformio/penv/bin/pio run -t upload --upload-port /dev/cu.usbserial-XXXX
~/.platformio/penv/bin/pio device monitor             # 115200 baud boot log
```

The first build downloads ESP-IDF and the driver (a few hundred MB, once).

**ESP32-CAM-MB (the USB base board):** plug the ESP32-CAM onto it and connect
USB. If the upload doesn't start on its own, hold the IO0 button, tap RST,
release IO0, then upload. Tap RST afterwards to run.

**USB-serial adapter (3.3 V logic):**

| Adapter | ESP32-CAM |
| --- | --- |
| 5V | 5V |
| GND | GND |
| TX | U0R (GPIO3) |
| RX | U0T (GPIO1) |

Use 5 V for power: on 3.3 V the board tends to reset when Wi-Fi starts. To
flash, wire IO0 to GND, press RST, and upload. Afterwards remove the IO0
wire and press RST to run.

The boot log shows whether the camera started, for example
`camera start: 320x320 gray field wide`, or the driver's error.

## Using it

1. Join the Wi-Fi network **`ov2640-ref`**, password **`ov2640ref`**. The Mac
   has no internet while it's on this network.
2. Open **http://192.168.4.1/**.

The page shows the preview on the left and the controls on the right.
Grayscale frames are drawn pixel for pixel: what you see is the Y the sensor
sent, with no JPEG in between. "Zoom" enlarges the frame without smoothing.
The line under the preview shows the frame size, the sensor window, the live
exposure (rows) and gain read from the sensor, and which settings you have
changed from the driver's defaults.

It starts the way our device frames the code: 320x320 grayscale, the wide
field.

### Mode (each change restarts the camera)

- **Frame size**: 240x240, QVGA, 320x320, CIF, HVGA, VGA, SVGA, XGA, HD, SXGA,
  UXGA. Grayscale can only use these sizes, because the driver allocates its
  buffers from this list. 384x384, our device's size, isn't in it, so
  320x320 is the closest square.
- **Field**: which part of the sensor the frame covers.

  | Field | What it is | Our device |
  | --- | --- | --- |
  | full | the driver's own framing for that frame size | – |
  | wide | the centre square of the sensor's full height, scaled down to the frame | zoom 0 |
  | half | SVGA mode's own pixels (half resolution), centre window | zoom 1 |
  | native | the sensor's native pixels, centre window | zoom 2 |

  "half" and "native" give the same pixel scale as our device, so sharpness
  compares directly; the window is just smaller at 320 than at 384. At 320,
  "wide" is slightly coarser than our device at 384 (1.875 SVGA pixels per
  frame pixel instead of 1.56).
- **Format**: grayscale (Y only, what our decoder reads) or JPEG.
- **XCLK**: 20 or 10 MHz.

### Sensor controls (applied at once)

Auto exposure and the manual exposure value (0–1200), AE level, AEC DSP,
auto gain and the manual gain (0–30), gain ceiling (2x–128x), brightness,
contrast, saturation, special effect (leave it off), white balance, lens
correction, black/white pixel correction, raw gamma, downsize (DCW),
mirror/flip, colour bar, and JPEG quality. Turn auto exposure off to use the
manual exposure value; turn auto gain off to use the manual gain.

The driver at this commit has no sharpness or denoise control for the OV2640
(its setters return an error), so the page doesn't show them. Any register
can still be set by hand, see below.

**Driver defaults** forgets everything set on the page and restarts the camera
with the driver's own settings.

### Differences from our device to keep in mind

On the ESP32 the driver runs the grayscale modes at a slower sensor clock than
our device does (no clock doubler, divider 8), so frames come slower and the
longest exposure is longer. When comparing sharpness against our device,
compare at similar exposure times.

## Pulling raw frames

**Download frame** on the page saves the current frame as a PGM (grayscale)
or JPEG. The PGM holds the frame's exact Y bytes, ready for quirc.

`pull.py` (plain Python 3, nothing to install) saves several frames, with an
optional setting change first:

```sh
python3 pull.py                                     # 5 frames as the page is set
python3 pull.py -n 10 -o frames/native320 --set framesize=7 --set field=3
python3 pull.py -o frames/wide-ae --set aec=0 --set aec_value=200
python3 pull.py --set format=0 --fmt jpeg           # JPEG frames
```

Each frame is saved as `.pgm` and `.png`, with `status.json` (mode, window,
every setting, live exposure and gain) beside them. For each frame it prints
the brightness summary `camdbg.py` prints for our device's frames. `--skip`
(default 3) drops frames after a change so exposure can settle. The
frame-size numbers are listed under `framesizes` in `/status`:
240x240 = 5, QVGA = 6, 320x320 = 7, VGA = 10, SVGA = 11, UXGA = 15.

A UXGA grayscale frame is about 2 MB and takes a few seconds over the Wi-Fi.

## Registers

- **http://192.168.4.1/regs** dumps both OV2640 register banks, in the same
  layout as `camdbg.py dump 0` / `dump 1` on our device, so the two can be
  diffed line by line.
- **http://192.168.4.1/reg?bank=1&addr=0x10** reads one register;
  `&val=0x40` writes it first. Bank 0 is the DSP, bank 1 the sensor. The page
  doesn't know about writes made this way, and a mode change or Driver
  defaults overwrites them.

## Not included

There's no QR decoding on the ESP32: decode the pulled PGMs with quirc on the
Mac.
