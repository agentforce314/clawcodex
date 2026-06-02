"""Tests for the downstream CLI dispatch module (clawcodex_ext.cli.dispatch)."""

from __future__ import annotations

import sys


def test_run_cli_version_short_circuit(monkeypatch):
    """--version short-circuits without loading TUI/REPL."""
    from clawcodex_ext.cli.dispatch import run_cli

    # Ensure clean state.
    for name in list(sys.modules.keys()):
        if name in ('src.tui.app', 'src.repl.core', 'src'):
            sys.modules.pop(name, None)

    # Also clear src.entrypoints.tui which may have cached src reference.
    for name in list(sys.modules.keys()):
        if name.startswith('src.entrypoints.tui'):
            sys.modules.pop(name, None)

    rc = run_cli(['clawcodex', '--version'])
    assert rc == 0
    assert 'src.tui.app' not in sys.modules
    assert 'src.repl.core' not in sys.modules


def test_run_cli_with_args_config_skips_run_pre_action(monkeypatch):
    """--config calls show_config and skips run_pre_action."""
    from clawcodex_ext.cli.dispatch import run_cli

    pre_action_called = []
    def fake_run_pre_action(args):
        pre_action_called.append(args)

    monkeypatch.setattr('src.init.run_pre_action', fake_run_pre_action)
    monkeypatch.setattr('src.cli.show_config', lambda: 0)

    rc = run_cli(['clawcodex', '--config'])
    # --config short-circuits before run_pre_action
    assert pre_action_called == []
    assert rc == 0


def test_run_cli_default_invocation_calls_repl_via_src_cli(monkeypatch):
    """Default (no flags) reaches REPLFrontend which creates ClawcodexREPL."""
    from clawcodex_ext.cli.dispatch import run_cli
    import src.entrypoints.tui as tui_module

    init_calls = []
    repl_calls = []

    monkeypatch.setattr('src.init.run_pre_action', lambda args: init_calls.append(args))
    monkeypatch.setattr('clawcodex_ext.cli.permissions.resolve_permission_state', lambda args: None)
    monkeypatch.setattr(tui_module, 'should_use_tui', lambda explicit: False)

    # Patch ClawcodexREPL.__init__ to capture kwargs, and .run to be a no-op.
    original_init = None

    def fake_repl_init(self, **kwargs):
        repl_calls.append(kwargs)
        # Don't call real run() — just return silently
        def noop_run():
            return 0
        self.run = noop_run

    monkeypatch.setattr('src.repl.ClawcodexREPL.__init__', fake_repl_init)

    rc = run_cli(['clawcodex'])
    assert rc == 0
    assert len(init_calls) == 1, f"expected 1 init call, got {init_calls}"
    assert len(repl_calls) == 1, f"expected 1 repl call, got {repl_calls}"
    assert repl_calls[0]['permission_mode'] == 'default'


def test_run_cli_permission_flags_resolved(monkeypatch):
    """With --dangerously-skip-permissions, RuntimeContext is built with bypass_available=True."""
    from clawcodex_ext.cli.dispatch import run_cli

    # Ensure src.entrypoints.tui is imported before patching.
    import src.entrypoints.tui as tui_ref

    repl_calls = []
    built_options = []

    monkeypatch.setattr('src.init.run_pre_action', lambda args: None)
    monkeypatch.setattr(tui_ref, 'should_use_tui', lambda explicit: False)

    # Patch RuntimeContext.build to capture options and still allow REPL to run
    original_build = None

    def capture_runtime_build(cls, options):
        built_options.append(options)
        return original_build(cls, options)

    def fake_repl_init(self, **kwargs):
        repl_calls.append(kwargs)
        def noop_run():
            return 0
        self.run = noop_run

    # Load modules first
    from clawcodex_ext.runtime.context import RuntimeContext
    original_build = RuntimeContext.build.__func__
    monkeypatch.setattr('clawcodex_ext.runtime.context.RuntimeContext.build', classmethod(lambda cls, opts: capture_runtime_build(cls, opts)))

    monkeypatch.setattr('src.repl.ClawcodexREPL.__init__', fake_repl_init)

    rc = run_cli(['clawcodex', '--dangerously-skip-permissions'])
    assert rc == 0
    assert len(repl_calls) == 1, f"expected 1 repl call, got {repl_calls}"
    # With the real resolve_permission_state (not patched), --dangerously-skip-permissions
    # sets args._resolved_is_bypass_available = True, which propagates to
    # RuntimeOptions.is_bypass_permissions_mode_available = True
    assert len(built_options) == 1
    assert built_options[0].is_bypass_permissions_mode_available is True


def test_run_cli_provider_model_fast_paths_skip_pre_action(monkeypatch, capsys):
    from clawcodex_ext.cli.dispatch import run_cli

    pre_action_calls = []
    monkeypatch.setattr('src.init.run_pre_action', lambda args: pre_action_calls.append(args))
    monkeypatch.setattr('src.config.set_default_provider', lambda provider: None)
    monkeypatch.setattr(
        'src.config.get_provider_config',
        lambda provider: {'api_key': 'secret', 'base_url': 'https://custom.example'},
    )
    monkeypatch.setattr('src.config.set_api_key', lambda *args, **kwargs: None)

    provider_rc = run_cli(['clawcodex', 'provider', 'use', 'glm'])
    provider_out = capsys.readouterr().out
    model_rc = run_cli(['clawcodex', 'model', 'use', 'zai/glm-4', '--provider', 'glm'])
    model_out = capsys.readouterr().out

    assert provider_rc == 0
    assert model_rc == 0
    assert pre_action_calls == []
    assert 'Default provider set to: glm' in provider_out
    assert 'Default model for glm set to: zai/glm-4' in model_out


def test_run_cli_model_flag_value_provider_does_not_route_as_subcommand(monkeypatch):
    from clawcodex_ext.cli.dispatch import run_cli
    import src.entrypoints.tui as tui_module

    init_calls = []
    repl_calls = []
    monkeypatch.setattr('src.init.run_pre_action', lambda args: init_calls.append(args))
    monkeypatch.setattr('clawcodex_ext.cli.permissions.resolve_permission_state', lambda args: None)
    monkeypatch.setattr(tui_module, 'should_use_tui', lambda explicit: False)

    def fake_repl_init(self, **kwargs):
        repl_calls.append(kwargs)
        self.run = lambda: 0

    monkeypatch.setattr('src.repl.ClawcodexREPL.__init__', fake_repl_init)

    rc = run_cli(['clawcodex', '--model', 'claude-sonnet-4-6'])

    assert rc == 0
    assert len(init_calls) == 1
    assert len(repl_calls) == 1
    assert repl_calls[0]['provider'].model == 'claude-sonnet-4-6'


def test_run_cli_schedule_get_and_run_fast_paths(tmp_path, monkeypatch, capsys):
    from clawcodex_ext.cli.dispatch import run_cli
    from clawcodex_ext.cron_system.tasks import add_cron_task

    task = add_cron_task(tmp_path, cron="*/5 * * * *", prompt="ping", durable=True, created_at=1_000)
    monkeypatch.chdir(tmp_path)

    get_rc = run_cli(['clawcodex', 'schedule', 'get', task.id])
    get_output = capsys.readouterr().out
    run_rc = run_cli(['clawcodex', 'schedule', 'run', task.id])
    run_output = capsys.readouterr().out

    assert get_rc == 0
    assert f"Trigger: {task.id}" in get_output
    assert "Prompt: ping" in get_output
    assert run_rc == 0
    assert f"Trigger {task.id} fired." in run_output
    assert "Run ID:" in run_output



def test_build_parser_produces_functional_parser():
    """build_parser() returns a parser that handles permission flags."""
    from clawcodex_ext.cli.parser import build_parser

    parser = build_parser()
    args = parser.parse_args(['--dangerously-skip-permissions', '--permission-mode', 'plan'])
    assert args.dangerously_skip_permissions is True
    assert args.permission_mode == 'plan'


def test_build_parser_handles_allow_dangerously_skip(monkeypatch):
    """build_parser() accepts --allow-dangerously-skip-permissions."""
    from clawcodex_ext.cli.parser import build_parser

    parser = build_parser()
    args = parser.parse_args(['--allow-dangerously-skip-permissions'])
    assert args.allow_dangerously_skip_permissions is True


def test_resolve_permission_state_sets_args_attributes(monkeypatch):
    """resolve_permission_state stashes _resolved_permission_mode on args."""
    from argparse import Namespace
    from clawcodex_ext.cli.permissions import resolve_permission_state

    # Mock the safety gate and permission mode resolution
    monkeypatch.setattr('src.permissions.dangerous_safety.enforce_dangerous_skip_permissions_safety', lambda bypass_requested: None)
    monkeypatch.setattr('src.permissions.modes.initial_permission_mode_from_cli', lambda permission_mode_cli, dangerously_skip_permissions, settings_default_mode=None: 'bypassPermissions')
    monkeypatch.setattr('src.permissions.modes.has_allow_bypass_permissions_mode', lambda: False)

    args = Namespace(
        dangerously_skip_permissions=True,
        allow_dangerously_skip_permissions=False,
        permission_mode=None,
    )
    resolve_permission_state(args)
    assert args._resolved_permission_mode == 'bypassPermissions'
    assert args._resolved_is_bypass_available is True


def test_split_csv_utility():
    """_split_csv helper handles comma-separated tools."""
    from clawcodex_ext.cli.runners import _split_csv

    assert _split_csv(None) == []
    assert _split_csv('') == []
    assert _split_csv('foo') == ['foo']
    assert _split_csv('foo, bar, baz') == ['foo', 'bar', 'baz']
    assert _split_csv('foo,, bar') == ['foo', 'bar']  # empty segments skipped