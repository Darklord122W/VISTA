# Config reference — `camera_params.yaml`

Every key the reference app reads, with the **effective** default (the value
you get when the key is absent), taken from `load_config()` in the app's
`app_config.cpp`.

The YAML is shared history: it also contains sections that this C++ app
**ignores**. They are listed at the bottom so you do not tune something that is
not read.

Contents:

- [How paths resolve](#how-paths-resolve)
- [`capture`](#capture)
- [`cameras`](#cameras)
- [`source`](#source)
- [`streammux`](#streammux)
- [`pgie`](#pgie)
- [`tracker`](#tracker)
- [`output`](#output)
- [`display`](#display)
- [Keys this app ignores](#keys-this-app-ignores)
- [Error messages](#error-messages)

---

## How paths resolve

**Relative paths in the YAML resolve against the parent of the config file's
directory — not against your working directory, and not against the config
file's own directory.**

```cpp
const fs::path cfg_dir      = fs::absolute(path).parent_path();
const fs::path project_root = cfg_dir.parent_path();      // <-- the anchor
```

So with `--config <root>/config/camera_params.yaml`:

| YAML value | Resolves to |
|---|---|
| `config/mux_default.txt` | `<root>/config/mux_default.txt` |
| `experiments/clips` | `<root>/experiments/clips` |
| `/abs/path` | itself (absolute paths pass through, normalised) |

This is why every path in the shipped YAML starts with `config/` or
`experiments/` and why the app runs correctly from any working directory. It
also means that **moving the config file up or down one directory silently
re-anchors every relative path in it.** If you relocate it, use absolute paths
or move the whole tree.

(Note the different rule for the *nvinfer* config: nvinfer resolves
`onnx-file` / `model-engine-file` relative to **its own** file's directory,
which is why those read `../models/...`.)

---

## `capture`

Global capture defaults. Each camera entry may override all of these except
`pts_fix`.

| Key | Default | Unit | Meaning |
|---|---|---|---|
| `format` | `mjpeg` | `mjpeg`\|`raw` | `raw`/`yuyv`/`yuy2` are accepted spellings of the same thing. |
| `width` | **`1280`** | px | See the divergence note below. |
| `height` | **`720`** | px | |
| `fps` | `30` | fps | |
| `mjpeg_decoder` | `nvjpegdec` | `nvjpegdec`\|`jpegdec`\|`nvv4l2` | HW JPEG, SW JPEG, or nvv4l2decoder. |
| `pts_fix` | `true` | bool | Restore the true kernel capture PTS around `jpegparse`. CLI: `--no-pts-fix` / `--pts-fix`. **Capture-level only** — there is no per-camera override. |

> **The 640×480 vs 1280×720 divergence.** The `CameraCfg` struct in
> `app_config.hpp` declares `width = 640, height = 480`. Those values are
> **dead**: `load_config()` unconditionally assigns `cam.width = def_width`,
> where `def_width = get<int>(capture, "width", 1280)`. So if your YAML has no
> `capture:` section, you get **1280×720**, not the 640×480 the header
> suggests. The shipped `camera_params.yaml` sets 640×480 explicitly, which is
> the paper's rig configuration and what every archived run used. If you write
> a config from scratch, set them explicitly. Do not infer the default from the
> struct.

**Why the rig is 640×480 and not 720p.** All four C920s share one USB-2 bus.
`uvcvideo` reserves bandwidth from each camera's *peak* payload, not its true
compressed MJPEG rate, so the bus fills fast. Measured on this exact hardware:
4 × 640×480 MJPG@30 works; 2 × 1280×720 works; **4 × 1280×720 fails** — the
third camera errors with `Failed to allocate required memory` / `STREAMON
ENOSPC`. For 4 cameras above VGA, split them across separate USB host
controllers.

**Why MJPEG.** The C920 offers exactly two usable formats over USB-2: MJPG at
30 fps up to 1080p, and YUYV at 30 fps only up to 640×480 (720p caps at 10 fps,
1080p at 5). Use `mjpeg` for anything above VGA.

**Why `nvjpegdec` and not `nvv4l2decoder`.** C920 MJPEG is YUV 4:2:2.
`nvv4l2decoder mjpeg=1` decodes 4:2:0 only. The `mjpeg` path is
`jpegparse ! nvjpegdec` (or `jpegdec` for the software fallback).

## `cameras`

Required, non-empty sequence. **The list order defines `camera_id`**: entry `i`
becomes `source-bin-<i>`, links to the batcher's `sink_<i>`, becomes
`source_id = i` in the metadata, and `camera_id = i` in every output record and
in `Stats::per_cam_*`. It is the identity, everywhere.

Each entry is either a **scalar** (a device path):

```yaml
cameras:
  - /dev/video0
```

or a **mapping**:

| Key | Default | Meaning |
|---|---|---|
| `device` | — | `/dev/videoN`. **Required** for `v4l2` sources. |
| `file` | `<replay_dir>/cam<i>.mp4` | Clip path for `file` sources; resolved against the project root. |
| `format` | from `capture` | Per-camera override. |
| `width` / `height` / `fps` | from `capture` | Per-camera overrides. |
| `mjpeg_decoder` | from `capture` | Per-camera override. |

The shipped rig:

```yaml
cameras:
  - device: /dev/video0
  - device: /dev/video2
  - device: /dev/video4
  - device: /dev/video6
```

Odd-numbered nodes are UVC metadata, not video — hence the stride of 2. On this
board 4 × C920 enumerate as `/dev/video{0,2,4,6}`. Comment entries out to run
fewer cameras; the pipeline builds itself from the list length.

## `source`

| Key | Default | Meaning |
|---|---|---|
| `type` | `v4l2` | `v4l2` (live) or `file` (deterministic replay). CLI: `--source`. |
| `replay_dir` | `experiments/clips` | Where `cam<i>.mp4` live, for `file` sources. CLI: `--replay-dir`. |

## `streammux`

**New** nvstreammux only. It never scales or converts — frames are batched at
native resolution — so it has no `width`/`height`.

| Key | Default | Unit | Meaning |
|---|---|---|---|
| `batched_push_timeout_us` | `33333` | µs | The `batched-push-timeout` property. **Measured inert on DS 7.1** (see [`03-cli-reference.md`](03-cli-reference.md)); it survives into `metrics.csv`'s `timeout_us` column as the run's intended deadline. |
| `sync_inputs` | `false` | bool | `true` time-aligns across cameras and drops what cannot align. Incompatible with `--sched`. Accepts `0/1` and `true/false/on/off`. |
| `max_latency_ns` | `33333333` (33.3 ms) | ns | Sync-on **only**: extra wait for a late frame. |
| `config_file` |  `config/mux_default.txt` | path | The new-mux batching INI. `none` or empty runs on the mux's built-in defaults. |

> **`config_file` has an asymmetric failure mode.** If the key is **present**
> (as it is in the shipped YAML) and the file is missing, the app throws:
> `streammux config file not found: <path>`. If the key is **absent** and the
> default file is missing, the app **silently clears it and runs on the mux's
> built-in defaults** — which means `overall-min-fps = 5`, a 200 ms service
> cycle. That is a very large, very quiet latency change. Keep the key present.

The INI is where batching actually lives; the properties above mostly do not.
See [`docs/integration/03-pipeline-obligations.md`](../integration/03-pipeline-obligations.md).

## `pgie`

| Key | Default | Meaning |
|---|---|---|
| `config_file` |  `config/pgie_yolo11n.txt` | The nvinfer config. CLI: `--pgie-config`. **Always** throws if missing: `nvinfer config file not found: <path>`. |

## `tracker`

| Key | Default | Unit |
|---|---|---|
| `ll_lib_file` | `/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so` | path (not resolved — use absolute) |
| `ll_config_file` | `config/tracker_config.yml` | path (resolved against the project root) |
| `width` | `640` | px — tracker processing width, **must be a multiple of 32** |
| `height` | `384` | px — likewise |
| `gpu_id` | `0` | |

NvSORT: Kalman + association, persistent per-camera IDs, no re-ID. The app also
sets `display-tracking-id=TRUE`.

The tracker matters to VISTA for one reason only: in `imp`/`salvage` mode, the
activity signal is "a track ID I have not seen before on this camera". A
different tracker changes that signal. It is not otherwise in the loop.

## `output`

| Key | Default | Meaning |
|---|---|---|
| `only_nonempty` | `false` | `true` skips frames with zero detections. **Leave false for analysis**: a frame with no detections is data — it is how you know the frame was processed. |
| `pretty` | `false` | `true` = multi-line JSON. **Leave false**: `dets.jsonl` is line-delimited by convention, and pretty-printing breaks every parser that assumes it. |
| `log_interval_s` | `1.0` | s — `--log human` only: minimum seconds between lines per camera. |

## `display`

Only used with `--display` / `--debug` / `--record`.

| Key | Default | Meaning |
|---|---|---|
| `width` | `1280` | Tiled composite width (all cameras together). |
| `height` | `720` | Tiled composite height. |
| `window_width` | `0` | On-screen window width; `0` = composite size. |
| `window_height` | `0` | Likewise. |

The tiler grid is computed from the camera count:
`cols = ceil(sqrt(N))`, `rows = ceil(N/cols)`.

---

## Keys this app ignores

Present in the shipped YAML, read by **nothing** in the C++ app. They are
legacy-mux (Python app) keys or sections from an earlier experiment harness.
Changing them has no effect here.

| Section / key | Why ignored |
|---|---|
| `streammux.width`, `streammux.height` | The new mux does not scale. |
| `streammux.live_source` | Legacy-mux only. |
| `streammux.nvbuf_memory_type` | Legacy-mux only. |
| `timeout:` (`policy`, `base_us`, `min_us`, `max_us`) | Adaptive-timeout experiment; not implemented in this app. Use `--timeout-us` (and read the note about it being inert). |
| `batch:` (`policy`) | Adaptive-batch-size experiment. Measured not to help on the legacy mux; not implemented here. |
| `context:` (`type`, `idle_secs`, `reprobe_secs`) | Camera skipping / valves. **This app has no camera skipping** — which is why `metrics.csv`'s `n_active` is always N and `active_mask` is always all-ones. |
| `control.tick_ms` | Controller tick for the above. |

If you are wondering why `active_mask` never changes: that is why.

---

## Error messages

All are `std::runtime_error`, caught in `main()` and printed as
`[main] ERROR: <msg>` with exit code 2 — before GStreamer is touched.

**Loading**

```
Config file not found: <path>
Failed to parse <path>: <yaml-cpp error>
Config file <path> is not a YAML mapping.
Config file <path> has no 'cameras' configured.
Config key '<key>' has an invalid value: <dump>
```

The last one is deliberate: a key that is **present but unconvertible**
(`fps: thirty`) throws rather than silently falling back to the default. A key
that is absent or null takes the default. Typos in *values* are caught; typos
in *key names* are not — an unknown key is simply never read.

**Cameras**

```
cameras[<i>] must be a string or a mapping.
cameras[<i>] needs a 'device' path for the v4l2 source.
source.type must be 'v4l2' or 'file'; got '<x>'.
camera <i>: unknown mjpeg_decoder '<x>' (use 'nvjpegdec', 'jpegdec', or 'nvv4l2').
camera <i>: unknown capture format '<x>' (use 'mjpeg'/'raw').
```

**Validation** (fails fast, and tells you what *is* there)

```
Replay clip(s) not found: <list>.
Record them first: python3 scripts/record_replay_clips.py
```

> **That second line names a script this repository does not ship.** The
> message is quoted here verbatim because it is what the binary prints. The
> paper's clips were recorded from the live rig and are an office scene
> containing an identifiable person, so **neither the clips nor the recorder
> ship** — see the artifact's data README. Supply your own
> `<replay_dir>/cam<i>.mp4` clips, or run `--source v4l2` with your own
> cameras.

```
Configured camera device(s) not found: <list>.
Devices present: <list>
Check the cameras are plugged in and the 'cameras' list in the config.
```

**Files**

```
streammux config file not found: <path>
nvinfer config file not found: <path>
```
