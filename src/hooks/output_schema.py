"""Hook output JSON schema validation.

Phase-1 / WI-1.4. Mirrors TS ``schemas/hooks.ts:hookJSONOutputSchema`` (lines
169-176). Replaces the ad-hoc ``dict.get`` block at the old ``hook_executor.py``
``_execute_command_hook`` lines 170-188 — that path silently no-op'd on
malformed output (e.g., ``{"decision": "Deny"}`` with capital D would not match
the literal ``Literal["allow","deny","ask"]`` and the executor never fired the
behavior).

The new path uses Pydantic 2 (already a transitive dependency via the
Anthropic SDK) to validate against a strict schema:

    * Unknown fields are forbidden (``extra = "forbid"``).
    * ``decision`` must be one of ``"allow" | "deny" | "ask"``.
    * Other fields type-check via Pydantic.

Validation failures don't raise — they return ``(None, error_msg)`` so the
executor can log a WARNING and continue. The hook's exit code is still
honored; only the *decision payload* is dropped on bad JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


class HookOutput(BaseModel):
    """Schema for the JSON object hooks emit on stdout (exit code 0).

    Mirrors TS ``schemas/hooks.ts:hookJSONOutputSchema``. Field naming follows
    the wire format (camelCase) since hooks are language-agnostic and the wire
    format is what users actually write in their settings.json.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny", "ask"] | None = None
    reason: str | None = None
    updatedInput: dict[str, Any] | None = None
    additionalContexts: list[str] | None = None
    preventContinuation: bool | None = None
    stopReason: str | None = None
    updatedMCPToolOutput: Any | None = None
    # PermissionRequest-event extras (HOOKS-1). Flat form follows this
    # schema's existing convention; ``hookSpecificOutput`` accepts the TS
    # wire envelope (``{hookEventName, decision: {behavior, message,
    # updatedInput, updatedPermissions, interrupt}}`` — utils/hooks.ts:833-840)
    # so hooks written for the reference CLI work unchanged. The executor
    # normalizes both forms onto the same HookResult fields.
    updatedPermissions: list[dict[str, Any]] | None = None
    interrupt: bool | None = None
    hookSpecificOutput: dict[str, Any] | None = None


def parse_hook_output(stdout: str) -> tuple[HookOutput | None, str | None]:
    """Parse a hook's stdout into a typed ``HookOutput`` (or an error message).

    Returns ``(output, None)`` on success, ``(None, error_msg)`` on failure.

    Empty / whitespace-only stdout is **not** an error: it's treated as "the
    hook chose not to emit a decision" and returns ``(None, None)``. This
    matches TS' behavior where hooks may exit 0 without writing anything.

    Failure cases that return ``(None, error_msg)``:
      * stdout is non-empty but not valid JSON.
      * stdout is JSON but doesn't satisfy the schema (unknown field, bad
        type, ``decision`` not in the allowed literal, etc.).
    """
    if not stdout or not stdout.strip():
        return (None, None)
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return (None, f"Hook output is not valid JSON: {exc}")
    if not isinstance(data, dict):
        return (None, f"Hook output must be a JSON object, got {type(data).__name__}")
    try:
        return (HookOutput.model_validate(data), None)
    except ValidationError as exc:
        return (None, f"Hook output failed schema validation: {exc}")
