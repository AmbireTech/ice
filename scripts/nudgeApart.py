#!/usr/bin/env python3
"""Give crowded parts room: small moves apart, tracks dragged along (KiCad's drag).

    $KICAD_PY scripts/nudgeApart.py BOARD --refs R_TMR,R_VSET,... [--gap 0.3] [--max 0.8] [--fixed U1]

The owner, on the charger block (2026-10-23): "they just need renudging, nothing else".
Each listed part steps away from its closest same-side neighbours until their courtyards
are --gap apart, at most --max mm from where it was, 0.05 mm at a time, and only where
optimizePlacement's legality allows (board edge, slots, holes on both faces, keep-outs,
other nets' copper). Tracks ending on a moved pad move their end with it, so routed
copper stays connected. A part whose dragged tracks would then clash with other copper
is put back. --fixed parts never move. Lists every move and writes BOARD in place (a
wallet.kicad_pcb beside its .kicad_pro: pcbnew writes a default project over it
otherwise); the caller checks the result with KiCad's DRC.
"""
import argparse
import math
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import newBoard as nb  # noqa: E402
import optimizePlacement as op  # noqa: E402

STEP = 0.05


def gap(a, b):
    """Courtyard gap between boxes a and b (negative when they overlap)."""
    dx = max(b[0] - a[2], a[0] - b[2])
    dy = max(b[1] - a[3], a[1] - b[3])
    return max(dx, dy) if dx > 0 or dy > 0 else max(dx, dy)


def attached(board, fp):
    """(track, end) pairs whose end lies on one of fp's pads, same net."""
    out = []
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        for end, pt in (("start", t.GetStart()), ("end", t.GetEnd())):
            for pad in fp.Pads():
                if pad.GetNetname() == t.GetNetname() and pad.IsOnLayer(t.GetLayer()) and pad.HitTest(pt):
                    out.append((t, end))
                    break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--refs", required=True)
    ap.add_argument("--gap", type=float, default=0.3)
    ap.add_argument("--max", type=float, default=0.8)
    ap.add_argument("--fixed", default="")
    a = ap.parse_args()
    if Path(a.board).name != "wallet.kicad_pcb" or not Path(a.board).with_suffix(".kicad_pro").exists():
        raise SystemExit("BOARD must be a wallet.kicad_pcb with its wallet.kicad_pro beside it")
    board = pcbnew.LoadBoard(a.board)
    m = op.Model(board)
    refs = [r for r in a.refs.split(",") if r]
    fixed = set(a.fixed.split(",")) - {""}
    home = {r: m.parts[r].pos() for r in refs}
    drag = {r: attached(board, m.parts[r].fp) for r in refs}

    def neighbours(r):
        p = m.parts[r]
        mine = m.boxes[r]
        return [(o, gap(mine, m.boxes[o])) for o, q in m.parts.items()
                if o != r and q.side == p.side and gap(mine, m.boxes[o]) < a.gap + 0.5]

    def faults(r):
        """What keeps r illegal where it is: an empty set is a legal spot."""
        p = m.parts[r]
        box, tight = nb._box(p.fp, op.TOUCH), nb._box(p.fp, 0.1)
        out = set()
        extra = m.back_only if p.side == nb.BOT else []
        if any(nb._hits(tight, o) for o in m.forbidden + extra) or (r != "U11" and nb._hits(tight, m.ant_keepout)):
            out.add("keep-out")
        if any(nb._hits(tight, h[:4]) for h in m.holes if h[4] != r):
            out.add("hole")
        out |= {"overlap " + o for o, q in m.parts.items() if o != r and q.side == p.side and nb._hits(box, m.boxes[o])}
        if not m.copper_ok(p):
            out.add("copper")
        if not m.pads_ok(p):
            out.add("pads")
        return out

    for _ in range(60):
        moved_any = False
        for r in refs:
            if r in fixed:
                continue
            p = m.parts[r]
            near = neighbours(r)
            close = [(o, g) for o, g in near if g < a.gap]
            f0 = faults(r)
            if not close and not f0:
                continue
            cx, cy = p.pos()
            vx = vy = 0.0
            for o, g in close or near:
                ox, oy = m.parts[o].pos()
                d = math.hypot(cx - ox, cy - oy) or 1e-6
                vx += (cx - ox) / d * (a.gap - g)
                vy += (cy - oy) / d * (a.gap - g)
            n = math.hypot(vx, vy)
            g0 = min((g for _, g in near), default=9)
            best = None
            # along the push first, then the eight compass steps
            dirs = ([(vx / n, vy / n)] if n > 1e-9 else []) + \
                [(math.cos(k * math.pi / 4), math.sin(k * math.pi / 4)) for k in range(8)]
            for ux, uy in dirs:
                nx, ny = cx + ux * STEP, cy + uy * STEP
                hx, hy = home[r]
                if math.hypot(nx - hx, ny - hy) > a.max:
                    continue
                p.set(nx, ny, p.rot())
                m.moved(r)
                f1 = faults(r)
                g1 = min((g for _, g in neighbours(r)), default=9)
                # better: fewer faults, or as legal and more room
                key = (len(f1), -min(g1, a.gap))
                if (len(f1) < len(f0) or (not f1 and not f0 and g1 > g0 + 1e-6)) and (best is None or key < best[0]):
                    best = (key, nx, ny)
                p.set(cx, cy, p.rot())
                m.moved(r)
            if best:
                p.set(best[1], best[2], p.rot())
                m.moved(r)
                moved_any = True
        if not moved_any:
            break

    for r in refs:
        p = m.parts[r]
        (hx, hy), (x, y) = home[r], p.pos()
        dx, dy = x - hx, y - hy
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            continue
        for t, end in drag[r]:
            pt = t.GetStart() if end == "start" else t.GetEnd()
            q = pcbnew.VECTOR2I(pt.x + pcbnew.FromMM(dx), pt.y + pcbnew.FromMM(dy))
            t.SetStart(q) if end == "start" else t.SetEnd(q)
        p.fp.Reference().SetPosition(p.fp.GetPosition())
        g = min((g for _, g in neighbours(r)), default=9)
        left = faults(r)
        print(f"{r:10s} {'still ' + '; '.join(sorted(left)) + ', ' if left else ''}moved {math.hypot(dx, dy):.2f} mm ({dx:+.2f}, {dy:+.2f}), {len(drag[r])} track end(s) dragged, "
              f"nearest courtyard now {g:.2f} mm")
    board.Save(a.board)
    print(f"wrote {a.board}")


if __name__ == "__main__":
    main()
