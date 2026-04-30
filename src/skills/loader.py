from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from .argument_substitution import parse_argument_names
from .frontmatter import parse_frontmatter
from .model import Skill

logger = logging.getLogger(__name__)

LoadedFrom = str


def _get_global_config_dir() -> Path:
    env_override = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path.home() / ".claude"


def _get_managed_file_path() -> Path:
    env_override = os.environ.get("CLAUDE_MANAGED_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path("/etc/claude")


def get_skills_path(source: str, dir_type: str = "skills") -> str:
    if source == "policySettings":
        return str(_get_managed_file_path() / ".claude" / dir_type)
    elif source == "userSettings":
        return str(_get_global_config_dir() / dir_type)
    elif source == "projectSettings":
        return f".claude/{dir_type}"
    elif source == "plugin":
        return "plugin"
    return ""


def _is_skill_file(file_path: str | Path) -> bool:
    return Path(file_path).name.lower() == "skill.md"


def _build_namespace(target_dir: str, base_dir: str) -> str:
    base = base_dir.rstrip(os.sep)
    if target_dir == base:
        return ""
    rel = target_dir[len(base) + 1:]
    return ":".join(rel.split(os.sep)) if rel else ""


def _get_skill_command_name(file_path: str, base_dir: str) -> str:
    skill_directory = str(Path(file_path).parent)
    parent_of_skill_dir = str(Path(skill_directory).parent)
    command_base_name = Path(skill_directory).name

    namespace = _build_namespace(parent_of_skill_dir, base_dir)
    return f"{namespace}:{command_base_name}" if namespace else command_base_name


# ----------------------------------------------------------------------
# Field validators (mirrored from TS)
#
# These ride alongside ``parse_skill_frontmatter_fields`` and degrade
# gracefully — a bad value logs a debug warning and either drops the
# field (``None``) or keeps a permissive coerced value, mirroring TS
# behavior of "fall back to default; never block-load the skill".
# ----------------------------------------------------------------------

# Mirrors TS ``EFFORT_LEVELS`` (utils/effort.ts).
EFFORT_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "max"})

# Mirrors TS ``FRONTMATTER_SHELLS`` (utils/frontmatterParser.ts).
FRONTMATTER_SHELLS: frozenset[str] = frozenset({"bash", "powershell"})


def _extract_description_from_markdown(content: str, default: str) -> str:
    """Port of TS ``extractDescriptionFromMarkdown``.

    Pulls the first non-empty line, strips a leading ``#`` heading
    prefix, and clamps to 100 chars (the TS limit) with a trailing
    ``...`` ellipsis when truncated. Falls back to ``default`` if no
    content line is found.
    """
    for line in (content or "").splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        # Strip ``#``/``##``/etc. heading prefix
        m = re.match(r"^#+\s+(.+)$", trimmed)
        text = m.group(1) if m else trimmed
        if len(text) > 100:
            return text[:97] + "..."
        return text
    return default


def _coerce_description(
    raw: Any, markdown_content: str, resolved_name: str
) -> tuple[str, bool]:
    """Return ``(description, has_user_specified_description)``.

    Precedence: explicit frontmatter ``description`` > first-line of
    markdown body > generated ``Skill: <name>`` placeholder.
    """
    if raw is None or raw == "":
        body_desc = _extract_description_from_markdown(
            markdown_content, default=f"Skill: {resolved_name}"
        )
        return body_desc, False

    if isinstance(raw, list):
        return " ".join(str(x) for x in raw), True
    return str(raw), True


def _coerce_bool(value: Any, *, default: bool) -> bool:
    """Coerce a YAML scalar to bool; falls back to ``default`` for
    non-recognized values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1")
    return default


def _coerce_allowed_tools(value: Any) -> list[str]:
    """Parse the ``allowed-tools`` field.

    Accepts a string (comma-separated) or a list. Each entry can be a
    bare tool name (``Read``) or a tool with a parenthesized arg pattern
    (``Bash(git status:*)``). Both forms are preserved verbatim so the
    permission layer can apply its own matching semantics.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, list):
        return [str(t).strip() for t in value if str(t).strip()]
    return []


def _coerce_model(value: Any) -> str | None:
    """Validate the ``model`` field.

    ``inherit`` / falsy → ``None`` (use caller default). Otherwise
    return as a string. Logs a debug warning when the value isn't in
    ``MODEL_ALIASES`` AND doesn't look like a canonical provider model
    (``claude-``, ``gpt-``, ``o1-``, etc.) — but always keeps the value
    so the user can pin a brand-new model that the alias table hasn't
    learned about yet.
    """
    if value is None or value == "" or value == "inherit":
        return None
    s = str(value).strip()
    if not s:
        return None

    try:
        from src.models.aliases import MODEL_ALIASES
        known_aliases = set(MODEL_ALIASES.keys())
    except Exception:
        known_aliases = set()

    lower = s.lower()
    looks_canonical = any(
        lower.startswith(prefix)
        for prefix in (
            "claude-", "gpt-", "o1-", "o3-", "o4-",
            "grok-", "gemini-", "deepseek-", "glm-", "qwen-",
        )
    )
    if lower not in known_aliases and not looks_canonical:
        logger.warning(
            "skill frontmatter model %r is not a recognized alias or "
            "canonical model name; keeping as-is",
            s,
        )
    return s


def _coerce_effort(value: Any) -> str | None:
    """Port of TS ``parseEffortValue``.

    Accepts:
      - One of ``EFFORT_LEVELS`` (case-insensitive) → returned lowercased.
      - An integer (or numeric string) → returned as a string.
    Anything else logs a warning and returns ``None`` (mirrors TS
    "drop on invalid").
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        # bool is a subclass of int in Python — exclude explicitly
        logger.warning("skill frontmatter effort=%r is not a valid level", value)
        return None
    if isinstance(value, int):
        return str(value)
    s = str(value).strip().lower()
    if not s:
        return None
    if s in EFFORT_LEVELS:
        return s
    try:
        n = int(s, 10)
        return str(n)
    except (TypeError, ValueError):
        pass
    logger.warning(
        "skill frontmatter effort=%r is not a valid level "
        "(expected one of %s or an integer); dropping",
        value,
        sorted(EFFORT_LEVELS),
    )
    return None


def _coerce_shell(value: Any) -> str | None:
    """Port of TS ``parseShellFrontmatter`` — accepts ``bash`` /
    ``powershell``; logs and drops anything else."""
    if value is None or value == "":
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s in FRONTMATTER_SHELLS:
        return s
    logger.warning(
        "skill frontmatter shell=%r is not recognized "
        "(valid: %s); falling back to default",
        value,
        sorted(FRONTMATTER_SHELLS),
    )
    return None


def _coerce_hooks(value: Any, *, skill_name: str) -> dict | None:
    """Validate the ``hooks:`` frontmatter dict against the rough TS
    schema (``Partial<Record<HookEvent, HookMatcher[]>>``).

    Schema (loose check, no Zod equivalent):
      hooks:
        <HookEvent>:               # one of ALL_HOOK_EVENTS
          - matcher: <str>         # optional
            hooks:                 # required, list
              - type: <str>        # required ("command" / "agent" / ...)
                ...                # other shape-specific fields
                                   #   (passed through verbatim)

    Returns the parsed dict on shape-match. Returns ``None`` (and logs
    a debug message) on any structural mismatch — the SkillTool can
    keep loading the skill, just without the hooks.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        logger.debug(
            "skill %r hooks: expected dict, got %s; dropping",
            skill_name,
            type(value).__name__,
        )
        return None

    try:
        from src.hooks.hook_types import ALL_HOOK_EVENTS
        valid_events = set(ALL_HOOK_EVENTS)
    except Exception:
        valid_events = set()

    for event_name, matchers in value.items():
        if valid_events and event_name not in valid_events:
            logger.debug(
                "skill %r hooks: unknown event %r; dropping all hooks",
                skill_name,
                event_name,
            )
            return None
        if not isinstance(matchers, list):
            logger.debug(
                "skill %r hooks.%s: expected list of matchers, got %s",
                skill_name,
                event_name,
                type(matchers).__name__,
            )
            return None
        for matcher in matchers:
            if not isinstance(matcher, dict):
                logger.debug(
                    "skill %r hooks.%s: matcher must be a dict",
                    skill_name,
                    event_name,
                )
                return None
            inner = matcher.get("hooks")
            if not isinstance(inner, list):
                logger.debug(
                    "skill %r hooks.%s.hooks: required list missing or wrong type",
                    skill_name,
                    event_name,
                )
                return None
            for cmd in inner:
                if not isinstance(cmd, dict) or "type" not in cmd:
                    logger.debug(
                        "skill %r hooks.%s.hooks[]: each entry needs a `type` field",
                        skill_name,
                        event_name,
                    )
                    return None

    return value


def parse_skill_frontmatter_fields(
    frontmatter: dict[str, Any],
    markdown_content: str,
    resolved_name: str,
) -> dict[str, Any]:
    raw_desc = frontmatter.get("description")
    description, has_user_specified_description = _coerce_description(
        raw_desc, markdown_content, resolved_name
    )

    user_invocable = _coerce_bool(frontmatter.get("user-invocable", True), default=True)
    disable_model = _coerce_bool(
        frontmatter.get("disable-model-invocation", False), default=False
    )

    allowed_tools = _coerce_allowed_tools(frontmatter.get("allowed-tools"))

    argument_names = parse_argument_names(frontmatter.get("arguments"))

    when_to_use = frontmatter.get("when_to_use")
    if when_to_use is not None:
        when_to_use = str(when_to_use)

    version = frontmatter.get("version")
    if version is not None:
        version = str(version)

    model = _coerce_model(frontmatter.get("model"))

    context = frontmatter.get("context", "inline")
    execution_context = "fork" if context == "fork" else None

    agent = frontmatter.get("agent")
    if agent is not None:
        agent = str(agent)

    effort = _coerce_effort(frontmatter.get("effort"))

    shell = _coerce_shell(frontmatter.get("shell"))

    hooks = _coerce_hooks(frontmatter.get("hooks"), skill_name=resolved_name)

    argument_hint_raw = frontmatter.get("argument-hint")
    argument_hint = str(argument_hint_raw) if argument_hint_raw is not None else None

    display_name_raw = frontmatter.get("name")
    display_name = str(display_name_raw) if display_name_raw is not None else None

    paths_raw = frontmatter.get("paths")
    paths = None
    if paths_raw:
        if isinstance(paths_raw, str):
            paths = [p.strip() for p in paths_raw.split(",") if p.strip()]
        elif isinstance(paths_raw, list):
            paths = [str(p) for p in paths_raw]
        if paths:
            cleaned: list[str] = []
            for p in paths:
                if p.endswith("/**"):
                    p = p[:-3]
                if p and p != "**":
                    cleaned.append(p)
            paths = cleaned if cleaned else None

    return {
        "display_name": display_name,
        "description": description,
        "has_user_specified_description": has_user_specified_description,
        "allowed_tools": allowed_tools,
        "argument_hint": argument_hint,
        "argument_names": argument_names,
        "when_to_use": when_to_use,
        "version": version,
        "model": model,
        "disable_model_invocation": disable_model,
        "user_invocable": user_invocable,
        "execution_context": execution_context,
        "agent": agent,
        "effort": effort,
        "paths": paths,
        "hooks": hooks,
        "shell": shell,
    }


def create_skill_command(
    *,
    skill_name: str,
    display_name: str | None,
    description: str,
    has_user_specified_description: bool,
    markdown_content: str,
    allowed_tools: list[str],
    argument_hint: str | None,
    argument_names: list[str],
    when_to_use: str | None,
    version: str | None,
    model: str | None,
    disable_model_invocation: bool,
    user_invocable: bool,
    source: str,
    base_dir: str | None,
    loaded_from: str,
    execution_context: str | None = None,
    agent: str | None = None,
    paths: list[str] | None = None,
    effort: str | None = None,
    hooks: dict | None = None,
    shell: str | None = None,
) -> Skill:
    return Skill(
        name=skill_name,
        description=description,
        content=markdown_content,
        source=source,
        loaded_from=loaded_from,
        user_invocable=user_invocable,
        disable_model_invocation=disable_model_invocation,
        content_length=len(markdown_content),
        is_hidden=not user_invocable,
        skill_root=base_dir,
        aliases=[],
        allowed_tools=allowed_tools,
        argument_hint=argument_hint,
        argument_names=argument_names,
        when_to_use=when_to_use,
        version=version,
        model=model,
        context=execution_context or "inline",
        agent=agent,
        effort=effort,
        paths=paths,
        display_name=display_name,
        has_user_specified_description=has_user_specified_description,
        base_dir=base_dir,
        markdown_content=markdown_content,
        hooks=hooks,
        shell=shell,
    )


def _find_skill_markdown_files(base_path: str) -> list[str]:
    base = Path(base_path)
    if not base.is_dir():
        return []

    visited: set[str] = set()
    skill_files: list[str] = []

    def _walk(skill_dir_path: Path) -> None:
        try:
            resolved = str(skill_dir_path.resolve())
        except OSError:
            return
        if resolved in visited:
            return
        visited.add(resolved)

        try:
            entries = list(skill_dir_path.iterdir())
        except (PermissionError, OSError):
            return

        child_dirs: list[Path] = []
        for entry in entries:
            if _is_skill_file(entry):
                skill_files.append(str(entry))
                continue
            if entry.is_dir():
                child_dirs.append(entry)
            elif entry.is_symlink():
                try:
                    if entry.resolve().is_dir():
                        child_dirs.append(entry)
                except (OSError, ValueError):
                    pass

        for d in child_dirs:
            _walk(d)

    try:
        top_entries = list(base.iterdir())
    except (PermissionError, OSError):
        return []

    top_dirs: list[Path] = []
    for entry in top_entries:
        if entry.is_dir():
            top_dirs.append(entry)
        elif entry.is_symlink():
            try:
                if entry.resolve().is_dir():
                    top_dirs.append(entry)
            except (OSError, ValueError):
                pass

    for d in top_dirs:
        _walk(d)

    skill_files.sort()
    return skill_files


_SOURCE_TO_LOADED_FROM: dict[str, str] = {
    "policySettings": "managed",
    "userSettings": "user",
    "projectSettings": "project",
    "plugin": "plugin",
}


def load_skills_from_skills_dir(
    base_path: str,
    source: str,
    *,
    loaded_from: str | None = None,
) -> list[Skill]:
    """Load every ``SKILL.md`` under ``base_path`` recursively.

    ``loaded_from`` defaults to a friendly label derived from ``source``
    (``policySettings`` -> ``managed``, ``userSettings`` -> ``user``,
    ``projectSettings`` -> ``project``). Callers can pass an explicit
    string to override (used by the legacy registry path).
    """
    resolved_loaded_from = (
        loaded_from
        if loaded_from is not None
        else _SOURCE_TO_LOADED_FROM.get(source, "skills")
    )

    skill_files = _find_skill_markdown_files(base_path)
    skills: list[Skill] = []

    for skill_file_path in skill_files:
        try:
            content = Path(skill_file_path).read_text(encoding="utf-8")
        except (OSError, PermissionError):
            continue

        try:
            result = parse_frontmatter(content)
            frontmatter = result.frontmatter
            markdown_content = result.body
        except Exception:
            continue

        skill_name = _get_skill_command_name(skill_file_path, base_path)
        parsed = parse_skill_frontmatter_fields(
            frontmatter, markdown_content, skill_name
        )

        skill = create_skill_command(
            skill_name=skill_name,
            display_name=parsed["display_name"],
            description=parsed["description"],
            has_user_specified_description=parsed["has_user_specified_description"],
            markdown_content=markdown_content,
            allowed_tools=parsed["allowed_tools"],
            argument_hint=parsed["argument_hint"],
            argument_names=parsed["argument_names"],
            when_to_use=parsed["when_to_use"],
            version=parsed["version"],
            model=parsed["model"],
            disable_model_invocation=parsed["disable_model_invocation"],
            user_invocable=parsed["user_invocable"],
            source=source,
            base_dir=str(Path(skill_file_path).parent),
            loaded_from=resolved_loaded_from,
            execution_context=parsed["execution_context"],
            agent=parsed["agent"],
            paths=parsed["paths"],
            effort=parsed["effort"],
            hooks=parsed.get("hooks"),
            shell=parsed.get("shell"),
        )
        skills.append(skill)

    return skills


def _get_project_skills_dirs(cwd: str) -> list[str]:
    dirs: list[str] = []
    current = Path(cwd).resolve()
    home = Path.home().resolve()

    while True:
        skills_dir = current / ".claude" / "skills"
        dirs.append(str(skills_dir))
        if current == home or current == current.parent:
            break
        current = current.parent

    return list(reversed(dirs))


_skill_dir_cache: dict[str, list[Skill]] = {}


# ----------------------------------------------------------------------
# Bare mode + policy plumbing
#
# Mirrors TS' `isBareMode()` / `CLAUDE_CODE_DISABLE_POLICY_SKILLS` /
# `isRestrictedToPluginOnly('skills')` gates so the Python loader honors
# the same env-driven safety flags. We reuse the existing
# `_is_bare_mode` / `_get_additional_directories` semantics already
# established by `src/context_system/claude_md.py` rather than inventing
# parallel ones.
# ----------------------------------------------------------------------


def _is_bare_mode() -> bool:
    """True when ``CLAUDE_CODE_BARE_MODE`` is set (matches CLAUDE.md path).

    Bare mode skips autodiscovery entirely; only ``--add-dir`` paths
    contribute disk skills. Bundled/MCP skills are unaffected (they go
    through `get_all_skills`'s separate merge path).
    """
    return os.environ.get("CLAUDE_CODE_BARE_MODE", "").lower() in ("1", "true", "yes")


def _is_skills_policy_disabled() -> bool:
    """True when ``CLAUDE_CODE_DISABLE_POLICY_SKILLS`` is set.

    Mirrors the TS check at `loadSkillsDir.ts:771`. When set, the
    managed/policy skills directory (`/etc/claude/.claude/skills`) is
    skipped — useful for opting out of admin-distributed skills on
    multi-tenant machines.
    """
    return os.environ.get("CLAUDE_CODE_DISABLE_POLICY_SKILLS", "").lower() in (
        "1", "true", "yes",
    )


def _is_restricted_to_plugin_only(scope: str) -> bool:
    """Stub for the TS `isRestrictedToPluginOnly(scope)` policy gate.

    The TS implementation is policy-driven (managed settings can lock
    skills/agents/etc. to plugin-supplied entries only). The Python port
    has no policy plumbing yet, so this returns False — equivalent to
    "policy unset; allow normal discovery". Plugin-policy support can
    flip this in a future task without touching call sites.
    """
    _ = scope  # documented hook for future plugin policy
    return False


def _get_additional_skill_dirs() -> list[str]:
    """Read ``--add-dir`` paths from ``CLAUDE_CODE_ADDITIONAL_DIRECTORIES``.

    Each entry maps to ``<dir>/.claude/skills`` for skill loading
    (matches TS `additionalSkillsNested` block).
    """
    val = os.environ.get("CLAUDE_CODE_ADDITIONAL_DIRECTORIES", "")
    if not val:
        return []
    return [d.strip() for d in val.split(os.pathsep) if d.strip()]


def _get_file_identity(path: str) -> str | None:
    """Return a stable identity for ``path`` (resolved via realpath).

    Mirrors TS `getFileIdentity` (`loadSkillsDir.ts:118`). Uses
    realpath so symlinked-and-overlapping mounts collapse to the same
    identity. Returns ``None`` on broken-symlink / OSError so the caller
    can fail open (i.e., keep the skill rather than drop it on a stat
    failure).
    """
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return None


def get_skill_dir_commands(cwd: str) -> list[Skill]:
    """Return the union of disk-loaded skills, deduped by realpath identity.

    Behavior matches TS `loadSkillsFromSkillsDir` (line 720+):
      - Bare mode: load only ``--add-dir`` paths; skip everything else.
      - Policy disabled: skip the managed/`/etc/claude` dir.
      - Plugin-only restriction: collapses user + project loads to empty
        (the gate currently always returns False; future hook).
      - Order: managed → user → project → additional. Realpath dedup is
        first-wins so the same SKILL.md file accessed via overlapping
        sources (symlinks, bind mounts) collapses to one entry.

    The cwd-keyed cache is preserved; cache invalidation goes through
    ``clear_skill_caches``.
    """
    if cwd in _skill_dir_cache:
        return list(_skill_dir_cache[cwd])

    additional_dirs = _get_additional_skill_dirs()
    plugin_only = _is_restricted_to_plugin_only("skills")

    # --- Bare mode short-circuit --------------------------------------
    if _is_bare_mode():
        if not additional_dirs or plugin_only:
            logger.debug(
                "[skills] bare mode active; skipping discovery "
                "(additional_dirs=%d plugin_only=%s)",
                len(additional_dirs),
                plugin_only,
            )
            _skill_dir_cache[cwd] = []
            return []
        bare_skills: list[Skill] = []
        for d in additional_dirs:
            skills_dir = str(Path(d) / ".claude" / "skills")
            bare_skills.extend(
                load_skills_from_skills_dir(skills_dir, "projectSettings")
            )
        unconditional = _split_conditional(bare_skills)
        _skill_dir_cache[cwd] = unconditional
        return list(unconditional)

    # --- Standard discovery -------------------------------------------
    managed_skills_dir = str(_get_managed_file_path() / ".claude" / "skills")
    user_skills_dir = str(_get_global_config_dir() / "skills")
    project_skills_dirs = _get_project_skills_dirs(cwd)

    managed_skills: list[Skill] = []
    if not _is_skills_policy_disabled():
        managed_skills = load_skills_from_skills_dir(
            managed_skills_dir, "policySettings"
        )

    user_skills: list[Skill] = []
    project_skills: list[Skill] = []
    if not plugin_only:
        user_skills = load_skills_from_skills_dir(user_skills_dir, "userSettings")
        for d in project_skills_dirs:
            project_skills.extend(
                load_skills_from_skills_dir(d, "projectSettings")
            )

    additional_skills: list[Skill] = []
    if not plugin_only:
        for d in additional_dirs:
            skills_dir = str(Path(d) / ".claude" / "skills")
            additional_skills.extend(
                load_skills_from_skills_dir(skills_dir, "projectSettings")
            )

    all_skills = managed_skills + user_skills + project_skills + additional_skills

    deduped = _dedup_by_realpath(all_skills)
    unconditional = _split_conditional(deduped)

    _skill_dir_cache[cwd] = unconditional
    return list(unconditional)


def _dedup_by_realpath(skills: list[Skill]) -> list[Skill]:
    """First-wins dedup keyed on each skill's resolved SKILL.md path.

    Matches TS `loadSkillsDir.ts:813-848`. Skills whose `base_dir` can't
    be resolved (broken symlink, OSError) fall through unchanged — fail
    open, mirroring TS' `null` identity branch.
    """
    seen: dict[str, Skill] = {}
    out: list[Skill] = []
    for skill in skills:
        skill_md = (
            str(Path(skill.base_dir) / "SKILL.md")
            if skill.base_dir
            else None
        )
        identity = _get_file_identity(skill_md) if skill_md else None
        if identity is None:
            out.append(skill)
            continue
        existing = seen.get(identity)
        if existing is not None:
            logger.debug(
                "[skills] dropping duplicate %r from %s "
                "(same SKILL.md already loaded from %s)",
                skill.name,
                skill.source,
                existing.source,
            )
            continue
        seen[identity] = skill
        out.append(skill)
    return out


def _split_conditional(skills: list[Skill]) -> list[Skill]:
    """Move conditional (paths-gated) skills to ``_conditional_skills``.

    Returns the list of unconditional skills. Conditional ones become
    visible only after `activate_conditional_skills_for_paths` matches
    a touched file against their `paths:` patterns.
    """
    unconditional: list[Skill] = []
    for skill in skills:
        if not skill.is_conditional:
            unconditional.append(skill)
        else:
            _conditional_skills[skill.name] = skill
    return unconditional


_conditional_skills: dict[str, Skill] = {}
_activated_conditional_names: set[str] = set()
_dynamic_skill_dirs: set[str] = set()
_dynamic_skills: dict[str, Skill] = {}


def get_dynamic_skills() -> list[Skill]:
    return list(_dynamic_skills.values())


def _is_path_gitignored(path: str, cwd: str) -> bool:
    """Return True iff git considers ``path`` ignored.

    Mirrors TS `isPathGitignored` (`utils/git/gitignore.ts`) — shells out
    to ``git check-ignore <path>`` and treats exit 0 as ignored, exit 1
    as not-ignored, anything else (e.g. exit 128 outside a git repo) as
    not-ignored. Fails open on subprocess errors so non-git workspaces
    keep working.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "check-ignore", path],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


def discover_skill_dirs_for_paths(
    file_paths: list[str],
    cwd: str,
) -> list[str]:
    """Walk parent dirs of each touched file looking for `.claude/skills`.

    Mirrors TS `discoverSkillDirsForPaths` (`loadSkillsDir.ts:951+`).
    Each newly-found skills dir whose containing folder is gitignored is
    skipped (e.g. `node_modules/pkg/.claude/skills` won't load silently);
    `git check-ignore` handles nested `.gitignore` and global rules with
    correct precedence. Fails open outside a git repo.
    """
    resolved_cwd = cwd.rstrip(os.sep)
    new_dirs: list[str] = []

    for file_path in file_paths:
        current_dir = str(Path(file_path).parent)

        while current_dir.startswith(resolved_cwd + os.sep):
            skill_dir = os.path.join(current_dir, ".claude", "skills")
            if skill_dir not in _dynamic_skill_dirs:
                _dynamic_skill_dirs.add(skill_dir)
                if Path(skill_dir).is_dir():
                    if _is_path_gitignored(current_dir, resolved_cwd):
                        logger.debug(
                            "[skills] Skipped gitignored skills dir: %s",
                            skill_dir,
                        )
                    else:
                        new_dirs.append(skill_dir)

            parent = str(Path(current_dir).parent)
            if parent == current_dir:
                break
            current_dir = parent

    return sorted(new_dirs, key=lambda d: d.count(os.sep), reverse=True)


def add_skill_directories(dirs: list[str]) -> None:
    if not dirs:
        return
    for d in dirs:
        loaded = load_skills_from_skills_dir(d, "projectSettings")
        for skill in loaded:
            _dynamic_skills[skill.name] = skill


def activate_conditional_skills_for_paths(
    file_paths: list[str],
    cwd: str,
) -> list[str]:
    """Promote conditional skills whose ``paths:`` patterns match.

    Path matching uses gitignore semantics via ``pathspec`` (TS uses the
    `ignore` library) — supports ``**`` recursion, anchoring, negation,
    etc. Each skill's full ``paths:`` list compiles into a single
    `PathSpec` that the touched files match against; skills where any
    pattern matches any file get moved out of `_conditional_skills`
    into `_dynamic_skills`.

    Path-validity guards from TS:
      - Skip empty rel-paths.
      - Skip ``..``-prefixed paths (file escaped the workspace).
      - Skip absolute paths (Windows cross-drive).
    """
    if not _conditional_skills:
        return []

    # Pre-resolve relative paths once per file_path so we don't repeat
    # the work for each conditional skill. Filter invalid entries here.
    rel_paths: list[str] = []
    for file_path in file_paths:
        rel_path = os.path.relpath(file_path, cwd)
        if not rel_path or rel_path.startswith("..") or os.path.isabs(rel_path):
            continue
        rel_paths.append(rel_path)

    if not rel_paths:
        return []

    activated: list[str] = []
    for name, skill in list(_conditional_skills.items()):
        if not skill.paths:
            continue
        spec = _compile_path_spec(skill.paths)
        if spec is None:
            continue
        for rel_path in rel_paths:
            if spec.match_file(rel_path):
                _dynamic_skills[name] = skill
                del _conditional_skills[name]
                _activated_conditional_names.add(name)
                activated.append(name)
                break

    return activated


def _compile_path_spec(patterns: list[str]):
    """Compile a list of gitwildmatch patterns into a ``PathSpec``.

    Returns ``None`` (and logs at debug) if pathspec isn't installed or
    the patterns can't compile. The unconditional-skill path treats this
    as a no-match — better than crashing the activation loop.
    """
    try:
        import pathspec
    except ImportError:  # pragma: no cover — pathspec is a hard dep
        logger.debug(
            "pathspec not installed; conditional `paths:` matching disabled"
        )
        return None
    # Prefer the modern ``gitignore`` pattern factory (pathspec >= 1.0)
    # which subsumes the legacy ``gitwildmatch`` and emits no deprecation
    # warning. Fall back to ``gitwildmatch`` for older pathspec versions.
    for factory in ("gitignore", "gitwildmatch"):
        try:
            return pathspec.PathSpec.from_lines(factory, patterns)
        except (LookupError, KeyError):
            continue
        except Exception as exc:
            logger.debug("failed to compile path spec %r: %s", patterns, exc)
            return None
    return None


def _path_matches_pattern(path: str, pattern: str) -> bool:
    """Legacy single-pattern matcher kept for backward compat.

    Uses pathspec under the hood now (so ``src/**/*.py`` works correctly
    against ``src/foo/bar.py``); the prior `fnmatch`-based impl missed
    those cases. New code should prefer building a `PathSpec` once and
    calling `.match_file(...)` directly.
    """
    spec = _compile_path_spec([pattern])
    if spec is None:
        return False
    return spec.match_file(path)


def clear_skill_caches() -> None:
    _skill_dir_cache.clear()
    _conditional_skills.clear()
    _activated_conditional_names.clear()


def clear_dynamic_skills() -> None:
    _dynamic_skill_dirs.clear()
    _dynamic_skills.clear()
    _conditional_skills.clear()
    _activated_conditional_names.clear()


def get_conditional_skill_count() -> int:
    return len(_conditional_skills)


# ----------------------------------------------------------------------
# Unified skill registry
#
# Historically this module had two parallel disk-loading paths: the
# TS-port branch (above) that produced rich `Skill` objects with nested
# namespace support, and a second branch that produced `PromptSkill`
# objects via `load_skills_from_dir` and populated `_skill_registry`.
# Only the second registry was wired into `SkillTool`, so everything
# loaded by the TS-port path was invisible to the model.
#
# `Skill` and `PromptSkill` are now the same class (see model.py).
# `get_all_skills` delegates to `get_skill_dir_commands` for the
# managed/user/project disk layout, then merges in bundled skills, MCP
# skills, and any legacy clawcodex-specific directories. The unified
# result is cached in `_skill_registry` so `get_registered_skill`
# (used by `SkillTool`) continues to work and now sees every skill.
# ----------------------------------------------------------------------

from .model import PromptSkill  # noqa: E402  (re-exported for back-compat)
from .bundled_skills import get_bundled_skills, get_bundled_skill_by_name  # noqa: E402

_skill_registry: dict[str, Skill] = {}


def clear_skill_registry() -> None:
    _skill_registry.clear()


def _legacy_user_skill_dirs(
    user_skills_dir: str | Path | None,
) -> list[Path]:
    """Resolve the legacy clawcodex user-skill locations.

    The TS-port loader walks `~/.claude/skills` (handled inside
    `get_skill_dir_commands`). This function returns the additional
    clawcodex-specific dirs plus any env overrides so existing setups
    that drop skills under `CLAWCODEX_SKILLS_DIR` or `~/.clawcodex/skills`
    continue to work.
    """
    dirs: list[Path] = []

    if user_skills_dir is not None:
        dirs.append(Path(user_skills_dir).expanduser().resolve())
        return dirs

    env_primary = os.environ.get("CLAWCODEX_SKILLS_DIR")
    env_ts = os.environ.get("CLAUDE_SKILLS_DIR")
    if env_primary:
        dirs.append(Path(env_primary).expanduser().resolve())
    if env_ts:
        p = Path(env_ts).expanduser().resolve()
        if p not in dirs:
            dirs.append(p)

    clawcodex_dir = (Path.home() / ".clawcodex" / "skills").expanduser().resolve()
    if clawcodex_dir not in dirs:
        dirs.append(clawcodex_dir)

    return dirs


def _legacy_project_skill_dirs(project_root: str | Path) -> list[Path]:
    """Resolve clawcodex-specific project skill dirs.

    `.claude/skills` is already handled by `get_skill_dir_commands`; here
    we add `.clawcodex/skills` as a sibling so the legacy layout still
    works.
    """
    pr = Path(project_root).expanduser().resolve()
    return [pr / ".clawcodex" / "skills"]


def _load_dirs_as(
    dirs: Sequence[Path | str],
    source: str,
    loaded_from: str,
) -> list[Skill]:
    skills: list[Skill] = []
    for d in dirs:
        skills.extend(
            load_skills_from_skills_dir(str(d), source, loaded_from=loaded_from)
        )
    return skills


def get_all_skills(
    *,
    project_root: str | Path | None = None,
    user_skills_dir: str | Path | None = None,
) -> Sequence[Skill]:
    """Return the unified set of skills available to the model.

    Sources, in priority order (first occurrence of a name wins):

    1. Managed/policy skills (``/etc/claude/.claude/skills`` by default)
    2. User skills (``~/.claude/skills`` plus any clawcodex/env-specified
       user-skill dirs)
    3. Project skills (walking up from ``project_root`` to ``$HOME`` for
       ``.claude/skills`` plus ``<project_root>/.clawcodex/skills``)
    4. Managed override via ``CLAWCODEX_MANAGED_SKILLS_DIR``
    5. Bundled skills registered via ``register_bundled_skill``
    6. MCP-loaded skills returned by registered MCP skill builders

    Disk skills support nested namespacing (``category/skill/SKILL.md``
    becomes ``category:skill``) via ``get_skill_dir_commands``.

    The returned set is also stored in ``_skill_registry`` so legacy
    callers of ``get_registered_skill`` see every source.
    """
    clear_skill_registry()

    cwd = (
        str(Path(project_root).expanduser().resolve())
        if project_root is not None
        else os.getcwd()
    )

    # 1-3: Managed + user + project disk skills via the unified TS-port loader
    disk_skills: list[Skill] = list(get_skill_dir_commands(cwd))

    # 2b: Additional user-skill dirs (clawcodex-specific + env overrides)
    extra_user_skills = _load_dirs_as(
        _legacy_user_skill_dirs(user_skills_dir),
        source="userSettings",
        loaded_from="user",
    )

    # 3b: Additional project-skill dir for the clawcodex layout
    extra_project_skills: list[Skill] = []
    if project_root is not None:
        extra_project_skills = _load_dirs_as(
            _legacy_project_skill_dirs(project_root),
            source="projectSettings",
            loaded_from="project",
        )

    # 4: Managed override env (separate from /etc/claude policy dir)
    extra_managed_skills: list[Skill] = []
    managed_env = os.environ.get("CLAWCODEX_MANAGED_SKILLS_DIR")
    if managed_env:
        extra_managed_skills = _load_dirs_as(
            [managed_env],
            source="policySettings",
            loaded_from="managed",
        )

    # 5: Bundled skills (always present)
    bundled = get_bundled_skills()

    # 6: MCP skills (if a builder is registered)
    mcp_skills: list[Skill] = []
    builders = None
    try:
        from .mcp_skill_builders import get_mcp_skill_builders
        builders = get_mcp_skill_builders()
    except Exception:
        builders = None
    if builders:
        for builder in builders.values():
            try:
                produced = builder()
            except Exception:
                continue
            if produced:
                mcp_skills.extend(produced)

    # Activated conditional skills + skills introduced by
    # ``add_skill_directories`` live in ``_dynamic_skills``. They must be
    # merged last so explicit user/project skills with the same name win
    # — a conditional skill is by definition the "lowest-priority"
    # entry. Without this, ``activate_conditional_skills_for_paths``
    # would silently move a skill into ``_dynamic_skills`` while the
    # canonical ``SkillTool`` lookup (which goes through
    # ``get_registered_skill`` → ``_skill_registry``) still returns
    # "Unknown skill". (QA bug #14.)
    dynamic_skills = get_dynamic_skills()

    # Merge with first-source-wins precedence. The order below mirrors
    # the priority list above: managed env override first (so admins can
    # force a version), then policy/user/project from the unified loader,
    # then clawcodex extras, then bundled and MCP as fallbacks, with
    # dynamic (activated conditional / runtime-added) entries last.
    merge_order: list[Skill] = (
        list(extra_managed_skills)
        + list(disk_skills)
        + list(extra_user_skills)
        + list(extra_project_skills)
        + list(bundled)
        + list(mcp_skills)
        + list(dynamic_skills)
    )

    deduped: dict[str, Skill] = {}
    for skill in merge_order:
        if skill.name not in deduped:
            deduped[skill.name] = skill

    _skill_registry.update(deduped)
    return list(deduped.values())


def get_registered_skill(name: str) -> Skill | None:
    """Look up a skill in the unified registry.

    Falls back to ``get_bundled_skill_by_name`` so callers that rely on
    bundled-skill aliases keep working even before ``get_all_skills`` is
    populated for the current cwd.
    """
    found = _skill_registry.get(name)
    if found is not None:
        return found
    return get_bundled_skill_by_name(name)


# Back-compat shim for the old PromptSkill-producing loader. Some callers
# (and downstream tests) imported `load_skills_from_dir` directly. Keep
# the surface the same but route through the unified
# `load_skills_from_skills_dir` so behaviour matches the registry.
def load_skills_from_dir(
    base_dir: str | Path, *, loaded_from: str = "skills"
) -> list[Skill]:
    return load_skills_from_skills_dir(
        str(Path(base_dir).expanduser().resolve()),
        source="userSettings",
        loaded_from=loaded_from,
    )
