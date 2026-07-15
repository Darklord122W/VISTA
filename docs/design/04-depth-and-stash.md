# K x depth x stash — the three knobs, and the one rule

*The mechanism behind the paper's "When Does Importance-Aware Allocation Help?"
(Sec. V-D) and the `stash >= d` configuration rule of Sec. III-D. The arithmetic
here is read from `vista/src/vista_scheduler.cpp` and you can check it against
the source. **The measured share and recall numbers are reported**: they come
from the paper's campaign, whose data this repository does not distribute. The
share numbers are the ones to trust anyway — they fall out of the retention
arithmetic below, and your own runs will reproduce them.*

Policy names follow the paper; the arms carry their original names.
`analysis/campaigns.yaml` maps between them.

| paper name | on disk |
|---|---|
| VISTA-Fresh | `fresh-k2` |
| VISTA-Activity | `imp-k2` |
| all-admit ablation | `fresh-k4` |

---

## 1. The two knobs, precisely

| | **depth** (`SchedCfg::depth`, default 2) | **stash** (`SchedCfg::stash`, default 1) |
|---|---|---|
| where it acts | downstream: scheduler → GPU | upstream: camera → scheduler |
| what it counts | **batches** allowed in flight at once | **frames** each camera's box can store |
| code | the release gate: `if (in_flight_ > (depth-1)*k) return false` (`vista_scheduler.cpp:491`) | the deque: `if (fresh.size() >= stash) pop_front()` (`vista_scheduler.cpp:314-329`) |
| what it buys | GPU utilization: at `d=2` a loaded batch always waits behind the executing one, so the GPU never idles | retention: a camera can still be holding a frame when the *second* release of a burst arrives |
| cost of more | staleness: each extra unit of depth is a re-forming queue (~one service time of age per unit) | candidate age: oldest-first draining makes the offered frame up to one frame period (~33 ms) older |
| cost of less | idle GPU between completion and next release (~5% throughput at `d=1`) | the concentration cap (§3) |

Two facts make them interact:

1. **One candidate per camera per release.** `release_once()` offers only
   `fresh[c].front()` (`vista_scheduler.cpp:536`). A deeper stash never adds
   candidates *within* a release — only across consecutive releases.
2. **Releases arrive in bursts of ~d, not evenly spaced.** The `d` in-flight
   batches tend to drain through the tracker together, returning credit in a
   lump; the `while (release_once())` loop (`vista_scheduler.cpp:474-475`) then
   fires `d` releases microseconds apart, followed by a gap of ≈ `d*S(K)`.

## 2. The timeline that creates the interaction

Measured scales at YOLO11m, K=2: `S(2) ≈ 37 ms`, so a `d=2` cycle is
**[release, release] … ~74 ms gap … [release, release]**, while each camera
delivers a frame every **~33.6 ms** (≈2.2 arrivals per camera per cycle).

### stash = 1 — the cap

```
  gap (~74 ms): frames f1, f2 arrive at cam0.
                f2 DISPLACES f1 (counted drop) — the box holds ONE.
  completion burst -> credit for 2 releases
  release A: cam0 wins a seat with f2.  Box now EMPTY.
  ...microseconds later...
  release B: cam0 has NOTHING left. Other cameras fill both seats.

  => cam0 supplied 1 of the cycle's K*d = 4 seats = 25% share,
     regardless of its importance score.
```

The frames **did arrive** — 2.2 per cycle. A 1-deep box just cannot *keep* two
of them. **The failure is retention, not arrival**, which is why importance
weight is nearly powerless against it. Measured like-for-like on the persistent
skew clips: `w_i = 0.96` (28.7%) buys **~3 points** of hot-camera share over the
default `w_i = 0.35` (25.5 / 25.9 / 25.4%, median 25.5%). On that same clip set,
releasing the cap with `stash = 2` at the *default* weight reaches **48.4%**
(medians of 3). So a 0.35 → 0.96
weight swing buys 3 points; releasing the cap buys 23 — at no weight change at
all. The weight is not *nothing*, it is *dominated* — which is the same
conclusion, honestly stated.

### stash = 2 — the cap released

```
  gap: f1, f2 arrive — BOTH retained.
  completion burst
  release A: cam0 wins with f1 (oldest first, ascending PTS)
  release B: cam0 wins AGAIN with f2

  => cam0 supplied 2 of 4 seats => 50% possible (measured: 48.3%)
```

### depth = 1 — the other way to release the cap

With one batch in flight, releases are spaced a full `S(2) ≈ 37 ms` apart —
*longer* than the 33.6 ms refill period — so even a 1-deep box has refilled by
every audition. No burst, no cap. The price moves elsewhere: the GPU idles
between completion and the next release (~5% throughput), though with no queued
batch the output age roughly halves (~70 vs ~115 ms mean).

## 3. The cap as arithmetic

Per cycle of one depth-burst:

```
seats served per cycle       = K * d                    (2*2 = 4)
frames one camera can supply = min(stash, d)            (1 candidate/release,
                                                         drained oldest-first)
=> max share of any camera   = min(stash, d) / (K * d)
```

| stash | predicted max hot-camera share (K=2, d=2, N=4) | measured |
|---|---|---|
| 1 | 1/4 = **25% — exactly the even split** | 25.3% (fresh), 28.7% (activity) |
| 2 (= d) | 2/4 = **50%** | **48.3%** |
| ≥ 3 | still 2/4 — frame `d+1` cannot fit in the burst | not run (dominated, §5) |

Measured values are medians of 3, from the stash-1 control and stash-2 arms —
**both on the brief skew clips, both at the default `w_i = 0.35`**. The
`w_i = 0.96` run quoted in §2 is on a *different* clip set (persistent skew); its
28.7% and this row's 28.7% are a coincidence of rounding across two workloads,
not the same measurement. Compare weights only
within a clip set — the like-for-like pair is in §2. The full per-camera
picture at stash 2 is worth seeing, because it shows *who pays*:

| campaign | cam0 (hot) | cam1 (empty) | cam2 (empty) | cam3 (quiet, 1 rare event) |
|---|---:|---:|---:|---:|
| VISTA-Fresh, stash 1 | 25.3% | 24.7% | 24.8% | 25.1% |
| VISTA-Fresh, stash 2 | 25.4% | 24.3% | 25.0% | 25.2% |
| VISTA-Activity, stash 1 | 28.7% | 23.6% | 22.8% | 25.0% |
| **VISTA-Activity, stash 2** | **48.3%** | **14.3%** | **14.3%** | **22.8%** |

The concentration is paid for by the **empty** cameras (24.7% → 14.3%), while
the quiet camera holding the single rare event keeps 22.8% — the fairness floor
holding, visibly, in the data. This is the mechanism behind the paper's "the
active camera then receives 48% of the service slots" and "the fairness
constraint still captures the quiet camera's rare event in every run."

This is why the rule is `stash >= depth` and not "stash as big as possible": the
benefit saturates exactly at `d`, and beyond `d` the extra frames are pure age.

## 4. The four regimes, all measured

| | GPU idles? | concentration | throughput | age |
|---|---|---|---|---|
| **d=2, stash=1** — module default; **VISTA-Fresh**, and the wrong place to turn importance on | never | **capped at even share** — importance structurally inert | full | ~115 ms |
| **d=2, stash=2** — **VISTA-Activity** | never | expressible (48% share) | no measured cost | +~13 ms |
| **d=1, stash=1** (alternative) | between batches | expressible (48–78% share) | ~5% loss | ~70 ms (halves) |
| **d=1, stash=2** | between batches | expressible | ~5% loss | pointless — the 2nd slot is never needed |

Recall behind that (brief = 368-event oracle, persistent = 257-event oracle;
medians of 3; all at `d=2`; `event_recall`, i.e. onset time):

| arm | hot-cam share | brief @250 ms | brief @1 s | pers. @250 ms | pers. @1 s |
|---|---:|---:|---:|---:|---:|
| VISTA-Fresh, stash 1 | 25 | 25 | 54 | 47 | 78 |
| VISTA-Fresh, stash 2 | 25 | 30 | 57 | 35 | 67 |
| VISTA-Activity, stash 1 | 26–29 | 39 | 68 | 48 | 77 |
| **VISTA-Activity, stash 2** | **48** | **71** | **80** | **77** | **86** |

**The paper's Table IV is the three bold-able columns of this one:** hot-camera
share, brief recall, persistent recall, all at 250 ms. The `@1 s` columns are not
in the paper; they were computed from the same runs and are kept because they
show
the gain is a *timeliness* gain — it shrinks as the deadline relaxes (brief
71 vs 80 at stash 2, against 25 vs 54 at fresh stash 1).

The paper reads its headline off this table **stash 2 against stash 2** — brief
0.30 → 0.71, persistent 35 → 77 — holding the stash fixed and varying only
importance. Note that the fresh rows are **non-monotonic in stash**: brief
recall rises 25 → 30 while persistent recall *falls* 47 → 35. That is why the
baseline matters, and why an earlier draft's "47 → 77" for persistent events
overstated the importance term's contribution: it read the stash-1 fresh row
against the stash-2 activity row, crediting importance with a gain that is
partly the stash's. The current draft compares like with like.

Read the share column against the recall column and the mechanism chain is
visible:

> **retention (stash) → share (concentration) → recall.**

Stash 2 without importance moves neither share nor recall (nothing to
concentrate); importance without stash 2 moves neither (nothing to retain);
together they move both. That is the paper's rule, and it is the reason it is a
*rule* rather than a tuning suggestion: neither knob alone does anything.

Supporting measurements, all from the paper's campaign and none of them
regenerable from this repository. The first two were re-derived from that
campaign's raw detections; the rest are its own figures, quoted as such.

- **Detection yield rises with the concentration (VERIFIED).** Overall detection
  yield, medians of 3, recomputed from raw: VISTA-Fresh stash 1 **19.8%** →
  VISTA-Activity stash 2 **59.1%** on the brief clips (3.0x); the stash-1
  activity control sits at 29.2%. The paper reports "detection yield 2.6x",
  which is the *hot camera's* yield (0.23 → 0.59) from the campaign notes — a
  different denominator, not re-derived here.
- **The fairness floor holds (VERIFIED).** At stash 2 the quiet camera keeps
  22.8% of service while the two empty cameras fall to 14.3% — the per-camera
  table above. The paper's stronger claim, that its single rare event was caught
  in *every* run, is the campaign's; this artifact verified the service share
  that makes it possible, not the per-event outcome.
- **Zero throughput cost of stash 2** (campaign figure): 47.6 f/s vs 43.9–47.1
  across the other arms — the same frames get processed, just *chosen from* the
  hot camera more often.
- **The cost when importance is OFF** (campaign figure): oldest-first draining
  adds up to one frame period of age — e2e 131 vs 118 ms, and persistent recall
  *drops* (47 → 35 @250 ms, 78 → 67 @1 s), which **is** re-derived here from the
  `persS2` vs `impcmp` rows of §4. **Keep stash 1 when `w_i = 0`**: for a
  pure-freshness policy, "keep exactly the newest frame" is not a limitation, it
  is the optimal retention rule.

## 5. Why not stash = 3, 4, …?

Three independent reasons the benefit stops at `d`:

1. **No seat for it.** Frame `d+1` cannot ride in the current burst (only `d`
   releases fire); by the next burst a fresher frame has arrived and stands in
   front of it. It is a *dominated* candidate — same camera ⇒ same importance
   and fairness; only freshness differs, and it loses.
2. **The eviction clock.** A frame at deque position `n` is ≥ `n*33 ms` old
   before it competes; `tau_max = 150 ms` discards everything past depth ~4
   unserved. Deep stashes fill with frames destined for the bin.
3. **Buffer-pool pressure.** Stashed frames pin converter-pool surfaces; hold
   too many and backpressure returns to the kernel ring — the silent-drop zone
   the design exists to avoid (`03-backpressure.md` §6).

The module warns rather than assumes. `validate_cfg()`
(`vista_scheduler.cpp:113-121`) prints a loud warning when
`use_importance() && stash < depth`, because silently accepting that exact
misconfiguration is what the deployment rule exists to prevent.

## 6. The wrong turn — how this was actually found

Honest history: the interaction was **discovered, not designed**.

1. **v1 hardwired stash = 1.** There was a single `fresh[c]` slot and no
   `--sched-stash` flag to sweep. Depth existed only as "double-buffering so the
   GPU never idles." Nobody conceived of them as coupled.
2. **The main workload masked the cap.** The office clips have uniform activity
   across cameras (reference events split 37/33/28/25), so importance had
   nothing to concentrate and a cap on concentration changed nothing measurable.
   Every early number (Tables II/III) is correct *and* cap-insensitive.
3. **The first skewed campaign compared two equally-capped arms.** `impcmp_*`
   ran VISTA-Activity-stash1 against VISTA-Fresh-stash1 — both capped — saw
   nearly identical recall (0.482 vs 0.471 @250 ms, verified here from
   that campaign's scoring output), and concluded *"importance is inert when
   objects persist."* Plausible, and wrong: the cap, not the event duration,
   was the gate.
4. **The unmasking.** A depth diagnostic (`impdiag_d1`) ran the same weights at
   `d=1` and service suddenly concentrated (48–78% share). Same value function,
   one knob changed ⇒ the cap had to be structural. `--sched-stash` was then
   implemented (deque + oldest-first), and the full 2x2 matrix
   (`briefS2`/`persS2` + `briefD2ctl` controls, `e9` for the d=1 costs)
   validated the rule in all three directions: **a weight sweep cannot fix it,
   `d=1` fixes it, `stash=d` fixes it.**

```
  early campaigns            impcmp: Activity-s1 vs Fresh-s1
  (uniform office scene)  ->  both capped => "importance inert"
  cap present, invisible      (wrong conclusion)
                                      |
                                      v
                              impdiag_d1: d=1 concentrates!
                              => the cap is structural
                                      |
                                      v
                              --sched-stash implemented (deque, oldest-first)
                                      |
                                      v
                              briefS2 / persS2 / briefD2ctl / e9 matrix:
                              rule validated 3 ways -> Table IV
```

> **What the paper says about this.** The final manuscript does **not** narrate
> the wrong turn. Its RQ3 opens with the uniform-activity null and presents the
> stash rule directly from the 2x2 matrix. Some working notes in the authors'
> tree assert that the paper "reports the wrong turn on purpose" and quote a
> Sec. V-D sentence to that effect; that sentence is **not in the final
> revision**, which reorganized the evaluation around four RQs and demoted
> activity to an optional extension. The history above is recorded here, in the
> artifact, because it is the confounder a reader reproducing the skew study
> would most plausibly fall into — not because the paper claims it.

## 7. The decision rule

```
  Using importance? (w_i > 0 AND activity demonstrably skewed)
    |
    +-- no --> stash = 1, depth = 2
    |          freshest candidates, GPU never idles.  THE SHIPPED DEFAULT.
    |
    +-- yes -> which matters more?
                 |
                 +-- throughput --> stash = depth = 2
                 |                  concentration at zero throughput cost;
                 |                  candidates <=1 frame period older
                 |
                 +-- latency ----> depth = 1, stash = 1
                                    same concentration, e2e ~halved,
                                    ~5% throughput loss
```

The paper's one-sentence version (Sec. III-D): *for activity-aware reallocation,
the per-camera stash depth should be at least the release depth, i.e.
`stash >= d`; when importance weighting is disabled, a one-frame stash remains
preferable because it implements pure keep-newest retention and minimizes
admission age.*

This is why the paper's two configurations differ in **two** knobs and not one:
**VISTA-Fresh is `fresh`, K=2, d=2, stash 1, `w_i = 0`; VISTA-Activity is `imp`,
K=2, d=2, stash 2, `0.40/0.35/0.25`.** The stash is not an afterthought carried
over between them — it is the knob that makes the weights mean anything.

## 8. Where the evidence lives

**The arithmetic** — the part that decides everything on this page — is in the
code and you can read it today: `vista/src/vista_scheduler.cpp` (`release_once`,
`on_arrival`), with the rule stated in
`vista/include/vista/vista_scheduler.hpp`. The gate, the deque and the
oldest-first drain are all there.

**The measurements are not here.** The campaigns behind the share and recall
numbers above — the capped-vs-uncapped matrix, the `d=1` cost pair, the
`w_i = 0.96` diagnostic that failed to beat the cap — are the paper's, and this
repository does not distribute run data. To check the claim rather than read it,
run the two arms yourself: `--sched imp --sched-stash 1` against
`--sched imp --sched-stash 2`, on skewed footage, and watch the hot camera's
share move from ~1/4 to ~1/2 while nothing else changes.
[`../reproduction/01-run-the-experiments.md`](../reproduction/01-run-the-experiments.md).

## 9. FAQ

**Q. Why not stash many frames and score a big pool — more choices?**
Extra same-camera frames are *dominated* choices: importance and fairness are
per-camera terms, so an older sibling can never outrank a newer one. Under
`rho > 1` a big pool is just a pool of old frames (staleness = backlog ÷
throughput — the deep-pool stock baseline *is* this policy, at 857 ms), and
stashed frames pin buffer-pool surfaces. The useful extra depth is exactly `d`,
and only when importance is on.

**Q. What does `in_flight <= (depth-1)*K` mean?**
A credit budget. Releases add K, completions subtract the batch. At the
defaults: release → 2 in flight → release again → 4 → gate closed; only a
completion reopens it. Steady state = exactly `d` batches out: one executing,
one queued behind it. **Sustained release rate ≡ GPU completion rate, for any
detector, with no tuning.** That is the whole point of the completion clock.

**Q. What does "one camera, one candidate per release" mean?**
At each release the scheduler holds an audition where **each camera steps
forward with ONE frame** — its oldest stashed one. Stash depth changes how many
frames wait in each camera's box, never how many contestants stand in line. So
candidates ≤ N per release, always.

**Q. With stash=2 we store 8 frames — why can't K=4 choose 4 of 8?**
The 8 are stored, but only 4 (one per camera) ever compete at once. And if you
changed that rule, the *only* new option it unlocks is "give the hot camera both
of its 33 ms-apart near-duplicates and skip a distinct view" — while still
paying S(4) per batch and deciding half as often on staler scores. That trade was
measured in a separate study that went beyond the paper's system, and the
argument held at the shipped operating point: a seat-by-seat auction that lets
one camera take multiple seats of a batch landed within run-to-run noise of the
shipped rule at K=2/stash=2. It won only at deeper stash, i.e. outside the
envelope the paper measures, and it bought that recall with output age — the axis
VISTA exists to defend. That study's data does not ship here either.

**Q. Why does release B fire microseconds after A — is the GPU "ready"?**
B is not a second batch to *execute*. It is the **standby batch** that refills
the queue slot behind the executing one, so the instant A's inference finishes,
B starts with zero gap. Withholding B is the measured `d=1` regime (~5%
throughput loss). And no, the scheduler should not wait ~30 ms for the hot
camera's next frame before forming B: that gambles on a frame that does not
exist yet, ages the three cameras already in hand, risks idling the GPU on the
knife-edge — and retention (stash=2) buys the same outcome for free.

**Q. Why does d=1 lose ~5% throughput?**
No standby batch. At `d=1` the next batch is not even *formed* until the
previous one exits the tracker: completion signal → scheduler wakes →
scores/sorts → pushes → mux forms the batch → GPU starts. That round-trip is GPU
idle time, ~2 ms of a ~37 ms cycle ≈ the measured 5% (40.8 vs 42.7 f/s). At
`d=2` the standby batch is already loaded in the detector's queue, so the
round-trip hides behind execution — but the standby batch *waits* one service
time before running, which is exactly why `d=2` adds ~S(K) of frame age
(e2e ~115 vs ~70 ms). Depth is a clean two-way trade: **age for throughput**.
Stash is irrelevant to it — the loss is an empty GPU queue, not an empty camera
box.

**Q. Did stock batches really always carry 4 frames?**
Under overload, yes — measured fill 3.996–4.000 for the deep-pool stock arms and
the all-admit ablation, and 1.999–2.000 for the K=2 arms. The mux's per-camera
queues are never empty at `rho = 1.86`, so every stock batch completes with one
frame per camera before any deadline could fire. The exceptions are instructive:
Stock-LiveDepth averages 3.43 (~30% partial — *pool* starvation) and
Static-Decimation averages 1.73 and never once assembles a batch of 4 — *source*
starvation.
