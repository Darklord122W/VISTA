# Models and engines

**No model weights, ONNX files or TensorRT engines ship with this repository.**
This page is the recipe that produces them, and the explanation of why shipping
them would have been worse than not shipping them.

Read [the tolerance statement](#read-this-first-what-will-and-will-not-reproduce)
before you start, so you know what "reproduced" is allowed to mean.

---

## Read this first: what will and will not reproduce

| Artifact | Reproduces bit-exactly? |
|---|---|
| `labels.txt` | Yes (80 COCO class names, one per line). |
| `<model>.onnx` | Expected to, from the same `.pt` with the same exporter and options. **Not verified across machines** — treat as expected, not guaranteed. |
| `<model>.onnx_b4_gpu0_fp16.engine` | **No. Never. Not even on this same machine.** |
| Detections | No, to the last decimal. Same detections, same tracks, same conclusions — with FP16 numerics noise. |

**Why the engine cannot match.** TensorRT builds an engine by *timing candidate
kernels on the actual device* and picking the fastest. Timing has noise, so
tactic selection has noise, so the serialized bytes differ between builds of
the same ONNX on the same board. On top of that the engine is baked to a
specific compute capability (`sm_87` for Orin), a specific TensorRT version
(10.3.0 here), and a specific CUDA/driver stack. An engine built here would not
merely be unhelpful on your machine — TensorRT would refuse to deserialize it,
or worse, deserialize something wrong.

**So: do not compare engine hashes across machines.** Compare *behaviour*.

**What the hash is for.** Every run records `engine_sha256_16` in its
`run_meta.json`, which is what lets you prove that a set of runs used *one*
engine rather than assuming it. In the paper's campaign each model had exactly
one engine hash across every run that used it:

| `model_tag` | `engine_sha256_16` |
|---|---|
| `s` | `0682566be3d7450b` |
| `m` | `c60c26a55e914320` |
| `l` | `1226706a45485594` |
| `x` | `03a8c113800a2587` |

That is what the field is for: it proves the campaign's runs all used *the same*
engine per detector, so cross-arm comparisons are not confounded by a rebuild.
It is not a checksum you can match.

---

## Why no weights ship

Three reasons, in descending order of how much they would have hurt you.

1. **The engines would be wrong elsewhere.** See above. Shipping a `sm_87` /
   TRT-10.3 engine in a public artifact invites someone to run it on other
   hardware and get either a deserialization failure (best case) or silent
   nonsense (worst case). A recipe cannot be wrong in that way.
2. **Size.** The `models/` tree on the reference machine is 6.4 GB. Only
   678 MB of that is artifacts (5 ONNX files + 5 engines); the remaining
   ~5.7 GB is the export scratch directory — a Python venv with torch, a
   DeepStream-Yolo clone, and the `.pt` checkpoints. None of it belongs in a
   research artifact.
3. **The weights are not ours.** YOLO11 checkpoints come from Ultralytics under
   their own licence and are one command away. Redistributing them adds a
   licence surface and subtracts nothing.

---

## Why you cannot use a stock Ultralytics ONNX export

This is the failure mode most likely to cost you an afternoon, so it gets its
own section.

The pgie configs in `config/` point at a **custom bbox parser**
(`libnvdsinfer_custom_impl_Yolo.so`, from
[DeepStream-Yolo](https://github.com/marcoslucianops/DeepStream-Yolo)):

```ini
parse-bbox-func-name=NvDsInferParseYoloCuda
custom-lib-path=../models/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so
engine-create-func-name=NvDsInferYoloCudaEngineGet
```

That parser reads **one specific output layout**, and DeepStream-Yolo's
exporter is what produces it. The exporter appends a `DeepStreamOutput` module
to the network which does, verbatim:

```python
x = x.transpose(1, 2)                                   # (B, 84, 8400) -> (B, 8400, 84)
boxes = x[:, :, :4]                                     # xyxy
scores, labels = torch.max(x[:, :, 4:], dim=-1, keepdim=True)
return torch.cat([boxes, scores, labels.to(boxes.dtype)], dim=-1)   # (B, 8400, 6)
```

So the exported graph has a **`(B, 8400, 6)`** output: boxes, score, label —
transposed, argmax'd, concatenated. You can see it in the archived run logs, in
the engine TensorRT itself reports:

```
0   INPUT  kFLOAT input   3x640x640   min: 1x3x640x640  opt: 4x3x640x640  Max: 4x3x640x640
1   OUTPUT kFLOAT output  8400x6      min: 0            opt: 0            Max: 0
```

A stock `YOLO(...).export(format="onnx")` gives you the raw `(B, 84, 8400)`
head with no transpose and no argmax. The custom `.so` does not parse that
layout. It will not error usefully; you will get no detections, or garbage
boxes.

**The rule:** the ONNX and the parser `.so` are a matched pair. Use the
exporter that comes with the parser. Do not mix.

### The `dynamo=False` requirement

torch ≥ 2.9 defaults `torch.onnx.export` to the new dynamo/`torch.export`
path, which does **not** reproduce the custom output graph above. The export
must force the legacy TorchScript exporter:

```python
torch.onnx.export(model, ..., verbose=False, dynamo=False, opset_version=17, ...)
```

The vendored exporter in `third_party/DeepStream-Yolo/` has this patch
**pre-applied**, and `scripts/fetch_models.sh` refuses to run without it:

```bash
grep -q "dynamo=False" "${EXPORTER}" || {
  echo "ERROR: ${EXPORTER} lacks dynamo=False." >&2
  ...
  exit 2
}
```

That tripwire is there because of how the *original* version of this recipe
failed. It cloned upstream and `sed`-patched `dynamo=False` in at run time,
guarded by the same `grep`. When an upstream reformat moved the anchor line,
the `sed` silently matched nothing, the `grep` still found no `dynamo=False`,
and the export produced a dynamo graph the parser cannot read — **no error,
just zero detections at run time.** Vendoring the patched file removes the
guess; the check remains in case someone re-vendors without it.

If you export by hand, you own this. Symptom of getting it wrong: an ONNX that
loads fine and produces no usable detections.

---

## The recipe

### Step 1 — ONNX + labels + parser `.so`

`scripts/fetch_models.sh` does the whole of step 1. Models are **positional
arguments**; with none, it does all five:

```bash
scripts/fetch_models.sh                    # all five: n s m l x
scripts/fetch_models.sh yolo11m            # just the primary operating point
scripts/fetch_models.sh yolo11m yolo11x    # the smallest useful set: primary + oracle
CUDA_VER=12.2 scripts/fetch_models.sh      # a non-DS7.1 CUDA
```

| Variable | Default | Meaning |
|---|---|---|
| `IMG_SIZE` | `640` | Square inference size. The paper used 640. |
| `CUDA_VER` | `12.6` | **Must match the DeepStream build.** DS 7.1 on Jetson is CUDA 12.6. Passed to the parser's Makefile. |
| `VISTA_MODELS_DIR` | `<repo>/models` | Where artifacts land. |
| `VISTA_MODELS_WORK` | `<models>/_build` | Export scratch (the venv lives here). |
| `VISTA_DS_YOLO_DIR` | `<repo>/third_party/DeepStream-Yolo` | The **vendored** exporter + parser. |

What it does, in order:

1. Uses the **vendored** DeepStream-Yolo in `third_party/`, not a fresh clone.
   Two reasons: upstream master moves, and the export graph and the parser only
   provably agree at the pinned commit; and the `dynamo=False` patch is
   pre-applied there. The script still *checks* for `dynamo=False` and refuses
   to run without it — a tripwire, because the original version of this script
   sed-patched a fresh clone at run time, and when an upstream reformat moved
   the anchor line the patch silently matched nothing and the export produced an
   unparseable graph with no error and zero detections.
2. Creates a **CPU-only throwaway venv** under the work dir and installs
   `ultralytics>=8.3.0`, `onnx`, `onnxscript`. The export never touches the
   system Python or the DeepStream runtime; the only artifacts consumed at
   runtime are the ONNX, `labels.txt` and the `.so`.
3. Downloads `<model>.pt` through Ultralytics' own asset resolver (rather than a
   hard-coded GitHub release URL, which rots). ~110 MB for the full set.
4. Runs the vendored exporter with `--dynamic`.
5. Builds the parser: `CUDA_VER=<ver> make -C third_party/DeepStream-Yolo/nvdsinfer_custom_impl_Yolo`.
6. Publishes `models/<model>.onnx`, `models/labels.txt` (80 lines), and
   `models/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so`.

Budget a few GB of scratch for the venv (torch is the bulk of it). The scratch
directory is not needed at run time and can be deleted afterwards.

Two dependency notes from the script that are load-bearing:

- **`onnxscript` must be installed** even though the legacy exporter is used —
  torch ≥ 2.9 imports it unconditionally from its ONNX export machinery.
- **`onnxslim` / `onnxruntime` are deliberately omitted.** `onnxruntime` aborts
  on the Tegra CPU (its cpuinfo cannot identify the vendor), and simplification
  buys nothing here: TensorRT re-optimises the graph when it builds the engine.
  Do not add `--simplify`.

**`--dynamic` matters.** It puts a dynamic batch axis on input and output, so
**one engine serves any batch 1..N**. VISTA needs this: `nvinfer` runs at
`batch-size = k`, and a K=2 batch must cost S(2) — not S(4) with padding. A
static-batch engine would make the whole `k < N` argument moot. (A static
batch-4 engine and its config existed in the original tree, used only for a
one-off engine A/B comparison. Neither ships here, and neither should be used
for scheduler runs.)

### Step 2 — the TensorRT engine

`nvinfer` builds the engine from the ONNX on first launch and caches it at the
path in `model-engine-file`. The first launch therefore takes **several
minutes** and the app tells you so:

```
[main] Running. First launch may build the TensorRT engine (several minutes). Press Ctrl-C to stop.
```

**Do it up front. Do not let it land inside a timed run.**

```bash
scripts/build_engines.sh                    # all five
scripts/build_engines.sh yolo11m yolo11x    # primary operating point + oracle
scripts/build_engine.py --config config/pgie_yolo11m.txt   # one, by hand
```

`build_engines.sh` runs `build_engine.py` once per pgie config, refuses unknown
model names up front (rather than at minute 58), and skips a model whose engine
already exists. **Budget an hour for the full set** — the yolo11x oracle engine
alone took ~37 minutes on the paper's rig.

`build_engine.py` pushes a few `videotestsrc` frames through the **exact
nvinfer config you will run with** (`videotestsrc → nvstreammux → nvinfer →
fakesink`), which is what forces nvinfer to build and serialize the engine. You
cannot pre-build by hand: the engine must be produced by the same nvinfer that
will load it. No cameras and no clips are involved.

| Flag | Default | Meaning |
|---|---|---|
| `--config` | `config/pgie_yolo11m.txt` | The nvinfer config to build for. |
| `--batch` | `4` | Batch profile to build. Must be ≥ the largest `k`/camera count you will run. The ONNX is dynamic-batch, so one engine serves 1..4 — but TensorRT optimises for the profile it is given, and the paper's runs all used 4. |
| `--width` / `--height` | `640` / `480` | Synthetic source size. |

`BATCH=4` is the env override on `build_engines.sh`.

> **`build_engine.py` runs on the legacy nvstreammux on purpose.** It sets the
> mux's `width`/`height`/`batched-push-timeout` properties, which only exist on
> the legacy mux, and it does not set `USE_NEW_NVSTREAMMUX`. That is fine — it
> is a build harness, not a pipeline. Do not copy its mux setup into anything
> that measures something. And do not run it with `USE_NEW_NVSTREAMMUX=yes` in
> your environment.

Engine naming is nvinfer's, derived from the ONNX name and build parameters:
`yolo11m.onnx_b4_gpu0_fp16.engine`. To force a rebuild, delete the engine file
(or the `model-engine-file` line) and relaunch.

### Step 3 — point a pgie config at it

The five configs in `config/` (`pgie_yolo11{n,s,m,l,x}.txt`) differ only in
`onnx-file` and `model-engine-file`. Select one per run with `--pgie-config`;
`pgie_yolo11n.txt` is the app's default.

Settings shared by all of them, and why:

| Key | Value | Why |
|---|---|---|
| `net-scale-factor` | `0.0039215697906911373` | 1/255 — how the ONNX was exported. |
| `model-color-format` | `0` (RGB) | YOLO11's channel order. |
| `network-mode` | `2` (FP16) | ~2× throughput on Orin, no calibration needed. |
| `batch-size` | `4` | **A fallback.** The app overrides the nvinfer `batch-size` property at runtime — to the camera count normally, and to `k` under `--sched`. |
| `maintain-aspect-ratio` + `symmetric-padding` | `1`, `1` | YOLO11 letterbox. |
| `cluster-mode` | `2` | NMS clustering; YOLO outputs raw boxes. |
| `pre-cluster-threshold` | `0.25` | Confidence floor. |
| `nms-iou-threshold` | `0.45` | |
| `topk` | `300` | |

Two file-format traps, both real:

- **The pgie config is a GLib GKeyFile.** Comments must be on their **own
  line**. `key=value  # note` makes the note part of the value, and nvinfer
  fails with `Failed to parse group property`.
- **Model paths resolve relative to the config file's directory**, not the
  working directory. That is why they read `../models/...` and why the app runs
  correctly from anywhere.

---

## The offline reference detector (YOLO11x)

The paper's ground truth comes from an offline completeness pass in which
YOLO11x processes **every** frame, so no policy grades its own homework. All
123 events in the oracle come from that pass.

**Build YOLO11x's engine deliberately and verify it before any campaign.** It
is the largest engine here (117 MB serialized, from a 228 MB ONNX) and takes
the longest to build.

> **Known trap.** The original campaign runner wrapped the reference engine
> build in `timeout 3600` and, on timeout, **fell back to YOLO11l with only a
> warning**. That silently changes the ground truth every downstream number is
> scored against. If you adapt that runner, make the fallback fatal. Confirm
> the oracle's engine hash matches `03a8c113800a2587`'s *provenance* (the same
> ONNX, the same options) rather than its bytes — see the tolerance statement
> at the top.

---

## Checklist

- [ ] `models/<MODEL>.onnx` exists and was produced by DeepStream-Yolo's
      exporter (not `YOLO.export`).
- [ ] `dynamo=False` was in effect. (Symptom of failure: no detections.)
- [ ] `--dynamic` was passed → dynamic batch axis → one engine serves 1..N.
- [ ] `models/labels.txt` has 80 lines.
- [ ] `models/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so`
      exists and was built with `CUDA_VER` matching your DeepStream.
- [ ] The engine built and TensorRT reports `OUTPUT kFLOAT output 8400x6`.
- [ ] The engine's `min: 1x3x640x640 … Max: 4x3x640x640` — i.e. it really is
      dynamic-batch.
- [ ] You are not expecting the engine hash to match ours.
