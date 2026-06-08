"""Stage 6 — 性能守卫（< 3 秒）。

验证关键操作的响应时间在可接受范围内。
防止因意外引入重型 import 或阻塞操作导致的 CLI 启动缓慢。
"""

from __future__ import annotations

import time


class TestStage6Perf:
    """性能回归检测。"""

    def test_cli_help_import_time(self):
        """--help 快速路径不应导入重型模块。"""
        import subprocess
        import sys

        start = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv = ['clawcodex', '--help']; "
             "from src.cli import _build_parser; p = _build_parser(); "
             "p.parse_args(['--help'])"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        elapsed = time.monotonic() - start
        assert proc.returncode == 0, f"stderr={proc.stderr!r}"
        assert elapsed < 3.0, (
            f"CLI --help import took {elapsed:.2f}s, expected < 3s"
        )

    def test_conversation_import_time(self):
        """Conversation 模块导入不应拉入重型依赖。"""
        import subprocess
        import sys

        start = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-c",
             "from src.agent.conversation import Conversation"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        elapsed = time.monotonic() - start
        assert proc.returncode == 0, f"stderr={proc.stderr!r}"
        assert elapsed < 2.0, (
            f"Conversation import took {elapsed:.2f}s, expected < 2s"
        )

    def test_cli_subprocess_startup_time(self):
        """python -m src.cli --help 子进程启动时间。"""
        import subprocess
        import sys

        start = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "src.cli", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        elapsed = time.monotonic() - start
        assert proc.returncode == 0
        assert elapsed < 5.0, (
            f"CLI --help subprocess took {elapsed:.2f}s, expected < 5s"
        )
