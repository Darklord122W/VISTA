# Third-party notices

Everything this artifact depends on that we did not write, what its licence is,
and whether it ships here. The short version: **we redistribute exactly one
third-party component (the DeepStream-Yolo bbox parser, MIT), and no model
weights at all.**

The most important line on this page is the AGPL note in §3. If you plan to
build a product on this, read that first.

---

## 1. DeepStream-Yolo — **vendored**

| | |
|---|---|
| What | `nvdsinfer_custom_impl_Yolo`: the custom nvinfer bounding-box parser and CUDA engine hook that let DeepStream consume a YOLO11 ONNX graph. |
| Upstream | https://github.com/marcoslucianops/DeepStream-Yolo |
| Author | Marcos Luciano |
| Licence | MIT |
| Where | `third_party/DeepStream-Yolo/` (source); built to `models/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so`, which the `config/pgie_yolo11*.txt` files name in `custom-lib-path`. The built `.so` is git-ignored — it is compiled against your DeepStream install. |
| Modified? | The export path uses upstream's `utils/export_yolo11.py` to produce an ONNX with the decode + NMS-friendly output layout DeepStream expects. See `scripts/fetch_models.sh` for what is run and why a stock `ultralytics export format=onnx` graph does not work here. |

MIT is compatible with this repository's MIT licence. Upstream's copyright
notice travels with the vendored source; do not strip it.

Every detection behind the paper's numbers was parsed by this code
(`parse-bbox-func-name=NvDsInferParseYoloCuda`). If you replace the parser, you
change the numbers.

## 2. NVIDIA DeepStream SDK — **not redistributed**

| | |
|---|---|
| What | The pipeline itself: `nvstreammux`, `nvinfer`, `nvtracker` (NvSORT), `nvv4l2decoder`, and the `NvDsBatchMeta` headers `vista/` compiles against. |
| Version used | DeepStream 7.1 on JetPack 6, `/opt/nvidia/deepstream/deepstream`. |
| Licence | NVIDIA DeepStream SDK End User License Agreement. |
| Ships here? | **No.** Install it yourself; `docs/usage/01-build.md` states the exact version. |

Two consequences worth stating plainly:

* The **sample streams** under
  `/opt/nvidia/deepstream/deepstream/samples/streams/` are covered by the same
  EULA. The RQ3 skew microbenchmark's clips are composited from them, which is
  one of the two reasons those clips are regenerated locally
  (`scripts/make_skew_clips.py`) rather than shipped.
* `vista/` includes `nvdsmeta.h` / `gstnvdsmeta.h` from the SDK's
  `sources/includes` at build time. It does not copy them.

The tracker is NvSORT, DeepStream's implementation of SORT
(Bewley et al., 2016). Configuration in `config/tracker_config.yml`; the
algorithm and its code are NVIDIA's.

## 3. Ultralytics YOLO11 — **not redistributed; note the licence**

| | |
|---|---|
| What | The detector. `yolo11{n,s,m,l}` are the systems under test; `yolo11x` is the offline reference detector that produces the event ground truth every recall number is scored against. |
| Upstream | https://github.com/ultralytics/ultralytics |
| Licence | **AGPL-3.0** (an Enterprise licence is available from Ultralytics) |
| Ships here? | **No weights, no ONNX, no engines.** `scripts/fetch_models.sh` downloads the checkpoints and exports the ONNX; `scripts/build_engines.sh` builds the TensorRT engines locally. |

> ### The AGPL matters, and it is not a formality
>
> **YOLO11's weights and code are AGPL-3.0.** This repository is MIT, and
> nothing AGPL is present in it — that separation is real and it is why the
> weights are a download step rather than a directory.
>
> But it does not transfer to you for free. If you build a system that uses
> YOLO11 and expose it to users over a network, AGPL-3.0 §13 obliges you to
> offer those users the corresponding source of your whole combined work. For a
> networked multi-camera perception service — which is what most readers of
> this paper are building — that is exactly the case the clause was written
> for. Ultralytics sells an Enterprise licence precisely for people who do not
> want that obligation.
>
> **VISTA itself does not care which detector you run.** The scheduler
> (`vista/`) never looks at pixels, never links to Ultralytics, and reads only
> `num_frames_in_batch` off the batch meta. Any nvinfer-compatible detector
> under any licence works, and swapping to one is a change to
> `config/pgie_*.txt`, not to `vista/`. What you cannot do is take this
> artifact's YOLO11 numbers and a permissive licence at the same time.
>
> We are not your lawyers. Read the licence.

## 4. Everything else

| Component | Licence | Ships here? |
|---|---|---|
| GStreamer 1.20.3 | LGPL-2.1+ | No — system package. Linked dynamically. |
| yaml-cpp (used by `app/`) | MIT | No — system package. |
| matplotlib 3.5.1 | matplotlib licence (BSD-style / PSF-derived) | No — `pip install -r requirements.txt`. |
| PyYAML | MIT | No — `pip install -r requirements.txt`. |
| NumPy (pulled in by matplotlib) | BSD-3-Clause | No. |
| OpenCV (`scripts/make_skew_clips.py` only) | Apache-2.0 | No — distro package; needs a GStreamer-enabled build. |
| TensorRT, CUDA, cuDNN | NVIDIA licences | No — part of JetPack. |

## 5. What we wrote

`vista/`, `app/`, `analysis/`, `figures/src/`, `harness/`, `scripts/`,
`config/`, `docs/`, and every top-level file — MIT, see `LICENSE`.



`vista/src/vista_scheduler.cpp` is vendored from our own working tree
(`multicam_perception_rt/cpp/src/scheduler.cpp`); `vista/PAPER_DIFF.md`
enumerates every difference from the code that produced the paper's numbers.
