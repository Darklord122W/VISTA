# VISTA documentation

Companion to **"VISTA: Value-Driven Inference Scheduling for Timely Autonomous
Multi-Camera Perception"** (IEEE L-SMC, 2026).

VISTA is a completion-clocked load-shedding scheduler that sits in front of
DeepStream's `nvstreammux`. When a batched detector cannot keep up with its
cameras, some frames must be dropped; VISTA makes that an explicit, scored,
counted decision instead of leaving it to transport backpressure.

**Start with [`00-quickstart.md`](00-quickstart.md)** — build the module, run the
minimal example.

Or **watch it instead**: [`demo/`](demo/README.md) is a self-contained HTML page
that animates the admission cycle in any browser, no server. It is a
**simulation with illustrative timing** — the algorithm is real, the numbers on
screen are directional and are not measured results.

> **What this documentation cannot do.** This repository ships the code, not the
> paper's measurement archive. These pages *describe* what was measured and cite
> the draft by title, but **no table or figure in the draft is regenerable from
> this repository**, because the evidence for it is not distributed here. Where a
> number appears below, it is either a property of the shipped code, a
> configuration constant you can read, or a reported measurement labelled as
> such.

---

## Two reading paths

The same two the root [`../README.md`](../README.md) names: **use the scheduler**
and **take your own measurements**.

### Use the scheduler — "I want VISTA in my pipeline"

You have a multi-camera pipeline and an overload problem.

1. [`00-quickstart.md`](00-quickstart.md) — build it, see it run.
2. [`design/01-overview.md`](design/01-overview.md) — the capacity wall. **Read
   this even if you skip everything else in `design/`**: if `rho <= 1` you do
   not need VISTA, and §4 tells you what it costs when you do.
3. [`integration/01-integration-guide.md`](integration/01-integration-guide.md) —
   attach the module.
4. [`integration/03-pipeline-obligations.md`](integration/03-pipeline-obligations.md) —
   **the four things your host must guarantee.** The most common failure is a
   mux INI that silently splits your K-burst; the module warns at runtime, and
   this page explains why the obvious configuration-time check is wrong.
5. [`integration/02-api-reference.md`](integration/02-api-reference.md) — `SchedCfg`,
   `Stats`, the teardown ordering (which is load-bearing).
6. [`usage/06-tuning.md`](usage/06-tuning.md) — K, depth, stash, weights.
7. [`integration/04-porting-checklist.md`](integration/04-porting-checklist.md) —
   not on DeepStream? VISTA needs three things, none of them DeepStream-specific.
8. [`integration/05-troubleshooting.md`](integration/05-troubleshooting.md).

**The two pages that will save you a week:**
[`design/04-depth-and-stash.md`](design/04-depth-and-stash.md) (if you turn on
the activity extension with `stash < depth`, it does nothing, and nothing tells
you) and [`../KNOWN-ISSUES.md`](../KNOWN-ISSUES.md).

### Take your own measurements — "I want numbers"

You have a Jetson. **This is the only path to numbers that exists here**: the
archive behind the draft's tables is not distributed, so there is nothing to
re-score and no laptop tier. You run the campaign yourself, on your own footage,
and the numbers you get are yours.

1. [`reproduction/01-run-the-experiments.md`](reproduction/01-run-the-experiments.md)
   — **the whole path**: build, engines, clips, the injection recipe, the gates,
   the campaigns, the live rig, scoring. Read the opening section before planning
   anything: no bit-exactness, replay latencies are not live latencies, and your
   oracle is clip-specific.
2. [`usage/01-build.md`](usage/01-build.md) → [`usage/02-models-and-engines.md`](usage/02-models-and-engines.md)
   — no weights ship; here are the download and build recipes. Budget ~37 min for
   the YOLO11x oracle engine alone.
3. [`usage/03-cli-reference.md`](usage/03-cli-reference.md), [`usage/04-config-reference.md`](usage/04-config-reference.md).
4. [`usage/05-outputs.md`](usage/05-outputs.md) — what the files mean.
5. [`reproduction/03-code-provenance.md`](reproduction/03-code-provenance.md) —
   why the harness records a hash of the binary, and what happened when it did
   not.

**Traps that produce plausible wrong numbers, silently:** `--gap-every` is
overloaded (44 = fidelity, 3 = the decimation *baseline*); an unpinned GPU clock
flatters the stock baseline; `mux_batch` and `drops_cum` in `metrics.csv` do not
mean what they say ([`../KNOWN-ISSUES.md`](../KNOWN-ISSUES.md)).

---

## Everything, by directory

### `demo/` — see it move

| | |
|---|---|
| [`demo/README.md`](demo/README.md) | what the demo is, how to open it, and what to try |
| [`demo/vista_scheduler_animation.html`](demo/vista_scheduler_animation.html) | interactive **simulation** of the admission cycle — any browser, no server. Illustrative timing; its numbers are directional, **not** measured. |
| [`demo/vista_signal_flow.png`](demo/vista_signal_flow.png) | static signal-path diagram, for slides |

### `design/` — how it works and why

| | |
|---|---|
| [`01-overview.md`](design/01-overview.md) | the capacity wall: `rho`, `S(K)`, the funnel, what load shedding buys |
| [`02-deepstream-pipeline.md`](design/02-deepstream-pipeline.md) | element by element; the replay front-end; `--replay-surfaces` |
| [`03-backpressure.md`](design/03-backpressure.md) | where frames actually die: pool → converter → ring → kernel |
| [`04-depth-and-stash.md`](design/04-depth-and-stash.md) | the retention mechanism, the arithmetic, and the wrong turn |
| [`05-scheduler-internals.md`](design/05-scheduler-internals.md) | the full cycle, threading, buffer ownership, the importance trap |
| [`06-local-clocks.md`](design/06-local-clocks.md) | why VISTA never uses PTS |

### `integration/` — putting it in your pipeline

| | |
|---|---|
| [`01-integration-guide.md`](integration/01-integration-guide.md) | attach the module |
| [`02-api-reference.md`](integration/02-api-reference.md) | `SchedCfg`, `Stats`, `Scheduler` |
| [`03-pipeline-obligations.md`](integration/03-pipeline-obligations.md) | what your host must guarantee |
| [`04-porting-checklist.md`](integration/04-porting-checklist.md) | beyond DeepStream |
| [`05-troubleshooting.md`](integration/05-troubleshooting.md) | when it does not work |

### `usage/` — running the application

| | |
|---|---|
| [`01-build.md`](usage/01-build.md) | build the library and the app |
| [`02-models-and-engines.md`](usage/02-models-and-engines.md) | download weights, build engines (none ship) |
| [`03-cli-reference.md`](usage/03-cli-reference.md) | every flag |
| [`04-config-reference.md`](usage/04-config-reference.md) | YAML and the mux INIs |
| [`05-outputs.md`](usage/05-outputs.md) | `metrics.csv`, `dets.jsonl`, `sched.csv`, `run_meta.json` |
| [`06-tuning.md`](usage/06-tuning.md) | K, depth, stash, weights, `tau_max` |

### `reproduction/` — taking your own measurements

| | |
|---|---|
| [`01-run-the-experiments.md`](reproduction/01-run-the-experiments.md) | the rig, the injection recipe, the gates, the campaigns, scoring |
| [`03-code-provenance.md`](reproduction/03-code-provenance.md) | which binary — and why that is a hard question |

### `appendix/`

| | |
|---|---|
| [`glossary.md`](appendix/glossary.md) | `rho`, `S(K)`, depth vs stash, Window A vs B, TTA vs onset recall |

### Repository root

| | |
|---|---|
| [`../KNOWN-ISSUES.md`](../KNOWN-ISSUES.md) | where this code disagrees with the draft, and its own defects |
| [`../NAMING.md`](../NAMING.md) | SPARQ → VISTA; the draft's policy names as CLI configurations |
| [`../vista/PAPER_DIFF.md`](../vista/PAPER_DIFF.md) | how the shipped module differs from the code that produced the paper's numbers |
| [`../analysis/campaigns.yaml`](../analysis/campaigns.yaml) | the machine-readable campaign registry the analysis code resolves rows through. If prose and this file disagree, the file is right. |

---

## What is not here

- **The measurement archive.** No run directories, no scored aggregates, no
  generated figures. This is the big one: **the draft's tables cannot be
  regenerated from this repository**, and no page here pretends otherwise. The
  analysis and harness code ships and works — against data you produce.
- **The manuscript.** No `.tex`, no PDF, no `paper/` directory. This
  documentation describes the work and cites it by title; it does not reproduce
  it.
- **The camera footage.** It shows an identifiable person. Any 4-camera footage
  exercises the mechanism; the event oracle is clip-specific, so numbers taken on
  other footage are not comparable to the draft's.
- **Model weights and TensorRT engines.** Engines are hardware- and
  version-specific and would be **wrong** elsewhere.
  [`usage/02-models-and-engines.md`](usage/02-models-and-engines.md) has the
  recipes.
- **The application's git history.** See
  [`reproduction/03-code-provenance.md`](reproduction/03-code-provenance.md) for
  what that costs and what survives.

## A note on tone

This documentation names its own defects, and there are a fair number. That is
deliberate. What is imperfect is mostly *bookkeeping* — a build identity that was
never recorded, a metric column that is dead, a configuration whose label does
not describe it. Those are worth stating precisely, because a reader who finds
one unannounced is entitled to wonder what else went unmentioned.

Where the code and the draft disagree, the disagreement is documented rather than
smoothed over; the draft is the authors' to change.
