#!/usr/bin/env python3
"""Ambire Ice JLC fab export, modelled on hardware/carrier/production/{plot_fab,export_fab_inputs}.py.

One LoadBoard, one process:
  * gerbers (4 Cu, mask, paste, silk, Edge.Cuts) + Excellon PTH/NPTH + drill maps -> gerbers/ and the zip
  * placement rows + raw BOM rows -> work/positions-raw.csv, work/bom-raw.csv, work/export-stamp.json

Conventions are the GridCore exporter's (= Fabrication Toolkit's):
  position = anchor for SMD-attributed footprints, pad-bbox centre otherwise
  rotation = bottom: (180 - board angle), then + jlc_rotation_db.csv offset (first regex match)
Plotted AS SAVED: no zone refill, drill marks off.

Origin: the board's aux origin is (0,0) = the outline's top-left corner, which puts every Y
negative. Here the aux origin is moved IN MEMORY ONLY to the outline's bottom-left corner, and
gerbers, drill and CPL all use that same point. The board file is never saved.

    KPY export_fab.py <board.kicad_pcb> <outdir> <rotation_db.csv> [--colour]

--colour: for a colour-print (UV) order. The back silkscreen the UV print replaces,
the board-level labels (art/build_uv.py RESTYLED_LABELS) and the white flame (group
FLAME), is left out of the plot, in memory only, so it doesn't print twice.
"""
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile

import pcbnew

PASSIVE_SIZE = re.compile(r"^(\w*_SMD:)?\w{1,4}_(\d+)_\d+Metric.*$")

LAYERS = [
    (pcbnew.F_Cu, "F_Cu"), (pcbnew.In1_Cu, "In1_Cu"),
    (pcbnew.In2_Cu, "In2_Cu"), (pcbnew.B_Cu, "B_Cu"),
    (pcbnew.F_Paste, "F_Paste"), (pcbnew.B_Paste, "B_Paste"),
    (pcbnew.F_SilkS, "F_Silkscreen"), (pcbnew.B_SilkS, "B_Silkscreen"),
    (pcbnew.F_Mask, "F_Mask"), (pcbnew.B_Mask, "B_Mask"),
    (pcbnew.Edge_Cuts, "Edge_Cuts"),
]


def load_rotation_db(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [(row["Regex To Match"], float(row["Rotation"])) for row in csv.DictReader(fh)]


def db_match(db, fp_name):
    short = fp_name.split(":")[-1]
    for pattern, rot in db:
        if re.search(pattern, fp_name if ":" in pattern else short):
            return pattern, rot
    return None, 0.0


def pad_bbox_centre(pads):
    bb = None
    for pad in pads:
        pb = pad.GetBoundingBox()
        if bb is None:
            bb = pcbnew.BOX2I(pb.GetPosition(), pb.GetSize())
        else:
            bb.Merge(pb)
    c = bb.GetCenter()
    return c.x / 1e6, c.y / 1e6


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    colour = "--colour" in sys.argv
    board_path, outdir, db_path = args[0], args[1], args[2]
    outdir = os.path.abspath(outdir)   # KiCad resolves a relative plot directory against the board file, not the cwd
    gerb_dir = os.path.join(outdir, "gerbers")
    work = os.path.join(outdir, "work")
    shutil.rmtree(gerb_dir, ignore_errors=True)
    os.makedirs(gerb_dir)
    os.makedirs(work, exist_ok=True)

    board = pcbnew.LoadBoard(board_path)
    if colour:
        replaced = ("AIRGAP", "RADIO", "SECURE ELEMENT", "QR CAMERA", "ANT")   # = art/build_uv.py RESTYLED_LABELS
        hidden = [d for d in board.GetDrawings()
                  if d.GetClass() == "PCB_TEXT" and d.GetLayer() == pcbnew.B_SilkS and d.GetText() in replaced]
        for d in hidden:
            board.Remove(d)          # the plotter ignores a board text's hidden flag; the file is never saved
        flame = [i for g in board.Groups() if g.GetName() == "FLAME" for i in g.GetItems()]
        for i in flame:
            board.Remove(i)
        print(f"colour print: {len(hidden)} back silk texts left out ({', '.join(sorted(d.GetText() for d in hidden))}), "
              f"{len(flame)} flame polygons left out")
    ds = board.GetDesignSettings()
    old_aux = pcbnew.VECTOR2I(ds.GetAuxOrigin())

    # outline extents from the Edge.Cuts geometry itself (not the stroke-widened bbox)
    xs, ys = [], []
    for d in board.GetDrawings():
        if d.GetLayer() != pcbnew.Edge_Cuts:
            continue
        sb = d.GetBoundingBox()
        w = d.GetWidth() if hasattr(d, "GetWidth") else 0
        xs += [(sb.GetLeft() + w / 2) / 1e6, (sb.GetRight() - w / 2) / 1e6]
        ys += [(sb.GetTop() + w / 2) / 1e6, (sb.GetBottom() - w / 2) / 1e6]
    ox, oy = min(xs), max(ys)
    origin = pcbnew.VECTOR2I(pcbnew.FromMM(ox), pcbnew.FromMM(oy))
    ds.SetAuxOrigin(origin)
    print(f"aux origin in file ({old_aux.x/1e6:.3f}, {old_aux.y/1e6:.3f}); "
          f"plotting/CPL origin set in memory to outline bottom-left ({ox:.3f}, {oy:.3f}); "
          f"outline {max(xs)-min(xs):.3f} x {max(ys)-min(ys):.3f} mm; copper layers {board.GetCopperLayerCount()}")

    # ---- gerbers
    ctl = pcbnew.PLOT_CONTROLLER(board)
    po = ctl.GetPlotOptions()
    po.SetOutputDirectory(gerb_dir)
    po.SetUseAuxOrigin(True)
    po.SetPlotFrameRef(False)
    po.SetUseGerberProtelExtensions(True)
    po.SetSubtractMaskFromSilk(True)
    po.SetUseGerberX2format(True)
    po.SetIncludeGerberNetlistInfo(True)
    po.SetDrillMarksType(pcbnew.DRILL_MARKS_NO_DRILL_SHAPE)
    for lid, name in LAYERS:
        ctl.SetLayer(lid)
        ctl.OpenPlotfile(name, pcbnew.PLOT_FORMAT_GERBER, name)
        ctl.PlotLayer()
    ctl.ClosePlot()

    dw = pcbnew.EXCELLON_WRITER(board)
    dw.SetOptions(False, False, origin, False)
    dw.SetFormat(True)
    dw.SetMapFileFormat(pcbnew.PLOT_FORMAT_GERBER)
    dw.CreateDrillandMapFilesSet(gerb_dir, True, True)

    zip_path = os.path.join(outdir, "wallet_gerbers.zip")
    files = sorted(f for f in os.listdir(gerb_dir) if not f.endswith(".gbrjob"))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(os.path.join(gerb_dir, f), f)
    print(f"{len(files)} files -> {zip_path}")

    # ---- positions + raw BOM
    db = load_rotation_db(db_path)
    rows = []
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference().upper()):
        ref = fp.GetReference().upper()
        fp_name = str(fp.GetFPID().GetUniStringLibId())
        short = str(fp.GetFPID().GetLibItemName())
        flags = fp.GetAttributes()
        fields = {f.GetName(): f.GetText().strip() for f in fp.GetFields()}
        layer = "bottom" if fp.GetLayer() == pcbnew.B_Cu else "top"
        rot = fp.GetOrientationDegrees()
        pads = list(fp.Pads())
        anchor = (fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6)
        centre = pad_bbox_centre(pads) if pads else anchor
        origin_kind = "Anchor" if flags & pcbnew.FP_SMD else "Center"
        x, y = anchor if origin_kind == "Anchor" else centre
        pattern, db_rot = db_match(db, fp_name)
        base = (180.0 - rot) if layer == "bottom" else rot
        cpl_rot = (base + db_rot) % 360.0
        lcsc = next((fields[k] for k in ("LCSC Part #", "LCSC", "JLCPCB Part #", "JLC") if fields.get(k)), "")
        rows.append({
            "Designator": ref,
            "Value": fp.GetValue(),
            "FootprintFull": fp_name,
            "Footprint": PASSIVE_SIZE.sub(r"\2", short),
            "LCSC": lcsc,
            "Layer": layer,
            "BoardAngle": f"{rot:.1f}",
            "DbRule": pattern or "",
            "DbOffset": f"{db_rot:.1f}",
            "Rotation": f"{cpl_rot:.1f}",
            "Mid X": f"{x - ox:.4f}",
            "Mid Y": f"{oy - y:.4f}",
            "Origin": origin_kind,
            "AnchorToPadCentreMm": f"{((anchor[0]-centre[0])**2 + (anchor[1]-centre[1])**2) ** 0.5:.3f}",
            "Pads": str(len(pads)),
            "SMD": "1" if flags & pcbnew.FP_SMD else "0",
            "THT": "1" if flags & pcbnew.FP_THROUGH_HOLE else "0",
            "ExcludeBOM": "1" if flags & pcbnew.FP_EXCLUDE_FROM_BOM else "0",
            "ExcludePos": "1" if flags & pcbnew.FP_EXCLUDE_FROM_POS_FILES else "0",
            "BoardOnly": "1" if flags & pcbnew.FP_BOARD_ONLY else "0",
            "DNP": "1" if fp.IsDNP() else "0",
        })
    with open(os.path.join(work, "parts-raw.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    stamp = {
        "board": board_path,
        "board_sha256": hashlib.sha256(open(board_path, "rb").read()).hexdigest(),
        "source_commit": "49053dc (hardware/ambire-wallet repo)",
        "origin_mm_board_frame": [ox, oy],
        "kicad": pcbnew.GetBuildVersion(),
        "footprints": len(rows),
    }
    with open(os.path.join(work, "export-stamp.json"), "w") as fh:
        json.dump(stamp, fh, indent=1)
    print(f"{len(rows)} footprints -> {work}/parts-raw.csv")


if __name__ == "__main__":
    main()
