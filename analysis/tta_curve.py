#!/usr/bin/env python3
"""tta_curve.py — time-to-awareness recall curves (the money figure).

For each arm (model m): recall(D) = fraction of the 123 oracle events for
which the policy emitted a matching detection within D of event onset, where
emission time = frame onset delay + the run's MEAN e2e. Median across
repeats; band = min/max across repeats.

ON THE MEAN-e2e APPROXIMATION (corrected here — the previous docstring said
"validated against exact per-detection emission stamps: agreement within
+/-0.01", and the archive does not support that):

enriched_analysis.py scores the instrumented runs that carry true per-
detection t_emit stamps, and its own output (enriched_analysis.json, under
the derived dir) gives the measured agreement:

    enriched_m_imp    worst |exact-approx| = 0.008   (within +/-0.01)
    enriched_m_salv   worst |exact-approx| = 0.033   at D=100 ms
    enriched_m_fifo   worst |exact-approx| = 0.041   at D=1000 ms

So the approximation holds to +/-0.01 for the VISTA arms and NOT for the
stock arm. That is the expected shape of the error, not a surprise: charging
every match the run's MEAN age is accurate exactly when the age distribution
is tight (VISTA: p99/mean ~ 1.4) and inaccurate when it is wide (a deep
standing queue). The error is largest for the baseline whose age spread is
largest.

One consequence worth stating plainly: at D=500 ms the stock arm scores
exact 0.016 vs approx 0.000. Table II's "Stock-Default TTA@0.5s = 0.0" and
the prose claim that stock "recalls zero events within 500 ms" are artifacts
of the approximation at the 2-event level; with exact stamps the number is
small but not zero.
"""
import argparse
import json
import os
import sys
import statistics as st

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import derived, ensure_figdir, figure  # noqa: E402

ARMS = [("fifo33", "FIFO-33 (deep pool)", "#898781", "-"),
        ("fifo-s2", "FIFO-33 (live-depth pool)", "#52514e", "--"),
        ("dropold", "DROP-OLD (config)", "#c3c2b7", "--"),
        ("fresh-k4", "FRESH-K4", "#86b6ef", "-"),
        ("fresh-k2", "FRESH-K2", "#5598e7", "-"),
        ("imp-k2", "IMP-K2", "#2a78d6", "-"),
        ("salv-k2", "SALV-K2", "#104281", "-")]
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"

plt.rcParams.update({"font.size": 8, "axes.titlesize": 8.5,
                     "legend.fontsize": 7, "pdf.fonttype": 42})


def recall_curve(res, deltas):
    """res: one run's result dict -> recall at each delta (TTA)."""
    delays = [d + res["mean_e2e_ms"] for d in res["onset_delays_ms"]]
    n = res["n_events"] if "n_events" in res else 123
    return [sum(1 for t in delays if t <= d) / n for d in deltas]


def main(argv=None):
    # argparse claims -h/--help. Without it, `tta_curve.py --help` read
    # "--help" as a model tag; the scoring-JSON check below happened to abort
    # before savefig, but the argv-as-tag pattern is the same one that made
    # policy_report.py write e3_--help_aggregate.json into the archive.
    ap = argparse.ArgumentParser(
        prog="tta_curve.py",
        description=__doc__.split("\n\n")[0],
        epilog=("MODEL selects the scoring JSON recall_<MODEL>2.json, else "
                "recall_<MODEL>.json, under the derived dir "
                "($VISTA_DERIVED_DIR, else $VISTA_DATA_ROOT/derived). No "
                "scoring JSON ships: build one with make_all.py over run data "
                "you produced (see harness/README.md). The draft's models are "
                "m (via recall_m2.json), s and l. Writes "
                "<FIG_DIR>/fig_tta_<MODEL>.pdf. A MODEL with no scoring JSON "
                "is an error: nothing is written."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "model", nargs="?", default="m", metavar="MODEL",
        help="model tag selecting the scoring JSON (default: %(default)s)")
    model = ap.parse_args(argv).model
    # recall_m2.json supersedes recall_m.json (in the original archive,
    # recall_m.json's imp/salv rows are pre-importance-bugfix; see the
    # `superseded:` block in campaigns.yaml).
    p2 = derived(f"recall_{model}2.json")
    p1 = derived(f"recall_{model}.json")
    path = p2 if os.path.exists(p2) else p1
    if not os.path.exists(path):
        raise SystemExit(f"no scoring JSON for model {model!r}: looked for "
                         f"{p2} and {p1}")
    with open(path) as fh:
        data = json.load(fh)
    n_events = data["n_events"]
    by = {}
    for r in data["results"]:
        arm = os.path.basename(r["run"]).rsplit("_r", 1)[0]
        r["n_events"] = n_events
        by.setdefault(arm, []).append(r)

    deltas = list(range(50, 2501, 50))
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for arm, label, color, ls in ARMS:
        runs = by.get(arm)
        if not runs:
            continue
        curves = [recall_curve(r, deltas) for r in runs]
        med = [st.median(c[i] for c in curves) for i in range(len(deltas))]
        lo = [min(c[i] for c in curves) for i in range(len(deltas))]
        hi = [max(c[i] for c in curves) for i in range(len(deltas))]
        ax.plot(deltas, med, ls, color=color, lw=1.6, label=label)
        ax.fill_between(deltas, lo, hi, color=color, alpha=0.15, lw=0)
    ax.set_xlabel("awareness deadline $\\Delta$ after event onset (ms)")
    ax.set_ylabel(f"events recalled within $\\Delta$ (of {n_events})")
    ax.set_xlim(0, 2500)
    ax.set_ylim(0, 0.5)
    ax.grid(alpha=0.6, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)
    for s_ in ("left", "bottom"):
        ax.spines[s_].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED)
    ax.legend(frameon=False, loc="lower right", handlelength=1.6)
    fig.tight_layout()
    ensure_figdir()
    out = figure(f"fig_tta_{model}.pdf")
    fig.savefig(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
