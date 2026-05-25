from __future__ import annotations

"""
SkillRegistry Extension

Wraps upstream skills loader with clawcodex-specific functionality.
Uses composition to avoid modifying upstream code.

Mirrors ToolRegistryExt pattern for consistency.
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    from ..skills.loader import get_all_skills as upstream_get_all_skills
    from ..skills.model import Skill
    from .agent_config import AgentSkillConfig
    from .bundles import SKILL_BUNDLES

from .hooks import SkillRegistrationCallback

logger = logging.getLogger(__name__)


class SkillRegistryExt:
    """
    Extended registry that wraps upstream loader with bundle support.

    Does not modify upstream loader. Uses composition to provide
    selective skill loading per agent configuration.
    """

    def __init__(
        self,
        loader_module=None,
        project_root: str | Path | None = None,
    ) -> None:
        """
        Initialize SkillRegistryExt.

        Args:
            loader_module: Module containing upstream get_all_skills function.
                         Defaults to src.skills.loader.
            project_root: Project root path for skill resolution.
        """
        if loader_module is None:
            import importlib
            loader_module = importlib.import_module("src.skills.loader")
        self._loader = loader_module
        self._callbacks: list[SkillRegistrationCallback] = []
        self._project_root = project_root
        self._cached_skills: list[Skill] | None = None

    @property
    def upstream_loader(self):
        """Access the upstream loader module."""
        return self._loader

    def get_all_skills(
        self,
        *,
        project_root: str | Path | None = None,
        user_skills_dir: str | Path | None = None,
        force_refresh: bool = False,
    ) -> list[Skill]:
        """
        Get all skills (upstream + clawcodex extensions).

        Args:
            project_root: Project root path
            user_skills_dir: Custom user skills directory
            force_refresh: Skip cache and reload

        Returns:
            List of all available Skills
        """
        cache_key = f"{project_root}:{user_skills_dir}"
        if not force_refresh and self._cached_skills is not None:
            return list(self._cached_skills)

        # Get upstream skills
        try:
            base_skills = self._loader.get_all_skills(
                project_root=project_root,
                user_skills_dir=user_skills_dir,
            )
        except Exception as e:
            logger.warning(
                "[skills_ext] upstream get_all_skills failed: %s",
                e,
            )
            base_skills = []

        # Get clawcodex-specific skills
        clawcodex_skills = self._load_clawcodex_paths(
            project_root or self._project_root,
            user_skills_dir,
        )

        # Merge with first-source-wins precedence
        merged = self._merge_skills(base_skills, clawcodex_skills)

        # Notify callbacks
        for skill in merged:
            self._notify_skill_registered(skill)

        self._cached_skills = merged
        return list(merged)

    def _load_clawcodex_paths(
        self,
        project_root: str | Path | None,
        user_skills_dir: str | Path | None,
    ) -> list[Skill]:
        """Load skills from clawcodex-specific paths."""
        from ..skills.loader import load_skills_from_skills_dir

        skills: list[Skill] = []

        # Load from CLAWCODEX_SKILLS_DIR and ~/.clawcodex/skills
        clawcodex_dirs = self._get_clawcodex_dirs(user_skills_dir)
        for d in clawcodex_dirs:
            if Path(d).is_dir():
                try:
                    loaded = load_skills_from_skills_dir(d, "userSettings")
                    skills.extend(loaded)
                except Exception as e:
                    logger.debug(
                        "[skills_ext] failed to load from %s: %s",
                        d,
                        e,
                    )

        # Load from project .clawcodex/skills
        if project_root is not None:
            project_clawcodex = Path(project_root) / ".clawcodex" / "skills"
            if project_clawcodex.is_dir():
                try:
                    loaded = load_skills_from_skills_dir(
                        str(project_clawcodex),
                        "projectSettings",
                    )
                    skills.extend(loaded)
                except Exception as e:
                    logger.debug(
                        "[skills_ext] failed to load project skills: %s",
                        e,
                    )

        return skills

    def _get_clawcodex_dirs(
        self,
        user_skills_dir: str | Path | None,
    ) -> list[str]:
        """Get clawcodex-specific skill directories."""
        dirs: list[str] = []

        if user_skills_dir is not None:
            dirs.append(str(Path(user_skills_dir).expanduser().resolve()))
            return dirs

        env_primary = os.environ.get("CLAWCODEX_SKILLS_DIR")
        if env_primary:
            dirs.append(env_primary)

        ts_env = os.environ.get("CLAUDE_SKILLS_DIR")
        if ts_env:
            p = str(Path(ts_env).expanduser().resolve())
            if p not in dirs:
                dirs.append(p)

        clawcodex_dir = str(Path.home() / ".clawcodex" / "skills")
        if clawcodex_dir not in dirs:
            dirs.append(clawcodex_dir)

        return dirs

    def _merge_skills(
        self,
        base_skills: Sequence[Skill],
        extra_skills: Sequence[Skill],
    ) -> list[Skill]:
        """
        Merge base and extra skills with first-source-wins.

        Args:
            base_skills: Upstream skills
            extra_skills: clawcodex-specific skills

        Returns:
            Merged skill list
        """
        seen: dict[str, Skill] = {}

        for skill in base_skills:
            if skill.name not in seen:
                seen[skill.name] = skill

        for skill in extra_skills:
            if skill.name not in seen:
                seen[skill.name] = skill

        return list(seen.values())

    def get_skill(self, name: str) -> Skill | None:
        """
        Get a skill by name.

        Args:
            name: Skill name

        Returns:
            Skill or None if not found
        """
        skills = self.get_all_skills()
        for skill in skills:
            if skill.name == name:
                return skill
        return None

    def list_skills(self) -> list[Skill]:
        """List all available skills."""
        return self.get_all_skills()

    def on_skill_registered(self, callback: SkillRegistrationCallback) -> None:
        """
        Register a callback to be notified when skills are registered.

        Args:
            callback: Callable that takes a Skill as argument
        """
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def off_skill_registered(self, callback: SkillRegistrationCallback) -> None:
        """
        Remove a previously registered callback.

        Args:
            callback: Previously registered callback to remove
        """
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_skill_registered(self, skill: Skill) -> None:
        """Notify all callbacks of a skill registration."""
        for cb in self._callbacks:
            try:
                cb(skill)
            except Exception:
                pass

    def get_skills_for_config(
        self,
        config: AgentSkillConfig,
    ) -> list[Skill]:
        """
        Get filtered skill list based on AgentSkillConfig.

        Args:
            config: Agent skill configuration

        Returns:
            Filtered Skills list matching the configuration
        """
        from .bundles import SKILL_BUNDLES, MODE_BUNDLES

        if config.mode == "bare":
            return []

        all_skills = self.get_all_skills()
        skill_names_in_bundle: set[str] = set()

        if config.mode == "all":
            for bundle_skills in SKILL_BUNDLES.values():
                skill_names_in_bundle.update(bundle_skills)
        elif config.bundles is not None:
            for bundle in config.bundles:
                if bundle in SKILL_BUNDLES:
                    skill_names_in_bundle.update(SKILL_BUNDLES[bundle])
        else:
            default_bundles = MODE_BUNDLES.get(config.mode, ["default"])
            for bundle in default_bundles:
                if bundle in SKILL_BUNDLES:
                    skill_names_in_bundle.update(SKILL_BUNDLES[bundle])

        result: list[Skill] = []
        for skill in all_skills:
            if skill.name in config.exclude:
                continue
            if config.mode == "all" or skill.name in skill_names_in_bundle:
                result.append(skill)

        return result

    def load_bundle(self, bundle_name: str) -> list[str]:
        """
        Verify bundle skills are available.

        Args:
            bundle_name: Name of the bundle to load

        Returns:
            List of skill names that were found
        """
        from .bundles import SKILL_BUNDLES

        if bundle_name not in SKILL_BUNDLES:
            raise KeyError(f"unknown bundle: {bundle_name}")

        all_skills = self.get_all_skills()
        skill_names = {s.name for s in all_skills}

        loaded: list[str] = []
        for skill_name in SKILL_BUNDLES[bundle_name]:
            if skill_name in skill_names:
                loaded.append(skill_name)
        return loaded

    def get_available_bundle_names(self) -> list[str]:
        """Return all known bundle names."""
        from .bundles import ALL_BUNDLE_NAMES
        return ALL_BUNDLE_NAMES

    def clear_cache(self) -> None:
        """Clear the cached skills list."""
        self._cached_skills = None


# Module-level convenience functions
_default_registry: SkillRegistryExt | None = None


def get_default_registry() -> SkillRegistryExt:
    """Get the default SkillRegistryExt instance."""
    global _default_registry
    if _default_registry is None:
        _default_registry = SkillRegistryExt()
    return _default_registry


def clear_default_registry_cache() -> None:
    """Clear the default registry's cache."""
    global _default_registry
    if _default_registry is not None:
        _default_registry.clear_cache()
    _default_registry = None