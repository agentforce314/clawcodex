"""Parity test: legacy import paths still resolve to the expected callables.

Phases 0-12 of the ch13 refactor moved several modules around (notably
the ``at_file_completer`` move in Phase 3). The legacy import paths
must remain importable for at least one release cycle so external
embedders aren't broken silently.

These imports are end-to-end: any future rename at the moved location
that doesn't propagate to the shim breaks this test loudly.
"""

from __future__ import annotations


def test_legacy_at_file_completer_path_imports() -> None:
    from src.repl.at_file_completer import AtFileCompleter

    # Smoke: the class is constructible.
    completer = AtFileCompleter()
    assert completer is not None


def test_legacy_at_file_completer_private_helpers_importable() -> None:
    """Tests reach into private helpers — the shim must re-export them."""

    from src.repl.at_file_completer import (  # noqa: F401
        _filter_candidates,
        _is_path_like_token,
        _path_completions,
        _subsequence_score,
    )


def test_legacy_repl_core_importable() -> None:
    """The legacy REPL is the *default* — its core class import must work."""

    from src.repl.core import ClawcodexREPL  # noqa: F401


def test_legacy_replLauncher_module_importable() -> None:
    """``src/replLauncher.py`` is a parity-by-filename module; importing it
    should not crash on any of the docstring/banner cleanup edits."""

    from src.replLauncher import build_repl_banner, launch_repl  # noqa: F401

    # Smoke: ``build_repl_banner`` returns a non-empty string. The
    # banner content (Textual-default vs. legacy-default) is a
    # Phase-0 concern; this parity test only pins the import surface.
    banner = build_repl_banner()
    assert isinstance(banner, str) and banner


def test_keybindings_legacy_chord_tracker_imports() -> None:
    """The legacy chord-tracker module is consumed by tests; verify the
    Phase-2 refactor didn't drop the public surface."""

    from src.tui.keybindings import (  # noqa: F401
        ChordBinding,
        ChordTracker,
        default_bindings,
        make_default_tracker,
        make_tracker_from_entries,
    )


def test_outputStyles_legacy_top_level_imports() -> None:
    """``BUILTIN_OUTPUT_STYLES`` and ``OutputStyle`` are imported by
    legacy callers via ``src.outputStyles.styles``."""

    from src.outputStyles.styles import BUILTIN_OUTPUT_STYLES, OutputStyle

    assert "default" in BUILTIN_OUTPUT_STYLES
    assert OutputStyle("x", "p").name == "x"


def test_phase3_subcomponents_importable_from_widgets() -> None:
    """Phase-3 widgets land under the ``widgets/`` namespace; smoke-import
    so a future rename surfaces loudly."""

    from src.tui.widgets.prompt_input_footer import PromptInputFooter  # noqa: F401
    from src.tui.widgets.prompt_input_help_menu import PromptInputHelpMenu  # noqa: F401
    from src.tui.widgets.prompt_input_mode_indicator import (  # noqa: F401
        PromptInputModeIndicator,
    )
    from src.tui.widgets.prompt_input_queued_commands import (  # noqa: F401
        PromptInputQueuedCommands,
    )
    from src.tui.widgets.prompt_input_stash_notice import (  # noqa: F401
        PromptInputStashNotice,
    )


def test_phase4_vim_modules_importable() -> None:
    from src.tui.vim_buffer import Cursor, Range, VimBuffer  # noqa: F401
    from src.tui.vim_text_objects import find_text_object  # noqa: F401
    from src.tui.vim_operators import (  # noqa: F401
        ParsedOperator,
        apply_operator,
        parse_operator_motion,
        resolve_target_range,
    )
    from src.tui.vim_visual import VisualMode, VisualSelection, VisualState  # noqa: F401
    from src.tui.vim_search import VimSearchState, find_next  # noqa: F401


def test_phase5_to_phase12_modules_importable() -> None:
    from src.tui.widgets.transcript_search import (  # noqa: F401
        TranscriptSearch,
        find_matches,
    )
    from src.tui.declared_cursor import (  # noqa: F401
        DeclaredCursor,
        publish_cursor_position,
    )
    from src.tui.hyperlinks import (  # noqa: F401
        format_link,
        is_hyperlink_supported,
    )
    from src.tui.frame_metrics import (  # noqa: F401
        FrameEvent,
        emit_frame_event,
        register_frame_observer,
    )
    from src.tui.widgets.messages.assistant_thinking import (  # noqa: F401
        AssistantThinkingMessage,
    )
    from src.tui.screens.resume_conversation import ResumeConversation  # noqa: F401
    from src.tui.screens.doctor import DoctorScreen  # noqa: F401
