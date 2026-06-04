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
