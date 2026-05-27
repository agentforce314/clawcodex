"""``/buddy`` command behavior."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.command_system.buddy_command import (
    BUDDY_COMMAND,
    PET_REACTIONS,
    buddy_command_call,
)
from src.command_system.types import CommandContext, LocalCommandResult


def _ctx(workspace_root: Any = None) -> CommandContext:
    from pathlib import Path
    return CommandContext(
        workspace_root=workspace_root or Path('/tmp'),
        cwd=Path('/tmp'),
        conversation=None,
        cost_tracker=None,
        history=None,
    )


def test_help_arg_returns_help_text(isolated_config: dict[str, Any]) -> None:
    r = buddy_command_call('help', _ctx())
    assert r.type == 'text'
    assert 'Usage: /buddy' in r.value


def test_question_mark_routes_to_info_not_help(
    isolated_config: dict[str, Any],
) -> None:
    """TS contract: `?` is in COMMON_INFO_ARGS, not COMMON_HELP_ARGS,
    so `/buddy ?` should report status (or "no buddy hatched"), NOT
    show the help text."""
    r = buddy_command_call('?', _ctx())
    # No companion yet → status path returns the not-hatched message
    assert 'No buddy hatched yet' in r.value
    assert 'Usage' not in r.value


def test_status_no_companion(isolated_config: dict[str, Any]) -> None:
    r = buddy_command_call('status', _ctx())
    assert 'No buddy hatched yet' in r.value


def test_status_with_companion(isolated_config: dict[str, Any]) -> None:
    isolated_config['user_id'] = 'fixed-id'
    isolated_config['companion'] = {
        'name': 'Bytespark',
        'personality': 'Curious.',
        'hatched_at': 0,
    }
    r = buddy_command_call('status', _ctx())
    assert 'Bytespark' in r.value
    assert 'Curious.' in r.value


def test_mute_sets_companion_muted_true(
    isolated_config: dict[str, Any],
) -> None:
    r = buddy_command_call('mute', _ctx())
    assert 'muted' in r.value.lower()
    assert isolated_config.get('companion_muted') is True


def test_unmute_sets_companion_muted_false(
    isolated_config: dict[str, Any],
) -> None:
    isolated_config['companion_muted'] = True
    r = buddy_command_call('unmute', _ctx())
    assert 'unmuted' in r.value.lower()
    assert isolated_config.get('companion_muted') is False


def test_unknown_arg_returns_help(isolated_config: dict[str, Any]) -> None:
    r = buddy_command_call('explode', _ctx())
    assert 'Usage: /buddy' in r.value


def test_no_args_hatches_when_absent(
    isolated_config: dict[str, Any],
) -> None:
    """Empty arg + no companion → hatch."""
    isolated_config['user_id'] = 'alice-id'
    assert 'companion' not in isolated_config
    r = buddy_command_call('', _ctx())
    # Config should have a companion now
    stored = isolated_config.get('companion')
    assert stored is not None
    assert 'name' in stored
    assert 'personality' in stored
    assert 'hatched_at' in stored
    assert stored['name'] in r.value


def test_no_args_pets_when_present(
    isolated_config: dict[str, Any],
) -> None:
    """Empty arg + companion exists → pet (writes companion_pet_at)."""
    isolated_config['user_id'] = 'fixed-id'
    isolated_config['companion'] = {
        'name': 'Bytespark', 'personality': 'p.', 'hatched_at': 1,
    }
    r = buddy_command_call('', _ctx())
    assert any(reaction in r.value for reaction in PET_REACTIONS)
    # Top-level config['companion_pet_at'] should be set (plan §1.3)
    assert 'companion_pet_at' in isolated_config
    assert isinstance(isolated_config['companion_pet_at'], int)


def test_pet_reaction_deterministic_same_timestamp(
    isolated_config: dict[str, Any],
) -> None:
    """Two pets at the same time produce the same reaction (deterministic)."""
    isolated_config['user_id'] = 'fixed-id'
    isolated_config['companion'] = {
        'name': 'Bytespark', 'personality': 'p.', 'hatched_at': 1,
    }
    fixed_ts = 1_700_000_000.0
    with patch('src.command_system.buddy_command.time.time', return_value=fixed_ts):
        r1 = buddy_command_call('', _ctx())
        r2 = buddy_command_call('', _ctx())
    assert r1.value == r2.value


def test_buddy_command_registered_when_enabled() -> None:
    """``get_builtin_commands`` returns BUDDY_COMMAND when buddy enabled."""
    from src.command_system.builtins import get_builtin_commands
    cmds = get_builtin_commands()
    assert any(c.name == 'buddy' for c in cmds)


def test_buddy_command_omitted_when_disabled() -> None:
    """When ``is_buddy_command_enabled`` returns False, no BUDDY_COMMAND."""
    from src.command_system.builtins import get_builtin_commands
    with patch(
        'src.command_system.buddy_command.is_buddy_enabled',
        return_value=False,
    ):
        cmds = get_builtin_commands()
        assert not any(c.name == 'buddy' for c in cmds)


def test_buddy_command_shape() -> None:
    """The BUDDY_COMMAND object is a LocalCommand with the right name."""
    assert BUDDY_COMMAND.name == 'buddy'
    assert BUDDY_COMMAND.immediate is True
