from src.tool_system.tools.bash.sleep_detection import detect_blocked_sleep_pattern


def test_short_sleeps_match_prompt_and_are_allowed() -> None:
    for seconds in range(1, 6):
        assert detect_blocked_sleep_pattern(
            f"sleep {seconds}; curl -s http://localhost:8080"
        ) is None


def test_long_leading_sleep_is_blocked() -> None:
    assert detect_blocked_sleep_pattern("sleep 6 && echo ready") == (
        "sleep 6 followed by: echo ready"
    )


def test_long_standalone_sleep_is_blocked() -> None:
    assert detect_blocked_sleep_pattern("sleep 30") == "standalone sleep 30"


def test_sleep_inside_subshell_is_not_treated_as_leading_sleep() -> None:
    assert detect_blocked_sleep_pattern("(sleep 30); echo ready") is None
