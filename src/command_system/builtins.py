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
from .engine import CommandContext, CommandResult, LocalCommandResult
from .registry import CommandRegistry, get_command_registry, list_commands
from .security_review import SECURITY_REVIEW_COMMAND
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


def skills_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """
    Handle /skills command - list available skills.

    Args:
        args: Command arguments
        context: Command context

    Returns:
        LocalCommandResult
    """
    try:
        from ..skills.loader import get_all_skills
        # Pass project_root to find skills in project directories
        skills = get_all_skills(project_root=context.cwd or context.workspace_root)
    except Exception:
        skills = []

    if not skills:
        return LocalCommandResult(
            type="text",
            value="No skills available. Add skills to ~/.clawcodex/skills/ or ./.clawcodex/skills/.",
        )

    lines = ["Available skills:", ""]
    for skill in skills:
        lines.append(f"  {skill.name}")
        lines.append(f"      {skill.description}")
        if skill.when_to_use:
            lines.append(f"      When to use: {skill.when_to_use}")
        lines.append("")

    return LocalCommandResult(
        type="text",
        value="\n".join(lines),
    )


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


def _read_current_advisor_model(context: CommandContext) -> str | None:
    """Resolve the user's currently configured advisor model.

    Prefers the reactive AppState store if the caller wired one (so a
    just-issued ``/advisor`` from this session reads its own write
    without a settings-cache roundtrip), and falls back to the
    persisted settings — the source of truth on every restart.
    """
    store = getattr(context, "app_state_store", None)
    if store is not None:
        try:
            state = store.get_state()
            value = getattr(state, "advisor_model", None)
            if value:
                return value
        except Exception:
            pass
    try:
        from ..settings.settings import get_settings
        configured = (get_settings().advisor_model or "").strip()
        return configured or None
    except Exception:
        return None


def _write_advisor_model(context: CommandContext, value: str | None) -> None:
    """Persist a new advisor_model and update the reactive store if present.

    Both writes are idempotent. When an AppState store is wired (e.g.
    tests, future TUI wiring), ``replace_state`` fires
    ``_on_advisor_model_change`` which itself writes to settings — so
    we skip the direct settings write to avoid double-saving. When the
    store is absent (the current TUI configuration), we update settings
    directly via the same chokepoint the handler uses.
    """
    store = getattr(context, "app_state_store", None)
    if store is not None:
        from ..state.app_state import replace_state
        store.set_state(lambda s: replace_state(s, advisor_model=value or None))
        return
    # No reactive store — write straight to settings + invalidate cache
    # so the next API call picks up the change. Use the shared default
    # ConfigManager (instead of a fresh one) so the in-process
    # ``_global_cache`` field stays consistent for callers that read
    # via ``load_config()`` / ``_get_default_manager().get_merged()``.
    from .. import config as cfg_mod
    from ..settings.settings import invalidate_settings_cache
    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    settings_section = cfg.get("settings")
    if not isinstance(settings_section, dict):
        settings_section = {}
    settings_section["advisor_model"] = value or ""
    cfg["settings"] = settings_section
    mgr.save_global(cfg)
    invalidate_settings_cache()


def _read_current_advisor_provider(context: CommandContext) -> str:
    """Resolve the current advisor_provider (store preferred, settings
    fallback). Empty string = unset.
    Mirrors ``_read_current_advisor_model``."""
    store = getattr(context, "app_state_store", None)
    if store is not None:
        try:
            v = getattr(store.get_state(), "advisor_provider", None)
            return (v or "").strip()
        except Exception:
            pass
    try:
        from ..settings.settings import get_settings
        return (getattr(get_settings(), "advisor_provider", "") or "").strip()
    except Exception:
        return ""


def _write_advisor_provider(context: CommandContext, value: str | None) -> None:
    """Persist advisor_provider (store preferred, settings fallback).
    Mirrors ``_write_advisor_model`` — same dual-path persistence.
    Empty / None clears the field."""
    normalized = (value or "").strip()
    store = getattr(context, "app_state_store", None)
    if store is not None:
        from ..state.app_state import replace_state
        store.set_state(
            lambda s: replace_state(s, advisor_provider=(normalized or None))
        )
        return
    from .. import config as cfg_mod
    from ..settings.settings import invalidate_settings_cache
    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    settings_section = cfg.get("settings")
    if not isinstance(settings_section, dict):
        settings_section = {}
    settings_section["advisor_provider"] = normalized
    cfg["settings"] = settings_section
    mgr.save_global(cfg)
    invalidate_settings_cache()


def _list_configured_providers() -> list[str]:
    """Return the set of provider keys configured in
    ``~/.clawcodex/config.json``. Used by /advisor to validate that the
    user-supplied provider prefix refers to a real entry."""
    try:
        from .. import config as cfg_mod
        mgr = cfg_mod._get_default_manager()
        cfg = mgr.load_global()
        providers = cfg.get("providers")
        if isinstance(providers, dict):
            return sorted(providers.keys())
    except Exception:
        pass
    return []


def _read_current_advisor_client_mode(context: CommandContext) -> bool:
    """Resolve the user's current advisor_client_mode flag (reactive
    store preferred, settings fallback). Mirrors
    ``_read_current_advisor_model``."""
    store = getattr(context, "app_state_store", None)
    if store is not None:
        try:
            return bool(getattr(store.get_state(), "advisor_client_mode", False))
        except Exception:
            pass
    try:
        from ..settings.settings import get_settings
        return bool(getattr(get_settings(), "advisor_client_mode", False))
    except Exception:
        return False


def _write_advisor_client_mode(context: CommandContext, value: bool) -> None:
    """Persist advisor_client_mode (store-preferred, settings fallback).
    Mirrors ``_write_advisor_model`` — same dual-path persistence."""
    store = getattr(context, "app_state_store", None)
    if store is not None:
        from ..state.app_state import replace_state
        store.set_state(lambda s: replace_state(s, advisor_client_mode=bool(value)))
        return
    from .. import config as cfg_mod
    from ..settings.settings import invalidate_settings_cache
    mgr = cfg_mod._get_default_manager()
    cfg = mgr.load_global()
    settings_section = cfg.get("settings")
    if not isinstance(settings_section, dict):
        settings_section = {}
    settings_section["advisor_client_mode"] = bool(value)
    cfg["settings"] = settings_section
    mgr.save_global(cfg)
    invalidate_settings_cache()


def advisor_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """Handle /advisor — configure the reviewer model.

    Required argument shape: ``<provider>:<model>`` — both halves
    explicit, separated by the first colon (so model strings
    containing ``/`` or further ``:`` are preserved verbatim).
    Provider must match a key in ``~/.clawcodex/config.json``'s
    ``providers`` map; clawcodex is multi-provider and the same
    model name (e.g. ``claude-opus-4-7``) can sit behind anthropic,
    openai (litellm), openrouter, bedrock, etc. Name-based inference
    was ambiguous and silently routed to the wrong endpoint.

    Branches (after parsing optional ``--client`` / ``--no-client``):
      * no args, no flags → status report (provider/model + mode).
      * ``unset`` | ``off`` → clear advisor_model, advisor_provider,
        and advisor_client_mode.
      * ``--no-client`` alone → keep model+provider, clear client-mode.
      * ``--client`` alone → keep model+provider, set client-mode.
      * ``<provider>:<model>`` → validate provider exists in config,
        persist both fields together. ``--client`` flag (if present)
        also persists advisor_client_mode.

    Examples:
      * ``/advisor anthropic:claude-opus-4-7``  (direct Anthropic API)
      * ``/advisor openai:claude-opus-4-7``     (litellm/proxy via openai provider)
      * ``/advisor openrouter:anthropic/claude-opus-4.1``
      * ``/advisor gemini:gemini-2.5-pro``
    """
    from ..models.model import canonical_model_name, resolve_model
    from ..models.validation import validate_model_name
    from ..utils.advisor import (
        ADVISOR_MODE_CLIENT_SIDE,
        ADVISOR_MODE_INACTIVE,
        ADVISOR_MODE_SERVER_SIDE,
        can_user_configure_advisor,
        decide_advisor_mode,
    )

    provider = getattr(context, "provider", None)

    # Hard-reject only when env-disabled (the user would silently
    # configure a value that no request can use).
    if not can_user_configure_advisor(provider):
        return LocalCommandResult(
            type="text",
            value=(
                "Advisor is disabled by the CLAUDE_CODE_DISABLE_ADVISOR_TOOL "
                "env var."
            ),
        )

    # Tokenize raw args so flag handling is order-insensitive. A
    # trailing or leading ``--client`` / ``--no-client`` should peel
    # off cleanly without breaking the model identifier.
    raw_tokens = (args or "").strip().split()
    force_client_flag: bool | None = None  # None = no flag passed
    rest_tokens: list[str] = []
    for tok in raw_tokens:
        if tok == "--client":
            force_client_flag = True
        elif tok == "--no-client":
            force_client_flag = False
        else:
            rest_tokens.append(tok)
    arg = " ".join(rest_tokens).strip()
    arg_lower = arg.lower()

    current_advisor = _read_current_advisor_model(context)
    current_provider = _read_current_advisor_provider(context)
    current_client_mode = _read_current_advisor_client_mode(context)

    main_loop_model = ""
    if provider is not None:
        main_loop_model = getattr(provider, "model", "") or ""
    if not main_loop_model:
        store = getattr(context, "app_state_store", None)
        if store is not None:
            try:
                main_loop_model = getattr(store.get_state(), "main_loop_model", "") or ""
            except Exception:
                pass

    def _render_status() -> str:
        """Format the current state for the no-args branch."""
        if not current_advisor or not current_provider:
            # Either field missing → effectively unset. Show what's
            # there (if anything) so users can fix a partial config.
            partial = ""
            if current_advisor and not current_provider:
                partial = (
                    f"\n(Found advisor_model={current_advisor!r} but no "
                    "advisor_provider — clear with /advisor unset then "
                    "re-run with the explicit syntax.)"
                )
            elif current_provider and not current_advisor:
                partial = (
                    f"\n(Found advisor_provider={current_provider!r} but "
                    "no advisor_model — clear with /advisor unset.)"
                )
            # Critic C1: surface advisor_client_mode even on partial
            # configs so the user sees stored state that would silently
            # activate as soon as both fields land.
            if current_client_mode:
                partial += (
                    "\n(advisor_client_mode is ON but won't engage "
                    "until both advisor_model and advisor_provider "
                    "are set.)"
                )
            providers = _list_configured_providers()
            providers_hint = (
                f"Configured providers: {', '.join(providers)}.\n"
                if providers else ""
            )
            return (
                "Advisor: not set\n"
                f"{providers_hint}"
                "Use \"/advisor <provider>:<model>\" to enable, e.g.:\n"
                '  /advisor anthropic:claude-opus-4-7   (direct Anthropic)\n'
                '  /advisor openai:claude-opus-4-7      (via openai-compat, '
                'e.g. litellm)\n'
                '  /advisor openrouter:anthropic/claude-opus-4.1'
                f"{partial}"
            )
        mode = decide_advisor_mode(
            provider,
            main_loop_model,
            current_advisor,
            force_client_mode=current_client_mode,
            advisor_provider=current_provider,
        )
        mode_label = {
            ADVISOR_MODE_SERVER_SIDE: "active (server-side)",
            ADVISOR_MODE_CLIENT_SIDE: "active (client-side)",
            ADVISOR_MODE_INACTIVE: "inactive",
        }.get(mode, "inactive")
        suffix = ""
        if current_client_mode:
            suffix = " [--client forced]"
        return (
            f"Advisor: {current_provider}:{current_advisor} — {mode_label}{suffix}\n"
            'Use "/advisor unset" to disable or '
            '"/advisor <provider>:<model>" to change.'
        )

    # No model arg, no flags → status only.
    if not arg and force_client_flag is None:
        return LocalCommandResult(type="text", value=_render_status())

    # --no-client alone (no model) → just clear the forced-client flag.
    if not arg and force_client_flag is False:
        if not current_client_mode:
            return LocalCommandResult(
                type="text", value="Advisor client mode already off.",
            )
        _write_advisor_client_mode(context, False)
        return LocalCommandResult(
            type="text",
            value=(
                "Advisor client mode disabled. "
                "Server-side will be used when applicable."
            ),
        )

    # --client alone (no model) → just turn on the forced-client flag.
    # Critic C2: both fields are required before the flag matters; a
    # partial config + --client would silently fail at request time.
    if not arg and force_client_flag is True:
        if not current_advisor or not current_provider:
            return LocalCommandResult(
                type="text",
                value=(
                    "Cannot force client mode: advisor is not fully "
                    "configured. Use \"/advisor <provider>:<model> "
                    "--client\" together."
                ),
            )
        if current_client_mode:
            return LocalCommandResult(
                type="text", value="Advisor client mode already on.",
            )
        _write_advisor_client_mode(context, True)
        return LocalCommandResult(
            type="text",
            value=(
                "Advisor client mode enabled. The advisor will run via "
                "client-side dispatch on every request."
            ),
        )

    if arg_lower in ("unset", "off"):
        previous_model = current_advisor
        previous_provider = current_provider
        if previous_model:
            _write_advisor_model(context, None)
        if previous_provider:
            _write_advisor_provider(context, None)
        if current_client_mode:
            _write_advisor_client_mode(context, False)
        if previous_model or previous_provider:
            prior = (
                f"{previous_provider}:{previous_model}"
                if previous_provider and previous_model
                else (previous_model or previous_provider or "?")
            )
            return LocalCommandResult(
                type="text", value=f"Advisor disabled (was {prior}).",
            )
        return LocalCommandResult(
            type="text", value="Advisor already unset.",
        )

    # Parse <provider>:<model> — provider must be a known config key.
    raw = arg
    if ":" not in raw:
        providers = _list_configured_providers()
        providers_hint = (
            f" Configured providers: {', '.join(providers)}."
            if providers else ""
        )
        return LocalCommandResult(
            type="text",
            value=(
                "Advisor requires explicit <provider>:<model> syntax.\n"
                f"Got: {raw!r}.{providers_hint}\n"
                'Example: /advisor anthropic:claude-opus-4-7'
            ),
        )
    provider_part, model_part = raw.split(":", 1)
    provider_part = provider_part.strip()
    model_part = model_part.strip()
    if not provider_part or not model_part:
        return LocalCommandResult(
            type="text",
            value=(
                "Invalid syntax. Expected <provider>:<model> with both "
                f"halves non-empty. Got: {raw!r}."
            ),
        )
    # Critic S3: validate against the configured providers list
    # unconditionally. ``_list_configured_providers`` is empty only
    # when ``load_global`` crashes (its try/except swallows then
    # returns []) — in that pathological case a clearer signal is
    # better than a friendly silent-skip that lets bad input through.
    configured = _list_configured_providers()
    if provider_part not in configured:
        return LocalCommandResult(
            type="text",
            value=(
                f"Unknown provider {provider_part!r}. Configured: "
                f"{', '.join(configured) or '(none — check ~/.clawcodex/config.json)'}. "
                "Configure new providers in ~/.clawcodex/config.json."
            ),
        )
    try:
        resolved = resolve_model(model_part)
    except Exception as e:
        return LocalCommandResult(
            type="text", value=f"Invalid advisor model: {e}",
        )
    if not validate_model_name(resolved):
        return LocalCommandResult(
            type="text",
            value=f"Unknown model: {model_part} ({resolved})",
        )

    normalized = canonical_model_name(resolved)
    _write_advisor_model(context, normalized)
    _write_advisor_provider(context, provider_part)
    if force_client_flag is True:
        _write_advisor_client_mode(context, True)
    elif force_client_flag is False:
        _write_advisor_client_mode(context, False)

    # Report what mode the chosen pair lands in, so the user can spot
    # mismatches immediately (e.g., they expected server-side but the
    # main model doesn't qualify).
    effective_client_mode = (
        force_client_flag
        if force_client_flag is not None
        else current_client_mode
    )
    chosen_mode = decide_advisor_mode(
        provider,
        main_loop_model,
        normalized,
        force_client_mode=effective_client_mode,
        advisor_provider=provider_part,
    )
    if chosen_mode == ADVISOR_MODE_SERVER_SIDE:
        mode_msg = "Will run server-side (Anthropic beta path)."
    elif chosen_mode == ADVISOR_MODE_CLIENT_SIDE:
        mode_msg = "Will run client-side (separate API call)."
    else:
        mode_msg = (
            "Note: advisor is currently inactive (no path applies for "
            f"main loop {main_loop_model!r} + advisor {normalized!r})."
        )
    return LocalCommandResult(
        type="text",
        value=f"Advisor set to {provider_part}:{normalized}. {mode_msg}",
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

# /advisor — server-side reviewer tool. Python port of
# typescript/src/commands/advisor.ts. The `is_enabled` callable is read by
# the help-listing and command-availability checks. We pass ``provider=None``
# at command-list time because the registry doesn't know what provider is
# active; the env-disable check still applies. Per-request provider gating
# is enforced inside ``_call_model_sync`` so the user can't accidentally
# silence the API by toggling /advisor under a non-first-party provider.
from ..utils.advisor import can_user_configure_advisor as _can_user_configure_advisor

ADVISOR_COMMAND = LocalCommand(
    name="advisor",
    description="Configure the advisor model (server-side on 1P Anthropic, client-side on any provider)",
    argument_hint="[<model> [--client] | --no-client | off]",
    supports_non_interactive=True,
    is_enabled=lambda: _can_user_configure_advisor(None),
)

INIT_COMMAND = PromptCommand(
    name="init",
    description="Initialize new CLAUDE.md file(s) and optional skills/hooks with codebase documentation",
    markdown_content=NEW_INIT_PROMPT,
    progress_message="analyzing your codebase",
    content_length=0,
    source="builtin",
)

# Port of typescript/src/commands/auto-fix.ts (type: 'prompt'). Text is verbatim.
AUTO_FIX_PROMPT = (
    "The user wants to configure auto-fix settings. Auto-fix automatically runs lint "
    "and test commands after AI file edits, feeding errors back for self-repair.\n\n"
    "Current settings location: `.claude/settings.json` or "
    "`.claude/settings.local.json`\n\n"
    "Example configuration:\n```json\n{\n  \"autoFix\": {\n    \"enabled\": true,\n"
    "    \"lint\": \"eslint . --fix\",\n    \"test\": \"bun test\",\n"
    "    \"maxRetries\": 3,\n    \"timeout\": 30000\n  }\n}\n```\n\n"
    "Ask the user what lint and test commands they use, then help them set up the "
    "configuration."
)

# Port of the /review half of typescript/src/commands/review.ts (LOCAL_REVIEW_PROMPT).
# TS uses an indented template literal; this is intentionally dedented (the model is
# indentation-insensitive). `${args}` becomes `$ARGUMENTS` for substitute_arguments.
REVIEW_PROMPT = """
You are an expert code reviewer. Follow these steps:

1. If no PR number is provided in the args, run `gh pr list` to show open PRs
2. If a PR number is provided, run `gh pr view <number>` to get PR details
3. Run `gh pr diff <number>` to get the diff
4. Analyze the changes and provide a thorough code review that includes:
   - Overview of what the PR does
   - Analysis of code quality and style
   - Specific suggestions for improvements
   - Any potential issues or risks

Keep your review concise but thorough. Focus on:
- Code correctness
- Following project conventions
- Performance implications
- Test coverage
- Security considerations

Format your review with clear sections and bullet points.

PR number: $ARGUMENTS
"""

AUTO_FIX_COMMAND = PromptCommand(
    name="auto-fix",
    description="Configure auto-fix: run lint/test after AI edits",
    markdown_content=AUTO_FIX_PROMPT,
    progress_message="Configuring auto-fix...",
    content_length=0,
    source="builtin",
)

REVIEW_COMMAND = PromptCommand(
    name="review",
    description="Review a pull request",
    markdown_content=REVIEW_PROMPT,
    progress_message="reviewing pull request",
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
COST_COMMAND.set_call(cost_command_call)
CONTEXT_COMMAND.set_call(context_command_call)
COMPACT_COMMAND.set_call(compact_command_call)
ADVISOR_COMMAND.set_call(advisor_command_call)


def get_builtin_commands() -> list[Command]:
    """Get all built-in commands."""
    # Local import keeps the gate readable at the call site and avoids
    # pulling buddy module dependencies into command_system on bare
    # `import` of this module — matches pattern used elsewhere.
    from .buddy_command import BUDDY_COMMAND, is_buddy_command_enabled

    cmds: list[Command] = [
        HELP_COMMAND,
        CLEAR_COMMAND,
        EXIT_COMMAND,
        SKILLS_COMMAND,
        COST_COMMAND,
        CONTEXT_COMMAND,
        COMPACT_COMMAND,
        ADVISOR_COMMAND,
        INIT_COMMAND,
        AUTO_FIX_COMMAND,
        REVIEW_COMMAND,
        SECURITY_REVIEW_COMMAND,
    ]
    if is_buddy_command_enabled():
        cmds.append(BUDDY_COMMAND)
    return cmds


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
