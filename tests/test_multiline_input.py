"""Unit tests for the REPL's multi-line entry keybindings.

Mirrors the contract defined by
``typescript/src/hooks/useTextInput.ts#handleEnter``:

* plain Enter          -> submit
* ``\\`` + Enter       -> remove the ``\\``, insert ``\\n`` (portable fallback)
* Meta/Alt+Enter       -> insert ``\\n``  (via Escape + ControlM)
* Kitty-protocol Shift+Enter (CSI ``13;2u``) is registered to emit the
  same two-key sequence as Meta+Enter, so it hits the same handler.

The REPL drives ``prompt_toolkit`` via real key bindings, so these tests
feed keystrokes through the session's ``KeyBindings`` object to assert
the bindings do what ``handleEnter`` documents. We don't mount a full
``PromptSession`` — we call the bound callbacks directly with a minimal
fake event, which is exactly the contract prompt_toolkit itself invokes.
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.input import ansi_escape_sequences as _ansi_seq
from prompt_toolkit.keys import Keys


class _FakeEvent:
    """Minimal stand-in for ``prompt_toolkit.key_binding.KeyPressEvent``.

    Our handlers only ever read ``current_buffer``; that's the whole
    public surface we rely on.
    """

    def __init__(self, buffer: Buffer) -> None:
        self.current_buffer = buffer


def _make_repl():
    """Construct a ClawcodexREPL with provider/session/config patched.

    We don't need real config or a live provider — only the
    ``self.bindings`` object that ``__init__`` populates. We patch at the
    boundaries the REPL actually calls, which sidesteps the on-disk
    config manager entirely.
    """

    from src.repl.core import ClawcodexREPL

    mock_provider = Mock()
    mock_provider.model = "glm-4.5"

    with patch(
        "src.repl.core.get_provider_config",
        return_value={"api_key": "x", "default_model": "glm-4.5"},
    ), patch("src.repl.core.Session.create"), patch(
        "src.repl.core.get_provider_class"
    ) as mock_provider_class:
        mock_provider_class.return_value = mock_provider
        return ClawcodexREPL(provider_name="glm")


def _find_binding(bindings, *keys):
    """Look up the handler registered for a given keystroke sequence."""

    for b in bindings.bindings:
        if tuple(b.keys) == tuple(keys):
            return b.handler
    raise AssertionError(f"no binding found for {keys!r}")


class TestMultilineEntryBindings(unittest.TestCase):
    def setUp(self):
        self.repl = _make_repl()

    # ----- plain Enter -----
    def test_plain_enter_submits_buffer(self):
        """``handleEnter`` without modifiers or trailing ``\\`` calls onSubmit."""
        handler = _find_binding(self.repl.bindings, Keys.ControlM)
        buf = Buffer()
        buf.text = "hello"
        buf.cursor_position = len(buf.text)
        buf.validate_and_handle = Mock()
        handler(_FakeEvent(buf))
        buf.validate_and_handle.assert_called_once()
        # Buffer text must be left alone on submit.
        self.assertEqual(buf.text, "hello")

    def test_plain_enter_accepts_open_completion(self):
        """If a completion popup is open, Enter closes it instead of submitting."""
        handler = _find_binding(self.repl.bindings, Keys.ControlM)
        buf = Buffer()
        buf.text = "/hel"
        buf.cursor_position = len(buf.text)
        buf.complete_state = object()  # non-None == popup open
        buf.validate_and_handle = Mock()
        handler(_FakeEvent(buf))
        self.assertIsNone(buf.complete_state)
        buf.validate_and_handle.assert_not_called()

    # ----- backslash + Enter -----
    def test_backslash_enter_inserts_newline_and_removes_backslash(self):
        """The portable fallback: trailing ``\\`` + Enter -> newline."""
        handler = _find_binding(self.repl.bindings, Keys.ControlM)
        buf = Buffer()
        buf.text = "first line\\"
        buf.cursor_position = len(buf.text)
        buf.validate_and_handle = Mock()
        handler(_FakeEvent(buf))
        # Backslash consumed, real newline in its place.
        self.assertEqual(buf.text, "first line\n")
        self.assertEqual(buf.cursor_position, len(buf.text))
        buf.validate_and_handle.assert_not_called()

    def test_backslash_only_triggers_when_cursor_is_after_backslash(self):
        """A backslash elsewhere in the line must not turn Enter into newline."""
        handler = _find_binding(self.repl.bindings, Keys.ControlM)
        buf = Buffer()
        buf.text = "a\\b"
        buf.cursor_position = len(buf.text)  # cursor after 'b', not after '\'
        buf.validate_and_handle = Mock()
        handler(_FakeEvent(buf))
        buf.validate_and_handle.assert_called_once()
        self.assertEqual(buf.text, "a\\b")

    # ----- Meta+Enter / Shift+Enter -----
    def test_meta_enter_inserts_newline(self):
        """Escape+ControlM (Meta+Enter / Alt+Enter) -> insert ``\\n``."""
        handler = _find_binding(self.repl.bindings, Keys.Escape, Keys.ControlM)
        buf = Buffer()
        buf.text = "first"
        buf.cursor_position = len(buf.text)
        handler(_FakeEvent(buf))
        self.assertEqual(buf.text, "first\n")
        self.assertEqual(buf.cursor_position, len(buf.text))

    # ----- Kitty-protocol Shift+Enter -----
    def test_kitty_shift_enter_sequence_is_registered(self):
        """``\\x1b[13;2u`` must be mapped so Kitty/WezTerm/Ghostty Shift+Enter
        lands on the Meta+Enter binding."""
        mapped = _ansi_seq.ANSI_SEQUENCES.get("\x1b[13;2u")
        self.assertEqual(mapped, (Keys.Escape, Keys.ControlM))

    def test_xterm_modify_other_keys_shift_enter_sequence_is_registered(self):
        """``\\x1b[27;2;13~`` (xterm ``modifyOtherKeys`` Shift+Enter) must be
        rebound to (Escape, ControlM). prompt_toolkit's default maps it to
        plain ``ControlM``, which would make it indistinguishable from
        Enter — so Shift+Enter in xterm/VSCode with modifyOtherKeys would
        submit instead of inserting a newline."""
        mapped = _ansi_seq.ANSI_SEQUENCES.get("\x1b[27;2;13~")
        self.assertEqual(mapped, (Keys.Escape, Keys.ControlM))


class TestMultilineSlashCommandRemoved(unittest.TestCase):
    """``/multiline`` is no longer a command — its behavior is now the
    default entry mode, exactly as in the TS reference."""

    def setUp(self):
        self.repl = _make_repl()

    def test_repl_has_no_multiline_mode_state(self):
        self.assertFalse(hasattr(self.repl, "multiline_mode"))

    def test_multiline_is_not_listed_as_a_built_in(self):
        self.assertNotIn("/multiline", self.repl._built_in_commands)


if __name__ == "__main__":
    unittest.main()
