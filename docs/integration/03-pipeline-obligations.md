# Host obligations — what your pipeline owes VISTA, and why

VISTA makes one structural promise: **a release of K frames is executed as
exactly one batch of K frames, immediately.** Every obligation on this page
exists to keep that promise true. Break one and the scheduler still runs, still
logs, still closes its ledger — and produces numbers that are quietly wrong.

That is the reason this page is long. Each of these was found by measurement,
usually after a run that looked fine.

| # | Obligation | Checked | Failure mode if broken |
|---|---|---|---|
| 1 | Batcher `batch-size` = `k` | throws at `attach()` | releases merge or split; service time is not S(K) |
| 2 | Batcher must not force-push a partial burst (the INI) | runtime gate warns | **silent**: batches of 4 or of 1+(K-1) |
| 3 | `nvinfer` `batch-size` = `k` | throws at `attach()` | partial batches; Ŝ and every derived deadline drift |
| 4 | `sync-inputs = 0` | throws at `attach()` | measured 85.3% of arrived frames erased |
| 5 | Upstream pool slack (`nvvideoconvert output-buffers >= 12`) | **nothing** | backpressure returns; silent capture-ring loss resumes |
| — | `USE_NEW_NVSTREAMMUX=yes` before `gst_init()` | reference app hard-errors | obligation 2 has no expression on the legacy mux |

---

## 1. The batcher's `batch-size` must equal `k`

VISTA releases exactly `k` frames per service, one `gst_pad_push` per camera
pad, back to back. It relies on the batcher completing a batch the moment its
`k`-th buffer lands (nvstreammux's `is_ready()`), and pushing it downstream
without waiting for anything else.

Set `batch-size` to anything other than `k` and that stops being true:

- **`batch-size > k`**: the mux waits for frames that are not coming — the
  release is only `k` frames — so the batch is pushed by its *deadline* rather
  than by completion. The completion clock stops being the clock; the mux's
  timer is. Under a slow-anchor INI (obligation 2) that wait is up to hundreds
  of milliseconds.
- **`batch-size < k`**: your release splits.

Enforced at `attach()` unless `strict = false`:

```
vista: mux 'stream-muxer' batch-size=4 but k=2. A release of k frames must land
as exactly one batch. Set the mux batch-size to k (see vista/README.md 'Host
obligations').
```

The reference app does this for you: turning `--sched` on sets
`mux_batch_override = k`, which stamps `batch-size = k` onto both the mux and
nvinfer. Do the same in your integration. `k` is not a mux tuning parameter —
it is a property of the scheduler that the mux must be told about.

---

## 2. The batcher must not force-push a partial burst

**This is the obligation nothing can check by reading a property, and the one
that fails silently.** It is why the runtime atomicity gate exists.

On DeepStream 7.1's new nvstreammux, batching behaviour comes from an INI file
supplied via `config-file-path`. The reference INI is `vista/config/mux_vista.txt`.
Three things about it are load-bearing, and every one of them was learned by
measuring a run that had looked fine.

### 2a. `adaptive-batching=0`

With adaptive batching on, the mux **overrides your `batch-size` with the
source count**. Measured: with `k = 2` and four cameras, releases of 2 frames
were merged into batches of **4**. The scheduler was choosing 2 frames per
service and the detector was executing 4 — obligation 1 satisfied on paper, its
purpose defeated in fact.

There is no property that reports this. The only symptom is
`num_frames_in_batch`, which is exactly what the gate watches.

### 2b. The deadline anchors must be pushed far out

```ini
overall-min-fps-n=2
overall-min-fps-d=1
overall-max-fps-n=5
overall-max-fps-d=1
```

Two frames per second is not a throttle. On this mux the fps anchors are the
*push deadline*: `overall-min-fps` sets the floor cadence at which the mux
pushes an **incomplete** batch, computed from the time of the last batch. Set
them slow and the deadline can never preempt a forming K-burst, so the burst
always completes by arrival of its K-th frame — which is the behaviour VISTA
needs.

Set them fast and the opposite happens, in a way that reads as absurd until you
trace it. With a 1 ms-deadline INI, both anchors have **already expired** by
the time a release fires (they are measured from the last batch, which was one
service time ago). The mux therefore pushes the first buffer of the burst
immediately as a batch of 1, then the remaining `k-1` as a second batch. Every
burst splits into **1 + (k-1)**. No error, no warning, no property changed.

Concretely at the anchors above: `overall-min-fps=2` → the mux will not
force-push before `last_batch + 500 ms`; `overall-max-fps=5` → not before
`last_batch + 200 ms`. Both are far longer than any service time at our
operating points, so neither ever fires first.

> **Related trap in the same family (worth knowing even though it does not
> affect VISTA):** the mux's `batched-push-timeout` **property** and the INI's
> `overall-min-fps` are the same internal knob. Measured on DS 7.1 over an
> 8-run matrix from 1 ms to 100 ms, with and without an INI: the property
> changes nothing, because the mux re-reads its INI at state change — after any
> property you set. If you want to vary the deadline, generate an INI. Do not
> set the property and believe it.

### 2c. There is deliberately **no** `batch-size` key in the INI

For exactly the reason in the note above: the INI is re-read at state change,
*after* the application sets its properties. Last writer wins, and the INI is
the last writer. A `batch-size` key in the INI would silently override the
`batch-size = k` your code sets and break burst completion — with your code
looking correct.

The absence of that key is a deliberate choice, not an oversight. Do not add
it.

### 2d. `max-same-source-frames`

`vista/config/mux_vista.txt` sets `max-same-source-frames=2`, which permits one
camera to contribute two frames to the same batch. That is required only by
`salvage` mode (a camera's fresh frame *plus* its held frame). In `fresh` and
`imp` modes VISTA offers at most one frame per camera per release, so the
setting is inert. The stock baseline INI uses `1`.

### What catches this at runtime

The batch-atomicity gate, running in-process. After 20 completions it checks
the fill histogram, and if fewer than 90% of completed batches carry exactly
`k` frames it warns once:

```
[vista] WARNING: batch atomicity gate FAILED — only 51.0% of completed batches
carry exactly k=2. Your mux INI is probably wrong: use vista/config/mux_vista.txt
(adaptive-batching=0, deadline anchors pushed out). Numbers from this run are
not comparable to the paper's. See vista/README.md 'Host obligations'.
```

**The histogram names the cause.** Read `Stats::fill_hist`:

| `fill_hist` shape | Cause |
|---|---|
| Spike at `k` | Correct. |
| Spike at the source count | `adaptive-batching=1` (§2a). |
| Mix of `1` and `k-1` | Deadline anchors too tight (§2b). |
| Spike at `batch-size` ≠ `k` | Obligation 1 — but that should have thrown at `attach()`, so `strict` is off. |

This gate is the in-process form of the campaign's pre-run gate G1
([`../reproduction/01-run-the-experiments.md`](../reproduction/01-run-the-experiments.md)),
which asserts the same histogram from outside before any policy number is
trusted.

---

## 3. `nvinfer`'s `batch-size` must equal `k`

The detector must execute the batch it is handed as one batch of `k`, at S(K).
Two reasons this matters more than it looks:

- **Service time is the scheduler's only tunable-free input.** `s_hat_ms` is an
  EWMA of measured batch service time, and both fairness deadlines are derived
  from it at runtime: `D_fair = 2*(N/k)*s_hat`, `D_hard = 4*D_fair`. That is
  what lets a heavier detector automatically earn a longer grace period with
  nothing to retune. Feed it service times for a batch size you did not
  schedule and every deadline in the system is scaled wrong.
- **Batch size changes what the detector finds.** On our engines, batch-2
  configurations reproduce 90–93% of a same-model reference configuration's
  detections on the frames they process (batch-4: 96–100%) — an FP16
  batch-size numerics effect. "Same model, different batch size" is not a
  neutral change; compare like with like. (Which batch size that reference
  configuration actually ran at is one of the discrepancies documented in this
  artifact's claim→evidence matrix; the 90–93% figure is the paper's.)

Enforced at `attach()`:

```
vista: nvinfer 'primary-inference' batch-size=4 but k=2. Partial batches change
service time. Set it to k.
```

The engine itself should be **dynamic-batch** (min 1, max N), so one engine
serves any `k`. See [`docs/usage/02-models-and-engines.md`](../usage/02-models-and-engines.md).

---

## 4. `sync-inputs` must be 0

VISTA replaces cross-camera timestamp alignment with local arrival-clock
scheduling. The two are mutually exclusive: alignment drops frames on a rule
VISTA does not know about, upstream of its accounting, using timestamps VISTA
deliberately does not trust.

The measurement behind the refusal, quoted in the exception itself: on
commodity USB capture, the decode path fabricates per-camera PTS grids that
disagree by seconds (each grid anchors at its own camera's first frame, and USB
cameras enumerate one after another). A batcher told to align on those stamps
**silently erased 85.3% of arrived frames** in a controlled 120 s live A/B —
while the cameras' true capture instants already interleaved within a few
milliseconds.

Enforced at `attach()`:

```
vista: mux 'stream-muxer' has sync-inputs=1. VISTA replaces timestamp alignment
with local arrival-clock scheduling; on commodity USB capture the fabricated PTS
grids disagree by seconds and sync-inputs silently erased 85.3% of arrived
frames in our measurements. Set sync-inputs=0.
```

The reference app additionally refuses at config-load time
(`--sched requires sync-inputs=0 (the scheduler replaces alignment).`).

---

## 5. Upstream buffer-pool slack

**Nothing checks this one. It is yours.**

The stash holds `GstBuffer` refs, not pixel copies. Every stashed frame pins
one surface in the pool of whatever element allocated it — in the reference
pipeline, the per-camera `nvvideoconvert` that produces NVMM NV12. The pool has
a fixed size. Pin too many and the converter cannot allocate an output buffer,
so it blocks; the decoder blocks behind it; and backpressure climbs back to the
capture ring — which is precisely the silent, uncounted, newest-first drop
point VISTA exists to keep drained. You would have reintroduced the problem
inside the fix, and it would not show up as an error. It would show up as
coverage you cannot explain.

The bound is small and exact:

```
frames pinned inside the scheduler = (stash + 1) * N      // +1 = the held slot,
                                                          // salvage mode only
frames in flight downstream         = d * k
```

At the paper's defaults with 4 cameras: `(1+1)*4 = 8` refs held, `2*2 = 4` in
flight. The reference app sets `nvvideoconvert output-buffers = 12` when the
scheduler is on. The element's own default is **4** — verified with
`gst-inspect-1.0 nvvideoconvert` on this machine — which is less than the
stash bound alone.

Rule of thumb: give the pool `(stash + 1) + headroom` surfaces per camera, and
never fewer than the 12 the reference app uses. If you raise `stash`, raise
this with it.

On a Jetson (unified-memory SoC) those surfaces come out of the same physical
LPDDR as host RAM; on a discrete GPU they are VRAM across PCIe. The pointers
and counters are ordinary heap. Only the frame terms above scale with a knob.

---

## `USE_NEW_NVSTREAMMUX=yes`, before `gst_init()`

The environment variable decides which nvstreammux implementation the plugin
registers. It must be set before `gst_init()`; setting it afterwards does
nothing. The reference app does:

```c
setenv("USE_NEW_NVSTREAMMUX", "yes", 0);   // overwrite=0: an explicit setting wins
```

and then verifies the result rather than trusting it: the legacy mux has a
`width` property and the new one does not, so finding `width` means the switch
did not take. That produces a hard error naming the fix, rather than a subtly
different pipeline:

```
The LEGACY nvstreammux was loaded, but this app is written for the NEW mux. Run
with USE_NEW_NVSTREAMMUX=yes (the app sets it automatically unless your
environment overrides it — check `echo $USE_NEW_NVSTREAMMUX`).
```

This is a precondition of the *reference pipeline*, not of VISTA itself. VISTA
needs a batcher that satisfies obligations 1–4; the new mux is one that can. The
legacy mux has no INI and no equivalent of obligation 2 — it pushes on "all pads
delivered OR timeout" — so it cannot express the contract.

Copy the verify-don't-trust pattern. It costs four lines and turns a class of
silent-wrong into a startup error.
