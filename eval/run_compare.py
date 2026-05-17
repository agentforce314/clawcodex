#!/usr/bin/env python3
"""Driver: run SWE-bench against clawcodex AND openclaude, then diff the results.

This is the single entry point for parity comparisons. It composes the
existing primitives (already documented in ``SWE-bench-dev/clawcodex_test.md``):

1. ``prepare``  : build openclaude if needed, build the SWE-bench text dataset.
2. ``run``      : for each agent, start its API server, generate predictions, run
                  the Docker harness, then write a side-by-side comparison.
3. ``compare``  : skip predictions/harness — just diff two existing summary jsons.

Defaults assume:
- ``SWE-bench-dev/`` is cloned next to ``clawcodex/`` (override with ``SWEBENCH_REPO``)
- ``openclaude/`` is cloned next to ``clawcodex/`` (override with ``OPENCLAUDE_REPO``)
- The SWE-bench venv has been set up per ``clawcodex_test.md`` §2.1
  (override the interpreter with ``SWEBENCH_PYTHON``)
- Docker is running and clawcodex/openclaude are configured for the chosen model
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

EVAL_DIR = Path(__file__).resolve().parent
CLAWCODEX_ROOT = EVAL_DIR.parent

DEFAULT_SWEBENCH_REPO = CLAWCODEX_ROOT / "SWE-bench-dev"
DEFAULT_OPENCLAUDE_REPO = CLAWCODEX_ROOT / "openclaude"
DEFAULT_CLAWCODEX_REPO = CLAWCODEX_ROOT

DEFAULT_DATASET_NAME = "SWE-bench/SWE-bench_Lite"
DEFAULT_DATASET_LOCAL = (
    "datasets/SWE-bench__SWE-bench_Lite__style-3__fs-oracle"
)
DEFAULT_PROMPT_STYLE = "style-3"
DEFAULT_FILE_SOURCE = "oracle"
DEFAULT_SPLIT = "test"

# A handful of cheap, well-known instances for smoke runs.
DEFAULT_SMOKE_INSTANCES: tuple[str, ...] = (
    "astropy__astropy-12907",
)

# Where each agent's API server listens during a run.
AGENT_PORTS: dict[str, int] = {
    "clawcodex": 8000,
    "openclaude": 8001,
}


@dataclass(frozen=True)
class ProviderPreset:
    """Bundle of provider/model/base-url defaults applied to both agents.

    A preset selects:
      - ``model``                : the backing model name passed to both agents
      - ``clawcodex_provider``   : value for ``--provider`` on the clawcodex CLI
                                   (must already exist in ``~/.clawcodex/config.json``)
      - ``openclaude_provider``  : provider hint for the openclaude wrapper
                                   ("openai" routes through CLAUDE_CODE_USE_OPENAI=1)
      - ``openclaude_base_url``  : OPENAI_BASE_URL override used when the
                                   provider speaks the OpenAI-compatible API
                                   (e.g. DeepSeek, GLM). Empty string means
                                   "leave whatever is in the environment alone".

    Individual ``--model`` / ``--clawcodex-provider`` / etc. flags override
    per-field on top of the preset.
    """

    name: str
    model: str
    clawcodex_provider: str
    openclaude_provider: str
    openclaude_base_url: str
    description: str


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        name="openai",
        model="gpt-4o",
        clawcodex_provider="openai",
        openclaude_provider="openai",
        openclaude_base_url="",
        description="OpenAI gpt-4o on both agents.",
    ),
    "deepseek": ProviderPreset(
        name="deepseek",
        model="deepseek-v4-pro",
        clawcodex_provider="deepseek",
        openclaude_provider="openai",
        openclaude_base_url="https://api.deepseek.com/v1",
        description="DeepSeek v4-pro: clawcodex via 'deepseek' provider, openclaude via OpenAI-compatible API.",
    ),
    "anthropic": ProviderPreset(
        name="anthropic",
        model="claude-sonnet-4-6",
        clawcodex_provider="anthropic",
        openclaude_provider="anthropic",
        openclaude_base_url="",
        description="Anthropic Claude on both agents (uses native Claude path).",
    ),
    "glm": ProviderPreset(
        name="glm",
        model="zai/glm-5",
        clawcodex_provider="glm",
        openclaude_provider="openai",
        openclaude_base_url="https://open.bigmodel.cn/api/paas/v4",
        description="Zhipu GLM-5: clawcodex via 'glm' provider, openclaude via OpenAI-compatible API.",
    ),
    "gemini": ProviderPreset(
        name="gemini",
        model="gemini-2.5-pro",
        clawcodex_provider="gemini",
        openclaude_provider="gemini",
        openclaude_base_url="",
        description="Google Gemini 2.5 Pro: clawcodex via 'gemini' provider, openclaude via native Gemini API (CLAUDE_CODE_USE_GEMINI=1).",
    ),
}

DEFAULT_PROVIDER_PRESET = "openai"


@dataclass
class AgentSpec:
    """Static config for one agent in a comparison."""

    name: str
    server_module: str  # e.g. "scripts.clawcodex_api_server:app"
    port: int
    extra_payload: dict[str, object] = field(default_factory=dict)
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class RunPaths:
    """All paths for a single comparison run, rooted at ``run_dir``."""

    run_dir: Path
    run_id: str

    @property
    def predictions(self) -> dict[str, Path]:
        return {
            "clawcodex": self.run_dir / "clawcodex_preds.jsonl",
            "openclaude": self.run_dir / "openclaude_preds.jsonl",
        }

    @property
    def server_logs(self) -> dict[str, Path]:
        return {
            "clawcodex": self.run_dir / "clawcodex_server.log",
            "openclaude": self.run_dir / "openclaude_server.log",
        }

    @property
    def harness_logs(self) -> dict[str, Path]:
        return {
            "clawcodex": self.run_dir / "clawcodex_harness.log",
            "openclaude": self.run_dir / "openclaude_harness.log",
        }

    @property
    def comparison(self) -> Path:
        return self.run_dir / "comparison.md"


def _info(msg: str) -> None:
    sys.stdout.write(f"[run_compare] {msg}\n")
    sys.stdout.flush()


def _resolve_swebench_repo(arg: str | None) -> Path:
    candidate = Path(arg or os.environ.get("SWEBENCH_REPO") or DEFAULT_SWEBENCH_REPO)
    candidate = candidate.expanduser().resolve()
    if not (candidate / "swebench" / "harness").is_dir():
        raise SystemExit(
            f"SWE-bench harness not found under {candidate}. "
            f"Set SWEBENCH_REPO or pass --swebench-repo."
        )
    return candidate


def _resolve_openclaude_repo(arg: str | None) -> Path:
    candidate = Path(arg or os.environ.get("OPENCLAUDE_REPO") or DEFAULT_OPENCLAUDE_REPO)
    return candidate.expanduser().resolve()


def _resolve_clawcodex_repo(arg: str | None) -> Path:
    candidate = Path(arg or os.environ.get("CLAWCODEX_REPO") or DEFAULT_CLAWCODEX_REPO)
    return candidate.expanduser().resolve()


def _resolve_python(env_var: str, fallback: str = "python3") -> str:
    """Interpreter for ``pip``/``-m swebench``/``uvicorn`` subprocesses.

    When ``SWEBENCH_PYTHON`` (or the given *env_var*) is unset, we use
    :data:`sys.executable` — the same Python that is running *this* script — so
    a venv-local ``uv pip install -e ./SWE-bench-dev`` is visible to ``prepare``.

    On Windows, ``shutil.which("python3")`` often resolves to the Microsoft Store
    stub under ``WindowsApps``, which does not see your project venv; avoiding
    that shims the common ``.venv/Scripts/python.exe eval/run_compare.py`` flow.
    """
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit
    exe = sys.executable
    if exe and Path(exe).exists():
        return exe
    for name in (fallback, "python"):
        found = shutil.which(name)
        if found and "windowsapps" not in found.lower():
            return found
    raise SystemExit(
        f"Could not locate a Python interpreter ({env_var} or '{fallback}'). "
        f"Run with your project venv, e.g. `.venv/Scripts/python.exe eval/run_compare.py ...`, "
        f"or set {env_var} to that interpreter."
    )


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.status, resp.read()


def _wait_for_health(url: str, *, timeout: float, interval: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    last_err: str = ""
    while time.monotonic() < deadline:
        try:
            status, body = _http_get(url, timeout=2.0)
            if 200 <= status < 300:
                _info(f"  healthy: {url} -> {body[:120]!r}")
                return
            last_err = f"HTTP {status}"
        except urllib.error.URLError as exc:
            last_err = str(exc.reason)
        except Exception as exc:  # noqa: BLE001
            last_err = repr(exc)
        time.sleep(interval)
    raise RuntimeError(f"Server at {url} did not become healthy within {timeout:.0f}s: {last_err}")


@contextlib.contextmanager
def _spawn_server(
    *,
    swebench_repo: Path,
    swebench_python: str,
    server_module: str,
    port: int,
    log_path: Path,
    env: dict[str, str],
) -> Iterable[subprocess.Popen[bytes]]:
    """Boot a uvicorn server in a subprocess and tear it down on exit.

    ``server_module`` is e.g. ``scripts.clawcodex_api_server:app`` — passed
    straight through to uvicorn. ``cwd`` is ``swebench_repo`` so the
    ``scripts`` package import resolves.
    """
    cmd = [
        swebench_python,
        "-m",
        "uvicorn",
        server_module,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("wb")
    _info(f"  starting: {' '.join(cmd)}  (cwd={swebench_repo})")
    # On Windows, place uvicorn in its own process group so CTRL_BREAK_EVENT
    # only reaches uvicorn, not the run_compare process and any sibling harness
    # subprocess we spawn next. Without this, the signal leaks to the whole
    # console group and aborts the next harness run with an empty log.
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(swebench_repo),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
        **popen_kwargs,
    )
    try:
        try:
            _wait_for_health(f"http://127.0.0.1:{port}/health", timeout=30.0)
        except RuntimeError:
            # /health may not exist on older clawcodex_api_server.py — accept
            # any TCP-level liveness instead.
            _info("  /health probe failed; falling back to socket-only liveness check")
            _wait_for_socket("127.0.0.1", port, timeout=30.0)
        yield proc
    finally:
        if proc.poll() is None:
            _info(f"  stopping uvicorn (pid={proc.pid}) on port {port}")
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                proc.kill()
        log_handle.close()


def _wait_for_socket(host: str, port: int, *, timeout: float) -> None:
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.5)
    raise RuntimeError(f"Server at {host}:{port} did not accept connections within {timeout:.0f}s")


def cmd_prepare(args: argparse.Namespace) -> int:
    swebench_repo = _resolve_swebench_repo(args.swebench_repo)
    swebench_python = _resolve_python("SWEBENCH_PYTHON")
    try:
        subprocess.run(
            [swebench_python, "-c", "import swebench"],
            cwd=str(swebench_repo),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"The interpreter {swebench_python!r} cannot import `swebench`.\n\n"
            "Install the package (editable from your SWE-bench-dev checkout), then retry:\n\n"
            f"  {swebench_python} -m pip install -e {swebench_repo} "
            "fastapi uvicorn tiktoken transformers\n\n"
            "Or set SWEBENCH_PYTHON to a Python that already has SWE-bench installed."
        ) from exc

    openclaude_repo = _resolve_openclaude_repo(args.openclaude_repo)
    skip_openclaude_build = args.skip_openclaude_build

    # 1. Build openclaude dist/cli.mjs if missing.
    if not skip_openclaude_build:
        dist_cli = openclaude_repo / "dist" / "cli.mjs"
        if dist_cli.is_file():
            _info(f"openclaude dist already built: {dist_cli}")
        elif not openclaude_repo.is_dir():
            _info(f"openclaude repo not found at {openclaude_repo} — skipping build.")
        else:
            bun = shutil.which("bun")
            if bun is None:
                raise SystemExit(
                    "openclaude needs a build but `bun` was not found on PATH.\n\n"
                    "Pick one:\n"
                    "  1) Install Bun: https://bun.com/docs/installation "
                    "(Windows: PowerShell `irm bun.sh/install.ps1 | iex`)\n"
                    "  2) Skip the openclaude build for now and only build the SWE-bench text dataset:\n"
                    "       python eval/run_compare.py prepare --skip-openclaude-build\n"
                    "     You must build openclaude yourself later (`bun install && bun run build` in "
                    "openclaude/) so dist/cli.mjs exists before `run` with the openclaude agent.\n"
                )
            _info(f"installing openclaude deps in {openclaude_repo}")
            subprocess.run([bun, "install"], cwd=str(openclaude_repo), check=True)  # noqa: S603
            _info("building openclaude (bun run build)")
            subprocess.run([bun, "run", "build"], cwd=str(openclaude_repo), check=True)  # noqa: S603
            if not dist_cli.is_file():
                raise SystemExit(f"openclaude build did not produce {dist_cli}")

    # 2. Build the SWE-bench text dataset (style-3 + oracle) if missing.
    dataset_dir = swebench_repo / args.dataset_local
    if dataset_dir.is_dir():
        _info(f"text dataset already exists: {dataset_dir}")
    else:
        cmd = [
            swebench_python,
            "-m",
            "swebench.inference.make_datasets.create_text_dataset",
            "--dataset_name_or_path",
            args.dataset_name,
            "--splits",
            args.split,
            "--output_dir",
            str(swebench_repo / "datasets"),
            "--prompt_style",
            args.prompt_style,
            "--file_source",
            args.file_source,
        ]
        _info(f"building text dataset: {' '.join(cmd[3:])}")
        subprocess.run(cmd, cwd=str(swebench_repo), check=True)  # noqa: S603
        if not dataset_dir.is_dir():
            raise SystemExit(f"create_text_dataset finished but {dataset_dir} not found.")

    _info("prepare: done")
    return 0


def _resolve_provider_settings(args: argparse.Namespace) -> dict[str, str]:
    """Apply the chosen preset, then layer per-field overrides on top.

    Returns a dict with keys:
      ``model`` / ``clawcodex_provider`` / ``openclaude_provider`` / ``openclaude_base_url``
    """
    preset = PROVIDER_PRESETS[args.provider]
    return {
        "model": args.model or preset.model,
        "clawcodex_provider": (args.clawcodex_provider
                               if args.clawcodex_provider is not None
                               else preset.clawcodex_provider),
        "openclaude_provider": (args.openclaude_provider
                                if args.openclaude_provider is not None
                                else preset.openclaude_provider),
        "openclaude_base_url": (args.openclaude_base_url
                                if args.openclaude_base_url is not None
                                else preset.openclaude_base_url),
    }


def _build_agent_specs(args: argparse.Namespace) -> list[AgentSpec]:
    selected = [s.strip() for s in args.agents.split(",") if s.strip()]
    if not selected:
        raise SystemExit("--agents must list at least one agent")

    settings = _resolve_provider_settings(args)
    _info(
        f"provider preset='{args.provider}' "
        f"model='{settings['model']}' "
        f"clawcodex.provider='{settings['clawcodex_provider']}' "
        f"openclaude.provider='{settings['openclaude_provider']}' "
        f"openclaude.base_url='{settings['openclaude_base_url'] or '(env default)'}'"
    )

    common_payload: dict[str, object] = {
        "model": settings["model"],
        "max_turns": args.max_turns,
        "timeout": args.request_timeout,
        "dangerously_skip_permissions": True,
    }

    specs: list[AgentSpec] = []
    for name in selected:
        if name == "clawcodex":
            payload = dict(common_payload)
            if settings["clawcodex_provider"]:
                payload["provider"] = settings["clawcodex_provider"]
            specs.append(
                AgentSpec(
                    name="clawcodex",
                    server_module="scripts.clawcodex_api_server:app",
                    port=AGENT_PORTS["clawcodex"],
                    extra_payload=payload,
                )
            )
        elif name == "openclaude":
            payload = dict(common_payload)
            # The openclaude wrapper accepts ``provider`` and ``base_url`` and
            # maps them to env vars (CLAUDE_CODE_USE_OPENAI=1, OPENAI_BASE_URL,
            # OPENAI_MODEL) for the openclaude subprocess. ``api_key`` is
            # intentionally NOT passed in the payload — keys travel via the
            # server's environment (e.g. exported ``OPENAI_API_KEY``).
            if settings["openclaude_provider"]:
                payload["provider"] = settings["openclaude_provider"]
            if settings["openclaude_base_url"]:
                payload["base_url"] = settings["openclaude_base_url"]
            specs.append(
                AgentSpec(
                    name="openclaude",
                    server_module="scripts.openclaude_api_server:app",
                    port=AGENT_PORTS["openclaude"],
                    extra_payload=payload,
                )
            )
        else:
            raise SystemExit(f"unknown agent: {name!r} (valid: clawcodex, openclaude)")
    return specs


def _server_env(
    *,
    swebench_repo: Path,
    clawcodex_repo: Path,
    openclaude_repo: Path,
    agent: str,
) -> dict[str, str]:
    env = os.environ.copy()
    # Make `scripts.*` imports resolve from inside the SWE-bench repo.
    pythonpath = [str(swebench_repo)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    # Repo locations the wrappers themselves consult.
    if clawcodex_repo.is_dir():
        env.setdefault("CLAWCODEX_REPO", str(clawcodex_repo))
    # Only point at a source checkout when it is actually built; otherwise the
    # API server should fall through to global `openclaude` on PATH (npm -g).
    if openclaude_repo.is_dir() and (openclaude_repo / "dist" / "cli.mjs").is_file():
        env.setdefault("OPENCLAUDE_REPO", str(openclaude_repo))
    return env


def _run_predictions(
    *,
    spec: AgentSpec,
    swebench_repo: Path,
    swebench_python: str,
    dataset_local: Path,
    split: str,
    prompt_field: str,
    instance_ids: list[str] | None,
    output_path: Path,
    request_timeout: int,
    max_patch_retries: int,
    patch_retry_backoff: float,
    trace_dir: Path | None = None,
    workers: int = 1,
) -> None:
    cmd = [
        swebench_python,
        "scripts/run_custom_api.py",
        "--api_url",
        f"http://127.0.0.1:{spec.port}/generate",
        "--dataset_name_or_path",
        str(dataset_local),
        "--split",
        split,
        "--prompt_field",
        prompt_field,
        "--model_name_or_path",
        f"{spec.name}-local",
        "--output_file",
        str(output_path),
        "--timeout",
        str(request_timeout),
        "--append",
        "--max_patch_retries",
        str(max_patch_retries),
        "--patch_retry_backoff_seconds",
        str(patch_retry_backoff),
        "--extra_payload",
        json.dumps(spec.extra_payload),
        "--workers",
        str(workers),
    ]
    if instance_ids:
        cmd.extend(["--instance_ids", ",".join(instance_ids)])
    if trace_dir is not None:
        cmd.extend(["--trace_dir", str(trace_dir)])

    _info(f"  predictions: {spec.name} → {output_path}")
    subprocess.run(cmd, cwd=str(swebench_repo), check=True)  # noqa: S603


def _run_harness(
    *,
    swebench_repo: Path,
    swebench_python: str,
    dataset_name: str,
    split: str,
    predictions_path: Path,
    instance_ids: list[str] | None,
    run_id: str,
    max_workers: int,
    log_path: Path,
) -> None:
    cmd = [
        swebench_python,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        dataset_name,
        "--split",
        split,
        "--predictions_path",
        str(predictions_path),
        "--max_workers",
        str(max_workers),
        "--run_id",
        run_id,
    ]
    if instance_ids:
        cmd.extend(["--instance_ids", *instance_ids])
    _info(f"  harness: {predictions_path.name} (run_id={run_id})")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("wb") as log_handle:
            subprocess.run(  # noqa: S603
                cmd,
                cwd=str(swebench_repo),
                check=True,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
    except subprocess.CalledProcessError as e:
        _info(
            f"harness subprocess failed (exit {e.returncode}). "
            f"Later agents were not started. Full log: {log_path}"
        )
        raise


def _find_summary(swebench_repo: Path, model_name: str, run_id: str) -> Path:
    """Locate ``<model>.<run_id>.json`` produced by the harness.

    The harness writes summary files to the *current working directory*, which
    in our case is ``swebench_repo``. We also check ``evaluation_results/`` for
    forward-compatibility.
    """
    sanitized = model_name.replace("/", "__")
    candidates = [
        swebench_repo / f"{sanitized}.{run_id}.json",
        swebench_repo / "evaluation_results" / f"{sanitized}.{run_id}.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        f"Could not locate summary report for model={model_name!r} run_id={run_id!r}. "
        f"Searched: {[str(p) for p in candidates]}"
    )


def cmd_run(args: argparse.Namespace) -> int:
    swebench_repo = _resolve_swebench_repo(args.swebench_repo)
    swebench_python = _resolve_python("SWEBENCH_PYTHON")
    openclaude_repo = _resolve_openclaude_repo(args.openclaude_repo)
    clawcodex_repo = _resolve_clawcodex_repo(args.clawcodex_repo)

    dataset_local = swebench_repo / args.dataset_local
    if not dataset_local.is_dir():
        py_hint = _resolve_python("SWEBENCH_PYTHON")
        raise SystemExit(
            "Text dataset not found.\n\n"
            f"  Expected directory: {dataset_local}\n\n"
            "Build it once with:\n\n"
            f"  {py_hint} eval/run_compare.py prepare \\\n"
            f"    --swebench-repo {swebench_repo}\n\n"
            "That Python must have the `swebench` package (editable install from your "
            "SWE-bench-dev checkout). For example:\n\n"
            f"  {py_hint} -m pip install -e {swebench_repo} fastapi uvicorn tiktoken transformers\n\n"
            "Then re-run `run`. If the dataset lives elsewhere, pass "
            "`--dataset-local <relative-path-under-SWE-bench-dev>`."
        )

    instance_ids: list[str] | None = None
    if args.scope == "smoke":
        instance_ids = list(args.smoke_instances or DEFAULT_SMOKE_INSTANCES)
    elif args.scope == "instances":
        instance_ids = [i.strip() for i in args.instance_ids.split(",") if i.strip()]
        if not instance_ids:
            raise SystemExit("--scope=instances requires --instance-ids")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id_base = args.run_id or f"compare-{timestamp}"
    run_dir = (EVAL_DIR / "runs" / run_id_base).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = RunPaths(run_dir=run_dir, run_id=run_id_base)

    specs = _build_agent_specs(args)

    # Sequential mode: predictions → harness for each agent in turn.
    summary_paths: dict[str, Path] = {}
    for spec in specs:
        _info(f"== {spec.name} ==")
        env = _server_env(
            swebench_repo=swebench_repo,
            clawcodex_repo=clawcodex_repo,
            openclaude_repo=openclaude_repo,
            agent=spec.name,
        )
        env.update(spec.env_overrides)

        with _spawn_server(
            swebench_repo=swebench_repo,
            swebench_python=swebench_python,
            server_module=spec.server_module,
            port=spec.port,
            log_path=paths.server_logs[spec.name],
            env=env,
        ):
            trace_dir = (
                run_dir / "traces" / spec.name if args.capture_traces else None
            )
            _run_predictions(
                spec=spec,
                swebench_repo=swebench_repo,
                swebench_python=swebench_python,
                dataset_local=dataset_local,
                split=args.split,
                prompt_field=args.prompt_field,
                instance_ids=instance_ids,
                output_path=paths.predictions[spec.name],
                request_timeout=args.request_timeout,
                max_patch_retries=args.max_patch_retries,
                patch_retry_backoff=args.patch_retry_backoff_seconds,
                trace_dir=trace_dir,
                workers=args.predict_workers,
            )

        if args.skip_harness:
            _info(f"  --skip-harness set; not running Docker for {spec.name}.")
            continue

        run_id = f"{run_id_base}-{spec.name}"
        # In cumulative mode, drop the per-batch ``instance_ids`` filter on the
        # harness so it scans every prediction in the file (newly-added + any
        # prior batches). SWE-bench's skip-existing logic still avoids
        # re-running already-evaluated instances; the resulting summary is the
        # union of all batches.
        harness_instance_ids = None if args.cumulative else instance_ids
        _run_harness(
            swebench_repo=swebench_repo,
            swebench_python=swebench_python,
            dataset_name=args.dataset_name,
            split=args.split,
            predictions_path=paths.predictions[spec.name],
            instance_ids=harness_instance_ids,
            run_id=run_id,
            max_workers=args.max_workers,
            log_path=paths.harness_logs[spec.name],
        )
        summary_paths[spec.name] = _find_summary(
            swebench_repo, model_name=f"{spec.name}-local", run_id=run_id
        )

    # Comparison only makes sense if we ran both agents and the harness.
    if len(summary_paths) == 2:
        from compare_results import (  # noqa: PLC0415  (intentional local import)
            load_summary,
            render_markdown,
            render_text,
            write_disagreement_lists,
        )

        names = [spec.name for spec in specs if spec.name in summary_paths]
        left = load_summary(summary_paths[names[0]], names[0])
        right = load_summary(summary_paths[names[1]], names[1])
        print()
        print(render_text(left, right))
        paths.comparison.write_text(render_markdown(left, right), encoding="utf-8")
        write_disagreement_lists(paths.run_dir, left, right)
        _info(f"comparison written: {paths.comparison}")
    else:
        _info("only one agent ran (or --skip-harness was set); skipping comparison.")

    _info(f"run dir: {run_dir}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Standalone comparison of two pre-existing harness summary files."""
    sys.path.insert(0, str(EVAL_DIR))
    from compare_results import main as compare_main  # noqa: PLC0415

    return compare_main(
        [
            "--left",
            str(args.left),
            "--right",
            str(args.right),
            "--left-label",
            args.left_label,
            "--right-label",
            args.right_label,
            *(["--out", str(args.out)] if args.out else []),
        ]
    )


def _add_common_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--swebench-repo",
        default=None,
        help="Path to SWE-bench-dev/ (default: $SWEBENCH_REPO or sibling of clawcodex).",
    )
    parser.add_argument(
        "--openclaude-repo",
        default=None,
        help="Path to openclaude/ (default: $OPENCLAUDE_REPO or sibling of clawcodex).",
    )


def _add_dataset_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dataset-name",
        default=DEFAULT_DATASET_NAME,
        help=f"HF dataset name (default: {DEFAULT_DATASET_NAME}).",
    )
    parser.add_argument(
        "--dataset-local",
        default=DEFAULT_DATASET_LOCAL,
        help=(
            "Local relative path under SWE-bench-dev/ where the text dataset lives "
            f"(default: {DEFAULT_DATASET_LOCAL})."
        ),
    )
    parser.add_argument("--split", default=DEFAULT_SPLIT, help="Dataset split (default: test).")
    parser.add_argument(
        "--prompt-style",
        default=DEFAULT_PROMPT_STYLE,
        help=f"Prompt style for create_text_dataset (default: {DEFAULT_PROMPT_STYLE}).",
    )
    parser.add_argument(
        "--file-source",
        default=DEFAULT_FILE_SOURCE,
        help=f"File source for create_text_dataset (default: {DEFAULT_FILE_SOURCE}).",
    )
    parser.add_argument(
        "--prompt-field",
        default="text",
        help="Field in dataset row used as model prompt (default: text).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_compare",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- prepare ----
    p_prep = sub.add_parser("prepare", help="Build openclaude and the SWE-bench text dataset.")
    _add_common_path_args(p_prep)
    _add_dataset_args(p_prep)
    p_prep.add_argument(
        "--skip-openclaude-build",
        action="store_true",
        help="Don't try to bun install / bun run build inside openclaude.",
    )
    p_prep.set_defaults(func=cmd_prepare)

    # ---- run ----
    p_run = sub.add_parser("run", help="Run predictions + harness for both agents.")
    _add_common_path_args(p_run)
    _add_dataset_args(p_run)
    p_run.add_argument(
        "--clawcodex-repo",
        default=None,
        help="Path to clawcodex/ (default: this repo). Forwarded as CLAWCODEX_REPO.",
    )
    p_run.add_argument(
        "--agents",
        default="clawcodex,openclaude",
        help="Comma-separated list of agents to run (default: both).",
    )
    p_run.add_argument(
        "--scope",
        choices=("smoke", "instances", "all"),
        default="smoke",
        help="Run scope (default: smoke).",
    )
    p_run.add_argument(
        "--smoke-instances",
        nargs="*",
        default=None,
        help=f"Override smoke instance list (default: {DEFAULT_SMOKE_INSTANCES}).",
    )
    p_run.add_argument(
        "--instance-ids",
        default="",
        help="Comma-separated instance ids when --scope=instances.",
    )
    preset_help = "; ".join(
        f"'{name}': {p.description}" for name, p in PROVIDER_PRESETS.items()
    )
    p_run.add_argument(
        "--provider",
        choices=sorted(PROVIDER_PRESETS),
        default=DEFAULT_PROVIDER_PRESET,
        help=(
            f"Provider preset selecting model + per-agent provider config "
            f"(default: {DEFAULT_PROVIDER_PRESET}). Available: {preset_help}"
        ),
    )
    p_run.add_argument(
        "--model",
        default=None,
        help="Override the preset's model name (e.g. --model deepseek-v4-pro).",
    )
    p_run.add_argument(
        "--clawcodex-provider",
        default=None,
        help=(
            "Override the clawcodex provider (must be configured via `clawcodex login`). "
            "Pass empty string to skip --provider entirely and let clawcodex pick the default."
        ),
    )
    p_run.add_argument(
        "--openclaude-provider",
        default=None,
        help="Override the provider hint forwarded to openclaude.",
    )
    p_run.add_argument(
        "--openclaude-base-url",
        default=None,
        help=(
            "Override the OpenAI-compatible base URL the openclaude wrapper exports "
            "as OPENAI_BASE_URL (e.g. https://api.deepseek.com/v1)."
        ),
    )
    p_run.add_argument("--max-turns", type=int, default=30, help="Per-instance turn cap.")
    p_run.add_argument(
        "--request-timeout",
        type=int,
        default=1800,
        help="HTTP timeout per instance (default: 1800s).",
    )
    p_run.add_argument(
        "--max-patch-retries",
        type=int,
        default=2,
        help="run_custom_api.py: invalid-patch retry count.",
    )
    p_run.add_argument(
        "--patch-retry-backoff-seconds",
        type=float,
        default=3.0,
        help="run_custom_api.py: invalid-patch retry backoff seconds.",
    )
    p_run.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Docker harness worker count (default: 1).",
    )
    p_run.add_argument(
        "--run-id",
        default=None,
        help="Override the auto-generated comparison run id.",
    )
    p_run.add_argument(
        "--skip-harness",
        action="store_true",
        help="Generate predictions but skip the (slow) Docker harness step.",
    )
    p_run.add_argument(
        "--capture-traces",
        action="store_true",
        help=(
            "Ask each wrapper to emit a stream-json trace per instance under "
            "<run_dir>/traces/<agent>/<instance_id>.jsonl. Off by default to "
            "keep prediction-only flows lean."
        ),
    )
    p_run.add_argument(
        "--cumulative",
        action="store_true",
        help=(
            "Treat each invocation as an incremental batch against a growing "
            "predictions file. Predictions step still honors --instance-ids "
            "for the batch; the harness step drops the filter so its summary "
            "covers every prediction accumulated so far (skip-existing prevents "
            "re-running previously-evaluated instances)."
        ),
    )
    p_run.add_argument(
        "--predict-workers",
        type=int,
        default=1,
        help=(
            "Concurrent in-flight predictions per agent (default 1 = sequential). "
            "3-5 typically gives 3-5x speedup if the model API allows. Each "
            "worker spawns one wrapper subprocess at a time."
        ),
    )
    p_run.set_defaults(func=cmd_run)

    # ---- compare ----
    p_cmp = sub.add_parser("compare", help="Diff two existing harness summary jsons.")
    p_cmp.add_argument("--left", required=True, type=Path)
    p_cmp.add_argument("--right", required=True, type=Path)
    p_cmp.add_argument("--left-label", default="left")
    p_cmp.add_argument("--right-label", default="right")
    p_cmp.add_argument("--out", default=None, type=Path)
    p_cmp.set_defaults(func=cmd_compare)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.path.insert(0, str(EVAL_DIR))
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
