#!/usr/bin/env bash
# =============================================================================
# fetch_models.sh — download YOLO11 weights and export the DeepStream ONNX.
#
# The artifact ships NO model weights and NO TensorRT engines, on purpose:
#   * the five .pt files plus their ONNX come to ~6.4 GB, and
#   * a .engine is specific to the exact GPU, TensorRT version and driver that
#     built it. Shipping one would produce silently wrong behaviour (or a hard
#     load failure) on any other machine. This script plus build_engines.sh is
#     the recipe that regenerates both locally.
#
# What this produces, under $VISTA_MODELS_DIR (default <repo>/models):
#     yolo11{n,s,m,l,x}.onnx     detector graphs the pgie configs point at
#     labels.txt                 80 COCO class names, in the model's order
#     nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so
#
# WHY NOT STOCK ULTRALYTICS `yolo export format=onnx`:
#   Ultralytics emits the raw YOLO11 head, (B, 84, 8400) — 4 box coords plus 80
#   per-class scores, transposed, with no NMS-ready packing. DeepStream's
#   nvinfer cannot parse that on its own. DeepStream-Yolo's exporter appends a
#   `DeepStreamOutput` module that transposes to (B, 8400, 84) and reduces the
#   class scores with a max, yielding (B, 8400, 6) = [x1,y1,x2,y2,score,label].
#   That exact 6-wide layout is what libnvdsinfer_custom_impl_Yolo's
#   NvDsInferParseYoloCuda reads. Export with any other tool and the parser
#   reads garbage — usually as zero detections rather than an error.
#
# Tested on: Jetson AGX Orin 64 GB, JetPack 6.2.x (L4T r36.5), DeepStream 7.1,
#            CUDA 12.6, TensorRT 10.3, Python 3.10.
#
# Usage:
#     scripts/fetch_models.sh                  # all five: n s m l x
#     scripts/fetch_models.sh yolo11m          # just one
#     scripts/fetch_models.sh yolo11n yolo11x  # the app model + the oracle
#     CUDA_VER=12.2 scripts/fetch_models.sh    # non-DS7.1 CUDA
#
# Downloads ~110 MB of weights for the full set (yolo11x alone is ~110 MB) and
# needs a few GB of scratch for the export venv (torch is the bulk of it).
# =============================================================================
set -euo pipefail

# ---- Tunables ---------------------------------------------------------------
IMG_SIZE="${IMG_SIZE:-640}"     # square inference size; the paper used 640
CUDA_VER="${CUDA_VER:-12.6}"    # must match the DeepStream build (DS7.1 Jetson = 12.6)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MODELS_DIR="${VISTA_MODELS_DIR:-${REPO_ROOT}/models}"
WORK_DIR="${VISTA_MODELS_WORK:-${MODELS_DIR}/_build}"
VENV_DIR="${WORK_DIR}/export-venv"

# The exporter and the bbox parser come from the VENDORED DeepStream-Yolo, not
# from a fresh `git clone`. Two reasons:
#   1. Pinning. Upstream master moves; the export graph and the parser must
#      agree, and they only provably agree at the vendored commit.
#   2. The dynamo=False patch below is PRE-APPLIED in the vendored tree.
# See third_party/DeepStream-Yolo/PROVENANCE.md.
DS_YOLO_DIR="${VISTA_DS_YOLO_DIR:-${REPO_ROOT}/third_party/DeepStream-Yolo}"

ALL_MODELS=(yolo11n yolo11s yolo11m yolo11l yolo11x)
if [ "$#" -gt 0 ]; then
  MODELS=("$@")
else
  MODELS=("${ALL_MODELS[@]}")
fi

for m in "${MODELS[@]}"; do
  case " ${ALL_MODELS[*]} " in
    *" ${m} "*) ;;
    *) echo "ERROR: unknown model '${m}'. Known: ${ALL_MODELS[*]}" >&2; exit 2 ;;
  esac
done

EXPORTER="${DS_YOLO_DIR}/utils/export_yolo11.py"
PARSER_DIR="${DS_YOLO_DIR}/nvdsinfer_custom_impl_Yolo"
for p in "${EXPORTER}" "${PARSER_DIR}/Makefile"; do
  [ -f "${p}" ] || { echo "ERROR: vendored DeepStream-Yolo incomplete: ${p} missing." >&2; exit 2; }
done

# The vendored exporter must already force the legacy ONNX exporter. The
# original of this script sed-patched a fresh clone at run time, guarded by
# `grep -q "dynamo=False"`; when an upstream reformat moved the anchor line the
# sed silently matched nothing, the grep still found no "dynamo=False", and the
# export produced a dynamo graph the parser cannot read — with no error, just
# zero detections at run time. Vendoring the patched file removes the guess;
# this check is a tripwire in case someone re-vendors without the patch.
grep -q "dynamo=False" "${EXPORTER}" || {
  echo "ERROR: ${EXPORTER} lacks dynamo=False." >&2
  echo "       torch>=2.9 would use the dynamo exporter, whose graph" >&2
  echo "       libnvdsinfer_custom_impl_Yolo cannot parse (silently: 0 detections)." >&2
  echo "       Re-vendor from the pin in third_party/DeepStream-Yolo/PROVENANCE.md." >&2
  exit 2
}

mkdir -p "${MODELS_DIR}" "${WORK_DIR}"
echo "==> Repo:     ${REPO_ROOT}"
echo "==> Models:   ${MODELS_DIR}"
echo "==> Exporter: ${EXPORTER}"
echo "==> Building: ${MODELS[*]}  (input ${IMG_SIZE}x${IMG_SIZE})"

# ---- 1. Export venv (CPU-only; isolated from system + DeepStream python) -----
# Prefer stdlib venv; fall back to `virtualenv` on stripped-down JetPack images
# where python3-venv/ensurepip is absent (avoids needing sudo).
if [ ! -d "${VENV_DIR}" ]; then
  echo "==> Creating export venv ..."
  if ! python3 -m venv "${VENV_DIR}" 2>/dev/null; then
    echo "    stdlib venv unavailable (no python3-venv); using virtualenv ..."
    python3 -m virtualenv --version >/dev/null 2>&1 || pip3 install --user virtualenv
    python3 -m virtualenv "${VENV_DIR}"
  fi
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel >/dev/null
echo "==> Installing ultralytics + onnx tooling (several minutes on first run) ..."
# onnxscript is imported unconditionally by torch>=2.9's export machinery even
# on the legacy path, so it must be present. onnxslim/onnxruntime are
# deliberately omitted: onnxruntime aborts on the Tegra CPU (cpuinfo cannot
# identify the vendor), and graph simplification is pointless here because
# TensorRT re-optimizes the graph when it builds the engine anyway.
pip install "ultralytics>=8.3.0" onnx onnxscript

# ---- 2. Weights + ONNX ------------------------------------------------------
cd "${WORK_DIR}"
for MODEL in "${MODELS[@]}"; do
  echo
  echo "==> ${MODEL}"
  if [ ! -f "${MODEL}.pt" ]; then
    echo "    downloading ${MODEL}.pt via Ultralytics ..."
    # Instantiating with a bare name triggers Ultralytics' own asset download,
    # which resolves the current release tag itself — no hard-coded GitHub URL
    # to rot. The weights are Ultralytics' AGPL-3.0 COCO checkpoints.
    python - "${MODEL}" <<'PY'
import sys
from ultralytics import YOLO
YOLO(f"{sys.argv[1]}.pt")
PY
  else
    echo "    ${MODEL}.pt present, reusing."
  fi

  echo "    exporting -> ${MODEL}.onnx (dynamic batch) ..."
  # --dynamic: one engine serves batch 1..N (N = camera count, 4 in the paper).
  # opset 17 (the exporter default) is fine for TensorRT 10.3 / DeepStream 7.1.
  python "${EXPORTER}" -w "${MODEL}.pt" -s "${IMG_SIZE}" --dynamic

  cp -f "${WORK_DIR}/${MODEL}.onnx" "${MODELS_DIR}/${MODEL}.onnx"
  echo "    -> ${MODELS_DIR}/${MODEL}.onnx"
done

# labels.txt is rewritten by every export and is identical across the five
# models (same COCO 80). Publish it once, from whichever ran last.
cp -f "${WORK_DIR}/labels.txt" "${MODELS_DIR}/labels.txt"

# ---- 3. Custom bbox parser --------------------------------------------------
# Built against the DeepStream SDK installed on THIS machine; like the engines,
# it is not portable, which is why it is not shipped.
echo
echo "==> Building libnvdsinfer_custom_impl_Yolo.so (CUDA_VER=${CUDA_VER}) ..."
make -C "${PARSER_DIR}" clean >/dev/null 2>&1 || true
CUDA_VER="${CUDA_VER}" make -C "${PARSER_DIR}"
mkdir -p "${MODELS_DIR}/nvdsinfer_custom_impl_Yolo"
cp -f "${PARSER_DIR}/libnvdsinfer_custom_impl_Yolo.so" \
      "${MODELS_DIR}/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so"

deactivate || true

cat <<EOF

==> DONE. In ${MODELS_DIR}:
$(for m in "${MODELS[@]}"; do echo "      ${m}.onnx"; done)
      labels.txt   ($(wc -l < "${MODELS_DIR}/labels.txt") classes)
      nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so

    config/pgie_yolo11*.txt already point at these paths (../models/, resolved
    relative to the config file).

    Next: scripts/build_engines.sh — pre-builds the TensorRT engines. Without
    it nvinfer builds each engine on first pipeline launch, which puts a
    multi-minute stall inside your first timed run.
EOF
