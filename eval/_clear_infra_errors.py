"""Identify infra-only errors (docker-image-404, docker-timeout) per agent and
delete their per-instance harness logs so SWE-bench's skip-existing logic will
re-run them. Predictions stay cached.

Usage:
    python eval/_clear_infra_errors.py <run_id>
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(r"C:\Users\fmche\PycharmProjects\clawcodex")
SWEBENCH = REPO / "SWE-bench-dev"


def classify(log_text: str) -> str:
    if "ImageNotFound" in log_text or "No such image" in log_text:
        return "docker-image-404"
    if "patch unexpectedly ends in middle of line" in log_text:
        return "patch-missing-newline"
    if "Reversed (or previously applied) patch" in log_text:
        return "patch-reversed"
    if re.search(r"Hunk #\d+ FAILED at", log_text):
        return "patch-context-mismatch"
    if "Only garbage was found in the patch" in log_text:
        return "patch-garbage"
    if "Timeout" in log_text or "timed out" in log_text:
        return "docker-timeout"
    if "docker.errors" in log_text:
        return "docker-other"
    return "other"


def main(run_id: str) -> None:
    infra_categories = {"docker-image-404", "docker-timeout", "docker-other"}

    for agent in ("clawcodex", "openclaude"):
        summary_path = SWEBENCH / f"{agent}-local.{run_id}-{agent}.json"
        if not summary_path.exists():
            print(f"  [{agent}] no summary at {summary_path}; skipping")
            continue
        s = json.load(open(summary_path, encoding="utf-8"))
        err_ids = s.get("error_ids", [])

        infra_ids = []
        for iid in err_ids:
            log = (
                SWEBENCH / "logs" / "run_evaluation" / f"{run_id}-{agent}"
                / f"{agent}-local" / iid / "run_instance.log"
            )
            if not log.exists():
                continue
            cat = classify(log.read_text(encoding="utf-8", errors="replace"))
            if cat in infra_categories:
                infra_ids.append(iid)

        print(f"[{agent}] {len(infra_ids)} infra-error instances of {len(err_ids)} total errors")

        # Delete per-instance dirs so harness re-runs them
        for iid in infra_ids:
            d = (
                SWEBENCH / "logs" / "run_evaluation" / f"{run_id}-{agent}"
                / f"{agent}-local" / iid
            )
            if d.exists():
                shutil.rmtree(d)

        # Delete summary so it gets regenerated cleanly
        summary_path.unlink(missing_ok=True)
        print(f"  cleared {len(infra_ids)} dirs + summary JSON for {agent}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "verified-gemini-full")
