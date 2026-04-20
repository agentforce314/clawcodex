import pytest
from unittest.mock import patch

from src.hooks.ssrf_guard import (
    validate_hook_url,
    is_safe_url,
    _is_private_ip,
)


class TestIsPrivateIp:
    def test_loopback_v4(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_loopback_v6(self):
        assert _is_private_ip("::1") is True

    def test_private_10(self):
        assert _is_private_ip("10.0.0.1") is True

    def test_private_172(self):
        assert _is_private_ip("172.16.0.1") is True

    def test_private_192(self):
        assert _is_private_ip("192.168.1.1") is True

    def test_link_local(self):
        assert _is_private_ip("169.254.1.1") is True

    def test_public_ip(self):
        assert _is_private_ip("8.8.8.8") is False

    def test_public_ip_2(self):
        assert _is_private_ip("93.184.216.34") is False

    def test_invalid_ip(self):
        assert _is_private_ip("not-an-ip") is False


class TestValidateHookUrl:
    def test_valid_https(self):
        safe, reason = validate_hook_url("https://hooks.example.com/webhook", resolve_dns=False)
        assert safe is True
        assert reason is None

    def test_valid_http(self):
        safe, reason = validate_hook_url("http://hooks.example.com/webhook", resolve_dns=False)
        assert safe is True
        assert reason is None

    def test_invalid_scheme_ftp(self):
        safe, reason = validate_hook_url("ftp://example.com/file")
        assert safe is False
        assert "scheme" in reason.lower()

    def test_invalid_scheme_file(self):
        safe, reason = validate_hook_url("file:///etc/passwd")
        assert safe is False

    def test_localhost(self):
        safe, reason = validate_hook_url("http://localhost:8080/hook", resolve_dns=False)
        assert safe is False
        assert "localhost" in reason.lower()

    def test_localhost_localdomain(self):
        safe, reason = validate_hook_url("http://localhost.localdomain/hook", resolve_dns=False)
        assert safe is False

    def test_private_ip_10(self):
        safe, reason = validate_hook_url("http://10.0.0.1/hook", resolve_dns=False)
        assert safe is False
        assert "private" in reason.lower()

    def test_private_ip_172(self):
        safe, reason = validate_hook_url("http://172.16.0.1/hook", resolve_dns=False)
        assert safe is False

    def test_private_ip_192(self):
        safe, reason = validate_hook_url("http://192.168.1.1/hook", resolve_dns=False)
        assert safe is False

    def test_loopback_ipv4(self):
        safe, reason = validate_hook_url("http://127.0.0.1/hook", resolve_dns=False)
        assert safe is False

    def test_cloud_metadata_aws(self):
        safe, reason = validate_hook_url("http://169.254.169.254/latest/meta-data/", resolve_dns=False)
        assert safe is False

    def test_cloud_metadata_ecs(self):
        safe, reason = validate_hook_url("http://169.254.170.2/", resolve_dns=False)
        assert safe is False

    def test_google_metadata(self):
        safe, reason = validate_hook_url("http://metadata.google.internal/computeMetadata/", resolve_dns=False)
        assert safe is False

    def test_no_hostname(self):
        safe, reason = validate_hook_url("http:///path", resolve_dns=False)
        assert safe is False

    def test_dns_resolution_private(self):
        with patch("src.hooks.ssrf_guard._resolve_hostname", return_value=["127.0.0.1"]):
            safe, reason = validate_hook_url("http://evil.example.com/hook", resolve_dns=True)
            assert safe is False
            assert "private" in reason.lower()

    def test_dns_resolution_metadata(self):
        with patch("src.hooks.ssrf_guard._resolve_hostname", return_value=["169.254.169.254"]):
            safe, reason = validate_hook_url("http://evil.example.com/hook", resolve_dns=True)
            assert safe is False

    def test_dns_resolution_public(self):
        with patch("src.hooks.ssrf_guard._resolve_hostname", return_value=["93.184.216.34"]):
            safe, reason = validate_hook_url("http://example.com/hook", resolve_dns=True)
            assert safe is True


class TestIsSafeUrl:
    def test_safe(self):
        assert is_safe_url("https://hooks.example.com/webhook", resolve_dns=False) is True

    def test_unsafe(self):
        assert is_safe_url("http://localhost:8080/hook", resolve_dns=False) is False
