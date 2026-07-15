#!/usr/bin/env python3
"""clean_events.py — event-quality-stratified TTA recall + ref_m yield decomposition.

Addresses the oracle-stats audit:
(1) Event composition: of 123 oracle events, flag (a) office-implausible
    classes (YOLO flicker), (b) 'reincarnations' (same camera+class IoU>=0.3
    against an earlier expired event's last box — static-object confidence
    flicker). Report TTA recall for ALL / CLEAN / PERSON-ONLY event sets.
(2) Yield decomposition: recompute detection metrics against the SAME-MODEL
    completeness reference (ref_m) to separate scheduling loss from detector
    capability.

=============================================================================
THE "63 CLEAN EVENTS" ARE CLASSIFIED AUTOMATICALLY, NOT MANUALLY.
=============================================================================
This script is where the paper's clean-event count comes from, and it reaches
63 with no human in the loop: 100% of the 123 events are classified by the
two rules below — a 16-class implausibility blocklist and an IoU>=0.30
reincarnation heuristic. Nothing here inspects a frame, and no reviewer
decision is recorded anywhere in the archive. Any description of these as
"manually verified" is not supported by this code. The rules are defensible
and disclosed; the provenance adjective is the problem. See the claim matrix.

The blocklist is also workload-specific by construction (it encodes "a
surfboard in an office is a YOLO flicker"), so it does not transfer to
another deployment unedited.
"""
import json
import os
import sys
import statistics as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns
from _paths import DATA_ROOT, data, derived, ensure_derived
from match_events import build_oracle, evaluate, iou, load_run_dets

# Classes taken to be impossible in this office scene, i.e. detector flicker.
# Workload-specific: this is a judgement about the scene, not about YOLO.
IMPLAUSIBLE = {"tennis racket", "hair drier", "suitcase", "scissors",
               "toothbrush", "tie", "bicycle", "knife", "surfboard",
               "skateboard", "frisbee", "baseball bat", "boat", "bird",
               "umbrella", "handbag"}

# Arm label -> (campaigns.yaml table, paper_name). Run paths live in
# campaigns.yaml and nowhere else; these are only the display names this
# script has always used.
ARM_SOURCES = [
    ("FIFO-s20", "table2", "Stock-Default"),
    ("FIFO-s2", "table2", "Stock-LiveDepth"),
    ("FRESH-K2", "table2", "VISTA-Fresh"),
    ("IMP-fixed", "table2", "VISTA-Activity"),
    ("SALV-fixed", None, "(diagnostic) SALV-K2"),
]


def resolve_arms():
    """{arm label -> [run dir]} resolved through campaigns.yaml."""
    out = {}
    for label, table, paper_name in ARM_SOURCES:
        if table:
            rows = _campaigns.rows(table, paper_name=paper_name)
        else:
            rows = [d for d in _campaigns.diagnostics()
                    if d["paper_name"] == paper_name]
        if not rows:
            raise KeyError(f"campaigns.yaml has no row named {paper_name!r}")
        out[label] = _campaigns.run_dirs(rows[0])
    return out


def classify_events(events):
    """Tag each event: implausible-class / reincarnation / clean."""
    expired = []  # (cam, cls, last_box, last_pts)
    tags = []
    for e in sorted(events, key=lambda x: x["t0"]):
        tag = "clean"
        if e["cls"] in IMPLAUSIBLE:
            tag = "implausible"
        else:
            for (cam, cls, box, lp) in expired:
                if cam == e["cam"] and cls == e["cls"] and e["t0"] > lp \
                        and iou(box, e["last_box"]) >= 0.3:
                    tag = "reincarnation"
                    break
        tags.append((e, tag))
        expired.append((e["cam"], e["cls"], e["last_box"], e["last_pts"]))
    return tags


def main():
    oracle_dir, ometa = _campaigns.oracle("office_x")
    frames, events = build_oracle(load_run_dets(oracle_dir), ometa["conf"],
                                  0.30, 3)
    tags = classify_events(events)
    from collections import Counter
    comp = Counter(t for _, t in tags)
    print(f"event composition: {dict(comp)} of {len(events)}")

    sets = {
        "all": events,
        "clean": [e for e, t in tags if t == "clean"],
        "person": [e for e in events if e["cls"] == "person"],
    }
    for name, evs in sets.items():
        print(f"  {name}: {len(evs)} events")

    runs = resolve_arms()
    deltas = [500, 1000, 2000]
    out = {}
    for arm, rlist in runs.items():
        rows = {}
        for name, evs in sets.items():
            vals = {d: [] for d in deltas}
            for d_ in rlist:
                res, _ = evaluate(d_, frames, evs, deltas, 0.30)
                for d in deltas:
                    vals[d].append(res["tta_recall"][f"@{d:g}ms"])
            rows[name] = {d: st.median(v) for d, v in vals.items() if v}
        out[arm] = rows
        line = f"{arm:11s}"
        for name in sets:
            r = rows.get(name, {})
            line += "  " + name + ":" + "/".join(
                f"{r.get(d, float('nan')):.3f}" for d in deltas)
        print(line)

    # ---- ref_m yield decomposition (m arms, all frames/detections) ----
    print("\nref_m (same-model completeness) decomposition:")
    ref_dir, rmeta = _campaigns.oracle("ref_m")
    ref_frames, _ = build_oracle(load_run_dets(ref_dir), rmeta["conf"], 0.30, 3)
    # First two repeats only: this decomposition is a diagnostic, and the
    # oracle rebuild dominates its runtime.
    for arm in ("FIFO-s20", "FIFO-s2", "FRESH-K2", "IMP-fixed"):
        ys, prs = [], []
        for d_ in runs[arm][:2]:
            res, _ = evaluate(d_, ref_frames, [], [500], 0.30)
            ys.append(res["det_yield_overall"])
            prs.append(res["det_recall_processed_frames"])
        if ys:
            print(f"  {arm:11s} yield-vs-ref_m {st.median(ys):.3f}  "
                  f"per-frame-recall-on-processed {st.median(prs):.3f}")
    ensure_derived()
    with open(derived("clean_events.json"), "w") as f:
        json.dump({"composition": dict(comp), "tta": out}, f, indent=2)


if __name__ == "__main__":
    main()
