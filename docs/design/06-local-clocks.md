# Why VISTA never uses PTS

*Compressed from the authors' `SYNC_MECHANISM_ZH.md` (Chinese; substance
translated), `REPRODUCE_camera_interleave.md`, the `fake_PTS` measurement pack,
and `RTBEV_VERIFICATION.md`. The headline measurement is re-derived below from
the numbers in the pack.*

VISTA schedules on `CLOCK_MONOTONIC` arrival stamps taken by its own probe. It
never reads a buffer's PTS for a scheduling decision. This is a design
commitment, and it is the reason the design ports: it needs **a monotonic local
clock**, not a synchronized one.

---

## 1. The timestamp is a fiction

On the live path the only true timestamp is the one `uvcvideo` writes when a
frame lands: a `CLOCK_MONOTONIC` stamp at USB delivery. `v4l2src` turns it into
a buffer PTS. Then `jpegparse` throws it away.

`jpegparse` (GstBaseParse, GStreamer 1.20) **re-stamps every frame onto a
synthetic 33.33 ms grid anchored at that camera's own first frame.** Four USB
cameras enumerate one after another, so the four grids are offset from each
other by **1.05–1.70 s**, and the offsets are fixed for the life of the run.

From `jpegparse` onward — including at the mux — PTS is not capture time. It is
a per-camera fiction with a per-launch constant error. Anything that compares
PTS across cameras compares fictions.

Two properties make this worse than plain noise:

- **The grids have zero relative drift.** They are synthetic, so they do not
  wander. Which cameras land in the same batching window is decided by
  grid-anchor luck at startup and then stays **stable for the whole run**. A
  timestamp-based policy does not fail randomly; it fails *consistently and
  reproducibly in one direction*, which is exactly the shape of a result that
  looks like a finding.
- **It is not our bug.** `jpegparse` is stock, the C920 is stock, the pipeline
  is the documented one. `nvv4l2decoder` cannot decode the C920's 4:2:2 MJPEG,
  so `jpegparse` → `nvjpegdec` is the supported path.

## 2. What alignment does when you feed it fictions

The mux's `sync-inputs=1` is not "align four streams by timestamp". It is a
sliding **frame-age admission window**, anchored one push period `P` in the past
and `L = max-latency` wide. For a frame at running time `B`, with the pipeline
clock at `now` (`gstnvtimesynch.cpp`, `NvTimeSync::get_synch_info`):

```
  EARLY   if  B > now - P                       (too young; wait)
  ONTIME  if  now - P - L <= B <= now - P       (admit)
  LATE    if  B + L < now - P                   (erase, silently)

                    <----- L ----->
  --------------+---------------+---------------+-------->  time
            now-P-L           now-P            now
   LATE (dropped) |   ONTIME     |  EARLY (wait) |
```

**A frame must be at least `P` old to be eligible, and older than `P+L` is
discarded.** Point that rule at four grids that disagree by more than a second
and it does exactly what it is told: it erases most of the input.

Measured, in a controlled 120 s live A/B (`fake_PTS/campaign_2026-07-07_ptsfix`,
`STEP4_sync_after_fix.md`):

| | `sync-inputs=1`, fabricated PTS | `sync-inputs=1`, true PTS restored |
|---|---|---|
| arrivals → batched | **13,985 → 2,059 (14.7% kept)** | 9,587 → 9,580 (99.9% kept) |
| batches / mean fill / % full | 639 / 3.22 / 40.4% | 2,395 / 4.00 / **100.0%** |
| batch size distribution | `{1:27, 2:62, 3:292, 4:258}` | `{4: 2395}` |

`2059/13985 = 14.72%` kept ⇒ **85.3% of arrived frames silently erased.** That
is the paper's figure (Sec. IV-A), and it is the number to quote.

Note what the right-hand column proves: alignment is not broken. Given true
timestamps it works perfectly (100% full batches). The failure is entirely the
fabricated input. That is why the honest framing is *"timestamp alignment on
`jpegparse`-restamped MJPEG streams silently sheds most frames"* — a mechanism
statement — with the number attached to this rig.

> **A retracted number.** Several older working documents in the authors' tree
> report **93.6%** for this measurement. That figure has **no surviving raw
> data**: it came from a 2026-07-06 run deleted as unreliable pre-fix data. The
> paper deliberately replaced it with the traceable 85.3%. Do not reintroduce
> 93.6% from any older doc. (Some docs in the pack also write "14.6% kept"
> alongside "85.3% discarded"; the two disagree slightly and 14.7% is the one
> that matches the underlying 2,059/13,985.)

## 3. Escape conditions — does this apply to you?

The mechanism needs a specific triple: **MJPEG/`jpegparse` + multiple staggered
cameras + `nvstreammux` with `sync-inputs=1`.** Anyone with that triple will
suffer heavy silent loss. Any one of these avoids it:

- one camera (nothing to align);
- `sync-inputs=0` (the common default — zero alignment loss);
- a `max-latency` wide enough to cover the grid offsets (seconds);
- restored true timestamps (the PTS-fix probes).

The *number* (85.3%) is this rig's: it is a function of USB enumeration
stagger, camera count, `max-latency`, and drift. A different rig gets a
different number. The *mechanism* is general to that triple.

## 4. Why the fix is not the answer either

A PTS-restore probe pair straddles `jpegparse` and puts the true kernel stamps
back (verified live: pre-mux PTS == kernel capture PTS on **13,940/13,940**
frames). It is ON by default in the app. So why not just rely on it?

Because it is an **instrumentation** dependency, not a scheduling one:

- **It is our patch, not the platform's.** Any deployment that has not applied
  it — i.e. every stock DeepStream MJPEG pipeline — is back to fictions. A
  scheduler that requires it does not port.
- **Even with true stamps, `sync-inputs=1` costs a structural latency floor** of
  one push period (~+31 ms) that no parameter removes: the EARLY gate means a
  frame is unbatchable until it is `P` old. VISTA's whole argument is about
  output age.
- **True timestamps still would not help the decision.** VISTA needs to know how
  long *this host* has been holding *this frame*. That is `now - t_arrival` on
  one monotonic clock — a local subtraction, exact, requiring no agreement
  between cameras at all.

So: the PTS-fix exists so that **measurement** can join a detection back to its
capture instant (`buf_pts` is the join key for `dets.jsonl`). VISTA's scheduling
does not use it, and VISTA runs with `sync-inputs=0` everywhere.
`check_obligations()` throws if it finds `sync-inputs=1`
(`vista/src/vista_scheduler.cpp:184-191`) — not because VISTA cannot cope, but
because the host has then silently thrown away most of the frames before VISTA
ever sees them, and the run's numbers would be meaningless.

## 5. The free-running cameras were never the problem

The premise behind timestamp alignment is that unsynchronized cameras need
aligning. On this rig they largely do not: the cameras' **true capture instants
already interleave within a few milliseconds**. The measured nearest-frame
spread across cameras is ~8.9 ms live — roughly `33.3 ms / 4 cameras`, which is
what you get from four independent phases uniformly spread over one frame
period. That is geometry, not coincidence, and it is why the paper says the
free-running cameras "already interleave within a few milliseconds."

The disagreement worth seconds is entirely manufactured by `jpegparse`. The
platform breaks the timestamps and then offers a feature to align on the broken
timestamps.

## 6. Where alignment *is* the right answer

This is a claim about commodity USB rigs, not about synchronization in general.
Hardware-synchronized automotive rigs are a different world: RT-BEV
(RTSS'24) co-optimizes camera synchronization with fused BEV perception on
hardware-synced nuScenes keyframes, whose inter-camera skew is a **39–46 ms**
band (verified against the authors' PDF, Sec. III.B / Fig. 5). With stamps that
good, alignment is sound and valuable.

The inversion is the point, and the paper states it as such: timestamp-based
alignment is sound on hardware-synchronized rigs and *inverts* on commodity USB
cameras with fabricated stamps. VISTA targets the second case, and buys its
portability by refusing to depend on the distinction at all.

## 7. The one sentence

> The only trustworthy clock in a commodity multi-camera pipeline is the host's
> own monotonic clock at the moment a frame arrives; VISTA is built entirely on
> that subtraction, which is why it needs no camera synchronization, no
> timestamp quality, and no platform patch.
