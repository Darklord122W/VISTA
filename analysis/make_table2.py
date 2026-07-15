#!/usr/bin/env python3
"""make_table2.py — Table II: main policy comparison, YOLO11m (rho=1.86).

Columns: coverage (%) | age mean (ms) | age p99 (ms) | det. yield (%) |
         TTA@0.5s (%).   Statistic: median over repeats.

METRIC: tta_recall (EMISSION time) — the operator learns of an event when the
detection is emitted, mean-age after capture. Table IV uses event_recall
(onset time) instead. Do not mix them.

WHY THIS SCRIPT EXISTS: no generator produced Table II. Its ancestor,
figures/e3_table_m.tex, is a hand-assembled composite whose own header says
"reordered" — policy_report.py can only see ONE campaign dir (e3_m) and Table
II draws on four:

  Stock-Default     e3_m/fifo33_r{0..4}                    (n=5)
  Stock-LiveDepth   e7_surfcal_2, e7_s2_r1, e7_s2_r2       (n=3)
  Static-Decimation e3_m_decimate3_r{0,1,2}                (n=3)
  VISTA-Fresh       e3_m/fresh-k2_r{0..4}                  (n=5)
  VISTA-Activity    e8_impfix_r{0,1,2}                     (n=3)

Two of those are NOT what the archived recall_m2.json says they are; see the
Table II notes in campaigns.yaml for the resolution and the evidence. The
numbers in the paper are correct — the labels in the scoring JSON are not.

Two tiers:
  --tier derived  (default) read the archived recall_m2.json, matching rows by
                  the resolved run dirs, and take age/p99 from metrics.csv.
  --tier rescore  re-run match_events.py against oracle_x from the raw dets.
                  Slower (~1 min), independent of the archived JSON.

--check compares every cell against the paper's printed values and exits
nonzero on a mismatch. A mismatch is a finding to report, not a reason to
tune this script.
"""
import argparse
import csv
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns  # noqa: E402
from _paths import DATA_ROOT, derived, ensure_figdir, figure  # noqa: E402
from match_events import build_oracle, evaluate, load_run_dets  # noqa: E402
from policy_report import median_range, run_metrics  # noqa: E402

# The paper's printed cells (evaluation.tex:37-41), for --check.
PAPER = {
    "Stock-Default":     (46.2, 857, 1032, 34.2, 0.0),
    "Stock-LiveDepth":   (44.8, 410, 601, 37.6, 18.7),
    "Static-Decimation": (29.9, 64, 123, 21.5, 30.1),
    "VISTA-Fresh":       (38.8, 93, 139, 28.6, 30.1),
    "VISTA-Activity":    (31.7, 115, 200, 23.3, 28.5),
}
COLS = ["cov", "age", "p99", "yield", "tta500"]


def _rel(run_dir):
    return os.path.relpath(run_dir, DATA_ROOT)


def oracle_cols_from_derived(run_dirs, delta_ms):
    """Pull the oracle columns out of the archived scoring JSONs.

    Matching is by the RESOLVED run dir, not by the JSON's `run` string,
    because two of recall_m2.json's `run` strings are wrong (campaigns.yaml).
    We therefore index the JSONs by run dir where the string is trustworthy
    and fall back to a positional alias table for the two known mislabels.
    """
    # (json file, alias map) — alias maps a mislabelled `run` string to the
    # real run dir it actually describes. Each alias below is justified in
    # campaigns.yaml with a byte-level or re-scoring proof.
    sources = [
        ("recall_m2.json", {
            "experiments/results/e3_m/fifo-s2_r0": "e7_surfcal_2/fifo33_r0",
            "experiments/results/e3_m/fifo-s2_r1": "e7_s2_r1/fifo33_r0",
            "experiments/results/e3_m/fifo-s2_r2": "e7_s2_r2/fifo33_r0",
            "experiments/results/e3_m/imp-k2_r0": "e8_impfix_r0/imp-k2_r0",
            "experiments/results/e3_m/imp-k2_r1": "e8_impfix_r1/imp-k2_r0",
            "experiments/results/e3_m/imp-k2_r2": "e8_impfix_r2/imp-k2_r0",
            "experiments/results/e3_m/salv-k2_r0": "e8_salvfix_r0/salv-k2_r0",
            "experiments/results/e3_m/salv-k2_r1": "e8_salvfix_r1/salv-k2_r0",
            "experiments/results/e3_m/salv-k2_r2": "e8_salvfix_r2/salv-k2_r0",
        }),
        ("recall_m_decimate.json", {}),
        ("recall_e78.json", {}),
    ]
    index = {}
    for fname, alias in sources:
        p = derived(fname)
        if not os.path.exists(p):
            continue
        with open(p) as fh:
            for r in json.load(fh)["results"]:
                key = r["run"]
                key = alias.get(key, key)
                key = key.replace("experiments/results/", "")
                index.setdefault(key, r)

    out = []
    for d in run_dirs:
        r = index.get(_rel(d))
        if r is None:
            return None            # caller falls back to rescoring
        out.append(r)
    return out


def oracle_cols_by_rescore(run_dirs, oracle_frames, events, delta_ms):
    res = []
    for d in run_dirs:
        r, _ = evaluate(d, oracle_frames, events, [delta_ms], 0.30)
        res.append(r)
    return res


def build(tier, delta_ms=500):
    t2 = _campaigns.table("table2")
    oracle_dir, ometa = _campaigns.oracle(t2["oracle"])
    oracle_frames = events = None
    if tier == "rescore":
        print(f"rescoring against {ometa['run_dir']} ...", file=sys.stderr)
        oracle_frames, events = build_oracle(
            load_run_dets(oracle_dir), ometa["conf"], 0.30, 3)
        print(f"oracle: {len(oracle_frames)} frames, {len(events)} events",
              file=sys.stderr)

    rows = []
    for row in t2["rows"]:
        dirs = _campaigns.run_dirs(row)
        if tier == "rescore":
            res = oracle_cols_by_rescore(dirs, oracle_frames, events, delta_ms)
        else:
            res = oracle_cols_from_derived(dirs, delta_ms)
            if res is None:
                print(f"note: {row['paper_name']}: no archived scoring rows; "
                      f"rescoring this row", file=sys.stderr)
                if oracle_frames is None:
                    oracle_frames, events = build_oracle(
                        load_run_dets(oracle_dir), ometa["conf"], 0.30, 3)
                res = oracle_cols_by_rescore(dirs, oracle_frames, events,
                                             delta_ms)

        # Age and p99 always come from metrics.csv: no scoring JSON carries
        # p99, and mean_e2e_ms in the JSONs is computed the same way anyway.
        mets = [run_metrics(d) for d in dirs]
        key = f"@{delta_ms:g}ms"
        cells = {
            "cov": median_range([r["coverage_vs_oracle"] for r in res]),
            "age": median_range([m["e2e_mean"] for m in mets]),
            "p99": median_range([m["e2e_p99"] for m in mets]),
            "yield": median_range([r["det_yield_overall"] for r in res]),
            "tta500": median_range([r["tta_recall"][key] for r in res]),
        }
        rows.append({"name": row["paper_name"], "n": len(dirs),
                     "dirs": [_rel(d) for d in dirs], "cells": cells})
    return rows


def fmt_row(r):
    c = r["cells"]
    return (f"{r['name']:<18s} {100*c['cov'][0]:5.1f} {c['age'][0]:7.0f} "
            f"{c['p99'][0]:6.0f} {100*c['yield'][0]:6.1f} "
            f"{100*c['tta500'][0]:6.1f}   n={r['n']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["derived", "rescore"], default="derived")
    ap.add_argument("--check", action="store_true",
                    help="compare every cell to the paper's printed values")
    ap.add_argument("--tex", action="store_true", help="also write table2.tex")
    ap.add_argument("--json", action="store_true", help="also write table2.json")
    args = ap.parse_args()

    rows = build(args.tier)
    print(f"\nTable II — YOLO11m, rho=1.86 (tier={args.tier}, "
          f"metric=tta_recall@500ms, statistic=median over repeats)\n")
    print(f"{'policy':<18s} {'cov%':>5s} {'age ms':>7s} {'p99':>6s} "
          f"{'yield%':>6s} {'TTA%':>6s}")
    for r in rows:
        print(fmt_row(r))

    if args.tex:
        ensure_figdir()
        tex = ["% generated by analysis/make_table2.py — do not hand-edit",
               f"% tier={args.tier}; sources resolved via analysis/campaigns.yaml",
               "\\begin{tabular}{@{}lrrrrr@{}}", "\\toprule",
               " & cov. & age & p99 & yield & TTA@0.5\\,s \\\\",
               "policy & (\\%) & (ms) & (ms) & (\\%) & (\\%) \\\\",
               "\\midrule"]
        for r in rows:
            c = r["cells"]
            tex.append(f"{r['name']} & {100*c['cov'][0]:.1f} & "
                       f"{c['age'][0]:.0f} & {c['p99'][0]:.0f} & "
                       f"{100*c['yield'][0]:.1f} & {100*c['tta500'][0]:.1f} \\\\")
        tex += ["\\bottomrule", "\\end{tabular}"]
        with open(figure("table2.tex"), "w") as f:
            f.write("\n".join(tex) + "\n")
        print("\nwrote", figure("table2.tex"))

    if args.json:
        with open(derived("table2_regenerated.json"), "w") as f:
            json.dump(rows, f, indent=2)
        print("wrote", derived("table2_regenerated.json"))

    rc = 0
    if args.check:
        print("\ncell-by-cell vs the paper (evaluation.tex:37-41):")
        print(f"{'policy':<18s} {'column':>7s} {'paper':>8s} {'ours':>8s}  verdict")
        for r in rows:
            want = PAPER[r["name"]]
            got = (100 * r["cells"]["cov"][0], r["cells"]["age"][0],
                   r["cells"]["p99"][0], 100 * r["cells"]["yield"][0],
                   100 * r["cells"]["tta500"][0])
            for col, w, g in zip(COLS, want, got):
                prec = 1 if col in ("cov", "yield", "tta500") else 0
                gs = round(g, prec)
                ok = abs(gs - w) < 10 ** (-prec) / 2
                rc |= 0 if ok else 1
                print(f"{r['name']:<18s} {col:>7s} {w:8.{prec}f} "
                      f"{gs:8.{prec}f}  {'match' if ok else '*** MISMATCH ***'}")
        print("\nall cells match" if rc == 0 else
              "\nMISMATCHES ABOVE — report them; do not tune this script")
    return rc


if __name__ == "__main__":
    sys.exit(main())
