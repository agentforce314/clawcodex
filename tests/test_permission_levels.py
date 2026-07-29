"""The three `/permissions` levels, and the launch resolution behind them.

Covers the contract added when `/mode` became `/permissions` and interactive
launches started defaulting to Full Access:

* the level catalog and its TypeScript mirror,
* `resolve_interactive_permission_state` — precedence, and the suppressors that
  must be able to override the loose default,
* `permissions.defaultMode` read/write against the canonical settings store.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.permissions.levels import (
    PERMISSION_LEVEL_KEYS,
    PERMISSION_LEVELS,
    level_for_key,
    level_for_mode,
    mode_for_key,
)
from src.permissions.modes import (
    read_settings_default_mode,
    resolve_interactive_permission_state,
    set_settings_default_mode,
)
from src.permissions.types import PERMISSION_MODES

_TS_MIRROR = Path(__file__).resolve().parents[1] / "ui-tui" / "src" / "lib" / "permissionLevels.ts"


# --------------------------------------------------------------------------- #
# The catalog
# --------------------------------------------------------------------------- #
class TestLevelCatalog:
    def test_three_levels_loosest_last(self):
        assert [lv.key for lv in PERMISSION_LEVELS] == ["ask", "approve", "full"]
        assert [lv.mode for lv in PERMISSION_LEVELS] == [
            "default",
            "acceptEdits",
            "bypassPermissions",
        ]

    def test_keys_never_collide_with_engine_mode_names(self):
        """`/permissions <arg>` takes a level key OR a raw mode; the two
        namespaces must stay disjoint or the arg is ambiguous. In particular the
        middle level is `approve`, not `auto` — `auto` is a real mode."""
        assert not (set(PERMISSION_LEVEL_KEYS) & set(PERMISSION_MODES))

    def test_off_ladder_modes_have_no_level(self):
        # plan / dontAsk / auto are real modes with no picker row. Callers must
        # get None so they render "no level" rather than mislabeling them.
        for mode in ("plan", "dontAsk", "auto", "bubble", "nonsense"):
            assert level_for_mode(mode) is None

    def test_lookup_helpers(self):
        assert mode_for_key("FULL") == "bypassPermissions"
        assert mode_for_key(" ask ") == "default"
        assert mode_for_key("plan") is None
        assert level_for_key("approve").label == "Approve for me"
        assert level_for_mode("acceptEdits").key == "approve"

    def test_typescript_mirror_matches(self):
        """`ui-tui/src/lib/permissionLevels.ts` is a hand-kept mirror; the picker
        and every backend consumer must describe the levels identically. A drift
        here is a picker that misrepresents what it is about to permit."""
        block = (
            _TS_MIRROR.read_text(encoding="utf-8")
            .split("export const PERMISSION_LEVELS", 1)[1]
            .split("\n]", 1)[0]
        )
        # Prettier wraps long string literals onto their own line, so compare
        # against a whitespace-normalized source rather than a shape-sensitive
        # per-object regex.
        flat = " ".join(block.split())

        assert len(re.findall(r"key: '", flat)) == len(PERMISSION_LEVELS)
        assert [m for m in re.findall(r"key: '([^']+)'", flat)] == [
            lv.key for lv in PERMISSION_LEVELS
        ], "level ORDER must match — the picker numbers rows 1-3 from it"

        for lv in PERMISSION_LEVELS:
            assert f"key: '{lv.key}'" in flat
            assert f"label: '{lv.label}'" in flat
            assert f"mode: '{lv.mode}'" in flat
            assert " ".join(lv.description.split()) in flat, f"description drift: {lv.key}"


# --------------------------------------------------------------------------- #
# Launch resolution
# --------------------------------------------------------------------------- #
@pytest.fixture
def settings_home(tmp_path, monkeypatch):
    """`(user_settings_file, work_dir)` for the four-tier settings store.

    The user tier is resolved through :mod:`src.permissions.settings_paths`
    rather than assumed to be ``~/.clawcodex/settings.json``: conftest's autouse
    isolation already redirects that function so tests never read the
    developer's real settings, and hardcoding the home path here would write to
    a file nothing reads.
    """
    from src.permissions import settings_paths

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return Path(settings_paths.user_settings_path()), work


def _write_settings(path: Path, **permissions):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": permissions}), encoding="utf-8")


def _resolve(**kw):
    kw.setdefault("permission_mode_cli", None)
    kw.setdefault("dangerously_skip_permissions", False)
    kw.setdefault("allow_dangerously_skip_permissions", False)
    return resolve_interactive_permission_state(**kw)


def _tool_ctx(mode, available):
    from src.permissions.types import ToolPermissionContext

    return ToolPermissionContext(
        mode=mode, is_bypass_permissions_mode_available=available,
    )


class TestInteractiveDefault:
    def test_bare_interactive_launch_is_full_access(self, settings_home):
        mode, available, selectable = _resolve()
        assert mode == "bypassPermissions"
        assert selectable is True
        # …but NOT engine availability: that flag also relaxes plan mode, so
        # setting it here would make /plan permit everything.
        assert available is False

    def test_explicit_permission_mode_default_beats_the_loose_floor(self, settings_home):
        # "flag absent" vs "explicitly asked for prompts" must be distinguishable.
        mode, _available, _sel = _resolve(permission_mode_cli="default")
        assert mode == "default"

    def test_dangerously_skip_permissions_is_unchanged(self, settings_home):
        mode, available, selectable = _resolve(dangerously_skip_permissions=True)
        assert (mode, available, selectable) == ("bypassPermissions", True, True)

    def test_allow_flag_grants_availability_without_entering_bypass(self, settings_home):
        mode, available, selectable = _resolve(
            permission_mode_cli="default", allow_dangerously_skip_permissions=True
        )
        assert (mode, available, selectable) == ("default", True, True)

    def test_allow_flag_alone_suppresses_the_loose_floor(self, settings_home):
        """`--allow-dangerously-skip-permissions` says "available WITHOUT
        starting in it". Letting the implicit floor start the session in Full
        Access anyway would make the flag do the opposite of what it says."""
        mode, available, selectable = _resolve(allow_dangerously_skip_permissions=True)
        assert (mode, available, selectable) == ("default", True, True)

    def test_non_interactive_keeps_default(self, settings_home):
        """`--print` / headless must not inherit the loose default: CI and the
        Harbor eval harness drive it."""
        mode, available, selectable = _resolve(implicit_full_access=False)
        assert (mode, available, selectable) == ("default", False, False)


class TestSuppressors:
    def test_operator_lockdown_suppresses_the_implicit_default(self, settings_home, monkeypatch):
        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: True
        )
        mode, available, selectable = _resolve()
        assert mode == "default"
        assert available is False
        assert selectable is False

    def test_lockdown_also_overrides_an_explicit_flag(self, settings_home, monkeypatch):
        monkeypatch.setattr(
            "src.permissions.modes.is_bypass_permissions_mode_disabled", lambda: True
        )
        mode, available, _sel = _resolve(dangerously_skip_permissions=True)
        assert mode == "default"
        assert available is False

    def test_root_outside_sandbox_degrades_instead_of_exiting(self, settings_home, monkeypatch):
        """`sudo clawcodex` with no flags must still START — just not wide open.

        The explicit-flag path keeps its SystemExit
        (`enforce_dangerous_skip_permissions_safety`); an IMPLICIT default has
        to degrade, or elevating would break the app entirely.
        """
        monkeypatch.setattr("src.permissions.modes.is_elevated_without_sandbox", lambda: True)
        mode, _available, selectable = _resolve()  # must not raise SystemExit
        assert mode == "default"
        assert selectable is False

    def test_root_clamps_a_persisted_full_access_choice(self, settings_home, monkeypatch):
        """The suppressor has to clamp the RESOLVED mode, not just the floor.

        `/permissions full` writes `defaultMode: bypassPermissions`, which is a
        settings candidate that outranks the floor — so guarding only the floor
        left `sudo clawcodex` running root-full-access silently.
        """
        user_settings, _work = settings_home
        _write_settings(user_settings, defaultMode="bypassPermissions")
        monkeypatch.setattr("src.permissions.modes.is_elevated_without_sandbox", lambda: True)
        mode, available, selectable = _resolve()
        assert (mode, available, selectable) == ("default", False, False)

    def test_root_does_not_discard_an_explicit_permission_mode(self, settings_home, monkeypatch):
        """The clamp must not silently rewrite a flag the user typed.

        `--permission-mode bypassPermissions` does NOT go through
        `enforce_dangerous_skip_permissions_safety` (that guard keys off the two
        `*-skip-permissions` flags), so it reaches the clamp intact — and
        `docker run … clawcodex --permission-mode bypassPermissions -p …` as root
        is a routine invocation. Rewriting it to `default` would flip those runs
        from everything-allowed to everything-DENIED (headless denies every
        ask), announced only by a log line.
        """
        monkeypatch.setattr("src.permissions.modes.is_elevated_without_sandbox", lambda: True)
        mode, available, selectable = _resolve(
            permission_mode_cli="bypassPermissions", implicit_full_access=False
        )
        assert mode == "bypassPermissions"
        # Availability is still dropped: under root it can only come from
        # settings, and it is what lifts containment in plan mode.
        assert (available, selectable) == (False, False)

    def test_root_clamps_settings_granted_availability(self, settings_home, monkeypatch):
        """Availability alone is enough for a full bypass via plan mode
        (`check.py` `should_bypass`), so root must drop it too. The explicit
        flags never get here — the caller exits on those first."""
        monkeypatch.setattr("src.permissions.modes.is_elevated_without_sandbox", lambda: True)
        monkeypatch.setattr(
            "src.permissions.modes.has_allow_bypass_permissions_mode", lambda: True
        )
        _mode, available, selectable = _resolve()
        assert available is False
        assert selectable is False


class TestPersistedDefaultMode:
    def test_persisted_choice_beats_the_implicit_default(self, settings_home):
        user_settings, _work = settings_home
        _write_settings(user_settings, defaultMode="default")
        mode, _available, selectable = _resolve()
        assert mode == "default"
        # …and Full Access stays SELECTABLE so /permissions can go back.
        assert selectable is True

    def test_explicit_flags_beat_a_persisted_choice_without_overwriting_it(self, settings_home):
        path, _work = settings_home
        _write_settings(path, defaultMode="default")
        mode, _a, _s = _resolve(dangerously_skip_permissions=True)
        assert mode == "bypassPermissions"
        # A per-run override must not rewrite the standing preference.
        assert json.loads(path.read_text())["permissions"]["defaultMode"] == "default"

    def test_round_trip_through_the_public_writer(self, settings_home):
        assert set_settings_default_mode("acceptEdits") is True
        assert read_settings_default_mode() == "acceptEdits"

    def test_persisted_choice_applies_to_headless_too(self, settings_home):
        """An explicit user choice must not be honored interactively and ignored
        by `clawcodex -p`; only the implicit FLOOR is interactive-only."""
        user_settings, _work = settings_home
        _write_settings(user_settings, defaultMode="acceptEdits")
        mode, _a, _s = _resolve(implicit_full_access=False)
        assert mode == "acceptEdits"

    def test_a_persisted_full_access_does_not_arm_headless(self, settings_home):
        """…with one exception, in the safe direction.

        Persistence exists so dialing DOWN survives a relaunch. Letting it dial
        headless UP would mean one `/permissions full` click silently converts
        every future `clawcodex -p`, in every directory, into a full bypass —
        and `-p` returns before the folder-trust gate, so nothing would ask.
        That is the outcome the interactive-only floor exists to prevent.
        """
        user_settings, _work = settings_home
        _write_settings(user_settings, defaultMode="bypassPermissions")
        mode, _a, _s = _resolve(implicit_full_access=False)
        assert mode == "default"
        # Saying otherwise is still possible, explicitly.
        assert _resolve(
            dangerously_skip_permissions=True, implicit_full_access=False
        )[0] == "bypassPermissions"
        assert _resolve(
            permission_mode_cli="bypassPermissions", implicit_full_access=False
        )[0] == "bypassPermissions"

    def test_a_persisted_full_access_still_applies_interactively(self, settings_home):
        user_settings, _work = settings_home
        _write_settings(user_settings, defaultMode="bypassPermissions")
        assert _resolve()[0] == "bypassPermissions"

    def test_unknown_or_malformed_values_are_ignored(self, settings_home):
        path, _work = settings_home
        _write_settings(path, defaultMode="nonsense")
        assert read_settings_default_mode() is None
        path.write_text("{not json", encoding="utf-8")
        assert read_settings_default_mode() is None


class TestSettingsTierPrecedence:
    def test_user_tier_beats_a_repo_file_trying_to_loosen(self, settings_home):
        """The picker writes the USER tier, so a committable repo file must not
        silently defeat the user's explicit choice by loosening it."""
        user_settings, work = settings_home
        _write_settings(user_settings, defaultMode="default")
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="bypassPermissions")
        assert read_settings_default_mode(str(work)) == "default"

    def test_a_repo_may_still_tighten_over_the_user_tier(self, settings_home):
        """The other direction is the point of honoring the project tier at all:
        with Full Access as the default, "don't run wide open in this repo" is
        the most valuable thing a project can declare."""
        user_settings, work = settings_home
        _write_settings(user_settings, defaultMode="acceptEdits")
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="default")
        assert read_settings_default_mode(str(work)) == "default"

    def test_project_tier_may_tighten(self, settings_home):
        _home, work = settings_home
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="default")
        # Nothing in the trusted tiers; the interactive floor is Full Access, so
        # a repo asking for prompts is a tightening and is honored.
        assert read_settings_default_mode(str(work), baseline_mode="bypassPermissions") == "default"

    def test_project_tier_may_not_loosen(self, settings_home):
        _home, work = settings_home
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="bypassPermissions")
        assert read_settings_default_mode(str(work), baseline_mode="default") is None

    def test_project_tier_may_not_loosen_via_accept_edits_either(self, settings_home):
        """The guard is a direction rule, not a `bypassPermissions` blocklist:
        acceptEdits also loosens `default` (auto-accepted edits + write commands)
        for a user who deliberately chose "Ask for approval"."""
        user_settings, work = settings_home
        _write_settings(user_settings, defaultMode="default")
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="acceptEdits")
        assert read_settings_default_mode(str(work)) == "default"

    def test_local_tier_is_repo_scoped_and_cannot_loosen(self, settings_home):
        """`settings.local.json` is NOT trusted.

        The `.local` suffix conventionally means "gitignored, operator-owned",
        but that is a property of *your* `.gitignore` — a hostile repo simply
        commits the file. Treating it as trusted let `git clone && clawcodex -p`
        run with the permission gate off, and `-p` returns before the
        folder-trust gate, so nothing would have prompted.
        """
        _home, work = settings_home
        _write_settings(work / ".clawcodex" / "settings.local.json", defaultMode="bypassPermissions")
        assert read_settings_default_mode(str(work), baseline_mode="default") is None

    def test_local_tier_may_tighten_and_beats_project(self, settings_home):
        _home, work = settings_home
        _write_settings(work / ".clawcodex" / "settings.local.json", defaultMode="default")
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="dontAsk")
        assert read_settings_default_mode(str(work), baseline_mode="bypassPermissions") == "default"

    def test_a_repo_may_not_supply_plan(self, settings_home):
        """`plan` looks like the strictest mode and is not.

        `check.py`'s `should_bypass` is `mode == "bypassPermissions" or (mode ==
        "plan" and is_bypass_permissions_mode_available)`, and the same
        disjunction in `tool_system/context.py` lifts working-root containment.
        So in any session with bypass availability, a repo shipping
        `defaultMode: "plan"` would get unconstrained write-anywhere — which is
        why the repo tier is an ALLOWLIST, not a looseness ranking.
        """
        _home, work = settings_home
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="plan")
        assert read_settings_default_mode(str(work), baseline_mode="bypassPermissions") is None

    def test_a_repo_supplied_plan_cannot_reach_write_anywhere(self, settings_home):
        """The end-to-end version of the above, through the real gate."""
        import dataclasses

        from src.permissions.check import has_permissions_to_use_tool
        from src.permissions.types import ToolPermissionContext
        from src.tool_system.tools.write import WriteTool

        _home, work = settings_home
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="plan")

        mode, available, _sel = _resolve(
            allow_dangerously_skip_permissions=True, cwd=str(work)
        )
        ctx = ToolPermissionContext(
            mode=mode, is_bypass_permissions_mode_available=available,
        )
        decision = has_permissions_to_use_tool(
            WriteTool, {"file_path": "/etc/evil.txt", "content": "x"}, ctx,
        )
        assert decision.behavior != "allow", (
            f"a repo-supplied defaultMode reached write-anywhere (mode={mode!r}, "
            f"available={available})"
        )
        # Sanity: the same context WOULD allow it if plan had been honored, so
        # the assertion above is not passing for an unrelated reason.
        plan_ctx = dataclasses.replace(ctx, mode="plan")
        assert has_permissions_to_use_tool(
            WriteTool, {"file_path": "/etc/evil.txt", "content": "x"}, plan_ctx,
        ).behavior == "allow"

    def test_a_repo_may_still_force_dont_ask(self, settings_home):
        # Restrictive, so allowed — a repo-side annoyance, not an escalation.
        _home, work = settings_home
        _write_settings(work / ".clawcodex" / "settings.json", defaultMode="dontAsk")
        assert read_settings_default_mode(str(work), baseline_mode="bypassPermissions") == "dontAsk"
