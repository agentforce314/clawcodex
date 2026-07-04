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
    return (
        f"# {project} Wiki — Schema\n\n"
        "How this wiki is organized:\n\n"
        "- `index.md`: top-level navigation and major topics\n"
        "- `log.md`: append-only update log\n"
        "- `pages/`: durable topic and architecture pages\n"
        "- `sources/`: ingested source notes and summaries\n\n"
        "Keep pages focused on one topic.\n"
    )


def _index_template(project: str) -> str:
    return (
        f"# {project} Wiki\n\n"
        "## Pages\n\n- [Architecture](./pages/architecture.md)\n\n"
        "## Sources\n\nSource notes live in [sources/](./sources/)\n\n"
        "## Log\n\nSee [log.md](./log.md)\n"
    )


def _log_template() -> str:
    return "# Update Log\n\n- wiki initialized\n"


def _architecture_template(project: str) -> str:
    return f"# Architecture\n\n_Describe {project}'s architecture here._\n"


def _ensure_file(path: Path, content: str, created: list[str]) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        created.append(str(path))


def init_wiki(cwd: str | Path) -> dict:
    paths = get_wiki_paths(cwd)
    already = paths.index_file.exists()
    created: list[str] = []
    for d in (paths.root, paths.pages_dir, paths.sources_dir):
        d.mkdir(parents=True, exist_ok=True)
    project = Path(cwd).name or "project"
    _ensure_file(paths.schema_file, _schema_template(project), created)
    _ensure_file(paths.index_file, _index_template(project), created)
    _ensure_file(paths.log_file, _log_template(), created)
    _ensure_file(paths.pages_dir / "architecture.md", _architecture_template(project), created)
    return {"root": str(paths.root), "created_files": created, "already_existed": already}


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
        try:
            rel_source = src_path.resolve().relative_to(Path(cwd).resolve()).as_posix()
        except ValueError:
            rel_source = src_path.name
        ingested_at = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime()) + "Z"
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
