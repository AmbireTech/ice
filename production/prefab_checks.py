#!/usr/bin/env python3
"""Pre-fab checks on the board and the order set make_order.sh built; writes PREFAB-CHECKS.txt.

    KPY production/prefab_checks.py production/order

Every line is PASS, FAIL or NOTE; the exit code is 1 if anything FAILs. What it checks:
board (KiCad DRC as saved and after a refill, the repo's gates), assembly (BOM/CPL
parity, JLC stock, parts JLC places with no rotation rule), fabrication (drill census,
smallest drill, outline), and the colour print (mask openings vs KiCad's B.Mask plot,
EasyEDA-form Gerbers geometrically identical to KiCad's, the labels the art replaces
left out of the plain silk, the zip's contents).
"""
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

import pcbnew

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARD = os.path.join(HERE, "board/wallet.kicad_pcb")
KPY = sys.executable
lines, failed = [], []


def report(ok, what, detail=""):
    tag = "PASS" if ok is True else ("NOTE" if ok is None else "FAIL")
    lines.append(f"{tag}  {what}" + (f": {detail}" if detail else ""))
    if ok is False:
        failed.append(what)


def run(cmd):
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    out = "\n".join(l for l in (r.stdout + r.stderr).splitlines() if not re.search(r"swig|Debug|wxApp|leak", l))
    return r.returncode, out.strip()


def drc(refill):
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "drc.json")
        cmd = ["kicad-cli", "pcb", "drc", "--format", "json", "-o", out, BOARD] + (["--refill-zones"] if refill else [])
        subprocess.run(cmd, cwd=HERE, capture_output=True)
        d = json.load(open(out))
    v = [x["type"] for x in d["violations"]]
    return v, len(d["unconnected_items"])


def main():
    order = os.path.abspath(sys.argv[1])

    # ---- board
    for refill in (False, True):
        v, u = drc(refill)
        report(not v and u == 0, f"KiCad DRC {'after a zone refill' if refill else 'as saved'}",
               f"{len(v)} violations {sorted(set(v))}, {u} unconnected")
    gates = [(["python3", "scripts/checkNets.py"], "nets: every named net reaches two pads"),
             (["python3", "scripts/checkMcuAf.py"], "MCU pins vs ST's alternate functions"),
             ([KPY, "scripts/syncBoard.py"], "board matches the netlist"),
             ([KPY, "scripts/checkPinNets.py"], "IC pads vs datasheet pin tables"),
             ([KPY, "scripts/checkFpc.py"], "flex connectors: orientation, tail slack"),
             ([KPY, "scripts/checkParts.py", "board/wallet.kicad_pcb"], "lands vs JLC's land for each part"),
             ([KPY, "scripts/cleanCopper.py", "board/wallet.kicad_pcb", "--check"], "no stale copper")]
    for cmd, what in gates:
        code, out = run(cmd)
        bad = code != 0 or re.search(r"(?m)^ERROR|LAND MISMATCH|cannot place|not written|mismatch", out)
        report(not bad, what, out.splitlines()[-1] if out else "")
    code, out = run([KPY, "scripts/stripGndTies.py", "board/wallet.kicad_pcb", "--list"])
    drops = [l.strip() for l in out.splitlines() if l.strip().startswith("drop")]
    report(not drops, "no GND pad-to-via ties beyond the kept ones", "; ".join(drops) or "none")

    # ---- assembly
    log = open(os.path.join(order, "jlc-plain/build.log")).read()
    parity = re.search(r"BOM refs (\d+) in (\d+) lines; CPL rows (\d+)", log)
    report(parity and parity.group(1) == parity.group(3) and "CPL not BOM: []" in log and "BOM not CPL: []" in log
           and "no LCSC part: []" in log, "BOM / placement parity, every part has an LCSC number",
           parity.group(0) if parity else "no parity line")
    stock = list(csv.DictReader(open(os.path.join(order, "jlc-plain/work/stock.csv"))))
    short = [f"{r['LCSC']} {r['MPN']} ({r['Flag']})" for r in stock if r.get("Flag") and "deprioritized" not in r["Flag"]]
    dnp_at_order = [f"{r['LCSC']} {r['MPN']} ({r['Refs']})" for r in stock if "deprioritized" in r.get("Flag", "")]
    report(not short, "JLC stock for 10 boards", f"{len(stock)} lines" + (f"; short: {short}" if short else ", all ok"))
    if dnp_at_order:
        report(None, "out of stock, to DNP in JLC's order UI", "; ".join(dnp_at_order))
    rot = list(csv.DictReader(open(os.path.join(order, "jlc-plain/work/rotation-review.csv"))))
    eye = [r["Designator"] for r in rot if "eye" in r.get("Status", "").lower()]
    report(None, "check by eye in JLC's placement preview", ", ".join(x for x in eye if x))
    cpl = list(csv.DictReader(open(os.path.join(order, "wallet_positions-jlc.csv"))))
    top = [r["Designator"] for r in cpl if r["Layer"] == "Top"]
    if top and len(top) < len(cpl):
        report(None, "assembly side in JLC's order form: choose both sides",
               f"{len(cpl) - len(top)} bottom + {len(top)} top ({', '.join(top)})")

    # ---- fabrication
    board = pcbnew.LoadBoard(BOARD)
    holes = [v.GetDrillValue() for v in board.GetTracks() if v.GetClass() == "PCB_VIA"]
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetDrillSizeX() > 0:
                holes.append(min(p.GetDrillSizeX(), p.GetDrillSizeY()))
    drl = "".join(open(os.path.join(order, "jlc-plain/gerbers", f)).read() for f in ("wallet-PTH.drl", "wallet-NPTH.drl"))
    hits = (len(re.findall(r"(?m)^X[-\d.]+Y[-\d.]+$", drl))          # drilled holes
            + len(re.findall(r"(?m)^X[-\d.]+Y[-\d.]+G85X", drl))       # slots, G85 form
            + len(re.findall(r"(?m)^M15$", drl)))                       # slots, route form (KiCad's oval holes)
    report(hits == len(holes), "drill census: board holes vs drill files", f"{len(holes)} on the board, {hits} in the files")
    report(min(holes) / 1e6 >= 0.25, "smallest drill (JLC charges extra below 0.25 mm)", f"{min(holes) / 1e6:.2f} mm")
    # The planes and pours must span the whole outline: when the board grew 85 -> 87 mm
    # (2026-10-22) the zones kept their old outlines and the last 2 mm had no planes,
    # which DRC cannot see as long as nothing there needs them.
    edge = board.GetBoardEdgesBoundingBox()
    short = []
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        zb = z.GetBoundingBox()
        gap = max(zb.GetX() - edge.GetX(), zb.GetY() - edge.GetY(), edge.GetRight() - zb.GetRight(),
                  edge.GetBottom() - zb.GetBottom()) / 1e6
        if gap > 0.1:
            short.append(f"{z.GetNetname()} on {'/'.join(board.GetLayerName(l) for l in z.GetLayerSet().Seq())} "
                         f"stops {gap:.2f} mm short")
    report(not short, "copper zones span the board outline", "; ".join(short) or
           f"every plane and pour reaches the {(edge.GetWidth()) / 1e6:.1f} x {(edge.GetHeight()) / 1e6:.1f} mm outline")

    # ---- PCBWay set (production/build_pcbway.py)
    pw = os.path.join(order, "pcbway")
    bom_refs = sorted(r for ln in csv.DictReader(open(os.path.join(pw, "wallet_bom-pcbway.csv")))
                      for r in ln["Reference Designator"].split(","))
    jlc_refs = sorted(r for ln in csv.DictReader(open(os.path.join(order, "wallet_bom-jlc.csv")))
                      for r in ln["Designator"].split(","))
    cen_refs = sorted(r["Designator"] for r in csv.DictReader(open(os.path.join(pw, "wallet_centroid-pcbway.csv"))))
    no_mpn = [ln["Reference Designator"] for ln in csv.DictReader(open(os.path.join(pw, "wallet_bom-pcbway.csv")))
              if not ln["Manufacturers Name"] or not ln["Manufacturers Part Number"]]
    report(bom_refs == jlc_refs == cen_refs and not no_mpn, "PCBWay BOM / centroid: same parts as the JLC set, every line has manufacturer + MPN",
           f"{len(bom_refs)} refs" + (f"; no MPN: {no_mpn}" if no_mpn else ""))
    pz = zipfile.ZipFile(os.path.join(pw, "wallet_pcbway_fab.zip"))
    pnames = set(pz.namelist())
    want_pw = {f for f in os.listdir(os.path.join(order, "jlc-plain/gerbers")) if "drl_map" not in f and not f.endswith(".gbrjob")}
    want_pw |= {"wallet_bottom_UV_print_1200dpi.png", "wallet_UV_positioning.png"}
    from PIL import Image
    with pz.open("wallet_bottom_UV_print_1200dpi.png") as fh:
        im = Image.open(fh)
        size, mode = im.size, im.mode
    sys.path.insert(0, os.path.join(HERE, "production"))
    from colour_silk import outline_bounds
    x0, y0, x1, y1 = outline_bounds(board)
    exp = (round((x1 - x0) / 25.4 * 1200), round((y1 - y0) / 25.4 * 1200))
    report(want_pw <= pnames and mode == "RGBA" and all(abs(a - b) <= 3 for a, b in zip(size, exp)),
           "PCBWay fab zip: Gerbers + drills + print image (outline-sized, 1200 DPI, transparent = no ink) + positioning image",
           f"{len(pnames)} files, print {size[0]}x{size[1]} px {mode} vs outline {exp[0]}x{exp[1]}"
           + (f"; missing {sorted(want_pw - pnames)}" if want_pw - pnames else ""))
    eol = [f"{code} {b['mpn']}: {b['eol']}" for code, b in json.load(open(os.path.join(pw, "brands.json"))).items() if b["eol"]]
    if eol:
        report(None, "sourcing: JLC/LCSC flags as not buyable", "; ".join(eol))

    # ---- colour print (only when make_order.sh built it, COLOUR=1)
    if not os.path.isdir(os.path.join(order, "jlc-colour")):
        report(None, "colour print", "not built (COLOUR=1); JLC rejects non-EasyEDA colour files")
        return finish(order)
    sys.path.insert(0, os.path.join(HERE, "production"))
    import colour_silk as cs
    polys, bounds = cs.mask_polygons(board), cs.outline_bounds(board)
    cov, extra = cs.check_against_kicad(BOARD, polys, bounds, holes=cs.drill_holes(board))
    report(cov >= cs.MATCH_MIN and extra <= 1 - cs.MATCH_MIN, "colour print: openings vs KiCad's B.Mask plot (no ink on pads)",
           f"{len(polys)} openings, cover {cov:.1%}, {extra:.1%} beyond")
    stage, jlc = os.path.join(order, "colour-src/stage"), os.path.join(order, "jlc-colour")
    pairs = {"F_Cu": "Gerber_TopLayer.GTL", "B_Cu": "Gerber_BottomLayer.GBL", "In1_Cu": "Gerber_InnerLayer1.G1",
             "In2_Cu": "Gerber_InnerLayer2.G2", "F_Mask": "Gerber_TopSolderMaskLayer.GTS",
             "B_Mask": "Gerber_BottomSolderMaskLayer.GBS", "F_Silkscreen": "Gerber_TopSilkscreenLayer.GTO",
             "B_Silkscreen": "Gerber_BottomSilkscreenLayer.GBO", "F_Paste": "Gerber_TopPasteMaskLayer.GTP",
             "B_Paste": "Gerber_BottomPasteMaskLayer.GBP", "Edge_Cuts": "Gerber_BoardOutlineLayer.GKO"}
    diffs = []
    for layer, name in pairs.items():
        a = [l for l in open(os.path.join(stage, f"wallet-{layer}.gbr")).read().splitlines() if not l.startswith("G04")]
        b = [re.sub(r"^G54D", "D", l) for l in open(os.path.join(jlc, name)).read().splitlines() if not l.startswith("G04")]
        added = [l for l in b if l not in set(a)]
        ap_num = lambda l: re.match(r"%ADD(\d+)", l).group(1)
        unused = [l for l in added if re.match(r"%ADD(\d+)", l) and ("D" + ap_num(l) + "*") not in b]
        if len(a) != len(b) - len(added) or set(added) - set(unused):
            diffs.append(name)
    report(not diffs, "EasyEDA-form Gerbers draw exactly what KiCad plotted",
           "only the EasyEDA header, G54 prefixes and an unused aperture differ" if not diffs else f"differ: {diffs}")
    plain_b = open(os.path.join(order, "jlc-plain/gerbers/wallet-B_Silkscreen.gbo")).read().count("\n")
    colour_b = open(os.path.join(stage, "wallet-B_Silkscreen.gbr")).read().count("\n")
    report(colour_b < plain_b, "colour set: labels the art replaces left out of the plain silk", f"{plain_b} -> {colour_b} lines")
    z = zipfile.ZipFile(os.path.join(jlc, "wallet-colour-jlc.zip"))
    names = set(z.namelist())
    want = set(pairs.values()) | {"Drill_PTH_Through.DRL", "Drill_NPTH_Through.DRL", "Fabrication_ColorfulBottomSilkscreen.FCBS",
                                  "Fabrication_ColorfulBoardOutlineLayer.FCBO", "Fabrication_ColorfulBoardOutlineMark.FCBM"}
    missing = sorted(want - names)
    fcbs = z.getinfo("Fabrication_ColorfulBottomSilkscreen.FCBS").file_size if "Fabrication_ColorfulBottomSilkscreen.FCBS" in names else 0
    report(not missing and fcbs > 512, "colour zip contents", f"{len(names)} files, colour file {fcbs / 1e6:.1f} MB" +
           (f"; missing {missing}" if missing else ""))
    report(None, "colour order options", "white solder mask, ENIG, Silkscreen Technology = EasyEDA multi-color silkscreen, panel rails")
    finish(order)


def finish(order):
    text = "Ambire Ice pre-fab checks\n" + "\n".join(lines) + f"\n\n{'FAILED: ' + ', '.join(failed) if failed else 'all checks passed'}\n"
    open(os.path.join(order, "PREFAB-CHECKS.txt"), "w").write(text)
    print(text)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
