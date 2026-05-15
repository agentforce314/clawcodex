"""Pin the ``ToolContext.abort_controller`` non-optional contract.

Previously the field defaulted to ``None`` and every reader had to
defensively guard with ``getattr(..., None)`` or ``ctrl and …``. The
"field is None" hazard class is what allowed the original ESC-into-
subagent bug to slip through: the bridge was supposed to plumb the
field but forgot, and silently every downstream tool ran a fresh
disconnected controller.

The dataclass factory now installs an untripped ``AbortController`` on
every fresh context, so readers can drop the defensive checks and a
forgotten plumbing call no longer regresses ESC propagation into a
silent "the controller is None" landmine.
"""
from __future__ import annotations

from pathlib import Path

from src.tool_system.context import ToolContext
from src.utils.abort_controller import AbortController


def test_fresh_context_has_default_abort_controller(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)

    # The field is populated by the dataclass factory, not None.
    assert ctx.abort_controller is not None
    assert isinstance(ctx.abort_controller, AbortController)
    # Default controller is untripped — readers can dispatch tools
    # without worrying about an accidental "already aborted" state.
    assert ctx.abort_controller.signal.aborted is False


def test_each_context_gets_its_own_controller(tmp_path: Path) -> None:
    """Two contexts must NOT share the same default controller instance.

    A shared default would let one context's abort silently cascade into
    another — a class of action-at-a-distance bug we deliberately avoid
    by using ``default_factory`` instead of ``default=AbortController()``.
    """
    a = ToolContext(workspace_root=tmp_path)
    b = ToolContext(workspace_root=tmp_path)
    assert a.abort_controller is not b.abort_controller

    a.abort_controller.abort("a-only")
    assert a.abort_controller.signal.aborted is True
    assert b.abort_controller.signal.aborted is False


def test_explicit_controller_overrides_default(tmp_path: Path) -> None:
    """Callers that pass their own controller still win."""
    explicit = AbortController()
    ctx = ToolContext(workspace_root=tmp_path, abort_controller=explicit)
    assert ctx.abort_controller is explicit
