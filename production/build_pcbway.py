#!/usr/bin/env python3
"""PCBWay order set: fab zip with the full-colour UV back print, turnkey BOM, centroid.

    KPY production/build_pcbway.py <order dir>

Run by make_order.sh after the plain JLC set (it reuses jlc-plain's BOM lines and
stock table) and after art/build_uv.py has rebuilt the print file. Writes <order>/pcbway/:

  wallet_pcbway_fab.zip   Gerbers + drills (back silk without the labels the art
                          replaces) + the print image + the positioning image, one
                          zip as PCBWay asks for UV printing
  wallet_bom-pcbway.csv   turnkey BOM: manufacturer + MPN from JLC's parts API,
                          the LCSC code as distributor part number
  wallet_centroid-pcbway.csv   assembled parts only, mm, KiCad rotation convention
  ORDER-NOTES.txt         the order form settings and what to tell PCBWay

PCBWay's UV print takes the image as is: ink where the PNG is opaque, white mask
where it is transparent. art/build_uv.py already keeps every pad, opening, drill
and the camera window clear (0.10 mm ring) and sizes the image to the outline.
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "production"))
import build_jlc_files as jlc  # noqa: E402

BOARD = os.path.join(HERE, "board/wallet.kicad_pcb")
PRINT = os.path.join(HERE, "art/out/uv_back_seen_from_behind.png")
FONT = os.path.join(HERE, "art/brand/Poppins-SemiBold.ttf")
KPY = sys.executable
BOARDS = 10


def export_gerbers(out):
    """Gerbers with the replaced back-silk labels left out (export_fab.py --colour)."""
    src = os.path.join(out, "src")
    r = subprocess.run([KPY, os.path.join(HERE, "production/export_fab.py"), BOARD, src,
                        os.path.join(HERE, "production/jlc_rotation_db.csv"), "--colour"],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"export_fab.py failed:\n{r.stdout}\n{r.stderr}")
    gerb = os.path.join(src, "gerbers")
    return [os.path.join(gerb, f) for f in sorted(os.listdir(gerb)) if "drl_map" not in f and not f.endswith(".gbrjob")], src


def positioning_image(path, print_img):
    """Back layers seen from behind (KiCad, mirrored) | the print over them, with the frame stated."""
    W, H = print_img.size
    with tempfile.TemporaryDirectory() as td:
        svg, png = os.path.join(td, "b.svg"), os.path.join(td, "b.png")
        subprocess.run(["kicad-cli", "pcb", "export", "svg", "--layers", "Edge.Cuts,B.Mask,B.Silkscreen,B.Fab",
                        "--mirror", "--mode-single", "--exclude-drawing-sheet", "--page-size-mode", "2",
                        "-o", svg, BOARD], check=True, capture_output=True)
        subprocess.run(["rsvg-convert", "-w", str(W), "-h", str(H), "-b", "white", "-o", png, svg], check=True)
        layers = Image.open(png).convert("RGB")
    over = layers.copy()
    over.paste(print_img.convert("RGB"), mask=print_img.split()[3].point(lambda a: int(a * 0.75)))
    s = 0.5
    l2, o2 = layers.resize((int(W * s), int(H * s))), over.resize((int(W * s), int(H * s)))
    m, head = 60, 330
    sheet = Image.new("RGB", (l2.width * 2 + 3 * m, l2.height + head + m), "white")
    sheet.paste(l2, (m, head))
    sheet.paste(o2, (2 * m + l2.width, head))
    d = ImageDraw.Draw(sheet)
    big, small = ImageFont.truetype(FONT, 46), ImageFont.truetype(FONT, 30)
    d.text((m, 30), "BOTTOM side, viewed from the bottom (as printed, not mirrored)", font=big, fill="black")
    d.text((m, 100), f"Print image = the board outline exactly: 48.00 x 85.00 mm, 1200 DPI ({W} x {H} px).", font=small, fill="black")
    d.text((m, 145), "Transparent pixels = no ink (white solder mask shows). Pads, holes and the camera window are kept clear.", font=small, fill="black")
    d.text((m, 190), "Top side: no colour print (normal white silkscreen only).", font=small, fill="black")
    d.text((m, head - 60), "Board bottom layers (outline, mask openings, parts)", font=small, fill="#444")
    d.text((2 * m + l2.width, head - 60), "Print image placed on them", font=small, fill="#444")
    for x0 in (m, 2 * m + l2.width):
        d.rectangle([x0 - 2, head - 2, x0 + l2.width + 1, head + l2.height + 1], outline="#d00000", width=3)
    sheet.save(path, dpi=(150, 150))


def bom_and_centroid(order, src, out):
    lines = list(csv.DictReader(open(os.path.join(order, "jlc-plain/wallet_bom-jlc.csv"))))
    stock = {r["LCSC"]: r for r in csv.DictReader(open(os.path.join(order, "jlc-plain/work/stock.csv")))}
    parts = {r["Designator"]: r for r in csv.DictReader(open(os.path.join(src, "work/parts-raw.csv")))}
    cache_path = os.path.join(out, "brands.json")
    brands = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    rows, refs_all, notes = [], [], []
    for i, ln in enumerate(lines, 1):
        code = ln["LCSC Part #"]
        if code not in brands:
            rec = jlc.lookup(code)
            if rec is None:
                raise SystemExit(f"JLC parts API has no record for {code} ({ln['Designator']})")
            brands[code] = {"brand": rec.get("componentBrandEn") or "", "mpn": rec.get("componentModelEn") or "",
                            "describe": rec.get("describe") or "", "eol": rec.get("noBuyReason") or ""}
        b = brands[code]
        refs = [r.strip() for r in ln["Designator"].split(",")]
        refs_all += refs
        smd = all(parts[r]["SMD"] == "1" for r in refs)
        if b["eol"]:
            notes.append(f"{','.join(refs)} {b['mpn']}: JLC/LCSC says \"{b['eol']}\"")
        rows.append({"Line#": i, "Quantity Per Board": len(refs), "Reference Designator": ",".join(refs),
                     "Value": ln["Comment"], "Manufacturers Name": b["brand"], "Manufacturers Part Number": b["mpn"],
                     "Part Description": b["describe"], "Package": stock.get(code, {}).get("Package", ln["Footprint"]),
                     "Type": "Surface mount" if smd else "Thru-hole", "Distributors Part Number": f"LCSC {code}"})
    json.dump(brands, open(cache_path, "w"), indent=1)
    with open(os.path.join(out, "wallet_bom-pcbway.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cen = []
    for ref in sorted(refs_all):
        p = parts[ref]
        if p["SMD"] != "1":
            continue
        cen.append({"Designator": ref, "Mid X (mm)": p["Mid X"], "Mid Y (mm)": p["Mid Y"],
                    "Rotation": f"{float(p['BoardAngle']) % 360:.1f}", "Layer": "Bottom" if p["Layer"] == "bottom" else "Top"})
    with open(os.path.join(out, "wallet_centroid-pcbway.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cen[0]))
        w.writeheader()
        w.writerows(cen)
    return rows, refs_all, cen, notes


NOTES = """Ambire signer proto 0 - PCBWay order notes
==========================================

PCB (upload wallet_pcbway_fab.zip)
  Layers 4 | 48.00 x 85.00 mm | thickness 1.0 mm | FR-4 | qty {boards}
  Surface finish: ENIG
  Solder mask: white (both sides) | silkscreen: white
  Special: full-colour UV printing on the BOTTOM side only (matte, the default)
  Min drill 0.25 mm, min track/space per the Gerbers
  Layer order: top wallet-F_Cu.gtl / wallet-In1_Cu.g1 (3.3 V plane) / wallet-In2_Cu.g2 (GND plane) /
               bottom wallet-B_Cu.gbl; outline wallet-Edge_Cuts.gm1

Tell PCBWay in the order remark
  "Full-colour UV print on the bottom side from wallet_bottom_UV_print_1200dpi.png
   (in the zip). The image covers the board outline exactly (48 x 85 mm), viewed
   from the bottom, not mirrored; see wallet_UV_positioning.png. Transparent =
   no ink. Print the bottom white silkscreen Gerber as usual; the image already
   leaves pads and holes clear. Top side: normal white silkscreen, no UV print."

Assembly (turnkey, bottom side only, {parts} placements, {lines} BOM lines)
  BOM:      wallet_bom-pcbway.csv  (manufacturer + MPN; LCSC code as distributor PN)
  Centroid: wallet_centroid-pcbway.csv  (mm, origin = outline bottom-left, as the
            Gerbers; Layer = Bottom; rotation in KiCad's convention, parts on the
            bottom given as seen from the top; please check orientation against
            the Gerbers / pin-1 marks)
  Not assembled (fitted by hand): {handfit}
{eol}"""


def main():
    order = os.path.abspath(sys.argv[1])
    out = os.path.join(order, "pcbway")
    os.makedirs(out, exist_ok=True)
    gerbers, src = export_gerbers(out)
    img = Image.open(PRINT).convert("RGBA")
    pr = os.path.join(out, "wallet_bottom_UV_print_1200dpi.png")
    img.save(pr, dpi=(1200, 1200))
    pos = os.path.join(out, "wallet_UV_positioning.png")
    positioning_image(pos, img)
    rows, refs, cen, eol = bom_and_centroid(order, src, out)
    z = os.path.join(out, "wallet_pcbway_fab.zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in gerbers + [pr, pos]:
            zf.write(f, os.path.basename(f))
    eol_txt = ("\nSourcing warning\n" + "\n".join("  " + n for n in eol) + "\n  Confirm availability with PCBWay or "
               "DNP these and fit later.\n") if eol else ""
    open(os.path.join(out, "ORDER-NOTES.txt"), "w").write(NOTES.format(
        boards=BOARDS, parts=len(cen), lines=len(rows), eol=eol_txt,
        handfit="; ".join(f"{a} ({b})" for a, b, _ in jlc.HAND_FIT)))
    shutil.rmtree(src)
    print(f"pcbway: {len(gerbers)} fab files + print + positioning -> {z}; BOM {len(rows)} lines / {len(refs)} refs; "
          f"centroid {len(cen)} rows" + (f"; sourcing warnings: {len(eol)}" if eol else ""))


if __name__ == "__main__":
    main()
