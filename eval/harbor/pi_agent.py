"""Harbor installed-agent adapter for pi (``@earendil-works/pi-coding-agent``).

pi is the harness DeepSeek documents for its V4 models
(api-docs.deepseek.com/quick_start/agent_integrations/pi_mono). This adapter
runs it through the SAME terminal-bench harness as ``clawcodex_agent.py`` and
``openclaude_agent.py`` so the three are directly comparable::

    PYTHONPATH=eval/harbor harbor run \\
        --dataset terminal-bench/terminal-bench-2-1 \\
        --agent pi_agent:Pi \\
        --model deepseek/deepseek-v4-flash \\
        --jobs-dir eval/harbor/jobs

pi is installed from npm inside each container (Node >= 22, bootstrapped from
the official tarball when the image's node is missing or too old).

Tools
-----
pi ships exactly four built-in tools (read, bash, edit, write). Terminal-bench
2.1 needs two capabilities beyond those, and DeepSeek V4 is text-only, so the
adapter uploads ``pi_assets/tb-tools.ts`` and loads it with ``-e``. It
registers ``vision_analyze`` and ``websearch`` with the same argument shapes as
clawcodex's equivalents. This does NOT equalise the tool surfaces — pi runs 6
tools against clawcodex's much larger registry — it only removes the two
capability gaps that would make some tasks impossible rather than merely
harder. The remaining difference is a property of the harnesses and part of
what the benchmark measures. Pass ``--ak tools=off`` to measure stock pi with
its four built-ins only.

The prompt goes in over **stdin**, not as an argv message. pi has no ``--``
separator, and a bare argument starting with ``@`` is read as a file
reference (``cli/args.ts``: ``arg.startsWith("@") -> fileArgs``), so a task
instruction beginning with ``@`` or ``-`` would be silently misparsed.
``buildInitialMessage`` concatenates piped stdin into the first user message,
which is hazard-free for arbitrary text.

Wire facts, probed against pi 0.84.1 on 2026-08-11 (see ``RUN_PI_TB21.md``)
------------------------------------------------------------------------
* DeepSeek thinking is controlled by ``--thinking``. pi's catalogue maps
  minimal/low/medium/high -> ``reasoning_effort: "high"`` and xhigh/max ->
  ``"max"``; ``off`` sends ``thinking: {"type": "disabled"}`` and no effort.
  There is no way to request DeepSeek's ``low`` level through pi.
* Published pi 0.84.1 sends **``max_completion_tokens``** to DeepSeek, because
  ``isDeepSeek`` is missing from the ``useMaxTokens`` chain in that build (the
  git tree has since fixed it). DeepSeek **honours ``max_tokens`` and silently
  ignores ``max_completion_tokens``** — verified directly against the API:
  ``max_tokens: 16`` truncates at 16 tokens (``finish_reason="length"``) while
  ``max_completion_tokens: 16`` returns 456 tokens and stops normally. So
  stock pi runs with **no effective output cap** on DeepSeek and inherits the
  server default (131,072). That is the reasoning-runaway condition measured
  on this benchmark with ``deepseek-v4-flash``: single requests that stream
  100% ``reasoning_content``, emit no content and no tool call, and burn a
  whole task budget at the observed 90-145 tok/s.

  ``--ak output_cap=N`` writes a ``models.json`` override that pins
  ``compat.maxTokensField: "max_tokens"`` and ``maxTokens: N``, giving pi a
  cap DeepSeek actually honours. It is **off by default**: the headline run
  should measure pi as a user following DeepSeek's own docs would get it.

Agent kwargs (``--ak key=value``)
--------------------------------
* ``thinking`` — pi ``--thinking`` (off|minimal|low|medium|high|xhigh|max).
  Unset means pi's own default (medium, i.e. ``reasoning_effort: "high"``).
* ``pi_version`` — npm version to install (default below); ``latest`` allowed.
* ``output_cap`` — see above; ``0`` (default) leaves pi stock.
* ``tools`` — ``on`` (default) loads the vision/websearch extension, ``off``
  runs stock pi with its four built-ins.
* ``vision_model`` — model for ``vision_analyze`` (default
  ``gpt-5.6-luna``, matching the clawcodex TB2.1 baseline).
* ``extension`` — host path to the extension (default: ``pi_assets/tb-tools.ts``
  next to this file).
* ``trust_project`` — pass pi ``--approve`` instead of ``--no-approve``
  (default **false**). AGENTS.md / CLAUDE.md load either way; what trust
  actually gates is `.pi/settings.json`, `.pi/extensions`, `.pi/skills`,
  `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md` and `.agents/skills`
  (``core/trust-manager.ts``). Trusting those lets task content replace the
  system prompt or run its own extension code, which would silently change
  what is being benchmarked, so it is opt-in.

Credential note
---------------
Provider keys are injected as environment variables, and pi's bash tool
inherits the parent environment, so a task that runs ``env`` can read them and
the result is serialized into ``pi.jsonl`` and the session JSONL — both under
the host-synced trial directory. pi has no equivalent of Claude Code's
``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB``, so use keys scoped to benchmarking.
"""

import json
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import override

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    CliFlag,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

_CONTAINER_DIR = "/installed-agent/pi"
_CONTAINER_EXTENSION = f"{_CONTAINER_DIR}/tb-tools.ts"
_CONTAINER_NODE_DIR = "/installed-agent/node22"
_NODE_VERSION = "22.20.0"

#: Pinned so a mid-sweep npm publish can't split a benchmark run across two
#: harness versions. Bump deliberately, and re-probe the wire facts above.
_DEFAULT_PI_VERSION = "0.84.1"
_DEFAULT_VISION_MODEL = "gpt-5.6-luna"

_PATH_EXPORT = f'export PATH="{_CONTAINER_NODE_DIR}/bin:$HOME/.local/bin:$PATH"'

#: Credentials forwarded per model provider, mirroring clawcodex_agent's table.
_PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "zai": ("ZAI_API_KEY", "Z_AI_API_KEY"),
    "moonshot": ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

_VALID_THINKING = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]


def _default_extension() -> Path:
    return Path(__file__).resolve().parent / "pi_assets" / "tb-tools.ts"


class Pi(BaseInstalledAgent):
    """Run pi inside a Harbor task container."""

    CLI_FLAGS = [
        CliFlag(
            "thinking",
            cli="--thinking",
            type="enum",
            choices=_VALID_THINKING,
            env_fallback="PI_THINKING",
        ),
    ]

    def __init__(
        self,
        logs_dir: Path,
        pi_version: str = _DEFAULT_PI_VERSION,
        output_cap: int | str = 0,
        tools: str = "on",
        vision_model: str = _DEFAULT_VISION_MODEL,
        extension: str | None = None,
        trust_project: bool | str = False,
        *args,
        **kwargs,
    ):
        from harbor.utils.env import parse_bool_env_value

        self._pi_version = str(pi_version).strip() or _DEFAULT_PI_VERSION
        try:
            self._output_cap = int(output_cap)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"output_cap must be an integer, got {output_cap!r}") from exc
        if self._output_cap < 0:
            raise ValueError("output_cap must be >= 0 (0 disables the override)")

        tools_value = str(tools).strip().lower()
        if tools_value not in ("on", "off"):
            raise ValueError(f"tools must be 'on' or 'off', got {tools!r}")
        self._tools_enabled = tools_value == "on"

        self._vision_model = str(vision_model).strip() or _DEFAULT_VISION_MODEL
        self._extension = Path(extension) if extension else _default_extension()
        self._trust_project = parse_bool_env_value(trust_project, name="trust_project")

        super().__init__(logs_dir, *args, **kwargs)

        if self._tools_enabled and not self._extension.is_file():
            raise ValueError(
                f"pi tool extension not found at {self._extension} — expected "
                "eval/harbor/pi_assets/tb-tools.ts, or pass --ak extension=/path/to.ts "
                "(or --ak tools=off to run stock pi)."
            )

    @staticmethod
    @override
    def name() -> str:
        return "pi"

    @override
    def get_version_command(self) -> str | None:
        return f"{_PATH_EXPORT}; pi --version"

    @override
    def parse_version(self, stdout: str) -> str:
        import re

        match = re.search(r"(\d+\.\d+\.\d+)", stdout.strip())
        return match.group(1) if match else stdout.strip()

    # ------------------------------------------------------------------ #
    # Install
    # ------------------------------------------------------------------ #

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        # curl + CA certificates for the Node tarball and the npm registry.
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
                '  echo "Warning: no known package manager; assuming curl available" >&2;'
                " fi; }"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        # Node >= 22 with npm. Keep a suitable system node; otherwise the
        # official glibc tarball, or distro packages on musl (alpine).
        await self.exec_as_root(
            environment,
            command=(
                "set -eu; "
                "have_node() { command -v node >/dev/null 2>&1 && "
                "command -v npm >/dev/null 2>&1 && "
                "[ \"$(node -p 'process.versions.node.split(\".\")[0]')\" -ge 22 ]; }; "
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

        spec = f"@earendil-works/pi-coding-agent@{self._pi_version}"
        await self.exec_as_root(
            environment,
            command=(
                f"set -eu; {_PATH_EXPORT}; mkdir -p {_CONTAINER_DIR}; "
                f"npm install -g --no-audit --no-fund {shlex.quote(spec)}; "
                "pi --version"
            ),
        )

        if self._tools_enabled:
            await environment.upload_file(self._extension, _CONTAINER_EXTENSION)
            await self.exec_as_root(
                environment, command=f"chmod 644 {_CONTAINER_EXTENSION}"
            )

    # ------------------------------------------------------------------ #
    # Run
    # ------------------------------------------------------------------ #

    def _config_dir(self) -> str:
        # Under the synced agent dir so models.json and session JSONL come
        # back to the host. pi writes no auth file here (provider keys arrive
        # as environment variables) — but see the credential note in the module
        # docstring: the transcript itself can capture a key if a task dumps
        # its environment, and the transcript does land on the host.
        return (EnvironmentPaths.agent_dir / "pi-config").as_posix()

    def _build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}

        provider = (self._parsed_model_provider or "deepseek").lower()
        for key in _PROVIDER_ENV_VARS.get(provider, ("DEEPSEEK_API_KEY",)):
            value = self._get_env(key)
            if value:
                env[key] = value

        if self._tools_enabled:
            # vision_analyze reaches a vision-capable model; websearch reaches
            # Tavily. Absent keys are surfaced by the tools themselves as a
            # named error rather than a missing tool.
            for key in ("OPENAI_API_KEY", "PI_VISION_API_KEY", "TAVILY_API_KEY"):
                value = self._get_env(key)
                if value:
                    env[key] = value
            env["PI_VISION_MODEL"] = self._vision_model

        env["PI_CODING_AGENT_DIR"] = self._config_dir()
        # No update checks, package refreshes, or version pings from inside a
        # task container: they cost wall clock and can fail the run offline.
        env["PI_OFFLINE"] = "1"
        env["PI_SKIP_VERSION_CHECK"] = "1"
        env["NO_COLOR"] = "1"
        return env

    def _models_json(self) -> str | None:
        """A models.json that gives pi an output cap DeepSeek honours."""
        if self._output_cap <= 0:
            return None
        provider = (self._parsed_model_provider or "deepseek").lower()
        model = self._parsed_model_name or "deepseek-v4-flash"
        # `models` is an array of FULL model definitions; partial patches go
        # in `modelOverrides`, a record keyed by model id (model-config.ts:
        # ProviderConfigSchema). Using `models` here would require restating
        # the whole catalogue entry.
        return json.dumps(
            {
                "providers": {
                    provider: {
                        "modelOverrides": {
                            model: {
                                "maxTokens": self._output_cap,
                                "compat": {"maxTokensField": "max_tokens"},
                            }
                        }
                    }
                }
            },
            indent=2,
        )

    @with_prompt_template
    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        env = self._build_env()
        config_dir = self._config_dir()
        session_dir = (EnvironmentPaths.agent_dir / "pi-sessions").as_posix()
        log_path = (EnvironmentPaths.agent_dir / "pi.jsonl").as_posix()

        parts: list[str] = [
            "pi",
            "--print",
            "--mode",
            "json",
            "--provider",
            shlex.quote((self._parsed_model_provider or "deepseek").lower()),
        ]
        if self._parsed_model_name:
            parts += ["--model", shlex.quote(self._parsed_model_name)]
        if self._tools_enabled:
            parts += ["--extension", _CONTAINER_EXTENSION]
        # Explicit either way: omitting the flag leaves projectTrustOverride
        # undefined, which falls through to pi's saved trust store rather than
        # meaning "untrusted" (cli/args.ts, main.ts).
        parts.append("--approve" if self._trust_project else "--no-approve")
        parts += ["--session-dir", shlex.quote(session_dir)]

        cli_flags = self.build_cli_flags()
        if cli_flags:
            parts.append(cli_flags)

        setup = f"mkdir -p {shlex.quote(config_dir)} {shlex.quote(session_dir)}"
        models_json = self._models_json()
        if models_json:
            setup += (
                f"; printf '%s' {shlex.quote(models_json)} "
                f"> {shlex.quote(config_dir + '/models.json')}"
            )

        # `set -eu` so a failed mkdir or a failed models.json write aborts
        # instead of letting pi run UNCAPPED under a silently-missing override.
        # Harbor's _exec only prepends `set -o pipefail`, not `set -e`.
        #
        # Prompt via stdin: pi has no `--` separator and reads bare `@...`
        # arguments as file references, so argv is unsafe for arbitrary text.
        command = (
            f"set -eu; {_PATH_EXPORT}; {setup}; "
            f"printf '%s' {shlex.quote(instruction)} | "
            f"{' '.join(parts)} 2>&1 | tee {log_path}"
        )

        await self.exec_as_agent(environment, command=command, env=env)
        self._raise_on_agent_error(command)

    # ------------------------------------------------------------------ #
    # Failure detection
    # ------------------------------------------------------------------ #

    def _iter_events(self) -> Iterator[dict]:
        """Yield JSON events from the tee'd stream, one line at a time.

        Streamed rather than slurped: the log carries every bash tool result,
        and trials run concurrently. Non-JSON lines are pi's stderr (merged by
        `2>&1`) and are skipped.
        """
        log_path = self.logs_dir / "pi.jsonl"
        try:
            with log_path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        yield event
        except OSError:
            return

    def _raise_on_agent_error(self, command: str) -> None:
        """Fail the trial when pi ended on a model/API error.

        ``--mode json`` **always exits 0**. ``runPrintMode`` only assigns
        ``exitCode = 1`` inside its ``mode === "text"`` branch
        (``modes/print-mode.ts``), and pi's ``StreamFn`` contract forbids
        throwing for request failures — they arrive as an assistant message
        with ``stopReason: "error"``. So an expired key, a 429 storm or a
        provider 500 would otherwise look exactly like a task the agent
        legitimately failed: reward 0.0, zero exceptions, plausible tokens.
        That turns harbor's ERROR_PATTERNS classification and
        ``--retry-include ApiRateLimitError`` into dead code for this agent.

        Checked here rather than in ``populate_context_post_run`` because only
        an exception raised from ``run`` fails the trial. An unreadable log is
        deliberately NOT an error: the trial may have been killed for reasons
        harbor already records, and inventing a failure would be worse than
        missing one.
        """
        terminal: dict | None = None
        for event in self._iter_events():
            if event.get("type") != "message_end":
                continue
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                terminal = message

        if terminal is None:
            return
        stop_reason = terminal.get("stopReason")
        if stop_reason not in ("error", "aborted"):
            return

        message = str(terminal.get("errorMessage") or f"Request {stop_reason}")
        detail = (
            f"pi ended with stopReason={stop_reason!r} (exit code 0 — "
            f"--mode json never reports failure): {message}\n"
            f"command: {command}"
        )
        for compiled, exception in self._compiled_error_patterns:
            if compiled.search(message):
                raise exception(detail)
        raise NonZeroAgentExitCodeError(detail)

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """Sum usage across pi's JSON event stream.

        pi reports per-assistant-message ``Usage`` (``input``/``output``/
        ``cacheRead``/``cacheWrite``/``cost``); harbor wants run totals.

        Two event families carry usage and BOTH are counted:

        * ``message_end`` for assistant messages. Filtering on the role is
          load-bearing — pi also emits ``message_end`` for user prompts,
          injected steering messages and tool results. ``message_update`` is
          deliberately excluded: it repeats the same message while it streams,
          so summing it would multiply every turn.
        * ``compaction_end`` (``result.usage``). Compaction and branch
          summarization issue their own LLM calls and report usage there, never
          as a ``message_end``. Missing them undercounts by an amount that
          GROWS with task length — exactly the long tasks where the number
          matters — and would flatter pi against clawcodex, whose adapter takes
          a run-level total that already includes its compaction.
        """
        input_tokens = 0
        output_tokens = 0
        cache_read = 0
        cache_write = 0
        cost_usd = 0.0
        seen_usage = False

        def absorb(usage: object) -> bool:
            nonlocal input_tokens, output_tokens, cache_read, cache_write, cost_usd
            if not isinstance(usage, dict):
                return False
            input_tokens += int(usage.get("input") or 0)
            output_tokens += int(usage.get("output") or 0)
            cache_read += int(usage.get("cacheRead") or 0)
            cache_write += int(usage.get("cacheWrite") or 0)
            cost = usage.get("cost")
            if isinstance(cost, dict):
                total = cost.get("total")
                if isinstance(total, (int, float)):
                    cost_usd += float(total)
            return True

        for event in self._iter_events():
            event_type = event.get("type")
            if event_type == "message_end":
                message = event.get("message")
                if isinstance(message, dict) and message.get("role") == "assistant":
                    seen_usage |= absorb(message.get("usage"))
            elif event_type == "compaction_end":
                result = event.get("result")
                if isinstance(result, dict):
                    seen_usage |= absorb(result.get("usage"))

        if not seen_usage:
            return

        # Match clawcodex_agent / openclaude_agent: n_input_tokens is the FULL
        # prompt side. pi's `input` already excludes both cache counters, so
        # they are added back; the cached-read part is also reported on its own.
        context.n_input_tokens = input_tokens + cache_read + cache_write
        context.n_cache_tokens = cache_read
        context.n_output_tokens = output_tokens
        if cost_usd > 0:
            context.cost_usd = cost_usd
