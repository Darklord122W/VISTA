#!/usr/bin/env python3
"""_sched_log.py — the one place that parses the scheduler's stderr summary.

=============================================================================
THE DROP LEDGER IS IN stderr.log, NOT IN sched.csv.
=============================================================================
This surprises everyone, so it is worth being blunt. sched.csv logs ADMIT
DECISIONS ONLY — verified across the whole archive, every sched.csv contains
`admit` (and for salvage runs `admit-salvage` / `retain-held`) rows and
nothing else. It has no drop rows. A reader who counts drops from sched.csv
gets zero and concludes the ledger does not close.

The drop count lives in the one-line run summary the scheduler prints to
stderr at shutdown:

  [sched] imp: 2932 releases (24.1/s), 5864 fresh + 0 salvage admitted,
          8120 policy drops, s_hat 73.9 ms over 121.5 s.

and the ledger does close against it: 5864 + 8120 == 13984 arrivals, exactly.
That line is the ONLY evidence for the paper's "every drop counted" claim,
which is why the weightsweep aggregator regexes it out of archived stderr and
why this module exists.

=============================================================================
PREFIX DUALITY: "[sched]" (archived) vs "[vista]" (new module).
=============================================================================
The archived data was produced by the old binary, which named its thread
"sparq-sched" and prefixed its logs "[sched]". The shipped vista module names
its thread "vista-sched" and prefixes "[vista]". Every parser must accept
BOTH or it will silently return nothing on one of the two. SPARQ was the
project's former name; it never became a code identifier (the namespace was
mcrt, the class Scheduler, the flags --sched*), but it survives in the archived
thread name, in comments, and in mux_sched.txt's header.
"""
import os
import re

# Accept both prefixes, everywhere, always.
PREFIX = r"\[(?:sched|vista)\]"

SUMMARY_RE = re.compile(
    PREFIX + r"\s+(\w[\w-]*):\s+(\d+)\s+releases\s+\(([\d.]+)/s\),\s+"
    r"(\d+)\s+fresh\s+\+\s+(\d+)\s+salvage\s+admitted,\s+"
    r"(\d+)\s+policy\s+drops,\s+s_hat\s+([\d.]+)\s*ms\s+over\s+([\d.]+)\s*s")

CFG_RE = re.compile(
    PREFIX + r"\s+mode=(\S+)\s+k=(\d+)\s+depth=(\d+)\s+tau_max=([\d.]+)ms")

# "processed 5864 of 13984 arrived frames" — the metrics line, which gives the
# arrivals the ledger must close against.
PROCESSED_RE = re.compile(
    r"processed\s+(\d+)\s+of\s+(\d+)\s+arrived\s+frames")


def parse(run_dir):
    """Parse a run's stderr.log. Returns {} when there is nothing to parse
    (e.g. a stock run, which has no scheduler and therefore no summary)."""
    p = os.path.join(run_dir, "stderr.log")
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, errors="replace") as fh:
        text = fh.read()
    m = SUMMARY_RE.search(text)
    if m:
        out.update({
            "mode": m.group(1),
            "releases": int(m.group(2)),
            "releases_s": float(m.group(3)),
            "admitted_fresh": int(m.group(4)),
            "admitted_salvage": int(m.group(5)),
            "policy_drops": int(m.group(6)),
            "s_hat_ms": float(m.group(7)),
            "elapsed_s": float(m.group(8)),
        })
    m = CFG_RE.search(text)
    if m:
        out.update({"cfg_mode": m.group(1), "k": int(m.group(2)),
                    "depth": int(m.group(3)), "tau_max_ms": float(m.group(4))})
    m = PROCESSED_RE.search(text)
    if m:
        out["processed"] = int(m.group(1))
        out["arrivals"] = int(m.group(2))
    return out


def ledger_closes(info):
    """arrivals == admitted_fresh + admitted_salvage + policy_drops.

    Mirrors Stats::ledger_closes() in vista_scheduler.hpp. Returns None when
    the run has no scheduler summary to check (not False — absence of a
    ledger is not a broken ledger).
    """
    need = ("arrivals", "admitted_fresh", "admitted_salvage", "policy_drops")
    if not all(k in info for k in need):
        return None
    return info["arrivals"] == (info["admitted_fresh"]
                                + info["admitted_salvage"]
                                + info["policy_drops"])


def d_hard_ms(info, num_cams=4):
    """Indicative D_hard = 8*(N/K)*s_hat. See service_gaps.py for the caveat:
    the archived runs came from a build whose D_hard rule cannot be read back
    from source, so this reconstructs the NEW module's rule."""
    if "s_hat_ms" not in info or not info.get("k"):
        return None
    return 8.0 * (num_cams / info["k"]) * info["s_hat_ms"]
