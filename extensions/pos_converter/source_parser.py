"""SourceCodeParser — Python 源码 AST 解析，提取 SourceComponent[]。

从 Python 源码目录递归扫描 `.py` 文件，提取类定义、方法签名、
docstring、参数类型注解、import 依赖关系，输出结构化的
``list[SourceComponent]``。

设计决定：
- 不对源码做语义分析，只做结构化提取（类/方法/参数/docstring）。
- `SourceComponent`/`SourceOperation`/`ParamSpec` 是纯数据容器。
- docstring 兼容 Google / NumPy / reST 三种格式，统一降级取首段。
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class ParamSpec:
    """一个操作参数的规范描述。"""
    name: str
    type_hint: str | None = None
    default: Any | None = None
    required: bool = True
    description: str = ""


@dataclass
class SourceOperation:
    """组件内的一个可被 Agent 调用的操作。"""
    name: str
    description: str                 # 方法 docstring 首段
    parameters: list[ParamSpec] = field(default_factory=list)
    return_type: str | None = None
    source_code: str = ""            # 完整源码片段，嵌入技能参考


@dataclass
class SourceComponent:
    """一个从 Python 源码提取的「组件」——对应一个模块目录或一个类。"""
    name: str                        # "VideoOperations"
    file_path: str                   # "组件/视频算子/video_ops/video_operations.py"
    description: str                 # docstring 首段
    operations: list[SourceOperation] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # import 列表（去重本地文件）
    input_schema: dict = field(default_factory=dict)       # {name: type_hint}
    output_schema: dict = field(default_factory=dict)      # {name: type_hint}


# ---------------------------------------------------------------------------
# SourceCodeParser
# ---------------------------------------------------------------------------


class SourceCodeParser:
    """Python 源码解析器。

    输入：一个目录路径（递归扫描 ``.py`` 文件）。
    输出：``list[SourceComponent]``。

    Parameters
    ----------
    source_dir : str | Path
        源码根目录。
    exclude_patterns : list[str] | None
        要排除的文件/目录名模式（如 ``["__pycache__", "*.pyc"]``）。
    max_depth : int | None
        最大递归深度（None 表示不限制）。
    """

    _EXCLUDE_DIRS = frozenset({
        "__pycache__", ".git", "node_modules", ".venv", "venv",
        ".tox", ".egg-info", "dist", "build", "__pycache__",
    })

    def __init__(
        self,
        source_dir: str | Path,
        *,
        exclude_patterns: list[str] | None = None,
        max_depth: int | None = None,
    ) -> None:
        self._source_dir = Path(source_dir).resolve()
        self._exclude_patterns = exclude_patterns or []
        self._max_depth = max_depth
        self._parsed: list[SourceComponent] | None = None

    # ---- public API -------------------------------------------------------

    def parse(self) -> list[SourceComponent]:
        """解析源码目录，返回组件列表。"""
        if self._parsed is not None:
            return self._parsed

        if not self._source_dir.is_dir():
            raise NotADirectoryError(
                f"Source path is not a directory: {self._source_dir}"
            )

        components: list[SourceComponent] = []
        self._walk_module(self._source_dir, depth=0, components=components)

        self._parsed = components
        return self._parsed

    def parse_file(self, file_path: str | Path) -> list[SourceOperation]:
        """解析单个 Python 文件，返回操作列表。"""
        path = Path(file_path).resolve()
        if not path.is_file() or path.suffix != ".py":
            return []

        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            logger.warning("Syntax error in %s: %s", path, exc)
            return []

        lines = source.splitlines()
        operations: list[SourceOperation] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef,)):
                cls_ops = self._extract_class(path, node, lines)
                if cls_ops:
                    operations.extend(cls_ops)
            elif isinstance(node, ast.FunctionDef) and not isinstance(
                getattr(node, "parent", None), ast.ClassDef
            ):
                # Module-level function (not inside a class)
                op = self._extract_operation(node, lines)
                if op:
                    operations.append(op)

        return operations

    # ---- directory traversal ----------------------------------------------

    def _walk_module(
        self,
        dir_path: Path,
        depth: int,
        components: list[SourceComponent],
    ) -> None:
        """递归扫描目录，收集 SourceComponent。"""
        if self._max_depth is not None and depth > self._max_depth:
            return

        # Find __init__.py for package-level docstring
        init_file = dir_path / "__init__.py"
        package_desc = ""
        if init_file.is_file():
            init_source = init_file.read_text(encoding="utf-8")
            init_tree = ast.parse(init_source, filename=str(init_file))
            package_desc = self._extract_module_docstring(init_tree)

        # Gather all operations from .py files in this directory
        all_ops: list[SourceOperation] = []
        all_deps: set[str] = set()

        py_files = sorted(dir_path.glob("*.py"))
        for py_file in py_files:
            if py_file.name in ("__init__.py",) or self._should_exclude(
                py_file.name
            ):
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
                lines = source.splitlines()
            except (SyntaxError, UnicodeDecodeError) as exc:
                logger.warning("Skipping %s: %s", py_file, exc)
                continue

            # Extract imports
            file_deps = self._extract_imports(tree)
            all_deps.update(file_deps)

            # Extract class definitions
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.ClassDef):
                    ops = self._extract_class(py_file, node, lines)
                    all_ops.extend(ops)

            # Extract top-level functions
            top_ops = self._extract_top_functions(py_file, tree, lines)
            all_ops.extend(top_ops)

        # Build a component for this directory
        if all_ops:
            component_name = dir_path.name.replace("-", "_").replace(" ", "_")
            input_schema, output_schema = self._build_io_schema(all_ops)

            components.append(
                SourceComponent(
                    name=component_name,
                    file_path=str(dir_path.relative_to(
                        self._source_dir.parent
                    )),
                    description=package_desc or f"Module: {dir_path.name}",
                    operations=all_ops,
                    dependencies=sorted(all_deps),
                    input_schema=input_schema,
                    output_schema=output_schema,
                )
            )

        # Recurse into subdirectories
        for child in sorted(dir_path.iterdir()):
            if child.is_dir() and not self._should_exclude(child.name):
                self._walk_module(child, depth + 1, components)

    def _should_exclude(self, name: str) -> bool:
        """检查是否应排除此文件/目录名。"""
        if name in self._EXCLUDE_DIRS:
            return True
        for pattern in self._exclude_patterns:
            import fnmatch
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    # ---- class / function extraction --------------------------------------

    def _extract_class(
        self,
        file_path: Path,
        cls_node: ast.ClassDef,
        lines: list[str],
    ) -> list[SourceOperation]:
        """从 AST 类定义中提取所有公开方法的 SourceOperation。"""
        operations: list[SourceOperation] = []

        for node in ast.iter_child_nodes(cls_node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip private / dunder methods
                if node.name.startswith("_") and node.name not in (
                    "__init__",
                    "__call__",
                    "__enter__",
                    "__exit__",
                ):
                    continue
                op = self._extract_operation(node, lines)
                if op:
                    operations.append(op)

        return operations

    def _extract_top_functions(
        self,
        file_path: Path,
        module_node: ast.Module,
        lines: list[str],
    ) -> list[SourceOperation]:
        """提取模块级顶层函数。"""
        operations: list[SourceOperation] = []

        for node in ast.iter_child_nodes(module_node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                op = self._extract_operation(node, lines)
                if op:
                    operations.append(op)

        return operations

    def _extract_operation(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
    ) -> SourceOperation | None:
        """从 AST 函数/方法节点提取 SourceOperation。"""
        # docstring
        docstring = ast.get_docstring(node) or ""
        description, params_from_doc = self._parse_docstring(docstring)

        # Parameters from AST
        ast_params = self._infer_params(node.args)

        # Merge: docstring-derived descriptions enrich AST parameters
        doc_param_map = {p.name: p for p in params_from_doc}
        for ap in ast_params:
            if ap.name in doc_param_map:
                dp = doc_param_map[ap.name]
                if dp.description:
                    ap.description = dp.description
                if dp.type_hint and not ap.type_hint:
                    ap.type_hint = dp.type_hint

        # Return type
        return_type = self._resolve_type_hint(node.returns)

        # Source code snippet
        source_code = self._get_source_code(lines, node)

        return SourceOperation(
            name=node.name,
            description=description or node.name,
            parameters=ast_params,
            return_type=return_type,
            source_code=source_code,
        )

    # ---- docstring parsing ------------------------------------------------

    def _parse_docstring(
        self, docstring: str | None
    ) -> tuple[str, list[ParamSpec]]:
        """解析 docstring，提取首段描述和参数列表。

        兼容 Google / NumPy / reST 三种格式，降级取纯文本首段。
        """
        if not docstring:
            return "", []

        description = self._get_first_paragraph(docstring)
        params: list[ParamSpec] = []

        # Try Google style: Args: / Returns:
        params = self._parse_google_style(docstring)
        if params:
            return description, params

        # Try NumPy style: Parameters\\n---\\n
        params = self._parse_numpy_style(docstring)
        if params:
            return description, params

        # Try reST: :param name: desc
        params = self._parse_rest_style(docstring)
        if params:
            return description, params

        return description, []

    def _get_first_paragraph(self, text: str) -> str:
        """提取文本首段（遇到空行截断）。"""
        lines = text.strip().split("\n")
        para: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped and para:
                break
            if stripped:
                para.append(stripped)
        return " ".join(para).strip()

    def _parse_google_style(self, docstring: str) -> list[ParamSpec]:
        """解析 Google 风格 Args: 节。"""
        params: list[ParamSpec] = []
        lines = docstring.split("\n")

        in_args = False
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("args:"):
                in_args = True
                continue
            if in_args:
                if not stripped or stripped.startswith(
                    ("returns:", "raises:", "yields:")
                ):
                    break
                # Match "name (type): description" or "name: description"
                match = re.match(
                    r"^(\w+)\s*(?:\(([^)]*)\))?\s*:\s*(.*)", stripped
                )
                if match:
                    name, type_str, desc = match.groups()
                    params.append(
                        ParamSpec(
                            name=name,
                            type_hint=type_str.strip() if type_str else None,
                            description=desc.strip(),
                        )
                    )
        return params

    def _parse_numpy_style(self, docstring: str) -> list[ParamSpec]:
        """解析 NumPy 风格 Parameters 节。"""
        params: list[ParamSpec] = []
        lines = docstring.split("\n")

        in_params = False
        for i, line in enumerate(lines):
            if line.strip().lower() == "parameters":
                # Next line should be a separator line of dashes
                if i + 1 < len(lines) and re.match(r"^[-]+\s*$", lines[i + 1]):
                    in_params = True
                    continue
            if in_params:
                stripped = line.strip()
                if not stripped:
                    if i + 1 < len(lines) and (
                        lines[i + 1].strip().lower()
                        in ("returns", "raises", "yields", "note", "see also")
                        or re.match(r"^[-]+\s*$", lines[i + 1])
                    ):
                        break
                    continue
                # Match "name : type" or "name"
                match = re.match(r"^(\w+)\s*:\s*(.*)", stripped)
                if match:
                    name, rest = match.groups()
                    desc = rest.strip()
                    # Check if next lines are continuation of description
                    params.append(
                        ParamSpec(name=name, type_hint=desc if desc else None)
                    )
                elif params:
                    # Continuation of previous param's description
                    pass
        return params

    def _parse_rest_style(self, docstring: str) -> list[ParamSpec]:
        """解析 reST 风格 :param name: desc。"""
        params: list[ParamSpec] = []
        for line in docstring.split("\n"):
            stripped = line.strip()
            match = re.match(r":param\s+(\w+):\s*(.*)", stripped)
            if match:
                name, desc = match.groups()
                params.append(
                    ParamSpec(
                        name=name,
                        description=desc.strip(),
                    )
                )
        return params

    def _extract_module_docstring(self, tree: ast.Module) -> str:
        """提取模块级别 docstring。"""
        docstring = ast.get_docstring(tree)
        if docstring:
            return self._get_first_paragraph(docstring)
        return ""

    # ---- parameter inference ----------------------------------------------

    def _infer_params(self, args: ast.arguments) -> list[ParamSpec]:
        """从 AST arguments 节点提取参数列表。"""
        params: list[ParamSpec] = []
        defaults = [None] * (
            len(args.args) - len(args.defaults)
        ) + list(args.defaults)

        for i, arg in enumerate(args.args):
            if arg.arg == "self" or arg.arg == "cls":
                continue
            name = arg.arg
            type_hint = self._resolve_type_hint(arg.annotation)
            default = defaults[i] if i < len(defaults) else None
            required = default is None
            params.append(
                ParamSpec(
                    name=name,
                    type_hint=type_hint,
                    default=ast.unparse(default) if default is not None else None,
                    required=required,
                )
            )

        # Handle *args, **kwargs
        if args.vararg:
            params.append(
                ParamSpec(
                    name=f"*{args.vararg.arg}",
                    type_hint=self._resolve_type_hint(args.vararg.annotation),
                    required=False,
                )
            )
        if args.kwarg:
            params.append(
                ParamSpec(
                    name=f"**{args.kwarg.arg}",
                    type_hint=self._resolve_type_hint(args.kwarg.annotation),
                    required=False,
                )
            )

        return params

    @staticmethod
    def _resolve_type_hint(annotation: ast.AST | None) -> str | None:
        """将 AST 类型注解转为字符串。"""
        if annotation is None:
            return None
        try:
            return ast.unparse(annotation)
        except Exception:
            return None

    # ---- import analysis --------------------------------------------------

    def _extract_imports(self, tree: ast.Module) -> list[str]:
        """提取 import 语句中的本地模块引用。"""
        deps: list[str] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if self._is_local_import(alias.name):
                        deps.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and self._is_local_import(node.module):
                    for alias in node.names:
                        deps.append(f"{node.module}.{alias.name}")

        return deps

    def _is_local_import(self, module_name: str) -> bool:
        """判断是否为本地模块的 import（相对于 source_dir）。"""
        # Relative imports
        if module_name.startswith("."):
            return True
        # Check if module path exists relative to source_dir
        module_path = module_name.replace(".", "/")
        potential_path = self._source_dir / f"{module_path}.py"
        potential_pkg = self._source_dir / module_path / "__init__.py"
        return potential_path.exists() or potential_pkg.exists()

    # ---- source code extraction -------------------------------------------

    @staticmethod
    def _get_source_code(lines: list[str], node: ast.AST) -> str:
        """从文件行列表提取对应 AST 节点的源码片段。"""
        if hasattr(node, "lineno") and hasattr(node, "end_lineno"):
            start = node.lineno - 1
            end = node.end_lineno
            return "\n".join(lines[start:end])
        return ""

    # ---- IO schema --------------------------------------------------------

    @staticmethod
    def _build_io_schema(
        operations: list[SourceOperation],
    ) -> tuple[dict, dict]:
        """从操作列表构建输入/输出 schema。"""
        input_schema: dict[str, str] = {}
        output_schema: dict[str, str] = {}

        for op in operations:
            for param in op.parameters:
                if param.type_hint:
                    input_schema[param.name] = param.type_hint
            if op.return_type:
                output_schema[op.name] = op.return_type

        return input_schema, output_schema
