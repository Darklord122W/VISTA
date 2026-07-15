# VISTA — the scheduler module

This directory is the ready-to-use deliverable: the completion-clocked
load-shedding scheduler from the paper, as a library you drop into your own
DeepStream pipeline. It is the same code that produced the paper's numbers (the
delta is enumerated and justified in [PAPER_DIFF.md](PAPER_DIFF.md)).

VISTA sits in front of `nvstreammux`. It holds a bounded per-camera stash, and
at each inference completion scores the candidates

```
v(f) = w_fresh * fresh(f) + w_imp * imp(cam f) + w_fair * fair(cam f)
```

and releases the top-K into the next batch, under a hard staleness bound
(`tau_max`, 150 ms) and a hard per-camera service-gap bound. Every frame it does
not process is **counted**, not silently absorbed by transport backpressure.

```cpp
#include "vista/vista_scheduler.hpp"

vista::SchedCfg cfg;          // defaults == the paper's operating point
cfg.mode = "fresh";           // VISTA-Fresh: K=2, depth=2, stash=1
vista::Scheduler sched(cfg, num_cams);
sched.attach(pipeline);       // BEFORE PLAYING
// ... run ...
sched.request_stop();
gst_element_set_state(pipeline, GST_STATE_NULL);
sched.join_and_cleanup();
```

A complete, compiling, runnable pipeline is in
[`examples/minimal_pipeline/`](examples/minimal_pipeline/). If you read one
thing besides this file, read that `main.cpp`.

---

## 1. The three hard requirements of `attach()`

`attach()` throws `std::runtime_error` if any of these is unmet. They are
requirements of the *mechanism*, not style preferences.

**R1 — One bin per camera, named `<prefix><i>`, upstream of the mux, exposing a
static src pad.** Default prefix `source-bin-`, pad name `src`; override with
`SchedCfg::source_bin_prefix` / `source_pad_name`. VISTA puts its arrival probe
on that pad and returns `GST_PAD_PROBE_DROP`, then re-pushes the frames it
selects through the same pad. So the pad must be the bin's **only** outlet to
the mux: any frame of camera *i* that reaches the mux by another route is a
frame VISTA cannot account for, and the ledger stops meaning anything. A ghost
pad is a static pad, so the usual `gst_ghost_pad_new("src", target)` satisfies
this.

**R2 — An element downstream of `nvinfer` whose buffers carry
`NvDsBatchMeta`.** This is the **completion clock** — the entire design is
"decide what to run next at the instant the GPU finishes", so without it VISTA
has no clock and will not release anything. Point `SchedCfg::tracker_name` at
it (default `tracker`). It does not have to be a tracker: VISTA reads only
`num_frames_in_batch` off the batch meta. Any element after nvinfer works.

**R3 — Call `attach()` before `PLAYING`, and after any arrival-stamping probe
of your own.** Probes on a pad fire in the order they were added, and VISTA's
arrival probe returns `DROP` — so a probe you add *after* VISTA's will never see
the frames VISTA sheds. If you stamp arrival times for end-to-end latency
accounting, add that probe **first**; that ordering is why the reference app's
`e2e_ms` correctly includes the stash wait.

> **`mode = "off"` is the host's job.** `attach()` does *not* consult
> `cfg.enabled()`; it wires probes unconditionally. To get the bit-identical
> stock pipeline that `off` promises, construct **no `Scheduler` at all** — see
> the `sched_on` branch in the example.

---

## 2. The five host obligations

VISTA can only be correct if the pipeline around it is configured to let a
release of K frames land as exactly one batch. These five are on you.

| # | Obligation | How it is enforced |
|---|---|---|
| 1 | `nvstreammux` `batch-size` = k | **Partly checked.** Throws if `< k`; warns if `> k` (see below) |
| 2 | The mux INI (`config/mux_vista.txt`) | **Not checkable at attach.** Enforced at runtime by the atomicity gate |
| 3 | `nvinfer` `batch-size` = k | **Strict check — THROWS** |
| 4 | Upstream `nvvideoconvert` `output-buffers` >= 12 | **Not enforced.** Your responsibility |
| 5 | `nvstreammux` `sync-inputs` = 0 | **Strict check — THROWS** |

Strict checks run in `attach()` and can be disabled with `SchedCfg::strict =
false`. Do that only if you know why.

**(1) Mux batch-size — why this one only warns.** Measured on DS 7.1 with the
new mux: this property is *not reliable at `attach()` time*, because `attach()`
necessarily runs before the state change. Requesting sink pads raises
`batch-size` to the pad count while the mux still has its built-in
`adaptive-batching=1`; the INI that disables adaptive batching is not read until
the state change. Probed on a 4-camera pipeline with the app setting
`batch-size=2` first:

```
create -> 1 | set 2 -> 2 | INI -> 2 | sink_0 -> 2 | sink_1 -> 2
       | sink_2 -> 3 | sink_3 -> 4 | ... and 2 again once PLAYING
```

So on the paper's own headline configuration (4 cameras, k=2) the property reads
**4** exactly where the check runs, while the run's batch fill histogram is
**100% at exactly k=2**. An equality throw there rejects a correct pipeline —
verified, it did. What remains decidable is `batch-size < k`, which can never
batch k frames; that throws. `> k` warns and is settled by the runtime gate,
which decides on evidence instead of on a property that is lying at that moment.

**(2) The mux INI — enforced by evidence, not by a property.** nvstreammux does
not expose its INI-derived state, so this cannot be validated at `attach()`.
Instead the **batch atomicity gate** runs in `on_completion()`: after 20
completions, if fewer than 90% of completed batches carry exactly k frames, it
prints a loud warning. This matters because a wrong INI degrades *silently* —
with `adaptive-batching=1` the K-burst merges to the source count; with deadline
anchors too tight it splits into `1 + (k-1)`. Either way the run completes and
reports plausible numbers that are not comparable to the paper's. Use
`config/mux_vista.txt`, and read its header before editing it.

**(4) `output-buffers >= 12`.** VISTA stashes frames and releases them later, so
buffers live longer than in a stock pipeline. At the default pool size the pool
— not VISTA — becomes the throttle: it starves, upstream stalls, and the drop
decision silently migrates back into the transport, which is precisely what
VISTA exists to take away from it.

**(5) `sync-inputs = 0`.** VISTA replaces timestamp alignment with local
arrival-clock scheduling. On commodity USB capture the fabricated PTS grids
disagree by seconds; in our measurements `sync-inputs=1` silently erased 85.3%
of arrived frames.

---

## 3. Teardown — the order is load-bearing

```cpp
sched.request_stop();                            // 1
gst_element_set_state(pipeline, GST_STATE_NULL); // 2
sched.join_and_cleanup();                        // 3
sched.print_summary();                           // 4  (stats() also fine here)
gst_object_unref(pipeline);                      // 5
```

1. **`request_stop()`** — the release thread may be blocked inside
   `gst_pad_push`. This tells it to stop looping.
2. **`set_state(NULL)`** — flushes the pads, which is what actually unblocks
   that push. Before step 1 this can deadlock; after step 3, `join()` waits on a
   push that nothing will unblock.
3. **`join_and_cleanup()`** — joins the thread and releases stashed buffers.
4. **Read your stats** while the objects are still alive.
5. **`gst_object_unref(pipeline)` LAST** — the buffers released in step 3 belong
   to this pipeline's buffer pools. Unref it earlier and you are unreffing
   buffers into freed pools.

The destructor performs steps 1 and 3 if you forget, but it cannot perform step
2 for you — so a `Scheduler` that goes out of scope while the pipeline is still
PLAYING can hang. Do the teardown explicitly.

---

## 4. The decision CSV

Set `SchedCfg::decision_csv` to a path to get the per-decision audit trail. This
is the schema, taken verbatim from a real run:

```
t,event,cam,slot,age_ms,fresh_score,imp_score,fair_score,value,released,in_flight,buf_pts
0.5577,admit,0,fresh,0.0,1.000,0.000,1.000,0.650,2,2,0
1.8283,admit,0,fresh,41.8,0.721,0.000,1.000,0.538,2,4,1366666666
```

| column | meaning |
|---|---|
| `t` | seconds since `attach()` (CLOCK_MONOTONIC) |
| `event` | `admit`, `admit-salvage`, `retain-held`; drop reasons only if `log_drops` |
| `cam` | camera index (matches `source-bin-<i>`) |
| `slot` | `fresh` or `held` |
| `age_ms` | frame age at the decision — arrival stamp to release |
| `fresh_score`, `imp_score`, `fair_score` | the three terms of v(f), each in [0,1] |
| `value` | v(f), the weighted sum actually compared |
| `released` | frames released in this service (== k, or fewer at EOS) |
| `in_flight` | frames released but not yet completed, after this release |
| `buf_pts` | the buffer's PTS, to join against detection output |

By default (`log_drops = false`) the CSV records **admissions only**, so its row
count equals `admitted_fresh + admitted_salvage`. Verified on a run of the
example: 746 `admit` rows against 746 fresh admits. See PAPER_DIFF.md for why
drops are opt-in.

## 5. The Stats ledger invariant

```cpp
auto st = sched.stats();
assert(st.ledger_closes());   // arrivals == admitted_fresh + admitted_salvage + policy_drops
```

This is the paper's central accountability claim in one line: every frame that
arrived was either inferred or explicitly counted as a drop. Nothing vanishes.
The example asserts it on every run.

The general identity at an arbitrary instant is

```
arrivals == admitted_fresh + admitted_salvage + policy_drops + still_in_stash
```

so `ledger_closes()` is only meaningful **after `join_and_cleanup()`**, which is
what forces `still_in_stash` to zero by counting the residue as drops. Calling
it mid-run will report false whenever a stash is non-empty — that is arithmetic,
not a bug.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `could not find source-bin-0` from `attach()` | Bins not named as VISTA expects | Name them `source-bin-<i>`, or set `SchedCfg::source_bin_prefix` |
| `could not find 'tracker'` | No completion clock (R2) | Point `tracker_name` at any element downstream of nvinfer |
| Throws: `nvinfer batch-size=N but k=K` | Obligation 3 | Set the nvinfer `batch-size` property **after** `config-file-path` — the config file also carries one and the last writer wins |
| Throws: `sync-inputs=1` | Obligation 5 | `g_object_set(mux, "sync-inputs", FALSE, ...)` |
| `NOTE: mux reports batch-size=4 with k=2` | Expected on 4 cameras | Informational. The atomicity gate confirms the real value |
| `WARNING: batch atomicity gate FAILED` | Wrong mux INI (obligation 2) | Use `config/mux_vista.txt`. Numbers from that run are not comparable to the paper's |
| Nothing is ever released; pipeline hangs | `depth < 1`, or no completion clock | `depth >= 1` (rejected at construction now); check R2 |
| `WATCHDOG: no batch completion for ...` | Inference stalled or the engine is still building | First launch builds a TensorRT engine and can take minutes; the watchdog is armed only after 3 completions |
| Pipeline stalls; upstream starves | Obligation 4 | `output-buffers >= 12` on the nvvideoconvert feeding the mux |
| `WARNING: stash=1 < depth=2 with importance ON` | Activity term cannot concentrate | Set `stash >= depth` (paper: stash=2, depth=2), or use `mode=fresh` |
| Ledger does not close mid-run | Frames still in the stash | Only check after `join_and_cleanup()` (see §5) |
| Legacy mux loaded / mux has a `width` property | `USE_NEW_NVSTREAMMUX` unset | `setenv("USE_NEW_NVSTREAMMUX", "yes", 0)` **before** `gst_init` |

---

## 7. Salvage mode

`mode = "salvage"` is an **extra mode that was NOT evaluated in the paper.** It
keeps a displaced-but-important frame in a held slot and may admit it later,
under a longer staleness bound (`tau_salvage_ms`, 250 ms). It exists because the
mechanism supports it and removing it would have been a code change to the
paper's binary; it is not a contribution, it carries no measurements, and no
claim in the paper depends on it. It also admits frames **out of PTS order
across releases**, which the fresh path deliberately avoids. If you use it, you
are the first to evaluate it — treat every number it produces as unvalidated.
`fresh` is the paper's general-purpose default; `imp` is its optional extension
for demonstrable camera-activity skew, and only pays when `stash >= depth`.

---

## 8. Building

```bash
make                        # -> libvista.a
make install PREFIX=/usr/local
```

or

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
cmake --install build --prefix /usr/local
```

then, from your project:

```cmake
find_package(vista REQUIRED)
target_link_libraries(myapp PRIVATE vista::scheduler)
```

`DS_ROOT` defaults to `/opt/nvidia/deepstream/deepstream`; override with
`-DDS_ROOT=...` (CMake) or `DS_ROOT=... make`. Both paths install
`mux_vista.txt` to `share/vista/` so the INI travels with the library — it is
mechanism, and a deployment without it is not VISTA.
