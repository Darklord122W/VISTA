# CLI reference — the reference application

Every flag the reference app accepts, its default, and its unit. Taken from
`parse_args()` in the app's `main.cpp`, cross-checked against the effective
defaults in `app_config.cpp` (a CLI flag left unset falls back to the YAML, and
the YAML key left unset falls back to a code default — this page gives the
**effective** default).

The app's own `--help` is a good summary but is **not** authoritative on two
points, flagged below: `--gap-every`'s recommended value, and two flags it does
not mention at all.

Contents:

- [General](#general)
- [Source selection](#source-selection)
- [Replay-skew injection](#replay-skew-injection)
- [The batcher](#the-batcher)
- [The detector](#the-detector)
- [Output and instrumentation](#output-and-instrumentation)
- [The VISTA scheduler](#the-vista-scheduler)
- [Baselines](#baselines)
- [`--gap-every` has two meanings](#--gap-every-has-two-meanings)
- [Mutual exclusions and hard errors](#mutual-exclusions-and-hard-errors)

---

## General

| Flag | Default | Unit | Meaning |
|---|---|---|---|
| `--config PATH` | `config/camera_params.yaml` | path | The YAML config. See [`04-config-reference.md`](04-config-reference.md). |
| `--duration SECS` | `0` (= run until EOS or Ctrl-C) | s | Stop cleanly after this long. Sends EOS rather than quitting, so sinks and metrics finalise. |
| `--display` | off | — | Live tiled window with boxes, labels and track IDs. |
| `--debug` | off | — | Shorthand for `--display --log human`. |
| `-h`, `--help` | — | — | Print usage and exit 0. Needs no cameras, clips or model. |

Signals: the **first** Ctrl-C sends EOS so recordings and metrics finalise; the
**second** quits immediately.

## Source selection

| Flag | Default | Unit | Meaning |
|---|---|---|---|
| `--source v4l2\|file` | `v4l2` | enum | Live cameras, or deterministic file replay. |
| `--replay-dir DIR` | `experiments/clips` | path | Per-camera clips `cam0.mp4 .. cam<N-1>.mp4`, for `--source file`. Relative paths resolve against the **parent** of the config file's directory. |

**Every policy-comparison number in the paper comes from `--source file`.** Live
runs are the transfer check.

## Replay-skew injection

`--source file` only. These reproduce the live rig's timing imperfections on
recorded clips; a plain `--source file` run without them is an idealised replay
that does not behave like the rig. Passing any of them with `--source v4l2`
is a hard error.

| Flag | Default | Unit | Meaning |
|---|---|---|---|
| `--skew-ms a,b,c,d` | all `0` | ms | Per-camera start delay (USB enumeration stagger). Exactly one value per camera. |
| `--rate r0,r1,r2,r3` | all `1.0` | factor | Per-camera PTS rate factor. `PTS' = PTS*rate + skew`. `0.9608` turns a nominal-30 fps clip into the C920's true ~32.0 ms cadence; small per-camera differences simulate crystal drift. Exactly one value per camera. |
| `--gap-every N` | `0` (off) | frames | Drop **2 consecutive frames every N** per camera, phase-staggered per camera. **Two distinct uses — see below.** |
| `--ring N` | `0` (off) | buffers | Bounded **drop-newest** queue after the pacer: the v4l2 kernel-ring stand-in. Live rig: `4`. |
| `--replay-surfaces N` | `20` | surfaces | `nvv4l2decoder num-extra-surfaces`. Sets how deep a FIFO backlog replay can hold. `2`–`4` emulates live queue depth. |
| `--restamp` | off | — | Emulate the **unfixed** jpegparse: rewrite PTS onto a synthetic per-camera 33.333 ms grid counting only survivors. Default off = the batcher sees true pacing timestamps. |
| `--no-restamp` | — | — | Force off. **Accepted but absent from `--help`.** |

The values the paper's campaign used (`harness/vista_env.sh` carries them, and
every run records its own argv in `run_meta.json`):

```
--skew-ms 0,1134.8,1702.1,567.2  --rate 0.96063,0.96099,0.96087,0.96128
--gap-every 44  --ring 4
```

**`--replay-surfaces` is not cosmetic.** The decoder's default pool (~5
surfaces) is smaller than the backlog a congested batcher creates. Without
headroom the *pool*, not the ring, becomes the throttle: the pacer starves and
the lateness accumulated during the stagger window freezes in permanently
(measured: a constant ~938 ms pacing error). Extra surfaces keep the pacer
honest and move the drop decision to the ring, where it belongs. The cost is
that they also deepen the FIFO backlog replay can hold — which is why the
live-depth baseline (Stock-LiveDepth) is `--replay-surfaces 2`.

## The batcher

| Flag | Default | Unit | Meaning |
|---|---|---|---|
| `--sync` | off | — | `nvstreammux sync-inputs=1` (time-align across cameras; late frames are dropped). |
| `--no-sync` | — | — | Force the baseline (`sync-inputs=0`), overriding the YAML. |
| `--max-latency-ms N` | `33.333` (YAML `max_latency_ns: 33333333`) | ms | Sync-on **only**: extra wait for a late frame. Ignored when `sync-inputs=0`. |
| `--timeout-us N` | `33333` | µs | `batched-push-timeout`. **Measured inert on DS 7.1** — see below. |
| `--mux-config PATH` | `config/mux_default.txt` (YAML), or `config/mux_sched.txt` under `--sched` | path | The new-mux INI. `none` runs on the mux's built-in defaults. |

> **`--timeout-us` does nothing on DS 7.1.** Measured over an 8-run matrix from
> 1 ms to 100 ms, with and without an INI: fill and batch rate are identical.
> The property and the INI's `overall-min-fps` are the same internal knob, and
> the mux re-reads its INI at state change — *after* any property the app sets.
> The flag is kept because the value still lands in `metrics.csv`'s
> `timeout_us` column as the run's *intended* deadline. To actually vary the
> deadline, generate an INI with the anchors you want.

> **`--mux-config` under `--sched`.** If you do not pass it, the app looks for
> `mux_sched.txt` next to the run's mux INI and **hard-errors if it is
> absent**. It does not fall back to the baseline INI, because the baseline INI
> would break batch atomicity silently. See
> [`docs/integration/03-pipeline-obligations.md`](../integration/03-pipeline-obligations.md).

## The detector

| Flag | Default | Unit | Meaning |
|---|---|---|---|
| `--pgie-config PATH` | `config/pgie_yolo11n.txt` (YAML `pgie.config_file`) | path | The nvinfer config; selects the detector. `config/pgie_yolo11{n,s,m,l,x}.txt` are the campaign's five load points. |
| `--no-pts-fix` | fix is **on** | — | Disable the jpegparse PTS-restore fix (live MJPG sources). |
| `--pts-fix` | — | — | Force it on. **Accepted but absent from `--help`.** |

**What the PTS fix does.** `jpegparse` (GStreamer 1.20) re-stamps every output
buffer onto an ideal `first_pts + n/framerate` grid anchored at *that camera's*
first frame, destroying the kernel capture stamp — the only true capture time a
USB camera provides. The four cameras' grids then disagree by a constant
1.05–1.47 s. The fix straddles `jpegparse` with two probes: queue the true PTS
on the way in, restore it on the way out. Default **on**; `--no-pts-fix` exists
for the A/B.

This does not affect VISTA, which never schedules on PTS. It affects anything
that compares timestamps across cameras — which is why `--sync` without the fix
is catastrophic (14.7% of frames kept) and with it is merely expensive (99.9%
kept).

## Output and instrumentation

| Flag | Default | Unit | Meaning |
|---|---|---|---|
| `--log MODE` | `json` (or `human` with `--debug`) | `json`\|`human`\|`none` | Console detection output. `json` on **stdout** is what the harness captures as `dets.jsonl`. |
| `--metrics-csv PATH` | off | path | Per-batch latency/throughput CSV. Parent directories are created. |
| `--record PATH` | off | path | Record the annotated tiled view to an H.264 MP4. Parent directories are created. |

Schemas: [`05-outputs.md`](05-outputs.md).

## The VISTA scheduler

| Flag | Default | Unit | `SchedCfg` field |
|---|---|---|---|
| `--sched MODE` | `off` | `off`\|`fresh`\|`imp`\|`salvage` | `mode` |
| `--sched-k N` | `2` | frames per release | `k` |
| `--sched-depth N` | `2` | batches in flight | `depth` |
| `--sched-stash N` | `1` | frames per camera | `stash` |
| `--sched-tau-max MS` | `150` | ms | `tau_max_ms` |
| `--sched-tau-salvage MS` | `250` | ms | `tau_salvage_ms` |
| `--sched-w F,I,R` | `0.40,0.35,0.25` | weights | `w_fresh,w_imp,w_fair` |
| `--sched-imp-halflife S` | `2.0` | s | `imp_halflife_s` |
| `--sched-imp-max F` | `2.0` | activity events | `imp_max` |
| `--sched-retention F` | `0.30` | score in [0,1] | `retention_thresh` (salvage only) |
| `--sched-csv PATH` | off | path | `decision_csv` |
| `--sched-csv-drops` | off | — | `log_drops` |

The last four have **no equivalent in the paper's binary** — they expose
`SchedCfg` fields that were compile-time constants there. Their defaults are
the paper's values, so leaving them alone reproduces the paper's behaviour.

- **`--sched-imp-max`**: raising it toward the v1 value (10) turns the activity
  term into a constant on any scene holding standing objects. See
  [`06-tuning.md`](06-tuning.md).
- **`--sched-csv-drops`**: default off is paper-identical (`sched.csv` records
  admissions only). Drop rows add I/O on the arrival path of a timing-sensitive
  scheduler. The summary's drop counters are exact either way.

Mode names vs the paper's policy names:

| Flag | Paper |
|---|---|
| `--sched fresh --sched-k 2` | **VISTA-Fresh** (the general-purpose configuration) |
| `--sched imp --sched-k 2` | **VISTA-Activity** (the optional activity extension) |
| `--sched fresh --sched-k 4` | the all-admit ablation |
| `--sched salvage` | **not evaluated in the paper.** See [`06-tuning.md`](06-tuning.md). |

Turning `--sched` on has four side effects in the app, and you want all of
them: mux and nvinfer `batch-size` → `k`; `nvvideoconvert output-buffers` → 12;
`sync-inputs` refused; the scheduler mux INI substituted. Those are host
obligations 1, 5, 4 and 2.

> **If you are reading older material about this project**, the scheduler
> predates the name VISTA. The flags never changed (`--sched*` throughout), but
> output produced by the pre-rename binary carries the old spelling: its stderr
> ledger line is prefixed `[sched]` where this module prints `[vista]`, and its
> scheduler thread carries the former name. Parsers must accept both spellings;
> see [`05-outputs.md`](05-outputs.md) and [`../../NAMING.md`](../../NAMING.md).

## Baselines

| Flag | Default | Meaning |
|---|---|---|
| `--dropold` | off | Keep-newest config baseline: a per-camera 1-deep `leaky=downstream` queue before the batcher. No scheduler. |

`--dropold` is the *config-only* alternative to a scheduler, and it is in the
paper as a diagnostic for a reason: it lands within noise of the stock default
(856 vs 857 ms mean age). Keep-newest acts upstream of where the standing queue
forms, so it cannot change which frames are lost.

---

## `--gap-every` has two meanings

The same flag serves two unrelated purposes, and confusing them moves a point
on Fig. 3 by roughly 930 ms with no error message. **Always know which one you
are using.**

The mechanic: `--gap-every N` drops **2 consecutive frames out of every N**, per
camera, phase-staggered by camera index. So it keeps `(N-2)/N` of frames.

| `N` | Keeps | Purpose |
|---|---|---|
| `0` | everything | Off (default). Idealised replay. |
| `3` | **1 in 3** | **Static-Decimation** — a policy under test (ρ = 0.62, safely under capacity). The obvious hand-tuned remedy. On disk: `e3_m_decimate3*`. |
| `4` | **1 in 2** | The **DEC-1/2 diagnostic** (ρ = 0.93): decimation just under capacity, which never drains its startup backlog — **997 ms mean age, the stalest configuration measured**. On disk: `e3_m_decimate*`. |
| `44` | ~95.5% | **Timing fidelity, not a policy.** Combined with `--rate ~0.961` this reproduces the rig's measured ~29.8 fps/camera delivered rate. This is what every non-decimation run uses. |

> **The disk-name trap.** `e3_m_decimate` (`--gap-every 4`) is **DEC-1/2**, the
> 997 ms stalest configuration. `e3_m_decimate3` (`--gap-every 3`) is
> **Static-Decimation**, 64 ms. The names differ by one character and the
> results differ by ~930 ms. Result directories keep their original names on
> purpose — renaming them would silently break analysis — so the mapping lives
> in the registry, not in the filenames.

> **The `--help` text is wrong here.** It says *"kernel capture gaps; measured
> live: ~70"*. Do not use 70. That figure comes from a naive modal-cadence
> derivation; at 70 (or 275) the delivered rate does not match live, the
> emulated PTS grids' drift **flips sign** against real time, frames look
> future-stamped, nothing is ever LATE, and sync-on trivially "succeeds" — a
> pure artifact. The correct value for the 2026-07-07 reference rig is **44**,
> which is what the campaign used and what every `run_meta.json` records.

> **`--gap-every 1` and `--gap-every 2` drop every frame.** The test is
> `(idx + phase) % N < 2`, which for `N <= 2` is always true. There is no
> guard. You will get a pipeline that produces nothing.

---

## Mutual exclusions and hard errors

The app fails fast, before touching GStreamer, with an actionable message and
exit code 2.

| Condition | Message |
|---|---|
| `--sched` + `--dropold` | `--sched and --dropold are mutually exclusive.` |
| `--sched` with `sync_inputs` set | `--sched requires sync-inputs=0 (the scheduler replaces alignment).` |
| `--sched` without the scheduler INI present | `scheduler mux INI not found: <path>` |
| Replay knobs with `--source v4l2` | `--skew-ms/--rate/--gap-every/--ring/--restamp only apply to file replay (--source file).` |
| Wrong number of `--skew-ms` / `--rate` values | `--skew-ms / --rate need exactly one value per camera (N configured).` |
| Bad mode string | `--sched must be off\|fresh\|imp\|salvage, got '<x>'.` |
| `--sched-w` not three numbers | `--sched-w expects F,I,R (three numbers).` |
| A non-numeric value | `--duration: expected a number, got 'abc'.` |
| A flag missing its value | `--config needs a value.` |
| An unknown flag | usage, then `unknown argument: <x>` |
| A missing camera device | Lists the devices that *are* present. |
| A missing replay clip | Names the clips and how to record them. |

Exit codes: `0` clean, `1` pipeline/bus error, `2` configuration error.

---

## Worked invocations

**Reproduce a VISTA-Fresh replay run** (the campaign's actual arm, modulo
paths):

```bash
./app/vista_multicam --config config/camera_params.yaml \
  --source file --replay-dir <clips> \
  --pgie-config config/pgie_yolo11m.txt \
  --skew-ms 0,1134.8,1702.1,567.2 --rate 0.96063,0.96099,0.96087,0.96128 \
  --gap-every 44 --ring 4 --no-sync \
  --log json --metrics-csv out/metrics.csv \
  --sched fresh --sched-k 2 --sched-csv out/sched.csv \
  --duration 52.0 > out/dets.jsonl 2> out/stderr.log
```

**The stock baseline** (Stock-Default) is the same command with the `--sched*`
flags removed and `--timeout-us 33333 --mux-config <generated push INI>` added.
**Stock-LiveDepth** adds `--replay-surfaces 2`. **Static-Decimation** is the
stock baseline with `--gap-every 3` instead of `44`.

**A smoke test** (no scheduler, no cameras, 8 s):

```bash
./app/vista_multicam --config config/camera_params.yaml --source file --log human --duration 8
```
