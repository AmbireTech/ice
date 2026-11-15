#!/usr/bin/env python3
"""Lay the case's halves out face up on supports, for a clean, glossy outer face.

  enclosure/out/shell.stl, bezel.stl  ->  enclosure/out/halves_supported.ply  (+ .pwsz via slice_resin.py; PLY keeps the union's float64 vertices, which an STL's float32 would collapse into non-manifold slivers)

Each half prints with its outer face up (last), its open inside toward the plate: every
support lands on a surface that faces the plate, which in this orientation is the inside
(the rim, the inner face, pocket ceilings, the screen's seat). Nothing touches the outer
face or the outer walls (they lean in toward the face: they face up).

Supports: a raft (its edge chamfered, to get a scraper under), a pillar per contact
(a cone foot on the raft, a 1.0 mm body, a cone tip to a 0.35 mm contact). Contacts are
placed where a layer has an island or reaches more than REACH past the layer before,
on the surface seen from the plate there, greedily until each pixel of it is within COVER of a contact on the same surface; a
pillar is placed only where its body and tip clear the part all the way up from the raft.
The report lists the contacts by the assembled height they land at, so a contact on a
fit surface (the tongue, the board's seat, the screen's seat) is named, not hidden.

  python3 enclosure/support_resin.py [--cover 2.0] [--lift 4.0]

Needs trimesh, numpy, opencv, shapely and manifold3d.
"""
import argparse
import json
from pathlib import Path

import cv2
import manifold3d as mf
import numpy as np
import shapely.geometry as sg
import trimesh

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
PX = 0.1                 # mm per pixel of the height map
LH = 0.05                # layer height (the M7 Pro preset's)
REACH = 1.0              # an overhang this far past the layer before hangs on its own
RAFT_T, RAFT_GROW = 0.8, 2.0
BASE_R, BASE_H = 0.97, 0.6   # radii off the 0.1 mm grid: two pillars never sit exactly tangent (a non-manifold edge)
BODY_R = 0.49
TIP_R, TIP_L, TIP_IN = 0.175, 1.2, 0.1
BODY_CLEAR = 0.6         # the body's own radius plus a margin: the part must stay above the tip's start there
GAP_XY = 8.0             # between the two rafts
SEGMENTS = 16


def zbuffer(mesh, x0, y0, nx, ny):
    """The lowest surface over each pixel (z) and that surface's normal z, seen from below."""
    zmin = np.full((ny, nx), np.inf)
    nzmap = np.zeros((ny, nx))
    tri = mesh.triangles
    nrm = mesh.face_normals[:, 2]
    for t, nz in zip(tri, nrm):
        if abs(nz) < 1e-6:
            continue
        px = (t[:, 0] - x0) / PX - 0.5
        py = (t[:, 1] - y0) / PX - 0.5
        i0, i1 = max(int(np.floor(px.min())), 0), min(int(np.ceil(px.max())), nx - 1)
        j0, j1 = max(int(np.floor(py.min())), 0), min(int(np.ceil(py.max())), ny - 1)
        if i1 < i0 or j1 < j0:
            continue
        gx, gy = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        (ax, ay), (bx, by), (cx, cy) = zip(px, py)
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-12:
            continue
        w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / d
        w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / d
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if not inside.any():
            continue
        z = w0 * t[0, 2] + w1 * t[1, 2] + w2 * t[2, 2]
        sub = zmin[j0:j1 + 1, i0:i1 + 1]
        better = inside & (z < sub)
        sub[better] = z[better]
        nzmap[j0:j1 + 1, i0:i1 + 1][better] = nz
    return zmin, nzmap


def disc(r_mm):
    r = int(np.ceil(r_mm / PX))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def overhangs(mesh, x0, y0, nx, ny, zmin):
    """Where the part needs a support, layer by layer: an island (nothing under it in the
    layer before) everywhere, and an overhang wherever it reaches more than REACH past the
    layer before (a bridge spanning up to 2 x REACH between two edges hangs on its own).
    Returns the pixels to support on the surface seen from below, and how many needing pixels
    are hidden behind the part (no pillar can reach them from the raft)."""
    lo, hi = mesh.bounds
    n = int(np.ceil((hi[2] - lo[2]) / LH))
    zs = lo[2] + (np.arange(n) + 0.5) * LH
    secs = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=list(zs))
    need = np.zeros((ny, nx), bool)
    hidden, prev = 0, np.zeros((ny, nx), np.uint8)
    grow = disc(REACH)
    for z, sec in zip(zs, secs):
        cur = np.zeros((ny, nx), np.uint8)
        if sec is not None:
            t = sec.metadata["to_3D"]
            if not np.allclose(t[:3, :3], np.eye(3)) or not np.allclose(t[:2, 3], 0):
                raise SystemExit("section_multiplane: the sections are not in the mesh's own x, y")
            polys = [np.round((np.asarray(d)[:, :2] - (x0, y0)) / PX * 16 - 8).astype(np.int32) for d in sec.discrete]
            cv2.fillPoly(cur, polys, 1, cv2.LINE_8, 4)
        k, lab = cv2.connectedComponents(cur, connectivity=8)
        flag = cur.astype(bool) & ~cv2.dilate(prev, grow).astype(bool)
        for c in range(1, k):                       # islands: all of them
            comp = lab == c
            if not (comp & prev.astype(bool)).any():
                flag |= comp
        seen = flag & (np.abs(zmin - z) <= LH)
        hidden += int((flag & ~seen).sum())
        need |= seen
        prev = cur
    return need, hidden


def contacts(mesh, cover):
    lo, hi = mesh.bounds
    x0, y0 = lo[0] - 1, lo[1] - 1
    nx, ny = int(np.ceil((hi[0] - lo[0] + 2) / PX)), int(np.ceil((hi[1] - lo[1] + 2) / PX))
    zmin, _ = zbuffer(mesh, x0, y0, nx, ny)
    finite = np.isfinite(zmin)
    need, hidden = overhangs(mesh, x0, y0, nx, ny, zmin)
    zf = np.where(finite, zmin, 1e3).astype(np.float32)
    # a pillar fits where the part stays above the tip's start across the body's footprint
    floor_body = cv2.erode(zf, disc(BODY_CLEAR))
    floor_tip = cv2.erode(zf, disc(TIP_R + 0.1))
    ok = need & (floor_body >= zf - TIP_L) & (floor_tip >= zf - TIP_IN - 0.05)
    # surfaces: connected pixels whose heights step less than 0.15 mm
    band = np.round(np.where(need, zmin, 0) / 0.3).astype(int)
    pts, missed = [], 0
    cov = disc(cover)
    kr = cov.shape[0] // 2
    for b in np.unique(band[need]):
        m = (need & (band == b)).astype(np.uint8)
        n, lab = cv2.connectedComponents(m, connectivity=8)
        for k in range(1, n):
            comp = lab == k
            todo = comp.copy()
            valid = comp & ok
            vy, vx = np.nonzero(valid)
            while todo.any():
                ty, tx = np.nonzero(todo)
                # the lowest pixel first (where a surface starts), then raster order
                first = np.lexsort((tx, ty, np.round(zmin[ty, tx], 2)))[0]
                py_, px_ = ty[first], tx[first]
                if len(vx):
                    d2 = (vx - px_) ** 2 + (vy - py_) ** 2
                    best = int(np.argmin(d2))
                    if d2[best] * PX * PX <= cover * cover:
                        cy, cx = vy[best], vx[best]
                        pts.append((x0 + (cx + 0.5) * PX, y0 + (cy + 0.5) * PX, float(zmin[cy, cx])))
                        ya, yb = max(cy - kr, 0), min(cy + kr + 1, ny)
                        xa, xb = max(cx - kr, 0), min(cx + kr + 1, nx)
                        k_ = cov[kr - (cy - ya):kr + (yb - cy), kr - (cx - xa):kr + (xb - cx)].astype(bool)
                        todo[ya:yb, xa:xb] &= ~k_
                        continue
                todo[py_, px_] = False      # no pillar fits within reach of this pixel
                missed += 1
    return np.array(pts), missed + hidden, need.sum() * PX * PX


def pillar(x, y, zc, z_raft):
    z_tip0 = zc - TIP_L
    body_h = z_tip0 - (z_raft + BASE_H)
    if body_h <= 0:
        raise SystemExit(f"contact at {x:.1f}, {y:.1f} sits {zc - z_raft:.2f} mm over the raft: too short for a pillar")
    foot = mf.Manifold.cylinder(BASE_H + 0.01, BASE_R, BODY_R, SEGMENTS).translate((x, y, z_raft - 0.01))
    body = mf.Manifold.cylinder(body_h + 0.02, BODY_R, BODY_R, SEGMENTS).translate((x, y, z_raft + BASE_H - 0.01))
    tip = mf.Manifold.cylinder(TIP_L + TIP_IN, BODY_R, TIP_R, SEGMENTS).translate((x, y, z_tip0))
    return [foot, body, tip]


def raft(points_xy):
    hull = sg.MultiPoint([tuple(p) for p in points_xy]).convex_hull.buffer(RAFT_GROW, join_style=2)
    hull = hull.simplify(0.05)
    c = np.array(hull.centroid.coords[0])
    ring = np.array(hull.exterior.coords[:-1]) - c
    if sg.Polygon(ring).exterior.is_ccw is False:
        ring = ring[::-1]
    w, h = np.ptp(ring, axis=0)
    cs = mf.CrossSection([ring.tolist()])
    slab = mf.Manifold.extrude(cs, RAFT_T, 0, 0.0, (1 - 2 * RAFT_T / w, 1 - 2 * RAFT_T / h))   # 45 deg edge
    return slab.translate((c[0], c[1], 0.0))


def to_manifold(mesh):
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(mesh.vertices, np.float32),
                               tri_verts=np.asarray(mesh.faces, np.uint32)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cover", type=float, default=2.0, help="every supported pixel within this of a contact, mm")
    ap.add_argument("--lift", type=float, default=4.0, help="raft top to the part's lowest point, mm")
    ap.add_argument("--out", type=Path, default=OUT / "halves_supported.ply")
    args = ap.parse_args()

    z = json.loads((HERE.parent / "docs/v4/enclosure/enclosure.json").read_text())["z"]
    tongue_h = 1.8
    levels = {  # assembled height -> what sits there (enclosure/generate.py)
        "shell": {tongue_h: "tongue's top edge (into the bezel's slot, 0.1 mm clear)",
                  z["split"]: "shell rim at the parting line (meets the bezel)",
                  z["boardBack"]: "standoff tops (the board's seat)",
                  z["floorIn"]: "inside of the back (hidden)"},
        "bezel": {z["split"]: "bezel bottom at the parting line (skirt rim, fills on the board's front)",
                  tongue_h + 0.1: "tongue slot's ceiling (0.1 mm over the tongue)",
                  z["lip"]: "underside of the lip (the screen's seat, 0.1 mm over the glass)"},
    }
    halves = [("shell", trimesh.load(OUT / "shell.stl", force="mesh"), True),
              ("bezel", trimesh.load(OUT / "bezel.stl", force="mesh"), False)]
    solids, x_at, report = [], 0.0, []
    for name, mesh, flip in halves:
        if flip:   # face up: turn the shell over (180 deg about X keeps it a right-handed part)
            mesh = mesh.copy()
            mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0]))
        pts, missed, area = contacts(mesh, args.cover)
        lo, hi = mesh.bounds
        dz = RAFT_T + args.lift - lo[2]
        if not len(pts):
            raise SystemExit(f"{name}: no contacts found")
        dx = x_at + RAFT_GROW - min(lo[0], pts[:, 0].min())
        shift = np.array([dx, RAFT_GROW - min(lo[1], pts[:, 1].min()), dz])
        mesh = mesh.copy()
        mesh.apply_translation(shift)
        p = pts + shift
        parts = [to_manifold(mesh), raft(p[:, :2])]
        for x, y, zc in p:
            parts += pillar(x, y, zc, RAFT_T)
        solid = mf.Manifold.batch_boolean(parts, mf.OpType.Add)
        solids.append(solid)
        x_at = solid.bounding_box()[3] + GAP_XY
        # where the contacts land, by assembled height
        z_asm = -pts[:, 2] if flip else pts[:, 2]
        tally = {}
        for zc in z_asm:
            key = min(levels[name], key=lambda L: abs(L - zc))
            label = levels[name][key] if abs(key - zc) < 0.06 else f"other surface at z {zc:.2f}"
            if label.startswith("other"):
                label = f"other surfaces (pocket ceilings, features) z {round(zc, 1):.1f}"
            tally[label] = tally.get(label, 0) + 1
        report.append(f"{name}: {len(pts)} contacts over {area:.0f} mm2 needing support"
                      + (f", {missed} px out of reach" if missed else ""))
        for label, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            report.append(f"   {n:4d}  {label}")
    out = mf.Manifold.batch_boolean(solids, mf.OpType.Add).simplify(1e-3)   # collapse slivers where a tip grazes a wall
    if out.status() != mf.Error.NoError:
        raise SystemExit(f"the supported layout's union failed: {out.status()}")
    m = out.to_mesh64()
    tm = trimesh.Trimesh(m.vert_properties[:, :3], m.tri_verts, process=False)
    if not tm.is_watertight:
        raise SystemExit(f"the supported layout is not watertight ({len(tm.faces)} faces)")
    tm.apply_translation([0, 0, -tm.bounds[0][2]])
    tm.export(args.out)
    print(f"{args.out}: {tm.extents[0]:.1f} x {tm.extents[1]:.1f} x {tm.extents[2]:.1f} mm")
    print("\n".join(report))


if __name__ == "__main__":
    main()
