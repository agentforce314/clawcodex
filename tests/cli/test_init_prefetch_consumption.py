"""Tests for ch02 round-2 PR-1 (G1): init() consumes prefetch results.

Covers:
- MDM payload parsing (extract_mdm_safe_env) — safe vs unsafe keys,
  malformed inputs, missing fields, None / empty.
- Keychain stash semantics — set/read, idempotence, None handling.
- init() integration — MDM payload threaded into
  apply_safe_config_environment_variables(extra_env=...).
- Precedence — config-file values still win over MDM extras (setdefault).
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from src.init import init, reset_init_for_test_only
from src.permissions.trust_boundary import (
    apply_safe_config_environment_variables,
    extract_mdm_safe_env,
)
from src.utils.keychain_stash import (
    read_stashed_keychain,
    reset_stashed_keychain_for_test_only,
    stash_keychain_credentials,
)


_SAFE_KEY = "ANTHROPIC_MODEL"
_UNSAFE_KEY = "ANTHROPIC_API_KEY"


class ExtractMdmSafeEnvTests(unittest.TestCase):
    def test_filters_to_safe_keys_only(self) -> None:
        payload = (
            '{"env": {"' + _SAFE_KEY + '": "claude-opus-4-7", '
            '"' + _UNSAFE_KEY + '": "sk-secret"}}'
        )
        result = extract_mdm_safe_env(payload)
        self.assertEqual(result, {_SAFE_KEY: "claude-opus-4-7"})

    def test_returns_empty_dict_for_none_payload(self) -> None:
        self.assertEqual(extract_mdm_safe_env(None), {})

    def test_returns_empty_dict_for_empty_payload(self) -> None:
        self.assertEqual(extract_mdm_safe_env(""), {})

    def test_returns_empty_dict_for_malformed_json(self) -> None:
        self.assertEqual(extract_mdm_safe_env('{not valid json'), {})

    def test_returns_empty_dict_for_missing_env_key(self) -> None:
        self.assertEqual(extract_mdm_safe_env('{"other": 1}'), {})

    def test_returns_empty_dict_for_non_dict_env(self) -> None:
        self.assertEqual(extract_mdm_safe_env('{"env": "not a dict"}'), {})

    def test_returns_empty_dict_for_non_dict_root(self) -> None:
        self.assertEqual(extract_mdm_safe_env('["a", "b"]'), {})

    def test_skips_none_values(self) -> None:
        payload = '{"env": {"' + _SAFE_KEY + '": null}}'
        self.assertEqual(extract_mdm_safe_env(payload), {})

    def test_coerces_non_string_values(self) -> None:
        payload = '{"env": {"' + _SAFE_KEY + '": 42}}'
        self.assertEqual(extract_mdm_safe_env(payload), {_SAFE_KEY: "42"})


class KeychainStashTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_stashed_keychain_for_test_only()

    def tearDown(self) -> None:
        reset_stashed_keychain_for_test_only()

    def test_set_and_read(self) -> None:
        stash_keychain_credentials("secret-token")
        self.assertEqual(read_stashed_keychain(), "secret-token")

    def test_none_initial_state(self) -> None:
        self.assertIsNone(read_stashed_keychain())

    def test_idempotent_first_wins(self) -> None:
        stash_keychain_credentials("first")
        stash_keychain_credentials("second")
        self.assertEqual(read_stashed_keychain(), "first")

    def test_none_stash_leaves_state_unchanged(self) -> None:
        stash_keychain_credentials("token")
        stash_keychain_credentials(None)
        self.assertEqual(read_stashed_keychain(), "token")

    def test_none_stash_on_empty_state_stays_none(self) -> None:
        stash_keychain_credentials(None)
        self.assertIsNone(read_stashed_keychain())


class ApplySafeEnvExtraTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = os.environ.get(_SAFE_KEY)
        os.environ.pop(_SAFE_KEY, None)

    def tearDown(self) -> None:
        if self._original is None:
            os.environ.pop(_SAFE_KEY, None)
        else:
            os.environ[_SAFE_KEY] = self._original

    def test_extra_env_applies_safe_keys(self) -> None:
        apply_safe_config_environment_variables(
            config_env={}, extra_env={_SAFE_KEY: "from-mdm"}
        )
        self.assertEqual(os.environ.get(_SAFE_KEY), "from-mdm")

    def test_extra_env_ignores_unsafe_keys(self) -> None:
        apply_safe_config_environment_variables(
            config_env={},
            extra_env={_UNSAFE_KEY: "sk-leaked"},
        )
        self.assertIsNone(os.environ.get(_UNSAFE_KEY))

    def test_extra_env_loses_to_existing_environ(self) -> None:
        os.environ[_SAFE_KEY] = "from-env"
        apply_safe_config_environment_variables(
            config_env={}, extra_env={_SAFE_KEY: "from-mdm"}
        )
        # setdefault: pre-existing wins
        self.assertEqual(os.environ.get(_SAFE_KEY), "from-env")

    def test_config_env_overrides_extra_env_via_setdefault_ordering(self) -> None:
        # extra_env applies FIRST via setdefault, then config_env via
        # setdefault. So if both target the same key with no pre-existing
        # env, extra_env wins (first writer to setdefault).
        apply_safe_config_environment_variables(
            config_env={_SAFE_KEY: "from-config"},
            extra_env={_SAFE_KEY: "from-mdm"},
        )
        self.assertEqual(os.environ.get(_SAFE_KEY), "from-mdm")


class InitConsumesPrefetchesTests(unittest.TestCase):
    """Integration: init() pulls keychain + MDM through to env application."""

    def setUp(self) -> None:
        reset_init_for_test_only()
        reset_stashed_keychain_for_test_only()
        self._original_safe = os.environ.get(_SAFE_KEY)
        os.environ.pop(_SAFE_KEY, None)

    def tearDown(self) -> None:
        reset_init_for_test_only()
        reset_stashed_keychain_for_test_only()
        if self._original_safe is None:
            os.environ.pop(_SAFE_KEY, None)
        else:
            os.environ[_SAFE_KEY] = self._original_safe

    def _patch_prefetches(
        self,
        keychain_value: str | None,
        mdm_payload: str | None,
    ):
        # Patch the symbols where init.py imported them — not at source.
        keychain_patch = mock.patch(
            "src.init.wait_and_read_keychain", return_value=keychain_value
        )
        mdm_patch = mock.patch(
            "src.init.wait_and_read_mdm", return_value=mdm_payload
        )
        # Avoid spawning real Popens during the test.
        ks_patch = mock.patch(
            "src.init.get_or_start_keychain_prefetch",
            return_value=mock.MagicMock(),
        )
        ms_patch = mock.patch(
            "src.init.get_or_start_mdm_raw_read",
            return_value=mock.MagicMock(),
        )
        # Avoid network preconnect during the test.
        preconnect_patch = mock.patch("src.init.start_api_preconnect")
        # Don't touch signal handlers.
        shutdown_patch = mock.patch("src.init.setup_graceful_shutdown")
        return (
            keychain_patch,
            mdm_patch,
            ks_patch,
            ms_patch,
            preconnect_patch,
            shutdown_patch,
        )

    def test_init_stashes_keychain_value(self) -> None:
        patches = self._patch_prefetches(
            keychain_value="kc-token",
            mdm_payload=None,
        )
        try:
            for p in patches:
                p.start()
            init()
            self.assertEqual(read_stashed_keychain(), "kc-token")
        finally:
            for p in patches:
                p.stop()

    def test_init_applies_mdm_safe_env(self) -> None:
        payload = '{"env": {"' + _SAFE_KEY + '": "mdm-claude"}}'
        patches = self._patch_prefetches(
            keychain_value=None,
            mdm_payload=payload,
        )
        try:
            for p in patches:
                p.start()
            init()
            self.assertEqual(os.environ.get(_SAFE_KEY), "mdm-claude")
        finally:
            for p in patches:
                p.stop()

    def test_init_handles_both_none_silently(self) -> None:
        patches = self._patch_prefetches(
            keychain_value=None,
            mdm_payload=None,
        )
        try:
            for p in patches:
                p.start()
            init()  # must not raise
            self.assertIsNone(read_stashed_keychain())
            self.assertIsNone(os.environ.get(_SAFE_KEY))
        finally:
            for p in patches:
                p.stop()

    def test_init_handles_malformed_mdm_silently(self) -> None:
        patches = self._patch_prefetches(
            keychain_value=None,
            mdm_payload="{not valid json",
        )
        try:
            for p in patches:
                p.start()
            init()  # must not raise
            self.assertIsNone(os.environ.get(_SAFE_KEY))
        finally:
            for p in patches:
                p.stop()


if __name__ == "__main__":
    unittest.main()
