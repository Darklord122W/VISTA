#!/usr/bin/env python3
"""build_engine.py — pre-build one TensorRT engine, without needing cameras.

nvinfer builds the engine from the ONNX on first use, which takes minutes (over
half an hour for yolo11x). You do not want that stall to land inside a timed
run, and you cannot pre-build by hand because the engine must be produced by the
same nvinfer that will load it. So this pushes a few synthetic frames
(videotestsrc) through the exact nvinfer config you will run with, which makes
nvinfer build and serialize the engine to the `model-engine-file` path in that
config. No cameras and no clips are involved.

The ONNX is exported with dynamic batch (see scripts/fetch_models.sh), so the
single engine built at --batch 4 serves any camera count 1..4. --batch matters
anyway: TensorRT optimizes for the profile it is given, and the paper's runs all
used batch 4.

Usage:
    scripts/build_engine.py --config config/pgie_yolo11m.txt
    scripts/build_engine.py --config config/pgie_yolo11x.txt --batch 4

See scripts/build_engines.sh to do all five at once.
"""
from __future__ import annotations

import argparse
import os
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(config_path: str, batch: int, width: int, height: int) -> int:
    """Run videotestsrc -> nvstreammux -> nvinfer -> fakesink to force a build."""
    Gst.init(None)
    config_abs = os.path.abspath(config_path)
    if not os.path.isfile(config_abs):
        print(f"[build_engine] config not found: {config_abs}", file=sys.stderr)
        return 2

    pipeline = Gst.Pipeline.new("build-engine")
    mux = Gst.ElementFactory.make("nvstreammux", "mux")
    if mux is None:
        print("[build_engine] nvstreammux missing — is DeepStream installed and "
              "on GST_PLUGIN_PATH?", file=sys.stderr)
        return 2
    mux.set_property("batch-size", batch)
    mux.set_property("width", width)
    mux.set_property("height", height)
    mux.set_property("batched-push-timeout", 33333)
    pgie = Gst.ElementFactory.make("nvinfer", "pgie")
    pgie.set_property("config-file-path", config_abs)
    pgie.set_property("batch-size", batch)
    sink = Gst.ElementFactory.make("fakesink", "sink")
    sink.set_property("sync", 0)
    for e in (mux, pgie, sink):
        pipeline.add(e)
    mux.link(pgie)
    pgie.link(sink)

    for i in range(batch):
        src = Gst.ElementFactory.make("videotestsrc", f"src{i}")
        src.set_property("num-buffers", 10)
        src.set_property("is-live", 0)
        cf = Gst.ElementFactory.make("capsfilter", f"cf{i}")
        cf.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,width={width},height={height},framerate=30/1"))
        conv = Gst.ElementFactory.make("nvvideoconvert", f"conv{i}")
        ncf = Gst.ElementFactory.make("capsfilter", f"ncf{i}")
        ncf.set_property("caps", Gst.Caps.from_string(
            "video/x-raw(memory:NVMM),format=NV12"))
        for e in (src, cf, conv, ncf):
            pipeline.add(e)
        src.link(cf)
        cf.link(conv)
        conv.link(ncf)
        # request_pad_simple is 1.20+; get_request_pad is the deprecated name
        # still present on older GStreamer. JetPack 6.2 ships 1.20.3.
        req = getattr(mux, "request_pad_simple", None) or mux.get_request_pad
        ncf.get_static_pad("src").link(req(f"sink_{i}"))

    loop = GLib.MainLoop()
    status = {"error": None}

    def on_msg(_bus, msg, _loop):
        if msg.type == Gst.MessageType.EOS:
            loop.quit()
        elif msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            status["error"] = f"{err} | {dbg}"
            loop.quit()
        return True

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_msg, loop)

    print(f"[build_engine] {os.path.basename(config_abs)}: building engine "
          f"(batch={batch}); this can take 10+ min (yolo11x ~37 min) ...",
          flush=True)
    pipeline.set_state(Gst.State.PLAYING)
    loop.run()
    pipeline.set_state(Gst.State.NULL)
    if status["error"]:
        print(f"[build_engine] ERROR: {status['error']}", file=sys.stderr)
        return 1
    print("[build_engine] done — engine serialized and cached.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Pre-build a YOLO11 TensorRT engine (no cameras needed).")
    p.add_argument("--config",
                   default=os.path.join(_REPO_ROOT, "config", "pgie_yolo11m.txt"),
                   help="nvinfer config to build for (default: pgie_yolo11m.txt, "
                        "the paper's primary operating point)")
    p.add_argument("--batch", type=int, default=4,
                   help="batch to build the profile for (default 4 = the paper's "
                        "4 cameras)")
    p.add_argument("--width", type=int, default=640,
                   help="mux input width (default 640, the C920 capture width)")
    p.add_argument("--height", type=int, default=480,
                   help="mux input height (default 480)")
    args = p.parse_args()
    return build(args.config, args.batch, args.width, args.height)


if __name__ == "__main__":
    raise SystemExit(main())
