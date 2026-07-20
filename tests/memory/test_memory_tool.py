"""The ``Memory`` tool (src/tool_system/tools/memory.py): dispatch through
the real ToolRegistry (schema validation + permission lane), recoverable
protocol errors, terminal successes, gate staging, and the
NO_PERMISSION_TOOLS auto-allow."""

from __future__ import annotations

import json

import pytest

from src.memory import get_memory_dir, get_memory_store
from src.permissions.types import ToolPermissionContext
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolCall
from src.tool_system.registry import ToolRegistry
from src.tool_system.tools.memory import MemoryTool
from src.utils.abort_controller import AbortController


@pytest.fixture()
def registry() -> ToolRegistry:
    return ToolRegistry([MemoryTool])


@pytest.fixture()
def context(tmp_path) -> ToolContext:
    return ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="default"),
        abort_controller=AbortController(),
    )


def _call(registry, context, tool_input):
    return registry.dispatch(
        ToolCall(name="Memory", input=tool_input, tool_use_id="tu_1"), context
    )


class TestDispatch:
    def test_add_via_registry(self, registry, context):
        result = _call(registry, context, {
            "action": "add", "target": "memory", "content": "User prefers uv",
        })
        assert not result.is_error
        assert result.output["success"] and result.output["done"]
        assert (get_memory_dir() / "MEMORY.md").read_text(encoding="utf-8") == "User prefers uv"

    def test_batch_via_registry(self, registry, context):
        _call(registry, context, {"action": "add", "target": "memory", "content": "old fact"})
        result = _call(registry, context, {
            "target": "memory",
            "operations": [
                {"action": "replace", "old_text": "old fact", "content": "new fact"},
                {"action": "add", "content": "second fact"},
            ],
        })
        assert result.output["success"]
        store = get_memory_store()
        store.load_from_disk()
        assert store.memory_entries == ["new fact", "second fact"]

    def test_auto_allowed_no_permission_prompt(self, registry, context):
        # No permission_handler wired: an "ask" would fail closed. The call
        # succeeding proves Memory resolves allow (NO_PERMISSION_TOOLS).
        result = _call(registry, context, {
            "action": "add", "target": "user", "content": "Name is Sam",
        })
        assert not result.is_error and result.output["success"]

    def test_invalid_target_rejected_by_schema(self, registry, context):
        # Enum violations raise ToolInputError at the registry's schema
        # validation, before the tool body runs; the query pipeline formats
        # that into the model-facing error result.
        from src.tool_system.errors import ToolInputError

        with pytest.raises(ToolInputError):
            _call(registry, context, {"action": "add", "target": "memory2", "content": "x"})

    def test_unknown_action_rejected_by_schema(self, registry, context):
        from src.tool_system.errors import ToolInputError

        with pytest.raises(ToolInputError):
            _call(registry, context, {"action": "read", "target": "memory"})


class TestRecoverableErrors:
    def test_missing_old_text_returns_inventory_not_error(self, registry, context):
        _call(registry, context, {"action": "add", "target": "memory", "content": "only entry"})
        result = _call(registry, context, {"action": "remove", "target": "memory"})
        # Protocol response, not a tool failure: is_error False, success False.
        assert not result.is_error
        assert result.output["success"] is False
        assert result.output["current_entries"] == ["only entry"]
        assert "Reissue the remove" in result.output["error"]

    def test_over_budget_add_not_error(self, registry, context, monkeypatch):
        from src.settings import settings as settings_mod

        class _S:
            memory_store_enabled = True
            memory_char_limit = 30
            user_char_limit = 30

        monkeypatch.setattr(settings_mod, "get_settings", lambda **kw: _S())
        from src.memory import reset_memory_store_cache

        reset_memory_store_cache()
        _call(registry, context, {"action": "add", "target": "memory", "content": "short"})
        result = _call(registry, context, {
            "action": "add", "target": "memory", "content": "x" * 40,
        })
        assert not result.is_error
        assert result.output["success"] is False
        assert "current_entries" in result.output


class TestGateStaging:
    def test_gate_on_stages_instead_of_committing(self, registry, context, monkeypatch):
        import src.memory.write_approval as wa

        monkeypatch.setattr(wa, "write_approval_enabled", lambda: True)
        result = _call(registry, context, {
            "action": "add", "target": "memory", "content": "gated entry",
        })
        assert result.output["success"] and result.output["staged"] is True
        assert result.output["pending_id"]
        # Nothing committed to the store file.
        assert not (get_memory_dir() / "MEMORY.md").exists()
        records = wa.list_pending()
        assert len(records) == 1
        assert records[0]["payload"]["content"] == "gated entry"
        assert records[0]["origin"] == "foreground"

    def test_background_origin_recorded(self, registry, context, monkeypatch):
        import src.memory.write_approval as wa
        from src.memory import (
            BACKGROUND_REVIEW,
            reset_current_write_origin,
            set_current_write_origin,
        )

        monkeypatch.setattr(wa, "write_approval_enabled", lambda: True)
        token = set_current_write_origin(BACKGROUND_REVIEW)
        try:
            _call(registry, context, {
                "action": "add", "target": "memory", "content": "bg entry",
            })
        finally:
            reset_current_write_origin(token)
        assert wa.list_pending()[0]["origin"] == "background_review"


class TestSchema:
    def test_tool_registered_in_defaults(self):
        from src.tool_system.defaults import build_default_registry

        reg = build_default_registry()
        assert reg.get("Memory") is not None

    def test_schema_shape(self):
        schema = MemoryTool.input_schema
        assert schema["required"] == ["target"]
        props = schema["properties"]
        assert set(props) == {"action", "target", "content", "old_text", "operations"}
        assert props["action"]["enum"] == ["add", "replace", "remove"]

    def test_disabled_by_setting(self, monkeypatch):
        from src.settings import settings as settings_mod

        class _S:
            memory_store_enabled = False

        monkeypatch.setattr(settings_mod, "get_settings", lambda **kw: _S())
        assert MemoryTool.is_enabled() is False

    def test_result_serializes_to_json(self, registry, context):
        result = _call(registry, context, {
            "action": "add", "target": "memory", "content": "roundtrip",
        })
        assert json.loads(json.dumps(result.output))["success"] is True
