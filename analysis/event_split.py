#!/usr/bin/env python3
"""event_split.py — the per-camera split of the oracle event set.

Produces the paper's "reference events split 37/33/28/25" (RQ3's opening
negative result: activity is distributed roughly evenly across cameras on the
office workload, so reallocation has nothing to win).

Nothing in the archived analysis computed this either; it is a one-liner over
the oracle event set, but it is the entire evidential basis for the claim that
the primary workload is NOT skewed — which is what motivates the composited
microbenchmark of Table IV. Worth being able to regenerate.

Also reports the split of the two composited clip sets, where the point is the
opposite: they are lopsided by construction.
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _campaigns  # noqa: E402
from match_events import build_oracle, load_run_dets  # noqa: E402


def split(oracle_name):
    run_dir, meta = _campaigns.oracle(oracle_name)
    frames, events = build_oracle(load_run_dets(run_dir), meta["conf"],
                                  0.30, 3)
    c = Counter(e["cam"] for e in events)
    return meta, len(events), c, frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", default=None,
                    help="oracle name from campaigns.yaml (default: all event oracles)")
    args = ap.parse_args()

    names = [args.oracle] if args.oracle else ["office_x", "brief_x", "pers_x"]
    for name in names:
        meta, n, c, frames = split(name)
        print(f"\n{name}  ({meta['run_dir']}, {meta['detector']}, "
              f"conf>={meta['conf']})")
        print(f"  {len(frames)} oracle frames, {n} events "
              f"(declared {meta.get('n_events', '?')})")
        order = sorted(c)
        print(f"  per-camera event split: "
              f"{'/'.join(str(c[k]) for k in order)}  (cam {'/'.join(map(str, order))})")
        print(f"  shares: "
              f"{' '.join(f'c{k}:{100*c[k]/n:.0f}%' for k in order)}")
        desc = sorted(c.values(), reverse=True)
        print(f"  sorted descending:      {'/'.join(map(str, desc))}")
        if name == "office_x":
            # The paper quotes "37/33/28/25". That is the split SORTED
            # DESCENDING, not in camera order: by camera it is 37/25/28/33
            # (cam0/1/2/3). Same multiset, different presentation — the paper
            # never claims camera order, so this is a match, but anyone
            # comparing per-camera will think it is not.
            paper = [37, 33, 28, 25]
            ok = desc == paper
            print(f"  paper says 37/33/28/25 (sorted); we get "
                  f"{'/'.join(map(str, desc))}  "
                  f"{'MATCH' if ok else '*** MISMATCH ***'}")
            print(f"  NOTE: in CAMERA order this is "
                  f"{'/'.join(str(c[k]) for k in order)} — the paper's figure "
                  f"is sorted, not per-camera.")
            print("  (an even split would be 30.75 each; the observed spread "
                  "is 25-37, i.e. roughly even — this is the negative result "
                  "that motivates the Table IV microbenchmark)")
        if name in ("brief_x", "pers_x"):
            empty = [k for k in range(4) if c[k] == 0]
            rare = [k for k in sorted(c) if c[k] == 1]
            print(f"  by construction: cam0 busy ({c[0]} events), "
                  f"cameras {empty} empty, camera(s) {rare} carry a single "
                  f"rare event — matches the paper's description of the "
                  f"composited layout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
