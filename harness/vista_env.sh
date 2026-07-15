# vista_env.sh — every path and platform invariant the harness depends on.
#
# Source it, do not execute it:
#     . "$(dirname "$0")/vista_env.sh"
#
# Every variable below is `: "${VAR:=default}"`, so exporting VAR beforehand
# overrides it. This file exists because the original harness hardcoded
# /home/vista/... in run_campaign.sh, run_gates.sh, run_eval.py and five
# analysis scripts; nothing outside that one account could run any of it.
#
# WHY THE ASSERTIONS ARE HERE AND NOT IN A README
# The paper's numbers are only comparable across runs if the GPU clock is
# pinned and the batcher is the one VISTA expects. Both failures are SILENT:
# an unlocked GPU produces different latencies with perfectly valid-looking
# metadata (run_eval.py RECORDS gpu_clock_hz but never checked it), and a
# mismatched mux INI breaks batch atomicity without an error. Asserting costs
# milliseconds; not asserting costs a re-run of a 90-minute campaign, or worse,
# a number in a paper that nobody can reproduce.

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Resolve this file's own directory, whether sourced from bash or from a script
# invoked through a symlink. BASH_SOURCE[0] is this file even when sourced.
_vista_env_self="${BASH_SOURCE[0]:-$0}"
VISTA_HARNESS="$(cd "$(dirname "$_vista_env_self")" && pwd)"
unset _vista_env_self

# The artifact repository root (the directory containing harness/).
: "${VISTA_REPO:=$(cd "$VISTA_HARNESS/.." && pwd)}"

# VISTA_ROOT — the tree the application runs out of: it must contain the
# binary, config/ and models/. It defaults to this repository, whose app/
# builds vista_multicam. Point it at a different checkout to drive a different
# build (e.g. the original multicam_perception_rt, whose binary is
# cpp/multicam_rt — set VISTA_BIN too in that case).
: "${VISTA_ROOT:=$VISTA_REPO}"

# VISTA_BIN — the experiment application. This is NOT libvista: libvista is the
# scheduler library an integrator links; VISTA_BIN is the harness-driven app
# that links it and adds the replay/skew/metrics instrumentation the campaigns
# need.
: "${VISTA_BIN:=$VISTA_ROOT/app/vista_multicam}"

: "${VISTA_CONFIG:=$VISTA_ROOT/config}"
: "${VISTA_MODELS:=$VISTA_ROOT/models}"

# VISTA_CLIPS — the directory holding the replay clip sets, i.e. it contains
# myclipsForEXP/, clips_importance/ and clips_importance_brief/, each with
# cam0.mp4..cam3.mp4.
#
# THE CLIPS ARE NOT IN THIS REPOSITORY AND WILL NOT BE. The office footage is
# of an identifiable person who did not consent to publication. Blurring is not
# an option either: it changes what YOLO detects, and the oracle every recall
# number is scored against is built from what YOLO detects. The activity-skew
# clips are composited from NVIDIA's DeepStream sample streams (EULA, not
# redistributable) — rebuild those locally with scripts/make_skew_clips.py.
# Consequence, stated plainly: the replay campaigns below reproduce the
# METHOD, not the paper's clips. Point VISTA_CLIPS at your own footage.
: "${VISTA_CLIPS:=$VISTA_REPO/clips}"

# VISTA_RESULTS — where runs are written. This is also what you point
# $VISTA_DATA_ROOT at to analyse them (see analysis/README.md); the analysis
# half has NO default for that variable, deliberately, because a default that
# resolved to an empty directory would let a scoring run "succeed" over no
# data. There is no shipped archive to overwrite: this repository ships the
# code, not the runs.
: "${VISTA_RESULTS:=$VISTA_REPO/runs}"

# GPU_SYSFS — Jetson Orin GPU devfreq node. Holds cur_freq/min_freq/max_freq.
: "${GPU_SYSFS:=/sys/devices/platform/17000000.gpu/devfreq/17000000.gpu}"

# The clock every number in the paper was measured at (Jetson AGX Orin 64GB,
# MODE_30W). Locked via `jetson_clocks`; verified through the sysfs node above.
: "${VISTA_GPU_EXPECT_HZ:=612000000}"

# ---------------------------------------------------------------------------
# The measured live reference (2026-07-07), injected onto the replay clips.
# See cpp/experiments/frame_timing/REPLAY_SKEW.md in the application tree.
# Changing any of these makes runs incomparable to the archive.
# ---------------------------------------------------------------------------

# Per-camera startup stagger (ms) and PTS rate factors: 0.9608 turns a nominal
# 30 fps clip into the C920's true 32.026 ms cadence; the small per-camera
# differences stand in for crystal drift.
: "${VISTA_SKEW:=0,1134.8,1702.1,567.2}"
: "${VISTA_RATE:=0.96063,0.96099,0.96087,0.96128}"

# VISTA_GAP — `--gap-every N` = drop 2 consecutive frames every N frames, per
# camera.
#
# THIS FLAG IS OVERLOADED. It has two unrelated uses and confusing them is
# silent:
#   44 = TIMING FIDELITY. Chosen so the delivered rate matches the live rig
#        (~29.8 fps): 42 of every 44 frames survive (95.45%) at the 32.026 ms
#        cadence. Every campaign in the paper except Static-Decimation uses 44.
#   3  = the Static-Decimation BASELINE (keep 1 of every 3 = DEC-1/3), an
#        experimental arm, not a fidelity setting. `--gap-every 4` is DEC-1/2.
#
# The application's own --help says "measured live: ~70". Do not use 70. The
# app's pipeline_builder.cpp documents why: at 70/275 the emulated grid drifts
# the WRONG WAY against real time, frames look future-stamped, nothing is ever
# late, and sync-on trivially "succeeds" — a pure artifact. The help text has
# not been corrected; this is the corrected value.
: "${VISTA_GAP:=44}"

# Bounded drop-newest queue after the pacer: the v4l2 kernel ring stand-in.
# 4 = live. 0 = off, used only by the completeness/oracle references, which
# must process every frame.
: "${VISTA_RING:=4}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

vista_log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
vista_die() { printf '\n[FATAL] %s\n' "$*" >&2; exit 1; }

# vista_require_data_root — resolve $VISTA_DATA_ROOT for the scripts that READ
# runs rather than produce them. No default, and the wording matches
# analysis/_paths.py: this repository ships code, not runs, and a root that
# defaulted to an empty directory would let an analysis "succeed" over nothing.
# Echoes the absolute path on success.
vista_require_data_root() {
  [ -n "${VISTA_DATA_ROOT:-}" ] || vista_die \
"VISTA_DATA_ROOT is not set.
The measurement archive is not distributed with this repository: this artifact
ships code, not runs. Set VISTA_DATA_ROOT to a directory of campaign run
directories, or produce one with harness/run_campaign.sh (see harness/README.md)."
  [ -d "$VISTA_DATA_ROOT" ] || vista_die \
"VISTA_DATA_ROOT=$VISTA_DATA_ROOT is not a directory."
  (cd "$VISTA_DATA_ROOT" && pwd)
}

# vista_require_bin — the application must exist and be executable.
vista_require_bin() {
  [ -x "$VISTA_BIN" ] || vista_die \
"application not found or not executable:
    VISTA_BIN=$VISTA_BIN
Build it:  make -C '$VISTA_REPO/app'
or point VISTA_BIN at an existing build."
}

# vista_require_clips SET — one clip set (e.g. myclipsForEXP) with 4 cameras.
# Echoes the absolute path on success so callers can capture it.
vista_require_clips() {
  local set_name="$1" dir="$VISTA_CLIPS/$1"
  [ -d "$dir" ] || vista_die \
"replay clip set '$set_name' not found at:
    $dir
The clips are NOT shipped: they are office footage of an identifiable person
(see vista_env.sh). Replay campaigns cannot run without them. Set VISTA_CLIPS
to a directory containing $set_name/cam0.mp4..cam3.mp4, or use your own 4-camera
footage — in which case your numbers are yours, not the paper's, because the
oracle event set is clip-specific."
  local i
  for i in 0 1 2 3; do
    [ -f "$dir/cam$i.mp4" ] || vista_die "clip set '$set_name' is incomplete: missing $dir/cam$i.mp4"
  done
  printf '%s\n' "$dir"
}

# vista_require_pgie FILE — an nvinfer config, and the engine it names.
# The engine is hardware- and TensorRT-version-specific: an engine built on
# another machine is not merely slow, it will refuse to deserialize. None ship.
vista_require_pgie() {
  local pgie="$1"
  [ -f "$pgie" ] || vista_die \
"nvinfer config not found: $pgie
Expected under VISTA_CONFIG=$VISTA_CONFIG"
  local rel eng
  rel="$(sed -n 's/^model-engine-file=//p' "$pgie" | head -1)"
  [ -n "$rel" ] || return 0   # config builds its engine on first use
  eng="$(cd "$(dirname "$pgie")" && readlink -m "$rel")"
  [ -f "$eng" ] || vista_die \
"TensorRT engine named by $(basename "$pgie") is missing:
    $eng
Engines are not shipped (hardware/TensorRT-version specific — an engine from
another machine is invalid, not just slow). Build it:
    python3 '$VISTA_ROOT/scripts/build_engine.py' --config '$pgie' --batch 4
See docs/reproduction/ for the weight download + engine build recipe."
}

# vista_assert_gpu_lock — refuse to produce numbers at an unpinned clock.
#
# The lock is min_freq == max_freq == VISTA_GPU_EXPECT_HZ. Checking cur_freq
# alone is not enough: an unlocked GPU idling at 612 MHz reads correct here and
# then boosts under load, mid-run. Escape hatch: VISTA_ALLOW_UNLOCKED_GPU=1
# downgrades this to a warning, for smoke tests on other hardware. Do not use it
# for anything you intend to compare with the paper.
vista_assert_gpu_lock() {
  local why=""
  if [ ! -r "$GPU_SYSFS/cur_freq" ]; then
    why="no GPU devfreq node at GPU_SYSFS=$GPU_SYSFS (not a Jetson?)"
  else
    local cur min max
    cur="$(cat "$GPU_SYSFS/cur_freq" 2>/dev/null || echo 0)"
    min="$(cat "$GPU_SYSFS/min_freq" 2>/dev/null || echo 0)"
    max="$(cat "$GPU_SYSFS/max_freq" 2>/dev/null || echo 0)"
    if [ "$min" != "$max" ]; then
      why="GPU clock is NOT locked: min_freq=$min != max_freq=$max (governor free to boost mid-run)"
    elif [ "$cur" != "$VISTA_GPU_EXPECT_HZ" ]; then
      why="GPU locked at ${cur} Hz, expected ${VISTA_GPU_EXPECT_HZ} Hz"
    fi
  fi
  if [ -n "$why" ]; then
    if [ "${VISTA_ALLOW_UNLOCKED_GPU:-0}" = "1" ]; then
      printf '[WARN] %s\n[WARN] VISTA_ALLOW_UNLOCKED_GPU=1 — continuing. These numbers are NOT comparable to the paper.\n' "$why" >&2
      return 0
    fi
    vista_die \
"$why
Every latency in the paper was measured on a Jetson AGX Orin 64GB at
MODE_30W with the GPU pinned to ${VISTA_GPU_EXPECT_HZ} Hz. An unpinned clock
silently changes every number while the run metadata still looks valid.
Lock it:
    sudo nvpmodel -m 2 && sudo jetson_clocks
Then confirm: cat $GPU_SYSFS/cur_freq
To run anyway on other hardware (results not comparable):
    VISTA_ALLOW_UNLOCKED_GPU=1 $0 ..."
  fi
  vista_log "GPU clock locked at ${VISTA_GPU_EXPECT_HZ} Hz (min==max==cur). OK"
}

# vista_preflight — what every campaign script checks before burning GPU hours.
vista_preflight() {
  vista_require_bin
  vista_assert_gpu_lock
  mkdir -p "$VISTA_RESULTS"
}

# vista_env_dump — the provenance a reader needs to trust an output directory.
vista_env_dump() {
  printf 'VISTA_REPO    = %s\n' "$VISTA_REPO"
  printf 'VISTA_ROOT    = %s\n' "$VISTA_ROOT"
  printf 'VISTA_BIN     = %s (sha256 %s)\n' "$VISTA_BIN" \
         "$( [ -f "$VISTA_BIN" ] && sha256sum "$VISTA_BIN" | cut -c1-16 || echo MISSING)"
  printf 'VISTA_CONFIG  = %s\n' "$VISTA_CONFIG"
  printf 'VISTA_CLIPS   = %s\n' "$VISTA_CLIPS"
  printf 'VISTA_RESULTS = %s\n' "$VISTA_RESULTS"
  printf 'GPU_SYSFS     = %s (cur %s Hz)\n' "$GPU_SYSFS" \
         "$(cat "$GPU_SYSFS/cur_freq" 2>/dev/null || echo n/a)"
  printf 'skew=%s rate=%s gap-every=%s ring=%s\n' \
         "$VISTA_SKEW" "$VISTA_RATE" "$VISTA_GAP" "$VISTA_RING"
}
