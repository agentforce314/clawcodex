"""Chapter C5 Part 2 — the Monitor tool (streaming + backpressure).

Port of MonitorTool.ts: stream a shell command's stdout to the model as
~1s notifications. The novel + critical piece (the tools-round critic deferred
the first attempt because it lacked this): BACKPRESSURE — a monitor producing
too many notifications is auto-stopped, bounding its conversation footprint.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tool_system.tools.monitor import (
    MONITOR_TOOL_NAME,
    MonitorTool,
    _stream_output,
)
from src.utils.message_queue_manager import (
    clear_pending_notifications,
    peek_pending_notifications,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_pending_notifications()
    yield
    clear_pending_notifications()


class _Reg:
    """A runtime-tasks stub whose status the test controls."""
    def __init__(self, status="running"):
        self._status = status
        self.killed = False

    def get(self, task_id):
        return SimpleNamespace(status=self._status, output_path="x")

    def stop(self):
        self._status = "completed"


def test_registered_and_named():
    assert MonitorTool.name == "Monitor" == MONITOR_TOOL_NAME
    assert MonitorTool.is_read_only({}) is False
    assert MonitorTool.is_concurrency_safe({}) is True  # TS: fire-and-forget spawn


class TestStreaming:
    def test_new_lines_become_notifications(self, tmp_path, monkeypatch):
        log = tmp_path / "b.log"
        log.write_text("line1\nline2\n")
        reg = _Reg(status="completed")  # already terminal → drains once + stops
        ctx = SimpleNamespace(runtime_tasks=reg)
        _stream_output(task_id="b1", output_path=str(log), context=ctx,
                       description="tail log")
        q = peek_pending_notifications()
        joined = "\n".join(str(n) for n in q)
        # a render-path-compatible <task-notification> with status=running
        assert "<task-notification>" in joined
        assert "<status>running</status>" in joined
        assert "<task-id>b1</task-id>" in joined  # parse_task_id can correlate
        assert "line1" in joined and "line2" in joined

    def test_partial_line_held_until_complete(self, tmp_path):
        log = tmp_path / "b.log"
        log.write_text("partial-no-newline")  # no \n → nothing streamed
        reg = _Reg(status="completed")
        ctx = SimpleNamespace(runtime_tasks=reg)
        _stream_output(task_id="b2", output_path=str(log), context=ctx, description="d")
        assert peek_pending_notifications() == []  # partial not emitted

    def test_metachars_escaped(self, tmp_path):
        log = tmp_path / "b.log"
        log.write_text("error: a < b && c > d\n")
        reg = _Reg(status="completed")
        ctx = SimpleNamespace(runtime_tasks=reg)
        _stream_output(task_id="b3", output_path=str(log), context=ctx, description="d")
        joined = "\n".join(str(n) for n in peek_pending_notifications())
        assert "&lt; b &amp;&amp; c &gt;" in joined  # escaped
        assert "< b &&" not in joined


class TestBackpressure:
    """THE critical guard: a monitor producing too many notifications auto-stops
    (kill + a final notice) — the tools-critic's requirement my earlier version
    lacked."""

    def test_auto_stops_after_cap(self, tmp_path, monkeypatch):
        import src.tool_system.tools.monitor as mod

        # tiny cap so the test is fast
        monkeypatch.setattr(mod, "_MONITOR_MAX_NOTIFICATIONS", 3)
        monkeypatch.setattr(mod, "_POLL_INTERVAL_S", 0.0)

        log = tmp_path / "b.log"
        # a file that keeps growing so every poll drains a new line
        killed = {"v": False}

        class _GrowingReg:
            def __init__(self):
                self._n = 0
            def get(self, task_id):
                # append a new line each poll so _drain always has output
                self._n += 1
                log.write_text("".join(f"line{i}\n" for i in range(self._n + 5)))
                return SimpleNamespace(status="running", output_path=str(log))

        monkeypatch.setattr(mod, "_kill_monitor_task",
                            lambda tid, ctx: killed.__setitem__("v", True))
        log.write_text("line0\n")
        ctx = SimpleNamespace(runtime_tasks=_GrowingReg())
        _stream_output(task_id="bk", output_path=str(log), context=ctx, description="chatty")

        assert killed["v"] is True, "backpressure did not kill the monitor"
        joined = "\n".join(str(n) for n in peek_pending_notifications())
        assert "auto-stopped" in joined and "too many events" in joined

    def test_no_auto_stop_under_cap(self, tmp_path, monkeypatch):
        import src.tool_system.tools.monitor as mod

        monkeypatch.setattr(mod, "_MONITOR_MAX_NOTIFICATIONS", 100)
        killed = {"v": False}
        monkeypatch.setattr(mod, "_kill_monitor_task",
                            lambda tid, ctx: killed.__setitem__("v", True))
        log = tmp_path / "b.log"
        log.write_text("just one line\n")
        reg = _Reg(status="completed")  # terminal after first drain
        ctx = SimpleNamespace(runtime_tasks=reg)
        _stream_output(task_id="b", output_path=str(log), context=ctx, description="quiet")
        assert killed["v"] is False  # under cap → not auto-stopped
        joined = "\n".join(str(n) for n in peek_pending_notifications())
        assert "auto-stopped" not in joined


class TestLifecycle:
    def test_stops_when_task_leaves_running(self, tmp_path):
        log = tmp_path / "b.log"
        log.write_text("a\n")
        reg = _Reg(status="completed")
        ctx = SimpleNamespace(runtime_tasks=reg)
        # returns promptly (doesn't loop forever) because status != running
        _stream_output(task_id="b", output_path=str(log), context=ctx, description="d")
        assert "a" in "\n".join(str(n) for n in peek_pending_notifications())

    def test_stops_when_task_evicted(self, tmp_path):
        log = tmp_path / "b.log"
        log.write_text("a\n")

        class _GoneReg:
            def get(self, task_id):
                return None  # evicted
        ctx = SimpleNamespace(runtime_tasks=_GoneReg())
        _stream_output(task_id="b", output_path=str(log), context=ctx, description="d")
        # returns without hanging; the first drain may have emitted the line


class TestMonitorSafetyGuards:
    """critic C5-P2 #5: Monitor spawns via spawn_background_bash directly, so it
    must NOT be a way around the bash safety guards (hardcoded-dangerous + the
    C8 sandbox hard-gate) that live in _bash_call."""

    def _ctx(self, tmp_path):
        from src.tool_system.context import ToolContext, ToolUseOptions
        ctx = ToolContext(workspace_root=tmp_path)
        ctx.options = ToolUseOptions(tools=[])
        return ctx

    def test_dangerous_command_refused(self, tmp_path):
        from src.tool_system.errors import ToolPermissionError
        from src.tool_system.tools.monitor import _monitor_call
        # a command matching _HARDCODED_DANGEROUS_PATTERNS must be refused
        with pytest.raises(ToolPermissionError, match="dangerous"):
            _monitor_call({"command": "rm -rf /", "description": "boom"},
                          self._ctx(tmp_path))

    def test_sandbox_hard_gate_refused(self, tmp_path, monkeypatch):
        from src.settings.types import SettingsSchema
        from src.tool_system.errors import ToolPermissionError
        from src.tool_system.tools.monitor import _monitor_call

        hard = SettingsSchema.from_dict({"sandbox": {"enabled": True, "failIfUnavailable": True}})
        monkeypatch.setattr("src.settings.settings.get_settings", lambda *a, **k: hard)
        with pytest.raises(ToolPermissionError):
            _monitor_call({"command": "tail -f log", "description": "watch"},
                          self._ctx(tmp_path))


class TestSizeBound:
    """critic C5-P2 #1: a single poll batches all new lines into one
    notification, so the count cap doesn't bound a firehose — each notification
    is size-capped (tail kept + truncation marker)."""

    def test_huge_burst_truncated(self, tmp_path, monkeypatch):
        import src.tool_system.tools.monitor as mod
        monkeypatch.setattr(mod, "_MONITOR_MAX_NOTIFICATION_BYTES", 200)
        log = tmp_path / "b.log"
        log.write_text("X" * 5000 + "\nlast-line\n")  # one huge burst
        reg = _Reg(status="completed")
        ctx = SimpleNamespace(runtime_tasks=reg)
        _stream_output(task_id="b", output_path=str(log), context=ctx, description="firehose")
        joined = "\n".join(str(n) for n in peek_pending_notifications())
        assert "truncated" in joined            # the marker
        assert "last-line" in joined            # the tail is kept
        # the emitted body is bounded (not the full 5000 X's)
        assert joined.count("X") < 500


class TestRenderPathCompat:
    """critic C5-P2 major: the streamed envelope must be understood by the
    port's drain/render path — parse_task_id correlates, render_banner renders,
    and a pure-running batch is framed as streaming (not 'finished')."""

    def test_envelope_parses_and_renders(self, tmp_path):
        from src.server.task_notifications import (
            build_notification_turn,
            parse_task_id,
            render_banner,
        )
        log = tmp_path / "b.log"
        log.write_text("hello from monitor\n")
        reg = _Reg(status="completed")
        ctx = SimpleNamespace(runtime_tasks=reg)
        _stream_output(task_id="mon1", output_path=str(log), context=ctx, description="d")
        env = str(peek_pending_notifications()[0])
        # parse_task_id correlates (was None with the <monitor-output> tag)
        assert parse_task_id(env) == "mon1"
        # render_banner surfaces the streamed line (not "Background task finished")
        banner = "\n".join(render_banner(env, None))
        assert "hello from monitor" in banner
        # a pure-running batch → streaming preamble, NOT "finished"
        turn = build_notification_turn([env])
        assert "STILL RUNNING" in turn and "have finished" not in turn

    def test_finished_batch_keeps_completion_preamble(self):
        # a real completion envelope still gets the finished framing
        from src.server.task_notifications import build_notification_turn
        from src.utils.task_notification import build_shell_notification_xml
        done = build_shell_notification_xml(
            task_id="b", description="build", status="completed",
            output_file="/tmp/b.log", exit_code=0)
        turn = build_notification_turn([done])
        assert "have finished" in turn and "STILL RUNNING" not in turn
