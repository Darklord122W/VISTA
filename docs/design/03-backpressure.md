# Backpressure — the full causal chain

*Where frames actually die, link by link. Companion to
`02-deepstream-pipeline.md` and `04-depth-and-stash.md`. All line references are
to the module as shipped in this repository
(`vista/src/vista_scheduler.cpp`, `vista/include/vista/vista_scheduler.hpp`);
the authors' working tree has the same mechanism at different line numbers.*

Backpressure is the mechanism the whole VISTA design is organized around. The
stock pipeline lets it propagate all the way to the camera driver, where frames
die silently. VISTA **severs** that chain at the stash and replaces it with an
explicit, counted, self-imposed credit gate.

---

## 1. The chain, both ways

- **Stock:** GPU saturates → queues fill → the blocking push travels *upstream*
  element by element → `v4l2src` stops dequeuing → the **kernel capture ring
  fills** → the driver **silently drops the newest frame**. Uncounted, and
  before any instrumentation.
- **VISTA:** the arrival probe **intercepts every frame and returns `DROP`**, so
  the camera-side thread **never blocks on the mux**. The chain is cut at the
  source-bin src pad. GPU load is regulated instead by a **credit gate the
  scheduler applies to itself** — it withholds *release*, it never stalls the
  camera. The only way backpressure returns is indirectly, through **NVMM
  buffer-pool exhaustion** if the stash is sized too deep (§6) — which is
  exactly why the stash is bounded.

## 2. What "backpressure" actually is

A GStreamer `gst_pad_push()` is **synchronous and blocking**. When element A
pushes a buffer to element B, A's streaming thread is stuck inside that call
until B (and everything downstream of B) has accepted it. If B is full or slow,
the push does not return — so **A's thread stops**, which means A stops pulling
from *its* upstream, and so on. That "stop" travelling from a slow consumer back
toward the producer **is** backpressure.

It is not an error path. It is normal, correct GStreamer flow control. That is
what makes it dangerous: nothing logs it.

A `queue`'s three `leaky` modes are the three ways to *not* propagate it:

| `leaky` | when full it… | drops | net effect |
|---|---|---|---|
| `0` none | blocks upstream | nothing | **propagates** backpressure |
| `1` upstream | refuses the incoming buffer | **newest** | absorbs it, loses fresh frames |
| `2` downstream | evicts the queued buffer | **oldest** | absorbs it, keeps fresh frames |

## 3. The stock chain — propagation all the way to silent loss

With `mode=off` and stock buffer pools, nothing breaks the chain. Under
overload (`rho > 1`):

```
  nvinfer saturated (rho > 1): completes slower than frames arrive
        |  work piles up
        v
  nvstreammux input queue full
        |  sink pad blocks
        v
  nvvideoconvert output pool drained
        |  no free surface to write into
        v
  nvjpegdec / nvvideoconvert stall
        |  upstream push blocks
        v
  v4l2src stops calling DQBUF
        |  no buffer re-queued
        v
  kernel capture ring fills
        |  incoming DMA has nowhere to land
        v
  driver DROPS THE NEWEST frame   X silent, uncounted, pre-instrumentation
```

Two properties make this the failure the paper exists to fix:

1. **The loss is at the top of the chain but caused at the bottom.** The GPU is
   the bottleneck; the frame dies in the kernel ring — the one place no
   userspace counter can see it.
2. **It is stale-biased.** The ring delivers FIFO (oldest first) and drops the
   newest, so the survivors are the *old* frames.

The alternative stock configuration — **deep buffer pools** — doesn't drop, but
only by letting the queue grow: staleness ≈ pool depth ÷ throughput (measured
857 ms at the deep replay default). No free lunch: either silent drops or
unbounded age.

## 4. How VISTA severs the chain

VISTA puts a BUFFER probe on every source-bin ghost src pad, *upstream of the
mux*. Two things break the chain:

```c
// vista_scheduler.cpp:260-269 — arrival_probe
if (t_vista_pushing) return GST_PAD_PROBE_OK;   // our own release: pass through
...
gst_buffer_ref(buf);                            // take our own ref
ctx->self->on_arrival(ctx->cam, buf);
return GST_PAD_PROBE_DROP;                      // pull the buffer OUT of the dataflow
```

Returning `DROP` means the buffer **never travels toward the mux on the camera's
thread**. And `on_arrival` (`vista_scheduler.cpp:304-333`) **never blocks** — it
either has room or displaces its own oldest, then returns at once:

```c
// vista_scheduler.cpp:314-330 — on_arrival, no wait, ever
if (static_cast<int>(c.fresh.size()) >= cfg_.stash) {
  Slot victim = c.fresh.front();
  c.fresh.pop_front();          // displace the OLDEST — counted
  ...
  drop_slot(victim, cam, "displace");
}
c.fresh.push_back(Slot{buf, now});   // then return
```

```
  frame reaches source-bin src pad
        v
  arrival_probe: ref + return GST_PAD_PROBE_DROP
        v
  on_arrival()
        v
  stash full? --yes--> displace OLDEST (counted drop)
        |                       |
        +--no--> push_back      |
                    |           |
                    v           v
              returns immediately (no gst_pad_push, no wait)
                    v
  camera thread never blocks -> v4l2src keeps DQBUF ->
  kernel ring stays DRAINED -> no silent drop
```

**Net:** the mux/GPU side and the camera side are fully decoupled. A busy GPU
can no longer reach back through the mux to stall `v4l2src`, because the stash
is a wall the push never crosses. Frame loss still happens under overload — but
now it is **in the stash, oldest-first, counted, and value-aware** instead of
**in the kernel, newest-first, silent, and blind**.

The chosen frames re-enter later via the scheduler's *own* `gst_pad_push`
through the same pad, flagged so the probe lets them through
(`vista_scheduler.cpp:622-635`).

## 5. The replacement: backpressure the scheduler applies to itself

Removing propagation does not remove the need to not overwhelm the GPU. VISTA
does that with the **credit gate** — which is *not* GStreamer backpressure.
Nothing blocks; the scheduler simply **declines to release**:

```c
// vista_scheduler.cpp:491 — the gate
if (in_flight_.load() > static_cast<long>((cfg_.depth - 1) * cfg_.k))
  return false;                 // don't push; frames wait in the stash
```

- **Release** spends credit: `in_flight_.fetch_add(items.size())`
  (`vista_scheduler.cpp:610`).
- **Completion** returns credit: `in_flight_.fetch_sub(frames)`
  (`vista_scheduler.cpp:404`), and wakes the scheduler.

GPU load is capped at `d` batches in flight — the same ceiling ordinary
backpressure would enforce — but expressed as **a counter the scheduler reads**,
never as a blocked thread. Two consequences:

1. **The camera never feels it.** Credit exhaustion parks frames in the stash;
   it does not stall `v4l2src`. The kernel ring stays drained regardless of GPU
   state.
2. **Waiting is bounded.** A parked frame is evicted at `tau_max = 150 ms`
   (`vista_scheduler.cpp:495-505`), so "held back by the gate" can never become
   "unboundedly stale" — which is the deep-pool baseline's disease.

That is the whole trade: replace *implicit, propagating, unbounded* backpressure
with *explicit, local, bounded* admission control.

> Note the accounting unit. `in_flight_` counts **frames**, not batches: release
> adds K, completion subtracts `num_frames_in_batch`, and the gate compares
> against `(depth-1)*k`. Counting batches would be wrong the moment a batch
> completes split or short.

## 6. The one residual chain — NVMM pool pressure

VISTA severs the *flow-control* chain, but not the *memory* chain. Every stashed
frame holds a `GstBuffer` that pins an **NVMM surface** from the
`nvvideoconvert` output pool (`output-buffers = 12`). Hold too many and the pool
runs dry — and an empty pool re-creates backpressure the ordinary way:

```
  stash too deep: (stash+1)*N surfaces pinned
        v
  converter output pool (output-buffers=12) exhausted
        v
  nvvideoconvert can't allocate an output surface
        v
  backpressure RE-PROPAGATES: decoder stalls -> v4l2src stops DQBUF
        v
  kernel ring fills -> driver drops NEWEST   X silent
        v
  => an over-deep stash returns to the exact silent-drop zone the design removes
```

This is why:

- the stash is **bounded to 1..8** (`vista_scheduler.cpp:82-83`) and the design
  rule is `stash >= depth`, never "as deep as possible" (`04-depth-and-stash.md`);
- the converter pool carries **slack** (`output-buffers = 12`) and the decoder
  keeps `num-extra-surfaces = 20` — headroom so a correctly-sized stash pins
  surfaces without starving the pool;
- on this Jetson (unified LPDDR; NVMM is carved from the same DRAM as host RAM)
  these are real physical surfaces, not cheap pointers, so the ceiling is hard.

The live footprint that must stay under the pool budget is
`(stash+1)*N` refs inside the scheduler **+** `d*K` frames in flight downstream.
Those two terms are the only things a knob can grow.

## 7. The two "leaky" escape valves

Elsewhere the codebase deliberately *chooses* to absorb backpressure by dropping
rather than blocking:

| element | `leaky` | drops | role |
|---|---|---|---|
| `cam-ring-N` capture-ring stand-in | `1` upstream | **newest** | reproduces the kernel ring's silent drop-newest in replay |
| `cam-dropold` baseline queue | `2` downstream | **oldest** | the config-only "keep-newest" alternative to the scheduler |

The `dropold` caveat is the punchline that motivates VISTA: **a leaky queue is a
*passive* drop that only fires when backpressure reaches it.** It sits upstream
of where the standing queue actually forms (the buffer pool), so under overload
it is never backpressured and never leaks. Measured: DROP-OLD lands at 856 ms
mean age against Stock-Default's 857 ms — statistically the same run
(the keep-newest and stock arms at YOLO11m, medians of 5). The stash is an
*active*, always-deterministic drop; it does not wait for backpressure to
decide.

## 8. Who backpressures whom

| stage | propagates upstream? | under overload it… | code |
|---|---|---|---|
| kernel capture ring | can't push back on hardware DMA | drops **newest**, silent | driver; replay stand-in in `pipeline_builder.cpp` |
| `v4l2src → decoder → nvvideoconvert` | **yes** (stock GStreamer, `leaky=0`) | blocks; the stall climbs upstream | — |
| arrival probe + stash | **no** — `DROP` + never-blocking `on_arrival` | drops **oldest**, counted, value-aware | `vista_scheduler.cpp:260-269, 304-333` |
| credit gate | **self only** — withholds release, no thread blocks | caps GPU at `d` batches; frames wait in the stash | `vista_scheduler.cpp:491, 610, 404` |
| NVMM converter pool | **yes, if exhausted** | re-propagates → silent ring drop | app's `conv_output_buffers = 12` |
| `dropold` baseline queue | absorbs *only if* the mux backpressures | drops **oldest** — in practice, never fires | app's dropold arm |

## 9. Code map

| link in the chain | where |
|---|---|
| arrival interception (`DROP` + ref) | `vista_scheduler.cpp:260-269` |
| re-injection pass-through flag | `vista_scheduler.cpp:262`, `622-635` |
| never-blocking stash (drop-oldest) | `vista_scheduler.cpp:304-333` |
| credit gate | `vista_scheduler.cpp:491` |
| credit spend / return | `vista_scheduler.cpp:610` / `404` |
| stale eviction (bounds the wait) | `vista_scheduler.cpp:495-505` |
| stash bound 1..8 | `vista_scheduler.cpp:82-83` |
| watchdog (the gate's failsafe) | `vista_scheduler.cpp:459-470` |
| residual stash counted at teardown | `vista_scheduler.cpp:667-669` |

## 10. The one sentence

> The stock pipeline lets backpressure propagate from a saturated GPU all the
> way to the camera driver, where frames die silently and newest-first; VISTA
> cuts that propagation at the source-bin src pad with a never-blocking stash,
> regulates the GPU with a self-imposed credit gate instead, and the *only* way
> backpressure returns is if an over-deep stash exhausts the NVMM pool — which
> is why `stash >= depth` is a rule and the pool is sized with slack.
