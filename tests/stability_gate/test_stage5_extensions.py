"""Stage 5 — 三方扩展组件测试（< 5 秒）。

验证 clawcodex_ext/ 下扩展模块的可用性：
- CLI dispatch 入口
- Runtime context
- Frontend 插件注册
- TUI 应用层级
- REPL 扩展
- Provider / Auth 扩展
- 命令系统 / 权限 / Cron 系统
- Memory / Hooks / Settings / Skills
- Bridge 服务
- Tool system
"""

from __future__ import annotations

import sys


class TestStage5ExtCli:
    """下游 CLI 派发和入口点测试。"""

    def test_downstream_cli_main_is_callable(self):
        import clawcodex_ext.cli.main as main_mod

        assert callable(main_mod.main)

    def test_downstream_cli_dispatch_run_cli(self):
        from clawcodex_ext.cli.dispatch import run_cli

        assert callable(run_cli)

    def test_downstream_cli_parser_build(self):
        from clawcodex_ext.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["--dangerously-skip-permissions", "--permission-mode", "plan"])
        assert args.dangerously_skip_permissions is True
        assert args.permission_mode == "plan"

    def test_downstream_cli_import_is_lightweight(self):
        """导入入口模块不应该拉入重型模块。"""
        for name in ("clawcodex_ext.cli.main", "src.tui.app", "src.repl.core"):
            sys.modules.pop(name, None)

        import clawcodex_ext.cli.main as main_mod  # noqa: F811

        assert "src.tui.app" not in sys.modules
        assert "src.repl.core" not in sys.modules


class TestStage5ExtRuntime:
    """Runtime 扩展测试。"""

    def test_runtime_context_imports(self):
        from clawcodex_ext.runtime.context import RuntimeContext, RuntimeOptions

        assert RuntimeOptions is not None
        assert RuntimeContext is not None
        assert hasattr(RuntimeContext, "build")

    def test_runtime_observer_import(self):
        from clawcodex_ext.runtime.observer import RuntimeObserver

        assert RuntimeObserver is not None


class TestStage5ExtFrontend:
    """Frontend 插件注册测试。"""

    def test_frontend_plugins_registered(self):
        from clawcodex_ext.frontend import get_frontend

        for name in ("tui", "repl", "headless"):
            frontend = get_frontend(name)
            assert frontend is not None, f"Frontend {name!r} not registered"
            assert callable(frontend.run)

    def test_frontend_protocol_import(self):
        from clawcodex_ext.frontend.protocol import Frontend, FrontendPlugin

        assert Frontend is not None
        assert FrontendPlugin is not None

    def test_frontend_registry_import(self):
        from clawcodex_ext.frontend.registry import register_frontend, get_frontend, list_frontends

        assert callable(register_frontend)
        assert callable(get_frontend)
        assert callable(list_frontends)


class TestStage5ExtTui:
    """TUI 扩展测试。"""

    def test_downstream_tui_app_subclasses_upstream(self):
        from clawcodex_ext.tui.app import ClawCodexTUI as ClawCodexExtTUI
        from src.tui.app import ClawCodexTUI

        assert issubclass(ClawCodexExtTUI, ClawCodexTUI)

    def test_tui_entrypoint_imports(self):
        from clawcodex_ext.entrypoints.tui import run_tui, TUIOptions

        assert callable(run_tui)
        assert TUIOptions is not None

    def test_tui_should_use_tui_logic(self):
        from src.entrypoints.tui import should_use_tui

        result = should_use_tui(explicit=None)
        assert result in (True, False)

    def test_tui_should_use_tui_explicit_false(self):
        from src.entrypoints.tui import should_use_tui

        assert should_use_tui(explicit=False) is False


class TestStage5ExtRepl:
    """REPL 扩展测试。"""

    def test_repl_app_import(self):
        from clawcodex_ext.repl.app import ClawCodexExtREPL

        assert ClawCodexExtREPL is not None

    def test_repl_extensions_import(self):
        from clawcodex_ext.repl.core import ClawcodexREPL

        assert ClawcodexREPL is not None

    def test_repl_background_escape_import(self):
        from clawcodex_ext.repl.background_escape import BackgroundEscape

        assert BackgroundEscape is not None


class TestStage5ExtAgent:
    """Agent 扩展测试。"""

    def test_agent_session_ext_import(self):
        from clawcodex_ext.agent.session_ext import resume_session_with_tail

        assert callable(resume_session_with_tail)

    def test_background_runner_import(self):
        from clawcodex_ext.agent.background_runner import (
            launch_background_runner,
            get_background_runner_status,
        )

        assert callable(launch_background_runner)
        assert callable(get_background_runner_status)

    def test_tool_authoring_import(self):
        from clawcodex_ext.agent.tool_authoring import (
            AgentToolSpec,
            list_tools,
            add_tool,
        )

        assert AgentToolSpec is not None
        assert callable(list_tools)
        assert callable(add_tool)


class TestStage5ExtCommandSystem:
    """命令系统扩展测试。"""

    def test_command_system_engine_import(self):
        from clawcodex_ext.command_system.engine import CommandEngine

        assert CommandEngine is not None

    def test_command_system_builtins_import(self):
        from clawcodex_ext.command_system.builtins import (
            HELP_COMMAND,
            CLEAR_COMMAND,
            EXIT_COMMAND,
            get_builtin_commands,
            register_builtin_commands,
        )

        assert HELP_COMMAND is not None
        assert CLEAR_COMMAND is not None
        assert EXIT_COMMAND is not None
        assert callable(get_builtin_commands)
        assert callable(register_builtin_commands)


class TestStage5ExtProviders:
    """Provider 扩展测试。"""

    def test_providers_runtime_import(self):
        from clawcodex_ext.providers.runtime import create_provider, build_provider_from_config

        assert callable(create_provider)
        assert callable(build_provider_from_config)

    def test_providers_codex_models_import(self):
        from clawcodex_ext.providers.codex_models import get_codex_model_ids, CODEX_FALLBACK_MODELS

        assert callable(get_codex_model_ids)
        assert isinstance(CODEX_FALLBACK_MODELS, list)

    def test_providers_openai_codex_provider_import(self):
        from clawcodex_ext.providers.openai_codex_provider import OpenAICodexProvider

        assert OpenAICodexProvider is not None


class TestStage5ExtPermissions:
    """权限扩展测试。"""

    def test_permissions_cycle_import(self):
        from clawcodex_ext.permissions.cycle import (
            cycle_permission_mode,
            get_next_permission_mode,
        )

        assert callable(cycle_permission_mode)
        assert callable(get_next_permission_mode)


class TestStage5ExtCron:
    """Cron 系统扩展测试。"""

    def test_cron_subsystem_imports(self):
        from clawcodex_ext.cron_system import (
            CronTask,
            CronFields,
            CronRun,
            CronJitterConfig,
        )

        assert CronTask is not None
        assert CronFields is not None
        assert CronRun is not None
        assert CronJitterConfig is not None

    def test_cron_scheduler_import(self):
        from clawcodex_ext.cron_system.scheduler import CronScheduler

        assert CronScheduler is not None


class TestStage5ExtAuth:
    """Auth 扩展测试。"""

    def test_auth_codex_oauth_import(self):
        from clawcodex_ext.auth.codex_oauth import (
            CodexDeviceFlow,
            CodexOAuthTokens,
            login_codex_device_flow,
        )

        assert CodexDeviceFlow is not None
        assert CodexOAuthTokens is not None
        assert callable(login_codex_device_flow)

    def test_auth_codex_store_import(self):
        from clawcodex_ext.auth.codex_store import (
            CodexAuthRecord,
            CodexOAuthTokens,
            read_codex_tokens,
            save_codex_tokens,
        )

        assert CodexAuthRecord is not None
        assert CodexOAuthTokens is not None
        assert callable(read_codex_tokens)
        assert callable(save_codex_tokens)


class TestStage5ExtMemory:
    """Memory 扩展测试。"""

    def test_memory_scope_aware_prompt_import(self):
        from clawcodex_ext.memory.scope_aware_prompt import (
            build_scope_aware_memory_prompt,
            set_default_memory_scopes,
            VALID_MEMORY_SCOPES,
        )

        assert callable(build_scope_aware_memory_prompt)
        assert callable(set_default_memory_scopes)
        assert isinstance(VALID_MEMORY_SCOPES, (list, tuple, frozenset))


class TestStage5ExtHooks:
    """Hooks 扩展测试。"""

    def test_hooks_pluggy_adapter_import(self):
        from clawcodex_ext.hooks._pluggy_adapter import (
            PluggyHookManager,
            HookPluginAdapter,
            HookEvent,
            is_pluggy_available,
        )

        assert PluggyHookManager is not None
        assert HookPluginAdapter is not None
        assert HookEvent is not None
        assert callable(is_pluggy_available)


class TestStage5ExtSettings:
    """Settings 扩展测试。"""

    def test_settings_pydantic_adapter_import(self):
        from clawcodex_ext.settings.pydantic_adapter import (
            ClawCodexSettings,
            get_pydantic_settings_class,
            is_pydantic_settings_available,
        )

        assert ClawCodexSettings is not None
        assert callable(get_pydantic_settings_class)
        assert callable(is_pydantic_settings_available)


class TestStage5ExtSkills:
    """Skills 扩展测试。"""

    def test_skills_frontmatter_adapter_import(self):
        from clawcodex_ext.skills._frontmatter_adapter import (
            FrontmatterParseResult,
            parse_frontmatter_with_library,
            is_frontmatter_available,
        )

        assert FrontmatterParseResult is not None
        assert callable(parse_frontmatter_with_library)
        assert callable(is_frontmatter_available)


class TestStage5ExtToolSystem:
    """Tool system 扩展测试。"""

    def test_tool_system_tools_import(self):
        """工具模块（ask_issue_author / create_agent_tool / progress_report）可导入。"""
        from clawcodex_ext.tool_system.tools.ask_issue_author import _ask_issue_author_call
        from clawcodex_ext.tool_system.tools.create_agent_tool import make_create_agent_tool
        from clawcodex_ext.tool_system.tools.progress_report import ProgressReportTool

        assert callable(_ask_issue_author_call)
        assert callable(make_create_agent_tool)
        assert ProgressReportTool is not None


class TestStage5ExtContextSystem:
    """Context system 扩展测试。"""

    def test_context_system_prompt_assembly_import(self):
        from clawcodex_ext.context_system.prompt_assembly import (
            build_full_system_prompt,
            SystemPromptParts,
            SystemPromptSection,
        )

        assert callable(build_full_system_prompt)
        assert SystemPromptParts is not None
        assert SystemPromptSection is not None

    def test_context_system_gitpython_adapter_import(self):
        """GitPython 适配器导入。

        注意：由于 `tests/git/` 目录遮蔽了 `git` 包名，此导入可能失败。
        这是项目层级的 PYTHONPATH 问题，不是核心功能的回归。
        """
        import pytest
        import importlib
        import sys
        # 安全地检查 git 是否已加载且来自 tests/ 目录
        if 'git' in sys.modules:
            git_mod = sys.modules['git']
            git_file = getattr(git_mod, '__file__', '') or ''
            if 'tests' in git_file and 'gitpython' not in git_file.lower():
                pass  # 可能被遮蔽 — 继续下面的 try
        try:
            import git  # noqa: F401
        except ImportError:
            pytest.skip("GitPython 未安装")
        # 检查是否被 tests/git/ 遮蔽
        import git as _git_check
        if not hasattr(_git_check, 'Repo'):
            pytest.skip("PYTHONPATH 遮蔽：tests/git/ 遮盖了 GitPython (无 Repo)")
        from clawcodex_ext.context_system._gitpython_adapter import (
            GitPythonProvider,
            GitContextSnapshot,
            is_gitpython_available,
        )
        assert GitPythonProvider is not None
        assert GitContextSnapshot is not None
        assert callable(is_gitpython_available)


class TestStage5ExtUtils:
    """Utils 扩展测试。"""

    def test_utils_session_watcher_import(self):
        from clawcodex_ext.utils.session_watcher import SessionWatcher

        assert SessionWatcher is not None

    def test_utils_cache_warning_import(self):
        from clawcodex_ext.utils.cache_warning import CacheWarning

        assert CacheWarning is not None


class TestStage5ExtBridge:
    """Bridge 服务扩展测试。"""

    def test_bridge_session_import(self):
        from clawcodex_ext.services.bridge.session import BridgeSession

        assert BridgeSession is not None

    def test_bridge_transport_import(self):
        from clawcodex_ext.services.bridge.transport import BridgeTransport

        assert BridgeTransport is not None

    def test_bridge_auth_import(self):
        from clawcodex_ext.services.bridge.auth import BridgeAuth

        assert BridgeAuth is not None

    def test_tail_follower_import(self):
        from clawcodex_ext.services.tail_follower import TailFollower

        assert TailFollower is not None
