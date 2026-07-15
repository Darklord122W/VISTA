# DeepStream-Yolo — vendoring provenance

## Upstream

| | |
|---|---|
| Project | DeepStream-Yolo |
| Author | Marcos Luciano (`marcoslucianops`) |
| Repository | https://github.com/marcoslucianops/DeepStream-Yolo |
| Licence | MIT (see `LICENSE.md`, copied verbatim) |
| Commit | `2894babce8e75c49115dbe0c7b516289ed853565` |
| Commit date | 2026-01-25 12:24:30 -0300 |
| Commit subject | "Add YOLOv6 and YOLO-Master + minor fixes" |
| Branch | `master` |

The commit was read from the working clone the paper's models were actually
built with, at
`multicam_perception_rt/models/_build/DeepStream-Yolo`. That clone is a
**shallow** clone (`--depth 1`), so the commit id is exact but no history is
available locally to confirm it against upstream; verify it against GitHub if
that matters to you.

The clone's tracked files were unmodified at vendoring time — the only
differences from upstream were untracked build products (`*.o`, the built
`.so`), which are not vendored here.

## What is vendored, and what is not

Vendored:

| path | why |
|---|---|
| `nvdsinfer_custom_impl_Yolo/` | the custom bbox parser. nvinfer loads the `.so` built from this and calls `NvDsInferParseYoloCuda`. Without it the pipeline produces no detections. |
| `utils/export_yolo11.py` | the ONNX exporter. It is the only supported way to produce a graph the parser can read (see below). |
| `LICENSE.md` | MIT, upstream's own text. |

Deliberately NOT vendored:

* the nested `.git` directory (a shallow clone's metadata; not useful to a
  consumer and it would confuse a `git add`);
* `*.o` and the built `libnvdsinfer_custom_impl_Yolo.so` — those are specific to
  the DeepStream SDK, CUDA and compiler on the machine that built them.
  `scripts/fetch_models.sh` rebuilds the `.so` locally;
* the other 25 `utils/export_*.py` exporters and the ~27 `config_infer_*.txt`
  samples for models this paper does not use (YOLOv5, RT-DETR, PP-YOLOE, ...).
  This artifact only ever used YOLO11.

## The one local modification

`utils/export_yolo11.py` carries **exactly one functional change** from upstream
`2894bab`: `dynamo=False` is passed to `torch.onnx.export`. Everything else is
byte-identical to upstream (the rest of the diff is the comment block explaining
the change).

Why it must be there: `torch >= 2.9` defaults to the dynamo / `torch.export`
ONNX path. That path does not reproduce the graph shape that this exporter's
`DeepStreamOutput` module is written to produce, and
`libnvdsinfer_custom_impl_Yolo` cannot parse the result. **The failure is
silent** — the ONNX loads, TensorRT builds an engine, the pipeline runs, and
every frame yields zero detections.

Why it is pre-applied here rather than patched at run time: the original
`scripts/download_yolo11n.sh` — which no longer exists; its role is now
`scripts/fetch_models.sh` — cloned upstream fresh and injected the argument
with

```sh
if ! grep -q "dynamo=False" export_yolo11.py; then
  sed -i 's/^        verbose=False,/^        verbose=False,\n        dynamo=False,/' export_yolo11.py
fi
```

That construction **fails open**. The `sed` is anchored to `verbose=False,` at
exactly eight spaces of indentation. If upstream ever reformats that call — and
upstream is not pinned in that script; it clones `master` — the `sed` matches
nothing, the `grep` still finds no `dynamo=False`, no error is raised, and the
build silently produces the zero-detection graph. Vendoring at a pin with the
patch already applied removes the guess. `scripts/fetch_models.sh` additionally
greps for the line and refuses to run without it, so a careless re-vendor is
caught loudly instead of silently.

(At the vendored commit the anchor does still match, so the original script
would have worked today. The point is that nothing guaranteed it would.)

## Why the stock Ultralytics ONNX export cannot be substituted

`yolo export format=onnx` emits the raw YOLO11 head: `(B, 84, 8400)` — 4 box
coordinates plus 80 per-class scores. `export_yolo11.py` appends a
`DeepStreamOutput` module that transposes to `(B, 8400, 84)` and reduces the
class scores with a `max`, producing `(B, 8400, 6)` =
`[x1, y1, x2, y2, score, label]`. That 6-wide layout is exactly what
`NvDsInferParseYoloCuda` reads. Feed the parser a stock Ultralytics graph and it
reads whatever happens to be at those offsets — which, again, surfaces as zero
detections rather than an error.

## Verified locally (2026-07-15, Jetson AGX Orin, DeepStream 7.1, CUDA 12.6)

* The vendored `nvdsinfer_custom_impl_Yolo/` builds clean from this tree:
  `CUDA_VER=12.6 make` → `libnvdsinfer_custom_impl_Yolo.so`, exit 0.
* The resulting `.so` exports both symbols the pgie configs name:
  `NvDsInferParseYoloCuda` and `NvDsInferYoloCudaEngineGet`.
* The patched `utils/export_yolo11.py` compiles and the diff against upstream is
  the single `dynamo=False` line plus comments.

The build products from that check were removed; only sources are vendored.

## Re-vendoring

```sh
git clone https://github.com/marcoslucianops/DeepStream-Yolo.git
cd DeepStream-Yolo && git checkout 2894babce8e75c49115dbe0c7b516289ed853565
```

Then copy `nvdsinfer_custom_impl_Yolo/`, `utils/export_yolo11.py` and
`LICENSE.md`, re-apply the `dynamo=False` argument, and drop `.git` and all build
products. If you re-vendor at a *different* commit, re-verify that the parser's
expected output layout still matches what `export_yolo11.py` produces — that
pairing is the whole reason this is pinned.
