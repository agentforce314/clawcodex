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
import threading
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


# WI-3.1: 26-bit bitmap pre-filter. Each indexed path gets a 26-bit int
# encoding which lowercase letters (a-z) it contains. At search time, the
# query gets the same encoding and ``(path_bits & needle_bits) ==
# needle_bits`` is a 1-instruction reject for any path missing a letter.
# Mirrors TS ``native-ts/file-index/index.ts:156-167,210``.
#
# Cost: 8 bytes per path on 64-bit Python (vs TS's 4 bytes; arbitrary-
# precision int has overhead). At 270K paths that's ~2 MB — trivial.
# Win: 10-90% rejection depending on query letter rarity.


def _build_path_bitmap(path: str) -> int:
    """Pack lowercase a-z presence into a 26-bit integer.

    Each set bit corresponds to a letter present somewhere in ``path``.
    Non-letter characters (digits, punctuation, slashes) don't contribute.
    """
    mask = 0
    for ch in path.lower():
        code = ord(ch)
        if 97 <= code <= 122:  # 'a' .. 'z'
            mask |= 1 << (code - 97)
    return mask


# WI-3.2: yield-cadence constant. The branchless ``i & 0xff == 0xff`` check
# fires every 256 iterations to amortize ``time.perf_counter`` cost (mirrors
# TS at ``native-ts/file-index/index.ts:109,121``). After the check, if
# more than ``_INDEX_YIELD_INTERVAL_S`` has elapsed we ``time.sleep(0)`` to
# release the GIL and let the foreground thread (``get_completions``)
# observe the partial index.
_INDEX_CHUNK_SIZE = 256
_INDEX_YIELD_INTERVAL_S = 0.004  # 4 ms — chapter §"Async Indexing"

# Bounded wait on the first chunk when get_completions is called before
# the background warm-up has published anything. Keep this small enough
# that the user perceives the popup as instant; large enough that small
# workspaces (which walk in microseconds) always come back populated.
_FIRST_CHUNK_WAIT_S = 0.5


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
        # WI-3.1: parallel list of 26-bit bitmaps, one per ``self._cache``
        # entry. Same length as ``_cache``; index aligned. Read by
        # ``_filter_candidates`` to pre-filter paths missing query letters.
        self._cache_bitmaps: list[int] = []
        self._cache_built_at: float = 0.0
        # WI-3.2: thread-based async indexing primitives. ``_index_lock``
        # guards mutations to the cache lists during background warming.
        # ``_index_queryable_event`` fires when the first chunk is
        # indexed (allowing partial-results queries); ``_index_done_event``
        # fires when the full walk completes. ``_index_thread`` is the
        # background worker; None when no warming is in flight.
        # ``_index_generation`` is incremented by ``invalidate_cache`` so
        # in-flight worker threads (which captured an earlier generation
        # at start) abort their next publish (per Phase 3 critic m1 —
        # without this, ``set_cwd`` mid-warm-up produces duplicate cache
        # entries).
        self._index_lock = threading.Lock()
        self._index_queryable_event = threading.Event()
        self._index_done_event = threading.Event()
        self._index_thread: threading.Thread | None = None
        self._index_generation: int = 0

    # ---- public API ----
    def invalidate_cache(self) -> None:
        """Force the next ``get_completions`` call to rebuild the index.

        The cache is otherwise refreshed on a 5-second floor; this is
        an escape hatch for callers that know the workspace just
        changed (e.g. after a tool wrote new files).
        """

        with self._index_lock:
            self._cache = []
            self._cache_bitmaps = []
            self._cache_built_at = 0.0
            self._index_queryable_event.clear()
            self._index_done_event.clear()
            # Bump the generation so any in-flight worker thread sees
            # its captured generation diverge and aborts its next publish
            # (per Phase 3 critic m1). Without this, two threads racing
            # to fill the cache would publish duplicates.
            self._index_generation += 1
            self._index_thread = None

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

        # WI-3.2: trigger async warm-up if needed; read whatever is
        # indexed so far. Returns a snapshot under lock so a concurrent
        # background-thread mutation can't race the filter loop.
        candidates, bitmaps = self._candidates_snapshot()
        if not candidates:
            return

        matches = _filter_candidates(
            candidates, query, self._max_suggestions, bitmaps=bitmaps,
        )
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
    def _candidates_snapshot(self) -> tuple[list[str], list[int]]:
        """Return whatever portion of the index has been built so far.

        WI-3.2: kicks off a background-thread warm-up if the cache is
        stale and no warm-up is in flight; briefly waits for the first
        chunk so a popup that opens before the index is warm doesn't
        come back empty. Returns a snapshot of the current
        ``(_cache, _cache_bitmaps)`` lists (under lock so the background
        thread's mutations don't race).

        The bounded wait (default ``_FIRST_CHUNK_WAIT_S``) trades a tiny
        amount of UI latency for deterministic behavior: tests calling
        ``get_completions`` immediately after construction get the index;
        users on tiny workspaces (<5000 files) see instant popups; users
        on huge monorepos see a sub-100ms first-keystroke delay then
        progressively-richer results on subsequent keystrokes.

        Calls between ``_index_queryable_event`` and ``_index_done_event``
        return PARTIAL results from the first chunk(s); calls after
        ``_index_done_event`` return the complete index.
        """
        now = time.monotonic()
        # Fast path: cache fresh, full index ready, no warm-up needed.
        if (
            self._cache
            and self._index_done_event.is_set()
            and (now - self._cache_built_at) < _CACHE_TTL_SECONDS
        ):
            return self._cache, self._cache_bitmaps

        # Cache stale (or never built): kick off warm-up if not already running.
        self._start_index_warm_if_needed()

        # Wait briefly for at least the first chunk so a popup doesn't
        # come back empty just because the user typed @ before the
        # background thread had time to publish anything. The cap is
        # conservative — workspaces small enough to walk synchronously
        # finish well under it; huge monorepos publish their first
        # chunk within the cap and the user sees partial results.
        if not self._index_queryable_event.is_set():
            self._index_queryable_event.wait(timeout=_FIRST_CHUNK_WAIT_S)

        # Read whatever's been indexed so far, under lock so the
        # background thread can't tear the snapshot mid-read.
        with self._index_lock:
            return list(self._cache), list(self._cache_bitmaps)

    def _start_index_warm_if_needed(self) -> None:
        """Spawn the background indexing thread if one isn't already running."""
        if self._index_thread is not None and self._index_thread.is_alive():
            return
        self._index_queryable_event.clear()
        self._index_done_event.clear()
        self._index_thread = threading.Thread(
            target=self._build_index, name="at-file-completer-index",
            daemon=True,
        )
        self._index_thread.start()

    def _build_index(self) -> None:
        """Background worker: walk the file list, build bitmaps, populate cache.

        Yields to the event loop every ~256 paths if 4 ms of wall-clock
        time has passed since the last yield. Foreground ``get_completions``
        calls will see partial results once the first chunk lands.

        Captures ``self._index_generation`` at start; aborts publishing
        if the captured value diverges (i.e., ``invalidate_cache`` ran
        while we were walking). Per Phase 3 critic m1.
        """
        # Snapshot the generation. If invalidate_cache bumps it during
        # our walk, our publishes are no-ops and we exit cleanly.
        my_generation = self._index_generation
        try:
            paths = _list_git_files(self._cwd)
            if paths is None:
                paths = _walk_filesystem(self._cwd)

            # Sort stably so the popup ordering doesn't jump between
            # rebuilds. Case-insensitive matches the typical
            # filesystem-browser feel.
            paths.sort(key=str.lower)

            # Build bitmaps in a single pass; yield to the GIL every 256
            # iterations if the foreground might be waiting (4 ms cap on
            # how long the worker holds the GIL between yield checks).
            chunk_paths: list[str] = []
            chunk_bitmaps: list[int] = []
            chunk_start = time.perf_counter()
            for i, path in enumerate(paths):
                chunk_paths.append(path)
                chunk_bitmaps.append(_build_path_bitmap(path))
                # Branchless modulo-256 — same trick as TS.
                if (i & 0xff) == 0xff:
                    now = time.perf_counter()
                    if (now - chunk_start) > _INDEX_YIELD_INTERVAL_S:
                        # Publish what we have so far so foreground reads
                        # see partial results. Skip the publish (and
                        # exit) if we've been orphaned by invalidate_cache.
                        with self._index_lock:
                            if self._index_generation != my_generation:
                                return  # orphaned — abort cleanly
                            self._cache.extend(chunk_paths)
                            self._cache_bitmaps.extend(chunk_bitmaps)
                        chunk_paths = []
                        chunk_bitmaps = []
                        # Signal queryable on first publish; subsequent
                        # publishes are visible via the lock.
                        if not self._index_queryable_event.is_set():
                            self._index_queryable_event.set()
                        # Release the GIL so the foreground can read.
                        time.sleep(0)
                        chunk_start = time.perf_counter()

            # Publish any remaining tail (still gated by generation).
            if chunk_paths:
                with self._index_lock:
                    if self._index_generation != my_generation:
                        return
                    self._cache.extend(chunk_paths)
                    self._cache_bitmaps.extend(chunk_bitmaps)

            # Mark done. Queryable is implicitly set too (otherwise we
            # never published anything — empty workspace). Skip the
            # done-flip if we've been orphaned (a newer thread will
            # signal it).
            with self._index_lock:
                if self._index_generation != my_generation:
                    return
                self._cache_built_at = time.monotonic()
            self._index_queryable_event.set()
            self._index_done_event.set()
        except Exception:
            # Indexing failures must never break the REPL. Mark done so
            # subsequent calls fall through cleanly with an empty cache;
            # the next invalidate_cache() will retry. Still gate on
            # generation so we don't trample a newer thread's state.
            with self._index_lock:
                if self._index_generation != my_generation:
                    return
                self._cache_built_at = time.monotonic()
            self._index_queryable_event.set()
            self._index_done_event.set()


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


def _filter_candidates(
    paths: list[str],
    query: str,
    limit: int,
    *,
    bitmaps: list[int] | None = None,
) -> list[str]:
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

    # WI-3.1: build the needle bitmap once. The per-path bitmaps come
    # from the ``bitmaps`` parallel list when callers (the production
    # ``AtFileCompleter`` path) provide them; legacy callers passing
    # only ``paths`` still work — we just skip the pre-filter and pay
    # the full inner-match cost (same behavior as before WI-3.1).
    needle_bitmap = _build_path_bitmap(q) if bitmaps is not None else 0

    # WI-3.3: score-bound rejection threshold. Top-K is held as the
    # ``limit``-th best score seen so far; once filled, any candidate
    # whose tier-0 best case can't beat it is skipped before the
    # expensive subsequence check. Tier 0 (basename substring) is the
    # cheapest signal so that's the upper bound; if a path has any
    # query letter missing (bitmap-rejected) or its best-tier-0 score
    # is worse than the top-K threshold, skip the inner match entirely.
    scored: list[tuple[int, int, str]] = []
    # Top-K threshold tracked as ``(tier, position)``; ``None`` means
    # under-filled (every candidate is a contender).
    top_k_threshold: tuple[int, int] | None = None

    for i, path in enumerate(paths):
        # WI-3.1: bitmap pre-filter. One integer AND. If the path is
        # missing any query letter, reject without entering the inner
        # match. ~10-90% rejection on typical queries.
        if bitmaps is not None:
            path_bitmap = bitmaps[i]
            if (path_bitmap & needle_bitmap) != needle_bitmap:
                continue

        lower = path.lower()
        base = os.path.basename(lower)

        if q in base:
            entry = (0, base.find(q), path)
            scored.append(entry)
            if top_k_threshold is None or len(scored) >= limit:
                top_k_threshold = _maybe_tighten_threshold(
                    scored, limit, top_k_threshold,
                )
            continue
        if q in lower:
            entry = (1, lower.find(q), path)
            # WI-3.3: skip if tier-1 entry can't beat the current top-K.
            if top_k_threshold is not None and (entry[0], entry[1]) >= top_k_threshold:
                # Even an exact tier-1 match at position 0 won't beat a
                # full top-K of tier-0 entries.
                if entry[0] > top_k_threshold[0]:
                    continue
            scored.append(entry)
            if top_k_threshold is None or len(scored) >= limit:
                top_k_threshold = _maybe_tighten_threshold(
                    scored, limit, top_k_threshold,
                )
            continue
        # WI-3.3: skip the expensive subsequence scan when we already
        # have top-K full of tier-0/tier-1 hits — a tier-2 entry can
        # never beat a tier-0 or tier-1 in the final sort.
        if (
            top_k_threshold is not None
            and top_k_threshold[0] < 2
            and len(scored) >= limit
        ):
            continue
        sub_score = _subsequence_score(lower, q)
        if sub_score is not None:
            scored.append((2, sub_score, path))
            if top_k_threshold is None or len(scored) >= limit:
                top_k_threshold = _maybe_tighten_threshold(
                    scored, limit, top_k_threshold,
                )

    scored.sort(key=lambda t: (t[0], t[1], t[2].lower()))
    return [path for _, _, path in scored[:limit]]


def _maybe_tighten_threshold(
    scored: list[tuple[int, int, str]],
    limit: int,
    current: tuple[int, int] | None,
) -> tuple[int, int] | None:
    """Recompute the top-K threshold when ``scored`` may have grown past ``limit``.

    Returns the new threshold (the worst (tier, position) among the top
    ``limit`` entries) or ``None`` if ``scored`` is still under-filled.

    Cheap-but-not-free: O(n log n) sort. Called only when ``scored`` has
    grown to ``limit`` or beyond — at which point further inner-match
    work is already gated by the threshold.
    """
    if len(scored) < limit:
        return None
    # Sort by tier then position; the ``limit``-th entry is the worst
    # we'll accept. Higher (tier, position) tuples lose under
    # min-tuple sort.
    sorted_scores = sorted((s[0], s[1]) for s in scored)
    new_threshold = sorted_scores[limit - 1]
    if current is None or new_threshold < current:
        return new_threshold
    return current


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
