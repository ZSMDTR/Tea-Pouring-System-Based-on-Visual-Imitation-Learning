#!/usr/bin/env bash
# Clone exact upstream revisions, then apply the B601 changes in this release.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v git >/dev/null || { echo 'git is required' >&2; exit 1; }

clone_pin() {
  local name="$1" url="$2" revision="$3" path="$ROOT/$1"
  if [[ ! -e "$path" ]]; then
    git clone --filter=blob:none --no-checkout "$url" "$path"
    git -C "$path" checkout --detach "$revision"
  fi
  [[ -d "$path/.git" ]] || { echo "Not a git checkout: $path" >&2; exit 1; }
  [[ "$(git -C "$path" rev-parse HEAD)" == "$revision" ]] || {
    echo "Unexpected revision in $path; expected $revision" >&2; exit 1;
  }
}

apply_patch_once() {
  local name="$1" patch="$ROOT/patches/$1.patch" path="$ROOT/$1"
  if git -C "$path" apply --reverse --check "$patch" >/dev/null 2>&1; then
    echo "$name patch already applied"
  else
    git -C "$path" apply --check "$patch"
    git -C "$path" apply "$patch"
    echo "$name patch applied"
  fi
}

install_overlay() {
  local relative="$1" source="$ROOT/overlays/openpi/$1" target="$ROOT/openpi/$1"
  mkdir -p "$(dirname "$target")"
  if [[ -e "$target" ]]; then
    cmp -s "$source" "$target" || { echo "Overlay differs: $target" >&2; exit 1; }
  else
    cp "$source" "$target"
  fi
}

clone_pin openpi https://github.com/Physical-Intelligence/openpi.git 15a9616a00943ada6c20a0f158e3adb39df2ccac
clone_pin openpi_fullsave https://github.com/Physical-Intelligence/openpi.git 15a9616a00943ada6c20a0f158e3adb39df2ccac
clone_pin lerobot https://github.com/Seeed-Projects/lerobot.git fe078af31247a6144200abbfa3c2ce29d0062033
clone_pin lerobot-robot-seeed-b601 https://github.com/Seeed-Projects/lerobot-robot-seeed-b601.git 27b97166f49165c181bcf48db641324c81a37760

apply_patch_once openpi
apply_patch_once lerobot
apply_patch_once lerobot-robot-seeed-b601
while IFS= read -r -d '' file; do
  install_overlay "${file#"$ROOT/overlays/openpi/"}"
done < <(find "$ROOT/overlays/openpi" -type f -print0)
echo 'B601 source checkouts are ready.'
