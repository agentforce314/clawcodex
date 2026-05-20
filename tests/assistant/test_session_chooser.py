"""Tests for ``src.assistant.session_chooser``.

The chooser is a stub-mirror of the TS React component (which is itself
a stub upstream — see assistant-gap-analysis.md §2.2). These tests pin
the stub contract so a future implementation lands without breaking
the call shape.
"""

from __future__ import annotations

from src.assistant.session_chooser import AssistantSessionChooser


def test_stub_returns_none():
    result = AssistantSessionChooser(
        sessions=[],
        on_select=lambda _id: None,
        on_cancel=lambda: None,
    )
    assert result is None


def test_callbacks_are_not_invoked():
    select_calls: list[str] = []
    cancel_calls: list[None] = []

    def on_select(session_id: str) -> None:
        select_calls.append(session_id)

    def on_cancel() -> None:
        cancel_calls.append(None)

    AssistantSessionChooser(
        sessions=[{'id': 'sess_1'}, {'id': 'sess_2'}],
        on_select=on_select,
        on_cancel=on_cancel,
    )

    # Stub must not invoke either callback.
    assert select_calls == []
    assert cancel_calls == []
