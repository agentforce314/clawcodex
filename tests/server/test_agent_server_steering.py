"""Steering crosses the real worker/query boundary without ending its turn."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from src.server.agent_server import AgentServerConfig
from tests.server.test_agent_server_e2e import _TextProvider, _wait_for
from tests.server.test_agent_server_workflows import _control, _session_of, _spawned

pytestmark = pytest.mark.integration


def held_provider():
    started, release = threading.Event(), threading.Event()
    calls = []

    class Provider(_TextProvider):
        def chat(self, messages, **kwargs):
            calls.append(str(messages))
            if len(calls) == 1:
                started.set()
                assert release.wait(15), "test did not release the model request"
            return super().chat(messages, **kwargs)

    return Provider, started, release, calls


async def result_frames(gen):
    frames = []
    while True:
        frame = await asyncio.wait_for(gen.__anext__(), timeout=10)
        frames.append(frame)
        if frame.get("type") == "result":
            return frames


async def test_steer_during_model_request_continues_same_turn_and_persists(tmp_path):
    provider, started, release, calls = held_provider()
    async with _spawned(tmp_path, provider) as (handle, gen):
        await handle.send_to_agent({"type": "user", "message": {"content": "original task"}})
        try:
            assert await _wait_for(started.is_set)
            reply = await _control(handle, gen, "steer-1", {"subtype": "steer", "text": "  also check complex functions  "})
            assert reply == {"status": "queued"}
            assert len(calls) == 1  # no second query while the current request is held
        finally:
            release.set()
        frames = await result_frames(gen)
        assert frames[-1]["subtype"] == "success"
        assert frames[-1]["num_turns"] == 2
        assert len(calls) == 2
        assert "original task" in calls[1]
        assert "also check complex functions" in calls[1]
        assert "also check complex functions" not in calls[0]
        assert sum(frame.get("subtype") == "steer" for frame in frames) == 1
        session = _session_of(handle)
        assert session._stats_turns == 1
        assert any(message.role == "user" and message.content == "also check complex functions"
                   for message in session.session.conversation.messages)
        assert await _control(handle, gen, "late", {"subtype": "steer", "text": "too late"}) == {
            "status": "rejected", "reason": "not_running",
        }


async def test_steer_honors_user_prompt_hooks_and_rejects_marker_payloads(tmp_path, monkeypatch):
    provider, started, release, calls = held_provider()
    async with _spawned(tmp_path, provider) as (handle, gen):
        await handle.send_to_agent({"type": "user", "message": {"content": "original"}})
        try:
            assert await _wait_for(started.is_set)
            session = _session_of(handle)
            monkeypatch.setattr(session, "_run_user_prompt_submit_hooks", lambda text: SimpleNamespace(
                blocked=True, prevented=False, block_message="blocked by validation",
            ))
            reply = await _control(handle, gen, "hook", {"subtype": "steer", "text": "blocked input"})
            assert reply == {"status": "rejected", "error": "blocked by validation"}
            for text in (" ", "/plan do it", "[Image #1] inspect", "[File #2] read"):
                reply = await _control(handle, gen, "invalid", {"subtype": "steer", "text": text})
                assert reply["status"] == "rejected"
        finally:
            release.set()
        await result_frames(gen)
        assert len(calls) == 1


async def test_stop_returns_unread_input_without_starting_another_turn(tmp_path):
    provider, started, release, calls = held_provider()
    async with _spawned(tmp_path, provider) as (handle, gen):
        await handle.send_to_agent({"type": "user", "message": {"content": "original"}})
        try:
            assert await _wait_for(started.is_set)
            assert (await _control(handle, gen, "steer", {"subtype": "steer", "text": "keep this"}))["status"] == "queued"
            await handle.send_to_agent({"type": "control_request", "request_id": "stop", "request": {"subtype": "interrupt"}})
        finally:
            release.set()
        frames = []
        while not (any(frame.get("type") == "result" for frame in frames)
                   and any(frame.get("subtype") == "steer_failed" for frame in frames)):
            frames.append(await asyncio.wait_for(gen.__anext__(), timeout=10))
        returned = next(frame for frame in frames if frame.get("subtype") == "steer_failed")
        assert returned["text"] == "keep this"
        assert len(calls) == 1
        assert _session_of(handle)._inbox.empty()


async def test_step_limit_returns_unread_input_instead_of_exceeding_the_limit(tmp_path):
    provider, started, release, calls = held_provider()
    async with _spawned(tmp_path, provider, AgentServerConfig(max_turns=1)) as (handle, gen):
        await handle.send_to_agent({"type": "user", "message": {"content": "original"}})
        try:
            assert await _wait_for(started.is_set)
            assert (await _control(handle, gen, "steer", {"subtype": "steer", "text": "keep this"}))["status"] == "queued"
        finally:
            release.set()
        frames = await result_frames(gen)
        assert len(calls) == 1
        assert any(frame.get("subtype") == "steer_failed" and frame.get("text") == "keep this" for frame in frames)
