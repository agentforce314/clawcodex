from __future__ import annotations

"""
Skill Lifecycle Hooks

Provides callback hooks for skill registration and lifecycle events.
Mirrors the on_tool_registered pattern from ToolRegistryExt.

Hooks:
    - on_skill_registered: Called when a skill is registered
    - on_skill_activated: Called when a conditional skill is activated
    - on_skill_deactivated: Called when a skill is deactivated
"""

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..skills.model import Skill

# Callback type for skill registration events
SkillRegistrationCallback = Callable[["Skill"], None]
SkillActivationCallback = Callable[["Skill", list[str]], None]


class SkillHooks:
    """
    Manages skill lifecycle callbacks.

    Provides a central registry for callbacks that are notified when
    skills are registered, activated, or deactivated.
    """

    def __init__(self) -> None:
        self._callbacks: list[SkillRegistrationCallback] = []
        self._activation_callbacks: list[SkillActivationCallback] = []

    def on_skill_registered(self, callback: SkillRegistrationCallback) -> None:
        """
        Register a callback to be notified when a skill is registered.

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

    def _notify_skill_registered(self, skill: "Skill") -> None:
        """Notify all callbacks of a new skill registration."""
        for cb in self._callbacks:
            try:
                cb(skill)
            except Exception:
                pass  # Don't let callback errors break registration

    def on_skill_activated(
        self, callback: SkillActivationCallback
    ) -> None:
        """
        Register a callback to be notified when a conditional skill is activated.

        Args:
            callback: Callable that takes (Skill, list[str]) where list is matched paths
        """
        if callback not in self._activation_callbacks:
            self._activation_callbacks.append(callback)

    def off_skill_activated(self, callback: SkillActivationCallback) -> None:
        """
        Remove a previously registered activation callback.

        Args:
            callback: Previously registered callback to remove
        """
        if callback in self._activation_callbacks:
            self._activation_callbacks.remove(callback)

    def _notify_skill_activated(self, skill: "Skill", matched_paths: list[str]) -> None:
        """Notify all callbacks of a skill activation."""
        for cb in self._activation_callbacks:
            try:
                cb(skill, matched_paths)
            except Exception:
                pass  # Don't let callback errors break activation


# Global hooks instance
_global_hooks = SkillHooks()


def get_global_hooks() -> SkillHooks:
    """Get the global SkillHooks instance."""
    return _global_hooks


def on_skill_registered(callback: SkillRegistrationCallback) -> None:
    """Convenience function to register a global skill callback."""
    _global_hooks.on_skill_registered(callback)


def off_skill_registered(callback: SkillRegistrationCallback) -> None:
    """Convenience function to unregister a global skill callback."""
    _global_hooks.off_skill_registered(callback)


def on_skill_activated(callback: SkillActivationCallback) -> None:
    """Convenience function to register a global activation callback."""
    _global_hooks.on_skill_activated(callback)


def off_skill_activated(callback: SkillActivationCallback) -> None:
    """Convenience function to unregister a global activation callback."""
    _global_hooks.off_skill_activated(callback)