# Tuning

The short version, which is also the paper's deployment guidance:

> Enable VISTA when estimated load exceeds capacity; run **freshness +
> fairness** by default; enable activity weighting — **with stash ≥ d** — only
> when camera activity is demonstrably skewed.

The paper evaluates exactly two configurations, and they differ in **two**
knobs, not one:

| | mode | K | d | stash | weights |
|---|---|---|---|---|---|
| **VISTA-Fresh** — the recommended default | `fresh` | 2 | 2 | **1** | `w_i = 0` (freshness + fairness only) |
| **VISTA-Activity** — the optional extension | `imp` | 2 | 2 | **2** | `0.40 / 0.35 / 0.25` |

In flags:

```bash
# VISTA-Fresh — the general-purpose configuration
--sched fresh --sched-k 2 --sched-depth 2 --sched-stash 1 --sched-tau-max 150

# VISTA-Activity — only when activity is demonstrably skewed
--sched imp   --sched-k 2 --sched-depth 2 --sched-stash 2 --sched-tau-max 150
```

**The stash differs between them on purpose.** Stash 1 is not a default that
VISTA-Activity inherits and forgets to change — it is *optimal* for a
pure-freshness policy (pure keep-newest, minimum admission age) and
*structurally disabling* for an importance-weighted one. VISTA-Activity's
two-frame stash is what satisfies the `stash ≥ d` condition at the default
d = 2. Get this wrong and the activity term does nothing at all, silently, no
matter how you set the weights — [§4](#4-stash--and-the-rule) is the arithmetic.

Read the rest of this page before you change any of it.

Contents: [First decide whether to run it at all](#0-first-decide-whether-to-run-it-at-all) ·
[Which policy](#1-which-policy) · [K](#2-k--selection-pressure) ·
[d](#3-d--release-depth) · [stash](#4-stash--and-the-rule) ·
[tau_max](#5-tau_max) · [weights](#6-the-weights) ·
[D_fair / D_hard](#7-d_fair--d_hard-and-a-caveat-about-s_hat) ·
[salvage](#8-salvage--an-extra-mode-not-evaluated-in-the-paper) ·
[what not to touch](#9-what-not-to-touch)

---

## 0. First decide whether to run it at all

VISTA solves **oversubscription**. If your detector keeps up, it has nothing to
do and its always-K release costs you a little coverage for no gain — measured
96.6% coverage against the stock path's 100% at ρ = 0.84 (YOLO11n).

| ρ = S/T | What to run |
|---|---|
| < 1 | **`--sched off`.** With the scheduler off the binary is bit-identical to the stock pipeline. |
| ≈ 1 | Either. The boundary. |
| > 1 | VISTA. The heavier the overload, the larger the margin. |

Estimate ρ: measure your batch service time S (`compute_ms` in `metrics.csv`)
against your per-camera frame period T. The paper's load points: YOLO11n
ρ=0.84, YOLO11s ρ=1.00, YOLO11m ρ=1.86 (the primary operating point),
YOLO11l ρ=2.33.

## 1. Which policy

| Situation | Mode | Why |
|---|---|---|
| **Anything, unless you have a specific reason not to** | **`fresh`** | Freshness + fairness. VISTA-Fresh, stash 1. |
| Camera activity is **demonstrably skewed** *and* you have set `stash >= depth` | `imp` | The activity term redirects service toward active views. VISTA-Activity, stash 2. |
| Objects appear and vanish faster than the gap between a camera's servings | `salvage` | **Not evaluated in the paper.** [§8](#8-salvage--an-extra-mode-not-evaluated-in-the-paper). |
| ρ < 1 | `off` | [§0](#0-first-decide-whether-to-run-it-at-all). |

> **`fresh` is the default, not `imp`.** On the paper's uniform-activity
> workload the activity term only reshuffles *which* cameras are served —
> costing coverage (31.7% vs 38.8%) without buying recall. It is a genuine null
> on uniform activity, by design, and it is an **optional extension**, not the
> recommended starting point. The paper reflects this: VISTA-Activity's Table II
> row and Fig. 3 point show it buying nothing on uniform activity, and its real
> evaluation is Table IV, under *skewed* activity, where it has something to
> concentrate.
>
> **Provenance note on that 31.7%.** It is measured, from `e8_impfix_r*` — but
> those runs predate the `--sched-stash` flag and ran a **one-frame** stash,
> whereas the paper's VISTA-Activity is defined at stash 2. The comparison still
> makes the point (on uniform activity, importance buys nothing either way, and
> stash 2 without skew only adds age — see [§4](#4-stash--and-the-rule)), but
> the number is not a measurement of the stash-2 configuration the paper
> describes. See [`../../KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md).
>
> Some of this project's older internal notes recommended `imp` as the general
> default. **That recommendation was removed deliberately** and should not be
> reintroduced. "Free when useless" is not a reason to enable something that
> has a measured coverage cost and a configuration precondition
> (`stash >= depth`) that is easy to get wrong.

"Demonstrably skewed" means you have measured it, not that it seems plausible.
The bar: if you cannot point at a per-camera activity distribution that is
lopsided, the activity term has nothing to concentrate and you are paying for
it.

## 2. K — selection pressure

**Rule: `K ≈ N/2`.** Small enough to reject, large enough to amortise.

| K | Effect |
|---|---|
| `K = N` | **All-admit.** Every candidate is seated, so nothing is ever rejected and the value function has nothing to decide. This is the paper's ablation arm, kept to isolate what bounded stashing alone buys. Best batch efficiency (+2.3 coverage points, +8% throughput) — at e2e 151 vs 97 ms, and **the entire activity result is structurally impossible here**. |
| `K = N/2` | The recommended point. Half the candidates rejected per release, and smaller-batch latency: S(2) < S(4) with no padding on a dynamic engine. |
| `K = 1` | Maximum selectivity, but batch amortisation collapses (4·S(1) ≫ S(4)). Never sensible at N=4. |

`K < N` is the structural precondition for the value function to do anything at
all. On a uniform workload K=2 and K=4 tie on TTA — which is the *expected*
result: with nothing to choose between, selectivity cannot win. K=2 is the
default because it is the configuration in which selection *exists*, and
because its latency is genuinely ~35% lower.

If your deployment is uniform and will never use the activity term, `K = N` is
a respectable operating point. Know that you have turned the value function off.

Remember: **K is also your mux and nvinfer `batch-size`.** Changing it means
changing both.

## 3. d — release depth

The purest trade in the system: **age for GPU utilisation**.

| d | Effect |
|---|---|
| `1` | No standby batch. A released frame goes straight to an idle GPU: **e2e roughly halves**. The GPU idles between completion and the next release. Bonus: releases space further apart than the stash refill period, so even `stash = 1` can concentrate. |
| `2` | **Default.** One standby batch is always loaded, so the GPU never idles between completions. Price: the standby waits ~one service time before running, which is ~S(K) of frame age on every frame. |
| `>= 3` | Rebuilding the standing queue on purpose. Each extra unit adds ~one service time of age to every frame and buys nothing — one standby already guarantees zero GPU idle. |

**Measured, at YOLO11m / K=2** (on the paper's rig; 3 repeats each, 5 s warmup
trimmed — the runs themselves do not ship, so these are reported, not
regenerable here):

| | d = 1 | d = 2 |
|---|---|---|
| mean e2e (per rep) | 70.6 / 66.5 / 67.3 ms | 112.2 / 114.3 / 117.3 ms |
| p99 e2e | ~96–100 ms | ~183–196 ms |
| throughput (per rep) | 39.7 / 43.3 / 42.8 f/s | 42.6 / 41.7 / 40.3 f/s |

**The latency half is robust**: the two arms' e2e ranges are disjoint by a
factor of ~1.7, in both the `_v2` campaign and its predecessor.

> **The throughput half is not, and this artifact says so.** Older internal
> notes report d=1 costing ~5% throughput (40.8 vs 42.7 f/s). That pair matches
> the **first** e9 campaign (d=1: 40.4–40.9, d=2: 42.8–44.2) — which those same
> notes describe as discarded for a mid-campaign binary rebuild, and the
> archived stderr banners confirm the rebuild (the `stash=` field appears in
> `e9_depth*_v2` banners and is absent from `e9_depth2`'s). On the `_v2`
> repeat, the arms **overlap** and the medians invert (d=1: 42.8, d=2: 41.7).
> So at this operating point the d=1 throughput cost is **at or below
> rep-to-rep noise**, and the ~5% figure should not be quoted from the `_v2`
> data. The mechanism (a GPU that idles for the release round-trip, ~2 ms of a
> ~37 ms cycle) is sound and predicts a small cost; the measurement does not
> resolve it here.

**Rule: `d = 2` unless a hard sub-100 ms deadline outranks a few percent of
frames.** If you need the latency, take d=1 and measure your own throughput
rather than trusting either number above.

## 4. stash — and THE RULE

> **`stash >= depth` if the activity term is on. `stash = 1` if it is not.**

This is the paper's deployment rule and the one knob interaction that will
silently waste your time.

### The arithmetic

The `d` releases of one cycle fire **back-to-back, microseconds apart** (the
`d` in-flight batches drain through the tracker together and return their
credit in a lump). A camera offers **at most one candidate per release**, from
its stash, drained oldest-first. So per burst:

```
seats per cycle          = K * d
frames one camera can supply = min(stash, d)
max share of any camera  = min(stash, d) / (K * d)
```

| stash | Max share (K=2, d=2, N=4) | Measured |
|---|---|---|
| 1 | 1/4 = **25% — exactly the even split** | 26–29%, **even with `w_imp = 0.96`** |
| 2 (= d) | 2/4 = **50%** | **48%** |
| ≥ 3 | still 2/4 | not run — dominated |

The frames *did* arrive (~2.2 per camera per cycle). A 1-deep stash simply
cannot **keep** two of them. **The failure is retention, not arrival** — which
is exactly why no weight can fix it, and why the module warns rather than
letting you discover this yourself:

```
[vista] WARNING: stash=1 < depth=2 with importance ON. …
```

### Why not stash = 3, 4, …

Three independent reasons the benefit stops at `d`:

1. **No seat.** Frame `d+1` cannot ride the current burst — only `d` releases
   fire. By the next burst a fresher frame from the same camera stands in front
   of it, and it is a *dominated* candidate: importance and fairness are
   per-camera terms, so an older sibling can never outrank a newer one. Only
   freshness differs, and it loses.
2. **The eviction clock.** A frame at deque position `n` is ≥ `n * 33 ms` old
   before it competes. `tau_max = 150 ms` discards everything past depth ~4
   unserved. A deep stash fills with frames destined for the bin.
3. **Pool pressure.** Stashed frames pin buffer-pool surfaces. Hold too many
   and backpressure returns to the capture ring — the silent-drop zone the
   design exists to avoid. See host obligation 5.

### Why stash = 1 when importance is off

Not a limitation — **the optimal retention rule.** Oldest-first draining from a
2-deep stash makes every candidate up to one frame period older, for nothing:
without importance, no camera systematically wins twice per burst, so there is
nothing to concentrate. Measured with importance off: e2e 131 vs 118 ms, and
persistent recall *dropped*. For a pure-freshness policy, "keep exactly the
newest frame" is right.

### The other way out

`d = 1` releases space further apart than the ~33 ms refill period, so even a
1-deep stash has refilled by every audition. Same concentration, e2e roughly
halved. See [§3](#3-d--release-depth).

### On the recall numbers

The paper's Table IV reports the recall gain from `stash ≥ d` with importance
on. Its columns are **hot-camera share / brief recall / persistent recall**, all
at 250 ms, over four configurations:

| configuration | hot-camera share (%) | brief recall (%) | persistent recall (%) |
|---|---:|---:|---:|
| VISTA-Fresh, stash 1 | 25 | 25 | 47 |
| VISTA-Fresh, stash 2 | 25 | 30 | 35 |
| VISTA-Activity, stash 1 | 26–29 | 39 | 48 |
| **VISTA-Activity, stash 2** | **48** | **71** | **77** |

The headline is brief recall **0.30 → 0.71** and persistent recall **35 → 77**,
both read **stash 2 vs stash 2** — i.e. against `VISTA-Fresh, stash 2`, which
holds the stash fixed and varies only importance. That is the right baseline:
the earlier draft quoted persistent recall as **47 → 77**, which read the
stash-**1** fresh row against the stash-**2** activity row and so credited the
importance term with a gain that is partly the stash's. **The current draft
fixed that.** Note the fresh rows are non-monotonic in stash — brief recall
rises 25 → 30 while persistent recall *falls* 47 → 35 — which is exactly why the
baseline choice mattered.

Two honest notes before you quote any of it:

- The **share** numbers above (25% → 48%) are the mechanism, are directly
  implied by the arithmetic, and are consistent across every source in this
  artifact. The **recall** numbers depend on the event oracle and on which
  recall metric is used (onset time vs emission time — the tables do not all
  use the same one).
- **These recall numbers are reported, not regenerable here.** This repository
  ships no measurement data, so the table above is the draft's, quoted. The
  mechanism is checkable — run the arms yourself and the share arithmetic will
  hold on your footage — but the recall column is a property of the paper's
  clips.

The chain is: **retention (stash) → share (concentration) → recall.** Stash 2
without importance moves neither share nor recall (nothing to concentrate);
importance without stash 2 moves neither (nothing to retain). Together they
move both.

## 5. tau_max

**`tau_max = 150 ms`** by default. It is the **one deadline the design turns
on**, and it is the operator's to set.

- It is a **hard bound**: nothing older is ever admitted, whatever its score.
  Frames past it are evicted and counted before every release.
- **Set it from your application's freshness need**, not for score. A sane
  heuristic is ~Δ/2 where Δ is your deadline.
- **Raising it** re-admits stale frames — it is the latency wall in the CDF, and
  moving it moves the wall.
- **Lowering it** starts evicting frames the GPU could still have served.

Do not tune it against a metric. It is a statement about what your application
considers too late.

## 6. The weights

**Defaults: `w_f/w_i/w_r = 0.40/0.35/0.25`. Keep them.**

They were engineered, not fitted — and they have since been **measured**: a
simplex sweep of **150 runs** (25 weight points × 2 clip sets × 3 repeats) at the
K=2/stash=2/d=2 operating point, on both the brief and persistent skew clip sets.
That study was a separate campaign on a separate binary; **its data does not ship
here**, so what follows is its reported outcome, not something this repository
regenerates.

What it found:

1. **The landscape is a plateau with one cliff, not a peak.** Every point with
   `w_i >= 0.25` (and `w_r > 0`) lands on the same plateau — recall@250 medians
   0.745–0.791 (brief) / 0.794–0.813 (persistent), hot-camera share
   0.477–0.481. The five points with `w_i = 0` collapse to share 0.22–0.26 and
   recall@250 0.33–0.44 regardless of how the rest is split. **No weight
   direction improves on the default.**
2. **The `w_i` cliff is really a ramp with a knee at ≈ 0.20.** The default's
   0.35 has **1.75× margin** above it.
3. **`w_r` is an exploration budget, not a fairness nicety.** At `w_r = 0` the
   quiet camera's rare event becomes a per-run coin flip (its coverage ranged
   0.07–0.85 across reps of the same configuration). At pure importance
   (`0/100/0`) the quiet camera **starves outright: coverage 0.03** — because a
   camera that is never served can never ignite its own importance. This is the
   bootstrap failure the fairness term exists to prevent, and it is invisible on
   any headline metric.
4. The residual spread *within* the plateau does separate beyond rep noise, but
   it follows a run-to-run rhythm bistability (±9%), not any weight direction.

So: **the defaults are validated as a plateau, not as a peak.** That is a
weaker claim than "optimal" and a much stronger one than "guessed".

> An earlier version of these notes said this sweep (E5) was planned and never
> run. It has been run. Do not repeat that.

The hard bounds do the safety-critical work — `tau_max` eviction and the
fairness floor — and the weights only shuffle ranking among the ≤N in-bounds
candidates. That is why the plateau is flat, and why a non-unit weight sum only
warns: scores are compared, never thresholded.

## 7. D_fair / D_hard, and a caveat about `s_hat`

You do not set these. They are derived at runtime from the system's own
measured pace, which is what lets a heavier detector automatically earn a
longer grace period with nothing to retune:

```
D_fair = 2 * (N / K) * s_hat        # the soft fairness scale
D_hard = 4 * D_fair                 # = 8 * (N/K) * s_hat, the force-admit floor
```

`fair(c) = min(1, (now - t_served(c)) / D_fair)` rises as a camera waits. A
camera unserved past `D_hard` is **sorted ahead of value order** — that is the
fairness floor, and it is what keeps a quiet camera from starving.

Read that as a priority, not a guarantee of the next batch. In `release_once()`
the flag is `forced = since_served > d_hard_ms && !c.fresh.empty()`: a camera
with an empty stash cannot be forced, only `K` candidates are admitted per
batch (so simultaneous crossings queue), and the in-flight gate can return
before any deadline is examined.

**Do not read `D_hard` as a fixed ~1.25 s ceiling.** Because `s_hat` is measured
live, `D_hard` is per-run and per-moment. `analysis/service_gaps.py` reconstructs
it across the YOLO11m campaign at **1142–3211 ms** — the same nominal operating
point (N=4, K=2) yields end-of-run `s_hat` anywhere from 71 to 201 ms. Measured
admission gaps run p50 71–81 ms / p99 87–373 ms, with maxima of 1156–1260 ms
concentrated in the engine-load stall at run start; `e3_m/fresh-k2_r3` and
`e3_m/fresh-k2_r4` exceed their own indicative bound there. Tuning implication:
`D_hard` inherits every bit of `s_hat`'s instability, which the caveat below is
about.

> **Caveat — do not read `s_hat` from the summary line as "the batch service
> time".** The value `print_summary()` reports is the EWMA's final state, and it
> can drift far above the true service time. Measured across all 107 archived
> scheduler runs: in **64** of them the reported `s_hat` is within ~30% of the
> run's median `compute_ms` (as intended), but in **43** it exceeds it by more
> than 2× — and in the **e9 depth campaign it is inflated 11–15×** (reported
> `s_hat` 1221 ms vs a median `compute_ms` of 83 ms at d=2; 432 ms vs 39 ms at
> d=1).
>
> Two consequences. **For measurement:** use `compute_ms` from `metrics.csv` for
> service time, not the summary's `s_hat`. **For behaviour:** in the runs where
> it is inflated, `D_fair`/`D_hard` were computed from that inflated estimate,
> so the fairness floor's effective deadline was correspondingly wider —
> approaching "off" in the e9 runs. Those runs' latency and throughput numbers
> come from `metrics.csv` and are unaffected; their fairness behaviour is not
> the configuration's nominal one. The `fair_score` column in `sched.csv`
> (median 0.239 in those same runs) suggests the estimate was healthy for most
> of the run and grew late, but this artifact has not diagnosed it. Treat it as
> an open issue, not a settled one.

## 8. Salvage — an extra mode, NOT evaluated in the paper

**`--sched salvage` is not part of VISTA as published.** It appears in no table,
no figure, and no claim. It ships because the code shipped, and because the
measurements that led to it being excluded are worth publishing.

**What it does.** Each camera gets one extra slot. When a frame is displaced
from the stash, if that camera's importance is at or above
`retention_thresh` (0.30) the frame is *held* instead of dropped, and competes
in later releases with a gentler freshness dial (`tau_salvage_ms = 250` rather
than `tau_max = 150`). It has no special weight — a held frame runs the same
`v(f)`. Its knobs are `retention_thresh` and `tau_salvage_ms`; raising either
means deeper recovery, staler admissions, more fragmentation.

**What it cost, measured:**

| Cost | Measurement |
|---|---|
| It recovers almost nothing unique | Of ~580 salvaged (displaced-then-readmitted) frames per run, **435 oracle-matched detections contained zero unique information** within a ±150 ms neighbour window; **9 (2.1%)** were unique within the median inter-service gap. **≤2% unique recoveries.** |
| It regresses timestamps | Cross-release re-admissions send a camera's timestamps backwards: **1.4–6.4% of emissions** carry a per-camera timestamp regression. Every other mode: **0**. (VISTA's stash drains oldest-first precisely to avoid this.) |
| It fragments tracks | Live: **79 distinct track IDs vs 55** for the activity mode and 44 for the stock pipeline — measurable fragmentation, consistent with the replay null. (From the earlier draft's live evaluation; the current paper does not include salvage at all.) |

**So: leave salvage off** unless objects genuinely appear and vanish faster than
the gap between a camera's servings — the one regime held slots target — and
even then, watch your track IDs.

The reason the null is published rather than buried: the mechanism was this
project's own idea, and it does not pay on these workloads. Salvage also needs
`max-same-source-frames=2` in the mux INI (one camera contributing its fresh
*and* its held frame to one batch); that setting is why the shipped scheduler
INI carries it, and it is inert in `fresh`/`imp`.

## 9. What not to touch

| Knob | Why not |
|---|---|
| **The weights** | Validated as a plateau by 150 runs. No direction improves on them; `w_i` has 1.75× margin above its knee; `w_r = 0` breaks the quiet camera. [§6](#6-the-weights) |
| **`tau_max`** | Not a score knob. It should track your deadline. [§5](#5-tau_max) |
| **`imp_max` / `imp_halflife_s`** | Sized so ~0.7 new tracks/s saturates. If you change them, you are redefining what "active" means — measure the resulting `imp_score` distribution. If its median is 1.000, you have rebuilt the saturation trap. |
| **`tau_salvage_ms` / `retention_thresh`** | Only matter with salvage on, which it should not be. [§8](#8-salvage--an-extra-mode-not-evaluated-in-the-paper) |
| **The mux INI** | **Not a tuning surface — a correctness contract.** Get it wrong and batches silently merge or split. [Host obligations](../integration/03-pipeline-obligations.md). |
| **`strict` / `gate_check`** | Not tuning. They are the checks that catch the silent failures. |

---

## Situation → settings

| Situation | mode | K | d | stash |
|---|---|---|---|---|
| **Don't know / general default** | **`fresh`** | N/2 | 2 | **1** |
| Uniform activity, latency-relaxed, want max coverage | `fresh` | N | 2 | 1 |
| Hard deadline < 100 ms | `fresh` | N/2 | **1** | 1 |
| **Demonstrably skewed activity** | `imp` | N/2 | 2 | **2** (= d) |
| Skewed activity **and** a hard latency deadline | `imp` | N/2 | **1** | 1 |
| Detector keeps up (ρ < 1) | **`off`** | — | — | — |
| More cameras (N > 4) | as above | ≈N/2 | 2 | per the rule | 
| Heavier model (ρ ≫ 1) | as above | N/2 | 2 | per the rule |

The last two rows have nothing to retune on purpose: `D_fair = 2(N/K)·Ŝ(K)`
auto-scales the fairness deadlines with both the camera count and the
detector's measured pace, memory grows linearly in N (a few refs per camera),
and the completion clock slows releases automatically as service time grows.
That is the design's central claim about deployment: the knobs that matter are
the four on this page, and three of them have a default you should keep.
