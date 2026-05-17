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
"""


class PromptBuilder:
    """Render agent prompts from issue data + workflow config."""

    @staticmethod
    def render(
        issue: Any,
        attempt: int | None = None,
    ) -> str:
        """Build prompt using workflow's WORKFLOW.md body template + issue data."""
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

        context = {
            "attempt": attempt,
            "issue": _to_jinja_value(issue.to_dict() if hasattr(issue, "to_dict") else issue),
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


def _to_jinja_value(value: Any) -> Any:
    """Coerce a value into Jinja2-friendly shapes."""
    if isinstance(value, dict):
        return {str(k): _to_jinja_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jinja_value(v) for v in value]
    return value
