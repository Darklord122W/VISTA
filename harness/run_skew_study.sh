#!/usr/bin/env bash
# run_skew_study.sh — the camera-activity-skew study behind TABLE IV.
#
#   ./run_skew_study.sh           oracles + all 8 arms  (~25 min)
#   ./run_skew_study.sh oracles   the two YOLO11x oracles only (~7 min)
#   ./run_skew_study.sh arms      the 8 arms only (~18 min; needs the oracles)
#
# THE QUESTION. VISTA-Activity (imp-k2) is the paper's OPTIONAL extension, and
# the paper's own uniform-activity result is that it buys nothing: with every
# camera equally busy, an activity term has nothing to concentrate on. This
# study asks the only fair follow-up — when activity IS skewed, and the
# scheduler is actually able to act on it, does the term pay?
#
# TWO CLIP SETS, two kinds of skew:
#   clips_importance        activity that PERSISTS on one camera
#   clips_importance_brief  activity that is BRIEF and moves between cameras
#
# THE STASH IS THE POINT. imp needs stash >= depth to matter. With the default
# stash=1 a camera holds one frame, so a "hot" camera cannot win two releases in
# a row no matter how valuable it is — the mechanism is capped before the policy
# can express a preference. stash=2 lifts that cap. Hence the pairing: every
# clip set is run at stash=1 AND stash=2, fresh AND imp. That is the 2x2x2 = 8
# arms of Table IV, and it is why the stash-1 arms are controls, not filler.
#
#   arm                    clips                   stash  mode
#   briefD2ctl_fresh-k2    clips_importance_brief    1    fresh   (control)
#   briefD2ctl_imp-k2      clips_importance_brief    1    imp     (control)
#   briefS2_fresh-k2       clips_importance_brief    2    fresh
#   briefS2_imp-k2         clips_importance_brief    2    imp
#   impcmp_fresh-k2_r*     clips_importance          1    fresh   (control)
#   impcmp_imp-k2_r*       clips_importance          1    imp     (control)
#   persS2_fresh-k2        clips_importance          2    fresh
#   persS2_imp-k2          clips_importance          2    imp
#
# ORACLES. Each clip set needs its OWN YOLO11x ground truth (charBrief_x,
# charImp_x): events are clip-specific, so the myclipsForEXP oracle (oracle_x)
# says nothing about these clips. ring=0, run to EOS, every frame processed.
#
# ---------------------------------------------------------------------------
# THINGS A RE-RUNNER MUST KNOW ABOUT TABLE IV
#
# 1. TABLE IV USES A DIFFERENT METRIC FROM TABLES II/III. Table IV reports
#    event_recall, scored at event ONSET (frame time). Tables II/III report
#    tta_recall, scored at EMISSION time. Both live in the same analysis JSONs
#    under the same roof, and they are not interchangeable: for the brief clips
#    event_recall@250 median is .723 while tta_recall@250 median is .682. The
#    abstract's headline 0.30 -> 0.71 is the ONSET metric. Mixing them silently
#    produces a table that looks fine and compares nothing.
#
# 2. THE STASH-2 ROWS HAVE NO ARCHIVED SCORING OUTPUT. No analysis JSON in the
#    archive references briefS2/persS2/briefD2ctl: those run directories are
#    dated 2026-07-10 14:39, AFTER the newest analysis JSON (2026-07-09 23:36).
#    The raw dets exist in the authors' archive, so those rows are
#    RECOMPUTABLE from it — but they were never backed by a committed scoring
#    artifact. Re-deriving them is a real task, not a formality.
#
# 3. --sched-stash EXISTS IN NO COMMIT of the paper's application. The binary
#    that produced these runs was a dirty working tree; run_meta.json's git_sha
#    for them is not the code that ran. (run_eval.py now records the binary's
#    sha256 precisely so this cannot recur.) The flag is implemented in the
#    shipped module, so these arms are re-runnable here.
#
# RECONSTRUCTION NOTE. No script survives. Everything below is taken verbatim
# from the archived run_meta.json cmd arrays. The inconsistent directory layout
# is REPRODUCED, not tidied: briefD2ctl/briefS2/persS2 are one directory holding
# three repeats, while impcmp is three directories holding one run each. The
# analysis keys on these names.
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vista_env.sh"

WHAT="${1:-all}"
case "$WHAT" in all|oracles|arms) ;; *)
  vista_die "usage: $(basename "$0") [all|oracles|arms]" ;;
esac

vista_preflight
BRIEF="$(vista_require_clips clips_importance_brief)"
PERS="$(vista_require_clips clips_importance)"
PGIE_M="$VISTA_CONFIG/pgie_yolo11m.txt"
PGIE_X="$VISTA_CONFIG/pgie_yolo11x.txt"
vista_require_pgie "$PGIE_M"

run_eval() {  # run_eval <out-dir> <args...>
  local out="$VISTA_RESULTS/$1"; shift
  python3 "$VISTA_HARNESS/run_eval.py" \
      --skew "$VISTA_SKEW" --rate "$VISTA_RATE" --gap-every "$VISTA_GAP" \
      --skip-existing --out "$out" "$@"
}

# --- oracles ---------------------------------------------------------------
if [ "$WHAT" = all ] || [ "$WHAT" = oracles ]; then
  vista_require_pgie "$PGIE_X"
  vista_log "--- oracle: clips_importance_brief (charBrief_x) ..."
  run_eval "charBrief_x" --pgie "$PGIE_X" --model-tag x --arm ref \
      --replay-dir "$BRIEF" --ring 0 --run-timeout 2400
  vista_log "--- oracle: clips_importance (charImp_x) ..."
  run_eval "charImp_x" --pgie "$PGIE_X" --model-tag x --arm ref \
      --replay-dir "$PERS" --ring 0 --run-timeout 2400
fi

# --- the 8 arms ------------------------------------------------------------
if [ "$WHAT" = all ] || [ "$WHAT" = arms ]; then
  # arm_group <dir> <clips> <arm> <repeats> [extra...]
  arm_group() {
    local dir="$1" clips="$2" arm="$3" reps="$4"; shift 4
    vista_log "arm: $dir ($arm x $reps) ..."
    run_eval "$dir" --pgie "$PGIE_M" --model-tag m --arm "$arm" \
        --repeats "$reps" --duration 42 --replay-dir "$clips" \
        --ring "$VISTA_RING" "$@"
  }

  # Brief-activity clips. stash=1 controls, then stash=2.
  vista_log "--- brief activity (clips_importance_brief)"
  arm_group "briefD2ctl_fresh-k2" "$BRIEF" fresh-k2 3
  arm_group "briefD2ctl_imp-k2"   "$BRIEF" imp-k2   3
  arm_group "briefS2_fresh-k2"    "$BRIEF" fresh-k2 3 --extra "--sched-stash 2"
  arm_group "briefS2_imp-k2"      "$BRIEF" imp-k2   3 --extra "--sched-stash 2"

  # Persistent-activity clips. Same 2x2, but the stash=1 controls are archived
  # one-run-per-directory under a different prefix (impcmp, not persD2ctl).
  vista_log "--- persistent activity (clips_importance)"
  for r in 0 1 2; do
    arm_group "impcmp_fresh-k2_r$r" "$PERS" fresh-k2 1
    arm_group "impcmp_imp-k2_r$r"   "$PERS" imp-k2   1
  done
  arm_group "persS2_fresh-k2" "$PERS" fresh-k2 3 --extra "--sched-stash 2"
  arm_group "persS2_imp-k2"   "$PERS" imp-k2   3 --extra "--sched-stash 2"
fi

vista_log "skew study done -> $VISTA_RESULTS"
vista_log "NOTE: Table IV scores event_recall (onset), NOT tta_recall (emission)."
