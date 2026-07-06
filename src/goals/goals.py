"""Session goals — the /goal completion-condition loop (Ralph loop).

Port of Claude Code's ``/goal`` command (docs/en/goal) with
``reference_projects/hermes-agent/hermes_cli/goals.py`` as the engineering
donor. A goal is a free-form completion condition that stays active across
turns. After each turn completes, a small side-model call ("the evaluator")
judges whether the condition is satisfied by what the agent has surfaced in
the conversation. If not, the driver feeds a continuation prompt back into
the same session and keeps working until the goal is achieved, the turn
budget is exhausted, the user pauses/clears it, or real user input preempts
the loop.

Design notes / invariants (kept from the donor):

- The continuation prompt is a NORMAL user message enqueued through the
  driver's existing queue. No system-prompt mutation, no toolset swap —
  prompt caching stays intact.
- Judge failures are fail-OPEN: ``continue``. A broken judge must not wedge
  progress; the turn budget is the backstop.
- Real user input preempts the continuation prompt (the driver checks its
  queue before enqueueing); the judge re-runs after that turn anyway.
- This module has zero dependency on the agent-server or headless runner —
  both wire the same :class:`GoalManager` in and own persistence (the
  agent-server session file) and event emission.

Claude Code fidelity notes:

- ``/goal`` bare shows status (condition, running duration, turns evaluated,
  token spend, the evaluator's most recent reason); after achievement the
  status shows the achieved record (docs/en/goal §Check status).
- A "no" verdict's reason is included in the continuation prompt as guidance
  for the next turn (§How evaluation works).
- The condition is capped at 4,000 characters (§Write an effective condition).
- ``clear`` aliases: stop/off/reset/none/cancel (CC) + done (donor).
- Deviation: CC ships without a turn budget; this port keeps the donor's
  budget (default 20, ``goal_max_turns`` setting) as a runaway backstop —
  hitting it pauses the goal with a ``/goal resume`` hint instead of
  spending unbounded tokens.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Constants & defaults
# ──────────────────────────────────────────────────────────────────────

#: Turn budget backstop (donor default). CC ships unbounded; the budget
#: pauses (never clears) so ``/goal resume`` continues with a fresh budget.
DEFAULT_GOAL_MAX_TURNS = 20

#: Claude Code's documented condition cap (docs/en/goal).
GOAL_CONDITION_MAX_CHARS = 4000

#: Judge output budget. The judge returns a one-line JSON verdict, but
#: reasoning models (deepseek-v4, qwq, …) burn tokens on hidden reasoning
#: before emitting the visible JSON — tight caps truncate the JSON and trip
#: the parse-failure auto-pause (donor: goals.py DEFAULT_JUDGE_MAX_TOKENS).
DEFAULT_JUDGE_MAX_TOKENS = 4096

#: Cap on the conversation evidence sent to the judge.
JUDGE_EVIDENCE_MAX_CHARS = 4000

#: After this many consecutive judge *parse* failures (empty output /
#: non-JSON), the loop auto-pauses. API / transport errors do NOT count —
#: those are transient and fail open. Guards against small models that
#: cannot follow the strict JSON reply contract.
MAX_CONSECUTIVE_PARSE_FAILURES = 3

#: Hard bound on one evaluator call (donor: DEFAULT_JUDGE_TIMEOUT). A hung
#: judge must not wedge the driver — on timeout the loop PARKS (no
#: continuation) with the goal left active, instead of hermes's plain
#: fail-open-continue, so a dead evaluator can't drive blind turns.
DEFAULT_JUDGE_TIMEOUT_S = 30.0

#: ``/goal <alias>`` forms that clear the goal. CC: clear/stop/off/reset/
#: none/cancel (docs/en/goal §Clear a goal); donor adds done.
GOAL_CLEAR_ALIASES = frozenset(
    {"clear", "stop", "off", "reset", "none", "cancel", "done"}
)


CONTINUATION_PROMPT_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n\n"
    "Evaluator: the goal is not met yet — {reason}\n\n"
    "Continue working toward this goal. Take the next concrete step. "
    "If you believe the goal is complete, state so explicitly — and show "
    "the concrete evidence (command output, file contents, test result) — "
    "then stop. If you are blocked and need input from the user, say so "
    "clearly and stop."
)

# Used when the user has added one or more /subgoal criteria. Surfaced to
# the agent verbatim so it sees what to target on the next turn, and to the
# judge so the verdict considers them too.
CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n\n"
    "Additional criteria the user added mid-loop:\n"
    "{subgoals_block}\n\n"
    "Evaluator: the goal is not met yet — {reason}\n\n"
    "Continue working toward the goal AND all additional criteria. Take "
    "the next concrete step. If you believe the goal and every additional "
    "criterion are complete, state so explicitly — with concrete evidence — "
    "and stop. If you are blocked and need input from the user, say so "
    "clearly and stop."
)


JUDGE_SYSTEM_PROMPT = (
    "You are a strict judge evaluating whether an autonomous agent has "
    "achieved a user's stated goal. The goal is a CONDITION the agent must "
    "MAKE true by working — not a claim to verify. You receive the goal "
    "text and a transcript excerpt of the agent's most recent turn (its "
    "messages and tool results). Decide one of two verdicts.\n\n"
    "DONE — only when one of these holds:\n"
    "- The excerpt shows concrete evidence the goal's condition NOW holds "
    "(command output, file contents, test results — not just a claim), OR\n"
    "- The agent is genuinely blocked by something OUTSIDE its control "
    "(missing credentials, contradictory instructions, a decision only "
    "the user can make) and says so. The condition merely being false "
    "right now is NOT a block — if the agent could make it true by "
    "working (creating a file, fixing code, running a command), the "
    "verdict is CONTINUE, even when the agent itself claims the goal "
    "'cannot be satisfied'.\n\n"
    "CONTINUE — not done, and there is a concrete next step the agent can "
    "take right now. This is the default when in doubt.\n\n"
    "Reply ONLY with a single JSON object on one line. Shapes:\n"
    '{"verdict": "done", "reason": "<one sentence>"}\n'
    '{"verdict": "continue", "reason": "<one sentence>"}\n'
    "The legacy shape {\"done\": <true|false>, \"reason\": \"...\"} is "
    "also accepted (true=done, false=continue)."
)


JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Agent's most recent turn (transcript excerpt):\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Is the goal satisfied — done or continue?"
)

# Used when the user has added /subgoal criteria. The judge must evaluate
# ALL of them being met, not just the original goal.
JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Additional criteria the user added mid-loop (all must also be "
    "satisfied for the goal to be DONE):\n{subgoals_block}\n\n"
    "Agent's most recent turn (transcript excerpt):\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Decision: For each numbered criterion above, find concrete evidence "
    "in the excerpt that the criterion is satisfied. Do not accept generic "
    "phrases like 'all requirements met' — require specific evidence (a "
    "file contents excerpt, an output line, a command result). If ANY "
    "criterion lacks specific evidence, the goal is NOT done — return "
    "CONTINUE.\n\n"
    "Is the goal AND every additional criterion satisfied?"
)


# ──────────────────────────────────────────────────────────────────────
# State
# ──────────────────────────────────────────────────────────────────────


@dataclass
class GoalState:
    """Serializable per-session goal state."""

    goal: str
    status: str = "active"          # active | paused | done | cleared
    turns_used: int = 0
    max_turns: int = DEFAULT_GOAL_MAX_TURNS
    created_at: float = 0.0
    last_turn_at: float = 0.0
    achieved_at: float = 0.0
    last_verdict: Optional[str] = None   # done | continue | skipped
    last_reason: Optional[str] = None
    paused_reason: Optional[str] = None
    consecutive_parse_failures: int = 0
    # User-added criteria appended mid-loop via /subgoal. When non-empty the
    # judge prompt and continuation prompt both include them.
    subgoals: list[str] = field(default_factory=list)
    # Token/cost odometer readings captured when the goal was set, so status
    # can report spend attributable to the goal (CC status shows token
    # spend). The driver supplies the readings (bootstrap cost accumulators).
    baseline_tokens: int = 0
    baseline_cost_usd: float = 0.0
    # Spend recorded at the last evaluation (so an achieved/cleared record
    # can still report totals after the baseline counters move on).
    spent_tokens: int = 0
    spent_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoalState":
        raw_subgoals = data.get("subgoals") or []
        subgoals: list[str] = []
        if isinstance(raw_subgoals, list):
            subgoals = [str(s).strip() for s in raw_subgoals if str(s).strip()]
        return cls(
            goal=str(data.get("goal", "")),
            status=str(data.get("status", "active")),
            turns_used=int(data.get("turns_used", 0) or 0),
            max_turns=int(data.get("max_turns", DEFAULT_GOAL_MAX_TURNS)
                          or DEFAULT_GOAL_MAX_TURNS),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_turn_at=float(data.get("last_turn_at", 0.0) or 0.0),
            achieved_at=float(data.get("achieved_at", 0.0) or 0.0),
            last_verdict=data.get("last_verdict"),
            last_reason=data.get("last_reason"),
            paused_reason=data.get("paused_reason"),
            consecutive_parse_failures=int(
                data.get("consecutive_parse_failures", 0) or 0
            ),
            subgoals=subgoals,
            baseline_tokens=int(data.get("baseline_tokens", 0) or 0),
            baseline_cost_usd=float(data.get("baseline_cost_usd", 0.0) or 0.0),
            spent_tokens=int(data.get("spent_tokens", 0) or 0),
            spent_cost_usd=float(data.get("spent_cost_usd", 0.0) or 0.0),
        )

    def render_subgoals_block(self) -> str:
        if not self.subgoals:
            return ""
        return "\n".join(
            f"- {i}. {text}" for i, text in enumerate(self.subgoals, start=1)
        )


# ──────────────────────────────────────────────────────────────────────
# Judge
# ──────────────────────────────────────────────────────────────────────


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "… [truncated]"


_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _parse_judge_response(raw: str) -> tuple[str, str, bool]:
    """Parse the judge's reply. Fail-open on unusable output.

    Returns ``(verdict, reason, parse_failed)`` where ``verdict`` is
    ``"done"`` or ``"continue"`` and ``parse_failed`` is True when the reply
    couldn't be interpreted as the expected JSON verdict (empty body, prose,
    malformed JSON). Callers use it to auto-pause after N consecutive parse
    failures so a weak judge model doesn't silently burn the budget.

    Accepts both the ``{"verdict": ...}`` shape and the legacy
    ``{"done": <bool>}`` shape (donor contract).
    """
    if not raw:
        return "continue", "judge returned empty response", True

    text = raw.strip()

    # Strip markdown code fences the model may wrap JSON in.
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]

    data: Optional[dict[str, Any]] = None
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001 — fall through to first-object scan
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:  # noqa: BLE001
                data = None

    if not isinstance(data, dict):
        return (
            "continue",
            f"judge reply was not JSON: {_truncate(raw, 200)!r}",
            True,
        )

    reason = str(data.get("reason") or "").strip() or "no reason provided"

    verdict_raw = data.get("verdict")
    if isinstance(verdict_raw, str):
        verdict = verdict_raw.strip().lower()
    else:
        done_val = data.get("done")
        if isinstance(done_val, str):
            done = done_val.strip().lower() in {"true", "yes", "1", "done"}
        else:
            done = bool(done_val)
        verdict = "done" if done else "continue"

    if verdict not in {"done", "continue"}:
        verdict = "continue"
    return verdict, reason, False


#: The judge callable contract: ``(system, user) -> raw reply text or None``.
JudgeCallable = Callable[[str, str], Optional[str]]


class GoalJudgeTimeout(Exception):
    """The evaluator call exceeded its hard time bound."""


def judge_goal(
    goal: str,
    evidence: str,
    *,
    judge: Optional[JudgeCallable],
    subgoals: Optional[list[str]] = None,
) -> tuple[str, str, bool]:
    """Ask the evaluator whether the goal is satisfied.

    Returns ``(verdict, reason, parse_failed)`` — verdict is ``"done"``,
    ``"continue"``, ``"timeout"``, or ``"skipped"`` (empty goal).
    Deliberately fail-open: a fast judge error returns
    ``("continue", ..., False)`` so a broken judge doesn't wedge progress —
    the turn budget and the parse-failure auto-pause are the backstops. A
    :class:`GoalJudgeTimeout` is the exception: it returns ``"timeout"`` so
    the driver PARKS the loop (goal stays active, no continuation) instead
    of blindly looping on a hung evaluator.
    """
    if not goal.strip():
        return "skipped", "empty goal", False
    if not evidence.strip():
        # No substantive output this turn — almost certainly not done yet.
        return "continue", "empty response (nothing to evaluate)", False
    if judge is None:
        return "continue", "no evaluator configured", False

    clean_subgoals = [s.strip() for s in (subgoals or []) if s and s.strip()]
    current_time = datetime.now(tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )
    if clean_subgoals:
        subgoals_block = "\n".join(
            f"- {i}. {text}" for i, text in enumerate(clean_subgoals, start=1)
        )
        prompt = JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            subgoals_block=_truncate(subgoals_block, 2000),
            response=_truncate(evidence, JUDGE_EVIDENCE_MAX_CHARS),
            current_time=current_time,
        )
    else:
        prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            response=_truncate(evidence, JUDGE_EVIDENCE_MAX_CHARS),
            current_time=current_time,
        )

    try:
        raw = judge(JUDGE_SYSTEM_PROMPT, prompt) or ""
    except GoalJudgeTimeout:
        logger.info("goal judge: timed out — parking the loop")
        return (
            "timeout",
            f"evaluator timed out after {int(DEFAULT_JUDGE_TIMEOUT_S)}s",
            False,
        )
    except Exception as exc:  # noqa: BLE001 — transport errors fail open
        logger.info("goal judge: call failed (%s) — continuing", exc)
        return "continue", f"judge error: {type(exc).__name__}", False

    verdict, reason, parse_failed = _parse_judge_response(raw)
    logger.info(
        "goal judge: verdict=%s reason=%s", verdict, _truncate(reason, 120)
    )
    return verdict, reason, parse_failed


def build_judge_callable(
    provider: Any, *, timeout_s: float = DEFAULT_JUDGE_TIMEOUT_S
) -> JudgeCallable:
    """Wrap a provider into the judge contract, hard-bounded by ``timeout_s``.

    Follows the port's small-fast-model side-call convention
    (``services/tool_use_summary._resolve_summary_model`` /
    ``memdir/find_relevant_memories._resolve_recall_model``): pin
    ``settings.small_fast_model`` only when the session provider is the
    first-party Anthropic provider (CC parity — the evaluator "defaults to
    Haiku"); every other provider falls back to the session model, because
    the shipped small_fast_model default is an Anthropic id that would 400
    elsewhere.

    Uses the provider's synchronous ``chat`` — the call sites (agent-server
    worker thread, headless between-query loop) are sync contexts with no
    running event loop, and the judge is a short blocking side call. The
    call runs on a helper thread joined with ``timeout_s``: a hung HTTP
    call raises :class:`GoalJudgeTimeout` here instead of wedging the
    driver. The leaked helper thread is daemon-pooled and dies with the
    SDK's own HTTP timeout; no ``timeout`` kwarg is forwarded because not
    every provider ``chat`` tolerates unknown per-request kwargs, and a
    TypeError would silently disable judging for the whole goal.
    """

    def _judge(system: str, user: str) -> Optional[str]:
        if provider is None or not hasattr(provider, "chat"):
            return None
        kwargs: dict[str, Any] = {
            "system": system,
            "max_tokens": DEFAULT_JUDGE_MAX_TOKENS,
        }
        model = _resolve_judge_model(provider)
        if model:
            kwargs["model"] = model

        # Plain daemon thread, NOT ThreadPoolExecutor: concurrent.futures
        # atexit-joins its (non-daemon) workers, so a judge thread hung on a
        # dead socket would block interpreter exit. A daemon thread just
        # dies with the process.
        import threading

        box: dict[str, Any] = {}

        def _call() -> None:
            try:
                box["response"] = provider.chat(
                    [{"role": "user", "content": user}], **kwargs
                )
            except BaseException as exc:  # noqa: BLE001 — re-raised on the caller
                box["error"] = exc

        t = threading.Thread(target=_call, name="goal-judge", daemon=True)
        t.start()
        t.join(timeout=timeout_s)
        if t.is_alive():
            raise GoalJudgeTimeout(f"evaluator exceeded {timeout_s:.0f}s")
        if "error" in box:
            raise box["error"]
        response = box.get("response")
        if response is None:
            return None
        return (getattr(response, "content", "") or "").strip() or None

    return _judge


def _resolve_judge_model(provider: Any) -> Optional[str]:
    """The small-fast-model pin for the evaluator side query. Returns None
    to signal session-model fallback. Never raises."""
    try:
        from src.providers.anthropic_provider import AnthropicProvider

        if not isinstance(provider, AnthropicProvider):
            return None
        from src.settings.settings import get_settings

        model = (getattr(get_settings(), "small_fast_model", "") or "").strip()
        return model or None
    except Exception:  # noqa: BLE001 — a settings/import failure must not block judging
        return None


# ──────────────────────────────────────────────────────────────────────
# Evidence collection
# ──────────────────────────────────────────────────────────────────────


def collect_turn_evidence(
    messages: list[Any],
    *,
    limit_chars: int = JUDGE_EVIDENCE_MAX_CHARS,
    max_messages: int = 30,
) -> str:
    """Flatten the tail of the conversation into a judge-readable excerpt.

    CC's evaluator "judges your condition against what Claude has surfaced
    in the conversation" — the proof (a test run, a build exit code) usually
    lives in TOOL RESULTS, not the final assistant sentence. Walk backwards
    from the end until the previous real user prompt (bounded at
    ``max_messages``), collecting assistant text and short tool_result
    excerpts, newest last. The final assistant text is always included even
    when the char budget is tight.

    ``messages`` are conversation entries with ``role``/``content`` attrs or
    keys (str content or content-block lists). Never raises.
    """
    try:
        collected: list[str] = []  # built oldest→newest after the walk
        walked = 0
        for msg in reversed(messages or []):
            if walked >= max_messages:
                break
            walked += 1
            role = getattr(msg, "role", None)
            content = getattr(msg, "content", None)
            if role is None and isinstance(msg, dict):
                role = msg.get("role")
                content = msg.get("content")
            is_meta = bool(getattr(msg, "isMeta", False) or (
                isinstance(msg, dict) and msg.get("isMeta")
            ))

            if role == "user":
                # Tool-result carrier messages are part of the turn; a real
                # user prompt (plain text, non-meta) is the turn boundary.
                parts: list[str] = []
                if isinstance(content, list):
                    has_tool_result = False
                    for block in content:
                        btype = _block_get(block, "type")
                        if btype == "tool_result":
                            has_tool_result = True
                            body = _flatten_block_text(
                                _block_get(block, "content")
                            )
                            if body:
                                parts.append(
                                    f"[tool result] {_truncate(body, 400)}"
                                )
                    if not has_tool_result and not is_meta:
                        break  # multimodal real prompt — turn boundary
                    collected.append("\n".join(parts))
                    continue
                if is_meta:
                    continue
                break  # plain-text real user prompt — turn boundary

            if role == "assistant":
                parts = []
                if isinstance(content, str):
                    if content.strip():
                        parts.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        btype = _block_get(block, "type")
                        if btype == "text":
                            text = str(_block_get(block, "text") or "").strip()
                            if text:
                                parts.append(text)
                        elif btype == "tool_use":
                            name = _block_get(block, "name") or "tool"
                            parts.append(f"[called tool: {name}]")
                if parts:
                    collected.append("\n".join(parts))

        collected.reverse()
        if not collected:
            return ""
        # Budget: keep the newest content — trim from the oldest entries.
        out: list[str] = []
        remaining = limit_chars
        for chunk in reversed(collected):
            if remaining <= 0:
                break
            take = chunk[-remaining:] if len(chunk) > remaining else chunk
            out.append(take)
            remaining -= len(take) + 1
        out.reverse()
        return "\n".join(out).strip()
    except Exception:  # noqa: BLE001 — evidence is best-effort
        logger.debug("goal evidence collection failed", exc_info=True)
        return ""


def _block_get(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _flatten_block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            t = _block_get(b, "text")
            if isinstance(t, str) and t:
                parts.append(t)
        return "\n".join(parts)
    return str(content or "")


# ──────────────────────────────────────────────────────────────────────
# GoalManager — the orchestration surface the drivers talk to
# ──────────────────────────────────────────────────────────────────────


class GoalManager:
    """Per-session goal state + continuation decisions.

    The agent-server and headless runner each hold one per session. The
    manager owns no I/O: persistence is via ``state.to_dict()`` /
    :meth:`restore`, judging via the injected ``judge`` callable, and
    token/cost readings are supplied by the driver at evaluation time.
    """

    def __init__(
        self,
        session_id: str,
        *,
        default_max_turns: int = DEFAULT_GOAL_MAX_TURNS,
        judge: Optional[JudgeCallable] = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.session_id = session_id
        self.default_max_turns = int(default_max_turns or DEFAULT_GOAL_MAX_TURNS)
        self.judge = judge
        self._now = now
        self._state: Optional[GoalState] = None

    # --- introspection ------------------------------------------------

    @property
    def state(self) -> Optional[GoalState]:
        return self._state

    def is_active(self) -> bool:
        return self._state is not None and self._state.status == "active"

    def has_goal(self) -> bool:
        return self._state is not None and self._state.status in {
            "active", "paused",
        }

    def status_text(self) -> str:
        """Multi-line status block for ``/goal`` with no arguments (CC
        §Check status: condition, duration, turns evaluated, token spend,
        most recent reason; or the achieved record)."""
        s = self._state
        if s is None or s.status == "cleared":
            return "No active goal. Set one with /goal <condition>."
        turns = f"{s.turns_used}/{s.max_turns}"
        if s.status == "done":
            dur = _fmt_duration(
                (s.achieved_at or s.last_turn_at or s.created_at) - s.created_at
            )
            lines = [
                f"✓ Goal achieved: {s.goal}",
                f"  Duration: {dur} · Turns evaluated: {turns}"
                f" · Tokens: {_fmt_tokens(s.spent_tokens)}"
                f" · Cost: ${s.spent_cost_usd:.4f}",
            ]
            if s.last_reason:
                lines.append(f"  Evaluator: {s.last_reason}")
            return "\n".join(lines)
        dur = _fmt_duration(self._now() - s.created_at)
        sub = (
            f" · Subgoals: {len(s.subgoals)}" if s.subgoals else ""
        )
        if s.status == "paused":
            head = f"⏸ Goal (paused — {s.paused_reason or 'user-paused'}): {s.goal}"
            hint = "  Use /goal resume to continue, or /goal clear to stop."
        else:
            head = f"◎ Goal (active): {s.goal}"
            hint = ""
        lines = [
            head,
            f"  Running: {dur} · Turns evaluated: {turns}"
            f" · Tokens: {_fmt_tokens(s.spent_tokens)}"
            f" · Cost: ${s.spent_cost_usd:.4f}{sub}",
        ]
        if s.last_reason:
            lines.append(f"  Evaluator: {s.last_reason}")
        if hint:
            lines.append(hint)
        return "\n".join(lines)

    def status_line(self) -> str:
        """One-line status (transcript/status-bar friendly)."""
        s = self._state
        if s is None or s.status == "cleared":
            return "No active goal. Set one with /goal <condition>."
        turns = f"{s.turns_used}/{s.max_turns} turns"
        if s.status == "active":
            return f"◎ Goal (active, {turns}): {s.goal}"
        if s.status == "paused":
            extra = f" — {s.paused_reason}" if s.paused_reason else ""
            return f"⏸ Goal (paused, {turns}{extra}): {s.goal}"
        if s.status == "done":
            return f"✓ Goal achieved ({turns}): {s.goal}"
        return f"Goal ({s.status}, {turns}): {s.goal}"

    # --- mutation -----------------------------------------------------

    def set(
        self,
        goal: str,
        *,
        max_turns: Optional[int] = None,
        baseline_tokens: int = 0,
        baseline_cost_usd: float = 0.0,
    ) -> GoalState:
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal text is empty")
        if len(goal) > GOAL_CONDITION_MAX_CHARS:
            raise ValueError(
                f"goal condition is too long "
                f"({len(goal)} > {GOAL_CONDITION_MAX_CHARS} characters)"
            )
        state = GoalState(
            goal=goal,
            status="active",
            turns_used=0,
            max_turns=int(max_turns) if max_turns else self.default_max_turns,
            created_at=self._now(),
            baseline_tokens=int(baseline_tokens or 0),
            baseline_cost_usd=float(baseline_cost_usd or 0.0),
        )
        self._state = state
        return state

    def clear(self) -> bool:
        """Clear the goal. Returns whether an active/paused goal existed."""
        had = self.has_goal()
        if self._state is not None:
            self._state.status = "cleared"
            self._state = None
        return had

    def pause(self, reason: str = "user-paused") -> Optional[GoalState]:
        if not self._state or self._state.status not in {"active", "paused"}:
            return None
        self._state.status = "paused"
        self._state.paused_reason = reason
        return self._state

    def resume(self, *, reset_budget: bool = True) -> Optional[GoalState]:
        if not self._state or self._state.status not in {"active", "paused"}:
            return None
        self._state.status = "active"
        self._state.paused_reason = None
        self._state.consecutive_parse_failures = 0
        if reset_budget:
            self._state.turns_used = 0
        return self._state

    def mark_done(self, reason: str) -> None:
        if not self._state:
            return
        self._state.status = "done"
        self._state.last_verdict = "done"
        self._state.last_reason = reason
        self._state.achieved_at = self._now()

    # --- /subgoal user controls ---------------------------------------

    def add_subgoal(self, text: str) -> str:
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        text = (text or "").strip()
        if not text:
            raise ValueError("subgoal text is empty")
        self._state.subgoals.append(text)
        return text

    def remove_subgoal(self, index_1based: int) -> str:
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        idx = int(index_1based) - 1
        if idx < 0 or idx >= len(self._state.subgoals):
            raise IndexError(
                f"index out of range (1..{len(self._state.subgoals)})"
            )
        return self._state.subgoals.pop(idx)

    def clear_subgoals(self) -> int:
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        prev = len(self._state.subgoals)
        self._state.subgoals = []
        return prev

    def render_subgoals(self) -> str:
        if self._state is None:
            return "(no active goal)"
        if not self._state.subgoals:
            return "(no subgoals — use /subgoal <text> to add criteria)"
        return self._state.render_subgoals_block()

    # --- persistence bridge (driver-owned store) -----------------------

    def restore(
        self, data: dict[str, Any], *, reset_counters: bool = True
    ) -> Optional[GoalState]:
        """Restore a persisted goal (CC §Resume with an active goal: only
        an ACTIVE goal is restored; turn count, timer, and spend baseline
        reset). Returns the restored state or None."""
        try:
            state = GoalState.from_dict(data)
        except Exception:  # noqa: BLE001 — a corrupt record must not break resume
            logger.debug("goal restore failed", exc_info=True)
            return None
        if state.status != "active" or not state.goal.strip():
            return None
        if reset_counters:
            state.turns_used = 0
            state.created_at = self._now()
            state.last_turn_at = 0.0
            state.consecutive_parse_failures = 0
            state.spent_tokens = 0
            state.spent_cost_usd = 0.0
            # The driver re-baselines tokens/cost after restore.
        self._state = state
        return state

    def rebaseline(self, *, tokens: int, cost_usd: float) -> None:
        if self._state is None:
            return
        self._state.baseline_tokens = int(tokens or 0)
        self._state.baseline_cost_usd = float(cost_usd or 0.0)

    # --- the main entry point called after every turn -----------------

    def evaluate_after_turn(
        self,
        evidence: str,
        *,
        tokens_now: int = 0,
        cost_now_usd: float = 0.0,
    ) -> dict[str, Any]:
        """Run the judge and update state. Returns a decision dict:

        - ``status``: goal status after the update
        - ``should_continue``: bool — the driver should fire another turn
        - ``continuation_prompt``: str or None
        - ``verdict``: done | continue | timeout | skipped | inactive
        - ``reason``: str
        - ``message``: user-visible one-liner to surface

        Single-threaded convenience (headless runner, tests). A
        multi-threaded driver (the agent-server worker) must split the
        phases itself for double-checked locking: read ``state`` under its
        lock, call :func:`judge_goal` OUTSIDE the lock (network call),
        then :meth:`apply_verdict` under the lock again.
        """
        state = self._state
        if state is None or state.status != "active":
            return _INACTIVE_DECISION | {
                "status": state.status if state else None,
            }

        verdict, reason, parse_failed = judge_goal(
            state.goal,
            evidence,
            judge=self.judge,
            subgoals=list(state.subgoals) or None,
        )
        return self.apply_verdict(
            verdict, reason, parse_failed,
            tokens_now=tokens_now, cost_now_usd=cost_now_usd,
            expected_state=state,
        )

    def apply_verdict(
        self,
        verdict: str,
        reason: str,
        parse_failed: bool,
        *,
        tokens_now: int = 0,
        cost_now_usd: float = 0.0,
        expected_state: Optional[GoalState] = None,
    ) -> dict[str, Any]:
        """Apply a judge verdict to the state and return the decision dict.

        ``expected_state`` is the state snapshot the judge ran against: if
        the live state has been replaced or deactivated meanwhile (a
        concurrent ``/goal clear`` / new ``/goal`` set / pause), the stale
        verdict is DISCARDED and an inactive decision returned — the caller
        must never act on a verdict for a goal the user has since changed.
        """
        state = self._state
        if state is None or state.status != "active":
            return _INACTIVE_DECISION | {
                "status": state.status if state else None,
            }
        if expected_state is not None and state is not expected_state:
            return _INACTIVE_DECISION | {
                "status": state.status,
                "reason": "goal changed during evaluation",
            }

        state.turns_used += 1
        state.last_turn_at = self._now()
        if tokens_now:
            state.spent_tokens = max(0, int(tokens_now) - state.baseline_tokens)
        if cost_now_usd:
            state.spent_cost_usd = max(
                0.0, float(cost_now_usd) - state.baseline_cost_usd
            )

        state.last_verdict = verdict
        state.last_reason = reason

        # Track consecutive judge parse failures. Reset on any usable reply,
        # including transport errors (parse_failed=False), so a flaky network
        # doesn't trip the auto-pause meant for bad judge models.
        if parse_failed:
            state.consecutive_parse_failures += 1
        else:
            state.consecutive_parse_failures = 0

        if verdict == "timeout":
            # A hung evaluator PARKS the loop: goal stays active, no
            # continuation — the next completed turn re-evaluates. (A fast
            # transport error fail-opens to continue instead; see judge_goal.)
            return {
                "status": "active",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "timeout",
                "reason": reason,
                "message": (
                    f"⏳ Goal evaluator timed out — the goal stays active; "
                    f"I'll re-evaluate at the end of the next turn. ({reason})"
                ),
            }

        if verdict == "done":
            self.mark_done(reason)
            return {
                "status": "done",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "done",
                "reason": reason,
                "message": f"✓ Goal achieved: {reason}",
            }

        if state.consecutive_parse_failures >= MAX_CONSECUTIVE_PARSE_FAILURES:
            state.status = "paused"
            state.paused_reason = (
                f"evaluator returned unparseable output "
                f"{state.consecutive_parse_failures} turns in a row"
            )
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": verdict,
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — the evaluator model "
                    f"({state.consecutive_parse_failures} turns) isn't "
                    "returning the required JSON verdict. Configure "
                    "`small_fast_model` to a stricter model, then "
                    "/goal resume to continue."
                ),
            }

        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = (
                f"turn budget exhausted ({state.turns_used}/{state.max_turns})"
            )
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": verdict,
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — {state.turns_used}/{state.max_turns} "
                    "turns used. Use /goal resume to keep going, or "
                    "/goal clear to stop."
                ),
            }

        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": self.next_continuation_prompt(reason),
            "verdict": verdict,
            "reason": reason,
            "message": (
                f"↻ Continuing toward goal "
                f"({state.turns_used}/{state.max_turns}): {reason}"
            ),
        }

    def next_continuation_prompt(self, reason: str = "") -> Optional[str]:
        if not self._state or self._state.status != "active":
            return None
        reason = (reason or self._state.last_reason or "not done yet").strip()
        if self._state.subgoals:
            return CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
                goal=self._state.goal,
                subgoals_block=self._state.render_subgoals_block(),
                reason=reason,
            )
        return CONTINUATION_PROMPT_TEMPLATE.format(
            goal=self._state.goal, reason=reason,
        )


#: Decision returned when there is no active goal to evaluate.
_INACTIVE_DECISION: dict[str, Any] = {
    "status": None,
    "should_continue": False,
    "continuation_prompt": None,
    "verdict": "inactive",
    "reason": "no active goal",
    "message": "",
}


# ──────────────────────────────────────────────────────────────────────
# Rendering helpers
# ──────────────────────────────────────────────────────────────────────


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _fmt_tokens(n: int) -> str:
    n = max(0, int(n))
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


__all__ = [
    "DEFAULT_GOAL_MAX_TURNS",
    "DEFAULT_JUDGE_MAX_TOKENS",
    "DEFAULT_JUDGE_TIMEOUT_S",
    "GOAL_CLEAR_ALIASES",
    "GOAL_CONDITION_MAX_CHARS",
    "MAX_CONSECUTIVE_PARSE_FAILURES",
    "JUDGE_SYSTEM_PROMPT",
    "JUDGE_USER_PROMPT_TEMPLATE",
    "JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "CONTINUATION_PROMPT_TEMPLATE",
    "CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "GoalJudgeTimeout",
    "GoalManager",
    "GoalState",
    "build_judge_callable",
    "collect_turn_evidence",
    "judge_goal",
]
