import importlib

import pytest


class TestPhaseCImports:
    def test_hooks_registry(self):
        mod = importlib.import_module("src.hooks.registry")
        assert hasattr(mod, "AsyncHookRegistry")

    def test_hooks_ssrf(self):
        mod = importlib.import_module("src.hooks.ssrf_guard")
        assert hasattr(mod, "validate_hook_url")

    def test_hooks_exec_http(self):
        mod = importlib.import_module("src.hooks.exec_http_hook")
        assert hasattr(mod, "execute_http_hook")

    def test_hooks_exec_agent(self):
        mod = importlib.import_module("src.hooks.exec_agent_hook")
        assert hasattr(mod, "execute_agent_hook")

    def test_hooks_exec_prompt(self):
        mod = importlib.import_module("src.hooks.exec_prompt_hook")
        assert hasattr(mod, "execute_prompt_hook")

    def test_hooks_session(self):
        mod = importlib.import_module("src.hooks.session_hooks")
        assert hasattr(mod, "run_session_start_hooks")

    def test_hooks_post_sampling(self):
        mod = importlib.import_module("src.hooks.post_sampling_hooks")
        assert hasattr(mod, "run_post_sampling_hooks")

    def test_hooks_config_manager(self):
        mod = importlib.import_module("src.hooks.config_manager")
        assert hasattr(mod, "HookConfigManager")

    def test_mcp_auth(self):
        mod = importlib.import_module("src.services.mcp.auth")
        assert hasattr(mod, "McpAuthManager")

    def test_mcp_elicitation(self):
        mod = importlib.import_module("src.services.mcp.elicitation")
        assert hasattr(mod, "ElicitationHandler")

    def test_mcp_channel_permissions(self):
        mod = importlib.import_module("src.services.mcp.channel_permissions")
        assert hasattr(mod, "ChannelPermissionManager")

    def test_mcp_doctor(self):
        mod = importlib.import_module("src.services.mcp.doctor")
        assert hasattr(mod, "run_diagnostics")

    def test_compact_reactive(self):
        mod = importlib.import_module("src.services.compact.reactive_compact")
        assert hasattr(mod, "is_prompt_too_long_error")

    def test_compact_session_memory(self):
        mod = importlib.import_module("src.services.compact.session_memory_compact")
        assert hasattr(mod, "SessionMemory")

    def test_file_state_cache(self):
        mod = importlib.import_module("src.utils.file_state_cache")
        assert hasattr(mod, "FileStateCache")

    def test_file_history(self):
        mod = importlib.import_module("src.utils.file_history")
        assert hasattr(mod, "FileHistory")

    def test_git_utils(self):
        mod = importlib.import_module("src.utils.git")
        assert hasattr(mod, "get_session_diff")

    def test_skills_validation(self):
        mod = importlib.import_module("src.skills.bundled_skills")
        assert hasattr(mod, "validate_skill")
        assert hasattr(mod, "skill_from_mcp_tool")

    def test_plugin_loader(self):
        mod = importlib.import_module("src.plugins.loader")
        assert hasattr(mod, "discover_plugins")

    def test_plugin_validator(self):
        mod = importlib.import_module("src.plugins.validator")
        assert hasattr(mod, "validate_manifest")

    def test_plugin_dependency(self):
        mod = importlib.import_module("src.plugins.dependency")
        assert hasattr(mod, "resolve_dependencies")

    def test_plugin_marketplace(self):
        mod = importlib.import_module("src.plugins.marketplace")
        assert hasattr(mod, "search_marketplace")

    def test_plugin_mcp_integration(self):
        mod = importlib.import_module("src.plugins.mcp_integration")
        assert hasattr(mod, "wrap_mcp_server_as_plugin")

    def test_plugin_lsp_integration(self):
        mod = importlib.import_module("src.plugins.lsp_integration")
        assert hasattr(mod, "wrap_lsp_server_as_plugin")

    def test_hooks_init_reexports(self):
        import src.hooks as hooks
        assert hasattr(hooks, "AsyncHookRegistry")
        assert hasattr(hooks, "HookConfigManager")

    def test_plugins_init_reexports(self):
        import src.plugins as plugins
        assert hasattr(plugins, "discover_plugins")
        assert hasattr(plugins, "validate_manifest")
        assert hasattr(plugins, "resolve_dependencies")
        assert hasattr(plugins, "wrap_mcp_server_as_plugin")

    def test_skills_init_reexports(self):
        import src.skills as skills
        assert hasattr(skills, "validate_skill")
        assert hasattr(skills, "skill_from_mcp_tool")
        assert hasattr(skills, "get_bundled_skill_by_name")
