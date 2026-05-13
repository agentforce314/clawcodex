"""Unit tests for ``src/migrations/__init__.py`` (plan phase 5).

Verifies the migration runner contract: registration, version
ordering, idempotency, partial-run resilience.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from src.migrations import (
    SCHEMA_VERSION_KEY,
    Migration,
    clear_migrations_for_test_only,
    get_registered_migrations,
    get_schema_version,
    register_migration,
    run_pending_migrations,
    set_schema_version,
)


@pytest.fixture(autouse=True)
def _reset_migrations():
    clear_migrations_for_test_only()
    yield
    clear_migrations_for_test_only()


class TestRegisterMigration(unittest.TestCase):
    def test_decorator_registers_migration(self) -> None:
        @register_migration(version=1, name="test_m1")
        def m1() -> None:
            pass

        registered = get_registered_migrations()
        self.assertEqual(len(registered), 1)
        self.assertEqual(registered[0].version, 1)
        self.assertEqual(registered[0].name, "test_m1")
        self.assertIs(registered[0].fn, m1)

    def test_migrations_sorted_by_version(self) -> None:
        # Register out of order; runner sorts by version.
        @register_migration(version=3, name="m3")
        def m3() -> None:
            pass

        @register_migration(version=1, name="m1")
        def m1() -> None:
            pass

        @register_migration(version=2, name="m2")
        def m2() -> None:
            pass

        registered = get_registered_migrations()
        versions = [m.version for m in registered]
        self.assertEqual(versions, [1, 2, 3])

    def test_clear_outside_pytest_raises(self) -> None:
        import os
        saved = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            with self.assertRaises(RuntimeError):
                clear_migrations_for_test_only()
        finally:
            if saved is not None:
                os.environ["PYTEST_CURRENT_TEST"] = saved


class TestSchemaVersion(unittest.TestCase):
    def test_get_returns_zero_when_unset(self) -> None:
        with mock.patch("src.config.ConfigManager.load_global", return_value={}):
            self.assertEqual(get_schema_version(), 0)

    def test_get_returns_stored_value(self) -> None:
        with mock.patch(
            "src.config.ConfigManager.load_global",
            return_value={SCHEMA_VERSION_KEY: 5},
        ):
            self.assertEqual(get_schema_version(), 5)

    def test_get_returns_zero_on_read_failure(self) -> None:
        with mock.patch(
            "src.config.ConfigManager.load_global",
            side_effect=RuntimeError("disk fail"),
        ):
            # Best-effort: read failure returns 0 rather than raising.
            self.assertEqual(get_schema_version(), 0)

    def test_set_writes_to_global_config(self) -> None:
        # Use a real ConfigManager pointing at a temp file.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "config.json"
            with mock.patch(
                "src.config.get_global_config_path",
                return_value=tmp_path,
            ):
                set_schema_version(7)
                self.assertTrue(tmp_path.exists())
                data = json.loads(tmp_path.read_text())
                self.assertEqual(data[SCHEMA_VERSION_KEY], 7)

    def test_set_failure_does_not_raise(self) -> None:
        with mock.patch(
            "src.config.ConfigManager.save_global",
            side_effect=RuntimeError("disk fail"),
        ):
            # Best-effort: must not raise.
            set_schema_version(5)


class TestRunPendingMigrations(unittest.TestCase):
    def test_no_migrations_returns_zero(self) -> None:
        with mock.patch("src.migrations.get_schema_version", return_value=0):
            self.assertEqual(run_pending_migrations(), 0)

    def test_runs_pending_migrations_in_order(self) -> None:
        call_log: list[int] = []

        @register_migration(version=1, name="m1")
        def m1() -> None:
            call_log.append(1)

        @register_migration(version=2, name="m2")
        def m2() -> None:
            call_log.append(2)

        @register_migration(version=3, name="m3")
        def m3() -> None:
            call_log.append(3)

        with mock.patch("src.migrations.get_schema_version", return_value=0), \
                mock.patch("src.migrations.set_schema_version") as mock_set:
            ran = run_pending_migrations()
            self.assertEqual(ran, 3)
            self.assertEqual(call_log, [1, 2, 3])
            # Each successful migration writes its version.
            self.assertEqual(mock_set.call_count, 3)
            mock_set.assert_any_call(1)
            mock_set.assert_any_call(2)
            mock_set.assert_any_call(3)

    def test_skips_already_run_migrations(self) -> None:
        call_log: list[int] = []

        @register_migration(version=1, name="m1")
        def m1() -> None:
            call_log.append(1)

        @register_migration(version=2, name="m2")
        def m2() -> None:
            call_log.append(2)

        # Current schema is at v1; only m2 should run.
        with mock.patch("src.migrations.get_schema_version", return_value=1), \
                mock.patch("src.migrations.set_schema_version"):
            ran = run_pending_migrations()
            self.assertEqual(ran, 1)
            self.assertEqual(call_log, [2])

    def test_failed_migration_does_not_block_later_ones(self) -> None:
        """Chapter §"The Migration System": availability beats consistency.
        A failed migration logs and is skipped; the runner continues."""
        call_log: list[int] = []

        @register_migration(version=1, name="m1")
        def m1() -> None:
            call_log.append(1)

        @register_migration(version=2, name="m2_fails")
        def m2() -> None:
            call_log.append(2)
            raise RuntimeError("simulated failure")

        @register_migration(version=3, name="m3")
        def m3() -> None:
            call_log.append(3)

        with mock.patch("src.migrations.get_schema_version", return_value=0), \
                mock.patch("src.migrations.set_schema_version") as mock_set:
            ran = run_pending_migrations()
            # m1 and m3 ran (count 2); m2 failed and is skipped.
            self.assertEqual(ran, 2)
            self.assertEqual(call_log, [1, 2, 3])  # m2 was attempted
            # Schema version written for m1 and m3, NOT m2.
            written = [args[0] for args, _ in mock_set.call_args_list]
            self.assertIn(1, written)
            self.assertNotIn(2, written)
            self.assertIn(3, written)

    def test_schema_version_updated_per_migration(self) -> None:
        """Important property: schema version is written AFTER each
        successful migration (not at the end), so a crash mid-pass
        can be resumed on the next startup."""

        @register_migration(version=1, name="m1")
        def m1() -> None:
            pass

        @register_migration(version=2, name="m2")
        def m2() -> None:
            pass

        with mock.patch("src.migrations.get_schema_version", return_value=0), \
                mock.patch("src.migrations.set_schema_version") as mock_set:
            run_pending_migrations()
            # Order matters: 1 before 2.
            calls = [args[0] for args, _ in mock_set.call_args_list]
            self.assertEqual(calls, [1, 2])


class TestSetupWiresMigrationRunner(unittest.TestCase):
    """Integration: run_production_setup includes the migration step."""

    def test_setup_calls_run_pending_migrations(self) -> None:
        from src.bootstrap.state import reset_state_for_tests
        from src.hooks.snapshot import reset_hook_snapshot_for_test_only
        reset_state_for_tests()
        reset_hook_snapshot_for_test_only()

        with mock.patch(
            "src.migrations.run_pending_migrations", return_value=0
        ) as mock_run:
            from src.setup import run_production_setup
            run_production_setup(args=None)
            mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
