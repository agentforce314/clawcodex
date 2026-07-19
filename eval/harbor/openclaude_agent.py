"""Harbor installed-agent adapter for openclaude — the vendored TypeScript
Claude Code implementation at ``<repo>/typescript`` (``@gitlawb/openclaude``).

Runs the SAME benchmark harness as ``clawcodex_agent.py`` but with the old
TS implementation, for apples-to-apples comparisons:

    PYTHONPATH=eval/harbor harbor run \
        --dataset terminal-bench/terminal-bench-2-1 \
        --agent openclaude_agent:OpenClaude \
        --model anthropic/claude-opus-4-8 \
        --ak subscription=true \
        --ak effort=high

Unlike clawcodex (installed from PyPI/git inside the container), openclaude
ships as a single prebuilt bundle: the adapter uploads the host-built
``typescript/dist/cli.mjs`` (build with ``bun run build`` in ``typescript/``)
into each container and bootstraps Node >= 22 to run it.

Auth: ``subscription=true`` reuses :func:`clawcodex_agent.
fresh_subscription_credentials` (same host ``anthropic-oauth.json``, same
30-min-runway refresh) but injects ONLY the access token, via the
``CLAUDE_CODE_OAUTH_TOKEN`` env var — openclaude treats it as an
inference-only claude.ai token (typescript/src/utils/auth.ts:1280-1290:
``scopes: ['user:inference']``, no refresh token). Nothing credential-shaped
is ever written to disk in the container, so the config dir can live under
the synced ``/logs`` tree and session logs sync back for free.

``--provider anthropic`` is ALWAYS passed: openclaude is the any-LLM fork
and will otherwise auto-route to whatever provider credentials it detects
(observed live: a ChatGPT/Codex profile hijacking an opus model request).

Agent kwargs (``--ak key=value``):

* ``max_turns`` — openclaude ``--max-turns`` (present but hidden from
  ``--help`` in this fork); default 300 mirrors the clawcodex adapter.
* ``effort`` — openclaude ``--effort`` (low|medium|high|xhigh|max as of
  0.24.0). The TS flag is session-wide, subagents included.
* ``subscription`` — ``true`` to use the Claude Pro/Max subscription via
  the host's ``~/.clawcodex/anthropic-oauth.json`` (see clawcodex_agent).
* ``dist`` — host path to ``cli.mjs`` (default:
  ``<repo>/typescript/dist/cli.mjs`` next to this file, or the
  ``OPENCLAUDE_DIST`` env var). The worktree checkouts don't carry
  ``typescript/`` (gitignored) — run from the main checkout or point this
  at one.

Version note: at 0.13.0 the model metadata predated claude-opus-4-8
(conservative 128k compaction assumption); 0.24.0 registers it properly
(1M context / 128K output), so rebuilt bundles get correct compaction.
"""

import json
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    CliFlag,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

# Shared host-side subscription helpers (same PYTHONPATH directory).
from clawcodex_agent import fresh_subscription_credentials

_CONTAINER_APP = "/installed-agent/openclaude/cli.mjs"
_CONTAINER_NODE_DIR = "/installed-agent/node22"
# Official glibc build; musl (alpine) containers fall back to apk nodejs.
_NODE_VERSION = "22.20.0"

# The CLI bundle keeps these packages external (typescript/scripts/
# externals.ts CLI_EXTERNALS) — native binaries and heavy SDKs resolved from
# node_modules at runtime, so each container npm-installs them next to
# cli.mjs (platform-correct sharp/ripgrep binaries). The full COMMON_EXTERNALS
# set, pinned to the working host tree (typescript/node_modules @ openclaude
# 0.24.0, 2026-07-19 — all present there, unlike the 0.13.0 tree where three
# were absent lazy imports).
_RUNTIME_EXTERNALS = (
    "sharp@0.34.5",
    "@aws-sdk/client-bedrock@3.1047.0",
    "@aws-sdk/client-bedrock-runtime@3.1047.0",
    "@aws-sdk/client-sts@3.1047.0",
    "@aws-sdk/credential-provider-node@3.972.41",
    "@aws-sdk/credential-providers@3.1047.0",
    "@smithy/core@3.24.3",
    "@smithy/node-http-handler@4.7.3",
    "@azure/identity@4.13.1",
    "google-auth-library@10.6.2",
    "@vscode/ripgrep@1.18.0",
    "@orama/orama@3.1.18",
    "@orama/plugin-data-persistence@3.1.18",
)

_PATH_EXPORT = (
    f'export PATH="{_CONTAINER_NODE_DIR}/bin:$HOME/.local/bin:$PATH"'
)


def _default_dist() -> Path:
    import os

    env = os.environ.get("OPENCLAUDE_DIST")
    if env:
        return Path(env)
    return (
        Path(__file__).resolve().parent.parent.parent
        / "typescript"
        / "dist"
        / "cli.mjs"
    )


class OpenClaude(BaseInstalledAgent):
    """Run the vendored TS Claude Code (openclaude) inside a Harbor task."""

    CLI_FLAGS = [
        # --max-turns exists in this fork but is .hideHelp() (main.tsx:978);
        # default 300 mirrors the clawcodex adapter for comparability.
        CliFlag(
            "max_turns",
            cli="--max-turns",
            type="int",
            default=300,
            env_fallback="OPENCLAUDE_MAX_TURNS",
        ),
        CliFlag(
            "effort",
            cli="--effort",
            type="enum",
            # 0.24.0 ladder ("ultracode" — the workflow-orchestration mode —
            # deliberately excluded for harness comparability).
            choices=["low", "medium", "high", "xhigh", "max"],
            env_fallback="OPENCLAUDE_EFFORT",
        ),
    ]

    def __init__(
        self,
        logs_dir: Path,
        subscription: bool | str = False,
        dist: str | None = None,
        *args,
        **kwargs,
    ):
        from harbor.utils.env import parse_bool_env_value

        self._subscription = parse_bool_env_value(subscription, name="subscription")
        self._dist = Path(dist) if dist else _default_dist()
        super().__init__(logs_dir, *args, **kwargs)
        if not self._dist.is_file():
            raise ValueError(
                f"openclaude bundle not found at {self._dist} — build it with "
                "`bun run build` in the typescript/ directory of the main "
                "checkout (worktrees don't carry typescript/), or pass "
                "--ak dist=/path/to/cli.mjs / set OPENCLAUDE_DIST."
            )
        if self._subscription and "ANTHROPIC_API_KEY" in self._extra_env:
            raise ValueError(
                "subscription=true conflicts with --ae ANTHROPIC_API_KEY=... "
                "(the key would override OAuth inside openclaude and bill the "
                "API); drop one of the two."
            )

    @staticmethod
    @override
    def name() -> str:
        return "openclaude"

    @override
    def get_version_command(self) -> str | None:
        return f"{_PATH_EXPORT}; node {_CONTAINER_APP} --version"

    @override
    def parse_version(self, stdout: str) -> str:
        import re

        match = re.search(r"(\d+\.\d+\.\d+)", stdout.strip())
        return match.group(1) if match else stdout.strip()

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        # curl + CA certs for the node download (root).
        await self.exec_as_root(
            environment,
            command=(
                "command -v curl >/dev/null 2>&1 && "
                "{ [ -f /etc/ssl/certs/ca-certificates.crt ] || "
                "[ -f /etc/pki/tls/certs/ca-bundle.crt ]; } || { "
                "if command -v apk >/dev/null 2>&1; then"
                "  apk add --no-cache curl ca-certificates;"
                " elif command -v apt-get >/dev/null 2>&1; then"
                "  apt-get update && apt-get install -y curl ca-certificates;"
                " elif command -v yum >/dev/null 2>&1; then"
                "  yum install -y curl ca-certificates;"
                " else"
                '  echo "Warning: no known package manager; assuming curl'
                ' available" >&2;'
                " fi; }"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        # Node >= 22 (with npm, needed for the runtime externals): keep a
        # suitable system node; otherwise official tarball (glibc) or, on
        # musl images, the distro packages.
        await self.exec_as_root(
            environment,
            command=(
                "set -eu; "
                "have_node() { command -v node >/dev/null 2>&1 && "
                "command -v npm >/dev/null 2>&1 && "
                '[ "$(node -p \'process.versions.node.split(".")[0]\')" -ge 22 ]; }; '
                "if have_node; then echo 'system node OK'; "
                "elif [ -f /etc/alpine-release ]; then apk add --no-cache nodejs npm; "
                "  have_node || { echo 'alpine nodejs too old (<22)' >&2; exit 1; }; "
                "else "
                f"  mkdir -p {_CONTAINER_NODE_DIR}; "
                '  arch=$(uname -m); case "$arch" in '
                "    x86_64) narch=x64;; aarch64|arm64) narch=arm64;; "
                '    *) echo "unsupported arch $arch" >&2; exit 1;; esac; '
                f"  curl -fsSL https://nodejs.org/dist/v{_NODE_VERSION}/"
                f"node-v{_NODE_VERSION}-linux-$narch.tar.gz "
                f"  | tar -xz -C {_CONTAINER_NODE_DIR} --strip-components=1; "
                f"  {_CONTAINER_NODE_DIR}/bin/node --version; "
                "fi"
            ),
        )

        # Upload the host-built bundle and install its runtime externals
        # next to it (import resolution walks up from the bundle's dir).
        await self.exec_as_root(
            environment, command=f"mkdir -p {Path(_CONTAINER_APP).parent}"
        )
        await environment.upload_file(self._dist, _CONTAINER_APP)
        externals = " ".join(shlex.quote(p) for p in _RUNTIME_EXTERNALS)
        await self.exec_as_root(
            environment,
            command=(
                f"set -eu; {_PATH_EXPORT}; chmod 644 {_CONTAINER_APP}; "
                f"cd {Path(_CONTAINER_APP).parent} && "
                f"npm install --no-save --no-audit --no-fund {externals} && "
                f"node {_CONTAINER_APP} --version"
            ),
        )

    def _build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if not self._subscription:
            from clawcodex_agent import _PROVIDER_ENV_VARS

            provider = (self._parsed_model_provider or "anthropic").lower()
            for key in _PROVIDER_ENV_VARS.get(provider, ("ANTHROPIC_API_KEY",)):
                value = self._get_env(key)
                if value:
                    env[key] = value

        # Same root safety gate as upstream Claude Code (setup.ts:382-401).
        env["IS_SANDBOX"] = "1"
        # Auth is env-only in subscription mode (no credential file is ever
        # written — verified: the env-token path is inference-only and every
        # persist/refresh writer skips it), so the config dir can live in
        # the synced tree — session logs come back to the host for free,
        # like Harbor's claude-code agent does it.
        env["CLAUDE_CONFIG_DIR"] = (
            EnvironmentPaths.agent_dir / "sessions"
        ).as_posix()
        # The fork's own defense for exactly this setup (subprocessEnv.ts:
        # 79-99): scrub CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY et al.
        # from Bash-tool subprocess envs, so a prompt-injecting task can't
        # `env` the token into tool output → transcripts → the bind-mounted
        # /logs tree. Opt-in upstream; always on here.
        env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        env["NO_COLOR"] = "1"
        return env

    @with_prompt_template
    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        env = self._build_env()

        provider = (self._parsed_model_provider or "anthropic").lower()
        if self._subscription:
            if provider != "anthropic":
                raise RuntimeError(
                    "subscription=true only applies to anthropic/... models "
                    f"(got provider {provider!r})"
                )
            import asyncio

            credentials = await asyncio.to_thread(fresh_subscription_credentials)
            env["CLAUDE_CODE_OAUTH_TOKEN"] = credentials["access_token"]

        parts: list[str] = [
            "node",
            _CONTAINER_APP,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            # Always pinned: without it the any-LLM fork auto-routes to
            # whatever provider credentials it detects.
            "--provider",
            shlex.quote(provider),
        ]
        if self._parsed_model_name:
            parts += ["--model", shlex.quote(self._parsed_model_name)]

        cli_flags = self.build_cli_flags()
        if cli_flags:
            parts.append(cli_flags)

        setup = (
            'mkdir -p "$CLAUDE_CONFIG_DIR/projects" "$CLAUDE_CONFIG_DIR/todos" '
            '"$CLAUDE_CONFIG_DIR/shell-snapshots"'
        )
        log_path = (EnvironmentPaths.agent_dir / "openclaude.txt").as_posix()
        command = (
            f"{_PATH_EXPORT}; {setup}; "
            f"{' '.join(parts)} -- {shlex.quote(instruction)} "
            f"2>&1 </dev/null | tee {log_path}"
        )

        await self.exec_as_agent(environment, command=command, env=env)

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """Backfill cost/token metrics from the stream-json ``result`` event."""
        stream_path = self.logs_dir / "openclaude.txt"
        try:
            content = stream_path.read_text(encoding="utf-8", errors="replace")
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

        cost = result_event.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            context.cost_usd = float(cost)

        usage = result_event.get("usage")
        if not isinstance(usage, dict):
            return
        input_tokens = usage.get("input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        cache_creation = usage.get("cache_creation_input_tokens") or 0
        context.n_input_tokens = input_tokens + cache_read + cache_creation
        context.n_cache_tokens = cache_read
        context.n_output_tokens = usage.get("output_tokens") or 0
