"""Tests for the ``/export`` command (Phase 4 P3 — port of TS local-jsx).

Ports the behavior of ``typescript/src/commands/export/`` (``export.tsx`` +
``ExportDialog.tsx``) onto the interactive-command bridge. Mirrors the
permissions/output-style test layout (``tests/test_interactive_bridge.py``,
``tests/test_output_style_command.py``):

  * Registration in builtins + aggregator; metadata (INTERACTIVE, name, desc).
  * Bridge-safety **by type** (``is_bridge_safe_command`` False) + the TUI
    direct-dispatch table falling through to the async registry arm.
  * **Args path on ``NullUIHost``** — the headless keystone: ``/export out.json``
    renders + writes the file and returns success *without ever touching*
    ``ctx.ui`` (proven by using ``NullUIHost``, whose ``select``/``prompt_text``
    raise).
  * **Wizard path** via a scripted ``FakeUIHost`` — ``select`` format then
    ``prompt_text`` filename: happy path writes the file; cancel at either step
    → ``skip``. (No clipboard/method step this phase — plan §4.5.)
  * Empty/absent conversation → graceful "No conversation to export." (no raise).
  * Error path (unwritable target) → failure message (no raise).
  * Port-fidelity unit tests for the private helpers (``format_timestamp`` /
    ``extract_first_prompt`` / ``sanitize_filename``).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.command_system import (
    EXPORT_COMMAND,
    ExportCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.export_command import (
    extract_first_prompt,
    format_timestamp,
    sanitize_filename,
)
from src.command_system.registry import CommandRegistry
from src.command_system.types import (
    CommandType,
    InteractiveOutcome,
    InteractiveUnavailableError,
    NullUIHost,
)


# --------------------------------------------------------------------------- #
# Fakes / fixtures
# --------------------------------------------------------------------------- #
class FakeUIHost:
    """Scripted UI surface recording ``select`` / ``prompt_text`` calls.

    ``pick`` is returned by ``select`` (``None`` = cancel); ``text`` by
    ``prompt_text`` (``None`` = cancel, ``''`` = empty-but-valid submit).
    """

    def __init__(self, *, pick: str | None = None, text: str | None = None) -> None:
        self._pick = pick
        self._text = text
        self.select_calls: list[dict] = []
        self.prompt_calls: list[dict] = []
        self.display_calls: list[tuple[str, str]] = []

    async def select(self, title, options, *, current=None):
        self.select_calls.append(
            {"title": title, "values": [o.value for o in options], "current": current}
        )
        return self._pick

    async def prompt_text(self, title, *, default="", placeholder=None):
        self.prompt_calls.append(
            {"title": title, "default": default, "placeholder": placeholder}
        )
        return self._text

    async def display(self, title, body):
        self.display_calls.append((title, body))


def _conversation(*messages):
    """A minimal conversation object exposing ``.messages`` (the only attr the
    command reads)."""
    return SimpleNamespace(messages=list(messages))


def _user_msg(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _ctx(tmp_path: Path, *, conversation=None, ui=None):
    return create_command_context(
        workspace_root=tmp_path, cwd=tmp_path, conversation=conversation, ui=ui
    )


def _registry_with(*commands) -> CommandRegistry:
    reg = CommandRegistry()
    for c in commands:
        reg.register(c)
    return reg


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_export_registered_in_builtins_and_aggregator():
    assert "export" in {c.name for c in get_builtin_commands()}
    assert "export" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_export_metadata_mirrors_ts():
    assert isinstance(EXPORT_COMMAND, ExportCommand)
    assert EXPORT_COMMAND.name == "export"
    # Verbatim from typescript/src/commands/export/index.ts.
    assert EXPORT_COMMAND.description == (
        "Export the current conversation to a file or clipboard"
    )
    assert EXPORT_COMMAND.argument_hint == "[filename]"
    # local-jsx -> INTERACTIVE (so the remote-safety gate blocks it by type).
    assert EXPORT_COMMAND.command_type == CommandType.INTERACTIVE
    # TS sets only type/name/description/argumentHint; everything else defaults.
    assert EXPORT_COMMAND.is_hidden is False
    assert EXPORT_COMMAND.disable_model_invocation is False
    assert EXPORT_COMMAND.user_invocable is True


# --------------------------------------------------------------------------- #
# B. Bridge-safety BY TYPE + TUI dispatch fall-through
# --------------------------------------------------------------------------- #
def test_export_blocked_from_remote_by_type():
    # INTERACTIVE commands are never bridge-safe (mirrors TS local-jsx).
    assert is_bridge_safe_command(EXPORT_COMMAND) is False


def test_dispatch_local_command_falls_through_for_export():
    # The TUI's direct-dispatch table must NOT claim /export; it has to fall
    # through to the async registry path where the INTERACTIVE arm lives.
    from src.tui.commands import dispatch_local_command

    res = dispatch_local_command(
        "/export", session=None, workspace_root=Path("."), tool_registry=None
    )
    assert res.handled is False


# --------------------------------------------------------------------------- #
# C. Args path — headless render + write, never touches ctx.ui
# --------------------------------------------------------------------------- #
async def test_args_path_writes_file_on_null_surface(tmp_path):
    # The headless keystone: NullUIHost.select/prompt_text raise, so a green
    # write here proves the args path never reaches for ctx.ui.
    conv = _conversation(_user_msg("hello world"))
    outcome = await EXPORT_COMMAND.run("out.json", _ctx(tmp_path, conversation=conv, ui=NullUIHost()))

    assert isinstance(outcome, InteractiveOutcome)
    assert outcome.display == "system"
    target = tmp_path / "out.json"
    assert outcome.message == f"Conversation exported to: {target}"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["format"] == "json"
    assert data["messageCount"] == 1


async def test_args_path_infers_format_from_extension(tmp_path):
    conv = _conversation(_user_msg("hi"))
    outcome = await EXPORT_COMMAND.run("notes.md", _ctx(tmp_path, conversation=conv, ui=NullUIHost()))

    target = tmp_path / "notes.md"
    assert target.exists()
    assert outcome.message == f"Conversation exported to: {target}"
    # Markdown render, not JSON.
    assert not target.read_text(encoding="utf-8").lstrip().startswith("{")


async def test_args_path_format_flag_overrides_extension(tmp_path):
    # --format json wins over the .txt extension; the file is rewritten to .json.
    conv = _conversation(_user_msg("hi"))
    outcome = await EXPORT_COMMAND.run("transcript.txt --format json", _ctx(tmp_path, conversation=conv, ui=NullUIHost()))

    target = tmp_path / "transcript.json"
    assert target.exists()
    assert not (tmp_path / "transcript.txt").exists()
    assert outcome.message == f"Conversation exported to: {target}"
    json.loads(target.read_text(encoding="utf-8"))  # valid JSON


async def test_args_path_parse_error_is_reported(tmp_path):
    conv = _conversation(_user_msg("hi"))
    outcome = await EXPORT_COMMAND.run("--format xml out", _ctx(tmp_path, conversation=conv, ui=NullUIHost()))

    assert outcome.display == "system"
    assert outcome.message is not None
    assert "Unsupported export format: xml" in outcome.message


async def test_args_path_write_failure_returns_message_no_raise(tmp_path):
    # Unwritable target (parent dir doesn't exist) -> failure outcome, no raise.
    conv = _conversation(_user_msg("hi"))
    bad = tmp_path / "missing" / "sub" / "out.json"
    outcome = await EXPORT_COMMAND.run(str(bad), _ctx(tmp_path, conversation=conv, ui=NullUIHost()))

    assert outcome.display == "system"
    assert outcome.message is not None
    assert outcome.message.startswith("Failed to export conversation:")
    assert not bad.exists()


# --------------------------------------------------------------------------- #
# D. Wizard path — select format -> prompt_text filename
# --------------------------------------------------------------------------- #
async def test_wizard_happy_path_writes_file(tmp_path):
    conv = _conversation(_user_msg("Hi there"))
    ui = FakeUIHost(pick="markdown", text="mychat")
    outcome = await EXPORT_COMMAND.run("", _ctx(tmp_path, conversation=conv, ui=ui))

    target = tmp_path / "mychat.md"
    assert target.exists()
    assert outcome.message == f"Conversation exported to: {target}"
    assert outcome.display == "system"

    # The format select offered the three formats and seeded current=text.
    assert ui.select_calls[0]["values"] == ["text", "markdown", "json"]
    assert ui.select_calls[0]["current"] == "text"
    # The filename prompt's default carried the chosen (.md) extension and the
    # sanitized first-prompt slug + a timestamp.
    default = ui.prompt_calls[0]["default"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}-hi-there\.md", default)


async def test_wizard_format_flag_seeds_select_current(tmp_path):
    # --format with no filename runs the wizard, but the resolved format seeds
    # the select's `current` (export.tsx:60-63 feeds the dialog's initial
    # format). Proves the flag isn't silently dropped on the no-filename path.
    conv = _conversation(_user_msg("hi"))
    ui = FakeUIHost(pick="markdown", text="out")
    await EXPORT_COMMAND.run("--format markdown", _ctx(tmp_path, conversation=conv, ui=ui))

    assert ui.select_calls[0]["current"] == "markdown"


async def test_wizard_default_filename_falls_back_to_conversation_prefix(tmp_path):
    # No extractable first prompt -> conversation-<ts>.<ext> default.
    conv = _conversation({"type": "assistant", "message": {"role": "assistant", "content": "hi"}})
    ui = FakeUIHost(pick="json", text="dump")
    await EXPORT_COMMAND.run("", _ctx(tmp_path, conversation=conv, ui=ui))

    default = ui.prompt_calls[0]["default"]
    assert re.fullmatch(r"conversation-\d{4}-\d{2}-\d{2}-\d{6}\.json", default)


async def test_wizard_cancel_at_format_returns_skip(tmp_path):
    conv = _conversation(_user_msg("hi"))
    ui = FakeUIHost(pick=None)
    outcome = await EXPORT_COMMAND.run("", _ctx(tmp_path, conversation=conv, ui=ui))

    assert outcome.display == "skip"
    assert outcome.message is None
    # Cancelled before reaching the filename prompt; no file written.
    assert ui.prompt_calls == []
    assert list(tmp_path.iterdir()) == []


async def test_wizard_cancel_at_filename_returns_skip(tmp_path):
    conv = _conversation(_user_msg("hi"))
    ui = FakeUIHost(pick="json", text=None)  # Esc at the filename prompt
    outcome = await EXPORT_COMMAND.run("", _ctx(tmp_path, conversation=conv, ui=ui))

    assert outcome.display == "skip"
    assert outcome.message is None
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# E. No-conversation graceful degradation
# --------------------------------------------------------------------------- #
async def test_no_conversation_is_graceful(tmp_path):
    # conversation=None -> hasattr(None, "messages") is False -> graceful.
    outcome = await EXPORT_COMMAND.run("out.json", _ctx(tmp_path, conversation=None, ui=NullUIHost()))
    assert outcome.display == "system"
    assert outcome.message == "No conversation to export."
    assert not (tmp_path / "out.json").exists()


async def test_no_conversation_graceful_even_in_wizard(tmp_path):
    # Absent messages short-circuits before the wizard touches ctx.ui.
    ui = FakeUIHost(pick="json", text="x")
    outcome = await EXPORT_COMMAND.run("", _ctx(tmp_path, conversation=None, ui=ui))
    assert outcome.message == "No conversation to export."
    assert ui.select_calls == []


# --------------------------------------------------------------------------- #
# F. Engine INTERACTIVE arm — args path reachable on a null surface
# --------------------------------------------------------------------------- #
async def test_engine_routes_args_path_to_text_on_null_surface(tmp_path):
    # /export <file> with no UI wired: the engine substitutes NullUIHost, but
    # the args path never calls it -> clean text result (mirrors output-style).
    conv = _conversation(_user_msg("hi"))
    reg = _registry_with(EXPORT_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path, conversation=conv)
    assert ctx.ui is None
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/export out.json")

    assert result.success is True
    assert result.result_type == "text"
    assert result.display == "system"
    assert (tmp_path / "out.json").exists()


async def test_engine_wizard_errors_cleanly_on_null_surface(tmp_path):
    # No-args wizard with no UI wired: the engine's NullUIHost makes select
    # raise InteractiveUnavailableError, which the engine turns into a clean
    # error result (not a crash).
    conv = _conversation(_user_msg("hi"))
    reg = _registry_with(EXPORT_COMMAND)
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path, conversation=conv)
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)

    result = await eng.execute("/export")

    assert result.success is False
    assert result.error is not None


async def test_run_directly_raises_on_null_surface_for_wizard(tmp_path):
    # Calling run() directly (no engine substitution) with NullUIHost: the
    # wizard's select must raise rather than fake a pick.
    conv = _conversation(_user_msg("hi"))
    with pytest.raises(InteractiveUnavailableError):
        await EXPORT_COMMAND.run("", _ctx(tmp_path, conversation=conv, ui=NullUIHost()))


# --------------------------------------------------------------------------- #
# G. Helper port-fidelity (export.tsx:11-49)
# --------------------------------------------------------------------------- #
class TestFormatTimestamp:
    def test_zero_pads_all_fields(self):
        assert format_timestamp(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02-030405"

    def test_full_width_fields(self):
        assert format_timestamp(datetime(2026, 12, 31, 23, 59, 59)) == "2026-12-31-235959"


class TestExtractFirstPrompt:
    def test_string_content_first_line_trimmed(self):
        # TS trims the *whole* string first, then takes the first line
        # (export.tsx:27-37): leading whitespace and a trailing tail are
        # stripped, leaving the first line clean.
        msgs = [{"type": "user", "message": {"role": "user", "content": "  first line\nsecond  "}}]
        assert extract_first_prompt(msgs) == "first line"

    def test_array_content_uses_first_text_block(self):
        msgs = [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {}},
                        {"type": "text", "text": "  hello there  "},
                    ],
                },
            }
        ]
        assert extract_first_prompt(msgs) == "hello there"

    def test_skips_non_user_messages(self):
        msgs = [
            {"type": "assistant", "message": {"role": "assistant", "content": "ignored"}},
            {"type": "user", "message": {"role": "user", "content": "the prompt"}},
        ]
        assert extract_first_prompt(msgs) == "the prompt"

    def test_returns_empty_when_no_user_message(self):
        assert extract_first_prompt([]) == ""
        assert extract_first_prompt([{"type": "assistant", "message": {"role": "assistant", "content": "x"}}]) == ""

    def test_truncates_to_50_chars_with_ellipsis(self):
        long = "x" * 60
        result = extract_first_prompt([{"type": "user", "message": {"role": "user", "content": long}}])
        assert result == "x" * 49 + "…"
        assert len(result) == 50


class TestSanitizeFilename:
    def test_lowercases_and_hyphenates(self):
        assert sanitize_filename("Hello World") == "hello-world"

    def test_strips_special_characters(self):
        assert sanitize_filename("Hello, World!!") == "hello-world"

    def test_collapses_repeated_hyphens_and_whitespace(self):
        assert sanitize_filename("a   b---c") == "a-b-c"

    def test_trims_leading_and_trailing_hyphens(self):
        assert sanitize_filename("  -leading and trailing-  ") == "leading-and-trailing"

    def test_empty_after_sanitize(self):
        assert sanitize_filename("!!!") == ""
