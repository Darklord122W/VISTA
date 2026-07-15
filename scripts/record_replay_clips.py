#!/usr/bin/env python3
"""record_replay_clips.py — record per-camera clips for reproducible replay.

WHY. Live cameras are not reproducible, and every policy comparison in the
paper needs the SAME input frames under each policy. So the campaigns record
each camera's raw capture once to <out-dir>/cam{i}.mp4, then replay the clips
with `--source file`. Replay is deterministic in (camera_id, buf_pts), which is
what lets runs of different policies be compared frame for frame.

The clips are always RAW — no overlays, no inference baked in. Replaying them
and inferring fresh is the point; a clip with boxes burned into it would be
useless as an experiment input.

WHAT THIS RECORDS. One file per entry in camera_params.yaml's `cameras:` list,
in list order, so cam0.mp4 is the camera reported as camera_id 0. It captures
the SAME front end the measured pipeline uses (MJPG -> jpegparse -> nvjpegdec
-> NVMM NV12), then encodes with the Jetson's hardware H.264 encoder.

DIFFERENCE FROM THE ORIGINAL, STATED PLAINLY. The version of this script in the
research tree did `import main as app; import pipeline_builder as pb` — it
depended on the Python prototype pipeline. This artifact ships the C++
`vista_multicam` app instead, so those modules do not exist here and a verbatim
copy would fail at import. This is a standalone reimplementation of the same
recording pipeline. Two consequences worth knowing:

  * The original's `--display` flag (a live tiled inference view while
    recording) is NOT reimplemented. It needed the prototype's pgie/tracker/
    tiler builders and recorded nothing extra — it was an operator convenience.
  * The element chain here was written to match pipeline_builder._build_v4l2_
    front, including the decoder choice (see below). It has not been diffed
    against the original at the level of every property default.

THE DECODER CHOICE IS NOT ARBITRARY. C920 MJPEG is YUV 4:2:2. `nvv4l2decoder
mjpeg=1` handles 4:2:0 only, so the chain is `jpegparse ! nvjpegdec`. Using
nvv4l2decoder here fails or corrupts colour.

Stopping:
  * --duration N  : stop automatically after N seconds.
  * (no --duration): record until ENTER (or Ctrl-C).
Either way the pipeline is sent EOS so qtmux finalizes the MP4 — killing this
script with SIGKILL leaves unplayable 0-byte-moov files.

Usage:
    scripts/record_replay_clips.py --duration 45
    scripts/record_replay_clips.py --out-dir clips/live --duration 45
"""
from __future__ import annotations

import argparse
import os
import sys

import gi
import yaml

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make(factory: str, name: str) -> Gst.Element:
    el = Gst.ElementFactory.make(factory, name)
    if el is None:
        raise RuntimeError(
            f"failed to create element '{factory}' (name={name}). Missing "
            f"GStreamer/DeepStream plugin? Check with: gst-inspect-1.0 {factory}")
    return el


def _link_chain(elements: list[Gst.Element]) -> None:
    for a, b in zip(elements, elements[1:]):
        if not a.link(b):
            raise RuntimeError(f"link failed: {a.name} -> {b.name}")


def load_cameras(config_path: str) -> list[dict]:
    """Read camera_params.yaml and normalize each camera's capture settings."""
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    defaults = cfg.get("capture", {}) or {}
    cams = cfg.get("cameras", []) or []
    if not cams:
        raise RuntimeError(f"{config_path} lists no cameras.")
    out = []
    for cam in cams:
        c = dict(cam or {})
        merged = {
            "device": c["device"],
            "format": c.get("format", defaults.get("format", "mjpeg")),
            "width": int(c.get("width", defaults.get("width", 640))),
            "height": int(c.get("height", defaults.get("height", 480))),
            "fps": int(c.get("fps", defaults.get("fps", 30))),
            "mjpeg_decoder": c.get("mjpeg_decoder", "nvjpegdec"),
        }
        out.append(merged)
    return out


def validate_cameras(cams: list[dict]) -> None:
    """Fail before recording rather than producing empty clips."""
    missing = [c["device"] for c in cams if not os.path.exists(c["device"])]
    if missing:
        raise RuntimeError(
            f"camera device(s) not present: {', '.join(missing)}. "
            f"Run scripts/detect_cameras.sh — the node numbering is not what you "
            f"probably assume (each C920 also creates a metadata node).")


def _build_capture_front(nbin: Gst.Bin, index: int, cam: dict) -> Gst.Element:
    """v4l2src -> (decode) -> last element, added into nbin. Returns the tail."""
    src = _make("v4l2src", f"cam-src-{index}")
    src.set_property("device", cam["device"])
    # do-timestamp: stamp buffers on arrival. The capture PTS story on this rig
    # is subtle (see docs/design/): what matters for replay is
    # that each clip carries a monotonic 30 fps timeline, which the encoder and
    # qtmux below produce regardless.
    src.set_property("do-timestamp", True)
    nbin.add(src)
    w, h, fps = cam["width"], cam["height"], cam["fps"]

    if cam["format"] == "mjpeg":
        caps = _make("capsfilter", f"cam-caps-{index}")
        caps.set_property("caps", Gst.Caps.from_string(
            f"image/jpeg,width={w},height={h},framerate={fps}/1"))
        nbin.add(caps)
        dec_name = cam["mjpeg_decoder"]
        if dec_name in ("nvjpegdec", "jpegdec"):
            jparse = _make("jpegparse", f"cam-jparse-{index}")
            dec = _make(dec_name, f"cam-jpegdec-{index}")
            nbin.add(jparse)
            nbin.add(dec)
            _link_chain([src, caps, jparse, dec])
            return dec
        if dec_name in ("nvv4l2", "nvv4l2decoder"):
            # Kept for parity with the config schema. On a C920 this is wrong:
            # its MJPEG is 4:2:2 and nvv4l2decoder mjpeg=1 decodes 4:2:0 only.
            dec = _make("nvv4l2decoder", f"cam-jpegdec-{index}")
            dec.set_property("mjpeg", 1)
            nbin.add(dec)
            _link_chain([src, caps, dec])
            return dec
        raise RuntimeError(f"camera {index}: unknown mjpeg_decoder '{dec_name}' "
                           f"(use 'nvjpegdec', 'jpegdec', or 'nvv4l2').")

    if cam["format"] == "raw":
        # YUYV. The C920 does 30 fps raw only up to 640x480 (USB-2 bandwidth).
        caps = _make("capsfilter", f"cam-caps-{index}")
        caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=YUY2,width={w},height={h},framerate={fps}/1"))
        nbin.add(caps)
        _link_chain([src, caps])
        return caps

    raise RuntimeError(f"camera {index}: unknown capture format "
                       f"'{cam['format']}' (use 'mjpeg' or 'raw').")


def build_source_bin(index: int, cam: dict) -> Gst.Bin:
    """One camera's capture branch, ghosted on NVMM NV12."""
    nbin = Gst.Bin.new(f"source-bin-{index}")
    tail = _build_capture_front(nbin, index, cam)
    conv = _make("nvvideoconvert", f"cam-conv-{index}")
    nvmmcaps = _make("capsfilter", f"cam-nvmmcaps-{index}")
    nvmmcaps.set_property("caps", Gst.Caps.from_string(
        "video/x-raw(memory:NVMM),format=NV12"))
    nbin.add(conv)
    nbin.add(nvmmcaps)
    _link_chain([tail, conv, nvmmcaps])
    ghost = Gst.GhostPad.new("src", nvmmcaps.get_static_pad("src"))
    ghost.set_active(True)
    nbin.add_pad(ghost)
    return nbin


def add_record_branch(pipeline: Gst.Pipeline, index: int, out_dir: str) -> Gst.Element:
    """queue -> H264 enc -> parse -> qtmux -> filesink. Returns the head queue."""
    q = _make("queue", f"recq-{index}")
    enc = _make("nvv4l2h264enc", f"enc-{index}")
    parse = _make("h264parse", f"parse-{index}")
    qmux = _make("qtmux", f"recmux-{index}")
    sink = _make("filesink", f"filesink-{index}")
    sink.set_property("location", os.path.join(out_dir, f"cam{index}.mp4"))
    sink.set_property("sync", 0)
    # async=false so this non-live sink does not block the live (v4l2) pipeline's
    # PAUSED->PLAYING preroll. With async=true the pipeline deadlocks in PAUSED.
    sink.set_property("async", 0)
    for el in (q, enc, parse, qmux, sink):
        pipeline.add(el)
    _link_chain([q, enc, parse, qmux, sink])
    return q


def build_record_pipeline(cams: list[dict], out_dir: str) -> Gst.Pipeline:
    Gst.init(None)
    pipeline = Gst.Pipeline.new("record-clips")
    for i, cam in enumerate(cams):
        source_bin = build_source_bin(i, cam)
        pipeline.add(source_bin)
        rec_head = add_record_branch(pipeline, i, out_dir)
        if not source_bin.link(rec_head):
            raise RuntimeError(f"record: source-bin-{i} -> recorder link failed.")
    return pipeline


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Record per-camera replay clips from the live cameras.")
    ap.add_argument("--config",
                    default=os.environ.get(
                        "VISTA_CAMERA_CONFIG",
                        os.path.join(_REPO_ROOT, "config", "camera_params.yaml")))
    ap.add_argument("--out-dir",
                    default=os.environ.get(
                        "VISTA_CLIPS_DIR", os.path.join(_REPO_ROOT, "clips")),
                    help="directory to write cam0.mp4 .. camN.mp4 into")
    ap.add_argument("--duration", type=float, default=None,
                    help="seconds to record (omit to stop with ENTER/Ctrl-C). "
                         "The paper's clips are 45 s.")
    args = ap.parse_args()

    cams = load_cameras(args.config)
    validate_cameras(cams)
    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    n = len(cams)
    stop_desc = f"{args.duration}s" if args.duration else "until ENTER/Ctrl-C"
    print(f"[record] {n} camera(s) -> {out_dir}/cam0..cam{n - 1}.mp4 ({stop_desc})",
          file=sys.stderr)
    for i, c in enumerate(cams):
        print(f"[record]   cam{i}: {c['device']} {c['format']} "
              f"{c['width']}x{c['height']}@{c['fps']}", file=sys.stderr)

    pipeline = build_record_pipeline(cams, out_dir)
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    status = {"error": None}

    def on_msg(_bus, msg, _loop):
        if msg.type == Gst.MessageType.EOS:
            loop.quit()
        elif msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            status["error"] = f"{err} | {dbg}"
            print(f"[record] ERROR: {status['error']}", file=sys.stderr)
            loop.quit()
        return True

    bus.connect("message", on_msg, loop)

    def stop(reason: str) -> bool:
        print(f"[record] stopping ({reason}) — EOS to finalize MP4s...",
              file=sys.stderr)
        pipeline.send_event(Gst.Event.new_eos())
        # Quit even if the bus EOS stalls. The media is already on disk; without
        # this a hung element would hang the script forever.
        GLib.timeout_add(3000, loop.quit)
        return False

    if args.duration:
        GLib.timeout_add(int(args.duration * 1000), lambda: stop("duration elapsed"))
    elif sys.stdin.isatty():
        print("[record] Recording... press ENTER to stop.", file=sys.stderr)

        def on_enter(_ch, _cond):
            try:
                sys.stdin.readline()
            except Exception:
                pass
            stop("ENTER pressed")
            return False

        ch = GLib.IOChannel.unix_new(sys.stdin.fileno())
        GLib.io_add_watch(ch, GLib.IOCondition.IN, on_enter)
    else:
        print("[record] No --duration and stdin is not a TTY — stop with Ctrl-C.",
              file=sys.stderr)

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except KeyboardInterrupt:
        stop("Ctrl-C")
        bus.timed_pop_filtered(8 * Gst.SECOND, Gst.MessageType.EOS)
    finally:
        pipeline.set_state(Gst.State.NULL)

    if status["error"]:
        return 1
    empty = [f"cam{i}.mp4" for i in range(n)
             if os.path.getsize(os.path.join(out_dir, f"cam{i}.mp4")) < 1024]
    if empty:
        print(f"[record] WARNING: suspiciously small: {', '.join(empty)} — "
              f"qtmux may not have finalized.", file=sys.stderr)
        return 1
    print(f"[record] done -> {out_dir}", file=sys.stderr)
    print(f"[record] replay with: --source file --replay-dir {args.out_dir}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
