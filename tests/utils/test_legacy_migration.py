"""Tests for the one-time ``.claude`` → ``.clawcodex`` migration.

The safety contract under test:
  * copy-only — the legacy tree is byte-identical afterwards;
  * destination-absent-only — existing ``.clawcodex`` content is never
    overwritten;
  * marker-gated — the user-level pass runs once, ``force`` re-attempts;
  * oversized skill dirs are skipped, small siblings still copied;
  * settings files and worktrees are never migrated.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.utils import legacy_migration as lm
from src.utils.clawcodex_dirs import (
    get_managed_config_dir,
    get_user_config_dir,
)


@pytest.fixture()
def fake_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAWCODEX_CONFIG_DIR", raising=False)
    # conftest disables migration suite-wide; these tests are the ones
    # that exercise it (against the fake home above).
    monkeypatch.delenv("CLAWCODEX_DISABLE_LEGACY_MIGRATION", raising=False)
    return home


def _snapshot(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        out[rel] = path.read_bytes() if path.is_file() else b"<dir>"
    return out


# ---------------------------------------------------------------------------
# clawcodex_dirs
# ---------------------------------------------------------------------------


def test_user_config_dir_default_and_env(fake_home, monkeypatch):
    assert get_user_config_dir() == fake_home / ".clawcodex"
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(fake_home / "custom"))
    assert get_user_config_dir() == fake_home / "custom"
    # The real Claude Code harness's override must NOT leak in.
    monkeypatch.delenv("CLAWCODEX_CONFIG_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home / "claude-home"))
    assert get_user_config_dir() == fake_home / ".clawcodex"


def test_managed_config_dir_default_and_env(monkeypatch):
    monkeypatch.delenv("CLAWCODEX_MANAGED_CONFIG_DIR", raising=False)
    assert str(get_managed_config_dir()) == "/etc/clawcodex"
    monkeypatch.setenv("CLAWCODEX_MANAGED_CONFIG_DIR", "/opt/policy")
    assert str(get_managed_config_dir()) == "/opt/policy"


# ---------------------------------------------------------------------------
# migrate_user_dir_once
# ---------------------------------------------------------------------------


def test_user_migration_copies_and_preserves_source(fake_home):
    legacy = fake_home / ".claude"
    (legacy / "skills" / "greet").mkdir(parents=True)
    (legacy / "skills" / "greet" / "SKILL.md").write_text("hello")
    (legacy / "agents").mkdir()
    (legacy / "agents" / "critic.md").write_text("agent")
    # The legacy harness's real file name — migration renames it to the
    # canonical CLAWCODEX.md at the destination (_MIGRATE_RENAMES).
    (legacy / "CLAUDE.md").write_text("global memory")
    mem = legacy / "projects" / "-repo-a" / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("index")
    # Session transcripts must NOT be copied.
    (legacy / "projects" / "-repo-a" / "session.jsonl").write_text("{}")

    before = _snapshot(legacy)
    report = lm.migrate_user_dir_once()

    assert report is not None
    assert sorted(report.copied) == [
        "CLAWCODEX.md",
        "agents",
        "projects/-repo-a/memory",
        "skills/greet",
    ]
    assert report.errors == []

    dst = fake_home / ".clawcodex"
    assert (dst / "skills" / "greet" / "SKILL.md").read_text() == "hello"
    assert (dst / "agents" / "critic.md").read_text() == "agent"
    assert (dst / "CLAWCODEX.md").read_text() == "global memory"
    assert (dst / "projects" / "-repo-a" / "memory" / "MEMORY.md").read_text() == "index"
    assert not (dst / "projects" / "-repo-a" / "session.jsonl").exists()

    # Copy-only: the legacy tree is untouched.
    assert _snapshot(legacy) == before
    # Marker written; second call short-circuits.
    assert (dst / lm.MARKER_FILENAME).exists()
    assert lm.migrate_user_dir_once() is None


def test_user_migration_never_overwrites_existing_destination(fake_home):
    legacy = fake_home / ".claude"
    (legacy / "agents").mkdir(parents=True)
    (legacy / "agents" / "critic.md").write_text("legacy version")
    dst_agents = fake_home / ".clawcodex" / "agents"
    dst_agents.mkdir(parents=True)
    (dst_agents / "critic.md").write_text("clawcodex version")

    report = lm.migrate_user_dir_once()

    assert report is not None
    assert (dst_agents / "critic.md").read_text() == "clawcodex version"
    assert {"item": "agents", "reason": "destination already exists"} in report.skipped


def test_user_migration_skips_oversized_skill_dir(fake_home, monkeypatch):
    monkeypatch.setattr(lm, "_MAX_SKILL_DIR_BYTES", 10)
    legacy_skills = fake_home / ".claude" / "skills"
    (legacy_skills / "small").mkdir(parents=True)
    (legacy_skills / "small" / "SKILL.md").write_text("ok")
    (legacy_skills / "huge").mkdir()
    (legacy_skills / "huge" / "blob.bin").write_bytes(b"x" * 1000)

    report = lm.migrate_user_dir_once()

    assert report is not None
    assert "skills/small" in report.copied
    dst_skills = fake_home / ".clawcodex" / "skills"
    assert (dst_skills / "small" / "SKILL.md").exists()
    assert not (dst_skills / "huge").exists()
    huge_skips = [s for s in report.skipped if s["item"] == "skills/huge"]
    assert len(huge_skips) == 1 and "cap" in huge_skips[0]["reason"]


def test_user_migration_without_legacy_dir_writes_marker(fake_home):
    report = lm.migrate_user_dir_once()
    assert report is not None
    assert report.copied == []
    assert (fake_home / ".clawcodex" / lm.MARKER_FILENAME).exists()
    marker = json.loads(
        (fake_home / ".clawcodex" / lm.MARKER_FILENAME).read_text()
    )
    assert marker["copied"] == []


def test_user_migration_force_reattempts_after_marker(fake_home):
    assert lm.migrate_user_dir_once() is not None
    # New legacy content appears AFTER the first pass (e.g. the user ran
    # real Claude Code in between) — force re-attempts, marker alone skips.
    legacy = fake_home / ".claude"
    (legacy / "workflows").mkdir(parents=True)
    (legacy / "workflows" / "triage.py").write_text("meta = {}")
    assert lm.migrate_user_dir_once() is None
    report = lm.migrate_user_dir_once(force=True)
    assert report is not None and "workflows" in report.copied
    assert (fake_home / ".clawcodex" / "workflows" / "triage.py").exists()


def test_user_migration_env_override_destination(fake_home, monkeypatch):
    custom = fake_home / "custom-home"
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(custom))
    legacy = fake_home / ".claude"
    (legacy / "plugins").mkdir(parents=True)
    (legacy / "plugins" / "p.json").write_text("{}")

    report = lm.migrate_user_dir_once()

    assert report is not None and "plugins" in report.copied
    assert (custom / "plugins" / "p.json").exists()
    assert (custom / lm.MARKER_FILENAME).exists()


def test_user_migration_preserves_symlinks_as_symlinks(fake_home):
    legacy = fake_home / ".claude"
    (legacy / "skills" / "linked").mkdir(parents=True)
    target = fake_home / "elsewhere.txt"
    target.write_text("target")
    os.symlink(target, legacy / "skills" / "linked" / "ref.txt")

    report = lm.migrate_user_dir_once()

    assert report is not None and "skills/linked" in report.copied
    copied_link = fake_home / ".clawcodex" / "skills" / "linked" / "ref.txt"
    assert copied_link.is_symlink()
    assert os.readlink(copied_link) == str(target)


# ---------------------------------------------------------------------------
# migrate_project_dir
# ---------------------------------------------------------------------------


def test_project_migration_copies_config_and_skips_settings(tmp_path):
    project = tmp_path / "repo"
    legacy = project / ".claude"
    (legacy / "skills" / "deploy").mkdir(parents=True)
    (legacy / "skills" / "deploy" / "SKILL.md").write_text("deploy")
    (legacy / "config.json").write_text("{}")
    (legacy / "settings.local.json").write_text('{"permissions": {}}')
    (legacy / "worktrees" / "wt-a").mkdir(parents=True)

    before = _snapshot(legacy)
    report = lm.migrate_project_dir(project)

    assert sorted(report.copied) == ["config.json", "skills"]
    dst = project / ".clawcodex"
    assert (dst / "skills" / "deploy" / "SKILL.md").exists()
    assert (dst / "config.json").exists()
    assert not (dst / "settings.local.json").exists()
    assert not (dst / "worktrees").exists()
    skipped_items = {s["item"] for s in report.skipped}
    assert {"settings.local.json", "worktrees"} <= skipped_items
    assert _snapshot(legacy) == before  # copy-only

    # Idempotent: second run copies nothing new.
    report2 = lm.migrate_project_dir(project)
    assert report2.copied == []


def test_project_migration_without_legacy_dir(tmp_path):
    report = lm.migrate_project_dir(tmp_path)
    assert report.copied == []
    assert report.skipped and report.skipped[0]["item"] == "*"
