# PAPER_DIFF — this module vs. the paper's binary

`src/vista_scheduler.cpp` is the scheduler that produced every number in the
VISTA paper, vendored from `multicam_perception_rt/cpp/src/scheduler.cpp`. This
file enumerates every way it differs, so a reviewer can decide for themselves
whether the artifact still measures what the paper measured.

**The scoring, selection, gating and eviction logic is byte-for-byte the
paper's.** Every difference below falls into one of four categories, and all of
them are **behaviour-neutral on the paper's configurations** — with two
exceptions in category 3/4 that are *bug fixes*, called out explicitly and
bounded.

> A caveat that belongs at the top: the paper's exact binary **exists in no
> commit** (`--sched-stash` and `int stash` are absent from every commit; three
> files were dirty at run time, and `run_meta.json`'s `git_sha` is therefore not
> a reliable pointer). "Byte-for-byte the paper's" means *byte-for-byte the
> working-tree source that produced the archived runs*, which is what was
> vendored here. It is not a claim that can be discharged against git history.

---

## 1. Rename

Mechanical, no behaviour.

| Was | Now |
|---|---|
| namespace `mcrt` | namespace `vista` |
| `t_sched_pushing` | `t_vista_pushing` |
| stderr prefix `[sched]` | `[vista]` |
| pthread name `sparq-sched` | `vista-sched` |
| `config/mux_sched.txt` | `config/mux_vista.txt` (keys byte-identical; verified by md5 of the comment-stripped key section) |

SPARQ never became a code identifier — the namespace was `mcrt`, the class
`Scheduler`, the flags `--sched*`. It survives only in comments, help text, the
pthread name, and `mux_sched.txt`'s header.

**Rename hazard, and why parsers must accept both spellings.** Two archived
tools key on the OLD names against ARCHIVED data:

- `harness/run_plan.py` hardcodes `sname="sparq-sched"` to find the scheduler
  thread's CPU time. That thread name is the *only* evidence behind the paper's
  CPU-overhead claim (it measures 0.345%).
- `analysis/aggregate_runs.py` regexes a literal `[sched]` prefix against
  archived `stderr.log` files. That regex **is** the drop-ledger evidence.

This module emits `[vista]` and names its thread `vista-sched`. Any parser must
accept **both**; archived data has the old ones and will never have the new
ones. `print_summary()`'s line format is otherwise unchanged for exactly this
reason.

---

## 2. Parameter injection

Values that were compile-time constants or hardcoded strings are now
`SchedCfg` fields. **Every default equals the paper's value**, so a
default-constructed `SchedCfg` is the paper's operating point.

| Field | Default | Was |
|---|---|---|
| `source_bin_prefix`, `source_pad_name` | `source-bin-`, `src` | hardcoded |
| `tracker_name`, `tracker_pad_name` | `tracker`, `src` | hardcoded |
| `mux_name`, `pgie_name` | `stream-muxer`, `primary-inference` | hardcoded (`""` disables the check) |
| `imp_halflife_s` | 2.0 | compile-time constant |
| `imp_max` | 2.0 | compile-time constant |
| `retention_thresh` | 0.30 | compile-time constant |

This is what makes the module droppable into a pipeline whose elements are named
differently. It cannot change results: the paper's runs used these values.

---

## 3. Validation

Rejects configurations the paper never used. On any paper configuration these
checks are silent and the code path is identical.

- `mode` must be one of `off|fresh|imp|salvage`.
- `k` in `1..2*num_cams`; `stash` in `1..8`.
- **`depth >= 1`.** The paper binary accepted `--sched-depth 0` and hung
  silently, with no output and no message: the gate `in_flight > (depth-1)*k`
  reads `0 > -k` — true forever. Now it throws.
- Weights non-negative, at least one positive; warns (does not throw) if they do
  not sum to 1.0, because scores are compared and never thresholded, so a
  non-unit sum only rescales v(f) uniformly.
- Warns when `use_importance() && stash < depth` — the paper's RQ3 result, which
  is a real deployment trap rather than a typo.
- Host-obligation checks on `attach()` (mux/nvinfer `batch-size`,
  `sync-inputs`); `strict = true` by default.

### 3a. CORRECTION vs. the paper binary: the mux batch-size check cannot be an equality throw

This check was originally written as `mux batch-size != k -> throw`. **Measured
on this hardware, that rejects the paper's own headline configuration.** On
DS 7.1's new mux the property is not reliable at `attach()` time — `attach()`
necessarily runs before the state change, and requesting sink pads raises
`batch-size` to the pad count while the mux still has its built-in
`adaptive-batching=1` (the INI that disables it is not read until the state
change). Probed on 4 cameras with the app setting `batch-size=2` first:

```
create -> 1 | set 2 -> 2 | INI -> 2 | sink_0 -> 2 | sink_1 -> 2
       | sink_2 -> 3 | sink_3 -> 4 | ... and 2 again once PLAYING
```

The same run's batch fill histogram was **100% at exactly k=2** (303/303
completed batches). So the property said 4 while the mux batched 2. The check is
now: throw if `< k` (never batchable), warn if `> k` and let the **runtime**
atomicity gate decide from the fill histogram.

This is a change to a check that **did not exist in the paper binary at all**,
so it cannot have affected any archived run. It is recorded here because it is
the one place where a "validation" addition was itself wrong on first contact
with the hardware.

---

## 4. Additive

New surface; nothing removed. Except where noted, no effect on scheduling.

- **`Stats` + `stats()`** — snapshot counters. Read-only.
- **`Stats::ledger_closes()`** — the paper's accountability claim, checkable.
- **The runtime batch-atomicity gate** (`gate_check`, default on). After 20
  completions, warns if <90% of completed batches carry exactly k frames. This
  is the campaign's pre-run gate G1 moved in-process, and it is the only
  enforcement possible for the mux INI, which is not property-readable. It only
  reads a histogram the scheduler already maintained; it changes no decision.
- **Optional drop rows in the decision CSV** (`log_drops`, default **false** —
  see below).
- **Watchdog** — pre-existing; armed only after 3 completions because the first
  batches can include a multi-minute TensorRT engine build.

### 4a. FIX vs. the paper binary: shutdown accounting (the ledger)

`join_and_cleanup()` raw-unreffed the buffers still sitting in the stashes at
teardown, without counting them. Those frames were counted as **arrivals** and
never as admits or drops, so the invariant

```
arrivals == admitted_fresh + admitted_salvage + policy_drops
```

closed **only if every stash happened to be empty at the instant the run
ended**. Measured on identical configurations: it closed on a 20 s run
(2356 = 2032 + 0 + 324) and **failed on a 12 s run** of the same pipeline. It was
a coin flip on the paper's headline claim.

Residual stashed frames are now counted as policy drops — which is what they
are: they arrived, they will never be inferred, and VISTA's entire argument is
that such frames are counted rather than silently absorbed. The general identity
is `arrivals == admitted + policy_drops + still_in_stash`; the fix forces
`still_in_stash` to zero at shutdown.

**Bound on the effect:** at most `num_cams * stash` frames, entirely at
teardown — ≤4 frames out of ~2.4k in a 20 s 4-camera run (<0.2%). It changes no
scheduling decision and no steady-state rate. Its consequence for the archive is
that the paper binary's reported `policy drops` **undercount by up to
`num_cams * stash` frames per run**, always in the same direction. Any drop-rate
figure derived from archived `stderr.log` lines carries that bounded bias; it is
far below the effects the paper reports, but it is real and it is stated here
rather than quietly fixed.

### 4b. Why `log_drops` defaults to false

Two reasons, both about not perturbing what is being measured:

1. **Timing.** A drop row is written from `on_arrival()` — the arrival path of a
   timing-sensitive scheduler, on the streaming thread, under `mu_`. Buffered
   `fprintf` still takes a lock and can block on a flush. VISTA's whole premise
   is deciding at completion instants with sub-millisecond release bursts; the
   honest default is to keep I/O off that path. Admission rows are written from
   the release thread, which is VISTA's own.
2. **Schema.** With `log_drops = false` the CSV contains admissions only, which
   is **exactly the schema the analysis scripts in this repository expect**. Turning it
   on adds row types (`displace`, `evict-stale`, `evict-held`, `shutdown`) that
   the archived analysis scripts have never seen. Default-off means a CSV from
   this module drops straight into the existing tooling.

The drop *counts* are always exact in `Stats` regardless — `log_drops` controls
only whether each drop also gets a CSV row. Accountability does not depend on it.

---

## 5. What was verified, and how

On the paper's hardware (Jetson AGX Orin 64GB, DeepStream 7.1, g++ 11.4),
replaying 4 clips through YOLO11n FP16:

| Claim | Result |
|---|---|
| Library builds, both paths, zero warnings under `-Wall -Wextra` | pass |
| `find_package(vista)` -> `vista::scheduler` compiles, links, runs | pass |
| Example builds, zero warnings; runs with `strict` on | pass |
| Batch atomicity with `mux_vista.txt` | **303/303 completed batches at exactly k=2** |
| Ledger closes (`mode=fresh`, 12 s / 17 s / 20 s) | pass, after the 4a fix |
| Ledger closes (`mode=off`/`imp`/`salvage`) | pass |
| Decision CSV = admissions only | 746 rows == 746 fresh admits |
| INI keys identical to `mux_sched.txt` | md5 match on the comment-stripped key section |

What was **not** verified: nothing here reproduces the paper's latency or recall
numbers. The example replays clips, has no arrival stamping and no detection
dump; its latencies are replay latencies. It is an integration and correctness
harness, and the table above is the scope of its claims.
