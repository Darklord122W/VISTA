#!/usr/bin/env bash
# run_baselines.sh — the two non-trivial baselines in TABLE II (rows 2 and 3).
#
#   ./run_baselines.sh              both baselines + controls  (~11 min)
#   ./run_baselines.sh livedepth    Stock-LiveDepth only        (~5 min)
#   ./run_baselines.sh decimation   Static-Decimation only      (~3 min)
#   ./run_baselines.sh dec12        the DEC-1/2 diagnostic      (~3 min)
#
# These answer the two obvious objections to the paper's premise: "your FIFO
# baseline is just badly configured" and "you could have just dropped frames".
#
# ---------------------------------------------------------------------------
# Stock-LiveDepth  (archived as e7_surfcal_2 / e7_s2_r1 / e7_s2_r2)
#   --replay-surfaces 2
# File replay hands the decoder 20 spare surfaces, so a backlogged FIFO can
# stand a queue 20 frames deep that live capture could never hold: replay
# FLATTERS the stock pipeline's coverage and PUNISHES its latency, relative to
# the live rig. Setting num-extra-surfaces to 2 makes replay's queue depth
# resemble the live one. This is the honest stock baseline, and it is the
# fairer comparison for VISTA, not a weaker one.
# The --replay-surfaces 4 arm (e7_surfcal_4*) is the CONTROL that calibrates
# this knob; it is run by default because surfaces=2 is uninterpretable alone.
#
# ---------------------------------------------------------------------------
# Static-Decimation  (archived as e3_m_decimate3_r{0,1,2})
#   --gap-every 3
# The "just process every third frame" answer: shed load blindly, on a fixed
# schedule, with no idea which frames matter. Uniform decimation is the
# scheduler-shaped hole VISTA fills.
#
# *** THE TRAP IN THIS FILE. READ BEFORE EDITING. ***
# --gap-every N means "drop 2 consecutive frames every N frames per camera".
# So the KEEP fraction is (N-2)/N, and:
#     --gap-every 3  ->  keeps 1 of 3  =  DEC-1/3  =  Static-Decimation (Table II)
#     --gap-every 4  ->  keeps 2 of 4  =  DEC-1/2  =  a DIAGNOSTIC, not Table II
# The archived directories are named for the POLICY, not the flag:
#     e3_m_decimate3_r*  <- --gap-every 3  = DEC-1/3 = the paper's baseline
#     e3_m_decimate_r*   <- --gap-every 4  = DEC-1/2 = the stalest config measured
# `e3_m_decimate` and `e3_m_decimate3` differ by one character and by ~930 ms of
# latency. Confusing them moves a point on Fig. 3 across the plot with no error
# anywhere. DEC-1/2 is therefore NOT run by default; ask for it explicitly.
#
# (Both baselines also collide with the OTHER meaning of --gap-every: 44, the
# live-rate timing fidelity setting every other campaign uses. Here the flag is
# the experimental variable. See vista_env.sh.)
#
# RECONSTRUCTION NOTE. No script for these campaigns survives. Parameters come
# from the authors' run_meta.json cmd arrays for e7_* and e3_m_decimate*
# (not distributed here): arm fifo33, yolo11m, --duration 52, ring 4, and
# the standard skew/rate. Directory names are reproduced EXACTLY as archived —
# including the inconsistent e7_surfcal_2 / e7_s2_r1 / e7_s2_r2 triple — because
# the analysis keys on them. Do not tidy them; map names in a registry instead.
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vista_env.sh"

WHAT="${1:-all}"
case "$WHAT" in all|livedepth|decimation|dec12) ;; *)
  vista_die "usage: $(basename "$0") [all|livedepth|decimation|dec12]" ;;
esac

vista_preflight
CLIPS="$(vista_require_clips myclipsForEXP)"
PGIE="$VISTA_CONFIG/pgie_yolo11m.txt"
vista_require_pgie "$PGIE"

# fifo33_one <out-dir> <extra run_eval args...>
# Every arm here is Stock-Default (fifo33) at yolo11m for 52 s; only the
# variable under test differs.
fifo33_one() {
  local out="$VISTA_RESULTS/$1"; shift
  if [ -f "$out/fifo33_r0/metrics.csv" ]; then
    vista_log "$(basename "$out") already present — skipping"
    return 0
  fi
  vista_log "running $(basename "$out") ..."
  python3 "$VISTA_HARNESS/run_eval.py" \
      --pgie "$PGIE" --model-tag m --arm fifo33 --repeats 1 --duration 52 \
      --replay-dir "$CLIPS" --skew "$VISTA_SKEW" --rate "$VISTA_RATE" \
      --ring "$VISTA_RING" --skip-existing --out "$out" "$@"
}

if [ "$WHAT" = all ] || [ "$WHAT" = livedepth ]; then
  # Stock-LiveDepth (Table II row 2) + its surfaces=4 control.
  # The r0 of each triple carries the odd historical name.
  vista_log "--- Stock-LiveDepth: --replay-surfaces 2 (+ surfaces=4 control)"
  for spec in "e7_surfcal_2:2" "e7_s2_r1:2" "e7_s2_r2:2" \
              "e7_surfcal_4:4" "e7_surfcal_4_r1:4" "e7_surfcal_4_r2:4"; do
    dir="${spec%%:*}"; surf="${spec##*:}"
    fifo33_one "$dir" --gap-every "$VISTA_GAP" \
        --extra "--replay-surfaces $surf"
  done
fi

if [ "$WHAT" = all ] || [ "$WHAT" = decimation ]; then
  # Static-Decimation (Table II row 3) = DEC-1/3 = --gap-every 3.
  vista_log "--- Static-Decimation: --gap-every 3 (DEC-1/3, keeps 1 of 3)"
  for r in 0 1 2; do
    fifo33_one "e3_m_decimate3_r$r" --gap-every 3
  done
fi

if [ "$WHAT" = dec12 ]; then
  # DEC-1/2 = --gap-every 4. NOT Table II. This is the stalest configuration
  # measured anywhere in the study; it exists to show that decimating less
  # aggressively does not rescue the stock pipeline.
  vista_log "--- DEC-1/2 diagnostic: --gap-every 4 (keeps 2 of 4) — NOT Table II"
  for r in 0 1 2; do
    fifo33_one "e3_m_decimate_r$r" --gap-every 4
  done
fi

vista_log "baselines done -> $VISTA_RESULTS"
