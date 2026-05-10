"""Back-compat re-export shim.

The implementation has moved to :mod:`src.utils.at_file_completer` so the
Textual-based ``/tui`` PromptInput (Phase 3 of the ch13 refactor) can
share the same matching logic with the legacy prompt_toolkit REPL. This
shim preserves the legacy import path for the duration of one release
cycle.

Direct imports of the public + private-helper names are listed
explicitly so test collection fails loudly if a name is renamed at the
new location — a star-import would silently miss tests that reach into
``_filter_candidates`` / ``_is_path_like_token`` / etc.
"""

from __future__ import annotations

from src.utils.at_file_completer import (
    AtFileCompleter,         # primary class (Completer subclass)
    # Private helpers reached into by tests/test_at_file_completer.py:
    _build_path_bitmap,      # WI-3.1
    _filter_candidates,
    _is_path_like_token,
    _path_completions,
    _subsequence_score,
)

__all__ = [
    "AtFileCompleter",
    "_build_path_bitmap",
    "_filter_candidates",
    "_is_path_like_token",
    "_path_completions",
    "_subsequence_score",
]
