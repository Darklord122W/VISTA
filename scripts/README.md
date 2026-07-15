# scripts/ — getting from a bare Jetson to a runnable experiment

These scripts produce the things this repository deliberately does not ship:
the model weights and engines, the microbenchmark clips, and a correct camera
list. They are the first half of getting a measurement; `harness/` is the
second. This repository ships code, not runs — there is no archive to download
and nothing here fetches one.

## Order of operations

```sh
scripts/detect_cameras.sh --check --expect 4   # only if you have cameras
scripts/fetch_models.sh                        # weights -> ONNX + parser .so
scripts/build_engines.sh                       # ONNX -> TensorRT engines (~1 h)
scripts/make_skew_clips.py --variant brief     # the RQ3 microbenchmark clips
scripts/make_skew_clips.py --variant persistent
```

Then take measurements with `harness/run_campaign.sh` and score them with
`analysis/` (see `harness/README.md`).

## Paths and environment

Nothing here hardcodes an absolute path. Every script resolves relative to the
repository root and honours the same environment variables `analysis/_paths.py`
uses:

| variable | default | used by |
|---|---|---|
| `VISTA_MODELS_DIR` | `<repo>/models` | `fetch_models.sh`, `build_engines.sh` |
| `VISTA_CONFIG_DIR` | `<repo>/config` | `build_engines.sh` |
| `VISTA_CLIPS_DIR` | `<repo>/clips` | `make_skew_clips.py`, `record_replay_clips.py` |
| `VISTA_CAMERA_CONFIG` | `<repo>/config/camera_params.yaml` | `record_replay_clips.py` |
| `VISTA_DS_YOLO_DIR` | `<repo>/third_party/DeepStream-Yolo` | `fetch_models.sh` |

## The scripts

### `detect_cameras.sh` — find the real capture nodes

Run this first, and do not skip it because it looks trivial. **The obvious guess
(`/dev/video0..3` for four cameras) is wrong.** Every UVC camera creates two
nodes — a capture node and a metadata node — so four C920s occupy
`/dev/video0..7` and the cameras are the **even** ones: 0, 2, 4, 6.

It does not rely on that parity, which is an artifact of enumeration order and
breaks the moment you unplug something. It asks each node what it is via the
V4L2 `VIDIOC_QUERYCAP` ioctl and keeps `V4L2_CAP_VIDEO_CAPTURE`, rejecting
`V4L2_CAP_META_CAPTURE`. Cameras are ordered by USB **bus path**, not node
number, so `cam0..cam3` stay bound to the same physical camera across replugs —
which matters, because `camera_id` is baked into every detection record.

```sh
scripts/detect_cameras.sh                 # table of every node and its verdict
scripts/detect_cameras.sh --yaml          # paste-able camera_params.yaml block
scripts/detect_cameras.sh --check --expect 4
```

Listing a metadata node as a camera does not error. It yields a silent no-frame
stream.

### `fetch_models.sh` — weights → ONNX + the bbox parser

```sh
scripts/fetch_models.sh                   # all five: n s m l x
scripts/fetch_models.sh yolo11m yolo11x   # the useful minimum: primary + oracle
```

Downloads the YOLO11 checkpoints via Ultralytics into a throwaway CPU-only venv,
exports each to ONNX with the **vendored** DeepStream-Yolo exporter, and builds
`libnvdsinfer_custom_impl_Yolo.so` against the local DeepStream SDK. Publishes to
`models/`, where `config/pgie_yolo11*.txt` already points.

**Stock Ultralytics ONNX will not work.** `yolo export format=onnx` emits the raw
`(B, 84, 8400)` head. DeepStream-Yolo's exporter appends a module that produces
`(B, 8400, 6)` = `[x1,y1,x2,y2,score,label]`, which is exactly what
`NvDsInferParseYoloCuda` parses. Feed it anything else and you get **zero
detections, with no error**.

The export must also use torch's legacy ONNX exporter (`dynamo=False`); torch
≥2.9 defaults to the dynamo path, whose graph the parser cannot read — again
silently. The original of this script sed-patched a fresh `git clone` at run time
to inject that argument, guarded by a `grep`; that construction **fails open** (an
upstream reformat silently yields the broken build). We vendor DeepStream-Yolo at
a pin with the patch pre-applied, and this script now *verifies* the line is
present and refuses to run if it is not. See
`third_party/DeepStream-Yolo/PROVENANCE.md`.

### `build_engine.py` / `build_engines.sh` — ONNX → TensorRT

```sh
scripts/build_engines.sh                  # all five
scripts/build_engines.sh yolo11m yolo11x  # primary + oracle
scripts/build_engine.py --config config/pgie_yolo11m.txt
```

You cannot build these engines by hand: nvinfer must produce the engine it will
later load. So `build_engine.py` pushes a few `videotestsrc` frames through the
real nvinfer config, which makes nvinfer build and serialize the engine. No
cameras or clips involved.

**Budget an hour.** The yolo11x oracle engine alone took ~37 minutes on the
paper's rig. Do this before any timed run — otherwise the build lands *inside*
your first measurement. `build_engines.sh` skips models whose engine already
exists, reads the engine path back out of the pgie config rather than guessing
its name, and fails loudly if nvinfer exits 0 without producing the file.

Engines are specific to the GPU, TensorRT version and driver that built them.
That is why none ship. Do not copy them between machines.

### `make_skew_clips.py` — the RQ3 activity-skew clips

```sh
scripts/make_skew_clips.py --variant brief
scripts/make_skew_clips.py --variant persistent
scripts/make_skew_clips.py --variant brief --verify-only
```

Builds four 45 s, 640×480, 30 fps clips with lopsided **activity**: cam0 busy,
cam1/cam2 black, cam3 carrying one rare 9.6 s walk burst at t=18 s (the fairness
floor's test). `--variant brief` speeds cam0 up 6× so crossings last ~145 ms and
policy actually matters; `--variant persistent` is the control where events last
long enough that it does not.

The clips are not redistributed (DeepStream EULA, and `sample_walk.mov` depicts
people), so this script is the supported way to get them. It merges the two
original generators behind `--variant`.

**Read the reproducibility section in its docstring before trusting a rebuild.**
Measured on the paper's own rig:

* the **MP4 container hash never reproduces** — qtmux stamps wall-clock times
  into the header. The script therefore verifies the **H.264 elementary
  bitstream**, demuxed back out, not the `.mp4`;
* cam0, cam1 and cam2 reproduce **exactly** (cam0 verified against clips encoded
  six days earlier);
* **cam3 does not reproduce at all**, even run-to-run on one machine with
  identical input frames — three consecutive encodes gave three different
  bitstreams. x264enc defaults to `threads=0` and cam3 is the only clip built
  around hard scene changes, so threaded rate control resolves differently each
  time. `--deterministic` (threads=1) fixes that, but produces a *different*
  encode from the paper's, so it is off by default.

Consequence: a rebuild is equivalent in construction but not guaranteed identical
in detections. **Re-score your own YOLO11x oracle.** The paper's counts (368
brief / 257 persistent) describe the authors' clips, not yours, and this
repository ships neither those clips nor the runs scored from them.

### `record_replay_clips.py` — record live clips for reproducible replay

```sh
scripts/record_replay_clips.py --duration 45
```

Records each camera's raw capture to `<out-dir>/cam{i}.mp4` so experiments can
replay identical frames under every policy. Clips are raw — no overlays, no
inference baked in.

**This is a reimplementation, not a copy.** The research-tree original did
`import main as app; import pipeline_builder as pb` — it depended on the Python
prototype pipeline. This artifact ships the C++ `vista_multicam` instead, so a
verbatim copy would fail at import. The capture chain here was written to match
`pipeline_builder._build_v4l2_front`, including the decoder choice (C920 MJPEG is
YUV 4:2:2, so it is `jpegparse ! nvjpegdec`, **not** `nvv4l2decoder mjpeg=1`,
which is 4:2:0-only). The original's `--display` flag is not reimplemented — it
needed the prototype's pgie/tracker/tiler builders and recorded nothing extra.

The office clips the paper's RQ4 used are **not** shipped and cannot be
regenerated from this repository: they show an identifiable person.

### There is no `fetch_data.sh`

There was, and it only ever refused: no measurement archive is published, so it
had no URL, and inventing one would have turned an honest refusal into what
looks like a transient network error. It was removed rather than kept as a
permanent stub.

Nothing you need is downloadable from this project. Weights come from
Ultralytics via `fetch_models.sh`; engines you build with `build_engines.sh`;
the activity-skew clips you rebuild with `make_skew_clips.py`; the office
footage is not published at all (it shows an identifiable person), so RQ4 needs
your own cameras via `record_replay_clips.py`. Runs come from `harness/`.

## Verified on this hardware (2026-07-15)

Jetson AGX Orin 64 GB, DeepStream 7.1, GStreamer 1.20.3, CUDA 12.6, Python 3.10.

| check | result |
|---|---|
| `bash -n` on every `.sh`, `py_compile` on every `.py` | clean |
| `detect_cameras.sh` in all four modes | correct: 3 C920s attached → 3 capture + 3 metadata nodes, correctly classified; `--check --expect 4` fails as it should |
| `make_skew_clips.py --verify-only` against the authors' clips | 4/4 bitstreams match, both variants |
| `make_skew_clips.py --variant brief` full rebuild | cam0/cam1/cam2 bit-identical to the authors' encode; cam3 differs (documented, expected) |
| vendored `nvdsinfer_custom_impl_Yolo` build | clean; exports `NvDsInferParseYoloCuda` + `NvDsInferYoloCudaEngineGet` |

**Not verified:** `fetch_models.sh` and `build_engines.sh` were not executed
end-to-end (a full weights download plus a ~1 h engine build). Their argument
handling, preflight checks and the vendored-exporter tripwire were exercised;
the download and TensorRT paths were not. `record_replay_clips.py` was not run
against live cameras (only 3 of the paper's 4 are currently attached, and
recording would not have reproduced the paper's clips anyway).

The two clip checks above were run when the authors' clips were on this
machine; they are not in this repository, so you cannot repeat them here.
