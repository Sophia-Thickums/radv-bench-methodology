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
