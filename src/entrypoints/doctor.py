"""Fast-path handler for ``clawcodex doctor``.

WI-4.3: skips the TUI/REPL load. Doctor is diagnostic-only, doesn't need
the full tool registry or the prompt assembly. Mirrors TS ``main.tsx``
``claude doctor`` early-return.
"""

from __future__ import annotations

import sys


def run_doctor() -> int:
    """Print the basic environment + version sanity report.

    Imports only the modules needed for the diagnostic surface — does NOT
    load the Ink-TUI launcher, the agent-server, or the full tool registry.
    MCP-side diagnostics are loaded lazily below only when ``--mcp`` is
    present.
    """
    print("clawcodex doctor")
    print("================")
    try:
        from src import __version__
        print(f"version:       {__version__}")
    except Exception:  # pragma: no cover
        print("version:       (unknown)")
    print(f"python:        {sys.version.split()[0]}")
    print(f"platform:      {sys.platform}")
    # If `mcp` subflag is present, run MCP diagnostics; else skip.
    if "--mcp" in sys.argv:
        try:
            import asyncio
            from src.services.mcp.doctor import run_diagnostics
            print("")
            print("MCP diagnostics")
            print("---------------")
            result = asyncio.run(run_diagnostics())
            for line in str(result).splitlines():
                print(f"  {line}")
        except Exception as exc:
            print(f"MCP diagnostics failed: {exc}", file=sys.stderr)
            return 1
    return 0
