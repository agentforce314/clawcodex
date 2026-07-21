"""Bounded curated memory store — MEMORY.md / USER.md with file persistence.

Port of ``reference_projects/hermes-agent/tools/memory_tool.py``'s
``MemoryStore`` (the write-path engineering documented in
``my-docs/memory-and-self-improvement/02-memory-tool.md``). Two stores:

* ``MEMORY.md`` — the agent's notes (environment facts, conventions, tool
  quirks, lessons). Default budget 2,200 chars (~800 tokens).
* ``USER.md`` — the user model (name, role, preferences, style). Default
  budget 1,375 chars (~500 tokens).

Both are injected into the system prompt as a **frozen snapshot** captured
at ``load_from_disk()`` time. Mid-session writes update the files on disk
immediately (durable) but do NOT change the captured snapshot — the system
prompt stays byte-stable for the session, preserving provider prefix
caches.

Snapshot freshness contract (differs mechanically from the donor, same
semantics): the prompt section (``prompt_assembly._build_memory_store_
section``) calls ``load_from_disk()`` inline at every prompt **build**, so
reload is structurally coupled to rebuild — the donor's explicit
``invalidate_system_prompt() → load_from_disk()`` pairing without the
must-remember-to-call hazard. Prompt builds happen only at cache-boundary
events (session init, /clear, /resume, provider/model/output-style
switches), and every builder caller MUST cache the built prompt for the
session span between those events; a caller that rebuilt per turn would
both recapture the snapshot per turn and thrash the provider prefix cache.

Entry delimiter: ``§`` (section sign), full 3-char form ``"\\n§\\n"`` so a
literal § inside an entry doesn't split it. Character limits (not tokens)
because char counts are model-independent.

Donor invariants kept:
* exclusive ``.lock`` sidecar + reload-under-lock so concurrent sessions
  compose; atomic temp-file + fsync + ``os.replace`` writes so readers
  never see a truncated file;
* external-drift guard: refuse full-file rewrites when the on-disk content
  wouldn't round-trip (or an entry exceeds the whole-store budget) and
  snapshot to ``.bak.<ts>`` first — silent data loss prevention
  (donor issue #26045). ``add`` appends and skips the guard;
* batch ops are all-or-nothing, budgeted against the FINAL state only;
* success responses are terminal (anti-thrash); the full entry inventory
  rides only on error paths, where the model needs it to consolidate;
* every write is threat-scanned at ``strict`` scope; snapshot entries are
  re-scanned at build time and replaced with ``[BLOCKED: …]`` placeholders
  (live state keeps the raw text so the user can inspect + remove).
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.utils.clawcodex_dirs import get_user_config_dir

from .threat_patterns import first_threat_message, scan_for_threats

# fcntl is Unix-only; on Windows use msvcrt for file locking.
msvcrt = None
try:
    import fcntl
except ImportError:  # pragma: no cover - platform-dependent
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass

logger = logging.getLogger(__name__)

ENTRY_DELIMITER = "\n§\n"

DEFAULT_MEMORY_CHAR_LIMIT = 2200
DEFAULT_USER_CHAR_LIMIT = 1375

VALID_TARGETS = ("memory", "user")


def get_memory_dir() -> Path:
    """The memories directory, resolved dynamically on every access.

    Dynamic (not a module constant) so ``$CLAWCODEX_CONFIG_DIR`` overrides
    — tests, profile switches — are always respected; the donor records a
    stale-module-constant bug from exactly this (memory_tool.py:51-54).
    """
    return get_user_config_dir() / "memories"


def _scan_memory_content(content: str) -> str | None:
    """Scan content bound for a memory file. Returns an error string to
    block the write, or None. ``strict`` scope: memory is user-curated (a
    false positive is fixable by rewording) and enters the system prompt as
    a frozen snapshot, so a poisoned entry would persist across sessions.

    Known tradeoff (design-accepted): strict patterns can false-positive on
    legitimate config-workflow facts (e.g. anything phrased "update …
    CLAWCODEX.md"). A foreground write surfaces the error so the model can
    reword; a background-review write is silently dropped (the fork
    swallows rejections) — aggressive scanning wins because this content
    persists into every future system prompt.
    """
    return first_threat_message(content, scope="strict")


def _drift_error(path: Path, bak_path: str) -> dict[str, Any]:
    """Error dict returned when external drift is detected. The on-disk
    file contains content that wouldn't round-trip through the parser —
    flushing would discard it (patch tool, shell append, manual edit, or
    sister-session write). Refuse, point at the ``.bak`` snapshot."""
    return {
        "success": False,
        "error": (
            f"Refusing to write {path.name}: file on disk has content that "
            f"wouldn't round-trip through the memory tool (likely added by "
            f"an editor, a shell append, or a concurrent session). A "
            f"snapshot was saved to {bak_path}. Resolve the drift first — "
            f"either rewrite the file as a clean §-delimited list of "
            f"entries, or move the extra content out — then retry. This "
            f"guard exists to prevent silent data loss."
        ),
        "drift_backup": bak_path,
        "remediation": (
            "Open the .bak file, integrate the missing entries into the "
            "memory tool one at a time via Memory(action=add, content=...), "
            "then remove or rewrite the original file to a clean state."
        ),
    }


class MemoryStore:
    """Bounded curated memory with file persistence.

    Maintains two parallel states:

    * ``_system_prompt_snapshot`` — frozen at :meth:`load_from_disk`, used
      for system-prompt injection. Never mutated mid-session.
    * ``memory_entries`` / ``user_entries`` — live state, mutated by tool
      calls, persisted to disk immediately. Tool responses reflect this.
    """

    def __init__(
        self,
        memory_char_limit: int = DEFAULT_MEMORY_CHAR_LIMIT,
        user_char_limit: int = DEFAULT_USER_CHAR_LIMIT,
    ) -> None:
        self.memory_entries: list[str] = []
        self.user_entries: list[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # Frozen snapshot for the system prompt — set at load_from_disk().
        self._system_prompt_snapshot: dict[str, str] = {"memory": "", "user": ""}

    # ── loading & snapshot ────────────────────────────────────────────

    def load_from_disk(self) -> None:
        """Load entries from MEMORY.md / USER.md and capture the frozen
        system-prompt snapshot.

        Every entry is threat-scanned at snapshot-build time: any hit is
        replaced *in the snapshot only* with a ``[BLOCKED: …]`` placeholder
        so a poisoned-on-disk file (supply chain, sister-session write,
        direct edit) cannot inject into the system prompt. Live state keeps
        the raw text so the user can inspect and remove the entry —
        silently dropping it would hide the attack. Scanning is
        deterministic from disk bytes, so the snapshot stays stable for the
        session (prefix-cache invariant holds).
        """
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")

        # Deduplicate (order-preserving, first occurrence wins).
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        sanitized_memory = self._sanitize_entries_for_snapshot(
            self.memory_entries, "MEMORY.md"
        )
        sanitized_user = self._sanitize_entries_for_snapshot(
            self.user_entries, "USER.md"
        )

        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", sanitized_memory),
            "user": self._render_block("user", sanitized_user),
        }

    @staticmethod
    def _sanitize_entries_for_snapshot(
        entries: list[str], filename: str
    ) -> list[str]:
        """Replace threat-matching entries with ``[BLOCKED: …]`` placeholders
        (snapshot copy only; live state keeps the raw entry).

        Hardening over the donor (design-critic follow-up): entries are
        scanned even when they already carry a ``[BLOCKED:`` prefix — the
        donor skips those, which lets a direct file writer smuggle a
        payload past the load-time scan by prefixing it. Our own
        placeholders scan clean (pattern IDs don't match their own
        regexes), so re-scanning them is a no-op.
        """
        sanitized: list[str] = []
        for entry in entries:
            if not entry:
                sanitized.append(entry)
                continue
            findings = scan_for_threats(entry, scope="strict")
            if findings:
                logger.warning(
                    "Memory entry from %s blocked at load time: %s",
                    filename, ", ".join(findings),
                )
                sanitized.append(
                    f"[BLOCKED: {filename} entry contained threat pattern(s): "
                    f"{', '.join(findings)}. Removed from system prompt; "
                    f"use Memory(action=remove) to delete the original.]"
                )
            else:
                sanitized.append(entry)
        return sanitized

    def format_for_system_prompt(self, target: str) -> str | None:
        """The frozen snapshot for system-prompt injection — the state at
        :meth:`load_from_disk` time, NOT live state. None when empty."""
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # ── locking & paths ───────────────────────────────────────────────

    @staticmethod
    @contextmanager
    def _file_lock(path: Path) -> Iterator[None]:
        """Exclusive lock on a ``.lock`` sidecar for read-modify-write
        safety (a sidecar so the data file itself can still be atomically
        replaced via ``os.replace``)."""
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None and msvcrt is None:  # pragma: no cover - exotic platform
            yield
            return

        fd = open(lock_path, "a+", encoding="utf-8")
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:  # pragma: no cover - Windows
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            elif msvcrt:  # pragma: no cover - Windows
                try:
                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            fd.close()

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = get_memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str, *, skip_drift: bool = False) -> str | None:
        """Re-read entries from disk into live state (called under the file
        lock before mutating, so concurrent-session writes compose).

        Returns the backup path when external drift was detected — the
        caller must abort the mutation. ``skip_drift=True`` bypasses the
        check (used by ``add``, which appends without rewriting existing
        content)."""
        path = self._path_for(target)
        bak = None if skip_drift else self._detect_external_drift(target)
        fresh = list(dict.fromkeys(self._read_file(path)))
        self._set_entries(target, fresh)
        return bak

    def save_to_disk(self, target: str) -> None:
        """Persist entries for ``target``. Called after every mutation."""
        get_memory_dir().mkdir(parents=True, exist_ok=True)
        self._write_file(self._path_for(target), self._entries_for(target))

    # ── entry accessors ───────────────────────────────────────────────

    def _entries_for(self, target: str) -> list[str]:
        if target == "user":
            return self.user_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: list[str]) -> None:
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        return self.memory_char_limit

    # ── mutations ─────────────────────────────────────────────────────

    def add(self, target: str, content: str) -> dict[str, Any]:
        """Append a new entry. Over-budget adds fail with the full entry
        inventory so the model can consolidate and retry in one turn."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            # Append-only: skip the drift guard — appending never clobbers
            # existing content, so add keeps working against a drifted file
            # while full-file rewrites (replace/remove/batch) are blocked.
            self._reload_target(target, skip_drift=True)

            entries = self._entries_for(target)
            limit = self._char_limit(target)

            if content in entries:
                return self._success_response(
                    target, "Entry already exists (no duplicate added)."
                )

            new_total = len(ENTRY_DELIMITER.join(entries + [content]))
            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(content)} chars) would exceed the limit. "
                        f"Consolidate now: use 'replace' to merge overlapping entries into "
                        f"shorter ones or 'remove' stale or less important entries (see "
                        f"current_entries below), then retry this add — all in this turn."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(content)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry added.")

    def replace(self, target: str, old_text: str, new_content: str) -> dict[str, Any]:
        """Replace the entry containing the ``old_text`` substring."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {
                "success": False,
                "error": "content cannot be empty. Use 'remove' to delete entries.",
            }

        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            bak = self._reload_target(target)
            if bak:
                return _drift_error(self._path_for(target), bak)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # Identical duplicates: operate on the first. Distinct
                # matches: ambiguous — show previews, ask to be specific.
                unique_texts = {e for _, e in matches}
                if len(unique_texts) > 1:
                    previews = [
                        e[:80] + ("..." if len(e) > 80 else "") for _, e in matches
                    ]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }

            idx = matches[0][0]
            limit = self._char_limit(target)

            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))
            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content, or 'remove' other stale or less important "
                        f"entries to make room (see current_entries below), then retry — all "
                        f"in this turn."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries[idx] = new_content
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> dict[str, Any]:
        """Remove the entry containing the ``old_text`` substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            bak = self._reload_target(target)
            if bak:
                return _drift_error(self._path_for(target), bak)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                unique_texts = {e for _, e in matches}
                if len(unique_texts) > 1:
                    previews = [
                        e[:80] + ("..." if len(e) > 80 else "") for _, e in matches
                    ]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }

            entries.pop(matches[0][0])
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry removed.")

    def apply_batch(self, target: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply a sequence of add/replace/remove ops atomically.

        All-or-nothing, budgeted against the FINAL state — intermediate
        overflow is irrelevant. Lets the model free space and add new
        entries in ONE call instead of the multi-turn consolidate-then-retry
        dance. Any malformed op, failed match, or ambiguous substring aborts
        the whole batch with the live state attached.
        """
        if not operations:
            return {"success": False, "error": "operations list is empty."}

        # Scan every add/replace content BEFORE touching disk — a single
        # poisoned op rejects the whole batch.
        for i, op in enumerate(operations):
            act = (op or {}).get("action")
            new_content = (op or {}).get("content")
            if act in {"add", "replace"} and new_content:
                scan_error = _scan_memory_content(new_content)
                if scan_error:
                    return {"success": False, "error": f"Operation {i + 1}: {scan_error}"}

        with self._file_lock(self._path_for(target)):
            bak = self._reload_target(target)
            if bak:
                return _drift_error(self._path_for(target), bak)

            working: list[str] = list(self._entries_for(target))
            limit = self._char_limit(target)

            for i, op in enumerate(operations):
                op = op or {}
                act = op.get("action")
                content = (op.get("content") or "").strip()
                old_text = (op.get("old_text") or "").strip()
                pos = f"Operation {i + 1} ({act or 'unknown'})"

                if act == "add":
                    if not content:
                        return self._batch_error(target, f"{pos}: content is required.")
                    if content in working:
                        continue  # idempotent — skip duplicate, don't fail the batch
                    working.append(content)

                elif act == "replace":
                    if not old_text:
                        return self._batch_error(target, f"{pos}: old_text is required.")
                    if not content:
                        return self._batch_error(
                            target,
                            f"{pos}: content is required (use action='remove' to delete).",
                        )
                    matches = [j for j, e in enumerate(working) if old_text in e]
                    if not matches:
                        return self._batch_error(target, f"{pos}: no entry matched '{old_text}'.")
                    if len({working[j] for j in matches}) > 1:
                        return self._batch_error(
                            target,
                            f"{pos}: '{old_text}' matched multiple distinct entries -- be more specific.",
                        )
                    working[matches[0]] = content

                elif act == "remove":
                    if not old_text:
                        return self._batch_error(target, f"{pos}: old_text is required.")
                    matches = [j for j, e in enumerate(working) if old_text in e]
                    if not matches:
                        return self._batch_error(target, f"{pos}: no entry matched '{old_text}'.")
                    if len({working[j] for j in matches}) > 1:
                        return self._batch_error(
                            target,
                            f"{pos}: '{old_text}' matched multiple distinct entries -- be more specific.",
                        )
                    working.pop(matches[0])

                else:
                    return self._batch_error(
                        target,
                        f"{pos}: unknown action. Use add, replace, or remove.",
                    )

            new_total = len(ENTRY_DELIMITER.join(working)) if working else 0
            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"After applying all {len(operations)} operations, memory would be at "
                        f"{new_total:,}/{limit:,} chars -- over the limit. Remove or shorten more "
                        f"entries in the same batch (see current_entries below), then retry."
                    ),
                    "current_entries": self._entries_for(target),
                    "usage": f"{current:,}/{limit:,}",
                }

            self._set_entries(target, working)
            self.save_to_disk(target)

        return self._success_response(target, f"Applied {len(operations)} operation(s).")

    def _batch_error(self, target: str, message: str) -> dict[str, Any]:
        """Batch-abort error reporting live (uncommitted) state."""
        current = self._char_count(target)
        limit = self._char_limit(target)
        return {
            "success": False,
            "error": message + " No operations were applied (batch is all-or-nothing).",
            "current_entries": self._entries_for(target),
            "usage": f"{current:,}/{limit:,}",
        }

    # ── responses & rendering ─────────────────────────────────────────

    def _success_response(self, target: str, message: str | None = None) -> dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        # Intentionally TERMINAL: confirms the write landed and tells the
        # model to stop. Do NOT echo the entries list — dumping it invites
        # the model to "find more to fix" and re-issue the same operations
        # (donor observed 5 redundant repeat batches). Entries ride only on
        # error paths, where the model genuinely needs them.
        resp: dict[str, Any] = {
            "success": True,
            "done": True,
            "target": target,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        resp["note"] = "Write saved. This update is complete — do not repeat it."
        return resp

    def _render_block(self, target: str, entries: list[str]) -> str:
        """Render a system-prompt block with header + usage indicator."""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    # ── file I/O ──────────────────────────────────────────────────────

    @staticmethod
    def _read_file(path: Path) -> list[str]:
        """Read a memory file and split into entries. No lock needed:
        ``_write_file`` renames atomically, so readers always see either
        the previous complete file or the new one."""
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []

        if not raw.strip():
            return []

        # Split on the full delimiter — a bare "§" inside an entry stays.
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    def _detect_external_drift(self, target: str) -> str | None:
        """Return a backup-path string when on-disk content shows external
        drift. Two signals:

        1. Round-trip mismatch — re-parsing + re-serializing doesn't
           reproduce the on-disk bytes.
        2. Entry-size overflow — a single parsed entry exceeds the whole
           store's char limit (no tool-written entry can; an external
           writer appended free-form content the parser lumped together).

        On drift: snapshot to ``<file>.bak.<unix-ts>`` and return its path
        so the caller refuses the mutation.
        """
        path = self._path_for(target)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        if not raw.strip():
            return None

        parsed = [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]
        roundtrip = ENTRY_DELIMITER.join(parsed)

        char_limit = self._char_limit(target)
        max_entry_len = max((len(e) for e in parsed), default=0)

        drift_detected = (raw.strip() != roundtrip) or (max_entry_len > char_limit)
        if not drift_detected:
            return None

        ts = int(time.time())
        bak_path = path.with_suffix(path.suffix + f".bak.{ts}")
        try:
            bak_path.write_text(raw, encoding="utf-8")
        except OSError:
            return str(bak_path) + " (BACKUP FAILED — file unchanged on disk)"
        return str(bak_path)

    @staticmethod
    def _write_file(path: Path, entries: list[str]) -> None:
        """Atomic temp-file + fsync + rename write. (A plain ``open("w")``
        truncates before any lock is acquired — readers could see an empty
        file; rename means they see old-complete or new-complete only.)"""
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}") from e


# ── module singleton ──────────────────────────────────────────────────
#
# One store per process serves the tool, the prompt section, and the
# review fork (the donor shares the parent's store object with its fork by
# assignment — background_review.py:665). Keyed by (config dir, limits) so
# tests that monkeypatch $CLAWCODEX_CONFIG_DIR or limits get a fresh
# store instead of a stale one.

_store_cache: tuple[tuple[str, int, int], MemoryStore] | None = None


def _configured_limits() -> tuple[int, int]:
    """Read char limits from settings; never raises (defaults on failure)."""
    try:
        from src.settings.settings import get_settings

        s = get_settings()
        memory_limit = int(
            getattr(s, "memory_char_limit", DEFAULT_MEMORY_CHAR_LIMIT)
            or DEFAULT_MEMORY_CHAR_LIMIT
        )
        user_limit = int(
            getattr(s, "user_char_limit", DEFAULT_USER_CHAR_LIMIT)
            or DEFAULT_USER_CHAR_LIMIT
        )
        # Guard misconfiguration: a zero/negative limit would reject every
        # write (`0` already falls back via `or` above; clamp negatives).
        if memory_limit <= 0:
            memory_limit = DEFAULT_MEMORY_CHAR_LIMIT
        if user_limit <= 0:
            user_limit = DEFAULT_USER_CHAR_LIMIT
        return memory_limit, user_limit
    except Exception:  # noqa: BLE001 — memory is optional; never break callers
        return DEFAULT_MEMORY_CHAR_LIMIT, DEFAULT_USER_CHAR_LIMIT


def get_memory_store() -> MemoryStore:
    """The process-wide store (built + loaded on first use; rebuilt when
    the config dir or configured limits change — test/profile switches)."""
    global _store_cache
    memory_limit, user_limit = _configured_limits()
    key = (str(get_user_config_dir()), memory_limit, user_limit)
    if _store_cache is not None and _store_cache[0] == key:
        return _store_cache[1]
    store = MemoryStore(memory_char_limit=memory_limit, user_char_limit=user_limit)
    try:
        store.load_from_disk()
    except Exception:  # noqa: BLE001 — memory is optional; don't break callers
        logger.debug("memory store initial load failed", exc_info=True)
    _store_cache = (key, store)
    return store


def reset_memory_store_cache() -> None:
    """Drop the cached store (tests)."""
    global _store_cache
    _store_cache = None


__all__ = [
    "DEFAULT_MEMORY_CHAR_LIMIT",
    "DEFAULT_USER_CHAR_LIMIT",
    "ENTRY_DELIMITER",
    "MemoryStore",
    "VALID_TARGETS",
    "get_memory_dir",
    "get_memory_store",
    "reset_memory_store_cache",
]
