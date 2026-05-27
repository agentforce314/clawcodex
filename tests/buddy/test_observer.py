"""Per-turn observer behavior."""
from __future__ import annotations

from typing import Any

from src.buddy.observer import fire_companion_observer
from src.types.messages import create_user_message


def _hatch(config: dict[str, Any], name: str = 'Bytespark') -> None:
    """Helper to set up a hatched companion in the isolated config."""
    config['user_id'] = 'fixed-id'
    config['companion'] = {
        'name': name,
        'personality': 'Curious test personality.',
        'hatched_at': 0,
    }


def test_observer_silent_when_no_companion(
    isolated_config: dict[str, Any],
) -> None:
    called: list[str | None] = []
    fire_companion_observer([create_user_message('hello')], called.append)
    assert called == []


def test_observer_silent_when_muted(
    isolated_config: dict[str, Any],
) -> None:
    _hatch(isolated_config)
    isolated_config['companion_muted'] = True
    called: list[str | None] = []
    fire_companion_observer(
        [create_user_message('Bytespark hi!')], called.append,
    )
    assert called == []


def test_observer_silent_on_no_user_message(
    isolated_config: dict[str, Any],
) -> None:
    _hatch(isolated_config)
    called: list[str | None] = []
    fire_companion_observer([], called.append)
    assert called == []


def test_observer_pet_reply_on_slash_buddy(
    isolated_config: dict[str, Any],
) -> None:
    _hatch(isolated_config)
    called: list[str | None] = []
    fire_companion_observer(
        [create_user_message('/buddy please')], called.append,
    )
    assert len(called) == 1
    # Pet reply is one of the _PET_REPLIES strings (lowercased phrases)
    from src.buddy.observer import _PET_REPLIES
    assert called[0] in _PET_REPLIES


def test_observer_direct_reply_on_name_mention(
    isolated_config: dict[str, Any],
) -> None:
    _hatch(isolated_config, name='Bytespark')
    called: list[str | None] = []
    fire_companion_observer(
        [create_user_message('hey Bytespark, look at this')], called.append,
    )
    assert len(called) == 1
    msg = called[0]
    assert msg is not None
    # Format: "{name}: {reply}"
    assert msg.startswith('Bytespark: ')
    from src.buddy.observer import _DIRECT_REPLIES
    assert any(msg.endswith(r) for r in _DIRECT_REPLIES)


def test_observer_direct_reply_on_buddy_word(
    isolated_config: dict[str, Any],
) -> None:
    _hatch(isolated_config)
    called: list[str | None] = []
    fire_companion_observer(
        [create_user_message('hey buddy what now')], called.append,
    )
    assert len(called) == 1
    assert called[0] is not None
    assert called[0].startswith('Bytespark: ')


def test_observer_direct_reply_on_companion_word(
    isolated_config: dict[str, Any],
) -> None:
    _hatch(isolated_config)
    called: list[str | None] = []
    fire_companion_observer(
        [create_user_message('what does my companion think?')], called.append,
    )
    assert len(called) == 1


def test_observer_silent_on_unrelated_text(
    isolated_config: dict[str, Any],
) -> None:
    _hatch(isolated_config)
    called: list[str | None] = []
    fire_companion_observer(
        [create_user_message('just normal code review please')], called.append,
    )
    assert called == []


def test_observer_deterministic_per_text(
    isolated_config: dict[str, Any],
) -> None:
    """Same input → same reaction."""
    _hatch(isolated_config)
    a: list[str | None] = []
    b: list[str | None] = []
    fire_companion_observer(
        [create_user_message('Bytespark hi!')], a.append,
    )
    fire_companion_observer(
        [create_user_message('Bytespark hi!')], b.append,
    )
    assert a == b


def test_observer_uses_last_user_message(
    isolated_config: dict[str, Any],
) -> None:
    """The observer reads the MOST RECENT user message, not all of them."""
    _hatch(isolated_config)
    called: list[str | None] = []
    msgs = [
        create_user_message('boring first message'),
        create_user_message('/buddy please'),
    ]
    fire_companion_observer(msgs, called.append)
    assert len(called) == 1
    # Should be a pet reply (triggered by the /buddy in the LAST message)
    from src.buddy.observer import _PET_REPLIES
    assert called[0] in _PET_REPLIES
