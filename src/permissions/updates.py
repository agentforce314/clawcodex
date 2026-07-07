"""Apply and persist :class:`PermissionUpdate` objects.

Mirrors ``typescript/src/utils/permissions/PermissionUpdate.ts``. The TS file
threads updates through both an in-memory context (``applyPermissionUpdate``)
and on-disk settings (``persistPermissionUpdate``); we keep the same split.

The persistence helpers take an injectable ``settings_path_for_destination``
callable so callers can stub out filesystem access in tests. The default
resolver returns ``None`` for non-persistable destinations — callers should
gate on :func:`supports_persistence` before constructing the path.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .rule_parser import (
    permission_rule_value_from_string,
    permission_rule_value_to_string,
)
from .types import (
    AdditionalWorkingDirectory,
    PermissionRuleValue,
    PermissionUpdate,
    PermissionUpdateAddDirectories,
    PermissionUpdateAddRules,
    PermissionUpdateDestination,
    PermissionUpdateRemoveDirectories,
    PermissionUpdateRemoveRules,
    PermissionUpdateReplaceRules,
    PermissionUpdateSetMode,
    ToolPermissionContext,
)

log = logging.getLogger(__name__)

PERSISTABLE_DESTINATIONS: tuple[PermissionUpdateDestination, ...] = (
    "userSettings",
    "projectSettings",
    "localSettings",
)


def supports_persistence(destination: PermissionUpdateDestination) -> bool:
    """True if ``destination`` is a persistable settings file source.

    Mirrors ``supportsPersistence`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:208-216``.
    ``cliArg`` and ``session`` are in-memory only.
    """
    return destination in PERSISTABLE_DESTINATIONS


def extract_rules(updates: list[PermissionUpdate] | None) -> list[PermissionRuleValue]:
    """Flatten ``addRules`` updates into a list of rule values.

    Mirrors ``extractRules`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:30-43``. Only
    ``addRules`` updates contribute — ``replaceRules`` / ``removeRules`` are
    ignored because their semantics differ.
    """
    if not updates:
        return []
    out: list[PermissionRuleValue] = []
    for update in updates:
        if isinstance(update, PermissionUpdateAddRules):
            out.extend(update.rules)
    return out


def suggestions_label(updates: tuple[PermissionUpdate, ...] | list[PermissionUpdate]) -> str | None:
    """Human label for an "always allow" option, naming the rule(s).

    Mirrors the intent of TS ``generateShellSuggestionsLabel``
    (components/permissions/shellPermissionHelpers.tsx:65): name the
    rules the user is about to save, e.g.
    ``don't ask again for Bash(git diff:*)``.
    """

    rule_strings: list[str] = []
    for rule_value in extract_rules(list(updates)):
        try:
            rule_strings.append(permission_rule_value_to_string(rule_value))
        except Exception:
            continue
    if not rule_strings:
        return None
    shown = ", ".join(rule_strings[:3])
    if len(rule_strings) > 3:
        shown += ", …"
    return f"don't ask again for {shown}"


def has_rules(updates: list[PermissionUpdate] | None) -> bool:
    """True if ``updates`` contains at least one ``addRules`` rule."""
    return len(extract_rules(updates)) > 0


def _ruleset_key(behavior: str) -> str:
    if behavior == "allow":
        return "always_allow_rules"
    if behavior == "deny":
        return "always_deny_rules"
    return "always_ask_rules"


def _replace_ruleset(
    context: ToolPermissionContext,
    behavior: str,
    destination: PermissionUpdateDestination,
    new_strings: list[str],
) -> ToolPermissionContext:
    key = _ruleset_key(behavior)
    current = dict(getattr(context, key))
    current[destination] = new_strings
    # dataclasses.replace (not a field-explicit constructor) so every field
    # not named here — including later additions like ``pre_plan_mode`` —
    # carries through instead of silently resetting to its default. The
    # ruleset dicts are still defensively copied (functional-update
    # contract: the input context is left unchanged).
    kwargs: dict[str, Any] = {
        "additional_working_directories": dict(context.additional_working_directories),
        "always_allow_rules": dict(context.always_allow_rules),
        "always_deny_rules": dict(context.always_deny_rules),
        "always_ask_rules": dict(context.always_ask_rules),
    }
    kwargs[key] = current
    return replace(context, **kwargs)


def apply_permission_update(
    context: ToolPermissionContext,
    update: PermissionUpdate,
) -> ToolPermissionContext:
    """Apply a single :class:`PermissionUpdate`, returning a new context.

    Mirrors ``applyPermissionUpdate`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:55-188``. The
    update kinds and their semantics:

    - ``setMode`` — replace ``context.mode``.
    - ``addRules`` — append rule strings to the matching ruleset slot.
    - ``replaceRules`` — replace the ruleset slot with the supplied rules.
    - ``removeRules`` — drop matching rule strings from the slot.
    - ``addDirectories`` — register additional working directories.
    - ``removeDirectories`` — drop registered working directories by path.

    Returns a fresh :class:`ToolPermissionContext`; the input is left
    unchanged.
    """
    if isinstance(update, PermissionUpdateSetMode):
        log.debug("permission update: setMode -> %s", update.mode)
        return replace(
            context,
            mode=update.mode,
            additional_working_directories=dict(context.additional_working_directories),
            always_allow_rules=dict(context.always_allow_rules),
            always_deny_rules=dict(context.always_deny_rules),
            always_ask_rules=dict(context.always_ask_rules),
        )

    if isinstance(update, PermissionUpdateAddRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        log.debug(
            "permission update: addRules behavior=%s dest=%s rules=%s",
            update.behavior, update.destination, rule_strings,
        )
        existing = list(getattr(context, _ruleset_key(update.behavior)).get(update.destination, []))
        return _replace_ruleset(
            context, update.behavior, update.destination, existing + rule_strings,
        )

    if isinstance(update, PermissionUpdateReplaceRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        log.debug(
            "permission update: replaceRules behavior=%s dest=%s rules=%s",
            update.behavior, update.destination, rule_strings,
        )
        return _replace_ruleset(
            context, update.behavior, update.destination, rule_strings,
        )

    if isinstance(update, PermissionUpdateRemoveRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        log.debug(
            "permission update: removeRules behavior=%s dest=%s rules=%s",
            update.behavior, update.destination, rule_strings,
        )
        existing = list(getattr(context, _ruleset_key(update.behavior)).get(update.destination, []))
        to_remove = set(rule_strings)
        filtered = [r for r in existing if r not in to_remove]
        return _replace_ruleset(
            context, update.behavior, update.destination, filtered,
        )

    if isinstance(update, PermissionUpdateAddDirectories):
        log.debug(
            "permission update: addDirectories dest=%s dirs=%s",
            update.destination, list(update.directories),
        )
        new_dirs = dict(context.additional_working_directories)
        for path in update.directories:
            new_dirs[path] = AdditionalWorkingDirectory(
                path=path, source=update.destination,  # type: ignore[arg-type]
            )
        return replace(
            context,
            additional_working_directories=new_dirs,
            always_allow_rules=dict(context.always_allow_rules),
            always_deny_rules=dict(context.always_deny_rules),
            always_ask_rules=dict(context.always_ask_rules),
        )

    if isinstance(update, PermissionUpdateRemoveDirectories):
        log.debug(
            "permission update: removeDirectories dirs=%s",
            list(update.directories),
        )
        new_dirs = dict(context.additional_working_directories)
        for path in update.directories:
            new_dirs.pop(path, None)
        return replace(
            context,
            additional_working_directories=new_dirs,
            always_allow_rules=dict(context.always_allow_rules),
            always_deny_rules=dict(context.always_deny_rules),
            always_ask_rules=dict(context.always_ask_rules),
        )

    return context


def apply_permission_updates(
    context: ToolPermissionContext,
    updates: list[PermissionUpdate],
) -> ToolPermissionContext:
    """Fold :func:`apply_permission_update` over an ordered list of updates.

    Mirrors ``applyPermissionUpdates`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:196-206``.
    """
    out = context
    for update in updates:
        out = apply_permission_update(out, update)
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

SettingsPathResolver = Callable[[PermissionUpdateDestination], str | None]


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: str, data: dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError:
        log.error("failed to persist settings to %s", path)
        return False


def persist_permission_update(
    update: PermissionUpdate,
    *,
    settings_path_for_destination: SettingsPathResolver,
) -> bool:
    """Persist one update to its destination's settings file.

    Mirrors ``persistPermissionUpdate`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:222-342``.

    Returns ``False`` if the destination is in-memory only or if the settings
    file write failed; ``True`` on a clean write. Non-persistable destinations
    (``cliArg``, ``session``) are a successful no-op from the caller's
    perspective and return ``False`` to make that observable in tests.
    """
    destination = update.destination
    if not supports_persistence(destination):
        return False

    path = settings_path_for_destination(destination)
    if not path:
        return False

    settings = _read_json(path)
    permissions = settings.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions

    if isinstance(update, PermissionUpdateAddRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        existing = permissions.get(update.behavior, [])
        if not isinstance(existing, list):
            existing = []
        for rule_str in rule_strings:
            if rule_str not in existing:
                existing.append(rule_str)
        permissions[update.behavior] = existing

    elif isinstance(update, PermissionUpdateReplaceRules):
        rule_strings = [permission_rule_value_to_string(r) for r in update.rules]
        permissions[update.behavior] = rule_strings

    elif isinstance(update, PermissionUpdateRemoveRules):
        # Normalize via parse → serialize round-trip so a stored "Bash(*)"
        # matches a removal request for "Bash" (parity with TS line 282-287).
        target_strings = {permission_rule_value_to_string(r) for r in update.rules}
        existing = permissions.get(update.behavior, [])
        if not isinstance(existing, list):
            existing = []
        filtered: list[str] = []
        for rule_str in existing:
            normalized = permission_rule_value_to_string(
                permission_rule_value_from_string(rule_str)
            )
            if normalized not in target_strings:
                filtered.append(rule_str)
        permissions[update.behavior] = filtered

    elif isinstance(update, PermissionUpdateAddDirectories):
        existing = permissions.get("additionalDirectories", [])
        if not isinstance(existing, list):
            existing = []
        for d in update.directories:
            if d not in existing:
                existing.append(d)
        permissions["additionalDirectories"] = existing

    elif isinstance(update, PermissionUpdateRemoveDirectories):
        existing = permissions.get("additionalDirectories", [])
        if not isinstance(existing, list):
            existing = []
        target = set(update.directories)
        permissions["additionalDirectories"] = [d for d in existing if d not in target]

    elif isinstance(update, PermissionUpdateSetMode):
        # Write-only today: setup_permissions does not read defaultMode
        # back at startup (mode comes from CLI/config). Asymmetry noted in
        # the C1 review; wire the read side with the C8 mode-gate work.
        permissions["defaultMode"] = update.mode

    return _write_json(path, settings)


def persist_permission_updates(
    updates: list[PermissionUpdate],
    *,
    settings_path_for_destination: SettingsPathResolver,
) -> list[bool]:
    """Persist a list of updates; returns the per-update success flag.

    Mirrors ``persistPermissionUpdates`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:349-353``.
    """
    return [
        persist_permission_update(
            u, settings_path_for_destination=settings_path_for_destination,
        )
        for u in updates
    ]


def create_read_rule_suggestion(
    dir_path: str,
    destination: PermissionUpdateDestination = "session",
) -> PermissionUpdate | None:
    """Build a ``Read(<path>/**)`` allow-rule suggestion for ``dir_path``.

    Mirrors ``createReadRuleSuggestion`` in
    ``typescript/src/utils/permissions/PermissionUpdate.ts:361-389``. Returns
    ``None`` for the filesystem root (``"/"``) — too broad to be a meaningful
    permission target.
    """
    posix_path = dir_path.replace("\\", "/")
    if posix_path == "/":
        return None

    if PurePosixPath(posix_path).is_absolute():
        rule_content = f"/{posix_path}/**"
    else:
        rule_content = f"{posix_path}/**"

    return PermissionUpdateAddRules(
        type="addRules",
        rules=(PermissionRuleValue(tool_name="Read", rule_content=rule_content),),
        behavior="allow",
        destination=destination,
    )


# ---------------------------------------------------------------------------
# Per-tool "allow for the whole session" suggestions + labels
# ---------------------------------------------------------------------------
#
# Mirrors the original Claude Code per-tool permission option set:
#   typescript/src/components/permissions/FilePermissionDialog/permissionOptions.tsx
#   typescript/src/utils/permissions/filesystem.ts:generateSuggestions (1436)
#   typescript/src/components/permissions/FallbackPermissionRequest.tsx
#
# Adapted to the mechanisms the Python matcher (check.py) actually honors:
#   * a file edit's "allow all edits during this session" maps to
#     ``setMode:acceptEdits`` (honored by ``has_permissions_to_use_tool_inner``),
#   * edits/reads whose target is outside the working roots also grant the
#     directory (``addDirectories``, bridged into ``ToolContext.allowed_roots``),
#   * Bash keeps its command-prefix rule (``suggestions_for_bash_command``),
#   * every other tool gets a content-less allow rule (matched by
#     ``tool_always_allowed_rule`` for any tool).
#
# Persistence destinations match the original exactly: file edits/reads apply
# in-memory (``session``); Bash and other tools persist (``localSettings``).

FILE_EDIT_TOOL_NAMES: tuple[str, ...] = ("Write", "Edit", "MultiEdit", "NotebookEdit")
FILE_READ_TOOL_NAMES: tuple[str, ...] = ("Read", "Glob", "Grep")

# Interaction / meta tools render their own dialogs in the original and never
# carry a "don't ask again" option — never mint a session rule for them.
_NO_SESSION_OPTION_TOOLS: frozenset[str] = frozenset(
    {"AskUserQuestion", "EnterPlanMode", "ExitPlanMode"}
)

# NB: the original surfaces this option with a "(shift+tab)" hint, because
# there the key cycles permission modes. The TUI wired shift+tab cycling in
# ch13 round-4 (ui-tui useInputHandlers → cycle_permission_mode control), so
# the hint would now be truthful — it stays off the LABEL only because the
# option text mirrors the original's wording exactly.

_PATH_INPUT_KEYS: tuple[str, ...] = ("file_path", "notebook_path", "path")


def _tool_input_path(tool_input: dict[str, Any] | None) -> str | None:
    if not tool_input:
        return None
    for key in _PATH_INPUT_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _abs_path(file_path: str) -> str:
    return os.path.abspath(os.path.expanduser(file_path))


def _resolve(path: str) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(_abs_path(path))


def _path_within_roots(
    file_path: str, allowed_roots: tuple[str, ...] | None
) -> bool:
    """Whether ``file_path`` lives under one of ``allowed_roots``.

    ``allowed_roots is None`` means "unknown" — assume inside so we do not
    suggest a directory grant the matcher would never need. The real matcher
    still gates access; this only shapes the suggestion/label.
    """
    if not allowed_roots:
        return True
    target = _resolve(file_path)
    for root in allowed_roots:
        try:
            target.relative_to(_resolve(root))
            return True
        except ValueError:
            continue
    return False


def _grant_directory(tool_name: str, path: str) -> str | None:
    """The directory to grant for an out-of-roots access.

    Search tools (Glob/Grep) pass a directory as their target; file tools pass
    a file, so we grant its parent. Mirrors TS ``getDirectoryForPath``.

    Returns ``None`` for a filesystem-root grant (``/``) so we never register
    the whole filesystem as a session working directory — same guard
    :func:`create_read_rule_suggestion` already applies.
    """
    if tool_name in ("Glob", "Grep"):
        directory = _abs_path(path)
    else:
        directory = os.path.dirname(_abs_path(path))
    if not directory or directory == "/":
        return None
    return directory


def default_session_suggestions(
    tool_name: str,
    tool_input: dict[str, Any] | None,
    perm_context: ToolPermissionContext | None = None,
    *,
    allowed_roots: tuple[str, ...] | None = None,
) -> list[PermissionUpdate]:
    """Build the "allow for the whole session" updates for ``tool_name``.

    Returns the :class:`PermissionUpdate` list an "always allow" choice would
    apply. Empty list = no session option for this ask.
    """
    if tool_name in _NO_SESSION_OPTION_TOOLS:
        return []
    tool_input = tool_input or {}
    mode = getattr(perm_context, "mode", "default")

    # Bash: command-prefix rule, persisted (unchanged behavior).
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if isinstance(command, str) and command.strip():
            from .bash_suggestions import suggestions_for_bash_command

            return suggestions_for_bash_command(command)
        return []

    # File edits: setMode acceptEdits (+ addDirectories when outside roots).
    if tool_name in FILE_EDIT_TOOL_NAMES:
        updates: list[PermissionUpdate] = []
        if mode in ("default", "plan"):
            updates.append(
                PermissionUpdateSetMode(destination="session", mode="acceptEdits")
            )
        path = _tool_input_path(tool_input)
        if path and not _path_within_roots(path, allowed_roots):
            grant = _grant_directory(tool_name, path)
            if grant is not None:
                updates.append(
                    PermissionUpdateAddDirectories(
                        destination="session",
                        directories=(grant,),
                    )
                )
        return updates

    # File reads/search: content-less session rule for the tool (+ a directory
    # grant when the target is outside roots so execution is permitted too).
    if tool_name in FILE_READ_TOOL_NAMES:
        updates = [
            PermissionUpdateAddRules(
                destination="session",
                behavior="allow",
                rules=(PermissionRuleValue(tool_name=tool_name),),
            )
        ]
        path = _tool_input_path(tool_input)
        if path and not _path_within_roots(path, allowed_roots):
            grant = _grant_directory(tool_name, path)
            if grant is not None:
                updates.append(
                    PermissionUpdateAddDirectories(
                        destination="session",
                        directories=(grant,),
                    )
                )
        return updates

    # WebFetch: domain-scoped rule (TS WebFetchTool.ts:346 buildSuggestions) —
    # "don't ask again" grants the HOST, not every future fetch. Normally the
    # tool's own check supplies this on its passthrough; this branch is the
    # fallback for callers that build suggestions from tool_input directly.
    if tool_name == "WebFetch":
        url = tool_input.get("url", "")
        hostname = None
        if isinstance(url, str) and url:
            import urllib.parse

            try:
                hostname = urllib.parse.urlparse(url).hostname
            except Exception:
                hostname = None
        if hostname:
            return [
                PermissionUpdateAddRules(
                    destination="localSettings",
                    behavior="allow",
                    rules=(
                        PermissionRuleValue(
                            tool_name="WebFetch",
                            rule_content=f"domain:{hostname}",
                        ),
                    ),
                )
            ]
        # Unparseable URL → fall through to the content-less rule below.

    # Every other tool (Skill, MCP, …): persisted content-less rule.
    if tool_name:
        return [
            PermissionUpdateAddRules(
                destination="localSettings",
                behavior="allow",
                rules=(PermissionRuleValue(tool_name=tool_name),),
            )
        ]
    return []


def _directory_label(directories: tuple[str, ...]) -> str:
    if not directories:
        return "this directory"
    return os.path.basename(directories[0].rstrip("/")) or "this directory"


def session_option_label(
    suggestions: tuple[PermissionUpdate, ...] | list[PermissionUpdate],
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
) -> str | None:
    """Human label for the session option, rendered as ``f"Yes, {label}"``.

    Mirrors the per-tool option text in the original (permissionOptions.tsx /
    FallbackPermissionRequest.tsx). Returns ``None`` when there is nothing to
    offer (the caller then omits the option).
    """
    suggestions = tuple(suggestions or ())
    if not suggestions:
        return None

    dir_update = next(
        (
            u
            for u in suggestions
            if isinstance(u, PermissionUpdateAddDirectories) and u.directories
        ),
        None,
    )
    has_accept_edits = any(
        isinstance(u, PermissionUpdateSetMode) and u.mode == "acceptEdits"
        for u in suggestions
    )

    # File edits — "allow all edits [in <dir>/] during this session".
    if has_accept_edits or tool_name in FILE_EDIT_TOOL_NAMES:
        if dir_update:
            name = _directory_label(dir_update.directories)
            return f"allow all edits in {name}/ during this session"
        return "allow all edits during this session"

    # File reads — "allow reading from <dir>/ during this session" / generic.
    if tool_name in FILE_READ_TOOL_NAMES:
        if dir_update:
            name = _directory_label(dir_update.directories)
            return f"allow reading from {name}/ during this session"
        return "allow reading during this session"

    # Bash and every other tool — "and don't ask again for <rule(s)>".
    base = suggestions_label(suggestions)
    if base:
        return f"and {base}"
    return None


def deserialize_permission_update(data: dict) -> "Any":
    """Wire dict → PermissionUpdate dataclass (None on unrecognized type).

    Promoted from the agent-server (HOOKS-1): the can_use_tool reply AND
    PermissionRequest-hook ``updatedPermissions`` both arrive in the same
    wire shape, so the one parser lives here with the update types.
    """
    from .types import (
        PermissionRuleValue,
        PermissionUpdateAddDirectories,
        PermissionUpdateAddRules,
        PermissionUpdateRemoveDirectories,
        PermissionUpdateRemoveRules,
        PermissionUpdateReplaceRules,
        PermissionUpdateSetMode,
    )

    utype = data.get("type")
    dest = data.get("destination", "session")
    behavior = data.get("behavior", "allow")

    def _rules() -> tuple:
        return tuple(
            PermissionRuleValue(
                tool_name=str(r.get("tool_name", "")),
                rule_content=r.get("rule_content"),
            )
            for r in (data.get("rules") or []) if isinstance(r, dict)
        )

    if utype == "addRules":
        return PermissionUpdateAddRules(destination=dest, behavior=behavior, rules=_rules())
    if utype == "replaceRules":
        return PermissionUpdateReplaceRules(destination=dest, behavior=behavior, rules=_rules())
    if utype == "removeRules":
        return PermissionUpdateRemoveRules(destination=dest, behavior=behavior, rules=_rules())
    if utype == "setMode":
        return PermissionUpdateSetMode(destination=dest, mode=data.get("mode", "default"))
    if utype == "addDirectories":
        return PermissionUpdateAddDirectories(
            destination=dest, directories=tuple(data.get("directories") or ()),
        )
    if utype == "removeDirectories":
        return PermissionUpdateRemoveDirectories(
            destination=dest, directories=tuple(data.get("directories") or ()),
        )
    return None


def serialize_permission_update(update: "Any") -> dict:
    """PermissionUpdate dataclass → wire dict (the shape hooks and the TUI
    see; ``deserialize_permission_update`` reverses it).

    Promoted from the agent-server (HOOKS-1 critic round): the can_use_tool
    round-trip and PermissionRequest-hook stdin both need the SAME canonical
    JSON — nested rules as ``{"tool_name", "rule_content"}`` dicts, never
    Python reprs.
    """
    out: dict = {
        "type": getattr(update, "type", "addRules"),
        "destination": getattr(update, "destination", "session"),
    }
    behavior = getattr(update, "behavior", None)
    if behavior is not None:
        out["behavior"] = behavior
    rules = getattr(update, "rules", None)
    if rules:
        out["rules"] = [
            {"tool_name": getattr(r, "tool_name", ""),
             "rule_content": getattr(r, "rule_content", None)}
            for r in rules
        ]
    mode = getattr(update, "mode", None)
    if mode is not None:
        out["mode"] = mode
    directories = getattr(update, "directories", None)
    if directories:
        out["directories"] = list(directories)
    return out
