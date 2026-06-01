# ClawCodex 特性规划与设计文档

> 文档路径: `docs/FEATURE_PLAN.md`
> 基于: `clawcodex-opensource-replacement-analysis-v2.md`, `clawcodex_vs_ccb_analysis-v3.md`, `INTEGRATION.md`, `TEAM_MEMBERSHIP.md`
> 版本: v1.7
> 更新日期: 2026-05-30
> 上游同步: 68dc3c5 (Phase 11 bridge complete)

---

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
> 以下列出的所有功能模块已在归档文档中详细记录：核心Agent系统、三层解耦架构、Provider层、工具系统、开源替代组件、后台运行与恢复同步、Bridge桥接器、Agent Loop Consolidation、Advisor Token计数、REPL/TUI增强、MCP协议扩展、Orchestrator自主模式等。

---

## 三、规划功能模块

### 3.1 Orchestrator 自主模式（Symphony 集成）

**状态**: ✅ 完成（Symphony 集成）
**目标**: 支持 `clawcodex --workflow WORKFLOW.md` 自主运行模式

#### 3.1.1 核心组件

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| Orchestrator | `orchestrator/orchestrator.py` | ✅ 完成 | 轮询循环 + 任务分发 |
| WorkspaceManager | `orchestrator/workspace.py` | ✅ 完成 | 每个 Issue 的隔离工作区 |
| LinearAdapter | `orchestrator/linear/adapter.py` | ✅ 完成 | Linear GraphQL API 适配器 |
| LinearClient | `orchestrator/linear/client.py` | ✅ 完成 | HTTP + GraphQL 客户端 |
| Issue | `orchestrator/linear/issue.py` | ✅ 完成 | Issue 数据模型 |
| AgentRunner | `orchestrator/agent_runner.py` | ✅ 完成 | 连接 QueryRunner |
| PromptBuilder | `orchestrator/prompt_builder.py` | ✅ 完成 | 模板渲染 |
| WorkflowLoader | `orchestrator/workflow.py` | ✅ 完成 | WORKFLOW.md 解析 |
| ApprovalPolicy | `orchestrator/approval_policy.py` | ✅ 完成 | 工具调用审批策略 |
| StatusDashboard | `orchestrator/status_dashboard.py` | ✅ 完成 | 终端 UI 状态面板 |
| TrackerAdapter | `orchestrator/tracker.py` | ✅ 完成 | Tracker 协议抽象 |
| IssueRegistry | `orchestrator/issue_registry.py` | ✅ 完成 | 持久化 issue→commit→PR 映射 |
| ClarificationQueue | `orchestrator/clarification_queue.py` | ✅ 完成 | 操作员异步应答队列（Phase A） |
| CLI orchestrator group | `orchestrator/cli/` | ✅ 完成 | `clawcodex orchestrator` 统一入口 |

#### 3.1.2 待完成功能

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 多 Tracker 支持 | ✅ 已完成 | GitHub/Gitee/GitCode 通用 REST 适配器已实现（`repo_tracker/adapter.py` + `repo_tracker/client.py`），TrackerAdapter 协议已完整，支持 `ensure_pull_request` |
| CLI 集成 | ✅ 已完成 | `cli.py:596-666` 已实现 `--workflow`、`--dashboard`、`--port` |
| 重试队列 + 退避 | ✅ 已完成 | `orchestrator.py:205-298` 实现指数退避重试 |
| **重试上限保护** | ✅ 已完成 | `_schedule_retry` 增加最大重试次数限制，防止无限重试；超过上限后不再自动重试 |
| **Issue State 前置检查** | ✅ 已完成 | `_poll_and_dispatch` 在 launch 前查 issue 最新 state，非 active 状态直接跳过 |
| **已有 PR 跳过后续处理** | ✅ 已完成 | `_launch_issue` 前查 `find_pull_request`，已有 PR 则标记 completed 并跳过 |
| **本地 Issue 注册表** | ✅ 已完成 | 持久化 issue→commit→PR 映射到 JSON，重启后可识别已处理 issue |
| **Issue Clarification 流程** | ✅ 完成 | 三通道 ClarificationQueue + TrackerAdapter 评论接口 + CLI `clarify`（Phase A-G） |
| **Orchestrator CLI** | ✅ 完成 | `clawcodex orchestrator` 统一入口（Phase O1-O8） |
| **LocalTracker 本地 Issue 文档源** | ✅ 完成 | `tracker.kind: local` 实现 + Human Review Gate（`pending_review` 状态、review 审批/拒绝、diff 变更概览） |

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

---

### 3.2 Agent 阶段性进度汇报

**状态**: ✅ 已完成
**目标**: 在 Agent 编排中阶段性将结果汇报至任务看板，将任务看板提取为工具

#### 三组合实现方案

| 维度 | 方案 | 解决的问题 |
|------|------|-----------|
| **触发时机** | 方式一：检查点触发 | "什么时候汇报" — 在 Agent 的 phase/step 完成检查点自动触发 |
| **工具形态** | 方式二：ProgressReportTool | "用什么汇报" — 封装专门的汇报工具，而不是直接调用 TaskUpdate |
| **数据存储** | 方式三：ToolContext.tasks | "存在哪" — 通过 ToolContext.tasks 持久化 |

三个方案**互补不冲突**，组合使用：

```
Agent 执行到检查点 (方式一)
    ↓
调用 ProgressReportTool (方式二)
    ↓
数据存入 ToolContext.tasks (方式三)
```

#### 架构设计

```
src/tool_system/tools/
└── progress_report.py           # ProgressReportTool（新）

src/orchestrator/
├── agent_runner.py              # 事件流中新增 PhaseComplete 事件
└── progress_reporter.py         # 汇报逻辑处理器（新）
```

#### ProgressReportTool 设计

```python
ProgressReportTool = build_tool(
    name="ProgressReport",
    input_schema={
        "type": "object",
        "properties": {
            "taskId": {"type": "string"},      # 任务 ID
            "stage": {"type": "string"},       # 当前阶段名
            "progress": {"type": "number"},    # 0-100 进度
            "summary": {"type": "string"},    # 阶段性总结
            "nextAction": {"type": "string"}, # 下一步动作
            "metadata": {"type": "object"},   # 额外元数据
        },
        "required": ["taskId", "stage"]
    },
    call=_progress_report_call,
    description="Report 阶段性进度至任务看板"
)
```

#### 触发时机（方式一）

在 `AgentRunner` 事件流中新增 `PhaseComplete` 事件：

| 事件 | 触发位置 | 说明 |
|------|---------|------|
| `PhaseComplete` | `agent_runner.py` 的 phase 边界 | Agent 完成一个阶段（多个 turn 组成） |
| `StepComplete` | tool call 完成后 | 每个工具调用完成（可选，粒度过细） |

#### 数据持久化（方式三）

现有 `TaskUpdateTool` 已支持 `metadata` 字段，ProgressReport 通过 metadata 扩展阶段信息。

#### 与现有组件关系

| 现有组件 | 集成点 | 说明 |
|---------|--------|------|
| `tasks_v2.py` | TaskUpdate/TaskCreate | 复用现有工具，通过 metadata 扩展 |
| `StatusDashboard` | 状态展示 | 可消费汇报数据实时展示 |
| `AgentRunner` | 事件流 | PhaseComplete 事件触发汇报 |
| `ToolContext.tasks` | 存储后端 | 已有实现，无需修改 |

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

**状态**: 基础已完成，持续增强
**目标**: 完整的 MCP 协议支持

#### 3.4.1 当前支持

| 功能 | 文件 | 状态 |
|------|------|------|
| Stdio Transport | `services/mcp/` | ✅ 完成 |
| HTTP/SSE Transport | `services/mcp/` | ✅ 完成 |
| WebSocket Transport | `services/mcp/` | ✅ 完成 |
| OAuth 支持 | `services/mcp/` | ✅ 完成 |
| HTTPS/XSS 硬化 | `services/mcp/` | ✅ 完成 |

#### 3.4.2 待增强

| 功能 | 优先级 | 说明 |
|------|--------|------|
| MCP 资源缓存 | P2 | 减少重复获取 |
| MCP Batch 工具调用 | P2 | 批量工具执行 |
| MCP Progress 通知 | P3 | 长任务进度报告 |

---

### 3.6 Agent 记忆作用域隔离

**状态**: 🔄 规划中 (代码待实现)
**目标**: 支持 Agent 按需加载不同作用域的记忆内容

#### 3.6.1 设计背景

传统的记忆系统是单例模式，所有 Agent 共享相同的记忆目录。在多 Agent 协作场景下，不同 Agent 可能需要访问不同范围的信息：
- 用户/私有记忆：仅当前用户可见
- 项目记忆：项目团队共享
- 团队记忆：跨项目团队共享
- 本地记忆：会话级临时信息

#### 3.6.2 实现方案

```
memory/
├── user/           # user 类型记忆
├── project/        # project 类型记忆
├── reference/      # reference 类型记忆
└── team/           # team 共享记忆
```

| 作用域 | 说明 |
|--------|------|
| `user` | 用户/私有记忆 |
| `project` | 项目上下文记忆 |
| `reference` | 外部系统指针 |
| `team` | 团队共享记忆 |
| `local` | 会话级本地记忆 |

#### 3.6.3 核心 API

```python
# 按需加载特定作用域的记忆
memory_prompts = load_memory_prompts(['user', 'team'])

# Agent 定义时可以指定记忆作用域
agent = AgentDefinition(
    agent_type="research-agent",
    memory="user",  # 只读取用户记忆
    ...
)
```

#### 3.6.4 实现文件（待实现）

| 文件 | 功能 | 状态 |
|------|------|------|
| `memdir/memdir.py` | `load_memory_prompts()` 按作用域加载 | 🔄 待实现 |
| `memdir/memory_types.py` | 四种记忆类型定义 | 🔄 待实现 |
| `memdir/paths.py` | 记忆目录路径解析 | 🔄 待实现 |
| `memdir/team_mem_paths.py` | 团队记忆路径解析 | 🔄 待实现 |
| `memdir/team_mem_prompts.py` | 团队记忆 prompt 构建 | 🔄 待实现 |
| `context_system/prompt_assembly.py` | 支持 `memory_scopes` 参数 | 🔄 待实现 |
| `agent/agent_definitions.py` | `memory` 字段定义 | 🔄 待实现 |

#### 3.6.5 使用示例

```python
# 在 build_full_system_prompt 中使用
prompt = build_full_system_prompt(
    memory_scopes=['user', 'project'],  # Agent 按需指定
    ...
)

# 自定义 Agent 只读取用户记忆
---
name: research-agent
description: Research agent for exploring codebases
memory: user
---

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

**状态**: 规划中
**优先级**: P1
**目标**: 当 Issue 语义模糊时，通过**三通道优先机制**获取澄清——本地操作员（Dashboard/ClarificationQueue）优先，作者 @mention 兜底

#### 3.11.1 方案概述

采用**三通道优先机制**，确保语义模糊的 Issue 始终能被处理：

| 通道 | 方式 | 响应速度 | 操作员必须在线 | Headless 支持 |
|------|------|---------|--------------|--------------|
| **通道一：Dashboard 交互** | StatusDashboard 弹窗交互输入 | 最快（即时） | ✅ | ❌ |
| **通道二：ClarificationQueue** | 文件队列 + CLI 命令应答 | 中（轮询） | ❌（异步） | ✅ |
| **通道三：@mention 评论** | Issue 评论 @mention 作者 | 慢（数小时） | ❌ | ✅（完全异步） |

**优先级**：通道一 > 通道二 > 通道三，操作员在线时响应最快；操作员无响应时降级到 @mention 等待作者回复。

#### 3.11.2 平台能力对比

| 平台 | Direct Message API | Issue 评论 | @mention |
|------|-------------------|-----------|----------|
| GitHub | ✅ 有（关联账号） | ✅ 支持 | ✅ 支持 |
| Gitee | ❌ 无 | ✅ 支持 | ✅ 支持 |
| GitCode | ❌ 无 | ✅ 支持 | ✅ 支持 |

**关键约束**: 三个平台均无直接 DM/私信 API，唯一外部通知通道是 Issue 评论 + @mention。

#### 3.11.3 整体流程（双通道降级）

```
Agent 检测到 Issue 语义模糊
        ↓
ClarificationResolver 收到澄清请求
        ↓
通道一: StatusDashboard 交互提示（若操作员在线且非 headless）
        ├─ 操作员在 timeout 内应答 → 使用操作员答案 → RESOLVED
        └─ timeout 或 headless 模式 → 降级通道二
        ↓
通道二: ClarificationQueue 文件写入（~/.clawcodex/clarification_queue.json）
        ├─ 操作员通过 CLI 应答（clawcodex clarify --issue N --answer "..."）
        │    → 轮询检测到应答 → 使用答案 → RESOLVED
        └─ timeout（默认 30 分钟） → 降级通道三
        ↓
通道三: @mention Issue 作者
        ├─ 作者在 timeout 内（默认 72h）回复
        │    → 轮询检测到回复 → 解析回复 → RESOLVED
        └─ timeout → escalation 策略（skip / mark_failed / notify）
```

#### 3.11.4 通道一：StatusDashboard 交互提示

**文件**: `orchestrator/status_dashboard.py`

检测到语义模糊时，在终端面板中显示交互提示：

```
┌─ 🔵 运行中 ──────────────────────────────┐
│  Issue #42  ( chadwweng/AgentLearning )  │
│  ⚠️  语义模糊，等待本地确认              │
│                                           │
│  Agent 判断：「这个 Issue 想要 A 还是 B？」 │
│                                           │
│  输入选项:                                 │
│    [1] 选 A                                │
│    [2] 选 B                                │
│    [3] 跳过此 Issue（降级通道三）          │
│    [4] 转发给作者（@mention）             │
└───────────────────────────────────────────┘
```

- **优点**：响应最快，操作员可结合代码上下文判断
- **缺点**：需要终端支持交互输入，headless 模式下不可用
- **降级**：若 `dashboard.interactive_clarification=false` 或 headless 模式，自动跳过通道一

#### 3.11.5 通道二：ClarificationQueue 异步队列

**文件**: `orchestrator/clarification_queue.py`

将澄清问题写入队列文件，操作员可在任何终端通过 CLI 命令回复：

```bash
# 检测到模糊问题后，队列文件内容：
$ cat ~/.clawcodex/clarification_queue.json
[
  {
    "issue_id": "42",
    "issue_identifier": "chadwweng/AgentLearning#42",
    "question": "这个 Issue 优先级是 P0 还是 P1？",
    "options": ["P0", "P1"],
    "context_summary": "Issue 提到 '尽快处理' 但未指定严重程度...",
    "created_at": "2026-05-19T10:30:00Z",
    "expires_at": "2026-05-19T11:00:00Z",  # 30min local timeout
    "status": "pending",
    "source": "local"
  }
]

# 操作员通过 CLI 回复（异步，不阻塞 orchestrator）
$ clawcodex clarify --issue 42 --answer "P0"

# 或选择转发给作者（跳过通道二，直接通道三）
$ clawcodex clarify --issue 42 --forward-to-author

# Orchestrator 轮询队列，收到回复后恢复 Agent 处理
```

**核心模块**: `orchestrator/clarification_queue.py`

```python
class ClarificationQueue:
    """异步澄清队列，~/.clawcodex/clarification_queue.json"""

    def __init__(self, queue_path: Path):
        self._path = queue_path
        self._load()

    def enqueue(self, item: ClarificationItem) -> None:
        """写入待澄清项"""
        ...

    def poll_pending(self) -> list[ClarificationItem]:
        """返回所有 pending 且未过期的项"""
        ...

    def resolve(self, issue_id: str, answer: str, source: str) -> None:
        """操作员或作者回复后标记为 resolved"""
        ...

    def mark_expired(self, issue_id: str) -> None:
        """超时后标记为 expired，触发降级通道三"""
        ...
```

- **优点**：完全异步，操作员无需盯屏，不影响 orchestrator 持续运行
- **CLI 命令**：`clawcodex clarify --issue <id> --answer <text>`
- **超时检测**：Orchestrator 每轮 poll 检查 `expires_at`，过期后降级通道三

#### 3.11.6 通道三：@mention 评论（最终降级）

当通道一、二均无响应时，通过 Issue 评论 @mention 作者：

```
@chadwweng 你好！关于 Issue #42，我需要澄清一点：
这个函数是应该同步还是异步执行？选项：
1. 同步（当前实现）
2. 异步（推荐，更好的性能）

请回复对应的选项编号。谢谢！
```

- **优点**：完全异步，无需操作员在线
- **缺点**：响应最慢（可能数小时甚至不回复）
- **降级策略**：`escalation: skip | mark_failed | notify`

#### 3.11.7 ClarificationStatus 枚举（扩展支持多通道）

```python
class ClarificationStatus(str, Enum):
    NONE = "none"                          # 不需要澄清
    AWAITING_LOCAL = "awaiting_local"       # 等待本地操作员应答（Dashboard / ClarificationQueue）
    AWAITING_AUTHOR = "awaiting_author"     # 已发 @mention，等待作者回复
    RECEIVED = "received"                   # 收到回复，待解析
    RESOLVED_LOCAL = "resolved_local"       # 澄清完成（来自本地操作员）
    RESOLVED_AUTHOR = "resolved_author"     # 澄清完成（来自 @mention 作者）
    TIMED_OUT_LOCAL = "timed_out_local"    # 本地超时，降级通道三
    TIMED_OUT_AUTHOR = "timed_out_author"  # 作者超时，escalation 触发
    EXHAUSTED = "exhausted"                # 超过 max_questions 强制终止
```

#### 3.11.8 关键约束 & 风险

| 风险 | 描述 | 缓解方案 |
|------|------|----------|
| **操作员不在线 + 作者不回复** | 两层降级后均无人应答 | `escalation: skip` 直接跳过 Issue；`notify` 发送告警 |
| **Agent 反复提问** | Agent 不停提问，骚扰操作员/作者 | `max_questions_per_issue`（默认 3 次）上限 |
| **@mention 噪音** | 每个模糊 Issue 都 @mention，产生大量通知 | 仅在置信度 > threshold（默认 0.7）时触发；通道一二优先消耗模糊 Issue |
| **作者回复是另一个问题** | 作者误解或反问，无法解析 | LLM 重新判定回复是否有效；无效则计入重试次数 |
| **跨平台用户身份** | GitHub/Gitee/GitCode 用户身份体系独立 | `Issue.author_login` 统一字段，TrackerAdapter 负责映射 |
| **评论顺序问题** | 多轮评论中顺序错乱 | 每条评论携带 `in_reply_to_comment_id`，按时间戳 + 父子关系重建对话树 |
| **Agent 放弃后重启** | 重启后丢失澄清上下文 | ClarificationQueue 文件 + IssueRegistry 保存澄清状态，重启后可恢复 |
| **平台 API 限制** | 评论 API 限流或不可用 | 降级：超时后跳过，保留 `AWAITING` 状态到队列和注册表 |
| **多操作员同时应答** | 多人同时操作同一 Issue | ClarificationQueue 加锁；`status: resolved` 后其他应答者收到提示 |
| **Headless 模式无 Dashboard** | 无法弹出交互提示 | headless 模式下自动跳过通道一，直达 ClarificationQueue |

#### 3.11.9 核心模块变更

| 模块 | 文件 | 变更内容 |
|------|------|---------|
| **AskIssueAuthor 工具** | `tool_system/tools/ask_issue_author.py` | 新增工具，接收 `question` 和 `context`，触发三通道澄清流程 |
| **ClarificationResolver** | `orchestrator/clarification.py` | 新增状态机，管理双通道降级流程（LOCAL → AUTHOR → escalation） |
| **ClarificationQueue** | `orchestrator/clarification_queue.py` | 新增文件队列，管理本地异步应答（~/.clawcodex/clarification_queue.json） |
| **StatusDashboard 扩展** | `orchestrator/status_dashboard.py` | 新增交互提示组件，支持语义模糊的即时确认输入 |
| **TrackerAdapter 扩展** | `orchestrator/tracker.py` | 新增 `fetch_issue_comments(issue_id)` / `create_clarification_comment(issue_id, body, mentions)` |
| **IssueRegistry 扩展** | `orchestrator/issue_registry.py` | 新增 `clarification_status`（支持 AWAITING_LOCAL / AWAITING_AUTHOR）、`question_history`、`author_login`、`local_answer_source` |
| **Orchestrator 变更** | `orchestrator/orchestrator.py` | `_poll_and_dispatch` 中对 AWAITING 状态 Issue 单独处理；同时轮询 ClarificationQueue 和 Issue 评论 |
| **CLI 扩展** | `cli.py` | 新增 `clarify` 子命令：`clawcodex clarify --issue <id> --answer <text>` |
| **PromptBuilder 扩展** | `orchestrator/prompt_builder.py` | 将澄清内容注入 system prompt，引导 Agent 正确使用 AskIssueAuthor |

#### 3.11.10 配置 Schema（扩展支持三通道）

```yaml
agent:
  clarification:
    enabled: true                    # 是否启用澄清流程
    timeout_local_minutes: 30       # 本地操作员应答超时（通道一二合计）
    timeout_author_hours: 72        # 等待作者回复的超时时间（通道三）
    max_questions_per_issue: 3      # 每个 Issue 最多提问次数
    confidence_threshold: 0.7       # 触发澄清的语义模糊置信度阈值（0.0-1.0）
    escalation: "skip" | "mark_failed" | "notify"  # 超时后处理策略

dashboard:
  interactive_clarification: true  # 是否启用 Dashboard 交互提示（headless 时自动关闭）
```

#### 3.11.11 IssueRegistry 扩展字段

```python
@dataclass
class IssueRecord:
    # ... 现有字段 ...
    clarification_status: ClarificationStatus = ClarificationStatus.NONE
    question_history: list[str] = field(default_factory=list)
    author_login: str | None = None
    awaiting_since: float | None = None
    last_checked_comment_id: str | None = None
    local_answer: str | None = None          # 本地操作员的回答
    local_answer_source: str | None = None    # "dashboard" | "clarification_queue"
    first_response_source: str | None = None   # "local" | "author" — 第一个被采纳的答案来源
    stale_answers: list[str] = field(default_factory=list)  # 被拒绝的过时答案（记录用于通知）
```

#### 3.11.12 多渠道冲突处理方案

##### 问题场景

三通道机制引入了多个边缘冲突场景：

| 场景 | 描述 |
|------|------|
| **同时多渠道应答** | 操作员和作者在同一时间窗口内同时回答 |
| **超时后迟到** | 通道二超时升级通道三后，操作员的本地回答才到达 |
| **重复提交** | 同一渠道内同一答案被多次提交 |
| **升级通知丢失** | 操作员在不知情的情况下回答了已升级的 Issue |

##### 核心原则

| 原则 | 说明 |
|------|------|
| **第一响应者优先** | 第一个被 Orchestrator 检测到的有效答案被采纳 |
| **操作员优先级** | 操作员答案始终比作者更可信（`operator_priority: true`） |
| **单向升级不可逆** | 通道二超时 → 通道三后，原通道的迟来答案标记为 STALE_REJECTED |
| **过期主动通知** | 所有被拒绝的答案都要通知对应应答者，避免无谓等待 |
| **去重幂等** | 同一答案的重复提交第二次标记为 DUPLICATE_REJECTED，无特殊通知 |

##### ClarificationStatus 扩展（支持冲突处理）

```python
class ClarificationStatus(str, Enum):
    NONE = "none"
    AWAITING_LOCAL = "awaiting_local"
    AWAITING_AUTHOR = "awaiting_author"
    RECEIVED = "received"                    # 收到回复，待判定
    RESOLVED_LOCAL = "resolved_local"        # 澄清完成（来自本地操作员）
    RESOLVED_AUTHOR = "resolved_author"     # 澄清完成（来自 @mention 作者）
    TIMED_OUT_LOCAL = "timed_out_local"    # 本地超时，降级通道三
    TIMED_OUT_AUTHOR = "timed_out_author"  # 作者超时，escalation 触发
    EXHAUSTED = "exhausted"                # 超过 max_questions 强制终止
    # --- 新增：冲突处理状态 ---
    DUPLICATE_REJECTED = "duplicate_rejected"   # 重复提交，被去重丢弃
    STALE_REJECTED = "stale_rejected"         # 超时升级后收到的过时答案
    CONFLICT_RESOLVED = "conflict_resolved"    # 多渠道冲突已裁决
```

##### 冲突处理状态机

```
ClarificationResolver 收到任意渠道的回答
        │
        ▼
┌─ 是本通道的第一响应？ ────────────────────┐
│  否 → 标记为 DUPLICATE_REJECTED，丢弃     │
│  是 → 继续                                │
└──────────────────────────────────────────┘
        │
        ▼
┌─ 当前 clarification_status 是？ ───────────┐
│                                           │
│  AWAITING_LOCAL:                          │
│    → LOCAL 答案 → RESOLVED_LOCAL          │
│    → AUTHOR 答案（在 AWAITING_LOCAL 期间） │
│      → RESOLVED_AUTHOR                     │
│      → 操作员收到："作者已先回复，         │
│        您的窗口已关闭"                     │
│                                           │
│  AWAITING_AUTHOR:                          │
│    → AUTHOR 答案 → RESOLVED_AUTHOR        │
│    → LOCAL 答案（在 AWAITING_AUTHOR 期间） │
│      → STALE_REJECTED                      │
│      → 操作员收到："通道二已超时，          │
│        @mention 已发出，您的回答已过时"    │
│                                           │
│  TIMED_OUT_LOCAL:                          │
│    → 任何答案 → STALE_REJECTED            │
│    → 通知应答者："该 Issue 已超时升级"     │
│                                           │
│  TIMED_OUT_AUTHOR / EXHAUSTED:             │
│    → 任何答案 → STALE_REJECTED            │
│    → 通知应答者："该 Issue 已结束处理"     │
└───────────────────────────────────────────┘
```

##### 同时应答检测

Orchestrator 在同一轮 poll 中同时检查 ClarificationQueue 和 Issue 新评论，以 timestamp 决胜：

```python
async def _poll_clarification_answers(self):
    # 非阻塞读取 ClarificationQueue
    local_item = self._clarification_queue.poll_pending()

    # 获取 Issue 新评论（增量）
    author_comments = await self.tracker.fetch_new_comments_since(
        issue_id,
        since=self._registry.get(issue_id).last_checked_comment_id
    )

    candidates = []
    if local_item and local_item.answer:
        candidates.append(("local", local_item.answer, local_item.answered_at))
    if author_comments:
        latest = author_comments[0]
        candidates.append(("author", latest.body, latest.created_at))

    if len(candidates) > 1:
        # 时间戳更早者胜出；5ms 内视为"同时"，操作员优先
        delta_ms = abs(candidates[0][2] - candidates[1][2]) * 1000
        if delta_ms < 5000 and self._config.operator_priority:
            winner, loser = 0, 1  # 操作员优先
        else:
            winner = min(range(len(candidates)), key=lambda i: candidates[i][2])
            loser = 1 - winner
        self._notify_rejected(candidates[loser][0], issue_id)
    elif len(candidates) == 1:
        winner = 0

    if candidates:
        self._process_answer(candidates[winner], issue_id)
```

##### 超时告知机制（防止操作员无谓等待）

在 ClarificationQueue 中每个 pending 项包含 `escalation_notified: bool`，超时升级时主动写入通知：

```python
# 通道二超时 → 升级通道三时
{
  "issue_id": "42",
  "status": "escalated_to_author",
  "escalation_at": "2026-05-19T11:00:00Z",
  "answer": null,
  "escalation_notified": true
}

# 操作员下次运行任何 clawcodex 命令时看到：
# ⚠️ Issue #42 的澄清请求已超时升级
#    您的本地回答窗口已关闭，@mention 已发给作者
#    若有紧急情况，请手动处理此 Issue
```

| 升级事件 | 通知内容 |
|---------|---------|
| 通道二超时，升级通道三 | "您的本地回答窗口已关闭，@mention 已发给作者" |
| 通道三超时，触发 escalation | "Issue #42 澄清超时，最终处理：skip/mark_failed/notify" |
| 迟到操作员答案（在通道三之后到达） | "您对 Issue #42 的回答已过时，@mention 已发出，作者回复已被采纳" |
| 迟到作者答案（在 escalation 之后到达） | 忽略，不更新任何状态（已有最终决策） |
| 多操作员同时写 ClarificationQueue | 先写入者 RESOLVED，落败者收到"已被其他操作员抢先" | ✅ |

##### 冲突场景汇总

| 场景 | 处理结果 | 是否通知 |
|------|---------|---------|
| T4a < T3（操作员先答） | RESOLVED_LOCAL，正常流程 | 无（正常完成） |
| T3 < T4a（作者先回复） | RESOLVED_AUTHOR，操作员收到超时通知 | ✅ 通道二超时通知 |
| T4a ≈ T4b（同时，< 5ms） | 操作员优先 RESOLVED_LOCAL，落败作者收到通知 | ✅ 双方均通知 |
| 通道三已升级后操作员才答 | STALE_REJECTED，操作员收到"已超时升级" | ✅ 明确告知过时 |
| 多操作员同时写 ClarificationQueue | 先写入者 RESOLVED，落败者收到"已被抢先" | ✅ 落败方通知 |
| 同一答案被重复提交 | DUPLICATE_REJECTED，第二次被丢弃 | ❌ 无需（幂等） |

##### 配置选项（冲突处理相关）

```yaml
agent:
  clarification:
    enabled: true
    timeout_local_minutes: 30
    timeout_author_hours: 72
    max_questions_per_issue: 3
    confidence_threshold: 0.7
    escalation: "skip" | "mark_failed" | "notify"
    # --- 冲突处理配置 ---
    operator_priority: true             # 操作员答案始终优先于作者（默认 true）
    stale_notification: "all"           # "all" | "operator_only" | "none"
    simultaneous_grace_ms: 5000         # 5ms 内视为"同时"，由 operator_priority 决胜
```

#### 3.11.13 实施阶段

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase A | ClarificationQueue 文件队列 + Orchestrator 轮询 | P1 | ✅ 完成 |
| Phase A | 冲突处理状态机（DUPLICATE_REJECTED / STALE_REJECTED / CONFLICT_RESOLVED） | P1 | ✅ 完成 |
| Phase A | 超时告知机制（escalation_notified + stale_notification） | P1 | ✅ 完成 |
| Phase A | 同时应答检测逻辑（simultaneous_grace_ms + operator_priority） | P1 | ✅ 完成 |
| Phase B | StatusDashboard 交互提示组件 | P1 | ✅ 完成 |
| Phase C | AskIssueAuthor 工具 + ClarificationResolver 状态机 | P1 | ✅ 完成 |
| Phase D | CLI `clarify` 子命令 + 操作员应答接口 | P1 | ✅ 完成 |
| Phase E | TrackerAdapter 评论接口（@mention 通道三） | P1 | ✅ 完成 |
| Phase F | IssueRegistry 澄清字段持久化 + PromptBuilder 澄清内容注入 | P2 | ✅ 完成 |
| Phase G | escalation 策略实现（skip / mark_failed / notify） | P2 | ✅ 完成 |

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

**状态**: 规划中
**优先级**: P1
**目标**: 实现 Manager Agent 全自动观察 Worker Agent 状态并注入指令，支持优先级队列和权限审批

#### 3.14.1 角色定义

| 角色 | 判断标准 | 说明 |
|------|---------|------|
| **Manager Agent** | 工具集中包含 `TaskInspect` + `TaskDirectives` | 通过工具组合自动识别，无需独立 Agent 类型 |
| **Worker Agent** | 不包含上述管理工具 | 普通执行单元 |

任意现有 Agent（`general-purpose`、`worker` 等）只需添加工具即可具备管理能力。

#### 3.14.2 核心工具

**工具 1：`TaskInspect`（状态查看）**

**用途**: Manager Agent 按需主动查询一个或多个 Worker 的运行时状态

**输入 schema**:
```json
{
  "type": "object",
  "properties": {
    "targets": {
      "type": "array",
      "description": "要查询的 task_id 列表；空数组表示查询所有运行中的 worker"
    },
    "fields": {
      "type": "array",
      "description": "指定要返回的字段；空/省略则返回所有字段",
      "items": {"enum": ["status", "progress", "pending_messages", "error", "result_text", "turn_count"]}
    },
    "summary_only": {
      "type": "boolean",
      "description": "true 时只返回一句话摘要，不返回 pending_messages 内容"
    }
  }
}
```

**输出**（结构化）:
```json
{
  "workers": [
    {
      "task_id": "local_agent_xxxxx",
      "status": "running",
      "progress": {"summary": "Refactoring auth module...", "tool_uses": 12},
      "pending_messages": ["Please check permission boundary conditions"],
      "error": null,
      "last_activity": "2026-05-24T10:30:00Z"
    }
  ]
}
```

**行为**:
- 查询 `runtime_tasks` registry
- `pending_messages` 字段反映有多少条待注入消息（不会被消费）
- 非 Manager Agent 调用时报 `ToolInputError: "permission denied"`

---

**工具 2：`TaskDirectives`（消息注入）**

**用途**: Manager → Worker 的指令注入，支持优先级和权限配置

**输入 schema**:
```json
{
  "type": "object",
  "properties": {
    "to": {
      "type": "array",
      "description": "目标 task_id 列表；支持 ['*'] 表示所有运行中的 worker"
    },
    "priority": {
      "type": "string",
      "enum": ["normal", "high", "critical"],
      "default": "normal"
    },
    "message": {
      "type": "string",
      "description": "指令内容，支持结构化标记如 [OBSERVE]、[INTERVENE]、[CORRECT]"
    },
    "reason": {
      "type": "string",
      "description": "可选的干预原因说明，供 worker 理解上下文"
    },
    "worker_permission_mode": {
      "type": "string",
      "enum": ["bypassPermissions", "bubble", "plan", "default"],
      "description": "Worker 使用的权限模式，默认继承 Manager 设置"
    },
    "always_allow_rules": {
      "type": "array",
      "description": "Worker 的权限白名单规则"
    }
  },
  "required": ["to", "message"]
}
```

**输出**:
```json
{
  "delivered": ["local_agent_xxxxx"],
  "queued": ["local_agent_yyyyy"],
  "failed": []
}
```

**行为**:
- 高优先级消息（`high`、`critical`）插入队列**头部**，worker 下一 turn 优先处理
- `normal` 追加到**尾部**，FIFO 顺序
- 注入消息格式：`[MANAGER] [PRIORITY] {message}`
- 权限 gate 同 `TaskInspect`

---

**工具 3：`ReportToSupervisor`（可选，Worker 自愿上报）**

```json
{
  "to": "manager_task_id",
  "status_report": "Progress: auth refactoring done, working on permission check",
  "needs_intervention": false,
  "blockers": []
}
```

**用途**: Worker 认为需要 Manager 介入时主动调用，非强制。

#### 3.14.3 优先级处理

`queue_pending_message` 新增 priority 参数：

```python
def queue_priority_message(task_id, message, priority, registry):
    def _enqueue(prev):
        if priority in ("critical", "high"):
            prefix = "[CRITICAL]" if priority == "critical" else "[HIGH]"
            return replace(prev, pending_messages=[prefix + message, *prev.pending_messages])
        # normal: append to tail
        return replace(prev, pending_messages=[*prev.pending_messages, message])
```

| 优先级 | 队列位置 | 用途 |
|--------|---------|------|
| `critical` | 队列头部，最先消费 | 紧急修正，worker 必须响应 |
| `high` | 队列头部 | 重要建议，worker 应优先处理 |
| `normal` | 队列尾部，FIFO | 普通协调信息 |

Worker 消费时通过 `drain_pending_messages` 读取消息。

#### 3.14.4 交互流程

**Manager 主循环**:

```
┌─────────────────────────────────────────────────────┐
│  Manager Agent Turn N                                │
│                                                     │
│  1. TaskInspect(targets=[], summary_only=true)     │
│     ↓ 观察所有 worker 状态摘要                        │
│                                                     │
│  2. 分析状态：                                       │
│     - 有 worker 出错？→ TaskDirectives(INTERVENE)   │
│     - 有 worker 停滞？→ TaskDirectives(OBSERVE)     │
│     - 一切正常？→ 继续工作                           │
│                                                     │
│  3. 如有注入指令 → TaskDirectives                    │
│                                                     │
│  4. 执行自身其他任务                                  │
└─────────────────────────────────────────────────────┘
```

**Worker 消费注入消息**:

```
Turn M 开始 (tool-round 边界):

  drain_pending_messages() → ["[MANAGER] [CRITICAL] Permission logic error, please re-implement"]
                                ↓
  作为 UserMessage 追加到 messages[]
                                ↓
  Worker LLM 看到新的 user 消息，理解并执行修正
```

**权限配置传递**:

Manager 可在 `TaskDirectives` 中指定 `worker_permission_mode` 和 `always_allow_rules`：

```python
# 示例：启动一个信任的 worker
TaskDirectives(
    to=["worker_abc"],
    message="Continue with deployment",
    worker_permission_mode="bypassPermissions"
)

# 示例：启动一个需要审批的 worker
TaskDirectives(
    to=["worker_xyz"],
    message="Refactor the auth module",
    worker_permission_mode="plan",
    always_allow_rules=[
        {"tool": "Read", "pattern": "*.py"},
        {"tool": "Write", "pattern": "*.py"},
        {"tool": "Bash", "pattern": "pytest.*"},
    ]
)
```

#### 3.14.5 权限方案

**权限层级**:

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `bypassPermissions` | 所有工具直接执行，不弹窗 | 测试/完全信任的 worker |
| `bubble` | 权限弹窗冒泡到 Manager 终端 | 受控环境，需人类监督高风险操作 |
| `plan` | 高风险操作需 Manager 实时审批 | 生产/高风险场景 |
| `default` | 标准运行时审批 | 普通场景 |

**always_allow_rules 格式**:

```json
[
  {"tool": "Bash", "pattern": "rm -rf /tmp/*"},
  {"tool": "Write", "pattern": "*.py"},
  {"tool": "Read", "pattern": "*"}
]
```

权限系统匹配规则时**先检查** `always_allow_rules`，匹配则直接放行。

**Plan Mode 审批流程**:

```
Worker 执行: Bash(rm -rf build/)
    ↓
权限系统卡住，发送 plan_approval_request 给 Manager
    ↓
Manager 收到通知 → 分析风险 → 决定审批或拒绝
    ↓
SendMessage(message={
  "type": "plan_approval_response",
  "approve": true,
  "request_id": "xxx",
  "feedback": "Approved - this is a clean build directory"
})
    ↓
Worker 继续执行
```

**组合推荐**:

| 场景 | Worker 模式 | Manager 职责 |
|------|-------------|-------------|
| 测试/开发 | `bypassPermissions` | 无需审批 |
| 受控环境 | `bubble` + `always_allow_rules` | 规则外的工具弹窗给人类 |
| 生产/高风险 | `plan` | Manager 实时审批关键操作 |

#### 3.14.6 错误处理

| 场景 | 处理方式 |
|------|---------|
| Worker 不存在 | `TaskDirectives` 返回 `failed: [task_id]` |
| Worker 已终止 | 自动 resume（`resume_agent_background` 机制） |
| 注入消息丢失（worker 被 kill） | Manager 收到 notification 后重新分配任务 |
| Manager 无权限 | `ToolInputError: "permission denied"` |
| Worker 拒绝执行 | Manager 收到错误报告，通过 `TaskDirectives` 重新注入修正指令 |

#### 3.14.7 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/tool_system/tools/task_inspect.py` | 新增 | 状态查看工具 |
| `src/tool_system/tools/task_directives.py` | 新增 | 消息注入工具 |
| `src/tasks/local_agent.py` | 修改 | `queue_pending_message` 支持 priority 参数 |
| `src/query/query.py` | 修改 | `drain_pending_messages` 按优先级消费 |
| `src/agent/agent_tool_utils.py` | 修改 | `resolve_agent_tools` 过滤管理工具（仅 Manager 可用） |
| `src/tool_system/tools/send_message.py` | 修改 | 复用结构化消息逻辑 |

#### 3.14.8 实施阶段

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase M1 | `TaskInspect` + `TaskDirectives` 核心工具 | P1 | ⏳ 待开始 |
| Phase M2 | `queue_pending_message` 支持 priority | P1 | ⏳ 待开始 |
| Phase M3 | `drain_pending_messages` 按优先级消费 | P1 | ⏳ 待开始 |
| Phase M4 | 工具可见性过滤（仅 Manager 可调用） | P1 | ⏳ 待开始 |
| Phase M5 | 权限规则传递（`always_allow_rules` + `worker_permission_mode`） | P1 | ⏳ 待开始 |
| Phase M6 | 测试与联调 | P2 | ⏳ 待开始 |

---

### 3.12 Orchestrator CLI 运维操作界面

**状态**: 规划中
**优先级**: P1
**目标**: 通过 `clawcodex orchestrator` 统一入口，实现运行期间的全程可视化监控与中途介入

#### 3.12.1 背景与问题

当前 orchestrator 在 agent 运行期间对操作员是完全黑盒的：

| 能力 | 现状 |
|------|------|
| 查看运行中的 issue 列表 | ✅ StatusDashboard 显示 running/completed/failed |
| 查看 issue 当前状态 | ✅ StatusDashboard 显示 issue_identifier + workspace_path |
| 查看 agent 正在做什么 | ❌ 完全黑盒 |
| 中途暂停/终止 agent | ❌ 无法做到 |
| 修改 workspace 文件 | ❌ 无法做到 |
| 向运行中的 agent 注入指令 | ❌ 无法做到 |
| 实时查看 agent 的 tool call 日志 | ❌ 无 |

操作员常见需求：
- "Issue #42 现在在干啥？"
- "它改了我不该改的文件，能撤回吗？"
- "它理解错了，能强制终止并重新开始吗？"
- "我手动改了些文件，能让它接着干吗？"
- "它卡住了，能直接塞一条 hint 进去吗？"

#### 3.12.2 CLI 命令树

```
clawcodex orchestrator                    # 统一入口
│
├── run               # 启动 orchestrator（原有 --workflow 的替代）
│   └── --workflow WORKFLOW.md
│   └── --dashboard（开启内嵌面板）
│   └── --port 8080（LiveView 端口）
│
├── status           # 全局状态
│   └── --watch     # 实时监控模式（类似 top）
│
├── issues           # issue 相关操作
│   ├── list        # 列出所有 issue 及状态
│   ├── show <id>   # 查看 issue 详情（理解上下文、token 使用量、workspace 路径）
│   └── tail <id>   # 实时 tail tool call 日志（流式）
│
├── pause <id>      # 暂停 agent（停在当前 tool call 边界）
│
├── resume <id>     # 恢复已暂停的 agent
│
├── stop <id>       # 强制终止 agent
│
├── takeover <id>   # 完全接管（终止 agent + 启动 REPL）
│
├── inject          # 向运行中的 agent 注入提示
│   ├── <id> "hint text"      # 注入文字提示
│   ├── --list <id>            # 查看已注入的提示列表
│   └── --remove <id> <hint_num># 删除某条提示
│
├── clarify         # 澄清应答（本地操作员回答）
│   └── --issue <id> --answer <text>
│
├── workspace       # workspace 文件操作
│   ├── <id> --ls              # 列出文件树
│   ├── <id> --cat <file>      # 查看文件内容
│   └── <id> --edit <file> --with <content>  # 修改文件
│
└── dashboard       # 启动独立 dashboard UI
    └── --port 8080
```

#### 3.12.3 核心模块变更

| 模块 | 文件 | 变更内容 |
|------|------|---------|
| **CLI 入口** | `cli.py` | 新增 `orchestrator` group，所有子命令挂在此下 |
| **orchestrator server** | `orchestrator/cli/server.py` | noun-verb 结构：`server start/status/stop` |
| **orchestrator issue** | `orchestrator/cli/issue.py` | noun-verb 结构：`issue list/show/tail/stop/pause/resume/takeover/clarify/inject/workspace` |
| **orchestrator dashboard** | `orchestrator/cli/dashboard.py` | 独立 LiveView UI |
| **Orchestrator 扩展** | `orchestrator/orchestrator.py` | 支持 pause / resume / stop 状态，event stream 推送 |
| **AgentRunner 扩展** | `orchestrator/agent_runner.py` | 支持 pause at tool boundary，event stream 推送 tool calls |
| **WorkspaceManager 扩展** | `orchestrator/workspace.py` | `.operator_hints.md` 注入，文件读写控制 |

#### 3.12.4 LiveView 实时窥视（Dashboard 增强）

```
┌─ 🔵 运行中 ─────────────────────────────────────────┐
│  Issue #42  (chadwweng/AgentLearning#42)             │
│  运行时长: 00:05:23                                   │
│  Agent: claude-sonnet-4-20250501                      │
│                                                        │
│  📋 当前系统 Prompt 摘要:                              │
│  ────────────────────────────────────────────────    │
│  你是一个 autonomous coding agent，目标是解决 Issue...  │
│                                                        │
│  🔧 最近工具调用:                                      │
│  ────────────────────────────────────────────────    │
│  10:32:01  Grep     "TODO.*auth"  → 3 matches       │
│  10:31:55  Read     src/auth.py   → OK              │
│  10:31:48  Bash     git status   → clean            │
│                                                        │
│  📝 最近 LLM 响应摘要:                                │
│  ────────────────────────────────────────────────    │
│  "我正在定位认证模块的问题，在搜索 TODO 标记..."       │
│                                                        │
│  ⚡ 操作:                                              │
│    [暂停]  [终止]  [注入 Hint]  [打开 Workspace]      │
└──────────────────────────────────────────────────────┘
```

**实现方式**：AgentRunner 通过 asyncio queue 实时推送 tool call 和 LLM 摘要事件，StatusDashboard 消费这些事件并渲染。

#### 3.12.5 Pause / Resume 机制

**Pause**：
- Agent 在当前 tool call 返回后停止，不执行下一个 tool call
- `AgentSession` 增加 `paused_at: float`、`pause_reason: str` 字段
- Orchestrator 将该 issue 从 running 移到 paused 集合

**Resume**：
- 操作员修改 workspace 文件或注入 hint 后调用 `orchestrator issue resume --id <id>`
- `pause_reason` 内容注入到下一个 LLM prompt 的 system context
- Agent 从断点继续

**Takeover（最强介入）**：
```bash
clawcodex orchestrator issue takeover --id 42
# 效果：
#  1. 运行中的 agent 被立即终止
#  2. clawcodex REPL 启动，加载 Issue #42 的完整 workspace
#  3. 操作员在 REPL 中手动处理
#  4. 完成后 /done，commit + push
#  5. Issue 标记为 COMPLETED（operator 模式）
```

#### 3.12.6 Operator Hint 注入机制

操作员通过 `inject` 命令向运行中的 agent 注入提示：

```bash
# 注入文字提示（agent 下次 tool call 会自动读到）
clawcodex orchestrator issue inject --id 42 "别动 auth.py，已经有人在改了"

# 查看已注入的提示列表
clawcodex orchestrator issue inject --id 42 --list

# 删除某条提示
clawcodex orchestrator issue inject --id 42 --remove 1
```

**注入时机**：WorkspaceManager 在每个 tool call 执行前，检查 `.operator_hints.md` 并将内容以特殊格式追加到 tool context 中：

```
--- Operator Hint (注入于 2026-05-19 10:35:00) ---
别动 auth.py，已经有人在改了
-----------------------------------
```

#### 3.12.7 不兼容变更记录

> **不兼容变更**：扁平子命令（`run`、`status`、`issues`、`pause`、`resume`、`stop`、`inject`、`clarify`、`workspace`、`takeover`）已移除，统一使用 noun-verb 结构：`server <verb>` / `issue <verb> --id <id>`。

#### 3.12.8 实施阶段

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase O1 | CLI `orchestrator` 子命令框架搭建（run / status / issues list） | P1 | ✅ 完成 |
| Phase O2 | `stop` / `pause` / `resume` agent 生命周期控制 | P1 | ✅ 完成 |
| Phase O3 | `issues tail` 实时 tool call 日志流 | P1 | ✅ 完成 |
| Phase O4 | `inject` 操作员 Hint 注入 | P1 | ✅ 完成 |
| Phase O5 | `workspace` 文件查看 / 修改 | P1 | ✅ 完成 |
| Phase O6 | `takeover` 会话接管 | P2 | ✅ 完成 |
| Phase O7 | `clarify` 澄清应答（与 Phase D/C 合并） | P1 | ✅ 完成 |
| Phase O8 | Dashboard LiveView 增强（event stream） | P2 | ✅ 完成 |

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
| 状态查看 | 提供 `/autonomy status`、`/autonomy runs`、`/autonomy status --deep` 或 ClawCodex 等价命令；用户可区分“cron job 定义”和“scheduled-task run 生命周期”，并能查看 trigger detail 中的 `last_run`、`next_run`、`created_at` 与手动 fire 返回的 run id |
| 运行去重 | 同一 cron task 的上一轮 run 仍处于 `queued`/`running` 时，不重复创建新的 active run，避免每分钟任务堆积 |
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

`claude-code-best` 的结果查看链路不是在 Cron task 表里保存完整回答，而是将每次 scheduled fire 转换成 autonomy queued prompt，并在 `.claude/autonomy/runs.json` 中维护运行账本；`/schedule get` 的 detail 视图展示 `next_run`、`last_run`、`created_at`、prompt 和启用状态，`/schedule run` 手动触发后直接显示 run id。ClawCodex 迁移时应复刻这条语义链，或提供字段和命令等价的下游实现。

当前 ClawCodex 已有 `clawcodex_ext/cron_system/runs.py` 与 `status.py` 的基础文本输出，但它们仍停留在较窄 schema 与汇总表格层面：run 记录只包含 `id`、`task_id`、`prompt`、`status`、时间戳和 `error`，缺少 `claude-code-best` 用于 operator 追溯的 `source_id`、`source_label`、`prompt_preview`、`root_dir`、`current_dir`、ownership/session 元数据；状态输出也没有 trigger detail / manual-fire outcome 等价视图。因此 Phase D 的目标不是“新建空模块”，而是把现有 runs/status 基础扩展到参考实现的可查询深度。

完整链路：

```text
CronTask due
  → create scheduled-task queued prompt
  → create run record(status=queued, source_id=cron task id)
  → enqueue prompt into REPL/TUI/headless queue
  → queue consumer claims run: queued → running
  → normal query pipeline executes prompt
  → finalize run: completed / failed / cancelled
  → /autonomy status|runs|status --deep or equivalent command reads run store
```

| 文件 | 改动 |
|------|------|
| `clawcodex_ext/cron_system/runtime.py` | 把 `outbox` 升级为 typed dispatch bridge，负责把 task 转成 frontend 可执行命令 |
| `clawcodex_ext/cron_system/runs.py` | 扩展现有 scheduled run 记录到 autonomy-compatible schema，覆盖 queued/running/completed/failed/cancelled，并持久化到 `.claude/autonomy/runs.json` 或 ClawCodex 等价路径 |
| `clawcodex_ext/cron_system/status.py` | 在现有文本表格基础上补齐 run status、recent runs、deep status 的 cron section；对齐 `autonomyRuns.ts` 与 `autonomyStatus.ts` 的用户可见输出语义 |
| REPL/TUI downstream adapter | scheduled fire 时入队 prompt，渲染 scheduled-task 系统消息，避免同 sourceId 重复 active run；消费前原子 claim queued run 为 running |
| headless downstream adapter | mirror `claude-code-best` print mode，把 due task 交给 headless runner；无法路由 teammate 时标记 failed |
| command/skill adapter | 提供 `/autonomy status`、`/autonomy runs [limit]`、`/autonomy status --deep` 或明确命名的 ClawCodex 等价命令 |
| trigger detail / manual-fire adapter | 提供等价于 `/schedule get <id>` 与 `/schedule run <id>` 的用户路径：detail 展示 status、schedule、agent、next run、last run、created、prompt；manual fire 创建 queued run 并回显 run id |

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

如果 ClawCodex 已有 orchestrator/task run 存储，优先复用；否则扩展 `clawcodex_ext/cron_system/runs.py` 中的最小 run store，并在后续 autonomy 系统成熟后迁移。扩展时需保留对现有 `.claude/scheduled_task_runs.json` 的读取兼容，避免丢失早期运行记录。

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

---

## 十、Skills System Extension（技能系统扩展层）

**状态**: 🆕 新规划
**优先级**: P1
**目标**: 仿照 `tool_system_ext` 的模式，构建独立的技能系统扩展层，降低上游更新时的侵入式修改

### 10.1 背景

当前 `src/skills/loader.py` 存在以下问题：

1. **耦合 clawcodex 特定逻辑**: 硬编码 `~/.clawcodex/skills`、`CLAWCODEX_SKILLS_DIR` 等路径
2. **职责过于集中**: `get_all_skills()` 混合了上游加载 + clawcodex 扩展 + 条件激活 + MCP 构建
3. **难以独立更新上游**: 每当上游 skills 系统更新，需要仔细比对 diff，区分哪些是上游变更、哪些是 clawcodex 扩展

### 10.2 设计模式对比

| 组件 | Tool System | Skills System |
|------|-------------|---------------|
| 上游核心 | `src/tool_system/registry.py` (ToolRegistry) | `src/skills/loader.py` (get_all_skills) |
| 扩展目录 | `src/tool_system_ext/` | `src/skills_ext/` (新) |
| 扩展包装类 | `ToolRegistryExt` | `SkillRegistryExt` (新) |
| Bundle机制 | `TOOL_BUNDLES` (bundles.py) | `SKILL_BUNDLES` (新) |
| Agent配置 | `AgentToolConfig` | `AgentSkillConfig` (新) |
| 额外路径 | 无 | `~/.clawcodex/skills` 等 |
| 注册回调 | `on_tool_registered` | `on_skill_registered` (新) |

### 10.3 架构设计

```
src/
├── skills/                      # Layer 1: 上游原始代码（只读）
│   ├── loader.py                #   核心加载逻辑（上游）
│   ├── model.py                 #   Skill 数据模型（上游）
│   ├── bundled_skills.py        #   内置 skill 注册（上游）
│   ├── bundled/                 #   内置 skill 实现（上游）
│   └── ...
│
├── skills_ext/                  # Layer 2: clawcodex 扩展层（新增）
│   ├── __init__.py
│   ├── registry_ext.py          #   SkillRegistryExt 包装类
│   ├── bundles.py               #   Skill Bundle 定义
│   ├── agent_config.py          #   Agent Skill 配置
│   ├── paths.py                 #   clawcodex 特定路径解析
│   ├── hooks.py                 #   Skill 生命周期钩子
│   └── cache.py                 #   扩展层缓存管理
```

### 10.4 核心组件设计

#### 10.4.1 SkillRegistryExt

```python
# src/skills_ext/registry_ext.py

class SkillRegistryExt:
    """包装上游 loader，添加 clawcodex 特定功能"""
    
    def __init__(self, loader_module=None) -> None:
        self._loader = loader_module or import.import_module('src.skills.loader')
    
    def get_all_skills(self, **kwargs) -> list[Skill]:
        """获取所有 skills（上游 + 扩展）"""
        # 1. 调用上游获取基础 skills
        base_skills = self._loader.get_all_skills(**kwargs)
        
        # 2. 追加 clawcodex 特定 skills
        clawcodex_skills = self._load_clawcodex_paths()
        
        # 3. 合并（去重）
        return self._merge_skills(base_skills, clawcodex_skills)
    
    def _load_clawcodex_paths(self) -> list[Skill]:
        """加载 clawcodex 特定路径的 skills"""
        # 加载 ~/.clawcodex/skills
        # 加载 CLAWCODEX_SKILLS_DIR
        # 加载 CLAWCODEX_MANAGED_SKILLS_DIR
        ...
    
    def on_skill_registered(self, callback: Callable[[Skill], None]) -> None:
        """注册 Skill 生命周期回调"""
        ...
```

#### 10.4.2 SKILL_BUNDLES

```python
# src/skills_ext/bundles.py

# Skill Bundle 定义
SKILL_BUNDLES: dict[str, list[str]] = {
    "default": ["git:commit", "git:push", "review-pr", ...],
    "clawcodex": ["simplify", "debug", "loop", "verify-content", ...],
    "all": list(TOOL_BUNDLES.keys()),
}

MODE_BUNDLES: dict[str, list[str]] = {
    "bare": [],
    "default": ["default"],
    "clawcodex": ["clawcodex"],
    "all": list(SKILL_BUNDLES.keys()),
}
```

#### 10.4.3 AgentSkillConfig

```python
# src/skills_ext/agent_config.py

@dataclass
class AgentSkillConfig:
    """配置 Agent 可访问的 skills"""
    mode: Literal["bare", "default", "all"] = "default"
    bundles: list[str] | None = None
    exclude: list[str] = field(default_factory=list)
```

### 10.5 迁移策略

1. **Phase 1**: 创建 `src/skills_ext/` 目录和基础结构
2. **Phase 2**: 将 clawcodex 特定路径逻辑从 `loader.py` 迁移到 `skills_ext/paths.py`
3. **Phase 3**: 添加 Bundle 机制和 AgentSkillConfig
4. **Phase 4**: 添加 Hook 机制和回调系统
5. **Phase 5**: 更新 `get_all_skills()` 调用点使用 `SkillRegistryExt`

### 10.6 优势

| 优势 | 说明 |
|------|------|
| **非侵入式** | 上游代码保持原样，只需在 `skills_ext/` 中包装 |
| **易于更新** | 上游更新时，只需同步 `loader.py`，扩展层改动很少 |
| **清晰边界** | clawcodex 特定逻辑与上游逻辑分离 |
| **可测试** | 扩展层可以独立测试 |
| **可替换** | 可通过环境变量切换使用上游原始 loader |

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

*文档更新时间: 2026-05-25*

*版本 v2.0 更新：新增 F-35 二开特性可切换架构设计，Feature Toggle 系统 + 584 个内联修改文件特性提取方案。*