from __future__ import annotations

import unittest

from src.permissions.modes import (
    is_default_mode,
    permission_mode_from_string,
    permission_mode_short_title,
    permission_mode_symbol,
    permission_mode_title,
)
from src.permissions.types import PERMISSION_MODES


class TestPermissionModeConfig(unittest.TestCase):
    def test_all_modes_have_title(self) -> None:
        for mode in PERMISSION_MODES:
            title = permission_mode_title(mode)
            self.assertIsInstance(title, str)
            self.assertGreater(len(title), 0)

    def test_all_modes_have_short_title(self) -> None:
        for mode in PERMISSION_MODES:
            title = permission_mode_short_title(mode)
            self.assertIsInstance(title, str)
            self.assertGreater(len(title), 0)

    def test_all_modes_have_symbol(self) -> None:
        for mode in PERMISSION_MODES:
            sym = permission_mode_symbol(mode)
            self.assertIsInstance(sym, str)


class TestPermissionModeFromString(unittest.TestCase):
    def test_valid_mode(self) -> None:
        self.assertEqual(permission_mode_from_string("default"), "default")
        self.assertEqual(permission_mode_from_string("plan"), "plan")
        self.assertEqual(permission_mode_from_string("bypassPermissions"), "bypassPermissions")
        self.assertEqual(permission_mode_from_string("dontAsk"), "dontAsk")
        self.assertEqual(permission_mode_from_string("acceptEdits"), "acceptEdits")

    def test_invalid_mode_returns_default(self) -> None:
        self.assertEqual(permission_mode_from_string("invalid"), "default")
        self.assertEqual(permission_mode_from_string(""), "default")


class TestIsDefaultMode(unittest.TestCase):
    def test_default_is_default(self) -> None:
        self.assertTrue(is_default_mode("default"))

    def test_none_is_default(self) -> None:
        self.assertTrue(is_default_mode(None))

    def test_other_modes_not_default(self) -> None:
        self.assertFalse(is_default_mode("plan"))
        self.assertFalse(is_default_mode("bypassPermissions"))
        self.assertFalse(is_default_mode("dontAsk"))


if __name__ == "__main__":
    unittest.main()
