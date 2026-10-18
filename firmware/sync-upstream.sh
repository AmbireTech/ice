#!/usr/bin/env bash
# Move firmware/trezor-firmware to a newer trezor-firmware commit, keeping our
# changes. The tree here has no .git, so the sync replays our changes in a
# scratch checkout of upstream: base commit + our tree as one commit, rebased
# onto the target. Conflicts come out as ordinary git conflicts there.
#
#   firmware/sync-upstream.sh               # to upstream main
#   firmware/sync-upstream.sh <ref>         # to a tag or commit (e.g. core/v2.12.5)
#   firmware/sync-upstream.sh --commit ...  # also commit the result in this repo
#   firmware/sync-upstream.sh --continue    # after resolving conflicts
#   firmware/sync-upstream.sh --abort       # drop a sync stuck on conflicts
#
# The base is the commit in vendor-pins.txt's first line; on success that line
# and the submodule pins are rewritten for the target.
#
# Exit status: 0 synced or already current, 2 conflicts to resolve, 1 error.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
tree="$here/trezor-firmware"
pins="$here/vendor-pins.txt"
cache="$here/.upstream"   # scratch clone + worktree, ignored by this repo
clone="$cache/repo"
work="$cache/work"
URL="https://github.com/trezor/trezor-firmware.git"
repo="$(cd "$(git -C "$here" rev-parse --show-toplevel)" && pwd -P)"
rel="${tree#"$repo"/}"

commit=0
mode=sync
target_ref=origin/main
for arg in "$@"; do
  case "$arg" in
    --commit) commit=1 ;;
    --continue) mode="continue" ;;
    --abort) mode="abort" ;;
    -*) echo "unknown option $arg" >&2; exit 1 ;;
    *) target_ref="$arg" ;;
  esac
done

base="$(sed -n '1s/^# trezor-firmware \([0-9a-f]\{40\}\) .*/\1/p' "$pins")"
if [ -z "$base" ]; then
  echo "no base commit on the first line of $pins" >&2
  exit 1
fi

# git in the scratch checkout: its own identity, no hooks or signing
wgit() {
  git -C "$work" -c user.name="firmware sync" -c user.email="sync@localhost" \
    -c commit.gpgsign=false -c core.hooksPath=/dev/null "$@"
}

# Tracked non-submodule files of a checkout, NUL-separated
plain_files() {
  git -C "$1" ls-files -z -s | perl -0ne 'print "$1\0" if /^(?!160000)\d+ \S+ \d\t(.*)\0/s'
}

# git add/rm of NUL-separated tree-relative paths, run from the repo root
# (git 2.39 refuses --pathspec-from-file inside a subdirectory)
tree_git() {
  perl -0pe "s|^|$rel/|" | git -C "$repo" "$@" --pathspec-from-file=- --pathspec-file-nul
}

stop_on_conflicts() {
  echo
  echo "Conflicts replaying our changes onto $(cat "$cache/target"):"
  wgit diff --name-only --diff-filter=U | sed 's/^/  /'
  echo
  echo "Resolve them in $work, git add each one there, then run"
  echo "firmware/sync-upstream.sh --continue (or --abort to drop the sync)."
  exit 2
}

if [ "$mode" = abort ]; then
  if [ -d "$work" ]; then
    wgit rebase --abort 2>/dev/null || echo "no rebase in progress in $work"
    git -C "$clone" worktree remove --force "$work"
  fi
  rm -f "$cache/target"
  echo "sync aborted; firmware/trezor-firmware unchanged"
  exit 0
fi

if [ "$mode" = sync ]; then
  if [ -n "$(git -C "$repo" status --porcelain -- "$tree" "$pins")" ]; then
    echo "firmware/trezor-firmware or vendor-pins.txt has uncommitted changes:" >&2
    echo "commit or stash them first (the sync replays committed files only)" >&2
    exit 1
  fi

  if [ -d "$clone/.git" ]; then
    git -C "$clone" fetch -q --tags origin
  else
    mkdir -p "$cache"
    git clone -q --filter=blob:none --no-checkout "$URL" "$clone"
  fi
  target="$(git -C "$clone" rev-parse --verify "$target_ref^{commit}")"

  if [ "$target" = "$base" ]; then
    echo "already at $target_ref ($base)"
    exit 0
  fi
  if git -C "$clone" merge-base --is-ancestor "$target" "$base"; then
    echo "note: $target_ref ($target) is older than the current base $base"
  fi
  echo "$target" > "$cache/target"

  # Scratch checkout of the base, with our tree in place of upstream's files
  git -C "$clone" worktree prune
  rm -rf "$work"
  git -C "$clone" worktree add -q --detach "$work" "$base"
  plain_files "$work" | (cd "$work" && xargs -0 rm -f)
  git -C "$tree" ls-files -z | tar -C "$tree" --null -T - -cf - | tar -C "$work" -xf -
  wgit add -A
  wgit commit -q --allow-empty -m "Ambire port"
  echo "our changes against $base:"
  wgit diff --stat HEAD~1 HEAD | tail -1

  if ! wgit rebase -q --onto "$target" "$base"; then
    stop_on_conflicts
  fi
else
  if [ ! -f "$cache/target" ] || [ ! -d "$work" ]; then
    echo "no sync in progress" >&2
    exit 1
  fi
  target="$(cat "$cache/target")"
  if [ -d "$work/.git" ] || [ -f "$work/.git" ]; then
    if wgit status | grep -q 'rebase in progress'; then
      if ! wgit -c core.editor=true rebase --continue; then
        stop_on_conflicts
      fi
    fi
  fi
fi

if [ "$(wgit rev-parse HEAD~1)" != "$target" ]; then
  echo "the scratch checkout is not our changes on top of $target; run --abort and retry" >&2
  exit 1
fi

# Copy the result back: drop files that left, write the rest
plain_files "$work" > "$cache/keep"
git -C "$tree" ls-files -z |
  perl -0ne 'BEGIN { open K, "<", shift; %keep = map { $_ => 1 } <K> } print unless $keep{$_}' \
    "$cache/keep" > "$cache/gone"
if [ -s "$cache/gone" ]; then
  tree_git rm -q < "$cache/gone"
fi
rm -f "$cache/keep" "$cache/gone"
plain_files "$work" | tar -C "$work" --null -T - -cf - | tar -C "$tree" -xf -
plain_files "$work" | tree_git add -f

# Submodule pins of the target
{
  echo "# trezor-firmware $target ($(git -C "$work" log -1 --format=%cs "$target")) submodule pins: path url commit"
  git -C "$work" ls-tree -r "$target" | awk '$1 == "160000" { print $4, $3 }' |
    while read -r sub sha; do
      name="$(git -C "$work" config -f .gitmodules --get-regexp '\.path$' |
        awk -v p="$sub" '$2 == p { sub(/^submodule\./, "", $1); sub(/\.path$/, "", $1); print $1 }')"
      echo "$sub $(git -C "$work" config -f .gitmodules --get "submodule.$name.url") $sha"
    done
} > "$pins"
git -C "$repo" add "$pins"

ahead="$(git -C "$clone" rev-list --count "$base..$target")"
behind="$(git -C "$clone" rev-list --count "$target..$base")"
if [ "$behind" -gt 0 ] && [ "$ahead" -eq 0 ]; then
  count="$behind upstream commits back"
elif [ "$behind" -gt 0 ]; then
  count="$ahead upstream commits in, $behind out"
else
  count="$ahead upstream commits"
fi
summary="trezor-firmware ${base:0:9} -> ${target:0:9} ($count)"
git -C "$clone" worktree remove --force "$work"
rm -f "$cache/target"

echo "synced: $summary"
if [ "$commit" = 1 ]; then
  git -C "$repo" commit -q --no-gpg-sign -m "Firmware: sync $summary" \
    -m "https://github.com/trezor/trezor-firmware/compare/$base...$target"
  echo "committed: $(git -C "$repo" log -1 --format='%h %s')"
else
  echo "staged; next: firmware/docker-build.sh, then commit"
fi
