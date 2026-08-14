"""``clawcodex serve`` — run the desktop gateway server.

Backend for the ClawCodex Desktop app (``ui-desktop/``): one loopback HTTP
server exposing ``/api/health``, ``/api/status``, a token-bearing ``/`` page,
and the JSON-RPC WebSocket at ``/api/ws`` that carries the entire chat
surface. Sessions run in-process on the same agent core the TUI uses
(``src.server.agent_server``); this transport only adapts wire shapes.

The desktop boot contract (``ui-desktop/electron/backend-ready.ts``):

- spawned as ``clawcodex serve --host 127.0.0.1 --port 0`` (port 0 = OS pick),
- announces readiness by printing ``CLAWCODEX_BACKEND_READY port=<N>`` on
  stdout (and writing ``{"port": N}`` to ``$CLAWCODEX_DESKTOP_READY_FILE``
  when set),
- authenticates REST via the ``X-ClawCodex-Session-Token`` header and the
  WebSocket via ``?token=``; the spawn token arrives in
  ``$CLAWCODEX_DASHBOARD_SESSION_TOKEN``,
- serves ``window.__CLAWCODEX_SESSION_TOKEN__`` on ``GET /`` so the shell can
  adopt the token of an already-running backend it recognizes.

Usage::

    clawcodex serve [--host H] [--port P] [--token T] [--workspace DIR]
                    [--provider NAME] [--model M] [--effort E]
                    [--permission-mode MODE]
                    [--dangerously-skip-permissions]
                    [--allow-dangerously-skip-permissions]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
from typing import Callable

# Same per-process default as the agent-server entry: experimental API betas
# off unless the user opts in. This process makes the API calls.
os.environ.setdefault("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "true")
from pathlib import Path

from src.utils.startup_profiler import profile_checkpoint

profile_checkpoint("serve_import_start")

from src.server.agent_server import (
    DEFAULT_MAX_TURNS,
    AgentServerConfig,
    PROTOCOL_VERSION,
    make_spawn_agent,
)
from src.server.session_manager import SessionManager

profile_checkpoint("serve_import_end")

logger = logging.getLogger(__name__)

READY_MARKER = "CLAWCODEX_BACKEND_READY"
TOKEN_ENV = "CLAWCODEX_DASHBOARD_SESSION_TOKEN"
READY_FILE_ENV = "CLAWCODEX_DESKTOP_READY_FILE"

# ``(host, port, token) -> None``, called once the socket is bound. See
# ``run_serve_subcommand``.
ReadyHook = Callable[[str, int, str], None]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clawcodex serve",
        description="Run the desktop gateway server (ClawCodex Desktop backend).",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1 — loopback only).")
    parser.add_argument("--port", type=int, default=0,
                        help="Port (default: 0 — OS-assigned, announced on stdout).")
    parser.add_argument("--token", default=None,
                        help=f"Session token; default ${TOKEN_ENV} or generated.")
    parser.add_argument("--profile", default=None,
                        help="Profile name (accepted for desktop compatibility; "
                             "single-profile for now).")
    parser.add_argument("--workspace", default=None,
                        help="Default workspace for new sessions (default: cwd).")
    parser.add_argument("--provider", default=None, help="Provider name override.")
    parser.add_argument("--model", default=None, help="Model override.")
    parser.add_argument(
        "--effort", default=None,
        choices=("low", "medium", "high", "xhigh", "max"),
        help="Reasoning effort seed for sessions.",
    )
    parser.add_argument(
        "--fallback-model", default=None, dest="fallback_model",
        help="Model to switch to after repeated overloaded errors.",
    )
    parser.add_argument("--permission-mode", default=None, dest="permission_mode",
                        help="default | acceptEdits | bypassPermissions | plan | auto")
    parser.add_argument("--dangerously-skip-permissions", action="store_true",
                        dest="dangerously_skip_permissions",
                        help="Bypass all permission checks (start in bypassPermissions).")
    parser.add_argument("--allow-dangerously-skip-permissions", action="store_true",
                        dest="allow_dangerously_skip_permissions",
                        help="Make bypassPermissions available without starting in it.")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS,
                        dest="max_turns")
    parser.add_argument(
        "--exit-on-parent", action="store_true", dest="exit_on_parent",
        help="Exit when stdin reaches EOF (the desktop shell owns this child).",
    )
    return parser


def _exit_when_stdin_closes() -> None:
    """Exit when stdin EOFs — the Electron shell holds the pipe open.

    Mirrors the agent-server's parent watch: if the desktop app dies without
    cleanup, the OS closes the pipe and this backend exits instead of leaking.
    """
    import threading

    def _watch() -> None:
        try:
            sys.stdin.buffer.read()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)

    threading.Thread(target=_watch, name="serve-parent-watch", daemon=True).start()


def run_serve_subcommand(argv: list[str], *, on_ready: ReadyHook | None = None) -> int:
    """Entry point for ``clawcodex serve`` (fast-path subcommand).

    ``on_ready(host, port, token)`` replaces the desktop readiness marker when
    given — ``clawcodex web`` uses it to print a browser URL instead. Default
    None keeps the desktop's stdout contract exactly as it was.
    """
    try:
        from src.utils.legacy_migration import migrate_user_dir_once
        migrate_user_dir_once()
    except Exception:  # noqa: BLE001 — migration is best-effort by contract
        pass

    args = _build_parser().parse_args(argv)

    # Interactive task surface, same classification as the agent-server: this
    # process backs an interactive client even though its stdio are pipes.
    from src.bootstrap.state import set_is_interactive

    set_is_interactive(True)

    if args.exit_on_parent:
        _exit_when_stdin_closes()

    if args.permission_mode == "bubble":
        print("serve: --permission-mode 'bubble' is a runtime-only sub-agent "
              "mode; use default | plan | acceptEdits | bypassPermissions | auto",
              file=sys.stderr)
        return 2

    dangerously = bool(args.dangerously_skip_permissions)
    allow_dangerously = bool(args.allow_dangerously_skip_permissions)
    from src.permissions.dangerous_safety import (
        enforce_dangerous_skip_permissions_safety,
    )

    enforce_dangerous_skip_permissions_safety(
        bypass_requested=dangerously or allow_dangerously,
    )

    workspace = str(Path(args.workspace).resolve()) if args.workspace else str(Path.cwd())

    # The desktop is an INTERACTIVE surface with a real user at the window, and
    # this server is its own loopback, token-gated child — the same trust model
    # as the TUI launcher spawning its agent-server. So it resolves permissions
    # through the shared interactive resolver (src/cli.py + tui_launcher use it
    # too), which means Full Access by default, exactly like `clawcodex`.
    #
    # Without this the desktop ran in "default" mode and asked approval for
    # every Write/Bash; the resolver also carries the guards that make the
    # implicit floor safe — an operator `disableBypassPermissionsMode`
    # lockdown and root-outside-sandbox both drop it back to prompting, and a
    # persisted `permissions.defaultMode` still wins.
    from src.permissions.modes import resolve_interactive_permission_state

    mode, is_bypass_available, bypass_selectable = resolve_interactive_permission_state(
        permission_mode_cli=args.permission_mode,
        dangerously_skip_permissions=dangerously,
        allow_dangerously_skip_permissions=allow_dangerously,
        cwd=workspace,
    )
    args.permission_mode = mode

    if args.fallback_model and args.fallback_model == args.model:
        print("serve: --fallback-model must differ from --model", file=sys.stderr)
        return 2

    token = args.token if args.token is not None else os.environ.get(TOKEN_ENV) or ""
    if not token:
        token = secrets.token_urlsafe(32)

    agent_config = AgentServerConfig(
        provider_name=args.provider,
        model=args.model,
        effort=args.effort,
        fallback_model=args.fallback_model,
        permission_mode=args.permission_mode,
        is_bypass_available=is_bypass_available,
        bypass_selectable=bypass_selectable,
        max_turns=args.max_turns,
    )

    try:
        return asyncio.run(_serve(args, workspace, token, agent_config, on_ready=on_ready))
    except KeyboardInterrupt:
        print("\nserve: shutting down", file=sys.stderr)
        return 0


def _announce_ready(port: int) -> None:
    """Print the desktop's readiness marker and honor the ready-file contract.

    stdout is the desktop's primary channel (``backend-ready.ts`` parses
    ``CLAWCODEX_BACKEND_READY port=<N>``); the ready file is the fallback for
    hosts where child stdout is unreliable.
    """
    print(f"{READY_MARKER} port={port}", flush=True)
    ready_file = os.environ.get(READY_FILE_ENV)
    if not ready_file:
        return
    try:
        path = Path(ready_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"port": port}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("serve: could not write ready file %s", ready_file,
                       exc_info=True)


async def _serve(args, workspace: str, token: str,
                 agent_config: AgentServerConfig,
                 on_ready: ReadyHook | None = None) -> int:
    import uvicorn

    from src.server.desktop_serve import DesktopServeState, build_app
    from src.utils.clawcodex_dirs import get_user_config_dir

    index_path = Path(get_user_config_dir()) / "server-sessions.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    manager = SessionManager(workspace=workspace, index_path=index_path)
    spawn = make_spawn_agent(agent_config)

    state = DesktopServeState(
        token=token,
        workspace=workspace,
        manager=manager,
        spawn_agent=spawn,
        agent_config=agent_config,
        protocol_version=PROTOCOL_VERSION,
    )
    app = build_app(state)

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())

    # uvicorn flips ``started`` after binding; port 0 resolves to the real
    # port only then. A failed bind ends serve_task instead — surface that
    # rather than spinning forever.
    while not server.started:
        if serve_task.done():
            exc = serve_task.exception()
            if exc:
                print(f"serve: failed to start: {exc}", file=sys.stderr)
            return 1
        await asyncio.sleep(0.01)

    bound_port = 0
    for srv in server.servers or []:
        for sock in srv.sockets or []:
            bound_port = sock.getsockname()[1]
            break
        if bound_port:
            break
    if on_ready is None:
        _announce_ready(bound_port)
    else:
        on_ready(args.host, bound_port, token)
    logger.info("serve: listening on http://%s:%s (workspace %s)",
                args.host, bound_port, workspace)

    try:
        await serve_task
    finally:
        await state.shutdown()
    return 0


__all__ = ["run_serve_subcommand"]


if __name__ == "__main__":
    raise SystemExit(run_serve_subcommand(sys.argv[1:]))
