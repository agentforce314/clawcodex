from __future__ import annotations

import os
import pytest
from src.services.mcp.env_expansion import expand_env_vars_in_string


class TestExpandEnvVarsInString:
    def test_simple_expansion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_VAR", "hello")
        result = expand_env_vars_in_string("${TEST_VAR} world")
        assert result.expanded == "hello world"
        assert result.missing_vars == []

    def test_default_value(self) -> None:
        result = expand_env_vars_in_string("${NONEXISTENT_VAR:-default}")
        assert result.expanded == "default"
        assert result.missing_vars == []

    def test_missing_variable(self) -> None:
        result = expand_env_vars_in_string("${DEFINITELY_NOT_SET}")
        assert "${DEFINITELY_NOT_SET}" in result.expanded
        assert "DEFINITELY_NOT_SET" in result.missing_vars

    def test_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A_VAR", "a")
        monkeypatch.setenv("B_VAR", "b")
        result = expand_env_vars_in_string("${A_VAR}+${B_VAR}")
        assert result.expanded == "a+b"
        assert result.missing_vars == []

    def test_no_vars(self) -> None:
        result = expand_env_vars_in_string("no variables here")
        assert result.expanded == "no variables here"
        assert result.missing_vars == []

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_VAR", "actual")
        result = expand_env_vars_in_string("${TEST_VAR:-default}")
        assert result.expanded == "actual"

    def test_empty_default(self) -> None:
        result = expand_env_vars_in_string("${NONEXISTENT:-}")
        assert result.expanded == ""
        assert result.missing_vars == []
