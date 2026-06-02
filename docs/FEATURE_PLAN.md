# ClawCodex 特性规划与设计文档

> 文档路径: `docs/FEATURE_PLAN.md`
> 基于: `clawcodex-opensource-replacement-analysis-v2.md`, `clawcodex_vs_ccb_analysis-v3.md`, `INTEGRATION.md`, `TEAM_MEMBERSHIP.md`
> 版本: v2.12
> 更新日期: 2026-06-02
> 上游同步: 68dc3c5 (Phase 11 bridge complete)
>
> **v2.12 变更**：新增 §3.15 CLI 模型供应商与模型切换设计（F-43，📋 设计完成）。规划 `clawcodex provider` / `clawcodex model` 子命令族（list/show/current/use/unset）+ REPL/TUI 内 `/provider` / `/model` 斜杠命令，覆盖查看、列出、切换当前生效的 LLM 供应商与模型。所有新代码落在 `clawcodex_ext/cli/` 下，遵守 "src/* 不动" 边界；持久化借道 `src.config`，不重写 I/O；错误文案统一英文；`--scope project` 落入后续规划。
>
> **v2.11 变更**：F-42 Sequential Workspace 策略实现完成（✅ 完成）。`workspace.strategy: isolated | shared | sequential` 落地：isolated 保持原有 per-issue 行为；shared/sequential 复用 `workspace.root` 作为共享工作树；sequential 强制单并发、使用 `.clawcodex_workspace.lock` 顺序锁、在 integration branch 上累积 commit 链；commit 元数据（base/start SHA、sequence_index）写入 registry；GitSync sequential 模式本地 commit 不 push、不 PR；shared/sequential root 在 cleanup 时保留。19 个专项测试 + 245 个 orchestrator 回归全部通过。
>
> **v2.10 变更**：新增 §3.1.9 Shared / Sequential Workspace 策略设计（F-42，📋 设计完成）。为 Orchestrator 增加 `workspace.strategy: isolated | shared | sequential` 规划：保留现有 per-issue isolated 行为，同时支持多个本地 issue 在同一 shared/sequential working tree 上按顺序累积 commit；设计覆盖配置字段、WorkspaceManager 路径选择、顺序锁、dirty tree guard、issue registry commit 元数据、GitSync/cleanup 行为与端到端验收。
>
> **v2.9 变更**：补充 F-22 Cron 系统相对 `claude-code-best` 的最新缺口复核结论。`clawcodex_ext/cron_system/` 已覆盖 parser/storage/scheduler/jitter/lock/permanent/inFlight/基础 runs/status 等底层能力，G1~G8 不再作为主要缺口；剩余 P0 缺口集中在真实 REPL/TUI/headless 运行路径接线、scheduled fire 执行队列、run lifecycle finalize、`/cron-list`/`/cron-delete`/trigger detail/manual fire/autonomy status 用户入口、busy gate/filter/teammate ownership 与 durable 文件变更 reload 行为。
>
> **v2.8 变更**：新增 §3.1.8 Coordinator 轻量工具集（F-41，✅ 已完成）。给 Coordinator 配置独立的轻量工具集（Read、WebSearch、WebFetch），加上原有的 Agent、SendMessage、TaskStop，共 6 个工具。 Coordinator 可直接处理简单查询（搜网页、读文件），无需为每个请求创建 Worker。所有写操作工具（Write、Edit、Bash、Grep、Glob）仍隔离，强制委派复杂任务给 Worker。涉及 `src/coordinator/mode.py` 的 `_COORDINATOR_ALLOWED_TOOLS` 扩展 + `src/coordinator/prompt.py` 的 "Your Tools" 提示词更新 + `src/repl/core.py` 注释同步。231/231 orchestrator 测试通过。
>
> **v2.7 变更**：F-39 Orchestrator Issue 重跑入口落地（Sub-A~F 全部 ✅）。`tracker.py` 增 `Intent` str-Enum + `intent_from_label_set` 优先级助手 + `Command` enum + `parse_agent_command` 正则 + `CommandIntent` 数据类（携带 `author_login`/`comment_id` 用于 Sub-F 角色校验）+ `fetch_issue_command_intent` 默认实现；`issue_registry.py:IssueRecord` 增 `intent/retry_count/last_command/intent_source/command_cursor` 字段 + `mark_intent/clear_intent/reset_for_retry/increment_retry_count/unblock` 方法；`orchestrator.py` 在 `_poll_and_dispatch` 增加 Sub-F 角色校验（`allow_anyone_to_retry`/作者匹配/fail-closed）+ 限频（`max_retries_per_issue=3`）+ 拒绝评论与高优 audit 日志；`cli/issue.py` 增 `retry` 子命令（`--mode {reset,followup,unblock}` + `--force` + `--max-retries` + `--operator` + `--reason`）写 `~/.clawcodex/orchestrator/audit.jsonl`。新增 153 个 F-39 专项单测，orchestrator 回归 231/231 通过。端到端 10-11 阶段（实际 GitCode/GitHub issue 联动）待真实环境验证。

> **v2.6 变更**：新增 §3.1.7 ProgressReporter Sink 协议重构设计（F-40，📋 设计完成）。解决 F-38 Sub-D 落地时遗留的三个问题：(1) `Orchestrator` 上 `ProgressReporter` 单例的 `_current_task_id` / `_phase_count` 共享可变状态在并发 issue 下竞争；(2) `AgentRunner` 只转发 `PhaseComplete`，`_on_session_complete` 形同虚设，session 结束无进度落点；(3) `progress = phase_count * 25` 是假数据。设计引入 `ProgressSink` Protocol + `CompositeProgressSink` 扇出 + `ToolContextProgressSink` 默认实现 + `ProgressReporter` 降级为 shim；新增 `WorkflowConfig.phases` 用于真实进度计算。

> **v2.5 变更**：已完成归档的特性（§3.1 Orchestrator 核心组件、§3.1.1~§3.1.2、§3.2 Agent 阶段性进度汇报、§3.4 MCP、§3.11 Issue 语义澄清、§3.12 Orchestrator CLI、§3.14 Agent 间自主观察、§十 Skills System Extension）以单段概览 + 归档链接形式保留，详细设计归档至 [ARCHIVED_FEATURES.md](./ARCHIVED_FEATURES.md)。本文件仅保留仍处规划/设计阶段的详细设计稿。

---

*版本 v2.6 更新：新增 §3.1.7 ProgressReporter Sink 协议重构设计（F-40）。把 `Orchestrator` 上 `ProgressReporter` 单例拆为每 session 独立的 `ProgressSink` 实例；新增 `CompositeProgressSink` 扇出支持 F-37/F-39 零侵入接入；补全 `SessionComplete` / `TurnComplete` 转发；引入 `WorkflowConfig.phases` 做真实进度计算，淘汰 `phase_count * 25` 假数据。*

*版本 v2.4 更新：新增 3.1.6 Issue 重跑入口设计（F-39）。三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发，Sub-B 重置重跑，Sub-C follow-up 叠 commit，Sub-D comment 命令解析，Sub-E CLI 兜底，Sub-F 限频+角色校验。*

*版本 v2.3 更新：新增 3.1.5 Orchestrator 验证与报告闭环设计（F-38）。Sub-A 在 `HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点，git_sync 在 commit/push 前后自动跑 verification gate（默认 `pytest -x`，用户可配 `test_command`）；Sub-B 新增 `report_writer` 生成 Markdown/JSON 报告，`IssueRecord` 增 `report_path` 字段，`git_sync._build_pr_body` 改模板插值；Sub-C 抽象 `TrackerAdapter.update_pull_request`，GitCode 客户端实现 `PATCH /repos/{owner}/{repo}/pulls/{id}`，把报告回写到 PR body 并合并为单条汇总评论；Sub-D 修复 `progress_reporter` 死代码，PhaseComplete 接入 ndjson event log。*

*版本 v1.8 更新：新增 F-37 Orchestrator PR 检视意见自动修复产品化规划。目标是在 issue 自动实现并提交 PR 后，持续读取 PR 网页检视意见、inline comments 与 CI 失败日志，驱动 agent 在同一 PR 分支上自动修改、验证、提交和推送。*

*版本 v1.7 更新：F-34 Phase 1-3 全部完成。CLI parser/dispatch 迁入 `clawcodex_ext/cli`；RuntimeContext 工厂 + Frontend 协议/注册表完成；`ClawCodexExtTUI` 8 个扩展钩子就绪。*

---

## 项目级二开边界约束

> **约束层级**: 项目级（所有 downstream/custom 开发必须遵守）
> **约束目标**: 防止二开代码污染 `src/*` 上游形状兼容区，确保未来上游同步不产生大量本地补丁累积

### 核心约束

1. **默认路径**: 所有 downstream/custom 开发默认进入根级 `clawcodex_ext/*`；**不得**在 `src/*` 中直接添加项目专属逻辑。

2. **`src/*` 定位**: `src/*` 被视为上游形状/core 兼容区，除非文件被明确标注为项目自主拥有，否则只接受：
   - 从 `clawcodex_ext/*` 向上的 thin forwarding seams
   - 最小适配层（adapter/wrapper）
   - 上游同步带来的必要更新
   - 窄范围 bug fix

3. **明确接受 minimal patch 的文件**（仅限这些文件可接受 thin forwarding/adapter 改动）：
   - `src/cli.py`
   - `src/entrypoints/tui.py`
   - `src/repl/*`
   - `src/tui/*`
   - `src/runtime/*`（未来）

4. **`src/upstream/<rev>/*`**: 仅作为上游快照同步区，**不得**在此路径下添加任何 downstream 代码。

5. **二开功能目标路径**（示例）:
   - `clawcodex_ext/cli` — CLI parser/dispatch 下游实现
   - `clawcodex_ext/tui` — TUI 下游定制
   - `clawcodex_ext/frontend` — Frontend 协议/注册表
   - `clawcodex_ext/runtime` — Runtime context factory
   - `clawcodex_ext/skills` — 下游技能/hook 扩展

6. **新功能实现流程**: 新 downstream 特性、frontend 行为、runtime 接线、命令、UI 定制、provider/tool 编排变更应首先在 `clawcodex_ext/*` 实现；对 `src/*` 的改动仅限于 thin forwarding seams。

### 约束起源

此约束将 F-34/F-35 的 CLI/TUI 前端解耦边界推广至全项目级别。最初来源于 `CONTRIBUTING.md` 中的 CLI/TUI 二开边界规则，已不能满足多层次解耦架构（upstream-sync layer + capabilities layer + orchestrator/api layer + downstream extension layer）的需求。本约束确保 downstream 扩展开发默认在 `clawcodex_ext/*` 进行，而不是直接修改上游形状文件，从而在未来的上游快照同步中避免大量本地补丁累积。

F-34/F-35 中"CLI/TUI 新功能"的描述扩展为全项目范围：所有 frontend 行为、runtime 接线、命令、UI 定制、provider/tool 编排均受此约束约束。

---

## 一、项目概述

### 1.1 项目定位

ClawCodex 是 Anthropic Claude Code 的 Python 移植版，同时扩展多 Provider 支持，目标成为功能完整的 AI Agent CLI 工具。

### 1.2 当前架构（三层解耦）

```
src/
├── upstream/            # Layer 1: 上游快照（git archive 提取的原始代码）
│   └── v2025_04/        #     具体版本标签镜像
├── capabilities/        # Layer 2: ClawCodex Protocol 接口定义
│   ├── agent_protocol.py
│   ├── tool_protocol.py
│   ├── context_protocol.py
│   ├── provider_protocol.py
│   ├── event_protocol.py          # ToolEvent 接口
│   ├── headless_protocol.py       # HeadlessOptions 接口
│   └── headless_runner.py          # 可插拔 headless 后端分发器
├── orchestrator/        # Layer 3: 自主模式编排（完全新增，无上游依赖）
├── api/                 # Layer 3: 公共 Python API（完全新增，无上游依赖）
└── ...                  # 其余为上游原有模块
```

**层约束（upstream-sync audit 强制）：**
- `src.upstream` → 只能被 `src.capabilities` 依赖
- `src.capabilities` → 不能导入 `src.upstream`
- `src.orchestrator` / `src.api` → 只能从 `src.capabilities` 导入，不能直接导入 `src.upstream`

---

## 二、已归档功能模块

> **已实现功能已归档至 [ARCHIVED_FEATURES.md](./ARCHIVED_FEATURES.md)**
>
> 以下列出的所有功能模块已在归档文档中详细记录（v2.5 范围）：
> - §一 核心 Agent 系统
> - §二 三层解耦架构（Layer Isolation）
> - §三 Provider 层（含 §3.3 LiteLLM Provider 替换，R-7）
> - §四 工具系统
> - §五 开源替代组件（R-1~R-6）
> - §六 后台运行 + 恢复同步
> - §七 Bridge Phase 8-11 多 Session Daemon 桥接器
> - §八 Agent Loop Consolidation (Stage 4)
> - §九 Advisor Token 计数与状态显示
> - §十 REPL 与 TUI 增强
> - §十一 TUI 响应性修复
> - §十二~十五 TaskInspect/TaskDirectives、ProgressReportTool、TUI 权限模式选择器、会话恢复浏览器
> - §十六 Orchestrator 自主模式（含 §16.4 生产强化 F-1.1~1.4、§16.5 三通道澄清 F-1.5~1.11、§16.6 CLI 运维界面 F-1.13）
> - §十七 MCP 协议扩展
> - §十八 Agent 间自主观察与消息交互
> - §十九 POS to Agent 转化模式
> - §二十 Skills System Extension（F-23）

---

## 三、规划功能模块

### 3.1 Orchestrator 自主模式（Symphony 集成）

**状态**: ✅ 完成（Symphony 集成）
**目标**: 支持 `clawcodex --workflow WORKFLOW.md` 自主运行模式

> 核心组件、生产强化（F-1.1~F-1.4）、Issue 语义澄清三通道（F-1.5~F-1.11）、Orchestrator CLI 运维界面（F-1.13）等子特性全部已归档。
> 详细架构、组件清单、配置形态与命令清单见 [ARCHIVED_FEATURES.md §16](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成)。
>
> 仍处规划/设计阶段、保留详细设计稿的子节如下：
> - §3.1.3 LocalTracker 本地 Issue 文档源设计（📋 设计完成）
> - §3.1.4 PR 检视意见自动修复闭环设计（F-37，📋 规划中）
> - §3.1.5 Orchestrator 验证与报告闭环设计（F-38，📋 设计完成）
> - §3.1.6 Issue 重跑入口设计（F-39，✅ 已完成）
> - §3.1.7 ProgressReporter Sink 协议重构设计（F-40，📋 设计完成）
> - §3.1.8 Coordinator 轻量工具集（F-41，✅ 已完成）
> - §3.1.9 Shared / Sequential Workspace 策略设计（F-42，📋 设计完成）

#### 3.1.3 LocalTracker 本地 Issue 文档源设计

**状态**: ✅ 完成
**目标**: 支持在本地特定路径新增 issue 文档，并由 Orchestrator 像处理 Linear/GitHub/Gitee/GitCode issue 一样追踪、领取、运行和更新状态。

##### Human Review Gate

LocalTracker 在 git commit 完成后不进行 push（无远程仓库），增加了 Human Review Gate 机制让人类审批代码变更：

```
Agent 完成 → git commit → pending_review
                              ↓
                    人类审查 diff
                              ↓
            ┌─────────────────┴─────────────────┐
            │                               │
       --approve                        --reject
            │                               │
            ↓                               ↓
    completed（工作目录保留）        反馈注入 ClarificationQueue
                                      ↓
                                  agent 重试
```

**新增状态**: `PENDING_REVIEW` — Agent 完成 git commit，等待人类 review

**新增 CLI 命令**:

| 命令 | 说明 |
|------|------|
| `clawcodex orchestrator issue diff --id <id>` | 查看变更概览（Agent Summary + 文件统计 + diff preview） |
| `clawcodex orchestrator issue diff --id <id> --stat` | 仅显示文件统计 |
| `clawcodex orchestrator issue diff --id <id> --full` | 显示完整 diff |
| `clawcodex orchestrator issue review --id <id> --approve [--comment "<text>"]` | 审批通过 |
| `clawcodex orchestrator issue review --id <id> --reject --feedback "<text>"]` | 审批拒绝，触发重试 |

**Agent Summary**: 从 `*.comments.ndjson` 中提取 `## ClawCodex Run Complete` 注释内容，显示 agent 的工作摘要。

##### 配置形态

```yaml
tracker:
  kind: local
  issues_path: /tmp/clawcodex_local_issues
  active_states:
    - open
    - ready
  terminal_states:
    - completed
    - closed
    - cancelled

workspace:
  root: /tmp/clawcodex_orchestrator_test_workspaces
  repo_clone_url: /mnt/e/Nodel/ExerciseProject/clawcodex
```

`tracker.issues_path` 是 issue 来源目录；`workspace.root` 仍只负责 per-issue workspace、registry、event logs 与运行产物，二者不应混用。

##### Issue 文档格式

首期支持 Markdown front matter，后续可扩展 JSON：

```markdown
---
id: LOCAL-001
identifier: LOCAL-001
state: open
priority: 1
branch_name: local-001-fix-dashboard-workspace
labels:
  - orchestrator
---

# 修复 dashboard workspace 解析

当前 dashboard 只读取默认 workspace 或 CLAWCODEX_WORKSPACE_ROOT。
希望它支持从 WORKFLOW.md 的 workspace.root 解析。
```

解析规则：
- `id` / `identifier` 必填；缺失时可由文件名派生，但写回时必须固化到 front matter。
- Markdown 第一个一级标题作为 `title`；正文剩余内容作为 `description`。
- `state` 必须匹配 `active_states` 才会进入候选列表。
- `branch_name` 可选；缺失时由 `identifier + title` slug 派生。
- `labels`、`priority`、`assignee_id`、`created_at`、`updated_at` 作为可选字段映射到统一 `Issue` 模型。

##### 适配器边界

新增 `LocalTrackerAdapter` 应实现既有 `TrackerAdapter` 协议，而不是在 Orchestrator 主循环中加入本地文件分支：

| 接口 | LocalTracker 行为 |
|------|-------------------|
| `fetch_candidate_issues()` | 扫描 `issues_path` 下 `.md` / `.json` 文件，过滤 active state，返回统一 `Issue` 列表 |
| `fetch_issue_states_by_ids(ids)` | 重新读取对应本地文件的 `state`，用于 launch 前前置检查 |
| `find_pull_request(...)` | 本地 tracker 无远程 PR 概念，默认返回 `None`；若 front matter 有 `pr_url` 可返回轻量结果 |
| `ensure_pull_request(...)` | 不创建远程 PR；首期写回 `commit_sha` / `branch_name` / `status`，并返回空结果或本地同步结果 |
| `fetch_issue_comments(...)` | 首期可读取同目录下 `<id>.comments.ndjson` 或 issue front matter 的 `comments` 字段；非必需 |
| `create_clarification_comment(...)` | 写入本地 comments 文件或 clarification queue，不访问外部服务 |

##### 状态写回策略

LocalTracker 的状态写回应以 issue 文档 front matter 为单一来源，`IssueRegistry` 继续保存运行态映射：

```text
open/ready → running → completed
                  └── failed
                  └── abandoned
```

建议写回字段：
- `state`: `running` / `completed` / `failed` / `abandoned`
- `claimed_at`, `completed_at`, `updated_at`
- `workspace_path`
- `branch_name`
- `commit_sha`
- `pr_url`（如后续接入本地 forge 或远程 PR）
- `last_error`（失败时）

为避免破坏用户手写正文，写回只修改 front matter，不重排 Markdown body。

##### 并发与幂等

- 每个 issue 文件旁使用短生命周期 lock（如 `.LOCAL-001.lock`）或原子 rename，避免多 orchestrator 实例同时领取。
- `fetch_candidate_issues()` 必须跳过已在 `IssueRegistry` 中 `COMPLETED`、已有 PR 或 terminal state 的 issue。
- 写回采用读-改-写，并校验 `updated_at` 或文件 mtime，检测外部编辑冲突。
- 若本地 issue 在运行中被人工改为 terminal state，launch 前检查或下一轮 poll 应停止后续处理。

##### CLI 与看板行为

LocalTracker 不需要新增独立 issue 创建命令即可工作；用户可直接在 `issues_path` 新增 `.md` 文件。现有命令继续通过 registry/event logs 工作：

```bash
clawcodex orchestrator issue list --workspace /tmp/clawcodex_orchestrator_test_workspaces
clawcodex orchestrator issue show LOCAL-001 --workspace /tmp/clawcodex_orchestrator_test_workspaces
clawcodex orchestrator issue tail LOCAL-001 --workspace /tmp/clawcodex_orchestrator_test_workspaces
```

后续可选增强：
- `clawcodex orchestrator issue new --local --title ...` 生成本地 issue 文档模板。
- dashboard 显示 `source: local` 和 issue file path。
- `issue inject` 仍作为运行中 operator hints，不替代初始 issue 文档。

##### 实施切片

1. 配置 schema 增加 `tracker.kind: local` 与 `tracker.issues_path`。
2. 新增 `local_tracker` adapter/client/parser，复用 `Issue` dataclass。
3. 接入 tracker factory，确保 Orchestrator 主循环无需感知本地/远程差异。
4. 实现 Markdown front matter 读取、active state 过滤和状态写回。
5. 增加单元测试：解析、过滤、写回、并发锁、launch 前 state 检查。
6. 增加本地 workflow 示例和端到端 smoke test。

#### 3.1.4 PR 检视意见自动修复闭环设计

**状态**: 📋 规划中
**优先级**: P0
**目标**: 将“基于 PR 网页检视意见自动修改并更新 PR”的能力产品化到 `extensions/orchestrator`，形成 issue → implementation PR → review feedback → follow-up fix → push update 的自动闭环。

##### 背景与范围

当前 Orchestrator 已具备从 issue 自动实现、提交、推送并创建/复用 PR 的能力；`GitSyncService` 会在 agent run 完成后提交改动并调用 tracker 创建 PR。缺口在 PR 创建后的 follow-up 阶段：网页上的 PR 检视意见、inline comments、review summary 与 CI/pipeline 失败日志尚未被 Orchestrator 主循环读取，也不会自动转化为同一 PR 分支上的二次修改任务。

该特性应覆盖：
- PR conversation 普通评论中的修改建议。
- PR inline review comments 中带文件路径、diff hunk、行号的修改建议。
- PR review summary 中的整体变更要求。
- CI/pipeline 失败日志中的 lint、test、typecheck 等可修复问题。
- 已处理反馈的幂等记录，避免重复修同一条评论或同一条失败检查。

首期不做自动合并 PR，也不自动处理安全敏感或需求不明确的评论；遇到互相冲突、要求不明确或超出原 issue 范围的反馈时，应进入 clarification / operator hint 流程。

##### 架构扩展

新增能力应保持 TrackerAdapter 抽象边界，由各仓库平台适配器负责平台 API 差异，Orchestrator 主循环只消费规范化后的反馈模型。

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| PullRequestFeedback 数据模型 | `extensions/orchestrator/tracker.py` | 📋 规划 | 规范化 PR 评论、inline comment、review summary、CI 失败项 |
| TrackerAdapter PR feedback 接口 | `extensions/orchestrator/tracker.py` | 📋 规划 | 新增 `fetch_pull_request_feedback`、`reply_to_pull_request_feedback` 等可选接口 |
| RepositoryIssueClient PR API | `extensions/orchestrator/repo_tracker/client.py` | 📋 规划 | 接入 GitHub/Gitee/GitCode PR review comments、PR issue comments、checks/pipelines API |
| RepositoryTrackerAdapter 映射 | `extensions/orchestrator/repo_tracker/adapter.py` | 📋 规划 | 将平台原始响应转成统一 feedback 模型 |
| Review feedback registry | `extensions/orchestrator/issue_registry.py` 或独立 store | 📋 规划 | 记录已处理 feedback id、check run id、commit sha，保证幂等 |
| Review follow-up poller | `extensions/orchestrator/orchestrator.py` | 📋 规划 | 对已有 PR 的 issue 轮询新反馈并调度 follow-up agent run |
| Review prompt builder | `extensions/orchestrator/prompt_builder.py` | 📋 规划 | 构造专用 prompt，明确只处理检视意见并保持同一 PR 分支 |
| Git sync follow-up 模式 | `extensions/orchestrator/git_sync.py` | 📋 规划 | 已有 PR 时只提交并 push 同一分支，不创建新 PR |

##### 规范化反馈模型

```python
@dataclass(frozen=True)
class PullRequestFeedback:
    id: str
    source: Literal["conversation", "inline_review", "review_summary", "ci"]
    body: str
    author_login: str | None = None
    file_path: str | None = None
    line: int | None = None
    diff_hunk: str | None = None
    severity: Literal["info", "warning", "error"] | None = None
    status: Literal["open", "resolved", "outdated"] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    commit_sha: str | None = None
    url: str | None = None
```

反馈模型必须携带稳定 id，用于幂等去重；inline review comments 应尽量保留 `file_path`、`line`、`diff_hunk`，让 agent 能精确定位；CI 失败项应保留 job/check 名称、失败摘要和日志片段，但避免把超大日志原样塞入 prompt。

##### Follow-up 运行流程

```text
1. Orchestrator 完成 issue 首次实现并创建 PR
2. registry 保存 issue_id → branch_name → pr_number/pr_url → last_feedback_cursor
3. 周期性扫描已有 open PR 的新反馈和失败检查
4. 过滤 bot 自己发布的状态评论、已处理 feedback、已过时 inline comments
5. 将剩余反馈合并为一个 review-fix prompt
6. 在同一 workspace/branch 上运行 agent
7. agent 只处理 PR feedback，不扩展新功能范围
8. 运行项目测试/检查
9. commit + push 到原 PR branch
10. registry 标记 feedback 已处理，并可选回复评论说明处理结果
```

##### Prompt 约束

Review follow-up prompt 应明确：
- 当前任务是修复 PR 检视意见，不重新实现整个 issue。
- 优先处理有文件路径和行号的 inline comments。
- 对 CI 失败，应先定位最小失败原因，再做最小修改。
- 对评论中互相冲突或需求不明确的内容，应请求 clarification，不要猜测。
- 修改完成后运行相关测试；无法运行时必须记录原因。
- 不创建新分支、不创建新 PR，只更新当前 PR 分支。

##### 配置建议

```yaml
review_feedback:
  enabled: true
  poll_interval_ms: 60000
  max_feedback_items_per_run: 20
  include_ci_failures: true
  reply_to_comments: true
  ignore_authors:
    - clawcodex-bot
  max_log_chars_per_check: 12000
  max_followup_attempts_per_pr: 5
```

该配置可以作为 `WorkflowConfig` 的新段落，默认关闭或默认跟随 orchestrator 自主模式开启；生产环境建议限制单次处理反馈数量和单 PR follow-up 次数，防止评论噪声导致无限循环。

##### 幂等与安全边界

- `IssueRegistry` 或独立 feedback store 需要记录 `feedback_id → processed_commit_sha/status`。
- 对已 resolved/outdated 的 inline comments 不应触发修改。
- 对 bot 自己发布的评论必须过滤，防止自触发循环。
- 对 destructive 操作、force push、自动合并 PR 等动作默认禁止。
- 评论中出现外部 URL、脚本或凭据相关要求时，只作为需求文本处理，不自动执行未授权命令。
- CI 日志截断后应保留失败命令、错误摘要和相关文件路径，避免 prompt 过载。

##### 实施切片

1. 扩展 tracker 协议与数据模型：新增 `PullRequestFeedback`、PR feedback fetch/reply 可选接口。
2. 为 GitHub/Gitee/GitCode repository tracker 增加 PR comments、review comments、review summary 与 CI/pipeline 读取能力。
3. 扩展 registry，记录 PR feedback cursor、已处理 feedback id、follow-up attempt 次数。
4. 在 Orchestrator poll loop 中增加 review follow-up 阶段，扫描已有 PR 并调度同分支 agent run。
5. 增加 review-fix prompt builder，约束 agent 只处理检视意见和 CI 失败。
6. 调整 git sync，使 follow-up run 只 commit/push 原分支并复用已有 PR。
7. 增加回复评论/汇总评论能力，标记哪些 feedback 已处理、哪些需要人工确认。
8. 增加单元测试和端到端测试：评论去重、inline comment 映射、CI 日志截断、bot 评论过滤、重复 follow-up 上限。

---

#### 3.1.5 Orchestrator 验证与报告闭环设计（F-38）

**状态**: 📋 设计完成
**优先级**: P0
**触发场景**: 2026-06-01 在 `chadwweng/AgentSDK` 跑 issue #1 时发现 agent 一次工具都没调（`tools=0`）仍走 SessionComplete → commit/push/PR 全程无验证；事后 PR `#1` 收到 1 条 Git Sync 评论但无 Run Complete 汇总；PR body 是静态模板不含验证/产物信息；reviewer 找不到 diff 与 workspace 路径。

##### 目标

把 `extensions/orchestrator` 的 issue 跟踪流程从「commit/push/PR 直通」补全为「commit 验证 → push 验证 → 报告生成 → PR 反馈」的端到端闭环：

1. **Sub-A Verification Gate**：commit/push 之前自动跑 `test_command`（默认 `pytest -x`，用户可配），失败时阻止 commit/push 并把 issue 标 `verification_failed`。
2. **Sub-B 结构化报告**：agent 跑完写一份 Markdown（人读）+ JSON（机读）报告到 `workspace/.reports/{id}.{md,json}`，内容包括 issue 摘要、turns/tools 计数、verification 结果、commit/diff stat、报告路径。
3. **Sub-C PR 报告回写**：抽象 `TrackerAdapter.update_pull_request` 协议，GitCode 客户端实现 `PATCH /repos/{owner}/{repo}/pulls/{id}`，git_sync 在 PR 开完后用报告回写 PR body，并把原 `_post_run_comment` + `_comment_sync_result` 两条独立评论合并为一条汇总评论。
4. **Sub-D ProgressReporter 接入**：修复 `progress_reporter.py` 死代码（`orchestrator.py:329-336` 调 `agent_runner.run(...)` 时不传 `progress_reporter` 参数），把 PhaseComplete 事件写入 ndjson event log。

##### 子特性拆分

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| A | Verification Gate | commit/push 前自动跑 test_command | `config/schema.py:HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点；`AgentConfig` 增 `test_command` / `build_command` / `lint_command`（默认可空）；`extensions/orchestrator/git_sync.py` 在 `git commit` 前调 `run_pre_commit_hook`、在 `git push` 前调 `run_pre_push_hook`；失败抛 `VerificationFailed`，orchestrator 捕获后 issue 标 `verification_failed` 不 push |
| B | 结构化报告 | agent 跑完写 Markdown/JSON 报告 | `issue_registry.py:IssueRecord` 增 `report_path: str | None` / `verification_status: str | None` / `verification_output: str | None` 字段（旧 entry 加载兼容）；新增 `extensions/orchestrator/report_writer.py` 暴露 `write(session, workspace) -> Path`；`agent_runner.py` SessionComplete 时调 `report_writer.write` 并把 `report_path` 写回 registry；`git_sync._build_pr_body` 改模板插值，插入 issue 摘要、commit/diff stat、verification 状态、报告链接 |
| C | PR 报告回写 | 把报告回写到 GitCode PR | `tracker.py:TrackerAdapter` 增抽象 `update_pull_request(pr_number, *, body=None, state=None) -> PullRequestRef | None`；`repo_tracker/client.py:RepositoryIssueClient.update_pull_request` 实现 GitCode 平台用 `PATCH /repos/{owner}/{repo}/pulls/{id}?access_token=...`（GitHub / Gitee 列 TODO，先报不支持错误）；`git_sync.py:ensure_pull_request` 拿到 `pr.number` 后调 `tracker.update_pull_request(body=...)`；合并 `agent_runner._post_run_comment` + `git_sync._comment_sync_result` 为单条 `## ClawCodex Run Summary` 汇总评论 |
| D | ProgressReporter 接入 | 修死代码 | `orchestrator.py:329-336` 显式构造 `ProgressReporter` 并传入 `agent_runner.run(...)`；`progress_reporter.py` 把 PhaseComplete 事件写入 `event_log_dir/{id}.ndjson`（与现有 ndjson 通道合并 schema，新加 `{"type": "phase", "phase": "...", "progress": N}`），`issue tail --id N` 可消费 |

##### 背景与缺口

| 缺口 | 当前位置 | 修复方向 |
|------|----------|----------|
| commit/push 前无自动验证 | `agent_runner.py:286-309` 跑完 LLM 直接 `SessionComplete`；`git_sync.py` 只 `git add/commit/push`；`workflow.md:110` 写「Run the existing test suite」仅是 LLM prompt 文本，系统不强制 | Sub-A 引入 `pre_commit` / `pre_push` hook + `test_command`，把 prompt 文本升级为系统强制步骤 |
| `HooksConfig` 生命周期点不完整 | `config/schema.py:188-193` 仅 `after_create` / `before_run` / `after_run` / `before_remove` 四点 | 扩展为 7 个点（含 Sub-A 三个新增 + 现有四个） |
| AgentConfig / CodexConfig 无 verification 字段 | `config/schema.py:157-184` | 增 `test_command` / `build_command` / `lint_command` + `verification.timeout_ms`（默认 600000） |
| `IssueRecord` 无报告字段 | `issue_registry.py:36-58` 字段为 `issue_id/branch_name/commit_sha/pr_number/pr_url/base_branch/status/attempt_count` + 几个 clarification 字段 | 增 `report_path` / `verification_status` / `verification_output` |
| 无结构化报告文件 | `agent_runner.py:440-486` 只写 `.event_logs/{id}.ndjson`（stream events）；`git_sync.py` 不写报告 | Sub-B 新增 `report_writer.py` 写 `.reports/{id}.md` + `.reports/{id}.json` |
| PR body 静态 | `git_sync.py:264-282 _build_pr_body` 写死静态文本 | 改模板插值（Sub-B），后续 Sub-C 再回写 |
| 抽象层无 `update_pull_request` | `tracker.py:30-110 TrackerAdapter` 基类未声明该方法；代码库 0 处 `update_pull_request` / `edit_pull_request` 调用 | Sub-C 抽象 + GitCode 客户端实现 |
| 两条独立评论 | `agent_runner._post_run_comment` (Run Complete) + `git_sync._comment_sync_result` (Git Sync) | Sub-C 合并为单条 `## ClawCodex Run Summary` |
| `progress_reporter` 死代码 | `orchestrator.py:329-336` 调 `agent_runner.run(...)` 不传 `progress_reporter`；模块仅 4 处引用且都是构造参数 | Sub-D 接入主流程 |

##### 实施切片（按 Sub 分组）

**Sub-A Verification Gate**:
1. `config/schema.py` 扩展 `HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点 + `AgentConfig` 增 `test_command` / `build_command` / `lint_command`（默认可空）+ `verification.timeout_ms` 默认 600000。
2. `extensions/orchestrator/git_sync.py` 在 `git commit` 前调 `run_pre_commit_hook`、在 `git push` 前调 `run_pre_push_hook`；失败时抛 `VerificationFailed`。
3. `orchestrator.py` 在 `git_sync.sync()` 末尾 `finally` 块里调 `run_post_sync_hook(session)`，并把 verification 状态写入 `IssueRecord`。
4. verification 失败时 issue 标 `verification_failed`，agent run 状态记 `failed`，不创建 PR。

**Sub-B 结构化报告**:
1. `issue_registry.py:IssueRecord` 新增 `report_path: str | None` / `verification_status: str | None` / `verification_output: str | None` 字段，旧 entry 加载兼容。
2. 新增 `extensions/orchestrator/report_writer.py`，`write(session, workspace) -> Path` 生成 Markdown（人读）+ JSON（机读）报告。
3. `agent_runner.py` SessionComplete 时调 `report_writer.write` 并把 `report_path` 写回 registry。
4. `git_sync._build_pr_body` 改模板插值，插入 issue 摘要、commit/diff stat、verification 状态、报告链接（`/tmp/symphony_workspaces/agentsdk/_1/.reports/1.md`）。

**Sub-C PR 报告回写**:
1. `tracker.py:TrackerAdapter` 增抽象 `update_pull_request(pr_number, *, body=None, state=None) -> PullRequestRef | None`。
2. `repo_tracker/client.py` 增 `RepositoryIssueClient.update_pull_request`，GitCode 平台用 `PATCH /repos/{owner}/{repo}/pulls/{id}?access_token=...`，payload 含 `body` / `state`；GitHub / Gitee 暂列 TODO（先 raise `NotImplementedError`）。
3. `git_sync.py:ensure_pull_request` 拿到 `pr.number` 后调 `tracker.update_pull_request(body=...)` 把 Sub-B 报告回写 PR。
4. 合并 `agent_runner._post_run_comment` + `git_sync._comment_sync_result` 为单条 `## ClawCodex Run Summary` 汇总评论（含报告链接、verification 状态、commit、PR URL）。

**Sub-D ProgressReporter 接入**:
1. `orchestrator.py:329-336` 显式构造 `ProgressReporter` 并传入 `agent_runner.run(...)`。
2. `progress_reporter.py` 把 PhaseComplete 事件写入 `event_log_dir/{id}.ndjson`（与现有 ndjson 通道合并 schema，新加 `{"type": "phase", "phase": "...", "progress": N}`）。
3. `issue tail --id N` 解析 `phase` 类型事件，打印阶段进度（与现有 `tool_call` / `tool_result` 同列）。

##### 验收标准

- agent 一次工具都没调（`tools=0`）时，verification gate 拦截 push，PR 不被创建，issue 标 `verification_failed`。
- `test_command` 默认值为空时该步骤跳过（不破坏已有无测试项目）。
- agent 跑完 issue registry 的 `report_path` 指向一个真实存在的文件；该文件包含 issue 摘要、commit SHA、verification 状态、diff stat。
- PR body 含「Issue / Branch / Commit / Verification / Report」五段，verification 段落根据结果渲染 ✅/❌。
- PR 开完后 issue 收到**一条**汇总评论（合并原 Run Complete + Git Sync 两条）。
- 完整代码库 0 处对 `tracker.update_pull_request` 之外的非 CRUD PR API 调用（保留可审计性）。
- `progress_reporter.ProgressReporter` 在主流程被构造；`issue tail --id N` 能看到 `{"type": "phase", ...}` 事件。

##### 风险与约束

- verification gate 默认开在 `pre_push`，失败 = 不 push。需在 `workflow.md` 文档里强调，否则用户以为 push 失败是网络问题。
- `test_command` 跑长任务会拖慢 `max_turns=20` 的 issue 跑批，需提供 `verification.timeout_ms` 配置（默认 600000）。
- GitCode `PATCH /pulls` 的 body / state 字段是否被支持需先打一个 dry-run 验证；不支持则回退为「把报告写到 `workspace/.reports/{id}.md` + 在汇总评论里贴报告全文」。
- `_post_run_comment` 与 `_comment_sync_result` 合并时若平台限流，单条评论可能太长，需提供 `summary.max_comment_chars` 截断。
- `progress_reporter` 接入需不破坏 `event_log_dir/1.ndjson` 现有 schema，扩展字段而非替换。
- 与 F-37 的 PR review follow-up 闭环保持兼容：Sub-C 的 `update_pull_request` 应是 F-37 阶段 5/7（同 PR 分支 follow-up）的基础能力，先于 F-37 落地。

##### 配置示例

**示例 1：典型 Python 项目（启用完整验证）**

```yaml
agent:
  test_command: "pytest -x -q"            # 失败 = 阻止 push
  build_command: ""                       # 留空跳过
  lint_command: "ruff check ."            # 失败 = 阻止 push
  verification:
    timeout_ms: 600000

hooks:
  pre_commit: ""                          # 跳过：让 verification 字段负责检查
  pre_push: ""                            # 跳过：让 verification 字段负责检查
  post_sync: ""                           # 跳过：默认无副作用
```

**示例 2：无测试项目（向后兼容）**

```yaml
agent:
  test_command: ""                        # 显式空 = 跳过 verification gate
  build_command: ""
  lint_command: ""

hooks:
  pre_commit:
  pre_push:
  post_sync:
```

**示例 3：需要 hook 做副作用（hook 改文件并 amend commit）**

```yaml
agent:
  test_command: "pytest -x"
  build_command: ""
  lint_command: ""

hooks:
  pre_commit: "black . && isort ."        # 格式化后由 git_sync 自动 re-add + amend
  pre_push: ""                            # 不重复跑测试
  post_sync: ""                           # 默认无清理
```

**示例 4：完全禁用 verification（emergency override）**

```yaml
agent:
  test_command: ""                        # 跳过
  build_command: ""
  lint_command: ""

hooks:
  pre_commit: "true"                      # 显式 no-op
  pre_push: "true"
  post_sync: ""
# 文档注释：等价于 3.1.5 之前的行为，提交不做任何检查
```

**配套说明**：
- `agent.test_command` 等字段跑在 `pre_push` 阶段，**作用域是工作区根目录**。
- `hooks.pre_commit` 跑在 `git add` 之后、`git commit` 之前；可修改工作区，git_sync 会自动 `git add -A && git commit --amend`。
- `hooks.pre_push` 跑在 `git commit` 之后、`git push` 之前；**不应修改工作区**（修改会报错）。
- `hooks.post_sync` 跑在 PR 创建之后；**不应修改工作区**。
- 全部字段留空（`""` 或 `None`）= 跳过该步骤，**与 3.1.5 之前行为完全一致**。

LocalTracker（无 PR 路径）应跳过 Sub-C 的 `update_pull_request` 调用，Sub-B 的报告写到 `workspace/.reports/{id}.md` 即可，不强制回写 PR body。

##### 拟定的设计决定（针对设计稿识别出的 7 个 Open Questions）

设计稿审阅后识别出 7 个未决问题。2026-06-01 起拟定如下方案，每条都明确给出根因、契约/接口形态与落地策略。该节是 Sub-A/B/C/D 实施的「前置合同」，落地时不再重新讨论。

###### 1. ProgressReporter 接口与设计目标错位（解耦方案）

**根因**：`extensions/orchestrator/progress_reporter.py:38` 的 `__init__(self, context: ToolContext)` 把 reporter 绑死到工具系统上下文，而 Sub-D 想要的是 ndjson 落盘通道——两条通道是完全不同的接收方。

**建议：拆成「翻译层 + 通道层」**

```
AgentRunner.on_event(PhaseComplete)
       ↓
ProgressReporter.on_event(event, session)        # 翻译：把 PhaseComplete → 通用 dict
       ↓
ProgressSink.write(payload: dict)                # 通道：决定写到哪里
```

**接口契约**：
- 新增 `extensions/orchestrator/progress_sink.py`，定义 `ProgressSink` 协议：`write(payload: dict) -> None`。
- 三个实现：
  - `ToolContextSink(context: ToolContext)`：调用 `ProgressReportTool._progress_report_call`，保持现有语义。
  - `NdjsonSink(event_log_dir: Path)`：追加到 `event_log_dir/{id}.ndjson`，与现有 `tool_call`/`tool_result`/`text_delta` 同行，新加 `{"type": "phase", ...}` 记录。
  - `CompositeSink(sinks: list[ProgressSink])`：扇出。
- `ProgressReporter.__init__(self, sinks: list[ProgressSink])`，移除 `ToolContext` 依赖。
- Orchestrator 在 `_run_issue` 里根据 `workflow.observability.progress_sinks`（`["ndjson", "tool", "both"]`）显式构造。
- 合并 `agent_runner._write_event_log`（lines 440-486）的重复写盘逻辑，让 `NdjsonSink` 接管，避免一个事件被写两次。

**额外好处**：未来加 stdout sink / metrics sink 不需要改 reporter 类。

###### 2. Hook 执行上下文未约定

**根因**：现有 `workspace._run_hook`（`workspace.py:211-258`）只传 `cwd=workspace.path`，环境变量是系统默认的。`pre_commit` 等 hook 需要知道 `BRANCH`、`COMMIT_SHA` 等运行时信息，文档里完全没说 env 合约，hook 写作者无法落地。

**建议：在文档里固化一张「Hook Env Contract」表**

| Hook | CWD | 必传环境变量 | 触发后可读 |
|------|-----|------------|----------|
| `after_create` | workspace path | `ISSUE_ID`, `ISSUE_IDENTIFIER`, `ISSUE_BRANCH` | `REPO_ROOT?` |
| `before_run` | workspace path | ↑ | — |
| `after_run` | workspace path | ↑ + `AGENT_STATUS`, `AGENT_TURNS`, `AGENT_TOOLS` | `REPORT_PATH` |
| `before_remove` | workspace path | ↑ | — |
| **`pre_commit`**（新增） | repo root | ↑ + `BRANCH_NAME`, `BASE_BRANCH` | `STAGED_FILE_COUNT` |
| **`pre_push`**（新增） | repo root | ↑ + `BRANCH_NAME`, `COMMIT_SHA` | — |
| **`post_sync`**（新增） | repo root | ↑ + `PR_NUMBER`, `PR_URL`, `COMMIT_SHA`, `VERIFICATION_STATUS` | `REPORT_PATH` |

**实现策略**：抽一个统一 helper（在 `workspace.py` 已有 `_run_hook` 基础上扩展为 `_run_named_hook`）：
- 合并 `os.environ` + base env（来自 issue/branch/commit） + hook-specific extra env
- 走 `subprocess_shell` + 沿用 `_run_process` 的 timeout 模式
- 全部 7 个 hook 走同一条路径，CWD/env/timeout 一致，便于单元测试

###### 3. Hook 失败 vs 测试失败的语义重叠

**根因**：verification 字段（typed）和 hook 字段（opaque shell）当前都是任意 shell 命令，失败后果没有差异——都按 FAILED 处理。但角色不同：verification 是「通过/不通过这个变更」的判定，hook 是「给用户可编程的副作用点」。

**建议：在配置层面就把两个角色分开**

| 字段 | 类别 | 失败后果 | 记录字段 |
|------|------|---------|---------|
| `agent.test_command` / `build_command` / `lint_command` | **typed verification** | 阻止 commit/push；issue 标 `verification_failed`；`IssueRecord.verification_status="failed"`, `verification_output=<stdout/stderr>` | `verification_*` |
| `hooks.pre_commit` / `pre_push` / `post_sync` | **opaque hook** | 抛 `HookFailedError`；issue 标 `failed`（走现有 FAILED 路径走 retry） | `last_hook_error`（新增字段） |

**具体规则**：
- 三个 verification 字段默认空字符串 `""` 表示跳过（**保留对无测试项目的兼容**）。
- 三个 hook 字段默认 `None` 表示跳过。
- 同时配置 verification 和 hook 时，按 `verification → pre_commit → commit → pre_push → push` 顺序串行执行，任何一步失败立刻终止后续步骤。
- 在 `IssueStatus` 枚举中**新增 `VERIFICATION_FAILED`**，并新增 `IssueRegistry.mark_verification_failed(issue_id, *, output: str)` 方法。
- 异常类分两个：`VerificationFailed(output: str)` 与 `HookFailedError(hook_name: str, output: str)`，orchestrator 在 `git_sync.sync` 的 try/except 里分支处理。

###### 4. Hook 修改文件的副作用

**两类副作用要分开处理**：

**4a. verification 命令修改文件（如 `black .` 实际改文件）**

建议：verification 字段默认为「只读模式」。

```yaml
agent:
  lint_command: "ruff check ."        # 默认 read-only
  # 若要允许修改后 commit:
  # lint_command:
  #   cmd: "black ."
  #   write: true                       # 显式声明破坏只读契约
```

实现侧：verification 字段解析为 `VerificationCommand(cmd: str, write: bool = False)`。`write=False` 时，命令运行前后对 `repo_root` 做 `git status --porcelain` 快照，命令结束后若工作区脏了，记 WARNING 日志但**不阻止 commit**（用户可能故意改了文件想一起提交——这是不可判定的，留给用户）。

**4b. `pre_commit` hook 修改文件**

`pre_commit` hook 修改工作区后，git_sync 应**自动并入 commit**：

```python
# git_sync.sync 中 pre_commit hook 之后
after_status = get_file_status(repo_root)
if after_status:
    logger.info("pre_commit hook modified %d files; staging", len(after_status))
    self._run_git_checked(["add", "-A"], repo_root)
    self._run_git_checked(["commit", "--amend", "--no-edit"], repo_root)
    commit_sha = self._run_git_output(["rev-parse", "HEAD"], repo_root)
```

这样 hook 写作者修改文件的副作用是**确定性的**：要么进入同一个 commit，要么违反「修改后未 add」导致 push 失败——不会出现「hook 改了但 commit 不含」的诡异状态。

`pre_push` 和 `post_sync` 时序上 PR 已开/即将开，**不允许修改工作区**（修改会直接报错）：

```python
if hook_name in ("pre_push", "post_sync") and get_file_status(repo_root):
    raise HookFailedError(
        hook_name,
        f"{hook_name} hook modified working tree; this is not allowed",
    )
```

###### 5. 报告文件生命周期（cleanup 时机）

**当前时序回顾**：
- `workspace.cleanup(session.issue)` 在 `orchestrator.py:_run_issue` 的 finally 块最后执行（line 387-394），会 `shutil.rmtree(workspace_path)`，**`.reports/` 随之被删除**。
- 报告随 workspace 一起被删，审计丢失。

**建议：双层存储 + 复用现有 `before_remove` 钩子**

```
~/.clawcodex/
  reports/
    {tracker_kind}/
      {owner}/{repo}/                 # 来自 workflow.tracker.{kind,owner,repo}
        {issue_id}/
          {run_id}.md                  # run_id = "run-{attempt_count}-{timestamp}"
          {run_id}.json
```

**实施细节**：
- `report_writer.write()` **同步双写**：
  - `workspace/.reports/{id}.md`（瞬态，给 in-workspace 使用，cleanup 时删除无所谓）
  - `~/.clawcodex/reports/.../{run_id}.md`（持久，cleanup 之后还在）
- `report_writer.write()` 在 `agent_runner._post_run_comment` 之前调用（line 344-347），先写盘再发评论，这样评论里可以引用持久化路径。
- 复用现有 `before_remove` 钩子作为**容错备份**：双写失败时 `before_remove` 可以 fallback 把 `workspace/.reports/` 复制到持久目录，给用户自定义兜底策略的口子。
- 加保留策略：`workflow.reports.retention_days = 90`（默认），由 orchestrator 定期清理。

**对 `cleanup()` 时机本身**：**保留现状**——每次 session 结束都清理 workspace，不为保留报告延后 cleanup。报告的持久化是 `report_writer` 的职责，不是 `workspace` 的职责。明确分层。

###### 6. 「报告路径」字段的循环引用

**根因**：本节「子特性拆分」表 B 行原写「内容包括 ... 报告路径」是 typo，路径就是报告自己所在的文件路径，循环引用。

**建议：明确区分「报告的内容」与「报告的引用」**

报告文件 `.reports/1.md` 内部**不写自身路径**，只写：
- Issue 摘要（identifier + title + url）
- turns/tools 计数
- verification status + output（截断到 4KB）
- commit SHA + diff stat（`--stat` 输出）
- run_id（attempt 编号 + 时间戳）

报告文件的**外部引用**写在两个地方：
- **PR body**：由 `git_sync._build_pr_body` 模板插值，渲染时根据已知的 `report_path` 生成 `Report: /absolute/path/to/.reports/1.md` 这一行。
- **汇总评论**：合并 `_post_run_comment` + `_comment_sync_result` 后也引用同一路径。

也就是说，**报告的路径是 PR 评论 / PR body 的元数据，不是报告本身的内容**。这样就消除了循环。

如果出于审计需要，**路径可以以 `metadata` 区单独写一份**（如 `<!-- metadata: report_path = ... -->` 这种 HTML 注释风格），既保留信息又不污染正文。或者干脆让 PR body / 评论里用 `report_filename` 这样的相对名（如 `1.md`），调用方根据 issue_id 拼接完整路径——这样报告文件里不出现任何路径字符串，最干净。

###### 7. 配置示例具有误导性

**原示例问题**：

```yaml
hooks:
  pre_commit: "echo 'pre-commit verification'"   # 永远成功，没有验证效果
  pre_push: "echo 'pre-push verification'"        # 同上
  post_sync: "echo 'post-sync cleanup'"           # 同上
```

**建议**：替换为「能正确表达语义的」四组示例，详见本节「配置示例」小节开头的四组 YAML。所有 hook 字段默认 `""` 或 `None` 表示跳过——**保留对旧项目（无 verification）的完全兼容**，用户感知不到行为变化。

##### 第二轮审阅补遗（2026-06-01）

针对首轮「拟定的设计决定」外的 5 个未决项的补遗。落地时与首轮 7 个方案**合并实施**，不再单独迭代。

| # | 项 | 补遗内容 | 涉及 Sub |
|---|----|---------|---------|
| 1 | IssueStatus 枚举 | 在 `issue_registry.py:24-33` 新增 `VERIFICATION_FAILED = "verification_failed"` 枚举值；新增 `IssueRegistry.mark_verification_failed(issue_id, *, output: str)` 方法；orchestrator 捕获 `VerificationFailed` 时调此方法（而不是 `mark_failed`）；F-39 `agent:retry` 触发时把 `VERIFICATION_FAILED` 也重置回 `PENDING`；新增 `TERMINAL_STATUSES` 冻结集合，合并 `COMPLETED/FAILED/ABANDONED/VERIFICATION_FAILED`，统一散落的终态判断 | A |
| 2 | 汇总评论时序（Option A） | agent_runner.SessionComplete 立刻发 placeholder 评论（body 含 `⏳ This summary is being prepared. It will be updated once git sync and verification complete.`），把 comment_id 存到 `AgentSession.summary_comment_id`；git_sync.sync 末尾在拿到 commit_sha / PR URL / verification 全部信息后调 `tracker.update_comment(summary_comment_id, body=完整汇总)`；新增 `TrackerAdapter.update_comment(comment_id, *, body) -> None` 抽象；3 个平台实现：GitHub/Gitee/GitCode 用 `PATCH /repos/{o}/{r}/issues/comments/{id}`，Linear 用 GraphQL `updateIssueComment`，LocalTracker 用 ndjson 临时文件 + `os.replace` 原子替换 | C |
| 3 | 重跑幂等性 run_id | `run_id` 由 `agent_runner.SessionComplete` 显式构造并传入 `report_writer.write(session, workspace, run_id=...)`，避免 report_writer 自己猜 attempt_count；格式 `run-{attempt_count:02d}-{UTC_ts}`（`datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")`）；F-39 `agent:follow-up` 触发时 `attempt_count` 不变，使用 `run-N-followup-M-{UTC_ts}` 避免与主 run 冲突；持久化路径 `~/.clawcodex/reports/{tracker}/{owner}/{repo}/{issue_id}/{run_id}.{md,json}` | B |
| 4 | 文档 ID 一致性 | FEATURE_PLAN.md 节标题已加 `(F-38)` 标识（见本节标题）；PROGRESS.md 「规划文档」列已写 `docs/FEATURE_PLAN.md → 3.1.5 验证与报告闭环设计`，双向映射存在；这是**设计文档（按主题 §3.1 编排）与跟踪文档（按 ID F-N 索引）的正常分层**，不需要合并到同一个 ID 系统；本节底部加「设计章节 ↔ 功能 ID 反向索引表」便于快速跳转 | 文档 |
| 5 | test_command 触发器归属 | `agent.test_command` / `build_command` / `lint_command` **只在 pre_push 阶段跑**（不在 pre_commit 跑）；pre_commit 阶段只允许跑 `hooks.pre_commit`（典型用法：formatter，文件改动会被自动 amend 进 commit）；pre_commit 不重命名（保留 Git 生态术语 `pre-commit hook` 习惯）；`workflow.md` 注释里明确「pre_commit 适合改文件类副作用，verification 类请用 agent.test_command 字段跑在 pre_push 阶段」；pre_commit hook 改文件后 amend 失败 → 抛 `HookFailedError("pre_commit", "amend failed: <reason>")` 标 FAILED | A |

**第二轮 5 项的落地顺序**：

| 补遗 | 依赖的首轮方案 | 落地顺序 |
|------|--------------|---------|
| 1. IssueStatus 枚举 | 3（verification vs hook 分层） | 与首轮 3 同批 |
| 2. 汇总评论时序 | — | 独立，可与首轮 C 并行 |
| 3. 重跑 run_id | 5（报告双写） | 与首轮 5 同批 |
| 4. 文档 ID | — | 文档收尾（已完成节标题改动） |
| 5. test_command 触发器 | 3 + 4（hook 改文件） | 与首轮 3、4 同批 |

**合并实施顺序**：首轮 1 → (首轮 2 + 3 + 补遗 1) → (首轮 4 + 补遗 5) → (首轮 5 + 补遗 3) → 补遗 2 → 首轮 6 → 7。

##### 7 个方案的相互依赖与实施顺序

| 方案 | 依赖的其他方案 | 落地顺序 |
|------|--------------|---------|
| 1. ProgressReporter 解耦 | 独立，可先做 | 1st |
| 2. Hook Env Contract | 3（hook 失败语义） | 2nd |
| 3. Verification vs Hook 分层 | 2 | 2nd（与 2 并行） |
| 4. Hook 文件副作用 | 3 | 3rd |
| 5. 报告生命周期 | 1（reporter 解耦后才能干净地写） | 3rd |
| 6. 报告路径去重 | 5 | 4th |
| 7. 配置示例 | 2、3、4 | 最后（文档收尾） |

**实施顺序建议**：1 → (2 + 3) → 4 → 5 → 6 → 7。每完成一组就更新本节相应章节，把「拟定」沉淀为「已确定」。

##### 依赖与协同

- **依赖 F-1**：F-38 全部 Sub 都在 Orchestrator 主流程内，依赖现有 `git_sync` / `agent_runner` / `issue_registry`。
- **先于 F-37**：F-37 阶段 5/7 需要的「同 PR 分支 follow-up 修改」依赖 F-38 Sub-C 的 `update_pull_request` 能力。
- **与 F-36 兼容**：LocalTracker 走 `pending_review` 路径不创建 PR，F-38 Sub-C 在该路径下应跳过 PR body 改写。
- **不破坏 `progress_reporter` 现有 4 个引用点**：Sub-D 接入后，单元测试覆盖原参数接口。

---

#### 3.1.6 Issue 重跑入口设计（label + comment 命令双通道）

**状态**: ✅ 完成（Sub-A~F 全部落地；E2E 阶段 10-11 待真实环境验证）
**优先级**: P0
**目标**: 在 `extensions/orchestrator` 引入「重做意图」显式表达通道,让用户在 GitCode / GitHub / Gitee 等开源社区场景下能通过加 label / 写命令 / 跑 CLI 三种方式之一,表达「重置重跑」「同 PR 叠 commit」「永久跳过」意图,无需改 registry.json 或重启 daemon。

##### 背景与现状

2026-06-01 在 `chadwweng/AgentSDK` 跑完 issue #1 后,用户想「让 agent 重做」或「在同一 PR 上再改一版」,但当前 orchestrator 4 层防御(内存 `completed` set / IssueRegistry `is_completed` / `has_pr` / `find_pull_request`)只支持「PR 存在 = 已处理」语义,不支持「关 PR = 重做」语义。关掉 PR 之后下一轮 poll 仍被 ①②③ 任意一层拦截,用户被迫:

- 手动改 `~/.clawcodex/orchestrator/.../registry.json`(易污染、需停 daemon)
- 删除远端 PR branch(不可审计、误删风险)
- 把 issue 在 tracker 端转 terminal state(反方向,会被永远排除)

这在开源社区场景下尤其突出:外部贡献者无法直接修改本地 registry,只能「关 PR」表达重做意图,但 orchestrator 完全无视这个意图。

##### 三种重做意图的语义矩阵

| Label / 命令 | 语义 | 对本地 IssueRecord | 对远程 PR | 对远程 issue | 对 agent run |
|---|---|---|---|---|---|
| `agent:retry` | 重置 + 重跑整个 issue | 清空 `status` → `pending`,删 `commit_sha` / `pr_number` / `pr_url` / `report_path`;`retry_count++` | 关闭旧 PR(状态 `closed` `not merged`) | 加 `agent:retry` 自检注释(可选) | 新 workspace、新 agent run |
| `agent:follow-up` | 保留 PR,在同 PR branch 叠 commit | `status` 保持 `completed`,`pr_number` 不变,`attempt_count++` | 不动;`update_pull_request` 走 F-38 Sub-C 入口追加 commit | 不动 | 同 workspace 同 branch,prompt 强调「只处理 follow-up」 |
| `agent:blocked` | 永久跳过该 issue | `status` 写 `abandoned` | 不动 | 加 `agent:blocked` 自检注释 | 永不 launch |

**label 互斥优先级**:若 issue 同时存在多个 intent label,以「更保守」为准:`agent:blocked` > `agent:follow-up` > `agent:retry`。理由:「保留 PR 改动证据」>「重置」;「永久跳过」>「重做」。

##### 子特性拆分

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| A | Label 解析 + 意图分发 | 把 label 映射到「重置/follow-up/跳过」三态 | `extensions/orchestrator/tracker.py:TrackerAdapter` 增 `extract_intent_from_labels(labels) -> Intent` 抽象;`extensions/orchestrator/repo_tracker/client.py:RepositoryIssueClient.fetch_candidate_issues` 在返回前用 `_OPEN_STATE_ALIASES` 之外的「intent label」识别;`extensions/orchestrator/issue_registry.py:IssueRecord` 新增 `intent: Literal["none","retry","followup","blocked"]` + `retry_count: int` + `last_command_at: str | None`;`extensions/orchestrator/orchestrator.py:_poll_and_dispatch` 在 `has_pr` 判断之前先看 intent |
| B | 重置重跑 (`agent:retry`) | 清空本地状态 + 关闭远程 PR | 新增 `IssueRegistry.reset_for_retry(issue_id)` 方法,清空 `status` / `commit_sha` / `pr_number` / `pr_url` / `report_path` 并 `retry_count++`;`tracker.py:TrackerAdapter.close_pull_request(pr_number) -> bool` 抽象;`repo_tracker/client.py:RepositoryIssueClient.close_pull_request` 实现 `PATCH /repos/{owner}/{repo}/pulls/{id}?state=closed`;`orchestrator.py` 在 launch 前若 intent=retry,先调 `close_pull_request(pr_number)` 再 launch 新 run |
| C | Follow-up 叠 commit (`agent:follow-up`) | 不开新 PR,复用原 branch | `orchestrator.py` 检测 intent=followup 时,跳过 workspace 创建(复用现有 branch),用上次 run 的报告作为上下文;`extensions/orchestrator/git_sync.py:GitSyncService.sync` 加 `mode="followup"` 分支,只 `git commit` + `git push`,不创建新 PR;`IssueRecord.attempt_count++`;依赖 F-38 Sub-C 写新 commit 到 PR body(等 F-38 落地) |
| D | Comment 命令解析 | `/agent retry` `/agent follow-up` 触发 | `tracker.py:TrackerAdapter` 增 `fetch_issue_command_intent(issue_id, since_comment_id) -> Intent | None`;`repo_tracker/client.py` 复用 `fetch_new_comments_since` 拉新评论,正则匹配 `^/agent\s+(retry|follow-up|unblock)`;orchestrator 在 launch 前调用,合并 label 意图与 command 意图(以更保守者为准);comment 触发后由 orchestrator 发 bot 确认评论 `## ClawCodex: 已受理 ${command},下一轮 poll 开始执行` |
| E | CLI 兜底命令 | `issue retry` 提供本地入口 | `extensions/orchestrator/cli/issue.py` 增 `add_retry_parser` 与 `_run_retry(registry, args)`;支持 `--mode {reset,followup,unblock}` + `--id` + `--reason` + `--force`(绕过 `max_retries_per_issue` 限频);`IssueRegistry` 增 `unblock(issue_id)` 方法(把 `abandoned` 状态回滚);命令发一条本地 audit 日志 `~/.clawcodex/orchestrator/audit.jsonl` 记录 `{ts, operator, issue_id, mode, reason}` 便于追溯 |
| F | 限频 + 角色校验 | 防滥用 | comment 命令默认要求「issue 作者」或「仓库 maintainer」才能触发(`tracker.py:TrackerAdapter` 暴露 `is_maintainer(issue_id, login) -> bool`,依赖 F-37 Sub-B 的 `fetch_issue_comments` 拿作者信息);`IssueRecord.retry_count >= max_retries_per_issue(默认 3)` 时即使加 label 也拒绝重置(写一条 `agent:retry-rejected` label + 评论说明);`audit.jsonl` 记 limit 触发 |

##### 数据模型扩展

```python
# extensions/orchestrator/issue_registry.py
class IssueRecord:
    # 现有字段
    issue_id: str
    issue_identifier: str
    branch_name: str | None
    commit_sha: str | None
    pr_number: str | None
    pr_url: str | None
    base_branch: str
    status: IssueStatus
    created_at: float
    updated_at: float
    attempt_count: int
    # --- 新增字段 ---
    intent: Literal["none", "retry", "followup", "blocked"] = "none"
    retry_count: int = 0
    last_command: str | None = None        # 最近一次 /agent 命令内容
    last_command_at: float | None = None   # 最近一次 /agent 命令时间戳
    last_command_author: str | None = None # 最近一次 /agent 命令作者
```

新增方法:

```python
class IssueRegistry:
    def reset_for_retry(self, issue_id: str) -> IssueRecord | None: ...
    def mark_followup(self, issue_id: str) -> IssueRecord | None: ...
    def unblock(self, issue_id: str) -> IssueRecord | None: ...
    def increment_retry(self, issue_id: str) -> IssueRecord | None: ...
```

##### 抽象接口扩展(TrackerAdapter)

```python
# extensions/orchestrator/tracker.py
class TrackerAdapter:
    # ... 现有接口 ...

    def extract_intent_from_labels(self, labels: list[str]) -> str:
        """返回 'retry' / 'followup' / 'blocked' / 'none' 之一。"""
        ...

    async def close_pull_request(self, pr_number: str) -> bool:
        """关闭远程 PR(closed, not merged)。返回是否成功。"""
        ...

    async def fetch_issue_command_intent(
        self, issue_id: str, since_comment_id: str | None
    ) -> tuple[str, str, str] | None:
        """返回 (intent, command_body, author_login) 或 None。"""
        ...

    async def add_label(self, issue_id: str, label: str) -> bool:
        """(可选) 自检时给 issue 加 label,例如 `agent:retry-rejected`."""
        ...
```

##### 配置 Schema(workflow.md)

```yaml
agent:
  retry:
    enabled: true
    intent_labels:
      retry: "agent:retry"
      followup: "agent:follow-up"
      blocked: "agent:blocked"
    max_retries_per_issue: 3
    comment_command_enabled: true
    comment_command_required_role: "author_or_maintainer"  # 或 "anyone"
    audit_log_path: "~/.clawcodex/orchestrator/audit.jsonl"
```

##### 实施切片

1. `tracker.py:TrackerAdapter` 增 `extract_intent_from_labels` / `close_pull_request` / `fetch_issue_command_intent` / `add_label` 四个抽象,默认实现(子类的 no-op fallback 避免 LocalTracker 强制实现)。
2. `repo_tracker/client.py:RepositoryIssueClient` 实现上述四个方法(GitCode 优先,GitHub / Gitee 列 TODO),其中 `close_pull_request` 走 `PATCH /repos/{owner}/{repo}/pulls/{id}?access_token=...&state=closed`。
3. `issue_registry.py:IssueRecord` 增 `intent` / `retry_count` / `last_command*` 字段;新增 `reset_for_retry` / `mark_followup` / `unblock` / `increment_retry` 方法;旧 entry 加载兼容(新字段 default)。
4. `orchestrator.py:_poll_and_dispatch` 增 intent 前置判断:label 解析 + comment 命令解析 + 合并;launch 路径根据 intent 分流(reset / followup / skip)。
5. `orchestrator.py` 在 intent=retry 时调 `close_pull_request(pr_number)`,再 launch 新 run。
6. `git_sync.py:GitSyncService.sync` 加 `mode` 参数;`mode="followup"` 走「只 commit/push,不开 PR」分支。
7. `cli/issue.py` 增 `retry` 子命令,实现 `_run_retry`;`audit.jsonl` 写本地审计。
8. `orchestrator.py` 增 `max_retries_per_issue` 配置(默认 3);`IssueRecord.retry_count` 超过上限拒绝重置并发评论 + `agent:retry-rejected` label。
9. 单元测试:label 解析、命令正则、retry_count 限频、role 校验、registry.reset_for_retry 状态机。
10. 端到端:在 issue #1 上加 `agent:retry` label → 60s 内观察 daemon 日志确认走 retry 路径 → issue 重新 running → 完成后 PR 编号变化。
11. 端到端:在 issue #1 上加 `agent:follow-up` label → daemon 检测到后不关 PR,在同 branch 叠 commit → PR 编号不变,commit 数 +1。

##### 验收标准

- 用户在 GitCode issue #1 上加 `agent:retry` label 后,**60s 内**(下一轮 poll)daemon 日志输出 `Issue 1 retry intent detected`,issue 状态从 `completed` 回到 `running`,旧 PR 被关闭,新 PR 编号(原 PR 编号 + N)。
- 用户在 issue #1 上加 `agent:follow-up` label 后,daemon 在同 branch 上 commit + push,**不开新 PR**,原 PR 编号不变,commit 数 +1。
- 用户在 issue comment 发 `/agent retry`,且非原作者时,**daemon 拒绝执行**并发评论 `## ClawCodex: 仅 issue 作者或 maintainer 可触发 /agent retry`。
- `agent:retry` 累计触发 4 次(超过 `max_retries_per_issue=3`)后,daemon 拒绝再次 reset,issue 上自动加 `agent:retry-rejected` label,评论中说明「已达到最大重试次数,需人工处理」。
- `clawcodex orchestrator issue retry --id 1 --mode reset --reason "wrong approach"` 立即生效,等价于 label 触发的 reset 路径,audit.jsonl 有一行 `{ts, operator, issue_id, "reset", "wrong approach"}`。
- 重置不污染已有 issue_registry.json 旧 entry schema:加载老 JSON 时 `intent` / `retry_count` 默认值生效。
- 与 F-37 协同:`agent:follow-up` 触发的 follow-up run,行为与 F-37 阶段 6 的「review-fix prompt builder」一致(只改检视意见,不改 issue 范围)。
- 与 F-38 协同:`agent:follow-up` 触发的 follow-up run 完成后,F-38 Sub-C 调 `update_pull_request` 把新 commit / 新 diff stat / 新 verification 结果追加到 PR body 末尾(以 `## ClawCodex Follow-up #N` 段落追加,非覆盖)。

##### 风险与约束

- **LLM 自触发风险**:comment 命令必须做 role 校验,否则 LLM 在自动响应里写 `/agent retry` 会自触发。
- **label 互斥冲突**:`agent:retry` + `agent:follow-up` 同时存在时需定义优先级;本期以「更保守 = follow-up」为准,后续可加 `intent_priority` 配置。
- **重置不删 git history**:reset 走「关 PR + 删本地 registry entry」,但 git remote 的 commit/branch 仍存在,这是预期行为(便于审计)。
- **限频与人工 bypass**:CLI 兜底命令的 `--force` 参数可绕过 `max_retries_per_issue` 限频,需写 `audit.jsonl` 高优条目。
- **与 F-37 耦合**:`agent:follow-up` 依赖 F-37 阶段 6 的「review-fix prompt builder」;F-37 未落地时,follow-up 路径退化为「同 branch agent run」(语义较弱的 follow-up)。
- **平台差异**:GitCode `PATCH /pulls?state=closed` 与 GitHub `PATCH /repos/{owner}/{repo}/pulls/{number}` 端点路径不同,需在 `repo_tracker/client.py` 平台分发处分别实现;Gitee / GitHub 暂列 TODO(同 F-38 Sub-C 的处理)。
- **comment 命令回放**:用户编辑老评论(非最新一条)发命令时,应只处理 `created_at > since_comment_id` 的新评论;`fetch_new_comments_since` 已实现该语义,直接复用。

##### 与现有特性的关系

| 特性 | 关系 |
|---|---|
| F-1 Orchestrator 自主模式 | F-39 是 F-1 主循环的扩展,不替换原有 4 层防御 |
| F-36 LocalTracker | `close_pull_request` 在该路径下 no-op + warning;`unblock` 行为对 LocalTracker 等价(把 `pending_review` / `abandoned` 状态回滚到 `pending`) |
| F-37 PR 检视意见自动修复 | `agent:follow-up` 路径是 F-37 的 label 入口;F-37 未落地时 follow-up 退化为「同 branch 普通 agent run」 |
| F-38 验证与报告闭环 | Sub-B 报告回写复用 F-38 Sub-C 的 `update_pull_request`;follow-up 触发的报告追加为 `report_path_v{N+1}` 序列 |
| F-38 Sub-D progress_reporter | retry 路径下每次新 run 是新 session,PhaseComplete 写 ndjson 行为照常工作 |

##### 依赖与协同

- **依赖 F-1、F-38 Sub-C**:`close_pull_request` 与 F-38 Sub-C 共享 `PATCH /pulls` 协议层(Sub-C 改 body,F-39 Sub-B 改 state);先于 F-38 落地要冗余实现一次,建议先做 F-38 Sub-C,F-39 复用。
- **与 F-37 强协同**:`agent:follow-up` 路径是 F-37「PR 检视意见自动修复」的 label 入口;F-37 未落地时 follow-up 退化为「同 branch 普通 agent run」。
- **不破坏 F-38 Sub-D**:`progress_reporter` 的 PhaseComplete 写 ndjson 逻辑在 retry 路径下应照常工作(每次新 run 是新的 session)。
- **不破坏 F-36 LocalTracker**:LocalTracker 无远程 PR 概念,`close_pull_request` 在该路径下应 no-op 并打 warning 日志;`issue_registry.unblock` 行为对 LocalTracker 等价(把 `pending_review` / `abandoned` 状态回滚到 `pending`)。

##### 实际落地（2026-06-01）

| 维度 | 改动 |
|---|---|
| **核心抽象** | `extensions/orchestrator/tracker.py` 新增 `Intent` str-Enum（NONE/RETRY/FOLLOWUP/BLOCKED）、`Command` enum（RETRY/FOLLOWUP/UNBLOCK）、`CommandIntent` 数据类（带 author_login/comment_id/comment_body）、`DEFAULT_INTENT_LABELS`、`intent_from_label_set()`、`parse_agent_command()`、`command_to_intent()`、`merge_intents()`、`extract_intent_from_labels()` 默认实现、`close_pull_request()` 默认实现、`fetch_issue_command_intent()` 默认实现（返回 `CommandIntent \| None`） |
| **适配器** | `extensions/orchestrator/repo_tracker/{client,adapter}.py` 增 `close_pull_request`（`PATCH /repos/{owner}/{repo}/pulls/{number}` + `state=closed`，422 视为成功）+ `intent_labels` 参数 + `fetch_issue_command_intent` 委派到 `fetch_new_comments_since`；`local_tracker/adapter.py` 增 `close_pull_request` no-op + `fetch_issue_command_intent` 扫描本地 `*.comments.ndjson` + `intent_labels` 参数；`linear/adapter.py` 增 `intent_labels` 参数 + `extract_intent_from_labels` |
| **状态机** | `extensions/orchestrator/issue_registry.py:IssueRecord` 增 5 个字段（`intent/retry_count/last_command/intent_source/command_cursor`）+ 5 个方法（`mark_intent/clear_intent/reset_for_retry/increment_retry_count/unblock`）；`_load()` 过滤未知字段保证老 JSON 兼容；`unblock()` 把 ABANDONED 滚回 PENDING 且清 intent，`retry_count` 保留以便限频继续生效 |
| **调度逻辑** | `extensions/orchestrator/orchestrator.py` `_poll_and_dispatch` 增 `_resolve_intent()`（label+command 合并）、`_resolve_command_intent()`、`_post_command_acknowledgement()`（"已受理"评论 + cursor）、`_prepare_intent_reset()`（Sub-B 关 PR + reset）、`_prepare_intent_session()`（Sub-C 设 `run_kind=agent_followup` + branch 复用）、`_is_command_author_eligible()`（Sub-F fail-closed）、`_reject_unauthorized_command()`（Sub-F 拒绝评论 + audit）、`_check_retry_rate_limit()`（Sub-F 限频）、`_post_retry_rejection()`（Sub-F 拒绝评论 + 标签尝试）、`_log_audit_event()`（daemon-side 审计）。UNBLOCK 命令触发时把 ABANDONED 回滚到 PENDING 并清 intent |
| **Git 同步** | `extensions/orchestrator/git_sync.py:GitSyncService.sync()` 新增 `mode: str = "default"` 参数；`mode="followup"` 顶部短路要求 `session.pull_request` 存在（fail-fast），后续走现有 followup_pr 分支只 commit/push 不开新 PR |
| **配置** | `extensions/orchestrator/config/schema.py:AgentConfig` 新增 `max_retries_per_issue: int = 3` + `allow_anyone_to_retry: bool = False`；`WorkflowConfig.from_dict()` 加载两个新字段 |
| **CLI** | `extensions/orchestrator/cli/issue.py` 新增 `retry` 子命令（`--mode {reset,followup,unblock}` + `--id` + `--reason` + `--force` + `--max-retries` + `--operator` + `--workspace/--workflow`）+ `_run_retry()` + `_append_audit_log()`（写 `~/.clawcodex/orchestrator/audit.jsonl`）+ `_resolve_operator()`（`$USER` / `os.getlogin()` / "unknown"）；dispatch 在 `run()` 末尾 |
| **测试** | 新增 6 个测试文件 153 个用例：`test_orchestrator_f39_{intent,retry,followup,command,retry_cli,ratelimit}.py`；`Intent`/`Command`/`CommandIntent` 单元覆盖、`IssueRecord` JSON round-trip + 老 schema 兼容、`_run_retry` 三模式（reset/followup/unblock）+ `--force` 旁路 + `--max-retries` 覆盖 + rate-limit 拒绝（rc=3 不动 state）、`orchestrator._is_command_author_eligible` 7 种场景（allow_anyone/None/false/空/author 匹配/other/no record）、`_check_retry_rate_limit` at-limit 拒 + force 放、`_reject_unauthorized_command` 评论 + audit |
| **回归** | orchestrator 套件 231/231 通过（含 78 个原有用例 + 153 个 F-39 新增）；`tests/manual_e2e_f38.py` 不受影响（E2E 阶段 10-11 待真实 GitCode/GitHub issue 验证） |

##### 设计决定（落地记录）

1. **`CommandIntent` 携带 author_login**（F-39 Sub-D→Sub-F 接口扩展）：早期 Sub-D 用 `Command | None` 返类型，Sub-F 角色校验需要 author_login，所以把返回类型升级为 `CommandIntent(command, author_login, comment_id, comment_body)` 数据类，向后兼容通过 `intent.command` 字段读取命令值。
2. **role check fail-closed**（LLM 自触发防护）：`author_login is None` / 空字符串直接拒绝（即使配 `allow_anyone_to_retry=True` 也会放行）；`author_login == "clawcodex"` 永远放行（bot 自己），其余需匹配 `IssueRecord.author_login`（澄清流填的作者）。
3. **`unblock()` 总是清 intent**（不是真 no-op）：docstring 写"非 ABANDONED 时不修改 status"，但 intent/intent_source/last_command 总是清零——保证下次 poll 重新走 `_resolve_intent()`；`retry_count` 不清以维持限频。
4. **CLI `--force` 高优 audit**：`audit.jsonl` 写 `{event: "retry", priority: "high", force: true, retry_count: N, max_retries_per_issue: M, rate_limited: false}`，与正常 retry 区分；`--force` 缺省时 rate-limit 命中写 `{event: "retry_rejected", priority: "high", rate_limited: true}`。
5. **限频边界**：`retry_count < max_retries_per_issue` 放行（默认 3 表示可重试 3 次）；`retry_count >= max` 拒（CLAUDE.md 验收标准 4 描述为"累计触发 4 次后拒绝"——其实是第 4 次触发时 retry_count 已经是 3，命中 3 >= 3 边界，与设计一致）。
6. **审计日志差异**：daemon `_log_audit_event` 与 CLI `_append_audit_log` 字段集略有不同（daemon 写更少字段，CLI 写 retry_count/max_retries/rate_limited），都满足设计文档的最小集 `{ts, operator, issue_id, mode, reason}`；后续可统一字段。
7. **审计日志路径**：`~/.clawcodex/orchestrator/audit.jsonl`（设计文档指定）；测试通过 `patch(_DEFAULT_AUDIT_LOG_PATH, ...)` 重定向到 tmpdir。

---

#### 3.1.7 ProgressReporter Sink 协议重构设计（F-40）

**状态**: 📋 设计完成
**优先级**: P1
**跟踪文档**: `docs/PROGRESS.md` → `F-40: ProgressReporter Sink 协议重构`
**触发场景**: 2026-06-01 F-38 Sub-D 落地把 `ProgressReporter` 接到主流程后，审阅发现三个未解决的设计债:

1. **单例竞争**：`ProgressReporter` 在 `Orchestrator.__init__` 单例化（`orchestrator.py:125-126`），`_current_task_id` / `_phase_count` 是实例可变状态；F-39 多 issue 并发跑会竞争——Issue A `set_task_id("A")` 之后 Issue B `set_task_id("B")` 会覆盖 A 的状态，PhaseComplete 写到错误的 task_id。
2. **SessionComplete 形同虚设**：`progress_reporter.py:_on_session_complete` 定义但 `AgentRunner.run` 只在两处显式构造 `PhaseComplete` 并转发（`agent_runner.py:302-304, 362-364`），`SessionComplete` 事件从未实际派发到 reporter，会话结束无进度落点。
3. **进度数据假**：`progress_reporter.py:87` 写 `progress = min(self._phase_count * 25, 100)`——纯按 phase 计数线性插值，对下游 UI 无参考价值；用户看到 75% 误以为「工作完成 3/4」实则只是「phase 计数器到了 3」。

### 设计目标

把 `extensions/orchestrator/progress_reporter.py` 从「绑死 `ToolContext` 的单例」重构为「以 `ProgressSink` 协议为最小契约的多消费者可插拔架构」:

1. 每 session 持有独立 sink 实例，状态天然隔离，消除并发竞争。
2. `AgentRunner` 转发全部三类事件 (`PhaseComplete` / `TurnComplete` / `SessionComplete`)，session 结束一定有进度落点。
3. 进度计算改用 `WorkflowConfig.phases` 比例 + LLM 显式覆盖，淘汰 `phase_count * 25` 假数据。
4. 引入 `CompositeProgressSink` 扇出，让 F-37 (PR 检视意见自动修复) / F-39 (Issue 重跑) 后续可零侵入注册专用 sink。
5. 保留 `ProgressReporter` 名字为向后兼容 shim，既有测试与调用方不破。

### 架构对比

```
旧架构（F-38 落地后，2026-06-01）:
  Orchestrator.__init__ ── 一次 ──> ProgressReporter (单例)
                                          ↓
                                  共享 _current_task_id / _phase_count
                                          ↓
                          AgentRunner.run(progress_reporter=...)
                                          ↓
                                  progress_reporter.on_event(phase_event, session)
                                          ↓
                          _progress_report_call + _task_update_call
                                          ↓
                                  ToolContext.tasks[id].metadata.progress_stages

新架构（F-40 提案）:
  Orchestrator._dispatch_issue(issue):
      ↓
      task_id = self._allocate_task_id(issue)
      inner = ToolContextProgressSink(task_id, self._progress_context,
                                       workflow_phases=self.workflow.phases)
      sink = CompositeProgressSink([
          inner,
          # 未来: PRReviewAutoFixSink(task_id, registry, git_sync),  # F-37
          # 未来: RetryLabelSink(task_id, tracker),                  # F-39
      ])
      ↓
  await self.agent_runner.run(session, self.workflow, progress_sink=sink, ...)
      ↓
      sink.on_phase_complete(event, session)   # sink 实例独占 task_id
      sink.on_turn_complete(event, session)     # 三个事件全部转发
      sink.on_session_complete(event, session)  # session 结束必落点
```

### 关键组件

#### 1. ProgressSink 协议（extensions/orchestrator/progress_sink.py）

```python
class ProgressSink(Protocol):
    """A consumer of agent progress events for ONE task/session.

    每个 sink 实例独占一个 task_id 与私有计数，状态由实例承载
    （非线程安全 = 没问题，因为实例不会被并发访问）。
    """
    task_id: str

    def on_phase_complete(self, event: PhaseComplete, session: AgentSession) -> None: ...
    def on_turn_complete(self, event: TurnComplete, session: AgentSession) -> None: ...
    def on_session_complete(self, event: SessionComplete, session: AgentSession) -> None: ...
```

#### 2. CompositeProgressSink 扇出

```python
class CompositeProgressSink:
    def __init__(self, sinks: Iterable[ProgressSink]) -> None:
        self._sinks: list[ProgressSink] = list(sinks)

    def add(self, sink: ProgressSink) -> None:
        self._sinks.append(sink)

    def on_phase_complete(self, event, session):
        for s in self._sinks:
            try:
                s.on_phase_complete(event, session)
            except Exception:
                logger.exception("sink %s.on_phase_complete failed", s)
    # on_turn_complete / on_session_complete 同理
```

#### 3. ToolContextProgressSink 默认实现

```python
class ToolContextProgressSink:
    """默认实现：把事件落进 ToolContext.tasks（与原 ProgressReporter 行为等价）。"""

    def __init__(
        self,
        task_id: str,
        context: ToolContext,
        workflow_phases: list[str] | None = None,
        fallback_to_phase_step: bool = False,
    ) -> None:
        self.task_id = task_id
        self._context = context
        self._phase_count = 0
        self._workflow_phases = workflow_phases or []
        self._fallback_to_phase_step = fallback_to_phase_step

    def _named_phase(self, idx: int) -> str:
        if 1 <= idx <= len(self._workflow_phases):
            return self._workflow_phases[idx - 1]
        return f"phase_{idx}"

    def _phase_progress(self, idx: int) -> int | None:
        if self._workflow_phases:
            named = self._named_phase(idx)
            real_idx = self._workflow_phases.index(named)
            return int((real_idx + 1) / len(self._workflow_phases) * 100)
        if self._fallback_to_phase_step:
            return min(idx * 25, 100)
        return None  # 未知，让 LLM 显式报

    def on_phase_complete(self, event, session):
        if not self.task_id:
            return
        self._phase_count += 1
        phase_name = self._named_phase(self._phase_count)
        progress = self._phase_progress(self._phase_count)
        from src.tool_system.tools.progress_report import _progress_report_call
        from src.tool_system.tools.tasks_v2 import _task_update_call
        _progress_report_call({
            "taskId": self.task_id,
            "stage": phase_name,
            "progress": progress,  # 可能 None
            "summary": f"Completed phase {self._phase_count}",
            "metadata": {
                "turn_count": event.turn_count,
                "phase": event.phase,
                "auto": True,
            },
        }, self._context)
        _task_update_call({
            "taskId": self.task_id,
            "metadata": {
                "phase": event.phase,
                "turn_count": event.turn_count,
                "phase_name": phase_name,
                "phase_complete": True,
            },
        }, self._context)

    def on_turn_complete(self, event, session):
        # 不落 ToolContext（避免噪音），仅 debug 日志
        logger.debug("turn %d complete for task %s", event.turn, self.task_id)

    def on_session_complete(self, event, session):
        if not self.task_id:
            return
        from src.tool_system.tools.progress_report import _progress_report_call
        _progress_report_call({
            "taskId": self.task_id,
            "stage": f"session_{event.reason}",
            "progress": 100 if event.reason == "success" else None,
            "summary": f"Session ended: {event.reason}",
            "metadata": {
                "session_status": session.status,
                "turn_count": session.turn_count,
                "phase_count": self._phase_count,
            },
        }, self._context)
```

#### 4. ProgressReporter 兼容 shim

```python
class ProgressReporter:
    """已弃用。新代码请直接使用 ToolContextProgressSink。"""

    def __init__(self, context: ToolContext) -> None:
        self._context = context
        self._current_task_id: str | None = None
        self._phase_count = 0
        self._sink: ToolContextProgressSink | None = None

    def set_task_id(self, task_id: str) -> None:
        self._current_task_id = task_id
        self._phase_count = 0
        self._sink = ToolContextProgressSink(task_id, self._context)

    def on_event(self, event, session):
        # 老 API: 根据类型分发
        if not self._sink:
            return
        if isinstance(event, PhaseComplete):
            self._sink.on_phase_complete(event, session)
        elif isinstance(event, TurnComplete):
            self._sink.on_turn_complete(event, session)
        elif isinstance(event, SessionComplete):
            self._sink.on_session_complete(event, session)
```

### 改造点清单

| 文件 | 改动 | Sub |
|------|------|-----|
| `extensions/orchestrator/progress_sink.py` | **新建**：`ProgressSink` 协议 + `CompositeProgressSink` + `ToolContextProgressSink` | A/B |
| `src/orchestrator/config/schema.py` | `WorkflowConfig` 新增 `phases: list[str] = field(default_factory=list)` 字段 | E |
| `extensions/orchestrator/progress_reporter.py` | 改写为兼容 shim；`on_event` 走 `isinstance` 分发；`set_task_id` 创建新 sink；标记 `@deprecated` | F |
| `extensions/orchestrator/agent_runner.py` | 参数 `progress_reporter` → `progress_sink`；`SessionComplete` 分支与 `max_turns` 路径补 `sink.on_session_complete`；若有 `TurnComplete` 分支也补 `sink.on_turn_complete`；`_write_event_log` 行为不变 | C |
| `extensions/orchestrator/orchestrator.py` | 删除 `self._progress_reporter = ProgressReporter(...)`；`_dispatch_issue` / `_run_issue` 中为每个 session 新建 `ToolContextProgressSink` + `CompositeProgressSink`；保留 `_progress_context` 共享 | D |
| `src/tool_system/tools/progress_report.py` | `ProgressReportTool` prompt 增「建议显式传 `progress`」指引；`_progress_report_call` 接受 `progress=None`（已支持） | E |
| `tests/test_orchestrator_agent_runner.py` | 新增并发回归 + 三事件覆盖测试；保留现有 stub（走 `on_event` 老 API 兼容） | G |

### 进度计算决策表

| 来源 | 触发时机 | `progress` 值 | 优先级 |
|------|----------|---------------|--------|
| LLM 显式调 `ProgressReport` 工具 | LLM 主动汇报 | LLM 传入的 `progress` | 最高 (覆盖一切) |
| `WorkflowConfig.phases` + 自动 `on_phase_complete` | PhaseComplete 事件 | `(current_idx+1) / total * 100` | 中 |
| 兜底（均无） | PhaseComplete 事件 | `None` (UI 显示「未知」) | 最低 |
| `SessionComplete` 终态 | 会话结束 | `100` (reason=success) / `None` (其他) | 终态 |

`workflow.observability.progress.fallback_to_phase_step: bool = True` 时，中间档用 `phase_count * 25` 兜底（软迁移期），后续翻 `False` 强推 None。

### 并发正确性证明

| 时间 | 事件 | 旧实现（单例） | 新实现（每 session 独立 sink） |
|------|------|----------------|--------------------------------|
| t0 | Issue A 启动 → `set_task_id("A")` | `_current_task_id="A"` | 创建 `SinkA(task_id="A")` |
| t1 | Issue B 启动 → `set_task_id("B")` | `_current_task_id="B"` (覆盖) | 创建 `SinkB(task_id="B")` |
| t2 | A 触发 `PhaseComplete` | 写到 task **B** ❌ | 通过 `SinkA` 写到 task A ✓ |
| t3 | B 触发 `PhaseComplete` | 写到 task **B** ✓ | 通过 `SinkB` 写到 task B ✓ |

`AgentRunner.run` 当前是 `async`，每个 session 跑在独立 task 上；新架构下每个 task 持自己的 sink，无共享可变状态。

### 验收标准

- 并发跑两个 issue 时，每个 session 的 `ToolContext.tasks[id].metadata.progress_stages` 列表只含本 session 的事件，无串扰。
- `SessionComplete` 触发后，`ToolContext.tasks[id].metadata.current_stage` 含 `session_{reason}`、`metadata.progress` 在 `reason=success` 时为 100、其他情况为 `None`。
- `WorkflowConfig.phases=["analysis", "design", "impl", "test", "review"]` 配置下，完成第 2 个 phase 时 `progress=40`；LLM 显式调 `ProgressReport` 传 `progress=37` 时覆盖自动值。
- `WorkflowConfig.phases` 缺失或为空时，自动 `on_phase_complete` 写 `progress=None`，`StatusDashboard` 显示「Phase N (进度未知)」，而不是误导的 25/50/75/100。
- `ProgressReporter` 类的 `on_event(event, session)` 旧 API 仍可用，内部按 `isinstance(event, PhaseComplete / TurnComplete / SessionComplete)` 分发，现有 stub 测试不修改即可通过。
- `CompositeProgressSink` 内任一 sink 抛异常被独立捕获并 `logger.exception`，不影响其他 sink 接收事件。
- F-37 / F-39 后续接入时，只需在 `Orchestrator._dispatch_issue` 注册额外 sink（`PRReviewAutoFixSink` / `RetryLabelSink`），无需修改 `AgentRunner` 或 `progress_reporter.py`。

### 风险与约束

- **API 改名 breaking**：`AgentRunner.run` 的 `progress_reporter` kwarg 改 `progress_sink` 是字面量破坏，需同步改 `Orchestrator` 调用方与所有 stub 测试。Mitigation: `ProgressReporter` shim 仍可作为 `progress_sink` 传入（duck type，只要实现三个 `on_*` 方法即可）。
- **进度从假数据变 `None` 的 UI 退化**：默认配置下旧用户从「25/50/75/100」退到「未知」。Mitigation: 加 `workflow.observability.progress.fallback_to_phase_step: bool = True` 配置开关（默认保留旧行为），后续再翻 `False`。
- **每个 session 多一个 sink 对象**：内存增长可忽略（Python 单实例，几 KB），无 perf 风险。
- **事件总线语义变化**：`CompositeProgressSink` 是同步扇出，任意 sink 阻塞会卡住 `AgentRunner` 主循环。Mitigation: 每个 sink 内部 try/except + 短超时；慢消费者应自己 queue + 后台线程。
- **Import 顺序**：`progress_reporter.py` (shim) → `progress_sink.py` (默认实现) → `agent_runner.py` (调用方) 依赖链需保持单向，避免循环 import。建议 `progress_reporter.py` 用 `from .progress_sink import ToolContextProgressSink` 软引用，`TYPE_CHECKING` 保护。

### 实施阶段

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | 新建 `extensions/orchestrator/progress_sink.py`，定义 `ProgressSink` Protocol + `CompositeProgressSink` + `ToolContextProgressSink` | A/B | 📋 待开始 |
| 2 | `src/orchestrator/config/schema.py:WorkflowConfig` 新增 `phases: list[str] = field(default_factory=list)` 字段，旧 workflow.md 无 `phases` 时退化为「无 phase 权重，自动上报 `progress=None`」 | E | 📋 待开始 |
| 3 | `extensions/orchestrator/progress_reporter.py` 改写为 shim，内部维护 `ToolContextProgressSink`，`on_event` 走 `isinstance` 分发；`set_task_id` 创建新 sink；`from .progress_sink import ...` 软引用，避免循环 import | F | 📋 待开始 |
| 4 | `extensions/orchestrator/agent_runner.py`：参数 `progress_reporter` → `progress_sink`；`SessionComplete` 分支与 `max_turns` 路径补 `sink.on_session_complete`；若有 `TurnComplete` 分支也补 `sink.on_turn_complete`；`_write_event_log` 行为不变 | C | 📋 待开始 |
| 5 | `extensions/orchestrator/orchestrator.py:125-126` 删除单例；`_dispatch_issue` / `_run_issue` 中为每个 session 新建 `ToolContextProgressSink` + `CompositeProgressSink`；保留 `_progress_context` 共享 | D | 📋 待开始 |
| 6 | `src/tool_system/tools/progress_report.py` 的 `ProgressReportTool` prompt 增「建议显式传 `progress`」指引；`_progress_report_call` 接受 `progress=None`（已支持，无需改实现） | E | 📋 待开始 |
| 7 | 单元测试：`ToolContextProgressSink` 三个回调直接调；`CompositeProgressSink` 扇出且单 sink 异常不阻塞；`ProgressReporter` shim 的 `on_event` 类型分发；`WorkflowConfig.phases` 解析默认空 | A/B/E/F | 📋 待开始 |
| 8 | 回归测试：`asyncio.gather` 并发跑两个 session，断言各自的 `ToolContext.tasks` 写入互不串扰；`SessionComplete` 落点测试 | G | 📋 待开始 |
| 9 | 更新 `tests/test_orchestrator_agent_runner.py` 的 stub（若依赖 `progress_reporter` kwarg，改为 `progress_sink`）；运行 `pytest tests/test_orchestrator_*.py -q` 与 `tests/manual_e2e_f38.py -v -s` 确认不破 | G | 📋 待开始 |

### 依赖与协同

- **依赖 F-1、F-38 Sub-D**：F-38 已把 `ProgressReporter` 接到主流程，本特性在此基础上重构；不破坏 F-38 验收标准（`progress_reporter.ProgressReporter` 在主流程被构造 → 改为 `ToolContextProgressSink` 在主流程被构造）。
- **先于 F-37 落地收益**：F-37 (PR 检视意见自动修复) 后续可注册 `PRReviewAutoFixSink` 监听 `on_session_complete` 触发 follow-up run，无需改 `AgentRunner`。
- **先于 F-39 落地收益**：F-39 (Issue 重跑) 后续可注册 `RetryLabelSink` 监听 `on_session_complete` 更新 issue label，无需改 `AgentRunner`。
- **不破坏 F-36 LocalTracker**：LocalTracker 派发的 session 也走相同的 sink 构造路径，`ToolContextProgressSink` 行为对其等价（数据落 `ToolContext.tasks`，不访问远程）。
- **与 F-22 Cron 系统解耦**：Cron 触发的 prompt 不走 orchestrator，sink 链路不被影响。

---

#### 3.1.8 Coordinator 轻量工具集（F-41）

**状态**: ✅ 已完成
**优先级**: P1
**跟踪文档**: `docs/PROGRESS.md` → `F-41: Coordinator 轻量工具集`

### 目标

给 Coordinator Agent 配置独立的轻量工具集，使其可直接处理简单查询而不必为每个请求创建 Worker Agent，同时确保写操作类工具（Edit、Write、Bash、Grep、Glob）始终隔离，强制委派复杂任务给 Worker。

### 背景

Coordination 模式启用时（`CLAUDE_CODE_COORDINATOR_MODE=true`），Coordinator 需要同时扮演两个角色：(a) 快速响应简单用户请求（搜索网页、读取文件），(b) 将复杂实现任务委派给 Worker Agent。此前 Coordinator 只有三个管理工具（Agent / SendMessage / TaskStop），任何实际工作——包括读文件、搜网页——都必须创建 Worker，不仅增加延迟，而且浪费模型 token 做无意义的任务分配。

### 设计方案

在 `src/coordinator/mode.py` 定义 `_COORDINATOR_ALLOWED_TOOLS` 白名单：

```python
_COORDINATOR_ALLOWED_TOOLS = {
    "Agent", "SendMessage", "TaskStop",       # 原有的 Agent 管理工具
    "Read", "WebSearch", "WebFetch",          # 新增：轻量读/查工具
}
```

`filter_coordinator_tools(tools)` 通过模糊名称匹配（`startswith` 优先、`in` 兜底、`inverse in` 后备）从全部工具中筛选出属于白名单的工具实例。

### 变更清单

| 文件 | 改动 |
|------|------|
| `src/coordinator/mode.py` | `_COORDINATOR_ALLOWED_TOOLS` 新增 `Read` / `WebSearch` / `WebFetch`；`filter_coordinator_tools` 逻辑不变 |
| `src/coordinator/prompt.py` | 提示词 §2 "Your Tools" 各区段展开列出 Read、WebSearch、WebFetch 的用途说明 |
| `src/repl/core.py` | 注释同步更新，反映 Coordinator 的实际工具能力 |

### 工具隔离策略

| 角色 | 拥有的工具 | 能力边界 |
|------|-----------|---------|
| **Coordinator** | Agent / SendMessage / TaskStop / Read / WebSearch / WebFetch | 读文件、搜网页、管理 Worker，**不可**执行代码或写文件 |
| **Worker** | 完整工具套件（Bash / Write / Edit / Read / Grep / Glob / WebSearch / WebFetch / ...） | 完整的编码与调试能力 |

### 验收标准

1. `CLAUDE_CODE_COORDINATOR_MODE=true` 下 Coordinator 可调用 `Read` 读取文件内容。
2. Coordinator 可调用 `WebSearch` 进行网络搜索，`WebFetch` 获取指定 URL 内容。
3. Coordinator **不能**调用 `Bash`、`Write`、`Edit`、`Grep`、`Glob`——这些工具在 `filter_coordinator_tools` 输出中被过滤。
4. Worker Agent 不受影响，工具集保持不变。
5. Coordinator 提示词中列出 6 个可用工具（Agent / SendMessage / TaskStop / Read / WebSearch / WebFetch），且不误列被过滤的工具。
6. `filter_coordinator_tools()` 返回正确的 6 个工具实例（名称模糊匹配正确）。
7. 231/231 orchestrator 回归测试通过。

### 风险与约束

- **提示词与实现需同步**：`prompt.py` 的 "Your Tools" 列表必须与 `_COORDINATOR_ALLOWED_TOOLS` 手动保持同步——无自动校验机制。
- **工具名称模糊匹配**：`filter_coordinator_tools` 用的不是精确匹配而是三后备匹配策略，如果新增一个名称以 "Web" 开头的非预期工具可能导致误放行。Mitigation：白名单设置小（仅 6 个），且新增工具需 review 白名单。
- **不涉及 Worker 工具变更**：Worker 的 `filter_worker_tools` 逻辑不变，与 Coordinator 无关。
- **CLAUDE.md 注释同步风险**：`src/repl/core.py:8-30` 的注释手动列出 Coordinator 工具，需保持同步。

---

#### 3.1.9 Shared / Sequential Workspace 策略设计（F-42）

**状态**: ✅ 完成
**优先级**: P0
**跟踪文档**: `docs/PROGRESS.md` → `F-42: Orchestrator Shared / Sequential Workspace 策略`

### 目标

扩展 Orchestrator 的 workspace 策略，使本地 issue 驱动的特性规划流程既能保留现有“每个 issue 一个独立 clone”的隔离模式，也能支持多个 issue 在同一个 working tree / integration branch 上按排序顺序叠加开发。Sequential 模式的核心目标是：issue 2 启动时可以直接看到 issue 1 已提交的 commit，每个 issue 测试通过后留下一个可审查 commit，全部 issue 完成后由人工统一检视 commit 序列并创建一个 PR。

### 背景与问题

当前 `WorkspaceManager` 的语义是 per-issue isolated workspace：`create_for_issue(issue)` 会根据 `issue.identifier` 生成 `safe_id`，最终工作目录为 `workspace.root / safe_id`。当配置了 `repo_clone_url` 时，每个 issue 都会在自己的子目录内 clone / checkout issue branch。

这对远程 issue 并行开发是安全的，但不能满足本地特性规划拆分流程：

1. 多个 issue 必须按 `LocalTracker` 排序顺序逐个执行，而不是并行执行。
2. 后一个 issue 必须建立在前一个 issue 已提交 commit 的代码状态之上。
3. commit 序列必须保留在同一个 integration branch 上，等待人工最终合并为单个 PR。
4. workflow 配置不能仅通过把 `branch_name` 写成同一个分支来解决问题，因为当前 workspace path 仍按 issue 分裂，未推送 commit 不会自动出现在下一个 issue 的 clone 中。

### 配置设计

新增 `workspace.strategy`，默认值为 `isolated`，保证现有 workflow 不改配置也保持原行为。

```yaml
workspace:
  strategy: sequential          # isolated | shared | sequential
  root: /tmp/clawcodex-dev
  repo_clone_url: /mnt/e/Nodel/ExerciseProject/clawcodex
  clone_depth: 0
  base_branch: dev-decoupling-refactor-58ea488
  integration_branch: dev-decoupling-refactor-58ea488
  checkout_issue_branch: false
  require_clean_start: true
  require_clean_between_issues: true
  preserve_on_terminal: true
  sequential_lock: true

agent:
  max_concurrent_agents: 1
  max_concurrent_agents_by_state:
    open: 1
    ready: 1
```

建议 schema 扩展：

```python
@dataclass
class WorkspaceConfig:
    root: Path
    hooks: dict[str, Any] = None
    repo_clone_url: str | None = None
    clone_depth: int | None = 1
    checkout_issue_branch: bool = True
    git_username: str | None = None
    git_token: str | None = None
    strategy: Literal["isolated", "shared", "sequential"] = "isolated"
    base_branch: str | None = None
    integration_branch: str | None = None
    require_clean_start: bool = True
    require_clean_between_issues: bool = True
    preserve_on_terminal: bool = True
    sequential_lock: bool = True
```

### 策略语义

| strategy | workspace path | 并发语义 | checkout / branch 语义 | cleanup 语义 | 适用场景 |
|----------|----------------|----------|-------------------------|--------------|----------|
| `isolated` | `workspace.root / safe_issue_id` | 可按现有配置并发 | 每个 issue 独立 checkout issue branch | 保持现有 per-issue cleanup | 远程 issue、互不依赖任务 |
| `shared` | `workspace.root` | 默认要求 `max_concurrent_agents=1`，除非未来显式支持共享并发 | 多个 issue 共享同一工作树，可由 workflow 指定 branch | 不删除 shared root | 手工共享分支、少量串行本地任务 |
| `sequential` | `workspace.root` | 强制单 agent、单 active issue | 初始化或复用 integration branch；issue 间保留 commit 序列 | 永不自动删除工作树 | 特性规划拆分 issue，按顺序叠加开发 |

`shared` 和 `sequential` 都使用同一个目录，但 `sequential` 是更强约束：它必须验证调度并发为 1，必须持有顺序锁，必须在 issue 开始/结束时检查工作区清洁度，并且 registry 需要记录 issue 间 commit 链。

### WorkspaceManager 改造

保持 `WorkspaceManager.create_for_issue(issue)` 作为外部 API，避免影响 Orchestrator 调用方；内部按 strategy 分派：

```python
async def create_for_issue(self, issue: Any) -> Workspace:
    if self.config.strategy == "isolated":
        return await self._create_isolated_workspace(issue)
    if self.config.strategy == "shared":
        return await self._create_shared_workspace(issue)
    if self.config.strategy == "sequential":
        return await self._create_sequential_workspace(issue)
    raise ValueError(f"Unsupported workspace strategy: {self.config.strategy}")
```

路径选择规则：

- `isolated`: `_root / _safe_identifier(issue.identifier)`，完全沿用现状。
- `shared` / `sequential`: `_root` 本身就是 repo working tree；如果不存在则 clone 到 `_root`；如果存在但不是 git repo，根据配置 fail-closed，不自动删除用户目录。

Sequential 准备流程：

1. 获取 `.clawcodex_workspace.lock`，锁文件位于 shared root 或 root parent，记录 pid / issue_id / timestamp。
2. 如果 `root` 不存在且配置了 `repo_clone_url`，clone 到 `root`；`clone_depth: 0` 表示完整 clone，便于本地 commit 序列审查。
3. checkout `integration_branch`；如果不存在，则从 `base_branch` 创建。
4. 如果 `require_clean_start` 为 true，运行等价于 `git status --porcelain` 的检查，dirty 时拒绝启动当前 issue。
5. 返回的 `Workspace` 使用相同 `path=root`，但保留当前 `issue_identifier` / `issue_id`，供 dashboard、event log、registry 区分 session。

### Orchestrator 调度约束

当 `workspace.strategy == "sequential"` 时，配置加载或 Orchestrator 初始化阶段应强制校验：

1. `agent.max_concurrent_agents == 1`。
2. `agent.max_concurrent_agents_by_state` 中所有 active state 的值均不超过 1。
3. LocalTracker 场景下建议 issue frontmatter 使用 `priority: 1, 2, 3...` 与 `identifier: 001-...`，排序仍沿用 `LocalTrackerAdapter.fetch_candidate_issues()` 的现有规则。
4. 当前 issue 未进入 terminal state 前，不派发下一个 issue。
5. 如果当前 workspace 缺少前序 issue 应有的 commit 链，agent prompt 应停止并报告缺失前置，而不是重新实现前序 issue。

### IssueRegistry / 进度元数据

为 shared/sequential 模式补充 per-issue commit 链记录，便于 dashboard、报告和人工审查：

```python
@dataclass
class IssueRecord:
    workspace_strategy: str | None = None
    workspace_path: str | None = None
    base_commit_sha: str | None = None
    start_commit_sha: str | None = None
    commit_sha: str | None = None
    previous_issue_id: str | None = None
    sequence_index: int | None = None
```

字段语义：

- `base_commit_sha`: sequential workspace 初始化时 integration branch 的起点。
- `start_commit_sha`: 当前 issue agent run 开始前的 HEAD。
- `commit_sha`: 当前 issue 测试和 commit 成功后的 HEAD。
- `previous_issue_id`: 当前 issue 依赖的前一个已完成 issue。
- `sequence_index`: 本轮本地 issue 排序后的序号，用于 dashboard 展示和审查报告。

### GitSync / Hook / Cleanup 行为

Sequential 模式下 GitSync 继续保持“一 issue 一 commit”的交付边界，但不得自动 push / PR / merge。LocalTracker workflow 中 `post_sync` 应为空，最终远端 PR 由人工在完整 commit 序列审查后创建。

- `pre_commit`: 可运行测试或格式化 gate，但失败时必须阻止 commit。
- `pre_push` / `post_sync`: sequential local workflow 默认留空。
- `cleanup`: `isolated` 保持现有行为；`shared` / `sequential` 不调用 `shutil.rmtree(root)`，只释放锁并保留 working tree。
- 失败时保留 dirty workspace 供人工检查；除非用户显式 retry/reset，不自动丢弃改动。

### 风险与约束

- **并发风险**：shared working tree 不适合并发写入。Sequential 模式必须 fail-closed 地拒绝 `max_concurrent_agents > 1`。
- **脏工作区风险**：前一次失败可能留下未提交变更。默认 `require_clean_start=true`，避免后续 issue 混入未审查代码。
- **分支误用风险**：`base_branch` 与 `integration_branch` 配错会导致 commit 序列落在错误分支。启动时应在日志/dashboard 中显式展示 branch 和 start SHA。
- **cleanup 数据丢失风险**：shared/sequential workspace 可能包含人工未推送 commit，cleanup 必须默认 preserve。
- **重跑语义风险**：F-39 retry 在 sequential 模式下不能简单 reset 当前 issue 目录；需要区分“在当前 HEAD 追加 follow-up commit”和“人工回滚到 start_commit_sha 后重跑”。

### 测试计划

1. `WorkspaceManager` path selection：验证 `isolated` 使用 `root/safe_id`，`shared` / `sequential` 使用 `root`。
2. clone/reuse：sequential 第一个 issue clone repo，第二个 issue 复用同一 `.git`。
3. branch 初始化：`integration_branch` 存在时 checkout；不存在时从 `base_branch` 创建。
4. dirty guard：存在未提交文件且 `require_clean_start=true` 时拒绝派发。
5. cleanup preserve：shared/sequential 完成后不删除 `root`。
6. concurrency validation：`strategy=sequential` 且 `max_concurrent_agents>1` 时配置加载或 Orchestrator 初始化失败。
7. registry metadata：每个 issue 写入 `start_commit_sha` / `commit_sha` / `sequence_index`。
8. end-to-end local sequence：两个本地 issue 按 priority 执行，第二个 issue 的 `git log` 能看到第一个 issue commit，并最终形成两个连续 commit。

### 验收标准

1. 未配置 `workspace.strategy` 的现有 workflow 行为不变。
2. `workspace.strategy: sequential` 下，两个 active local issue 会在同一 working tree 中按 LocalTracker 排序串行执行。
3. 第二个 issue 启动时 HEAD 包含第一个 issue 的 commit。
4. 每个 issue 成功后留下一个独立 commit，并在 registry / dashboard 中可追踪。
5. sequential local workflow 默认不 push、不开 PR、不 merge、不 squash。
6. 工作区 dirty 或并发配置不安全时 fail-closed，并给出可操作错误信息。
7. 全部 issue 完成后，人工可以从 integration branch 上审查连续 commit 序列并创建一个 PR。

---

### 3.2 Agent 阶段性进度汇报

**状态**: ✅ 已完成（F-20）
**目标**: 在 Agent 编排中阶段性将结果汇报至任务看板，将任务看板提取为工具

> 三组合实现方案（检查点触发 + ProgressReportTool + ToolContext.tasks）、架构设计、工具 Schema、与现有组件集成点等已归档。
> 详见 [ARCHIVED_FEATURES.md §十六（Orchestrator 自主模式 16.x）](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成) 与对应进度归档 [ARCHIVED_PROGRESS.md F-20](./ARCHIVED_PROGRESS.md#f-20-agent-阶段性进度汇报)。

---

### 3.3 Team 成员管理（Phase-7）

**状态**: 规划中
**目标**: TeamCreate 扩展 `members` 数组，跟踪团队成员 Agent

#### 3.2.1 数据模型

```json
{
  "team_name": "backend-team",
  "lead_agent_id": "a1b2c3d4e5f6",
  "members": [
    {
      "agent_id": "g7h8i9j0k1l2",
      "name": "auth-dev",
      "agent_type": "general-purpose",
      "description": "认证模块开发",
      "status": "running",
      "joined_at": "2026-05-17T10:30:00Z"
    }
  ]
}
```

#### 3.2.2 核心机制

| 机制 | 说明 |
|------|------|
| TeammateInit | `agent(run_in_background=true)` 时自动注册到 `members` |
| 状态同步 | TaskOutput 显示 completed/failed 时更新成员状态 |
| 名称注册 | Agent 名称冲突检测 `agent_name_registry` |
| 递归 Fork 保护 | Fork Agent 无法嵌套调用 Fork |

#### 3.2.3 实现文件

| 文件 | 状态 |
|------|------|
| `tool_system/tools/team.py` | ✅ 已实现基础 TeamCreate/TeamDelete |
| `tool_system/tools/agent.py` | ⚠️ 待集成 TeammateInit |
| `services/swarm/agent_name_registry.py` | ✅ 已实现名称注册表 |

#### 3.2.4 测试覆盖

| 测试文件 | 测试用例 |
|----------|----------|
| `test_team_file.py` | `test_team_file_created_with_members_array`, `test_team_file_schema_members_array`, `test_team_file_missing_members_tolerated` |
| `test_team_membership.py` | `test_is_team_lead_true_*`, `test_is_team_lead_false_*` |

---

### 3.3 结构化输出增强（Outlines）

**状态**: 适配器已完成，待集成
**目标**: 使用 Outlines 预生成约束替代 json.loads + 手动验证

#### 3.3.1 适用场景

| 场景 | 当前实现 | Outlines 方案 |
|------|---------|---------------|
| Token 预算分析 | 正则解析 | 结构化 `TokenBudgetAnalysis` |
| 工具调用决策 | json.loads 解析 | 结构化 `ToolCallDecision` |
| 压缩策略选择 | 手动判断 | 结构化 `CompactionStrategy` |
| Bash 命令分类 | 多个 validator | 结构化 `BashSafetyLevel` |

#### 3.3.2 数据模型

```python
class ToolCallDecision(BaseModel):
    should_call_tool: bool
    tool_name: str | None
    reasoning: str
    safety_level: Literal["safe", "read_only", "write", "destructive", "dangerous"]

class TokenBudgetAnalysis(BaseModel):
    current_usage: int
    threshold: int
    should_compact: bool
    recommended_strategy: Literal["summarize", "truncate", "slide_window", "none"]
    confidence: float
```

#### 3.3.3 实现文件

| 文件 | 状态 |
|------|------|
| `agent/_outlines_adapter.py` | ✅ 适配器已完成 |
| `tool_system/` 集成 | ⏳ 待进行 |

---

### 3.4 MCP 扩展功能

**状态**: 基础已完成（F-3），持续增强
**目标**: 完整的 MCP 协议支持

> 5 项基础传输与硬化能力（Stdio / HTTP+SSE / WebSocket / OAuth / HTTPS+XSS 硬化）已归档。
> 详见 [ARCHIVED_FEATURES.md §十七（MCP 协议扩展）](./ARCHIVED_FEATURES.md#十七mcp-协议扩展) 与对应进度归档 [ARCHIVED_PROGRESS.md F-3](./ARCHIVED_PROGRESS.md#f-3-mcp-协议扩展)。

#### 3.4.1 待增强

| 功能 | 优先级 | 说明 |
|------|--------|------|
| MCP 资源缓存 | P2 | 减少重复获取 |
| MCP Batch 工具调用 | P2 | 批量工具执行 |
| MCP Progress 通知 | P3 | 长任务进度报告 |

---

### 3.6 Agent 记忆作用域隔离

**状态**: ✅ 已实现（2026-06-06）
**目标**: 支持 Agent 按需加载不同作用域的记忆内容

#### 3.6.1 实现概述

通过 `clawcodex_ext/memory/` 扩展包实现，采用 **try-import + 静默降级** 模式：
- 按需调用时优先使用 `clawcodex_ext` 的 scope-aware 路径
- 扩展包不可用时静默降级到原有 `load_memory_prompt()` 行为
- 不修改原有 `memdir/` 模块的任何代码，零侵入耦合

#### 3.6.2 设计背景

传统的记忆系统是单例模式，所有 Agent 共享相同的记忆目录。在多 Agent 协作场景下，不同 Agent 可能需要访问不同范围的信息：
- 用户/私有记忆：仅当前用户可见
- 项目记忆：项目团队共享
- 团队记忆：跨项目团队共享
- 本地记忆：会话级临时信息

#### 3.6.3 实现方案

```
clawcodex_ext/memory/
├── __init__.py                 # 包声明
└── scope_aware_prompt.py       # 核心 scope 感知 prompt 逻辑
```

| 作用域 | 说明 |
|--------|------|
| `user` | 用户/私有记忆 |
| `project` | 项目上下文记忆 |
| `local` | 会话级本地记忆 |

> 注：`reference` 和 `team` 作用域保留为预留，待后续实现记忆路径体系后启用。

#### 3.6.4 核心 API

```python
# 按需加载特定作用域的记忆（通过 scope_aware_prompt 扩展）
from clawcodex_ext.memory.scope_aware_prompt import build_scope_aware_memory_prompt

# 在 build_full_system_prompt 中使用
prompt = build_full_system_prompt(
    memory_scopes=['user', 'project'],  # Agent 按需指定
    ...
)

# 或在 Agent 定义中指定
agent = AgentDefinition(
    agent_type="research-agent",
    memory_scopes=["user"],
    ...
)
```

#### 3.6.5 实现文件

| 文件 | 功能 | 类型 |
|------|------|------|
| `clawcodex_ext/memory/__init__.py` | 包声明，docstring 说明用途 | ✅ 新建 |
| `clawcodex_ext/memory/scope_aware_prompt.py` | 核心 scope 感知 prompt 逻辑（88 行） | ✅ 新建 |
| `src/context_system/prompt_assembly.py` | 4 处 forwarding seam：`build_full_system_prompt()`、`build_full_system_prompt_blocks()`、`_build_memory_section()` 参数透传 + `build_scope_aware_memory_prompt` 调用 | ✅ 修改 |

#### 3.6.6 架构决策

```
用户请求层面: build_full_system_prompt(memory_scopes=["user", "team"])
                                    │
                                    ▼
                  _build_memory_section(memory_scopes)
                                    │
                          ┌─────────▼─────────┐
                          │ memory_scopes 非 None? │
                          └─────────┬─────────┘
                           Yes │         No │
                               ▼           ▼
                   try: clawcodex_ext     src.memdir
                   └→ scope_aware_prompt  load_memory_prompt()
                      build_...()
                        │
                        ▼ (fallback if ext unavailable)
                      src.memdir
                      load_memory_prompt()
```

**关键设计决策：**
- `memory_scopes` 参数默认 `None` → 100% 向后兼容
- `clawcodex_ext` 通过 try-import 方式调用，失败时静默降级到原有 `load_memory_prompt()` 行为
- `VALID_MEMORY_SCOPES` 在两个模块中各自定义（镜像关系），避免 `clawcodex_ext` 对 `src` 的导入依赖
- 未知 scope 记录 warning 但不会 crash

#### 3.6.7 验证结果

- ✅ 231/231 orchestrator 测试通过（F-39 Sub-A~F 全部落地，含 153 个 F-39 专项用例）
- ✅ 371/378 parity 测试通过（7 个预存失败）
- ✅ F-38 E2E 全部 4 轮通过

# 3.7 /goal 命令（目标管理）

| 功能 | ClawCodex | Claude Code Best | 优先级 |
|------|-----------|------------------|--------|
| Voice Mode | ❌ 未实现 | ✅ 完整 | P3 |
| Computer Use | ❌ 未实现 | ✅ 完整 | P3 |
| Chrome Use | ❌ 未实现 | ✅ 浏览器自动化 | P3 |
| Remote Control (Docker+WebUI) | ⚠️ 基础 | ✅ 完整 | P2 |
| Pipe IPC / LAN | ❌ | ✅ | P3 |
| ACP/Zed/Cursor 集成 | ❌ | ✅ | P3 |
| Langfuse 监控 | ❌ | ✅ | P3 |
| Feature Flags | ❌ | ✅ | P3 |

---

### 3.7 /goal 命令（目标管理）

**状态**: ⏳ 待实现
**目标**: 支持长时间运行任务的目标管理

#### 3.7.1 功能说明

支持长时间任务的目标状态管理与 token 用量追踪：

| 子命令 | 功能 |
|--------|------|
| `/goal set <goal>` | 设置当前任务目标 |
| `/goal clear` | 清除目标 |
| `/goal pause` | 暂停目标追踪 |
| `/goal resume` | 恢复目标追踪 |
| `/goal complete` | 标记目标完成 |

#### 3.7.2 核心机制

| 机制 | 说明 |
|------|------|
| Goal 状态机 | `active` / `paused` / `budget_limited` / `complete` |
| Token 用量追踪 | 自动追踪当前 session 的 token 消耗 |
| Continuation Prompt | 目标状态自动注入到 continuation prompt |
| session-scoped 隔离 | 按 sessionId 管理独立的目标状态 |

#### 3.7.3 实现文件

| 文件 | 位置 | 状态 |
|------|------|------|
| Goal 命令 | `commands/goal/goal.ts` | 待实现 |
| Goal 状态管理 | `services/goal/goalState.ts` | 待实现 |
| Goal 工具 | `packages/builtin-tools/src/tools/GoalTool/` | 待实现 |

#### 3.7.4 数据模型

```typescript
interface GoalState {
  sessionId: UUID
  goal: string
  status: 'active' | 'paused' | 'budget_limited' | 'complete'
  createdAt: Date
  updatedAt: Date
  tokenUsage: {
    current: number
    threshold: number
  }
}
```

---

### 3.8 ExecuteExtraTool 延迟工具系统

**状态**: ⏳ 待实现
**目标**: 按需加载延迟工具，支持语义搜索

#### 3.8.1 功能说明

完整的延迟工具按需加载系统，支持子代理（Async Agent）执行：

| 组件 | 功能 |
|------|------|
| SearchExtraToolsTool | TF-IDF 工具索引语义搜索 |
| ExecuteExtraTool | 通过名称和参数执行延迟工具 |
| validateInput 校验 | 调用前校验防止崩溃 |
| ASYNC_AGENT_ALLOWED_TOOLS | 子代理可执行延迟工具 |

#### 3.8.2 核心机制

| 机制 | 说明 |
|------|------|
| 工具延迟加载 | 工具按名称和参数动态执行，非预加载 |
| 语义搜索 | TF-IDF 索引支持自然语言工具搜索 |
| 子代理执行 | Async Agent 可调用延迟工具 |
| 输入校验 | execute 前 validateInput 防止无效调用 |

#### 3.8.3 实现文件

| 文件 | 位置 | 状态 |
|------|------|------|
| ExecuteExtraTool | `packages/builtin-tools/src/tools/ExecuteTool/ExecuteTool.ts` | 待实现 |
| SearchExtraToolsTool | `packages/builtin-tools/src/tools/SearchExtraToolsTool/` | 待实现 |
| ASYNC_AGENT_ALLOWED_TOOLS | `constants/tools.ts` | 待配置 |
| 延迟工具提示 | `constants/prompts.ts` | 待配置 |

---

### 3.9 工具/Skill 调用统计（跨会话）

**状态**: 🔄 规划中
**目标**: 通过追加日志（JSON Lines）实现轻量级跨会话工具和 Skill 调用统计，不支持实时查询

#### 3.9.1 背景

当前项目没有调用统计功能，无法了解工具和 Skill 使用分布情况。本特性解决跨会话数据持久化问题，工具和 Skill 共用同一日志 schema。

#### 3.9.2 日志格式

```
~/.clawcodex/tool_stats.jsonl
{"agent_id": "dev", "kind": "tool", "tool": "Read", "ts": 1748..., "dur_ms": 12.3, "ok": true}
{"agent_id": "dev", "kind": "skill", "skill": "code_review", "ts": 1748..., "dur_ms": 3200.0, "ok": true}
{"agent_id": "orchestrator-001", "kind": "tool", "tool": "Bash", "ts": 1748..., "dur_ms": 2300.0, "ok": false, "error": "timeout"}
```

#### 3.9.3 日志字段（统一 schema）

| 字段 | 类型 | 说明 |
|------|------|------|
| `agent_id` | string | Agent 标识符（REPL 会话为 "main"，子 agent 按配置） |
| `kind` | string | `"tool"` 或 `"skill"` |
| `tool` | string \| null | 工具名称（kind=tool 时） |
| `skill` | string \| null | Skill 名称（kind=skill 时） |
| `ts` | float | Unix 时间戳（秒） |
| `dur_ms` | float | 执行耗时（毫秒） |
| `ok` | bool | 是否成功 |
| `error` | string \| null | 错误信息（失败时） |
| `params` | dict \| null | Skill 调用参数（kind=skill 时） |
| `skill_version` | string \| null | Skill 版本（kind=skill 时） |

#### 3.9.4 性能特性

| 操作 | 性能影响 | 说明 |
|------|---------|------|
| 追加写入 | 极小 | 顺序追加是磁盘 I/O 最优模式 |
| 文件过大后查询 | 较大 | 全量扫描，数据量大时需预聚合 |
| 多进程并发写 | 中等 | 建议单进程内汇聚后批量写入 |

#### 3.9.5 架构设计

```
src/tool_system/
└── stats.py                    # 统计模块（新）
    ├── record(name, dur_ms, ok, error, *, kind, params, version)  # 统一记录
    ├── get_stats()             # 查询汇总（读取日志文件聚合）
    └── _write_buffered()       # 批量写入

注入点:
  agent_loop.py                 # 工具执行完成后调用 record(kind="tool")
  skills/loader.py             # Skill 执行完成后调用 record(kind="skill")
```

#### 3.9.6 查询示例

```bash
# 统计所有 skill 调用
grep '"kind":"skill"' ~/.clawcodex/tool_stats.jsonl | jq '.skill' | sort | uniq -c | sort -rn

# 统计工具 vs skill 调用比例
grep -E '"kind":"(tool|skill)"' ~/.clawcodex/tool_stats.jsonl | jq -s 'group_by(.kind) | map({kind: .[0].kind, count: length})'

# 统计某个 agent 的调用
grep '"agent_id":"orchestrator-001"' ~/.clawcodex/tool_stats.jsonl | jq -s 'group_by(.kind) | map({kind: .[0].kind, count: length, avg_ms: (map(.dur_ms) | add / length)})'
```

#### 3.9.7 数据清理

日志文件需定期归档或设置 TTL（建议保留最近 90 天数据）。

#### 3.9.8 实时查询

**不支持**。如需实时展示（如 TUI 状态栏），需另建汇总表预聚合。

#### 3.9.9 替代方案：基于 Transcript 的轻量级统计

如果只关心**调用频率和成功率**（不需要耗时），可直接解析现有 Transcript 文件，无需新建日志系统。

**数据来源**:

```
~/.clawcodex/transcripts/<agent_id>.jsonl
```

每行是一个 `Message`，其中包含 `ToolUseBlock`：

```json
{"type": "user", "content": [{"type": "tool_use", "id": "2", "name": "Read", "input": {"path": "foo.py"}}]}
{"type": "assistant", "content": [{"type": "tool_use", "id": "3", "name": "Edit", ...}]}
{"type": "user", "content": [{"type": "tool_result", "tool_use_id": "2", "content": "...", "is_error": false}]}
```

**统计维度**:

| 维度 | 支持 | 说明 |
|------|------|------|
| 调用频率 | ✅ | 按 tool/skill 名称统计 |
| 成功率 | ✅ | ToolResult.is_error 可判断 |
| 执行耗时 | ❌ | Transcript 不记录执行时长 |
| Skill 调用 | ⚠️ | 取决于 Skill 是否走 ToolUseBlock |

**查询示例**:

```bash
# 统计所有工具调用次数
grep '"type":"tool_use"' ~/.clawcodex/transcripts/*.jsonl | jq '.content[].name' | sort | uniq -c | sort -rn

# 统计某个 agent 的工具调用
grep '"type":"tool_use"' ~/.clawcodex/transcripts/agent-123.jsonl | jq -s 'group_by(.content[].name) | map({tool: .[0].content[].name, count: length})'

# 统计错误率（需配对 ToolUse → ToolResult）
# 由于 ToolUse 和 ToolResult 通过 id/tool_use_id 关联，需要更复杂的脚本
```

**优缺点对比**:

| 方案 | 优势 | 劣势 |
|------|------|------|
| **Transcript 方案** | 无需新增日志写入；已有数据 | 无耗时；Skill 覆盖不确定；解析稍复杂 |
| **JSON Lines 日志方案** | 包含耗时；字段完整；格式统一 | 需新增写入逻辑；数据冗余 |

**决策建议**:
- 仅需调用频率/成功率 → 用 Transcript 方案
- 需耗时统计 → 用 JSON Lines 日志方案

#### 3.9.10 基于使用频率的工具/Skill 裁剪

基于工具和 Skill 的使用频率统计，可自动识别并裁剪低使用率组件，减少 Bundle 大小和上下文开销。

**裁剪策略**:

| 策略 | 说明 |
|------|------|
| **自动隐藏** | 低频工具从默认 bundle 移到 `bare` 模式，需显式引用 |
| **提示建议** | 统计报告提示"X 工具过去 90 天仅使用 N 次，可考虑移除" |
| **按需加载** | 低频工具默认不加载，使用前需 `ExecuteExtraTool` 引用 |

**配置参数**:

```yaml
tool_pruning:
  enabled: true
  lookback_days: 90          # 统计回溯周期
  low_usage_threshold: 0.01  # 使用率 < 1% 则标记为低频
  cooldown_days: 30          # 工具存在 > 30 天才纳入裁剪统计
  action: "hide"             # "hide" | "suggest" | "remove"
```

**实现逻辑**:

```python
def get_rarely_used_tools(lookback_days=90, threshold=0.01, cooldown_days=30) -> list[str]:
    """返回应裁剪的工具列表"""
    stats = parse_transcript_stats(lookback_days=lookback_days)
    total = sum(stats.values())
    now = time.time()
    for name, count in stats.items():
        usage_rate = count / total
        if usage_rate < threshold:
            # 冷却期判断（工具创建时间 > cooldown_days）
            if tool_exists_longer_than(name, days=cooldown_days):
                yield name
```

**注意事项**:

| 注意点 | 说明 |
|--------|------|
| 学习曲线 | 新工具初期使用率低不代表价值低，需冷却期保护 |
| 核心工具 | `Read/Edit/Bash` 等高频核心工具不受影响 |
| 保留 fallback | 低频工具仍可通过 `bare` 模式访问 |

#### 3.9.11 POS to Agent 转化模式

将专业工作流（POS）拆解为 Agent 架构，实现工作流的可复用、可观测、可编排。

**三层映射关系**:

| 工作流组件 | Agent 架构 | 示例 |
|-----------|-----------|------|
| POS (专业系统) | Agent | 数据分析 Agent、CI/CD Agent、ML Pipeline Agent |
| 工作流步骤 | Skill | `deploy_service`、`run_etl`、`train_model` |
| SDK 接口 | 原子工具 | `s3_upload`、`k8s_apply`、`spark_submit` |

**架构示例**:

```
CI/CD Agent
├── Skill: build_image
│   ├── tool: docker_build()
│   ├── tool: docker_tag()
│   └── tool: docker_push()
├── Skill: deploy_service
│   ├── tool: k8s_apply()
│   ├── tool: health_check()
│   └── tool: rollback_if_failed()
└── Skill: notify_team
    ├── tool: slack_send()
    └── tool: email_send()
```

**转化过程（Skill + Template + Config）**:

| 层面 | 形式 | 说明 |
|------|------|------|
| **转化执行器** | Skill | 需要 LLM 判断如何分组、如何命名 |
| **产出物规范** | Template | Agent/Skill 定义的结构规范 |
| **映射规则** | Config | SDK method → tool 的映射表 |

```
Skill（执行器）+ Template（产出物规范）+ Config（映射规则）
```

**转化 Skill 示例**:

```python
class ConvertPOSToAgent:
    """将 POS SDK 转换为 Agent 的 Skill"""

    async def execute(self, sdk_spec: str, requirements: str) -> AgentDefinition:
        # 1. 解析 SDK 接口 → 需要理解 API 语义（LLM）
        atomic_tools = await self._parse_sdk_methods(sdk_spec)

        # 2. 按业务逻辑分组 → 需要判断相关性（LLM）
        skills = await self._group_into_skills(atomic_tools, requirements)

        # 3. 填充 Agent 定义模板
        return self._fill_template(skills)
```

**优势**:

| 优势 | 说明 |
|------|------|
| 可复用性 | 原子工具可在不同 Skill/Agent 间共享 |
| 可观测性 | 每步工具调用独立记录，便于调试 |
| 容错粒度 | 可在工具级别重试，而非整个工作流 |
| 动态编排 | Agent 可根据上下文选择不同的 Skill 执行路径 |

**与 F-18 CreateAgentTool 的关系**:

F-18 解决"工具创建工具"（Meta Tool 能力），此模式解决"工作流转化为 Agent"。两者结合可实现：SDK 接口 → 原子工具 → Skill 组合 → Agent 定义 → 动态注册。

**实现清单**:

| 文件 | 说明 |
|------|------|
| `src/pos_converter/__init__.py` | 模块入口 |
| `src/pos_converter/sdk_parser.py` | SDK 解析（支持 OpenAPI JSON / URL / 简单方法列表） |
| `src/pos_converter/skill_grouper.py` | Skill 分组（静态 MappingRule + LLM 辅助） |
| `src/pos_converter/agent_builder.py` | Agent 构建 + 持久化（`~/.clawcodex/agents/<name>.json`） |
| `src/pos_converter/convert_pos_skill.py` | `/convert-pos-to-agent` Skill 实现 |
| `src/pos_converter/templates.py` | 模板定义 |
| `src/skills_ext/bundled/pos_to_agent.py` | bundled skill 注册（解耦上游） |

**三层映射实现**:

```
SdkParser.parse()           → list[SdkMethod]  (原子工具)
SkillGrouper.group()       → list[SkillSpec]  (Skill 规范)
AgentBuilder.build()       → AgentDefinition (Agent 定义)
persist_converted_agent()   → ~/.clawcodex/agents/<name>.json
```

**使用方式**:

```bash
/convert-pos-to-agent docker_build,k8s_apply::CI/CD pipeline
```

#### 3.9.12 业务 Agent 长期使用（新窗口重连）

将 POS 转化的 Agent 作为主 Agent 长期使用，并支持在新窗口中重新连接。

**核心能力**:

| 能力 | 说明 | 实现 |
|------|------|------|
| **持久化** | Agent 定义保存到文件 | `~/.clawcodex/agents/<name>.json` |
| **主 Agent 指定** | 启动时指定使用哪个 Agent | `clawcodex --agent <name>` 或配置文件 |
| **窗口重连** | 新窗口连接到已运行的 Agent | Session ID / Named Pipe |

**Agent 持久化格式**:

```json
// ~/.clawcodex/agents/cicd-agent.json
{
  "name": "cicd-agent",
  "description": "自动化部署 Agent",
  "model": "claude-sonnet",
  "tools": ["k8s_apply", "docker_push", "health_check"],
  "skills": ["deploy_service", "rollback"],
  "memory_scope": ["project", "team"],
  "persistent": true
}
```

**启动方式**:

```bash
# 方式一：启动时指定
clawcodex --agent cicd-agent

# 方式二：配置为默认
# ~/.clawcodex/settings.json
{
  "default_agent": "cicd-agent"
}

# 方式三：daemon 模式长期运行
clawcodex --daemon --agent cicd-agent
# 新窗口 attach
clawcodex attach cicd-agent
```

**Daemon + Attach 架构**:

```
终端 1: clawcodex --daemon --agent cicd-agent
        └── cicd-agent 进程运行中，保持状态
               ↓
终端 2: clawcodex attach cicd-agent
        └── 连接到已有 Agent 会话，继续交互
```

**需要新增的组件**:

| 组件 | 文件 | 说明 |
|------|------|------|
| Agent 存储 | `src/agent/agent_persistence.py` | 读写 `~/.clawcodex/agents/` |
| Agent 加载器 | `src/agent/agent_loader.py` | 启动时加载指定 Agent |
| Attach 协议 | `src/agent/attach.py` | 连接到已有 Agent 会话 |

**与现有组件的集成**:

| 现有组件 | 集成点 |
|---------|--------|
| `agent/agent_definitions.py` | Agent 定义模型 |
| `agent/session.py` | Session 持久化 |
| `agent/run_agent.py` | 主 Agent 启动逻辑 |
| `repl/core.py` | REPL 启动入口 |
| `src/entrypoints/headless.py` | Daemon 模式支持 |

---

### 3.10 CreateAgentTool 动态工具创建

**状态**: 🔄 规划中
**目标**: Agent 可根据三方 CLI/API 规范动态创建工具，实现"工具创建工具"的 Meta Tool 能力

#### 3.9.1 功能说明

允许 Agent 分析第三方工具（CLI 命令或 HTTP API）的接口规范，然后动态创建一个可用的工具：

```
Agent 分析 CLI 规范 → 生成工具规范 → 调用 CreateAgentTool → 注册新工具 → 使用新工具
```

#### 3.9.2 架构设计

```
src/agent/tool_authoring/           # 新增模块（与上游解耦）
├── spec.py                         # AgentToolSpec 定义
├── validators.py                   # 规范验证器
├── factory.py                      # build_tool() 调用封装
├── registry_ext.py                 # Agent 创建工具注册表
├── persistence.py                  # 工具持久化
└── call_handlers/                  # call_impl 处理
    ├── bash.py                     # bash 命令调用
    ├── http.py                     # HTTP 请求调用
    └── python.py                   # Python 函数映射

src/tool_system/tools/
└── create_agent_tool.py            # CreateAgentTool 实现
```

#### 3.9.3 工具规范（AgentToolSpec）

```python
@dataclass(frozen=True)
class AgentToolSpec:
    name: str                          # 工具唯一名称
    description: str                   # 工具描述
    input_schema: dict                 # JSON Schema
    call_type: "bash" | "http" | "python"  # 调用类型
    call_impl: str | dict              # 实现（类型依赖）
    tags: list[str] = field(default_factory=list)  # 分类标签
    aliases: tuple[str, ...] = ()
    source: str = "agent-created"      # 来源标记
```

#### 3.9.4 三种 call_impl 安全限制

| call_type | call_impl 示例 | 安全级别 |
|-----------|---------------|---------|
| `bash` | `"git status --porcelain {path}"` | ✅ 占位符防注入，预定义命令白名单 |
| `http` | `{"method": "GET", "url": "https://api.github.com/{endpoint}"}` | ✅ 模板化，方法白名单 |
| `python` | `"fetch_data"` → 映射到预定义函数 | ⚠️ 仅白名单函数注册 |

**命令白名单（bash）**：`git`, `gh`, `glab`, `curl`, `wget`, `kubectl`, `docker`, `npm`, `pip`

**HTTP 方法白名单**：`GET`, `POST`, `PUT`, `DELETE`, `PATCH`

#### 3.9.5 CreateAgentTool 输入规范

```json
{
  "name": "my-gitlab-query",
  "description": "查询 GitLab 项目信息",
  "input_schema": {
    "type": "object",
    "properties": {
      "project_id": {"type": "string", "description": "项目 ID"}
    },
    "required": ["project_id"]
  },
  "call_type": "bash",
  "call_impl": "glab project view {project_id} --output json",
  "tags": ["gitlab", "project"],
  "aliases": ["glab-project"]
}
```

#### 3.9.6 安全性约束

| 约束类型 | 实现位置 | 说明 |
|---------|---------|------|
| 命令白名单 | `validators.py:_validate_bash_impl` | 仅允许预定义命令 |
| HTTP 方法白名单 | `validators.py:_validate_http_impl` | 仅白名单方法 |
| Python 函数注册 | `validators.py:_validate_python_impl` | 仅白名单函数 |
| 无任意代码执行 | `factory.py` | call_impl 是模板/映射，非代码 |
| 参数化防注入 | `call_handlers/bash.py` | format 替换，无 shell 注入 |
| 超时保护 | `call_handlers/bash.py` | subprocess timeout=30 |

#### 3.9.7 持久化机制

Agent 创建的工具保存到 `~/.clawcodex/agent-tools/{name}.json`，重启后自动加载。

#### 3.9.8 与现有系统集成

| 现有组件 | 如何协作 |
|---------|---------|
| `build_tool()` | 作为工厂函数，CreateAgentTool 调用它 |
| `ToolRegistry` | 工具创建后调用 `registry.register(tool)` |
| `parse_agent_markdown` | 已有工具定义解析，可复用 schema 验证 |
| MCP 工具包装 | 参考 `tool_wrapper.py` 的声明式工具模式 |
| `resolve_agent_tools()` | 允许 `source="agent-created"` 的工具被解析 |

#### 3.9.9 实现文件

| 文件 | 位置 | 状态 |
|------|------|------|
| `tool_authoring/spec.py` | `src/agent/tool_authoring/` | 规划中 |
| `tool_authoring/validators.py` | `src/agent/tool_authoring/` | 规划中 |
| `tool_authoring/call_handlers/bash.py` | `src/agent/tool_authoring/` | 规划中 |
| `tool_authoring/call_handlers/http.py` | `src/agent/tool_authoring/` | 规划中 |
| `tool_authoring/factory.py` | `src/agent/tool_authoring/` | 规划中 |
| `tool_authoring/registry_ext.py` | `src/agent/tool_authoring/` | 规划中 |
| `tool_authoring/persistence.py` | `src/agent/tool_authoring/` | 规划中 |
| `create_agent_tool.py` | `src/tool_system/tools/` | 规划中 |

---

### 3.9 sessionStorage 容量限制

**状态**: ⏳ 待实现
**目标**: 防止长时间运行的 daemon/swarm 会话导致内存泄漏

#### 3.9.1 功能说明

为 `existingSessionFiles` Map 设置容量上限，防止无限增长：

```python
MAX_CACHED_SESSION_FILES = 200

def add_session_file(sessionId: UUID, filePath: str):
    if len(existingSessionFiles) >= MAX_CACHED_SESSION_FILES:
        oldest_key = next(iter(existingSessionFiles))
        del existingSessionFiles[oldest_key]
    existingSessionFiles[sessionId] = filePath
```

#### 3.9.2 问题场景

- daemon/swarm 模式下长时间运行
- sessionId 频繁创建销毁
- Map 无限增长导致 OOM

#### 3.9.3 实现文件

| 文件 | 位置 | 状态 |
|------|------|------|
| sessionStorage | `utils/sessionStorage.ts` → `utils/session_storage.py` | 待实现 |

---

### 3.10 cacheWarning 容量限制

**状态**: ⏳ 待实现
**目标**: 防止 querySource 类型为 any 时内存泄漏

#### 3.10.1 功能说明

为 `cacheWarningStateBySource` Map 设置容量上限：

```python
MAX_SOURCE_ENTRIES = 50

def update_cache_warning(source: str, state: CacheWarningState):
    if len(cacheWarningStateBySource) >= MAX_SOURCE_ENTRIES:
        oldest_key = next(iter(cacheWarningStateBySource))
        del cacheWarningStateBySource[oldest_key]
    cacheWarningStateBySource[source] = state
```

#### 3.10.2 问题场景

- querySource 类型为 any
- 长时间会话产生大量唯一 source 值
- Map 无限增长导致内存泄漏

#### 3.10.3 实现文件

| 文件 | 位置 | 状态 |
|------|------|------|
| cacheWarning | `utils/cacheWarning.ts` → `utils/cache_warning.py` | 待实现 |

---

### 3.11 Issue 语义澄清流程（自主模式扩展）

**状态**: ✅ 已完成（F-1.5~F-1.11，Phase A-G 全部完成）
**优先级**: P1
**目标**: 当 Issue 语义模糊时，通过**三通道优先机制**获取澄清——本地操作员（Dashboard/ClarificationQueue）优先，作者 @mention 兜底

> 三通道优先机制（Dashboard / ClarificationQueue / @mention）、平台能力对比、整体流程图、各通道详细设计、ClarificationStatus 枚举（含冲突处理 `DUPLICATE_REJECTED` / `STALE_REJECTED` / `CONFLICT_RESOLVED`）、多渠道冲突处理状态机、CLI `clarify` 命令、TrackerAdapter 评论接口与 GitHub/Gitee/GitCode 实现、IssueRegistry 澄清字段持久化、PromptBuilder 澄清内容注入、escalation 策略与配置等已归档。
> 详见 [ARCHIVED_FEATURES.md §16.5（Issue 语义澄清流程）](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成) 与对应进度归档 [ARCHIVED_PROGRESS.md F-1.x 子特性](./ARCHIVED_PROGRESS.md#f-1x-orchestrator-自主模式f-1-子特性全部完成)。

---


### 3.13 Auto 模式 (TRANSCRIPT_CLASSIFIER)

**状态**: ⏳ 待实现
**优先级**: P2
**目标**: 基于 LLM 的自动权限模式切换，减少交互疲劳

#### 3.13.1 功能说明

Auto 模式是一种智能权限模式，通过 LLM 分类器（TRANSCRIPT_CLASSIFIER）自动判断何时允许执行敏感操作。在长时间任务或重复性操作场景下，Auto 模式可以减少用户确认的交互频率。

#### 3.13.2 工作原理

```
用户启动 Auto 模式
        ↓
Agent 执行工具调用时触发分类器
        ↓
TRANSCRIPT_CLASSIFIER 分析:
  - 工具类型 (Bash/Write/Edit/etc.)
  - 命令内容 (是否危险)
  - 执行上下文 (当前目录/文件类型)
  - 历史行为模式
        ↓
分类决策:
  - Auto-Allow: 直接执行，无需确认
  - Auto-Deny: 静默拒绝或降级
  - Fallback to Ask: 无法判断时回退到 ask 模式
        ↓
记录分类结果用于后续判断
```

#### 3.13.3 与手动模式的区别

| 模式 | 触发方式 | 确认频率 | 适用场景 |
|------|---------|---------|---------|
| `default` | 手动确认每个敏感操作 | 高 | 学习/审查模式 |
| `acceptEdits` | 手动确认写操作 | 中 | 代码迭代 |
| `plan` | 仅读取，编辑前分析 | 低 | 探索代码库 |
| `auto` | LLM 自动判断 | 自动调节 | 长任务/减少疲劳 |
| `bypassPermissions` | 无限制 | 无 | 隔离环境 |

#### 3.13.4 循环切换逻辑（已实现部分）

`Shift+Tab` 循环切换顺序：
```
default → acceptEdits → plan → bypassPermissions (如果可用) → default
```

注意：`auto` 模式不出现在手动循环中，需要通过 `--permission-mode auto` 启动或由分类器自动触发。

#### 3.13.5 待实现组件

| 组件 | 文件 | 说明 |
|------|------|------|
| TRANSCRIPT_CLASSIFIER | `permissions/classifier.py` | LLM 分类器核心 |
| canCycleToAuto | `permissions/cycle.py` | 判断是否可切换到 auto |
| Auto Mode 集成 | `agent/run_agent.py` | 在工具执行前调用分类器 |
| 分类结果缓存 | `permissions/cache.py` | 避免重复分类 |

#### 3.13.6 分类器 prompt 设计

```python
AUTO_MODE_CLASSIFIER_PROMPT = """
你是一个安全分类器，判断以下工具调用是否可以在 auto 模式下自动执行。

工具: {tool_name}
命令: {command}
当前目录: {cwd}
文件类型: {file_type}

考虑因素:
1. 工具类型 (Read/Glob/Grep 安全, Bash/Write/Edit 需谨慎)
2. 命令是否包含危险操作 (rm -rf, sudo, 破坏性命令)
3. 目标路径是否在保护目录内 (.git, .vscode, .clawcodex)
4. 历史行为模式 (是否重复执行类似操作)

输出格式:
- AUTO_ALLOW: 可以自动执行
- AUTO_DENY: 应拒绝执行
- ASK_USER: 无法判断，需要用户确认

决策: {decision}
原因: {reasoning}
"""
```

#### 3.13.7 实施阶段

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase A1 | TRANSCRIPT_CLASSIFIER 核心实现 | P2 | ⏳ 待开始 |
| Phase A2 | `canCycleToAuto()` 判断逻辑 | P2 | ⏳ 待开始 |
| Phase A3 | Auto Mode 工具执行前集成 | P2 | ⏳ 待开始 |
| Phase A4 | 分类结果缓存机制 | P3 | ⏳ 待开始 |

---

### 3.14 Agent 间自主观察与消息交互

**状态**: ✅ 已完成（Phase M1-M5 全部完成）
**优先级**: P1
**目标**: 实现 Manager Agent 全自动观察 Worker Agent 状态并注入指令，支持优先级队列和权限审批

> 角色定义（Manager / Worker 通过工具组合自动识别）、核心工具（`TaskInspect` + `TaskDirectives`）、优先级队列（`queue_pending_message` priority 字段 + `drain_pending_messages` 按优先级消费）、工具可见性过滤（仅 Manager 可调用）、权限规则传递与 Phase M1-M5 实施阶段已归档。
> 详见 [ARCHIVED_FEATURES.md §十八（Agent 间自主观察与消息交互）](./ARCHIVED_FEATURES.md#十八agent-间自主观察与消息交互) 与对应进度归档 [ARCHIVED_PROGRESS.md F-29（TaskInspect/TaskDirectives 工具注册）](./ARCHIVED_PROGRESS.md#f-29-taskinspecttaskdirectives-工具注册)。


### 3.12 Orchestrator CLI 运维操作界面

**状态**: ✅ 已完成（F-1.13，Phase O1-O8 全部完成）
**优先级**: P1
**目标**: 通过 `clawcodex orchestrator` 统一入口，实现运行期间的全程可视化监控与中途介入
---

### 3.15 CLI 模型供应商与模型切换设计（F-43）

**状态**: 📋 设计完成
**优先级**: P1
**跟踪文档**: `docs/PROGRESS.md` → `F-43: CLI 模型供应商与模型切换`

#### 目标

新增 `clawcodex provider` 与 `clawcodex model` 两个子命令族，让用户能在 CLI 内**查看、切换、列出**当前生效的 LLM 供应商与模型；并在 REPL/TUI 内部以 `/provider` 与 `/model` 斜杠命令提供运行期热切换。所有新代码落在 `clawcodex_ext/cli/` 下，不动 `src/*` 或 `extensions/*`。

#### 背景与问题

- 一次性覆盖：CLI 已支持 `--provider NAME` / `--model NAME`（`parser.py:88-99`），仅对本次调用生效；想换默认需要重跑 `login`
- 持久化入口耦合：仅 `runners.py:120-191` 的 `handle_login` 在配凭证时同步写 `default_model`，没有独立的"切换默认模型"命令
- 没有 `clawcodex model show` 这类查询入口，用户看不到当前生效的 provider / model
- REPL/TUI 运行期无法热切换：`RuntimeContext` 只在启动时构造一次
- 解析优先级在 `RuntimeContext.build` 中硬编码 "CLI flag > default_provider > provider default_model"，无法扩展环境变量 / 项目级 scope

#### 子命令形态

```
clawcodex provider
  list
  show [NAME]                       # NAME 省略时显示当前
  current
  use NAME [--scope user|project]
  unset

clawcodex model
  list [--provider NAME]
  show [NAME] [--provider NAME]
  current
  use NAME [--provider NAME] [--scope user|project]
```

要点：

- 全部为 fast-path 子命令（在 `dispatch.py:argv[0]` 分支中注册），不走 argparse
- `--scope project` 是后续议题（G-1），第一版只实现 `user`（全局）
- `provider use` 不重写 API key / base_url，只动 `default_provider`；`model use` 只动指定 provider 的 `default_model`
- `login` 子命令行为保留，内部把"保存 default_model"委托给新模块 setter

#### 目录与模块划分

新增内容全部在 `clawcodex_ext/cli/` 下：

```
clawcodex_ext/cli/
├── main.py                  # 已有
├── parser.py                # 已有（不需改）
├── dispatch.py              # 改动一处：fast-path 改查表
├── runners.py               # 已有（不需改）
├── permissions.py           # 已有（不需改）
├── subcommand_registry.py   # 新增：SUBCOMMANDS 表 + @register 装饰器
├── provider_cmd/
│   ├── __init__.py
│   ├── commands.py          # list / show / current / use / unset
│   └── errors.py
└── model_cmd/
    ├── __init__.py
    ├── registry.py          # 包装 PROVIDER_INFO
    ├── resolver.py          # 解析优先级
    ├── store.py             # 通过 src.config 持久化
    ├── commands.py          # list / show / current / use
    └── errors.py
```

`subcommand_registry.py` 是关键解耦点：`@register("provider")` / `@register("model")` 让 `provider_cmd` / `model_cmd` 自注册，`dispatch.py` 改为查表。

#### 核心数据结构

```python
# model_cmd/registry.py
@dataclass(frozen=True)
class ModelSpec:
    provider: str
    name: str
    base_url: str | None = None
    api_key_present: bool = False

class ModelRegistry:
    def list_providers(self) -> list[ProviderInfo]: ...
    def get_provider_info(self, name: str) -> ProviderInfo: ...
    def list_models(self, provider: str) -> list[str]: ...
    def resolve_model(self, provider: str, name: str) -> ModelSpec: ...   # 校验白名单
    def has_credentials(self, provider: str) -> bool: ...
```

```python
# model_cmd/resolver.py
@dataclass(frozen=True)
class Resolution:
    provider: str
    model: str
    source: Literal["cli", "env", "project", "user_default_provider", "provider_default"]

def resolve(*, cli_provider, cli_model, project_root) -> Resolution: ...
```

```python
# model_cmd/store.py
class ModelStore:
    def set_default_provider(self, name: str) -> None: ...
    def set_default_model(self, provider: str, model: str) -> None: ...
    def get_default_provider(self) -> str | None: ...
    def get_default_model(self, provider: str) -> str | None: ...
```

#### 解析优先级

| 序 | 来源 | 字段 |
|----|------|------|
| 1 | CLI 标志 `--provider` / `--model` | `cli_provider` / `cli_model` |
| 2 | 环境变量 `CLAWCODEX_PROVIDER` / `CLAWCODEX_MODEL` | env |
| 3 | 项目级 config（未来 G-1） | project |
| 4 | 用户全局 config `default_provider` | user |
| 5 | 用户全局 config `providers[provider].default_model` | user |
| 6 | `PROVIDER_INFO[provider].default_model` | builtin fallback |

每次解析都记录 `source`，用于 `model current` 输出形如 `provider: glm [user]`。

#### 存储模型

**第一版只实现 user scope（全局）：**

- 读：`src.config.load_config` / `get_provider_config` / `get_default_provider`
- 写：`src.config.set_default_provider` 与 `set_api_key(provider, default_model=X)`（保留其它字段）

**项目级 scope（`--scope project`）作为后续 G-1 议题：**

- 落到 `<project>/.clawcodex/config.local.json`（默认加入 `.gitignore`）
- `store.py` 接口预留 `scope` 参数，避免后续大改签名

#### REPL / TUI 斜杠命令

REPL 与 TUI 的 `/provider` / `/model` 斜杠命令复用 `model_cmd.resolver` + `model_cmd.store`：

- `/provider list` / `/model list` → 复用 `cmd_*_list`
- `/provider <name>` / `/model <name>` → 复用 `cmd_*_use`，并通过新增的 `RuntimeContext.swap_provider(provider, model)` 触发运行时切换
- `swap_provider` 重建 provider + 复用 session ID + 重建 tool registry（仅当工具绑定 model context 时）
- 错误处理：复用 `provider_cmd.errors` / `model_cmd.errors` 的英文文案

#### 与现有代码的关系

| 既有模块 | 关系 |
|----------|------|
| `parser.py` | 不动。新子命令走 fast-path |
| `dispatch.py:run_cli` | 改一行：fast-path 改查表 |
| `runners.py:handle_login` | 不动。`model use` 与之并存 |
| `RuntimeContext.build` | 不动。本方案只新增友好入口 |
| `extensions/providers_ext` | 不动。正交 |
| `src.providers.PROVIDER_INFO` / `src.config` | 只读不写 |

唯一需要修改 `src/*` 的是 `dispatch.py` 的一行（fast-path 改查表）；其余都在 `clawcodex_ext/cli/`。

#### 错误模型（统一英文）

| 异常 | 触发条件 | 文案 |
|------|----------|------|
| `UnknownProviderError` | provider 不在 `PROVIDER_INFO` | `unknown provider: <name>. available: <list>` |
| `UnknownModelError` | model 不在 `available_models` | `model <name> is not in <provider>'s available models. pick one of: <list>` |
| `ProviderMismatchError` | `--provider` 与 model 默认 provider 不一致且无显式 `--provider` | `<model> belongs to <other-provider>, not <provider>. pass --provider <other-provider> or pick a model from <provider>.` |
| `NotConfiguredError` | 切换时无凭证 | `provider <name> has no API key configured. run \`clawcodex login\` first.` |

所有错误统一在 `provider_cmd/errors.py` 与 `model_cmd/errors.py` 定义；`commands.py` 捕获后用 `rich.console` 打印，exit code = 2。

#### 后续规划（推迟到 G-1 / v2.13+）

- `clawcodex provider use --scope project` 落入 `<project>/.clawcodex/config.local.json`
- `clawcodex model use --scope project` 同上
- 项目级 scope 的 resolver 优先级插在 user 之前
- 多窗口并发写盘的 `fcntl` 文件锁

#### 测试策略

| 测试 | 覆盖点 |
|------|--------|
| `test_resolver.py` | 6 级优先级矩阵；env 覆盖；非法 provider/model 抛错 |
| `test_store.py` | round-trip 读写；`set_default_model` 不影响 `api_key` / `base_url`；注入 mock config 模块隔离磁盘 |
| `test_provider_commands.py` / `test_model_commands.py` | `capsys` 抓 stdout，断言表格 / 错误信息；mock `Console` 避免终端 |
| `test_subcommand_registry.py` | 注册 / 重复注册 / 未注册命令的 fallback 行为 |
| `test_dispatch_integration.py` | `clawcodex provider list` / `clawcodex model use zai/glm-4` 端到端跑通 |
| `test_slash_commands.py` | REPL / TUI 内 `/provider` / `/model` 触发 `swap_provider`；mock `RuntimeContext` 验证调用 |
| 手工 smoke | 真实 `clawcodex -p "hi" --provider glm --model zai/glm-4` 验证切换生效 |

#### 风险与约束

1. **写盘并发**：现有 `src.config` 没有文件锁；第一版接受 "最后写者赢"，G-1 加 `fcntl` 锁
2. **`--model` 与子命令 `model` 同名**：fast-path 只看 `argv[0]`，无歧义；未来 argparse 接管需重新审视
3. **环境变量命名**：建议 `CLAWCODEX_PROVIDER` / `CLAWCODEX_MODEL`，与现有 `CLAW_USE_LITELLM` / `CLAUDE_CONFIG_DIR` 一致
4. **REPL/TUI 热切换**：本方案实现 `/provider` / `/model` 与 `swap_provider`；后续若 `swap_provider` 影响 tool registry 行为，需单测覆盖
5. **`login` 仍可写 `default_model`**：保持原行为，文档化 "用 `clawcodex model use` 更轻量"
6. **`runners.py:_show_provider_defaults_table` 与新 `provider list` 重复**：G-1 合并；第一版接受短期重复

#### 实施阶段

| 阶段 | 内容 | 依赖 |
|------|------|------|
| 1 | `subcommand_registry.py` 注册表骨架 + `dispatch.py` 接入 | 无 |
| 2 | `model_cmd` 核心（registry / errors / resolver / store） + 单测 | 阶段 1 |
| 3 | `model_cmd/commands.py`（list / show / current / use） | 阶段 2 |
| 4 | `provider_cmd` 5 个 handler | 阶段 2、3 |
| 5 | REPL `/provider` / `/model` 斜杠命令 + `RuntimeContext.swap_provider` | 阶段 3 |
| 6 | TUI `/provider` / `/model` 斜杠命令 | 阶段 5 |
| 7 | 端到端测试 + 文档 | 阶段 6 |

---

## 四、开源替代路线图

### 4.1 已完成（✅）

| 组件 | 替代方案 | 代码减少 | 完成日期 |
|------|---------|----------|----------|
| 配置系统 | Pydantic-settings | ~220 行 | 2026-05-17 |
| Frontmatter 解析 | python-frontmatter | ~80 行 | 2026-05-17 |
| Bash AST 解析器 | tree-sitter-bash | ~1,400 行 | 2026-05-17 |
| Git 操作 | GitPython | ~200 行 | 2026-05-17 |
| Hook 系统 | Pluggy | ~1,000 行 | 2026-05-17 |
| 结构化输出 | Outlines | ~200 行 | 2026-05-17 |

### 4.2 待实施（⏳）

| 组件 | 替代方案 | 代码减少 | 优先级 | 状态 |
|------|---------|----------|--------|------|
| Provider 层 | LiteLLM | ~1,430 行 | P0 | ✅ 已完成（2026-05-30） |
| 工具语义搜索 | Qdrant | ~100 行 | P2 | 规划中 |
| 权限规则引擎 | Casbin | ~150 行 | P2 | 规划中 |
| 日志系统 | structlog | - | P2 | 规划中 |

### 4.3 不可替代组件

| 组件 | 原因 |
|------|------|
| Agent 执行循环 | 四级权限模型、Subagent 隔离、消息完整性保证 |
| MCP 服务 | 已完整实现，替换成本过高 |
| Trust Boundary | 项目特定安全策略 |
| Bridge/FlushGate | 最解耦模块，替换无意义 |

---

## 五、CLI 扩展规划

### 5.1 当前 CLI 结构

```bash
clawcodex                    # 默认 REPL（prompt_toolkit + Rich）
clawcodex --tui             # Textual TUI
clawcodex -p "prompt"       # 头速/非交互模式
clawcodex login             # API key 配置
clawcodex config            # 配置查看
clawcodex mcp/daemon/doctor # 子命令
```

### 5.2 Orchestrator 子命令（统一入口）

> `clawcodex orchestrator` 采用 noun-verb 结构：`server start/status/stop` 管理 daemon，`issue list/show/tail/stop/pause/resume/takeover/clarify/inject/workspace --id <id>` 管理 issue。

```bash
clawcodex orchestrator                    # Orchestrator 所有操作的统一入口
│
├── run               # 启动 orchestrator（替代原有 --workflow）
│   └── --workflow, --dashboard, --port
├── status           # 全局状态总览
│   └── --watch（实时监控）
├── issues           # issue 操作
│   ├── list         # 列出所有 issue（含状态）
│   ├── show <id>    # 某个 issue 的详细信息
│   └── tail <id>    # 实时 tail 某个 issue 的 tool call 日志
├── pause <id>       # 暂停某个 issue 的 agent
├── resume <id>      # 恢复暂停中的 agent
├── stop <id>        # 强制终止 agent
├── takeover <id>    # 完全接管（终止 agent + 启动 REPL）
├── inject           # 向运行中的 agent 注入提示
│   ├── <id> "hint text"
│   ├── --list       # 查看已注入的提示
│   └── --remove <id># 删除某条提示
├── clarify          # 澄清应答（操作员本地回答）
│   └── --issue <id> --answer <text>
├── workspace        # workspace 文件操作
│   ├── <id> --ls    # 列出文件
│   ├── <id> --cat <file>   # 查看文件
│   └── <id> --edit <file> --with <content>  # 编辑文件
└── dashboard       # 启动独立 dashboard UI
    └── --port
```

### 5.3 CLI 扩展总览

| 命令 | 说明 | 状态 |
|------|------|------|
| `clawcodex orchestrator server start --workflow PATH` | 启动 orchestrator daemon | ✅ 完成 |
| `clawcodex orchestrator server status` | 查看 daemon 运行状态 | ✅ 完成 |
| `clawcodex orchestrator server stop` | 停止 orchestrator daemon | ✅ 完成 |
| `clawcodex orchestrator issue list [--status]` | 列出 issue 及状态 | ✅ 完成 |
| `clawcodex orchestrator issue show --id <id>` | 查看 issue 详情 | ✅ 完成 |
| `clawcodex orchestrator issue tail --id <id>` | 实时 tail 日志 | ✅ 完成 |
| `clawcodex orchestrator issue pause --id <id>` | 暂停 agent | ✅ 完成 |
| `clawcodex orchestrator issue resume --id <id>` | 恢复 agent | ✅ 完成 |
| `clawcodex orchestrator issue stop --id <id>` | 终止 agent | ✅ 完成 |
| `clawcodex orchestrator issue takeover --id <id>` | 会话接管 | ✅ 完成 |
| `clawcodex orchestrator issue inject --id <id> <hint>` | 操作员 Hint 注入 | ✅ 完成 |
| `clawcodex orchestrator issue clarify --id <id> --answer <text>` | 澄清应答 | ✅ 完成 |
| `clawcodex orchestrator issue workspace --id <id> --ls` | workspace 文件查看 | ✅ 完成 |
| `clawcodex orchestrator dashboard [--port PORT]` | 独立 dashboard UI | ✅ 完成 |

---

## 六、数据流与架构

### 6.1 交互模式数据流

```
用户输入 → REPL/TUI → QueryEngine → Provider → LLM
                                    ↓
                              ToolSystem (30+ 工具)
                                    ↓
                              权限检查 → 工具执行 → 结果返回
```

### 6.2 自主模式数据流

```
WORKFLOW.md → Orchestrator → LinearAdapter (轮询 Issue)
                              ↓
                    WorkspaceManager (创建工作区)
                              ↓
                    AgentRunner → QueryEngine → ToolSystem
                              ↓
                    ApprovalPolicy (工具审批)
                              ↓
                    LinearAdapter (更新 Issue 状态)
```

---

## 七、测试策略

### 7.1 测试框架

- **pytest**: 主测试框架
- **测试规模**: 37 个测试文件，~10,480 行

### 7.2 关键测试覆盖

| 模块 | 测试文件 | 覆盖内容 |
|------|----------|----------|
| Pydantic Adapter | `test_pydantic_adapter.py` | 9 个测试 |
| Frontmatter Adapter | `test_frontmatter_adapter.py` | 9 个测试 |
| Treesitter Adapter | `test_treesitter_adapter.py` | 16 个测试 |
| GitPython Adapter | `test_gitpython_adapter.py` | 9 个测试 |
| Team File | `test_team_file.py` | members 数组测试 |
| Team Membership | `test_team_membership.py` | lead 判定测试 |

### 7.3 安全测试

- **Bash 安全**: 18 个 validator，163 个测试用例

---

## 八、文档索引

| 文档 | 说明 |
|------|------|
| `docs/FEATURE_PLAN.md` | 本文档 - 特性规划总览 |
| `docs/PROGRESS.md` | 进度跟踪文档 |
| `docs/INTEGRATION.md` | Symphony 集成规范 |
| `docs/TEAM_MEMBERSHIP.md` | Team 成员扩展设计 |
| `docs/clawcodex-opensource-replacement-analysis-v2.md` | 开源替代分析（已归档） |
| `docs/clawcodex_vs_ccb_analysis-v3.md` | 与 CCB 对比分析（已归档） |

---

---

## 九、Cron 系统执行引擎（F-22 完整迁移版）

> 优先级: P0  
> 状态: 设计重整，待实现收敛  
> 目标: 完整还原 `claude-code-best` 的 Cron / scheduled-task 行为  
> 下游边界: 业务实现默认进入 `clawcodex_ext/*`，`src/*` 仅允许 thin forwarding seams

### 9.1 背景与目标

本阶段不是新增一个简单的 `CronCreate/CronList/CronDelete` CRUD 工具，而是将 `claude-code-best` 中已经打通的定时任务系统完整迁移到 ClawCodex 的下游扩展层。最终用户应能在 REPL、TUI、headless/print 模式中创建、查看、删除和执行定时任务，并能查看定时任务触发后的运行状态与结果。

`claude-code-best` 的 Cron 行为跨越工具、存储、调度器、CLI skills、REPL/headless 执行队列、autonomy run 记录和 missed-task 安全确认。ClawCodex 当前已经有 `clawcodex_ext/cron_system/*` 的核心模块，但还没有把这些模块完整接入真实 CLI 运行路径，因此 F-22 的完成标准必须从“模块存在”提升为“端到端行为与 `claude-code-best` 对齐”。

### 9.2 参考实现边界

迁移时以 `claude-code-best` 的以下文件作为行为来源：

| 能力 | `claude-code-best` 参考文件 | 迁移关注点 |
|------|-----------------------------|------------|
| Cron 工具 | `packages/builtin-tools/src/tools/ScheduleCronTool/CronCreateTool.ts` | schema、cron 校验、durable 处理、返回字段、启用 scheduler |
| Cron 列表 | `packages/builtin-tools/src/tools/ScheduleCronTool/CronListTool.ts` | session + durable 聚合、teammate 过滤、展示字段 |
| Cron 删除 | `packages/builtin-tools/src/tools/ScheduleCronTool/CronDeleteTool.ts` | ID 校验、权限/归属校验、删除语义 |
| Feature gate | `packages/builtin-tools/src/tools/ScheduleCronTool/prompt.ts` | `CLAUDE_CODE_DISABLE_CRON`、durable gate、工具名常量 |
| 存储模型 | `src/utils/cronTasks.ts` | session-only 与 durable 分离、`.claude/scheduled_tasks.json`、8 位 ID |
| 调度器 | `src/utils/cronScheduler.ts` | 1 秒轮询、busy gate、scheduler lock、missed one-shot、filter、`onFireTask` 优先级 |
| REPL 集成 | `src/hooks/useScheduledTasks.ts` | scheduled task 入队、系统消息、去重、pending notification |
| Headless 集成 | `src/cli/print.ts` | print 模式定时任务入队、teammate 任务失败记录 |
| `/loop` | `src/skills/bundled/loop.ts` | interval 解析、默认 10m、创建后立即执行一次 |
| 管理命令 | `src/skills/bundled/cronManage.ts` | `/cron-list`、`/cron-delete` 用户可调用 skill |
| 运行记录 | `src/utils/autonomyRuns.ts` | queued/running/completed/failed/cancelled 生命周期 |
| 状态展示 | `src/utils/autonomyStatus.ts` | cron section、runs/status 输出 |
| 系统消息 | `src/utils/messages.ts` | `scheduled_task_fire` 消息类型 |

### 9.3 当前 ClawCodex 状态诊断

#### 9.3.1 fallback 工具层

`src/tool_system/tools/cron.py` 目前只是兼容用 fallback：

- 任务保存在 `ToolContext.crons` 的进程内 dict 中。
- `durable` 参数会被接受并返回，但不会写入 `.claude/scheduled_tasks.json`。
- 不验证 5 字段 cron 语义，只检查字符串非空。
- `humanSchedule` 直接返回原始 cron 字符串。
- 没有 scheduler，不会自动触发任务。
- `CronCreateTool` / `CronDeleteTool` 被标记为 read-only，但实际会修改上下文状态。

该层应继续保留为静态工具兼容 fallback，但不应作为完整 Cron 行为的实现主体。

#### 9.3.2 下游扩展核心模块

`clawcodex_ext/cron_system/*` 已经具备可复用基础：

```
clawcodex_ext/cron_system/
├── models.py          # CronFields、CronTask、路径、默认 max-age/jitter
├── parser.py          # 5 字段 cron 解析、next run、human schedule
├── tasks.py           # 文件存储 CRUD、due/missed/prune、storage lock
├── lock.py            # scheduler/storage filesystem lock
├── jitter.py          # deterministic jitter
├── notifications.py   # missed one-shot notification
├── scheduler.py       # scheduler thread + check_once
├── tools.py           # replacement CronCreate/CronList/CronDelete
└── runtime.py         # replace_cron_tools + attach_cron_runtime
```

这些模块是 F-22 后续实现的主战场。优先补齐语义差异，而不是把逻辑迁回 `src/*`。

#### 9.3.3 关键运行路径断点

目前最大缺口是 runtime/frontend 接线：

1. `clawcodex_ext/runtime/context.py` 构造 `RuntimeContext`，调用 `replace_cron_tools(tool_registry)`，并 `attach_cron_runtime(runtime)`。
2. 但 `clawcodex_ext/frontend/repl.py`、`clawcodex_ext/frontend/headless.py`、`clawcodex_ext/frontend/tui.py` 只把 options 传给旧入口。
3. 旧入口内部又重新构造 `tool_registry` 和 `tool_context`，导致前一步准备好的 Cron replacement tools、scheduler、outbox 没有进入真实执行路径。
4. `attach_cron_runtime()` 默认 `autostart=False`，即便被挂载也不会启动 scheduler。
5. scheduler 触发后只是向 `tool_context.outbox` 追加 `cron_prompt` / `cron_missed` 事件，当前没有发现 REPL/TUI/headless drain outbox 并执行 prompt 的路径。

因此当前扩展 Cron 更接近“有测试覆盖的核心模块”，尚未达到 `claude-code-best` 的 CLI 级完整行为。

### 9.4 完整还原的目标行为

#### 9.4.0 2026-06 最新 CCB 对比缺口复核

本轮复核同时查看了 `claude-code-best` 的 `src/utils/cron.ts`、`src/utils/cronTasks.ts`、`src/utils/cronScheduler.ts`、`src/utils/cronTasksLock.ts`、`src/utils/cronJitterConfig.ts`、`packages/builtin-tools/src/tools/ScheduleCronTool/*`、`src/skills/bundled/cronManage.ts`，以及 ClawCodex 的 `src/tool_system/tools/cron.py` 与 `clawcodex_ext/cron_system/*`。结论是：ClawCodex 已经不再只是 `src/tool_system/tools/cron.py` 的内存型 fallback；扩展层已经实现了多数底层语义，包括 5 字段 cron 解析、durable/session task 存储、storage/scheduler lock、deterministic jitter、permanent task、missed one-shot notification、基础 run store、status 表格、kill switch、event hooks 和 in-flight 防重。

但 `claude-code-best` 的 cron 是产品级端到端链路：工具创建任务后会启用 scheduler，scheduler 按 REPL/headless lifecycle 运行，due task 会进入真实用户 prompt 队列，run 账本从 queued 原子切换到 running/completed/failed/cancelled，`/cron-list`、`/cron-delete`、autonomy status/runs 等用户入口能解释任务和执行结果。ClawCodex 当前的主要缺口不在 G1~G8 这类底层函数，而在“扩展模块是否进入真实 CLI 路径并消费执行结果”。因此 F-22 仍应保持“进行中”，完成口径必须是端到端 smoke 通过，而不是 cron 单元测试通过。

最新剩余缺口如下：

| 缺口 ID | 缺口 | 对标 `claude-code-best` 行为 | ClawCodex 当前状态 | 补齐要求 |
|---------|------|------------------------------|--------------------|----------|
| F22-R1 | 真实 frontend/runtime 接线 | REPL/headless 启动时使用同一套工具 registry、tool context、scheduler lifecycle | `clawcodex_ext/cron_system/runtime.py` 可替换工具并挂 scheduler，但旧 REPL/TUI/headless 入口仍可能重建 registry/context，导致 fallback 工具和扩展 scheduler 脱节 | REPL/TUI/headless 全部接受并使用预构造 `RuntimeContext`；启动 scheduler，退出释放 lock；测试证明 `CronCreate` 命中扩展实现 |
| F22-R2 | scheduled fire 执行队列 | `useScheduledTasks` / print 模式把 due prompt 注入真实 query 队列并渲染 scheduled-task 系统消息 | scheduler 目前主要向 `tool_context.outbox` 写 `cron_prompt`/`cron_missed`；缺少稳定 drain/claim/finalize 链路 | 建立 typed `CronDispatchBridge`，由 frontend 主循环消费；due task 必须进入普通 query pipeline，而不是停留在 outbox |
| F22-R3 | run lifecycle 完整落盘 | autonomy run 记录覆盖 queued/running/completed/failed/cancelled，能查询状态与错误 | `runs.py`/`status.py` 已有基础账本，但未与真实执行队列 finalize 接线，字段也窄于 CCB autonomy run | queue consumer claim 时写 running；query 成功/失败/取消后写 completed/failed/cancelled；补齐 root/current dir、prompt preview、source、error、ownership/session 字段 |
| F22-R4 | 用户管理入口 | `/cron-list`、`/cron-delete` 是用户可调用 skill；状态入口能区分 job 定义、trigger detail、run history | `/loop` 已存在；`/cron-list`、`/cron-delete`、trigger detail/manual fire、autonomy status/runs richer output 仍待接线或扩展 | 在下游 skill/command 层注册用户入口；表格展示 job；manual fire 返回 run id；status/runs 使用真实 run store |
| F22-R5 | busy gate / assistant/headless/filter 语义 | scheduler 支持 `isLoading`、`assistantMode`、`filter`，忙碌时延后执行，daemon 可过滤 permanent task | 当前 scheduler 有 kill switch 与 event hooks，但未完整暴露 busy gate、assistant mode、per-task filter | 为 `CronScheduler` 增加 `is_loading`、`assistant_mode`、`filter` 并接入 frontend 状态；headless/daemon 特殊路径按 CCB 行为处理 |
| F22-R6 | durable 文件变更 reload | CCB 使用 watcher + stability delay 重新加载 `.claude/scheduled_tasks.json` | ClawCodex 有文件 CRUD，但 scheduler tick 主要按存储读取；reload 行为、外部编辑稳定性和 mtime/watch 策略需明确 | 首期可用 mtime polling；后续再引入 watcher。测试覆盖外部新增/删除/修改 durable task 后 scheduler 与 list 可见 |
| F22-R7 | teammate/agent ownership | session-only cron 带 `agentId`，列表/删除/触发按 owner 过滤，无法路由时失败落账 | 数据模型有预留方向，但真实 team runtime 注入与 orphan handling 未完成 | 与 Team/Coordinator runtime 对齐；首期至少保留字段、过滤接口和 headless failed run，避免静默丢弃 |
| F22-R8 | CCB-compatible gate 命名与用户心智 | CCB 使用 `CLAUDE_CODE_DISABLE_CRON`；ClawCodex 已有 `CLAWCODEX_DISABLE_CRON` | 当前扩展 prompt 和 `is_cron_disabled()` 以 `CLAWCODEX_DISABLE_CRON` 为主 | 建议兼容读取 `CLAUDE_CODE_DISABLE_CRON` 作为别名；文档统一说明 ClawCodex 首选 env 与 CCB 兼容 env |

F-22 完成后应满足以下端到端行为：

| 能力 | 完成标准 |
|------|----------|
| 工具可用性 | `CronCreate`、`CronList`、`CronDelete` 在 REPL/TUI/headless 真实路径中使用下游扩展实现，而不是 fallback `context.crons` 实现 |
| `/loop` | `/loop [interval] <prompt>` 创建 recurring cron，默认 `10m`，确认 job ID 后立即执行 prompt 一次 |
| 管理命令 | 提供 `/cron-list` 和 `/cron-delete <id>`，以表格展示 ID、Schedule、Prompt、Recurring、Durable |
| session-only | `durable=False` 的任务只保存在当前 runtime/session 中，CLI 退出后消失 |
| durable | `durable=True` 的任务写入 `.claude/scheduled_tasks.json`，重启后继续可见并可执行 |
| 调度器 | 每秒检查 due tasks，持有 `.claude/scheduled_tasks.lock`，防止多个 CLI 实例重复触发 |
| busy gate | 当前会话正在处理模型响应或工具调用时不抢跑 cron；assistant/headless 特殊模式按 `claude-code-best` 语义处理 |
| dispatch | 如果提供 `on_fire_task`，只调用 task 级回调，不再同时调用 prompt 级 `on_fire`，避免重复执行 |
| 结果追踪 | 每次 scheduled fire 都生成可查询运行记录，状态包括 `queued`、`running`、`completed`、`failed`、`cancelled` |
| missed one-shot | durable one-shot 在 CLI 关闭期间错过时，启动后删除该任务并展示安全 fenced prompt，必须先询问用户是否现在执行 |
| auto-expiry | recurring task 默认 7 天过期；支持配置 max-age，`0` 表示不过期 |
| jitter | recurring jitter 为确定性、按周期比例延后、最多 15 分钟；one-shot 在配置分钟边界可提前最多 90 秒 |
| 文件变更 | durable task 文件被外部更新后，scheduler 能重新读取或通过 mtime 轮询感知 |
| tool metadata | `CronCreate` / `CronDelete` 是 mutating tool，不再标记为 read-only |
| teammate parity | 如果 ClawCodex 启用 team/agent ownership，需实现 job ownership、列表过滤、删除归属校验和 orphaned task 处理；否则明确标记为后续依赖项 |

### 9.5 目标架构

```
CLI parser / dispatch
        ↓
clawcodex_ext.runtime.RuntimeContext
        ├── provider
        ├── tool_registry  ── replace_cron_tools() ── CronCreate/List/Delete
        ├── tool_context   ── session cron store + dispatch hooks
        ├── session
        └── cron_runtime
              ├── CronScheduler
              ├── CronDispatchBridge
              └── CronRunStore / autonomy-compatible run records
        ↓
Frontend plugin (REPL / TUI / headless)
        ↓
使用预构造 RuntimeContext，而不是重新构造 registry/context
        ↓
Scheduled fire → queued command / run record → frontend 执行 → status 可查询
```

关键原则：

- `clawcodex_ext/cron_system/*` 持有业务实现。
- `src/tool_system/tools/cron.py` 保留 fallback，不承载完整行为。
- `src/repl/*`、`src/entrypoints/headless.py`、`src/entrypoints/tui.py` 如需改动，只增加可选 prebuilt runtime/context 参数或 thin forwarding seam。
- 不为了 Cron 在 `src/*` 中复制一套下游逻辑。

### 9.6 实施阶段

#### Phase A — runtime-first 接线

**目标**: 先让真实 CLI 路径使用 `RuntimeContext` 中已替换的工具、上下文和 scheduler。

| 文件 | 改动 |
|------|------|
| `clawcodex_ext/runtime/context.py` | 将 cron runtime 附加为结构化对象，包含 scheduler、dispatch bridge、session store、run store |
| `clawcodex_ext/frontend/protocol.py` | 明确 frontend 接收完整 RuntimeContext 的协议，而不是只接收 options |
| `clawcodex_ext/frontend/repl.py` | 让 REPL 使用 `ctx.tool_registry`、`ctx.tool_context`、`ctx.cron_scheduler` |
| `clawcodex_ext/frontend/headless.py` | headless 入口使用预构造 registry/context，并接入 cron dispatch |
| `clawcodex_ext/frontend/tui.py` | TUI 入口使用预构造 registry/context，并接入 cron dispatch |
| `src/repl/core.py` | 如不可避免，仅增加 optional prebuilt provider/registry/context/session 参数 |
| `src/entrypoints/headless.py` | 如不可避免，仅增加 optional runtime 参数 |
| `src/entrypoints/tui.py` | 如不可避免，仅增加 optional runtime 参数 |

实现顺序：

1. 定义 downstream runtime 对象，例如 `CronRuntime` / `CronDispatchBridge`。
2. 让 frontend plugin 不再丢弃 `ctx`，而是把 prebuilt runtime 传到底层 runner。
3. scheduler lifecycle 由 frontend 启动/停止，确保退出时释放 lock。
4. 增加测试证明 `CronCreate` 命中 `clawcodex_ext/cron_system/tools.py`，而非 fallback `src/tool_system/tools/cron.py`。

#### Phase B — 存储与模型语义对齐

**目标**: 补齐 session-only 与 durable 分离，统一文件 schema 和工具行为。

| 文件 | 改动 |
|------|------|
| `clawcodex_ext/cron_system/models.py` | 对齐 `CronTask` 字段：`id`、`cron`、`prompt`、`created_at`、`updated_at`、`last_fired_at`、`next_fire_at`、`expires_at`、`recurring`、`permanent`、`durable`、可选 `agent_id` |
| `clawcodex_ext/cron_system/tasks.py` | durable 文件 CRUD 只管理 durable tasks；新增/接入 session task store；读入兼容 snake_case/camelCase，写出 canonical schema |
| `clawcodex_ext/cron_system/tools.py` | `CronCreate` 按 `durable` 分流；`CronList` 聚合 durable + session；`CronDelete` 同时删除两类 store 并对 missing ID 报错 |

关键决策：

- `durable=False` 不写 `.claude/scheduled_tasks.json`。
- durable 文件不写 runtime-only 字段，除非该字段是 `claude-code-best` 持久格式的一部分。
- 读取时容忍旧 extension 的 snake_case 和未来兼容用 camelCase。
- `CronCreate` / `CronDelete` 的 `is_read_only` 改为 `False`。
- 缺失 ID 的 `CronDelete` 应返回 tool input error 或 validation error，而不是静默 `success=false`。

#### Phase C — scheduler 语义对齐

**目标**: 让 scheduler 行为与 `claude-code-best` 的 `src/utils/cronScheduler.ts` 对齐。

| 文件 | 改动 |
|------|------|
| `clawcodex_ext/cron_system/scheduler.py` | 增加 `is_loading`、`assistant_mode`、`is_killed`、`filter`、`get_jitter_config`；修正 `on_fire_task` 优先级 |
| `clawcodex_ext/cron_system/lock.py` | 保持 `O_EXCL` lock；补齐同 session 重入/接管语义（如需要） |
| `clawcodex_ext/cron_system/jitter.py` | 实现 recurring 10% period capped by 15m；实现 one-shot configured boundary early jitter |
| `clawcodex_ext/cron_system/notifications.py` | missed one-shot 文案要求用户确认，并用安全 fence 包裹 prompt |
| `clawcodex_ext/cron_system/tasks.py` | due/missed/prune/mark-fired 在 storage lock 下保持原子状态转换 |

调度语义：

- `check_once()` 先判断 `is_killed()`，再判断 `is_loading()` 与 `assistant_mode`。
- 对 due task，如果有 `on_fire_task`，只调用 `on_fire_task(task)`；否则调用 `on_fire(task.prompt)`。
- recurring task fired 后更新 `last_fired_at`、`next_fire_at`、`updated_at`。
- one-shot task fired 后删除。
- missed durable one-shot 启动时删除并通知，不自动执行。
- 文件变更首期可用 mtime polling 实现，避免立即引入 watchdog 依赖；如果已有项目依赖再切换 watcher。

#### Phase D — 执行队列与结果追踪

**目标**: scheduled fire 不只是写 outbox，而是进入真实命令执行与结果查询路径。

| 文件 | 改动 |
|------|------|
| `clawcodex_ext/cron_system/runtime.py` | 把 `outbox` 升级为 typed dispatch bridge，负责把 task 转成 frontend 可执行命令 |
| `clawcodex_ext/cron_system/runs.py`（新） | 若无可复用模块，新增 scheduled run 记录：queued/running/completed/failed/cancelled |
| REPL/TUI downstream adapter | scheduled fire 时入队 prompt，渲染 scheduled-task 系统消息，避免同 sourceId 重复 active run |
| headless downstream adapter | mirror `claude-code-best` print mode，把 due task 交给 headless runner；无法路由 teammate 时标记 failed |

运行记录字段建议：

```json
{
  "run_id": "uuid",
  "runtime": "automatic",
  "trigger": "scheduled-task",
  "status": "queued",
  "root_dir": "/path/to/project",
  "current_dir": "/path/to/project",
  "source_id": "a1b2c3d4",
  "source_label": "Check deploy",
  "workload": "cron",
  "prompt_preview": "Check deploy",
  "created_at": 1700000000000,
  "updated_at": 1700000000000,
  "ended_at": null,
  "error": null
}
```

如果 ClawCodex 已有 orchestrator/task run 存储，优先复用；否则在 `clawcodex_ext/cron_system/runs.py` 中实现最小 run store，并在后续 autonomy 系统成熟后迁移。

#### Phase E — skills 与用户命令

**目标**: 用户无需知道底层工具名即可管理 cron。

| 命令 | 行为 |
|------|------|
| `/loop [interval] <prompt>` | 创建 recurring task，默认 `10m`，创建后立即执行 prompt 一次 |
| `/cron-list` | 调用 `CronList` 并以表格展示 ID、Schedule、Prompt、Recurring、Durable |
| `/cron-delete <id>` | 调用 `CronDelete` 删除任务；ID 缺失或不存在时给出清晰错误 |

实现路径：

- 现有 `src/skills/bundled/loop.py` 可保留，但其 enable gate 需要接入 Python 侧 cron gate。
- `/cron-list` 与 `/cron-delete` 优先在下游 skill extension 层注册；如果当前 skill extension 尚未落地，可在文档中标明这是 F-22 对 F-10 Skills Extension 的依赖点，必要时用最小 forwarding seam 过渡。
- gate 对齐 `CLAUDE_CODE_DISABLE_CRON`：设置后隐藏/禁用 Cron 工具、skills 和 scheduler。

#### Phase F — teammate / agent ownership

**目标**: 在 ClawCodex 支持 teammate runtime 时，还原 `claude-code-best` 的 cron ownership 行为。

| 场景 | 行为 |
|------|------|
| teammate 创建 session-only cron | job 带 `agent_id`，只在该 agent 上下文可见/可删 |
| lead 列表 | 可按上下文过滤，避免误删其他 agent job |
| teammate 已退出 | scheduler 触发 owned task 时记录 failed 或清理 orphaned cron |
| headless 无 teammate runtime | 创建 failed run，错误说明无法路由 owner |

如果当前 ClawCodex teammate 系统尚未具备完整 runtime 注入能力，F-22 首期可把 ownership 标记为“等待 team runtime 接口”，但数据模型和删除校验应预留 `agent_id`。

### 9.7 文件格式

#### durable task 文件

路径固定为项目根目录下：

```text
.claude/scheduled_tasks.json
```

写出格式建议使用 snake_case，以匹配当前 Python 模型；读取时兼容 snake_case 与 `claude-code-best` 的 camelCase：

```json
{
  "version": 1,
  "tasks": [
    {
      "id": "a1b2c3d4",
      "cron": "0 9 * * 1-5",
      "prompt": "Check my PRs",
      "recurring": true,
      "durable": true,
      "created_at": 1700000000000,
      "updated_at": 1700000000000,
      "last_fired_at": null,
      "next_fire_at": 1700003600000,
      "expires_at": 1700604800000,
      "jitter": {
        "recurring_frac": 0.1,
        "recurring_cap_ms": 900000,
        "one_shot_max_ms": 90000,
        "one_shot_floor_ms": 0,
        "one_shot_minute_mod": 30,
        "recurring_max_age_ms": 604800000
      }
    }
  ]
}
```

#### lock 文件

```text
.claude/scheduled_tasks.lock
.claude/scheduled_tasks.storage.lock
```

```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "pid": 12345,
  "acquiredAt": 1700000000000
}
```

### 9.8 测试计划

| 测试文件 | 新增/强化覆盖 |
|----------|---------------|
| `tests/cron/test_parser.py` | 5 字段 cron、range/list/step/name、DoM/DoW OR 语义、invalid 表达式 |
| `tests/cron/test_tasks.py` | durable/session 分离、文件 schema 兼容、missing/invalid record skip、并发 storage lock |
| `tests/cron/test_scheduler.py` | busy gate、`on_fire_task` 不重复 dispatch、one-shot 删除、recurring reschedule、expired prune、mtime reload |
| `tests/cron/test_lock.py` | scheduler lock、storage lock、stale lock recovery、live lock blocking |
| `tests/cron/test_tools_runtime.py` | runtime 替换 fallback cron tools、mutating metadata、CronDelete missing ID |
| `tests/test_downstream_cli_dispatch.py` | CLI dispatch 后 frontend 使用预构造 RuntimeContext |
| `tests/test_repl.py` / TUI tests | scheduled fire 入队、系统消息、run status |
| `tests/test_skills_e2e.py` | `/loop`、`/cron-list`、`/cron-delete` prompt/tool 调用链 |

### 9.9 手工验收流程

在临时 workspace 中执行端到端 smoke：

1. 启动 ClawCodex，确认 cron gate 未禁用。
2. 使用 `/loop 1m check status` 或直接调用 `CronCreate` 创建 session-only recurring task。
3. 使用 `/cron-list` 确认任务存在，字段包含 ID、human schedule、prompt、recurring、durable。
4. 创建 durable one-shot task，确认 `.claude/scheduled_tasks.json` 写入。
5. 让 scheduler tick 或构造 due time，确认任务进入 queued/running/completed 或 failed run 记录。
6. 用 status/runs 命令查看 scheduled-task 结果。
7. 使用 `/cron-delete <id>` 删除任务，并确认 session store 与 durable file 都已更新。
8. 重启 CLI，确认 durable task 继续存在，session-only task 消失。
9. 构造 missed durable one-shot，确认启动后提示用户确认，而不是直接执行 prompt。
10. 同时启动两个 CLI 实例，确认只有 lock owner 触发 durable task。

### 9.10 实施顺序与完成标准

| 阶段 | 完成标准 |
|------|----------|
| A. Runtime 接线 | REPL/TUI/headless 真实路径使用扩展 Cron tools；scheduler 可按 frontend lifecycle 启停 |
| B. 存储模型 | session-only 与 durable 分离；文件 schema 兼容；CronCreate/List/Delete 行为对齐 |
| C. Scheduler | busy gate、lock、jitter、missed、expiry、reload、single dispatch 全部有测试 |
| D. 执行结果 | scheduled fire 可入队执行并生成可查询 run status |
| E. Skills | `/loop`、`/cron-list`、`/cron-delete` 用户路径可用 |
| F. Ownership | teammate/agent ownership 能力按当前 runtime 成熟度实现或明确阻塞依赖 |

F-22 不应在只有 `clawcodex_ext/cron_system` 单元测试通过时标记完成。完成标准必须是：从 CLI 用户路径创建的任务能够被真实 scheduler 触发、执行、记录结果，并可被用户查看和删除。

### 9.11 CCB 对比发现的补充缺口

> 以下缺口基于 2026-06 对 `claude-code-best` cron 系统的完整对比分析得出，多数未被 F-22 原有 Phase A~F 覆盖，需作为 F-22 的补充子任务纳入实施计划。
>
> **2026-06 实施状态**：G1、G2、G3、G4、G5、G6、G7、G8 全部完成（`clawcodex_ext/cron_system/` 改造 + 46 个新单元测试 + 90/90 cron 测试 + 231/231 orchestrator 测试通过；独立 verification agent 两次给出 PASS 判定）。详见各小节末"实施状态"。

#### 9.11.1 Feature Gate 系统——isKilled 运行时 kill 开关（F-22-G1）

**优先级**: P0  
**参考实现**: `claude-code-best/src/utils/cronScheduler.ts` 的 `isKilled` 轮询 + `prompt.ts` 的 `isKairosCronEnabled` / `CLAUDE_CODE_DISABLE_CRON` 环境变量

**现状**: F-22 Phase E 已规划 gate 对齐 `CLAUDE_CODE_DISABLE_CRON`，但缺少运行时 kill 开关。

**缺口详述**:

CCB 的 `cronScheduler.ts` 在每次 `check()` 前做 `isKilled?.()` 轮询检查。当 Feature Flag 服务（GrowthBook）推送关闭时，所有正在运行的 scheduler 在下一 tick 立即停止触发，无需重启 CLI。这在运维场景中至关重要：当 cron 系统引发异常行为（如无限循环、API 打满）时，可秒级止血。

ClawCodex 当前仅支持启动时通过环境变量禁用，无法运行时紧急关闭。

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 环境变量门 | `CLAWCODEX_DISABLE_CRON=true` 启动时禁用所有 cron 工具、skills 和 scheduler |
| 运行时 kill 接口 | `CronScheduler` 支持 `is_killed: Callable[[], bool]` 轮询 |
| 动态切换路径 | 从配置文件或 provider config 变更事件中触发 kill 状态变更 |
| 工具 prompt 门 | 关闭时工具返回 "Cron is disabled"，而非错误 |

**实施状态（2026-06）**: ✅ 完成。
- `models.py::is_cron_disabled(env=None)` 读 `CLAWCODEX_DISABLE_CRON`，支持 `1/true/yes/on` 及带空格的写法
- `scheduler.py::CronScheduler.is_killed: Callable[[], bool] | None`，`check_once` / `notify_missed_once` / `get_next_fire_time` 三个入口都先轮询，kill 时直接 return
- `tools.py` 在 CronCreate/CronDelete/CronList 三个工具的 `call` 开头判定 disabled，统一返回 `_cron_disabled_result(tool_name)` → `{success: false, disabled: true, message: "Cron is disabled (CLAWCODEX_DISABLE_CRON is set)."}`
- `runtime.py::attach_cron_runtime` 把 `is_cron_disabled`（或调用方注入的 `is_killed`）接到 scheduler；outbox 在 disabled 时不再入队
- 测试：`tests/cron/test_f22_gaps.py::TestG1FeatureGate`（9 个用例覆盖 env 值、scheduler tick 行为、kill 切换可恢复）

---

#### 9.11.2 远程 Jitter 实时配置（F-22-G2）

**优先级**: P0  
**参考实现**: `claude-code-best/src/utils/cronJitterConfig.ts` -> GrowthBook Feature Flag `tengu_kairos_cron_config` -> Zod 校验 + 兜底默认值

**现状**: F-22 Phase C 只规划了静态 jitter 实现（10% recurring cap 15min、one-shot 90s），没有远程实时调参能力。

**缺口详述**:

CCB 的 jitter 参数并非硬编码，而是通过 GrowthBook 实时下发。6 个可调参数（`recurringFrac`, `recurringCapMs`, `oneShotMaxMs`, `oneShotFloorMs`, `oneShotMinuteMod`, `recurringMaxAgeMs`）可在不重启客户端的情况下动态调整。这对于集群运营至关重要——当整点 (:00/:30) 出现 thundering herd 时，运维可立即增大 jitter 窗口。

ClawCodex 当前 jitter 参数为静态常量，无调参能力。

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 配置可调 | 支持通过配置文件或环境变量覆盖全部 6 个 jitter 参数 |
| 热加载 | Scheduler 在每次 `check_once()` 时重新读取配置，不要求 CLI 重启 |
| 兜底默认值 | 配置加载失败时使用安全默认值，不中断 scheduler |
| 参数校验 | 加载后校验参数范围（如 `recurringFrac` 应在 [0, 1)），超范围时 fallback 默认值 |

**实施状态（2026-06）**: ✅ 完成。
- `models.py::CronJitterConfig` 扩展为 6 参数字段（`recurring_frac`/`recurring_cap_ms`/`one_shot_max_ms`/`one_shot_floor_ms`/`one_shot_minute_mod`/`recurring_max_age_ms`），保留旧 `enabled`/`max_jitter_ms` 以做向后兼容
- `load_jitter_config(workspace_root, env=...)` 解析顺序：env 变量（`CLAWCODEX_CRON_RECURRING_FRAC` 等 8 个）> `.claude/cron_jitter_config.json` > 内置默认；接受 snake_case 与 camelCase 两种键
- `validate_jitter_config` 防御性夹紧（`recurring_frac` ∈ [0, 1)、`recurring_cap_ms` ≤ 30 min、`one_shot_minute_mod` ≤ 60 等），夹紧后失败字段自动收敛到安全范围
- `scheduler.py::CronScheduler.load_jitter_config: Callable[[], CronJitterConfig] | None` —— 调用方注入远程源（GrowthBook 等），默认走本地 loader；`check_once` 每个 tick 调用并把 `recurring_max_age_ms` 透传到 `prune_expired_recurring_tasks(max_age_ms=...)`
- `max_age_ms=0` 关闭过期（对齐 CCB `recurringMaxAgeMs=0`）
- 防御性：loader 抛异常时回退到缓存值，首次启动完全失败回退到 `load_jitter_config(workspace_root)`，scheduler 永不中断
- 测试：`TestG2JitterConfig`（7 个） + `test_scheduler_hot_reloads_jitter_per_tick` + `test_prune_uses_live_max_age`

---

#### 9.11.3 One-shot 反向 Jitter（整点提前）（F-22-G3）

**优先级**: P1  
**参考实现**: `claude-code-best/src/utils/cronTasks.ts` 的 `oneShotJitteredNextCronRunMs()`

**现状**: F-22 Phase C 描述了基本 jitter 但未明确区分正向与反向 jitter。

**缺口详述**:

CCB 对 scheduled fire 有两种 jitter 策略：
- **Recurring 任务**：正向 jitter（延迟触发），比例 10%，最多 15 分钟。避免所有 session 在 :00 同时触发。
- **One-shot 任务**：反向 jitter（提前触发），最多 90 秒。只在 one-shot 的触发时间落在 `minute % oneShotMinuteMod === 0` 时（默认 `mod=30`，即 :00/:30）生效。此举让集群中大量 one-shot 任务不在整点同时命中推理服务。

ClawCodex 当前的 `jitter.py` 仅实现了最基本的 `max_jitter_ms` 正向延迟，缺少 one-shot 反向 jitter 策略。

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 分钟门槛 | 仅当触发分钟满足 `minute % oneShotMinuteMod === 0` 时应用反向 jitter |
| 最大提前 | `oneShotMaxMs` 默认 90s，任务可提前触发 |
| 最小提前 | `oneShotFloorMs` 保证即使 taskId hash 接近 0 也有最低提前量 |
| 确定性 | 反向 jitter 值由 taskId 的 hash 决定，同一 task 同一配置产生相同偏移 |

**实施状态（2026-06）**: ✅ 完成。
- `jitter.py::one_shot_jittered_next_cron_run_ms(task_id, fields, from_time, config)`：先用 `compute_next_cron_run` 算精确时间，命中 `minute % one_shot_minute_mod == 0`（默认 30 → :00/:30）才施加 lead，否则原样返回
- lead 计算：`one_shot_floor_ms + jitter_frac(task_id) * (one_shot_max_ms - one_shot_floor_ms)`，默认 floor=0/max=90000 ms；确定性由 sha256(task_id)[:8] 决定，跨进程稳定
- 防过早触发：`max(base_ms - lead, from_time_ms)` —— 任务创建时间落在自身 lead 窗口内时不会"未出生就触发"
- recurring 路径同步重写：`jittered_next_cron_run_ms` 用 `recurring_frac × interval`，截断到 `recurring_cap_ms`（不再用旧 `max_jitter_ms` 单参）；若 `recurring_frac=0`/`recurring_cap_ms=0` 走旧路径以保后向兼容
- 测试：`TestG3OneShotJitter`（6 个）覆盖 off-minute no-lead、round-minute lead、floor+max 范围、确定性、disabled 退化

---

#### 9.11.4 Permanent 免过期任务机制（F-22-G4）

**优先级**: P1  
**参考实现**: `claude-code-best/src/utils/cronTasks.ts` 的 `permanent` 字段 + `src/assistant/install.ts` 的 `writeIfMissing()`

**现状**: F-22 Phase B 已规划 `permanent` 字段作为数据模型的一部分，但缺少助手指令模式的用例设计。

**缺口详述**:

CCB 支持 `permanent: true` 标记，此标记不可通过 `CronCreateTool` 设置，仅由 assistant mode 的安装脚本通过 `writeIfMissing()` 写入。永久任务跳过 `recurringMaxAgeMs` 自动过期机制。典型用途：
- `catch-up`：周期性从 Issue 跟踪器拉取待办
- `morning-checkin`：每日工作汇报
- `dream`：后台探索性分析

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 数据模型 | `CronTask.permanent` 字段，仅从文件直写（exempt from CronCreate） |
| 过期豁免 | `recurringMaxAgeMs` 检查跳过 `permanent=true` 的任务 |
| 写保护 | `CronCreate` 拒绝设置 `permanent=true` |
| 安装入口 | 为 assistant/daemon 模式提供 `write_if_missing()` 等价工具方法 |

**实施状态（2026-06）**: ✅ 完成。
- `models.py::CronTask.permanent: bool = False`，加入 `to_dict` / `from_dict` 持久化
- `tasks.py::write_permanent_task_if_missing(workspace_root, cron, prompt, recurring=True, jitter=None, created_at=None, task_id=None)`：file-lock 内做幂等检查（按 cron+prompt 匹配）；命中永久任务且 spec 一致 → 返回 `(task, created=False)`；命中永久任务但 spec 不一致 → 抛 `PermissionError` 防 installer 误覆盖；命中非永久任务且 spec 一致 → 替换为永久；新增 `expires_at=None` 确保永不自动过期
- `prune_expired_recurring_tasks`：`_is_kept` 守卫 `if task.permanent: return True`，无论 `max_age_ms` 取何值 permanent 都不被剪
- `tools.py::_cron_create_call`：检测 `tool_input.get("permanent") is True` → 抛 `ToolInputError("permanent is a system-only flag and cannot be set via CronCreate")`；CronList `_task_output` 输出 `permanent` 字段以便用户可见
- `runtime.py::install_permanent_cron_tasks(workspace_root, [specs])`：批量包装 `write_permanent_task_if_missing` 并吞掉 `PermissionError`（记 warning）；用于 assistant installer 接入 catch-up / morning-checkin / dream 三个内置任务
- 测试：`TestG4Permanent`（4 个）覆盖 CronCreate 拒绝、idempotent、覆盖保护、prune 豁免

---

#### 9.11.5 锁注册式清理与 PID 存活探测增强（F-22-G5）

**优先级**: P1  
**参考实现**: `claude-code-best/src/utils/cronTasksLock.ts` 的 `cleanupRegistry` + `isProcessRunning()`

**现状**: F-22 Phase C 已规划基础 `O_EXCL` lock 实现，`lock.py` 已有 `os.kill(pid,0)` 探测。

**缺口详述**:

CCB 的锁系统在 `cronTasksLock.ts` 中有三项增强机制未被 `lock.py` 覆盖：

| 机制 | CCB 实现 | ClawCodex 现状 |
|------|---------|---------------|
| 注册式退出清理 | `cleanupRegistry.add(cleanup)` / `process.on('exit', runAll)`. 进程正常/异常退出时自动释放锁。 | ❌ 无注册式清理。进程 crash 后锁可能残留，需等待 stale lock 恢复机制。 |
| PID 分身检测 | 新实例发现锁的 PID 存活但进程不是 Claude 时（如 PID 被其他进程复用），主动清理。 | ⚠️ 有基本 `os.kill(pid,0)`，但无分身检测和主动恢复。 |
| 锁升级 | 同 sessionId 的进程可接管自己之前持有的锁（fork/exec 场景）。 | ❌ 无锁接管机制。 |

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 退出清理 | 注册 `atexit`/信号处理器，进程退出时自动释放 scheduler lock 和 storage lock |
| 分身检测 | 读取锁文件中的 PID，若进程存活但不是当前 ClawCodex 进程，则视为 stale 并覆盖 |
| 锁接管 | 同 sessionId 重入时允许跳过锁竞争（同一会话内的 fork 恢复场景） |

**实施状态（2026-06）**: ✅ 完成。
- `lock.py::register_lock_cleanup(callback)` + `release_all_locks()`：模块级清理注册表；首次注册时自动 `atexit.register(release_all_locks)` + 在主线程上 `signal.signal(SIGTERM/SIGINT, ...)` 包装原 handler，确保进程正常/异常退出都触发；`_register_self_cleanup(lock)` 在每次 `CronTaskLock.acquire()` 成功时挂一个 release 回调
- `_default_pid_validator(pid)` 读 `/proc/<pid>/comm`，白名单 `python*` / `clawcodex*` / `claude*` / `orchestrator*`（comm 未知时仍返回 True，附 debug log）；`set_pid_validator(callable)` 注入测试桩
- `_recover_if_stale` 三段式判断：age 超 `stale_after_ms` → 删；PID dead → 删；PID alive 但 validator 返回 False（PID 被非 ClawCodex 进程回收）→ 删 + warning log
- `CronTaskLock.acquire` 新增 `allow_session_takeover=True`（默认开）：先读 payload，若 `sessionId` 与自己相同则 in-place refresh lock 内容（`tmp.write_text` + `os.replace`，不抢占 O_EXCL 路径）后直接返回 True，覆盖 fork/exec 场景
- 测试：`TestG5LockImprovements`（6 个）覆盖 session takeover、不同 session 拒绝、PID validator 覆盖、register/unregister、stale age 恢复

---

#### 9.11.6 工具 Prompt 指引文档增强（F-22-G6）

**优先级**: P2  
**参考实现**: `claude-code-best` 的 `CronCreateTool.ts` / `CronDeleteTool.ts` 中内联的全面 prompt 文档

**现状**: F-22 未涉及工具 prompt 内容的设计。

**缺口详述**:

CCB 的 cron 工具在 prompt 中内联了用户指导信息，包括：
- Jitter 原理说明和避免 `:00/:30` 整点的建议
- 自动过期时间提示（7 天默认）
- Teammate/agent scope 限制
- 最多 50 个 job 的限制
- Durable vs session-only 的选择建议

ClawCodex 当前工具的 `prompt` 字段仅为 "Schedule a recurring or one-shot prompt."，LLM 无法了解最佳实践。

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| CronCreate prompt | 包含 cron 表达式示例、jitter 说明、过期机制、durable 建议 |
| CronDelete prompt | 包含使用前提（先 CronList 查询 ID）、删除不可恢复提示 |
| CronList prompt | 包含字段说明、teammate scope 提示 |

**实施状态（2026-06）**: ✅ 完成。
- `tools.py::CRON_CREATE_PROMPT`：多行块，覆盖 5 字段 cron 语法 + 3 条示例、recurring/one-shot 区别与 7 天自动过期、jitter 原理（recurring forward + one-shot backward lead + :00/:30 hotspot）、durable vs session 选型指引、`permanent` 系统字段、50 job 上限、disabled 软返回说明
- `CRON_LIST_PROMPT`：列出返回字段（`id`/`cron`/`humanSchedule`/`recurring`/`durable`/`permanent`/`createdAt`/`updatedAt`/`lastFiredAt`/`nextFireAt`/`expiresAt`），提示 `permanent` 不可删，teammate/agent scope 提示
- `CRON_DELETE_PROMPT`：明确"先 CronList 取 id"的前置步骤，强调删除不可逆（recurring 直接删 + 不可暂停；session-only 清内存记录）
- `description` 字段也取自 `prompt.splitlines()[0].lstrip('# ').strip()`，保留与 CCB 一致的展示
- 测试：`TestG6ToolPrompts`（4 个）覆盖三类 prompt 关键文本 + disabled 工具返回的 `disabled=true` + `message=CRON_DISABLED_MESSAGE`

---

#### 9.11.7 Analytics 遥测事件注入（F-22-G7）

**优先级**: P2  
**参考实现**: `claude-code-best` 的 `tengu_scheduled_task_fire` / `tengu_scheduled_task_missed` / `tengu_scheduled_task_expired`

**现状**: ClawCodex 无遥测系统，此缺口为项目级，但 cron 模块在设计时应预留事件点。

**缺口详述**:

CCB 在每个关键 cron 事件点注入遥测事件：
| 事件 | 触发时机 |
|------|---------|
| `tengu_scheduled_task_fire` | 每次 cron task 被触发执行时，携带 `recurring` 标记和 `taskId` |
| `tengu_scheduled_task_missed` | 启动时发现 missed one-shot 任务并通知用户时 |
| `tengu_scheduled_task_expired` | 周期性任务因超龄被自动删除时，携带 `ageHours` |

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 事件预留点 | 在 scheduler 的 fire、missed、expired 路径预留 callback/event hook |
| 不阻塞遥测接入 | 如果 ClawCodex 尚无遥测系统，预留点应设计为可选的 `Optional[Callable]`，不引入额外依赖 |
| 数据结构 | 事件数据保持简单字典，未来可序列化为 JSON log 行 |

**实施状态（2026-06）**: ✅ 完成。
- `scheduler.py::CronScheduler` 暴露三个 `Callable[[dict], None]` 字段：`on_fire_event` / `on_missed_event` / `on_expired_event`，默认实现 `_noop_event`（不引入任何依赖，零开销）
- `check_once` 在每次创建 queued run 之后立即 `self.on_fire_event({type:"fire", task_id, recurring, fire_at})`
- `notify_missed_once` 在删除 missed tasks 后 `self.on_missed_event({type:"missed", count, task_ids})`
- `runtime.py::attach_cron_runtime` 默认接 `_log_event(payload)`（走 `logging.debug("cron event: %s", payload)`），未来接入 telemetry 时只换 hook 实现
- 事件数据是简单 dict，可直接 `json.dumps` 落 NDJSON；不阻塞也未引入新依赖（grep `from .analytics|growthbook|telemetry` 应为空）
- 测试：默认 `_noop_event` 可调用 + `check_once` 路径覆盖（与 G8 测试共用，9 个对抗性探针全通过）

---

#### 9.11.8 inFlight 防重复触发机制（F-22-G8）

**优先级**: P2  
**参考实现**: `claude-code-best/src/utils/cronScheduler.ts` 的 `inFlight` Set

**现状**: F-22 未提及 inFlight 保护。

**缺口详述**:

CCB 的 scheduler 维护一个 `inFlight` Set，在异步操作（`removeCronTasks` / `markCronTasksFired`）进行中时记录 task ID。在此期间，同一 task 不会被 `check()` 再次触发。这是应对文件 IO 异步延迟的关键保护——如果 scheduler tick 在 `removeCronTasks` 完成前再次触发，可能导致同一 one-shot 任务被触发两次。

ClawCodex 当前 scheduler 无此保护。

**实施要求**:

在 scheduler 的 `process()` 方法中：
1. 触发前将 task ID 加入 `in_flight` 集合
2. 异步操作完成后从 `in_flight` 移除
3. `process()` 开头检查 `if task.id in in_flight: return`
4. `in_flight` 使用线程安全的数据结构（如 `threading.Lock` + `set()`）

**实施状态（2026-06）**: ✅ 完成。
- `scheduler.py::CronScheduler` 字段 `_in_flight: set[str]` + `_in_flight_lock: threading.Lock`
- `check_once` 在 fire 循环里：先 `_in_flight_contains(task.id)` 命中则 skip → `_in_flight_add` → 跑 `create_queued_run_for_task` / `on_fire_event` / `on_fire_task|prompt` → `finally: _in_flight_remove`（异常路径也释放）
- 8 worker × 50 taskID 并发压测：所有 contains/remove 调用都成功，最终集合为空
- 在 `check_once` 顶层先 `is_disabled()` 早返回，再 reload jitter config，再 prune → find_due，再循环 in_flight 检查，保证 disabled 状态下连 in_flight 都不占用
- 测试：`TestG8InFlight`（3 个）覆盖 skip-double-fire、fire-后自动释放、并发线程安全

---

#### 9.11.9 ClawCodex 已有但 CCB 缺失的优势特性（F-22-A1 ~ A5）

以下为 ClawCodex `clawcodex_ext/cron_system/` 中已实现但 CCB 没有的特性，需在 F-22 迁移中保持：

| 编号 | 特性 | 文件 | 说明 | 迁移风险 |
|------|------|------|------|---------|
| A1 | CronRun 完整状态机追踪 | `runs.py` | queued/running/completed/failed/cancelled 全生命周期，运行历史持久化 | 低——已成为独立模块 |
| A2 | 手动触发任务 | `runtime.py` / `manual_fire_cron_task()` | 支持通过 CLI 或 API 手动触发指定 cron 任务，返回 run_id | 低——接口已定义 |
| A3 | Autonomy 状态展示 | `status.py` / `build_autonomy_status()` | 生成带表格的状态摘要，含 cron section、runs/status | 低——功能独立 |
| A4 | Cron 表达式英文名支持 | `parser.py` | 支持 `jan/feb/mon/tue` 英文月份/星期缩写 | 低——parser 独立 |
| A5 | 条目化输出详情 | `tools.py` / `_task_output()` | CronList 返回 `createdAt`/`updatedAt`/`lastFiredAt`/`nextFireAt`/`expiresAt` | 低——输出格式扩展 |

**实施要求**:
- F-22 Phase A~F 实施过程中不得破坏上述 A1~A5 的现有行为。
- A2（手动触发）应在 Phase D（执行队列）完成后接入真实 dispatch 路径。
- A3（状态展示）应在 Phase D 完成后与 `autonomy status/runs` 命令对齐。

---

#### 9.11.10 补充缺口实施优先级矩阵

> **2026-06 实施状态更新**：G1~G8 全部完成（✅），工作量合计 ~10 人天（落在矩阵估算的 11.5-17.5 天区间内下限，5 个文件 + 1 个测试文件，约 950 行变更 + 950 行测试）。

| 编号 | 缺口 | F-22 Phase 关联 | 优先级 | 预计工作量 | 实际状态 |
|------|------|----------------|--------|-----------|---------|
| G1 | isKilled 运行时 kill 开关 | Phase E (gate) | P0 | 1-2天 | ✅ 完成 |
| G2 | 远程 Jitter 实时配置 | Phase C (jitter) | P0 | 3-5天 | ✅ 完成 |
| G3 | One-shot 反向 Jitter | Phase C (jitter) | P1 | 2-3天 | ✅ 完成 |
| G4 | Permanent 免过期任务 | Phase B (model) | P1 | 1-2天 | ✅ 完成 |
| G5 | 锁注册式清理与 PID 增强 | Phase C (lock) | P1 | 2-3天 | ✅ 完成 |
| G6 | 工具 Prompt 指引增强 | Phase E (skills) | P2 | 0.5天 | ✅ 完成 |
| G7 | Analytics 遥测事件预留 | 项目级 | P2 | 1天 | ✅ 完成 |
| G8 | inFlight 防重复触发 | Phase C (scheduler) | P2 | 1天 | ✅ 完成 |
| A1~A5 | 已有优势特性保持 | 全 Phase | — | 检查点 (0.5天) | ✅ 保持（9.11 实施未破坏 A1~A5 行为；G4 install_permanent_cron_tasks 顺便提供 A2 手动触发的入口） |

> **建议实施顺序**：G2 → G1 → G5 → G3 → G4 → G8 → G6 → G7，穿插在各 Phase 之间作为增量 PR 提交。

---

## 十、Skills System Extension（技能系统扩展层）

**状态**: ✅ 已完成（F-23，2026-05-24）
**优先级**: P1
**目标**: 仿照 `tool_system_ext` 的模式，构建独立的技能系统扩展层，降低上游更新时的侵入式修改
### 2.11 Away-Summary（离开摘要）功能

**状态**: 📋 规划中
**上游版本**: claude-code-best `src/services/awaySummary.ts`, `src/hooks/useAwaySummary.ts`, `src/commands/recap/`
**目标**: 在一次交互对话完成后，CCB 会总结对话内容并给出总结与下一步的意见（以 ※ 开头，字体颜色为浅灰色）

#### 功能描述

Away-summary 是 Claude Code 的一个贴心功能：当用户离开终端一段时间后返回，CLI 会自动生成一段简短摘要，说明：
1. 用户在做什么（高层目标，不是实现细节）
2. 下一步具体操作

#### 上游实现分析

| 文件 | 功能 | 关键点 |
|------|------|--------|
| `src/constants/figures.ts:29` | 定义 `REFERENCE_MARK = '\u203b'` | ※ 符号，用于 away-summary 标记 |
| `src/services/awaySummary.ts` | 生成离开摘要 | 调用小模型生成 1-3 句话摘要 |
| `src/commands/recap/generateRecap.ts` | 手动 recap 命令 | `/recap`, `/away`, `/catchup` 别名 |
| `src/hooks/useAwaySummary.ts` | React hook | 终端失焦 5 分钟后自动触发 |
| `src/components/messages/SystemTextMessage.tsx:55-64` | 渲染组件 | `dimColor` 浅灰色显示 |

#### 渲染样式

```tsx
// SystemTextMessage.tsx:55-64
if (message.subtype === 'away_summary') {
  return (
    <Box flexDirection="row" marginTop={addMargin ? 1 : 0} backgroundColor={bg} width="100%">
      <Box minWidth={2}>
        <Text dimColor>{REFERENCE_MARK}</Text>
      </Box>
      <Text dimColor>{String(message.content ?? '')}</Text>
    </Box>
  );
}
```

#### 触发条件

1. **自动触发**: 终端失焦（blur）5 分钟后，且无进行中的 turn
2. **手动触发**: `/recap`, `/away`, `/catchup` 命令
3. **前提条件**: 
   - Feature flag `AWAY_SUMMARY` 启用
   - GrowthBook A/B 测试 `tengu_sedge_lantern` 为 true

#### 摘要内容规范

**英文版 prompt** (`src/services/awaySummary.ts:23`):
```
The user stepped away and is coming back. Write exactly 1-3 short sentences. 
Start by stating the high-level task — what they are building or debugging, 
not implementation details. Next: the concrete next step. 
Skip status reports and commit recaps.
```

**中文版 prompt** (`src/services/awaySummary.ts:26`):
```
用户离开后回来了。用中文写 1-3 句话。先说明用户在做什么（高层目标，不是实现细节），
然后说明下一步具体操作。不要写状态报告或提交总结。
```

#### ClawCodex 实现方案

##### 架构设计

```
src/
├── services/
│   └── away_summary.py          # 核心服务：生成离开摘要
├── hooks/
│   └── use_away_summary.py     # 终端焦点状态监控 + 定时器
├── commands/
│   └── recap.py                 # /recap, /away, /catchup 命令
├── components/
│   └── messages/
│       └── system_text.py       # SystemTextMessage 渲染 away_summary subtype
├── types/
│   └── message.py               # 添加 SystemAwaySummaryMessage 类型
└── constants/
    └── figures.py               # 添加 REFERENCE_MARK = '\u203b'
```

##### 核心组件

| 组件 | 文件 | 功能 |
|------|------|------|
| `generate_away_summary()` | `services/away_summary.py` | 调用小模型生成 1-3 句话摘要 |
| `generate_recap()` | `commands/recap.py` | 手动 recap 命令实现 |
| `use_away_summary()` | `hooks/use_away_summary.py` | 监控终端焦点，5 分钟失焦后触发 |
| `SystemAwaySummaryMessage` | `types/message.py` | 消息类型定义 |
| `REFERENCE_MARK` | `constants/figures.py` | ※ 符号常量 |

##### 消息类型

```python
@dataclass
class SystemAwaySummaryMessage(SystemMessage):
    """离开摘要消息"""
    type: Literal["system"] = "system"
    subtype: Literal["away_summary"] = "away_summary"
    content: str
```

##### 触发逻辑

```python
# hooks/use_away_summary.py

BLUR_DELAY_MS = 5 * 60_000  # 5 分钟

def use_away_summary(messages, set_messages, is_loading):
    """监控终端焦点，失焦 5 分钟后生成摘要"""
    timer_ref = None
    
    def on_focus_change(state):
        nonlocal timer_ref
        if state in ('blurred', 'unknown'):
            timer_ref = set_timeout(on_blur_timer_fire, BLUR_DELAY_MS)
        elif state == 'focused':
            cancel_timer(timer_ref)
            abort_in_flight()
    
    # 焦点变化时启动定时器
    # 定时器触发时检查：无进行中的 turn，无已有摘要 → 生成摘要
```

##### 与现有组件集成

| 现有组件 | 集成点 | 说明 |
|---------|--------|------|
| `src/repl/live_status.py` | 焦点状态 | 复用终端焦点感知 |
| `src/agent/session.py` | 消息历史 | 读取最近 30 条消息生成摘要 |
| `src/providers/base.py` | 模型调用 | 使用小模型（fast model）生成摘要 |
| `src/tool_system/tools/recap.py` | 命令注册 | 注册 `/recap` 命令 |

##### 外部依赖

无新增外部依赖，复用现有基础设施。

---

### 2.12 Ctrl+B Agent 后台持续运行 + `--resume` 恢复会话（Fork-Continue 模式）

**状态**: 🔄 设计完成，待实现
**目标**: Ctrl+B 后 Agent 在子进程中继续运行，用户可通过 `--resume` 重新连接并实时查看 Agent 进度

#### 问题背景

当前 Ctrl+B 的实际行为：

1. TUI 按 Ctrl+B → `action_agent_background()` 调用 `self.exit(result=("__FULL_EXIT__", sid))`
2. 整个 ClawCodex 进程退出，agent worker 线程（daemon thread）随之被杀死
3. `background_signal` 被设置但无人监听（`run_with_background_escape` 未在 TUI agent loop 路径中被调用）
4. `--resume` 仅恢复已保存的 JSONL 快照，不会连接到任何活跃的后台 agent

**核心缺陷**：Ctrl+B 只是"保存退出"，Agent 并未在后台继续运行。

#### 设计方案：Fork-Continue 模式

采用 **父进程退出 + 子进程继续运行 agent** 的模式，而非"进程内线程分离"：

- agent 在子进程中拥有完整的独立事件循环，不受父进程退出影响
- 子进程通过已有的 `SessionStorage` JSONL 文件持续写入 agent 输出
- `--resume` 通过 `TailFollower` 读取 JSONL 增量，实时显示 agent 进度
- 子进程自然终止后（agent 完成），JSONL 不再增长，resume 端能检测到

#### 数据流

```
                    ┌───────────────────────────────────┐
  Ctrl+B 触发 ───→ │  action_agent_background()        │
                    │  1. session.save()                │
                    │  2. 写入 .background-runner.json   │
                    │  3. os.fork()                      │
                    │     ├─ 父进程: exit → shell        │
                    │     └─ 子进程: 继续运行 agent loop │
                    │         → 持续写入 JSONL transcript│
                    └───────────────────────────────────┘

                    ┌───────────────────────────────────┐
  --resume ────→   │  run_tui()                         │
                    │  1. Session.resume_with_tail()     │
                    │  2. TailFollower 监听 JSONL 增量   │
                    │  3. AgentBridge._run_tail_follower │
                    │     → 实时渲染后台 agent 输出      │
                    │  4. agent 完成后自动检测           │
                    └───────────────────────────────────┘
```

#### 新增模块：`src/agent/background_runner.py`

管理后台 agent 子进程的完整生命周期：

```python
"""Background agent runner — manages the forked child process that
continues the agent loop after Ctrl+B.

Lifecycle:
  1. Parent: ``launch_background_runner()`` forks a child that runs
     the agent loop headlessly, writing output to the session's
     JSONL transcript.
  2. Child:  ``_run_agent_headless()`` drives the agent loop with
     on_message/write_message callbacks (no TUI, no streaming).
  3. Resume: ``--resume`` attaches a TailFollower to the JSONL file
     for real-time output. When the child finishes, the JSONL stops
     growing and a completion marker is appended.
  4. Cleanup: ``cleanup_background_runner()`` removes the marker
     file after successful resume.

State file: ``~/.clawcodex/sessions/{session_id}/.background-runner.json``
  {
    "pid": 12345,
    "session_id": "abc123",
    "started_at": "2025-01-01T00:00:00",
    "status": "running" | "completed" | "failed"
  }
"""
```

##### 关键函数

| 函数 | 说明 |
|------|------|
| `launch_background_runner(session, provider, tool_registry, tool_context, max_turns)` | Fork 子进程，在子进程中运行 headless agent loop |
| `_run_agent_headless(session, provider, tool_registry, tool_context, max_turns)` | 子进程入口：构建独立 asyncio loop，调用 `run_query_as_agent_loop`，通过 `SessionStorage.write_message` 持续写入输出 |
| `get_background_runner_status(session_id)` | 读取 `.background-runner.json`，检查子进程是否存活 |
| `wait_for_background_runner(session_id, timeout=None)` | 等待子进程完成（可选，用于同步场景） |
| `cleanup_background_runner(session_id)` | 清理 marker 文件 |

##### Fork 实现细节

```python
def launch_background_runner(session, provider, tool_registry, tool_context, max_turns):
    session.save()  # 确保 JSONL transcript 存在

    pid = os.fork()
    if pid > 0:
        # 父进程：记录子进程信息，立即返回
        _write_runner_marker(session.session_id, pid)
        return pid
    else:
        # 子进程：脱离终端，运行 headless agent
        os.setsid()  # 新会话组，不受父进程终端影响
        # 关闭 Textual 的文件描述符（stdin/stdout/stderr 重定向）
        sys.stdin.close()
        # 重定向 stdout/stderr 到日志文件
        log_path = _runner_log_path(session.session_id)
        sys.stdout = open(log_path, 'a')
        sys.stderr = open(log_path, 'a')
        # 运行 agent loop
        _run_agent_headless(session, provider, tool_registry, tool_context, max_turns)
        os._exit(0)
```

##### Headless Agent Loop

```python
def _run_agent_headless(session, provider, tool_registry, tool_context, max_turns):
    """子进程入口：驱动 agent loop，将输出写入 JSONL transcript。"""
    import asyncio
    from src.query.agent_loop_compat import run_query_as_agent_loop, build_effective_system_prompt
    from src.services.session_storage import SessionStorage
    from src.outputStyles import resolve_output_style

    storage = SessionStorage(session_id=session.session_id)

    style_prompt = resolve_output_style(
        getattr(tool_context, "output_style_name", None),
        getattr(tool_context, "output_style_dir", None),
    ).prompt
    effective_system_prompt = build_effective_system_prompt(style_prompt, tool_context)

    # on_message: 将每条消息写入 JSONL transcript
    def _on_message(msg):
        try:
            storage.write_message(msg)
            storage.flush()
        except Exception:
            pass

    # 不需要 on_text_chunk（headless 不需要实时流式渲染）
    # 权限处理：后台模式自动批准所有权限（用户已在 Ctrl+B 前确认）
    tool_context.permission_context.mode = "bypassPermissions"

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(run_query_as_agent_loop(
            initial_messages=list(session.conversation.messages),
            provider=provider,
            tool_registry=tool_registry,
            tool_context=tool_context,
            system_prompt=effective_system_prompt,
            max_turns=max_turns,
            on_message=_on_message,
        ))
        _update_runner_status(session.session_id, "completed")
    except Exception as e:
        _update_runner_status(session.session_id, "failed", error=str(e))
    finally:
        loop.close()
        # 写入完成标记，resume 端可检测
        storage.write_raw({"role": "system", "content": "__background_complete__"})
        storage.flush()
```

#### 现有模块修改

##### 1. `src/tui/app.py` — `action_agent_background()` 重构

```python
def action_agent_background(self) -> None:
    """Handle Ctrl+B — fork agent into background, exit to terminal."""
    from src.agent.background_runner import launch_background_runner

    # 仅在 agent 正忙时有意义（空闲时只是保存退出）
    is_busy = self._agent_bridge.busy

    try:
        self.session.save()
    except Exception:
        pass

    if is_busy:
        # 取消前台 agent run（将在子进程中重新运行）
        self._agent_bridge.cancel(reason="background_promotion")
        # 等待 worker 停止
        import time; time.sleep(0.1)

        # Fork 后台 runner
        try:
            pid = launch_background_runner(
                session=self.session,
                provider=self.provider,
                tool_registry=self.tool_registry,
                tool_context=self.tool_context,
                max_turns=self.max_turns,
            )
        except Exception:
            pid = None

    sid = getattr(self.session, "session_id", None) or ""
    # 使用新标记区分"有后台 agent"和"仅保存退出"
    self.exit(result=("__BACKGROUND_EXIT__", sid, bool(is_busy)))
```

##### 2. `src/tui/agent_bridge.py` — TailFollower 完成检测

在 `_run_tail_follower` 的 `_follow()` 异步迭代中添加完成标记检测：

```python
async for msg_dict in follower:
    if msg_dict is None:
        continue

    # 检测后台 agent 完成标记
    if (msg_dict.get("role") == "system" and
        msg_dict.get("content") == "__background_complete__"):
        self._post(AgentRunFinished(
            response_text="",
            num_turns=0,
            usage=None,
            error=None,
        ))
        break

    # 现有的消息分发逻辑（role-based dispatch）...
```

##### 3. `src/entrypoints/tui.py` — 退出处理增强

```python
# run_tui() 末尾
if isinstance(result, tuple) and result[0] in ("__FULL_EXIT__", "__BACKGROUND_EXIT__"):
    session_id = result[1] if len(result) > 1 else ""
    has_bg_agent = result[2] if len(result) > 2 else False
    from rich.console import Console as RichConsole
    rc = RichConsole()
    if session_id:
        if has_bg_agent:
            rc.print(
                f"\n  [bold green]Agent is running in background.[/bold green]\n"
                f"  Resume with:\n"
                f"    [cyan]clawcodex --tui --resume {session_id}[/cyan]"
            )
        else:
            rc.print(
                f"\n  [bold yellow]Session {session_id} saved.[/bold yellow] Resume with:\n"
                f"    [cyan]clawcodex --tui --resume {session_id}[/cyan]"
            )
```

##### 4. `src/repl/core.py` — `_handoff_to_textual_tui` 退出处理同步更新

```python
# _handoff_to_textual_tui() 中 Ctrl+B 处理分支
if isinstance(result, tuple) and result[0] == "__BACKGROUND_EXIT__":
    session_id = result[1] if len(result) > 1 else ""
    has_bg_agent = result[2] if len(result) > 2 else False
    if session_id and has_bg_agent:
        self.console.print(
            f"\n  [bold green]Agent is running in background.[/bold green] Resume with:\n"
            f"    [cyan]clawcodex --tui --resume {session_id}[/cyan]"
        )
    elif session_id:
        self.console.print(
            f"\n  [bold yellow]Session {session_id} saved.[/bold yellow] Resume with:\n"
            f"    [cyan]clawcodex --tui --resume {session_id}[/cyan]"
        )
    self.console.print("[dim]Exiting clawcodex...[/dim]")
    sys.exit(0)

# 向下兼容旧的 __FULL_EXIT__ 标记
elif isinstance(result, tuple) and result[0] == "__FULL_EXIT__":
    # ... 保持原有逻辑不变
```

##### 5. `src/agent/session.py` — `resume_with_tail()` 增强

```python
@classmethod
def resume_with_tail(cls, session_id: str) -> tuple[Optional['Session'], Any | None]:
    """Resume a session and optionally attach a TailFollower.

    如果有后台 runner 正在运行，必须附加 TailFollower 以便
    实时显示后台 agent 的增量输出。
    """
    session = cls.resume(session_id)
    if session is None:
        return None, None

    # 检查是否有后台 runner 正在运行
    from src.agent.background_runner import get_background_runner_status
    bg_status = get_background_runner_status(session_id)
    logger.info(
        "resume_with_tail: session=%s, bg_status=%s",
        session_id, bg_status,
    )

    tail_follower = None
    try:
        from src.services.session_storage import SessionStorage
        from src.services.tail_follower import TailFollower

        storage = SessionStorage(session_id=session_id)
        transcript_path = storage._transcript_path
        if transcript_path.exists():
            current_size = transcript_path.stat().st_size
            tail_follower = TailFollower(str(transcript_path))
            tail_follower._offset = current_size
    except Exception:
        tail_follower = None

    return session, tail_follower
```

##### 6. `src/agent/background_state.py` — 文档注释更新

原 `background_state.py` 中的 `background_signal` / `is_backgrounded` 机制为上游
TypeScript 的 `Promise.race` 竞速模式翻译，但在 TUI agent loop 路径中未被调用。
Fork-Continue 模式取代了原有的信号竞态设计：

```python
"""Process-level background signal manager (singleton).

NOTE (Fork-Continue redesign): The signal/flag pattern defined here
mirrors the TS ``Promise.race`` pattern from ``foreground_promotion.py``
but was never wired into the TUI agent loop path.  The Ctrl+B feature
now uses the Fork-Continue model (``src/agent/background_runner.py``)
where the parent process exits and a forked child continues the agent.

This module is retained for:
  - backward compatibility with any code that reads ``is_backgrounded()``
  - potential future use by the REPL (non-TUI) path
  - test coverage of the ``run_with_background_escape`` race logic
"""
```

#### 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/agent/background_runner.py` | **新增** | 后台 runner 核心模块（fork + headless loop + marker 管理） |
| `src/tui/app.py` | 修改 | `action_agent_background()` 改为调用 `launch_background_runner()` + 新退出标记 `__BACKGROUND_EXIT__` |
| `src/tui/agent_bridge.py` | 修改 | `_run_tail_follower` 添加 `__background_complete__` 完成标记检测 |
| `src/entrypoints/tui.py` | 修改 | 退出处理区分"有/无后台 agent"；resume 时检查 bg runner 状态 |
| `src/repl/core.py` | 修改 | `_handoff_to_textual_tui` 退出处理同步更新 |
| `src/agent/session.py` | 微调 | `resume_with_tail()` 添加 bg runner 状态检查日志 |
| `src/agent/background_state.py` | 微调 | 更新文档注释，说明 Fork-Continue 模式替代了原始信号竞态设计 |

#### 并发安全保证

| 场景 | 保证 |
|------|------|
| JSONL 写入竞态 | `SessionStorage.write_message` 已有 atomic write（`_atomic_write`）；父进程退出后子进程独占写入，不存在并发写入 |
| Fork 时序 | fork 前先 `session.save()` 确保状态落盘；fork 后父进程立即退出，子进程从头开始 `run_query_as_agent_loop`，不依赖父进程的任何运行时状态 |
| 权限处理 | 后台模式使用 `bypassPermissions`，因为用户不在场无法交互式授权。Ctrl+B 本身就是用户的显式授权动作 |
| 僵尸进程 | 子进程 `os.setsid()` 后独立于父进程会话组；若子进程崩溃，marker 文件记录 `failed` 状态，resume 时可检测并提示 |

#### 边界情况处理

| 场景 | 处理方式 |
|------|----------|
| Ctrl+B 时 agent 空闲 | 仅保存会话 + 退出，无 fork（与当前行为一致） |
| Ctrl+B 时 agent 正在请求权限 | 取消当前 run，fork 后重新运行（headless bypass） |
| Resume 时后台 agent 已完成 | TailFollower 读到 `__background_complete__` 后停止，正常进入交互模式 |
| Resume 时后台 agent 已崩溃 | marker 文件为 `failed`，提示用户 "agent 遇到错误" 并显示日志路径 |
| 多次 Ctrl+B | 同一 session 只有一个 runner；检查 marker 文件，若已有 running 则提示 |
| `os.fork()` 不可用（Windows） | 回退到当前行为（保存退出，不 fork），打印提示 |

#### Windows 兼容性设计

`os.fork()` 在 Windows 上不可用。设计方案提供降级路径：

```python
def launch_background_runner(...):
    if not hasattr(os, 'fork'):
        # Windows: 使用 subprocess 启动 headless runner
        import subprocess
        subprocess.Popen(
            [sys.executable, '-m', 'src.agent.background_runner',
             '--session-id', session.session_id,
             '--max-turns', str(max_turns)],
            stdout=open(log_path, 'a'),
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # Windows
        )
        return  # 父进程继续退出流程
```

这需要一个 `__main__.py` 入口点，但架构上与 fork 模式一致：子进程独立运行 headless agent loop，通过 JSONL 通信。

#### 实现优先级与里程碑

| 里程碑 | 内容 | 依赖 |
|--------|------|------|
| M1 | `background_runner.py` 核心模块 + fork 逻辑 | 无 |
| M2 | `action_agent_background()` 重构 + 退出标记 | M1 |
| M3 | TailFollower 完成检测 + `__background_complete__` | M1 |
| M4 | `tui.py` / `repl/core.py` 退出处理增强 | M2 |
| M5 | `resume_with_tail()` bg runner 状态集成 | M1, M3 |
| M6 | Windows subprocess 降级路径 | M1 |
| M7 | 端到端测试（Ctrl+B → resume → 验证 agent 输出完整） | M1-M5 |

---

### 2.13 REPL 模式 Ctrl+B 后台运行支持（F-33）

**状态**: 📋 规划中
**目标**: REPL（非 TUI）模式下按 Ctrl+B 触发 Agent 后台持续运行，与 TUI 的 `action_agent_background()` 行为对齐

#### 问题背景

当前 Ctrl+B 仅在 TUI 模式下有效：

1. TUI 按 Ctrl+B → `action_agent_background()` → `launch_background_runner()` → fork 子进程继续运行 agent
2. REPL 按 Ctrl+B → **无响应**，LiveStatus 的按键绑定中不包含 Ctrl+B
3. REPL 的 `chat()` 方法中，ESC/Ctrl+C 走 `on_cancel` 回调（仅取消，不后台化）
4. REPL 退出（Ctrl+C/Ctrl+D）只打印 "Interrupted"，不会触发后台运行

**核心缺陷**：REPL 模式完全没有 Ctrl+B → 后台运行的路径。

#### 解耦设计原则

为了便于原项目 REPL 升级，Ctrl+B 的实现必须与现有组件最小化耦合：

1. **不修改 `LiveStatus` 内部**：`LiveStatus` 是一个独立的 `prompt_toolkit` 应用，已有 ESC/c-m/c-o/c-t/s-tab 绑定。Ctrl+B 绑定应通过**外部注入**而非修改 `LiveStatus` 源码
2. **复用 `background_runner.py`**：fork/subprocess 逻辑已经在 `background_runner.py` 中完整实现，REPL 只需调用 `launch_background_runner()`
3. **通过 `on_background` 回调注入**：与 `on_cancel`/`on_submit`/`on_expand` 同构的新回调参数，由 `LiveStatus` 在检测到 Ctrl+B 时调用
4. **`BackgroundEscape` 信号对象**：`chat()` 方法通过检查返回的异常/信号判断是否需要 fork 后台，而非在 LiveStatus 内部直接执行 fork

#### 架构设计

```
                    ┌───────────────────────────────────────┐
  Ctrl+B 触发 ───→ │  LiveStatus (prompt_toolkit app)      │
                    │  on_background 回调 → 设置信号标志     │
                    │  停止 LiveStatus + 取消当前 agent run  │
                    └──────────────┬────────────────────────┘
                                   │
                    ┌──────────────▼────────────────────────┐
                    │  chat() 捕获 BackgroundEscape 信号     │
                    │  1. session.save()                     │
                    │  2. launch_background_runner(...)      │
                    │  3. 打印后台运行提示 + resume 命令      │
                    │  4. sys.exit(0)                        │
                    └───────────────────────────────────────┘
```

#### 核心组件

##### 1. `src/repl/background_escape.py` — 信号对象（新增）

解耦的关键：将 Ctrl+B 的"意图"从 LiveStatus 的按键处理中分离出来，变成一个可被 `chat()` 捕获的信号。

```python
class BackgroundEscape(Exception):
    """Raised when the user presses Ctrl+B during an active agent run.

    The REPL's ``chat()`` method catches this exception to trigger
    the background runner fork.  Using an exception (rather than a
    callback that directly calls ``os.fork``) keeps the LiveStatus
    keybinding handler free of process-management logic — it only
    signals intent, ``chat()`` decides what to do about it.
    """

    def __init__(self, message: str = "Background escape requested") -> None:
        super().__init__(message)
```

##### 2. `src/repl/live_status.py` — 新增 `on_background` 参数（微调）

在 `LiveStatus.__init__` 中新增 `on_background: Callable[[], None] | None = None` 参数，并在内部 keybinding 中添加 Ctrl+B 绑定：

```python
# LiveStatus.__init__ 新增参数
def __init__(
    self,
    message: str,
    *,
    on_cancel: Callable[[], None] | None = None,
    on_submit: Callable[[str], None] | None = None,
    on_expand: Callable[[], None] | None = None,
    on_background: Callable[[], None] | None = None,   # ← 新增
    completer=None,
    verbose: bool = False,
) -> None:
    ...
    self._on_background = on_background

# 在 _run_thread 的 bindings 中新增
@bindings.add("c-b")
def _on_background(event):  # type: ignore[no-untyped-def]
    cb = self._on_background
    if cb is None:
        return
    try:
        cb()
    except Exception:
        pass
```

**变更范围极小**：仅增加一个回调参数 + 一个 keybinding handler，不改变任何现有逻辑。

##### 3. `src/repl/core.py` — `chat()` 方法的两个路径（修改）

`chat()` 中有两条 agent 运行路径，各自需要添加 `on_background` 回调：

**路径 A：Direct Stream（简单流式，无工具循环）**

```python
# chat() 中的 direct stream 路径
_bg_escape_flag: list[bool] = [False]

def _on_background_direct() -> None:
    _bg_escape_flag[0] = True
    self._direct_stream_abort = True  # 同时取消流

with LiveStatus(
    self._status_message(),
    on_cancel=_cancel_direct_stream,
    on_submit=_on_submit_direct,
    on_expand=self._do_expand_last,
    on_background=_on_background_direct,    # ← 新增
    completer=self.completer,
) as status:
    ...

# LiveStatus 退出后检查
if _bg_escape_flag[0]:
    raise BackgroundEscape()
```

**路径 B：Engine Mode（工具循环，QueryEngine 驱动）**

```python
# chat() 中的 engine 路径
_bg_escape_flag_engine: list[bool] = [False]

def _on_background_engine() -> None:
    _bg_escape_flag_engine[0] = True
    try:
        engine.interrupt()   # 同时中断引擎
    except Exception:
        pass

with LiveStatus(
    self._status_message(),
    on_cancel=_cancel_engine,
    on_submit=_on_submit_engine,
    on_expand=self._do_expand_last,
    on_background=_on_background_engine,    # ← 新增
    completer=self.completer,
) as status:
    ...

# LiveStatus 退出后检查
if _bg_escape_flag_engine[0]:
    raise BackgroundEscape()
```

**chat() 方法外层捕获**：

```python
def chat(self, user_input: str, max_turns: int | None = None):
    try:
        ...  # 现有逻辑
    except BackgroundEscape:
        self._handle_background_escape()
        return
    except Exception as e:
        ...  # 现有错误处理

def _handle_background_escape(self) -> None:
    """Ctrl+B 后台运行处理。"""
    from src.agent.background_runner import launch_background_runner

    self.session.save()

    # 仅在 agent 正在运行时有意义
    pid = launch_background_runner(
        session=self.session,
        provider=self.provider,
        tool_registry=self.tool_registry,
        tool_context=self.tool_context,
        max_turns=20,
    )

    session_id = self.session.session_id
    if pid and pid > 0:
        self.console.print(
            "\
  [bold green]Agent is running in background[/bold green]"
        )
    else:
        self.console.print(
            f"\
  [bold yellow]Session {session_id} saved.[/bold yellow]"
        )

    self.console.print(
        f"  [dim]Resume with: clawcodex --resume {session_id}[/dim]"
    )
    self.console.print("[dim]Exiting clawcodex...[/dim]")
    sys.exit(0)
```

##### 4. `src/repl/core.py` — REPL 空闲状态下的 Ctrl+B（新增绑定）

在 REPL 的**主循环空闲态**（等待用户输入时），Ctrl+B 应该只是保存退出：

```python
# __init__ 的 self.bindings 中新增
@self.bindings.add("c-b")
def _background_or_exit(event):  # type: ignore[no-untyped-def]
    """Ctrl+B: save session and exit to shell.

    When the agent is idle (prompt is showing), this just saves and exits.
    When the agent is active (LiveStatus is showing), the LiveStatus's
    own Ctrl+B binding fires instead and triggers BackgroundEscape.
    """
    self.session.save()
    session_id = self.session.session_id
    self.console.print(
        f"\
  [bold yellow]Session {session_id} saved.[/bold yellow]"
    )
    self.console.print(
        f"  [dim]Resume with: clawcodex --resume {session_id}[/dim]"
    )
    self.console.print("[dim]Exiting clawcodex...[/dim]")
    raise EOFError()  # 让 run() 的 while 循环正常退出
```

注意：`run()` 方法已经有 `except EOFError: break` 处理，无需额外修改。

#### 信号流对比

| 事件 | TUI 路径 | REPL 路径（F-33 新增） |
|------|---------|----------------------|
| Agent 空闲 + Ctrl+B | 无响应（TUI 无此场景） | 保存退出，打印 resume 提示 |
| Agent 运行中 + Ctrl+B | `action_agent_background()` → fork → `__BACKGROUND_EXIT__` | `on_background` 回调 → `BackgroundEscape` 异常 → `_handle_background_escape()` → fork → `sys.exit(0)` |
| Agent 运行中 + ESC | 取消 agent run | 取消 agent run（现有行为不变） |
| `--resume` 恢复 | `Session.resume_with_tail()` + TailFollower | `Session.resume()` + 重新进入 REPL（现有行为） |

#### 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/repl/background_escape.py` | **新增** | `BackgroundEscape` 异常类，解耦信号 |
| `src/repl/live_status.py` | **微调** | 新增 `on_background` 参数 + Ctrl+B keybinding |
| `src/repl/core.py` | **修改** | chat() 两路径添加 `on_background` 回调；外层捕获 `BackgroundEscape`；空闲态 Ctrl+B 绑定 |
| `src/repl/__init__.py` | **微调** | 导出 `BackgroundEscape` |

**不修改的文件**（关键解耦点）：
- `src/agent/background_runner.py` — 完全复用，无需修改
- `src/agent/session.py` — `save()` / `resume()` 已有，无需修改
- `src/cli.py` — `start_repl()` 无需修改，`--resume` 路径已通
- `src/utils/abort_controller.py` — 取消逻辑不受影响

#### 解耦优势

1. **`BackgroundEscape` 作为信号边界**：LiveStatus 只负责检测按键和触发回调，不感知 fork/子进程逻辑。如果上游修改 LiveStatus 的按键处理方式，只需确保 `on_background` 被正确触发即可
2. **`on_background` 与 `on_cancel` 同构**：新增参数遵循已有的回调模式，代码审查和上游合并的冲突最小
3. **`background_runner.py` 完全复用**：fork/subprocess/headless loop 逻辑不重复，TUI 和 REPL 共享同一套后台运行机制
4. **`BackgroundEscape` 是普通 Python 异常**：不依赖 `asyncio.Event` 或 `threading.Event` 等需要共享状态的机制，异常自然穿过调用栈

#### 边界情况

| 场景 | 处理方式 |
|------|----------|
| Ctrl+B 时 agent 空闲（LiveStatus 未显示） | 主循环的 Ctrl+B 绑定生效，保存退出 |
| Ctrl+B 时 agent 运行中 | LiveStatus 的 Ctrl+B 绑定生效 → `BackgroundEscape` → fork |
| Ctrl+B 时 agent 正在请求权限 | `engine.interrupt()` 取消当前 run，fork 后重新运行（headless bypass） |
| Windows（无 os.fork） | `launch_background_runner` 内部已有 `_launch_via_subprocess` 降级路径 |
| 快速按 ESC 后 Ctrl+B | ESC 先触发 cancel，LiveStatus 退出，然后 chat() 正常返回；Ctrl+B 在下次 agent 运行时生效 |
| `on_background` 为 None（未传入） | Ctrl+B 绑定 handler 直接 return，无副作用 |

#### 实现优先级与里程碑

| 里程碑 | 内容 | 依赖 | 估计工作量 |
|--------|------|------|-----------|
| M1 | `background_escape.py` 异常类 | 无 | 5 min |
| M2 | `live_status.py` 新增 `on_background` 参数 + Ctrl+B 绑定 | M1 | 15 min |
| M3 | `core.py` chat() direct stream 路径 | M1, M2 | 15 min |
| M4 | `core.py` chat() engine 路径 | M1, M2 | 15 min |
| M5 | `core.py` 空闲态 Ctrl+B 绑定 + `_handle_background_escape()` | M1 | 15 min |
| M6 | 手动集成测试 | M1-M5 | 10 min |

### 2.14 CLI/TUI Frontend 解耦架构（F-34）

#### 2.14.1 问题现状

当前 CLI、TUI、Headless 三个入口点各自重复构造核心依赖（Provider、ToolRegistry、ToolContext、Session），耦合图谱如下：

```
 src/cli.py (604行)
   ├── argparse 定义所有入口参数
   ├── _resolve_permission_state()           ← 共享，但存 args 上
   ├──→ _run_print_mode() → entrypoints/headless.py
   │     └── 自建 provider/registry/context/session
   ├──→ _run_tui_mode()   → entrypoints/tui.py → tui/app.py
   │     └── 自建 provider/registry/context/session
   └──→ start_repl()     → repl/core.py (ClawcodexREPL)
         └── 自建 provider/registry/context/session
```

**核心问题**：

| 问题 | 后果 |
|------|------|
| Provider/Registry/Session 构造代码 ×3 处 | 改动需同步 N 个入口，易遗漏 |
| argparse 参数与 frontend 选择耦合 | 加新 frontend 需改 argparse + dispatch + N 个 `_run_*_mode()` |
| Agent 循环实现 ×2（AgentBridge vs repl/core 内联） | bug 修复和行为变更需改两套代码 |
| 权限状态通过 args 传递 | 每个 frontend 要自己解释权限字符串配置 tool context |

#### 2.14.2 设计目标

1. **统一 Runtime 初始化**：消除 provider/registry/context/session 的三重复造
2. **Frontend 协议化**：任何 UI 实现只需实现 `Frontend` 协议即可接入
3. **Agent 循环单一实现**：一个 `AgentEngine` 供所有 frontend 使用
4. **插件式 frontend 注册**：`cli.py` 不再需要知道有哪些 frontend

**当前迁移约束**：项目级二开边界约束已推广至全项目范围。所有下游/定制功能（frontend 行为、runtime 接线、命令、UI 定制、provider/tool 编排变更）默认只能进入 `clawcodex_ext/*`；`src/cli.py`、`src/entrypoints/tui.py`、`src/tui/*` 和 `src/upstream/<rev>/*` 只保留最小适配、上游同步或窄范围 bug fix。具体示例路径：`clawcodex_ext/cli`、`clawcodex_ext/tui`、`clawcodex_ext/frontend`、`clawcodex_ext/runtime`。

**当前迁移进度**：✅ F-34 Phase 1-3 全部完成。
- Phase 1: CLI parser/dispatch 所有权迁入 `clawcodex_ext/cli/`
- Phase 2: `RuntimeContext` 工厂（`clawcodex_ext/runtime/context.py`）+ Frontend 协议/注册表（`clawcodex_ext/frontend/`）
- Phase 3: `ClawCodexExtTUI` 8 个扩展钩子就绪（`clawcodex_ext/tui/app.py`）

#### 2.14.3 架构概览

```
 src/runtime/
   ├── __init__.py           # 公共导出
   ├── context.py            # RuntimeContext（统一的 factory）
   ├── protocol.py           # Frontend 协议
   ├── events.py             # 标准化事件类型
   ├── engine.py             # AgentEngine（从 frontend 解耦的 agent 循环）
   └── registry.py           # Frontend 注册表（插件式）
```

#### 2.14.4 核心组件设计

##### 1. `RuntimeContext` — 统一运行时上下文

```python
# src/runtime/context.py

@dataclass
class RuntimeOptions:
    """构建 RuntimeContext 的选项，从 CLI args 或 API 调用中提取。"""
    provider_name: str | None = None
    model: str | None = None
    workspace_root: Path | None = None
    max_turns: int = 20
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    permission_mode: str = "default"
    is_bypass_permissions_mode_available: bool = False
    resume_session_id: str | None = None
    resume_browse: bool = False
    stream: bool = True
    verbose: bool = False


@dataclass
class RuntimeContext:
    """每个 frontend 启动时必需的共享上下文。

    取代三个入口点各自重复的 provider/registry/context/session 构造。
    """
    provider: object                         # BaseProvider 实例
    provider_name: str
    model: str
    workspace_root: Path
    tool_registry: ToolRegistry
    tool_context: ToolContext
    session: Session
    permission_mode: str
    is_bypass_permissions_mode_available: bool
    max_turns: int = 20
    stream: bool = True
    cost_tracker: CostTracker | None = None
    history: HistoryLog | None = None

    @classmethod
    def build(cls, options: RuntimeOptions) -> RuntimeContext:
        """统一的 factory 方法，替代 3 套重复代码。

        负责：
        1. 解析 provider_name → 构建 provider 实例
        2. 调用 build_default_registry()
        3. 创建 ToolContext
        4. 创建或恢复 Session
        5. 应用工具过滤（allowed/disallowed）
        6. 应用权限状态
        """
        # ... 统一实现，消除三个入口点的重复代码
```

##### 2. `Frontend` 协议

```python
# src/runtime/protocol.py

class Frontend(Protocol):
    """UI 前端必须实现的协议。

    实现此协议即可注册为 clawcodex 的可用前端。
    """

    # 元信息（供 CLI --help 和注册表使用）
    name: str                                  # 唯一标识，如 "repl", "tui", "headless"
    display_name: str                          # 显示名称，如 "Interactive REPL"
    description: str                           # 简短描述

    def run(self, ctx: RuntimeContext) -> int:
        """运行前端，返回 CLI 退出码。"""
        ...

    # 可选 hook
    def on_start(self, ctx: RuntimeContext) -> None: ...
    def on_finish(self, exit_code: int) -> None: ...

    # 可选：此前端支持的 CLI 参数组
    @classmethod
    def argparse_group(cls, parser: argparse.ArgumentParser) -> None: ...
```

##### 3. `AgentEngine` — 统一 Agent 循环

```python
# src/runtime/engine.py

@dataclass
class AgentEngine:
    """从 frontend 解耦的 agent 循环，提供统一的 submit/cancel/event 接口。

    替代：
    - tui/agent_bridge.py (TUI 专用)
    - repl/core.py 中的内联 agent 循环
    """

    session: Session
    provider: object
    tool_registry: ToolRegistry
    tool_context: ToolContext
    max_turns: int = 20
    stream: bool = True

    def submit(self, prompt: str) -> bool:
        """提交用户输入，启动 agent 循环。返回 False 表示忙。"""
        ...

    def cancel(self) -> bool:
        """取消当前 agent 运行。返回 False 表示无运行中。"""
        ...

    # 事件流（订阅者模式）
    def subscribe(self, event_type: type, callback: Callable) -> None: ...
    def unsubscribe(self, event_type: type, callback: Callable) -> None: ...

    # 生命周期
    async def run(self) -> None: ...
    def stop(self) -> None: ...
```

##### 4. `FrontendRegistry` — 插件式注册表

```python
# src/runtime/registry.py

_frontends: dict[str, type[Frontend]] = {}

def register(name: str, frontend_cls: type[Frontend]) -> None:
    """注册一个前端实现。"""
    _frontends[name] = frontend_cls

def get(name: str) -> type[Frontend] | None:
    """按名称获取前端类。"""
    return _frontends.get(name)

def list_frontends() -> dict[str, type[Frontend]]:
    """返回所有已注册的前端。"""
    return dict(_frontends)

def available_names() -> list[str]:
    """返回所有已注册前端名称列表（按注册顺序）。"""
    return list(_frontends.keys())

def dispatch(args) -> int:
    """根据 CLI args 选择并运行前端。

    Args:
        args: argparse.Namespace，含 ``_frontend`` 属性

    Returns:
        CLI 退出码
    """
    name = getattr(args, '_frontend', None) or os.environ.get('CLAWCODEX_FRONTEND', 'repl')
    frontend_cls = get(name)
    if frontend_cls is None:
        console = Console(stderr=True)
        console.print(f"[red]Unknown frontend: {name}[/red]")
        console.print(f"Available: {', '.join(available_names())}")
        return 1

    options = _build_runtime_options(args)
    ctx = RuntimeContext.build(options)
    return frontend_cls().run(ctx)
```

#### 2.14.5 标准事件类型

```python
# src/runtime/events.py

@dataclass
class TextChunkEvent:
    """LLM 返回的文本片段（流式）。"""
    text: str

@dataclass
class ToolUseEvent:
    """Agent 请求使用工具。"""
    tool_name: str
    tool_input: dict
    tool_use_id: str

@dataclass
class ToolResultEvent:
    """工具执行结果。"""
    tool_use_id: str
    tool_name: str
    output: str
    is_error: bool

@dataclass
class PermissionRequested:
    """工具需要用户授权。"""
    tool_name: str
    tool_input: dict
    permission_id: str
    resolve: Callable[[bool], None]

@dataclass
class ErrorEvent:
    """Agent 循环中发生错误。"""
    error: str
    fatal: bool = False

@dataclass
class DoneEvent:
    """Agent 循环完成。"""
    total_turns: int
    total_cost: float | None
```

#### 2.14.6 分阶段实施计划

##### Phase 1 — 提取 `RuntimeContext`（消除 3 处重复构造）

| 步骤 | 内容 | 文件 | 工作量 |
|------|------|------|--------|
| 1.1 | 创建 `src/runtime/context.py`（`RuntimeOptions` + `RuntimeContext.build()`） | 新增 | 2h |
| 1.2 | 创建 `src/runtime/__init__.py`（导出） | 新增 | 5min |
| 1.3 | 修改 `src/entrypoints/tui.py` → 使用 `RuntimeContext.build()` | 修改 | 30min |
| 1.4 | 修改 `src/repl/core.py` → `ClawcodexREPL` 接受 `RuntimeContext` | 修改 | 30min |
| 1.5 | 修改 `src/entrypoints/headless.py` → 使用 `RuntimeContext.build()` | 修改 | 30min |
| 1.6 | 验证：三入口点行为不变 | 测试 | 30min |

**Phase 1 后状态**：三入口点各减 30-50 行重复代码

##### Phase 2 — 提取 `AgentEngine`（统一 agent 循环）

| 步骤 | 内容 | 文件 | 工作量 |
|------|------|------|--------|
| 2.1 | 创建 `src/runtime/events.py`（标准事件类型） | 新增 | 30min |
| 2.2 | 创建 `src/runtime/engine.py`（`AgentEngine`） | 新增 | 4h |
| 2.3 | 修改 `tui/agent_bridge.py` → 封装/委派给 `AgentEngine` | 修改 | 2h |
| 2.4 | 修改 `repl/core.py` → 使用 `AgentEngine` | 修改 | 2h |
| 2.5 | 集成测试：TUI + REPL 正常 submit/cancel/event | 测试 | 1h |

##### Phase 3 — Frontend 协议 + 注册表（插件化）

| 步骤 | 内容 | 文件 | 工作量 |
|------|------|------|--------|
| 3.1 | 创建 `src/runtime/protocol.py`（`Frontend` 协议） | 新增 | 30min |
| 3.2 | 创建 `src/runtime/registry.py`（注册表 + dispatch） | 新增 | 1h |
| 3.3 | 实现 `ReplFrontend`、`TuiFrontend`、`HeadlessFrontend` | 新增 | 2h |
| 3.4 | 修改 `src/cli.py` → 使用 `registry.dispatch()` + 注册 | 修改 | 1h |
| 3.5 | 注册 `claude_repl` 和 `clawcodex_cli_integration` 的 frontend | 注册 | 各 1h |

##### Phase 4（可选）— CLI 参数插件化

| 步骤 | 内容 | 文件 | 工作量 |
|------|------|------|--------|
| 4.1 | Frontend 协议增加 `argparse_group()` 类方法 | 修改 protocol | 30min |
| 4.2 | CLI 遍历注册表收集参数组 | 修改 cli.py | 1h |
| 4.3 | 各 frontend 实现自己的参数组 | 各 frontend | 各 30min |

#### 2.14.7 文件变更清单

| 操作 | 文件路径 | Phase |
|------|----------|-------|
| 新增 | `src/runtime/__init__.py` | 1 |
| 新增 | `src/runtime/context.py` | 1 |
| 新增 | `src/runtime/events.py` | 2 |
| 新增 | `src/runtime/engine.py` | 2 |
| 新增 | `src/runtime/protocol.py` | 3 |
| 新增 | `src/runtime/registry.py` | 3 |
| 修改 | `src/cli.py` | 1-3 |
| 修改 | `src/entrypoints/tui.py` | 1-3 |
| 修改 | `src/entrypoints/headless.py` | 1-3 |
| 修改 | `src/repl/core.py` | 1-3 |
| 修改 | `src/tui/app.py` | 1-3 |
| 修改 | `src/tui/agent_bridge.py` | 2 |

#### 2.14.8 集成外部 Frontend

##### 集成 `claude_repl`

```python
# claude_repl 项目内
from clawcodex.runtime import Frontend, RuntimeContext, register

class ClaudeReplFrontend:
    name = "claude-repl"
    display_name = "Claude REPL"
    description = "Claude 原生命令行 REPL 体验"

    def run(self, ctx: RuntimeContext) -> int:
        # 使用 ctx.provider, ctx.session, ctx.tool_registry
        # 运行 claude_repl 自己的 REPL 循环
        ...

# 注册
register("claude-repl", ClaudeReplFrontend)
```

##### 集成 `clawcodex_cli_integration`

```python
# clawcodex_cli_integration 项目内
from clawcodex.runtime import Frontend, RuntimeContext, register

class CliIntegrationFrontend:
    name = "cli-integration"
    display_name = "CLI Integration"
    description = "集成式 CLI 工具包"

    def run(self, ctx: RuntimeContext) -> int:
        # 使用 ctx 运行集成式 CLI
        ...

register("cli-integration", CliIntegrationFrontend)
```

使用方式：
```bash
# 指定 frontend
clawcodex --frontend claude-repl -p "hello"
clawcodex --frontend cli-integration --tui

# 环境变量全局切换
export CLAWCODEX_FRONTEND=claude-repl
clawcodex  # 自动使用 claude-repl
```

#### 2.14.9 与上游解耦的关系

解耦后的架构使得二开版本和上游版本能共享同一套 frontend 协议：

```
上游版本:
  clawcodex (upstream)
    └── 注册 repl, tui, headless

下游二开:
  clawcodex (clawcodex)
    ├── 注册 repl (二改版), tui (二改版), headless
    └── 注册 claude-repl (新增)
    └── 注册 cli-integration (新增)
```

**好处**：
- 上游升级 `repl`/`tui` 模块时，只需更新对应的 Frontend 实现
- 二开版本保持自己的 frontend 自定义行为，不影响上游 core
- 第三方 frontend 无需修改 clawcodex 核心代码

#### 2.14.10 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| `RuntimeContext.build()` 耦合了 provider/registry 实现 | 更换 provider/registry 需要修改 build | 抽象 ProviderFactory/RegistryFactory，可配置 |
| `AgentEngine` 与现有 AgentBridge 行为差异 | TUI 行为回归 | Phase 2 中保留 AgentBridge 接口，内部委派，逐步替换 |
| 第三方 frontend 需要引用 `clawcodex.runtime` | import 耦合 | runtime 模块设计为对外无副作用，仅依赖公共类型 |
| 重构过程中破坏已实现功能 | 开发中断 | 每个 Phase 完成后执行完整的集成测试套件 |

---

*文档更新时间: 2026-05-30*

*版本 v1.7 更新：F-34 Phase 1-3 全部完成。CLI parser/dispatch 迁入 `clawcodex_ext/cli`；RuntimeContext 工厂 + Frontend 协议/注册表完成；`ClawCodexExtTUI` 8 个扩展钩子就绪。*
*版本 v2.0 更新：新增 F-35 二开特性统一切换架构设计，一个全局开关（CLAWCODEX_UPSTREAM_MODE）控制所有二开特性，文件级 import hook 实现模块替换，分批还原 584 个内联修改文件。*

---

### 2.15 F-35: 二开特性统一切换（上游纯净模式开关）（已简化）

#### 2.15.1 问题现状

F-34 解决了前端层的切换问题，但 `src/` 中还有大量二开特性与上游源码 58ea488 深度混合：

| 分类 | 数量 | 说明 |
|------|------|------|
| 二开新增文件（Only in src/） | 23 个 | 上游不存在，纯二开特性 |
| 二开修改文件（Files differ） | **584 个** | 上游源码被直接内联修改，二开内容与上游代码混编 |

**584 个内联修改文件是主要问题**。当前格局：

```
src/agent/conversation.py  ← 上游 58ea488 + 二开修改混在一起
src/repl/core.py           ← 上游 58ea488 + 吉祥物删除 + Ctrl+B + ...
src/tui/app.py             ← 上游 58ea488 + 二开改动
...共 584 个文件
```

这意味着：
- **不能直接切换回上游** — 因为 inline 修改无法单独关闭
- **上游升级困难** — 每次合入需要手动 diff 584 个文件
- **特性边界不清** — 不知道每个文件改了什么用途

#### 2.15.2 设计目标

1. **一个开关统一切换**：运行时通过 `CLAWCODEX_UPSTREAM_MODE=true` 决定加载上游版本还是二开版本
2. **零代码切换**：无需改 import、无需改代码，修改环境变量即可
3. **上游兼容**：上游模式开启时，系统行为与上游 58ea488 一致
4. **逐步迁移**：584 个文件不必一次全部提取，可以分批渐进

**当前迁移切片**：✅ 已完成 `clawcodex_ext/` 扩展边界建立 + CLI parser/dispatch 所有权迁入 `clawcodex_ext/cli` + RuntimeContext 工厂 + Frontend 协议/注册表 + TUI App 扩展钩子。项目级二开约束现已覆盖所有 downstream 特性（frontend 行为、runtime 接线、命令、UI 定制、provider/tool 编排），不仅仅限于 CLI/TUI。TUI Phase 4 采用方案 A：二开 TUI 拥有自己的 App class，通过 subclassing/composition 复用上游组件，`ClawCodexExtTUI` 提供 8 个扩展钩子方法。

#### 2.15.3 二开特性全景

##### A. 纯新增文件（23 个，上游不存在）

| 文件 | 功能 |
|------|------|
| `agent/background_runner.py`, `agent/background_state.py`, `repl/background_escape.py` | Ctrl+B 后台运行 |
| `agent/_outlines_adapter.py` | 结构化输出 |
| `agent/tool_authoring/` | 工具创作 |
| `cli/` (commands/input/permissions/renderer/session/tasks/utils/) | CLI 模块化重构 |
| `entrypoints/orchestrator.py` | Orchestrator 自主模式 |
| `context_system/_gitpython_adapter.py` | GitPython 上下文 |
| `hooks/_pluggy_adapter.py` | Pluggy 钩子系统 |
| `permissions/_treesitter_adapter.py` | Tree-sitter 权限分析 |
| `providers/_litellm_adapter.py` | LiteLLM Provider |
| `services/bridge/` | 桥接服务 |
| `services/tail_follower.py` | 尾部追随 |
| `settings/pydantic_adapter.py` | Pydantic 配置 |
| `skills/_frontmatter_adapter.py` | Frontmatter 技能 |
| `tool_system/tools/ask_issue_author.py` | 问题作者询问 |
| `tool_system/tools/create_agent_tool.py` | 动态工具创建 |
| `tool_system/tools/progress_report.py` | 进度报告 |
| `tool_system/tools/task_directives.py` | 任务指令 |
| `tool_system/tools/task_inspect.py` | 任务检查 |
| `tui/screens/permission_mode_picker.py` | 权限模式选择器 |
| `utils/session_watcher.py` | 会话监视器 |

##### B. 内联修改（584 个文件，与上游源码混编）

这些修改散布在 584 个上游文件中，包括但不限于：
- 吉祥物移除（`repl/core.py`, `tui/widgets/header.py`, `task_registry.py`）
- 权限模式增强（`cli.py`, `permissions/*`）
- TUI 响应性修复（`tui/app.py`, `tui/*`）
- Away-Summary（`services/away_summary.py` 等）
- Agent Loop Consolidation（`agent/run_agent.py`, `agent/prompt.py`）
- Advisor Token 计数（`agent/advisor.py`, `agent/conversation.py`）
- Session 恢复（`agent/session.py`, `assistant/*`）
- Cron 系统（`cron_system/*`）
- Bridge Phase 8-11（`bridge/*`）
- 文档变更（`FEATURE_LIST.md`, `CHANGELOG.md` 等）

#### 2.15.4 架构设计：二开/上游统一切换层

```
┌───────────────────────────────────────────┐
│              clawcodex 入口                 │
│  (cli.py / registry.dispatch())            │
└─────────────────┬─────────────────────────┘
                  │
                  ▼
┌───────────────────────────────────────────┐
│          Import Resolution Layer            │  ← 新增
│  上游模式 → 加载 src/upstream/58ea488/      │
│  二开模式 → 加载 src/ 下的二开模块          │
│  src/features/resolver.py                  │
└──────┬──────────────────────┬──────────────┘
       │                      │
       ▼                      ▼
┌──────────────┐   ┌────────────────────┐
│ src/         │   │ src/upstream/       │
│ (二开版本)   │   │ 58ea488/ (上游)     │
│              │   │                     │
│ 23 新增文件  │   │ 原始上游文件         │
│ 584 修改文件  │   │ (无二开改动)        │
└──────────────┘   └────────────────────┘
```

#### 2.15.5 核心组件设计

##### 1. 上游模式检测（最简单形式）

```python
# src/features/__init__.py

import os

def is_upstream_mode() -> bool:
    """检查是否以上游纯净模式运行。
    
    优先级：环境变量 CLAWCODEX_UPSTREAM_MODE > settings.json upstream_mode > 默认 False
    """
    # 默认二开模式（False），仅通过特定配置进入上游模式
    return os.environ.get("CLAWCODEX_UPSTREAM_MODE", "0").lower() in ("1", "true")

def init_features():
    """启动时初始化：根据模式决定 import 路径。"""
    if is_upstream_mode():
        _setup_upstream_resolver()
```

##### 2. 模块解析器（通过 Python import hooks）

核心思路：拦截 `import` 语句，根据上游模式决定加载 `src/` 版本还是 `src/upstream/58ea488/` 版本。

```python
# src/features/resolver.py

import sys
import importlib.abc
import importlib.util

class UpstreamResolver(importlib.abc.MetaPathFinder):
    """Import hook: 上游模式时拦截模块加载，指向 upstream 版本。
    
    upstream_mode=True:
        import repl.core
        → 实际上加载 src/upstream/58ea488/repl/core.py（纯上游）
        
    upstream_mode=False:
        import repl.core
        → 正常加载 src/repl/core.py（二开版本）
    """
    
    _UPSTREAM_MAPPINGS: dict[str, str] = {}  # module → upstream path
    
    @classmethod
    def register(cls, module_name: str, upstream_path: str) -> None:
        """注册需要重定向的模块映射。"""
        cls._UPSTREAM_MAPPINGS[module_name] = upstream_path
    
    def find_spec(self, fullname, path, target=None):
        if fullname not in self._UPSTREAM_MAPPINGS:
            return None
        
        upstream_path = self._UPSTREAM_MAPPINGS[fullname]
        spec = importlib.util.spec_from_file_location(fullname, upstream_path)
        return spec
```

##### 3. 提取方式：整体文件替换 vs 补丁

对于 584 个内联修改文件，分两种提取策略：

| 策略 | 适用场景 | 原理 | 复杂度 |
|------|----------|------|--------|
| **文件级替换** | 整个文件的大幅修改 | 二开版本保留在 `src/`，上游模式时重定向到 `src/upstream/58ea488/` 版 | 低 |
| **补丁（Patch）** | 文件内小范围修改 | 提取 diff 为独立 patch，启动时对上游模块应用 patch | 中 |

**推荐策略**：优先使用**文件级替换**（import hook 加载上游原版），只有小范围改动（几行）再用补丁。

#### 2.15.6 提取流程

```
原始状态（当前）:
  src/repl/core.py = 上游 58ea488 + 吉祥物删除 + Ctrl+B + 欢迎消息修改
  src/tui/app.py   = 上游 58ea488 + TUI 增强 + 权限模式选择器
  ... 共 584 个文件混编

步骤 A: 备份上游原版（一次完成）
  scripts/backup_upstream.sh
  → 将 src/upstream/58ea488/ 目录中尚未备份的原版文件补全
  → 注：目前已有一份 58ea488 快照，可能不完整

步骤 B: 还原内联修改文件为上游（分批）
  Phase 1: 高优先级文件还原
    cp src/upstream/58ea488/repl/core.py src/repl/core.py
    cp src/upstream/58ea488/tui/app.py src/tui/app.py
    cp src/upstream/58ea488/tui/widgets/header.py src/tui/widgets/header.py
    ...
  Phase 2: 中优先级文件还原
  Phase 3: 低优先级文件还原（依此类推）

步骤 C: 注册 import 映射
  src/features/resolver.py 中注册已还原的文件
    当 upstream_mode=True 时 → 加载 src/upstream/58ea488/ 版本
    当 upstream_mode=False 时 → 加载 src/ 版本（已还原，行为同上游...）

  注意：还原上游后，二开模式下需要保留二开行为。
  因此需要先把二开改动提取出来存到 src/features/patches/，启动时应用。
```

#### 2.15.7 完整数据流

```
                       启动
                         │
                     ┌───▼────┐
                     │ 检测    │
                     │ 模式    │
                     └───┬────┘
                    ╱          ╲
            上游模式╱            ╲二开模式
              ┌──▼──┐          ┌──▼──┐
              │加载  │          │加载  │
              │上游  │          │二开  │
              │原版  │          │版本  │
              └──┬──┘          └──┬──┘
                 │                │
                 ▼                ▼
          行为 = 上游 58ea488  行为 = 当前二开版本

       CLAWCODEX_UPSTREAM_MODE=1         普通启动
```

#### 2.15.8 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `src/features/__init__.py` | 包入口 + `is_upstream_mode()` |
| 新增 | `src/features/resolver.py` | Import hook 模块解析器 |
| 新增 | `src/features/patches/` | 提取后的二开补丁目录（可选） |
| 新增 | `scripts/backup_upstream.sh` | 补全上游快照中缺失的文件 |
| 新增 | `scripts/restore_upstream_file.py` | 还原单个文件为上游版本 + 提取 diff |
| 修改 | `src/cli.py` → 启动时调用 `init_features()` | 根据模式启用 import hook |
| 修改 | 584 个文件分批还原为上游 | 按优先级分批进行 |

#### 2.15.9 实施路线图

| Phase | 内容 | 工作量 | 说明 |
|-------|------|--------|------|
| **P1** | 基础设施：`features/__init__.py` + `resolver.py` + cli.py 初始化 | 1 天 | 最简可用的 import hook |
| **P2** | 补全上游快照：确保 `src/upstream/58ea488/` 与原版完全一致 | 1 天 | 与 git 历史对比确认 |
| **P3** | 高优先级文件提取 + 还原（~20 个核心文件） | 3 天 | repl/core.py, tui/app.py, cli.py 等 |
| **P4** | 中优先级文件提取 + 还原（~100 个文件） | 1 周 | 按模块分批发 |
| **P5** | 低优先级文件提取 + 还原（剩余 ~460 个文件） | 2 周 | 批量脚本处理 |
| **P6** | 完整验证 | 2 天 | 上游模式 = 原始 58ea488；二开模式 = 当前行为一致 |

#### 2.15.10 使用方式

```bash
# 默认启动（二开模式，同当前行为不变）
clawcodex

# 上游纯净模式（所有二开特性关闭）
CLAWCODEX_UPSTREAM_MODE=1 clawcodex

# 通过环境变量
CLAWCODEX_UPSTREAM_MODE=1 clawcodex
CLAWCODEX_UPSTREAM_MODE=true clawcodex-tui

# 通过配置文件
# ~/.clawcodex/settings.json → "upstream_mode": true
```

#### 2.15.11 对比：简化前后

| 维度 | 之前（30 个独立 FTR） | 现在（一个全局开关） |
|------|----------------------|---------------------|
| 代码复杂度 | 需要 `toggles.py` 注册表、30+ env var 解析、依赖校验 | 只需 `is_upstream_mode()` + import hook |
| 配置量 | 30 个 `CLAWCODEX_FTR_*` 环境变量 | 仅 1 个 `CLAWCODEX_UPSTREAM_MODE` |
| 提取难度 | 需逐段标注 diff（行级标记 FTR-ID） | 整体文件提取即可 |
| 灵活性 | 极高（每个特性可单独开关） | 低（要么全开要么全关） |
| 用户心智负担 | 高（需要知道每个 FTR 什么含义） | 极低（开关即模式切换） |

#### 2.15.12 风险与缓解

| 风险 | 缓解 |
|------|------|
| Import hook 与现有模块系统冲突 | P1 充分测试；备选方案：直接 `sys.path` 操作 |
| 584 个文件还原时间过长 | 优先级分批进行，P1-P2 即可获得核心功能 |
| 上游源码升级后 diff 过大 | 提取时保留完整文件的二开版本副本，二开模式用 diff apply 而非 import hook |
| 还原后二开模式行为偏差 | 分步还原每个文件后立即验证 |

---

*文档更新时间: 2026-06-01*

*版本 v2.0 更新：新增 F-35 二开特性可切换架构设计，Feature Toggle 系统 + 584 个内联修改文件特性提取方案。*

*版本 v2.3 更新：新增 3.1.5 Orchestrator 验证与报告闭环设计（F-38）。Sub-A 在 `HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点，git_sync 在 commit/push 前后自动跑 verification gate（默认 `pytest -x`，用户可配 `test_command`）；Sub-B 新增 `report_writer` 生成 Markdown/JSON 报告，`IssueRecord` 增 `report_path` 字段，`git_sync._build_pr_body` 改模板插值；Sub-C 抽象 `TrackerAdapter.update_pull_request`，GitCode 客户端实现 `PATCH /repos/{owner}/{repo}/pulls/{id}`，把报告回写到 PR body 并合并为单条汇总评论；Sub-D 修复 `progress_reporter` 死代码，PhaseComplete 接入 ndjson event log。*

*版本 v2.7 更新：F-39 Orchestrator Issue 重跑入口落地（Sub-A~F 全部 ✅）。`tracker.py` 增 `Intent` str-Enum + `Command` enum + `CommandIntent` 数据类 + 默认 `fetch_issue_command_intent`；`issue_registry.py:IssueRecord` 增 5 字段 + 5 方法（`mark_intent/clear_intent/reset_for_retry/increment_retry_count/unblock`）；`orchestrator.py` 在 `_poll_and_dispatch` 增加 Sub-F 角色校验（fail-closed）+ 限频（`max_retries_per_issue=3`）+ 拒绝评论与高优 audit；`cli/issue.py` 增 `retry` 子命令（`--mode {reset,followup,unblock}` + `--force` + `--max-retries` + `--operator` + `--reason`）写 `~/.clawcodex/orchestrator/audit.jsonl`。新增 153 个 F-39 专项单测，orchestrator 回归 231/231 通过。端到端 10-11 阶段（实际 GitCode/GitHub issue 联动）待真实环境验证。*

*版本 v2.6 更新：新增 §3.1.7 ProgressReporter Sink 协议重构设计（F-40）。把 `Orchestrator` 上 `ProgressReporter` 单例拆为每 session 独立的 `ProgressSink` 实例；新增 `CompositeProgressSink` 扇出支持 F-37/F-39 零侵入接入；补全 `SessionComplete` / `TurnComplete` 转发；引入 `WorkflowConfig.phases` 做真实进度计算，淘汰 `phase_count * 25` 假数据。*

*版本 v2.4 更新：新增 3.1.6 Issue 重跑入口设计（F-39）。三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发，Sub-B 重置重跑，Sub-C follow-up 叠 commit，Sub-D comment 命令解析，Sub-E CLI 兜底，Sub-F 限频+角色校验。*