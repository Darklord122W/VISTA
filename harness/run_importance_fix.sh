#!/usr/bin/env bash
# run_importance_fix.sh — the post-bugfix VISTA-Activity and salvage runs.
#   e8_impfix_r{0,1,2}   imp-k2    -> TABLE II row 5 (VISTA-Activity)
#   e8_salvfix_r{0,1,2}  salv-k2   -> salvage mode; NOT a table row in the paper
#
#   ./run_importance_fix.sh          both      (~5 min)
#   ./run_importance_fix.sh imp      imp only  (~3 min)
#
# WHY THIS CAMPAIGN EXISTS SEPARATELY FROM e3_m
# The importance term had a bug. The imp-k2 runs inside the e3_m campaign were
# taken with it; these re-runs are the corrected measurement, which is why the
# paper's VISTA-Activity numbers come from e8_* and not from e3_m/imp-k2.
#
# TWO ARCHIVE HAZARDS THIS TOUCHES — neither is a defect in this script, but
# anyone re-running this campaign will meet both:
#
# 1. STALE DATA THAT DISAGREES. The authors' e3_m_aggregate.json holds the
#    PRE-bugfix imp-k2 aggregates (coverage 38.8%, e2e 94.6 ms). Those numbers
#    contradict Table II in every cell. The paper is right not to use them; the
#    file is quarantined, not deleted, because it is evidence of the bug's
#    history. Do not "reconcile" Table II against it.
#
# 2. MISLABELLED PROVENANCE IN THE TRUE SOURCE. recall_m2.json — which IS the
#    source of Table II — labels these very runs "e3_m/imp-k2_r{0,1,2}". Those
#    rows are byte-identical to e8_impfix_r{0,1,2} in recall_e78.json
#    (cov .3431/e2e 109.1, .3166/115.5, .3090/120.6; median 31.7/115 = the
#    published cell). So Table II's NUMBERS are real and come from these
#    post-bugfix runs; only the PROVENANCE LABEL is wrong. That is a labelling
#    defect, not fabricated data, and the distinction matters. See
#    docs/ for the claim->evidence matrix.
#
# RECONSTRUCTION NOTE. No script survives. Parameters are taken verbatim from
# the authors' e8_{impfix,salvfix}_r*/imp-k2_r0/run_meta.json: yolo11m,
# --duration 52, gap-every 44, ring 4, standard skew/rate, K=2, defaults
# otherwise (depth 2, stash 1, w=0.40/0.35/0.25).
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vista_env.sh"

WHAT="${1:-all}"
case "$WHAT" in all|imp|salv) ;; *)
  vista_die "usage: $(basename "$0") [all|imp|salv]" ;;
esac

vista_preflight
CLIPS="$(vista_require_clips myclipsForEXP)"
PGIE="$VISTA_CONFIG/pgie_yolo11m.txt"
vista_require_pgie "$PGIE"

one() {  # one <out-dir> <arm>
  local out="$VISTA_RESULTS/$1" arm="$2"
  if [ -f "$out/${arm}_r0/metrics.csv" ]; then
    vista_log "$1 already present — skipping"
    return 0
  fi
  vista_log "running $1 ($arm) ..."
  python3 "$VISTA_HARNESS/run_eval.py" \
      --pgie "$PGIE" --model-tag m --arm "$arm" --repeats 1 --duration 52 \
      --replay-dir "$CLIPS" --skew "$VISTA_SKEW" --rate "$VISTA_RATE" \
      --gap-every "$VISTA_GAP" --ring "$VISTA_RING" \
      --skip-existing --out "$out"
}

# Each repeat is its own top-level directory (r0/r1/r2), each holding a single
# <arm>_r0 run. That is how the archive is laid out; the analysis keys on it.
if [ "$WHAT" = all ] || [ "$WHAT" = imp ]; then
  vista_log "--- VISTA-Activity (imp-k2), post-importance-bugfix -> Table II row 5"
  for r in 0 1 2; do one "e8_impfix_r$r" imp-k2; done
fi

if [ "$WHAT" = all ] || [ "$WHAT" = salv ]; then
  # Salvage is NOT evaluated in the paper. It is kept because its actionability
  # result is a negative one worth having: held frames were redundant with a
  # neighbour frame within 150 ms in 435 of 435 cases.
  vista_log "--- salvage (salv-k2), post-bugfix — not a paper table row"
  for r in 0 1 2; do one "e8_salvfix_r$r" salv-k2; done
fi

vista_log "importance-fix campaign done -> $VISTA_RESULTS"
