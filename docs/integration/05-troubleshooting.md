# Troubleshooting

Symptom → cause → fix. Ordered roughly by how often each one is the real
problem.

Every quoted string in this page was reproduced against the module on the
reference machine, not transcribed from memory. Search for the string you got.

Jump to:

- [It refuses to start](#it-refuses-to-start)
- [It starts but the numbers are wrong](#it-starts-but-the-numbers-are-wrong)
- [It hangs, stalls, or crashes](#it-hangs-stalls-or-crashes)
- [It runs but nothing changes](#it-runs-but-nothing-changes)
- [The two warnings you must not ignore](#the-two-warnings-you-must-not-ignore)

---

## It refuses to start

### `vista: could not find source-bin-0. …`

**Cause.** VISTA looks up per-camera bins by name: `source_bin_prefix` + index,
for `0 .. num_cams-1`. Either your bins are named something else, or
`num_cams` is larger than the number of bins that exist.

**Fix.** Name your bins `source-bin-<i>`, or set `SchedCfg::source_bin_prefix`.
Check `num_cams` matches. The index is not cosmetic: camera `i` must be the bin
you link to the batcher's `sink_<i>`, because the batcher turns pad number into
`source_id`, which becomes `camera_id` in your output and the index into
`Stats::per_cam_*`.

### `vista: no 'src' pad on source-bin-0`

**Cause.** The bin exists but has no pad by that name. Usually the ghost pad
was never added, or it is named something else.

**Fix.** Expose one ghost src pad per bin, or set
`SchedCfg::source_pad_name`. It must be the *last* pad before the batcher —
VISTA both intercepts and re-pushes on this pad.

### `vista: could not find 'tracker'. …`

**Cause.** No element named `tracker` in the pipeline.

**Fix.** Point `SchedCfg::tracker_name` at **any** element downstream of
`nvinfer`. VISTA only reads batch metadata off it; it does not need a tracker.
An OSD, a sink, an `identity` — all fine, as long as it is after inference.

### `vista: mux 'stream-muxer' batch-size=4 but k=2. …`

**Cause.** Obligation 1. A release of `k` frames must land as exactly one
batch.

**Fix.** Set the batcher's `batch-size` to `k` **in code**, when you turn the
scheduler on. Do not leave it to configuration; it is a property of the
scheduler, not a tuning parameter. See
[`03-pipeline-obligations.md`](03-pipeline-obligations.md#1-the-batchers-batch-size-must-equal-k).

> Do **not** "fix" this by setting `strict = false` or `mux_name = ""`. That
> disables the check, not the problem — and `strict = false` disables the
> `sync-inputs` check too.

### `vista: nvinfer 'primary-inference' batch-size=4 but k=2. …`

**Cause.** Obligation 3. Same fix, on the inference element. Your engine should
be dynamic-batch (min 1, max N) so one engine serves any `k`.

### `vista: mux 'stream-muxer' has sync-inputs=1. …`

**Cause.** Obligation 4. Timestamp alignment and arrival-clock scheduling are
mutually exclusive: alignment drops frames upstream of VISTA's accounting,
using timestamps VISTA deliberately does not trust.

**Fix.** `sync-inputs = 0`. If you believe you need alignment, read the
measurement in the exception first — on commodity USB capture, alignment
silently erased 85.3% of arrived frames.

### `vista: mode must be off|fresh|imp|salvage, got '<x>'`

**Cause.** Typo. Note `fresh` is VISTA-Fresh and `imp` is VISTA-Activity; the
paper's names are not the mode strings.

### `vista: k must be in 1..2*num_cams`

**Cause.** `k < 1`, or `k > 2*num_cams`. The upper bound is `2*N` rather than
`N` because `salvage` mode can offer two candidates per camera.

**Fix.** Use `k ≈ N/2`. `k = N` is the all-admit ablation: with as many seats
as cameras nothing is ever rejected and the value function has nothing to
decide.

### `vista: stash must be in 1..8` / `vista: depth must be in 1..8 (depth 0 hangs)`

**Cause.** Out of range.

**On `depth = 0` specifically:** the gate reads `in_flight > (depth-1)*k`,
which at `depth = 0` is `0 > -k` — true forever, so nothing is ever released.
**The paper binary accepted `--sched-depth 0` and hung silently: no output, no
message, no error.** This module rejects it at construction. If you are reading
this because an old binary hung on you, that was it.

### `vista: at least one weight must be > 0` / `vista: weights must be non-negative`

**Cause.** All three weights zero, or one negative.

**Note.** All-zero weights are rejected, but `w_fresh = 0` alone is legal, as
is `w_fair = 0`. Be careful with the latter: fairness is what guarantees a
quiet camera gets served at all. Measured in the weight sweep: at `w_r = 0` the
quiet camera's rare event becomes a per-run coin flip, and at pure importance
it starves outright (coverage 0.03) — a camera that is never served can never
raise its own importance.

### `vista: cannot open decision CSV: <path>`

**Cause.** The parent directory does not exist, or is not writable. VISTA does
not create directories.

### `vista: num_cams must be >= 1`

**Cause.** Self-explanatory. Usually an empty camera list that was not
validated before this point.

### `vista: attach() called with a null pipeline`

**Cause.** Pipeline construction failed earlier and the error was swallowed.

---

## It starts but the numbers are wrong

### `[vista] WARNING: batch atomicity gate FAILED — only 51.0% of completed batches carry exactly k=2. …`

**This is the important one.** Everything downstream of it is invalid. The
scheduler is choosing `k` frames per service and the detector is executing
something else, so service time is not S(K), `s_hat` is wrong, and every
deadline derived from it is wrong.

**Cause and fix, read off the fill histogram** (`Stats::fill_hist`):

| Shape | Cause | Fix |
|---|---|---|
| Spike at the source count (e.g. 4 with `k=2`) | `adaptive-batching=1` — the mux overrode your `batch-size` with the source count | `adaptive-batching=0` in the INI |
| Mix of `1` and `k-1` | Deadline anchors too tight: at release time both have already expired (they are measured from the *last batch*), so the mux pushes buffer 1 immediately and the rest as a second batch | Push the anchors out — `overall-min-fps-n=2`, `overall-max-fps-n=5` |
| Spike at some other constant | Batcher `batch-size` ≠ `k` | Obligation 1 — and `strict` must be off, or it would have thrown |

Use `vista/config/mux_vista.txt`. Do **not** add a `batch-size` key to the INI:
the INI is re-read at state change, *after* your properties are set, so an INI
`batch-size` silently overrides the `k` your code sets.

Suppressing this with `gate_check = false` suppresses the message, not the
problem.

### The ledger does not close (`ledger_closes()` is false)

**Cause.** Frames are reaching the batcher without passing the arrival probe.
The drop decision is still partly the transport's, which is the thing VISTA
exists to prevent.

**Fix.** Find the second path. Common ones: a camera bin added after
`attach()`; a probe attached to the wrong pad (an inner element's pad rather
than the bin's ghost pad, so frames route around it); `num_cams` smaller than
the number of bins actually linked to the batcher — those extra cameras were
never probed and are feeding the batcher directly.

**Expected exception:** at most `stash` tail frames per camera at EOS. VISTA
lets EOS pass through and releases that camera's stashed refs; those frames are
counted as drops, so the ledger still closes. Runs that end by EOS on all
cameras close exactly. (The archived campaign observed exact closure on the
large majority of runs, and closure within ≤2 tail frames on the rest.)

### `e2e` / latency looks better than it should

**Cause.** Probe order. Probes fire in attach order. If your latency
instrumentation attaches *after* `Scheduler::attach()`, it stamps arrival at
the moment VISTA re-pushes the frame — so the time the frame spent in the stash
vanishes from your measurement.

**Fix.** Attach your instrumentation **before** `Scheduler::attach()`. The
reference app does this deliberately, and it is why its `e2e_ms` includes the
stash wait.

### Coverage is worse than expected under light load

**Not a bug.** VISTA always releases exactly `k` frames per service. When the
detector keeps up (ρ < 1) there is nothing to shed, and the always-K release
quantises admission for no benefit — measured 96.6% coverage against the stock
path's 100% at ρ = 0.84.

**Fix.** Run `mode = "off"`. The scheduler solves oversubscription; below
capacity the stock path is fine, and with the scheduler off the binary is
bit-identical to it.

---

## It hangs, stalls, or crashes

### Nothing is ever released; no error

**Cause 1: `depth = 0`** on an old binary — see above. This module rejects it.

**Cause 2: not enough candidates.** `release_once()` returns without releasing
when fewer than `k` cameras have a stashed frame *and* no camera is at EOS.
That is correct behaviour and resolves within one frame period at 30 fps/cam.
If it never resolves, your arrival hook is not firing for enough cameras —
check `Stats::arrivals` per camera (`per_cam_admits` / `per_cam_drops` are
both zero for a camera that never delivered).

**Cause 3: the gate never reopens.** The gate only reopens on a completion.
If your completion signal never fires, `in_flight` stays at `depth*k` forever.
The watchdog covers this:

```
[vista] WATCHDOG: no batch completion for 2000 ms with 4 frames in flight — resetting gate.
```

It is armed only after 3 real completions (early batches can legitimately take
seconds while TensorRT builds or loads an engine) and fires after
`max(10 * max(s_hat, 50ms), 2000ms)` of silence. **If you see it in steady
state, do not treat it as a fix.** It means VISTA is crediting releases that
never complete — your completion probe is on the wrong element, or your
inference path is dropping batches.

### Hang at shutdown

**Cause.** Teardown order. The release thread is blocked inside
`gst_pad_push`, and `join()` will wait forever.

**Fix.** `request_stop()` → `set_state(NULL)` → `join_and_cleanup()` →
`unref(pipeline)`. The NULL transition flushes the pads, which is what unblocks
the push. This is why `request_stop()` does not join. See
[`01-integration-guide.md` §5](01-integration-guide.md#5-teardown).

### Crash or corruption at shutdown

**Cause.** `gst_object_unref(pipeline)` before `join_and_cleanup()`. The
stashed buffers belong to pools owned by elements in that pipeline; unreffing
the pipeline first means unreffing buffers whose pool is gone.

**Fix.** The four-step order, in order. The destructor does `request_stop()` +
`join_and_cleanup()`, so a `Scheduler` whose scope closes before the
pipeline's ref is dropped is safe.

### Deadlock at EOS (on a modified module)

**Cause.** You made EOS wait for the stash to drain. An early version did
exactly that and deadlocked the pipeline at teardown.

**Fix.** Let EOS pass through untouched, exactly as the module does: mark the
camera done, release its stashed refs, move on. Losing at most `stash` tail
frames per camera is the right trade — steady-state benchmarks measure rates
and distributions, not totals.

### The producer blocks / capture-ring loss reappears

**Cause.** Obligation 5. Stashed frames pin pool surfaces; the pool ran out, so
the allocator blocks, so backpressure climbs back to the capture ring — the
silent newest-first drop point VISTA exists to keep drained.

**Fix.** Raise the upstream pool. In DeepStream: `nvvideoconvert
output-buffers >= 12` (its own default is 4, which is less than the stash bound
alone). If you raised `stash`, raise this with it. The bound to size against is
`(stash + 1) * N` refs held plus `d * k` in flight.

**Nothing checks this.** The only symptom is coverage you cannot explain.

---

## It runs but nothing changes

### Raising `w_imp` does nothing. The busy camera still gets its even share.

**Cause.** `stash < depth` with importance on. **This is the paper's RQ3
result, not a bug** — and the module warns about it at construction:

```
[vista] WARNING: stash=1 < depth=2 with importance ON. The activity term cannot
concentrate service through a stash shallower than the release pipeline: the
busy camera stays near its even share no matter how high w_imp is. Set
stash >= depth (paper: stash=2, depth=2). See docs/design/04-depth-and-stash.md
```

**The mechanism.** The `depth` releases of one cycle fire back-to-back,
microseconds apart. A camera offers at most one frame per release, from its
stash. With `stash = 1` its stash is empty by the second release of the burst —
the frames *did* arrive between bursts, a 1-deep stash just could not *keep*
them. So the maximum share of any camera is:

```
share = min(stash, depth) / (K * depth)
```

At `K=2, d=2, stash=1`: `1/4` — exactly the even split for 4 cameras. Measured:
26–29% share, and raising the importance weight to **0.96** buys only ~3 points
(28.7% vs 25.5% median at the default `w_i = 0.35` — both on `clips_importance`,
`impdiag_heavy` vs `impcmp_imp-k2_r{0,1,2}`). At `stash = 2`: `2/4 = 50%`
possible; measured 48%. **The weight is dominated by the cap, not defeated by
it** — turning the weight up cannot buy what retention did not keep.

The failure is **retention, not arrival**, which is why no weight gets you near
the 50% the cap allows.

**Fix.** `stash = depth` (the paper's stash-2 configuration), or `depth = 1`
(releases then space further apart than the refill period, so even a 1-deep
stash has refilled by every audition — at ~5% throughput, with e2e roughly
halved). Both were measured; see
[`docs/usage/06-tuning.md`](../usage/06-tuning.md).

> This exact misconfiguration produced a wrong conclusion in an earlier
> campaign of our own: it compared two equally-capped arms, saw nearly
> identical recall, and concluded "importance is inert when objects persist".
> The cap, not the event duration, was the gate. That is why the warning is
> loud.

### `imp_score` is 1.000 for every camera, all the time

**Cause.** Your activity signal measures **content**, not **change**. This is
the practitioner trap the paper reports: an early version of this signal
incremented on `3*new_tracks + detections` with `imp_max = 10`, and saturated
on any scene containing standing objects — median score 1.000 on every camera,
68% of admissions at ≥0.99. The term becomes a constant, and `imp` mode becomes
structurally identical to `fresh` mode while looking like it is working.

**Fix.** Count *new* objects only, and size `imp_max` to the rate you actually
expect. The shipped default (`imp_max = 2.0`, +1 per new track, 2 s half-life)
is sized so roughly 0.7 new tracks/s saturates. Any signal keyed to how *much*
is in frame will saturate the same way.

### `mode = "fresh"` but `w_imp = 0.35` — is importance on?

**No.** The mode decides, not the weight: `imp_score` is computed as 0 unless
`use_importance()` (`imp` or `salvage`). You do not need to zero `w_imp` to get
VISTA-Fresh. The decision log confirms it — the `imp_score` column is `0.000`
on every row.

### Turning the scheduler on changed nothing at all

**Check `mode`.** With `mode = "off"` the probes are never attached and the
binary is bit-identical to the stock pipeline. That is deliberate — it is what
makes an A/B honest — and it means a typo in the mode string silently gives you
the baseline. `attach()`'s banner line is the confirmation:

```
[vista] mode=fresh k=2 depth=2 stash=1 tau_max=150ms tau_salvage=250ms w=(0.40,0.35,0.25)
```

No banner, no scheduler.

---

## The two warnings you must not ignore

1. **`batch atomicity gate FAILED`** — the run is not comparable to anything.
   Fix the INI and re-run. There is no interpretation of the numbers that
   rescues them.
2. **`stash=<n> < depth=<m> with importance ON`** — the activity term cannot do
   what you are asking. Either set `stash >= depth`, or accept that you are
   running something behaviourally close to `fresh` mode with extra steps.

Everything else in this document produces either an exception or a number you
can reason about. These two produce plausible-looking output.
