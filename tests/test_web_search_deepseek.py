"""WebSearch through DeepSeek's Search API (opened 2026-10-17).

A configured DeepSeek key makes WebSearch work without a Tavily key; Tavily
stays first whenever it is set. The global config these tests write is
isolated per test by the autouse fixture in ``tests/conftest.py``, and every
network call goes through a stubbed ``urllib.request.urlopen``.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from src import config as cfg_mod
from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolInputError
from src.tool_system.tools.web_search import (
    WebSearchTool,
    _deepseek_search,
    _deepseek_search_api_key,
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


# One result as the live API returned it on 2026-11-02 (trimmed).
_RESULT = {
    "title": "Change Log | DeepSeek API Docs",
    "url": "https://api-docs.deepseek.com/updates",
    "snippet": "DeepSeek-V4.1-Flash-1017 Update ...",
    "summary": "A much longer, model-oriented summary of the page.",
    "content": "",
    "site_name": "DeepSeek",
    "site_icon": "https://cdn.deepseek.com/site-icons/deepseek.com",
    "date": "2026-10-16T16:00:00+00:00",
    "rerank_score": 2.91,
    "authority": 3,
}


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch):
    """Neither backend is configured unless a test configures it."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("src.secret_store._config_env", lambda: {})


@pytest.fixture
def requests(monkeypatch):
    """Record every request; answer with ``requests.reply`` (a payload or an
    exception)."""

    class _Recorder(list):
        reply: object = {"results": [_RESULT]}

    sent = _Recorder()

    def fake_urlopen(req, timeout=None):
        sent.append({
            "url": req.full_url,
            "auth": req.headers.get("Authorization"),
            "body": json.loads(req.data.decode("utf-8")),
            "timeout": timeout,
        })
        if isinstance(sent.reply, Exception):
            raise sent.reply
        return _FakeResp(sent.reply)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return sent


def _http_error(code: int, payload: object) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.deepseek.com/v1/tools/search", code, "err", {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def _ctx() -> ToolContext:
    return ToolContext(workspace_root=".")


# --------------------------------------------------------------- selection


def test_nothing_configured_names_both_backends() -> None:
    assert is_web_search_configured() is False
    with pytest.raises(ToolInputError) as exc:
        _web_search_call({"query": "python"}, _ctx())
    message = str(exc.value)
    assert "TAVILY_API_KEY" in message
    assert "DeepSeek" in message


def test_a_deepseek_env_key_alone_configures_search(monkeypatch, requests) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert is_web_search_configured() is True

    result = _web_search_call({"query": "deepseek flash 1017"}, _ctx())

    assert [r["url"] for r in requests] == [
        "https://api.deepseek.com/v1/tools/search"
    ]
    assert requests[0]["auth"] == "Bearer sk-env"
    assert requests[0]["body"] == {
        "query": "deepseek flash 1017",
        "count": 15,
        "need_content": False,
    }
    links = [b for b in result.output["results"] if isinstance(b, dict)]
    assert links == [{
        "tool_use_id": "deepseek-search",
        "content": [{"title": _RESULT["title"], "url": _RESULT["url"]}],
    }]
    # The model reads the short snippet, not the long summary.
    text = result.output["results"][0]
    assert _RESULT["snippet"] in text
    assert _RESULT["summary"] not in text


def test_the_configured_provider_key_is_used(requests) -> None:
    cfg_mod.set_api_key("deepseek", "sk-config", base_url="https://api.deepseek.com")
    assert _deepseek_search_api_key() == "sk-config"
    _web_search_call({"query": "python"}, _ctx())
    assert requests[0]["auth"] == "Bearer sk-config"


def test_tavily_stays_first_when_both_are_configured(monkeypatch, requests) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    requests.reply = {"results": [{"title": "T", "url": "https://t.example"}]}

    result = _web_search_call({"query": "python"}, _ctx())

    assert [r["url"] for r in requests] == ["https://api.tavily.com/search"]
    assert requests[0]["auth"] == "Bearer tvly-key"
    links = [b for b in result.output["results"] if isinstance(b, dict)]
    assert links[0]["tool_use_id"] == "tavily-search"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.deepseek.com",
        "https://api.deepseek.com/",
        "https://api.deepseek.com/v1",
        "",
    ],
)
def test_official_base_urls_keep_the_backend(base_url: str) -> None:
    cfg_mod.set_api_key("deepseek", "sk-config", base_url=base_url)
    assert _deepseek_search_api_key() == "sk-config"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://my-gateway.example.com/v1",
        "https://api.deepseek.com.evil.example/v1",
        "http://localhost:8000",
    ],
)
def test_a_gateway_key_is_never_sent_to_deepseek(monkeypatch, requests, base_url) -> None:
    """A base_url on another host means the key belongs to that gateway."""
    cfg_mod.set_api_key("deepseek", "sk-gateway", base_url=base_url)
    assert _deepseek_search_api_key() is None
    assert is_web_search_configured() is False
    with pytest.raises(ToolInputError):
        _web_search_call({"query": "python"}, _ctx())
    assert requests == []


def test_a_gateway_base_url_also_blocks_the_env_key(monkeypatch) -> None:
    cfg_mod.set_api_key("deepseek", "", base_url="https://my-gateway.example.com")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert _deepseek_search_api_key() is None


# ---------------------------------------------------------------- requests


def test_queries_over_128_characters_fail_before_the_request(monkeypatch, requests) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    with pytest.raises(ToolInputError) as exc:
        _deepseek_search("x" * 129)
    assert "128" in str(exc.value)
    assert requests == []
    _deepseek_search("x" * 128)  # the limit itself is accepted
    assert len(requests) == 1


def test_count_is_clamped_to_the_api_range(monkeypatch, requests) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    _deepseek_search("python", num=500)
    _deepseek_search("python", num=0)
    assert [r["body"]["count"] for r in requests] == [50, 1]


def test_results_keep_rank_order_and_fall_back_to_summary(monkeypatch, requests) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    requests.reply = {"results": [
        {"title": "first", "url": "https://a.example", "snippet": "", "summary": "sum"},
        {"title": "", "url": "https://b.example", "snippet": "snip", "date": None},
        {"title": "", "url": ""},  # neither a title nor a URL: dropped
    ]}
    hits = _deepseek_search("python")
    assert hits == [
        {"title": "first", "url": "https://a.example", "snippet": "sum"},
        {"title": "https://b.example", "url": "https://b.example", "snippet": "snip"},
    ]


def test_empty_results_are_a_successful_search(monkeypatch, requests) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    requests.reply = {"results": []}
    result = _web_search_call({"query": "python"}, _ctx())
    assert result.output["results"] == ["No results found."]


def test_domain_filters_apply_to_deepseek_results(monkeypatch, requests) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    requests.reply = {"results": [
        {"title": "keep", "url": "https://docs.python.org/3/", "snippet": "s"},
        {"title": "drop", "url": "https://spam.example/", "snippet": "s"},
    ]}
    result = _web_search_call(
        {"query": "python", "allowed_domains": ["python.org"]}, _ctx()
    )
    links = [b for b in result.output["results"] if isinstance(b, dict)]
    assert [c["title"] for c in links[0]["content"]] == ["keep"]


@pytest.mark.parametrize("payload", [{"data": []}, ["not", "an", "object"]])
def test_a_response_without_results_is_an_error(monkeypatch, requests, payload) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    requests.reply = payload
    with pytest.raises(ToolInputError):
        _deepseek_search("python")


@pytest.mark.parametrize(
    "code, remedy",
    [
        (401, "rejected the API key"),
        (402, "topped up"),
        (429, "wait a moment"),
        (504, "wait a moment"),
    ],
)
def test_http_errors_carry_the_vendor_message_and_remedy(
    monkeypatch, requests, code, remedy
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    requests.reply = _http_error(
        code, {"error": {"message": "vendor says no.", "param": None}}
    )
    with pytest.raises(ToolInputError) as exc:
        _deepseek_search("python")
    message = str(exc.value)
    assert f"error {code}" in message
    assert "vendor says no" in message
    assert remedy in message
    assert ".." not in message


def test_a_non_json_error_body_still_reports_the_status(monkeypatch, requests) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    requests.reply = urllib.error.HTTPError(
        "https://api.deepseek.com/v1/tools/search", 502, "bad gateway", {},
        io.BytesIO(b"<html>bad gateway</html>"),
    )
    with pytest.raises(ToolInputError) as exc:
        _deepseek_search("python")
    assert "error 502" in str(exc.value)


def test_the_tool_maps_deepseek_results_for_the_model(monkeypatch, requests) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    result = WebSearchTool.call({"query": "deepseek"}, _ctx())
    api = WebSearchTool.map_result_to_api(result.output, "toolu_1")
    assert api["tool_use_id"] == "toolu_1"
    assert _RESULT["url"] in api["content"]
    assert "REMINDER" in api["content"]
