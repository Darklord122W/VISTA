# harness/ — the VISTA experiment runner

Every campaign behind the paper's tables, as scripts rather than as shell
history. Each script states what it runs, where it lands, which table it feeds,
and what it costs in wall-clock time.

**This directory is how you get data.** No run data ships with this repository,
so this is not a "reproduce the archive" harness — it is the experiment itself,
runnable on your own Jetson and your own clips. What it produces is what
`analysis/` and `figures/` then read, via `$VISTA_DATA_ROOT`.

Read [Before you run anything](#before-you-run-anything) first. Two of the
invariants below fail *silently*, and one flag means two unrelated things.

---

## Quick start

```bash
# 1. Point the harness at your tree (defaults assume this repository).
export VISTA_ROOT=/path/to/app-checkout      # holds the binary, config/, models/
export VISTA_CLIPS=/path/to/clips            # NOT shipped — see "Clips"
export VISTA_RESULTS=$PWD/runs               # where runs land

# 2. Prove the environment and the scheduler's invariants (~3 min).
./run_gates.sh

# 3. Run the headline campaign (~36 min + a one-time ~37 min engine build).
./run_campaign.sh core

# 4. Score what you produced.
export VISTA_DATA_ROOT=$VISTA_RESULTS
python3 ../analysis/make_all.py --tier rescore
```

`$VISTA_RESULTS` and `$VISTA_DATA_ROOT` are the same directory seen from the two
halves: the harness writes it, the analysis reads it. The analysis half has no
default for `$VISTA_DATA_ROOT` and will say so rather than score an empty tree.

Two things here read runs instead of producing them, so they need a data root
but no GPU and no clips:

```bash
./run_gates.sh --analyze-only runs/gates   # assert G0-G4 over gate runs you have
python3 verify_reconstruction.py           # compare rebuilt commands against run_meta.json
```

Neither can do anything in a bare clone: the runs they read are measurements,
and measurements are what this repository does not distribute.

---

## Before you run anything

### The GPU clock is asserted, not recorded

Every latency in the paper was measured on a Jetson AGX Orin 64GB at MODE_30W
with the GPU pinned to **612 MHz**. The original harness *recorded*
`gpu_clock_hz` in `run_meta.json` and never checked it, so an unpinned clock
produced different numbers with perfectly valid-looking metadata. Every script
here now calls `vista_assert_gpu_lock` before doing any work.

The check is `min_freq == max_freq == 612000000`, not `cur_freq == 612000000`: a
GPU merely *idling* at 612 MHz is one load spike away from boosting mid-run.

```bash
sudo nvpmodel -m 2 && sudo jetson_clocks     # lock
cat /sys/devices/platform/17000000.gpu/devfreq/17000000.gpu/cur_freq
```

To run on other hardware anyway — results will not be comparable to the paper:

```bash
export VISTA_ALLOW_UNLOCKED_GPU=1
```

### `--gap-every` is overloaded. This is the sharpest edge here.

`--gap-every N` = *drop 2 consecutive frames every N frames, per camera*. The
keep fraction is `(N-2)/N`. It is used for two unrelated purposes:

| Value | Meaning | Used by |
|---|---|---|
| **44** | **Timing fidelity.** Makes the delivered rate match the live rig (~29.8 fps): 42 of 44 frames survive at the C920's true 32.026 ms cadence. | every campaign except Static-Decimation |
| **3** | **An experiment.** DEC-1/3 — keep 1 of 3. The Static-Decimation baseline. | `run_baselines.sh decimation` (Table II row 3) |
| **4** | **An experiment.** DEC-1/2 — keep 2 of 4. A diagnostic, *not* a table row. | `run_baselines.sh dec12` |

**The application's own `--help` says `measured live: ~70`. Do not use 70.**
The app's `pipeline_builder.cpp` documents why, and contradicts its own help
text: at 70/275 the emulated grid drifts the *wrong way* against real time,
frames look future-stamped, nothing is ever late, and sync-on trivially
"succeeds" — a pure artifact. 44 is the corrected value and the one every run
behind the paper used.

> **Flagged, not fixed.** The misleading `~70` string is still in
> `app/src/main.cpp`'s usage text in this repository. Correcting it is an
> application change, outside this directory. The harness never passes 70, and
> `vista_env.sh` pins `VISTA_GAP=44`.

### The DEC naming trap

Two directories differ by one character and by ~930 ms of latency:

| Directory | Flag | Policy | Is it Table II? |
|---|---|---|---|
| `e3_m_decimate3_r*` | `--gap-every 3` | DEC-1/3 (keep 1 of 3) | **Yes** — Static-Decimation, ~64 ms |
| `e3_m_decimate_r*` | `--gap-every 4` | DEC-1/2 (keep 2 of 4) | No — diagnostic, ~997 ms (stalest measured) |

Confusing them moves a point on Fig. 3 across the plot with no error anywhere.
DEC-1/2 is not run by default.

### Result directory names are load-bearing

The names on disk are the *old, internal* ones and several are inconsistent
(`e7_surfcal_2` / `e7_s2_r1` / `e7_s2_r2` are three repeats of one arm). The
analysis keys on them. **Do not rename directories to match the paper's policy
names** — map names in a registry instead. These scripts reproduce the paper's
directory names exactly, warts included, so a fresh campaign is directly
comparable to the published one.

| Paper name | Directory / arm |
|---|---|
| Stock-Default | `fifo33` |
| Stock-LiveDepth | `e7_surfcal_2`, `e7_s2_r*` (`--replay-surfaces 2`) |
| Static-Decimation | `e3_m_decimate3_r*` (`--gap-every 3`) |
| VISTA-Fresh | `fresh-k2` |
| VISTA-Activity | `imp-k2` (Table II row 5 comes from `e8_impfix_r*`) |
| all-admit ablation | `fresh-k4` |
| diagnostics | `fifo5`, `dropold`, `e3_m_decimate_r*` |

### Clips, weights and engines are not in this repository

- **Clips**: office footage of an identifiable person. Not shipped, and not
  blurrable — blurring changes what YOLO detects, and the oracle every recall
  number is scored against is built from what YOLO detects. The activity-skew
  clips are composited from NVIDIA sample streams (EULA); rebuild those with
  `scripts/make_skew_clips.py`. **The paper's replay campaigns are therefore
  not reproducible from this repository at all.** What is reproducible is the
  method, on your own footage: these scripts, that scheduler, your clips, your
  numbers.
- **Weights / TensorRT engines**: not shipped (6.4 GB, and engines are
  hardware- and TensorRT-version-specific — an engine built elsewhere is
  *invalid*, not merely slow). See `docs/reproduction/` for the download and
  `build_engine` recipes.

### There is no archive here to compare against

`VISTA_RESULTS` defaults to `<repo>/runs`, which `.gitignore` excludes. Earlier
revisions of this repository shipped the paper's runs read-only and warned you
not to overwrite them; they are no longer distributed, so every number you get
from this harness is one you measured. The paper's values are in the paper, and
`analysis/campaigns.yaml` maps each of its rows to the arm that produces it.

---

## Campaigns

Runtimes are **measured** on the authors' Jetson AGX Orin at MODE_30W, by
summing `wall_s` across the `run_meta.json` files for exactly that scope —
except where marked *estimated*. They are what to budget, not a promise about
your board. Add a one-time **~37 min** YOLO11x engine build to anything using
the oracle.

| Script | Campaign | Output dirs | Paper | Runtime |
|---|---|---|---|---|
| `run_gates.sh` | G0–G4 implementation gates (yolo11n, 25 s) | `gates/` | §gates | ~3 min *(est.)* |
| `run_capacity.sh` | E1 deadline×model sweep | `e1_yolo11{n,s,m,l}/` | **Table I** | ~25 min *(est.)* |
| `run_campaign.sh core` | oracle + ref_m + 7 arms × 5 repeats | `oracle_x/`, `ref_m/`, `e3_m/` | **Tables II/III** | **36.3 min** |
| `run_campaign.sh full` | core + refs/arms at s,l + E6 | `+ ref_{s,l}/`, `e3_{s,l}/`, `e6_off*/` | Tables II/III, Fig. offsets | **89.9 min** |
| `run_baselines.sh livedepth` | `--replay-surfaces 2` + surfaces-4 control | `e7_surfcal_2/`, `e7_s2_r{1,2}/`, `e7_surfcal_4*/` | **Table II row 2** | **5.4 min** |
| `run_baselines.sh decimation` | `--gap-every 3` (DEC-1/3) | `e3_m_decimate3_r{0,1,2}/` | **Table II row 3** | **2.7 min** |
| `run_baselines.sh dec12` | `--gap-every 4` (DEC-1/2) | `e3_m_decimate_r{0,1,2}/` | diagnostic | **2.7 min** |
| `run_importance_fix.sh imp` | post-bugfix `imp-k2` | `e8_impfix_r{0,1,2}/` | **Table II row 5** | **2.7 min** |
| `run_importance_fix.sh salv` | post-bugfix `salv-k2` | `e8_salvfix_r{0,1,2}/` | not in paper | **2.7 min** |
| `run_skew_study.sh oracles` | YOLO11x oracles for both skew clip sets | `charBrief_x/`, `charImp_x/` | **Table IV** (prereq) | **7.0 min** |
| `run_skew_study.sh arms` | the 8 activity-skew arms | `briefD2ctl_*`, `briefS2_*`, `impcmp_*`, `persS2_*` | **Table IV** | **17.6 min** |
| `run_ablations.sh depth` | `--sched-depth 1` vs 2 (+ `_v2` replication) | `e9_depth{1,2}[_v2]/` | ablation | **12.4 min** |
| `run_ablations.sh enriched` | exact emission stamps | `enriched_m_{fifo,imp,salv}/` | TTA validation | **2.7 min** |
| `run_ablations.sh offset` | E6 offset robustness | `e6_off{0,0.33,0.66,1.0}_*/` | offsets | **13.8 min** |
| `run_ablations.sh impdiag` | importance-weight probes | `impdiag_{d1,heavy}/` | diagnostic | **1.5 min** |
| `run_ablations.sh briefdepth1` | depth-1 arms on brief clips | `brief_{fresh,imp}-k2_r*/` | diagnostic | **4.4 min** |
| `run_live.sh` | 3 arms × 120 s on 4 physical C920s | `e4_live/{fifo33,imp,salv}/` | **Table V** | ~7 min |

`run_campaign.sh full` = 89.9 min is corroborated twice: by summing `wall_s`
(89.9 min) and by the authors' `LOG.md` bracket 19:00:41 → 20:30:40 (89.98 min).
The original header's "~2.5 h" appears to have folded in the engine build
(18:22:25 → 18:59:16 = 36 min 51 s) and a first aborted attempt.

### Commands

```bash
./run_gates.sh                       # or: --analyze-only [DIR]
./run_capacity.sh                    # or: ./run_capacity.sh m l
./run_campaign.sh core               # or: full
./run_baselines.sh                   # all | livedepth | decimation | dec12
./run_importance_fix.sh              # all | imp | salv
./run_skew_study.sh                  # all | oracles | arms
./run_ablations.sh                   # all | depth | enriched | offset | impdiag | briefdepth1
./run_live.sh                        # all three arms; --check for preflight only
```

All campaigns are **idempotent**: a run whose `metrics.csv` exists is skipped, so
an interrupted campaign resumes by re-running the same command. Delete an output
directory to force it.

---

## Files

| File | What |
|---|---|
| `vista_env.sh` | Every path and platform invariant. Sourced by all scripts; kills the hardcoded `/home/vista` paths. Defines the assertions. |
| `run_eval.py` | The driver: builds each arm's command, runs it, writes `run_meta.json`. |
| `run_gates.sh` | G0–G4, asserted. |
| `run_campaign.sh` | `core` / `full`. |
| `run_capacity.sh`, `run_baselines.sh`, `run_importance_fix.sh`, `run_skew_study.sh`, `run_ablations.sh`, `run_live.sh` | Reconstructed campaigns (see below). |
| `timeout_sweep_cpp.py` | E1 sweep. Copied from the application tree; only path resolution changed. |
| `verify_reconstruction.py` | Proves the harness still rebuilds the paper's commands, given the runs. No GPU. |

### Arms (`run_eval.py --arm`)

| Arm | Flags | Is |
|---|---|---|
| `fifo33` | `--timeout-us 33333` | Stock-Default |
| `fifo5` | `--timeout-us 5000` | diagnostic |
| `dropold` | `--timeout-us 33333 --dropold` | keep-newest config baseline (gate G4) |
| `fresh-k2` | `--sched fresh --sched-k 2` | **VISTA-Fresh** (paper default) |
| `fresh-k4` | `--sched fresh --sched-k 4` | all-admit ablation |
| `imp-k2` | `--sched imp --sched-k 2` | **VISTA-Activity** |
| `salv-k2` | `--sched salvage --sched-k 2` | not evaluated in the paper |
| `ref` | `--timeout-us 33333`, `--ring 0`, no `--duration` | completeness reference (oracle input) |

Defaults, unless an arm overrides them: K=2, depth=2, stash=1,
w = 0.40/0.35/0.25, τ_max = 150 ms.

---

## Provenance: what changed and why

`run_meta.json` used to record `git rev-parse HEAD` and nothing else about the
code. That is not the identity of what ran — **if the working tree is dirty,
HEAD describes code that was never executed.** This is not hypothetical: three
of the paper's campaigns record a SHA that provably cannot produce them, because
the flag they pass (`--sched-stash`) exists in no commit of the application's
history. The metadata looked authoritative and was wrong.

`run_eval.py` now records, per run:

| Field | Why |
|---|---|
| `app_sha256` | The hash of the binary that actually ran. The only unambiguous identity, dirty tree or not. |
| `git.describe` | `git describe --always --dirty`. The `-dirty` suffix is the part that matters. |
| `git.dirty_files` | *Which* files were uncommitted, so a reader can judge. |
| `git.sha`, `git.branch` | Kept — but as weak signals, not as the answer. |
| `gpu_locked` | Whether the clock was pinned, alongside `gpu_clock_hz`. |
| `engine_sha256_16` | The detector engine, unchanged from before. |

Verified on the application tree: `describe` → `370c7ef-dirty`, `dirty_files` →
the three known-modified files. The technique is lifted from the weight-sweep
study's own `run_plan.py`, which already hashed the binary; that study's harness
was part of the measurement archive and is not distributed here.

All git calls are read-only (`describe`, `rev-parse`, `status`). The harness
never writes to the repository it describes.

---

## Reconstructed campaigns — the honesty note

Six of these scripts **did not exist**. Only `run_campaign.sh`, `run_gates.sh`
and `run_eval.py` survive in the paper's harness; the rest of the study existed
solely as invocations. They are reconstructed from the `cmd` arrays inside each
`run_meta.json` the original runs wrote, which record the full argument vector.

`verify_reconstruction.py` checks that claim mechanically — it rebuilds each
command and compares it token-for-token against the `run_meta.json` of the run
it claims to reproduce. Against the authors' archive it reported **29 match, 0
differ**, covering every reconstructed campaign. That archive is not distributed
here, so the check needs `$VISTA_DATA_ROOT` to point at those runs; with a root
in which no case has a `run_meta.json` it reports that it verified **nothing**
and exits non-zero, rather than passing over an empty fixture.

**`run_live.sh` is the exception, and the least certain file in this
directory.** The `e4_live/` runs carry no `run_meta.json` at all; the only
surviving record is the banner at the top of each `stderr.log`. It reproduces
everything those banners record, but a flag that left no trace in a banner would
be invisible. Treat it as a faithful reconstruction, not as the original.

---

## Gates

`run_gates.sh` asserts. The original printed a table and sentences like *"G1
PASS if fill dist for sched runs is a spike at K"*, then always exited 0 — so
`run_gates.sh && run_campaign.sh` would proceed over a failed gate and CI could
never catch a regression.

The `Reference` column records what the authors measured, for comparison. Your
gate runs must satisfy the assertion; they will not match these to the digit.

| Gate | Asserts | Reference |
|---|---|---|
| **G0** | Binary present; GPU pinned at 612 MHz; clips and engine present. | — |
| **G1** | Batch atomicity: ≥98% of batches carry exactly K frames. | 99.93% (K=2), 99.84% (K=4) |
| **G2** | Drop-ledger closure: `admitted_fresh + admitted_salvage + policy_drops == arrivals`. | delta 0 on all three scheduler runs |
| **G3** | Salvage pairs survive NvSORT: detections present, no ERROR lines. | 2869 records, 3939 detections |
| **G4** | The keep-newest config baseline is inert (<5% difference vs untouched path). | 0.08% |

**G2 parses stderr, not the CSV.** `drops_cum` reads `0` in all five gate CSVs —
it counts the mux's dropped-buffer signal, which never fires, because the
scheduler drops *upstream* of the mux. Reading `drops_cum` would make G2 a
tautology that passes on a broken scheduler.

**G2 accepts both `[sched]` and `[vista]` prefixes.** The paper's runs came from
a binary that printed `[sched]`; the module here prints `[vista]`.
The rest of the line is byte-identical, so one regex covers both. An *unknown*
prefix fails as "cannot verify closure" rather than passing silently.

Related rename hazards, for anyone touching identifiers: the name SPARQ never
became a code identifier, but it survives in the pthread name `sparq-sched`,
which is what the paper's binary called its scheduler thread. The module in this
repository names that thread `vista-sched`.

> **A live hazard whose instrument is not here.** The weight-sweep study
> measured the scheduler's CPU overhead by finding that thread *by name*, and it
> is the only evidence for the paper's CPU-overhead claim. Its harness shipped
> with the measurement archive and is therefore no longer in this repository.
> The hazard is worth knowing anyway, because anything that finds a thread by
> name inherits it: a probe looking for `sparq-sched` finds nothing in the
> module shipped here and reports `-1` — it fails to a sentinel, not to an
> error. If you write such a probe, accept both spellings.

---

## Known discrepancies this directory touches

Documented, not silently fixed. The paper is the author's to change; see the
repository's claim→evidence matrix.

1. **Table II's provenance labels are wrong; its numbers are not.**
   `recall_m2.json` labels the Table II source rows `e3_m/imp-k2_r{0,1,2}`, but
   those values are byte-identical to `e8_impfix_r{0,1,2}` — the correct
   post-bugfix runs — and `e3_m/fifo-s2_r*` does not exist as a directory. A
   labelling defect, **not fabricated data**. The distinction matters.
2. **`e3_m_aggregate.json` is stale.** It holds pre-importance-bugfix `imp-k2`
   aggregates (cov 38.8%, e2e 94.6 ms) that contradict Table II in every cell.
   The paper correctly does not use it. It does not ship; the hazard is recorded
   in `analysis/campaigns.yaml`'s `superseded:` block, because scoring the
   authors' `e3_m/` runs rebuilds exactly those numbers.
3. **Tables II/III and Table IV use different metrics.** II/III report
   `tta_recall` (emission time); Table IV reports `event_recall` (onset). Both
   live in the same JSONs. For the brief clips, `event_recall@250` median = .723
   vs `tta_recall@250` median = .682. The abstract's headline 0.30 → 0.71 is the
   onset metric.
4. **Table IV's stash-2 rows never had a scoring output.** No analysis JSON in
   the authors' archive references `briefS2`/`persS2`/`briefD2ctl`; those
   directories postdate the newest analysis JSON. `make_table4.py` therefore
   rescores from raw detections always, and has no derived tier.
5. **The oracle could silently degrade.** `run_campaign.sh` used to fall back
   from YOLO11x to YOLO11l with only a `WARNING` if the engine build exceeded
   `timeout 3600`, changing the ground-truth event set and every recall number.
   This one matters to you, not just to the paper: it is a live failure mode of
   the campaign you are about to run.
   **Now fatal, with no fallback.** Margin note: the measured build is 36 min
   51 s against that 3600 s timeout — 61% of the budget, so a ~1.6× slower
   machine would have tripped it. The default is now `VISTA_ENGINE_TIMEOUT=7200`.

Leads recorded elsewhere and **not independently re-derived here**: the
published **7%** throughput tax appears to be the `fresh-k4` all-admit
ablation's (6.8%) rather than the recommended VISTA-Fresh's (≈ 14.7% at the
YOLO11m load point the draft names); and "63 manually verified clean events" —
`clean_events.py` classifies 100% automatically via a 16-class blocklist plus an
IoU ≥ 0.3 reincarnation heuristic. Both are still live against the current
draft and are recorded in [`../KNOWN-ISSUES.md`](../KNOWN-ISSUES.md).

---

## Parsing the outputs

Two traps, both of which will bite a naive reader:

- **`dets.jsonl` is not valid JSONL.** The application shares stdout with
  GStreamer, which injects lines like `Opening in BLOCKING MODE` (measured: 15
  of 2884 in one gate run). **Skip any line not starting with `{`.**
- **Archived detections are gzipped** (`dets.jsonl.gz`; 212 MB → 67 MB). Fresh
  runs write plain `dets.jsonl`. **Any parser must handle both** — use
  `gzip.open` when the path ends in `.gz`, else `open`.
- **`e2e_ms` carries negative sentinels** for frames with no valid stamp. Filter
  `>= 0`.

---

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VISTA_ROOT` | this repository | Tree holding the binary, `config/`, `models/` |
| `VISTA_BIN` | `$VISTA_ROOT/app/vista_multicam` | The experiment application |
| `VISTA_CONFIG` | `$VISTA_ROOT/config` | nvinfer / mux / camera configs |
| `VISTA_CLIPS` | `$VISTA_REPO/clips` | Replay clip sets (not shipped; yours) |
| `VISTA_RESULTS` | `$VISTA_REPO/runs` | Where runs land (git-ignored) |
| `VISTA_DATA_ROOT` | *(unset — required)* | Runs to READ, for `--analyze-only` and `verify_reconstruction.py`. Normally `$VISTA_RESULTS`. |
| `GPU_SYSFS` | `/sys/devices/platform/17000000.gpu/devfreq/17000000.gpu` | GPU devfreq node |
| `VISTA_GPU_EXPECT_HZ` | `612000000` | The clock the paper was measured at |
| `VISTA_ALLOW_UNLOCKED_GPU` | unset | `1` downgrades the clock assert to a warning |
| `VISTA_ENGINE_TIMEOUT` | `7200` | Seconds allowed for the oracle engine build |
| `VISTA_SKEW` | `0,1134.8,1702.1,567.2` | Measured live startup stagger (ms) |
| `VISTA_RATE` | `0.96063,0.96099,0.96087,0.96128` | Per-camera PTS rate factors |
| `VISTA_GAP` | `44` | See the overload table above. Not 70. |
| `VISTA_RING` | `4` | Kernel-ring stand-in depth; 0 for references |
