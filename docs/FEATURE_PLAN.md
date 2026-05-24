# ClawCodex 特性规划与设计文档

> 文档路径: `docs/FEATURE_PLAN.md`
> 基于: `clawcodex-opensource-replacement-analysis-v2.md`, `clawcodex_vs_ccb_analysis-v3.md`, `INTEGRATION.md`, `TEAM_MEMBERSHIP.md`
> 版本: v1.4
> 更新日期: 2026-05-23

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

## 二、已实现功能模块

### 2.1 核心 Agent 系统

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| Agent 执行循环 | `agent/run_agent.py` | 四级权限模型、Subagent 隔离、消息完整性 | ✅ 完成 |
| Fork Subagent | `agent/fork_subagent.py` | 创建独立会话的 sub-agent | ✅ 完成 |
| Resume Agent | `agent/resume_agent.py` | 从断点恢复 sub-agent | ✅ 完成 |
| Foreground Promotion | `agent/foreground_promotion.py` | 后台 agent 提升到前台 | ✅ 完成 |
| Session 管理 | `agent/session.py` | 会话状态管理 | ✅ 完成 |
| Transcript | `agent/transcript.py` | 对话转录本管理 | ✅ 完成 |
| Prompt 构建 | `agent/prompt.py` | 系统 Prompt 组装 | ✅ 完成 |
| Agent 定义系统 | `agent/agent_definitions.py` | Agent 类型、工具、配置定义 | ✅ 完成 |
| Agent 记忆作用域 | `memdir/memdir.py` | 按需加载不同作用域的记忆 | ✅ 完成 |

### 2.0 三层解耦架构（Layer Isolation）

| Layer | 路径 | 说明 | upstream-sync 层 |
|-------|------|------|-----------------|
| Layer 1 | `src/upstream/` / `src/upstream/v2025_04/` | 上游代码镜像（只读） | `upstream` |
| Layer 2 | `src/capabilities/` | Protocol 接口定义，无运行时上游依赖 | `capabilities` |
| Layer 3 | `src/orchestrator/` / `src/api/` | ClawCodex 新增组件，完全解耦 | `features` |

**解耦实现：**
- `src/api/query.py` 通过 `capabilities/headless_runner.py` 间接调用 `entrypoints/headless.run_headless`，运行时无直接上游引用
- `src/api/query.py` 使用 `ToolEventProtocol` / `HeadlessOptionsProtocol` 做类型标注，与上游具体实现解耦
- 所有 Protocol 使用 `typing.Protocol` 结构子类型（无 ABC 继承）
- 适配器文件（`_gitpython_adapter.py` 等）在 `src/` 内，随上游代码一同在补丁范围内，不形成独立依赖

**upstream-sync audit**：零层违规（`upstream-sync audit` 验证通过）

### 2.1 核心 Agent 系统

| Provider | 文件 | 状态 | 备注 |
|----------|------|------|------|
| Anthropic | `providers/anthropic_provider.py` | ✅ 完成 | 官方 API |
| OpenAI | `providers/openai_provider.py` | ✅ 完成 | |
| OpenAI Compatible | `providers/openai_compatible.py` | ✅ 完成 | 通用 OpenAI 兼容端点 |
| GLM | `providers/glm_provider.py` | ✅ 完成 | 智谱 GLM |
| MiniMax | `providers/minimax_provider.py` | ✅ 完成 | |
| DeepSeek | `providers/deepseek_provider.py` | ✅ 完成 | |
| OpenRouter | `providers/openrouter_provider.py` | ✅ 完成 | |
| **LiteLLM 适配器** | `providers/_litellm_adapter.py` | ✅ 完成 | P0，统一 100+ 模型 |

### 2.3 工具系统

| 工具 | 文件 | 状态 |
|------|------|------|
| FileRead | `tool_system/tools/read.py` | ✅ 完成 |
| FileWrite | `tool_system/tools/write.py` | ✅ 完成 |
| FileEdit | `tool_system/tools/edit.py` | ✅ 完成 |
| Glob | `tool_system/tools/glob.py` | ✅ 完成 |
| Grep | `tool_system/tools/grep.py` | ✅ 完成 |
| Bash | `tool_system/tools/bash/` | ✅ 完成 |
| WebFetch | `tool_system/tools/web_fetch.py` | ✅ 完成 |
| WebSearch | `tool_system/tools/web_search.py` | ✅ 完成 |
| AskUserQuestion | `tool_system/tools/ask_user_question.py` | ✅ 完成 |
| SendMessage | `tool_system/tools/send_message.py` | ✅ 完成 |
| TodoWrite | `tool_system/tools/todo_write.py` | ✅ 完成 |
| TaskStop | `tool_system/tools/task_stop.py` | ✅ 完成 |
| TasksV2 | `tool_system/tools/tasks_v2.py` | ✅ 完成 |
| Agent | `tool_system/tools/agent.py` | ✅ 完成 |
| Team | `tool_system/tools/team.py` | ✅ 完成 |
| Config | `tool_system/tools/config.py` | ✅ 完成 |
| PlanMode | `tool_system/tools/plan_mode.py` | ✅ 完成 |
| Cron | `tool_system/tools/cron.py` | ✅ 完成 |
| MCPTool | `tool_system/tools/mcp.py` | ✅ 完成 |
| MCPResources | `tool_system/tools/mcp_resources.py` | ✅ 完成 |
| Skill | `tool_system/tools/skill.py` | ✅ 完成 |
| ToolSearch | `tool_system/tools/tool_search.py` | ✅ 完成 |
| LSP | `tool_system/tools/lsp.py` | ✅ 完成 |
| Worktree | `tool_system/tools/worktree.py` | ✅ 完成 |

### 2.4 开源替代组件（已完成）

| 组件 | 原始实现 | 替代方案 | 适配器文件 | 状态 |
|------|---------|---------|-----------|------|
| 配置系统 | 手动 JSON 管理 | Pydantic-settings | `settings/pydantic_adapter.py` | ✅ 完成 |
| Frontmatter 解析 | 手动 yaml.safe_load | python-frontmatter | `skills/_frontmatter_adapter.py` | ✅ 完成 |
| Bash AST 解析器 | ~1,500 行自建 | tree-sitter-bash | `permissions/_treesitter_adapter.py` | ✅ 完成 |
| Git 操作 | 6 个 subprocess.run() | GitPython | `context_system/_gitpython_adapter.py` | ✅ 完成 |
| Hook 系统 | ~1,200 行自建 | Pluggy | `hooks/_pluggy_adapter.py` | ✅ 完成 |
| 结构化输出 | json.loads + 手动验证 | Outlines | `agent/_outlines_adapter.py` | ✅ 完成 |

### 2.6 后台运行 + 恢复同步（Background Running & Resume）

**状态**: 🔄 补丁已创建（0067-0074）
**目标**: 支持 Ctrl+B 后台化 CLI/TUI 任务，执行 `clawcodex --resume` 时对话流实时同步更新（非静态快照）

#### 核心设计

```
┌──────────────────┐     ┌───────────────────┐     ┌─────────────────┐
│   后台任务循环    │────►│  TranscriptWriter │────►│  transcript.jsonl│
│                  │     │  (O_APPEND 原子)   │     │  (实时增量)      │
└──────────────────┘     └───────────────────┘     └─────────────────┘
                                                              │
                                                              │ watchdog
                                                              ▼ 通知
┌──────────────────┐     ┌───────────────────┐     ┌─────────────────┐
│   新终端 TUI      │◄────│  SessionWatcher    │◄────│  会话目录变更   │
│                  │     │  (监控 + 事件)     │     │                 │
└──────────────────┘     └───────────────────┘     └─────────────────┘
```

#### 架构组件

| 组件 | 补丁文件 | 功能 |
|------|---------|------|
| `BackgroundState` | `0067.src.agent.background_state.py.patch` | 进程级后台信号管理器单例，signal/flag 管理 |
| `TailFollower` | `0068.src.services.tail_follower.py.patch` | tail -f 风格尾部追踪器，实时读取 JSONL 增量 |
| `SessionWatcher` | `0069.src.utils.session_watcher.py.patch` | 目录变更监控（inotify/FSEvents/500ms polling fallback） |
| `keybindings.py` | `0070.src.tui.keybindings.py.patch` | 添加 `ctrl+b → agent.background` 绑定 |
| `app.py` | `0071.src.tui.app.py.patch` | `action_agent_background()` 处理 Ctrl+B |
| `session.py` | `0072.src.agent.session.py.patch` | 新增 `Session.resume_with_tail()` 工厂方法 |
| `agent_bridge.py` | `0073.src.tui.agent_bridge.py.patch` | 集成 TailFollower 支持 |
| `graceful_shutdown.py` | `0074.src.utils.graceful_shutdown.py.patch` | 添加 SIGTSTP 处理 |

#### 工作流程

1. **后台化**: TUI 按 Ctrl+B → `signal_background()` 设置信号 → `foreground_promotion.run_with_background_escape` 竞速检测 → `register_agent_background()` → TUI 退出，后台任务通过 `TranscriptWriter` 追加消息
2. **恢复**: `Session.resume_with_tail()` 恢复会话 + 启动 `TailFollower` → 新消息写入时 TailFollower 检测到偏移量变化 → 通知 UI 实时更新

#### 关键设计点

- **不修改上游源码** — 所有改动通过标准 quilt 补丁注入（`patches/upstream/b125e16/`）
- **O_APPEND 原子写入** — 后台任务写入时不会丢失或交错
- **尾部追踪而非快照** — 恢复时读取增量，而非全量重放
- **跨平台** — SessionWatcher 自动选择 inotify (Linux) / FSEvents (macOS) / polling fallback

### 2.5 工具系统按需加载（Tool System Extension）

**状态**: ✅ 完成
**目标**: 工具组件解耦，Agent 可配置完全无工具，支持按 bundle 选择性加载

#### 架构设计

```
src/tool_system_ext/          # 扩展层（与上游解耦）
├── bundles.py                 # 工具束定义
├── registry_ext.py           # Registry 扩展（组合模式）
├── agent_config.py           # Agent 工具配置
└── patches/tool_system/      # 上游适配补丁
```

#### 四种工具模式

| 模式 | 说明 | 工具数 |
|------|------|--------|
| `bare` | 零工具，纯推理 Agent | 0 |
| `default` | 默认束（Bash, Edit, Write, Read, Glob, Grep, WebSearch, WebFetch） | 8 |
| `clawcodex` | 所有原生内置工具 | 42 |
| `all` | 所有工具束（即 default + clawcodex） | 2 bundles |

#### 工具束定义

| 束名 | 工具 |
|------|------|
| `default` | Bash, Edit, Write, Read, Glob, Grep, WebSearch, WebFetch |
| `clawcodex` | 全部原生工具（Agent, AskUserQuestion, Bash, ... 等 42 个） |

#### 自定义 Agent 工具配置

自定义 Agent 在 Markdown frontmatter 中使用 `tools` 字段时，支持 bundle 引用（以 `:` 前缀区分）：

```markdown
---
name: my-agent
description: A research agent
tools: [":default"]           # 使用 default bundle
---

# 或混用
tools: [":clawcodex", "Bash"] # clawcodex 全部工具 + 额外的 Bash
```

解析时展开逻辑：
- `":bundle_name"` → 展开为对应 bundle 的工具列表
- 普通工具名 → 保持原样

#### 模式到 Bundle 的映射

```python
MODE_BUNDLES = {
    "bare": [],
    "default": ["default"],
    "clawcodex": ["clawcodex"],
    "all": ["default", "clawcodex"],
}
```

#### 动态工具注册通知机制

```python
class ToolRegistryExt:
    def on_tool_registered(self, callback: ToolRegistrationCallback) -> None
    def off_tool_registered(self, callback: ToolRegistrationCallback) -> None
```

Bare Agent 可通过切换配置动态加载新工具：

```python
ext = ToolRegistryExt(registry)

# Bare 状态
bare_config = load_tool_config(mode="bare")
assert ext.get_tools_for_config(bare_config) == []

# 切换配置后自动加载
default_config = load_tool_config(mode="default")
tools = ext.get_tools_for_config(default_config)  # 包含所有注册表工具
```

#### 与上游解耦策略

- 使用**组合模式**扩展 ToolRegistry，不修改原类
- Bundle 定义独立于上游代码
- 补丁目录 `patches/tool_system/` 用于快速适配上游更新（当前为空，扩展层独立实现）

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

**状态**: ✅ 已完成
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

#### 3.6.4 实现文件

| 文件 | 功能 | 状态 |
|------|------|------|
| `memdir/memdir.py` | `load_memory_prompts()` 按作用域加载 | ✅ 完成 |
| `memdir/memory_types.py` | 四种记忆类型定义 | ✅ 完成 |
| `memdir/paths.py` | 记忆目录路径解析 | ✅ 完成 |
| `context_system/prompt_assembly.py` | 支持 `memory_scopes` 参数 | ✅ 完成 |
| `agent/agent_definitions.py` | `memory` 字段定义 | ✅ 完成 |

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
| **orchestrator run** | `orchestrator/cli/run.py` | 启动 orchestrator，保留原有 `--workflow` / `--dashboard` / `--port` 参数 |
| **orchestrator status** | `orchestrator/cli/status.py` | 全局 running/paused/completed/failed 状态汇总 |
| **orchestrator issues** | `orchestrator/cli/issues.py` | list / show / tail 子命令 |
| **orchestrator pause/resume/stop** | `orchestrator/cli/lifecycle.py` | agent 生命周期控制 |
| **orchestrator takeover** | `orchestrator/cli/takeover.py` | 会话接管：终止 agent + 启动 REPL |
| **orchestrator inject** | `orchestrator/cli/inject.py` | 操作员 Hint 注入 |
| **orchestrator clarify** | `orchestrator/cli/clarify.py` | 澄清应答 |
| **orchestrator workspace** | `orchestrator/cli/workspace.py` | workspace 文件操作 |
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
- 操作员修改 workspace 文件或注入 hint 后调用 `orchestrator resume <id>`
- `pause_reason` 内容注入到下一个 LLM prompt 的 system context
- Agent 从断点继续

**Takeover（最强介入）**：
```bash
clawcodex orchestrator takeover 42
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
clawcodex orchestrator inject 42 "别动 auth.py，已经有人在改了"

# 查看已注入的提示列表
clawcodex orchestrator inject 42 --list

# 删除某条提示
clawcodex orchestrator inject 42 --remove 1
```

**注入时机**：WorkspaceManager 在每个 tool call 执行前，检查 `.operator_hints.md` 并将内容以特殊格式追加到 tool context 中：

```
--- Operator Hint (注入于 2026-05-19 10:35:00) ---
别动 auth.py，已经有人在改了
-----------------------------------
```

#### 3.12.7 不兼容变更记录

> **重要**：发布时需将现有 `clawcodex --workflow` 改为 `clawcodex orchestrator run`。
> 这是唯一一个不兼容 CLI 变更，需在 release note 中特别说明。

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
| Provider 层 | LiteLLM | ~1,430 行 | P0 | 适配器已完成，待集成 |
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

> **注意**：`clawcodex --workflow` 将在发布时废弃，替换为 `clawcodex orchestrator run`。
> 这是一个**不兼容变更**，现有启动方式需切换到新的子命令结构。

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
| `clawcodex orchestrator run` | 启动自主模式（替代 `--workflow`） | ⏳ 待实现 |
| `clawcodex orchestrator status` | 全局状态总览 | ⏳ 待实现 |
| `clawcodex orchestrator issues list/tail/show` | issue 查看与监控 | ⏳ 待实现 |
| `clawcodex orchestrator pause/resume/stop` | agent 生命周期控制 | ⏳ 待实现 |
| `clawcodex orchestrator takeover` | 会话接管 | ⏳ 待实现 |
| `clawcodex orchestrator inject` | 操作员 Hint 注入 | ⏳ 待实现 |
| `clawcodex orchestrator clarify` | 澄清应答 | ⏳ 待实现 |
| `clawcodex orchestrator workspace` | workspace 文件操作 | ⏳ 待实现 |
| `clawcodex orchestrator dashboard` | 独立 dashboard UI | ⏳ 待实现 |

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

*文档更新时间: 2026-05-20*

*版本 v1.3 更新：三层解耦架构（Layer 1 upstream / Layer 2 capabilities / Layer 3 features），新增 `src/api/` 加入 features 层，`src/api/query.py` 通过 `capabilities/headless_runner.py` 实现运行时零上游耦合。*