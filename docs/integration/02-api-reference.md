# VISTA API reference

Every public symbol in `vista/include/vista/vista_scheduler.hpp`, with defaults
verified by compiling and running against the module on the reference machine
(Jetson AGX Orin, DeepStream 7.1, g++ 11.4.0, GStreamer 1.20.3).

Namespace: `vista`. Single header: `vista/vista_scheduler.hpp`. Single
translation unit: `vista/src/vista_scheduler.cpp`.

Contents:

- [`struct SchedCfg`](#struct-schedcfg)
- [`class Scheduler`](#class-scheduler)
- [`struct Stats`](#struct-stats)
- [`thread_local bool t_vista_pushing`](#thread_local-bool-t_vista_pushing)
- [Exception strings](#exception-strings-verbatim)
- [Warning strings](#warning-strings-verbatim)

---

## `struct SchedCfg`

The whole configuration surface. Copied by value into the `Scheduler` at
construction; changing your `SchedCfg` afterwards has no effect.

A default-constructed `SchedCfg` with `mode = "fresh"` is exactly **VISTA-Fresh
at the paper's operating point**.

### Policy

| Field | Default | Unit | Reference-app flag | Paper symbol |
|---|---|---|---|---|
| `std::string mode` | `"off"` | `off` \| `fresh` \| `imp` \| `salvage` | `--sched MODE` | policy (VISTA-Fresh = `fresh`, VISTA-Activity = `imp`) |
| `int k` | `2` | frames per release | `--sched-k N` | *K* |
| `int depth` | `2` | batches allowed in flight | `--sched-depth N` | *d* (release depth) |
| `int stash` | `1` | frames retained per camera | `--sched-stash N` | *stash* |
| `double tau_max_ms` | `150.0` | ms | `--sched-tau-max MS` | *τ_max* |
| `double tau_salvage_ms` | `250.0` | ms | `--sched-tau-salvage MS` | *τ_salv* (salvage only; not in the paper) |
| `double w_fresh` | `0.40` | weight | `--sched-w F,I,R` | *w_f* |
| `double w_imp` | `0.35` | weight | `--sched-w F,I,R` | *w_i* |
| `double w_fair` | `0.25` | weight | `--sched-w F,I,R` | *w_r* |
| `double imp_halflife_s` | `2.0` | s | *(none — new field)* | activity EWMA half-life |
| `double imp_max` | `2.0` | activity events | *(none — new field)* | *I_max* |
| `double retention_thresh` | `0.30` | normalised score in [0,1] | *(none — new field)* | salvage retention threshold (not in the paper) |

Notes that the table cannot carry:

- **`k` must equal the batcher's and the detector's `batch-size`.** This is not
  a convention; it is checked at `attach()`. See
  [`03-pipeline-obligations.md`](03-pipeline-obligations.md).
- **`depth`** is a credit ceiling, not a buffer. The release gate is
  `in_flight <= (depth-1) * k`, counted in **frames**, not batches.
- **`stash` must be `>= depth` for the activity term to do anything.** The
  `depth` releases of one cycle fire microseconds apart, and a camera can offer
  at most one frame per release from its stash. With a 1-deep stash the busy
  camera's stash is empty by the second release, capping it near its even share
  no matter how large `w_imp` is. With importance **off**, `stash = 1` is
  optimal (pure keep-newest), not a limitation. Setting `use_importance()` with
  `stash < depth` warns at construction; it does not throw, because it is legal
  and was measured.
- **`w_imp` is ignored in `fresh` mode.** The mode, not the weight, decides
  whether the activity term contributes: `imp_score` is computed as 0 unless
  `use_importance()`. You do not need to zero `w_imp` to get VISTA-Fresh.
- **`imp_max = 2.0` is sized so roughly 0.7 new tracks/s saturates it.** The
  first version of this signal used `imp_max = 10` with a `3*new_tracks + dets`
  increment and saturated on any scene containing standing objects — median
  score 1.000 on every camera — silently turning the activity term into a
  constant. The lesson generalises: any signal keyed to *how much* is in frame
  does this. Importance must measure *change*.
- **`tau_salvage_ms` and `retention_thresh` only matter in `salvage` mode**,
  which was **not evaluated in the paper**. See
  [`docs/usage/06-tuning.md`](../usage/06-tuning.md) before enabling it.

### Pipeline binding

VISTA locates its probe points by element name via `gst_bin_get_by_name()`.

| Field | Default | Points at |
|---|---|---|
| `std::string source_bin_prefix` | `"source-bin-"` | Per-camera bins `<prefix>0 .. <prefix>(num_cams-1)`. |
| `std::string source_pad_name` | `"src"` | The ghost src pad on each such bin. |
| `std::string tracker_name` | `"tracker"` | Any element **downstream of nvinfer**; only its batch metadata is read. |
| `std::string tracker_pad_name` | `"src"` | The pad on that element to observe. |
| `std::string mux_name` | `"stream-muxer"` | The batcher. Used **only** for obligation checks. `""` disables them. |
| `std::string pgie_name` | `"primary-inference"` | The inference element. Used **only** for the obligation check. `""` disables it. |

### Diagnostics

| Field | Default | Meaning |
|---|---|---|
| `std::string decision_csv` | `""` | Path for the per-decision audit log. `""` = no log. Opened (`"w"`) at construction; throws if it cannot be opened. |
| `bool log_drops` | `false` | `false` is paper-identical: the decision CSV records **admissions only**. Drop rows add file I/O to the arrival path of a timing-sensitive scheduler, so they are opt-in. Drop *counts* are always exact regardless. |
| `bool strict` | `true` | Throw at `attach()` on a verifiable host-obligation violation. `false` skips **all three** attach-time checks. |
| `bool gate_check` | `true` | Warn on stderr if batch atomicity fails at runtime. |

### Predicates

```cpp
bool enabled()        const;  // mode != "off"
bool use_importance() const;  // mode == "imp" || mode == "salvage"
bool use_salvage()    const;  // mode == "salvage"
```

All three are `const` and cheap; they are string comparisons on `mode`.

---

## `class Scheduler`

```cpp
Scheduler(const SchedCfg& cfg, int num_cams);
~Scheduler();

void  attach(GstElement* pipeline);
void  request_stop();
void  join_and_cleanup();
void  print_summary() const;
Stats stats() const;
```

Non-copyable in practice (it owns a `std::thread`, a `std::mutex` and a
`FILE*`). Construct one per pipeline.

### `Scheduler(const SchedCfg& cfg, int num_cams)`

Validates `cfg`, sizes the per-camera state, and opens the decision CSV.
Throws `std::runtime_error` on an invalid configuration — see
[Exception strings](#exception-strings-verbatim). Does **not** touch
GStreamer.

`num_cams` must be `>= 1` and must match the number of `source-bin-<i>`
elements `attach()` will look for.

### `void attach(GstElement* pipeline)`

Checks host obligations, finds the pads, attaches the arrival/EOS/completion
probes, and starts the release thread. Prints one configuration line to stderr:

```
[vista] mode=fresh k=2 depth=2 stash=1 tau_max=150ms tau_salvage=250ms w=(0.40,0.35,0.25)
```

(plus ` (decision log on)` when `decision_csv` is set).

Two ordering constraints:

- **Call it before the pipeline goes to `PLAYING`.**
- **Call it after any probe of yours that must stamp arrivals first.** Probes
  fire in attach order. This is why the reference app's `e2e_ms` includes the
  stash wait rather than hiding it.

Throws if the pipeline is null, if a required element or pad is missing, or —
when `cfg.strict` — if the mux/nvinfer `batch-size` disagrees with `k` or the
mux has `sync-inputs=1`.

### `void request_stop()` / `void join_and_cleanup()`

Two-phase teardown; the order relative to the pipeline's NULL transition is
load-bearing. See [`01-integration-guide.md` §5](01-integration-guide.md#5-teardown).

`request_stop()` sets the stop flag and notifies the condition variable. It
does not join, because the release thread may be blocked inside
`gst_pad_push` — only flushing the pads (the NULL transition) returns it.

`join_and_cleanup()` joins the thread, unrefs every stashed `GstBuffer`,
releases the pad refs, and flushes the decision CSV. Idempotent enough to be
called by the destructor after you have already called it.

### `void print_summary() const`

Writes one line to stderr:

```
[vista] fresh: 1186 releases (22.1/s), 2372 fresh + 0 salvage admitted, 3805 policy drops, s_hat 89.5 ms over 53.7 s.
```

> **This line's format is a parsing contract.** `harness/run_gates.sh` recovers
> the drop ledger by matching it against a run's `stderr.log`, and gate G2 is
> that match. The paper binary printed a `[sched]` prefix; this module emits
> `[vista]`. Parsers must accept both — everything after the prefix is
> byte-identical between them. See `../../NAMING.md`.

### `Stats stats() const`

Snapshot of the counters, taken under the scheduler's mutex. Safe to call at
any time, including while running.

---

## `struct Stats`

```cpp
struct Stats {
  long arrivals = 0;
  long admitted_fresh = 0;
  long admitted_salvage = 0;
  long policy_drops = 0;
  long releases = 0;
  long completions = 0;
  double s_hat_ms = 0.0;
  double elapsed_s = 0.0;
  std::vector<long> per_cam_admits;
  std::vector<long> per_cam_drops;
  std::map<int, long> fill_hist;

  bool ledger_closes() const;
};
```

| Field | Unit | Meaning |
|---|---|---|
| `arrivals` | frames | Every frame the arrival probe took custody of. |
| `admitted_fresh` | frames | Admitted from a camera's `fresh` deque. |
| `admitted_salvage` | frames | Admitted from a camera's `held` slot (salvage mode only). |
| `policy_drops` | frames | Displaced, evicted at `tau_max`, or released at EOS. Every one is a counted decision. |
| `releases` | releases | Number of release events (**not** frames: a release is normally `k` frames). |
| `completions` | batches | Completed batches observed at the completion probe. |
| `s_hat_ms` | ms | EWMA of batch service time (`0.8*old + 0.2*new`), seeded at 50.0, measured from the release FIFO. `D_fair = 2*(N/k)*s_hat` and `D_hard = 4*D_fair` derive from it. **Verify it against your measured batch service time before trusting it** — in 43 of the 107 archived scheduler runs the final value exceeds the run's median measured service time by more than 2×, and by 11–15× in one campaign. See [`docs/usage/06-tuning.md` §7](../usage/06-tuning.md#7-d_fair--d_hard-and-a-caveat-about-s_hat). |
| `elapsed_s` | s | Seconds since `attach()`. |
| `per_cam_admits` | frames | Indexed by camera: `admitted_fresh + admitted_held`. |
| `per_cam_drops` | frames | Indexed by camera. |
| `fill_hist` | count | Completed-batch fill → count. Under a correct configuration this is a spike at `k`. |

### `bool ledger_closes() const`

```cpp
return arrivals == admitted_fresh + admitted_salvage + policy_drops;
```

This is the paper's exact-accounting claim, as an assertion you can run. It
must hold on every run. If it does not, frames are reaching the batcher
without passing the arrival probe — which means the drop decision is still
partly the transport's.

`fill_hist` is the other one worth asserting on: it should be a spike at `k`.
Its shape names the failure when it is not (see
[`05-troubleshooting.md`](05-troubleshooting.md)).

---

## `thread_local bool t_vista_pushing`

```cpp
extern thread_local bool t_vista_pushing;
```

Set to `true` while the release thread is inside `gst_pad_push`, so that the
arrival probe lets the scheduler's own traffic through instead of stashing it
again. Without it, a released frame would re-enter the stash and never reach
the batcher.

You almost certainly never touch this. Two facts if you do:

- It is **one process-wide `thread_local`**. Two `Scheduler` instances share
  the symbol. This is benign: each only ever pushes from its own release
  thread, and a `thread_local` on that thread is private to it.
- If you add your own probe on the same pads and need to distinguish a
  scheduler release from a genuine arrival, read this flag. That is what it is
  for.

---

## Exception strings, verbatim

All of these are `std::runtime_error`. The list below was produced by
constructing/attaching the real module with each fault on the reference
machine, not by reading the source — readers search for these strings, so they
must match byte for byte.

### From the constructor

```
vista: num_cams must be >= 1
vista: mode must be off|fresh|imp|salvage, got '<mode>'
vista: k must be in 1..2*num_cams
vista: stash must be in 1..8
vista: depth must be in 1..8 (depth 0 hangs)
vista: tau_max_ms must be > 0
vista: imp_max must be > 0
vista: imp_halflife_s must be > 0
vista: weights must be non-negative
vista: at least one weight must be > 0
vista: cannot open decision CSV: <path>
```

`depth 0 hangs` is not a stylistic note. The gate reads
`in_flight > (depth-1)*k`; at `depth = 0` that is `0 > -k`, true forever. The
paper binary accepted `--sched-depth 0` and hung with no output and no
message. This bound closes that.

### From `attach()`

```
vista: attach() called with a null pipeline

vista: could not find <source_bin_prefix><i>. VISTA intercepts frames on each camera's source bin; name your bins '<source_bin_prefix><i>' or set SchedCfg::source_bin_prefix.

vista: no '<source_pad_name>' pad on <source_bin_prefix><i>

vista: could not find '<tracker_name>'. VISTA needs an inference-completion signal: point SchedCfg::tracker_name at any element downstream of nvinfer.

vista: '<tracker_name>' has no '<tracker_pad_name>' pad.
```

### From the obligation checks (`attach()`, `strict = true` only)

```
vista: mux '<mux_name>' batch-size=<n> but k=<k>. A release of k frames must land as exactly one batch. Set the mux batch-size to k (see vista/README.md 'Host obligations').

vista: nvinfer '<pgie_name>' batch-size=<n> but k=<k>. Partial batches change service time. Set it to k.

vista: mux '<mux_name>' has sync-inputs=1. VISTA replaces timestamp alignment with local arrival-clock scheduling; on commodity USB capture the fabricated PTS grids disagree by seconds and sync-inputs silently erased 85.3% of arrived frames in our measurements. Set sync-inputs=0.
```

An element whose name is `""`, or which is not in the pipeline, is skipped —
the property read falls back to a value that passes. That is a deliberate
escape hatch for non-DeepStream hosts, and a trap if you use it to silence an
error you should be fixing.

---

## Warning strings, verbatim

These go to stderr and do **not** throw. All three were reproduced on the
reference machine.

### Non-unit weight sum (construction)

```
[vista] WARNING: weights sum to 1.100, not 1.0 (w_fresh=0.50 w_imp=0.35 w_fair=0.25). Scores are compared, not thresholded, so this only rescales v(f) — but check it.
```

Harmless by construction — `v(f)` is only ever compared against other `v(f)` —
and almost always a typo.

### `stash < depth` with importance on (construction)

```
[vista] WARNING: stash=1 < depth=2 with importance ON. The activity term cannot concentrate service through a stash shallower than the release pipeline: the busy camera stays near its even share no matter how high w_imp is. Set stash >= depth (paper: stash=2, depth=2). See docs/design/04-depth-and-stash.md
```

This is the paper's RQ3 result, fired at you at construction time. The
configuration is legal and was measured; it just cannot do what you probably
think it does.

### Batch atomicity gate failed (runtime, after ≥20 completions)

```
[vista] WARNING: batch atomicity gate FAILED — only <p>% of completed batches carry exactly k=<k>. Your mux INI is probably wrong: use vista/config/mux_vista.txt (adaptive-batching=0, deadline anchors pushed out). Numbers from this run are not comparable to the paper's. See vista/README.md 'Host obligations'.
```

Fires once per run, at the first check after 20 completions, when fewer than
90% of completed batches carry exactly `k` frames. Suppressed with
`gate_check = false` — which suppresses the message, not the problem.

### Watchdog (runtime)

```
[vista] WATCHDOG: no batch completion for <ms> ms with <n> frames in flight — resetting gate.
```

Armed only after 3 real completions (the first batches can legitimately take
seconds while TensorRT loads an engine), and fires when there has been no
completion for `max(10 * max(s_hat, 50ms), 2000ms)` with frames outstanding.
It resets `in_flight` to 0 so the pipeline recovers rather than deadlocking. If
you see this in steady state, your completion signal is wrong — VISTA is
crediting releases that never complete.

### Push failure (runtime)

```
[vista] push on cam <i> returned <GstFlowReturn name>
```

`GST_FLOW_EOS` and `GST_FLOW_FLUSHING` are expected at teardown and are handled
silently (the camera's pad is marked dead). Anything else prints this line.
