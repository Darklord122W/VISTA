"""Path resolution for the figure generators.

Every generator imports this instead of hard-coding an absolute path. The
originals each carried their own `PD = "/home/vista/..."`, which made them
unrunnable off the author's machine; two of them additionally *failed open*
(printed "skip (no data)" and exited 0), so a broken path produced a blank
figure rather than an error.

THE RUN DATA IS NOT IN THIS REPOSITORY. This artifact ships the generators,
not the runs they plot. There is no shipped default and no in-repo fallback
for the data root: `$VISTA_DATA_ROOT` must name a directory of campaign run
directories, or a generator that needs data exits 2 with one message
(`require_data_root()` below). Produce such a directory with
`harness/run_campaign.sh`; see `harness/README.md`.

    <repo>/figures/src/_paths.py   <- __file__
    <repo>/figures/generated/      <- FIG_OUT   (generated .pdf/.png land here)
    $VISTA_DATA_ROOT/              <- RESULTS   (one dir per campaign)
    $VISTA_DATA_ROOT/derived/      <- DERIVED   (scored analysis JSONs)

`analysis/_paths.py` resolves `$VISTA_DATA_ROOT` identically and must stay in
step with this file. Until 2026-07-15 the two disagreed: this module read the
root as a *parent* holding `results/` while `analysis/_paths.py` read it as the
directory of campaigns itself, so a single `VISTA_DATA_ROOT=/mnt/vista-data`
meant two different directories depending on which half of the pipeline you
invoked. It now means the directory of campaigns in both, which is the tree
`harness/run_campaign.sh` writes.

OUTPUT DIRECTORY: everything generated in this repository lands in
`figures/generated/`, and `$VISTA_FIG_DIR` overrides it. Both statements are
also true of `analysis/_paths.py`. `figures/` holds source (`src/`,
`diagrams/`) and output (`generated/`) under separate names; nothing generated
is committed (see .gitignore).
"""
import gzip
import os
import sys

SRC = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SRC, "..", ".."))

#: Where generated .pdf/.png land. Same default and same env var as
#: analysis/_paths.py's FIG_DIR — keep the two in step. This one HAS a
#: default: it is an output directory, so it is always resolvable, and a
#: generator that needs no data (make_pipeline.py) needs nothing else.
_fig_env = os.environ.get("VISTA_FIG_DIR")
FIG_OUT = (os.path.abspath(os.path.expanduser(_fig_env)) if _fig_env
           else os.path.join(REPO_ROOT, "figures", "generated"))


class MissingData(Exception):
    """Raised instead of degrading to an empty plot.

    A figure built from a subset of its arms is indistinguishable, at a glance,
    from one built from all of them -- the reader cannot see the arm that was
    silently dropped. Generators therefore abort rather than emit a partial
    figure; the caller sees a non-zero exit.

    This is the "you have data, but not *this* run" error. "You have no data at
    all" is require_data_root()'s, and it exits before a generator starts.
    """


# ---------------------------------------------------------------------------
# The data root. No default: this repository ships no measurements.
# ---------------------------------------------------------------------------
# Same check and same wording as analysis/_paths.py; keep the two in step.

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
    v = os.environ.get("VISTA_DATA_ROOT")
    if not v:
        _die("VISTA_DATA_ROOT is not set.")
    root = os.path.abspath(os.path.expanduser(v))
    if not os.path.isdir(root):
        _die("VISTA_DATA_ROOT=%s is not a directory." % root)
    _cached_root = root
    return root


def _derived_dir():
    v = os.environ.get("VISTA_DERIVED_DIR")
    if v:
        return os.path.abspath(os.path.expanduser(v))
    return os.path.join(require_data_root(), "derived")


def __getattr__(name):
    """Resolve DATA_ROOT / RESULTS / DERIVED lazily (PEP 562).

    Deliberately not module globals: `from _paths import RESULTS` must run the
    check above at import time, so a generator that needs data fails on its
    first line instead of building a figure out of nothing. make_pipeline.py
    imports only FIG_OUT -- it draws a schematic and reads no run -- so it
    never triggers the check.
    """
    if name in ("DATA_ROOT", "RESULTS"):
        return require_data_root()
    if name == "DERIVED":
        return _derived_dir()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def require(path, what=""):
    """Return `path`, or raise MissingData naming what was expected."""
    if not os.path.exists(path):
        hint = " (%s)" % what if what else ""
        raise MissingData(
            "missing%s: %s\n"
            "  VISTA_DATA_ROOT=%s\n"
            "  That root exists, but this run does not. Campaign directory\n"
            "  names are load-bearing (see analysis/campaigns.yaml); a run\n"
            "  produced under a different arm or name will not be found."
            % (hint, path, require_data_root()))
    return path


def derived(name):
    """Path to a scored analysis JSON, checked for existence."""
    return require(os.path.join(_derived_dir(), name),
                   "derived analysis %s" % name)


def results(*parts):
    """Path to a campaign/run directory under the data root, checked."""
    return require(os.path.join(require_data_root(), *parts), "result dir")


def require_globs(pattern_to_label):
    """Resolve {glob: label}; raise listing every pattern that matched nothing.

    Reports all failures at once: fixing a data path one arm per run is slow
    when several arms moved together.
    """
    import glob as _glob
    out, missing = {}, []
    for pat, label in pattern_to_label.items():
        hits = sorted(_glob.glob(pat))
        if hits:
            out[pat] = hits
        else:
            missing.append("  %s: no match for %s" % (label, pat))
    if missing:
        raise MissingData(
            "no result dirs matched:\n" + "\n".join(missing) +
            "\n  VISTA_DATA_ROOT=%s" % require_data_root())
    return out


def open_text(path):
    """Open a run artifact, transparently handling gzip.

    A fresh run writes plain dets.jsonl; an archived one may be gzipped (the
    runs behind the paper compressed 212 MB to 67 MB). Both are accepted so a
    locally produced run works without a flag.
    """
    if path.endswith(".gz"):
        return gzip.open(path, "rt")
    return open(path, "r")


def open_dets(run_dir, stem="dets"):
    """Open <run_dir>/<stem>.jsonl[.gz], preferring whichever exists."""
    for cand in ("%s.jsonl.gz" % stem, "%s.jsonl" % stem):
        p = os.path.join(run_dir, cand)
        if os.path.exists(p):
            return open_text(p)
    raise MissingData("no %s.jsonl(.gz) in %s" % (stem, run_dir))


def iter_dets(run_dir, stem="dets"):
    """Yield parsed records from a dets file.

    dets.jsonl is NOT valid JSONL: the DeepStream/GStreamer child process writes
    plain stdout ("Opening in BLOCKING MODE", ~14 of 2673 lines in the runs we
    checked) into the same stream. Lines that do not start with '{' are skipped
    -- that is expected content, not corruption.
    """
    import json
    with open_dets(run_dir, stem) as fh:
        for line in fh:
            line = line.lstrip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def savefig(fig, name, dpi=300, **kw):
    """Write <name>.pdf and <name>.png into FIG_OUT and echo the paths.

    PNGs are byte-reproducible; PDFs are not unless SOURCE_DATE_EPOCH is set,
    because matplotlib stamps /CreationDate. See figures/README.md.
    """
    kw.setdefault("bbox_inches", "tight")
    kw.setdefault("pad_inches", 0.02)
    os.makedirs(FIG_OUT, exist_ok=True)   # FIG_OUT may be a fresh $VISTA_FIG_DIR
    written = []
    for ext in ("pdf", "png"):
        p = os.path.join(FIG_OUT, "%s.%s" % (name, ext))
        fig.savefig(p, dpi=dpi, **kw)
        written.append(p)
    for p in written:
        print("wrote", os.path.relpath(p, REPO_ROOT))
    return written
