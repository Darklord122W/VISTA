# Running the experiments

**The paper's measurement archive is not distributed in this repository.** No
run directories, no scored aggregates, no figures of them. What ships is the
scheduler, the reference application, the harness that drives the campaigns, and
the analysis code that scores them. The consequence is worth stating plainly:
**the numbers printed in the draft cannot be regenerated from this repository
alone.** There is no tier of this page that ends with the paper's tables.

What you can do is take your own measurements — build the pipeline, supply your
own footage, run the campaign, assert the gates, and score the result with the
same code. That is what this page is for.

---

## Read this first: what your numbers will be

**They will not be the paper's numbers, and that is not a failure.** Coverage,
detection yield and every event-recall figure are scored against an oracle built
from *your* clips. The event set is a property of the footage. Expect the
ordering and the ratios to hold — bounded output age independent of pool depth,
coverage traded for freshness, a drop ledger that closes. Do not expect the
paper's milliseconds.

**Nothing here reproduces bit-exactly**, on any hardware, including the original
rig:

* **TensorRT tactics are nondeterministic.** An engine is built by timing
  candidate kernels on the actual device and keeping the fastest. Timing has
  noise, so two builds of the same ONNX on the same board can select different
  kernels and produce slightly different detections.
* **The campaign has a documented ±9% throughput bistability.** Runs settle into
  one of two rhythm attractors as the release rhythm locks against the arrival
  phase; basin membership is metastable, and identical settings have been
  observed to split across repeats. Coverage moves with the basin, and yield and
  recall move with coverage. Median-of-3 absorbs most of it; an arm whose repeats
  all draw one basin carries the full offset.
* **Which frames drop is thread-timing dependent.** Two repeats of one arm share
  only a fraction of their processed frames.

So compare **ranges across repeats**, not single runs. Run at least three.

**Replay latencies are not live latencies.** Decoding four H.264 streams inflates
compute roughly 3x relative to live MJPG capture, so a replay campaign's absolute
latencies are systematically higher than the same policy's on physical cameras.
Ratios transfer between the two; absolute milliseconds do not. The two modes
answer different questions: replay gives frame-for-frame comparability across
policies (impossible live, because the drop decision changes which frames exist
downstream); live gives transfer.

---

## 1. What you need

| | |
|---|---|
| board | Jetson AGX Orin (or Jetson-class), `nvpmodel -m 2` (30 W) + `jetson_clocks` → GPU **612 MHz** |
| software | DeepStream 7.1 with the new `nvstreammux`, JetPack 6.2, CUDA 12.6, TensorRT 10.3, g++ 11.4 |
| models | YOLO11 FP16 TensorRT engines, built locally — none ship |
| input | 4 camera streams: either recorded clips for replay, or 4x USB cameras |
| analysis | Python 3.8+, `pyyaml`, `matplotlib` |

Exact versions and the build recipes: [`../usage/01-build.md`](../usage/01-build.md).

**Pin the GPU clock before anything.** An unpinned Orin scales its clock with
load, and the scheduler *reduces* load — so an unpinned board clocks down under
VISTA and up under the stock pipeline, silently flattering the baseline. Every
service time, every `rho`, every throughput comparison assumes a fixed clock. The
harness asserts `min_freq == max_freq == 612000000` rather than trusting
`cur_freq`, because a GPU merely idling at 612 MHz is one load spike away from
boosting mid-run.

```bash
sudo nvpmodel -m 2 && sudo jetson_clocks
```

## 2. Build

```bash
make lib app          # vista/libvista.a and app/vista_multicam
```

[`../usage/01-build.md`](../usage/01-build.md) covers `DS_ROOT`, the rpath, and
the build-time gotchas.

## 3. Models and engines

Weights and engines do not ship: weights are large, and a TensorRT engine is
specific to the exact GPU, driver, TensorRT and DeepStream version that built it,
so a shipped engine would be silently wrong elsewhere.

```bash
scripts/fetch_models.sh          # ONNX exports
scripts/build_engines.sh         # FP16, dynamic batch 1-4
```

**Budget an hour before the first campaign starts.** The YOLO11x oracle engine
alone measured **~37 min** on the reference rig; the four evaluation engines
(n/s/m/l) take several minutes each. `run_campaign.sh` builds them up front
rather than at minute 58, and validates every model name before starting.

> **The oracle engine is load-bearing.** It is the reference detector that
> defines the event ground truth every coverage, yield and recall number is
> scored against. An earlier `run_campaign.sh` wrapped its build in
> `timeout 3600` and fell back to YOLO11l with only a `WARNING` — quietly
> changing the ground truth while still producing plausible tables. **The
> fallback is now fatal and there is no YOLO11l path.** If your oracle build
> fails, the campaign stops.

Details: [`../usage/02-models-and-engines.md`](../usage/02-models-and-engines.md).

## 4. Clips

The paper's replay footage does not ship. Use your own: four synchronized
~60 s clips, H.264 in MP4, one per camera.

```bash
scripts/record_replay_clips.py       # record a set from physical cameras
export VISTA_CLIPS=/path/to/clips    # the harness reads this
```

Any 4-camera footage works, and the mechanism will reproduce on it. The oracle
event set is clip-specific, so your numbers are yours.

### The injection recipe — why a naive replay measures nothing

A recorded clip does not behave like a camera unless you make it. `filesrc` at
its natural rate reproduces **none** of the pathology: zero intra-batch spread,
flat staleness, no drops — a perfectly clean, perfectly wrong world in which the
problem does not exist. The reference application injects the measured live
imperfections back in:

| flag | reproduces | without it |
|---|---|---|
| `--skew-ms 0,1134.8,1702.1,567.2` | USB enumeration stagger (measured 1.05–1.70 s between cameras) | all four cameras start together: no staleness ladder, no realistic batching phase |
| `--rate ≈0.961` (per camera) | the cameras' true ~32.0 ms frame period, not the nominal 33.3 | wrong arrival rate, hence wrong `rho` |
| `--gap-every 44` | the measured ~29.8 fps delivered rate | over-delivery |
| `--ring 4` | the kernel capture ring's **drop-newest** behaviour | the pacer blocks instead of dropping — the silent-drop point disappears, i.e. the thing being studied |
| `--replay-surfaces 20` | the standing-queue depth (`2` = live depth) | the decoder pool, not the ring, becomes the throttle: the pacer starves and the replay measures its own decoder |

Arrival load is then `4 x (30/0.961) x (44-2)/44 = 119.2 f/s`, which against a
measured YOLO11m batch service time of ~62 ms gives the paper's primary
operating point, `rho = 1.86`.

**Give the four cameras slightly different rates** (the campaign uses
`0.96063, 0.96099, 0.96087, 0.96128`, not one shared value). With identical
rates the injected phases are constant, set by `skew mod 32 ms`; live phases
drift through all values.

> **`--gap-every N` drops two consecutive frames of every N**, keeping
> `(N-2)/N`. It is overloaded and this is the sharpest edge in the harness:
> **44 is timing fidelity**, while **3** and **4** are the *decimation
> baselines* (keeping 1/3 and 1/2). The application's own `--help` says
> "measured live: ~70". **Do not use 70.** At 70/275 the emulated grid drifts
> the wrong way against real time, frames look future-stamped, nothing is ever
> late, and sync-on trivially "succeeds" — a pure artifact.

### Re-deriving the parameters for your own rig

They are measurements, not magic constants. From a live run with capture
instrumentation:

```python
import pandas as pd
cap = pd.read_csv("runs/<live_run>/capture.csv")

first = cap.groupby("cam").pts_ns.min() / 1e6
print("skew-ms:", (first - first.min()).round(1).to_dict())

per = cap.sort_values("pts_ns").groupby("cam").pts_ns.diff().median() / 1e6
print("rate:", round(per / (1000/30), 4))     # true period / clip period
```

Derive `gap-every` from the **delivered** rate (frames actually seen ÷ elapsed),
not from the modal step: `--rate` already reproduces the modal cadence, and
deriving the gap from it again over-delivers.

## 5. The gates — run these first

The gates answer "is the scheduler **correct**?" separately from "is it
**good**?", and they are asserted as code rather than read off a table by a
human. Run them before you trust any policy number.

```bash
harness/run_gates.sh                                  # ~3 min: 5 runs, then assert
harness/run_gates.sh --analyze-only $VISTA_RESULTS/gates   # re-assert, no GPU
```

Every gate run is 25 s on YOLO11n — i.e. `rho = 0.84`, *below* capacity. That
looks backwards, and is deliberate: these are **invariants, not behaviours**.
Batch atomicity and ledger closure must hold trivially when nothing is stressed.
If they fail at `rho = 0.84` they are broken, not merely stressed — and a gate
that only fails under load is a gate that will be argued with.

The script exits non-zero on failure. That is the entire reason it exists: the
original printed a table and a sentence like *"G1 PASS if fill dist for sched
runs is a spike at K"*, ending in a heredoc that always exited 0 — so
`run_gates.sh && run_campaign.sh` would proceed cheerfully over a failed gate.

### G0 — environment

The gate before the gates, asserted rather than assumed because both failure
modes are silent: the binary exists and is the one about to run; **the GPU clock
is pinned**; the clips are present and complete (a missing camera changes `N`,
hence `D_fair = 2*(N/K)*s_hat`, hence the fairness floor itself); the pgie config
and its engine exist (nvinfer will otherwise spend ~37 min building one
mid-gate).

### G1 — batch atomicity

**The claim:** a release of K frames arrives at the detector as **one batch of
exactly K**.

```python
pct = 100.0 * (batches with n_in_batch == K) / (total batches)
assert pct >= 98.0                       # ATOMICITY_MIN_PCT
```

**Why it matters more than it sounds.** If the mux INI is wrong this degrades
*silently*: batches merge to the source count (`adaptive-batching=1`) or split
into `1 + (K-1)` (deadline anchors too tight). The run still completes, still
writes plausible metrics, and is simply not measuring the policy you asked for.
Nothing errors. A genuine INI regression produces fills in the 40–70% range, not
99%, so the 98% threshold has real margin against a healthy run's single non-K
teardown batch.

The module also keeps this histogram **in-process** and warns after 20
completions if fewer than 90% of batches carry exactly K
(`vista/src/vista_scheduler.cpp`). That runtime check is not redundant: the mux's
`batch-size` *property* is unreliable at attach time, so batch atomicity can only
be settled by evidence, and the fill histogram is the evidence. See
[`../design/05-scheduler-internals.md`](../design/05-scheduler-internals.md).

### G2 — drop-ledger closure

**The paper's central accountability claim:**

```
arrivals == admitted_fresh + admitted_salvage + policy_drops
```

exactly, on every instrumented run — not approximately.

```python
# parsed from stderr.log's "[sched]"/"[vista]" summary line
assert fresh + salvage + drops == arrivals      # delta must be 0, not "small"
```

Two things this gate is careful about. **It reads `stderr.log`, not
`metrics.csv`:** the ledger lives in the scheduler's summary line, while
`metrics.csv`'s `drops_cum` column is dead — 0 in every row of every run
([`../../KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md)) — so using it
would make G2 a tautology that passes on a broken scheduler (`0 == 0`). **It
accepts both log prefixes**, `[sched]` and `[vista]`; a parser that accepts only
one silently turns G2 into "no ledger found" on half its input.

> **Closure is not free.** `join_and_cleanup()` in this repository counts frames
> still stashed at teardown as policy drops; the paper's binary raw-unreffed
> them. Without that fix, closure is a coin flip on the state of the stashes when
> the run ends — measured: broken at 12 s, closing at 20 s, same config. Keep
> gate runs long enough. See [`../../vista/PAPER_DIFF.md`](../../vista/PAPER_DIFF.md).

### G3 — tracker pairing across the scheduler

The scheduler's re-injection must not break NvSORT: `dets.jsonl` parses and is
non-empty, and no ERROR/CRITICAL lines appear in `stderr.log`. It targets
`salvage` mode specifically, because salvage re-admits an *older* frame after a
newer one — the case most likely to confuse a tracker with per-camera timestamp
regressions.

A second check hides here: **`dets.jsonl` is not valid JSONL.** The application
writes it on the same fd GStreamer logs to, so lines like `Opening in BLOCKING
MODE` are interleaved. Any parser must skip lines not starting with `{`. The gate
exercises the shipped parser against real contaminated output rather than
asserting the file is clean.

### G4 — `off` is inert

With the scheduler off, the binary must behave as the stock pipeline does, and
the config-only "keep-newest" alternative (`--dropold`) must change nothing:

```python
assert abs(off_fps - dropold_fps) / off_fps < 0.05      # G4_MAX_REL_DIFF
```

Two findings share this gate. `--sched off` constructs no scheduler at all — no
probes, no thread — so it costs nothing. And **`dropold` being inert is a
result**: a leaky keep-newest queue *sounds* like a cheap version of VISTA, but
it sits upstream of where the standing queue actually forms (the buffer pool), so
under overload it is never backpressured and never leaks. **A passive drop that
waits for backpressure cannot be a policy.**

### What the gates do not cover

Stated so nobody reads a green run as more than it is.

- **They do not validate the policy.** A scheduler that admitted the *worst*
  frames would pass all of them.
- **They are light-load.** Batch atomicity under overload is evidenced by a
  campaign's fill histograms, not by G1.
- **G2 tests closure, not the correctness of the count.** A scheduler that
  miscounted an admit as a drop would still close.
- **There is no gate for the fairness floor.** `analysis/service_gaps.py`
  reports admission gaps against a `D_hard` it derives per run, and prints it as
  a reference line, not a verdict. See
  [`../design/05-scheduler-internals.md`](../design/05-scheduler-internals.md#the-fairness-floor-is-indicative-not-conformance).

## 6. The campaign

```bash
export VISTA_CLIPS=/path/to/clips
export VISTA_RESULTS=$PWD/runs        # where new runs land

make campaign-core                    # oracle + ref + the main policy campaign
make campaign-full                    # core + the s/l load points + the offset sweep
harness/run_skew_study.sh             # the skewed-activity study
make campaign-live                    # physical cameras — read §7 first
```

| target | what it runs | measured |
|---|---|---|
| YOLO11x oracle engine | one-time | **~37 min** |
| YOLO11{n,s,m,l} engines | one-time | several min each |
| `harness/run_gates.sh` | G0–G4 | ~3 min |
| `make campaign-core` | oracle + YOLO11m completeness ref + the 7-arm policy campaign at YOLO11m x 5 repeats | **~36 min** |
| `make campaign-full` | core + the YOLO11s/YOLO11l refs and campaigns + the offset sweep | **~90 min total** |
| `harness/run_skew_study.sh` | 2 oracles + 8 arms | ~25 min |
| `make campaign-live` | 3 arms x 120 s | ~7 min |

Every campaign is **idempotent and resumable**: a run whose `metrics.csv` exists
is skipped, so an interrupted campaign restarts with the same command. To force a
stage, delete its output directory. Everything is GPU-serialized; nothing here is
safe to run concurrently.

**The method, in one paragraph.** Each campaign first runs an **oracle /
completeness reference** that processes *every* frame (`ring=0`, so backpressure
blocks the pacer instead of dropping), then runs each policy arm and scores it
against that reference. Frames are keyed by `(camera_id, capture buf_pts)`, so
two policies over the same clips receive the same logical input sequence and can
be compared frame for frame: coverage means "of *these* frames, which did you
process?", and detection yield means "on *this* frame, did you find what the
oracle found?". That comparison is impossible on live input, where two policies
never see the same photons — which is why the policy comparison is replay and the
live rig is validation.

The arms, and the paper names they carry:

| paper name | arm | what it is |
|---|---|---|
| Stock-Default | `fifo33` | the stock pipeline, replay pool depth |
| Stock-LiveDepth | `fifo33` at `--replay-surfaces 2` | the same pipeline at the depth the physical rig actually uses |
| Static-Decimation | `--gap-every 3` | keep 1 of 3, no scheduler |
| VISTA-Fresh | `fresh-k2` | freshness + fairness, stash 1 — the default |
| VISTA-Activity | `imp-k2` | + activity weighting, stash 2 — needs `stash >= depth` |
| all-admit ablation | `fresh-k4` | `K = N`: every candidate seated, the value function inert |

> **Why a Stock-LiveDepth arm exists at all.** The stock pipeline's staleness
> *is* its buffer pool depth. Quoting the deep replay pool's mean age as "the
> baseline" would overstate VISTA's win against a rig that runs a shallow kernel
> ring, so the honest baseline re-runs the stock pipeline at
> `--replay-surfaces 2`. This is a policy knob wearing a plumbing costume, and it
> is the reason the paper headlines against the calibrated number rather than the
> flattering one.

## 7. The live rig

*Where the pathology is real rather than emulated: the kernel ring genuinely
overwrites its newest arrivals upstream of every counter the pipeline has.*

```bash
sudo nvpmodel -m 2 && sudo jetson_clocks
harness/run_live.sh --check          # preflight only; runs nothing
make campaign-live                   # 3 arms x 120 s, ~7 min
```

| | |
|---|---|
| cameras | 4x Logitech C920, MJPG **640x480 @ 30** |
| nodes | **`/dev/video0`, `/dev/video2`, `/dev/video4`, `/dev/video6`** |
| bus | one USB-2 bus |
| board | Jetson AGX Orin 64 GB, `nvpmodel -m 2` (30 W) + `jetson_clocks` → GPU **612 MHz** |
| detector | YOLO11m FP16, `sync-inputs=0`, `pts-fix=ON`, `timeout=33333us` |
| duration | 120 s per arm |

### The odd device nodes are not cameras

Each C920 enumerates **two** `/dev/video*` nodes: an even one (video capture) and
an odd one (**UVC metadata**). Four cameras therefore occupy `video0..7`, and the
four you want are **0, 2, 4, 6**. Pointing the app at `video0..3` gets you two
cameras and two metadata nodes, and the failure is not obvious — it looks like a
capture error on half the rig. `config/camera_params.yaml` lists the even nodes,
and `run_live.sh --check` verifies them before running anything.

### 640x480 is a bus ceiling, not a preference

**4x C920 at 720p fails on the third camera with `STREAMON` `ENOSPC`.**

This is not a bandwidth *measurement* failing. `uvcvideo` reserves isochronous
bandwidth from each camera's **peak** MJPEG payload at open time; MJPEG is
variable-bitrate, so the reservation is sized for the worst case, and three
cameras' worst cases exceed one USB-2 bus. The third `STREAMON` is refused before
a single frame moves. Lowering the *average* bitrate does not help — the
reservation is computed from the declared maximum. So 640x480 is the resolution
at which four C920s coexist on one bus. If you have a bus per camera, or USB-3
cameras, this does not bind you — but then your rig is not this rig.

### What live measures, and what it cannot

**Can:** output age, throughput, drop visibility, track churn — and ledger
closure on real cameras.

**Cannot: recall, coverage-against-truth, or detection yield.** There is no
oracle for live input. No two runs ever see the same photons, and you cannot run
a completeness pass over frames that were never recorded. That absence is
structural, not an omission.

> **Your stock arm will report `coverage 1.000`. That is the finding, not a
> bug.** The stock pipeline's frames die in the kernel capture ring, upstream of
> `v4l2src` and upstream of every probe — so its own log reports `processed N of
> N arrived frames` while a large fraction of what the cameras delivered is gone.
> The metric is not lying; it cannot see the loss. A pipeline cannot count what
> never entered it. The only way to know the true denominator is to run a policy
> that keeps the ring drained — VISTA's arrival probe sits at the source-bin pad
> and never blocks — and compare. **That cross-run inference is not a
> measurement of the stock arm**, and no evidence for the denominator exists
> anywhere in the stock run's own output. There is none. There cannot be.
> `analysis/live_report.py` prints the caveat next to the number rather than
> leaving it to prose.

## 8. Scoring your runs

The analysis code reads a **run-data root**: one directory per campaign, each
containing run directories with `metrics.csv`, `dets.jsonl(.gz)`, `sched.csv` and
`run_meta.json`.

```bash
export VISTA_DATA_ROOT=$PWD/runs

python3 analysis/policy_report.py m           # per-arm medians for campaign e3_m
python3 analysis/service_gaps.py --run e3_m/fresh-k2_r0 --per-cam
python3 analysis/_campaigns.py                # verify every path in campaigns.yaml resolves
```

Two things to know before you read the output.

**The table generators compare against the paper.** `make_table2.py --check` and
its siblings carry the draft's printed values as constants and exit non-zero on a
mismatch. Against *your* clips they will mismatch, because the oracle is
clip-specific — that is expected and is not evidence of a defect. Use
`policy_report.py` for your own campaign; reach for the `--check` generators only
if you are reproducing the paper's workload.

**`campaigns.yaml` is the only place a run path is written down.** No script
hardcodes one. If prose and that file ever disagree, the file is right.
[`../../analysis/README.md`](../../analysis/README.md) has the full dependency
chain.

## 9. Recording what ran

`harness/run_eval.py` records, per run, the **sha256 of the binary that actually
ran** (`app_sha256`), `git describe --always --dirty`, and the list of
uncommitted files. That is deliberate, and
[`03-code-provenance.md`](03-code-provenance.md) is why: the paper's own
`run_meta.json` recorded `git_sha` = HEAD at run time, which is not the source
that was compiled, and the campaign's working tree was dirty in exactly the files
that mattered. Do not remove those fields.

## 10. If something looks wrong

1. **Check the range across repeats, not one run.** Most surprises are the
   rhythm bistability.
2. **Check you are not comparing replay against live.** Ratios transfer;
   milliseconds do not.
3. **Check the gates first.** A failed batch-atomicity gate means the mux INI is
   wrong and every number from that run is incomparable — the module warns at
   runtime for exactly this reason.
4. **Check `stderr.log` for `WATCHDOG`.** If the watchdog fired, that run's drop
   accounting was reset rather than reconciled; discard the run.
5. **Check `--gap-every`.** 44 is fidelity; 3 and 4 are experiments. This is the
   single easiest way to produce plausible wrong numbers.
6. **Check the traps in [`../../KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md)**
   before concluding the code is wrong. `mux_batch` and `drops_cum` in
   `metrics.csv` do not mean what they say.
