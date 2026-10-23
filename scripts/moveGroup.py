#!/usr/bin/env python3
"""Move a group of parts rigidly, with the copper between them, and stretch what leaves it.

    $KICAD_PY scripts/moveGroup.py BOARD --refs U6,D_ESD,C_USBSW --dx 0 --dy 0.8 [--apply]

BOARD must be a wallet.kicad_pcb beside its .kicad_pro (pcbnew writes a default project
over it otherwise). The parts move by (dx, dy). Tracks and vias of the group's nets that
lie inside the group's footprint (courtyards + 0.3 mm) move with them, so routing between
the parts keeps its shape; a track with one end on a moved pad or a moved track keeps its
other end and stretches. Each part's new spot is checked against keep-outs, slots, holes and
other parts' courtyards; copper is left to KiCad's DRC; with --apply BOARD is written, else nothing is.
"""
import argparse
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).parent))
import newBoard as nb  # noqa: E402
import optimizePlacement as op  # noqa: E402

M, F = pcbnew.ToMM, pcbnew.FromMM


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--refs", required=True)
    ap.add_argument("--dx", type=float, default=0.0)
    ap.add_argument("--dy", type=float, default=0.0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if Path(a.board).name != "wallet.kicad_pcb" or not Path(a.board).with_suffix(".kicad_pro").exists():
        raise SystemExit("BOARD must be a wallet.kicad_pcb with its wallet.kicad_pro beside it")
    b = pcbnew.LoadBoard(a.board)
    m = op.Model(b)
    refs = a.refs.split(",")
    boxes = [nb._box(m.parts[r].fp, 0.3) for r in refs]
    inside = lambda x, y: any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes)
    nets = {p.GetNetname() for r in refs for p in m.parts[r].fp.Pads()} - {""}
    pads = [p for r in refs for p in m.parts[r].fp.Pads()]
    d = pcbnew.VECTOR2I(F(a.dx), F(a.dy))
    rigid, stretch, loose = [], [], []
    for t in b.GetTracks():
        if t.GetNetname() not in nets or t.GetNetname() in ("GND", "V3V3"):
            # plane nets: only their vias and stubs right inside the group move
            # (free stitching vias stay with the pour; a fanout via tied to a moved pad by a
            # track travels with its pad)
            if t.GetNetname() in ("GND", "V3V3") and t.Type() == pcbnew.PCB_VIA_T and inside(M(t.GetPosition().x), M(t.GetPosition().y)):
                tied = any(u.Type() != pcbnew.PCB_VIA_T and u.GetNetname() == t.GetNetname()
                           and (t.HitTest(u.GetStart()) or t.HitTest(u.GetEnd()))
                           and any(p.HitTest(u.GetStart()) or p.HitTest(u.GetEnd()) for p in pads)
                           for u in b.GetTracks())
                if tied:
                    rigid.append(t)
                else:
                    loose.append(t)
            elif t.GetNetname() in ("GND", "V3V3") and t.Type() != pcbnew.PCB_VIA_T and \
                    any(p.GetNetname() == t.GetNetname() and (p.HitTest(t.GetStart()) or p.HitTest(t.GetEnd())) for p in pads):
                rigid.append(t)     # a plane fanout stub on a moved pad
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            if inside(M(t.GetPosition().x), M(t.GetPosition().y)):
                rigid.append(t)
            continue
        s_in = inside(M(t.GetStart().x), M(t.GetStart().y))
        e_in = inside(M(t.GetEnd().x), M(t.GetEnd().y))
        if s_in and e_in:
            rigid.append(t)
        elif s_in or e_in:
            stretch.append((t, "start" if s_in else "end"))
    # placement legality at the new spots without the copper test (the group's own copper
    # moves with it; the caller judges the result with KiCad's DRC): keep-outs, slots,
    # holes on both faces, and courtyards of parts outside the group
    bad = []
    for r in refs:
        p = m.parts[r]
        x0, y0, x1, y1 = nb._box(p.fp, 0.1)
        tight = (x0 + a.dx, y0 + a.dy, x1 + a.dx, y1 + a.dy)
        bx0, by0, bx1, by1 = nb._box(p.fp, op.TOUCH)
        box = (bx0 + a.dx, by0 + a.dy, bx1 + a.dx, by1 + a.dy)
        extra = m.back_only if p.side == nb.BOT else []
        hit = [o for o in m.forbidden + extra + [m.ant_keepout] if nb._hits(tight, o)]
        hit += [h[4] for h in m.holes if h[4] != r and nb._hits(tight, h[:4])]
        hit += [o for o, q in m.parts.items() if o not in refs and q.side == p.side and nb._hits(box, m.boxes[o])]
        if hit:
            bad.append(f"{r}: {hit[:3]}")
    for r in refs:
        p = m.parts[r]
        x, y = p.pos()
        p.set(x + a.dx, y + a.dy, p.rot())
    print(f"moving {', '.join(refs)} by ({a.dx:+.2f}, {a.dy:+.2f}): {len(rigid)} tracks/vias with them, "
          f"{len(stretch)} stretched" + (f"; NOT LEGAL at the new spot: {bad}" if bad else ""))
    for t in rigid:
        if t.Type() == pcbnew.PCB_VIA_T:
            t.SetPosition(t.GetPosition() + d)
        else:
            t.SetStart(t.GetStart() + d)
            t.SetEnd(t.GetEnd() + d)
    for t, end in stretch:
        if end == "start":
            t.SetStart(t.GetStart() + d)
        else:
            t.SetEnd(t.GetEnd() + d)
    # a free stitching via under the group's new outline is dropped (the pour gets
    # re-stitched; such vias carry nothing of their own)
    moved_boxes = [(x0 + a.dx, y0 + a.dy, x1 + a.dx, y1 + a.dy) for x0, y0, x1, y1 in boxes]
    dropped = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() in ("GND", "V3V3")
               and t not in rigid and any(x0 <= M(t.GetPosition().x) <= x1 and y0 <= M(t.GetPosition().y) <= y1
                                          for x0, y0, x1, y1 in moved_boxes)
               and not any(u.Type() != pcbnew.PCB_VIA_T and u.GetNetname() == t.GetNetname()
                           and (t.HitTest(u.GetStart()) or t.HitTest(u.GetEnd())) for u in b.GetTracks())]
    for t in dropped:
        b.Remove(t)
    if dropped:
        print(f"dropped {len(dropped)} free stitching via(s) under the group's new outline")
    for r in refs:
        m.parts[r].fp.Reference().SetPosition(m.parts[r].fp.GetPosition())
    if a.apply and not bad:
        b.Save(a.board)
        print(f"wrote {a.board}")
    elif a.apply:
        raise SystemExit("not written")


if __name__ == "__main__":
    main()
