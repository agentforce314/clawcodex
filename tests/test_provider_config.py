from __future__ import annotations

import unittest

from src.services.api.provider_config import ProviderOverride, resolve_agent_provider


class TestResolveAgentProvider(unittest.TestCase):
    def test_returns_none_without_settings(self) -> None:
        self.assertIsNone(resolve_agent_provider("agent", "subagent", None))

    def test_returns_none_without_routing(self) -> None:
        self.assertIsNone(resolve_agent_provider("agent", None, {"agentModels": {}}))

    def test_returns_none_without_models(self) -> None:
        self.assertIsNone(resolve_agent_provider("agent", None, {"agentRouting": {}}))

    def test_resolves_by_name(self) -> None:
        settings = {
            "agentRouting": {"myagent": "fast-model"},
            "agentModels": {
                "fast-model": {"base_url": "http://localhost:8080", "api_key": "sk-123"},
            },
        }
        result = resolve_agent_provider("myagent", None, settings)
        self.assertIsNotNone(result)
        self.assertEqual(result.model, "fast-model")
        self.assertEqual(result.base_url, "http://localhost:8080")
        self.assertEqual(result.api_key, "sk-123")

    def test_resolves_by_subagent_type(self) -> None:
        settings = {
            "agentRouting": {"code-reviewer": "review-model"},
            "agentModels": {
                "review-model": {"base_url": "", "api_key": ""},
            },
        }
        result = resolve_agent_provider("unknown", "code-reviewer", settings)
        self.assertIsNotNone(result)
        self.assertEqual(result.model, "review-model")

    def test_falls_back_to_default(self) -> None:
        settings = {
            "agentRouting": {"default": "default-model"},
            "agentModels": {
                "default-model": {"base_url": "", "api_key": ""},
            },
        }
        result = resolve_agent_provider("unknown", "also-unknown", settings)
        self.assertIsNotNone(result)
        self.assertEqual(result.model, "default-model")

    def test_returns_none_if_model_not_found(self) -> None:
        settings = {
            "agentRouting": {"agent": "nonexistent-model"},
            "agentModels": {},
        }
        result = resolve_agent_provider("agent", None, settings)
        self.assertIsNone(result)

    def test_normalizes_key_casing(self) -> None:
        settings = {
            "agentRouting": {"My-Agent": "fast-model"},
            "agentModels": {
                "fast-model": {"base_url": "", "api_key": ""},
            },
        }
        result = resolve_agent_provider("my_agent", None, settings)
        self.assertIsNotNone(result)

    def test_frozen_dataclass(self) -> None:
        p = ProviderOverride(model="m", base_url="u", api_key="k")
        with self.assertRaises(AttributeError):
            p.model = "other"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
