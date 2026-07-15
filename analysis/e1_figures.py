#!/usr/bin/env python3
"""e1_figures.py — E1 (model x deadline) paper figures + capacity table.

Reads experiments/results/e1_yolo11{n,s,m,l}/ (summary.csv + per-run
metrics push_*.csv) and produces, in paper/figures/:

  fig_capacity_wall.pdf   2-panel: coverage vs deadline, e2e mean vs deadline,
                          one line per model (sequential blue ramp: model size
                          is ordered magnitude).
  fig_service_times.pdf   S(k): per-batch-size compute time per model
                          (from metrics rows, post-warmup).
  capacity_table.tex      booktabs table: S(1), S(4), sustainable frames/s,
                          arrival rate, load factor rho per model.

Design rules (dataviz skill): one axis per plot; sequential ramp for the
ordered model family; direct labels + legend; thin marks; recessive grid;
vector PDF output for print (light surface only).
"""
import csv
import os
import statistics
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import data as data_path  # noqa: E402
from _paths import ensure_figdir, figure  # noqa: E402

FIGDIR = ensure_figdir()

MODELS = ["n", "s", "m", "l"]
MODEL_LABEL = {m: f"YOLO11{m}" for m in MODELS}
# one-hue sequential ramp, light -> dark with model size (ordered magnitude)
RAMP = {"n": "#9dc2f0", "s": "#5598e7", "m": "#2a78d6", "l": "#104281"}
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
ARRIVAL_FPS = 4 * 29.8   # measured delivered rate (gap-every 44)

plt.rcParams.update({
    "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
    "legend.fontsize": 7, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "pdf.fonttype": 42, "ps.fonttype": 42,
})


def style(ax):
    ax.grid(alpha=0.6, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)
    for s_ in ("left", "bottom"):
        ax.spines[s_].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED)
    ax.xaxis.label.set_color(INK2)
    ax.yaxis.label.set_color(INK2)
    ax.title.set_color(INK)


def load_summary(model):
    p = data_path(f"e1_yolo11{model}", "summary.csv")
    if not os.path.exists(p):
        return []
    return list(csv.DictReader(open(p)))


def service_times(model, warmup=5.0):
    """S(k): compute_ms by n_in_batch across all runs of the model's sweep."""
    d = data_path(f"e1_yolo11{model}")
    by_k = defaultdict(list)
    if not os.path.isdir(d):
        return by_k
    for f in sorted(os.listdir(d)):
        if not (f.startswith("push_") and f.endswith(".csv")):
            continue
        rows = list(csv.DictReader(open(os.path.join(d, f))))
        if not rows:
            continue
        t0 = float(rows[0]["t_mono"])
        for r in rows:
            if float(r["t_mono"]) < t0 + warmup:
                continue
            k = int(r["n_in_batch"])
            c = float(r["compute_ms"])
            if 1 <= k <= 4 and c >= 0:
                by_k[k].append(c)
    return by_k


def main():
    summaries = {m: load_summary(m) for m in MODELS}
    present = [m for m in MODELS if summaries[m]]
    if not present:
        print("no E1 summaries found", file=sys.stderr)
        return 1

    # ---- fig_capacity_wall ----
    fig, (ax_cov, ax_lat) = plt.subplots(1, 2, figsize=(7.0, 2.5))
    for m in present:
        ms = [float(r["push_ms"]) for r in summaries[m]]
        # TRUE coverage = frames actually processed per second vs paced input.
        # The pipeline's own 'coverage' column (processed/arrivals) reads
        # 1.0000 even when the transport ring kills half the paced frames
        # upstream of the first probe — that blindness is itself a finding.
        cov = [min(100.0, 100 * float(r["frames_s"]) / ARRIVAL_FPS)
               for r in summaries[m]]
        e2e = [float(r["e2e_mean_ms"]) for r in summaries[m]]
        ax_cov.plot(ms, cov, "o-", color=RAMP[m], lw=1.6, ms=3.5,
                    label=MODEL_LABEL[m])
        ax_lat.plot(ms, e2e, "o-", color=RAMP[m], lw=1.6, ms=3.5,
                    label=MODEL_LABEL[m])
    ax_cov.set_xlabel("push deadline (ms)")
    ax_cov.set_ylabel("delivered / paced frames (%)")
    ax_cov.set_ylim(0, 105)
    ax_cov.set_title("(a) true coverage: heavier detectors cannot keep up")
    ax_cov.legend(frameon=False, loc="lower right")
    ax_lat.set_xlabel("push deadline (ms)")
    ax_lat.set_ylabel("e2e latency, mean (ms)")
    ax_lat.set_yscale("log")
    ax_lat.set_title("(b) latency under FIFO backpressure")
    ax_lat.legend(frameon=False, loc="center left")
    for ax in (ax_cov, ax_lat):
        style(ax)
    fig.tight_layout()
    out = figure("fig_capacity_wall.pdf")
    fig.savefig(out)
    plt.close(fig)
    print("wrote", out)

    # ---- capacity table ----
    # Under saturation, compute_ms (mux->tracker) is inflated by post-mux
    # queueing, so the honest batch-4 service time is derived from saturated
    # throughput: S4_eff = 4000 / max sustained frames/s. YOLO11n never
    # saturates, so its S(1)/S(4) come from measured compute at fill 1 / 4.
    tab_rows = []
    for m in present:
        by_k = service_times(m)
        fps_cells = [float(r["frames_s"]) for r in summaries[m]]
        capacity = max(fps_cells)
        rho = ARRIVAL_FPS / capacity
        # Saturated iff batches stay full even at the smallest push deadline
        # (an unsaturated model forms small batches there, like YOLO11n's
        # fill 1.18 at 5 ms; a saturated one always has a backlog of 4).
        smallest = min(summaries[m], key=lambda r: float(r["push_ms"]))
        saturated = float(smallest["mean_n_in_batch"]) > 3.5
        if saturated:
            s4 = 4000.0 / capacity
        else:
            s4 = statistics.median(by_k[4]) if 4 in by_k else float("nan")
            capacity = 4000.0 / s4 if s4 == s4 else float("nan")
            rho = ARRIVAL_FPS / capacity
        s1 = statistics.median(by_k[1]) if 1 in by_k and len(by_k[1]) > 20 \
            else float("nan")
        tab_rows.append((MODEL_LABEL[m], s1, s4, capacity, rho))

    with open(figure("capacity_table.tex"), "w") as f:
        f.write("% generated by analysis/e1_figures.py\n"
                "% S(4) from saturated throughput (queue-free); S(1) measured\n"
                "% directly where the model runs unsaturated batches of 1.\n")
        f.write("\\begin{tabular}{lrrrr}\n\\toprule\n")
        f.write("detector & $S(1)$ (ms) & $S(4)$ (ms) & capacity (f/s) "
                "& load $\\rho$ \\\\\n\\midrule\n")
        for name, s1, s4, cap, rho in tab_rows:
            s1s = f"{s1:.1f}" if s1 == s1 else "--"
            f.write(f"{name} & {s1s} & {s4:.1f} & {cap:.0f} & {rho:.2f} \\\\\n")
        f.write("\\bottomrule\n\\end{tabular}\n")
    print("wrote capacity_table.tex")

    print(f"\n{'model':8s} {'S(1)':>7s} {'S(4)':>7s} {'capacity f/s':>13s} {'rho':>6s}")
    for name, s1, s4, cap, rho in tab_rows:
        s1s = f"{s1:7.1f}" if s1 == s1 else "     --"
        print(f"{name:8s} {s1s} {s4:7.1f} {cap:13.0f} {rho:6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
