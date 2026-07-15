#!/usr/bin/env python3
"""Pipeline diagram (double-column figure*, ~7.0 in wide).

NOT CURRENTLY IN THE PAPER: no \\includegraphics references fig_pipeline in
VISTA-Rev2; Fig. 2 is the drawio system diagram instead (see ../diagrams/).
Shipped because it is pure matplotlib -- it reads no measurement data, so it
cannot go stale against the results, and it is the only regenerable schematic
in the repo.

(a) the stock pipeline: transport backpressure makes the drop decision —
    newest frames die in the bounded ring, uncounted; survivors queue.
(b) the VISTA pipeline: identical elements, plus interception into a bounded
    per-camera stash and a completion-clocked scheduler that releases the
    top-K frames; drops are counted policy decisions.

Colors: stock elements neutral gray; VISTA additions blue (#2a78d6, slot 1);
the silent-loss point uses the reserved 'serious' red (#e34948) as status,
with text label (never color alone). Outputs ../fig_pipeline.{pdf,png}.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import FIG_OUT

HERE = os.path.dirname(os.path.abspath(__file__))
INK, MUTED = "#0b0b0b", "#52514e"
GRAY_FILL, GRAY_EDGE = "#f2f1ec", "#8a8984"
BLUE, BLUE_FILL = "#2a78d6", "#cde2fb"
RED = "#c93938"

plt.rcParams.update({"font.size": 6.5, "pdf.fonttype": 42})

FW, FH = 7.0, 2.75          # inches
fig = plt.figure(figsize=(FW, FH))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 100)
ax.set_ylim(-11, 100)
ax.axis("off")

BH = 15                      # box height (data units)
YA, YB = 74, 20              # row center y for (a) and (b)


def box(xc, yc, w, text, fill=GRAY_FILL, edge=GRAY_EDGE, tc=INK, fs=6.5, lw=0.9):
    ax.add_patch(FancyBboxPatch(
        (xc - w / 2, yc - BH / 2), w, BH,
        boxstyle="round,pad=0.4,rounding_size=1.2",
        fc=fill, ec=edge, lw=lw))
    ax.text(xc, yc, text, ha="center", va="center", fontsize=fs, color=tc,
            linespacing=1.25)


def arrow(x0, x1, y, color=MUTED, ls="-", lw=1.0):
    ax.add_patch(FancyArrowPatch((x0, y), (x1, y), arrowstyle="-|>",
                                 mutation_scale=7, color=color, ls=ls, lw=lw,
                                 shrinkA=0, shrinkB=0))


# ---------------- row (a): stock pipeline --------------------------------
ax.text(1, YA + BH / 2 + 8, "(a) stock pipeline: transport backpressure decides which frames die",
        fontsize=7.5, color=INK, fontweight="bold", ha="left")

box(6.5, YA, 9, "USB\ncams ×4")
box(20.5, YA, 15, "capture + decode\n(per camera)")
box(38.5, YA, 15, "capture ring\n(bounded, per cam)")
box(56.5, YA, 15, "batcher\n(nvstreammux)")
box(73.5, YA, 12, "detector\n(YOLO11, b$\\leq$4)")
box(88.5, YA, 10, "tracker\n(NvSORT)")

arrow(11.5, 12.5, YA)
arrow(28.5, 30.5, YA)
arrow(46.5, 48.5, YA)
arrow(64.5, 67.0, YA)
arrow(80.0, 83.0, YA)

# backpressure + silent loss annotations
ax.add_patch(FancyArrowPatch((56.5, YA - BH / 2 - 1.5), (40.5, YA - BH / 2 - 1.5),
                             connectionstyle="arc3,rad=-0.3", arrowstyle="-|>",
                             mutation_scale=6, color=MUTED, lw=0.8,
                             ls=(0, (3, 2))))
ax.text(50.0, YA - BH / 2 - 10.5, "backpressure (pool fills;\nstanding queue = 276–855 ms)",
        ha="center", va="top", fontsize=5.8, color=MUTED)
ax.add_patch(FancyArrowPatch((33.0, YA - BH / 2), (33.0, YA - BH / 2 - 11),
                             arrowstyle="-|>", mutation_scale=6, color=RED,
                             lw=1.0))
ax.text(24.0, YA - BH / 2 - 13.5, "silent drop of NEWEST frames\n(uncounted, invisible to metrics)",
        ha="center", va="top", fontsize=5.8, color=RED)

# ---------------- row (b): VISTA pipeline ---------------------------------
ax.text(1, YB + BH / 2 + 8, "(b) with VISTA: the drop decision is explicit, value-driven, and counted",
        fontsize=7.5, color=INK, fontweight="bold", ha="left")

box(6.5, YB, 9, "USB\ncams ×4")
box(20.5, YB, 15, "capture + decode\n(per camera)")
box(38.5, YB, 15, "stash (bounded)\nfresh frames", fill=BLUE_FILL, edge=BLUE)
box(56.5, YB, 15, "scheduler\nrelease top-$K$", fill=BLUE_FILL, edge=BLUE)
box(73.5, YB, 14, "batcher + detector\n(batch $=K$ exact)")
box(88.5, YB, 10, "tracker\n(NvSORT)")

arrow(11.5, 12.5, YB)
arrow(28.5, 30.5, YB, color=BLUE)
arrow(46.5, 48.5, YB, color=BLUE)
arrow(64.5, 66.0, YB, color=BLUE)
arrow(81.0, 83.0, YB)

# completion clock feedback
ax.add_patch(FancyArrowPatch((73.5, YB - BH / 2 - 1.5), (58.5, YB - BH / 2 - 1.5),
                             connectionstyle="arc3,rad=-0.3", arrowstyle="-|>",
                             mutation_scale=6, color=BLUE, lw=0.9,
                             ls=(0, (3, 2))))
ax.text(67.0, YB - BH / 2 - 10.5, "completion clock\n(one release per finished batch)",
        ha="center", va="top", fontsize=5.8, color=BLUE)
# counted drops
ax.add_patch(FancyArrowPatch((33.0, YB - BH / 2), (33.0, YB - BH / 2 - 11),
                             arrowstyle="-|>", mutation_scale=6, color=BLUE,
                             lw=1.0))
ax.text(24.0, YB - BH / 2 - 13.5, "drops counted (arrivals $=$\nadmissions $+$ policy drops)",
        ha="center", va="top", fontsize=5.8, color=BLUE)
# value function note above scheduler
ax.text(56.5, YB + BH / 2 + 2.0,
        "$v = w_f\\,\\mathrm{fresh} + w_i\\,\\mathrm{imp} + w_r\\,\\mathrm{fair}$",
        ha="center", va="bottom", fontsize=6.0, color=BLUE)

out = FIG_OUT
os.makedirs(out, exist_ok=True)   # not committed, so it may not exist yet
fig.savefig(os.path.join(out, "fig_pipeline.pdf"), bbox_inches="tight",
            pad_inches=0.02)
fig.savefig(os.path.join(out, "fig_pipeline.png"), dpi=300,
            bbox_inches="tight", pad_inches=0.02)
print("wrote fig_pipeline.{pdf,png}")
