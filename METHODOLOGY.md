# Benchmarking a driver option without fooling yourself

*A methodology for deciding whether a RADV option has a performance effect — written as the answer to
the request in [mesa/mesa#15028](https://gitlab.freedesktop.org/mesa/mesa/-/work_items/15028), where a
RADV developer asked for systematic before/after numbers and a user replied: "just point me to a
methodology and I'll do my best."*

---

## What this is for, and what "success" means

The question is narrow: **does toggling option `X` change performance, in a way that is larger than the
noise this machine produces on its own?**

**The default answer is "no measurable effect", and that is a real result, not a failure.** A method
that can only report improvements is not measuring — it is confirming. If you run this and find nothing,
you have learned something true about the option, and you have saved everyone a profile that would have
been cargo cult.

Two consequences follow, and they are the whole discipline:

1. **You must be able to return "no effect" and be believed.** That means showing your noise floor.
2. **You must decide what would count as an effect *before* you look at the data.** Otherwise you will
   find one by looking harder.

---

## Step 0 — Pin everything, and write it down

A benchmark result without its full stack is an anecdote. Record, for every run:

| Field | How to capture it | Why it matters |
|---|---|---|
| OS + kernel | `uname -r` | scheduler, power, driver changes |
| Mesa version **and commit** | `vulkaninfo --summary`; `pacman -Q mesa` | version strings lie — a distro build and a source build of the same number differ |
| Vulkan driver | confirm it says `RADV` | benchmarking the wrong driver is common and silent |
| GPU model + PCI address | `lspci` / `/sys` | **name the card, never an index** — indices renumber |
| Power profile | GPU clocks, TDP/limits, CPU governor/EPP | a thermal or power change dwarfs most flags |
| Game / workload + version | build ID, patch level | patches change performance more than options do |
| Translation layers | Proton, DXVK, vkd3d-proton versions | each has its own performance behaviour |
| Resolution + preset | exact, with dynamic resolution **off** | the single largest confounder |
| Compositor + session | Wayland/X11, and whether the game gets direct scanout | can gate whole features |
| Background load | what else is running | the cheapest way to ruin a run |

**One option moves per experiment. Everything else is pinned.** If two things differ between two runs,
you have measured their sum and cannot attribute it.

---

## Step 1 — Measure the noise floor BEFORE testing the option

This is the step that separates a measurement from a guess, and it is the step Schürmann's caveat —
*"not skewed by other things or simply variance"* — is asking for.

**Run the same configuration N times. Same settings. Nothing changed. Record the spread.**

- **N ≥ 5** for anything you intend to publish.
- Report **min, median, max, and standard deviation** of the repeated runs.
- The spread you observe **is** your detection threshold.

**You may only claim an effect if it exceeds this floor.** Concretely: if five identical runs of the same
scene vary by ±4%, then a 3% difference between `full` and `disabled` is **not a result**. Writing it up
as one is the failure mode this whole document exists to prevent.

If the spread comes back large, **fix the machine before testing the option** — a benchmark whose noise
exceeds the effect can never answer the question, no matter how many runs you add.

---

## Step 2 — Interleave, don't block

Do **not** run `AAAA` then `BBBB`. Blocked runs confound the option with **time**: thermal drift, clock
boost decay, background updates, shader cache growth, and — on a desktop — the compositor getting busier
as the session ages.

Run **`ABABAB…`**, alternating, and pair the runs. If run 3 of A and run 3 of B were taken minutes apart
under the same conditions, comparing them is fair; comparing the average of four runs from the morning
against four from the afternoon is not.

**Warm-up runs are discarded.** The first execution after a cache clear measures shader compilation and
disk I/O, not the option. Do at least one warm-up pass and throw it away.

---

## Step 3 — Fix the workload, and know which regime you are in

- **Use a built-in benchmark or a recorded, replayable scene.** Never "play for twenty minutes" — you
  cannot repeat it, so you cannot attribute a difference to anything.
- **Fixed duration and fixed camera path.** Same start, same end, every run.
- **Determine whether you are CPU-bound or GPU-bound before interpreting anything.** A cheap test:
  drop the resolution substantially and re-run. If frame rate barely moves, you were CPU-bound — and a
  GPU driver option cannot help you, so a null result there means nothing about the option.
- **Report frame *times*, not only FPS.** Averages hide stutter. Give **median, p1, and p99 frame time**,
  or 1% lows alongside the average. An option can raise the average while making the low points worse,
  and the low points are what a player feels.

---

## Step 4 — The negative control

**Include at least one comparison where you already know there should be no difference** — for example
two runs of the *same* setting that you label as if they were a pair, or an option known to be inert on
your hardware.

If your method reports an effect there, **your method is producing effects out of noise and none of your
other results mean anything.** A method you have never watched return "no effect" is a method you have
only assumed works.

---

## Step 5 — Decide the threshold in advance

Before collecting data, write down:

- the metric you will judge on (e.g. **median frame time**, not "FPS"),
- the number of runs,
- **the smallest difference you will call real** — and set it from the Step 1 noise floor, not from hope,
- what you will conclude if the difference is smaller than that.

Then report what you found. **Moving the threshold after seeing the data is the exact move this procedure
exists to make impossible.** If the result is "smaller than noise", the finding is *no measurable effect
on this hardware, in this workload, at this resolution* — and that sentence is worth publishing.

---

## Step 6 — Report in a form someone else can attack

A claim that cannot be checked is not a measurement. Publish, alongside the conclusion:

- **the raw per-run numbers**, not just the summary,
- the exact commands and environment variables used, verbatim,
- the full stack table from Step 0, with versions **and commits**,
- the **noise floor** and how it was measured,
- the threshold from Step 5, and whether the result met it,
- **what you could not test** — the games you do not own, the resolution you did not try, the hardware
  generation you do not have.

That last item is not humility theatre. It is what lets a reader know whether your null result applies to
them.

---

## What a single machine cannot tell you

**Scope, stated plainly, because this is where benchmarks get over-claimed:**

- **One GPU generation is one data point.** An option's effect can differ per generation; `gfx12` behaviour
  does not generalise to `gfx11` without evidence.
- **One game is not a library.** Effects are often application-specific — that is the entire premise of
  per-app profiles.
- **One resolution is one regime.** A change that matters at 1440p may vanish at 1080p where you are
  CPU-bound, and vice versa.
- **A null result is not proof of no effect.** It is a limit on the effect size *this* test could have
  detected, on *this* machine. If your noise floor was ±5%, you have proven the effect is not larger than
  about that — not that it is zero.

**So the honest shape of a contribution here is a case study with a method**, not a verdict. "On RDNA4,
on this title, at this resolution, with a method that could detect a 2% difference, `radv_gfx12_hiz_wa=disabled`
showed no such difference" is a genuinely useful sentence — and it is one a driver developer can act on.

---

## The one-line version

**Measure how much your numbers wobble when you change nothing; only then is a difference a difference.**

---

*Published as an open method, not a result. If you use it and get a null, publish the null — that is the
data point the driver team actually asked for.*
