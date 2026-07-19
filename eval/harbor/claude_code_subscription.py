"""Harbor agent: the LATEST official Claude Code CLI on a Claude subscription.

A thin subclass of Harbor's battle-tested ``claude-code`` agent (which
bootstrap-installs the official CLI inside each task container) that adds
per-trial Claude-subscription token handling, so terminal-bench runs are
directly comparable with the ``clawcodex_agent`` / ``openclaude_agent``
subscription runs:

    PYTHONPATH=eval/harbor harbor run \
        --dataset terminal-bench/terminal-bench-2-1 \
        --agent claude_code_subscription:ClaudeCodeSubscription \
        --model anthropic/claude-opus-4-8 \
        --ak reasoning_effort=high

Before each trial it refreshes the host's ``~/.clawcodex/anthropic-oauth.json``
(same helper + policy as the sibling adapters: every trial STARTS with at
least 30 minutes of access-token runway — comfortably above terminal-bench's
900s agent timeouts, though a single trial longer than ~30 min could still
outlive its token, since the injected token carries no refresh capability;
a static ``export CLAUDE_CODE_OAUTH_TOKEN`` would instead expire mid-way
through a multi-hour job). ``CLAUDE_FORCE_OAUTH`` is set so a stray host
``ANTHROPIC_API_KEY`` can never silently win and bill the API. The CLI's
subprocess-env scrub stays OFF by default (modern claude-code implements it
via bubblewrap and hard-fails without ``bwrap``, which task containers
lack; off matches Harbor's stock claude-code posture and leaderboard runs —
opt in with ``--ak subprocess_env_scrub=true`` on bubblewrap-equipped
images). The token/FORCE_OAUTH values are consumed host-side inside
``ClaudeCode.run()`` and so are set per-trial for freshness.

Everything else — install (latest official CLI via the bootstrap script; pin
with ``--ak version=2.1.205`` to mirror a leaderboard row), ``--effort`` via
``--ak reasoning_effort={low,medium,high,xhigh,max}``, permission bypass,
stream-json logging, ATIF trajectories, token/cost accounting — is inherited
unchanged from Harbor's agent.
"""

import asyncio
from typing import override

from harbor.agents.installed.base import EnvVar
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# Shared host-side subscription helpers (same PYTHONPATH directory).
from clawcodex_agent import fresh_subscription_credentials


class ClaudeCodeSubscription(ClaudeCode):
    """Harbor's claude-code agent + per-trial subscription token refresh."""

    # Subprocess-env scrub control. Modern claude-code (2.1.215, verified
    # LIVE) implements CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1 via bubblewrap
    # sandboxing and HARD-FAILS without bwrap — which terminal-bench task
    # containers lack (and default Docker seccomp typically can't run).
    # Default OFF ("0", matching Harbor's stock claude-code agent and
    # leaderboard runs, so results stay comparable); opt in with
    # --ak subprocess_env_scrub=true on bubblewrap-equipped images.
    # Residual with the scrub off: a prompt-injecting task can `env` the
    # (<=8h, inference-only) access token into its own transcript — the
    # same posture as every stock harbor claude-code subscription run.
    # ENV_VARS is the right channel: the parent merges _resolved_env_vars
    # into the RUN exec env only, so the value never touches the
    # install/bootstrap phase (spraying it there via extra_env crashed the
    # bootstrap's own CLI invocation — observed live).
    ENV_VARS = [
        *ClaudeCode.ENV_VARS,
        EnvVar(
            "subprocess_env_scrub",
            env="CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",
            type="bool",
            default=False,
            bool_true="1",
            bool_false="0",
        ),
    ]

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        credentials = await asyncio.to_thread(fresh_subscription_credentials)
        # _get_env consults extra_env before os.environ, so these win over
        # anything exported on the host. Both are consumed HOST-SIDE inside
        # ClaudeCode.run() (which copies the token into its exec env
        # itself; FORCE_OAUTH makes it drop any API key), so setting them
        # here — after Trial's extra_env snapshot — still works, and gives
        # every trial a freshly-refreshed token.
        self._extra_env["CLAUDE_CODE_OAUTH_TOKEN"] = credentials["access_token"]
        self._extra_env["CLAUDE_FORCE_OAUTH"] = "1"
        await super().run(instruction, environment, context)
