"""Session goals — the /goal completion-condition loop.

Public surface re-exported from :mod:`src.goals.goals`.
"""

from src.goals.goals import (
    CONTINUATION_PROMPT_TEMPLATE,
    CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE,
    DEFAULT_GOAL_MAX_TURNS,
    DEFAULT_JUDGE_MAX_TOKENS,
    DEFAULT_JUDGE_TIMEOUT_S,
    GOAL_CLEAR_ALIASES,
    GOAL_CONDITION_MAX_CHARS,
    MAX_CONSECUTIVE_PARSE_FAILURES,
    GoalJudgeTimeout,
    GoalManager,
    GoalState,
    JUDGE_SYSTEM_PROMPT,
    JUDGE_USER_PROMPT_TEMPLATE,
    JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE,
    build_judge_callable,
    collect_turn_evidence,
    judge_goal,
)

__all__ = [
    "CONTINUATION_PROMPT_TEMPLATE",
    "CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "DEFAULT_GOAL_MAX_TURNS",
    "DEFAULT_JUDGE_MAX_TOKENS",
    "DEFAULT_JUDGE_TIMEOUT_S",
    "GOAL_CLEAR_ALIASES",
    "GOAL_CONDITION_MAX_CHARS",
    "MAX_CONSECUTIVE_PARSE_FAILURES",
    "GoalJudgeTimeout",
    "GoalManager",
    "GoalState",
    "JUDGE_SYSTEM_PROMPT",
    "JUDGE_USER_PROMPT_TEMPLATE",
    "JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "build_judge_callable",
    "collect_turn_evidence",
    "judge_goal",
]
