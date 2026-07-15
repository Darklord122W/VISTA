# Glossary

*Terms that mean something specific in this project, and the handful that are
routinely confused with each other. Source: the authors' `TERMINOLOGY.md`,
verified against the mux source shipped on the reference Jetson
(`/opt/nvidia/deepstream/deepstream/sources/`) and against this repository's
code.*

---

## The scheduling model

**load ratio (`rho`)** — `S/T`: batch service time over camera frame period. The
system is oversubscribed when `rho > 1`, and a fraction `1 - 1/rho` of arriving
frames **cannot** be processed on any schedule. Instantiated on this rig at
0.84 / 1.00 / 1.86 / 2.33 (YOLO11n/s/m/l). See `docs/design/01-overview.md`.

**`S(K)`** — GPU service time for a batch of `K` frames. Because nvinfer runs a
**dynamic** batch-1–4 engine, `S(2) < S(4)` genuinely: a K=2 batch is not a
padded K=4 batch. Measured `S(4)`: 28.3 / 33.5 / 62.4 / 78.3 ms for
YOLO11n/s/m/l.

**`s_hat` (Ŝ)** — the scheduler's live EWMA estimate of `S(K)`, measured
release-to-completion (`s_hat = 0.8*s_hat + 0.2*dt`). It is *not* raw GPU time:
at `d=2` it includes the standby batch's wait, so it runs ~1.9x the raw service
time. This matters because `D_fair` is derived from it.

**`K`** — frames released per batch (= the mux `batch-size` on a scheduler run).
`K < N` is the structural precondition for the value function to decide
anything; at `K = N` every candidate is seated and the policy is inert
("all-admit").

**`depth` (`d`)** — how many batches may be in flight. Enforced as a credit
gate: `in_flight <= (depth-1)*K`. `d=2` keeps one batch queued so the GPU never
idles.

**`stash`** — frames each camera's box retains. **Not the same thing as depth**
(see below).

**`tau_max`** — hard staleness bound, 150 ms. The oldest a frame may be and
still deserve GPU time. Frames older are evicted and **counted**. This bounds
*admission age*, not output latency: a served result adds inference and tracker
time after admission.

**`D_fair` / `D_hard`** — the fairness soft scale and the force-admit deadline.
Derived, not configured: `D_fair = 2*(N/K)*s_hat`, `D_hard = 4*D_fair`. Because
`s_hat` is measured live, a heavier detector automatically earns a longer grace
period with nothing to retune.

**completion clock** — the scheduler wakes when an inference *completes*, i.e.
exactly when the GPU can accept work. Borrowed from continuous batching in LLM
serving; the difference is that a serving system must eventually run every
request, while VISTA decides which droppable inputs run at all.

**the ledger** — `arrivals == admitted_fresh + admitted_salvage + policy_drops`.
Checked by `Stats::ledger_closes()` and by gate G2. Closes exactly on every
instrumented run.

---

## Two pairs people confuse

### depth vs stash

| | **depth** | **stash** |
|---|---|---|
| direction | downstream (scheduler → GPU) | upstream (camera → scheduler) |
| counts | **batches** in flight | **frames** per camera |
| buys | GPU utilization | retention |

Analogy: **depth = vans in circulation; stash = the size of each camera's shelf
at the van stop.** The rule is *shelf ≥ vans that depart back-to-back*
(`stash >= depth`), and only when importance is on. `docs/design/04-depth-and-stash.md`.

### Window A vs Window B

Both are `nvstreammux` internals, both are time windows, and they do completely
different jobs. Only relevant when `sync-inputs=1` — which VISTA never uses.

| | **Window A — eligibility** | **Window B — co-batching** |
|---|---|---|
| what it is | per-frame age band: `[minFpsDuration, minFpsDuration + upstreamLatency]` | the force-push interval: a non-full batch holding ≥1 frame is pushed once `now >= last_batch + 1/overall-max-fps` |
| width | wide (tens–hundreds of ms) | **narrow** — 8.33 ms at `overall-max-fps=120` |
| controls | **retention/drop** — whether a frame lives | **batch composition** — who shares a batch |
| knob | `max-latency`, `overall-min-fps` / `batched-push-timeout` | `overall-max-fps` |

The distinction matters because the two knobs that were swept in this project
(`--timeout-us`, `max-latency`) **only move Window A**. Frames co-batch only if
they mature within the same ≤8.33 ms Window B slot — which is why sync-on
batches were sparse, and why `overall-max-fps` (never lowered in any experiment)
is the one knob that might legitimately have produced full sync-on batches.

---

## `nvstreammux` internals

**NvTimeSync verdict** — with `sync-inputs=1`, every queued buffer is classified
per scan (`gstnvtimesynch.cpp`, `get_synch_info`). For a frame at running time
`B`, push period `P`, latency width `L`:

| verdict | condition | consequence |
|---|---|---|
| **EARLY** | `B > now - P` | too young — wait, stop scanning this pad |
| **ONTIME** | `now - P - L <= B <= now - P` | eligible for the batch |
| **LATE** | `B + L < now - P` | **erased**, silently |

A frame must be at least `P` **old** to be eligible. That is the structural
latency floor sync-on cannot escape, and pointing it at fabricated timestamp
grids is what erased 85.3% of frames. `docs/design/06-local-clocks.md`.

**minFpsDuration** — the EARLY gate; `1/overall-min-fps`.

> **Landmine:** `batched-push-timeout` (property) and `overall-min-fps` (INI) are
> **the same internal field**; last writer wins. The app used to set the property
> *before* loading the INI, so the shipped INI silently overrode every
> `--timeout-us`. Fixed 2026-07-07: the INI is loaded first and the CLI is
> authoritative.

**adaptive batching** — batch-size = number of connected sources. Must be **off**
(`adaptive-batching=0`, `config/mux_sched.txt`) for a K-burst to land as one
batch of K.

**max-same-source-frames** — ≤ this many frames per camera per batch. `1` for
stock, `2` for the scheduler INI.

**partial batch** — `num_frames_in_batch < batch-size`. **Do not apply this term
to a scheduler run:** VISTA releases exactly K by construction, so `fill = 2` at
K=2 is a **full batch of 2**, not a partial batch of 4.

---

## Measurement vocabulary

**`buf_pts`** — the original per-source PTS, copied by DeepStream into
`NvDsFrameMeta`. **The join key** tying an output detection back to its capture,
and therefore the key `dets.jsonl` is written against. Pre-PTS-fix it is the
synthetic `jpegparse` grid PTS; with the fix it is the true capture PTS
(verified live: 13,940/13,940 exact). **VISTA never reads it for scheduling** —
it is instrumentation only.

**`n_in_batch`** — `NvDsBatchMeta::num_frames_in_batch`, the real batch. **Use
this**, not `mux_batch` (which is wrong under `--sched`; see
`../../KNOWN-ISSUES.md`).

**`e2e_ms`** — source-arrival → tracker-output latency, joined on per-frame
`buf_pts`. This is "output age". Two caveats: it carries **negative sentinels**
(filter `>= 0`), and it **understates** true glass-to-out by the ~20–28 ms
pre-arrival age (in-camera exposure, readout, USB transfer).

**`compute_ms`** — mux-src → tracker-src, joined on the batch buffer PTS.

**`coverage`** (the CSV column) — `processed ÷ arrivals`, measured *after* the
transport ring dropped the surplus. **Reads 1.0000 on every stock run. Never
quote it.** The tables use `coverage_vs_oracle = frames_processed /
frames_oracle`.

**`drops_cum`** — the mux "dropped" signal count. **Dead: 0 in every row of every
run.** The scheduler's ledger is the drop record. `../../KNOWN-ISSUES.md`.

**detection yield** — fraction of *reference* detections recovered, matched by
class and IoU ≥ 0.3.

---

## TTA vs onset recall — the one metric split that matters

The scorer emits both, from the same run. They are **not** interchangeable.

| | measures | used by |
|---|---|---|
| **`event_recall@D`** | **onset delay, in frame time**: was a matching detection found on a frame *captured* within `D` of onset? | **Table IV** |
| **`tta_recall@D`** (TTA) | **emission time**: `onset_delay + the run's mean e2e <= D` | **Tables II, III** |

`tta_recall` charges the policy for its own output latency — which is the entire
point of "time-to-awareness": an operator learns of an event when the detection
is *emitted*, not when the photon landed. `event_recall` does not charge it.

The gap is large. On the brief skew clips, VISTA-Activity at stash 2:

| metric | @250 ms |
|---:|---:|
| `event_recall` | **0.71** |
| `tta_recall` | **0.58** |

So **the abstract's headline "0.30→0.71 at 250 ms" is the onset metric**, while
its "500 ms event awareness from 0–19% to ~30%" is the emission metric. Both are
defensible; they answer different questions. Know which one you are holding
before comparing a Table IV cell to a Table II cell.

**event** — a new reference-tracked object persisting ≥3 reference frames,
oracle confidence ≥0.40, IoU-tracked at ≥0.30. The event set is **a property of
the clips**: a different scene is a different denominator, which is why recall
numbers taken on other footage are not comparable to the draft's. The
stratification into clean / reincarnation / class-flicker events is
**automatic**, not human — `../../KNOWN-ISSUES.md`.

---

## Naming

The project renamed to VISTA late. The name never became a code identifier — the
namespace was `mcrt`, the class `Scheduler`, the flags `--sched*` — so the old
spelling survives in places that are load-bearing: the pre-rename binary's stderr
ledger line is prefixed `[sched]` where the shipped module prints `[vista]`, and
both the old and the current scheduler thread name appear in traces. **Parsers
accept both.** A parser that accepts only one silently reports "no ledger found"
on half its input. Full map and every rename hazard: `../../NAMING.md`.

The draft's policy names are configurations of one binary, not separate systems:

| paper name | arm | configuration |
|---|---|---|
| Stock-Default | `fifo33` | no scheduler |
| Stock-LiveDepth | `fifo33` | no scheduler, `--replay-surfaces 2` |
| Static-Decimation | `dec13` | no scheduler, `--gap-every 3` |
| VISTA-Fresh | `fresh-k2` | `--sched fresh --sched-k 2 --sched-stash 1` |
| VISTA-Activity | `imp-k2` | `--sched imp --sched-k 2 --sched-stash 2` |
| all-admit ablation | `fresh-k4` | `--sched fresh --sched-k 4` (`K = N`) |
