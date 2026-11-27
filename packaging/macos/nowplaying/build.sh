#!/bin/bash
# Builds st_nowplaying.dylib (universal) next to core/media.py's lookup path: resources/.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$here/../../../resources/st_nowplaying.dylib}"
mkdir -p "$(dirname "$out")"
clang -dynamiclib -fobjc-arc -O2 -mmacosx-version-min=12.0 -arch arm64 -arch x86_64 \
      -framework Foundation -framework AppKit \
      -o "$out" "$here/st_nowplaying.m"
codesign --force --sign - "$out" 2>/dev/null || true
echo "built $out"
