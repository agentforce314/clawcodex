from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


def _send_user_message_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    message = tool_input.get("message")
    status = tool_input.get("status")
    attachments = tool_input.get("attachments")

    if not isinstance(message, str):
        raise ToolInputError("message must be a string")
    if status not in {"normal", "proactive"}:
        raise ToolInputError("status must be 'normal' or 'proactive'")
    if attachments is not None and not isinstance(attachments, list):
        raise ToolInputError("attachments must be an array when provided")

    resolved_attachments: list[dict[str, Any]] | None = None
    if attachments:
        resolved_attachments = []
        for i, p in enumerate(attachments):
            if not isinstance(p, str) or not p:
                raise ToolInputError(f"attachments[{i}] must be a non-empty string")
            path = Path(p).expanduser()
            if not path.is_absolute():
                base = context.cwd or context.workspace_root
                path = (base / path).resolve()
            path = context.ensure_allowed_path(path)
            if not path.exists() or not path.is_file():
                raise ToolInputError(f"attachment not found: {path}")
            mime, _ = mimetypes.guess_type(str(path))
            is_image = bool(mime and mime.startswith("image/"))
            resolved_attachments.append(
                {"path": str(path), "size": path.stat().st_size, "isImage": is_image}
            )

    sent_at = datetime.now(timezone.utc).isoformat()
    context.outbox.append(
        {"tool": "SendUserMessage", "status": status, "message": message, "attachments": resolved_attachments}
    )
    return ToolResult(
        name="SendUserMessage",
        output={
            "message": message,
            "attachments": resolved_attachments,
            "sentAt": sent_at,
        },
        content_type="json",
    )


SendUserMessageTool: Tool = build_tool(
    name="SendUserMessage",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "message": {"type": "string"},
            "attachments": {"type": "array", "items": {"type": "string"}},
            "status": {"type": "string", "enum": ["normal", "proactive"]},
        },
        "required": ["message", "status"],
    },
    call=_send_user_message_call,
    prompt="Send a message to the user (primary visible output channel).",
    description="Send a message to the user (primary visible output channel).",
    aliases=(),
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)
