"""Forked skill execution.

Phase-5 / WI-5.1. Mirrors TS
``typescript/src/tools/SkillTool/SkillTool.ts:122-289`` (``executeForkedSkill``).

Closes gap #8: the previously-dead ``status == "forked"`` branch in
``_skill_map_result_to_api`` (skill.py:124-141 in the pre-Phase-5 layout)
becomes live code. Skills declaring ``context: 'fork'`` in frontmatter
now run with a separate context window — sub-agent token budget is
isolated from the parent's, per the chapter's "Forked Skills" semantic.

**Runner indirection (Phase 5 design choice).** ``execute_forked_skill``
does NOT spawn the sub-agent directly. Instead it calls a runner
callback wired onto ``ToolContext.forked_skill_runner``. Two reasons:

  1. **Provider injection.** The agent-tool's ``run_agent`` machinery
     is module-scoped over a ``BaseProvider`` instance, captured at
     ``build_agent_tool(provider, registry)``. SkillTool has no provider
     handle today; threading one through would require either a circular
     import or duplicating the spawn-and-collect loop. The runner
     callback resolves that without either tradeoff.
  2. **Testability.** Forked execution by definition makes an LLM call.
     Tests inject a stub runner that returns a fixed string; production
     bootstrap wires a real runner that drives ``run_agent`` with the
     skill's parameters. The fork code path is exercised in CI without
     spinning up a real provider.

When ``forked_skill_runner`` is None on the context, the fork branch
returns an ``is_error=True`` ToolResult. Skill authors get a clear
"forked execution unavailable" signal rather than a silent
degradation-to-inline.

Hook registration (skill frontmatter ``hooks:``) for forked skills
flows through ``register_frontmatter_hooks(is_agent=True)`` per the
B1-corrected gap analysis: forked skills are sub-agents, so their
``Stop`` hooks need converting to ``SubagentStop`` (the conversion
``register_skill_hooks`` does NOT do). The session_id is the
parent's (the sub-agent inherits the parent's session for hook scope);
this matches TS' executeForkedSkill behavior at SkillTool.ts:226-247.
"""

from __future__ import annotations

import logging
from typing import Any

from ..context import ToolContext
from ..protocol import ToolResult

logger = logging.getLogger(__name__)


async def execute_forked_skill(
    *,
    skill: Any,
    args: str,
    context: ToolContext,
    tool_use_id: str,
) -> ToolResult:
    """Run a skill in a forked sub-agent context. Returns the sub-agent's
    final result text packed into a ToolResult with ``status="forked"``.

    Caller responsibilities:
      * Skill prompt rendering — done by the caller (``_run_markdown_skill``).
      * Hook registration with ``is_agent=True`` — done here, after the
        runner returns, so hooks scoped to the forked execution don't
        outlive a runner that errored out.

    The runner indirection means this function is *almost entirely*
    bookkeeping; the actual sub-agent spawn lives in
    ``ToolContext.forked_skill_runner``.
    """
    from src.skills.runtime_substitution import render_skill_prompt
    from src.tool_system.tools.skill import _make_shell_executor

    runner = getattr(context, "forked_skill_runner", None)
    if runner is None:
        return ToolResult(
            name="Skill",
            output={
                "status": "forked",
                "commandName": skill.name,
                "error": (
                    "Forked skill execution requires a forked_skill_runner "
                    "on the ToolContext. Bootstrap wires this in production; "
                    "tests inject a stub. See "
                    "src/tool_system/tools/skill_fork.py for the contract."
                ),
            },
            is_error=True,
        )

    # Render the skill prompt the same way inline skills do (skill.py:_run_markdown_skill).
    # The forked sub-agent receives this rendered string as its initial
    # user prompt. Shell-block execution is gated on non-MCP sources;
    # MCP-loaded skills' shell blocks are left as text in the rendered
    # prompt for the sub-agent to interpret literally (per the chapter's
    # MCP security boundary).
    if getattr(skill, "get_prompt_for_command", None) is not None:
        prompt = skill.get_prompt_for_command(args or "")
    else:
        body = skill.markdown_content or skill.content or ""
        base_dir = skill.base_dir or skill.skill_root
        executor = _make_shell_executor(
            context, skill.allowed_tools, slash_command_name=f"/{skill.name}",
        )
        prompt = render_skill_prompt(
            body=body,
            args=args,
            base_dir=base_dir,
            argument_names=skill.argument_names,
            session_id=context.session_id,
            loaded_from=skill.loaded_from,
            slash_command_name=f"/{skill.name}",
            shell_executor=executor,
        )

    # Drive the sub-agent via the injected runner. Errors from the
    # runner surface as is_error=True ToolResults; the user sees the
    # underlying error message, not a generic "forked failed."
    try:
        result_text = await runner(
            prompt=prompt,
            allowed_tools=getattr(skill, "allowed_tools", None),
            model=getattr(skill, "model", None),
            effort=getattr(skill, "effort", None),
            parent_context=context,
        )
    except Exception as exc:
        logger.exception(
            "forked_skill_runner raised for skill %r", skill.name,
        )
        return ToolResult(
            name="Skill",
            output={
                "status": "forked",
                "commandName": skill.name,
                "error": f"Forked skill execution failed: {exc}",
            },
            is_error=True,
        )

    # Register skill-frontmatter hooks scoped to the parent session
    # but with ``is_agent=True`` so the Stop→SubagentStop conversion
    # fires (the forked skill IS a sub-agent for hook-routing purposes).
    # B1 correction: this conversion lives in register_frontmatter_hooks,
    # NOT register_skill_hooks. The forked-skill case is one of the few
    # places skill code calls register_frontmatter_hooks directly.
    skill_hooks = getattr(skill, "hooks", None)
    if skill_hooks and context.session_hook_registry is not None and context.session_id:
        from src.hooks.register_frontmatter_hooks import register_frontmatter_hooks
        try:
            count = await register_frontmatter_hooks(
                registry=context.session_hook_registry,
                session_id=context.session_id,
                frontmatter_hooks=skill_hooks,
                source_name=f"forked-skill {skill.name!r}",
                is_agent=True,
                skill_root=skill.skill_root,
            )
            if count:
                logger.debug(
                    "Forked skill %r registered %d session hooks (Stop→SubagentStop)",
                    skill.name, count,
                )
        except Exception:
            logger.exception(
                "register_frontmatter_hooks failed for forked skill %r",
                skill.name,
            )

    return ToolResult(
        name="Skill",
        output={
            "success": True,
            "status": "forked",
            "commandName": skill.name,
            "result": result_text,
            "loadedFrom": skill.loaded_from,
            "skillRoot": skill.skill_root,
            "allowedTools": skill.allowed_tools if skill.allowed_tools else None,
            "model": skill.model,
        },
    )
