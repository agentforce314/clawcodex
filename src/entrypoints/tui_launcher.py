"""``clawcodex tui`` — one command to run the TypeScript Ink TUI.

The TUI (TypeScript) and the agent engine (Python) are separate runtimes, so
they talk over the Direct Connect WebSocket protocol — that's why there is a
"server". This launcher hides the split: it starts the agent-server *in this
process* on an ephemeral loopback port, then spawns the Ink TUI as a child
pointed at it, and tears the server down when the TUI exits. The user runs one
command; the client/server plumbing is invisible.

    clawcodex tui [--provider P] [--model M] [--permission-mode MODE]
                  [--workspace DIR] [--tui-dir DIR] [--print-connect]

`--print-connect` starts the server and prints the cc:// URL + token without
spawning a TUI (useful for attaching the reference `claude open cc://…` client
or debugging). The TUI command is auto-detected (bun, else a built node dist);
override with the `CLAWCODEX_TUI_CMD` env var.
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

from src.server.agent_server import AgentServerConfig, make_spawn_agent
from src.server.server import DirectConnectServer
from src.server.session_manager import SessionManager
from src.server.types import ServerConfig


def run_tui_launcher(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="clawcodex tui",
        description="Run the TypeScript Ink TUI on a local agent-server (one command).",
    )
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--permission-mode", default="default", dest="permission_mode")
    parser.add_argument("--workspace", default=None,
                        help="Workspace root the agent operates in (default: cwd).")
    parser.add_argument("--tui-dir", default=None,
                        help="Path to the tui_typescript client (default: auto-detect).")
    parser.add_argument("--print-connect", action="store_true",
                        help="Start the server, print cc:// URL + token, and wait (no TUI spawn).")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_launch(args))
    except KeyboardInterrupt:
        return 0


def _resolve_tui_dir(explicit: str | None) -> Path | None:
    """Locate the tui_typescript client directory."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("CLAWCODEX_TUI_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    # Walk up from this file looking for a sibling tui_typescript/.
    here = Path(__file__).resolve()
    candidates.extend(parent / "tui_typescript" for parent in here.parents)
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


async def _launch(args) -> int:
    workspace = str(Path(args.workspace).resolve()) if args.workspace else str(Path.cwd())
    index_path = Path.home() / ".clawcodex" / "server-sessions.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)

    # Per-launch server token: required on POST /sessions even on loopback.
    token = secrets.token_urlsafe(24)
    server_config = ServerConfig(host="127.0.0.1", port=0, auth_token=token, workspace=workspace)
    manager = SessionManager(workspace=workspace, index_path=index_path)
    agent_config = AgentServerConfig(
        provider_name=args.provider, model=args.model, permission_mode=args.permission_mode
    )
    server = DirectConnectServer(
        config=server_config, manager=manager, spawn_agent=make_spawn_agent(agent_config)
    )

    await server.start()
    port = server.bound_http_port
    cc_url = f"cc://127.0.0.1:{port}"
    serve_task = asyncio.get_running_loop().create_task(server.serve_forever())

    try:
        if args.print_connect:
            print(f"agent-server: {cc_url}")
            print(f"agent-server: token {token}")
            print("agent-server: waiting (Ctrl-C to stop)")
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.Event().wait()
            return 0

        cmd = _resolve_tui_command(_resolve_tui_dir(args.tui_dir))
        if cmd is None:
            print(
                "clawcodex tui: could not find/run the Ink TUI client.\n"
                "  - pass --tui-dir DIR (or set CLAWCODEX_TUI_DIR) to the tui_typescript folder, and\n"
                "  - install a runner: `bun` (no build), or `node` after `npm install && npm run build`.\n"
                f"  - or set CLAWCODEX_TUI_CMD to a custom launch command.\n"
                f"  (the server is up at {cc_url})",
                file=sys.stderr,
            )
            return 1

        full = [*cmd, cc_url, "--token", token, "--cwd", workspace]
        with _parent_ignores_sigint():
            child = await asyncio.create_subprocess_exec(*full)  # inherits stdio/TTY
            rc = await child.wait()
        return rc if rc is not None else 0
    finally:
        await server.stop()
        serve_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serve_task


__all__ = ["run_tui_launcher"]
