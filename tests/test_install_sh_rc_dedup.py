"""Regression test for install.sh's update_shell_rc() PATH-patch dedup.

Bug: the dedup check compared the shell-EXPANDED real home directory
(``grep -qF "$HOME/.local/bin" "$rc"``, double-quoted so bash expands
``$HOME`` to e.g. ``/Users/alice/.local/bin`` before grep ever sees it)
against a line the function itself writes from a SINGLE-quoted variable
(``path_line='export PATH="$HOME/.local/bin:$PATH"'``), which lands in the
rc file as the literal, unexpanded text ``$HOME/.local/bin`` -- a literal
dollar sign, never the real path. The check could therefore never match the
installer's own prior write, and every re-run (reinstall, ``--local``,
``update``) appended another duplicate marker+PATH block to the user's rc
file, unbounded, forever.

This test extracts ``update_shell_rc`` (and its ``RC_MARKER`` dependency)
verbatim out of the real ``install.sh`` via ``sed`` -- not a hand-copied
approximation -- so it always exercises the actual current source, and
would catch a regression if someone reintroduces the mismatch.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"


def _extract_function(name: str) -> str:
    """Pull a top-level ``name() { ... }`` function verbatim out of install.sh."""
    result = subprocess.run(
        ["sed", "-n", f"/^{name}() {{/,/^}}/p", str(INSTALL_SH)],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip(), f"could not extract {name}() from install.sh"
    return result.stdout


def _extract_rc_marker() -> str:
    result = subprocess.run(
        ["grep", "-m", "1", "^readonly RC_MARKER=", str(INSTALL_SH)],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip(), "could not find RC_MARKER declaration in install.sh"
    return result.stdout


def _run_update_shell_rc(fake_home: Path, *, times: int = 1) -> str:
    """Run the REAL update_shell_rc() ``times`` times against a fake $HOME.

    Stubs only the logging helpers it calls (log_ok/log_warn) and the
    dry-run-preview helper (_script_p1); everything else is the unmodified
    function body extracted from install.sh.
    """
    harness = textwrap.dedent(
        """\
        log_ok() { :; }
        log_warn() { :; }
        _script_p1() { :; }
        DRY_RUN=0
        """
    )
    harness += _extract_rc_marker()
    harness += _extract_function("update_shell_rc")
    harness += "\n" + ("update_shell_rc\n" * times)

    script_path = fake_home / "_harness.sh"
    script_path.write_text(harness)

    subprocess.run(
        ["bash", str(script_path)],
        env={"HOME": str(fake_home), "PATH": "/usr/bin:/bin"},
        check=True, capture_output=True, text=True,
    )
    rc_path = fake_home / ".zshrc"
    return rc_path.read_text() if rc_path.exists() else ""


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    (tmp_path / ".zshrc").write_text("# pre-existing content\n")
    return tmp_path


def test_single_run_patches_exactly_once(fake_home: Path) -> None:
    content = _run_update_shell_rc(fake_home, times=1)
    assert content.count("clawcodex installer") == 1
    assert content.count('export PATH="$HOME/.local/bin:$PATH"') == 1


def test_rerun_does_not_duplicate(fake_home: Path) -> None:
    """THE regression guard: reinstall/update running update_shell_rc again
    must not append a second copy of the marker+PATH block."""
    once = _run_update_shell_rc(fake_home, times=1)
    thrice = _run_update_shell_rc(fake_home, times=3)
    assert thrice == once, (
        "re-running update_shell_rc changed the file — it should be a no-op "
        "once the marker is already present"
    )
    assert thrice.count("clawcodex installer") == 1


def test_five_consecutive_runs_in_one_process_stay_at_one_copy(fake_home: Path) -> None:
    content = _run_update_shell_rc(fake_home, times=5)
    assert content.count("clawcodex installer") == 1


def test_preexisting_tilde_path_is_recognized_and_not_repatched(
    tmp_path: Path,
) -> None:
    """A user who already has ~/.local/bin on PATH some other way must not
    get a redundant installer block appended."""
    (tmp_path / ".zshrc").write_text("export PATH=~/.local/bin:$PATH\n")
    content = _run_update_shell_rc(tmp_path, times=1)
    assert "clawcodex installer" not in content
    assert content == "export PATH=~/.local/bin:$PATH\n"


def test_preexisting_expanded_real_path_is_recognized(tmp_path: Path) -> None:
    """A pre-existing line spelled with the fully-expanded real path (what
    the OLD dedup check searched for) must still be recognized too."""
    (tmp_path / ".zshrc").write_text(
        f'export PATH="{tmp_path}/.local/bin:$PATH"\n'
    )
    content = _run_update_shell_rc(tmp_path, times=1)
    assert "clawcodex installer" not in content
