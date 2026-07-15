/* main.cpp — the smallest COMPLETE DeepStream pipeline that VISTA can drive.
 *
 * This is a drop-in template, not a demo: every element name, property and
 * teardown step here exists to satisfy a specific requirement of
 * vista::Scheduler::attach(). Each one is marked with the requirement or
 * obligation it discharges — see vista/README.md for the full contract.
 *
 *   filesrc -> qtdemux -> h264parse -> nvv4l2decoder -> nvvideoconvert
 *     |__ all inside bin "source-bin-<i>", exposing ghost src pad "src"
 *   -> nvstreammux "stream-muxer" -> nvinfer "primary-inference"
 *   -> nvtracker "tracker" -> fakesink
 *
 * It replays clips instead of opening cameras so that anyone can run it without
 * our hardware. VISTA cannot tell the difference: it schedules on local
 * CLOCK_MONOTONIC arrival stamps and never reads PTS.
 *
 * NOTE ON WHAT THIS DOES *NOT* DO: it does not reproduce the paper's numbers.
 * There is no per-frame arrival stamping, no detection dump and no live
 * capture, so latency here is replay latency. It is a correctness and
 * integration harness — it proves the contract holds and the ledger closes.
 *
 * Build:  make            (see also CMakeLists.txt)
 * Run:    ./minimal_pipeline --clips ./clips --cams 4 --k 2 --mode fresh
 */
#include <gst/gst.h>

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>

#include "vista/vista_scheduler.hpp"

namespace {

struct Args {
  std::string clips = "./clips";
  std::string pgie = "minimal_pgie.txt";
  std::string mux_ini = "../../config/mux_vista.txt";
  std::string csv;  // empty = no decision log
  std::string mode = "fresh";
  int cams = 4;
  int k = 2;
  int stash = 1;
  double duration = 30.0;
};

const char* kUsage =
    "usage: minimal_pipeline [options]\n"
    "  --clips DIR     directory holding cam0.mp4 .. cam<N-1>.mp4 (default ./clips)\n"
    "  --cams N        number of cameras to replay (default 4)\n"
    "  --k K           frames released per service == mux batch-size (default 2)\n"
    "  --mode M        off | fresh | imp | salvage (default fresh)\n"
    "  --stash S       fresh frames kept per camera (default 1)\n"
    "  --duration S    seconds to run, 0 = until EOS (default 30)\n"
    "  --csv FILE      write the per-decision audit CSV (default: none)\n"
    "  --pgie FILE     nvinfer config (default minimal_pgie.txt)\n"
    "  --mux-ini FILE  nvstreammux INI (default ../../config/mux_vista.txt)\n";

Args parse_args(int argc, char** argv) {
  Args a;
  for (int i = 1; i < argc; ++i) {
    const std::string f = argv[i];
    auto next = [&]() -> std::string {
      if (i + 1 >= argc) throw std::runtime_error("missing value for " + f);
      return argv[++i];
    };
    if (f == "--clips") a.clips = next();
    else if (f == "--cams") a.cams = std::stoi(next());
    else if (f == "--k") a.k = std::stoi(next());
    else if (f == "--mode") a.mode = next();
    else if (f == "--stash") a.stash = std::stoi(next());
    else if (f == "--duration") a.duration = std::stod(next());
    else if (f == "--csv") a.csv = next();
    else if (f == "--pgie") a.pgie = next();
    else if (f == "--mux-ini") a.mux_ini = next();
    else if (f == "-h" || f == "--help") { std::fputs(kUsage, stdout); std::exit(0); }
    else throw std::runtime_error("unknown flag: " + f + "\n" + kUsage);
  }
  if (a.cams < 1) throw std::runtime_error("--cams must be >= 1");
  return a;
}

GstElement* make(const char* factory, const std::string& name) {
  GstElement* e = gst_element_factory_make(factory, name.c_str());
  if (e == nullptr)
    throw std::runtime_error(std::string("failed to create '") + factory +
                             "'. Is the DeepStream plugin path set? "
                             "(gst-inspect-1.0 " + factory + ")");
  return e;
}

/* qtdemux exposes its video pad only once it has parsed the moov atom. */
void on_demux_pad_added(GstElement*, GstPad* pad, gpointer user_data) {
  auto* parser = static_cast<GstElement*>(user_data);
  GstPad* sink = gst_element_get_static_pad(parser, "sink");
  if (sink != nullptr && !gst_pad_is_linked(sink)) gst_pad_link(pad, sink);
  if (sink != nullptr) gst_object_unref(sink);
}

/* REQUIREMENT 1 of attach(): one bin per camera, named <prefix><i> (default
 * "source-bin-<i>"), sitting upstream of the mux and exposing a STATIC src pad
 * named "src". VISTA puts its arrival probe on that pad and returns
 * GST_PAD_PROBE_DROP, which is why the pad must be the bin's only outlet: every
 * frame of camera i has to pass through it, or VISTA cannot account for it.
 * A ghost pad is a static pad, so this satisfies the requirement. */
GstElement* build_source_bin(int i, const Args& a) {
  const std::string idx = std::to_string(i);
  const std::string file = a.clips + "/cam" + idx + ".mp4";
  GstElement* bin = gst_bin_new(("source-bin-" + idx).c_str());

  GstElement* src = make("filesrc", "cam-src-" + idx);
  g_object_set(src, "location", file.c_str(), nullptr);
  GstElement* demux = make("qtdemux", "cam-demux-" + idx);
  GstElement* parse = make("h264parse", "cam-parse-" + idx);
  GstElement* dec = make("nvv4l2decoder", "cam-dec-" + idx);
  /* Pace each clip to its own timestamps, so frames arrive at ~30 fps as they
   * would from a camera. Without this, filesrc delivers the whole clip as fast
   * as it decodes and there is no oversubscription to schedule. */
  GstElement* pace = make("identity", "cam-pace-" + idx);
  g_object_set(pace, "sync", TRUE, nullptr);
  GstElement* conv = make("nvvideoconvert", "cam-conv-" + idx);

  /* HOST OBLIGATION 4: output-buffers >= 12 on the nvvideoconvert feeding the
   * mux. VISTA holds frames in its stash and releases them later, so buffers
   * live longer than in a stock pipeline. At the default pool size the pool —
   * not VISTA — becomes the throttle: the pool starves, upstream stalls, and
   * the drop decision silently moves back into the transport, which is the
   * exact thing VISTA exists to take control of. */
  g_object_set(conv, "output-buffers", 12u, nullptr);

  GstElement* caps = make("capsfilter", "cam-caps-" + idx);
  GstCaps* c = gst_caps_from_string("video/x-raw(memory:NVMM),format=NV12");
  g_object_set(caps, "caps", c, nullptr);
  gst_caps_unref(c);

  gst_bin_add_many(GST_BIN(bin), src, demux, parse, dec, pace, conv, caps, nullptr);
  if (!gst_element_link(src, demux))
    throw std::runtime_error("cam " + idx + ": filesrc -> qtdemux link failed");
  if (!gst_element_link_many(parse, dec, pace, conv, caps, nullptr))
    throw std::runtime_error("cam " + idx + ": decode chain link failed");
  g_signal_connect(demux, "pad-added", G_CALLBACK(on_demux_pad_added), parse);

  GstPad* target = gst_element_get_static_pad(caps, "src");
  GstPad* ghost = gst_ghost_pad_new("src", target);
  gst_pad_set_active(ghost, TRUE);
  gst_element_add_pad(bin, ghost);
  gst_object_unref(target);
  return bin;
}

struct LoopCtx {
  GMainLoop* loop = nullptr;
  int exit_code = 0;
};

gboolean bus_call(GstBus*, GstMessage* msg, gpointer user_data) {
  auto* ctx = static_cast<LoopCtx*>(user_data);
  switch (GST_MESSAGE_TYPE(msg)) {
    case GST_MESSAGE_EOS:
      std::fprintf(stderr, "[app] EOS\n");
      g_main_loop_quit(ctx->loop);
      break;
    case GST_MESSAGE_ERROR: {
      GError* err = nullptr;
      gchar* dbg = nullptr;
      gst_message_parse_error(msg, &err, &dbg);
      std::fprintf(stderr, "[app] ERROR from %s: %s\n",
                   GST_OBJECT_NAME(msg->src), err->message);
      if (dbg != nullptr) std::fprintf(stderr, "[app] debug: %s\n", dbg);
      g_error_free(err);
      g_free(dbg);
      ctx->exit_code = 1;
      g_main_loop_quit(ctx->loop);
      break;
    }
    default:
      break;
  }
  return TRUE;
}

gboolean on_duration(gpointer user_data) {
  auto* ctx = static_cast<LoopCtx*>(user_data);
  std::fprintf(stderr, "[app] duration elapsed\n");
  g_main_loop_quit(ctx->loop);
  return G_SOURCE_REMOVE;
}

int run(const Args& a) {
  vista::SchedCfg cfg;
  cfg.mode = a.mode;
  cfg.k = a.k;
  cfg.stash = a.stash;
  cfg.decision_csv = a.csv;
  // Defaults already match this pipeline's element names; shown for clarity.
  cfg.source_bin_prefix = "source-bin-";
  cfg.mux_name = "stream-muxer";
  cfg.pgie_name = "primary-inference";
  cfg.tracker_name = "tracker";

  /* mode=off means "probes never attached". attach() does NOT check this for
   * you — it wires probes unconditionally — so honouring `off` is the HOST's
   * job: construct no Scheduler at all. That is what makes an off run
   * bit-identical to a stock pipeline rather than merely similar. */
  const bool sched_on = cfg.enabled();

  /* With VISTA driving, batch-size is K (a release must land as ONE batch).
   * With mode=off there are no releases, so the stock choice is the camera
   * count. NB: an off run here is a convenience A/B smoke test, NOT the
   * paper's Stock-Default baseline — that baseline has its own mux INI. */
  const int batch = sched_on ? a.k : a.cams;

  GstElement* pipeline = gst_pipeline_new("vista-minimal");
  for (int i = 0; i < a.cams; ++i)
    gst_bin_add(GST_BIN(pipeline), build_source_bin(i, a));

  GstElement* mux = make("nvstreammux", "stream-muxer");
  /* HOST OBLIGATION 1: mux batch-size == k. Enforced by a strict check that
   * THROWS from attach() (batch-size is property-readable). */
  g_object_set(mux, "batch-size", static_cast<guint>(batch), nullptr);
  /* HOST OBLIGATION 2: the mux INI. NOT property-readable, so it cannot be
   * checked at attach() — it is enforced at RUNTIME by the batch-atomicity
   * gate, which warns if <90% of completed batches carry exactly k frames.
   * A wrong INI degrades silently; see config/mux_vista.txt. */
  if (sched_on && !a.mux_ini.empty())
    g_object_set(mux, "config-file-path", a.mux_ini.c_str(), nullptr);
  /* HOST OBLIGATION 5: sync-inputs=0. Also strict-checked (throws). VISTA
   * replaces timestamp alignment with local arrival-clock scheduling. */
  g_object_set(mux, "sync-inputs", FALSE, nullptr);

  GstElement* pgie = make("nvinfer", "primary-inference");
  g_object_set(pgie, "config-file-path", a.pgie.c_str(), nullptr);
  /* HOST OBLIGATION 3: nvinfer batch-size == k. Strict-checked (throws).
   * Set AFTER config-file-path: the config file also carries a batch-size and
   * the last writer wins. */
  g_object_set(pgie, "batch-size", static_cast<guint>(batch), nullptr);

  /* REQUIREMENT 2 of attach(): an element downstream of nvinfer whose buffers
   * carry NvDsBatchMeta. That is the completion clock — VISTA reads only
   * num_frames_in_batch. nvtracker is used here because the paper's pipeline
   * has one; any downstream element works (SchedCfg::tracker_name). */
  GstElement* trk = make("nvtracker", "tracker");
  const char* ds_root = std::getenv("DS_ROOT");
  const std::string ds = ds_root != nullptr ? ds_root
                                            : "/opt/nvidia/deepstream/deepstream";
  g_object_set(trk, "ll-lib-file",
               (ds + "/lib/libnvds_nvmultiobjecttracker.so").c_str(),
               "ll-config-file",
               (ds + "/samples/configs/deepstream-app/config_tracker_NvSORT.yml")
                   .c_str(),
               "tracker-width", 640u, "tracker-height", 384u, nullptr);

  GstElement* sink = make("fakesink", "sink");
  g_object_set(sink, "sync", FALSE, "async", FALSE, nullptr);

  gst_bin_add_many(GST_BIN(pipeline), mux, pgie, trk, sink, nullptr);
  if (!gst_element_link_many(mux, pgie, trk, sink, nullptr))
    throw std::runtime_error("mux -> pgie -> tracker -> sink link failed");

  // Link each source bin's ghost src pad to a mux request sink pad.
  for (int i = 0; i < a.cams; ++i) {
    const std::string idx = std::to_string(i);
    GstElement* bin =
        gst_bin_get_by_name(GST_BIN(pipeline), ("source-bin-" + idx).c_str());
    GstPad* srcpad = gst_element_get_static_pad(bin, "src");
    GstPad* muxpad = gst_element_request_pad_simple(mux, ("sink_" + idx).c_str());
    if (muxpad == nullptr || gst_pad_link(srcpad, muxpad) != GST_PAD_LINK_OK)
      throw std::runtime_error("source-bin-" + idx + " -> mux link failed");
    gst_object_unref(srcpad);
    gst_object_unref(muxpad);
    gst_object_unref(bin);
  }

  /* REQUIREMENT 3 of attach(): attach BEFORE PLAYING, and AFTER any probe of
   * yours that must stamp arrivals first (probes on a pad fire in the order
   * they were added, and VISTA's arrival probe returns DROP). */
  std::unique_ptr<vista::Scheduler> sched;
  if (sched_on) {
    sched.reset(new vista::Scheduler(cfg, a.cams));
    sched->attach(pipeline);
  } else {
    std::fprintf(stderr, "[app] mode=off: no scheduler attached (stock path)\n");
  }

  LoopCtx ctx;
  ctx.loop = g_main_loop_new(nullptr, FALSE);
  GstBus* bus = gst_element_get_bus(pipeline);
  const guint bus_watch = gst_bus_add_watch(bus, bus_call, &ctx);
  gst_object_unref(bus);
  guint dur_watch = 0;
  if (a.duration > 0.0)
    dur_watch = g_timeout_add(static_cast<guint>(a.duration * 1000.0),
                              on_duration, &ctx);

  if (gst_element_set_state(pipeline, GST_STATE_PLAYING) ==
      GST_STATE_CHANGE_FAILURE) {
    gst_element_set_state(pipeline, GST_STATE_NULL);
    gst_object_unref(pipeline);
    g_main_loop_unref(ctx.loop);
    throw std::runtime_error("failed to set pipeline to PLAYING");
  }
  std::fprintf(stderr,
               "[app] running. The first launch may build the TensorRT engine "
               "(minutes).\n");
  g_main_loop_run(ctx.loop);

  if (dur_watch != 0) g_source_remove(dur_watch);
  g_source_remove(bus_watch);

  /* ---- TEARDOWN. The order is load-bearing; do not rearrange. ----
   * 1. request_stop()  — the release thread may be blocked inside
   *                      gst_pad_push; this tells it to stop looping.
   * 2. set_state(NULL) — flushes the pads, which unblocks that push. Doing
   *                      this BEFORE step 1 can deadlock; doing it after
   *                      step 3 means join() waits on a blocked push.
   * 3. join_and_cleanup() — joins the thread, unrefs stashed buffers.
   * 4. read stats / print_summary() — before the pipeline dies.
   * 5. gst_object_unref(pipeline) — LAST: the stashed buffers released in
   *                      step 3 belong to this pipeline's buffer pools. */
  if (sched) sched->request_stop();                       // 1
  gst_element_set_state(pipeline, GST_STATE_NULL);        // 2
  int rc = ctx.exit_code;
  if (sched) {
    sched->join_and_cleanup();                            // 3
    sched->print_summary();                               // 4

    /* The paper's ledger invariant, asserted: every arrived frame is either
     * admitted (fresh or salvage) or explicitly counted as a policy drop.
     * Nothing is lost silently. (Build without -DNDEBUG or this vanishes.) */
    auto st = sched->stats();
    assert(st.ledger_closes());
    std::fprintf(stderr,
                 "[app] ledger: %ld arrivals = %ld fresh + %ld salvage + %ld "
                 "drops -> %s\n",
                 st.arrivals, st.admitted_fresh, st.admitted_salvage,
                 st.policy_drops, st.ledger_closes() ? "CLOSES" : "BROKEN");
    if (!st.ledger_closes()) rc = 1;  // survives -DNDEBUG
  }
  gst_object_unref(pipeline);                             // 5
  g_main_loop_unref(ctx.loop);
  return rc;
}

}  // namespace

int main(int argc, char** argv) {
  /* MUST precede gst_init: this env var decides which nvstreammux
   * implementation the plugin registers, and it is read at registration.
   * VISTA requires the NEW mux — the legacy one has no INI and batches on a
   * timeout it will not surrender. overwrite=0 so an explicit setting wins. */
  setenv("USE_NEW_NVSTREAMMUX", "yes", 0);
  try {
    const Args a = parse_args(argc, argv);
    gst_init(&argc, &argv);
    return run(a);
  } catch (const std::exception& e) {
    std::fprintf(stderr, "[app] ERROR: %s\n", e.what());
    return 2;
  }
}
