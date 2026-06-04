"""Build agent prompts from Linear issue data.

Port of Symphony's PromptBuilder (Solid template → Jinja2).
"""

from __future__ import annotations

import logging
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError

from .tracker import PullRequestFeedback, PullRequestRef
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

_REVIEW_FEEDBACK_TEMPLATE = """You are an autonomous software engineering agent fixing pull request feedback.

Issue: {{ issue.identifier }} - {{ issue.title }}
Pull request: {% if pull_request.number %}#{{ pull_request.number }}{% else %}unknown{% endif %}{% if pull_request.url %} ({{ pull_request.url }}){% endif %}
Branch: {{ branch_name }}

Current task:
- Fix only the PR review feedback and CI failures listed below.
- Do not expand scope or reimplement unrelated issue requirements.
- Work on the current branch only; do not create a new branch or pull request.
- Prefer the smallest correct change that addresses the feedback.
- If feedback is conflicting or unclear, leave code unchanged for that item and explain what clarification is needed.
- Run relevant tests or record why they cannot be run.

Feedback:
{% for item in feedback %}
{{ loop.index }}. [{{ item.source }}] {{ item.id }}{% if item.severity %} severity={{ item.severity }}{% endif %}{% if item.status %} status={{ item.status }}{% endif %}
{% if item.file_path %}   File: {{ item.file_path }}{% if item.line %}:{{ item.line }}{% endif %}
{% endif %}{% if item.commit_sha %}   Commit: {{ item.commit_sha }}
{% endif %}{% if item.url %}   URL: {{ item.url }}
{% endif %}{% if item.diff_hunk %}   Diff hunk:
```diff
{{ item.diff_hunk }}
```
{% endif %}   Body:
{{ item.body | indent(3) }}
{% endfor %}
"""


class PromptBuilder:
    """Render agent prompts from issue data + workflow config."""

    @staticmethod
    def render(
        issue: Any,
        attempt: int | None = None,
        clarification_context: str | None = None,
        pending_question: str | None = None,
        options: list[str] | None = None,
        session: Any | None = None,
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
            rendered = template.render(context).strip()
        except TemplateError as exc:
            logger.error("Template render error: %s", exc)
            # Fallback to default prompt
            fallback = _jinja_env.from_string(_DEFAULT_PROMPT)
            rendered = fallback.render(context).strip()
        if session is not None and getattr(session, "workspace_strategy", None) == "sequential":
            rendered = f"{rendered}\n\n{_build_sequential_workspace_context(session)}"
        return rendered

    @staticmethod
    def render_review_feedback(
        *,
        issue: Any,
        pull_request: PullRequestRef,
        branch_name: str,
        feedback: list[PullRequestFeedback],
    ) -> str:
        issue_dict = issue.to_dict() if hasattr(issue, "to_dict") else issue
        context = {
            "issue": _to_jinja_value(issue_dict),
            "pull_request": pull_request,
            "branch_name": branch_name,
            "feedback": feedback,
        }
        try:
            return _jinja_env.from_string(_REVIEW_FEEDBACK_TEMPLATE).render(context).strip()
        except TemplateError as exc:
            logger.error("Review feedback template render error: %s", exc)
            return _DEFAULT_PROMPT

    @staticmethod
    def build_continuation_prompt(
        turn_number: int,
        max_turns: int,
        issue_context: str | None = None,
    ) -> str:
        """Build continuation prompt for subsequent turns."""
        context_block = f"\n\nCurrent issue context:\n{issue_context}\n" if issue_context else ""
        urgency = (
            f"\n- ⚠️  You have only {max_turns - turn_number + 1} turn(s) remaining. "
            f"Prioritize code implementation over reading more files. "
            f"Use Write/Edit to make concrete changes NOW."
            if turn_number >= max_turns // 2
            else ""
        )
        return (
            f"Continuation guidance:\n\n"
            f"- This is continuation turn #{turn_number} of {max_turns}.{context_block}{urgency}\n"
            f"- Resume from the current workspace state and continue implementing.\n"
            f"- Use available tools (Bash, Write, Edit, Grep, Glob, etc.) to make changes.\n"
            f"- Focus on completing the issue requirements. Do NOT re-read files you have already explored.\n"
            f"- Your FIRST action should be a Write or Edit to implement the feature.\n"
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


def _build_sequential_workspace_context(session: Any) -> str:
    return "\n".join(
        [
            "---",
            "## Sequential Workspace Context",
            "",
            "This issue is running in a sequential shared workspace.",
            f"- Workspace strategy: `{getattr(session, 'workspace_strategy', 'sequential')}`",
            f"- Integration branch: `{getattr(session, 'integration_branch', None) or 'current branch'}`",
            f"- Start commit: `{getattr(session, 'start_commit_sha', None) or 'unknown'}`",
            f"- Base commit: `{getattr(session, 'base_commit_sha', None) or 'unknown'}`",
            f"- Previous issue: `{getattr(session, 'previous_issue_id', None) or 'none'}`",
            f"- Sequence index: `{getattr(session, 'sequence_index', None) or 'unknown'}`",
            "",
            "Build on the existing commit chain in this workspace. Do not redo earlier issues.",
            "If the expected prior commit chain appears to be missing, stop and report it.",
            "---",
        ]
    )


def _to_jinja_value(value: Any) -> Any:
    """Coerce a value into Jinja2-friendly shapes."""
    if isinstance(value, dict):
        return {str(k): _to_jinja_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jinja_value(v) for v in value]
    return value
