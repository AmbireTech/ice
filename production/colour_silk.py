#!/usr/bin/env python3
"""Write JLC's bottom colour-silkscreen file (.FCBS) for the UV print, from the board itself.

    KPY colour_silk.py <board.kicad_pcb> <print.jpeg> <out.FCBS> [--plain out.svg]

JLC's multi-colour silkscreen takes an EasyEDA Pro file: an SVG holding the print
image, a white base and the solder-mask openings carved out of both (so no ink
lands on a pad), encrypted to JLC's public key. TransJLC (HalfSweet/TransJLC,
which mirrors EasyEDA's own files) writes the same thing, but its mask parser
drops KiCad's rounded-rectangle corner radius and skips custom pads, so its
openings came out about half size: ink on every pad. Here the openings are the
board's own pad and via shapes on B.Mask with their mask expansion, checked
against KiCad's plot of B.Mask before anything is written.

Frame (as in EasyEDA/TransJLC): 10-mil units, Gerber axes (origin at the
outline's bottom-left, y up) with y negated; the bottom drawing sits in a group
mirrored about the board centre, so the image is the back as seen from behind.
File: RSA-OAEP(SHA-256) wrapped AES key (256 B), wrapped nonce (256 B), then
AES-128-GCM ciphertext with its 16-byte tag (16-byte nonce).
"""
import base64
import io
import os
import subprocess
import sys
import tempfile

import pcbnew
from PIL import Image, ImageChops, ImageDraw, ImageFilter

JLC_PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzPtuUqJecaR/wWtctGT8
QuVslmDH3Ut3s8c1Ls4A+M9rwpeLjgDUqfcrSrTHBrl5k/dOeJEWMeNF7STWS5jo
WZE0H60cvf2bhormC9S6CRwq4Lw0ua0YQMo66R/qCtLVa5w6WkaPCz4b0xaHWtej
JH49C0T67rU2DkepXuMPpwNCflMU+WgEQioZEldUTD6gYpu2U5GrW4AE0AQiIo+j
e7tgN8PlBMbMaEfu0LokZyth1ugfuLAgyogWnedAegQmPZzAUe36Sni94AsDlhxm
mjFl+WQZzD3MclbEY6KQB5XL8zCR/J6pCUUwfHantLxY/gQi0XJG5hWWtDyH/fR2
lwIDAQAB
-----END PUBLIC KEY-----"""
CLIP_MARGIN, EXPAND = 0.8374, 0.5          # 10-mil units, as in EasyEDA's files
MATCH_MIN = 0.97                           # share of KiCad's plotted mask openings ours must cover


def mil10(mm):
    return mm / 0.254


def outline_bounds(board):
    xs, ys = [], []
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            bb, w = d.GetBoundingBox(), (d.GetWidth() if hasattr(d, "GetWidth") else 0)
            xs += [(bb.GetLeft() + w / 2) / 1e6, (bb.GetRight() - w / 2) / 1e6]
            ys += [(bb.GetTop() + w / 2) / 1e6, (bb.GetBottom() - w / 2) / 1e6]
    return min(xs), min(ys), max(xs), max(ys)


def mask_polygons(board):
    """B.Mask openings as lists of (x, y) mm in KiCad coordinates."""
    err = pcbnew.FromMM(0.005)
    polys = []

    def take(sps):
        for i in range(sps.OutlineCount()):
            o = sps.Outline(i)
            polys.append([(o.CPoint(k).x / 1e6, o.CPoint(k).y / 1e6) for k in range(o.PointCount())])

    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.IsOnLayer(pcbnew.B_Mask):
                sps = pcbnew.SHAPE_POLY_SET()
                pad.TransformShapeToPolygon(sps, pcbnew.B_Mask, pad.GetSolderMaskExpansion(pcbnew.B_Mask), err,
                                            pcbnew.ERROR_OUTSIDE)
                take(sps)
    for v in board.GetTracks():
        if v.GetClass() == "PCB_VIA" and v.IsOnLayer(pcbnew.B_Mask):
            sps = pcbnew.SHAPE_POLY_SET()
            v.TransformShapeToPolygon(sps, pcbnew.B_Mask, v.GetSolderMaskExpansion(), err, pcbnew.ERROR_OUTSIDE)
            take(sps)
    return polys


def drill_holes(board):
    """Plated and unplated hole shapes of pads (the mounting holes, the USB-C shell slots)."""
    out = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetDrillSizeX() > 0:
                sps = pcbnew.SHAPE_POLY_SET()
                if pad.TransformHoleToPolygon(sps, 0, pcbnew.FromMM(0.005), pcbnew.ERROR_OUTSIDE):
                    for i in range(sps.OutlineCount()):
                        o = sps.Outline(i)
                        out.append([(o.CPoint(k).x / 1e6, o.CPoint(k).y / 1e6) for k in range(o.PointCount())])
    return out


def check_against_kicad(board_path, polys, bounds, px_per_mm=40, holes=()):
    """Rasterise ours and KiCad's own B.Mask plot in the same frame; return (covered, extra) shares.
    KiCad's plot leaves drilled holes open and rasterises a pixel smaller: holes count as
    openings and one pixel of rim is allowed, so only a real mismatch fails."""
    x0, y0, x1, y1 = bounds
    W, H = int((x1 - x0) * px_per_mm), int((y1 - y0) * px_per_mm)
    ours = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(ours)
    for p in polys:
        dr.polygon([((x - x0) * px_per_mm, (y - y0) * px_per_mm) for x, y in p], fill=255)
    with tempfile.TemporaryDirectory() as td:
        svg = os.path.join(td, "mask.svg")
        subprocess.run(["kicad-cli", "pcb", "export", "svg", "-l", "B.Mask", "--mode-single", "--exclude-drawing-sheet",
                        "--page-size-mode", "2", "--black-and-white", "-o", svg, board_path],
                       check=True, capture_output=True)
        png = os.path.join(td, "mask.png")
        subprocess.run(["rsvg-convert", "-w", str(W), "-h", str(H), "-b", "white", "-o", png, svg], check=True)
        kicad = ImageChops.invert(Image.open(png).convert("L")).point(lambda v: 255 if v > 128 else 0)
    hd = ImageDraw.Draw(kicad)
    for p in holes:
        hd.polygon([((x - x0) * px_per_mm, (y - y0) * px_per_mm) for x, y in p], fill=255)
    kicad_rim = kicad.filter(ImageFilter.MaxFilter(3))
    k, o = kicad.getdata(), ours.getdata()
    kn = sum(1 for v in k if v)
    both = sum(1 for a, b in zip(k, o) if a and b)
    extra = sum(1 for a, b in zip(kicad_rim.getdata(), o) if b and not a)
    return both / max(kn, 1), extra / max(sum(1 for v in o if v), 1)


def build_svg(bounds, image_bytes, image_size, polys):
    x0, y0, x1, y1 = bounds
    oy = y1                                          # Gerber origin: outline bottom-left, y up
    gx = lambda x: mil10(x - x0)
    gy = lambda y: -mil10(oy - y)                    # Gerber y, negated
    min_x, max_x, min_y, max_y = gx(x0), gx(x1), gy(y0), gy(y1)
    w, h = max_x - min_x, max_y - min_y
    cx = min_x + w / 2
    iw, ih = image_size
    marks = []
    inset = 0.0762
    for mx, my in ((x0 + inset, y0 + inset), (x0 + inset, y1 - inset), (x1 - inset, y1 - inset)):
        marks += [str(mil10(mx - x0)), str(mil10(oy - my))]
    mask_d = "".join("M " + " L ".join(f"{gx(x)} {gy(y)}" for x, y in p) + " Z " for p in polys if len(p) > 2)
    uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
    rect = lambda a, b, c, d: f"M {a} {b} L {c} {b} {c} {d} {a} {d} {a} {b} "
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg width="{x1 - x0}mm" height="{y1 - y0}mm" boardBox="{min_x} {min_y} {w} {h}" viewBox="{min_x} {min_y} {w} {h}" version="1.1" eda-version="1.6(2025-08-27)" mark-points="{' '.join(marks)}" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg">
<defs><clipPath id="clipPath0"><path d="{rect(max_x - CLIP_MARGIN, max_y - CLIP_MARGIN, min_x + CLIP_MARGIN, min_y + CLIP_MARGIN)}" id="outline0" stroke="none" style="fill-opacity:1;fill-rule:nonzero;fill:block;"/></clipPath></defs>
<defs><clipPath id="clipPath1" clip-path="url(#clipPath0)"><path d="{rect(max_x + EXPAND, min_y - EXPAND, min_x - EXPAND, max_y + EXPAND)}{mask_d}" id="solder1" stroke="none" style="fill-opacity:1;fill-rule:evenodd;clip-rule:evenodd;fill:block;"/></clipPath></defs>
<g clip-path="url(#clipPath1)" transform="scale(-1 1) translate({-2.0 * cx} 0)">
<path d="{rect(min_x - EXPAND, max_y + EXPAND, max_x + EXPAND, min_y - EXPAND)}" fill="#FFFFFF" stroke="none" stroke-width="0" id="background"/>
<image width="{iw}" height="{ih}" preserveAspectRatio="none" xlink:href="{uri}" transform="matrix({-(w / iw)} 0 0 {h / ih} {max_x} {min_y})"/>
</g>
</svg>'''


def encrypt(svg_text):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, nonce = os.urandom(16), os.urandom(16)
    pub = serialization.load_pem_public_key(JLC_PUBLIC_KEY)
    oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    return pub.encrypt(key, oaep) + pub.encrypt(nonce, oaep) + AESGCM(key).encrypt(nonce, svg_text.encode(), None)


def main():
    args = [a for a in sys.argv[1:]]
    plain = None
    if "--plain" in args:
        i = args.index("--plain"); plain = args[i + 1]; del args[i:i + 2]
    board_path, image_path, out_path = args
    board = pcbnew.LoadBoard(board_path)
    bounds = outline_bounds(board)
    polys = mask_polygons(board)
    covered, extra = check_against_kicad(board_path, polys, bounds, holes=drill_holes(board))
    print(f"{len(polys)} mask openings; cover {covered:.1%} of KiCad's plotted B.Mask, {extra:.1%} beyond it")
    if covered < MATCH_MIN or extra > 1 - MATCH_MIN:
        raise SystemExit("mask openings do not match KiCad's B.Mask plot: nothing written")
    data = open(image_path, "rb").read()
    size = Image.open(io.BytesIO(data)).size
    svg = build_svg(bounds, data, size, polys)
    if plain:
        open(plain, "w").write(svg)
    open(out_path, "wb").write(encrypt(svg))
    print(f"wrote {out_path} ({os.path.getsize(out_path) / 1e6:.1f} MB, image {size[0]}x{size[1]})")


if __name__ == "__main__":
    main()
