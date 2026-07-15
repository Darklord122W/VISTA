/* vista_scheduler.cpp — see include/vista/vista_scheduler.hpp.
 *
 * Provenance: this file is the scheduler that produced every number in the
 * VISTA paper, vendored from multicam_perception_rt/cpp/src/scheduler.cpp.
 * The delta vs. the paper binary is enumerated in vista/PAPER_DIFF.md:
 *   1. rename  (SPARQ -> VISTA, namespace mcrt -> vista)
 *   2. parameter injection (element names + 3 ex-compile-time constants)
 *   3. validation (rejects only configurations the paper never used)
 *   4. additive (Stats, the runtime atomicity gate, optional drop rows)
 * The scoring, selection, gating and eviction logic is byte-for-byte the
 * paper's, and the delta is behaviour-neutral on paper configurations with ONE
 * exception, stated here rather than buried: join_and_cleanup() now COUNTS the
 * frames still stashed at teardown as policy drops instead of silently
 * unreffing them (PAPER_DIFF 4a). Without it Stats::ledger_closes() — the
 * paper's central accountability claim — is a coin flip on whether the stashes
 * happen to be empty when the run ends (measured: broken at 12 s, closing at
 * 20 s, same config). The effect is bounded by num_cams*stash frames, lands
 * entirely at teardown, and changes no scheduling decision; its consequence is
 * that the paper binary's reported drop counts undercount by that bound.
 */
#include "vista/vista_scheduler.hpp"

#include <glib.h>
#include <pthread.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <stdexcept>

#include "gstnvdsmeta.h"

namespace vista {

thread_local bool t_vista_pushing = false;

namespace {
double mono_secs() { return g_get_monotonic_time() / 1e6; }

/* Read an integer GObject property, returning `fallback` if the element or
 * property does not exist. Used only for the obligation checks. */
gint get_int_prop(GstElement* pipeline, const std::string& elem_name,
                  const char* prop, gint fallback) {
  if (elem_name.empty()) return fallback;
  GstElement* e = gst_bin_get_by_name(GST_BIN(pipeline), elem_name.c_str());
  if (e == nullptr) return fallback;
  gint v = fallback;
  if (g_object_class_find_property(G_OBJECT_GET_CLASS(e), prop) != nullptr)
    g_object_get(G_OBJECT(e), prop, &v, nullptr);
  gst_object_unref(e);
  return v;
}
}  // namespace

Scheduler::Scheduler(const SchedCfg& cfg, int num_cams)
    : cfg_(cfg), num_cams_(num_cams), cams_(num_cams) {
  if (num_cams < 1)
    throw std::runtime_error("vista: num_cams must be >= 1");
  validate_cfg();
  if (!cfg_.decision_csv.empty()) {
    dlog_ = std::fopen(cfg_.decision_csv.c_str(), "w");
    if (dlog_ == nullptr)
      throw std::runtime_error("vista: cannot open decision CSV: " +
                               cfg_.decision_csv);
    std::fputs(
        "t,event,cam,slot,age_ms,fresh_score,imp_score,fair_score,value,"
        "released,in_flight,buf_pts\n",
        dlog_);
  }
}

/* Reject configurations that are verifiably broken. Every bound here either
 * matches the paper binary's checks or closes a hang/silent-wrong that the
 * paper binary had. */
void Scheduler::validate_cfg() const {
  if (cfg_.mode != "off" && cfg_.mode != "fresh" && cfg_.mode != "imp" &&
      cfg_.mode != "salvage")
    throw std::runtime_error("vista: mode must be off|fresh|imp|salvage, got '" +
                             cfg_.mode + "'");
  if (cfg_.k < 1 || cfg_.k > num_cams_ * 2)
    throw std::runtime_error("vista: k must be in 1..2*num_cams");
  if (cfg_.stash < 1 || cfg_.stash > 8)
    throw std::runtime_error("vista: stash must be in 1..8");
  /* depth < 1 makes the release gate `in_flight > (depth-1)*k` read
   * `0 > -k` = true forever: the paper binary accepted --sched-depth 0 and
   * hung silently with no output and no message. */
  if (cfg_.depth < 1 || cfg_.depth > 8)
    throw std::runtime_error("vista: depth must be in 1..8 (depth 0 hangs)");
  if (cfg_.tau_max_ms <= 0.0)
    throw std::runtime_error("vista: tau_max_ms must be > 0");
  if (cfg_.imp_max <= 0.0)
    throw std::runtime_error("vista: imp_max must be > 0");
  if (cfg_.imp_halflife_s <= 0.0)
    throw std::runtime_error("vista: imp_halflife_s must be > 0");
  if (cfg_.w_fresh < 0.0 || cfg_.w_imp < 0.0 || cfg_.w_fair < 0.0)
    throw std::runtime_error("vista: weights must be non-negative");

  const double wsum = cfg_.w_fresh + cfg_.w_imp + cfg_.w_fair;
  if (wsum <= 0.0)
    throw std::runtime_error("vista: at least one weight must be > 0");
  /* Scores are compared, never thresholded, so a non-unit weight sum only
   * rescales v(f) uniformly — harmless but almost always a typo. */
  if (std::fabs(wsum - 1.0) > 1e-6)
    std::fprintf(stderr,
                 "[vista] WARNING: weights sum to %.3f, not 1.0 "
                 "(w_fresh=%.2f w_imp=%.2f w_fair=%.2f). Scores are compared, "
                 "not thresholded, so this only rescales v(f) — but check it.\n",
                 wsum, cfg_.w_fresh, cfg_.w_imp, cfg_.w_fair);
  /* The paper's RQ3 result: through a 1-deep stash, activity weighting cannot
   * concentrate service on a busy camera — it stays pinned near its even
   * share regardless of w_imp. Silently accepting this exact misconfiguration
   * is what the deployment rule exists to prevent. */
  if (cfg_.use_importance() && cfg_.stash < cfg_.depth)
    std::fprintf(stderr,
                 "[vista] WARNING: stash=%d < depth=%d with importance ON. The "
                 "activity term cannot concentrate service through a stash "
                 "shallower than the release pipeline: the busy camera stays "
                 "near its even share no matter how high w_imp is. Set "
                 "stash >= depth (paper: stash=2, depth=2). See docs/design/"
                 "04-depth-and-stash.md\n",
                 cfg_.stash, cfg_.depth);
}

Scheduler::~Scheduler() {
  request_stop();
  join_and_cleanup();
  if (dlog_ != nullptr) {
    std::fclose(dlog_);
    dlog_ = nullptr;
  }
}

/* Host obligations that ARE readable as GObject properties. The mux INI
 * (obligation 2) is not property-readable — it is enforced at runtime by the
 * batch-atomicity gate in on_completion(). */
void Scheduler::check_obligations(GstElement* pipeline) const {
  if (!cfg_.strict) return;

  /* Mux batch-size. MEASURED (DS 7.1, new mux): this property is NOT reliable
   * at attach() time, because attach() necessarily runs before the state
   * change. Requesting sink pads raises batch-size to the pad count while the
   * mux still has its built-in adaptive-batching=1 — the INI that turns
   * adaptive batching off is not read until the state change. Probed on a
   * 4-camera pipeline with the app setting batch-size=2 first:
   *   create 1 | set 2 -> 2 | INI -> 2 | sink_0 -> 2 | sink_1 -> 2
   *   | sink_2 -> 3 | sink_3 -> 4 ... and 2 again once PLAYING.
   * So on the paper's own headline configuration (4 cameras, k=2) the property
   * reads 4 exactly where this check runs, and an equality throw here rejects
   * a CORRECT pipeline. (Verified: it did, and the batch fill histogram of the
   * same run was 100% at exactly k=2.)
   *
   * What remains decidable: batch-size < k can never batch k frames, whatever
   * the mux does later. That throws. batch-size > k is ambiguous — it is the
   * pad-count bump on a correct pipeline, and a genuine misconfiguration on a
   * wrong one — so it warns, and the RUNTIME atomicity gate in on_completion()
   * decides it on evidence (the fill histogram), which is the only thing that
   * actually settles the question. */
  const gint mux_bs = get_int_prop(pipeline, cfg_.mux_name, "batch-size", cfg_.k);
  if (mux_bs < cfg_.k)
    throw std::runtime_error(
        "vista: mux '" + cfg_.mux_name + "' batch-size=" +
        std::to_string(mux_bs) + " but k=" + std::to_string(cfg_.k) +
        ". A release of k frames cannot land as one batch through a mux that "
        "batches fewer. Set the mux batch-size to k (see vista/README.md "
        "'Host obligations').");
  if (mux_bs > cfg_.k)
    std::fprintf(stderr,
                 "[vista] NOTE: mux '%s' reports batch-size=%d with k=%d. On "
                 "DS 7.1 the new mux reports the sink-pad count until the INI "
                 "is read at the state change, so this is EXPECTED when you "
                 "set batch-size=k before linking %d sources. The batch "
                 "atomicity gate will confirm the real value from the fill "
                 "histogram; if it warns, your mux batch-size really is wrong.\n",
                 cfg_.mux_name.c_str(), mux_bs, cfg_.k, mux_bs);

  const gint pgie_bs =
      get_int_prop(pipeline, cfg_.pgie_name, "batch-size", cfg_.k);
  if (pgie_bs != cfg_.k)
    throw std::runtime_error(
        "vista: nvinfer '" + cfg_.pgie_name + "' batch-size=" +
        std::to_string(pgie_bs) + " but k=" + std::to_string(cfg_.k) +
        ". Partial batches change service time. Set it to k.");

  const gint sync_inputs = get_int_prop(pipeline, cfg_.mux_name, "sync-inputs", 0);
  if (sync_inputs != 0)
    throw std::runtime_error(
        "vista: mux '" + cfg_.mux_name +
        "' has sync-inputs=1. VISTA replaces timestamp alignment with local "
        "arrival-clock scheduling; on commodity USB capture the fabricated PTS "
        "grids disagree by seconds and sync-inputs silently erased 85.3% of "
        "arrived frames in our measurements. Set sync-inputs=0.");
}

void Scheduler::attach(GstElement* pipeline) {
  if (pipeline == nullptr)
    throw std::runtime_error("vista: attach() called with a null pipeline");
  check_obligations(pipeline);

  const double now = mono_secs();
  t_start_ = now;
  last_completion_ = now;
  for (int i = 0; i < num_cams_; ++i) {
    const std::string name = cfg_.source_bin_prefix + std::to_string(i);
    GstElement* bin = gst_bin_get_by_name(GST_BIN(pipeline), name.c_str());
    if (bin == nullptr)
      throw std::runtime_error(
          "vista: could not find " + name +
          ". VISTA intercepts frames on each camera's source bin; name your "
          "bins '" + cfg_.source_bin_prefix +
          "<i>' or set SchedCfg::source_bin_prefix.");
    GstPad* pad = gst_element_get_static_pad(bin, cfg_.source_pad_name.c_str());
    gst_object_unref(bin);
    if (pad == nullptr)
      throw std::runtime_error("vista: no '" + cfg_.source_pad_name +
                               "' pad on " + name);

    auto ctx = std::make_unique<ArrivalCtx>();
    ctx->self = this;
    ctx->cam = i;
    gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, arrival_probe, ctx.get(),
                      nullptr);
    gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM, event_probe,
                      ctx.get(), nullptr);
    arrival_ctxs_.push_back(std::move(ctx));
    cams_[i].pad = pad;  // keep the ref; released in join_and_cleanup()
    cams_[i].last_served = now;
  }

  /* The completion clock. Any element downstream of nvinfer works — VISTA only
   * reads num_frames_in_batch off the batch meta. */
  GstElement* trk =
      gst_bin_get_by_name(GST_BIN(pipeline), cfg_.tracker_name.c_str());
  if (trk == nullptr)
    throw std::runtime_error(
        "vista: could not find '" + cfg_.tracker_name +
        "'. VISTA needs an inference-completion signal: point "
        "SchedCfg::tracker_name at any element downstream of nvinfer.");
  GstPad* trk_src =
      gst_element_get_static_pad(trk, cfg_.tracker_pad_name.c_str());
  gst_object_unref(trk);
  if (trk_src == nullptr)
    throw std::runtime_error("vista: '" + cfg_.tracker_name + "' has no '" +
                             cfg_.tracker_pad_name + "' pad.");
  gst_pad_add_probe(trk_src, GST_PAD_PROBE_TYPE_BUFFER, completion_probe, this,
                    nullptr);
  gst_object_unref(trk_src);

  thread_ = std::thread(&Scheduler::thread_main, this);
  std::fprintf(stderr,
               "[vista] mode=%s k=%d depth=%d stash=%d tau_max=%.0fms "
               "tau_salvage=%.0fms w=(%.2f,%.2f,%.2f)%s\n",
               cfg_.mode.c_str(), cfg_.k, cfg_.depth, cfg_.stash,
               cfg_.tau_max_ms, cfg_.tau_salvage_ms, cfg_.w_fresh, cfg_.w_imp,
               cfg_.w_fair, dlog_ ? " (decision log on)" : "");
}

// ---------------------------------------------------------------------------
// Probes (streaming threads)
// ---------------------------------------------------------------------------
GstPadProbeReturn Scheduler::arrival_probe(GstPad*, GstPadProbeInfo* info,
                                           gpointer user_data) {
  if (t_vista_pushing) return GST_PAD_PROBE_OK;  // our own release: pass
  auto* ctx = static_cast<ArrivalCtx*>(user_data);
  GstBuffer* buf = GST_PAD_PROBE_INFO_BUFFER(info);
  if (buf == nullptr) return GST_PAD_PROBE_OK;
  gst_buffer_ref(buf);  // we own a ref; the DROP below releases the pad's
  ctx->self->on_arrival(ctx->cam, buf);
  return GST_PAD_PROBE_DROP;
}

GstPadProbeReturn Scheduler::event_probe(GstPad*, GstPadProbeInfo* info,
                                         gpointer user_data) {
  GstEvent* ev = GST_PAD_PROBE_INFO_EVENT(info);
  if (ev == nullptr || GST_EVENT_TYPE(ev) != GST_EVENT_EOS)
    return GST_PAD_PROBE_OK;
  auto* ctx = static_cast<ArrivalCtx*>(user_data);
  Scheduler* self = ctx->self;
  {
    /* Let the EOS PASS THROUGH untouched (the pipeline's normal, known-good
     * teardown path — a v1 swallow-and-forward-later drain deadlocked the
     * pipeline at EOS). The scheduler just stops scheduling this camera and
     * releases its stashed refs: at most `stash` tail frames per camera are
     * not processed, which is irrelevant for steady-state benchmarks
     * (--duration runs trim warmup and use rates/distributions, not totals). */
    std::lock_guard<std::mutex> lock(self->mu_);
    CamState& c = self->cams_[ctx->cam];
    c.eos = true;
    for (auto& s : c.fresh) self->drop_slot(s, ctx->cam, "eos");
    c.fresh.clear();
    self->drop_slot(c.held, ctx->cam, "eos");
  }
  self->cv_.notify_all();
  return GST_PAD_PROBE_OK;
}

GstPadProbeReturn Scheduler::completion_probe(GstPad*, GstPadProbeInfo* info,
                                              gpointer user_data) {
  auto* self = static_cast<Scheduler*>(user_data);
  GstBuffer* buf = GST_PAD_PROBE_INFO_BUFFER(info);
  if (buf != nullptr) self->on_completion(buf);
  return GST_PAD_PROBE_OK;
}

void Scheduler::on_arrival(int cam, GstBuffer* buf) {
  const double now = mono_secs();
  {
    std::lock_guard<std::mutex> lock(mu_);
    CamState& c = cams_[cam];
    if (stop_.load() || c.eos) {  // late arrival during teardown: discard
      gst_buffer_unref(buf);
      return;
    }
    c.arrivals++;
    if (static_cast<int>(c.fresh.size()) >= cfg_.stash) {
      // Stash full: displace the OLDEST fresh frame (keep-newest overall).
      Slot victim = c.fresh.front();
      c.fresh.pop_front();
      const double imp_s =
          std::min(importance_now(cam, now) / cfg_.imp_max, 1.0);
      if (cfg_.use_salvage() && imp_s >= cfg_.retention_thresh) {
        drop_slot(c.held, cam, "displace-held");
        c.held = victim;  // retained for possible salvage
        log_decision(now - t_start_, "retain-held", cam, "held",
                     (now - c.held.t_arrival) * 1e3, 0, imp_s, 0, 0, 0,
                     in_flight_.load(), GST_BUFFER_PTS(c.held.buf));
      } else {
        drop_slot(victim, cam, "displace");
      }
    }
    c.fresh.push_back(Slot{buf, now});
  }
  cv_.notify_all();
}

void Scheduler::on_completion(GstBuffer* buf) {
  NvDsBatchMeta* batch_meta = gst_buffer_get_nvds_batch_meta(buf);
  if (batch_meta == nullptr) return;
  const double now = mono_secs();
  int frames = static_cast<int>(batch_meta->num_frames_in_batch);

  {
    std::lock_guard<std::mutex> lock(mu_);
    for (NvDsMetaList* l = batch_meta->frame_meta_list; l != nullptr;
         l = l->next) {
      auto* fm = static_cast<NvDsFrameMeta*>(l->data);
      const int cam = static_cast<int>(fm->source_id);
      if (cam < 0 || cam >= num_cams_) continue;
      CamState& c = cams_[cam];
      int dets = 0, new_tracks = 0;
      for (NvDsMetaList* lo = fm->obj_meta_list; lo != nullptr; lo = lo->next) {
        auto* obj = static_cast<NvDsObjectMeta*>(lo->data);
        ++dets;
        const auto tid = static_cast<int64_t>(obj->object_id);
        if (tid >= 0 && c.seen_ids.insert(tid).second) ++new_tracks;
      }
      /* Importance measures ACTIVITY (new objects appearing), not standing
       * content. The v1 increment (3*new_tracks + 1*dets) saturated at I_max
       * on any persistent-object scene — measured: median imp_score 1.000 on
       * every camera of an office scene, 68% of admissions at >=0.99 — making
       * the importance term a constant and imp mode structurally identical to
       * fresh mode. v2: new-track events only. */
      const double inc = 1.0 * new_tracks;
      const double old = importance_now(cam, now);  // decays to now
      c.importance = std::min(old + inc, cfg_.imp_max);
      c.imp_updated = now;
      (void)dets;
    }

    // Service-time estimate from the release FIFO.
    if (!released_.empty()) {
      const double dt_ms = (now - released_.front().first) * 1e3;
      released_.pop_front();
      s_hat_ms_ = 0.8 * s_hat_ms_ + 0.2 * dt_ms;
    }
    ++completions_;
    last_completion_ = now;
    fill_hist_[frames]++;

    /* Batch-atomicity gate (the paper's pre-run gate G1, running in-process).
     * A release of k frames must arrive as ONE batch of exactly k. If the mux
     * INI is wrong this degrades SILENTLY — batches merge to the source count
     * (adaptive-batching=1) or split into 1+(k-1) (INI deadline anchors too
     * tight) — and the run simply produces quietly wrong numbers. */
    if (cfg_.gate_check && !gate_warned_ && completions_ >= 20) {
      long total = 0, at_k = 0;
      for (const auto& kv : fill_hist_) {
        total += kv.second;
        if (kv.first == cfg_.k) at_k += kv.second;
      }
      if (total > 0 && (100.0 * at_k / total) < 90.0) {
        gate_warned_ = true;
        std::fprintf(stderr,
                     "[vista] WARNING: batch atomicity gate FAILED — only "
                     "%.1f%% of completed batches carry exactly k=%d. Your mux "
                     "INI is probably wrong: use vista/config/mux_vista.txt "
                     "(adaptive-batching=0, deadline anchors pushed out). "
                     "Numbers from this run are not comparable to the paper's. "
                     "See vista/README.md 'Host obligations'.\n",
                     100.0 * at_k / total, cfg_.k);
      }
    }
  }

  long prev = in_flight_.fetch_sub(frames);
  if (prev - frames < 0) in_flight_.store(0);  // clamp (split accounting)
  cv_.notify_all();
}

// ---------------------------------------------------------------------------
// Scheduler thread
// ---------------------------------------------------------------------------
double Scheduler::importance_now(int cam, double now) {
  CamState& c = cams_[cam];
  if (c.imp_updated <= 0.0) return 0.0;
  const double dt = now - c.imp_updated;
  if (dt > 0.0)
    return c.importance * std::exp2(-dt / cfg_.imp_halflife_s);
  return c.importance;
}

void Scheduler::drop_slot(Slot& slot, int cam, const char* why) {
  if (slot.buf == nullptr) return;
  if (cfg_.log_drops && dlog_ != nullptr) {
    const double now = mono_secs();
    log_decision(now - t_start_, why, cam, "fresh",
                 (now - slot.t_arrival) * 1e3, 0, 0, 0, 0, 0,
                 in_flight_.load(), GST_BUFFER_PTS(slot.buf));
  }
  gst_buffer_unref(slot.buf);
  slot = Slot{};
  cams_[cam].policy_drops++;
  (void)why;
}

void Scheduler::log_decision(double t, const char* event, int cam,
                             const char* slot, double age_ms, double fresh_s,
                             double imp_s, double fair_s, double value,
                             int released, long in_flight, guint64 buf_pts) {
  if (dlog_ == nullptr) return;
  std::lock_guard<std::mutex> lock(dlog_mu_);
  std::fprintf(dlog_,
               "%.4f,%s,%d,%s,%.1f,%.3f,%.3f,%.3f,%.3f,%d,%ld,%" G_GUINT64_FORMAT "\n",
               t, event, cam, slot, age_ms, fresh_s, imp_s, fair_s, value,
               released, in_flight, buf_pts);
}

void Scheduler::thread_main() {
  pthread_setname_np(pthread_self(), "vista-sched");  // /proc-visible for
                                                      // overhead accounting
  std::unique_lock<std::mutex> lock(mu_);
  while (!stop_.load()) {
    // Wake on arrivals/completions/EOS; timeout keeps the watchdog alive.
    cv_.wait_for(lock, std::chrono::milliseconds(5));
    if (stop_.load()) break;

    // Watchdog: releases outstanding but no completion for 10x service time.
    // Armed only after a few real completions — the first batches include
    // TensorRT engine load and can legitimately take seconds.
    const double now = mono_secs();
    if (completions_ >= 3 && in_flight_.load() > 0 &&
        (now - last_completion_) * 1e3 >
            std::max(10.0 * std::max(s_hat_ms_, 50.0), 2000.0)) {
      std::fprintf(stderr,
                   "[vista] WATCHDOG: no batch completion for %.0f ms with "
                   "%ld frames in flight — resetting gate.\n",
                   (now - last_completion_) * 1e3, in_flight_.load());
      in_flight_.store(0);
      released_.clear();
      last_completion_ = now;
    }

    // Release as long as the gate allows (a completion may free room for
    // more than one release at small k).
    while (!stop_.load() && release_once()) {
    }

    // All cameras at EOS: nothing left to schedule; the pipeline's own EOS
    // (which passed through untouched) finishes the run.
    bool all_done = true;
    for (int i = 0; i < num_cams_; ++i) all_done = all_done && cams_[i].eos;
    if (all_done) break;
  }
}

/* One release attempt. Must be called with mu_ held; unlocks around the
 * pushes. Returns true if a release happened (caller loops). */
bool Scheduler::release_once() {
  const double now = mono_secs();

  // Gate: keep at most (depth-1)*k frames in flight.
  if (in_flight_.load() > static_cast<long>((cfg_.depth - 1) * cfg_.k))
    return false;

  // Evict stale slots (fresh is arrival-ordered: oldest at front).
  for (int i = 0; i < num_cams_; ++i) {
    CamState& c = cams_[i];
    while (!c.fresh.empty() &&
           (now - c.fresh.front().t_arrival) * 1e3 > cfg_.tau_max_ms) {
      drop_slot(c.fresh.front(), i, "evict-stale");
      c.fresh.pop_front();
    }
    if (c.held.buf != nullptr &&
        (now - c.held.t_arrival) * 1e3 > cfg_.tau_salvage_ms)
      drop_slot(c.held, i, "evict-held");
  }

  // Build the candidate list.
  struct Cand {
    int cam;
    bool held;
    double age_ms, fresh_s, imp_s, fair_s, value;
    bool forced;
  };
  std::vector<Cand> cands;
  /* D_fair is set from the system's own measured pace — roughly the time to
   * give every camera one turn, times a small safety factor — and re-measured
   * while running, so a heavier detector automatically earns a longer grace
   * period with nothing to retune. D_hard is the force-admission deadline. */
  const double d_fair_ms =
      2.0 * (static_cast<double>(num_cams_) / cfg_.k) * s_hat_ms_;
  const double d_hard_ms = 4.0 * d_fair_ms;
  int n_eos = 0;
  for (int i = 0; i < num_cams_; ++i) {
    CamState& c = cams_[i];
    if (c.eos) ++n_eos;
    const double imp_raw = importance_now(i, now);
    const double imp_s =
        cfg_.use_importance() ? std::min(imp_raw / cfg_.imp_max, 1.0) : 0.0;
    const double since_served = (now - c.last_served) * 1e3;
    const double fair_s = std::min(since_served / d_fair_ms, 1.0);
    const bool forced = since_served > d_hard_ms && !c.fresh.empty();
    if (!c.fresh.empty()) {
      /* Offer the OLDEST stashed frame (front): consecutive releases of one
       * depth-cycle then drain a hot camera's stash in ascending-PTS order —
       * no cross-release timestamp regression, unlike salvage. */
      const double age = (now - c.fresh.front().t_arrival) * 1e3;
      const double fresh_s = std::max(0.0, 1.0 - age / cfg_.tau_max_ms);
      cands.push_back({i, false, age, fresh_s, imp_s, fair_s,
                       cfg_.w_fresh * fresh_s + cfg_.w_imp * imp_s +
                           cfg_.w_fair * fair_s,
                       forced});
    }
    if (cfg_.use_salvage() && c.held.buf != nullptr) {
      const double age = (now - c.held.t_arrival) * 1e3;
      const double fresh_s = std::max(0.0, 1.0 - age / cfg_.tau_salvage_ms);
      cands.push_back({i, true, age, fresh_s, imp_s, fair_s,
                       cfg_.w_fresh * fresh_s + cfg_.w_imp * imp_s +
                           cfg_.w_fair * fair_s,
                       false});
    }
  }

  // Not enough material for a full K-batch: wait for more arrivals (frames
  // arrive at 30 fps/cam, so this resolves within one frame period). Once
  // cameras hit EOS, allow short releases from the remaining live cameras.
  if (static_cast<int>(cands.size()) < cfg_.k && n_eos == 0) return false;
  if (cands.empty()) return false;

  // Selection: forced cameras first (the fairness floor), then by value.
  std::stable_sort(cands.begin(), cands.end(), [](const Cand& a, const Cand& b) {
    if (a.forced != b.forced) return a.forced;
    return a.value > b.value;
  });
  std::vector<Cand> admitted;
  for (const auto& cd : cands) {
    if (static_cast<int>(admitted.size()) >= cfg_.k) break;
    admitted.push_back(cd);  // <=1 fresh + <=1 held per camera by stash shape
  }

  // Take the buffers out of the stash while still locked.
  struct PushItem {
    int cam;
    bool held;
    GstBuffer* buf;
    GstPad* pad;
    Cand cd;
  };
  std::vector<PushItem> items;
  for (const auto& cd : admitted) {
    CamState& c = cams_[cd.cam];
    if (c.pad_dead) continue;
    GstBuffer* buf = nullptr;
    if (cd.held) {
      if (c.held.buf == nullptr) continue;
      buf = c.held.buf;
      c.held = Slot{};
    } else {
      if (c.fresh.empty()) continue;
      buf = c.fresh.front().buf;
      c.fresh.pop_front();
    }
    items.push_back({cd.cam, cd.held, buf, c.pad, cd});
    c.last_served = now;
    if (cd.held) {
      c.admitted_held++;
      salvage_admits_++;
    } else {
      c.admitted_fresh++;
    }
  }
  if (items.empty()) return false;

  // Ascending PTS within a camera: held (older) before fresh.
  std::stable_sort(items.begin(), items.end(),
                   [](const PushItem& a, const PushItem& b) {
                     if (a.cam != b.cam) return a.cam < b.cam;
                     return a.held && !b.held;
                   });

  in_flight_.fetch_add(static_cast<long>(items.size()));
  released_.emplace_back(now, static_cast<int>(items.size()));
  ++releases_;
  const long inflt = in_flight_.load();
  for (const auto& it : items)
    log_decision(now - t_start_, it.held ? "admit-salvage" : "admit", it.cam,
                 it.held ? "held" : "fresh", it.cd.age_ms, it.cd.fresh_s,
                 it.cd.imp_s, it.cd.fair_s, it.cd.value,
                 static_cast<int>(items.size()), inflt,
                 GST_BUFFER_PTS(it.buf));

  // Push outside the lock (the arrival probes must not deadlock against us).
  mu_.unlock();
  t_vista_pushing = true;
  for (const auto& it : items) {
    const GstFlowReturn ret = gst_pad_push(it.pad, it.buf);  // consumes ref
    if (ret == GST_FLOW_EOS || ret == GST_FLOW_FLUSHING) {
      std::lock_guard<std::mutex> lk(mu_);
      cams_[it.cam].pad_dead = true;
    } else if (ret != GST_FLOW_OK) {
      std::fprintf(stderr, "[vista] push on cam %d returned %s\n", it.cam,
                   gst_flow_get_name(ret));
    }
  }
  t_vista_pushing = false;
  mu_.lock();
  return true;
}

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------
void Scheduler::request_stop() {
  stop_.store(true);
  cv_.notify_all();
}

void Scheduler::join_and_cleanup() {
  if (thread_.joinable()) thread_.join();
  std::lock_guard<std::mutex> lock(mu_);
  for (int i = 0; i < num_cams_; ++i) {
    CamState& c = cams_[i];
    /* Residual stashed frames are COUNTED as policy drops, not silently
     * unreffed. At any instant the true identity is
     *   arrivals == admitted + policy_drops + still_in_stash
     * so the ledger the paper claims (still_in_stash == 0) only closes if
     * shutdown accounts for what the stash still holds. These frames arrived
     * and will never be inferred: that is a drop, and VISTA's whole argument is
     * that such drops are counted rather than silently absorbed.
     *
     * This is a REAL FIX vs. the paper binary, which raw-unreffed here. Its
     * effect is bounded by num_cams*stash frames (<=4 of ~2.4k in a 20 s
     * 4-camera run) and lands entirely at teardown, so it changes no
     * scheduling decision and no steady-state rate — but without it
     * Stats::ledger_closes() is a coin flip on the state of the stashes at the
     * instant the run ends. Measured: broken on a 12 s run, closing on a 20 s
     * run of the same config. See vista/PAPER_DIFF.md. */
    for (auto& s : c.fresh) drop_slot(s, i, "shutdown");
    c.fresh.clear();
    drop_slot(c.held, i, "shutdown");
    if (c.pad != nullptr) {
      gst_object_unref(c.pad);
      c.pad = nullptr;
    }
  }
  if (dlog_ != nullptr) std::fflush(dlog_);
}

Stats Scheduler::stats() const {
  std::lock_guard<std::mutex> lock(mu_);
  Stats s;
  for (const auto& c : cams_) {
    s.arrivals += c.arrivals;
    s.admitted_fresh += c.admitted_fresh;
    s.admitted_salvage += c.admitted_held;
    s.policy_drops += c.policy_drops;
    s.per_cam_admits.push_back(c.admitted_fresh + c.admitted_held);
    s.per_cam_drops.push_back(c.policy_drops);
  }
  s.releases = releases_;
  s.completions = completions_;
  s.s_hat_ms = s_hat_ms_;
  s.elapsed_s = std::max(1e-6, mono_secs() - t_start_);
  s.fill_hist = fill_hist_;
  return s;
}

/* NOTE: this line's format is parsed by analysis/weightsweep/aggregate_runs.py to recover
 * the drop ledger from archived stderr.log files. The parser accepts both
 * "[vista]" and the paper binary's original "[sched]" prefix; if you change
 * anything else here, update the regex too. See NAMING.md. */
void Scheduler::print_summary() const {
  const double dur = std::max(1e-6, mono_secs() - t_start_);
  long drops = 0, af = 0, ah = 0;
  for (const auto& c : cams_) {
    drops += c.policy_drops;
    af += c.admitted_fresh;
    ah += c.admitted_held;
  }
  std::fprintf(stderr,
               "[vista] %s: %ld releases (%.1f/s), %ld fresh + %ld salvage "
               "admitted, %ld policy drops, s_hat %.1f ms over %.1f s.\n",
               cfg_.mode.c_str(), releases_, releases_ / dur, af, ah, drops,
               s_hat_ms_, dur);
}

}  // namespace vista
