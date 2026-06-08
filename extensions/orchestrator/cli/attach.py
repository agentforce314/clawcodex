"""F-49 Phase 2: live TUI client for the Unix control socket.

Connects to ``{workspace}/.run_control/{run_id}.sock`` and renders
TextDelta / ToolCallEvent / ToolResultEvent events in a Textual
``App``. Keyboard: p pause, r resume, s stop, t takeover, i inject
(opens a modal Input), q detach+quit.

Falls back to a non-interactive JSON-line printer when stdout is not
a TTY (piped invocation, CI). Reads only; no orchestrator coupling
beyond the Phase 1 socket protocol in
``extensions/orchestrator/control_socket.py``.

The Textual import is deferred to inside the TTY branch of
``_run_attach`` so the fallback path never pays the cost of
importing the heavy TUI stack.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class _AttachTarget:
    """Resolved target for the attach session."""

    run_id: str
    workspace_path: Path
    issue_id: str  # for header display


def _resolve_attach_target(
    registry_path: Path | None,
    workspace_root: Path | None,
    issue_id: str | None,
    run_id: str | None,
) -> _AttachTarget | None:
    """Resolve the (run_id, workspace_path, issue_id) triple.

    Lookup priority:
      1. ``--id <issue_id>`` via :class:`IssueRegistry.get_by_issue_ref`.
         Returns the record's ``run_id`` and ``workspace_path``.
      2. ``--run <run_id>`` + ``--workspace <path>`` (or resolved
         ``workspace_root``). The caller is responsible for telling
         us the workspace because there is no inverse index from
         ``run_id`` → ``workspace_path`` in the registry.
      3. Otherwise returns ``None``; the CLI handler turns that into
         a usage error.
    """
    from extensions.orchestrator.issue_registry import IssueRegistry

    if issue_id:
        if registry_path is None or not registry_path.exists():
            return None
        try:
            registry = IssueRegistry(registry_path)
        except Exception:
            return None
        record = registry.get_by_issue_ref(issue_id)
        if record is None or record.run_id is None:
            return None
        if record.workspace_path is None:
            return None
        return _AttachTarget(
            run_id=record.run_id,
            workspace_path=Path(record.workspace_path),
            issue_id=record.issue_identifier or record.issue_id,
        )

    if run_id and workspace_root is not None:
        # ``--run`` mode: workspace comes from ``--workspace`` or
        # the resolved ``workspace_root``. We don't have the issue
        # identifier here, so use the run_id as a label.
        return _AttachTarget(
            run_id=run_id,
            workspace_path=Path(workspace_root),
            issue_id=f"run:{run_id}",
        )

    return None


async def _send_cmd(
    writer: asyncio.StreamWriter, verb: str, payload: str = "",
) -> None:
    """Send one newline-delimited JSON control command.

    Reused by the Textual App actions and the tests. Mirrors the
    client side of the Phase 1 protocol at
    ``extensions/orchestrator/control_socket.py``.
    """
    writer.write(
        (json.dumps({"cmd": verb, "payload": payload}) + "\n").encode("utf-8"),
    )
    await writer.drain()


def _run_attach(
    registry_path: Path | None,
    workspace_root: Path | None,
    args: argparse.Namespace,
) -> int:
    """CLI handler for ``clawcodex orchestrator issue attach``.

    Resolves the target, validates the socket exists, then branches
    on whether stdout is a TTY:

      * TTY: launch the Textual ``AttachApp`` (deferred import).
      * Non-TTY: print one JSON event per line, read verbs from
        stdin (``pause``, ``resume``, ``stop``, ``takeover``,
        ``inject <text>``, ``quit``).

    Returns the process exit code (0 success, 1 error, 2 usage).
    """
    issue_id = getattr(args, "id", None) or getattr(args, "issue_id", None)
    run_id = getattr(args, "run", None) or getattr(args, "run_id", None)
    workspace_arg = getattr(args, "workspace", None)

    if not issue_id and not run_id:
        print(
            "error: --id <issue_id> or --run <run_id> is required",
            file=sys.stderr,
        )
        return 2

    if run_id and not workspace_root and not workspace_arg:
        print(
            "error: --run requires --workspace (or a resolved workspace root)",
            file=sys.stderr,
        )
        return 2

    target = _resolve_attach_target(
        registry_path, workspace_root, issue_id, run_id,
    )
    if target is None:
        if issue_id:
            print(
                f"error: no active run found for issue {issue_id!r}. "
                f"It may have ended before this command was issued.",
                file=sys.stderr,
            )
        else:
            print(
                f"error: could not resolve target for run {run_id!r}",
                file=sys.stderr,
            )
        return 1

    sock_path = (
        target.workspace_path
        / ".run_control"
        / f"{target.run_id}.sock"
    )
    if not sock_path.exists():
        print(
            f"error: socket not found at {sock_path}. "
            f"The agent run may have ended — use "
            f"`clawcodex orchestrator issue transcript --id {target.issue_id}` "
            f"to view past events.",
            file=sys.stderr,
        )
        return 1

    issue_label = f"{target.issue_id} (run {target.run_id})"

    async def _driver() -> int:
        reader, writer = await asyncio.open_unix_connection(str(sock_path))
        try:
            if not (sys.stdout.isatty() and sys.stdin.isatty()):
                return await _run_tail_fallback(reader, writer, issue_label)
            # TTY: defer Textual import so the non-TTY path is fast.
            try:
                from extensions.orchestrator.cli.attach import (
                    AttachApp,
                )
            except ImportError as exc:
                print(
                    f"error: Textual not available ({exc}); "
                    f"fall back to non-TTY mode (pipe or redirect)",
                    file=sys.stderr,
                )
                return 1
            app = AttachApp(reader, writer, issue_label)
            await app.run_async()
            return 0
        finally:
            try:
                writer.close()
            except Exception:
                pass

    try:
        return asyncio.run(_driver())
    except ConnectionRefusedError:
        print(
            f"error: connection refused at {sock_path} — "
            f"the agent process may have just exited",
            file=sys.stderr,
        )
        return 1
    except FileNotFoundError:
        # Race: the .sock was unlinked between exists() and open.
        print(
            f"error: socket vanished at {sock_path} — "
            f"the agent may have just exited",
            file=sys.stderr,
        )
        return 1


# ------------------------------------------------------------------
# Non-TTY fallback (defined after the dispatcher so we can import
# attach.py without pulling in Textual at module load time)
# ------------------------------------------------------------------


async def _run_tail_fallback(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    issue_label: str,
) -> int:
    """Non-TTY mode: print one JSON event per line, accept verbs on stdin.

    Reads newline-terminated commands from stdin (``pause``,
    ``resume``, ``stop``, ``takeover``, ``inject <text>``,
    ``quit``). Exits when the server closes the socket (EOF) or
    when ``quit`` is received.
    """
    print(f"attach (non-tty): {issue_label}", file=sys.stderr)
    print(
        "Commands: pause | resume | stop | takeover | inject <text> | quit",
        file=sys.stderr,
    )
    loop = asyncio.get_event_loop()

    async def _stdin_commands() -> None:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            if line == "quit":
                return
            parts = line.split(maxsplit=1)
            verb = parts[0]
            payload = parts[1] if len(parts) > 1 else ""
            try:
                await _send_cmd(writer, verb, payload)
            except Exception:
                return

    stdin_task = asyncio.create_task(_stdin_commands())
    try:
        while True:
            line = await reader.readline()
            if not line:
                return 0
            sys.stdout.write(line.decode("utf-8"))
            sys.stdout.flush()
    finally:
        stdin_task.cancel()
        try:
            await stdin_task
        except (asyncio.CancelledError, Exception):
            pass


# ------------------------------------------------------------------
# Textual app + supporting classes — only imported on the TTY path.
# Defined at module level (rather than inside the if/else) so tests
# can construct AttachApp directly without going through the
# import-time guard.
# ------------------------------------------------------------------


class AttachMessage:
    """Lightweight message posted by the socket reader task.

    Uses duck-typing rather than ``textual.message.Message`` so this
    module imports cleanly without Textual available. The Textual
    ``App.on_attach_message`` handler accepts the dict-shaped
    ``.frame`` attribute.
    """

    def __init__(self, frame: dict) -> None:
        self.frame = frame


class AttachApp:
    """Placeholder for the Textual App class.

    The real Textual ``App`` subclass is defined inside the
    ``_build_textual_app`` factory below, which is only invoked on
    the TTY branch of ``_run_attach``. Importing this module
    without Textual available must remain safe, so the class is
    constructed lazily.
    """

    def __init__(  # noqa: D401 - placeholder
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        issue_label: str,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._issue_label = issue_label
        self._reader_task: asyncio.Task[None] | None = None
        self._real_app = None

    def run_async(self) -> asyncio.Future:
        """Construct and run the real Textual app.

        Deferred to runtime so import-time stays Textual-free.
        """
        import textual.app  # local import: TTY-only path

        if self._real_app is None:
            self._real_app = self._build_textual_app()(
                self._reader, self._writer, self._issue_label,
            )
        return self._real_app.run_async()

    @staticmethod
    def _build_textual_app() -> type:
        """Build the real Textual ``App`` subclass.

        Returns the class; caller instantiates with (reader, writer,
        issue_label). Imported here (not at module top) so a missing
        Textual doesn't break the rest of this module.
        """
        import textual.app as _tapp
        import textual.containers as _tcont
        import textual.message as _tmsg
        import textual.screen as _tscr
        import textual.widgets as _tw

        class _RealAttachMessage(_tmsg.Message):
            def __init__(self, frame: dict) -> None:
                super().__init__()
                self.frame = frame

        class _ModalInputScreen(_tscr.ModalScreen[str | None]):
            BINDINGS = [
                _tapp.Binding("escape", "cancel", "Cancel"),
            ]

            def __init__(self, writer: asyncio.StreamWriter) -> None:
                super().__init__()
                self._writer = writer

            def compose(self) -> _tapp.ComposeResult:
                yield _tw.Label("Inject hint to agent:")
                yield _tw.Input(id="hint", placeholder="type and press Enter")
                yield _tw.Static("[dim]Esc to cancel[/]")

            async def on_input_submitted(
                self, event: _tw.Input.Submitted,
            ) -> None:
                value = event.value
                if value:
                    await _send_cmd(self._writer, "inject", value)
                self.dismiss(value)

            def action_cancel(self) -> None:
                self.dismiss(None)

        class _RealAttachApp(_tapp.App):
            CSS_PATH = "attach.tcss"
            BINDINGS = [
                _tapp.Binding("p", "pause", "Pause"),
                _tapp.Binding("r", "resume", "Resume"),
                _tapp.Binding("s", "stop", "Stop"),
                _tapp.Binding("t", "takeover", "Takeover"),
                _tapp.Binding("i", "inject", "Inject"),
                _tapp.Binding("q", "quit_app", "Detach+Quit"),
            ]

            def __init__(
                self,
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
                issue_label: str,
            ) -> None:
                super().__init__()
                self._reader = reader
                self._writer = writer
                self._issue_label = issue_label
                self._reader_task: asyncio.Task[None] | None = None

            def compose(self) -> _tapp.ComposeResult:
                yield _tw.Header()
                with _tcont.VerticalScroll(id="transcript"):
                    yield _tw.RichLog(id="events", highlight=True)
                yield _tw.Footer()

            async def on_mount(self) -> None:
                self._reader_task = asyncio.create_task(
                    _attach_socket_loop(self._reader, self._writer, self),
                )
                self.title = f"clawcodex attach — {self._issue_label}"

            def on_unmount(self) -> None:
                if self._reader_task and not self._reader_task.done():
                    self._reader_task.cancel()
                # Best-effort detach so the runner cleans up the session.
                try:
                    self._writer.write(
                        (json.dumps({"cmd": "detach", "payload": ""}) + "\n")
                        .encode("utf-8"),
                    )
                except Exception:
                    pass
                try:
                    self._writer.close()
                except Exception:
                    pass

            def on__real_attach_message(
                self, message: _RealAttachMessage,
            ) -> None:
                self._render_frame(message.frame)

            def _render_frame(self, frame: dict) -> None:
                try:
                    log = self.query_one("#events", _tw.RichLog)
                except Exception:
                    return
                t = frame.get("type", "")
                data = frame.get("data", {})
                if t == "TextDelta":
                    log.write(data.get("content", ""))
                elif t == "ToolCallEvent":
                    log.write(
                        f"[bold cyan]→ {data.get('tool_name')}[/] "
                        f"(id={data.get('tool_use_id')}) {data.get('params')}"
                    )
                elif t == "ToolResultEvent":
                    err = data.get("result", {}).get("is_error")
                    tag = "[red]ERR[/]" if err else "[green]OK[/]"
                    log.write(
                        f"  {tag} {data.get('tool_name')} "
                        f"(id={data.get('tool_use_id')})"
                    )
                elif t == "__disconnected__":
                    log.write(
                        "[bold yellow]⚠ socket closed — agent has exited[/]",
                    )

            async def action_pause(self) -> None:
                await _send_cmd(self._writer, "pause")
                self.notify("Sent: pause")

            async def action_resume(self) -> None:
                await _send_cmd(self._writer, "resume")
                self.notify("Sent: resume")

            async def action_stop(self) -> None:
                await _send_cmd(self._writer, "stop")
                self.notify("Sent: stop")

            async def action_takeover(self) -> None:
                await _send_cmd(self._writer, "takeover")
                self.notify("Sent: takeover")

            async def action_inject(self) -> None:
                self.push_screen(_ModalInputScreen(self._writer))

            async def action_quit_app(self) -> None:
                await _send_cmd(self._writer, "detach")
                self.exit()

        async def _attach_socket_loop(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            app: "_RealAttachApp",
        ) -> None:
            """Read newline-delimited JSON; post a message per frame.

            On EOF (server closed), post a synthetic
            ``__disconnected__`` message so the UI shows a banner.
            """
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        app.post_message(
                            _RealAttachMessage({"type": "__disconnected__"}),
                        )
                        return
                    try:
                        frame = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    app.post_message(_RealAttachMessage(frame))
            except (
                asyncio.IncompleteReadError, ConnectionResetError,
            ):
                app.post_message(
                    _RealAttachMessage({"type": "__disconnected__"}),
                )

        return _RealAttachApp
