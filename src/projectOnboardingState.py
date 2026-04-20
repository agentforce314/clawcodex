from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProjectOnboardingState:
    has_readme: bool
    has_tests: bool
    python_first: bool = True
