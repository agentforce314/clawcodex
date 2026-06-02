"""Runtime observer protocol for F-43 hot-swap notifications.

Defines the :class:`RuntimeObserver` Protocol and the
:class:`RuntimeContext.attach_observer` / :meth:`detach_observer` helpers.
Downstream consumers (REPL, TUI, AgentBridge) implement this protocol to
react when :meth:`RuntimeContext.swap_provider` rebuilds the provider +
tool registry, without ``src/*`` having to know about each consumer's
private state.

Why a Protocol? The runtime context lives in
``clawcodex_ext/runtime/context.py`` and the consumers (REPL/TUI) live in
``src/*`` — a downstream-defined interface keeps the dependency arrow
pointing one way. The runtime does not import the REPL or TUI; instead
the REPL/TUI register themselves with the runtime at construction time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from clawcodex_ext.runtime.context import RuntimeContext


@runtime_checkable
class RuntimeObserver(Protocol):
    """Subscriber that reacts to :meth:`RuntimeContext.swap_provider`.

    The runtime calls :meth:`on_runtime_swap` *after* the provider,
    provider_name, tool_registry, and tool_context on the runtime have
    been replaced. Implementations should sync their own private
    references (e.g. ``self.provider = runtime.provider``) but must not
    mutate the runtime itself.
    """

    def on_runtime_swap(self, runtime: "RuntimeContext") -> None:
        """Called immediately after a successful ``swap_provider``.

        Args:
            runtime: The :class:`RuntimeContext` whose provider + tool
                state has just been rebuilt. The observer may read
                ``runtime.provider``, ``runtime.provider_name``,
                ``runtime.tool_registry``, ``runtime.tool_context``,
                ``runtime.options.provider_name``, and
                ``runtime.options.model``.
        """
        ...


def attach_observer(
    runtime: "RuntimeContext",
    observer: RuntimeObserver,
) -> None:
    """Register ``observer`` to receive swap notifications.

    Duplicate registrations are ignored (set semantics). Exceptions raised
    by the observer during :meth:`on_runtime_swap` are swallowed by the
    runtime so a single buggy observer cannot break provider switching.
    """
    observers = getattr(runtime, "_observers", None)
    if observers is None:
        observers = []
        # ``_observers`` is not a dataclass field; attach ad-hoc.
        try:
            object.__setattr__(runtime, "_observers", observers)
        except Exception:
            runtime.__dict__["_observers"] = observers
    if observer in observers:
        return
    observers.append(observer)


def detach_observer(
    runtime: "RuntimeContext",
    observer: RuntimeObserver,
) -> None:
    """Unregister a previously-attached observer. No-op if not attached."""
    observers = getattr(runtime, "_observers", None)
    if not observers:
        return
    try:
        observers.remove(observer)
    except ValueError:
        pass


def notify_observers(runtime: "RuntimeContext") -> None:
    """Fan out a swap event to all attached observers.

    Called by :meth:`RuntimeContext.swap_provider` after the internal
    state has been replaced. Errors are caught per-observer so a single
    faulty consumer cannot abort the switch.
    """
    observers = list(getattr(runtime, "_observers", []) or [])
    for observer in observers:
        try:
            observer.on_runtime_swap(runtime)
        except Exception:
            # Swallow observer errors; the swap has already succeeded.
            pass
