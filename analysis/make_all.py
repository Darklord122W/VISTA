#!/usr/bin/env python3
"""make_all.py — regenerate every table and figure the paper depends on, from
run data you supply.

NO RUN DATA SHIPS with this repository: point $VISTA_DATA_ROOT at a directory
of campaign run directories (produce one with harness/run_campaign.sh) or every
script here exits with one message saying so. See analysis/README.md.

TIERS
-----
--tier derived   (default) Trust the scoring JSONs under the derived dir and
                 recompute only what sits above them (medians, age/p99 from
                 metrics.csv, tables, figures). Those JSONs are an output of
                 --tier rescore, so on a fresh data root this tier has nothing
                 to trust: run rescore first.

--tier rescore   Ignore any existing scoring JSON. Rebuild every oracle from
                 the raw dets and re-score every run from scratch. This is the
                 tier that turns raw runs into derived data, and the only one
                 that would catch a corrupted or hand-edited scoring JSON.

Table IV has no derived tier at all — no scoring JSON covers its stash-2 rows —
so it is always rescored. See make_table4.py.

WHAT --check MEANS: each table generator compares its output cell-by-cell
against the values printed in the paper and exits nonzero on any mismatch. It
is meaningful only against the runs the paper was written from: your own
campaign measures your own hardware and clips, so --check will report
mismatches that are differences, not defects. A mismatch is a finding to
understand, not a reason to adjust a script.

ORDER (this is the real dependency chain):
    raw run dirs
      -> match_events.py        oracle build + per-run scoring
      -> clean_events.py        event-quality stratification
      -> policy_report.py       per-arm medians (one campaign at a time)
      -> make_table{2,3,4}.py   the paper's tables (cross-campaign)
      -> figures
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns  # noqa: E402
from _paths import DATA_ROOT, DERIVED_DIR, FIG_DIR  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def run(argv, label):
    print(f"\n{'='*74}\n== {label}\n{'='*74}", flush=True)
    t0 = time.time()
    rc = subprocess.call([sys.executable] + argv, cwd=HERE)
    dt = time.time() - t0
    print(f"-- {label}: rc={rc} ({dt:.1f}s)", flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["derived", "rescore"], default="derived")
    ap.add_argument("--check", action="store_true",
                    help="compare every table cell against the paper")
    ap.add_argument("--skip-figures", action="store_true")
    args = ap.parse_args()

    print(f"data root   : {DATA_ROOT}")
    print(f"derived dir : {DERIVED_DIR}")
    print(f"figure dir  : {FIG_DIR}")
    print(f"tier        : {args.tier}")

    missing = _campaigns.verify_all()
    if missing:
        print("\nABORT: campaigns.yaml references directories that do not "
              "exist. Fix the data root or the registry before generating "
              "anything — a table quietly built from n-1 repeats is worse "
              "than no table.")
        return 2

    failures = []
    check = ["--check"] if args.check else []

    # --- tables -----------------------------------------------------------
    for script, label in [("make_table2.py", "Table II — main policy comparison (YOLO11m)"),
                          ("make_table3.py", "Table III — other load points (YOLO11s/l)")]:
        rc = run([script, "--tier", args.tier, "--tex", "--json"] + check, label)
        if rc:
            failures.append(label)

    # Table IV is rescore-only by construction.
    rc = run(["make_table4.py", "--tex", "--json"] + check,
             "Table IV — skew microbenchmark (always rescored; metric=event_recall)")
    if rc:
        failures.append("Table IV")

    # --- supporting reports ----------------------------------------------
    for script, label in [
            ("live_report.py", "Table V — live validation"),
            ("skew_report.py", "hot-camera service share (the 25% -> 48% claim)"),
            ("event_split.py", "oracle event split (the 37/33/28/25 claim)"),
            ("service_gaps.py", "per-camera service gaps (D_hard)"),
    ]:
        rc = run([script], label)
        if rc:
            failures.append(label)

    # --- figures ----------------------------------------------------------
    if not args.skip_figures:
        for argv, label in [
                (["e1_figures.py"], "Table I + capacity figures"),
                (["frame_funnel_fig.py"], "frame funnel figure"),
                (["tta_curve.py", "m"], "TTA curve (YOLO11m)"),
                (["policy_report.py", "s"], "per-arm aggregate, YOLO11s"),
                (["policy_report.py", "l"], "per-arm aggregate, YOLO11l"),
        ]:
            rc = run(argv, label)
            if rc:
                failures.append(label)

    # policy_report.py m is deliberately NOT run here. Against the ORIGINAL
    # archive it would build e3_m_aggregate.json from e3_m/imp-k2_*, which is
    # pre-importance-bugfix data contradicting the draft's VISTA-Activity row
    # by ~7 coverage points; the published row comes from e8_impfix_* via
    # make_table2.py. Run it by hand if you want the diagnostic, and read the
    # `superseded:` block in campaigns.yaml first.

    if args.tier == "rescore":
        for argv, label in [
                (["clean_events.py"], "clean-event stratification (the '63 clean events')"),
                (["enriched_analysis.py"], "exact-TTA validation (enriched build)"),
        ]:
            rc = run(argv, label)
            if rc:
                failures.append(label)

    print(f"\n{'='*74}")
    if failures:
        print("FAILED / MISMATCHED:")
        for f in failures:
            print(f"  - {f}")
        print("\nA cell mismatch is a finding. Report it; do not tune the "
              "generator to force agreement.")
        return 1
    print("all generators completed" + (" and every checked cell matches the "
                                        "paper" if args.check else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
