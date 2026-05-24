# ClawCodex Workflow Configuration Guide

This guide shows how to configure `WORKFLOW.md` to fully wire up the autonomous issue→PR pipeline.

## Pipeline Overview

```
issue polling → clone → before_run → agent → after_run → git sync → PR → issue comment
                     ↓                                              ↓
              (your install/                          (conflict recovery:
               bootstrap/setup)                        rebase → agent fix)
```

| Stage | Responsible Config | What Happens |
|-------|-------------------|--------------|
| Issue polling | `tracker.*` | Fetches issues from Linear/GitHub/Gitee/GitCode |
| Repository clone | `workspace.repo_clone_url` | Clones repo into local workspace |
| before_run hook | `hooks.before_run` | Runs shell command in workspace (e.g. `uv sync`) |
| Agent development | `agent.*` + `codex.*` | Claude Code develops the fix |
| after_run hook | `hooks.after_run` | Runs shell command after agent (e.g. test summary) |
| git sync | `orchestrator` auto | commit → push → ensure PR → comment issue |
| Conflict recovery | `git_sync.py` auto | Detects non-fast-forward, rebases, marks conflict files |

---

## Minimal GitHub Example

```yaml
# WORKFLOW.md
tracker:
  kind: github
  owner: myorg
  repo: myrepo
  api_key: $GITHUB_TOKEN
  active_states:
    - open

workspace:
  root: /tmp/clawcodex_workspaces
  repo_clone_url: https://github.com/myorg/myrepo.git
  checkout_issue_branch: true

hooks:
  before_run: uv sync && uv run pytest --co
  after_run: uv run pytest --tb=short 2>&1 | tail -20
  timeout_ms: 120000

agent:
  max_concurrent_agents: 5
  max_turns: 30

codex:
  command: codex app-server
```

---

## Python Project with uv (before_run does install)

```yaml
# WORKFLOW.md
tracker:
  kind: github
  owner: myorg
  repo: myrepo
  api_key: $GITHUB_TOKEN
  active_states:
    - open

workspace:
  repo_clone_url: https://github.com/myorg/myrepo.git
  checkout_issue_branch: true

hooks:
  # Install dependencies and verify test collection before agent runs
  before_run: |
    uv sync --frozen
    uv run pytest --co -q
  # After agent, run full test suite and format check
  after_run: |
    uv run ruff check .
    uv run pytest --tb=short

agent:
  max_concurrent_agents: 3
  max_turns: 50

codex:
  command: codex app-server
```

**What happens:**
1. Orchestrator clones repo into `/tmp/clawcodex_workspaces/<issue-id>/`
2. `before_run: uv sync && uv run pytest --co` installs deps and validates test discovery
3. If `before_run` fails, agent does not start — issue goes to retry queue
4. Agent edits code
5. `after_run: uv run ruff check . && uv run pytest --tb=short` runs linter + tests
6. git sync: commits changes, pushes to `clawcodex/<issue-id>` branch, ensures PR
7. PR link + commit SHA written to issue comment

---

## Node.js Project with npm (before_run does install)

```yaml
# WORKFLOW.md
tracker:
  kind: github
  owner: myorg
  repo: myrepo
  api_key: $GITHUB_TOKEN
  active_states:
    - open

workspace:
  repo_clone_url: https://github.com/myorg/myrepo.git
  checkout_issue_branch: true

hooks:
  before_run: npm install && npm run test:ci -- --dry-run
  after_run: npm run test:ci

agent:
  max_concurrent_agents: 3
  max_turns: 40

codex:
  command: codex app-server
```

---

## Linear Tracker with Full Feature Flags

```yaml
# WORKFLOW.md
tracker:
  kind: linear
  project_slug: $LINEAR_PROJECT_SLUG
  api_key: $LINEAR_API_KEY
  endpoint: https://api.linear.app/graphql

workspace:
  repo_clone_url: https://github.com/myorg/myrepo.git
  checkout_issue_branch: true

hooks:
  before_run: |
    uv sync --frozen
    echo "Repository initialized for issue $ISSUE_IDENTIFIER"
  after_run: |
    uv run pytest --tb=short
    uv run coverage report --fail-under=80

agent:
  max_concurrent_agents: 5
  max_turns: 30

codex:
  command: codex app-server
  turn_timeout_ms: 3600000
```

---

## Gitee Tracker

```yaml
# WORKFLOW.md
tracker:
  kind: gitee
  owner: $GITEE_OWNER
  repo: $GITEE_REPO
  api_key: $GITEE_TOKEN
  active_states:
    - open

workspace:
  repo_clone_url: https://gitee.com/$GITEE_OWNER/$GITEE_REPO.git
  checkout_issue_branch: true

hooks:
  before_run: |
    uv sync --frozen
  after_run: uv run pytest --tb=short

agent:
  max_concurrent_agents: 3

codex:
  command: codex app-server
```

---

## GitCode Tracker

```yaml
# WORKFLOW.md
tracker:
  kind: gitcode
  owner: $GITCODE_OWNER
  repo: $GITCODE_REPO
  api_key: $GITCODE_TOKEN
  active_states:
    - opened

workspace:
  repo_clone_url: https://gitcode.com/$GITCODE_OWNER/$GITCODE_REPO.git
  checkout_issue_branch: true

hooks:
  before_run: uv sync --frozen
  after_run: uv run pytest --tb=short

agent:
  max_concurrent_agents: 3

codex:
  command: codex app-server
```

---

## Hook Environment Variables

The following environment variables are available inside `before_run` and `after_run` shell commands:

| Variable | Value |
|----------|-------|
| `WORKSPACE` | Absolute path to the workspace directory |
| `ISSUE_ID` | Numeric issue ID |
| `ISSUE_IDENTIFIER` | Human-readable identifier (e.g. `myrepo#123`) |
| `ISSUE_TITLE` | Issue title |
| `ISSUE_URL` | URL to the issue |
| `ISSUE_BRANCH` | Branch name (from `branch_name` field or auto-generated) |

The hook runs with `cwd` set to the workspace root.

---

## Conflict Recovery Flow

When a concurrent force-push makes the local branch diverge from origin:

```
1. git push → non-fast-forward error detected
2. git fetch origin
3. git rebase origin/<branch>
   → if conflicts: workspace left in rebasing state, conflict files recorded
   → if clean rebase: retry git push
4. If conflicts existed → next agent run detects has_conflict=True
   → agent resolves conflict markers
   → after_run hook runs again
   → git sync retries push
```

The `GitSyncResult` carries:
- `pushed`: whether push succeeded
- `has_conflict`: whether rebase left unresolved conflicts
- `conflict_files`: tuple of file paths with conflict markers

---

## Starting the Orchestrator

```bash
# With WORKFLOW.md in current directory
clawcodex orchestrator

# With explicit workflow file
clawcodex orchestrator --workflow /path/to/WORKFLOW.md

# Dry run (poll only, no agent execution)
clawcodex orchestrator --dry-run

# With debug logging
RUST_LOG=debug clawcodex orchestrator
```

---

## End-to-End Flow Example

Given a GitHub issue `#123: Add user authentication` in `myorg/myrepo`:

```
1. Orchestrator polls GitHub, finds issue #123 in "open" state
2. Creates workspace: /tmp/clawcodex_workspaces/123/
3. Clones https://github.com/myorg/myrepo.git into workspace
4. Runs before_run hook: uv sync --frozen (succeeds)
5. Launches Claude Code agent in workspace
   → Agent reads issue, makes code changes, creates tests
6. Runs after_run hook: uv run pytest --tb=short (passes)
7. Git sync:
   - Branch: clawcodex/issue-123-add-user-authentication (created)
   - Commit: "feat: #123 Add user authentication"
   - Push: origin/clawcodex/issue-123-add-user-authentication
   - PR: created via GitHub API
8. Issue #123 receives comment:
   ## ClawCodex Git Sync
   - Branch: `clawcodex/issue-123-add-user-authentication`
   - Committed: yes
   - Pushed: yes
   - Commit: `abc1234`
   - Pull request: https://github.com/myorg/myrepo/pull/456
```

If a concurrent force-push causes a conflict during step 7:
```
7. Git sync detects non-fast-forward → rebases → finds conflicts in auth.py
8. Returns has_conflict=True, conflict_files=('auth.py',)
9. Issue receives comment reporting conflict state
10. Next agent run detects conflict files → resolves them
11. after_run runs again → tests pass → git sync retries push → PR updated
```

---

## Key Configuration Reference

| Field | Default | Description |
|-------|---------|-------------|
| `tracker.kind` | `linear` | `linear`, `github`, `gitee`, `gitcode` |
| `tracker.active_states` | varies by platform | Issue states that trigger agent runs |
| `workspace.root` | `/tmp/symphony_workspaces` | Parent directory for all workspaces |
| `workspace.checkout_issue_branch` | `true` | Whether to create issue-named branches |
| `hooks.before_run` | none | Shell command before agent starts |
| `hooks.after_run` | none | Shell command after agent completes |
| `hooks.timeout_ms` | `60000` | Max time for hook execution |
| `agent.max_concurrent_agents` | `10` | Max parallel agent sessions |
| `agent.max_turns` | `20` | Max turns per agent session |
| `agent.max_retry_backoff_ms` | `300000` | Max retry delay (exponential backoff) |
| `agent.max_retry_attempts` | `5` | Max retry attempts before giving up on an issue |
| `codex.turn_timeout_ms` | `3600000` | Agent turn timeout |