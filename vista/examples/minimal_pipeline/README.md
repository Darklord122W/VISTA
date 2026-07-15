# minimal_pipeline — the VISTA integration template

The smallest complete DeepStream pipeline VISTA can drive. It is a **template**,
not a demo: every element name, property and teardown step exists to discharge a
specific requirement of `vista::Scheduler::attach()`, and each is commented with
the requirement it satisfies. Copy `main.cpp` and replace the source bins with
your own.

```
filesrc -> qtdemux -> h264parse -> nvv4l2decoder -> identity(sync) -> nvvideoconvert
  |__ inside bin "source-bin-<i>", exposing ghost src pad "src"     (output-buffers=12)
-> nvstreammux "stream-muxer"  (batch-size=k, config-file-path=mux_vista.txt, sync-inputs=0)
-> nvinfer     "primary-inference" (batch-size=k)
-> nvtracker   "tracker"       (the completion clock)
-> fakesink
```

It replays clips rather than opening cameras, so anyone can run it without our
hardware. VISTA cannot tell the difference: it schedules on local
`CLOCK_MONOTONIC` arrival stamps and never reads PTS.

## What this does and does not show

It proves the integration contract holds and that **the ledger closes** —
`assert(st.ledger_closes())` runs on every invocation.

It does **not** reproduce the paper's numbers. There is no arrival stamping, no
detection dump and no live capture; the latencies here are replay latencies. For
the paper's measurements see the campaign harness at the repository root.

## Build

```bash
make                    # -> ./minimal_pipeline
```

Verified command and result on a Jetson AGX Orin (DeepStream 7.1, g++ 11.4):

```
$ cd vista/examples/minimal_pipeline && make
g++ -std=c++17 -O2 -Wall -Wextra -I../../include \
    -I/opt/nvidia/deepstream/deepstream/sources/includes ... -c -o main.o main.cpp
g++ -std=c++17 -O2 -Wall -Wextra ... -c -o vista_scheduler.o ../../src/vista_scheduler.cpp
g++ -o minimal_pipeline main.o vista_scheduler.o -lgstreamer-1.0 -lgobject-2.0 -lglib-2.0 \
    -L/opt/nvidia/deepstream/deepstream/lib -lnvdsgst_meta -lnvds_meta \
    -Wl,-rpath,/opt/nvidia/deepstream/deepstream/lib -lpthread
```

Zero warnings, exit 0. CMake works too:

```bash
cmake -B build && cmake --build build                       # in-tree
cmake -B build -DUSE_INSTALLED_VISTA=ON \
      -DCMAKE_PREFIX_PATH=/your/prefix && cmake --build build
```

`DS_ROOT` defaults to `/opt/nvidia/deepstream/deepstream`; override via the
environment (`DS_ROOT=... make`) or `-DDS_ROOT=...`.

The Makefile deliberately does **not** define `NDEBUG`: that would delete the
`assert(st.ledger_closes())` this example exists to run. (`main.cpp` also
returns a non-zero exit code on a broken ledger, so the check survives `NDEBUG`
— but the assert should be what fires.)

## Prerequisites

**Clips.** A directory with `cam0.mp4 .. cam<N-1>.mp4`, H.264 in MP4. Any
footage works. None ships with this repository — the paper's footage contains an
identifiable person and is withheld for privacy.

**Model.** `minimal_pgie.txt` points at `<repo>/models/`, which does not ship:
weights are ~6.4 GB, and TensorRT engines are specific to the exact GPU, driver,
TensorRT and DeepStream version that built them, so a shipped engine would be
silently wrong elsewhere. Populate `models/` with the repository's download +
`build_engine` recipes first. On the first launch with no prebuilt engine,
nvinfer builds one and that takes minutes — the app warns.

## Run

```bash
./minimal_pipeline --clips ./clips --cams 4 --k 2 --mode fresh --duration 30
```

| flag | default | meaning |
|---|---|---|
| `--clips DIR` | `./clips` | directory of `cam<i>.mp4` |
| `--cams N` | 4 | cameras to replay |
| `--k K` | 2 | frames per release == mux batch-size |
| `--mode M` | `fresh` | `off` \| `fresh` \| `imp` \| `salvage` |
| `--stash S` | 1 | fresh frames kept per camera (use `>= depth` with `imp`) |
| `--duration S` | 30 | seconds; 0 = run to EOS |
| `--csv FILE` | none | per-decision audit CSV |
| `--pgie FILE` | `minimal_pgie.txt` | nvinfer config |
| `--mux-ini FILE` | `../../config/mux_vista.txt` | nvstreammux INI |

`--mode off` constructs **no scheduler at all** (which is what makes `off`
bit-identical to a stock pipeline) and sets `batch-size` to the camera count.
That is a convenience A/B smoke test, **not** the paper's Stock-Default
baseline, which has its own mux INI.

## Verified output

A real 17 s run on the paper's hardware (4 clips, YOLO11n FP16, `strict` on):

```
[vista] NOTE: mux 'stream-muxer' reports batch-size=4 with k=2. On DS 7.1 the new
        mux reports the sink-pad count until the INI is read at the state change...
[vista] mode=fresh k=2 depth=2 stash=1 tau_max=150ms tau_salvage=250ms w=(0.40,0.35,0.25)
[vista] fresh: 696 releases (40.3/s), 1392 fresh + 0 salvage admitted, 588 policy drops,
        s_hat 46.4 ms over 17.3 s.
[app] ledger: 1980 arrivals = 1392 fresh + 0 salvage + 588 drops -> CLOSES
```

1392 admits / 696 releases = exactly 2.0 frames per release — the K-burst is
landing as one batch, which is the whole point of `mux_vista.txt`. The
`batch-size=4` NOTE is expected on 4 cameras and is explained in
[`../../README.md`](../../README.md) §2; the atomicity gate stays silent, which
is the evidence that the mux really is batching k=2.
