from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable
from uuid import uuid4


@dataclass
class QueryDeps:
    call_model: Callable[..., AsyncGenerator[Any, None]]
    uuid: Callable[[], str] = field(default_factory=lambda: lambda: uuid4().hex)
