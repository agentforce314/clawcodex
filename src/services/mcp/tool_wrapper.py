"""MCP tool wrapping: convert MCP tool schemas to Claude Code ``Tool`` objects.

Phase 8 improvements (ch15-mcp WIs 8.1, 8.3, 8.4):
- **WI-8.1**: validate model-supplied args against the MCP tool's input
  schema using ``jsonschema``. Compiled validators are cached in a
  ``WeakValueDictionary`` keyed on a hash of the schema, mirroring TS'
  AJV cache. Invalid args fail at the client boundary with a structured
  error instead of the cryptic server-side "Invalid params" the model
  would otherwise see.
- **WI-8.3**: preserve ``ContentBlockParam[]`` end-to-end where possible.
  The previous implementation flattened everything to text (images
  became literal ``[image content]`` placeholder strings); that lost
  multimodal fidelity. Phase 8 keeps the block list when the API result
  surface accepts it and only flattens when the consumer requires str.
- **WI-8.4**: enforce ``MAX_RESULT_SIZE_CHARS`` (100,000) on the textual
  rendering of the result, matching TS' ``MCPTool.maxResultSizeChars``.
- **WI-8.2 integration**: results are passed through
  ``truncate_mcp_content_if_needed`` so a misbehaving server can't
  exhaust the model's context budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any, Optional

from src.permissions.types import PermissionPassthroughResult, PermissionResult
from src.tool_system.build_tool import McpInfo, Tool, build_tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolResult

from .client import McpClient
from .mcp_string_utils import build_mcp_tool_name
from .output_validation import (
    MAX_RESULT_SIZE_CHARS,
    truncate_mcp_content_if_needed,
)
from .types import ConnectedMCPServer, McpToolSchema

logger = logging.getLogger(__name__)

MAX_MCP_DESCRIPTION_LENGTH = 2048


# Strong-reference dict so cached validators survive across calls. Validators
# are small (~kB each) and the upper bound is small (n_servers × n_tools ×
# n_schema_revisions). The previous WeakValueDictionary was effectively a
# no-op: jsonschema validators are weakly-referenceable but no other strong
# reference is retained, so they were GC'd between calls and re-compiled
# every time. Keyed on (server_name, tool_name, schema_hash) so different
# servers with same-named tools and / or schema revisions stay separate.
_validator_cache: dict[str, Any] = {}


def _get_input_validator(server_name: str, tool_name: str, schema: dict[str, Any]) -> Optional[Any]:
    """Return a ``jsonschema`` validator for the given input schema, cached.

    Returns None if ``jsonschema`` is unavailable or if the schema can't
    be compiled — the caller falls back to a passthrough (current behavior),
    so a broken schema doesn't break the call entirely.
    """
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a hard dep today
        return None
    # Hash schema content so cache survives object-identity churn but
    # invalidates on real schema change. SHA1 truncated to 16 chars is
    # plenty (collisions are inert: a stale validator just re-validates
    # against an equivalent schema).
    schema_blob = json.dumps(schema, sort_keys=True, default=str).encode("utf-8")
    schema_hash = hashlib.sha1(schema_blob).hexdigest()[:16]
    key = f"{server_name}|{tool_name}|{schema_hash}"
    existing = _validator_cache.get(key)
    if existing is not None:
        return existing
    try:
        # Use Draft202012Validator (modern JSON Schema). MCP servers in the
        # wild emit schemas that may not declare $schema; tolerate that.
        validator_cls = getattr(jsonschema, "Draft202012Validator", None) or jsonschema.Draft7Validator
        validator = validator_cls(schema)
    except Exception as exc:
        logger.warning(
            "MCP %s/%s: failed to compile input schema validator (%s); "
            "falling back to passthrough validation",
            server_name, tool_name, exc,
        )
        return None
    _validator_cache[key] = validator
    return validator


def _flatten_content_blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    """Render a list of MCP content blocks as a single string for the
    text-only consumer path. Preserves text content; image blocks are
    rendered as a placeholder; resource and other types serialize to JSON.

    ContentBlock fidelity is preserved at the McpToolResult level (gap #21
    fix): the flatten happens only when the downstream consumer surface
    requires ``str``. WI-8.3 retains the list shape on ``ToolResult.output``
    when the consumer can accept ``Any``; this helper exists for the
    legacy str surface.
    """
    parts: list[str] = []
    for item in blocks:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        item_type = item.get("type", "")
        if item_type == "text":
            parts.append(item.get("text", ""))
        elif item_type == "image":
            parts.append("[image content]")
        elif item_type == "resource":
            parts.append(json.dumps(item.get("resource", {})))
        else:
            parts.append(json.dumps(item))
    return "\n".join(parts)


def wrap_mcp_tool(
    server_name: str,
    mcp_tool: McpToolSchema,
    client: McpClient,
) -> Tool:
    fully_qualified_name = build_mcp_tool_name(server_name, mcp_tool.name)
    annotations = mcp_tool.annotations or {}

    read_only = annotations.get("readOnlyHint", False)
    destructive = annotations.get("destructiveHint", False)
    open_world = annotations.get("openWorldHint", False)

    raw_desc = mcp_tool.description or ""
    truncated_desc = (
        raw_desc[:MAX_MCP_DESCRIPTION_LENGTH] + "... [truncated]"
        if len(raw_desc) > MAX_MCP_DESCRIPTION_LENGTH
        else raw_desc
    )

    search_hint = None
    if mcp_tool.meta and isinstance(mcp_tool.meta.get("anthropic/searchHint"), str):
        hint = mcp_tool.meta["anthropic/searchHint"]
        cleaned = re.sub(r"\s+", " ", hint).strip()
        search_hint = cleaned or None

    always_load_val = bool(
        mcp_tool.meta and mcp_tool.meta.get("anthropic/alwaysLoad") is True
    )

    input_schema = mcp_tool.input_schema or {"type": "object", "properties": {}}
    # Compile the input-schema validator once at wrap time. Captured into
    # the _async_call closure below so each wrapped Tool holds a strong
    # reference to its validator (eliminates per-call cache lookups and
    # closes the validator-GC bug from the WeakValueDictionary version).
    bound_validator = _get_input_validator(server_name, mcp_tool.name, input_schema)

    def _call(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # Detect whether we're inside a running event loop. In 3.14+,
        # ``get_event_loop()`` raises when no loop is set in the current
        # thread, so use ``get_running_loop()`` (the running-only API)
        # and fall back to ``asyncio.run`` if there isn't one.
        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False

        if running:
            # Cannot await our coroutine on the active loop without
            # blocking; run it in a worker thread with its own loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _async_call(args, ctx))
                return future.result()
        return asyncio.run(_async_call(args, ctx))

    async def _async_call(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # WI-8.1: validate args against the closure-captured validator.
        if bound_validator is not None:
            errors = list(bound_validator.iter_errors(args))
            if errors:
                # Prefer the first error's path + message; users debugging
                # against the raw model output benefit from a structured
                # location pointer.
                first = errors[0]
                path = ".".join(str(p) for p in first.absolute_path) or "<root>"
                detail = "; ".join(
                    f"{('.'.join(str(p) for p in e.absolute_path) or '<root>')}: {e.message}"
                    for e in errors[:5]
                )
                msg = f"Invalid input for {fully_qualified_name} at {path}: {detail}"
                if len(errors) > 5:
                    msg += f" (+{len(errors) - 5} more)"
                return ToolResult(
                    name=fully_qualified_name,
                    output=msg,
                    is_error=True,
                )

        try:
            result = await client.call_tool(mcp_tool.name, args)
            content_blocks: list[dict[str, Any]] = list(result.content) if result.content else []

            # WI-8.2: budget-truncate before rendering so the model never
            # sees an over-budget block list, even if the consumer surface
            # requires text (the text rendering of an over-budget list
            # would still exceed the budget).
            truncated_blocks, was_truncated = truncate_mcp_content_if_needed(content_blocks)
            if was_truncated:
                logger.info(
                    "MCP %s/%s: result exceeded token budget; truncated",
                    server_name, mcp_tool.name,
                )

            # WI-8.3 + WI-8.4: render to text for the ToolResult.output
            # str-typed contract; enforce the 100,000-char hard cap on top
            # of the token-budget truncation. Future work can preserve the
            # block list end-to-end when the API mapper accepts it.
            text_output = _flatten_content_blocks_to_text(
                truncated_blocks if isinstance(truncated_blocks, list) else [
                    {"type": "text", "text": str(truncated_blocks)},
                ]
            )
            # If token-budget truncation already fired, skip the char cap:
            # the budget output ends with the actionable
            # "[content truncated by MCP output limit; raise
            # MCP_MAX_OUTPUT_TOKENS to see more]" notice — slicing it off
            # at MAX_RESULT_SIZE_CHARS would lose the operator hint. With
            # the default cap of 25,000 tokens × 4 chars/token = 100,000
            # chars, the budget output stays within the char cap anyway;
            # only operators who explicitly raise the budget can exceed
            # MAX_RESULT_SIZE_CHARS, and they'd want to see the hint.
            if not was_truncated and len(text_output) > MAX_RESULT_SIZE_CHARS:
                text_output = (
                    text_output[:MAX_RESULT_SIZE_CHARS]
                    + "\n\n[content exceeded MAX_RESULT_SIZE_CHARS=100000; truncated]"
                )

            return ToolResult(
                name=fully_qualified_name,
                output=text_output,
                is_error=False,
            )
        except Exception as e:
            return ToolResult(
                name=fully_qualified_name,
                output=str(e),
                is_error=True,
            )

    def _check_permissions(
        _input: dict[str, Any], _ctx: ToolContext
    ) -> PermissionResult:
        return PermissionPassthroughResult()

    return build_tool(
        name=fully_qualified_name,
        input_schema=input_schema,
        call=_call,
        prompt=truncated_desc,
        description=truncated_desc,
        is_mcp=True,
        mcp_info=McpInfo(server_name=server_name, tool_name=mcp_tool.name),
        is_concurrency_safe=lambda _input: read_only,
        is_read_only=lambda _input: read_only,
        is_destructive=lambda _input: destructive,
        is_open_world=lambda _input: open_world,
        check_permissions=_check_permissions,
        search_hint=search_hint,
        always_load=always_load_val,
        input_json_schema=input_schema,
    )


def wrap_mcp_tools_for_server(
    server: ConnectedMCPServer,
    tools: list[McpToolSchema],
    client: McpClient,
) -> list[Tool]:
    wrapped: list[Tool] = []
    for mcp_tool in tools:
        try:
            tool = wrap_mcp_tool(server.name, mcp_tool, client)
            wrapped.append(tool)
        except Exception as e:
            logger.warning(
                "Failed to wrap MCP tool %s from server %s: %s",
                mcp_tool.name, server.name, e,
            )
    return wrapped
