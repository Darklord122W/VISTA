#!/usr/bin/env python3
"""match_events.py — oracle-referenced event & detection recall for SPARQ runs.

Inputs
------
--oracle DIR   a `ref` run of run_eval.py made with the ORACLE detector
               (yolo11x) at ring=0: every surviving frame processed. Its
               dets.jsonl defines ground truth. Frames are keyed by
               (camera_id, buf_pts) — deterministic across runs because the
               replay skew injection is index/PTS-deterministic.
--run DIR      one policy run dir (repeatable). Its dets.jsonl holds what the
               policy's detector saw on the frames the policy processed.

Ground truth
------------
* ORACLE DETECTIONS: every (frame, class, bbox) from the oracle with
  confidence >= --oracle-conf.
* ORACLE EVENTS: greedy IoU tracking over oracle detections per (camera,
  class): a new event starts when a detection matches no active track
  (IoU < --iou-track) ; an event must persist >= --min-frames oracle frames
  to count (kills single-frame flicker). Event time = first frame's buf_pts.

Metrics per policy run
----------------------
* detection recall  : oracle detections on frames the policy processed that
  the policy matched (same class, IoU >= --iou-det). Also the "processed-
  frame" denominator variant.
* detection yield   : matched oracle detections / ALL oracle detections
  (coverage-weighted recall — the honest headline).
* event recall@D    : an event is recalled if the policy has a matching
  detection (class + IoU vs the oracle event's bbox in the matched frame) on
  that camera within [t0, t0 + D] for D in --deltas (ms).
* event onset delay : first policy match time - event t0 (dist for recalled).
* per-camera coverage of processed frames.

Notes: PTS (buf_pts) is used ONLY as a frame identity/time key of the replay
timeline, not as a wall-clock quantity. IoU matching is in pixel coords
(both detectors run at 640x480 source resolution).

Output: JSON to stdout or --out FILE.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

# Reuse the parent package's dets reader rather than keeping a second copy:
# it is the one place that knows dets files may be gzipped and that the file
# is not valid JSONL (GStreamer stdout is interleaved into it). A duplicated
# reader here would drift from it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _paths import dets_path, iter_jsonl  # noqa: E402


def load_jsonl(path):
    """Load a dets file. Accepts dets.jsonl even when only dets.jsonl.gz
    exists — the archive ships the gzipped form."""
    if path.endswith(".jsonl") and not os.path.exists(path):
        gz = path + ".gz"
        if os.path.exists(gz):
            path = gz
    return list(iter_jsonl(path))


def load_run_dets(run_dir, stem="dets.jsonl"):
    """Resolve .gz-or-plain inside a run directory."""
    p = dets_path(run_dir, stem)
    if p is None:
        raise FileNotFoundError(os.path.join(run_dir, stem + "{,.gz}"))
    return list(iter_jsonl(p))


def iou(a, b):
    ax1, ay1, ax2, ay2 = a["x"], a["y"], a["x"] + a["width"], a["y"] + a["height"]
    bx1, by1, bx2, by2 = b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / max(ua, 1e-9)


def build_oracle(recs, conf, iou_track, min_frames):
    """frames: {(cam, pts): [dets]}; events: list of dicts."""
    frames = defaultdict(list)
    for r in recs:
        for d in r.get("detections", []):
            if d.get("confidence", 0.0) >= conf:
                frames[(r["camera_id"], r["buf_pts"])].append(d)

    # Greedy track-building per (camera, class) over PTS-ordered frames.
    by_cam = defaultdict(list)   # cam -> sorted [(pts, dets)]
    for (cam, pts), dets in frames.items():
        by_cam[cam].append((pts, dets))
    events = []
    for cam, lst in by_cam.items():
        lst.sort()
        active = []  # {cls, last_pts, last_box, frames, t0, boxes:{pts:box}}
        for pts, dets in lst:
            unmatched = list(dets)
            for tr in active:
                best, best_i = None, iou_track
                for d in unmatched:
                    if d["class_name"] != tr["cls"]:
                        continue
                    i = iou(d, tr["last_box"])
                    if i >= best_i:
                        best, best_i = d, i
                if best is not None:
                    unmatched.remove(best)
                    tr["last_box"] = best
                    tr["last_pts"] = pts
                    tr["frames"] += 1
                    tr["boxes"][pts] = best
            # expire tracks idle > 500 ms of replay-time
            for tr in list(active):
                if pts - tr["last_pts"] > 500e6:
                    events.append(tr)
                    active.remove(tr)
            for d in unmatched:
                active.append({"cam": cam, "cls": d["class_name"], "t0": pts,
                               "last_pts": pts, "last_box": d, "frames": 1,
                               "boxes": {pts: d}})
        events.extend(active)
    events = [e for e in events if e["frames"] >= min_frames]
    events.sort(key=lambda e: e["t0"])
    return frames, events


def run_mean_e2e(run_dir, warmup=5.0):
    """Mean e2e (capture->emission) of the run, from metrics.csv, post-warmup.
    Used to convert frame-time event recall into EMISSION-time recall
    (time-to-awareness): dets.jsonl has no emission stamps, so each match is
    charged the run's mean output staleness."""
    import csv as csvmod
    p = os.path.join(run_dir, "metrics.csv")
    if not os.path.exists(p):
        return 0.0
    rows = list(csvmod.DictReader(open(p)))
    if not rows:
        return 0.0
    t0 = float(rows[0]["t_mono"])
    e2e = [float(r["e2e_ms"]) for r in rows
           if float(r["t_mono"]) >= t0 + warmup and float(r["e2e_ms"]) >= 0]
    return sum(e2e) / len(e2e) if e2e else 0.0


def evaluate(run_dir, oracle_frames, events, deltas_ms, iou_det):
    recs = load_run_dets(run_dir)
    pol_frames = defaultdict(list)
    for r in recs:
        pol_frames[(r["camera_id"], r["buf_pts"])].extend(
            r.get("detections", []))
        if not r.get("detections"):
            pol_frames[(r["camera_id"], r["buf_pts"])]  # mark processed

    processed = set(pol_frames.keys())
    oracle_keys = set(oracle_frames.keys())
    per_cam_proc = defaultdict(int)
    per_cam_orc = defaultdict(int)
    for (cam, pts) in oracle_keys:
        per_cam_orc[cam] += 1
        if (cam, pts) in processed:
            per_cam_proc[cam] += 1

    # Detection matching on common frames.
    o_common = o_matched = 0
    for key in oracle_keys & processed:
        odets = oracle_frames[key]
        pdets = list(pol_frames[key])
        o_common += len(odets)
        for od in odets:
            hit = None
            for pd in pdets:
                if pd["class_name"] == od["class_name"] and iou(pd, od) >= iou_det:
                    hit = pd
                    break
            if hit is not None:
                pdets.remove(hit)
                o_matched += 1
    o_total = sum(len(v) for v in oracle_frames.values())

    # Event recall: policy detection matching the event's oracle box at any
    # oracle frame of the event, within t0..t0+delta.
    ev_results = []
    for e in events:
        cam = e["cam"]
        first_match_dt = None
        for pts, box in sorted(e["boxes"].items()):
            key = (cam, pts)
            if key not in processed:
                continue
            for pd in pol_frames[key]:
                if pd["class_name"] == e["cls"] and iou(pd, box) >= iou_det:
                    first_match_dt = (pts - e["t0"]) / 1e6  # ns -> ms
                    break
            if first_match_dt is not None:
                break
        ev_results.append({"cam": cam, "cls": e["cls"], "t0": e["t0"],
                           "frames": e["frames"],
                           "onset_delay_ms": first_match_dt})

    mean_e2e = run_mean_e2e(run_dir)
    out = {
        "run": run_dir,
        "mean_e2e_ms": mean_e2e,
        "frames_processed": len(processed),
        "frames_oracle": len(oracle_keys),
        "coverage_vs_oracle": len(oracle_keys & processed) / max(len(oracle_keys), 1),
        "per_cam_coverage": {c: per_cam_proc[c] / max(per_cam_orc[c], 1)
                             for c in sorted(per_cam_orc)},
        "det_recall_processed_frames": o_matched / max(o_common, 1),
        "det_yield_overall": o_matched / max(o_total, 1),
        "oracle_dets_total": o_total,
        "oracle_dets_on_processed": o_common,
        "policy_dets_matched": o_matched,
        "n_events": len(events),
        "event_recall": {},
        "onset_delays_ms": sorted(r["onset_delay_ms"] for r in ev_results
                                  if r["onset_delay_ms"] is not None),
    }
    out["tta_recall"] = {}
    for d in deltas_ms:
        rec = sum(1 for r in ev_results
                  if r["onset_delay_ms"] is not None and r["onset_delay_ms"] <= d)
        out["event_recall"][f"@{d:g}ms"] = rec / max(len(events), 1)
        # Time-to-awareness: the operator learns of the event only when the
        # detection is EMITTED, mean_e2e after the frame's capture.
        rec_tta = sum(1 for r in ev_results
                      if r["onset_delay_ms"] is not None
                      and r["onset_delay_ms"] + mean_e2e <= d)
        out["tta_recall"][f"@{d:g}ms"] = rec_tta / max(len(events), 1)
    return out, ev_results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--oracle-conf", type=float, default=0.40)
    ap.add_argument("--iou-track", type=float, default=0.30)
    ap.add_argument("--iou-det", type=float, default=0.30)
    ap.add_argument("--min-frames", type=int, default=3)
    ap.add_argument("--deltas", type=float, nargs="+",
                    default=[100, 250, 500, 1000])
    ap.add_argument("--out", default=None)
    ap.add_argument("--events-out", default=None,
                    help="also dump per-event results per run (JSONL)")
    args = ap.parse_args()

    oracle_recs = load_run_dets(args.oracle)
    frames, events = build_oracle(oracle_recs, args.oracle_conf,
                                  args.iou_track, args.min_frames)
    print(f"oracle: {len(frames)} frames, "
          f"{sum(len(v) for v in frames.values())} detections, "
          f"{len(events)} events (>= {args.min_frames} frames)",
          file=sys.stderr)

    results = []
    for run in args.run:
        res, ev = evaluate(run, frames, events, args.deltas, args.iou_det)
        results.append(res)
        print(f"  {os.path.basename(run)}: cov {res['coverage_vs_oracle']:.3f} "
              f"det_yield {res['det_yield_overall']:.3f} "
              f"ev@500 {res['event_recall'].get('@500ms', 0):.3f}",
              file=sys.stderr)
        if args.events_out:
            with open(args.events_out, "a") as f:
                for e in ev:
                    f.write(json.dumps({"run": run, **e}) + "\n")

    payload = json.dumps({"oracle": args.oracle, "n_events": len(events),
                          "results": results}, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(payload)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
