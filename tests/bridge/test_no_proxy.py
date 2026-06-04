"""Tests for ``src.bridge.no_proxy``."""

from __future__ import annotations

from src.bridge.no_proxy import NO_PROXY_LIST, default_no_proxy


def test_three_anthropic_forms_in_correct_order() -> None:
    """Per gap analysis #38: order MUST be apex / Python-suffix / glob.

    Loopback/RFC1918 entries come before the Anthropic forms.
    """
    apex_idx = NO_PROXY_LIST.index('anthropic.com')
    suffix_idx = NO_PROXY_LIST.index('.anthropic.com')
    glob_idx = NO_PROXY_LIST.index('*.anthropic.com')
    assert apex_idx < suffix_idx < glob_idx, (
        f'expected apex<suffix<glob, got apex={apex_idx} suffix={suffix_idx} glob={glob_idx}'
    )


def test_default_no_proxy_is_comma_joined() -> None:
    s = default_no_proxy()
    assert isinstance(s, str)
    parts = s.split(',')
    assert parts[0] == 'localhost'
    assert 'anthropic.com' in parts
    assert '.anthropic.com' in parts
    assert '*.anthropic.com' in parts


def test_required_loopback_and_rfc1918_present() -> None:
    """The CCR container must not proxy itself or RFC1918 networks."""
    required = {
        'localhost',
        '127.0.0.1',
        '::1',
        '169.254.0.0/16',  # IMDS
        '10.0.0.0/8',
        '172.16.0.0/12',
        '192.168.0.0/16',
    }
    assert required.issubset(set(NO_PROXY_LIST))


def test_github_pypi_npm_excluded_to_prevent_mitm() -> None:
    """Package registries must bypass the upstream proxy (TLS pinning)."""
    expected = {
        'github.com',
        'api.github.com',
        '*.github.com',
        '*.githubusercontent.com',
        'registry.npmjs.org',
        'pypi.org',
        'files.pythonhosted.org',
        'index.crates.io',
        'proxy.golang.org',
    }
    assert expected.issubset(set(NO_PROXY_LIST))


def test_no_duplicates() -> None:
    assert len(NO_PROXY_LIST) == len(set(NO_PROXY_LIST))
