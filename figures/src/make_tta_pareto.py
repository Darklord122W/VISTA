#!/usr/bin/env python3
"""Fig. 3 (main result, sections/evaluation.tex:54): the freshness--coverage
frontier and the time-to-awareness curves, at YOLO11m (rho=1.86).

(a) one point per Table II policy, median over repeats, min-max whiskers,
    log-x mean output age; up-left is better; the pale staircase is the
    measured Pareto front.
(b) recall within Delta of event onset, median + min-max band.

Only the five policies that appear in Table II are drawn. The diagnostics
(FIFO-5, DROP-OLD, DEC-1/2) and the all-admit ablation (FRESH-K4) are omitted
deliberately -- they are in the data and in docs/, not in this figure.

Data: $VISTA_DATA_ROOT/derived/recall_m2.json + recall_m_decimate.json, written
by analysis/make_all.py --tier rescore, both scored against
the 123-event YOLO11x offline reference detector.

---------------------------------------------------------------------------
PROVENANCE DEFECT -- read before citing this figure
---------------------------------------------------------------------------
recall_m2.json MISLABELS two of the five arms this figure plots. Verified:

  * its "e3_m/imp-k2_r{0,1,2}" rows are byte-identical to the e8_impfix_r{0,1,2}
    rows in recall_e78.json -- i.e. the VISTA-Activity point is scored from the
    post-importance-bugfix runs, not from e3_m/imp-k2_r*;
  * "e3_m/fifo-s2_r*" does not exist as a directory at all; Stock-LiveDepth was
    run as fifo33 under --replay-surfaces 2 (e7_s2_r*,
    e7_surfcal_2).

The plotted NUMBERS are real measurements from the correct post-bugfix runs and
match Table II. The `run` STRINGS recording where they came from are wrong.
This is a labeling defect, not fabricated data. This script keys off those
strings, so it inherits the defect: it reproduces the paper's figure exactly,
including the wrong provenance. Do not "fix" the mapping here without
rescoring -- that would silently change the figure.

A separate consequence: e3_m_aggregate.json holds STALE pre-bugfix imp-k2 data
(38.8% / 94.6 ms) that contradicts Table II on every cell. It is not used here
and must not be.

TRAP: dec13 == e3_m_decimate3 == --gap-every 3 == Static-Decimation (~64 ms).
      dec12 == e3_m_decimate  == --gap-every 2 == DEC-1/2 (~997 ms, a
      diagnostic, and the stalest config measured). Swapping them moves this
      figure's yellow point ~930 ms with no error raised.
---------------------------------------------------------------------------
"""
import json
import os
import statistics as st
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import MissingData, derived, savefig  # noqa: E402

INK, MUTED, GRID, SPINE = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"

# CANONICAL POLICY PALETTE. This figure defines it; make_latency_cdf.py imports
# these same hexes. Grays = stock baselines, yellow = static decimation,
# blues = VISTA. Keys are the on-disk/internal arm names (results dirs keep the
# old names); values carry the paper-facing name.
STYLE = {
    "fifo33":   ("Stock-Default", "#898781", "-"),
    "fifo-s2":  ("Stock-LiveDepth", "#52514e", "--"),
    "dec13":    ("Static-Decimation", "#eda100", "--"),
    "fresh-k2": ("VISTA-Fresh", "#5598e7", "-"),
    "imp-k2":   ("VISTA-Activity", "#2a78d6", (0, (4, 1.5))),
}
ARMS = ["fifo33", "fifo-s2", "dec13", "fresh-k2", "imp-k2"]

plt.rcParams.update({
    "font.size": 8, "axes.titlesize": 8.5, "legend.fontsize": 7.5,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "axes.labelsize": 8,
    "pdf.fonttype": 42,
})


def load_runs():
    by = {}
    d = json.load(open(derived("recall_m2.json")))
    n_events = d["n_events"]
    for r in d["results"]:
        arm = os.path.basename(r["run"]).rsplit("_r", 1)[0]
        r["n_events"] = n_events
        by.setdefault(arm, []).append(r)
    dd = json.load(open(derived("recall_m_decimate.json")))
    for r in dd["results"]:
        # See the TRAP note above: "decimate3" is the 1/3 arm; anything else in
        # this file is the 1/2 diagnostic.
        arm = "dec13" if "decimate3" in r["run"] else "dec12"
        r["n_events"] = dd["n_events"]
        by.setdefault(arm, []).append(r)

    absent = [a for a in ARMS if not by.get(a)]
    if absent:
        raise MissingData(
            "recall JSONs contain no runs for: " + ", ".join(absent) +
            "\n  Present arms: " + ", ".join(sorted(by)) +
            "\n  A figure missing an arm looks identical to a complete one, so"
            "\n  this is fatal rather than a warning.")
    return by


def recall_curve(res, deltas):
    """Fraction of the 123 events whose detection is EMITTED within Delta.

    Adds mean_e2e_ms to each onset delay: this is tta_recall (emission time),
    the metric Tables II/III use. Table IV instead uses event_recall (onset,
    frame time), which is systematically higher -- on the brief clips, median
    .723 vs .682 at Delta=250 ms. Both live in these same JSONs; do not mix
    them in one comparison.
    """
    delays = [t + res["mean_e2e_ms"] for t in res["onset_delays_ms"]]
    return [sum(1 for t in delays if t <= d) / res["n_events"] for d in deltas]


def style_ax(ax):
    ax.grid(alpha=0.6, color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(SPINE)
    ax.tick_params(colors=MUTED, length=2, width=0.6)


def main():
    by = load_runs()
    fig, (axa, axb) = plt.subplots(
        2, 1, figsize=(3.5, 4.6), gridspec_kw={"height_ratios": [1, 1.15]})

    # ---------------- (a) freshness-coverage frontier ----------------------
    pts = {}
    for arm in ARMS:
        runs = by[arm]
        label, color, _ = STYLE[arm]
        e2es = [r["mean_e2e_ms"] for r in runs]
        covs = [100 * r["coverage_vs_oracle"] for r in runs]
        pts[arm] = (st.median(e2es), st.median(covs),
                    (min(e2es), max(e2es)), (min(covs), max(covs)), color, label)

    front = []
    for arm, (x, y, *_) in sorted(pts.items(), key=lambda kv: kv[1][0]):
        if not front or y > front[-1][1]:
            front.append((x, y))
    axa.step([p[0] for p in front], [p[1] for p in front], where="post",
             color=GRID, lw=1.4, zorder=1)
    for arm, (x, y, xr, yr, color, label) in pts.items():
        axa.plot([xr[0], xr[1]], [y, y], color=color, lw=0.8, zorder=2)
        axa.plot([x, x], [yr[0], yr[1]], color=color, lw=0.8, zorder=2)
        axa.plot(x, y, "o", ms=4.5, color=color, mec="white", mew=0.5, zorder=3)

    # Direct labels, hand-nudged: (x-multiplier, y-offset, horizontal align).
    # x is a MULTIPLIER because the axis is log. Re-check these if the data
    # moves -- they are placed against the current point positions, and nothing
    # detects a collision for you.
    NUDGE = {"fifo33": (0.97, 2.2, "right"),
             "fifo-s2": (0.90, 1.6, "right"),
             "dec13": (1.03, -2.6, "left"),
             "fresh-k2": (0.90, 1.9, "right"), "imp-k2": (0.88, 2.5, "right")}
    for arm, (x, y, *_rest) in pts.items():
        mx, dy, ha = NUDGE.get(arm, (1.0, 1.5, "center"))
        axa.text(x * mx, y + dy, STYLE[arm][0],
                 fontsize=5.8, color=INK, ha=ha, va="center", zorder=4,
                 linespacing=1.1)
    axa.set_xscale("log")
    axa.set_xticks([64, 125, 250, 500, 1000])
    axa.set_xticklabels(["64", "125", "250", "500", "1000"])
    axa.set_xlim(45, 1500)
    axa.set_ylim(24, 52)
    axa.set_xlabel("mean output age, e2e (ms, log)")
    axa.set_ylabel("coverage (%)")
    axa.set_title("(a) freshness--coverage frontier", pad=3)
    axa.annotate("better", xy=(58, 49), xytext=(120, 44.5), fontsize=6,
                 color=MUTED, ha="center",
                 arrowprops=dict(arrowstyle="->", color=MUTED, lw=0.7))
    style_ax(axa)

    # ---------------- (b) TTA curves ---------------------------------------
    deltas = list(range(50, 2501, 50))
    for arm in ARMS:
        runs = by[arm]
        label, color, ls = STYLE[arm]
        curves = [recall_curve(r, deltas) for r in runs]
        med = [st.median(c[i] for c in curves) for i in range(len(deltas))]
        lo = [min(c[i] for c in curves) for i in range(len(deltas))]
        hi = [max(c[i] for c in curves) for i in range(len(deltas))]
        axb.fill_between(deltas, lo, hi, color=color, alpha=0.12, lw=0)
        axb.plot(deltas, med, color=color, ls=ls, lw=1.3, label=label)
    axb.axvline(500, color=MUTED, lw=0.8, ls=":")
    axb.text(490, 0.47, "$\\Delta$=500 ms", fontsize=6, color=MUTED,
             ha="right", va="top")   # top of the line, clear of the legend
    axb.set_xlim(0, 2500)
    axb.set_ylim(0, 0.5)
    axb.set_xlabel("awareness deadline $\\Delta$ after event onset (ms)")
    axb.set_ylabel("events recalled within $\\Delta$")
    axb.set_title("(b) time-to-awareness", pad=3)
    axb.legend(frameon=False, ncol=2, loc="lower right", fontsize=6.2,
               handlelength=1.6, labelspacing=0.3, columnspacing=0.9,
               borderpad=0.1)
    style_ax(axb)

    fig.tight_layout(h_pad=1.4)
    savefig(fig, "fig_tta_pareto")
    for arm, (x, y, *_x) in sorted(pts.items(), key=lambda kv: kv[1][0]):
        print(f"{STYLE[arm][0]:18s} e2e {x:6.0f} ms  cov {y:4.1f}%  "
              f"({len(by[arm])} repeats)")


if __name__ == "__main__":
    try:
        main()
    except MissingData as e:
        sys.exit(f"make_tta_pareto: {e}")
