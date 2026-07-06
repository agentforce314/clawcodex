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
                           [--dangerously-skip-permissions]
                           [--allow-dangerously-skip-permissions]

On start it prints the ``cc://`` and ``http://`` URLs to connect to.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading

# OpenClaude default: experimental API betas off unless the user opts in
# (mirrors typescript/src/entrypoints/cli.tsx:44's MODULE-scope placement —
# beats any import-time env capture in the heavy imports below). Per-process
# entry: this is the standalone backend where API calls happen; a direct
# ``clawcodex agent-server`` / ``python -m`` run inherits nothing from
# cli.main(), and Ink-spawned children get it by env inheritance too.
os.environ.setdefault("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "true")
from pathlib import Path

# ch02 round-4 WI-4 — bracket the child's import cost. This module is the
# spawned backend's entry (`python -m src.entrypoints.agent_server_cli`),
# and the transitive agent_server/query/tool imports below dominate its
# cold start. Zero cost unless CLAUDE_CODE_PROFILE_STARTUP is set (the
# gate latches at startup_profiler import).
from src.utils.startup_profiler import profile_checkpoint

profile_checkpoint("agent_server_import_start")

from src.server.agent_server import (
    DEFAULT_MAX_TURNS,
    AgentServerConfig,
    PROTOCOL_VERSION,
    make_spawn_agent,
)
from src.server.server import DirectConnectServer
from src.server.session_manager import SessionManager
from src.server.types import ServerConfig

profile_checkpoint("agent_server_import_end")

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
    parser.add_argument(
        "--fallback-model", default=None, dest="fallback_model",
        help="Model to switch to after repeated overloaded (529) errors "
             "(session-sticky; never persisted).",
    )
    parser.add_argument("--permission-mode", default="default",
                        dest="permission_mode",
                        help="default | acceptEdits | bypassPermissions | plan | auto")
    parser.add_argument(
        "--dangerously-skip-permissions", action="store_true",
        dest="dangerously_skip_permissions",
        help="Bypass all permission checks (start in bypassPermissions mode). "
             "Recommended only for sandboxes with no internet access.",
    )
    parser.add_argument(
        "--allow-dangerously-skip-permissions", action="store_true",
        dest="allow_dangerously_skip_permissions",
        help="Make bypassPermissions AVAILABLE (Shift+Tab / /mode) without "
             "starting in it.",
    )
    parser.add_argument("--workspace", default=None,
                        help="Workspace root the agent operates in (default: cwd).")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, dest="max_turns")
    parser.add_argument(
        "--exit-on-parent", action="store_true", dest="exit_on_parent",
        help="Exit when stdin reaches EOF — used when a parent TUI spawns this "
             "server, so the backend reliably dies if the TUI crashes (hermes route).",
    )
    parser.add_argument(
        "--stdio", action="store_true",
        help="Talk NDJSON over stdin/stdout (one session) instead of HTTP+"
             "WebSocket — the default LOCAL transport for the TUI (a pipe can't "
             "idle-time-out). stdout is reserved for JSON frames; diagnostics go "
             "to stderr; stdin EOF ends the session.",
    )
    args = parser.parse_args(argv)

    # In --stdio mode the inbound reader owns stdin, so the EOF watcher (which
    # also reads stdin) must NOT run — EOF is detected by the reader instead.
    if args.exit_on_parent and not args.stdio:
        _exit_when_stdin_closes()

    # ch08 round-4 WI-3 — 'bubble' is a runtime-only sub-agent-escalation
    # mode; it has no meaning as a top-level session mode (there is no
    # parent to escalate to). Reject it explicitly rather than start a
    # session that behaves surprisingly. ('auto' IS a valid top-level mode
    # — the ch06 classifier lane — so it stays allowed.)
    if args.permission_mode == "bubble":
        print("agent-server: --permission-mode 'bubble' is a runtime-only "
              "sub-agent mode; use default | plan | acceptEdits | "
              "bypassPermissions | auto", file=sys.stderr)
        return 2

    # Same root-outside-sandbox refusal the top-level CLI runs for these
    # flags (src/cli.py _resolve_permission_state); a hand-launched
    # agent-server must not be a way around it.
    dangerously = bool(args.dangerously_skip_permissions)
    allow_dangerously = bool(args.allow_dangerously_skip_permissions)
    from src.permissions.dangerous_safety import (
        enforce_dangerous_skip_permissions_safety,
    )

    enforce_dangerous_skip_permissions_safety(
        bypass_requested=dangerously or allow_dangerously,
    )
    from src.permissions.modes import is_bypass_permissions_mode_disabled
    _bypass_disabled = is_bypass_permissions_mode_disabled()
    if dangerously and not _bypass_disabled:
        # Flag wins over --permission-mode, same priority as
        # initial_permission_mode_from_cli (src/permissions/modes.py).
        args.permission_mode = "bypassPermissions"
    elif dangerously and _bypass_disabled:
        # C12: a disableBypassPermissionsMode lockdown overrides the flag —
        # do NOT set bypass mode (TS skips the candidate → default).
        log.warning("Bypass permissions mode disabled by settings/policy; ignoring --dangerously-skip-permissions for mode")

    # bypass AVAILABILITY. Flags always count. Trusted settings
    # (permissions.allowBypassPermissionsMode) count only on the
    # single-session --stdio transport: this server serves exactly the
    # operator who launched it, so their own user/local settings apply. On
    # the multi-session --http transport, folding host settings in would
    # unlock bypass for every remote client — resolve that per-launch
    # upstream and pass a flag instead.
    is_bypass_available = dangerously or allow_dangerously
    if not is_bypass_available and args.stdio:
        from src.permissions.modes import has_allow_bypass_permissions_mode

        is_bypass_available = has_allow_bypass_permissions_mode()
    # ... AND NOT disabled (critic C12 — the dropped negative guard; a lockdown
    # overrides even an explicit bypass request).
    if is_bypass_available:
        from src.permissions.modes import is_bypass_permissions_mode_disabled
        if is_bypass_permissions_mode_disabled():
            is_bypass_available = False

    workspace = str(Path(args.workspace).resolve()) if args.workspace else str(Path.cwd())

    if args.fallback_model and args.fallback_model == args.model:
        print("agent-server: --fallback-model must differ from --model",
              file=sys.stderr)
        return 2
    agent_config = AgentServerConfig(
        provider_name=args.provider,
        model=args.model,
        fallback_model=args.fallback_model,
        permission_mode=args.permission_mode,
        is_bypass_available=is_bypass_available,
        max_turns=args.max_turns,
    )

    if args.stdio:
        try:
            return asyncio.run(_serve_stdio(workspace, agent_config))
        except KeyboardInterrupt:
            return 0

    try:
        return asyncio.run(_serve(args, workspace, agent_config))
    except KeyboardInterrupt:
        print("\nagent-server: shutting down")
        return 0


async def _serve_stdio(workspace: str, agent_config: AgentServerConfig) -> int:
    """Single-session stdio transport — the hermes route's local link.

    Pumps newline-delimited JSON between this process's stdin/stdout and the
    in-process agent, reusing ``make_spawn_agent``/``AgentHandle`` exactly as the
    WS pump does. There is no socket, so nothing can idle-time-out. ``stdout`` is
    RESERVED for JSON frames (diagnostics go to ``stderr``); ``stdin`` EOF — the
    parent TUI going away — ends the session.
    """
    profile_checkpoint("agent_server_serve_start")
    # This transport serves exactly ONE session (the Ink client's child, or
    # a hand-run standalone). single_session unlocks the process-global
    # side effects in _build_runtime (post-trust env apply, context-cache
    # prefetch) that the multi-session --http transport must not perform.
    import dataclasses as _dc

    agent_config = _dc.replace(agent_config, single_session=True)
    index_path = Path.home() / ".clawcodex" / "server-sessions.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    manager = SessionManager(workspace=workspace, index_path=index_path)
    info = manager.create_session(cwd=workspace)
    spawn = make_spawn_agent(agent_config)
    agent = await spawn(info.id, workspace, None)  # emits system/init as its first frame
    manager.mark_running(info.id)

    loop = asyncio.get_running_loop()
    out = sys.stdout

    async def outbound() -> None:
        """Agent → stdout: one JSON object per line, flushed."""
        async for msg in agent.messages_from_agent():
            try:
                out.write(json.dumps(msg) + "\n")
                out.flush()
            except (BrokenPipeError, OSError):
                return

    # Inbound: a daemon thread reads stdin lines and dispatches them onto the
    # loop (mirrors the existing threaded pattern). stdin EOF resolves `closed`.
    closed: asyncio.Future[None] = loop.create_future()

    def _resolve_closed() -> None:
        if not closed.done():
            closed.set_result(None)

    def _read_stdin() -> None:
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    asyncio.run_coroutine_threadsafe(agent.send_to_agent(parsed), loop)
        except Exception:  # noqa: BLE001 - any stdin error ends the session cleanly
            pass
        finally:
            loop.call_soon_threadsafe(_resolve_closed)

    threading.Thread(target=_read_stdin, name="agent-server-stdin", daemon=True).start()

    out_task = loop.create_task(outbound())
    try:
        await asyncio.wait({out_task, closed}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not out_task.done():
            out_task.cancel()
        try:
            await agent.shutdown()
        except Exception:  # noqa: BLE001
            pass
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
