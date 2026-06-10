# Faithful Port Plan: The Workflow Engine → clawcodex (Python)

> **Goal.** Port Claude Code's *dynamic workflows* subsystem into the clawcodex Python rebuild
> (`src/` at the repo root). "Faithful" = behaviourally and structurally faithful to the official
> spec and the TypeScript integration surface, expressed idiomatically in Python. See
> [`workflow-engine.md`](./workflow-engine.md) for the feature behaviour this plan implements.
>
> **Decided:** workflow scripts are **Python** (executed by an in-process sandbox that injects async
> orchestration globals), not JavaScript. The behaviour, API shape, and semantics are preserved;
> only the surface syntax the model authors changes.

**Anchor conventions.** Python anchors are `src/...` (repo root). TypeScript anchors are
`typescript/src/...` and appear only as the parity/Rosetta reference. All anchors verified against
the tree at the time of writing.

---

## 1. Current state in the Python tree

The port was **anticipated** — two scaffold points already exist, declared but unimplemented:

| Scaffold | Anchor | State |
|---|---|---|
| `"local_workflow"` task-type discriminant | `src/tasks_core.py:36` | Declared in `TaskType`; comment: "declared so the discriminator is byte-aligned with TS, but no Task implementation" |
| Workflow task-id prefix `"w"` | `src/tasks_core.py:73` (`_TASK_ID_PREFIXES`) | `generate_task_id("local_workflow")` already yields `w########` |
| `CommandBase.kind = "workflow"` badge field | `src/command_system/types.py:113` | Field present (`# "workflow" or None`); no producer yet |

Everything else is greenfield. Note two **stubs to be aware of** (not blockers, but adjacent):
- The TUI background-task panel is **broken**: `/tasks` calls `REPLScreen.focus_task_panel()` which **does not exist** (`src/tui/app.py:716-729`), so it always degrades to "Task panel focus is not available." The workflow port introduces the first working background-task UI.
- `StructuredOutputTool` exists but is an **unvalidated no-op** whose result never returns to a caller (§4.3).

---

## 2. Reuse map

### 2.1 Reusable as-is (the substrate)

| Concern | Reuse | Anchor |
|---|---|---|
| Run one subagent (stream messages) | `run_agent(params)` → `AsyncGenerator[Message]` | `src/agent/run_agent.py:218` (params dataclass `:36-66`) |
| One-shot subagent convenience | `run_query(params)` → `(messages, terminal)` | `src/query/query.py:1694` |
| Final text + usage from a finished run | `finalize_agent_tool(messages, agent_id, metadata)` | `src/agent/agent_tool_utils.py:200` (`AgentToolResult` `:176`); partial: `extract_partial_result` `:315` |
| Agent-type resolution + default | `get_agent_definitions_with_overrides(cwd)`, `find_agent_by_type`, `GENERAL_PURPOSE_AGENT` | `src/agent/load_agents_dir.py:115`; `src/agent/agent_definitions.py:88,248,265` |
| JSON-Schema validation (only validator in tree) | `validate_json_schema(value, schema, root)` | `src/tool_system/schema_validation.py:37` |
| Bounded-concurrency fan-out **pattern** | `asyncio.Semaphore + ensure_future + Queue` | `src/services/tool_execution/orchestrator.py:213-282` |
| Cancellation | `AbortController` / `AbortSignal`, `create_child_abort_controller(parent)` | `src/utils/abort_controller.py` (child `:88`) |
| Background-task model + store | `TaskStateBase`, `RuntimeTaskRegistry`, `Task` protocol | `src/tasks_core.py:104-158`; `src/task_registry.py:45-175` |
| Task lifecycle template | `LocalAgentTask` (state, register, progress, terminal, eviction) | `src/tasks/local_agent.py:35-405`; eviction `src/tasks/eviction.py:41-179` |
| Progress accounting | `AgentProgress`, `ProgressTracker`, `total_tokens_from_tracker` | `src/tasks/progress.py:67-114` |
| Tool construction + registration | `build_tool(...)`, `ToolRegistry`, `ALL_STATIC_TOOLS` | `src/tool_system/build_tool.py:122`; `src/tool_system/registry.py:20`; `src/tool_system/tools/__init__.py:43` |
| Command base + aggregation | `PromptCommand`/`InteractiveCommand`, `get_commands(cwd)`, builtins append-gate | `src/command_system/types.py:96-412`; `src/command_system/aggregator.py:71-118`; `src/command_system/builtins.py:1226-1266` |
| Disk→dynamic command precedent | `skills_integration.skill_to_prompt_command` + `src/skills/loader.py` disk walk | `src/command_system/skills_integration.py:28-73`; `src/skills/loader.py:600-737` |
| Permission decision + dialog | `has_permissions_to_use_tool`, `PermissionModal`, `auto_mode_classify` | `src/permissions/check.py:104-206,451-489`; `src/tui/screens/permission_modal.py:36-186` |
| Permission-rule persistence ("don't ask again for X in Y") | `PermissionUpdateAddRules`, `persist_permission_update`, `create_read_rule_suggestion` | `src/permissions/types.py:141-148`; `src/permissions/updates.py:267-345,366-391` |
| Settings/env gating pattern | advisor gate (`is_advisor_enabled`, `_env_truthy`) + `SettingsSchema` | `src/utils/advisor.py:73-133`; `src/settings/types.py:66-151`; `src/settings/settings.py:25-67` |
| Modal/list TUI primitives | `DialogScreen(ModalScreen)`, `SelectList`, master/detail exemplar | `src/tui/screens/dialog_base.py:33`; `src/tui/widgets/select_list.py:50-77`; `src/tui/screens/mcp_dialogs.py:40` |
| Cross-thread UI updates | `App.post_message` of `Message` subclasses via the bridge | `src/tui/messages.py`; `src/tui/agent_bridge.py:72` |
| Cost computation + accumulator | `compute_cost`, `add_to_total_cost_state`, `get_total_cost_usd` | `src/services/pricing.py`; `src/bootstrap/state.py:534,595` |
| Token-target budget semantics | `BudgetTracker`, `check_token_budget`, `parse_token_budget` | `src/query/token_budget.py:29,59,117` |

### 2.2 Net-new (the engine and its gaps)

1. **Python script sandbox** — there is **no `exec`/`eval`/`compile` of model code anywhere** (`ast` appears only in tests). Build: async-wrap + `compile` + `exec` into a curated namespace; `ast.parse` + `ast.literal_eval` for `meta`; frozen/absent `time`/`random`/`datetime`.
2. **Schema-validated structured output with return + retry** — the biggest gap. `StructuredOutputTool` (`src/tool_system/tools/structured_output.py`) does **not** validate (schema is `additionalProperties:True`), writes to `context.outbox`, and its result **is never read back** (only `SendUserMessage` outbox entries are consumed, `agent_loop_compat.py:411-421`); `finalize_agent_tool` extracts **text only**. Build: per-call schema injection, validation via `validate_json_schema`, retrieval from a dedicated channel, and the retry cap.
3. **Bounded subagent fan-out** — no `gather`-over-subagents exists; the Agent tool is **not concurrency-safe** (`is_destructive=True`, no `is_concurrency_safe`), so it runs serially today. Build the `parallel()`/`pipeline()` scheduler by cloning the `orchestrator.py` Semaphore/Queue pattern over `run_agent` coroutines.
4. **Per-run caps** — ≤16 concurrent, 1000/run, 4096/call have **no precedent**.
5. **Production-path budget predicate** — `max_budget_usd` is threaded (`src/tool_system/context.py:40`) but **inert**; the only predicate (`is_over_budget`) lives on the **deprecated** tracker (`src/services/cost_tracker.py:241`). Build a run-wide predicate over `get_total_cost_usd()` / token accounting.
6. **Per-agent abort map** — siblings track one `asyncio.Event`; workflows need a `dict[agent_id → abort handle]` plus a run-level abort (for `x` stop-agent-vs-stop-run and `r` retry).
7. **Background-task UI** — the pill (`get_pill_label`) and the `/workflows` progress/detail dialog do not exist; the `/tasks` panel is a broken stub.
8. **Progress channel** — no background-task progress `Message` exists; add one or poll the registry.
9. **Journaling + resume** — per-`runId` journal under the session dir; same-session prefix replay.
10. **Worktree-per-agent** — `wf_<runId>-<idx>` creation/cleanup.

---

## 3. The Python script engine (net-new core)

A new package, suggested `src/workflow/`:

```
src/workflow/
  runtime.py      # run_workflow(...) entry; owns the asyncio loop, journal, progress
  sandbox.py      # meta extraction + curated exec of the Python script
  scheduler.py    # Semaphore(≤16) + ensure_future + Queue; 1000/run + 4096/call caps
  primitives.py   # agent / pipeline / parallel / phase / log / workflow / budget impls
  structured.py   # schema-injected StructuredOutput + validate + retrieve + retry
  journal.py      # per-runId (call_index → result) persistence for resume
  registry.py     # bundled + disk-discovered workflows (name → script source)
  budget.py       # run-wide token/USD ceiling over the production accounting
```

**Verified API-usage profile** (from `demos/workflows/*.js`, four real workflows): `log` (27×),
`args` (27×), `agent` (20×), `phase` (20×), **`schema` structured output (19×)**, `parallel` (3×);
`pipeline`, `workflow`, `budget`, `isolation`, `model`, and `agentType` appear in the API but in
none of these demos. So the engine's **common path** — `agent` + `parallel` + `phase`/`log` + script
helpers + `return`, with **schema-validated structured output** — carries essentially all real
usage and must be the most robust part; the rarer primitives can land later (Phase 4+).

### 3.1 `runtime.run_workflow(...)`

`async def run_workflow(source: str, *, args, run_id, tool_context, on_progress, resume_from=None) -> WorkflowResult`.
Because the engine **owns its own async entry** (preferred over being driven by sync tool dispatch — see §6 trap), it can `await` natively. Steps: `parse_meta` → build the sandbox namespace (injected primitives bound to this run) → `exec` the wrapped script → `await __workflow_main__()` → collect the return value → finalize.

### 3.2 `sandbox.py` — meta extraction + curated exec

- **`extract_meta(source) -> dict`**: `tree = ast.parse(source)`; find the top-level `meta = {...}` (`ast.Assign`/`ast.AnnAssign`); `ast.literal_eval(node.value)` (rejects anything but literals — no execution); validate `name`/`description`/`phases`.
- **Curated execution** (idiomatic Python, the only viable approach — no precedent exists):
  ```
  wrapped = "async def __workflow_main__():\n" + textwrap.indent(source, "    ")
  code = compile(wrapped, "<workflow>", "exec")
  ns = {"__builtins__": CURATED_BUILTINS, "agent": ..., "pipeline": ..., "parallel": ...,
        "phase": ..., "log": ..., "workflow": ..., "args": args, "budget": budget}
  exec(code, ns)
  result = await ns["__workflow_main__"]()
  ```
- **Namespace contents — corrected against the real demo corpus.** The demos (`demos/workflows/*.js`) lean heavily on JSON, regex, and math (`JSON.parse`/`JSON.stringify`/`Math.round` are everywhere), so the sandbox must expose those, not strip all imports. Provide:
  - Safe builtins: `len`, `range`, `enumerate`, `sorted`, `min`, `max`, `sum`, `round`, `abs`, `any`, `all`, `dict`, `list`, `set`, `tuple`, `str`, `int`, `float`, `bool`, `zip`, `map`, `filter`, `reversed`, `isinstance`, `print`→`log`, `True/False/None`.
  - Exception types the scripts raise: `Exception`, `ValueError`, `RuntimeError`, `KeyError`, `TypeError`, `IndexError`.
  - The **deterministic** stdlib real workflows need: `json`, `re`, `math`, `collections`, `itertools`, `functools`, `string`, `textwrap`. Expose these **either** as pre-bound globals **or** via a **whitelisted `__import__`** (so the model can write `import json` / `import re` naturally) that permits only this set. (Decision §8.)
  - **Block**: `os`, `sys`, `subprocess`, `socket`, `pathlib`, `open`, `exec`, `eval`, `compile`, `input`, `globals`, and dunder-escape attributes. Removing the dangerous `__import__` targets (not `__import__` wholesale) keeps `import json` working while denying `os`/`subprocess`/`sys`.
  - **Exclude `time` / `random` / `datetime`** — non-determinism breaks resume (JS parity: `Date.now`/`Math.random` are unavailable; they appear **zero** times across the demos). A workflow needing a timestamp passes one via `args` or has an agent run `date` (demo 02 does exactly this in its CSV-export step).
  - **Define-before-use:** the script defines its own helper functions/classes (`slugify`, `formatOrganic`, …); Python does not hoist `def`, so a faithful JS→Python port must order helper definitions before their first call.
- **Security posture:** the script is **model-authored, not adversarial** — the goal is determinism + ergonomics, not a hard boundary (this matches clawcodex, which already runs tools in `bypassPermissions` mode by default, `src/tool_system/context.py:70`). `exec` with curated builtins removes fs/shell/import reach and forces all side effects through the injected agent primitives. A harder boundary (subprocess `-I`/`-S`, WASM) is out of scope.

### 3.3 `scheduler.py` — bounded concurrency + caps

Clone the canonical pattern (`src/services/tool_execution/orchestrator.py:213-282`): `asyncio.Semaphore(max_concurrent)`, workers via `asyncio.ensure_future`, results via `asyncio.Queue`, cancel outstanding tasks on early exit. Parameterize over `run_agent` coroutines instead of tool calls. Enforce:
- `WORKFLOW_MAX_CONCURRENT_AGENTS` — default **16**. House style uses fixed env/config ints (`os.cpu_count()` is used nowhere in `src/`), but the official spec reduces on low-core machines, so the faithful value is `min(16, max(1, (os.cpu_count() or 3) - 2))` with a `CLAUDE_CODE_WORKFLOW_MAX_AGENTS` override. (Decision §8.)
- `WORKFLOW_MAX_AGENTS_PER_RUN = 1000` (raise when exceeded).
- Per-call item cap **4096** for `pipeline`/`parallel` (explicit error).

### 3.4 `primitives.py` — the injected API

- **`agent(prompt, opts=None)`** — the core. Resolve `agent_type` (default `general-purpose`) via `find_agent_by_type`; build `RunAgentParams` (prompt, agent definition, model, a **child** `AbortController` from the run controller, `acceptEdits` permission mode, the inherited allowlist); acquire a scheduler slot; drive `run_agent(...)` to completion; `finalize_agent_tool(...)` → final text. If `opts.schema`, route through `structured.py` (§4.3). Record `(prompt, opts) → result` to the journal; update the task's per-agent state. Return `None` on skip/death.
- **`parallel(items)`** — barrier. `items` is an iterable of **coroutines** (`agent(...)`, the idiomatic Python form the demos' `await parallel([...])` maps to) **or** zero-arg callables returning coroutines (thunks, faithful to the JS `() => agent(...)` surface). Gather all under the concurrency cap; a raised item → `None`; the call itself never raises; results returned in input order (so `a, b = await parallel([...])` unpacking works).
- **`pipeline(items, *stages)`** — per-item independent stage chains, **no barrier**. Stages are async callables: the first is `stage(item)`, each later stage is `stage(prev, original_item, index)`; a raised stage drops that item to `None`. (Not used by the four demos, but part of the API surface.)
- The wrapped body's top-level **`return` value is the workflow result** (demo 02 returns `{"keyword", "csv_path", ...}`); a top-level **`raise`** aborts the run with that error.
- **`phase(title)` / `log(msg)`** — update the task's phase tree / emit a narrator line via the progress channel (§5.4).
- **`workflow(name_or_ref, args=None)`** — resolve from `registry.py`, run inline via `run_workflow`, one nesting level (raise if already nested).
- **`budget`** — a small object over `budget.py` (§4.4).

---

## 4. Module-by-module integration

### 4.1 The Workflow tool — `src/tool_system/tools/workflow.py`

`build_tool(name=WORKFLOW_TOOL_NAME, ...)` (template: the Agent tool's async-launch path,
`src/tool_system/tools/agent.py:395-614`, and `tasks_v2.py` TaskOutput poller). Contract:
- `input_schema` (plain JSON-Schema dict): one of `script` / `name` / `script_path`, optional `args`, `resume_from_run_id`.
- `call(input, context) -> ToolResult` — **`Tool.call` does not stream** (`src/tool_system/build_tool.py:43`); it may be `async def` (the registry bridges coroutines, `registry.py:134-188`). Generate a `w########` id (`generate_task_id("local_workflow")`), register a `LocalWorkflowTaskState`, launch `run_workflow(...)` as a background task (mirror `_launch_async_agent`), and **return a handle immediately** (`{"status":"workflow_launched","run_id":...,"task_output_key":...}`). Progress reaches the TUI via the task registry/progress channel (§5.4), **not** via `call`.
- `is_enabled` → gated on `disable_workflows` (§4.6). `is_read_only=True` (subagents carry their own permissions). `is_destructive` left default (False). Register in `ALL_STATIC_TOOLS` (`src/tool_system/tools/__init__.py:43`) — keep it a stable prefix entry.
- `WORKFLOW_TOOL_NAME = "Workflow"` in `src/agent/constants.py` (alongside `AGENT_TOOL_NAME`), and **added to `ALL_AGENT_DISALLOWED_TOOLS`** (`src/agent/constants.py:26-34`) so subagents can't recursively spawn workflows.

### 4.2 Subagent reuse for `agent()`

Use `run_agent` + `finalize_agent_tool` directly (NOT the `Agent` tool, which adds JSON-serialization, a sync/async bridge, and its own background-task registration — overhead the engine doesn't want). `_collect_agent_messages` (`src/tool_system/tools/agent.py:802`) is the reference for collect-and-track-progress.

### 4.3 Structured output — `src/workflow/structured.py` (largest gap)

`StructuredOutputTool` today: `call` appends raw input to `context.outbox` and returns a fixed string; schema is `additionalProperties:True`; result never returns; it's filtered out of worker tool sets (`src/coordinator/mode.py:97`). Build:
1. **Per-call schema tool**: construct a `StructuredOutput`-named tool whose `input_schema = opts.schema` and whose `call` runs `validate_json_schema(input, schema, "StructuredOutput")` (`src/tool_system/schema_validation.py:37`); on success, surface the validated object on a **dedicated channel** the engine reads (a fresh field on the subagent's `ToolContext`, or a sentinel in `outbox` the engine consumes — do not rely on the text-only `finalize_agent_tool`).
2. **Steer + retry**: inject a prompt instruction ("call StructuredOutput once at the end"); count `StructuredOutput` calls and cap retries (`MAX_STRUCTURED_OUTPUT_RETRIES`, default 5 — upstream parity; the existing loop only has `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT=3` for a different purpose). On exhaustion, resolve `agent()` to `None`.
3. Add the per-call tool to the subagent's `available_tools` for that run only.

### 4.4 Budget — `src/workflow/budget.py`

`budget.total` from the turn's token directive (parse via `parse_token_budget`, `src/query/token_budget.py:117`); `budget.spent()` reads the production accumulator `get_total_cost_usd()` / model-usage (`src/bootstrap/state.py:595`) — **a run-wide ceiling predicate must be added on the production path** (only `is_over_budget` exists, on the deprecated tracker `src/services/cost_tracker.py:241`). `remaining()` = `max(0, total - spent())`. Exceeding `total` makes `agent()` raise.

### 4.5 Background task — `src/tasks/local_workflow.py`

Mirror `src/tasks/local_agent.py`. Register in `src/tasks/__init__.py:42-44` and re-export in `__all__`.
- **`LocalWorkflowTaskState(TaskStateBase)`** with `type: Literal["local_workflow"] = "local_workflow"`, plus: `run_id`, `summary: str | None`, a **phase/agent tree** (`phases: list[WorkflowPhaseState]`, each phase → `agents: dict[str, WorkflowAgentState]`), a **per-agent abort map** (each `WorkflowAgentState` carries an `abort_event: asyncio.Event` + `AgentProgress`), a **run-level `abort_event`** (stop-all), `is_paused`, and the usual `error`/`result`/`evict_after`. Aggregate agent count / token total / elapsed by summing `AgentProgress` + `total_tokens_from_tracker` upward.
- All mutations go through `registry.update(task_id, mutator)` with a **synchronous pure mutator** using `dataclasses.replace` (hard contract — `update` rejects coroutine mutators before taking the lock; `src/task_registry.py:128-168`).
- **`class LocalWorkflowTask`**: `name`, `type="local_workflow"`, `async def kill` (run-level stop). Auto-reachable from `stop_task` (`src/tasks/stop_task.py:82`) once registered — no edit there.
- **Named API** (the TS `killWorkflowTask`/`skipWorkflowAgent`/`retryWorkflowAgent` equivalents): `kill_workflow_task(task_id, registry)`, `skip_workflow_agent(task_id, agent_id, registry)`, `retry_workflow_agent(task_id, agent_id, registry)` — these set the relevant `abort_event`(s) and re-enqueue work. Reuse `_terminal_replace` + `schedule_eviction` (`PANEL_GRACE_SECONDS=30`, `src/tasks/eviction.py:41`).

### 4.6 TUI — pill + `/workflows` dialog

- **Pill** (`get_pill_label`, port of `typescript/src/tasks/pillLabel.ts:60-61`): `"1 background workflow"` / `"N background workflows"`. New `src/tasks/pill_label.py`; consume in `StatusLine` (`src/tui/widgets/status_line.py`, which already polls `AppState` every 100ms — `:77`) or a footer segment. **`AppState` does not hold the registry today** — thread `runtime_tasks` (or a running-task snapshot) onto it.
- **`/workflows` progress view** — new `src/tui/screens/workflow_dialog.py` extending `DialogScreen(ModalScreen)` (`src/tui/screens/dialog_base.py:33`), using `SelectList` (`src/tui/widgets/select_list.py:50` — already binds `j/k`, `enter`, `esc`). Master/detail exemplar: `McpListScreen` (`src/tui/screens/mcp_dialogs.py:40`). Detail analog: `typescript/src/components/tasks/AsyncAgentDetailDialog.tsx`. Wire bindings to the §4.5 named API: `p`=pause/resume run, `x`=`skip_workflow_agent` (or `kill_workflow_task` when focused on the run), `r`=`retry_workflow_agent`, `s`=save script (§4.7), `Enter/→`=drill phase→agent, `Esc/←`=back, `j/k`=scroll.
- **Wire the command**: add `/workflows` to the TUI local builtins (`src/tui/commands.py:32-55`, dispatch `:272-291`) and an opener in `src/tui/app.py` (`_open_phase2_dialog` `:389-418`, opener pattern `_open_mcp_list` `:677-690`). (Optionally fix the broken `/tasks` `focus_task_panel` at the same time.)
- **Progress channel** (§5.4): lowest-friction faithful fit is **poll-on-interval** — the dialog reads `runtime_tasks.get(run_id)` on a Textual `set_interval` tick (the registry is RLock-guarded, safe from the UI thread), matching the `StatusLine` precedent. Alternatively add a `WorkflowProgress(Message)` to `src/tui/messages.py` posted via `agent_bridge` for push updates.

### 4.7 Commands — discovery, static, and bundled

- **Dynamic per-workflow commands**: new `src/command_system/workflows_integration.py` (clone of `skills_integration.py`) + a disk walker modeled on `src/skills/loader.py` retargeted to `.claude/workflows/` (project) and `~/.claude/workflows/` (personal). Each file → a `PromptCommand`-style command with `kind="workflow"` (`src/command_system/types.py:113`) and `loaded_from="project"|"user"`, whose `get_prompt_for_command` emits a Workflow tool call with the chosen `name`/`script_path`. Splice into `get_commands` (`src/command_system/aggregator.py:89-92`); **project-wins-over-personal** falls out of enumeration order + the existing name-dedupe. Add a `_load_workflow_commands_cached` and clear it in `clear_commands_cache()` (`aggregator.py:212`).
- **Static `/workflows` + bundled `/deep-research`**: module-level command singletons appended in `get_builtin_commands()` behind the `disable_workflows` gate (precedent: `if is_buddy_command_enabled(): cmds.append(...)`, `src/command_system/builtins.py:1264`). `/deep-research` is a markdown-bodied prompt command with a declared tool allowlist — template: `security_review.py` + `create_moved_to_plugin_command` (`src/command_system/moved_to_plugin.py:71`).
- **Save (`s` in `/workflows`)**: write the run's script to the chosen `.claude/workflows/` location; it becomes `/<name>` next session via the discovery loader above.

### 4.8 Permission

- **Approval dialog**: extend `PermissionModal` (`src/tui/screens/permission_modal.py:36-186`). Add a `"Workflow"` entry to `_TOOL_RENDERERS` (`:361-372`) rendering the planned phases + a "View raw script" body, and add the extra options ("Yes, and don't ask again for `<name>` in `<path>`") via the existing `enable_setting` channel (`:138-151`) → a `PermissionUpdateAddRules` scoped to name+path (`create_read_rule_suggestion` pattern, `src/permissions/updates.py:366`) persisted by `persist_permission_update` (`:267-345`).
- **Auto-mode pre-allowlist**: add a `"Workflow"` branch to `auto_mode_classify` returning `allow=True` (next to the existing `"Agent"` branch, `src/permissions/check.py:483-484`).
- **Subagents → `acceptEdits` + inherited allowlist**: in `_build_permission_context` (`src/agent/run_agent.py:108-161`) force `effective_mode="acceptEdits"` for workflow-spawned agents and ensure `always_allow_rules` are copied (already at `:155`); the allowlist is the agent's resolved tools (`resolve_agent_tools`, `src/agent/agent_tool_utils.py:83`).

### 4.9 Gating — settings + env + `/config`

No central feature-flag system exists; use the advisor pattern (`src/utils/advisor.py:73-133`):
- Add `disable_workflows: bool = False` to `SettingsSchema` (`src/settings/types.py:66-151`) and `DEFAULT_SETTINGS` (`src/settings/constants.py`). (If keeping the literal JSON key `disableWorkflows`, it is also readable via `SettingsSchema.extra`, the pattern used for `permissions.allowBypassPermissionsMode` at `src/permissions/modes.py:137`.)
- Env `CLAUDE_CODE_DISABLE_WORKFLOWS` via `_env_truthy` (`src/utils/advisor.py:73`); **add it to the honored-env allowlist** in `src/permissions/trust_boundary.py:75-96`.
- `/config` toggle writes through `ConfigManager.set_global/set_project` (`src/config.py:218-226`, as `set_effort` does `:355`).
- A gate helper `is_workflows_enabled()` (env shortcut beats settings) gates: the static/bundled command appends, the dynamic loader (returns `[]`), and the Workflow tool's `is_enabled`.

---

## 5. Cross-cutting design notes

### 5.1 The engine owns its loop
Launch the runtime as its own async entry (like `QueryEngine.submit_message`, `src/query/engine.py:203`) so `await agent(...)` and the bounded fan-out are native asyncio. Avoid driving it from synchronous tool dispatch; if the Workflow tool's `call` must kick it off, schedule the runtime as a background task on the running loop (mirror `agent.py:356-372`) rather than blocking dispatch.

### 5.2 Cancellation tree
One run-level `AbortController`; each `agent()` derives a child via `create_child_abort_controller` (`src/utils/abort_controller.py:88`) so ESC/`x`-on-run cascades to all subagents, while `x`-on-agent / `r` set only that agent's `abort_event`. Cancellation is **cooperative** — `run_agent` must observe the abort at yield points (today only the parent controller propagates; the per-agent `abort_event` check is part of the workflow wrapper). Tear down outstanding asyncio tasks with `task.cancel()` as the orchestrator does (`orchestrator.py:280-282`).

### 5.3 Journaling + resume — `src/workflow/journal.py`
Persist `(call_index → result)` per `runId` under the session dir (`~/.claude/projects/<session>/` — the official script-file location). On `resume_from_run_id`, the `agent()` primitive returns the cached result for unchanged `(prompt, opts)` at the same index and runs live from the first divergence. **Same-session only** (per spec); a fresh session restarts the run.

### 5.4 Progress channel
Each `phase()`/`log()`/agent-state change updates `LocalWorkflowTaskState` via `registry.update`. The TUI surfaces it by **polling** the registry on an interval (faithful, low-friction, matches `StatusLine`) or via a new `WorkflowProgress(Message)` posted through `agent_bridge` (`src/tui/agent_bridge.py:72`) for push updates. (Decision §8.)

---

## 6. Known traps / divergences from the TS source

- **`Tool.call` does not stream** (Python diverges from TS's async-generator `call`). Progress must flow through the background-task registry + progress channel, never `call` yields.
- **`StructuredOutputTool` is an unvalidated no-op** whose result never returns — must be upgraded, not merely reused (§4.3).
- **The Agent tool is serial** (`is_destructive=True`, not concurrency-safe). The fan-out limiter is genuinely net-new; reuse `run_agent`, not the Agent tool.
- **`RuntimeTaskRegistry.update` mutators must be synchronous pure functions** — never `await` under its lock (deadlocks asyncio vs. worker threads).
- **No `os.cpu_count()` in `src/`** — the ≤16 cap should be a constant/env per house style; the low-core reduction is an explicit deviation to add (§3.3, §8).
- **The `/tasks` background-task panel is a broken stub** — the workflow port is the first working background-task UI; budget for building it, not extending it.
- **Cost recording is a consumer concern**, not in the loop — the budget predicate must be added on the production path (`get_total_cost_usd`), not the deprecated tracker.
- **`src/constants/` is a placeholder**; tool-name/disallowed constants live in `src/agent/constants.py`.

---

## 7. Suggested sequencing

Each phase is independently testable; the feature is usable behind the gate after Phase 5.

| Phase | Deliverable | Gate / test |
|---|---|---|
| **0. Scaffold + gate** | `src/workflow/` package skeleton; `WORKFLOW_TOOL_NAME`; `disable_workflows` setting + env + `is_workflows_enabled()`; register tool (disabled by default). | Tree imports with the gate on/off; tool absent when disabled. |
| **1. Sandbox + meta** | `extract_meta` (ast) + curated `exec` async-wrap. | A trivial script's `meta` validates; body runs; `import`/`open` raise inside it. |
| **2. Core `agent()`** | `agent()` (text) over `run_agent` + `finalize_agent_tool`; serial scheduler. | A single-`agent()` script returns text end-to-end. |
| **3. Structured output** | schema-injected `StructuredOutput` + `validate_json_schema` + return channel + retry cap. | A script gets a validated object; malformed output retries then `None` at the cap. |
| **4. Concurrency + budget** | `parallel`/`pipeline`, `Semaphore(≤16)`, 1000/run + 4096/call caps, `budget`. | Fan-out respects the cap; `budget.remaining()` drives a loop; ceiling raises. |
| **5. Background task** | `LocalWorkflowTask` (+ state, named API, abort map), registration, pill. | A run shows in the pill; `stop_task` kills it; completes within the grace window. |
| **6. `/workflows` UI** | progress/detail dialog with `p/x/r/s/Enter/Esc/j/k`; progress channel. | Drill phase→agent; pause/stop/retry/save work live. |
| **7. Commands** | dynamic `.claude/workflows` discovery + static `/workflows` + bundled `/deep-research`. | `/<name>` runs a saved workflow; `/deep-research` works; project wins on clash. |
| **8. Permission** | approval renderer + don't-ask-again rule + auto-mode allowlist + subagent acceptEdits. | Default mode prompts per run; auto pre-allowlists; subagents auto-approve edits. |
| **9. Resume + worktree** | per-`runId` journal + `resume_from_run_id`; `wf_<runId>-<idx>` worktrees. | Same script+args ⇒ cache hit; edited call N re-runs from N; parallel file-mutating agents don't collide. |

---

## 8. Open decisions

1. **Concurrency cap source** — fixed `16` (house style) vs. `min(16, cpu_count−2)` (faithful low-core reduction). Recommend the latter with a `CLAUDE_CODE_WORKFLOW_MAX_AGENTS` override. (§3.3)
2. **Structured-output return channel** — a new `ToolContext` field vs. a typed sentinel in `outbox`. Pick one explicit channel; do not overload the text path. (§4.3)
3. **Progress delivery** — poll-on-interval (simplest, matches `StatusLine`) vs. a `WorkflowProgress` Textual message (push). (§5.4)
4. **Budget unit** — token-target (parity with `token_budget.py`) vs. USD cap (parity with TS `maxBudgetUsd`). Spec leans token; implement the predicate on the production accumulator either way. (§4.4)
5. **Save UX** — reuse `InteractiveCommand`/`ctx.ui.select` (like `copy_command.py`) for the two-location save picker, or a Textual modal. (§4.7)
6. **Fixing `/tasks`** — implement `REPLScreen.focus_task_panel` as part of this work, or scope the workflow dialog independently and leave `/tasks` broken.
7. **Settings key casing** — typed `disable_workflows` field vs. literal `disableWorkflows` via `SettingsSchema.extra`. (§4.9)
8. **Sandbox stdlib exposure** — pre-bound globals vs. a whitelisted `__import__` for the deterministic modules workflows need (`json`/`re`/`math`/`collections`/…). The demos write JSON/regex/math constantly, so one of these is mandatory; pick the one that reads most naturally for model-authored scripts. (§3.2)

---

## 9. Effort note

The integration glue (modules §4.1, §4.5–§4.9) is mechanical — every one has a sibling template in
the tree and a contract pinned by an existing consumer or the official spec. The genuine
engineering is **§3 (the script engine)** and **§4.3 (validated structured output with return +
retry)**: neither has any precedent in the codebase (no `exec` of model code exists; the structured
tool is a no-op), and they are where the upstream engine's complexity lived. Budget the bulk of the
effort there; the rest is faithful wiring against the anchors above. The two scaffold points already
in the tree (`local_workflow` task type, `CommandBase.kind="workflow"`) confirm the architecture was
designed to accept this port.

---

## 10. Implementation status

**Built (plan Phases 1–4: the engine core, `src/workflow/`) — 90 unit/integration tests passing.**

| Module | What it does | Tested |
|---|---|---|
| `sandbox.py` | `ast` `meta` extraction; curated `exec` async-wrap; whitelisted stdlib (`json`/`re`/`math`/…) with `time`/`random`/`datetime`/host access withheld | ✅ `test_sandbox.py` |
| `scheduler.py` | `asyncio.Semaphore` concurrency cap + per-run 1000 cap | ✅ `test_scheduler.py` |
| `budget.py` | token ceiling (`total`/`spent()`/`remaining()`), shared-pool via `base_spent` | ✅ `test_budget.py` |
| `callpath.py` + `journal.py` | **deterministic call-path keys** (fixes the multi-round fan-out resume bug) + per-key fingerprint matching | ✅ `test_journal.py`, `test_runtime.py::…multiround…` |
| `primitives.py` / `runtime.py` | `agent`/`parallel`/`pipeline`/`phase`/`log`/`workflow`/`budget`; failure→`None`; item cap; per-agent abort handles | ✅ `test_primitives.py`, `test_runtime.py` |
| `structured.py` | schema validation + retry collector + the injected `StructuredOutput` tool (built on the real `build_tool`) | ✅ `test_structured.py` |
| `runner.py` (`LiveAgentRunner`) | production seam: `run_agent` + `finalize_agent_tool` + structured tool | ⚠️ structured tool unit-tested; `run_agent` composition needs **live integration test** |
| `demos/workflows/*.py` | the four JS demos ported to Python; run end-to-end against the engine | ✅ `test_demos.py` |

**Built (plan Phases 5–9: integration) — the feature is now user-reachable behind the gate.**

| Piece | Module | Tested |
|---|---|---|
| Gating + constants | `src/workflow/gating.py`, `disable_workflows` setting, `CLAUDE_CODE_DISABLE_WORKFLOWS`, `WORKFLOW_TOOL_NAME` (+ recursion guard) | ✅ |
| Background task (Phase 5) | `src/tasks/local_workflow.py` (state, lifecycle, `kill`/`skip`/`retry` API), registered; pill `src/tasks/pill_label.py` | ✅ |
| Workflow tool (entry) | `src/tool_system/tools/workflow.py` + `src/workflow/launch.py`; registered (gated) in `defaults.py` | ✅ |
| Commands (Phase 7) | `src/command_system/workflows_integration.py` (discovery + `/deep-research`) + `workflows_command.py` (`/workflows`), spliced into the aggregator | ✅ |
| Permissions (Phase 8) | `auto_mode_classify` Workflow branch; subagents forced `acceptEdits` in `LiveAgentRunner` | ✅ (classify) |
| Resume persistence (Phase 9) | `Journal` persisted to the run's file; `resume_from_run_id` reloads it | ✅ |
| Bundled workflow | `src/workflow/bundled/deep_research.py` (real fan-out → cross-check → cited report) | ✅ |

**Polish — now built (port-plan §10 follow-ups):**

| Item | Module | Tested |
|---|---|---|
| Result delivery to the model | `enqueue_workflow_notification` on terminal transitions | ✅ |
| Run-file location | `get_workflow_run_path` → `~/.clawcodex/transcripts/workflows/` | ✅ |
| `retry_workflow_agent` | engine bounded retry loop (`WorkflowRun.retry_agent`) | ✅ |
| ProgressTracker tokens | `LiveAgentRunner` feeds `finalize_agent_tool(progress=…)` | ✅ |
| Worktree-per-agent | `isolation="worktree"` → `wf_<runId>-<idx>` (`src/workflow/worktree.py`) | ✅ |
| Live integration test | `LiveAgentRunner` → `run_agent` → real query loop w/ a fake provider | ✅ |
| `/workflows` TUI dialog | `src/tui/screens/workflow_dialog.py` (list + detail, `x` stop, `r` retry) + opener | ✅ (pilot) |
| Live pill | `StatusLine` shows "N background workflows" from `runtime_tasks` | ✅ (pilot) |

The integration test caught two real bugs (fixed): tool **dispatch resolves by name from the
registry**, so a schema agent needs a per-call registry where `StructuredOutput` is the validating
tool; and that injected tool was **permission-blocked** in the subagent (now explicitly allowed).

**Still remaining (genuinely small, not blocking):**
- **`p` (pause) / `s` (save)** in the `/workflows` dialog — pause needs an engine pause gate; save
  needs the `.claude/workflows` write + save dialog. The `x`/`r` actions and the detail view are built.
- **Budget shared-pool** — `Budget(base_spent=…)` accepts a shared-pool getter, but no token-target
  source is wired on this path (`budget_total` is `None` by default), so it's a param awaiting a caller.

**Hardening (post adversarial review).** Fixed before merge: the background launch now runs on a
dedicated daemon thread (`task_manager.start`) so the run outlives the throwaway `asyncio.run`
dispatch loop — with a production-topology regression test; schema subagents now go through
`resolve_agent_tools` (firewall + scoping, `Workflow` stripped — no recursion) before the injected
`StructuredOutput` tool, instead of receiving the full base pool; and `run_agent` now honors
`permission_mode_override`, so the `acceptEdits` guarantee for workflow subagents is actually applied.
