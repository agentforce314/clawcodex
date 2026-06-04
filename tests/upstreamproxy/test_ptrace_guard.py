"""Tests for ``src.upstreamproxy.ptrace_guard``.

Most tests are platform-gated. The actual prctl invocation only runs on
Linux (gated by ``@pytest.mark.linux_only``); on macOS/Windows we
verify the no-op return.
"""

from __future__ import annotations

import platform

import pytest

from src.upstreamproxy.ptrace_guard import PR_SET_DUMPABLE, set_non_dumpable


def test_pr_set_dumpable_constant() -> None:
    """Operation code from <linux/prctl.h> is 4."""
    assert PR_SET_DUMPABLE == 4


def test_non_linux_returns_false() -> None:
    """On macOS/Windows, set_non_dumpable is a no-op returning False."""
    if platform.system() == 'Linux':
        pytest.skip('platform-specific test for non-Linux')
    assert set_non_dumpable() is False


@pytest.mark.linux_only
def test_linux_returns_true() -> None:
    """On Linux, prctl(PR_SET_DUMPABLE, 0) succeeds."""
    if platform.system() != 'Linux':
        pytest.skip('Linux-only test')
    assert set_non_dumpable() is True


def test_no_exception_on_failure() -> None:
    """Whatever the underlying call does, set_non_dumpable must NEVER raise.

    Per chapter "Apply This" rule #5 (fail-open semantics).
    """
    # Just call it; no platform-specific expectation. The only requirement
    # is that the call returns rather than raising.
    result = set_non_dumpable()
    assert isinstance(result, bool)
