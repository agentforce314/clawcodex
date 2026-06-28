"""Project wiki (the original's /wiki) — a file-based knowledge area under
``.clawcodex/wiki`` with pages/, sources/, and top-level index/log/schema files.
Supports init (create structure), status (counts), and ingest (copy a file into
sources/). Mirrors typescript/src/services/wiki.
"""

from .wiki import get_wiki_paths, ingest_source, init_wiki, wiki_status

__all__ = ["get_wiki_paths", "init_wiki", "wiki_status", "ingest_source"]
