# ClawCodex Workflow Configuration Guide

This guide shows how to configure `workflow.md` (or `WORKFLOW.md`) to fully
wire up the autonomous issue→PR pipeline that runs in `extensions/orchestrator`.

> **Naming note.** The historical filename `WORKFLOW.md` still works. The
> orchestrator now uses `workflow.md` as the canonical name. See
> `extensions/orchestrator/templates/workflow.template.md` for the
> placeholders and per-field comments.

---

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [Top-Level Config Anatomy](#top-level-config-anatomy)
3. [Tracker Adapters](#tracker-adapters)
4. [Workspace & Cloning](#workspace--cloning)
5. [Hooks: before/after Run and Sync](#hooks-beforeafter-run-and-sync)
6. [Agent Configuration](#agent-configuration)
7. [Verification (F-38)](#verification-f-38)
8. [Review Feedback Auto-Fix (F-37)](#review-feedback-auto-fix-f-37)
9. [Operator Intent System (F-39)](#operator-intent-system-f-39)
10. [Three-Channel Clarification](#three-channel-clarification)
11. [Observability & Server](#observability--server)
12. [CLI Reference](#cli-reference)
13. [End-to-End Flow Example](#end-to-end-flow-example)
14. [Conflict Recovery](#conflict-recovery)
15. [Reports & Persistence](#reports--persistence)
16. [Complete Key Reference](#complete-key-reference)

---

## Pipeline Overview

```
issue polling → clone → after_create → before_run → agent → after_run
                → pre_commit hook → git commit → pre_push verification
                → pre_push hook → git push → ensure PR → update PR
                → post_sync hook → update summary comment
                          ↓
            (if conflict): rebase → agent fix → loop
```

| Stage | Responsible Config | What Happens |
|-------|-------------------|--------------|
| Issue polling | `tracker.*` | Fetches issues from Linear / GitHub / Gitee / GitCode / Local markdown |
| Repository clone | `workspace.repo_clone_url` | `git clone` into `workspace.root` |
| After-create hook | `hooks.after_create` | Runs shell in workspace after clone (e.g. `uv sync`) |
| Before-run hook | `hooks.before_run` | Runs shell right before the agent starts |
| Agent development | `agent.*` + `codex.*` | Claude Code develops the fix |
| After-run hook | `hooks.after_run` | Runs shell right after the agent exits |
| Pre-commit hook | `hooks.pre_commit` | Runs shell on staged changes; may auto-amend |
| Pre-push verification | `agent.test_command` / `build_command` / `lint_command` | Runs shell verification (gates push) |
| Pre-push hook | `hooks.pre_push` | Runs shell right before `git push` |
| Git push | `git_sync.py` auto | commit → push → rebase on conflict |
| PR creation | `tracker.*` (adapter) | `ensure_pull_request` via platform API |
| PR body update | `git_sync.py` auto | Re-renders body with status snapshot |
| Post-sync hook | `hooks.post_sync` | Runs shell after sync (e.g. push review branch) |
| Summary comment | `tracker.update_comment` / `_append_comment` | Posts results to issue |
| Review feedback loop | `review_feedback.*` | F-37: scans PR comments & CI, plans follow-up |
| Operator intent | `agent:retry` / `agent:follow-up` / `agent:blocked` labels | F-39: reset, follow-up, or skip |

---

## Top-Level Config Anatomy

A `workflow.md` has two parts:

1. **YAML frontmatter** (between the `---` markers) — parsed by
   `extensions/orchestrator/config/schema.py` into a `WorkflowConfig`.
2. **Markdown body** — the agent prompt template. The orchestrator renders
   Jinja-like placeholders (`{{ issue.identifier }}`, `{{ issue.title }}`, etc.)
   once per issue.

```yaml
---
tracker:        # see Tracker Adapters
polling:        # { interval_ms: 30000 }
workspace:      # clone config + strategy
worker:         # optional remote workers (ssh)
agent:          # concurrency, provider, commands
codex:          # optional codex app-server integration
hooks:          # before/after/pre-commit/pre-push/post-sync
review_feedback: # F-37 PR review auto-fix
observability:  # dashboard
server:         # local HTTP API
---
# Orchestrator Agent Prompt (Jinja template)
You are working on issue {{ issue.identifier }} — {{ issue.title }}
...
```

Each top-level key is optional and has sensible defaults. The minimum
viable workflow needs at least `tracker.kind` and a usable
`agent.test_command` (or empty string to skip verification).

---

## Tracker Adapters

The orchestrator polls one of five tracker kinds. Each has a default
endpoint, default `active_states`, default `terminal_states`, and a set
of API-key / owner / repo environment variables it checks.

| `kind` | Backend | Required config | Default active states |
|--------|---------|-----------------|------------------------|
| `linear` | Linear GraphQL | `api_key`, `project_slug` | `["Todo", "In Progress"]` |
| `github` | REST v3 | `api_key`, `owner`, `repo` | `["open"]` |
| `gitee` | REST v5 | `api_key`, `owner`, `repo` | `["open"]` |
| `gitcode` | REST v5 | `api_key`, `owner`, `repo` | `["opened"]` |
| `local` | Filesystem (markdown + NDJSON) | `issues_path` | `["open", "ready"]` |

The `tracker` block also accepts `endpoint`, `clone_url`, `assignee`,
`branch_prefix`, and override lists for `active_states` / `terminal_states`.

### Minimal GitHub Example

```yaml
tracker:
  kind: github
  owner: myorg
  repo: myrepo
  api_key: $GITHUB_TOKEN         # $VAR → resolved at load time
  active_states: [open]
```

### Linear with Full Feature Flags

```yaml
tracker:
  kind: linear
  project_slug: $LINEAR_PROJECT_SLUG
  api_key: $LINEAR_API_KEY
  endpoint: https://api.linear.app/graphql
  assignee: $LINEAR_ASSIGNEE
  active_states: [Todo, "In Progress"]
  terminal_states: [Done, Cancelled, Duplicate, Closed]
```

### Gitee / GitCode (REST v5)

```yaml
tracker:
  kind: gitcode                   # or `gitee`
  owner: $GITCODE_OWNER
  repo: $GITCODE_REPO
  api_key: $GITCODE_TOKEN
  active_states: [opened]
```

### Local Markdown Tracker (filesystem)

```yaml
tracker:
  kind: local
  issues_path: $HOME/.clawcodex_issues/myrepo
  branch_prefix: feature
  active_states: [open, ready]
  terminal_states: [completed, closed, cancelled, failed, abandoned]
```

The `local` adapter expects one markdown file per issue in
`issues_path`, with YAML frontmatter (`id`, `identifier`, `title`,
`state`, `priority`, `labels`, `branch_name`, `base_branch`, …) and a
free-form body. See `extensions/orchestrator/issue-local.md` for the
template and the field semantics. Comments are stored as
`<safe_id>.comments.ndjson` next to the issue file.

Local tracker runs **skip the remote push** (`git_sync.no_push=True`)
and end in `PENDING_REVIEW` instead of `SYNCED`. The branch is left
in the workspace and (optionally) pushed to a review remote by
`hooks.post_sync`.

### Environment Variable Resolution

Any value starting with `$` is read from `os.environ` at load time:

```yaml
api_key: $GITHUB_TOKEN           # looks up GITHUB_TOKEN
owner:   $GITCODE_OWNER           # looks up GITCODE_OWNER
```

For tracker kinds, the schema also auto-falls-back to a list of
well-known env vars (e.g. GitHub checks `GITHUB_TOKEN` *or*
`GITHUB_API_KEY`).

---

## Workspace & Cloning

The orchestrator clones the target repository into `workspace.root`
and creates an isolated (or shared) worktree per issue.

```yaml
workspace:
  root: /tmp/clawcodex_workspaces           # default: $TMPDIR/symphony_workspaces
  repo_clone_url: https://github.com/myorg/myrepo.git
  clone_depth: 1                             # shallow clone (default)
  checkout_issue_branch: true                # create per-issue branch
  git_username: myorg
  git_token: $GIT_PUSH_TOKEN                 # used for `git push`
  gitignore_patterns:                        # paths excluded from agent
    - .event_logs
    - "*.pyc"
    - __pycache__
    - "*.egg-info"
    - .pytest_cache
    - .reports
  strategy: isolated                         # isolated | shared | sequential
  base_branch: main                          # override default branch detection
  integration_branch: dev                    # only meaningful for `sequential`
  require_clean_start: true                  # refuse to start if dirty
  require_clean_between_issues: true
  preserve_on_terminal: true                 # keep workspace after completion
  sequential_lock: true                      # single-writer for sequential
```

### Workspace strategies

- **`isolated`** (default) — one fresh worktree per issue.
- **`shared`** — multiple agents share a worktree (advanced).
- **`sequential`** — issues are run one at a time on a single
  integration branch (`integration_branch`); each issue lands as a
  commit on top of the previous. Requires
  `agent.max_concurrent_agents: 1` and
  `agent.max_concurrent_agents_by_state.*: 1`.

### Gitignore patterns

Patterns are matched against files in the workspace before
`get_file_status()` runs. The default set already covers
`.event_logs`, `.reports`, `*.pyc`, `__pycache__`, `*.egg-info`,
`.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `*.log`. Add more
patterns here if your repository generates build artifacts the
orchestrator should treat as inert.

### Hooks that run inside the workspace

The following hooks all execute with `cwd = workspace path`:

- `hooks.after_create` — after `git clone` finishes
- `hooks.before_run` — before the agent starts
- `hooks.after_run` — after the agent exits
- `hooks.pre_commit` — after `git add -A`, before `git commit`
- `hooks.pre_push` — after verification, before `git push`
- `hooks.post_sync` — after PR creation, before status update
- `hooks.before_remove` — before the workspace is deleted (terminal state)

---

## Hooks: before/after Run and Sync

Every hook is a string passed to a shell. Empty string = skip. The
hooks block:

```yaml
hooks:
  after_create: |
    uv sync --frozen
    echo "[orchestrator] workspace ready for $ISSUE_IDENTIFIER"
  before_run: |
    uv run pytest --co -q
  after_run: |
    uv run ruff check .
    uv run pytest --tb=short
  pre_commit: ""                              # skipped
  pre_push: ""                                # skipped
  post_sync: ""                               # skipped
  before_remove: ""                           # skipped
  timeout_ms: 120000                          # all hooks share this
```

### Available environment variables inside hooks

| Variable | Value |
|----------|-------|
| `WORKSPACE` | Absolute path to the workspace directory |
| `ISSUE_ID` | Numeric / opaque issue ID |
| `ISSUE_IDENTIFIER` | Human-readable identifier (e.g. `myrepo#123`) |
| `ISSUE_TITLE` | Issue title |
| `ISSUE_URL` | URL to the issue (empty for local tracker) |
| `ISSUE_BRANCH` | Branch name (from issue or auto-generated) |
| `BASE_BRANCH` | Base branch the workspace was forked from |
| `RUN_ID` | Stable run identifier (F-45) |

### Hook semantics in detail

- **`after_create`** runs once per workspace, after `git clone`. Use it
  for `uv sync` / `npm install` / `cargo fetch`.
- **`before_run`** runs every time the agent is about to start, even on
  retries. Use it to re-validate the test discovery.
- **`after_run`** runs after the agent returns, regardless of success or
  failure.
- **`pre_commit`** runs after `git add -A` and before `git commit`. If
  it modifies the working tree, the orchestrator auto-amends the commit
  (`git add -A && git commit --amend --no-edit`). Typical use:
  `black --check .` followed by `black .` in the same script.
- **`pre_push`** runs after the verification commands (see below) and
  before `git push`. The orchestrator snapshots the workspace before
  and after; if `pre_push` modified the tree, it raises
  `HookFailedError("pre_push hook modified the workspace")`.
- **`post_sync`** runs after PR creation / update. Same dirty-check
  applies. Typical use: `git push -u origin HEAD:review/$ISSUE_ID` to
  publish a review-only branch.
- **`before_remove`** runs when the workspace is about to be deleted
  (after a terminal state and `preserve_on_terminal` is false).

All hook failures raise typed exceptions:

- `VerificationFailed` for `test_command` / `build_command` /
  `lint_command` failures, **and for `pre_commit` hook failures**
  (the `pre_commit` shell invocation reuses `_run_shell` and shares
  its `VerificationFailed` exception type — a known deviation from
  the F-38 design doc, also flagged in `CLAUDE.md`).
- `HookFailedError(hook_name, …)` for `pre_push` / `post_sync` /
  `after_create` / `before_run` / `after_run` / `before_remove`
  failures (and for `pre_push` / `post_sync` when the hook modified
  the workspace, which the orchestrator detects via a status
  snapshot diff).

The orchestrator catches both and marks the issue as
`verification_failed` (for verification) or `failed` (for hooks).

---

## Agent Configuration

```yaml
agent:
  # Concurrency
  max_concurrent_agents: 5                     # global cap
  max_concurrent_agents_by_state:              # per-state caps
    open: 5
    ready: 3
    "In Progress": 2

  # Per-run knobs
  max_turns: 30                                # tool-call turns per run
  max_retry_attempts: 5                        # per issue before abandoned
  max_retry_backoff_ms: 300000                 # 5 min exponential cap
  max_turns_retry_delay_ms: 30000              # delay after max_turns

  # Provider
  provider: anthropic                          # anthropic | openai | ...
  permission_mode: dontAsk                     # auto-promoted to bypassPermissions

  # F-38 verification commands (see next section)
  test_command: ""                             # default empty = skip
  build_command: ""
  lint_command: ""
  verification:
    timeout_ms: 600000                         # 10 min per command

  # F-39 operator-driven retry guard-rails
  max_retries_per_issue: 3
  allow_anyone_to_retry: false

  # 429-aware in-turn backoff (see notes below)
  rate_limit_base_delay_ms: 30000
  rate_limit_max_backoff_ms: 600000
  rate_limit_exponential_factor: 2.0
  rate_limit_max_retries: 5
```

### `permission_mode` resolution

When a `workflow.md` has a `tracker` block (i.e. is being loaded for the
orchestrator), the schema **auto-promotes** `dontAsk` → `bypassPermissions`
because `dontAsk` still triggers `ApprovalPolicy` checks that can block
unattended headless runs. Explicit non-default values are preserved.

Supported values: `default`, `acceptEdits`, `dontAsk` (promoted),
`bypassPermissions`, `plan`, `auto`, `bubble`. The orchestrator always
runs in `bypassPermissions` in production.

### 429-aware in-turn backoff

When the LLM provider returns HTTP 429 inside a single `QueryRunner`
turn, the agent runner sleeps for an exponentially-growing delay
(`rate_limit_base_delay_ms` × `rate_limit_exponential_factor^N`,
capped at `rate_limit_max_backoff_ms`) and re-issues the prompt. After
`rate_limit_max_retries` consecutive 429s, the in-turn circuit breaker
opens (`status="rate_limit_circuit_open"`) and the run is handed back
to the orchestrator's inter-run retry queue.

These are **distinct** from `max_retry_backoff_ms` /
`max_turns_retry_delay_ms`, which govern *between-run* retries.

### Codex integration (optional)

```yaml
codex:
  command: codex app-server
  thread_sandbox: workspace-write
  turn_sandbox_policy:                          # auto-generated if omitted
    type: workspaceWrite
    writableRoots: [/tmp/clawcodex_workspaces]
    networkAccess: false
  turn_timeout_ms: 3600000                      # 1h per turn
  read_timeout_ms: 5000
  stall_timeout_ms: 300000                      # 5m idle abort
  approval_policy:
    reject:
      sandbox_approval: true
      rules: true
      mcp_elicitations: true
```

The `codex` block is only used if the upstream LLM provider is the
codex app-server. For Anthropic / OpenAI it can be omitted.

---

## Verification (F-38)

F-38 adds a **pre-push verification gate** that runs the configured
`test_command`, `build_command`, and `lint_command` (in that order)
before the orchestrator pushes the branch or opens a PR. The gate is
on by default — to skip a step, set the command to an empty string.

```yaml
agent:
  test_command: "python3 -m pytest tests/test_orchestrator_*.py -q"
  build_command: "uv build"
  lint_command: "uv run ruff check ."
  verification:
    timeout_ms: 600000
```

Behavior:

1. Each command runs in the workspace with the verification timeout.
2. If any exits non-zero, the orchestrator raises `VerificationFailed`
   and marks the issue `verification_failed`. **The commit is kept, the
   push is skipped, and the issue comment is updated.**
3. If a hook (`pre_push`) modified the working tree, the orchestrator
   raises `HookFailedError("pre_push hook modified the workspace")`.
4. Reports (`{workspace}/.reports/{run_id}.md`) are dual-written to
   `~/.clawcodex/reports/{run_id}.md` regardless of verification
   outcome.

A typical Python-project workflow:

```yaml
agent:
  test_command: "python3 -m pytest tests/ -x -q"
  build_command: ""
  lint_command: "uv run ruff check ."
  verification:
    timeout_ms: 600000
hooks:
  pre_commit: "uv run black --check ."
  pre_push: ""
  post_sync: ""
```

---

## Review Feedback Auto-Fix (F-37)

The orchestrator can poll **open pull requests for review feedback and
CI failures** and schedule a follow-up agent run to address them. This
is independent of the issue polling loop.

```yaml
review_feedback:
  enabled: true
  mode: manual                                 # manual | auto
  poll_interval_ms: 60000
  max_feedback_items_per_run: 20
  include_ci_failures: true
  reply_to_comments: true
  ignore_authors:                               # don't react to these users
    - dependabot
    - renovate-bot
  max_log_chars_per_check: 12000
  max_followup_attempts_per_pr: 5
```

The service uses `tracker.fetch_pull_request_feedback` /
`tracker.reply_to_pull_request_feedback`. Local tracker always returns
empty feedback (no real PR), so this section is a no-op there.

A follow-up run uses `git_sync.sync(mode="followup")`, which:
- reuses the existing `pull_request` from the issue registry,
- commits on the same branch with a `fix:` prefix (vs. `feat:` for new
  runs — see `_build_commit_message` in `git_sync.py:491`),
- re-renders the PR body via `_build_pr_body` (`git_sync.py:503-531`),
  which emits the standard "## ClawCodex Automated Change" block. (The
  `git_sync` docstring mentions a `## ClawCodex Follow-up #N` section
  for F-38 Sub-C, but the current `_build_pr_body` implementation does
  not yet emit it — the header is regenerated wholesale each run, so
  the body reflects the latest verification status / report path
  rather than an append-only history.)
- replies to each feedback item via `tracker.reply_to_pull_request_feedback`.

---

## Operator Intent System (F-39)

F-39 adds three operator intents that override the default
"issue-state-only" run flow. Two ways to set them:

### Labels (passive, no comment needed)

| Label | Intent | Behavior |
|-------|--------|----------|
| `agent:retry` | `RETRY` | Reset local registry entry, close remote PR, fresh run |
| `agent:follow-up` | `FOLLOWUP` | Keep PR, append a commit on the same branch |
| `agent:blocked` | `BLOCKED` | Permanently skip until the label is removed or `/agent unblock` is posted |

### Comment commands (explicit, require author/maintainer role)

```
/agent retry [reason]
/agent follow-up [note]
/agent unblock
```

`/agent unblock` clears an abandoned / blocked state and re-runs the
default poll-based check.

### Priority rules

- `BLOCKED` is sticky — only `/agent unblock` (or a CLI override) can lift it.
- `FOLLOWUP` beats `RETRY` when both labels are present (more conservative:
  keeps PR evidence).
- A comment command beats a label of lower precedence, but a label of
  higher precedence still wins.
- The `agent.max_retries_per_issue` cap protects against operator
  retry storms: after the cap, `agent:retry` is logged as an audit entry
  but no new run is launched.

### CLI fallback

```bash
# Retry with reset (close PR, fresh run)
clawcodex-dev issue retry --id 42 --mode reset

# Follow-up (keep PR, append commit)
clawcodex-dev issue retry --id 42 --mode follow-up

# Force override the per-issue retry cap
clawcodex-dev issue retry --id 42 --mode reset --force
```

`allow_anyone_to_retry: true` in `agent` disables the
"author-or-maintainer" role check (use for trusted-team scenarios).

---

## Three-Channel Clarification

When the agent encounters ambiguous semantics, the orchestrator
escalates through three channels before failing:

```
Channel 1: StatusDashboard interactive prompt   (30 min timeout)
        ↓
Channel 2: ClarificationQueue file              (30 min timeout)
        ↓
Channel 3: @mention issue comment               (72 h timeout)
        ↓
Escalation policy: skip | mark_failed | notify
```

The clarification config (used by the `ClarificationResolver`) is not
in `workflow.md` by default — it lives in the orchestrator subsystem.
The relevant defaults are:

| Setting | Default | Meaning |
|---------|---------|---------|
| `enabled` | `true` | Turn the resolver on |
| `timeout_local_seconds` | `1800` | 30 min for channels 1 & 2 |
| `timeout_author_seconds` | `259200` | 72 h for channel 3 |
| `max_questions_per_issue` | `3` | Hard cap on rounds |
| `confidence_threshold` | `0.7` | Below this, ask rather than guess |
| `operator_priority` | `true` | Operator answers beat author |
| `simultaneous_grace_ms` | `5000` | 5s window for "tied" answers |
| `escalation` | `skip` | What to do if all channels time out |

When the agent's prompt asks a clarification, the dashboard shows a
"Clarify" button, the `~/.clawcodex/clarification_queue/` directory
receives a pending JSON entry, and (as a last resort) a comment is
posted on the issue with an `@author` mention.

---

## Observability & Server

```yaml
observability:
  dashboard_enabled: true
  refresh_ms: 1000                              # how often the TUI redraws
  render_interval_ms: 16                        # 60 FPS target

server:
  host: 127.0.0.1
  port: 8765                                    # HTTP API for the dashboard
```

`dashboard_enabled: true` shows the in-process TUI dashboard when the
orchestrator runs in the foreground. The `server` block configures
the standalone LiveView HTTP server (used by `clawcodex orchestrator
dashboard --port 8765`).

---

## CLI Reference

### Server (daemon lifecycle)

```bash
clawcodex orchestrator server start \
    --workflow ./workflow.md \
    [--dashboard] \
    [--port 8765]

clawcodex orchestrator server status
clawcodex orchestrator server stop [--force] [--timeout 5.0]
```

All three are **idempotent** — `status` is a pure read, `stop` on an
already-stopped daemon exits 0, `start` on an already-running daemon
prints status and exits 0.

### Issue (per-issue operations)

```bash
# Query
clawcodex orchestrator issue list [--status pending|running|synced|completed|failed|abandoned]
clawcodex orchestrator issue show --id <id>
clawcodex orchestrator issue tail --id <id>          # stream tool-call events

# Lifecycle
clawcodex orchestrator issue stop --id <id>
clawcodex orchestrator issue pause --id <id> [--reason "..."]
clawcodex orchestrator issue resume --id <id>
clawcodex orchestrator issue takeover --id <id>      # stop agent, start REPL

# Operator interaction
clawcodex orchestrator issue clarify --id <id> --answer "..." [--forward-to-author]
clawcodex orchestrator issue inject --id <id> <hint> [--list] [--remove N]

# Workspace
clawcodex orchestrator issue workspace --id <id> [--ls] [--cat FILE] [--edit FILE --with CONTENT]
```

Issue-level commands use `--id` (self-describing) and are idempotent
where possible. `takeover` shells you into the workspace and starts
an interactive clawcodex REPL for manual intervention.

### Dashboard (standalone HTTP UI)

```bash
clawcodex orchestrator dashboard \
    --port 8080 \
    [--host 127.0.0.1] \
    [--workspace /path/to/workspace] \
    [--no-browser]
```

Streams real-time orchestrator events (running sessions, tool calls,
LLM responses) to a web UI. Reads from
`{workspace}/.clawcodex_issue_registry.json` and the per-run
`~/.clawcodex/tool-events/{run_id}/events.ndjson` files.

### Issue retry (F-39 CLI fallback)

```bash
clawcodex-dev issue retry --id <id> --mode reset|follow-up [--force]
```

Equivalent to posting the `agent:retry` / `agent:follow-up` label from
the CLI. Use `--force` to override the `max_retries_per_issue` cap.

---

## End-to-End Flow Example

Given a GitHub issue `#123: Add user authentication` in `myorg/myrepo`:

```
1. Orchestrator polls GitHub, finds issue #123 in "open" state
2. Creates workspace: /tmp/clawcodex_workspaces/<issue-id>/
3. Clones https://github.com/myorg/myrepo.git into workspace
4. Runs hooks.after_create: uv sync --frozen
5. Runs hooks.before_run: uv run pytest --co
6. Launches Claude Code agent
   → Agent reads issue, makes code changes, creates tests
7. Runs hooks.after_run: uv run ruff check . (informational)
8. git add -A → runs hooks.pre_commit (may auto-amend)
9. git commit -m "feat: #123 Add user authentication"
10. Runs agent.test_command: uv run pytest -x -q  (gates push)
11. Runs hooks.pre_push (if set)
12. git push origin clawcodex/issue-123-add-user-authentication
13. tracker.ensure_pull_request → creates PR
14. tracker.update_pull_request → fills body with status snapshot
15. Runs hooks.post_sync
16. Issue #123 receives a "ClawCodex Git Sync" comment with:
    - branch, commit SHA, PR URL
    - verification output (test/build/lint logs)
    - report path
```

If a concurrent force-push causes a conflict at step 12:
```
12a. git push → non-fast-forward error detected
12b. git fetch origin && git rebase origin/<branch>
     → if conflicts: workspace left in rebasing state
     → if clean rebase: retry git push
12c. Conflict files recorded in GitSyncResult.conflict_files
12d. Next agent run detects has_conflict=True → resolves
12e. Loop returns to step 6
```

---

## Conflict Recovery

When a concurrent force-push makes the local branch diverge from
origin, the `git_sync` service runs a built-in recovery loop:

1. `git push` fails with non-fast-forward.
2. `git fetch origin` updates the remote-tracking refs.
3. `git rebase origin/<branch>` is attempted.
4. **If the rebase is clean**, the push is retried.
5. **If the rebase has conflicts**, the workspace is left in a
   rebasing state, the conflicting files are recorded in
   `GitSyncResult.conflict_files`, and the next agent run is given a
   prompt that includes the conflict markers to resolve.

The `GitSyncResult` carries:

- `pushed: bool` — whether push succeeded
- `has_conflict: bool` — whether rebase left unresolved conflicts
- `conflict_files: tuple[str, ...]` — paths with conflict markers
- `pending_review: bool` — `True` for `LocalTracker` after a successful
  commit (the branch is on disk, no remote PR)
- `commit_sha: str | None` — final HEAD after commit (and amend)

For local tracker, `no_push=True` is the **design behavior** (not a
failure): the orchestrator records the branch in the issue registry
and stops. A human reviews the workspace before merging.

---

## Reports & Persistence

Every run produces two report artifacts:

| Path | Lifetime | Audience |
|------|----------|----------|
| `{workspace}/.reports/{run_id}.md` | Workspace lifetime | Agent, dashboard |
| `~/.clawcodex/reports/{run_id}.md` | Persistent | Audit, long-term history |

Both are dual-written by `report_writer.write()` (atomic via
`.tmp` + `os.replace`). `.reports` is in the default
`gitignore_patterns` so it does not show as "dirty" in the
`pre_commit` hook.

The orchestrator also persists:

- **`{workspace}/.clawcodex_issue_registry.json`** — every issue's
  status, branch, PR, attempt count, retry count, intent, command
  cursor, etc. (the `IssueRecord` schema in
  `extensions/orchestrator/issue_registry.py`).
- **`~/.clawcodex/tool-events/{run_id}/events.ndjson`** — every
  tool-call decision, with permission-mode column, auto-rotated at
  50 MB. F-45.
- **`~/.clawcodex/clarification_queue/*.json`** — pending
  clarification questions awaiting operator response.
- **`~/.clawcodex/orchestrator/<slug>/metadata.json`** — daemon
  metadata (PID, started_at, workspace_root, workflow_path). Used by
  `server status` / `server stop` to locate the running process.

The `IssueStatus` lifecycle:

```
QUEUED → PENDING → RUNNING → SYNCED
                          ↘ PENDING_REVIEW  (LocalTracker only)
                          ↘ COMPLETED
                          ↘ FAILED
                          ↘ VERIFICATION_FAILED
                          ↘ ABANDONED
```

`COMPLETED`, `FAILED`, `ABANDONED`, `VERIFICATION_FAILED` are terminal
(`TERMINAL_STATUSES`).

---

## Complete Key Reference

Every key with its default, type, and behavior. All paths are resolved
relative to the workspace unless noted.

### `tracker.*`

| Field | Default | Description |
|-------|---------|-------------|
| `kind` | `linear` | `linear` / `github` / `gitee` / `gitcode` / `local` |
| `endpoint` | per-kind | API base URL |
| `api_key` | `$ENV` | API token; `$VAR` resolves from env |
| `project_slug` | none | Linear project (`$LINEAR_PROJECT_SLUG`) |
| `owner` | none | Repository owner (GitHub/Gitee/GitCode) |
| `repo` | none | Repository name (GitHub/Gitee/GitCode) |
| `clone_url` | derived | Override the auto-derived clone URL |
| `assignee` | none | Filter to issues assigned to this user |
| `branch_prefix` | none | Prefix for auto-generated branches |
| `issues_path` | none | Local-tracker markdown directory |
| `active_states` | per-kind | Issue states that trigger a run |
| `terminal_states` | per-kind | Issue states that skip polling |

### `polling.*`

| Field | Default | Description |
|-------|---------|-------------|
| `interval_ms` | `30000` | Polling cadence |

### `workspace.*`

| Field | Default | Description |
|-------|---------|-------------|
| `root` | `$TMPDIR/symphony_workspaces` | Parent for per-issue worktrees |
| `hooks` | `{}` | Reserved (per-workspace hook dict) |
| `repo_clone_url` | none | Target repository URL |
| `clone_depth` | `1` | Shallow clone depth |
| `checkout_issue_branch` | `true` | Create per-issue branch |
| `git_username` | none | Used for `git push` |
| `git_token` | none | Used for `git push` |
| `gitignore_patterns` | see [Workspace & Cloning](#workspace--cloning) | Files excluded from dirty-check |
| `strategy` | `isolated` | `isolated` / `shared` / `sequential` |
| `base_branch` | none | Override the default branch |
| `integration_branch` | none | Sequential strategy integration branch |
| `require_clean_start` | `true` | Refuse to start in dirty workspace |
| `require_clean_between_issues` | `true` | Clean between sequential issues |
| `preserve_on_terminal` | `true` | Keep workspace after terminal state |
| `sequential_lock` | `true` | Single-writer for sequential strategy |

### `worker.*`

| Field | Default | Description |
|-------|---------|-------------|
| `ssh_hosts` | `[]` | Optional remote worker pool |
| `max_concurrent_agents_per_host` | none | Per-host concurrency cap |

### `agent.*`

| Field | Default | Description |
|-------|---------|-------------|
| `max_concurrent_agents` | `10` | Global concurrency cap |
| `max_concurrent_agents_by_state` | `{}` | Per-state caps |
| `max_turns` | `20` | Max tool-call turns per run |
| `max_retry_attempts` | `5` | Per-issue retry attempts |
| `max_retry_backoff_ms` | `300000` | Exponential backoff cap (5 min) |
| `max_turns_retry_delay_ms` | `30000` | Delay after `max_turns` |
| `provider` | `anthropic` | LLM provider |
| `permission_mode` | `dontAsk` (promoted to `bypassPermissions`) | Permission mode |
| `test_command` | `""` | Pre-push verification (F-38) |
| `build_command` | `""` | Pre-push verification (F-38) |
| `lint_command` | `""` | Pre-push verification (F-38) |
| `verification.timeout_ms` | `600000` | Per-command timeout |
| `max_retries_per_issue` | `3` | F-39 Sub-F: cap on operator retries |
| `allow_anyone_to_retry` | `false` | F-39 Sub-F: disable role check |
| `rate_limit_base_delay_ms` | `30000` | 429 in-turn backoff base |
| `rate_limit_max_backoff_ms` | `600000` | 429 in-turn backoff cap |
| `rate_limit_exponential_factor` | `2.0` | 429 backoff multiplier |
| `rate_limit_max_retries` | `5` | 429 circuit-breaker threshold |

### `codex.*`

| Field | Default | Description |
|-------|---------|-------------|
| `command` | `codex app-server` | Codex executable |
| `approval_policy` | `{"reject": {"sandbox_approval": true, "rules": true, "mcp_elicitations": true}}` | Sandbox / rules / MCP elicitation policy |
| `thread_sandbox` | `workspace-write` | Codex thread sandbox mode |
| `turn_sandbox_policy` | auto-generated | Per-turn sandbox policy |
| `turn_timeout_ms` | `3600000` | 1 h per turn |
| `read_timeout_ms` | `5000` | Stdin read timeout |
| `stall_timeout_ms` | `300000` | 5 min idle abort |

### `hooks.*`

| Field | Default | Description |
|-------|---------|-------------|
| `after_create` | none | Shell after `git clone` |
| `before_run` | none | Shell before agent starts |
| `after_run` | none | Shell after agent exits |
| `before_remove` | none | Shell before workspace removal |
| `pre_commit` | none | Shell between `git add` and `git commit` (auto-amends on change) |
| `pre_push` | none | Shell between verification and `git push` (dirty-check) |
| `post_sync` | none | Shell after PR creation (dirty-check) |
| `timeout_ms` | `60000` | Per-hook timeout |

### `review_feedback.*`

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Turn F-37 on/off |
| `mode` | `manual` | `manual` / `auto` |
| `poll_interval_ms` | `60000` | Polling cadence |
| `max_feedback_items_per_run` | `20` | Cap on items per follow-up |
| `include_ci_failures` | `true` | Treat CI failures as feedback |
| `reply_to_comments` | `true` | Post replies via `tracker.reply_to_pull_request_feedback` |
| `ignore_authors` | `[]` | Skip these comment authors |
| `max_log_chars_per_check` | `12000` | Truncate log excerpts |
| `max_followup_attempts_per_pr` | `5` | Hard cap on follow-ups per PR |

### `observability.*`

| Field | Default | Description |
|-------|---------|-------------|
| `dashboard_enabled` | `true` | Show in-process TUI dashboard |
| `refresh_ms` | `1000` | TUI redraw interval |
| `render_interval_ms` | `16` | Render target frame interval (60 FPS) |

### `server.*`

| Field | Default | Description |
|-------|---------|-------------|
| `host` | `127.0.0.1` | HTTP bind host |
| `port` | none | HTTP bind port (required to expose server) |

---

## Starting the Orchestrator

```bash
# Foreground with the in-process TUI dashboard
clawcodex orchestrator --workflow ./workflow.md

# Daemonized
clawcodex orchestrator server start --workflow ./workflow.md
clawcodex orchestrator server status
clawcodex orchestrator server stop

# Standalone LiveView dashboard (separate HTTP server)
clawcodex orchestrator dashboard --port 8080
```

The daemon writes its PID and metadata to
`~/.clawcodex/orchestrator/<slug>/metadata.json`. The slug is derived
from the last 3 path segments of the workspace root.

---

## See also

- `extensions/orchestrator/templates/workflow.template.md` — the
  authoritative placeholder template with per-field comments.
- `extensions/orchestrator/workflow-local.md` — the local-tracker
  template with the F-38 verification trio and F-39 retry knobs.
- `extensions/orchestrator/issue-local.md` — local-tracker issue card
  template.
- `docs/FEATURE_PLAN.md` (F-37, F-38, F-39) — design docs.
- `docs/PROGRESS.md` — per-feature status and acceptance criteria.
