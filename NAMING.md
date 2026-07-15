# Naming

This system was developed under the name **SPARQ** and is published as
**VISTA**. Everything user-facing is VISTA. If you find the string `sparq`
somewhere, it is a survival from the working title — in comments, in `--help`
text, and in the two places under §3 that a parser has to know about.

## 1. Policy names: draft ↔ CLI

The draft's Sec. IV-A names five policies. Each is a configuration of one
binary, not a separate build:

| Draft name | CLI configuration |
|---|---|
| **Stock-Default** | scheduler off, default decoder pools |
| **Stock-LiveDepth** | off, calibrated pools (`--replay-surfaces 2`) |
| **Static-Decimation** | off, `--gap-every 3` |
| **VISTA-Fresh** | `--sched fresh --sched-k 2 --sched-depth 2 --sched-stash 1` |
| **VISTA-Activity** | `--sched imp`, with `--sched-stash` >= `--sched-depth` |

VISTA-Fresh's flags are all defaults except `--sched fresh`; they are written out
because the draft names the values. VISTA-Activity requires stash >= d — below
that, importance cannot concentrate service and the mode silently does nothing
useful (`docs/design/04-depth-and-stash.md`).

Note that `--gap-every N` drops two consecutive frames per N and therefore keeps
`(N-2)/N`: `--gap-every 3` keeps one frame in three. The flag value is not the
denominator, and `--gap-every 2` keeps nothing at all.

## 2. Code identifiers

| Was (the paper's working tree) | Now |
|---|---|
| namespace `mcrt` | namespace `vista` |
| stderr prefix `[sched]` | `[vista]` |
| pthread name `sparq-sched` | `vista-sched` |

The class was always `Scheduler` and the CLI flags were always `--sched*`.
`vista/PAPER_DIFF.md` enumerates every difference between this module and the
code that produced the paper's numbers, the rename included.

## 3. The compatibility rule that matters to code

**Parsers must accept both spellings, always.** Previously collected data was
produced by the predecessor and carries the old names:

* **Log prefix** — the archived logs say `[sched]`; this module prints
  `[vista]`. `analysis/_sched_log.py` is the single implementation and matches
  both. If you write a new parser, use it; do not re-derive the regex. The drop
  ledger lives on that line, so a parser that matches only one prefix silently
  reads no drops.
* **Thread name** — archived data was produced by `sparq-sched`, new data by
  `vista-sched`. A sampler that matches only one does not measure nothing; it
  measures `-1` and reports it as a number.
