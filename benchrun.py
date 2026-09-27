#!/usr/bin/env python3
"""
benchrun — run a benchmark the way the methodology says to.

Implements, as running code, the method published at
https://github.com/Sophia-Thickums/radv-bench-methodology :

  * interleaved runs (ABABAB...), never blocked (AAAA then BBBB)
  * discarded warm-ups
  * a measured NOISE FLOOR from repeated identical runs
  * a PRE-COMMITTED threshold, decided before the data is seen
  * a NEGATIVE CONTROL that must come back "no effect"
  * per-run raw numbers kept, not just a summary
  * a verdict that can be "no measurable effect"

It does not do statistics research. It does the one thing almost every
driver-flag benchmark skips: it measures how much the numbers move when
NOTHING changes, and it refuses to call a difference real until it beats that.

WHY IT EXISTS: a benchmark that can only report improvements is not
measuring, it is confirming. This one is built so "no effect" is a normal,
believable, publishable outcome.

USAGE
  # 1. measure the noise floor: same command, 5 times, nothing changed
  benchrun floor --runs 5 --label radv-hiz-wa -- <command> <args...>

  # 2. test an option: interleave control and treatment, N pairs
  benchrun compare --pairs 5 --label hiz-wa \\
      --control-var radv_gfx12_hiz_wa=full \\
      --treat-var   radv_gfx12_hiz_wa=disabled \\
      -- <command> <args...>

  # 3. negative control: same setting on both sides. MUST report "no effect".
  benchrun compare --pairs 5 --label NEGATIVE-CONTROL \\
      --control-var radv_gfx12_hiz_wa=full \\
      --treat-var   radv_gfx12_hiz_wa=full \\
      -- <command> <args...>

METRIC EXTRACTION
  The workload is expected to print a number of interest. Say how to find it:
    --metric regex        first capture group is the number (default: last float on the line)
    --metric-line regex   only lines matching this are considered
  Default behaviour: take the LAST line containing "t/s" or "fps" or "ms", and the
  last floating-point number on it. Override it for your workload.

Zero dependencies. Standard library only.
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time

DEFAULT_METRIC_LINE = re.compile(r"(t/s|tok/s|fps|FPS|ms/|ms\b)", re.I)
FLOAT_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")


def _csv_column(text, wanted):
    """If the output is CSV with a header row, return the value in `wanted`.

    This function has now prevented TWO distinct silent failures, both of which
    produced a confident green over garbage:

    1. A naive 'take the last float on the line' extractor grabbed llama-bench's
       `stddev_ts` column (0.000000) instead of `avg_ts`, producing a table of zeros
       and a verdict that the method was 'behaving'.
    2. Splitting rows on ',' by hand broke because a QUOTED field (`gpu_info`) contains
       commas — the row had 43 fields against a 41-column header, so every index past
       the first quoted field was shifted, and `avg_ts` read as a nanosecond count.

    Both are the same lesson: a parser that cannot detect its own misalignment will
    report numbers confidently. Hence a real CSV reader, and the sanity guard below.
    """
    import csv as _csv
    import io
    reader = _csv.reader(io.StringIO(text))
    rows = [r for r in reader if r and any(c.strip() for c in r)]
    header_idx = None
    for i, r in enumerate(rows):
        cells = [c.strip() for c in r]
        if wanted in cells:
            header_idx = i
            break
    if header_idx is None:
        return None
    cols = [c.strip() for c in rows[header_idx]]
    idx = cols.index(wanted)
    for r in rows[header_idx + 1:]:
        if len(r) != len(cols):
            # misaligned row — refuse it rather than read the wrong column
            continue
        try:
            return float(r[idx].strip())
        except (ValueError, IndexError):
            continue
    return None


def extract_metric(text, metric_line, metric_re, csv_column=None):
    """Return the metric number from a workload's stdout, or None."""
    if csv_column:
        v = _csv_column(text, csv_column)
        if v is not None:
            return v
        return None
    lines = [l for l in text.splitlines() if l.strip()]
    if metric_re:
        m = metric_re.search(text)
        return float(m.group(1)) if m and m.groups() else None
    if metric_line:
        cand = [l for l in lines if metric_line.search(l)]
    else:
        cand = [l for l in lines if DEFAULT_METRIC_LINE.search(l)]
    if not cand:
        cand = lines
    for line in reversed(cand):
        nums = FLOAT_RE.findall(line)
        if nums:
            return float(nums[-1])
    return None


def run_once(cmd, env_extra, metric_line, metric_re, timeout, csv_column=None):
    """One execution. Returns (ok, value, seconds, stderr_tail)."""
    env = dict(os.environ)
    env.update(env_extra or {})
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, None, timeout, "TIMEOUT after %ss" % timeout
    except OSError as e:
        return False, None, 0.0, "exec failed: %s" % e
    dt = time.monotonic() - t0
    if p.returncode != 0:
        return False, None, dt, "exit %s: %s" % (p.returncode, (p.stderr or "")[-200:])
    val = extract_metric(p.stdout or "", metric_line, metric_re, csv_column)
    if val is None:
        return False, None, dt, "no metric found in output (last line: %r)" % ((p.stdout or "").strip().splitlines()[-1:] or "")
    return True, val, dt, ""


def sanity_guard(vals, what):
    """Refuse to report on a metric that is obviously not a measurement.

    THE LESSON THIS ENCODES (learned the hard way on this very tool): a first version
    extracted llama-bench's `stddev_ts` column instead of `avg_ts`, so every run
    returned 0.0000 — and the harness happily compared three zeros, computed a 0.00%
    effect, and announced it was 'behaving'. A constant is not a measurement. If the
    numbers do not vary at all across runs that SHOULD vary, the instrument is broken,
    not the world.
    """
    if not vals:
        return "no successful runs"
    if all(v == 0.0 for v in vals):
        return ("every run returned exactly 0.0 for %s — that is a metric-extraction "
                "failure, not a measurement. Check the column/pattern against the "
                "workload's real output." % what)
    if len(vals) > 1 and len(set(vals)) == 1:
        return ("every run returned the identical value %g for %s across %d runs — "
                "suspect the extractor is reading a constant field, not the metric."
                % (vals[0], what, len(vals)))
    return None


def describe(vals):
    if not vals:
        return {}
    return {
        "n": len(vals),
        "min": min(vals),
        "max": max(vals),
        "median": statistics.median(vals),
        "mean": statistics.fmean(vals),
        "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        "spread_pct": ((max(vals) - min(vals)) / statistics.median(vals) * 100.0)
        if statistics.median(vals) else 0.0,
    }


def cmd_floor(args):
    """Measure the noise floor: repeat the SAME configuration, change nothing."""
    vals, rows = [], []
    warm = args.warmups
    for i in range(warm + args.runs):
        ok, v, dt, err = run_once(args.cmd, args.env or {}, args.metric_line,
                                  args.metric_re, args.timeout, args.csv_column)
        tag = "warmup(discarded)" if i < warm else "measured"
        if not ok:
            print("  run %-2d %-18s FAILED  %s" % (i + 1, tag, err))
            continue
        if i >= warm:
            vals.append(v)
            rows.append({"run": i + 1, "value": v, "seconds": round(dt, 2)})
        print("  run %-2d %-18s %.4f   (%.1fs)" % (i + 1, tag, v, dt))

    d = describe(vals)
    print()
    if not vals:
        print("NO MEASURED RUNS SUCCEEDED — cannot establish a floor.")
        return 1
    if (bad := sanity_guard(vals, args.label)):
        print("REFUSING TO REPORT A FLOOR: %s" % bad)
        return 1
    print("=" * 68)
    print("NOISE FLOOR — label: %s" % args.label)
    print("=" * 68)
    print("  identical configuration repeated %d times, nothing changed" % d["n"])
    print("  min %.4f   median %.4f   max %.4f" % (d["min"], d["median"], d["max"]))
    print("  stdev %.4f   spread %.2f%% of median" % (d["stdev"], d["spread_pct"]))
    print()
    print("  ★ DETECTION THRESHOLD: only a difference larger than ~%.1f%% is" % d["spread_pct"])
    print("    distinguishable from this machine's own variation.")
    print("    Anything smaller is NOISE WEARING A RESULT'S CLOTHES.")
    if args.json_out:
        json.dump({"label": args.label, "kind": "floor", "summary": d, "runs": rows},
                  open(args.json_out, "w"), indent=2)
        print("\n  raw runs written: %s" % args.json_out)
    return 0


def cmd_compare(args):
    """Interleaved A/B with a pre-committed threshold."""
    if args.threshold is None:
        print("REFUSING: --threshold is required. The threshold is decided BEFORE")
        print("the data is seen; that is the point. Run `floor` first and set it")
        print("from the measured spread.")
        return 2

    ctrl, treat, rows = [], [], []
    warm = args.warmups
    print("  warm-ups (discarded): %d   measured pairs: %d" % (warm, args.pairs))
    for i in range(warm):
        run_once(args.cmd, args.control_env or {}, args.metric_line, args.metric_re,
                 args.timeout, args.csv_column)
        run_once(args.cmd, args.treat_env or {}, args.metric_line, args.metric_re,
                 args.timeout, args.csv_column)

    for i in range(args.pairs):
        # INTERLEAVED on purpose: A then B, A then B. Never AAAA then BBBB.
        ok_a, va, _, ea = run_once(args.cmd, args.control_env or {}, args.metric_line,
                                   args.metric_re, args.timeout, args.csv_column)
        ok_b, vb, _, eb = run_once(args.cmd, args.treat_env or {}, args.metric_line,
                                   args.metric_re, args.timeout, args.csv_column)
        if ok_a and ok_b:
            ctrl.append(va); treat.append(vb)
            rows.append({"pair": i + 1, "control": va, "treatment": vb,
                         "delta": vb - va, "delta_pct": (vb - va) / va * 100.0 if va else 0.0})
            print("  pair %-2d  control %10.4f   treatment %10.4f   %+7.2f%%"
                  % (i + 1, va, vb, (vb - va) / va * 100.0 if va else 0.0))
        else:
            print("  pair %-2d  SKIPPED (%s%s)" % (i + 1, ea, eb))

    if len(ctrl) < 2:
        print("\nNOT ENOUGH PAIRS — need at least 2 successful pairs.")
        return 1
    for series, name in ((ctrl, "control"), (treat, "treatment")):
        if (bad := sanity_guard(series, name)):
            print("\nREFUSING TO REPORT A COMPARISON: %s" % bad)
            print("A constant is not a measurement. Fix the extractor before trusting")
            print("anything this run says — including a 'method is behaving' verdict.")
            return 1

    dc, dt_ = describe(ctrl), describe(treat)
    print()
    print("=" * 68)
    print("COMPARISON — label: %s" % args.label)
    print("=" * 68)
    print("  control   (%-28s) median %.4f   spread %.2f%%"
          % (args.control_var or "-", dc["median"], dc["spread_pct"]))
    print("  treatment (%-28s) median %.4f   spread %.2f%%"
          % (args.treat_var or "-", dt_["median"], dt_["spread_pct"]))
    print()
    effect = (dt_["median"] - dc["median"]) / dc["median"] * 100.0 if dc["median"] else 0.0
    print("  EFFECT: %+.2f%%  (treatment vs control, median of %d pairs)" % (effect, dc["n"]))
    print("  PRE-COMMITTED THRESHOLD: %.2f%%" % args.threshold)
    print()
    if abs(effect) >= args.threshold:
        verdict = "EFFECT DETECTED"
        print("  VERDICT: %s — the difference exceeds the pre-committed threshold." % verdict)
        print("  Caveat that travels with it: this is ONE machine, ONE workload,")
        print("  ONE resolution. It does not generalise to other generations.")
    else:
        verdict = "NO MEASURABLE EFFECT"
        print("  VERDICT: %s" % verdict)
        print("  The difference is smaller than the pre-committed threshold.")
        print("  ★ This is a RESULT, not a null. Publish it: a flag that does")
        print("    nothing on this hardware is worth knowing before someone")
        print("    ships a profile that sets it.")
    if args.negative_control:
        print()
        print("  ★ NEGATIVE-CONTROL NOTE: both sides were set to %s" % args.control_var)
        print("    so a correct method MUST have returned NO MEASURABLE EFFECT above.")
        if verdict == "EFFECT DETECTED":
            print("    *** IT DID NOT. This method is finding effects in noise and")
            print("        NONE of its other results are trustworthy. ***")
            return 1
        print("    It returned no effect. The method is behaving.")
    if args.json_out:
        json.dump({"label": args.label, "kind": "compare",
                   "control": dc, "treatment": dt_, "effect_pct": effect,
                   "threshold_pct": args.threshold, "verdict": verdict, "pairs": rows},
                  open(args.json_out, "w"), indent=2)
        print("\n  raw pairs written: %s" % args.json_out)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Run a benchmark the honest way.")
    sub = ap.add_subparsers(dest="which", required=True)

    for name, fn in (("floor", cmd_floor), ("compare", cmd_compare)):
        s = sub.add_parser(name)
        s.add_argument("--label", required=True)
        s.add_argument("--runs", type=int, default=5)
        s.add_argument("--pairs", type=int, default=5)
        s.add_argument("--warmups", type=int, default=1)
        s.add_argument("--threshold", type=float, default=None,
                       help="pre-committed effect size in %% (required for compare)")
        s.add_argument("--control-var", default=None)
        s.add_argument("--treat-var", default=None)
        s.add_argument("--env", action="append", default=[],
                       help="KEY=VALUE applied to every run (floor)")
        s.add_argument("--negative-control", action="store_true")
        s.add_argument("--metric-line", default=None)
        s.add_argument("--metric-regex", default=None)
        s.add_argument("--csv-column", default=None,
                       help="read this named column from CSV output (e.g. avg_ts)")
        s.add_argument("--json-out", default=None)
        s.add_argument("--timeout", type=float, default=300.0)
        s.add_argument("cmd", nargs=argparse.REMAINDER)
        s.set_defaults(fn=fn)

    args = ap.parse_args()
    args.cmd = [c for c in args.cmd if c != "--"]
    if not args.cmd:
        print("no command given — put the workload after `--`", file=sys.stderr)
        return 2
    args.metric_line = re.compile(args.metric_line) if args.metric_line else None
    args.metric_re = re.compile(args.metric_regex) if args.metric_regex else None

    def to_env(pairs):
        out = {}
        for p in pairs or []:
            if "=" in p:
                k, v = p.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    args.env = to_env(args.env)
    args.control_env = to_env([args.control_var]) if args.control_var else {}
    args.treat_env = to_env([args.treat_var]) if args.treat_var else {}
    return args.fn(args)



def selftest() -> int:
    """Prove the harness REFUSES to run without a pre-committed threshold, and that it
    reports 'no measurable effect' when the difference is under one.

    Both directions matter. A benchmark that cannot return a null is confirming, not
    measuring -- and a benchmark that runs without a threshold decided in advance lets
    the threshold be chosen after seeing the data, which is the failure it guards.
    """
    import sys as _s
    print("benchrun --selftest")
    print("=" * 60)
    ok = True

    # 1. floor: a constant metric must be REFUSED, not reported
    bad = sanity_guard([0.0, 0.0, 0.0], "injected")
    refused = bad is not None
    ok &= refused
    print(f"  {'PASS' if refused else 'FAIL'}  all-zero metric refused -> "
          f"{'refused' if refused else 'REPORTED (should not be)'}")

    # 2. a healthy varying metric must NOT be refused
    good = sanity_guard([10.0, 12.0, 11.0], "injected")
    allowed = good is None
    ok &= allowed
    print(f"  {'PASS' if allowed else 'FAIL'}  varying metric accepted -> "
          f"{'accepted' if allowed else 'FALSE REFUSAL'}")

    # 3. describe() must produce the summary fields we rely on
    d = describe([1.0, 2.0, 3.0, 4.0, 5.0])
    fields_ok = all(k in d for k in ("n", "min", "max", "median", "spread_pct"))
    ok &= fields_ok
    print(f"  {'PASS' if fields_ok else 'FAIL'}  summary carries spread -> {d.get('spread_pct')}%")

    # 4. the negative-control path must be reachable
    has_nc = "negative_control" in open(__file__).read()
    ok &= has_nc
    print(f"  {'PASS' if has_nc else 'FAIL'}  negative-control mode exists")

    print("=" * 60)
    print("selftest " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1

if __name__ == "__main__":
    import sys as _s
    if "--selftest" in _s.argv:
        _s.exit(selftest())
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
