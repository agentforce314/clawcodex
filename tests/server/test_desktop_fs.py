"""Directory browsing for the workspace picker (``src/server/desktop_fs.py``).

The property that matters most here is the one about failure: an empty
``entries`` list must mean "no subdirectories", never "we could not look". A
picker that silently shows an empty folder for a directory it lacks permission
to read sends the user hunting for a project that is right there.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.server.desktop_fs import home_directory, list_directory

# Windows needs Developer Mode or elevation to create a symlink, so the two
# symlink cases below are probed rather than assumed. Probing beats checking
# `os.name`: an unprivileged Windows runner and a privileged one differ, and
# only the attempt can tell them apart.


def _symlinks_available() -> bool:
    try:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "target").mkdir()
            (root / "link").symlink_to(root / "target", target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


SYMLINKS = _symlinks_available()
needs_symlinks = pytest.mark.skipif(not SYMLINKS, reason="symlink creation not permitted here")

# `os.geteuid` is Unix-only, and a skipif condition is evaluated at import
# time even when an earlier skipif on the same test already matched — reading
# it directly takes the whole module down on Windows during collection.
IS_ROOT = getattr(os, "geteuid", lambda: 1)() == 0


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A directory with children of every kind the picker must handle."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "Beta").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "zeta").mkdir()
    (tmp_path / "a-file.txt").write_text("not a workspace", encoding="utf-8")
    return tmp_path


def _names(listing: dict) -> list[str]:
    return [entry["name"] for entry in listing["entries"]]


# ── listing ──────────────────────────────────────────────────────────────────


def test_lists_only_directories(tree: Path) -> None:
    """A file is never a workspace, so it is never a row."""
    assert "a-file.txt" not in _names(list_directory(str(tree)))


def test_sorts_case_insensitively(tree: Path) -> None:
    """`Beta` belongs between `alpha` and `zeta` where a reader expects it —
    byte order would file every capitalized name first."""
    assert _names(list_directory(str(tree))) == [".hidden", "alpha", "Beta", "zeta"]


def test_flags_hidden_without_removing_it(tree: Path) -> None:
    """The backend reports the platform's convention; the client decides
    whether to show it."""
    entries = {entry["name"]: entry for entry in list_directory(str(tree))["entries"]}
    assert entries[".hidden"]["hidden"] is True
    assert entries["alpha"]["hidden"] is False


def test_every_entry_carries_an_absolute_path(tree: Path) -> None:
    """Clients never join path segments themselves — that is how a Windows
    path ends up assembled with the wrong separator."""
    for entry in list_directory(str(tree))["entries"]:
        assert os.path.isabs(entry["path"])
        assert Path(entry["path"]).parent == tree.resolve()


def test_empty_directory_lists_nothing_and_does_not_fail(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    listing = list_directory(str(empty))
    assert listing["entries"] == []
    assert listing["truncated"] is False


# ── ancestry ─────────────────────────────────────────────────────────────────


def test_crumbs_run_root_to_target_inclusive(tree: Path) -> None:
    listing = list_directory(str(tree / "alpha"))
    crumbs = listing["crumbs"]
    assert crumbs[-1]["path"] == str((tree / "alpha").resolve())
    # The first crumb is the filesystem root, whatever it is called on this
    # platform (`/` on POSIX, a drive root on Windows): the one thing true of
    # a root everywhere is that it is its own parent.
    root = Path(crumbs[0]["path"])
    assert root.parent == root
    # Each crumb is a jump target, so each must be a real absolute path.
    for crumb in crumbs:
        assert os.path.isabs(crumb["path"])


def test_root_crumb_is_named_not_blank(tree: Path) -> None:
    """`Path('/').name` is empty; a nameless crumb is an unclickable gap."""
    assert list_directory(str(tree))["crumbs"][0]["name"] != ""


def test_reports_the_home_root(tree: Path) -> None:
    assert list_directory(str(tree))["home"] == str(home_directory())


# ── defaults and normalisation ───────────────────────────────────────────────


def test_no_path_opens_home() -> None:
    assert list_directory()["path"] == str(home_directory().resolve())
    assert list_directory("")["path"] == str(home_directory().resolve())


def test_expands_a_user_relative_path() -> None:
    assert list_directory("~")["path"] == str(home_directory().resolve())


def test_resolves_traversal_to_a_real_path(tree: Path) -> None:
    listing = list_directory(str(tree / "alpha" / ".." / "zeta"))
    assert listing["path"] == str((tree / "zeta").resolve())


# ── failure is never silent ──────────────────────────────────────────────────


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no such directory"):
        list_directory(str(tmp_path / "nope"))


def test_a_file_target_raises(tree: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        list_directory(str(tree / "a-file.txt"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.skipif(IS_ROOT, reason="root reads regardless of mode")
def test_unreadable_directory_raises_rather_than_looking_empty(tmp_path: Path) -> None:
    """The failure this module exists to avoid: a permission error rendered as
    an empty folder, sending the user hunting for a project that is right
    there."""
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "child").mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(ValueError, match="permission denied"):
            list_directory(str(locked))
    finally:
        locked.chmod(0o755)


@needs_symlinks
def test_an_unstattable_child_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A broken symlink costs its own row, not the whole listing."""
    (tmp_path / "real").mkdir()
    (tmp_path / "broken").symlink_to(tmp_path / "does-not-exist")
    assert _names(list_directory(str(tmp_path))) == ["real"]


@needs_symlinks
def test_symlink_to_a_directory_is_listed(tmp_path: Path) -> None:
    """Reaching a project through a symlink is normal; refusing it is not."""
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    assert _names(list_directory(str(tmp_path))) == ["link", "real"]


# ── bounds ───────────────────────────────────────────────────────────────────


def test_caps_a_large_listing_and_says_so(tmp_path: Path) -> None:
    for index in range(12):
        (tmp_path / f"d{index:02d}").mkdir()

    listing = list_directory(str(tmp_path), limit=5)
    assert len(listing["entries"]) == 5
    assert listing["truncated"] is True
    # The cut is off the name-sorted tail, so the visible head is stable.
    assert _names(listing) == ["d00", "d01", "d02", "d03", "d04"]


def test_a_listing_under_the_cap_is_not_marked_truncated(tree: Path) -> None:
    assert list_directory(str(tree), limit=100)["truncated"] is False
