"""Map a key sequence to an action, given an active-context set.

Mirrors ``typescript/src/keybindings/resolver.ts``. The resolver is the
read side of the keybinding system: given a list of registered bindings
and the user's current active contexts, ask "is this sequence bound?
return the action that fires."

Three resolution rules (refactoring-plan §6 WI-2.3):

* **Exact match wins immediately**. A two-key chord ``("g", "g")`` only
  resolves once both keys have arrived; until then the resolver returns
  :data:`PENDING` if any binding has the buffer as a prefix, else
  :data:`NO_MATCH`.
* **Context filtering**: bindings with a non-``None`` ``when`` clause
  only fire when ``when`` is in the active-context set passed to
  :meth:`resolve`. Bindings with ``when=None`` are global — always
  candidate.
* **Longest match wins** when multiple bindings would match. Specifically:
  if ``("g",)`` and ``("g", "g")`` are both bound, typing the second ``g``
  resolves the longer chord. Implemented by buffering until no longer
  prefix can extend.

Reserved shortcuts (mirrors TS ``reservedShortcuts.ts``) are listed
separately; user-config validators (Phase 2 callers) consult them so they
can warn when a user tries to rebind ``ctrl+c`` (which the host terminal
intercepts).

Public surface:

* :class:`ResolveResult` — typed verdict (matched/pending/no-match).
* :class:`KeybindingResolver` — the main class.
* :data:`RESERVED_SHORTCUTS` — frozenset of keys that must not be rebound.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Iterable

from .keybindings_schema import KeybindingEntry


# Keys the host terminal / Textual intercept universally; rebinding them
# either does nothing (terminal swallows the key before our process sees
# it) or breaks core functionality.
#
# Aligned with ``typescript/src/keybindings/reservedShortcuts.ts``:
# ctrl+s / ctrl+q (XOFF/XON flow control) are NOT reserved — modern
# terminals disable flow-control by default and the chapter uses
# ctrl+s for stash. ctrl+m (CR alias for Enter) and ctrl+\ (SIGQUIT)
# ARE reserved.
RESERVED_SHORTCUTS: Final[frozenset[str]] = frozenset(
    {
        "ctrl+c",   # SIGINT / cancel
        "ctrl+z",   # SIGTSTP / suspend
        "ctrl+d",   # EOF / exit
        "ctrl+m",   # CR alias for Enter — unreliable to bind to
        "ctrl+\\",  # SIGQUIT
    }
)


class ResolveStatus(Enum):
    MATCHED = "matched"
    PENDING = "pending"
    NO_MATCH = "no-match"


@dataclass(frozen=True)
class ResolveResult:
    """The resolver's verdict for a buffered key sequence.

    * ``MATCHED`` — ``action`` is the action id to fire; clear the buffer.
    * ``PENDING`` — at least one binding has the buffer as a prefix; keep
      buffering. ``action`` is ``None``.
    * ``NO_MATCH`` — no binding could possibly extend this buffer; reset.
      ``action`` is ``None``.
    """

    status: ResolveStatus
    action: str | None = None


class KeybindingResolver:
    """Read-only wrapper around a list of typed bindings.

    Construction is cheap; a fresh resolver per session is fine. The
    bindings list is captured at construction time — call
    :meth:`replace_bindings` (e.g. after a hot-reload) to swap.
    """

    def __init__(self, bindings: Iterable[KeybindingEntry]) -> None:
        self._bindings: tuple[KeybindingEntry, ...] = tuple(bindings)

    def replace_bindings(self, bindings: Iterable[KeybindingEntry]) -> None:
        """Swap the binding set wholesale (for hot-reload after config change)."""

        self._bindings = tuple(bindings)

    @property
    def bindings(self) -> tuple[KeybindingEntry, ...]:
        return self._bindings

    def resolve(
        self,
        sequence: list[str] | tuple[str, ...],
        context: set[str] | frozenset[str] | None = None,
        *,
        committed: bool = False,
    ) -> ResolveResult:
        """Map ``sequence`` to an action under ``context``.

        Args:
            sequence: The buffered key tokens — must be at least one
                token. Single-key bindings dispatch on a one-element
                sequence; chords on two or more.
            context: Set of active context names. ``None`` is treated as
                an empty set (only global bindings — those with
                ``when=None`` — can match).
            committed: Suppress the longest-match-wins / PENDING check.
                Use this when the chord-tracker's timeout has elapsed and
                the buffer can no longer extend — the resolver should
                fire the shortest exact match instead of waiting for an
                extension that will never arrive. Mirrors vim's
                post-``timeoutlen`` behavior.

        Returns:
            A :class:`ResolveResult`. ``MATCHED`` means fire the action
            and clear the buffer; ``PENDING`` means a longer match is
            still possible (only returned when ``committed=False``);
            ``NO_MATCH`` means reset the buffer.
        """

        if not sequence:
            return ResolveResult(status=ResolveStatus.NO_MATCH)

        active: frozenset[str] = (
            frozenset(context) if context is not None else frozenset()
        )
        seq_tuple = tuple(sequence)

        # Walk every binding once. Track exact matches (potential
        # ``MATCHED`` candidates) and prefix matches (potential
        # ``PENDING`` reasons) separately; the verdict synthesizes them.
        matched_candidates: list[KeybindingEntry] = []
        has_pending_extension = False

        for entry in self._bindings:
            if not _context_allows(entry, active):
                continue
            if entry.keys == seq_tuple:
                matched_candidates.append(entry)
                continue
            # Is the buffer a strict prefix of this binding? If yes, the
            # user might still type more keys to reach this entry. Only
            # relevant when not yet committed (timeout not yet elapsed).
            if (
                not committed
                and len(entry.keys) > len(seq_tuple)
                and entry.keys[: len(seq_tuple)] == seq_tuple
            ):
                has_pending_extension = True

        # Longest-match-wins (vim ``timeoutlen`` semantic): if any binding
        # could be extended past the current buffer, defer to PENDING even
        # when a shorter exact match exists. The chord-tracker's timeout
        # is the disambiguator — once committed=True (timeout fired), this
        # branch is suppressed and the shorter exact match fires as
        # MATCHED below.
        if has_pending_extension:
            return ResolveResult(status=ResolveStatus.PENDING)

        if matched_candidates:
            # Most-specific-context-wins: prefer entries with a non-None
            # ``when`` (context-scoped) over global entries when both
            # match. Mirrors chapter resolver semantics: a
            # ``transcript.focused``-scoped binding overrides a global one.
            scoped = [e for e in matched_candidates if e.when is not None]
            chosen = scoped[0] if scoped else matched_candidates[0]
            return ResolveResult(
                status=ResolveStatus.MATCHED, action=chosen.action
            )

        return ResolveResult(status=ResolveStatus.NO_MATCH)

    def reserved_shortcuts(self) -> frozenset[str]:
        """Return the immutable set of keys that should not be rebound.

        Mirrors ``typescript/src/keybindings/reservedShortcuts.ts``. Callers
        (e.g. a user-config validator) compare against this and warn the
        user when they try to bind a reserved key.
        """

        return RESERVED_SHORTCUTS

    def is_reserved(self, key_token: str) -> bool:
        """True iff ``key_token`` is in :attr:`RESERVED_SHORTCUTS`."""

        return key_token in RESERVED_SHORTCUTS

    def find_reserved_collisions(self) -> list[KeybindingEntry]:
        """Return bindings whose first key token is reserved.

        Useful for early validation: a binding starting on ``ctrl+c`` will
        never fire because the terminal intercepts the keystroke.
        """

        out: list[KeybindingEntry] = []
        for entry in self._bindings:
            if entry.keys and entry.keys[0] in RESERVED_SHORTCUTS:
                out.append(entry)
        return out


def _context_allows(entry: KeybindingEntry, active: frozenset[str]) -> bool:
    """``entry`` fires if (a) it is global OR (b) its ``when`` is active."""

    if entry.when is None:
        return True
    return entry.when in active


__all__ = [
    "KeybindingResolver",
    "RESERVED_SHORTCUTS",
    "ResolveResult",
    "ResolveStatus",
]
