#!/usr/bin/env python3
"""skew_report.py — per-camera service share on the skew microbenchmark.

This computes the paper's "25% -> 48% hot-camera share" headline
(Sec. RQ3 / Table IV's first column). NOTHING in the archived analysis
computed it: the figure that was supposed to be its evidence,
figures/src/make_imp_concentration.py, carries a hardcoded absolute results
path and FAILS OPEN — when the path does not resolve it prints "skip (no
data)", exits 0, and emits a blank plot. So the number had no generator.

DEFINITION AS IMPLEMENTED: hot-camera share = admits charged to cam0 divided
by all admits, over a run's sched.csv, counting both `admit` and
`admit-salvage` rows (a salvage admit consumes an inference slot exactly like
a fresh admit, so excluding it would understate service). Rows are counted
over the WHOLE run — sched.csv has no warmup trim because it is an event log,
not a sampled series; the 5 s trim used for age applies to metrics.csv only.

The clip sets are composited so that cam0 is busy, cam1 and cam2 are empty,
and cam3 has one rare ~10 s event. With four cameras an even split is 25%, so
"25%" means "the activity term bought nothing" and "48%" means "cam0 took
1.9x its even share, paid for by the empty cameras".

Usage:
  python3 skew_report.py                 # all Table IV configurations
  python3 skew_report.py --clip-set brief
"""
import argparse
import csv
import os
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns  # noqa: E402
from _paths import DATA_ROOT  # noqa: E402

ADMIT_EVENTS = ("admit", "admit-salvage")
HOT_CAM = 0

# Paper's printed hot-cam share column (evaluation.tex:193-196).
PAPER_SHARE = {
    ("VISTA-Fresh, stash 1"): "25",
    ("VISTA-Fresh, stash 2"): "25",
    ("VISTA-Activity, stash 1"): "26--29",
    ("VISTA-Activity, stash 2"): "48",
}


def admits_by_cam(run_dir):
    """Counter{cam -> admits} from one run's sched.csv.

    Returns None when the run has no sched.csv — that is not an error, it is
    what a stock (unscheduled) run looks like, and such a run has no admit
    decisions to attribute.
    """
    p = os.path.join(run_dir, "sched.csv")
    if not os.path.exists(p):
        return None
    c = Counter()
    with open(p) as fh:
        for r in csv.DictReader(fh):
            if r["event"] in ADMIT_EVENTS:
                c[int(r["cam"])] += 1
    return c


def hot_share(run_dir):
    c = admits_by_cam(run_dir)
    if not c:
        return None
    total = sum(c.values())
    return c[HOT_CAM] / total if total else None


def row_shares(row):
    out = []
    for d in _campaigns.run_dirs(row):
        s = hot_share(d)
        if s is not None:
            out.append((os.path.relpath(d, DATA_ROOT), s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip-set", choices=["brief", "persistent"], default=None)
    ap.add_argument("--per-run", action="store_true",
                    help="print every run, not just the per-config median")
    args = ap.parse_args()

    filters = {}
    if args.clip_set:
        filters["clip_set"] = args.clip_set

    print("hot-camera (cam0) service share — admits on cam0 / all admits\n"
          "even share with 4 cameras = 25.0%\n")
    print(f"{'clip set':<11s} {'config':<24s} {'median':>7s} {'min':>6s} "
          f"{'max':>6s}  {'paper':>7s}  per-camera admit split (median run)")

    seen = {}
    for row in _campaigns.rows("table4", **filters):
        shares = row_shares(row)
        if not shares:
            print(f"{row['clip_set']:<11s} {row['paper_name']:<24s} "
                  f"  no sched.csv")
            continue
        vals = [s for _, s in shares]
        med = statistics.median(vals)
        # per-camera split of the run closest to the median, for context
        mid = min(shares, key=lambda t: abs(t[1] - med))
        c = admits_by_cam(os.path.join(DATA_ROOT, mid[0]))
        tot = sum(c.values())
        split = " ".join(f"c{k}:{100*c[k]/tot:4.1f}%" for k in sorted(c))
        paper = PAPER_SHARE.get(row["paper_name"], "?")
        print(f"{row['clip_set']:<11s} {row['paper_name']:<24s} "
              f"{100*med:6.1f}% {100*min(vals):5.1f}% {100*max(vals):5.1f}%  "
              f"{paper:>7s}  {split}")
        if args.per_run:
            for name, s in shares:
                print(f"{'':<11s}   {name:<40s} {100*s:5.1f}%")
        seen.setdefault(row["paper_name"], []).extend(vals)

    print("\nThe paper quotes ONE share per config, but each config was run on "
          "BOTH clip sets;\nthe quoted column is the pair pooled:")
    for name, vals in seen.items():
        if len(vals) > 3:
            print(f"  {name:<24s} pooled median {100*statistics.median(vals):5.1f}%  "
                  f"range {100*min(vals):.1f}-{100*max(vals):.1f}%  "
                  f"(paper: {PAPER_SHARE.get(name,'?')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
