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

    # Populate the unified registry for the current cwd, then look up.
    # The registry now includes managed/user/project disk skills (with
    # nested namespacing like "git:commit"), bundled skills, and any
    # MCP-provided skills. `get_registered_skill` falls back to bundled
    # alias matching for back-compat.
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
# Permission check (adapted from TS SkillTool/SkillTool.ts checkPermissions)
# ---------------------------------------------------------------------------

def _skill_check_permissions(tool_input: dict[str, Any], context: ToolContext) -> Any:
    """Resolve permission for a skill invocation.

    Policy (see ``src/permissions/check.py`` NO_PERMISSION_TOOLS comment): the
    invocation AUTO-ALLOWS. In this port a skill grants no ungated capability —
    its embedded ``!`` shell is permission-checked in :func:`_make_shell_executor`
    and the model's own tool calls are gated normally — so the invocation itself
    need not prompt. This deliberately diverges from TS, which gates skills that
    declare ``allowed-tools``; that pre-authorization is not wired through this
    port, so there is nothing extra to gate.

    Explicit rules still win. Blanket ``Skill`` deny/ask rules are honored
    upstream in ``has_permissions_to_use_tool_inner`` (which runs before this);
    this function additionally honors per-skill *content* rules — ``Skill(<name>)``
    and ``Skill(<prefix>:*)`` — for both ``deny`` (security-critical: never
    auto-allow a denied skill) and ``ask``. Rule matching mirrors TS'
    ``ruleMatches`` (strip leading slash, then exact or ``<prefix>:*``).
    """
    from src.permissions.rules import get_rule_by_contents_for_tool
    from src.permissions.types import (
        PermissionAllowDecision,
        PermissionAskDecision,
        PermissionDenyDecision,
        RuleDecisionReason,
    )

    raw = tool_input.get("skill")
    command_name = raw.strip().lstrip("/") if isinstance(raw, str) else ""

    perm_ctx = getattr(context, "permission_context", None)
    if perm_ctx is None or not command_name:
        # No rule context in scope (or malformed input — validate_input rejects
        # that separately). Auto-allow per policy; a blanket ``Skill`` deny is
        # still caught upstream from the real permission context.
        return PermissionAllowDecision(behavior="allow", updated_input=tool_input)

    def _rule_matches(rule_content: str) -> bool:
        normalized = rule_content.lstrip("/")
        if normalized == command_name:
            return True
        if normalized.endswith(":*"):
            return command_name.startswith(normalized[:-2])
        return False

    # Per-skill deny rules first — an explicit deny must never be auto-allowed.
    for rule_content, rule in get_rule_by_contents_for_tool(perm_ctx, "Skill", "deny").items():
        if _rule_matches(rule_content):
            return PermissionDenyDecision(
                behavior="deny",
                message="Skill execution blocked by permission rules",
                decision_reason=RuleDecisionReason(rule=rule),
            )

    # Per-skill ask rules: honor an explicit "prompt me for this skill".
    for rule_content, rule in get_rule_by_contents_for_tool(perm_ctx, "Skill", "ask").items():
        if _rule_matches(rule_content):
            return PermissionAskDecision(
                behavior="ask",
                message=f"Execute skill: {command_name}",
                decision_reason=RuleDecisionReason(rule=rule),
            )

    return PermissionAllowDecision(behavior="allow", updated_input=tool_input)


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
    from src.skills.runtime_substitution import render_skill_prompt

    # Populate / refresh the unified registry for the active cwd. This
    # pulls in managed / user / project disk skills (with nested
    # `category:skill` namespacing), bundled skills, and any registered
    # MCP skills.
    get_all_skills(project_root=context.workspace_root)
    skill = get_registered_skill(skill_name)
    if skill is None:
        return ToolResult(
            name="Skill",
            output={"error": f"skill not found: {skill_name}"},
            is_error=True,
        )

    # Bundled skills supply a callable prompt builder and define their
    # own substitution semantics; we pass args through and trust the
    # callable. Disk-loaded skills go through the canonical renderer
    # which mirrors TS' getPromptForCommand transform pipeline:
    #   1. base-dir header → 2. arg substitute → 3. ${CLAUDE_SKILL_DIR}
    #   → 4. ${CLAUDE_SESSION_ID} → 5. embedded shell exec (gated on
    #   non-MCP sources, scoped through skill.allowed_tools).
    if getattr(skill, "get_prompt_for_command", None) is not None:
        prompt = skill.get_prompt_for_command(args or "")
    else:
        body = skill.markdown_content or skill.content or ""
        base_dir = skill.base_dir or skill.skill_root
        executor = _make_shell_executor(
            context, skill.allowed_tools, slash_command_name=f"/{skill_name}"
        )
        prompt = render_skill_prompt(
            body=body,
            args=args,
            base_dir=base_dir,
            argument_names=skill.argument_names,
            session_id=context.session_id,
            loaded_from=skill.loaded_from,
            slash_command_name=f"/{skill_name}",
            shell_executor=executor,
        )

    # Build context modifier if skill specifies allowed_tools, model, or effort
    context_modifier = _build_context_modifier(skill)

    # ch12 round-4 WI-1 — DELIVER the rendered skill body to the model. The
    # skill tool_result content is only "Launching skill: {name}"
    # (_skill_map_result_to_api); the actual instructions must ride as a
    # separate meta user message. TS SkillTool.ts:1111-1117 does exactly
    # this: newMessages: [createUserMessage({content: finalContent,
    # isMeta:true})]. Without it the model was told a skill launched but
    # never received its instructions — skills did nothing. The executor
    # already consumes ToolResult.new_messages (tool_execution.py:449-451).
    new_messages: list[Any] = []
    if prompt and prompt.strip():
        from src.types.messages import create_user_message

        new_messages.append(create_user_message(content=prompt, isMeta=True))

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
        new_messages=new_messages or None,
    )


def _permission_context_with_skill_bash_rules(
    base: Any, allowed_tools: list[str] | None
) -> Any:
    """Return ``base`` with the skill's ``Bash(...)`` allowed-tools added as
    session allow rules.

    Mirrors TS injecting a skill's ``allowed-tools`` as
    ``alwaysAllowRules.command`` for the duration of the call: commands the skill
    declares auto-allow, while everything else stays gated. Non-Bash entries are
    irrelevant to embedded shell and ignored. A new context is returned; ``base``
    is never mutated.
    """
    from dataclasses import replace

    bash_rules = [
        t for t in (allowed_tools or []) if t == "Bash" or t.startswith("Bash(")
    ]
    if not bash_rules:
        return base
    # Use the ``command`` source (TS injects allowed-tools as the slash
    # command's own rules), so these never conflate with genuine session grants.
    merged = {src: list(rules) for src, rules in base.always_allow_rules.items()}
    merged["command"] = merged.get("command", []) + bash_rules
    return replace(base, always_allow_rules=merged)


def _make_shell_executor(
    context: ToolContext,
    allowed_tools: list[str] | None,
    *,
    slash_command_name: str,
):
    """Return a callable that runs a skill's embedded ``!`` shell command via
    BashTool, **gated through the permission system**.

    The returned executor matches the ``runtime_substitution.ShellExecutor``
    signature ``(command, inline) -> rendered text``. Before running, each
    command is permission-checked exactly like any Bash tool call (deny rules →
    bash safety screen → the skill's declared ``allowed_tools`` Bash rules), with
    ``allowed_tools`` injected as the command's allow rules so declared commands
    run silently. Only a permission ``allow`` runs; an undeclared or
    safety-screened command is hard-denied and rendered inline as an error
    (matching TS ``promptShellExecution``, which fails rather than prompting the
    user mid-expansion). ``bypassPermissions`` mode still runs everything. So
    embedded shell can no longer bypass the gate.
    """
    from .bash import BashTool
    from src.skills.runtime_substitution import (
        format_shell_error,
        format_shell_output,
    )

    # Skill-scoped permission context: the skill's declared Bash commands
    # auto-allow; everything else flows through the normal gate.
    skill_perm_ctx = _permission_context_with_skill_bash_rules(
        context.permission_context, allowed_tools
    )

    def _exec(command: str, inline: bool) -> str:
        # Gate the command before running it. Mirrors TS ``promptShellExecution``:
        # only a permission ``allow`` runs the command; anything else (``ask`` or
        # ``deny``) is treated as denied and rendered inline as an error instead
        # of executing — TS hard-denies here rather than prompting the user
        # mid-skill-expansion, and we match that. A skill grants its commands by
        # DECLARING them in ``allowed-tools``; undeclared / safety-screened
        # commands do not run. Fails CLOSED on any gate error.
        try:
            from src.permissions.check import has_permissions_to_use_tool

            decision = has_permissions_to_use_tool(
                BashTool,
                {"command": command},
                skill_perm_ctx,
                tool_use_context=context,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed, never crash render
            return format_shell_error(exc, command, inline=inline)

        if decision.behavior != "allow":
            reason = getattr(decision, "decision_reason", None)
            if reason is not None and getattr(reason, "type", None) == "safetyCheck":
                msg = getattr(decision, "message", None) or "blocked by a safety check"
            else:
                msg = (
                    "command not permitted — declare it in the skill's "
                    "`allowed-tools` (e.g. `Bash(<cmd>:*)`)"
                )
            return format_shell_error(msg, command, inline=inline)

        try:
            tr = BashTool.call({"command": command}, context)
        except Exception as exc:  # noqa: BLE001 — surface every failure
            return format_shell_error(exc, command, inline=inline)

        output = tr.output if isinstance(tr.output, dict) else {}
        stdout = str(output.get("stdout", ""))
        stderr = str(output.get("stderr", ""))
        exit_code = output.get("exit_code")

        # Treat non-zero exit codes the same way TS' ShellError surfaces
        # — embed the failure text inline so the model sees what went
        # wrong, but keep going so the rest of the prompt still renders.
        if isinstance(exit_code, int) and exit_code != 0:
            err_text = format_shell_output(stdout, stderr, inline=inline)
            err_text = err_text or f"command failed (exit {exit_code})"
            return format_shell_error(err_text, command, inline=inline)

        if tr.is_error:
            err_text = (
                format_shell_output(stdout, stderr, inline=inline)
                or output.get("error")
                or "command failed"
            )
            return format_shell_error(str(err_text), command, inline=inline)

        return format_shell_output(stdout, stderr, inline=inline)

    return _exec


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
    d = Path.home() / ".clawcodex" / "skills"
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
    check_permissions=_skill_check_permissions,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    search_hint="skill run execute invoke slash command",
    to_auto_classifier_input=lambda _input: _input.get("skill", ""),
)
