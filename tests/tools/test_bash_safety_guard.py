"""Pre-spawn bash safety guard: token boundaries + bypass-mode gating.

Both behaviours were found by a terminal-bench 2.1 differential (2026-08-02):
``qemu-system-x86_64 … -no-reboot`` was refused as "potentially dangerous"
because ``\\breboot\\b`` matches inside the flag, and the refusal stood even
under ``--dangerously-skip-permissions``.
"""

from __future__ import annotations

import pytest

from src.tool_system.errors import ToolPermissionError
from src.tool_system.tools.bash.bash_tool import bash_command_safety_guard


class _PermCtx:
    def __init__(self, mode: str, bypass_available: bool = False) -> None:
        self.mode = mode
        self.is_bypass_permissions_mode_available = bypass_available


class _Ctx:
    def __init__(self, mode: str, bypass_available: bool = False) -> None:
        self.permission_context = _PermCtx(mode, bypass_available)


def _bypass() -> _Ctx:
    """What ``--dangerously-skip-permissions`` actually resolves to."""
    return _Ctx("bypassPermissions", bypass_available=True)


# --- the flags that must stop being refused -------------------------------

# Real commands the agent legitimately needs. Each previously matched a
# hardcoded pattern through a hyphen.
ALLOWED = [
    # the exact command terminal-bench's qemu-startup was refused on
    "qemu-system-x86_64 -name alpine -m 1024 -cdrom /app/alpine.iso "
    "-boot d -display none -no-reboot -daemonize",
    "qemu-system-x86_64 -no-reboot -no-shutdown",
    "qemu-system-x86_64 --no-reboot",
    "systemctl list-units --no-reboot-check",
    "./configure --enable-reboot-tests",
    "echo reboot-helper",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_hyphenated_neighbour_is_not_a_bare_command(command: str) -> None:
    bash_command_safety_guard(command)


# --- what must STILL be refused -------------------------------------------

REFUSED = [
    "reboot",
    "reboot -f",
    "/sbin/reboot",
    "sudo rm -f /etc/passwd",
    "shutdown -h now",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    "rm -rf / ",
    "rm -rf /",
    ":(){ :|:& };:",
    # still caught when it is one clause of a compound command
    "cd /tmp && sudo make install",
]


@pytest.mark.parametrize("command", REFUSED)
def test_real_dangerous_commands_still_refused(command: str) -> None:
    with pytest.raises(ToolPermissionError):
        bash_command_safety_guard(command)


def test_uppercase_still_refused() -> None:
    with pytest.raises(ToolPermissionError):
        bash_command_safety_guard("SUDO rm -rf /etc")


# --- bypass-mode gating ---------------------------------------------------


def test_bypass_permissions_skips_the_blocklist() -> None:
    bash_command_safety_guard("sudo apt-get install -y qemu", _bypass())


def test_plan_mode_with_bypass_available_skips_the_blocklist() -> None:
    bash_command_safety_guard("reboot", _Ctx("plan", bypass_available=True))


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan"])
def test_non_bypass_modes_still_refuse(mode: str) -> None:
    with pytest.raises(ToolPermissionError):
        bash_command_safety_guard("sudo rm -rf /etc", _Ctx(mode))


def test_bypass_mode_without_availability_still_refuses() -> None:
    """The load-bearing half of the predicate.

    ``ToolContext.permission_context`` DEFAULTS to
    ``ToolPermissionContext(mode="bypassPermissions")`` while leaving
    ``is_bypass_permissions_mode_available`` False. Keying the skip on mode
    alone would therefore disarm the blocklist for every default-constructed
    context — mcp_serve, tests, any caller that never considered permissions.
    Only a session that actually resolved the posture sets both.
    """
    with pytest.raises(ToolPermissionError):
        bash_command_safety_guard("sudo rm -rf /etc", _Ctx("bypassPermissions"))


def test_real_default_tool_context_still_refuses() -> None:
    """Pin the above against the REAL dataclass, not a stub."""
    from pathlib import Path

    from src.tool_system.context import ToolContext

    ctx = ToolContext(workspace_root=Path("."))
    assert ctx.permission_context.mode == "bypassPermissions"
    with pytest.raises(ToolPermissionError):
        bash_command_safety_guard("sudo rm -rf /etc", ctx)


def test_missing_context_fails_closed() -> None:
    """No context at all must keep guarding — the pre-fix behaviour."""
    with pytest.raises(ToolPermissionError):
        bash_command_safety_guard("sudo rm -rf /etc")
    with pytest.raises(ToolPermissionError):
        bash_command_safety_guard("sudo rm -rf /etc", object())
