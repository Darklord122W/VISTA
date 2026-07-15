#!/usr/bin/env python3
"""_paths.py — the only place in analysis/ that knows where anything lives.

Every other script imports from here. The originals each carried their own
`PD = "/home/vista/..."`, which made them run on exactly one machine.

THE RUN DATA IS NOT IN THIS REPOSITORY. This artifact ships the code that
scores runs, not the runs. There is no shipped default and no in-repo
fallback for the data root: `$VISTA_DATA_ROOT` must name a directory of
campaign run directories, or every script here exits 2 with one message
(`require_data_root()` below). Produce such a directory with
`harness/run_campaign.sh` on a Jetson; see `harness/README.md`.

Expected layout under `$VISTA_DATA_ROOT` — one directory per campaign
(`e3_m`, `e8_impfix_r0`, `oracle_x`, ...), each holding run directories with
`metrics.csv`, `dets.jsonl[.gz]`, `sched.csv`, `run_meta.json`. This is the
tree `harness/run_campaign.sh` writes, so `VISTA_DATA_ROOT=$VISTA_RESULTS`
is the normal setting.

  $VISTA_DATA_ROOT/            one dir per campaign, plus:
  $VISTA_DATA_ROOT/derived/    scored JSONs written here ($VISTA_DERIVED_DIR
                               overrides)

`figures/src/_paths.py` resolves `$VISTA_DATA_ROOT` identically and must stay
in step with this file; until 2026-07-15 the two disagreed (that module read
the root as a *parent* holding `results/`), so one env var meant two
directories depending on which half of the pipeline you invoked.

Campaign directory names are the ORIGINAL experiment names and are
load-bearing: `campaigns.yaml` keys off them, so they must not be renamed to
the paper's policy names. See `campaigns.yaml` for the mapping.

Nothing here creates directories except the `ensure_*` helpers, so importing
this module is side-effect free apart from the data-root check.
"""
import gzip
import io
import json
import os
import sys

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(ANALYSIS_DIR)

#: campaigns.yaml — the single source of truth for paper row -> run dirs.
#: It is code, and ships; the runs it names do not.
CAMPAIGNS_YAML = os.path.join(ANALYSIS_DIR, "campaigns.yaml")


def _env_dir(name):
    v = os.environ.get(name)
    return os.path.abspath(os.path.expanduser(v)) if v else None


# ---------------------------------------------------------------------------
# The data root. No default: this repository ships no measurements.
# ---------------------------------------------------------------------------
# The message below is the ONLY thing a user with no data should ever see from
# this codebase -- not a traceback, and never an empty result that looks like a
# successful run of a policy that scored zero. figures/src/_paths.py carries
# the same check and the same wording; keep the two in step.

_cached_root = None


def _die(reason):
    sys.stderr.write(
        "vista: " + reason + "\n"
        "  The measurement archive is not distributed with this repository:\n"
        "  this artifact ships code, not runs.\n"
        "  Set VISTA_DATA_ROOT to a directory of campaign run directories,\n"
        "  or produce one on a Jetson with harness/run_campaign.sh\n"
        "  (see harness/README.md).\n")
    sys.exit(2)


def require_data_root():
    """Return the validated data root, or exit 2 with one clear message."""
    global _cached_root
    if _cached_root is not None:
        return _cached_root
    root = _env_dir("VISTA_DATA_ROOT")
    if root is None:
        _die("VISTA_DATA_ROOT is not set.")
    if not os.path.isdir(root):
        _die("VISTA_DATA_ROOT=%s is not a directory." % root)
    _cached_root = root
    return root


def _derived_dir():
    return _env_dir("VISTA_DERIVED_DIR") or os.path.join(
        require_data_root(), "derived")


def __getattr__(name):
    """Resolve DATA_ROOT / DERIVED_DIR lazily (PEP 562).

    Deliberately not module globals: `from _paths import DATA_ROOT` must run
    the check above at import time, so a script that needs data fails on its
    first line rather than half way through a campaign. A consumer that needs
    no data (e.g. figures/src/make_pipeline.py, which only wants an output
    directory) never touches these names and so never triggers the check.
    """
    if name == "DATA_ROOT":
        return require_data_root()
    if name == "DERIVED_DIR":
        return _derived_dir()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


#: Generated figures and .tex table fragments. This one HAS a default: it is
#: an output directory, so it is always resolvable and is created on demand.
FIG_DIR = _env_dir("VISTA_FIG_DIR") or os.path.join(
    REPO_ROOT, "figures", "generated")


def data(*parts):
    """Path under the run-data root, e.g. data("e3_m", "fifo33_r0")."""
    return os.path.join(require_data_root(), *parts)


def derived(*parts):
    """Path under the derived-JSON directory."""
    return os.path.join(_derived_dir(), *parts)


def figure(*parts):
    return os.path.join(FIG_DIR, *parts)


def ensure_derived():
    d = _derived_dir()
    os.makedirs(d, exist_ok=True)
    return d


def ensure_figdir():
    os.makedirs(FIG_DIR, exist_ok=True)
    return FIG_DIR


# --------------------------------------------------------------------------
# dets.jsonl readers
# --------------------------------------------------------------------------
# Two facts every reader must survive:
#
#  1. A fresh run writes dets.jsonl; an archived one may be gzipped
#     (dets.jsonl.gz -- the runs behind the paper compressed 212 MB to 67 MB).
#     Accept either.
#  2. dets.jsonl is NOT valid JSONL. The C++ app writes it on the same fd
#     GStreamer logs to, so lines like "Opening in BLOCKING MODE" are
#     interleaved (~14 of 2673 lines in a typical run). Anything not starting
#     with '{' is skipped; a JSONDecodeError on a torn line is skipped too.

def dets_path(run_dir, stem="dets.jsonl"):
    """Resolve a dets file, preferring the gzipped form. Returns None if
    neither exists (callers decide whether that is fatal)."""
    gz = os.path.join(run_dir, stem + ".gz")
    if os.path.exists(gz):
        return gz
    plain = os.path.join(run_dir, stem)
    if os.path.exists(plain):
        return plain
    return None


def open_text(path):
    """Open .gz transparently, plain text otherwise."""
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8",
                                errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def iter_jsonl(path):
    """Yield the JSON objects of a dets file, skipping interleaved stdout."""
    with open_text(path) as f:
        for line in f:
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
