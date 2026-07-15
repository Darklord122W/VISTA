#!/usr/bin/env bash
# run_capacity.sh — E1: the deadline x model capacity sweep behind TABLE I.
#
#   ./run_capacity.sh            all four models  (~25 min, estimated)
#   ./run_capacity.sh m l        selected models
#
# WHAT IT MEASURES. For each detector, the batched service time S(4), the
# achieved frame rate, and the load ratio rho = S/T against the camera period
# T. This is the table that establishes the paper's premise: at yolo11m and
# above the system is oversubscribed (rho > 1), so a fraction 1 - 1/rho of all
# frames cannot be processed no matter how the pipeline is configured. Whoever
# drops them is scheduling; the only question is whether it is done explicitly.
#
# It also demonstrates the negative result the sweep exists to show: the push
# deadline is INERT at rho >= 1. Five deadlines (5/10/20/33.3/66.7 ms) are
# swept per model; on the NEW nvstreammux the batched-push-timeout PROPERTY does
# nothing at all, so each cell delivers its deadline through a generated INI
# (overall-min-fps). Even so, at rho >= 1 the batch is always already full and
# the deadline never fires. That is the point: you cannot configure your way out.
#
# RECONSTRUCTION NOTE. No script for E1 survives in the paper's harness; the
# campaign existed only as invocations. Every parameter below is taken from the
# authors' e1_yolo11{n,s,m,l}/run_meta.json (not distributed here), which
# timeout_sweep_cpp.py writes and which records the full argument set:
#   ms=[5,10,20,33.3,66.7] ref_ms=66.7 num_cams=4 duration=50 warmup=5
#   gap_every=44 ring=4 skew/rate = the measured live reference
# The one asymmetry is real and preserved: yolo11n's nvinfer config is
# config/pgie_config.txt (there is no pgie_yolo11n.txt).
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vista_env.sh"

MODELS=("$@")
[ ${#MODELS[@]} -eq 0 ] && MODELS=(n s m l)

vista_preflight
CLIPS="$(vista_require_clips myclipsForEXP)"

pgie_for() {  # yolo11n is configured by pgie_config.txt, not pgie_yolo11n.txt
  case "$1" in
    n) printf '%s\n' "$VISTA_CONFIG/pgie_config.txt" ;;
    *) printf '%s\n' "$VISTA_CONFIG/pgie_yolo11$1.txt" ;;
  esac
}

for M in "${MODELS[@]}"; do
  case "$M" in n|s|m|l) ;; *) vista_die "unknown model '$M' (want: n s m l)" ;; esac
  PGIE="$(pgie_for "$M")"
  vista_require_pgie "$PGIE"
  OUT="$VISTA_RESULTS/e1_yolo11$M"
  # Idempotent: the sweep's own summary CSV is the completion marker.
  if [ -f "$OUT/summary.csv" ] || [ -f "$OUT/detection_perf.png" ]; then
    vista_log "e1_yolo11$M already present — skipping (delete $OUT to force)"
    continue
  fi
  vista_log "E1: yolo11$M deadline sweep (5 cells x ~55 s) ..."
  python3 "$VISTA_HARNESS/timeout_sweep_cpp.py" \
      --config "$VISTA_CONFIG/camera_params.yaml" \
      --pgie "$PGIE" \
      --tag "yolo11$M dynamic" \
      --ms 5 10 20 33.3 66.7 \
      --ref-ms 66.7 \
      --num-cams 4 \
      --duration 50 --warmup 5 \
      --replay-dir "$CLIPS" \
      --skew-ms "$VISTA_SKEW" --rate "$VISTA_RATE" \
      --gap-every "$VISTA_GAP" --ring "$VISTA_RING" \
      --out "$OUT"
done

vista_log "capacity sweep done -> $VISTA_RESULTS"
vista_log "Table I is produced by analysis/ from these directories."
