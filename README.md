# VISTA

**"VISTA: Value-Driven Inference Scheduling for Timely Autonomous Multi-Camera
Perception"** submitted to IEEE L-SMC, 2026.

The scheduler as a drop-in module for a DeepStream pipeline, a reference
application that runs it, and the analysis and harness code behind the paper's
experiments. **The measurement archive does not ship** — see
[what is and is not here](#what-this-repository-is-and-is-not).

---

## What VISTA is

Several cameras share one embedded GPU through a batched detector. When the
batch takes longer than the camera frame period, demand exceeds capacity and some
frames can never be processed — no tuning changes that; it is arithmetic. On a
stock pipeline nobody decides which frames die: transport backpressure does, and
it decides badly and invisibly, overwriting the *newest* arrivals in the kernel
capture ring upstream of every statistic the application can see, while the
survivors emerge hundreds of milliseconds stale.

VISTA makes that decision explicit, bounded and counted. It intercepts frames
ahead of `nvstreammux` into a bounded per-camera stash and, at each inference
completion, scores every candidate and releases the top-K:

```
v(f) = w_f * fresh(f)  +  w_i * imp(camera)  +  w_r * fair(camera)
        0.40              0.35 (optional)       0.25
```

with a hard staleness bound (`tau_max = 150 ms`), a per-camera service deadline
(`D_hard`), and a ledger entry for **every** dropped frame:
`arrivals == admitted_fresh + admitted_salvage + policy_drops`, exactly. It does
not create capacity. It changes who decides, which frames die, and whether anyone
is told.

Modes are `off | fresh | imp | salvage`. **VISTA-Fresh** (freshness + fairness,
`w_i = 0`, stash 1) is the general-purpose default; **VISTA-Activity** (`imp`,
stash ≥ depth) is an optional extension for demonstrably skewed workloads.

## Quick start

Needs a Jetson-class board with DeepStream 7.1. Nothing here needs the paper's
data, because nothing here reproduces the paper.

```sh
# 1. The module: one header, one translation unit, no build system of its own.
g++ -std=c++17 -O2 -Wall -Wextra -c vista/src/vista_scheduler.cpp \
    -o vista_scheduler.o -Ivista/include \
    -I/opt/nvidia/deepstream/deepstream/sources/includes \
    $(pkg-config --cflags gstreamer-1.0)

# 2. The integration template: the smallest complete pipeline VISTA can drive.
cd vista/examples/minimal_pipeline && make
./minimal_pipeline --clips ./clips --cams 4 --k 2 --mode fresh --duration 30
```

The example replays four clips of **your own** (`cam0.mp4 .. cam3.mp4`; none
ship) against a model engine **you have built**
([`docs/usage/02-models-and-engines.md`](docs/usage/02-models-and-engines.md)).
It asserts `ledger_closes()` on every invocation, and its release count is the
evidence that your mux INI is batching K frames atomically rather than splitting
the burst silently.

Ten more minutes: [`docs/00-quickstart.md`](docs/00-quickstart.md).

## The repository

| | |
|---|---|
| [`vista/`](vista/README.md) | **the deliverable.** The scheduler: one header, one `.cpp`, plus the mux INI it requires and the [`minimal_pipeline`](vista/examples/minimal_pipeline/README.md) integration template. Knows nothing about the experiments. |
| [`app/`](app/README.md) | the reference application — the multi-camera DeepStream detect/track app that ran the campaigns, with the replay pacer, the skew injection and the metrics writers |
| [`harness/`](harness/README.md) | the campaign runner: gates, policy campaigns, the live rig, and the per-run provenance record |
| [`analysis/`](analysis/README.md) | scoring and table generation — oracle construction, event matching, per-arm aggregates |
| [`figures/`](figures/README.md) | figure sources and the system diagram |
| [`config/`](config/README.md) | camera params, the pgie configs, and the mux INIs (**a correctness contract, not a tuning surface**) |
| [`scripts/`](scripts/README.md) | fetch models, build engines, record clips, detect cameras |
| [`docs/`](docs/README.md) | design, integration, usage, and how to run the experiments |
| [`third_party/`](third_party/DeepStream-Yolo/PROVENANCE.md) | the vendored YOLO parser, with its upstream provenance |



## Start here

[`docs/README.md`](docs/README.md) lays out two reading paths — **use the
scheduler** and **take your own measurements** — and indexes everything.

If you would rather see it than read it, there is an interactive demo:
[`docs/demo/`](docs/demo/README.md) — one self-contained HTML file, opens in any
browser with no server. It animates the real admission cycle, and it is a
**simulation with illustrative timing**: its numbers are directional and are not
measured results. The demo README says so precisely.


