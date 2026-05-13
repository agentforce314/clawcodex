"""Schema migration runner — chapter §"The Migration System".

Mirrors TS ``typescript/src/main.tsx``'s migration sequence (many
``migrateXxxToYyy`` functions imported and invoked very early in the
boot). The chapter §"The Migration System" describes the contract:

> Each migration is a function with a version number. The system checks
> the current schema version against the highest migration version,
> runs pending migrations in order, and updates the version.
> Migrations are idempotent and fast (operating on small local files,
> not databases). The entire migration pass typically completes in
> under 5ms. If a migration fails, it logs the error and continues —
> availability beats strict consistency for local configuration.

Plan reference: ``my-docs/ch02-bootstrap-refactoring-plan.md`` Phase 5.

Migration storage: the schema version lives in the global config
(``~/.clawcodex/config.json``) under the key ``schema_version`` (int,
default 0). The migration runner reads this, finds all pending
migrations registered via :func:`register_migration`, runs them in
order, and writes the new version back.

Most TS migrations are model-alias renames (Sonnet 1m → 4.5, Opus →
Opus 1m, etc.) which don't apply to Python's provider surface. The
runner is built without porting individual migrations — they can be
added later as needed.

**Adding a new migration**: create ``src/migrations/vN_short_name.py``
with a top-level ``@register_migration(version=N, name="short_name")``
decorator, then add ``from . import vN_short_name  # noqa: F401`` to
this file's import block. The decorator-based registration is import-
time, so the submodule must be imported before ``run_pending_migrations``
runs.

**Failed-migration semantics**: a migration that *raises* is logged
and the runner moves on to the next. The failed migration's schema
version is NOT written, but subsequent successful migrations DO
update the version. Practical consequence: if v2 raises and v3
succeeds, the disk ends up at v3 — v2 is permanently skipped on
future startups. This matches the chapter's "availability beats
strict consistency" stance: a broken migration shouldn't block
future ones. Migrations must be designed so vN+1 doesn't depend on
vN's effects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

__all__ = [
    "Migration",
    "register_migration",
    "get_registered_migrations",
    "clear_migrations_for_test_only",
    "run_pending_migrations",
    "get_schema_version",
    "set_schema_version",
    "SCHEMA_VERSION_KEY",
]


_logger = logging.getLogger("clawcodex.migrations")


SCHEMA_VERSION_KEY = "schema_version"


@dataclass(frozen=True)
class Migration:
    """A single schema migration.

    Each migration has a version number (monotonically increasing)
    and a callable that runs the actual upgrade. The callable should:

    - Be idempotent (safe to re-run if the version write fails).
    - Be fast (local-file mutations only, no network).
    - Not raise on partial state (degrade gracefully).
    - Return ``None``.

    The chapter's philosophy: "availability beats strict consistency
    for local configuration." A failed migration logs an error and
    the runner continues to the next.
    """

    version: int
    name: str
    fn: Callable[[], None]


_registry: list[Migration] = []


def register_migration(version: int, name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    """Decorator: register a migration in the runner's registry.

    Usage::

        @register_migration(version=1, name="rename_opus_to_opus_1m")
        def _migrate_opus_to_opus_1m() -> None:
            ...

    Registration happens at module-import time. Migration modules
    should be imported before ``run_pending_migrations()`` is called —
    typically from this package's ``__init__`` if/when migrations are
    added. For plan-phase-5 the registry is empty by design (no
    migrations to run yet); the runner is structurally in place for
    future migrations.
    """

    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        _registry.append(Migration(version=version, name=name, fn=fn))
        # Keep the registry sorted by version so the runner can iterate
        # in order without re-sorting on every call.
        _registry.sort(key=lambda m: m.version)
        return fn

    return decorator


def get_registered_migrations() -> tuple[Migration, ...]:
    """Return the currently-registered migrations, sorted by version."""
    return tuple(_registry)


def clear_migrations_for_test_only() -> None:
    """Wipe the registry. Test-only."""
    import os
    if os.environ.get("PYTEST_CURRENT_TEST") is None:
        raise RuntimeError(
            "clear_migrations_for_test_only can only be called in tests"
        )
    _registry.clear()


def get_schema_version() -> int:
    """Read the current schema version from the global config.

    Returns ``0`` (the default for a fresh install) when the key is
    missing. Lazy import of ``ConfigManager`` avoids early-cycle risk.
    """
    try:
        from src.config import ConfigManager
        cm = ConfigManager()
        return int(cm.load_global().get(SCHEMA_VERSION_KEY, 0))
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.debug("schema version read failed: %s; assuming 0", exc)
        return 0


def set_schema_version(version: int) -> None:
    """Write the schema version to the global config.

    Best-effort: any exception is logged but doesn't crash the migration
    pass. Future migrations will re-attempt on the next startup.
    """
    try:
        from src.config import ConfigManager
        cm = ConfigManager()
        config = cm.load_global()
        config[SCHEMA_VERSION_KEY] = int(version)
        cm.save_global(config)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("schema version write failed: %s", exc)


def run_pending_migrations() -> int:
    """Run all migrations with version > current schema version.

    Returns the number of migrations that ran (0 if none pending).

    Each migration runs inside its own try/except — a failure logs
    and is skipped, matching the chapter's "availability beats strict
    consistency" stance. The schema version is updated AFTER each
    successful migration (not at the end), with two consequences:

    1. **Process-crash resilience**: if v1 succeeds, the version
       writes to disk; if the process is killed before v2 runs, the
       next startup correctly picks up at v2.
    2. **Failed-migration semantics**: if v2 raises and v3 succeeds,
       disk = v3 — v2 is NOT retried on subsequent startups. See the
       module docstring §"Failed-migration semantics" for the
       rationale.
    """
    current = get_schema_version()
    pending = [m for m in _registry if m.version > current]
    if not pending:
        return 0

    ran = 0
    for migration in pending:
        try:
            _logger.info(
                "running migration v%d (%s)",
                migration.version,
                migration.name,
            )
            migration.fn()
            set_schema_version(migration.version)
            ran += 1
        except Exception as exc:  # noqa: BLE001 — best-effort
            _logger.error(
                "migration v%d (%s) failed: %s; continuing",
                migration.version,
                migration.name,
                exc,
            )
    return ran
