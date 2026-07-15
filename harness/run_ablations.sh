#!/usr/bin/env bash
# run_ablations.sh — the mechanism ablations and diagnostics.
#
#   ./run_ablations.sh              everything          (~35 min)
#   ./run_ablations.sh depth        e9: pipeline depth  (~12 min)
#   ./run_ablations.sh enriched     exact-emission runs (~3 min)
#   ./run_ablations.sh offset       E6 offset sweep     (~14 min)
#   ./run_ablations.sh impdiag      importance diagnostics (~2 min)
#   ./run_ablations.sh briefdepth1  the depth-1 brief arms (~5 min)
#
# ---------------------------------------------------------------------------
# depth (e9_depth1, e9_depth2, e9_depth1_v2, e9_depth2_v2)
#   --sched-depth 1  vs  the default 2, at fresh-k2, 60 s, 3 repeats.
# depth is the release gate: in-flight <= (depth-1)*K frames. depth=1 admits
# nothing while a batch is in flight, so the GPU idles between completions
# (strictly completion-clocked, zero overlap); depth=2 keeps one batch queued
# behind the running one, which is what makes the pipeline work-conserving. The
# ablation is the evidence for choosing d=2, and for the claim that going deeper
# would only rebuild the standing queue VISTA exists to remove.
#
# The _v2 directories are a straight REPLICATION of the same configuration —
# same flags, same everything. They are not a second variant. Both are shipped
# because a reader is entitled to see the repeat.
#
# ---------------------------------------------------------------------------
# enriched (enriched_m_fifo, enriched_m_imp, enriched_m_salv)
# Runs carrying exact per-frame emission stamps, used to validate the TTA
# metric against the coarser derivation and to test salvage actionability.
# The salvage answer was negative and is reported as such: held frames were
# redundant with a neighbour frame within 150 ms in 435 of 435 cases.
#
# ---------------------------------------------------------------------------
# offset (e6_off*)
# Also run by `run_campaign.sh full`; exposed here to re-run in isolation.
# VISTA never reads camera timestamps — all ages come from local arrival
# stamps — so it should be FLAT across injected startup offsets. The comparison
# arm is timestamp alignment (sync-inputs=1, PTS fix off, restamped), i.e. what
# a stock pipeline does with the fabricated timestamps USB capture produces.
#
# ---------------------------------------------------------------------------
# impdiag (impdiag_d1, impdiag_heavy)
# Two single-run probes into why the importance term is inert on uniform
# activity, both on clips_importance:
#   impdiag_d1     --sched-depth 1 --sched-w 0.3,0.5,0.2
#   impdiag_heavy  --sched-w 0.02,0.96,0.02   (importance almost alone)
# impdiag_heavy is the useful one: if a 0.96 importance weight still does not
# concentrate service on the busy camera, the term is not merely underweighted
# — the mechanism is capped elsewhere (stash=1; see run_skew_study.sh).
#
# briefdepth1 (brief_fresh-k2_r*, brief_imp-k2_r*)
# The depth-1 arms on the brief clips; briefD2ctl_* (run by run_skew_study.sh)
# is their depth-2 control. Named "brief_*" in the archive, one run per dir.
#
# RECONSTRUCTION NOTE. No script survives for any of these. Every parameter is
# taken verbatim from the authors' run_meta.json cmd arrays for
# {e9_*,enriched_m_*,e6_*,impdiag_*,brief_*}, which are not distributed here.
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vista_env.sh"

WHAT="${1:-all}"
case "$WHAT" in all|depth|enriched|offset|impdiag|briefdepth1) ;; *)
  vista_die "usage: $(basename "$0") [all|depth|enriched|offset|impdiag|briefdepth1]" ;;
esac

vista_preflight
CLIPS="$(vista_require_clips myclipsForEXP)"
PGIE="$VISTA_CONFIG/pgie_yolo11m.txt"
vista_require_pgie "$PGIE"

run_eval() {  # run_eval <out-dir> <args...>
  local out="$VISTA_RESULTS/$1"; shift
  python3 "$VISTA_HARNESS/run_eval.py" --pgie "$PGIE" --model-tag m \
      --skew "$VISTA_SKEW" --rate "$VISTA_RATE" --gap-every "$VISTA_GAP" \
      --ring "$VISTA_RING" --skip-existing --out "$out" "$@"
}

# --- e9: release-gate depth ------------------------------------------------
if [ "$WHAT" = all ] || [ "$WHAT" = depth ]; then
  vista_log "--- e9: release-gate depth (fresh-k2, 60 s, 3 repeats)"
  for suffix in "" "_v2"; do   # _v2 = replication of the identical config
    run_eval "e9_depth1$suffix" --arm fresh-k2 --repeats 3 --duration 60 \
        --replay-dir "$CLIPS" --extra "--sched-depth 1"
    run_eval "e9_depth2$suffix" --arm fresh-k2 --repeats 3 --duration 60 \
        --replay-dir "$CLIPS"          # depth 2 = the default; passed implicitly
  done
fi

# --- enriched emission-stamp runs ------------------------------------------
if [ "$WHAT" = all ] || [ "$WHAT" = enriched ]; then
  vista_log "--- enriched: exact emission stamps (52 s)"
  run_eval "enriched_m_fifo" --arm fifo33  --repeats 1 --duration 52 --replay-dir "$CLIPS"
  run_eval "enriched_m_imp"  --arm imp-k2  --repeats 1 --duration 52 --replay-dir "$CLIPS"
  run_eval "enriched_m_salv" --arm salv-k2 --repeats 1 --duration 52 --replay-dir "$CLIPS"
fi

# --- E6 offset robustness --------------------------------------------------
if [ "$WHAT" = all ] || [ "$WHAT" = offset ]; then
  vista_log "--- E6: offset robustness (imp-k2 vs timestamp-aligned sync)"
  for OFF in 0 0.33 0.66 1.0; do
    SK="$(VISTA_SKEW="$VISTA_SKEW" OFF="$OFF" python3 -c '
import os
base = [float(x) for x in os.environ["VISTA_SKEW"].split(",")]
off = float(os.environ["OFF"])
print(",".join(f"{x*off:.1f}" for x in base))')"
    for r in 0 1; do
      run_eval "e6_off${OFF}_sparq_r$r" --arm imp-k2 --repeats 1 \
          --duration 50 --replay-dir "$CLIPS" --skew "$SK"
      run_eval "e6_off${OFF}_syncbroken_r$r" --arm fifo33 --repeats 1 \
          --duration 50 --replay-dir "$CLIPS" --skew "$SK" \
          --extra "--sync --max-latency-ms 33.333 --no-pts-fix --restamp"
    done
  done
fi

# --- importance diagnostics ------------------------------------------------
if [ "$WHAT" = all ] || [ "$WHAT" = impdiag ]; then
  PERS="$(vista_require_clips clips_importance)"
  vista_log "--- impdiag: why the importance term is inert on uniform activity"
  run_eval "impdiag_d1" --arm imp-k2 --repeats 1 --duration 42 \
      --replay-dir "$PERS" --extra "--sched-depth 1 --sched-w 0.3,0.5,0.2"
  run_eval "impdiag_heavy" --arm imp-k2 --repeats 1 --duration 42 \
      --replay-dir "$PERS" --extra "--sched-w 0.02,0.96,0.02"
fi

# --- depth-1 arms on the brief clips ---------------------------------------
if [ "$WHAT" = all ] || [ "$WHAT" = briefdepth1 ]; then
  BRIEF="$(vista_require_clips clips_importance_brief)"
  vista_log "--- brief clips at depth 1 (control: briefD2ctl_* in run_skew_study.sh)"
  for r in 0 1 2; do
    run_eval "brief_fresh-k2_r$r" --arm fresh-k2 --repeats 1 --duration 42 \
        --replay-dir "$BRIEF" --extra "--sched-depth 1"
    run_eval "brief_imp-k2_r$r" --arm imp-k2 --repeats 1 --duration 42 \
        --replay-dir "$BRIEF" --extra "--sched-depth 1"
  done
fi

vista_log "ablations done -> $VISTA_RESULTS"
