"""Forked skill execution.

Phase-5 / WI-5.1 + Phase-5 follow-ups (D3, D4). Mirrors TS
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

    # D4 fix — register skill-frontmatter hooks BEFORE the runner so the
    # hooks fire on THIS forked skill's SubagentStop, not future
    # sub-agents'. ``is_agent=True`` triggers the B1 Stop→SubagentStop
    # conversion (the forked skill IS a sub-agent for hook-routing
    # purposes). Pre-D4 ordering registered AFTER the runner returned —
    # by which point the sub-agent's SubagentStop had already fired.
    # Chapter intent ("an agent's stop-verification hook fires when this
    # agent stops") was therefore unrealized despite the conversion being
    # plumbed correctly.
    #
    # Rollback contract: if the runner raises, the registered entries are
    # removed from the registry (try/finally with explicit remove). Avoids
    # a "the runner errored but its hooks are now active for the rest of
    # the session" leak.
    registered_entries: list[tuple[Any, Any]] = []
    skill_hooks = getattr(skill, "hooks", None)
    if skill_hooks and context.session_hook_registry is not None and context.session_id:
        from src.hooks.register_frontmatter_hooks import register_frontmatter_hooks
        try:
            registered_entries = await register_frontmatter_hooks(
                registry=context.session_hook_registry,
                session_id=context.session_id,
                frontmatter_hooks=skill_hooks,
                source_name=f"forked-skill {skill.name!r}",
                is_agent=True,
                skill_root=skill.skill_root,
            )
            if registered_entries:
                logger.debug(
                    "Forked skill %r registered %d session hooks (Stop→SubagentStop)",
                    skill.name, len(registered_entries),
                )
        except Exception:
            logger.exception(
                "register_frontmatter_hooks failed for forked skill %r",
                skill.name,
            )
            registered_entries = []

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
        # D4 rollback — remove the hooks we registered before the runner
        # ran. Keeps the session's hook surface clean of artifacts from a
        # forked skill that never actually ran.
        if registered_entries and context.session_hook_registry and context.session_id:
            from src.hooks.session_hooks import remove_session_hook
            for event, hook_config in registered_entries:
                try:
                    await remove_session_hook(
                        registry=context.session_hook_registry,
                        session_id=context.session_id,
                        event=event,
                        hook=hook_config,
                    )
                except Exception:
                    logger.exception(
                        "rollback of forked-skill hook registration failed "
                        "(skill=%r, event=%r)", skill.name, event,
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


# ---------------------------------------------------------------------------
# Phase-5 follow-up D3 — production runner factory.
# ---------------------------------------------------------------------------


def make_forked_skill_runner(
    *,
    provider: Any,
    tool_registry: Any,
):
    """Build a forked-skill runner closure over ``provider`` + ``tool_registry``.

    Phase-5 follow-up D3 (critic-flagged). Pre-D3, the prior commit's
    summary claimed "Production bootstrap wires this in production" but
    ``bootstrap_graph.py`` / ``bootstrap/state.py`` had zero references.
    Production sessions hit the "configure forked_skill_runner" error.

    This factory closes the gap: bootstrap calls
    ``make_forked_skill_runner(provider=provider, tool_registry=registry)``
    and mounts the result on ``ToolContext.forked_skill_runner``. The
    closure has the exact ``execute_forked_skill`` signature
    ``(prompt, allowed_tools, model, effort, parent_context) -> str``.

    The runner drives ``run_agent`` with the skill's parameters and
    returns the sub-agent's final assistant text. When ``allowed_tools``
    is set, the available-tool list is filtered to that allowlist.

    Falls back to ``general-purpose`` agent definition (the chapter's
    "default agent" — it has no agent-type-specific tools, so the skill's
    ``allowed_tools`` is the only filter that matters).

    Local imports below: avoids dragging the agent-tool / run_agent
    machinery into module-init paths that don't need it (e.g., test
    fixtures that only hit ``execute_forked_skill`` with a stub runner).
    """

    async def _runner(
        *,
        prompt: str,
        allowed_tools: list[str] | None,
        model: str | None,
        effort: str | None,
        parent_context: ToolContext,
    ) -> str:
        from src.agent.agent_definitions import find_agent_by_type, get_built_in_agents
        from src.agent.run_agent import RunAgentParams
        from src.tasks_core import generate_task_id
        from src.tool_system.tools.agent import (
            _collect_agent_messages,
            finalize_agent_tool,
        )
        import time as _time

        agent_def = find_agent_by_type(get_built_in_agents(), "general-purpose")
        if agent_def is None:
            raise RuntimeError(
                "No general-purpose agent definition available for forked skill"
            )

        # Filter the registry's tool list to ``allowed_tools`` when the
        # skill specifies one. Skills can scope their forked sub-agent's
        # capabilities — e.g., a research skill may only need Read and
        # Glob. ``None``/empty means "use parent's full tool list."
        available_tools = list(tool_registry.list_tools())
        if allowed_tools:
            allowed_set = set(allowed_tools)
            available_tools = [
                t for t in available_tools
                if getattr(t, "name", "") in allowed_set
            ]

        agent_id = generate_task_id("local_agent")
        start_time = _time.time()

        run_params = RunAgentParams(
            parent_context=parent_context,
            agent_definition=agent_def,
            prompt=prompt,
            available_tools=available_tools,
            tool_registry=tool_registry,
            provider=provider,
            model=model,
            agent_id=agent_id,
            is_async=False,
            max_turns=agent_def.max_turns,
        )

        messages = await _collect_agent_messages(run_params)
        result = finalize_agent_tool(
            messages,
            agent_id,
            {
                "start_time": start_time,
                "agent_type": agent_def.agent_type,
            },
        )
        # Return the sub-agent's final assistant text. ``content`` is the
        # canonical field per ``finalize_agent_tool``'s return shape.
        return result.content

    return _runner


def wire_forked_skill_runner(
    *,
    tool_context: ToolContext,
    provider: Any,
    tool_registry: Any,
) -> None:
    """Mount a production forked-skill runner on ``tool_context``.

    Convenience wrapper around ``make_forked_skill_runner`` so each
    bootstrap entry point (tui, repl, headless, subagent_context) has
    a one-liner instead of repeating the factory + assignment.

    Idempotent: a context that already has a runner is left unchanged
    (test fixtures that inject stubs aren't clobbered if they happen to
    flow through a bootstrap path).
    """
    if getattr(tool_context, "forked_skill_runner", None) is not None:
        return
    tool_context.forked_skill_runner = make_forked_skill_runner(
        provider=provider,
        tool_registry=tool_registry,
    )
