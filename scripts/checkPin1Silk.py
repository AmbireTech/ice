#!/usr/bin/env python3
"""Every polarized part's silkscreen shows which way it goes.

    KPY scripts/checkPin1Silk.py [board/wallet.kicad_pcb]

JLC holds an order and asks by mail when its engineers cannot tell a part's
orientation from the board's silkscreen (2026-11-04: the LTC3539 boost U2 had no
silk at all; the joystick SW_JOY's pin-1 dot was 0.12 mm wide, below what prints,
and sat as far from pad 3 as from pad 1). This check looks at each part the way
they do:

- the land: every turn (90/180/270 degrees) that maps the part's pads onto
  themselves but moves pad 1 onto a pad with another number is a way to place it
  wrongly that the copper alone does not rule out;
- the silk: the part's own silk shapes on its face (texts left out: a reference
  label is no orientation mark), minus the solder-mask openings (JLC clips silk
  there), minus lines under 0.10 mm and specks under 0.05 mm2 (they do not print
  legibly; KiCad's 0.12 mm silk lines do, JLC has never asked about those);
- the part fails when, for such a turn, no printable area of that silk (at least a
  speck's 0.05 mm2) lands off the silk turned, i.e. the silk looks the same turned
  (or there is none).

Two-pad capacitors, resistors, inductors, fuses, thermistors and solder jumpers
(C_, R_, L_, F_, RT_, JP_) are non-polar and skipped.
Exit 1 on any failure.
"""
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NONPOLAR_PREFIX = ("C_", "R_", "L_", "F_", "RT_", "JP_")   # two-pad: caps, resistors, inductors, fuses, thermistors, solder jumpers
MIN_SILK = pcbnew.FromMM(0.10)    # thinner silk lines do not print
MIN_ISLAND = 0.05                 # mm2: a smaller silk island is a speck (SW_JOY's old 0.12 mm dot: 0.011)
GRID = pcbnew.FromMM(0.05)        # sampling step over the silk
TOL = pcbnew.FromMM(0.10)         # a turned silk point "lands on silk" within this
MAXERR = pcbnew.FromMM(0.005)


def pin1(fp):
    names = {p.GetNumber() for p in fp.Pads()}
    return next((n for n in ("1", "A1") if n in names), None)


def turns_that_misplace(fp, one):
    """Turns (degrees) mapping the pad set onto itself while moving pad `one` to another number."""
    pads = [(p.GetBoundingBox(), p.GetNumber()) for p in fp.Pads()]
    cx = sum(b.GetCenter().x for b, _ in pads) / len(pads)
    cy = sum(b.GetCenter().y for b, _ in pads) / len(pads)
    tol = pcbnew.FromMM(0.05)
    bad = []
    for deg in (90, 180, 270):
        ok, moved = True, False
        for b, num in pads:
            x, y = b.GetCenter().x - cx, b.GetCenter().y - cy
            tx, ty = {90: (-y, x), 180: (-x, -y), 270: (y, -x)}[deg]
            w, h = (b.GetHeight(), b.GetWidth()) if deg != 180 else (b.GetWidth(), b.GetHeight())
            hit = [n for b2, n in pads
                   if abs(b2.GetCenter().x - cx - tx) < tol and abs(b2.GetCenter().y - cy - ty) < tol
                   and abs(b2.GetWidth() - w) < tol and abs(b2.GetHeight() - h) < tol]
            if not hit:
                ok = False
                break
            if num == one and one not in hit:
                moved = True
        if ok and moved:
            bad.append(deg)
    return bad, (cx, cy)


def printed_silk(fp):
    """The part's silk shapes on its face, minus mask openings and sub-printable features."""
    silk = pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS
    mask = pcbnew.B_Mask if fp.IsFlipped() else pcbnew.F_Mask
    poly = pcbnew.SHAPE_POLY_SET()
    for g in fp.GraphicalItems():
        if isinstance(g, pcbnew.PCB_SHAPE) and g.GetLayer() == silk:
            g.TransformShapeToPolygon(poly, silk, 0, MAXERR, pcbnew.ERROR_INSIDE)
    if poly.OutlineCount() == 0:
        return poly
    poly.Simplify()
    holes = pcbnew.SHAPE_POLY_SET()
    for p in fp.Pads():
        if p.IsOnLayer(mask):
            p.TransformShapeToPolygon(holes, mask, p.GetSolderMaskExpansion(mask), MAXERR, pcbnew.ERROR_OUTSIDE)
    if holes.OutlineCount():
        holes.Simplify()
        poly.BooleanSubtract(holes)
    # opening: what survives shrinking by half the smallest printed line, grown back
    poly.Deflate(MIN_SILK // 2 - 1, pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, MAXERR)
    poly.Inflate(MIN_SILK // 2 - 1, pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, MAXERR)
    for i in reversed(range(poly.OutlineCount())):
        if poly.Outline(i).Area() / 1e12 < MIN_ISLAND:
            poly.DeletePolygon(i)
    return poly


def samples(poly):
    pts = []
    for i in range(poly.OutlineCount()):
        b = poly.Outline(i).BBox()
        for x in range(b.GetLeft(), b.GetRight() + 1, GRID):
            for y in range(b.GetTop(), b.GetBottom() + 1, GRID):
                v = pcbnew.VECTOR2I(x, y)
                if poly.Contains(v):
                    pts.append((x, y))
    return pts


def check(fp):
    """None if fine, else why."""
    ref = fp.GetReference()
    if len(fp.Pads()) < 2 or (ref.startswith(NONPOLAR_PREFIX) and len(fp.Pads()) == 2):
        return None
    one = pin1(fp)
    if one is None:
        return None
    turns, (cx, cy) = turns_that_misplace(fp, one)
    if not turns:
        return None
    poly = printed_silk(fp)
    pts = samples(poly) if poly.OutlineCount() else []
    if not pts:
        return f"land fits turned {'/'.join(map(str, turns))} deg and no silk prints"
    grown = pcbnew.SHAPE_POLY_SET(poly)
    grown.Inflate(TOL, pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, MAXERR)
    same = []
    for deg in turns:
        off = 0
        for x, y in pts:
            dx, dy = x - cx, y - cy
            tx, ty = {90: (-dy, dx), 180: (-dx, -dy), 270: (dy, -dx)}[deg]
            off += not grown.Contains(pcbnew.VECTOR2I(int(cx + tx), int(cy + ty)))
        off_mm2 = off * (GRID / 1e6) ** 2
        if off_mm2 < MIN_ISLAND:
            same.append(f"{deg} deg (only {off_mm2:.3f} mm2 of it lands off itself)")
    return f"silk looks the same turned {', '.join(same)}" if same else None


def main():
    path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "board/wallet.kicad_pcb"))
    board = pcbnew.LoadBoard(path)
    bad, n = [], 0
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        n += 1
        why = check(fp)
        if why:
            bad.append(f"{fp.GetReference()} ({fp.GetValue()}): {why}")
    for b in bad:
        print(f"ERROR no pin-1 mark JLC can read: {b}")
    print(f"pin-1 marks: {n} parts, " + (f"{len(bad)} without a readable orientation: "
                                          + ", ".join(b.split(' ')[0] for b in bad) if bad else "every polarized part's silk shows its orientation"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
