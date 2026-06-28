"""Plan file storage + system-prompt compose tests."""

from src.plan import clear_plan, get_plan, plan_path, set_plan


def test_set_get_clear(tmp_path):
    assert get_plan(tmp_path) == ""
    set_plan(tmp_path, "1. do X\n2. do Y")
    assert "do X" in get_plan(tmp_path)
    assert plan_path(tmp_path).exists()
    assert clear_plan(tmp_path) is True
    assert get_plan(tmp_path) == ""


def test_compose_with_plan_appends_only_when_present(tmp_path):
    from src.server.agent_server import _AgentSession

    sess = _AgentSession.__new__(_AgentSession)
    sess.cwd = str(tmp_path)
    base = [{"type": "text", "text": "base"}]
    # no plan → unchanged (regression-safe)
    assert sess._compose_with_plan(base) == base
    # with plan → appends a "# Current Plan" block
    set_plan(tmp_path, "ship it")
    composed = sess._compose_with_plan(base)
    assert len(composed) == len(base) + 1
    assert "Current Plan" in composed[-1]["text"]
    assert "ship it" in composed[-1]["text"]
