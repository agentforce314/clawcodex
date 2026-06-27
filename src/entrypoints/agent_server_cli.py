"""``clawcodex agent-server`` — run the Direct Connect agent server.

Starts a local NDJSON-over-WebSocket server (``src.server.server``) backed by
the real agent loop (``src.server.agent_server``). A TUI client — the original
TypeScript Ink TUI via ``claude open cc://…``, or the Python Direct Connect
client (``src.server.direct_connect_manager``) — connects, sends prompts, and
renders the streamed agent output. Tools run here, in this process, on this
machine's filesystem (co-located; no file proxy).

Usage::

    clawcodex agent-server [--host H] [--port P] [--token T]
                           [--provider NAME] [--model M]
                           [--permission-mode MODE] [--workspace DIR]

On start it prints the ``cc://`` and ``http://`` URLs to connect to.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from src.server.agent_server import AgentServerConfig, PROTOCOL_VERSION, make_spawn_agent
from src.server.server import DirectConnectServer
from src.server.session_manager import SessionManager
from src.server.types import ServerConfig

logger = logging.getLogger(__name__)


def _exit_when_stdin_closes() -> None:
    """Exit the process when stdin reaches EOF (parent TUI went away).

    The Ink TUI spawns this server with stdin wired to a pipe it holds open.
    If the TUI exits — even a hard crash that skips its cleanup — the OS closes
    that pipe, stdin EOFs here, and we exit. Mirrors hermes's gateway, which
    exits on stdin EOF when its Node parent goes away. Daemon thread so it never
    blocks normal shutdown.
    """
    import os as _os
    import threading as _threading

    def _watch() -> None:
        try:
            sys.stdin.buffer.read()  # blocks until the parent closes the pipe
        except Exception:  # noqa: BLE001
            pass
        _os._exit(0)

    _threading.Thread(target=_watch, name="agent-server-parent-watch", daemon=True).start()


def run_agent_server_subcommand(argv: list[str]) -> int:
    """Entry point for ``clawcodex agent-server`` (fast-path subcommand)."""
    parser = argparse.ArgumentParser(
        prog="clawcodex agent-server",
        description="Run the Direct Connect agent server (TUI-client backend).",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1 — loopback only).")
    parser.add_argument("--port", type=int, default=0,
                        help="HTTP port for POST /sessions (default: ephemeral).")
    parser.add_argument("--token", default="",
                        help="Optional bearer token required on POST /sessions.")
    parser.add_argument("--provider", default=None, help="Provider name override.")
    parser.add_argument("--model", default=None, help="Model override.")
    parser.add_argument("--permission-mode", default="default",
                        dest="permission_mode",
                        help="default | acceptEdits | bypassPermissions | plan | auto")
    parser.add_argument("--workspace", default=None,
                        help="Workspace root the agent operates in (default: cwd).")
    parser.add_argument("--max-turns", type=int, default=20, dest="max_turns")
    parser.add_argument(
        "--exit-on-parent", action="store_true", dest="exit_on_parent",
        help="Exit when stdin reaches EOF — used when a parent TUI spawns this "
             "server, so the backend reliably dies if the TUI crashes (hermes route).",
    )
    args = parser.parse_args(argv)

    if args.exit_on_parent:
        _exit_when_stdin_closes()

    workspace = str(Path(args.workspace).resolve()) if args.workspace else str(Path.cwd())

    agent_config = AgentServerConfig(
        provider_name=args.provider,
        model=args.model,
        permission_mode=args.permission_mode,
        max_turns=args.max_turns,
    )

    try:
        return asyncio.run(_serve(args, workspace, agent_config))
    except KeyboardInterrupt:
        print("\nagent-server: shutting down")
        return 0


async def _serve(args, workspace: str, agent_config: AgentServerConfig) -> int:
    index_path = Path.home() / ".clawcodex" / "server-sessions.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)

    server_config = ServerConfig(
        host=args.host,
        port=args.port,
        auth_token=args.token or "",
        workspace=workspace,
    )
    manager = SessionManager(workspace=workspace, index_path=index_path)
    spawn = make_spawn_agent(agent_config)
    server = DirectConnectServer(config=server_config, manager=manager, spawn_agent=spawn)

    await server.start()
    http_port = server.bound_http_port
    base = f"{args.host}:{http_port}"
    print(f"agent-server: protocol v{PROTOCOL_VERSION}")
    print(f"agent-server: workspace {workspace}")
    print(f"agent-server: listening on http://{base}  (POST /sessions)")
    print(f"agent-server: connect a TUI with  cc://{base}")
    if args.token:
        print("agent-server: POST /sessions requires Authorization: Bearer <token>")
    print("agent-server: Ctrl-C to stop")

    try:
        await server.serve_forever()
    finally:
        await server.stop()
    return 0


__all__ = ["run_agent_server_subcommand"]


if __name__ == "__main__":
    # Standalone entry so the Ink TUI can spawn the backend as
    #   python -m src.entrypoints.agent_server_cli --host … --port 0 --token …
    # (the hermes route: the TS client owns the Python child).
    import sys

    raise SystemExit(run_agent_server_subcommand(sys.argv[1:]))
