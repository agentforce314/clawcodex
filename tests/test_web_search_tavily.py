"""Tests for the Tavily web-search backend (replaces DuckDuckGo)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolInputError
from src.tool_system.tools.web_search import (
    WebSearchTool,
    _tavily_search,
    _web_search_call,
    is_web_search_configured,
)


class _FakeResp:
    def __init__(self, payload):
        self._d = json.dumps(payload).encode("utf-8")

    def read(self, _n=None):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_unconfigured_raises_clear_error(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    # Isolate from the developer's real ~/.clawcodex/config.json "env" block.
    monkeypatch.setattr("src.secret_store._config_env", lambda: {})
    assert is_web_search_configured() is False
    with pytest.raises(ToolInputError) as exc:
        _tavily_search("python")
    assert "TAVILY_API_KEY" in str(exc.value)


def test_configured_via_config_env(monkeypatch):
    """A key stored in config.json's 'env' block configures search (no export)."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(
        "src.secret_store._config_env", lambda: {"TAVILY_API_KEY": "tvly-from-config"}
    )
    assert is_web_search_configured() is True
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization")
        return _FakeResp({"results": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    _tavily_search("python")
    assert captured["auth"] == "Bearer tvly-from-config"


def test_parses_results_and_request_shape(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test123")
    assert is_web_search_configured() is True
    payload = {
        "results": [
            {"title": "Python", "url": "https://python.org", "content": "The official site"},
            {"title": "Docs", "url": "https://docs.python.org", "content": "Documentation"},
        ]
    }
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data)
        return _FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    hits = _tavily_search("python", num=5)

    assert len(hits) == 2
    assert hits[0]["title"] == "Python"
    assert hits[0]["url"] == "https://python.org"
    assert hits[0]["snippet"] == "The official site"  # content -> snippet alias
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer tvly-test123"
    assert captured["body"]["query"] == "python"
    assert captured["body"]["max_results"] == 5
    assert captured["body"]["include_answer"] is False


def test_http_error_is_surfaced(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-bad")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ToolInputError) as exc:
        _tavily_search("python")
    assert "401" in str(exc.value)


def test_full_call_returns_tool_result(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    payload = {"results": [{"title": "T", "url": "https://t.example", "content": "snippet here"}]}
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeResp(payload))

    result = _web_search_call({"query": "test query"}, ToolContext(workspace_root=tmp_path))
    assert result.name == "WebSearch"
    assert result.output["query"] == "test query"
    # the structured links block carries the hit
    blocks = result.output["results"]
    assert any(isinstance(b, dict) and b.get("tool_use_id") == "tavily-search" for b in blocks)


def test_tool_is_still_registered_and_named():
    assert WebSearchTool.name == "WebSearch"
    assert WebSearchTool.is_read_only({}) is True
