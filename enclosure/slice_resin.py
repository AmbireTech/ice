#!/usr/bin/env python3
"""Slice the case's print layout for the Anycubic Photon Mono M7 Pro.

  enclosure/out/halves.stl  ->  enclosure/out/halves.pwsz   (print it from the Anycubic app or a USB stick)

The layout prints as generate.py lays it out: every part directly on the plate, no
supports, no raft, centred on the plate. The .pwsz is written the way Photon Workshop 4.2.0
writes one (checked against a file it sliced from the same STL, 2026-11-14):

  - each layer is the mesh's cross-section at the layer's middle, stored as outline
    segments in mm from the plate's centre (.pwszImg: the printer fills them, non-zero
    winding, solid outlines clockwise, holes anticlockwise; segments sorted by y, each
    stored low end first with a flag when that reverses it);
  - the print settings are Photon Workshop's own M7 Pro profile
    (enclosure/resin/anycubic_photon_mono_m7_pro.pwsp, copied from it), unchanged, with the
    chosen resin preset active; each layer's exposure and lift are that preset's (the
    bottom layers at the bottom exposure, then the normal one: Photon Workshop lists no
    transition layers per layer, the preset's transition count is left to the printer);
  - scene.slice and calc_layer_volumes.data carry the per-layer areas, bounds and volumes.

  python3 enclosure/slice_resin.py [--stl enclosure/out/halves.stl] [--preset Anycubic@abs_like_resin_pro2@ACF@normal_print]
                                   [--bottom-time S] [--bottom-layers N] [--exposure S] [--lift-speed MM_S]

--bottom-time / --bottom-layers change the preset's bottom exposure, --exposure every other
layer's, --lift-speed its lift
(every layer's peel off the film: a slower lift lets resin refill a pocket sealed against
the film instead of sucking on it); the changed preset goes
into the profile's user resins under the same name, as Photon Workshop stores a preset you
edit, and the factory one stays as it was.

Needs trimesh, numpy and opencv.
"""
import argparse
import io
import json
import struct
import zipfile
from pathlib import Path

import cv2
import numpy as np
import trimesh

HERE = Path(__file__).resolve().parent
SOFTWARE = "ambire-hw enclosure/slice_resin.py"
SEG = np.dtype([("sx", "<f4"), ("sy", "<f4"), ("ex", "<f4"), ("ey", "<f4"), ("rev", "u1")])
VOLUME_SLAB = 0.2          # calc_layer_volumes.data: mm per entry
RAW_TO_ML = 0.0011         # its raw volume (mm3) to the ml Photon Workshop reports (it adds 10 %)


def shoelace(r):
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def section_rings(mesh, z, centre):
    """The cross-section's closed outlines at height z, in mm from the plate's centre, each
    oriented for a non-zero fill: solid outlines clockwise, holes anticlockwise."""
    sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if sec is None:
        return []
    rings = []
    for d in sec.discrete:
        d = np.asarray(d)
        if np.abs(d[:, 2] - z).max() > 1e-6:
            raise SystemExit(f"section at z {z:.3f} left its plane")
        r = np.round(d[:, :2] - centre, 3)
        if np.allclose(r[0], r[-1]):
            r = r[:-1]
        if len(r) >= 3:
            rings.append(r)
    out = []
    for i, r in enumerate(rings):
        # nesting depth: a ring inside an even number of others is solid, else a hole
        p = r[0]
        depth = sum(1 for j, o in enumerate(rings) if j != i and cv2.pointPolygonTest(
            o.astype(np.float32).reshape(-1, 1, 2), (float(p[0]), float(p[1])), False) > 0)
        ccw = shoelace(r) > 0
        if ccw == (depth % 2 == 0):      # solid must run clockwise, a hole anticlockwise
            r = r[::-1]
        out.append(r)
    return out


def encode_layer(rings):
    """One .pwszImg: header, the outline segments, markers. Returns (bytes, area, bbox, count)."""
    if not rings:
        raise SystemExit("an empty layer: the layout has a gap in z")
    a = np.concatenate(rings)
    b = np.concatenate([np.roll(r, -1, axis=0) for r in rings])
    keep = ~np.all(a == b, axis=1)
    a, b = a[keep], b[keep]
    swap = a[:, 1] > b[:, 1]                       # store the low-y end first, flag the reversal
    s = np.where(swap[:, None], b, a)
    e = np.where(swap[:, None], a, b)
    order = np.argsort(s[:, 1], kind="stable")
    seg = np.zeros(len(a), SEG)
    seg["sx"], seg["sy"], seg["ex"], seg["ey"], seg["rev"] = s[order, 0], s[order, 1], e[order, 0], e[order, 1], swap[order]
    area = -sum(shoelace(r) for r in rings)        # solid clockwise: the signed sum is minus the area
    bbox = (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max()))
    head = b"{==\0" + struct.pack("<5f2I", 2 * area, *bbox, 0, len(rings)) + b"[--\0" + struct.pack("<3I", 0, len(seg), 1)
    return head + seg.tobytes() + b"--]\0==}\0", area, bbox, len(rings)


def scene_slice(layers, lh, model_bbox, z_max):
    out = bytearray(b"ANYCUBIC-PWSZ".ljust(16, b"\0") + SOFTWARE.encode()[:63].ljust(64, b"\0"))
    x0, y0, x1, y1 = model_bbox
    out += struct.pack("<4IfI6fI", 3, 3, 0, 0, 1.0, len(layers), x0, y0, 0.0, x1, y1, z_max, 0)
    out += bytes(256) + b"<---" + struct.pack("<I", len(layers))
    for i, (area, bbox, count) in enumerate(layers):
        out += struct.pack("<6fIf8I", (i + 0.5) * lh, area, *bbox, count, 0.0, *([0] * 8))
    return bytes(out + b"--->")


def layer_volumes(areas, lh):
    """calc_layer_volumes.data: the volume in VOLUME_SLAB slabs, the top half-slab left out."""
    per = max(1, round(VOLUME_SLAB / lh))
    n = max(1, len(areas) - max(1, round(VOLUME_SLAB / 2 / lh)))
    entries, z = [], 0.0
    for start in range(0, n, per):
        take = areas[start:min(start + per, n)]
        entries.append((max(0, start - 1), round(z, 4), round(len(take) * lh, 4), sum(take) * lh))
        z += len(take) * lh
    data = struct.pack("<IfII", 0x20251024, VOLUME_SLAB, 1, len(entries))
    for ref, z_, t, v in entries:
        data += struct.pack("<Iff8If", ref, z_, t, *([0] * 8), v)
    return data, sum(e[3] for e in entries)


def previews(mesh, sizes):
    """Flat-shaded views of the layout from above and the front, one PNG per size."""
    v = mesh.vertices - mesh.bounding_box.centroid
    p = v @ trimesh.transformations.euler_matrix(np.radians(-50), 0, np.radians(25))[:3, :3].T
    tri = p[mesh.faces]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
    shade = 0.35 + 0.65 * np.clip(nrm @ np.array([0.3, 0.4, 0.87]), 0, 1)
    order = [f for f in np.argsort(tri[:, :, 2].mean(1)) if nrm[f, 2] > 0]
    span = np.ptp(p[:, :2], axis=0)
    pngs = []
    for w, h in sizes:
        img = np.zeros((h, w, 4), np.uint8)
        img[:] = (246, 241, 238, 255)                 # BGRA: Photon Workshop's pale background
        k = 0.85 * min(w / span[0], h / span[1])
        xy = np.round(np.stack([w / 2 + p[:, 0] * k, h / 2 - p[:, 1] * k], 1) * 16).astype(np.int32)
        for f in order:
            g = int(200 * shade[f])
            cv2.fillConvexPoly(img, xy[mesh.faces[f]], (g, g, g, 255), cv2.LINE_AA, 4)
        ok, png = cv2.imencode(".png", img)
        if not ok:
            raise SystemExit("preview PNG encoding failed")
        pngs.append(png.tobytes())
    return pngs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stl", type=Path, default=HERE / "out/halves.stl")
    ap.add_argument("--profile", type=Path, default=HERE / "resin/anycubic_photon_mono_m7_pro.pwsp")
    ap.add_argument("--preset", default="Anycubic@abs_like_resin_pro2@ACF@normal_print",
                    help="a resin preset's name in the profile (resin@film@quality)")
    ap.add_argument("--bottom-time", type=float, default=None, help="bottom layers' exposure, s (default: the preset's)")
    ap.add_argument("--bottom-layers", type=int, default=None, help="how many bottom layers (default: the preset's)")
    ap.add_argument("--exposure", type=float, default=None, help="normal layers' exposure, s (default: the preset's)")
    ap.add_argument("--lift-speed", type=float, default=None, help="lift off the film, mm/s (default: the preset's)")
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
    overrides = {k: v for k, v in (("bott_time", args.bottom_time), ("bott_time_dual", args.bottom_time),
                                   ("bott_layers", args.bottom_layers), ("exposure_time", args.exposure),
                                   ("zup_speed", args.lift_speed)) if v is not None}
    if overrides:
        resin = json.loads(json.dumps(resin))
        resin["slicepara"].update(overrides)
        ext = profile["machine_extern"]
        ext["user_resins"] = [r for r in ext.get("user_resins", []) if r["property"]["name"] != args.preset] + [resin]
    sp = resin["slicepara"]
    lh = round(sp["zthick"], 4)

    mesh = trimesh.load(args.stl, force="mesh")
    if not mesh.is_watertight:
        raise SystemExit(f"{args.stl.name} is not watertight: its sections would not close")
    lo, hi = mesh.bounds
    if abs(lo[2]) > 1e-3:
        raise SystemExit(f"{args.stl.name} starts at z {lo[2]:.3f}, not on the plate (z 0)")
    centre = (lo[:2] + hi[:2]) / 2
    half = (hi[:2] - lo[:2]) / 2
    if half[0] > mt["print_xsize"] / 2 or half[1] > mt["print_ysize"] / 2:
        raise SystemExit(f"{2 * half[0]:.1f} x {2 * half[1]:.1f} mm does not fit the "
                         f"{mt['print_xsize']:.1f} x {mt['print_ysize']:.1f} mm plate")
    n = int(np.ceil(hi[2] / lh - 1e-6))

    images, layers = [], []
    for i in range(n):
        data, area, bbox, count = encode_layer(section_rings(mesh, (i + 0.5) * lh, centre))
        if area <= 0:
            raise SystemExit(f"layer {i} has no solid area: an outline's orientation went wrong")
        images.append(data)
        layers.append((area, bbox, count))
    areas = [a for a, _, _ in layers]
    vol_data, raw = layer_volumes(areas, lh)
    ml = raw * RAW_TO_ML
    density, price = resin["property"]["density"], resin["property"]["price"]
    lift = sp["zup_height"] / sp["zup_speed"] + sp["zup_height"] / sp["zdown_speed"]
    seconds = round(sum(sp["bott_time"] if i < sp["bott_layers"] else sp["exposure_time"] for i in range(n)) + n * lift)
    estimate = {"estimated_cost": ml / 1000 * price, "estimated_cost_currency": resin["property"]["currency"],
                "estimated_time": seconds, "estimated_volume": ml, "estimated_weight": ml * density, "model_layers_count": n}
    print_info = {"cost": estimate["estimated_cost"], "currency": estimate["estimated_cost_currency"], "model_layers_count": n,
                  "options_infos": None, "print_time": seconds, "sub_estimated_infos": [estimate], "volume": ml,
                  "weight": ml * density}
    controller = {"count": n, "paras": [{"exposure_time": sp["bott_time"] if i < sp["bott_layers"] else sp["exposure_time"],
                                          "layer_index": i, "layer_minheight": round(i * lh, 4), "layer_thickness": lh,
                                          "zup_height": sp["zup_height"], "zup_speed": sp["zup_speed"]} for i in range(n)]}
    lcd = {"models_processed_info": {"models": [{"auto_support": 0, "hollow": 0, "makeronline_sourceid": 0,
                                                 "manual_support": 0, "name": args.stl.name, "punch": 0, "source_from": 0}],
                                     "scene_models_from": 0, "slice_paras_process": 0, "software_version": SOFTWARE},
           "rerf_function": {"enable": False, "model_name": "", "model_type": 1, "partition_exposure_array": [],
                             "partition_num": 0}}
    software = {"mark": "ambire-hw", "opengl": "", "os": "", "version": SOFTWARE}
    model_bbox = (round(-half[0], 3), round(-half[1], 3), round(half[0], 3), round(half[1], 3))
    pngs = previews(mesh, [tuple(mt["prev_image_size"]), tuple(mt["prev2_image_size"]), (800, 600)])

    # the entries in Photon Workshop's order
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("anycubic_photon_resins.pwsp", json.dumps(profile, indent=4))
        z.writestr("print_info.json", json.dumps(print_info, indent=4))
        z.writestr("software_info.conf", json.dumps(software, indent=4))
        for k, png in enumerate(pngs):
            z.writestr(f"preview_images/preview_{k}.png", png)
        z.writestr("layers_controller.conf", json.dumps(controller, indent=4))
        z.writestr("lcd_function.json", json.dumps(lcd, indent=4))
        for i, data in enumerate(images):
            z.writestr(f"layer_images/layer_{i}.pwszImg", data)
        z.writestr("calc_layer_volumes.data", vol_data)
        z.writestr("scene.slice", scene_slice(layers, lh, model_bbox, round(float(hi[2]), 3)))
    out.write_bytes(buf.getvalue())
    print(f"{out}: {n} layers of {lh} mm, {2 * half[0]:.1f} x {2 * half[1]:.1f} mm centred on the plate, "
          f"largest layer {max(areas):.0f} mm2, {sum(areas) * lh / 1000:.2f} ml of part ({ml:.2f} ml estimated), "
          f"~{seconds / 60:.0f} min; {args.preset}, bottom {sp['bott_layers']} x {sp['bott_time']:g} s, "
          f"then {sp['exposure_time']:.1f} s; lift {sp['zup_height']:g} mm at {sp['zup_speed']:g} mm/s")


if __name__ == "__main__":
    main()
