#!/usr/bin/env python3
"""Per-camera service concentration on the skewed brief-event microbenchmark.

One bar group per camera, one series per configuration. The rule in one
picture: importance cannot act through a 1-frame stash; a 2-frame stash frees
it. All three arms run at the default release depth d=2, so the stash depth is
the only variable.

Counts scheduler admissions per camera straight from each run's sched.csv
(median over repeats). This figure needs no scored recall JSON -- it reads the
ledger the scheduler itself wrote -- which matters, because the stash-2
campaigns (briefS2_*, briefD2ctl_*) have no committed scoring output: every
analysis JSON predates them.

NOT CURRENTLY IN THE PAPER: no \\includegraphics references it in VISTA-Rev2.
It is the supporting evidence for the Table IV stash-depth discussion.

Data: $VISTA_DATA_ROOT/{briefS2_fresh-k2,briefD2ctl_imp-k2,briefS2_imp-k2}/
(not shipped; produce with harness/run_skew_study.sh).

---------------------------------------------------------------------------
FAIL-LOUD, and why
---------------------------------------------------------------------------
The upstream version hard-coded RES2 = "/home/vista/.../paper_draft/
experiments/results" and, when a glob matched nothing, printed "skip (no data)"
and CONTINUED. Off the author's machine every glob missed, so it exited 0 and
wrote a blank plot -- an empty figure and a complete figure are both "success"
to a build script, and a figure silently missing one of its three bars looks
exactly like one that has all three. Paths are now repo-relative, and a missing
arm raises MissingData and exits non-zero.
---------------------------------------------------------------------------
"""
import csv
import os
import statistics
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import RESULTS, MissingData, require_globs, savefig  # noqa: E402

INK, MUTED, GRID, SPINE = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"

# Palette, reconciled against make_tta_pareto.py's canonical STYLE:
#   #5598e7 = VISTA-Fresh, #2a78d6 = VISTA-Activity -- the same meanings they
# carry in Fig. 3. The upstream version gave #2a78d6 to the FRESH arm and
# #eda100 to an IMP arm, i.e. the strong blue named VISTA-Fresh here and
# VISTA-Activity in Fig. 3, and the yellow reserved for Static-Decimation
# named an importance arm. Aqua marks the stash-1 CONTROL, a configuration
# variant that appears in no other figure and so needs no canonical hue;
# it is deliberately outside the blue family to keep the two Activity bars
# distinguishable in a 3-bar group.
VISTA_FRESH, AQUA, VISTA_ACTIVITY = "#5598e7", "#1baf7a", "#2a78d6"

plt.rcParams.update({
    "font.size": 8, "axes.titlesize": 8.5, "legend.fontsize": 7,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "axes.labelsize": 8,
    "pdf.fonttype": 42,
})

ARMS = [  # (label, glob, color)
    ("VISTA-Fresh (no importance)",
     os.path.join(RESULTS, "briefS2_fresh-k2", "fresh-k2_r*"), VISTA_FRESH),
    ("VISTA-Activity, stash 1: capped",
     os.path.join(RESULTS, "briefD2ctl_imp-k2", "imp-k2_r*"), AQUA),
    ("VISTA-Activity, stash 2: concentrates",
     os.path.join(RESULTS, "briefS2_imp-k2", "imp-k2_r*"), VISTA_ACTIVITY),
]
CAMS = ["cam0\nbusy", "cam1\nempty", "cam2\nempty", "cam3\nrare event"]


def admits(run_dir):
    """Admissions per camera from one run's scheduler ledger.

    Both admit reasons count as service: a salvaged frame was still inferred.
    """
    counts = {c: 0 for c in range(4)}
    with open(os.path.join(run_dir, "sched.csv")) as fh:
        for row in csv.DictReader(fh):
            if row["event"] in ("admit", "admit-salvage"):
                counts[int(row["cam"])] += 1
    return counts


def main():
    resolved = require_globs({g: label for label, g, _ in ARMS})

    series = []
    for label, glob_pat, color in ARMS:
        dirs = resolved[glob_pat]
        med = [statistics.median([admits(d)[c] for d in dirs]) for c in range(4)]
        series.append((label, med, color))
        print(f"{label:38s} -> {[int(v) for v in med]} ({len(dirs)} runs)")

    n = len(series)
    fig, ax = plt.subplots(figsize=(3.9, 2.1))
    w = 0.8 / n
    for j, (label, med, color) in enumerate(series):
        xs = [i - 0.4 + w / 2 + j * w for i in range(4)]
        ax.bar(xs, med, w * 0.92, color=color, edgecolor="white", lw=0.6,
               label=label)
        for x, v in zip(xs, med):
            # Values are labeled in INK, never in the series color: aqua and
            # the light blue have too little contrast against white to be read
            # as text.
            ax.text(x, v + 18, f"{v:.0f}", ha="center", fontsize=6.2, color=INK)
    ax.set_xticks(range(4))
    ax.set_xticklabels(CAMS, fontsize=7)
    ax.set_ylabel("frames processed (42 s run)")
    ax.set_title("all configurations at the default release depth $d{=}2$",
                 fontsize=7.5, color=MUTED, pad=3)
    ax.set_ylim(0, 1280)
    ax.grid(alpha=0.6, color=GRID, lw=0.5, axis="y")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(SPINE)
    ax.tick_params(colors=MUTED, length=2, width=0.6)
    ax.legend(frameon=False, loc="upper right", handlelength=1.1,
              handleheight=0.85, labelspacing=0.3, borderpad=0.1)

    fig.tight_layout()
    savefig(fig, "fig_imp_concentration")


if __name__ == "__main__":
    try:
        main()
    except MissingData as e:
        sys.exit(f"make_imp_concentration: {e}")
