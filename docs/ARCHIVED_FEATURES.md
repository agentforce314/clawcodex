# ClawCodex 已归档功能详情

> 文档路径: `docs/ARCHIVED_FEATURES.md`
> 源文档: `docs/FEATURE_PLAN.md` 第2节 (已实现功能模块)
> 版本: v1.0
> 创建日期: 2026-05-30

---

## 一、核心 Agent 系统

### 1.1 Agent 执行循环

| 属性 | 值 |
|------|-----|
| 文件 | `src/upstream/b125e16/agent/run_agent.py` |
| 功能 | 四级权限模型、Subagent 隔离、消息完整性 |
| 状态 | ✅ 已归档 |

### 1.2 Fork Subagent

| 属性 | 值 |
|------|-----|
| 文件 | `src/upstream/b125e16/agent/fork_subagent.py` |
| 功能 | 创建独立会话的 sub-agent |
| 状态 | ✅ 已归档 |

### 1.3 Resume Agent

| 属性 | 值 |
|------|-----|
| 文件 | `src/upstream/b125e16/agent/resume_agent.py` |
| 功能 | 从断点恢复 sub-agent |
| 状态 | ✅ 已归档 |

### 1.4 Foreground Promotion

| 属性 | 值 |
|------|-----|
| 文件 | `src/upstream/b125e16/agent/foreground_promotion.py` |
| 功能 | 后台 agent 提升到前台 |
| 状态 | ✅ 已归档 |

### 1.5 Session 管理

| 属性 | 值 |
|------|-----|
| 文件 | `src/upstream/b125e16/agent/session.py` |
| 功能 | 会话状态管理 |
| 状态 | ✅ 已归档 |

### 1.6 Transcript

| 属性 | 值 |
|------|-----|
| 文件 | `src/upstream/b125e16/agent/transcript.py` |
| 功能 | 对话转录本管理 |
| 状态 | ✅ 已归档 |

### 1.7 Prompt 构建

| 属性 | 值 |
|------|-----|
| 文件 | `src/upstream/b125e16/agent/prompt.py` |
| 功能 | 系统 Prompt 组装 |
| 状态 | ✅ 已归档 |

### 1.8 Agent 定义系统

| 属性 | 值 |
|------|-----|
| 文件 | `src/upstream/b125e16/agent/agent_definitions.py` |
| 功能 | Agent 类型、工具、配置定义 |
| 状态 | ✅ 已归档 |

### 1.9 Agent 记忆作用域

| 属性 | 值 |
|------|-----|
| 文件 | `src/memdir/memdir.py` |
| 功能 | 按需加载不同作用域的记忆 |
| 状态 | ✅ 已归档 |

---

## 二、三层解耦架构（Layer Isolation）

### 2.1 架构概述

| 属性 | 值 |
|------|-----|
| Layer 1 | `src/upstream/` / `src/upstream/v2025_04/` — 上游代码镜像（只读） |
| Layer 2 | `src/capabilities/` — Protocol 接口定义，无运行时上游依赖 |
| Layer 3 | `src/orchestrator/` / `src/api/` — ClawCodex 新增组件，完全解耦 |

### 2.2 关键文件

| 文件 | 功能 |
|------|------|
| `src/capabilities/event_protocol.py` | ToolEvent 接口协议 |
| `src/capabilities/headless_protocol.py` | HeadlessOptions / HeadlessRunner 接口协议 |
| `src/capabilities/headless_runner.py` | 可插拔后端分发器 |
| `src/api/query.py` | 运行时零上游耦合 |
| `upstream-sync.yaml` | `src/api` 加入 features 层 |

### 2.3 解耦结果

| 组件 | 上游直接引用 | 运行时耦合 |
|------|------------|-----------|
| `src/orchestrator/` | ❌ 无 | ✅ 通过 headless_runner 间接 |
| `src/api/query.py` | ❌ 无 | ✅ 通过 headless_runner 间接 |
| `src/api/orchestration.py` | ❌ 无 | ✅ 只调用 orchestrator 内部 |
| `src/capabilities/` | ❌ 无 | ✅ 只定义 Protocol，无实现 |

**upstream-sync audit**: 零层违规验证通过

---

## 三、Provider 层

### 3.1 支持的 Provider

| Provider | 文件 | 状态 |
|----------|------|------|
| Anthropic | `src/providers/anthropic_provider.py` | ✅ 已归档 |
| OpenAI | `src/providers/openai_provider.py` | ✅ 已归档 |
| OpenAI Compatible | `src/providers/openai_compatible.py` | ✅ 已归档 |
| GLM | `src/providers/glm_provider.py` | ✅ 已归档 |
| MiniMax | `src/providers/minimax_provider.py` | ✅ 已归档 |
| DeepSeek | `src/providers/deepseek_provider.py` | ✅ 已归档 |
| OpenRouter | `src/providers/openrouter_provider.py` | ✅ 已归档 |
| LiteLLM 适配器 | `src/providers/_litellm_adapter.py` | ✅ 已归档 |

### 3.2 LiteLLM 适配器

| 属性 | 值 |
|------|-----|
| 文件 | `src/providers/_litellm_adapter.py` |
| 功能 | P0，统一 100+ 模型 |
| 状态 | ✅ 已归档 |

---

## 四、工具系统

### 4.1 内置工具列表

| 工具 | 文件 | 状态 |
|------|------|------|
| FileRead | `src/tool_system/tools/read.py` | ✅ 已归档 |
| FileWrite | `src/tool_system/tools/write.py` | ✅ 已归档 |
| FileEdit | `src/tool_system/tools/edit.py` | ✅ 已归档 |
| Glob | `src/tool_system/tools/glob.py` | ✅ 已归档 |
| Grep | `src/tool_system/tools/grep.py` | ✅ 已归档 |
| Bash | `src/tool_system/tools/bash/` | ✅ 已归档 |
| WebFetch | `src/tool_system/tools/web_fetch.py` | ✅ 已归档 |
| WebSearch | `src/tool_system/tools/web_search.py` | ✅ 已归档 |
| AskUserQuestion | `src/tool_system/tools/ask_user_question.py` | ✅ 已归档 |
| SendMessage | `src/tool_system/tools/send_message.py` | ✅ 已归档 |
| TodoWrite | `src/tool_system/tools/todo_write.py` | ✅ 已归档 |
| TaskStop | `src/tool_system/tools/task_stop.py` | ✅ 已归档 |
| TasksV2 | `src/tool_system/tools/tasks_v2.py` | ✅ 已归档 |
| Agent | `src/tool_system/tools/agent.py` | ✅ 已归档 |
| Team | `src/tool_system/tools/team.py` | ✅ 已归档 |
| Config | `src/tool_system/tools/config.py` | ✅ 已归档 |
| PlanMode | `src/tool_system/tools/plan_mode.py` | ✅ 已归档 |
| Cron | `src/tool_system/tools/cron.py` | ✅ 已归档 |
| MCPTool | `src/tool_system/tools/mcp.py` | ✅ 已归档 |
| MCPResources | `src/tool_system/tools/mcp_resources.py` | ✅ 已归档 |
| Skill | `src/tool_system/tools/skill.py` | ✅ 已归档 |
| ToolSearch | `src/tool_system/tools/tool_search.py` | ✅ 已归档 |
| LSP | `src/tool_system/tools/lsp.py` | ✅ 已归档 |
| Worktree | `src/tool_system/tools/worktree.py` | ✅ 已归档 |
| TaskInspect | `src/tool_system/tools/task_inspect.py` | ✅ 已归档 |
| TaskDirectives | `src/tool_system/tools/task_directives.py` | ✅ 已归档 |
| ProgressReport | `src/tool_system/tools/progress_report.py` | ✅ 已归档 |

### 4.2 工具系统按需加载（Tool System Extension）

| 属性 | 值 |
|------|-----|
| 目录 | `src/tool_system_ext/` |
| 功能 | 工具组件解耦，Agent 可配置完全无工具，支持按 bundle 选择性加载 |
| 状态 | ✅ 已归档 |

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

---

## 五、开源替代组件

| 组件 | 原始实现 | 替代方案 | 适配器文件 | 状态 |
|------|---------|---------|-----------|------|
| 配置系统 | 手动 JSON 管理 | Pydantic-settings | `src/settings/pydantic_adapter.py` | ✅ 已归档 |
| Frontmatter 解析 | 手动 yaml.safe_load | python-frontmatter | `src/skills/_frontmatter_adapter.py` | ✅ 已归档 |
| Bash AST 解析器 | ~1,500 行自建 | tree-sitter-bash | `src/permissions/_treesitter_adapter.py` | ✅ 已归档 |
| Git 操作 | 6 个 subprocess.run() | GitPython | `src/context_system/_gitpython_adapter.py` | ✅ 已归档 |
| Hook 系统 | ~1,200 行自建 | Pluggy | `src/hooks/_pluggy_adapter.py` | ✅ 已归档 |
| 结构化输出 | json.loads + 手动验证 | Outlines | `src/agent/_outlines_adapter.py` | ✅ 已归档 |

**总计已减少代码**: ~3,100 行

---

## 六、后台运行 + 恢复同步

### 6.1 架构设计

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

### 6.2 核心组件

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

### 6.3 工作流程

1. **后台化**: TUI 按 Ctrl+B → `signal_background()` 设置信号 → `foreground_promotion.run_with_background_escape` 竞速检测 → `register_agent_background()` → TUI 退出，后台任务通过 `TranscriptWriter` 追加消息
2. **恢复**: `Session.resume_with_tail()` 恢复会话 + 启动 `TailFollower` → 新消息写入时 TailFollower 检测到偏移量变化 → 通知 UI 实时更新

### 6.4 关键设计点

- **不修改上游源码** — 所有改动通过标准 quilt 补丁注入（`patches/upstream/b125e16/`）
- **O_APPEND 原子写入** — 后台任务写入时不会丢失或交错
- **尾部追踪而非快照** — 恢复时读取增量，而非全量重放
- **跨平台** — SessionWatcher 自动选择 inotify (Linux) / FSEvents (macOS) / polling fallback

---

## 七、Bridge Phase 8-11 多 Session Daemon 桥接器

### 7.1 架构设计

```
src/bridge/                    # 桥接层（与上游解耦新增）
├── __init__.py                # 模块入口
├── bridge_api.py               # Phase 3: HTTP 客户端 + API 定义
├── bridge_main.py              # Phase 8: 多 Session Daemon 入口
├── remote_bridge_core.py       # Phase 5: 远程桥接核心
├── session_runner.py           # Phase 4: 子 CLI 会话生成
├── repl_bridge.py              # Phase 11: REPL 桥接
├── init_repl_bridge.py         # 初始化 REPL 桥接
├── messaging.py                # 消息传递机制
├── types.py                   # 桥接类型定义
└── headless_bridge.py          # Headless 桥接
```

### 7.2 Phase 里程碑

| Phase | 补丁文件 | 核心组件 | 状态 |
|-------|---------|---------|------|
| Phase 1 | 0002-bridge-complete-Phase-1-* | Config/URL 处理/polling URL | ✅ 已归档 |
| Phase 3 | 0003-bridge-phase-3-port-bridgeApi.ts-* | bridge_api.py HTTP 客户端 | ✅ 已归档 |
| Phase 4 | 0005-bridge-phase-4-port-sessionRunner.ts-* | session_runner.py 子 CLI 生成 | ✅ 已归档 |
| Phase 5 | 0004-bridge-phase-5-MVP-port-remoteBridgeCore.ts-* | remote_bridge_core.py 远程桥接 | ✅ 已归档 |
| Phase 6 | 0006-bridge-phase-6-*-orchestrator-skel-* | 基于 env 的编排器骨架 | ✅ 已归档 |
| Phase 8 | 0007-bridge-phase-8-*-multi-session-daemon-* | bridge_main.py 多会话轮询 | ✅ 已归档 |
| Phase 11a | 0008-bridge-phase-11a-bridge_main-hardening-* | bridge_main.py 硬化 | ✅ 已归档 |
| Phase 11b | 0009-bridge-phase-11b-repl_bridge-hardening-* | repl_bridge.py 硬化 | ✅ 已归档 |

### 7.3 核心组件详细说明

#### 7.3.1 bridge_main.py - 多 Session Daemon 入口 (Phase 8)

多会话轮询守护进程，负责：
- CLI 参数解析 (`--verbose`, `--sandbox`, `--spawn`, `--capacity`, `--permission-mode`, `--name`)
- 多会话容量控制 (capacity gating)
- 会话状态管理 (active_sessions, session_work_ids, completed_work_ids)
- 工作轮询循环 (work poll loop)
- 优雅关闭 (SIGTERM → wait grace → SIGKILL stragglers → deregister)
- SIGINT/SIGTERM 处理器安装

#### 7.3.2 remote_bridge_core.py - 远程桥接核心 (Phase 5)

远程桥接实现，支持：
- v2 环境变量驱动配置
- 远程会话生命周期管理
- 跨进程通信

#### 7.3.3 session_runner.py - 子 CLI 会话生成 (Phase 4)

子进程管理，实现：
- Child CLI 生成和监控
- 工作目录管理
- 会话超时控制

#### 7.3.4 repl_bridge.py - REPL 桥接 (Phase 11)

REPL 集成桥接器，实现：
- REPL 与 Bridge 的消息路由
- 会话状态同步
- TUI 交互支持

#### 7.3.5 bridge_api.py - HTTP 客户端 (Phase 3)

API 通信层：
- 轮询 URL 处理
- 会话注册/注销
- 工作队列管理

---

## 八、Agent Loop Consolidation (Stage 4)

### 8.1 核心变更

| 变更 | 说明 | 行数 |
|------|------|------|
| 删除 `agent_loop.py` | 上游原 Agent 循环逻辑移除 | -537 行 |
| 新增 `renderers.py` | 系统 prompt 渲染器 | +257 行 |
| 新增 `advisor.py` | Advisor 工具 | +125 行 |
| 重构到 `src/query/` | 查询引擎解耦 | - |

### 8.2 renderers.py - 系统 Prompt 渲染器

渲染器负责将系统 prompt 组件组合并格式化：

```python
class SystemPromptRenderer:
    """系统 Prompt 渲染器"""
    def render(self, context: PromptContext) -> str: ...
    def render_capabilities(self, capabilities: list[str]) -> str: ...
    def render_rules(self, rules: list[str]) -> str: ...
```

### 8.3 advisor.py - Advisor 工具

Advisor 工具提供 Token 计数和状态显示：

```python
class AdvisorTool:
    """Advisor 工具 - 提供 token 计数和状态信息"""
    def get_token_usage(self) -> TokenUsage: ...
    def get_cost_estimate(self) -> CostEstimate: ...
```

---

## 九、Advisor Token 计数与状态显示

### 9.1 核心改进

| 改进 | 文件 | 说明 |
|------|------|------|
| Token 计数显示 | `src/agent/conversation.py` | max_history: 100 → 2000 |
| Provider Token 追踪 | `src/providers/anthropic_provider.py` | 增加 token 使用追踪 |
| Base Provider 增强 | `src/providers/base.py` | 统一 token 计数接口 |

### 9.2 max_history 扩展

`src/agent/conversation.py` 中 `max_history` 从 100 提升到 2000，允许更长的对话历史。

### 9.3 Provider Token 追踪

```python
@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
```

---

## 十、REPL 与 TUI 增强

### 10.1 核心组件

| 组件 | 文件 | 功能 |
|------|------|------|
| REPL Core | `src/repl/core.py` | REPL 核心逻辑 |
| TUI App | `src/tui/app.py` | Textual TUI 应用 |
| Keybindings | `src/tui/keybindings.py` | 快捷键绑定 |
| LiveStatus | `src/repl/live_status.py` | 实时状态栏 |

### 10.2 Shift+Tab 权限模式循环

支持在 REPL/LiveStatus/TUI 中通过 `Shift+Tab` 循环切换权限模式：`default → acceptEdits → plan → bypassPermissions`

### 10.3 TUI /permission 命令

在 TUI 中可通过 `/permission` 命令打开权限模式选择器，支持选择：
- Default (default)
- Accept edits (acceptEdits)
- Plan mode (plan)
- Bypass permissions (bypassPermissions) - 需要配置启用
- Don't ask (dontAsk)

### 10.4 REPL/TUI 双向切换

- **REPL → TUI**: `/tui` 命令切换到 Textual TUI，会话历史自动同步
- **TUI → REPL**: `/repl` 命令切换回 CLI REPL，TUI 会话自动保存
- 切换时保留 session、conversation、permission_mode 等状态

---

## 十一、TUI 响应性修复

### 11.1 问题描述

thinking 过程中 LLM 服务超时时，ESC、CTRL+C、CTRL+D 和 /exit 都无效，界面完全无反应。

### 11.2 根因分析

1. `StreamWatchdog` 超时只关闭 HTTP 响应流，不触发 TUI 的 `AbortController`
2. `action_cancel_or_quit`（Ctrl+C 处理）直接调用 `self.exit()`，没有先调用 `agent_bridge.cancel()`

### 11.3 修复方案

| 文件 | 修改内容 |
|------|---------|
| `src/tui/app.py:322` | `action_cancel_or_quit` 先调用 `self._agent_bridge.cancel()`，取消成功则返回，失败才 exit |
| `src/utils/stream_watchdog.py` | 新增 `abort_signal` 参数，超时时调用 `abort_signal._fire()` 触发 TUI 取消机制 |
| `src/providers/anthropic_provider.py:366` | `StreamWatchdog(stream)` → `StreamWatchdog(stream, abort_signal=abort_signal)` |

---

## 十二、TaskInspect/TaskDirectives 工具注册

### 12.1 问题

`TaskInspectTool` 和 `TaskDirectivesTool` 代码文件存在于 `src/tool_system/tools/` 目录，但未注册到 `ALL_STATIC_TOOLS`，导致 AI Agent 无法调用。

### 12.2 修复

在 `src/tool_system/tools/__init__.py` 中添加：
- 导入: `from .task_inspect import TaskInspectTool`, `from .task_directives import TaskDirectivesTool`
- 添加到 `ALL_STATIC_TOOLS` 列表
- 添加到 `__all__` 导出列表

---

## 十三、ProgressReportTool 工具注册

### 13.1 问题

`ProgressReportTool` 代码文件存在于 `src/tool_system/tools/progress_report.py`，但未注册到 `ALL_STATIC_TOOLS`。

### 13.2 修复

在 `src/tool_system/tools/__init__.py` 中添加：
- 导入: `from .progress_report import ProgressReportTool`
- 添加到 `ALL_STATIC_TOOLS` 列表
- 添加到 `__all__` 导出列表

---

## 十四、TUI 权限模式选择器

### 14.1 功能

通过 `PermissionModePickerScreen` 模态对话框支持 5 种权限模式：
- `default` - 每个工具运行前询问
- `acceptEdits` - 自动批准文件编辑操作
- `plan` - Plan mode - 自动批准只读操作
- `bypassPermissions` - 运行所有工具不提示
- `dontAsk` - 从不提示，自动批准所有

### 14.2 组件位置

```
src/tui/screens/permission_mode_picker.py
```

---

## 十五、会话恢复浏览器 (Resume Conversation)

### 15.1 功能

- 模糊搜索 (fuzzy search)：支持输入过滤历史会话
- 实时计数显示：显示 "X / Y sessions" 过滤结果
- 会话元数据展示：标题、模型、消息数、时间戳

### 15.2 使用方式

| 方式 | 说明 |
|------|------|
| `clawcodex --tui --resume` | 启动时直接进入会话选择 |
| `/resume` 命令 | 从 REPL 呼出会话选择器 |
| Ctrl+B 后台后 | 用户选择会话重新附着 |

### 15.3 组件位置

```
src/tui/screens/resume_conversation.py
src/repl/live_status.py  # 新增 Live Status 实时状态组件
```

---

## 十六、Orchestrator 自主模式（Symphony 集成）

### 16.1 核心组件

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| Orchestrator | `src/orchestrator/orchestrator.py` | ✅ 已归档 | 轮询循环 + 任务分发 |
| WorkspaceManager | `src/orchestrator/workspace.py` | ✅ 已归档 | 每个 Issue 的隔离工作区 |
| LinearAdapter | `src/orchestrator/linear/adapter.py` | ✅ 已归档 | Linear GraphQL API 适配器 |
| LinearClient | `src/orchestrator/linear/client.py` | ✅ 已归档 | HTTP + GraphQL 客户端 |
| Issue | `src/orchestrator/linear/issue.py` | ✅ 已归档 | Issue 数据模型 |
| AgentRunner | `src/orchestrator/agent_runner.py` | ✅ 已归档 | 连接 QueryRunner |
| PromptBuilder | `src/orchestrator/prompt_builder.py` | ✅ 已归档 | 模板渲染 |
| WorkflowLoader | `src/orchestrator/workflow.py` | ✅ 已归档 | WORKFLOW.md 解析 |
| ApprovalPolicy | `src/orchestrator/approval_policy.py` | ✅ 已归档 | 工具调用审批策略 |
| StatusDashboard | `src/orchestrator/status_dashboard.py` | ✅ 已归档 | 终端 UI 状态面板 |
| TrackerAdapter | `src/orchestrator/tracker.py` | ✅ 已归档 | Tracker 协议抽象 |
| IssueRegistry | `src/orchestrator/issue_registry.py` | ✅ 已归档 | 持久化 issue→commit→PR 映射 |
| ClarificationQueue | `src/orchestrator/clarification_queue.py` | ✅ 已归档 | 操作员异步应答队列 |
| CLI orchestrator group | `src/orchestrator/cli/` | ✅ 已归档 | `clawcodex orchestrator` 统一入口 |

### 16.2 已完成功能

| 功能 | 说明 |
|------|------|
| 多 Tracker 支持 | GitHub/Gitee/GitCode 通用 REST 适配器已实现 |
| CLI 集成 | `cli.py:596-666` 已实现 `--workflow`、`--dashboard`、`--port` |
| 重试队列 + 退避 | 实现指数退避重试 |
| 重试上限保护 | `_schedule_retry` 增加最大重试次数限制 |
| Issue State 前置检查 | `_poll_and_dispatch` 在 launch 前查 issue 最新 state |
| 已有 PR 跳过后续处理 | `_launch_issue` 前查 `find_pull_request` |
| 本地 Issue 注册表 | 持久化 issue→commit→PR 映射到 JSON |
| Issue Clarification 流程 | 三通道 ClarificationQueue + TrackerAdapter 评论接口 |
| Orchestrator CLI | `clawcodex orchestrator` 统一入口 |

### 16.3 Orchestrator CLI 命令

| 命令 | 说明 |
|------|------|
| `clawcodex orchestrator server start --workflow PATH` | 启动 orchestrator daemon |
| `clawcodex orchestrator server status` | 查看 daemon 运行状态 |
| `clawcodex orchestrator server stop` | 停止 orchestrator daemon |
| `clawcodex orchestrator issue list [--status]` | 列出所有 issue 及状态 |
| `clawcodex orchestrator issue tail --id <id>` | 实时 tail tool call 日志 |
| `clawcodex orchestrator issue show --id <id>` | 查看 issue 详情 |
| `clawcodex orchestrator issue pause --id <id>` | 暂停 agent |
| `clawcodex orchestrator issue resume --id <id>` | 恢复暂停中的 agent |
| `clawcodex orchestrator issue stop --id <id>` | 强制终止 agent |
| `clawcodex orchestrator issue inject --id <id> <hint>` | 向运行中的 agent 注入提示 |
| `clawcodex orchestrator issue clarify --id <id> --answer <text>` | 操作员澄清应答 |
| `clawcodex orchestrator issue workspace --id <id> --ls` | 列出 workspace 文件 |
| `clawcodex orchestrator issue takeover --id <id>` | 完全接管 |
| `clawcodex orchestrator dashboard --port` | 独立 dashboard UI |

---

## 十七、MCP 协议扩展

### 17.1 当前支持

| 功能 | 文件 | 状态 |
|------|------|------|
| Stdio Transport | `src/services/mcp/` | ✅ 已归档 |
| HTTP/SSE Transport | `src/services/mcp/` | ✅ 已归档 |
| WebSocket Transport | `src/services/mcp/` | ✅ 已归档 |
| OAuth 支持 | `src/services/mcp/` | ✅ 已归档 |
| HTTPS/XSS 硬化 | `src/services/mcp/` | ✅ 已归档 |

---

## 十八、Agent 间自主观察与消息交互

### 18.1 角色定义

| 角色 | 判断标准 | 说明 |
|------|---------|------|
| **Manager Agent** | 工具集中包含 `TaskInspect` + `TaskDirectives` | 通过工具组合自动识别，无需独立 Agent 类型 |
| **Worker Agent** | 不包含上述管理工具 | 普通执行单元 |

### 18.2 核心工具

| 工具 | 文件 | 功能 |
|------|------|------|
| `TaskInspect` | `src/tool_system/tools/task_inspect.py` | Manager 查询 Worker 运行时状态 |
| `TaskDirectives` | `src/tool_system/tools/task_directives.py` | Manager 向 Worker 注入优先级指令 |

### 18.3 实施阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase M1 | `TaskInspect` + `TaskDirectives` 核心工具 | ✅ 已归档 |
| Phase M2 | `queue_pending_message` 支持 priority | ✅ 已归档 |
| Phase M3 | `drain_pending_messages` 按优先级消费 | ✅ 已归档 |
| Phase M4 | 工具可见性过滤（仅 Manager 可调用） | ✅ 已归档 |
| Phase M5 | 权限规则传递 | ✅ 已归档 |

---

## 十九、POS to Agent 转化模式

### 19.1 三层映射关系

| 工作流组件 | Agent 架构 | 示例 |
|-----------|-----------|------|
| POS (专业系统) | Agent | 数据分析 Agent、CI/CD Agent、ML Pipeline Agent |
| 工作流步骤 | Skill | `deploy_service`、`run_etl`、`train_model` |
| SDK 接口 | 原子工具 | `s3_upload`、`k8s_apply`、`spark_submit` |

### 19.2 实现文件

| 文件 | 说明 |
|------|------|
| `src/pos_converter/__init__.py` | 模块入口 |
| `src/pos_converter/sdk_parser.py` | SDK 解析（支持 OpenAPI JSON / URL / 简单方法列表） |
| `src/pos_converter/skill_grouper.py` | Skill 分组（静态 MappingRule + LLM 辅助） |
| `src/pos_converter/agent_builder.py` | Agent 构建 + 持久化 |
| `src/pos_converter/convert_pos_skill.py` | `/convert-pos-to-agent` Skill 实现 |
| `src/pos_converter/templates.py` | 模板定义 |
| `src/skills_ext/bundled/pos_to_agent.py` | bundled skill 注册（解耦上游） |

---

*本文档由 `docs/FEATURE_PLAN.md` 第2节归档生成，最后更新于 2026-05-30*