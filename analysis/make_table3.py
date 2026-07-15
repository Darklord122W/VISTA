#!/usr/bin/env python3
"""make_table3.py — Table III: the other two load points (YOLO11s, YOLO11l).

Columns and statistic are Table II's: coverage (%) | age mean (ms) | age p99
(ms) | det. yield (%) | TTA@0.5s (%), median over 3 repeats.

METRIC: tta_recall (EMISSION time), same as Table II.

Unlike Table II this table needs no cross-campaign composition: every cell
equals the corresponding e3_s_aggregate.json / e3_l_aggregate.json entry, and
those come straight from policy_report.py over e3_s/ and e3_l/. This script
exists so the table is generated rather than transcribed, and so the
provenance caveat below travels with it.

CAVEAT THAT THE PAPER DOES NOT STATE (verified — see campaigns.yaml):
e3_s/imp-k2_* and e3_l/imp-k2_* ran under the PRE-importance-bugfix build
(2026-07-08 19:54 and 20:12, recorded sha 7f98270). The fix was re-run only at
YOLO11m, as e8_impfix_* (21:44, sha e514f5f), which is what Table II uses.
So Table II's VISTA-Activity and Table III's VISTA-Activity are different
importance implementations wearing the same name. At YOLO11m the fix moved
coverage ~7 points; its effect at s/l is unmeasured. This script prints the
warning on every run — it is not decoration.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns  # noqa: E402
from _paths import DATA_ROOT, derived, ensure_figdir, figure  # noqa: E402
from match_events import build_oracle, evaluate, load_run_dets  # noqa: E402
from policy_report import median_range, run_metrics  # noqa: E402

# The paper's printed cells (evaluation.tex:137-144), for --check.
PAPER = {
    ("yolo11s rho=1.00", "Stock-Default"):  (78.6, 497, 609, 62.5, 22.8),
    ("yolo11s rho=1.00", "VISTA-Fresh"):    (69.3, 58, 78, 55.2, 35.8),
    ("yolo11s rho=1.00", "VISTA-Activity"): (68.4, 60, 89, 54.5, 35.8),
    ("yolo11l rho=2.33", "Stock-Default"):  (33.7, 1112, 1280, 24.6, 0.0),
    ("yolo11l rho=2.33", "VISTA-Fresh"):    (28.8, 121, 186, 20.7, 21.1),
    ("yolo11l rho=2.33", "VISTA-Activity"): (28.7, 122, 187, 20.9, 19.5),
}
COLS = ["cov", "age", "p99", "yield", "tta500"]
WARN = ("VISTA-Activity at YOLO11s/YOLO11l is PRE-importance-bugfix code "
        "(sha 7f98270, never re-run after the fix that produced e8_impfix at "
        "YOLO11m). Table II's VISTA-Activity is POST-fix. See campaigns.yaml.")


def _rel(d):
    return os.path.relpath(d, DATA_ROOT)


def derived_index():
    """{run_dir -> scoring row} from recall_s.json / recall_l.json. Their
    `run` strings are trustworthy (unlike recall_m2.json's — Table II)."""
    index = {}
    for fname in ("recall_s.json", "recall_l.json"):
        p = derived(fname)
        if not os.path.exists(p):
            continue
        with open(p) as fh:
            for r in json.load(fh)["results"]:
                index[r["run"].replace("experiments/results/", "")] = r
    return index


def build(tier, delta_ms=500):
    t3 = _campaigns.table("table3")
    oracle_dir, ometa = _campaigns.oracle(t3["oracle"])
    index = {} if tier == "rescore" else derived_index()
    oracle_frames = events = None
    if tier == "rescore":
        oracle_frames, events = build_oracle(
            load_run_dets(oracle_dir), ometa["conf"], 0.30, 3)
        print(f"oracle: {len(oracle_frames)} frames, {len(events)} events",
              file=sys.stderr)

    rows = []
    for row in t3["rows"]:
        dirs = _campaigns.run_dirs(row)
        res = []
        for d in dirs:
            r = index.get(_rel(d))
            if r is None:
                if oracle_frames is None:
                    oracle_frames, events = build_oracle(
                        load_run_dets(oracle_dir), ometa["conf"], 0.30, 3)
                r, _ = evaluate(d, oracle_frames, events, [delta_ms], 0.30)
            res.append(r)
        mets = [run_metrics(d) for d in dirs]
        key = f"@{delta_ms:g}ms"
        rows.append({
            "name": row["paper_name"], "load": row["load_point"],
            "n": len(dirs), "dirs": [_rel(d) for d in dirs],
            "cells": {
                "cov": median_range([r["coverage_vs_oracle"] for r in res]),
                "age": median_range([m["e2e_mean"] for m in mets]),
                "p99": median_range([m["e2e_p99"] for m in mets]),
                "yield": median_range([r["det_yield_overall"] for r in res]),
                "tta500": median_range([r["tta_recall"][key] for r in res]),
            }})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["derived", "rescore"], default="derived")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--tex", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = build(args.tier)
    print(f"\nTable III — other two load points (tier={args.tier}, "
          f"metric=tta_recall@500ms, statistic=median over 3 repeats)\n")
    last = None
    print(f"{'policy':<18s} {'cov%':>5s} {'age ms':>7s} {'p99':>6s} "
          f"{'yield%':>6s} {'TTA%':>6s}")
    for r in rows:
        if r["load"] != last:
            print(f"  -- {r['load']} --")
            last = r["load"]
        c = r["cells"]
        print(f"{r['name']:<18s} {100*c['cov'][0]:5.1f} {c['age'][0]:7.0f} "
              f"{c['p99'][0]:6.0f} {100*c['yield'][0]:6.1f} "
              f"{100*c['tta500'][0]:6.1f}   n={r['n']}")
    print(f"\nWARNING: {WARN}")

    if args.tex:
        ensure_figdir()
        tex = ["% generated by analysis/make_table3.py — do not hand-edit",
               f"% tier={args.tier}; sources resolved via analysis/campaigns.yaml",
               f"% CAVEAT: {WARN}",
               "\\begin{tabular}{@{}lrrrrr@{}}", "\\toprule",
               " & cov. & age & p99 & yield & TTA@0.5\\,s \\\\",
               "policy & (\\%) & (ms) & (ms) & (\\%) & (\\%) \\\\",
               "\\midrule"]
        last = None
        for r in rows:
            if r["load"] != last:
                tex.append("\\multicolumn{6}{@{}l}{\\emph{%s}} \\\\"
                           % r["load"].replace("rho=", "$\\rho{=}$"))
                last = r["load"]
            c = r["cells"]
            tex.append(f"{r['name']} & {100*c['cov'][0]:.1f} & "
                       f"{c['age'][0]:.0f} & {c['p99'][0]:.0f} & "
                       f"{100*c['yield'][0]:.1f} & {100*c['tta500'][0]:.1f} \\\\")
        tex += ["\\bottomrule", "\\end{tabular}"]
        with open(figure("table3.tex"), "w") as f:
            f.write("\n".join(tex) + "\n")
        print("wrote", figure("table3.tex"))

    if args.json:
        with open(derived("table3_regenerated.json"), "w") as f:
            json.dump(rows, f, indent=2)
        print("wrote", derived("table3_regenerated.json"))

    rc = 0
    if args.check:
        print("\ncell-by-cell vs the paper (evaluation.tex:137-144):")
        print(f"{'load':<18s} {'policy':<16s} {'col':>7s} {'paper':>8s} "
              f"{'ours':>8s}  verdict")
        for r in rows:
            want = PAPER[(r["load"], r["name"])]
            c = r["cells"]
            got = (100 * c["cov"][0], c["age"][0], c["p99"][0],
                   100 * c["yield"][0], 100 * c["tta500"][0])
            for col, w, g in zip(COLS, want, got):
                prec = 1 if col in ("cov", "yield", "tta500") else 0
                gs = round(g, prec)
                ok = abs(gs - w) < 10 ** (-prec) / 2
                rc |= 0 if ok else 1
                print(f"{r['load']:<18s} {r['name']:<16s} {col:>7s} "
                      f"{w:8.{prec}f} {gs:8.{prec}f}  "
                      f"{'match' if ok else '*** MISMATCH ***'}")
        print("\nall cells match" if rc == 0 else
              "\nMISMATCHES ABOVE — report them; do not tune this script")
    return rc


if __name__ == "__main__":
    sys.exit(main())
