"""Peer address parser — Chunk F / WI-7.2.

Mirrors the ``parseAddress`` helper in ``typescript/src/utils/peerAddress.ts``.
SendMessage's ``to:`` field accepts four forms:

* ``"<name>"`` or ``"<agent_id>"`` — in-process / mailbox routing
  (the parser tags this as ``scheme="other"``).
* ``"*"`` — broadcast (also ``scheme="other"``; SendMessage handles
  the wildcard at routing-time, not parsing-time).
* ``"bridge:<session-id>"`` — cross-machine via Anthropic's relay.
* ``"uds:<socket-path>"`` — local Unix-domain-socket peer.

The bridge: / uds: schemes are out of scope for this port (per
ambiguity #5 — gated behind ``feature('UDS_INBOX')`` even in TS).
SendMessage's dispatch chain still has explicit branches for them
(``NotImplementedError`` stubs preserving TS dispatch order so a
future implementation is a localized body change).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AddressScheme = Literal["bridge", "uds", "other"]


@dataclass(frozen=True)
class ParsedAddress:
    """Output of ``parse_address``.

    * ``scheme`` — ``"bridge"`` / ``"uds"`` / ``"other"``.
    * ``target`` — the part after the prefix (e.g.
      ``"my-session-id"`` for ``"bridge:my-session-id"``). For
      ``"other"`` it's the entire input.
    """

    scheme: AddressScheme
    target: str


def parse_address(to: str) -> ParsedAddress:
    """Classify the ``to:`` field by prefix.

    The ``"bridge:"`` and ``"uds:"`` checks are case-sensitive
    (matching TS); a payload of ``"Bridge:..."`` falls through to
    ``"other"``. Empty input is allowed and tagged ``"other"`` —
    SendMessage's input-validation layer rejects empty strings before
    they reach this helper.
    """
    if to.startswith("bridge:"):
        return ParsedAddress(scheme="bridge", target=to[len("bridge:"):])
    if to.startswith("uds:"):
        return ParsedAddress(scheme="uds", target=to[len("uds:"):])
    return ParsedAddress(scheme="other", target=to)


__all__ = [
    "AddressScheme",
    "ParsedAddress",
    "parse_address",
]
