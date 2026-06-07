---
# =============================================================================
# ClawCodex Orchestrator — Sequential Overlay Development Workflow
# =============================================================================
# 本 workflow 启用 extensions/orchestrator 新增的 workspace.strategy=sequential
# 策略，把「特性规划文档」拆分出的本地 issue 顺序叠加到同一条集成分支上：
#
#   - tracker.kind = local，issue 卡片存放在 /tmp/clawcodex-issues/*.md
#   - LocalTracker 候选 issue 排序规则：priority 小数字优先，其次 identifier 字典序
#   - workspace.strategy = sequential：
#       * 所有 issue 共用同一工作树 /tmp/clawcodex-dev/
#       * 集成分支 dev-decoupling-refactor-58ea488 由 base_branch 拉出
#       * 前一个 issue 的 commit 是后一个 issue 的 start_commit_sha
#       * 顺序锁 .clawcodex_workspace.lock 阻止并发
#       * dirty guard 在 issue 启动时强制要求工作区干净
#   - max_concurrent_agents = 1（sequential 强约束），保证一次只开发一个 issue
#   - workspace.hooks.after_create 在工作树首次创建时把已有集成分支备份为
#     dev-decoupling-refactor-58ea488.backup.<timestamp>，便于回滚
#   - 顺序叠加完成后，集成分支 dev-decoupling-refactor-58ea488 留有 N 个本地
#     commit，全部 issue 完成后再由人工检视并统一发起一次 PR
#   - GitSync 在 sequential / LocalTracker 模式下不会自动 push、不创建 PR
#   - pre_push / post_sync 全部留空，避免未检视改动进入共享仓库
#
# 启动方式：
#   clawcodex-dev orchestrator server start --workflow ./workflow.md
#
# issue 编写建议：
#   1. 文件名使用 001-xxx、002-xxx、003-xxx 保持顺序可读
#   2. frontmatter 中设置 priority: 1、2、3... 强制执行顺序
#   3. base_branch / branch_name 固定为 dev-decoupling-refactor-58ea488
#      （因为本流程不需要再切出独立工作分支，commit 直接落在集成分支上）
# =============================================================================

tracker:
  kind: local
  issues_path: /tmp/clawcodex-issues
  assignee: chadwweng
  branch_prefix: dev-decoupling-refactor
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
# Polling: 任务看板刷新节奏。串行叠加开发时不需要高频轮询。
# -----------------------------------------------------------------------------
polling:
  interval_ms: 30000

# -----------------------------------------------------------------------------
# Workspace: sequential 策略，所有 issue 共用 /tmp/clawcodex-dev/
# -----------------------------------------------------------------------------
# sequential 行为要点：
#   - 第一次创建时根据 repo_clone_url 完整 clone 到 /tmp/clawcodex-dev/
#   - 切到 integration_branch（不存在则从 base_branch 拉出）
#   - acquire_sequential_lock 写入 .clawcodex_workspace.lock
#   - require_clean_start=True：要求首次启动前工作区是干净的
#   - require_clean_between_issues=True：每个 issue 启动前再次确认
#   - preserve_on_terminal=True：issue 走完不删除 /tmp/clawcodex-dev/
#   - checkout_issue_branch 必须为 false：sequential 不切到 issue 分支，
#     全部 commit 直接落在 integration_branch 上
#   - issue 卡片由 tracker.issues_path 指向的 /tmp/clawcodex-issues/ 管理，
#     与 workspace.root 分离，避免被 stage 到 commit 里
workspace:
  root: /tmp/clawcodex-dev/
  repo_clone_url: /mnt/e/Nodel/ExerciseProject/clawcodex
  clone_depth: 0
  checkout_issue_branch: false
  strategy: sequential
  base_branch: dev-decoupling-refactor-58ea488
  integration_branch: dev-decoupling-refactor-58ea488
  require_clean_start: false
  require_clean_between_issues: false
  preserve_on_terminal: true
  sequential_lock: true
  git_username: chadwweng
  git_token: ""
  gitignore_patterns:
    - .event_streams
    - .orchestrator_control
    - .clawcodex_clarification_queue.json
    - .clawcodex_issue_registry.json
    - .clawcodex_workspace.lock
    - .reports
    - "*.pyc"
    - __pycache__
    - "*.egg-info"
    - .pytest_cache
    - .mypy_cache
    - .ruff_cache
    - "*.log"
    - ".issues/*.comments.ndjson"
    # 兼容旧布局：若 workspace 根目录仍残留 NNN-*.md 卡片，避免提交
    - "[0-9][0-9][0-9]-*.md"
  # Workspace 钩子：仅在 /tmp/clawcodex-dev/ 被首次创建时执行一次。
  # 备份已存在的集成分支，避免顺序叠加开发过程中出现不可逆问题。
  hooks:
    after_create: |
      if git show-ref --verify --quiet refs/heads/dev-decoupling-refactor-58ea488; then
        TS=$(date +%Y%m%d-%H%M%S)
        BACKUP_BRANCH="dev-decoupling-refactor-58ea488.backup.${TS}"
        git branch "$BACKUP_BRANCH" dev-decoupling-refactor-58ea488
        echo "[sequential-workflow] backed up integration branch to ${BACKUP_BRANCH}"
      else
        echo "[sequential-workflow] no existing integration branch to back up"
      fi
    before_run: ""
    after_run: ""
    before_remove: ""
    timeout_ms: 60000

# -----------------------------------------------------------------------------
# Agent: sequential 强制 max_concurrent_agents = 1
# -----------------------------------------------------------------------------
agent:
  provider: minimax
  model: null
  delay_between_requests_ms: 3500
  max_concurrent_agents: 1
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state:
    open: 1
    ready: 1
  permission_mode: bypassPermissions
  test_command: "/root/Conda/bin/python3 -m pytest tests/orchestrator/ -q --ignore=tests/orchestrator/test_layer_isolation.py --ignore=tests/orchestrator/test_architecture_stats.py"
  build_command: ""
  lint_command: ""
  verification:
    timeout_ms: 600000
  allow_anyone_to_retry: false
  # F-44: 人工检视闸门 — sync 后标记 PENDING_REVIEW 而非 COMPLETED
  # 运维人员需执行 `clawcodex-dev orchestrator issue review --id <id> --approve`
  review_required: true
  # F-40 root-cause fix: stagnation / loop 守卫旋钮。
  # 连续 max_no_op_turns 轮 LLM 没产生工具调用且输出为空时，break outer while，
  # session_end_reason="stagnation"。最近 loop_detection_window 轮内同一
  # tool-call 签名重复 >= loop_detection_threshold 次时，session_end_reason=
  # "loop_detected"。这两个旋钮替代了原 _NOOP_DETECTION_MAX_TURNS=5 的
  # workspace-dirty 启发式（该启发式在 workspace 含 untracked 文件时永远不触发，
  # 正是 F-09 三次 30 min 超时的根因）。
  # F-40 事后分析：agent 在首轮已写出全部代码（19 次 Write/Edit 调用），但后
  # 续轮次 LLM 空转返回 SessionComplete 导致 stagnation 误杀。因此调大阈值。
  # 同时 agent_runner 新增 has_made_progress 双阈值机制：
  #   从未写过文件（has_made_progress=False）→ max_no_op_turns 触发
  #   写过文件（has_made_progress=True）→ max_no_op_turns * 2 触发
  max_no_op_turns: 6
  loop_detection_window: 10
  loop_detection_threshold: 6


# -----------------------------------------------------------------------------
# Hooks: 只做本地日志，不 push、不开 PR、不 merge
# -----------------------------------------------------------------------------
# sequential / LocalTracker 模式下，GitSync 已自动跳过 push、PR、merge。
# 这里把 pre_push / post_sync 留空作为最后一道防线。
# 全部 issue 完成后，人工在 dev-decoupling-refactor-58ea488 上检视 commit 序列，
# 确认无误后由人工统一创建 PR。
hooks:
  before_run: |
    # Ensure python3 resolves to the conda Python in the BashTool
    # (F-40 root cause: bare ``python3`` hangs in the workspace shell
    # because the BashTool subprocess doesn't inherit our PATH).
    export PATH=/root/Conda/bin:$PATH
    if [ -f /tmp/clawcodex-dev/.bashrc ]; then
      . /tmp/clawcodex-dev/.bashrc
    fi
    echo "[sequential-workflow] starting $ISSUE_IDENTIFIER on dev-decoupling-refactor-58ea488 (PATH=$PATH)"
  after_run: "echo '[sequential-workflow] finished $ISSUE_IDENTIFIER; commit '$(git rev-parse --short HEAD)' appended to dev-decoupling-refactor-58ea488; human review will batch all commits into one PR'"
  pre_commit: ""
  pre_push: ""
  post_sync: ""
  timeout_ms: 120000

# -----------------------------------------------------------------------------
# Review feedback: 本地顺序开发阶段没有远程 PR review，关闭即可
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
# Observability / Server: 启用任务编排看板
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

你正在为 **clawcodex** 仓库执行一个按特性规划文档拆分出的本地 issue。整个流程是 **顺序叠加开发（sequential overlay）**：所有 issue 共用同一工作树 `/tmp/clawcodex-dev/`、共用同一集成分支 `dev-decoupling-refactor-58ea488`，由 orchestrator 强制单 agent 串行执行；前一个 issue 的 commit 是后一个 issue 的起点，全部 issue 完成后再由人工统一检视并提交一个 PR。

**当前流程目标：**
- issue 卡片目录：`/tmp/clawcodex-issues`（frontmatter + body 的 `*.md` 卡片）
- 集成工作树：`/tmp/clawcodex-dev/`（clone 自源仓库，与 issue 卡片目录分离）
- 集成分支：`dev-decoupling-refactor-58ea488`（即 base_branch = integration_branch）
- 工作区策略：`workspace.strategy = sequential`（共享工作树、顺序锁、dirty guard）
- 启动前：orchestrator 的 `workspace.hooks.after_create` 会自动把已存在的
  `dev-decoupling-refactor-58ea488` 备份为 `dev-decoupling-refactor-58ea488.backup.<timestamp>`
- 执行方式：一次只处理一个 issue，完成开发、测试和 commit 后再进入下一个 issue
- 当前 issue 的 `start_commit_sha` == 上一 issue 的 `commit_sha`（registry 记录）
- 最终交付：多个按顺序生成的 commit，经人工检视后统一作为一次 PR 提交到远端仓库

**排序规则：**
- 本地 tracker 会先按 `priority` 升序排序，`priority: 1` 最先执行。
- `priority` 相同或缺失时，再按 `identifier` / `id` 字典序排序。
- 因此 issue frontmatter 应使用 `priority: 1、2、3...`，并建议 identifier 使用 `001-...`、`002-...` 这样的前缀。

**当前状态（不要重复这些步骤）：**
- 任务卡片已经从 `/tmp/clawcodex-issues/<id>.md` 解析为 frontmatter + body。
- 仓库已 clone 到 `/tmp/clawcodex-dev/`（首次 issue 时），后续 issue 复用同一工作树。
- orchestrator 已切到 `dev-decoupling-refactor-58ea488`（`integration_branch`），
  并已获取 `.clawcodex_workspace.lock` 顺序锁。
- dirty guard 通过：`git status --porcelain` 为空。
- 上一 issue 的 commit 已落到集成分支上（`HEAD` 即是当前 issue 的起点）。
- registry 会在 prompt 末尾追加一段「Sequential Workspace Context」，标明
  `start_commit_sha`、`base_commit_sha`、`previous_issue_id`、`sequence_index`。
- dashboard 会展示 running / completed / failed / retry queue / tokens / last_event。

**你的任务：**

⛔ **强制约束（优先于所有步骤，不得违反）**
   1. **Python 路径**：`python3`/`pytest` 裸命令在此环境会**挂起**。必须使用绝对路径 `/root/Conda/bin/python3`。
   2. **不要调试环境**：如果绝对路径运行测试能通过，立即进入**最终三步**（git add → git commit → DONE）。
   3. **不要反复运行测试**：测试运行一次通过后即视为验证完成，不得因输出格式问题重复运行。
   4. **运行测试时禁止使用管道**：`| tail -40` / `| head -50` 等管道在此环境会产生空输出，**必须**使用 `--tb=short -q` 参数代替。
   5. **已知约束，不是 bug**：不要在探索阶段花任何时间诊断环境差异。

1. 先阅读当前 issue 卡片，重点关注「目标」「验收标准」「依赖前序 issue 的内容」「不要做」「验证方式」。
2. 跑 `git status && git branch --show-current && git log --oneline -8`，确认当前分支、工作区清洁度、最近 commit 链。
3. 确认当前 issue 是否依赖前序 issue 的实现；如果任务描述与当前代码状态不一致（例如前序 commit 缺失），**停止并报告澄清，不要猜测补做大量范围外内容**。

⏩ **执行逻辑（关键——必须遵循其中一条路径，不可兼得）：**
   A. **如果 issue 的所有代码变更已在 workspace 中实现且测试通过**（`git status` 显示有修改/新增文件，且 `test_command` 通过）：
      1. 直接进入下面的**最终三步**——`git add` → `git commit` → 输出 `DONE`。
      2. **不要重复实现已存在的功能，不要重复运行已通过的测试。**
   B. **否则**（issue 的代码尚未实现）：
      1. 先探索与任务相关的代码，优先参考 `extensions/orchestrator/` 下已有模块。
      2. **只修改当前 issue 范围内的文件**。不要重写、压缩或修改前序 issue 的 commit。
      3. 跑**一次** `agent.test_command` 确认测试通过。
      4. 进入下面的**最终三步**。

⚡ **最终三步（不允许跳过）：**
   1. `git add <改过的文件>`
   2. `git commit -m "<conventional commit message>"`
   3. 用 BashTool 输出 "DONE: <issue_id> 已提交 <short_sha>"
   在完成这三步之前，**不得返回 SessionComplete**。

**顺序叠加开发约束：**
- 一次只完成当前 issue，不要提前实现后续 issue。
- 不要修改 `.clawcodex_workspace.lock`、不要删除 `dev-decoupling-refactor-58ea488.backup.*` 备份分支。
- 不要修改 `/tmp/clawcodex-issues/*.md` issue 卡片本身（它们由 LocalTracker 解析，不属于代码变更）。
- 不要把 `start_commit_sha` 之前的历史 commit 改写；如果当前 issue 需要修正前序行为，应基于前序 commit 写一个新的 fix commit，而不是 amend。
- 如果当前 issue 需要建立在前序 issue commit 之上，但当前 workspace 缺少这些 commit（即 registry 标注的 `start_commit_sha` 与 `git log` 不一致），应**停止并报告**缺少的前置提交，不要另起一套并行实现。
- 每个 issue 完成后都必须留下一个清晰、可审查的 commit，commit 落在
  `dev-decoupling-refactor-58ea488` 分支上。
- 全部 commit 保持在本地集成分支上，等待人工统一检视后发起 PR。

**看板相关约束：**
- 保持任务状态可追踪：不要绕过 tracker、progress reporter、status dashboard 或 retry queue 的既有语义。
- 看板展示应优先复用 `extensions/orchestrator/status_dashboard.py` 和 `progress_reporter.py` 的现有能力。
- 本地 tracker 场景下，任务卡片状态应从 active_states 进入 terminal_states；不要引入只有远程 PR tracker 才能工作的必需流程。
- 对需要人工澄清的任务，优先使用 orchestrator 已有 clarification 机制，不要让 agent 随意猜测需求。

**仓库约束：**
- 只修改当前 workspace working tree 内的文件，不要写 workspace 根目录之外的文件。
- 不要新建仓库、不要改 `.git/config`、不要 force push、不要 reset --hard 集成分支。
- 不要提交 secrets、token、环境变量明文值或本地机器专属路径。
- 不要改 `extensions/orchestrator/tracker.py` 的 `TrackerAdapter` 接口，除非任务卡片明确要求。

**Issue:** {{ issue.identifier }} — {{ issue.title }}
{% if issue.description %}
**描述：**
{{ issue.description }}
{% endif %}
{% if issue.labels %}
**Labels:** {{ issue.labels | join(", ") }}
{% endif %}
{% if issue.branch_name %}
**工作分支:** `{{ issue.branch_name }}`
{% endif %}
{% if issue.priority is not none %}
**优先级:** P{{ issue.priority }}
{% endif %}

工作目录就是 `/tmp/clawcodex-dev/`（sequential 共享工作树，集成分支
`dev-decoupling-refactor-58ea488`）。完成实现、验证并提交当前 issue 的 commit 后，
等待 orchestrator 释放顺序锁并调度下一个排序后的 issue；全部 issue 完成后再由
人工统一检视所有 commit 并创建一次 PR。
