"""Static guard against Textual reserved-name collisions in ``src/tui``.

Textual's :class:`textual.widget.Widget` uses several private / reserved
names (``_render`` in particular) that MUST return a
:class:`textual.visual.Visual`. Early in the parity port we accidentally
defined helper methods called ``_render`` on custom rows that returned
a ``rich.text.Text``, which poisoned Textual's layout cache and raised
``AttributeError: 'Text' object has no attribute 'render_strips'`` deep
inside the render loop.

This test walks every ``*.py`` in ``src/tui`` and fails the build if a
class (anywhere in the tree) defines a method name that collides with
Textual internals, unless the class is explicitly allow-listed below.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Method names that Textual reserves on Widget / Screen / App. Overriding
# these without understanding the contract is almost always a bug.
RESERVED_METHODS: frozenset[str] = frozenset(
    {
        "_render",
    }
)

# Classes that legitimately override a reserved method (e.g. a Visual
# subclass that IS supposed to provide ``_render``). Kept intentionally
# empty today; add entries as ``{module_relpath}::{class_name}``.
ALLOWED_OVERRIDES: frozenset[str] = frozenset()

TUI_ROOT = Path(__file__).resolve().parents[2] / "src" / "tui"


def _iter_python_files() -> list[Path]:
    return sorted(p for p in TUI_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _collect_offences() -> list[str]:
    offences: list[str] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            rel = path.relative_to(TUI_ROOT.parent.parent).as_posix()
            key = f"{rel}::{node.name}"
            if key in ALLOWED_OVERRIDES:
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in RESERVED_METHODS:
                        offences.append(
                            f"{rel}:{item.lineno}: class {node.name} "
                            f"overrides reserved Textual method '{item.name}'"
                        )
    return offences


def test_no_textual_reserved_method_overrides() -> None:
    offences = _collect_offences()
    assert not offences, (
        "Textual reserves these method names for its render loop; "
        "rename the helper or add the class to ALLOWED_OVERRIDES if the "
        "override is intentional:\n  " + "\n  ".join(offences)
    )


if __name__ == "__main__":  # pragma: no cover - convenience
    raise SystemExit(pytest.main([__file__, "-v"]))
