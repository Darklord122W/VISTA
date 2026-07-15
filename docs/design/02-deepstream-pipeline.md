# The two pipelines, element by element

*Source: the authors' `PIPELINE_STRUCTURE.md`, verified line-by-line against
`multicam_perception_rt/cpp/src/` on 2026-07-11 and re-checked here against the
vendored `vista/` module and the archived `run_meta.json` command lines.*

One binary builds one of two per-camera **front-ends** (live capture or
deterministic replay) feeding an identical shared **trunk** (mux → detector →
tracker → metrics).

> **VISTA is not a pipeline.** It is three pad probes and one thread bolted onto
> that same graph. With `--sched off` the probes are never attached and the
> binary is bit-identical to the stock build.

```
per-camera front-end (x4)                     shared trunk
+---------------------------+
| LIVE:                     |
|   v4l2src -> jpegparse     |
|   -> nvjpegdec             |\
+---------------------------+ \
                               >-- nvvideoconvert --> (( source-bin-N ))
+---------------------------+ /                        ghost src pad
| REPLAY:                   |/                              |
|   filesrc -> qtdemux       |                              | <-- VISTA arrival
|   -> h264parse             |                              |     probe attaches
|   -> nvv4l2decoder         |                              v
|   -> skew probe -> pacer   |                        nvstreammux
|   -> ring stand-in         |                              |
+---------------------------+                              v
                                                        nvinfer
                                                     (YOLO11 FP16,
                                                      dyn. batch 1-4)
                                                            |
                                                            v
                                                    nvtracker (NvSORT)
                                                            |  <-- VISTA completion
                                                            v      probe attaches
                                              metrics.csv / dets.jsonl / sched.csv
```

| | source | decoder | evaluation role |
|---|---|---|---|
| **live** | `v4l2src` (C920, MJPG 640x480@30) | `jpegparse` → `nvjpegdec` (HW) | motivation + live validation (Table V) |
| **replay** | `filesrc` → `qtdemux` → `h264parse` | `nvv4l2decoder`, `num-extra-surfaces` = `--replay-surfaces` | **every policy-comparison number** (Tables II–IV) |

`nvv4l2decoder` is explicitly rejected for the live path: it cannot decode the
C920's 4:2:2 MJPEG. That is why the two front-ends use different decoders, and
it is the root of the timestamp problem in §2.

---

## 1. The shared trunk

| element (name) | role |
|---|---|
| `nvstreammux` (`stream-muxer`) | batches N cameras' frames into one buffer. NEW mux enforced (`USE_NEW_NVSTREAMMUX=yes`; legacy ⇒ hard error). `batch-size` = 4 (stock) or K (scheduled) |
| `nvinfer` (`primary-inference`) | runs the YOLO11 TensorRT engine once per **batch**. FP16, **dynamic batch 1–4** — so a K=2 batch genuinely costs S(2) < S(4); there is no padding |
| `nvtracker` (`tracker`) | NvSORT, per-camera persistent track IDs |
| metrics probes | `metrics.csv` (per batch), `dets.jsonl` (per frame, keyed `(camera, buf_pts)`), `sched.csv` (per scheduler decision) |

Two structural facts about the mux that the whole design turns on:

- **Its per-source queues are unbounded in count, but every queued frame pins
  one buffer-pool slot.** Pool depth is therefore the real queue bound.
  Measured backlog: **51–55 frames** at deep pools, **~24** at
  `--replay-surfaces 2`, **~4/camera** in the live kernel ring. Divide by the
  ~64 f/s YOLO11m capacity and you get the 855 / 410 / 276 ms staleness
  plateau. The staleness is the pool, arithmetically.
- **In a scheduler run the mux batch is completed by the scheduler pushing its
  K-th buffer**, not by the mux timer. The scheduler INI's rate anchors are set
  slower than any service period so the deadline can never preempt a forming
  K-burst. This is gate G1
  (`docs/reproduction/01-run-the-experiments.md`), and it is checked
  at runtime, not assumed — see `vista/src/vista_scheduler.cpp:384-401`.

## 2. The live front-end — and where the truth is destroyed

```
Logitech C920 (MJPG ~30 fps over USB)
      |
      v
kernel v4l2 CAPTURE RING (uvcvideo, ~4 fixed buffers)
      |                          ^
      |                          +-- ring full + new frame => NEWEST frame
      |                              overwritten. No signal, no counter.
      |                              THE SILENT DROP POINT.
      v
v4l2src (cam-src-N) -- stamps the KERNEL CAPTURE TIME (the only true timestamp)
      |
      v
jpegparse (cam-jparse-N) -- DESTROYS the true stamp; re-stamps onto an ideal
      |                     33.33 ms grid anchored at THIS camera's first frame
      v
nvjpegdec (cam-jpegdec-N) -> NVMM GPU buffers
```

Two facts to carry forward:

- **The silent drop point is upstream of everything.** No element, probe, or
  metric ever sees a frame the kernel ring overwrote. This is why a stock
  pipeline's coverage statistic reads 100% while half the frames die.
- **Per-camera fake clocks.** Each camera's 33.33 ms grid anchors at its *own*
  first frame, and USB cameras enumerate one after another, so the four grids
  disagree by **1.05–1.70 s** per launch. Anything comparing PTS across cameras
  compares fictions. This is the entire subject of `06-local-clocks.md`.

A `pts_fix_{sink,src}_probe` pair straddles `jpegparse` and restores the true
kernel stamps (verified live 2026-07-07: pre-mux PTS == kernel capture PTS on
13,940/13,940 frames). **VISTA does not need it.** The scheduler never reads
PTS. The fix exists so that *measurement* can join a detection back to its
capture instant; it is an instrumentation dependency, not a scheduling one.

## 3. The replay front-end

Every evaluation number in Tables II–IV comes from this path. Three injection
stages make a recorded clip arrive like the live rig (order verified in
`build_file_front()`):

```
filesrc -> qtdemux -> h264parse
      |
      v
nvv4l2decoder (cam-dec-N), num-extra-surfaces = --replay-surfaces (20 / 2)
      |
      v
replay_skew_probe (decoder src pad):  PTS' = PTS*rate + skew;  drop gap frames
      |
      v
identity sync=true (cam-pace-N): releases each buffer at running-time PTS'
      |                          => wall-clock arrival, exactly like a camera
      v
queue leaky=upstream, 4 deep (cam-ring-N): the v4l2 ring STAND-IN — fills when
      |                                     the mux side backs up, then DROPS
      |                                     THE NEWEST; the pacer never blocks
      v
(optional) replay_restamp_probe -- UNFIXED-jpegparse emulation, E6 sync arms only
```

## 4. The injected imperfections

| injected | value (`run_eval.py` defaults) | reproduces |
|---|---|---|
| per-camera start skew | `--skew-ms 0,1134.8,1702.1,567.2` | measured USB enumeration stagger |
| clock-rate factor | `--rate ≈0.961` per camera | the cameras' TRUE ~32.0 ms frame period (not the nominal 33.3) |
| capture gaps | `--gap-every 44` (phase-staggered) | the measured ~29.8 fps delivered rate |
| capture ring | `--ring 4`, drop-newest | kernel capture-ring loss behaviour |

**`--gap-every N` drops two consecutive frames out of every N**, keeping
`(N-2)/N`. The implementation is one line
(`pipeline_builder.cpp:203-204`: `(idx + gap_phase) % gap_every < 2`), and the
`< 2` is the whole story. This matters twice:

- **Fidelity.** `--gap-every 44` keeps 42/44 = 95.45%, so arrival is
  `4 x (30/0.961) x 0.9545 = 119.2 f/s` — reproducing Table I's "≈119 f/s"
  exactly, and with it `rho = 119.2/64 = 1.86`.
- **The decimation baseline.** The same flag, set low, *is* Static-Decimation.
  `--gap-every 3` keeps `(3-2)/3 = 1/3` of frames — literally one in three,
  `rho = 1.86/3 = 0.62`. `--gap-every 4` keeps `1/2`, `rho = 1.86/2 = 0.93`.

> **Trap.** `--gap-every` is overloaded, and the two roles differ by a factor of
> ten in the flag but by a factor of two in the outcome:
> **44 = live-rate fidelity** (every normal run), **4 = DEC-1/2** (the stalest
> configuration measured, 997 ms — a diagnostic), **3 = DEC-1/3 = the paper's
> Static-Decimation row** (64 ms). See
> `docs/reproduction/01-run-the-experiments.md`.

The `1/2` and `1/3` labels are **literal**, not "by measured effect": they fall
straight out of `(N-2)/N`. This artifact re-derived them and cross-checked
against the data — DEC-1/3's predicted arrival of 41.6 f/s versus its measured
served rate of 42.1 f/s (it runs under capacity, so served == arrived). Some
working notes in the authors' tree state the labels are approximations and give
`--gap-every 2` for DEC-1/2; both are mistaken. The archived
`run_meta.json` `cmd` arrays record `--gap-every 4` for all three
`e3_m_decimate_r*` runs and `--gap-every 3` for all three `e3_m_decimate3_r*`.

### Why `--replay-surfaces` exists

The decoder's default ~5-surface pool is smaller than a congested mux's
backlog. Without headroom the *pool*, not the ring, becomes the throttle: the
pacer starves and a constant ~938 ms pacing error freezes in. Extra surfaces
keep the pacer on time and move the drop decision to the ring, where the live
rig puts it.

`--replay-surfaces` is therefore also the knob that sets the baseline's standing
queue, which makes it a **policy** knob in disguise:

| `--replay-surfaces` | backlog | mean age | paper row |
|---|---:|---:|---|
| 20 (replay default) | 51–55 frames | 857 ms | **Stock-Default** |
| 2 (live depth) | ~24 frames | 410 ms | **Stock-LiveDepth** |
| — (live rig, kernel ring) | ~4/camera | 276 ms | Table V stock row |

Stock-LiveDepth is not a different algorithm. It is Stock-Default with the pool
calibrated to what the physical rig actually uses — which is why the paper
headlines against it rather than against the 857 ms deep-pool number.

## 5. VISTA on this graph

`Scheduler::attach()` (`vista/src/vista_scheduler.cpp:194-255`) adds three
probes and one thread. Nothing is removed:

| probe | pad | what it does |
|---|---|---|
| `arrival_probe` (BUFFER) | each `source-bin-N` ghost src | ref the buffer, stash it, return `GST_PAD_PROBE_DROP` |
| `event_probe` (EVENT_DOWNSTREAM) | same pad | let EOS pass through untouched; stop scheduling that camera |
| `completion_probe` (BUFFER) | `tracker` src | the completion clock: importance update, `s_hat` update, `in_flight -= frames` |

**`PROBE_DROP` + ref is the whole trick upstream.** GStreamer believes delivery
succeeded, so no backpressure ever forms, so the ring stays drained and the
silent drop point never activates. The stash stores **pointers, not pixels** —
the frames stay in the converter's pool, pinned by the ref. That is why the app
raises `output-buffers = 12` when the scheduler is on, and why the stash is
bounded (`03-backpressure.md` §6).

The release thread pushes its chosen frames back through the *same* pads with a
`thread_local` guard (`t_vista_pushing`, `vista_scheduler.cpp:262`) so its own
traffic passes the arrival probe instead of being re-stashed forever.

## 6. Element ↔ role map

| general term (`01-overview.md`) | explicit component |
|---|---|
| camera | Logitech C920 (MJPG 640x480@30) |
| driver capture ring | kernel v4l2 ring, `uvcvideo` (~4 buffers); replay stand-in: `queue leaky=upstream`, 4 deep |
| capture source | `v4l2src` (live) / `filesrc → qtdemux → h264parse` + pacing (replay) |
| decode-stage parser | `jpegparse` (live only — and the timestamp villain) |
| hardware decoder | `nvjpegdec` (live) / `nvv4l2decoder` (replay) |
| format converter | `nvvideoconvert` → NVMM NV12 |
| interception callback | `arrival_probe` on the source-bin ghost pad |
| per-camera stash | `CamState::fresh` deque of `GstBuffer*` refs |
| release step | `release_once()` on the `vista-sched` thread |
| batcher | `nvstreammux` (NEW mux; batch-size 4 stock / K scheduled) |
| detector | `nvinfer`, YOLO11 TensorRT FP16, dynamic batch 1–4 |
| tracker | `nvtracker` (NvSORT) |
| completion signal | `completion_probe` on the tracker src pad |
| in-flight credit | `in_flight_` + the `(depth-1)*K` gate |
| staleness bound | `tau_max_ms = 150` |
| decision log | `sched.csv` |

> **Naming note.** Archived logs and configs use the project's older internal
> name (`[sched]` prefix, thread `sparq-sched`, `mux_sched.txt`). The shipped
> module emits `[vista]` and names its thread `vista-sched`. Parsers in
> `analysis/` accept **both**; the archived data has the old ones. See
> `NAMING.md`.
