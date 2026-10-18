# Back print: Ambire signer

The back face is the one the case shows, so it carries the brand: the Ambire
flame alone, with no wordmark. It comes in two forms, drawn from the same geometry
(`flame.py`):

| | What it is | Made by | Used by |
|---|---|---|---|
| **Flame silkscreen** | The flame in one ink (white), across the whole back | `../scripts/silkFlame.py` (into the board) | The board file, the JLC order set; the next run |
| **Colour print** | The flame in Purple on a Volt, black or white base, with the labels | `build_uv.py` | The PCBWay order set |

The first three boards were ordered before the flame, with the old wordmark in
plain silkscreen. The schemes the configurator offers, and which of them need
the colour print, are in [../docs/direction.md](../docs/direction.md#board-print).

## The flame (silkscreen)

The Purple and Violet facets of the official logo (`brand/Ambire-ver-on-dark.svg`),
with no wordmark: the word "Ambire" is nowhere on the board. It spans the back,
1.2 mm from each edge, with 0.35 mm of bare board between facets so one ink still
shows the faceting. `scripts/silkFlame.py` cuts it back from pads, holes, the edge
and slots, footprint silkscreen and the labels (QR CAMERA, SECURE ELEMENT,
AIRGAP, RADIO, ANT), which stay as clean islands. It puts the flame in the board's
group FLAME, which a rerun replaces, and installs only if DRC does not get worse.

```
$KICAD_PY ../scripts/silkFlame.py          # dry run on a copy, with DRC
$KICAD_PY ../scripts/silkFlame.py --apply  # install (close KiCad first)
```

Rerun it after moving parts, pads or labels on the back. `flame.py` is also what
the concept page's 3D preview draws.

## The colour print

![assembled preview](preview_back_assembled.png)

The same flame as the silkscreen, in Ambire Purple, over the base of one of the
purple schemes in [../docs/direction.md](../docs/direction.md#board-print):

| `--scheme` | Base | Labels |
|---|---|---|
| `neon` (default) | Volt #D7FF00 | Black |
| `black` | Black #181A1F | White |
| `white` | none: the white mask shows | Black |

The facets keep the same 0.35 mm gap of base between them as the silkscreen. The
labels (QR CAMERA, SECURE ELEMENT, AIRGAP, RADIO, ANT) are set in Poppins SemiBold
where they are in the silkscreen, at the same size and angle, each on a clean
island of base 0.5 mm wide. `schemes.png` shows all three on the board.

In the PCBWay order set, the back silkscreen Gerber leaves out the labels and the
white flame, so neither prints twice.

### Files

- `out/uv_back_seen_from_behind.png`: **the print file**. RGBA, 1200 DPI, 2268×4016 px = 48.00×85.00 mm. It shows the back as a person sees it. Transparent pixels mean no ink. The PCBWay order set ships it as `wallet_bottom_UV_print_1200dpi.png`.
- `out/uv_back_kicad_front_view.png`: the same image mirrored, in KiCad's front-looking coordinates (the frame bottom-layer Gerbers use).
- `out/check_no_ink.png`: the print over white mask, with every pad, opening, drill and the camera window in red. Red must never touch colour; the build also checks this itself.
- `preview_back.png`: the print on a fresh 3D render of the back.
- `preview_back_assembled.png`: the same with the two display flex tails and the camera module drawn on.
- `schemes.png`: the three schemes side by side.

The PNGs in `out/` stay out of git; `production/make_order.sh` rebuilds them.

### Rebuild after placement changes

```
python3 -m venv .venv && .venv/bin/pip install pillow numpy
.venv/bin/python build_uv.py                  # print files + previews, purple on neon (~30 s)
.venv/bin/python build_uv.py --scheme black   # another scheme
.venv/bin/python build_uv.py --schemes        # also schemes.png
```

The script needs `kicad-cli` (KiCad 10) and `rsvg-convert` on the PATH. It only reads `../board/wallet.kicad_pcb`. Each run re-derives the following from the board:

- **Outline and slots:** from Edge.Cuts.
- **No-ink areas:** the B.Mask openings and every PTH/NPTH drill and slot (via kicad-cli), plus the CAMERA window.
- **Camera and flex-tail zones (previews):** from the labelled Dwgs.User rectangles `CAMERA`, `TFT TAIL` and `TOUCH TAIL`.
- **Labels:** from the board-level B.SilkS texts: position, size and angle.

Options: `--scheme`, `--dpi`, `--pad-margin`, `--edge-margin`.

## Who prints colour

**PCBWay.** Its full-colour UV print takes our image as it is: the order set in
`production/order/pcbway/` has the fab zip with the print and a positioning
image, and `ORDER-NOTES.txt` with the remark to paste into the order (white mask,
ENIG, UV print on the bottom only).

**Not JLCPCB.** JLC calls its service "EasyEDA multi-color silkscreen" and only
offers it for files EasyEDA wrote: it has blocked colour files from other tools
since January 2026, and still did when we tried on 2026-10-17. Our JLC colour zip
(`COLOUR=1 production/make_order.sh`, `production/colour_silk.py`) is kept for
reference only.

What we found about JLC's process when we researched it (2026-10-14):

- [H] https://jlcpcb.com/help/article/how-to-design-multi-color-silkscreen-using-easyeda
- [E] https://prodocs.easyeda.com/en/pcb/other-draw-colorful-silkscreen/
- [G] https://prodocs.easyeda.com/en/pcb/export-pcb-fabrication-file-gerber/
- [N] https://jlcpcb.com/news/multi-color-silkscreen-pcb
- [R] https://jlcpcb.com/resources/multicolor-silkscreen-pcb
- [C] https://jlcpcb.com/capabilities/pcb-capabilities

| Item | What we found |
|---|---|
| Toolchain | **EasyEDA Pro only** [H]. It exports extra files (`.FCTS` / `.FCBS` / `.FCBO`) alongside the Gerbers [G]. There is no official KiCad route. |
| Image input | PNG or SVG, placed on the board with "place original image" [H, E]. Older JLC pages say JPG/PNG. |
| Resolution | 6-colour UV printer, up to **1200 DPI** [E, N]. That is why we deliver 1200 DPI. JLC does not state a required input DPI. |
| Colour profile | Not stated. We deliver sRGB. |
| Solder mask | **White only** [H, E]. A "board colour" base, white by default and set per side, is printed first, then the image [E]. |
| Sides / stackup | Top and/or bottom; 2 or 4 layers; 0.4–2.0 mm thick; FR4; **ENIG only** [H, E]. |
| Min. features | Colour text 0.8 mm [R]. The general legend rules are 0.15 mm lines, 1.0 mm text, 0.15 mm from pads [C]. Our labels use 0.85 mm caps. |
| Normal silkscreen | Printed by the same colour process, in a per-part or default colour [E]. A separate white pass is not mentioned. |
| Clearances | No colour-specific rule was published. We keep 0.10 mm of bare mask around every opening and hole, and 0.30 mm from the outline and slots. |
| Alignment | The image is placed on the board outline in EasyEDA, and JLC adds an outline-mark file [G]. If JLC's own Gerber edits move things by more than 2 mm, the colour print is dropped [H]. |
| Assembly | The panel needs edge rails or the colour print is skipped. Boards under 70×70 mm get rails automatically [H]. |
| Cost / time | From $6.71, but [N] and [R] do not say whether that is per order or per board. Lead time is +1 day. ENIG is mandatory. |
| Bottom mirroring | **Not documented.** |

## Brand sources

Everything comes from github.com/AmbireTech/ambire-brand, commit `5939a5bf` (2026-06-01).

- **Logo:** `Logos/Ambire/SVG/Ambire-ver-on-dark.svg`, copied unmodified into `brand/`. The flame is its Purple and Violet facets.
- **Colours used:** *Ambire Brand Basics.pdf* v2.0, pages 49 and 51:
  - Purple `#6000FF`
  - Black `#181A1F`
  - White `#DCE2F2`
  - Volt `#D7FF00`
- **Font:** Poppins, the brand's main typeface (page 43). The file `brand/Poppins-SemiBold.ttf` is from Google Fonts and licensed under the OFL (`brand/Poppins-OFL.txt`).
- **Rules followed** (pages 37–41): the flame is not stretched or given effects and is not repeated as a pattern. Printing it alone, in one colour, is the board-print decision in [../docs/direction.md](../docs/direction.md#board-print).

## Open questions

1. **The white mask shows through on purpose** as a 0.10 mm ring around each pad and a 0.30 mm rim at the edge. Ask the maker what clearance they apply themselves; if they pull the ink back too, `--pad-margin 0` may be correct.
2. **PCBWay's colour price and how it orients a bottom-side image** are not recorded here; `wallet_UV_positioning.png` is in the zip so they can check the image against the Gerbers.
