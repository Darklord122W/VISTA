# Building

Two things can be built from this repository:

1. **The VISTA module** (`vista/`) — the scheduler itself, ~670 lines, one
   header + one translation unit. This is what you link into your own
   pipeline.
2. **The reference application** (`app/`) — the multi-camera DeepStream
   detection/tracking app that produced every number in the paper. Only needed
   to reproduce the paper or to run the scheduler end to end.

Neither builds a model. See
[`02-models-and-engines.md`](02-models-and-engines.md).

---

## Requirements

The reference machine, and the versions every number in this repository was
produced on. All of these were verified on that machine at the time of
writing:

| Component | Version | How to check |
|---|---|---|
| Board | Jetson AGX Orin 64 GB, 30 W, GPU locked 612 MHz | `nvpmodel -q`, `jetson_clocks --show` |
| L4T / JetPack | R36.5.0 → **JetPack 6.2** | `cat /etc/nv_tegra_release` |
| DeepStream | **7.1** | `cat /opt/nvidia/deepstream/deepstream/version` |
| GStreamer | **1.20.3** | `pkg-config --modversion gstreamer-1.0` |
| yaml-cpp | **0.7.0** | `pkg-config --modversion yaml-cpp` |
| g++ | **11.4.0** | `g++ --version` |
| CUDA | 12.6 | `nvcc --version` |
| TensorRT | 10.3.0 | `python3 -c "import tensorrt; print(tensorrt.__version__)"` |

Everything above ships on a stock JetPack 6.2 + DeepStream 7.1 image. The C++
app needs no Python bindings (`pyds`); the analysis scripts are separate.

**Other platforms.** Nothing in `vista/` is Jetson-specific — it needs
GStreamer, the DeepStream metadata headers, and a C++17 compiler. It has not
been built anywhere else, so treat "should work on x86 DeepStream 7.1" as
untested rather than supported. The *engines* are another matter entirely and
genuinely are not portable — see
[`02-models-and-engines.md`](02-models-and-engines.md).

---

## Building the VISTA module

`vista/` has no build system of its own on purpose: it is two files you add to
your project. This is the compile line, verified on the reference machine
(clean, zero warnings, ~4 s):

```bash
g++ -std=c++17 -O2 -Wall -Wextra -c vista/src/vista_scheduler.cpp \
    -o vista_scheduler.o \
    -Ivista/include \
    -I/opt/nvidia/deepstream/deepstream/sources/includes \
    $(pkg-config --cflags gstreamer-1.0)
```

and this is the link line:

```bash
g++ -o your_app your_objs... vista_scheduler.o \
    $(pkg-config --libs gstreamer-1.0) \
    -L/opt/nvidia/deepstream/deepstream/lib -lnvdsgst_meta -lnvds_meta \
    -Wl,-rpath,/opt/nvidia/deepstream/deepstream/lib \
    -lpthread
```

What each piece is for:

| Flag | Why |
|---|---|
| `-Ivista/include` | The header is included as `"vista/vista_scheduler.hpp"`. |
| `-I$(DS_ROOT)/sources/includes` | `gstnvdsmeta.h` / `nvdsmeta.h` — the batch metadata VISTA reads at the completion probe. |
| `pkg-config gstreamer-1.0` | Pulls GStreamer **and** GLib (`g_get_monotonic_time`). |
| `-lnvdsgst_meta -lnvds_meta` | `gst_buffer_get_nvds_batch_meta()` and the meta accessors. |
| `-Wl,-rpath,$(DS_ROOT)/lib` | Bakes the DeepStream library path into the binary, so **no `LD_LIBRARY_PATH` is needed at runtime**. Do this; the alternative is an environment that has to be right on every machine that runs it. |
| `-lpthread` | `std::thread` and `pthread_setname_np` (the release thread names itself `vista-sched`, which is what makes its CPU cost visible in `/proc`). |

`-Wall -Wextra` is not decoration: the module is expected to compile clean under
both. If it does not on your toolchain, that is a portability report worth
filing.

---

## Building the reference application

```bash
cd app
make            # -> ./app/vista_multicam
make clean      # remove objects, dependency files and the binary
```

**The app builds the scheduler from source, not from a library.** There is no
installed `.so` and no copy of the scheduler under `app/src/`: the Makefile
compiles `../vista/src/vista_scheduler.cpp` into `app/build/` and links it. So
`vista/` stays self-contained and knows nothing about the app; the only
coupling is the public header. Editing `vista/` and running `make` in `app/`
rebuilds exactly what changed, and an app build never writes into `vista/`.

The Makefile is deliberately small. Three things about it are worth knowing:

### `DS_ROOT`

```makefile
DS_ROOT ?= /opt/nvidia/deepstream/deepstream
```

`?=` means the environment wins. If DeepStream is installed elsewhere, or you
want to build against a specific version rather than the `deepstream` symlink:

```bash
make DS_ROOT=/opt/nvidia/deepstream/deepstream-7.1
```

That one variable controls the include path (`$(DS_ROOT)/sources/includes`), the
library path (`-L$(DS_ROOT)/lib`) and the rpath. There is no other hardcoded
DeepStream location in the build.

### rpath, not `LD_LIBRARY_PATH`

```makefile
LDLIBS += ... -L$(DS_ROOT)/lib -lnvdsgst_meta -lnvds_meta -Wl,-rpath,$(DS_ROOT)/lib
```

The binary carries the DeepStream library path. You do not need
`LD_LIBRARY_PATH` set to run it, and you should not set one — an
`LD_LIBRARY_PATH` pointing at a *different* DeepStream would silently win over
the rpath.

Verify with:

```bash
ldd app/vista_multicam | grep nvds
```

Every `libnvds*` should resolve under `$DS_ROOT/lib` with no environment set.

### Incremental builds

The Makefile emits `-MMD -MP` dependency files, so editing a header rebuilds
exactly the objects that include it. After pulling changes that touch the build
itself, `make clean && make`.

---

## Checking the build

```bash
./app/vista_multicam --help
```

prints the full flag list ([`03-cli-reference.md`](03-cli-reference.md)) and
exits 0. This needs no cameras, no clips, no model, and no engine — it is the
fastest confirmation that the binary links and runs.

Next: a replay smoke test, which needs clips and an engine — see
[`02-models-and-engines.md`](02-models-and-engines.md) and
[`03-cli-reference.md`](03-cli-reference.md).

---

## Known build-time gotchas

- **`USE_NEW_NVSTREAMMUX` set in your shell.** The app sets it to `yes` itself
  with `overwrite=0`, so an explicit setting in your environment **wins**. If
  you have it set to anything else, the legacy mux loads and the app hard-errors
  at pipeline build (`The LEGACY nvstreammux was loaded, but this app is written
  for the NEW mux…`). `unset USE_NEW_NVSTREAMMUX`.
- **A stale `LD_LIBRARY_PATH`** pointing at another DeepStream will override the
  rpath. Unset it.
- **yaml-cpp missing.** `pkg-config --modversion yaml-cpp` failing means
  `libyaml-cpp-dev` is not installed. Only the app needs it; the VISTA module
  does not.
- **First run is not a build problem.** `[main] Running. First launch may build
  the TensorRT engine (several minutes).` is the engine build, not a hang. See
  [`02-models-and-engines.md`](02-models-and-engines.md).
