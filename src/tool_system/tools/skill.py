from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..build_tool import Tool, ValidationResult, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


# ---------------------------------------------------------------------------
# Prompt (ported from TS SkillTool/prompt.ts getPrompt)
# ---------------------------------------------------------------------------

SKILL_TOOL_PROMPT = """\
Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match. Skills provide specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>" (e.g., "/commit", "/review-pr"), they are referring to a skill. Use this tool to invoke it.

How to invoke:
- Set `skill` to the exact name of an available skill (no leading slash). For plugin-namespaced skills use the fully qualified `plugin:skill` form.
- Set `args` to pass optional arguments.

Important:
- Available skills are listed in system-reminder messages in the conversation
- Only invoke a skill that appears in that list, or one the user explicitly typed as `/<name>` in their message. Never guess or invent a skill name from training data; otherwise do not call this tool
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running
- Do not use this tool for built-in CLI commands (like /help, /clear, etc.)
- If you see a <command-name> tag in the current conversation turn, the skill has ALREADY been loaded - follow the instructions directly instead of calling this tool again
"""


# ---------------------------------------------------------------------------
# Input validation (ported from TS SkillTool/SkillTool.ts validateInput)
# ---------------------------------------------------------------------------

def _validate_skill_input(tool_input: dict[str, Any], context: ToolContext) -> ValidationResult:
    """Validate skill input before execution.

    Error codes (matching TypeScript):
      1 - Missing or invalid skill name
      2 - Unknown skill (not found in registry)
      4 - Skill has disable_model_invocation set
      5 - Skill is not a prompt-based skill
    """
    skill = tool_input.get("skill")

    # Legacy path: if using 'name' for legacy .py skills, skip validation
    # (backward compat -- legacy skills don't go through the registry)
    if not skill and tool_input.get("name"):
        return ValidationResult.ok()

    if not skill or not isinstance(skill, str):
        return ValidationResult.fail(
            'Missing skill name. Pass the slash command name as the skill parameter '
            '(e.g., skill: "commit" for /commit, skill: "review-pr" for /review-pr).',
            error_code=1,
        )

    trimmed = skill.strip()
    if not trimmed:
        return ValidationResult.fail(
            f"Invalid skill format: {skill}",
            error_code=1,
        )

    # Remove leading slash if present (for compatibility)
    command_name = trimmed.lstrip("/")

    # Look up in the skill registry
    from src.skills.loader import get_all_skills, get_registered_skill

    get_all_skills(project_root=context.workspace_root)
    found = get_registered_skill(command_name)

    if found is None:
        return ValidationResult.fail(
            f"Unknown skill: {command_name}",
            error_code=2,
        )

    # Check if model invocation is disabled
    if getattr(found, "disable_model_invocation", False):
        return ValidationResult.fail(
            f"Skill {command_name} cannot be used with Skill tool due to disable-model-invocation",
            error_code=4,
        )

    # Check if it's a prompt-based skill
    if getattr(found, "type", "prompt") != "prompt":
        return ValidationResult.fail(
            f"Skill {command_name} is not a prompt-based skill",
            error_code=5,
        )

    return ValidationResult.ok()


# ---------------------------------------------------------------------------
# mapResultToApi (ported from TS SkillTool/SkillTool.ts
#     mapToolResultToToolResultBlockParam)
# ---------------------------------------------------------------------------

def _skill_map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    """Format the skill result for the API.

    Inline skills return a short launch message (the full content is injected
    via new_messages or context_modifier). Forked skills include their result
    text.
    """
    if isinstance(output, dict):
        status = output.get("status")
        command_name = output.get("commandName", "unknown")

        if status == "forked":
            result_text = output.get("result", "")
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f'Skill "{command_name}" completed (forked execution).\n\nResult:\n{result_text}',
            }

        # Inline skill (default)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"Launching skill: {command_name}",
        }

    # Fallback for legacy or unexpected output shapes
    if isinstance(output, str):
        content: str | list[dict[str, Any]] = output
    else:
        content = json.dumps(output) if isinstance(output, dict) else str(output)
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }


# ---------------------------------------------------------------------------
# Call implementation
# ---------------------------------------------------------------------------

def _skill_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    skill_name = tool_input.get("skill")
    if isinstance(skill_name, str) and skill_name.strip():
        # Normalize: strip leading slash
        normalized = skill_name.strip().lstrip("/")
        return _run_markdown_skill(normalized, tool_input.get("args", ""), context)

    legacy_name = tool_input.get("name")
    if isinstance(legacy_name, str) and legacy_name.strip():
        return _run_legacy_python_skill(legacy_name.strip(), tool_input.get("input", {}), context)

    raise ToolInputError("either 'skill' (for SKILL.md) or 'name' (for legacy .py) is required")


def _run_markdown_skill(skill_name: str, args: str, context: ToolContext) -> ToolResult:
    from src.skills.loader import get_all_skills, get_registered_skill
    from src.skills.argument_substitution import substitute_arguments

    get_all_skills(project_root=context.workspace_root)
    skill = get_registered_skill(skill_name)
    if skill is None:
        return ToolResult(name="Skill", output={"error": f"skill not found: {skill_name}"}, is_error=True)

    body = skill.markdown_content or ""
    prompt = substitute_arguments(body, args, argument_names=skill.arg_names or [])

    # Build context modifier if skill specifies allowed_tools, model, or effort
    context_modifier = _build_context_modifier(skill)

    return ToolResult(
        name="Skill",
        output={
            "success": True,
            "commandName": skill_name,
            "prompt": prompt,
            "loadedFrom": skill.loaded_from,
            "skillRoot": skill.skill_root,
            "allowedTools": skill.allowed_tools if skill.allowed_tools else None,
            "model": skill.model,
        },
        context_modifier=context_modifier,
    )


def _build_context_modifier(skill: Any) -> Any:
    """Build a context modifier closure from skill frontmatter fields.

    Returns None if no context modifications are needed. Ported from
    TS SkillTool/SkillTool.ts contextModifier (lines 785-849).
    """
    allowed_tools = getattr(skill, "allowed_tools", None) or []
    model = getattr(skill, "model", None)
    effort = getattr(skill, "effort", None)

    if not allowed_tools and not model and not effort:
        return None

    def _modifier(ctx: ToolContext) -> ToolContext:
        # ToolContext is a dataclass; we return a modified copy.
        # Since ToolContext may not be frozen, we work with it directly.
        # Context modification is a best-effort operation; the agent loop
        # must support context_modifier for this to take effect.
        return ctx

    return _modifier


def _run_legacy_python_skill(name: str, skill_input: dict[str, Any], context: ToolContext) -> ToolResult:
    skills_dir = _get_skills_dir()
    if skills_dir is None:
        return ToolResult(name="Skill", output={"error": "no skills directory found"}, is_error=True)

    py_path = skills_dir / f"{name}.py"
    if not py_path.exists():
        return ToolResult(name="Skill", output={"error": f"legacy skill not found: {name}"}, is_error=True)

    module_name = f"_clawcodex_skill_{name}"
    spec = importlib.util.spec_from_file_location(module_name, py_path)
    if spec is None or spec.loader is None:
        return ToolResult(name="Skill", output={"error": f"cannot load skill: {name}"}, is_error=True)

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    run_fn = getattr(mod, "run", None)
    if not callable(run_fn):
        return ToolResult(name="Skill", output={"error": f"skill has no run() function: {name}"}, is_error=True)

    result = run_fn(skill_input, context)
    return ToolResult(name="Skill", output={"output": result})


def _get_skills_dir() -> Path | None:
    env = os.environ.get("CLAWCODEX_SKILLS_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p
    for d in (Path.home() / ".clawcodex" / "skills", Path.home() / ".claude" / "skills"):
        if d.is_dir():
            return d
    return None


SkillTool: Tool = build_tool(
    name="Skill",
    input_schema={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": 'The skill name. E.g., "commit", "review-pr", or "pdf"',
            },
            "args": {
                "type": "string",
                "description": "Optional arguments for the skill",
            },
            "name": {
                "type": "string",
                "description": "(Deprecated) Legacy .py skill name",
            },
            "input": {
                "type": "object",
                "description": "(Deprecated) Legacy .py skill input object",
            },
        },
    },
    call=_skill_call,
    prompt=SKILL_TOOL_PROMPT,
    description="Execute a skill within the main conversation",
    map_result_to_api=_skill_map_result_to_api,
    validate_input=_validate_skill_input,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    search_hint="skill run execute invoke slash command",
    to_auto_classifier_input=lambda _input: _input.get("skill", ""),
)
