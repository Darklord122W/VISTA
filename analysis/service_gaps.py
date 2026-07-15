#!/usr/bin/env python3
"""service_gaps.py — per-camera service-gap distribution (the D_hard statistic).

VISTA promises a hard per-camera service interval D_hard: a camera that has
gone unserved for longer than D_hard is force-admitted ahead of value order
(the fairness floor). This script measures the gaps actually observed, which
is the only way to check that promise against the data.

=============================================================================
D_hard IS NOT A CONSTANT, AND IS NOT 150 ms.
=============================================================================
150 ms is tau_max, the STALENESS bound on a fresh frame — a different
quantity. D_hard is derived from the system's own measured pace, so a heavier
detector automatically earns a longer grace period (vista_scheduler.cpp):

    D_fair = 2 * (N / K) * s_hat        D_hard = 4 * D_fair

i.e. D_hard = 8 * (N/K) * s_hat, where s_hat is the scheduler's running
estimate of service time. At the paper's operating point (N=4, K=2) with the
s_hat of 200.7 ms that e3_m/fresh-k2_r0's own stderr reports, D_hard is about
3.2 SECONDS, not 150 ms. Anyone who checks these runs against a 150 ms bound
will "find" thousands of violations that are not violations. This script
therefore derives the bound per run instead of accepting a number.

CAVEAT ON THE DERIVED BOUND (important): the archived runs were produced by
the OLD scheduler binary (namespace mcrt, thread "sparq-sched", "[sched]" log
prefix). The formula above is read from the NEW vista module. They are
believed to be the same rule, but that is NOT verified — the paper's binary
exists in no commit, so the archived build's D_hard cannot be read back from
source. The bound printed below is therefore INDICATIVE. It is offered as a
reference line, not as a conformance verdict.

DEFINITION AS IMPLEMENTED: for each camera, take the timestamps of its admit
rows in sched.csv (`admit` and `admit-salvage` both count — both consume an
inference slot and both reset the camera's starvation clock), sort, and take
successive differences, in ms. The first gap is measured from the camera's
first admit, not from run start, so warmup does not manufacture a gap.

WHAT THIS CAN AND CANNOT SHOW: sched.csv records the scheduler's DECISIONS,
so a gap here is a gap in ADMISSION, which is what the fairness floor bounds.
It is not the interval between EMITTED detections for that camera — after
admission the batch still has to clear the GPU. Right statistic for the
release rule; wrong one for a claim about output cadence.

Note also that the force only fires when the starved camera actually HAS a
stashed frame (`forced = since_served > d_hard && !c.fresh.empty()`): a camera
delivering nothing cannot be force-admitted, so a gap may legitimately exceed
D_hard when the camera supplied no frames.

Usage:
  python3 service_gaps.py                    # the Table II VISTA rows
  python3 service_gaps.py --table table4     # the skew microbenchmark
  python3 service_gaps.py --run e3_m/fresh-k2_r0
"""
import argparse
import csv
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns  # noqa: E402
import _sched_log  # noqa: E402
from _paths import DATA_ROOT, data  # noqa: E402

ADMIT_EVENTS = ("admit", "admit-salvage")


def d_hard_ms(run_dir, num_cams=4):
    """Indicative D_hard for a run: 8 * (N/K) * s_hat, from its own stderr
    summary. See the caveat in the module docstring — this reconstructs the
    NEW module's rule and applies it to data from the OLD binary."""
    return _sched_log.d_hard_ms(_sched_log.parse(run_dir), num_cams)


def gaps(run_dir):
    """{cam -> [gap_ms]} from one run's sched.csv, or None if absent."""
    p = os.path.join(run_dir, "sched.csv")
    if not os.path.exists(p):
        return None
    t_by_cam = defaultdict(list)
    with open(p) as fh:
        for r in csv.DictReader(fh):
            if r["event"] in ADMIT_EVENTS:
                t_by_cam[int(r["cam"])].append(float(r["t"]))  # seconds, monotonic
    out = {}
    for cam, ts in t_by_cam.items():
        ts.sort()
        out[cam] = [1000.0 * (b - a) for a, b in zip(ts, ts[1:])]
    return out


def pctl(vals, q):
    """Nearest-rank percentile — the same estimator as the age p99 elsewhere."""
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[int(q * (len(s) - 1))]


def report(run_dir, label=None):
    g = gaps(run_dir)
    rel = os.path.relpath(run_dir, DATA_ROOT)
    lab = label or rel
    if g is None:
        print(f"{lab:<46s}  no sched.csv (stock run — no admission decisions)")
        return None
    allgaps = [x for v in g.values() for x in v]
    if not allgaps:
        print(f"{lab:<46s}  no admits")
        return None
    dh = d_hard_ms(run_dir)
    dh_s = f"{dh:7.0f}" if dh else "      ?"
    over = [x for x in allgaps if dh and x > dh]
    print(f"{lab:<46s}  p50 {pctl(allgaps,0.50):6.1f}  p99 {pctl(allgaps,0.99):7.1f}  "
          f"max {max(allgaps):7.1f}  | D_hard~{dh_s}  "
          f"over: {len(over) if dh else '?'}")
    return allgaps, dh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="table2",
                    help="campaigns.yaml table whose rows to report")
    ap.add_argument("--run", default=None,
                    help="a single run dir, relative to the data root")
    ap.add_argument("--per-cam", action="store_true")
    args = ap.parse_args()

    print("per-camera service gaps (ms) between successive admits (sched.csv).")
    print("D_hard is derived per run as 8*(N/K)*s_hat and is INDICATIVE only — "
          "see the module docstring.\n")

    if args.run:
        d = data(args.run)
        report(d)
        if args.per_cam:
            for cam, v in sorted((gaps(d) or {}).items()):
                print(f"    cam{cam}: n={len(v)} p50 {pctl(v,0.5):.1f} "
                      f"p99 {pctl(v,0.99):.1f} max {max(v):.1f}")
        return 0

    worst = []
    for row in _campaigns.table(args.table).get("rows", []):
        for d in _campaigns.run_dirs(row):
            r = report(d, f"{row['paper_name']:<18s} "
                          f"{os.path.relpath(d, DATA_ROOT)}")
            if r and r[1]:
                worst.append((max(r[0]), r[1], os.path.relpath(d, DATA_ROOT)))
    if worst:
        print("\nworst observed gap vs its run's indicative D_hard:")
        for mx, dh, name in sorted(worst, reverse=True)[:5]:
            print(f"  {name:<34s} max {mx:7.1f} ms  D_hard~{dh:7.0f} ms  "
                  f"{'WITHIN' if mx <= dh else 'EXCEEDS'}")
    print("\nThese are gaps in ADMISSION, which is what the fairness floor "
          "bounds — not\nthe interval between emitted detections for a camera.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
