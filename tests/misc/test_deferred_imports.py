"""WI-4.4 regression tests — heavy SDK imports must not load on cold start.

The Anthropic Python SDK is ~150-200ms to import (per ``my-docs/profiler-baseline.md``).
That cost is unnecessary on cold-start paths that don't make API calls (e.g.
``clawcodex --version``, ``clawcodex config``, fast-path subcommands once
WI-4.3 lands). The provider module uses PEP 562 ``__getattr__`` to defer
the SDK import until the ``anthropic`` attribute is first accessed.

These tests run in subprocesses so each starts with a fresh interpreter
(no preloaded ``anthropic`` from prior test runs polluting the result).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


def _run_in_subprocess(snippet: str) -> tuple[int, str, str]:
    """Run ``snippet`` in a fresh interpreter; return (rc, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_importing_anthropic_provider_does_not_import_anthropic():
    """``import src.providers.anthropic_provider`` must NOT trigger the SDK load.

    Cold-start paths that import the provider module (e.g. via the providers
    package's ``__init__.py``) but never make an API call should not pay the
    ~150-200ms SDK import cost. PEP 562 ``__getattr__`` defers the load.
    """
    rc, stdout, stderr = _run_in_subprocess(
        """
        import sys
        import src.providers.anthropic_provider  # noqa: F401
        print('anthropic_loaded:', 'anthropic' in sys.modules)
        """
    )
    assert rc == 0, f"subprocess failed: {stderr}"
    assert "anthropic_loaded: False" in stdout, (
        f"importing src.providers.anthropic_provider triggered the SDK; "
        f"stdout={stdout!r}"
    )


def test_first_access_to_anthropic_attribute_loads_sdk():
    """First read of ``ap.anthropic`` triggers the lazy load. Verifies the
    PEP 562 ``__getattr__`` fires and caches the import for subsequent access."""
    rc, stdout, stderr = _run_in_subprocess(
        """
        import sys
        import src.providers.anthropic_provider as ap
        before = 'anthropic' in sys.modules
        # Trigger the lazy load via attribute access.
        _ = ap.anthropic
        after = 'anthropic' in sys.modules
        print(f'before:{before} after:{after}')
        """
    )
    assert rc == 0, f"subprocess failed: {stderr}"
    assert "before:False after:True" in stdout, (
        f"lazy load did not fire; stdout={stdout!r}"
    )


def test_unknown_attribute_still_raises_attribute_error():
    """The PEP 562 hook must not swallow typos: only ``anthropic`` is lazy.

    A typo like ``ap.anthroopic`` should raise AttributeError as Python
    normally would, not silently return None or a fake module.
    """
    rc, stdout, stderr = _run_in_subprocess(
        """
        import src.providers.anthropic_provider as ap
        try:
            _ = ap.anthroopic  # deliberate typo
            print('UNEXPECTED:', _)
        except AttributeError as e:
            print('AttributeError raised:', str(e)[:80])
        """
    )
    assert rc == 0, f"subprocess failed: {stderr}"
    assert "AttributeError raised:" in stdout, (
        f"expected AttributeError on unknown attr; stdout={stdout!r}"
    )


def test_chat_method_resolves_anthropic_at_call_time():
    """``provider.chat(...)`` must work end-to-end: lazy load + SDK call.

    Regression check: if ``_ensure_client`` accidentally captured the local
    ``anthropic`` name at import time (when it was None), subsequent calls
    to ``chat`` would NameError or AttributeError. This test exercises the
    full path with a mocked SDK client.
    """
    # Run inline (not in subprocess) so unittest.mock.patch works on the
    # parent process's sys.modules.
    from unittest.mock import MagicMock, patch
    import src.providers.anthropic_provider as ap

    # Trigger the lazy load so ``ap.anthropic`` is populated, then patch
    # ``Anthropic`` on it. This mirrors the pattern existing tests use.
    _ = ap.anthropic

    mock_client = MagicMock()
    mock_response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "ok"
    mock_response.content = [text_block]
    mock_response.model = "claude-sonnet-4-20250514"
    mock_response.usage = MagicMock(
        input_tokens=1, output_tokens=1,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
        cache_creation=None,
    )
    mock_response.stop_reason = "end_turn"
    mock_client.messages.create.return_value = mock_response

    with patch.object(ap.anthropic, "Anthropic", return_value=mock_client):
        provider = ap.AnthropicProvider(api_key="test")
        result = provider.chat([{"role": "user", "content": "hi"}])

    assert result.content == "ok"
    assert result.usage["input_tokens"] == 1
