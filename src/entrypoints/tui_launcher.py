"""``clawcodex tui`` — run the TypeScript Ink TUI.

Architecture (the hermes-agent route): the **TypeScript Ink TUI is the parent**
and spawns the **Python agent-server as a child** it owns. This launcher is a
thin bootstrap — it resolves the Ink client command plus the command the client
should use to spawn the backend (``CLAWCODEX_AGENT_SERVER_CMD``), then execs the
client. The client spawns the agent-server and talks to it over the child's
stdin/stdout (NDJSON) — a pipe can't idle-time-out — tearing the child down on
exit:

    clawcodex tui   →   node ui-tui  →   python -m src.entrypoints.agent_server_cli

Usage::

    clawcodex tui [--provider P] [--model M] [--permission-mode MODE]
                  [--dangerously-skip-permissions]
                  [--allow-dangerously-skip-permissions]
                  [--workspace DIR] [--tui-dir DIR] [--print-connect]

``--print-connect`` instead runs the agent-server directly and prints its
``cc://`` URL + token, then waits (for attaching the reference client or
debugging). The TUI command is auto-detected (``bun``, else a built ``node``
dist); override with ``CLAWCODEX_TUI_CMD``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import secrets
import shutil
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

from src.entrypoints.agent_server_cli import run_agent_server_subcommand

#: Repo root (src/entrypoints/tui_launcher.py → parents[2]); used so the spawned
#: ``python -m src.entrypoints.agent_server_cli`` resolves via PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def run_tui_launcher(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="clawcodex tui",
        description="Run the Ink TUI; it spawns + owns a Python agent-server child.",
    )
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--permission-mode", default="default", dest="permission_mode")
    parser.add_argument(
        "--dangerously-skip-permissions", action="store_true",
        dest="dangerously_skip_permissions",
        help="Bypass all permission checks. Recommended only for sandboxes "
             "with no internet access.",
    )
    parser.add_argument(
        "--allow-dangerously-skip-permissions", action="store_true",
        dest="allow_dangerously_skip_permissions",
        help="Make bypassPermissions available (Shift+Tab / /mode) without "
             "starting in it.",
    )
    parser.add_argument("--workspace", default=None,
                        help="Workspace root the agent operates in (default: cwd).")
    parser.add_argument("--tui-dir", default=None,
                        help="Path to the ui-tui client (default: auto-detect).")
    parser.add_argument("--print-connect", action="store_true",
                        help="Run the agent-server directly, print cc:// URL + token, and wait (no TUI).")
    args = parser.parse_args(argv)

    # ch08 round-4 WI-3 — 'bubble' is a runtime-only sub-agent-escalation
    # mode with no top-level meaning; reject it (auto stays valid).
    if args.permission_mode == "bubble":
        print("clawcodex tui: --permission-mode 'bubble' is a runtime-only "
              "sub-agent mode; use default | plan | acceptEdits | "
              "bypassPermissions | auto", file=sys.stderr)
        return 2

    # Same resolution the default `clawcodex` entry runs in
    # src/cli.py::_resolve_permission_state — safety gate first, then
    # flag > --permission-mode priority, then availability.
    from src.permissions.dangerous_safety import (
        enforce_dangerous_skip_permissions_safety,
    )
    from src.permissions.modes import (
        has_allow_bypass_permissions_mode,
        initial_permission_mode_from_cli,
    )

    dangerously = bool(args.dangerously_skip_permissions)
    allow_dangerously = bool(args.allow_dangerously_skip_permissions)
    enforce_dangerous_skip_permissions_safety(
        bypass_requested=dangerously or allow_dangerously,
    )
    mode = initial_permission_mode_from_cli(
        permission_mode_cli=args.permission_mode,
        dangerously_skip_permissions=dangerously,
    )
    is_bypass_available = (
        dangerously or allow_dangerously or has_allow_bypass_permissions_mode()
    )

    if args.print_connect:
        args.permission_mode = mode
        args.is_bypass_available = is_bypass_available
        return _print_connect(args)
    return launch_ink_tui(
        provider=args.provider,
        model=args.model,
        permission_mode=mode,
        is_bypass_available=is_bypass_available,
        workspace=args.workspace,
        tui_dir=args.tui_dir,
    )


def launch_ink_tui(
    *,
    provider: str | None = None,
    model: str | None = None,
    permission_mode: str = "default",
    is_bypass_available: bool = False,
    workspace: str | None = None,
    tui_dir: str | None = None,
) -> int:
    """Launch the Ink TUI as the interactive UI (it spawns + owns the agent-server).

    Shared by the ``clawcodex tui`` subcommand and the **default** interactive
    entry in :func:`src.cli.main`. Returns the Ink client's exit code, or a
    non-zero code with a helpful message (printed to stderr by :func:`_launch`)
    when the client or a JS runtime can't be found.

    ``is_bypass_available`` carries the caller-resolved bypassPermissions
    availability (``--dangerously-skip-permissions`` /
    ``--allow-dangerously-skip-permissions`` / settings) to the spawned
    agent-server, which owns the Shift+Tab cycle and set_permission_mode
    guards. Without it, availability resolved by the CLI was silently dropped
    and bypass could never be reached at runtime.
    """
    args = SimpleNamespace(
        provider=provider,
        model=model,
        permission_mode=permission_mode,
        is_bypass_available=is_bypass_available,
        workspace=workspace,
        tui_dir=tui_dir,
        print_connect=False,
    )
    try:
        return asyncio.run(_launch(args))
    except KeyboardInterrupt:
        return 0


def _resolve_tui_dir(explicit: str | None) -> Path | None:
    """Locate the ui-tui client directory."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("CLAWCODEX_TUI_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    # Walk up from this file looking for a sibling ui-tui/.
    here = Path(__file__).resolve()
    candidates.extend(parent / "ui-tui" for parent in here.parents)
    for cand in candidates:
        if (cand / "src" / "entry.tsx").exists():
            return cand.resolve()
    return None


def _resolve_tui_command(tui_dir: Path | None) -> list[str] | None:
    """The base command (without connection args) that runs the Ink TUI.

    Prefer the built ``dist`` on node: it uses standard node module resolution.
    ``bun run src/cli.tsx`` (no build) is the fallback — but bun can mis-resolve
    ``react/jsx-dev-runtime`` from its global auto-install cache ("Cannot find
    package 'react' from …/.bun/install/cache/…"), so it's no longer the default
    when a dist exists.
    """
    override = os.environ.get("CLAWCODEX_TUI_CMD")
    if override:
        return override.split()
    if tui_dir is None:
        return None
    node = shutil.which("node")
    dist = tui_dir / "dist" / "entry.js"
    if node and dist.exists():
        return [node, str(dist)]
    bun = shutil.which("bun")
    if bun:
        return [bun, "run", str(tui_dir / "src" / "entry.tsx")]
    return None


def _agent_server_cmd(args) -> list[str]:
    """Command the Ink client runs to spawn the Python agent-server child.

    The client appends ``--stdio`` and talks over the child's stdin/stdout.
    """
    cmd = [sys.executable, "-m", "src.entrypoints.agent_server_cli",
           "--permission-mode", args.permission_mode]
    if getattr(args, "is_bypass_available", False):
        # Availability only — the launch mode above decides whether the
        # session STARTS in bypass; this keeps bypass reachable via
        # Shift+Tab / /mode either way.
        cmd += ["--allow-dangerously-skip-permissions"]
    if args.provider:
        cmd += ["--provider", args.provider]
    if args.model:
        cmd += ["--model", args.model]
    return cmd


def _child_env(args) -> dict[str, str]:
    """Env for the Ink client: where to find the backend command + the src root."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(_REPO_ROOT) + (os.pathsep + existing if existing else "")
    env["CLAWCODEX_AGENT_SERVER_CMD"] = " ".join(_agent_server_cmd(args))
    return env


@contextlib.contextmanager
def _parent_ignores_sigint():
    """Make the parent ignore Ctrl-C so the child (which owns the TTY) handles it.

    A no-op Python handler — NOT ``SIG_IGN`` — so that after ``exec`` the child
    resets to the default disposition and Ink's own Ctrl-C handling works.
    """
    try:
        prev = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_a: None)
    except (ValueError, OSError):
        prev = None
    try:
        yield
    finally:
        if prev is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signal.SIGINT, prev)


def _print_connect(args) -> int:
    """Run the agent-server directly, printing its cc:// URL + token, and wait."""
    workspace = str(Path(args.workspace).resolve()) if args.workspace else str(Path.cwd())
    token = secrets.token_urlsafe(24)
    print(f"agent-server: token {token}")
    return run_agent_server_subcommand([
        "--host", "127.0.0.1", "--port", "0", "--token", token,
        "--permission-mode", args.permission_mode,
        *(["--allow-dangerously-skip-permissions"]
          if getattr(args, "is_bypass_available", False) else []),
        *(["--provider", args.provider] if args.provider else []),
        *(["--model", args.model] if args.model else []),
        "--workspace", workspace,
    ])


async def _launch(args) -> int:
    workspace = str(Path(args.workspace).resolve()) if args.workspace else str(Path.cwd())
    cmd = _resolve_tui_command(_resolve_tui_dir(args.tui_dir))
    if cmd is None:
        print(
            "clawcodex tui: could not find/run the Ink TUI client.\n"
            "  - pass --tui-dir DIR (or set CLAWCODEX_TUI_DIR) to the ui-tui folder, and\n"
            "  - install a runner: `bun` (no build), or `node` after `npm install && npm run build`.\n"
            "  - or set CLAWCODEX_TUI_CMD to a custom launch command.",
            file=sys.stderr,
        )
        return 1

    env = _child_env(args)
    # The Ink entry (entry.tsx) reads the workspace from env, not argv; the
    # adapter (gatewayClient) launches the agent-server with --workspace from
    # CLAWCODEX_WORKSPACE. (--cwd is kept as a harmless hint; entry.tsx ignores
    # argv.)
    env["CLAWCODEX_WORKSPACE"] = workspace
    # No URL argument → the client spawns + owns the backend (hermes route).
    full = [*cmd, "--cwd", workspace]
    with _parent_ignores_sigint():
        # cwd=workspace so the spawned node process (and the agent-server it
        # owns) operate in the workspace root.
        child = await asyncio.create_subprocess_exec(*full, env=env, cwd=workspace)  # inherits stdio/TTY
        rc = await child.wait()
    return rc if rc is not None else 0


__all__ = ["launch_ink_tui", "run_tui_launcher"]
