# TODOS

## Server / Agent Loop

### Advisor tool violates default is_read_only / is_concurrency_safe

**What:** The `advisor` tool reports `is_read_only({}) == True` and `is_concurrency_safe({}) == True`, but the tool-property-parity suite expects tools not in the override list to fall back to the registry default (`False` for both). Also breaks two advisor smoke-test flows.

**Why:** Silently-wrong tool properties can change scheduling/concurrency behavior for a tool in ways nothing else guards against (e.g. a tool assumed read-only being allowed to run concurrently with a write).

**Context:** Discovered pre-existing on 2026-07-04 while shipping an unrelated `max_turns` default change (branch `chore/max-turns-default-50`) — verified these fail identically on `main` with that branch's changes stashed, so this predates and is unrelated to that PR. Failing tests:
- `tests/parity/test_tool_parity.py::TestToolPropertyParity::test_default_is_concurrency_safe_false`
- `tests/parity/test_tool_parity.py::TestToolPropertyParity::test_default_is_read_only_false`
- `tests/integration/test_advisor_smoke.py::TestAdvisorHappyPath::test_advisor_pair_preserved_in_history`
- `tests/integration/test_advisor_smoke.py::TestAdvisorInterruptPath::test_orphan_stripped_even_with_beta_active`

Start by checking whether `advisor` is missing from `tool_overrides` (if `True`/`True` is actually correct for this tool) or whether its `is_read_only`/`is_concurrency_safe` implementation is wrong (if `False`/`False` is correct and it should behave like other tools).

**Effort:** S
**Priority:** P0
**Depends on:** None

### Workspace-boundary blocking not enforced in write/read e2e flows

**What:** Writes and reads outside the workspace root are not being blocked in the e2e flow tests.

**Why:** Workspace-boundary enforcement is a safety boundary — if it's silently not firing, a tool call could read or write outside the intended sandboxed directory.

**Context:** Discovered pre-existing on 2026-07-04, same session as above — verified identical failures on `main` with the unrelated branch's changes stashed. Failing tests:
- `tests/parity/test_e2e_edit_flow.py::TestE2EWriteFlow::test_write_outside_workspace_blocked`
- `tests/parity/test_e2e_file_read.py::TestE2EFileRead::test_read_outside_workspace_blocked`

**Effort:** M
**Priority:** P0
**Depends on:** None

### Wire up max_cost_usd / settings.max_turns, and let the TUI override --max-turns per launch

**What:** `SettingsSchema.max_cost_usd` and `SettingsSchema.max_turns` are both defined and validated but never actually read/enforced anywhere in the query loop or agent-server (confirmed: `agent_server.py`'s only `load_settings()` call reads `.hooks` only). Separately, `clawcodex tui` spawns the backend without ever forwarding a `--max-turns` flag, so a running interactive session has no way to raise or lower its own turn ceiling.

**Why:** `AgentServerConfig.max_turns` / `--max-turns` is currently the *only* enforced ceiling on a single prompt's wall-clock time, token spend, $ cost, and tool side effects. Independently flagged by both a Claude adversarial-review subagent and a Codex adversarial pass while shipping the `max_turns` default bump (20→50, branch `chore/max-turns-default-50`) — raising that default widens the blast radius of this pre-existing gap by 2.5x with nothing else to catch a model that keeps calling tools "successfully" forever.

**Context:** Two independent fixes bundled here since they're the same root gap: (1) actually enforce `max_cost_usd`/`settings.max_turns` as a real backstop, not just a validated-but-unused setting; (2) add a per-launch (or in-session) `--max-turns` override path for `clawcodex tui`, mirroring the flag `clawcodex agent-server`/`clawcodex -p` already accept directly.

**Effort:** M
**Priority:** P2
**Depends on:** None

## TUI (Ink)

### 8 pre-existing vitest failures across five unrelated subsystems

**What:** `vitest run` in `ui-tui/` fails 8 tests spanning five subsystems that have nothing to do with each other:
- `src/__tests__/createGatewayEventHandler.test.ts` — "anchors inline_diff as its own segment where the edit happened"
- `src/__tests__/createGatewayEventHandler.test.ts` — "keeps verbose result text on inline_diff tool completions"
- `src/__tests__/cursorDriftRegression.test.ts` — "agrees with wrap-ansi at every typing-prefix of the user-reported message"
- `src/__tests__/statusRule.test.ts` — "collapses the context bar to a token count on narrow terminals"
- `src/__tests__/statusRule.test.ts` — "shows every segment on a wide terminal"
- `src/__tests__/useConfigSync.test.ts` — "falls back to kaomoji default when missing or invalid"
- `src/__tests__/useConfigSync.test.ts` — "defaults to kaomoji for missing/unknown values"
- `src/__tests__/virtualHeights.test.ts` — "uses compound user prompt width when estimating user message wrapping"

**Why:** A standing red baseline trains everyone to ignore vitest output, which is exactly how a known-8 becomes a known-12 and a real regression lands unnoticed. The Python suite is at a 0-failure baseline; the TS suite should be too. `cursorDriftRegression` and `virtualHeights` in particular guard real rendering correctness — composer cursor placement and scroll-height estimation — so a silent regression there is user-visible.

**Context:** Discovered pre-existing on 2026-07-30 while shipping the AskUserQuestion picker (branch `feat/ask-user-question-picker`) — verified they fail identically on `main` with that branch's changes stashed, so this predates and is unrelated to that PR. `createGatewayEventHandler.test.ts` is the only one whose source file that branch touched, and it fails the same way without the change.

Likely independent root causes given the spread; treat as five small investigations, not one. `useConfigSync` (2 failures, both about the kaomoji default) and `statusRule` (2 failures) each look like a single cause with two symptoms.

**Effort:** M
**Priority:** P0
**Depends on:** None

### Worker housekeeping stalls for the whole of a long AskUserQuestion dialog

**What:** `_run_worker` (`src/server/agent_server.py`) fires `_deliver_task_notifications()` and `_fire_due_scheduled()` only from its `except _queue.Empty` idle branch. While `ask_user` is parked waiting on the user, the worker is inside `_run_turn`, so neither runs. A user who walks away from a question dialog silences background-agent completion notifications and every scheduled-task/cron tick (`/loop`, `Cron*`) for up to `ask_user_timeout_s` (30 min).

**Why:** Those ticks are documented as "checks every second". 30 minutes is 6x the previous worst case, which was the 300s permission timeout — so this change widened an existing stall rather than creating it, but it widened it a lot. A `/loop` that silently skips half an hour looks like a broken loop, not a busy one.

**Context:** Found 2026-07-31 by the performance specialist during `/ship` of the AskUserQuestion picker (branch `feat/ask-user-question-picker`). Verified: the synchronous `pending.event.wait()` itself is CORRECT and does not block the stdio pump — sync tools dispatch via `asyncio.to_thread` (`tool_execution.py:590`), so the wait lands on a pool thread, and the stdio pump is a separate loop on the main thread. The stall is specifically the worker's idle-branch housekeeping, not the event loop.

Two candidate fixes: wait in short slices (`while not pending.event.wait(1.0) and elapsed < timeout`) pumping housekeeping each slice — but that puts worker concerns inside a session handler; or move the scheduler tick onto its own daemon thread so turn duration stops gating it. The second is cleaner and fixes it for long turns generally, not just dialogs.

**Effort:** M
**Priority:** P2
**Depends on:** None

### Ctrl+C on a prompt overlay leaves the status line stale

**What:** `cancelOverlayFromCtrlC` (`ui-tui/src/app/useInputHandlers.ts`) patches the overlay and the turn outcome but not `status`, while the `actions.answer*` paths also patch `status: 'running…'`. So dismissing an approval, a plan approval, or a question dialog with Ctrl+C leaves the footer showing the waiting-state text (`waiting on your answers` / the approval status) while the turn resumes.

**Why:** Cosmetic, but the footer is the one place that says whether the agent is waiting on you. Saying "waiting on your answers" when it is not is exactly the kind of small lie that trains people to stop reading it.

**Context:** Found 2026-07-31 during `/ship` of the AskUserQuestion picker. PRE-EXISTING pattern, not introduced by that branch — the `approval` and `planApproval` Ctrl+C branches have the identical omission, and the new `questions` branch faithfully copies them. Fix all three together, or route them through their `actions.answer*` callbacks once the `useMainApp` declaration-order constraint noted in the code comment is resolved (the callbacks are declared after `useInputHandlers` is called, which is why the branches use `gateway.rpc` directly).

**Effort:** S
**Priority:** P3
**Depends on:** None

## Completed


</content>
