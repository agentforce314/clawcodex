"""Project wiki file management: init / status / ingest."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

CLAWCODEX_DIRNAME = ".clawcodex"
WIKI_DIRNAME = "wiki"


@dataclass
class WikiPaths:
    root: Path
    pages_dir: Path
    sources_dir: Path
    schema_file: Path
    index_file: Path
    log_file: Path


def get_wiki_paths(cwd: str | Path) -> WikiPaths:
    root = Path(cwd) / CLAWCODEX_DIRNAME / WIKI_DIRNAME
    return WikiPaths(
        root=root,
        pages_dir=root / "pages",
        sources_dir=root / "sources",
        schema_file=root / "schema.md",
        index_file=root / "index.md",
        log_file=root / "log.md",
    )


def _schema_template(project: str) -> str:
    """Verbatim port of init.ts buildSchemaTemplate (C7 — wiki-init-fidelity;
    OpenClaude→clawcodex per the wiki round's approved rename)."""
    return (
        "# clawcodex Wiki Schema\n\n"
        "This wiki stores durable, human-readable project knowledge for "
        f"{project}.\n\n"
        "## Goals\n\n"
        "- Keep useful project knowledge in markdown, not only in chat history\n"
        "- Prefer synthesized facts over raw copy-paste\n"
        "- Keep source attribution explicit\n"
        "- Make pages easy for both humans and agents to update\n\n"
        "## Structure\n\n"
        "- `index.md`: top-level navigation and major topics\n"
        "- `log.md`: append-only update log\n"
        "- `pages/`: durable topic and architecture pages\n"
        "- `sources/`: source ingestion notes and summaries\n\n"
        "## Page Rules\n\n"
        "- Keep pages focused on one topic\n"
        "- Use stable headings such as:\n"
        "  - `## Summary`\n"
        "  - `## Key Facts`\n"
        "  - `## Relationships`\n"
        "  - `## Open Questions`\n"
        "  - `## Sources`\n"
        "- Add or update facts only when they are grounded in project files "
        "or explicit source notes\n"
        "- Prefer editing an existing page over creating duplicates\n"
    )


def _index_template(project: str) -> str:
    # DELIBERATE divergence from TS buildIndexTemplate: matches
    # rebuild_wiki_index's structure for a just-initialized wiki (only
    # architecture.md present) so the first ingest's rebuild does not flip
    # the headers user-visibly (SERVICES-4 critic MINOR-1 — TS itself has
    # the init≠rebuild inconsistency; the port fixed it).
    return (
        f"# {project} Wiki\n\n"
        "This wiki is maintained by clawcodex as a durable project "
        "knowledge layer.\n\n"
        "## Core Pages\n\n- [Architecture](./pages/architecture.md)\n\n"
        "## Sources\n\n- No sources yet\n\n"
        "## Recent Updates\n\n- See [log.md](./log.md)\n"
    )


def _log_template(timestamp: str) -> str:
    """Port of init.ts buildLogTemplate — the ISO timestamp + attribution."""
    return f"# Wiki Update Log\n\n- {timestamp}: Wiki initialized by clawcodex\n"


def _architecture_template(project: str) -> str:
    """Verbatim port of init.ts buildArchitectureTemplate."""
    return (
        "# Architecture\n\n"
        "## Summary\n\n"
        f"High-level architecture notes for {project}.\n\n"
        "## Key Facts\n\n"
        "- This page is the starting point for durable architecture knowledge.\n\n"
        "## Relationships\n\n"
        "- Link this page to major subsystems as the wiki grows.\n\n"
        "## Open Questions\n\n"
        "- What are the most important runtime subsystems?\n"
        "- Which files best represent the system architecture?\n\n"
        "## Sources\n\n"
        "- Wiki bootstrap\n"
    )


def _ensure_file(path: Path, content: str, created: list[str]) -> None:
    """Create-if-absent, atomically (the TS ``wx`` flag — open(..., "x")
    raises FileExistsError instead of the racy exists()-then-write)."""
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(content)
        created.append(str(path))
    except FileExistsError:
        return


def _init_timestamp() -> str:
    """ISO-8601 with milliseconds + Z (Date.toISOString parity — the same
    format ingest uses)."""
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def init_wiki(cwd: str | Path) -> dict:
    """Port of ``initializeWiki`` (init.ts): create-if-absent the wiki seed
    files; ``already_existed`` = nothing was created (TS
    ``createdFiles.length === 0``, replacing the prior index-precheck);
    created paths are cwd-relative (TS ``relative(cwd, …)``)."""
    paths = get_wiki_paths(cwd)
    created: list[str] = []
    created_dirs: list[str] = []
    for d in (paths.root, paths.pages_dir, paths.sources_dir):
        d.mkdir(parents=True, exist_ok=True)
        created_dirs.append(str(d))
    project = Path(cwd).name or "project"
    timestamp = _init_timestamp()
    _ensure_file(paths.schema_file, _schema_template(project), created)
    _ensure_file(paths.index_file, _index_template(project), created)
    _ensure_file(paths.log_file, _log_template(timestamp), created)
    _ensure_file(paths.pages_dir / "architecture.md", _architecture_template(project), created)

    def _rel(p: str) -> str:
        try:
            return str(Path(p).relative_to(Path(cwd)))
        except ValueError:
            return p

    return {
        "root": str(paths.root),
        "created_files": [_rel(p) for p in created],
        "created_directories": [_rel(d) for d in created_dirs],
        "already_existed": len(created) == 0,
    }


def wiki_status(cwd: str | Path) -> dict:
    paths = get_wiki_paths(cwd)
    if not paths.index_file.exists():
        return {"initialized": False, "root": str(paths.root), "page_count": 0, "source_count": 0}
    pages = len(list(paths.pages_dir.glob("*.md"))) if paths.pages_dir.exists() else 0
    sources = len(list(paths.sources_dir.glob("*"))) if paths.sources_dir.exists() else 0
    return {"initialized": True, "root": str(paths.root), "page_count": pages, "source_count": sources}


def _build_source_note(
    *, title: str, source_path: str, ingested_at: str, summary: str, excerpt: str
) -> str:
    """Verbatim template from ingest.ts:13-46 (buildSourceNote)."""
    return (
        f"# {title}\n\n"
        "## Source\n\n"
        f"- Path: `{source_path}`\n"
        f"- Ingested at: {ingested_at}\n\n"
        "## Summary\n\n"
        f"{summary}\n\n"
        "## Excerpt\n\n"
        "```\n"
        f"{excerpt}\n"
        "```\n\n"
        "## Linked Pages\n\n"
        "- [Architecture](../pages/architecture.md)\n"
    )


def ingest_source(cwd: str | Path, src: str) -> dict:
    """Port of ``ingestLocalWikiSource`` (SERVICES-4): write a STRUCTURED
    source note (title/summary/excerpt) + a log entry + rebuild the index —
    replacing the prior copy-only behavior."""
    import datetime as _dt
    import os as _os
    import time as _time

    from .index_builder import rebuild_wiki_index
    from .utils import extract_title_from_text, sanitize_wiki_slug, summarize_text

    paths = get_wiki_paths(cwd)
    if not paths.index_file.exists():
        return {"ok": False, "error": "wiki not initialized — run /wiki init first"}
    src_path = Path(src)
    if not src_path.is_absolute():
        src_path = Path(cwd) / src
    if not src_path.is_file():
        return {"ok": False, "error": f"not a file: {src}"}
    try:
        content = src_path.read_text(encoding="utf-8", errors="replace")
        # Lexical relative path (TS path.relative), so out-of-cwd sources get a
        # ``../``-style path rather than a bare basename (critic MINOR-4).
        rel_source = _os.path.relpath(
            _os.path.abspath(str(src_path)), _os.path.abspath(str(cwd))
        ).replace(_os.sep, "/")
        # ISO-8601 with milliseconds + Z, matching TS Date.toISOString()
        # (critic MINOR-2).
        _now = _dt.datetime.now(_dt.timezone.utc)
        ingested_at = _now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{_now.microsecond // 1000:03d}Z"
        base_name = src_path.stem
        title = extract_title_from_text(base_name, content)
        summary = summarize_text(content)
        excerpt = "\n".join(content.split("\n")[:20]).strip()
        ms = _time.time_ns() // 1_000_000
        slug = sanitize_wiki_slug(f"{base_name}-{ms}") or f"source-{ms}"

        note_path = paths.sources_dir / f"{slug}.md"
        note_path.write_text(
            _build_source_note(
                title=title, source_path=rel_source, ingested_at=ingested_at,
                summary=summary, excerpt=excerpt,
            ),
            encoding="utf-8",
        )
        with paths.log_file.open("a", encoding="utf-8") as f:
            f.write(
                f'- {ingested_at}: Ingested `{rel_source}` into source '
                f'note "{title}"\n'
            )
        rebuild_wiki_index(cwd)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "dest": str(note_path),
        "source_note": note_path.relative_to(Path(cwd)).as_posix()
        if note_path.is_relative_to(Path(cwd))
        else str(note_path),
        "summary": summary,
        "title": title,
    }
