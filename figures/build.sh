#!/usr/bin/env bash
# Rebuild every figure from $VISTA_DATA_ROOT into figures/generated/. No run
# data ships with this repository; the generators say so and exit if it is
# unset. See README.md.
#
# SOURCE_DATE_EPOCH is defaulted (not forced): matplotlib stamps /CreationDate
# into PDFs, so without it two identical builds differ. Export your own value
# to override. PNGs are byte-reproducible either way.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1752537600}"   # 2025-07-15T00:00:00Z

GENERATORS=(
    make_frame_funnel.py        # Fig. 1  (in paper)
    make_tta_pareto.py          # Fig. 3  (in paper)
    make_pipeline.py            # schematic, not in paper
    make_latency_cdf.py         # not in paper (evaluation.tex:71 commented out)
    make_imp_concentration.py   # not in paper
)

rc=0
for g in "${GENERATORS[@]}"; do
    echo "=== $g"
    if ! python3 "$HERE/src/$g"; then
        echo "!!! $g FAILED" >&2
        rc=1
    fi
done

echo "=== diagrams/export.sh (Fig. 2)"
if ! "$HERE/diagrams/export.sh"; then
    echo "!!! export.sh FAILED (needs cairosvg or inkscape)" >&2
    rc=1
fi

exit "$rc"
