# Known issues

Two kinds of thing are recorded here: places where this code disagrees with the
current draft of *"VISTA: Value-Driven Inference Scheduling for Timely
Autonomous Multi-Camera Perception"* (IEEE L-SMC, 2026), and defects in the code
itself.

**The measurement archive behind the paper is not distributed with this
repository.** The items in the first section were established against that
archive while it was in hand; they are therefore **recorded here rather than
demonstrated here**, and the commands that produced them are not runnable from a
clean clone. The items in the second section are about source files that do
ship, and can be checked by reading the file named.

---

## Discrepancies between this code and the current draft

### The "7% throughput reduction" is the all-admit ablation's figure

Sec. V-E reads: "The completion-triggered release path adds approximately
5–10 ms per batch, corresponding to a 7% throughput reduction at the primary
YOLO11m load point." That 7% is the **all-admit ablation's** measured 6.8%
(K=4, every frame admitted) — an arm the current draft no longer discusses
anywhere, so a reader cannot locate the number's source. The throughput tax of
the configuration the draft recommends, at the load point the draft names, was
measured at **14.7%** (VISTA-Fresh, K=2, YOLO11m). The neighbouring live figure
in the same section is consistent with the larger number and not with 7%: Table
V's 0.92x is an 8% tax at K=2 on live cameras.

### The 63 "manually verified" object arrivals are classified automatically

Sec. IV-C describes the reference set as "123 events, including 63 manually
verified object arrivals, 39 tracker rediscoveries, and 21 class-instability
events." `analysis/clean_events.py` classifies **all 123 automatically**, with
two rules and no human in the loop: a 16-class scene-implausibility blocklist,
and an IoU >= 0.30 re-identification heuristic that reads a re-detection near an
expired event's last box as a rediscovery rather than a new object. Nothing in
it inspects a frame, and **no annotation file exists**. The rules are defensible
and the draft discloses their effect honestly; the adjective is what the code
does not support. Note also that the blocklist is workload-specific by
construction — it encodes what is implausible in an office scene — so porting
the analysis to another scene type means revisiting it.

### Coverage's stated definition is not its implementation

Sec. IV-C defines coverage as "the fraction of camera-generated frames that are
processed." The implementation (`analysis/match_events.py`) computes the
reference-detected frames a policy also processed, divided by the
reference-detected frames: the denominator is the **6,415** frames carrying a
reference detection, not the **6,882** frames the cameras generated. Coverage as
reported is therefore roughly 7% optimistic against its stated definition. The
bias applies to every policy equally, so the comparison between policies holds;
the sentence defining the metric does not.

### VISTA-Activity's stated configuration is not the one that was run

Sec. IV-A states that VISTA-Activity runs "with K=2, d=2, and a two-frame
stash," and that "the two-frame stash satisfies the stash >= d condition
required for importance-aware service concentration." The runs behind the
draft's VISTA-Activity results pass **no `--sched-stash`** and therefore ran at
the shipped default of **stash 1** (`vista/include/vista/vista_scheduler.hpp`,
`struct SchedCfg`). A full census of the archive found `--sched-stash 2` on the
**skewed-activity microbenchmark only** — none on the primary workload, in any
mode. The K=2 and d=2 halves of the sentence are right.

The consequence is worth stating plainly rather than filing as a label error. At
stash 1 with d=2, the design's own rule (`docs/design/04-depth-and-stash.md`)
says importance **cannot** concentrate service *regardless of the workload*. So
the draft's reading of the null result — VISTA-Activity "provides no additional
benefit on this approximately uniform-activity workload" — is confounded with
"the stash capped it." Both explanations may be true, and the workload one is
independently supported by the roughly even event split across cameras; but only
one is stated, and the unstated one is the paper's own contributed
configuration rule. The run that would separate them (imp, K=2, d=2, stash 2, on
the primary workload) does not exist and needs the clips and the rig.

### D_hard is a per-run admission deadline, not a constant or a guarantee

Sec. III-B describes a per-camera service deadline `D_hard` derived from the
measured batch-completion rate. Two properties a reader should know, neither of
which the draft states:

* It is **derived per run** — roughly 1.1–3.2 s across the primary load point —
  not a fixed value. Any single ceiling quoted for it is a per-run artifact.
* It is an **admission deadline**, the point at which a starved camera's next
  eligible frame is force-included in a batch. It is not a conformance
  guarantee, and in the measured campaign some runs' worst observed admission
  gap exceeded their own indicative bound. Every run's worst gap traced to the
  TensorRT engine-load stall shortly after the first admit.

The current draft states the deadline qualitatively and prints no number, which
is the conservative choice. `docs/design/05-scheduler-internals.md` carries the
analysis; the true service-interval bound is `D_hard + ceil(N/K)*S(K)`, since
simultaneous floor-crossings queue.

---

## Known defects in this code

### `metrics.csv`'s `mux_batch` column was wrong under `--sched` — fixed here

`MetricsCollector` writes its `mux_batch` constructor argument verbatim into
every CSV row. The archived binary passed the camera count (`n`, i.e. 4)
unconditionally, while `pipeline_builder.cpp` derives the mux's real batch size
as the override when set — and a `--sched` run sets the override to `k`. So
every archived scheduler run recorded `mux_batch=4` while the mux was configured
for K (2 at the paper's operating point).

`app/src/main.cpp` now passes the same effective value the builder uses. No
analysis code reads the column — `n_in_batch`, from
`NvDsBatchMeta::num_frames_in_batch`, is the real batch — so correcting it
changes no published result. **If you parse archived `metrics.csv`, treat
`mux_batch` as unreliable under `--sched`**: it reads 4, and the true value is
the run's `--sched-k`.

### `metrics.csv`'s `drops_cum` is dead — always 0

`drops_cum` counts the mux's `"dropped"` signal, which the new `nvstreammux`
emits **only** for frames its `NvTimeSync` module erases as LATE — that is, only
under `sync-inputs=1`. Every run in this project sets `sync-inputs=0`
(`docs/design/06-local-clocks.md`), so the signal never fires and the column is 0
in every row of every run.

**The trap is that 0 is also what a perfect run would report.** A reader who
takes `drops_cum` as "frames this pipeline lost" concludes the stock pipeline
loses nothing — precisely the illusion the paper exists to dispel. The stock
pipeline's losses happen in the kernel capture ring, upstream of the mux and of
every counter the pipeline has.

**The drop ledger is the scheduler's summary line on stderr** (`[sched]` /
`[vista]`), which is why gate G2 parses stderr rather than `metrics.csv` — using
`drops_cum` would make G2 the tautology `0 == 0`. `sched.csv` holds **admits
only**; the module can emit drop rows opt-in via `SchedCfg::log_drops`, which the
paper's runs did not set.

### `dets.jsonl` is not valid JSONL, and `e2e_ms` carries negative sentinels

The application writes `dets.jsonl` on the same file descriptor GStreamer logs
to, so plugin chatter (`Opening in BLOCKING MODE` and similar) is interleaved
with the JSON records — roughly **14 of 2,673 lines** in a typical run.
`json.loads` line by line raises on the first one. The rule: skip any line not
starting with `{`, and skip a `JSONDecodeError` on a torn line.

Separately, `e2e_ms` in `metrics.csv` carries **negative sentinels** for batches
whose capture stamp was unavailable. Filter `>= 0`; do not clamp.

`analysis/_paths.py` handles both, and readers should accept plain and gzipped
detection files. Gate G3 exercises the reader against real contaminated output
rather than asserting the file is clean, and reports its skip count so a sudden
change is visible.

### `seen_ids` is an unbounded `std::set` per camera

`CamState::seen_ids` (`vista/include/vista/vista_scheduler.hpp`) is how
importance measures *change* rather than content: a track ID is "new" the first
time it is seen. Nothing ever erases from it, and the tracker allocates IDs
monotonically, so the set grows for the lifetime of the process — once per
distinct object ever tracked, per camera.

At benchmark timescales this is noise (order 55 distinct tracks per 120 s run
across four cameras, a few kilobytes), which is why it was never noticed. Over a
month of deployment it is tens of megabytes of pure ratchet, and the lookup cost
grows as `log n` on the completion path, which holds the scheduler's mutex. A
deployment should bound it — a bloom filter, a ring of recent IDs, or an LRU
sized to a few multiples of `imp_halflife_s` all preserve the signal, since
importance decays with a 2 s half-life and an ID older than a few half-lives
cannot influence any current score. Not applied here because it changes the
importance path (`vista/PAPER_DIFF.md`).

Worth knowing regardless: because the set is never reset, importance's notion of
"new" is lifetime-scoped. An object that leaves and returns after an hour is not
new; one that gets a fresh track ID is, even if it is the same physical object.
That is inherited from the tracker's ID allocation, not a property of VISTA.

### `in_flight_` can leak the release gate on a null batch meta

`Scheduler::on_completion()` (`vista/src/vista_scheduler.cpp`) returns early when
the buffer carries no `NvDsBatchMeta`, and it returns **before the decrement**,
so the K frames released for that batch are never credited back. `in_flight_`
ratchets up; once it exceeds `(depth-1)*k` permanently, the release gate closes
**forever** and the scheduler silently stops releasing.

The only recovery is the watchdog, and its cure is blunt: after
`max(10*s_hat, 2000)` ms of silence it **resets** `in_flight_` to 0 and clears
the release FIFO rather than reconciling them. Throughput returns; accurate
accounting does not, and the run stalls for at least 2 s. The ledger's
`arrivals == admitted + drops` identity survives, because it is maintained on the
arrival and admit paths rather than here. **Treat a `[vista] WATCHDOG` line in
stderr as invalidating the run.**

No archived run triggered it: every buffer reaching the tracker's src pad in a
DeepStream pipeline carries batch meta. The null case is reachable if the
completion probe is attached upstream of `nvstreammux`, or to a branch carrying
non-batched buffers — which `SchedCfg::tracker_name` makes configurable, and
therefore possible to get wrong. Not fixed here because changing the credit path
would alter behaviour relative to the paper's binary (`vista/PAPER_DIFF.md`).

### The default streammux INI is a fail-open path

`app/src/app_config.cpp` throws when an **explicitly configured** streammux INI
is missing, but when the INI is only the *default* it clears the path and runs on
the mux's built-in defaults with a warning — not the batching settings the
campaign used — and still produces plausible numbers. Keep the fallback string in
`app_config.cpp` in sync with the shipped filename in `config/`. `mux_sched.txt`
keeps its name for the same class of reason: `app/src/main.cpp` resolves it by
hardcoded basename and throws if it is absent, so renaming it breaks every
`--sched` run.
