# Plan: dynamic workflow slash commands + the `ultracode` authoring keyword

> Closes the two gaps the user flagged against [`workflow-engine.md`](./workflow-engine.md)
> §4.1 (ultracode authoring) and §4.5/§4.7 (saved `/<name>` workflows). Status from an
> audit of the current tree, then a concrete build.

## Audit — what exists vs. what's missing

### Feature 1 — `ultracode` authoring keyword (§4.1, §4.8): **NOT IMPLEMENTED**
`grep -ri ultracode src/` → **0 hits.** None of the three pieces exist:
- the `ultracode` keyword in a prompt injecting a `<system-reminder>` that nudges the model to author/launch a workflow via the Workflow tool;
- `/effort ultracode` enabling a session-long auto-orchestration mode;
- the §4.8 gate (keyword no-ops + `ultracode` removed from the `/effort` menu when workflows are disabled).

> Note: Python's effort pipeline is **inert** (`effort_command.py` docstring: `settings.effort`
> reaches no request builder; there is no `xhigh`). So the faithfully-implementable half of
> `/effort ultracode` is the **session orchestration mode** (a standing reminder), not a reasoning level.

### Feature 2 — dynamic `/<name>` workflow commands (§4.5, §4.7): **PARTIAL**
- ✅ Bundled `/deep-research` + `/workflows` register into the global registry via
  `get_builtin_commands()` → dispatch + autocomplete work (fixed in #267).
- ✅ Discovery logic exists: `workflows_integration.load_workflow_commands(cwd)` finds project
  `.claude/workflows/*.py` + personal `~/.claude/workflows/*.py`, project-wins-personal.
- ❌ **Gap:** that discovery is wired only into the aggregator's `get_commands()`, which has
  **no real REPL consumers**. Saved workflows never reach the global command registry that
  dispatch + suggestions read. **Confirmed empirically:** a saved `.claude/workflows/triage_issues.py`
  → `global registry (dispatch): False`, `aggregator get_commands: True`. So `/triage-issues`
  does not dispatch or autocomplete in the REPL.

The fix model already exists for skills: `load_and_register_skills(registry=None)` is called at
startup from REPL `_init_command_system` (`core.py:1073`) and TUI `app.py:1486`. Workflows need the
identical treatment — they have no `load_and_register_workflows`.

## Build

### Part A — register saved workflows into the command registry
- **`src/command_system/workflows_integration.py`**: add
  `load_and_register_workflows(project_root=None, registry=None) -> list[PromptCommand]`, mirroring
  `load_and_register_skills`:
  - reuse `_discover_dir` for project `.claude/workflows/` + personal `~/.claude/workflows/`
    (project enumerated first → wins on clash);
  - **shadowing guard**: skip any name colliding with an already-registered command or alias
    (builtins/bundled win — same rule skills use);
  - gated by `is_workflows_enabled()` → returns `[]` when disabled;
  - registers ONLY the discovered project/personal commands (bundled `/deep-research` + `/workflows`
    are already registered by `register_builtin_commands`).
- **Call it at startup** after skills: REPL `core.py::_init_command_system` and TUI
  `app.py` (next to the existing `load_and_register_skills(registry=None)`).
- **Tests** (`tests/test_workflow_dynamic_commands.py`): a saved project workflow resolves in the
  global registry + carries the Workflow-tool directive; project-wins-personal; builtin-wins-over-
  workflow on a name clash; disabled ⇒ not registered.

### Part B — the `ultracode` authoring keyword + session mode
- **New `src/workflow/ultracode.py`** (pure + process-global session flag, like
  `message_queue_manager`):
  - `prompt_requests_ultracode(text) -> bool` — case-insensitive `\bultracode\b`, gated by
    `is_workflows_enabled()`;
  - session flag: `set_ultracode_session(bool)` / `is_ultracode_session()` / `reset_ultracode()`;
  - `ultracode_reminder_for(text) -> str | None` — the single decision used by the chat seam:
    returns the keyword `<system-reminder>` when the keyword is present, else the standing
    session reminder when session mode is on, else `None`; always `None` when workflows are disabled;
  - reminder texts: confirm ultracode and instruct the model to **author/launch a workflow via the
    Workflow tool** for this task (keyword = this turn; session = every substantive task this session).
- **REPL `chat()` wiring**: after the existing at-mention/companion-intro attachment block, append
  `ultracode_reminder_for(user_input)` to `user_input` when non-None (mirrors the companion-intro append).
- **`/effort ultracode`** (`effort_command.py`): an `ultracode` branch (gated by
  `is_workflows_enabled()`) that calls `set_ultracode_session(True)` and confirms; selecting any real
  level or `auto` calls `set_ultracode_session(False)` (reset per "reset with /effort high"). Add an
  `ultracode` picker option **only when workflows are enabled** (§4.8 removal when disabled).
- **Tests** (`tests/test_ultracode.py`): keyword detection (positive / negative / word-boundary /
  case); `ultracode_reminder_for` precedence + gating (None when disabled); session set/clear/reset;
  `/effort ultracode` enables session mode and is gated; a real `/effort` level clears it.

## Out of scope (call it out, don't half-build)
- Wiring the keyword into the **TUI** chat path (different submission seam); the detection/reminder
  logic is a reusable pure module so the TUI can adopt it in a follow-up. REPL is the user's surface.
- `Option+W` keyword-highlight dismissal and the editor "View raw script" affordance (UI polish).
- Making the effort *reasoning level* actually reach inference (a separate, already-deferred phase).
