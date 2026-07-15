#!/usr/bin/env python3
"""run_eval.py — VISTA policy-evaluation campaign driver.

Runs the experiment application (VISTA_BIN) over deterministic skewed replay
and collects, per run: a metrics CSV, a detections JSONL keyed by
(camera_id, buf_pts), the scheduler decision CSV, stderr, and a run_meta.json
recording exactly what produced them. Repeats are separate invocations of an
identical command.

Arms (--arm, repeatable; default: the full policy set). PAPER NAME <- arm:
  fifo33      Stock-Default. Scheduler off, 33.3 ms push deadline.
  fifo5       diagnostic: scheduler off, 5 ms push deadline.
  dropold     diagnostic: keep-newest config queue (gate G4 arm).
  fresh-k4    all-admit ABLATION (K=4 = admit everything; not a VISTA arm).
  fresh-k2    VISTA-Fresh   — the paper's default operating point.
  imp-k2      VISTA-Activity — the optional activity-aware extension.
  salv-k2     salvage mode. NOT evaluated in the paper.
  ref         completeness reference: ring=0, run to EOS, no --duration. Every
              surviving frame is processed; this is the oracle's input.

Examples:
  python3 run_eval.py --pgie config/pgie_yolo11m.txt --model-tag m \\
      --repeats 5 --duration 50 --out "$VISTA_RESULTS/e3_m"
  python3 run_eval.py --pgie config/pgie_yolo11x.txt --model-tag x \\
      --arm ref --out "$VISTA_RESULTS/oracle_x"

PROVENANCE (the reason this file was changed)
The original recorded only `git rev-parse HEAD`. That is not the identity of
what ran: if the working tree is dirty, HEAD's SHA describes code that was
never executed. This is not hypothetical here — three archived campaigns record
a SHA that provably cannot produce them, because the flag they pass
(--sched-stash) exists in no commit of the application's history. The metadata
looked authoritative and was wrong.

So this version records, per run:
  * app_sha256      — the hash of the binary that actually ran. This is the
                      only unambiguous identity, dirty tree or not.
  * git.describe    — `git describe --always --dirty`; the -dirty suffix is the
                      part that matters.
  * git.dirty_files — WHICH files were uncommitted, so a reader can judge.
It still records git.sha, but as one weak signal among several rather than as
the answer. Binary hashing is lifted from weightsweep's harness/run_plan.py,
which already did this correctly.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _env(name, default):
    v = os.environ.get(name)
    return v if v else default


# Paths come from the environment (see vista_env.sh), never from a literal.
# The defaults mirror vista_env.sh so this script is usable standalone.
REPO = os.path.abspath(_env("VISTA_ROOT", os.path.dirname(HERE)))
APP = _env("VISTA_BIN", os.path.join(REPO, "app", "vista_multicam"))
CONFIG_DIR = _env("VISTA_CONFIG", os.path.join(REPO, "config"))
CLIPS_DIR = _env("VISTA_CLIPS", os.path.join(os.path.dirname(HERE), "data", "clips"))
GPU_SYSFS = _env("GPU_SYSFS",
                 "/sys/devices/platform/17000000.gpu/devfreq/17000000.gpu")
GPU_EXPECT_HZ = int(_env("VISTA_GPU_EXPECT_HZ", "612000000"))

# The measured live reference (2026-07-07). See vista_env.sh for why gap=44 and
# not the ~70 the application's --help suggests.
DEF_SKEW = _env("VISTA_SKEW", "0,1134.8,1702.1,567.2")
DEF_RATE = _env("VISTA_RATE", "0.96063,0.96099,0.96087,0.96128")
DEF_GAP = int(_env("VISTA_GAP", "44"))
DEF_RING = int(_env("VISTA_RING", "4"))

# Arm -> application flags. These strings are the experiment: they are
# reproduced verbatim from the run_meta.json cmd arrays of the paper's runs,
# so a re-run issues the same command those runs did. (Those runs are not
# distributed here; verify_reconstruction.py checks this claim against them if
# you have them.)
ARMS = {
    "fifo33":   {"args": ["--timeout-us", "33333"], "push_us": 33333},
    "fifo5":    {"args": ["--timeout-us", "5000"], "push_us": 5000},
    "dropold":  {"args": ["--timeout-us", "33333", "--dropold"], "push_us": 33333},
    "fresh-k4": {"args": ["--sched", "fresh", "--sched-k", "4"], "sched": True},
    "fresh-k2": {"args": ["--sched", "fresh", "--sched-k", "2"], "sched": True},
    "imp-k2":   {"args": ["--sched", "imp", "--sched-k", "2"], "sched": True},
    "salv-k2":  {"args": ["--sched", "salvage", "--sched-k", "2"], "sched": True},
    "ref":      {"args": ["--timeout-us", "33333"], "push_us": 33333,
                 "ref": True},
}

DEFAULT_ARMS = ["fifo33", "fifo5", "dropold", "fresh-k4", "fresh-k2",
                "imp-k2", "salv-k2"]


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------

def gpu_clock():
    """Current GPU frequency in Hz, or None when there is no devfreq node
    (i.e. not a Jetson). None is a legitimate value, not an error: it records
    honestly that the clock could not be observed."""
    try:
        with open(os.path.join(GPU_SYSFS, "cur_freq")) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def gpu_lock_state():
    """(locked, detail). Locked means min==max==expected: a GPU merely idling
    at the expected frequency is NOT locked, it is one load spike away from
    boosting mid-run and silently changing every latency in the campaign."""
    vals = {}
    for k in ("cur_freq", "min_freq", "max_freq"):
        try:
            with open(os.path.join(GPU_SYSFS, k)) as f:
                vals[k] = int(f.read().strip())
        except (OSError, ValueError):
            return False, f"no readable GPU devfreq node at {GPU_SYSFS}"
    if vals["min_freq"] != vals["max_freq"]:
        return False, (f"GPU not locked: min_freq={vals['min_freq']} != "
                       f"max_freq={vals['max_freq']}")
    if vals["cur_freq"] != GPU_EXPECT_HZ:
        return False, (f"GPU locked at {vals['cur_freq']} Hz, expected "
                       f"{GPU_EXPECT_HZ} Hz")
    return True, f"GPU locked at {vals['cur_freq']} Hz"


def assert_gpu_lock(allow_unlocked):
    ok, detail = gpu_lock_state()
    if ok:
        return
    if allow_unlocked or os.environ.get("VISTA_ALLOW_UNLOCKED_GPU") == "1":
        print(f"[WARN] {detail}", file=sys.stderr)
        print("[WARN] continuing anyway — these numbers are NOT comparable "
              "to the paper.", file=sys.stderr)
        return
    sys.exit(f"[FATAL] {detail}\n"
             f"Every latency in the paper was measured with the GPU pinned to "
             f"{GPU_EXPECT_HZ} Hz (Jetson AGX Orin 64GB, MODE_30W).\n"
             f"Lock it:  sudo nvpmodel -m 2 && sudo jetson_clocks\n"
             f"Override: --allow-unlocked-gpu  (results not comparable)")


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def file_hash(path, full=False):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    d = h.hexdigest()
    return d if full else d[:16]


def _git(args, cwd, strip=True):
    """Read-only git. Returns stdout or None. Never writes: this harness must
    not mutate the repository it is describing.

    strip=False matters for `status --porcelain`, whose first two columns are
    significant and may be spaces (" M path"): stripping would shift the path."""
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip() if strip else p.stdout.rstrip("\n")


def _parse_porcelain(out):
    """Paths from `git status --porcelain` v1: 'XY path', X/Y possibly spaces.
    Renames appear as 'R  old -> new'; record the destination."""
    files = []
    for ln in out.split("\n"):
        if not ln.strip():
            continue
        path = ln[3:] if len(ln) > 3 else ln.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path.strip('"'))
    return files


def git_provenance(cwd):
    """What git can and cannot tell us about the code that produced a run.

    `describe --always --dirty` is the headline: the -dirty suffix is precisely
    the condition under which the SHA is a lie about what executed. dirty_files
    is recorded so a reader can judge whether the difference mattered, rather
    than having to trust that it did not."""
    if _git(["rev-parse", "--is-inside-work-tree"], cwd) != "true":
        return {"available": False,
                "reason": f"{cwd} is not a git work tree",
                "describe": None, "sha": None, "dirty": None,
                "dirty_files": []}
    porcelain = _git(["status", "--porcelain", "--untracked-files=no"], cwd,
                     strip=False) or ""
    dirty_files = _parse_porcelain(porcelain)
    return {
        "available": True,
        "describe": _git(["describe", "--always", "--dirty"], cwd),
        "sha": _git(["rev-parse", "HEAD"], cwd),
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd),
        # True means: HEAD does NOT describe what ran. Trust app_sha256 instead.
        "dirty": bool(dirty_files),
        "dirty_files": dirty_files[:50],
    }


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def resolve_pgie(pgie):
    """Accept an absolute path, a path relative to VISTA_ROOT (the archived
    form, e.g. 'config/pgie_yolo11m.txt'), or a bare name in VISTA_CONFIG."""
    for cand in (pgie,
                 os.path.join(REPO, pgie),
                 os.path.join(CONFIG_DIR, pgie),
                 os.path.join(CONFIG_DIR, os.path.basename(pgie))):
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    sys.exit(f"[FATAL] nvinfer config not found: {pgie}\n"
             f"  looked in VISTA_ROOT={REPO} and VISTA_CONFIG={CONFIG_DIR}")


def engine_path_from_pgie(pgie_abs):
    """model-engine-file is written relative to the config file's directory."""
    try:
        with open(pgie_abs) as f:
            txt = f.read()
    except OSError:
        return None
    m = re.search(r"^model-engine-file=(\S+)", txt, re.M)
    if not m:
        return None
    return os.path.normpath(os.path.join(os.path.dirname(pgie_abs), m.group(1)))


def make_push_ini(outdir, push_us):
    """FIFO arms: the push deadline must be delivered via a generated INI.

    On the NEW nvstreammux the batched-push-timeout PROPERTY is inert (measured
    2026-07-07: fill and batches/s identical from 1 to 100 ms). The knob it
    honours is the INI's overall-min-fps. --timeout-us is still passed so the
    metrics CSV records the intended value. See timeout_sweep_cpp.py."""
    min_fps = 1e6 / push_us
    max_fps_n = max(120, math.ceil(min_fps))
    path = os.path.join(outdir, f"mux_push_{push_us}us.txt")
    with open(path, "w") as f:
        f.write("# generated by run_eval.py\n[property]\nalgorithm-type=1\n"
                "max-fps-control=0\n"
                f"overall-max-fps-n={max_fps_n}\noverall-max-fps-d=1\n"
                "overall-min-fps-n=1000000\n"
                f"overall-min-fps-d={push_us}\n"
                "max-same-source-frames=1\n")
    return path


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def build_cmd(arm, args, rundir, pgie_abs, extra_args):
    is_ref = arm.get("ref", False)
    cmd = [APP,
           "--config", os.path.join(CONFIG_DIR, "camera_params.yaml"),
           "--source", "file", "--replay-dir", args.replay_dir,
           "--pgie-config", pgie_abs,
           "--skew-ms", args.skew, "--rate", args.rate,
           "--gap-every", str(args.gap_every),
           # ring=0 for the completeness reference: it must see every frame.
           "--ring", "0" if is_ref else str(args.ring),
           "--no-sync", "--log", "json",
           "--metrics-csv", os.path.join(rundir, "metrics.csv")]
    cmd += arm["args"]
    if arm.get("sched"):
        # The scheduler needs its own mux INI (batch-size=K, slow deadline) so
        # the K-frame burst forms exactly one batch. The app selects it.
        cmd += ["--sched-csv", os.path.join(rundir, "sched.csv")]
    elif "push_us" in arm:
        cmd += ["--mux-config", make_push_ini(rundir, arm["push_us"])]
    if not is_ref and args.duration > 0:
        cmd += ["--duration", str(args.duration)]
    cmd += extra_args
    return cmd


def run_one(arm_name, arm, args, rundir, pgie_abs, extra_args):
    os.makedirs(rundir, exist_ok=True)
    is_ref = arm.get("ref", False)
    cmd = build_cmd(arm, args, rundir, pgie_abs, extra_args)
    engine = engine_path_from_pgie(pgie_abs)

    meta = {
        "arm": arm_name,
        "cmd": cmd,
        "t_start": time.time(),
        "gpu_clock_hz": gpu_clock(),
        "gpu_locked": gpu_lock_state()[0],
        "pgie": pgie_abs,
        "model_tag": args.model_tag,
        "engine": engine,
        "engine_sha256_16": file_hash(engine) if engine else None,
        # --- what actually ran -------------------------------------------
        "app_path": APP,
        "app_sha256": file_hash(APP, full=True),
        "app_sha256_16": file_hash(APP),
        "app_mtime": (os.path.getmtime(APP) if os.path.exists(APP) else None),
        "git": git_provenance(REPO),
        "harness": os.path.basename(__file__),
        # -----------------------------------------------------------------
        "replay": {"dir": args.replay_dir, "skew_ms": args.skew,
                   "rate": args.rate, "gap_every": args.gap_every,
                   "ring": 0 if is_ref else args.ring},
        "duration_s": None if is_ref else args.duration,
    }
    meta_path = os.path.join(rundir, "run_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    t0 = time.time()
    with open(os.path.join(rundir, "dets.jsonl"), "w") as out, \
         open(os.path.join(rundir, "stderr.log"), "w") as err:
        try:
            proc = subprocess.run(cmd, cwd=REPO, stdout=out, stderr=err,
                                  timeout=args.run_timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            # Recorded, not swallowed: a timed-out run leaves a truncated CSV
            # that looks like a short run. rc=-1 marks it.
            rc = -1
            err.write(f"\n[run_eval] TIMEOUT after {args.run_timeout}s\n")
    meta["t_end"] = time.time()
    meta["wall_s"] = time.time() - t0
    meta["returncode"] = rc
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return rc, meta["wall_s"]


def quick_stats(rundir):
    """One console line per run, from metrics.csv."""
    import csv as csvmod
    import statistics
    p = os.path.join(rundir, "metrics.csv")
    if not os.path.exists(p):
        return "NO METRICS"
    with open(p) as f:
        rows = list(csvmod.DictReader(f))
    if not rows:
        return "0 batches"
    nin = [int(r["n_in_batch"]) for r in rows]
    # e2e_ms carries negative sentinels for frames with no valid stamp.
    e2e = [float(r["e2e_ms"]) for r in rows if float(r["e2e_ms"]) >= 0]
    arr = int(rows[-1]["arrivals_cum"])
    proc_n = sum(int(r["n_real"]) for r in rows)
    dur = float(rows[-1]["t_mono"]) - float(rows[0]["t_mono"])
    return (f"{len(rows)} batches, fill {statistics.mean(nin):.2f}, "
            f"{sum(nin)/max(dur,1e-6):.1f} f/s, "
            f"e2e {statistics.mean(e2e) if e2e else -1:.0f}ms "
            f"p99 {sorted(e2e)[int(0.99*(len(e2e)-1))] if e2e else -1:.0f}ms, "
            f"cov {proc_n/max(arr,1):.3f} ({proc_n}/{arr})")


def main():
    ap = argparse.ArgumentParser(
        description="VISTA policy-evaluation campaign driver.")
    ap.add_argument("--pgie", required=True,
                    help="nvinfer config: absolute, or relative to VISTA_ROOT "
                         "(e.g. config/pgie_yolo11m.txt)")
    ap.add_argument("--model-tag", required=True)
    ap.add_argument("--arm", action="append", default=None,
                    help=f"repeatable; default: {' '.join(DEFAULT_ARMS)}. "
                         f"One of {list(ARMS)}")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--duration", type=float, default=80.0)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--replay-dir",
                    default=os.path.join(CLIPS_DIR, "myclipsForEXP"))
    ap.add_argument("--skew", default=DEF_SKEW)
    ap.add_argument("--rate", default=DEF_RATE)
    ap.add_argument("--gap-every", type=int, default=DEF_GAP,
                    help="drop 2 frames every N per camera. 44 = live-rate "
                         "fidelity (default). 3 = the Static-Decimation "
                         "baseline. NOT ~70 (see vista_env.sh).")
    ap.add_argument("--ring", type=int, default=DEF_RING)
    ap.add_argument("--run-timeout", type=float, default=1200.0)
    ap.add_argument("--extra", default="",
                    help="extra CLI args appended verbatim (space-separated)")
    ap.add_argument("--allow-unlocked-gpu", action="store_true",
                    help="proceed with an unpinned GPU clock. Results will "
                         "not be comparable to the paper.")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip runs whose metrics.csv already exists "
                         "(idempotent campaign resume)")
    args = ap.parse_args()

    if not os.path.isfile(APP) or not os.access(APP, os.X_OK):
        sys.exit(f"[FATAL] application not found or not executable: {APP}\n"
                 f"  build it: make -C {os.path.dirname(APP)}\n"
                 f"  or set VISTA_BIN")
    if not os.path.isdir(args.replay_dir):
        sys.exit(f"[FATAL] replay clips not found: {args.replay_dir}\n"
                 f"  The clips are not shipped (privacy: identifiable person; "
                 f"see vista_env.sh).\n  Set VISTA_CLIPS or --replay-dir.")
    assert_gpu_lock(args.allow_unlocked_gpu)

    pgie_abs = resolve_pgie(args.pgie)
    arms = args.arm or DEFAULT_ARMS
    for a in arms:
        if a not in ARMS:
            sys.exit(f"[FATAL] unknown arm '{a}'. Known: {list(ARMS)}")
    extra = args.extra.split() if args.extra else []
    outroot = os.path.abspath(args.out)
    os.makedirs(outroot, exist_ok=True)

    log_lines = []
    for arm_name in arms:
        arm = ARMS[arm_name]
        n_rep = 1 if arm.get("ref") else args.repeats
        for r in range(n_rep):
            rundir = os.path.join(outroot, f"{arm_name}_r{r}")
            if args.skip_existing and os.path.exists(
                    os.path.join(rundir, "metrics.csv")):
                print(f"[{args.model_tag}] {arm_name} r{r} — exists, skip",
                      flush=True)
                continue
            print(f"[{args.model_tag}] {arm_name} r{r} ...", flush=True)
            rc, wall = run_one(arm_name, arm, args, rundir, pgie_abs, extra)
            line = (f"{args.model_tag} {arm_name} r{r} rc={rc} "
                    f"wall={wall:.0f}s  {quick_stats(rundir)}")
            print("   " + line, flush=True)
            log_lines.append(line)
            if rc != 0:
                print(f"   [WARN] non-zero return code {rc} — see "
                      f"{rundir}/stderr.log", flush=True)

    if log_lines:
        with open(os.path.join(outroot, "CAMPAIGN_LOG.txt"), "a") as f:
            f.write("\n".join(log_lines) + "\n")
    print("done ->", outroot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
