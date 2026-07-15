#!/usr/bin/env python3
"""policy_report.py — per-arm aggregate for one model's e3_<model> campaign.

For each arm run dir (e3_<model>/<arm>_r<i>):
  from metrics.csv (post-warmup): fill, batches/s, frames/s, e2e mean/p99,
    staleness proxy (e2e - compute = pre-inference wait of the worst frame),
    pipeline-reported coverage (processed/arrivals), tracks.
  from match_events.py output (recall_<model>.json): event recall@D, TTA
    recall@D, det yield, true coverage vs the oracle, Jain fairness index.
  from sched.csv (if present): policy drops, salvage admits.

Repeats -> median + observed min/max. Outputs:
  <FIG_DIR>/fig_policy_<model>.pdf   (coverage/age/TTA panels)
  <FIG_DIR>/e3_table_<model>.tex     (booktabs)
  <DERIVED_DIR>/e3_<model>_aggregate.json

SCOPE: this script aggregates ONE campaign directory (e3_s, e3_l, e3_m). It
cannot produce the paper's Table II, which is a composite across four
campaigns (e3_m + e7_surfcal_2/e7_s2_* + e3_m_decimate3_* + e8_impfix_*) —
use make_table2.py for that. It DOES produce Table III wholesale for s and l.

CAUTION: run this for model "m" against the ORIGINAL archive and you will get
an imp-k2 row built from e3_m/imp-k2_r*, which is PRE-importance-bugfix data
that contradicts the draft's VISTA-Activity row by ~7 coverage points. That is
exactly how the stale e3_m_aggregate.json came to exist. The published row
comes from e8_impfix_*. See the `superseded:` block in campaigns.yaml. Runs you
produce with this repository's scheduler are post-fix and unaffected.
"""
import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import data, derived, ensure_derived, ensure_figdir, figure  # noqa: E402

ARMS = ["fifo33", "fifo5", "dropold", "fresh-k4", "fresh-k2", "imp-k2", "salv-k2"]
ARM_LABEL = {"fifo33": "FIFO-33", "fifo5": "FIFO-5", "dropold": "DROP-OLD",
             "fresh-k4": "FRESH-K4", "fresh-k2": "FRESH-K2",
             "imp-k2": "IMP-K2", "salv-k2": "SALV-K2"}
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BLUE, HILIT = "#2a78d6", "#104281"

plt.rcParams.update({
    "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
    "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7.5,
    "pdf.fonttype": 42,
})


def style(ax):
    ax.grid(alpha=0.6, color=GRID, linewidth=0.6, axis="y")
    ax.set_axisbelow(True)
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)
    for s_ in ("left", "bottom"):
        ax.spines[s_].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED)


def run_metrics(rundir, warmup=5.0):
    """Post-warmup metrics of one run.

    The warmup trim (5 s) and the p99 estimator (sorted[int(0.99*(n-1))],
    nearest-rank, no interpolation) are the paper's definitions; every age
    number in every table depends on both. e2e_ms and compute_ms carry
    negative sentinels for batches whose capture stamp was unavailable —
    those rows are DROPPED, not clamped, which is why the e2e and compute
    lists are filtered independently and the wait list is zipped from the
    unfiltered rows with its own guard.
    """
    p = os.path.join(rundir, "metrics.csv")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        allrows = list(csv.DictReader(fh))
    if not allrows:
        return None
    t0 = float(allrows[0]["t_mono"])
    rows = [r for r in allrows if float(r["t_mono"]) >= t0 + warmup]
    if not rows:
        return None
    e2e = [float(r["e2e_ms"]) for r in rows if float(r["e2e_ms"]) >= 0]
    comp = [float(r["compute_ms"]) for r in rows if float(r["compute_ms"]) >= 0]
    wait = [e - c for e, c in zip(
        [float(r["e2e_ms"]) for r in rows],
        [float(r["compute_ms"]) for r in rows])
        if e >= 0 and c >= 0]
    nin = [int(r["n_in_batch"]) for r in rows]
    dur = float(rows[-1]["t_mono"]) - float(rows[0]["t_mono"])
    arr = int(allrows[-1]["arrivals_cum"])
    proc = sum(int(r["n_real"]) for r in allrows)
    out = {
        "fill": statistics.mean(nin),
        "batches_s": len(rows) / max(dur, 1e-6),
        "frames_s": sum(nin) / max(dur, 1e-6),
        "e2e_mean": statistics.mean(e2e) if e2e else float("nan"),
        "e2e_p99": sorted(e2e)[int(0.99 * (len(e2e) - 1))] if e2e else float("nan"),
        "wait_mean": statistics.mean(wait) if wait else float("nan"),
        "compute_mean": statistics.mean(comp) if comp else float("nan"),
        # NOTE: this "coverage" is processed/arrivals, the PIPELINE'S OWN
        # blind metric — it reads ~100% even when the transport ring killed
        # half the paced input upstream of the first probe. The honest number
        # is true_coverage (vs the oracle frame set), added below from the
        # recall JSON. Both are kept so the gap stays visible.
        "coverage": proc / max(arr, 1),
        "tracks": int(allrows[-1]["new_ids_cum"]),
    }
    sched_p = os.path.join(rundir, "sched.csv")
    if os.path.exists(sched_p):
        ev = defaultdict(int)
        with open(sched_p) as fh:
            for r in csv.DictReader(fh):
                ev[r["event"]] += 1
        out["salvage_admits"] = ev.get("admit-salvage", 0)
        out["admits"] = ev.get("admit", 0) + ev.get("admit-salvage", 0)
    return out


def median_range(vals):
    """(median, min, max) over repeats.

    Formerly named boot_ci(), and it took n=2000/seed=7 bootstrap arguments
    that it never used — it has always returned the observed range. At n<=5
    repeats a bootstrap percentile CI degenerates to approximately the
    min/max anyway, and reporting it as a "95% CI" would be a claim the data
    cannot support. The paper says "medians with min-max whiskers", which is
    what this returns: the paper was right and the function name was wrong.
    """
    vals = [v for v in vals if v == v]          # drop NaN
    if not vals:
        return (float("nan"),) * 3
    if len(vals) == 1:
        return vals[0], vals[0], vals[0]
    return statistics.median(vals), min(vals), max(vals)


def explain_missing_recall(model, recall_file):
    """One line saying why the oracle columns are blank."""
    print(f"note: {recall_file} absent — oracle columns will be blank. "
          f"Run make_all.py --tier rescore to build it.", file=sys.stderr)
    if model == "m":
        print(
            "      CAUTION for model 'm': if this data root is the ORIGINAL "
            "archive,\n"
            "      rescoring e3_m/ rebuilds imp-k2/salv-k2 rows from "
            "PRE-importance-bugfix\n"
            "      runs, which contradict the draft's VISTA-Activity row by "
            "~7 coverage\n"
            "      points. The published row comes from e8_impfix_* via "
            "make_table2.py.\n"
            "      See the `superseded:` block in campaigns.yaml. Data you "
            "produced\n"
            "      yourself with this repository's scheduler is post-fix and "
            "unaffected.",
            file=sys.stderr)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="policy_report.py",
        description=__doc__.split("\n\n")[0],
        epilog=(
            "MODEL names the campaign dir e3_<MODEL> under the run-data root\n"
            "($VISTA_DATA_ROOT — no run data ships in this repository; see\n"
            "harness/README.md). The campaigns behind the draft are e3_s,\n"
            "e3_m and e3_l; a root you built yourself may define others. A\n"
            "MODEL with no campaign dir, or one with no readable run dirs, is\n"
            "an error: nothing is written.\n"
            "\n"
            "CAUTION, if the root is the ORIGINAL archive: MODEL 'm' builds\n"
            "an imp-k2 row from PRE-importance-bugfix data that contradicts\n"
            "the draft's VISTA-Activity row by ~7 coverage points. That row\n"
            "comes from e8_impfix_* via make_table2.py. See the `superseded:`\n"
            "block in analysis/campaigns.yaml."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "model", nargs="?", default="m", metavar="MODEL",
        help="model tag selecting campaign dir e3_<MODEL> (default: %(default)s)")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    model = args.model

    # Resolve and validate BEFORE creating any output. The generator used to
    # take sys.argv[1] unchecked, so `--help` was read as a model tag and wrote
    # e3_--help_aggregate.json / e3_table_--help.tex / fig_policy_--help.pdf
    # into the archive. argparse now claims --help, and the two guards below
    # reject any tag with no data instead of emitting an empty-{} aggregate.
    root = data(f"e3_{model}")
    if not os.path.isdir(root):
        ap.error(f"no campaign directory for model {model!r}: {root} does not "
                 f"exist. Shipped models are s, m, l (set $VISTA_DATA_ROOT to "
                 f"point at another run-data root). Nothing was written.")

    recall_file = derived(f"recall_{model}.json")
    recalls = {}
    if os.path.exists(recall_file):
        with open(recall_file) as fh:
            data_ = json.load(fh)
        for r in data_["results"]:
            arm = os.path.basename(r["run"]).rsplit("_r", 1)[0]
            recalls.setdefault(arm, []).append(r)
    else:
        explain_missing_recall(model, recall_file)

    agg = {}
    for arm in ARMS:
        runs = []
        for i in range(10):
            d = os.path.join(root, f"{arm}_r{i}")
            if os.path.isdir(d):
                m = run_metrics(d)
                if m:
                    runs.append(m)
        if not runs:
            continue
        a = {}
        for k in runs[0]:
            a[k] = median_range([r[k] for r in runs])
        if arm in recalls:
            for delta in ["@250ms", "@500ms", "@1000ms", "@2000ms"]:
                a[f"ev{delta}"] = median_range(
                    [r["event_recall"].get(delta, float("nan"))
                     for r in recalls[arm]])
                a[f"tta{delta}"] = median_range(
                    [r.get("tta_recall", {}).get(delta, float("nan"))
                     for r in recalls[arm]])
            a["true_coverage"] = median_range(
                [r["coverage_vs_oracle"] for r in recalls[arm]])
            a["det_yield"] = median_range(
                [r["det_yield_overall"] for r in recalls[arm]])
            a["jain"] = median_range([
                (sum(r["per_cam_coverage"].values()) ** 2) /
                (4 * sum(v * v for v in r["per_cam_coverage"].values()) or 1)
                for r in recalls[arm]])
        a["n_runs"] = len(runs)
        agg[arm] = a

    if not agg:
        print(f"error: {root} contains no readable run dir for any known arm "
              f"({', '.join(ARMS)}); expected <arm>_r<i>/metrics.csv. "
              f"Nothing was written.", file=sys.stderr)
        return 2

    ensure_derived()
    with open(derived(f"e3_{model}_aggregate.json"), "w") as f:
        json.dump(agg, f, indent=2)

    # ---- console + tex table ----
    cols = ["true_coverage", "e2e_mean", "e2e_p99", "wait_mean", "det_yield",
            "tta@500ms", "tta@2000ms", "salvage_admits"]
    print(f"{'arm':10s}" + "".join(f"{c:>16s}" for c in cols))
    tex = [
        "% generated by analysis/policy_report.py",
        "\\begin{tabular}{lrrrrrrr}",
        "\\toprule",
        "policy & coverage & e2e mean & e2e p99 & det.\\ yield & "
        "TTA@0.5s & TTA@2s & salv.\\ \\\\",
        " & (\\%) & (ms) & (ms) & (\\%) & (\\%) & (\\%) & \\\\",
        "\\midrule"]
    for arm in ARMS:
        if arm not in agg:
            continue
        a = agg[arm]
        line = f"{arm:10s}"
        for c in cols:
            v = a.get(c)
            line += f"{v[0]:>16.3f}" if v and v[0] == v[0] else f"{'-':>16s}"
        print(line)

        def fmt(key, scale=1, prec=2):
            v = a.get(key)
            if not v or v[0] != v[0]:
                return "--"
            return f"{v[0]*scale:.{prec}f}"
        tex.append(
            f"{ARM_LABEL[arm]} & {fmt('true_coverage',100,1)} & {fmt('e2e_mean',1,0)} & "
            f"{fmt('e2e_p99',1,0)} & {fmt('det_yield',100,1)} & "
            f"{fmt('tta@500ms',100,1)} & {fmt('tta@2000ms',100,1)} & "
            f"{fmt('salvage_admits',1,0)} \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    ensure_figdir()
    with open(figure(f"e3_table_{model}.tex"), "w") as f:
        f.write("\n".join(tex) + "\n")

    # ---- figure: coverage / age / TTA bars with min-max whiskers ----
    arms = [a for a in ARMS if a in agg]
    x = range(len(arms))
    panels = [("true_coverage", "true coverage (%)", 100, "(a) coverage"),
              ("e2e_mean", "output age, mean (ms)", 1, "(b) age"),
              ("tta@500ms", "TTA recall@500ms (%)", 100,
               "(c) event awareness")]
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.3))
    for ax, (key, ylab, scale, title) in zip(axes, panels):
        med, lo, hi = [], [], []
        for a in arms:
            v = agg[a].get(key, (float("nan"),) * 3)
            med.append(v[0] * scale)
            lo.append((v[0] - v[1]) * scale)
            hi.append((v[2] - v[0]) * scale)
        colors = [HILIT if a in ("imp-k2", "salv-k2") else BLUE for a in arms]
        ax.bar(x, med, width=0.68, color=colors, edgecolor="white", linewidth=1)
        # An oracle-backed panel is all-NaN when recall_<model>.json is
        # quarantined/absent. Drawing error bars over all-NaN makes numpy
        # reduce over NaN and emit "invalid value encountered in reduce" —
        # 4 warnings that look like a numerical fault but only mean "no oracle
        # data here". Skip the call instead of muffling the category: NaN error
        # bars render nothing, so the figure is byte-for-byte what it was, and
        # a real NaN in a panel that DOES have data will still warn.
        if any(v == v for v in med):
            ax.errorbar(x, med, yerr=[lo, hi], fmt="none", ecolor=INK2,
                        elinewidth=0.9, capsize=2)
        ax.set_xticks(list(x))
        ax.set_xticklabels([ARM_LABEL[a] for a in arms], rotation=38,
                           ha="right")
        ax.set_title(title, color=INK)
        ax.set_ylabel(ylab, color=INK2)
        style(ax)
    fig.tight_layout()
    out = figure(f"fig_policy_{model}.pdf")
    fig.savefig(out)
    plt.close(fig)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
