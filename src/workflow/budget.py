"""The ``budget`` primitive — a token target that acts as a hard ceiling.

Exposes ``budget.total`` / ``budget.spent()`` / ``budget.remaining()`` to the
script. The engine adds each agent's token usage via :meth:`Budget.add`; once
``spent`` reaches ``total`` the next ``agent()`` call raises
:class:`WorkflowBudgetExceeded`.

Scripts scale depth with::

    while budget.total and budget.remaining() > 50_000:
        ...

``total`` is ``None`` when no target was set, in which case ``remaining()`` is
``math.inf`` and the ceiling never trips.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from .errors import WorkflowBudgetExceeded


class Budget:
    def __init__(
        self,
        total: Optional[int] = None,
        *,
        base_spent: Callable[[], int] | None = None,
    ) -> None:
        """``base_spent`` optionally reports tokens already spent outside the
        workflow (the main loop), so ``spent()`` is a shared pool as the spec
        requires; it defaults to zero for a standalone run."""
        self._total = total
        self._own_spent = 0
        self._base_spent = base_spent

    @property
    def total(self) -> Optional[int]:
        return self._total

    def spent(self) -> int:
        base = self._base_spent() if self._base_spent is not None else 0
        return base + self._own_spent

    def remaining(self) -> float:
        if self._total is None:
            return math.inf
        return max(0, self._total - self.spent())

    def add(self, tokens: int) -> None:
        if tokens > 0:
            self._own_spent += tokens

    def check(self) -> None:
        """Raise if the ceiling has been reached. Called before each ``agent()``."""
        if self._total is not None and self.spent() >= self._total:
            raise WorkflowBudgetExceeded(
                f"workflow budget exhausted: spent {self.spent()} of {self._total} tokens"
            )
