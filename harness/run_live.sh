#!/usr/bin/env bash
# run_live.sh — the live-camera runs behind TABLE V. 3 arms x 120 s on four
# physical Logitech C920s.  (~7 min wall, plus camera setup)
#
#   ./run_live.sh                 all three arms
#   ./run_live.sh fifo33          one arm (fifo33 | imp | salv)
#   ./run_live.sh --check         preflight only: verify cameras, run nothing
#
# NO SCRIPT FOR THIS CAMPAIGN EVER EXISTED. The archived e4_live/ runs were
# issued by hand and carry no run_meta.json — the only surviving record of how
# they were invoked is the banner at the top of each stderr.log:
#
#   [main] 4 camera(s) [v4l2] 640x480@30 (mjpeg); NEW nvstreammux sync-inputs=OFF
#   timeout=33333us; pts-fix=ON; pgie=.../pgie_yolo11m.txt; sched=imp k=2 log=json
#
# This script is reconstructed from those three banners plus the metrics lines
# ("...over 121.5s"). It is therefore the least certain file in this harness: it
# reproduces everything the banners record, but a flag that left no trace in a
# banner would be invisible to me and is not here. Treat it as a faithful
# reconstruction, not as the original.
#
# WHAT LIVE MEASURES, AND WHAT IT CANNOT
# Live is where the pathology is real rather than emulated: the v4l2 kernel ring
# silently overwrites its newest arrivals upstream of every counter the pipeline
# has. The archived numbers make that concrete — Stock-Default reports
# "processed 6433 of 6433 arrived frames", i.e. a perfect 100% coverage, while
# ~54% of captured frames never reached the pipeline at all. The metric is not
# lying; it cannot see the loss. VISTA's ledger, by contrast, closes on the same
# rig: imp counted 5864 fresh + 0 salvage + 8120 drops = 13984 arrivals, exactly.
#
# What live CANNOT do is recall. There is no oracle: no two runs ever see the
# same photons, so there is nothing to score against. Table V is therefore
# latency, throughput and track churn only. Do not expect a recall column, and
# do not compare these latencies with the replay tables — different input.
#
# NOT DETERMINISTIC. Re-running will not reproduce the archived numbers. It
# should reproduce the archived SHAPE (stock latency several times VISTA's;
# VISTA's ledger closing; salvage inflating track IDs). If it does not, that is
# the interesting result.
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vista_env.sh"

CHECK_ONLY=0
ARMS=(fifo33 imp salv)
case "${1:-}" in
  --check) CHECK_ONLY=1 ;;
  "") ;;
  fifo33|imp|salv) ARMS=("$1") ;;
  *) vista_die "usage: $(basename "$0") [--check | fifo33 | imp | salv]" ;;
esac

OUT="$VISTA_RESULTS/e4_live"
CAM_YAML="$VISTA_CONFIG/camera_params.yaml"
PGIE="$VISTA_CONFIG/pgie_yolo11m.txt"
DURATION=120          # archived runs elapsed 121.5-121.9 s

# --- preflight -------------------------------------------------------------
# The GPU lock matters here for the same reason it does on replay, and the
# cameras matter more: a missing device is the difference between a 4-camera
# and a 3-camera experiment, which changes the load ratio and therefore every
# number, while the app still starts and still writes a plausible CSV.
vista_preflight
[ -f "$CAM_YAML" ] || vista_die "camera config not found: $CAM_YAML"
vista_require_pgie "$PGIE"

vista_log "checking capture devices listed in $(basename "$CAM_YAML") ..."
CAM_YAML="$CAM_YAML" python3 - <<'PY' || vista_die "camera preflight failed (see above)"
import os
import sys
try:
    import yaml
except ImportError:
    sys.exit("[FATAL] pyyaml is required to validate the camera list")
path = os.environ["CAM_YAML"]
with open(path) as f:
    cfg = yaml.safe_load(f)
cams = cfg.get("cameras") or []
if len(cams) != 4:
    print(f"[WARN] {len(cams)} cameras configured, not 4. The paper's live runs "
          f"used 4; load ratio and every latency depend on the count.",
          file=sys.stderr)
missing = []
for c in cams:
    dev = c.get("device") if isinstance(c, dict) else c
    ok = dev and os.path.exists(dev)
    print(f"    {dev:16s} {'OK' if ok else 'MISSING'}")
    if not ok:
        missing.append(dev)
if missing:
    sys.exit(
        f"[FATAL] capture device(s) not present: {', '.join(map(str, missing))}\n"
        f"  On this rig the four C920 capture nodes are /dev/video0,2,4,6 (the\n"
        f"  odd-numbered nodes are UVC metadata, not video). Check the cameras\n"
        f"  are attached, and that all four are on one USB-2 bus at 640x480\n"
        f"  MJPG — 720p x4 exceeds the bus and the third camera fails to\n"
        f"  allocate. Edit the `cameras:` list in {os.path.basename(path)} if\n"
        f"  your node numbering differs.")
print("    all configured capture devices present")
PY

if [ "$CHECK_ONLY" -eq 1 ]; then
  vista_log "--check: preflight passed, nothing run."
  exit 0
fi

mkdir -p "$OUT"

# --- arms ------------------------------------------------------------------
# Reproduced from the archived stderr banners. NOTE: the replay flags
# (--skew-ms/--rate/--gap-every/--ring) are deliberately absent — they apply
# only to --source file, and the app rejects them for v4l2. The live rig
# supplies the real stagger, the real cadence and the real kernel ring, which
# is the entire point of running live.
#
# Stock-Default passes --timeout-us 33333 and NO --mux-config: on the new mux
# that property is inert, so this arm is genuinely the stock configuration,
# batching under config/mux_config.txt. That is what it is meant to be.
arm_args() {
  case "$1" in
    fifo33) printf '%s\n' --timeout-us 33333 ;;
    imp)    printf '%s\n' --sched imp --sched-k 2 ;;
    salv)   printf '%s\n' --sched salvage --sched-k 2 ;;
  esac
}

for ARM in "${ARMS[@]}"; do
  d="$OUT/$ARM"
  if [ -f "$d/metrics.csv" ]; then
    vista_log "$ARM already present — skipping (delete $d to force)"
    continue
  fi
  mkdir -p "$d"
  mapfile -t EXTRA < <(arm_args "$ARM")
  # Scheduler arms also log per-decision records.
  if [ "$ARM" != fifo33 ]; then
    EXTRA+=(--sched-csv "$d/sched.csv")
  fi
  vista_log "live run: $ARM (${DURATION}s) — point the cameras at something moving"
  rc=0
  "$VISTA_BIN" --config "$CAM_YAML" --source v4l2 \
      --pgie-config "$PGIE" --no-sync --log json \
      --duration "$DURATION" --metrics-csv "$d/metrics.csv" \
      "${EXTRA[@]}" \
      > "$d/dets.jsonl" 2> "$d/stderr.log" || rc=$?
  [ "$rc" -eq 0 ] || vista_die "live run '$ARM' exited $rc — see $d/stderr.log"
  # The ledger line is the honest drop account; surface it immediately.
  grep -E '^\[(sched|vista)\]' "$d/stderr.log" | tail -1 || true
  grep -E '^\[metrics\]' "$d/stderr.log" | tail -1 || true
done

vista_log "live runs done -> $OUT"
vista_log "Reminder: no oracle exists for live input; Table V reports latency,"
vista_log "throughput and track churn only — never recall."
