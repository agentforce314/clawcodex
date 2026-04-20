"""Unit tests for :mod:`src.repl.at_file_completer`.

Focus on the parts that don't need a real prompt_toolkit Application:
the trigger regex (token detection), candidate ranking, and the
``Completion`` shape (``text`` includes the leading ``@``;
``start_position`` covers the whole ``@<query>`` span so accepting a
suggestion replaces ``@partial`` with ``@/path/to/file``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.document import Document

from src.repl.at_file_completer import (
    AtFileCompleter,
    _filter_candidates,
    _is_path_like_token,
    _path_completions,
    _subsequence_score,
)


def _completions(completer: AtFileCompleter, text: str) -> list[tuple[str, int, str]]:
    doc = Document(text=text, cursor_position=len(text))
    return [
        (c.text, c.start_position, c.display_text)
        for c in completer.get_completions(doc, None)
    ]


def test_no_at_returns_no_completions(tmp_path):
    (tmp_path / "a.py").write_text("")
    c = AtFileCompleter(cwd=tmp_path)

    assert _completions(c, "hello world") == []


def test_at_in_middle_of_word_does_not_trigger(tmp_path):
    """``user@host`` is an email, not an @-mention. Match TS rule:
    the ``@`` must be at start-of-line or preceded by whitespace."""
    (tmp_path / "host.py").write_text("")
    c = AtFileCompleter(cwd=tmp_path)

    assert _completions(c, "eric@host") == []


def test_at_after_whitespace_triggers(tmp_path):
    (tmp_path / "alpha.py").write_text("")
    (tmp_path / "beta.py").write_text("")
    c = AtFileCompleter(cwd=tmp_path)

    out = _completions(c, "look at @al")
    assert any(disp == "alpha.py" for _, _, disp in out)


def test_empty_query_lists_top_candidates(tmp_path):
    """Typing just ``@`` (no query) opens the popup with the top
    of the cached set — matches TS ``showOnEmpty``."""

    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("")
    c = AtFileCompleter(cwd=tmp_path)

    out = _completions(c, "@")
    assert len(out) == 3
    # ``@<path>`` replaces the bare ``@`` (start_position=-1).
    for text, start, _ in out:
        assert text.startswith("@")
        assert start == -1


def test_completion_replaces_full_at_token(tmp_path):
    """Accepting a suggestion replaces the entire ``@partial`` span
    with ``@<path>`` (TS ``applyFileSuggestion``)."""

    (tmp_path / "alpha.py").write_text("")
    c = AtFileCompleter(cwd=tmp_path)

    out = _completions(c, "look at @alp")
    assert ("@alpha.py", -4, "alpha.py") in out


def test_invalidate_cache_forces_rebuild(tmp_path):
    (tmp_path / "first.py").write_text("")
    c = AtFileCompleter(cwd=tmp_path)

    assert any("first.py" in disp for _, _, disp in _completions(c, "@first"))

    (tmp_path / "second.py").write_text("")
    # Without invalidation, the new file is invisible until the TTL
    # expires; the explicit invalidate should pick it up immediately.
    c.invalidate_cache()
    out = _completions(c, "@second")
    assert any(disp == "second.py" for _, _, disp in out)


def test_set_cwd_invalidates_cache(tmp_path):
    (tmp_path / "old.py").write_text("")
    c = AtFileCompleter(cwd=tmp_path)
    _completions(c, "@old")  # warm the cache

    other = tmp_path / "child"
    other.mkdir()
    (other / "fresh.py").write_text("")
    c.set_cwd(other)

    out = _completions(c, "@fresh")
    assert any(disp == "fresh.py" for _, _, disp in out)


# ---- ranking ----------------------------------------------------------------


def test_basename_substring_beats_path_substring():
    """Files whose basename contains the query rank above files
    where only an ancestor directory matches."""

    paths = [
        "src/repl/foo.py",         # basename ``foo.py`` matches
        "src/foo/other.py",        # only ``foo`` directory matches
        "src/repl/bar.py",         # no match
    ]
    ranked = _filter_candidates(paths, "foo", limit=10)
    assert ranked[0] == "src/repl/foo.py"
    assert ranked[1] == "src/foo/other.py"
    assert "src/repl/bar.py" not in ranked


def test_subsequence_match_falls_back_below_substring():
    paths = [
        "src/foo/bar.py",          # substring "fb" not present
        "fb.py",                   # substring match on basename
    ]
    ranked = _filter_candidates(paths, "fb", limit=10)
    assert ranked[0] == "fb.py"
    # ``src/foo/bar.py`` is a subsequence match (f-b across "foo/bar")
    assert "src/foo/bar.py" in ranked


def test_no_match_drops_path():
    ranked = _filter_candidates(["src/repl/core.py"], "zzz", limit=10)
    assert ranked == []


# ---- path-like completion ---------------------------------------------------


def test_is_path_like_token_recognizes_absolute_and_relative_prefixes():
    assert _is_path_like_token("/")
    assert _is_path_like_token("/Users/")
    assert _is_path_like_token("~/")
    assert _is_path_like_token("~/Down")
    assert _is_path_like_token("./")
    assert _is_path_like_token("../")
    # Bare tokens are themselves directory references.
    assert _is_path_like_token("~")
    assert _is_path_like_token(".")
    assert _is_path_like_token("..")
    # Project-file tokens (basename or relative-without-dot) shouldn't
    # be treated as path-like — they go through the git-index branch.
    assert not _is_path_like_token("src/repl")
    assert not _is_path_like_token("alpha.py")
    assert not _is_path_like_token("")


def test_path_completion_lists_directory_with_trailing_slash(tmp_path):
    """Typing ``@<dir>/`` should list everything under that dir, with
    directories suffixed with ``/`` so the user can keep traversing."""

    (tmp_path / "alpha.py").write_text("")
    sub = tmp_path / "subdir"
    sub.mkdir()

    out = _path_completions(str(tmp_path) + "/", limit=10)
    displays = [s.display for s in out]
    assert "subdir/" in displays
    assert "alpha.py" in displays
    # Directories sort before files.
    assert displays.index("subdir/") < displays.index("alpha.py")


def test_path_completion_filters_by_basename_prefix(tmp_path):
    (tmp_path / "alpha.py").write_text("")
    (tmp_path / "beta.py").write_text("")
    (tmp_path / "alphabet.txt").write_text("")

    out = _path_completions(str(tmp_path) + "/al", limit=10)
    displays = sorted(s.display for s in out)
    assert displays == ["alpha.py", "alphabet.txt"]


def test_path_completion_preserves_input_shape(tmp_path):
    """A query of ``<dir>/al`` should return ``text`` rooted at the
    same ``<dir>/`` prefix the user typed (so the splice into the
    buffer keeps their absolute/relative shape intact)."""

    (tmp_path / "alpha.py").write_text("")

    query = str(tmp_path) + "/al"
    out = _path_completions(query, limit=10)
    assert len(out) == 1
    assert out[0].text == str(tmp_path) + "/alpha.py"


def test_path_completion_skips_hidden_unless_prefix_is_dot(tmp_path):
    (tmp_path / ".secret").write_text("")
    (tmp_path / "visible.txt").write_text("")

    visible_only = _path_completions(str(tmp_path) + "/", limit=10)
    assert [s.display for s in visible_only] == ["visible.txt"]

    # Explicit dot prefix opts in to hidden entries.
    with_hidden = _path_completions(str(tmp_path) + "/.", limit=10)
    assert ".secret" in [s.display for s in with_hidden]


def test_at_completer_path_like_branch_uses_filesystem(tmp_path):
    """The completer should route ``@/abs/path`` through the
    filesystem walker rather than the project-files index."""

    target = tmp_path / "outside.py"
    target.write_text("")

    # Create a separate cwd so the candidate index would NOT contain
    # ``outside.py`` — proving the completion came from the path-like
    # branch, not the project index.
    cwd = tmp_path / "project"
    cwd.mkdir()
    (cwd / "indexed.py").write_text("")

    c = AtFileCompleter(cwd=cwd)
    out = _completions(c, "@" + str(tmp_path) + "/out")
    displays = [disp for _, _, disp in out]
    assert "outside.py" in displays


def test_subsequence_score_returns_span():
    # 'srpy' against 'src/repl.py': s@0, r@1, p@8, y@10 → span = 10
    assert _subsequence_score("src/repl.py", "srpy") == 10
    # query missing chars
    assert _subsequence_score("py", "srpy") is None
    # exact prefix has span 0 (but caller would have caught it as a
    # substring before reaching here — we just sanity-check the math)
    assert _subsequence_score("abcdef", "abc") == 2
