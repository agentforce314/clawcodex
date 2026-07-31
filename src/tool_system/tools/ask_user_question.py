from __future__ import annotations

import json
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult

#: Self-describing tag on this tool's ``output``. ``_display_tool_result``
#: (agent_server.py) keys on it to forward the answers as DISPLAY data, so the
#: TUI renders "· question → answer" rows from structure instead of scraping
#: the model-facing prose below.
RESULT_TYPE = "ask_user_question"

#: Shared tail for every substitute answer handed back when no human answer is
#: coming. The lead-in differs by surface (no user exists at all vs. a dialog
#: was shown and went unanswered), but the instruction must not: an agent left
#: holding an unanswered question should commit to a default rather than re-ask
#: into the void. Observed live on terminal-bench (raman-fitting) that a bare
#: empty answer makes the model flail instead of proceeding.
_PROCEED_AUTONOMOUSLY = (
    "Proceed autonomously with your best judgment and reasonable default "
    "assumptions; do not ask again."
)

#: Headless / SDK: there is no interactive surface to ask on.
NON_INTERACTIVE_ANSWER = (
    "No interactive user is available (running headless/non-interactive). "
    f"{_PROCEED_AUTONOMOUSLY}"
)

#: Interactive: the dialog WAS shown, the user just never answered it.
TIMED_OUT_ANSWER = f"The user did not answer in time. {_PROCEED_AUTONOMOUSLY}"

#: Model-facing text when the user dismisses the dialog (Esc / Ctrl+C /
#: interrupt). Mirrors TS ``renderToolUseRejectedMessage``.
DECLINED_MESSAGE = "User declined to answer questions"


def _ask_user_question_classifier_input(input_data: dict) -> str:
    """Mirror TS ``AskUserQuestionTool.toAutoClassifierInput`` --
    join the question text from each question entry. Each entry may be
    a string or a dict with a ``question`` field; tolerate both."""
    qs = (input_data or {}).get("questions") or []
    parts: list[str] = []
    for q in qs:
        if isinstance(q, str):
            parts.append(q)
        elif isinstance(q, dict):
            text = q.get("question")
            if isinstance(text, str):
                parts.append(text)
    return " | ".join(parts)


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

    # Uniqueness is load-bearing, not cosmetic: answers/picked/texts, the answered
    # chip, allAnswered, the server-side asked-set filter and the display envelope
    # are ALL keyed by question text, and the dialog keys option identity by
    # label. Duplicates make two questions share one answer and mark an untouched
    # question answered. Mirrors TS UNIQUENESS_REFINE.
    texts = [q["question"] for q in normalized]
    if len(texts) != len(set(texts)):
        raise ToolInputError("question texts must be unique")
    for q in normalized:
        labels = [o.get("label") for o in q.get("options") or []]
        if len(labels) != len(set(labels)):
            raise ToolInputError(
                f"option labels must be unique within question {q['question']!r}"
            )

    if context.ask_user is not None:
        answers = context.ask_user(normalized)
        # ``None`` (not an empty dict) is the decline signal: the user
        # dismissed the dialog. An empty dict is a legitimate SUBMIT of nothing
        # -- the review step lets you submit with questions unanswered -- so
        # the two must stay distinguishable.
        if answers is None:
            return ToolResult(
                name="AskUserQuestion",
                output={"type": RESULT_TYPE, "questions": normalized, "declined": True},
            )
        return ToolResult(
            name="AskUserQuestion",
            output={"type": RESULT_TYPE, "questions": normalized, "answers": answers},
        )

    context.outbox.append({"tool": "AskUserQuestion", "questions": normalized})
    return ToolResult(name="AskUserQuestion", output={"questions": normalized, "status": "pending"})


def _ask_user_question_map_result(output: Any, tool_use_id: str) -> dict[str, Any]:
    """Model-facing result text (TS ``AskUserQuestionTool.mapToolResultToToolResultBlockParam``).

    Without this the default mapper JSON-dumps the whole output dict as the
    result content -- which is both noise for the model and, before the picker
    was wired, the literal source of the raw ``{"questions": [...]}`` blob in
    the transcript.
    """
    data = output if isinstance(output, dict) else {}

    if data.get("status") == "pending":
        # The outbox fallback: no surface could ask (subagent, SDK, MCP, an older
        # agent-server). Saying "submitted no answers" would fabricate a user
        # interaction that never happened -- the same trust violation the
        # server-side asked-key filter exists to prevent, from the other side.
        content = NON_INTERACTIVE_ANSWER
    elif data.get("declined"):
        content = DECLINED_MESSAGE
    else:
        answers = data.get("answers")
        answers = answers if isinstance(answers, dict) else {}
        # json.dumps, not bare f-string quotes: an answer containing a double
        # quote would otherwise forge extra "Q"="A" pairs inside a sentence the
        # model treats as the user speaking. Free text and model-authored option
        # labels both reach here, so neither side can be assumed quote-free.
        joined = ", ".join(f"{json.dumps(q)}={json.dumps(a)}" for q, a in answers.items())
        content = (
            f"User has answered your questions: {joined}. "
            "You can now continue with the user's answers in mind."
            if joined
            else "User submitted no answers. " + _PROCEED_AUTONOMOUSLY
        )

    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


AskUserQuestionTool: Tool = build_tool(
    name="AskUserQuestion",
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                # ADVERTISED bounds only, matching TS (questions 1-4,
                # options 2-4): schema_validation.py implements type/required/
                # enum/items and ignores minItems/maxItems, so these steer the
                # model rather than rejecting anything. That is deliberate --
                # the dialog degrades gracefully past them (only options 1-9
                # are digit-reachable, and the nav bar elides chips as they
                # stop fitting) so a hard reject would be worse than a nudge.
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "header": {"type": "string"},
                        "multiSelect": {"type": "boolean"},
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                    "preview": {"type": "string"},
                                },
                                "required": ["label"],
                            },
                        },
                    },
                    "required": ["question"],
                },
            },
        },
        "required": ["questions"],
    },
    call=_ask_user_question_call,
    map_result_to_api=_ask_user_question_map_result,
    prompt="Ask the user one or more clarifying questions.",
    description="Ask the user one or more clarifying questions.",
    max_result_size_chars=10_000,
    is_read_only=lambda _input: True,
    # DELIBERATE DIVERGENCE from TS (isConcurrencySafe -> true). Upstream
    # serializes every dialog through a toolUseConfirmQueue and renders the
    # head; this port has no such queue and the TUI's PromptZone is an
    # exclusive if-chain, so a second concurrent dialog would be invisible
    # while still blocking its tool. Serial is the only honest answer here.
    is_concurrency_safe=lambda _input: False,
    search_hint="ask question user input",
    to_auto_classifier_input=_ask_user_question_classifier_input,
)
