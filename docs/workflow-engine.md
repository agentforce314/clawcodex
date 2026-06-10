# The Workflow Engine — Feature Deep Dive

> **Purpose.** This document is the authoritative behavioural specification of Claude Code's
> *dynamic workflows* feature, written to drive a faithful port into the **clawcodex Python
> implementation** (`src/` at the repo root). It is implementation-neutral: it describes *what the
> feature does* and *how a user experiences it*. The companion
> [`workflow-engine-port-plan.md`](./workflow-engine-port-plan.md) maps every piece onto the Python
> architecture with `file:line` anchors.
>
> **Sources of truth.** (1) The official documentation —
> <https://code.claude.com/docs/en/workflows> (feature requires Claude Code v2.1.154+). (2) The
> public Workflow tool contract (the script API the model is taught). (3) The surviving TypeScript
> integration surface under `typescript/src/`, which carries the real signatures of the
> closed-source engine and is used as a Rosetta stone. Where these disagree, the official docs win.

---

## 1. What a workflow is

A **dynamic workflow is a JavaScript script that orchestrates [subagents](https://code.claude.com/docs/en/sub-agents) at scale.** Claude writes the script for the task you describe, and a runtime executes it **in the background** while your session stays responsive. Intermediate results live in **script variables**, not in Claude's context window — so Claude's context holds only the final answer.

Reach for a workflow when a task needs more agents than one conversation can coordinate, or when you want the orchestration codified as a script you can read and rerun. Canonical uses: a codebase-wide bug sweep, a 500-file migration, a research question whose sources must be cross-checked against each other, and a hard plan worth drafting from several independent angles before committing.

Crucially, moving the plan into code lets a workflow apply a **repeatable quality pattern**, not just run more agents: independent agents can adversarially review each other's findings before anything is reported, or draft a plan from several angles and weigh them — yielding a more trustworthy result than a single pass.

> **Port note.** In a Python host the script language is **Python**, not JavaScript (decided —
> see the port plan). The *behaviour*, *API shape*, and *semantics* below are preserved; only the
> surface syntax the model authors changes. Every JS signature in this document has a direct
> Python equivalent (`await agent(...)`, `await pipeline(...)`, `meta = {...}`).

### 1.1 Workflows vs. subagents, skills, and agent teams

All four can run a multi-step task; the difference is **who holds the plan** (from the official docs):

| | Subagents | Skills | Agent teams | **Workflows** |
|---|---|---|---|---|
| What it is | A worker Claude spawns | Instructions Claude follows | A lead supervising peer sessions | **A script the runtime executes** |
| Who decides what runs next | Claude, turn by turn | Claude, per the prompt | The lead, turn by turn | **The script** |
| Where intermediate results live | Claude's context | Claude's context | A shared task list | **Script variables** |
| What's repeatable | The worker definition | The instructions | The team definition | **The orchestration itself** |
| Scale | A few per turn | A few per turn | A handful of peers | **Dozens to hundreds per run** |
| Interruption | Restarts the turn | Restarts the turn | Teammates keep running | **Resumable in the same session** |

---

## 2. The programming model

A workflow script (Python in the port; JavaScript upstream) runs in an **async context** with a small set of orchestration primitives injected as globals. It must begin with a `meta` declaration.

### 2.1 The `meta` block

`meta` is a **pure literal** — no variables, calls, spreads, or interpolation — because the runtime extracts and validates it by **static analysis before executing the script**. (In the Python port this is an `ast.literal_eval` over the `meta = {...}` assignment node.)

```python
# Python-native form (port)
meta = {
    "name": "find-flaky-tests",
    "description": "Find flaky tests and propose fixes",   # shown in the approval prompt
    "when_to_use": "...",                                   # optional; shown in the workflow list
    "phases": [                                             # optional; one entry per phase() call
        {"title": "Scan", "detail": "grep test logs for retries"},
        {"title": "Fix",  "detail": "one agent per flaky test"},
    ],
    "model": "sonnet",                                      # optional default/per-phase model
}
```

Required: `name`, `description`. `phases[].title` values are matched **exactly** against `phase()` calls in the body to drive the progress display.

### 2.2 Injected globals (the orchestration API)

| Primitive | Signature | Behaviour |
|---|---|---|
| `agent` | `await agent(prompt, opts=None) -> Any` | Spawn one subagent. Without `schema`, resolves to its final text. With `schema`, the subagent is forced to emit a **validated** object. Resolves to `None` if the agent is skipped or dies after retries. |
| `pipeline` | `await pipeline(items, stage1, stage2, ...) -> list` | Run each item through all stages **independently — no barrier between stages** (item A may be in stage 3 while item B is still in stage 1). The default for multi-stage work. Each later stage receives `(prev_result, original_item, index)`. A stage that raises drops that item to `None`. |
| `parallel` | `await parallel(thunks) -> list` | Run thunks concurrently and **await all** (a barrier). A thunk that raises resolves to `None`; the call itself never raises. Use only when you genuinely need all results together. |
| `phase` | `phase(title) -> None` | Start a new progress phase; subsequent `agent()` calls group under it. |
| `log` | `log(message) -> None` | Emit a narrator progress line to the user. |
| `workflow` | `await workflow(name_or_ref, args=None) -> Any` | Run another workflow inline as a sub-step. **One level of nesting only** — `workflow()` inside a child raises. |
| `args` | value | The `args` input passed at invocation, verbatim (structured data — lists/dicts usable directly). `None`/`undefined` if omitted. |
| `budget` | `{ total, spent(), remaining() }` | The turn's token target. `total` is `None` if unset; `spent()` is the shared output-token total across the main loop **and** all workflows; reaching `total` is a **hard ceiling** that makes further `agent()` calls raise. Scripts scale depth with `while budget.total and budget.remaining() > N: ...`. |

`agent()` options (`opts`): `label`, `phase`, `schema`, `model`, `isolation="worktree"`, `agent_type`.
- `schema` — a JSON Schema; forces schema-validated structured output (see §2.3).
- `label` — overrides the display label; `phase` — explicitly assigns this call to a progress group (use inside `pipeline`/`parallel` stages to avoid racing the global `phase()` state).
- `model` — per-call model override (omit to inherit the session model — almost always correct).
- `isolation="worktree"` — run the agent in a fresh git worktree (expensive; only when parallel agents mutate files and would conflict).
- `agent_type` — use a named subagent type (e.g. `Explore`) instead of the default general agent; composes with `schema`.

### 2.3 Structured output

`agent(prompt, {schema})` gives the subagent a `StructuredOutput` tool compiled from the JSON Schema. The subagent is steered (by the tool's prompt, not by a forced API `tool_choice`) to call it once at the end; the call's input is **validated against the schema**, and the validated object flows back to the script as the resolved value. Validation failures become an error the model sees and retries, up to a retry cap (upstream default 5). A schema reused across many `agent()` calls is compiled once (identity-cached upstream via `WeakMap`).

### 2.4 Canonical composition patterns

These are conventions the model is taught, not separate API:

- **Pipeline by default** — multi-stage work with no cross-item barrier.
- **Barrier only when stage N needs all of stage N−1** — dedup/merge across the full set, early-exit on zero, or cross-item comparison. Otherwise the transform goes *inside* a pipeline stage.
- **Adversarial verify** — N independent skeptics per finding, each prompted to refute; drop on majority-refute.
- **Perspective-diverse verify** — give each verifier a distinct lens (correctness, security, perf, reproducibility) rather than N identical refuters.
- **Judge panel** — generate N independent attempts from different angles, score with parallel judges, synthesize from the winner.
- **Loop-until-dry** — keep spawning finders until K consecutive rounds surface nothing new.
- **Multi-modal sweep / completeness critic** — parallel searches each blind to the others, then a final agent asking "what's missing?".

### 2.5 The runtime environment (Python port)

The script runs inside an **async sandbox**. Available: the *deterministic* parts of the standard
library that real workflows rely on — `json`, `re`, `math`, `collections`, `itertools`,
`functools`, `string`, `textwrap` — plus the safe builtins and exception types. The script may
define and call its own helper functions and classes (**define before use** — Python does not hoist
`def`). Top-level `await`, `return` (whose value becomes the workflow's result), and `raise` all
work. **Not** available: any filesystem, shell, or network access from the script itself (agents do
all I/O), and the non-deterministic primitives `time`, `random`, and `datetime.now()` — they would
break deterministic resume, so pass timestamps via `args` or have an agent run `date`. `parallel`
and `pipeline` accept either coroutines (`agent(...)`, idiomatic) or zero-arg callables returning
them.

### 2.6 A worked example (Python)

A faithful Python translation of the basic structure used by the real demo corpus
(`demos/workflows/*.js`) — `meta`, `args` handling, a pure helper, `phase`/`log`, a parallel pair of
a text agent and a schema agent, and a `return` value:

```python
meta = {
    "name": "user-prompt-research",
    "description": "Discover what customers ask about a keyword; score questions by search volume.",
    "when_to_use": "Run with a company, product, or keyword to mine real user questions for AEO.",
    "phases": [
        {"title": "Search",    "detail": "Reddit + Google search in parallel"},
        {"title": "Questions", "detail": "Rewrite intent signals into natural user questions"},
    ],
}

# args may be a bare keyword string or a dict; normalize both.
data = args if isinstance(args, dict) else {}
keyword = (args if isinstance(args, str) else data.get("keyword", "")).strip()
if not keyword:
    raise ValueError('Pass a keyword via args — e.g. args="AI coding assistant"')
count = int(data.get("count", 50))

def slugify(s, maxlen=80):  # plain helper, defined before use (Python does not hoist)
    return re.sub(r"-+$", "", re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:maxlen]) or "item"

GOOGLE_SCHEMA = {
    "type": "object",
    "properties": {
        "organic_results": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "link": {"type": "string"}},
            "required": ["title", "link"],
        }},
        "related_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["organic_results", "related_questions"],
}

log(f'Keyword: "{keyword}" · target questions: {count}')

phase("Search")
reddit, google = await parallel([
    agent(f'Summarize real Reddit discussion about "{keyword}" for SEO research ...', label="reddit"),
    agent(f'Gather the Google SERP for "{keyword}" ...', label="google", schema=GOOGLE_SCHEMA),
])
google = google or {"organic_results": [], "related_questions": []}
log(f'Google: {len(google["organic_results"])} organic results')

phase("Questions")
raw = await agent(
    f"Using this research, write {count} natural user questions, one per line:\n"
    f"{reddit}\n{json.dumps(google)}",
    label="questions",
)
questions = [q.strip() for q in (raw or "").splitlines() if "?" in q]
if not questions:
    raise RuntimeError("No questions produced.")

return {                                   # the returned value is the workflow's result
    "keyword": keyword,
    "questions": questions,
    "csv_path": f"./out/{slugify(keyword)}-questions.csv",
}
```

The original JavaScript form of this workflow is `demos/workflows/02-user-prompt-research.js`; the
Python ports of the demo corpus should live alongside it (e.g. `demos/workflows/*.py`).

---

## 3. Execution semantics

| Aspect | Behaviour (authoritative) |
|---|---|
| **Concurrency cap** | **Up to 16 concurrent agents, fewer on machines with limited CPU cores.** Excess `agent()` calls queue and run as slots free. (Upstream cap: `min(16, cpu_cores − 2)`.) |
| **Lifetime agent cap** | **1,000 agents total per run** — a runaway-loop backstop. |
| **Per-call item cap** | A single `pipeline()`/`parallel()` call accepts at most 4,096 items; more is an explicit error, not silent truncation. |
| **No mid-run user input** | Only agent permission prompts can pause a run. For sign-off between stages, run each stage as its own workflow. |
| **No direct fs/shell from the script** | The script coordinates; **agents** read, write, and run commands. The workflow body has no filesystem or shell access. |
| **Budget** | A turn-level token target acts as a hard ceiling. `budget.spent()` is shared across the main loop and all workflows; once it reaches `budget.total`, `agent()` raises. The agent caps also bound the cost of a runaway script. |
| **Worktree isolation** | `agent(..., {isolation:"worktree"})` runs the agent in a fresh git worktree, auto-removed if unchanged. Per-agent worktrees are named `wf_<runId>-<idx>`. |
| **Sandbox restrictions** | No filesystem/host access from the script. Non-deterministic primitives (`Date.now`/`Math.random` upstream; `time`/`random`/`datetime.now` in the Python port) are unavailable or frozen — they would break deterministic resume. Timestamps come in via `args`; randomness varies by agent index. |
| **Resume / journaling** | Every run has a `runId`. Each `agent()` call's inputs and result are journaled. Resuming replays the **longest unchanged prefix** from cache and runs the rest live; same script + same args ⇒ 100% cache hit. **Resume works only within the same session** — exiting Claude Code restarts a running workflow fresh next session. |
| **Model** | Every agent uses the **session's model** unless the script routes a stage elsewhere via `agent(..., {model})`. |
| **MCP access** | Workflow agents reach session-connected MCP tools (schemas load on demand). Interactively-authenticated MCP servers may be absent in headless/cron runs. |

---

## 4. The user experience and surfaces

### 4.1 Authoring a workflow

- **Ask in your prompt.** Include the keyword `ultracode` (pre-v2.1.160 it was `workflow`), or just ask in your own words ("use a workflow", "run a workflow"). Claude writes a script for the task instead of working turn by turn. `Option+W` / `Alt+W` dismisses the keyword highlight for one prompt.
- **`/effort ultracode`.** Combines `xhigh` reasoning with automatic workflow orchestration: Claude plans a workflow for *every* substantive task in the session (often several in a row — understand, change, verify). Lasts the session; reset with `/effort high`. Only on models that support `xhigh` effort.

### 4.2 Bundled workflow: `/deep-research`

The one built-in workflow. `/deep-research <question>` fans out web searches across several angles, fetches and cross-checks sources, **votes on each claim**, and returns a cited report with claims that didn't survive cross-checking filtered out. Requires the WebSearch tool. Saved workflows become `/<name>` commands the same way and appear in `/` autocomplete alongside it.

### 4.3 Watching a run: `/workflows`

Workflows run in the background. `/workflows` lists running and completed runs; select one to open the **progress view** showing each phase with its **agent count, token total, and elapsed time**. A one-line summary also appears in the task panel below the input box. Key bindings in the progress view:

| Key | Action |
|---|---|
| `↑` / `↓` | Select a phase or agent |
| `Enter` / `→` | Drill into the phase, then into an agent (read its prompt, recent tool calls, result) |
| `Esc` | Back out one level |
| `j` / `k` | Scroll within agent detail on overflow |
| `p` | **Pause or resume** the run |
| `x` | **Stop** the selected agent, or the whole workflow when focus is on the run |
| `r` | **Restart** the selected running agent |
| `s` | **Save** the run's script as a command |

### 4.4 Approval and permission model

The per-run prompt shows the planned phases and: **Yes, run it** / **Yes, and don't ask again for `<name>` in `<path>`** / **View raw script** / **No**. `Ctrl+G` opens the script in your editor; `Tab` lets you adjust the prompt before the run starts.

Whether you're prompted depends on permission mode:

| Permission mode | When you're prompted |
|---|---|
| Default, accept edits | Every run, unless you chose "don't ask again" for that workflow in this project |
| Auto | First launch only; any **Yes** records consent in user settings; skipped entirely when ultracode is on |
| Bypass / `claude -p` / Agent SDK | Never — the run starts immediately |

**Critically:** the subagents a workflow spawns **always run in `acceptEdits` mode and inherit your tool allowlist, regardless of your session's mode.** File edits are auto-approved; shell/web/MCP calls *not* in your allowlist can still prompt mid-run. Your session's permission mode controls only the launch prompt.

### 4.5 Saving, discovery, and `args`

- **Save:** in `/workflows`, select a run and press `s`. `Tab` toggles two locations: `.claude/workflows/` (project, shared via the repo) or `~/.claude/workflows/` (personal, all projects). Saved workflows run as `/<name>`. **A project workflow wins over a personal one** on name clash.
- **`args`:** a saved workflow reads invocation input from the `args` global, passed as **structured data** (lists/dicts usable without parsing); `undefined`/`None` if omitted. Example: `Run /triage-issues on issues 1024, 1025, and 1030`.

### 4.6 Persistence and resume

Every run writes its script to a file under the session's directory in `~/.claude/projects/<session>/`; Claude receives the path when the run starts (you can ask for it, diff it against a prior run, or edit it and relaunch). The runtime tracks each agent's result, which is what makes a run **resumable within the same session** (`p` in `/workflows`, or ask Claude to relaunch with the same script). Exiting Claude Code mid-run restarts it fresh next session.

### 4.7 Cost

A workflow spawns many agents, so one run can use meaningfully more tokens than the same task in conversation; runs count toward plan usage and rate limits. The agent caps bound a runaway script. The `/workflows` view shows each agent's token usage live, and you can stop a run there without losing completed work. To gauge spend, run on a small slice first.

### 4.8 Turning workflows off

- `/config` → toggle **Dynamic workflows** off (persists).
- `"disableWorkflows": true` in `~/.claude/settings.json` (persists), or in managed settings for an org.
- `CLAUDE_CODE_DISABLE_WORKFLOWS=1` (read at startup).

When disabled: bundled workflow commands are unavailable, the `ultracode` keyword no longer triggers a run, and `ultracode` is removed from the `/effort` menu.

---

## 5. Reference architecture (implementation-neutral)

The engine is a constellation of seams, not one module. The port maps each onto a clawcodex subsystem; see the port plan for exact targets.

```
   user / model invokes a workflow
   (ultracode keyword · /deep-research · /<saved-name>)
                    │
                    ▼
        ┌───────────────────────┐   spawns + streams
        │   Workflow tool        │──────────────────────────────┐
        │   (one tool-use call)  │                              │
        └───────────┬───────────┘                              ▼
                    │ runs the script           ┌───────────────────────────┐
                    ▼                            │  per-agent() call:        │
        ┌───────────────────────┐  agent()      │   subagent runner         │
        │   Workflow runtime    │──────────────►│   + finalize → text       │
        │   · sandbox (script)  │◄──────────────│   + schema → validated obj │
        │   · scheduler (≤16)   │   result      │   + child abort controller │
        │   · budget · journal  │               └───────────────────────────┘
        └───────────┬───────────┘
                    │ surfaces as a background task
                    ▼
   ┌───────────────────────────┐    ┌─────────────────────────────────────┐
   │  Workflow background task │───►│  task-panel pill ("N background      │
   │  (phases → agents tree,   │    │  workflows") · /workflows progress   │
   │   per-agent abort map)    │    │  view · per-agent detail (p/x/r/s)   │
   └───────────────────────────┘    └─────────────────────────────────────┘

   discovery   ──►  /workflows (static) · /deep-research (bundled) ·
                    /<name> per file in .claude/workflows + ~/.claude/workflows
   approval    ──►  per-run permission prompt; subagents run acceptEdits + inherit allowlist
   gating      ──►  disableWorkflows setting · CLAUDE_CODE_DISABLE_WORKFLOWS · /config
```

The subsystems involved:

1. **The Workflow tool** — a single tool-use the model calls; it launches the run and returns a handle/result.
2. **The runtime** — executes the script in a sandbox, injects the primitives, schedules bounded-concurrency `agent()` fan-out, accounts budget, and journals for resume.
3. **The subagent path** — each `agent()` reuses the host's existing subagent runner and result-finalization, plus a schema-validated structured-output mechanism.
4. **The background task** — a phase/agent tree with per-agent abort handles, surfaced as a pill and a `/workflows` progress/detail view.
5. **Commands** — a static `/workflows`, the bundled `/deep-research`, and one dynamic `/<name>` command per saved workflow file.
6. **Permission** — a per-run approval dialog with "don't ask again for `<name>` in `<path>`"; auto-mode pre-allowlisting; subagents forced to `acceptEdits` with the inherited allowlist.
7. **Gating** — a settings key + env var + `/config` toggle.

---

## 6. Provenance — why there is no source to copy

The upstream engine is gated behind the internal-only build flag `WORKFLOW_SCRIPTS`. In the bundled `typescript/` snapshot its implementation files were stripped (only the stub `typescript/src/tools/WorkflowTool/constants.ts` remains), and the compiled `dist/cli.mjs` was built with the flag off, so every workflow symbol was dead-code-eliminated to `null`. The canonical public repo `Gitlawb/openclaude` ships the identical stub. What survives is the **integration surface** — the flag-gated call sites — which carries the real signatures and is the Rosetta stone the port plan uses. There is therefore nothing to extract; the engine must be **reconstructed** against this spec. The reconstruction targets the **Python** implementation; see [`workflow-engine-port-plan.md`](./workflow-engine-port-plan.md).
