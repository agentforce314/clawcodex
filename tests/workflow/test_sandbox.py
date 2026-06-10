"""Tests for static ``meta`` extraction and the curated execution sandbox."""

from __future__ import annotations

import pytest

from src.workflow.errors import WorkflowMetaError
from src.workflow.sandbox import execute_workflow, extract_meta


# ── meta extraction ──────────────────────────────────────────────────────────


def test_extract_meta_valid():
    meta = extract_meta(
        'meta = {"name": "x", "description": "d", '
        '"when_to_use": "u", "phases": [{"title": "P", "detail": "dd"}], "model": "sonnet"}'
    )
    assert meta.name == "x"
    assert meta.description == "d"
    assert meta.when_to_use == "u"
    assert meta.phases == [{"title": "P", "detail": "dd"}]
    assert meta.model == "sonnet"


def test_extract_meta_annotated_assignment():
    meta = extract_meta('meta: dict = {"name": "x", "description": "d"}')
    assert meta.name == "x"
    assert meta.phases == []


def test_extract_meta_missing_block():
    with pytest.raises(WorkflowMetaError):
        extract_meta("x = 1\n")


def test_extract_meta_non_literal_rejected():
    # A computed value in `meta` must not be silently executed.
    with pytest.raises(WorkflowMetaError):
        extract_meta('name = "n"\nmeta = {"name": name, "description": "d"}')


def test_extract_meta_missing_required_key():
    with pytest.raises(WorkflowMetaError):
        extract_meta('meta = {"name": "x"}')


def test_extract_meta_empty_required_key():
    with pytest.raises(WorkflowMetaError):
        extract_meta('meta = {"name": "", "description": "d"}')


def test_extract_meta_bad_phases_type():
    with pytest.raises(WorkflowMetaError):
        extract_meta('meta = {"name": "x", "description": "d", "phases": "nope"}')


def test_extract_meta_syntax_error():
    with pytest.raises(WorkflowMetaError):
        extract_meta("meta = {")


# ── execution: return / raise / helpers / await ──────────────────────────────


async def test_top_level_return_value():
    assert await execute_workflow("return 1 + 2", {}, None) == 3


async def test_falls_off_end_returns_none():
    assert await execute_workflow("x = 5", {}, None) is None


async def test_top_level_raise_propagates():
    with pytest.raises(ValueError):
        await execute_workflow('raise ValueError("boom")', {}, None)


async def test_helper_def_and_await_injected_primitive():
    async def ping(value):
        return value * 2

    body = "def double(n):\n    return n * 2\nreturn await ping(double(3))"
    assert await execute_workflow(body, {"ping": ping}, None) == 12


async def test_args_is_injected():
    assert await execute_workflow("return args['k']", {}, {"k": 42}) == 42


# ── sandbox restrictions ─────────────────────────────────────────────────────


async def test_json_available_as_global_without_import():
    assert await execute_workflow('return json.dumps({"a": 1})', {}, None) == '{"a": 1}'


async def test_math_and_re_available():
    assert await execute_workflow("return math.floor(3.9)", {}, None) == 3
    assert await execute_workflow('return re.sub(r"a", "b", "aaa")', {}, None) == "bbb"


async def test_import_json_allowed():
    assert await execute_workflow("import json\nreturn json.dumps([1, 2])", {}, None) == "[1, 2]"


async def test_import_os_blocked():
    with pytest.raises(ImportError):
        await execute_workflow("import os\nreturn os.getcwd()", {}, None)


async def test_import_subprocess_blocked():
    with pytest.raises(ImportError):
        await execute_workflow("import subprocess\nreturn 1", {}, None)


async def test_open_is_unavailable():
    with pytest.raises(NameError):
        await execute_workflow('return open("/etc/passwd")', {}, None)


async def test_eval_is_unavailable():
    with pytest.raises(NameError):
        await execute_workflow('return eval("1+1")', {}, None)


async def test_time_excluded_for_determinism():
    # Not importable...
    with pytest.raises(ImportError):
        await execute_workflow("import time\nreturn time.time()", {}, None)
    # ...and not a pre-bound global either.
    with pytest.raises(NameError):
        await execute_workflow("return time.time()", {}, None)


async def test_random_excluded_for_determinism():
    with pytest.raises(ImportError):
        await execute_workflow("import random\nreturn random.random()", {}, None)
