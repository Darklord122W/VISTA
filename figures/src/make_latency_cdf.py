#!/usr/bin/env python3
"""End-to-end latency CDF: stock plateaus at its pool depth, VISTA cliffs early.

One sample per released batch (metrics.csv e2e_ms), first 5 s of warmup
discarded, repeats pooled -- the same filtering policy_report.py applies for
Table II.

NOT CURRENTLY IN THE PAPER. sections/evaluation.tex:71 has the \\includegraphics
commented out. It is shipped here because it is the only view of the full
latency distribution (Tables II/III report means only), but read the two notes
below before promoting it back into the paper.

---------------------------------------------------------------------------
NOTE 1 -- arm selection: VISTA-Activity comes from e8_impfix, not e3_m
---------------------------------------------------------------------------
The upstream version of this script pooled `e3_m/imp-k2_r*` for its IMP-K2 arm.
Those directories hold PRE-importance-bugfix data. Measured here:

    e3_m/imp-k2_r0..r4        mean e2e 95.3 / 97.8 / 92.2 / 91.5 / 94.6 ms
    e8_impfix_r0..r2/imp-k2_r0  mean e2e 109.1 / 115.5 / 120.6 ms

The e8_impfix triple is what Table II and Fig. 3 report (median 115.5 ms ->
Table II's "115"); the e3_m/imp-k2 median 94.6 ms is the stale number preserved
in the quarantined e3_m_aggregate.json, which contradicts Table II on every
cell. Keeping the upstream arm would have made this figure disagree with the
paper's own headline table by ~21 ms with nothing to flag it, so the arm is
repointed here. This CHANGES the figure from the shipped fig_latency_cdf.png
(md5 b6790c7288374b3c42d0d2f1344ecc9a) -- that PNG plots the stale arm.

VISTA-Fresh legitimately stays on e3_m/fresh-k2_r*: Fig. 3 reports 93 ms for it
and this pool's median is 93.3 ms. Only the importance path was re-run.

---------------------------------------------------------------------------
NOTE 2 -- tau_max is NOT a bound on e2e; do not read one off this axis
---------------------------------------------------------------------------
tau_max gates STASH AGE. vista_scheduler.cpp evicts a frame whose time since
arrival exceeds tau_max *before it is ever released* (the "evict-stale" ledger
reason). e2e_ms measures capture -> output and, per vista_scheduler.hpp,
"includes the stash wait" AND the inference itself (~62 ms at YOLO11m,
rho=1.86) AND downstream. So e2e >= stash age by construction, and a batch with
e2e > 150 ms is NOT a tau_max violation.

The upstream version drew a tau_max line on this axis and annotated
"~99% within tau_max" against both VISTA arms. That reads as a bound-
satisfaction check, which this axis cannot support -- and on the correct
post-bugfix data the number is not 99% anyway (measured: VISTA-Fresh 98.9%,
VISTA-Activity 87.8%, p99 215.6 ms). The line is kept as a scale reference and
relabelled as the stash-age budget; the crossing percentages are stated as
measured descriptive values, not as a bound being met.
"""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import RESULTS, MissingData, require, require_globs, savefig  # noqa: E402

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e1e0d9"

# Palette is the CANONICAL one defined in make_tta_pareto.py's STYLE. It must
# match: before reconciliation this figure used #52514e for Stock-Default and
# #2a78d6 for VISTA-Fresh, while Fig. 3 uses those same two hexes for
# Stock-LiveDepth and VISTA-Activity respectively -- so #2a78d6 denoted two
# different policies across two figures in one paper. Fig. 3 wins (it is the
# figure that ships). Yellow #eda100, freed here, now means only
# Static-Decimation, which this figure does not plot.
STOCK_DEFAULT = "#898781"
STOCK_LIVEDEPTH = "#52514e"
VISTA_FRESH = "#5598e7"
VISTA_ACTIVITY = "#2a78d6"

# Stock-LiveDepth == fifo33 run under --replay-surfaces 2; it has no fifo-s2
# directory of its own. These three campaigns are that configuration.
LIVEDEPTH_DIRS = [os.path.join(RESULTS, "e7_s2_r1", "fifo33_r0"),
                  os.path.join(RESULTS, "e7_s2_r2", "fifo33_r0"),
                  os.path.join(RESULTS, "e7_surfcal_2", "fifo33_r0")]

TAU_MAX = 150.0

plt.rcParams.update({
    "font.size": 8, "axes.labelsize": 8,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "pdf.fonttype": 42, "mathtext.default": "regular",
})


def e2e_samples(rundir, warmup=5.0):
    """Post-warmup, non-sentinel e2e samples from one run.

    e2e_ms carries negative sentinels for batches with no valid timestamp;
    filtering >= 0 matches policy_report.py.
    """
    path = require(os.path.join(rundir, "metrics.csv"), f"metrics for {rundir}")
    rows = list(csv.DictReader(open(path)))
    t0 = float(rows[0]["t_mono"])
    return [float(r["e2e_ms"]) for r in rows
            if float(r["t_mono"]) >= t0 + warmup and float(r["e2e_ms"]) >= 0]


def resolve_arms():
    globbed = require_globs({
        os.path.join(RESULTS, "e3_m", "fifo33_r*"): "Stock-Default",
        os.path.join(RESULTS, "e3_m", "fresh-k2_r*"): "VISTA-Fresh",
        os.path.join(RESULTS, "e8_impfix_r*", "imp-k2_r0"): "VISTA-Activity",
    })
    for d in LIVEDEPTH_DIRS:
        require(d, "Stock-LiveDepth (fifo33 under --replay-surfaces 2)")
    return [
        ("Stock-Default", globbed[os.path.join(RESULTS, "e3_m", "fifo33_r*")],
         STOCK_DEFAULT, 1.4),
        ("Stock-LiveDepth", LIVEDEPTH_DIRS, STOCK_LIVEDEPTH, 1.4),
        ("VISTA-Fresh", globbed[os.path.join(RESULTS, "e3_m", "fresh-k2_r*")],
         VISTA_FRESH, 1.4),
        ("VISTA-Activity",
         globbed[os.path.join(RESULTS, "e8_impfix_r*", "imp-k2_r0")],
         VISTA_ACTIVITY, 1.4),
    ]


def main():
    arms = resolve_arms()
    fig, ax = plt.subplots(figsize=(3.5, 2.25))

    curves = {}
    for label, dirs, color, lw in arms:
        pooled = sorted(s for d in dirs for s in e2e_samples(d))
        n = len(pooled)
        ys = [(i + 1) / n for i in range(n)]
        ax.plot(pooled, ys, color=color, lw=lw, solid_capstyle="round", zorder=3)
        within = sum(1 for v in pooled if v <= TAU_MAX) / n
        curves[label] = (pooled, ys, color, len(dirs), n, within)

    # tau_max reference. Labelled as the stash-age budget, not as an e2e bound
    # -- see NOTE 2 in the docstring.
    ax.axvline(TAU_MAX, color=MUTED, lw=0.7, ls=(0, (2, 2)), zorder=2)
    ax.text(TAU_MAX * 1.05, 0.42, r"$\tau_{max}$ = 150 ms",
            color=MUTED, fontsize=7, ha="left", va="center")
    # The qualifier is the guard against reading a bound off this axis. It has
    # to stay short: the free channel here is only ~0.3 decades wide before the
    # Stock-LiveDepth curve. Full explanation is in NOTE 2 and the caption.
    ax.text(TAU_MAX * 1.05, 0.365, "(stash age)",
            color=MUTED, fontsize=5.8, ha="left", va="center")

    # Dot each curve where it crosses the tau_max line and label the measured
    # fraction in the curve's own color. Descriptive values, not a bound check.
    for label in ("Stock-LiveDepth", "VISTA-Fresh", "VISTA-Activity"):
        _, _, color, _, _, within = curves[label]
        ax.plot(TAU_MAX, within, "o", ms=4.5, color=color,
                mec="white", mew=0.7, zorder=5)
    # The two VISTA curves plateau at ~1.0 immediately past the line, so their
    # percentages cannot sit beside their own dots; they go in the clear
    # channel to the right, matched to the dots by color.
    for label, x, y in (("VISTA-Fresh", 250, 0.95), ("VISTA-Activity", 250, 0.87),
                        ("Stock-LiveDepth", 166, 0.075)):
        _, _, color, _, _, within = curves[label]
        ax.text(x, y, f"{100*within:.0f}%", color=color, fontsize=6.4,
                ha="left", va="center")

    # Direct labels in whitespace, leader-free. x is in DATA units on a LOG
    # axis, so a label's width in data units depends on where it starts --
    # re-render and LOOK after any change to xlim, font size, or label text.
    # The VISTA/Stock names are 5-8 chars longer than the FRESH-K2/IMP-K2/
    # FIFO-33 they replaced, which is why none of the upstream x-positions
    # survived: at 7.2 pt a 14-char label spans ~0.30 decades, and the free
    # channel between the VISTA plateau and the Stock-LiveDepth curve is only
    # ~0.35 decades wide.
    ax.text(86, 0.80, "VISTA-Fresh", color=VISTA_FRESH, fontsize=7.2,
            ha="right", va="center")
    ax.text(128, 0.62, "VISTA-Activity", color=VISTA_ACTIVITY, fontsize=7.2,
            ha="left", va="center")
    ax.text(420, 0.76, "Stock-LiveDepth", color=STOCK_LIVEDEPTH, fontsize=7.2,
            ha="right", va="center")
    ax.text(790, 0.28, "Stock-Default", color=STOCK_DEFAULT, fontsize=7.2,
            ha="right", va="center")

    ax.set_xscale("log")
    ax.set_xlim(45, 1250)
    ax.set_ylim(0, 1.02)
    ax.set_xticks([50, 100, 200, 500, 1000])
    ax.get_xaxis().set_major_formatter(mticker.ScalarFormatter())
    ax.minorticks_off()
    ax.set_xlabel("end-to-end latency (ms, log scale)")
    ax.set_ylabel("fraction of batches within $x$")
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])

    ax.grid(alpha=0.6, color=GRID, lw=0.6, axis="y")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED)

    fig.tight_layout(pad=0.3)
    savefig(fig, "fig_latency_cdf", bbox_inches=None, pad_inches=None)

    for label, (pooled, ys, color, nruns, n, within) in curves.items():
        q = lambda p: pooled[int(p * (n - 1))]  # noqa: E731
        print(f"{label:16s} runs={nruns} n={n:5d} p50={q(.5):6.1f} "
              f"p99={q(.99):6.1f} max={pooled[-1]:6.1f}  "
              f"<={TAU_MAX:.0f}ms: {100*within:.1f}%")


if __name__ == "__main__":
    try:
        main()
    except MissingData as e:
        sys.exit(f"make_latency_cdf: {e}")
