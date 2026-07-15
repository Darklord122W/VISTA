#!/usr/bin/env bash
# =============================================================================
# build_engines.sh — pre-build every TensorRT engine the paper's campaigns need.
#
# Runs scripts/build_engine.py once per pgie config. Do this BEFORE any timed
# run: otherwise nvinfer builds the engine inside your first run and the
# multi-minute stall lands in the measured window.
#
# BUDGET THIS. On the paper's rig (AGX Orin 64 GB, MODE_30W, GPU 612 MHz) the
# yolo11x oracle engine alone took ~37 minutes; the full set is roughly an hour.
# Engines are cached, so a re-run of this script is a no-op per model whose
# engine already exists — unless you deleted it or changed the ONNX.
#
# Usage:
#     scripts/build_engines.sh                 # all five: n s m l x
#     scripts/build_engines.sh yolo11m         # only the primary operating point
#     scripts/build_engines.sh yolo11m yolo11x # the smallest useful set:
#                                              #   m = RQ1/RQ2 primary, x = oracle
#     BATCH=4 scripts/build_engines.sh
#
# Which engines you actually need:
#   yolo11m  the paper's primary operating point (rho ~= 1.86; Tables II/IV)
#   yolo11x  the OFFLINE REFERENCE DETECTOR that defines the event oracle.
#            Every recall number is scored against it. Do not substitute.
#   yolo11n/s/l  the load-sensitivity sweep (RQ2, Table III).
# =============================================================================
set -euo pipefail

BATCH="${BATCH:-4}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${VISTA_CONFIG_DIR:-${REPO_ROOT}/config}"
MODELS_DIR="${VISTA_MODELS_DIR:-${REPO_ROOT}/models}"

ALL_MODELS=(yolo11n yolo11s yolo11m yolo11l yolo11x)
if [ "$#" -gt 0 ]; then MODELS=("$@"); else MODELS=("${ALL_MODELS[@]}"); fi

# Fail before spending an hour, not during minute 58.
for m in "${MODELS[@]}"; do
  case " ${ALL_MODELS[*]} " in
    *" ${m} "*) ;;
    *) echo "ERROR: unknown model '${m}'. Known: ${ALL_MODELS[*]}" >&2; exit 2 ;;
  esac
  [ -f "${CONFIG_DIR}/pgie_${m}.txt" ] || {
    echo "ERROR: ${CONFIG_DIR}/pgie_${m}.txt missing." >&2; exit 2; }
  [ -f "${MODELS_DIR}/${m}.onnx" ] || {
    echo "ERROR: ${MODELS_DIR}/${m}.onnx missing — run scripts/fetch_models.sh ${m} first." >&2
    exit 2; }
done
[ -f "${MODELS_DIR}/nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so" ] || {
  echo "ERROR: the custom bbox parser .so is missing — run scripts/fetch_models.sh." >&2
  echo "       nvinfer will build the engine without it and then find no detections." >&2
  exit 2; }

echo "==> Building engines for: ${MODELS[*]}  (batch=${BATCH})"
echo "==> Expect ~1 h for the full set; yolo11x alone was ~37 min on the paper's rig."
echo

start_all=$(date +%s)
for m in "${MODELS[@]}"; do
  cfg="${CONFIG_DIR}/pgie_${m}.txt"
  # The engine path is whatever the config declares, so read it back rather than
  # reconstructing the name — nvinfer's auto-naming has bitten this before.
  eng_rel="$(sed -n 's/^model-engine-file=//p' "${cfg}" | head -1)"
  eng="$(cd "${CONFIG_DIR}" && readlink -m "${eng_rel}")"
  if [ -f "${eng}" ]; then
    echo "==> ${m}: engine already present, skipping ($(basename "${eng}"))"
    continue
  fi
  echo "==> ${m}: building ..."
  t0=$(date +%s)
  python3 "${SCRIPT_DIR}/build_engine.py" --config "${cfg}" --batch "${BATCH}"
  t1=$(date +%s)
  if [ ! -f "${eng}" ]; then
    # nvinfer can exit 0 having failed to serialize (e.g. out of disk). Catch it
    # here rather than at the start of a 45 s timed run.
    echo "ERROR: ${m}: build_engine.py returned 0 but ${eng} does not exist." >&2
    exit 1
  fi
  echo "==> ${m}: done in $(( t1 - t0 ))s -> ${eng}"
done
echo
echo "==> All requested engines present. Total $(( $(date +%s) - start_all ))s."
echo "    Engines are tied to THIS GPU + TensorRT + driver. Do not copy them"
echo "    to another machine; re-run this script there instead."
