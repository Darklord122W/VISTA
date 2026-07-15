#!/usr/bin/env python3
"""load_report.py — aggregate the C4 load study (scheduler off vs on).

Per run dir reads:
  load.csv        (run_plan.py LoadSampler: per-core CPU %, GPU load %,
                   app-process CPU %, scheduler-thread CPU %, RAM). The
                   column is `sched_thread_cpu_pct` regardless of whether the
                   sampler found the thread as sparq-sched (archived binary)
                   or vista-sched (shipped module); -1 means it found neither.)
  tegrastats.log  (adds VDD_GPU_SOC / VDD_CPU_CV power rails, mW)
  metrics.csv     (throughput context: f/s, e2e)

Steady-state window: samples from t0+12 s (TensorRT engine load + pipeline
warmup are excluded) until 2 s before the last sample.

Usage: load_report.py results/C4_load_office results/C4_load_brief ...
Writes LOAD_AGGREGATE.json into each campaign dir.
"""
import csv
import json
import os
import re
import statistics
import sys
from collections import defaultdict

TRIM_HEAD_S = 12.0
TRIM_TAIL_S = 2.0


def load_csv_stats(path):
    rows = list(csv.DictReader(open(path)))
    if len(rows) < 10:
        return None
    t = [float(r["t"]) for r in rows]
    t0, t1 = t[0] + TRIM_HEAD_S, t[-1] - TRIM_TAIL_S
    sel = [r for r, tt in zip(rows, t) if t0 <= tt <= t1]
    if len(sel) < 5:
        sel = rows
    out = {}
    keys = [k for k in rows[0] if k != "t"]
    for k in keys:
        vals = [float(r[k]) for r in sel if float(r[k]) >= 0]
        if vals:
            out[k + "_mean"] = statistics.mean(vals)
            out[k + "_max"] = max(vals)
    out["n_samples"] = len(sel)
    return out


TEGRA_RE = re.compile(
    r"RAM (\d+)/(\d+)MB .*?GR3D_FREQ (\d+)%"
    r".*?VDD_GPU_SOC (\d+)mW.*?VDD_CPU_CV (\d+)mW")


def tegrastats_stats(path):
    if not os.path.exists(path):
        return None
    ram, gr3d, pgpu, pcpu = [], [], [], []
    lines = open(path, errors="replace").read().splitlines()
    if len(lines) < 10:
        return None
    n = len(lines)
    head = int(TRIM_HEAD_S * 2)          # 500 ms interval
    tail = int(TRIM_TAIL_S * 2)
    for line in lines[head:n - tail if n - tail > head else n]:
        m = TEGRA_RE.search(line)
        if not m:
            continue
        ram.append(int(m.group(1)))
        gr3d.append(int(m.group(3)))
        pgpu.append(int(m.group(4)))
        pcpu.append(int(m.group(5)))
    if not gr3d:
        return None
    return {
        "tegra_gr3d_pct_mean": statistics.mean(gr3d),
        "tegra_ram_mb_mean": statistics.mean(ram),
        "tegra_vdd_gpu_soc_mw_mean": statistics.mean(pgpu),
        "tegra_vdd_cpu_cv_mw_mean": statistics.mean(pcpu),
        "tegra_samples": len(gr3d),
    }


def metrics_ctx(rundir):
    p = os.path.join(rundir, "metrics.csv")
    if not os.path.exists(p):
        return {}
    rows = list(csv.DictReader(open(p)))
    if len(rows) < 10:
        return {}
    t0 = float(rows[0]["t_mono"])
    post = [r for r in rows if float(r["t_mono"]) - t0 >= 5.0]
    nin = [int(r["n_in_batch"]) for r in post]
    e2e = [float(r["e2e_ms"]) for r in post if float(r["e2e_ms"]) >= 0]
    dur = float(post[-1]["t_mono"]) - float(post[0]["t_mono"])
    return {"fps": sum(nin) / max(dur, 1e-6),
            "e2e_mean_ms": statistics.mean(e2e) if e2e else -1}


def main():
    for camp in sys.argv[1:]:
        camp = os.path.abspath(camp)
        rows = []
        for d in sorted(os.listdir(camp)):
            rd = os.path.join(camp, d)
            lc = os.path.join(rd, "load.csv")
            if not os.path.isdir(rd) or not os.path.exists(lc):
                continue
            m = re.match(r"(.+)_r(\d+)$", d)
            row = {"run": d, "arm": m.group(1) if m else d}
            ls = load_csv_stats(lc)
            if ls:
                row.update(ls)
            ts = tegrastats_stats(os.path.join(rd, "tegrastats.log"))
            if ts:
                row.update(ts)
            row.update(metrics_ctx(rd))
            rows.append(row)
        by_arm = defaultdict(list)
        for r in rows:
            by_arm[r["arm"]].append(r)
        med = {}
        for arm, rr in sorted(by_arm.items()):
            keys = set().union(*(set(x) for x in rr)) - {"run", "arm"}
            med[arm] = {"n_reps": len(rr)}
            for k in sorted(keys):
                vals = [x[k] for x in rr if isinstance(x.get(k), (int, float))]
                if vals:
                    med[arm][k] = statistics.median(vals)
        out = {"campaign": camp, "runs": rows, "arm_medians": med}
        p = os.path.join(camp, "LOAD_AGGREGATE.json")
        with open(p, "w") as f:
            json.dump(out, f, indent=1)
        print(f"wrote {p} ({len(rows)} runs)")


if __name__ == "__main__":
    main()
