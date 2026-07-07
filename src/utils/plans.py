"""Plan-file management for plan mode.

Ports the local-session subset of ``typescript/src/utils/plans.ts``:

* :func:`get_default_plans_directory` — ``~/.clawcodex/plans`` (the port's
  ``GLOBAL_CONFIG_DIR``; TS uses ``CLAUDE_CONFIG_DIR`` / ``~/.openclaude``).
* :func:`get_plans_directory` — honors a ``plansDirectory`` settings override
  (project-relative, traversal-guarded like ``plans.ts:96-110``), memoized,
  created on first use.
* :func:`get_plan_slug` — lazy per-session word slug (``generateWordSlug``)
  with ≤10 collision retries, cached in ``bootstrap.state``.
* :func:`get_plan_file_path` / :func:`get_plan` — ``{slug}.md`` for the main
  conversation, ``{slug}-agent-{agent_id}.md`` for subagents.

NOT ported (documented in my-docs/plan-mode/plan-mode-port-design.md §3.1):
``copyPlanForResume``/``copyPlanForFork`` (blocked on the TUI cold-resume
chapter), ``persistFileSnapshotIfRemote`` + message-history recovery
(CCR-remote only), and the transcript ``slug`` field.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from src.bootstrap.state import get_plan_slug_cache, get_session_id
from src.utils.words import generate_word_slug

logger = logging.getLogger(__name__)

MAX_SLUG_RETRIES = 10

_plans_directory_cache: Path | None = None


def get_default_plans_directory() -> Path:
    """Default plans home (TS ``getDefaultPlansDirectory``, plans.ts:27-38)."""
    from src.config import GLOBAL_CONFIG_DIR

    return Path(GLOBAL_CONFIG_DIR) / "plans"


def _settings_plans_directory() -> str | None:
    """The ``plansDirectory`` settings override, if configured.

    The port's :class:`SettingsSchema` has no dedicated field for it, so the
    key lands in ``settings.extra`` (unknown-key passthrough).
    """
    try:
        from src.settings.settings import get_settings

        raw = get_settings().extra.get("plansDirectory")
        return raw if isinstance(raw, str) and raw.strip() else None
    except Exception:  # noqa: BLE001 — settings failure must not break plans
        return None


def get_plans_directory() -> Path:
    """Resolved plans directory, created on first use (memoized).

    Mirrors ``getPlansDirectory`` (plans.ts:92-124): a settings override is
    resolved against the cwd and must stay within it (path-traversal guard);
    otherwise the default directory is used.
    """
    global _plans_directory_cache
    if _plans_directory_cache is not None:
        return _plans_directory_cache

    settings_dir = _settings_plans_directory()
    if settings_dir:
        cwd = Path(os.getcwd()).resolve()
        resolved = (cwd / os.path.expanduser(settings_dir)).resolve()
        # Validate the override stays within the project root (plans.ts:103).
        if resolved != cwd and not str(resolved).startswith(str(cwd) + os.sep):
            logger.error(
                "plansDirectory must be within project root: %s", settings_dir
            )
            plans_path = get_default_plans_directory()
        else:
            plans_path = resolved
    else:
        plans_path = get_default_plans_directory()

    try:
        plans_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("failed to create plans directory %s", plans_path)

    _plans_directory_cache = plans_path
    return plans_path


def _reset_plans_directory_cache_for_tests() -> None:
    global _plans_directory_cache
    _plans_directory_cache = None


def get_plan_slug(session_id: str | None = None) -> str:
    """Get or lazily generate the session's plan-file word slug.

    Mirrors ``getPlanSlug`` (plans.ts:45-62): generated on first access,
    cached per session id, retried up to :data:`MAX_SLUG_RETRIES` times when
    the generated filename already exists in the plans directory.
    """
    sid = session_id or get_session_id()
    cache = get_plan_slug_cache()
    slug = cache.get(sid)
    if not slug:
        plans_dir = get_plans_directory()
        for _ in range(MAX_SLUG_RETRIES):
            slug = generate_word_slug()
            if not (plans_dir / f"{slug}.md").exists():
                break
        cache[sid] = slug  # type: ignore[assignment]
    return slug  # type: ignore[return-value]


def set_plan_slug(session_id: str, slug: str) -> None:
    """Pin a slug for a session (used when resuming — ``setPlanSlug``)."""
    get_plan_slug_cache()[session_id] = slug


def clear_plan_slug(session_id: str | None = None) -> None:
    """Forget the current session's slug so /clear starts a fresh plan file."""
    get_plan_slug_cache().pop(session_id or get_session_id(), None)


def clear_all_plan_slugs() -> None:
    """Clear every session's slug entry (``clearAllPlanSlugs`` — on /clear)."""
    get_plan_slug_cache().clear()


def get_plan_file_path(agent_id: str | None = None) -> Path:
    """Path of the session's plan file (``getPlanFilePath``, plans.ts:132-142).

    Main conversation → ``{slug}.md``; subagents → ``{slug}-agent-{id}.md``.
    """
    slug = get_plan_slug(get_session_id())
    if not agent_id:
        return get_plans_directory() / f"{slug}.md"
    return get_plans_directory() / f"{slug}-agent-{agent_id}.md"


def get_plan(agent_id: str | None = None) -> str | None:
    """Plan file content, or None when it doesn't exist (``getPlan``)."""
    path = get_plan_file_path(agent_id)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("failed to read plan file %s", path)
        return None


def is_session_plan_file(absolute_path: str | Path) -> bool:
    """True when *absolute_path* is one of THIS session's plan files.

    Mirrors ``isSessionPlanFile`` (filesystem.ts:263-273): a normalized
    prefix + ``.md`` suffix match on ``{plans_dir}/{slug}`` — deliberately
    covering both ``{slug}.md`` and ``{slug}-agent-{agent_id}.md`` so
    subagent plan writes are exempt too.
    """
    try:
        normalized = os.path.normpath(str(absolute_path))
        prefix = os.path.normpath(str(get_plans_directory() / get_plan_slug()))
        return normalized.startswith(prefix) and normalized.endswith(".md")
    except Exception:  # noqa: BLE001 — a failure here must fail CLOSED (no exemption)
        return False
