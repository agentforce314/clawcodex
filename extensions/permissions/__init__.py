"""二开 permissions extensions."""

from .perms_reader import settings_perms, settings_perms_structured_is_explicit

__all__ = [
    "settings_perms",
    "settings_perms_structured_is_explicit",
]
