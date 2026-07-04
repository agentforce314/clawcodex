"""Bundled ``/batch`` skill — verbatim port of ``bundled/batch.ts``.

Orchestrates a large, parallelizable change: research + plan in plan mode,
then spawn 5–30 isolated-worktree agents that each implement one unit and
open a PR. User-invocable only (``disable_model_invocation``).
"""

from __future__ import annotations

from ..bundled_skills import BundledSkillDefinition, register_bundled_skill

# Port tool-name literals (TS interpolates the *_TOOL_NAME constants; the
# Python tool registry uses these exact names — verified in
# src/tool_system/tools/{plan_mode,agent,ask_user_question,skill}.py).
_AGENT_TOOL_NAME = "Agent"
_ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"
_ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"
_EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"
_SKILL_TOOL_NAME = "Skill"

_MIN_AGENTS = 5
_MAX_AGENTS = 30

WORKER_INSTRUCTIONS = f"""After you finish implementing the change:
1. **Simplify** — Invoke the `{_SKILL_TOOL_NAME}` tool with `skill: "simplify"` to review and clean up your changes.
2. **Run unit tests** — Run the project's test suite (check for package.json scripts, Makefile targets, or common commands like `npm test`, `bun test`, `pytest`, `go test`). If tests fail, fix them.
3. **Test end-to-end** — Follow the e2e test recipe from the coordinator's prompt (below). If the recipe says to skip e2e for this unit, skip it.
4. **Commit and push** — Commit all changes with a clear message, push the branch, and create a PR with `gh pr create`. Use a descriptive title. If `gh` is not available or the push fails, note it in your final message.
5. **Report** — End with a single line: `PR: <url>` so the coordinator can track it. If no PR was created, end with `PR: none — <reason>`."""


def _build_prompt(instruction: str) -> str:
    return f"""# Batch: Parallel Work Orchestration

You are orchestrating a large, parallelizable change across this codebase.

## User Instruction

{instruction}

## Phase 1: Research and Plan (Plan Mode)

Call the `{_ENTER_PLAN_MODE_TOOL_NAME}` tool now to enter plan mode, then:

1. **Understand the scope.** Launch one or more subagents (in the foreground — you need their results) to deeply research what this instruction touches. Find all the files, patterns, and call sites that need to change. Understand the existing conventions so the migration is consistent.

2. **Decompose into independent units.** Break the work into {_MIN_AGENTS}–{_MAX_AGENTS} self-contained units. Each unit must:
   - Be independently implementable in an isolated git worktree (no shared state with sibling units)
   - Be mergeable on its own without depending on another unit's PR landing first
   - Be roughly uniform in size (split large units, merge trivial ones)

   Scale the count to the actual work: few files → closer to {_MIN_AGENTS}; hundreds of files → closer to {_MAX_AGENTS}. Prefer per-directory or per-module slicing over arbitrary file lists.

3. **Determine the e2e test recipe.** Figure out how a worker can verify its change actually works end-to-end — not just that unit tests pass. Look for:
   - A `claude-in-chrome` skill or browser-automation tool (for UI changes: click through the affected flow, screenshot the result)
   - A `tmux` or CLI-verifier skill (for CLI changes: launch the app interactively, exercise the changed behavior)
   - A dev-server + curl pattern (for API changes: start the server, hit the affected endpoints)
   - An existing e2e/integration test suite the worker can run

   If you cannot find a concrete e2e path, use the `{_ASK_USER_QUESTION_TOOL_NAME}` tool to ask the user how to verify this change end-to-end. Offer 2–3 specific options based on what you found (e.g., "Screenshot via chrome extension", "Run `bun run dev` and curl the endpoint", "No e2e — unit tests are sufficient"). Do not skip this — the workers cannot ask the user themselves.

   Write the recipe as a short, concrete set of steps that a worker can execute autonomously. Include any setup (start a dev server, build first) and the exact command/interaction to verify.

4. **Write the plan.** In your plan file, include:
   - A summary of what you found during research
   - A numbered list of work units — for each: a short title, the list of files/directories it covers, and a one-line description of the change
   - The e2e test recipe (or "skip e2e because …" if the user chose that)
   - The exact worker instructions you will give each agent (the shared template)

5. Call `{_EXIT_PLAN_MODE_TOOL_NAME}` to present the plan for approval.

## Phase 2: Spawn Workers (After Plan Approval)

Once the plan is approved, spawn one background agent per work unit using the `{_AGENT_TOOL_NAME}` tool. **All agents must use `isolation: "worktree"` and `run_in_background: true`.** Launch them all in a single message block so they run in parallel.

For each agent, the prompt must be fully self-contained. Include:
- The overall goal (the user's instruction)
- This unit's specific task (title, file list, change description — copied verbatim from your plan)
- Any codebase conventions you discovered that the worker needs to follow
- The e2e test recipe from your plan (or "skip e2e because …")
- The worker instructions below, copied verbatim:

```
{WORKER_INSTRUCTIONS}
```

Use `subagent_type: "general-purpose"` unless a more specific agent type fits.

## Phase 3: Track Progress

After launching all workers, render an initial status table:

| # | Unit | Status | PR |
|---|------|--------|----|
| 1 | <title> | running | — |
| 2 | <title> | running | — |

As background-agent completion notifications arrive, parse the `PR: <url>` line from each agent's result and re-render the table with updated status (`done` / `failed`) and PR links. Keep a brief failure note for any agent that did not produce a PR.

When all agents have reported, render the final table and a one-line summary (e.g., "22/24 units landed as PRs").
"""


_NOT_A_GIT_REPO_MESSAGE = (
    "This is not a git repository. The `/batch` command requires a git repo "
    "because it spawns agents in isolated git worktrees and creates PRs from "
    "each. Initialize a repo first, or run this from inside an existing one."
)

_MISSING_INSTRUCTION_MESSAGE = """Provide an instruction describing the batch change you want to make.

Examples:
  /batch migrate from react to vue
  /batch replace all uses of lodash with native equivalents
  /batch add type annotations to all untyped function parameters"""


def _get_prompt_for_command(args: str) -> str:
    instruction = args.strip()
    if not instruction:
        return _MISSING_INSTRUCTION_MESSAGE
    # TS awaits getIsGit(); the port's prompt-builder signature is sync, so
    # use the sync get_is_git helper.
    from src.context_system.git_context import get_is_git

    if not get_is_git():
        return _NOT_A_GIT_REPO_MESSAGE
    return _build_prompt(instruction)


def register_batch_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            name="batch",
            description=(
                "Research and plan a large-scale change, then execute it in "
                "parallel across 5–30 isolated worktree agents that each open "
                "a PR."
            ),
            when_to_use=(
                "Use when the user wants to make a sweeping, mechanical change "
                "across many files (migrations, refactors, bulk renames) that "
                "can be decomposed into independent parallel units."
            ),
            argument_hint="<instruction>",
            user_invocable=True,
            disable_model_invocation=True,
            get_prompt_for_command=_get_prompt_for_command,
        )
    )
