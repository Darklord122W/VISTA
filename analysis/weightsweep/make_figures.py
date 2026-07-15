#!/usr/bin/env python3
"""make_figures.py — FINDINGS.md figures for the weightsweep_systemB study.

Style: paper_lsmc/FIGURE_PLAN.md global contract — fixed policy colors
(FIFO/baseline gray #52514e, FRESH blue #2a78d6, IMP amber #eda100; System B
gets #1baf7a, unused by any other arm in this document), direct labels (no
legend boxes), 8 pt mathtext, no titles (captions in FINDINGS.md interpret),
no top/right spines, light #e1e0d9 grid on the value axis only, vector PDF at
3.5 in column width + 2x PNG for markdown. One claim per figure, ≤2 panels.
(Palette validator unavailable on this host (no node); mitigation per the
dataviz method: every series is direct-labeled, so identity is never
color-alone, and the four hues are strongly lightness-separated.)
"""
import json
import os
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "figures")
os.makedirs(OUT, exist_ok=True)

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e1e0d9"
GRAY, BLUE, AMBER, AQUA = "#52514e", "#2a78d6", "#eda100", "#1baf7a"
COL = 3.5  # in

plt.rcParams.update({
    "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "mathtext.default": "regular", "pdf.fonttype": 42,
    "axes.linewidth": 0.7,
})


def style(ax, grid_axis="y"):
    ax.grid(alpha=0.7, color=GRID, linewidth=0.6, axis=grid_axis)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, width=0.7)


def load(campaign):
    with open(os.path.join(RES, campaign, "AGGREGATE.json")) as f:
        return json.load(f)


def by_arm(agg):
    d = defaultdict(list)
    for r in agg["runs"]:
        d[r["arm"]].append(r)
    return d


def wparse(arm):  # 'w40-35-25' -> (0.40, 0.35, 0.25)
    f, i, r = (int(x) for x in arm[1:].split("-"))
    return f / 100, i / 100, r / 100


def save(fig, name):
    fig.savefig(os.path.join(OUT, name + ".pdf"))
    fig.savefig(os.path.join(OUT, name + ".png"), dpi=200)
    plt.close(fig)
    print("wrote", name)


# ---------------------------------------------------------------------------
# F1 — weight sweep: recall@250 vs w_i (plateau + cliff), brief & persistent
# ---------------------------------------------------------------------------
def fig_sweep():
    fig, axes = plt.subplots(2, 1, figsize=(COL, 3.4), sharex=True)
    for ax, camp, label in [(axes[0], "C1_sweep_brief", "brief events"),
                            (axes[1], "C1_sweep_pers", "persistent events")]:
        arms = by_arm(load(camp))
        xs, ys, isdef = [], [], []
        for arm, rr in arms.items():
            _, wi, _ = wparse(arm)
            med = statistics.median([x.get("recall@250ms", -1) for x in rr])
            xs.append(wi)
            ys.append(med)
            isdef.append(arm == "w40-35-25")
        ax.scatter([x for x, d in zip(xs, isdef) if not d],
                   [y for y, d in zip(ys, isdef) if not d],
                   s=14, color=AMBER, linewidths=0, zorder=3, alpha=0.9)
        for x, y, d in zip(xs, ys, isdef):
            if d:
                ax.scatter([x], [y], s=30, facecolors="none",
                           edgecolors=INK, linewidths=1.0, zorder=4)
                ax.annotate("default", (x, y), xytext=(6, -11),
                            textcoords="offset points", fontsize=7,
                            color=INK)
        ax.axvspan(-0.02, 0.028, color=GRID, alpha=0.5, zorder=1)
        ax.set_ylim(0.25, 0.92)
        ax.set_ylabel("event recall@250 ms")
        ax.text(0.99, 0.06, label, transform=ax.transAxes, ha="right",
                fontsize=7.5, color=MUTED)
        style(ax)
    axes[1].set_xlabel("importance weight $w_i$   (all F/R splits "
                       "overplotted)")
    axes[1].set_xlim(-0.04, 1.04)
    fig.tight_layout(h_pad=0.6)
    save(fig, "fig_sweep_plateau")


# ---------------------------------------------------------------------------
# F2 — quiet-camera ignition: cam3 coverage vs fairness weight (plateau pts)
# ---------------------------------------------------------------------------
def fig_ignition():
    arms = by_arm(load("C1_sweep_brief"))
    fig, ax = plt.subplots(figsize=(COL, 2.3))
    jitter = {"w00-100-00": -0.010, "w25-75-00": 0.0, "w50-50-00": 0.010,
              "w75-25-00": 0.020}
    for arm, rr in arms.items():
        _, wi, wr = wparse(arm)
        if wi < 0.05:      # cliff points: different regime, excluded
            continue
        x = wr + jitter.get(arm, 0.0)
        reps = [r.get("per_cam_oracle_cov", {}).get("3", 0) for r in rr]
        ax.scatter([x] * len(reps), reps, s=10, color=AMBER, alpha=0.55,
                   linewidths=0, zorder=3)
        ax.scatter([x], [statistics.median(reps)], s=26, color=AMBER,
                   edgecolors="white", linewidths=0.8, zorder=4)
        if arm == "w00-100-00":
            ax.annotate("pure importance:\nquiet camera starves",
                        (x, statistics.median(reps)), xytext=(14, -2),
                        textcoords="offset points", fontsize=7, color=INK)
    ax.axvspan(-0.022, 0.032, color=GRID, alpha=0.5, zorder=1)
    ax.text(0.05, 0.44, "$w_r=0$: bistable across repeats\n(same weights, "
            "ignition or starvation)", fontsize=7, color=MUTED, va="center",
            transform=ax.get_xaxis_transform())
    ax.set_xlabel("fairness weight $w_r$   (plateau points, $w_i\\geq0.25$)")
    ax.set_ylabel("quiet-camera coverage")
    ax.set_xlim(-0.05, 0.80)
    ax.set_ylim(0, 1.0)
    style(ax)
    fig.tight_layout()
    save(fig, "fig_quiet_ignition")


# ---------------------------------------------------------------------------
# F3 — the frontier: recall@250 vs e2e, A vs B arms, brief & persistent
# ---------------------------------------------------------------------------
FRONTIER_ARMS = {
    # arm -> (campaign_prefix, color, label, label_offset)
    "A-fresh-k2-s1": ("C2_matrix", BLUE, "A fresh-K2", (5, -9)),
    "A-imp-k2-s1":   ("C2_matrix", AMBER, "A imp-K2-s1", (5, 3)),
    "A-imp-k2-s2":   ("C2_matrix", AMBER, "A imp-K2-s2 (paper)", (-3, 6)),
    "A-imp-k4-s2":   ("C2_matrix", AMBER, "A imp-K4-s2", (5, -3)),
    "A-imp-k2-s3":   ("C3_systemB", AMBER, "A imp-K2-s3", (5, -10)),
    "A-imp-k4-s4":   ("C3_systemB", AMBER, "A imp-K4-s4", (5, -3)),
    "B-imp-k2-s2":   ("C3_systemB", AQUA, "B K2-s2", (5, -9)),
    "B-imp-k2-s3":   ("C3_systemB", AQUA, "B K2-s3", (-6, 6)),
    "B-imp-k4-s2":   ("C3_systemB", AQUA, "B K4-s2", (2, 6)),
    "B-imp-k4-s4":   ("C3_systemB", AQUA, "B K4-s4", (-6, 6)),
}


def fig_frontier():
    fig, axes = plt.subplots(2, 1, figsize=(COL, 4.4), sharex=True)
    for ax, clip, label in [(axes[0], "brief", "brief events"),
                            (axes[1], "pers", "persistent events")]:
        cache = {p: by_arm(load(f"{p}_{clip}"))
                 for p in ("C2_matrix", "C3_systemB")}
        for arm, (pref, color, lab, off) in FRONTIER_ARMS.items():
            rr = cache[pref].get(arm)
            if not rr:
                continue
            xs = sorted(x["e2e_mean_ms"] for x in rr)
            ys = sorted(x.get("recall@250ms", -1) for x in rr)
            xm, ym = statistics.median(xs), statistics.median(ys)
            ax.plot([xs[0], xs[-1]], [ym, ym], color=color, lw=0.8,
                    alpha=0.6, zorder=2)
            ax.plot([xm, xm], [ys[0], ys[-1]], color=color, lw=0.8,
                    alpha=0.6, zorder=2)
            ax.scatter([xm], [ym], s=22, color=color, zorder=3,
                       edgecolors="white", linewidths=0.7)
            ax.annotate(lab, (xm, ym), xytext=off,
                        textcoords="offset points", fontsize=6.6, color=INK)
        ax.set_ylabel("event recall@250 ms")
        ax.text(0.99, 0.04, label, transform=ax.transAxes, ha="right",
                fontsize=7.5, color=MUTED)
        ax.set_ylim(0.28, 0.95)
        style(ax)
    axes[1].set_xlabel("mean output age, ms (whiskers = min–max of 3)")
    axes[1].set_xlim(80, 270)
    fig.tight_layout(h_pad=0.6)
    save(fig, "fig_frontier")


# ---------------------------------------------------------------------------
# F4 — retention law: predicted vs measured hot-camera share
# ---------------------------------------------------------------------------
LAW = [  # arm, campaign_prefix, K, stash, c(per-release cap), system
    ("A-imp-k2-s1", "C2_matrix", 2, 1, 1),
    ("A-imp-k2-s2", "C2_matrix", 2, 2, 1),
    ("A-imp-k2-s3", "C3_systemB", 2, 3, 1),
    ("A-imp-k4-s1", "C2_matrix", 4, 1, 1),
    ("A-imp-k4-s2", "C2_matrix", 4, 2, 1),
    ("A-imp-k4-s4", "C3_systemB", 4, 4, 1),
    ("B-imp-k2-s2", "C3_systemB", 2, 2, 2),
    ("B-imp-k2-s3", "C3_systemB", 2, 3, 2),
    ("B-imp-k4-s2", "C3_systemB", 4, 2, 2),
    ("B-imp-k4-s4", "C3_systemB", 4, 4, 4),
]


def fig_law():
    """Predicted (upper bound) vs measured hot-camera share.

    Where selection exists (candidates > seats) the bound is
    min(stash, d*c)/(K*d).  System A at K=N has NO selection (all-admit):
    every release takes one frame from every camera, so share is exactly
    1/N whatever the stash — those arms are plotted at 1/N and labeled
    once as a cluster.
    """
    d, N = 2, 4
    fig, ax = plt.subplots(figsize=(COL, 2.6))
    ax.plot([0.1, 0.85], [0.1, 0.85], color=GRID, lw=1.0, zorder=1)
    ax.text(0.585, 0.625, "bound met", fontsize=7, color=MUTED, rotation=40)
    pts = defaultdict(list)   # (pred, system) -> [(arm, meas_brief)]
    for arm, pref, K, s, c in LAW:
        allow_all = arm.startswith("A") and K == N
        pred = (1.0 / N) if allow_all else min(s, d * c) / (K * d)
        is_b = arm.startswith("B")
        color = AQUA if is_b else AMBER
        dodge = 0.008 if is_b else -0.008   # coincident A/B points visible
        meas_b = None
        for clip, marker in [("brief", "o"), ("pers", "s")]:
            rr = by_arm(load(f"{pref}_{clip}")).get(arm)
            if not rr:
                continue
            meas = statistics.median([x["cam0_share"] for x in rr])
            if clip == "brief":
                meas_b = meas
            ax.scatter([pred + dodge], [meas], s=20, color=color,
                       marker=marker, zorder=3, edgecolors="white",
                       linewidths=0.6)
        pts[(round(pred, 3), arm[0])].append(
            (f"{'B' if arm.startswith('B') else 'A'} K{K}-s{s}", meas_b))
    # one label per coincident cluster
    labtext = {(0.25, "A"): "A K2-s1 + A-K4 all-admit (s1/s2/s4)",
               (0.25, "B"): "B K4-s2",
               (0.5, "A"): "A K2-s2, A K2-s3",
               (0.5, "B"): "B K2-s2, B K4-s4",
               (0.75, "B"): "B K2-s3"}
    offsets = {(0.25, "A"): (8, -13), (0.25, "B"): (-14, 9),
               (0.5, "A"): (-72, 5), (0.5, "B"): (8, -9),
               (0.75, "B"): (8, -3)}
    for (pred, sysname), entries in pts.items():
        y = statistics.median([e[1] for e in entries if e[1] is not None])
        ax.annotate(labtext[(pred, sysname)], (pred, y),
                    xytext=offsets[(pred, sysname)],
                    textcoords="offset points", fontsize=6.4, color=INK)
    ax.annotate("fairness floor\nbinds first", (0.75, 0.537),
                xytext=(10, -22), textcoords="offset points", fontsize=6.4,
                color=MUTED)
    ax.set_xlabel("predicted share bound  $\\min(stash,\\ d{\\cdot}c)\\,/\\,"
                  "(K{\\cdot}d)$", fontsize=7.5)
    ax.set_ylabel("measured hot-camera share")
    ax.set_xlim(0.15, 0.88)
    ax.set_ylim(0.12, 0.85)
    style(ax, grid_axis="both")
    fig.tight_layout()
    save(fig, "fig_retention_law")


# ---------------------------------------------------------------------------
# F5 — load: total CPU and GPU utilization, scheduler off vs on
# ---------------------------------------------------------------------------
def fig_load():
    rows = []
    for camp, clip in [("C4_load_office", "office"),
                       ("C4_load_brief", "brief")]:
        with open(os.path.join(RES, camp, "LOAD_AGGREGATE.json")) as f:
            d = json.load(f)
        for arm, m in d["arm_medians"].items():
            rows.append((clip, arm, m))
    order = [("office", "off-fifo33"), ("office", "A-imp-k2-s2"),
             ("brief", "off-fifo33"), ("brief", "A-imp-k2-s2"),
             ("brief", "B-imp-k4-s2")]
    labels = {"off-fifo33": "scheduler off (FIFO-33)",
              "A-imp-k2-s2": "scheduler on (imp-K2-s2)",
              "B-imp-k4-s2": "System B (imp-K4-s2)"}
    colors = {"off-fifo33": GRAY, "A-imp-k2-s2": AMBER,
              "B-imp-k4-s2": AQUA}
    fig, axes = plt.subplots(1, 2, figsize=(COL * 2.05, 1.9), sharey=True)
    for i, (ax, key, xlabel, xmax) in enumerate([
            (axes[0], "cpu_all_pct_mean", "total CPU, % of one core", 100),
            (axes[1], "tegra_vdd_gpu_soc_mw_mean",
             "GPU+SoC power, W", 12)]):
        ys, vals, cs, labs = [], [], [], []
        y = 0
        for clip, arm in order:
            m = next((m for c, a, m in rows if c == clip and a == arm), None)
            if m is None or key not in m:
                continue
            v = m[key] / (1000 if "mw" in key else 1)
            ys.append(y)
            vals.append(v)
            cs.append(colors[arm])
            labs.append(f"{labels[arm]} — {clip}")
            y -= 1
        ax.barh(ys, vals, height=0.62, color=cs, linewidth=0)
        for yy, v in zip(ys, vals):
            ax.text(v + xmax * 0.02, yy, f"{v:.1f}", va="center",
                    fontsize=7, color=INK)
        if i == 0:
            ax.set_yticks(ys)
            ax.set_yticklabels(labs, fontsize=6.8, color=INK)
            ax.tick_params(axis="y", length=0)
        ax.set_xlim(0, xmax)
        ax.set_xlabel(xlabel)
        style(ax, grid_axis="x")
    fig.tight_layout(w_pad=1.0)
    save(fig, "fig_load")


if __name__ == "__main__":
    fig_sweep()
    fig_ignition()
    fig_frontier()
    fig_law()
    fig_load()
