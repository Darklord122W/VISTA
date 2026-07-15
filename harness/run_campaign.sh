#!/usr/bin/env bash
# run_campaign.sh — the policy-evaluation campaign (oracle -> refs -> arms ->
# offset sweep). GPU-serialized: nothing here is safe to run concurrently, with
# itself or with anything else that touches the GPU.
#
#   ./run_campaign.sh core      oracle_x + ref_m + e3_m        (~36 min)
#   ./run_campaign.sh full      core + ref_{s,l} + e3_{s,l} + e6 (~90 min)
#
# RUNTIME (measured, not estimated). The original header claimed "~2.5 h" for
# this sequence. Two independent sources say otherwise:
#   * summing wall_s over the archived run_meta.json files for exactly this
#     scope gives 89.9 min;
#   * the archived LOG.md brackets the campaign at 19:00:41 -> 20:30:40 = 89.98
#     min.
# So: ~1 h 30 for `full`, ~36 min for `core`, PLUS a one-time ~37 min YOLO11x
# oracle engine build (LOG.md: 18:22:25 -> 18:59:16 = 36 min 51 s) if that
# engine is not already present. The 2.5 h figure appears to have folded the
# engine build and a first aborted attempt into one number.
#
# IDEMPOTENT. Every stage is guarded and every run is skipped if its metrics.csv
# already exists, so an interrupted campaign resumes by re-running the same
# command. To force a stage, delete its output directory.
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vista_env.sh"

MODE="${1:-}"
case "$MODE" in
  core|full) ;;
  *) cat >&2 <<EOF
usage: $(basename "$0") {core|full}

  core   YOLO11x oracle + yolo11m completeness ref + the 7-arm policy campaign
         at yolo11m x 5 repeats. Everything Tables II/III need.   (~36 min)
  full   core, plus the yolo11s/yolo11l refs and policy campaigns and the E6
         offset-robustness sweep.                                  (~1 h 30)

Both additionally need a one-time ~37 min YOLO11x engine build if
models/yolo11x.onnx_b4_gpu0_fp16.engine is absent.

Environment (see vista_env.sh): VISTA_ROOT VISTA_BIN VISTA_CONFIG VISTA_CLIPS
VISTA_RESULTS GPU_SYSFS. Output goes to VISTA_RESULTS, which defaults to
$VISTA_REPO/runs. Point $VISTA_DATA_ROOT at it to analyse what you produce
(analysis/README.md); no run data ships with this repository.
EOF
     exit 2 ;;
esac

CAMPAIGN_LOG="$VISTA_RESULTS/CAMPAIGN.log"
log() { vista_log "$*" | tee -a "$CAMPAIGN_LOG"; }

# ---------------------------------------------------------------------------
# Preflight. Fail before burning an hour of GPU, not after.
# ---------------------------------------------------------------------------
vista_preflight                       # binary exists; GPU clock pinned
CLIPS="$(vista_require_clips myclipsForEXP)"
mkdir -p "$VISTA_RESULTS"

log "=== campaign start (mode=$MODE) ==="
vista_env_dump | tee -a "$CAMPAIGN_LOG"

eval_run() {  # eval_run <out-subdir> <args...>
  local out="$VISTA_RESULTS/$1"; shift
  python3 "$VISTA_HARNESS/run_eval.py" --replay-dir "$CLIPS" \
      --skew "$VISTA_SKEW" --rate "$VISTA_RATE" --gap-every "$VISTA_GAP" \
      --ring "$VISTA_RING" --skip-existing --out "$out" "$@" \
    2>&1 | tee -a "$CAMPAIGN_LOG"
}

# ---------------------------------------------------------------------------
# 0. YOLO11x oracle engine.
#
# THE FIX THAT MATTERS IN THIS FILE. The original degraded silently here: if the
# engine build exceeded `timeout 3600` it logged
#     "WARNING: ... oracle will use yolo11l instead"
# and carried on, swapping the ORACLE DETECTOR from YOLO11x to YOLO11l. The
# oracle defines the ground-truth event set (123 events) that every recall
# number in the paper is scored against. A different detector finds a different
# set of events, so every recall number silently changes meaning while the run
# metadata still says "campaign done". A warning in a 400-line log is not
# adequate protection against that. It is now fatal, and there is no fallback.
#
# Margin note: the measured build is 36 min 51 s against the original's 3600 s
# timeout — 61% of the budget. A thermally throttled or busier machine only has
# to be 1.6x slower to trip it. The default here is 7200 s, overridable.
# ---------------------------------------------------------------------------
ORACLE_PGIE="$VISTA_CONFIG/pgie_yolo11x.txt"
: "${VISTA_ENGINE_TIMEOUT:=7200}"

engine_for_pgie() {  # echo the absolute model-engine-file named by a pgie config
  local pgie="$1" rel
  rel="$(sed -n 's/^model-engine-file=//p' "$pgie" | head -1)"
  [ -n "$rel" ] || return 1
  (cd "$(dirname "$pgie")" && readlink -m "$rel")
}

ensure_oracle_engine() {
  [ -f "$ORACLE_PGIE" ] || vista_die "oracle nvinfer config missing: $ORACLE_PGIE"
  local eng
  eng="$(engine_for_pgie "$ORACLE_PGIE")" || vista_die \
      "$ORACLE_PGIE names no model-engine-file; cannot locate the oracle engine."
  if [ -f "$eng" ]; then
    log "oracle engine present: $eng"
    return 0
  fi
  log "building YOLO11x oracle engine (measured ~37 min on AGX Orin @30W) ..."
  local blog="$VISTA_RESULTS/engine_build_yolo11x.log"
  # stderr is KEPT: the original sent it to /dev/null, so when the build failed
  # the reason was gone and all that survived was the fallback warning.
  if ! timeout "$VISTA_ENGINE_TIMEOUT" python3 \
        "$VISTA_ROOT/scripts/build_engine.py" \
        --config "$ORACLE_PGIE" --batch 4 >"$blog" 2>&1; then
    vista_die "YOLO11x engine build FAILED or timed out after ${VISTA_ENGINE_TIMEOUT}s.
Build log: $blog
NOT falling back to YOLO11l. The oracle detector defines the ground-truth
event set; substituting a weaker detector silently changes every recall number
in the paper. Fix the build, or raise VISTA_ENGINE_TIMEOUT, then re-run."
  fi
  # nvinfer serializes under an auto-generated name; older harness revisions
  # left it in the tree root. Accept either, then place it where the config
  # says it must be.
  if [ ! -f "$eng" ]; then
    local stray
    for stray in "$VISTA_ROOT/model_b4_gpu0_fp16.engine" \
                 "$VISTA_ROOT/$(basename "$eng")"; do
      if [ -f "$stray" ]; then
        mv "$stray" "$eng"
        log "moved $stray -> $eng"
        break
      fi
    done
  fi
  [ -f "$eng" ] || vista_die "YOLO11x engine build reported success but produced no
engine at $eng (build log: $blog). Refusing to continue: see above — there is
deliberately no YOLO11l fallback."
  log "yolo11x engine built: $eng"
}

ensure_oracle_engine

# ---------------------------------------------------------------------------
# 1. Oracle + completeness references (ring=0, run to EOS).
# ring=0 disables the kernel-ring stand-in so every frame is processed; these
# runs are the input to event extraction, not performance measurements.
# ---------------------------------------------------------------------------
log "oracle run (yolo11x, ring=0, to EOS) ..."
eval_run "oracle_x" --pgie "$ORACLE_PGIE" --model-tag x --arm ref \
    --run-timeout 2400

REF_MODELS=(m)
[ "$MODE" = "full" ] && REF_MODELS=(s m l)
for M in "${REF_MODELS[@]}"; do
  log "completeness ref (yolo11$M, ring=0) ..."
  eval_run "ref_$M" --pgie "$VISTA_CONFIG/pgie_yolo11$M.txt" --model-tag "$M" \
      --arm ref --run-timeout 2400
done

# ---------------------------------------------------------------------------
# 2. Policy campaign (7 arms x 50 s). yolo11m is the primary model: 5 repeats.
# Arms are defined in run_eval.py (fifo33 fifo5 dropold fresh-k4 fresh-k2
# imp-k2 salv-k2). fresh-k2 = VISTA-Fresh; imp-k2 = VISTA-Activity;
# fifo33 = Stock-Default; fresh-k4 = the all-admit ablation.
# ---------------------------------------------------------------------------
log "E3: yolo11m x 7 arms x 5 repeats ..."
eval_run "e3_m" --pgie "$VISTA_CONFIG/pgie_yolo11m.txt" --model-tag m \
    --repeats 5 --duration 50

if [ "$MODE" = "full" ]; then
  for M in s l; do
    log "E3: yolo11$M x 7 arms x 3 repeats ..."
    eval_run "e3_$M" --pgie "$VISTA_CONFIG/pgie_yolo11$M.txt" --model-tag "$M" \
        --repeats 3 --duration 50
  done

  # -------------------------------------------------------------------------
  # 3. E6 offset robustness.
  #
  # VISTA (imp-k2) should be flat across injected startup offsets, because it
  # never reads camera timestamps — all ages come from local arrival stamps.
  # The comparison arm is timestamp-based alignment with sync-inputs=1 and the
  # PTS fix disabled, which is what a stock pipeline does with fabricated USB
  # timestamps; it degrades as offsets grow.
  #
  # The skews are the measured live stagger scaled by OFF. The original
  # computed them with a python one-liner containing an unused list, an unused
  # `import sys`, and a `/1.0` no-op, and interpolated $OFF into the program
  # text. Same arithmetic, stated once:
  #   OFF=0.33 -> 0.0,374.5,561.7,187.2   (verified against the archive)
  # -------------------------------------------------------------------------
  for OFF in 0 0.33 0.66 1.0; do
    SK="$(VISTA_SKEW="$VISTA_SKEW" OFF="$OFF" python3 -c '
import os
base = [float(x) for x in os.environ["VISTA_SKEW"].split(",")]
off = float(os.environ["OFF"])
print(",".join(f"{x*off:.1f}" for x in base))')"
    for r in 0 1; do
      log "E6: offset x$OFF r$r (skew=$SK) ..."
      eval_run "e6_off${OFF}_sparq_r$r" \
          --pgie "$VISTA_CONFIG/pgie_yolo11m.txt" --model-tag m \
          --arm imp-k2 --repeats 1 --duration 50 --skew "$SK"
      eval_run "e6_off${OFF}_syncbroken_r$r" \
          --pgie "$VISTA_CONFIG/pgie_yolo11m.txt" --model-tag m \
          --arm fifo33 --repeats 1 --duration 50 --skew "$SK" \
          --extra "--sync --max-latency-ms 33.333 --no-pts-fix --restamp"
    done
  done
fi

log "=== campaign done (mode=$MODE) -> $VISTA_RESULTS ==="
