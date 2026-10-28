#!/usr/bin/env bash
# Build the signer firmware in Docker, with Trezor's own toolchain (Alpine +
# Nix + trezor-firmware's shell.nix, i.e. the image build-docker.sh uses).
# Trezor's build-docker.sh itself clones a git tag into the container, which
# does not fit a tree vendored into this repo, so this script mounts the tree
# instead.
#
#   firmware/docker-build.sh                      # boardloader, bootloader, firmware + combined image
#   firmware/docker-build.sh firmware             # one project
#   firmware/docker-build.sh -- xtask build firmware --emulator -m T3T2   # any command
#   BOARD=devkit firmware/docker-build.sh         # Trezor's own T3T2 devkit board
#   XTASK_OPTS="--bootloader-devel -p dev" ...    # extra xtask flags (default: dev keys)
#   firmware/docker-build.sh --image-only         # just the toolchain image
#
# Project builds are copied to firmware/out/<board>/ (xtask itself writes
# core/build/artifacts/<model>/, shared by every board of the model).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
tree="$here/trezor-firmware"
MODEL="${MODEL:-T3T2}"
BOARD="${BOARD:-ambire_p0}"
# Development keys: T3T2 has no production key set, and neither do we yet
XTASK_OPTS="${XTASK_OPTS:---bootloader-devel}"
DOCKER="${DOCKER:-docker}"

# The image is keyed by shell.nix: a new toolchain pin means a new image.
nix_hash="$(shasum -a 256 "$tree/shell.nix" | cut -c1-12)"
IMAGE="ambire-ice-fw-env:$nix_hash"

case "$(uname -m)" in
  arm64|aarch64) arch=aarch64 sum=1be50ae27c8463d005c4de16558d239e11a88ac6b2f8721c47e660fbeead69bf ;;
  x86_64) arch=x86_64 sum=ec7ec80a96500f13c189a6125f2dbe8600ef593b87fc4670fe959dc02db727a2 ;;
  *) echo "unsupported host arch $(uname -m)" >&2; exit 1 ;;
esac

if ! "$DOCKER" image inspect "$IMAGE" > /dev/null 2>&1; then
  echo ">>> building $IMAGE (first run downloads the Nix toolchain, several GB)"
  ctx="$(mktemp -d)"
  trap 'rm -rf "$ctx"' EXIT
  tarball="alpine-minirootfs-3.15.0-$arch.tar.gz"
  curl --fail --no-progress-meter -L -o "$ctx/$tarball" \
    "https://dl-cdn.alpinelinux.org/alpine/v3.15/releases/$arch/$tarball"
  echo "$sum  $ctx/$tarball" | shasum -a 256 -c -
  cp "$tree/ci/Dockerfile" "$ctx/"
  mkdir -p "$ctx/common_sync"
  cp "$tree/ci/common_sync/git-filter-repo" "$ctx/common_sync/"
  sed "s|./ci/|./|" < "$tree/shell.nix" > "$ctx/shell.nix"
  "$DOCKER" build --build-arg ALPINE_VERSION=3.15.0 --build-arg ALPINE_ARCH="$arch" \
    -t "$IMAGE" "$ctx"
fi

[ "${1:-}" = "--image-only" ] && exit 0

"$here/fetch-vendor.sh"

if [ "${1:-}" = "--" ]; then
  shift
  cmd="$*"
else
  projects=("$@")
  if [ $# -eq 0 ]; then
    projects=(boardloader bootloader firmware)
  fi
  cmd=""
  for p in "${projects[@]}"; do
    cmd+="xtask build $p -m $MODEL -b $BOARD $XTASK_OPTS && "
  done
  if [ $# -eq 0 ]; then
    # boardloader + bootloader + secmon + firmware in one image for a blank chip
    cmd+="xtask combine firmware -m $MODEL && "
  fi
  cmd+="true"
fi

# The whole repo is mounted, because the build stamps the git commit (this
# repo's: the firmware tree has no .git of its own). The Python venv and
# cargo's caches live in volumes, not in the mounted tree: they hold Linux
# binaries and are rebuilt per image. Both paths are physical: the checkout
# may be reached through a symlink, and git reports the resolved one.
repo="$(cd "$(git -C "$here" rev-parse --show-toplevel)" && pwd -P)"
workdir="/repo/${tree#"$repo"/}"
echo ">>> in $IMAGE at $workdir: $cmd"
"$DOCKER" run --rm \
  -v "$repo:/repo" \
  -v "ambire-ice-fw-venv-$nix_hash:/venv" \
  -v "ambire-ice-fw-cargo-$nix_hash:/root/.cargo" \
  -e UV_PROJECT_ENVIRONMENT=/venv \
  -w "$workdir" \
  "$IMAGE" \
  nix-shell --run "set -e; git config --global --add safe.directory /repo; uv sync --locked; source /venv/bin/activate; $cmd"

if [ "${1:-}" != "--" ]; then
  out="$here/out/$BOARD"
  mkdir -p "$out"
  cp "$tree/core/build/artifacts/$MODEL/"*.bin "$tree/core/build/artifacts/$MODEL/"*.elf "$out/"
  echo ">>> artifacts in $out"
fi
