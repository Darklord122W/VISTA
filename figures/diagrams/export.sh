#!/usr/bin/env bash
# Export the Fig. 2 system diagram (drawio SVG) -> figures/generated/fig_system_diagram.pdf.
#
# WHICH SOURCE, AND HOW WE KNOW
# -----------------------------
# sections/design.tex:42 names "system_diagram_lateJuly.drawio.svg". That file
# is NOT the source of the shipped figure. Verified 2026-07-15 against
# VISTA-Rev2/figures/fig_system_diagram.pdf (MediaBox 1012.5 x 540):
#
#   system_diagram_lateJuly.drawio.svg     1364x759 -> 1023 x 569.25 pt.  WRONG
#                                          size, and cairosvg 2.x cannot even
#                                          render it (ValueError on a hex
#                                          colour literal); emits an 860-byte
#                                          near-empty PDF.
#   system_diagram_lateJuly_v2.drawio.svg  1350x720 -> 1012.5 x 540 pt. Correct
#                                          page size, but the content differs
#                                          from the shipped PDF.
#   system_diagram_lateJuly_v3.drawio.svg  1350x720 -> 1012.5 x 540 pt, and its
#                                          conversion is BYTE-IDENTICAL to the
#                                          shipped PDF once /CreationDate is
#                                          removed (420875 bytes both), and
#                                          pixel-identical when rasterised at
#                                          100 dpi (md5 90e5228c...).
#
# v3 is therefore the source, and it is the only one archived here. v1/v2 were
# left in the author's ~/Downloads and are not part of the artifact.
#
# SCALE: do NOT pass a scale factor. The 0.75 relating 1350x720 px to
# 1012.5x540 pt is the SVG px -> PostScript pt unit conversion (72/96), which
# cairosvg applies on its own. Passing `-s 0.75` on top of it yields
# 759.375 x 405 pt -- a figure 25% too small, with no error.
#
# DETERMINISM: cairo stamps /CreationDate and does not honour SOURCE_DATE_EPOCH.
# If SOURCE_DATE_EPOCH is set, this script rewrites that one field afterwards,
# which makes the PDF byte-reproducible (that field is the only difference we
# measured between two runs).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:-$HERE/system_diagram_lateJuly_v3.drawio.svg}"
# Generated output goes to figures/generated/, matching figures/src/_paths.py
# and analysis/_paths.py. $VISTA_FIG_DIR overrides, as it does for both.
OUT="${2:-${VISTA_FIG_DIR:-$HERE/../generated}/fig_system_diagram.pdf}"
mkdir -p "$(dirname "$OUT")"

if [[ ! -f "$SRC" ]]; then
    echo "export.sh: missing source SVG: $SRC" >&2
    exit 1
fi

if command -v cairosvg >/dev/null 2>&1; then
    # The reference converter: this is what produced the shipped PDF.
    cairosvg "$SRC" -o "$OUT"
elif command -v inkscape >/dev/null 2>&1; then
    # Fallback. Inkscape's renderer is NOT byte-compatible with cairo's output
    # and its text shaping can differ; use it only if cairosvg is unavailable,
    # and eyeball the result against figures/generated/fig_system_diagram.pdf.
    echo "export.sh: cairosvg not found, falling back to inkscape" >&2
    echo "export.sh: output will NOT be byte-identical to the shipped PDF" >&2
    inkscape "$SRC" --export-type=pdf --export-filename="$OUT"
else
    echo "export.sh: need cairosvg (pip install cairosvg) or inkscape" >&2
    exit 1
fi

if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
    python3 - "$OUT" "$SOURCE_DATE_EPOCH" <<'PY'
import re, sys, time
path, epoch = sys.argv[1], int(sys.argv[2])
stamp = time.strftime("D:%Y%m%d%H%M%S+00'00", time.gmtime(epoch)).encode()
data = open(path, "rb").read()
new, n = re.subn(rb"/CreationDate\s*\([^)]*\)", b"/CreationDate (" + stamp + b")", data)
open(path, "wb").write(new)
print(f"export.sh: normalised {n} /CreationDate field(s) to {stamp.decode()}")
PY
fi

echo "export.sh: wrote $OUT"
python3 - "$OUT" <<'PY'
import re, sys
d = open(sys.argv[1], "rb").read()
mb = re.findall(rb"/MediaBox\s*\[([^\]]*)\]", d)
print("export.sh: MediaBox", mb[0].decode().strip() if mb else "?", f"({len(d)} bytes)")
print("export.sh: expected MediaBox 0 0 1012.5 540")
PY
