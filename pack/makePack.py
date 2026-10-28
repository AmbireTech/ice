#!/usr/bin/env python3
"""The v4 battery pack's board: placed and routed by script, from the numbers the case
generator fixes (enclosure/generate.py, docs/v4/enclosure/enclosure.json -> "pack").

    KICAD_PY pack/makePack.py        (from hardware/ambire-hw; writes pack/pack.kicad_pcb)

It writes the board from scratch: after the first hand edit, edit the board, not this.

Coordinates are the DEVICE board's (x east, y south, mm): seen from its outer face (F,
toward the device), the pack lines up with the device seen from the front.

  F (outer face, against the device's back): the Xinyangze magnetic pogo male (through-
    hole), its pins in a column north-south, its tab west; a BAT- pour; nothing else on it.
    It mates with the magnetic receptacle on the device's back, whose magnets hold the pack.
  B (inner face): two AAA cells east-west in series, in MYOUNG clips copied from the
    device board (the south cell as proto 1's cell sat, + east; the north cell turned
    end for end, + west; the west clips are the mid-point); the fuse, the thermistor and
    the mid-point's series resistor beside the connector's pins.

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
DEVICE = os.path.join(ROOT, "board/wallet.kicad_pcb")
ENCLOSURE = os.path.join(ROOT, "docs/v4/enclosure/enclosure.json")
KICAD_FP = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"

# ── geometry: the outline and the cells from the case generator ─────────────
_PACK = json.load(open(ENCLOSURE))["pack"]
OUTLINE = tuple(_PACK["board"])           # x0, y0, x1, y1
CELL_YS = tuple(_PACK["cells"]["y"])      # north, south
CORNER_R = 1.0
THICKNESS = 0.8
CLIP_X = (5.5, 42.5)                      # the clips' base centres, west and east (proto 1's)
CLIP_BASE = (10.0, 7.0)                   # MY-AAA-19-J / -20-J steel base, x by y (the footprint's descr)
# The connector. The case put its pin column at x 43.6 (over the device's receptacle), but
# there the column lands on the east clips' steel bases (x 37.5-47.5, 3.5 mm each side of
# a cell's axis): the BAT- pin on the south clip's pad, BAT+ under the north clip's base,
# and its through-hole joints could not be soldered under the clips' arms. Clear of the
# bases the column fits from x 11.5 to 36.5: CONN_X is the nearest to the case's.
CONN_X, CONN_Y = 36.5, 35.4
CONN_ROT = 270                            # long axis north-south, tab west
CONN_PAD = 1.6                            # the land's pad (see the footprint)
PITCH = 2.5
PIN_NETS = ["BAT+", "MID_SENSE", "NTC", "BAT-"]   # north to south: the one place the pin order lives
PIN_YS = [round(CONN_Y + (i - 1.5) * PITCH, 3) for i in range(4)]
PIN = dict(zip(PIN_NETS, PIN_YS))

# ── parts ───────────────────────────────────────────────────────────────────
# (ref, library, footprint, value, LCSC, mpn, at, rotation, (west pad's net, east pad's net))
# Each sits west of the pins, on its pin's line.
SMALL = [
    ("F_BAT", f"{KICAD_FP}/Fuse.pretty", "Fuse_1206_3216Metric", "0.75A hold", "C976305", "BSMD1206-075-30V",
     (31.0, PIN["BAT+"]), 0, ("CELL+", "BAT+")),
    ("R_MID", f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric", "1k", "C11702", "0402WGF1001TCE",
     (34.0, PIN["MID_SENSE"]), 0, ("MID", "MID_SENSE")),
    ("RT_PACK", f"{KICAD_FP}/Resistor_SMD.pretty", "R_0603_1608Metric", "10k NTC B3380", "C13564", "NCP18XH103F03RB",
     (34.0, PIN["NTC"]), 0, ("BAT-", "NTC")),
]
CONN = ("J_PACK", os.path.join(HERE, "pack.pretty"), "Xinyangze_YZP0048-20048-04025-03", "YZP0048-20048-04025-03",
        "C5126845", "YZP0048-20048-04025-03")
CLIPS = {"BT_N": ("C6937104", "MY-AAA-19-J"), "BT_P": ("C6937107", "MY-AAA-20-J")}

W_PWR, W_SIG = 0.8, 0.25
VIA = (0.6, 0.3)

# ── copper, by hand: (net, layer, width, points) and (net, x, y) vias ───────
# A straight run from each small part to its pin. CELL+ goes round them to the south-east
# clip; the mid-point crosses from the west clips on the outer face, under the
# connector's tab; BAT- reaches the outer face's pour.
_cn, _cs = CELL_YS
TRACKS = [
    ("BAT+", "B", W_PWR, [(32.4, PIN["BAT+"]), (CONN_X, PIN["BAT+"])]),
    ("MID_SENSE", "B", W_SIG, [(34.51, PIN["MID_SENSE"]), (CONN_X, PIN["MID_SENSE"])]),
    ("NTC", "B", W_SIG, [(34.825, PIN["NTC"]), (CONN_X, PIN["NTC"])]),
    ("BAT-", "B", W_SIG, [(33.175, PIN["NTC"]), (32.2, PIN["NTC"] + 0.95)]),
    ("CELL+", "B", W_PWR, [(29.6, PIN["BAT+"]), (28.4, PIN["BAT+"] + 1.2), (28.4, _cs + 0.7), (38.6, _cs + 0.7)]),
    ("BAT-", "B", W_PWR, [(40.0, _cn + 2.0), (40.0, _cn + 3.5)]),    # north cell - -> vias into the pour
    ("BAT-", "B", W_PWR, [(45.0, _cn + 2.0), (45.0, _cn + 3.5)]),
    ("MID", "B", 1.0, [(2.2, _cn + 2.0), (2.2, _cs - 2.0)]),         # the cells' link, west
    ("MID", "B", 0.4, [(2.2, PIN["MID_SENSE"]), (5.0, PIN["MID_SENSE"])]),
    ("MID", "F", 0.4, [(5.0, PIN["MID_SENSE"]), (31.6, PIN["MID_SENSE"])]),
    ("MID", "B", 0.4, [(31.6, PIN["MID_SENSE"]), (33.49, PIN["MID_SENSE"])]),
]
VIAS = [("BAT-", 32.2, PIN["NTC"] + 0.95), ("BAT-", 40.0, _cn + 3.5), ("BAT-", 45.0, _cn + 3.5),
        ("MID", 5.0, PIN["MID_SENSE"]), ("MID", 31.6, PIN["MID_SENSE"])]

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
        sys.exit(f"clips not on the device board: {set(CLIPS) - set(found)}")
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
    """The connector's pads stay off every clip's steel base, with the board's clearance."""
    clr = 0.2
    for cx in CLIP_X:
        for cy in CELL_YS:
            bx0, bx1 = cx - CLIP_BASE[0] / 2, cx + CLIP_BASE[0] / 2
            by0, by1 = cy - CLIP_BASE[1] / 2, cy + CLIP_BASE[1] / 2
            for net, y in PIN.items():
                d = ((max(bx0 - CONN_X, 0, CONN_X - bx1)) ** 2 + (max(by0 - y, 0, y - by1)) ** 2) ** 0.5
                if d < CONN_PAD / 2 + clr:
                    sys.exit(f"pin {net} at {CONN_X}, {y} is {d:.2f} mm from the clip base at {cx}, {cy}")


def main():
    check_pins_clear_clips()
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

    # the connector on the outer face
    ref, lib, name, value, lcsc, mpn = CONN
    c = pcbnew.FootprintLoad(lib, name)
    c.SetReference(ref); set_fields(c, value, lcsc, mpn)
    c.Reference().SetVisible(False)          # the outer face stays bare: the pins are labelled beside the body
    board.Add(c)
    c.SetPosition(pt(CONN_X, CONN_Y)); c.SetOrientationDegrees(CONN_ROT)
    silk = [g.GetBoundingBox() for g in c.GraphicalItems() if g.GetLayer() == pcbnew.F_SilkS]
    tab_x = (min(b.GetLeft() for b in silk) + max(b.GetRight() for b in silk)) / 2e6   # the outline with its tab
    if tab_x >= CONN_X:
        sys.exit(f"the connector's tab does not point west (outline centre x {tab_x:.2f}): change CONN_ROT")
    for p in c.Pads():
        y = round(p.GetPosition().y / 1e6, 3)
        hit = [n for n, py in PIN.items() if abs(py - y) < 0.01]
        if len(hit) != 1 or abs(p.GetPosition().x / 1e6 - CONN_X) > 0.01:
            sys.exit(f"connector pad {p.GetNumber()} at {p.GetPosition().x/1e6:.3f}, {y} is not one of {PIN}")
        p.SetNet(net(hit[0]))

    # the small parts on the inner face; nets by pad, west and east
    for ref, lib, name, value, lcsc, mpn, (x, y), rot, (west, east) in SMALL:
        f = pcbnew.FootprintLoad(lib, name)
        f.SetReference(ref); set_fields(f, value, lcsc, mpn)
        board.Add(f)
        f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)   # two-pad, unpolarised: the mirror axis does not matter
        f.SetPosition(pt(x, y)); f.SetOrientationDegrees(rot)
        f.Reference().SetVisible(False)       # no room for labels beside the pins
        for p in f.Pads():
            p.SetNet(net(west if p.GetPosition().x < mm(x) else east))
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
    for n, py in PIN.items():
        text(board, n.replace("_SENSE", ""), 27.6, py, pcbnew.F_SilkS, 0.8)
    text(board, "AMBIRE ICE BATTERY PACK v4", 20.0, OUTLINE[3] - 2.2, pcbnew.F_SilkS, 0.9)

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(OUT, board)
    if rules_of(PRO) != pro_before:
        sys.exit("SaveBoard changed pack.kicad_pro's rules or net classes")
    print(f"wrote {OUT}: {len(list(board.GetFootprints()))} parts, {len(list(board.GetTracks()))} tracks/vias, nets {sorted(nets)}")


if __name__ == "__main__":
    main()
