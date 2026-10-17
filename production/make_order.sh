#!/bin/sh
# Build the JLC order set from board/wallet.kicad_pcb into production/order/, then run
# the pre-fab checks (production/prefab_checks.py) into production/order/PREFAB-CHECKS.txt.
#
#   production/make_order.sh            (from hardware/ambire-wallet; refill + save in KiCad first)
#
# order/jlc-colour/wallet-colour-jlc.zip  (COLOUR=1 only) Gerbers in EasyEDA Pro form with the bottom
#                                         colour print. JLC did NOT offer its colour option for this zip
#                                         (2026-10-17): it has blocked non-EasyEDA colour files since
#                                         Jan 2026 (TransJLC issues #10/#11). Kept for reference.
# order/jlc-plain/wallet_gerbers.zip      the same board with ordinary white silkscreen
# order/pcbway/                          PCBWay: fab zip with the UV back print + positioning image,
#                                         turnkey BOM, centroid, ORDER-NOTES.txt (production/build_pcbway.py)
# order/wallet_bom-jlc.csv, order/wallet_positions-jlc.csv   assembly (bottom side), same for both
#
# Needs: KiCad 10, rsvg-convert, Pillow, cryptography, and TransJLC 0.4.0 on PATH
# (cargo install TransJLC --version 0.4.0, or TRANSJLC=/path/to/TransJLC) for the
# EasyEDA file names and headers; the colour file itself is ours (colour_silk.py).
set -eu
cd "$(dirname "$0")/.."
KPY=${KPY:-/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3}
TRANSJLC=${TRANSJLC:-TransJLC}
OUT=production/order
rm -rf "$OUT"; mkdir -p "$OUT"

echo "== UV artwork from the current placement"
(cd art && python3 build_uv.py >/dev/null)
python3 - <<'EOF'
from PIL import Image
im = Image.open("art/out/uv_back_seen_from_behind.png").convert("RGBA")
bg = Image.new("RGB", im.size, (255, 255, 255)); bg.paste(im, mask=im.split()[3])   # no-ink = the white base
bg.save("production/order/uv_back_print.jpeg", quality=93, subsampling=0, dpi=(1200, 1200))
EOF

echo "== plain set"
"$KPY" production/export_fab.py board/wallet.kicad_pcb "$OUT/jlc-plain" production/jlc_rotation_db.csv | grep -E "files ->|origin"
python3 -I production/build_jlc_files.py "$OUT/jlc-plain" 10 > "$OUT/jlc-plain/build.log"
cp "$OUT/jlc-plain/wallet_bom-jlc.csv" "$OUT/jlc-plain/wallet_positions-jlc.csv" \
   "$OUT/jlc-plain/work/rotation-review.csv" "$OUT/jlc-plain/work/stock.csv" "$OUT/"

echo "== PCBWay set (full-colour UV back print)"
"$KPY" production/build_pcbway.py "$OUT" | grep -v -iE "swig|wxApp"

if [ "${COLOUR:-0}" = 1 ]; then
echo "== colour set"
"$KPY" production/export_fab.py board/wallet.kicad_pcb "$OUT/colour-src" production/jlc_rotation_db.csv --colour | grep -E "colour print|files ->"
mkdir -p "$OUT/colour-src/stage"
for f in "$OUT"/colour-src/gerbers/*; do
  b=$(basename "$f")
  case "$b" in *drl_map*) ;; *.drl) cp "$f" "$OUT/colour-src/stage/$b" ;; *) cp "$f" "$OUT/colour-src/stage/${b%.*}.gbr" ;; esac
done
# names, EasyEDA header, outline (.FCBO) and mark (.FCBM) files; its .FCBS is replaced below
"$TRANSJLC" -e kicad -p "$OUT/colour-src/stage" -o "$OUT/jlc-colour" --no-progress \
  --bottom_color_image "$OUT/uv_back_print.jpeg" >/dev/null
"$KPY" production/colour_silk.py board/wallet.kicad_pcb "$OUT/uv_back_print.jpeg" \
  "$OUT/jlc-colour/Fabrication_ColorfulBottomSilkscreen.FCBS" --plain "$OUT/colour-src/bottom-colour.plain.svg"
(cd "$OUT/jlc-colour" && rm -f ./*.zip && zip -q -X wallet-colour-jlc.zip ./*)
fi

echo "== pre-fab checks"
"$KPY" production/prefab_checks.py "$OUT"
