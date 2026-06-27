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
    parser.add_argument("--workspace", default=None,
                        help="Workspace root the agent operates in (default: cwd).")
    parser.add_argument("--tui-dir", default=None,
                        help="Path to the ui-tui client (default: auto-detect).")
    parser.add_argument("--print-connect", action="store_true",
                        help="Run the agent-server directly, print cc:// URL + token, and wait (no TUI).")
    args = parser.parse_args(argv)

    if args.print_connect:
        return _print_connect(args)
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
        if (cand / "src" / "cli.tsx").exists():
            return cand.resolve()
    return None


def _resolve_tui_command(tui_dir: Path | None) -> list[str] | None:
    """The base command (without connection args) that runs the Ink TUI."""
    override = os.environ.get("CLAWCODEX_TUI_CMD")
    if override:
        return override.split()
    if tui_dir is None:
        return None
    bun = shutil.which("bun")
    if bun:
        return [bun, "run", str(tui_dir / "src" / "cli.tsx")]
    node = shutil.which("node")
    dist = tui_dir / "dist" / "cli.js"
    if node and dist.exists():
        return [node, str(dist)]
    return None


def _agent_server_cmd(args) -> list[str]:
    """Command the Ink client runs to spawn the Python agent-server child.

    The client appends ``--stdio`` and talks over the child's stdin/stdout.
    """
    cmd = [sys.executable, "-m", "src.entrypoints.agent_server_cli",
           "--permission-mode", args.permission_mode]
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
    # No URL argument → the client spawns + owns the backend (hermes route).
    full = [*cmd, "--cwd", workspace]
    with _parent_ignores_sigint():
        child = await asyncio.create_subprocess_exec(*full, env=env)  # inherits stdio/TTY
        rc = await child.wait()
    return rc if rc is not None else 0


__all__ = ["run_tui_launcher"]
