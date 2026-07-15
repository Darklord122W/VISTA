#!/usr/bin/env python3
"""Fig. 1 (motivation, sections/introduction.tex:31): where paced frames go under
overload, and how stale the survivors are.

(a) share of the paced input that reached the detector vs. died in the capture
    ring, per YOLO11 size; (b) mean output latency for those survivors.
The point of the figure is the gap between (a)'s yellow and the pipeline's own
report: DeepStream reports 100% coverage in every cell, because frames that die
in the bounded capture ring are never counted as having existed.

Data: $VISTA_DATA_ROOT/e1_yolo11{n,s,m,l}/ -- the push-deadline sweep (not
shipped; produce with harness/run_capacity.sh). Arrivals come
from push_33.3ms.csv's last cumulative counter; latency from summary.csv.

Colors: categorical slots 1/3 of the validated palette (blue #2a78d6 /
yellow #eda100); hatch on "died" = secondary encoding for print/grayscale.

===========================================================================
 TUNING GUIDE -- read this before changing sizes
===========================================================================
The paper includes this file as \\includegraphics[width=0.86\\columnwidth]{...}.
The FINAL WIDTH ON THE PAGE IS THEREFORE FIXED, whatever `figsize` says:

  * figsize WIDTH  -> does NOT change printed width. It only sets the scale
    factor applied to everything. A bigger width => the whole figure is shrunk
    more to fit the column => fonts/lines look SMALLER on the page.
  * figsize HEIGHT -> sets the ASPECT RATIO, i.e. how TALL the figure prints
    at the fixed column width.
  * At an IEEE column (~3.42 in) this 4.5 in-wide figure is scaled x0.76, so
    fonts look ~24% smaller in the paper than in a viewer at 100% zoom.

Collision order when you enlarge fonts (fix in this order):
  1. x-tick labels (the two-line "11n / 0.84" ticks) merge first.
  2. in-bar number labels ("100"/"99") touch at the top of adjacent bars.
  3. titles / top annotations clip against the top edge -> raise set_ylim top.

Many font sizes are HARD-CODED per element (fontsize=...) and OVERRIDE the
rcParams defaults, so bumping rcParams alone will not move them.

PROVENANCE (verified 2026-07-15): this geometry -- figsize height 2.05 and
w_pad 0.1 -- reproduces the shipped fig_frame_funnel.png BYTE-FOR-BYTE
(md5 edf38bafce59fdcf29af9e91b1871c87) under matplotlib 3.5.1. An older v6
variant of this script (height 2.35, w_pad 1.2, legend upper-center, panel (b)
y-axis on the left) is still present in VISTA-Rev2/figures/src/ and does NOT
reproduce the shipped figure. Do not "restore" those values.
===========================================================================
"""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import RESULTS, MissingData, require, savefig  # noqa: E402

MODELS = ["n", "s", "m", "l"]
# Load ratio rho = (mean inference time) / (frame period), from the e1 sweep;
# printed on the second line of each x tick.
RHO = {"n": 0.84, "s": 1.00, "m": 1.86, "l": 2.33}

INK, MUTED, GRID, SPINE = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
BLUE, YELLOW = "#2a78d6", "#eda100"

plt.rcParams.update({
    "font.size": 8, "axes.titlesize": 8.5, "legend.fontsize": 7.5,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "axes.labelsize": 8,
    "pdf.fonttype": 42,      # embed TrueType, not outlines (IEEE requires text)
    "hatch.linewidth": 0.5,
})


def cell(model, ms="33.3"):
    """(arrived_frames, mean_e2e_ms) for one model at the 33.3 ms push deadline.

    33.3 ms is the live 30 fps frame period -- the paper's operating point.
    """
    csv_path = require(os.path.join(RESULTS, f"e1_yolo11{model}", f"push_{ms}ms.csv"),
                       f"e1 push sweep for YOLO11{model}")
    rows = list(csv.DictReader(open(csv_path)))
    arrived = int(rows[-1]["arrivals_cum"])

    sum_path = require(os.path.join(RESULTS, f"e1_yolo11{model}", "summary.csv"),
                       f"e1 summary for YOLO11{model}")
    matches = [r for r in csv.DictReader(open(sum_path))
               if abs(float(r["push_ms"]) - float(ms)) < 0.1]
    if not matches:
        raise MissingData(f"no push_ms=={ms} row in {sum_path}")
    return arrived, float(matches[0]["e2e_mean_ms"])


def style(ax):
    ax.grid(alpha=0.6, color=GRID, lw=0.5, axis="y")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(SPINE)
    ax.tick_params(colors=MUTED, length=2, width=0.6)


def main():
    # YOLO11n is the only model that keeps up (rho<1), so its arrival count is
    # the paced input -- the 100% denominator for every other bar.
    paced = cell("n")[0]
    data = {m: cell(m) for m in MODELS}
    x = list(range(len(MODELS)))
    labels = [f"11{m}\n{RHO[m]:.2f}" for m in MODELS]

    fig, (axa, axb) = plt.subplots(1, 2, figsize=(4.5, 2.05))

    # ---- (a) share of paced frames: survived vs silently died -------------
    surv = [100 * data[m][0] / paced for m in MODELS]
    died = [100 - v for v in surv]
    axa.bar(x, surv, 0.62, color=BLUE, edgecolor="white", lw=0.8)
    axa.bar(x, died, 0.62, bottom=surv, color=YELLOW, edgecolor="white",
            lw=0.8, hatch="///")
    for i, m in enumerate(MODELS):
        axa.text(i, surv[i] / 2, f"{surv[i]:.0f}", ha="center", va="center",
                 fontsize=6.5, color="white", fontweight="bold")
        if died[i] > 8:   # a shorter yellow segment cannot fit its own label
            axa.text(i, surv[i] + died[i] / 2, f"{died[i]:.0f}", ha="center",
                     va="center", fontsize=7, color=INK)
    axa.axhline(100, ls=(0, (2, 2)), color=INK, lw=0.9)
    # Data coords: x=1.9 keeps the callout clear of the y-axis; keep y+lines
    # under set_ylim's 128 or it clips.
    axa.text(1.9, 104, "pipeline-reported coverage:\n100% in every cell",
             ha="center", va="bottom", fontsize=6.4, color=INK)
    axa.set_ylim(0, 128)
    axa.set_yticks([0, 25, 50, 75, 100])
    axa.set_xticks(x)
    axa.set_xticklabels(labels, fontsize=7)
    axa.set_xlabel(r"detector (load $\rho$)", fontsize=7, color=MUTED)
    axa.set_ylabel("share of paced frames (%)")
    axa.set_title("(a) where paced frames go", pad=3)
    # bbox_to_anchor is in axes fractions of axa; negative y puts the legend
    # below the panel, in the space subplots_adjust(bottom=0.34) reserves.
    axa.legend(handles=[
        Patch(fc=BLUE, label="processed"),
        Patch(fc=YELLOW, hatch="///", label="died in capture ring")],
        frameon=False, loc="upper left", bbox_to_anchor=(-0.18, -0.42),
        ncol=2, handlelength=1.2, handleheight=0.9, borderpad=0,
        labelspacing=0.25, columnspacing=1.0)
    style(axa)
    axa.spines["top"].set_visible(False)

    # ---- (b) survivors are stale ------------------------------------------
    e2e = [data[m][1] for m in MODELS]
    axb.bar(x, e2e, 0.62, color=BLUE, edgecolor="white", lw=0.8)
    for i, v in enumerate(e2e):
        # +26 is in DATA units (ms); re-tune if the panel height or font changes.
        axb.text(i, v + 26, f"{v:.0f}", ha="center", fontsize=7, color=INK)
    axb.axhline(33.3, ls=":", color=MUTED, lw=0.9)   # one 30 fps frame period
    axb.set_ylim(0, 1180)
    axb.set_xticks(x)
    axb.set_xticklabels(labels, fontsize=7)
    axb.set_xlabel(r"detector (load $\rho$)", fontsize=7, color=MUTED)
    # (b)'s y-axis lives on the OUTER right edge so the centre gap stays empty
    # and w_pad=0.1 can pull the two plot boxes together.
    axb.set_ylabel("mean output latency (ms)", rotation=270, labelpad=14)
    axb.yaxis.set_label_position("right")
    axb.yaxis.tick_right()
    axb.tick_params(axis="y", left=False, right=True)
    axb.set_title("(b) survivors are stale", pad=3)
    style(axb)
    axb.spines["left"].set_visible(False)
    axb.spines["right"].set_visible(True)
    axb.spines["right"].set_color(SPINE)

    fig.tight_layout(w_pad=0.1)
    fig.subplots_adjust(bottom=0.34, top=0.90)
    savefig(fig, "fig_frame_funnel")

    print(f"paced={paced}")
    for m in MODELS:
        a, e = data[m]
        print(f"  YOLO11{m}: arrived {a} ({100*a/paced:.0f}%), e2e {e:.0f} ms")


if __name__ == "__main__":
    try:
        main()
    except MissingData as e:
        sys.exit(f"make_frame_funnel: {e}")
