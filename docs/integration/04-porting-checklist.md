# Porting VISTA to a non-DeepStream pipeline

The scheduler in `vista/` is bound to GStreamer/DeepStream: it takes a
`GstElement*`, uses pad probes, and reads `NvDsBatchMeta`. But the *design*
needs only three things from a host, none of them DeepStream-specific. This
page maps those three onto whatever you have, and lists what you must replace.

If you are on DeepStream, you do not need this page — you need
[`01-integration-guide.md`](01-integration-guide.md).

---

## The three requirements, and what supplies them

| # | Requirement | DeepStream binding today | What you replace it with |
|---|---|---|---|
| (i) | An interception point before the shared batcher, at which you take custody of a frame and tell the transport "delivered" | Buffer probe on each `source-bin-<i>` ghost src pad; `gst_buffer_ref` then `return GST_PAD_PROBE_DROP` | **An arrival hook that can return DROP.** See §1. |
| (ii) | An inference-completion signal carrying the number of frames completed | Buffer probe on the tracker src pad reading `NvDsBatchMeta::num_frames_in_batch` | **A completion callback carrying a frame count.** See §2. |
| (iii) | A monotonic local clock | `g_get_monotonic_time() / 1e6` | **Any monotonic clock in seconds.** See §3. |

Nothing else in the design touches the framework. In particular VISTA does
**not** need: camera timestamps, camera synchronisation, knowledge of the
batcher's internals, access to pixels, or a GPU.

---

## 1. The arrival probe → your arrival hook

**What it must do**

```
on_arrival(camera_index, frame):
    take ownership of `frame`                 # a ref, a handle, a shared_ptr
    stash it (displacing the oldest if full)
    tell the transport the frame was CONSUMED # <-- the load-bearing half
```

**What "consumed" must mean.** The transport must believe delivery succeeded
and move on. This is the entire mechanism: if the producer thinks the frame was
delivered, it never blocks, so backpressure never forms, so the upstream
capture buffer never fills, so the newest frames are never silently overwritten.

The property you need is: **no frame can reach the batcher except by being
pushed by the scheduler's release thread.** If frames can arrive by any other
route, you have not moved the drop decision — you have added a second one. The
diagnostic is exact and cheap: `Stats::ledger_closes()`. If arrivals ≠
admissions + drops, something is bypassing you.

**What this looks like in other hosts**

| Host | Interception point | "Consumed" is |
|---|---|---|
| GStreamer | pad probe on the source's src pad | `GST_PAD_PROBE_DROP` after a `gst_buffer_ref` |
| A callback-based SDK | the per-camera frame-ready callback | return without forwarding; copy or retain the handle |
| Your own capture loop | the point where you currently enqueue to the batcher | enqueue to the stash instead; the loop continues |
| A ring buffer you own | the producer's write path | you already own the drop decision — the win here is smaller, but the *value ordering* still applies |

**Re-entrancy.** The release thread pushes frames back through the same path
the arrival hook watches. You need a way for the hook to recognise its own
scheduler's traffic and pass it through. VISTA uses a `thread_local bool`
(`t_vista_pushing`) set around the push, because the push always happens on the
release thread. If your host pushes from a different thread, or hands the frame
back through a different entry point, use a flag on the frame instead — but
make sure it cannot be confused with a genuine arrival, or released frames will
be re-stashed and never run.

**Ownership.** The stash holds references, not copies. Whatever you retain must
(a) keep the pixels alive and (b) be released on drop, on eviction, at EOS, and
in `join_and_cleanup()`. This is why obligation 5 (pool slack) exists: a
retained frame pins a slot in *someone's* pool, and that pool is finite. Count
your bound before you raise `stash`:

```
pinned inside the scheduler = (stash + 1) * N     # +1 = the salvage held slot
in flight downstream        = d * k
```

## 2. The completion probe → your completion callback

**What it must do**

```
on_completion(batch):
    frames = number of frames in this batch     # NOT batches
    in_flight -= frames
    s_hat = 0.8*s_hat + 0.2*(now - release_time_of_oldest_outstanding_release)
    (optional) update per-camera activity from whatever the batch reports
    wake the release thread
```

**Why a frame count and not a batch count.** `in_flight` is counted in frames
because the release gate is `in_flight <= (depth-1)*k` and a degraded batcher
can hand you a batch that is not `k` frames. Counting batches would make the
gate silently wrong in exactly the situation the gate exists to survive. VISTA
also clamps at zero on the subtraction, for the same reason.

**Where the signal may come from.** Anywhere downstream of inference. VISTA's
DeepStream binding reads the tracker's src pad only because that is a
convenient place to see completed batch metadata — the header says so
explicitly and `tracker_name` accepts any element. In your host: the
`enqueue`/`dequeue` completion of your inference runtime, a CUDA stream
callback, the return of a synchronous `infer()` call, the point where results
reach your consumer. All that matters is that it fires **once per completed
batch**, **after** the work is done, and knows **how many frames** were in it.

**This is the clock.** Sustained release rate ≡ completion rate, for any
detector, with nothing to tune. If your completion signal fires early
(on submission rather than completion), you have built a timer, not a clock,
and the scheduler will overcommit. The watchdog will not save you — it only
fires when completions *stop*.

**Activity (only if you use `imp` mode).** VISTA's importance signal is
per-camera "a new object appeared", derived from tracker IDs it has not seen
before on that camera. If your host has no tracker, you need some other
change-keyed, camera-level signal. It must measure **change, not content**: the
first version of this signal counted objects in view and saturated at `I_max`
on any scene with standing objects — median score 1.000 on every camera —
silently turning the term into a constant. Any signal keyed to *how much* is in
frame will do the same.

## 3. The clock

`g_get_monotonic_time()` returns microseconds from a monotonic source; VISTA
divides by 1e6 and works in seconds throughout. Replace with
`std::chrono::steady_clock` or `clock_gettime(CLOCK_MONOTONIC, ...)`.

Requirements: **monotonic** (never steps backwards, unaffected by NTP), and
**local** (the host's own clock, not the stream's). Do not substitute PTS,
capture timestamps, or anything cross-camera. That substitution is the mistake
the design exists to avoid — see obligation 4 in
[`03-pipeline-obligations.md`](03-pipeline-obligations.md) for the 85.3%
measurement.

---

## What you keep, unchanged

Everything that makes VISTA *VISTA* is host-independent and should be ported
verbatim, not reimplemented:

- the value function `v(f) = w_f*fresh(f) + w_i*imp(c) + w_r*fair(c)` and the
  two normalised terms `fresh(f) = max(0, 1 - age/tau_max)` and
  `fair(c) = min(1, (now - t_served(c)) / D_fair)`;
- the runtime-derived deadlines `D_fair = 2*(N/k)*s_hat`, `D_hard = 4*D_fair`;
- forced-first selection (a camera past `D_hard` is admitted whatever its
  score), then top-K by value;
- oldest-first offering from the stash, so consecutive releases drain a camera
  in ascending timestamp order;
- `tau_max` eviction before every release;
- the release gate `in_flight <= (depth-1)*k`;
- the ledger: `arrivals == admitted_fresh + admitted_salvage + policy_drops`.

These are ~200 lines with no framework in them. The port is the three hooks.

---

## Checklist

Work top to bottom; each step has a check that fails loudly.

- [ ] **Arrival hook takes custody and returns DROP.** Check: with the
      scheduler releasing nothing, the batcher receives nothing and your
      producer does not block.
- [ ] **Released frames are not re-stashed.** Check: releases actually reach
      the batcher; `Stats::releases` grows.
- [ ] **Completion callback fires once per completed batch, after the work,
      with a frame count.** Check: `Stats::completions` tracks batches 1:1 and
      `s_hat_ms` settles near your measured batch service time.
- [ ] **Clock is monotonic and local.** Check: `age_ms` in the decision log is
      never negative and never exceeds `tau_max` on an admitted `fresh` row.
- [ ] **Batcher batches exactly `k`.** Check: `Stats::fill_hist` is a spike at
      `k`. This is obligation 1+2 in your host's terms — the atomicity gate
      does not care how your batcher works, only what it produces.
- [ ] **Pool slack.** Check: raise `stash` by one and confirm your producer
      still does not block.
- [ ] **Ledger closes.** Check: `Stats::ledger_closes()` is true at teardown.
      This is the one that catches "frames are still reaching the batcher
      behind my back".
- [ ] **Teardown order.** Check: no hang at shutdown, no use-after-free. The
      release thread may be blocked inside your push; something must flush it
      before you join. See
      [`01-integration-guide.md` §5](01-integration-guide.md#5-teardown).
