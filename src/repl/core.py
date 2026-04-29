"""Interactive REPL for Claw Codex."""

from __future__ import annotations

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.styles import Style
    from prompt_toolkit.completion import Completer, Completion, WordCompleter
    from prompt_toolkit.input import ansi_escape_sequences as _pt_ansi_seq
    from prompt_toolkit.keys import Keys as _PTKeys

    # Teach prompt_toolkit to distinguish Shift+Enter from plain Enter.
    #
    # Two distinct Shift+Enter sequences are in the wild; we route both to
    # the same two-key tuple Meta+Enter uses (Escape + ControlM), so a
    # single ``escape, c-m`` binding covers them all:
    #
    # 1. ``\x1b[13;2u`` — Kitty keyboard protocol (Kitty, WezTerm, Ghostty,
    #    iTerm2 with CSI u mode). Not known to prompt_toolkit at all.
    # 2. ``\x1b[27;2;13~`` — xterm ``modifyOtherKeys`` level 2 (xterm with
    #    modifyOtherKeys on, some VSCode configurations). prompt_toolkit
    #    maps this to plain ``ControlM``, so by default it's
    #    indistinguishable from Enter — we override it.
    #
    # This matches the TypeScript reference's behavior in ``useTextInput.ts``
    # which explicitly treats both CSI 13;2u and CSI 27;2;13~ as "insert
    # newline" on Shift+Enter.
    if not hasattr(_pt_ansi_seq, "_clawcodex_shift_enter_registered"):
        _pt_ansi_seq.ANSI_SEQUENCES["\x1b[13;2u"] = (
            _PTKeys.Escape,
            _PTKeys.ControlM,
        )
        _pt_ansi_seq.ANSI_SEQUENCES["\x1b[27;2;13~"] = (
            _PTKeys.Escape,
            _PTKeys.ControlM,
        )
        _pt_ansi_seq._clawcodex_shift_enter_registered = True  # type: ignore[attr-defined]
    try:
        from prompt_toolkit.completion import FuzzyCompleter
    except Exception:  # pragma: no cover
        FuzzyCompleter = None  # type: ignore
    from prompt_toolkit.key_binding import KeyBindings
    _HAS_PROMPT_TOOLKIT = True
except ModuleNotFoundError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

    class FileHistory:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

    class AutoSuggestFromHistory:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

    class Style:  # type: ignore
        @staticmethod
        def from_dict(*args, **kwargs):
            return None

    class WordCompleter:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

    class Completer:  # type: ignore
        def get_completions(self, *args, **kwargs):
            return iter(())

    class Completion:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

    FuzzyCompleter = None  # type: ignore

    class KeyBindings:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

    class PromptSession:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

        def prompt(self, *args, **kwargs):
            raise EOFError()


class _SlashOnlyCompleter(Completer):
    """Trigger autocompletion only for slash commands, matching the reference
    Claude Code behavior.

    Rules (mirrors ``typescript/src/utils/suggestions/commandSuggestions.ts``):

    * If the whole buffer starts with ``/`` and the cursor is on the first
      token, complete slash commands (prefix match against the command name).
    * If the cursor sits on a ``/``-prefixed token preceded by whitespace,
      complete that mid-input slash command.
    * In every other case (plain words like ``hello``, ``ex``, etc.) return
      no completions so the user can type freely without a suggestion popup.

    The underlying list of command words is provided as a callable so it can
    be refreshed dynamically (skills, plugin commands, …) without replacing
    the completer instance.
    """

    def __init__(self, words_provider):
        self._words_provider = words_provider

    def get_completions(self, document, complete_event):  # type: ignore[override]
        text = document.text_before_cursor
        token, token_start = self._current_slash_token(text)
        if token is None:
            return
        partial = token[1:].lower()  # strip leading '/'
        words = self._words_provider() or []
        seen: set[str] = set()
        for word in words:
            if not isinstance(word, str) or not word.startswith("/"):
                continue
            name = word[1:]
            key = name.lower()
            if key in seen:
                continue
            if not partial or key.startswith(partial):
                seen.add(key)
                start_position = token_start - len(text)
                yield Completion(
                    text=word,
                    start_position=start_position,
                    display=word,
                )

    @staticmethod
    def _current_slash_token(text: str) -> tuple[str | None, int]:
        """Return ``(token, start_index)`` for the slash token under the cursor.

        ``token`` is ``None`` when the cursor is not inside a slash command.
        ``start_index`` is the offset of the leading ``/`` in ``text``.
        """

        if not text:
            return None, 0
        if text.startswith("/"):
            # Start-of-buffer slash: complete only while cursor is on the
            # command word (before the first space).
            space_idx = text.find(" ")
            if space_idx != -1:
                return None, 0
            return text, 0
        # Mid-input slash: whitespace + '/' immediately before the cursor.
        for i in range(len(text) - 1, -1, -1):
            ch = text[i]
            if ch == "/":
                if i > 0 and not text[i - 1].isspace():
                    return None, 0
                token = text[i:]
                if " " in token:
                    return None, 0
                return token, i
            if ch.isspace():
                return None, 0
        return None, 0

try:
    from rich.console import Console, Group
    from rich.align import Align
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.markdown import Markdown
    from rich.columns import Columns
except ModuleNotFoundError:  # pragma: no cover
    class Console:  # type: ignore
        def print(self, *args, **kwargs):
            return None

    Group = None  # type: ignore
    Align = None  # type: ignore
    Panel = None  # type: ignore
    Table = None  # type: ignore
    Text = None  # type: ignore
    Columns = None  # type: ignore

    class Markdown:  # type: ignore
        def __init__(self, text: str):
            self.text = text
from pathlib import Path
import asyncio
import sys
import json
import threading
from collections import deque
from typing import Any

from src.agent import Session
from src.config import get_provider_config
from src.outputStyles import resolve_output_style
from src.providers import get_provider_class
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import ChatMessage
from src.providers.minimax_provider import MinimaxProvider
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.tool_system.agent_loop import ToolEvent, run_agent_loop, summarize_tool_result, summarize_tool_use
from src.query.engine import QueryEngine, QueryEngineConfig
from src.query.query import StreamEvent
from src.types.messages import AssistantMessage, SystemMessage, UserMessage
from src.types.content_blocks import TextBlock, ToolUseBlock, ToolResultBlock

# New command system imports
from src.command_system import (
    CommandRegistry,
    CommandResult,
    create_command_context,
    execute_command_async,
    execute_command_sync,
    register_builtin_commands,
)
from src.cost_tracker import CostTracker
from src.history import HistoryLog
from src.repl.at_file_completer import AtFileCompleter
from src.repl.live_status import LiveStatus

try:
    from prompt_toolkit.patch_stdout import patch_stdout as _pt_patch_stdout
except ModuleNotFoundError:  # pragma: no cover - prompt_toolkit guarded above
    from contextlib import nullcontext as _pt_patch_stdout  # type: ignore


def _format_edit_summary_text(adds: int, removes: int) -> str:
    """Format an "Added X lines, removed Y lines" summary.

    Mirrors the pluralization in the TS reference component
    (``FileEditToolUpdatedMessage.tsx``) — sentence-cased standalone
    clauses, lowercase ``removed`` after a comma.
    """

    if adds <= 0 and removes <= 0:
        return ""
    parts: list[str] = []
    if adds > 0:
        parts.append(f"Added {adds} {'line' if adds == 1 else 'lines'}")
    if removes > 0:
        verb = "Removed" if adds == 0 else "removed"
        parts.append(f"{verb} {removes} {'line' if removes == 1 else 'lines'}")
    return ", ".join(parts)


# Tool names whose consecutive calls should be coalesced into a single
# ``TaskListV2`` snapshot in the transcript. See
# ``typescript/src/components/TaskListV2.tsx`` for the reference UI.
_TASK_WIDGET_TOOL_NAMES: set[str] = {
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "TodoWrite",
}


class ClawcodexREPL:
    """Interactive REPL for Claw Codex."""

    def __init__(
        self,
        provider_name: str = "glm",
        stream: bool = False,
        *,
        permission_mode: str = "default",
        is_bypass_permissions_mode_available: bool = False,
    ):
        # Mark this process as running an interactive session BEFORE we build
        # the tool registry. Tools like TaskCreate / TaskUpdate / TodoWrite
        # toggle themselves on/off via ``is_todo_v2_enabled()`` which reads
        # this flag (mirroring ``typescript/src/bootstrap/state.ts``).
        from src.bootstrap.state import set_is_interactive

        set_is_interactive(True)

        # Stash the resolved permission state so ``ToolContext`` honors
        # ``--dangerously-skip-permissions`` / ``--permission-mode`` flags
        # passed at startup. See ``src/cli.py:_resolve_permission_state``.
        self._permission_mode = permission_mode
        self._is_bypass_permissions_mode_available = bool(
            is_bypass_permissions_mode_available
        )

        self.console = Console()
        self.provider_name = provider_name
        self.stream = stream

        # Load configuration
        config = get_provider_config(provider_name)
        if not config.get("api_key"):
            self.console.print("[red]Error: API key not configured.[/red]")
            self.console.print("Run [bold]clawcodex login[/bold] to configure.")
            sys.exit(1)

        # Initialize provider
        provider_class = get_provider_class(provider_name)
        self.provider = provider_class(
            api_key=config["api_key"],
            base_url=config.get("base_url"),
            model=config.get("default_model")
        )

        # Create session
        self.session = Session.create(
            provider_name,
            self.provider.model
        )

        self.tool_registry = build_default_registry(provider=self.provider)
        self._engine_messages: list[Any] = []
        from src.permissions.types import ToolPermissionContext

        self.tool_context = ToolContext(
            workspace_root=Path.cwd(),
            permission_context=ToolPermissionContext(
                mode=self._permission_mode,  # type: ignore[arg-type]
                is_bypass_permissions_mode_available=(
                    self._is_bypass_permissions_mode_available
                ),
            ),
        )
        self.tool_context.ask_user = self._ask_user_questions
        # Permission handler with status control for proper input handling
        self._current_status = None
        if self._permission_mode == "bypassPermissions":
            # The bypass mode short-circuits the registry's permission check
            # before the handler is ever consulted, but a few tools call the
            # handler directly (e.g. the doc-write gate). Auto-allow there
            # too so the user's explicit opt-in is honored end-to-end.
            self.tool_context.allow_docs = True
            self.tool_context.permission_handler = (
                lambda _tn, _msg, _sug: (True, False)
            )
        else:
            self.tool_context.permission_handler = self._handle_permission_request

        # Persistent bottom-toolbar accumulators. Mirrors the TS Ink
        # status line that always shows model · provider · cwd · turn /
        # token totals.
        self._stats_turns: int = 0
        self._stats_input_tokens: int = 0
        self._stats_output_tokens: int = 0
        self._direct_stream_abort: bool = False

        # Messages the user typed into LiveStatus while the agent was
        # working. The main run() loop drains this before falling back to
        # ``prompt_session.prompt()`` so queued prompts are sent back-to-back
        # without the user having to retype them — matches the TS Ink
        # reference's "type while it's still thinking" affordance.
        self._queued_prompts: list[str] = []
        self._queued_prompts_lock = threading.Lock()
        # Permission dialogs can be requested from different worker paths
        # (e.g. subagents/tools). Serialize interactive prompts so we never
        # mount competing prompt_toolkit applications at once.
        self._permission_prompt_lock = threading.Lock()
        # Session-level cache for permission decisions (tool_name -> allow/deny)
        # so identical prompts in loops don't repeatedly interrupt the user.
        self._permission_decision_cache: dict[str, bool] = {}

        # The currently mounted ``LiveStatus`` (if any). ``_safe_input``
        # pauses it before reading a synchronous answer (e.g. permission
        # prompts) so two prompt_toolkit Applications don't fight over
        # the TTY and tear the spinner row.
        self._active_live_status: LiveStatus | None = None

        # Bounded stash of (label, full_content) pairs for blocks rendered
        # truncated in the transcript (currently only Write previews).
        # ``ctrl+o`` re-prints the most recent entry as a fresh block
        # below — see ``_do_expand_last``. Bounded so the deque doesn't
        # grow unboundedly during a long session.
        self._expandable_blocks: deque[tuple[str, str]] = deque(maxlen=20)

        # Original built-in commands - define this FIRST!
        self._original_built_ins = [
            "/",
            "/help",
            "/exit",
            "/quit",
            "/q",
            "/clear",
            "/save",
            "/load",
            "/stream",
            "/render-last",
            "/tools",
            "/tool",
            "/skills",
            "/init",
            "/tui",
        ]
        self._built_in_commands = list(self._original_built_ins)

        # Initialize new command system
        self._init_command_system()

        # Prompt toolkit with tab completion
        history_file = Path.home() / ".clawcodex" / "history"
        history_file.parent.mkdir(parents=True, exist_ok=True)

        # ``_SlashOnlyCompleter`` handles ``/`` slash commands; the
        # ``AtFileCompleter`` adds ``@``-mention file completion that
        # mirrors the TS Ink reference (see
        # ``typescript/src/hooks/fileSuggestions.ts``). Merging keeps
        # both behaviors active simultaneously without either side
        # interfering with the other's trigger.
        from prompt_toolkit.completion import merge_completers

        self._slash_completer = _SlashOnlyCompleter(self._get_slash_command_words)
        self._at_completer = AtFileCompleter(
            cwd=str(self.tool_context.workspace_root)
        )
        self.completer = merge_completers(
            [self._slash_completer, self._at_completer]
        )

        # Key bindings.
        #
        # Multiline-entry contract (mirrors
        # ``typescript/src/hooks/useTextInput.ts#handleEnter``):
        #
        #   * plain Enter          -> submit
        #   * Shift+Enter          -> insert newline  (terminals with
        #                             Kitty-protocol CSI 13;2u, iTerm2
        #                             or VSCode configured via
        #                             /terminal-setup)
        #   * Meta/Alt/Option+Enter -> insert newline  (universally
        #                             supported: the terminal sends
        #                             "\x1b\r", which prompt_toolkit
        #                             parses as Escape+ControlM)
        #   * ``\`` + Enter        -> insert newline  (portable fallback
        #                             that works on ANY terminal — the
        #                             trailing backslash is removed and
        #                             replaced by a real newline)
        #
        # The buffer is always created in ``multiline=True`` mode so that
        # real newlines can live in it; we override the default Enter
        # behavior below so Enter still submits (prompt_toolkit's default
        # in multiline mode is "insert newline").
        self.bindings = KeyBindings()
        if hasattr(self.bindings, "add"):
            @self.bindings.add("/")  # type: ignore[attr-defined]
            def _show_slash_completions(event):  # type: ignore[no-untyped-def]
                # Always insert the literal ``/`` — earlier versions
                # short-circuited when the buffer was non-empty and
                # silently swallowed the keystroke, so paths like
                # ``src/repl/core.py`` were untypable. Only auto-pop
                # the slash-command menu when ``/`` is the first
                # character of the buffer (mirrors the TS reference's
                # ``commandSuggestions`` trigger rule).
                buf = event.current_buffer
                was_empty = buf.text == ""
                buf.insert_text("/")
                if was_empty:
                    buf.start_completion(select_first=False)

            @self.bindings.add("c-m")  # type: ignore[attr-defined]
            def _enter_submits_or_backslash_newline(event):  # type: ignore[no-untyped-def]
                """Enter: submit, or convert trailing ``\\`` into a newline.

                Exactly mirrors the TypeScript ``handleEnter`` logic. When a
                completion popup is open we accept the current selection and
                close the popup (prompt_toolkit's default Enter behavior) so
                the slash-command menu still works as expected.
                """
                buf = event.current_buffer
                if buf.complete_state:
                    buf.complete_state = None
                    return
                text = buf.text
                pos = buf.cursor_position
                if pos > 0 and text[pos - 1] == "\\":
                    buf.delete_before_cursor(count=1)
                    buf.insert_text("\n")
                    return
                buf.validate_and_handle()

            @self.bindings.add("escape", "c-m")  # type: ignore[attr-defined]
            def _meta_or_shift_enter_inserts_newline(event):  # type: ignore[no-untyped-def]
                """Meta+Enter (and Kitty-protocol Shift+Enter): insert ``\\n``."""
                event.current_buffer.insert_text("\n")

            @self.bindings.add("c-o")  # type: ignore[attr-defined]
            def _expand_last(event):  # type: ignore[no-untyped-def]
                """Ctrl+O: re-print the most recent truncated block in
                full as a fresh block below the prompt. ``run_in_terminal``
                temporarily exits the prompt loop so the output doesn't
                fight the prompt's redraw."""

                try:
                    from prompt_toolkit.application import run_in_terminal
                    run_in_terminal(self._do_expand_last)
                except Exception:
                    # Fallback: print directly. Prompt may redraw oddly
                    # but at least the expansion lands in scrollback.
                    self._do_expand_last()

        self.prompt_session = PromptSession(
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=self.completer,
            style=Style.from_dict({
                # Dim background on the ``❯`` marker so the user
                # input row reads as a discrete block above the
                # transcript — matches Claude Code's input
                # background highlight.
                'prompt': 'bold fg:ansiblue bg:#262626',
                'bottom-toolbar': 'fg:#888888 bg:default',
            }),
            key_bindings=self.bindings,
            complete_while_typing=True,
            multiline=True,
            prompt_continuation=self._prompt_continuation,
            bottom_toolbar=self._bottom_toolbar,
        )

    def _bottom_toolbar(self):
        """Single-line status footer for the input prompt.

        Mirrors the TS Ink reference's persistent status row at the
        bottom: provider, model, current working directory, and
        accumulated turn / token counts for the session. Kept terse so
        it doesn't compete with the input row for attention.
        """

        try:
            provider = (
                getattr(self.provider, "provider_name", None)
                or self.provider_name
                or "?"
            )
            model = getattr(self.provider, "model", "") or "?"
            cwd_full = str(self.tool_context.cwd or self.tool_context.workspace_root)
            cwd = self._shorten_path_text(cwd_full) or cwd_full
            return (
                f" {provider} · {model} · {cwd} · "
                f"turns: {self._stats_turns} · "
                f"tokens: {self._stats_input_tokens} in / "
                f"{self._stats_output_tokens} out "
            )
        except Exception:
            # Never let the toolbar break the input prompt.
            return ""

    def _echo_user_input(self, text: str) -> None:
        """Print a user message to the transcript with a dim background.

        Used for queued submissions (typed during agent work via
        :class:`LiveStatus`) and any other path that needs to surface a
        user-authored message into scrollback. Each line is padded to
        the terminal width so the highlight reaches the right edge,
        matching the boxed input row Claude Code renders for user
        messages.
        """

        try:
            import shutil
            width = shutil.get_terminal_size((100, 24)).columns
        except Exception:
            width = 80

        from rich.text import Text

        bg_style = "on grey15"
        prefix = "❯ "
        for idx, line in enumerate(text.split("\n")):
            body = (prefix if idx == 0 else "  ") + line
            padded = body.ljust(max(width, len(body)))
            self.console.print(
                Text(padded, style=bg_style),
                highlight=False,
                soft_wrap=True,
            )

    def _prompt_continuation(self, width, line_number, is_soft_wrap):
        """Continuation prompt for wrapped / multi-line input.

        Logical lines get ``"… "`` so it's obvious we're in an in-progress
        multi-line prompt; soft wraps get blank padding so long lines
        flow naturally. Width-padded to keep the text column aligned
        with the primary ``❯ `` prompt.
        """
        if is_soft_wrap:
            return " " * width
        marker = "… "
        if width <= len(marker):
            return marker[:width]
        return marker.rjust(width)

    def _ask_user_questions(self, questions: list[dict]) -> dict[str, str]:
        # Stop the Rich status spinner if running, so we can get clean input
        if self._current_status is not None:
            try:
                self._current_status.stop()
            except Exception:
                pass

        answers: dict[str, str] = {}
        for q in questions:
            if isinstance(q, str):
                q = {"question": q}
            if not isinstance(q, dict):
                continue
            question_text = str(q.get("question", "")).strip()
            options = q.get("options") or []
            multi = bool(q.get("multiSelect", False))
            if not question_text or not isinstance(options, list) or len(options) < 2:
                continue

            self.console.print(f"\n[bold]{question_text}[/bold]")
            labels: list[str] = []
            for i, opt in enumerate(options, start=1):
                if isinstance(opt, str):
                    opt = {"label": opt, "description": ""}
                if not isinstance(opt, dict):
                    continue
                label = str(opt.get("label", "")).strip()
                desc = str(opt.get("description", "")).strip()
                labels.append(label)
                self.console.print(f"  {i}. {label}  [dim]{desc}[/dim]")
            other_idx = len(labels) + 1
            self.console.print(f"  {other_idx}. Other  [dim]Provide custom text[/dim]")

            prompt = "Select (comma-separated) > " if multi else "Select > "
            raw = self._safe_input(prompt).strip()
            if not raw:
                choice_str = "1"
            else:
                choice_str = raw

            selected: list[str] = []
            parts = [p.strip() for p in choice_str.split(",") if p.strip()]
            if not parts:
                parts = ["1"]
            for part in parts:
                try:
                    idx = int(part)
                except ValueError:
                    idx = -1
                if idx == other_idx:
                    free = input("Other > ").strip()
                    if free:
                        selected.append(free)
                    continue
                if 1 <= idx <= len(labels):
                    selected.append(labels[idx - 1])
            if not selected:
                selected = [labels[0]]
            answers[question_text] = ", ".join(selected) if multi else selected[0]

        # Restart spinner after getting answers
        if self._current_status is not None:
            try:
                self._current_status.start()
            except Exception:
                pass

        return answers

    def _handle_permission_request(
        self,
        tool_name: str,
        message: str,
        suggestion: str | None,
    ) -> tuple[bool, bool]:
        """Handle interactive permission requests from tools.

        Args:
            tool_name: Name of the tool requesting permission.
            message: Message explaining what permission is needed.
            suggestion: Optional suggestion for enabling the setting.

        Returns:
            Tuple of (allowed: bool, continue_without_caching: bool).
            continue_without_caching is always False since we don't cache in REPL.
        """
        with self._permission_prompt_lock:
            cache_key = tool_name.strip().lower()
            cached = self._permission_decision_cache.get(cache_key)
            if cached is not None:
                return cached, False

            # Stop the Rich status spinner if running, so we can get clean input
            if self._current_status is not None:
                try:
                    self._current_status.stop()
                except Exception:
                    pass

            self.console.print("")
            self.console.print("[bold yellow]⚠ Permission Required[/bold yellow]")
            self.console.print(f"  {message}")
            self.console.print("")

            # Determine if this is a setting that can be enabled
            can_enable_setting = False
            setting_to_enable: str | None = None

            msg_lower = message.lower()
            if "allow_docs" in msg_lower or "documentation files" in msg_lower:
                if not self.tool_context.allow_docs:
                    can_enable_setting = True
                    setting_to_enable = "allow_docs"

            # Build options
            options: list[tuple[str, str]] = [
                ("y", "Yes, allow this action"),
                ("n", "No, deny this action"),
            ]
            if can_enable_setting:
                options.insert(0, ("e", f"Enable {setting_to_enable} and allow"))

            self.console.print("[bold]Options:[/bold]")
            for i, (key, desc) in enumerate(options, start=1):
                self.console.print(f"  {i}. [{key}] {desc}")
            self.console.print("")

            # Get input via prompt_toolkit so it cooperates with patch_stdout()
            # and the LiveStatus bottom region.
            choice = self._safe_input("Select option> ").strip().lower()

            # Parse choice based on the actual displayed options
            if can_enable_setting:
                # Menu: 1=Enable, 2=Yes, 3=No
                if choice in ("1", "e", "enable"):
                    self._enable_permission_setting(setting_to_enable)
                    self._permission_decision_cache[cache_key] = True
                    return True, False
                elif choice in ("2", "y", "yes", ""):
                    self._permission_decision_cache[cache_key] = True
                    return True, False
                elif choice in ("3", "n", "no"):
                    self._permission_decision_cache[cache_key] = False
                    return False, False
            else:
                # Menu: 1=Yes, 2=No
                if choice in ("1", "y", "yes", ""):
                    self._permission_decision_cache[cache_key] = True
                    return True, False
                elif choice in ("2", "n", "no"):
                    self._permission_decision_cache[cache_key] = False
                    return False, False

            # Default to deny for invalid input
            self.console.print("[dim]Invalid choice, defaulting to deny.[/dim]")
            return False, False

    def _enable_permission_setting(self, setting_name: str | None) -> None:
        """Enable a permission setting in the tool context."""
        if not setting_name:
            return

        self.console.print(f"\n[dim]Enabling {setting_name}...[/dim]")

        if setting_name == "allow_docs":
            self.tool_context.allow_docs = True
            self.console.print(f"[green]✓ {setting_name} enabled for this session[/green]")
            return

        self.console.print(f"[dim]Could not enable {setting_name}.[/dim]")

    def _init_command_system(self):
        """Initialize the new command system."""
        # Also register to global registry so execute_command_async can find commands
        register_builtin_commands(None)  # None = use global registry

        # Create command registry and register built-ins
        self.command_registry = CommandRegistry()
        register_builtin_commands(self.command_registry)

        # Create cost tracker and history
        self.cost_tracker = CostTracker()
        self.history_log = HistoryLog()

        # Create command context
        self.command_context = create_command_context(
            workspace_root=Path.cwd(),
            conversation=self.session.conversation,
            cost_tracker=self.cost_tracker,
            history=self.history_log,
        )

        # Merge new commands with built-in list for completion
        self._update_built_in_commands_with_command_system()

    def _update_built_in_commands_with_command_system(self):
        """Update the built-in commands list with commands from the new system."""
        # Start with original built-ins
        self._built_in_commands = list(self._original_built_ins)

        # Add commands from the new command system
        try:
            for cmd in self.command_registry.list_commands():
                cmd_name = f"/{cmd.name}"
                if cmd_name not in self._built_in_commands:
                    self._built_in_commands.append(cmd_name)
                # Add aliases
                for alias in cmd.aliases:
                    alias_name = f"/{alias}"
                    if alias_name not in self._built_in_commands:
                        self._built_in_commands.append(alias_name)
        except Exception:
            pass

    def _try_execute_new_command(self, command: str, args: str) -> tuple[bool, str | None]:
        """Try to execute a command using the new command system (sync path for LocalCommand only).

        Returns:
            Tuple of (handled: bool, result_text: str | None)
        """
        try:
            success, result_text, error = execute_command_sync(
                command, args, self.command_context
            )
            if success:
                return True, result_text
            else:
                return False, error
        except Exception as e:
            return False, str(e)

    async def _try_execute_command_async(self, command: str, args: str) -> CommandResult:
        """Execute a command asynchronously, supporting both LocalCommand and PromptCommand.

        Returns:
            CommandResult with the execution result
        """
        try:
            return await execute_command_async(command, args, self.command_context)
        except Exception as e:
            return CommandResult.error(command, str(e))

    def _handle_command_result(self, result: CommandResult) -> bool:
        """Handle the result of a command execution.

        Returns True if the command was handled, False otherwise.
        """
        if not result.success:
            if result.error:
                self.console.print(f"[red]{result.error}[/red]")
            return True

        if result.result_type == "text":
            if result.text:
                self.console.print("\n" + result.text)
                self.console.print()
            return True

        elif result.result_type == "prompt":
            # For PromptCommand, extract the text content and send to LLM
            prompt_text = ""
            for item in result.prompt_content:
                if item.get("type") == "text":
                    prompt_text = item.get("text", "")
                    break

            if prompt_text:
                # Send the prompt to the LLM for interactive execution
                # Use higher max_turns for complex commands like /init
                self.console.print("[dim]Initializing workspace setup...[/dim]")
                self.chat(prompt_text)
            return True

        elif result.result_type == "skip":
            # Command handled silently
            return True

        return False

    def _get_slash_command_words(self) -> list[str]:
        words = list(self._built_in_commands)
        try:
            from src.skills.loader import get_all_skills

            cwd = self.tool_context.cwd or self.tool_context.workspace_root
            for s in get_all_skills(project_root=cwd):
                words.append(f"/{s.name}")
        except Exception:
            pass
        deduped: list[str] = []
        seen: set[str] = set()
        for w in words:
            lw = w.lower()
            if lw in seen:
                continue
            seen.add(lw)
            deduped.append(w)
        return deduped

    def _refresh_completer(self) -> None:
        # The slash + ``@``-file completers are stable for the lifetime
        # of the REPL: ``_SlashOnlyCompleter`` reads its word list
        # lazily, and ``AtFileCompleter`` rebuilds its file index on
        # its own TTL. We just rebind the merged completer onto the
        # PromptSession in case anything in the tool-system replaced
        # ``self.completer`` with a stub.
        try:
            from prompt_toolkit.completion import merge_completers

            if not hasattr(self, "_at_completer") or self._at_completer is None:
                self._at_completer = AtFileCompleter(
                    cwd=str(self.tool_context.workspace_root)
                )
            if not hasattr(self, "_slash_completer") or self._slash_completer is None:
                self._slash_completer = _SlashOnlyCompleter(
                    self._get_slash_command_words
                )
            self.completer = merge_completers(
                [self._slash_completer, self._at_completer]
            )
            if hasattr(self, "prompt_session") and getattr(self.prompt_session, "completer", None) is not None:
                self.prompt_session.completer = self.completer
        except Exception:
            return

    def _show_slash_palette(self, query: str | None = None) -> None:
        q = (query or "").strip().lower()
        self.console.print("\n[bold]Available commands and skills:[/bold]")

        # Collect all commands
        all_commands: list[tuple[str, str, str]] = []  # (name, description, type)
        seen: set[str] = set()

        def add_command(name: str, desc: str, cmd_type: str = "command") -> None:
            if name in seen:
                return
            seen.add(name)
            if q and q not in name.lower() and q not in desc.lower():
                return
            all_commands.append((name, desc, cmd_type))

        # Add built-in commands
        for cmd in self._original_built_ins:
            if cmd == "/":
                continue
            add_command(cmd, "", "command")

        # Add commands from new command system
        try:
            for cmd in self.command_registry.list_commands():
                cmd_name = f"/{cmd.name}"
                if cmd_name in self._original_built_ins:
                    continue
                alias_str = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
                add_command(f"{cmd_name}{alias_str}", cmd.description, "command")
        except Exception:
            pass

        # Add skills
        try:
            from src.skills.loader import get_all_skills

            cwd = self.tool_context.cwd or self.tool_context.workspace_root
            skills = list(get_all_skills(project_root=cwd))
            skills.sort(key=lambda s: s.name.lower())
            for s in skills:
                desc = (s.description or "").strip()
                add_command(f"/{s.name}", desc, "skill")
        except Exception:
            pass

        # Sort and display
        all_commands.sort(key=lambda x: x[0].lower())
        for name, desc, cmd_type in all_commands:
            if cmd_type == "skill":
                self.console.print(f"  [magenta]{name}[/magenta]")
                if desc:
                    self.console.print(f"    [dim]{desc}[/dim]")
            else:
                if desc:
                    self.console.print(f"  {name}  [dim]- {desc}[/dim]")
                else:
                    self.console.print(f"  {name}")

        self.console.print()

    _MAX_PREVIEW_LINES = 3

    _EDIT_DIFF_MAX_LINES = 30

    def _format_edit_diff_preview(self, hunks: list[dict]):
        """Render an Edit/MultiEdit structured patch as a Rich :class:`Group`.

        Mirrors the TUI's ``EditActivity`` body: a one-line
        ``Added X lines, removed Y lines`` summary above the line-numbered
        diff with red/green markers and shaded backgrounds. Long diffs are
        truncated with a ``… +N more diff lines`` footer to keep the
        scrollback compact.
        """

        adds = 0
        removes = 0
        for hunk in hunks:
            for raw in hunk.get("lines") or []:
                if raw.startswith("+"):
                    adds += 1
                elif raw.startswith("-"):
                    removes += 1

        summary = _format_edit_summary_text(adds, removes) or "no changes"
        summary_text = Text(summary, style="dim")

        diff = Text()
        rendered = 0
        truncated = False
        for hunk in hunks:
            if truncated:
                break
            old_lineno = int(hunk.get("oldStart", 0) or 0)
            new_lineno = int(hunk.get("newStart", 0) or 0)
            for raw in hunk.get("lines") or []:
                if rendered >= self._EDIT_DIFF_MAX_LINES:
                    truncated = True
                    break
                # Edit's structuredPatch carries lines that already retain
                # their source ``\n`` (Edit calls splitlines(keepends=True)
                # before unified_diff). Strip it here so we don't double up
                # on newlines and produce blank rows between every entry.
                stripped = raw.rstrip("\n").rstrip("\r")
                if stripped.startswith("+"):
                    diff.append(f"     {new_lineno:>4}  ", style="green")
                    diff.append("+ ", style="bold green")
                    diff.append(stripped[1:] + "\n", style="green on #0f2314")
                    new_lineno += 1
                elif stripped.startswith("-"):
                    diff.append(f"     {old_lineno:>4}  ", style="red")
                    diff.append("- ", style="bold red")
                    diff.append(stripped[1:] + "\n", style="red on #2a1414")
                    old_lineno += 1
                else:
                    body = stripped[1:] if stripped.startswith(" ") else stripped
                    diff.append(f"     {old_lineno:>4}    ", style="dim")
                    diff.append(body + "\n", style="dim")
                    old_lineno += 1
                    new_lineno += 1
                rendered += 1

        if truncated:
            total = sum(len(h.get("lines") or []) for h in hunks)
            remaining = max(0, total - rendered)
            diff.append(
                f"     … +{remaining} more diff "
                f"{'line' if remaining == 1 else 'lines'}\n",
                style="dim",
            )

        return Group(summary_text, diff) if Group is not None else summary_text

    def _format_tool_result_preview(
        self,
        block: "ToolResultBlock",
        tool_info: tuple[str, dict] | None,
    ):
        """Return either a plain string or a Rich renderable (Edit diff)."""
        import json as _json

        raw = block.content if isinstance(block.content, str) else str(block.content)
        tool_name = tool_info[0] if tool_info else ""

        # Prefer the original ToolResult.output threaded through as
        # in-process metadata — `block.content` is the API-mapped string
        # (e.g. "The file X has been updated successfully.") and no longer
        # carries structured fields like Edit's `structuredPatch`.
        parsed: dict | None = None
        meta_output = getattr(block, "metadata", None)
        if isinstance(meta_output, dict):
            tool_output = meta_output.get("tool_output")
            if isinstance(tool_output, dict):
                parsed = tool_output
        if parsed is None:
            try:
                parsed = _json.loads(raw)
                if not isinstance(parsed, dict):
                    parsed = None
            except Exception:
                pass

        if tool_name == "Read":
            if parsed:
                t = parsed.get("type", "")
                if t == "file_unchanged":
                    return "Unchanged since last read"
                f = parsed.get("file", {})
                n = f.get("numLines", 0)
                if t == "text":
                    return f"Read {n} {'line' if n == 1 else 'lines'}"
                if t == "notebook":
                    cells = f.get("cells", [])
                    return f"Read {len(cells)} cells"
                if t == "pdf":
                    return "Read PDF"
                if t == "image":
                    return "Read image"
            if "unchanged" in raw.lower():
                return "Unchanged since last read"
            return "Read file"

        if tool_name == "Bash":
            stdout = raw
            if parsed:
                stdout = parsed.get("stdout", "")
                stderr = parsed.get("stderr", "")
                if not stdout and not stderr:
                    return "(No output)"
                if not stdout:
                    stdout = stderr
            if not stdout or not stdout.strip():
                return "(No output)"
            lines = stdout.rstrip("\n").split("\n")
            total_chars = len(stdout)
            if len(lines) <= self._MAX_PREVIEW_LINES + 1 and total_chars <= 200:
                return stdout.rstrip("\n")
            if len(lines) <= self._MAX_PREVIEW_LINES + 1:
                first_line = lines[0]
                if len(first_line) > 120:
                    return f"{first_line[:120]}…\n… +{total_chars - 120} chars"
                return f"{stdout[:200]}…\n… +{total_chars - 200} chars"
            preview = "\n".join(lines[: self._MAX_PREVIEW_LINES])
            remaining = len(lines) - self._MAX_PREVIEW_LINES
            return f"{preview}\n… +{remaining} lines"

        if tool_name == "Glob":
            if parsed:
                n = parsed.get("numFiles", 0)
                return f"Found {n} {'file' if n == 1 else 'files'}"
            return "done"

        if tool_name == "Grep":
            if parsed:
                mode = parsed.get("mode", "files_with_matches")
                if mode == "content":
                    n = parsed.get("numLines", 0)
                    return f"Found {n} {'line' if n == 1 else 'lines'}"
                if mode == "count":
                    n = parsed.get("numMatches", 0)
                    nf = parsed.get("numFiles", 0)
                    return f"Found {n} {'match' if n == 1 else 'matches'} across {nf} {'file' if nf == 1 else 'files'}"
                n = parsed.get("numFiles", 0)
                return f"Found {n} {'file' if n == 1 else 'files'}"
            return "done"

        if tool_name == "Write":
            # Port of ``typescript/src/tools/FileWriteTool/UI.tsx`` —
            # ``FileWriteToolCreatedMessage`` renders ``Wrote N lines to
            # <path>`` followed by the first MAX_LINES_TO_RENDER (10) lines
            # of the new content and a ``… +M lines`` footer when truncated.
            # Update results render a diff in the TS UI; we keep that as a
            # follow-up and only show the header for now.
            path = ""
            content = ""
            if tool_info and isinstance(tool_info[1], dict):
                path = tool_info[1].get("file_path") or tool_info[1].get("filePath") or ""
                c = tool_info[1].get("content")
                if isinstance(c, str):
                    content = c
            if parsed:
                path = parsed.get("filePath") or parsed.get("path") or path
                if not content:
                    c = parsed.get("content")
                    if isinstance(c, str):
                        content = c
            if not path:
                return "done"

            # Distinguish create vs update from the API result string emitted
            # by ``_map_result_to_api`` in ``src/tool_system/tools/write.py``.
            is_update = "has been updated successfully" in raw

            short = self._shorten_path_text(path)
            # ``countLines`` parity: trailing newline is a terminator.
            if content:
                parts = content.split("\n")
                n = len(parts) - 1 if content.endswith("\n") else len(parts)
            else:
                n = 0

            header = (
                f"Wrote {n} {'line' if n == 1 else 'lines'} to {short}"
                if n else f"Wrote to {short}"
            )
            if is_update or not content:
                return header

            MAX = 10
            content_lines = content.split("\n")
            # Drop the trailing empty element produced by a terminator newline
            # so we don't render a phantom blank line N+1.
            if content.endswith("\n") and content_lines and content_lines[-1] == "":
                content_lines = content_lines[:-1]
            preview_lines = content_lines[:MAX]
            body = "\n".join(
                f"     {i:>3}  {line}"
                for i, line in enumerate(preview_lines, start=1)
            )
            extra = max(0, len(content_lines) - MAX)
            footer = (
                f"\n     … +{extra} {'line' if extra == 1 else 'lines'} (ctrl+o to expand)"
                if extra else ""
            )
            if extra:
                # Stash the full content so ``ctrl+o`` can re-print it as
                # a fresh block below. We can't mutate the truncated
                # block in scrollback once it's printed, so the
                # expansion appends instead of swapping in place.
                self._stash_expandable(f"Write({short})", content)
            if not body:
                return header
            return f"{header}\n{body}{footer}"

        if tool_name in ("Edit", "MultiEdit"):
            # Port of ``typescript/src/components/FileEditToolUpdatedMessage.tsx``:
            # show ``Added X lines, removed Y lines`` plus the line-numbered
            # diff with red/green markers, instead of a bare ``done``.
            if parsed:
                hunks = parsed.get("structuredPatch") or []
                if hunks:
                    return self._format_edit_diff_preview(hunks)
                if parsed.get("type") == "create":
                    path = parsed.get("filePath") or parsed.get("path") or ""
                    content = parsed.get("content") or ""
                    if path:
                        if content:
                            parts = content.split("\n")
                            n = len(parts) - 1 if content.endswith("\n") else len(parts)
                        else:
                            n = 0
                        short = self._shorten_path_text(path)
                        return (
                            f"Wrote {n} {'line' if n == 1 else 'lines'} to {short}"
                            if n else f"Wrote to {short}"
                        )
            return "done"

        if tool_name == "TaskCreate":
            if parsed:
                task = parsed.get("task") or {}
                subject = task.get("subject") or ""
                task_id = task.get("id") or ""
                if subject:
                    return f"Created task #{task_id}: {subject}"
                if task_id:
                    return f"Created task #{task_id}"
            return "Task created"

        if tool_name == "TaskUpdate":
            if parsed:
                changed = parsed.get("updatedFields") or []
                task_id = parsed.get("taskId") or ""
                status_change = parsed.get("statusChange") or {}
                if status_change:
                    return (
                        f"Task #{task_id}: {status_change.get('from')} → "
                        f"{status_change.get('to')}"
                    )
                if "deleted" in changed:
                    return f"Task #{task_id} deleted"
                if changed:
                    return f"Task #{task_id} updated ({', '.join(changed)})"
            return "Task updated"

        if tool_name == "TaskList":
            if parsed:
                tasks = parsed.get("tasks") or []
                return f"Listed {len(tasks)} task{'' if len(tasks) == 1 else 's'}"
            return "Listed tasks"

        if tool_name == "TaskGet":
            if parsed and parsed.get("task"):
                t = parsed["task"]
                return f"Task #{t.get('id')}: {t.get('subject')} ({t.get('status')})"
            return "Task not found"

        if tool_name == "TodoWrite":
            if parsed:
                new = parsed.get("newTodos") or []
                done = sum(1 for t in new if t.get("status") == "completed")
                in_prog = sum(1 for t in new if t.get("status") == "in_progress")
                pending = sum(1 for t in new if t.get("status") == "pending")
                return (
                    f"{len(new)} todo{'' if len(new) == 1 else 's'} "
                    f"({done} done, {in_prog} in progress, {pending} open)"
                )
            return "Todos updated"

        if not raw or len(raw) < 80:
            return raw or "done"
        lines = raw.rstrip("\n").split("\n")
        total_chars = len(raw)
        if len(lines) <= self._MAX_PREVIEW_LINES + 1 and total_chars <= 200:
            return raw.rstrip("\n")
        if len(lines) <= self._MAX_PREVIEW_LINES + 1:
            first_line = lines[0]
            if len(first_line) > 120:
                return f"{first_line[:120]}…\n… +{total_chars - 120} chars"
            return f"{raw[:200]}…\n… +{total_chars - 200} chars"
        preview = "\n".join(lines[: self._MAX_PREVIEW_LINES])
        remaining = len(lines) - self._MAX_PREVIEW_LINES
        return f"{preview}\n… +{remaining} lines"

    def _available_agents(self) -> list[Any]:
        """Return the list of agent definitions that can be invoked via ``@agent-...``.

        Pulls built-in agents and merges any extras registered on
        ``tool_context.options.agent_definitions`` so that user/plugin agents
        participate in the same ``@agent-<type>`` lookup that the TypeScript
        ``processAgentMentions`` uses.
        """
        try:
            from src.agent.agent_definitions import get_built_in_agents
        except Exception:
            return []

        agents = list(get_built_in_agents())
        extra = getattr(getattr(self.tool_context, "options", None), "agent_definitions", None)
        if isinstance(extra, dict):
            agents.extend(extra.values())
        elif isinstance(extra, list):
            agents.extend(extra)
        return agents

    def _enqueue_prompt(self, text: str) -> None:
        """Append a user-typed prompt to the queue from any thread."""

        text = (text or "").strip()
        if not text:
            return
        with self._queued_prompts_lock:
            self._queued_prompts.append(text)

    def _pop_queued_prompt(self) -> str | None:
        with self._queued_prompts_lock:
            if not self._queued_prompts:
                return None
            return self._queued_prompts.pop(0)

    def _queued_count(self) -> int:
        with self._queued_prompts_lock:
            return len(self._queued_prompts)

    def _status_message(self) -> str:
        """Spinner status text. Includes queued-prompt count when non-zero."""

        n = self._queued_count()
        if n == 0:
            return "Thinking…"
        return f"Thinking… ({n} queued)"

    def _safe_input(self, prompt: str) -> str:
        """Read a line from the user.

        Tries ``prompt_toolkit.prompt`` first because it cooperates with
        :func:`prompt_toolkit.patch_stdout.patch_stdout`. Falls back to
        bare ``input()`` if prompt_toolkit isn't available or the runtime
        can't open a TTY (e.g. piped stdin).

        If a :class:`~src.repl.live_status.LiveStatus` is currently
        mounted (we're inside ``chat()``), pause it for the duration of
        the read. Two prompt_toolkit Applications cannot share a TTY —
        without pausing, the spinner row keeps redrawing and shreds the
        user's keystrokes.
        """

        live = self._active_live_status

        def _do_read() -> str:
            if _HAS_PROMPT_TOOLKIT:
                try:
                    from prompt_toolkit import prompt as pt_prompt

                    return pt_prompt(prompt)
                except Exception:
                    pass
            return input(prompt)

        if live is not None:
            with live.paused():
                return _do_read()
        return _do_read()

    # ------------------------------------------------------------------
    # ctrl+o expansion (truncated tool-result blocks)
    # ------------------------------------------------------------------
    def _stash_expandable(self, label: str, content: str) -> None:
        """Record a truncated block so ``ctrl+o`` can re-print its
        full content. Bounded by ``self._expandable_blocks`` ``maxlen``."""

        if not content:
            return
        self._expandable_blocks.append((label, content))

    def _do_expand_last(self) -> None:
        """Print the most recently stashed truncated block in full.

        Invoked via ``prompt_toolkit.application.run_in_terminal`` from
        the ``ctrl+o`` keybindings on both the idle prompt and the
        ``LiveStatus`` live region, so the print doesn't fight either
        Application's redraw.
        """

        if not self._expandable_blocks:
            return
        label, content = self._expandable_blocks[-1]
        lines = content.split("\n")
        # Trim the trailing empty element produced by a terminator newline
        # so we don't render a phantom blank line at the end.
        if content.endswith("\n") and lines and lines[-1] == "":
            lines = lines[:-1]
        self.console.print(
            f"  [dim]── Expanded {label} ──[/dim]", highlight=False
        )
        for i, line in enumerate(lines, start=1):
            # markup=False / highlight=False so a stray ``[`` or ``$`` in
            # the file content can't be interpreted as Rich markup or a
            # syntax token.
            self.console.print(
                f"     {i:>3}  {line}",
                markup=False,
                highlight=False,
                soft_wrap=True,
            )
        self.console.print("  [dim]── End ──[/dim]", highlight=False)

    def _shorten_path_text(self, text: str) -> str:
        root = str(self.tool_context.workspace_root)
        cwd = str(self.tool_context.cwd or self.tool_context.workspace_root)
        for base in (cwd, root):
            prefix = base.rstrip("/") + "/"
            if text.startswith(prefix):
                return "./" + text[len(prefix):]
            text = text.replace(prefix, "")
        return text

    # ------------------------------------------------------------------
    # Task widget (coalesced Task* / TodoWrite snapshot)
    # ------------------------------------------------------------------
    #
    # Mirrors ``typescript/src/components/TaskListV2.tsx``: instead of
    # printing one bullet per ``TaskCreate``/``TaskUpdate`` call, we wait
    # until a run of task-management calls is finished and then render
    # the current task-state once.

    def _render_task_snapshot(self) -> None:
        """Print a compact snapshot of the current task / todo list."""
        # Prefer V2 tasks (interactive mode); fall back to V1 todos.
        tasks = self._collect_task_entries()
        if not tasks:
            return

        def _sort_key(entry: dict[str, Any]) -> tuple[int, str]:
            try:
                return (0, f"{int(entry['id']):08d}")
            except (TypeError, ValueError):
                return (1, str(entry.get("id", "")))

        sorted_tasks = sorted(tasks, key=_sort_key)

        completed = sum(1 for t in sorted_tasks if t["status"] == "completed")
        in_progress = sum(1 for t in sorted_tasks if t["status"] == "in_progress")
        pending = len(sorted_tasks) - completed - in_progress

        parts = [f"[bold]{completed}[/bold] done"]
        if in_progress > 0:
            parts.append(f"[bold]{in_progress}[/bold] in progress")
        parts.append(f"[bold]{pending}[/bold] open")
        header = (
            f"[green]●[/green] [bold cyan]Tasks[/bold cyan] "
            f"[dim]([bold]{len(sorted_tasks)}[/bold] total: {', '.join(parts)})[/dim]"
        )
        self.console.print(header)

        unresolved = {t["id"] for t in sorted_tasks if t["status"] != "completed"}

        for task in sorted_tasks:
            status = task["status"]
            subject = str(task.get("subject") or "")
            if status == "completed":
                icon, style, subject_style = "✓", "green", "dim strike"
            elif status == "in_progress":
                icon, style, subject_style = "◼", "cyan", "bold"
            else:
                icon, style, subject_style = "◻", "dim", ""

            blocked_by = [
                bid for bid in (task.get("blockedBy") or []) if bid in unresolved
            ]
            owner = task.get("owner")
            suffix_parts: list[str] = []
            if owner:
                suffix_parts.append(f"[dim] (@{owner})[/dim]")
            if blocked_by:
                blockers = ", ".join(f"#{bid}" for bid in sorted(blocked_by))
                suffix_parts.append(f"[dim] ▸ blocked by {blockers}[/dim]")
            suffix = "".join(suffix_parts)

            subject_markup = (
                f"[{subject_style}]{subject}[/{subject_style}]" if subject_style else subject
            )
            self.console.print(
                f"  [{style}]{icon}[/{style}] {subject_markup}{suffix}"
            )

    def _collect_task_entries(self) -> list[dict[str, Any]]:
        """Return a normalised list of task dicts from the tool context.

        Uses V2 ``tasks`` if populated, otherwise falls back to the V1
        ``todos`` list written by ``TodoWrite``. Both are coalesced into
        the same shape: ``{id, status, subject, owner?, blockedBy?}``.
        """
        entries: list[dict[str, Any]] = []
        v2 = getattr(self.tool_context, "tasks", None) or {}
        if isinstance(v2, dict) and v2:
            for tid, t in v2.items():
                if not isinstance(t, dict):
                    continue
                entries.append({
                    "id": str(t.get("id", tid)),
                    "status": t.get("status", "pending"),
                    "subject": t.get("subject", ""),
                    "owner": t.get("owner"),
                    "blockedBy": list(t.get("blockedBy") or []),
                })
            return entries

        todos = getattr(self.tool_context, "todos", None) or []
        for td in todos:
            if not isinstance(td, dict):
                continue
            entries.append({
                "id": str(td.get("id", "")),
                "status": td.get("status", "pending"),
                "subject": td.get("content") or td.get("activeForm") or "",
                "owner": None,
                "blockedBy": [],
            })
        return entries

    def _display_cwd(self) -> str:
        cwd = str(Path.cwd())
        home = str(Path.home())
        if cwd.startswith(home):
            return cwd.replace(home, "~", 1)
        return cwd

    def _truncate_middle(self, text: str, limit: int) -> str:
        if limit <= 0 or len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        head = max(1, (limit - 1) // 2)
        tail = max(1, limit - head - 1)
        return f"{text[:head]}…{text[-tail:]}"

    def _handoff_to_textual_tui(self) -> None:
        """Switch from the Rich REPL into the Textual TUI for this session.

        Runs the Textual app inline. When the user quits the TUI (``Ctrl+D``
        or ``/exit`` inside it), control returns here and the caller's REPL
        loop resumes with the same session, provider, tool registry and
        tool context — so conversation history is preserved across the
        handoff.
        """
        try:
            from src.tui.app import ClawCodexTUI
        except Exception as exc:
            self.console.print(
                f"[red]Textual TUI is unavailable: {exc}[/red]\n"
                "[dim]Install it with `pip install 'textual>=0.79'`.[/dim]"
            )
            return

        self.console.print("[dim]Entering Textual TUI. Press Ctrl+D or type /exit to return.[/dim]")
        app = ClawCodexTUI(
            provider=self.provider,
            provider_name=self.provider_name,
            workspace_root=self.tool_context.workspace_root,
            tool_registry=self.tool_registry,
            tool_context=self.tool_context,
            session=self.session,
            stream=True,
        )
        try:
            app.run()
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            self.console.print(f"[red]TUI exited with error: {exc}[/red]")
        finally:
            # Dump the transcript we captured right before exit so the
            # conversation stays in the host's scrollback — matching
            # ink's non-fullscreen behaviour.
            snapshot = getattr(app, "exit_snapshot", None) or []
            for piece in snapshot:
                try:
                    self.console.print(piece)
                except Exception:
                    continue
            self.console.print("[dim]Returned from Textual TUI.[/dim]")

    def _print_startup_header(self):
        from src import __version__

        display_path = self._display_cwd()
        provider_label = f"{self.provider_name.upper()} Provider"
        model_label = self.provider.model or "Unknown model"

        mascot_ascii = "\n".join([
            "  /\\__/\\",
            " / o  o \\",
            "(  __  )",
            " \\/__/  ",
        ])

        if Panel is None or Group is None or Align is None or Table is None or Text is None or Columns is None:
            print(mascot_ascii)
            print(f"Claw Codex v{__version__}")
            print(f"{model_label} · {provider_label}")
            print(f"{display_path}\n")
            return

        width = getattr(self.console, "width", 80)
        content_width = max(28, min(width - 12, 72))
        table = Table.grid(padding=(0, 1))
        table.add_column(style="bright_black", justify="right", no_wrap=True)
        table.add_column(style="white", ratio=1)
        table.add_row("Version", Text.assemble(("Claw Codex", "bold white"), ("  ", ""), (f"v{__version__}", "bold cyan")))
        table.add_row("Model", Text(model_label, style="bold magenta"))
        table.add_row("Provider", Text(provider_label, style="bold green"))
        table.add_row("Workspace", Text(self._truncate_middle(display_path, content_width - 12), style="bold blue"))

        footer = Text("/help  •  /tools  •  /tui  •  /stream  •  /exit", style="dim")
        mascot_block = Text(mascot_ascii, style="bold orange3", no_wrap=True)
        body = Group(
            Columns([mascot_block, table], align="center", expand=False),
            Text(""),
            Align.center(footer),
        )
        header = Panel(
            body,
            border_style="bright_black",
            title="[bold bright_cyan] CLAW CODEX [/bold bright_cyan]",
            subtitle="[dim]interactive terminal[/dim]",
            padding=(1, 2),
        )
        self.console.print(header)
        self.console.print()

    def run(self):
        """Run the REPL."""
        self._print_startup_header()

        while True:
            try:
                self._refresh_completer()
                queued = self._pop_queued_prompt()
                if queued is not None:
                    # Echo queued submissions with a dim background so
                    # they read as a discrete user-message block when
                    # they land in scrollback alongside the agent's
                    # transcript output.
                    self._echo_user_input(queued)
                    user_input = queued
                else:
                    # Blank line of breathing room between the previous
                    # transcript and the next prompt. The bg highlight
                    # on the prompt itself (PromptSession ``style``)
                    # provides the visual cue that the next row is
                    # user input — no divider needed.
                    self.console.print()
                    # The prompt session is configured with ``multiline=True``
                    # up front so that newlines (via Shift+Enter / Meta+Enter
                    # / ``\`` + Enter) can live in the buffer. Plain Enter
                    # still submits via our custom ``c-m`` binding.
                    user_input = self.prompt_session.prompt('❯ ')

                if not user_input.strip():
                    continue

                if user_input.startswith('/'):
                    self.handle_command(user_input)
                    continue

                self.chat(user_input)

            except KeyboardInterrupt:
                self.console.print("\n[yellow]Interrupted. Type /exit to quit.[/yellow]")
                continue
            except EOFError:
                self.console.print("\n[blue]Goodbye![/blue]")
                break

    def handle_command(self, command: str):
        """Handle slash commands."""
        raw = command.strip()
        if raw == "/":
            self._show_slash_palette()
            return
        if raw.startswith("/") and " " not in raw and raw.lower() not in (c.lower() for c in self._built_in_commands):
            query = raw[1:]
            if query:
                self._show_slash_palette(query=query)
                return

        # First, try the new command system
        if raw.startswith("/"):
            parts = raw[1:].split(maxsplit=1)
            cmd_name = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            # Check if this command exists in the new command system
            # but skip the ones we handle specially
            # Note: /context, /compact, /skill need special handling, don't route through new system
            # /init is handled via new command system (PromptCommand) so it's NOT in special_commands
            special_commands = {
                'exit', 'quit', 'q',
                'help', 'tools', 'tool',
                'save', 'load', 'stream', 'render-last',
                'skill',
                'context', 'compact',  # These need special handling
                'tui',  # handoff to Textual TUI
                ''
            }

            # Handle /init through the new command system (PromptCommand path)
            if cmd_name == 'init':
                # Use async path for PromptCommand
                try:
                    # Run async command execution in a new event loop
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            asyncio.run,
                            self._try_execute_command_async(cmd_name, args)
                        )
                        result = future.result()

                    if result.success:
                        self._handle_command_result(result)
                    elif result.error:
                        self.console.print(f"[red]{result.error}[/red]")
                except Exception as e:
                    self.console.print(f"[red]Error executing /init: {e}[/red]")
                return

            if cmd_name not in special_commands:
                # Try to execute via new command system
                # First try sync path for LocalCommand (faster)
                try:
                    handled, result_text = self._try_execute_new_command(cmd_name, args)
                    if handled:
                        if result_text:
                            self.console.print("\n" + result_text)
                        self.console.print()
                        return
                except Exception as e:
                    # Fall through to async path
                    pass

                # Use async path for PromptCommand
                # Run in a new event loop since we're in a sync context
                try:
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            asyncio.run,
                            self._try_execute_command_async(cmd_name, args)
                        )
                        result = future.result()

                    if result.success:
                        if self._handle_command_result(result):
                            return
                except Exception:
                    pass

        # Fall back to original command handling
        cmd = raw.lower()

        if cmd in ['/exit', '/quit', '/q']:
            self.console.print("[blue]Goodbye![/blue]")
            sys.exit(0)

        elif cmd == '/tui':
            self._handoff_to_textual_tui()

        elif cmd == '/help':
            self.show_help()

        elif cmd == '/tools':
            names = [spec.name for spec in self.tool_registry.list_specs()]
            names.sort(key=str.lower)
            self.console.print("\n[bold]Available tools:[/bold]")
            for name in names:
                self.console.print(f"  - {name}")
            self.console.print()

        elif cmd.startswith('/tool'):
            parts = command.strip().split(maxsplit=2)
            if len(parts) < 2:
                self.console.print("[red]Usage: /tool <name> <json-input>[/red]")
                return
            name = parts[1]
            payload = {}
            if len(parts) == 3:
                try:
                    payload = json.loads(parts[2])
                except json.JSONDecodeError as e:
                    self.console.print(f"[red]Invalid JSON input: {e}[/red]")
                    return
            try:
                result = self.tool_registry.dispatch(ToolCall(name=name, input=payload), self.tool_context)
            except Exception as e:
                self.console.print(f"[red]Tool error: {e}[/red]")
                return
            self.console.print("\n[bold]Tool result:[/bold]")
            self.console.print(json.dumps(result.output, indent=2, ensure_ascii=False))
            self.console.print()

        elif cmd == '/clear':
            # Try new command system first, fall back to original
            try:
                handled, result_text = self._try_execute_new_command('clear', '')
                if handled and result_text:
                    self.console.print("\n[green]" + result_text + "[/green]")
                    return
            except Exception:
                pass
            # Original implementation
            self.session.conversation.clear()
            self._engine_messages = []
            self.console.print("[green]Conversation cleared.[/green]")

        elif cmd == '/save':
            self.save_session()

        elif cmd == '/stream' or cmd.startswith('/stream '):
            parts = raw.split(maxsplit=1)
            if len(parts) == 1:
                status = "enabled" if self.stream else "disabled"
                self.console.print(f"[green]Stream mode {status}.[/green]")
                return

            action = parts[1].strip().lower()
            if action in {"on", "true", "1", "enable", "enabled"}:
                self.stream = True
            elif action in {"off", "false", "0", "disable", "disabled"}:
                self.stream = False
            elif action == "toggle":
                self.stream = not self.stream
            else:
                self.console.print("[red]Usage: /stream [on|off|toggle][/red]")
                return

            status = "enabled" if self.stream else "disabled"
            self.console.print(f"[green]Stream mode {status}.[/green]")

        elif cmd == '/render-last':
            rendered = self._render_last_assistant_message()
            if not rendered:
                self.console.print("[yellow]No assistant response available to render.[/yellow]")

        elif cmd.startswith('/load'):
            parts = command.strip().split(maxsplit=1)
            if len(parts) < 2:
                self.console.print("[red]Usage: /load <session-id>[/red]")
            else:
                session_id = parts[1]
                self.load_session(session_id)

        elif cmd == '/skill':
            self._handle_skill_command()

        elif cmd == '/context':
            # Populate command context config for context analysis
            self.command_context.config["provider"] = self.provider
            self.command_context.config["model"] = self.provider.model
            self.command_context.config["tool_schemas"] = [
                spec.to_dict() if hasattr(spec, "to_dict") else {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": dict(spec.input_schema) if hasattr(spec.input_schema, "keys") else spec.input_schema,
                }
                for spec in self.tool_registry.list_specs()
            ]
            self.command_context.config["system_prompt"] = ""
            # Try new command system
            try:
                handled, result_text = self._try_execute_new_command('context', '')
                if handled and result_text:
                    self.console.print(Markdown(result_text))
                    return
            except Exception:
                pass
            self.console.print("[yellow]/context analysis unavailable in this context.[/yellow]")

        elif cmd == '/compact':
            # Populate command context config for compact
            self.command_context.config["provider"] = self.provider
            self.command_context.config["model"] = self.provider.model
            self.command_context.config["system_prompt"] = ""
            # Try new command system
            try:
                handled, result_text = self._try_execute_new_command('compact', '')
                if handled and result_text:
                    self.console.print("\n[green]" + result_text + "[/green]")
                    return
            except Exception:
                pass
            # Simple fallback: just clear conversation
            self.session.conversation.clear()
            self._engine_messages = []
            self.console.print("[green]Conversation cleared.[/green]")

        else:
            if raw.startswith("/"):
                if self._try_run_skill_slash(raw):
                    return
            self.console.print(f"[red]Unknown command: {command}[/red]")

    def _try_run_skill_slash(self, raw: str) -> bool:
        text = raw.strip()
        if not text.startswith("/"):
            return False
        body = text[1:]
        if not body:
            return False
        if body.split(maxsplit=1)[0].lower() in {c.lstrip("/").lower() for c in self._built_in_commands if c != "/"}:
            return False

        parts = body.split(maxsplit=1)
        skill_name = parts[0].strip()
        args = parts[1] if len(parts) > 1 else ""
        if not skill_name:
            return False

        try:
            result = self.tool_registry.dispatch(
                ToolCall(name="Skill", input={"skill": skill_name, "args": args}),
                self.tool_context,
            )
        except Exception as e:
            self.console.print(f"[red]Skill error: {e}[/red]")
            return True

        payload = result.output if isinstance(result.output, dict) else {}
        if result.is_error or not payload.get("success"):
            err = payload.get("error") if isinstance(payload.get("error"), str) else "Unknown skill error"
            self.console.print(f"[red]{err}[/red]")
            return True

        self.console.print(f"[dim]Launching skill: {payload.get('commandName', skill_name)}[/dim]")
        meta_parts: list[str] = []
        loaded = payload.get("loadedFrom")
        if isinstance(loaded, str) and loaded:
            meta_parts.append(f"source={loaded}")
        model = payload.get("model")
        if isinstance(model, str) and model:
            meta_parts.append(f"model={model}")
        tools = payload.get("allowedTools")
        if isinstance(tools, list) and tools:
            shown = ", ".join(str(t) for t in tools[:6])
            more = f" (+{len(tools) - 6})" if len(tools) > 6 else ""
            meta_parts.append(f"tools={shown}{more}")
        if meta_parts:
            self.console.print(f"[dim]{' · '.join(meta_parts)}[/dim]")

        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            self.console.print("[red]Skill produced empty prompt[/red]")
            return True

        self.chat(prompt)
        return True

    def show_help(self):
        """Show help message."""
        help_text = """
**Available Commands:**

- `/` - Show all commands and skills
- `/help` - Show this help message
- `/exit`, `/quit`, `/q` - Exit the REPL
- `/clear`, `/reset`, `/new` - Clear conversation history
- `/save` - Save current session
- `/load <session-id>` - Load a previous session
- `/stream [on|off|toggle]` - Toggle live response rendering
- `/render-last` - Re-render the last assistant reply as Markdown
- `/tools` - List available built-in tools
- `/tool <name> <json>` - Run a tool directly
- `/skills` - List all available skills
- `/init` - Create CLAUDE.md file for the project
- `/cost` - Show session cost and usage
- `/compact` - Compact conversation to save context space
- `/tui` - Switch into the Textual-based full-screen TUI (opt-in)

**Usage:**
- Type your message and press Enter to chat
- Use Tab for command completion
- Press Ctrl+C to interrupt current operation
- Press Ctrl+D to exit
- Multi-line input: Shift+Enter, Meta/Alt+Enter, or `\` + Enter inserts a newline; plain Enter submits
"""
        self.console.print(Markdown(help_text))

    def _handle_skill_command(self) -> None:
        """Handle /skill command - list all available skills."""
        try:
            from src.skills.loader import get_all_skills

            cwd = self.tool_context.cwd or self.tool_context.workspace_root
            skills = list(get_all_skills(project_root=cwd))
            skills.sort(key=lambda s: s.name.lower())

            if not skills:
                self.console.print("\n[bold]Available Skills:[/bold]")
                self.console.print("[dim]No skills found.[/dim]")
                self.console.print("[dim]Create skills in ~/.clawcodex/skills/ or ~/.claude/skills/ or .clawcodex/skills/ in your project.[/dim]")
                return

            # Group skills by source
            from collections import defaultdict
            by_source: dict[str, list] = defaultdict(list)
            for s in skills:
                loaded = getattr(s, "loaded_from", "") or "unknown"
                by_source[loaded].append(s)

            self.console.print(f"\n[bold]Available Skills ({len(skills)}):[/bold]")
            for source in sorted(by_source.keys()):
                source_skills = by_source[source]
                self.console.print(f"\n[cyan]{source.title()} Skills:[/cyan]")
                for s in source_skills:
                    desc = (getattr(s, "description", None) or "").strip()
                    user_invocable = getattr(s, "user_invocable", True)
                    inv_str = "" if user_invocable else " [dim](not user-invocable)[/dim]"
                    self.console.print(f"  [green]/{s.name}[/green]{inv_str}")
                    if desc:
                        self.console.print(f"    [dim]{desc}[/dim]")
            self.console.print()
        except Exception as e:
            self.console.print(f"[red]Error loading skills: {e}[/red]")

    def _is_recoverable_tool_error(self, tool_name: str, tool_output) -> bool:
        if not isinstance(tool_name, str):
            return False
        if not isinstance(tool_output, dict):
            return False
        name = tool_name.strip().lower()
        err = tool_output.get("error")
        if not isinstance(err, str):
            return False
        e = err.lower()
        if name == "read" and e.startswith("file not found:"):
            p = err.split(":", 2)[-1].strip()
            if "/.clawcodex/skills/" in p or "\\.clawcodex\\skills\\" in p or "/.claude/skills/" in p or "\\.claude\\skills\\" in p:
                return True
        return False

    def _provider_uses_system_kwarg(self) -> bool:
        return isinstance(self.provider, (AnthropicProvider, MinimaxProvider))

    def _build_direct_stream_payload(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        style_name = getattr(self.tool_context, "output_style_name", None)
        style_dir = getattr(self.tool_context, "output_style_dir", None)
        style_prompt = resolve_output_style(style_name, style_dir).prompt

        if self._provider_uses_system_kwarg():
            return self.session.conversation.get_messages(), (
                {"system": style_prompt} if style_prompt.strip() else {}
            )

        messages: list[dict[str, Any]] = []
        for msg in self.session.conversation.messages:
            if isinstance(msg.content, str):
                messages.append({"role": msg.role, "content": msg.content})
        if style_prompt.strip():
            messages = [{"role": "system", "content": style_prompt}, *messages]
        return messages, {}

    def _should_try_direct_stream(self, user_input: str) -> bool:
        if not self.stream:
            return False
        text = user_input.strip().lower()
        if not text or text.startswith("/"):
            return False
        if len(text) > 240:
            return False

        code_task_markers = (
            "/", "\\", "src/", "tests/", ".py", ".ts", ".md",
            "file", "files", "read", "write", "edit", "modify", "change",
            "search", "grep", "glob", "bash", "shell", "command", "run",
            "test", "fix", "bug", "refactor", "repo", "repository",
            "project", "workspace", "folder", "directory", "function",
            "class", "module", "code", "implementation", "readme",
            "pyproject", "package.json", "git", "commit", "diff", "tool",
            "文件", "代码", "仓库", "项目", "目录", "读取", "写入", "修改",
            "搜索", "运行", "测试", "修复", "命令", "工具", "函数", "类",
        )
        return not any(marker in text for marker in code_task_markers)

    def _stream_direct_response(self, on_text_chunk=None) -> str | None:
        streamed_chunks: list[str] = []

        try:
            api_messages, call_kwargs = self._build_direct_stream_payload()
            stream_iter = self.provider.chat_stream(api_messages, tools=None, **call_kwargs)
            for chunk in stream_iter:
                # ESC inside ``LiveStatus`` flips this flag; bail at the next
                # streamed chunk so the user feels an immediate response.
                if getattr(self, "_direct_stream_abort", False):
                    break
                if not chunk:
                    continue
                streamed_chunks.append(chunk)
                if on_text_chunk is not None:
                    on_text_chunk(chunk)
        except Exception:
            # Safe fallback: only fall back when nothing has been emitted yet.
            if not streamed_chunks:
                return None
            raise

        if not streamed_chunks:
            return None

        full_response = "".join(streamed_chunks)
        self.session.conversation.add_assistant_message(full_response)
        return full_response

    def _get_last_assistant_text(self) -> str | None:
        for message in reversed(self.session.conversation.messages):
            if message.role != "assistant":
                continue
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    block_type = getattr(block, "type", None)
                    if block_type == "text":
                        text = getattr(block, "text", "")
                        if isinstance(text, str) and text:
                            parts.append(text)
                joined = "".join(parts).strip()
                if joined:
                    return joined
        return None

    def _render_last_assistant_message(self) -> bool:
        text = self._get_last_assistant_text()
        if not text:
            return False
        self.console.print("\n[bold]Last Assistant Response[/bold]")
        self.console.print(Markdown(text))
        self.console.print()
        return True

    def chat(self, user_input: str, max_turns: int | None = None):
        """Send message to LLM and display response.

        Uses the new QueryEngine (WS-4) state machine to drive the query loop.

        Args:
            user_input: The user message to send.
            max_turns: Maximum number of tool call turns. None means unlimited
                (matching TS interactive REPL behavior). Only set for SDK/non-interactive mode.
        """
        # Expand ``@path`` mentions into context attachments before the model
        # sees the message. Port of
        # ``typescript/src/utils/attachments.ts#processAtMentionedFiles``.
        from src.command_system.input_processing import (
            expand_agent_mentions,
            expand_at_mentions,
            format_at_mention_attachments,
        )

        cwd_for_mentions = str(self.tool_context.cwd or self.tool_context.workspace_root)
        _, at_attachments = expand_at_mentions(user_input, cwd=cwd_for_mentions)

        # Port of ``processAgentMentions`` from
        # ``typescript/src/utils/attachments.ts``: if the user types
        # ``@agent-explore`` (or the autocomplete form ``@"explore (agent)"``),
        # attach a system-reminder telling the model to delegate to that
        # agent via the Agent tool. Mentions of unknown agents are ignored so
        # we don't polute context with misleading reminders.
        agent_attachments = expand_agent_mentions(user_input, self._available_agents())

        all_attachments = list(at_attachments) + list(agent_attachments)
        if all_attachments:
            attachment_text = format_at_mention_attachments(all_attachments)
            user_input = f"{attachment_text}\n\n{user_input}" if attachment_text else user_input
            for att in at_attachments:
                kind = "directory" if att["kind"] == "directory" else "file"
                self.console.print(
                    f"[dim]  ⎿  Listed {kind} {att['display_path']}{'/' if kind == 'directory' else ''}[/dim]"
                )
            for att in agent_attachments:
                self.console.print(
                    f"[dim]  ⎿  Invoking agent @{att['agent_type']}[/dim]"
                )

        self.session.conversation.add_user_message(user_input)

        try:
            self.console.print("\n[bold]Assistant[/bold]")

            stream_started = False

            def _stop_status_once() -> None:
                nonlocal stream_started
                if self._current_status is not None and not stream_started:
                    try:
                        self._current_status.stop()
                    except Exception:
                        pass
                stream_started = True

            if self._should_try_direct_stream(user_input):
                def on_text_chunk_direct(chunk: str) -> None:
                    if not chunk:
                        return
                    _stop_status_once()
                    self.console.print(chunk, end="", markup=False, highlight=False, soft_wrap=True)

                self._direct_stream_abort = False

                def _cancel_direct_stream() -> None:
                    self._direct_stream_abort = True

                _direct_status_ref: list[LiveStatus] = []

                def _on_submit_direct(text: str) -> None:
                    self._enqueue_prompt(text)
                    if _direct_status_ref:
                        _direct_status_ref[0].update(self._status_message())

                with _pt_patch_stdout(raw=True):
                    with LiveStatus(
                        self._status_message(),
                        on_cancel=_cancel_direct_stream,
                        on_submit=_on_submit_direct,
                        on_expand=self._do_expand_last,
                        completer=self.completer,
                    ) as status:
                        _direct_status_ref.append(status)
                        self._active_live_status = status
                        try:
                            direct_response = self._stream_direct_response(
                                on_text_chunk=on_text_chunk_direct,
                            )
                        finally:
                            self._active_live_status = None
                if direct_response is not None:
                    self.console.print("\n")
                    return

            from src.outputStyles import resolve_output_style

            style_name = getattr(self.tool_context, "output_style_name", None)
            style_dir = getattr(self.tool_context, "output_style_dir", None)
            style_prompt = resolve_output_style(style_name, style_dir).prompt

            tools = self.tool_registry.list_tools()

            prior_messages = list(self._engine_messages)

            engine_config = QueryEngineConfig(
                cwd=self.tool_context.workspace_root,
                provider=self.provider,
                tool_registry=self.tool_registry,
                tools=tools,
                tool_context=self.tool_context,
                append_system_prompt=style_prompt,
                max_turns=max_turns,
                initial_messages=prior_messages,
            )
            engine = QueryEngine(engine_config)

            response_text = ""
            last_text_was_printed = False

            async def _run_query() -> tuple[str, bool]:
                nonlocal stream_started
                last_text = ""
                last_text_was_printed = False
                api_call_count = 0
                tool_use_map: dict[str, tuple[str, dict]] = {}
                # Per-turn token totals — surfaced to the spinner suffix
                # via ``status.set_tokens(...)``. Local to the closure so
                # they reset every turn; ``self._stats_*`` remain the
                # session-cumulative counters for ``/stats``.
                turn_tokens = 0
                # Track whether a Task*/TodoWrite round is "in flight" so we
                # can coalesce a run of task-management calls into a single
                # TaskListV2-style snapshot instead of dumping one ``●`` bullet
                # per call. This mirrors the behaviour of
                # ``typescript/src/components/TaskListV2.tsx``, which re-renders
                # a single widget each time the ``tasks`` slice of AppState
                # changes.
                pending_task_flush = False
                task_tool_ids: set[str] = set()
                # When the assistant emits multiple tool_use blocks in one
                # message, printing all ``● Tool(args)`` lines eagerly and
                # then dumping every ``⎿ preview`` underneath stacks the
                # output into one tall, hard-to-scan block. Defer each
                # header so it prints right above its matching result —
                # this is what produces the per-call "small block" look
                # in the TS Ink reference (see
                # ``typescript/src/components/REPL.tsx``).
                pending_tool_use_prints: dict[str, str] = {}

                def _flush_task_snapshot_if_any() -> None:
                    nonlocal pending_task_flush
                    if not pending_task_flush:
                        return
                    pending_task_flush = False
                    self._render_task_snapshot()

                async for msg in engine.submit_message(user_input):
                    if isinstance(msg, StreamEvent):
                        if msg.type == "stream_request_start":
                            api_call_count += 1
                            # The TypeScript reference does not print a
                            # ``Thinking…`` line between API calls — the
                            # spinner already communicates activity. Printing
                            # it between every tool round-trip clutters the
                            # transcript, so we suppress it here to match
                            # ``typescript/src/components/REPL.tsx``.
                        continue

                    if isinstance(msg, AssistantMessage):
                        self.session.conversation.add_assistant_message(msg.content)
                        usage = getattr(msg, "usage", None)
                        if isinstance(usage, dict):
                            in_toks = int(usage.get("input_tokens", 0) or 0)
                            out_toks = int(usage.get("output_tokens", 0) or 0)
                            self._stats_input_tokens += in_toks
                            self._stats_output_tokens += out_toks
                            turn_tokens += in_toks + out_toks
                            if _engine_status_ref:
                                _engine_status_ref[0].set_tokens(turn_tokens)
                        content = msg.content
                        if isinstance(content, str):
                            if content:
                                last_text = content
                                last_text_was_printed = False
                                _stop_status_once()
                                stream_started = True
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, TextBlock) and block.text:
                                    # New assistant text -> first flush any
                                    # pending task snapshot so the widget
                                    # lands above the explanatory text.
                                    _flush_task_snapshot_if_any()
                                    last_text = block.text
                                    _stop_status_once()
                                    stream_started = True
                                    self.console.print(Markdown(block.text))
                                    last_text_was_printed = True
                                elif isinstance(block, ToolUseBlock):
                                    tool_use_map[block.id] = (block.name, block.input)
                                    if block.name in _TASK_WIDGET_TOOL_NAMES:
                                        task_tool_ids.add(block.id)
                                        pending_task_flush = True
                                        continue
                                    # Any non-task tool call terminates the
                                    # current task widget run; flush the
                                    # snapshot before printing the new call.
                                    _flush_task_snapshot_if_any()
                                    summary = summarize_tool_use(block.name, block.input)
                                    if isinstance(summary, str) and summary:
                                        summary = self._shorten_path_text(summary)
                                    # Mirror the compact ``● ToolName(args)``
                                    # rendering used by
                                    # ``typescript/src/components/REPL.tsx``
                                    # (and Claude Code's Ink UI). The function-
                                    # call shape is less noisy than the old
                                    # ``• ToolName (args) running…`` format
                                    # and is easier to scan.
                                    call_args = f"({summary})" if summary else "()"
                                    pending_tool_use_prints[block.id] = (
                                        f"[green]●[/green] [bold cyan]{block.name}[/bold cyan]"
                                        f"[dim]{call_args}[/dim]"
                                    )
                        continue

                    if isinstance(msg, SystemMessage):
                        subtype = getattr(msg, "subtype", None)
                        if subtype == "max_turns_reached":
                            _stop_status_once()
                            stream_started = True
                            self.console.print(
                                f"[yellow]Reached maximum number of turns. "
                                f"The task may be incomplete.[/yellow]"
                            )
                        continue

                    if isinstance(msg, UserMessage):
                        content = msg.content
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, ToolResultBlock):
                                    # Suppress per-call ``⎿ ...`` result
                                    # output for task widget tools — the
                                    # flushed snapshot already reflects the
                                    # post-call state. Errors still surface
                                    # so the user sees validation problems.
                                    if block.tool_use_id in task_tool_ids:
                                        # Task tool headers were never
                                        # buffered (we render a snapshot
                                        # instead) so nothing to flush.
                                        if block.is_error:
                                            _flush_task_snapshot_if_any()
                                            err_text = block.content if isinstance(block.content, str) else str(block.content)
                                            self.console.print(f"[red]  ⎿  {err_text or 'Error'}[/red]")
                                        continue
                                    # Print the deferred ``● Tool(args)``
                                    # header right above this result so each
                                    # call renders as a self-contained block.
                                    header = pending_tool_use_prints.pop(
                                        block.tool_use_id, None
                                    )
                                    if header is not None:
                                        self.console.print(header)
                                    # Match the TS UI's tool-result prefix
                                    # ``  ⎿  `` (see
                                    # ``typescript/src/components/MessageResponse.tsx``).
                                    if block.is_error:
                                        err_text = block.content if isinstance(block.content, str) else str(block.content)
                                        self.console.print(f"[red]  ⎿  {err_text or 'Error'}[/red]")
                                    else:
                                        preview = self._format_tool_result_preview(
                                            block, tool_use_map.get(block.tool_use_id),
                                        )
                                        if isinstance(preview, str):
                                            self.console.print(f"[dim]  ⎿  {preview}[/dim]")
                                        else:
                                            # Rich renderable (e.g. Edit diff
                                            # Group) — emit the prefix then
                                            # the renderable so its internal
                                            # styling survives the dim wrap.
                                            self.console.print("[dim]  ⎿  [/dim]", end="")
                                            self.console.print(preview)
                        continue

                # Flush any trailing task snapshot at end-of-turn so the
                # final "N tasks (...)" summary lands in the transcript.
                _flush_task_snapshot_if_any()

                # If a tool_use never received a matching result (turn cut
                # short, error mid-loop), surface the headers we were
                # holding so the user can still see what was attempted.
                for header in pending_tool_use_prints.values():
                    self.console.print(header)
                pending_tool_use_prints.clear()

                return last_text, last_text_was_printed

            def _cancel_engine() -> None:
                try:
                    engine.interrupt()
                except Exception:
                    pass

            _engine_status_ref: list[LiveStatus] = []

            def _on_submit_engine(text: str) -> None:
                self._enqueue_prompt(text)
                if _engine_status_ref:
                    _engine_status_ref[0].update(self._status_message())

            with _pt_patch_stdout(raw=True):
                with LiveStatus(
                    self._status_message(),
                    on_cancel=_cancel_engine,
                    on_submit=_on_submit_engine,
                    on_expand=self._do_expand_last,
                    completer=self.completer,
                ) as status:
                    _engine_status_ref.append(status)
                    self._active_live_status = status
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor() as pool:
                                response_text, last_text_was_printed = pool.submit(lambda: asyncio.run(_run_query())).result()
                        else:
                            response_text, last_text_was_printed = loop.run_until_complete(_run_query())
                    except RuntimeError:
                        response_text, last_text_was_printed = asyncio.run(_run_query())
                    finally:
                        self._active_live_status = None

            engine.reset_abort_controller()

            self._engine_messages = engine.get_messages()
            self._stats_turns += 1

            if not last_text_was_printed and response_text:
                self.console.print(Markdown(response_text))
            self.console.print()

        except Exception as e:
            error_str = str(e)

            if "401" in error_str or "authentication" in error_str.lower() or "令牌" in error_str:
                self.console.print(f"\n[red]❌ Authentication Error: {e}[/red]")
                self.console.print("\n[yellow]Your API key appears to be invalid or expired.[/yellow]")

                from rich.prompt import Prompt
                choice = Prompt.ask(
                    "\nWould you like to reconfigure your API key now?",
                    choices=["y", "n"],
                    default="y"
                )

                if choice == "y":
                    self._handle_relogin()
                else:
                    self.console.print("\n[dim]You can run [bold]clawcodex login[/bold] later to update your API key.[/dim]")
            else:
                self.console.print(f"\n[red]Error: {e}[/red]")
                import traceback
                traceback.print_exc()

    def _handle_relogin(self):
        """Handle re-authentication when API key fails."""
        from rich.prompt import Prompt
        from src.config import set_api_key, set_default_provider
        from src.providers import PROVIDER_INFO

        self.console.print("\n[bold blue]🔑 Reconfigure API Key[/bold blue]\n")

        # Show available providers and defaults
        provider_names = list(PROVIDER_INFO.keys())
        self.console.print("[bold]Available providers:[/bold]")
        for name, info in PROVIDER_INFO.items():
            self.console.print(f"  [cyan]{name}[/cyan] - {info['label']} (default model: {info['default_model']})")
        self.console.print()

        # Select provider
        provider = Prompt.ask(
            "Select LLM provider",
            choices=provider_names,
            default=self.provider_name if self.provider_name in provider_names else "anthropic"
        )

        info = PROVIDER_INFO[provider]

        # Input API Key
        api_key = Prompt.ask(
            f"Enter {provider.upper()} API Key",
            password=True
        )

        if not api_key:
            self.console.print("\n[red]Error: API Key cannot be empty[/red]")
            return

        # Optional: Base URL (show default)
        self.console.print(f"\n[dim]Default:[/dim] {info['default_base_url']}")
        base_url = Prompt.ask(
            f"{provider.upper()} Base URL",
            default=info["default_base_url"]
        )

        # Optional: Default Model (show options)
        self.console.print(f"\n[dim]Available models:[/dim] {', '.join(info['available_models'])}")
        self.console.print(f"[dim]Default:[/dim] [bold]{info['default_model']}[/bold]")
        default_model = Prompt.ask(
            f"{provider.upper()} Default Model",
            default=info["default_model"]
        )

        # Save configuration
        set_api_key(provider, api_key=api_key, base_url=base_url, default_model=default_model)
        set_default_provider(provider)

        self.console.print(f"\n[green]✓ {provider.upper()} API Key updated successfully![/green]\n")

        # Reinitialize provider
        from src.config import get_provider_config
        from src.providers import get_provider_class

        config = get_provider_config(provider)
        provider_class = get_provider_class(provider)

        self.provider = provider_class(
            api_key=config["api_key"],
            base_url=config.get("base_url"),
            model=config.get("default_model")
        )
        self.provider_name = provider

        # Rebuild tool registry with new provider so Agent tool works
        self.tool_registry = build_default_registry(provider=self.provider)

        self.console.print("[green]✓ Provider reinitialized. You can continue chatting![/green]\n")

    def save_session(self):
        """Save current session."""
        self.session.save()
        self.console.print(f"[green]Session saved: {self.session.session_id}[/green]")

    def load_session(self, session_id: str):
        """Load a previous session.

        Args:
            session_id: Session ID to load
        """
        from src.agent import Session

        loaded_session = Session.load(session_id)
        if loaded_session is None:
            self.console.print(f"[red]Session not found: {session_id}[/red]")
            return

        # Replace current session
        self.session = loaded_session
        self.console.print(f"[green]Session loaded: {session_id}[/green]")
        self.console.print(f"[dim]Provider: {loaded_session.provider}, Model: {loaded_session.model}[/dim]")
        self.console.print(f"[dim]Messages: {len(loaded_session.conversation.messages)}[/dim]")

        # Show conversation history
        if loaded_session.conversation.messages:
            self.console.print("\n[bold]Conversation History:[/bold]")
            for msg in loaded_session.conversation.messages[-5:]:  # Show last 5 messages
                role_color = "blue" if msg.role == "user" else "green"
                self.console.print(f"[{role_color}]{msg.role}[/{role_color}]: {msg.content[:100]}...")
