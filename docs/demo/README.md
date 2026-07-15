# The interactive scheduler demo

Two files, both self-contained:

| file | what |
|---|---|
| [`vista_scheduler_animation.html`](vista_scheduler_animation.html) | an interactive, discrete-event **simulation** of the VISTA admission cycle |
| [`vista_signal_flow.png`](vista_signal_flow.png) | a static diagram of the same signal path, for slides and for readers who want one picture |

---

## Read this before you quote a number off the screen

**The animation is a simulation, not a measurement.** It implements the real
algorithm — the same arrival → stash → gate → evict → score → top-K → completion-credit
cycle that `vista/src/vista_scheduler.cpp` runs — but it drives that algorithm with
**illustrative timing**: a made-up service time and a made-up arrival rate, chosen so
that a human can watch the decisions happen. It does not read the archive, it does not
replay a clip, and it has never touched a GPU.

Consequently **every number the page displays is directional only**:

- the **~42% coverage** it settles at is an artifact of the simulated service time and
  arrival rate. It is *not* VISTA-Fresh's measured coverage (38.8% at YOLO11m, ρ=1.86);
- the hot-camera share it converges on is *not* Table IV's measured 48%;
- the cap-vs-concentrate *magnitudes* are directional. The *mechanism* is real; the
  quantities are not.

The measured numbers are the draft's, and this repository does not ship the data behind
them. Where a measured number and this page disagree, the page is wrong on purpose — it
is a teaching aid. Where this code disagrees with the draft:
[`../../KNOWN-ISSUES.md`](../../KNOWN-ISSUES.md).

The page carries this warning at the top of its own body, so a screenshot of it cannot
be mistaken for a result.

## How to open it

Open `vista_scheduler_animation.html` in any browser. Double-click it, or:

```sh
xdg-open docs/demo/vista_scheduler_animation.html    # Linux
open     docs/demo/vista_scheduler_animation.html    # macOS
```

**No server, no build, no network, no dependencies.** It is one HTML file with its CSS
and JavaScript inline; it loads no fonts and no scripts from anywhere. It works offline,
from a `file://` URL, and it follows your system's light/dark setting.

## What you are looking at

Four ~30 fps cameras feed a per-camera bounded stash. Frames age in the stash
(colour tracks age: fresh → aging → near `τ_max` = 150 ms, at which point they are
evicted and counted). A batch releases only while the in-flight credit gate is open;
when it opens, each camera offers **one** candidate — its oldest stashed frame — the
candidates are scored by `v(f) = w_f·fresh + w_i·imp + w_r·fair`, and the top K are
released. Completions return credit and re-open the gate.

Two panels make the accounting visible:

- **Decision log** — every frame's fate, one line each, in the spirit of `sched.csv`.
- **Processed share per camera** — the concentration, which is what the activity term
  is for.

The arrivals / admitted / dropped counters demonstrate the property that matters:
**arrivals == admitted + counted drops, always.** No frame leaves without a line in the
ledger. That is the invariant gate G2 asserts on a real run
([`../reproduction/01-run-the-experiments.md`](../reproduction/01-run-the-experiments.md)),
and the one the minimal example asserts on every invocation.

## What to try

The defaults are **K=2, d=2, stash 1, importance ON**.

| do this | and watch |
|---|---|
| **Toggle importance off, then on** | Off: service levels out across the four cameras — every camera near its 25% even share. On: the active camera (cam 0) pulls ahead. This is the value function's whole job. |
| **Set d = 3** | The in-flight credit climbs to 6 and **a standing queue re-forms**. Frames sit longer, ages climb, and throughput does not improve — one standby batch already keeps the GPU busy. This is the animated version of why `d ≥ 3` is a mistake. |
| **Set K = 4** | Every camera fits in every batch, so nothing is ever rejected and **the ranking stops mattering.** `K < N` is the structural precondition for the value function to do anything — the all-admit ablation, live. |
| **Turn importance on and flip stash 1 → 2** | The one interaction worth the visit. See below. |
| **Drag speed down, or use Step ▸** | Read individual decisions in the log rather than watching the aggregate. |

### The stash × depth rule, which is the point

The default (importance ON with stash 1 at d = 2) is deliberately **the
misconfiguration the module warns about**:

```
[vista] WARNING: stash=1 < depth=2 with importance ON. …
```

It is the default here because it is the thing worth seeing. With `stash < d`, a camera
can supply a frame to the *first* release of a burst and has nothing left for the
second, so its share is capped at `min(stash,d) / (K·d)` = 25% — the even split — **no
matter how high you push importance.** Flip stash to 2 and the cap lifts.

The paper's deployment rule follows from exactly this: **`stash ≥ d` if the activity
term is on; `stash = 1` if it is not.** The full arithmetic, and the measured numbers
behind it, are in [`../design/04-depth-and-stash.md`](../design/04-depth-and-stash.md)
and [`../usage/06-tuning.md`](../usage/06-tuning.md) §4.

## The static diagram

`vista_signal_flow.png` shows the same signal path without the animation: where VISTA
intercepts (before the shared batcher), what it holds, what triggers a release, and
where the counted drops go. Use it in slides. For the element-by-element pipeline
reality — including where frames die in the *stock* path — see
[`../design/02-deepstream-pipeline.md`](../design/02-deepstream-pipeline.md) and
[`../design/03-backpressure.md`](../design/03-backpressure.md).
