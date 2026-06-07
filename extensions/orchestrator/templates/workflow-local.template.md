---
# =============================================================================
# ClawCodex Orchestrator — Local-Tracker Workflow Template
# =============================================================================
# 本模板用于「本地文件 issue 驱动」的开发场景：
#   - tracker.kind = local
#   - issues 存为 <issues_path>/*.md，frontmatter + body
#   - workspace.repo_clone_url 指向目标仓库（orchestrator 会 fresh clone）
#   - 本地 tracker 触发 no_push，commit 只在 workspace 留下分支
#   - 人工检视后从 workspace 拉分支合入目标分支
#
# 启动方式：
#   clawcodex-dev orchestrator server start --workflow ./workflow-local.md
#
# 占位符（启动前替换或改成 env 引用）：
#   <OWNER>         仓库 owner
#   <REPO>          仓库名
#   <REPO_URL>      仓库 clone URL（https / ssh / file 均可）
#   <BRANCH_PREFIX> issue 分支前缀，例如 feature / fix
#   <REVIEW_REMOTE> post_sync 推 review 分支用的 remote 名（默认 origin）
#   <REVIEW_PREFIX> review 分支前缀，默认 review
# =============================================================================

tracker:
  kind: local
  issues_path: <ISSUES_PATH>            # 例如 $HOME/projects/<REPO>/.issues
  assignee: <OWNER>                     # 仅作记录用，不参与轮询过滤
  branch_prefix: <BRANCH_PREFIX>        # 生成的分支形如 <BRANCH_PREFIX>/<id>-<slug>
  active_states:
    - open
    - ready
  terminal_states:
    - completed
    - closed
    - cancelled
    - failed
    - abandoned

# -----------------------------------------------------------------------------
# Polling: 每 30 秒扫一次 issues 目录
# -----------------------------------------------------------------------------
polling:
  interval_ms: 30000

# -----------------------------------------------------------------------------
# Workspace: 把目标仓库 fresh clone 到本地，issue 分支在 clone 里提交
# -----------------------------------------------------------------------------
# 注意：workspace 是独立 working tree，不是你当前的项目目录。
# 分支、commit 全部落在这个 clone 的 .git/。
# 检视后用 git push / cherry-pick / pull 把改动搬回主项目。
workspace:
  root: <WORKSPACE_ROOT>                # 例如 $HOME/.cache/clawcodex-workspaces
  repo_clone_url: <REPO_URL>            # 例如 https://gitcode.com/<OWNER>/<REPO>.git
  clone_depth: 1
  checkout_issue_branch: true
  git_username: <OWNER>
  git_token: $GITCODE_TOKEN             # 走环境变量，避免明文
  gitignore_patterns:
    - .reports
    - "*.pyc"
    - __pycache__
    - "*.egg-info"
    - .pytest_cache
    - .mypy_cache
    - .ruff_cache
    - "*.log"
    - ".issues/*.comments.ndjson"

# -----------------------------------------------------------------------------
# Agent: ClawCodex / Codex 的执行参数
# -----------------------------------------------------------------------------
agent:
  max_concurrent_agents: 1              # LocalTracker 共享 ProgressReporter，并发建议 1
  max_turns: 200
  max_retry_attempts: 6
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    open: 1
    ready: 1
  provider: anthropic
  # 留空会让 schema.py 自动从 dontAsk 升级为 bypassPermissions（headless 安全）
  permission_mode: bypassPermissions
  # F-38 验证三件套。空字符串 = 跳过该步
  test_command: "<TEST_COMMAND>"        # 例如 "python3 -m pytest tests/test_orchestrator_*.py -q"
  build_command: ""
  lint_command: ""
  verification:
    timeout_ms: 600000
  # F-39 Sub-F: 单 issue 最多自动重试次数（超过后 agent:retry 也不再生效）
  max_retries_per_issue: 3
  allow_anyone_to_retry: false

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

# -----------------------------------------------------------------------------
# Hooks
# -----------------------------------------------------------------------------
# - before_run / after_run：开始 / 结束时的轻量通知
# - pre_commit / pre_push / post_sync：F-38 同步钩子；空字符串 = 跳过
# - post_sync 这里演示把分支推到 <REVIEW_REMOTE> 的 review/<id>，
#   供你 fetch 后检视合入。push 不改 working tree，post_sync dirty check 不会触发。
#   如果不想推远程，把 post_sync 留空，分支只在 workspace 目录里。
hooks:
  before_run: "echo '[orchestrator] starting $ISSUE_IDENTIFIER'"
  after_run:  "echo '[orchestrator] finished $ISSUE_IDENTIFIER'"
  pre_commit: ""
  pre_push: ""
  post_sync: "git push -u <REVIEW_REMOTE> HEAD:<REVIEW_PREFIX>/$ISSUE_IDENTIFIER"
  timeout_ms: 120000

# -----------------------------------------------------------------------------
# Review feedback (F-37)：从 PR review 拉评论触发 follow-up
# 本地场景下没有真实 PR，关闭即可；要开启就把 enabled 设 true 并配 mode
# -----------------------------------------------------------------------------
review_feedback:
  enabled: false
  mode: manual
  poll_interval_ms: 60000
  max_feedback_items_per_run: 20
  include_ci_failures: true
  reply_to_comments: true
  ignore_authors: []
  max_log_chars_per_check: 12000
  max_followup_attempts_per_pr: 5

# -----------------------------------------------------------------------------
# Observability / Server
# -----------------------------------------------------------------------------
observability:
  dashboard_enabled: true
  refresh_ms: 1000
  render_interval_ms: 16

server:
  host: 127.0.0.1
  port: 8765
---

# Orchestrator Agent Prompt

你正在为仓库 **<REPO_URL>** 开发一个由本地 issue 卡片定义的子特性。

**当前状态（不要重复这些步骤）：**
- issue frontmatter 和 body 已经从 `<ISSUES_PATH>/<id>.md` 解析出来
- 仓库已 fresh clone 到当前工作目录（`<WORKSPACE_ROOT>/<safe_id>/`）
- 已切到 `{{ issue.branch_name }}` 分支，基于 `{{ issue.base_branch or '默认分支' }}`

**你的任务：**
1. 读 issue 卡片（frontmatter + body），重点关注「验收标准」「不要做」
2. 跑 `git status && git log --oneline -5` 确认基线
3. 探索代码、定位需要改的文件
4. 实施修改、添加 / 更新测试
5. 运行 `agent.test_command`（workflow 里配置的）确保通过
6. **不要 push、不要开 PR、不要 merge** —— orchestrator 会处理同步
7. 用 Conventional Commits 风格的 commit message（`feat:` / `fix:` / `refactor:` 等）

**约束：**
- 只在 working tree 里动手脚，不要碰 workspace 根目录之外的文件
- 不要新建仓库、不要改 `.git/config`、不要 force push
- 不要改 `extensions/orchestrator/tracker.py` 的 `TrackerAdapter` 接口
- 保持改动聚焦在当前 issue，不要顺手清理无关代码

**Issue:** {{ issue.identifier }} — {{ issue.title }}
{% if issue.description %}
**描述：**
{{ issue.description }}
{% endif %}
{% if issue.labels %}
**Labels:** {{ issue.labels | join(", ") }}
{% endif %}
{% if issue.base_branch %}
**基线分支:** `{{ issue.base_branch }}`
{% endif %}
{% if issue.priority is not none %}
**优先级:** P{{ issue.priority }}
{% endif %}

工作目录就是 issue 分支的根。改完代码、写完 commit，剩下的交给 orchestrator。
