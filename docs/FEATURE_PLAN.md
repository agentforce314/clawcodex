# ClawCodex 特性规划与设计文档

> 文档路径: `docs/FEATURE_PLAN.md`
> 基于: `clawcodex-opensource-replacement-analysis-v2.md`, `clawcodex_vs_ccb_analysis-v3.md`, `INTEGRATION.md`, `TEAM_MEMBERSHIP.md`
> 版本: v1.2
> 更新日期: 2026-05-19

---

## 一、项目概述

### 1.1 项目定位

ClawCodex 是 Anthropic Claude Code 的 Python 移植版，同时扩展多 Provider 支持，目标成为功能完整的 AI Agent CLI 工具。

### 1.2 当前架构

```
src/
├── agent/              # Agent 核心（run_agent, fork_subagent, resume_agent）
├── orchestrator/       # 自主模式编排（Symphony 集成）
├── providers/          # 多 Provider 支持（Anthropic/OpenAI/GLM/Minimax/DeepSeek/OpenRouter）
├── tool_system/        # 工具系统（30+ 内置工具）
├── hooks/              # 钩子系统（28 事件）
├── permissions/        # 权限与安全（Bash 安全、文件系统权限）
├── context_system/     # 上下文构建（Git/Memory/Prompt）
├── compact_service/    # 上下文压缩
├── services/           # 扩展服务（MCP/Swarm/IDE/Analytics）
├── api/                # 公共 API 层
├── settings/           # 配置系统（Pydantic-settings）
└── cli.py              # CLI 入口
```

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

### 2.2 Provider 层

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

### 3.2 Team 成员管理（Phase-7）

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