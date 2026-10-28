#!/usr/bin/env python3
"""The v4 battery pack's board: placed and routed by script, from the numbers the case
generator fixes (enclosure/generate.py, docs/v4/enclosure/enclosure.json -> "pack").

    KICAD_PY pack/makePack.py        (from hardware/ambire-hw; writes pack/pack.kicad_pcb)

It writes the board from scratch: after the first hand edit, edit the board, not this.

Coordinates are the DEVICE board's (x east, y south, mm): seen from its outer face (F,
toward the device), the pack lines up with the device seen from the front.

  F (outer face, against the device's back): the Xinyangze magnetic pogo male, its pins
    in a column at x 4.5, BAT+ north .. BAT- south; a BAT- pour; nothing else stands on it.
  B (inner face): two AAA cells east-west in series, in MYOUNG clips copied from the
    device board (the south cell as proto 1's cell sat, + east; the north cell turned
    end for end, + west; the west clips are the mid-point); the fuse, the thermistor and
    the mid-point's series resistor between the cells; two rings where the 6 x 2 mm
    magnets are glued.

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
KICAD_FP = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"

# ── geometry, from the case generator (2026-10-28) ──────────────────────────
OUTLINE = (0.0, 19.707, 48.0, 47.807)
CORNER_R = 1.0
THICKNESS = 0.8
CELL_YS = (26.507, 41.007)          # north, south
CLIP_X = (5.5, 42.5)                # the clips' base centres, west and east (proto 1's)
CONN_AT, CONN_ROT = (4.5, 32.707), 90   # body centre; 90 puts the tab east
CONN_PADS = {28.957: "BAT+", 31.457: "MID_SENSE", 33.957: "NTC", 36.457: "BAT-"}   # by y
MAGNETS = ((16.30, 33.757), (34.05, 33.757))
MAGNET_D = 6.2

# ── parts ───────────────────────────────────────────────────────────────────
# (ref, library, footprint, value, LCSC, mpn, at, rotation)
SMALL = [
    ("F_BAT", f"{KICAD_FP}/Fuse.pretty", "Fuse_1206_3216Metric", "0.75A hold", "C976305", "BSMD1206-075-30V",
     (25.2, 33.757), 0),
    ("R_MID", f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric", "1k", "C11702", "0402WGF1001TCE",
     (7.6, 32.98), 0),
    ("RT_PACK", f"{KICAD_FP}/Resistor_SMD.pretty", "R_0603_1608Metric", "10k NTC B3380", "C13564", "NCP18XH103F03RB",
     (7.6, 34.27), 0),
]
CONN = ("J_PACK", os.path.join(HERE, "pack.pretty"), "Xinyangze_YZR0158-20050-04025-01", "YZR0158-20050-04025-01",
        "C5126843", "YZR0158-20050-04025-01")
CLIPS = {"BT_N": ("C6937104", "MY-AAA-19-J"), "BT_P": ("C6937107", "MY-AAA-20-J")}

W_PWR, W_SIG = 0.8, 0.25
VIA = (0.6, 0.3)

# ── copper, by hand: (net, layer, width, points) and (net, x, y) vias ───────
# The clips' courtyards (their arms) leave a 2.6 mm band between the west clips, y 32.45-35.06:
# the resistor and the thermistor stack in it, beside the connector. The fuse sits in the
# middle, between the magnets' rings, clear of the cans (a 1206 needs ~3.2 mm off a cell's axis).
TRACKS = [
    ("CELL+", "B", W_PWR, [(38.6, 39.0), (32.5, 39.0), (28.0, 34.5), (26.6, 33.757)]),   # south cell + -> fuse
    ("BAT+", "B", W_PWR, [(23.8, 33.757), (22.5, 33.757)]),                               # fuse -> via
    ("BAT+", "F", W_PWR, [(22.5, 33.757), (22.5, 28.0), (21.6, 27.1), (6.6, 27.1), (4.5, 28.957)]),   # -> the + pin
    ("BAT-", "B", W_PWR, [(40.0, 28.5), (40.0, 30.5)]),              # north cell - -> vias into the F pour
    ("BAT-", "B", W_PWR, [(45.0, 28.5), (45.0, 30.5)]),
    ("MID", "B", 1.0, [(2.2, 28.5), (2.2, 39.0)]),                   # the cells' link, west
    ("MID", "B", 0.4, [(2.2, 32.98), (7.09, 32.98)]),                # -> the series resistor
    ("MID_SENSE", "B", W_SIG, [(8.11, 32.98), (9.5, 31.6)]),
    ("MID_SENSE", "F", W_SIG, [(9.5, 31.6), (4.5, 31.457)]),
    ("NTC", "F", W_SIG, [(4.5, 33.957), (5.9, 35.6)]),
    ("NTC", "B", W_SIG, [(5.9, 35.6), (6.775, 34.27)]),
    ("BAT-", "B", W_SIG, [(8.425, 34.27), (9.6, 34.9)]),
]
VIAS = [("BAT+", 22.5, 33.757), ("BAT-", 40.0, 30.5), ("BAT-", 45.0, 30.5), ("MID_SENSE", 9.5, 31.6),
        ("NTC", 5.9, 35.6), ("BAT-", 9.6, 34.9)]

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
            "\t(title_block\n\t\t(title \"Ambire hardware signer: battery pack\")\n\t\t(rev \"v4\")\n\t)\n"
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


def main():
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
    c.SetPosition(pt(*CONN_AT)); c.SetOrientationDegrees(CONN_ROT)
    cy = c.GetCourtyard(pcbnew.F_CrtYd).BBox() if hasattr(c, "GetCourtyard") else c.GetBoundingBox()
    if (cy.GetCenter().x - mm(CONN_AT[0])) <= 0:
        sys.exit("the connector's tab does not point east: change CONN_ROT")
    for p in c.Pads():
        y = round(p.GetPosition().y / 1e6, 3)
        hit = [n for py, n in CONN_PADS.items() if abs(py - y) < 0.01]
        if len(hit) != 1 or abs(p.GetPosition().x / 1e6 - CONN_AT[0]) > 0.01:
            sys.exit(f"connector pad {p.GetNumber()} at {p.GetPosition().x/1e6:.3f}, {y} is not one of {CONN_PADS}")
        p.SetNet(net(hit[0]))

    # the small parts on the inner face
    for ref, lib, name, value, lcsc, mpn, (x, y), rot in SMALL:
        f = pcbnew.FootprintLoad(lib, name)
        f.SetReference(ref); set_fields(f, value, lcsc, mpn)
        board.Add(f)
        f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)   # two-pad, unpolarised: the mirror axis does not matter
        f.SetPosition(pt(x, y)); f.SetOrientationDegrees(rot)
        if ref in ("R_MID", "RT_PACK"):
            f.Reference().SetVisible(False)   # no room for labels in the band between the clips' arms
    pad_nets = {"F_BAT": ("CELL+", "BAT+"), "R_MID": ("MID_SENSE", "MID"), "RT_PACK": ("BAT-", "NTC")}
    for f in board.GetFootprints():
        if f.GetReference() in pad_nets:
            # by position: the fuse's south pad meets the cell, the resistor's and the thermistor's west pad
            pads = sorted(f.Pads(), key=lambda p: (p.GetPosition().y, p.GetPosition().x) if f.GetReference() == "F_BAT"
                          else (-p.GetPosition().x,))
            if f.GetReference() == "F_BAT":
                pads[0].SetNet(net("BAT+")); pads[1].SetNet(net("CELL+"))
            elif f.GetReference() == "R_MID":
                pads[0].SetNet(net("MID_SENSE")); pads[1].SetNet(net("MID"))
            else:
                pads[0].SetNet(net("BAT-")); pads[1].SetNet(net("NTC"))
            print(f.GetReference(), [(p.GetNetname(), round(p.GetPosition().x / 1e6, 3), round(p.GetPosition().y / 1e6, 3))
                                      for p in f.Pads()])

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

    # the magnets' rings and the labels
    for mx, my in MAGNETS:
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_CIRCLE)
        s.SetCenter(pt(mx, my)); s.SetEnd(pt(mx + MAGNET_D / 2, my)); s.SetLayer(pcbnew.B_SilkS); s.SetWidth(mm(0.15))
        board.Add(s)
        text(board, "MAGNET", mx, my, pcbnew.B_SilkS, 0.8, mirrored=True)
    for y, (w_, e_) in zip(CELL_YS, (("+", "-"), ("-", "+"))):
        text(board, w_, 12.5, y, pcbnew.B_SilkS, 1.6, mirrored=True)
        text(board, e_, 35.5, y, pcbnew.B_SilkS, 1.6, mirrored=True)
        text(board, "AAA", 24.0, y, pcbnew.B_SilkS, 1.2, mirrored=True)
    for py, n in CONN_PADS.items():
        text(board, n.replace("_SENSE", ""), 12.6, py, pcbnew.F_SilkS, 0.8)
    text(board, "AMBIRE SIGNER BATTERY PACK v4", 28.0, 45.6, pcbnew.F_SilkS, 0.9)

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(OUT, board)
    if rules_of(PRO) != pro_before:
        sys.exit("SaveBoard changed pack.kicad_pro's rules or net classes")
    print(f"wrote {OUT}: {len(list(board.GetFootprints()))} parts, {len(list(board.GetTracks()))} tracks/vias, nets {sorted(nets)}")


if __name__ == "__main__":
    main()
