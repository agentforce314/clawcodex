from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


def _ask_user_question_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ToolInputError("questions must be a non-empty list")

    normalized: list[dict[str, Any]] = []
    for q in questions:
        if isinstance(q, str):
            q = {"question": q}
        if not isinstance(q, dict) or not isinstance(q.get("question"), str):
            raise ToolInputError("each question must be a dict with a 'question' string")
        if isinstance(q.get("options"), list):
            q["options"] = [
                opt if isinstance(opt, dict) else {"label": str(opt), "description": ""}
                for opt in q["options"]
            ]
        normalized.append(q)

    if context.ask_user is not None:
        answers = context.ask_user(normalized)
        return ToolResult(name="AskUserQuestion", output={"answers": answers})

    context.outbox.append({"tool": "AskUserQuestion", "questions": normalized})
    return ToolResult(name="AskUserQuestion", output={"questions": normalized, "status": "pending"})


AskUserQuestionTool: Tool = build_tool(
    name="AskUserQuestion",
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "header": {"type": "string"},
                        "options": {"type": "array"},
                    },
                    "required": ["question"],
                },
            },
        },
        "required": ["questions"],
    },
    call=_ask_user_question_call,
    prompt="Ask the user one or more clarifying questions.",
    description="Ask the user one or more clarifying questions.",
    max_result_size_chars=10_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    search_hint="ask question user input",
)
