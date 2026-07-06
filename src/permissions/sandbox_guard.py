"""Sandbox availability guard (C8 — the silent-unsandboxed footgun).

The port does NOT implement sandbox ENFORCEMENT: TS wraps the external
``@anthropic-ai/sandbox-runtime`` (macOS Seatbelt / Linux bubblewrap) via
``SandboxManager``; the port has only the decision helper
(``bash_security.should_sandbox_command``, currently unwired) and runs a bare
``subprocess.Popen`` (bash_tool.py). So in the port the sandbox is permanently
UNAVAILABLE.

That is not a divergence to hide — it maps exactly onto TS's OWN documented
sandbox-unavailable path (``entrypoints/sandboxTypes.ts:96-103``): when
``sandbox.enabled`` is true but the sandbox cannot start,

  * ``failIfUnavailable: true``  → "Exit with an error at startup" (a
    managed-settings HARD GATE — sandboxing is required, so running
    unsandboxed is a refusal, not a warning), and
  * ``failIfUnavailable: false`` (default) → "a warning is shown and commands
    run unsandboxed".

This module surfaces that guard so a user who asked for a sandbox is never
silently given an unsandboxed shell. Actual enforcement (a native
``sandbox-exec``/``bwrap`` engine) is the deferred
**sandbox-native-enforcement** sub-chapter.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_UNSANDBOXED_WARNING = (
    "settings.sandbox.enabled is true, but this build does not implement "
    "sandbox enforcement — commands run UNSANDBOXED. Set "
    "sandbox.failIfUnavailable=true to make this a hard startup error instead, "
    "or unset sandbox.enabled to silence this warning."
)

_HARD_GATE_ERROR = (
    "settings.sandbox.enabled and sandbox.failIfUnavailable are both true, but "
    "this build does not implement sandbox enforcement, so the sandbox cannot "
    "start. Refusing to run unsandboxed (the managed-settings hard gate). "
    "Unset sandbox.failIfUnavailable to fall back to a warning + unsandboxed "
    "execution."
)


def _sandbox(settings: Any) -> Any | None:
    return getattr(settings, "sandbox", None)


def is_sandbox_requested(settings: Any) -> bool:
    sb = _sandbox(settings)
    return bool(sb is not None and getattr(sb, "enabled", False))


def sandbox_hard_gate_error(settings: Any) -> str | None:
    """The hard-gate message when the user REQUIRES a sandbox
    (``enabled`` + ``failIfUnavailable``) that the port cannot provide, else
    ``None``. Callers must refuse to proceed when this is non-None."""
    sb = _sandbox(settings)
    if sb is not None and getattr(sb, "enabled", False) and getattr(sb, "fail_if_unavailable", False):
        return _HARD_GATE_ERROR
    return None


def sandbox_unsandboxed_warning(settings: Any) -> str | None:
    """The warning message when the user ASKED for a sandbox but it is
    unavailable and NOT required (``enabled`` + not ``failIfUnavailable``),
    else ``None``."""
    sb = _sandbox(settings)
    if sb is not None and getattr(sb, "enabled", False) and not getattr(sb, "fail_if_unavailable", False):
        return _UNSANDBOXED_WARNING
    return None


_warned_once = False


def warn_if_unsandboxed_once(settings: Any) -> None:
    """Emit the unsandboxed warning at most once per process (so bash-per-call
    wiring doesn't spam). The hard-gate case is handled separately by callers
    that must refuse."""
    global _warned_once
    if _warned_once:
        return
    msg = sandbox_unsandboxed_warning(settings)
    if msg:
        logger.warning("[sandbox] %s", msg)
        _warned_once = True
