"""Build agent prompts from Linear issue data.

Port of Symphony's PromptBuilder (Solid template → Jinja2).
"""

from __future__ import annotations

import logging
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError

from .workflow_store import get_workflow_store

logger = logging.getLogger(__name__)

# Jinja2 environment with strict undefined handling (mirrors Solid's strict_variables)
_jinja_env = Environment(undefined=StrictUndefined)

_DEFAULT_PROMPT = """You are an autonomous software engineering agent.

Issue: {{ issue.identifier }} - {{ issue.title }}
{% if issue.description %}
Description:
{{ issue.description }}
{% endif %}
{% if issue.priority %}
Priority: {{ issue.priority }}
{% endif %}
{% if issue.state %}
State: {{ issue.state }}
{% endif %}

Please analyze the issue, implement the necessary changes, and ensure all tests pass.
{% if clarification %}
{{ clarification }}
{% endif %}
"""


# Jinja2 template for clarification guidance injected into the prompt.
# Rendered when an issue is in the clarification flow.
_CLARIFICATION_TEMPLATE = """
---
## Clarification Context

This issue is currently awaiting clarification. When the answer is available,
it will be provided below. If you are unsure about any aspect of the issue,
use the `AskIssueAuthor` tool to request clarification from the issue author
or local operator.

When requesting clarification:
- Be specific: ask exactly what is ambiguous (e.g., "Should this function be sync or async?")
- Provide context: include relevant code snippets or error messages
- Limit to one question at a time to avoid overwhelming responders
{% if pending_question %}
- Current pending question: "{{ pending_question }}"
{% if options %}
- Available options: {{ options|join(', ') }}
{% endif %}
{% endif %}
---"""


class PromptBuilder:
    """Render agent prompts from issue data + workflow config."""

    @staticmethod
    def render(
        issue: Any,
        attempt: int | None = None,
        clarification_context: str | None = None,
        pending_question: str | None = None,
        options: list[str] | None = None,
    ) -> str:
        """Build prompt using workflow's WORKFLOW.md body template + issue data.

        Args:
            issue: Issue object with to_dict() method or dict-like
            attempt: Current attempt number (for retry tracking)
            clarification_context: Pre-rendered clarification guidance block
            pending_question: If issue is in clarification flow, the pending question
            options: If in clarification flow, the available options for the question
        """
        store = get_workflow_store()
        current = store.current()

        if current:
            template_str = current[1]
        else:
            template_str = _DEFAULT_PROMPT

        if not template_str or not template_str.strip():
            template_str = _DEFAULT_PROMPT

        try:
            template = _jinja_env.from_string(template_str)
        except TemplateError as exc:
            logger.error("Template parse error: %s", exc)
            template = _jinja_env.from_string(_DEFAULT_PROMPT)

        issue_dict = issue.to_dict() if hasattr(issue, "to_dict") else issue
        context = {
            "attempt": attempt,
            "issue": _to_jinja_value(issue_dict),
            "clarification": clarification_context,
            "pending_question": pending_question,
            "options": options,
        }

        try:
            return template.render(context).strip()
        except TemplateError as exc:
            logger.error("Template render error: %s", exc)
            # Fallback to default prompt
            fallback = _jinja_env.from_string(_DEFAULT_PROMPT)
            return fallback.render(context).strip()

    @staticmethod
    def build_continuation_prompt(turn_number: int, max_turns: int) -> str:
        """Build continuation prompt for subsequent turns."""
        return (
            f"Continuation guidance:\n\n"
            f"- The previous turn completed normally, but the issue is still active.\n"
            f"- This is continuation turn #{turn_number} of {max_turns}.\n"
            f"- Resume from the current workspace state instead of restarting.\n"
            f"- The original task instructions are already in this thread; do not restate them.\n"
            f"- Focus on remaining work and do not end the turn while the issue stays active.\n"
        )

    @staticmethod
    def build_clarification_context(
        pending_question: str | None = None,
        options: list[str] | None = None,
    ) -> str:
        """Build a clarification guidance block for the system prompt.

        This text is injected into the agent's prompt when an issue is in
        the clarification flow, guiding the agent to use AskIssueAuthor
        correctly and informing it about any pending question.

        Args:
            pending_question: The pending clarification question, if any
            options: Available options (for multiple-choice questions)

        Returns:
            A formatted clarification guidance block, or empty string if
            clarification is not active
        """
        if not pending_question:
            return ""

        template_str = _CLARIFICATION_TEMPLATE.strip()
        try:
            template = _jinja_env.from_string(template_str)
        except TemplateError as exc:
            logger.error("Clarification template parse error: %s", exc)
            return ""

        context = {
            "pending_question": pending_question,
            "options": options or [],
        }
        try:
            return template.render(context).strip()
        except TemplateError as exc:
            logger.error("Clarification template render error: %s", exc)
            return ""


def _to_jinja_value(value: Any) -> Any:
    """Coerce a value into Jinja2-friendly shapes."""
    if isinstance(value, dict):
        return {str(k): _to_jinja_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jinja_value(v) for v in value]
    return value
