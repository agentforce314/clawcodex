"""``$CLAWCODEX_CONFIG_DIR`` has to cover config.json too.

``clawcodex_dirs`` documents the variable as the user config root and honors it
for sessions, memories, skills and projects. ``config.py`` used to hardcode
``Path.home() / ".clawcodex"`` beside it, so an isolated profile moved
everything EXCEPT the file holding the user's API keys — silently, with no
error and no warning.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _resolve(env_dir: str | None) -> str:
    """Import config in a fresh interpreter and report where it points.

    A subprocess because these are import-time constants: re-importing in this
    process would not re-evaluate them.
    """
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
    if env_dir is not None:
        env["CLAWCODEX_CONFIG_DIR"] = env_dir
    result = subprocess.run(
        [sys.executable, "-c", "from src.config import GLOBAL_CONFIG_FILE; print(GLOBAL_CONFIG_FILE)"],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_config_file_follows_the_override(tmp_path) -> None:
    assert _resolve(str(tmp_path)) == str(tmp_path / "config.json")


def test_config_file_defaults_to_the_home_directory() -> None:
    # Unset, nothing changes: this is what every ordinary run gets.
    assert _resolve(None) == str(Path.home() / ".clawcodex" / "config.json")


def test_the_override_agrees_with_the_dirs_helper(tmp_path, monkeypatch) -> None:
    # One root, not two that can disagree — which is how the bug arose.
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    from src.utils.clawcodex_dirs import get_user_config_dir

    assert get_user_config_dir() == tmp_path
    assert _resolve(str(tmp_path)).startswith(str(tmp_path))
