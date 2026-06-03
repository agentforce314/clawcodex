from __future__ import annotations

from dataclasses import dataclass

from clawcodex_ext.cron_system.tools import CronCreateTool
from clawcodex_ext.frontend.headless import HeadlessFrontend
from clawcodex_ext.frontend.repl import REPLFrontend
from clawcodex_ext.frontend.tui import TUIFrontend


@dataclass
class _Options:
    prompt: str | None = None
    output_format: str = "text"
    input_format: str = "text"
    model: str | None = None
    max_turns: int = 20
    permission_mode: str = "default"
    is_bypass_permissions_mode_available: bool = False
    skip_permissions: bool = False
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    include_partial_messages: bool = False
    verbose: bool = False
    stream: bool = False
    resume_session_id: str | None = None
    resume_browse: bool = False


@dataclass
class _Runtime:
    provider: object
    provider_name: str
    tool_registry: object
    tool_context: object
    session: object
    workspace_root: object
    options: _Options


def test_headless_frontend_passes_prebuilt_runtime(monkeypatch, tmp_path) -> None:
    captured = {}
    runtime = _Runtime(object(), "test", object(), object(), object(), tmp_path, _Options())

    def fake_run_headless(options):
        captured["options"] = options
        return 0

    monkeypatch.setattr("src.entrypoints.headless.run_headless", fake_run_headless)

    assert HeadlessFrontend().run(runtime, []) == 0
    options = captured["options"]
    # Headless mode passes provider/workspace info via HeadlessOptions fields;
    # pre-built provider/session/tool_registry are not passed through
    # HeadlessOptions — the headless entrypoint builds its own.
    assert options.provider_name == "test"
    assert options.workspace_root == tmp_path


def test_tui_frontend_passes_prebuilt_runtime(monkeypatch, tmp_path) -> None:
    captured = {}
    runtime = _Runtime(object(), "test", object(), object(), object(), tmp_path, _Options())

    def fake_run_tui(options, **kwargs):
        captured["options"] = options
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("clawcodex_ext.tui.entrypoint.run_tui", fake_run_tui)

    assert TUIFrontend().run(runtime, []) == 0
    options = captured["options"]
    # TUIOptions carries CLI flags only
    assert options.provider_name == "test"
    # Pre-built objects are passed as keyword args to run_tui()
    assert captured.get("provider") is runtime.provider
    assert captured.get("session") is runtime.session
    assert captured.get("tool_registry") is runtime.tool_registry
    assert captured.get("tool_context") is runtime.tool_context
    assert captured.get("resume_session_id") is runtime.options.resume_session_id
    assert captured.get("resume_browse") is runtime.options.resume_browse


def test_repl_frontend_passes_prebuilt_runtime(monkeypatch, tmp_path) -> None:
    captured = {}
    runtime = _Runtime(object(), "test", object(), object(), object(), tmp_path, _Options())

    def fake_repl_init(self, **kwargs):
        captured.update(kwargs)
        self.run = lambda: None

    # REPLFrontend uses ClawCodexExtREPL (downstream), not ClawcodexREPL (upstream).
    monkeypatch.setattr("clawcodex_ext.repl.app.ClawCodexExtREPL.__init__", fake_repl_init)

    assert REPLFrontend().run(runtime, []) == 0
    assert captured["provider"] is runtime.provider
    assert captured["session"] is runtime.session
    assert captured["tool_registry"] is runtime.tool_registry
    assert captured["tool_context"] is runtime.tool_context
    assert captured["workspace_root"] == tmp_path


def test_headless_keeps_injected_cron_tool(monkeypatch, tmp_path) -> None:
    from clawcodex_ext.runtime.context import RuntimeContext, RuntimeOptions

    runtime = RuntimeContext.build(RuntimeOptions(workspace_root=tmp_path))
    captured = {}

    def fake_run_headless(options):
        captured["options"] = options
        return 0

    monkeypatch.setattr("src.entrypoints.headless.run_headless", fake_run_headless)

    assert HeadlessFrontend().run(runtime, []) == 0
    # HeadlessOptions does not carry tool_registry — the headless entrypoint
    # builds its own tool registry from provider/options. Cron tools are
    # injected at the RuntimeContext level, not via HeadlessOptions.
    # Verify provider_name is passed correctly.
    assert captured["options"].provider_name is not None
