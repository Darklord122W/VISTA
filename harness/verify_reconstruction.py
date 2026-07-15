#!/usr/bin/env python3
"""verify_reconstruction.py — prove this harness still rebuilds the commands
that produced a set of runs.

    VISTA_DATA_ROOT=/path/to/runs python3 harness/verify_reconstruction.py
                                                      # 29 cases, ~1 s, no GPU

WHY THIS EXISTS. Several of the paper's campaigns have no surviving script;
they were reconstructed from the `cmd` arrays inside their own run_meta.json.
That reconstruction is an ASSERTION about history, and assertions rot: a
plausible-looking edit to an arm's flags, a "tidied" duration, a changed default
in vista_env.sh, and the harness quietly stops reproducing the experiment while
every script still runs and still produces data.

So the runs themselves are the fixture: each carries the argv that produced it.
For each case below, run_eval.py builds the command it WOULD issue today, and it
is compared token-for-token against the command that actually ran. This needs no
GPU, no clips and no engines — only each run's run_meta.json — so it is cheap
enough to run on every change.

IT NEEDS THE RUNS, and this repository ships none: point $VISTA_DATA_ROOT at the
campaigns these cases name. The authors' archive is what the 29 cases were
written against; against any other tree, expect SKIPs for cases you did not run.
Verifying nothing is reported as a failure, not as "0 differ".

Comparison is exact except for two deliberate normalizations:
  * absolute paths are reduced to basenames. The original runs used cwd=<repo>
    and relative paths ("config/pgie_yolo11m.txt"); this harness passes absolute
    paths so it does not depend on cwd. Same file, different spelling.
  * the leading argv[0] (the binary) is dropped: the paper's runs invoked
    cpp/multicam_rt, this repo ships app/vista_multicam.

A FAILURE HERE IS NOT A STYLE COMPLAINT. It means the harness no longer
reproduces the paper's experiment, and either the change or this file is wrong.
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

def require_data_root():
    """The archive is the fixture, and it does not ship. One message, exit 2.

    Same wording as analysis/_paths.py and vista_env.sh's
    vista_require_data_root; keep the three in step.
    """
    v = os.environ.get("VISTA_DATA_ROOT")
    if not v:
        reason = "VISTA_DATA_ROOT is not set."
    else:
        root = os.path.abspath(os.path.expanduser(v))
        if os.path.isdir(root):
            return root
        reason = "VISTA_DATA_ROOT=%s is not a directory." % root
    sys.stderr.write(
        "vista: " + reason + "\n"
        "  This check compares against the run_meta.json of the runs it\n"
        "  reconstructs, and the measurement archive is not distributed with\n"
        "  this repository: this artifact ships code, not runs.\n"
        "  Set VISTA_DATA_ROOT to a directory of campaign run directories,\n"
        "  or produce one on a Jetson with harness/run_campaign.sh\n"
        "  (see harness/README.md).\n")
    sys.exit(2)


# Any path works: build_cmd only string-formats it. Nothing is executed and no
# clip is read, so the private clips need not exist.
CLIPS = os.environ.get("VISTA_CLIPS", "/clips")


def load_run_eval():
    spec = importlib.util.spec_from_file_location(
        "run_eval", os.path.join(HERE, "run_eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def norm(cmd):
    """Basename any path so absolute (ours) and repo-relative (archived)
    spellings of the same file compare equal."""
    return [os.path.basename(t) if t.startswith(("/", "config/")) else t
            for t in cmd]


# (archived run dir, arm, duration, clip set, kwargs)
# Duration/gap/skew/extra are the archived values; that is the whole point.
CASES = [
    # --- e3: the policy campaign (Tables II/III) --------------------------
    ("e3_m/fifo33_r0",        "fifo33",   50.0, "myclipsForEXP", {}),
    ("e3_m/fifo5_r0",         "fifo5",    50.0, "myclipsForEXP", {}),
    ("e3_m/dropold_r0",       "dropold",  50.0, "myclipsForEXP", {}),
    ("e3_m/fresh-k2_r0",      "fresh-k2", 50.0, "myclipsForEXP", {}),
    ("e3_m/fresh-k4_r0",      "fresh-k4", 50.0, "myclipsForEXP", {}),
    ("e3_m/imp-k2_r0",        "imp-k2",   50.0, "myclipsForEXP", {}),
    ("e3_m/salv-k2_r0",       "salv-k2",  50.0, "myclipsForEXP", {}),
    # --- oracles + completeness refs (ring=0, no --duration) --------------
    ("oracle_x/ref_r0",       "ref", 0, "myclipsForEXP",
     {"pgie": "config/pgie_yolo11x.txt"}),
    ("ref_m/ref_r0",          "ref", 0, "myclipsForEXP", {}),
    ("charBrief_x/ref_r0",    "ref", 0, "clips_importance_brief",
     {"pgie": "config/pgie_yolo11x.txt"}),
    ("charImp_x/ref_r0",      "ref", 0, "clips_importance",
     {"pgie": "config/pgie_yolo11x.txt"}),
    # --- baselines (Table II rows 2,3) ------------------------------------
    # THE TRAP: gap-every 3 = DEC-1/3 = Static-Decimation;
    #           gap-every 4 = DEC-1/2 = a diagnostic. See run_baselines.sh.
    ("e3_m_decimate3_r0/fifo33_r0", "fifo33", 52.0, "myclipsForEXP",
     {"gap": 3}),
    ("e3_m_decimate_r0/fifo33_r0",  "fifo33", 52.0, "myclipsForEXP",
     {"gap": 4}),
    ("e7_surfcal_2/fifo33_r0", "fifo33", 52.0, "myclipsForEXP",
     {"extra": "--replay-surfaces 2"}),
    ("e7_surfcal_4/fifo33_r0", "fifo33", 52.0, "myclipsForEXP",
     {"extra": "--replay-surfaces 4"}),
    # --- e8: post-importance-bugfix (Table II row 5) ----------------------
    ("e8_impfix_r0/imp-k2_r0",  "imp-k2",  52.0, "myclipsForEXP", {}),
    ("e8_salvfix_r0/salv-k2_r0", "salv-k2", 52.0, "myclipsForEXP", {}),
    # --- skew study (Table IV) --------------------------------------------
    ("briefD2ctl_imp-k2/imp-k2_r0", "imp-k2", 42.0, "clips_importance_brief",
     {}),
    ("briefS2_imp-k2/imp-k2_r0", "imp-k2", 42.0, "clips_importance_brief",
     {"extra": "--sched-stash 2"}),
    ("persS2_fresh-k2/fresh-k2_r0", "fresh-k2", 42.0, "clips_importance",
     {"extra": "--sched-stash 2"}),
    ("impcmp_imp-k2_r0/imp-k2_r0", "imp-k2", 42.0, "clips_importance", {}),
    # --- ablations / diagnostics ------------------------------------------
    ("e9_depth1/fresh-k2_r0", "fresh-k2", 60.0, "myclipsForEXP",
     {"extra": "--sched-depth 1"}),
    ("e9_depth2/fresh-k2_r0", "fresh-k2", 60.0, "myclipsForEXP", {}),
    ("brief_imp-k2_r0/imp-k2_r0", "imp-k2", 42.0, "clips_importance_brief",
     {"extra": "--sched-depth 1"}),
    ("impdiag_d1/imp-k2_r0", "imp-k2", 42.0, "clips_importance",
     {"extra": "--sched-depth 1 --sched-w 0.3,0.5,0.2"}),
    ("impdiag_heavy/imp-k2_r0", "imp-k2", 42.0, "clips_importance",
     {"extra": "--sched-w 0.02,0.96,0.02"}),
    ("enriched_m_salv/salv-k2_r0", "salv-k2", 52.0, "myclipsForEXP", {}),
    # --- E6 offset sweep: the scaled skews run_campaign.sh computes --------
    ("e6_off0.33_sparq_r0/imp-k2_r0", "imp-k2", 50.0, "myclipsForEXP",
     {"skew": "0.0,374.5,561.7,187.2"}),
    ("e6_off1.0_syncbroken_r0/fifo33_r0", "fifo33", 50.0, "myclipsForEXP",
     {"skew": "0.0,1134.8,1702.1,567.2",
      "extra": "--sync --max-latency-ms 33.333 --no-pts-fix --restamp"}),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print the full command for every case")
    args = ap.parse_args()

    archive = require_data_root()
    m = load_run_eval()

    ok = bad = skipped = 0
    for rel, arm, duration, clipset, kw in CASES:
        meta_p = os.path.join(archive, rel, "run_meta.json")
        if not os.path.exists(meta_p):
            print(f"SKIP   {rel} (no archived run_meta.json)")
            skipped += 1
            continue
        with open(meta_p) as f:
            archived = norm(json.load(f)["cmd"])[1:]

        ns = argparse.Namespace(
            replay_dir=os.path.join(CLIPS, clipset),
            skew=kw.get("skew", m.DEF_SKEW), rate=m.DEF_RATE,
            gap_every=kw.get("gap", m.DEF_GAP), ring=kw.get("ring", m.DEF_RING),
            duration=duration, model_tag="m")
        pgie = kw.get("pgie", "config/pgie_yolo11m.txt")
        extra = kw.get("extra", "")
        # A temp rundir: build_cmd only needs somewhere to name metrics.csv,
        # sched.csv and (for FIFO arms) the generated mux INI.
        with tempfile.TemporaryDirectory() as td:
            got = norm(m.build_cmd(m.ARMS[arm], ns, td, os.path.abspath(pgie),
                                   extra.split() if extra else []))[1:]

        if got == archived:
            ok += 1
            print(f"MATCH  {rel}")
            if args.verbose:
                print("       " + " ".join(got))
        else:
            bad += 1
            print(f"DIFF   {rel}")
            sa, sg = set(archived), set(got)
            print(f"       only in archive: {sorted(sa - sg)}")
            print(f"       only in harness: {sorted(sg - sa)}")

    print(f"\nRESULT: {ok} match, {bad} differ, {skipped} skipped "
          f"(of {len(CASES)})")
    if bad:
        print("The harness no longer reproduces the archived commands. Either\n"
              "the change is wrong, or this fixture needs updating — decide\n"
              "deliberately, and say which in the commit message.")
        return 1
    if not ok:
        # Verifying nothing is not passing. Every case skipping means the root
        # holds no run_meta.json for any of them -- a check that reports "0
        # differ" over an empty fixture is worse than one that fails.
        print(f"Nothing was verified: no case in {archive} carried a\n"
              "run_meta.json. VISTA_DATA_ROOT must name the directory of\n"
              "campaign run directories these cases live in.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
