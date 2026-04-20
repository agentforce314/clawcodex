"""TUI (Textual) entrypoint.

Phase 11 counterpart to :mod:`src.entrypoints.headless`. Where ``headless``
emits NDJSON for pipes, ``tui`` owns the interactive experience: a
retained-mode Textual UI matching the layout of
``typescript/src/screens/REPL.tsx``.

This module deliberately does the provider / session / tool-context setup
*outside* the Textual app so unit tests can construct a :class:`TUIOptions`,
build the app manually, and drive it with :meth:`textual.app.App.run_test`
without touching real network I/O.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.cli_core.exit import cli_error
from src.config import get_default_provider, get_provider_config
from src.providers import get_provider_class


@dataclass
class TUIOptions:
    """Options for :func:`run_tui`. Mirrors :class:`HeadlessOptions`."""

    provider_name: str | None = None
    model: str | None = None
    max_turns: int = 20
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    workspace_root: Path | None = None
    stream: bool = True
    # Test hook: replace the provider instance we'd otherwise build from config.
    provider_factory: Callable[[], object] | None = None


def run_tui(options: TUIOptions) -> int:
    """Boot the Textual TUI and block until the user exits.

    Returns a conventional CLI exit code.
    """

    if not _textual_available():
        cli_error(
            "error: textual is not installed. "
            "Install it with `pip install 'textual>=0.79'` or pass --no-tui.",
            2,
        )

    # Match typescript/src/bootstrap/state.ts: tool gating like
    # ``isTodoV2Enabled()`` reads this flag, so set it BEFORE we build the
    # registry.
    from src.bootstrap.state import set_is_interactive

    set_is_interactive(True)

    workspace_root = options.workspace_root or Path.cwd()

    # Build provider ------------------------------------------------------
    if options.provider_factory is not None:
        provider = options.provider_factory()
        provider_name = options.provider_name or getattr(
            provider, "provider_name", "unknown"
        )
    else:
        provider_name = options.provider_name or get_default_provider()
        try:
            provider_cfg = get_provider_config(provider_name)
        except Exception as exc:
            cli_error(f"error: unable to load provider config: {exc}", 2)
        if not provider_cfg.get("api_key"):
            cli_error(
                f"error: API key for provider '{provider_name}' is not configured. "
                "Run `clawcodex login` to set it up.",
                2,
            )
        provider_cls = get_provider_class(provider_name)
        model = options.model or provider_cfg.get("default_model")
        provider = provider_cls(
            api_key=provider_cfg["api_key"],
            base_url=provider_cfg.get("base_url"),
            model=model,
        )

    # Build tool registry + context --------------------------------------
    from src.tool_system.context import ToolContext
    from src.tool_system.defaults import build_default_registry

    tool_registry = build_default_registry(provider=provider)
    if options.allowed_tools:
        allow = {name.lower() for name in options.allowed_tools}
        _filter_registry(tool_registry, keep=lambda n: n.lower() in allow)
    if options.disallowed_tools:
        deny = {name.lower() for name in options.disallowed_tools}
        _filter_registry(tool_registry, keep=lambda n: n.lower() not in deny)

    tool_context = ToolContext(workspace_root=workspace_root)
    # In the TUI we *do* want tool permission prompts to show up as dialogs,
    # but until we ship ``PermissionDialog`` we default to the same
    # bypassPermissions behavior the current Rich REPL uses.
    tool_context.options.is_non_interactive_session = False

    # Build and run app ---------------------------------------------------
    from src.tui.app import ClawCodexTUI

    app = ClawCodexTUI(
        provider=provider,
        provider_name=provider_name,
        workspace_root=workspace_root,
        tool_registry=tool_registry,
        tool_context=tool_context,
        max_turns=options.max_turns,
        stream=options.stream,
    )
    try:
        # ``inline=True`` renders the app in-place at the bottom of the
        # terminal rather than grabbing the alt-screen — previous shell
        # output stays in scrollback, and ``/exit`` leaves the rendered
        # transcript intact (``inline_no_clear=True``). Matches the
        # TS / ink reference's terminal-native experience.
        # ``mouse=False`` lets the host terminal handle mouse events so
        # the user can drag-select and copy text natively. The trade-off
        # is no in-app mouse scroll on the transcript — keyboard scroll
        # bindings (PgUp/PgDn) still work.
        app.run(inline=True, inline_no_clear=True, mouse=False)
    except KeyboardInterrupt:
        return 130
    return 0


def _replay_transcript_to_host(app) -> None:
    """Dump the captured transcript to the host terminal after exit.

    Mirrors ink's non-fullscreen behaviour: when the app exits, the
    conversation the user saw stays in scrollback. Textual runs in
    the alt-screen by default which would otherwise wipe the rendered
    transcript on teardown.
    """

    snapshot = getattr(app, "exit_snapshot", None)
    if not snapshot:
        return
    try:
        from rich.console import Console

        console = Console()
        for piece in snapshot:
            try:
                console.print(piece)
            except Exception:
                continue
    except Exception:
        pass


def should_use_tui(explicit: bool | None) -> bool:
    """Decide whether to launch the Textual TUI based on flags + environment.

    The default interactive experience is the prompt_toolkit + rich REPL at
    :mod:`src.repl.core` — it matches the TS Ink reference's terminal-native
    UX (transcript flows into scrollback, only the prompt + status row are
    live, native mouse copy works). The Textual TUI is opt-in and reachable
    via ``--tui`` or ``CLAWCODEX_TUI=1`` for users who prefer the richer
    in-app experience.

    * ``explicit=True``   -> always TUI when ``textual`` is importable.
      Also enabled by ``CLAWCODEX_TUI=1``.
    * ``explicit=False``  -> never TUI. Also forced by
      ``CLAWCODEX_LEGACY_REPL=1`` (kept for back-compat).
    * ``explicit=None``   -> default to the REPL. Honor ``CLAWCODEX_TUI=1``
      from the environment so users can pin the TUI without a flag.
    """

    if explicit is False:
        return False
    if os.environ.get("CLAWCODEX_LEGACY_REPL") == "1":
        return False
    if os.environ.get("CLAWCODEX_TUI") == "0":
        return False

    env_tui = os.environ.get("CLAWCODEX_TUI") == "1"
    if not (explicit is True or env_tui):
        return False

    if not _textual_available():
        return False

    term = os.environ.get("TERM", "")
    if term == "dumb" or term == "":
        return False
    try:
        if not sys.stdout.isatty() or not sys.stdin.isatty():
            return False
    except Exception:
        return False
    return True


def _textual_available() -> bool:
    try:
        import textual  # noqa: F401
        return True
    except Exception:
        return False


def _filter_registry(registry, *, keep: Callable[[str], bool]) -> None:
    names = [t.name for t in registry.list_tools()]
    for name in names:
        if not keep(name):
            try:
                registry.unregister(name)
            except Exception:
                try:
                    del registry._tools[name]  # type: ignore[attr-defined]
                except Exception:
                    pass
