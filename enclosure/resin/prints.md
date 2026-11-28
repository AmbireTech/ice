# Case prints

Anycubic Photon Mono M7 Pro, Anycubic ABS-like translucent resin, on an nFEP (the ACF it
replaced had a hole: prints before 2026-11-25 are not comparable). Only prints the owner
reported go here; not every sliced file was printed.

| # | Date | Model | Settings (as reported) | Result |
|---|---|---|---|---|
| 1 | 2026-11-25 | ? | post-cure 4 min per side | Fragile, a tiny bit tacky. The back split round the magnetic connector while the supports came off. Screw boss too shallow for the M2. Airgap switch hard to reach, the joystick's hole a bit tight. Display hard to fit. 92.0 (maybe 91.9) x 52.7 mm |
| 2 | 2026-11-25 | as #1 | post-cure 5 + 7 min, before the supports came off | Still brittle and tacky |
| 3 | 2026-11-25 | `93c3c80` | shrinkage compensation X 100.5 %, Y 100.5 %, Z 100.3 %; post-cure 2 + 3 min | Settings perfect: not tacky, the display goes in easily and sits snug. 92.3 x 53.0 mm, the back a tad larger than the bezel. Screw boss solid, half-cured resin inside. The USB-C sliver broke off with the supports. Top snap hard: the centre rail shaves plastic |
| 4 | 2026-11-26 | `0accffc` | from its file (`vent hole case…2026_11_26_12_23_00.pwsz`): layer 0.1 mm, exposure 2.1 s, bottom 25 s x 3, 15 transition layers, lift 4 mm at 2.67 mm/s (shrinkage compensation is not stored in the file) | Snap fit amazing. The back too thin at the pack's recess, supports in it |
| 5 | 2026-11-26 | `b37cc6f` | light supports (CHITUBOX) | Complete fail: one half came off the build plate entirely, the other held partly |
| 6 | 2026-11-27 | `6de6511` | yellow; `enclosure/resin_plate.py`'s file (2 mm trunks, 583 contacts, first layer 1561 / 1630 mm2), settings as #4 | The first print with the top screw bosses and the new size. Rejected by the owner: visible layer lines, warping, the supports "a nightmare to take off" |
| 7 | 2026-11-27 (evening) | `6de6511` | yellow; sliced by the owner in CHITUBOX (most likely `case.3mf…2026_11_27_20_24_00.pwsz`: layer 0.1 mm, exposure 2.1 s, bottom 25 s x 3, 15 transition layers, lift 4 mm at 2.67 mm/s) | Printed whole; no feedback on it yet |
| 8 | 2026-11-27/28 | `6de6511` | blue; Elegoo printer, from the STL, sliced with Elegoo's slicer; settings not reported | Printed whole; no feedback on it yet |

The owner's photo of 2026-11-28 shows four prints, left to right: an earlier blue print that is
already in this table with its feedback (which row was not said), then #6, #7 and #8.
