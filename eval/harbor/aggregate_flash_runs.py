#!/usr/bin/env python3
"""Combine flash-h2* job dirs into one 74-task comparison vs baseline."""
import json, glob, os

WT = "/Users/ericlee2/workspace/clawcodex/.claude/worktrees/fineturn-deepseek-harness/eval/harbor/jobs"
BASE = "/Users/ericlee2/workspace/clawcodex/eval/harbor/jobs/tb21-flash-visiontool"
OPUS = "/Users/ericlee2/workspace/clawcodex/eval/harbor/jobs/tb21-clawcodex-3"
# All 40K-cap ("h2"/"c") tuned batch dirs. EXCLUDES the 16K-era dirs
# (flash-h1-subsetA, smoke-fuse-1) which used the pre-re-tune harness.
TUNED_GLOBS = ["flash-h2*", "flash-c*"]


def load_single(d):
    """One reward per task from a single job dir (last-write on dup dirs)."""
    r = {}
    for rj in glob.glob(f"{d}/*/result.json"):
        t = os.path.basename(os.path.dirname(rj)).rsplit("__", 1)[0]
        j = json.load(open(rj))
        rew = ((j.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if t not in r or (r[t] is None and rew is not None):
            r[t] = rew
    return r


def load_tuned_samples():
    """All flash-h2* measurements per task -> list of rewards (replicates)."""
    samples = {}
    dirs = sorted({d for g in TUNED_GLOBS for d in glob.glob(f"{WT}/{g}")})
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for rj in glob.glob(f"{d}/*/result.json"):
            t = os.path.basename(os.path.dirname(rj)).rsplit("__", 1)[0]
            j = json.load(open(rj))
            rew = ((j.get("verifier_result") or {}).get("rewards") or {}).get("reward")
            if rew is not None:
                samples.setdefault(t, []).append(float(rew))
    return samples


def main():
    samples = load_tuned_samples()
    base = load_single(BASE)
    opus = load_single(OPUS)

    common = sorted(set(samples) & set(base))
    print(f"{'task':34s} {'tuned':>6} {'n':>2} {'base':>5} {'opus':>5}  note")
    fixed = regr = 0
    for t in common:
        tr = sum(samples[t]) / len(samples[t])
        br = base[t] or 0
        o = opus.get(t)
        note = ""
        if tr > br:
            note = "<< FIXED"; fixed += 1
        elif tr < br:
            note = ">> REGRESSED"; regr += 1
        print(f"{t:34s} {tr:>6.2f} {len(samples[t]):>2} {str(br):>5} {str(o):>5}  {note}")

    tmean = sum(sum(samples[t]) / len(samples[t]) for t in common) / len(common)
    bmean = sum((base[t] or 0) for t in common) / len(common)
    total_meas = sum(len(samples[t]) for t in common)
    missing = sorted(set(base) - set(samples))
    print()
    print(f"DISTINCT TASKS SCORED ON TUNED: {len(common)}/74  ({total_meas} total measurements)")
    print(f"  tuned mean (per-task avg): {tmean:.3f}")
    print(f"  baseline mean:             {bmean:.3f}")
    print(f"  delta:                     {tmean - bmean:+.3f}   (per-task-avg: +{fixed} up, -{regr} down vs baseline)")
    if missing:
        print(f"  not yet scored on tuned ({len(missing)}): {' '.join(missing)}")


if __name__ == "__main__":
    main()
