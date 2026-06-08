"""F-49 Phase 4 tests for the ``takeover`` CLI subcommand.

Covers:
  * ``argparse`` registration: ``clawcodex issue takeover --id X``
    parses into ``args.issue_subcommand == "takeover"``, and the
    parser also accepts ``--run`` + ``--workspace`` (sibling of
    attach / resume-session).
  * ``run()`` dispatcher routes ``takeover`` to ``_run_takeover``
    with ``(registry_path, ws, args)`` (the same signature
    _run_attach / _run_resume_session use).
  * ``_resolve_target`` returns the correct ``_TakeoverTarget`` for
    each lookup mode: ``--id`` (IssueRegistry), ``--run`` +
    ``--workspace`` (registry bypass), and the not-found / no-run-id
    / no-workspace-path / no-registry negative cases.

The Step 2 slice covers only the parser, dispatcher, and resolver.
Socket send + REPL spawn + end-to-end land in Steps 3 and 4.

Uses ``unittest.TestCase`` (the resolver / parser are sync) and
``tempfile.TemporaryDirectory`` for IssueRegistry isolation.
Patches ``extensions.orchestrator.issue_registry``'s default path
so the test does not touch the user's real ``~/.clawcodex``.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import unittest
from contextlib import redirect_stderr
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from extensions.orchestrator.cli.takeover import (
    _TakeoverTarget,
    _resolve_target,
    _run_takeover,
    _run_takeover_async,
    _send_pause_and_takeover,
)
from extensions.orchestrator.control_socket import (
    ControlCommand,
    ControlSocket,
)
from extensions.orchestrator.issue_registry import (
    IssueRecord,
    IssueRegistry,
    IssueStatus,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _write_registry(path: Path, record: IssueRecord) -> None:
    """Write a single-record IssueRegistry JSON file at ``path``.

    The on-disk format is ``{issue_id: record_dict}`` — see
    ``IssueRegistry._save`` for the canonical shape. ``status`` is
    serialised as its ``.value`` so the loader's
    ``IssueStatus(v)`` round-trip succeeds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {record.issue_id: asdict(record)}
    data[record.issue_id]["status"] = record.status.value
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _make_record(
    issue_id: str = "42",
    issue_identifier: str = "owner/repo#42",
    run_id: str | None = "run-abc",
    workspace_path: str | None = "/tmp/ws",
) -> IssueRecord:
    """Build an IssueRecord for tests."""
    return IssueRecord(
        issue_id=issue_id,
        issue_identifier=issue_identifier,
        status=IssueStatus.RUNNING,
        branch_name="f-49-takeover-test",
        base_branch="main",
        workspace_path=workspace_path,
        workspace_strategy="worktree",
        run_id=run_id,
    )


# ------------------------------------------------------------------
# Parser registration
# ------------------------------------------------------------------


class TestTakeoverParser(unittest.TestCase):
    """The new subcommand is registered with --id, --run, --workspace."""

    def test_takeover_parser_registered(self) -> None:
        from extensions.orchestrator.cli.issue import add_issue_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="top")
        add_issue_parser(sub)
        args = parent.parse_args(["issue", "takeover", "--id", "X"])
        self.assertEqual(args.issue_subcommand, "takeover")
        self.assertEqual(args.id, "X")
        self.assertIsNone(args.run)
        self.assertIsNone(args.workspace)

    def test_takeover_parser_accepts_run_and_workspace(self) -> None:
        from extensions.orchestrator.cli.issue import add_issue_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="top")
        add_issue_parser(sub)
        args = parent.parse_args(
            ["issue", "takeover", "--run", "r-1", "--workspace", "/w"],
        )
        self.assertEqual(args.issue_subcommand, "takeover")
        self.assertIsNone(args.id)
        self.assertEqual(args.run, "r-1")
        self.assertEqual(args.workspace, "/w")

    def test_takeover_parser_allows_no_args(self) -> None:
        """Unlike the legacy version, --id is now optional — usage
        is enforced at the handler, not the parser (so --run
        alone is parseable).
        """
        from extensions.orchestrator.cli.issue import add_issue_parser

        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="top")
        add_issue_parser(sub)
        args = parent.parse_args(["issue", "takeover"])
        self.assertEqual(args.issue_subcommand, "takeover")
        self.assertIsNone(args.id)
        self.assertIsNone(args.run)
        self.assertIsNone(args.workspace)


# ------------------------------------------------------------------
# Dispatcher
# ------------------------------------------------------------------


class TestTakeoverDispatch(unittest.TestCase):
    """The ``run()`` dispatcher routes ``takeover`` to the new
    module-level ``_run_takeover`` with the (registry_path, ws,
    args) signature.
    """

    def test_dispatch_to_run_takeover(self) -> None:
        from extensions.orchestrator import cli as cli_mod
        from extensions.orchestrator.cli import issue as cli_issue

        captured: dict = {}

        def fake(registry_path, workspace_root, args) -> int:
            captured["called"] = True
            captured["registry_path"] = registry_path
            captured["workspace_root"] = workspace_root
            captured["id"] = getattr(args, "id", None)
            captured["run"] = getattr(args, "run", None)
            captured["workspace"] = getattr(args, "workspace", None)
            return 0

        with patch.object(cli_issue, "_run_takeover", side_effect=fake):
            args = argparse.Namespace(
                issue_subcommand="takeover",
                id="X",
                run=None,
                workspace=None,
            )
            rc = cli_issue.run(args)
        self.assertEqual(rc, 0)
        self.assertTrue(captured.get("called"))
        self.assertEqual(captured.get("id"), "X")
        self.assertIsNone(captured.get("run"))
        # The dispatcher passes the resolved registry path and the
        # ``--workspace`` argument through unchanged.
        self.assertEqual(
            captured.get("workspace"), args.workspace,
        )

    def test_dispatch_passes_registry_path_through(self) -> None:
        """When a registry_path is configured, the dispatcher
        forwards it to ``_run_takeover`` so the handler can look up
        the run_id via IssueRegistry.
        """
        from extensions.orchestrator.cli import issue as cli_issue

        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(registry_path, _make_record())

            captured: dict = {}

            def fake(registry_path_arg, workspace_root, args) -> int:
                captured["registry_path"] = registry_path_arg
                return 0

            with patch.object(cli_issue, "_run_takeover", side_effect=fake):
                args = argparse.Namespace(
                    issue_subcommand="takeover",
                    id="owner/repo#42",
                    run=None,
                    workspace=None,
                )
                # Inject the registry_path the way ``run()`` does.
                cli_issue._run_takeover(registry_path, Path(tmp), args)
            self.assertEqual(captured["registry_path"], registry_path)


# ------------------------------------------------------------------
# _resolve_target
# ------------------------------------------------------------------


class TestResolveTarget(unittest.TestCase):
    """The lookup helper returns the correct _TakeoverTarget or None."""

    def test_resolve_via_issue_id(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(registry_path, _make_record())
            result = _resolve_target(
                registry_path, None, "owner/repo#42", None,
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, _TakeoverTarget)
        self.assertEqual(result.run_id, "run-abc")
        self.assertEqual(result.workspace_path, Path("/tmp/ws"))
        self.assertEqual(result.issue_id, "owner/repo#42")

    def test_resolve_returns_none_for_no_run_id(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(
                registry_path, _make_record(run_id=None),
            )
            result = _resolve_target(
                registry_path, None, "owner/repo#42", None,
            )
        self.assertIsNone(result)

    def test_resolve_returns_none_for_no_workspace_path(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(
                registry_path, _make_record(workspace_path=None),
            )
            result = _resolve_target(
                registry_path, None, "owner/repo#42", None,
            )
        self.assertIsNone(result)

    def test_resolve_via_run_id_with_workspace(self) -> None:
        """--run + --workspace bypasses the registry entirely."""
        result = _resolve_target(
            None, Path("/w"), None, "run-xyz",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.run_id, "run-xyz")
        self.assertEqual(result.workspace_path, Path("/w"))
        self.assertEqual(result.issue_id, "run:run-xyz")

    def test_resolve_returns_none_when_registry_missing(self) -> None:
        result = _resolve_target(
            Path("/nonexistent/registry.json"), None, "X", None,
        )
        self.assertIsNone(result)

    def test_resolve_returns_none_for_missing_issue(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            _write_registry(registry_path, _make_record())
            result = _resolve_target(
                registry_path, None, "MISSING", None,
            )
        self.assertIsNone(result)

    def test_resolve_returns_none_when_both_args_missing(self) -> None:
        result = _resolve_target(None, None, None, None)
        self.assertIsNone(result)

    def test_resolve_via_run_id_ignores_workspace_root_when_path_given(
        self,
    ) -> None:
        """--workspace overrides the resolved workspace_root."""
        result = _resolve_target(
            None, Path("/default"), None, "run-xyz",
        )
        assert result is not None
        self.assertEqual(result.workspace_path, Path("/default"))

    def test_resolve_via_run_id_uses_workspace_arg(self) -> None:
        """When the handler passes workspace_root=Path('/explicit')
        and --workspace='explicit', the explicit one wins (because
        the handler builds ``effective_workspace`` before calling
        _resolve_target). This test guards the contract that
        _resolve_target treats ``workspace_root`` as authoritative.
        """
        result = _resolve_target(
            None, Path("/explicit"), None, "run-xyz",
        )
        assert result is not None
        self.assertEqual(result.workspace_path, Path("/explicit"))


# ------------------------------------------------------------------
# _run_takeover — Step 1 stub behaviour
# ------------------------------------------------------------------


class TestRunTakeoverStub(unittest.TestCase):
    """The Step 1 stub validates args + resolves the target +
    prints a TODO. Step 3 replaces the stub body with the socket
    + REPL flow but keeps the same exit-code contract.
    """

    def test_missing_id_and_run_returns_2(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            args = argparse.Namespace(id=None, run=None, workspace=None)
            rc = _run_takeover(None, None, args)
        self.assertEqual(rc, 2)
        self.assertIn("--id", err.getvalue())

    def test_run_without_workspace_returns_2(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            args = argparse.Namespace(
                id=None, run="r-1", workspace=None,
            )
            rc = _run_takeover(None, None, args)
        self.assertEqual(rc, 2)
        self.assertIn("--workspace", err.getvalue())

    def test_issue_not_found_returns_1(self) -> None:
        with TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "registry.json"
            # Registry exists but is empty.
            registry_path.write_text("{}", encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                args = argparse.Namespace(
                    id="MISSING", run=None, workspace=None,
                )
                rc = _run_takeover(registry_path, Path(tmp), args)
        self.assertEqual(rc, 1)
        self.assertIn("no active run", err.getvalue().lower())

    def test_run_with_no_resolution_returns_0(self) -> None:
        """--run + --workspace bypasses the registry and resolves
        cleanly. The stub-era version of this test asserted
        ``rc == 0`` based on a TODO stub; the full flow is now
        patched at the spawn layer so we can assert the same
        success code without launching a real REPL.
        """
        with patch(
            "extensions.orchestrator.cli.takeover.subprocess.call",
            return_value=0,
        ):
            err = io.StringIO()
            with redirect_stderr(err):
                args = argparse.Namespace(
                    id=None, run="r-1", workspace="/w",
                )
                rc = _run_takeover(None, Path("/w"), args)
        self.assertEqual(rc, 0)


# ------------------------------------------------------------------
# _send_pause_and_takeover — Phase 1 socket
# ------------------------------------------------------------------


async def _wait_for_clients(
    cs: ControlSocket, expected: int = 1, timeout: float = 2.0,
) -> None:
    """Poll until the server has registered ``expected`` clients.

    Mirrors the helper at
    ``test_orchestrator_f49_control_socket.py`` so this test
    file is self-contained. The Unix-socket accept task races
    the client's first send, so callers that need to broadcast
    must wait for the server to register the writer.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(cs._clients) >= expected:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(
        f"Expected {expected} connected client(s), got {len(cs._clients)}",
    )


async def _drain_one(
    cs: ControlSocket, timeout: float = 2.0,
) -> ControlCommand | None:
    """Read at most one command from the control socket's queue.

    Returns the command if one arrives within ``timeout``,
    else ``None``. Mirrors the helper at
    ``test_orchestrator_f49_control_socket.py``.
    """
    async def _next() -> ControlCommand | None:
        async for cmd in cs.poll_commands():
            return cmd
        return None
    try:
        return await asyncio.wait_for(_next(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


class TestSendPauseAndTakeover(unittest.IsolatedAsyncioTestCase):
    """The socket sender writes the right verbs in the right order."""

    async def test_sends_pause_and_takeover_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            sock_path = Path(tmp) / "ctrl.sock"
            cs = ControlSocket(sock_path)
            await cs.start()
            try:
                ok = await _send_pause_and_takeover(sock_path)
                self.assertTrue(ok)
                # The sender is one-shot: it opens, sends, closes.
                # By the time we get here the server's read loop has
                # already processed the two lines and discarded the
                # writer from cs._clients. Drain the queue — that is
                # the authoritative record of what was sent.
                first = await _drain_one(cs)
                second = await _drain_one(cs)
                self.assertIsNotNone(first)
                self.assertIsNotNone(second)
                assert first is not None and second is not None
                self.assertEqual(first.cmd, "pause")
                self.assertEqual(second.cmd, "takeover")
            finally:
                await cs.stop()

    async def test_returns_false_when_socket_missing(self) -> None:
        """No socket file at the path → returns False, no exception."""
        with TemporaryDirectory() as tmp:
            sock_path = Path(tmp) / "no-such.sock"
            ok = await _send_pause_and_takeover(sock_path)
        self.assertFalse(ok)

    async def test_returns_false_on_connection_refused(self) -> None:
        """sock_path exists but no listener is bound."""
        with TemporaryDirectory() as tmp:
            sock_path = Path(tmp) / "ctrl.sock"
            sock_path.touch()  # not a socket; open_unix_connection fails
            ok = await _send_pause_and_takeover(sock_path)
        self.assertFalse(ok)


# ------------------------------------------------------------------
# _run_takeover — full flow (socket + REPL spawn)
# ------------------------------------------------------------------


class TestRunTakeoverFullFlow(unittest.IsolatedAsyncioTestCase):
    """The full flow: resolve → socket send (if alive) → REPL spawn.

    The REPL spawn is patched out (``subprocess.call``) so the
    test does not launch a real Python interpreter; the test
    asserts the right command was constructed and the right exit
    code is propagated.
    """

    async def test_socket_path_missing_spawns_repl_anyway(self) -> None:
        """If the agent has already ended (no .sock), the handler
        still spawns the REPL with ``--resume <run_id>`` against
        the on-disk transcript.
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            registry_path = tmp_path / "registry.json"
            _write_registry(
                registry_path,
                _make_record(
                    run_id="run-1",
                    workspace_path=str(workspace),
                ),
            )

            # No socket file: agent already ended.
            run_dir = workspace / ".run_control"
            run_dir.mkdir(parents=True, exist_ok=True)
            # Intentionally do NOT create the sock file.

            with patch(
                "extensions.orchestrator.cli.takeover.subprocess.call",
                return_value=0,
            ) as mock_call:
                with patch(
                    "extensions.orchestrator.cli.takeover.time.sleep",
                ) as mock_sleep:
                    args = argparse.Namespace(
                        id="owner/repo#42",
                        run=None,
                        workspace=None,
                    )
                    rc = await _run_takeover_async(
                        registry_path, tmp_path, args,
                    )
            self.assertEqual(rc, 0)
            # No socket → no pause, no quiet period.
            mock_sleep.assert_not_called()
            # REPL spawned with --resume run-1 --workspace <ws>
            self.assertEqual(mock_call.call_count, 1)
            cmd = mock_call.call_args[0][0]
            self.assertEqual(cmd[0], "python3")
            self.assertIn("--resume", cmd)
            self.assertIn("run-1", cmd)
            self.assertIn("--workspace", cmd)
            self.assertIn(str(workspace), cmd)

    async def test_socket_path_present_sends_pause_and_takeover(
        self,
    ) -> None:
        """If the .sock is alive, the handler sends pause +
        takeover over the socket, waits the quiet period, then
        spawns the REPL.
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            registry_path = tmp_path / "registry.json"
            run_id = "run-2"
            _write_registry(
                registry_path,
                _make_record(
                    run_id=run_id,
                    workspace_path=str(workspace),
                ),
            )

            # Create the socket + start the server.
            sock_path = workspace / ".run_control" / f"{run_id}.sock"
            sock_path.parent.mkdir(parents=True, exist_ok=True)
            cs = ControlSocket(sock_path)
            await cs.start()
            try:
                with patch(
                    "extensions.orchestrator.cli.takeover.subprocess.call",
                    return_value=0,
                ) as mock_call:
                    with patch(
                        "extensions.orchestrator.cli.takeover.time.sleep",
                    ) as mock_sleep:
                        args = argparse.Namespace(
                            id="owner/repo#42",
                            run=None,
                            workspace=None,
                        )
                        rc = await _run_takeover_async(
                            registry_path, tmp_path, args,
                        )
                        # Drain the queue (sender closed, so the
                        # server's read loop has already discarded
                        # the writer — no need to wait for clients).
                        first = await _drain_one(cs)
                        second = await _drain_one(cs)
            finally:
                await cs.stop()

        self.assertEqual(rc, 0)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.cmd, "pause")
        self.assertEqual(second.cmd, "takeover")
        # Quiet period called with the default 1.5s.
        mock_sleep.assert_called_once()
        self.assertAlmostEqual(mock_sleep.call_args[0][0], 1.5, places=2)
        # REPL spawned with --resume <run_id>.
        self.assertEqual(mock_call.call_count, 1)
        cmd = mock_call.call_args[0][0]
        self.assertIn("--resume", cmd)
        self.assertIn(run_id, cmd)

    async def test_run_mode_resolves_via_run_id(self) -> None:
        """--run + --workspace bypasses the registry; the
        handler still sends pause + takeover over the resolved
        socket and spawns the REPL.
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            run_id = "run-3"
            sock_path = workspace / ".run_control" / f"{run_id}.sock"
            sock_path.parent.mkdir(parents=True, exist_ok=True)
            cs = ControlSocket(sock_path)
            await cs.start()
            try:
                with patch(
                    "extensions.orchestrator.cli.takeover.subprocess.call",
                    return_value=0,
                ):
                    with patch(
                        "extensions.orchestrator.cli.takeover.time.sleep",
                    ):
                        args = argparse.Namespace(
                            id=None,
                            run=run_id,
                            workspace=str(workspace),
                        )
                        rc = await _run_takeover_async(
                            None, tmp_path, args,
                        )
                        first = await _drain_one(cs)
                        second = await _drain_one(cs)
            finally:
                await cs.stop()

        self.assertEqual(rc, 0)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.cmd, "pause")
        self.assertEqual(second.cmd, "takeover")


# ------------------------------------------------------------------
# End-to-end: socket + transcript persistence + REPL spawn
# ------------------------------------------------------------------


class TestTakeoverEndToEnd(unittest.IsolatedAsyncioTestCase):
    """Full integration: orchestrator writes a transcript →
    takeover sends pause+takeover over the live socket →
    spawns the ``--resume`` REPL against the on-disk
    ``transcript.jsonl``.

    Mirrors the round-trip exercised by
    ``test_orchestrator_f49_resume.py::TestResumeSessionEndToEnd``
    (orchestrator writes a transcript; resume-session CLI
    reads it) but for the takeover flow specifically: the
    REPL is spawned with ``--resume <run_id>`` so its
    inputs would land in the same transcript.
    """

    async def test_socket_pause_then_resume_repl_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "ws"
            workspace.mkdir()
            sessions_dir = tmp_path / "sessions"
            run_id = "run-f49-takeover-e2e"
            sock_path = workspace / ".run_control" / f"{run_id}.sock"

            # 1. Orchestrator side: write a transcript the way
            #    the headless agent would. Patches
            #    ``src.services.session_storage.SESSIONS_DIR``
            #    so the storage writes into our tmp dir.
            from src.services.session_storage import SessionStorage
            from src.types.messages import (
                AssistantMessage,
                UserMessage,
                message_to_dict,
            )

            with patch(
                "src.services.session_storage.SESSIONS_DIR",
                sessions_dir,
            ):
                storage = SessionStorage(
                    session_id=run_id, sessions_dir=sessions_dir,
                )
                storage.init_metadata(
                    model="claude-sonnet-4-20250514",
                    cwd=str(workspace),
                    title="orchestrator-takeover-e2e",
                )
                storage.write_raw(
                    message_to_dict(
                        UserMessage(
                            content=[{
                                "type": "text",
                                "text": "fix the bug in takeover",
                            }],
                        ),
                    ),
                )
                storage.write_raw(
                    message_to_dict(
                        AssistantMessage(
                            content=[{
                                "type": "text",
                                "text": "Reading the relevant file.",
                            }],
                            model="claude-sonnet-4-20250514",
                        ),
                    ),
                )
                storage.flush()
                self.assertTrue(
                    (sessions_dir / run_id / "transcript.jsonl").exists(),
                )
                self.assertTrue(
                    (sessions_dir / run_id / "metadata.json").exists(),
                )

            # 2. Control socket: start a real ``ControlSocket``
            #    in the workspace's ``.run_control`` directory.
            sock_path.parent.mkdir(parents=True, exist_ok=True)
            cs = ControlSocket(sock_path)
            await cs.start()

            # 3. Run the takeover. Patch ``subprocess.call`` so
            #    the REPL does not actually launch, and assert
            #    the right argv was constructed.
            try:
                with patch(
                    "extensions.orchestrator.cli.takeover.subprocess.call",
                    return_value=0,
                ) as mock_call:
                    with patch(
                        "extensions.orchestrator.cli.takeover.time.sleep",
                    ):
                        args = argparse.Namespace(
                            id=None,
                            run=run_id,
                            workspace=str(workspace),
                        )
                        rc = await _run_takeover_async(
                            None, tmp_path, args,
                        )
                        # Drain the two control commands the
                        # sender wrote to the queue.
                        first = await _drain_one(cs)
                        second = await _drain_one(cs)
            finally:
                await cs.stop()

            # 4. Verify: takeover returned 0, the socket saw
            #    ``pause`` then ``takeover`` in that order, and
            #    the patched ``subprocess.call`` was invoked
            #    with the ``--resume <run_id> --workspace <ws>``
            #    argv the REPL would use.
            self.assertEqual(rc, 0)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            assert first is not None and second is not None
            self.assertEqual(first.cmd, "pause")
            self.assertEqual(second.cmd, "takeover")
            self.assertEqual(mock_call.call_count, 1)
            argv = mock_call.call_args[0][0]
            self.assertEqual(argv[0], "python3")
            self.assertIn("--resume", argv)
            self.assertIn(run_id, argv)
            self.assertIn("--workspace", argv)
            self.assertIn(str(workspace), argv)
            self.assertEqual(mock_call.call_args[1]["cwd"], str(workspace))


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
