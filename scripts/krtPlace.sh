#!/bin/sh
# scripts/krtPlace.sh BOARD MOVABLE [extra place_optimize.py args]
#   BOARD: a wallet.kicad_pcb with its project beside it (written in place)
#   MOVABLE: comma list of refs the quench may move; everything else is locked
#   e.g. scripts/krtPlace.sh /tmp/x/wallet.kicad_pcb U1,R_ISET --max-displacement 2 --no-swap
# KiCadRoutingTools' greedy quench, one face at a time (it is side-blind: it would treat a
# front and a back part over each other as a collision), on a copy holding only that
# face's parts; every move it proposes is kept only if optimizePlacement's two-sided rules
# (slots, keep-outs, holes, the cell, the tails, other parts' copper) still hold.
# Judge the result with a trial route (routeGauge.py). On the 2026-10-25 chin re-placement
# it was worse than optimizePlacement's own seed both times (more vias; once 3 nets
# unrouted, and a same-footprint swap took the charger's NTC out from under the cell):
# pass --no-swap and leave parts with a placement rule of their own out of MOVABLE.
B=$1; MOV=$2; shift 2
S=$(cd "$(dirname "$0")" && pwd)
K=${KICAD_PY:-/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3}
KRT=${KRT:-$S/../../../projects/KiCadRoutingTools}
T=$(mktemp -d)
for FACE in bottom top; do
  $K - "$B" "$T/face.kicad_pcb" "$FACE" <<'PY' 2>&1 | grep -v "Debug\|wxApp\|swig"
import sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1]); face = sys.argv[3]
gone = [f for f in b.GetFootprints() if (f.IsFlipped() != (face == "bottom"))]
gone += [t for t in b.GetTracks()] + [z for z in b.Zones()]
for x in gone: b.Remove(x)
b.Save(sys.argv[2])
PY
  LOCK=$($K - "$T/face.kicad_pcb" "$MOV" <<'PY' 2>/dev/null | tail -1
import sys, re
mov = set(sys.argv[2].split(","))
refs = re.findall(r'\(property "Reference" "([^"]+)"', open(sys.argv[1]).read())
print(" ".join(r for r in refs if r not in mov))
PY
)
  echo "== $FACE: $(echo $LOCK | wc -w) locked"
  (cd $KRT && python3 place_optimize.py "$T/face.kicad_pcb" "$T/out_$FACE.kicad_pcb" --ignore-nets GND V3V3 --lock $LOCK "$@" 2>&1 | tail -3)
  (cd $KRT && python3 -c "
import json, sys
from kicad_parser import parse_kicad_pcb
d = parse_kicad_pcb(sys.argv[1])
json.dump({r: (f.x, f.y, f.rotation) for r, f in d.footprints.items()}, open(sys.argv[2], 'w'))
" "$T/out_$FACE.kicad_pcb" "$T/pos_$FACE.json")
  $K - "$B" "$S" "$T/pos_$FACE.json" "$MOV" <<'PY' 2>&1 | grep -v "Debug\|wxApp\|swig"
import sys, json, math, pcbnew
sys.path.insert(0, sys.argv[2]); import optimizePlacement as op
pos = json.load(open(sys.argv[3]))
b = pcbnew.LoadBoard(sys.argv[1]); mdl = op.Model(b)
kept = rej = 0
for ref in sys.argv[4].split(","):
    if ref not in pos or ref not in mdl.parts: continue
    p = mdl.parts[ref]; x0, y0, r0 = *p.pos(), p.rot()
    x, y, r = pos[ref]
    if math.hypot(x - x0, y - y0) < 1e-3 and abs(((r - r0) + 180) % 360 - 180) < 1e-3: continue
    p.set(x, y, r); mdl.moved(ref)
    if mdl.legal(p): kept += 1; print(f"  keep {ref} ({x0:.2f},{y0:.2f}) {r0:.0f} -> ({x:.2f},{y:.2f}) {r:.0f}")
    else: p.set(x0, y0, r0); mdl.moved(ref); rej += 1
for f in b.GetFootprints(): f.Reference().SetPosition(f.GetPosition())
b.Save(sys.argv[1]); print(f"  {kept} moves kept, {rej} rejected by the board's rules")
PY
done
rm -rf "$T"
