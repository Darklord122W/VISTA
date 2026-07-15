# Code provenance — which binary produced the numbers

*One page, because one fact carries it: the build behind the draft's results
cannot be identified from what it recorded. This page says why, and what the
harness in this repository does so that it does not happen again.*

---

## The paper's binary exists in no commit

Not "is hard to identify" — does not exist. The working tree was dirty for the
campaign, and the dirty files were the scheduler itself. The clearest case is the
stash results: **`--sched-stash` appears in no commit of the application's
history**, yet the runs behind the draft's stash comparison pass that flag. The
code that produced them was never committed anywhere, and the campaign is over.
That loss is not recoverable by any amount of care now.

## `git_sha` is a valid pointer to the wrong thing

The application's `run_meta.json` records a `git_sha`. It records **`HEAD` at run
time, not the source that was compiled.** On a clean tree those coincide; on a
dirty tree they do not, and nothing in the record says which case you are looking
at. That is worse than a missing field, because it looks authoritative — and
runs exist that record a sha predating the very flag they pass on their own
command line.

**So: verify against `cmd`, never against `git_sha`.** The `cmd` array is the
literal `argv` the run was launched with; a flag in `cmd` provably ran. If you
want to know what a run *did*, read its argv. If you want to know what code did
it, you cannot.

## What the harness here records instead

`harness/run_eval.py` writes, per run:

| field | what it is |
|---|---|
| `app_sha256` | **SHA-256 of the binary that actually ran.** The only unambiguous identity, dirty tree or not. |
| `app_sha256_16` / `app_mtime` | short form + mtime, for eyeballing |
| `git.describe` | `git describe --always --dirty` — **the `-dirty` suffix is the point** |
| `git.dirty_files` | *which* files were uncommitted, so a reader can judge whether it mattered rather than trusting that it did not |
| `git.dirty` | boolean; `true` means "HEAD does not describe what ran — trust `app_sha256`" |
| `git.sha` | still recorded, now as one weak signal among several rather than as the answer |

Had this existed during the campaign, the record would have read `<sha>-dirty`
with the scheduler's source files listed — exactly the information needed to know
that the sha is not the build, and exactly what was missing. The binary hash
additionally lets two runs be proved to have used the same build without any
reference to git at all.

**Do not remove those fields.** They cost microseconds and they are the only
thing standing between a future campaign and this page.

## And the module in `vista/`

Separately from any archive: the scheduler shipped here is not byte-identical to
the paper's binary either. The scoring, selection, gating and eviction logic is
byte-for-byte the paper's; the delta is a rename, parameter injection, validation
of configurations the paper never used, and additive instrumentation — plus one
behavioural change that is stated rather than buried (`join_and_cleanup()` now
counts frames still stashed at teardown as policy drops instead of silently
unreffing them, without which the ledger's closure is a coin flip on when the run
ends). It is enumerated, not asserted away, in
[`../../vista/PAPER_DIFF.md`](../../vista/PAPER_DIFF.md).
