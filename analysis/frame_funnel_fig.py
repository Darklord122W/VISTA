#!/usr/bin/env python3
"""frame_funnel_fig.py — explanatory figure: where frames die under overload,
and why the pipeline's own coverage metric can't see it.

Three panels (E1 data, 33.3 ms deadline cell, ring=4):
 (a) frame funnel per model: of the paced input, how many survived the ring
     (processed) vs died in it (dropped) — pipeline coverage is 100% on all.
 (b) the two coverage numbers: TRUE delivery (arrived/paced) vs what the
     pipeline reports (processed/arrived = 100%) — the gap is the blind spot.
 (c) survivors are also stale: mean e2e per model.
Outputs analysis/frame_funnel.pdf + .png.
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import data as data_path  # noqa: E402
from _paths import ensure_figdir, figure  # noqa: E402
MODELS = ["n", "s", "m", "l"]
LABEL = {m: f"YOLO11{m}" for m in MODELS}
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BLUE, ORANGE = "#2a78d6", "#eda100"   # processed vs dropped (CVD- + print-safe)

plt.rcParams.update({"font.size": 9, "axes.titlesize": 9.5, "legend.fontsize": 8,
                     "pdf.fonttype": 42})


def cell(model, ms="33.3"):
    # The capacity sweep is not shipped (no run data is). Say which file is
    # missing and how to make it, rather than tracebacking on open().
    for need in (f"push_{ms}ms.csv", "summary.csv"):
        p = data_path(f"e1_yolo11{model}", need)
        if not os.path.exists(p):
            sys.exit(f"vista: missing {p}\n"
                     f"  This figure reads the E1 capacity sweep, which is not\n"
                     f"  distributed with this repository. Produce it with\n"
                     f"  harness/run_capacity.sh, then point $VISTA_DATA_ROOT\n"
                     f"  at the results (see harness/README.md).")
    with open(data_path(f"e1_yolo11{model}", f"push_{ms}ms.csv")) as fh:
        r = list(csv.DictReader(fh))
    arrived = int(r[-1]["arrivals_cum"])
    processed = sum(int(x["n_real"]) for x in r)
    with open(data_path(f"e1_yolo11{model}", "summary.csv")) as fh:
        e2e = float([row for row in csv.DictReader(fh)
                     if abs(float(row["push_ms"]) - float(ms)) < 0.1
                     ][0]["e2e_mean_ms"])
    return arrived, processed, e2e


def style(ax):
    ax.grid(alpha=0.6, color=GRID, lw=0.6, axis="y")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED)


def main():
    paced = cell("n")[0]                 # YOLO11n keeps up -> its arrivals = paced
    data = {m: cell(m) for m in MODELS}
    x = range(len(MODELS))

    fig, (axa, axb, axc) = plt.subplots(1, 3, figsize=(9.2, 3.0))

    # (a) frame funnel — bar height = paced; split processed vs dropped-in-ring
    for i, m in enumerate(MODELS):
        arrived, processed, _ = data[m]
        dropped = paced - arrived
        axa.bar(i, processed, color=BLUE, edgecolor="white", lw=1)
        axa.bar(i, dropped, bottom=processed, color=ORANGE, edgecolor="white",
                lw=1, hatch="///")
        if dropped > 40:
            axa.text(i, processed + dropped / 2, f"{100*dropped/paced:.0f}%\ndied",
                     ha="center", va="center", fontsize=7.5, color=INK)
        axa.text(i, processed / 2, f"{100*processed/paced:.0f}%", ha="center",
                 va="center", fontsize=7.5, color="white", fontweight="bold")
    axa.axhline(paced, ls=":", color=MUTED, lw=1)
    axa.text(3.4, paced, "paced input", ha="right", va="bottom", fontsize=7,
             color=MUTED)
    axa.set_xticks(list(x)); axa.set_xticklabels([LABEL[m] for m in MODELS],
                                                 rotation=20, ha="right")
    axa.set_ylabel("frames in the 50 s window")
    axa.set_title("(a) where paced frames go")
    axa.legend(handles=[Patch(fc=BLUE, label="processed (survived ring)"),
                        Patch(fc=ORANGE, hatch="///", label="died in transport ring")],
               frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.28),
               fontsize=7)
    style(axa)

    # (b) two coverage numbers
    true_cov = [100 * data[m][0] / paced for m in MODELS]
    pipe_cov = [100.0 for _ in MODELS]         # processed/arrived is always 100%
    w = 0.38
    axb.bar([i - w/2 for i in x], pipe_cov, w, color=MUTED,
            label="pipeline reports (processed/arrived)")
    axb.bar([i + w/2 for i in x], true_cov, w, color=BLUE,
            label="reality (arrived/paced)")
    for i, v in enumerate(true_cov):
        axb.text(i + w/2, v + 2, f"{v:.0f}%", ha="center", fontsize=7, color=INK)
    axb.set_ylim(0, 112)
    axb.set_xticks(list(x)); axb.set_xticklabels([LABEL[m] for m in MODELS],
                                                 rotation=20, ha="right")
    axb.set_ylabel("coverage (%)")
    axb.set_title("(b) what the metric hides")
    axb.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.28),
               fontsize=7)
    style(axb)

    # (c) survivors are stale too
    e2e = [data[m][2] for m in MODELS]
    axc.bar(x, e2e, color=BLUE, edgecolor="white", lw=1)
    for i, v in enumerate(e2e):
        axc.text(i, v + 15, f"{v:.0f}", ha="center", fontsize=7, color=INK)
    axc.axhline(33.3, ls=":", color=MUTED, lw=1)
    axc.text(3.4, 55, "1 frame period", ha="right", fontsize=7, color=MUTED)
    axc.set_xticks(list(x)); axc.set_xticklabels([LABEL[m] for m in MODELS],
                                                 rotation=20, ha="right")
    axc.set_ylabel("mean output latency (ms)")
    axc.set_title("(c) survivors are stale, too")
    style(axc)

    fig.suptitle(f"Under overload the transport ring silently drops frames "
                 f"(E1 replay, 33.3 ms deadline; paced = {paced} frames)",
                 fontsize=10, y=1.02)
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.34)
    ensure_figdir()
    for ext in ("pdf", "png"):
        fig.savefig(figure(f"frame_funnel.{ext}"), dpi=150,
                    bbox_inches="tight")
    print("wrote", figure("frame_funnel.{pdf,png}"))
    print(f"\npaced={paced}")
    for m in MODELS:
        a, p, e = data[m]
        print(f"  YOLO11{m}: arrived {a} ({100*a/paced:.0f}% of paced), "
              f"dropped {paced-a} ({100*(paced-a)/paced:.0f}%), "
              f"pipeline-cov {100*p/a:.0f}%, e2e {e:.0f}ms")


if __name__ == "__main__":
    main()
