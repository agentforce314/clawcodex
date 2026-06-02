# ClawCodex 已归档功能详情

> 文档路径: `docs/ARCHIVED_FEATURES.md`
> 源文档: `docs/FEATURE_PLAN.md` 第2节 (已实现功能模块)
> 版本: v1.2
> 创建日期: 2026-05-30
> 最后更新: 2026-06-02
> 新增归档: F-13、F-34、F-36、F-38、F-39、F-41、F-42、F-43、F-45、F-47 已实现功能设计归档

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

### 3.3 LiteLLM Provider 替换（开源替代组件 R-7）

| 属性 | 值 |
|------|-----|
| 适配器文件 | `src/providers/_litellm_adapter.py` + `extensions/providers_ext/litellm_provider.py` |
| 工厂入口 | `src/providers/__init__.py:create_provider()` / `should_use_litellm()` |
| 环境变量 | `CLAW_USE_LITELLM=true|1|yes|on` |
| 状态 | ✅ 已归档（2026-05-30） |

#### 架构

```
src/providers/base.py (保留 BaseProvider 抽象)
    ↓
src/providers/__init__.py (should_use_litellm() + create_provider() 工厂)
    ↓
extensions/providers_ext/litellm_provider.py (LiteLLM 实现)
    ↓
LiteLLM (开源依赖)
```

#### 关键文件

| 文件 | 功能 |
|------|------|
| `extensions/providers_ext/__init__.py` | 扩展包导出 |
| `extensions/providers_ext/litellm_provider.py` | LiteLLM Provider 实现（含 `_get_litellm_model()` 提取）|
| `src/providers/__init__.py` | 工厂函数 `should_use_litellm()` / `create_provider()` |
| `src/providers/_litellm_adapter.py` | 兼容垫片（重新导出扩展包符号） |
| `src/entrypoints/headless.py` | 使用 `create_provider()` |
| `src/entrypoints/tui.py` | 使用 `create_provider()` |
| `pyproject.toml` | 包发现包含 `extensions*` |

#### 代码减少

- 原始 Provider 类：~1,630 行
- 替换后：~200 行
- **减少代码**：~1,430 行

#### 环境开关行为

| `CLAW_USE_LITELLM` | 行为 |
|--------------------|------|
| `false`（默认） | 使用原始 Provider 类 |
| `1` / `true` / `yes` / `on` | 使用 LiteLLM 统一 Provider |

#### 兼容性

- LiteLLM 保留 `BaseProvider` 接口可回退
- 旧导入路径 `from src.providers._litellm_adapter import ...` 继续有效

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

### 16.4 生产强化（F-1.1~F-1.4）

#### F-1.1 重试上限保护

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_schedule_retry` |
| 新增字段 | `workflow.agent.max_retry_attempts: int = 5` |
| 触发条件 | `attempt > max_retry_attempts` 时跳过调度 |
| 副作用 | 不写入 `completed`（需人工确认后手动关闭 issue） |
| 状态 | ✅ 已归档 |

#### F-1.2 Issue State 前置检查

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_launch_issue` |
| 检查方式 | `tracker.fetch_issue_states_by_ids([issue.id])`，非 active 跳过 |
| 副作用 | 从 `claimed` 集合移除，不进入 `completed` |
| 状态 | ✅ 已归档 |

#### F-1.3 已有 PR 跳过后续处理

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_launch_issue` |
| 检查方式 | `tracker.find_pull_request(head_branch, base_branch)` |
| 适用范围 | 仅 RepositoryTrackerAdapter（GitHub/Gitee/GitCode） |
| 副作用 | 标记 completed，重启后不重复处理 |
| 状态 | ✅ 已归档 |

#### F-1.4 本地 Issue 注册表

| 项 | 值 |
|---|---|
| 文件位置 | `{workspace.root}/.clawcodex_issue_registry.json` |
| 实现文件 | `orchestrator/issue_registry.py:IssueRegistry` |
| 记录字段 | `issue_id / identifier / branch_name / commit_sha / pr_number / pr_url / status / attempt_count / clarification_status / question_history` |
| Status 枚举 | `PENDING → SYNCED → COMPLETED / FAILED / ABANDONED` |
| 状态 | ✅ 已归档 |

### 16.5 Issue 语义澄清流程（F-1.5~F-1.11）

| 通道 | 实现 | 触发 | 降级 |
|------|------|------|------|
| 通道一 | `StatusDashboard` 交互提示 | 非 headless + 操作员在线 | 5 分钟无操作 |
| 通道二 | `ClarificationQueue` 文件队列（`~/.clawcodex/clarification_queue.json`） | 异步 CLI `clarify` 应答 | 30 分钟 |
| 通道三 | `TrackerAdapter.create_clarification_comment()` | @mention Issue 作者 | 72 小时 |

#### ClarificationStatus 枚举

```python
class ClarificationStatus(str, Enum):
    NONE = "none"
    AWAITING_LOCAL = "awaiting_local"        # 等待本地操作员
    AWAITING_AUTHOR = "awaiting_author"     # 已发 @mention，等待作者
    RECEIVED = "received"
    RESOLVED_LOCAL = "resolved_local"        # 来自本地操作员
    RESOLVED_AUTHOR = "resolved_author"     # 来自 @mention 作者
    TIMED_OUT_LOCAL = "timed_out_local"     # 本地超时，降级通道三
    TIMED_OUT_AUTHOR = "timed_out_author"   # 作者超时
    EXHAUSTED = "exhausted"
    DUPLICATE_REJECTED = "duplicate_rejected"  # 重复提交，被去重丢弃
    STALE_REJECTED = "stale_rejected"          # 超时升级后收到的过时答案
    CONFLICT_RESOLVED = "conflict_resolved"    # 多渠道冲突已裁决
```

#### 冲突处理原则

- **第一响应者优先**：第一个被 Orchestrator 检测到的有效答案被采纳
- **操作员优先级**：操作员答案始终比作者更可信（`operator_priority: true`）
- **单向升级不可逆**：通道二超时 → 通道三后，原通道迟来答案标记 STALE_REJECTED
- **过期主动通知**：所有被拒绝的答案都要通知对应应答者
- **去重幂等**：同一答案重复提交第二次标记 DUPLICATE_REJECTED

#### 完成阶段（Phase A-G）

- [x] Phase A: `ClarificationQueue` 文件队列 + 冲突处理状态机 + 超时告知
- [x] Phase B: StatusDashboard 交互提示组件
- [x] Phase C: `AskIssueAuthor` 工具 + `ClarificationResolver` 三通道降级
- [x] Phase D: CLI `clarify` 子命令
- [x] Phase E: `TrackerAdapter.fetch_issue_comments()` / `create_clarification_comment()` 接口 + GitHub/Gitee/GitCode 实现
- [x] Phase F: IssueRegistry 澄清字段持久化 + PromptBuilder 澄清内容注入
- [x] Phase G: escalation 策略实现（skip / mark_failed / notify）

#### 新增配置

```yaml
agent:
  clarification:
    operator_priority: true        # 操作员答案优先于作者（默认 true）
    stale_notification: "all"      # "all" | "operator_only" | "none"
    simultaneous_grace_ms: 5000    # 5ms 内视为同时，由 operator_priority 决胜
```

#### 状态

✅ 已归档

### 16.6 Orchestrator CLI 运维操作界面（F-1.13）

完整 CLI 命令集（O1-O8 阶段）：

| 命令 | 阶段 | 状态 |
|------|------|------|
| `clawcodex orchestrator server start --workflow PATH` | O1 | ✅ 已归档 |
| `clawcodex orchestrator server status` | O1 | ✅ 已归档 |
| `clawcodex orchestrator server stop` | O1 | ✅ 已归档 |
| `clawcodex orchestrator issue list [--status]` | O1 | ✅ 已归档 |
| `clawcodex orchestrator issue tail --id <id>` | O3 | ✅ 已归档 |
| `clawcodex orchestrator issue show --id <id>` | O3 | ✅ 已归档 |
| `clawcodex orchestrator issue pause --id <id>` | O2 | ✅ 已归档 |
| `clawcodex orchestrator issue resume --id <id>` | O2 | ✅ 已归档 |
| `clawcodex orchestrator issue stop --id <id>` | O2 | ✅ 已归档 |
| `clawcodex orchestrator issue inject --id <id> <hint>` | O4 | ✅ 已归档 |
| `clawcodex orchestrator issue inject --id <id> --list` | O4 | ✅ 已归档 |
| `clawcodex orchestrator issue inject --id <id> --remove <n>` | O4 | ✅ 已归档 |
| `clawcodex orchestrator issue clarify --id <id> --answer <text>` | O7 | ✅ 已归档 |
| `clawcodex orchestrator issue workspace --id <id> --ls` | O5 | ✅ 已归档 |
| `clawcodex orchestrator issue workspace --id <id> --cat <file>` | O5 | ✅ 已归档 |
| `clawcodex orchestrator issue workspace --id <id> --edit <file> --with <content>` | O5 | ✅ 已归档 |
| `clawcodex orchestrator issue takeover --id <id>` | O6 | ✅ 已归档 |
| `clawcodex orchestrator dashboard --port` | O8 | ✅ 已归档 |

#### 实施阶段

- [x] O1: CLI `orchestrator` group 框架（替代旧 `--workflow` 顶层 flag）
- [x] O2: pause/resume/stop + 状态机
- [x] O3: `issue tail` 流式 event stream + StatusDashboard 实时渲染
- [x] O4: `issue inject` Hint 注入（`.operator_hints.md` 机制）
- [x] O5: `issue workspace --ls/--cat/--edit`
- [x] O6: `issue takeover` 终止 + REPL 接管
- [x] O7: `issue clarify` 澄清应答
- [x] O8: Dashboard LiveView 增强（LLM 摘要 + tool calls 推送）

#### 不兼容变更

- `clawcodex --workflow` 已废弃，替换为 `clawcodex orchestrator server start --workflow PATH`
- 原有扁平子命令（`run`、`status`、`issues`、`pause`、`resume`、`stop`、`inject`、`clarify`、`workspace`、`takeover`）已移除
- 统一使用 noun-verb 结构：`server <verb>` / `issue <verb> --id <id>`

```bash
# 新命令
clawcodex orchestrator server start --workflow test_gitcode_workflow.md
clawcodex orchestrator server status
clawcodex orchestrator issue list
clawcodex orchestrator issue pause --id 42
clawcodex orchestrator issue inject --id 42 "hint text"
```

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

## 二十、Skills System Extension（技能系统扩展层）

> 对应 **F-23**（2026-05-24 完成）

### 20.1 背景

`src/skills/loader.py` 存在以下问题：
- 硬编码 clawcodex 特定路径（`~/.clawcodex/skills` 等）
- `get_all_skills()` 职责过于集中
- 难以独立更新上游

### 20.2 与 Tool System Ext 的对齐设计

| 组件 | Tool System | Skills System |
|------|-------------|---------------|
| 上游核心 | `tool_system/registry.py` | `skills/loader.py` |
| 扩展目录 | `tool_system_ext/` | `skills_ext/` |
| 扩展包装类 | `ToolRegistryExt` | `SkillRegistryExt` |
| Bundle 机制 | `TOOL_BUNDLES` | `SKILL_BUNDLES` |
| Agent 配置 | `AgentToolConfig` | `AgentSkillConfig` |

### 20.3 实现文件清单

| 文件路径 | 优先级 | 状态 | 说明 |
|---------|--------|------|------|
| `src/skills_ext/__init__.py` | P0 | ✅ 已归档 | 扩展层入口 |
| `src/skills_ext/registry_ext.py` | P0 | ✅ 已归档 | `SkillRegistryExt` 包装类 |
| `src/skills_ext/bundles.py` | P0 | ✅ 已归档 | Skill Bundle 定义 |
| `src/skills_ext/agent_config.py` | P1 | ✅ 已归档 | Agent Skill 配置 |
| `src/skills_ext/paths.py` | P1 | ✅ 已归档 | clawcodex 特定路径解析 |
| `src/skills_ext/hooks.py` | P2 | ✅ 已归档 | Skill 生命周期钩子 |
| `src/skills_ext/cache.py` | P2 | ✅ 已归档 | 扩展层缓存管理 |

### 20.4 核心组件

```python
# src/skills_ext/registry_ext.py
class SkillRegistryExt:
    """包装上游 loader，添加 clawcodex 特定功能"""

    def get_all_skills(self, **kwargs) -> list[Skill]:
        base = self._loader.get_all_skills(**kwargs)  # 上游 skills
        clawcodex = self._load_clawcodex_paths()      # clawcodex 特定
        return self._merge_skills(base, clawcodex)     # 合并去重

    def on_skill_registered(self, callback):
        """Skill 注册回调通知"""
        ...
```

### 20.5 迁移阶段

- [x] 阶段 1：创建 `src/skills_ext/` 目录和基础结构
- [x] 阶段 2：迁移 clawcodex 特定路径逻辑到 `skills_ext/paths.py`
- [x] 阶段 3：添加 Bundle 机制和 AgentSkillConfig
- [x] 阶段 4：添加 Hook 机制和回调系统
- [x] 阶段 5：更新 `get_all_skills()` 调用点使用 `SkillRegistryExt`

### 20.6 状态

✅ 已归档（2026-05-24）

---

*本文档由 `docs/FEATURE_PLAN.md` 第2节归档生成，最后更新于 2026-06-01*

<!-- archived-2026-06-02-feature-plan -->

## 二十一2026-06-02 已实现功能归档

> 归档日期: 2026-06-02
> 来源: 本轮从活动规划/进度文档迁移的已实现条目。

#### 二十一1 F-36 LocalTracker 本地 Issue 文档源

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
id: LOCAL-001
identifier: LOCAL-001
state: open
priority: 1
branch_name: local-001-fix-dashboard-workspace
labels:
  - orchestrator
---

---

#### 二十一2 F-38 Orchestrator 验证与报告闭环

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

---

#### 二十一3 F-39 Orchestrator Issue 重跑入口

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

---

#### 二十一4 F-41 Coordinator 轻量工具集

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

---

#### 二十一5 F-42 Shared / Sequential Workspace 策略

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

---

#### 二十一6 F-45 Orchestrator tool-call 审计旁路

**状态**: ✅ 已完成 (2026-06-02)
**优先级**: P1
**跟踪文档**: `docs/PROGRESS.md` → `F-45: Orchestrator tool-call 审计旁路（tool-events.ndjson + 报告登记）`

##### 目标

在 `extensions/orchestrator/agent_runner.py` 的 `_handle_tool_call` 之后追加 NDJSON 旁路落盘，**与 `permission_mode` 解耦**，扩展 `report_writer.RunReport` 字段与 markdown 模板，让审计员从 run 报告就能定位 `~/.clawcodex/tool-events/{run_id}/events.ndjson` 完整 per-tool 决策流水。**终结 "bypass ≠ 无审计" 误读**——bypass 关闭的是 user-prompt audit 层，本特性补上 per-tool 决策 audit 层。

##### 触发背景

- `extensions/orchestrator/report_writer.py:write()` 只持久化 `tool_count: int` 与末尾 4000 字符的 `output_excerpt`，per-tool 决策流水不落盘
- `extensions/orchestrator/agent_runner.py:87-108` 的 `_handle_tool_call` 始终调 `ApprovalPolicy.evaluate()`，`_approved` / `_deny_reason` 写回 `ToolCallEvent` 内存对象 —— 进程崩溃即丢
- 在 orchestrator headless 场景下 `permission_mode` 走 auto-upgrade 到 `bypassPermissions`（`patches/upstream/58ea488/merged/0026.tui_app_py.patch:1287-1291`），TS 注释说 "no logging"，Python 端其实有 ApprovalPolicy —— 审计数据其实有，只是没落盘

##### 旁路落点

```
agent_runner.py:_handle_tool_call(event, session_context)
    ├── ApprovalPolicy.evaluate(policy_event, session_context)  # 已有
    ├── event._approved = policy_event._approved                 # 已有
    ├── event._deny_reason = policy_event._deny_reason           # 已有
    └── _append_tool_event_log(event, session_context)           # 新增 (Sub-A)
            │
            └── 写 ~/.clawcodex/tool-events/{run_id}/events.ndjson
```

##### NDJSON 字段契约（ToolEventLog）

每行 JSON 含 8 字段：

```python
{
    "ts": 1717350000.123,            # time.time()
    "tool": "Bash",                  # event.tool_name
    "params": {"command": "ls -la"}, # event.params（完整）
    "approved": true,                # event._approved
    "deny_reason": null,             # event._deny_reason（允许时为 null）
    "permission_mode": "bypassPermissions",  # session_context["permission_mode"]
    "turn": 12,                      # session.turn_count
    "session_run_id": "2026-06-02T..."      # session.run_id
}
```

##### 报告登记

`report_writer.RunReport` 新增字段：

```python
@dataclass(frozen=True)
class RunReport:
    # ... 已有字段 ...
    tool_events_path: str | None  # 新增
```

`write()` 多接收 `tool_events_path: str | None = None`，`_render_markdown` 加一行 `Tool events: <path>`，`_copy_with_fallback` 把 NDJSON 拷到 `~/.clawcodex/reports/.../{run_id}/` 持久化层。

##### 关键设计决定

1. **旁路挂 `agent_runner` 层，不动 `ApprovalPolicy`**：策略层不感知 run_id / session_context，旁路在 orchestrator 拦截层做，对策略零侵入
2. **NDJSON 而非 SQLite / Parquet**：追加写 O(1)，`tail` / `grep` 友好，无新依赖；审计场景 "看尾部" 占 90%
3. **落 `~/.clawcodex/tool-events/` 而非 workspace**：workspace 会被 `git_sync` 推到 PR，审计数据污染仓库
4. **`params` 不 redact**：与 TS upstream `dontAsk` "All allowed, logged" 行为对齐
5. **不动 `extensions/api/query.py` stream 协议**：职责分离，旁路在 orchestrator 内部
6. **`RunReport.tool_events_path` 加在末尾**：旧 reader 不识别此字段就忽略，向前兼容
7. **rotate 阈值 50MB，7 天清理推 v2.14**：rotate 是单文件级别，清理是跨文件级别，降低本 PR 风险

##### 风险与缓解

| 风险 | 缓解 |
|------|------|
| 磁盘撑大 | 50MB rotate，7 天清理（v2.14 挂 cron） |
| 写并发 | 单 run_id 单 session，`fdopen` + `flush` + O_APPEND 原子写 |
| 异常阻塞 agent | try/except + `logger.exception`，不 raise |
| 敏感数据泄露 | 文档明示 "events.ndjson 在 `~/.clawcodex/`，用户自管 ACL"；后续可加 `--redact` |
| 与 F-40 sink 重叠 | F-40 走 `ToolContext.tasks` 进程内 metadata，本特性走文件系统 NDJSON；两套并存，职责分离 |
| 不动 `extensions/api/query.py` stream 协议 | 旁路在 orchestrator 内部拦截，stream 出口职责不变 |

##### 子特性

- **Sub-A** `_append_tool_event_log` 旁路方法（~50 行）
- **Sub-B** `ToolEventLog` 数据契约（8 字段）+ JSON serializer
- **Sub-C** `RunReport.tool_events_path` 字段 + markdown 模板 + dual-write NDJSON 到 `~/​.clawcodex/reports/...`
- **Sub-D** `AgentRunner.run` 注入 `run_id` 到 `session_context`
- **Sub-E** rotate 策略 + `.gitignore` 默认 patterns
- **Sub-F** 单测 + 集成测试 + 四种 mode 回归

详细 sub-task、当前基线、验收标准、风险与协同见 PROGRESS.md 详节。

##### 实施摘要 (2026-06-02)

落地时同步修复了设计文档的一处隐藏缺口：原设计假设 `_handle_tool_call`（`agent_runner.py:121-142`）已在 run-loop 的 ToolCallEvent 分支被调用，但实际代码中该方法**从未被调用**（run-loop 里有显式注释 "the orchestrator's ApprovalPolicy is not consulted here"）。如果按字面落地，NDJSON 的 `approved` 字段会永远是 `None`，审计数据无意义。修复：在 `agent_runner.py:505-509` 显式 `event = self._handle_tool_call(event, session_context)` 再 `_append_tool_event_log`，并把 `turn` 写回 `session_context`。其他 5 个 sub-task 按设计字面落地。

**新增/修改文件**:
- `extensions/orchestrator/tool_event_log.py`（新增）— `ToolEventLog` 8 字段 frozen dataclass
- `extensions/orchestrator/agent_runner.py`（修改）— `_append_tool_event_log` 方法、`_TOOL_EVENT_LOG_ROTATE_BYTES` 常量、`AgentSession.tool_events_path` 字段、`session_context` 注入、ToolCallEvent 分支接 `_handle_tool_call`
- `extensions/orchestrator/report_writer.py`（修改）— `RunReport.tool_events_path` 字段（末尾默认 `None`）、`write()` dual-write、markdown 模板加 `Tool events:` 行
- `extensions/orchestrator/git_sync.py`（修改）— `_write_report` 转发 `tool_events_path`
- `extensions/orchestrator/config/schema.py`（修改）— `WorkspaceConfig.gitignore_patterns` 默认加 `.reports`
- `tests/test_orchestrator_f45_audit_bypass.py`（新增）— 7 个测试类，16 个 case

**测试**: `tests/test_orchestrator_f45_audit_bypass.py` 16/16、`tests/test_orchestrator_*.py` 271/271、`tests/manual_e2e_f38.py` 4/4 — 共 291 例全绿，零回归。

**与设计文档的两处偏差**（均已与用户确认）:
1. **同步修复 `_handle_tool_call` 调用链**（见上方缺口段）
2. **单文件 50MB rotate**：旧 `events.ndjson` 直接 rename 为 `events.ndjson.1`（覆盖），无多代轮转；7 天清理推 v2.14

---

---

### 二十一7 F-13 Agent 记忆作用域隔离

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

---

### 二十一8 F-43 CLI 模型供应商与模型切换

**状态**: ✅ 已完成 (2026-06-02)
**优先级**: P1
**跟踪文档**: `docs/PROGRESS.md` → `F-43: CLI 模型供应商与模型切换`

> **实施完成**（v2.13）：所有设计要点已落地。`clawcodex_ext/cli/{subcommand_registry.py, provider_cmd/, model_cmd/}` 新增；`clawcodex_ext/runtime/context.py` 接入 `Resolver` 并新增 `swap_provider`；`CommandContext.runtime_context` seam + `TUIOptions.runtime_context` 透传；`/provider` / `/model` 斜杠命令注册到全局 `CommandRegistry` 并在 REPL/TUI 同步私有引用。20/20 F-43 单元测试通过，orchestrator 回归 271/271 通过。`--scope project` 落入 G-1 后续规划。

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

---

### 二十一9 F-47 Permission Settings Schema 重构

**状态**: 📋 设计完成
**优先级**: P1
**跟踪文档**: `docs/PROGRESS.md` → `F-47: Permission Settings Schema 重构（`permissions` 改 dict 形态 + plumb 启动模式）`

#### 目标

修四层串联 bug：

1. `SettingsSchema.permissions: list[PermissionRule]` 的 schema 形状与磁盘实际 dict 形态（`src/permissions/updates.py:291-343` / `src/permissions/setup.py:62-67` / `src/permissions/loader.py:14-30` 写入）不一致
2. `has_allow_bypass_permissions_mode()` 写死了 `settings.extra["permissions"]` 路径
3. `clawcodex_ext/cli/permissions.py:36-39` 调 `initial_permission_mode_from_cli` 时没传 `settings_default_mode`
4. 顶层 `settings.permission_mode` 字段未被 `resolve_permission_state` 读

核心方案：把 `permissions` 改为 `PermissionsConfig` dataclass（dict 形态），与磁盘 + TS 上游契约对齐；`resolve_permission_state` 真正 plumb 启动模式；删除 settings 层"假" `PermissionRule` 死代码。后续 `permissions.*` 新增 sub-key 不需要改 schema —— 走 `PermissionsConfig.additional` 前向兼容包。

> **F-47.1 (2026-06-02) hotfix**：F-47 设计阶段在 `resolve_permission_state` 保留顶层 `settings.permission_mode` 作为 back-compat 读取通道。F-47.1 在项目尚未发布的前提下直接删除该通道——`SettingsSchema.permission_mode` 字段保留为兼容形态但启动时不再被读，磁盘上残留的旧值在启动时被静默忽略。F-46.2 的 deprecation 步骤因此 N/A。详见 `docs/PROGRESS.md` F-47.1 备注。

#### 触发背景

- 2026-06-02 用户报告"配置 `~/.clawcodex/config.json` 的 `settings.permissions.allowBypassPermissionsMode: true` 后,REPL Shift+Tab 仍然只循环 3 档"——四层 bug 串联
- `SettingsSchema.permissions: list[PermissionRule]`（`src/settings/types.py:100`）与磁盘 dict 形态（`updates.py:persist_permission_update` 写 `{allow: [...], defaultMode, ...}`）冲突
- `has_allow_bypass_permissions_mode`（`src/permissions/modes.py:113-140`）只读 `settings.extra["permissions"]`，但 dict 进 known field 后 `extra` 永远是 None
- `resolve_permission_state`（`clawcodex_ext/cli/permissions.py:36-39`）形参 `settings_default_mode` 留好但调用方从未传
- 顶层 `SettingsSchema.permission_mode`（`src/settings/types.py:97`）字段存在但 `resolve_permission_state` 不读
- `src/settings/types.py:13-20` 的 `PermissionRule`（带 `tool/allow/glob/regex/description/source`）与运行时 `src/permissions/types.py:80-84` frozen `PermissionRule`（带 `source/rule_behavior/rule_value`）同名异构，且前者无 caller——死代码

#### Schema 形态变化

**Before**（v2.12）：
```python
@dataclass
class SettingsSchema:
    permissions: list[PermissionRule] = field(default_factory=list)
    permission_mode: PermissionModeType = "default"

@dataclass
class PermissionRule:                      # 死代码
    tool: str = ""
    allow: bool = True
    glob: str | None = None
    regex: str | None = None
    description: str = ""
    source: str = "user"
```

**After**（v2.13）：
```python
@dataclass
class PermissionsConfig:
    """对齐磁盘 + TS 上游契约的 permissions 结构。"""
    allow_bypass_permissions_mode: bool = False
    default_mode: str | None = None
    rules: dict[str, list[str]] = field(default_factory=dict)  # {"allow":[...], "deny":[...], "ask":[...]}
    additional_directories: list[str] = field(default_factory=list)
    additional: dict[str, Any] = field(default_factory=dict)  # forward-compat bag

    @classmethod
    def from_dict(cls, data: Any) -> "PermissionsConfig":
        if not isinstance(data, dict):
            return cls()
        rules: dict[str, list[str]] = {}
        rules_raw = data.get("rules", {}) if isinstance(data.get("rules"), dict) else {}
        for behavior in ("allow", "deny", "ask"):
            v = rules_raw.get(behavior) or data.get(behavior)
            if isinstance(v, list):
                rules[behavior] = [str(x) for x in v]
        add_dirs = data.get("additionalDirectories")
        if not isinstance(add_dirs, list):
            add_dirs = []
        known = {"allow", "deny", "ask", "defaultMode",
                 "additionalDirectories", "allowBypassPermissionsMode", "rules"}
        additional = {k: v for k, v in data.items() if k not in known}
        return cls(
            allow_bypass_permissions_mode=bool(data.get("allowBypassPermissionsMode", False)),
            default_mode=data.get("defaultMode"),
            rules=rules,
            additional_directories=[str(d) for d in add_dirs],
            additional=additional,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = dict(self.additional)
        d["allowBypassPermissionsMode"] = self.allow_bypass_permissions_mode
        if self.default_mode is not None:
            d["defaultMode"] = self.default_mode
        if self.rules:
            d["rules"] = dict(self.rules)
        if self.additional_directories:
            d["additionalDirectories"] = list(self.additional_directories)
        return d


@dataclass
class SettingsSchema:
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    permission_mode: str = ""   # 顶层 back-compat 形态保留；空串视为未设置。
    # F-47.1 (2026-06-02) 已删除启动模式 plumb 时的 fallback 读取，
    # 磁盘上残留的 settings.permission_mode 字段在启动时被忽略。
```

#### 加载路径改造

`SettingsSchema.from_dict`（`src/settings/types.py:161-198`）把：

```python
if "permissions" in known and isinstance(known["permissions"], list):
    known["permissions"] = [PermissionRule(**r) if isinstance(r, dict) else r for r in known["permissions"]]
```

替换为：

```python
if "permissions" in known:
    known["permissions"] = PermissionsConfig.from_dict(known["permissions"])
```

dict / list / None 都安全降级到 `PermissionsConfig`，未知 sub-key 进 `additional` 不会丢。

#### 读路径 + 启动模式 plumb

`src/permissions/modes.py` 加私有聚合器：

```python
def _settings_perms(settings) -> dict[str, Any]:
    """聚合所有可识别的 permissions sub-key。

    优先级：
    1. `settings.permissions.additional`（forward-compat bag）
    2. `settings.permissions.to_dict()`（结构化字段）
    3. `settings.extra["permissions"]`（F-47 落地前旧 binary 旁路）
    """
    perms_obj = getattr(settings, "permissions", None)
    if perms_obj is None:
        return {}
    bag: dict[str, Any] = {}
    if hasattr(perms_obj, "additional") and isinstance(perms_obj.additional, dict):
        bag.update(perms_obj.additional)
    if hasattr(perms_obj, "to_dict"):
        try:
            for k, v in perms_obj.to_dict().items():
                bag.setdefault(k, v)
        except Exception:
            pass
    legacy = (getattr(settings, "extra", None) or {}).get("permissions")
    if isinstance(legacy, dict):
        for k, v in legacy.items():
            bag.setdefault(k, v)
    return bag


def has_allow_bypass_permissions_mode() -> bool:
    try:
        from src.settings.settings import get_settings
    except Exception:
        return False
    try:
        settings = get_settings()
    except Exception:
        return False
    return bool(_settings_perms(settings).get("allowBypassPermissionsMode"))
```

`clawcodex_ext/cli/permissions.py` plumb：

```python
from src.settings.settings import get_settings
from src.permissions.modes import (
    has_allow_bypass_permissions_mode,
    initial_permission_mode_from_cli,
    PERMISSION_MODES,
)

def resolve_permission_state(args) -> None:
    dangerously = bool(getattr(args, 'dangerously_skip_permissions', False))
    allow_dangerously = bool(getattr(args, 'allow_dangerously_skip_permissions', False))
    permission_mode_cli = getattr(args, 'permission_mode', None)

    enforce_dangerous_skip_permissions_safety(
        bypass_requested=dangerously or allow_dangerously,
    )

    # F-47: 启动模式 plumb —— 读 permissions.default_mode。
    # F-47.1 (2026-06-02) 已删除"再 fallback 顶层 permission_mode"分支，
    # 磁盘上残留的 settings.permission_mode 字段在启动时被忽略。
    settings_default_mode: str | None = None
    try:
        s = get_settings()
        pc = getattr(s, "permissions", None)
        if pc is not None:
            settings_default_mode = getattr(pc, "default_mode", None) or None

    mode = initial_permission_mode_from_cli(
        permission_mode_cli=permission_mode_cli,
        dangerously_skip_permissions=dangerously,
        settings_default_mode=settings_default_mode,
    )

    is_bypass_available = (
        dangerously
        or allow_dangerously
        or has_allow_bypass_permissions_mode()
    )
    ...
```

#### 校验重写

`src/settings/validation.py` 改写 `permission_mode` / `permissions` 校验段：

```python

# 旧 (32-38 行):
if settings.permission_mode not in VALID_PERMISSION_MODES:
    errors.append(ValidationError(...))

# 新:
effective_default_mode = (
    settings.permissions.default_mode
    if settings.permissions.default_mode
    else (settings.permission_mode or None)
)
if effective_default_mode is not None and effective_default_mode not in VALID_PERMISSION_MODES:
    errors.append(ValidationError(
        field="permissions.defaultMode",
        message=f"Invalid default permission mode: {effective_default_mode!r}",
        value=effective_default_mode,
    ))

# 旧 (97-103 行):
for i, rule in enumerate(settings.permissions):
    if not rule.tool:
        errors.append(ValidationError(
            field=f"permissions[{i}].tool",
            message="Permission rule must have a 'tool' field",
        ))

# 新:
for behavior in ("allow", "deny", "ask"):
    bucket = settings.permissions.rules.get(behavior, [])
    for j, rule_str in enumerate(bucket):
        if not isinstance(rule_str, str) or not rule_str.strip():
            errors.append(ValidationError(
                field=f"permissions.rules.{behavior}[{j}]",
                message="Rule must be a non-empty string",
            ))
```

#### 关键设计决定

1. **`permissions` 改 dict 形态（`PermissionsConfig` dataclass）**：对齐磁盘格式（`updates.py:persist_permission_update` 写 dict）+ TS 上游契约（`modes.py:118-141` docstring 明确 TS 是 dict），消除运行时 + schema + 磁盘三处形态漂移。
2. **强类型 sub-key + `additional` 前向兼容 bag**：已知 sub-key（`allowBypassPermissionsMode` / `defaultMode` / `rules` / `additionalDirectories`）给类型化访问，未知 sub-key 进 `additional` 兜底。新增 sub-key 不需要改 schema。
3. **顶层 `settings.permission_mode` 字段保留为 back-compat 读取通道**：本次不引入一次性 breaking change；F-46 后续阶段会统一 deprecate。空串视为未设置、不触发 `validation.py` enum 校验误报。**F-47.1 (2026-06-02) hotfix：在项目尚未发布的前提下直接删除该通道**（磁盘上没有需要迁移的旧配置），F-46 deprecate 步骤 N/A。`validation.py` 跳过空串校验的规则保留（无副作用，不删以避免引入额外变更面）。
4. **删除 settings 层"假" `PermissionRule` 死代码**：与运行时 `PermissionRule` 同名异构（一个带 `tool/allow/glob/regex/description/source`，一个带 `source/rule_behavior/rule_value`），混淆读者。`grep` 确认唯一引用是 `from_dict:176-179`（本次同时改写），可安全删。
5. **`has_allow_bypass_permissions_mode` 加 `_settings_perms` 聚合器**：保留 `extra["permissions"]` fallback，F-47 落地前的旧 binary 不炸；同时支持过渡期调试（直接写 `extra` 也能读出）。
6. **`PermissionsConfig.rules` 用 `dict[str, list[str]]` 而不是 `list[PermissionRule]`**：与磁盘原样（字符串数组）对齐；`PermissionRule` 字符串解析走运行时现成的 `permissions/rule_parser.py:permission_rule_value_from_string`，不重新引入 dataclass 死代码。
7. **阶段化落地：1→2→3→4→5→6（可选）→7→8→9**：自包含 schema 改造先闭环（Sub-A + Sub-B + Sub-F），读路径 + 校验（Sub-C + Sub-E），启动模式 plumb（Sub-D），可选 setup 改造（Sub-F），最后清死代码（Sub-H）+ 测试（Sub-G）。每步独立可回滚。
8. **不动 runtime `PermissionRule`（`src/permissions/types.py:80-84`）**：那是 `ToolPermissionContext` 实际用的，与 settings 加载无关；F-47 只动 settings 层。

#### 风险与缓解

| 风险 | 缓解 |
|------|------|
| 死代码清理连带引用 | `grep -r "from src.settings.types import PermissionRule" src/ tests/` 确认唯一引用是 `from_dict:176-179`（本次同时改写） |
| pydantic-settings 后端 schema 漂移 | 本期只覆盖 dataclass 后端；F-47.1 单独补 pydantic 路径对齐，TODO 标在 `from_dict` 注释里 |
| 顶层 `permission_mode` 字段 deprecation 风险 | 本次只保留读取、不标 deprecated；F-46 后续阶段统一 deprecate。**F-47.1 (2026-06-02) hotfix 已先一步直接删除读取通道**，deprecation 步骤 N/A。 |
| `extra` 字段语义迁移 | `SettingsSchema.extra` 仍是"未识别 sub-key 的兜底"；F-47 之后 `permissions` 已知 sub-key 不再溢出到 `extra`，但其它未知 sub-key 仍走 `extra`（行为不变） |
| 改动 6 个文件 | 每个文件改动局部，git revert 风险可控；阶段化落地每步可独立 PR |
| F-47 与 F-46 顺序 | 两者不耦合，可独立 PR、并行落地；F-47 落地后 `permissions.defaultMode` 字段自动成为 F-46.0 拆 `audit_log` 后的"启动默认模式"读路径 |
| `validate_settings` 空 `permission_mode` 误报 | 旧默认值 `"default"` 合法；F-47 改成 `permission_mode: str = ""` 后空串跳过校验 |
| `for i, rule in enumerate(settings.permissions)` 旧代码潜在 TypeError | 旧校验段被 `isinstance(..., list)` 短路掩盖；F-47 直接重写为对 `rules` 字典的字符串非空检查，TypeError 不再有触发路径 |

#### 子特性

- **Sub-A** `PermissionsConfig` dataclass 定义（`src/settings/types.py`）
- **Sub-B** `SettingsSchema.from_dict` 加载改造（`src/settings/types.py:161-198`）
- **Sub-C** `has_allow_bypass_permissions_mode` 加 `_settings_perms` 聚合器（`src/permissions/modes.py:113-140`）
- **Sub-D** `resolve_permission_state` plumb（`clawcodex_ext/cli/permissions.py:36-39`）
- **Sub-E** `validate_settings` 重写（`src/settings/validation.py:32-38, 96-103`）
- **Sub-F** `DEFAULT_SETTINGS` 改造（`src/settings/constants.py:12-46`）
- **Sub-G** 单元测试 + e2e（`tests/test_permission_settings_schema.py` + `tests/manual_e2e_f38_permissions.py`）
- **Sub-H** 死代码清理（删除 `src/settings/types.py:13-20` `PermissionRule`）

#### 落地顺序（建议）

1. **Sub-A + Sub-B + Sub-F**（schema 自包含改造）—— 跑现有测试，确认无回归。`PermissionsConfig` 与 dict 互转是自包含的，不会触发其他模块报错。
2. **Sub-C + Sub-E**（读路径 + 校验）—— 读路径加了 fallback，旧 binary 仍能跑；校验移走对 `list[PermissionRule]` 的迭代，dict 形态合法。
3. **Sub-D**（`resolve_permission_state` plumb）—— 启动模式生效。
4. **Sub-F 可选**（`setup_permissions` 签名扩 `default_mode`）—— 不做也不影响当前 bug；F-47.1 后续必做。
5. **Sub-H**（清死代码）—— `grep` 确认无引用后落地。
6. **Sub-G**（7 条 unittest + 1 条 e2e）—— 最后覆盖。

#### 协同与影响

- **F-15**（Shift+Tab cycle）：F-15 实现了 `default→acceptEdits→plan→bypassPermissions→default` cycle；F-47 让 cycle 真正能切到 `bypassPermissions`。
- **F-31**（TUI 权限模式选择器）：TUI 模态对话框消费 `permissions.defaultMode` 字段。
- **F-46** 弱相关：F-46 后续 `interactive` / `default_decision` 字段落地时，`PermissionsConfig` 是天然的承接结构。
- **F-40** 无关：ProgressSink 重构不涉及 settings schema。
- **`docs/new-features-guide.md`**：F-47.1 阶段补"permission settings 配置迁移"章节，给新 schema 形态做用户级解释。**F-47.1 hotfix 后**：旧字段 `settings.permission_mode` 不再做 back-compat 读取，迁移章节需直接建议用户把顶层 `permission_mode` 改成 `permissions.defaultMode`，而不是"两种写法都生效"。

---

### 二十一10 F-34 CLI/TUI Frontend 解耦架构

**状态**: ✅ 已完成 Phase 1-3

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
