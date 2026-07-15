# `vista_multicam` — the paper's reference application

A multi-camera DeepStream detection + tracking pipeline for Jetson: N cameras →
`nvstreammux` → YOLO11 (`nvinfer`) → NvSORT (`nvtracker`) → JSON/CSV output, with
an optional display/record tail.

This binary is two things at once.

**1. It is the paper's reference app.** Every run behind the paper's numbers was
produced by this program under some combination of the flags below. The arms in
the paper are flag settings, not different binaries: `--sched off` is
Stock-Default, `--sched fresh` is VISTA-Fresh, `--sched imp` is VISTA-Activity,
`--dropold` and `--gap-every 3` are baselines. See `docs/reproduction/` for the
name mapping and the exact invocations.

**2. It is the second worked integration example for `vista/`.** It consumes the
scheduler through the identical public API that
`vista/examples/minimal_pipeline/` uses, and through no other channel:

```cpp
#include "vista/vista_scheduler.hpp"

vista::SchedCfg cfg;            // filled from --sched* flags
cfg.mode = "fresh"; cfg.k = 2;

auto sched = std::make_unique<vista::Scheduler>(cfg, num_cams);
sched->attach(pipeline);        // BEFORE the pipeline goes to PLAYING

/* ... run ... */

sched->request_stop();                              // 1. thread may be in a push
gst_element_set_state(pipeline, GST_STATE_NULL);    // 2. flushes; unblocks it
sched->join_and_cleanup();                          // 3. joins; unrefs stashes
gst_object_unref(pipeline);                         // 4. LAST
```

Nothing in `vista/` knows this app exists: no `vista` header includes an app
header, and the module's only pipeline knowledge is the element *names* it probes
(`SchedCfg::source_bin_prefix`, `tracker_name`, `mux_name`, `pgie_name`), all
overridable. The example and this app together are the evidence that the API is
genuinely reusable rather than shaped around one caller. The teardown order above
is load-bearing — `vista/include/vista/vista_scheduler.hpp` explains why.

`app/src/` holds no scheduler source. `scheduler.{cpp,hpp}` from the paper's
working tree became `vista/src/vista_scheduler.cpp` and
`vista/include/vista/vista_scheduler.hpp`; the app compiles it from `../vista/`.

## Build

```sh
cd app && make -j4          # -> ./vista_multicam
```

Needs a Jetson with DeepStream 7.1 (`gstreamer-1.0` dev, `yaml-cpp` dev, the
DeepStream SDK headers/libs), `g++` with C++17. Override the SDK prefix with
`make DS_ROOT=/opt/nvidia/deepstream/deepstream-7.1`.

Verified on the paper's hardware (Jetson AGX Orin 64 GB, DeepStream 7.1, g++
11.4, aarch64): builds clean from scratch with zero warnings under `-Wall
-Wextra`, and links `../vista/src/vista_scheduler.cpp` directly — there is no
installed library.

## Run

```sh
# stock pipeline (no scheduler) against live cameras
./vista_multicam --config ../config/camera_params.yaml

# VISTA-Fresh at the paper's operating point, deterministic file replay
./vista_multicam --config ../config/camera_params.yaml \
    --source file --replay-dir /path/to/clips \
    --sched fresh --sched-k 2 \
    --metrics-csv out/metrics.csv --sched-csv out/sched.csv \
    --duration 42 --log none
```

Neither the models nor the paper's camera footage ship with this repository; see
`config/README.md` and `docs/reproduction/`. The first run with a new model
builds a TensorRT engine and takes several minutes.

`--sched` needs `config/mux_sched.txt` next to the active mux INI. The app
selects it automatically (unless you pass `--mux-config`) and fails loudly if it
is missing.

## Flags

Every flag name is byte-identical to the archived binary's: `run_meta.json`'s
`cmd` arrays record the exact argv of each published run, and the harness
replays them. All 26 distinct flags used across the 165 archived `run_meta.json`
files still parse. Four flags are **added** (marked *new*); none renamed, none
removed. `--help` is authoritative.

### General

| flag | meaning |
|---|---|
| `--config PATH` | YAML config (default `config/camera_params.yaml`) |
| `--sync` / `--no-sync` | `nvstreammux sync-inputs=1` / force 0, overriding the YAML |
| `--max-latency-ms N` | sync-on only: extra wait for late frames (default 33) |
| `--timeout-us N` | `batched-push-timeout` (default 33333). Inert on DS 7.1 — the mux INI's `overall-min-fps` is the deadline that works |
| `--mux-config PATH` | new-mux INI; `none` runs on the mux's built-in defaults |
| `--pgie-config PATH` | nvinfer config, e.g. `config/pgie_yolo11m.txt` |
| `--no-pts-fix` | disable the jpegparse PTS-restore fix (default on) |
| `--source v4l2\|file` | live cameras (default) or deterministic file replay |
| `--replay-dir DIR` | per-camera clips `cam0.mp4`.. for `--source file` |
| `--display`, `--record PATH`, `--log MODE`, `--debug` | tiled window; H.264 MP4; `json\|human\|none`; `--display --log human` |
| `--metrics-csv PATH` | per-batch latency/throughput CSV |
| `--duration SECS` | stop cleanly after N seconds |

### Replay-skew injection (`--source file`)

Reproduces the live rig's timing imperfections on recorded clips.

| flag | meaning |
|---|---|
| `--skew-ms a,b,..` | per-camera start delay (startup stagger) |
| `--rate r0,r1,..` | per-camera PTS rate factor; `0.9608` turns a 30 fps clip into the C920's true 32.026 ms cadence |
| `--gap-every N` | drop 2 consecutive frames every N frames per camera |
| `--ring N` | bounded drop-newest queue after the pacer (live: 4; 0 = off) |
| `--replay-surfaces N` | `nvv4l2decoder num-extra-surfaces` (default 20; lower to 2-4 to emulate live queue depth) |
| `--restamp` | emulate the *unfixed* jpegparse synthetic-grid PTS |

> `--gap-every` is **overloaded** across the paper's campaigns: `44` is live-rate
> timing fidelity, `3` is the Static-Decimation *baseline*. `--replay-surfaces 2`
> is the Stock-LiveDepth arm. Read `docs/reproduction/` before reusing them.

### VISTA scheduler

| flag | meaning |
|---|---|
| `--sched MODE` | `off` (default) \| `fresh` (VISTA-Fresh) \| `imp` (VISTA-Activity) \| `salvage` (**not evaluated in the paper**) |
| `--sched-k N` | frames per release = mux batch-size (default 2) |
| `--sched-depth N` | release gate: in-flight ≤ (N−1)·k frames (default 2) |
| `--sched-stash N` | fresh frames stashed per camera (default 1); set ≥ depth for importance concentration |
| `--sched-tau-max MS` | hard staleness bound for fresh frames (default 150) |
| `--sched-tau-salvage MS` | staleness bound for held frames (default 250) |
| `--sched-w F,I,R` | value weights fresh,importance,fairness (default `0.40,0.35,0.25`) |
| `--sched-imp-halflife S` | *new* — importance EWMA half-life, seconds (default 2.0) |
| `--sched-imp-max F` | *new* — importance clip (default 2.0) |
| `--sched-retention F` | *new* — `imp_score` at displacement ≥ this → held slot (default 0.30; salvage only) |
| `--sched-csv PATH` | per-decision log (the audit trail) |
| `--sched-csv-drops` | *new* — also log policy DROP rows to `--sched-csv` |
| `--dropold` | keep-newest config baseline: per-camera 1-deep `leaky=downstream` queue, no scheduler |

The four new flags expose `SchedCfg` fields that the archived binary hardcoded.
Their defaults reproduce the archived behaviour exactly, so adding them changes
no published result:

- `--sched-csv-drops` defaults **off**, matching the paper: an archived
  `sched.csv` contains `admit` rows only, and so does this app's default output.
  Drop rows are opt-in because they add I/O on the arrival path of a
  timing-sensitive scheduler. The summary counters count every drop either way.
- `--sched-imp-max` is worth understanding before changing: importance is both
  clipped at and normalised by it, and the v1 default (10, with a "+detections"
  increment) saturated on any scene holding standing objects — median score 1.000
  on every camera — silently turning the importance term into a constant.
  Importance must measure *change*, not how much is in frame.

## Reading the output

`--sched` prints a one-line summary to stderr whose format is parsed by
`analysis/weightsweep/aggregate_runs.py`:

```
[vista] fresh: 547 releases (39.5/s), 1094 fresh + 0 salvage admitted,
        426 policy drops, s_hat 48.8 ms over 13.8 s.
```

The ledger closes exactly: `arrivals == admitted_fresh + admitted_salvage +
policy_drops`. It is worth checking against `metrics.csv`'s independently
counted `arrivals_cum` — on the verification run above both said 1520.

> **Log-prefix compatibility.** This module emits `[vista]` and names its thread
> `vista-sched`. The archived data was produced by the predecessor, which emitted
> `[sched]` and named its thread `sparq-sched`. Parsers that read archived stderr
> must accept **both**. See `NAMING.md`.

`metrics.csv`'s `mux_batch` column was wrong under `--sched` in the archived
binary (it recorded the camera count, not K). It is fixed here; archived files
are affected. No analysis reads it — `n_in_batch` is the real batch. Details are
in `KNOWN-ISSUES.md`.

## What was changed from the paper's source

The app is a verbatim copy of the paper's working tree
(`multicam_perception_rt/cpp/src/`) minus `scheduler.{cpp,hpp}`, with:

- `namespace mcrt` → `namespace vista`; `#include "scheduler.hpp"` →
  `#include "vista/vista_scheduler.hpp"`.
- GStreamer pipeline name `multicam-perception-rt` → `vista-multicam`; binary
  `multicam_rt` → `vista_multicam`.
- SPARQ → VISTA in comments and help text. (SPARQ was never a code identifier:
  the namespace was `mcrt`, the class `Scheduler`, the flags `--sched*`.)
- The four new `--sched*` flags above.
- The `mux_batch` fix, and the two renamed config paths in `app_config.cpp`'s
  hardcoded fallbacks (`mux_config.txt` → `mux_default.txt`, `pgie_config.txt` →
  `pgie_yolo11n.txt`) — see `config/README.md`.

No behavioural change to the pipeline itself: batching, probes, PTS handling and
teardown order are untouched.
