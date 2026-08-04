#!/usr/bin/env python3
"""True combined cost of a Harbor job, split by model.

Harbor's ``result.json`` reports ``cost_usd`` from the session's
``total_cost_usd``, which sums ACTUAL BILLED cost. A subscription-backed
advisor bills $0.00 per token, so an advisor run's headline cost is the
WORKER'S cost alone — on one 89-task run the reviewer was 87% of the
economic cost and reported as free.

Token counts are unaffected: ``n_input_tokens`` / ``n_output_tokens``
already combine every model. Only the dollar figure misleads.

PREFER THE BILLED FIGURE; RECOMPUTE ONLY WHAT BILLED $0
-------------------------------------------------------
``compute_cost`` picks a pricing tier from the prompt size of the record it
is handed, because some models are context-tiered — ``gpt-5.6-luna`` doubles
above 272K prompt tokens. ``model_usage`` has already aggregated individual
requests away, so recomputing from it prices an entire session as one giant
long-context request. Measured on an 89-task run: $4.96 recomputed vs $2.74
actually billed.

So this does NOT recompute what is already priced. ``cost_usd`` was computed
per-REQUEST by the live agent and is the best figure available. Recomputation
applies only where ``cost_usd`` is 0.00 because the model ran on a
subscription — Anthropic today, which has no context tier, making aggregate
pricing exact there.

Usage:  python3 eval/harbor/advisor_cost.py <job-dir> [<job-dir> ...]
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


def _blank() -> dict:
    return {"in": 0, "out": 0, "cache_read": 0, "cache_create": 0,
            "billed": 0.0, "listed": 0.0}


def job_cost(job: pathlib.Path) -> dict:
    from src.services.pricing import compute_cost

    per_model: dict[str, dict] = {}
    billed = listed = 0.0
    sessions = 0
    for s in job.rglob("sessions/*.json"):
        try:
            cost = json.loads(s.read_text()).get("cost") or {}
        except (OSError, ValueError):
            continue
        if not cost:
            continue
        sessions += 1
        billed += float(cost.get("total_cost_usd") or 0.0)
        for model, u in (cost.get("model_usage") or {}).items():
            acc = per_model.setdefault(model, _blank())
            rec = {
                "input_tokens": int(u.get("input_tokens") or 0),
                "output_tokens": int(u.get("output_tokens") or 0),
                "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
                "cache_creation_input_tokens": int(
                    u.get("cache_creation_input_tokens") or 0),
            }
            acc["in"] += rec["input_tokens"]
            acc["out"] += rec["output_tokens"]
            acc["cache_read"] += rec["cache_read_input_tokens"]
            acc["cache_create"] += rec["cache_creation_input_tokens"]
            acc["billed"] += float(u.get("cost_usd") or 0.0)
            acc["listed"] += compute_cost(model, rec)

    # PREFER THE BILLED FIGURE WHEREVER IT EXISTS.
    #
    # ``cost_usd`` was computed per-REQUEST by the live agent, which is the
    # only place the context tier can be applied correctly: gpt-5.6-luna
    # doubles above 272K prompt tokens, and ``model_usage`` has already
    # aggregated individual requests away, so ANY recomputation from it
    # prices a whole session as one giant long-context request. Measured on
    # an 89-task run: recomputed $4.96 vs $2.74 actually billed, an 81%
    # overstatement — the reconstruction is strictly worse than the number
    # already in the file.
    #
    # Recomputation is only needed where ``cost_usd`` is 0.00 because the
    # model ran on a subscription. That is Anthropic today, which has no
    # context tier, so aggregate pricing is exact for it.
    for u in per_model.values():
        u["listed"] = u["billed"] if u["billed"] > 0 else u["listed"]
    listed = sum(m["listed"] for m in per_model.values())
    return {"sessions": sessions, "billed": billed,
            "listed": listed, "per_model": per_model}


def _trial_dirs(job: pathlib.Path) -> int:
    return sum(1 for p in job.iterdir() if p.is_dir())


def _declared_trials(job: pathlib.Path) -> int | None:
    try:
        return json.loads((job / "result.json").read_text()).get("n_total_trials")
    except (OSError, ValueError):
        return None


def report(job: pathlib.Path) -> None:
    from src.services.pricing import get_pricing

    if not job.exists():
        print(f"\n=== {job.name} ===")
        print(f"  NO SUCH JOB DIRECTORY: {job}")
        return

    r = job_cost(job)
    declared = _declared_trials(job)
    on_disk = _trial_dirs(job)

    print(f"\n=== {job.name} ===")
    if declared is not None and on_disk < declared:
        # An aborted run leaves a partial tree; reporting its cost as if it
        # were the whole job is how a 3-of-89 job gets mistaken for a
        # finished one.
        print(f"  INCOMPLETE JOB: {on_disk} trial dirs on disk, "
              f"{declared} declared. Cost below covers only what ran.")
    print(f"  sessions with cost data: {r['sessions']}"
          + (f" of {on_disk} trial dirs" if on_disk else ""))
    if on_disk and r["sessions"] < on_disk:
        print(f"  {on_disk - r['sessions']} trial(s) have NO session JSON — "
              "timed-out trials are killed before session.save(), so their "
              "cost is invisible here and everywhere else. Totals are a "
              "lower bound.")
    if not r["sessions"]:
        return

    unpriced = []
    for model, u in sorted(r["per_model"].items()):
        if get_pricing(model) is None:
            unpriced.append(model)
            share, note = "   ?", "  <-- NO PRICING DATA (not free; excluded)"
        else:
            share = (f"{100 * u['listed'] / r['listed']:4.1f}"
                     if r["listed"] else "   ?")
            note = ""
        print(f"  {model}")
        print(f"    input {u['in']:>13,}   output {u['out']:>12,}")
        print(f"    cache_read {u['cache_read']:>8,}   "
              f"cache_create {u['cache_create']:>8,}")
        prompt = u["in"] + u["cache_read"] + u["cache_create"]
        if prompt:
            print(f"    prompt total {prompt:>11,}  "
                  f"({100 * u['cache_read'] / prompt:.1f}% cached)")
        print(f"    billed ${u['billed']:>10.4f}   "
              f"list ${u['listed']:>10.4f}  ({share}%){note}")
    if unpriced:
        print(f"  WARNING: no pricing row for {', '.join(unpriced)} — "
              "excluded from the totals, which are a lower bound.")
    print(f"  {'BILLED (harbor cost_usd)':30s} ${r['billed']:>12.4f}")
    print(f"  {'TRUE COMBINED (list price)':30s} ${r['listed']:>12.4f}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for arg in sys.argv[1:]:
        report(pathlib.Path(arg))
    print("\nNote: 'billed' omits any model on a subscription ($0.00/token). "
          "'list price' is what the same tokens would cost on the API, and is "
          "the figure to compare between runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
