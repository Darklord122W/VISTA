#!/usr/bin/env bash
# run_gates.sh — implementation gates G0-G4. These must pass before any policy
# experiment is trusted. Short runs (25 s) on yolo11n, i.e. light load, where
# the scheduler's invariants must hold trivially; if they fail here they are
# broken, not merely stressed.
#
#   ./run_gates.sh                 run the gate runs, then assert  (~3 min)
#   ./run_gates.sh --analyze-only [DIR]
#                                  assert against an EXISTING gate directory,
#                                  running nothing — no GPU, no clips. DIR
#                                  defaults to $VISTA_DATA_ROOT/gates. No gate
#                                  runs ship with this repository, so DIR must
#                                  name gate output you produced:
#                                      ./run_gates.sh              # produce it
#                                      ./run_gates.sh --analyze-only runs/gates
#
# THIS SCRIPT'S REASON TO EXIST. The original printed a table and then printed
# sentences like "G1 PASS if fill dist for sched runs is a spike at K" — leaving
# a human to check. It ended with a python heredoc that always exited 0, so
# `run_gates.sh && run_campaign.sh` would happily proceed over a failed gate,
# and CI could never catch a regression. Every gate below now asserts and the
# script exits non-zero if any fails.
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vista_env.sh"

ANALYZE_ONLY=0
OUT="$VISTA_RESULTS/gates"
if [ "${1:-}" = "--analyze-only" ]; then
  ANALYZE_ONLY=1
  # No gate output ships. With no DIR, fall back to $VISTA_DATA_ROOT/gates and
  # let vista_require_data_root say so in one line if that is unset.
  OUT="${2:-$(vista_require_data_root)/gates}"
  [ -d "$OUT" ] || vista_die \
"no gate output at: $OUT
--analyze-only asserts against gate runs that already exist; this repository
ships none. Produce them on a Jetson with:  ./run_gates.sh
or pass a directory:                        ./run_gates.sh --analyze-only DIR"
elif [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,21p' "$0"; exit 0
fi

# ---------------------------------------------------------------------------
# G0 — environment. The gate before the gates: the invariants that make every
# later number meaningful. Asserted here rather than assumed, because both of
# its failure modes are silent (see vista_env.sh).
# ---------------------------------------------------------------------------
if [ "$ANALYZE_ONLY" -eq 0 ]; then
  echo "=== G0: environment ==="
  vista_preflight                                  # binary + GPU clock pinned
  CLIPS="$(vista_require_clips myclipsForEXP)"
  PGIE="$VISTA_CONFIG/pgie_config.txt"             # yolo11n: the light-load model
  vista_require_pgie "$PGIE"
  mkdir -p "$OUT"
  echo "G0 PASS"

  run() {  # run <name> <extra app args...>
    local name="$1"; shift
    echo "=== gate run: $name ==="
    local rc=0
    "$VISTA_BIN" --config "$VISTA_CONFIG/camera_params.yaml" \
      --source file --replay-dir "$CLIPS" \
      --pgie-config "$PGIE" \
      --skew-ms "$VISTA_SKEW" --rate "$VISTA_RATE" \
      --gap-every "$VISTA_GAP" --ring "$VISTA_RING" \
      --no-sync --log json --duration 25 \
      --metrics-csv "$OUT/${name}.csv" "$@" \
      > "$OUT/${name}_dets.jsonl" 2> "$OUT/${name}_stderr.log" || rc=$?
    # A crashed gate run must not be analysed as if it were data.
    [ "$rc" -eq 0 ] || vista_die "gate run '$name' exited $rc — see $OUT/${name}_stderr.log"
  }

  # G1/G2 (scheduler on, light load): batch atomicity + ledger closure.
  run g1_fresh_k2_n --sched fresh --sched-k 2 --sched-csv "$OUT/g1_fresh_k2_n_sched.csv"
  run g1_fresh_k4_n --sched fresh --sched-k 4 --sched-csv "$OUT/g1_fresh_k4_n_sched.csv"
  # G3: salvage mode — same-source frame pairs must survive NvSORT.
  run g3_salv_k2_n  --sched salvage --sched-k 2 --sched-csv "$OUT/g3_salv_k2_n_sched.csv"
  # G4: the untouched path, and the keep-newest config baseline.
  run g4_off_n      --timeout-us 33333
  run g4_dropold_n  --timeout-us 33333 --dropold
else
  echo "=== analyze-only: $OUT (nothing will be run) ==="
  [ -d "$OUT" ] || vista_die "no such gate directory: $OUT"
fi

echo
echo "=== gate assertions ==="
python3 - "$OUT" <<'PY'
"""Assert G1-G4 over a gate directory. Exits 1 on the first failing gate's
report (all gates are evaluated; the summary lists every one)."""
import csv
import gzip
import os
import re
import statistics
import sys
from collections import Counter

OUT = sys.argv[1]

# The scheduler's end-of-run ledger line. Both prefixes are accepted on
# purpose: the archived data was produced by the paper binary, which printed
# "[sched]"; the shipped module prints "[vista]". The rest of the line is
# byte-identical between them (vista/src/vista_scheduler.cpp::print_summary),
# so one pattern covers both. Renaming without updating this regex would
# silently turn G2 into "no ledger found" on new runs.
LEDGER = re.compile(
    r"\[(?:sched|vista)\]\s+(?P<mode>\w+):\s+(?P<releases>\d+)\s+releases\s+"
    r"\([\d.]+/s\),\s+(?P<fresh>\d+)\s+fresh\s+\+\s+(?P<salvage>\d+)\s+salvage"
    r"\s+admitted,\s+(?P<drops>\d+)\s+policy\s+drops,\s+s_hat\s+"
    r"(?P<s_hat>[\d.]+)\s+ms\s+over\s+(?P<dur>[\d.]+)\s+s\.")

GATES = {
    "g1_fresh_k2_n": {"k": 2, "sched": True},
    "g1_fresh_k4_n": {"k": 4, "sched": True},
    "g3_salv_k2_n":  {"k": 2, "sched": True},
    "g4_off_n":      {"sched": False},
    "g4_dropold_n":  {"sched": False},
}

ATOMICITY_MIN_PCT = 98.0   # archive measures 99.93 (K=2) / 99.84 (K=4)
G4_MAX_REL_DIFF = 0.05     # archive measures 0.0008 (0.08%)

# The analysis-wide warmup trim (analysis/policy_report.py, and
# analysis/weightsweep/aggregate_runs.py, both use WARMUP_S = 5.0). The
# DESCRIPTIVE table below trims to match them, so that one run yields ONE age
# everywhere in this artifact. Untrimmed, g1_fresh_k2_n's mean e2e is 25.9 ms
# against the analysis's 24.19 ms — the same run, two ages, for no reason
# except that the first two batches (1186 ms and 1155 ms) are measuring the
# TensorRT engine load rather than the scheduler.
#
# The GATE ASSERTIONS below deliberately do NOT trim: G1 is a claim about
# every batch the scheduler released, G2 about every arrival in the run, and
# G4 about whole-run throughput. Trimming those would narrow what they check.
WARMUP_S = 5.0

results = []               # (gate, name, ok, detail)


def add(gate, name, ok, detail):
    results.append((gate, name, ok, detail))


def rows(name):
    p = os.path.join(OUT, f"{name}.csv")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return list(csv.DictReader(f))


def post_warmup(rs):
    """Drop the first WARMUP_S seconds, exactly as the analysis scripts do."""
    t0 = float(rs[0]["t_mono"])
    sel = [r for r in rs if float(r["t_mono"]) - t0 >= WARMUP_S]
    return sel or rs


def open_maybe_gz(path):
    """dets are archived gzipped (212 MB -> 67 MB); fresh runs are plain."""
    return (gzip.open(path, "rt") if path.endswith(".gz") else open(path))


def dets_path(name):
    for suffix in (".jsonl", ".jsonl.gz"):
        p = os.path.join(OUT, f"{name}_dets{suffix}")
        if os.path.exists(p):
            return p
    return None


def count_dets(name):
    """(json_lines, total_detections, skipped_non_json).

    dets.jsonl is NOT valid JSONL: the application shares stdout with
    GStreamer, which injects lines like 'Opening in BLOCKING MODE'. Measured 15
    such lines in 2884 here. Any line not starting with '{' is skipped."""
    import json
    p = dets_path(name)
    if p is None:
        return None
    lines = dets = skipped = 0
    with open_maybe_gz(p) as f:
        for ln in f:
            if not ln.lstrip().startswith("{"):
                skipped += 1
                continue
            try:
                o = json.loads(ln)
            except ValueError:
                skipped += 1
                continue
            lines += 1
            dets += int(o.get("num_detections", 0))
    return lines, dets, skipped


def ledger(name):
    p = os.path.join(OUT, f"{name}_stderr.log")
    if not os.path.exists(p):
        return None
    with open(p, errors="replace") as f:
        for ln in f:
            m = LEDGER.search(ln)
            if m:
                return {k: (int(v) if v.isdigit() else v)
                        for k, v in m.groupdict().items()}
    return None


# --- report table ----------------------------------------------------------
print(f"descriptive stats over the post-{WARMUP_S:.0f}s-warmup window, matching "
      f"analysis/policy_report.py; `arrivals` is the whole-run total.")
print(f"{'run':16s} {'batches':>7s} {'fill':>5s} {'f/s':>7s} {'e2e':>7s} "
      f"{'p99':>7s} {'arrivals':>8s}  fill-dist")
for name in GATES:
    rs = rows(name)
    if not rs:
        print(f"{name:16s}  NO DATA")
        continue
    post = post_warmup(rs)
    nin = [int(r["n_in_batch"]) for r in post]
    e2e = [float(r["e2e_ms"]) for r in post if float(r["e2e_ms"]) >= 0]
    dur = float(post[-1]["t_mono"]) - float(post[0]["t_mono"])
    p99 = sorted(e2e)[int(0.99 * (len(e2e) - 1))] if e2e else -1
    # arrivals_cum is a cumulative counter: the run total is the last row's
    # value, which a head-trim must not touch.
    print(f"{name:16s} {len(post):7d} {statistics.mean(nin):5.2f} "
          f"{sum(nin)/max(dur,1e-6):7.1f} "
          f"{statistics.mean(e2e) if e2e else -1:7.1f} {p99:7.1f} "
          f"{int(rs[-1]['arrivals_cum']):8d}  {dict(sorted(Counter(nin).items()))}")
print()

# --- G1: batch atomicity ---------------------------------------------------
# The mechanism claim: the scheduler releases exactly K frames and the mux
# forms them into ONE batch. If the INI is wrong or the release path races, K
# frames scatter across batches and every latency claim loses its meaning.
for name, cfg in GATES.items():
    if not cfg.get("sched"):
        continue
    rs = rows(name)
    if not rs:
        add("G1", name, False, "no metrics.csv")
        continue
    nin = [int(r["n_in_batch"]) for r in rs]
    k = cfg["k"]
    pct = 100.0 * Counter(nin)[k] / len(nin)
    # The one non-K batch is the final partial release at shutdown.
    add("G1", name, pct >= ATOMICITY_MIN_PCT,
        f"{pct:.2f}% of {len(nin)} batches carried exactly K={k} "
        f"(need >={ATOMICITY_MIN_PCT}%)")

# --- G2: drop-ledger closure ----------------------------------------------
# The paper's honesty claim: arrivals == admitted_fresh + admitted_salvage +
# policy_drops. Nothing vanishes silently.
#
# This is parsed from stderr, NOT from the CSV's drops_cum column: drops_cum
# reads 0 in all five gate CSVs (it counts the mux's dropped-buffer signal,
# which never fires — the scheduler drops upstream of the mux). Reading
# drops_cum here would make G2 a tautology that passes on a broken scheduler.
for name, cfg in GATES.items():
    if not cfg.get("sched"):
        continue
    rs = rows(name)
    led = ledger(name)
    if not rs:
        add("G2", name, False, "no metrics.csv")
        continue
    if led is None:
        add("G2", name, False,
            "no [sched]/[vista] ledger line in stderr.log — cannot verify "
            "closure (did the summary format change?)")
        continue
    arrivals = int(rs[-1]["arrivals_cum"])
    total = led["fresh"] + led["salvage"] + led["drops"]
    add("G2", name, total == arrivals,
        f"{led['fresh']} fresh + {led['salvage']} salvage + {led['drops']} "
        f"drops = {total} vs {arrivals} arrivals (delta {total - arrivals})")

# --- G3: salvage pairs survive the tracker ---------------------------------
# Salvage can put two frames from the SAME camera in one batch. NvSORT is not
# obliged to like that. The gate is that the run produces detections and logs
# no errors.
name = "g3_salv_k2_n"
c = count_dets(name)
if c is None:
    add("G3", name, False, "no dets file")
else:
    lines, dets, skipped = c
    add("G3", name, lines > 0 and dets > 0,
        f"{lines} frame records, {dets} detections "
        f"({skipped} non-JSON lines skipped)")
errp = os.path.join(OUT, f"{name}_stderr.log")
if os.path.exists(errp):
    with open(errp, errors="replace") as f:
        errs = [ln.strip() for ln in f
                if re.search(r"\bERROR\b|\bCRITICAL\b|Assertion", ln)]
    add("G3", name + " (stderr)", not errs,
        "no ERROR/CRITICAL lines" if not errs
        else f"{len(errs)} error line(s), first: {errs[0][:90]}")

# --- G4: the keep-newest config baseline is inert --------------------------
# DROP-OLD is the "just configure the queue" answer to this problem. The claim
# is that it does nothing, because the mux never backpressures. At light load
# the two arms must therefore be indistinguishable. (This is an equivalence
# check, so it is deliberately loose: 5% against a measured 0.08%. The
# load-bearing comparison happens at heavy load in E3, not here.)
ro, rd = rows("g4_off_n"), rows("g4_dropold_n")
if not ro or not rd:
    add("G4", "off vs dropold", False, "missing g4_off_n or g4_dropold_n")
else:
    def fps(rs):
        dur = float(rs[-1]["t_mono"]) - float(rs[0]["t_mono"])
        return sum(int(r["n_in_batch"]) for r in rs) / max(dur, 1e-6)
    fo, fd = fps(ro), fps(rd)
    rel = abs(fo - fd) / max(fo, 1e-6)
    add("G4", "off vs dropold", rel < G4_MAX_REL_DIFF,
        f"{fo:.1f} vs {fd:.1f} f/s — relative difference {rel*100:.2f}% "
        f"(need <{G4_MAX_REL_DIFF*100:.0f}%); dropold is expected to be inert")

# --- verdict ---------------------------------------------------------------
print(f"{'gate':5s} {'run':24s} {'verdict':8s} detail")
failed = 0
for gate, name, ok, detail in results:
    if not ok:
        failed += 1
    print(f"{gate:5s} {name:24s} {'PASS' if ok else 'FAIL':8s} {detail}")
print()
if failed:
    print(f"RESULT: {failed} of {len(results)} checks FAILED.")
    sys.exit(1)
print(f"RESULT: all {len(results)} checks passed.")
PY
