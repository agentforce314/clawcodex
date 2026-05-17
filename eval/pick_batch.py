"""Pick the next batch of unseen SWE-bench instances for a cumulative eval run.

Given a dataset and a predictions file, emit the next N instance IDs that
haven't been predicted yet. Two picking modes:

* ``--random`` (recommended) — uniform random sample from the unseen pool.
  Best for accumulating an unbiased estimate of resolve rate over many
  batches. Pass ``--seed`` for reproducibility.
* default (stratified round-robin) — picks one instance from each repo in
  turn, then loops. Skews toward alphabetically-early issues in each repo,
  which tend to be older / better-characterized. Useful for diversity in a
  single batch but biased over multiple batches.

Examples:
    # Random batch of 50 unseen, reproducible with seed
    python eval/pick_batch.py --random --seed 42 \\
        SWE-bench-dev/datasets/SWE-bench__SWE-bench_Verified__style-3__fs-oracle \\
        eval/runs/mini-10-deepseek/clawcodex_preds.jsonl 50

    # Stratified (default) — one instance per repo, rotates
    python eval/pick_batch.py \\
        SWE-bench-dev/datasets/SWE-bench__SWE-bench_Verified__style-3__fs-oracle \\
        eval/runs/mini-10-deepseek/clawcodex_preds.jsonl 50
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_local", help="Path to the local SWE-bench dataset dir.")
    parser.add_argument("preds_path", type=Path, help="Predictions JSONL to skip-already-done.")
    parser.add_argument("n", type=int, help="How many to pick.")
    parser.add_argument(
        "--random",
        action="store_true",
        help="Uniform random sample from unseen pool (instead of stratified round-robin).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed; only meaningful with --random. Omit for non-reproducible sampling.",
    )
    args = parser.parse_args()

    from datasets import load_from_disk

    ds = load_from_disk(args.dataset_local)["test"]
    all_rows = [(r["instance_id"], r["repo"]) for r in ds]

    seen: set[str] = set()
    if args.preds_path.exists():
        with args.preds_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                iid = rec.get("instance_id")
                if iid:
                    seen.add(iid)

    unseen = [(iid, repo) for iid, repo in all_rows if iid not in seen]
    if not unseen:
        print(
            f"All {len(all_rows)} instances already predicted in {args.preds_path}",
            file=sys.stderr,
        )
        return 1

    if args.random:
        rng = random.Random(args.seed)
        sample = rng.sample(unseen, k=min(args.n, len(unseen)))
        chosen = [iid for iid, _ in sample]
        mode = f"random{f' (seed={args.seed})' if args.seed is not None else ''}"
    else:
        # Stratified round-robin: pick from each repo in turn so the batch
        # spans the dataset breadth rather than clustering on one repo.
        by_repo: dict[str, list[str]] = defaultdict(list)
        for iid, repo in unseen:
            by_repo[repo].append(iid)
        chosen = []
        while len(chosen) < args.n:
            added_this_round = False
            for repo in sorted(by_repo):
                if not by_repo[repo]:
                    continue
                chosen.append(by_repo[repo].pop(0))
                added_this_round = True
                if len(chosen) >= args.n:
                    break
            if not added_this_round:
                break
        mode = "stratified"

    print(
        f"picked {len(chosen)} unseen instance(s) ({mode}) of {len(unseen)} "
        f"remaining ({len(seen)} previously predicted in {args.preds_path.name})",
        file=sys.stderr,
    )
    print(",".join(chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
