# Scheduler internals

*A walk through `vista/src/vista_scheduler.cpp` and
`vista/include/vista/vista_scheduler.hpp` as shipped. This page explains the
things the code cannot show you: why the ordering is what it is, what breaks if
you change it, and one trap that cost this project a whole campaign.*

Read `03-backpressure.md` first for *why* the arrival probe returns `DROP`, and
`04-depth-and-stash.md` for *why* the release gate and the stash interact.

---

## 1. State

The scheduler's entire state is one small struct per camera
(`vista_scheduler.hpp:196-211`):

```c
struct CamState {
  std::deque<Slot> fresh;     // oldest at front, newest at back; size <= cfg.stash
  Slot held;                  // salvage slot (mode=salvage only)
  double importance = 0.0;    // EWMA, decayed lazily
  double imp_updated = 0.0;
  double last_served = 0.0;
  long policy_drops, arrivals, admitted_fresh, admitted_held;
  bool eos = false;
  GstPad* pad = nullptr;      // ghost src pad (owned ref)
  bool pad_dead = false;      // push returned EOS/FLUSHING
  std::set<int64_t> seen_ids; // for new-track importance events
};
```

A `Slot` is a `GstBuffer*` plus a `double t_arrival` in `CLOCK_MONOTONIC`
seconds. **That arrival stamp is the only clock in the design.** No PTS is ever
read for a scheduling decision (`06-local-clocks.md`).

Bounded by construction: at most `stash` frames per camera in `fresh`, plus one
`held` slot in salvage mode. There is no other place a frame can wait.

## 2. Threading

Three streaming threads and one scheduler thread touch this state:

| thread | enters via | what it does |
|---|---|---|
| camera streaming thread (x N) | `arrival_probe` → `on_arrival` | stash a frame; never blocks |
| camera streaming thread (x N) | `event_probe` | mark EOS; release that camera's refs |
| tracker streaming thread | `completion_probe` → `on_completion` | return credit; update importance and `s_hat` |
| `vista-sched` | `thread_main` → `release_once` | score, select, push |

One mutex (`mu_`) covers all camera state; one condition variable (`cv_`) wakes
the scheduler on arrivals, completions and EOS, with a 5 ms fallback tick that
keeps the watchdog alive (`vista_scheduler.cpp:453`).

**The one hard rule: never hold `mu_` across `gst_pad_push`.** `release_once()`
takes the buffers out of the stash while locked, then unlocks, pushes, and
relocks (`vista_scheduler.cpp:622-635`). A push blocks until downstream accepts;
if the lock were held, an arriving frame on a camera thread would block on
`mu_` inside the probe, which stalls the camera, which re-creates the very
backpressure chain the design exists to cut. Holding the lock across the push
would not deadlock — it would silently reintroduce the pathology.

The `thread_local bool t_vista_pushing` (`vista_scheduler.hpp:262`) is set
around the push loop so the scheduler's own re-injected buffers pass the arrival
probe rather than being stashed again. It is process-wide and shared between
`Scheduler` instances, which is benign: each only ever pushes from its own
release thread.

## 3. The cycle

### 3.1 Arrival (`vista_scheduler.cpp:304-333`)

```
  ref the buffer, return PROBE_DROP
  lock
    if stopping or camera at EOS -> unref and discard (teardown race)
    arrivals++
    if fresh.size() >= stash:
        victim = fresh.pop_front()          // the OLDEST goes: keep-newest
        [salvage mode only] if imp_score >= retention_thresh -> victim becomes `held`
        else drop_slot(victim, "displace")  // counted
    fresh.push_back({buf, now})
  unlock; notify cv
```

Displacing the **front** (oldest) while appending at the back is what makes the
stash "keep newest overall" while still offering the *oldest retained* frame as
a candidate. Those are not in tension: the box keeps the newest `stash` frames;
within the box, the oldest goes first so that consecutive releases of one
depth-burst drain a camera in ascending-PTS order.

### 3.2 Completion (`vista_scheduler.cpp:335-407`) — the clock

```
  batch_meta = gst_buffer_get_nvds_batch_meta(buf)
  if (batch_meta == nullptr) return;            // <-- see known-issues: leaks credit
  lock
    for each frame in the batch:
        count detections; count NEW track ids (seen_ids.insert(...).second)
        importance[cam] = min(decayed_old + 1.0*new_tracks, imp_max)
    s_hat = 0.8*s_hat + 0.2*(now - release_time)   // from the released_ FIFO
    completions++; last_completion = now; fill_hist[frames]++
    [gate] after 20 completions, warn if <90% of batches carry exactly k
  unlock
  in_flight_ -= frames        (clamped at 0)
  notify cv
```

Three things ride on the completion probe, and it is worth being explicit that
they are three:

1. **credit return** — the release gate's only input;
2. **service-time estimate** `s_hat` — which sets `D_fair`/`D_hard`, so the
   fairness deadlines self-calibrate to whatever detector is loaded;
3. **the importance signal** — which is why importance is *feedback from
   processed frames* and therefore lags, and why a never-served camera can never
   raise its own importance (see §5, and `docs/usage/06-tuning.md` §6 — a
   never-served camera cannot ignite its own importance, which is what the
   fairness term exists to prevent).

### 3.3 Release (`vista_scheduler.cpp:487-637`)

```
  GATE: if in_flight > (depth-1)*K -> return false
  evict fresh fronts older than tau_max (150 ms), counted as "evict-stale"
  D_fair = 2*(N/K)*s_hat ; D_hard = 4*D_fair
  candidates = the OLDEST stashed frame of each live camera (<= N of them)
      forced = (now - last_served) > D_hard && !fresh.empty()
      v = w_f*fresh_s + w_i*imp_s + w_r*fair_s
  if |candidates| < K and no camera at EOS -> return false   // wait for arrivals
  stable_sort: forced first, then by value descending
  admit top K
  take buffers out of the stash (still locked); last_served = now
  sort items ascending PTS within a camera (held before fresh)
  in_flight += |items| ; released_.push_back((now, |items|)) ; log every decision
  unlock -> push each on its own camera pad -> relock
  return true      // caller loops: one completion can free room for >1 release
```

Four decisions in that block are load-bearing:

- **`D_fair` and `D_hard` are derived, not configured.** `D_fair = 2*(N/K)*s_hat`
  is "roughly the time to give every camera one turn, times a small safety
  factor", and `D_hard = 4*D_fair`. Because `s_hat` is measured live, a heavier
  detector automatically earns a longer grace period with nothing to retune.
  Because it is derived, `D_hard` is **not a constant**: it moves with `s_hat`,
  both across runs and within one run. Across the YOLO11m campaign
  `analysis/service_gaps.py` reconstructs it per run at **1142–3211 ms**
  (~1.1–3.2 s) — the spread is entirely `s_hat`, which the run summaries report
  at 71–201 ms for the same nominal operating point. Measured per-camera
  *admission* gaps in replay run **p50 71–81 ms, p99 87–373 ms, max
  1156–1260 ms**. Two runs' worst gaps **exceed their own indicative bound**:
  `e3_m/fresh-k2_r3` (max 1156.4 ms vs `D_hard`~1142.4 ms) and
  `e3_m/fresh-k2_r4` (max 1171.9 ms vs `D_hard`~1144.0 ms), two gaps each. See
  [the fairness-floor note](#the-fairness-floor-is-indicative-not-conformance)
  below for why that is expected rather than a refutation, and
  [`../../KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md) for what the draft does and
  does not claim about `D_hard`.
- **Forced-first sorting is the fairness floor**, and it is a `stable_sort`, so
  among equally-forced (or equally-unforced) cameras the value ordering decides
  and ties break deterministically by camera index.
- **`|candidates| < K` waits rather than releasing short.** Frames arrive at
  ~30 fps/camera, so this resolves within a frame period. Once cameras hit EOS
  the rule relaxes and short releases are allowed — otherwise the run could not
  drain. (The paper's Limitations section notes the same relaxation is what a
  mid-run camera *failure* would need; that path is not measured.)
- **The `while (release_once())` loop** is what produces the burst of `d`
  releases microseconds apart — the mechanism behind the whole stash rule of
  `04-depth-and-stash.md`.

#### The fairness floor is indicative, not conformance

`analysis/service_gaps.py` prints `D_hard` as a **reference line, not a verdict**,
and the two exceedances above do not mean the floor failed. Four reasons, all
readable in `release_once()` (`vista_scheduler.cpp:487-566`):

1. **Force-admission is a priority, not a delivery guarantee.** The flag is
   `forced = since_served > d_hard_ms && !c.fresh.empty()`. Crossing `D_hard`
   sorts a camera to the front of the candidate list — it does not create a
   slot. A camera with an empty stash cannot be forced at all (it supplied no
   frame to admit), so a gap may legitimately exceed `D_hard`.
2. **The in-flight gate runs first.** `release_once()` returns at its first
   line while `in_flight_ > (depth-1)*k`, before any candidate is built or any
   deadline is examined. While the GPU is behind, *nothing* is admitted,
   forced or not — so batch service time bounds admission cadence from below.
3. **Only `K` candidates are admitted per batch.** If more than `K` cameras
   cross the floor together, the surplus waits another batch. This is exactly
   the `ceil(N/K)*S(K)` term: the realised bound is `D_hard + ceil(N/K)*S(K)`,
   not `D_hard`.
4. **The printed `D_hard` is reconstructed from the wrong `s_hat`.** `s_hat_ms_`
   is a live EWMA (`s_hat = 0.8*s_hat + 0.2*dt`), so the in-force `D_hard` moves
   continuously; the tool can only read the **end-of-run** `s_hat` from the
   summary line. It compares each gap against a bound that was not the one in
   force when the gap happened. The tool's docstring adds a fifth reason: the
   archived runs were produced by the **old binary**, whose rule cannot be read
   back from source.

Reason 4 is what produces both exceedances, and it is worth being precise about
rather than rounding away. **Every** run's worst gap — all eight, replay and
live-imp alike — falls ~30 ms after that run's *first* admit, at t≈1.0-1.08 s:

| run | max gap | at t | first admit | `D_hard`~ | |
|---|---:|---:|---:|---:|---|
| `e3_m/fresh-k2_r0` | 1168.7 | 1.034 | 1.002 | 3211 | within |
| `e3_m/fresh-k2_r1` | 1260.3 | 1.043 | 1.011 | 3139 | within |
| `e3_m/fresh-k2_r2` | 1165.0 | 1.038 | 1.006 | 3139 | within |
| `e3_m/fresh-k2_r3` | 1156.4 | 1.041 | 1.009 | 1142 | **exceeds** |
| `e3_m/fresh-k2_r4` | 1171.9 | 1.030 | 0.998 | 1144 | **exceeds** |
| `e8_impfix_r0/imp-k2_r0` | 1193.8 | 1.005 | 0.973 | 1432 | within |
| `e8_impfix_r1/imp-k2_r0` | 1244.3 | 1.083 | 1.051 | 1405 | within |
| `e8_impfix_r2/imp-k2_r0` | 1209.9 | 1.008 | 0.976 | 1882 | within |

That is the TensorRT engine-load stall: each camera takes its first admit, the
gate closes behind a batch that takes ~1.2 s to clear, and every camera records
one ~1.2 s gap simultaneously. It is the same warmup the watchdog below is armed
late to tolerate. The maxima are near-identical *because* they are one shared
event, not four independent ones. `r3` and `r4` are flagged not because their
gaps were worse (1156/1172 ms is mid-pack — `r1`'s 1260 ms is the campaign
worst and passes) but because those two runs ended with a *fast* `s_hat`
(~71 ms vs `r0`'s 201 ms), which retro-fits a ~1.14 s bound onto a warmup-era
gap. During the stall itself `s_hat` was large and the in-force `D_hard`
correspondingly loose.

The tool's docstring claims "the first gap is measured from the camera's first
admit, not from run start, so warmup does not manufacture a gap." That
mitigation is **insufficient**, as the table shows: the stall lands *after* each
camera's first admit, so measuring from it does not exclude warmup.

Two honest readings follow, and the artifact does not pick between them: either
the floor holds and the tool's post-hoc bound is too tight at warmup, or the
floor genuinely misses during engine load. Distinguishing them needs the
in-force `s_hat` at decision time, which the archived `sched.csv` does not
record. Steady-state behaviour is not in question — p99 is 87–373 ms against
bounds of 1.1–3.2 s.

> Note also that the tool's "worst observed gap" summary prints only the **top
> five by max gap**. `r3` exceeds its bound but sorts sixth, so it does not
> appear there. The per-run `over:` column is authoritative; both runs show
> `over: 2`.

### 3.4 Watchdog (`vista_scheduler.cpp:459-470`)

```
  if completions >= 3 && in_flight > 0
     && silence > max(10*max(s_hat,50), 2000) ms:
        warn; in_flight = 0; released_.clear(); last_completion = now
```

Armed only after three real completions, because the first batches include
TensorRT engine load and can legitimately take seconds. It exists because a lost
completion would otherwise close the gate forever with no output and no message.
It is a failsafe, not a mechanism: **if the watchdog fires, that run's drop
accounting is no longer exact** — it resets the credit counter rather than
reconciling it. Treat a `WATCHDOG` line in `stderr.log` as invalidating the run.

### 3.5 EOS (`vista_scheduler.cpp:271-294`)

EOS **passes through untouched** (`GST_PAD_PROBE_OK`) — the pipeline's normal,
known-good teardown path. The scheduler just marks the camera done and releases
its stashed refs. A v1 swallow-and-forward-later drain deadlocked the pipeline
at EOS; this is the fix, and its cost is that at most `stash` tail frames per
camera are never processed. That is irrelevant to steady-state benchmarks, which
trim warmup and report rates and distributions rather than totals.

### 3.6 Teardown ordering

The order in `vista/README.md` is load-bearing and is not a style preference:

```
  1. request_stop()                  // the release thread may be blocked
                                     // inside gst_pad_push
  2. gst_element_set_state(p, NULL)  // flushes pads; unblocks that push
  3. join_and_cleanup()              // joins; unrefs stashed buffers
  4. gst_object_unref(pipeline)      // LAST — stashed buffers belong to its pools
```

Step 3 before step 2 hangs (the thread is inside a push that will never return).
Step 4 before step 3 unrefs buffers whose pools are already gone.

`join_and_cleanup()` (`vista_scheduler.cpp:647-676`) **counts** the frames still
stashed at teardown as policy drops rather than silently unreffing them. This is
a deliberate difference from the paper binary, documented in
`vista/PAPER_DIFF.md`. The reasoning: at any instant the true identity is

```
  arrivals == admitted + policy_drops + still_in_stash
```

so the ledger the paper claims (`still_in_stash == 0`) only closes if shutdown
accounts for what the stash still holds. Those frames arrived and will never be
inferred — that is a drop, and VISTA's whole argument is that such drops are
counted rather than silently absorbed. The effect is bounded by `num_cams*stash`
frames, lands entirely at teardown, and changes no scheduling decision.

## 4. Buffer ownership

One rule: **the stash owns exactly one ref per stashed frame, and exactly one
thing consumes it.**

| point | ref action |
|---|---|
| `arrival_probe` | `gst_buffer_ref(buf)` — we take our own; the `PROBE_DROP` releases the pad's |
| stash → `items` in `release_once` | ownership moves; the slot is cleared |
| `gst_pad_push(pad, buf)` | **consumes** the ref |
| `drop_slot()` | `gst_buffer_unref(buf)`, slot cleared, `policy_drops++` |
| EOS / shutdown | `drop_slot()` on everything still stashed |

Every path out of the stash goes through either `gst_pad_push` or `drop_slot`,
and `drop_slot` is the only place `policy_drops` is incremented. That is why the
ledger closes: the counter is incremented by the same statement that releases
the ref. `Stats::ledger_closes()` (`vista_scheduler.hpp:95-97`) checks

```
  arrivals == admitted_fresh + admitted_salvage + policy_drops
```

and `vista/examples/minimal_pipeline/main.cpp` asserts it. On the paper's live
rig it closed exactly: `5864 admitted + 8120 drops == 13984 arrivals`.

The camera's `pad` is also an owned ref, taken in `attach()` and released in
`join_and_cleanup()`. `pad_dead` latches when a push returns `EOS`/`FLUSHING`
so the scheduler stops pushing into a torn-down branch.

## 5. The importance trap (v1 → v2)

This is the practitioner finding the paper reports in Sec. III-B, and it is
worth stating concretely because the failure is **silent and looks like a null
result**.

**v1** incremented importance by `3*new_tracks + 1*dets` with `I_max = 10`. The
`dets` term counts objects *merely sitting in view*. On any scene with standing
objects — a chair, a monitor, a parked car — every camera's importance pinned at
`I_max`, so `imp(c) = I_c/I_max = 1.0` for all `c`, so the importance term
became a **constant added to every candidate's score**. Constants do not change
an argmax. `imp` mode was structurally identical to `fresh` mode, and nothing
warned.

Measured directly from the archived decision logs (`sched.csv`, `admit` rows):

| signal | per-camera median `imp_score` | admissions with `imp_score >= 0.99` |
|---|---|---|
| **v1** (`e3_m/imp-k2_r0`) | 1.000 / 1.000 / 1.000 / 1.000 | **69.4%** |
| **v1** (`e3_m/imp-k2_r1`) | 1.000 / 1.000 / 1.000 / 1.000 | 68.1% |
| **v2** (`e8_impfix_r0`) | 0.657 / 0.223 / 0.386 / 0.346 | 1.2% |
| **v2** (`e8_impfix_r1`) | 0.624 / 0.258 / 0.352 / 0.333 | 1.2% |

The v2 medians track the workload: the office clips' reference events split
37/33/28/25 across cam0..cam3, and cam0 — the busiest — carries the highest
median importance. The signal discriminates. (It still yields no recall gain on
that workload, because uniform activity leaves nothing to reallocate. That null
is now genuine and explained, rather than an artifact.)

**v2** (`vista_scheduler.cpp:349-366`) increments by `1.0 * new_tracks` only,
with `imp_max = 2.0` sized so that ~0.7 new tracks/s saturates:

```c
const double inc = 1.0 * new_tracks;
const double old = importance_now(cam, now);   // decays to now
c.importance = std::min(old + inc, cfg_.imp_max);
```

with lazy exponential decay at `imp_halflife_s = 2.0`
(`vista_scheduler.cpp:412-419`).

**The generalizable lesson, and the reason the header carries a comment about
it** (`vista_scheduler.hpp:121-128`): *importance must measure **change**, not
content.* Any signal keyed to how **much** is in frame will saturate on a static
scene and silently switch the term off. Detection count, occupied area, and
"activity" measured as pixel energy all have this failure mode. Count events —
new tracks — not objects.

Two consequences for anyone re-running the campaign:

- **The v1 signal saturated**, which made `imp` mode structurally identical to
  `fresh`: the paper's first importance runs read cov 38.8% / e2e 95 ms —
  numerically on top of VISTA-Fresh, exactly as saturation predicts. The
  published VISTA-Activity point comes from the rerun on the fixed
  event-driven signal. **Check your own `imp_score` distribution before
  believing an importance result**: if its median is 1.000, you have rebuilt the
  trap and are measuring `fresh` under another name.
- `imp_max` is now a config field, not a compile-time constant, so the
  saturation point is inspectable. If your scene's activity rate is far from
  ~0.7 new tracks/s, this is the number to reconsider — and the way to check is
  to histogram `imp_score` in `sched.csv`, exactly as the table above does. A
  median of 1.000 means the term is off.

## 6. What the module validates, and what it refuses to

`validate_cfg()` (`vista_scheduler.cpp:75-122`) rejects only configurations that
are *verifiably* broken, and each bound closes a real failure:

| check | why |
|---|---|
| `mode` in `off\|fresh\|imp\|salvage` | typo protection |
| `k` in `1..2*num_cams` | — |
| `stash` in `1..8` | above ~8 the NVMM pool starves (`03-backpressure.md` §6) |
| `depth >= 1` | **`depth 0` makes the gate `0 > -k` read true forever: the paper binary accepted it and hung silently, with no output and no message** |
| `tau_max_ms > 0`, `imp_max > 0`, `imp_halflife_s > 0` | division / saturation |
| weights non-negative, at least one positive | — |

Two *warnings* rather than throws, both deliberate:

- **weights not summing to 1.0** — scores are compared, never thresholded, so a
  non-unit sum only rescales `v(f)` uniformly. Harmless, but almost always a
  typo.
- **`stash < depth` with importance on** — the exact misconfiguration the RQ3
  result is about. It cannot be an error (it is the shipped default for
  `fresh` mode, where it is *optimal*), but silently accepting it with `w_i > 0`
  is how a deployment gets the paper's null instead of the paper's result.

`check_obligations()` (`vista_scheduler.cpp:136-192`) is the interesting one,
because it demonstrates a place where the obvious check is *wrong*. The mux's
`batch-size` property is not reliable at `attach()` time: requesting sink pads
raises it to the pad count while the mux still has built-in adaptive batching,
and the INI that turns adaptive batching off is not read until the state change.
On the paper's own configuration (4 cameras, `k=2`) the property reads **4**
exactly where the check runs. An equality throw there rejects a *correct*
pipeline — and did, until it was caught by an end-to-end run.

So the module throws only on `batch-size < k` (never satisfiable), warns on
`> k` (ambiguous), and defers the real decision to the **runtime** batch
atomicity gate in `on_completion` (`vista_scheduler.cpp:384-401`), which decides
it on evidence: the fill histogram. That is the only thing that actually settles
the question. Measured on the gate runs: 1434/1435 batches at exactly k=2
(99.93%) and 643/644 at exactly k=4 (99.84%) — the single outlier in each is the
final teardown batch. See `docs/reproduction/01-run-the-experiments.md`.
