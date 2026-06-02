"""P0-6 Option B / Phase 3.5 — skill registration + execution-path convergence.

Pins the contract that user-invocable skills register into the GLOBAL command
registry and execute through a ``SkillPromptCommand`` whose render is identical
*by construction* to the model's Skill-tool path (``_run_markdown_skill``).

Covers T1-T8 of
``my-docs/get-parity-by-folder/commands-phase3.5-skill-registration-plan.md`` §6:

  T1  Gating parity — SkillPromptCommand render == Skill-tool render (+ R2
      namespaced re-resolution).
  T2  END-TO-END REPL dispatch — ``/p06-gizmo widget`` routes through the
      registry path, calls ``chat`` once, never ``_try_run_skill_slash``.
  T3  Bare form preserved — ``/p06-gizmo`` opens the palette, no ``chat``.
  T4  Error → no double-dispatch (D-5) — a registered skill whose render raises
      surfaces the error and does NOT fall through to the Skill-tool fallback.
  T5  Unregistered fallback — a disk skill not in the registry runs once via
      ``_try_run_skill_slash``.
  T6  Shadowing — a skill named ``review`` does NOT replace the builtin.
  T7  Degradation — no ToolContext → headless render (arg + ${…} resolved,
      shell block left verbatim), no crash.
  T8  TUI — ``dispatch_registry_command`` returns ``handled=True`` + the same
      prompt text (was ``handled=False`` + literal-text forward).

**Mandatory harness (§6).** The global ``_REGISTRY`` is process-global and never
cleared between sessions; the D-4 guard is skip-if-present, so a name left over
from an earlier test would silently block a later test's same-named skill. Every
test therefore runs under ``_clean_global_registry`` (clear + re-register
builtins around each test) — the re-register step is what gives T6 a builtin
``review`` to defend.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Iterator
from unittest.mock import Mock, patch

import pytest

import src.config as config_module
from src.command_system import (
    SkillPromptCommand,
    create_command_context,
    get_command_registry,
    load_and_register_skills,
    register_builtin_commands,
    skill_to_prompt_command,
)
from src.command_system.builtins import REVIEW_COMMAND
from src.skills.bundled_skills import clear_bundled_skills
from src.skills.loader import (
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
    get_all_skills,
    get_registered_skill,
)
from src.tool_system.context import ToolContext
from src.tool_system.tools.skill import _run_markdown_skill
from src.tui.commands import dispatch_registry_command


# ----------------------------------------------------------------------
# Fixture skill bodies (written to a tmp workspace's .claude/skills tree).
# ----------------------------------------------------------------------

# Exercises every transform: a named arg ($topic), ${CLAUDE_SKILL_DIR},
# ${CLAUDE_SESSION_ID}, and an inline `!`…`` shell block (leading space
# before `!` is required by the renderer's lookbehind).
GIZMO_SKILL = """\
---
description: P0-6 gizmo fixture exercising every transform.
allowed-tools: [Bash]
arguments: [topic]
argument-hint: <topic>
---
# Gizmo

Investigate `$topic` thoroughly.
Skill base: ${CLAUDE_SKILL_DIR}
Session: ${CLAUDE_SESSION_ID}
Shell says: !`echo gizmo-shell-marker`
"""

# Nested namespace → resolves as ``widgets:build`` (R2: name == loader key).
NAMESPACED_SKILL = """\
---
description: Namespaced fixture (resolves widgets:build).
arguments: [target]
argument-hint: <target>
---
# Build Widget

Build the `$target` widget.
"""

# Named like the ``review`` builtin — must be skipped by the shadowing guard.
REVIEW_SKILL = """\
---
description: A skill named like the review builtin (shadowing target).
---
# Fake Review

This skill must NOT shadow the builtin /review.
"""

# On disk but deliberately left unregistered for the fallback test.
LATE_SKILL = """\
---
description: On disk but never registered (unregistered-fallback fixture).
arguments: [thing]
argument-hint: <thing>
---
# Late

Handle `$thing` late.
"""


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolate every env knob that would inject a non-fixture skill dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in (
        "CLAUDE_CONFIG_DIR",
        "CLAWCODEX_SKILLS_DIR",
        "CLAUDE_SKILLS_DIR",
        "CLAWCODEX_MANAGED_SKILLS_DIR",
        "CLAUDE_CODE_BARE_MODE",
        "CLAUDE_CODE_DISABLE_POLICY_SKILLS",
        "CLAUDE_CODE_ADDITIONAL_DIRECTORIES",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    yield home


@pytest.fixture(autouse=True)
def _clean_skill_state() -> Iterator[None]:
    """Force a cold loader walk each test (skills live on disk per-tmp)."""
    clear_skill_caches()
    clear_dynamic_skills()
    clear_skill_registry()
    clear_bundled_skills()
    yield
    clear_skill_caches()
    clear_dynamic_skills()
    clear_skill_registry()
    clear_bundled_skills()


@pytest.fixture(autouse=True)
def _clean_global_registry() -> Iterator[None]:
    """Mandatory harness (§6): clear the process-global command registry and
    re-register builtins around each test, so the skip-if-present D-4 guard
    can't make these order-dependent and T6 has a builtin ``review`` present.
    """
    reg = get_command_registry()
    reg.clear()
    register_builtin_commands(None)
    yield
    reg.clear()
    register_builtin_commands(None)


@pytest.fixture(autouse=True)
def _reset_config_manager() -> Iterator[None]:
    """Drop the cached ConfigManager singleton after any test that built a
    REPL (which repoints GLOBAL_CONFIG_FILE) so config state can't leak."""
    yield
    config_module._default_manager = None


@pytest.fixture
def gizmo_ws(tmp_path: Path, isolated_home: Path) -> Path:
    """A workspace whose ``.claude/skills/`` holds the four fixture skills."""
    ws = tmp_path / "proj"
    skills = ws / ".claude" / "skills"

    def _write(rel: str, body: str) -> None:
        path = skills / rel / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    _write("p06-gizmo", GIZMO_SKILL)
    _write("widgets/build", NAMESPACED_SKILL)
    _write("review", REVIEW_SKILL)
    _write("late", LATE_SKILL)
    return ws


def _tc(ws: Path, session_id: str = "S-p06") -> ToolContext:
    """A ToolContext for ``ws`` with a deterministic session id. The default
    permission context is ``bypassPermissions`` (so the gizmo shell block runs
    end-to-end, matching the e2e-suite pattern)."""
    tc = ToolContext(workspace_root=ws)
    tc.session_id = session_id
    return tc


def _make_repl(ws: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a real ``ClawcodexREPL`` (the T2-T5 entrypoint owner) pointed at
    ``ws``, then install a clean, controlled global registry: builtins plus the
    on-disk fixtures, exactly as ``_init_command_system`` would but scoped to
    ``ws`` (whose discovery we suppress during construction to keep it fast and
    hermetic). Threads a fixed ToolContext onto both the REPL and its command
    context so rendered prompts are byte-stable.
    """
    cfg_dir = ws / ".clawcodex"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "default_provider": "glm",
                "providers": {
                    "glm": {
                        "api_key": "test_api_key_12345678",
                        "base_url": "https://open.bigmodel.cn/api/paas/v4",
                        "default_model": "glm-4.5",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_FILE", cfg_file)
    config_module._default_manager = None

    from src.repl import ClawcodexREPL

    with patch("src.config.get_config_path", return_value=cfg_file), patch(
        "src.repl.core.Session.create", return_value=Mock()
    ), patch("src.repl.core.get_provider_class") as mock_pc, patch(
        # Suppress the broad bootstrap skill-walk during construction: it would
        # scan the real cwd and is slow. We register the ws fixtures ourselves
        # below, which is what "as bootstrap does (global)" means here.
        "src.repl.core.load_and_register_skills"
    ), patch(
        "src.repl.core.Path.cwd", return_value=ws
    ):
        prov = Mock()
        prov.model = "glm-4.5"
        mock_pc.return_value = prov
        repl = ClawcodexREPL(provider_name="glm")

    reg = get_command_registry()
    reg.clear()
    register_builtin_commands(None)
    load_and_register_skills(registry=None, project_root=ws)

    tc = _tc(ws)
    repl.tool_context = tc
    repl.command_context.tool_context = tc
    return repl


# ======================================================================
# T1 — Gating parity (the test that makes Option B safe).
# ======================================================================


def test_t1_gating_parity(gizmo_ws: Path) -> None:
    ws = gizmo_ws
    tc = _tc(ws)

    get_all_skills(project_root=ws)
    skill = get_registered_skill("p06-gizmo")
    assert skill is not None

    cmd = skill_to_prompt_command(skill)
    assert isinstance(cmd, SkillPromptCommand), (
        "skill_to_prompt_command must yield a SkillPromptCommand so execution "
        "routes through the faithful renderer, not bare substitution"
    )
    assert cmd.name == "p06-gizmo"

    ctx = create_command_context(workspace_root=ws, tool_context=tc)
    new_text = asyncio.run(cmd.get_prompt_for_command("widget", ctx))[0]["text"]
    old_text = _run_markdown_skill("p06-gizmo", "widget", tc).output["prompt"]

    # The core assertion — render equality with the Skill-tool path.
    assert new_text == old_text

    # Sanity: the transforms actually fired (arg, both vars, shell exec).
    assert "widget" in new_text
    assert "${CLAUDE_SKILL_DIR}" not in new_text
    assert "${CLAUDE_SESSION_ID}" not in new_text
    assert "S-p06" in new_text
    assert "gizmo-shell-marker" in new_text  # embedded shell block executed

    # R2 — namespaced skill: registered name == loader key, delegation by that
    # name resolves (the failure mode if name/key disagree).
    ns = get_registered_skill("widgets:build")
    assert ns is not None
    ns_cmd = skill_to_prompt_command(ns)
    assert ns_cmd.name == "widgets:build"
    ns_ctx = create_command_context(workspace_root=ws, tool_context=tc)
    ns_new = asyncio.run(ns_cmd.get_prompt_for_command("Button", ns_ctx))[0]["text"]
    ns_old = _run_markdown_skill("widgets:build", "Button", tc).output["prompt"]
    assert ns_new == ns_old
    assert "Button" in ns_new


# ======================================================================
# T2 — END-TO-END REPL dispatch (proves the wiring, not just fn equality).
# ======================================================================


def test_t2_repl_success_dispatch(gizmo_ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = gizmo_ws
    repl = _make_repl(ws, monkeypatch)
    tc = repl.command_context.tool_context

    repl.chat = Mock()
    repl._try_run_skill_slash = Mock(wraps=repl._try_run_skill_slash)

    repl.handle_command("/p06-gizmo widget")

    expected = _run_markdown_skill("p06-gizmo", "widget", tc).output["prompt"]
    repl.chat.assert_called_once()
    assert repl.chat.call_args.args[0] == expected
    repl._try_run_skill_slash.assert_not_called()


# ======================================================================
# T3 — Bare form preserved (`/p06-gizmo` → palette, not execution).
# ======================================================================


def test_t3_repl_bare_form_opens_palette(
    gizmo_ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repl = _make_repl(gizmo_ws, monkeypatch)
    repl.chat = Mock()
    repl._show_slash_palette = Mock()

    repl.handle_command("/p06-gizmo")

    repl._show_slash_palette.assert_called_once()
    repl.chat.assert_not_called()


# ======================================================================
# T4 — Error → no double-dispatch (D-5 guard is load-bearing).
# ======================================================================


def test_t4_repl_error_no_double_dispatch(
    gizmo_ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = gizmo_ws
    repl = _make_repl(ws, monkeypatch)

    # A registered (global-only) skill whose render raises.
    boom = SkillPromptCommand(
        name="boom",
        description="boom",
        markdown_content="x",
        skill_root=str(ws),
        loaded_from="project",
    )
    get_command_registry().register(boom)

    def _raise(*args, **kwargs):
        raise RuntimeError("render boom")

    monkeypatch.setattr("src.tool_system.tools.skill._run_markdown_skill", _raise)

    repl.chat = Mock()
    repl._try_run_skill_slash = Mock(wraps=repl._try_run_skill_slash)

    repl.handle_command("/boom arg")

    # The error surfaced via the registry path; the Skill-tool fallback (which
    # would re-render and double-dispatch) was NOT reached. Without the D-5
    # change (guard on the GLOBAL registry) this fails: boom is global-only, so
    # a local-registry guard misses it and falls through to the fallback.
    repl._try_run_skill_slash.assert_not_called()
    repl.chat.assert_not_called()


# ======================================================================
# T5 — Unregistered fallback (disk skill not in the registry).
# ======================================================================


def test_t5_repl_unregistered_fallback(
    gizmo_ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = gizmo_ws
    repl = _make_repl(ws, monkeypatch)

    # Model "present on disk but not registered": drop the registry back to
    # builtins-only, leaving ``late`` discoverable only via the Skill tool.
    reg = get_command_registry()
    reg.clear()
    register_builtin_commands(None)

    tc = repl.command_context.tool_context
    repl.chat = Mock()
    repl._try_run_skill_slash = Mock(wraps=repl._try_run_skill_slash)

    repl.handle_command("/late arg")

    repl._try_run_skill_slash.assert_called_once()
    expected = _run_markdown_skill("late", "arg", tc).output["prompt"]
    repl.chat.assert_called_once()
    assert repl.chat.call_args.args[0] == expected


# ======================================================================
# T6 — Shadowing (a skill named `review` must not replace the builtin).
# ======================================================================


def test_t6_shadowing_builtin_wins(gizmo_ws: Path) -> None:
    ws = gizmo_ws
    reg = get_command_registry()
    reg.clear()
    register_builtin_commands(None)
    load_and_register_skills(registry=None, project_root=ws)

    got = reg.get("review")
    assert got is REVIEW_COMMAND, "builtin /review must win over the fixture skill"
    assert not isinstance(got, SkillPromptCommand)

    # Sanity: a non-colliding fixture skill DID register (so the skip above is
    # the guard firing, not registration silently doing nothing).
    assert isinstance(reg.get("p06-gizmo"), SkillPromptCommand)


# ======================================================================
# T7 — Degradation (no ToolContext → headless render, no crash).
# ======================================================================


def test_t7_headless_degradation(gizmo_ws: Path) -> None:
    ws = gizmo_ws
    get_all_skills(project_root=ws)
    cmd = skill_to_prompt_command(get_registered_skill("p06-gizmo"))

    ctx = create_command_context(workspace_root=ws, tool_context=None)
    text = asyncio.run(cmd.get_prompt_for_command("widget", ctx))[0]["text"]

    assert "widget" in text  # arg substituted
    assert "${CLAUDE_SKILL_DIR}" not in text  # var resolved
    assert "${CLAUDE_SESSION_ID}" not in text  # var resolved
    # No executor off the REPL/TUI path → shell block survives verbatim.
    assert "!`echo gizmo-shell-marker`" in text


# ======================================================================
# T8 — TUI dispatch (handled=True + same prompt text).
# ======================================================================


def test_t8_tui_dispatch(gizmo_ws: Path) -> None:
    ws = gizmo_ws
    tc = _tc(ws)
    load_and_register_skills(registry=None, project_root=ws)

    ctx = create_command_context(workspace_root=ws, tool_context=tc)
    result = asyncio.run(
        dispatch_registry_command("/p06-gizmo widget", command_context=ctx)
    )

    assert result.handled is True
    expected = _run_markdown_skill("p06-gizmo", "widget", tc).output["prompt"]
    assert result.prompt_text == expected
