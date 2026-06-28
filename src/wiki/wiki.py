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


def ingest_source(cwd: str | Path, src: str) -> dict:
    paths = get_wiki_paths(cwd)
    if not paths.index_file.exists():
        return {"ok": False, "error": "wiki not initialized — run /wiki init first"}
    src_path = Path(src)
    if not src_path.is_absolute():
        src_path = Path(cwd) / src
    if not src_path.is_file():
        return {"ok": False, "error": f"not a file: {src}"}
    dest = paths.sources_dir / src_path.name
    try:
        shutil.copyfile(src_path, dest)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    try:  # append to the log (best-effort)
        with paths.log_file.open("a", encoding="utf-8") as f:
            f.write(f"- ingested source: {src_path.name}\n")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "dest": str(dest)}
