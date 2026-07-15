# figures/

Generators for every figure in the VISTA paper, plus two that support it but do
not appear in it. Each reads run data from `$VISTA_DATA_ROOT` and writes its
`.pdf`/`.png` into **`figures/generated/`**.

**No run data and no generated figure ships here.** This repository ships the
generators, not the measurements they plot and not their output: point
`$VISTA_DATA_ROOT` at a directory of campaign run directories you produced
(`harness/run_campaign.sh`) or a generator that needs data exits 2 saying so.
Two need none — `make_pipeline.py` and `diagrams/export.sh` (Fig. 2) draw
schematics — so they work in a bare clone.

Layout: `src/` and `diagrams/` are source, `generated/` is output. Everything
this repository generates — these figures and the `.tex` table fragments from
`analysis/` alike — lands in `generated/`, and `$VISTA_FIG_DIR` relocates all of
it. (Before 2026-07-15 the generators here wrote to `figures/` while `analysis/`
wrote to `figures/generated/`, so output lived in two places depending on which
half of the pipeline made it, and `$VISTA_FIG_DIR` moved only one half.)

```sh
export VISTA_DATA_ROOT=/mnt/big/vista-data  # a directory of campaign run dirs
./build.sh                                  # rebuild everything
python3 src/make_tta_pareto.py              # or one at a time
```

`$VISTA_DATA_ROOT` is the directory holding one subdirectory per campaign — the
tree `harness/run_campaign.sh` writes. Scored JSONs are read from
`$VISTA_DATA_ROOT/derived` (`$VISTA_DERIVED_DIR` overrides), and `analysis/`
writes them there. Resolution lives in `src/_paths.py`, which reads the variable
exactly as `analysis/_paths.py` does; no generator hard-codes an absolute path.

(Before 2026-07-15 the two disagreed: this half read `$VISTA_DATA_ROOT` as a
*parent* containing `results/`, the other half as the campaigns directory
itself, so one setting meant two directories.)

## What is here

All outputs land in `generated/`.

| Generator | Output | In the paper? |
|---|---|---|
| `make_frame_funnel.py` | `generated/fig_frame_funnel` | **Yes** — Fig. 1, `introduction.tex:31` |
| `diagrams/export.sh` | `generated/fig_system_diagram` | **Yes** — Fig. 2, `design.tex:47` |
| `make_tta_pareto.py` | `generated/fig_tta_pareto` | **Yes** — Fig. 3, `evaluation.tex:54` |
| `make_latency_cdf.py` | `generated/fig_latency_cdf` | No — `evaluation.tex:71` has it commented out |
| `make_imp_concentration.py` | `generated/fig_imp_concentration` | No — supports the Table IV stash discussion |
| `make_pipeline.py` | `generated/fig_pipeline` | No — schematic superseded by Fig. 2 |

Fig. 4 does not exist. `evaluation.tex:113-121` holds a commented placeholder
proposing a two-panel load sweep (rho vs. output age, rho vs. TTA@0.5 s) and
suggesting a `make_load_sweep.py`. **We deliberately did not write it.** The
placeholder is a proposal, not a result: nothing in the paper cites a Fig. 4,
and Table III (`tab:policy-sl`) already reports the same measurements at
rho = 1.00 / 1.86 / 2.33. Adding a generator would have meant inventing a
figure the authors never approved.

## Environment

**`matplotlib==3.5.1` is a hard pin** (verified: this is the installed version
and the one that produced the paper's figures). These figures are not
version-portable. Every one of them positions text by hand, in data
coordinates, against curves whose pixel positions depend on the layout engine:

* `make_latency_cdf.py` places 8 labels at literal data coordinates on a **log**
  x-axis, where a label's width in data units depends on where it starts. The
  free channel between the VISTA plateau and the Stock-LiveDepth curve is about
  0.35 decades wide; a 14-character label at 7.2 pt spans about 0.30 of that.
* `make_frame_funnel.py` depends on `tight_layout(w_pad=0.1)` plus
  `subplots_adjust(bottom=0.34)` to seat the legend, and on `+26` **milliseconds**
  as a vertical text offset.
* `make_tta_pareto.py` nudges its five point labels by x-multipliers on a log
  axis.

A matplotlib bump changes default font metrics and `tight_layout` behaviour, so
labels shift and collide. Nothing detects this — the build still exits 0. If you
must upgrade, re-render each figure and **look at it**.

## Determinism

Measured on this machine, matplotlib 3.5.1:

* **PNGs are byte-reproducible** with no special handling. Two runs of
  `make_frame_funnel.py` a second apart both produce
  `edf38bafce59fdcf29af9e91b1871c87`.
* **PDFs are not**, because matplotlib stamps `/CreationDate`. Two runs gave
  `d570ef9c…` and `7db30033…`.
* Setting `SOURCE_DATE_EPOCH` fixes it: matplotlib honours it (the PDF then
  carries `/CreationDate (D:20231114221320Z)` for epoch 1700000000), and both
  runs gave `5afdb8ad…`.

`build.sh` therefore defaults `SOURCE_DATE_EPOCH=1752537600` (2025-07-15Z) and
two consecutive full builds are byte-identical across all 11 outputs (measured
when the run data was on this machine; in a bare clone only the two schematics
build at all). `cairo`
does **not** honour `SOURCE_DATE_EPOCH`, so `diagrams/export.sh` rewrites that
one field itself.

## Canonical policy palette

Defined in `make_tta_pareto.py`'s `STYLE` (Fig. 3, the figure in the paper) and
imported by meaning elsewhere. Result directories keep their old internal names
on disk; the mapping is:

| Paper name | Internal / on-disk | Colour |
|---|---|---|
| Stock-Default | `fifo33` | `#898781` |
| Stock-LiveDepth | `fifo-s2` (run as `fifo33` under `--replay-surfaces 2`) | `#52514e` |
| Static-Decimation | `dec13` / `e3_m_decimate3` (`--gap-every 3`) | `#eda100` |
| VISTA-Fresh | `fresh-k2` | `#5598e7` |
| VISTA-Activity | `imp-k2` | `#2a78d6` |

**This was inconsistent before and we changed it.** `make_latency_cdf.py` used
`#52514e` for Stock-Default and `#2a78d6` for VISTA-Fresh, while Fig. 3 uses
those same two hexes for Stock-LiveDepth and VISTA-Activity — so `#2a78d6` named
two different policies in one paper. `make_imp_concentration.py` had the same
collision plus `#eda100` (reserved for Static-Decimation) on an importance arm.
Both now follow Fig. 3. Aqua `#1baf7a` in `make_imp_concentration.py` marks a
stash-1 *control* and is used in no other figure.

## Known defects in the figures and their inputs

These are documented, not fixed. Fixing them is the author's call.

1. **`evaluation.tex:54` does not compile.** It includes
   `{VISTA-Rev1/figures/fig_tta_pareto_v7_t2.pdf}`. No `VISTA-Rev1` directory
   exists anywhere; the file is in `VISTA-Rev2/figures/`.

2. **`design.tex:42` names the wrong diagram source.** It claims
   `system_diagram_lateJuly.drawio.svg`. That file cannot be the source: it is
   1364x759 px (-> 1023 x 569.25 pt, not the paper's 1012.5 x 540), and cairosvg
   cannot render it at all. The real source is `_v3` — see `diagrams/export.sh`
   for the verification.

3. **Fig. 2 depicts `stash=2`; the default is `stash=1`.** Panel (c) is labelled
   "Per-camera stash=2" and draws two slots per camera, while `design.tex:73`
   ("one frame by default") and `vista_scheduler.hpp:105` (`int stash = 1`)
   both say one. The figure illustrates a non-default configuration 26 lines
   after its own section states the default.

4. **`recall_m2.json` mislabels two of Fig. 3's five arms.** Its
   `e3_m/imp-k2_r{0,1,2}` rows are byte-identical to `e8_impfix_r{0,1,2}` in
   `recall_e78.json`, and `e3_m/fifo-s2_r*` does not exist as a directory at
   all. The plotted **numbers are real** measurements from the correct
   post-bugfix runs and match Table II (VISTA-Activity: 115 ms / 31.7% — this
   script prints exactly that). The **provenance strings are wrong**. That is a
   labeling defect, not fabricated data. `make_tta_pareto.py` keys off those
   strings and so reproduces the paper's figure including the wrong provenance;
   do not remap without rescoring.

5. **`make_latency_cdf.py`'s VISTA-Activity arm was repointed, changing the
   figure.** Upstream pooled `e3_m/imp-k2_r*` (pre-importance-bugfix, mean e2e
   91.5-97.8 ms). Table II and Fig. 3 use `e8_impfix_r*` (109.1 / 115.5 /
   120.6 ms). Keeping the old arm would have put a 94.6 ms VISTA-Activity in
   this figure against 115 ms in the paper's own table. The authors'
   `fig_latency_cdf.png` (`b6790c72…`) plots the stale arm. VISTA-Fresh was left
   on `e3_m/fresh-k2_r*`, which is correct — only the importance path was re-run.

6. **`tau_max` is not a bound on `e2e`, and the old annotation implied it was.**
   `tau_max` gates *stash age*: `vista_scheduler.cpp:461` evicts a frame whose
   age exceeds it *before release*. `e2e_ms` includes the stash wait **plus**
   inference (~62 ms at YOLO11m) plus downstream, so `e2e >= stash age` by
   construction and `e2e > 150 ms` is not a violation. Upstream drew the
   `tau_max` line on the e2e axis and annotated "~99% within tau_max" — a
   bound-satisfaction claim this axis cannot test, and on the correct data the
   number is not 99% anyway (measured: VISTA-Fresh 98.9%, VISTA-Activity 87.8%,
   p99 215.6 ms). The line is kept as a scale reference, relabelled "stash age";
   the percentages are stated as measured descriptives.

7. **Fig. 3's Static-Decimation point uses 2 repeats, not 3.**
   `recall_m_decimate.json` scores only `e3_m_decimate3_r{0,1}` although
   `_r2` exists on disk. Its whiskers are therefore a min-max over two runs.

8. **`e3_m_aggregate.json` is stale** (pre-bugfix imp-k2: 38.8% / 94.6 ms) and
   contradicts Table II on every cell. No figure here reads it. It does not
   ship; see the `superseded:` block in `analysis/campaigns.yaml`.

## Provenance of the generators

Two upstream trees carried divergent copies under the same filenames. The
sources here were chosen by **reproducing the authors' published figures**, not
by date. Those figures are not in this repository; the hashes below record
checks made when they were on the machine, and you cannot repeat them here:

* `make_frame_funnel.py` — from `paper_lsmc`. Verified: it reproduces the
  paper's `fig_frame_funnel.png` **byte-for-byte**
  (`edf38bafce59fdcf29af9e91b1871c87`). The `VISTA-Rev2` copy is an older v6
  geometry (figsize height 2.35 vs 2.05, `w_pad` 1.2 vs 0.1, legend
  upper-center, panel (b) y-axis on the left) and produces a **different** image
  (`d5569baf…`) — Rev2 carries the correct PNG beside a generator that cannot
  make it. Do not "restore" those values.
* `make_tta_pareto.py` — from `paper_lsmc/src/make_tta_pareto_v7_t2.py`, with
  only the two `savefig` names changed. Verified byte-identical to the paper's
  `fig_tta_pareto_v7_t2.png` (`faf2f954…`). `VISTA-Rev2/src/make_tta_pareto.py`
  is an older 9-arm version using the pre-rename policy names.
* `make_pipeline.py`, `make_latency_cdf.py`, `make_imp_concentration.py` — the
  two trees' copies are identical; `paper_lsmc` used as the source.

**TRAP.** `dec13` = `e3_m_decimate3` = `--gap-every 3` = Static-Decimation
(~64 ms). `dec12` = `e3_m_decimate` = DEC-1/2 (~997 ms, the stalest configuration
measured, a diagnostic). Confusing them moves Fig. 3's yellow point ~930 ms with
no error raised. Separately, `--gap-every 44` is unrelated: it is a live-rate
timing-fidelity setting, not a decimation baseline.

## Data dependencies

None of this is in the repository; it is what a data root must contain.

* `$VISTA_DATA_ROOT/<campaign>/<run>/` — raw per-run output under the original
  campaign directory names (they are load-bearing; see
  `analysis/campaigns.yaml`). A fresh run writes plain `dets.jsonl`; the paper's
  runs were gzipped as `dets.jsonl.gz`, and `_paths.open_text()` / `iter_dets()`
  handle both. `dets.jsonl` is **not** valid JSONL — the GStreamer child writes
  plain stdout ("Opening in BLOCKING MODE") into the same stream, so non-`{`
  lines must be skipped. `e2e_ms` carries negative sentinels; filter `>= 0`.
* `$VISTA_DATA_ROOT/derived/` — scored analysis JSONs, an **output of
  `analysis/`** (`make_all.py --tier rescore`), not an input you must find.
  `make_tta_pareto.py` needs `recall_m2.json` and `recall_m_decimate.json`.

No generator reads `dets.jsonl` today — the funnel reads `push_*.csv`, the CDF
reads `metrics.csv`, and the concentration figure reads `sched.csv` — but the
gzip-transparent helpers are in `_paths.py` for anything added later.

## Failure behaviour

Generators **fail loudly**. `make_imp_concentration.py` previously hard-coded an
absolute results path and, on a miss, printed `skip (no data)` and exited **0**,
producing a blank plot. A figure missing one of its three bars is
indistinguishable at a glance from a complete one, and exit 0 tells a build
script everything is fine. All generators now raise `_paths.MissingData` and
exit non-zero.

Two distinct failures, both non-zero, neither a traceback:

**No data root at all** — `_paths.require_data_root()`, at import:

```
$ python3 src/make_tta_pareto.py; echo $?
vista: VISTA_DATA_ROOT is not set.
  The measurement archive is not distributed with this repository:
  this artifact ships code, not runs.
  Set VISTA_DATA_ROOT to a directory of campaign run directories,
  or produce one on a Jetson with harness/run_campaign.sh
  (see harness/README.md).
2
```

**A root, but not this run** — `_paths.MissingData`, reporting every unmatched
path at once:

```
$ VISTA_DATA_ROOT=/some/runs python3 src/make_imp_concentration.py; echo $?
make_imp_concentration: no result dirs matched:
  VISTA-Fresh (no importance): no match for /some/runs/briefS2_fresh-k2/fresh-k2_r*
  ...
1
```

`build.sh` runs every generator, reports which failed, and exits non-zero if any
did — so in a bare clone it builds the two schematics and fails the other four,
loudly.
