"""Regression guard: shadowed-dead decoy modules must not return.

Phase 2 of the ch01 architecture refactor deleted three top-level
modules that were shadowed by same-named packages or had zero
consumers:

    src/query.py     (shadowed by src/query/)
    src/Tool.py      (zero consumers; not shadowed)
    src/models.py    (shadowed by src/models/)

Round-2 P3 then relocated all audit-only scaffolding to
``scripts/audit/``, eliminating the final shadow pair
(``src/repl.py`` ↔ ``src/repl/``). The ``KNOWN_SHADOWS_PENDING_P3``
allowlist below is now empty.

Re-introducing any decoy — or adding a *new* same-name-as-package
module — would re-create the architectural fog the ch01 gap analysis
surfaced (see ``my-docs/ch01-architecture-gap-analysis.md`` §1 TL;DR
item 1).
"""

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"


SHADOWED_DEAD_FORBIDDEN = (
    "query.py",
    "Tool.py",
    "models.py",
)


PACKAGE_NAMES = (
    "query",
    "tool_system",
    "tasks",
    "permissions",
    "providers",
    "memdir",
    "hooks",
    "state",
    "bootstrap",
    "services",
    "context_system",
    "command_system",
    "tui",
    "entrypoints",
    "transports",
    "remote",
    "server",
    "bridge",
    "models",
    "coordinator",
    "assistant",
    "buddy",
    "plugins",
    "skills",
    "schemas",
    "screens",
    "constants",
    "keybindings",
    "migrations",
    "components",
    "auth",
    "moreright",
    "compact_service",
    "voice",
    "repl",
)


KNOWN_SHADOWS_PENDING_P3: dict[str, str] = {}


@pytest.mark.parametrize("filename", SHADOWED_DEAD_FORBIDDEN)
def test_no_shadowed_dead_decoy(filename: str) -> None:
    """A previously-deleted top-level decoy module must not reappear."""
    path = SRC / filename
    assert not path.exists(), (
        f"Top-level decoy {path} was re-introduced after Phase 2 of the "
        f"ch01 architecture refactor. See my-docs/ch01-architecture-"
        f"refactoring-plan.md §P2."
    )


@pytest.mark.parametrize("pkg", PACKAGE_NAMES)
def test_no_module_shadows_package(pkg: str) -> None:
    """Same-named .py + package/ creates a silent shadow trap (the .py
    becomes unreachable via ``import src.<pkg>``). Allowlist entries
    in ``KNOWN_SHADOWS_PENDING_P3`` exempt their package; everything
    else must not shadow.
    """
    if pkg in KNOWN_SHADOWS_PENDING_P3:
        pytest.skip(f"{pkg}: allowed pending P3 — {KNOWN_SHADOWS_PENDING_P3[pkg]}")
    package_init = SRC / pkg / "__init__.py"
    same_name_module = SRC / f"{pkg}.py"
    if package_init.exists() and same_name_module.exists():
        pytest.fail(
            f"src/{pkg}.py shadows src/{pkg}/__init__.py — Python's import "
            f"resolution prefers the package, leaving {pkg}.py unreachable. "
            f"See my-docs/ch01-architecture-gap-analysis.md §1 TL;DR item 1."
        )


def test_known_shadow_allowlist_is_tight() -> None:
    """Every entry in ``KNOWN_SHADOWS_PENDING_P3`` must correspond to a
    real shadow on disk. Round-2 P3 landed: the dict is now empty and
    must stay empty unless a future plan introduces a transitional
    shadow.
    """
    for pkg in KNOWN_SHADOWS_PENDING_P3:
        package_init = SRC / pkg / "__init__.py"
        same_name_module = SRC / f"{pkg}.py"
        assert package_init.exists() and same_name_module.exists(), (
            f"KNOWN_SHADOWS_PENDING_P3 lists '{pkg}' but no shadow exists "
            f"on disk. Remove the allowlist entry."
        )
