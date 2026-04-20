from __future__ import annotations

import json
import os
import tempfile
import unittest

from src.permissions.setup import (
    DangerousRuleWarning,
    PermissionSetupResult,
    persist_session_rule,
    setup_permissions,
    validate_permission_rules,
)
from src.permissions.types import PermissionRuleValue


class TestSetupPermissionsBasic(unittest.TestCase):
    def test_returns_result(self) -> None:
        result = setup_permissions()
        self.assertIsInstance(result, PermissionSetupResult)
        self.assertIsNotNone(result.context)

    def test_default_mode(self) -> None:
        result = setup_permissions(mode="default")
        self.assertEqual(result.context.mode, "default")

    def test_bypass_mode(self) -> None:
        result = setup_permissions(mode="bypassPermissions", is_bypass_available=True)
        self.assertEqual(result.context.mode, "bypassPermissions")
        self.assertTrue(result.context.is_bypass_permissions_mode_available)


class TestSetupPermissionsFromFile(unittest.TestCase):
    def test_loads_user_settings(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "permissions": {
                    "allow": ["Bash(ls*)"],
                },
            }, f)
            f.flush()
            try:
                result = setup_permissions(user_settings_path=f.name)
                self.assertTrue(len(result.context.always_allow_rules) > 0)
            finally:
                os.unlink(f.name)

    def test_dangerous_rule_warning(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "permissions": {
                    "allow": ["Bash(python*)"],
                },
            }, f)
            f.flush()
            try:
                result = setup_permissions(user_settings_path=f.name)
                self.assertTrue(len(result.warnings) > 0)
                self.assertEqual(result.warnings[0].tool_name, "Bash")
            finally:
                os.unlink(f.name)

    def test_missing_file_no_error(self) -> None:
        result = setup_permissions(user_settings_path="/nonexistent/path.json")
        self.assertIsInstance(result, PermissionSetupResult)


class TestSetupPermissionsCLI(unittest.TestCase):
    def test_cli_allow_rules(self) -> None:
        result = setup_permissions(cli_allow=["Bash(ls*)"])
        self.assertTrue(len(result.context.always_allow_rules) > 0)

    def test_cli_deny_rules(self) -> None:
        result = setup_permissions(cli_deny=["Bash"])
        self.assertTrue(len(result.context.always_deny_rules) > 0)


class TestShadowedRules(unittest.TestCase):
    def test_detects_shadowed(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "permissions": {
                    "allow": ["Bash(ls*)"],
                    "deny": ["Bash"],
                },
            }, f)
            f.flush()
            try:
                result = setup_permissions(user_settings_path=f.name)
                self.assertTrue(len(result.shadowed_rules) > 0)
            finally:
                os.unlink(f.name)


class TestPersistSessionRule(unittest.TestCase):
    def test_persists_rule(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({}, f)
            f.flush()
            try:
                rule_value = PermissionRuleValue(tool_name="Bash", rule_content="ls*")
                success = persist_session_rule(f.name, rule_value, "allow")
                self.assertTrue(success)

                with open(f.name) as rf:
                    data = json.load(rf)
                self.assertIn("permissions", data)
                self.assertIn("allow", data["permissions"])
            finally:
                os.unlink(f.name)

    def test_no_duplicates(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({}, f)
            f.flush()
            try:
                rule_value = PermissionRuleValue(tool_name="Bash", rule_content="ls*")
                persist_session_rule(f.name, rule_value, "allow")
                persist_session_rule(f.name, rule_value, "allow")

                with open(f.name) as rf:
                    data = json.load(rf)
                self.assertEqual(len(data["permissions"]["allow"]), 1)
            finally:
                os.unlink(f.name)


class TestValidatePermissionRules(unittest.TestCase):
    def test_valid_rules(self) -> None:
        rules = [
            {"tool": "Bash", "behavior": "allow"},
            {"tool": "Write", "behavior": "deny"},
        ]
        errors = validate_permission_rules(rules)
        self.assertEqual(len(errors), 0)

    def test_missing_tool(self) -> None:
        rules = [{"behavior": "allow"}]
        errors = validate_permission_rules(rules)
        self.assertGreater(len(errors), 0)
        self.assertIn("missing", errors[0].lower())

    def test_invalid_behavior(self) -> None:
        rules = [{"tool": "Bash", "behavior": "maybe"}]
        errors = validate_permission_rules(rules)
        self.assertGreater(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
