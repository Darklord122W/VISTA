#!/usr/bin/env python3
"""count_drops.py — how many paced frames each model drops (E1 data).

The subtlety this script exists for: the pipeline CANNOT see its own drops.
`arrivals_cum` in the metrics CSV counts frames that reached the first
instrumented pad (source-bin egress), i.e. survivors of the transport ring.
The ring drops the *newest* frames upstream of that pad, so
processed/arrivals reads ~100% even when half the input died (paper F5).

To count the true drops we need the PACED input (frames fed in before the
ring). Two independent references give it:
  (1) YOLO11n keeps up (rho=0.84), so its ring never overflows -> its
      arrivals == the paced input every model was fed.
  (2) A ring=0 run (the completeness/oracle pass) processes every paced
      frame; its arrivals also == the paced input.
Both agree (~5934 over a 50 s window). drops(model) = paced - arrivals(model).

Usage: python3 count_drops.py [deadline_ms]   (default 33.3)
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import data as data_path  # noqa: E402


def cell(model, ms):
    p = data_path(f"e1_yolo11{model}", f"push_{ms}ms.csv")
    with open(p) as fh:
        rows = list(csv.DictReader(fh))
    arrivals = int(rows[-1]["arrivals_cum"])          # post-ring survivors
    processed = sum(int(r["n_real"]) for r in rows)   # actually inferred
    return arrivals, processed


def main(argv=None):
    # argparse claims -h/--help. Without it, `count_drops.py --help` read
    # "--help" as a deadline and died on an uncaught FileNotFoundError for
    # push_--helpms.csv.
    ap = argparse.ArgumentParser(
        prog="count_drops.py",
        description=__doc__.split("\n\n")[0],
        epilog=("DEADLINE_MS selects the E1 push traces push_<DEADLINE_MS>ms"
                ".csv under the run-data root; the shipped sweep is 10, 20, "
                "33.3, 66.7. Read-only: prints a table, writes nothing."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "deadline_ms", nargs="?", default="33.3", metavar="DEADLINE_MS",
        help="batching deadline naming the push trace (default: %(default)s)")
    ms = ap.parse_args(argv).deadline_ms
    probe = data_path("e1_yolo11n", f"push_{ms}ms.csv")
    if not os.path.exists(probe):
        ap.error(f"no E1 push trace for deadline {ms!r}: {probe} does not "
                 f"exist. Shipped deadlines are 10, 20, 33.3, 66.7.")
    paced, _ = cell("n", ms)   # n never overflows the ring -> paced baseline
    print(f"deadline {ms} ms, ring=4, replay (paced input = {paced} "
          f"frames/50 s, from YOLO11n which keeps up)\n")
    print(f"{'model':8}{'arrived':>9}{'dropped':>9}{'drop%':>8}"
          f"{'processed':>11}{'pipeline cov':>14}")
    for m in ["n", "s", "m", "l"]:
        arr, proc = cell(m, ms)
        drop = paced - arr
        print(f"yolo11{m:2}{arr:9}{drop:9}{100*drop/paced:7.1f}%"
              f"{proc:11}{100*proc/max(arr,1):13.1f}%")


if __name__ == "__main__":
    main()
