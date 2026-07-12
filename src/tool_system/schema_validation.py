from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ToolInputError


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    # Structured fields consumed by the TS-parity tool-input renderer
    # (``format_tool_validation_error``). ``kind`` mirrors the three issue
    # categories ``formatZodValidationError`` (typescript/src/utils/
    # toolErrors.ts:68) extracts from a ZodError: "missing" (invalid_type +
    # "received undefined"), "unexpected" (unrecognized_keys) and "type"
    # (remaining invalid_type). Anything else stays "other" and falls back
    # to the generic per-issue rendering.
    kind: str = "other"
    param: str = ""
    expected: str = ""
    received: str = ""


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _js_type_name(value: Any) -> str:
    """JS-flavored type name for TS-parity error text.

    ``formatZodValidationError`` reports the received type with JavaScript's
    vocabulary ("provided as `number`"); Python's int/float split must not
    leak into the message, so both map to "number".
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _child_key_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _child_index_path(path: str, idx: int) -> str:
    return f"{path}[{idx}]"


def _render_issues(issues: list[ValidationIssue]) -> str:
    rendered = "; ".join(f"{i.path}: {i.message}" for i in issues[:5])
    if len(issues) > 5:
        rendered += f"; (+{len(issues) - 5} more)"
    return rendered


def validate_json_schema(value: Any, schema: Mapping[str, Any], *, root_name: str = "input") -> None:
    issues: list[ValidationIssue] = []
    _validate(value, schema, path=root_name, issues=issues)
    if issues:
        raise ToolInputError(_render_issues(issues))


# -- Semantic coercion (TS parity) --------------------------------------------
#
# The original wraps model-facing boolean/number fields in ``semanticBoolean``
# / ``semanticNumber`` (typescript/src/utils/semanticBoolean.ts,
# semanticNumber.ts): zod ``preprocess`` steps that coerce the string literals
# "true"/"false" and decimal number literals BEFORE type validation, while the
# API-advertised schema stays a plain {"type": "boolean"|"number"}. Models —
# third-party ones especially — routinely quote scalars ("head_limit": "30",
# "-n": "true"); without the preprocess step the validator hard-rejects input
# the original accepts and the tool's own call-time coercion never runs.
#
# The port applies the coercion type-driven at the validation boundary rather
# than replicating the per-field wrappers: every schema node declaring type
# boolean/number/integer coerces. That covers the original's wrapped fields
# (Bash, FileEdit, FileRead, Grep, CronCreate, SendMessage, TaskOutput, …)
# exactly, and additionally tolerates quoted scalars on the few unwrapped
# fields (e.g. AgentTool.run_in_background) and on MCP tool schemas — a
# deliberate, tolerant-direction deviation. ``anyOf``/``oneOf`` nodes never
# coerce, mirroring the original's unwrapped unions (ConfigTool.value must
# keep "true" a string).

_NUMBER_LITERAL_RE = re.compile(r"-?\d+(\.\d+)?")


def semantic_coerce(value: Any, schema: Mapping[str, Any]) -> Any:
    """Return ``value`` with semantic string→scalar coercions applied.

    Copy-on-write: containers are rebuilt only when a nested coercion fired,
    and the input is never mutated — callers may share the original dict with
    the assistant message's recorded ``tool_use`` block, which must keep the
    model's raw input.
    """
    if not isinstance(schema, Mapping):
        return value
    if "anyOf" in schema or "oneOf" in schema:
        return value

    schema_type = schema.get("type")

    if schema_type == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return value
        out: dict[str, Any] | None = None
        for key, item in value.items():
            prop_schema = properties.get(key)
            if not isinstance(prop_schema, Mapping):
                continue
            coerced = semantic_coerce(item, prop_schema)
            if coerced is not item:
                if out is None:
                    out = dict(value)
                out[key] = coerced
        return out if out is not None else value

    if schema_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            return value
        coerced_items = [semantic_coerce(item, item_schema) for item in value]
        if any(c is not o for c, o in zip(coerced_items, value)):
            return coerced_items
        return value

    if isinstance(value, str):
        if schema_type == "boolean":
            # Exactly "true"/"false" — semanticBoolean.ts coerces nothing
            # else (JS-truthiness coercion would turn "false" into True).
            if value == "true":
                return True
            if value == "false":
                return False
        elif schema_type in ("number", "integer"):
            # Only decimal number literals, and only when finite as a JS
            # number — semanticNumber.ts's /^-?\d+(\.\d+)?$/ gate plus its
            # Number.isFinite check (a 400-digit literal overflows to
            # Infinity in JS and is NOT coerced there; mirror that).
            if _NUMBER_LITERAL_RE.fullmatch(value):
                if math.isinf(float(value)):
                    return value
                return float(value) if "." in value else int(value)

    return value


def format_tool_validation_error(tool_name: str, issues: list[ValidationIssue]) -> str:
    """Render issues the way the original renders a failed tool-input parse.

    Mirrors ``formatZodValidationError`` (typescript/src/utils/toolErrors.ts:
    68-134): the three curated categories render grouped in fixed order —
    missing, unexpected, type mismatch — under a "{tool} failed due to the
    following issue(s):" header. When no issue falls in a curated category the
    original falls back to zod's raw ``error.message`` (a JSON dump); the port
    keeps its readable per-issue rendering instead (deliberate deviation).
    """
    missing = [i for i in issues if i.kind == "missing"]
    unexpected = [i for i in issues if i.kind == "unexpected"]
    mismatched = [i for i in issues if i.kind == "type"]

    parts = [f"The required parameter `{i.param}` is missing" for i in missing]
    parts += [f"An unexpected parameter `{i.param}` was provided" for i in unexpected]
    parts += [
        f"The parameter `{i.param}` type is expected as `{i.expected}` but provided as `{i.received}`"
        for i in mismatched
    ]

    if parts:
        noun = "issues" if len(parts) > 1 else "issue"
        return f"{tool_name} failed due to the following {noun}:\n" + "\n".join(parts)

    prefixed = "; ".join(
        f"{tool_name}.{i.path}: {i.message}" if i.path else f"{tool_name}: {i.message}"
        for i in issues[:5]
    )
    if len(issues) > 5:
        prefixed += f"; (+{len(issues) - 5} more)"
    return prefixed


def validate_tool_input(tool_name: str, value: Any, schema: Mapping[str, Any]) -> Any:
    """Semantic-coerce then validate a tool input; return the input the
    pipeline should carry forward.

    The tool-dispatch counterpart of ``validate_json_schema``. Mirrors the
    original's ``tool.inputSchema.safeParse(normalizedInput)`` at
    typescript/src/services/tools/toolExecution.ts:669: the zod schema both
    coerces (semantic preprocess wrappers) and validates, and the *parsed*
    output — not the raw model input — is what flows on to hooks, permission
    checks and ``call()`` (``let processedInput = parsedInput.data``,
    toolExecution.ts:821). On failure raises ``ToolInputError`` with
    ``formatZodValidationError``-parity text.
    """
    if not isinstance(schema, Mapping):
        return value
    coerced = semantic_coerce(value, schema)
    issues: list[ValidationIssue] = []
    _validate(coerced, schema, path="", issues=issues)
    if issues:
        raise ToolInputError(format_tool_validation_error(tool_name, issues))
    return coerced


def _validate(value: Any, schema: Mapping[str, Any], *, path: str, issues: list[ValidationIssue]) -> None:
    if "oneOf" in schema:
        options = schema.get("oneOf") or []
        if any(_is_valid(value, opt) for opt in options):
            return
        issues.append(ValidationIssue(path, "does not match any allowed schema (oneOf)"))
        return

    if "anyOf" in schema:
        options = schema.get("anyOf") or []
        if any(_is_valid(value, opt) for opt in options):
            return
        issues.append(ValidationIssue(path, "does not match any allowed schema (anyOf)"))
        return

    expected_type = schema.get("type")
    if expected_type:
        if expected_type == "object":
            if not isinstance(value, dict):
                issues.append(_type_issue(path, value, "object"))
                return
            _validate_object(value, schema, path=path, issues=issues)
            return
        if expected_type == "array":
            if not isinstance(value, list):
                issues.append(_type_issue(path, value, "array"))
                return
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for idx, item in enumerate(value):
                    _validate(item, item_schema, path=_child_index_path(path, idx), issues=issues)
            return
        if expected_type == "string":
            if not isinstance(value, str):
                issues.append(_type_issue(path, value, "string"))
                return
        elif expected_type == "boolean":
            if not isinstance(value, bool):
                issues.append(_type_issue(path, value, "boolean"))
                return
        elif expected_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                issues.append(_type_issue(path, value, "number"))
                return
        elif expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                issues.append(_type_issue(path, value, "integer"))
                return

    if "enum" in schema:
        allowed = schema.get("enum") or []
        if value not in allowed:
            issues.append(ValidationIssue(path, f"expected one of {allowed!r}, got {value!r}"))
            return


def _type_issue(path: str, value: Any, expected: str) -> ValidationIssue:
    return ValidationIssue(
        path,
        f"expected {expected}, got {_type_name(value)}",
        kind="type",
        param=path,
        expected=expected,
        received=_js_type_name(value),
    )


def _validate_object(value: dict[str, Any], schema: Mapping[str, Any], *, path: str, issues: list[ValidationIssue]) -> None:
    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    additional = schema.get("additionalProperties", True)

    seen_required: set[str] = set()
    for req in required:
        if req in seen_required:
            continue
        seen_required.add(req)
        if req not in value:
            issues.append(ValidationIssue(
                path,
                f"missing required field {req!r}",
                kind="missing",
                # The original reports the full formatted path of the missing
                # key (formatValidationPath on the zod issue path).
                param=_child_key_path(path, req),
            ))

    for key, val in value.items():
        prop_schema = properties.get(key) if isinstance(properties, dict) else None
        if prop_schema is None:
            if additional is False:
                issues.append(ValidationIssue(
                    _child_key_path(path, key),
                    "unexpected field",
                    kind="unexpected",
                    # The original renders the BARE key for unrecognized_keys
                    # (it flatMaps ``err.keys``, not the issue path).
                    param=key,
                ))
            continue
        if isinstance(prop_schema, dict):
            _validate(val, prop_schema, path=_child_key_path(path, key), issues=issues)


def _is_valid(value: Any, schema: Mapping[str, Any]) -> bool:
    issues: list[ValidationIssue] = []
    _validate(value, schema, path="$", issues=issues)
    return not issues


def build_schema_not_sent_hint(tool: Any) -> str:
    """Recovery hint when a deferred tool is called without ToolSearch.

    Mirrors ``buildSchemaNotSentHint`` in
    ``typescript/src/services/tools/toolExecution.ts:579``. Deferred tools
    are sent to the API with ``defer_loading: true`` (name + description
    only) — the model must call ``ToolSearchTool`` first to load the full
    parameter schema. When the model skips that step and calls the tool
    directly, schema validation fails because the typed parameters arrive
    as raw strings. The hint nudges the model to use ToolSearch.
    """
    name = getattr(tool, "name", "this tool")
    return (
        f"\n\nHint: '{name}' is a deferred tool — its full parameter schema "
        "is loaded on demand. Call the ToolSearchTool first with this tool's "
        "name to retrieve the schema, then re-issue the call."
    )
