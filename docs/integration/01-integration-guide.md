# Integrating VISTA into your own pipeline

This is the long-form guide for putting VISTA in front of your own batched
detector. It assumes you have a working multi-camera GStreamer/DeepStream
pipeline and want the frame-drop decision to become explicit, bounded, and
counted.

If you only want to run the paper's reference application, you want
[`docs/usage/`](../usage/) instead.

Contents:

1. [What VISTA actually needs](#1-what-vista-actually-needs)
2. [The 60-second version](#2-the-60-second-version)
3. [Naming your elements](#3-naming-your-elements)
4. [The five host obligations](#4-the-five-host-obligations)
5. [Teardown](#5-teardown)
6. [Reading the decision log](#6-reading-the-decision-log)
7. [Worked example A — `vista/examples/minimal_pipeline`](#7-worked-example-a--the-minimal-pipeline)
8. [Worked example B — the reference app in `app/`](#8-worked-example-b--the-reference-app)
9. [What to check before you believe your numbers](#9-what-to-check-before-you-believe-your-numbers)

---

## 1. What VISTA actually needs

VISTA is not a plugin and not a pipeline. It is one class
(`vista::Scheduler`), three pad probes and one thread, bolted onto a graph you
already have. Nothing is removed; with `mode = "off"` the probes are never
attached and your pipeline is bit-identical to what it was.

The design needs exactly three things from a host system. Everything else —
DeepStream, nvstreammux, nvinfer, NvSORT — is one particular way of supplying
them.

| # | Requirement | Why it is required | DeepStream binding (what the module does today) |
|---|---|---|---|
| **(i)** | **An interception point before the shared batcher**, at which you can take custody of an arriving frame and tell the transport "delivered". | This is what converts an invisible drop into a decision. If frames can reach the batcher without passing you, the transport still decides — see [`05-troubleshooting.md`](05-troubleshooting.md). | A `GST_PAD_PROBE_TYPE_BUFFER` probe on **each source bin's ghost `src` pad**. It refs the buffer, stashes it, and returns `GST_PAD_PROBE_DROP`. GStreamer believes delivery succeeded, so backpressure never forms upstream. |
| **(ii)** | **An inference-completion signal** carrying how many frames completed. | It is the clock. Releases are paid for by completions, so the release rate equals the GPU's completion rate for any detector, with nothing to tune. | A buffer probe on the **tracker's `src` pad**, reading `NvDsBatchMeta::num_frames_in_batch`. Any element downstream of `nvinfer` works; VISTA only reads batch metadata. |
| **(iii)** | **A monotonic local clock.** | All ages are computed on the host's own arrival stamps. VISTA never reads PTS for scheduling. Commodity USB capture paths fabricate per-camera timestamp grids that disagree by seconds; a scheduler that trusted them would be scheduling on fiction. | `g_get_monotonic_time() / 1e6`. |

Two consequences worth internalising:

- **VISTA does not depend on camera synchronisation, camera timestamps, or the
  batcher's internals.** Frames from unsynchronised free-running cameras are
  fine. That is the point.
- **VISTA stores pointers, not pixels.** A stashed frame is a `GstBuffer*` ref;
  the pixel data stays in the upstream converter's buffer pool. This is why
  obligation 5 (pool slack) exists.

For a non-GStreamer host, the mapping of these three requirements onto whatever
you have is in [`04-porting-checklist.md`](04-porting-checklist.md).

---

## 2. The 60-second version

```cpp
#include "vista/vista_scheduler.hpp"

// 1. Configure. A default-constructed SchedCfg with mode="fresh" is exactly
//    the paper's VISTA-Fresh operating point (K=2, d=2, stash=1, tau_max=150ms,
//    w = 0.40/0.35/0.25 with w_imp forced to 0 by the mode). This is the
//    recommended default for approximately uniform camera activity.
vista::SchedCfg cfg;
cfg.mode = "fresh";
cfg.k    = 2;              // must equal your mux AND nvinfer batch-size

// If — and only if — your camera activity is demonstrably skewed, the paper's
// other configuration is VISTA-Activity. It changes TWO things, not one:
//
//   cfg.mode  = "imp";    // freshness + importance + fairness
//   cfg.stash = 2;        // REQUIRED: stash >= depth, or the activity term is
//                         // structurally inert and no weight can fix it.
//
// Leaving stash at 1 here is the one misconfiguration that fails silently in
// the metric and loudly in the log. See docs/usage/06-tuning.md §4.

// 2. Construct. Throws std::runtime_error on an invalid configuration.
auto sched = std::make_unique<vista::Scheduler>(cfg, num_cams);

// 3. Attach BEFORE the pipeline goes to PLAYING. Throws if a required element
//    is missing or a host obligation is verifiably violated.
sched->attach(pipeline);

gst_element_set_state(pipeline, GST_STATE_PLAYING);
g_main_loop_run(loop);

// 4. Teardown. The order is load-bearing — see §5.
sched->request_stop();                            // 1: unblock intent
gst_element_set_state(pipeline, GST_STATE_NULL);  // 2: flush pads
sched->join_and_cleanup();                        // 3: join + unref stash
sched->print_summary();                           // optional: one stderr line
gst_object_unref(pipeline);                       // 4: LAST
```

Build (verified on this machine, see [`docs/usage/01-build.md`](../usage/01-build.md)):

```bash
g++ -std=c++17 -O2 -Wall -Wextra -c vista/src/vista_scheduler.cpp \
    -Ivista/include -I/opt/nvidia/deepstream/deepstream/sources/includes \
    $(pkg-config --cflags gstreamer-1.0)
# link with: $(pkg-config --libs gstreamer-1.0) \
#            -L/opt/nvidia/deepstream/deepstream/lib -lnvdsgst_meta -lnvds_meta \
#            -Wl,-rpath,/opt/nvidia/deepstream/deepstream/lib -lpthread
```

That snippet is the whole integration **provided your pipeline satisfies §4**.
It will not silently misbehave if it does not: three of the five obligations
throw at `attach()`, and a fourth warns at runtime.

---

## 3. Naming your elements

VISTA finds its probe points **by element name**, through
`gst_bin_get_by_name()` on the pipeline you hand to `attach()`. Five
`SchedCfg` fields control that lookup. The defaults match the reference app;
override them to match your graph rather than renaming your elements.

| Field | Default | What it must point at |
|---|---|---|
| `source_bin_prefix` | `"source-bin-"` | Per-camera bins named `<prefix><i>` for `i` in `0 .. num_cams-1`. Camera `i`'s index *is* its identity: it must be the same `i` you link to the mux's `sink_<i>`, because the mux turns pad number into `source_id`, which becomes `camera_id` in your output. |
| `source_pad_name` | `"src"` | The pad on that bin that VISTA probes and later pushes on. Must be the bin's ghost src pad — the last pad before the batcher. |
| `tracker_name` | `"tracker"` | **Any** element downstream of `nvinfer`. It is only a place to observe completed batch metadata. Point it at your OSD, your sink, whatever — as long as it is downstream of inference. |
| `tracker_pad_name` | `"src"` | The pad on that element to observe. |
| `mux_name` | `"stream-muxer"` | The batcher, used **only** for the obligation checks. Set to `""` to disable those checks (see the warning below). |
| `pgie_name` | `"primary-inference"` | The inference element, used **only** for the obligation check. `""` disables it. |

If a required element cannot be found, `attach()` throws a message naming the
element and the field to change. Verified verbatim on this machine:

```
vista: could not find source-bin-0. VISTA intercepts frames on each camera's
source bin; name your bins 'source-bin-<i>' or set SchedCfg::source_bin_prefix.

vista: could not find 'tracker'. VISTA needs an inference-completion signal:
point SchedCfg::tracker_name at any element downstream of nvinfer.
```

> **Do not set `mux_name`/`pgie_name` to `""` to make an error go away.** Those
> checks exist because the failure they catch is silent. An empty name makes
> VISTA skip the check, not satisfy it. The same applies to `strict = false`,
> which disables *all three* attach-time checks at once — including
> `sync-inputs`.

### Probe ordering

Probes fire in attach order on a pad. If you have your own instrumentation
probe that must stamp a frame's arrival, **attach it before**
`Scheduler::attach()`. The reference app does exactly this, which is why its
`e2e_ms` includes the time a frame spent waiting in the stash rather than
hiding it.

---

## 4. The five host obligations

Full reasoning and the measurement behind each one is in
[`03-pipeline-obligations.md`](03-pipeline-obligations.md). The summary:

| # | Obligation | Enforced how |
|---|---|---|
| 1 | **Batcher `batch-size` = `cfg.k`.** A release of K frames must land as exactly one batch. | Throws at `attach()` (unless `strict = false`). |
| 2 | **The batcher must not force-push a partial burst.** For DS 7.1's new nvstreammux this means the INI: `adaptive-batching=0`, deadline anchors pushed far out, and **no `batch-size` key**. Use `vista/config/mux_vista.txt`. | Not readable as a property. Caught at runtime by the batch-atomicity gate, which warns on stderr after 20 completions if fewer than 90% of batches carry exactly K frames. |
| 3 | **`nvinfer` `batch-size` = `cfg.k`.** Partial batches change service time, so Ŝ and every deadline derived from it drift. | Throws at `attach()`. |
| 4 | **`sync-inputs = 0`.** VISTA replaces timestamp alignment with local arrival-clock scheduling. | Throws at `attach()`. |
| 5 | **Upstream buffer-pool slack** — for DeepStream, `nvvideoconvert output-buffers >= 12` (the element's own default is 4). The stash pins one pool surface per stashed frame; without slack the pool becomes the throttle and backpressure returns to exactly the place VISTA exists to keep drained. | **Not checkable.** You must do this yourself. |

Additionally, on DeepStream: `USE_NEW_NVSTREAMMUX=yes` must be set **before
`gst_init()`**, because the environment variable decides which nvstreammux
implementation gets registered. This is a precondition of the reference
pipeline rather than of VISTA itself — but obligation 2 is written for the new
mux's INI, and the legacy mux has no equivalent knob.

---

## 5. Teardown

Teardown order is load-bearing and gets its own section because getting it
wrong produces a hang or a use-after-free rather than a message.

```cpp
sched->request_stop();                            // 1
gst_element_set_state(pipeline, GST_STATE_NULL);  // 2
sched->join_and_cleanup();                        // 3
gst_object_unref(pipeline);                       // 4
```

1. **`request_stop()` first.** It sets a flag and notifies the condition
   variable. It does *not* join — the release thread may be blocked inside
   `gst_pad_push`, and nothing you do from this thread will return it.
2. **`GST_STATE_NULL` second.** Flushing the pads is what unblocks that
   `gst_pad_push`. This is why step 1 cannot be `join()`.
3. **`join_and_cleanup()` third.** Joins the release thread, unrefs every
   stashed `GstBuffer`, releases the pad refs, flushes the decision CSV.
4. **`gst_object_unref(pipeline)` last.** The stashed buffers belong to pools
   owned by elements in that pipeline. Unref the pipeline before step 3 and
   you are unreffing buffers whose pool is gone.

The destructor does steps 1 and 3 for you (`~Scheduler` calls `request_stop()`
then `join_and_cleanup()`), so a `Scheduler` that outlives its pipeline's
NULL transition is safe. A `Scheduler` destroyed *after* `gst_object_unref` on
the pipeline is not. Keep the `Scheduler` in a scope that closes before the
pipeline's ref is dropped, or drive the four steps explicitly as above.

### EOS

EOS **passes through untouched**. VISTA does not swallow it, buffer it, or
forward it later — an early version that did deadlocked teardown. When a
camera's EOS goes by, VISTA marks that camera done and drops its stashed refs.
The cost is at most `stash` tail frames per camera never being processed, which
is irrelevant to steady-state benchmarks (they measure rates and
distributions, not totals) and which you should be aware of if you are
measuring something else.

---

## 6. Reading the decision log

Set `cfg.decision_csv` to a path and VISTA writes one row per decision — the
audit trail behind every claim about which frame ran and why.

```
t,event,cam,slot,age_ms,fresh_score,imp_score,fair_score,value,released,in_flight,buf_pts
0.9730,admit,0,fresh,22.9,0.847,0.000,1.000,0.589,2,2,544356999
0.9730,admit,3,fresh,0.0,1.000,0.000,1.000,0.650,2,2,567200000
```

| Column | Meaning |
|---|---|
| `t` | Seconds since `attach()`, monotonic. |
| `event` | `admit`, `admit-salvage`, `retain-held`, or (only with `log_drops = true`) `displace`, `displace-held`, `evict-stale`, `evict-held`, `eos`. |
| `cam` | Camera index. |
| `slot` | `fresh` or `held`. |
| `age_ms` | Frame age at the decision, on the local arrival clock. |
| `fresh_score` / `imp_score` / `fair_score` | The three normalised terms, each in [0,1]. `imp_score` is 0 in `fresh` mode by construction. |
| `value` | `w_fresh*fresh + w_imp*imp + w_fair*fair`. Reproduce it from the three scores to check your weights are what you think. |
| `released` | How many frames went out in this release (should equal K). |
| `in_flight` | Frames released but not yet completed, after this release. |
| `buf_pts` | The frame's PTS in ns — the identity that lets you join a decision to a `dets.jsonl` record. |

Three things to know before you parse it:

- **It is admissions-only by default.** `log_drops` defaults to `false`, which
  is the paper-identical behaviour: drop rows put file I/O on the arrival path
  of a timing-sensitive scheduler. Turn it on when you are debugging a policy,
  off when you are measuring one. The drop *count* is always exact regardless —
  it is in `Stats` and in `print_summary()`.
- **The ledger is checkable.** `arrivals == admitted_fresh + admitted_salvage +
  policy_drops` on every run. `Stats::ledger_closes()` is that assertion;
  `vista/examples/minimal_pipeline` asserts it.
- **`value` is a ranking, not a threshold.** Nothing in the scheduler compares
  `value` against a constant, which is why a non-unit weight sum only warns.

---

## 7. Worked example A — the minimal pipeline

`vista/examples/minimal_pipeline/` is the copy-paste starting point: a
synthetic-source pipeline that needs no cameras, no clips and no model. Read
it before you touch your own graph — it is the smallest thing that exercises
all three probe points and the ledger.

What to look at, in order:

1. **The source bins.** Each is a `GstBin` named `source-bin-<i>` exposing one
   ghost `src` pad. That naming and that pad shape is the entire contract for
   requirement (i). If your pipeline builds cameras as bins already — most do —
   you are done.
2. **The trunk.** `nvstreammux` named `stream-muxer` with `batch-size = K` and
   `config-file-path` pointing at `vista/config/mux_vista.txt`; `nvinfer` named
   `primary-inference` with `batch-size = K`; a completion element named
   `tracker`. Obligations 1–4, in graph form.
3. **The converter's `output-buffers`.** Obligation 5, the one nothing checks.
4. **The attach/teardown pair**, which is §2 and §5 verbatim.
5. **The ledger assertion** at the end: it reads `sched.stats()` and checks
   `ledger_closes()`. If your integration is correct, that assertion holds on
   every run. If it does not, frames are reaching the batcher without passing
   your arrival probe — obligation 1's failure mode, not a bookkeeping bug.

## 8. Worked example B — the reference app

`app/` is the real thing: the application that produced every number in the
paper. It is worth reading precisely because it is *not* minimal — it shows
what integration looks like once the pipeline has instrumentation, two
front-ends and a CLI.

The parts that are about VISTA, and nothing else:

1. **Mode gating.** The scheduler is constructed only when `--sched` is not
   `off`. When it is off, the probes are never attached and the binary is the
   stock pipeline. Keep this property in your own integration: it is what makes
   an A/B honest.
2. **The obligations are set in code, not left to the operator.** When
   `--sched` is on, the app overrides the mux and nvinfer `batch-size` to K,
   raises `nvvideoconvert output-buffers` to 12, refuses to start if
   `sync_inputs` is set, and swaps in the scheduler mux INI. This is the right
   shape: obligations 1, 3, 4 and 5 are consequences of turning the scheduler
   on, not things a user can forget.
3. **Attach order.** The metrics collector's probes are attached *before* the
   scheduler's, so a frame is stamped on arrival and its `e2e_ms` includes the
   stash wait. If you have your own latency instrumentation, copy this
   ordering. Attaching the scheduler first would make your latency numbers
   flattering and wrong.
4. **Teardown.** `request_stop()` → `set_state(NULL)` → `join_and_cleanup()` →
   `print_summary()` → `unref`. Exactly §5.

Flags, defaults and units: [`docs/usage/03-cli-reference.md`](../usage/03-cli-reference.md).
Output schemas: [`docs/usage/05-outputs.md`](../usage/05-outputs.md).

---

## 9. What to check before you believe your numbers

In order of how often each one is the actual problem:

1. **Did the atomicity gate warn?** Grep stderr for `batch atomicity gate
   FAILED`. If it fired, obligation 2 is broken and your run is not comparable
   to anything. This is the failure that is otherwise silent.
2. **Does the ledger close?** `arrivals == admitted_fresh + admitted_salvage +
   policy_drops`. If not, frames are bypassing the arrival probe.
3. **Is `n_in_batch` really K?** `Stats::fill_hist` is the histogram of
   completed batch fills. It should be a spike at K. The gate checks exactly
   this, but the histogram tells you *what* it degraded to, which names the
   cause: fill = source count means `adaptive-batching` is on; a mix of 1 and
   K-1 means your deadline anchors are too tight.
4. **Did you get the warning about `stash < depth` with importance on?** If you
   are running `imp` or `salvage` mode, read it. It is describing the exact
   misconfiguration that makes the activity term structurally inert — see
   [`docs/usage/06-tuning.md`](../usage/06-tuning.md).
5. **Is your load actually over capacity?** VISTA solves oversubscription. If
   your detector keeps up (ρ < 1), there is nothing to shed, and the always-K
   release quantises admission for no benefit. Run `mode = "off"`.
