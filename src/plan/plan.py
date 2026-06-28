"""Plan file storage (.clawcodex/plan.md)."""

from __future__ import annotations

from pathlib import Path


def plan_path(cwd: str | Path) -> Path:
    return Path(cwd) / ".clawcodex" / "plan.md"


def get_plan(cwd: str | Path) -> str:
    try:
        return plan_path(cwd).read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 - missing/unreadable → no plan
        return ""


def set_plan(cwd: str | Path, text: str) -> None:
    p = plan_path(cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.strip() + "\n", encoding="utf-8")


def clear_plan(cwd: str | Path) -> bool:
    p = plan_path(cwd)
    try:
        p.unlink()
        return True
    except Exception:  # noqa: BLE001 - already absent
        return False
