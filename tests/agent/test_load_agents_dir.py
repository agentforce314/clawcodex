"""Tests for custom subagent discovery (src/agent/load_agents_dir.py).

Mirrors the headline scenarios from
typescript/src/tools/AgentTool/loadAgentsDir.test.ts plus the
Python-specific cache + MCP filter behaviours.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.agent_definitions import AgentDefinition, get_built_in_agents
from src.agent.filter_agents_by_mcp import filter_agents_by_mcp_requirements
from src.agent.load_agents_dir import (
    clear_agent_definitions_cache,
    get_agent_definitions_with_overrides,
)


def _write_agent(
    path: Path,
    *,
    name: str = "critic",
    description: str = "Test critic agent",
    extra_frontmatter: str = "",
    body: str = "You are a critic.",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter_parts = [f"name: {name}", f"description: {description}"]
    if extra_frontmatter:
        frontmatter_parts.append(extra_frontmatter)
    content = (
        "---\n"
        + "\n".join(frontmatter_parts)
        + "\n---\n"
        + body
        + "\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolated_config_dirs(tmp_path, monkeypatch):
    user_dir = tmp_path / "claude_home"
    managed_dir = tmp_path / "managed"
    user_dir.mkdir()
    managed_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_dir))
    monkeypatch.setenv("CLAUDE_MANAGED_CONFIG_DIR", str(managed_dir))
    clear_agent_definitions_cache()
    yield {"user": user_dir, "managed": managed_dir, "tmp_path": tmp_path}
    clear_agent_definitions_cache()


def _by_type(agents: list[AgentDefinition]) -> dict[str, AgentDefinition]:
    return {a.agent_type: a for a in agents}


def test_user_dir_agent_loaded(_isolated_config_dirs, tmp_path):
    """An agent in ~/.claude/agents/ is discoverable."""
    user_dir = _isolated_config_dirs["user"]
    _write_agent(user_dir / "agents" / "critic.md")
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    assert "critic" in _by_type(agents)
    assert _by_type(agents)["critic"].when_to_use == "Test critic agent"


def test_project_dir_walk_up_to_home(_isolated_config_dirs, tmp_path):
    """A project agent is found when cwd is a nested subdir of the project."""
    proj = tmp_path / "proj"
    nested_cwd = proj / "src" / "sub"
    nested_cwd.mkdir(parents=True)
    _write_agent(
        proj / ".claude" / "agents" / "reviewer.md",
        name="reviewer",
        description="Project reviewer",
    )
    agents = get_agent_definitions_with_overrides(str(nested_cwd))
    assert "reviewer" in _by_type(agents)


def test_project_overrides_user_same_agent_type(_isolated_config_dirs, tmp_path):
    """A project-defined agent wins over a same-named user-defined one."""
    user_dir = _isolated_config_dirs["user"]
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_agent(
        user_dir / "agents" / "foo.md",
        name="foo",
        description="from user",
    )
    _write_agent(
        proj / ".claude" / "agents" / "foo.md",
        name="foo",
        description="from project",
    )
    agents = get_agent_definitions_with_overrides(str(proj))
    assert _by_type(agents)["foo"].when_to_use == "from project"


def test_managed_wins_over_project(_isolated_config_dirs, tmp_path):
    """Managed/policy source has the highest priority among custom sources."""
    managed_dir = _isolated_config_dirs["managed"]
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_agent(
        proj / ".claude" / "agents" / "foo.md",
        name="foo",
        description="from project",
    )
    _write_agent(
        managed_dir / ".claude" / "agents" / "foo.md",
        name="foo",
        description="from managed",
    )
    agents = get_agent_definitions_with_overrides(str(proj))
    assert _by_type(agents)["foo"].when_to_use == "from managed"


def test_builtin_overridden_by_user_same_type(_isolated_config_dirs, tmp_path):
    """A custom agent named Explore overrides the built-in Explore."""
    user_dir = _isolated_config_dirs["user"]
    _write_agent(
        user_dir / "agents" / "explore.md",
        name="Explore",
        description="my custom explore",
    )
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    explore = _by_type(agents).get("Explore")
    assert explore is not None
    assert explore.when_to_use == "my custom explore"
    assert explore.source == "user"


def test_malformed_frontmatter_does_not_crash(_isolated_config_dirs, tmp_path):
    """A file with broken YAML is silently dropped; siblings still load."""
    user_dir = _isolated_config_dirs["user"]
    agents_dir = user_dir / "agents"
    agents_dir.mkdir(parents=True)
    # Broken frontmatter (unterminated quote)
    (agents_dir / "bad.md").write_text(
        '---\nname: bad\ndescription: "unterminated\n---\nbody\n',
        encoding="utf-8",
    )
    _write_agent(agents_dir / "good.md", name="good", description="good one")
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    types = _by_type(agents)
    assert "good" in types
    assert "bad" not in types


def test_builtin_priority_preserved_when_no_collision(_isolated_config_dirs, tmp_path):
    """With no custom agents, built-ins are returned unchanged."""
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    builtin_types = {a.agent_type for a in get_built_in_agents()}
    discovered_types = _by_type(agents).keys()
    assert builtin_types.issubset(discovered_types)


def test_mcp_filter_drops_agent_missing_required_server(_isolated_config_dirs, tmp_path):
    """An agent declaring required-mcp-servers is filtered out when unavailable."""
    user_dir = _isolated_config_dirs["user"]
    _write_agent(
        user_dir / "agents" / "slack-bot.md",
        name="slack-bot",
        description="needs slack",
        extra_frontmatter="required-mcp-servers:\n  - slack",
    )
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    assert "slack-bot" in _by_type(agents)

    filtered_no_mcp = filter_agents_by_mcp_requirements(agents, [])
    assert "slack-bot" not in _by_type(filtered_no_mcp)

    filtered_with_mcp = filter_agents_by_mcp_requirements(agents, ["slack"])
    assert "slack-bot" in _by_type(filtered_with_mcp)


def test_mcp_filter_keeps_builtins_regardless(_isolated_config_dirs, tmp_path):
    """Built-ins survive the MCP filter even when the available set is empty."""
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    filtered = filter_agents_by_mcp_requirements(agents, [])
    builtin_types = {a.agent_type for a in get_built_in_agents()}
    discovered = _by_type(filtered).keys()
    assert builtin_types.issubset(discovered)


def test_cache_invalidation_picks_up_new_file(_isolated_config_dirs, tmp_path):
    """The cache hides new files until clear_agent_definitions_cache() is called."""
    user_dir = _isolated_config_dirs["user"]
    _write_agent(user_dir / "agents" / "first.md", name="first", description="first")
    first_call = get_agent_definitions_with_overrides(str(tmp_path))
    assert "first" in _by_type(first_call)

    _write_agent(user_dir / "agents" / "second.md", name="second", description="second")
    stale_call = get_agent_definitions_with_overrides(str(tmp_path))
    assert "second" not in _by_type(stale_call)

    clear_agent_definitions_cache()
    fresh_call = get_agent_definitions_with_overrides(str(tmp_path))
    assert "second" in _by_type(fresh_call)


def test_git_root_boundary_blocks_parent_dir_leak(_isolated_config_dirs, tmp_path):
    """Agents in dirs above the project's git-root must not leak in.

    Layout:
        tmp_path/parent/.claude/agents/leaky.md   (must NOT appear)
        tmp_path/parent/proj/.git/                (the project's git root)
        tmp_path/parent/proj/src/                 (cwd)
    """
    parent = tmp_path / "parent"
    proj = parent / "proj"
    cwd = proj / "src"
    cwd.mkdir(parents=True)
    (proj / ".git").mkdir()
    _write_agent(parent / ".claude" / "agents" / "leaky.md", name="leaky", description="parent")
    agents = get_agent_definitions_with_overrides(str(cwd))
    assert "leaky" not in _by_type(agents)


def test_project_inside_git_root_still_loads(_isolated_config_dirs, tmp_path):
    """The git-root boundary stops the walk AT the root, not before it."""
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    cwd = proj / "src" / "nested"
    cwd.mkdir(parents=True)
    _write_agent(proj / ".claude" / "agents" / "ok.md", name="ok", description="root-level agent")
    agents = get_agent_definitions_with_overrides(str(cwd))
    assert "ok" in _by_type(agents)


def test_managed_source_label_preserved(_isolated_config_dirs, tmp_path):
    """Agents loaded from the managed dir keep ``source='managed'``."""
    managed_dir = _isolated_config_dirs["managed"]
    _write_agent(
        managed_dir / ".claude" / "agents" / "policy.md",
        name="policy",
        description="from managed",
    )
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    policy = _by_type(agents).get("policy")
    assert policy is not None
    assert policy.source == "managed"


def test_at_agent_mention_resolves_custom_agent(_isolated_config_dirs, tmp_path):
    """``@agent-<custom>`` mention syntax sees on-disk agents.

    Regression for the REPL ``_available_agents`` wire-up: the old code
    extended the agent list with ``dict.values()`` from the wrapping
    ``{"active_agents": [...]}`` shape, producing a single nested list
    that ``expand_agent_mentions`` couldn't introspect — every
    @agent-<custom> token was silently dropped.
    """
    from src.command_system.input_processing import expand_agent_mentions

    user_dir = _isolated_config_dirs["user"]
    _write_agent(
        user_dir / "agents" / "critic.md",
        name="critic",
        description="reviewer",
    )
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    attachments = expand_agent_mentions("@agent-critic please review", agents)
    assert {"kind": "agent_mention", "agent_type": "critic"} in attachments


def test_cache_dedupes_path_aliases(_isolated_config_dirs, tmp_path):
    """``cwd`` with a trailing slash hits the same cache entry."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_agent(
        proj / ".claude" / "agents" / "x.md",
        name="x",
        description="x",
    )
    a = get_agent_definitions_with_overrides(str(proj))
    b = get_agent_definitions_with_overrides(str(proj) + "/")
    assert [agent.agent_type for agent in a] == [agent.agent_type for agent in b]


def test_sdk_register_agents_appears_in_discovery(_isolated_config_dirs, tmp_path):
    """``register_sdk_agents`` agents merge with ``flag`` precedence (overrides user/project)."""
    from src.agent.agent_definitions import AgentDefinition
    from src.agent.load_agents_dir import clear_sdk_agents, register_sdk_agents

    user_dir = _isolated_config_dirs["user"]
    _write_agent(
        user_dir / "agents" / "shared.md",
        name="shared",
        description="from user",
    )
    sdk_agent = AgentDefinition(
        agent_type="shared",
        when_to_use="from sdk flag",
        source="user",
        base_dir="json",
        get_system_prompt=lambda **_kw: "sdk body",
    )
    register_sdk_agents([sdk_agent])
    try:
        agents = get_agent_definitions_with_overrides(str(tmp_path))
        assert _by_type(agents)["shared"].when_to_use == "from sdk flag"
    finally:
        clear_sdk_agents()


def test_managed_still_beats_sdk_flag(_isolated_config_dirs, tmp_path):
    """SDK ``flag`` agents do NOT override managed/policy agents."""
    from src.agent.agent_definitions import AgentDefinition
    from src.agent.load_agents_dir import clear_sdk_agents, register_sdk_agents

    managed_dir = _isolated_config_dirs["managed"]
    _write_agent(
        managed_dir / ".claude" / "agents" / "shared.md",
        name="shared",
        description="from managed",
    )
    register_sdk_agents([
        AgentDefinition(
            agent_type="shared",
            when_to_use="from sdk",
            source="user",
            base_dir="json",
            get_system_prompt=lambda **_kw: "",
        )
    ])
    try:
        agents = get_agent_definitions_with_overrides(str(tmp_path))
        assert _by_type(agents)["shared"].when_to_use == "from managed"
    finally:
        clear_sdk_agents()


def test_worktree_fallback_uses_main_repo_agents(_isolated_config_dirs, tmp_path):
    """When a worktree lacks .claude/agents/, main repo's copy is used.

    Layout:
        tmp_path/main/.git/                       (real git dir)
        tmp_path/main/.claude/agents/critic.md    (main-only agent)
        tmp_path/main/.git/worktrees/wt/          (worktree's gitdir)
        tmp_path/wt/.git                          (file: gitdir: <main>/.git/worktrees/wt)
        # no tmp_path/wt/.claude/ at all → fallback kicks in
    """
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt.mkdir()
    (wt / ".git").write_text(
        f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n",
        encoding="utf-8",
    )
    _write_agent(
        main / ".claude" / "agents" / "critic.md",
        name="critic",
        description="main-only",
    )
    agents = get_agent_definitions_with_overrides(str(wt))
    assert "critic" in _by_type(agents)


def test_worktree_no_fallback_when_worktree_has_agents(_isolated_config_dirs, tmp_path):
    """Worktree fallback skipped when the worktree already has its own agents/."""
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt.mkdir()
    (wt / ".git").write_text(
        f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n",
        encoding="utf-8",
    )
    _write_agent(
        main / ".claude" / "agents" / "shared.md",
        name="shared",
        description="from main",
    )
    _write_agent(
        wt / ".claude" / "agents" / "shared.md",
        name="shared",
        description="from worktree",
    )
    agents = get_agent_definitions_with_overrides(str(wt))
    assert _by_type(agents)["shared"].when_to_use == "from worktree"


def test_builtins_disabled_when_sdk_env_var_set(_isolated_config_dirs, tmp_path, monkeypatch):
    """``CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS`` removes built-ins in SDK mode."""
    from src.agent.load_agents_dir import clear_agent_definitions_cache

    monkeypatch.setenv("CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS", "true")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
    clear_agent_definitions_cache()
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    types = {a.agent_type for a in agents}
    assert "general-purpose" not in types
    assert "Explore" not in types
    assert "Plan" not in types


def test_builtins_kept_when_env_var_set_but_not_sdk_entrypoint(
    _isolated_config_dirs, tmp_path, monkeypatch
):
    """Interactive REPL ignores the env var — only SDK entrypoints honor it."""
    from src.agent.load_agents_dir import clear_agent_definitions_cache

    monkeypatch.setenv("CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS", "true")
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    clear_agent_definitions_cache()
    agents = get_agent_definitions_with_overrides(str(tmp_path))
    types = {a.agent_type for a in agents}
    assert "general-purpose" in types
