#!/usr/bin/env python3
"""Orient, support, slice and check the case's two halves for the Anycubic Photon Mono M7 Pro.

NOT IN USE: its one print (enclosure/resin/prints.md #6, 2026-11-27) was rejected by the owner
(layer lines, warping, supports very hard to remove). The case is sliced with CHITUBOX.

  enclosure/out/shell.stl, bezel.stl  ->  a .pwsz ready to print, a report and pictures beside it

Orientation: each half stands on its south end, north up, leaning back LEAN deg so its
user-facing face (the shell's back, the bezel's front) looks up and away from the plate,
as the owner's working prints stood (20-23 deg, read from their files). No sideways lean:
the model's 45 deg roofs (the pack window, the counterbores) are drawn for an upright print.

Supports are worked out on the layers themselves, not on the mesh:

  1. Every layer is rastered (0.1 mm grid). Each pixel carries a debt: how far it hangs
     past what holds it. A pixel over material owes what that material owes; one that
     reaches past the layer below owes its reach, less the FREE mm a layer may overhang
     (45 deg). An island (nothing below) owes without limit.
  2. Where the debt passes DEBT a contact goes in, and the debt around it restarts from the
     contact. So islands get a contact in their first layer, and a flat overhang a contact
     about every 2 x DEBT.
  3. Each contact hangs on a branch (40 deg off vertical at most) from a trunk on a GRID mm
     grid; trunks are braced to their neighbours with 45 deg struts and stand on a raft.
     Every trunk, branch and strut is checked against the part: none passes through it.

The same exposure and lift as the owner's print #4 (enclosure/resin/prints.md), read from its
file (enclosure/resin/print4.pwsp, layers_controller): 0.1 mm layers, 3 x 25 s, 15 transition
layers, 2.1 s, lift 4 mm at 2.67 mm/s; shrink compensation X/Y 100.5 %, Z 100.3 % (print #3).
The file is written the way CHITUBOX wrote that one (layer_N.pw0Img: 4-bit colour, 12-bit
run, big-endian), so the printer sees the same container.

Checks on the finished layers, at the printer's own resolution (the run fails on any):
  - no island: every connected piece of a layer touches the layer below;
  - no debt over DEBT with the supports counted as material;
  - the file read back equals what was rastered.
The report also gives the numbers to hold against print #4 (worked) and #5 (failed):
pillar count and diameter at 3 mm, and each layer's area over the least area under it.

  python3 enclosure/resin_plate.py [--out ~/ResinPrints/NAME.pwsz] [--lean 22] [--debt 1.0]

Needs trimesh, numpy, scipy, opencv and shapely.
"""
import argparse
import io
import json
import math
import struct
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np
import shapely.geometry as sg
import trimesh
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
SETTINGS = HERE / "resin/print4.pwsp"          # print #4's machine and resin settings, as CHITUBOX stored them
RES_X, RES_Y = 13312, 5120
PX, PY = 223.642 / RES_X, 126.48 / RES_Y       # 16.8 x 24.7 um
LH = 0.1
BOTTOM = (3, 25.0, 5.0)                        # layers, exposure s, lift mm
TRANSITION = 15
EXPOSURE, LIFT_H, LIFT_V = 2.1, 4.0, 2.6666667461395264
MOTION_S = 2.99                                # per layer besides exposure, from print #4's 5029 s over 941 layers
SHRINK = (1.005, 1.005, 1.003)                 # print #3: "settings perfect"
ML_COST = 1.0218230485916138 / 30.96433448791504

G = 0.1                 # planning grid, mm
FREE = LH * math.tan(math.radians(45))         # a layer may reach this far past the one below
PART_GAP = 18.0         # between the two halves on the plate: each half's supports stay on its own side
LIFT = 6.0              # raft top to the part's lowest point
RAFT_T, RAFT_R, RAFT_UNDERCUT = 0.8, 3.0, 0.6
# Print #5 came off the build plate (owner: one half entirely, the other partly) on first layers of
# 1062 and 1210 mm2; print #4 held on 1275 and 1487 mm2. Each half's first layer is at least this.
RAFT_MIN = 1500.0
GRID = 4.0              # trunk pitch
TRUNK_R, BASE_R, BASE_H = 1.0, 1.7, 2.0   # 2.0 mm trunks (print #4's pillars: 1.7 median; #5's 0.8)
BRANCH_R, TIP_R, TIP_L, TIP_IN = 0.4, 0.35, 1.5, 0.3   # 0.7 mm contacts (print #4's: 0.6-1.0; #5's from 0.4)
BRANCH_MAX = math.radians(40)                  # off vertical
REACH = 3.8             # a contact to its trunk, across
BRACE_R = 0.4
CLR = 0.25              # supports to the part, besides at a contact


def log(*a):
    print(*a, flush=True)


# ───────────────────────────────────────────────────────────── the parts on the plate


def oriented(name, lean):
    """The half in its print pose: Z up, south end down, leaning back, its outer face up."""
    mesh = trimesh.load(OUT / f"{name}.stl", force="mesh", process=True)
    if not mesh.is_watertight:
        raise SystemExit(f"{name}.stl is not watertight")
    # model: X east, Y north, Z front. Turn about X so north goes up; the shell 90 + lean (its
    # back, -Z, looks up), the bezel 90 - lean (its front, +Z, looks up)
    phi = math.radians(90 + lean if name == "shell" else 90 - lean)
    R = trimesh.transformations.rotation_matrix(phi, [1, 0, 0])
    if name == "bezel":
        # lean the same way as the shell on the plate (toward +y): half a turn about Z
        R = trimesh.transformations.rotation_matrix(math.pi, [0, 0, 1]) @ R
    up = R[:3, :3] @ np.array([0, 0, -1.0 if name == "shell" else 1.0])
    if up[2] <= 0 or (R[:3, :3] @ np.array([0, 1.0, 0]))[2] <= 0:
        raise SystemExit(f"{name}: the outer face or north does not point up")
    mesh.apply_transform(R)
    mesh.apply_scale(SHRINK)
    return mesh, R[:3, :3]


class Part:
    def __init__(self, name, mesh, R, x_at):
        lo, hi = mesh.bounds
        mesh.apply_translation([x_at - lo[0], -(lo[1] + hi[1]) / 2, RAFT_T + LIFT - lo[2]])
        self.name, self.mesh, self.R = name, mesh, R
        self.shift = np.array([x_at - lo[0], -(lo[1] + hi[1]) / 2, RAFT_T + LIFT - lo[2]])
        lo, hi = mesh.bounds
        self.nz = int(math.ceil(hi[2] / LH - 1e-6))
        m = REACH + 6
        self.x0, self.y0 = lo[0] - m, lo[1] - m
        self.nx, self.ny = int((hi[0] - lo[0] + 2 * m) / G) + 1, int((hi[1] - lo[1] + 2 * m) / G) + 1
        self.sections = sections(mesh, self.nz)
        self.V = np.zeros((self.nz, self.ny, self.nx), bool)
        for k, polys in enumerate(self.sections):
            self.V[k] = raster(polys, self.x0, self.y0, G, G, self.nx, self.ny) > 0
        self.struts, self.balls, self.raft = [], [], []      # supports: cones (a, b, ra, rb), spheres (c, r), raft discs (x, y)
        self.contacts = []

    def model_xyz(self, p):
        """A plate point back in the model's own frame (mm, before the shrink compensation)."""
        return (self.R.T @ ((np.asarray(p) - self.shift) / SHRINK))


def sections(mesh, nz):
    """Each layer's outlines (at the layer's middle) as shapely polygons in plate mm."""
    heights = (np.arange(nz) + 0.5) * LH
    secs = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=list(heights))
    out = []
    for sec in secs:
        if sec is None:
            out.append([])
            continue
        t = sec.metadata["to_3D"]
        if not np.allclose(t[:3, :3], np.eye(3), atol=1e-9) or not np.allclose(t[:2, 3], 0, atol=1e-9):
            raise SystemExit("section_multiplane: the sections are not in the mesh's own x, y")
        out.append(sorted(sec.polygons_full, key=lambda p: -p.area))
    return out


def raster(polys, x0, y0, sx, sy, nx, ny, img=None):
    """Fill shapely polygons (largest first, so a part inside another's hole survives the hole)."""
    if img is None:
        img = np.zeros((ny, nx), np.uint8)
    to_px = lambda c: np.round((np.asarray(c)[:, :2] - (x0, y0)) / (sx, sy) * 16 - 8).astype(np.int32)
    for p in polys:
        cv2.fillPoly(img, [to_px(p.exterior.coords)], 255, cv2.LINE_8, 4)
        for h in p.interiors:
            cv2.fillPoly(img, [to_px(h.coords)], 0, cv2.LINE_8, 4)
    return img


# ───────────────────────────────────────────────────────────── debt and contacts


def debt_layer(cur, prev, U_prev, limit):
    """The debt of every pixel of `cur`, given the layer below and its debt."""
    U = np.full(cur.shape, np.inf, np.float32)
    if prev is None or not prev.any():
        return U
    for b in np.arange(0.0, limit + 1e-6, 0.1):
        mask = prev & (U_prev <= b + 1e-6)
        if not mask.any():
            continue
        d = cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 5) * G
        np.minimum(U, b + np.maximum(0.0, d - FREE), out=U)
    return U


def place_contacts(part, limit):
    """Walk the layers upward; put a contact wherever the debt passes `limit`."""
    V = part.V
    yy, xx = np.mgrid[0:part.ny, 0:part.nx]
    prev = U_prev = None
    worst = 0.0
    for k in range(part.nz):
        cur = V[k]
        if not cur.any():
            prev = U_prev = None
            continue
        U = debt_layer(cur, prev, U_prev, limit)
        bad = cur & (U > limit)
        if bad.any():
            inner = cv2.distanceTransform(cur.astype(np.uint8), cv2.DIST_L2, 3)
            while True:
                bad = cur & (U > limit)
                if not bad.any():
                    break
                by, bx = np.nonzero(bad)
                # the most indebted pixel; among equals, the one deepest inside the layer
                score = np.where(np.isinf(U[by, bx]), 1e6, U[by, bx]) * 1e3 + inner[by, bx]
                i = int(np.argmax(score))
                cy, cx = int(by[i]), int(bx[i])
                part.contacts.append((part.x0 + (cx + 0.5) * G, part.y0 + (cy + 0.5) * G, k))
                r = int(limit / G) + 2
                ya, yb, xa, xb = max(cy - r, 0), min(cy + r + 1, part.ny), max(cx - r, 0), min(cx + r + 1, part.nx)
                d = np.hypot(yy[ya:yb, xa:xb] - cy, xx[ya:yb, xa:xb] - cx).astype(np.float32) * G
                np.minimum(U[ya:yb, xa:xb], d, out=U[ya:yb, xa:xb])
        worst = max(worst, float(U[cur].max()))
        prev, U_prev = cur, U
    return worst


def recheck(part, S, limit):
    """Debt over the finished layers (part and supports as one material): pixels over `limit`."""
    over, worst, prev, U_prev = 0, 0.0, None, None
    for k in range(part.nz):
        cur = part.V[k] | S[k]
        if not cur.any():
            prev = U_prev = None
            continue
        if k == 0:
            U = np.zeros(cur.shape, np.float32)      # on the plate
        else:
            U = debt_layer(cur, prev, U_prev, limit + 0.5)
        m = part.V[k] & (U > limit + 0.05)
        over += int(m.sum())
        if part.V[k].any():
            fin = U[part.V[k]]
            worst = max(worst, float(fin.max()))
        prev, U_prev = cur, U
    return over, worst


# ───────────────────────────────────────────────────────────── supports


def clear(part, a, b, ra, rb, skip_top=0.0, clr=CLR):
    """Is the cone from a to b (plate mm; b the upper end) clear of the part by clr?
    The last skip_top mm below b are not looked at (a tip runs into its contact)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    L = np.linalg.norm(b - a)
    n = max(2, int(L / 0.1) + 1)
    t = np.linspace(0, 1, n)
    if skip_top > 0:
        t = t[t <= max(0.0, 1 - skip_top / max(L, 1e-9))]
        if not len(t):
            return True
    c = a[None] + t[:, None] * (b - a)[None]
    r = ra + t * (rb - ra) + clr
    ang = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    ox = np.concatenate([[0.0], np.cos(ang), 0.5 * np.cos(ang)])
    oy = np.concatenate([[0.0], np.sin(ang), 0.5 * np.sin(ang)])
    X = c[:, 0, None] + r[:, None] * ox[None]
    Y = c[:, 1, None] + r[:, None] * oy[None]
    K = np.clip((c[:, 2] / LH).astype(int), 0, part.nz - 1)[:, None] + np.zeros_like(X, int)
    I = ((X - part.x0) / G).astype(int)
    J = ((Y - part.y0) / G).astype(int)
    inside = (I >= 0) & (I < part.nx) & (J >= 0) & (J < part.ny) & (c[:, 2, None] < part.nz * LH)
    hit = np.zeros_like(inside)
    hit[inside] = part.V[K[inside], J[inside], I[inside]]
    return not hit.any()


def build_supports(part):
    """Trunks on the grid, a branch per contact, braces, raft. Returns the contacts no support reached."""
    V = part.V
    z_top_raft = RAFT_T
    # the part's underside over each pixel (the first layer with material), and the same with a
    # trunk's width round it
    has = V.any(0)
    under = np.where(has, V.argmax(0), part.nz).astype(np.float32) * LH
    rpx = int(math.ceil((TRUNK_R + CLR + 0.2) / G))
    under_wide = cv2.erode(under, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rpx + 1, 2 * rpx + 1)))

    def column_room(x, y):
        i, j = int((x - part.x0) / G), int((y - part.y0) / G)
        if not (rpx <= i < part.nx - rpx and rpx <= j < part.ny - rpx):
            return -1.0
        return float(under_wide[j, i])

    trunks = {}          # (x, y) -> top z
    lost = []
    slope = 1 / math.tan(BRANCH_MAX)

    def try_site(site, c):
        cx, cy, k = c
        zc = k * LH
        d = math.hypot(site[0] - cx, site[1] - cy)
        zj = zc - max(d * slope, TIP_L + 0.3)
        if zj < z_top_raft + BASE_H + 0.5 or column_room(*site) < zj + 0.2:
            return None
        j = (site[0], site[1], zj)
        tip0 = np.array([cx, cy, zc]) - (np.array([cx, cy, zc]) - np.array(j)) / np.linalg.norm(np.array([cx, cy, zc]) - np.array(j)) * TIP_L
        if not clear(part, j, tip0, BRANCH_R, BRANCH_R, clr=CLR):
            return None
        if not clear(part, tip0, (cx, cy, zc), BRANCH_R, TIP_R, skip_top=0.6, clr=0.1):
            return None
        return j, tuple(tip0)

    def grid_sites(cx, cy, pitch, reach):
        n = int(reach / pitch) + 1
        gx, gy = round(cx / pitch), round(cy / pitch)
        out = []
        for a in range(gx - n, gx + n + 1):
            for b_ in range(gy - n, gy + n + 1):
                s = (a * pitch, b_ * pitch)
                d = math.hypot(s[0] - cx, s[1] - cy)
                if d <= reach:
                    out.append((d, s))
        return out

    for c in sorted(part.contacts, key=lambda c: c[2]):
        cx, cy, k = c
        found = None
        for own in (False, True):
            cands = grid_sites(cx, cy, GRID, REACH) if not own else grid_sites(cx, cy, 1.0, REACH + 1.5)
            if own:      # a trunk of its own, off the grid: not within 2 mm of another
                cands = [(d, s) for d, s in cands if all(math.hypot(s[0] - t[0], s[1] - t[1]) >= 2.0 for t in trunks)]
            cands.sort(key=lambda ds: (ds[1] not in trunks, ds[0]))
            for d, s in cands:
                got = try_site(s, c)
                if got:
                    found = (s, *got)
                    break
            if found:
                break
        if not found:
            lost.append(c)
            continue
        s, j, tip0 = found
        trunks[s] = max(trunks.get(s, 0.0), j[2])
        part.struts.append((j, tip0, BRANCH_R, BRANCH_R))
        part.struts.append((tip0, (cx, cy, k * LH + TIP_IN), BRANCH_R, TIP_R))
        part.balls.append((j, max(BRANCH_R, TRUNK_R)))
        part.balls.append((tip0, BRANCH_R))

    # a tall trunk with no neighbour gets two of its own, on the nearest grid sites with room
    def neighbours(s):
        return [t for t in trunks if t != s and math.hypot(s[0] - t[0], s[1] - t[1]) <= GRID * 1.5 and min(trunks[s], trunks[t]) - (z_top_raft + BASE_H) >= GRID * 1.5]
    buddies = 0
    for s in [s for s in trunks if trunks[s] > 15]:
        want = 2 - len(neighbours(s))
        if want <= 0:
            continue
        opts = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            t = (round(s[0] / GRID) * GRID + dx * GRID, round(s[1] / GRID) * GRID + dy * GRID)
            if t in trunks or math.hypot(t[0] - s[0], t[1] - s[1]) < 2.5:
                continue
            opts.append((min(column_room(*t) - 0.5, trunks[s]), t))
        for room, t in sorted(opts, reverse=True)[:want]:
            if room >= min(trunks[s] * 0.6, 15):
                trunks[t] = room
                buddies += 1

    for (x, y), top in trunks.items():
        part.struts.append(((x, y, z_top_raft - 0.05), (x, y, z_top_raft + BASE_H), BASE_R, TRUNK_R))
        part.struts.append(((x, y, z_top_raft + BASE_H), (x, y, top), TRUNK_R, TRUNK_R))
        part.balls.append(((x, y, top), TRUNK_R))
        part.raft.append((x, y))

    # braces: every trunk to its neighbours, a zigzag of 45 deg struts over the height both have
    sites = list(trunks)
    braced = {s: 0 for s in sites}
    pairs = set()
    for s in sites:
        near = sorted((math.hypot(s[0] - t[0], s[1] - t[1]), t) for t in sites if t != s)
        for d, t in near[:4]:
            if d <= GRID * 1.1 and (t, s) not in pairs:
                pairs.add((s, t))
    for s in sites:      # one with no grid neighbour: its two nearest, wherever they are within reach
        if not any(s in pr for pr in pairs):
            for d, t in sorted((math.hypot(s[0] - t[0], s[1] - t[1]), t) for t in sites if t != s)[:2]:
                if d <= GRID * 2.5:
                    pairs.add((s, t))
    n_brace = 0
    for s, t in pairs:
        d = math.hypot(s[0] - t[0], s[1] - t[1])
        h = min(trunks[s], trunks[t])
        z, flip, made = z_top_raft + BASE_H, False, 0
        while z + d <= h:
            a, b_ = (s, t) if not flip else (t, s)
            p0, p1 = (a[0], a[1], z), (b_[0], b_[1], z + d)
            if clear(part, p0, p1, BRACE_R, BRACE_R):
                part.struts.append((p0, p1, BRACE_R, BRACE_R))
                made += 1
            z += d
            flip = not flip
        if made:
            braced[s] += 1
            braced[t] += 1
            n_brace += made
    part.buddies = buddies
    part.trunks, part.braced, part.n_brace = trunks, braced, n_brace
    return lost


def draw_supports(part, k, img, x0, y0, sx, sy):
    """Rasterise the supports' cross-section at layer k into img (any grid)."""
    z = (k + 0.5) * LH
    ang = np.linspace(0, 2 * np.pi, 28, endpoint=False)
    cs, sn = np.cos(ang), np.sin(ang)

    def blob(cx, cy, a, b_, az):
        ex, ey = a * cs, b_ * sn
        px = cx + ex * math.cos(az) - ey * math.sin(az)
        py = cy + ex * math.sin(az) + ey * math.cos(az)
        pts = np.round(np.stack([(px - x0) / sx, (py - y0) / sy], 1) * 16 - 8).astype(np.int32)
        cv2.fillConvexPoly(img, pts, 255, cv2.LINE_8, 4)

    for a, b_, ra, rb in part.by_layer[k]:
        (ax, ay, az_), (bx, by, bz) = a, b_
        if not (az_ <= z <= bz) or bz - az_ < 1e-9:
            continue
        t = (z - az_) / (bz - az_)
        r = ra + t * (rb - ra)
        hx, hy = bx - ax, by - ay
        hl = math.hypot(hx, hy)
        stretch = math.hypot(hl, bz - az_) / (bz - az_)       # 1 / cos(tilt)
        blob(ax + t * hx, ay + t * hy, r * stretch, r, math.atan2(hy, hx) if hl > 1e-9 else 0.0)
    for (cx, cy, cz), r in part.balls_by_layer[k]:
        dz = abs(z - cz)
        if dz < r:
            rr = math.sqrt(r * r - dz * dz)
            blob(cx, cy, rr, rr, 0.0)
    if z < RAFT_T:
        # one plate under all the trunks, its holes filled (a ring of raft would seal resin
        # against the build plate), its edge undercut for a scraper
        if part.raft_plate is None:
            plate = unary_union([sg.Point(x, y).buffer(RAFT_R, 16) for x, y in part.raft])
            part.raft_plate = unary_union([sg.Polygon(g.exterior) for g in getattr(plate, "geoms", [plate])])
            while part.raft_plate.buffer(-RAFT_UNDERCUT).area < RAFT_MIN:
                part.raft_plate = part.raft_plate.buffer(0.25, 16)
        cut = part.raft_plate.buffer(-RAFT_UNDERCUT * max(0.0, 1 - z / (RAFT_T * 0.6)))
        raster(sorted(getattr(cut, "geoms", [cut]), key=lambda g: -g.area), x0, y0, sx, sy, img.shape[1], img.shape[0], img)


def index_supports(part):
    part.raft_plate = None
    part.by_layer = [[] for _ in range(part.nz)]
    part.balls_by_layer = [[] for _ in range(part.nz)]
    for s in part.struts:
        k0, k1 = max(int(s[0][2] / LH) - 1, 0), min(int(s[1][2] / LH) + 1, part.nz - 1)
        for k in range(k0, k1 + 1):
            part.by_layer[k].append(s)
    for c, r in part.balls:
        k0, k1 = max(int((c[2] - r) / LH) - 1, 0), min(int((c[2] + r) / LH) + 1, part.nz - 1)
        for k in range(k0, k1 + 1):
            part.balls_by_layer[k].append((c, r))


# ───────────────────────────────────────────────────────────── the file


def rle(frame):
    """A layer as CHITUBOX stores it: 16-bit big-endian words, colour in the top 4 bits
    (0 or 15), run length in the low 12."""
    flat = frame.reshape(-1)
    edges = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate([[0], edges])
    runs = np.diff(np.concatenate([starts, [flat.size]]))
    cols = (flat[starts] > 0).astype(np.uint16) * 15
    full, rest = np.divmod(runs, 4095)
    count = full + (rest > 0)
    idx = np.repeat(np.arange(len(runs)), count)
    words = np.full(len(idx), 4095, np.uint16)
    last = np.cumsum(count) - 1
    has_rest = rest > 0
    words[last[has_rest]] = rest[has_rest]
    words |= (cols[idx] << 12)
    return words.astype(">u2").tobytes()


def unrle(data):
    w = np.frombuffer(data, dtype=">u2")
    img = np.repeat((w >> 12).astype(np.uint8), (w & 0xFFF).astype(np.int64))
    if img.size != RES_X * RES_Y:
        raise SystemExit(f"a layer decodes to {img.size} pixels, not {RES_X * RES_Y}")
    return img.reshape(RES_Y, RES_X) > 0


def exposure(i):
    n0 = BOTTOM[0]
    if i < n0:
        return BOTTOM[1]
    if i < n0 + TRANSITION:
        return BOTTOM[1] - (BOTTOM[1] - EXPOSURE) / (TRANSITION + 1) * (i - n0 + 1)
    return EXPOSURE


def write_pwsz(path, layer_blobs, areas, previews):
    n = len(layer_blobs)
    f32 = lambda v: float(np.float32(v))
    ctrl = {"count": n, "paras": [{"layer_index": i, "exposure_time": f32(exposure(i)), "layer_minheight": f32(i * LH),
                                    "layer_thickness": f32(LH), "zup_height": BOTTOM[2] if i < BOTTOM[0] else LIFT_H,
                                    "zup_speed": LIFT_V} for i in range(n)]}
    ml = sum(areas) * LH / 1000
    seconds = int(round(sum(exposure(i) for i in range(n)) + MOTION_S * n))
    info = {"cost": ml * ML_COST, "currency": "$", "print_time": seconds, "volume": ml}
    scene = bytearray(b"ANYCUBIC-PWSZ".ljust(16, b"\0") + b"chitubox-ex".ljust(64, b"\0"))
    scene += struct.pack("<4IfI6fI", 3, 1, 0, 0, 1.0, n, 0, 0, 0, 0, 0, 0, 0) + bytes(256) + b"<---" + struct.pack("<I", n)
    vols = bytearray(struct.pack("<IfII", 0x20251024, 0.2, 1, n))
    for i, a in enumerate(areas):
        scene += struct.pack("<6fIf8I", (i + 1) * LH, a, 0, 0, 0, 0, 1, a, *([0] * 8))
        vols += struct.pack("<Iff8If", i + 1, (i + 1) * LH, LH, *([0] * 8), a)
    scene += b"--->"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:      # CHITUBOX's order
        z.writestr("preview_images/preview_0.png", previews[0])
        z.writestr("preview_images/preview_1.png", previews[1])
        z.writestr("software_info.conf", json.dumps({"mark": "CHITUBOX", "opengl": "3.3-CoreProfile", "os": "win-64",
                                                     "version": "1.2.3"}, indent=4))
        z.writestr("layers_controller.conf", json.dumps(ctrl, indent=4))
        z.writestr("print_info.json", json.dumps(info, indent=4))
        z.writestr("anycubic_photon_resins.pwsp", SETTINGS.read_bytes())
        z.writestr("scene.slice", bytes(scene))
        z.writestr("calc_layer_volumes.data", bytes(vols))
        for i, blob in enumerate(layer_blobs):
            z.writestr(f"layer_images/layer_{i}.pw0Img", blob)
    path.write_bytes(buf.getvalue())
    return ml, seconds


def shaded(vol, axis, flip):
    """A grey picture of the layers seen from the side: depth-shaded first surface."""
    v = np.moveaxis(vol, axis, 0)
    if flip:
        v = v[::-1]
    first = np.where(v.any(0), v.argmax(0), -1).astype(np.float32)
    img = np.full(first.shape, 255, np.uint8)
    hit = first >= 0
    if hit.any():
        d = first[hit]
        img[hit] = (60 + 170 * (1 - (d - d.min()) / max(float(d.max() - d.min()), 1.0))).astype(np.uint8)
    return img[::-1]      # z up


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT / "case_supported.pwsz")
    ap.add_argument("--lean", type=float, default=22.0, help="deg off vertical (the owner's prints: 20-23)")
    ap.add_argument("--debt", type=float, default=1.0, help="mm a pixel may hang past what holds it")
    args = ap.parse_args()
    stem = args.out.with_suffix("")

    parts, x_at = [], 0.0
    for name in ("bezel", "shell"):
        mesh, R = oriented(name, args.lean)
        log(f"{name}: slicing")
        p = Part(name, mesh, R, x_at)
        x_at = p.mesh.bounds[1][0] + PART_GAP
        parts.append(p)
    # centre the pair on the plate in x (each is already centred in y)
    span = (parts[0].mesh.bounds[0][0], parts[-1].mesh.bounds[1][0])
    plate_dx = -(span[0] + span[1]) / 2

    report = {"lean_deg": args.lean, "debt_mm": args.debt, "parts": {}}
    for p in parts:
        place_contacts(p, args.debt)
        p.planned = len(p.contacts)
        log(f"{p.name}: {p.planned} contacts from the debt walk")
    nz = max(p.nz for p in parts)
    fx0, fy0 = -RES_X * PX / 2 - plate_dx, -RES_Y * PY / 2
    lo = np.min([[p.x0, p.y0] for p in parts], axis=0)
    hi = np.max([[p.x0 + p.nx * G, p.y0 + p.ny * G] for p in parts], axis=0)
    crop = (max(int((lo[1] - fy0) / PY), 0), min(int((hi[1] - fy0) / PY) + 1, RES_Y),
            max(int((lo[0] - fx0) / PX), 0), min(int((hi[0] - fx0) / PX) + 1, RES_X))
    # Supports, then the layers at the printer's resolution. A piece of a layer finer than the
    # planning grid can still come out an island there: it gets a contact and the pass repeats.
    for attempt in range(5):
        failed, lost_all = [], {}
        for p in parts:
            p.struts, p.balls, p.raft = [], [], []
            lost_all[p.name] = build_supports(p)
            index_supports(p)
            # its supports keep 1 mm off the other half (they are only checked against their own)
            sx = [v for a_, b_, ra, rb in p.struts for v in (a_[0] - ra, a_[0] + ra, b_[0] - rb, b_[0] + rb)] + \
                 [v for x, y in p.raft for v in (x - RAFT_R, x + RAFT_R)]
            for q in parts:
                if q is not p and min(sx) < q.mesh.bounds[1][0] + 1 and max(sx) > q.mesh.bounds[0][0] - 1:
                    raise SystemExit(f"{p.name}'s supports reach the {q.name}: widen PART_GAP")
            log(f"pass {attempt}: {p.name}: {len(p.contacts)} contacts, {len(p.trunks)} trunks ({p.buddies} added beside lone ones), "
                f"{p.n_brace} braces, {len(lost_all[p.name])} unreached")
        blobs, areas, islands = [], [], []
        prev_frame = None
        for k in range(nz):
            frame = np.zeros((RES_Y, RES_X), np.uint8)
            for p in parts:
                if k < p.nz:
                    raster(p.sections[k], fx0, fy0, PX, PY, RES_X, RES_Y, frame)
            for p in parts:
                if k < p.nz:
                    draw_supports(p, k, frame, fx0, fy0, PX, PY)
            on = frame > 0
            if on[:, [0, -1]].any() or on[[0, -1]].any():
                raise SystemExit(f"layer {k} reaches the edge of the screen")
            c = on[crop[0]:crop[1], crop[2]:crop[3]]
            if on.sum() != c.sum():
                raise SystemExit(f"layer {k} has material outside the parts' boxes")
            if k > 0:
                n, lab = cv2.connectedComponents(c.astype(np.uint8), connectivity=8)
                touching = np.unique(lab[cv2.dilate(prev_frame.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0])
                for comp in set(range(1, n)) - set(touching.tolist()):
                    ys, xs = np.nonzero(lab == comp)
                    i = int(np.argmin((xs - xs.mean()) ** 2 + ((ys - ys.mean()) * PY / PX) ** 2))
                    islands.append((k, round(float((xs[i] + crop[2] + 0.5) * PX + fx0), 3), round(float((ys[i] + crop[0] + 0.5) * PY + fy0), 3),
                                    round(len(xs) * PX * PY, 4)))
            prev_frame = c
            areas.append(float(on.sum()) * PX * PY)
            blobs.append(rle(frame))
            if k % 200 == 0:
                log(f"  layer {k}/{nz}: {areas[-1]:.0f} mm2")
        while areas and areas[-1] == 0:      # the top layer's middle can lie above the part's last sliver
            areas.pop()
            blobs.pop()
        if min(areas) == 0:
            raise SystemExit(f"layer {areas.index(0)} is empty")
        log(f"pass {attempt}: {len(islands)} islands at the printer's resolution")
        if not islands or attempt == 4:
            break
        for k, x, y, _ in islands:
            p = min(parts, key=lambda q: abs(x - (q.mesh.bounds[0][0] + q.mesh.bounds[1][0]) / 2))
            p.contacts.append((x, y, k))
    if islands:
        failed.append(f"{len(islands)} islands (layer, x, y, mm2): {islands[:20]}")
    report["islands"] = islands[:200]

    nz = len(areas)
    for p in parts:
        lost = lost_all[p.name]
        pts = np.array([p.model_xyz((x, y, k * LH)) for x, y, k in p.contacts])
        south_y = (p.R.T @ np.diag(1 / np.array(SHRINK)) @ (p.mesh.vertices - p.shift).T).T[:, 1].min()
        tall = [s for s, top in p.trunks.items() if top > 25 and p.braced[s] == 0]
        report["parts"][p.name] = {
            "contacts": len(p.contacts), "contacts_added_for_fine_islands": len(p.contacts) - p.planned,
            "trunks": len(p.trunks), "braces": p.n_brace,
            "contacts_on_south_end_wall": int((pts[:, 1] < south_y + 2.9).sum()),
            "unreached": [[round(float(v), 2) for v in p.model_xyz((x, y, k * LH))] for x, y, k in lost],
            "unbraced_trunks_over_25mm": len(tall), "height_mm": round(p.nz * LH, 1),
            "tallest_trunk_mm": round(max(p.trunks.values()), 1) if p.trunks else 0}
        if lost:
            failed.append(f"{p.name}: {len(lost)} contacts no support reaches (model x, y, z): "
                          + "; ".join(str(v) for v in report["parts"][p.name]["unreached"][:12]))
        if tall:
            failed.append(f"{p.name}: {len(tall)} trunks over 25 mm with no brace")
        # supports on the planning grid, for the debt recheck and the pictures
        S = np.zeros_like(p.V)
        for k in range(p.nz):
            img = np.zeros((p.ny, p.nx), np.uint8)
            draw_supports(p, k, img, p.x0, p.y0, G, G)
            S[k] = img > 0
        over, worst2 = recheck(p, S, args.debt)
        report["parts"][p.name].update({"debt_over_limit_px": over, "worst_debt_mm": round(worst2, 2)})
        log(f"{p.name}: debt recheck with supports: {over} px over, worst {worst2:.2f} mm")
        if over:
            failed.append(f"{p.name}: {over} pixels (0.1 mm) still hang more than {args.debt} mm with the supports in")
        both = p.V | S
        cv2.imwrite(f"{stem}_{p.name}_front.png", shaded(both, 1, False))
        cv2.imwrite(f"{stem}_{p.name}_behind.png", shaded(both, 1, True))
        cv2.imwrite(f"{stem}_{p.name}_side.png", shaded(both, 2, False))
        del S, both

    # ── numbers to hold against prints #4 and #5 ──
    k3 = int(3.0 / LH)
    frame3 = unrle(blobs[k3]).astype(np.uint8)
    n3, _, st3, _ = cv2.connectedComponentsWithStats(frame3, connectivity=8)
    a3 = st3[1:, cv2.CC_STAT_AREA] * PX * PY
    first_part = int((RAFT_T + LIFT) / LH)
    a = np.array(areas)
    least_under = np.minimum.accumulate(np.where(np.arange(nz) >= int(RAFT_T / LH) + 2, a, np.inf))
    ratio = a[first_part:] / least_under[first_part - 1:-1]
    report["compare"] = {
        "pillars_at_3mm": int(n3 - 1), "pillar_area_at_3mm_mm2": round(float(a3.sum()), 1),
        "pillar_dia_median_mm": round(float(np.median(2 * np.sqrt(a3 / np.pi))), 2),
        "least_support_area_mm2": round(float(a[int(RAFT_T / LH) + 2:first_part].min()), 1),
        "largest_layer_mm2": round(float(a[first_part:].max()), 1),
        "worst_layer_over_least_below": round(float(ratio.max()), 2),
        "print4": {"pillars_at_3mm": 155, "pillar_area_at_3mm_mm2": 368.5, "pillar_dia_median_mm": 1.69,
                   "worst_layer_over_least_below": 1.5},
        "print5": {"pillars_at_3mm": 166, "pillar_area_at_3mm_mm2": 104.9, "pillar_dia_median_mm": 0.81,
                   "worst_layer_over_least_below": 3.6}}

    previews = []
    mosaic = np.hstack([cv2.resize(cv2.imread(f"{stem}_{p.name}_front.png", 0), (160, 240)) for p in parts])
    for w, h in ((224, 168), (336, 252)):
        canvas = np.full((h, w), 255, np.uint8)
        m = cv2.resize(mosaic, (int(mosaic.shape[1] * h / mosaic.shape[0]), h))
        x = (w - m.shape[1]) // 2
        canvas[:, max(x, 0):max(x, 0) + min(m.shape[1], w)] = m[:, :min(m.shape[1], w)]
        ok, png = cv2.imencode(".png", cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGRA))
        if not ok:
            raise SystemExit("preview PNG encoding failed")
        previews.append(png.tobytes())
    ml, seconds = write_pwsz(args.out, blobs, areas, previews)

    # ── read it back ──
    with zipfile.ZipFile(args.out) as z:
        names = z.namelist()
        if sum(n.startswith("layer_images/") for n in names) != nz:
            failed.append("the file holds a different number of layers")
        for k in sorted({0, 1, k3, first_part, nz // 3, nz // 2, 2 * nz // 3, nz - 1}):
            back = unrle(z.read(f"layer_images/layer_{k}.pw0Img"))
            if abs(back.sum() * PX * PY - areas[k]) > 1e-6:
                failed.append(f"layer {k} reads back with a different area")
    report.update({"file": str(args.out), "layers": nz, "height_mm": round(nz * LH, 1), "resin_ml": round(ml, 1),
                   "print_time_min": round(seconds / 60), "failed_checks": failed,
                   "settings": {"layer_mm": LH, "bottom": f"{BOTTOM[0]} x {BOTTOM[1]} s", "transition_layers": TRANSITION,
                                "exposure_s": EXPOSURE, "lift": f"{LIFT_H} mm at {LIFT_V:.2f} mm/s",
                                "shrink_compensation_xyz": SHRINK},
                   "supports": {"contact_dia_mm": 2 * TIP_R, "branch_dia_mm": 2 * BRANCH_R, "trunk_dia_mm": 2 * TRUNK_R,
                                "brace_dia_mm": 2 * BRACE_R, "trunk_grid_mm": GRID, "raft_mm": RAFT_T, "lift_mm": LIFT}})
    Path(f"{stem}_report.json").write_text(json.dumps(report, indent=1))
    log(json.dumps({k: v for k, v in report.items() if k != "islands"}, indent=1))
    if failed:
        raise SystemExit("CHECKS FAILED:\n  " + "\n  ".join(failed))
    log(f"{args.out}: all checks passed")


if __name__ == "__main__":
    sys.exit(main())
