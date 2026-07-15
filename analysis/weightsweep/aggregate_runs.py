#!/usr/bin/env python3
"""aggregate_runs.py — one row of metrics per run dir, for every campaign in
the weightsweep_systemB study.

For each run dir (results/<campaign>/<arm>_r<i>/) computes:

  from metrics.csv (post-warmup, 5 s trim like policy_report.py):
    batches, mean fill, frames/s, e2e mean + p99 (ms), coverage
    (processed/arrivals), accounting-closure delta
  from dets.jsonl:
    per-camera processed-frame counts, cam0 ("hot") share
  from sched.csv (scheduler arms):
    releases, policy drops (displace/evict-stale/evict-held/eos),
    seat-multiplicity histogram (how many releases seated the same camera
    2/3/4 times — System B's signature; System A must be all-1),
    per-camera PTS-regression count across consecutive admits (must be 0
    for fresh-only arms in both systems; salvage is the known exception),
    mean value of admitted candidates
  from match_events (imported from ./match_events.py, same oracle recipe as
  every paper number):
    coverage_vs_oracle, det yield, event recall@{100,250,500,1000} ms,
    per-camera oracle coverage

Usage:
  aggregate_runs.py --campaign results/C1_sweep_brief --oracle <ref_run_dir>
  (--oracle "" skips oracle scoring)

Writes <campaign>/AGGREGATE.json (per-run rows + per-arm medians).
"""
import argparse
import csv
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match_events  # noqa: E402  (the paper's scorer, reused verbatim)

WARMUP_S = 5.0


def metrics_row(rundir):
    p = os.path.join(rundir, "metrics.csv")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        rows = list(csv.DictReader(fh))
    if len(rows) < 10:
        return None
    t0 = float(rows[0]["t_mono"])
    post = [r for r in rows if float(r["t_mono"]) - t0 >= WARMUP_S]
    if not post:
        post = rows
    nin = [int(r["n_in_batch"]) for r in post]
    e2e = sorted(float(r["e2e_ms"]) for r in post if float(r["e2e_ms"]) >= 0)
    dur = float(post[-1]["t_mono"]) - float(post[0]["t_mono"])
    arrivals = int(rows[-1]["arrivals_cum"])
    processed = sum(int(r["n_real"]) for r in rows)
    drops = int(rows[-1]["drops_cum"])
    return {
        "batches": len(post),
        "fill": statistics.mean(nin),
        "fps": sum(nin) / max(dur, 1e-6),
        "e2e_mean_ms": statistics.mean(e2e) if e2e else -1,
        "e2e_p50_ms": e2e[len(e2e) // 2] if e2e else -1,
        "e2e_p99_ms": e2e[int(0.99 * (len(e2e) - 1))] if e2e else -1,
        "coverage": processed / max(arrivals, 1),
        "arrivals": arrivals,
        "processed": processed,
        "drops_cum": drops,
        "closure_delta": arrivals - processed - drops,
    }


def dets_row(rundir):
    # dets_path prefers dets.jsonl.gz (what the archive ships) and falls back
    # to the plain file; iter_jsonl skips the GStreamer stdout interleaved
    # into it, so this must not be replaced with a bare open()/json.loads().
    p = match_events.dets_path(rundir)
    if p is None:
        return None
    per_cam = Counter()
    for r in match_events.iter_jsonl(p):
        per_cam[r["camera_id"]] += 1
    tot = sum(per_cam.values())
    return {
        "proc_cam": {str(c): per_cam.get(c, 0) for c in range(4)},
        "cam0_share": per_cam.get(0, 0) / max(tot, 1),
        "frames_in_dets": tot,
    }


def sched_row(rundir):
    p = os.path.join(rundir, "sched.csv")
    if not os.path.exists(p):
        return None
    admits_by_t = defaultdict(list)      # t -> [(cam, buf_pts)]
    admits_by_cam = defaultdict(list)    # cam -> [buf_pts] in file order
    drops = Counter()
    values = []
    n_admit = 0
    for r in csv.DictReader(open(p)):
        ev = r["event"]
        if ev in ("admit", "admit-salvage"):
            cam = int(r["cam"])
            pts = int(r["buf_pts"])
            admits_by_t[r["t"]].append((cam, pts))
            admits_by_cam[cam].append(pts)
            values.append(float(r["value"]))
            n_admit += 1
        elif ev in ("displace", "evict-stale", "evict-held", "eos",
                    "retain-held"):
            drops[ev] += 1
    seat_hist = Counter()                # max same-cam seats in a release
    multi_releases = 0
    for t, lst in admits_by_t.items():
        per_cam = Counter(c for c, _ in lst)
        mx = max(per_cam.values())
        seat_hist[mx] += 1
        if mx >= 2:
            multi_releases += 1
    regressions = 0
    for cam, pts_list in admits_by_cam.items():
        for a, b in zip(pts_list, pts_list[1:]):
            if b < a:
                regressions += 1
    return {
        "releases": len(admits_by_t),
        "admits": n_admit,
        "seat_hist": {str(k): v for k, v in sorted(seat_hist.items())},
        "multi_seat_releases": multi_releases,
        "multi_seat_frac": multi_releases / max(len(admits_by_t), 1),
        "pts_regressions": regressions,
        "drop_events": dict(drops),
        "admit_value_mean": statistics.mean(values) if values else -1,
    }


# The scheduler's shutdown summary is the ONLY place the drop ledger is
# recorded — sched.csv logs admits only, and has no drop rows. So this regex
# IS the evidence for "every drop counted"; if it stops matching, the drop
# columns silently become empty rather than wrong, which is worse.
#
# PREFIX DUALITY: the archived runs came from the old binary and print
# "[sched]"; the shipped vista module prints "[vista]". Both must match or
# this returns None on one of the two corpora. Do not narrow this.
SCHED_SUMMARY_RE = re.compile(
    r"\[(?:sched|vista)\] \w+: (\d+) releases \(([\d.]+)/s\), (\d+) fresh \+ "
    r"(\d+) salvage admitted, (\d+) policy drops, s_hat ([\d.]+) ms")


def stderr_row(rundir):
    """[sched]/[vista] summary: policy drops + release rate + s_hat."""
    p = os.path.join(rundir, "stderr.log")
    if not os.path.exists(p):
        return None
    m = None
    with open(p, errors="replace") as fh:
        for line in fh:
            mm = SCHED_SUMMARY_RE.search(line)
            if mm:
                m = mm
    if not m:
        return None
    return {"sum_releases": int(m.group(1)),
            "releases_per_s": float(m.group(2)),
            "admitted_fresh": int(m.group(3)),
            "admitted_salvage": int(m.group(4)),
            "policy_drops": int(m.group(5)),
            "s_hat_ms": float(m.group(6))}


def arm_of(dirname):
    """'w40-35-25_r2' -> ('w40-35-25', 2)"""
    m = re.match(r"(.+)_r(\d+)$", dirname)
    return (m.group(1), int(m.group(2))) if m else (dirname, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--oracle", default="")
    ap.add_argument("--deltas", type=float, nargs="+",
                    default=[100, 250, 500, 1000])
    args = ap.parse_args()

    camp = os.path.abspath(args.campaign)
    rundirs = sorted(d for d in os.listdir(camp)
                     if os.path.isdir(os.path.join(camp, d))
                     and re.search(r"_r\d+$", d))

    oracle_frames = oracle_events = None
    if args.oracle:
        recs = match_events.load_run_dets(args.oracle)
        # 0.40/0.30/3 = match_events.py argparse defaults, i.e. the exact
        # oracle recipe behind every published recall number.
        oracle_frames, oracle_events = match_events.build_oracle(
            recs, 0.40, 0.30, 3)
        print(f"oracle: {len(oracle_frames)} frames, "
              f"{len(oracle_events)} events", file=sys.stderr)

    rows = []
    for d in rundirs:
        rd = os.path.join(camp, d)
        # only completed runs (skips in-flight and failed dirs)
        try:
            if json.load(open(os.path.join(rd, "run_meta.json"))).get(
                    "returncode") != 0:
                continue
        except (OSError, ValueError):
            continue
        arm, rep = arm_of(d)
        row = {"run": d, "arm": arm, "rep": rep}
        mr = metrics_row(rd)
        if mr:
            row.update(mr)
        dr = dets_row(rd)
        if dr:
            row.update(dr)
        sr = sched_row(rd)
        if sr:
            row.update(sr)
        er = stderr_row(rd)
        if er:
            row.update(er)
            # Honest-accounting closure (gate G2): every arrival is either
            # processed, still stashed/in-flight at EOS, or a counted policy
            # drop. |delta| should be tiny (tail frames at teardown).
            if "arrivals" in row:
                row["closure_delta"] = (row["arrivals"] - row["processed"]
                                        - er["policy_drops"])
        if (oracle_frames is not None
                and match_events.dets_path(rd) is not None):
            res, _ = match_events.evaluate(rd, oracle_frames, oracle_events,
                                           args.deltas, 0.30)
            row["coverage_vs_oracle"] = res["coverage_vs_oracle"]
            row["det_yield"] = res["det_yield_overall"]
            for k, v in res["event_recall"].items():
                row[f"recall{k}"] = v
            row["per_cam_oracle_cov"] = res["per_cam_coverage"]
        rows.append(row)
        print(f"  {d}: fill {row.get('fill', -1):.2f} "
              f"fps {row.get('fps', -1):.1f} "
              f"share {row.get('cam0_share', -1):.3f} "
              f"r250 {row.get('recall@250ms', -1):.3f}", file=sys.stderr)

    # per-arm medians over repeats
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    NUMKEYS = ["fill", "fps", "e2e_mean_ms", "e2e_p50_ms", "e2e_p99_ms",
               "coverage", "cam0_share", "multi_seat_frac",
               "pts_regressions", "coverage_vs_oracle", "det_yield",
               "recall@100ms", "recall@250ms", "recall@500ms",
               "recall@1000ms", "closure_delta", "admit_value_mean"]
    medians = {}
    for arm, rr in sorted(by_arm.items()):
        med = {"n_reps": len(rr)}
        for k in NUMKEYS:
            vals = [r[k] for r in rr if k in r and r[k] is not None]
            if vals:
                med[k] = statistics.median(vals)
                med[k + "_min"] = min(vals)
                med[k + "_max"] = max(vals)
        medians[arm] = med

    out = {"campaign": camp, "oracle": args.oracle,
           "runs": rows, "arm_medians": medians}
    outp = os.path.join(camp, "AGGREGATE.json")
    with open(outp, "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {outp} ({len(rows)} runs, {len(medians)} arms)")


if __name__ == "__main__":
    main()
