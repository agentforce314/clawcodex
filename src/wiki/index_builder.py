"""Wiki index rebuild — port of typescript/src/services/wiki/indexBuilder.ts.

Lists the wiki's markdown pages + sources, extracts each page's title (its
first ``# `` heading, else the filename), and writes a browsable index.
Called after each ingest; exposed for a future ``/wiki reindex``.
"""

from __future__ import annotations

from pathlib import Path

from .wiki import get_wiki_paths


def _list_markdown_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files: list[Path] = []
    for entry in sorted(directory.rglob("*.md")):
        if entry.is_file():
            files.append(entry)
    return sorted(files)


def _get_page_title(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return path.stem
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.stem


def rebuild_wiki_index(cwd: str | Path) -> None:
    """Port of ``rebuildWikiIndex``: write ``index.md`` from the pages +
    sources markdown."""
    paths = get_wiki_paths(cwd)
    page_files = _list_markdown_files(paths.pages_dir)
    source_files = _list_markdown_files(paths.sources_dir)

    page_links = []
    for f in page_files:
        rel = f.relative_to(paths.root).as_posix()
        title = _get_page_title(f)
        page_links.append(f"- [{title}](./{rel})")

    source_links = []
    for f in source_files:
        rel = f.relative_to(paths.root).as_posix()
        source_links.append(f"- [{f.stem}](./{rel})")

    project_name = Path(cwd).name
    content = (
        f"# {project_name} Wiki\n\n"
        "This wiki is maintained by clawcodex as a durable project "
        "knowledge layer.\n\n"
        "## Core Pages\n\n"
        f"{chr(10).join(page_links) if page_links else '- No pages yet'}\n\n"
        "## Sources\n\n"
        f"{chr(10).join(source_links) if source_links else '- No sources yet'}\n\n"
        "## Recent Updates\n\n"
        "- See [log.md](./log.md)\n"
    )
    paths.index_file.write_text(content, encoding="utf-8")
