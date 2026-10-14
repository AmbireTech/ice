#!/bin/sh
# Routability measure: autoroute every net but GND on a scratch copy of the board
# with KiCadRoutingTools' route.py and report failed nets, vias and search effort.
# The board in board/ is never touched. Use it to judge placement changes: the
# crossing/airwire proxies alone have been seen to disagree with the router.
#
#   scripts/trialRoute.sh [board.kicad_pcb]
#   KRT=/path/to/KiCadRoutingTools (default: ../../projects/KiCadRoutingTools)
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
SRC=${1:-$HERE/board/wallet.kicad_pcb}
KRT=${KRT:-$HERE/../../projects/KiCadRoutingTools}
WORK=$(mktemp -d)
cp "$SRC" "$WORK/wallet.kicad_pcb"
cp "$HERE/board/wallet.kicad_pro" "$HERE/board/wallet.kicad_dru" "$WORK/"
cd "$KRT"
NETS=$(python3 -c "
import sys; sys.path.insert(0, '.')
from kicad_parser import parse_kicad_pcb
p = parse_kicad_pcb('$WORK/wallet.kicad_pcb')
print(' '.join(sorted(n.name for n in p.nets.values()
                      if n.name and n.name != 'GND' and not n.name.startswith('NC') and len(n.pads) > 1)))")
# shellcheck disable=SC2086
python3 route.py "$WORK/wallet.kicad_pcb" "$WORK/routed.kicad_pcb" --nets $NETS \
  --layers F.Cu In2.Cu B.Cu --track-width 0.15 --clearance 0.15 --via-size 0.45 --via-drill 0.25 \
  > "$WORK/route.log" 2>&1 || true
python3 - "$WORK/route.log" <<'PY'
import json, re, sys
t = open(sys.argv[1]).read()
sums = [json.loads(m) for m in re.findall(r"JSON_SUMMARY: (\{.*\})", t)]
if not sums:
    sys.exit(f"route.py produced no summary; see {sys.argv[1]}")
main, last = sums[0], sums[-1]
failed = set(main["failed_single"]) | {f["net_name"] for f in main["failed_multipoint"]}
if len(sums) > 1:
    failed = (failed - set(last.get("rescue", {}).get("recovered", []))) | set(last["failed_single"]) \
        | {f["net_name"] for f in last["failed_multipoint"]}
print(f"failed nets: {len(failed)} {sorted(failed)}  vias: {sum(s.get('total_vias', 0) for s in sums)}  "
      f"search: {sum(s.get('total_iterations', 0) for s in sums) / 1e6:.1f}M iterations")
PY
echo "log and routed copy: $WORK"
