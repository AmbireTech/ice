#!/usr/bin/env python3
"""The v4 battery pack's board: placed and routed by script, from the numbers the case
generator fixes (enclosure/generate.py, docs/v4/enclosure/enclosure.json -> "pack").

    KICAD_PY pack/makePack.py        (from hardware/ambire-hw; writes pack/pack.kicad_pcb)

It writes the board from scratch: after the first hand edit, edit the board, not this.

Coordinates are the DEVICE board's (x east, y south, mm): seen from its outer face (F,
toward the device), the pack lines up with the device seen from the front.

  F (outer face, against the device's back): four Xinyangze single SMD spring pins in a
    row east-west, the two magnets' seats beside them (glued, in the printed frame, whose
    pegs go into two holes here), a BAT- pour; nothing else on it. They land on the device's
    back: the pins on four plain ENIG pads, the magnets on two steel spacers, which hold
    the pack. Each pin's peg hole is plated: it carries the pin to the inner face.
  B (inner face): two AAA cells east-west in series, in MYOUNG clips copied from the
    device board (the south cell as proto 1's cell sat, + east; the north cell turned
    end for end, + west; the west clips are the mid-point); the fuse, the thermistor and
    the mid-point's series resistor beside the pins.

pcbnew: one LoadBoard per process. The board is started as a minimal file beside
pack.kicad_pro (so it gets the pack's rules), loaded once, built, zones filled, saved.
"""
import json
import os
import re
import sys
import uuid

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "pack.kicad_pcb")
PRO = os.path.join(HERE, "pack.kicad_pro")
DEVICE = os.path.join(ROOT, "enclosure/pack_clips.kicad_pcb")   # proto 1's clips, as that board placed them
ENCLOSURE = os.path.join(ROOT, "docs/v4/enclosure/enclosure.json")
KICAD_FP = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"

# ── geometry: the outline and the cells from the case generator ─────────────
_PACK = json.load(open(ENCLOSURE))["pack"]
OUTLINE = tuple(_PACK["board"])           # x0, y0, x1, y1
CELL_YS = tuple(_PACK["cells"]["y"])      # north, south
CORNER_R = 1.0
THICKNESS = 0.8
CLIP_X = (5.5, 42.5)                      # the clips' base centres, west and east (proto 1's)
# The clips' steel lying on this board (each foot: a body along the clip, a short tab across
# it that the arms rise from), as the case reads it from their models: x0, y0, x1, y1.
CLIP_STEEL = [tuple(r) for r in _PACK["clipSteel"]]
# The contacts, where the case puts them: over the device's discs and steel, in a row
# east-west along the south cell, under the cell's south flank, clear of the clips (they
# hold the cells' ends). The pins' plated peg holes and the frame's peg holes must clear
# the clips' steel on the inner face (check_pins_clear_clips).
_FRAME = _PACK["frame"]
_FO = _FRAME["outline"]
CONN_X, CONN_Y = round((_FO[0] + _FO[2]) / 2, 3), round((_FO[1] + _FO[3]) / 2, 3)
CONN_PAD = 1.5                            # a pin's plated peg hole's ring on the inner face (see the footprint)
JOINT_CLR = 0.3                           # a hole's ring to a clip's steel, at least
PITCH = 2.5
PIN_NETS = ["BAT-", "NTC", "MID_SENSE", "BAT+"]   # west to east: the one place the pin order lives
PIN_XS = [round(CONN_X + (i - 1.5) * PITCH, 3) for i in range(4)]
PIN = dict(zip(PIN_NETS, PIN_XS))
if sorted(round(x, 3) for x, _ in _PACK["pins"]["xy"]) != PIN_XS or any(abs(y - CONN_Y) > 0.01 for _, y in _PACK["pins"]["xy"]):
    sys.exit(f"the case's pins {_PACK['pins']['xy']} are not at {PIN_XS}, {CONN_Y}")
MAGNETS = [tuple(m) for m in _PACK["magnets"]["xy"]]
PEGS = [tuple(p_) for p_ in _FRAME["pegs"]]

# ── parts ───────────────────────────────────────────────────────────────────
# (ref, library, footprint, value, LCSC, mpn, at, rotation, (west/north pad's net, east/south pad's net))
# In the strip south of the south cell's can, where nothing stands over them (the case
# checks it, PACK_SMALL), each straddling its pin's line: the thermistor between BAT- and
# NTC, the resistor at MID, the fuse east of BAT+.
_SY = round(CONN_Y + 2.5, 3)              # the strip's centre line
SMALL = [
    ("F_BAT", f"{KICAD_FP}/Fuse.pretty", "Fuse_1206_3216Metric", "0.75A hold", "C976305", "BSMD1206-075-30V",
     (round(CONN_X + 6.5, 3), _SY), 0, ("BAT+", "CELL+")),
    ("R_MID", f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric", "1k", "C11702", "0402WGF1001TCE",
     (CONN_X, _SY), 0, ("MID", "MID_SENSE")),
    ("RT_PACK", f"{KICAD_FP}/Resistor_SMD.pretty", "R_0603_1608Metric", "10k NTC B3380", "C13564", "NCP18XH103F03RB",
     (round(CONN_X - 2.5, 3), _SY), 0, ("BAT-", "NTC")),
]
PINS = ("J_PACK", os.path.join(HERE, "pack.pretty"), "Xinyangze_YZP0118-20063-02", "YZP0118-20063-02",
        "C5157302", "YZP0118-20063-02")
FRAME_FP = "Pack_Frame"                   # written by frame_footprint() from the case's numbers
CLIPS = {"BT_N": ("C6937104", "MY-AAA-19-J"), "BT_P": ("C6937107", "MY-AAA-20-J")}

W_PWR, W_SIG = 0.8, 0.25
W_FAN = 0.4                # a power pad's fanout: thick, capped at the pad's narrow side
VIA = (0.6, 0.3)

# ── copper, by hand: (net, layer, width, points) and (net, x, y) vias ───────
# From each small part up to its pin; CELL+ from the fuse's east end to the south-east
# clip; the mid-point from the south-west clip on the outer face, under the frame, to the
# resistor; BAT- reaches the outer face's pour (the BAT- pin's pads are in it). Each run
# ends at its pin's plated peg hole on the inner face.
_cn, _cs = CELL_YS
_fx, _rx, _tx = (p[6][0] for p in SMALL)          # the fuse's, the resistor's, the thermistor's x
_up = lambda x0, x1: [(x0, _SY), (x1, _SY - abs(x1 - x0)), (x1, CONN_Y)]   # 45 deg off the pad, then up to the pin
_mv = round(CONN_Y + 1.67, 3)                        # MID's crossing on the outer face, between the joints and the strip
TRACKS = [
    ("BAT+", "B", W_PWR, _up(_fx - 1.4, PIN["BAT+"])),
    ("MID_SENSE", "B", W_SIG, _up(_rx + 0.51, PIN["MID_SENSE"])),
    ("NTC", "B", W_SIG, _up(_tx + 0.825, PIN["NTC"])),
    ("BAT-", "B", W_FAN, _up(_tx - 0.825, PIN["BAT-"])),
    ("CELL+", "B", W_PWR, [(_fx + 1.4, _SY), (34.5, _SY), (38.6, _SY - 4.1)]),   # into the south-east clip's pad
    ("BAT-", "B", W_PWR, [(40.0, _cn - 2.0), (40.0, _cn - 3.8)]),    # north cell - -> vias into the pour, north
    ("BAT-", "B", W_PWR, [(45.0, _cn - 2.0), (45.0, _cn - 3.8)]),    # of its clip
    ("MID", "B", 1.0, [(2.2, _cn + 2.0), (2.2, _cs - 2.0)]),         # the cells' link, west
    ("MID", "B", 0.4, [(5.5, _cs + 2.0), (5.5, _mv)]),                # down from the south-west clip's pad
    ("MID", "F", 0.4, [(5.5, _mv), (_rx - 0.51, _mv)]),               # east under the frame
]
VIAS = [("BAT-", 40.0, _cn - 3.8), ("BAT-", 45.0, _cn - 3.8), ("MID", 5.5, _mv), ("MID", _rx - 0.51, _mv)]
# the resistor's MID pad sits straight under its via
TRACKS.append(("MID", "B", 0.4, [(_rx - 0.51, _mv), (_rx - 0.51, _SY)]))

LAYERS = """	(layers
		(0 "F.Cu" signal)
		(2 "B.Cu" signal)
		(9 "F.Adhes" user "F.Adhesive")
		(11 "B.Adhes" user "B.Adhesive")
		(13 "F.Paste" user)
		(15 "B.Paste" user)
		(5 "F.SilkS" user "F.Silkscreen")
		(7 "B.SilkS" user "B.Silkscreen")
		(1 "F.Mask" user)
		(3 "B.Mask" user)
		(17 "Dwgs.User" user "User.Drawings")
		(19 "Cmts.User" user "User.Comments")
		(25 "Edge.Cuts" user)
		(27 "Margin" user)
		(31 "F.CrtYd" user "F.Courtyard")
		(29 "B.CrtYd" user "B.Courtyard")
		(35 "F.Fab" user)
		(33 "B.Fab" user)
	)"""

mm = pcbnew.FromMM
pt = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))


def clip_blocks():
    """BT_N and BT_P as the device board stores them (on B, holding a cell at y 72), as text,
    four copies with fresh uuids and no nets: north/south x minus/plus."""
    t = open(DEVICE, encoding="utf-8").read()
    found = {}
    for m in re.finditer(r'\n\t\(footprint "', t):
        a = m.start()
        b = t.find("\n\t(", a + 1)
        ref = re.search(r'\(property "Reference" "([^"]+)"', t[a:b])
        if ref and ref.group(1) in CLIPS:
            found[ref.group(1)] = t[a:b]
    if set(found) != set(CLIPS):
        sys.exit(f"clips not in {DEVICE}: {set(CLIPS) - set(found)}")
    out = []
    for cell in ("1", "2"):                  # 1 north, 2 south
        for ref, blk in found.items():
            blk = re.sub(r'\(uuid "[^"]+"\)', lambda _: f'(uuid "{uuid.uuid4()}")', blk)
            blk = re.sub(r'\n\t*\(net "[^"]*"\)', "", blk)
            blk = blk.replace(f'(property "Reference" "{ref}"', f'(property "Reference" "{ref}{cell}"', 1)
            out.append(blk)
    return "".join(out)


def start_file():
    text = ("(kicad_pcb\n\t(version 20260206)\n\t(generator \"pcbnew\")\n\t(generator_version \"10.0\")\n"
            f"\t(general\n\t\t(thickness {THICKNESS})\n\t\t(legacy_teardrops no)\n\t)\n\t(paper \"A4\")\n"
            "\t(title_block\n\t\t(title \"Ambire Ice: battery pack\")\n\t\t(rev \"v4\")\n\t)\n"
            f"{LAYERS}\n\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t\t(allow_soldermask_bridges_in_footprints no)\n"
            "\t\t(tenting\n\t\t\t(front yes)\n\t\t\t(back yes)\n\t\t)\n\t)" + clip_blocks() + "\n)\n")
    open(OUT, "w", encoding="utf-8").write(text)


def edge(board):
    x0, y0, x1, y1 = OUTLINE
    r = CORNER_R
    def seg(a, b_):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pt(*a)); s.SetEnd(pt(*b_)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.05)); board.Add(s)
    def arc(a, m, b_):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        s.SetArcGeometry(pt(*a), pt(*m), pt(*b_)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.05)); board.Add(s)
    k = r * (1 - 2 ** -0.5)
    seg((x0 + r, y0), (x1 - r, y0)); arc((x1 - r, y0), (x1 - k, y0 + k), (x1, y0 + r))
    seg((x1, y0 + r), (x1, y1 - r)); arc((x1, y1 - r), (x1 - k, y1 - k), (x1 - r, y1))
    seg((x1 - r, y1), (x0 + r, y1)); arc((x0 + r, y1), (x0 + k, y1 - k), (x0, y1 - r))
    seg((x0, y1 - r), (x0, y0 + r)); arc((x0, y0 + r), (x0 + k, y0 + k), (x0 + r, y0))


def set_fields(fp, value, lcsc, mpn):
    fp.SetValue(value)
    fp.SetField("LCSC Part #", lcsc)
    fp.SetField("MPN", mpn)
    for f in fp.GetFields():
        if f.GetName() in ("LCSC Part #", "MPN"):
            f.SetVisible(False)


def text(board, s, x, y, layer, size=0.8, mirrored=False):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s); t.SetPosition(pt(x, y)); t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); t.SetTextThickness(mm(size * 0.15))
    if mirrored:
        t.SetMirrored(True)
    board.Add(t)


def rules_of(path):
    """What of the project file the board depends on: the rules and the net classes."""
    d = json.load(open(path))
    return json.dumps([d["board"]["design_settings"]["rules"], d["net_settings"]["classes"],
                       d["net_settings"].get("netclass_patterns")], sort_keys=True)


def check_pins_clear_clips():
    """The pins' plated peg holes and the frame's peg holes stay JOINT_CLR off every
    clip's steel on the inner face (CLIP_STEEL). Returns the least clearance."""
    least = 99.0
    holes = [(net, x, CONN_Y, CONN_PAD / 2) for net, x in PIN.items()] + [("frame peg", x, y, _FRAME["pegHole"] / 2) for x, y in PEGS]
    for x0, y0, x1, y1 in CLIP_STEEL:
        for what, x, y, r in holes:
            d = ((max(x0 - x, 0, x - x1)) ** 2 + (max(y0 - y, 0, y - y1)) ** 2) ** 0.5 - r
            least = min(least, d)
            if d < JOINT_CLR - 1e-6:
                sys.exit(f"{what} at {x}, {y} is {d:.2f} mm from clip steel at {x0}..{x1}, {y0}..{y1}")
    return least


def frame_footprint():
    """The printed frame's footprint, from the case's numbers: its two peg holes (NPTH), its
    outline and the magnets' seats on F.Fab. Not assembled: out of the BOM and the CPL."""
    x0, y0, x1, y1 = (v - c for v, c in zip(_FO, (CONN_X, CONN_Y, CONN_X, CONN_Y)))
    t = [v - c for v, c in zip(_FRAME["tab"], (CONN_X, CONN_Y, CONN_X, CONN_Y))]
    r = (y1 - y0) / 2
    fab = lambda body: f'\t{body} (stroke (width 0.1) (type solid)) (layer "F.Fab"))'
    lines = [f'(footprint "{FRAME_FP}"', '\t(version 20240108)', '\t(generator "makePack")', '\t(layer "F.Cu")',
             '\t(descr "The pack\'s printed frame (enclosure/generate.py): it holds the two magnets on this face and keys '
             'the pack in the window in the device\'s back; its pegs go into these holes. Printed, glued: not in the BOM or the CPL.")',
             '\t(attr exclude_from_pos_files exclude_from_bom)',
             '\t(property "Reference" "FR1" (at 0 0 0) (layer "F.Fab") (hide yes) (effects (font (size 0.6 0.6) (thickness 0.1))))',
             '\t(property "Value" "frame (print)" (at 0 0 0) (layer "F.Fab") (hide yes) (effects (font (size 0.6 0.6) (thickness 0.1))))',
             fab(f"(fp_line (start {x0 + r:.3f} {y0:.3f}) (end {x1 - r:.3f} {y0:.3f})"),
             fab(f"(fp_line (start {x0 + r:.3f} {y1:.3f}) (end {x1 - r:.3f} {y1:.3f})"),
             fab(f"(fp_arc (start {x1 - r:.3f} {y0:.3f}) (mid {x1:.3f} {(y0 + y1) / 2:.3f}) (end {x1 - r:.3f} {y1:.3f})"),
             fab(f"(fp_arc (start {x0 + r:.3f} {y1:.3f}) (mid {x0:.3f} {(y0 + y1) / 2:.3f}) (end {x0 + r:.3f} {y0:.3f})"),
             fab(f"(fp_rect (start {t[0]:.3f} {t[1]:.3f}) (end {t[2]:.3f} {t[3]:.3f}) (fill no)")]
    d = _PACK["magnets"]["d"]
    for mx, my in MAGNETS:
        lines.append(fab(f"(fp_circle (center {mx - CONN_X:.3f} {my - CONN_Y:.3f}) (end {mx - CONN_X + d / 2:.3f} {my - CONN_Y:.3f}) (fill no)"))
    h = _FRAME["pegHole"]
    for px, py in PEGS:
        lines.append(f'\t(pad "" np_thru_hole circle (at {px - CONN_X:.3f} {py - CONN_Y:.3f}) (size {h} {h}) (drill {h}) (layers "*.Cu" "*.Mask"))')
    lines.append(")")
    open(os.path.join(HERE, "pack.pretty", FRAME_FP + ".kicad_mod"), "w").write("\n".join(lines) + "\n")


def main():
    print(f"contacts at {CONN_X}, {CONN_Y}; pins {PIN}; magnets {MAGNETS}; holes {check_pins_clear_clips():.2f} mm from the clips' steel")
    pro_before = rules_of(PRO)
    start_file()
    board = pcbnew.LoadBoard(OUT)
    board.GetDesignSettings().SetBoardThickness(mm(THICKNESS))
    edge(board)
    nets = {}
    def net(name):
        if name not in nets:
            n = pcbnew.NETINFO_ITEM(board, name)
            board.Add(n)
            nets[name] = n
        return nets[name]

    # the clips: north cell turned end for end about its own centre (+ west)
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    plan = {"BT_N1": (CLIP_X[1], CELL_YS[0], 180, "BAT-"), "BT_P1": (CLIP_X[0], CELL_YS[0], 0, "MID"),
            "BT_N2": (CLIP_X[0], CELL_YS[1], 0, "MID"), "BT_P2": (CLIP_X[1], CELL_YS[1], 180, "CELL+")}
    for ref, (x, y, rot, n) in plan.items():
        f = fps[ref]
        if f.GetLayer() != pcbnew.B_Cu:
            sys.exit(f"{ref} is not on the back")
        f.SetPosition(pt(x, y)); f.SetOrientationDegrees(rot)
        set_fields(f, f.GetValue(), *CLIPS[ref[:4]])
        for p in f.Pads():
            p.SetNet(net(n))

    # the pins on the outer face, each pad (the four quadrants and the plated peg hole) on its net
    ref, lib, name, value, lcsc, mpn = PINS
    for i, (n, x) in enumerate(PIN.items()):
        c = pcbnew.FootprintLoad(lib, name)
        c.SetReference(f"{ref}{i + 1}"); set_fields(c, value, lcsc, mpn)
        c.Reference().SetVisible(False)      # the outer face stays bare: the pins are labelled north of the frame
        board.Add(c)
        c.SetPosition(pt(x, CONN_Y))
        for p in c.Pads():
            p.SetNet(net(n))
    # the frame's peg holes and outline
    frame_footprint()
    f = pcbnew.FootprintLoad(os.path.join(HERE, "pack.pretty"), FRAME_FP)
    board.Add(f)
    f.SetPosition(pt(CONN_X, CONN_Y))

    # the small parts on the inner face; nets by pad, west and east
    for ref, lib, name, value, lcsc, mpn, (x, y), rot, (west, east) in SMALL:
        f = pcbnew.FootprintLoad(lib, name)
        f.SetReference(ref); set_fields(f, value, lcsc, mpn)
        board.Add(f)
        f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)   # two-pad, unpolarised: the mirror axis does not matter
        f.SetPosition(pt(x, y)); f.SetOrientationDegrees(rot)
        f.Reference().SetVisible(False)       # no room for labels beside the pins
        for p in f.Pads():                    # west (or north, turned) pad first
            first = p.GetPosition().x < mm(x) - 1000 or (abs(p.GetPosition().x - mm(x)) <= 1000 and p.GetPosition().y < mm(y))
            p.SetNet(net(west if first else east))
        print(ref, [(p.GetNetname(), round(p.GetPosition().x / 1e6, 3), round(p.GetPosition().y / 1e6, 3)) for p in f.Pads()])

    for n, layer, w, pts in TRACKS:
        for a, b_ in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pt(*a)); t.SetEnd(pt(*b_)); t.SetWidth(mm(w))
            t.SetLayer(pcbnew.F_Cu if layer == "F" else pcbnew.B_Cu); t.SetNet(net(n))
            board.Add(t)
    for n, x, y in VIAS:
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pt(x, y)); v.SetWidth(mm(VIA[0])); v.SetDrill(mm(VIA[1])); v.SetNet(net(n))
        board.Add(v)

    # the outer face: one BAT- pour, solid to its pads
    z = pcbnew.ZONE(board)
    z.SetLayer(pcbnew.F_Cu); z.SetNet(net("BAT-"))
    z.SetLocalClearance(mm(0.25)); z.SetMinThickness(mm(0.25)); z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    x0, y0, x1, y1 = OUTLINE
    poly = z.Outline()
    poly.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        poly.Append(mm(x), mm(y))
    board.Add(z)

    # labels: the cells' ends on the inner face (read from behind: mirrored), the pins on the outer
    for y, (w_, e_) in zip(CELL_YS, (("+", "-"), ("-", "+"))):
        text(board, w_, 12.5, y, pcbnew.B_SilkS, 1.6, mirrored=True)
        text(board, e_, 35.5, y, pcbnew.B_SilkS, 1.6, mirrored=True)
        text(board, "AAA", 24.0, y, pcbnew.B_SilkS, 1.2, mirrored=True)
    for n, px in PIN.items():                 # north of the frame's tab
        text(board, n.replace("_SENSE", ""), px, _FRAME["tab"][1] - 0.9, pcbnew.F_SilkS, 0.7)
    text(board, "AMBIRE ICE BATTERY PACK v4", 24.0, OUTLINE[1] + 2.2, pcbnew.F_SilkS, 0.9)

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(OUT, board)
    if rules_of(PRO) != pro_before:
        sys.exit("SaveBoard changed pack.kicad_pro's rules or net classes")
    print(f"wrote {OUT}: {len(list(board.GetFootprints()))} parts, {len(list(board.GetTracks()))} tracks/vias, nets {sorted(nets)}")


if __name__ == "__main__":
    main()
