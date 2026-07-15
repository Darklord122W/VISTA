# Quickstart

Build the scheduler and run it in a pipeline. That needs a Jetson-class board
with DeepStream 7.1, four clips and a model engine — it is the whole of what this
repository can show you, because **no measurement data ships here**. There is
nothing to regenerate on a laptop.

---

## 1. The idea, in sixty seconds

Four cameras, one embedded GPU, one shared detector. At the paper's primary
operating point the detector's batch takes ~62 ms while the cameras deliver a
frame every ~33 ms. **Demand exceeds capacity by 1.86x, so roughly 46% of frames
can never be processed** — not by any tuning, on any schedule. That is
arithmetic, not a bug.

The question is *which* frames die, and who decides.

On a stock pipeline, nobody decides. Transport backpressure does, and it makes
three choices nobody chose:

- it drops the **newest** frames (the freshest data dies first);
- it drops them in the **kernel capture ring**, upstream of every counter the
  application has — so the pipeline's own coverage statistic reads **100%** while
  half the frames are gone;
- the survivors emerge through a standing queue **hundreds of milliseconds
  stale** — and the queue depth is a *buffer pool setting*, not a property of the
  workload.

VISTA intercepts frames before the batcher into a small bounded per-camera stash,
and at each inference completion releases the top-K by value:

```
v(f) = w_f * fresh(f)  +  w_i * imp(camera)  +  w_r * fair(camera)
        0.40              0.35 (optional)       0.25
```

with a hard 150 ms staleness bound (`tau_max`), a per-camera service deadline
(`D_hard`), and **every drop counted**. It does not create capacity. It changes
who decides, which frames die, and whether anyone is told.

## 2. Build the module

The scheduler is one header and one translation unit. It has no build system of
its own on purpose — it is two files you add to your project:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -c vista/src/vista_scheduler.cpp \
    -o vista_scheduler.o \
    -Ivista/include \
    -I/opt/nvidia/deepstream/deepstream/sources/includes \
    $(pkg-config --cflags gstreamer-1.0)
```

Clean, zero warnings, a few seconds. Full flag-by-flag explanation and the link
line: [`usage/01-build.md`](usage/01-build.md).

## 3. Run the minimal example

[`vista/examples/minimal_pipeline/`](../vista/examples/minimal_pipeline/README.md)
is the smallest complete DeepStream pipeline VISTA can drive. It is a
**template**, not a demo: every element name, property and teardown step exists
to discharge a specific requirement of `vista::Scheduler::attach()`, and each is
commented with the requirement it satisfies.

```bash
cd vista/examples/minimal_pipeline
make
./minimal_pipeline --clips ./clips --cams 4 --k 2 --mode fresh --duration 30
```

It replays clips rather than opening cameras, so it needs no camera rig — but it
does need **four `cam<i>.mp4` files of your own** (none ship) and a **model
engine you have built** ([`usage/02-models-and-engines.md`](usage/02-models-and-engines.md)).
On the first launch with no prebuilt engine, nvinfer builds one and that takes
minutes; the app warns.

What you should see:

```
[vista] mode=fresh k=2 depth=2 stash=1 tau_max=150ms tau_salvage=250ms w=(0.40,0.35,0.25)
[vista] fresh: 696 releases (40.3/s), 1392 fresh + 0 salvage admitted, 588 policy drops,
        s_hat 46.4 ms over 17.3 s.
[app] ledger: 1980 arrivals = 1392 fresh + 0 salvage + 588 drops -> CLOSES
```

Two things are being demonstrated there, and they are the two claims the module
makes about itself:

- **The K-burst lands as one batch.** 1392 admits / 696 releases = exactly 2.0
  frames per release. If your mux INI is wrong this degrades *silently* — batches
  merge or split, the run completes, the metrics look plausible, and you are not
  measuring the policy you asked for. The example asserts it rather than hoping.
- **The ledger closes.** `arrivals == fresh + salvage + drops`, exactly, checked
  by `assert(st.ledger_closes())` on every invocation. Every dropped frame is
  accounted for. That is the accountability claim, and it is an assertion in the
  code, not a sentence in a paper.

## 4. What you just did — and did not

**Did:** build the scheduler, attach it to a real DeepStream pipeline, and watch
it shed load under a policy with a closing ledger.

**Did not:** reproduce anything from the paper. **This repository ships no
measurement data**, no camera footage and no model weights. Its numbers are not
regenerable here — not because the code is missing, but because the evidence is
not distributed with it. If you want measurements, you take your own:
[`reproduction/01-run-the-experiments.md`](reproduction/01-run-the-experiments.md).

## 5. Where next

| you are… | go to |
|---|---|
| putting VISTA in your pipeline | [`integration/01-integration-guide.md`](integration/01-integration-guide.md), then [`integration/03-pipeline-obligations.md`](integration/03-pipeline-obligations.md) — the things your host must guarantee |
| deciding whether you need it at all | [`design/01-overview.md`](design/01-overview.md) §4 — if `rho <= 1`, you do not |
| tuning it | [`usage/06-tuning.md`](usage/06-tuning.md) — K, depth, stash, weights |
| taking your own measurements | [`reproduction/01-run-the-experiments.md`](reproduction/01-run-the-experiments.md) |
| wanting to *see* the algorithm | [`demo/`](demo/README.md) — the animation. A simulation; its numbers are illustrative, not measured. |

**Before you turn on the activity extension:**
[`design/04-depth-and-stash.md`](design/04-depth-and-stash.md). With
`stash < depth` it does nothing at all, silently, no matter how you set the
weights. That is the one configuration mistake that costs a week.
