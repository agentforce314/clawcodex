"""Stage 1 — 核心模块导入验证（< 2 秒）。

确保所有核心模块可导入且关键类/函数可调用。这是最基本的门禁，
防止因 import 链断裂、AttributeError 或依赖缺失导致的启动崩溃。
"""

from __future__ import annotations


class TestStage1CoreImports:
    """验证 src/ 下所有核心模块的导入性。"""

    def test_src_cli_import(self):
        import src.cli

        assert callable(src.cli.main)
        assert callable(src.cli._build_parser)

    def test_src_entrypoints_tui_import(self):
        import src.entrypoints.tui as tui

        assert hasattr(tui, "run_tui")
        assert hasattr(tui, "TUIOptions")
        assert hasattr(tui, "should_use_tui")

    def test_src_entrypoints_headless_import(self):
        import src.entrypoints.headless as hl

        assert hasattr(hl, "run_headless")
        assert hasattr(hl, "HeadlessOptions")

    def test_repl_core_import(self):
        import src.repl.core as repl_core

        assert hasattr(repl_core, "ClawcodexREPL")

    def test_agent_session_import(self):
        import src.agent.session

        assert hasattr(src.agent.session, "Session")
        assert callable(src.agent.session.Session.create)

    def test_agent_conversation_import(self):
        import src.agent.conversation

        assert hasattr(src.agent.conversation, "Conversation")
        assert callable(src.agent.conversation.Conversation)

    def test_providers_base_import(self):
        import src.providers.base

        assert hasattr(src.providers.base, "ChatResponse")

    def test_tool_system_context_import(self):
        import src.tool_system.context

        assert hasattr(src.tool_system.context, "ToolContext")

    def test_tool_system_defaults_import(self):
        import src.tool_system.defaults

        assert callable(src.tool_system.defaults.build_default_registry)

    def test_permissions_types_import(self):
        import src.permissions.types

        assert hasattr(src.permissions.types, "ToolPermissionContext")

    def test_bootstrap_state_import(self):
        import src.bootstrap.state

        assert callable(src.bootstrap.state.get_session_id)

    def test_types_messages_import(self):
        from src.types.messages import UserMessage, AssistantMessage

        assert UserMessage is not None
        assert AssistantMessage is not None

    def test_types_content_blocks_import(self):
        from src.types.content_blocks import TextBlock, ToolUseBlock

        assert TextBlock is not None
        assert ToolUseBlock is not None

    def test_types_stream_events_import(self):
        from src.types.stream_events import (
            MessageStart,
            ContentBlockStart,
            ContentBlockDelta,
            ContentBlockStop,
            MessageDelta,
            MessageStop,
        )

        assert MessageStart is not None
        assert ContentBlockStart is not None
        assert ContentBlockDelta is not None
        assert ContentBlockStop is not None
        assert MessageDelta is not None
        assert MessageStop is not None

    def test_api_query_events_import(self):
        """Query loop 事件（TextDelta / ToolCallEvent / PhaseComplete）可导入。"""
        from extensions.api.query import (
            TextDelta,
            ToolCallEvent,
            ToolResultEvent,
            PhaseComplete,
            SessionComplete,
        )

        assert TextDelta is not None
        assert ToolCallEvent is not None
        assert ToolResultEvent is not None
        assert PhaseComplete is not None
        assert SessionComplete is not None

    def test_config_manager_import(self):
        import src.config

        assert hasattr(src.config, "ConfigManager")
        assert callable(src.config.get_default_provider)

    def test_init_module_import(self):
        import src.init

        assert callable(src.init.run_pre_action)

    def test_cli_core_exit_import(self):
        from src.cli_core.exit import cli_error

        assert callable(cli_error)

    def test_command_system_import(self):
        import src.command_system

        assert hasattr(src.command_system, "get_command_registry")
        assert hasattr(src.command_system, "CommandEngine")
        assert hasattr(src.command_system, "list_commands")

    def test_skills_frontmatter_import(self):
        from src.skills.frontmatter import parse_frontmatter

        assert callable(parse_frontmatter)

    def test_prefetch_import(self):
        import src.prefetch

        assert callable(src.prefetch.get_or_start_keychain_prefetch)
