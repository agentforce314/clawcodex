"""
Built-in commands for Claw Codex.

Implements core commands like /help, /clear, /exit, /skills, etc.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Optional

from ..context_system.builder import build_context_prompt
from ..context_system.context_analyzer import (
    analyze_context,
    format_context_as_markdown,
    get_context_window_for_model,
)
from ..context_system.microcompact import microcompact_messages, strip_images_from_messages
from ..cost_tracker import CostTracker
from ..history import HistoryLog
from ..providers.base import BaseProvider
from ..setup import run_setup
from .engine import CommandContext, CommandResult, LocalCommandResult
from .registry import CommandRegistry, get_command_registry, list_commands
from .types import Command, CommandType, CompactionResult, LocalCommand, PromptCommand


# Official Claude Code /init prompts (Simplified)
NEW_INIT_PROMPT = """Set up a CLAUDE.md file for this repo. CLAUDE.md is loaded into every Claude Code session, so it must be concise — only include what Claude would get wrong without it.

## Step 1: Ask what to set up

Use AskUserQuestion to ask the user:
- "Which CLAUDE.md files should /init set up?" with options: "Project CLAUDE.md" | "Personal CLAUDE.local.md" | "Both project + personal"

Use AskUserQuestion to ask:
- "Also set up skills and hooks?" with options: "Skills + hooks" | "Skills only" | "Hooks only" | "Neither, just CLAUDE.md"

## Step 2: Explore the codebase

Use tools to understand the project:
- Read key files: README, package.json, pyproject.toml, Cargo.toml, Makefile, existing CLAUDE.md
- Detect: build/test/lint commands, languages, frameworks, project structure
- Detect: code style rules, required env vars, gotchas
- Check for formatter config (ruff, black, prettier, etc.)

## Step 3: Ask follow-up questions (if needed)

Use AskUserQuestion to ask only things you CAN'T figure out from code:
- User's role (e.g., "backend engineer", "new hire")
- Non-obvious workflows or commands
- Communication preferences (terse vs detailed)

## Step 4: Write CLAUDE.md

Write a minimal CLAUDE.md at the project root.

Include:
- Build/test/lint commands that aren't standard (e.g., "uv run pytest" not just "pytest")
- Code style rules that DIFFER from defaults
- Required env vars or setup steps
- Non-obvious gotchas

Exclude:
- File structure (Claude can discover this)
- Standard conventions Claude already knows
- Generic advice

Prefix with:
```
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
```

If CLAUDE.md exists: read it, propose specific improvements.

## Step 5: Write CLAUDE.local.md (if user chose personal or both)

Write CLAUDE.local.md at project root. Add it to .gitignore.

Include:
- User's role and familiarity with codebase
- Personal sandbox URLs, test accounts
- Communication preferences

## Step 6: Create skills (if user chose skills)

Create skills at `.claude/skills/<name>/SKILL.md`:
```yaml
---
name: <skill-name>
description: <what it does>
---

<Instructions>
```

## Step 7: Summary

Tell the user what was set up and suggest any additional optimizations."""

# Fallback prompt for simpler initialization
OLD_INIT_PROMPT = """Please analyze this codebase and create a CLAUDE.md file, which will be given to future instances of Claude Code to operate in this repository.

What to add:
1. Commands that will be commonly used, such as how to build, lint, and run tests. Include the necessary commands to develop in this codebase, such as how to run a single test.
2. High-level code architecture and structure so that future instances can be productive more quickly. Focus on the "big picture" architecture that requires reading multiple files to understand.

Usage notes:
- If there's already a CLAUDE.md, suggest improvements to it.
- When you make the initial CLAUDE.md, do not repeat yourself and do not include obvious instructions like "Provide helpful error messages to users", "Write unit tests for all new utilities", "Never include sensitive information (API keys, tokens) in code or commits".
- Avoid listing every component or file structure that can be easily discovered.
- Don't include generic development practices.
- If there are Cursor rules (in .cursor/rules/ or .cursorrules) or Copilot rules (in .github/copilot-instructions.md), make sure to include the important parts.
- If there is a README.md, make sure to include the important parts.
- Do not make up information such as "Common Development Tasks", "Tips for Development", "Support and Documentation" unless this is expressly included in other files that you read.
- Be sure to prefix the file with the following text:

```
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
```"""


def clear_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """
    Handle /clear command - clear conversation history.

    Args:
        args: Command arguments
        context: Command context

    Returns:
        LocalCommandResult
    """
    if hasattr(context.conversation, "clear"):
        context.conversation.clear()

    if hasattr(context.history, "events"):
        context.history.events.clear()

    return LocalCommandResult(
        type="text",
        value="Conversation cleared.",
    )


def help_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """
    Handle /help command - show available commands.

    Args:
        args: Command arguments (optional search query)
        context: Command context

    Returns:
        LocalCommandResult
    """
    registry = get_command_registry()
    query = args.strip()

    if query:
        commands = registry.find_commands(query, limit=50)
        header = f"Commands matching '{query}':"
    else:
        commands = registry.list_commands(include_hidden=False)
        header = "Available commands:"

    lines = [header, ""]

    for cmd in commands:
        alias_str = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
        lines.append(f"  /{cmd.name}{alias_str}")
        lines.append(f"      {cmd.description}")
        if cmd.argument_hint:
            lines.append(f"      Usage: /{cmd.name} {cmd.argument_hint}")
        lines.append("")

    return LocalCommandResult(
        type="text",
        value="\n".join(lines),
    )


def _build_skills_menu_items(skills: list) -> list[dict[str, Any]]:
    """Build the structured menu-item list for ``/skills``.

    Phase-9 / WI-9.3 — interactive variant data shape. Each item is a
    dict with the fields a TUI menu component (or SDK consumer) needs:

      * ``name``: skill identifier (e.g., ``"commit"`` or ``"git:commit"``)
      * ``description``: one-line description from frontmatter
      * ``when_to_use``: optional usage guidance from frontmatter
      * ``source``: the chapter's "loaded_from" tier (``user`` /
        ``project`` / ``bundled`` / ``mcp``)
      * ``status``: ``"installed"`` (always — current implementation
        only surfaces installed skills; ``"available"`` is reserved for
        a future skill-marketplace integration)
      * ``has_hooks``: True if the skill declares frontmatter hooks
        (helpful indicator for users debugging hook firings)
      * ``context``: ``"inline"`` or ``"fork"`` (forked skills run in
        their own context window — relevant signal for the menu)

    The structured shape is what TUI components consume; the textual
    fallback (the existing ``/skills`` rendering) is built on top of
    the same items so the two views never drift.
    """
    items: list[dict[str, Any]] = []
    for skill in skills:
        items.append({
            "name": skill.name,
            "description": skill.description or "",
            "when_to_use": skill.when_to_use,
            "source": getattr(skill, "loaded_from", None) or "unknown",
            "status": "installed",
            "has_hooks": bool(getattr(skill, "hooks", None)),
            "context": getattr(skill, "context", "inline") or "inline",
        })
    return items


def _format_skills_menu_text(items: list[dict[str, Any]]) -> str:
    """Textual fallback rendering when no interactive UI is available.

    Mirrors the pre-Phase-9 flat listing but adds the source/context/
    has_hooks columns for parity with the interactive menu.
    """
    if not items:
        return (
            "No skills available. Add skills to ~/.clawcodex/skills/ or "
            "./.clawcodex/skills/."
        )

    lines = ["Available skills:", ""]
    for item in items:
        # Inline indicators for non-default options:
        #   [F] = forked execution (context: 'fork')
        #   [H] = declares frontmatter hooks
        flags = []
        if item["context"] == "fork":
            flags.append("F")
        if item["has_hooks"]:
            flags.append("H")
        flag_str = f" [{','.join(flags)}]" if flags else ""

        lines.append(f"  {item['name']}{flag_str}  ({item['source']})")
        if item["description"]:
            lines.append(f"      {item['description']}")
        if item["when_to_use"]:
            lines.append(f"      When to use: {item['when_to_use']}")
        lines.append("")

    lines.append("Flags: F=forked-execution, H=declares-frontmatter-hooks")
    return "\n".join(lines)


def skills_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """Handle ``/skills`` command.

    Phase-9 / WI-9.3: builds a structured menu-item list (consumable by
    interactive TUI components) and formats a text fallback for the
    REPL / non-interactive paths. Both views share the same data
    so they never drift.

    The structured items are exposed on the result via the
    ``menu_items`` attribute (when supported by ``LocalCommandResult``)
    so an interactive caller can render the menu directly. Callers
    that don't need the structure get the formatted text.
    """
    try:
        from ..skills.loader import get_all_skills
        skills = get_all_skills(project_root=context.cwd or context.workspace_root)
    except Exception:
        skills = []

    items = _build_skills_menu_items(skills)
    text = _format_skills_menu_text(items)

    result = LocalCommandResult(type="text", value=text)
    # Attach structured data for interactive UI consumers. Setattr is
    # used because ``LocalCommandResult`` is a fixed dataclass; this
    # keeps the back-compat text shape while adding optional
    # structured access.
    try:
        setattr(result, "menu_items", items)
    except (AttributeError, TypeError):
        # Frozen dataclass — fall back to text only.
        pass
    return result


# ---------------------------------------------------------------------------
# Phase-9 / WI-9.4 — /hooks slash command
# ---------------------------------------------------------------------------


def hooks_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """Handle ``/hooks`` command — list configured hooks.

    Phase-9 / WI-9.4. Lists hooks from the active session's snapshot,
    grouped by event then sorted by source priority (the chapter's
    "userSettings → policySettings → pluginHook" order). Useful for
    debugging "what's about to fire when I run X."

    Respects the workspace-trust gate: if the workspace isn't trusted,
    only policy-source hooks are listed (matching the executor's
    runtime gate behavior).
    """
    # The snapshot lives on ToolContext.hook_config_manager; the
    # CommandContext doesn't carry that directly. Look it up via the
    # context's tool_context attribute if present, falling back to
    # an "unconfigured" message.
    tool_ctx = getattr(context, "tool_context", None)
    if tool_ctx is None:
        return LocalCommandResult(
            type="text",
            value=(
                "Hook listing unavailable: no ToolContext attached to the "
                "command context. (This is a configuration issue in the "
                "session bootstrap, not a user error.)"
            ),
        )

    manager = getattr(tool_ctx, "hook_config_manager", None)
    snapshot = getattr(manager, "snapshot", None) if manager is not None else None
    if snapshot is None or not snapshot.hooks:
        return LocalCommandResult(
            type="text",
            value=(
                "No hooks configured. Add hooks to ~/.claude/settings.json, "
                "your project's .claude/settings.json, or a plugin's "
                "hooks.json."
            ),
        )

    # Trust gate — same logic as the executor's runtime gate.
    workspace_trusted = getattr(tool_ctx, "workspace_trusted", False)

    lines = ["Configured hooks:"]
    if not workspace_trusted:
        lines.append(
            "  (Workspace untrusted — only policy-source hooks shown; "
            "trust the workspace to see all hooks.)"
        )
    lines.append("")

    for event_name in sorted(snapshot.hooks.keys()):
        event_hooks = snapshot.hooks[event_name]
        if not workspace_trusted:
            event_hooks = [h for h in event_hooks if h.source.is_policy]
        if not event_hooks:
            continue

        # Sort by source priority (lower = higher precedence per the
        # chapter's "Six Hook Sources" table).
        sorted_hooks = sorted(event_hooks, key=lambda h: h.source.priority)

        lines.append(f"  {event_name}:")
        for hook in sorted_hooks:
            descriptor = _describe_hook(hook)
            lines.append(f"    [{hook.source.value}] {descriptor}")
        lines.append("")

    return LocalCommandResult(type="text", value="\n".join(lines).rstrip())


def _describe_hook(hook: Any) -> str:
    """Format a HookConfig as a one-line descriptor for the listing."""
    parts = [f"type={hook.type}"]
    if hook.matcher:
        parts.append(f"matcher={hook.matcher!r}")
    if hook.if_condition:
        parts.append(f"if={hook.if_condition!r}")
    if hook.once:
        parts.append("once")
    if hook.type == "command" and hook.command:
        # Truncate long commands so the listing stays readable.
        cmd = hook.command if len(hook.command) <= 60 else hook.command[:57] + "..."
        parts.append(f"command={cmd!r}")
    elif hook.type == "http" and hook.url:
        parts.append(f"url={hook.url}")
    elif hook.type == "agent" and hook.agent_instructions:
        instr = hook.agent_instructions
        instr = instr if len(instr) <= 50 else instr[:47] + "..."
        parts.append(f"agent_instructions={instr!r}")
    elif hook.type == "prompt" and hook.prompt_text:
        prompt = hook.prompt_text
        prompt = prompt if len(prompt) <= 50 else prompt[:47] + "..."
        parts.append(f"prompt_text={prompt!r}")
    elif hook.type == "callback":
        parts.append("callback=<programmatic>")
    return " ".join(parts)


def exit_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """
    Handle /exit command - exit the application.

    Args:
        args: Command arguments
        context: Command context

    Returns:
        LocalCommandResult
    """
    return LocalCommandResult(
        type="text",
        value="Goodbye!",
    )


def cost_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """
    Handle /cost command - show session cost.

    Args:
        args: Command arguments
        context: Command context

    Returns:
        LocalCommandResult
    """
    tracker = context.cost_tracker
    if tracker is None:
        return LocalCommandResult(
            type="text",
            value="Cost tracking not available.",
        )

    lines = ["Session Cost:", ""]
    lines.append(f"  Total units: {tracker.total_units}")

    if tracker.events:
        lines.append("")
        lines.append("  Recent events:")
        for event in tracker.events[-10:]:
            lines.append(f"    - {event}")

    return LocalCommandResult(
        type="text",
        value="\n".join(lines),
    )


def context_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """
    Handle /context command - show token usage breakdown.

    Args:
        args: Command arguments
        context: Command context

    Returns:
        LocalCommandResult with Markdown table of context usage
    """
    try:
        # Get conversation messages in API format
        conversation_api: list[dict[str, Any]] = []
        if hasattr(context.conversation, "get_messages"):
            conversation_api = context.conversation.get_messages()
        elif hasattr(context.conversation, "messages"):
            # Fall back for simple mock conversations
            for msg in context.conversation.messages:
                role = getattr(msg, 'role', 'unknown')
                content = getattr(msg, 'content', '')
                conversation_api.append({"role": role, "content": content})

        # Get system prompt from config
        system_prompt = context.config.get("system_prompt", "")

        # Get tool schemas from config
        tool_schemas = context.config.get("tool_schemas", [])

        # Get MCP tools info from config
        mcp_tools = context.config.get("mcp_tools", [])

        # Get custom agents info from config
        custom_agents = context.config.get("custom_agents", [])

        # Get CLAUDE.md content
        claude_md_content = ""
        try:
            import asyncio
            from ..context_system.claude_md import get_claude_mds, get_memory_files

            async def _load():
                files = await get_memory_files(cwd=str(context.cwd or context.workspace_root))
                return get_claude_mds(files)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    claude_md_content = pool.submit(asyncio.run, _load()).result(timeout=10)
            else:
                claude_md_content = asyncio.run(_load())
        except Exception:
            pass

        # Get model from config
        model = context.config.get("model", "claude-sonnet-4-6")

        # Get skills info from config
        skills_frontmatter_tokens = context.config.get("skills_tokens", 0)
        skills_count = context.config.get("skills_count", 0)

        # Get API usage from cost tracker
        api_usage = None
        if hasattr(context.cost_tracker, "last_usage"):
            api_usage = context.cost_tracker.last_usage

        # Get auto-compact info from config
        auto_compact_threshold = context.config.get("auto_compact_threshold")
        is_auto_compact_enabled = context.config.get("is_auto_compact_enabled", False)

        data = analyze_context(
            conversation_api_messages=conversation_api,
            model=model,
            system_prompt=system_prompt,
            tool_schemas=tool_schemas,
            claude_md_content=claude_md_content,
            skills_frontmatter_tokens=skills_frontmatter_tokens,
            skills_count=skills_count,
            api_usage=api_usage,
            mcp_tools=mcp_tools,
            custom_agents=custom_agents,
            auto_compact_threshold=auto_compact_threshold,
            is_auto_compact_enabled=is_auto_compact_enabled,
        )

        markdown = format_context_as_markdown(data)
        return LocalCommandResult(type="text", value=markdown)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return LocalCommandResult(type="text", value=f"Context analysis failed: {e}")


async def _compact_async(args: str, context: CommandContext) -> LocalCommandResult:
    """
    Async implementation of compact command.
    """
    if not hasattr(context.conversation, "messages"):
        return LocalCommandResult(
            type="text",
            value="No conversation to compact.",
        )

    messages = context.conversation.messages
    if len(messages) < 2:
        return LocalCommandResult(
            type="text",
            value=f"Nothing to compact: only {len(messages)} messages.",
        )

    # Get provider from config
    provider = context.config.get("provider")
    if provider is None:
        return LocalCommandResult(
            type="text",
            value="Compact requires an LLM provider (not available in this context).",
        )

    model = context.config.get("model", "claude-sonnet-4-6")
    custom_instructions = args.strip() or None

    try:
        # Import here to avoid circular imports
        from ..compact_service.service import compact_conversation

        result = await compact_conversation(
            conversation=context.conversation,
            provider=provider,
            model=model,
            custom_instructions=custom_instructions,
            trigger="manual",
        )
        return LocalCommandResult(
            type="compact",
            value=result.user_display_message or "Conversation compacted.",
            compaction_result=CompactionResult(
                pre_compact_count=result.pre_compact_count,
                post_compact_count=result.post_compact_count,
                tokens_saved=result.tokens_saved,
                trigger=result.trigger,
                summary_preview=result.summary_text[:200] if len(result.summary_text) > 200 else result.summary_text,
            ),
        )
    except ValueError as e:
        return LocalCommandResult(type="text", value=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return LocalCommandResult(
            type="text",
            value=f"Compact failed: {e}",
        )


def compact_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """
    Handle /compact command - compact conversation context.

    Args:
        args: Command arguments
        context: Command context

    Returns:
        LocalCommandResult
    """
    # Run the async version in a new event loop
    try:
        loop = asyncio.get_running_loop()
        # If we're already in an async context, we can't use asyncio.run
        # Fall back to sync path
        return _sync_compact_fallback(context)
    except RuntimeError:
        # No running event loop — safe to use asyncio.run
        try:
            return asyncio.run(_compact_async(args, context))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return _sync_compact_fallback(context)


def _sync_compact_fallback(context: CommandContext) -> LocalCommandResult:
    """Synchronous fallback when async provider is not available."""
    if not hasattr(context.conversation, "messages"):
        return LocalCommandResult(type="text", value="No conversation to compact.")

    messages = context.conversation.messages
    if len(messages) < 2:
        return LocalCommandResult(
            type="text",
            value=f"Nothing to compact: only {len(messages)} messages.",
        )

    # Get messages after last boundary
    try:
        from ..compact_service.messages import (
            create_compact_boundary_message,
            create_compact_summary_message,
            get_messages_after_boundary,
            is_compact_boundary_message,
        )
        from ..token_estimation import count_messages_tokens

        after_boundary = get_messages_after_boundary(messages)
        if len(after_boundary) < 2:
            return LocalCommandResult(
                type="text",
                value=f"Nothing to compact: only {len(after_boundary)} messages after boundary.",
            )

        # Count tokens
        api_messages = context.conversation.get_messages()
        pre_tokens = count_messages_tokens(api_messages)

        # Strip images and microcompact
        stripped = strip_images_from_messages(api_messages)
        compacted, saved = microcompact_messages(stripped)

        # Find boundary position
        boundary_indices = [
            i for i, m in enumerate(messages)
            if is_compact_boundary_message(m)
        ]

        if boundary_indices:
            insert_pos = max(boundary_indices) + 1
        else:
            insert_pos = 0

        # Create simple text summary
        summary_parts = [f"Conversation had {len(after_boundary)} messages ({pre_tokens:,} tokens)."]
        summary_text = "\n".join(summary_parts)

        boundary = create_compact_boundary_message(
            trigger="manual",
            pre_compact_token_count=pre_tokens,
        )
        summary = create_compact_summary_message(summary_text)

        # Rebuild conversation
        if insert_pos == 0:
            context.conversation.messages.clear()
            context.conversation.messages.append(boundary)
            context.conversation.messages.append(summary)
        else:
            context.conversation.messages = list(messages[:insert_pos])
            context.conversation.messages.append(boundary)
            context.conversation.messages.append(summary)

        return LocalCommandResult(
            type="compact",
            value=f"Compacted: removed {len(after_boundary) - 2} messages ({pre_tokens:,} tokens → ~{saved} saved).",
            compaction_result=CompactionResult(
                pre_compact_count=len(messages),
                post_compact_count=len(context.conversation.messages),
                tokens_saved=saved,
                trigger="manual",
                summary_preview=summary_text[:200],
            ),
        )
    except Exception as e:
        # Last resort: just clear old messages
        original_count = len(messages)
        if original_count > 10:
            context.conversation.messages = list(messages[-10:])
            return LocalCommandResult(
                type="compact",
                value=f"Compacted: removed {original_count - 10} messages (fallback mode).",
                compaction_result=CompactionResult(
                    pre_compact_count=original_count,
                    post_compact_count=10,
                    tokens_saved=0,
                    trigger="manual",
                ),
            )
        return LocalCommandResult(
            type="text",
            value="Nothing to compact.",
        )


# Command definitions
HELP_COMMAND = LocalCommand(
    name="help",
    description="Show available commands",
    aliases=["?"],
    argument_hint="[search_query]",
    supports_non_interactive=True,
)

CLEAR_COMMAND = LocalCommand(
    name="clear",
    description="Clear conversation history",
    aliases=["reset", "new"],
    supports_non_interactive=False,
)

EXIT_COMMAND = LocalCommand(
    name="exit",
    description="Exit the application",
    aliases=["quit", "q"],
    supports_non_interactive=True,
)

SKILLS_COMMAND = LocalCommand(
    name="skills",
    description="List available skills",
    argument_hint="",
    supports_non_interactive=True,
)

HOOKS_COMMAND = LocalCommand(
    name="hooks",
    description="List configured hooks (Phase-9 / WI-9.4)",
    argument_hint="",
    supports_non_interactive=True,
)

COST_COMMAND = LocalCommand(
    name="cost",
    description="Show session cost and usage",
    argument_hint="",
    supports_non_interactive=True,
)

CONTEXT_COMMAND = LocalCommand(
    name="context",
    description="Show current workspace context",
    argument_hint="",
    supports_non_interactive=True,
)

COMPACT_COMMAND = LocalCommand(
    name="compact",
    description="Compact conversation to save context space",
    argument_hint="",
    supports_non_interactive=True,
)

INIT_COMMAND = PromptCommand(
    name="init",
    description="Initialize new CLAUDE.md file(s) and optional skills/hooks with codebase documentation",
    markdown_content=NEW_INIT_PROMPT,
    progress_message="analyzing your codebase",
    content_length=0,
    source="builtin",
)


# Synchronous versions for REPL integration
def execute_command_sync(cmd_name: str, args: str, context: CommandContext) -> tuple[bool, str | None, str | None]:
    """
    Execute a command synchronously.

    Returns:
        Tuple of (success: bool, result_text: str | None, error: str | None)
    """
    cmd = None
    for builtin_cmd in get_builtin_commands():
        if builtin_cmd.name.lower() == cmd_name.lower() or cmd_name.lower() in [a.lower() for a in builtin_cmd.aliases]:
            cmd = builtin_cmd
            break

    if cmd is None:
        return False, None, f"Unknown command: {cmd_name}"

    try:
        # This is a synchronous wrapper - we directly call the underlying function
        # instead of going through the async call() method
        if cmd is HELP_COMMAND:
            result = help_command_call(args, context)
        elif cmd is CLEAR_COMMAND:
            result = clear_command_call(args, context)
        elif cmd is EXIT_COMMAND:
            result = exit_command_call(args, context)
        elif cmd is SKILLS_COMMAND:
            result = skills_command_call(args, context)
        elif cmd is HOOKS_COMMAND:
            result = hooks_command_call(args, context)
        elif cmd is COST_COMMAND:
            result = cost_command_call(args, context)
        elif cmd is CONTEXT_COMMAND:
            result = context_command_call(args, context)
        elif cmd is COMPACT_COMMAND:
            result = compact_command_call(args, context)
        else:
            return False, None, f"Command not implemented for sync execution: {cmd_name}"

        return True, result.value, None
    except Exception as e:
        return False, None, str(e)


# Set the call implementations
HELP_COMMAND.set_call(help_command_call)
CLEAR_COMMAND.set_call(clear_command_call)
EXIT_COMMAND.set_call(exit_command_call)
SKILLS_COMMAND.set_call(skills_command_call)
HOOKS_COMMAND.set_call(hooks_command_call)
COST_COMMAND.set_call(cost_command_call)
CONTEXT_COMMAND.set_call(context_command_call)
COMPACT_COMMAND.set_call(compact_command_call)


def get_builtin_commands() -> list[Command]:
    """Get all built-in commands."""
    return [
        HELP_COMMAND,
        CLEAR_COMMAND,
        EXIT_COMMAND,
        SKILLS_COMMAND,
        HOOKS_COMMAND,
        COST_COMMAND,
        CONTEXT_COMMAND,
        COMPACT_COMMAND,
        INIT_COMMAND,
    ]


def register_builtin_commands(registry: CommandRegistry | None = None) -> None:
    """
    Register all built-in commands.

    Args:
        registry: Optional registry to use (uses global if None)
    """
    reg = registry or get_command_registry()
    for cmd in get_builtin_commands():
        reg.register(cmd)


async def execute_command_async(
    cmd_name: str,
    args: str,
    context: CommandContext,
) -> CommandResult:
    """
    Execute a command asynchronously.

    This function handles both LocalCommand and PromptCommand types.
    For PromptCommand, it returns the prompt content that should be sent to the LLM.

    Args:
        cmd_name: Name of the command to execute
        args: Arguments for the command
        context: Command context

    Returns:
        CommandResult with the execution result
    """
    from .engine import CommandEngine

    registry = get_command_registry()
    cmd = registry.get(cmd_name)

    if cmd is None:
        return CommandResult.error(cmd_name, f"Unknown command: {cmd_name}")

    if not cmd.is_enabled():
        return CommandResult.error(cmd_name, f"Command {cmd_name} is disabled")

    engine = CommandEngine(
        registry=registry,
        workspace_root=context.workspace_root,
        context=context,
    )

    # Create a fake command input string for the engine
    command_input = f"/{cmd_name}"
    if args:
        command_input += f" {args}"

    return await engine.execute(command_input)
