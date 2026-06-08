"""
Command type system for Claw Codex.

Implements the core command types inspired by Claude Code's command system:
- PromptCommand: Expands to text/prompt content sent to the model
- LocalCommand: Executes local code without rendering UI
- InteractiveCommand: Drives a surface-agnostic UIHost and returns an
  InteractiveOutcome (port of TS ``local-jsx``)
"""

from __future__ import annotations

import asyncio

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence


class CommandType(Enum):
    """Types of commands."""
    PROMPT = "prompt"
    LOCAL = "local"
    # Ports TS ``type: 'local-jsx'`` — a command that drives an interactive
    # UI (via the surface-agnostic ``UIHost`` port) and returns an
    # ``InteractiveOutcome``. A *distinct* type (not a LocalCommand subtype)
    # so the engine routes it to ``_execute_interactive`` and the
    # remote-safety gate blocks it by type (see ``safe_commands``).
    INTERACTIVE = "interactive"


class CommandAvailability(Enum):
    """Availability environments where a command is available."""
    CLAUDE_AI = "claude-ai"
    CONSOLE = "console"


@dataclass(frozen=True)
class CompactionResult:
    """Result data from a compaction operation."""
    pre_compact_count: int = 0
    post_compact_count: int = 0
    tokens_saved: int = 0
    trigger: str = "manual"
    summary_preview: Optional[str] = None


@dataclass(frozen=True)
class LocalCommandResult:
    """Result of a local command execution."""
    type: str = "text"  # "text" | "compact" | "skip"
    value: str = ""
    compaction_result: Optional[CompactionResult] = None
    display_text: Optional[str] = None


@dataclass
class CommandContext:
    """Context passed to command execution."""
    workspace_root: Path
    cwd: Path
    conversation: Any  # Will be type hinted properly later
    cost_tracker: Any
    history: Any
    config: dict[str, Any] = field(default_factory=dict)
    # Optional handles for commands that need to read or mutate
    # cross-session state. Default to None so existing call sites that
    # build CommandContext positionally / partially still work.
    #
    # ``app_state_store`` is the reactive AppState store created at TUI /
    # REPL startup. /advisor uses it to read the current advisor model
    # and to flip it via ``set_state`` (which fires the persistence
    # handler in ``src/state/app_state.py``).
    #
    # ``provider`` is the currently-active LLM provider; /advisor checks
    # it to decide whether the user can configure the advisor at all
    # (only first-party Anthropic supports it).
    app_state_store: Any = None
    provider: Any = None
    # ``ui`` is the surface-agnostic interaction port (a ``UIHost``) that
    # interactive commands drive. None on surfaces that didn't wire one;
    # the engine substitutes a ``NullUIHost`` (which raises for mutating
    # prompts) so a command body can always assume ``ctx.ui`` is present.
    ui: Any = None  # UIHost | None
    # ``tool_context`` is the surface's ToolContext (REPL/TUI), threaded
    # through so a SkillPromptCommand can render with the *same* session id +
    # shell executor the model's Skill tool uses (P0-6 Option B / Phase 3.5).
    # None on listing/aggregation paths, which never call
    # get_prompt_for_command, so the default is correct there.
    tool_context: Any = None  # ToolContext | None


# ---------------------------------------------------------------------------
# Downstream context extension — attach extra handles without modifying
# CommandContext's upstream signature.  Extensions call
# ``attach_downstream_context(ctx, ...)`` after creating a CommandContext
# to inject tool_registry / tool_context / runtime_context.
# ---------------------------------------------------------------------------

def attach_downstream_context(
    context: CommandContext,
    *,
    tool_registry: Any = None,
    tool_context: Any = None,
    runtime_context: Any = None,
) -> None:
    """Post-init injection of downstream-only fields onto *context*.

    This keeps CommandContext's dataclass signature identical to upstream
    while still allowing ext commands (e.g. /provider, /model) to read
    tool and runtime state via ``getattr(context, 'tool_registry', None)``.
    """
    if tool_registry is not None:
        context.tool_registry = tool_registry  # type: ignore[attr-defined]
    if tool_context is not None:
        context.tool_context = tool_context  # type: ignore[attr-defined]
    if runtime_context is not None:
        context.runtime_context = runtime_context  # type: ignore[attr-defined]


# Protocol for local command callables
LocalCommandCall = Callable[[str, CommandContext], LocalCommandResult]


@dataclass(frozen=True)
class CommandBase:
    """Base class for all commands."""
    name: str
    description: str
    aliases: list[str] = field(default_factory=list)
    availability: list[CommandAvailability] = field(default_factory=list)
    is_enabled: Callable[[], bool] = field(default=lambda: True)
    is_hidden: bool = False
    has_user_specified_description: bool = False
    argument_hint: Optional[str] = None
    when_to_use: Optional[str] = None
    version: Optional[str] = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    loaded_from: str = "builtin"  # "builtin" | "skills" | "plugin" | "bundled" | "mcp"
    kind: Optional[str] = None  # "workflow" or None
    immediate: bool = False
    is_sensitive: bool = False

    @property
    def command_type(self) -> CommandType:
        """Get the command type. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement command_type property")

    def user_facing_name(self) -> str:
        """Get the user-facing name of the command."""
        return self.name


@dataclass(frozen=True)
class PromptCommand(CommandBase):
    """A command that expands to prompt content."""
    progress_message: str = ""
    content_length: int = 0
    arg_names: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    model: Optional[str] = None
    source: str = "builtin"
    plugin_info: Optional[dict[str, Any]] = None
    disable_non_interactive: bool = False
    hooks: dict[str, Any] = field(default_factory=dict)
    skill_root: Optional[str] = None
    context: str = "inline"  # "inline" | "fork"
    agent: Optional[str] = None
    effort: Optional[str] = None
    paths: list[str] = field(default_factory=list)
    markdown_content: str = ""

    @property
    def command_type(self) -> CommandType:
        return CommandType.PROMPT

    async def get_prompt_for_command(
        self,
        args: str,
        context: CommandContext,
    ) -> list[dict[str, Any]]:
        """Get the prompt content for this command."""
        # Default implementation - will be overridden
        from .argument_substitution import substitute_arguments
        content = substitute_arguments(self.markdown_content, args, self.arg_names)
        return [{"type": "text", "text": content}]


@dataclass(frozen=True)
class SkillPromptCommand(PromptCommand):
    """A PromptCommand backed by a markdown skill (P0-6 Option B / Phase 3.5).

    The base ``PromptCommand.get_prompt_for_command`` does only bare argument
    substitution, which is lossy for skills (it drops the base-dir header,
    ``${CLAUDE_SKILL_DIR}`` / ``${CLAUDE_SESSION_ID}`` substitution, and gated
    shell-exec). This subclass overrides it with a render that is identical *by
    construction* to the model's Skill-tool path: when the surface threads its
    ``ToolContext`` onto the ``CommandContext`` (REPL/TUI execution), it
    delegates to ``_run_markdown_skill`` — the same function the Skill tool runs
    — re-resolving the skill by ``self.name`` so session id and shell-exec are
    byte-for-byte the same. Off that path (no ToolContext: SDK / listing
    callers) it degrades to a headless render with no shell executor.
    """

    async def get_prompt_for_command(
        self,
        args: str,
        context: CommandContext,
    ) -> list[dict[str, Any]]:
        tc = getattr(context, "tool_context", None)
        if tc is not None:
            # Function-scope import: command_system must not import tool_system
            # at module load (would cycle). The edge is one private helper, and
            # command_system already imports ..skills.
            from clawcodex_ext.tool_system.tools.skill import _run_markdown_skill

            # ``_run_markdown_skill`` is sync and may block (disk I/O + a gated
            # BashTool subprocess for embedded shell blocks). Run it off the
            # event loop so neither the REPL thread-pool loop nor the Textual
            # loop stalls. Note: it mutates a process-global skill registry
            # (clear + re-resolve), which is unsynchronized — safe only because
            # both surfaces serialize command dispatch, so no two renders run
            # concurrently. A future concurrent-dispatch change would need a lock.
            res = await asyncio.to_thread(
                _run_markdown_skill, self.name, args or "", tc
            )
            payload = res.output if isinstance(res.output, dict) else {}
            prompt = payload.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                return [{"type": "text", "text": prompt}]
            # error / empty (e.g. the skill was removed since registration) →
            # fall through to a headless render from the cached fields.
        return self._render_headless(args)

    def _render_headless(self, args: str) -> list[dict[str, Any]]:
        """Best-effort render with no ToolContext: base-dir header + argument /
        ``${CLAUDE_SKILL_DIR}`` / ``${CLAUDE_SESSION_ID}`` substitution, with
        embedded shell blocks left verbatim (no executor). Only reachable off
        the REPL/TUI execution paths, which always thread a ToolContext."""
        from clawcodex_ext.bootstrap.state import get_session_id
        from clawcodex_ext.skills.runtime_substitution import render_skill_prompt

        text = render_skill_prompt(
            body=self.markdown_content,
            args=args or "",
            base_dir=self.skill_root,
            argument_names=self.arg_names,
            session_id=get_session_id(),
            loaded_from=self.loaded_from,
            slash_command_name=f"/{self.name}",
            shell_executor=None,
        )
        return [{"type": "text", "text": text}]


@dataclass(frozen=True)
class LocalCommand(CommandBase):
    """A command that executes local code."""
    supports_non_interactive: bool = False
    _call_impl: Optional[LocalCommandCall] = field(default=None, repr=False, compare=False)

    @property
    def command_type(self) -> CommandType:
        return CommandType.LOCAL

    def set_call(self, call: LocalCommandCall) -> None:
        """Set the call implementation."""
        object.__setattr__(self, '_call_impl', call)

    async def call(self, args: str, context: CommandContext) -> LocalCommandResult:
        """Execute the local command."""
        if self._call_impl is not None:
            return self._call_impl(args, context)
        return LocalCommandResult(type="text", value=f"Command {self.name} not implemented")


# ---------------------------------------------------------------------------
# Interactive command bridge (port of TS ``local-jsx`` commands)
# ---------------------------------------------------------------------------
#
# TS interactive commands (``type: 'local-jsx'``) render an Ink element and
# resolve via an ``onDone`` callback. The Python port replaces "render an
# element" with "drive a surface-agnostic ``UIHost`` port", so one command
# body works headless (REPL), in the Textual TUI, and (raising) under the
# SDK / non-interactive null surface.


@dataclass(frozen=True)
class UIOption:
    """One selectable row passed to :meth:`UIHost.select`.

    ``value`` is what the host returns on selection; ``label`` is shown to
    the user; ``description`` is optional secondary text (rendered dim in
    the TUI, parenthesized in the REPL menu).
    """

    value: str
    label: str
    description: Optional[str] = None


class InteractiveUnavailableError(RuntimeError):
    """Raised by :class:`NullUIHost` when an interactive command tries to
    prompt on a surface with no UI (SDK / non-interactive). The engine turns
    this into a clean error ``CommandResult`` rather than a crash.
    """


class UIHost(Protocol):
    """Surface-agnostic interaction port injected as ``CommandContext.ui``.

    Adapters: ``ReplUIHost`` (numbered menu), ``TextualUIHost``
    (``push_screen_wait`` modal), ``NullUIHost`` (raises for the mutating
    ``select``; ``display`` no-ops). Mirrors the TS pattern of injecting host
    callbacks into the command context.

    The slice ships the primitives in-scope Class-B commands need:
    ``select`` (single choice), ``prompt_text`` (free-text line), plus
    read-only ``display``. ``prompt_text`` lands with its first consumer
    ``/export``. ``confirm`` stays deferred — TS expresses it as a 2-option
    ``select`` over Yes/No, so it needs no new method. The port grows by
    adding a method here and one line per adapter.
    """

    async def select(
        self,
        title: str,
        options: Sequence[UIOption],
        *,
        current: Optional[str] = None,
    ) -> Optional[str]:
        """Prompt the user to pick one option. Returns the chosen
        ``UIOption.value``, or ``None`` if cancelled."""
        ...

    async def prompt_text(
        self,
        title: str,
        *,
        default: str = "",
        placeholder: Optional[str] = None,
    ) -> Optional[str]:
        """Prompt for a single free-text line. Returns the submitted string,
        which MAY be ``''`` — an empty submit is valid input, not a cancel
        (mirrors TS ``TextInput.onSubmit('')``). Returns ``None`` *only* when
        cancelled (Esc / EOF / Ctrl-C).

        ``default`` pre-fills the editable value; ``placeholder`` is a hint
        shown while the field is empty and is never submitted."""
        ...

    async def display(self, title: str, body: str) -> None:
        """Show read-only information. No return value."""
        ...


class NullUIHost:
    """UIHost for surfaces without a UI (SDK / non-interactive).

    The *mutating* primitives :meth:`select` and :meth:`prompt_text` raise
    :class:`InteractiveUnavailableError` — deliberately NOT returning a
    default/``current`` value, which would read as a false success. Only the
    read-only :meth:`display` no-ops. (Resolved contract — see plan §4/§7.)
    """

    _MSG = "This command needs an interactive surface (TUI or REPL)."

    async def select(
        self,
        title: str,
        options: Sequence[UIOption],
        *,
        current: Optional[str] = None,
    ) -> Optional[str]:
        raise InteractiveUnavailableError(self._MSG)

    async def prompt_text(
        self,
        title: str,
        *,
        default: str = "",
        placeholder: Optional[str] = None,
    ) -> Optional[str]:
        raise InteractiveUnavailableError(self._MSG)

    async def display(self, title: str, body: str) -> None:
        return None


@dataclass(frozen=True)
class InteractiveOutcome:
    """What an interactive command returns to the engine — the Python
    analogue of the TS ``onDone`` payload (``command.ts:117-126``).

    The engine maps this onto a :class:`CommandResult`, propagating
    ``display`` / ``should_query`` / ``meta_messages`` (which the LOCAL arm
    hardcodes away). ``display == "skip"`` signals "produce no output" (TS
    ``display: 'skip'``); use :meth:`skip` for the cancelled path.
    """

    message: Optional[str] = None          # TS onDone.result
    display: str = "system"                # "skip" | "system" | "user"
    should_query: bool = False             # TS onDone.shouldQuery
    meta_messages: list[str] = field(default_factory=list)  # TS onDone.metaMessages

    @classmethod
    def skip(cls) -> "InteractiveOutcome":
        """Cancelled / no-op outcome — the engine returns
        ``CommandResult.skip`` for it."""
        return cls(display="skip")


@dataclass(frozen=True)
class InteractiveCommand(CommandBase):
    """Base for commands that drive ``ctx.ui`` and return an
    :class:`InteractiveOutcome`.

    Reports ``CommandType.INTERACTIVE`` so the engine routes it to
    ``_execute_interactive`` and the remote-safety gate blocks it *by type*
    (``safe_commands.is_bridge_safe_command``). Concrete commands subclass
    this and override :meth:`run` (the ``StatuslineCommand`` pattern — no new
    dataclass fields required).
    """

    @property
    def command_type(self) -> CommandType:
        return CommandType.INTERACTIVE

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        """Drive ``context.ui`` and return the outcome. Override in
        subclasses."""
        raise NotImplementedError(
            "InteractiveCommand subclasses must implement run()"
        )


# Type alias for any command
Command = PromptCommand | LocalCommand | InteractiveCommand


def get_command_name(cmd: CommandBase) -> str:
    """Get the user-facing name of a command."""
    return cmd.user_facing_name()


def is_command_enabled(cmd: CommandBase) -> bool:
    """Check if a command is enabled."""
    return cmd.is_enabled()


def meets_availability_requirement(
    cmd: CommandBase,
    is_claude_ai_subscriber: bool = False,
    is_console_user: bool = False,
) -> bool:
    """Check if a command meets the availability requirement."""
    if not cmd.availability:
        return True

    for availability in cmd.availability:
        if availability == CommandAvailability.CLAUDE_AI and is_claude_ai_subscriber:
            return True
        if availability == CommandAvailability.CONSOLE and is_console_user:
            return True
    return False


__all__ = [
    "Command",
    "CommandAvailability",
    "CommandBase",
    "CommandContext",
    "CommandType",
    "CompactionResult",
    "InteractiveCommand",
    "InteractiveOutcome",
    "InteractiveUnavailableError",
    "LocalCommand",
    "LocalCommandCall",
    "LocalCommandResult",
    "NullUIHost",
    "PromptCommand",
    "SkillPromptCommand",
    "UIHost",
    "UIOption",
    "attach_downstream_context",
    "get_command_name",
    "is_command_enabled",
    "meets_availability_requirement",
]
