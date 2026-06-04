---
# ---------------------------------------------------------------------------
# Tracker: GitCode (https://gitcode.com/chadwweng/AgentSDK) master branch
# ---------------------------------------------------------------------------
tracker:
  kind: gitcode
  endpoint: https://api.gitcode.com/api/v5
  owner: chadwweng
  repo: AgentSDK
  clone_url: https://gitcode.com/chadwweng/AgentSDK.git
  # GitCode personal access token. 优先读取 $GITCODE_TOKEN / $GITCODE_API_KEY;
  # 若必须内联,使用 $GITCODE_TOKEN 形式让 schema 在加载时从 env 解析。
  api_key: WV9arqgZdsAPdL3TQYgASrrR
  # 只跟踪 master 分支上被指派给本账号、且处于打开状态的 issue
  assignee: chadwweng
  branch_prefix: clawcodex
  # GitCode uses "open" state for open issues
  active_states:
    - open
  terminal_states:
    - closed

# ---------------------------------------------------------------------------
# Polling: 每 60 秒拉取一次 issues 列表
# ---------------------------------------------------------------------------
polling:
  interval_ms: 60000

# ---------------------------------------------------------------------------
# Workspace: 克隆 AgentSDK 仓库到本地,基于 master 拉分支处理 issue
# ---------------------------------------------------------------------------
workspace:
  root: /tmp/symphony_workspaces/agentsdk
  repo_clone_url: https://gitcode.com/chadwweng/AgentSDK.git
  clone_depth: 1
  checkout_issue_branch: true
  git_username: chadwweng
  git_token: WV9arqgZdsAPdL3TQYgASrrR
  gitignore_patterns:
    - .event_logs
    - "*.pyc"
    - __pycache__
    - "*.egg-info"
    - .pytest_cache

# ---------------------------------------------------------------------------
# Agent / Codex: 调度 ClawCodex 处理 issue
# ---------------------------------------------------------------------------
agent:
  max_concurrent_agents: 3
  max_concurrent_agents_by_state:
    open: 3
  max_turns: 20
  max_retry_attempts: 3
  max_retry_backoff_ms: 300000
  provider: anthropic
  # Orchestrator 模式必须使用 bypassPermissions:
  #   - dontAsk 仍可能触发 ApprovalPolicy,导致 headless 下工具调用被拒绝
  #   - bypassPermissions 会让 headless 把 permission_handler 设为 None,所有工具调用自动批准
  # 即使不显式写此值,schema.py 也会在检测到 tracker 配置时自动从 dontAsk 升级到 bypassPermissions。
  permission_mode: bypassPermissions

codex:
  thread_sandbox: workspace-write
  turn_timeout_ms: 3600000
  read_timeout_ms: 5000
  stall_timeout_ms: 300000
  approval_policy:
    reject:
      sandbox_approval: true
      rules: true
      mcp_elicitations: true

# ---------------------------------------------------------------------------
# Hooks: 每次 run 前后做日志/通知
# ---------------------------------------------------------------------------
hooks:
  before_run: echo "[orchestrator] starting work on issue $ISSUE_IDENTIFIER"
  after_run: echo "[orchestrator] finished issue $ISSUE_IDENTIFIER"
  timeout_ms: 60000

# ---------------------------------------------------------------------------
# Observability: 启用 TUI dashboard
# ---------------------------------------------------------------------------
observability:
  dashboard_enabled: true
  refresh_ms: 1000
  render_interval_ms: 16

# ---------------------------------------------------------------------------
# Server: 本地 HTTP API / dashboard
# ---------------------------------------------------------------------------
server:
  host: 127.0.0.1
  port: 8765
---

# Orchestrator Agent Prompt

You are an autonomous development agent working on a GitCode issue from the
**AgentSDK** repository (https://gitcode.com/chadwweng/AgentSDK). The issue has
already been assigned to you.

**Current state (do NOT repeat these steps):**
- The issue description below has been fetched from the GitCode REST API
- The repository has been cloned (depth=1) into your workspace
- A working branch `clawcodex/<issue-id>` has been checked out from `master`

**Your task:**
1. Read and understand the issue description, labels, and any comments.
2. Explore the AgentSDK codebase as needed (Read, Grep, Glob, Bash).
3. Implement the required changes against the `master` branch baseline.
4. Run the existing test suite (and add new tests if behavior is non-trivial).
5. Commit your changes with a descriptive message
   (`feat: ...` / `fix: ...` / `refactor: ...`).
6. Push the branch and open a pull request back to `master` on GitCode.

**Coding conventions for AgentSDK:**
- Follow existing module structure under `extensions/orchestrator/` and `src/`.
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
push — the orchestrator will then create the pull request on GitCode.
