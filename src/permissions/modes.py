from __future__ import annotations

import logging

from .types import (
    EXTERNAL_PERMISSION_MODES,
    PERMISSION_MODES,
    ExternalPermissionMode,
    PermissionMode,
)

log = logging.getLogger(__name__)


_MODE_CONFIG: dict[PermissionMode, dict[str, str]] = {
    "default": {"title": "Default", "short_title": "Default", "symbol": "", "external": "default"},
    "plan": {"title": "Plan Mode", "short_title": "Plan", "symbol": "⏸", "external": "plan"},
    "acceptEdits": {"title": "Accept edits", "short_title": "Accept", "symbol": "⏵⏵", "external": "acceptEdits"},
    "bypassPermissions": {"title": "Bypass Permissions", "short_title": "Bypass", "symbol": "⏵⏵", "external": "bypassPermissions"},
    "dontAsk": {"title": "Don't Ask", "short_title": "DontAsk", "symbol": "⏵⏵", "external": "dontAsk"},
    # Internal modes — neither user-addressable nor persisted to settings.json.
    # `external` maps them to a sensible external display mode (parity with
    # typescript/src/utils/permissions/PermissionMode.ts:80-90).
    "auto": {"title": "Auto mode", "short_title": "Auto", "symbol": "⏵⏵", "external": "default"},
    "bubble": {"title": "Bubble", "short_title": "Bubble", "symbol": "↑", "external": "default"},
}


def _get_config(mode: PermissionMode) -> dict[str, str]:
    return _MODE_CONFIG.get(mode, _MODE_CONFIG["default"])


def permission_mode_title(mode: PermissionMode) -> str:
    return _get_config(mode)["title"]


def permission_mode_short_title(mode: PermissionMode) -> str:
    return _get_config(mode)["short_title"]


def permission_mode_symbol(mode: PermissionMode) -> str:
    return _get_config(mode)["symbol"]


def permission_mode_from_string(s: str) -> PermissionMode:
    if s in PERMISSION_MODES:
        return s  # type: ignore[return-value]
    return "default"


def is_default_mode(mode: PermissionMode | None) -> bool:
    return mode is None or mode == "default"


def to_external_permission_mode(mode: PermissionMode) -> ExternalPermissionMode:
    """Map a possibly-internal mode to its external representation.

    Mirrors ``toExternalPermissionMode`` in
    ``typescript/src/utils/permissions/PermissionMode.ts:111-115``. ``auto``
    and ``bubble`` are internal and surface to external consumers as
    ``"default"``.
    """
    config = _get_config(mode)
    external = config.get("external", "default")
    return external  # type: ignore[return-value]


def is_external_permission_mode(mode: PermissionMode) -> bool:
    """True when ``mode`` is in :data:`EXTERNAL_PERMISSION_MODES`.

    Mirrors ``isExternalPermissionMode`` in
    ``typescript/src/utils/permissions/PermissionMode.ts:97-105``. The TS
    reference adds an internal ``USER_TYPE === 'ant'`` guard; we omit it
    because that gate is Anthropic-internal and not part of the public
    Python contract.
    """
    return mode in EXTERNAL_PERMISSION_MODES


def initial_permission_mode_from_cli(
    *,
    permission_mode_cli: str | None = None,
    dangerously_skip_permissions: bool = False,
    settings_default_mode: str | None = None,
    disable_bypass_permissions_mode: bool | None = None,
    fallback_mode: PermissionMode = "default",
) -> PermissionMode:
    """Resolve the effective :class:`PermissionMode` from CLI flags + settings.

    Mirrors ``initialPermissionModeFromCLI`` in
    ``typescript/src/utils/permissions/permissionSetup.ts:690``.

    Priority order (first match wins):

    1. ``--dangerously-skip-permissions`` -> ``bypassPermissions``
    2. ``--permission-mode <name>``       -> the parsed mode
    3. ``settings.permissions.defaultMode``
    4. fallback to ``default``

    Unknown / mistyped mode strings degrade to ``default`` via
    :func:`permission_mode_from_string`.

    ``fallback_mode`` replaces the hardcoded ``default`` FLOOR reached when no
    candidate matched. It is how the interactive entrypoints express "nothing
    was configured, so start in full access" (the ``/permissions`` loose
    default) WITHOUT inserting a non-TS candidate into the ladder above — the
    priority list stays a faithful mirror, and "an explicit
    ``--permission-mode default``" stays distinguishable from "nothing
    matched", because any matched candidate returns before the floor.
    Callers are responsible for passing ``default`` when a lockdown or an
    elevated-privileges check should suppress their looser floor.

    ``disable_bypass_permissions_mode`` (critic C12): when a bypass lockdown is
    in effect, the ``bypassPermissions`` candidate is SKIPPED (TS
    permissionSetup.ts:778-793 ``continue``s past it) so the resolved mode
    falls through to the next candidate / ``default``. This is the ONLY
    faithful way to enforce the lockdown, because the port's permission check
    (``check.py:456``) bypasses on ``mode == "bypassPermissions"`` ALONE — the
    availability boolean is consulted only for ``plan`` mode. ``None`` →
    resolve the lockdown here (so every caller is covered by default)."""
    if disable_bypass_permissions_mode is None:
        disable_bypass_permissions_mode = is_bypass_permissions_mode_disabled()
    candidates: list[PermissionMode] = []
    if dangerously_skip_permissions:
        candidates.append("bypassPermissions")
    if permission_mode_cli:
        candidates.append(permission_mode_from_string(permission_mode_cli))
    if settings_default_mode:
        candidates.append(permission_mode_from_string(settings_default_mode))
    for candidate in candidates:
        if candidate == "bypassPermissions" and disable_bypass_permissions_mode:
            log.warning(
                "Bypass permissions mode was disabled by settings/policy "
                "(permissions.disableBypassPermissionsMode); falling back."
            )
            continue  # TS: skip this mode if it's disabled
        return candidate
    # The floor. TS returns "default" unconditionally here; ``fallback_mode``
    # keeps that as the default while letting an interactive launcher raise it.
    if fallback_mode == "bypassPermissions" and disable_bypass_permissions_mode:
        log.warning(
            "Bypass permissions mode was disabled by settings/policy "
            "(permissions.disableBypassPermissionsMode); falling back."
        )
        return "default"
    return fallback_mode


# The ONLY modes a repo-scoped settings file may supply as
# ``permissions.defaultMode``. An allowlist, not a "block bypassPermissions"
# denylist, because looseness is not a property of the mode name alone:
#
#   ``plan`` LOOKS like the strictest mode, but ``check.py``'s ``should_bypass``
#   is ``mode == "bypassPermissions" or (mode == "plan" and
#   is_bypass_permissions_mode_available)`` — and the same disjunction in
#   ``tool_system/context.py`` is what lifts working-root containment. So in any
#   session that has bypass availability (``--allow-dangerously-skip-permissions``,
#   or ``allowBypassPermissionsMode`` plus a user who ran ``/permissions ask``),
#   a repo shipping ``defaultMode: "plan"`` would get unconstrained
#   write-anywhere. A ranking cannot express that, because the answer depends on
#   a second variable the ranking never sees.
#
# ``default`` and ``dontAsk`` are unambiguously restrictive whatever else is
# true of the session, so they are the two a repo may ask for. That still covers
# the case worth supporting — "don't run wide open in this repo" — now that Full
# Access is the interactive default.
_REPO_SETTABLE_DEFAULT_MODES: tuple[str, ...] = ("default", "dontAsk")

# How loose each mode is, low → high. Applied ON TOP of the allowlist above so a
# repo cannot loosen relative to what would otherwise apply (e.g. proposing
# ``default`` to a user who persisted ``dontAsk``).
_MODE_LOOSENESS: dict[str, int] = {
    "dontAsk": 0,
    "plan": 1,
    "default": 2,
    "auto": 3,
    "acceptEdits": 4,
    "bypassPermissions": 5,
}


def _read_settings_file_default_mode(path: str) -> PermissionMode | None:
    """Read ``permissions.defaultMode`` from one settings file, or ``None``."""
    import json
    import os

    try:
        if not path or not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001 — an unreadable/!JSON tier is simply absent
        log.debug("settings file unreadable for defaultMode: %s", path, exc_info=True)
        return None
    perms = (data or {}).get("permissions")
    if not isinstance(perms, dict):
        return None
    mode = perms.get("defaultMode")
    if not isinstance(mode, str) or mode not in EXTERNAL_PERMISSION_MODES:
        return None
    return mode  # type: ignore[return-value]


def _read_config_default_mode() -> PermissionMode | None:
    """``config.json`` → ``settings.permissions.defaultMode`` (user tier).

    Global tier only, deliberately: ``load_global`` rather than the merged
    view, because a repo-committed ``.clawcodex/config.json`` must not be able
    to raise the mode — that is the same trust split the repo-scoped settings
    files get below.
    """
    try:
        from src.config import ConfigManager

        perms = ConfigManager().load_global().get("settings", {}).get("permissions")
    except Exception:  # noqa: BLE001 — an unreadable config tier is simply absent
        log.debug("config.json unreadable for defaultMode", exc_info=True)
        return None
    if not isinstance(perms, dict):
        # `permissions` is also modeled as a flat rule LIST elsewhere in the
        # schema; a list here just means no mode is configured.
        return None
    mode = perms.get("defaultMode")
    if not isinstance(mode, str) or mode not in EXTERNAL_PERMISSION_MODES:
        return None
    return mode  # type: ignore[return-value]


def read_settings_default_mode(
    cwd: str | None = None,
    *,
    baseline_mode: PermissionMode = "default",
) -> PermissionMode | None:
    """Resolve ``permissions.defaultMode`` from the settings files.

    This is the READ side of the mode that ``persist_permission_update``
    (``updates.py`` ``PermissionUpdateSetMode`` branch) has been writing
    write-only since the C1 review — its standing TODO ("setup_permissions does
    not read defaultMode back at startup; wire the read side") is this function.

    The store is the canonical permission settings layer
    (:mod:`src.permissions.settings_paths`: ``~/.clawcodex/settings.json``,
    ``<root>/.clawcodex/settings{,.local}.json``, managed policy) — NOT
    ``~/.clawcodex/config.json["settings"]``. Writing a ``permissions`` DICT
    into the latter would collide with :class:`~src.settings.types.SettingsSchema`,
    whose ``permissions`` field is a typed rule LIST.

    Tiers split into TRUSTED and REPO-SCOPED, which is the security boundary:

    * **trusted** — managed (root-owned MDM policy), then **user**
      (``~/.clawcodex/settings.json``). User comes before any repo file because
      that is where ``/permissions`` writes the user's own explicit choice, and
      a repo must not silently defeat it. First hit wins.
    * **repo-scoped** — ``<cwd>/.clawcodex/settings.local.json`` then
      ``settings.json``. BOTH are treated as untrusted-for-loosening. The
      ``.local`` suffix conventionally means "gitignored, operator-owned", but
      that is a property of *your* ``.gitignore``: a hostile repo simply commits
      the file. Trusting it let a cloned repo hand itself ``bypassPermissions``,
      including on ``clawcodex -p``, which returns before the folder-trust gate.

    A repo-scoped value is honored only when it is both in
    :data:`_REPO_SETTABLE_DEFAULT_MODES` and **no looser** than what would
    otherwise apply, so a repo can tighten but never loosen. ``baseline_mode`` is
    what the caller would use absent any setting — pass the interactive floor
    (``bypassPermissions``) so a repo can tighten it.

    Returns ``None`` when nothing is configured.
    """
    from .settings_paths import (
        local_settings_path,
        project_settings_path,
        user_settings_path,
    )

    trusted: PermissionMode | None = None
    try:
        from src.settings.managed_path import resolve_managed_settings_path

        managed = resolve_managed_settings_path()
        if managed is not None:
            trusted = _read_settings_file_default_mode(str(managed))
    except Exception:  # noqa: BLE001 — no managed policy on this platform
        log.debug("managed settings unavailable for defaultMode", exc_info=True)

    if trusted is None:
        # User tier. `~/.clawcodex/config.json` is the primary home: its
        # `settings.permissions` block is already the source of truth for
        # `allowBypassPermissionsMode` / `disableBypassPermissionsMode` (see
        # has_allow_bypass_permissions_mode and is_bypass_permissions_mode_disabled),
        # so the MODE belongs beside them rather than in a second file whose
        # existence surprised the one person it was meant to serve. The
        # standalone settings.json stays readable as a fallback so an existing
        # choice keeps working; it is no longer where new ones are written.
        trusted = _read_config_default_mode()
    if trusted is None:
        trusted = _read_settings_file_default_mode(user_settings_path())

    effective = trusted if trusted is not None else baseline_mode
    for path in (local_settings_path(cwd), project_settings_path(cwd)):
        proposed = _read_settings_file_default_mode(path)
        if proposed is None:
            continue
        if proposed not in _REPO_SETTABLE_DEFAULT_MODES:
            log.warning(
                "Ignoring permissions.defaultMode=%r from %s: a repository "
                "settings file may only request %s.",
                proposed,
                path,
                " or ".join(_REPO_SETTABLE_DEFAULT_MODES),
            )
            continue
        if _MODE_LOOSENESS.get(proposed, 99) > _MODE_LOOSENESS.get(effective, 99):
            log.warning(
                "Ignoring permissions.defaultMode=%r from %s: a repository "
                "settings file may tighten permissions but not loosen them "
                "(effective mode is %r).",
                proposed,
                path,
                effective,
            )
            continue
        # A repo CAN force `dontAsk`, which denies everything that would prompt.
        # Restrictive, so allowed — but say why, or a user whose agent suddenly
        # refuses every action has no way to trace it back to the repo.
        if proposed == "dontAsk":
            log.warning(
                "%s sets permissions.defaultMode='dontAsk': this session will "
                "DENY anything that would otherwise prompt. Override with "
                "--permission-mode or /permissions.",
                path,
            )
        return proposed
    return trusted


def set_settings_default_mode(mode: PermissionMode) -> bool:
    """Persist ``mode`` as ``permissions.defaultMode`` in the USER settings file.

    Reuses the existing :func:`~src.permissions.updates.persist_permission_update`
    ``setMode`` writer rather than adding a second one, so the on-disk shape stays
    whatever that function already produces. Returns the writer's success flag.

    Refuses anything :func:`read_settings_default_mode` would not read back —
    notably ``auto``, which is a valid runtime mode but not an
    :data:`EXTERNAL_PERMISSION_MODES` member. Writing it would clobber a real
    prior choice with a value nothing consumes.
    """
    if mode not in EXTERNAL_PERMISSION_MODES:
        log.debug("refusing to persist non-external defaultMode %r", mode)
        return False

    # Writes go to config.json, beside the bypass flags that already live in
    # `settings.permissions`, so a user has one file to read and edit. The
    # legacy settings.json is still READ (read_settings_default_mode), but
    # nothing writes it any more — a stale value there is shadowed by the
    # write here, which is what makes this a migration and not a second store.
    try:
        from src.config import ConfigManager

        cm = ConfigManager()
        data = cm.load_global()
        settings = dict(data.get("settings") or {})
        perms = settings.get("permissions")
        # `permissions` doubles as a flat rule LIST in the schema; only a dict
        # can carry the mode, so start one rather than clobbering rules.
        settings["permissions"] = {**perms, "defaultMode": mode} if isinstance(perms, dict) else {"defaultMode": mode}
        data["settings"] = settings
        cm.save_global(data)
        cm.invalidate()
        return True
    except Exception:  # noqa: BLE001 — a failed write must not fail the set
        log.debug("config.json defaultMode persist failed", exc_info=True)
        return False


def is_elevated_without_sandbox() -> bool:
    """True when running as uid 0 outside a sandbox.

    The implicit full-access default is suppressed in that case. This is NOT
    :func:`~src.permissions.dangerous_safety.enforce_dangerous_skip_permissions_safety`
    — that helper ``SystemExit``s, which is right for an explicit
    ``--dangerously-skip-permissions`` but would make a plain ``sudo clawcodex``
    fail to start. An implicit default must degrade, never abort.
    """
    from .dangerous_safety import _is_running_as_root, is_sandbox_environment

    return _is_running_as_root() and not is_sandbox_environment()


def resolve_interactive_permission_state(
    *,
    permission_mode_cli: str | None,
    dangerously_skip_permissions: bool,
    allow_dangerously_skip_permissions: bool,
    cwd: str | None = None,
    implicit_full_access: bool = True,
) -> tuple[PermissionMode, bool, bool]:
    """Resolve ``(mode, is_bypass_available, bypass_selectable)`` for a launch.

    The single resolver shared by ``src/cli.py::_resolve_permission_state`` and
    ``src/entrypoints/tui_launcher.py`` so the two interactive entrypoints cannot
    drift.

    Priority (via :func:`initial_permission_mode_from_cli`, unchanged):
    ``--dangerously-skip-permissions`` → ``--permission-mode`` → persisted
    ``permissions.defaultMode`` → the floor. ``implicit_full_access`` raises the
    floor to ``bypassPermissions``; it is suppressed under an operator lockdown
    (handled inside the ladder) and under root-outside-sandbox (here).

    The two booleans are deliberately distinct:

    ``is_bypass_available``
        the ENGINE flag (``ToolPermissionContext.is_bypass_permissions_mode_available``).
        It also relaxes **plan mode** (``check.py`` ``should_bypass``), so it stays
        strictly opt-in — flags or ``allowBypassPermissionsMode`` only. The
        implicit default must NOT set it, or ``/plan`` would stop restraining.

    ``bypass_selectable``
        whether ``/permissions`` may select Full Access. True for an interactive
        launch unless locked down or elevated. Selecting Full Access sets the
        MODE only and never flips the engine flag, so a later ``/plan`` still asks.
    """
    disabled = is_bypass_permissions_mode_disabled()
    elevated = is_elevated_without_sandbox()

    # `--allow-dangerously-skip-permissions` means, in its own words, "make
    # bypass AVAILABLE without starting in it". That is an explicit statement
    # about the starting mode, so it suppresses the implicit floor — otherwise
    # the flag would silently do the opposite of what it says.
    asked_for_available_not_entered = (
        allow_dangerously_skip_permissions and not dangerously_skip_permissions
    )

    # The floor: full access only when this launch opted in AND nothing forbids
    # it. The ladder re-checks the lockdown itself; the elevated check is ours.
    floor: PermissionMode = "default"
    if (
        implicit_full_access
        and not asked_for_available_not_entered
        and not disabled
        and not elevated
    ):
        floor = "bypassPermissions"

    persisted_mode = read_settings_default_mode(cwd, baseline_mode=floor)

    # A persisted `bypassPermissions` does NOT arm non-interactive launches.
    # Persistence exists so that dialing permissions DOWN survives a relaunch;
    # letting it dial headless UP would mean one `/permissions full` click
    # silently converts every future `clawcodex -p`, in every directory, into a
    # full bypass — and `-p` returns before the folder-trust gate, so nothing
    # would ask. That is precisely the outcome the interactive-only scoping of
    # the implicit floor exists to prevent, and it would falsify the documented
    # contract that headless stays in `default` unless you say otherwise.
    # Saying otherwise is `--dangerously-skip-permissions` or an explicit
    # `--permission-mode`, both of which outrank this candidate anyway.
    #
    # This drops a MANAGED-policy `bypassPermissions` too, not just a user's own
    # `/permissions full` click. Deliberate: it fails safe, and an admin who
    # wants to constrain bypass has `disableBypassPermissionsMode`, not this.
    if not implicit_full_access and persisted_mode == "bypassPermissions":
        log.debug(
            "Ignoring persisted permissions.defaultMode='bypassPermissions' for "
            "a non-interactive launch; pass --dangerously-skip-permissions to "
            "opt in explicitly."
        )
        persisted_mode = None

    mode = initial_permission_mode_from_cli(
        permission_mode_cli=permission_mode_cli,
        dangerously_skip_permissions=dangerously_skip_permissions,
        settings_default_mode=persisted_mode,
        disable_bypass_permissions_mode=disabled,
        fallback_mode=floor,
    )

    is_bypass_available = (
        dangerously_skip_permissions
        or allow_dangerously_skip_permissions
        or has_allow_bypass_permissions_mode()
    ) and not disabled

    bypass_selectable = is_bypass_available or (
        implicit_full_access and not disabled and not elevated
    )

    # Root outside a sandbox clamps the RESOLVED state, not just the floor: the
    # floor guard alone left a persisted `defaultMode: bypassPermissions`
    # (exactly what `/permissions full` writes) and settings-granted
    # availability — which turns `plan` into a full bypass via check.py's
    # `should_bypass` — both reaching root sessions untouched.
    #
    # But it must clamp only what the user did NOT ask for. `--permission-mode
    # bypassPermissions` does NOT go through
    # `enforce_dangerous_skip_permissions_safety` (that guard keys off the two
    # `*-skip-permissions` flags and returns early otherwise), so it arrives here
    # intact — and `docker run … clawcodex --permission-mode bypassPermissions
    # -p …` as root is a routine invocation. Silently rewriting it to `default`
    # would flip those runs from "everything allowed" to "everything denied"
    # (headless denies every ask), announced only by a log line.
    mode_was_explicit = bool(permission_mode_cli) or dangerously_skip_permissions
    if elevated and (mode == "bypassPermissions" or is_bypass_available):
        clamping_mode = mode == "bypassPermissions" and not mode_was_explicit
        log.warning(
            "Running as root outside a sandbox: %s (resolved mode %r, "
            "availability %s). Set IS_SANDBOX=1 or CLAUDE_CODE_BUBBLEWRAP=1 if "
            "this really is a sandbox.",
            "refusing full access" if clamping_mode else "refusing bypass availability",
            mode,
            is_bypass_available,
        )
        if clamping_mode:
            mode = "default"
        # Availability is dropped either way: under root it can only come from
        # settings (both flags that grant it exit before this function), and it
        # is the flag that lifts working-root containment in plan mode.
        is_bypass_available = False
        bypass_selectable = False

    return mode, is_bypass_available, bypass_selectable


def has_allow_bypass_permissions_mode() -> bool:
    """Return True if a trusted settings source enables bypass availability.

    Follows ``hasAllowBypassPermissionsMode`` in
    ``typescript/src/utils/settings/settings.ts:897``, which reads
    ``permissions.allowBypassPermissionsMode`` and EXCLUDES the committable
    project tier so a cloned repo cannot auto-enable bypass (the TS comment: "a
    malicious project could otherwise enable bypass mode (security risk)").

    **The USER (global) tier only** — a deliberate divergence from the TS
    reference, which also reads the *local* tier. Here the local tier is
    ``<git-root>/.clawcodex/config.local.json`` (``config.get_local_config_path``
    is git-root relative, not home relative), so it is just as committable as
    the project tier the exclusion already names; ``.local`` describes a
    convention in *your* ``.gitignore``, not a trust boundary. A repo shipping
    that file could grant itself bypass availability — and availability is not a
    minor capability: ``check.py``'s ``should_bypass`` is ``mode ==
    "bypassPermissions" or (mode == "plan" and
    is_bypass_permissions_mode_available)``, and the same disjunction in
    ``tool_system/context.py`` lifts working-root containment. So a committed
    file could make ``plan`` — the mode whose whole purpose is changing nothing —
    a full write-anywhere bypass, with ``clawcodex -p`` never reaching the
    folder-trust gate. Same reasoning as
    :func:`read_settings_default_mode`'s repo-scoped tiers.

    We read the RAW per-tier config dicts rather than the merged
    ``SettingsSchema`` for two reasons: the schema has no structured slot for
    this scalar (``permissions`` is modeled as a flat rule *list*), and the
    merged view can't tell the tiers apart, which is exactly the distinction the
    security exclusion turns on.
    """
    try:
        from src.config import ConfigManager

        cm = ConfigManager()
        perms = cm.load_global().get("settings", {}).get("permissions")
        return bool(isinstance(perms, dict) and perms.get("allowBypassPermissionsMode"))
    except Exception:
        return False


def is_bypass_permissions_mode_disabled() -> bool:
    """Return True if a settings source DISABLES bypass availability.

    Mirrors TS ``settings.permissions?.disableBypassPermissionsMode ===
    'disable'`` (``permissionSetup.ts:939``), the negative guard on
    ``isBypassPermissionsModeAvailable`` that the port previously dropped — so
    an operator locking bypass down (managed MDM policy, or a user/local
    ``settings.json``) was SILENTLY IGNORED and bypass stayed available
    (a live fail-open; critic C12).

    Unlike the POSITIVE ``allowBypassPermissionsMode`` (which excludes the
    committable project tier so a malicious repo can't ENABLE bypass), a
    ``disable`` only ever REMOVES capability, so honoring it from ANY tier —
    including the managed/policy tier (the org-admin lockdown) and the project
    tier — is always safe. Reads the raw per-tier dicts (the SettingsSchema
    models ``permissions`` as a flat rule list, no scalar slot).

    .. warning::

       The FILE is ``~/.clawcodex/config.json`` (under its ``"settings"`` key),
       plus the managed policy file — the :class:`~src.config.ConfigManager`
       tiers, NOT the ``settings.json`` layer that
       :func:`read_settings_default_mode` and ``setup_permissions`` read. Two
       adjacent permission knobs, two different files; an operator who sets
       ``disableBypassPermissionsMode`` in ``~/.clawcodex/settings.json`` gets
       silence, not a lockdown."""
    try:
        from src.config import ConfigManager

        cm = ConfigManager()
        for loader in (cm.load_global, cm.load_local, cm.load_project):
            perms = loader().get("settings", {}).get("permissions")
            if isinstance(perms, dict) and perms.get("disableBypassPermissionsMode") == "disable":
                return True
        # Managed/policy tier (root-owned MDM preferences) — the primary
        # lockdown source. Read directly (setup.py:57 pattern).
        try:
            import json

            from src.settings.managed_path import resolve_managed_settings_path

            mp = resolve_managed_settings_path()
            if mp is not None and mp.exists():
                with mp.open() as f:
                    managed = json.load(f)
                perms = (managed or {}).get("permissions")
                if isinstance(perms, dict) and perms.get("disableBypassPermissionsMode") == "disable":
                    return True
        except Exception:
            # A managed policy file that fails to parse/read would silently
            # fail-OPEN the lockdown (parity with TS dropping a malformed
            # source); log it so an admin can diagnose why their policy isn't
            # taking effect (critic C12).
            log.debug("managed settings unreadable for bypass-disable check", exc_info=True)
    except Exception:
        return False
    return False
