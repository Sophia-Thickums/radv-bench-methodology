# radv-bench-methodology

**How to benchmark a driver option without fooling yourself.**

A short, opinionated method for deciding whether a RADV option (or any GPU driver flag) has a
performance effect — written as the answer to the request in
[mesa/mesa#15028](https://gitlab.freedesktop.org/mesa/mesa/-/work_items/15028), where a RADV
developer asked for systematic before/after numbers and a user replied *"just point me to a
methodology and I'll do my best."*

**→ [Read the methodology](METHODOLOGY.md)**

## The one-line version

> Measure how much your numbers wobble when you change nothing; only then is a difference a difference.


## The harness

`benchrun.py` implements the method as running code — because a methodology nobody executes is a
suggestion. Zero dependencies, standard library only.

```bash
# 1. measure your noise floor: same command, N times, nothing changed
python3 benchrun.py floor --runs 5 --warmups 1 --label my-floor \
    --csv-column avg_ts -- <your workload>

# 2. test an option: interleaved control/treatment, pre-committed threshold
python3 benchrun.py compare --pairs 5 --warmups 1 --threshold 5.6 \
    --label hiz-wa \
    --control-var radv_gfx12_hiz_wa=full \
    --treat-var   radv_gfx12_hiz_wa=disabled \
    -- <your workload>

# 3. the negative control — MUST come back "no effect"
python3 benchrun.py compare --pairs 5 --threshold 5.6 --negative-control \
    --label NEGATIVE-CONTROL \
    --control-var radv_gfx12_hiz_wa=full --treat-var radv_gfx12_hiz_wa=full \
    -- <your workload>
```

It **refuses to run a comparison without a pre-committed `--threshold`**, because deciding what counts
after seeing the data is the exact move this method exists to prevent. And it **refuses to report at all**
when the extracted metric is a constant — see below.

## Two bugs this tool caught in itself, both of the same kind

Worth publishing, because they are the failure mode the method is for, committed by the tool that
preaches it:

1. **A table of zeros reported as a success.** The first metric extractor took the last number on a line
   and therefore read `stddev_ts` (`0.000000`) instead of `avg_ts`. Three runs of `0.0` were compared,
   a `+0.00%` effect was computed, and the tool announced it was *"behaving."* **A constant is not a
   measurement.**
2. **A quoted field with commas in it.** The next version split rows on `,` by hand. `gpu_info` contains
   commas, so rows had 43 fields against a 41-column header and every index shifted — `avg_ts` read as a
   nanosecond count (`~1.03e9` instead of `~52`). **A parser that cannot detect its own misalignment
   reports numbers confidently.**

Both now have fixes *and* guards: a real CSV reader with a row-length assertion, and a `sanity_guard()`
that refuses the whole run when the metric is all-zero or perfectly constant.

**The honest lesson: the tool was wrong twice and reported success twice. That is why the noise floor
and the negative control are not optional steps.**


## What it covers

- **The noise floor first.** Run identical configs N≥5 times; the spread you observe *is* your
  detection threshold. A difference smaller than it is not a result.
- **Interleave, don't block** — `ABABAB…`, never `AAAA` then `BBBB`, because blocked runs confound
  the option with thermal drift and session age.
- **A negative control** — if your method reports an effect where you know there is none, nothing
  else it told you is trustworthy.
- **Decide the threshold before collecting data**, and never move it after.
- **Report frame times, not just FPS** — averages hide the stutter a player feels.
- **A null result is a real result**, and the honest scope limits of a single machine.

## Why it exists

The failure mode of driver-flag benchmarking is that it always finds the flag helps. This method is
built so that it can return "no measurable effect" and be believed — which is the only version of the
exercise that produces knowledge.

No results are claimed here. It is a method, published so anyone can use it and so a null is
publishable.

MIT licensed.
