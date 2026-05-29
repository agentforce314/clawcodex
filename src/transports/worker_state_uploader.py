"""Coalescing PUT /worker uploader.

Port of ``typescript/src/cli/transports/WorkerStateUploader.ts``.

Contract (mirrors TS)
---------------------

* 1 in-flight PUT + 1 pending patch slot at any time.
* New ``enqueue`` calls coalesce into the pending slot — never grows
  beyond 1 slot of backpressure.
* Top-level keys: last value wins.
* Inside ``external_metadata`` / ``internal_metadata``: RFC 7396 merge
  one level deep (overlay keys overwrite base; nulls preserved for
  server-side delete).
* On send failure: exponential backoff (``base * 2^(failures-1)``
  clamped to ``max``) plus uniform jitter ``[0, jitter)``. Retries
  indefinitely until success or ``close()``.
* ``close()`` drops pending; an in-flight retry exits at the next
  ``self._closed`` check.

Calling convention
------------------

``enqueue`` is sync but uses ``asyncio.get_running_loop()`` to schedule
the drain task. **Calling from a non-async context (no running event
loop) raises ``RuntimeError``.** TS calls the equivalent ``enqueue``
fire-and-forget from anywhere; Python callers must be inside an asyncio
loop. Matches the pattern of sibling ``ccr_client.py`` /
``hybrid_transport.py`` which also assume a running loop.

See ``my-docs/get-parity-by-folder/cli-refactoring-plan.md`` §2.2 for
the source-of-truth design notes, and ``cli-gap-analysis.md`` §3.4 / §4.2
for the inventory entry and port spec this file satisfies.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerStateUploaderConfig:
    """Construction-time knobs for :class:`WorkerStateUploader`.

    Attributes:
        send: Async callable invoked once per drained payload. Returns
            ``True`` on success, ``False`` to trigger a retry. Raising
            an exception is treated as ``False``.
        base_delay_ms: Initial exponential-backoff delay (failure 1).
        max_delay_ms: Cap for the exponential growth.
        jitter_ms: Width of the uniform jitter added to each backoff.
    """

    send: Callable[[dict[str, Any]], Awaitable[bool]]
    base_delay_ms: int
    max_delay_ms: int
    jitter_ms: int


class WorkerStateUploader:
    """Single-slot coalescing PUT uploader. See module docstring."""

    def __init__(self, config: WorkerStateUploaderConfig) -> None:
        self._config = config
        self._inflight: asyncio.Task[None] | None = None
        self._pending: dict[str, Any] | None = None
        self._closed = False

    def enqueue(self, patch: dict[str, Any]) -> None:
        """Enqueue a patch. Must be called from an async context.

        Coalesces with any existing pending patch into a single slot.
        Schedules the drain via ``loop.create_task`` if one isn't
        already running.
        """
        if self._closed:
            return
        self._pending = (
            _coalesce_patches(self._pending, patch)
            if self._pending is not None
            else dict(patch)
        )
        # Fire-and-forget drain. Requires a running event loop — see
        # module docstring "Calling convention".
        if self._inflight is None or self._inflight.done():
            loop = asyncio.get_running_loop()
            self._inflight = loop.create_task(self._drain())

    def close(self) -> None:
        """Drop pending; in-flight task exits at next ``_closed`` check."""
        self._closed = True
        self._pending = None

    async def _drain(self) -> None:
        # Iterative — matches TS's `.then(() => if (pending) drain())`
        # pattern, which schedules a new task rather than recursing.
        while not self._closed and self._pending is not None:
            payload = self._pending
            self._pending = None
            await self._send_with_retry(payload)

    async def _send_with_retry(self, payload: dict[str, Any]) -> None:
        current = payload
        failures = 0
        while not self._closed:
            try:
                ok = await self._config.send(current)
            except Exception:  # noqa: BLE001 — match TS behavior (treat throw as failure)
                ok = False
            if ok:
                return
            failures += 1
            await asyncio.sleep(self._retry_delay(failures) / 1000)
            # Absorb any patches that arrived during the retry.
            if self._pending is not None and not self._closed:
                current = _coalesce_patches(current, self._pending)
                self._pending = None

    def _retry_delay(self, failures: int) -> float:
        """Exponential backoff, clamped, then jittered.

        Order matters: clamp the exponential *before* adding jitter, so
        the cap doesn't apply to the jittered value. Matches TS
        ``WorkerStateUploader.retryDelay``.
        """
        exponential = min(
            self._config.base_delay_ms * (2 ** (failures - 1)),
            self._config.max_delay_ms,
        )
        return exponential + random.random() * self._config.jitter_ms


def _coalesce_patches(
    base: dict[str, Any] | None,
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """RFC 7396 merge one level deep inside metadata containers.

    Top-level keys: overlay replaces base (last value wins).
    ``external_metadata`` and ``internal_metadata`` keys: shallow merge
    (overlay keys are added/overwritten; ``None`` values preserved so
    the server can delete the corresponding key).
    """
    if base is None:
        return dict(overlay)
    merged = dict(base)
    for key, value in overlay.items():
        if (
            key in ("external_metadata", "internal_metadata")
            and isinstance(merged.get(key), dict)
            and isinstance(value, dict)
        ):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged
