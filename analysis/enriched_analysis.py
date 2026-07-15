#!/usr/bin/env python3
"""enriched_analysis.py — exact TTA validation + salvage actionability.

Uses runs recorded with the enriched binary:
  dets.jsonl has t_emit (CLOCK_MONOTONIC s at emission)
  sched.csv has buf_pts per admit/admit-salvage decision

1. EXACT TTA: map the replay/pts timeline to the mono clock per run via
   c = min(t_emit - pts) (the freshest emission, minus its minimal pipeline
   latency, bounds c; using the min biases all TTAs of a run DOWN by that
   minimal latency, which is conservative *against* the FIFO-vs-sched gap
   because the sched runs' minimal latency is smaller). Event TTA_exact =
   t_emit(first matching detection) - (t_onset_pts + c). Compare recall@D
   exact vs the mean-e2e approximation used for the campaign.

2. SALVAGE ACTIONABILITY: for each admit-salvage frame, its oracle-matched
   detections; count those NOT matched by any other processed frame of the
   same camera within +/-W ms of pts (default W=150) — the unique yield.
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns
from _paths import data, derived, ensure_derived
from match_events import build_oracle, iou, load_run_dets, run_mean_e2e
DELTAS = [100, 250, 500, 1000, 2000]


def load_run(run_dir):
    recs = load_run_dets(run_dir)
    frames = {}
    for r in recs:
        frames[(r["camera_id"], r["buf_pts"])] = {
            "t_emit": r.get("t_emit"), "dets": r.get("detections", [])}
    return frames


def pts_clock_offset(frames):
    return min(f["t_emit"] - pts / 1e9
               for (c, pts), f in frames.items() if f["t_emit"] is not None)


def tta_recall(run_dir, events, iou_det=0.30):
    frames = load_run(run_dir)
    c0 = pts_clock_offset(frames)
    mean_e2e = run_mean_e2e(run_dir)
    exact, approx = [], []
    for e in events:
        cam = e["cam"]
        t_ex = t_ap = None
        for pts, box in sorted(e["boxes"].items()):
            f = frames.get((cam, pts))
            if f is None:
                continue
            for pd in f["dets"]:
                if pd["class_name"] == e["cls"] and iou(pd, box) >= iou_det:
                    t_ap = (pts - e["t0"]) / 1e6 + mean_e2e
                    if f["t_emit"] is not None:
                        t_ex = (f["t_emit"] - (e["t0"] / 1e9 + c0)) * 1e3
                    break
            if t_ap is not None:
                break
        exact.append(t_ex)
        approx.append(t_ap)
    out = {"run": run_dir, "mean_e2e": mean_e2e, "n_events": len(events)}
    for d in DELTAS:
        out[f"exact@{d}"] = sum(1 for t in exact if t is not None and t <= d) / len(events)
        out[f"approx@{d}"] = sum(1 for t in approx if t is not None and t <= d) / len(events)
    return out


def salvage_actionability(run_dir, oracle_frames, window_ms=150, iou_det=0.30):
    frames = load_run(run_dir)
    salv = []
    with open(os.path.join(run_dir, "sched.csv")) as f:
        for r in csv.DictReader(f):
            if r["event"] == "admit-salvage":
                salv.append((int(r["cam"]), int(r["buf_pts"])))
    by_cam = {}
    for (cam, pts) in frames:
        by_cam.setdefault(cam, []).append(pts)
    for cam in by_cam:
        by_cam[cam].sort()

    n_dets = n_oracle_matched = n_unique = 0
    for cam, pts in salv:
        f = frames.get((cam, pts))
        if f is None:
            continue
        odets = oracle_frames.get((cam, pts), [])
        n_dets += len(f["dets"])
        for od in odets:
            hit = any(pd["class_name"] == od["class_name"] and
                      iou(pd, od) >= iou_det for pd in f["dets"])
            if not hit:
                continue
            n_oracle_matched += 1
            # covered by a neighboring processed frame of the same camera?
            covered = False
            for npts in by_cam[cam]:
                if npts == pts or abs(npts - pts) > window_ms * 1e6:
                    continue
                nod = oracle_frames.get((cam, npts), [])
                ndet = frames[(cam, npts)]["dets"]
                for od2 in nod:
                    if od2["class_name"] != od.get("class_name"):
                        continue
                    if iou(od2, od) < 0.3:
                        continue
                    if any(pd["class_name"] == od2["class_name"] and
                           iou(pd, od2) >= iou_det for pd in ndet):
                        covered = True
                        break
                if covered:
                    break
            if not covered:
                n_unique += 1
    return {"run": run_dir, "salvage_frames": len(salv),
            "salvage_dets": n_dets, "oracle_matched": n_oracle_matched,
            "unique_within_%dms" % window_ms: n_unique,
            "actionability": n_unique / max(n_oracle_matched, 1)}


def main():
    oracle_dir, ometa = _campaigns.oracle("office_x")
    oracle_frames, events = build_oracle(load_run_dets(oracle_dir),
                                         ometa["conf"], 0.30, 3)
    print(f"oracle: {len(events)} events", file=sys.stderr)

    out = {"tta": [], "salvage": []}
    for run in ["enriched_m_fifo/fifo33_r0", "enriched_m_imp/imp-k2_r0",
                "enriched_m_salv/salv-k2_r0"]:
        d = data(run)
        if not os.path.isdir(d):
            continue
        r = tta_recall(d, events)
        out["tta"].append(r)
        name = run.split("/")[0]
        row = " ".join(f"{r[f'exact@{d_}']:.3f}/{r[f'approx@{d_}']:.3f}"
                       for d_ in DELTAS)
        print(f"{name:20s} e2e={r['mean_e2e']:.0f}ms  exact/approx @",
              DELTAS, ":", row)
    d = data("enriched_m_salv", "salv-k2_r0")
    if os.path.isdir(d):
        s = salvage_actionability(d, oracle_frames)
        out["salvage"].append(s)
        print("salvage:", json.dumps(s, indent=1))
    ensure_derived()
    with open(derived("enriched_analysis.json"), "w") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
