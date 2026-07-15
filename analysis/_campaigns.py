#!/usr/bin/env python3
"""_campaigns.py — loader for campaigns.yaml.

campaigns.yaml is the only place a run path may be written down. Everything
that needs to know "which directories back paper row X" comes through here.

The loader deliberately does NOT silently skip missing directories: a row that
cannot be resolved is an error worth seeing, because the failure mode this
guards against is a table quietly regenerating from n-1 repeats.

Its paths are relative to $VISTA_DATA_ROOT, and no run data ships with this
repository, so "the row is not there" is the ordinary case for anyone who has
not run that campaign. It is reported as one line and a non-zero exit, not as a
traceback: an absent run is a fact about your data root, not a bug in the code.
"""
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import CAMPAIGNS_YAML, DATA_ROOT  # noqa: E402
from _sched_log import PREFIX as _SCHED_PREFIX  # noqa: E402

_CACHE = None


def load():
    global _CACHE
    if _CACHE is None:
        with open(CAMPAIGNS_YAML) as f:
            _CACHE = yaml.safe_load(f)
    return _CACHE


def _die(msg):
    """One line, non-zero exit. See the module docstring: a row that does not
    resolve is a fact about the data root, and a traceback would suggest
    otherwise."""
    sys.stderr.write("vista: " + msg + "\n"
                     "  Data root: " + DATA_ROOT + "\n"
                     "  campaigns.yaml paths are relative to $VISTA_DATA_ROOT.\n"
                     "  Produce the campaign with harness/ (see its README), or\n"
                     "  point $VISTA_DATA_ROOT at a tree that holds it.\n")
    sys.exit(3)


def oracle(name):
    """Absolute run dir of a named oracle, plus its declared metadata."""
    o = load()["oracles"][name]
    p = os.path.join(DATA_ROOT, o["run_dir"])
    if not os.path.isdir(p):
        _die(f"campaigns.yaml oracle {name!r} references "
             f"{o['run_dir']!r}, which is not in the data root. Every recall "
             f"number is scored against this oracle; without it there is "
             f"nothing to score.")
    return p, o


def table(name):
    return load()["tables"][name]


def diagnostics():
    return load()["diagnostics"]


def rows(table_name, **filters):
    """Rows of a table, optionally filtered on any scalar field
    (e.g. rows("table4", clip_set="brief"))."""
    out = []
    for r in table(table_name).get("rows", []):
        if all(r.get(k) == v for k, v in filters.items()):
            out.append(r)
    return out


def run_dirs(row, require=True):
    """Absolute run dirs of a row. Raises if any is missing (see module doc)."""
    out = []
    for d in row.get("run_dirs", []):
        p = os.path.join(DATA_ROOT, d)
        if not os.path.isdir(p):
            if require:
                _die(f"campaigns.yaml row {row.get('paper_name')!r} references "
                     f"{d!r}, which is not in the data root.")
            continue
        out.append(p)
    if require and len(out) != row.get("n_repeats", len(out)):
        _die(f"campaigns.yaml row {row.get('paper_name')!r} declares "
             f"n_repeats={row.get('n_repeats')} but only {len(out)} of its run "
             f"dirs are present. Refusing to build a row from fewer repeats "
             f"than it declares — that is the silent failure this check exists "
             f"to prevent.")
    return out


def verify_all(verbose=True):
    """Check every path in campaigns.yaml. Returns list of (context, path)."""
    c = load()
    missing = []
    checked = 0

    def chk(p, ctx):
        nonlocal checked
        checked += 1
        if not os.path.isdir(os.path.join(DATA_ROOT, p)):
            missing.append((ctx, p))

    for k, o in c["oracles"].items():
        chk(o["run_dir"], f"oracle:{k}")
    for tname, t in c["tables"].items():
        for row in t.get("rows", []):
            # A row with NO run_dirs at all must be an error, not a silent
            # skip: an edit that drops the key would otherwise sail through
            # every check here and only surface as a generator crash.
            if not row.get("run_dirs"):
                missing.append((f"{tname}:{row['paper_name']}",
                                "<row declares no run_dirs>"))
                continue
            for d in row.get("run_dirs", []):
                chk(d, f"{tname}:{row['paper_name']}")
    for d_ in c.get("diagnostics", []):
        for d in d_.get("run_dirs", []):
            chk(d, f"diagnostic:{d_['paper_name']}")
    if verbose:
        print(f"campaigns.yaml: checked {checked} paths under {DATA_ROOT}; "
              f"{len(missing)} missing")
        for ctx, p in missing:
            print(f"  MISSING {ctx} -> {p}")
    return missing


def _actual_sched_config(run_dir):
    """(mode, depth, stash, evidence) a run ACTUALLY used, from its own files.

    run_meta.json's `cmd` is the literal argv and is preferred. e4_live has no
    run_meta.json, so fall back to the [sched] banner on stderr.

    Absent flags resolve to the SchedCfg defaults (depth 2, stash 1) — see
    vista/include/vista/vista_scheduler.hpp. On the PRE-stash build the banner
    emits no stash field at all and stash 1 was the only reachable value
    (docs/design/04 section 6.1), so the two paths agree.
    """
    meta = os.path.join(run_dir, "run_meta.json")
    if os.path.exists(meta):
        import json
        cmd = json.load(open(meta)).get("cmd")
        if cmd and "--sched" in cmd:
            def val(flag, default):
                return int(cmd[cmd.index(flag) + 1]) if flag in cmd else default
            return (cmd[cmd.index("--sched") + 1],
                    val("--sched-depth", 2), val("--sched-stash", 1), "cmd")

    err = os.path.join(run_dir, "stderr.log")
    if os.path.exists(err):
        import re
        # PREFIX, not a literal "[sched]": the archived binary prints [sched],
        # the shipped vista module prints [vista]. A literal here would make
        # this fall through to "cannot verify" on every run made with the
        # module this artifact actually ships.
        m = re.search(_SCHED_PREFIX +
                      r" mode=(\S+) k=\d+ depth=(\d+)( stash=(\d+))?",
                      open(err, errors="replace").read(400000))
        if m:
            # No stash field => pre-stash build => hardwired single slot.
            stash = int(m.group(4)) if m.group(4) else 1
            return m.group(1), int(m.group(2)), stash, "banner"
    return None


def verify_sched_config(verbose=True):
    """Assert every declared `stash:`/`depth:` against what the run did.

    campaigns.yaml records the RUN's configuration, never the paper's claim
    about it (the two disagree for VISTA-Activity — KNOWN-ISSUES.md).
    This check is what keeps that promise honest: it fails if anyone edits a
    stash value to match prose instead of data.
    """
    bad, checked = [], 0
    for tname, t in load()["tables"].items():
        for row in t.get("rows", []):
            if "stash" not in row and "depth" not in row:
                continue
            for d in row.get("run_dirs", []):
                p = os.path.join(DATA_ROOT, d)
                if not os.path.isdir(p):
                    continue
                got = _actual_sched_config(p)
                if got is None:
                    bad.append((f"{tname}:{row['paper_name']}", d,
                                "no cmd and no [sched] banner — cannot verify"))
                    continue
                mode, depth, stash, how = got
                checked += 1
                for key, actual in (("stash", stash), ("depth", depth)):
                    want = row.get(key)
                    if want is not None and want != actual:
                        bad.append((f"{tname}:{row['paper_name']}", d,
                                    f"declares {key}={want} but the run's "
                                    f"{how} says {key}={actual}"))
    if verbose:
        print(f"campaigns.yaml: verified sched config of {checked} runs; "
              f"{len(bad)} disagree")
        for ctx, d, why in bad:
            print(f"  MISMATCH {ctx} -> {d}: {why}")
    return bad


if __name__ == "__main__":
    _missing = verify_all()
    _bad = verify_sched_config()
    sys.exit(1 if (_missing or _bad) else 0)
