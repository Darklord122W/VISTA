# Output schemas

What a run writes, what each field means, and — for every file — the caveats
that will otherwise cost you a day. Everything here was checked against the
source *and* against the 168 runs of the paper's campaign (which this repository
does not ship), so the caveats are the ones real output actually exhibits, not
the ones the code implies.

A standard run directory:

```
<run>/
  metrics.csv       per-batch timing        (--metrics-csv)
  sched.csv         per-decision audit log  (--sched-csv)
  dets.jsonl(.gz)   per-frame detections    (the app's STDOUT)
  stderr.log        the drop ledger + banners (the app's STDERR)
  run_meta.json     provenance              (written by the harness, not the app)
```

Two of those come from shell redirection, not from a flag:

```bash
./app/vista_multicam ... --log json  > dets.jsonl  2> stderr.log
```

Contents: [`metrics.csv`](#metricscsv) · [`sched.csv`](#schedcsv) ·
[`dets.jsonl`](#detsjsonl--detsjsonlgz) · [`stderr.log`](#stderrlog) ·
[`run_meta.json`](#run_metajson) ·
[the E1 sweep layout](#the-exception-the-e1-capacity-sweep-layout) ·
[reading a run tree](#reading-a-run-tree)

---

## `metrics.csv`

One row per **batch**, written on the tracker's streaming thread. **18 columns
for 4 cameras**; the schema is `14 + N`. All 168 archived files carry the
identical header.

```
batch_idx,t_mono,n_in_batch,n_real,n_active,active_mask,timeout_us,mux_batch,
compute_ms,e2e_ms,total_dets,new_ids_cum,dets_cam0,dets_cam1,dets_cam2,dets_cam3,
drops_cum,arrivals_cum
```

| # | Column | Unit | Meaning |
|---|---|---|---|
| 1 | `batch_idx` | count | 0-based batch counter. |
| 2 | `t_mono` | s | Seconds since the collector started (monotonic). |
| 3 | `n_in_batch` | frames | `NvDsBatchMeta::num_frames_in_batch`. **This is the batch-atomicity check**: under `--sched` it must be `k`. |
| 4 | `n_real` | frames | Frames in this batch whose PTS matched a recorded source arrival. |
| 5 | `n_active` | count | **Always N.** This app has no camera skipping. |
| 6 | `active_mask` | string | **Always all-ones** (`1111`). Same reason. |
| 7 | `timeout_us` | µs | The run's *intended* push deadline. See the caveat below. |
| 8 | `mux_batch` | frames | **WRONG under `--sched`.** See below. |
| 9 | `compute_ms` | ms | Batcher src → tracker src, matched by batch PTS. `-1` if unmatched. |
| 10 | `e2e_ms` | ms | **The latency number.** Max over the batch's frames of (source arrival → tracker src). `-1` if no frame matched. |
| 11 | `total_dets` | count | Detections in this batch. |
| 12 | `new_ids_cum` | count | Cumulative distinct `(camera, track_id)` pairs — a tracking-stability proxy. A policy that fragments tracks grows this faster. |
| 13..12+N | `dets_cam<i>` | count | Per-camera detections in this batch. `0` both for "camera present, nothing detected" and "camera absent from this batch" — disambiguate with the frame list in `dets.jsonl`. |
| 13+N | `drops_cum` | count | **DEAD. Always 0.** See below. |
| 14+N | `arrivals_cum` | frames | Cumulative distinct frames seen at the source probes. |

### Caveat 1 — `mux_batch` is wrong under `--sched`

The collector is constructed with the **camera count** as its `mux_batch`
argument, not with the scheduler's `k`. So a `--sched-k 2` run records
`mux_batch = 4` while `n_in_batch` is correctly `2`. Verified in the archive:
every scheduler run has `mux_batch = 4, n_in_batch = 2`.

**Never read `mux_batch`.** Read `n_in_batch`, which is measured, or read the
command line from `run_meta.json`, which is authoritative.

### Caveat 2 — `drops_cum` is dead

It counts the new mux's `dropped` signal. On DS 7.1 that signal is **never
emitted** — sync-inputs discards do not fire it. Verified: **0 in every row of
all 168 archived `metrics.csv` files.**

The real loss accounting is elsewhere, and there are two different kinds:

- **Policy drops** (scheduler runs): `stderr.log`'s `[sched]`/`[vista]` line,
  or `Stats::policy_drops`. Exact.
- **Frames the pipeline never processed** (any run): `arrivals_cum` minus
  processed frames. The collector's closing line reports it directly.

### Caveat 3 — `e2e_ms` / `compute_ms` sentinels

Both are `-1` when the PTS lookup misses. **Filter `>= 0` in any parser** —
this is what the shipped analysis code does.

That said, the honest measurement: across **186,096 rows in all 168 archived
files there are zero negative values** in either column. The sentinel is a code
path that the archive never exercised (the PTS map holds 600 entries per
camera, and the deepest measured backlog is 51–55 frames). Filter anyway; your
run may not be ours.

### Caveat 4 — `e2e_ms` includes the stash wait, by design

The source probe records the **first** stamp per `(camera, PTS)` and ignores
later ones (`emplace`, insert-if-absent). Under `--sched` the probe fires twice
per frame — once on arrival, once when the scheduler re-pushes it — and the
first stamp is the true arrival. So `e2e_ms` counts the time the frame waited
in the stash, rather than hiding it. `arrivals_cum` counts the frame once, for
the same reason.

This only works because the metrics probes are attached **before** the
scheduler's. If you reorder that, you get flattering numbers.

### Caveat 5 — `timeout_us` records intent, not effect

It is the `--timeout-us` value. The `batched-push-timeout` property was
measured inert on DS 7.1 (the mux re-reads its INI at state change, after any
property). The column tells you what the run *asked for*; the INI tells you
what it *got*.

---

## `sched.csv`

One row per scheduler decision. Only written with `--sched-csv`.

**Two header variants exist in the archive** — 55 files with 12 columns and 52
with 11. The 12th column, `buf_pts`, was added later. Parsers must handle both;
key off the header, not the column count.

```
t,event,cam,slot,age_ms,fresh_score,imp_score,fair_score,value,released,in_flight[,buf_pts]
```

| Column | Unit | Meaning |
|---|---|---|
| `t` | s | Seconds since `attach()`. |
| `event` | enum | See below. |
| `cam` | index | Camera. |
| `slot` | `fresh`\|`held` | Which stash slot the frame came from. |
| `age_ms` | ms | Frame age at the decision, on the local arrival clock. |
| `fresh_score` | [0,1] | `max(0, 1 - age/tau_max)`. |
| `imp_score` | [0,1] | `min(I_c/I_max, 1)`. **Always `0.000` in `fresh` mode** — the mode zeroes it, not the weight. |
| `fair_score` | [0,1] | `min((now - t_served)/D_fair, 1)`. |
| `value` | — | `w_f*fresh + w_i*imp + w_r*fair`. Recomputable from the three scores: a cheap check that your weights are what you think. |
| `released` | frames | Frames in this release. Should equal `k`. |
| `in_flight` | frames | Frames released but not completed, after this release. |
| `buf_pts` | ns | Frame PTS — **the join key to `dets.jsonl`** (with `cam`). 12-column variant only. |

### It is admissions-only by default

`log_drops` defaults to `false` (paper-identical: drop rows put file I/O on the
arrival path of a timing-sensitive scheduler). The events actually present in
the archive, across all 107 `sched.csv` files:

| `event` | Present by default? |
|---|---|
| `admit` | yes |
| `admit-salvage` | yes (salvage runs) |
| `retain-held` | yes (salvage runs) |
| `displace`, `displace-held`, `evict-stale`, `evict-held`, `eos` | **only with `log_drops = true`** |

So **you cannot count drops from `sched.csv`.** Count them from `stderr.log`,
which is exact regardless.

---

## `dets.jsonl` / `dets.jsonl.gz`

One JSON object per **processed frame** (`--log json`). One record per admitted
frame — 2,372 records for a run that admitted 2,372 frames.

In this repository every `dets.jsonl` is **gzipped** to `dets.jsonl.gz`
(212 MB → 67 MB). **Any parser must handle both**: `gzip.open` when the path
ends in `.gz`, plain `open` otherwise.

```json
{"camera_id":0,"frame_num":0,"buf_pts":544356999,"t_emit":89646.4,
 "num_detections":1,
 "detections":[{"camera_id":0,"track_id":0,"class_name":"person",
                "confidence":0.9375,"x":0,"y":0.518186,
                "width":640,"height":399.924}]}
```

| Field | Unit | Meaning |
|---|---|---|
| `camera_id` | index | `source_id` — the camera's index in the YAML `cameras:` list. |
| `frame_num` | count | Per-camera frame index **as the batcher numbered it**. |
| `buf_pts` | ns | The frame's PTS. |
| `t_emit` | s | `CLOCK_MONOTONIC` seconds at which the record left the pipeline. |
| `num_detections` | count | |
| `detections[]` | | Per detection: `camera_id`, `track_id` (`-1` = not yet tracked), `class_name`, `confidence` in [0,1], and `x`,`y`,`width`,`height` in **pixels of the source resolution** (top-left origin). |

**`(camera_id, buf_pts)` is the frame identity, not `frame_num`.** With the PTS
fix on, `buf_pts` is the true capture stamp and is stable across runs on
identical input — so two policies' outputs can be compared frame for frame.
`frame_num` cannot do this: the batcher renumbers.

**`buf_pts` vs `t_emit`:** `buf_pts` says which instant the pixels show;
`t_emit` says when you found out. The gap between them is the thing the paper
is about.

### It is NOT valid JSONL

The file is the app's **stdout**, and DeepStream, TensorRT and the tracker also
print to stdout. Measured across all 168 archived files:

- **165 files contain exactly 14 non-JSON lines**; the 3 live-capture runs
  (`e4_live/*`) contain 10 (they lack the four `Opening in BLOCKING MODE`
  lines the replay decoder emits).
- Total lines per file range from 256 to 6,896 — so the contamination is
  0.2%–5% of lines, and **it is always at the head**, from startup.

Actual contaminating lines, verbatim:

```
Opening in BLOCKING MODE                (×4 — one per camera, replay only)
gstnvtracker: Loading low-level lib at /opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so
[NvMultiObjectTracker] Initialized
[NvMultiObjectTracker] De-initialized
Setting min object dimensions as 16x16 instead of 1x1 to support VIC compute mode.
INFO: [FullDims Engine Info]: layers num: 2
0   INPUT  kFLOAT input           3x640x640       min: 1x3x640x640     opt: 4x3x640x640     Max: 4x3x640x640
1   OUTPUT kFLOAT output          8400x6          min: 0               opt: 0               Max: 0
max_fps_dur <n> min_fps_dur <n>
(one blank line)
```

**Rule: skip every line that does not start with `{`.** A parser that does
`json.loads` on each line dies on line 1.

Two of those lines are useful rather than noise:

- The `INPUT`/`OUTPUT` pair **proves the engine's shape** — `8400x6` is the
  DeepStream-Yolo head, and `min: 1x3x640x640 … Max: 4x3x640x640` proves it is
  dynamic-batch. See [`02-models-and-engines.md`](02-models-and-engines.md).
- `max_fps_dur / min_fps_dur` **proves which mux INI loaded**, in nanoseconds:
  `2e+08 / 5e+08` = 200 ms / 500 ms = the scheduler INI's `overall-max-fps=5`,
  `overall-min-fps=2`. `8.33333e+06 / 3.3333e+07` = the baseline INI's 120/30.
  This is an independent check on host obligation 2 — and it passes on the
  archive: **all 105 `--sched` runs loaded the slow-anchor INI, all 60
  non-scheduler runs did not, zero mismatches.**

---

## `stderr.log`

**This is the drop ledger.** It is not a log file you can discard.

The two lines that matter, at the end of every run:

```
[sched] imp: 1186 releases (22.1/s), 2372 fresh + 0 salvage admitted, 3805 policy drops, s_hat 89.5 ms over 53.7 s.
[metrics] wrote 1186 batches to <path> (22.1 batches/s over 53.8s; 75 distinct tracks; sync off; processed 2372 of 6177 arrived frames; mux dropped-signal count 0).
```

Read together they close the ledger: `2372 + 3805 = 6177` — admissions plus
policy drops equals arrivals, exactly. That is the paper's exact-accounting
claim, per run, from the shipped artifact.

> **Do not read `s_hat` as the batch service time.** It is the EWMA's final
> state, and it can drift far above the truth. Measured across all 107 archived
> scheduler runs, comparing the reported `s_hat` against the same run's median
> `compute_ms`: **64 runs agree within ~30%** (as designed), **43 exceed it by
> more than 2×**, and the **e9 depth campaign is inflated 11–15×** (`s_hat`
> 1221 ms against a median `compute_ms` of 83 ms). For service time, use
> `compute_ms`. See [`06-tuning.md` §7](06-tuning.md#7-d_fair--d_hard-and-a-caveat-about-s_hat)
> for the behavioural consequence — `D_fair`/`D_hard` derive from this
> estimate.

> **Prefix: `[sched]` vs `[vista]`.** The archived runs were produced by the
> paper binary, which printed `[sched]`. The module in `vista/` prints
> `[vista]`. **Everything after the prefix is byte-identical.** Parsers must
> accept both:
>
> ```python
> re.search(r"\[(?:sched|vista)\] \w+: (\d+) releases \(([\d.]+)/s\), "
>           r"(\d+) fresh \+ (\d+) salvage admitted, (\d+) policy drops, "
>           r"s_hat ([\d.]+) ms", line)
> ```
>
> The shipped `analysis/weightsweep/aggregate_runs.py` recovers the ledger this way. See
> `NAMING.md`.

Also on stderr, and worth grepping for:

| Line | Meaning |
|---|---|
| `[main] USE_NEW_NVSTREAMMUX=yes` | The mux switch took. |
| `[main] <n> camera(s) [file] 640x480@30 (mjpeg); NEW nvstreammux sync-inputs=OFF …` | The full run banner: source, resolution, sync state, replay-skew parameters, pgie config, sched mode. This is the run's configuration, as the app understood it. |
| `[vista] mode=… k=… depth=… stash=… tau_max=…` | The scheduler's configuration. **No line, no scheduler.** |
| `[vista] WARNING: batch atomicity gate FAILED …` | **The run is invalid.** See [troubleshooting](../integration/05-troubleshooting.md). |
| `[vista] WATCHDOG: …` | Completions stopped. |
| `[pts-fix] cam <i>: jpegparse buffered more than 4 frames …` | Restored PTS may be off by one period. |
| `[main] ERROR from <element>: …` | Bus error; exit code 1. |

---

## `run_meta.json`

Written by the harness, not the app. One per run directory.

| Field | Meaning |
|---|---|
| `arm` | The policy arm's internal name (`fifo33`, `fresh-k2`, `imp-k2`, …). See the name registry — **these are the pre-revision names and the result directories keep them**. |
| `cmd` | **The full argv.** This is the authoritative record of what ran. When `run_meta` and any other field disagree, believe `cmd`. |
| `t_start`, `t_end`, `wall_s`, `duration_s` | Unix timestamps and durations. |
| `returncode` | Process exit code. |
| `gpu_clock_hz` | `612000000` — the locked clock. |
| `pgie`, `model_tag` | The detector config and its short tag (`n`/`s`/`m`/`l`/`x`). |
| `engine_sha256_16` | First 16 hex of the engine's SHA-256. Constant per `model_tag` across this archive; **not** reproducible on another machine — see [`02-models-and-engines.md`](02-models-and-engines.md). |
| `replay` | `{dir, skew_ms, rate, gap_every, ring}` — the injected timing. |
| `git_sha` | **Do not trust this field.** The tree was dirty when these runs were made; the recorded SHA does not identify the binary that produced them. The artifact's claim→evidence matrix documents this. Use `cmd` + `engine_sha256_16` for provenance instead. |

---

## The exception: the E1 capacity sweep layout

**The capacity-sweep campaigns (`e1_yolo11{n,s,m,l}/`) do not follow the layout
above**, and a glob of `*/*/metrics.csv` silently misses them. They come from the
deadline sweep harness (`harness/run_capacity.sh`) and are **flat**:

```
e1_yolo11m/
  run_meta.json              one for the whole sweep (different schema!)
  summary.csv                the derived per-deadline table
  push_5ms.csv               <- the per-batch metrics, same 18-column schema
  push_5ms_dets.jsonl.gz
  push_5ms_stderr.log
  push_10ms.csv  … push_66.7ms.csv
  mux_push_5000us.txt …      the generated INIs
  timeout_sweep.png, detection_perf.png
```

The `push_<N>ms.csv` files use **exactly the `metrics.csv` schema** above. The
sweep's `run_meta.json` is a *different* schema — it describes the sweep
(`ms: [5, 10, 20, 33.3, 66.7]`, `ref_ms`, `warmup`), not a single run.

`summary.csv` is derived, one row per deadline: `push_ms, mean_n_in_batch,
frac_full, batches_s, frames_s, e2e_mean_ms, e2e_p99_ms, compute_mean_ms,
frac_0..frac_4, arrivals, processed, coverage, dets_total, dets_per_frame,
dets_per_frame_cam0..3, distinct_tracks, match_frames, det_agree_pct,
det_absdiff_mean`. The `frac_<i>` columns are the batch-fill histogram — the
same information as `Stats::fill_hist`.

The generated INIs show how a real deadline is expressed on this mux:

```ini
overall-min-fps-n=1000000
overall-min-fps-d=33300        # = 33.3 ms, as a fraction
```

not via the `batched-push-timeout` property, which is inert.

---

## Reading a run tree

A parser that works on everything a campaign writes:

```python
import csv, glob, gzip, json, os, re

def open_maybe_gz(path):
    """dets.jsonl in this repo is gzipped; older trees have it plain."""
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") \
           else open(path, errors="replace")

def read_dets(path):
    with open_maybe_gz(path) as fh:
        for line in fh:
            if not line.startswith("{"):     # GStreamer/TensorRT stdout
                continue
            yield json.loads(line)

def read_metrics(path):
    with open(path) as fh:
        for row in csv.DictReader(fh):       # keys off the header: N varies
            yield row

def e2e_values(path):
    return [float(r["e2e_ms"]) for r in read_metrics(path)
            if float(r["e2e_ms"]) >= 0]      # -1 = unmatched sentinel

LEDGER = re.compile(
    r"\[(?:sched|vista)\] \w+: (\d+) releases \(([\d.]+)/s\), "
    r"(\d+) fresh \+ (\d+) salvage admitted, (\d+) policy drops, "
    r"s_hat ([\d.]+) ms")

def read_ledger(path):
    """Last match wins. Accepts both the archive's [sched] and vista's [vista]."""
    m = None
    for line in open(path, errors="replace"):
        m = LEDGER.search(line) or m
    return m and dict(releases=int(m[1]), releases_per_s=float(m[2]),
                      admitted_fresh=int(m[3]), admitted_salvage=int(m[4]),
                      policy_drops=int(m[5]), s_hat_ms=float(m[6]))
```

The five rules, condensed:

1. `dets.jsonl` may be `.gz`, and is **not** valid JSONL — skip non-`{` lines.
2. Filter `e2e_ms >= 0` and `compute_ms >= 0`.
3. Never read `mux_batch`; read `n_in_batch`. Never read `drops_cum`; it is 0.
4. Accept both `[sched]` and `[vista]`.
5. Key CSV columns off the header — the column count depends on `N`, and
   `sched.csv` has two variants.

And two rules about campaign trees specifically:

6. `e1_yolo11*/` is flat, not `<campaign>/<run>/`. Glob for both.
7. Result directory names are the **pre-revision** names and are load-bearing —
   `e3_m_decimate` (DEC-1/2, 997 ms) and `e3_m_decimate3` (Static-Decimation,
   64 ms) differ by one character and ~930 ms. Map names through the registry;
   never rename a directory.
