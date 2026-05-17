"""Repair an existing predictions JSONL: re-extract diff, recompute hunk
headers from each row's ``full_output``, and rewrite ``model_patch`` in place.

Useful when ``run_custom_api.py`` produced empty patches because the LLM emitted
malformed ``@@`` line counts and ``unidiff`` rejected them. The recomputer
fixes the bookkeeping deterministically.

Usage:
    python eval/repair_preds.py <predictions.jsonl>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "SWE-bench-dev"))
sys.path.insert(0, str(REPO_ROOT / "SWE-bench-dev" / "scripts"))

from swebench.inference.make_datasets.utils import extract_diff  # noqa: E402
from run_custom_api import is_valid_unified_diff, recompute_hunk_headers  # noqa: E402


def repair(path: Path) -> None:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    changes = 0
    rescued = 0
    for r in rows:
        old_patch = r.get("model_patch") or ""
        full = r.get("full_output") or ""
        new_patch = recompute_hunk_headers(extract_diff(full) or "")
        if new_patch and not new_patch.endswith("\n"):
            new_patch += "\n"
        if new_patch != old_patch:
            changes += 1
        had_valid = is_valid_unified_diff(old_patch)
        has_valid = is_valid_unified_diff(new_patch)
        if not had_valid and has_valid:
            rescued += 1
        elif had_valid and not has_valid:
            print(f"  WARNING: {r['instance_id']} was valid, repair broke it — keeping original")
            continue
        r["model_patch"] = new_patch

    out_lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"  repaired {path}: changed {changes}/{len(rows)} rows; rescued {rescued} from empty/invalid")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python eval/repair_preds.py <predictions.jsonl>")
        sys.exit(2)
    repair(Path(sys.argv[1]))
