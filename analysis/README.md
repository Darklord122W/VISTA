# analysis/ — scoring and table generation

Everything the paper reports is downstream of this directory. The pipeline
takes raw run directories, builds an event oracle from a reference detector,
scores each policy run against it, and emits the tables.

**The runs are not here.** This repository ships the scoring code, not the
measurements: point `$VISTA_DATA_ROOT` at a directory of campaign run
directories you produced (`harness/run_campaign.sh`), or every script below
exits 2 with one message saying exactly this. There is no default and no
in-repo fallback — a default that resolved to an empty directory would let a
scoring run "succeed" over no data, which is the one outcome worse than a
failure.

Two rules hold throughout:

1. **`campaigns.yaml` is the only place a run path is written down.** No
   script hardcodes one. Its paths are relative to `$VISTA_DATA_ROOT`. If you
   want to know which directories back a paper row — and which of the original
   labels for them are wrong — that file is the answer, and it carries the
   evidence for each claim it makes. It is the specification of the experiment,
   which is why it ships even though the runs do not.
2. **A cell that does not reproduce is a finding.** The generators compare
   themselves against the paper's printed values on `--check`. Report a
   mismatch; do not tune the generator until it agrees. Note what `--check`
   means against *your* data: it compares your runs to numbers measured on the
   authors' rig and clips, so mismatches there are differences, not defects.

## Quick start

```bash
export VISTA_DATA_ROOT=/path/to/runs           # produced by harness/run_campaign.sh
python3 _campaigns.py                          # verify every path in campaigns.yaml resolves
python3 make_all.py --tier rescore             # score the raw dets: oracles, recall JSONs, tables, figures
python3 make_all.py --tier derived             # recompute only what sits above those JSONs
```

`--tier rescore` makes the table generators rebuild each oracle and re-score
every run from the raw `dets.jsonl[.gz]`, ignoring any `recall_*.json`. It is
the tier that *creates* the derived data, so it is the one to run first on a
fresh data root. `--tier derived` then trusts those JSONs and recomputes only
what sits above them; running both and getting the same cells is the
cross-check that the derived data faithfully represents the raw data.

Path resolution, all of it in `_paths.py`:

| variable | default | is |
|---|---|---|
| `$VISTA_DATA_ROOT` | **none — required** | directory of campaign run dirs |
| `$VISTA_DERIVED_DIR` | `$VISTA_DATA_ROOT/derived` | scored JSONs (an output) |
| `$VISTA_FIG_DIR` | `<repo>/figures/generated` | figures and .tex fragments (an output) |

`figures/src/_paths.py` reads `$VISTA_DATA_ROOT` the same way; the two used to
disagree about whether it named the campaigns directory or its parent.

Requires Python 3, `pyyaml`, and `matplotlib` for the figure scripts.

## Dependency order

This is the real chain. Each stage consumes only the stage above it.

```
raw run dirs  ($VISTA_DATA_ROOT/<campaign>/<run>/: metrics.csv, dets.jsonl[.gz], sched.csv, stderr.log)
     |
     |  match_events.py       build the oracle from the reference detector's dets;
     |                        score each run: coverage, yield, event_recall, tta_recall
     v
$VISTA_DATA_ROOT/derived/recall_*.json
     |
     |  clean_events.py       stratify the 123 events into all / clean / person
     |  enriched_analysis.py  validate the mean-age approximation against true emission stamps
     v
     |  policy_report.py      per-arm medians for ONE campaign dir  -> e3_<model>_aggregate.json
     v
     |  make_table2.py        Table II   (composite across FOUR campaigns)
     |  make_table3.py        Table III  (YOLO11s / YOLO11l)
     |  make_table4.py        Table IV   (always rescored; no derived tier exists)
     |  live_report.py        Table V
     |  e1_figures.py         Table I + capacity figures
     v
$VISTA_FIG_DIR/*.{tex,pdf}      (default figures/generated/, not committed)
```

`make_all.py` drives all of it. Note that `policy_report.py` sees exactly one
campaign directory, which is why it can produce Table III but *cannot*
produce Table II — Table II draws on four campaigns and was assembled by
hand. `make_table2.py` exists to make that composition explicit and checkable.

## What each script does

| script | purpose |
|---|---|
| `_paths.py` | Path resolution + the gz-aware, tolerant dets reader. The only module that knows where data lives. |
| `_campaigns.py` | Loader for `campaigns.yaml`. Refuses to silently resolve a row to fewer repeats than it declares. |
| `_sched_log.py` | Parses the scheduler's stderr summary — **the drop ledger lives there, not in `sched.csv`**. Accepts both log prefixes. |
| `match_events.py` | Oracle construction and per-run scoring. The root of everything. |
| `policy_report.py` | Per-arm medians + min/max for one `e3_<model>` campaign. |
| `clean_events.py` | Event-quality stratification (the "63 clean events") + same-model yield decomposition. |
| `enriched_analysis.py` | Exact-TTA validation from the instrumented build; salvage actionability. |
| `make_table2.py` | Table II. Resolves two mislabelled provenance strings; `--check` against the paper. |
| `make_table3.py` | Table III. Warns that its VISTA-Activity rows are pre-bugfix code. |
| `make_table4.py` | Table IV. Rescores from raw dets; **uses `event_recall`, not `tta_recall`**. |
| `live_report.py` | Table V. Verifies the drop ledger closes. |
| `skew_report.py` | Hot-camera service share — the 25% → 48% headline. Nothing else computes it. |
| `event_split.py` | The 37/33/28/25 oracle event split. |
| `service_gaps.py` | Per-camera admission gaps vs a per-run reconstruction of D_hard. |
| `e1_figures.py` | Table I (capacity), `fig_capacity_wall`, service times. |
| `frame_funnel_fig.py` | Where paced frames die, and why pipeline coverage cannot see it. |
| `tta_curve.py` | TTA recall curves. |
| `count_drops.py` | True transport-ring drops per model, using YOLO11n as the paced-input reference. |
| `reproduce_spread.py` | Cross-camera capture-time spread. Reads a frame-timing capture, not an inference run. |
| `make_all.py` | Driver. |
| `weightsweep/` | The System B weight-sweep study's own aggregator. See below. |

## Metric definitions, as implemented

These describe what the code computes, which is not always what the prose
says. Where they differ, that is noted.

**Oracle events.** YOLO11x at ring=0 (every frame processed), detections
filtered at `confidence >= 0.40`. Greedy IoU tracking per `(camera, class)`:
a detection extends the active track it best matches at `IoU >= 0.30`,
otherwise it starts a new track; a track idle for more than 500 ms of replay
time expires. A track must persist `>= 3` frames to become an event (this
kills single-frame flicker). Event time `t0` = its first frame's `buf_pts`.
The office replay yields **6415 oracle frames, 9715 detections, 123 events** —
reproduced exactly during this artifact's assembly.

**`buf_pts` is a frame identity key**, not a wall clock. It indexes the replay
timeline and is deterministic across runs because skew injection is
index/PTS-deterministic. That determinism is what makes "did this policy
process the same frame the oracle did" a well-posed question.

**Coverage** = |oracle frames the policy also processed| / |oracle frames|.

> **The denominator is not all frames.** It is the frames carrying at least
> one `conf >= 0.40` YOLO11x detection: **6415**, not the **6882** frames the
> cameras actually produced (verified: the oracle run's `dets.jsonl` holds
> 6882 distinct `(cam, pts)` keys and `arrivals_cum` = 6882; 6415 of them
> carry a conf≥0.40 detection). Coverage is therefore **≈7.3% optimistic**
> relative to the all-frames definition the paper's prose implies. It is
> optimistic *by the same construction for every policy*, so the comparison
> between policies — which is what every claim rests on — holds. Absolute
> coverage values should be read with the 7.3% in mind.

**Detection yield** = matched oracle detections / **all** oracle detections
(not just those on processed frames). A match requires the same `class_name`
and `IoU >= 0.30`; each oracle detection consumes at most one policy
detection. This is the coverage-weighted quantity: a policy that skips a frame
forfeits every detection on it.

**Detection recall on processed frames** = the same numerator over the oracle
detections on processed frames only. Yield ÷ this ≈ coverage; the pair
separates *scheduling* loss from *detector* loss.

**Output age** — mean and p99, from `metrics.csv`, both with:
- a **5 s warmup trim**: rows with `t_mono < t_mono[0] + 5.0` are dropped;
- **negative sentinels excluded**: `e2e_ms < 0` marks a batch whose capture
  stamp was unavailable. Those rows are dropped, not clamped;
- **p99 = `sorted(e2e)[int(0.99 * (n - 1))]`** — nearest-rank, no
  interpolation. At n ≈ 1000 batches this is a real sample, but it is the
  99th-percentile *batch*, not frame.

**TTA@Δ (`tta_recall`)** — the fraction of oracle events whose first matching
policy detection is *emitted* within Δ of onset: `onset_delay + mean_e2e <= Δ`.
Every match is charged the run's **mean** age, because standard `dets.jsonl`
carries no emission stamp.

> **This approximation is good for VISTA and poor for stock.** The instrumented
> build does carry per-detection `t_emit`, and `enriched_analysis.py` measures
> the error: worst |exact − approx| = **0.008** for `imp`, **0.033** for
> `salv`, **0.041** for `fifo`. Charging the mean is accurate when the age
> distribution is tight (VISTA: p99/mean ≈ 1.4) and inaccurate when it is wide
> (a deep standing queue). Consequence worth knowing: at Δ=500 ms the stock arm
> scores exact **0.016** vs approx **0.000**, so Table II's "Stock-Default
> TTA@0.5s = 0.0" and the prose's "recalls *zero* events within 500 ms" are
> artifacts of the approximation at the 2-event level.

**Event recall@Δ (`event_recall`)** — the same, in **frame time**:
`onset_delay <= Δ`, with no age charged.

> **Tables II and III use `tta_recall`. Table IV uses `event_recall`.** Both
> live in the same JSONs under adjacent keys. The gap is material: on the brief
> clip set `event_recall@250` medians .723 against `tta_recall@250` at .682.
> The abstract's 0.30 → 0.71 headline is the **onset** metric.

**Clean events** — of the 123, `clean_events.py` tags each as
*implausible-class* (a 16-class blocklist of things that cannot be in this
office — a surfboard is detector flicker), *reincarnation* (same camera+class,
`IoU >= 0.30` against an earlier expired event's last box — static-object
confidence flicker), or *clean*. Result: **63 clean / 39 reincarnation / 21
implausible**.

> **This classification is 100% automatic.** No human inspects a frame, and no
> reviewer decision is recorded anywhere in the archive. Any description of
> these as "manually verified" is unsupported by the code. The rules are
> reasonable and disclosed; the adjective is the problem. The blocklist is also
> workload-specific by construction and does not transfer unedited.

**IoU** is computed in pixel coordinates at the 640×480 source resolution, for
both tracking (`--iou-track`) and matching (`--iou-det`), both defaulting to
0.30.

## Things that will bite you

**`dets.jsonl` is not valid JSONL.** The app writes it to the same fd
GStreamer logs to, so lines like `Opening in BLOCKING MODE` are interleaved
(~14 of 2673 in a typical run). Every reader must skip lines not starting with
`{`. `_paths.iter_jsonl` does; use it.

**dets files may be gzipped.** A fresh run writes plain `dets.jsonl`; the
paper's runs were compressed (212 MB → 67 MB). Readers accept either form —
`_paths.dets_path` prefers the `.gz`.

**The drop ledger is in `stderr.log`, not `sched.csv`.** `sched.csv` logs
admit decisions only; it has no drop rows anywhere in the archive. Counting
drops from it yields zero. The ledger is the shutdown summary line, and it
closes exactly — verified on the live runs:
`13984 arrivals == 5864 fresh + 0 salvage + 8120 policy drops`.

**Log prefixes: `[sched]` and `[vista]`.** The archived data came from the old
binary, which named its thread `sparq-sched` and prefixed logs `[sched]`. The
shipped module names its thread `vista-sched` and prefixes `[vista]`. Every
parser here accepts **both**; narrowing one to a single prefix makes it return
nothing on half the corpus, silently. SPARQ was the project's former name — it
never became a code identifier, but it survives in archived thread names, run
directory names (`e6_*_sparq_*`), and comments.

**D_hard is not 150 ms.** 150 ms is `tau_max`, the staleness bound.
`D_hard = 8·(N/K)·s_hat` — load-derived, so ≈3.2 s at the operating point
where `s_hat` = 200.7 ms. Checking these runs against 150 ms invents thousands
of violations. See `service_gaps.py`.

**`--gap-every` is overloaded.** 44 = live-rate timing fidelity (every normal
run). 3 = the Static-Decimation baseline. 2 = the DEC-1/2 diagnostic.

**`e3_m_decimate` ≠ `e3_m_decimate3`.** The first is DEC-1/2 (997 ms mean age
— the *stalest* configuration measured). The second is Static-Decimation
(64 ms — a Table II row). One integer apart, ~930 ms apart, no error raised.

**Directory names are load-bearing.** They keep the original experiment names
and the archived JSONs key off them. Map names in `campaigns.yaml`; never
rename a directory.

**`run_meta.json`'s `git_sha` is unreliable.** The binary that produced these
runs exists in no commit. The `cmd` array in the same file *is* reliable — it
is the literal argv — and is what the provenance notes cite.

## `weightsweep/`

The System B weight-sweep study's own aggregator, copied from that study and
patched here for two archive realities: the drop-ledger regex now accepts
`[sched]` **and** `[vista]`, and the dets readers go through the shared
gz-aware reader. Its `SCHED_SUMMARY_RE` is the only evidence for the
"every drop counted" claim in that study — if it stops matching, the drop
columns go silently *empty* rather than wrong, which is worse.

```bash
python3 weightsweep/aggregate_runs.py --campaign /path/to/results/C2_matrix_office
```

That study's "System B" variant beats the paper's headline configuration. It
ships in full, deliberately.
