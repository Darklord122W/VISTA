#!/usr/bin/env python3
"""make_table4.py — Table IV: the skewed-activity microbenchmark.

Columns: hot-cam share (%) | brief @250ms | brief @1s | persistent @250ms |
         persistent @1s.   Statistic: median over 3 repeats.

=============================================================================
METRIC: event_recall — recall within Delta of event ONSET, i.e. FRAME TIME.
        NOT tta_recall. Tables II and III use tta_recall (EMISSION time,
        which charges every match the run's mean output age).
=============================================================================
This is stated here, in the caption of the printed table, in the .tex header,
and in campaigns.yaml, because the two metrics live in the same JSONs under
adjacent keys and the difference is material: on the brief clip set
event_recall@250 medians .723 while tta_recall@250 medians .682. The paper's
caption ("recall within Delta of onset") and the abstract's 0.30 -> 0.71
headline are both the ONSET metric. Reading tta_recall here would silently
lower every cell by several points.

TIER: this table has no `derived` tier. No archived analysis JSON references
briefS2 / persS2 / briefD2ctl at all — those run dirs (2026-07-10 14:39)
postdate the newest analysis JSON in the archive (recall_brief.json,
2026-07-09 23:36). The stash-2 rows were therefore never scored by any
committed script. The raw dets survive, so this script rescores everything
from scratch. Nothing in the archive independently corroborates these cells.

STASH-1 ROW SOURCE: briefD2ctl_* (d=2), NOT brief_* (d=1). See campaigns.yaml
for the evidence; brief_* is the d=1 practitioner-note arm and using it here
would compare across two variables at once.
"""
import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns  # noqa: E402
from _paths import DATA_ROOT, derived, ensure_figdir, figure  # noqa: E402
from match_events import build_oracle, evaluate, load_run_dets  # noqa: E402
from skew_report import hot_share  # noqa: E402

DELTAS = [250, 1000]
CONFIGS = ["VISTA-Fresh, stash 1", "VISTA-Fresh, stash 2",
           "VISTA-Activity, stash 1", "VISTA-Activity, stash 2"]

# The paper's printed cells (evaluation.tex:193-196):
# hot-share, brief@250, brief@1s, pers@250, pers@1s
PAPER = {
    "VISTA-Fresh, stash 1":    ("25", 25, 54, 47, 78),
    "VISTA-Fresh, stash 2":    ("25", 30, 57, 35, 67),
    "VISTA-Activity, stash 1": ("26--29", 39, 68, 48, 77),
    "VISTA-Activity, stash 2": ("48", 71, 80, 77, 86),
}


def score_clip_set(clip_set, metric="event_recall"):
    """{config -> {delta -> median recall}} plus the per-run detail."""
    rows = _campaigns.rows("table4", clip_set=clip_set)
    if not rows:
        return {}, {}
    oracle_name = rows[0]["oracle"]
    oracle_dir, ometa = _campaigns.oracle(oracle_name)
    frames, events = build_oracle(load_run_dets(oracle_dir), ometa["conf"],
                                  0.30, 3)
    print(f"  {clip_set}: oracle {ometa['run_dir']} -> {len(frames)} frames, "
          f"{len(events)} events (declared {ometa['n_events']})",
          file=sys.stderr)
    if len(events) != ometa["n_events"]:
        print(f"  WARNING: event count differs from campaigns.yaml's declared "
              f"{ometa['n_events']}", file=sys.stderr)

    out, detail = {}, {}
    for row in rows:
        per_run = []
        for d in _campaigns.run_dirs(row):
            r, _ = evaluate(d, frames, events, DELTAS, 0.30)
            per_run.append({
                "run": os.path.relpath(d, DATA_ROOT),
                "hot_share": hot_share(d),
                **{f"@{x}": r[metric][f"@{x:g}ms"] for x in DELTAS},
            })
        out[row["paper_name"]] = {
            x: statistics.median(p[f"@{x}"] for p in per_run) for x in DELTAS}
        out[row["paper_name"]]["hot"] = statistics.median(
            p["hot_share"] for p in per_run if p["hot_share"] is not None)
        detail[row["paper_name"]] = per_run
    return out, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["rescore"], default="rescore",
                    help="only rescore exists; see the module docstring")
    ap.add_argument("--metric", choices=["event_recall", "tta_recall"],
                    default="event_recall",
                    help="event_recall (onset) is Table IV's metric; "
                         "tta_recall is offered only for comparison")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--tex", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    print(f"scoring (metric={args.metric}) ...", file=sys.stderr)
    brief, brief_d = score_clip_set("brief", args.metric)
    pers, pers_d = score_clip_set("persistent", args.metric)

    print(f"\nTable IV — skewed-activity microbenchmark "
          f"(YOLO11m, d=2, medians of 3)")
    print(f"METRIC: {args.metric} "
          f"({'recall within Delta of event ONSET — frame time' if args.metric == 'event_recall' else 'EMISSION time — NOT the paper table metric'})")
    print(f"TTA over each clip set's own reference: "
          f"368 brief / 257 persistent events\n")
    print(f"{'config':<24s} {'hot%':>5s} | {'brief@250':>9s} {'brief@1s':>8s} "
          f"| {'pers@250':>8s} {'pers@1s':>7s}")
    for cfg in CONFIGS:
        b, p = brief.get(cfg, {}), pers.get(cfg, {})
        hot = b.get("hot")
        print(f"{cfg:<24s} {100*hot:4.0f}% | {100*b.get(250, float('nan')):8.0f} "
              f"{100*b.get(1000, float('nan')):8.0f} | "
              f"{100*p.get(250, float('nan')):8.0f} "
              f"{100*p.get(1000, float('nan')):7.0f}")

    if args.tex:
        ensure_figdir()
        tex = ["% generated by analysis/make_table4.py — do not hand-edit",
               f"% METRIC: {args.metric} (recall within Delta of event ONSET).",
               "% Tables II/III use tta_recall (emission time) — different metric.",
               "% Sources resolved via analysis/campaigns.yaml; rescored from raw",
               "% dets because no archived JSON scores the stash-2 rows.",
               "\\begin{tabular}{@{}lrrrrr@{}}", "\\toprule",
               " & hot-cam & \\multicolumn{2}{c}{brief events (\\%)} & "
               "\\multicolumn{2}{c}{persistent (\\%)} \\\\",
               " \\cmidrule(lr){3-4} \\cmidrule(lr){5-6}",
               "config. & share (\\%) & @250\\,ms & @1\\,s & @250\\,ms & @1\\,s \\\\",
               "\\midrule"]
        for cfg in CONFIGS:
            b, p = brief.get(cfg, {}), pers.get(cfg, {})
            tex.append(f"{cfg} & {100*b['hot']:.0f} & {100*b[250]:.0f} & "
                       f"{100*b[1000]:.0f} & {100*p[250]:.0f} & "
                       f"{100*p[1000]:.0f} \\\\")
        tex += ["\\bottomrule", "\\end{tabular}"]
        with open(figure("table4.tex"), "w") as f:
            f.write("\n".join(tex) + "\n")
        print("\nwrote", figure("table4.tex"))

    if args.json:
        with open(derived("table4_regenerated.json"), "w") as f:
            json.dump({"metric": args.metric, "brief": brief_d,
                       "persistent": pers_d}, f, indent=2, default=float)
        print("wrote", derived("table4_regenerated.json"))

    rc = 0
    if args.check:
        print("\ncell-by-cell vs the paper (evaluation.tex:193-196):")
        print(f"{'config':<24s} {'column':>10s} {'paper':>7s} {'ours':>6s}  verdict")
        for cfg in CONFIGS:
            b, p = brief.get(cfg, {}), pers.get(cfg, {})
            want = PAPER[cfg]
            got = [100 * b["hot"], 100 * b[250], 100 * b[1000],
                   100 * p[250], 100 * p[1000]]
            names = ["hot-share", "brief@250", "brief@1s", "pers@250", "pers@1s"]
            for name, w, g in zip(names, want, got):
                if name == "hot-share":
                    # The paper prints a range for one row; compare loosely and
                    # let the reader judge.
                    print(f"{cfg:<24s} {name:>10s} {str(w):>7s} {g:6.1f}  "
                          f"(informational)")
                    continue
                ok = abs(round(g) - w) < 0.5
                rc |= 0 if ok else 1
                print(f"{cfg:<24s} {name:>10s} {w:7.0f} {round(g):6.0f}  "
                      f"{'match' if ok else '*** MISMATCH ***'}")
        print("\nall recall cells match" if rc == 0 else
              "\nMISMATCHES ABOVE — report them; do not tune this script")
    return rc


if __name__ == "__main__":
    sys.exit(main())
