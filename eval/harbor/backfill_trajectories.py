"""Backfill ATIF ``trajectory.json`` for a past Harbor job — no re-run.

The ``clawcodex`` adapter now emits a trajectory per trial
(``clawcodex_agent.Clawcodex.populate_context_post_run``), but jobs run
before that landed have none. Every trial still kept its stream-json log
(``agent/clawcodex.txt``), which carries the full tool-call/result
sequence, the final answer, and token usage — enough to reconstruct the
trajectory offline via the adapter's own fallback converter.

Usage (from the repo root, in Harbor's venv so ``harbor`` imports resolve)::

    PYTHONPATH=eval/harbor \
      ~/.local/share/uv/tools/harbor/bin/python \
      eval/harbor/backfill_trajectories.py eval/harbor/jobs/<job-dir> [...]

Writes ``agent/trajectory.json`` into each trial dir (same location the
built-in claude-code agent uses), skipping trials that already have one
unless ``--force`` is passed. Trials whose log is empty/crashed before any
event produce no trajectory (nothing to reconstruct) and are reported.

Note: reconstructed trajectories use the stream-json FALLBACK path, so
per-step assistant narration is absent (the pre-``session.save()`` print
path persisted no conversation). Fresh runs get the rich, narrated
trajectory automatically.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# The adapter lives next to this file; import it directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clawcodex_agent import Clawcodex  # noqa: E402

from harbor.models.agent.context import AgentContext  # noqa: E402


def _model_name(trial_dir: Path) -> tuple[str | None, str | None]:
    """(model_name, version) from the trial's result.json agent_info."""
    try:
        info = json.loads((trial_dir / "result.json").read_text()).get(
            "agent_info"
        ) or {}
    except (OSError, ValueError):
        return None, None
    model_info = info.get("model_info") or {}
    model = model_info.get("name")
    provider = model_info.get("provider")
    model_name = f"{provider}/{model}" if provider and model else model
    return model_name, info.get("version")


def _task_instruction(trial_dir: Path) -> str | None:
    """Recover the task instruction from the Harbor task cache, so a
    reconstructed trajectory can open with the user step like a live run.

    The trial's ``config.json`` records ``task.name`` + ``task.ref``
    (``sha256:<hash>``); Harbor caches the compiled task under
    ``~/.cache/harbor/tasks/packages/<name>/<hash>/instruction.md``.
    Returns ``None`` when the cache entry isn't present.
    """
    try:
        task = json.loads((trial_dir / "config.json").read_text()).get("task") or {}
    except (OSError, ValueError):
        return None
    name = task.get("name")
    ref = task.get("ref") or ""
    if not name or not ref.startswith("sha256:"):
        return None
    cache = Path.home() / ".cache" / "harbor" / "tasks" / "packages"
    instr = cache / name / ref[len("sha256:") :] / "instruction.md"
    try:
        text = instr.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def backfill_job(job_dir: Path, *, force: bool) -> tuple[int, int, int]:
    """Returns (written, skipped_existing, no_events)."""
    written = skipped = empty = 0
    for trial_dir in sorted(p for p in job_dir.iterdir() if p.is_dir()):
        agent_dir = trial_dir / "agent"
        log = agent_dir / "clawcodex.txt"
        if not log.exists():
            continue
        out = agent_dir / "trajectory.json"
        if out.exists() and not force:
            skipped += 1
            continue
        model_name, version = _model_name(trial_dir)
        agent = Clawcodex(logs_dir=agent_dir, model_name=model_name, version=version)
        # Give the reconstruction the task instruction (opening user step),
        # recovered from the Harbor task cache; harmless when unavailable.
        instruction = _task_instruction(trial_dir)
        if instruction:
            agent._captured_instruction = instruction
        agent.populate_context_post_run(AgentContext())
        if out.exists():
            written += 1
        else:
            empty += 1
            print(f"  no events to reconstruct: {trial_dir.name}")
    return written, skipped, empty


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dirs", nargs="+", type=Path)
    parser.add_argument(
        "--force", action="store_true", help="overwrite existing trajectory.json"
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)  # the adapter's debug logs are noise here

    total_w = total_s = total_e = 0
    for job_dir in args.job_dirs:
        if not job_dir.is_dir():
            print(f"skip (not a dir): {job_dir}", file=sys.stderr)
            continue
        w, s, e = backfill_job(job_dir, force=args.force)
        print(f"{job_dir}: wrote {w}, skipped-existing {s}, no-events {e}")
        total_w += w
        total_s += s
        total_e += e
    print(f"total: wrote {total_w}, skipped {total_s}, no-events {total_e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
