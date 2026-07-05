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

## Completed
</content>
