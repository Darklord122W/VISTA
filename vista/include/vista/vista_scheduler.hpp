/* vista_scheduler.hpp — VISTA: Value-driven Inference Scheduling for Timely
 * Autonomous multi-camera perception.
 *
 * A completion-clocked load-shedding scheduler for droppable multi-camera
 * frames. When a batched detector's service time S exceeds the camera frame
 * period T, the system is oversubscribed (load ratio rho = S/T > 1) and a
 * fraction 1 - 1/rho of all frames CANNOT be processed. A stock pipeline
 * leaves that decision to transport backpressure, which makes it badly and
 * invisibly: the capture ring silently overwrites its NEWEST arrivals upstream
 * of all instrumentation, and the survivors emerge through a standing queue
 * hundreds of milliseconds stale.
 *
 * VISTA makes the drop decision explicit. It owns a bounded per-camera stash
 * upstream of nvstreammux and, at each inference completion, admits the K most
 * valuable frames into the next batch:
 *
 *   v(f) = w_fresh * fresh(f) + w_imp * imp(cam f) + w_fair * fair(cam f)
 *
 *   fresh(f) = max(0, 1 - age(f) / tau_max)
 *   fair(c)  = min(1, (t_now - t_served(c)) / D_fair)
 *
 * with a hard staleness bound (tau_max), a hard per-camera service-interval
 * bound (D_hard force-admission), and every drop counted. The ledger closes
 * exactly: arrivals == admitted_fresh + admitted_salvage + policy_drops.
 *
 * PORTABILITY. VISTA needs only three things, and none of them are
 * DeepStream-specific (see docs/integration/04-porting-checklist.md):
 *   (i)   an interception point before the shared batcher,
 *   (ii)  an inference-completion signal,
 *   (iii) a monotonic local clock.
 * Nothing depends on camera timestamps, camera synchronization, or the
 * batcher's internals. All ages come from local CLOCK_MONOTONIC arrival
 * stamps — never from PTS, which commodity USB capture paths fabricate.
 *
 * MECHANISM (DeepStream binding). A BUFFER probe on every source bin's ghost
 * src pad refs + stashes each arriving frame and returns GST_PAD_PROBE_DROP; a
 * dedicated scheduler thread releases exactly K frames per service via
 * gst_pad_push on the same pads (a thread_local re-entrancy guard lets its own
 * pushes back through the probe). The mux runs with batch-size = K and a slow
 * INI deadline, so the K-burst completes as ONE batch, immediately.
 * in_flight is counted in FRAMES (release += K, completion -= frames) and
 * gates the next release at (depth-1)*K — GPU-clocked, work-conserving, no
 * queue growth.
 *
 * MODES:
 *   off      probes never attached — bit-identical to the stock pipeline
 *   fresh    w_imp forced to 0. VISTA-Fresh: the paper's general-purpose
 *            default (freshness + fairness)
 *   imp      adds the activity term. VISTA-Activity: the paper's OPTIONAL
 *            extension — pays only under demonstrable camera-activity skew,
 *            and only when stash >= depth (see SchedCfg::stash)
 *   salvage  imp + held slots. NOT EVALUATED IN THE PAPER; see
 *            docs/usage/06-tuning.md before using it.
 *
 * Paper: "VISTA: Value-Driven Inference Scheduling for Timely Autonomous
 * Multi-Camera Perception". The defaults below are the paper's defaults; a
 * default-constructed SchedCfg with mode="fresh" is exactly VISTA-Fresh at the
 * paper's operating point (K=2, d=2, stash=1, tau_max=150ms).
 */
#pragma once

#include <gst/gst.h>

#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace vista {

/* Run counters. The ledger invariant the paper claims is checkable here:
 *   arrivals == admitted_fresh + admitted_salvage + policy_drops
 * (see vista/examples/minimal_pipeline/main.cpp, which asserts it). */
struct Stats {
  long arrivals = 0;
  long admitted_fresh = 0;
  long admitted_salvage = 0;
  long policy_drops = 0;
  long releases = 0;
  long completions = 0;
  double s_hat_ms = 0.0;   // EWMA batch service time
  double elapsed_s = 0.0;
  std::vector<long> per_cam_admits;
  std::vector<long> per_cam_drops;
  std::map<int, long> fill_hist;  // completed batch fill -> count (gate G1)

  bool ledger_closes() const {
    return arrivals == admitted_fresh + admitted_salvage + policy_drops;
  }
};

struct SchedCfg {
  // ---- policy (paper defaults) -------------------------------------------
  std::string mode = "off";      // off | fresh | imp | salvage
  int k = 2;                     // frames per release (== mux batch-size)
  int depth = 2;                 // release gate: in_flight <= (depth-1)*k
  int stash = 1;                 // fresh frames kept per camera.
                                 // MUST be >= depth for value-driven
                                 // concentration: the `depth` releases of one
                                 // cycle fire back-to-back microseconds apart,
                                 // and a camera can supply at most one frame
                                 // per release from a 1-deep stash — capping
                                 // every camera near its even share no matter
                                 // how high its importance weight. With
                                 // importance OFF, stash 1 is optimal (pure
                                 // keep-newest). See docs/design/04.
  double tau_max_ms = 150.0;     // hard staleness bound for fresh frames
  double tau_salvage_ms = 250.0; // staleness bound for held frames (salvage)
  double w_fresh = 0.40;
  double w_imp = 0.35;
  double w_fair = 0.25;
  double imp_halflife_s = 2.0;   // importance EWMA half-life
  double imp_max = 2.0;          // clip; sized so ~0.7 new tracks/s saturates.
                                 // The v1 default (10, with a "+detections"
                                 // increment) saturated on any scene holding
                                 // standing objects — median score 1.000 on
                                 // every camera — silently turning the
                                 // importance term into a constant. Any signal
                                 // keyed to how MUCH is in frame does the same;
                                 // importance must measure CHANGE.
  double retention_thresh = 0.30;// imp_score >= this at displacement -> held

  // ---- pipeline binding ---------------------------------------------------
  // VISTA finds its probe points by element name. Override these to match your
  // own pipeline's naming; the defaults match the paper's reference app.
  std::string source_bin_prefix = "source-bin-";  // per-camera bins <prefix><i>
  std::string source_pad_name = "src";
  std::string tracker_name = "tracker";     // ANY element downstream of nvinfer
  std::string tracker_pad_name = "src";     // — VISTA only reads batch meta
  std::string mux_name = "stream-muxer";    // "" disables the obligation check
  std::string pgie_name = "primary-inference";  // "" disables the check

  // ---- diagnostics --------------------------------------------------------
  std::string decision_csv;      // "" = no per-decision log (the audit trail)
  bool log_drops = false;        // false = paper-identical: the decision CSV
                                 // records admissions only. Drop rows add I/O
                                 // on the arrival path of a timing-sensitive
                                 // scheduler, so they are opt-in.
  bool strict = true;            // throw on a verifiable misconfiguration
  bool gate_check = true;        // warn loudly if batch atomicity fails

  bool enabled() const { return mode != "off"; }
  bool use_importance() const { return mode == "imp" || mode == "salvage"; }
  bool use_salvage() const { return mode == "salvage"; }
};

class Scheduler {
 public:
  /* Validates cfg and opens the decision CSV. Throws std::runtime_error on an
   * invalid configuration (see docs/integration/05-troubleshooting.md). */
  Scheduler(const SchedCfg& cfg, int num_cams);
  ~Scheduler();

  /* Find the source-bin ghost src pads + the completion element's src pad,
   * attach the arrival/EOS/completion probes, and start the release thread.
   *
   * Call this BEFORE the pipeline goes to PLAYING, and AFTER any probe that
   * must stamp arrivals first (probes fire in attach order — this is why the
   * reference app's e2e_ms includes the stash wait).
   *
   * Throws if a required element is missing, or — when cfg.strict — if a
   * host obligation is verifiably violated (mux/pgie batch-size != k,
   * sync-inputs != 0). See vista/README.md "Host obligations". */
  void attach(GstElement* pipeline);

  /* Two-phase teardown. Order is load-bearing:
   *   1. request_stop()                     (the release thread may be blocked
   *                                          inside gst_pad_push)
   *   2. gst_element_set_state(p, NULL)     (flushes pads; unblocks that push)
   *   3. join_and_cleanup()                 (joins; unrefs stashed buffers)
   *   4. gst_object_unref(pipeline)         (LAST — stashed buffers belong to
   *                                          its pools) */
  void request_stop();
  void join_and_cleanup();

  /* One-line run summary on stderr. Format is stable and parsed by
   * analysis/weightsweep/aggregate_runs.py — see NAMING.md before changing it. */
  void print_summary() const;

  /* Snapshot of the counters. Safe to call at any time. */
  Stats stats() const;

 private:
  struct Slot {                 // one stashed frame
    GstBuffer* buf = nullptr;   // owned ref (nullptr = empty)
    double t_arrival = 0.0;     // CLOCK_MONOTONIC seconds
  };
  struct CamState {
    std::deque<Slot> fresh;     // oldest at front, newest at back;
                                // size <= cfg.stash (arrival displaces front)
    Slot held;                  // salvage slot (mode=salvage only)
    double importance = 0.0;    // EWMA, decayed lazily
    double imp_updated = 0.0;
    double last_served = 0.0;
    long policy_drops = 0;      // displaced/evicted by policy
    long arrivals = 0;
    long admitted_fresh = 0;
    long admitted_held = 0;
    bool eos = false;           // EOS passed through; camera done
    GstPad* pad = nullptr;      // ghost src pad (owned ref)
    bool pad_dead = false;      // push returned EOS/FLUSHING
    std::set<int64_t> seen_ids; // for new-track importance events
  };
  struct ArrivalCtx {
    Scheduler* self;
    int cam;
  };

  static GstPadProbeReturn arrival_probe(GstPad*, GstPadProbeInfo*, gpointer);
  static GstPadProbeReturn event_probe(GstPad*, GstPadProbeInfo*, gpointer);
  static GstPadProbeReturn completion_probe(GstPad*, GstPadProbeInfo*, gpointer);

  void validate_cfg() const;
  void check_obligations(GstElement* pipeline) const;
  void on_arrival(int cam, GstBuffer* buf);
  void on_completion(GstBuffer* buf);
  void thread_main();
  bool release_once();          // returns false when fully drained + all EOS
  void drop_slot(Slot& slot, int cam, const char* why);
  double importance_now(int cam, double now);
  void log_decision(double t, const char* event, int cam, const char* slot,
                    double age_ms, double fresh_s, double imp_s, double fair_s,
                    double value, int released, long in_flight,
                    guint64 buf_pts = 0);

  const SchedCfg cfg_;
  const int num_cams_;

  mutable std::mutex mu_;
  std::condition_variable cv_;
  std::vector<CamState> cams_;
  std::atomic<bool> stop_{false};
  std::atomic<long> in_flight_{0};        // FRAMES released, not yet completed
  double s_hat_ms_ = 50.0;                // EWMA service time per batch
  std::deque<std::pair<double, int>> released_;  // (t_release, k) FIFO
  long releases_ = 0;
  long completions_ = 0;
  long salvage_admits_ = 0;
  double t_start_ = 0.0;
  double last_completion_ = 0.0;          // watchdog
  std::map<int, long> fill_hist_;         // completed batch fill histogram
  bool gate_warned_ = false;

  std::thread thread_;
  std::vector<std::unique_ptr<ArrivalCtx>> arrival_ctxs_;
  std::FILE* dlog_ = nullptr;
  mutable std::mutex dlog_mu_;
};

/* Set while the scheduler thread is inside gst_pad_push, so the arrival and
 * event probes let the scheduler's own traffic through. This is one
 * process-wide thread_local: two Scheduler instances share it, which is benign
 * because each only ever pushes from its own release thread. */
extern thread_local bool t_vista_pushing;

}  // namespace vista
