"""Auto-memory path resolution and enable/disable.

Ports `typescript/src/memdir/paths.ts`. The auto-memory subsystem stores
typed memory files in a per-project directory under
``~/.clawcodex/projects/<sanitized-git-root>/memory/``.

Public surface:
    is_auto_memory_enabled() -> bool
    has_auto_mem_path_override() -> bool
    get_memory_base_dir() -> str
    get_auto_mem_path() -> str
    get_auto_mem_entrypoint() -> str
    get_auto_mem_daily_log_path(date=None) -> str
    is_auto_mem_path(absolute_path) -> bool

Resolution order for ``get_auto_mem_path()`` (matches TS):
    1. ``CLAUDE_COWORK_MEMORY_PATH_OVERRIDE`` env (no tilde expansion).
    2. Default: ``<base>/projects/<sanitized-git-root>/memory/``.

The ``autoMemoryDirectory`` settings field from TS is deferred — Python's
settings module does not expose per-source access (only merged), and
honoring the field from a merged read would honor a malicious project's
``.clawcodex/settings.json``, which combined with the Write-tool carve-out
(see ``tool_system/tools/write.py``) could grant write access outside the
project. Track as follow-up: add ``get_settings_for_source(...)`` in
``src/settings`` and wire the trusted-source chain (policy/flag/local/user
only — never project).

Enable/disable order:
    1. ``CLAUDE_CODE_DISABLE_AUTO_MEMORY`` env (1/true → off, 0/false → on)
    2. ``CLAUDE_CODE_SIMPLE`` (bare mode) → off
    3. ``CLAUDE_CODE_REMOTE`` without ``CLAUDE_CODE_REMOTE_MEMORY_DIR`` → off
    4. ``autoMemoryEnabled`` in merged settings (any source — supports
       project-level opt-out, matching TS ``paths.ts:50``)
    5. Default: enabled
"""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from datetime import date as _date
from pathlib import Path

__all__ = [
    "is_auto_memory_enabled",
    "has_auto_mem_path_override",
    "get_memory_base_dir",
    "get_auto_mem_path",
    "get_auto_mem_entrypoint",
    "get_auto_mem_daily_log_path",
    "is_auto_mem_path",
    "sanitize_path",
    "find_canonical_git_root",
]


_AUTO_MEM_DIRNAME = "memory"
_AUTO_MEM_ENTRYPOINT_NAME = "MEMORY.md"


def _is_env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def _is_env_defined_falsy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("0", "false", "no", "off")


def is_auto_memory_enabled() -> bool:
    """Whether auto-memory is active for this session."""
    env_val = os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY")
    if _is_env_truthy(env_val):
        return False
    if _is_env_defined_falsy(env_val):
        return True
    if _is_env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE")):
        return False
    if (
        _is_env_truthy(os.environ.get("CLAUDE_CODE_REMOTE"))
        and not os.environ.get("CLAUDE_CODE_REMOTE_MEMORY_DIR")
    ):
        return False
    # Merged settings — any source. Project-level opt-out is intentional.
    try:
        from src.settings.settings import get_settings

        settings = get_settings()
        # The Python SettingsSchema does not declare auto_memory_enabled
        # today; treat absence as "default on". When the field is added,
        # honor it here.
        flag = getattr(settings, "auto_memory_enabled", None)
        if flag is False:
            return False
    except Exception:
        # Settings module errors should not silently disable memory; the
        # default is on. Log via debug if a logger is configured.
        pass
    return True


def get_claude_config_home_dir() -> str:
    """Return ``$CLAWCODEX_CONFIG_DIR`` if set, else ``~/.clawcodex``."""
    override = os.environ.get("CLAWCODEX_CONFIG_DIR")
    if override:
        return str(Path(override).expanduser())
    return str(Path.home() / ".clawcodex")


def get_memory_base_dir() -> str:
    """Base directory for memory storage.

    ``CLAUDE_CODE_REMOTE_MEMORY_DIR`` overrides for CCR scenarios; default
    is the standard config home (``~/.clawcodex``).
    """
    remote = os.environ.get("CLAUDE_CODE_REMOTE_MEMORY_DIR")
    if remote:
        return remote
    return get_claude_config_home_dir()


def sanitize_path(path: str) -> str:
    """Flatten a filesystem path into a directory-safe key.

    Mirrors TS ``sanitizePath``: replaces ``/``, ``\\``, and ``:`` with
    ``-`` so that an absolute path becomes a single folder name. Keeps a
    leading ``-`` for absolute Unix paths (the chapter's example shows
    ``/Users/alex/code/myapp`` → ``-Users-alex-code-myapp``).
    """
    return re.sub(r"[\\/:]+", "-", path)


def _validate_memory_path(raw: str | None, *, expand_tilde: bool) -> str | None:
    """Normalize and validate a candidate memory directory path.

    Rejects paths that are not safe to use as a write-allowlist root:
    relative, near-root, Windows drive-root, UNC, null-byte. Returns the
    NFC-normalized absolute path with a single trailing separator, or
    ``None`` on rejection.
    """
    if not raw:
        return None
    candidate = raw
    if expand_tilde and (candidate.startswith("~/") or candidate.startswith("~\\")):
        rest = candidate[2:]
        # Reject trivial remainders that would expand to $HOME or an ancestor.
        rest_norm = os.path.normpath(rest or ".")
        if rest_norm in (".", ".."):
            return None
        candidate = str(Path.home() / rest)
    if "\0" in candidate:
        return None
    normalized = os.path.normpath(candidate).rstrip("/\\")
    if not os.path.isabs(normalized):
        return None
    if len(normalized) < 3:
        return None
    if re.match(r"^[A-Za-z]:$", normalized):
        return None
    if normalized.startswith("\\\\") or normalized.startswith("//"):
        return None
    nfc = unicodedata.normalize("NFC", normalized + os.sep)
    return nfc


def _get_auto_mem_path_override() -> str | None:
    """Read ``CLAUDE_COWORK_MEMORY_PATH_OVERRIDE`` if set and valid."""
    return _validate_memory_path(
        os.environ.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"),
        expand_tilde=False,
    )


def has_auto_mem_path_override() -> bool:
    """True iff the override env var is set to a valid absolute path.

    Used by the SDK custom-prompt branch in
    ``context_system/prompt_assembly.py`` to decide whether to inject
    the memory section after a caller-provided custom system prompt.
    """
    return _get_auto_mem_path_override() is not None


def find_canonical_git_root(start: str | os.PathLike[str] | None = None) -> str | None:
    """Resolve the canonical work tree shared across worktrees.

    `git rev-parse --show-toplevel` returns the *worktree* path, not the
    main checkout — so two worktrees of the same repo would each get a
    different memory dir. The canonical root is derived from
    ``--git-common-dir``:

    1. If ``--git-common-dir`` ends in ``.git/worktrees/<name>``, walk up
       two levels to reach the main checkout's ``.git`` dir.
    2. If it ends in ``.git``, return its parent (the main work tree).
    3. Bare repo or other unusual layout: return ``None`` so callers can
       fall back.
    """
    cwd = str(start) if start else None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        common = result.stdout.strip()
        if not common:
            return None
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return None

    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = (Path(cwd) if cwd else Path.cwd()) / common_path
    common_path = common_path.resolve()

    parts = common_path.parts
    if (
        len(parts) >= 3
        and parts[-3] == ".git"
        and parts[-2] == "worktrees"
    ):
        # .git/worktrees/<name> → walk up to the main work tree
        return str(common_path.parents[2])
    if common_path.name == ".git":
        return str(common_path.parent)
    return None


def _get_project_root() -> str:
    """Best-effort current project root: canonical git root, else cwd."""
    canonical = find_canonical_git_root()
    if canonical:
        return canonical
    return os.getcwd()


def get_auto_mem_path() -> str:
    """Resolve the auto-memory directory.

    Memoizing here would be wrong for tests that switch project roots
    between calls; callers re-enter cheaply. Returns NFC-normalized path
    with a trailing separator.
    """
    override = _get_auto_mem_path_override()
    if override:
        return override
    base = get_memory_base_dir()
    project_root = _get_project_root()
    sanitized = sanitize_path(project_root)
    path = os.path.join(base, "projects", sanitized, _AUTO_MEM_DIRNAME) + os.sep
    return unicodedata.normalize("NFC", path)


def get_auto_mem_entrypoint() -> str:
    """Path to ``MEMORY.md`` inside the auto-memory dir."""
    return os.path.join(get_auto_mem_path(), _AUTO_MEM_ENTRYPOINT_NAME)


def get_auto_mem_daily_log_path(date: _date | None = None) -> str:
    """Path to a KAIROS daily log file (``logs/YYYY/MM/YYYY-MM-DD.md``).

    Helper is shipped for forward-compat; the KAIROS subsystem itself is
    deferred (Slice D in the refactor plan).
    """
    d = date or _date.today()
    yyyy = f"{d.year:04d}"
    mm = f"{d.month:02d}"
    dd = f"{d.day:02d}"
    return os.path.join(
        get_auto_mem_path(), "logs", yyyy, mm, f"{yyyy}-{mm}-{dd}.md"
    )


def is_auto_mem_path(absolute_path: str) -> bool:
    """Whether *absolute_path* is within the auto-memory directory.

    Normalizes before prefix-checking to defeat ``..`` traversal.
    """
    if not absolute_path:
        return False
    normalized = os.path.normpath(absolute_path)
    auto_mem = get_auto_mem_path()
    # get_auto_mem_path always ends in os.sep; ensure the comparison
    # also has the separator so "/foo/memory-evil/..." can't match
    # "/foo/memory/".
    return normalized.startswith(auto_mem) or normalized + os.sep == auto_mem
