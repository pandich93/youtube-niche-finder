#!/usr/bin/env sh
# Build the Chrome extension zip that goes into a GitHub release:
#   dist/niche-finder-extension-v<version>.zip
# The version is read from extension/manifest.json. Only files committed to
# git are packed (git archive), so local junk such as .DS_Store never ships.
#
#   scripts/package_extension.sh          # pack HEAD
#   scripts/package_extension.sh v0.2.0   # pack a tag (what the release workflow does)
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$DIR"
REF=${1:-HEAD}
VERSION=$(git show "$REF:extension/manifest.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
mkdir -p dist
OUT="dist/niche-finder-extension-v$VERSION.zip"
git archive --format=zip --prefix=niche-finder-extension/ -o "$OUT" "$REF:extension"
echo "$OUT"
