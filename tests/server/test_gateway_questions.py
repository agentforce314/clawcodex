"""AskUserQuestion over the desktop/web gateway.

The gateway auto-denies every ask subtype it has no surface for, and the agent
server substitutes a non-interactive answer for AskUserQuestion on the
multi-session transport — both deliberately, because a client that ignored a
question would park the session's worker thread until the ask timeout. These
cover the negotiation that lets a client which *does* render questions opt in,
and the shape of what crosses the wire once it has.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

# Evaluated at import on EVERY platform, so it must not touch a POSIX-only
# name directly: pytest reads each skipif condition even when another matched.
IS_ROOT = getattr(os, "geteuid", lambda: 1)() == 0

from src.server.desktop_gateway_methods import (
    DesktopSession,
    _wants_questions,
    question_request_payload,
)


class _Agent:
    """Records what the session sends back to the agent process."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_to_agent(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    @property
    def replies(self) -> list[dict[str, Any]]:
        return [m["response"] for m in self.sent if m.get("type") == "control_response"]


def _session() -> tuple[DesktopSession, _Agent, list[tuple[str, Any]]]:
    session = DesktopSession.__new__(DesktopSession)
    session.session_id = "s1"
    session._pending_asks = {}
    session._last_ask_id = None
    session._pending_question = None
    session.asks_questions = False
    agent = _Agent()
    session.agent = agent
    broadcasts: list[tuple[str, Any]] = []

    async def _broadcast(type_: str, payload: Any) -> None:
        broadcasts.append((type_, payload))

    session._broadcast = _broadcast  # type: ignore[method-assign]
    return session, agent, broadcasts


def _ask(**questions: Any) -> dict[str, Any]:
    return {
        "request_id": "ask-1",
        "request": {"subtype": "ask_user_question", **questions},
    }


# ── capability declaration ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"capabilities": {"ask_user_question": True}}, True),
        ({"capabilities": ["ask_user_question"]}, True),
        ({"capabilities": {"ask_user_question": False}}, False),
        ({"capabilities": {}}, False),
        ({"capabilities": None}, False),
        ({}, False),
        # A truthy non-True value is not a declaration; the check is `is True`
        # so a stray string cannot switch the agent into blocking on a human.
        ({"capabilities": {"ask_user_question": "yes"}}, False),
    ],
)
def test_wants_questions(params: dict[str, Any], expected: bool) -> None:
    assert _wants_questions(params) is expected


# ── payload shaping ───────────────────────────────────────────────────────────


def test_payload_carries_question_text_verbatim() -> None:
    # The text is the agent's answer KEY -- the tool drops any answer whose key
    # it did not ask about -- so it must survive the trip unchanged.
    payload = question_request_payload(
        "r1",
        {"questions": [{"question": "Which colour?  ", "options": [{"label": "Red"}]}]},
    )

    assert payload is not None
    assert payload["questions"][0]["question"] == "Which colour?  "
    assert payload["request_id"] == "r1"


def test_payload_keeps_header_options_and_multiselect() -> None:
    payload = question_request_payload(
        "r1",
        {
            "questions": [
                {
                    "question": "Which files?",
                    "header": "Scope",
                    "multiSelect": True,
                    "options": [
                        {"label": "a.py", "description": "the module"},
                        {"label": "b.py"},
                    ],
                }
            ]
        },
    )

    assert payload is not None
    assert payload["questions"][0] == {
        "question": "Which files?",
        "header": "Scope",
        "multi_select": True,
        "options": [{"label": "a.py", "description": "the module"}, {"label": "b.py"}],
    }


def test_payload_allows_a_question_with_no_options() -> None:
    # An open question is legitimate: the composer offers a text field for it.
    payload = question_request_payload("r1", {"questions": [{"question": "Name it?"}]})

    assert payload is not None
    assert payload["questions"][0]["options"] == []


@pytest.mark.parametrize(
    "request_",
    [
        {},
        {"questions": []},
        {"questions": [{"question": ""}]},
        {"questions": [{"question": "   "}]},
        {"questions": [{"question": 42}]},
        {"questions": ["not a dict"]},
    ],
)
def test_payload_is_none_when_there_is_nothing_to_render(request_: dict[str, Any]) -> None:
    # Falling through to the decline is better than seating an empty takeover
    # the user cannot answer and cannot dismiss.
    assert question_request_payload("r1", request_) is None


def test_payload_drops_a_malformed_option_but_keeps_the_question() -> None:
    payload = question_request_payload(
        "r1",
        {"questions": [{"question": "Pick", "options": [{"label": "ok"}, {"nope": 1}, "x"]}]},
    )

    assert payload is not None
    assert payload["questions"][0]["options"] == [{"label": "ok"}]


# ── routing ───────────────────────────────────────────────────────────────────


def test_route_denies_a_question_when_the_client_never_declared_support() -> None:
    # Preserves today's behavior for a client with no question surface.
    session, agent, broadcasts = _session()

    asyncio.run(session._route_ask(_ask(questions=[{"question": "Which colour?"}])))

    assert [t for t, _ in broadcasts] == []
    assert agent.replies[0]["response"]["behavior"] == "deny"
    assert session._pending_question is None


def test_route_broadcasts_a_question_once_the_client_has_declared_support() -> None:
    session, agent, broadcasts = _session()
    session.asks_questions = True

    asyncio.run(session._route_ask(_ask(questions=[{"question": "Which colour?"}])))

    assert [t for t, _ in broadcasts] == ["question.request"]
    assert broadcasts[0][1]["questions"][0]["question"] == "Which colour?"
    assert session._pending_question == "ask-1"
    assert agent.replies == []


def test_route_declines_an_unrenderable_question_even_when_capable() -> None:
    session, agent, broadcasts = _session()
    session.asks_questions = True

    asyncio.run(session._route_ask(_ask(questions=[])))

    assert broadcasts == []
    assert agent.replies[0]["response"]["behavior"] == "deny"


def test_a_question_does_not_occupy_the_approval_slot() -> None:
    # One slot for both would let a stray approval click answer a question --
    # an "allow" is not a submit, so the user's question would silently come
    # back to the agent as a decline.
    session, agent, _broadcasts = _session()
    session.asks_questions = True

    asyncio.run(session._route_ask(_ask(questions=[{"question": "Which colour?"}])))

    assert session._last_ask_id is None
    assert session._pending_question == "ask-1"

    assert asyncio.run(session.respond_approval("allow")) == {"resolved": False}
    assert agent.replies == []
    # …and the question is still answerable.
    assert asyncio.run(session.respond_question("submit", {"Which colour?": "Red"})) == {
        "resolved": True
    }


# ── responding ────────────────────────────────────────────────────────────────


def _park(session: DesktopSession) -> None:
    session.asks_questions = True
    asyncio.run(session._route_ask(_ask(questions=[{"question": "Which colour?"}])))


def test_submit_forwards_the_answers_under_the_submit_action() -> None:
    session, agent, _ = _session()
    _park(session)

    result = asyncio.run(session.respond_question("submit", {"Which colour?": "Red"}))

    assert result == {"resolved": True}
    assert agent.replies[0] == {
        "request_id": "ask-1",
        "response": {"action": "submit", "answers": {"Which colour?": "Red"}},
    }


def test_submit_with_no_answers_is_still_a_submit() -> None:
    # Skipping every question is "the user submitted nothing", which the tool
    # reports differently from a decline.
    session, agent, _ = _session()
    _park(session)

    asyncio.run(session.respond_question("submit", {}))

    assert agent.replies[0]["response"] == {"action": "submit", "answers": {}}


def test_decline_is_not_a_submit() -> None:
    session, agent, _ = _session()
    _park(session)

    asyncio.run(session.respond_question("decline", {"Which colour?": "Red"}))

    # Anything that is not action=="submit" reads as a decline on the far side;
    # the answers are dropped rather than smuggled through.
    assert agent.replies[0]["response"] == {"action": "decline"}


def test_non_string_answers_are_dropped_not_coerced_into_prose() -> None:
    # Answer values land in text the model reads as the user's own words, so a
    # structure that is not a string has no business being stringified into it.
    session, agent, _ = _session()
    _park(session)

    asyncio.run(
        session.respond_question(
            "submit", {"Which colour?": "Red", "other": {"nested": 1}, 7: "x"}
        )
    )

    assert agent.replies[0]["response"]["answers"] == {"Which colour?": "Red"}


def test_answering_twice_resolves_only_once() -> None:
    session, agent, _ = _session()
    _park(session)

    assert asyncio.run(session.respond_question("submit", {})) == {"resolved": True}
    assert asyncio.run(session.respond_question("submit", {})) == {"resolved": False}
    assert len(agent.replies) == 1


def test_answering_with_nothing_pending_is_reported_not_replied_to() -> None:
    session, agent, _ = _session()

    assert asyncio.run(session.respond_question("submit", {"q": "a"})) == {"resolved": False}
    assert agent.replies == []


# ── effort options ────────────────────────────────────────────────────────────
#
# The ladder is a property of the MODEL: `supported: false` means the model
# takes no effort parameter, and the client is expected to show no control
# rather than a list it cannot apply. Every failure path therefore has to
# report "unsupported", never an empty-but-supported ladder.


class _Queryable:
    """A session stub that answers one control query with a canned reply."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.asked: tuple[str, dict[str, Any]] | None = None

    async def control_query(self, subtype: str, params: dict[str, Any]) -> Any:
        self.asked = (subtype, params)
        return self.reply


def _effort(reply: Any, *, live: bool = True) -> dict[str, Any]:
    from src.server.desktop_gateway_methods import GatewayConnection

    connection = GatewayConnection.__new__(GatewayConnection)
    session = _Queryable(reply) if live else None
    connection._first_session = lambda params: session  # type: ignore[method-assign]
    return asyncio.run(GatewayConnection.effort_options(connection, {}))


def test_effort_reports_unsupported_with_no_live_session() -> None:
    # Nothing to ask and nowhere to apply a level; claiming a ladder would
    # offer a control that silently does nothing.
    assert _effort(None, live=False) == {"supported": False, "levels": [], "current": ""}


def test_effort_passes_through_the_model_ladder() -> None:
    result = _effort(
        {
            "ok": True,
            "supported": True,
            "levels": ["low", "high"],
            "current": "high",
            "model": "m",
            "provider": "p",
        }
    )

    assert result == {
        "current": "high",
        "levels": ["low", "high"],
        "model": "m",
        "provider": "p",
        "supported": True,
    }


def test_effort_reports_unsupported_when_the_model_takes_no_parameter() -> None:
    assert _effort({"ok": True, "supported": False, "levels": []})["supported"] is False


def test_effort_reports_unsupported_when_the_control_fails() -> None:
    # A picker built on an error reply would list nothing and still render.
    assert _effort({"ok": False, "error": "boom"})["supported"] is False
    assert _effort(None)["supported"] is False


def test_effort_never_claims_support_on_a_truthy_non_true_flag() -> None:
    assert _effort({"ok": True, "supported": "yes", "levels": ["low"]})["supported"] is False


# ── workspace file listing (@ mentions) ───────────────────────────────────────


def _tree(root, spec: dict) -> None:
    """Build a directory tree from a nested dict; str values are file bodies."""
    for name, value in spec.items():
        path = root / name
        if isinstance(value, dict):
            path.mkdir(parents=True, exist_ok=True)
            _tree(path, value)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)


def test_walk_lists_files_relative_and_posix(tmp_path) -> None:
    from src.server.desktop_gateway_methods import _walk_workspace_files

    _tree(tmp_path, {"a.py": "", "src": {"b.py": "", "deep": {"c.py": ""}}})

    files, truncated = _walk_workspace_files(str(tmp_path))

    assert sorted(files) == ["a.py", "src/b.py", "src/deep/c.py"]
    assert truncated is False


def test_walk_skips_noise_directories_and_dotfiles(tmp_path) -> None:
    from src.server.desktop_gateway_methods import _walk_workspace_files

    _tree(tmp_path, {
        "keep.py": "",
        ".hidden": "",
        ".git": {"config": ""},
        "node_modules": {"pkg": {"index.js": ""}},
        "__pycache__": {"x.pyc": ""},
    })

    assert _walk_workspace_files(str(tmp_path))[0] == ["keep.py"]


def test_walk_is_breadth_first_so_truncation_drops_the_deepest(tmp_path, monkeypatch) -> None:
    # The bug this ordering exists for: depth-first spent the whole cap on
    # whichever subtree sorted first, and the obvious query returned nothing
    # because its directory was never reached.
    import src.server.desktop_gateway_methods as mod

    monkeypatch.setattr(mod, "_WALK_MAX_FILES", 2)
    _tree(tmp_path, {
        "aaa": {"deep": {"deeper": {"buried.py": ""}}, "one.py": ""},
        "zzz": {"two.py": ""},
    })

    files, truncated = mod._walk_workspace_files(str(tmp_path))

    assert truncated is True
    # Both top-level directories are represented before anything goes deep.
    assert "aaa/one.py" in files
    assert "zzz/two.py" in files
    assert "aaa/deep/deeper/buried.py" not in files


def test_walk_survives_an_unreadable_directory(tmp_path) -> None:
    import os

    from src.server.desktop_gateway_methods import _walk_workspace_files

    if IS_ROOT:
        pytest.skip("root reads every directory regardless of mode")

    _tree(tmp_path, {"readable.py": "", "locked": {"hidden.py": ""}})
    os.chmod(tmp_path / "locked", 0o000)
    try:
        files, _ = _walk_workspace_files(str(tmp_path))
    finally:
        os.chmod(tmp_path / "locked", 0o755)

    # The rest of the tree is still a useful list.
    assert files == ["readable.py"]


def test_cache_falls_back_to_the_walk_when_ripgrep_is_missing(tmp_path, monkeypatch) -> None:
    import src.server.desktop_gateway_methods as mod

    def _no_rg(cwd: str) -> tuple[list[str], bool]:
        raise RuntimeError("ripgrep (rg) is required for file search but could not be found")

    monkeypatch.setattr("src.services.workspace_search.list_workspace_files", _no_rg)
    monkeypatch.setattr(mod, "_file_cache", {})
    _tree(tmp_path, {"kept.py": ""})

    assert mod._workspace_files_cached(str(tmp_path))[0] == ["kept.py"]


def test_cache_does_not_swallow_a_real_listing_failure(tmp_path, monkeypatch) -> None:
    # An unreadable workspace is a real answer the caller reports; papering
    # over it with a partial list would look like an empty repository.
    import src.server.desktop_gateway_methods as mod

    def _boom(cwd: str) -> tuple[list[str], bool]:
        raise PermissionError("nope")

    monkeypatch.setattr("src.services.workspace_search.list_workspace_files", _boom)
    monkeypatch.setattr(mod, "_file_cache", {})

    with pytest.raises(PermissionError):
        mod._workspace_files_cached(str(tmp_path))


# ── rewind (retry) ────────────────────────────────────────────────────────────


def _rewind(reply: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    from src.server.desktop_gateway_methods import GatewayConnection

    connection = GatewayConnection.__new__(GatewayConnection)
    session = _Queryable(reply)
    connection._session = lambda p: session  # type: ignore[method-assign]
    result = asyncio.run(GatewayConnection.session_rewind(connection, params or {}))
    return {**result, "_asked": session.asked}


def test_rewind_defaults_to_one_turn() -> None:
    result = _rewind({"ok": True, "removed": 4, "count": 2})

    assert result["_asked"] == ("rewind", {"turns": 1})
    assert result["removed"] == 4
    assert result["ok"] is True


@pytest.mark.parametrize(
    "given,expected",
    [({"turns": 3}, 3), ({"turns": 0}, 1), ({"turns": -2}, 1), ({"turns": "x"}, 1), ({}, 1)],
)
def test_rewind_clamps_the_turn_count(given: dict[str, Any], expected: int) -> None:
    assert _rewind({"ok": True}, given)["_asked"][1] == {"turns": expected}


def test_rewind_passes_the_refusal_through() -> None:
    # The agent refuses mid-turn because it mutates the conversation the worker
    # is reading. A retry that silently did nothing would look like the model
    # ignoring the click.
    result = _rewind({"ok": False, "error": "cannot rewind during an active turn"})

    assert result["ok"] is False
    assert result["error"] == "cannot rewind during an active turn"


def test_rewind_reports_a_session_that_did_not_answer() -> None:
    assert _rewind(None)["ok"] is False


# ── auto session title ────────────────────────────────────────────────────────


def _titling_session(tmp_path, titled: bool = False):
    """A DesktopSession wired just enough to auto-title."""
    from src.server.desktop_gateway_methods import DesktopSession

    session = DesktopSession.__new__(DesktopSession)
    session.session_id = "s1"
    session.titled = titled
    session._background = set()
    renames: list[dict[str, Any]] = []
    events: list[tuple[str, Any]] = []

    async def _control_query(subtype: str, params: dict[str, Any]) -> Any:
        renames.append({"subtype": subtype, **params})
        return {"ok": True, "name": params.get("name")}

    async def _broadcast(type_: str, payload: Any = None) -> None:
        events.append((type_, payload))

    class _State:
        def saved_sessions_dir(self) -> str:
            return str(tmp_path)

    session.control_query = _control_query  # type: ignore[method-assign]
    session._broadcast = _broadcast  # type: ignore[method-assign]
    session.state = _State()  # type: ignore[assignment]
    return session, renames, events


def test_auto_title_names_an_untitled_session_after_its_first_prompt(tmp_path) -> None:
    session, renames, events = _titling_session(tmp_path)

    title = asyncio.run(session.auto_title("please add a retry button to the composer"))

    # The heuristic strips the throat-clearing and capitalizes.
    assert title == "Add a retry button to the composer"
    assert renames[0]["name"] == title
    assert ("session.title", {"title": title}) in events


def test_auto_title_leaves_a_named_session_alone(tmp_path) -> None:
    # A resumed session carries the user's own title, and an explicit rename
    # is a decision auto-titling must not undo.
    session, renames, events = _titling_session(tmp_path, titled=True)

    assert asyncio.run(session.auto_title("something else entirely")) is None
    assert renames == []
    assert events == []


def test_auto_title_happens_once_even_if_two_prompts_race(tmp_path) -> None:
    # The flag is set BEFORE the rename round-trip, so a second prompt arriving
    # while the first is in flight cannot start a second rename.
    session, renames, _ = _titling_session(tmp_path)

    asyncio.run(session.auto_title("first prompt"))
    asyncio.run(session.auto_title("second prompt"))

    assert len(renames) == 1


def test_auto_title_skips_a_prompt_it_cannot_name(tmp_path) -> None:
    # "Untitled session" is the heuristic's way of saying it has nothing; it
    # would be a worse title than the blank it replaces.
    session, renames, _ = _titling_session(tmp_path)

    assert asyncio.run(session.auto_title("   ")) is None
    assert renames == []
    # …and the session stays open to being named by a later prompt.
    assert session.titled is False


def test_auto_title_survives_a_saved_file_it_cannot_stamp(tmp_path) -> None:
    # The runtime rename is the part that matters; a file sync failure must not
    # lose the title the client is about to be told about.
    session, _renames, events = _titling_session(tmp_path / "does-not-exist")

    title = asyncio.run(session.auto_title("run the tests"))

    assert title == "Run the tests"
    assert ("session.title", {"title": title}) in events


# ── image attach ──────────────────────────────────────────────────────────────
#
# The agent's attach_image control reads a path off the machine it runs on,
# which a browser cannot produce. The gateway lands the bytes in a temp file so
# the agent side stays exactly as the TUI uses it.


def _attach(
    reply: Any, params: dict[str, Any], model: str = "claude-sonnet-4-6"
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from src.server.desktop_gateway_methods import GatewayConnection

    seen: list[dict[str, Any]] = []

    class _Session:
        init_info = {"model": model}

        async def control_query(self, subtype: str, args: dict[str, Any]) -> Any:
            # Record what the control actually saw, contents included: the file
            # is gone by the time the caller could look.
            path = args.get("path")
            body = b""
            if isinstance(path, str) and os.path.exists(path):
                with open(path, "rb") as fh:
                    body = fh.read()
            seen.append({"subtype": subtype, **args, "_body": body, "_existed": bool(body)})
            return reply

    connection = GatewayConnection.__new__(GatewayConnection)
    connection._session = lambda p: _Session()  # type: ignore[method-assign]
    return asyncio.run(GatewayConnection.image_attach(connection, params)), seen


ONE_PIXEL_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
OK_REPLY = {"attached": True, "id": 1, "name": "shot.png"}


def test_attach_writes_the_bytes_where_the_control_can_read_them() -> None:
    result, seen = _attach(OK_REPLY, {"data": ONE_PIXEL_B64, "name": "shot.png"})

    assert result == {"attached": True, "id": 1, "name": "shot.png"}
    assert seen[0]["_existed"] is True
    assert seen[0]["_body"].startswith(b"\x89PNG")


def test_attach_asks_for_the_placeholder_contract() -> None:
    # placeholder=True is what makes the [Image #N] chip authoritative, so
    # deleting it in the composer un-attaches the image.
    _result, seen = _attach(OK_REPLY, {"data": ONE_PIXEL_B64})

    assert seen[0]["placeholder"] is True


def test_attach_accepts_the_data_url_a_browser_paste_produces() -> None:
    result, seen = _attach(OK_REPLY, {"data": f"data:image/png;base64,{ONE_PIXEL_B64}"})

    assert result["attached"] is True
    assert seen[0]["_body"].startswith(b"\x89PNG")


def test_attach_removes_the_temp_file_afterwards() -> None:
    _result, seen = _attach(OK_REPLY, {"data": ONE_PIXEL_B64})

    # The control reads the file into memory, so the copy on disk is dead the
    # moment it returns.
    assert not os.path.exists(seen[0]["path"])


def test_attach_removes_the_temp_file_even_when_the_control_refuses() -> None:
    _result, seen = _attach({"error": "too many images"}, {"data": ONE_PIXEL_B64})

    assert not os.path.exists(seen[0]["path"])


@pytest.mark.parametrize(
    "params", [{}, {"data": ""}, {"data": 5}, {"data": "not base64!!"}, {"data": "===="}]
)
def test_attach_rejects_data_it_cannot_decode(params: dict[str, Any]) -> None:
    result, seen = _attach(OK_REPLY, params)

    assert "error" in result
    assert result.get("attached") is not True
    # Nothing reached the session, so nothing was queued for the next prompt.
    assert seen == []


def test_attach_reports_a_refusal_rather_than_claiming_success() -> None:
    result, _seen = _attach({"error": "too many images"}, {"data": ONE_PIXEL_B64})

    assert result.get("attached") is not True
    assert "too many images" in result["error"]


def test_attach_reports_a_session_that_did_not_answer() -> None:
    result, _seen = _attach(None, {"data": ONE_PIXEL_B64})

    assert result.get("attached") is not True


def test_attach_refuses_a_model_that_cannot_read_images() -> None:
    # deepseek-v4-pro answers a request carrying an image with a hard 400 that
    # kills the turn ("Failed to deserialize the JSON body"). Naming the model
    # is far more use than passing that through.
    result, seen = _attach(OK_REPLY, {"data": ONE_PIXEL_B64}, model="deepseek-v4-pro")

    assert result.get("attached") is not True
    assert "deepseek-v4-pro" in result["error"]
    assert "cannot read images" in result["error"]
    # Nothing was queued, so the next prompt is unaffected.
    assert seen == []


def test_attach_allows_a_model_the_table_has_never_heard_of() -> None:
    # supports_vision' own rule: an unknown id is assumed capable, because a
    # real API error beats a wrong refusal.
    result, _seen = _attach(OK_REPLY, {"data": ONE_PIXEL_B64}, model="some-new-model-9000")

    assert result["attached"] is True


def test_vision_flag_follows_the_model() -> None:
    from src.server.desktop_gateway_methods import _vision_ok

    assert _vision_ok("deepseek-v4-pro") is False
    assert _vision_ok("some-new-model-9000") is True
    # An absent model is not evidence of anything.
    assert _vision_ok("") is True
    assert _vision_ok(None) is True


# ── plan review ───────────────────────────────────────────────────────────────
#
# ExitPlanMode is a V2 tool: the plan lives in the session plan FILE, not the
# tool input, so the ask carries nothing to show and the client fetches it.


def _plan_get(reply: Any) -> dict[str, Any]:
    from src.server.desktop_gateway_methods import GatewayConnection

    connection = GatewayConnection.__new__(GatewayConnection)
    connection._session = lambda p: _Queryable(reply)  # type: ignore[method-assign]
    return asyncio.run(GatewayConnection.plan_get(connection, {}))


def test_plan_get_returns_the_plan_text() -> None:
    result = _plan_get(
        {"ok": True, "plan": "## Steps\n1. do it", "mode": "plan", "plan_file_path": "/p.md"}
    )

    assert result == {"mode": "plan", "path": "/p.md", "plan": "## Steps\n1. do it"}


def test_plan_get_reports_an_absent_plan_as_empty_not_missing() -> None:
    # get_plan returns None when the file does not exist; the panel renders its
    # own "no plan" state rather than being handed a null.
    assert _plan_get({"ok": True, "plan": None})["plan"] == ""


@pytest.mark.parametrize("reply", [None, {"ok": False, "error": "boom"}, "nonsense"])
def test_plan_get_degrades_to_empty_rather_than_raising(reply: Any) -> None:
    # A failed fetch must still leave the approval answerable.
    assert _plan_get(reply)["plan"] == ""


def test_approval_respond_forwards_explicit_updates() -> None:
    # ExitPlanMode's ask carries no suggestions, so the plan dialog composes
    # its own setMode; without this it could not be sent at all.
    session, agent, _ = _session()
    asyncio.run(
        session._route_ask(
            {"request_id": "ask-1", "request": {"subtype": "can_use_tool", "input": {}}}
        )
    )

    updates = [{"type": "setMode", "destination": "session", "mode": "acceptEdits"}]
    asyncio.run(session.respond_approval("allow", updates=updates))

    assert agent.replies[0]["response"]["chosen_updates"] == updates


def test_explicit_updates_beat_the_suggestion_list() -> None:
    session, agent, _ = _session()
    asyncio.run(
        session._route_ask(
            {
                "request_id": "ask-1",
                "request": {
                    "subtype": "can_use_tool",
                    "input": {},
                    "suggestions": [{"type": "addRules", "destination": "session"}],
                },
            }
        )
    )

    updates = [{"type": "setMode", "destination": "session", "mode": "default"}]
    asyncio.run(session.respond_approval("session", updates=updates))

    assert agent.replies[0]["response"]["chosen_updates"] == updates


def test_a_plain_approval_still_uses_its_suggestions() -> None:
    # The explicit path must not disturb the ordinary allow-for-session flow.
    session, agent, _ = _session()
    suggestion = {"type": "addRules", "destination": "session"}
    asyncio.run(
        session._route_ask(
            {
                "request_id": "ask-1",
                "request": {"subtype": "can_use_tool", "input": {}, "suggestions": [suggestion]},
            }
        )
    )

    asyncio.run(session.respond_approval("session"))

    assert agent.replies[0]["response"]["chosen_updates"] == [suggestion]


# ── the welcome screen's model chip ───────────────────────────────────────────


def _model_options(agent_config: Any, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    import src.server.desktop_gateway_methods as mod

    connection = mod.GatewayConnection.__new__(mod.GatewayConnection)
    connection._first_session = lambda p: None  # type: ignore[method-assign]

    class _State:
        pass

    state = _State()
    state.agent_config = agent_config
    connection.state = state  # type: ignore[attr-defined]

    base = catalog if catalog is not None else {
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
        "providers": [],
    }
    original = mod._catalog_from_config
    mod._catalog_from_config = lambda: dict(base)
    try:
        return asyncio.run(mod.GatewayConnection.model_options(connection, {}))
    finally:
        mod._catalog_from_config = original


class _ServeConfig:
    def __init__(self, model: str = "", provider_name: str = "") -> None:
        self.model = model
        self.provider_name = provider_name


def test_welcome_chip_names_the_model_serve_was_launched_with() -> None:
    # Otherwise the composer advertises the config default on the welcome
    # screen and silently switches the moment a session starts.
    result = _model_options(_ServeConfig(model="deepseek-v4-pro", provider_name="deepseek"))

    assert result["model"] == "deepseek-v4-pro"
    assert result["provider"] == "deepseek"


def test_welcome_chip_falls_back_to_config_when_serve_overrode_nothing() -> None:
    result = _model_options(_ServeConfig())

    assert result["model"] == "claude-sonnet-4-6"
    assert result["provider"] == "anthropic"


def test_welcome_chip_survives_a_state_with_no_agent_config() -> None:
    # Tests inject a bare spawn with no config; the catalog still answers.
    result = _model_options(None)

    assert result["model"] == "claude-sonnet-4-6"


def test_a_serve_model_without_a_provider_still_names_the_model() -> None:
    result = _model_options(_ServeConfig(model="deepseek-v4-pro"))

    assert result["model"] == "deepseek-v4-pro"
    assert result["provider"] == "anthropic"


# ── resumed titles ────────────────────────────────────────────────────────────


def _saved(tmp_path, **extra: Any) -> str:
    import json

    session_id = "ds_resume_me"
    payload = {
        "session_id": session_id,
        "conversation": {"messages": [{"role": "user", "content": "hello"}]},
        **extra,
    }
    (tmp_path / f"{session_id}.json").write_text(json.dumps(payload))
    return session_id


def test_a_saved_session_reports_its_stored_title(tmp_path) -> None:
    # The sidebar reads the name from this same file; a header that disagreed
    # with the row the user just clicked is its own small confusion.
    from src.server.desktop_sessions import load_session_messages

    session_id = _saved(tmp_path, name="Count slowly from 1 to 40")

    assert load_session_messages(tmp_path, session_id)["title"] == "Count slowly from 1 to 40"


@pytest.mark.parametrize("extra", [{}, {"name": ""}, {"name": "   "}, {"name": 7}])
def test_a_session_with_no_usable_name_reports_no_title(tmp_path, extra: dict[str, Any]) -> None:
    # Empty rather than absent, so the client's check is one shape not four.
    from src.server.desktop_sessions import load_session_messages

    session_id = _saved(tmp_path, **extra)

    assert load_session_messages(tmp_path, session_id)["title"] == ""
