#!/usr/bin/env python3
"""make_skew_clips.py — regenerate the RQ3 activity-skew microbenchmark clips.

WHY THIS SCRIPT EXISTS. The four clips it builds are the input to the RQ3
(activity-skew) experiments. They are NOT redistributed with this artifact: they
are composited from NVIDIA's DeepStream sample streams, which ship under the
DeepStream EULA rather than a redistributable licence, and sample_walk.mov
depicts identifiable people. So the artifact ships the recipe and the reference
hashes instead of the media. Anyone with DeepStream 7.1 installed can rebuild
byte-comparable clips here.

WHAT THE CLIPS ARE. Four 45 s, 640x480, 30 fps H.264/MP4 files with deliberately
lopsided ACTIVITY, so that camera-level importance has something to reallocate:

    cam0  BUSY   sample_720p road traffic (the only camera with steady events)
    cam1  EMPTY  black
    cam2  EMPTY  black
    cam3  RARE   black, with a single ~9.6 s people-walking burst at t=18 s
                 (this is the fairness floor's test: a quiet camera's lone
                 event must still be served while importance concentrates
                 on cam0)

Two variants, and the difference is the whole point of having both:

    --variant persistent   cam0 plays sample_720p at native rate. Each car
                           crossing lasts seconds, so almost any sampling
                           policy catches it. Importance has little to win.
                           (Was analysis/make_importance_clips.py.)
    --variant brief        cam0 plays sample_720p subsampled every 6th frame,
                           so cars cross in ~4 frames (~145 ms). Below ~50%
                           coverage a crossing is often missed entirely, so
                           WHICH camera gets the GPU actually matters. This is
                           the variant that discriminates.
                           (Was analysis/make_importance_clips_v2.py.)

TIMING SKEW IS NOT BAKED IN. These clips carry ACTIVITY skew only. The arrival
skew (--skew-ms) is injected at run time by the replay harness, exactly as with
the real-camera clips. Do not confuse the two: see the paper's RQ3 setup.

WHY SPED-UP CONTINUOUS AND NOT BLACK-STROBING. An earlier attempt gave cam0 a
strobe (3 content frames, 7 black, repeat) to make events brief. Those clips
decoded perfectly on the Jetson hardware decoder — verified frame by frame — and
then produced ZERO detections through nvinfer. It is a real DeepStream quirk:
rapidly black-interspersed content silently yields no detections. Sped-up
continuous motion gives short events without tripping it. Fully black cameras
(cam1/cam2) are fine, because all-black content legitimately detects nothing.

RUN THE BRIEF VARIANT AT --sched-depth 1. At the default depth 2 the
double-buffered release plus the 1-frame stash caps any single camera near even
share regardless of importance, so importance cannot concentrate and the
comparison is uninformative. Compare VISTA-Fresh vs VISTA-Activity both at
depth 1.

HOW REPRODUCIBLE ARE THESE CLIPS, REALLY? Measured, not assumed (2026-07-15,
on the paper's own rig, rebuilding from the same DeepStream samples):

  * The MP4 CONTAINER hash never reproduces. qtmux stamps wall-clock creation
    and modification times into mvhd/tkhd/mdhd; rebuilding a byte-identical
    clip still yields a different file hash in 18 bytes. So this script
    verifies the H.264 ELEMENTARY BITSTREAM, demuxed back out, not the .mp4.
  * cam1/cam2 (pure black) reproduce EXACTLY — byte-identical bitstreams, and
    identical across both variants.
  * cam0 reproduced EXACTLY against clips encoded six days earlier on this
    machine.
  * cam3 DOES NOT REPRODUCE, even run-to-run on the same machine with
    bit-identical input frames. Three consecutive encodes gave three different
    bitstreams (2 683 504 / 2 685 406 / 2 684 572 bytes). Cause: x264enc
    defaults to threads=0 (auto) with CBR rate control, and cam3 is the one
    clip built around hard scene changes (black -> walk burst -> black), so
    the rate-control feedback across worker threads resolves differently
    depending on thread scheduling. Setting threads=1 makes the bitstream
    deterministic (verified: 3/3 identical size) — see --deterministic, and
    read its caveat before using it.

The practical consequence: rebuilt clips are equivalent in construction but not
guaranteed identical in detections. Re-score your own YOLO11x oracle rather
than reusing the archived event counts.

Usage:
    scripts/make_skew_clips.py --variant brief
    scripts/make_skew_clips.py --variant persistent --out-dir /tmp/clips_pers
    scripts/make_skew_clips.py --variant brief --verify-only
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np

# DeepStream's stock sample streams. Present on any DeepStream 7.1 install.
DEFAULT_SAMPLES = "/opt/nvidia/deepstream/deepstream/samples/streams"

W, H, FPS = 640, 480, 30
L_SEC = 45
L = L_SEC * FPS                 # 1350 output frames per clip
WALK_START = 18 * FPS           # cam3's burst begins at t=18 s
SPEEDUP = 6                     # 'brief' only: keep every 6th source frame

# SHA-256 of the H.264 ELEMENTARY BITSTREAM (not the .mp4) of the clips the
# paper's RQ3 runs actually consumed, demuxed with:
#   gst-launch-1.0 filesrc ! qtdemux ! h264parse !
#       video/x-h264,stream-format=byte-stream,alignment=au ! filesink
# Extracted from the originals on 2026-07-15; the extraction is itself
# deterministic (verified by repeating it).
#
# Note cam1 and cam2 share one hash across BOTH variants: 45 s of black encodes
# identically every time, which is exactly why they make a good canary.
REFERENCE_ES_SHA256 = {
    "persistent": {
        "cam0.mp4": "b7bd346fae4207d2f5853f83766331aa31249cf4d01a582e4904c6d82067b3ba",
        "cam1.mp4": "c077e333cf7fd8369e33b9faff84ba3d2674f53af1c345463a5b39bf5c5a2ee9",
        "cam2.mp4": "c077e333cf7fd8369e33b9faff84ba3d2674f53af1c345463a5b39bf5c5a2ee9",
        "cam3.mp4": "49eb76031e09150566ef439a965eda64a9b4014dae4345a3381aed0580362d8b",
    },
    "brief": {
        "cam0.mp4": "7bc35dccad6ff40f2fc7d3ed3e04424a05a9c415225644ef4b8da7defcb9cce5",
        "cam1.mp4": "c077e333cf7fd8369e33b9faff84ba3d2674f53af1c345463a5b39bf5c5a2ee9",
        "cam2.mp4": "c077e333cf7fd8369e33b9faff84ba3d2674f53af1c345463a5b39bf5c5a2ee9",
        "cam3.mp4": "0acea38f652e6d6bb2b8eee2196a7c39aa1eec98329fa6ef38a48423d1a8aa40",
    },
}

# What a mismatch means, per clip. This is measured behaviour, not a guess:
# see the module docstring. Encoding cam3 twice on ONE machine already produces
# two different bitstreams, so a cam3 mismatch carries no information at all
# and must not be reported as a failure.
CLIP_EXPECTATION = {
    "cam0.mp4": "reproducible on the same libx264 build (verified across a 6-day gap)",
    "cam1.mp4": "must match — 45 s of black is fully deterministic",
    "cam2.mp4": "must match — 45 s of black is fully deterministic",
    "cam3.mp4": "NOT reproducible — x264 threaded CBR over hard scene changes; "
                "differs run-to-run on identical input",
}
NONDETERMINISTIC_CLIPS = ("cam3.mp4",)

# The authors' YOLO11x oracle event count for each variant — the denominator
# of every RQ3 recall number. Verified against their derived JSONs, which are
# not distributed here:
#   brief      368  recall_brief.json   ("n_events", oracle charBrief_x/ref_r0)
#   persistent 257  recall_impcmp.json  ("n_events", oracle charImp_x/ref_r0)
# Rebuild the oracle on your own clips and compare: a count far from these means
# the clips drifted enough to change the ground truth, and RQ3 numbers computed
# against them are not comparable to the paper's.
ORACLE_EVENTS = {"persistent": 257, "brief": 368}

# cam1/cam2 are the canary: pure black, and they reproduced byte-identically in
# every test including across both variants. If THEY differ, the encoder
# settings differ (profile/container/preset) — that is a real problem, unlike a
# cam0 or cam3 difference.
CANARY_CLIPS = ("cam1.mp4", "cam2.mp4")


def writer(path: str, deterministic: bool = False) -> cv2.VideoWriter:
    """Open an x264 writer producing constrained-baseline H.264 in MP4.

    constrained-baseline is not an aesthetic choice: it matches the real-camera
    clips, and it is what the Jetson's nvv4l2decoder in the replay pipeline
    accepts. High profile is rejected outright as "Unsupported Codec".

    `deterministic` adds threads=1, which makes the bitstream reproducible but
    produces a DIFFERENT encode from the paper's clips — see --deterministic.
    """
    threads = "threads=1 " if deterministic else ""
    gst = (f"appsrc ! videoconvert ! x264enc speed-preset=veryfast "
           f"tune=zerolatency {threads}key-int-max=30 ! "
           f"video/x-h264,profile=constrained-baseline ! h264parse ! qtmux ! "
           f"filesink location={path}")
    vw = cv2.VideoWriter(gst, cv2.CAP_GSTREAMER, 0, float(FPS), (W, H), True)
    if not vw.isOpened():
        raise RuntimeError(
            f"VideoWriter failed to open for {path}. Usually this means OpenCV "
            f"was built without GStreamer support (cv2.CAP_GSTREAMER), or "
            f"x264enc is missing (apt install gstreamer1.0-plugins-ugly).")
    return vw


def crop_resize(frame: np.ndarray) -> np.ndarray:
    """Center-crop to 4:3, then resize to WxH — no horizontal squish.

    The sources are 16:9 (1280x720 and 1920x1080). Squishing them to 4:3 would
    distort cars and people and change what the detector sees, so crop first.
    """
    h, w = frame.shape[:2]
    target_ar = W / H
    if w / h > target_ar:                    # too wide -> crop width
        cw = int(round(h * target_ar))
        x0 = (w - cw) // 2
        frame = frame[:, x0:x0 + cw]
    else:                                    # too tall -> crop height
        ch = int(round(w / target_ar))
        y0 = (h - ch) // 2
        frame = frame[y0:y0 + ch, :]
    return cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)


def read_frames(path: str, limit: int | None = None) -> list[np.ndarray]:
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"{path} not found. These are DeepStream's stock sample streams; "
            f"pass --samples-dir if yours live elsewhere.")
    cap = cv2.VideoCapture(path)
    frames = []
    while limit is None or len(frames) < limit:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(crop_resize(f))
    cap.release()
    if not frames:
        raise RuntimeError(f"decoded 0 frames from {path}")
    return frames


def write_clip(path: str, frames: list[np.ndarray], deterministic: bool = False) -> None:
    vw = writer(path, deterministic)
    for f in frames:
        vw.write(f)
    vw.release()
    print(f"  wrote {os.path.basename(path)}: {len(frames)} frames")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def es_sha256(mp4_path: str) -> str:
    """SHA-256 of the H.264 elementary bitstream inside an MP4.

    Hashing the .mp4 itself is meaningless: qtmux writes wall-clock times into
    the header, so two byte-identical encodes differ in 18 bytes. Demuxing back
    to an Annex-B elementary stream strips the container and leaves exactly the
    thing that determines what the detector sees. The demux is deterministic
    (verified by repetition).
    """
    if not shutil.which("gst-launch-1.0"):
        raise RuntimeError("gst-launch-1.0 not found; cannot demux for hashing.")
    tmp = tempfile.NamedTemporaryFile(suffix=".h264", delete=False)
    tmp.close()
    try:
        cmd = ["gst-launch-1.0", "-q",
               "filesrc", f"location={mp4_path}", "!", "qtdemux", "!", "h264parse", "!",
               "video/x-h264,stream-format=byte-stream,alignment=au", "!",
               "filesink", f"location={tmp.name}"]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode != 0 or os.path.getsize(tmp.name) == 0:
            raise RuntimeError(f"demux of {mp4_path} failed: "
                               f"{r.stderr.decode(errors='replace')[:400]}")
        return sha256_file(tmp.name)
    finally:
        os.unlink(tmp.name)


def verify(out_dir: str, variant: str) -> int:
    """Compare clips in out_dir against the archived reference bitstreams.

    Returns 0 if everything that CAN match does, 1 if a reproducible clip
    drifted, 2 if a file is missing. cam3 never counts against the result.
    """
    ref = REFERENCE_ES_SHA256[variant]
    missing = [n for n in ref if not os.path.isfile(os.path.join(out_dir, n))]
    if missing:
        print(f"[verify] missing in {out_dir}: {', '.join(missing)}", file=sys.stderr)
        return 2

    matched, drift, canary_drift, expected_drift = [], [], [], []
    for name, want in sorted(ref.items()):
        got = es_sha256(os.path.join(out_dir, name))
        if got == want:
            matched.append(name)
            print(f"  {name}: MATCH   (bitstream)")
        elif name in NONDETERMINISTIC_CLIPS:
            expected_drift.append(name)
            print(f"  {name}: differs — EXPECTED, carries no information")
            print(f"      {CLIP_EXPECTATION[name]}")
        else:
            drift.append(name)
            if name in CANARY_CLIPS:
                canary_drift.append(name)
            print(f"  {name}: DIFFERS")
            print(f"      want {want}\n      got  {got}")
            print(f"      expectation: {CLIP_EXPECTATION[name]}")

    checkable = len(ref) - len(NONDETERMINISTIC_CLIPS)
    print(f"\n[verify] {len(matched)}/{len(ref)} bitstreams match "
          f"({checkable} are expected to be reproducible at all).")

    if canary_drift:
        print(f"[verify] FAIL: {', '.join(canary_drift)} differ. Those clips are "
              "45 s of pure black and reproduced identically in every test here, "
              "including across both variants. A mismatch means your ENCODER "
              "SETTINGS differ — check that x264enc negotiated "
              "constrained-baseline and that speed-preset/tune match.",
              file=sys.stderr)
        return 1
    if drift:
        print("[verify] cam0 drifted while the black canaries matched: same "
              "settings, different libx264 build. The clips are structurally "
              "correct but not bit-identical to the paper's.", file=sys.stderr)
    if expected_drift and not drift:
        print("[verify] Everything reproducible reproduced. cam3 differing is "
              "normal (it differs between two encodes on one machine).")

    if drift or expected_drift:
        print(f"[verify] ACTION: build your own YOLO11x oracle on THESE clips. "
              f"The archived count ({ORACLE_EVENTS[variant]} events) is the "
              f"denominator of the paper's RQ3 recall numbers and applies only "
              f"to the archived clips.", file=sys.stderr)
    return 1 if drift else 0


def build(variant: str, out_dir: str, samples_dir: str,
          deterministic: bool = False) -> None:
    os.makedirs(out_dir, exist_ok=True)
    black = np.zeros((H, W, 3), dtype=np.uint8)
    p720 = os.path.join(samples_dir, "sample_720p.mp4")
    pwalk = os.path.join(samples_dir, "sample_walk.mov")

    if variant == "persistent":
        print("cam0 (BUSY, PERSISTENT): sample_720p at native rate")
        # The source is 1442 frames; we need 1350, so the loop below is dead
        # code on a stock DeepStream install. It is kept because a shorter
        # sample_720p in some other DeepStream version would otherwise produce
        # a short clip, and a short cam0 silently changes the experiment.
        busy = read_frames(p720, limit=L)
        while len(busy) < L:
            busy += busy[:L - len(busy)]
        cam0 = busy[:L]
    else:  # brief
        print(f"cam0 (BUSY, BRIEF): sample_720p sped up {SPEEDUP}x (continuous)")
        src = read_frames(p720)
        sub = src[::SPEEDUP]
        cam0 = (sub * (L // len(sub) + 1))[:L]
        # ~26 source frames per crossing at native rate is the measured figure
        # this subsample was chosen against.
        print(f"    {len(src)} source frames -> {len(sub)} unique -> cars cross "
              f"in ~{26 // SPEEDUP} frames (~{26 / SPEEDUP / FPS * 1000:.0f} ms each)")
    write_clip(os.path.join(out_dir, "cam0.mp4"), cam0, deterministic)

    print("cam1, cam2 (EMPTY): black")
    write_clip(os.path.join(out_dir, "cam1.mp4"), [black] * L, deterministic)
    write_clip(os.path.join(out_dir, "cam2.mp4"), [black] * L, deterministic)

    print("cam3 (RARE): black + one walk burst + black")
    walk = read_frames(pwalk)
    cam3 = [black] * L
    for i, f in enumerate(walk):
        if WALK_START + i < L:
            cam3[WALK_START + i] = f
    end = min(WALK_START + len(walk), L)
    print(f"    walk burst: frames {WALK_START}..{end} "
          f"(t={WALK_START / FPS:.0f}..{end / FPS:.1f}s)")
    write_clip(os.path.join(out_dir, "cam3.mp4"), cam3, deterministic)

    print(f"\ndone -> {out_dir}  ({L} frames = {L_SEC}s each @ {FPS}fps, {W}x{H})")


def main() -> int:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_clip_root = os.environ.get("VISTA_CLIPS_DIR",
                                       os.path.join(repo, "clips"))
    ap = argparse.ArgumentParser(
        description="Build the RQ3 activity-skew microbenchmark clips.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="The clips are not redistributed (DeepStream EULA + they depict "
               "people). This script is how a reproducer obtains them.")
    ap.add_argument("--variant", required=True, choices=("persistent", "brief"),
                    help="'brief' is the discriminating variant used for the "
                         "paper's RQ3 headline; 'persistent' is the control "
                         "where events last long enough that policy barely matters")
    ap.add_argument("--out-dir", default=None,
                    help="output directory (default: "
                         "$VISTA_CLIPS_DIR/clips_importance[_brief])")
    ap.add_argument("--samples-dir", default=DEFAULT_SAMPLES,
                    help=f"DeepStream sample streams (default {DEFAULT_SAMPLES})")
    ap.add_argument("--verify-only", action="store_true",
                    help="do not build; only hash existing clips against the "
                         "archived reference bitstreams")
    ap.add_argument("--deterministic", action="store_true",
                    help="encode with x264 threads=1, which makes the bitstream "
                         "reproducible run-to-run. NOT the paper's recipe: the "
                         "archived clips were encoded at the default threads=0, "
                         "so this produces a different (self-consistent) cam3 "
                         "and cam0. Use it when you want repeatable clips of "
                         "your own, not when you want the paper's.")
    args = ap.parse_args()

    if args.out_dir:
        out_dir = os.path.abspath(args.out_dir)
    else:
        leaf = "clips_importance" if args.variant == "persistent" else "clips_importance_brief"
        out_dir = os.path.join(default_clip_root, leaf)

    if args.verify_only:
        return verify(out_dir, args.variant)

    build(args.variant, out_dir, args.samples_dir, args.deterministic)

    if args.deterministic:
        print("\n[build] --deterministic: skipping the reference check. These "
              "clips are reproducible but are NOT the paper's encode, so the "
              "archived hashes are not the right yardstick and the archived "
              f"oracle ({ORACLE_EVENTS[args.variant]} events) does not apply.")
        return 0

    print("\n=== bitstream check against the archived clips ===")
    verify(out_dir, args.variant)
    # Drift is never fatal here: the clips are usable, they just need their own
    # oracle, and cam3 cannot match by construction. verify() has said so on
    # stderr. Exit 0 so this does not break a reproduction script.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
