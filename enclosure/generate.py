#!/usr/bin/env python3
"""Ambire signer enclosure: a two-part see-through case generated from the board.

Every feature that has to line up with the board is read from the board, not typed in:

  board/wallet.kicad_pcb    outline, slots, thickness, mounting holes, the camera
                            keep-out, the power button's land (its fab outline
                            carries the plunger)
  kicad-cli pcb export glb  every modelled part on the back face, as real geometry:
                            the deepest part sets the back cavity, and whatever
                            pokes past the board edge (USB-C mouth, airgap lever)
                            gets a wall opening sized from it

The rest are datasheet constants, each with its source below.

Parts (Z = 0 is the board's front face, +Z toward the screen; X east, Y north):

  shell   back + walls up to the board's front face; four standoffs the board rests
          on, each with a clearance hole and a counterbore in the back face where the
          screw head sits flush; USB-C opening, airgap-lever slot, camera window, and
          a flex tab in the east wall that presses the power button
  bezel   front plate with the display window and walls down to the parting line;
          its skirt laps over a tongue on the shell. Inside: four threaded bosses at
          the board's holes (under the screen's corners, where the battery is not),
          solid fills that locate the screen, and a solid chin with windows for the
          display ribbons

Assembly: screen face down into the bezel, battery onto the screen, board onto the
bosses, shell over it, four M2 screws from the back through shell and board into the
bezel's bosses. The screws clamp the board between the standoffs and the bosses.

Outputs:
  enclosure/out/{shell,bezel}.{step,stl}, case.3mf   for print / quote (the 3MF lays both
                                                      parts out flat for printing)
  docs/enclosure/                                     the concept page's 3D preview and configurator:
    enclosure.json    the numbers the preview draws from, the flame, the print schemes
    board.json        the populated board, from KiCad's own 3D export
    {shell,bezel}.json  the case parts (GLB as base64: the artifact host serves no .glb)
    downloads.json + files/   the DIY sets: fab files as ordered, and the case

Run from hardware/ambire-wallet:
  ../../.venv-enclosure/bin/python enclosure/generate.py
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from build123d import (
    Axis,
    Box,
    Rectangle,
    Cone,
    Cylinder,
    Pos,
    RectangleRounded,
    Rot,
    export_gltf,
    export_step,
    export_stl,
    extrude,
    Mesher,
    fillet,
)

HERE = Path(__file__).resolve().parent.parent
BOARD = HERE / "board/wallet.kicad_pcb"
OUT = HERE / "enclosure/out"
WEB = HERE / "docs/enclosure"
sys.path.insert(0, str(HERE / "art"))
import flame as flame_art  # noqa: E402  (art/flame.py, shared with scripts/silkFlame.py)
KICAD_3D = os.environ.get("KICAD_3DMODEL_DIR", "/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels")

# ── Stack in front of the board ─────────────────────────────────────────────
FOAM_T = 0.3          # foam tape under the cell (the same value newBoard.py budgets the tails with)
CELL = (30.0, 40.0, 4.0)   # 403040 pouch, w x h x t (newBoard.py CELL_T); position on the board not fixed yet
# Newhaven NHD-2.4-240320AF-CSXP-CTP mechanical drawing (12/15/2023): outline
# 42.80 x 59.91, overall 4.13 +/-0.30; viewing area 37.72 x 49.96, 2.54 in from
# each side and 2.52 down from the top edge.
PANEL_T = 4.13
PANEL_VA = (2.54, 2.52, 37.72, 49.96)  # x, y offset from the outline's top-left, w, h
PANEL_GAP = 0.1       # under the lip; the foam takes the panel's +/-0.3 tolerance

# ── Back face ───────────────────────────────────────────────────────────────
# Not modelled in KiCad: the OV2640 camera head ("5.4-5.9 mm tall on every
# drawing found", design.md) is fitted by hand; the others are flat packages
# whose library models are missing from this KiCad install. Each gets a height
# bound here, so a newly unmodelled part stops the build instead of passing unseen.
CAM_HEAD_H = 5.9
UNMODELLED_MAX_H = {"U5": 0.6, "U6": 0.6, "U13": 1.0, "U15": 0.8, "U16": 1.0}
BACK_CLEAR = 0.6      # between the deepest part and the inside of the back
SKRT_ACTUATOR_H = 2.05  # ALPS SKRTLAE010: actuator centre above the board (distributor drawing data)

# ── Case ────────────────────────────────────────────────────────────────────
WALL = 1.8
FLOOR = 1.2
LIP = 0.8             # bezel over the glass
GAP = 0.3             # board edge to wall
TONGUE_T, TONGUE_H, TONGUE_CLR = 0.8, 1.8, 0.1
BACK_FILLET, FRONT_FILLET = 1.5, 1.2
# Outer walls lean in toward the back and toward the front, widest at the parting
# line, so the two halves meet flush: the back and front faces are DRAFT_MM narrower
# (in total, both sides together) than the parting line. That softens the box and
# lets a moulded part release; both halves still print flat face down without
# supports (the walls overhang ~3 deg, far inside the ~45 deg a printer manages).
DRAFT_MM = 1.0
MAX_UNSUPPORTED = 45.0
MIN_WALL = 1.2        # thinnest the drafted wall may get, at the back and front faces
WINDOW_MARGIN = 0.3   # display window beyond the viewing area, each side
# Screws: M2 pan head, self-tapping into the bezel's bosses, from the back.
STANDOFF_R = 2.5     # shell standoff under each board hole (the board keeps parts 2.6 mm off the hole centre)
CLEAR_R = 1.2        # M2 clearance through the shell
CB_R, CB_DEPTH = 2.05, 1.7   # counterbore for the 3.8 x 1.6 mm head, flush with the back face
BOSS_R, PILOT_R = 2.2, 0.8   # bezel boss, 1.6 mm pilot for M2 self-tapping
PILOT_TOP_CLR = 0.8  # pilot stops this far under the screen's back
SCREW_LENGTHS = (6, 8, 10, 12)
MIN_ENGAGE = 2.5     # mm of thread in the boss
BOSS_PART_CLR = 0.2
M2_SHANK_R = 1.0
MIN_SHANK_CLR = 0.3  # screw shank to the nearest part; a standoff notched for a part may open on that side
SCREEN_CLR = 0.15    # bezel fills to the screen's glass edge
RIBBON_CLR = 0.3     # around the display ribbons' slots
CAM_WINDOW_D = 6.0
USB_PLUG = (12.6, 6.8, 2.2)  # opening for a USB-C plug overmold (w, h, corner r); common overmolds are <= 12.35 x 6.5
LEVER_TRAVEL, LEVER_CLR = 1.5, 0.5   # MSK12C02 slide travel; clearance around the lever (it sits ~1 mm inside the wall: a pen tip, not a thumb)
TAB = (8.5, 4.0, 0.5, 1.0)   # power tab: length, height, slot width, thickness
NUB_D, NUB_GAP = 2.2, 0.1    # nub on the tab; air left before the plunger tip


# ── Back print ──────────────────────────────────────────────────────────────
# The flame itself (facets, size, placement, gap) is art/flame.py, shared with the
# board's silkscreen (scripts/silkFlame.py).
# Ink on mask. Brand colours: Purple #6000FF, Black #181A1F, Volt #D7FF00 (the
# brand's neon green). JLC's standard silkscreen is white or black only, so a
# purple flame or a Volt board is a full-colour print (PCBWay); white on black and
# black on white are an ordinary mask + silkscreen order anywhere.
PRINT_SCHEMES = [
    {"id": "neon", "label": "Purple on neon", "ink": "#6000FF", "mask": "#D7FF00", "colour": True},
    {"id": "black", "label": "Purple on black", "ink": "#6000FF", "mask": "#181A1F", "colour": True},
    {"id": "white-black", "label": "White on black", "ink": "#F2F3F5", "mask": "#181A1F", "colour": False},
    {"id": "black-white", "label": "Black on white", "ink": "#181A1F", "mask": "#EEEEEA", "colour": False},
    {"id": "purple-white", "label": "Purple on white", "ink": "#6000FF", "mask": "#EEEEEA", "colour": True},
    {"id": "incognito", "label": "Incognito", "ink": None, "mask": "#181A1F", "colour": False},   # plain black, no print
]

CASE_README = """Ambire signer proto 0 case

case.3mf     both parts laid out for printing: the shell on its back, the bezel face down
shell.stl    back shell (four standoffs; the screw heads sit flush in its back face)
bezel.stl    front bezel (the screen sits in it; the screws thread into its bosses)

Assembly: screen face down into the bezel, battery onto the screen, board onto the
bezel's bosses, shell over it, then 4x M2 pan-head screws (length below) from the
back. Print the shell on its back and the bezel face down: neither needs supports.
*.step       the same parts for CAD

Units are millimetres. Print in translucent or clear resin or PETG. Nothing has been
test-printed yet: the screw bosses and the power-button tab may need adjusting.
"""

# ── DIY downloads (the board as ordered; the case from this run) ────────────
ORDER = HERE / "production/order"
FAB_SETS = {
    "jlc": [("Gerbers", ORDER / "jlc-plain/wallet_gerbers.zip"),
            ("Bill of materials", ORDER / "jlc-plain/wallet_bom-jlc.csv"),
            ("Pick and place", ORDER / "jlc-plain/wallet_positions-jlc.csv")],
    "pcbway": [("Gerbers", ORDER / "jlc-plain/wallet_gerbers.zip"),
               ("Bill of materials", ORDER / "pcbway/wallet_bom-pcbway.csv"),
               ("Pick and place", ORDER / "pcbway/wallet_centroid-pcbway.csv")],
}
MESH_TOL = 0.03


# ─────────────────────────────────────────────────────────────────── board file


def sexp(text: str):
    stack: list[list] = [[]]
    for t in re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+', text):
        if t == "(":
            stack.append([])
        elif t == ")":
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(t[1:-1] if t.startswith('"') else t)
    return stack[0][0]


def kids(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def kid(node, key):
    found = kids(node, key)
    return found[0] if found else None


def xy(node):
    return float(node[1]), float(node[2])


def read_board(path: Path) -> dict:
    root = sexp(path.read_text(encoding="utf-8"))
    thickness = float(kid(kid(root, "general"), "thickness")[1])

    edges = []
    for item in kids(root, "gr_line") + kids(root, "gr_arc"):
        if kid(item, "layer")[1] != "Edge.Cuts":
            continue
        pts = [xy(kid(item, k)) for k in ("start", "mid", "end") if kid(item, k)]
        edges.append(pts)
    allp = np.array([p for e in edges for p in e])
    x0, y0 = allp.min(0)
    x1, y1 = allp.max(0)
    if abs(x0) > 0.01 or abs(y0) > 0.01:
        raise SystemExit(f"board outline does not start at the origin: {x0:.3f}, {y0:.3f}")

    def on_outer(pts):
        return any(min(abs(px - x0), abs(px - x1), abs(py - y0), abs(py - y1)) < 0.01 for px, py in pts)

    corner = [e for e in edges if len(e) == 3 and on_outer(e)]
    radius = round(abs(corner[0][0][0] - corner[0][2][0]), 3)

    # Inner loops (the tail slots): union edges that share an endpoint.
    inner = [e for e in edges if not on_outer(e)]
    parent = list(range(len(inner)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    key = lambda p: (round(p[0], 2), round(p[1], 2))
    owner = {}
    for i, e in enumerate(inner):
        for p in (e[0], e[-1]):
            j = owner.setdefault(key(p), i)
            parent[find(i)] = find(j)
    loops: dict[int, list] = {}
    for i, e in enumerate(inner):
        loops.setdefault(find(i), []).extend(e)
    slots = []
    for pts in loops.values():
        a = np.array(pts)
        slots.append([round(v, 3) for v in (*a.min(0), *a.max(0))])
    slots.sort()

    fps = {}
    for fp in kids(root, "footprint"):
        ref = next((p[2] for p in kids(fp, "property") if p[1] == "Reference"), None)
        at = kid(fp, "at")
        fps[ref] = {
            "lib": fp[1], "x": float(at[1]), "y": float(at[2]),
            "rot": float(at[3]) if len(at) > 3 else 0.0, "layer": kid(fp, "layer")[1], "node": fp,
        }
    holes = []
    for ref, f in sorted(fps.items()):
        if f["lib"].startswith("MountingHole:"):
            drill = float(kid(kid(f["node"], "pad"), "drill")[1])
            holes.append([f["x"], f["y"], drill])

    zones = {}
    for z in kids(root, "zone"):
        name = kid(z, "name")
        if not name:
            continue
        pts = np.array([xy(p) for p in kid(kid(z, "polygon"), "pts") if p[0] == "xy"])
        zones[name[1]] = [round(v, 3) for v in (*pts.min(0), *pts.max(0))]

    return {"w": x1 - x0, "h": y1 - y0, "r": radius, "t": thickness, "slots": slots,
            "holes": holes, "zones": zones, "fps": fps}


def panel_outline():
    """The panel outline newBoard.py lays the display tails from (x, y, w, h)."""
    src = (HERE / "scripts/newBoard.py").read_text(encoding="utf-8")
    m = re.search(r"^PANEL = \(([^)]*)\)", src, re.M)
    if not m:
        raise SystemExit("PANEL not found in scripts/newBoard.py")
    return tuple(float(v) for v in m.group(1).split(","))


def fab_world(fp: dict) -> np.ndarray:
    """Fab-layer line endpoints of a footprint in board coordinates. KiCad stores a
    back-side footprint's graphics already mirrored; world = at + R(-rot) local."""
    th = math.radians(fp["rot"])
    c, s = math.cos(th), math.sin(th)
    out = []
    for ln in kids(fp["node"], "fp_line"):
        if not kid(ln, "layer")[1].endswith(".Fab"):
            continue
        for k in ("start", "end"):
            lx, ly = xy(kid(ln, k))
            out.append((fp["x"] + lx * c + ly * s, fp["y"] - lx * s + ly * c))
    return np.array(out)


# ───────────────────────────────────────────────────────────── parts geometry


def kicad_glb(board_path: Path, tmp: Path, *flags: str) -> list[tuple]:
    """Export the board with kicad-cli and flatten it: one (ref, material, vertices,
    triangles) per primitive, vertices in board mm as x, y (south +), h (height off the
    back face; parts on the back are negative). ref is the footprint reference, or None
    for the board's own layers (body, mask, pads)."""
    if not shutil.which("kicad-cli"):
        raise SystemExit("kicad-cli not on PATH (KiCad 8+)")
    text = board_path.read_text(encoding="utf-8")
    text = text.replace("${KIPRJMOD}", str(board_path.parent)).replace("${KICAD10_3DMODEL_DIR}", KICAD_3D)
    text = model_fallbacks(text, board_path.parent)
    copy = tmp / "board.kicad_pcb"
    copy.write_text(text, encoding="utf-8")
    glb = tmp / "export.glb"
    run = subprocess.run(["kicad-cli", "pcb", "export", "glb", *flags, "-f", "-o", str(glb), str(copy)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run.returncode != 0 or not glb.exists():
        raise SystemExit(f"kicad-cli glb export failed:\n{run.stdout}\n{run.stderr}")
    data = glb.read_bytes()
    jl = struct.unpack("<I", data[12:16])[0]
    g = json.loads(data[20:20 + jl])
    bin_ = data[20 + jl + 8:]

    def matrix(n):
        if "matrix" in n:
            return np.array(n["matrix"]).reshape(4, 4).T
        x, y, z, w = n.get("rotation", [0, 0, 0, 1])
        m = np.eye(4)
        m[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
        m[:3, :3] *= np.array(n.get("scale", [1, 1, 1]))
        m[:3, 3] = n.get("translation", [0, 0, 0])
        return m

    def accessor(i, width, dtype):
        a = g["accessors"][i]
        bv = g["bufferViews"][a["bufferView"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        size = np.dtype(dtype).itemsize * width
        stride = bv.get("byteStride", size)
        raw = np.frombuffer(bin_, dtype=np.uint8, count=stride * (a["count"] - 1) + size, offset=off)
        rows = np.lib.stride_tricks.as_strided(raw, shape=(a["count"], size), strides=(stride, 1))
        return np.ascontiguousarray(rows).view(dtype).reshape(a["count"], width)

    index_types = {5121: "<u1", 5123: "<u2", 5125: "<u4"}
    out = []

    def walk(i, parent_m, ref):
        n = g["nodes"][i]
        m = parent_m @ matrix(n)
        if "mesh" in n:
            for p in g["meshes"][n["mesh"]]["primitives"]:
                v = accessor(p["attributes"]["POSITION"], 3, "<f4").astype(np.float64)
                v = (np.c_[v, np.ones(len(v))] @ m.T)[:, :3] * 1000.0
                if "indices" in p:
                    a = g["accessors"][p["indices"]]
                    tri = accessor(p["indices"], 1, index_types[a["componentType"]]).reshape(-1, 3).astype(np.int64)
                else:
                    tri = np.arange(len(v)).reshape(-1, 3)
                mi = p.get("material")
                out.append((ref, None if mi is None else g["materials"][mi], v[:, [0, 2, 1]], tri))  # glTF x, y-up, z -> board x, y, h
        for c in n.get("children", []):
            walk(c, m, ref)

    for r in g["scenes"][0]["nodes"]:
        root = g["nodes"][r]
        for c in root.get("children", []):
            name = g["nodes"][c].get("name") or ""
            walk(c, matrix(root), None if name.startswith("=>") else name)
    return out


def model_fallbacks(text: str, board_dir: Path) -> str:
    """Where a footprint's model file does not exist (KiCad names library models it does
    not ship), use board/models.json's model for that footprint, as applyModels.py would."""
    table = {k: v for k, v in json.loads((board_dir / "models.json").read_text(encoding="utf-8")).items()
             if not k.startswith("_")}
    starts = [m.start() for m in re.finditer(r"\n\t\(footprint \"", text)] + [len(text)]
    out, done = [text[:starts[0]]], []
    for a, b in zip(starts, starts[1:]):
        block = text[a:b]
        lib = re.match(r'\n\t\(footprint "([^"]+)"', block).group(1)
        model = re.search(r'\(model "([^"]+)"', block)
        spec = table.get(lib)
        if model and spec and not Path(model.group(1)).exists():
            if any(spec["offset"]) or any(spec["rotate"]):
                raise SystemExit(f"{lib}: models.json has an offset/rotation; apply it with scripts/applyModels.py")
            path = spec["path"].replace("${KIPRJMOD}", str(board_dir))
            block = block.replace(model.group(0), f'(model "{path}"', 1)
            done.append(lib.split(":")[-1])
        out.append(block)
    if done:
        print(f"  models.json stands in for missing models: {', '.join(sorted(set(done)))}")
    return "".join(out)


def parts_geometry(board_path: Path, tmp: Path) -> dict[str, np.ndarray]:
    """Vertices of every modelled part (no board body), by reference."""
    parts: dict[str, list] = {}
    for ref, _, v, _ in kicad_glb(board_path, tmp, "--no-board-body"):
        if ref:
            parts.setdefault(ref, []).append(v)
    return {k: np.vstack(v) for k, v in parts.items()}


def board_model(board_path: Path, tmp: Path, thickness: float) -> tuple[dict, bytes]:
    """The populated board for the preview: KiCad's own export (body, soldermask, pads,
    every part model), no silkscreen (the print is drawn by the page, per scheme).
    Packed per material: uint16 positions quantised over the board's box, uint16
    indices, in model space (X east, Y north, Z = 0 at the board's front face)."""
    groups: dict[tuple, list] = {}
    for ref, mat, v, tri in kicad_glb(board_path, tmp, "--include-soldermask", "--include-pads"):
        pb = (mat or {}).get("pbrMetallicRoughness", {})
        r, g_, b_, a = pb.get("baseColorFactor", [0.55, 0.55, 0.55, 1.0])
        if ref is None:
            # KiCad gives only the board layers explicit metal/roughness
            role = "pads" if pb.get("metallicFactor", 0) > 0.5 else "mask" if a < 1 else "body"
        else:
            role = "part"
        key = (role, round(r, 3), round(g_, 3), round(b_, 3))
        model = np.c_[v[:, 0], -v[:, 1], v[:, 2] - thickness]
        groups.setdefault(key, []).append((model, tri))
    allp = np.vstack([P for lst in groups.values() for P, _ in lst])
    lo, hi = allp.min(0), allp.max(0)
    scale = (hi - lo) / 65535.0
    chunks, blobs, offset = [], [], 0
    for (role, r, g_, b_), lst in groups.items():
        pending: list = []

        def flush():
            nonlocal offset, pending
            if not pending:
                return
            P = np.vstack([q for q, _ in pending])
            base, idx = 0, []
            for q, t in pending:
                idx.append(t + base)
                base += len(q)
            I = np.vstack(idx).astype("<u2")
            pq = np.round((P - lo) / scale).astype("<u2").tobytes()
            ib = I.tobytes()
            pad = (-len(pq)) % 4
            chunks.append({"role": role, "color": [r, g_, b_], "vo": offset, "vc": len(P),
                           "io": offset + len(pq) + pad, "ic": int(I.size)})
            blobs.extend([pq, b"\0" * pad, ib, b"\0" * ((-len(ib)) % 4)])
            offset += len(pq) + pad + len(ib) + (-len(ib)) % 4
            pending = []

        count = 0
        for P, T in lst:
            if len(P) > 65535:
                raise SystemExit(f"a {role} primitive has {len(P)} vertices; split it before packing")
            if count + len(P) > 65535:
                flush()
                count = 0
            pending.append((P, T))
            count += len(P)
        flush()
    mask_back = float(min(P[:, 2].min() for (role, *_), lst in groups.items() if role == "mask" for P, _ in lst))
    header = {"lo": lo.tolist(), "scale": scale.tolist(), "chunks": chunks, "maskBack": mask_back}
    return header, b"".join(blobs)


# ───────────────────────────────────────────────────────────── print artwork


# ──────────────────────────────────────────────────────────────── solids


def rr_prism(x0, y0, x1, y1, r, z0, z1):
    """Rounded-rectangle prism from board coordinates (y south) to model space (Y north)."""
    sk = RectangleRounded(x1 - x0, y1 - y0, min(r, (x1 - x0) / 2 - 1e-3, (y1 - y0) / 2 - 1e-3))
    return Pos((x0 + x1) / 2, -(y0 + y1) / 2, z0) * extrude(sk, amount=z1 - z0)


def rr_prism_drafted(x0, y0, x1, y1, r, z_wide, z_narrow, draft):
    """Rounded-rectangle prism whose sides lean in by `draft` degrees from z_wide to z_narrow."""
    sk = RectangleRounded(x1 - x0, y1 - y0, min(r, (x1 - x0) / 2 - 1e-3, (y1 - y0) / 2 - 1e-3))
    part = extrude(sk, amount=abs(z_narrow - z_wide), taper=draft)   # narrows toward +Z
    if z_narrow < z_wide:
        part = Rot(180, 0, 0) * part   # the outline is symmetric, so flipping keeps it in place
    return Pos((x0 + x1) / 2, -(y0 + y1) / 2, z_wide) * part


def box(x0, y0, x1, y1, z0, z1):
    return Pos((x0 + x1) / 2, -(y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def outline(b, grow):
    return (-grow, -grow, b["w"] + grow, b["h"] + grow, b["r"] + grow)


def wall_cut(side, b, along, z, size_along, size_z, r):
    """Rounded opening through one wall. along: centre along the wall (x for N/S, y for E/W)."""
    depth = 2 * (GAP + WALL) + 2
    sk = RectangleRounded(size_along, size_z, min(r, size_along / 2 - 1e-3, size_z / 2 - 1e-3))
    if side in ("S", "N"):
        y = b["h"] + GAP + WALL / 2 if side == "S" else -(GAP + WALL / 2)
        return Pos(along, -y, z) * Rot(90, 0, 0) * extrude(sk, amount=depth / 2, both=True)
    x = -(GAP + WALL / 2) if side == "W" else b["w"] + GAP + WALL / 2
    return Pos(x, -along, z) * Rot(0, 90, 0) * Rot(0, 0, 90) * extrude(sk, amount=depth / 2, both=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    b = read_board(BOARD)
    W, H, T = b["w"], b["h"], b["t"]
    with tempfile.TemporaryDirectory() as tmp:
        parts = parts_geometry(BOARD, Path(tmp))

    back = {ref: v for ref, v in parts.items() if v[:, 2].min() < -0.01}
    unmodelled = sorted(ref for ref, f in b["fps"].items()
                        if f["layer"] == "B.Cu" and ref not in parts and not f["lib"].startswith(("TestPoint", "MountingHole")))
    surprise = [r for r in unmodelled if r not in UNMODELLED_MAX_H]
    if surprise:
        raise SystemExit(f"back-side parts with no 3D model and no height bound: {surprise}")

    cam = b["zones"]["no parts: Camera module"]
    deepest_ref, deepest = max(((r, -v[:, 2].min()) for r, v in back.items()), key=lambda kv: kv[1])
    depth = max(deepest, CAM_HEAD_H, *(UNMODELLED_MAX_H[r] for r in unmodelled if r in UNMODELLED_MAX_H))
    driver = "camera head" if depth == CAM_HEAD_H else deepest_ref

    # Z levels
    z_back = -T
    z_floor_in = z_back - depth - BACK_CLEAR
    z_floor_out = z_floor_in - FLOOR
    z_split = 0.0
    z_cell = (FOAM_T, FOAM_T + CELL[2])
    z_panel = (z_cell[1], z_cell[1] + PANEL_T)
    z_lip = z_panel[1] + PANEL_GAP
    z_top = z_lip + LIP
    draft_back = math.degrees(math.atan(DRAFT_MM / 2 / (z_split - z_floor_out)))
    draft_front = math.degrees(math.atan(DRAFT_MM / 2 / (z_top - z_split)))
    if max(draft_back, draft_front) >= MAX_UNSUPPORTED:
        raise SystemExit("draft: the walls would need supports when printed")
    if WALL - DRAFT_MM / 2 < MIN_WALL:
        raise SystemExit(f"draft: the wall thins to {WALL - DRAFT_MM / 2:.2f} mm at the back/front, under {MIN_WALL}")

    px0, py0, pw, ph = panel_outline()
    panel = (px0, py0, px0 + pw, py0 + ph)
    va = (px0 + PANEL_VA[0], py0 + PANEL_VA[1], px0 + PANEL_VA[0] + PANEL_VA[2], py0 + PANEL_VA[1] + PANEL_VA[3])
    win = (va[0] - WINDOW_MARGIN, va[1] - WINDOW_MARGIN, va[2] + WINDOW_MARGIN, va[3] + WINDOW_MARGIN)

    out_o = outline(b, GAP + WALL)
    in_o = outline(b, GAP)
    tongue_o = outline(b, GAP + TONGUE_T)
    skirt_o = outline(b, GAP + TONGUE_T + TONGUE_CLR)

    report = []

    # ── openings from whatever crosses the board edge ──
    openings = []
    plunger_pts = None   # the power button's plunger: its opening is the flex tab, checked below
    for ref, v in parts.items():
        beyond = {
            "W": v[v[:, 0] < -0.05], "E": v[v[:, 0] > W + 0.05],
            "N": v[v[:, 1] < -0.05], "S": v[v[:, 1] > H + 0.05],
        }
        for side, pts in beyond.items():
            if not len(pts) or (ref, side) in [(o[0], o[1]) for o in openings]:
                continue
            zlo, zhi = z_back + pts[:, 2].min(), z_back + pts[:, 2].max()
            if ref.startswith("J1"):
                mouth = v[v[:, 1] > H - 0.5]
                cx = (mouth[:, 0].min() + mouth[:, 0].max()) / 2
                cz = z_back + (mouth[:, 2].min() + mouth[:, 2].max()) / 2
                openings.append((ref, side, wall_cut(side, b, cx, cz, USB_PLUG[0], USB_PLUG[1], USB_PLUG[2])))
                report.append(f"USB-C opening {USB_PLUG[0]} x {USB_PLUG[1]} at x {cx:.2f}, Z {cz:.2f}")
            elif ref.startswith("SW_AIR"):
                ya, yb = pts[:, 1].min(), pts[:, 1].max()
                along = (ya + yb) / 2
                size_y = yb - ya + 2 * LEVER_TRAVEL + 2 * LEVER_CLR
                size_z = zhi - zlo + 2 * LEVER_CLR
                openings.append((ref, side, wall_cut(side, b, along, (zlo + zhi) / 2, size_y, size_z, 0.6)))
                report.append(f"airgap lever slot {size_y:.1f} x {size_z:.1f} at y {along:.2f}")
            elif ref == "SW_PWR" and side == "E":
                plunger_pts = pts
            else:
                raise SystemExit(f"{ref} crosses the {side} board edge and has no opening rule")

    # ── power button: plunger from the land's fab outline ──
    fab = fab_world(b["fps"]["SW_PWR"])
    tip_x = fab[:, 0].max()
    if tip_x < W - 1.0:
        raise SystemExit(f"SW_PWR plunger tip at x {tip_x:.2f}: expected it at the east edge")
    plunger = fab[fab[:, 0] > tip_x - 0.9]
    py_c = (plunger[:, 1].min() + plunger[:, 1].max()) / 2
    pz = z_back - SKRT_ACTUATOR_H
    tab_len, tab_h, slot_w, tab_t = TAB
    hinge_y, free_y = py_c - tab_len + NUB_D, py_c + NUB_D
    wx_in, wx_out = W + GAP, W + GAP + WALL
    tab_cuts = [
        box(wx_in - 0.5, hinge_y, wx_out + 0.5, free_y + slot_w, pz + tab_h / 2, pz + tab_h / 2 + slot_w),
        box(wx_in - 0.5, hinge_y, wx_out + 0.5, free_y + slot_w, pz - tab_h / 2 - slot_w, pz - tab_h / 2),
        box(wx_in - 0.5, free_y, wx_out + 0.5, free_y + slot_w, pz - tab_h / 2 - slot_w, pz + tab_h / 2 + slot_w),
        box(wx_in - 0.5, hinge_y - 1.0, wx_out - tab_t, free_y, pz - tab_h / 2, pz + tab_h / 2),
    ]
    if plunger_pts is not None:
        mx, my_lo, my_hi = plunger_pts[:, 0].max(), plunger_pts[:, 1].min(), plunger_pts[:, 1].max()
        mz_lo, mz_hi = z_back + plunger_pts[:, 2].min(), z_back + plunger_pts[:, 2].max()
        if not (hinge_y < my_lo and my_hi < free_y and pz - tab_h / 2 < mz_lo and mz_hi < pz + tab_h / 2):
            raise SystemExit(f"SW_PWR plunger (y {my_lo:.2f}..{my_hi:.2f}, Z {mz_lo:.2f}..{mz_hi:.2f}) is outside the power tab")
        if mx > tip_x + NUB_GAP / 2:
            raise SystemExit(f"SW_PWR model's plunger reaches x {mx:.2f}, past the land's tip {tip_x:.2f}: the nub would preload it")
        report.append(f"power button model: plunger at x {mx:.2f}, inside the tab window")
    nub_x0 = tip_x + NUB_GAP
    nub_len = wx_out - tab_t - nub_x0
    nub = Pos(nub_x0 + nub_len / 2, -py_c, pz) * Rot(0, 90, 0) * Cylinder(NUB_D / 2, nub_len)
    report.append(f"draft: back and front {DRAFT_MM} mm narrower than the parting line "
                  f"({draft_back:.1f} / {draft_front:.1f} deg), wall {WALL - DRAFT_MM / 2:.2f} mm at its thinnest")
    report.append(f"power tab {tab_len} x {tab_h} in the east wall, nub {NUB_GAP} mm off the plunger tip (x {tip_x:.2f})")

    # ── shell ──
    shell = rr_prism_drafted(*out_o[:4], out_o[4], z_split, z_floor_out, draft_back)
    shell = fillet(shell.edges().group_by(Axis.Z)[0], BACK_FILLET)
    shell -= rr_prism(*in_o[:4], in_o[4], z_floor_in, z_split + 1)
    shell += rr_prism(*tongue_o[:4], tongue_o[4], z_split, z_split + TONGUE_H) - rr_prism(*in_o[:4], in_o[4], z_split - 1, z_split + TONGUE_H + 1)

    for hx, hy, _ in b["holes"]:
        boss = Pos(hx, -hy, (z_floor_in + z_back) / 2) * Cylinder(STANDOFF_R, z_back - z_floor_in + 0.02)
        nearest = math.inf
        cut_by = []
        envs = [(r, (v[:, 0].min(), v[:, 1].min(), v[:, 0].max(), v[:, 1].max())) for r, v in back.items()]
        envs.append(("camera head", tuple(cam)))
        for ref, (ex0, ey0, ex1, ey1) in envs:
            ex0, ey0, ex1, ey1 = ex0 - BOSS_PART_CLR, ey0 - BOSS_PART_CLR, ex1 + BOSS_PART_CLR, ey1 + BOSS_PART_CLR
            dx = max(ex0 - hx, 0, hx - ex1)
            dy = max(ey0 - hy, 0, hy - ey1)
            d = math.hypot(dx, dy)
            if d < STANDOFF_R:
                boss -= box(ex0, ey0, ex1, ey1, z_floor_in + 0.01, z_back + 1)   # the floor stays whole
                raw = math.hypot(max(ex0 + BOSS_PART_CLR - hx, 0, hx - ex1 + BOSS_PART_CLR),
                                 max(ey0 + BOSS_PART_CLR - hy, 0, hy - ey1 + BOSS_PART_CLR))
                nearest = min(nearest, raw - M2_SHANK_R)
                cut_by.append(ref)
        if nearest < MIN_SHANK_CLR:
            raise SystemExit(f"standoff at {hx}, {hy}: the screw passes {nearest:.2f} mm from {cut_by}")
        shell += boss
        # clearance hole, and the head's counterbore in the back face with a 45 deg roof (prints without support)
        shell -= Pos(hx, -hy, (z_floor_out + z_back) / 2) * Cylinder(CLEAR_R, z_back - z_floor_out + 2)
        shell -= Pos(hx, -hy, z_floor_out - 0.5 + (CB_DEPTH + 0.5) / 2) * Cylinder(CB_R, CB_DEPTH + 0.5)
        shell -= Pos(hx, -hy, z_floor_out + CB_DEPTH + (CB_R - CLEAR_R) / 2) * Cone(CB_R, CLEAR_R, CB_R - CLEAR_R)
        if cut_by:
            report.append(f"standoff {hx},{hy} notched for {', '.join(cut_by)}: the screw passes {nearest:.2f} mm from it")

    ccx, ccy = (cam[0] + cam[2]) / 2, (cam[1] + cam[3]) / 2
    shell -= Pos(ccx, -ccy, (z_floor_in + z_floor_out) / 2) * Cylinder(CAM_WINDOW_D / 2, FLOOR + 1)
    shell -= Pos(ccx, -ccy, z_floor_out + 0.3) * Cone(CAM_WINDOW_D / 2 + 0.6, CAM_WINDOW_D / 2, 0.6)
    for _, _, cut in openings:
        shell -= cut
    for c in tab_cuts:
        shell -= c
    shell += nub

    # ── bezel ──
    bezel = rr_prism_drafted(*out_o[:4], out_o[4], z_split, z_top, draft_front)
    bezel = fillet(bezel.edges().group_by(Axis.Z)[-1], FRONT_FILLET)
    bezel -= rr_prism(*in_o[:4], in_o[4], z_split - 1, z_lip)
    bezel -= rr_prism(*skirt_o[:4], skirt_o[4], z_split - 1, z_split + TONGUE_H + TONGUE_CLR)
    bezel -= rr_prism(*win, 0.8, z_lip - 1, z_top + 1)
    # Solid where the screen is not: fills along the screen's edges locate it, the chin
    # is solid down to the board, with windows where the display ribbons drop through
    # the board's slots.
    glass = (panel[0] - SCREEN_CLR, panel[1] - SCREEN_CLR, panel[2] + SCREEN_CLR, panel[3] + SCREEN_CLR)
    open_ = box(*glass, z_split - 1, z_lip + 0.01)
    for sx0, sy0, sx1, sy1 in b["slots"]:
        open_ += box(sx0 - RIBBON_CLR, glass[3] - 0.01, sx1 + RIBBON_CLR, sy1 + RIBBON_CLR, z_split - 1, z_lip + 0.01)
    bezel += rr_prism(*in_o[:4], in_o[4], z_split, z_lip + 0.01) - open_

    # Bosses at the board's holes, from the board's front up to the screen's back. Under
    # the screen their underside rises 45 deg from the fills (a roof over the screen's
    # corner), so the bezel prints face down without supports.
    gw, gh = glass[2] - glass[0], glass[3] - glass[1]
    roof = Rot(180, 0, 0) * extrude(Rectangle(gw, gh), amount=z_panel[0] - z_split + 0.02, taper=45)
    roof = Pos((glass[0] + glass[2]) / 2, -(glass[1] + glass[3]) / 2, z_panel[0]) * roof
    roof += box(*glass, z_panel[0] - 0.01, z_lip + 0.01)
    pilot_top = z_panel[0] - PILOT_TOP_CLR
    engage = []
    for hx, hy, _ in b["holes"]:
        boss = Pos(hx, -hy, (z_split + z_panel[0]) / 2) * Cylinder(BOSS_R, z_panel[0] - z_split)
        bezel += boss - roof
        bezel -= Pos(hx, -hy, (z_split - 0.01 + pilot_top) / 2) * Cylinder(PILOT_R, pilot_top - z_split + 0.01)
        # thread in plastic: up to the roof at the pilot's far side, at most the pilot's depth
        inside = glass[0] < hx < glass[2] and glass[1] < hy < glass[3]
        far = min(hx + PILOT_R - glass[0], glass[2] - hx + PILOT_R, hy + PILOT_R - glass[1], glass[3] - hy + PILOT_R) if inside else 0
        engage.append(min(pilot_top, z_panel[0] - far) - z_split)

    # The screw: the longest stock M2 whose tip stays in the pilot.
    seat = z_floor_out + CB_DEPTH
    fits = [L for L in SCREW_LENGTHS if seat + L <= pilot_top]
    if not fits:
        raise SystemExit("no stock M2 length fits between the counterbore and the pilot")
    screw = max(fits)
    thread = min(min(engage), seat + screw - z_split)
    if thread < MIN_ENGAGE:
        raise SystemExit(f"M2x{screw}: only {thread:.2f} mm of thread in the bezel's bosses")
    report.append(f"screws: 4x M2x{screw} pan head from the back, {thread:.1f} mm of thread in the bezel's bosses")
    for _, _, cut in openings:
        bezel -= cut

    # ── checks that the geometry, not prose, has to satisfy ──
    cell_rect = ((panel[0] + panel[2] - CELL[0]) / 2, (panel[1] + panel[3] - CELL[1]) / 2)
    cell_box = (*cell_rect, cell_rect[0] + CELL[0], cell_rect[1] + CELL[1])
    if win[0] < panel[0] or win[2] > panel[2] or win[1] < panel[1] or win[3] > panel[3]:
        raise SystemExit("display window is larger than the glass")
    assert shell.is_valid and bezel.is_valid, "invalid solid"

    size = (out_o[2] - out_o[0], out_o[3] - out_o[1], z_top - z_floor_out)
    print_3mf(shell, bezel, OUT / "case.3mf")
    for name, part in (("shell", shell), ("bezel", bezel)):
        export_step(part, str(OUT / f"{name}.step"))
        export_stl(part, str(OUT / f"{name}.stl"), tolerance=MESH_TOL, angular_tolerance=0.2)
        glb = OUT / f"{name}.glb"
        export_gltf(part, str(glb), binary=True, linear_deflection=0.05, angular_deflection=0.3)
        gltf_json(glb, WEB / f"{name}.json")
        glb.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        header, blob = board_model(BOARD, Path(tmp), T)
    header["bin"] = base64.b64encode(blob).decode()
    (WEB / "board.json").write_text(json.dumps(header, separators=(",", ":")), encoding="utf-8")

    fbox = flame_art.box(flame_art.facets())
    at = flame_art.placement(W, H, fbox)
    flame = {"paths": flame_art.facet_paths(), "box": [round(v, 3) for v in fbox],
             "at": [round(v, 3) for v in at], "gap": flame_art.GAP}
    fw, fh = at[2] - at[0], at[3] - at[1]
    report.append(f"flame {fw:.1f} x {fh:.1f} mm across the back, mask back face at Z {header['maskBack']:.3f}")


    downloads = write_downloads()
    data = {
        "generated_from": "board/wallet.kicad_pcb",
        "size_mm": [round(v, 1) for v in size],
        "board": {"w": W, "h": H, "r": b["r"], "t": T, "slots": b["slots"], "holes": b["holes"]},
        "z": {"floorOut": z_floor_out, "floorIn": z_floor_in, "boardBack": z_back, "split": z_split,
              "cell": list(z_cell), "panel": list(z_panel), "lip": z_lip, "top": z_top},
        "panel": list(panel), "viewingArea": list(va),
        "cell": list(cell_box),
        "camera": cam,
        "depthDriver": driver,
        "parts": {"shell": "enclosure/shell.json", "bezel": "enclosure/bezel.json"},
        "board3d": "enclosure/board.json",
        "screws": {"length": screw, "d": 2.0, "head": [3.8, 1.6], "seat": seat,
                   "holes": [[hx, hy] for hx, hy, _ in b["holes"]]},
        "maskBack": header["maskBack"],
        "flame": flame,
        "schemes": PRINT_SCHEMES,
        "downloads": downloads,
    }
    (WEB / "enclosure.json").write_text(json.dumps(data, indent=1))

    print(f"case {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm "
          f"(back cavity {depth + BACK_CLEAR:.1f} mm, set by the {driver}; deepest modelled part {deepest_ref} {deepest:.2f} mm)")
    for line in report:
        print(" ", line)
    print(f"  unmodelled back parts (height bounds): {', '.join(unmodelled) or 'none'}")
    print(f"wrote {OUT.relative_to(HERE)}/ and {WEB.relative_to(HERE)}/")


def gltf_json(glb: Path, dest: Path):
    """GLB bytes as base64 in a .json file: the artifact host serves .json but not .glb,
    and its CSP blocks fetching data: URIs, so the page parses the GLB in memory."""
    dest.write_text(json.dumps({"glb": base64.b64encode(glb.read_bytes()).decode()}))


def print_3mf(shell, bezel, dest: Path):
    """Both parts laid out for printing: the shell on its back, the bezel face down beside it."""
    sb, bb = shell.bounding_box(), bezel.bounding_box()
    flat_shell = Pos(-sb.min.X, -sb.min.Y, -sb.min.Z) * shell
    turned = Rot(180, 0, 0) * bezel
    tb = turned.bounding_box()
    flat_bezel = Pos(sb.size.X + 8 - tb.min.X, -tb.min.Y, -tb.min.Z) * turned
    m = Mesher()
    m.add_shape(flat_shell, linear_deflection=MESH_TOL, angular_deflection=0.2, part_number="shell")
    m.add_shape(flat_bezel, linear_deflection=MESH_TOL, angular_deflection=0.2, part_number="bezel")
    m.write(str(dest))


def write_downloads() -> dict:
    """The DIY files for the page. The host serves text only: CSVs go as they are, STEP as
    .txt, binaries (zip, STL, 3MF) as base64 .txt; the page rebuilds each file and hands it
    to the viewer's downloads capability."""
    files_dir = WEB / "files"
    if files_dir.exists():
        shutil.rmtree(files_dir)
    files_dir.mkdir()

    def entry(label, src: Path):
        raw = src.read_bytes()
        if src.suffix == ".csv":
            part, b64 = src.name, False
            (files_dir / part).write_bytes(raw)
        elif src.suffix in (".step", ".stp"):
            part, b64 = src.name + ".txt", False
            (files_dir / part).write_bytes(raw)
        else:
            part, b64 = src.name + ".b64.txt", True
            (files_dir / part).write_text(base64.b64encode(raw).decode())
        return {"label": label, "filename": src.name, "bytes": len(raw), "part": f"enclosure/files/{part}", "b64": b64}

    # The viewer saves only allowlisted types (zip, csv, ...), so the case goes as one zip.
    case_zip = OUT / "ambire-signer-case.zip"
    with zipfile.ZipFile(case_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("case.3mf", "shell.stl", "bezel.stl", "shell.step", "bezel.step"):
            z.write(OUT / name, name)
        z.writestr("README.txt", CASE_README)
    return {"fabs": {fab: [entry(l, p) for l, p in files] for fab, files in FAB_SETS.items()},
            "case": [entry("Case: 3MF, STL and STEP (zip)", case_zip)]}


if __name__ == "__main__":
    sys.exit(main())
