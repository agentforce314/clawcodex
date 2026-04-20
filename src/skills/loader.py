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


def parse_skill_frontmatter_fields(
    frontmatter: dict[str, Any],
    markdown_content: str,
    resolved_name: str,
) -> dict[str, Any]:
    raw_desc = frontmatter.get("description", "")
    if isinstance(raw_desc, list):
        description = " ".join(str(x) for x in raw_desc)
    elif raw_desc:
        description = str(raw_desc)
    else:
        description = f"Skill: {resolved_name}"

    user_invocable = frontmatter.get("user-invocable", True)
    if isinstance(user_invocable, str):
        user_invocable = user_invocable.lower() in ("true", "yes", "1")
    elif not isinstance(user_invocable, bool):
        user_invocable = True

    disable_model = frontmatter.get("disable-model-invocation", False)
    if isinstance(disable_model, str):
        disable_model = disable_model.lower() in ("true", "yes", "1")
    elif not isinstance(disable_model, bool):
        disable_model = False

    allowed_tools_raw = frontmatter.get("allowed-tools", [])
    if isinstance(allowed_tools_raw, str):
        allowed_tools = [t.strip() for t in allowed_tools_raw.split(",") if t.strip()]
    elif isinstance(allowed_tools_raw, list):
        allowed_tools = [str(t) for t in allowed_tools_raw]
    else:
        allowed_tools = []

    argument_names = parse_argument_names(frontmatter.get("arguments"))

    when_to_use = frontmatter.get("when_to_use")
    if when_to_use is not None:
        when_to_use = str(when_to_use)

    version = frontmatter.get("version")
    if version is not None:
        version = str(version)

    model_raw = frontmatter.get("model")
    model = None
    if model_raw and model_raw != "inherit":
        model = str(model_raw)

    context = frontmatter.get("context", "inline")
    execution_context = "fork" if context == "fork" else None

    agent = frontmatter.get("agent")
    if agent is not None:
        agent = str(agent)

    effort = frontmatter.get("effort")
    if effort is not None:
        effort = str(effort)

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
        "has_user_specified_description": bool(raw_desc),
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


def load_skills_from_skills_dir(
    base_path: str,
    source: str,
) -> list[Skill]:
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
            loaded_from="skills",
            execution_context=parsed["execution_context"],
            agent=parsed["agent"],
            paths=parsed["paths"],
            effort=parsed["effort"],
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


def get_skill_dir_commands(cwd: str) -> list[Skill]:
    if cwd in _skill_dir_cache:
        return list(_skill_dir_cache[cwd])

    user_skills_dir = str(_get_global_config_dir() / "skills")
    managed_skills_dir = str(_get_managed_file_path() / ".claude" / "skills")
    project_skills_dirs = _get_project_skills_dirs(cwd)

    managed_skills = load_skills_from_skills_dir(managed_skills_dir, "policySettings")
    user_skills = load_skills_from_skills_dir(user_skills_dir, "userSettings")

    project_skills: list[Skill] = []
    for d in project_skills_dirs:
        project_skills.extend(load_skills_from_skills_dir(d, "projectSettings"))

    all_skills = managed_skills + user_skills + project_skills

    seen_names: set[str] = set()
    deduped: list[Skill] = []
    for skill in all_skills:
        if skill.name not in seen_names:
            seen_names.add(skill.name)
            deduped.append(skill)

    unconditional: list[Skill] = []
    for skill in deduped:
        if not skill.is_conditional:
            unconditional.append(skill)
        else:
            _conditional_skills[skill.name] = skill

    _skill_dir_cache[cwd] = unconditional
    return list(unconditional)


_conditional_skills: dict[str, Skill] = {}
_activated_conditional_names: set[str] = set()
_dynamic_skill_dirs: set[str] = set()
_dynamic_skills: dict[str, Skill] = {}


def get_dynamic_skills() -> list[Skill]:
    return list(_dynamic_skills.values())


def discover_skill_dirs_for_paths(
    file_paths: list[str],
    cwd: str,
) -> list[str]:
    resolved_cwd = cwd.rstrip(os.sep)
    new_dirs: list[str] = []

    for file_path in file_paths:
        current_dir = str(Path(file_path).parent)

        while current_dir.startswith(resolved_cwd + os.sep):
            skill_dir = os.path.join(current_dir, ".claude", "skills")
            if skill_dir not in _dynamic_skill_dirs:
                _dynamic_skill_dirs.add(skill_dir)
                if Path(skill_dir).is_dir():
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
    if not _conditional_skills:
        return []

    activated: list[str] = []
    for name, skill in list(_conditional_skills.items()):
        if not skill.paths:
            continue
        for file_path in file_paths:
            rel_path = os.path.relpath(file_path, cwd)
            if rel_path.startswith("..") or os.path.isabs(rel_path):
                continue
            for pattern in skill.paths:
                if _path_matches_pattern(rel_path, pattern):
                    _dynamic_skills[name] = skill
                    del _conditional_skills[name]
                    _activated_conditional_names.add(name)
                    activated.append(name)
                    break
            if name in activated:
                break

    return activated


def _path_matches_pattern(path: str, pattern: str) -> bool:
    import fnmatch
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern + "/**")


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


from .model import PromptSkill  # noqa: E402

_skill_registry: dict[str, PromptSkill] = {}


def clear_skill_registry() -> None:
    _skill_registry.clear()


def _candidate_user_skills_dirs() -> list[Path]:
    env_primary = os.environ.get("CLAWCODEX_SKILLS_DIR")
    env_ts = os.environ.get("CLAUDE_SKILLS_DIR")
    dirs: list[Path] = []
    if env_primary:
        dirs.append(Path(env_primary).expanduser().resolve())
    if env_ts:
        p = Path(env_ts).expanduser().resolve()
        if p not in dirs:
            dirs.append(p)
    for d in (Path.home() / ".clawcodex" / "skills", Path.home() / ".claude" / "skills"):
        p = d.expanduser().resolve()
        if p not in dirs:
            dirs.append(p)
    return dirs


def _as_str_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if str(x)]
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return []
        if "," in s:
            return [x.strip() for x in s.split(",") if x.strip()]
        return [s]
    return [str(val)]


def _extract_description(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        return stripped[:200]
    return None


def load_skills_from_dir(
    base_dir: str | Path, *, loaded_from: str = "skills"
) -> list[PromptSkill]:
    base = Path(base_dir).expanduser().resolve()
    if not base.exists() or not base.is_dir():
        return []

    skills: list[PromptSkill] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        skill_name = entry.name
        md_path = entry / "SKILL.md"
        if not md_path.exists():
            continue
        content = md_path.read_text(encoding="utf-8")
        parsed_fm = parse_frontmatter(content)
        fm = parsed_fm.frontmatter
        body = parsed_fm.body

        description = str(
            fm.get("description") or _extract_description(body) or f"Skill: {skill_name}"
        )
        user_invocable = bool(fm.get("user-invocable", True))
        disable_model_invocation = bool(fm.get("disable-model-invocation", False))
        when_to_use = fm.get("when_to_use")
        when_to_use = str(when_to_use) if when_to_use is not None else None
        version = fm.get("version")
        version = str(version) if version is not None else None
        model = fm.get("model")
        model = str(model) if model is not None else None

        allowed_tools = _as_str_list(fm.get("allowed-tools"))
        arg_names = parse_argument_names(fm.get("arguments"))
        context = "fork" if str(fm.get("context", "")).lower() == "fork" else "inline"
        agent_val = fm.get("agent")
        agent_val = str(agent_val) if agent_val is not None else None
        effort_val = fm.get("effort")
        effort_val = str(effort_val) if effort_val is not None else None
        paths_val = _as_str_list(fm.get("paths"))
        if paths_val == []:
            paths_val = None

        skill = PromptSkill(
            name=skill_name,
            description=description,
            loaded_from=loaded_from,
            user_invocable=user_invocable,
            disable_model_invocation=disable_model_invocation,
            content_length=len(body),
            is_hidden=not user_invocable,
            skill_root=str(entry),
            when_to_use=when_to_use,
            version=version,
            model=model,
            allowed_tools=allowed_tools,
            arg_names=arg_names,
            context=context,
            agent=agent_val,
            effort=effort_val,
            paths=paths_val,
            markdown_content=body,
        )
        skills.append(skill)
    return skills


def get_all_skills(
    *,
    project_root: str | Path | None = None,
    user_skills_dir: str | Path | None = None,
) -> Sequence[PromptSkill]:
    clear_skill_registry()
    if user_skills_dir is not None:
        user_dirs = [Path(user_skills_dir).expanduser().resolve()]
    else:
        user_dirs = _candidate_user_skills_dirs()
    for user_dir in user_dirs:
        for s in load_skills_from_dir(user_dir, loaded_from="user"):
            _skill_registry[s.name] = s

    managed_env = os.environ.get("CLAWCODEX_MANAGED_SKILLS_DIR")
    if managed_env:
        managed_dir = Path(managed_env).expanduser().resolve()
        for s in load_skills_from_dir(managed_dir, loaded_from="managed"):
            _skill_registry[s.name] = s

    if project_root is not None:
        pr = Path(project_root).expanduser().resolve()
        proj_dirs = []
        main_path = pr / ".clawcodex" / "skills"
        compat_path = pr / ".claude" / "skills"
        proj_dirs.append(main_path)
        if compat_path != main_path:
            proj_dirs.append(compat_path)
        for pr_dir in proj_dirs:
            for s in load_skills_from_dir(pr_dir, loaded_from="project"):
                _skill_registry[s.name] = s

    return list(_skill_registry.values())


def get_registered_skill(name: str) -> PromptSkill | None:
    return _skill_registry.get(name)
