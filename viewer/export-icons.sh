#!/usr/bin/env bash
# Regenerate every raster icon from the master static/icon.svg.
# Needs ffmpeg built with librsvg (the `svg` decoder) and python3 (stdlib only).
set -euo pipefail
cd "$(dirname "$0")/static"

render() {  # render SIZE OUT
  ffmpeg -hide_banner -loglevel error -y -width "$1" -height "$1" \
    -i icon.svg -frames:v 1 "$2"
}

render 512 icon.png
render 192 icon-192.png
render 180 apple-touch-icon.png

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
for s in 16 32 48; do render "$s" "$tmp/$s.png"; done

# favicon.ico holding 16/32/48 PNG frames (ICO allows embedded PNGs).
python3 - "$tmp" favicon.ico <<'PY'
import struct, sys
src, out = sys.argv[1], sys.argv[2]
sizes = [16, 32, 48]
blobs = [open(f"{src}/{s}.png", "rb").read() for s in sizes]
offset = 6 + 16 * len(sizes)
header = struct.pack("<HHH", 0, 1, len(sizes))
entries = b""
for s, b in zip(sizes, blobs):
    entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(b), offset)
    offset += len(b)
open(out, "wb").write(header + entries + b"".join(blobs))
PY

ls -l icon.png icon-192.png apple-touch-icon.png favicon.ico
