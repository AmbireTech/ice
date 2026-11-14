#!/usr/bin/env python3
"""Slice the case's print layout for the Anycubic Photon Mono M7 Pro.

  enclosure/out/case.stl  ->  enclosure/out/case.pwsz   (send it from the Anycubic app or a USB stick)

The layout is printed as generate.py lays it out: every part directly on the plate, no
supports, no raft. Each layer is the mesh's cross-section at the layer's middle, filled
into the printer's 13312 x 5120 LCD (16.8 x 24.8 um pixels) and centred on it. UVtools
(https://github.com/sn4k3/UVtools, UVTOOLS_CMD=/path/to/UVtoolsCmd) packs the layers into
Anycubic's .pwsz. The print settings are Anycubic's own: Photon Workshop's M7 Pro profile
(enclosure/resin/anycubic_photon_mono_m7_pro.pwsp, copied from Photon Workshop 4.2.0) goes
into the file unchanged, as Photon Workshop puts it there, with the chosen resin preset
active; the layers' exposures and lifts are that preset's.

Run with a Python that has trimesh, numpy and opencv:
  python3 enclosure/slice_resin.py [--stl enclosure/out/case.stl] [--preset Anycubic@abs_like_resin_pro2@ACF@normal_print]
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import cv2
import numpy as np
import trimesh

HERE = Path(__file__).resolve().parent
UVTOOLS = os.environ.get("UVTOOLS_CMD", "UVtoolsCmd")
SHIFT = 4   # cv2 fixed-point bits: polygon vertices to 1/16 pixel
PROFILE_ENTRY = "anycubic_photon_resins.pwsp"   # the machine and resin settings inside a .pwsz


def rings(mesh: trimesh.Trimesh, z: float) -> list[np.ndarray]:
    """Every closed outline of the mesh's cross-section at height z, in world XY (mm)."""
    sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if sec is None:
        return []
    out = [np.asarray(d)[:, :2] for d in sec.discrete if len(d) >= 3]
    if any(np.abs(np.asarray(d)[:, 2] - z).max() > 1e-6 for d in sec.discrete):
        raise SystemExit(f"section at z {z:.3f} is not in the plane z = {z:.3f}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stl", type=Path, default=HERE / "out/case.stl")
    ap.add_argument("--profile", type=Path, default=HERE / "resin/anycubic_photon_mono_m7_pro.pwsp")
    ap.add_argument("--preset", default="Anycubic@abs_like_resin_pro2@ACF@normal_print",
                    help="a resin preset's name in the profile (resin@film@quality)")
    ap.add_argument("--out", type=Path, default=None, help="default: the STL's path with .pwsz")
    args = ap.parse_args()
    out = args.out or args.stl.with_suffix(".pwsz")

    profile = json.loads(args.profile.read_text())
    mt = profile["machine_type"]
    found = [r for r in profile["machine_extern"]["factory_resins"] + profile["machine_extern"].get("user_resins", [])
             if r["property"]["name"] == args.preset]
    if len(found) != 1:
        raise SystemExit(f"{args.profile.name}: {len(found)} presets named {args.preset}")
    resin = found[0]
    profile["machine_extern"]["active_resins"] = [args.preset]
    sp, ext = resin["slicepara"], resin["slice_extpara"]
    W, H = mt["res_x"], mt["res_y"]
    px, py = mt["xy_pixel"] / 1000, mt["xy_pixel_y"] / 1000          # mm per pixel
    lh = round(sp["zthick"], 4)

    mesh = trimesh.load(args.stl, force="mesh")
    if not mesh.is_watertight:
        raise SystemExit(f"{args.stl.name} is not watertight: its sections would not close")
    lo, hi = mesh.bounds
    if abs(lo[2]) > 1e-3:
        raise SystemExit(f"{args.stl.name} starts at z {lo[2]:.3f}, not on the plate (z 0)")
    size = hi[:2] - lo[:2]
    if size[0] > W * px or size[1] > H * py:
        raise SystemExit(f"{size[0]:.1f} x {size[1]:.1f} mm does not fit the {W * px:.1f} x {H * py:.1f} mm LCD")
    cx, cy = (lo[:2] + hi[:2]) / 2
    n = int(np.ceil(hi[2] / lh - 1e-6))

    def to_px(r):
        """World XY (mm) -> LCD pixels, centred; the image's top row is the layout's +Y edge."""
        col = W / 2 + (r[:, 0] - cx) / px
        row = H / 2 - (r[:, 1] - cy) / py
        return np.round(np.stack([col, row], 1) * (1 << SHIFT)).astype(np.int32)

    tmp = Path(tempfile.mkdtemp(prefix="slice_resin_"))
    uvj = tmp / "layers.uvj"
    area_mm2 = []
    with zipfile.ZipFile(uvj, "w", zipfile.ZIP_STORED) as z:
        for i in range(n):
            zc = (i + 0.5) * lh
            img = np.zeros((H, W), np.uint8)
            rs = rings(mesh, zc)
            if rs:
                # all outlines together, even-odd: holes and islands in holes come out right
                cv2.fillPoly(img, [to_px(r) for r in rs], 255, lineType=cv2.LINE_8, shift=SHIFT)
            area_mm2.append(int(cv2.countNonZero(img)) * px * py)
            ok, png = cv2.imencode(".png", img)
            if not ok:
                raise SystemExit(f"layer {i}: PNG encoding failed")
            z.writestr(f"slice/{i:08d}.png", png.tobytes())
        bottom, normal = {}, {}
        for d, t in ((bottom, sp["bott_time"]), (normal, sp["exposure_time"])):
            d.update(LightOnTime=round(t, 3), LiftHeight=sp["zup_height"], LiftSpeed=sp["zup_speed"] * 60,
                     RetractSpeed=sp["zdown_speed"] * 60, LightOffTime=0.0)
        bottom["Count"] = sp["bott_layers"]
        config = {"Properties": {"Size": {"X": W, "Y": H, "Millimeter": {"X": round(W * px, 4), "Y": round(H * py, 4)},
                                          "Layers": n, "LayerHeight": lh},
                                 "Exposure": normal, "Bottom": bottom, "AntiAliasLevel": sp["anti_count"]},
                  "Layers": [{"Z": round((i + 1) * lh, 4), "Exposure": {k: v for k, v in (bottom if i < sp["bott_layers"] else normal).items() if k != "Count"}}
                             for i in range(n)]}
        z.writestr("config.json", json.dumps(config, indent=1))

    pwsz = tmp / "layers.pwsz"
    for cmd in ([UVTOOLS, "convert", str(uvj), "pwsz", str(pwsz)],
                [UVTOOLS, "set-properties", str(pwsz), f"MachineName={mt['name']}",
                 f"TransitionLayerCount={ext['transition_layercount']}"]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"{' '.join(cmd[:2])} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    # UVtools writes its own generic resin settings; put Photon Workshop's profile in their place
    with zipfile.ZipFile(pwsz) as src, zipfile.ZipFile(out, "w") as dst:
        names = src.namelist()
        if PROFILE_ENTRY not in names:
            raise SystemExit(f"UVtools' .pwsz has no {PROFILE_ENTRY}: the format changed, check before printing")
        for info in src.infolist():
            data = (json.dumps(profile, indent=4) if info.filename == PROFILE_ENTRY else src.read(info))
            dst.writestr(info, data)
    shutil.rmtree(tmp)
    print(f"{out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}: {n} layers of {lh} mm, "
          f"{size[0]:.1f} x {size[1]:.1f} mm centred on the plate, largest layer {max(area_mm2):.0f} mm2; "
          f"{resin['property']['name']}")


if __name__ == "__main__":
    main()
