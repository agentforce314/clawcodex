"""Chapter C7 — wiki-init fidelity (port of initializeWiki, init.ts).

SERVICES-4 follow-up: the init seed templates were a low-fidelity port.
Pins the verbatim schema/log/architecture templates (clawcodex-branded), the
TS already_existed semantics (createdFiles.length === 0, replacing the
index-precheck), cwd-relative created paths + created_directories, and the
atomic create-if-absent (the wx flag).
"""
from __future__ import annotations

import re

from src.wiki import get_wiki_paths, init_wiki


def test_schema_template_verbatim(tmp_path):
    init_wiki(tmp_path)
    s = get_wiki_paths(tmp_path).schema_file.read_text()
    assert s.startswith("# clawcodex Wiki Schema\n")
    for h in ("## Goals", "## Structure", "## Page Rules",
              "`## Summary`", "`## Key Facts`", "`## Relationships`",
              "`## Open Questions`", "`## Sources`"):
        assert h in s, h
    assert "Prefer editing an existing page over creating duplicates" in s


def test_log_template_timestamped(tmp_path):
    init_wiki(tmp_path)
    log = get_wiki_paths(tmp_path).log_file.read_text()
    assert log.startswith("# Wiki Update Log\n")
    assert re.search(
        r"- \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z: Wiki initialized by clawcodex\n",
        log,
    )


def test_architecture_template_sections(tmp_path):
    init_wiki(tmp_path)
    a = (get_wiki_paths(tmp_path).pages_dir / "architecture.md").read_text()
    for h in ("## Summary", "## Key Facts", "## Relationships",
              "## Open Questions", "## Sources"):
        assert h in a, h
    assert "Wiki bootstrap" in a


def test_already_existed_is_created_count_semantics(tmp_path):
    r1 = init_wiki(tmp_path)
    assert r1["already_existed"] is False and len(r1["created_files"]) == 4
    # delete ONE seed file: the old index-precheck would say already_existed
    # =True AND not recreate; TS semantics recreate the missing file and
    # report already_existed=False (something was created).
    get_wiki_paths(tmp_path).schema_file.unlink()
    r2 = init_wiki(tmp_path)
    assert r2["created_files"] == [".clawcodex/wiki/schema.md".replace(".", ".")] or (
        len(r2["created_files"]) == 1 and r2["created_files"][0].endswith("schema.md")
    )
    assert r2["already_existed"] is False
    r3 = init_wiki(tmp_path)
    assert r3["already_existed"] is True and r3["created_files"] == []


def test_created_paths_relative_and_directories_reported(tmp_path):
    r = init_wiki(tmp_path)
    assert all(not p.startswith("/") for p in r["created_files"])
    assert any(p.endswith("index.md") for p in r["created_files"])
    assert len(r["created_directories"]) == 3
    assert all(not p.startswith("/") for p in r["created_directories"])


def test_existing_content_never_clobbered(tmp_path):
    init_wiki(tmp_path)
    idx = get_wiki_paths(tmp_path).index_file
    idx.write_text("USER EDITED")
    init_wiki(tmp_path)
    assert idx.read_text() == "USER EDITED"  # create-if-absent only
