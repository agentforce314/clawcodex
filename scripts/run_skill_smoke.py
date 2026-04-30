#!/usr/bin/env python3
"""Manual smoke-test runner for the QA-2 example skills.

Loads every skill under ``tests/fixtures/skills/`` (plus the bundled
catalogue from DEV-5), prints a listing that mirrors what the model
would see in its system-reminder, and then invokes each in turn so you
can eyeball the rendered prompt.

Useful as:

  - A manual artifact for the user / team-lead ("here's what skills
    look like once they hit the model").
  - A quick smoke-check that the disk → registry → SkillTool pipeline
    is wired correctly without spinning up a real REPL.

Run from the repo root:

    python scripts/run_skill_smoke.py

No arguments. Output goes to stdout.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


# Make the repo root importable when the script is launched from a
# subdirectory.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from src.skills.bundled import init_bundled_skills  # noqa: E402
from src.skills.bundled_skills import clear_bundled_skills  # noqa: E402
from src.skills.loader import (  # noqa: E402
    activate_conditional_skills_for_paths,
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
    get_all_skills,
)
from src.tool_system.context import ToolContext  # noqa: E402
from src.tool_system.tools import SkillTool  # noqa: E402


FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures" / "skills"
FIXTURE_NAMES = ("commit-helper", "frontend", "lint-py")
PREVIEW_CHARS = 200


def _build_workspace(tmp: Path) -> Path:
    """Construct a workspace whose ``.claude/skills/`` mirrors the
    fixture catalogue. Returns the workspace root."""
    project = tmp / "proj"
    skills_root = project / ".claude" / "skills"
    skills_root.mkdir(parents=True)
    for name in FIXTURE_NAMES:
        shutil.copytree(FIXTURES_ROOT / name, skills_root / name)
    return project


def _isolate_env(tmp: Path) -> None:
    """Strip every env knob that would inject non-fixture skill dirs."""
    fake_home = tmp / "home"
    fake_home.mkdir()
    os.environ["HOME"] = str(fake_home)
    os.environ["CLAUDE_MANAGED_CONFIG_DIR"] = str(tmp / "managed")
    for var in (
        "CLAUDE_CONFIG_DIR",
        "CLAWCODEX_SKILLS_DIR",
        "CLAUDE_SKILLS_DIR",
        "CLAWCODEX_MANAGED_SKILLS_DIR",
        "CLAUDE_CODE_BARE_MODE",
        "CLAUDE_CODE_DISABLE_POLICY_SKILLS",
        "CLAUDE_CODE_ADDITIONAL_DIRECTORIES",
    ):
        os.environ.pop(var, None)


def _print_section(title: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n{title}\n{bar}")


def _print_listing(project: Path) -> None:
    skills = get_all_skills(project_root=project)
    _print_section(f"Available skills ({len(skills)}, unconditional only)")
    print(f"{'name':<30}  {'source':<10}  description")
    print("-" * 72)
    for s in sorted(skills, key=lambda x: x.name):
        desc = s.description or ""
        if len(desc) > 60:
            desc = desc[:57] + "..."
        print(f"{s.name:<30}  {s.loaded_from:<10}  {desc}")


def _print_invocation(
    skill_name: str, args: str, ctx: ToolContext
) -> None:
    result = SkillTool.call({"skill": skill_name, "args": args}, ctx)
    out = result.output
    head = f"--- /{skill_name}"
    if args:
        head += f' "{args}"'
    head += " ---"
    print(f"\n{head}")
    if not out.get("success"):
        print(f"  ERROR: {out}")
        return
    prompt = out["prompt"]
    preview = prompt if len(prompt) <= PREVIEW_CHARS else (
        prompt[:PREVIEW_CHARS] + f"\n... [truncated, total {len(prompt)} chars]"
    )
    for line in preview.splitlines():
        print(f"  {line}")
    if out.get("loadedFrom"):
        print(f"  [loadedFrom={out['loadedFrom']}, allowedTools={out.get('allowedTools')}]")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        _isolate_env(tmp)

        # Reset every cache + register the bundled catalogue.
        clear_skill_caches()
        clear_dynamic_skills()
        clear_skill_registry()
        clear_bundled_skills()
        init_bundled_skills()

        project = _build_workspace(tmp)
        ctx = ToolContext(workspace_root=project)
        ctx.session_id = "S-smoke-001"

        _print_listing(project)

        _print_section("Invocations (first 200 chars of each rendered prompt)")
        # commit-helper — substitutions + args
        _print_invocation("commit-helper", "feat", ctx)

        # frontend:add-component — namespaced + named arg
        _print_invocation("frontend:add-component", "Button", ctx)

        # simplify — bundled skill (no base-dir header)
        _print_invocation("simplify", "focus on caching", ctx)

        # lint-py — conditional skill: activate it by touching a
        # matching path, then invoke. As of bug fix #14, activated
        # conditional skills flow through `get_all_skills` →
        # `_skill_registry` automatically, so SkillTool resolves them
        # naturally on the next call (no manual registry promotion).
        _print_section("Activating conditional `lint-py` for src/foo.py")
        py_path = project / "src" / "foo.py"
        py_path.parent.mkdir(parents=True, exist_ok=True)
        py_path.write_text("# placeholder")
        activated = activate_conditional_skills_for_paths(
            [str(py_path)], str(project)
        )
        print(f"  activated: {activated}")
        _print_invocation("lint-py", "", ctx)

    return 0


if __name__ == "__main__":
    sys.exit(main())
