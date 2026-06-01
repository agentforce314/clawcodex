"""REPL adapter for the interactive-command :class:`UIHost` port.

Implements the surface-agnostic ``UIHost`` (``src.command_system.types``) for
the headless REPL. ``select`` renders a numbered menu and reads the choice via
the REPL's ``_safe_input`` (which already pauses the live spinner), wrapped in
``loop.run_in_executor`` so the blocking read doesn't stall the event loop the
async command path runs on. ``display`` prints to the console.

The adapter takes the ``_safe_input`` bound method and the console as plain
callables rather than the whole REPL, so it stays decoupled and trivially
testable (the tests pass a scripted ``safe_input``).
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional, Sequence

from src.command_system.types import UIOption


class ReplUIHost:
    """``UIHost`` backed by a numbered terminal menu."""

    def __init__(
        self,
        safe_input: Callable[[str], str],
        console: object = None,
    ) -> None:
        self._safe_input = safe_input
        self._console = console

    def _print(self, text: str = "") -> None:
        printer = getattr(self._console, "print", None)
        if callable(printer):
            printer(text)
        else:
            print(text)

    async def select(
        self,
        title: str,
        options: Sequence[UIOption],
        *,
        current: Optional[str] = None,
    ) -> Optional[str]:
        opts = list(options)
        if not opts:
            return None
        self._print(f"\n{title}")
        for idx, opt in enumerate(opts, start=1):
            marker = (
                " (current)"
                if current is not None and opt.value == current
                else ""
            )
            desc = f" — {opt.description}" if opt.description else ""
            self._print(f"  {idx}. {opt.label}{desc}{marker}")
        prompt = f"Select [1-{len(opts)}] (Enter to cancel): "

        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, self._safe_input, prompt)
        except (EOFError, KeyboardInterrupt):
            return None
        raw = (raw or "").strip()
        if not raw:
            return None  # empty -> cancel
        try:
            choice = int(raw)
        except ValueError:
            return None  # non-numeric -> cancel
        if choice < 1 or choice > len(opts):
            return None  # out of range -> cancel
        return opts[choice - 1].value

    async def display(self, title: str, body: str) -> None:
        self._print(f"\n{title}")
        if body:
            self._print(body)


__all__ = ["ReplUIHost"]
