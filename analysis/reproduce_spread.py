#!/usr/bin/env python3
"""
Reproduce the paper's cross-camera "nearest-frame spread" (RT-BEV Fig. 5a),
the number quoted as ~8.9 ms / 1.6 ms, from a raw capture.csv.

Metric (identical to analyze_timing.py:approx_sync_spreads):
  For every reference frame t of cam0 (in the steady-state window), build a
  "synchronized sample set" = {t} plus, for each other camera, that camera's
  single frame whose TRUE capture time is nearest to t. The sample's spread is
  max(members) - min(members). We report the distribution of those spreads.

TRUE capture time (CRITICAL): the kernel capture instant, reconstructed as
    cap_mono_ns = pts_ns + base_time_ns
where pts_ns is the buffer PTS the PTS-restore probe put back (kernel capture
time expressed as pipeline running-time) and base_time_ns comes from meta.json.
This is the ABSOLUTE instant the sensor grabbed the frame.

DO NOT use the mono_ns column: that is when the *pad probe fired* (after USB
dequeue + jpegparse + decode + queueing), so it carries per-camera decode/
transport latency and inflates the spread ~5x (an easy mistake — it gives
~7.6 ms instead of the true ~1.6 ms). analyze_timing.py:95 uses pts_ns+base.

INPUT DATA: this tool reads a frame-timing capture run (capture.csv +
meta.json), which is NOT an inference run dir — it comes from the separate
timing harness. No capture ships with this repository: pass a path to your
own, or set $VISTA_TIMING_ROOT (no default) and pass a bare name resolved
under it.

Measured on the authors' baseline_pinned_rerun capture (2026-07-15), which is
not distributed here:
p50 1.62 ms, p90 7.41 ms, p99 31.79 ms over 3464 sample sets — consistent
with the ~1.6 ms figure quoted for the true-capture metric, and with the
~7.6 ms the mono_ns mistake would have produced showing up at p90.

Usage:
  python3 reproduce_spread.py <run_dir> [ref_cam] [warmup_s] [tail_s]
Example:
  python3 reproduce_spread.py baseline_pinned_rerun
  python3 reproduce_spread.py /abs/path/to/frame_timing/results/baseline_pinned_rerun
"""
import argparse, csv, sys, os, json, statistics, bisect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import _env_dir  # noqa: E402

#: Where frame-timing captures live. Not the inference data root, and it has
#: no default: no capture ships. A bare RUN_DIR name resolves under it; an
#: absolute or explicitly relative path needs it not at all.
TIMING_ROOT = _env_dir("VISTA_TIMING_ROOT")


def resolve(run_dir):
    """Bare name -> under TIMING_ROOT; anything path-like -> as given."""
    if os.path.isdir(run_dir):
        return run_dir
    if TIMING_ROOT:
        cand = os.path.join(TIMING_ROOT, run_dir)
        if os.path.isdir(cand):
            return cand
    return run_dir

def load(run_dir):
    run_dir = resolve(run_dir)
    if not os.path.isdir(run_dir):
        raise SystemExit(
            f"no such run directory: {run_dir}\n"
            f"Pass a RUN DIRECTORY containing capture.csv + meta.json. No "
            f"frame-timing capture ships with this repository; produce one "
            f"with the timing harness, then pass its path or set "
            f"$VISTA_TIMING_ROOT (currently "
            f"{TIMING_ROOT or 'unset'}) and pass a bare name.")
    for need in ("capture.csv", "meta.json"):
        if not os.path.exists(os.path.join(run_dir, need)):
            raise SystemExit(f"{run_dir} has no {need} — this tool reads a "
                             f"frame-timing capture, not an inference run dir.")
    base = json.load(open(os.path.join(run_dir, "meta.json")))["base_time_ns"]
    rows = []
    with open(os.path.join(run_dir, "capture.csv")) as f:
        for r in csv.DictReader(f):
            cap_mono = int(r["pts_ns"]) + base          # TRUE capture instant
            rows.append((int(r["cam"]), cap_mono))
    return os.path.join(run_dir, "capture.csv"), rows

def pctl(a, q):
    a = sorted(a)
    return a[min(len(a) - 1, int(q * len(a)))]

def spread(rows, ref_cam=0, warmup_s=2.0, tail_s=1.1):
    cams = sorted({c for c, _ in rows})
    t0 = min(t for _, t in rows)
    t_end = max(t for _, t in rows)
    lo = t0 + warmup_s * 1e9
    hi = t_end - tail_s * 1e9                     # trim last ~1.1 s (steady window)
    by = {c: sorted(t for cc, t in rows if cc == c) for c in cams}
    ref = [t for t in by[ref_cam] if lo <= t <= hi]
    others = [c for c in cams if c != ref_cam]
    diffs = []
    for t in ref:
        members = [t]
        ok = True
        for c in others:
            arr = by[c]
            i = bisect.bisect_left(arr, t)
            cand = [arr[j] for j in (i - 1, i) if 0 <= j < len(arr)]
            if not cand:
                ok = False; break
            members.append(min(cand, key=lambda x: abs(x - t)))
        if ok:
            diffs.append((max(members) - min(members)) / 1e6)   # ns -> ms
    return cams, diffs

def build_parser():
    # argparse claims -h/--help. Without it, `reproduce_spread.py --help` read
    # "--help" as a run dir and no args at all died on an uncaught IndexError.
    ap = argparse.ArgumentParser(
        prog="reproduce_spread.py",
        description=__doc__.split("\n\n")[0].strip(),
        epilog=("RUN_DIR is a frame-timing capture dir containing capture.csv "
                "+ meta.json, given as a path or as a bare name under the "
                "timing root ($VISTA_TIMING_ROOT — no default; no capture "
                "ships with this repository). "
                "Read-only: prints the spread distribution, writes nothing."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", metavar="RUN_DIR",
                    help="frame-timing capture dir (contains capture.csv)")
    ap.add_argument("ref_cam", nargs="?", type=int, default=0,
                    metavar="REF_CAM",
                    help="reference camera id (default: %(default)s)")
    ap.add_argument("warmup", nargs="?", type=float, default=2.0,
                    metavar="WARMUP_S",
                    help="seconds trimmed from the start (default: %(default)s)")
    ap.add_argument("tail", nargs="?", type=float, default=1.1,
                    metavar="TAIL_S",
                    help="seconds trimmed from the end (default: %(default)s)")
    return ap


if __name__ == "__main__":
    _args = build_parser().parse_args()
    path, ref_cam = _args.run_dir, _args.ref_cam
    warmup, tail = _args.warmup, _args.tail
    resolved, rows = load(path)
    cams, d = spread(rows, ref_cam, warmup, tail)
    print(f"file        : {resolved}")
    print(f"cameras     : {cams}   reference cam: {ref_cam}")
    print(f"total frames: {len(rows)}   sample sets: {len(d)}")
    print(f"nearest-frame spread (true capture), ms:")
    print(f"  p50 = {statistics.median(d):.2f}")
    print(f"  p90 = {pctl(d, 0.90):.2f}")
    print(f"  p99 = {pctl(d, 0.99):.2f}")
    print(f"  max = {max(d):.2f}")
    print(f"  vs RT-BEV hardware-synced nuScenes reference: 39-46 ms")
