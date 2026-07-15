#!/usr/bin/env python3
"""live_report.py — Table V: live validation on the physical cameras.

Columns: frames seen | throughput (relative) | age (mean / p99) | drop
visibility.

NO ORACLE, NO REPEATS, AND ONE ESTIMATED DENOMINATOR. Read this before
quoting anything it prints:

  * There is no ground truth for live footage. An oracle pass would have to
    score the frames the stock pipeline never received, which by definition do
    not exist on disk. So this table reports throughput and age only — no
    coverage, no yield, no recall.
  * Each row is ONE 120 s run. The paper's caption says "three 120 s runs";
    the archive holds three live run dirs (fifo33, imp, salv) — three runs of
    DIFFERENT policies, one each, not three repeats of one policy. The paper's
    table prints two of the three; salv is unreported. This script prints all
    three.
  * The "~14,000" denominator in the paper's Stock-Default cell is NOT
    measured by that run. The stock run can only count 6,433 arrivals — the
    frames that survived the transport ring — and cannot see what the ring
    dropped upstream of its first probe. That blindness is the paper's point
    (F5), but it also means the denominator is inferred from the camera's
    nominal delivery rate, not observed. The VISTA run's 13,984 arrivals are
    the closest thing to a measurement of what the cameras actually delivered,
    which is why the paper cross-checks the two against each other.

PRIVACY: the live footage itself is not shipped — it contains an identifiable
person. Only the derived metrics.csv / sched.csv / dets survive here. Do not
propose recovering the footage by blurring: re-encoding changes YOLO's
detections and would invalidate the archived event ground truth.
"""
import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns  # noqa: E402
import _sched_log  # noqa: E402
from _paths import DATA_ROOT, derived  # noqa: E402
from policy_report import run_metrics  # noqa: E402


def sched_events(run_dir):
    """{event -> count} from sched.csv. None for stock runs.

    NOTE: sched.csv holds ADMITS ONLY — no drop rows exist in it anywhere in
    the archive. The drop ledger comes from stderr.log via _sched_log. Do not
    compute a drop count from this.
    """
    p = os.path.join(run_dir, "sched.csv")
    if not os.path.exists(p):
        return None
    c = Counter()
    with open(p) as fh:
        for r in csv.DictReader(fh):
            c[r["event"]] += 1
    return c


def run_row(run_dir):
    m = run_metrics(run_dir)
    with open(os.path.join(run_dir, "metrics.csv")) as fh:
        rows = list(csv.DictReader(fh))
    arrivals = int(rows[-1]["arrivals_cum"])
    processed = sum(int(r["n_real"]) for r in rows)
    dur = float(rows[-1]["t_mono"]) - float(rows[0]["t_mono"])
    info = _sched_log.parse(run_dir)
    return {
        "run": os.path.relpath(run_dir, DATA_ROOT),
        "arrivals": arrivals,
        "processed": processed,
        "duration_s": dur,
        "frames_s": sum(int(r["n_in_batch"]) for r in rows) / max(dur, 1e-6),
        "age_mean_ms": m["e2e_mean"],
        "age_p99_ms": m["e2e_p99"],
        "sched_csv_events": dict(sched_events(run_dir) or {}) or None,
        "stderr_ledger": info or None,
        "ledger_closes": _sched_log.ledger_closes(info),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    t5 = _campaigns.table("table5")
    rows = []
    for row in t5["rows"]:
        for d in _campaigns.run_dirs(row):
            r = run_row(d)
            r["paper_name"] = row["paper_name"]
            rows.append(r)

    base = next((r for r in rows if r["paper_name"] == "Stock-Default"), None)
    base_fps = base["frames_s"] if base else float("nan")

    print("\nTable V — live validation, YOLO11m, physical cameras "
          "(one run per policy, no oracle)\n")
    print(f"{'policy':<26s} {'arrivals':>8s} {'proc':>7s} {'f/s':>6s} "
          f"{'thr.':>6s} {'age ms':>7s} {'p99':>6s}  drop visibility")
    for r in rows:
        thr = r["frames_s"] / base_fps if base_fps == base_fps else float("nan")
        led = r["stderr_ledger"]
        # A stock run's stderr still has a [metrics] line (so `led` is
        # non-empty) but no [sched] summary, hence no drop ledger at all —
        # which is precisely the "upstream, uncounted" condition.
        if not led or "policy_drops" not in led:
            vis = "upstream, uncounted"
        else:
            closes = r["ledger_closes"]
            vis = (f"explicit, counted ({led['policy_drops']} policy drops; "
                   f"ledger {'CLOSES' if closes else 'DOES NOT CLOSE'})")
        print(f"{r['paper_name']:<26s} {r['arrivals']:8d} {r['processed']:7d} "
              f"{r['frames_s']:6.1f} {thr:5.2f}x {r['age_mean_ms']:7.0f} "
              f"{r['age_p99_ms']:6.0f}  {vis}")

    print("\nvs the paper's Table V (evaluation.tex:248-249):")
    print("  Stock-Default  6,433 / ~14,000  1.00x  276      upstream, uncounted")
    print("  VISTA-Activity 13,984 / 13,984  0.92x  101/155  explicit, counted")
    print("\nNOTE: '~14,000' is not measured by the stock run — it is inferred "
          "from the\ncameras' nominal delivery rate. The stock run cannot see "
          "its own ring drops.")
    print("NOTE: each row is ONE run. No repeats, no oracle, no coverage or "
          "recall here.")

    print("\ndrop ledger (from stderr.log — sched.csv holds ADMITS ONLY and "
          "has no drop rows):")
    for r in rows:
        led = r["stderr_ledger"]
        if not led or "policy_drops" not in led:
            print(f"  {r['paper_name']:<26s} no [sched]/[vista] summary — "
                  f"stock run: its drops happen upstream, in the transport "
                  f"ring, and nothing counts them")
            continue
        print(f"  {r['paper_name']:<26s} arrivals {led['arrivals']} == "
              f"fresh {led['admitted_fresh']} + salvage "
              f"{led['admitted_salvage']} + drops {led['policy_drops']}  -> "
              f"{'CLOSES' if r['ledger_closes'] else '*** DOES NOT CLOSE ***'}"
              f"   (s_hat {led['s_hat_ms']:.1f} ms)")
        print(f"  {'':<26s} sched.csv events: {r['sched_csv_events']}")

    if args.json:
        with open(derived("live_report.json"), "w") as f:
            json.dump(rows, f, indent=2)
        print("\nwrote", derived("live_report.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
