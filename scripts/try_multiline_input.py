"""Drive the Rich REPL's real ``PromptSession`` with raw byte sequences
to verify the multi-line entry contract end-to-end.

We don't mock out ``prompt_toolkit``: we use its official
``create_pipe_input`` + ``DummyOutput`` harness, which is exactly how
prompt_toolkit's own test suite drives a session. Every sequence below
is the actual bytes a real terminal emits, so the results here
faithfully predict what happens when you press those keys in your WSL
terminal.

Run from repo root:
    python scripts/try_multiline_input.py
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


# ---------------------------------------------------------------------------
# Build the REAL REPL so we exercise the same bindings clawcodex ships with.
# ---------------------------------------------------------------------------
def make_repl():
    from src.repl.core import ClawcodexREPL

    mock_provider = Mock()
    mock_provider.model = "glm-4.5"

    with patch(
        "src.repl.core.get_provider_config",
        return_value={"api_key": "x", "default_model": "glm-4.5"},
    ), patch("src.repl.core.Session.create"), patch(
        "src.repl.core.get_provider_class"
    ) as gpc:
        gpc.return_value = mock_provider
        return ClawcodexREPL(provider_name="glm")


def drive(keystrokes: str) -> str:
    """Feed raw bytes into a ``PromptSession`` configured with the REPL's
    real bindings, and return the final submitted string.

    We build a fresh ``PromptSession`` per call so we can attach the pipe
    input + dummy output, but we pass in ``repl.bindings`` verbatim —
    those are the same ``KeyBindings`` object the live REPL uses. In
    other words this exercises the exact Enter / Shift+Enter /
    Meta+Enter / ``\\``+Enter logic shipping in ``clawcodex``.
    """
    repl = make_repl()
    with create_pipe_input() as pipe_input:
        pipe_input.send_text(keystrokes)
        session = PromptSession(
            key_bindings=repl.bindings,
            multiline=True,
            complete_while_typing=True,
            input=pipe_input,
            output=DummyOutput(),
        )
        try:
            result = session.prompt("❯ ")
        except EOFError:
            return "<EOF>"
    return result if result is not None else "<NO-SUBMIT>"


def show(label: str, keystrokes: str, expected: str) -> None:
    actual = drive(keystrokes)
    # Prettify output for the terminal log.
    disp = actual.replace("\n", "\\n")
    exp = expected.replace("\n", "\\n")
    ok = "PASS" if actual == expected else "FAIL"
    print(f"[{ok}] {label}")
    print(f"       keystrokes: {keystrokes!r}")
    print(f"       got:        {disp!r}")
    print(f"       expected:   {exp!r}")


# ---------------------------------------------------------------------------
# Scenarios — each corresponds to a physical key on a Windows/WSL terminal.
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[str, str, str]] = [
    # Plain Enter: the terminal sends \r. This should submit.
    ("plain Enter submits", "hello\r", "hello"),

    # Alt+Enter on Windows Terminal / VSCode / xterm:
    # the terminal sends ESC + \r, which prompt_toolkit parses as
    # (Escape, ControlM). We bind that to "insert newline".
    (
        "Alt+Enter inserts newline (Windows Terminal / VSCode / xterm)",
        "line 1\x1b\rline 2\r",
        "line 1\nline 2",
    ),

    # Portable fallback: backslash then Enter. Works on ANY terminal.
    (
        "backslash + Enter inserts newline (portable fallback)",
        "line 1\\\rline 2\r",
        "line 1\nline 2",
    ),

    # Kitty keyboard protocol Shift+Enter (CSI 13;2u).
    # Terminals: Kitty, WezTerm, Ghostty, iTerm2 (with CSI u mode).
    # We registered this in ANSI_SEQUENCES → (Escape, ControlM).
    (
        "Shift+Enter via Kitty CSI 13;2u (Kitty/WezTerm/Ghostty)",
        "line 1\x1b[13;2uline 2\r",
        "line 1\nline 2",
    ),

    # xterm modifyOtherKeys Shift+Enter (CSI 27;2;13~).
    # Terminals: VSCode with modifyOtherKeys enabled, xterm with it on.
    # Whether this inserts a newline or submits depends on the mapping.
    (
        "Shift+Enter via xterm modifyOtherKeys CSI 27;2;13~",
        "line 1\x1b[27;2;13~line 2\r",
        "line 1\nline 2",  # what we WANT; flags a gap if it doesn't.
    ),

    # Multiple backslash-Enter continuations.
    (
        "multi-line via backslash-Enter x2",
        "a\\\rb\\\rc\r",
        "a\nb\nc",
    ),

    # Mixed: Alt+Enter then backslash+Enter then plain Enter.
    (
        "mixed Alt+Enter and backslash+Enter",
        "one\x1b\rtwo\\\rthree\r",
        "one\ntwo\nthree",
    ),

    # Regression: a lone backslash in the middle of a line must NOT make
    # Enter act as a newline. Cursor is AFTER 'bar', so ``text[pos-1]``
    # is 'r', not '\\'. Expected: submits the whole literal string.
    (
        "backslash mid-line does NOT trigger newline",
        "foo\\bar\r",
        "foo\\bar",
    ),
]


def main() -> int:
    print("=" * 72)
    print("Rich REPL multi-line input — end-to-end keystroke verification")
    print("=" * 72)
    fails = 0
    for label, keys, expected in SCENARIOS:
        before = fails
        show(label, keys, expected)
        # show() already printed PASS/FAIL; recompute for counting.
        if drive(keys) != expected:
            fails = before + 1
        else:
            fails = before
        print()
    print("-" * 72)
    print(f"{len(SCENARIOS) - fails}/{len(SCENARIOS)} scenarios passed")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
