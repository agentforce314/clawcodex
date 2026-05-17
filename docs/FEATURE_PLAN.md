# ClawCodex 特性规划与设计文档

> 文档路径: `docs/FEATURE_PLAN.md`
> 基于: `clawcodex-opensource-replacement-analysis-v2.md`, `clawcodex_vs_ccb_analysis-v3.md`, `INTEGRATION.md`, `TEAM_MEMBERSHIP.md`
> 版本: v1.0
> 更新日期: 2026-05-18

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

**状态**: 实现中（Phase 1-2）
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

#### 3.1.2 待完成功能

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 多 Tracker 支持 | P2 | 除 Linear 外支持其他 Tracker |
| 重试队列 + 退避 | P2 | 失败任务自动重试 |
| SSH 远程执行 | P3 | 远程工作区支持 |
| 可观测性集成 | P3 | Langfuse/Sentry 集成 |

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

### 3.5 高级功能（对比 Claude Code Best）

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

### 5.2 规划 CLI 扩展

| 命令 | 说明 | 状态 |
|------|------|------|
| `clawcodex --workflow WORKFLOW.md` | 自主模式 | ✅ 已规划 |
| `clawcodex --workflow WORKFLOW.md --dashboard` | 带状态面板 | ✅ 已规划 |
| `clawcodex --workflow WORKFLOW.md --port 8080` | LiveView 端口 | ⏳ 待实现 |

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

*文档更新时间: 2026-05-18*