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

* exported in the host environment (the running provider's key vars are
  forwarded; unknown/absent provider falls back to the full allowlist), or
* passed explicitly: ``--ae DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}"``
  (Harbor wires ``--agent-env`` vars into every agent-phase exec itself).

Agent kwargs (``--ak key=value``):

* ``max_turns`` — clawcodex ``--max-turns`` (default 300 here; the CLI's
  own default of 50 is too low for terminal-bench tasks). The
  ``CLAWCODEX_MAX_TURNS`` host env var works as a fallback.
* ``effort`` — clawcodex ``--effort`` (low|medium|high|xhigh|max) for
  models that support ``output_config.effort`` (Opus 4.6/4.8, Sonnet 4.6,
  Fable 5). ``xhigh`` is model-dependent (opus-4-8 yes; sonnet-4-6/
  opus-4-6 no) — clawcodex degrades it to ``high`` where rejected.
  ``CLAWCODEX_EFFORT`` host env var works as a fallback.
* ``version`` — pin a ``clawcodex-cli`` PyPI version (default: latest).
* ``source`` — full pip-installable spec overriding the PyPI package, e.g.
  ``git+https://github.com/agentforce314/clawcodex@main`` to eval unreleased
  code. Mutually exclusive with ``version``.
* ``subscription`` — ``true`` to authenticate the Anthropic provider with a
  Claude Pro/Max subscription instead of an API key. Reads (and refreshes)
  the host's ``~/.clawcodex/anthropic-oauth.json`` — created by
  ``clawcodex login`` — and injects it into each container OUTSIDE the
  synced ``/logs`` tree; ``ANTHROPIC_API_KEY`` is deliberately not
  forwarded so clawcodex takes the OAuth path.
"""

import json
import os
import re
import shlex
import threading
import time
import urllib.request
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

# Host env vars forwardable into the container (clawcodex's builtin provider
# key candidates — see src/providers/__init__.py in the clawcodex repo), keyed
# by provider so an eval only exposes the key(s) of the provider it runs:
# agents shell out to `env` often enough, and /logs/agent syncs to the host
# jobs dir. Unknown/absent provider falls back to the full allowlist. Keys
# passed via --agent-env are injected by Harbor itself and don't appear here.
_PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "zai": ("ZAI_API_KEY", "Z_AI_API_KEY"),
    "minimax": ("MINIMAX_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}
_ALL_PROVIDER_ENV_VARS: tuple[str, ...] = tuple(
    dict.fromkeys(key for keys in _PROVIDER_ENV_VARS.values() for key in keys)
)

_PATH_EXPORT = 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"'

# Managed CPython pin for the uv tool env — deterministic across task images
# regardless of (or absent) system Pythons. clawcodex-cli requires >= 3.10.
_PYTHON_PIN = "3.13"

# ---------------------------------------------------------------------------
# Claude subscription (OAuth) support.
#
# Constants mirror clawcodex's src/auth/anthropic_subscription.py (the host
# file format is defined there). The refresh POST is duplicated here because
# this module runs inside Harbor's venv where clawcodex is not importable.
_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
# Cloudflare bot-blocks the default Python-urllib UA with a 403 (code 1010);
# clawcodex sends this same identity.
_OAUTH_USER_AGENT = "claude-cli/2.1.2 (external, cli)"
_oauth_refresh_lock = threading.Lock()


def _oauth_file() -> Path:
    override_path = os.environ.get("CLAWCODEX_OAUTH_FILE")
    if override_path:
        return Path(override_path)
    return Path.home() / ".clawcodex" / "anthropic-oauth.json"


def _load_host_oauth() -> dict[str, Any] | None:
    try:
        value = json.loads(_oauth_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not value.get("access_token"):
        return None
    return value


def _save_host_oauth(credentials: dict[str, Any]) -> None:
    path = _oauth_file()
    tmp = path.with_suffix(f".tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(credentials, stream, indent=2)
        stream.write("\n")
    os.replace(tmp, path)


def fresh_subscription_credentials() -> dict[str, Any]:
    """Host-side equivalent of clawcodex's ``get_valid_credentials``.

    Returns the credentials dict, refreshing (and persisting back to the
    host file) when the access token is within 5 minutes of expiry so every
    trial starts with far more runway than a task's agent timeout. Raises
    RuntimeError with a remedial message when no usable credentials exist.
    """
    with _oauth_refresh_lock:
        credentials = _load_host_oauth()
        if credentials is None:
            raise RuntimeError(
                f"No Claude subscription credentials at {_oauth_file()} — "
                "run `clawcodex login` on the host first (or set "
                "CLAWCODEX_OAUTH_FILE)."
            )
        if float(credentials.get("expires_at", 0)) > time.time() + 300:
            return credentials

        request = urllib.request.Request(
            _OAUTH_TOKEN_URL,
            data=json.dumps({
                "grant_type": "refresh_token",
                "refresh_token": credentials.get("refresh_token", ""),
                "client_id": _OAUTH_CLIENT_ID,
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": _OAUTH_USER_AGENT,
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — surfaced with context below
            raise RuntimeError(
                "Claude subscription token refresh failed — re-run "
                f"`clawcodex login` on the host. ({exc})"
            ) from exc

        refreshed = {
            "access_token": str(result["access_token"]),
            "refresh_token": str(
                result.get("refresh_token") or credentials.get("refresh_token", "")
            ),
            "expires_at": time.time() + float(result.get("expires_in", 3600)),
            "scope": str(result.get("scope", credentials.get("scope", ""))),
        }
        _save_host_oauth(refreshed)
        return refreshed


# Container-side config dir. Deliberately OUTSIDE /logs: Harbor syncs
# /logs/agent back to the host jobs dir (and `--upload` can publish it), and
# this directory holds anthropic-oauth.json in subscription mode. Sessions/
# transcripts are copied back into /logs/agent/sessions post-run, minus the
# token file. Must be an absolute literal — env values are not shell-expanded.
_CONTAINER_CONFIG_DIR = "/installed-agent/clawcodex-config"


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
        CliFlag(
            "effort",
            cli="--effort",
            type="enum",
            choices=["low", "medium", "high", "xhigh", "max"],
            env_fallback="CLAWCODEX_EFFORT",
        ),
    ]

    def __init__(
        self,
        logs_dir: Path,
        subscription: bool | str = False,
        source: str | None = None,
        *args,
        **kwargs,
    ):
        from harbor.utils.env import parse_bool_env_value

        self._subscription = parse_bool_env_value(subscription, name="subscription")
        self._source = source
        super().__init__(logs_dir, *args, **kwargs)
        if self._source and self._version:
            raise ValueError(
                "Agent kwargs 'source' and 'version' are mutually exclusive"
            )

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
        # A git-sourced install (uv shells out to the git CLI) also needs git.
        need_git = bool(self._source and "git+" in self._source)
        git_check = " && command -v git >/dev/null 2>&1" if need_git else ""
        git_pkg = " git" if need_git else ""

        # System prerequisites (root): curl + CA certs for the uv installer.
        # Mirrors the package-manager sieve Harbor's Claude Code agent uses.
        await self.exec_as_root(
            environment,
            command=(
                "command -v curl >/dev/null 2>&1 && "
                "{ [ -f /etc/ssl/certs/ca-certificates.crt ] || "
                f"[ -f /etc/pki/tls/certs/ca-bundle.crt ]; }}{git_check} || {{ "
                "if command -v apk >/dev/null 2>&1; then"
                f"  apk add --no-cache curl ca-certificates{git_pkg};"
                " elif command -v apt-get >/dev/null 2>&1; then"
                f"  apt-get update && apt-get install -y curl ca-certificates{git_pkg};"
                " elif command -v yum >/dev/null 2>&1; then"
                f"  yum install -y curl ca-certificates{git_pkg};"
                " else"
                '  echo "Warning: no known package manager; assuming curl exists" >&2;'
                " fi; }"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        if self._source:
            install_spec = self._source
        elif self._version:
            install_spec = f"clawcodex-cli=={self._version}"
        else:
            install_spec = "clawcodex-cli"
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
                f"uv tool install --python {_PYTHON_PIN} {shlex.quote(install_spec)} && "
                "clawcodex --version"
            ),
        )

        # Pre-create the container config dir (see _CONTAINER_CONFIG_DIR).
        # 1777 so a non-root default agent user can write it too; the oauth
        # file itself is written 0600 by the run-phase umask.
        await self.exec_as_root(
            environment,
            command=(
                f"mkdir -p {_CONTAINER_CONFIG_DIR} && "
                f"chmod 1777 {_CONTAINER_CONFIG_DIR}"
            ),
        )

    def _build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self._subscription:
            # Subscription mode authenticates via the injected OAuth file;
            # forwarding ANTHROPIC_API_KEY would silently win over it inside
            # clawcodex (API key takes precedence), billing the API instead.
            forwarded: tuple[str, ...] = ()
        else:
            forwarded = _PROVIDER_ENV_VARS.get(
                (self._parsed_model_provider or "").lower(), _ALL_PROVIDER_ENV_VARS
            )
        for key in forwarded:
            value = self._get_env(key)
            if value:
                env[key] = value

        # Open clawcodex's root safety gate for --dangerously-skip-permissions
        # (task containers usually run the agent as root).
        env["IS_SANDBOX"] = "1"
        # Config dir lives OUTSIDE /logs (token privacy in subscription
        # mode); run() copies sessions/transcripts back into /logs/agent
        # post-run so the host still gets them.
        env["CLAWCODEX_CONFIG_DIR"] = _CONTAINER_CONFIG_DIR
        env["NO_COLOR"] = "1"
        return env

    async def _inject_subscription_credentials(
        self, environment: BaseEnvironment
    ) -> None:
        """Write freshly-refreshed host OAuth credentials into the container.

        The JSON travels via the exec env (never the command string, which
        is logged) and lands 0600 outside the synced /logs tree.
        """
        provider = (self._parsed_model_provider or "anthropic").lower()
        if provider != "anthropic":
            raise RuntimeError(
                "subscription=true only applies to anthropic/... models "
                f"(got provider {provider!r})"
            )
        credentials = fresh_subscription_credentials()
        await self.exec_as_agent(
            environment,
            command=(
                'umask 077; printf \'%s\' "$CLAWCODEX_OAUTH_JSON" > '
                f"{_CONTAINER_CONFIG_DIR}/anthropic-oauth.json"
            ),
            env={"CLAWCODEX_OAUTH_JSON": json.dumps(credentials)},
        )

    @with_prompt_template
    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._subscription:
            await self._inject_subscription_credentials(environment)

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

        # NOTE: build_cli_flags() output is NOT shell-quoted by the base
        # class — safe while every CLI_FLAGS entry is int/enum-typed; quote
        # at the descriptor level before adding any string-typed flag.
        cli_flags = self.build_cli_flags()
        if cli_flags:
            parts.append(cli_flags)

        log_path = (EnvironmentPaths.agent_dir / "clawcodex.txt").as_posix()
        sessions_sync_dir = (EnvironmentPaths.agent_dir / "sessions").as_posix()
        command = (
            f"{_PATH_EXPORT}; "
            f"{' '.join(parts)} -- {shlex.quote(instruction)} "
            f"2>&1 </dev/null | tee {log_path}; rc=$?; "
            # Copy session/transcript logs back into the synced tree for
            # host-side debugging — minus the OAuth token file. Preserves
            # the clawcodex exit code (pipefail is set by the base _exec).
            f"mkdir -p {sessions_sync_dir} && "
            f"cp -r {_CONTAINER_CONFIG_DIR}/. {sessions_sync_dir}/ 2>/dev/null; "
            f"rm -f {sessions_sync_dir}/anthropic-oauth.json; "
            "exit $rc"
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
