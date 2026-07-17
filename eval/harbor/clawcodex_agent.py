"""Harbor installed-agent adapter for clawcodex (PyPI: ``clawcodex-cli``).

Lets the `Harbor <https://github.com/harbor-framework/harbor>`_ eval framework
run clawcodex headless inside task containers (e.g. terminal-bench 2.0):

    export DEEPSEEK_API_KEY=sk-...
    PYTHONPATH=eval/harbor harbor run \
        --dataset terminal-bench@2.0 \
        --agent clawcodex_agent:Clawcodex \
        --model deepseek/deepseek-v4-flash

Harbor imports this module in the *host* process (put its directory on
``PYTHONPATH``), then drives the container over ``environment.exec``:

* ``install()`` — bootstraps ``uv`` (standalone binary, so the task image
  needs no preinstalled Python) and installs ``clawcodex-cli`` from PyPI
  with a pinned managed CPython.
* ``run()`` — invokes ``clawcodex --print`` with
  ``--dangerously-skip-permissions`` (containers run as root; clawcodex's
  root safety gate is opened with ``IS_SANDBOX=1``, mirroring how Harbor
  drives Claude Code) and tees stream-json output to ``/logs/agent/`` which
  Harbor syncs back to the host trial directory.

Model names use Harbor's ``provider/model`` convention: the prefix becomes
``--provider`` and the rest ``--model`` (``deepseek/deepseek-v4-flash`` →
``--provider deepseek --model deepseek-v4-flash``). A bare name is passed
through as ``--model`` alone, falling back to clawcodex's own routing.

API keys reach the container two ways, either is sufficient:

* exported in the host environment (the allowlist below is forwarded), or
* passed explicitly: ``--ae DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}"``
  (Harbor wires ``--agent-env`` vars into every agent-phase exec itself).

Agent kwargs (``--ak key=value``):

* ``max_turns`` — clawcodex ``--max-turns`` (default 300 here; the CLI's
  own default of 50 is too low for terminal-bench tasks).
* ``version`` — pin a ``clawcodex-cli`` PyPI version (default: latest).
"""

import json
import re
import shlex
from typing import Any, override

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    CliFlag,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

# Host env vars forwarded into the container when set (clawcodex's builtin
# provider key candidates — see src/providers/__init__.py in the clawcodex
# repo). Keys passed via --agent-env are injected by Harbor itself and don't
# need to appear here.
_FORWARDED_ENV_VARS: tuple[str, ...] = (
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ZAI_API_KEY",
    "Z_AI_API_KEY",
    "MINIMAX_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)

_PATH_EXPORT = 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"'

# Managed CPython pin for the uv tool env — deterministic across task images
# regardless of (or absent) system Pythons. clawcodex-cli requires >= 3.10.
_PYTHON_PIN = "3.13"


class Clawcodex(BaseInstalledAgent):
    """Run the clawcodex CLI headless inside a Harbor task environment."""

    CLI_FLAGS = [
        CliFlag(
            "max_turns",
            cli="--max-turns",
            type="int",
            default=300,
            env_fallback="CLAWCODEX_MAX_TURNS",
        ),
    ]

    @staticmethod
    @override
    def name() -> str:
        return "clawcodex"

    @override
    def get_version_command(self) -> str | None:
        return f"{_PATH_EXPORT}; clawcodex --version"

    @override
    def parse_version(self, stdout: str) -> str:
        # Output format: "claw-codex version 1.2.1 (Python)"
        match = re.search(r"(\d+\.\d+\.\d+)", stdout.strip())
        return match.group(1) if match else stdout.strip()

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        # System prerequisites (root): curl + CA certs for the uv installer.
        # Mirrors the package-manager sieve Harbor's Claude Code agent uses.
        await self.exec_as_root(
            environment,
            command=(
                "command -v curl >/dev/null 2>&1 && [ -d /etc/ssl ] || { "
                "if command -v apk >/dev/null 2>&1; then"
                "  apk add --no-cache curl ca-certificates bash;"
                " elif command -v apt-get >/dev/null 2>&1; then"
                "  apt-get update && apt-get install -y curl ca-certificates;"
                " elif command -v yum >/dev/null 2>&1; then"
                "  yum install -y curl ca-certificates;"
                " else"
                '  echo "Warning: no known package manager; assuming curl exists" >&2;'
                " fi; }"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        version_spec = (
            f"clawcodex-cli=={self._version}" if self._version else "clawcodex-cli"
        )
        # uv is a static binary; `uv tool install --python` fetches a managed
        # CPython, so this works even on images with no usable Python.
        await self.exec_as_agent(
            environment,
            command=(
                "set -eu; "
                f"{_PATH_EXPORT}; "
                "command -v uv >/dev/null 2>&1 || "
                "curl -LsSf https://astral.sh/uv/install.sh | sh; "
                f"{_PATH_EXPORT}; "
                f"uv tool install --python {_PYTHON_PIN} {shlex.quote(version_spec)} && "
                "clawcodex --version"
            ),
        )

    def _build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for key in _FORWARDED_ENV_VARS:
            value = self._get_env(key)
            if value:
                env[key] = value

        # Open clawcodex's root safety gate for --dangerously-skip-permissions
        # (task containers usually run the agent as root).
        env["IS_SANDBOX"] = "1"
        # Keep sessions/transcripts under /logs/agent so Harbor syncs them
        # back to the host trial directory for debugging.
        env["CLAWCODEX_CONFIG_DIR"] = (
            EnvironmentPaths.agent_dir / "sessions"
        ).as_posix()
        env["NO_COLOR"] = "1"
        return env

    @with_prompt_template
    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        parts: list[str] = [
            "clawcodex",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if self._parsed_model_name:
            parts += ["--model", shlex.quote(self._parsed_model_name)]
        if self._parsed_model_provider:
            parts += ["--provider", shlex.quote(self._parsed_model_provider)]

        cli_flags = self.build_cli_flags()
        if cli_flags:
            parts.append(cli_flags)

        log_path = (EnvironmentPaths.agent_dir / "clawcodex.txt").as_posix()
        command = (
            f"{_PATH_EXPORT}; "
            f"{' '.join(parts)} -- {shlex.quote(instruction)} "
            f"2>&1 </dev/null | tee {log_path}"
        )

        await self.exec_as_agent(environment, command=command, env=self._build_env())

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """Backfill token metrics from the final stream-json ``result`` event."""
        stream_path = self.logs_dir / "clawcodex.txt"
        try:
            content = stream_path.read_text(encoding="utf-8")
        except OSError:
            return

        result_event: dict[str, Any] | None = None
        for line in content.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result_event = event

        if not result_event:
            return

        usage = result_event.get("usage")
        if not isinstance(usage, dict):
            return
        input_tokens = usage.get("input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        cache_creation = usage.get("cache_creation_input_tokens") or 0
        context.n_input_tokens = input_tokens + cache_read + cache_creation
        context.n_cache_tokens = cache_read
        context.n_output_tokens = usage.get("output_tokens") or 0
