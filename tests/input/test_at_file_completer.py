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
    _build_path_bitmap,
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


# ---- WI-3.1: 26-bit bitmap pre-filter ---------------------------------------


class TestPathBitmap:
    """Bitmap correctness — single-letter, multi-letter, subset checks."""

    def test_abc_packs_first_three_bits(self):
        # 'a'=bit 0, 'b'=bit 1, 'c'=bit 2 → 0b111 = 7.
        assert _build_path_bitmap("abc") == 0b111

    def test_z_packs_top_bit(self):
        # 'z' = bit 25.
        assert _build_path_bitmap("z") == (1 << 25)

    def test_az_packs_bottom_and_top_bits(self):
        # 'a' + 'z' = bit 0 | bit 25.
        assert _build_path_bitmap("az") == (1 | (1 << 25))

    def test_uppercase_lowercased(self):
        # Bitmap is case-insensitive; ABC == abc.
        assert _build_path_bitmap("ABC") == _build_path_bitmap("abc")

    def test_non_letter_chars_ignored(self):
        # Digits, punctuation, slashes don't contribute.
        assert _build_path_bitmap("a1b/c.x") == _build_path_bitmap("abcx")

    def test_subset_relationship(self):
        # The full word's bitmap is a superset of any sub-word's bitmap.
        # ``(superset & subset) == subset`` — the bitmap-rejection idiom.
        full = _build_path_bitmap("alphabet")
        partial = _build_path_bitmap("alpha")
        assert (full & partial) == partial

    def test_missing_letter_breaks_subset(self):
        # Path missing letter ``z`` cannot satisfy a query with ``z``.
        path_bits = _build_path_bitmap("alpha")
        needle_bits = _build_path_bitmap("zalpha")
        # path & needle == needle would mean alpha contains z. It doesn't.
        assert (path_bits & needle_bits) != needle_bits


class TestBitmapPreFilterRejectionRatio:
    """Structural acceptance test (per critic M10): the bitmap pre-filter
    rejects N% of candidates with rare-letter queries before they reach
    the inner match. Replaces flake-prone wall-clock thresholds.
    """

    def test_rare_letter_query_rejects_most_candidates(self):
        # Synthetic candidates with no 'z'; querying 'z' should reject all.
        paths = [f"src/file_{i}.py" for i in range(1000)]
        bitmaps = [_build_path_bitmap(p) for p in paths]
        # Filter with a 'z' query.
        result = _filter_candidates(paths, "z", limit=15, bitmaps=bitmaps)
        # Zero results — every path correctly rejected by bitmap.
        assert result == []

    def test_letter_in_some_paths_filters_to_those(self):
        # 100 paths with no 'z'; 5 paths containing 'z' (zonk_*.py).
        paths = [f"file_{i}.py" for i in range(100)]
        paths.extend(f"zonk_{i}.py" for i in range(5))
        bitmaps = [_build_path_bitmap(p) for p in paths]
        # Querying 'zonk' should return only the zonk_* candidates.
        result = _filter_candidates(paths, "zonk", limit=15, bitmaps=bitmaps)
        assert len(result) == 5
        for path in result:
            assert path.startswith("zonk_")


# ---- WI-3.2: async indexing with thread-based queryable/done ----------------


class TestAsyncIndexing:
    """Thread-based warm-up: queryable resolves before done; bounded wait."""

    def test_queryable_event_set_after_first_completion(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text("")
        c = AtFileCompleter(cwd=tmp_path)
        # Trigger the warm-up by reading from the index.
        completions = list(c.get_completions(
            Document("@"), None,
        ))
        # By now the warm-up should have published at least one chunk
        # (or the synchronous fallback has). Both events should be set.
        assert c._index_queryable_event.is_set()
        # Small workspace: done quickly too.
        assert c._index_done_event.wait(timeout=1.0)
        # And we got non-empty completions.
        assert len(completions) > 0

    def test_invalidate_cache_clears_events(self, tmp_path):
        (tmp_path / "x.py").write_text("")
        c = AtFileCompleter(cwd=tmp_path)
        # Warm up.
        list(c.get_completions(Document("@"), None))
        assert c._index_done_event.is_set()
        # Invalidate.
        c.invalidate_cache()
        assert not c._index_queryable_event.is_set()
        assert not c._index_done_event.is_set()
        # Next call rebuilds.
        list(c.get_completions(Document("@"), None))
        assert c._index_done_event.is_set()

    def test_bitmap_built_alongside_path_cache(self, tmp_path):
        for name in ("alpha.py", "beta.py", "gamma.py"):
            (tmp_path / name).write_text("")
        c = AtFileCompleter(cwd=tmp_path)
        # Warm up via a query.
        list(c.get_completions(Document("@"), None))
        # Bitmaps list mirrors paths list length.
        assert len(c._cache) == len(c._cache_bitmaps)
        # Each bitmap is a non-zero int (every path has at least one letter).
        for bm in c._cache_bitmaps:
            assert isinstance(bm, int)
            assert bm > 0


# ---- WI-3.3: score-bound rejection ------------------------------------------


class TestScoreBoundRejection:
    """The score-bound check skips inner-match work on outclassed candidates.

    These tests directly verify WI-3.3 by patching ``_subsequence_score``
    and counting calls. Tier-2 candidates are paths where the query letters
    appear as a subsequence but NOT as a contiguous substring of the
    basename or full path — so they require the expensive
    ``_subsequence_score`` scan unless score-bound rejection skips them.

    Verified tier-2 construction: ``q="foo"`` against ``"xfxoxox.py"``::
        - basename "xfxoxox.py": "foo" NOT a substring → tier-0 fails
        - full path "xfxoxox.py": "foo" NOT a substring → tier-1 fails
        - subsequence: f@1, o@3, o@5 → tier-2 hit
    """

    def test_top_k_full_of_tier_0_skips_tier_2_inner_match(self, monkeypatch):
        """WI-3.3 must skip the subsequence-score scan for tier-2 candidates
        when top-K is already full of tier-0 hits.

        Patches ``_subsequence_score`` and asserts call_count == 0. A future
        regression to the score-bound logic (e.g., removing the early-skip)
        would cause _subsequence_score to be invoked on the tier-2 paths
        and fail this test.
        """
        import src.utils.at_file_completer as af

        # 20 tier-0 candidates: "foo" is a substring of basename.
        tier0 = [f"dir{i}/foo.py" for i in range(20)]
        # Real tier-2 candidates: "foo" is a subsequence but NOT a substring
        # of basename or path. Verified by the docstring above.
        tier2 = [
            "xfxoxox.py",
            "yfybyoybyoy.py",
            "zfqzqozqozqz.py",
            "wfwwowwowww.py",
        ]
        paths = tier0 + tier2
        bitmaps = [_build_path_bitmap(p) for p in paths]

        # Sanity: confirm tier-2 candidates ARE tier-2 — q='foo' should
        # subsequence-match each but NOT substring-match basename or path.
        import os as _os
        for tp in tier2:
            assert "foo" not in _os.path.basename(tp).lower()
            assert "foo" not in tp.lower()
            assert _subsequence_score(tp.lower(), "foo") is not None

        # Instrument: count subsequence-score calls.
        call_count = [0]
        orig = af._subsequence_score
        def counting(text, query):
            call_count[0] += 1
            return orig(text, query)
        monkeypatch.setattr(af, "_subsequence_score", counting)

        result = _filter_candidates(paths, "foo", limit=15, bitmaps=bitmaps)

        # WI-3.3 acceptance: zero subsequence-score calls when top-K is
        # full of tier-0. If this fires, the score-bound skip regressed.
        assert call_count[0] == 0, (
            f"WI-3.3 must skip _subsequence_score for tier-2 candidates "
            f"when top-K is full of tier-0; got {call_count[0]} calls"
        )
        # End-result: top 15 are all tier-0.
        assert len(result) == 15
        for path in result:
            assert "foo" in _os.path.basename(path).lower()

    def test_under_filled_top_k_invokes_subsequence_score(self, monkeypatch):
        """WI-3.3 must NOT skip when top-K is under-filled — every tier-2
        candidate is a contender and should enter the inner match.

        Counterpoint to the previous test: with fewer candidates than
        ``limit``, the score-bound check is a no-op and tier-2 paths
        SHOULD trigger ``_subsequence_score``.
        """
        import src.utils.at_file_completer as af
        # Only 3 candidates total, all tier-2 (subsequence-only).
        paths = ["xfxoxox.py", "yfybyoybyoy.py", "zfqzqozqozqz.py"]
        bitmaps = [_build_path_bitmap(p) for p in paths]

        call_count = [0]
        orig = af._subsequence_score
        def counting(text, query):
            call_count[0] += 1
            return orig(text, query)
        monkeypatch.setattr(af, "_subsequence_score", counting)

        result = _filter_candidates(paths, "foo", limit=15, bitmaps=bitmaps)

        # All 3 tier-2 paths reach the inner match (top-K under-filled).
        assert call_count[0] == 3, (
            f"WI-3.3 must NOT skip when top-K is under-filled; expected 3 "
            f"_subsequence_score calls, got {call_count[0]}"
        )
        # All 3 returned (each has 'foo' as a subsequence).
        assert len(result) == 3


# ---- backward-compat: legacy callers that don't pass bitmaps still work -----


class TestFilterCandidatesBackwardCompat:
    def test_filter_without_bitmaps_kwarg(self):
        """Legacy callers that don't pass ``bitmaps`` get the same matching
        as before (bitmap pre-filter is skipped, full inner-match runs)."""
        paths = ["alpha.py", "beta.py", "alpha_v2.py"]
        # No bitmaps kwarg — exercises the legacy code path.
        result = _filter_candidates(paths, "alpha", limit=15)
        assert "alpha.py" in result
        assert "alpha_v2.py" in result
        assert "beta.py" not in result
