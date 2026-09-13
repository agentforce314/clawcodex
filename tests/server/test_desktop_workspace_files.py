"""Workspace-confined file reading for the web client's sidebar.

The contract these pin: a page is lines, a failure is a code, and nothing
outside the workspace root is ever read — not through ``..``, not through a
symlink that points out of the tree.
"""

from __future__ import annotations

import base64
import os

import pytest

from src.server import desktop_workspace_files as workspace_files
from src.server.desktop_workspace_files import (
    MAX_BYTES,
    MAX_ENTRIES,
    MAX_LINES,
    list_dir,
    read_bytes,
    read_file,
    read_related,
)


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


# ── read_file ────────────────────────────────────────────────────────────────


def test_reads_a_whole_small_file_as_one_page(workspace):
    (workspace / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    page = read_file(str(workspace), "notes.txt")

    assert page["ok"] is True
    assert page["text"] == "alpha\nbeta\ngamma"
    assert page["lines"] == 3
    assert page["offset"] == 1
    assert page["eof"] is True
    assert page["absolute_path"] == str(workspace / "notes.txt")


def test_a_page_keeps_its_last_line_when_the_file_has_no_final_newline(workspace):
    (workspace / "notes.txt").write_text("alpha\nbeta", encoding="utf-8")

    page = read_file(str(workspace), "notes.txt")

    assert page["text"] == "alpha\nbeta"
    assert page["lines"] == 2


def test_one_empty_line_and_a_page_past_the_end_read_differently(workspace):
    (workspace / "blank.txt").write_text("\n", encoding="utf-8")

    empty_line = read_file(str(workspace), "blank.txt")
    past_end = read_file(str(workspace), "blank.txt", offset=5)

    assert (empty_line["lines"], empty_line["text"]) == (1, "")
    assert (past_end["lines"], past_end["text"]) == (0, "")
    assert past_end["eof"] is True


def test_an_empty_file_is_a_page_of_no_lines(workspace):
    (workspace / "empty.txt").write_text("", encoding="utf-8")

    page = read_file(str(workspace), "empty.txt")

    assert page["ok"] is True
    assert (page["lines"], page["text"], page["eof"]) == (0, "", True)


def test_pages_walk_the_file_in_order_and_only_the_last_one_is_eof(workspace):
    (workspace / "long.txt").write_text(
        "".join(f"line {n}\n" for n in range(1, 8)), encoding="utf-8"
    )

    first = read_file(str(workspace), "long.txt", offset=1, limit=3)
    second = read_file(str(workspace), "long.txt", offset=4, limit=3)
    third = read_file(str(workspace), "long.txt", offset=7, limit=3)

    assert (first["text"], first["eof"]) == ("line 1\nline 2\nline 3", False)
    assert (second["text"], second["eof"]) == ("line 4\nline 5\nline 6", False)
    assert (third["text"], third["eof"], third["lines"]) == ("line 7", True, 1)


def test_a_page_that_ends_exactly_on_the_last_line_is_not_guessed_to_be_eof(workspace):
    """The final page's ``eof`` is decided by a read past it, not by arithmetic."""
    (workspace / "six.txt").write_text(
        "".join(f"line {n}\n" for n in range(1, 7)), encoding="utf-8"
    )

    page = read_file(str(workspace), "six.txt", offset=4, limit=3)

    assert page["lines"] == 3
    assert page["eof"] is True


def test_limit_narrows_but_never_widens_the_page(workspace):
    (workspace / "long.txt").write_text(
        "".join(f"line {n}\n" for n in range(1, 20)), encoding="utf-8"
    )

    page = read_file(str(workspace), "long.txt", limit=MAX_LINES * 10)

    assert page["lines"] == 19


def test_crlf_endings_do_not_reach_the_reader(workspace):
    (workspace / "dos.txt").write_bytes(b"alpha\r\nbeta\r\n")

    page = read_file(str(workspace), "dos.txt")

    assert page["text"] == "alpha\nbeta"
    assert page["lines"] == 2


def test_an_absolute_path_inside_the_workspace_is_accepted(workspace):
    (workspace / "notes.txt").write_text("hello\n", encoding="utf-8")

    page = read_file(str(workspace), str(workspace / "notes.txt"))

    assert page["ok"] is True
    assert page["text"] == "hello"


def test_a_missing_file_is_reported_as_gone(workspace):
    result = read_file(str(workspace), "nope.txt")

    assert result["ok"] is False
    assert result["error"]["code"] == "workspace-file/not-found"


def test_a_directory_has_no_text_to_show(workspace):
    (workspace / "src").mkdir()

    result = read_file(str(workspace), "src")

    assert result["error"]["code"] == "workspace-file/not-regular-file"


def test_a_binary_that_happens_to_decode_is_still_refused(workspace):
    # A NUL byte is valid UTF-8, so decoding alone lets this through: UTF-16LE
    # ASCII is exactly the case, and it would render as text interleaved with
    # invisible NULs rather than as "not a text file".
    (workspace / "utf16.txt").write_bytes("hello".encode("utf-16-le"))

    result = read_file(str(workspace), "utf16.txt")

    assert result["error"]["code"] == "workspace-file/not-text"


def test_a_binary_file_is_refused_as_not_text(workspace):
    (workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00binary")

    result = read_file(str(workspace), "logo.png")

    assert result["error"]["code"] == "workspace-file/not-text"


def test_a_page_over_the_byte_cap_is_refused_with_its_limit(workspace):
    line = ("x" * 4096) + "\n"
    (workspace / "huge.txt").write_text(line * ((MAX_BYTES // len(line)) + 8), encoding="utf-8")

    result = read_file(str(workspace), "huge.txt")

    assert result["error"]["code"] == "workspace-file/too-large"
    assert result["error"]["details"]["limit"] == MAX_BYTES


def test_a_path_climbing_out_of_the_workspace_is_refused(workspace, tmp_path):
    (tmp_path / "secret.txt").write_text("private\n", encoding="utf-8")

    result = read_file(str(workspace), "../secret.txt")

    assert result["error"]["code"] == "workspace-file/outside-workspace"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlink_pointing_out_of_the_workspace_is_refused(workspace, tmp_path):
    (tmp_path / "secret.txt").write_text("private\n", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(tmp_path / "secret.txt")

    result = read_file(str(workspace), "escape.txt")

    assert result["error"]["code"] == "workspace-file/outside-workspace"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlink_staying_inside_the_workspace_is_read(workspace):
    (workspace / "real.txt").write_text("inside\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(workspace / "real.txt")

    page = read_file(str(workspace), "link.txt")

    assert page["ok"] is True
    assert page["text"] == "inside"


def test_a_filename_keeps_the_spaces_it_was_given(workspace):
    # A POSIX name may legally begin or end with a space; trimming it would
    # read a different file, or none.
    (workspace / " spaced.txt").write_text("held\n", encoding="utf-8")

    page = read_file(str(workspace), " spaced.txt")

    assert page["ok"] is True
    assert page["text"] == "held"


def test_a_session_without_a_workspace_says_so(workspace):
    result = read_file("", "notes.txt")

    assert result["error"]["code"] == "workspace-file/unknown-workspace"


def test_the_version_changes_when_the_file_does(workspace):
    target = workspace / "notes.txt"
    target.write_text("one\n", encoding="utf-8")
    before = read_file(str(workspace), "notes.txt")["version"]

    target.write_text("one\ntwo\n", encoding="utf-8")
    after = read_file(str(workspace), "notes.txt")["version"]

    assert before != after


# ── list_dir ─────────────────────────────────────────────────────────────────


def test_lists_the_root_when_no_path_is_given(workspace):
    (workspace / "src").mkdir()
    (workspace / "README.md").write_text("hi\n", encoding="utf-8")

    listing = list_dir(str(workspace))

    assert listing["ok"] is True
    assert listing["truncated"] is False
    assert {entry["name"]: entry["type"] for entry in listing["entries"]} == {
        "src": "directory",
        "README.md": "file",
    }


def test_a_file_entry_carries_its_size(workspace):
    # Bytes, not text: Python's text mode would write "\r\n" on Windows and
    # make this six-byte file seven bytes long there.
    (workspace / "README.md").write_bytes(b"hello\n")

    entry = list_dir(str(workspace))["entries"][0]

    assert entry["size"] == 6


def test_dotfiles_are_listed_like_any_other_name(workspace):
    (workspace / ".env").write_text("SECRET=1\n", encoding="utf-8")

    names = [entry["name"] for entry in list_dir(str(workspace))["entries"]]

    assert names == [".env"]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_broken_link_is_listed_as_something_that_cannot_be_opened(workspace):
    (workspace / "dangling").symlink_to(workspace / "gone.txt")

    entries = list_dir(str(workspace))["entries"]

    assert entries == [{"name": "dangling", "type": "other"}]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_link_out_of_the_workspace_is_not_offered_as_openable(workspace, tmp_path):
    """Reported, but not as a file: opening it would be refused every time."""
    (tmp_path / "outside.txt").write_text("private\n", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (workspace / "escape.txt").symlink_to(tmp_path / "outside.txt")
    (workspace / "escape-dir").symlink_to(tmp_path / "outside")

    entries = list_dir(str(workspace))["entries"]

    assert {entry["name"]: entry["type"] for entry in entries} == {
        "escape.txt": "other",
        "escape-dir": "other",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores the mode bits"
)
def test_an_unreadable_directory_does_not_read_as_a_missing_one(workspace):
    locked = workspace / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        result = list_dir(str(workspace), "locked")
    finally:
        locked.chmod(0o755)

    assert result["error"]["code"] == "workspace-file/unavailable"


def test_a_level_of_exactly_the_cap_is_not_reported_as_cut(workspace):
    for index in range(MAX_ENTRIES):
        (workspace / f"file-{index:04d}.txt").write_text("x", encoding="utf-8")

    listing = list_dir(str(workspace))

    assert len(listing["entries"]) == MAX_ENTRIES
    assert listing["truncated"] is False


def test_a_long_level_is_cut_and_says_so(workspace, monkeypatch):
    monkeypatch.setattr(workspace_files, "MAX_ENTRIES", 5)
    for index in range(8):
        (workspace / f"file-{index}.txt").write_text("x", encoding="utf-8")

    listing = list_dir(str(workspace))

    assert len(listing["entries"]) == 5
    assert listing["truncated"] is True


def test_a_cut_level_keeps_the_alphabetical_head(workspace, monkeypatch):
    """`truncated` claims a tail was cut, so a tail has to be what was cut.

    Cutting in `scandir` order means *some* N children survive: `a.txt` can be
    missing while `z.txt` is present, and Reload walks the same directory in the
    same order, so the missing one stays missing. Written in reverse so a naive
    cut would keep the alphabetical tail instead.
    """
    monkeypatch.setattr(workspace_files, "MAX_ENTRIES", 3)
    for index in reversed(range(6)):
        (workspace / f"file-{index}.txt").write_text("x", encoding="utf-8")

    names = [entry["name"] for entry in list_dir(str(workspace))["entries"]]

    assert names == ["file-0.txt", "file-1.txt", "file-2.txt"]


def test_a_cut_level_counts_rather_than_spells_its_numbers(workspace, monkeypatch):
    """Non-padded sequential names are what fills a directory past the cap.

    In codepoint order the survivors are `file1, file10, file11, file12, file2`
    — high numbers present, low ones missing, which is the arbitrary-looking
    hole the ordering exists to prevent.
    """
    monkeypatch.setattr(workspace_files, "MAX_ENTRIES", 5)
    for index in range(1, 13):
        (workspace / f"file{index}.txt").write_text("x", encoding="utf-8")

    names = [entry["name"] for entry in list_dir(str(workspace))["entries"]]

    assert names == [f"file{index}.txt" for index in range(1, 6)]


def test_a_name_carrying_a_digit_python_cannot_parse_does_not_take_the_level_out(workspace):
    """`isdigit()` is wider than `int()` accepts, and `\\d` is narrower than both.

    A superscript or a circled digit is `isdigit()` but not `isdecimal()`, and
    `\\d` never splits it out — so it reached the numeric branch as ordinary text
    and `int()` raised. The `ValueError` escaped the module, failed the RPC, and
    left the whole directory unlistable with Reload hitting the same path.
    """
    (workspace / "²").write_text("x", encoding="utf-8")
    (workspace / "①").write_text("x", encoding="utf-8")
    (workspace / "normal.txt").write_text("x", encoding="utf-8")

    listing = list_dir(str(workspace))

    assert listing["ok"] is True
    assert sorted(entry["name"] for entry in listing["entries"]) == sorted(
        ["²", "①", "normal.txt"]
    )


def test_a_non_ascii_decimal_still_sorts_as_a_number(workspace):
    """The narrowing must not cost the digits that do work: Arabic-Indic is
    `Nd`, so `\\d` splits it, `isdecimal()` is true, and `int()` reads it."""
    for numeral in ("١", "٢", "١٠"):
        (workspace / f"file{numeral}.txt").write_text("x", encoding="utf-8")

    names = [entry["name"] for entry in list_dir(str(workspace))["entries"]]

    assert names == ["file١.txt", "file٢.txt", "file١٠.txt"]


def test_the_cap_bounds_the_work_and_not_only_the_answer(workspace, monkeypatch):
    """Statting the whole level to return a slice of it is the same cost bug the
    cap exists to avoid: `readdir` hands over names for free, but every type and
    size below is a syscall per child.
    """
    statted: list[str] = []

    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name
            self.path = str(workspace / name)

        def is_symlink(self) -> bool:
            statted.append(self.name)
            return False

        def is_dir(self, follow_symlinks: bool = True) -> bool:
            statted.append(self.name)
            return False

        def is_file(self, follow_symlinks: bool = True) -> bool:
            statted.append(self.name)
            return True

        def stat(self, follow_symlinks: bool = True):
            statted.append(self.name)
            return os.stat_result((0o100644, 0, 0, 1, 0, 0, 7, 0, 0, 0))

    class _Scan:
        def __enter__(self):
            return iter([_Entry(f"file-{index}.txt") for index in reversed(range(50))])

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(workspace_files, "MAX_ENTRIES", 5)
    monkeypatch.setattr(workspace_files.os, "scandir", lambda _target: _Scan())

    listing = list_dir(str(workspace))

    assert [entry["name"] for entry in listing["entries"]] == [
        "file-0.txt",
        "file-1.txt",
        "file-2.txt",
        "file-3.txt",
        "file-4.txt",
    ]
    assert listing["truncated"] is True
    # Only the five that were returned were ever asked about.
    assert set(statted) == {entry["name"] for entry in listing["entries"]}


def test_listing_a_file_is_reported_as_not_a_directory(workspace):
    (workspace / "README.md").write_text("hi\n", encoding="utf-8")

    result = list_dir(str(workspace), "README.md")

    assert result["error"]["code"] == "workspace-file/not-directory"


def test_listing_a_missing_directory_is_reported_as_gone(workspace):
    result = list_dir(str(workspace), "nowhere")

    assert result["error"]["code"] == "workspace-file/not-found"


def test_listing_outside_the_workspace_is_refused(workspace):
    result = list_dir(str(workspace), "..")

    assert result["error"]["code"] == "workspace-file/outside-workspace"


# ── the gateway methods ──────────────────────────────────────────────────────


def test_the_reads_are_reachable_over_the_socket(tmp_path):
    """Registered, dispatched, and defaulted to the session's workspace.

    The module above is pure; this is the wiring — a method missing from the
    handler table is a sidebar that shows nothing, and no unit test of
    ``read_file`` would notice.
    """
    from starlette.testclient import TestClient

    from src.server.desktop_serve import build_app
    from tests.server.test_desktop_gateway import (
        TOKEN,
        _drain_for_response,
        _fake_state,
        _rpc,
    )

    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    state, _ = _fake_state(tmp_path)

    with TestClient(build_app(state)) as client, client.websocket_connect(
        f"/api/ws?token={TOKEN}"
    ) as ws:
        ws.receive_json()  # gateway.ready

        # The root comes from the server, not the call: an unknown session id
        # falls back to this server's workspace.
        _rpc(ws, "r1", "fs.list_dir", {"session_id": "nobody"})
        listing = _drain_for_response(ws, "r1", [])["result"]

        _rpc(ws, "r2", "fs.read_file", {"path": "notes.txt"})
        page = _drain_for_response(ws, "r2", [])["result"]

        _rpc(ws, "r3", "fs.read_file", {"path": "../outside.txt"})
        refused = _drain_for_response(ws, "r3", [])["result"]

        # The boundary is not a parameter: naming another root does not move it.
        _rpc(ws, "r4", "fs.read_file", {"cwd": "/", "path": "/etc/hosts"})
        elsewhere = _drain_for_response(ws, "r4", [])["result"]

        _rpc(ws, "r5", "fs.list_dir", {"cwd": "/", "path": "/etc"})
        listed_elsewhere = _drain_for_response(ws, "r5", [])["result"]

    assert listing["ok"] is True
    assert [entry["name"] for entry in listing["entries"]] == ["notes.txt"]
    assert (page["text"], page["lines"], page["eof"]) == ("alpha\nbeta", 2, True)
    assert refused["error"]["code"] == "workspace-file/outside-workspace"
    assert elsewhere["error"]["code"] == "workspace-file/outside-workspace"
    assert listed_elsewhere["error"]["code"] == "workspace-file/outside-workspace"


# ── read_bytes ───────────────────────────────────────────────────────────────


def test_reads_a_whole_file_as_base64(workspace):
    payload = b"\x89PNG\r\n\x1a\n\x00rest"
    (workspace / "logo.png").write_bytes(payload)

    result = read_bytes(str(workspace), "logo.png")

    assert result["ok"] is True
    assert base64.b64decode(result["data"]) == payload
    assert result["bytes"] == len(payload)
    assert result["offset"] == 0
    assert result["eof"] is True
    assert result["absolute_path"] == str((workspace / "logo.png").resolve())


def test_a_whole_file_over_the_cap_is_refused_with_its_limit(workspace, monkeypatch):
    monkeypatch.setattr(workspace_files, "MAX_FILE_BYTES", 8)
    (workspace / "big.bin").write_bytes(b"x" * 9)

    result = read_bytes(str(workspace), "big.bin")

    assert result["ok"] is False
    assert result["error"]["code"] == "workspace-file/too-large"
    assert result["error"]["details"]["limit"] == 8


def test_a_file_of_exactly_the_cap_is_read_whole(workspace, monkeypatch):
    monkeypatch.setattr(workspace_files, "MAX_FILE_BYTES", 8)
    (workspace / "fits.bin").write_bytes(b"x" * 8)

    result = read_bytes(str(workspace), "fits.bin")

    assert result["ok"] is True
    assert base64.b64decode(result["data"]) == b"x" * 8


def test_a_missing_or_non_regular_file_has_no_bytes(workspace):
    assert read_bytes(str(workspace), "gone.png")["error"]["code"] == "workspace-file/not-found"

    (workspace / "dir").mkdir()

    assert read_bytes(str(workspace), "dir")["error"]["code"] == "workspace-file/not-regular-file"


def test_bytes_are_confined_to_the_workspace_too(workspace, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")

    assert read_bytes(str(workspace), str(outside))["error"]["code"] == "workspace-file/outside-workspace"


# ── read_related ─────────────────────────────────────────────────────────────


def test_a_related_file_is_read_beside_its_document(workspace):
    (workspace / "site" / "css").mkdir(parents=True)
    (workspace / "site" / "index.html").write_text("<html>")
    (workspace / "site" / "css" / "app.css").write_text("body{}")

    result = read_related(str(workspace), "site/index.html", "css/app.css")

    assert result["ok"] is True
    assert base64.b64decode(result["data"]) == b"body{}"
    assert result["absolute_path"] == str((workspace / "site" / "css" / "app.css").resolve())


def test_a_related_file_may_climb_within_the_workspace(workspace):
    (workspace / "site").mkdir()
    (workspace / "site" / "index.html").write_text("<html>")
    (workspace / "shared.js").write_text("1")

    result = read_related(str(workspace), "site/index.html", "../shared.js")

    assert result["ok"] is True
    assert base64.b64decode(result["data"]) == b"1"


def test_a_related_path_stays_inside_the_workspace(workspace, tmp_path):
    (workspace / "index.html").write_text("<html>")
    (tmp_path / "secret.css").write_text("x")

    result = read_related(str(workspace), "index.html", "../secret.css")

    assert result["error"]["code"] == "workspace-file/outside-workspace"


@pytest.mark.parametrize("bad", ["/etc/hosts", "https://example.com/a.css", "", "a\x00b"])
def test_a_related_path_must_be_relative(workspace, bad):
    (workspace / "index.html").write_text("<html>")

    result = read_related(str(workspace), "index.html", bad)

    assert result["error"]["code"] == "workspace-file/bad-relative-path"
