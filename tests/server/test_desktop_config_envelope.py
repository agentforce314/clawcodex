"""`PUT /api/config` must never store its own transport envelope.

The renderer sends `{"config": {...}}` and autosaves the WHOLE record it last
read, so one mis-nested write is copied forward on every later save. A stored
`config` key then shadows real settings — a voice/STT block written inside it
is invisible to the backend, which reads the top level — and `_deep_merge`
only ever adds, so it survives every subsequent write.

Seen in the wild: a config carrying a full stale snapshot under `config`,
with the live `stt` block stranded inside it.
"""

from __future__ import annotations

import json

import pytest

from src.server.desktop_serve import (
    _save_config_merged,
    strip_config_envelope,
    unwrap_config_envelope,
)


def test_unwraps_the_renderers_envelope() -> None:
    assert unwrap_config_envelope({"config": {"stt": {"enabled": True}}}) == {
        "stt": {"enabled": True}
    }


def test_unwraps_a_double_envelope() -> None:
    """The shape that caused the damage: unwrapping once leaves a `config`
    key behind, which then merges in as data."""
    assert unwrap_config_envelope({"config": {"config": {"stt": {"enabled": True}}}}) == {
        "stt": {"enabled": True}
    }


def test_leaves_a_bare_record_alone() -> None:
    record = {"providers": {"openai": {"base_url": "u"}}}

    assert unwrap_config_envelope(record) == record


def test_strip_is_a_no_op_without_an_envelope() -> None:
    record = {"stt": {"enabled": True}}

    assert strip_config_envelope(record) is record


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr("src.config.get_global_config_path", lambda: path)
    import src.config as config_mod

    config_mod._get_default_manager().invalidate()
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_FILE", path, raising=False)

    return path


def test_a_wrapped_save_lands_at_the_top_level(config_file) -> None:
    config_file.write_text(json.dumps({"providers": {"openai": {"api_key": "secret"}}}))

    result = _save_config_merged(
        unwrap_config_envelope({"config": {"stt": {"enabled": True, "provider": "openai"}}})
    )

    assert result["ok"] is True
    saved = json.loads(config_file.read_text())
    assert saved["stt"] == {"enabled": True, "provider": "openai"}
    assert "config" not in saved
    # The redacted round-trip must not cost the user their credentials.
    assert saved["providers"]["openai"]["api_key"] == "secret"


def test_an_existing_envelope_is_repaired_on_the_next_save(config_file) -> None:
    """No migration step: the next ordinary write cleans up a config that
    already picked one up."""
    config_file.write_text(
        json.dumps({
            "providers": {"openai": {"api_key": "secret"}},
            "config": {"stt": {"enabled": True}, "logoColor": "ocean"},
        })
    )

    _save_config_merged(unwrap_config_envelope({"config": {"logoColor": "sunset"}}))

    saved = json.loads(config_file.read_text())
    assert "config" not in saved
    assert saved["logoColor"] == "sunset"
    assert saved["providers"]["openai"]["api_key"] == "secret"
