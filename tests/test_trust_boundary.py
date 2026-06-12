"""Unit tests for ``src/permissions/trust_boundary.py`` (ch02 round-3).

The boundary is SOURCE-class based (TS managedEnv.ts:106-110):

* trusted tiers (global config, user settings) apply IN FULL pre-trust;
* project-scoped tiers apply only the ``SAFE_ENV_KEYS`` subset pre-trust
  and in full post-trust;
* the MDM policy tier applies in full, last, and alone may override the
  inherited shell environment;
* original (non-empty) shell env keys are never overridden by config
  tiers — the port's documented shell-wins customization.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from src.permissions.trust_boundary import (
    SAFE_ENV_KEYS,
    UNSAFE_ENV_KEYS,
    UNSAFE_ENV_PREFIXES,
    apply_full_config_environment_variables,
    apply_safe_config_environment_variables,
    is_safe_env_key,
    reset_trust_boundary_for_test_only,
)


class TestSafeKeyAllowList(unittest.TestCase):
    def test_every_entry_in_safe_env_keys_is_safe(self) -> None:
        for key in SAFE_ENV_KEYS:
            with self.subTest(key=key):
                self.assertTrue(
                    is_safe_env_key(key),
                    f"{key!r} is in SAFE_ENV_KEYS but is_safe_env_key returns False",
                )

    def test_set_size_matches_ts_port(self) -> None:
        # Sanity check: TS managedEnvConstants.ts:109-194 has 82 entries
        # in SAFE_ENV_VARS. Our port should match exactly.
        self.assertEqual(
            len(SAFE_ENV_KEYS),
            82,
            "SAFE_ENV_KEYS count drifted from the TS port",
        )

    def test_membership_is_case_insensitive(self) -> None:
        # TS tests key.toUpperCase() (managedEnv.ts:175) — a project
        # settings file may write `anthropic_model` and it still counts
        # as the safe key.
        self.assertTrue(is_safe_env_key("anthropic_model"))
        self.assertTrue(is_safe_env_key("Disable_Telemetry"))


class TestUnsafeKeysBlocked(unittest.TestCase):
    """``is_safe_env_key`` is the PROJECT-TIER gate. These keys may
    never apply pre-trust **from a project-scoped source**; trusted
    tiers (global config, user settings, MDM) may legitimately set
    them — see TS caCertsConfig.ts:59-66 for the same source-relative
    distinction on NODE_EXTRA_CA_CERTS."""

    def test_path_returns_false(self) -> None:
        self.assertFalse(is_safe_env_key("PATH"))

    def test_pythonpath_returns_false(self) -> None:
        self.assertFalse(is_safe_env_key("PYTHONPATH"))

    def test_node_options_returns_false(self) -> None:
        self.assertFalse(is_safe_env_key("NODE_OPTIONS"))

    def test_node_extra_ca_certs_returns_false(self) -> None:
        # TS flags NODE_EXTRA_CA_CERTS as "TRUST ATTACKER-CONTROLLED
        # SERVER" for PROJECT sources (managedEnvConstants.ts:99-103);
        # the same key from global config/user settings applies
        # pre-trust via the trusted-tier pass (caCertsConfig.ts:59-66).
        self.assertFalse(is_safe_env_key("NODE_EXTRA_CA_CERTS"))

    def test_anthropic_base_url_returns_false(self) -> None:
        # "REDIRECT TO ATTACKER-CONTROLLED SERVER"
        # (managedEnvConstants.ts:95-97).
        self.assertFalse(is_safe_env_key("ANTHROPIC_BASE_URL"))

    def test_anthropic_api_key_returns_false(self) -> None:
        # "SWITCH TO ATTACKER-CONTROLLED PROJECT"
        # (managedEnvConstants.ts:105-108).
        self.assertFalse(is_safe_env_key("ANTHROPIC_API_KEY"))


class TestUnsafePrefixesBlocked(unittest.TestCase):
    def test_ld_preload_returns_false(self) -> None:
        self.assertFalse(is_safe_env_key("LD_PRELOAD"))

    def test_ld_library_path_returns_false(self) -> None:
        self.assertFalse(is_safe_env_key("LD_LIBRARY_PATH"))

    def test_dyld_insert_libraries_returns_false(self) -> None:
        self.assertFalse(is_safe_env_key("DYLD_INSERT_LIBRARIES"))


class TestUnknownKeyDefaultDeny(unittest.TestCase):
    def test_random_key_returns_false(self) -> None:
        self.assertFalse(is_safe_env_key("FOO_BAR_BAZ"))

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(is_safe_env_key(""))


class _HermeticEnvCase(unittest.TestCase):
    """Base for application-semantics tests: resets the module's
    shell-env snapshot, patches all tier loaders to empty (tests opt in
    per tier), and restores every env key it touches."""

    TOUCHED_KEYS: tuple[str, ...] = ()

    def setUp(self) -> None:
        reset_trust_boundary_for_test_only()
        self._saved = {k: os.environ.get(k) for k in self.TOUCHED_KEYS}
        for key in self.TOUCHED_KEYS:
            os.environ.pop(key, None)
        self._patches = [
            mock.patch(
                "src.permissions.trust_boundary._load_global_config_env",
                return_value={},
            ),
            mock.patch(
                "src.permissions.trust_boundary._load_user_settings_env",
                return_value={},
            ),
            mock.patch(
                "src.permissions.trust_boundary._load_project_scoped_env",
                return_value={},
            ),
        ]
        self.mock_global, self.mock_user, self.mock_project = [
            p.start() for p in self._patches
        ]

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_trust_boundary_for_test_only()


class TestTrustedTiersApplyFully(_HermeticEnvCase):
    """TS applies trusted-source env IN FULL pre-trust
    (managedEnv.ts:137-159) — including keys on the project-unsafe list."""

    TOUCHED_KEYS = ("NODE_TLS_REJECT_UNAUTHORIZED", "ANTHROPIC_MODEL")

    def test_global_config_unsafe_class_key_applies_pre_trust(self) -> None:
        apply_safe_config_environment_variables(
            config_env={"NODE_TLS_REJECT_UNAUTHORIZED": "0"}
        )
        self.assertEqual(os.environ.get("NODE_TLS_REJECT_UNAUTHORIZED"), "0")

    def test_user_settings_env_applies_pre_trust(self) -> None:
        self.mock_user.return_value = {"ANTHROPIC_MODEL": "from-user-settings"}
        apply_safe_config_environment_variables(config_env={})
        self.assertEqual(
            os.environ.get("ANTHROPIC_MODEL"), "from-user-settings"
        )

    def test_user_settings_beat_global(self) -> None:
        self.mock_user.return_value = {"ANTHROPIC_MODEL": "from-user-settings"}
        apply_safe_config_environment_variables(
            config_env={"ANTHROPIC_MODEL": "from-global"}
        )
        self.assertEqual(
            os.environ.get("ANTHROPIC_MODEL"), "from-user-settings"
        )


class TestProjectTierSafeSubset(_HermeticEnvCase):
    """Project-scoped sources: SAFE subset pre-trust (managedEnv.ts:173-178),
    everything post-trust."""

    TOUCHED_KEYS = (
        "ANTHROPIC_MODEL",
        "NODE_EXTRA_CA_CERTS",
        "LD_PRELOAD",
        "GIT_WORK_TREE_TEST_TB",
    )

    def test_project_safe_key_applies_pre_trust(self) -> None:
        self.mock_project.return_value = {"ANTHROPIC_MODEL": "from-project"}
        apply_safe_config_environment_variables(config_env={})
        self.assertEqual(os.environ.get("ANTHROPIC_MODEL"), "from-project")

    def test_project_safe_key_lowercase_applies_pre_trust(self) -> None:
        self.mock_project.return_value = {"anthropic_model": "from-project"}
        apply_safe_config_environment_variables(config_env={})
        self.assertEqual(os.environ.get("anthropic_model"), "from-project")

    def test_project_unsafe_keys_never_apply_pre_trust(self) -> None:
        self.mock_project.return_value = {
            "NODE_EXTRA_CA_CERTS": "/opt/evil.crt",
            "LD_PRELOAD": "/opt/evil.so",
        }
        apply_safe_config_environment_variables(config_env={})
        self.assertIsNone(os.environ.get("NODE_EXTRA_CA_CERTS"))
        self.assertIsNone(os.environ.get("LD_PRELOAD"))

    def test_full_pass_applies_project_unsafe_keys(self) -> None:
        # Post-trust the project tier applies in full — for keys not
        # reserved by the shell snapshot (GIT_WORK_TREE-style keys are
        # the TS-cited motivation, main.tsx:1955-1961).
        self.mock_project.return_value = {
            "GIT_WORK_TREE_TEST_TB": "/repo/worktree"
        }
        apply_full_config_environment_variables(config_env={})
        self.assertEqual(
            os.environ.get("GIT_WORK_TREE_TEST_TB"), "/repo/worktree"
        )

    def test_full_pass_project_beats_global(self) -> None:
        self.mock_project.return_value = {"ANTHROPIC_MODEL": "from-project"}
        apply_full_config_environment_variables(
            config_env={"ANTHROPIC_MODEL": "from-global"}
        )
        self.assertEqual(os.environ.get("ANTHROPIC_MODEL"), "from-project")


class TestShellWinsCustomization(_HermeticEnvCase):
    """Documented port divergence from TS Object.assign: variables
    present (non-empty) in the ORIGINAL process environment are never
    overridden by config tiers — only by the MDM policy tier. Mechanism:
    TS's CCD spawn-env-keys filter (managedEnv.ts:62-80) always-on."""

    TOUCHED_KEYS = ("ANTHROPIC_MODEL",)

    def test_shell_var_survives_safe_and_full_pass(self) -> None:
        os.environ["ANTHROPIC_MODEL"] = "from-shell"
        reset_trust_boundary_for_test_only()  # snapshot AFTER the export
        apply_safe_config_environment_variables(
            config_env={"ANTHROPIC_MODEL": "from-config"}
        )
        self.assertEqual(os.environ["ANTHROPIC_MODEL"], "from-shell")
        apply_full_config_environment_variables(
            config_env={"ANTHROPIC_MODEL": "from-config"}
        )
        self.assertEqual(os.environ["ANTHROPIC_MODEL"], "from-shell")

    def test_empty_shell_var_does_not_reserve_the_key(self) -> None:
        # Carry-over from the retired secret_store applier: an empty env
        # var counts as unset (`export FOO=` should not mask config).
        os.environ["ANTHROPIC_MODEL"] = "   "
        reset_trust_boundary_for_test_only()
        apply_safe_config_environment_variables(
            config_env={"ANTHROPIC_MODEL": "from-config"}
        )
        self.assertEqual(os.environ["ANTHROPIC_MODEL"], "from-config")

    def test_mdm_overrides_shell(self) -> None:
        # The policy tier is the single exception (TS applies
        # policySettings last among trusted sources; IT must win).
        os.environ["ANTHROPIC_MODEL"] = "from-shell"
        reset_trust_boundary_for_test_only()
        apply_safe_config_environment_variables(
            config_env={}, extra_env={"ANTHROPIC_MODEL": "from-mdm"}
        )
        self.assertEqual(os.environ["ANTHROPIC_MODEL"], "from-mdm")


class TestMdmPolicyTier(_HermeticEnvCase):
    TOUCHED_KEYS = ("ANTHROPIC_MODEL", "HTTPS_PROXY")

    def test_mdm_applies_last_over_other_tiers(self) -> None:
        self.mock_project.return_value = {"ANTHROPIC_MODEL": "from-project"}
        apply_safe_config_environment_variables(
            config_env={"ANTHROPIC_MODEL": "from-global"},
            extra_env={"ANTHROPIC_MODEL": "from-mdm"},
        )
        self.assertEqual(os.environ["ANTHROPIC_MODEL"], "from-mdm")

    def test_mdm_unsafe_class_keys_apply(self) -> None:
        # Root-owned policy tier is unfiltered (TS applies policySettings
        # without a safe-key filter, managedEnv.ts:160-172) — the
        # enterprise-proxy use case.
        apply_safe_config_environment_variables(
            config_env={}, extra_env={"HTTPS_PROXY": "http://corp-proxy:3128"}
        )
        self.assertEqual(
            os.environ.get("HTTPS_PROXY"), "http://corp-proxy:3128"
        )

    def test_full_pass_reasserts_mdm_last(self) -> None:
        # The safe pass stashes the MDM env; the full pass re-applies it
        # after the project tier so policy survives post-trust too.
        apply_safe_config_environment_variables(
            config_env={}, extra_env={"ANTHROPIC_MODEL": "from-mdm"}
        )
        self.mock_project.return_value = {"ANTHROPIC_MODEL": "from-project"}
        apply_full_config_environment_variables(config_env={})
        self.assertEqual(os.environ["ANTHROPIC_MODEL"], "from-mdm")


class TestLoadConfigEnvFallthrough(unittest.TestCase):
    def test_no_global_config_does_not_raise(self) -> None:
        reset_trust_boundary_for_test_only()
        with mock.patch(
            "src.config.ConfigManager.load_global", return_value={}
        ), mock.patch(
            "src.config.ConfigManager.load_project", return_value={}
        ), mock.patch(
            "src.config.ConfigManager.load_local", return_value={}
        ), mock.patch(
            "src.permissions.trust_boundary._read_settings_env",
            return_value={},
        ):
            apply_safe_config_environment_variables()  # must not raise


class TestUnsafeKeySetIsAuditable(unittest.TestCase):
    """Documented audit guarantees: the explicit unsafe set has every
    name the chapter / TS reference calls out by name. NB these are
    PROJECT-TIER exclusions; trusted tiers may set them (the
    classification is source-relative — caCertsConfig.ts:59-66)."""

    def test_chapter_mentioned_keys_are_explicitly_unsafe(self) -> None:
        self.assertIn("PATH", UNSAFE_ENV_KEYS)
        self.assertIn("NODE_OPTIONS", UNSAFE_ENV_KEYS)
        self.assertIn("LD_", UNSAFE_ENV_PREFIXES)

    def test_ts_attack_category_keys_explicitly_unsafe(self) -> None:
        for expected in (
            "NODE_EXTRA_CA_CERTS",
            "NODE_TLS_REJECT_UNAUTHORIZED",
            "ANTHROPIC_BASE_URL",
            "HTTP_PROXY",
            "ANTHROPIC_API_KEY",
            "AWS_BEARER_TOKEN_BEDROCK",
        ):
            with self.subTest(key=expected):
                self.assertIn(expected, UNSAFE_ENV_KEYS)


if __name__ == "__main__":
    unittest.main()
