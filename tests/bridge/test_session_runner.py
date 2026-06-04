"""Tests for ``src.bridge.session_runner``.

Three categories:

1. Pure helpers — ``build_child_env``, ``safe_filename_id``,
   ``extract_activities`` (no subprocess).
2. Integration tests using a real child process (``sys.executable`` + a
   small Python script) to exercise spawn → NDJSON parse → done.
3. Lifecycle — kill/force_kill, write_stdin, update_access_token.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from typing import Any

import pytest

from src.bridge.session_runner import (
    CHILD_ENV_ALLOWLIST,
    MAX_ACTIVITIES,
    MAX_STDERR_LINES,
    BuildChildEnvOpts,
    PermissionRequest,
    SessionSpawnerDeps,
    build_child_env,
    create_session_spawner,
    extract_activities,
    safe_filename_id,
)


# ── build_child_env ──────────────────────────────────────────────────────


def test_build_child_env_strips_non_allowlisted() -> None:
    parent = {
        'PATH': '/usr/bin',
        'ANTHROPIC_API_KEY': 'secret-key',
        'CLAUDE_CODE_OAUTH_TOKEN': 'oauth-secret',
        'DATABASE_PASSWORD': 'db-secret',
        'HOME': '/Users/test',
    }
    env = build_child_env(parent, BuildChildEnvOpts(access_token='tok'))
    assert env['PATH'] == '/usr/bin'
    assert env['HOME'] == '/Users/test'
    assert 'ANTHROPIC_API_KEY' not in env
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in env
    assert 'DATABASE_PASSWORD' not in env


def test_build_child_env_sets_required_bridge_vars() -> None:
    env = build_child_env({}, BuildChildEnvOpts(access_token='tok-123'))
    assert env['CLAUDE_CODE_ENVIRONMENT_KIND'] == 'bridge'
    assert env['CLAUDE_CODE_SESSION_ACCESS_TOKEN'] == 'tok-123'
    assert env['CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2'] == '1'


def test_build_child_env_sets_sandbox_flag_when_enabled() -> None:
    env = build_child_env(
        {}, BuildChildEnvOpts(access_token='tok', sandbox=True),
    )
    assert env['CLAUDE_CODE_FORCE_SANDBOX'] == '1'


def test_build_child_env_omits_sandbox_when_disabled() -> None:
    env = build_child_env(
        {}, BuildChildEnvOpts(access_token='tok', sandbox=False),
    )
    assert 'CLAUDE_CODE_FORCE_SANDBOX' not in env


def test_build_child_env_sets_ccr_v2_with_epoch() -> None:
    env = build_child_env(
        {},
        BuildChildEnvOpts(
            access_token='tok', use_ccr_v2=True, worker_epoch=7,
        ),
    )
    assert env['CLAUDE_CODE_USE_CCR_V2'] == '1'
    assert env['CLAUDE_CODE_WORKER_EPOCH'] == '7'


def test_build_child_env_omits_ccr_v2_when_disabled() -> None:
    env = build_child_env(
        {}, BuildChildEnvOpts(access_token='tok', use_ccr_v2=False),
    )
    assert 'CLAUDE_CODE_USE_CCR_V2' not in env
    assert 'CLAUDE_CODE_WORKER_EPOCH' not in env


def test_build_child_env_overrides_access_token_from_parent() -> None:
    """Parent's CLAUDE_CODE_SESSION_ACCESS_TOKEN should NOT leak through;
    only the explicit opts.access_token wins.
    """
    # The env var IS allowlist-eligible because it starts with the
    # allowlisted prefix? No — let's verify by including it in parent.
    parent = {'CLAUDE_CODE_SESSION_ACCESS_TOKEN': 'parent-stale-tok'}
    env = build_child_env(
        parent, BuildChildEnvOpts(access_token='fresh-tok'),
    )
    assert env['CLAUDE_CODE_SESSION_ACCESS_TOKEN'] == 'fresh-tok'


def test_allowlist_contains_essential_vars() -> None:
    """Pin the allowlist contents — any removal is a behavior change."""
    must_have = {
        'PATH', 'HOME', 'TMPDIR', 'LANG',
        'NODE_OPTIONS', 'NODE_PATH', 'NODE_ENV',
        'CLAUDE_CODE_ENVIRONMENT_KIND',
        'CLAUDE_CODE_FORCE_SANDBOX',
        'TERM', 'COLORTERM',
    }
    for var in must_have:
        assert var in CHILD_ENV_ALLOWLIST


def test_allowlist_excludes_secrets() -> None:
    """Defensive — secret-bearing vars must never enter the allowlist."""
    must_not = {
        'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN',
        'CLAUDE_TRUSTED_DEVICE_TOKEN', 'GITHUB_TOKEN', 'AWS_SECRET_ACCESS_KEY',
    }
    for var in must_not:
        assert var not in CHILD_ENV_ALLOWLIST


# ── safe_filename_id ─────────────────────────────────────────────────────


def test_safe_filename_id_preserves_alphanumeric() -> None:
    assert safe_filename_id('abc123_XYZ-def') == 'abc123_XYZ-def'


@pytest.mark.parametrize('bad', [
    '../etc/passwd',
    'sess/with/slash',
    'sess.with.dot',
    'sess with space',
    'sess%encoded',
    'sess\nnewline',
])
def test_safe_filename_id_replaces_unsafe(bad: str) -> None:
    out = safe_filename_id(bad)
    # No unsafe chars survive.
    for ch in out:
        assert ch.isalnum() or ch in '_-'


def test_safe_filename_id_handles_empty_string() -> None:
    assert safe_filename_id('') == ''


# ── extract_activities ──────────────────────────────────────────────────


def test_extract_activities_returns_empty_for_non_json() -> None:
    logs: list[str] = []
    out = extract_activities('not json', 'sess-1', logs.append)
    assert out == []


def test_extract_activities_returns_empty_for_non_object_json() -> None:
    out = extract_activities('123', 'sess-1', lambda _msg: None)
    assert out == []


def test_extract_activities_emits_tool_start() -> None:
    line = json.dumps({
        'type': 'assistant',
        'message': {
            'content': [
                {
                    'type': 'tool_use',
                    'name': 'Read',
                    'input': {'file_path': '/tmp/foo.py'},
                },
            ],
        },
    })
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert len(out) == 1
    assert out[0].type == 'tool_start'
    assert out[0].summary == 'Reading /tmp/foo.py'


def test_extract_activities_uses_tool_name_when_verb_unknown() -> None:
    line = json.dumps({
        'type': 'assistant',
        'message': {
            'content': [
                {
                    'type': 'tool_use',
                    'name': 'CustomTool',
                    'input': {'pattern': 'foo'},
                },
            ],
        },
    })
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert out[0].summary == 'CustomTool foo'


def test_extract_activities_truncates_bash_command() -> None:
    line = json.dumps({
        'type': 'assistant',
        'message': {
            'content': [
                {
                    'type': 'tool_use',
                    'name': 'Bash',
                    'input': {'command': 'x' * 200},
                },
            ],
        },
    })
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    # Bash command truncated to 60 chars.
    assert out[0].summary == 'Running ' + 'x' * 60


def test_extract_activities_emits_text() -> None:
    line = json.dumps({
        'type': 'assistant',
        'message': {
            'content': [{'type': 'text', 'text': 'Hello world'}],
        },
    })
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert len(out) == 1
    assert out[0].type == 'text'
    assert out[0].summary == 'Hello world'


def test_extract_activities_truncates_text_to_80_chars() -> None:
    line = json.dumps({
        'type': 'assistant',
        'message': {
            'content': [{'type': 'text', 'text': 'a' * 200}],
        },
    })
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert len(out[0].summary) == 80


def test_extract_activities_skips_empty_text() -> None:
    line = json.dumps({
        'type': 'assistant',
        'message': {'content': [{'type': 'text', 'text': ''}]},
    })
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert out == []


def test_extract_activities_emits_result_success() -> None:
    line = json.dumps({'type': 'result', 'subtype': 'success'})
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert len(out) == 1
    assert out[0].type == 'result'


def test_extract_activities_emits_result_error_with_message() -> None:
    line = json.dumps({
        'type': 'result',
        'subtype': 'failure',
        'errors': ['Something bad'],
    })
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert len(out) == 1
    assert out[0].type == 'error'
    assert out[0].summary == 'Something bad'


def test_extract_activities_emits_result_error_with_fallback() -> None:
    line = json.dumps({'type': 'result', 'subtype': 'failure'})
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert out[0].summary == 'Error: failure'


def test_extract_activities_ignores_unknown_types() -> None:
    line = json.dumps({'type': 'something-else'})
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert out == []


def test_extract_activities_multiple_blocks() -> None:
    """Multiple content blocks → multiple activities, in order."""
    line = json.dumps({
        'type': 'assistant',
        'message': {
            'content': [
                {'type': 'text', 'text': 'thinking...'},
                {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}},
            ],
        },
    })
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    assert [a.type for a in out] == ['text', 'tool_start']


# ── Integration: spawn a small Python child ─────────────────────────────


_CHILD_SCRIPT_HELLO = (
    'import sys, json, time\n'
    'sys.stdout.write(json.dumps({"type": "assistant", "message": '
    '{"content": [{"type": "text", "text": "hi from child"}]}}) + "\\n")\n'
    'sys.stdout.flush()\n'
    'sys.stdout.write(json.dumps({"type": "result", "subtype": "success"}) + "\\n")\n'
    'sys.stdout.flush()\n'
    'sys.exit(0)\n'
)


def _make_spawner(
    script_source: str = _CHILD_SCRIPT_HELLO,
    on_activity: Callable[[str, Any], None] | None = None,
    on_permission_request: (
        Callable[[str, PermissionRequest, str], None] | None
    ) = None,
    verbose: bool = False,
) -> SessionSpawnerDeps:
    """Build a SessionSpawnerDeps that runs the supplied Python script
    as the child process via ``sys.executable -c <script>``."""
    return SessionSpawnerDeps(
        exec_path=sys.executable,
        script_args=['-c', script_source],
        env={'PATH': os.environ.get('PATH', '')},
        verbose=verbose,
        sandbox=False,
        on_debug=lambda _msg: None,  # silent
        on_activity=on_activity,
        on_permission_request=on_permission_request,
    )


def _make_opts(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        'session_id': 'cse_test123',
        'sdk_url': 'wss://example.com/v2/session_ingress/ws/cse_test123',
        'access_token': 'tok-test',
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_spawn_runs_child_to_completion() -> None:
    """The child runs to exit(0); ``wait_done`` resolves to 'completed'."""
    # The child script ignores the bridge CLI flags (which we still pass
    # via _build_args). Python's `-c` consumes the rest as sys.argv.
    deps = _make_spawner()
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    status = await asyncio.wait_for(handle.wait_done(), timeout=10.0)
    assert status == 'completed'


@pytest.mark.asyncio
async def test_spawn_parses_activities_from_stdout() -> None:
    """Activities appear in the ring buffer in arrival order."""
    captured: list[tuple[str, Any]] = []
    deps = _make_spawner(on_activity=lambda sid, a: captured.append((sid, a)))
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    await asyncio.wait_for(handle.wait_done(), timeout=10.0)

    types = [a.type for _sid, a in captured]
    assert 'text' in types
    assert 'result' in types
    # Ring buffer also has them.
    activity_types = [a.type for a in handle.activities]
    assert 'text' in activity_types
    assert 'result' in activity_types


@pytest.mark.asyncio
async def test_spawn_fails_status_on_nonzero_exit() -> None:
    script = (
        'import sys\n'
        'sys.stderr.write("boom\\n")\n'
        'sys.exit(2)\n'
    )
    deps = _make_spawner(script_source=script)
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    status = await asyncio.wait_for(handle.wait_done(), timeout=10.0)
    assert status == 'failed'
    # Stderr captured.
    assert any('boom' in line for line in handle.last_stderr)


@pytest.mark.asyncio
async def test_spawn_handles_invalid_executable() -> None:
    """A non-existent exec path resolves to 'failed' without raising."""
    deps = SessionSpawnerDeps(
        exec_path='/no/such/binary/that/exists',
        env={'PATH': ''},
        on_debug=lambda _msg: None,
    )
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    status = await asyncio.wait_for(handle.wait_done(), timeout=5.0)
    assert status == 'failed'


@pytest.mark.asyncio
async def test_kill_interrupts_long_running_child() -> None:
    """SIGTERM is delivered as 'interrupted'."""
    script = (
        'import time, sys\n'
        'sys.stdout.write("ready\\n")\n'
        'sys.stdout.flush()\n'
        'time.sleep(30)\n'
    )
    deps = _make_spawner(script_source=script)
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    # Give the child a moment to start.
    await asyncio.sleep(0.2)
    handle.kill()  # type: ignore[attr-defined]
    status = await asyncio.wait_for(handle.wait_done(), timeout=10.0)
    assert status == 'interrupted'


@pytest.mark.asyncio
async def test_force_kill_terminates_unresponsive_child() -> None:
    """SIGKILL terminates a child ignoring SIGTERM."""
    if sys.platform == 'win32':
        pytest.skip('SIGKILL semantics differ on Windows')
    script = (
        'import signal, time, sys\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        'sys.stdout.write("ignoring SIGTERM\\n")\n'
        'sys.stdout.flush()\n'
        'time.sleep(30)\n'
    )
    deps = _make_spawner(script_source=script)
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    await asyncio.sleep(0.2)
    handle.kill()  # type: ignore[attr-defined]
    # SIGTERM ignored — give it a brief moment to NOT exit, then SIGKILL.
    await asyncio.sleep(0.2)
    handle.force_kill()  # type: ignore[attr-defined]
    status = await asyncio.wait_for(handle.wait_done(), timeout=10.0)
    assert status == 'failed'  # SIGKILL → returncode = -9, not -SIGTERM/SIGINT


@pytest.mark.asyncio
async def test_force_kill_is_idempotent() -> None:
    """Calling force_kill twice is a no-op the second time."""
    if sys.platform == 'win32':
        pytest.skip('SIGKILL semantics differ on Windows')
    script = 'import time; time.sleep(30)\n'
    deps = _make_spawner(script_source=script)
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    await asyncio.sleep(0.2)
    handle.force_kill()  # type: ignore[attr-defined]
    # Second call must not raise.
    handle.force_kill()  # type: ignore[attr-defined]
    await asyncio.wait_for(handle.wait_done(), timeout=5.0)


@pytest.mark.asyncio
async def test_update_access_token_writes_via_stdin() -> None:
    """The fresh token is delivered as an NDJSON line on stdin."""
    # Child echoes its stdin to stdout so we can verify the payload.
    script = (
        'import sys\n'
        'for line in sys.stdin:\n'
        '    sys.stdout.write("ECHO " + line)\n'
        '    sys.stdout.flush()\n'
        '    if "stop" in line:\n'
        '        break\n'
        'sys.exit(0)\n'
    )
    captured_lines: list[str] = []

    def on_activity(_sid: str, _a: Any) -> None:
        pass

    deps = SessionSpawnerDeps(
        exec_path=sys.executable,
        script_args=['-c', script],
        env={'PATH': os.environ.get('PATH', '')},
        on_debug=lambda msg: captured_lines.append(msg),
        on_activity=on_activity,
    )
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    # Wait for the child to be ready to read stdin.
    await asyncio.sleep(0.3)

    handle.update_access_token('fresh-tok-XYZ')  # type: ignore[attr-defined]
    # Tell it to stop.
    handle.write_stdin('stop\n')  # type: ignore[attr-defined]
    status = await asyncio.wait_for(handle.wait_done(), timeout=5.0)
    assert status == 'completed'

    # Property reflects the new token.
    assert handle.access_token == 'fresh-tok-XYZ'
    # The debug log captured the stdin write that includes the token.
    joined = '\n'.join(captured_lines)
    assert 'fresh-tok-XYZ' in joined
    assert 'update_environment_variables' in joined


@pytest.mark.asyncio
async def test_permission_request_fires_callback() -> None:
    """A ``control_request`` NDJSON line on stdout triggers the callback."""
    script = (
        'import sys, json\n'
        'sys.stdout.write(json.dumps({'
        '"type": "control_request",'
        '"request_id": "req-1",'
        '"request": {'
        '"subtype": "can_use_tool",'
        '"tool_name": "Bash",'
        '"input": {"command": "ls"},'
        '"tool_use_id": "tu-1"'
        '}}) + "\\n")\n'
        'sys.stdout.flush()\n'
        'sys.stdout.write(json.dumps({"type": "result", "subtype": "success"}) + "\\n")\n'
        'sys.stdout.flush()\n'
    )
    requests: list[tuple[str, dict[str, Any], str]] = []

    def on_perm(sid: str, req: PermissionRequest, tok: str) -> None:
        requests.append((sid, dict(req), tok))

    deps = _make_spawner(script_source=script, on_permission_request=on_perm)
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    await asyncio.wait_for(handle.wait_done(), timeout=10.0)

    assert len(requests) == 1
    sid, req, tok = requests[0]
    assert sid == 'cse_test123'
    assert tok == 'tok-test'
    assert req['request_id'] == 'req-1'
    assert req['request']['subtype'] == 'can_use_tool'


@pytest.mark.asyncio
async def test_first_user_message_callback_fires_once() -> None:
    """``on_first_user_message`` fires on the first real user message only."""
    script = (
        'import sys, json\n'
        # Synthetic — should be skipped.
        'sys.stdout.write(json.dumps({"type": "user", "isSynthetic": True, '
        '"message": {"content": "synthetic"}}) + "\\n")\n'
        'sys.stdout.flush()\n'
        # Real — should fire.
        'sys.stdout.write(json.dumps({"type": "user", '
        '"message": {"content": "first real prompt"}}) + "\\n")\n'
        'sys.stdout.flush()\n'
        # Second real — should NOT fire (already seen).
        'sys.stdout.write(json.dumps({"type": "user", '
        '"message": {"content": "second"}}) + "\\n")\n'
        'sys.stdout.flush()\n'
    )
    calls: list[str] = []
    deps = _make_spawner(script_source=script)
    spawner = create_session_spawner(deps)
    opts = _make_opts(on_first_user_message=lambda txt: calls.append(txt))
    handle = spawner.spawn(opts, os.getcwd())
    # Give the script enough time to write all three lines.
    await asyncio.sleep(0.5)
    handle.force_kill()  # type: ignore[attr-defined]
    await asyncio.wait_for(handle.wait_done(), timeout=5.0)

    assert calls == ['first real prompt']


@pytest.mark.asyncio
async def test_kill_before_spawn_ready_still_terminates_child() -> None:
    """Regression test per CRITIC MAJOR-2.

    Calling ``kill()`` immediately after ``spawn()`` (before the async
    subprocess-creation has returned) must still terminate the child.
    Pre-fix, the kill silently no-op'd because ``_process`` was still
    None.
    """
    script = (
        'import time, sys\n'
        'sys.stdout.write("ready\\n")\n'
        'sys.stdout.flush()\n'
        'time.sleep(30)\n'
    )
    deps = _make_spawner(script_source=script)
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    # Call kill() in the same event-loop tick as spawn() returned —
    # _process may still be None.
    handle.kill()  # type: ignore[attr-defined]
    status = await asyncio.wait_for(handle.wait_done(), timeout=10.0)
    assert status == 'interrupted'


def test_session_activity_timestamp_is_milliseconds() -> None:
    """Regression test per CRITIC BLOCKING-1.

    ``SessionActivity.timestamp`` must be in milliseconds since the
    Unix epoch (matching TS ``Date.now()``), not seconds. A current
    timestamp should be ≈ 1.7×10^12 (around year 2024+) not 1.7×10^9.
    """
    import time as _time

    line = json.dumps({
        'type': 'assistant',
        'message': {'content': [{'type': 'text', 'text': 'now'}]},
    })
    before_ms = int(_time.time() * 1000)
    out = extract_activities(line, 'sess-1', lambda _msg: None)
    after_ms = int(_time.time() * 1000)
    assert len(out) == 1
    ts = out[0].timestamp
    assert before_ms <= ts <= after_ms
    # Sanity: a current-epoch value in ms is at least 10^12.
    assert ts >= 10**12


@pytest.mark.asyncio
async def test_activity_ring_buffer_caps_at_max() -> None:
    """More than ``MAX_ACTIVITIES`` activities → oldest evicted."""
    # Emit MAX_ACTIVITIES + 5 text blocks.
    lines = []
    for i in range(MAX_ACTIVITIES + 5):
        lines.append(json.dumps({
            'type': 'assistant',
            'message': {'content': [{'type': 'text', 'text': f't{i}'}]},
        }))
    script = (
        'import sys\n'
        f'lines = {lines!r}\n'
        'for ln in lines:\n'
        '    sys.stdout.write(ln + "\\n")\n'
        'sys.stdout.flush()\n'
    )
    deps = _make_spawner(script_source=script)
    spawner = create_session_spawner(deps)
    handle = spawner.spawn(_make_opts(), os.getcwd())
    await asyncio.wait_for(handle.wait_done(), timeout=10.0)

    assert len(handle.activities) == MAX_ACTIVITIES
    # The first 5 should have been evicted; last 10 remain.
    summaries = [a.summary for a in handle.activities]
    assert 't0' not in summaries
    assert f't{MAX_ACTIVITIES + 4}' in summaries
