# Where the cell goes

Proto 1 runs on one AAA cell (why: [power-aaa.md](power-aaa.md)). This page compares
where that cell can sit, and records the choice.

**Decision (2026-10-25): the cell goes on the back, inside the case.** The front becomes
one flat face with no chin, and the cell sits under a hump on the back. The board
carries it since 2026-10-25 (cell and clips on the back, the radio module on the front,
antenna still south), routed.

**Update (2026-10-25, v3's case): open by default, with an optional cover.** A hump
built into the back half would have left that half unprintable without supports. So
the case leaves the cell open: it stands 5.6 mm out of a flat back, 17.1 mm thick
there. A separately printed, rounded cover slides on from the side and makes it
17.7 mm. The cell charges over USB in the device, so it rarely comes out. The options
below are the comparison the decision was made on.

## How the numbers were worked out

All heights are in mm from the board's front face. They come from the enclosure model
(`enclosure/generate.py`, as generated on 2026-10-23) and the parts' drawings.

| | mm |
|---|---|
| Screen face (foam, screen, lip) | +5.33 |
| Board back (1.0 mm board) | −1.0 |
| Normal back face: the USB-C receptacle (3.4) + 0.6 clearance + 1.2 floor | −6.2 |
| **Case body**, wherever the cell is not | **11.5** |
| AAA cell on its clips: 0.25 mm clip base + 10.5 mm cell | 10.75 |
| Over a covered cell: 0.2 mm air + a 0.6 mm skin, the thinnest the model allows for a resin print | 0.8 |

The cell is 44.5 × 10.5 mm, lying across the chin. The camera head always sticks out
0.7 mm through the back, in every option.

"Thickness at the cell" is the device's total there, the thickest point. A hump is how
far that part stands out past the 11.5 mm body: back, enclosed is 11.5 + 6.4 ≈ 17.9 mm,
not 17.9 + 6.4.

## The options

| Option | Thickness at the cell | Front face | Back face | Changing the cell | Board change |
|---|---|---|---|---|---|
| **Proto 0: LiPo pouch** | 18.0 everywhere (52.2 × 89.2) | flat | flat | sealed inside | — (a different board) |
| **Front, open** (proto 1 as built) | 16.95 | cell sticks out 5.4 past the screen face | flat | without tools | none |
| **Front, enclosed** | ~17.7 | a 6.2 hump at the chin | flat | needs a door | none |
| **Back, open** | ~17.1 | **flat** | cell sticks out 5.6 | without tools | chin re-laid |
| **Back, enclosed** (chosen) | ~17.9 | **flat** | a 6.4 hump over the cell | needs a door or an opening back | chin re-laid |
| **Sunken** in a board cutout | ~15.9 | **flat** | a 4.4 hump | needs a door | board and case 8–12 wider |

### Proto 0: LiPo pouch

A 1S pouch 4 mm thick under the screen, with the camera inside the case. The case was
18.0 mm thick all over. The AAA design replaced it: the body is 6.5 mm thinner, and the
cell is any off-the-shelf AAA instead of a pouch that is sealed in.

### Front, open (proto 1 as built)

The cell sits in two machine-placed SMT clips on the front face (MYOUNG MY-AAA-19-J /
-20-J) and stands out of the case through an opening. It is the thinnest of the
on-board options and changes cells without tools. But the chin is a bare cell standing
5.4 mm proud of the screen face.

### Front, enclosed

The same as front, open, with a 0.6 mm skin over the cell: about 0.8 mm more at the
chin. The chin turns into a 6.2 mm hump on the front instead of a bare cell. Changing
the cell needs a door in the front.

### Back, open

The same clips on the back face. The front becomes flat at the screen face, and the
cell stands out of the back. It changes cells without tools, at 17.1 mm. It needs the
same chin re-layout as back, enclosed (below).

### Back, enclosed (chosen)

The same clips on the back, under a 0.6 mm skin: 17.9 mm at the cell, with the rest of
the body still 11.5 mm.

The back of the chin holds 31 parts today, all inside the cell's 47 × 11 mm footprint:
- the BQ25172 charger and the LTC3539 boost with its inductor;
- the USB switch, the USBLC6 ESD clamp and the reverse-cell FET;
- the E73 radio module and their passives.

Two ways to make room:
- **Swap the chin's two faces (planned).** The area under the cell on the front
  becomes free: today it holds only the clips and the joystick ladder. Most of the
  31 parts move to the front, into pockets under the flat bezel. The front has
  5.33 mm of height and these parts are at most about 2 mm tall, so they fit. The cell
  stays on machine-placed clips.
- **Raise the cell over the parts with taller holders.** This is rejected: about
  20 mm thick, with the metal cell about 2 mm over the radio module.

### Sunken in a board cutout

The cell drops into a slot cut through the board, between two end contacts:
- the contacts are MYOUNG MY-AAA-15-J (+) and MY-AAA-16-J (−, with a spring), LCSC
  C51889749 / C51889750;
- each is a steel plate hanging from a 4 mm solder tab, wave or hand soldered, not
  machine placed;
- the plates put the cell's axis about 4.5 mm below the board's front face.

It is the thinnest option, 15.9 mm, with a flat front. But end contacts need the cell's
length plus the spring and the dimple: a cutout about 50.2 mm long, in a board 48 mm
wide. Each tab also needs FR4 beyond the cutout's end:
- with the whole tab on the board, about 4.75 mm per end, which makes the board about
  59.7 mm wide (+11.7 mm);
- with part of each tab over the edge and the case wall behind it, about 2.75 mm per
  end, which makes the board about 55.7 mm wide (+7.7 mm).

The case grows with the board. The plates are held by their solder tabs alone, so the
case needs a wall behind each one. All of that buys 2 mm of thickness.

## Why back, enclosed

- **A flat front with no chin**, which was the goal. Only the back and the sunken
  options give it.
- **The board stays 48 mm wide** and the cell stays on machine-placed SMT clips. The
  sunken cell is 2 mm thinner, but it costs 8–12 mm of width and a hand-soldering step.
- **About 1 mm thicker at the cell** than today's open front (17.9 vs 16.95). The rest
  of the body stays 11.5 mm.
- **The hump protects the camera:** the device rests on the hump, not on the camera
  head, which sticks out 0.7 mm at the back's north end.
- **Enclosed rather than open:** the case covers the cell, so a cell standing out of
  the back can't snag or be knocked. It costs 0.8 mm (17.9 vs 17.1).

## What it costs, and what is open

- **The chin is re-laid on both faces**, and re-routed. The chin's back is the part
  of the board that took the most hand routing.
- **Getting at the cell:** it charges in the device, and the case leaves it open; the
  cover slides off sideways.
- **The cover is compact:** its outside follows its inside at 0.5 mm, plus a short
  skirt over its lips. A wider version with sides ramping off the back at 40° covered
  26 mm of the back and was dropped.
- **The flame print** on the back loses its chin end under the cell. The cell shows
  through a clear cover.
- **The radio's antenna** stays at the south edge, outside the cell's footprint, as
  it is today.
- **The cover's numbers come from the generated case** (2026-10-25), sized from the
  clips' real 3D model: 14.7 mm wide on the back, 6.2 mm proud, 0.1 mm to the cell and
  0.15 mm to the clips inside, a 0.5 mm roof, 17.7 mm thick there. The cell sets that
  height (it sits on the clips' 0.25 mm steel base); thinner clips or a holder would not
  lower it. Nothing is
  test-printed yet: the slide's fit and whether it needs a detent to stay put are
  open.
