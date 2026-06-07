"""Facade — tui/screens/permission_mode_picker.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.tui.screens.permission_mode_picker import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.tui.screens.permission_mode_picker`` directly.
"""

from clawcodex_ext.tui.screens.permission_mode_picker import (  # noqa: F401
    PermissionModePickerScreen,
)

__all__ = [
    "PermissionModePickerScreen",
]
