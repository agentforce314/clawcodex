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
from typing import Any

import pytest

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
