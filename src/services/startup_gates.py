"""Startup security gates (components C8) — UI-neutral state layer.

Three gates, ported from the TS startup flow (interactiveHelpers.tsx:
trust :139-143, CLAUDE.md external includes :174-179, bypass
acceptance :228-232), in that order:

1. **Folder trust** (TS ``TrustDialog`` + ``config.ts
   checkHasTrustDialogAccepted`` :771-817): per-project flag
   ``hasTrustDialogAccepted`` in the USER-owned global config's
   ``projects[path]`` map — never in a committable file, so a repo
   cannot pre-trust itself. Trust at a directory implies trust for its
   children (parent-walk on read). Home-directory sessions are trusted
   for the SESSION only (TS ``setSessionTrustAccepted``) — persisting
   trust for ``$HOME`` would blanket-trust everything under it.
   Decline exits with code 1.

2. **External CLAUDE.md includes** (TS
   ``ClaudeMdExternalIncludesDialog`` :120-132): per-project boolean
   pair — ``hasClaudeMdExternalIncludesApproved`` +
   ``hasClaudeMdExternalIncludesWarningShown``. Asked once (yes/no both
   set WarningShown); the loader (``context_system/claude_md.py``)
   includes externals only when approved. Non-interactive sessions
   never load unapproved externals (TS parity: silent skip — the
   include flag simply stays false).

3. **Bypass-permissions acceptance** (TS
   ``BypassPermissionsModeDialog.tsx`` :31-40): shown when
   bypassPermissions mode is requested and
   ``skipDangerousModePermissionPrompt`` is not already true. Accept
   persists ``skipDangerousModePermissionPrompt: true`` to the USER
   settings file (TS ``updateSettingsForSource("userSettings", ...)``);
   decline exits 1; Esc exits 0 (TS ``_temp2`` →
   ``gracefulShutdownSync(0)``). TS reads the flag from
   user/local/flag/policy settings sources — this port models the user
   and local tiers (flag/policy sources don't exist here; the project
   tier is excluded in TS too, explicitly: a repo must not accept the
   bypass dialog on the user's behalf).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

TRUST_KEY = "hasTrustDialogAccepted"
INCLUDES_APPROVED_KEY = "hasClaudeMdExternalIncludesApproved"
INCLUDES_WARNING_SHOWN_KEY = "hasClaudeMdExternalIncludesWarningShown"
SKIP_DANGEROUS_PROMPT_KEY = "skipDangerousModePermissionPrompt"

# Session-only trust for the home-directory case (TS bootstrap/state
# setSessionTrustAccepted). Module state: one process == one session.
_session_trust_accepted = False


def reset_session_trust_for_testing() -> None:
    global _session_trust_accepted
    _session_trust_accepted = False


# ---------------------------------------------------------------------------
# Gate 1: folder trust
# ---------------------------------------------------------------------------

def check_trust_accepted(cwd: str | Path | None = None) -> bool:
    """TS ``computeTrustDialogAccepted``: session trust, then cwd and
    every parent. (TS also pre-checks the projectPath key location, but
    the git root is normally an ancestor-or-self of the resolved cwd,
    so the parent walk subsumes it — and skipping it saves a git
    subprocess per check. Known edge: GIT_WORK_TREE/GIT_DIR redirection
    can yield a non-ancestor root; trust recorded there is then missed
    and the dialog re-asks — fails safe, never silently trusts.)"""

    if _session_trust_accepted:
        return True

    from src import config as config_mod

    current = Path(config_mod.normalize_path_for_config_key(cwd or Path.cwd()))
    while True:
        if config_mod.get_project_entry(current).get(TRUST_KEY):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def grant_session_trust() -> None:
    """Set BOTH session-trust flags (this module's + bootstrap state's),
    with no persistence.

    The two flags exist because ``check_trust_accepted`` predates the
    bootstrap port; they must never desync — e.g. a piped-stdout session
    is classified non-interactive (implicit trust, full env applied in
    ``run_pre_action``) yet still dispatches to the REPL, whose gate
    consults ``check_trust_accepted``; without the sync it would prompt
    AFTER the env was already applied.
    """
    global _session_trust_accepted
    _session_trust_accepted = True
    try:
        from src.bootstrap.state import set_session_trust_accepted

        set_session_trust_accepted(True)
    except Exception:
        pass


def record_trust_accepted(cwd: str | Path | None = None) -> bool:
    """Persist acceptance (TS TrustDialog.tsx:172-178). Home directory
    → session-only; everything else → ``projects[path]`` entry."""

    grant_session_trust()

    from src import config as config_mod

    resolved_cwd = Path(
        config_mod.normalize_path_for_config_key(cwd or Path.cwd())
    )
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    if home is not None and resolved_cwd == home:
        return True  # session-only, by design

    project_path = config_mod.get_project_path_for_config(cwd)
    return config_mod.update_project_entry(project_path, {TRUST_KEY: True})


def collect_trust_warnings(cwd: str | Path | None = None) -> list[str]:
    """Degraded port of the TS trust dialog's warning enumeration.

    TS lists eight source kinds (hooks, bash allow rules, apiKeyHelper,
    AWS/GCP commands, OTel headers, dangerous env vars, slash-command
    bash). This port only models two of those subsystems today, so only
    those two are checked — bash allow rules and statusLine commands in
    the committable settings tiers. Never invent warnings for absent
    subsystems.
    """

    from src.permissions import settings_paths

    warnings: list[str] = []
    cwd_str = str(cwd) if cwd is not None else None
    for label, path in (
        (".clawcodex/settings.json", settings_paths.project_settings_path(cwd_str)),
        (
            ".clawcodex/settings.local.json",
            settings_paths.local_settings_path(cwd_str),
        ),
    ):
        data = _read_json_dict(path)
        if not data:
            continue
        permissions = data.get("permissions")
        allow = (
            permissions.get("allow") if isinstance(permissions, dict) else None
        )
        if isinstance(allow, list) and any(
            isinstance(rule, str) and rule.startswith("Bash") for rule in allow
        ):
            warnings.append(f"{label} pre-allows Bash commands")
        if data.get("statusLine"):
            warnings.append(f"{label} configures a status-line command")
    return warnings


# ---------------------------------------------------------------------------
# Gate 2: external CLAUDE.md includes
# ---------------------------------------------------------------------------

def get_external_includes_state(cwd: str | Path | None = None) -> str:
    """``approved`` | ``declined`` | ``unset`` for this project."""

    from src import config as config_mod

    entry = config_mod.get_project_entry(
        config_mod.get_project_path_for_config(cwd)
    )
    if entry.get(INCLUDES_APPROVED_KEY):
        return "approved"
    if entry.get(INCLUDES_WARNING_SHOWN_KEY):
        return "declined"
    return "unset"


def record_external_includes_choice(
    approved: bool, cwd: str | Path | None = None
) -> bool:
    """TS dialog updaters _temp2/_temp3: both choices mark the warning
    shown; only "yes" approves."""

    from src import config as config_mod

    ok = config_mod.update_project_entry(
        config_mod.get_project_path_for_config(cwd),
        {
            INCLUDES_APPROVED_KEY: bool(approved),
            INCLUDES_WARNING_SHOWN_KEY: True,
        },
    )
    if ok:
        # The loader memo AND the assembled-context memo both embed the
        # external-include decision; clear both so an approval recorded
        # mid-session takes effect on the next prompt, not next launch.
        from src.context_system.claude_md import clear_memory_file_caches

        clear_memory_file_caches()
        try:
            from src.context_system.prompt_assembly import (
                clear_context_caches,
            )

            clear_context_caches()
        except Exception:
            pass
    return ok


async def list_external_includes(cwd: str | Path | None = None) -> list[str]:
    """Paths of external @includes that WOULD load if approved (TS
    hasExternalClaudeMdIncludes over ``getMemoryFiles(true)``)."""

    from src.context_system.claude_md import (
        get_memory_files,
        is_external_memory_file,
    )

    cwd_str = str(cwd) if cwd is not None else None
    files = await get_memory_files(cwd=cwd_str, force_include_external=True)
    return [f.path for f in files if is_external_memory_file(f, cwd=cwd_str)]


# ---------------------------------------------------------------------------
# Gate 3: bypass-permissions acceptance
# ---------------------------------------------------------------------------

def _read_json_dict(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def has_skip_dangerous_mode_permission_prompt(
    cwd: str | Path | None = None,
) -> bool:
    from src.permissions import settings_paths

    cwd_str = str(cwd) if cwd is not None else None
    for path in (
        settings_paths.user_settings_path(),
        settings_paths.local_settings_path(cwd_str),
    ):
        if _read_json_dict(path).get(SKIP_DANGEROUS_PROMPT_KEY) is True:
            return True
    return False


def record_bypass_accepted() -> bool:
    """Persist ``skipDangerousModePermissionPrompt: true`` to the USER
    settings file (TS updateSettingsForSource("userSettings", ...))."""

    from src.permissions import settings_paths

    path = settings_paths.user_settings_path()
    data = _read_json_dict(path)
    data[SKIP_DANGEROUS_PROMPT_KEY] = True
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return True
    except OSError:
        return False


__all__ = [
    "INCLUDES_APPROVED_KEY",
    "INCLUDES_WARNING_SHOWN_KEY",
    "SKIP_DANGEROUS_PROMPT_KEY",
    "TRUST_KEY",
    "check_trust_accepted",
    "collect_trust_warnings",
    "get_external_includes_state",
    "grant_session_trust",
    "has_skip_dangerous_mode_permission_prompt",
    "list_external_includes",
    "record_bypass_accepted",
    "record_external_includes_choice",
    "record_trust_accepted",
    "reset_session_trust_for_testing",
]
