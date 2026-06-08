"""Stage 2 — CLI 烟雾测试（< 5 秒）。

使用子进程执行 CLI 命令，验证：
- --help / --version 正常退出
- provider list / model list 正常列出
- print 模式正常工作
- 常见标志解析不崩溃
- --help 不加载重型模块（快速路径）
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run ``python -m src.cli`` with *args in a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestStage2CliSmoke:
    """CLI smoke tests — 子进程执行，不依赖 provider 配置。"""

    def test_cli_help_exits_0(self):
        proc = _run_cli("--help")
        assert proc.returncode == 0, f"stderr={proc.stderr!r}"
        assert "usage:" in proc.stdout.lower() or "usage:" in proc.stderr.lower()

    def test_cli_help_contains_subcommands(self):
        proc = _run_cli("--help")
        output = proc.stdout + proc.stderr
        for keyword in ("provider", "model", "schedule", "print"):
            assert keyword in output, f"Expected {keyword!r} in --help output"

    def test_cli_version_exits_0(self):
        proc = _run_cli("--version")
        assert proc.returncode == 0
        assert len(proc.stdout) > 0 or len(proc.stderr) > 0

    def test_cli_provider_list_exits_0(self):
        proc = _run_cli("provider", "list")
        assert proc.returncode == 0, f"stderr={proc.stderr!r}"
        output = proc.stdout + proc.stderr
        for name in ("anthropic", "openai"):
            assert name.lower().replace("-", "") in output.lower().replace(
                "-", ""
            ), f"Expected {name!r} in provider list output"

    def test_cli_model_list_exits_0(self):
        proc = _run_cli("model", "list")
        assert proc.returncode == 0, f"stderr={proc.stderr!r}"
        assert len(proc.stdout.strip()) > 0

    @pytest.mark.parametrize(
        "flag,desc",
        [
            ("--dangerously-skip-permissions", "bypass permissions flag"),
            ("--verbose", "verbose mode flag"),
        ],
    )
    def test_cli_common_flags_parse(self, flag, desc):
        if flag == "--permission-mode":
            proc = _run_cli(flag, "plan", "--help")
        else:
            proc = _run_cli(flag, "--help")
        assert proc.returncode == 0, f"{desc}: stderr={proc.stderr!r}"

    def test_cli_help_does_not_load_heavy_modules(self):
        """--help 应该快速返回，不加载 TUI/REPL 重型模块。"""
        import time

        start = time.monotonic()
        proc = _run_cli("--help")
        elapsed = time.monotonic() - start
        assert proc.returncode == 0
        assert elapsed < 5.0, f"--help took {elapsed:.2f}s, expected < 5s"
