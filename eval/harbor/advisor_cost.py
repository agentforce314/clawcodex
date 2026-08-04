#!/usr/bin/env python3
"""True combined cost of a Harbor job, split by model.

Harbor's ``result.json`` reports ``cost_usd`` from the session's
``total_cost_usd``, which sums ACTUAL BILLED cost. A subscription-backed
advisor bills $0.00 per token, so an advisor run's headline cost is the
WORKER'S cost alone — in one measured trial that understated the true
economic cost by ~23x, because the reviewer was 92% of it.

Token counts are unaffected: n_input_tokens / n_output_tokens already
combine every model. Only the dollar figure is misleading.

Usage:  python3 eval/harbor/advisor_cost.py <job-dir> [<job-dir> ...]
"""
import json
import pathlib
import sys


def job_cost(job: pathlib.Path) -> dict:
    per_model: dict[str, dict] = {}
    billed = estimated = 0.0
    trials = 0
    for s in job.rglob("sessions/*.json"):
        try:
            cost = json.loads(s.read_text()).get("cost") or {}
        except (OSError, ValueError):
            continue
        if not cost:
            continue
        trials += 1
        billed += float(cost.get("total_cost_usd") or 0.0)
        estimated += float(cost.get("estimated_cost_usd") or 0.0)
        for model, u in (cost.get("model_usage") or {}).items():
            acc = per_model.setdefault(
                model, {"in": 0, "out": 0, "cache_read": 0, "cache_create": 0}
            )
            acc["in"] += int(u.get("input_tokens") or 0)
            acc["out"] += int(u.get("output_tokens") or 0)
            acc["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
            acc["cache_create"] += int(u.get("cache_creation_input_tokens") or 0)
    return {
        "trials": trials, "billed": billed,
        "estimated": estimated, "per_model": per_model,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for arg in sys.argv[1:]:
        job = pathlib.Path(arg)
        r = job_cost(job)
        if not r["trials"]:
            print(f"{job.name}: no session JSON found "
                  "(timed-out trials never persist one)")
            continue
        print(f"\n=== {job.name} ===")
        print(f"trials with cost data: {r['trials']}")
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
            from src.services.pricing import compute_cost, get_pricing
            unpriced = []
            for model, u in sorted(r["per_model"].items()):
                c = compute_cost(model, {
                    "input_tokens": u["in"], "output_tokens": u["out"],
                    "cache_read_input_tokens": u["cache_read"],
                    "cache_creation_input_tokens": u["cache_create"],
                })
                # A model with no pricing row costs $0.0000 here, which reads
                # as "free" when it means "unknown". Say which it is —
                # a silently-zero model would understate the very total this
                # script exists to correct.
                if get_pricing(model) is None:
                    unpriced.append(model)
                    note = "  <-- NO PRICING DATA (not free; excluded)"
                    share_s = "   ?"
                else:
                    note = ""
                    share_s = (f"{100 * c / r['estimated']:4.1f}"
                               if r["estimated"] else "   ?")
                print(f"  {model:22s} in={u['in']:>9,} out={u['out']:>8,}  "
                      f"list-price ${c:>9.4f}  ({share_s}%){note}")
            if unpriced:
                print(f"  WARNING: no pricing row for {', '.join(unpriced)} — "
                      "the totals below EXCLUDE them and are a lower bound.")
        except Exception as exc:  # noqa: BLE001 — reporting must not fail
            print(f"  (per-model pricing unavailable: {exc})")
        print(f"  {'BILLED (harbor cost_usd)':22s} ${r['billed']:>32.4f}")
        print(f"  {'TRUE COMBINED (list)':22s} ${r['estimated']:>32.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
