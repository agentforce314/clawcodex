"""Sandboxed execution of a model-authored Python workflow script.

Two responsibilities:

1. **Static ``meta`` extraction** — parse the script with ``ast`` and
   ``ast.literal_eval`` the top-level ``meta = {...}`` assignment *without
   executing the script* (a non-literal ``meta`` is rejected).
2. **Curated execution** — wrap the body in ``async def __workflow_main__()``,
   compile it, and run it against a namespace that injects the orchestration
   primitives + a deterministic, whitelisted standard library while denying
   filesystem / shell / network reach.

Security posture (per ``docs/workflow-engine-port-plan.md`` §3.2): the script
is *model-authored, not adversarial*, so the goal is determinism + ergonomics,
not a hard sandbox. ``exec`` with curated builtins removes ``os``/``subprocess``
/``open`` reach and forces all I/O through the injected ``agent`` primitive,
while ``time``/``random``/``datetime`` are withheld so a run is deterministic
and therefore resumable.
"""

from __future__ import annotations

import ast
import builtins as _builtins
import collections
import functools
import importlib
import itertools
import json
import math
import re
import string
import textwrap
from typing import Any, Mapping

from .errors import WorkflowMetaError
from .types import WorkflowMeta

_REQUIRED_META = ("name", "description")
_WRAPPER_NAME = "__workflow_main__"

# Deterministic stdlib modules real workflows lean on (the demo corpus uses
# json/re/math constantly). Injected as globals AND importable. Deliberately
# excludes time/random/datetime (non-determinism breaks resume) and anything
# touching the host (os/sys/subprocess/socket/pathlib).
_MODULE_WHITELIST: dict[str, Any] = {
    "json": json,
    "re": re,
    "math": math,
    "collections": collections,
    "itertools": itertools,
    "functools": functools,
    "string": string,
    "textwrap": textwrap,
}

# Builtins that are safe and commonly needed. Notably absent: __import__
# (provided separately, whitelisted), open, eval, exec, compile, input,
# globals, locals, vars, breakpoint, exit, quit, help.
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "bytes", "chr", "dict", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "getattr",
    "hasattr", "hash", "int", "isinstance", "issubclass", "iter", "len",
    "list", "map", "max", "min", "next", "ord", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
    # exception types the scripts raise / catch
    "Exception", "ValueError", "RuntimeError", "KeyError", "TypeError",
    "IndexError", "StopIteration", "ZeroDivisionError", "ArithmeticError",
    "AttributeError", "NotImplementedError", "AssertionError",
)


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    """A restricted ``__import__`` permitting only the whitelisted modules.

    Lets the model write idiomatic ``import json`` / ``from re import sub``
    while denying ``import os`` (and relative imports). The whitelisted
    modules are also pre-bound as globals, so either style works.
    """
    if level != 0:
        raise ImportError("relative imports are not allowed in workflow scripts")
    root = name.split(".")[0]
    if root not in _MODULE_WHITELIST:
        raise ImportError(f"import of '{name}' is not allowed in workflow scripts")
    return importlib.__import__(name, globals, locals, fromlist, level)


def _safe_builtins() -> dict[str, Any]:
    d: dict[str, Any] = {name: getattr(_builtins, name) for name in _SAFE_BUILTIN_NAMES}
    d["__import__"] = _safe_import
    d["True"] = True
    d["False"] = False
    d["None"] = None
    return d


# ── meta extraction ──────────────────────────────────────────────────────────


def extract_meta(source: str) -> WorkflowMeta:
    """Statically extract and validate the ``meta`` literal (no execution)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise WorkflowMetaError(f"workflow script has a syntax error: {exc}") from exc

    node = _find_meta_node(tree)
    if node is None:
        raise WorkflowMetaError(
            "workflow script must define a top-level `meta = {...}` dict literal"
        )
    try:
        raw = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError) as exc:
        raise WorkflowMetaError(
            "`meta` must be a pure literal (no variables, calls, f-strings, or comprehensions)"
        ) from exc
    if not isinstance(raw, dict):
        raise WorkflowMetaError("`meta` must be a dict")

    for key in _REQUIRED_META:
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            raise WorkflowMetaError(f"`meta` requires a non-empty string `{key}`")

    phases = raw.get("phases", []) or []
    if not isinstance(phases, list):
        raise WorkflowMetaError("`meta.phases` must be a list")

    return WorkflowMeta(
        name=raw["name"],
        description=raw["description"],
        when_to_use=raw.get("when_to_use"),
        phases=phases,
        model=raw.get("model"),
        raw=raw,
    )


def _find_meta_node(tree: ast.Module) -> ast.expr | None:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "meta":
                return value
    return None


# ── execution ────────────────────────────────────────────────────────────────


def compile_workflow(source: str):
    """Compile the script body as the body of ``async def __workflow_main__()``.

    Wrapping in an async function makes top-level ``await``, ``return`` (the
    workflow's result), and ``raise`` all valid.
    """
    body = source if source.strip() else "pass"
    wrapped = f"async def {_WRAPPER_NAME}():\n" + textwrap.indent(body, "    ")
    try:
        return compile(wrapped, "<workflow>", "exec")
    except SyntaxError as exc:
        raise WorkflowMetaError(f"workflow script failed to compile: {exc}") from exc


def build_namespace(primitives: Mapping[str, Any], args: Any) -> dict[str, Any]:
    """Assemble the global namespace the script executes in."""
    ns: dict[str, Any] = {"__builtins__": _safe_builtins()}
    ns.update(_MODULE_WHITELIST)  # json/re/math/... available without import too
    ns.update(primitives)         # agent/parallel/pipeline/phase/log/workflow/budget
    ns["args"] = args
    return ns


async def execute_workflow(source: str, primitives: Mapping[str, Any], args: Any) -> Any:
    """Compile + run the script, returning its top-level ``return`` value."""
    code = compile_workflow(source)
    namespace = build_namespace(primitives, args)
    exec(code, namespace)  # noqa: S102 — curated namespace; see module docstring
    return await namespace[_WRAPPER_NAME]()
