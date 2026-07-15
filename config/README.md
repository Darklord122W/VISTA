# config/ — runtime configuration for `vista_multicam`

These are the configuration files the paper's runs used, copied from the working
tree with two renames (below). They are consumed by `app/` (see `app/README.md`);
`vista/` itself reads none of them — the scheduler is configured entirely through
`SchedCfg`, which the app fills from `--sched*` flags.

| file | consumed by | what it is |
|---|---|---|
| `camera_params.yaml` | the app (`--config`) | the single entry point: cameras, capture format, and the paths to everything else here |
| `mux_default.txt` | `nvstreammux` | NEW-mux batching INI for non-scheduler runs |
| `mux_sched.txt` | `nvstreammux` | NEW-mux batching INI for `--sched` runs (auto-selected) |
| `pgie_yolo11{n,s,m,l,x}.txt` | `nvinfer` | one per detector size; `--pgie-config` picks one |
| `tracker_config.yml` | `nvtracker` | NvSORT settings |

Paths inside each file are relative to **that file's own directory**, so the set
moves as a unit. `camera_params.yaml`'s paths resolve against the *repository
root* (`app_config.cpp` uses the config file's parent's parent); the nvinfer
configs' `../models/...` paths resolve against `config/`, i.e. to `models/` at
the repo root.

## Renames from the paper's working tree

| here | paper working tree | why |
|---|---|---|
| `mux_default.txt` | `mux_config.txt` | "default" vs. the `mux_sched.txt` variant |
| `pgie_yolo11n.txt` | `pgie_config.txt` | consistency with the other four model configs |

`camera_params.yaml` and the hardcoded fallbacks in `app/src/app_config.cpp` were
updated to match. **`mux_sched.txt` deliberately keeps its name** — `main.cpp`
looks it up by hardcoded basename and every `--sched` run fails without it. See
`KNOWN-ISSUES.md` for the fail-open path behind the mux INI: when the default
INI is absent, `app_config.cpp` runs on the mux's built-in defaults with a
warning rather than throwing.

## What you must supply

**Models do not ship** (6.4 GB; TensorRT engines are hardware- and
version-specific and would be wrong on your machine anyway). Each
`pgie_yolo11*.txt` expects, relative to this directory:

```
../models/yolo11n.onnx                       # and s/m/l/x as needed
../models/labels.txt
../models/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so
```

`model-engine-file` names the engine nvinfer *auto-generates* from the ONNX
(`yolo11n.onnx_b4_gpu0_fp16.engine`). If that file is absent, nvinfer builds it
from `onnx-file` on first run — several minutes, once — and writes it under that
name. See `docs/reproduction/` for the download and build recipes.

**Camera footage does not ship** (it shows an identifiable person). The replay
clips the paper used are not redistributable; supply your own `cam0.mp4`..
`cam3.mp4` for `--source file`, or run `--source v4l2` against real cameras.

## The two mux INIs are not interchangeable

The app picks the INI for you: with `--sched`, `main.cpp` replaces the configured
mux INI with `mux_sched.txt` **from the same directory** unless you passed
`--mux-config` explicitly. The difference is load-bearing:

- `mux_default.txt` — `max-same-source-frames=1`, `overall-min-fps=30`. The
  min-fps floor is a *push deadline*, and on this DS 7.1 build it — not the
  `batched-push-timeout` property — is the only deadline knob that works. Its
  header documents a measured ~115 ms structural e2e penalty and an erratum
  about an earlier, wrong claim in that same header. Read it before trusting a
  latency number taken with it.
- `mux_sched.txt` — `adaptive-batching=0` and deadline anchors pushed far out
  (200 ms / 500 ms), so a scheduler release of K frames completes as exactly one
  batch instead of being split or force-pushed half-assembled. It deliberately
  carries **no `batch-size` key**: the INI is re-read at the state change, after
  the app sets its properties, so an INI `batch-size` would override the app's K.

Note that the mux `batch-size` *property* reads the sink-pad count (4), not K,
and cannot be lowered once the pads exist — `mux_sched.txt`, not the property, is
what makes K-frame batches happen. Verified on this hardware: 547/547 batches of
exactly 2 frames at K=2. Batch atomicity is therefore a *runtime* property,
confirmed by the fill histogram, not a configuration-time one — which is why
`vista`'s `check_obligations()` throws only when the mux `batch-size` is below K
and warns when it is above.

## Editing these safely

`pgie_yolo11*.txt` are GLib GKeyFiles: **comments must be on their own line.** A
trailing `key=value  # note` makes the note part of the value and nvinfer fails
with "Failed to parse group property". The files say so at the top; it is a real
mistake that has been made.

`batch-size` in the nvinfer configs is a fallback — the app overrides the
property at runtime (camera count normally, K under `--sched`).
