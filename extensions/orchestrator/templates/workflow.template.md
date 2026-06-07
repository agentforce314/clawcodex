# ============================================================================
# Orchestrator Workflow Configuration — TEMPLATE
# ============================================================================
#
# Usage:
#   1. Copy this file to your project root as `workflow.md`
#   2. Replace every {{PLACEHOLDER}} with your actual values
#   3. Customize the Agent Prompt body (the markdown after the second `---`)
#   4. Set the required environment variables (see "Required env vars" below)
#
# Frontmatter (between the two `---` markers) is parsed as YAML by
# extensions/orchestrator/config/schema.py. The body is the agent prompt
# template rendered with Jinja-like syntax for each issue.
#
# Required env vars (depending on tracker.kind):
#   - linear     : LINEAR_API_KEY
#   - github     : GITHUB_TOKEN (or GITHUB_API_KEY)
#   - gitee      : GITEE_TOKEN (or GITEE_API_KEY)
#   - gitcode    : GITCODE_TOKEN (or GITCODE_API_KEY)
#   - local      : (none; uses tracker.issues_path)
# ============================================================================

---
# ============================================================================
# Tracker — Issue source platform
# ============================================================================
# Picks which issue tracker the orchestrator polls. One of:
#   linear, github, gitee, gitcode, local
tracker:
  kind: {{TRACKER_KIND}}                                  # e.g. gitcode
  # API base URL. Leave blank to use the platform's default endpoint.
  endpoint: {{TRACKER_ENDPOINT}}                          # e.g. https://api.gitcode.com/api/v5
  # --- Repository-backed trackers (github / gitee / gitcode) ---
  owner: {{REPO_OWNER}}                                   # e.g. chadwweng
  repo: {{REPO_NAME}}                                     # e.g. AgentSDK
  clone_url: {{REPO_CLONE_URL}}                           # e.g. https://gitcode.com/{{REPO_OWNER}}/{{REPO_NAME}}.git
  api_key: ${{TRACKER_API_KEY_ENV}}                       # $VAR form reads from env at load time
  # Only poll issues assigned to this user/login. Leave blank to track all.
  assignee: {{REPO_ASSIGNEE}}
  # Branch prefix the orchestrator uses when checking out issue branches.
  branch_prefix: {{BRANCH_PREFIX}}                        # e.g. clawcodex
  # --- Linear-only (ignored by other trackers) ---
  # project_slug: my-team/my-project
  # --- Local-only (ignored by repository trackers) ---
  # issues_path: .clawcodex_local_issues
  # --- State filter (works for all repository-backed trackers) ---
  # GitHub / Gitee / GitCode all use state=open|closed|all. The defaults
  # are returned by tracker.default_active_states_for_kind() — override here
  # only if your repo uses custom state names.
  active_states:
    - open                                                 # all repo trackers (github/gitee/gitcode)
  terminal_states:
    - closed

# ============================================================================
# Polling — how often to fetch issue lists
# ============================================================================
polling:
  interval_ms: 60000                                       # 60s

# ============================================================================
# Workspace — local clone and per-issue checkout
# ============================================================================
workspace:
  root: {{WORKSPACE_ROOT}}                                 # e.g. /tmp/symphony_workspaces/myrepo
  repo_clone_url: {{REPO_CLONE_URL}}                       # clone URL passed to `git clone`
  clone_depth: 1                                           # shallow clone for speed
  checkout_issue_branch: true                              # create per-issue branch from main
  git_username: {{GIT_PUSH_USER}}                         # used for `git push`
  git_token: ${{GIT_PUSH_TOKEN_ENV}}                       # $VAR form reads from env at load time
  gitignore_patterns:                                      # paths excluded from agent workspace
    - "*.pyc"
    - __pycache__
    - "*.egg-info"
    - .pytest_cache

# ============================================================================
# Agent — Claude invocation parameters
# ============================================================================
agent:
  max_concurrent_agents: 1                                 # global concurrency cap
  max_concurrent_agents_by_state:                          # per-state cap (keys = active state names)
    open: 3
  max_turns: 200                                            # tool-call turns per issue run
  max_retry_attempts: 6
  max_retry_backoff_ms: 300000                             # 5 min backoff between retries
  provider: anthropic                                      # anthropic | openai | ...
  # permission_mode MUST be bypassPermissions for unattended orchestrator runs.
  # - dontAsk still triggers ApprovalPolicy checks → can block headless runs
  # - bypassPermissions → headless sets permission_handler=None, all tools auto-approved
  # schema.py auto-promotes dontAsk → bypassPermissions when tracker is present.
  permission_mode: bypassPermissions

# ============================================================================
# Codex — optional codex integration config
# ============================================================================
codex:
  thread_sandbox: workspace-write
  turn_timeout_ms: 3600000                                 # 1h per turn
  read_timeout_ms: 5000
  stall_timeout_ms: 300000                                 # 5m idle abort
  approval_policy:
    reject:
      sandbox_approval: true
      rules: true
      mcp_elicitations: true

# ============================================================================
# Hooks — shell commands run around each issue run
# ============================================================================
hooks:
  before_run: echo "[orchestrator] starting work on issue $ISSUE_IDENTIFIER"
  after_run: echo "[orchestrator] finished issue $ISSUE_IDENTIFIER"
  timeout_ms: 60000

# ============================================================================
# Observability — TUI dashboard
# ============================================================================
observability:
  dashboard_enabled: true
  refresh_ms: 1000
  render_interval_ms: 16

# ============================================================================
# Server — local HTTP API
# ============================================================================
server:
  host: 127.0.0.1
  port: 8765
---

# Orchestrator Agent Prompt

You are an autonomous development agent working on an issue from the
**{{REPO_NAME}}** repository ({{REPO_URL}}). The issue has already been
assigned to you.

**Current state (do NOT repeat these steps):**
- The issue description below has been fetched from the issue tracker API.
- The repository has been cloned (depth=1) into your workspace.
- A working branch `{{BRANCH_PREFIX}}/<issue-id>` has been checked out from the
  base branch (default: `main`).

**Your task:**
1. Read and understand the issue description, labels, and any comments.
2. Explore the {{REPO_NAME}} codebase as needed (Read, Grep, Glob, Bash).
3. Implement the required changes against the base branch baseline.
4. Run the existing test suite (and add new tests if behavior is non-trivial).
5. Commit your changes with a descriptive message
   (`feat: ...` / `fix: ...` / `refactor: ...`).
6. Push the branch and open a pull request back to the base branch.

**Coding conventions:**
- Follow the existing module structure of {{REPO_NAME}}.
- Keep changes focused on the issue; do not bundle unrelated cleanups.
- Add or update tests alongside any behavioral change.
- Use Conventional Commits for the commit message.

**Issue:** {{ issue.identifier }} - {{ issue.title }}
{% if issue.description %}
**Description:**
{{ issue.description }}
{% endif %}
{% if issue.labels %}
**Labels:** {{ issue.labels | join(", ") }}
{% endif %}

Work directly in the workspace. Make the necessary code changes, commit, and
push — the orchestrator will then create the pull request.
