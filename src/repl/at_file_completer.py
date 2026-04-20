"""``@`` file-mention completer for the REPL's prompt and live input.

Mirrors the TS Ink reference's ``@``-mention behavior
(``typescript/src/hooks/fileSuggestions.ts`` +
``typescript/src/hooks/useTypeahead.tsx``):

* Trigger on ``@`` at the start of a token (preceded by whitespace or
  beginning-of-line). The token after ``@`` is matched against a cached
  list of project files; matches are offered as completions that
  replace ``@<query>`` with ``@<path>`` in the buffer.
* The candidate list comes from ``git ls-files`` when the working
  directory sits inside a git repo (fast, gitignore-aware via git
  itself); otherwise we fall back to a bounded ``os.walk`` that skips
  the obvious heavyweight directories (``.git``, ``node_modules``,
  ``__pycache__`` …).
* Empty query (``@`` with nothing after) lists the top of the candidate
  set so the popup appears immediately on ``@``, matching the TS
  ``showOnEmpty`` path.
* The cache is rebuilt on a 5-second floor — short enough to pick up
  newly-created files in a typing session without spawning a git
  subprocess on every keystroke.

The class is intentionally framework-agnostic: it implements
``prompt_toolkit.completion.Completer`` so the same instance can plug
into the idle ``PromptSession`` and the live ``LiveStatus`` input
buffer used while the agent is working.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable

try:
    from prompt_toolkit.completion import Completer, Completion
except ModuleNotFoundError:  # pragma: no cover - prompt_toolkit guarded by REPL bootstrap
    class Completer:  # type: ignore[no-redef]
        pass

    class Completion:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass


# Same character class the TS reference uses for ``@`` tokens, minus the
# unicode property escapes (Python's ``re`` doesn't speak ``\p{L}``);
# ``\w`` already covers letters/digits/underscore for the common case
# and we add the punctuation set explicitly so paths like
# ``@~/foo/bar.py`` and ``@./src/utils.py`` complete.
_AT_TOKEN_CHAR = r"[\w\-./\\()\[\]~:]"
_AT_TOKEN_RE = re.compile(rf"@({_AT_TOKEN_CHAR}*)$")

# How long a cached file list is considered fresh before we re-run
# ``git ls-files`` / re-walk the tree.
_CACHE_TTL_SECONDS = 5.0

# Cap the number of suggestions we surface in the popup. Matches the TS
# ``MAX_SUGGESTIONS`` / ``MAX_UNIFIED_SUGGESTIONS`` (15) so the popup is
# never tall enough to clobber the transcript above the prompt.
_MAX_SUGGESTIONS = 15

# Directories we never descend into during the fallback walk. They're
# either VCS metadata, package caches, or build artefacts — none of
# which the user would reference with ``@`` in practice, and walking
# them on a JS/Python project blows up the candidate set.
_WALK_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".jj",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".turbo",
        ".cache",
        "target",
    }
)


class AtFileCompleter(Completer):
    """Completer that surfaces file paths after ``@``.

    Construct once per REPL session and pass to both the foreground
    ``PromptSession`` and any background ``LiveStatus`` buffer. The
    cache is shared between callers so the popup is instant the
    second time it's opened.
    """

    def __init__(
        self,
        cwd: str | os.PathLike[str] | None = None,
        *,
        max_suggestions: int = _MAX_SUGGESTIONS,
    ) -> None:
        self._cwd = Path(cwd or os.getcwd()).resolve()
        self._max_suggestions = max_suggestions
        self._cache: list[str] = []
        self._cache_built_at: float = 0.0

    # ---- public API ----
    def invalidate_cache(self) -> None:
        """Force the next ``get_completions`` call to rebuild the index.

        The cache is otherwise refreshed on a 5-second floor; this is
        an escape hatch for callers that know the workspace just
        changed (e.g. after a tool wrote new files).
        """

        self._cache = []
        self._cache_built_at = 0.0

    def set_cwd(self, cwd: str | os.PathLike[str]) -> None:
        new_cwd = Path(cwd).resolve()
        if new_cwd != self._cwd:
            self._cwd = new_cwd
            self.invalidate_cache()

    # ---- prompt_toolkit Completer interface ----
    def get_completions(
        self, document, complete_event
    ) -> Iterable["Completion"]:  # type: ignore[override]
        text = document.text_before_cursor
        match = _AT_TOKEN_RE.search(text)
        if match is None:
            return

        # Don't trigger on ``foo@bar`` (e.g. an email address) — the
        # ``@`` must be at the start of a token. The TS reference uses
        # the same rule.
        at_pos = match.start()
        if at_pos > 0 and not text[at_pos - 1].isspace():
            return

        query = match.group(1)
        replace_len = len(match.group(0))

        # Path-like tokens (``@/...``, ``@~/...``, ``@./...``,
        # ``@../...``) bypass the project-files index and walk the
        # filesystem directly so the user can reference any path on
        # disk — e.g. ``@/Users/me/Downloads/screenshot.png`` for
        # files outside the project. Mirrors the TS
        # ``isPathLikeToken`` branch in
        # ``typescript/src/utils/suggestions/directoryCompletion.ts``.
        if _is_path_like_token(query):
            for entry in _path_completions(query, self._max_suggestions):
                yield Completion(
                    text="@" + entry.text,
                    start_position=-replace_len,
                    display=entry.display,
                )
            return

        candidates = self._candidates()
        if not candidates:
            return

        matches = _filter_candidates(candidates, query, self._max_suggestions)
        # ``start_position`` is negative: how far back from the cursor
        # the replacement begins. We replace the ``@<query>`` span so
        # the result is ``@<path>`` (matching TS ``applyFileSuggestion``
        # which keeps the ``@`` prefix).
        for path in matches:
            yield Completion(
                text="@" + path,
                start_position=-replace_len,
                display=path,
            )

    # ---- internals ----
    def _candidates(self) -> list[str]:
        now = time.monotonic()
        if self._cache and (now - self._cache_built_at) < _CACHE_TTL_SECONDS:
            return self._cache

        paths = _list_git_files(self._cwd)
        if paths is None:
            paths = _walk_filesystem(self._cwd)

        # Sort stably so the popup ordering doesn't jump between
        # rebuilds when ``git ls-files`` happens to return entries in
        # a different order. Case-insensitive matches the typical
        # filesystem-browser feel.
        paths.sort(key=str.lower)
        self._cache = paths
        self._cache_built_at = now
        return self._cache


# ---- candidate gathering ----------------------------------------------------


def _list_git_files(cwd: Path) -> list[str] | None:
    """Return paths from ``git ls-files`` (tracked + untracked) or None.

    None means we're not inside a git repo (or ``git`` isn't on PATH);
    callers fall back to ``_walk_filesystem``.
    """

    git = _which("git")
    if git is None:
        return None

    # Confirm we're in a git work tree first — running ``ls-files``
    # outside one prints nothing on stdout and a complaint on stderr,
    # which we'd silently treat as "no files".
    try:
        rev = subprocess.run(
            [git, "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if rev.returncode != 0:
        return None

    repo_root = Path(rev.stdout.strip()).resolve()

    # ``--cached --others --exclude-standard`` = tracked + untracked
    # respecting ``.gitignore``. Matches the TS reference's union of
    # the tracked set and the background untracked fetch.
    try:
        result = subprocess.run(
            [
                git,
                "-c",
                "core.quotepath=false",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    raw = [line for line in result.stdout.split("\n") if line]
    if cwd == repo_root:
        return raw

    # Re-anchor paths so they're relative to the user's cwd (matches
    # TS ``normalizeGitPaths``).
    rel: list[str] = []
    for entry in raw:
        abs_path = repo_root / entry
        try:
            rel.append(os.path.relpath(abs_path, cwd))
        except ValueError:
            rel.append(entry)
    return rel


def _walk_filesystem(cwd: Path) -> list[str]:
    """Bounded walk for non-git directories.

    We cap the candidate set so a freshly-cloned monorepo or a home
    directory doesn't take seconds to enumerate.
    """

    out: list[str] = []
    cap = 5000
    for dirpath, dirnames, filenames in os.walk(str(cwd)):
        # Mutate dirnames in place so os.walk skips the heavyweight
        # directories on its next iteration.
        dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            try:
                rel = os.path.relpath(full, str(cwd))
            except ValueError:
                rel = full
            out.append(rel)
            if len(out) >= cap:
                return out
    return out


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


# ---- path-like completion ---------------------------------------------------


class _PathSuggestion:
    """Result of one filesystem-walk suggestion.

    ``text`` is what we splice into the buffer (preserves the user's
    input shape — relative tokens stay relative, absolute stay
    absolute, ``~/`` stays expanded out of the home prefix). ``display``
    is what the popup shows. Directories get a trailing ``/`` so the
    user can keep typing to traverse deeper without re-pressing ``@``.
    """

    __slots__ = ("text", "display")

    def __init__(self, text: str, display: str) -> None:
        self.text = text
        self.display = display


_PATH_LIKE_PREFIXES = ("/", "~/", "./", "../")


def _is_path_like_token(token: str) -> bool:
    """Mirror TS ``isPathLikeToken`` so absolute / explicit-relative
    paths bypass the project-files index and walk the filesystem
    directly."""

    if token in ("~", ".", ".."):
        return True
    return any(token.startswith(p) for p in _PATH_LIKE_PREFIXES)


def _path_completions(query: str, limit: int) -> list[_PathSuggestion]:
    """List directory entries matching ``query`` as a partial path.

    Splits ``query`` into ``dirname`` + ``basename``; lists entries
    in ``dirname`` whose name starts with ``basename`` (case
    insensitive). The returned ``text`` echoes the original
    ``dirname`` exactly — including ``~`` and trailing ``/`` — so a
    completion of ``@~/Doc`` lands as ``@~/Documents/`` in the buffer
    rather than the home-expanded absolute form.
    """

    expanded = os.path.expanduser(query)
    if query.endswith("/"):
        directory = expanded
        prefix = ""
    elif query in ("~", ".", ".."):
        # The bare token is a directory itself; list its contents.
        directory = expanded
        prefix = ""
    else:
        directory = os.path.dirname(expanded) or "."
        prefix = os.path.basename(expanded)

    try:
        entries = os.listdir(directory)
    except (OSError, PermissionError):
        return []

    # The visible portion of ``query`` we keep when splicing the
    # completed name back in. Strip the basename so completions
    # replace the partial filename, not the parent directory.
    if query.endswith("/") or query in ("~", ".", ".."):
        retained = query if query.endswith("/") else query + "/"
    else:
        # Find the last separator in the original (un-expanded) query
        # and keep everything up to and including it.
        sep_idx = query.rfind("/")
        retained = query[: sep_idx + 1] if sep_idx >= 0 else ""

    # Case-insensitive prefix match on the basename. Hidden entries
    # are surfaced only when the user explicitly types a dot prefix.
    show_hidden = prefix.startswith(".")
    prefix_lower = prefix.lower()

    matches: list[tuple[bool, str]] = []
    for name in entries:
        if not show_hidden and name.startswith("."):
            continue
        if prefix_lower and not name.lower().startswith(prefix_lower):
            continue
        full = os.path.join(directory, name)
        is_dir = os.path.isdir(full)
        matches.append((is_dir, name))

    # Directories first, then alphabetical case-insensitive.
    matches.sort(key=lambda t: (not t[0], t[1].lower()))

    out: list[_PathSuggestion] = []
    for is_dir, name in matches[:limit]:
        suffix = "/" if is_dir else ""
        out.append(_PathSuggestion(text=retained + name + suffix, display=name + suffix))
    return out


# ---- ranking ----------------------------------------------------------------


def _filter_candidates(paths: list[str], query: str, limit: int) -> list[str]:
    """Rank ``paths`` against ``query`` and return the top ``limit``.

    Empty query → return the first ``limit`` paths so the popup
    appears immediately on ``@`` (TS ``showOnEmpty``).

    Non-empty query → score each path on three signals (in order):
    1. Exact substring of basename, prefix preferred.
    2. Exact substring of full path, position preferred.
    3. Subsequence match (each query char appears in order).
    Paths failing all three are dropped.
    """

    if not query:
        return paths[:limit]

    q = query.lower()
    scored: list[tuple[int, int, str]] = []
    for path in paths:
        lower = path.lower()
        base = os.path.basename(lower)

        if q in base:
            # Lower score wins; basename prefix beats basename middle.
            scored.append((0, base.find(q), path))
            continue
        if q in lower:
            scored.append((1, lower.find(q), path))
            continue
        sub_score = _subsequence_score(lower, q)
        if sub_score is not None:
            scored.append((2, sub_score, path))

    scored.sort(key=lambda t: (t[0], t[1], t[2].lower()))
    return [path for _, _, path in scored[:limit]]


def _subsequence_score(text: str, query: str) -> int | None:
    """Return the index span of the first subsequence match, or None.

    Lower spans = more compact matches = better. ``"src/repl.py"`` vs
    query ``"srpy"`` matches at positions 0,1,9,10 → span 11. The same
    query against ``"py"`` doesn't match (missing ``s``,``r``).
    """

    ti = 0
    first: int | None = None
    last = 0
    for ch in query:
        found = text.find(ch, ti)
        if found == -1:
            return None
        if first is None:
            first = found
        last = found
        ti = found + 1
    if first is None:
        return None
    return last - first
