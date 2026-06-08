"""Stage 7 — 项目根目录无 src/ 阴影（无根门面 / 无 eager 根包 / 无陈旧空目录）。

背景: F-48 decoupling refactor 在 ``src/X/Y.py`` 留 ``__getattr__`` lazy proxy
门面以兼容 ``from src.X.Y import Z`` 的旧调用点是设计上的合法存在。但同
次 refactor 意外把同一份门面写到项目根 (``/X/Y.py``) — 共 14 个目录、40
个 ``.py`` 文件, 全部 untracked, 0 个 import 引用, ``pyproject.toml`` 也
未 include 根 (``packages.find = ["src*", "clawcodex_ext*", "extensions*"]``)
— 即根门面根本不被安装, 对运行时完全无效, 仅在 ``git status`` 制造噪声。

本阶段门禁防止该类问题复发: 任何根级 Python 包 (含 ``__init__.py``)
若与 ``src/`` 同名, 或任何根 ``*.py`` 文件以 ``Facade —`` 迁移 header
起头, 或任何根 ``*.py`` 顶层 ``from clawcodex_ext.X import ...`` eager
import, CI 即 fail。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

# 合法根级目录白名单 (来自 pyproject.toml packages.find include + 已知非包目录)
_ALLOWED_ROOT_DIRS = frozenset(
    {
        # 合法根级包
        "src",
        "clawcodex_ext",
        "extensions",
        "upstream_sync",
        # 非包目录 (内容 / 数据 / 工具)
        "tests",
        "scripts",
        "docs",
        "patches",
        "demos",
        "eval",
        "assets",
        "claude-code-wiki",
        # 打包 / 构建产物
        "build",
        "dist",
        "clawcodex_dev.egg-info",
        "clawcodex_dev_mind.egg-info",
        # 系统 / 缓存
        "__pycache__",
        ".git",
        ".github",
        ".claude",
        ".idea",
        ".pytest_cache",
        ".atomcode",
        ".audit_temp",
        ".port_sessions",
    }
)

# 已知的、合法的根级 ``*.py`` 真实入口 (非门面, 软断言 — 若存在则校验不被误删)
_KNOWN_LEGIT_ROOT_PY: frozenset[str] = frozenset()

# 模块级 eager import 模式: ``from clawcodex_ext.<pkg>.<mod> import ...``
_EAGER_IMPORT_RE = re.compile(
    r"^from\s+clawcodex_ext\.[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*\s+import\b"
)

_FACADE_HEADER = "Facade \u2014"  # "Facade —" with em-dash


def _iter_root_python_files():
    """遍历根处所有 ``*.py`` (限 1 层深度), 排除子目录内的 .py。"""
    for p in sorted(ROOT.iterdir()):
        if p.is_file() and p.suffix == ".py":
            yield p


def _iter_root_dirs():
    for p in sorted(ROOT.iterdir()):
        if p.is_dir():
            yield p


class TestNoRootLevelShadow:
    """项目根不应出现与 ``src/`` 同名的 Python 包、Facade 迁移 header
    或顶层 eager re-import 模式。"""

    def test_no_root_package_shadows_src(self) -> None:
        """根级 ``__init__.py`` 目录若与 ``src/<name>`` 同名, fail。"""
        violations: list[str] = []
        for d in _iter_root_dirs():
            if d.name in _ALLOWED_ROOT_DIRS:
                continue
            if not (d / "__init__.py").exists():
                continue
            if (ROOT / "src" / d.name).exists():
                violations.append(
                    f"[package] /{d.name} "
                    f"(contains __init__.py, also exists as src/{d.name})"
                )
        assert not violations, (
            "Root-level package shadowing detected "
            f"({len(violations)} violation(s)):\n  - "
            + "\n  - ".join(violations)
        )

    def test_no_root_facade_module(self) -> None:
        """根 ``*.py`` 不应以 F-48 迁移 header ``Facade —`` 起头。"""
        violations: list[str] = []
        for p in _iter_root_python_files():
            if p.name in _KNOWN_LEGIT_ROOT_PY:
                continue
            try:
                first_line = p.read_text(encoding="utf-8").splitlines()[0]
            except (IndexError, OSError):
                continue
            if first_line.startswith(_FACADE_HEADER):
                violations.append(f"[facade]   /{p.name} (starts with {first_line!r})")
        assert not violations, (
            "Root-level facade module(s) detected "
            f"({len(violations)} violation(s)):\n  - "
            + "\n  - ".join(violations)
        )

    def test_no_root_eager_import(self) -> None:
        """根 ``*.py`` 顶层不应出现 ``from clawcodex_ext.X.Y import ...``。"""
        violations: list[str] = []
        for p in _iter_root_python_files():
            if p.name in _KNOWN_LEGIT_ROOT_PY:
                continue
            try:
                source = p.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                tree = ast.parse(source, filename=str(p))
            except SyntaxError:
                continue
            for stmt in tree.body:  # 顶层 stmt
                if isinstance(stmt, ast.ImportFrom) and stmt.module:
                    if _EAGER_IMPORT_RE.match(
                        f"from {stmt.module} import {ast.unparse(stmt)}"
                    ):
                        violations.append(
                            f"[eager]    /{p.name} "
                            f"(top-level `from {stmt.module} import ...`)"
                        )
                        break
        assert not violations, (
            "Root-level eager re-import detected "
            f"({len(violations)} violation(s)):\n  - "
            + "\n  - ".join(violations)
        )

    def test_no_untracked_stale_dirs(self) -> None:
        """已知空 / 陈旧目录 (agents/, build/) 应已清理。"""
        # 仅在仓库内现存的目录进行软断言 (CI 干净 clone 可能本来就不存在)
        soft_violations: list[str] = []
        for name in ("agents", "build"):
            p = ROOT / name
            if p.exists() and p.is_dir():
                # 若存在但为空 (agents 历史情形) 或为陈旧 build/ 大目录 → 违规
                if name == "agents":
                    soft_violations.append(f"[stale]    /{name} (empty dir from refactor)")
                elif name == "build":
                    soft_violations.append(
                        f"[stale]    /{name} (26M stale build output, in .gitignore)"
                    )
        assert not soft_violations, (
            "Stale root-level directory/ies detected:\n  - "
            + "\n  - ".join(soft_violations)
        )

    def test_known_special_case_litellm_adapter_documented(self) -> None:
        """``src/providers/_litellm_adapter.py`` 是从 ``extensions.providers_ext``
        (而非 ``clawcodex_ext.providers``) 桥接的特殊情况, 被
        ``tests/provider/test_litellm_adapter.py:12`` 消费 — 须保留。"""
        shim = ROOT / "src" / "providers" / "_litellm_adapter.py"
        assert shim.exists(), (
            f"Expected compat shim still present: {shim} "
            "(consumed by tests/provider/test_litellm_adapter.py)"
        )
        content = shim.read_text(encoding="utf-8")
        assert "from extensions.providers_ext import" in content, (
            f"{shim} should re-export from extensions.providers_ext, "
            "not from clawcodex_ext.providers"
        )
        assert "from clawcodex_ext.providers" not in content, (
            f"{shim} unexpectedly re-exports from clawcodex_ext.providers "
            "— this special case exists to bridge extensions.providers_ext"
        )

    def test_known_legit_root_py_unchanged(self) -> None:
        """白名单内的根级 ``*.py`` 真实入口仍存在 (未误删)。"""
        for name in _KNOWN_LEGIT_ROOT_PY:
            p = ROOT / name
            assert p.exists(), f"Expected root-level script missing: /{name}"
