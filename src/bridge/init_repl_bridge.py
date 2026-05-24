"""REPL bridge bootstrap — Phase 7 MVP slice.

Ports ``typescript/src/bridge/initReplBridge.ts`` (570 lines).

The TS file is a thick bootstrap layer that reads bootstrap state (cwd,
session ID, git context, OAuth tokens, persisted title), runs pre-flight
checks (auth, version, org UUID, entitlement), branches between the
env-less (v2) and env-based (v1) bridge cores via the
``tengu_bridge_repl_v2`` GrowthBook gate, and wires the two-stage
title-derivation pipeline (instant placeholder + async Haiku
regeneration).

This Phase 7 MVP ports the structural skeleton:

* ``init_repl_bridge(options) -> ReplBridgeHandle | None``
* Pre-flight: OAuth token, org UUID, entitlement (via Phase 1 stubs)
* v1/v2 branching based on ``is_env_less_bridge_enabled() and not perpetual``
* Delegates to Phase 5 ``init_env_less_bridge_core`` (v2) or Phase 6
  ``init_bridge_core`` (v1, MVP slice)
* ``derive_title(raw)`` — synchronous quick placeholder (strip display
  tags, first sentence, truncate to 50 chars)

Explicit deferrals (Phase 10 follow-ups):

* **Two-stage Haiku title generation** — requires ``generate_session_title``
  from a future ``utils/session_title.py`` port (Haiku model call with
  15s timeout + fire-and-forget guards). Until then, only ``derive_title``'s
  instant placeholder is used; long-form titles wait for the user to
  rename via ``/rename``.
* **OAuth waterfall** (proactive refresh + cross-process backoff) —
  ``check_and_refresh_oauth_token_if_needed`` is a no-op stub today
  (Phase 2). The MVP just reads the token and proceeds.
* **KAIROS / assistant-mode worker_type detection** — defaults to
  ``claude_code``. Future port can extend.
* **Policy-limits gating** — TS calls ``isPolicyAllowed`` + waits for
  policy limits to load. The MVP skips this.
* **``previously_flushed_uuids``** — propagated to ``init_bridge_core``
  but the MVP doesn't maintain it across sessions yet.

The function signature matches TS so a future expansion is non-breaking.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.auth.claude_ai import (
    check_and_refresh_oauth_token_if_needed,
    has_profile_scope,
    is_claude_ai_subscriber,
)
from src.bridge.bridge_config import (
    get_bridge_access_token,
    get_bridge_base_url,
)
from src.bridge.bridge_enabled import (
    check_bridge_min_version,
    is_env_less_bridge_enabled,
)
from src.bridge.debug_utils import log_bridge_skip
from src.bridge.env_less_bridge_config import (
    check_env_less_bridge_min_version,
)
from src.bridge.remote_bridge_core import (
    EnvLessBridgeParams,
    init_env_less_bridge_core,
)
from src.bridge.repl_bridge import (
    BridgeCoreParams,
    ReplBridgeHandle,
    init_bridge_core,
)
from src.services.oauth.client import get_organization_uuid

logger = logging.getLogger(__name__)


# ── Public surface ────────────────────────────────────────────────────────


OnInboundMessage = Callable[[dict[str, Any]], Any]
OnUserMessage = Callable[[str, str], bool]
OnPermissionResponse = Callable[[dict[str, Any]], None]
OnInterrupt = Callable[[], None]
OnSetModel = Callable[[str | None], None]
OnSetMaxThinkingTokens = Callable[[int | None], None]
OnSetPermissionMode = Callable[[str], Any]
OnStateChange = Callable[..., None]


@dataclass
class InitBridgeOptions:
    """Caller-supplied configuration for ``init_repl_bridge``.

    Mirrors TS ``InitBridgeOptions`` consumer surface. Most fields are
    optional callbacks; the only required-by-policy field is
    ``initial_name`` (which defaults to a generated session title).
    """

    initial_name: str | None = None
    initial_messages: list[Any] | None = None
    previously_flushed_uuids: set[str] | None = None
    perpetual: bool = False
    outbound_only: bool = False
    tags: list[str] | None = None
    initial_history_cap: int = 200

    on_inbound_message: OnInboundMessage | None = None
    on_user_message: OnUserMessage | None = None
    on_permission_response: OnPermissionResponse | None = None
    on_interrupt: OnInterrupt | None = None
    on_set_model: OnSetModel | None = None
    on_set_max_thinking_tokens: OnSetMaxThinkingTokens | None = None
    on_set_permission_mode: OnSetPermissionMode | None = None
    on_state_change: OnStateChange | None = None

    # Inject sync createSession / archiveSession for v1 path — daemon /
    # REPL wrappers fill these with their own org-scoped HTTP wrappers.
    # MVP provides defaults that wire through the v2 ``code_session_api``
    # which is sufficient for the env-less code path.
    create_session: (
        Callable[[dict[str, Any]], Awaitable[str | None]] | None
    ) = None
    archive_session: Callable[[str], Awaitable[None]] | None = None

    # Phase 11c hooks:

    # Override the ``worker_type`` metadata sent at env registration.
    # Mirrors TS KAIROS / assistant-mode detection where the worker
    # advertises itself as ``claude_code_assistant`` so the claude.ai
    # session picker can filter it into the assistant tab. Defaults to
    # ``claude_code``. Callers can pass a callable so detection runs at
    # init time (e.g. reading bootstrap state).
    worker_type: str | Callable[[], str] = 'claude_code'

    # Whether to run a proactive OAuth-token refresh during pre-flight.
    # The check_and_refresh_oauth_token_if_needed call is a no-op stub
    # today (Phase 2 deferral) but emits a warning if a near-expiry
    # token is detected; flipping this off skips the warning when
    # callers know they're using a long-lived token.
    proactive_oauth_refresh: bool = True


async def init_repl_bridge(
    options: InitBridgeOptions | None = None,
    *,
    machine_name: str = 'localhost',
    branch: str = 'main',
    git_repo_url: str | None = None,
    working_dir: str = '.',
) -> ReplBridgeHandle | Any | None:
    """Bootstrap the REPL bridge: pre-flight + v1/v2 branching + delegate.

    Returns the bridge handle (``ReplBridgeHandle`` for v1 or
    ``RemoteBridgeHandle`` for v2) on success, or ``None`` on any
    pre-flight failure.

    Mirrors TS ``initReplBridge`` consumer surface. The wrapper fields
    (``machine_name``, ``branch``, ``git_repo_url``, ``working_dir``) are
    explicit kw-only args here rather than being read from bootstrap
    state — the Python port doesn't bind to the REPL's bootstrap layer
    yet, so callers supply them. A future expansion can default them
    from ``src/bootstrap/state``.
    """
    opts = options or InitBridgeOptions()

    # ── 1. Proactive OAuth refresh ─────────────────────────────────────
    # Phase 11c addition: run the refresh waterfall BEFORE reading the
    # access token. Real impl (Phase 10 keychain port) refreshes tokens
    # near expiry; the current stub is a no-op that emits a warning
    # when expiry is detected — useful signal for diagnosing 401s in
    # tests / dev.
    if opts.proactive_oauth_refresh:
        try:
            await check_and_refresh_oauth_token_if_needed()
        except Exception as err:  # noqa: BLE001
            # Best-effort: never block init on a refresh failure. The
            # subsequent token read + entitlement check will fail
            # explicitly if the token actually is stale.
            logger.debug(
                '[bridge:repl] proactive OAuth refresh raised '
                '(continuing): %s', err
            )

    # ── 2. OAuth token pre-check ───────────────────────────────────────
    access_token = get_bridge_access_token()
    if not access_token:
        log_bridge_skip(
            'no_oauth_token',
            '[bridge:repl] Skipping: no OAuth token',
        )
        _fire_state(opts.on_state_change, 'failed', '/login')
        return None

    # ── 2. Entitlement check (subscriber + profile scope) ──────────────
    if not is_claude_ai_subscriber():
        log_bridge_skip(
            'not_subscriber',
            '[bridge:repl] Skipping: not a claude.ai subscriber',
        )
        _fire_state(opts.on_state_change, 'failed', '/login')
        return None
    if not has_profile_scope():
        log_bridge_skip(
            'no_profile_scope',
            '[bridge:repl] Skipping: token missing user:profile scope',
        )
        _fire_state(opts.on_state_change, 'failed', '/login')
        return None

    # ── 3. Org UUID (needed by both v1 and v2 paths) ───────────────────
    org_uuid = await get_organization_uuid()
    if not org_uuid:
        log_bridge_skip(
            'no_org_uuid',
            '[bridge:repl] Skipping: no org UUID',
        )
        _fire_state(opts.on_state_change, 'failed', '/login')
        return None

    title = opts.initial_name or 'Remote Control session'
    base_url = get_bridge_base_url()

    # ── 4. v1/v2 branching ─────────────────────────────────────────────
    # The env-less (v2) path skips the Environments API entirely. Per
    # TS comment: perpetual mode is env-coupled and falls back to v1.
    if is_env_less_bridge_enabled() and not opts.perpetual:
        version_error = await check_env_less_bridge_min_version()
        if version_error:
            log_bridge_skip(
                'version_too_old',
                f'[bridge:repl] Skipping: {version_error}',
                v2=True,
            )
            _fire_state(
                opts.on_state_change, 'failed',
                'run `openclaude update` to upgrade',
            )
            return None
        logger.debug(
            '[bridge:repl] Using env-less bridge path '
            '(tengu_bridge_repl_v2 stub returns True)'
        )
        return await init_env_less_bridge_core(EnvLessBridgeParams(
            base_url=base_url,
            org_uuid=org_uuid,
            title=title,
            get_access_token=get_bridge_access_token,
            initial_history_cap=opts.initial_history_cap,
            initial_messages=opts.initial_messages,
            on_inbound_message=opts.on_inbound_message,
            on_user_message=opts.on_user_message,
            on_permission_response=opts.on_permission_response,
            on_interrupt=opts.on_interrupt,
            on_set_model=opts.on_set_model,
            on_set_max_thinking_tokens=opts.on_set_max_thinking_tokens,
            on_set_permission_mode=opts.on_set_permission_mode,
            on_state_change=opts.on_state_change,
            outbound_only=opts.outbound_only,
            tags=opts.tags,
        ))

    # ── v1 path: env-based (register/poll/ack/heartbeat) ──────────────
    version_error_sync = check_bridge_min_version()
    if version_error_sync:
        log_bridge_skip(
            'version_too_old',
            f'[bridge:repl] Skipping: {version_error_sync}',
        )
        _fire_state(
            opts.on_state_change, 'failed',
            'run `openclaude update` to upgrade',
        )
        return None

    # v1 path requires create_session + archive_session injections.
    # Without them we can't delegate to init_bridge_core. MVP returns
    # None with a clear log so the caller can wire them.
    if opts.create_session is None or opts.archive_session is None:
        log_bridge_skip(
            'v1_path_missing_callbacks',
            '[bridge:repl] Skipping: v1 path requires create_session + '
            'archive_session callbacks (Phase 7 MVP — wrappers come later)',
        )
        _fire_state(
            opts.on_state_change, 'failed',
            'v1 path not yet supported in MVP',
        )
        return None

    # Phase 11c: resolve worker_type from callable-or-string.
    worker_type = (
        opts.worker_type()
        if callable(opts.worker_type)
        else opts.worker_type
    )
    return await init_bridge_core(BridgeCoreParams(
        dir=working_dir,
        machine_name=machine_name,
        branch=branch,
        git_repo_url=git_repo_url,
        title=title,
        base_url=base_url,
        session_ingress_url=base_url,
        worker_type=worker_type,
        get_access_token=get_bridge_access_token,
        create_session=opts.create_session,
        archive_session=opts.archive_session,
        on_inbound_message=opts.on_inbound_message,
        on_user_message=opts.on_user_message,
        on_permission_response=opts.on_permission_response,
        on_interrupt=opts.on_interrupt,
        on_set_model=opts.on_set_model,
        on_set_max_thinking_tokens=opts.on_set_max_thinking_tokens,
        on_set_permission_mode=opts.on_set_permission_mode,
        on_state_change=opts.on_state_change,
        initial_history_cap=opts.initial_history_cap,
        initial_messages=opts.initial_messages,
        perpetual=opts.perpetual,
    ))


# ── Title derivation ──────────────────────────────────────────────────────


TITLE_MAX_LEN = 50


_FIRST_SENTENCE_RE = re.compile(r'^(.*?[.!?])\s', re.DOTALL)
_WHITESPACE_RE = re.compile(r'\s+')
_DISPLAY_TAG_RE = re.compile(
    r'<([a-z][\w-]*)(?:\s[^>]*)?>[\s\S]*?</\1>\n?',
)


def _strip_display_tags(text: str) -> str:
    """Strip XML display tags (mirrors TS ``stripDisplayTagsAllowEmpty``).

    Returns the empty string (not the original) when all content is tags,
    matching the TS contract.
    """
    return _DISPLAY_TAG_RE.sub('', text)


def derive_title(raw: str) -> str | None:
    """Quick placeholder title from a raw user message.

    Mirrors TS ``deriveTitle`` on ``initReplBridge.ts:556-569``:

    1. Strip ``<ide_opened_file>``, ``<session-start-hook>``, etc.
       (display tags injected by IDE/hooks).
    2. Take the first sentence (terminator: ``.``, ``!``, ``?``).
    3. Collapse newlines/tabs into single spaces.
    4. Truncate to 50 chars (with an ``…`` if truncated).

    Returns ``None`` for empty / pure-display-tag content so callers
    can fall through to the generated title.
    """
    clean = _strip_display_tags(raw)
    match = _FIRST_SENTENCE_RE.match(clean)
    first_sentence = match.group(1) if match else clean
    flat = _WHITESPACE_RE.sub(' ', first_sentence).strip()
    if not flat:
        return None
    if len(flat) > TITLE_MAX_LEN:
        return flat[:TITLE_MAX_LEN - 1] + '…'
    return flat


# ── Helpers ──────────────────────────────────────────────────────────────


def _fire_state(
    cb: OnStateChange | None,
    state: str,
    detail: str | None = None,
) -> None:
    if cb is None:
        return
    try:
        if detail is None:
            cb(state)
        else:
            cb(state, detail)
    except Exception as err:  # noqa: BLE001
        logger.warning('[bridge:repl] on_state_change raised: %s', err)


__all__ = [
    'TITLE_MAX_LEN',
    'InitBridgeOptions',
    'derive_title',
    'init_repl_bridge',
]
