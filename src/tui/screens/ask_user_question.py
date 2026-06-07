"""Facade — tui/screens/ask_user_question.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.tui.screens.ask_user_question import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.tui.screens.ask_user_question`` directly.
"""

from clawcodex_ext.tui.screens.ask_user_question import (  # noqa: F401
    AskUserQuestionModal,
)

__all__ = [
    "AskUserQuestionModal",
]
