# upstream_sync/core/layer_auditor.py
"""Layer dependency auditing.

Inspects source files in each configured layer and reports violations of
the import rules declared in ``upstream-sync.yaml``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from upstream_sync.config import LayerConfig, ProjectConfig


@dataclass
class Violation:
    """A single layer-import violation."""

    file: Path
    forbidden_import: str
    layer: str
    line_number: int


class LayerAuditor:
    """Audits configured layers for forbidden imports."""

    def __init__(self, config: ProjectConfig) -> None:
        self.layers = config.layers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def audit(self) -> list[Violation]:
        """Walk all configured layers and collect import violations."""
        violations: list[Violation] = []
        for layer in self.layers:
            for path in layer.paths:
                if not path.exists():
                    continue
                for py_file in path.rglob("*.py"):
                    imports = self._extract_imports(py_file)
                    for imp, lineno in imports:
                        if self._is_forbidden(imp, layer):
                            violations.append(Violation(
                                file=py_file,
                                forbidden_import=imp,
                                layer=layer.name,
                                line_number=lineno,
                            ))
        return violations

    def report(self, violations: list[Violation]) -> str:
        """Return a human-readable summary of violations."""
        if not violations:
            return "No layer violations found."
        lines = [f"Found {len(violations)} layer violation(s):\n"]
        for v in violations:
            lines.append(
                f"  [{v.layer}] {v.file}:{v.line_number} "
                f"imports '{v.forbidden_import}'"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_forbidden(self, imp: str, layer: LayerConfig) -> bool:
        """Check if an import is forbidden for the given layer."""
        # Check forbidden list first (takes precedence)
        if layer.forbidden_imports_from:
            return any(imp.startswith(f) for f in layer.forbidden_imports_from)
        # Otherwise check allowed list — if set, everything not in it is forbidden
        if layer.allowed_imports_from:
            return not any(imp.startswith(a) for a in layer.allowed_imports_from)
        return False

    def _extract_imports(self, py_file: Path) -> list[tuple[str, int]]:
        """Parse a Python file and return ``(module_name, lineno)`` pairs."""
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            return []

        imports: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module:
                    imports.append((module, node.lineno))
        return imports
