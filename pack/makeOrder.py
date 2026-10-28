#!/usr/bin/env python3
"""JLC order files for the pack board, into production/pack-order/:

    KICAD_PY pack/makeOrder.py        (from hardware/ambire-hw; refill zones and save first: it plots as saved)

  pack_gerbers.zip          Gerbers (2 Cu, mask, paste, silk, Edge.Cuts) + Excellon drill
  pack_bom-jlc.csv          Comment, Designator, Footprint, LCSC Part #
  pack_positions-jlc.csv    Designator, Mid X, Mid Y, Layer, Rotation
  stock.txt                 JLC's own stock for each part, read live

The origin of the plots and the positions is the outline's bottom-left corner (as the
device board's export_fab.py does). Rotation: KiCad's angle, bottom parts as 180 - angle;
JLC's own reference for the clips and the connector differs part by part, so check every
part in JLC's placement preview before paying (README: order-time checks).
"""
import csv
import json
import os
import shutil
import subprocess
import zipfile

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "pack.kicad_pcb")
OUT = os.path.join(os.path.dirname(HERE), "production", "pack-order")
API = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
LAYERS = [(pcbnew.F_Cu, "F_Cu"), (pcbnew.B_Cu, "B_Cu"), (pcbnew.F_Mask, "F_Mask"), (pcbnew.B_Mask, "B_Mask"),
          (pcbnew.F_Paste, "F_Paste"), (pcbnew.B_Paste, "B_Paste"), (pcbnew.F_SilkS, "F_Silkscreen"),
          (pcbnew.B_SilkS, "B_Silkscreen"), (pcbnew.Edge_Cuts, "Edge_Cuts")]


def jlc_stock(code):
    # curl, not urllib: KiCad's bundled Python ships with no CA bundle
    out = subprocess.run(["curl", "-s", "--max-time", "25", API, "-H", "content-type: application/json",
                          "-H", "user-agent: Mozilla/5.0", "--data",
                          json.dumps({"keyword": code, "currentPage": 1, "pageSize": 5})],
                         capture_output=True, text=True, check=True).stdout
    for c in ((json.loads(out).get("data") or {}).get("componentPageInfo") or {}).get("list") or []:
        if c.get("componentCode") == code:
            return c.get("stockCount") or 0, c.get("componentLibraryType"), c.get("componentModelEn")
    return None, None, None


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    gerb = os.path.join(OUT, "gerbers")
    os.makedirs(gerb)
    board = pcbnew.LoadBoard(BOARD)
    xs, ys = [], []
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            bb = d.GetBoundingBox()
            w = d.GetWidth() / 2e6        # the outline itself, not its stroke-widened box
            xs += [bb.GetLeft() / 1e6 + w, bb.GetRight() / 1e6 - w]
            ys += [bb.GetTop() / 1e6 + w, bb.GetBottom() / 1e6 - w]
    ox, oy = round(min(xs), 3), round(max(ys), 3)
    origin = pcbnew.VECTOR2I(pcbnew.FromMM(ox), pcbnew.FromMM(oy))
    board.GetDesignSettings().SetAuxOrigin(origin)   # in memory only: the board file is not saved

    ctl = pcbnew.PLOT_CONTROLLER(board)
    po = ctl.GetPlotOptions()
    po.SetOutputDirectory(gerb)
    po.SetUseAuxOrigin(True)
    po.SetPlotFrameRef(False)
    po.SetUseGerberProtelExtensions(True)
    po.SetSubtractMaskFromSilk(True)
    po.SetUseGerberX2format(True)
    po.SetDrillMarksType(pcbnew.DRILL_MARKS_NO_DRILL_SHAPE)
    for lid, name in LAYERS:
        ctl.SetLayer(lid)
        ctl.OpenPlotfile(name, pcbnew.PLOT_FORMAT_GERBER, name)
        ctl.PlotLayer()
    ctl.ClosePlot()
    dw = pcbnew.EXCELLON_WRITER(board)
    dw.SetOptions(False, False, origin, False)
    dw.SetFormat(True)
    dw.CreateDrillandMapFilesSet(gerb, True, False)
    files = sorted(f for f in os.listdir(gerb) if not f.endswith(".gbrjob"))
    with zipfile.ZipFile(os.path.join(OUT, "pack_gerbers.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(os.path.join(gerb, f), f)

    bom, cpl = {}, []
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        ref = fp.GetReference()
        lcsc = fp.GetFieldText("LCSC Part #") if fp.HasField("LCSC Part #") else ""
        if not lcsc:
            raise SystemExit(f"{ref} has no LCSC part number")
        bottom = fp.GetLayer() == pcbnew.B_Cu
        rot = fp.GetOrientationDegrees()
        x, y = fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6
        cpl.append({"Designator": ref, "Mid X": f"{x - ox:.4f}", "Mid Y": f"{oy - y:.4f}",
                    "Layer": "Bottom" if bottom else "Top", "Rotation": f"{((180.0 - rot) if bottom else rot) % 360:.1f}"})
        key = (fp.GetValue(), str(fp.GetFPID().GetLibItemName()), lcsc)
        bom.setdefault(key, []).append(ref)
    with open(os.path.join(OUT, "pack_bom-jlc.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (value, footprint, lcsc), refs in bom.items():
            w.writerow([value, ",".join(refs), footprint, lcsc])
    with open(os.path.join(OUT, "pack_positions-jlc.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cpl[0]))
        w.writeheader()
        w.writerows(cpl)

    lines = []
    for (value, footprint, lcsc), refs in bom.items():
        stock, kind, model = jlc_stock(lcsc)
        need = len(refs)
        flag = "MISSING at JLC" if stock is None else ("ZERO at JLC: source it (global sourcing / pre-order)" if stock == 0
                                                       else "low" if stock < 50 else "ok")
        lines.append(f"{lcsc:9} {model or '?':28} x{need}  JLC stock {stock}  {kind or ''}  {flag}  ({','.join(refs)})")
    open(os.path.join(OUT, "stock.txt"), "w").write("\n".join(lines) + "\n")
    print(f"origin ({ox}, {oy}); {len(files)} plot files; {len(cpl)} placements on "
          f"{sorted({r['Layer'] for r in cpl})}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
