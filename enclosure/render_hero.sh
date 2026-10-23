#!/bin/zsh
# The /v2/ concept page's hero image (docs/v2/brand/signer.webp): the page's own 3D model,
# rendered alone on a transparent background by headless Chrome. Rerun after
# enclosure/generate.py changes the model.
#
#   enclosure/render_hero.sh      (the page's default case and print: ice blue, purple on black;
#                                  from the front, low on the chin: the screen, the joystick and
#                                  the AAA cell's two clips)
#
# Needs Google Chrome and python3 with Pillow. Uses a throwaway copy of the page.
set -euo pipefail
cd "${0:A:h}/.."
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TMP=$(mktemp -d)
PORT=8799
trap 'kill $SERVER 2>/dev/null; rm -f docs/v2/_hero.html; rm -rf "$TMP"' EXIT

sed -e 's#</style>#html,body{background:transparent!important;padding:0!important;margin:0} body::after{display:none!important} .hero,.closer,footer,main>section:not(\#configure),\#configure .head,.config,figcaption,.onstage{display:none!important} .build{display:block} .stage{position:fixed!important;inset:0;border:0!important;border-radius:0!important;aspect-ratio:auto!important;background:transparent!important}</style>#' \
  -e 's#new THREE.WebGLRenderer({ antialias: true })#new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })#' \
  -e 's#scene.background = glowTexture(#scene.background = null \&\& glowTexture(#g' \
  -e 's#controls.autoRotate = true;#controls.autoRotate = false;#' \
  -e 's#camera.position.set(120, 70, -235);#camera.position.set(-70, -120, 200);#' \
  -e 's#^<title>#<meta charset=utf-8><title>#' docs/v2/index.html > docs/v2/_hero.html
for patched in 'alpha: true' 'scene.background = null && glowTexture(CASE.ice' 'background = null && glowTexture(CASE[none' 'camera.position.set(-70, -120, 200)'; do
  grep -qF "$patched" docs/v2/_hero.html || { echo "page changed: the hero patch '$patched' no longer applies" >&2; exit 1; }
done

python3 -m http.server $PORT --directory docs/v2 >/dev/null 2>&1 &
SERVER=$!
sleep 1
"$CHROME" --headless=new --enable-unsafe-swiftshader --use-angle=swiftshader --hide-scrollbars \
  --default-background-color=00000000 --window-size=800,1000 --force-device-scale-factor=1.25 \
  --virtual-time-budget=15000 --screenshot="$TMP/hero.png" "http://localhost:$PORT/_hero.html" >"$TMP/chrome.log" 2>&1 &
CHROME_PID=$!
for i in {1..60}; do grep -q "bytes written" "$TMP/chrome.log" && break; sleep 1; done
kill $CHROME_PID 2>/dev/null || true
[[ -s "$TMP/hero.png" ]] || { echo "no screenshot; Chrome log: $TMP/chrome.log" >&2; cat "$TMP/chrome.log" >&2; exit 1; }

python3 - "$TMP/hero.png" docs/v2/brand/signer.webp <<'EOF'
import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("RGBA")
box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
if not box:
    raise SystemExit("the render is empty")
pad = 24
x0, y0, x1, y1 = box
im = im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))
im.save(sys.argv[2], "WEBP", quality=88, method=6)
print(f"{sys.argv[2]}: {im.width}x{im.height}")
EOF
