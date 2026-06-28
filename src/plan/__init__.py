"""Project plan (the original's /plan) — a persisted plan the agent follows,
stored at ``.clawcodex/plan.md`` and injected as a system-prompt section so the
running agent honors it. Set/view/clear via /plan.
"""

from .plan import clear_plan, get_plan, plan_path, set_plan

__all__ = ["get_plan", "set_plan", "clear_plan", "plan_path"]
