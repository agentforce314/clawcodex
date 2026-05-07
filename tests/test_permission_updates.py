"""Tests for src/permissions/updates.py.

Mirrors the behaviors covered by ``typescript/src/utils/permissions/PermissionUpdate.ts``.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any

from src.permissions.types import (
    AdditionalWorkingDirectory,
    PermissionRuleValue,
    PermissionUpdateAddDirectories,
    PermissionUpdateAddRules,
    PermissionUpdateRemoveDirectories,
    PermissionUpdateRemoveRules,
    PermissionUpdateReplaceRules,
    PermissionUpdateSetMode,
    ToolPermissionContext,
)
from src.permissions.updates import (
    PERSISTABLE_DESTINATIONS,
    apply_permission_update,
    apply_permission_updates,
    create_read_rule_suggestion,
    extract_rules,
    has_rules,
    persist_permission_update,
    persist_permission_updates,
    supports_persistence,
)


class TestSupportsPersistence(unittest.TestCase):
    def test_persistable_destinations(self) -> None:
        for dest in ("userSettings", "projectSettings", "localSettings"):
            self.assertTrue(supports_persistence(dest))  # type: ignore[arg-type]

    def test_in_memory_destinations_not_persistable(self) -> None:
        self.assertFalse(supports_persistence("session"))  # type: ignore[arg-type]
        self.assertFalse(supports_persistence("cliArg"))  # type: ignore[arg-type]

    def test_persistable_destinations_constant(self) -> None:
        self.assertEqual(
            set(PERSISTABLE_DESTINATIONS),
            {"userSettings", "projectSettings", "localSettings"},
        )


class TestApplyPermissionUpdateSetMode(unittest.TestCase):
    def test_set_mode_changes_mode(self) -> None:
        ctx = ToolPermissionContext(mode="default")
        out = apply_permission_update(ctx, PermissionUpdateSetMode(mode="plan"))
        self.assertEqual(out.mode, "plan")

    def test_set_mode_preserves_other_fields(self) -> None:
        ctx = ToolPermissionContext(
            mode="default",
            always_allow_rules={"session": ["Read"]},
            is_bypass_permissions_mode_available=True,
        )
        out = apply_permission_update(ctx, PermissionUpdateSetMode(mode="acceptEdits"))
        self.assertEqual(out.always_allow_rules, {"session": ["Read"]})
        self.assertTrue(out.is_bypass_permissions_mode_available)

    def test_set_mode_to_internal_auto(self) -> None:
        ctx = ToolPermissionContext(mode="default")
        out = apply_permission_update(ctx, PermissionUpdateSetMode(mode="auto"))
        self.assertEqual(out.mode, "auto")

    def test_set_mode_to_internal_bubble(self) -> None:
        ctx = ToolPermissionContext(mode="default")
        out = apply_permission_update(ctx, PermissionUpdateSetMode(mode="bubble"))
        self.assertEqual(out.mode, "bubble")


class TestApplyPermissionUpdateAddRules(unittest.TestCase):
    def test_adds_rule_to_empty_slot(self) -> None:
        ctx = ToolPermissionContext()
        out = apply_permission_update(
            ctx,
            PermissionUpdateAddRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Read"),),
                behavior="allow",
            ),
        )
        self.assertEqual(out.always_allow_rules, {"session": ["Read"]})

    def test_appends_rule_preserves_existing(self) -> None:
        ctx = ToolPermissionContext(always_allow_rules={"session": ["Read"]})
        out = apply_permission_update(
            ctx,
            PermissionUpdateAddRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Edit"),),
                behavior="allow",
            ),
        )
        self.assertEqual(out.always_allow_rules["session"], ["Read", "Edit"])

    def test_deny_rule_routed_to_deny_slot(self) -> None:
        ctx = ToolPermissionContext()
        out = apply_permission_update(
            ctx,
            PermissionUpdateAddRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Bash"),),
                behavior="deny",
            ),
        )
        self.assertEqual(out.always_deny_rules, {"session": ["Bash"]})
        self.assertEqual(out.always_allow_rules, {})

    def test_rule_with_content_serialized(self) -> None:
        ctx = ToolPermissionContext()
        out = apply_permission_update(
            ctx,
            PermissionUpdateAddRules(
                destination="userSettings",
                rules=(PermissionRuleValue(tool_name="Bash", rule_content="git:*"),),
                behavior="allow",
            ),
        )
        self.assertEqual(out.always_allow_rules, {"userSettings": ["Bash(git:*)"]})


class TestApplyPermissionUpdateReplaceRules(unittest.TestCase):
    def test_replace_clears_existing(self) -> None:
        ctx = ToolPermissionContext(always_allow_rules={"session": ["Read", "Edit"]})
        out = apply_permission_update(
            ctx,
            PermissionUpdateReplaceRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Bash"),),
                behavior="allow",
            ),
        )
        self.assertEqual(out.always_allow_rules["session"], ["Bash"])

    def test_replace_with_empty_clears(self) -> None:
        ctx = ToolPermissionContext(always_allow_rules={"session": ["Read"]})
        out = apply_permission_update(
            ctx,
            PermissionUpdateReplaceRules(
                destination="session",
                rules=(),
                behavior="allow",
            ),
        )
        self.assertEqual(out.always_allow_rules["session"], [])


class TestApplyPermissionUpdateRemoveRules(unittest.TestCase):
    def test_remove_drops_matching_rule(self) -> None:
        ctx = ToolPermissionContext(always_allow_rules={"session": ["Read", "Edit"]})
        out = apply_permission_update(
            ctx,
            PermissionUpdateRemoveRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Read"),),
                behavior="allow",
            ),
        )
        self.assertEqual(out.always_allow_rules["session"], ["Edit"])

    def test_remove_missing_rule_is_noop(self) -> None:
        ctx = ToolPermissionContext(always_allow_rules={"session": ["Read"]})
        out = apply_permission_update(
            ctx,
            PermissionUpdateRemoveRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="DoesNotExist"),),
                behavior="allow",
            ),
        )
        self.assertEqual(out.always_allow_rules["session"], ["Read"])


class TestApplyPermissionUpdateDirectories(unittest.TestCase):
    def test_add_directory(self) -> None:
        ctx = ToolPermissionContext()
        out = apply_permission_update(
            ctx,
            PermissionUpdateAddDirectories(
                destination="session",
                directories=("/foo/bar",),
            ),
        )
        self.assertIn("/foo/bar", out.additional_working_directories)
        entry = out.additional_working_directories["/foo/bar"]
        self.assertIsInstance(entry, AdditionalWorkingDirectory)
        self.assertEqual(entry.path, "/foo/bar")

    def test_remove_directory(self) -> None:
        ctx = ToolPermissionContext(
            additional_working_directories={
                "/foo/bar": AdditionalWorkingDirectory(path="/foo/bar"),
                "/baz": AdditionalWorkingDirectory(path="/baz"),
            },
        )
        out = apply_permission_update(
            ctx,
            PermissionUpdateRemoveDirectories(directories=("/foo/bar",)),
        )
        self.assertNotIn("/foo/bar", out.additional_working_directories)
        self.assertIn("/baz", out.additional_working_directories)


class TestApplyPermissionUpdates(unittest.TestCase):
    def test_chain_add_then_remove(self) -> None:
        ctx = ToolPermissionContext()
        updates = [
            PermissionUpdateAddRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Read"),),
                behavior="allow",
            ),
            PermissionUpdateRemoveRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Read"),),
                behavior="allow",
            ),
        ]
        out = apply_permission_updates(ctx, updates)
        self.assertEqual(out.always_allow_rules.get("session", []), [])

    def test_chain_set_mode_then_add_rule(self) -> None:
        ctx = ToolPermissionContext(mode="default")
        updates = [
            PermissionUpdateSetMode(mode="acceptEdits"),
            PermissionUpdateAddRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Edit"),),
                behavior="allow",
            ),
        ]
        out = apply_permission_updates(ctx, updates)
        self.assertEqual(out.mode, "acceptEdits")
        self.assertEqual(out.always_allow_rules["session"], ["Edit"])


class TestExtractRules(unittest.TestCase):
    def test_only_addRules_extracted(self) -> None:
        updates = [
            PermissionUpdateAddRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Read"),),
                behavior="allow",
            ),
            PermissionUpdateReplaceRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Edit"),),
                behavior="allow",
            ),
            PermissionUpdateRemoveRules(
                destination="session",
                rules=(PermissionRuleValue(tool_name="Bash"),),
                behavior="deny",
            ),
        ]
        rules = extract_rules(updates)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].tool_name, "Read")

    def test_empty_for_none(self) -> None:
        self.assertEqual(extract_rules(None), [])

    def test_has_rules_true_only_with_addRules(self) -> None:
        self.assertTrue(has_rules([
            PermissionUpdateAddRules(
                rules=(PermissionRuleValue(tool_name="Read"),),
                behavior="allow",
            ),
        ]))
        self.assertFalse(has_rules([
            PermissionUpdateReplaceRules(
                rules=(PermissionRuleValue(tool_name="Read"),),
                behavior="allow",
            ),
        ]))


class TestCreateReadRuleSuggestion(unittest.TestCase):
    def test_root_returns_none(self) -> None:
        self.assertIsNone(create_read_rule_suggestion("/"))

    def test_absolute_path_double_slash(self) -> None:
        update = create_read_rule_suggestion("/src/app")
        self.assertIsInstance(update, PermissionUpdateAddRules)
        assert isinstance(update, PermissionUpdateAddRules)
        self.assertEqual(update.behavior, "allow")
        self.assertEqual(len(update.rules), 1)
        self.assertEqual(update.rules[0].tool_name, "Read")
        self.assertEqual(update.rules[0].rule_content, "//src/app/**")

    def test_relative_path_single_slash(self) -> None:
        update = create_read_rule_suggestion("subdir")
        assert isinstance(update, PermissionUpdateAddRules)
        self.assertEqual(update.rules[0].rule_content, "subdir/**")

    def test_destination_default_is_session(self) -> None:
        update = create_read_rule_suggestion("/src")
        assert isinstance(update, PermissionUpdateAddRules)
        self.assertEqual(update.destination, "session")

    def test_destination_override(self) -> None:
        update = create_read_rule_suggestion("/src", destination="userSettings")
        assert isinstance(update, PermissionUpdateAddRules)
        self.assertEqual(update.destination, "userSettings")


class TestPersistPermissionUpdate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.user_settings = os.path.join(self.tmpdir, "user-settings.json")

    def _resolver(self, dest: Any) -> str | None:
        if dest == "userSettings":
            return self.user_settings
        return None

    def _read(self) -> dict:
        if not os.path.isfile(self.user_settings):
            return {}
        with open(self.user_settings) as f:
            return json.load(f)

    def test_in_memory_destination_returns_false(self) -> None:
        update = PermissionUpdateAddRules(
            destination="session",
            rules=(PermissionRuleValue(tool_name="Read"),),
            behavior="allow",
        )
        self.assertFalse(persist_permission_update(
            update, settings_path_for_destination=self._resolver,
        ))

    def test_addRules_writes_file(self) -> None:
        update = PermissionUpdateAddRules(
            destination="userSettings",
            rules=(PermissionRuleValue(tool_name="Read"),),
            behavior="allow",
        )
        self.assertTrue(persist_permission_update(
            update, settings_path_for_destination=self._resolver,
        ))
        self.assertEqual(
            self._read(),
            {"permissions": {"allow": ["Read"]}},
        )

    def test_addRules_appends_without_duplicating(self) -> None:
        with open(self.user_settings, "w") as f:
            json.dump({"permissions": {"allow": ["Read"]}}, f)
        update = PermissionUpdateAddRules(
            destination="userSettings",
            rules=(PermissionRuleValue(tool_name="Read"), PermissionRuleValue(tool_name="Edit")),
            behavior="allow",
        )
        persist_permission_update(update, settings_path_for_destination=self._resolver)
        self.assertEqual(
            self._read()["permissions"]["allow"],
            ["Read", "Edit"],
        )

    def test_replaceRules_overwrites_slot(self) -> None:
        with open(self.user_settings, "w") as f:
            json.dump({"permissions": {"allow": ["Read", "Edit"]}}, f)
        update = PermissionUpdateReplaceRules(
            destination="userSettings",
            rules=(PermissionRuleValue(tool_name="Bash"),),
            behavior="allow",
        )
        persist_permission_update(update, settings_path_for_destination=self._resolver)
        self.assertEqual(self._read()["permissions"]["allow"], ["Bash"])

    def test_removeRules_normalizes_via_round_trip(self) -> None:
        # "Bash(*)" should normalize to "Bash" and match a removal request for "Bash"
        with open(self.user_settings, "w") as f:
            json.dump({"permissions": {"allow": ["Bash(*)", "Read"]}}, f)
        update = PermissionUpdateRemoveRules(
            destination="userSettings",
            rules=(PermissionRuleValue(tool_name="Bash"),),
            behavior="allow",
        )
        persist_permission_update(update, settings_path_for_destination=self._resolver)
        self.assertEqual(self._read()["permissions"]["allow"], ["Read"])

    def test_setMode_writes_default_mode(self) -> None:
        update = PermissionUpdateSetMode(destination="userSettings", mode="plan")
        persist_permission_update(update, settings_path_for_destination=self._resolver)
        self.assertEqual(self._read()["permissions"]["defaultMode"], "plan")

    def test_addDirectories_appends_unique(self) -> None:
        with open(self.user_settings, "w") as f:
            json.dump({"permissions": {"additionalDirectories": ["/foo"]}}, f)
        update = PermissionUpdateAddDirectories(
            destination="userSettings",
            directories=("/foo", "/bar"),
        )
        persist_permission_update(update, settings_path_for_destination=self._resolver)
        self.assertEqual(
            self._read()["permissions"]["additionalDirectories"],
            ["/foo", "/bar"],
        )

    def test_persist_permission_updates_returns_per_update_status(self) -> None:
        updates: list = [
            PermissionUpdateAddRules(
                destination="userSettings",
                rules=(PermissionRuleValue(tool_name="Read"),),
                behavior="allow",
            ),
            PermissionUpdateAddRules(
                destination="session",  # in-memory, not persistable
                rules=(PermissionRuleValue(tool_name="Edit"),),
                behavior="allow",
            ),
        ]
        results = persist_permission_updates(
            updates, settings_path_for_destination=self._resolver,
        )
        self.assertEqual(results, [True, False])


if __name__ == "__main__":
    unittest.main()
