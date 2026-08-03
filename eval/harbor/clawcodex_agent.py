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

The split is on the FIRST slash only, so an OpenRouter id — itself
``vendor/model`` — round-trips correctly by stacking the two conventions:
``openrouter/openai/gpt-5.6-luna`` → ``--provider openrouter --model
openai/gpt-5.6-luna``. For example, terminal-bench 2.1 at maximum
reasoning effort::

    export OPENROUTER_API_KEY=sk-or-v1-...
    PYTHONPATH=eval/harbor harbor run \
        --dataset terminal-bench/terminal-bench-2-1 \
        --agent clawcodex_agent:Clawcodex \
        --model openrouter/openai/gpt-5.6-luna \
        --ak effort=max \
        --jobs-dir eval/harbor/jobs --n-concurrent 4

API keys reach the container two ways, either is sufficient:

* exported in the host environment (the running provider's key vars are
  forwarded; unknown/absent provider falls back to the full allowlist), or
* passed explicitly: ``--ae DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}"``
  (Harbor wires ``--agent-env`` vars into every agent-phase exec itself).

Agent kwargs (``--ak key=value``):

* ``advisor`` — run with an ADVISOR (reviewer) model the worker consults
  through the advisor tool, as ``<provider>:<model>`` (e.g.
  ``advisor=anthropic:claude-opus-5``). Seeded into the container's global
  config rather than passed as a flag: clawcodex reads the advisor from
  settings, and ``/advisor`` is not reachable on the headless ``-p`` path.
  Always seeded as CLIENT-side dispatch (a separate API call per
  consultation), so the advisor behaves identically regardless of which
  worker model a run is comparing — the server-side path is an
  Anthropic-only beta that also requires a 1P Anthropic main loop.

  Combines with ``subscription=true`` even when the MAIN model is another
  vendor: ``--model openai/gpt-5.6-luna --ak subscription=true
  --ak advisor=anthropic:claude-opus-5`` runs an API-key worker against a
  Claude-subscription reviewer. The worker's own provider key is still
  forwarded in that configuration; only ``ANTHROPIC_API_KEY`` is withheld,
  so OAuth stays the single route to the subscription.

  NOTE the advisor doubles the API calls on turns where it fires, and each
  consultation forwards the whole conversation so far — budget for it.
* ``advisor_effort`` — reasoning effort for the ADVISOR's own call
  (low|medium|high|xhigh|max), independent of the worker's ``effort``.
  Requires ``advisor``. Unset means the advisor inherits ``effort``; if
  that is unset too the parameter is omitted and the API applies its model
  default. Subject to the same per-wire gates as ``effort`` below — on the
  Anthropic wire ``xhigh`` is clamped to ``high`` for models that reject it
  (Opus 5 accepts it; Sonnet 4.6 does not).
* ``fusion`` — run a FUSION model: a text-only base plus a borrowed
  vision model, as ``<base>+<vision>`` with each side ``provider:model``
  (e.g. ``fusion=deepseek:deepseek-v4-flash+openai:gpt-5.6-luna``). Pair
  it with ``--model <fusion-name>``; the seeded record is named after that
  so clawcodex can resolve the selector. Needed because a fusion model
  lives in the user's global config rather than being addressable as
  ``provider/model``, so a fresh container cannot resolve one otherwise.
  The name carries no ``/``, which leaves the env allowlist on its
  all-providers fallback — required here, since a fusion run legitimately
  needs two vendors' keys at once.
* ``max_turns`` — clawcodex ``--max-turns`` (default 300 here; the CLI's
  own default of 50 is too low for terminal-bench tasks). The
  ``CLAWCODEX_MAX_TURNS`` host env var works as a fallback.
* ``effort`` — clawcodex ``--effort`` (low|medium|high|xhigh|max). How it
  reaches the wire depends on the provider family, and the two behave
  differently:

  - **Anthropic wire** — sent as ``output_config.effort``, and gated on
    clawcodex's effort allowlist (Opus 5, Opus 4.6/4.8, Sonnet 4.6,
    Fable 5). A model missing from that allowlist drops the flag SILENTLY,
    so a new Anthropic model needs a clawcodex build that registers it
    (see ``source`` below) before an effort number means anything.
    ``xhigh`` is model-dependent (opus-5/opus-4-8 yes; sonnet-4-6/opus-4-6
    no) and degrades to ``high`` where rejected.
  - **OpenAI-compatible wire** (openrouter, deepseek, zai, …) — sent as
    the top-level ``reasoning_effort`` body field, with no model allowlist
    and no ``xhigh`` clamp; the level passes through verbatim. This is the
    path ``openrouter/openai/gpt-5.6-luna`` takes, and OpenRouter accepts
    ``max`` for that model.
  - **First-party ``--provider openai``** — reasoning models go over the
    Responses API, which tops out at ``xhigh``: ``max`` is degraded to
    ``xhigh`` rather than sent (the API rejects ``max`` outright with
    "Supported values are: 'none', 'low', 'medium', 'high', and 'xhigh'").
    So ``--ak effort=max`` against ``openai/gpt-5.6-luna`` really runs at
    ``xhigh``, while the same nominal setting on OpenRouter sends ``max``.
    Non-reasoning models (gpt-4o, …) stay on Chat Completions and have the
    effort field STRIPPED, since they reject it as an unknown argument.
  - **ChatGPT subscription** (``subscription=true`` with
    ``--model openai/…``) clamps ``xhigh`` and ``max`` down to ``high``,
    because that backend advertises only low/medium/high. So the same
    ``effort=max`` runs at three different levels depending on route:
    ``max`` on OpenRouter, ``xhigh`` on an OpenAI key, ``high`` on a
    ChatGPT plan.

  REQUIRES a clawcodex build from 2026-07-31 or later. Before that, effort
  was emitted ONLY on the Anthropic branch, so ``--effort`` was a silent
  no-op for every OpenAI-compatible provider — an eval run against, say,
  ``openrouter/openai/gpt-5.6-luna`` would report ``effort=max`` in its
  config and send nothing. Pin ``source`` accordingly when benchmarking a
  non-Anthropic model at a specific effort, and confirm the level is really
  on the wire rather than trusting the job config.

  ``CLAWCODEX_EFFORT`` host env var works as a fallback.
* ``version`` — pin a ``clawcodex-cli`` PyPI version (default: latest).
* ``source`` — full pip-installable spec overriding the PyPI package, e.g.
  ``git+https://github.com/agentforce314/clawcodex@main`` to eval unreleased
  code. Mutually exclusive with ``version``.
* ``subscription`` — ``true`` to authenticate the Anthropic provider with a
  Claude Pro/Max subscription instead of an API key. Reads the host's
  ``~/.clawcodex/anthropic-oauth.json`` (created by ``clawcodex login``;
  refreshed here when under 30 min of runway) and injects a
  refresh-token-free copy into each container OUTSIDE the bind-mounted
  ``/logs`` tree — the token never touches the host jobs dir.
  ``ANTHROPIC_API_KEY`` is deliberately not forwarded so clawcodex takes
  the OAuth path (and combining with ``--ae ANTHROPIC_API_KEY`` is
  rejected).

  **Prefer an API key for any scored or submitted run.** The injected copy
  deliberately carries no refresh token, so it cannot self-heal: when the
  credential is rotated — by this adapter refreshing for a later trial, or
  by ANY other process sharing ``anthropic-oauth.json`` (an interactive
  clawcodex session, another job) — the provider revokes the token already
  live inside running containers, and those trials die mid-run with
  ``401 … OAuth access token has been revoked``. It is indistinguishable
  from an agent crash in the results: Harbor records
  ``NonZeroAgentExitCodeError`` and scores 0. Measured on the tb2.1
  2026-07-25 run: 2 of 89 trials lost this way (one only 4.7 minutes in,
  so runway was not the issue — rotation was), against 0 such failures for
  the claude-code adapter in the comparable run. An API key has no
  rotation and no expiry, so a scored run should not be exposed to it.
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
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)

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
    """Host credentials path, mirroring clawcodex's ``credentials_path()``
    chain: CLAWCODEX_OAUTH_FILE > $CLAWCODEX_CONFIG_DIR > ~/.clawcodex."""
    override_path = os.environ.get("CLAWCODEX_OAUTH_FILE")
    if override_path:
        return Path(override_path)
    config_dir = os.environ.get("CLAWCODEX_CONFIG_DIR")
    root = Path(config_dir) if config_dir else Path.home() / ".clawcodex"
    return root / "anthropic-oauth.json"


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


# Refresh when under this much runway. Containers intentionally receive an
# access token WITHOUT a refresh token (see the injection notes), so the
# token must outlive the whole trial. Long problem-solving evaluations can
# legitimately run beyond the benchmark's original 15-60 minute budgets;
# keep two hours available so callers can raise Harbor's agent timeout
# without introducing an unrelated mid-trial authentication failure.
_MIN_TOKEN_RUNWAY_SEC = 2 * 60 * 60


def fresh_subscription_credentials() -> dict[str, Any]:
    """Host-side equivalent of clawcodex's ``get_valid_credentials``.

    Returns the credentials dict, refreshing (and persisting back to the
    host file) when the access token has under ``_MIN_TOKEN_RUNWAY_SEC`` of
    runway, so long-running trials do not inherit a nearly-expired token.
    Raises RuntimeError with a remedial message when no usable credentials
    exist.
    """
    with _oauth_refresh_lock:
        credentials = _load_host_oauth()
        if credentials is None:
            raise RuntimeError(
                f"No Claude subscription credentials at {_oauth_file()} — "
                "run `clawcodex login` on the host first (or set "
                "CLAWCODEX_OAUTH_FILE)."
            )
        if float(credentials.get("expires_at", 0)) > time.time() + _MIN_TOKEN_RUNWAY_SEC:
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

    # Emit an ATIF trajectory.json in populate_context_post_run (below), so
    # Harbor's viewer / leaderboard get a step-by-step trajectory like the
    # built-in claude-code agent produces.
    SUPPORTS_ATIF: bool = True

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
        fusion: str | None = None,
        forward_keys: bool | str = True,
        advisor: str | None = None,
        advisor_effort: str | None = None,
        *args,
        **kwargs,
    ):
        from harbor.utils.env import parse_bool_env_value

        self._subscription = parse_bool_env_value(subscription, name="subscription")
        self._source = source
        # ``advisor`` is ``<provider>:<model>`` — the reviewer model the
        # worker consults through the advisor tool. Same rationale as
        # ``fusion`` for living here rather than in CLI_FLAGS: it is config,
        # not a clawcodex flag (there is no ``--advisor``), and it reaches the
        # container through the seeded global config instead.
        self._advisor = advisor
        self._advisor_effort = advisor_effort
        if self._advisor:
            # Validate BOTH halves, as ``fusion=`` does. A bare colon test
            # lets "anthropic:" and ":claude-opus-5" through, and each seeds
            # a half-configured advisor that is silently inert at run time —
            # the failure mode this adapter keeps producing.
            provider_half, _, model_half = self._advisor.partition(":")
            if not provider_half.strip() or not model_half.strip():
                raise ValueError(
                    "Agent kwarg 'advisor' must be '<provider>:<model>' with "
                    f"both halves non-empty (got {self._advisor!r}) — "
                    "e.g. anthropic:claude-opus-5"
                )
        if self._advisor_effort and self._advisor_effort not in (
            "low", "medium", "high", "xhigh", "max",
        ):
            raise ValueError(
                "Agent kwarg 'advisor_effort' must be one of low|medium|"
                f"high|xhigh|max (got {self._advisor_effort!r})"
            )
        if self._advisor_effort and not self._advisor:
            raise ValueError(
                "Agent kwarg 'advisor_effort' requires 'advisor' — an effort "
                "level with no reviewer configured is silently inert."
            )
        # Explicit constructor kwargs, NOT CLI_FLAGS entries: both are config
        # inputs with no clawcodex flag behind them, and every CLI_FLAGS entry
        # is emitted into the command by ``build_cli_flags()``. Declaring them
        # there would pass ``--fusion ...`` to clawcodex, which rejects it.
        #
        # ``forward_keys`` was previously read from ``_resolved_flags`` while
        # being declared nowhere. ``_resolve_flag_values`` only walks
        # ``CLI_FLAGS``, so the value never arrived and the documented
        # ``--ak forward_keys=false`` opt-out silently did nothing — keys were
        # forwarded regardless. Same silent-drop that made ``fusion=`` a no-op
        # on its first run: an undeclared ``--ak`` key is accepted without
        # complaint and discarded.
        self._fusion = fusion
        self._forward_keys = parse_bool_env_value(forward_keys, name="forward_keys")
        super().__init__(logs_dir, *args, **kwargs)
        if self._source and self._version:
            raise ValueError(
                "Agent kwargs 'source' and 'version' are mutually exclusive"
            )
        # Harbor injects --agent-env vars into every agent exec itself
        # (Trial's scoped exec env), bypassing _build_env — and inside
        # clawcodex an API key outranks OAuth, silently billing the API.
        if self._subscription and "ANTHROPIC_API_KEY" in self._extra_env:
            raise ValueError(
                "subscription=true conflicts with --ae ANTHROPIC_API_KEY=... "
                "(the key would override OAuth inside clawcodex and bill the "
                "API); drop one of the two."
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
                '  echo "Warning: no known package manager; assuming curl'
                f'{" and git" if need_git else ""} available" >&2;'
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

    def _advisor_provider(self) -> str:
        """Provider half of the ``advisor`` kwarg ("" when unset)."""
        if not self._advisor:
            return ""
        return self._advisor.split(":", 1)[0].strip().lower()

    def _advisor_env_vars(self) -> tuple[str, ...]:
        """Env keys the ADVISOR provider needs, beyond the main loop's.

        The advisor makes its own API call to its own provider, so a run
        whose reviewer sits at a different vendor needs that vendor's key
        too. Without this the advisor fires and dies on "Missing
        credentials" — and because a failed consultation degrades quietly
        (the worker carries on and can still solve the task), the run
        LOOKS clean: reward 1.0 with an advisor that never once answered.
        Observed exactly that on the first container smoke.

        Anthropic is excluded under ``subscription``: OAuth must stay the
        only route there, matching the main-loop rule.
        """
        advisor_provider = self._advisor_provider()
        if not advisor_provider:
            return ()
        keys = _PROVIDER_ENV_VARS.get(advisor_provider, _ALL_PROVIDER_ENV_VARS)
        if self._subscription:
            keys = tuple(k for k in keys if k != "ANTHROPIC_API_KEY")
        return keys

    def _build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self._subscription:
            # Subscription mode authenticates via the injected OAuth file;
            # forwarding ANTHROPIC_API_KEY would silently win over it inside
            # clawcodex (API key takes precedence), billing the API instead.
            #
            # That reasoning is specific to ANTHROPIC. When the subscription
            # backs only the advisor, the MAIN loop is a different provider
            # and still needs its own key — suppressing everything left the
            # worker with no credentials at all.
            model_provider = (self._parsed_model_provider or "").lower()
            if model_provider and model_provider != "anthropic":
                forwarded = _PROVIDER_ENV_VARS.get(
                    model_provider, _ALL_PROVIDER_ENV_VARS
                )
                # Never the Anthropic key: OAuth must stay the only route to
                # the subscription, whichever role it is filling.
                forwarded = tuple(k for k in forwarded if k != "ANTHROPIC_API_KEY")
            else:
                forwarded = ()
        else:
            forwarded = _PROVIDER_ENV_VARS.get(
                (self._parsed_model_provider or "").lower(), _ALL_PROVIDER_ENV_VARS
            )
        # The advisor calls its own provider, which may be a different
        # vendor than the worker's. Union, deduped, order-preserving.
        forwarded = tuple(
            dict.fromkeys(forwarded + self._advisor_env_vars())
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
        # The CLI safely defaults experimental Anthropic betas off for
        # external API-key users. Subscription mode uses Claude's first-party
        # endpoint, where ToolSearch/tool_reference is supported; explicitly
        # override the default so deferred schemas actually leave the initial
        # prompt. ``setdefault`` in cli.py preserves this value.
        if self._subscription:
            env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "false"
        return env

    async def _inject_subscription_credentials(
        self, environment: BaseEnvironment
    ) -> None:
        """Write a freshly-refreshed access token into the container.

        The JSON travels via the exec env (never the command string, which
        is logged) and lands 0600 outside the synced /logs tree. The
        container copy carries an EMPTY refresh_token: the host guarantees
        ≥ ``_MIN_TOKEN_RUNWAY_SEC`` of access-token runway per trial, and
        fanning the real refresh token out to N concurrent containers risks
        a rotation invalidating the host's copy (and every sibling's)
        mid-eval. The key must still be present — clawcodex's
        ``load_credentials`` requires it — an empty value only fails at
        refresh time, which the runway guarantee makes unreachable.
        ``rm -f`` + ``set -C`` (noclobber) defeat a symlink pre-planted in
        the 1777 config dir by an earlier task process.
        """
        import asyncio

        provider = (self._parsed_model_provider or "anthropic").lower()
        # Subscription auth is legitimate for EITHER role. The original gate
        # assumed the subscription always backed the main loop, which made
        # the interesting pairing — a cheap API-key worker consulting a
        # premium subscription reviewer, e.g. openai/gpt-5.6-luna with
        # ``--ak advisor=anthropic:claude-opus-5`` — impossible to express.
        if provider != "anthropic" and self._advisor_provider() != "anthropic":
            raise RuntimeError(
                "subscription=true requires anthropic in some role: either an "
                "anthropic/... model or --ak advisor=anthropic:<model> "
                f"(got model provider {provider!r}, advisor "
                f"{self._advisor or '(none)'!r})"
            )
        credentials = dict(await asyncio.to_thread(fresh_subscription_credentials))
        credentials["refresh_token"] = ""
        target = f"{_CONTAINER_CONFIG_DIR}/anthropic-oauth.json"
        await self.exec_as_agent(
            environment,
            command=(
                f"umask 077; rm -f {target}; set -C; "
                f'printf \'%s\' "$CLAWCODEX_OAUTH_JSON" > {target}'
            ),
            env={"CLAWCODEX_OAUTH_JSON": json.dumps(credentials)},
        )

    def _host_env_keys(self) -> dict[str, str]:
        """The host global config's ``env`` block (stored API keys).

        clawcodex resolves keys like ``TAVILY_API_KEY`` via ``get_secret()``,
        which reads this block; a container without it cannot run WebSearch.
        Values travel to the container through the exec ENV, never the command
        string, so they stay out of process listings and Harbor's logs.
        """
        if not self._forward_keys:
            return {}
        path = Path.home() / ".clawcodex" / "config.json"
        try:
            block = json.loads(path.read_text(encoding="utf-8")).get("env")
        except (OSError, ValueError):
            return {}
        if not isinstance(block, dict):
            return {}
        keys = {
            str(k): str(v)
            for k, v in block.items()
            if isinstance(v, (str, int, float)) and str(v).strip()
        }
        if self._subscription:
            # This block is a SECOND route to a credential and it defeated the
            # process-env exclusion. ``get_secret`` reads the process env and
            # THEN this config ``env`` block, so a stored ANTHROPIC_API_KEY is
            # found by ``resolve_api_key``, takes the API-key path, and OAuth
            # never engages — silently billing the API on a run that asked for
            # the subscription. Withholding it from the exec env alone was not
            # enough; both doors have to be shut.
            keys.pop("ANTHROPIC_API_KEY", None)
        return keys

    def _fusion_record(self) -> dict[str, str] | None:
        """The ``fusionModels`` entry for a ``fusion=`` run, or None.

        A fusion model is a text-only base plus a borrowed vision model, and
        it lives in the user's GLOBAL config rather than being addressable as
        ``provider/model`` — so a fresh container cannot resolve one no matter
        what ``--model`` says. Seeding the record is what makes
        ``--model <fusion-name>`` mean anything in there.

        Named after ``--model`` so the two cannot drift: the record's name IS
        the selector clawcodex looks up.

        Note the key forwarding this relies on. ``--model <fusion-name>`` has
        no ``provider/`` prefix, so ``_parsed_model_provider`` is empty and
        the env allowlist falls back to ALL providers — which is required
        here and only here, because a fusion run legitimately needs two
        vendors' keys at once (the base's and the vision model's).
        """
        raw = str(self._fusion or "").strip()
        if not raw:
            return None
        name = (self._parsed_model_name or "").strip()
        if not name:
            raise ValueError(
                "fusion= requires --model <fusion-name>; the seeded record is "
                "named after it so clawcodex can resolve the selector"
            )
        base, sep, vision = raw.partition("+")
        if not sep or not base.strip() or not vision.strip():
            raise ValueError(
                "fusion= must be '<base>+<vision>', each as <provider>:<model> "
                "(e.g. fusion=deepseek:deepseek-v4-flash+openai:gpt-5.6-luna); "
                f"got {raw!r}"
            )
        for part in (base, vision):
            if ":" not in part:
                raise ValueError(
                    f"fusion= selector {part.strip()!r} must be <provider>:<model>"
                )
        return {"name": name, "base": base.strip(), "vision": vision.strip()}

    async def _seed_container_settings(
        self,
        environment: BaseEnvironment,
    ) -> None:
        """Seed session-wide effort in global config.

        clawcodex's ``--effort`` flag governs the MAIN loop only; subagents
        (Agent tool) resolve effort from ``settings.effort``. Seeding the
        container's global config (home-anchored ``~/.clawcodex/config.json``
        — the global-config path deliberately does not follow
        CLAWCODEX_CONFIG_DIR) makes the requested effort session-wide.

        Also forwards the host's stored API keys (the global config's ``env``
        block) so tools that need one work inside the container -- WebSearch
        resolves ``TAVILY_API_KEY`` through ``get_secret()``, which reads that
        block, and without it every WebSearch call fails with "Web search is
        not configured". Opt out with ``--ak forward_keys=false``.

        Behavioural guidance is still NOT seeded here: instructions an eval
        adapter gives one agent and not another do not belong in a comparison
        and would not exist on a run scored by the official tooling. Credentials
        are a capability, not guidance -- the built-in claude-code agent
        likewise runs with its own key material available.
        """
        settings: dict[str, Any] = {}
        effort = self._resolved_flags.get("effort")
        if effort:
            settings["effort"] = effort

        # Advisor (reviewer model). Settings-only by design: clawcodex reads
        # the advisor config from settings, there is no CLI flag, and the
        # /advisor slash command is not reachable on the headless -p path.
        if self._advisor:
            advisor_provider, advisor_model = self._advisor.split(":", 1)
            settings["advisor_enabled"] = True  # master switch, default off
            settings["advisor_provider"] = advisor_provider.strip()
            settings["advisor_model"] = advisor_model.strip()
            # Force client-side dispatch. Server-side is an Anthropic-only
            # beta that additionally requires the MAIN loop to be 1P
            # Anthropic; pinning client mode keeps the advisor behaving
            # identically no matter which worker model a run is comparing.
            settings["advisor_client_mode"] = True
            if self._advisor_effort:
                settings["advisor_effort"] = self._advisor_effort

        config: dict[str, Any] = {}
        if settings:
            config["settings"] = settings
        env_block = self._host_env_keys()
        if env_block:
            config["env"] = env_block
        fusion = self._fusion_record()
        if fusion:
            config["fusionModels"] = [fusion]
            # The fusion's BASE provider also becomes the session default.
            # Provider resolution runs BEFORE the fusion name is looked up:
            # with no default configured, clawcodex falls back to anthropic
            # and dies on its missing key without ever reaching the fusion
            # record. Seeding the base provider is also semantically right —
            # the base model is the one that runs the loop.
            config["default_provider"] = fusion["base"].split(":", 1)[0]
        if not config:
            return
        payload = json.dumps(config)
        await self.exec_as_agent(
            environment,
            command=(
                'mkdir -p "$HOME/.clawcodex" && '
                'printf \'%s\' "$CLAWCODEX_SEED_CONFIG" > '
                '"$HOME/.clawcodex/config.json"'
            ),
            env={"CLAWCODEX_SEED_CONFIG": payload},
        )

    @with_prompt_template
    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        # Captured so populate_context_post_run can open a stream-json-only
        # (reconstructed) trajectory with the task instruction as step 1,
        # like the built-in claude-code agent does. ``@with_prompt_template``
        # has already rendered any prompt template into ``instruction``.
        self._captured_instruction = instruction
        if self._subscription:
            await self._inject_subscription_credentials(environment)
        await self._seed_container_settings(environment)

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
        # ALLOWLIST copy-back: /logs/agent is a live bind mount of the host
        # trial dir, so the OAuth token file must NEVER transit it — copy
        # only the debugging artifacts, never the config-dir root. Preserves
        # the clawcodex exit code (pipefail is set by the base _exec).
        copy_back = (
            f"mkdir -p {sessions_sync_dir}; "
            # `projects` is where headless runs write per-project session
            # transcripts (CC-style layout); sessions/transcripts/todos
            # cover the other writers. Never the config-dir root: that's
            # where anthropic-oauth.json and config.json live.
            f"for d in projects sessions transcripts todos; do "
            f'[ -e "{_CONTAINER_CONFIG_DIR}/$d" ] && '
            f'cp -r "{_CONTAINER_CONFIG_DIR}/$d" {sessions_sync_dir}/ '
            f"2>/dev/null; done"
        )
        command = (
            f"{_PATH_EXPORT}; "
            f"{' '.join(parts)} -- {shlex.quote(instruction)} "
            f"2>&1 </dev/null | tee {log_path}; rc=$?; "
            f"{copy_back}; "
            "exit $rc"
        )

        await self.exec_as_agent(environment, command=command, env=self._build_env())

    @override
    # ------------------------------------------------------------------ #
    # Trajectory (ATIF) + token metrics
    # ------------------------------------------------------------------ #

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Backfill token metrics and write an ATIF ``trajectory.json``.

        Runs on the host after Harbor syncs the container ``/logs/agent``
        tree back. Two sources, in preference order:

        * the persisted clawcodex session conversation (rich — per-turn
          assistant narration, reasoning, tool calls AND results), synced
          under ``<logs>/sessions/`` when the CLI is new enough to save the
          session in headless mode; and
        * the stream-json log (``clawcodex.txt``) — always present; carries
          every tool call + result and the final answer, and is the sole
          source of authoritative token usage.

        Everything here is best-effort: a trajectory-build failure must
        never fail the trial (mirrors the built-in claude-code agent).
        """
        events = self._parse_stream_events()
        result_event = self._last_result_event(events)
        usage_event = self._last_usage_event(events)

        # Surface an EARLY STOP where a human reading the trial will see it.
        # clawcodex now marks a run the agent loop cut short with a non-success
        # result subtype (``error_during_execution`` when the tool-failure-loop
        # guard trips, ``error_max_turns`` on the turn ceiling) instead of
        # reporting success. Nothing here read ``subtype``, so without this the
        # stream log became honest while the trial output stayed silent — the
        # exact workflow that let 31-second guard kills pass as clean runs
        # scoring zero.
        #
        # Deliberately NOT raised as an exception: Harbor would record an
        # ``exception.txt``, and compare_trajectories.py excludes those trials
        # as "killed by harness", censoring the runs this is meant to reveal.
        # A log line plus a metadata field keeps the trial in the dataset AND
        # visible.
        stop_subtype = (result_event or {}).get("subtype")
        if isinstance(stop_subtype, str) and stop_subtype not in ("", "success"):
            self.logger.warning(
                f"clawcodex ended this trial early: {stop_subtype} "
                f"(the model did not finish the task)"
            )
            # Also machine-readable, so a sweep can count early stops without
            # re-parsing every stream log. Lands in the trial's result.json.
            context.metadata = {
                **(context.metadata or {}),
                "clawcodex_stop_subtype": stop_subtype,
            }

        try:
            trajectory, totals = self._build_trajectory(
                events, result_event, usage_event
            )
        except Exception as exc:  # noqa: BLE001 — never fail a trial over this
            self.logger.debug(f"clawcodex trajectory build failed: {exc}")
            trajectory, totals = None, None

        # Leaderboard token/cost columns. Prefer the session's authoritative
        # BILLING totals (input + cache, summed per turn — matches
        # claude-code); fall back to the stream-json usage only when no
        # session cost block was synced. That fallback now carries cumulative
        # cache counters, so the sum below is no longer short by the cached
        # portion — but it is still not COMPLETE: result.usage only ever sees
        # the main loop's assistant messages, so subagent and compaction
        # tokens never reach it. The cost block includes them, which is one
        # reason it stays preferred.
        if totals is not None:
            context.n_input_tokens = totals["prompt"]
            context.n_cache_tokens = totals["cached"]
            context.n_output_tokens = totals["completion"]
            if totals["cost"] is not None:
                context.cost_usd = totals["cost"]
        elif result_event and isinstance(result_event.get("usage"), dict):
            prompt, cached, completion = self._usage_columns(result_event["usage"])
            context.n_input_tokens = prompt
            context.n_cache_tokens = cached
            context.n_output_tokens = completion
        elif usage_event is not None:
            # Killed trial: no copy-back, no result event. The stream log is
            # a live bind mount so its last cumulative usage line survived,
            # and it is the only measurement of what this run spent.
            prompt, cached, completion = self._usage_columns(usage_event["usage"])
            context.n_input_tokens = prompt
            context.n_cache_tokens = cached
            context.n_output_tokens = completion

        if trajectory is None:
            return

        path = self.logs_dir / "trajectory.json"
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(trajectory.to_json_dict(), handle, indent=2, ensure_ascii=False)
            self.logger.debug(f"wrote clawcodex trajectory to {path}")
        except OSError as exc:
            self.logger.debug(f"failed to write trajectory {path}: {exc}")

    def _parse_stream_events(self) -> list[dict[str, Any]]:
        """All JSON objects from the teed stream-json log, in order."""
        try:
            content = (self.logs_dir / "clawcodex.txt").read_text(encoding="utf-8")
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in content.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    @staticmethod
    def _last_result_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
        for event in reversed(events):
            if event.get("type") == "result":
                return event
        return None

    @staticmethod
    def _last_usage_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The most recent incremental ``usage`` event, or None.

        The metrics lane of last resort, for a trial that was KILLED. Harbor
        cancels the exec on timeout, so the container copy-back never runs
        (no session data) and clawcodex never reaches its terminal ``result``
        event (no result usage) — the two sources below. Every killed trial
        therefore reported no tokens and no cost at all: 21 of 46 in one
        terminal-bench job (2026-08-02), and precisely the longest, most
        expensive ones, because those are what time out. Job totals are summed
        from per-trial values, so the headline cost was a floor biased low by
        exactly the trials that cost the most.

        The stream log survives a kill (it is a live bind mount of the host
        trial dir), so the last cumulative usage line in it is a real
        measurement of everything the run had spent up to the kill.
        """
        for event in reversed(events):
            if event.get("type") == "usage" and isinstance(event.get("usage"), dict):
                return event
        return None

    @staticmethod
    def _usage_columns(usage: dict[str, Any]) -> tuple[int, int, int]:
        """``(prompt, cached, completion)`` from a stream-json usage dict.

        ``input_tokens`` is only the NON-cached part of the prompt, so a total
        built from input+output alone silently omits every cached token — the
        bug #786 fixed. One helper so the result-event and usage-event lanes
        cannot drift apart on that arithmetic again.
        """
        cache_read = usage.get("cache_read_input_tokens") or 0
        cache_creation = usage.get("cache_creation_input_tokens") or 0
        prompt = (usage.get("input_tokens") or 0) + cache_read + cache_creation
        return prompt, cache_read, (usage.get("output_tokens") or 0)

    def _final_metrics(
        self,
        result_event: dict[str, Any] | None,
        total_steps: int,
        totals: dict[str, Any] | None,
        usage_event: dict[str, Any] | None = None,
    ) -> FinalMetrics | None:
        """Three lanes, in descending order of authority:

        1. ``totals`` — the session's BILLING totals. Includes subagent and
           compaction tokens, which nothing else sees.
        2. the terminal ``result`` event's usage — main loop only, but
           carries cumulative cache counters so it is not short by the
           cached portion.
        3. the last incremental ``usage`` event — the only one of the three
           that survives a KILLED run, where the copy-back never ran and no
           result event was ever emitted. See ``_last_usage_event``.

        Cost is unavailable in lane 3 (the incremental event carries tokens
        only); harbor prices those itself, and a token count is what the
        killed trials were missing entirely.
        """
        usage = (result_event or {}).get("usage")
        if not isinstance(usage, dict):
            usage = {}
        if totals is not None:
            prompt = totals["prompt"]
            completion = totals["completion"]
            cached = totals["cached"]
            cost = totals["cost"]
        elif result_event:
            prompt, cached, completion = self._usage_columns(usage)
            cost = result_event.get("total_cost_usd")
        elif usage_event:
            prompt, cached, completion = self._usage_columns(usage_event["usage"])
            cost = None
        else:
            return None
        extra: dict[str, Any] = {}
        num_turns = (result_event or {}).get("num_turns")
        if isinstance(num_turns, int):
            extra["num_turns"] = num_turns
        elif usage_event:
            # NOT mapped onto num_turns. The incremental event counts model
            # ROUND TRIPS, and an agent-loop turn can span several of them, so
            # writing it into num_turns would report a confidently wrong
            # number for exactly the killed trials this lane exists to
            # measure. Surfaced under its own name instead; a killed trial
            # legitimately has no turn count.
            messages = usage_event.get("assistant_messages")
            if isinstance(messages, int):
                extra["assistant_messages"] = messages
        duration_ms = (result_event or {}).get("duration_ms")
        if isinstance(duration_ms, (int, float)):
            extra["duration_ms"] = duration_ms
        return FinalMetrics(
            total_prompt_tokens=prompt,
            total_completion_tokens=completion,
            total_cached_tokens=cached,
            total_cost_usd=cost,
            total_steps=total_steps,
            extra=extra or None,
        )

    def _agent_meta(self, events: list[dict[str, Any]]) -> tuple[Agent, str | None]:
        """Build the ATIF ``Agent`` header from the system:init event."""
        init = next(
            (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"),
            {},
        )
        session_id = init.get("session_id")
        model_name = init.get("model") or self._parsed_model_name
        extra: dict[str, Any] = {}
        for key in ("provider", "cwd", "permission_mode"):
            if init.get(key):
                extra[key] = init[key]
        tools = init.get("tools")
        agent = Agent(
            name=self.name(),
            version=self.version() or "unknown",
            model_name=model_name,
            tool_definitions=(
                [{"name": t} for t in tools] if isinstance(tools, list) else None
            ),
            extra=extra or None,
        )
        return agent, session_id

    @staticmethod
    def _billing_totals_from_cost_block(
        cost: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Authoritative BILLING token totals from a session ``cost`` block.

        Historically clawcodex's stream-json ``result.usage`` summed only
        input+output, and ``input_tokens`` is the running sum of NON-cached
        input, so cumulative cache tokens were dropped entirely (only a
        ``last_*`` snapshot survived). For a heavily prompt-cached opus run
        that under-reported total prompt tokens by orders of magnitude —
        observed here as 6 against the real ~412 K, which is why this cost
        block path exists. ``result.usage`` now carries the cumulative cache
        counters, so the fallback is sound; this remains PREFERRED because
        the cost block also supplies the real dollar cost, and because a run
        against an older clawcodex build still needs it.

        The session cost block's ``model_usage`` accumulates the real
        per-turn billing counters (input + cache_read + cache_creation,
        summed across turns), which is exactly the convention the built-in
        claude-code agent reports.
        Returns ``{prompt, completion, cached, cost}`` or ``None``.
        """
        model_usage = cost.get("model_usage")
        if not isinstance(model_usage, dict) or not model_usage:
            return None
        prompt = completion = cached = 0
        for usage in model_usage.values():
            if not isinstance(usage, dict):
                continue
            inp = usage.get("input_tokens") or 0
            cread = usage.get("cache_read_input_tokens") or 0
            ccreate = usage.get("cache_creation_input_tokens") or 0
            prompt += inp + cread + ccreate
            cached += cread
            completion += usage.get("output_tokens") or 0
        # Cost: prefer the billed ``total_cost_usd``; when it's 0 (a
        # subscription run consumes plan allowance, not metered credits, so
        # the billed cost is $0) fall back to ``estimated_cost_usd`` — the
        # always-computed list-price figure — so the leaderboard/trajectory
        # cost column is populated and comparable with the claude-code
        # agent, which reports a list-price cost on subscription runs too.
        billed = cost.get("total_cost_usd")
        estimated = cost.get("estimated_cost_usd")
        if isinstance(billed, (int, float)) and billed > 0:
            cost_usd: float | None = float(billed)
        elif isinstance(estimated, (int, float)) and estimated > 0:
            cost_usd = float(estimated)
        elif isinstance(billed, (int, float)):
            cost_usd = float(billed)
        else:
            cost_usd = None
        return {
            "prompt": prompt,
            "completion": completion,
            "cached": cached,
            "cost": cost_usd,
        }

    def _load_session_data(
        self,
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
        """The richest persisted session synced under the logs dir, as
        ``(conversation.messages, billing_totals)`` where billing_totals is
        ``{prompt, completion, cached, cost}`` from the cost block (or
        ``None``). Messages and billing are selected INDEPENDENTLY (each by
        most messages) so a conversation-bearing session without a cost
        block still contributes its narration."""
        # ``self.logs_dir`` is the parent of ``sessions/`` — one root covers
        # the subtree; a second nested root would just re-parse each file.
        candidates = (
            sorted(self.logs_dir.rglob("*.json")) if self.logs_dir.is_dir() else []
        )
        best_msgs: list[dict[str, Any]] | None = None
        best_msgs_len = -1
        best_totals: dict[str, Any] | None = None
        best_totals_len = -1
        for path in candidates:
            if path.name == "trajectory.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            conv = data.get("conversation")
            msgs = conv.get("messages") if isinstance(conv, dict) else None
            n = len(msgs) if isinstance(msgs, list) else 0
            if isinstance(msgs, list) and n > best_msgs_len:
                best_msgs_len = n
                best_msgs = msgs
            # Billing from the richest COST-bearing session (== the run's
            # real session, not a seed) — independent of message selection.
            cost = data.get("cost")
            if isinstance(cost, dict) and n > best_totals_len:
                best_totals_len = n
                best_totals = self._billing_totals_from_cost_block(cost)
        return best_msgs, best_totals

    def _build_trajectory(
        self,
        events: list[dict[str, Any]],
        result_event: dict[str, Any] | None,
        usage_event: dict[str, Any] | None = None,
    ) -> tuple[Trajectory | None, dict[str, Any] | None]:
        """Returns ``(trajectory, billing_totals)`` — the totals are surfaced
        so the caller can also set the leaderboard token/cost columns."""
        agent, session_id = self._agent_meta(events)
        messages, totals = self._load_session_data()
        notes = None
        if messages:
            steps = self._steps_from_conversation(messages, agent.model_name)
        else:
            steps = self._steps_from_stream(events, agent.model_name)
            # No persisted conversation → the stream-json has no per-turn
            # narration, so agent steps carry only their tool calls. Prepend
            # the task instruction as the opening user step (claude-code's
            # trajectory opens with it) so the trace isn't headless.
            instruction_step = self._instruction_step()
            if instruction_step is not None:
                for s in steps:
                    s.step_id += 1
                steps.insert(0, instruction_step)
            notes = (
                "Reconstructed from the stream-json log; per-step assistant "
                "narration is unavailable (no persisted session conversation)."
            )
        if not steps:
            return None, totals
        trajectory = Trajectory(
            schema_version="ATIF-v1.7",
            session_id=session_id or "unknown",
            agent=agent,
            steps=steps,
            notes=notes,
            final_metrics=self._final_metrics(
                result_event, len(steps), totals, usage_event
            ),
        )
        return trajectory, totals

    def _instruction_step(self) -> Step | None:
        """The opening user step carrying the task instruction, if ``run()``
        captured it. ``step_id`` is fixed up by the caller."""
        instruction = getattr(self, "_captured_instruction", None)
        if not isinstance(instruction, str) or not instruction.strip():
            return None
        return Step(step_id=1, source="user", message=instruction)

    @staticmethod
    def _split_content(content: Any) -> tuple[str, str | None, list[dict[str, Any]], list[dict[str, Any]]]:
        """Split an Anthropic-shape message ``content`` into (text, reasoning,
        tool_use blocks, tool_result blocks)."""
        if isinstance(content, str):
            return content.strip(), None, [], []
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text" and isinstance(block.get("text"), str):
                    text_parts.append(block["text"])
                elif btype in ("thinking", "reasoning") and isinstance(
                    block.get("thinking") or block.get("text"), str
                ):
                    reasoning_parts.append(block.get("thinking") or block.get("text"))
                elif btype == "tool_use":
                    tool_uses.append(block)
                elif btype == "tool_result":
                    tool_results.append(block)
        text = "\n\n".join(p.strip() for p in text_parts if p and p.strip())
        reasoning = "\n\n".join(p.strip() for p in reasoning_parts if p and p.strip())
        return text, (reasoning or None), tool_uses, tool_results

    @staticmethod
    def _metrics_from_usage(usage: Any) -> Metrics | None:
        """Per-step ATIF ``Metrics`` from a persisted turn's ``usage`` dict
        (present since clawcodex persists per-message usage). ``prompt_tokens``
        follows the billing convention (input + cache_read + cache_creation);
        ``cached_tokens`` is cache_read. Returns ``None`` when no usage."""
        if not isinstance(usage, dict) or not usage:
            return None
        inp = usage.get("input_tokens") or 0
        cread = usage.get("cache_read_input_tokens") or 0
        ccreate = usage.get("cache_creation_input_tokens") or 0
        out = usage.get("output_tokens") or 0
        if not any((inp, cread, ccreate, out)):
            return None
        return Metrics(
            prompt_tokens=inp + cread + ccreate,
            completion_tokens=out,
            cached_tokens=cread,
        )

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    def _steps_from_conversation(
        self, messages: list[dict[str, Any]], default_model: str | None
    ) -> list[Step]:
        """Rich path: one ATIF step per assistant turn, with its tool calls;
        tool_result blocks from the following user message attach back to the
        matching call as observations (bundled by ``tool_use_id``)."""
        steps: list[Step] = []
        pending: dict[str, ToolCall] = {}
        pending_step: dict[str, Step] = {}
        for msg in messages:
            role = msg.get("role")
            if msg.get("isMeta"):
                continue
            text, reasoning, tool_uses, tool_results = self._split_content(
                msg.get("content")
            )
            timestamp = msg.get("timestamp")
            if role == "assistant":
                calls: list[ToolCall] = []
                for tu in tool_uses:
                    call_id = tu.get("id") or tu.get("tool_use_id")
                    if not call_id:
                        continue
                    args = tu.get("input")
                    call = ToolCall(
                        tool_call_id=call_id,
                        function_name=tu.get("name") or "",
                        arguments=args if isinstance(args, dict) else {"input": args},
                    )
                    calls.append(call)
                    pending[call_id] = call
                step = Step(
                    step_id=len(steps) + 1,
                    timestamp=timestamp,
                    source="agent",
                    model_name=default_model,
                    message=text,
                    reasoning_content=reasoning,
                    tool_calls=calls or None,
                    metrics=self._metrics_from_usage(msg.get("usage")),
                    llm_call_count=1,
                )
                steps.append(step)
                for c in calls:
                    pending_step[c.tool_call_id] = step
            elif role == "user":
                # tool results attach to the prior call's step; a leading
                # plain-text user message is the instruction.
                for tr in tool_results:
                    call_id = tr.get("tool_use_id")
                    step = pending_step.get(call_id) if call_id else None
                    if step is None:
                        continue
                    content = tr.get("content")
                    obs = ObservationResult(
                        source_call_id=call_id,
                        content=self._stringify(content) if content is not None else None,
                        extra={"is_error": True} if tr.get("is_error") else None,
                    )
                    if step.observation is None:
                        step.observation = Observation(results=[obs])
                    else:
                        step.observation.results.append(obs)
                if text and not tool_results:
                    steps.append(
                        Step(
                            step_id=len(steps) + 1,
                            timestamp=timestamp,
                            source="user",
                            message=text,
                        )
                    )
        return steps

    def _steps_from_stream(
        self, events: list[dict[str, Any]], default_model: str | None
    ) -> list[Step]:
        """Fallback path: build steps from the flat stream-json events — one
        step per tool call (with its result), plus the final answer."""
        steps: list[Step] = []
        step_by_call: dict[str, Step] = {}
        for event in events:
            etype = event.get("type")
            if etype == "tool_use":
                call_id = event.get("tool_use_id")
                if not call_id:
                    continue
                args = event.get("input")
                step = Step(
                    step_id=len(steps) + 1,
                    source="agent",
                    model_name=default_model,
                    message="",
                    tool_calls=[
                        ToolCall(
                            tool_call_id=call_id,
                            function_name=event.get("name") or "",
                            arguments=args if isinstance(args, dict) else {"input": args},
                        )
                    ],
                    llm_call_count=1,
                )
                steps.append(step)
                step_by_call[call_id] = step
            elif etype == "tool_result":
                call_id = event.get("tool_use_id")
                step = step_by_call.get(call_id) if call_id else None
                if step is None:
                    continue
                content = event.get("output")
                obs = ObservationResult(
                    source_call_id=call_id,
                    content=self._stringify(content) if content is not None else None,
                    extra={"is_error": True} if event.get("is_error") else None,
                )
                step.observation = Observation(results=[obs])
            elif etype == "assistant":
                text = event.get("text")
                if isinstance(text, str) and text.strip():
                    steps.append(
                        Step(
                            step_id=len(steps) + 1,
                            source="agent",
                            model_name=default_model,
                            message=text,
                            llm_call_count=1,
                        )
                    )
        return steps
