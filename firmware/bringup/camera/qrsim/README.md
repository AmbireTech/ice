# QR scan simulation

Tells how well the device's QR decoder reads a dense Ambire request code
under the camera's real conditions, before anything is tried on a device: how
many camera pixels each code module needs, how large the camera window should
be, and which way of making a wide view keeps codes readable.

It draws a request code on a simulated phone screen, photographs it with a
simulated camera (blur, tilt, noise, exposure, screen pixel grid), and decodes
each picture with the same decoder the device runs. The decoder is built from
the firmware's own copy, so a result here is the decoder's result, not a
stand-in's. The scene model and its assumptions are listed at the top of
`scene.py`.

## Run

Needs a C compiler and Python 3 with `numpy`, `opencv-python-headless` and
`segno`.

```sh
./build.sh                                  # the decoder, as the device has it, and the fixed variants
python3 sweep.py ppm out/ppm.csv            # decode rate against pixels per module
python3 sweep.py window out/window.csv      # window size, the code filling 60-85 % of it
python3 sweep.py wide out/wide.csv          # wide views: binning, skipping, scaling
python3 sweep.py version out/version.csv    # longer codes than the app's
python3 summarize.py out/ppm.csv            # the table (--by n,frac / method,ppm / version,ppm)
```

A sweep saves each batch as it finishes; run it again to continue where it
stopped. `--trials` sets the scenes per point (default 40).

## Decoder variants

- `stock`: as the device decodes today.
- `aff`: the finder patterns grouped with a sturdier test (turned codes were
  missed).
- `regrid`: a code that fails is retried as one version larger and smaller
  (small codes were often read as the wrong version).
- `ext`: both.

Threshold settings (columns): `otsu` (the decoder's own: one level for the
whole picture), `fixed` (the previous picture's level), `rowavg` (a running
average along each row, which can be worked out row by row as the picture
arrives), `boxN` (the average of the surrounding area), `otsu90`/`otsu110`
(the level 10 % lower or higher).

`results/` holds the summarised tables.
