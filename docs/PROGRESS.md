# ClawCodex 开发进度跟踪文档

> 文档路径: `docs/PROGRESS.md`
> 基于: `docs/open-source-replacement-progress.md`, `docs/FEATURE_PLAN.md`
> 版本: v1.5
> 更新日期: 2026-05-25
> 上游同步: 68dc3c5 (Phase 11 bridge complete)

---

## 一、任务总览

### 1.1 开源替代组件

| ID | 组件 | 原始实现 | 替代方案 | 代码减少 | 优先级 | 状态 |
|----|------|---------|---------|----------|--------|------|
| R-1 | 配置系统 | 手动 JSON 管理 (~220 行) | Pydantic-settings | ~220 行 | P0 | ✅ 完成 |
| R-2 | Frontmatter 解析 | yaml.safe_load (~80 行) | python-frontmatter | ~80 行 | P1 | ✅ 完成 |
| R-3 | Bash AST 解析器 | 自建 ~1,500 行 | tree-sitter-bash | ~1,400 行 | P0 | ✅ 完成 |
| R-4 | Git 操作 | 6 个 subprocess.run() (~200 行) | GitPython | ~200 行 | P1 | ✅ 完成 |
| R-5 | Hook 系统 | 自建 ~1,200 行 | Pluggy | ~1,000 行 | P1 | ✅ 完成 |
| R-6 | 结构化输出 | json.loads + 手动验证 (~200 行) | Outlines | ~200 行 | P1 | ✅ 完成 |
| R-7 | Provider 层 | 多个 Provider 类 (~1,630 行) | LiteLLM | ~1,430 行 | P0 | 🔄 进行中 |
| R-8 | 工具语义搜索 | 手动实现 (~100 行) | Qdrant | ~100 行 | P2 | ⏳ 待开始 |
| R-9 | 权限规则引擎 | 手动实现 (~150 行) | Casbin | ~150 行 | P2 | ⏳ 待开始 |
| R-10 | 日志系统 | print/logging | structlog | - | P2 | ⏳ 待开始 |

**总计已减少代码**: ~3,100 行
**预计全部完成后减少**: ~4,530+ 行

### 1.2 功能模块开发

| ID | 模块 | 优先级 | 状态 | 备注 |
|----|------|--------|------|------|
| F-1 | Orchestrator 自主模式 | P0 | ✅ 完成 | Symphony 集成 |
| F-2 | Team 成员管理 (Phase-7) | P1 | ⏳ 规划中 | members 数组 |
| F-3 | MCP 协议扩展 | P1 | ✅ 基础完成 | Stdio/HTTP/SSE/WS |
| F-4 | 结构化输出集成 | P2 | 🔄 进行中 | Outlines 适配器已就绪 |
| F-5 | Voice Mode | P3 | ⏳ 待开始 | 对标 CCB |
| F-6 | Computer Use | P3 | ⏳ 待开始 | 对标 CCB |
| F-7 | Remote Control | P2 | ⏳ 待开始 | Docker + WebUI |
| F-8 | ACP/Zed/Cursor 集成 | P3 | ⏳ 待开始 | IDE 集成 |
| F-9 | /goal 命令 | P2 | ⏳ 待开始 | 长时间任务目标管理 |
| F-10 | ExecuteExtraTool 延迟工具系统 | P2 | ⏳ 待开始 | TF-IDF 工具搜索 + 子代理执行 |
| F-11 | sessionStorage 容量限制 | P2 | ⏳ 待开始 | 防止 daemon 会话内存泄漏 |
| F-12 | cacheWarning 容量限制 | P2 | ⏳ 待开始 | 防止 source 类型内存泄漏 |
| F-13 | Agent 记忆作用域隔离 | P1 | 🔄 进行中 | 按需加载不同作用域记忆，部分实现 |
| F-14 | 三层解耦架构（Layer Isolation） | P1 | ✅ 完成 | upstream/capabilities/features 三层分离，零层违规 |
| F-15 | 权限模式切换 (Shift+Tab) | P1 | ✅ 完成 | REPL/LiveStatus 中支持 `default→acceptEdits→plan→bypassPermissions` 循环切换，状态栏显示当前模式 |
| F-16 | Auto 模式 (TRANSCRIPT_CLASSIFIER) | P2 | ⏳ 待开始 | 基于 LLM 的自动权限模式切换，减少交互疲劳 |
| F-17 | 工具系统按需加载（Tool System Extension） | P1 | ✅ 完成 | 四种工具模式（bare/default/clawcodex/all），4 bundle 简化设计，bundle 引用前缀 ":"，与上游解耦 |
| F-18 | CreateAgentTool 动态工具创建 | P2 | 🔄 规划中 | Agent 可根据 CLI/API 规范动态创建工具，Meta Tool 能力，bash/http/python 三种 call_impl 安全限制 |
| F-19 | POS to Agent 转化模式 | P2 | ✅ 完成 | 三层映射（POS→Agent、workflow→Skill、SDK→工具），SDK 解析 + Skill 分组 + Agent 构建 + 持久化 |
| F-20 | Agent 阶段性进度汇报 | P2 | ✅ 完成 | 三组合方案：检查点触发 + ProgressReportTool + ToolContext.tasks 持久化；PhaseComplete 时双重调用 ProgressReportTool + TaskUpdateTool 更新 metadata |
| F-21 | 后台运行 + 恢复同步 | P1 | ✅ 完成 | Ctrl+B 后台化 + TailFollower 实时同步 + SessionWatcher 多终端感知，补丁 0067-0074 |
| F-22 | Cron 系统执行引擎 | P0 | 🔄 进行中 | 工具定义和/loop skill已完成，核心执行引擎(cron_system/)待实现 |
| F-23 | Bridge Phase 8-11 多 Session Daemon | P1 | ✅ 完成 | 多会话桥接器完整实现，bridge_main/repl_bridge/remote_bridge_core/session_runner，Phase 1-11 全部完成 |
| F-24 | Agent Loop Consolidation (Stage 4) | P1 | ✅ 完成 | 删除 agent_loop.py (537 行)，新增 renderers.py (+257) 和 advisor.py (+125)，重构到 src/query/ |
| F-25 | Advisor Token 计数与状态显示 | P2 | ✅ 完成 | max_history 100→2000，Provider token 追踪增强，client-side advisor mode |
| F-26 | Away-Summary（离开摘要） | P2 | 📋 规划中 | ※ 标记 + 浅灰色，终端失焦 5 分钟自动触发，支持 /recap 手动命令 |

---

## F-26: Away-Summary（离开摘要）功能

**状态**: 📋 规划中
**优先级**: P2
**上游版本**: claude-code-best `src/services/awaySummary.ts`, `src/hooks/useAwaySummary.ts`
**参考实现**: claude-code-best `src/commands/recap/`

### 目标

实现离开摘要功能：在一次交互对话完成后，自动总结对话内容并给出总结与下一步的意见，以 ※ 开头显示在终端，字体颜色为浅灰色（dimColor）。

### 上游实现对照

| 上游文件 | 功能 | ClawCodex 映射 |
|----------|------|----------------|
| `src/constants/figures.ts:29` | 定义 `REFERENCE_MARK = '\u203b'` | `src/constants/figures.py` |
| `src/services/awaySummary.ts` | 生成离开摘要 | `src/services/away_summary.py` |
| `src/commands/recap/generateRecap.ts` | 手动 recap 命令 | `src/commands/recap.py` |
| `src/hooks/useAwaySummary.ts` | 终端焦点监控 | `src/hooks/use_away_summary.py` |
| `src/components/messages/SystemTextMessage.tsx:55-64` | 渲染 away_summary | `src/components/messages/system_text.py` |
| `src/types/message.ts` | `SystemAwaySummaryMessage` 类型 | `src/types/message.py` |

### 实现文件清单

| 文件路径 | 优先级 | 状态 | 依赖 |
|---------|--------|------|------|
| `src/constants/figures.py` | P0 | 📋 规划 | 添加 `REFERENCE_MARK = '\u203b'` |
| `src/types/message.py` | P0 | 📋 规划 | 添加 `SystemAwaySummaryMessage` |
| `src/services/away_summary.py` | P0 | 📋 规划 | 小模型调用、摘要生成 |
| `src/hooks/use_away_summary.py` | P0 | 📋 规划 | 终端焦点监控、5 分钟定时器 |
| `src/commands/recap.py` | P1 | 📋 规划 | `/recap`, `/away`, `/catchup` 命令 |
| `src/components/messages/system_text.py` | P1 | 📋 规划 | 渲染 away_summary subtype |

### 核心组件详细说明

#### 1. services/away_summary.py - 摘要生成服务

```python
BLUR_DELAY_MS = 5 * 60_000  # 5 分钟失焦触发

RECAP_PROMPT_ZH = """用户离开后回来了。用中文写 1-3 句话。
先说明用户在做什么（高层目标，不是实现细节），
然后说明下一步具体操作。不要写状态报告或提交总结。"""

async def generate_away_summary(messages: list[Message], signal: AbortSignal) -> str | None:
    """生成离开摘要，返回 None 表示取消或失败"""
    # 1. 取最近 30 条消息
    recent = messages[-30:]
    
    # 2. 调用小模型生成摘要
    model = get_small_fast_model()
    response = await query_model_without_streaming(
        messages=recent + [create_user_message(RECAP_PROMPT_ZH)],
        model=model,
        ...
    )
    
    # 3. 返回摘要文本
    return get_assistant_message_text(response)
```

#### 2. hooks/use_away_summary.py - 焦点监控

```python
def use_away_summary(messages, set_messages, is_loading):
    """监控终端焦点状态，失焦 5 分钟后生成摘要"""
    timer_ref = None
    abort_ref = None
    
    def on_blur_timer_fire():
        """定时器触发：检查条件后生成摘要"""
        if is_loading:
            pending = True  # turn 结束再生成
            return
        if has_summary_since_last_user_turn(messages):
            return  # 已有摘要，不重复生成
        abort_in_flight()
        controller = AbortController()
        abort_ref = controller
        text = await generate_away_summary(messages, controller.signal)
        if text:
            set_messages(prev => [...prev, create_away_summary_message(text)])
    
    def on_focus_change(state):
        """焦点变化处理"""
        if state in ('blurred', 'unknown'):
            timer_ref = set_timeout(on_blur_timer_fire, BLUR_DELAY_MS)
        else:
            clear_timer(timer_ref)
            abort_in_flight()
            pending = False
    
    # 订阅终端焦点变化
    subscribe_terminal_focus(on_focus_change)
```

#### 3. commands/recap.py - 手动 recap 命令

```python
RECAP_COMMAND = {
    "name": "recap",
    "description": "Generate a one-line session recap now",
    "aliases": ["away", "catchup"],
    "execute": async (session) -> CommandResult:
        """手动触发摘要生成"""
        result = await generate_recap(signal)
        return format_recap_result(result)
}
```

### 渲染样式

```python
# components/messages/system_text.py
if message.subtype == "away_summary":
    return Box(
        flex_direction="row",
        children=[
            Box(min_width=2, children=[Text(dim_color=True, children=["※"])]),
            Text(dim_color=True, children=[message.content]),
        ]
    )
```

### 触发条件

| 触发方式 | 条件 | 说明 |
|----------|------|------|
| 自动触发 | 终端失焦 5 分钟 + 无进行中 turn | 主要场景 |
| 手动触发 | `/recap` 或 `/away` 或 `/catchup` | 即时摘要 |

### 里程碑

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | `src/constants/figures.py` - 添加 REFERENCE_MARK | 📋 规划 |
| 2 | `src/types/message.py` - 添加 SystemAwaySummaryMessage | 📋 规划 |
| 3 | `src/services/away_summary.py` - 摘要生成服务 | 📋 规划 |
| 4 | `src/hooks/use_away_summary.py` - 焦点监控 | 📋 规划 |
| 5 | `src/components/messages/system_text.py` - 渲染组件 | 📋 规划 |
| 6 | `src/commands/recap.py` - 手动 recap 命令 | 📋 规划 |
| 7 | 测试覆盖 | 📋 规划 |

---

## F-22: Cron 系统执行引擎

**状态**: 🔄 进行中
**优先级**: P0
**参考实现**: claude-code-best `src/utils/cron*.ts`

### 目标

将 claude-code-best 的生产级别 cron 执行引擎移植到 ClawCodex，实现：
1. 完整 cron 表达式解析（5字段标准语法）
2. 下次执行时间计算（本地时区）
3. 调度器执行引擎（1秒轮询）
4. 任务持久化（`.claude/scheduled_tasks.json`）
5. 分布式锁（防止多进程重复执行）
6. Jitter 抖动算法（避免雷鸣般群体效应）
7. 任务过期机制（周期性任务7天自动删除）

### 当前实现状态

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| Cron 工具定义 | `src/tool_system/tools/cron.py` | ✅ 完成 | CronCreate/CronList/CronDelete 工具，内存存储 |
| /loop Skill | `src/skills/bundled/loop.py` | ✅ 完成 | 4种模式（fixed-prompt/fixed-maintenance/dynamic-prompt/dynamic-maintenance） |
| cron_parser.py | `src/cron_system/cron_parser.py` | ❌ 待实现 | 表达式解析与时间计算 |
| cron_scheduler.py | `src/cron_system/cron_scheduler.py` | ❌ 待实现 | 执行引擎核心 |
| cron_tasks.py | `src/cron_system/cron_tasks.py` | ❌ 待实现 | 任务存储 CRUD |
| cron_tasks_lock.py | `src/cron_system/cron_tasks_lock.py` | ❌ 待实现 | 分布式锁 |
| cron_jitter_config.py | `src/cron_system/cron_jitter_config.py` | ❌ 待实现 | GrowthBook 动态配置 |
| skills.py | `src/cron_system/skills.py` | ❌ 待实现 | /cron-list, /cron-delete 命令 |
| autonomy_runs.py | `src/cron_system/autonomy_runs.py` | ❌ 待实现 | 任务队列集成 |
| build_missed_task_notification | `src/cron_system/missed_task_notification.py` | ❌ 待实现 | 错失任务通知构建函数 |
| growthbook_config.py | `src/cron_system/growthbook_config.py` | ❌ 待实现 | Jitter 参数动态配置 |

### 里程碑

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | cron_parser.py - 表达式解析与时间计算 | ⏳ 待开始 |
| 2 | cron_tasks.py - 任务存储 CRUD | ⏳ 待开始 |
| 3 | cron_tasks_lock.py - 分布式锁 | ⏳ 待开始 |
| 4 | cron_scheduler.py - 执行引擎核心 | ⏳ 待开始 |
| 5 | cron_jitter_config.py - 动态配置 | ⏳ 待开始 |
| 6 | skills.py - CLI 命令 (/cron-list, /cron-delete) | ⏳ 待开始 |
| 7 | autonomy_runs.py - 任务队列集成 | ⏳ 待开始 |
| 8 | 测试覆盖 | ⏳ 待开始 |

---

## F-23: Bridge Phase 8-11 多 Session Daemon 桥接器

**状态**: ✅ 完成
**优先级**: P1
**完成日期**: 2026-05-25
**上游版本**: 68dc3c5 (Phase 11 bridge complete)

### 目标

实现多 Session Daemon 架构，支持远程桥接、REPL 桥接和多会话协调。

### 实现文件清单

| 文件路径 | Phase | 状态 |
|---------|-------|------|
| `src/bridge/__init__.py` | - | ✅ 完成 |
| `src/bridge/bridge_api.py` | Phase 3 | ✅ 完成 |
| `src/bridge/bridge_main.py` | Phase 8 | ✅ 完成 |
| `src/bridge/remote_bridge_core.py` | Phase 5 | ✅ 完成 |
| `src/bridge/session_runner.py` | Phase 4 | ✅ 完成 |
| `src/bridge/repl_bridge.py` | Phase 11 | ✅ 完成 |
| `src/bridge/init_repl_bridge.py` | Phase 11 | ✅ 完成 |
| `src/bridge/messaging.py` | - | ✅ 完成 |
| `src/bridge/types.py` | - | ✅ 完成 |
| `src/bridge/headless_bridge.py` | - | ✅ 完成 |

### 外部依赖

```toml
# pyproject.toml 新增 (如需要)
watchdog = ">=3.0"  # 文件监控
psutil = ">=5.9"     # 进程存活检测
```

### 核心组件详细说明

#### 1. bridge_main.py - 多 Session Daemon 入口 (Phase 8)

多会话轮询守护进程，负责：
- CLI 参数解析 (`--verbose`, `--sandbox`, `--spawn`, `--capacity`, `--permission-mode`, `--name`)
- 多会话容量控制 (capacity gating)
- 会话状态管理 (active_sessions, session_work_ids, completed_work_ids)
- 工作轮询循环 (work poll loop)
- 优雅关闭 (SIGTERM → wait grace → SIGKILL stragglers → deregister)
- SIGINT/SIGTERM 处理器安装

#### 2. remote_bridge_core.py - 远程桥接核心 (Phase 5)

远程桥接实现，支持：
- v2 环境变量驱动配置
- 远程会话生命周期管理
- 跨进程通信

#### 3. session_runner.py - 子 CLI 会话生成 (Phase 4)

子进程管理，实现：
- Child CLI 生成和监控
- 工作目录管理
- 会话超时控制

#### 4. repl_bridge.py - REPL 桥接 (Phase 11)

REPL 集成桥接器，实现：
- REPL 与 Bridge 的消息路由
- 会话状态同步
- TUI 交互支持

---

## F-24: Agent Loop Consolidation (Stage 4)

**状态**: ✅ 完成
**优先级**: P1
**完成日期**: 2026-05-25
**上游版本**: 68dc3c5

### 目标

删除 `agent_loop.py`，重构到 `src/query/` 模块，实现工具执行与 Agent 循环的解耦。

### 核心变更

| 变更 | 说明 | 行数 |
|------|------|------|
| 删除 `agent_loop.py` | 上游原 Agent 循环逻辑移除 | -537 行 |
| 新增 `renderers.py` | 系统 prompt 渲染器 | +257 行 |
| 新增 `advisor.py` | Advisor 工具 | +125 行 |
| 重构到 `src/query/` | 查询引擎解耦 | - |

### 实现文件清单

| 文件路径 | 状态 |
|---------|------|
| `src/tool_system/agent_loop.py` | ✅ 已删除 |
| `src/tool_system/renderers.py` | ✅ 完成 |
| `src/tool_system/tools/advisor.py` | ✅ 完成 |
| `src/query/` | ✅ 重构 |

### renderers.py - 系统 Prompt 渲染器

渲染器负责将系统 prompt 组件组合并格式化：

```python
class SystemPromptRenderer:
    """系统 Prompt 渲染器"""
    def render(self, context: PromptContext) -> str: ...
    def render_capabilities(self, capabilities: list[str]) -> str: ...
    def render_rules(self, rules: list[str]) -> str: ...
```

### advisor.py - Advisor 工具

Advisor 工具提供 Token 计数和状态显示：

```python
class AdvisorTool:
    """Advisor 工具 - 提供 token 计数和状态信息"""
    def get_token_usage(self) -> TokenUsage: ...
    def get_cost_estimate(self) -> CostEstimate: ...
```

---

## F-25: Advisor Token 计数与状态显示

**状态**: ✅ 完成
**优先级**: P2
**完成日期**: 2026-05-25
**上游版本**: 68dc3c5

### 目标

增强 Advisor 的 token 计数显示、client-side advisor mode 和 cost tracker。

### 核心改进

| 改进 | 文件 | 说明 |
|------|------|------|
| Token 计数显示 | `src/agent/conversation.py` | max_history: 100 → 2000 |
| Provider Token 追踪 | `src/providers/anthropic_provider.py` | 增加 token 使用追踪 |
| Base Provider 增强 | `src/providers/base.py` | 统一 token 计数接口 |

### max_history 扩展

`src/agent/conversation.py` 中 `max_history` 从 100 提升到 2000，允许更长的对话历史：

```python
@dataclass
class ConversationConfig:
    max_history: int = 2000  # 从 100 提升到 2000
```

### Provider Token 追踪

```python
@dataclass
class TokenUsage:
    """Token 使用统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    
    def add(self, other: TokenUsage) -> None:
        """累加 token 使用"""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
```

---

## F-21: 后台运行 + 恢复同步

**状态**: ✅ 已完成
**优先级**: P2
**目标**: 在 Agent 编排中阶段性将结果汇报至任务看板，将任务看板提取为工具

#### 背景

在 Agent 编排场景中，需要在阶段性检查点（如 phase/step complete）自动将进度汇报至任务看板。目前项目已有 TaskCreate/TaskGet/TaskList/TaskUpdate/TaskOutput 完整的任务看板工具集（`src/tool_system/tools/tasks_v2.py`），但缺少在 Agent 阶段性检查点自动触发汇报的机制。

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

```python
async def _on_phase_complete(self, session_id: str, phase_result: dict):
    # 自动汇报进度
    await self.tool_registry.dispatch(
        ToolCall(name="ProgressReport", input={
            "taskId": session_id,
            "stage": phase_result["phase"],
            "progress": phase_result["progress"],
            "summary": phase_result["summary"]
        }),
        context
    )
```

#### 数据持久化（方式三）

现有 `TaskUpdateTool` 已支持 `metadata` 字段，ProgressReport 只需扩展 metadata 结构：

```python
{
    "taskId": "abc123",
    "stage": "code_generation",
    "progress": 60,
    "summary": "已完成核心模块代码生成",
    "nextAction": "编写单元测试",
    "metadata": {
        "phases": [
            {"name": "analysis", "completed": true, "progress": 100},
            {"name": "code_generation", "completed": true, "progress": 100},
            {"name": "testing", "completed": false, "progress": 0}
        ],
        "tokenUsage": {"input": 5000, "output": 3000}
    }
}
```

#### 与现有组件的关系

| 现有组件 | 集成点 | 说明 |
|---------|--------|------|
| `tasks_v2.py` | TaskUpdate/TaskCreate | 复用现有工具，通过 metadata 扩展 |
| `StatusDashboard` | 状态展示 | 可消费汇报数据实时展示 |
| `AgentRunner` | 事件流 | PhaseComplete 事件触发汇报 |
| `ToolContext.tasks` | 存储后端 | 已有实现，无需修改 |

#### 实施阶段

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase A | ProgressReportTool 工具实现 | P2 | ✅ 已完成 |
| Phase B | AgentRunner PhaseComplete 事件 | P2 | ✅ 已完成 |
| Phase C | ProgressReporter 汇报处理器 | P2 | ✅ 已完成 |
| Phase D | 与 StatusDashboard 集成 | P3 | ⏳ 待开始 |

**状态**: 🔄 规划中
**优先级**: P2
**目标**: 跨会话统计所有 Agent 的工具和 Skill 调用频率、耗时和错误率

#### 背景

当前项目没有调用统计功能，无法了解工具和 Skill 使用分布情况。本特性通过追加日志（JSON Lines）实现轻量级跨会话持久化，不支持实时查询。

#### 实现方案

**日志格式（Append-only JSON Lines，统一记录工具和 Skill）**:

```
~/.clawcodex/tool_stats.jsonl
{"agent_id": "dev", "kind": "tool", "tool": "Read", "ts": 1748..., "dur_ms": 12.3, "ok": true}
{"agent_id": "dev", "kind": "tool", "tool": "Edit", "ts": 1748..., "dur_ms": 45.1, "ok": true}
{"agent_id": "dev", "kind": "skill", "skill": "code_review", "ts": 1748..., "dur_ms": 3200.0, "ok": true}
{"agent_id": "orchestrator-001", "kind": "tool", "tool": "Bash", "ts": 1748..., "dur_ms": 2300.0, "ok": false, "error": "timeout"}
{"agent_id": "orchestrator-001", "kind": "skill", "skill": "git_commit", "ts": 1748..., "dur_ms": 800.0, "ok": true}
```

**日志字段（统一 schema，工具和 Skill 共用）**:

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

**性能分析**:

| 操作 | 性能影响 | 说明 |
|------|---------|------|
| 追加写入 | 极小 | 顺序追加是磁盘 I/O 最优模式，现代文件系统高度优化 |
| 文件过大后查询 | 较大 | 每次 grep/jq 需全量扫描，数据量大时需预聚合 |
| 多进程并发写 | 中等 | OS 层写锁竞争，建议单进程内汇聚后批量写入 |

**架构设计**:

```
src/tool_system/
└── stats.py                    # 统计模块（新）
    ├── record(name, dur_ms, ok, error, *, kind, params, version)  # 统一记录
    ├── get_stats()             # 查询汇总（读取日志文件聚合）
    └── _write_buffered()       # 批量写入（缓冲后刷新到磁盘）

注入点:
  agent_loop.py                 # 工具执行完成后调用 record(kind="tool")
  skills/loader.py             # Skill 执行完成后调用 record(kind="skill")
```

**查询示例**:

```bash
# 统计所有 skill 调用
grep '"kind":"skill"' ~/.clawcodex/tool_stats.jsonl | jq '.skill' | sort | uniq -c | sort -rn

# 统计工具 vs skill 调用比例
grep -E '"kind":"(tool|skill)"' ~/.clawcodex/tool_stats.jsonl | jq -s 'group_by(.kind) | map({kind: .[0].kind, count: length})'

# 统计某个 agent 的调用
grep '"agent_id":"orchestrator-001"' ~/.clawcodex/tool_stats.jsonl | jq -s 'group_by(.kind) | map({kind: .[0].kind, count: length, avg_ms: (map(.dur_ms) | add / length)})'

# 统计错误率
grep '"kind":"skill"' ~/.clawcodex/tool_stats.jsonl | jq -s 'map({ok}) | group_by(.ok) | map({ok: .[0].ok, count: length})'
```

**数据清理**: 日志文件需定期归档或设置 TTL（建议保留最近 90 天数据）。

**不支持的功能**: 暂不支持实时查询（如 TUI 页面每次渲染时查询）。如需实时展示，需另建汇总表预聚合。

#### 替代方案：基于 Transcript 的轻量级统计

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
```

**优缺点对比**:

| 方案 | 优势 | 劣势 |
|------|------|------|
| **Transcript 方案** | 无需新增日志写入；已有数据 | 无耗时；Skill 覆盖不确定；解析稍复杂 |
| **JSON Lines 日志方案** | 包含耗时；字段完整；格式统一 | 需新增写入逻辑；数据冗余 |

**决策建议**:
- 仅需调用频率/成功率 → 用 Transcript 方案
- 需耗时统计 → 用 JSON Lines 日志方案

#### 基于使用频率的工具/Skill 裁剪

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

#### POS to Agent 转化模式

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

#### 业务 Agent 长期使用（新窗口重连）

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

### F-18: CreateAgentTool 动态工具创建

---

### F-15: 权限模式切换 (Shift+Tab)

**状态**: ✅ 完成
**完成日期**: 2026-05-21
**优先级**: P1

#### 背景

Claude Code 支持在多种权限模式之间切换（default / acceptEdits / plan / auto / bypassPermissions），但 clawcodex 原先只有代码实现，UI 绑定缺失。

#### 实现方案

1. **REPL 空闲状态切换** (`src/repl/core.py`)

   - 在 keybindings 中添加 `Shift+Tab` 绑定
   - 调用 `cycle_permission_mode()` 循环切换权限模式
   - 循环顺序：`default → acceptEdits → plan → bypassPermissions (如果可用) → default`

2. **对话过程中切换** (`src/repl/live_status.py`)

   - 在 LiveStatus 的 keybindings 中添加 `Shift+Tab` 处理器
   - 通过 `on_submit.__self__` 获取 REPL 实例来访问权限状态
   - 切换后更新 spinner 状态显示 `[mode: {next_mode}]`

3. **状态栏显示** (`src/repl/core.py:_bottom_toolbar`)

   - 在底部状态栏显示当前权限模式
   - 格式：`{provider} · {model} · {cwd} · mode: {perm_mode} · turns: X · tokens: X in / X out`

#### 完成的工作

- [x] REPL Shift+Tab 权限切换绑定
- [x] LiveStatus Shift+Tab 权限切换绑定
- [x] 底部状态栏显示当前权限模式
- [x] 补丁文件更新 (`0054.src.repl.core.py.patch`, `0066.src.repl.live_status.py.patch`)

#### 关键文件

- `src/repl/core.py` - REPL 空闲状态 Shift+Tab + 状态栏
- `src/repl/live_status.py` - 对话过程中 Shift+Tab
- `src/permissions/cycle.py` - `cycle_permission_mode()` 实现
- `src/permissions/modes.py` - `permission_mode_short_title()` 等工具函数

#### 注意

- `bypassPermissions` 需要通过 `--dangerously-skip-permissions` 启动或 `settings.json` 中配置 `permissions.allowBypassPermissionsMode: true` 才可用
- `auto` 模式不在手动循环中，需要通过 `--permission-mode auto` 启动或由 TRANSCRIPT_CLASSIFIER 自动触发

---

### F-16: Auto 模式 (TRANSCRIPT_CLASSIFIER)

**状态**: ⏳ 待实现
**优先级**: P2
**目标**: 基于 LLM 的自动权限模式切换，减少交互疲劳

#### 功能说明

Auto 模式是一种智能权限模式，通过 LLM 分类器（TRANSCRIPT_CLASSIFIER）自动判断何时允许执行敏感操作。在长时间任务或重复性操作场景下，Auto 模式可以减少用户确认的交互频率。

#### 工作原理

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
```

#### 待实现组件

| 组件 | 文件 | 说明 |
|------|------|------|
| TRANSCRIPT_CLASSIFIER | `permissions/classifier.py` | LLM 分类器核心 |
| canCycleToAuto | `permissions/cycle.py` | 判断是否可切换到 auto |
| Auto Mode 集成 | `agent/run_agent.py` | 在工具执行前调用分类器 |
| 分类结果缓存 | `permissions/cache.py` | 避免重复分类 |

#### 实施阶段

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase A1 | TRANSCRIPT_CLASSIFIER 核心实现 | P2 | ⏳ 待开始 |
| Phase A2 | `canCycleToAuto()` 判断逻辑 | P2 | ⏳ 待开始 |
| Phase A3 | Auto Mode 工具执行前集成 | P2 | ⏳ 待开始 |
| Phase A4 | 分类结果缓存机制 | P3 | ⏳ 待开始 |

---

### F-20: Agent 间自主观察与消息交互

**状态**: 🔄 开发中
**优先级**: P1
**目标**: 实现 Manager Agent 全自动观察 Worker Agent 状态并注入指令，支持优先级队列和权限审批

#### 背景

当前 ClawCodex 的 agent 之交互依赖人类的介入（操作员通过 CLI inject hint）。需要设计一套全自动的 agent-to-agent 状态观察与消息注入机制：

- Manager Agent 通过工具主动查询 Worker Agent 状态
- Manager 根据状态通过带优先级的消息注入机制向 Worker 发送修正指令
- Worker 在下一 turn 边界以 UserMessage 形式接收并执行

#### 角色定义

| 角色 | 判断标准 | 说明 |
|------|---------|------|
| **Manager Agent** | 工具集中包含 `TaskInspect` + `TaskDirectives` | 通过工具组合自动识别，无需独立 Agent 类型 |
| **Worker Agent** | 不包含上述管理工具 | 普通执行单元 |

#### 核心工具

| 工具 | 文件 | 功能 |
|------|------|------|
| `TaskInspect` | `src/tool_system/tools/task_inspect.py`（新增） | Manager 查询 Worker 运行时状态 |
| `TaskDirectives` | `src/tool_system/tools/task_directives.py`（新增） | Manager 向 Worker 注入优先级指令 |
| `ReportToSupervisor` | `src/tool_system/tools/report_to_supervisor.py`（新增） | Worker 可选自愿上报 |

#### 优先级处理

| 优先级 | 队列位置 | 用途 |
|--------|---------|------|
| `critical` | 队列头部，最先消费 | 紧急修正，worker 必须响应 |
| `high` | 队列头部 | 重要建议，worker 应优先处理 |
| `normal` | 队列尾部，FIFO | 普通协调信息 |

#### 权限方案

| 场景 | Worker 模式 | Manager 职责 |
|------|-------------|-------------|
| 测试/开发 | `bypassPermissions` | 无需审批 |
| 受控环境 | `bubble` + `always_allow_rules` | 规则外的工具弹窗给人类 |
| 生产/高风险 | `plan` | Manager 实时审批关键操作 |

#### 关键文件

| 文件 | 说明 |
|------|------|
| `src/tool_system/tools/task_inspect.py` | 新增，状态查看工具 |
| `src/tool_system/tools/task_directives.py` | 新增，消息注入工具 |
| `src/tasks/local_agent.py` | 修改，`queue_pending_message` 支持 priority |
| `src/query/query.py` | 修改，`drain_pending_messages` 按优先级消费 |
| `src/agent/agent_tool_utils.py` | 修改，过滤管理工具（仅 Manager 可用） |

#### 实施阶段

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase M1 | `TaskInspect` + `TaskDirectives` 核心工具 | P1 | ✅ 完成 |
| Phase M2 | `queue_pending_message` 支持 priority | P1 | ✅ 完成 |
| Phase M3 | `drain_pending_messages` 按优先级消费 | P1 | ✅ 完成 |
| Phase M4 | 工具可见性过滤（仅 Manager 可调用） | P1 | ✅ 完成 |
| Phase M5 | 权限规则传递（`always_allow_rules` + `worker_permission_mode`） | P1 | ✅ 完成 |
| Phase M6 | 测试与联调 | P2 | 🔄 进行中 |

---

### F-18: CreateAgentTool 动态工具创建

**状态**: 🔄 规划中
**优先级**: P2
**目标**: Agent 可根据三方 CLI/API 规范动态创建工具，实现"工具创建工具"的 Meta Tool 能力

#### 功能说明

允许 Agent 分析第三方工具（CLI 命令或 HTTP API）的接口规范，然后动态创建一个可用的工具：

```
Agent 分析 CLI 规范 → 生成工具规范 → 调用 CreateAgentTool → 注册新工具 → 使用新工具
```

#### 架构设计

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

#### 三种 call_impl 安全限制

| call_type | call_impl 示例 | 安全级别 |
|-----------|---------------|---------|
| `bash` | `"git status --porcelain {path}"` | ✅ 占位符防注入，预定义命令白名单 |
| `http` | `{"method": "GET", "url": "https://api.github.com/{endpoint}"}` | ✅ 模板化，方法白名单 |
| `python` | `"fetch_data"` → 映射到预定义函数 | ⚠️ 仅白名单函数注册 |

**命令白名单（bash）**：`git`, `gh`, `glab`, `curl`, `wget`, `kubectl`, `docker`, `npm`, `pip`

**HTTP 方法白名单**：`GET`, `POST`, `PUT`, `DELETE`, `PATCH`

#### 安全性约束

| 约束类型 | 说明 |
|---------|------|
| 命令白名单 | 仅允许预定义命令 |
| HTTP 方法白名单 | 仅白名单方法 |
| Python 函数注册 | 仅白名单函数 |
| 无任意代码执行 | call_impl 是模板/映射，非代码 |
| 参数化防注入 | format 替换，无 shell 注入 |
| 超时保护 | subprocess timeout=30 |

#### 实现文件

| 文件 | 位置 |
|------|------|
| `tool_authoring/spec.py` | `src/agent/tool_authoring/` |
| `tool_authoring/validators.py` | `src/agent/tool_authoring/` |
| `tool_authoring/call_handlers/bash.py` | `src/agent/tool_authoring/` |
| `tool_authoring/call_handlers/http.py` | `src/agent/tool_authoring/` |
| `tool_authoring/factory.py` | `src/agent/tool_authoring/` |
| `tool_authoring/registry_ext.py` | `src/agent/tool_authoring/` |
| `tool_authoring/persistence.py` | `src/agent/tool_authoring/` |
| `create_agent_tool.py` | `src/tool_system/tools/` |

#### 已有基础 (extensions/pos_converter/)

`extensions/pos_converter/` 目录已实现 POS → Agent 转换框架：

| 文件 | 功能 |
|------|------|
| `agent_builder.py` | Agent 构建器 |
| `convert_pos_skill.py` | POS 转换 Skill |
| `sdk_parser.py` | SDK 解析器 |
| `skill_grouper.py` | Skill 分组器 |
| `templates.py` | 模板定义 |

---

## 二、已完成任务详情

### F-14: 三层解耦架构（Layer Isolation）

**状态**: ✅ 完成
**完成日期**: 2026-05-20
**优先级**: P1

#### 背景

`src/api/query.py` 在 `stream()` 方法内部直接导入 `src.entrypoints.headless` 和 `src.tool_system.agent_loop`，这违反了 layer isolation 约束——features 层（api）不能直接依赖 upstream。

#### 实现方案

```
src/api/query.py
    ↓ imports（在 stream() 方法内，运行时）
src/capabilities/headless_runner.py  ← 函数内部懒加载，不在模块路径上
    ↓                                   （upstream import 在 headless_runner.py 函数内部）
src/entrypoints/headless.py          ← 上游模块（运行时才加载）
```

#### 解耦实现

1. **新增 `src/capabilities/event_protocol.py`** — 定义 `ToolEventProtocol`，从 `src/tool_system/agent_loop.ToolEvent` 提取接口契约
2. **新增 `src/capabilities/headless_protocol.py`** — 定义 `HeadlessOptionsProtocol` 和 `HeadlessRunnerProtocol`
3. **新增 `src/capabilities/headless_runner.py`** — `HeadlessSessionOptions` + `run_headless_session()` 函数，通过环境变量 `CLAW_HEADLESS_BACKEND=stub` 可在测试中完全绕过上游
4. **更新 `src/api/query.py`** — 类型标注改用 Protocol 接口，运行时通过 `run_headless_session()` 分发，不再直接引用上游

#### upstream-sync 配置更新

- `src/api` 加入 `features` 层（与 `orchestrator` 同层）
- `upstream-sync audit` 验证：**零层违规**

#### 关键文件

- `src/capabilities/event_protocol.py` - ToolEvent 接口协议
- `src/capabilities/headless_protocol.py` - HeadlessOptions / HeadlessRunner 接口协议
- `src/capabilities/headless_runner.py` - 可插拔后端分发器
- `src/api/query.py` - 运行时零上游耦合
- `upstream-sync.yaml` - `src/api` 加入 features 层

#### 解耦结果

| 组件 | 上游直接引用 | 运行时耦合 |
|------|------------|-----------|
| `src/orchestrator/` | ❌ 无 | ✅ 通过 headless_runner 间接 |
| `src/api/query.py` | ❌ 无 | ✅ 通过 headless_runner 间接 |
| `src/api/orchestration.py` | ❌ 无 | ✅ 只调用 orchestrator 内部 |
| `src/capabilities/` | ❌ 无 | ✅ 只定义 Protocol，无实现 |

#### 测试验证

```
tests/test_layer_isolation.py: 12/12 通过
upstream-sync audit: 零层违规
CLAW_HEADLESS_BACKEND=stub: 不加载任何上游模块
```

---

### R-1: Pydantic-settings 替换配置系统

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
当前 `src/config.py` 使用手动 JSON 配置管理，包括三层配置层级 (global > project > local) 和手动深合并实现 (`_deep_merge`)。

#### 实现方案
引入 `pydantic-settings` 作为配置底层引擎，保留现有 ConfigManager API。

#### 解耦设计
```
src/config.py (保留 ConfigManager 接口)
    ↓
src/settings/pydantic_adapter.py (新 Pydantic Settings 适配器)
    ↓
pydantic-settings + python-dotenv (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/settings/pydantic_adapter.py` 适配器模块
- [x] 定义 `ClawCodexSettings(BaseSettings)` 类
- [x] 实现 `load_settings_from_config_manager()` 桥接函数
- [x] 添加 `pydantic-settings>=2.0.0` 到 pyproject.toml 依赖
- [x] 创建测试文件 `tests/test_pydantic_adapter.py`
- [x] 所有测试通过 (9 个新测试 + 14 个原配置测试 + 24 个设置测试)

#### 关键文件
- `src/settings/pydantic_adapter.py` - Pydantic Settings 适配器
- `tests/test_pydantic_adapter.py` - 适配器测试

#### 问题与解决方案
(无)

---

### R-2: python-frontmatter 替换 frontmatter 解析

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
当前 `src/skills/frontmatter.py` 使用 `yaml.safe_load` 手动解析 frontmatter。

#### 实现方案
使用 `python-frontmatter` 库替换手动解析逻辑。

#### 解耦设计
```
src/skills/frontmatter.py (保留 parse_frontmatter 接口)
    ↓
src/skills/_frontmatter_adapter.py (适配器层)
    ↓
python-frontmatter (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/skills/_frontmatter_adapter.py` 适配器模块
- [x] 实现 `parse_frontmatter_with_library()` 函数
- [x] 添加 `python-frontmatter>=1.0.0` 到 pyproject.toml 依赖
- [x] 创建测试文件 `tests/test_frontmatter_adapter.py`
- [x] 所有测试通过 (9 个测试)

#### 关键文件
- `src/skills/_frontmatter_adapter.py` - python-frontmatter 适配器
- `tests/test_frontmatter_adapter.py` - 适配器测试

---

### R-3: tree-sitter-bash 替换 Bash AST 解析器

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
`src/permissions/bash_parser/` 包含 ~1,500 行自建 Bash AST 解析器。

#### 实现方案
使用 `tree-sitter-bash` 替换自建解析器（最初使用 bashlex，但 bashlex 采用 GPL v3+ 许可证与 ClawCodex 的 MIT 许可证不兼容）。

#### 解耦设计
```
src/permissions/bash_parser/ (保留原有公共接口)
    ↓
src/permissions/_treesitter_adapter.py (适配器层)
    ↓
tree-sitter + tree-sitter-bash (开源依赖, MIT 许可证)
```

#### 完成的工作
- [x] 创建 `src/permissions/_treesitter_adapter.py` 适配器模块
- [x] 实现 `parse_command_with_bashlex()` 和 `classify_command_with_bashlex()` 函数
- [x] 添加 `tree-sitter>=0.25.0` 和 `tree-sitter-bash>=0.25.0` 到 pyproject.toml 依赖
- [x] 移除 bashlex 依赖 (GPL v3+ 许可证不兼容)
- [x] 所有测试通过 (16 个测试)

#### 关键文件
- `src/permissions/_treesitter_adapter.py` - tree-sitter-bash 适配器
- `tests/test_treesitter_adapter.py` - 适配器测试

#### 问题与解决方案
- **bashlex 许可证问题**: bashlex 使用 GPL v3+ 许可证，与 ClawCodex 的 MIT 许可证不兼容
- **解决方案**: 替换为 tree-sitter-bash，采用 MIT 许可证，完全兼容

---

### R-4: GitPython 替换 Git 子进程调用

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
`src/context_system/git_context.py` 使用 6 个并发 `subprocess.run()` 调用 Git 命令。

#### 实现方案
使用 `GitPython` 替换子进程调用。

#### 解耦设计
```
src/context_system/git_context.py (保留原有接口)
    ↓
src/context_system/_gitpython_adapter.py (适配器层)
    ↓
GitPython (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/context_system/_gitpython_adapter.py` 适配器模块
- [x] 实现 `GitPythonProvider` 类和 `collect_git_context_with_gitpython()` 函数
- [x] 添加 `GitPython>=3.1.0` 到 pyproject.toml 依赖
- [x] 创建测试文件 `tests/test_gitpython_adapter.py`
- [x] 所有测试通过 (9 个测试)

#### 关键文件
- `src/context_system/_gitpython_adapter.py` - GitPython 适配器
- `tests/test_gitpython_adapter.py` - 适配器测试

---

### R-5: Pluggy 替换 Hook 系统

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
`src/hooks/` 包含 ~1,200 行自建 Hook 系统（28 个事件）。

#### 实现方案
使用 `Pluggy` 替换自建 Hook 系统。

#### 解耦设计
```
src/hooks/ (保留原有 Hook 接口)
    ↓
src/hooks/_pluggy_adapter.py (适配器层)
    ↓
pluggy (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/hooks/_pluggy_adapter.py` 适配器模块
- [x] 实现 ClawCodexHooks 规范定义
- [x] 实现 HookManager 类
- [x] 所有测试通过

#### 关键文件
- `src/hooks/_pluggy_adapter.py` - Pluggy 适配器

---

### R-6: Outlines 引入结构化输出

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
项目中散落多处 `json.loads` + 手动验证代码。

#### 实现方案
引入 `Outlines` 用于结构化输出（预生成约束，非后验证）。

#### 解耦设计
```
src/agent/ (使用结构化输出的模块)
    ↓
src/agent/_outlines_adapter.py (适配器层)
    ↓
Outlines (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/agent/_outlines_adapter.py` 适配器模块
- [x] 实现 `ToolCallDecision` 等结构化模型
- [x] 添加 `outlines` 到 pyproject.toml 依赖
- [x] 适配器就绪，待集成到各模块

#### 关键文件
- `src/agent/_outlines_adapter.py` - Outlines 适配器

---

## 三、进行中任务

### F-13: Agent 记忆作用域隔离

**状态**: 🔄 进行中
**优先级**: P1
**规划日期**: 2026-05-19

#### 背景
在多 Agent 协作场景下，不同 Agent 可能需要访问不同范围的信息。传统的记忆系统是单例模式，所有 Agent 共享相同的记忆目录，无法满足按需隔离的需求。

#### 实现方案
在 `AgentDefinition` 中添加 `memory` 字段，支持指定 Agent 可访问的记忆作用域。核心 API `load_memory_prompts()` 支持传入作用域列表按需加载。

#### 支持的作用域
| 作用域 | 说明 |
|--------|------|
| `user` | 用户/私有记忆 |
| `project` | 项目上下文记忆 |
| `reference` | 外部系统指针 |
| `team` | 团队共享记忆 |
| `local` | 会话级本地记忆 |

#### 待完成的工作
- [x] 添加 `load_memory_prompts()` 函数到 `memdir/memdir.py`
- [x] 添加 `_load_memory_prompt_for_scope()` 和 `_get_memory_path_for_scope()` 辅助函数
- [x] 导出 `load_memory_prompts` 到 `memdir/__init__.py`
- [ ] 更新 `build_full_system_prompt()` 支持 `memory_scopes` 参数
- [ ] 更新 `build_full_system_prompt_blocks()` 支持 `memory_scopes` 参数
- [ ] 更新 `_build_memory_section()` 接受 `memory_scopes` 参数
- [x] 保持 `load_memory_prompt()` 向后兼容

#### 关键文件（待创建/修改）
- `src/memdir/memdir.py` - 核心 `load_memory_prompts()` 实现
- `src/memdir/memory_types.py` - 四种记忆类型定义
- `src/memdir/paths.py` - 记忆目录路径解析
- `src/memdir/team_mem_paths.py` - 团队记忆路径
- `src/memdir/team_mem_prompts.py` - 团队记忆 prompt 构建
- `src/context_system/prompt_assembly.py` - 支持 `memory_scopes` 参数

#### API 使用方式（设计）
```python
# 按需加载特定作用域的记忆
memory_prompts = load_memory_prompts(['user', 'team'])

# 在 build_full_system_prompt 中使用
prompt = build_full_system_prompt(
    memory_scopes=['user', 'project'],  # Agent 按需指定
    ...
)
```

#### 问题与解决方案
(待实现)

---

### R-7: LiteLLM 替换 Provider 层

**状态**: 🔄 进行中
**优先级**: P0
**预计减少代码**: ~1,430 行

#### 背景
当前 `src/providers/` 包含多个 Provider 实现 (~1,630 行)。

#### 实现方案
使用 `LiteLLM` 统一 Provider 层，支持 100+ 模型。

#### 解耦设计
```
src/providers/base.py (保留 BaseProvider 抽象)
    ↓
src/providers/_litellm_adapter.py (LiteLLM 适配器)
    ↓
LiteLLM (开源依赖)
```

#### 进度
- [x] `src/providers/_litellm_adapter.py` 适配器文件已创建
- [x] 实现 `LiteLLMProvider` 类
- [ ] 集成到 Provider 注册系统
- [ ] 移除硬编码的 anthropic/openai/zhipuai 必装依赖
- [ ] 端到端测试

#### 注意事项
- LiteLLM 保留 `BaseProvider` 接口可回退
- Anthropic SDK 是可选依赖，仅在调用 Anthropic 模型时需要
- 当前 pyproject.toml 硬编码 `anthropic`、`openai`、`zhipuai` 三个 SDK

---

### F-1: Orchestrator 自主模式

**状态**: ✅ 完成
**完成日期**: 2026-05-20
**优先级**: P0

#### 目标
支持 `clawcodex --workflow WORKFLOW.md` 自主运行模式

#### 组件进度

| 组件 | 文件 | 状态 |
|------|------|------|
| Orchestrator | `orchestrator/orchestrator.py` | ✅ 完成 |
| WorkspaceManager | `orchestrator/workspace.py` | ✅ 完成 |
| LinearAdapter | `orchestrator/linear/adapter.py` | ✅ 完成 |
| LinearClient | `orchestrator/linear/client.py` | ✅ 完成 |
| Issue | `orchestrator/linear/issue.py` | ✅ 完成 |
| AgentRunner | `orchestrator/agent_runner.py` | ✅ 完成 |
| PromptBuilder | `orchestrator/prompt_builder.py` | ✅ 完成 |
| WorkflowLoader | `orchestrator/workflow.py` | ✅ 完成 |
| ApprovalPolicy | `orchestrator/approval_policy.py` | ✅ 完成 |
| StatusDashboard | `orchestrator/status_dashboard.py` | ✅ 完成 |
| TrackerAdapter | `orchestrator/tracker.py` | ✅ 完成 |
| GitSyncService | `orchestrator/git_sync.py` | ✅ 完成 |
| GitHub/Gitee/GitCode Adapter | `orchestrator/repo_tracker/adapter.py` | ✅ 完成 |
| Repository Issue Client | `orchestrator/repo_tracker/client.py` | ✅ 完成 |
| **重试上限保护** | `orchestrator/orchestrator.py` | ✅ 完成 |
| **Issue State 前置检查** | `orchestrator/orchestrator.py` | ✅ 完成 |
| **ClarificationQueue** | `orchestrator/clarification_queue.py` | ✅ 完成 |
| **TrackerAdapter 评论接口** | `orchestrator/tracker.py` + `repo_tracker/adapter.py` | ✅ 完成 |
| **Orchestrator CLI** | `orchestrator/cli/` | 🔄 进行中 |

#### 待完成

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 多 Tracker 支持 | ✅ 已完成 | GitHub/Gitee/GitCode REST 适配器已实现并通过实际测试（GitCode live test 完成 PR 创建） |
| 重试队列 + 退避 | ✅ 已完成 | 失败任务自动重试，指数退避机制已实现 |
| CLI 集成 | ✅ 已完成 | `--workflow` flag 已集成到 cli.py |
| **重试上限保护** | ✅ 已完成 | `_schedule_retry` 增加最大重试次数限制（建议默认 5 次），超过后停止自动重试 |
| **Issue State 前置检查** | ✅ 已完成 | `_launch_issue` 前调用 `tracker.fetch_issue_states_by_ids` 确认 issue 仍处于 active state，非 active 则跳过 |
| **已有 PR 跳过后续处理** | ✅ 已完成 | `_launch_issue` 前调用 `tracker.find_pull_request`，若存在已关联 PR 则标记 completed 并跳过 |
| **ClarificationQueue 文件队列** | ✅ 已完成 | `orchestrator/clarification_queue.py:ClarificationQueue` |
| **TrackerAdapter 评论接口** | ✅ 已完成 | `fetch_issue_comments()` / `fetch_new_comments_since()` / `create_clarification_comment()` |
| **Orchestrator CLI 统一入口** | 🔄 进行中 | `clawcodex orchestrator run/status/issues/clarify/pause/resume/stop/takeover` |
| **Issue Clarification 三通道** | 🔄 进行中 | Dashboard → ClarificationQueue → @mention 作者（Phase A-E） |
| **Orchestrator CLI 生命周期控制** | 🔄 进行中 | pause/resume/stop/takeover + `_process_control_commands()` |

#### Phase 3 生产强化详细设计

**F-1.1: 重试上限保护**

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_schedule_retry` |
| 新增字段 | `workflow.agent.max_retry_attempts: int = 5`（默认值 5） |
| 触发条件 | `attempt > max_retry_attempts` 时跳过调度，打印 warning 并从 `claimed` 集合移除 |
| 副作用 | 不写入 `completed`（需人工确认后手动关闭 issue） |

**F-1.2: Issue State 前置检查**

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_launch_issue`（创建 workspace 后、agent 运行前） |
| 检查方式 | 调用 `tracker.fetch_issue_states_by_ids([issue.id])`，若 state 不在 `active_states` 则跳过 |
| 副作用 | 从 `claimed` 集合移除，不进入 `completed` 集合 |

**F-1.3: 已有 PR 跳过后续处理**

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_launch_issue`（Issue State 检查之后） |
| 检查方式 | 调用 `tracker.find_pull_request(head_branch, base_branch)`，若存在 PR 则跳过 |
| 适用范围 | 仅 RepositoryTrackerAdapter（GitHub/Gitee/GitCode）；Linear 无 PR 概念，返回 None |
| 副作用 | 从 `claimed` 移除，写入 `completed`（重启后不重复处理） |

**F-1.4: 本地 Issue 注册表（持久化映射）**

| 项 | 值 |
|---|---|
| 文件位置 | `{workspace.root}/.clawcodex_issue_registry.json` |
| 实现文件 | `orchestrator/issue_registry.py:IssueRegistry` |
| 记录字段 | `issue_id / identifier / branch_name / commit_sha / pr_number / pr_url / status / attempt_count` |
| Status 枚举 | `PENDING → SYNCED → COMPLETED / FAILED / ABANDONED` |
| 启动时检查 | `_poll_and_dispatch` 遍历候选 issue 时跳过 `is_completed` 或 `has_pr` 的记录 |
| 注册时机 | `_launch_issue` workspace 创建后立即写入 PENDING |
| 更新时机 | `git_sync.sync()` 后写入 SYNCED + PR 信息；session 完成后写入 COMPLETED |
| Abandoned 时机 | 重试达到上限后标记为 ABANDONED |

#### 实施阶段

- [x] **Phase 1: Foundation (Week 1-2)** - 基础框架
- [x] **Phase 2: Agent Integration (Week 3-4)** - Agent 集成 + GitHub/Gitee/GitCode 支持
- [ ] **Phase 3: Production Hardening (Week 5-6)** - 重试上限保护 + Issue State 前置检查 + 冲突恢复
- [ ] **Phase 4: Issue Clarification (Week 7-8)** - 语义澄清流程
- [ ] **Phase 5: Observability (Week 9-10)** - 可观测性

#### Phase 4: Issue Clarification 详细设计

**F-1.5: Issue 语义澄清流程（三通道优先机制）**

| 项 | 值 |
|---|---|
| 核心思路 | **三通道优先机制**：本地操作员优先（Dashboard / ClarificationQueue），@mention 作者兜底 |
| 通道一 | StatusDashboard 交互提示（非 headless，操作员在线时即时响应） |
| 通道二 | ClarificationQueue 文件队列（~/.clawcodex/clarification_queue.json，操作员异步 CLI 应答） |
| 通道三 | @mention Issue 评论（操作员无响应后降级，完全异步等待作者回复） |
| 触发条件 | Agent 检测到 Issue 语义模糊，调用 AskIssueAuthor(question, context) 工具 |
| 降级时机 | 通道一 timeout（无 Dashboard 或 headless）→ 通道二 timeout（30min）→ 通道三（72h）→ escalation |
| 次数限制 | max_questions_per_issue（默认 3 次），超过后标记 EXHAUSTED |
| 状态机 | NONE → AWAITING_LOCAL → AWAITING_AUTHOR → RESOLVED / TIMED_OUT / EXHAUSTED |
| 持久化 | ClarificationQueue 文件 + IssueRegistry clarification_status + question_history |
| 平台约束 | GitHub/Gitee/GitCode 均无 DM/私信 API，外部通道唯一是 @mention 评论 |

**F-1.6: 三通道详细设计**

**通道一：StatusDashboard 交互提示**

| 项 | 值 |
|---|---|
| 文件 | `orchestrator/status_dashboard.py` |
| 触发条件 | 非 headless 模式 + 操作员在线 + `dashboard.interactive_clarification=true` |
| UI 形式 | 面板中内联选项列表（1-4 选项 + 跳过 + 转发给作者） |
| 优点 | 响应最快（即时），操作员可结合代码上下文判断 |
| 降级条件 | headless 模式或 timeout（默认 5 分钟无操作） |

**通道二：ClarificationQueue 文件队列**

| 项 | 值 |
|---|---|
| 文件 | `orchestrator/clarification_queue.py:ClarificationQueue` |
| 队列路径 | `~/.clawcodex/clarification_queue.json` |
| CLI 命令 | `clawcodex clarify --issue <id> --answer <text>` |
| 轮询机制 | Orchestrator 每轮 poll 检查队列（与 Issue 轮询同步） |
| 超时 | timeout_local_minutes（默认 30 分钟），过期后降级通道三 |
| 优点 | 完全异步，操作员无需盯屏，不阻塞 orchestrator |
| 降级条件 | timeout_local_minutes 内无应答则发 @mention |

**通道三：@mention 评论**

| 项 | 值 |
|---|---|
| 接口 | `TrackerAdapter.create_clarification_comment()` → Issue 下发评论 + @mention |
| 轮询机制 | Orchestrator 轮询检查 Issue 新评论（每 poll_interval_ms） |
| 超时 | timeout_author_hours（默认 72 小时） |
| escalation | 超时后 skip / mark_failed / notify |

**F-1.7: ClarificationStatus 枚举（扩展）**

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
```

**F-1.8: TrackerAdapter 评论接口扩展**

| 接口 | 说明 |
|------|------|
| `fetch_issue_comments(issue_id)` | 获取 Issue 所有评论，返回 `list[Comment]` |
| `create_clarification_comment(issue_id, body, mentions)` | 发评论并 @mention，触发通知 |
| `find_clarification_replies(since_comment_id)` | 查找某条评论之后的新回复（用于轮询增量检查） |

**F-1.9: IssueRegistry 澄清字段**

```python
clarification_status: ClarificationStatus = ClarificationStatus.NONE
question_history: list[str] = field(default_factory=list)
author_login: str | None = None
awaiting_since: float | None = None
last_checked_comment_id: str | None = None
local_answer: str | None = None           # 本地操作员的回答
local_answer_source: str | None = None    # "dashboard" | "clarification_queue"
```

**F-1.10: 关键约束 & 风险**

| 风险 | 缓解措施 |
|------|----------|
| 操作员不在线 + 作者不回复 | escalation 策略（skip/mark_failed/notify）+ 双通道降级 |
| Agent 反复提问 | max_questions_per_issue（默认 3 次）上限 |
| @mention 噪音 | 通道一二优先消耗模糊 Issue；仅置信度 > threshold（0.7）时触发 |
| 作者回复无效/误解 | LLM 重判定 + 计入重试次数 |
| 评论顺序错乱 | in_reply_to_comment_id + 时间戳重建对话树 |
| 重启丢失上下文 | ClarificationQueue 文件持久化 + IssueRegistry clarification_status |
| 多操作员同时应答 | ClarificationQueue 加锁；resolved 后其他应答者收到提示 |
| Headless 无 Dashboard | headless 模式下自动跳过通道一直达 ClarificationQueue |

**F-1.11: 多渠道冲突处理方案**

**问题场景**:

| 场景 | 描述 |
|------|------|
| 同时多渠道应答 | 操作员和作者在同一时间窗口内同时回答 |
| 超时后迟到 | 通道二超时升级通道三后，操作员的本地回答才到达 |
| 重复提交 | 同一渠道内同一答案被多次提交 |
| 升级通知丢失 | 操作员在不知情的情况下回答了已升级的 Issue |

**核心原则**:

| 原则 | 说明 |
|------|------|
| 第一响应者优先 | 第一个被 Orchestrator 检测到的有效答案被采纳 |
| 操作员优先级 | 操作员答案始终比作者更可信（`operator_priority: true`） |
| 单向升级不可逆 | 通道二超时 → 通道三后，原通道迟来答案标记 STALE_REJECTED |
| 过期主动通知 | 所有被拒绝的答案都要通知对应应答者，避免无谓等待 |
| 去重幂等 | 同一答案重复提交第二次标记 DUPLICATE_REJECTED |

**ClarificationStatus 扩展（冲突处理相关）**:

```python
DUPLICATE_REJECTED = "duplicate_rejected"   # 重复提交，被去重丢弃
STALE_REJECTED = "stale_rejected"           # 超时升级后收到的过时答案
CONFLICT_RESOLVED = "conflict_resolved"    # 多渠道冲突已裁决
```

**冲突处理状态机**:

```
收到任意渠道答案
        ↓
本通道第一响应？ → 否 → DUPLICATE_REJECTED 丢弃
        ↓ 是
当前 status = AWAITING_LOCAL:
    LOCAL 答案 → RESOLVED_LOCAL
    AUTHOR 答案（在 AWAITING_LOCAL 期间）→ RESOLVED_AUTHOR
    → 操作员收到："作者已先回复，您的窗口已关闭"

当前 status = AWAITING_AUTHOR:
    AUTHOR 答案 → RESOLVED_AUTHOR
    LOCAL 答案（在 AWAITING_AUTHOR 期间）→ STALE_REJECTED
    → 操作员收到："通道二已超时，@mention 已发出，您的回答已过时"

当前 status = TIMED_OUT_LOCAL / TIMED_OUT_AUTHOR / EXHAUSTED:
    任何答案 → STALE_REJECTED
    → 通知应答者："Issue 已超时升级/结束"
```

**同时应答检测**:

```python
# Orchestrator 同轮 poll 中同时检查 ClarificationQueue 和 Issue 评论
candidates = []
if local_item.answer:
    candidates.append(("local", answer, local_item.answered_at))
if author_comments:
    candidates.append(("author", latest.body, latest.created_at))

if len(candidates) > 1:
    delta_ms = abs(candidates[0][2] - candidates[1][2]) * 1000
    if delta_ms < 5000 and operator_priority:
        winner, loser = 0, 1   # 操作员优先
    else:
        winner = min(range(len(candidates)), key=lambda i: candidates[i][2])
    self._notify_rejected(candidates[1-winner][0], issue_id)
```

**超时告知机制**:

| 升级事件 | 通知内容 |
|---------|---------|
| 通道二超时 → 通道三 | "您的本地回答窗口已关闭，@mention 已发给作者" |
| 通道三超时 → escalation | "Issue #42 澄清超时，最终处理：skip/mark_failed/notify" |
| 迟到操作员答案（通道三之后） | "您的回答已过时，@mention 已发出，作者回复已被采纳" |
| 迟到作者答案（escalation 之后） | 忽略，不更新状态 |

**冲突场景汇总**:

| 场景 | 处理结果 | 是否通知 |
|------|---------|---------|
| T4a < T3（操作员先答） | RESOLVED_LOCAL | 无（正常） |
| T3 < T4a（作者先回复） | RESOLVED_AUTHOR | ✅ 操作员超时通知 |
| T4a ≈ T4b（同时 < 5ms） | 操作员优先 RESOLVED_LOCAL | ✅ 双方均通知 |
| 通道三已升级后操作员才答 | STALE_REJECTED | ✅ "已超时升级" |
| 多操作员同时写队列 | 先写入者 RESOLVED | ✅ 落败方"已被抢先" |
| 同一答案重复提交 | DUPLICATE_REJECTED | ❌ 幂等，无需通知 |

**新增配置**:

```yaml
agent:
  clarification:
    operator_priority: true        # 操作员答案优先于作者（默认 true）
    stale_notification: "all"      # "all" | "operator_only" | "none"
    simultaneous_grace_ms: 5000   # 5ms 内视为同时，由 operator_priority 决胜
```

**F-1.12: 实施检查清单**

- [x] Phase A: `ClarificationQueue` 文件队列 + Orchestrator 轮询逻辑（`orchestrator/clarification_queue.py`）
- [x] Phase A: 冲突处理状态机（`orchestrator/clarification.py`）：DUPLICATE_REJECTED / STALE_REJECTED / CONFLICT_RESOLVED
- [x] Phase A: 超时告知机制（escalation_notified + stale_notification）
- [x] Phase A: 同时应答检测逻辑（simultaneous_grace_ms + operator_priority）
- [x] Phase B: StatusDashboard 交互提示组件（`orchestrator/status_dashboard.py`）
- [x] Phase C: `AskIssueAuthor` 工具（`tool_system/tools/ask_issue_author.py`）
- [x] Phase C: `ClarificationResolver` 三通道降级 + 冲突裁决
- [x] Phase D: CLI `clarify` 子命令（`orchestrator/cli/clarify.py`）
- [x] Phase E: `TrackerAdapter.fetch_issue_comments()` / `create_clarification_comment()` 接口
- [x] Phase E: `RepositoryTrackerAdapter` 实现（GitHub / Gitee / GitCode）
- [x] Phase F: IssueRegistry 澄清字段持久化（`clarification_status`、`local_answer`、`first_response_source`、`stale_answers`）
- [x] Phase F: PromptBuilder 澄清内容注入（`prompt_builder.py`：`build_clarification_context()` + AgentRunner 集成）
- [x] Phase G: escalation 策略实现（skip / mark_failed / notify）

**F-1.13: Orchestrator CLI 运维操作界面**

| 命令 | 说明 | 优先级 | 状态 |
|------|------|--------|------|
| `clawcodex orchestrator run` | 启动 orchestrator（替代 `--workflow`，**不兼容变更**） | P1 | ✅ 完成 |
| `clawcodex orchestrator status` | 全局 running/paused/completed/failed 状态 | P1 | ✅ 完成 |
| `clawcodex orchestrator issues list` | 列出所有 issue 及状态 | P1 | ✅ 完成 |
| `clawcodex orchestrator issues tail <id>` | 实时 tail tool call 日志 | P1 | ✅ 完成 |
| `clawcodex orchestrator issues show <id>` | 查看 issue 详情（理解上下文、token 用量） | P1 | ✅ 完成 |
| `clawcodex orchestrator pause <id>` | 暂停 agent（停在当前 tool call 边界） | P1 | ✅ 完成 |
| `clawcodex orchestrator resume <id>` | 恢复暂停中的 agent | P1 | ✅ 完成 |
| `clawcodex orchestrator stop <id>` | 强制终止 agent | P1 | ✅ 完成 |
| `clawcodex orchestrator inject <id> "text"` | 向运行中的 agent 注入提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator inject <id> --list` | 查看已注入的提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator inject <id> --remove <n>` | 删除某条提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator clarify --issue <id> --answer <text>` | 操作员澄清应答 | P1 | ✅ 完成 |
| `clawcodex orchestrator workspace <id> --ls` | 列出 workspace 文件 | P1 | ✅ 完成 |
| `clawcodex orchestrator workspace <id> --cat <file>` | 查看文件内容 | P1 | ✅ 完成 |
| `clawcodex orchestrator workspace <id> --edit <file> --with <content>` | 修改文件 | P2 | ✅ 完成 |
| `clawcodex orchestrator takeover <id>` | 完全接管（终止 + REPL） | P2 | ✅ 完成 |
| `clawcodex orchestrator dashboard --port` | 独立 dashboard UI | P2 | ✅ 完成 |

**不兼容变更**：

> ⚠️ `clawcodex --workflow` 将在发布时废弃，替换为 `clawcodex orchestrator run`。
> 这是唯一的 CLI 不兼容变更。现有启动命令：
> ```bash
> # 旧（将废弃）
> clawcodex --workflow test_gitcode_workflow.md
> # 新
> clawcodex orchestrator run --workflow test_gitcode_workflow.md
> ```
> release note 中需特别说明。

**实施检查清单**：

- [x] Phase O1: CLI `orchestrator` group 框架（`cli.py`：`clawcodex orchestrator`）
- [x] Phase O1: `orchestrator run`（替代 `--workflow`）
- [x] Phase O1: `orchestrator status` / `orchestrator issues list`
- [x] Phase O2: `orchestrator pause <id>` / `orchestrator resume <id>` / `orchestrator stop <id>`
- [x] Phase O2: Orchestrator pause/resume 状态支持（running → paused → running）
- [x] Phase O3: `orchestrator issues tail <id>`（AgentRunner event stream → 流式推送 tool calls）
- [x] Phase O3: StatusDashboard 实时渲染（event stream 消费）
- [x] Phase O4: `orchestrator inject` Hint 注入（`.operator_hints.md` 机制）
- [x] Phase O5: `orchestrator workspace --ls` / `--cat`（文件查看）
- [x] Phase O5: `orchestrator workspace --edit`（文件修改，协作场景）
- [x] Phase O6: `orchestrator takeover <id>`（终止 + REPL 接管）
- [x] Phase O7: `orchestrator clarify`（澄清应答，与 Phase D/C 澄清流程合并）
- [x] Phase O8: Dashboard LiveView 增强（event stream 完整推送 LLM 摘要 + tool calls）

---

## 四、规划任务

### F-2: Team 成员管理 (Phase-7)

**状态**: ⏳ 规划中
**优先级**: P1
**WI**: WI-6.4

#### 目标
扩展 `TeamCreate` 工具，使其能够跟踪和管理团队中的成员 Agent。

#### 数据模型

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

#### 核心机制

| 机制 | 说明 |
|------|------|
| TeammateInit | `agent(run_in_background=true)` 时自动注册到 `members` |
| 状态同步 | TaskOutput 显示 completed/failed 时更新成员状态 |
| 名称注册 | Agent 名称冲突检测 `agent_name_registry` |
| 递归 Fork 保护 | Fork Agent 无法嵌套调用 Fork |

#### 实现文件

| 文件 | 状态 |
|------|------|
| `tool_system/tools/team.py` | ✅ 已实现 TeamCreate/TeamDelete，members 数组已支持 |
| `services/swarm/team_file.py` | ✅ 已实现 TeamFile、TeamMember 数据模型 |
| `services/swarm/team_membership.py` | ✅ 已实现 is_team_lead() 函数 |
| `services/swarm/agent_name_registry.py` | ✅ 已实现名称注册表 |
| `tool_system/tools/agent.py` | ✅ 基础完成，TeammateInit 机制就绪 |

#### 测试覆盖

| 测试文件 | 测试用例 |
|----------|----------|
| `test_team_file.py` | `test_team_file_created_with_members_array`, `test_team_file_schema_members_array`, `test_team_file_missing_members_tolerated` |
| `test_team_membership.py` | `test_is_team_lead_true_*`, `test_is_team_lead_false_*` |

---

### R-8 到 R-10: 其他开源替代

| 任务 | 替代方案 | 优先级 | 状态 |
|------|---------|--------|------|
| 工具语义搜索 | Qdrant | P2 | ⏳ 待开始 |
| 权限规则引擎 | Casbin | P2 | ⏳ 待开始 |
| 日志系统 | structlog | P2 | ⏳ 待开始 |

---

### F-9: /goal 命令（目标管理）

**状态**: ⏳ 待实现
**优先级**: P2

#### 目标
支持长时间运行任务的目标管理，包括 set/pause/resume/complete 子命令。

#### 功能说明

| 子命令 | 功能 |
|--------|------|
| `/goal set <goal>` | 设置当前任务目标 |
| `/goal clear` | 清除目标 |
| `/goal pause` | 暂停目标追踪 |
| `/goal resume` | 恢复目标追踪 |
| `/goal complete` | 标记目标完成 |

#### 核心机制

| 机制 | 说明 |
|------|------|
| Goal 状态机 | `active` / `paused` / `budget_limited` / `complete` |
| Token 用量追踪 | 自动追踪当前 session 的 token 消耗 |
| Continuation Prompt | 目标状态自动注入到 continuation prompt |
| session-scoped 隔离 | 按 sessionId 管理独立的目标状态 |

#### 参考实现

- `commands/goal/goal.ts` - /goal 斜杠命令
- `services/goal/goalState.ts` - Goal 状态管理
- `packages/builtin-tools/src/tools/GoalTool/GoalTool.ts` - Goal 工具

#### 数据模型

```python
class GoalState(BaseModel):
    session_id: UUID
    goal: str
    status: Literal["active", "paused", "budget_limited", "complete"]
    created_at: datetime
    updated_at: datetime
    token_usage: dict  # {current: int, threshold: int}
```

---

### F-10: ExecuteExtraTool 延迟工具系统

**状态**: ⏳ 待实现
**优先级**: P2

#### 目标
实现完整的延迟工具按需加载系统，支持 TF-IDF 语义搜索和子代理执行。

#### 功能说明

| 组件 | 功能 |
|------|------|
| SearchExtraToolsTool | TF-IDF 工具索引语义搜索 |
| ExecuteExtraTool | 通过名称和参数执行延迟工具 |
| validateInput 校验 | 调用前校验防止崩溃 |
| ASYNC_AGENT_ALLOWED_TOOLS | 子代理可执行延迟工具 |

#### 核心机制

| 机制 | 说明 |
|------|------|
| 工具延迟加载 | 工具按名称和参数动态执行，非预加载 |
| 语义搜索 | TF-IDF 索引支持自然语言工具搜索 |
| 子代理执行 | Async Agent 可调用延迟工具 |
| 输入校验 | execute 前 validateInput 防止无效调用 |

#### 参考实现

- `packages/builtin-tools/src/tools/ExecuteTool/ExecuteTool.ts`
- `packages/builtin-tools/src/tools/SearchExtraToolsTool/`
- `constants/tools.ts` - ASYNC_AGENT_ALLOWED_TOOLS

#### 现有基础

clawcodex 已有 `tool_system/tool_search.py` 工具搜索实现，需扩展为 SearchExtraToolsTool 语义搜索。

---

### F-11: sessionStorage 容量限制

**状态**: ⏳ 待实现
**优先级**: P2

#### 目标
为 `existingSessionFiles` Map 设置容量上限，防止 daemon/swarm 会话内存泄漏。

#### 问题场景

- daemon/swarm 模式下长时间运行
- sessionId 频繁创建销毁
- Map 无限增长导致 OOM

#### 实现方案

```python
MAX_CACHED_SESSION_FILES = 200

class SessionStorage:
    def __init__(self):
        self.existing_session_files: dict[UUID, str] = {}

    def add_session_file(self, session_id: UUID, file_path: str):
        if len(self.existing_session_files) >= MAX_CACHED_SESSION_FILES:
            oldest_key = next(iter(self.existing_session_files))
            del self.existing_session_files[oldest_key]
        self.existing_session_files[session_id] = file_path
```

#### 参考实现

- `src/utils/sessionStorage.ts` - existingSessionFiles Map + MAX_CACHED_SESSION_FILES = 200

---

### F-12: cacheWarning 容量限制

**状态**: ⏳ 待实现
**优先级**: P2

#### 目标
为 `cacheWarningStateBySource` Map 设置容量上限，防止 querySource 类型为 any 时内存泄漏。

#### 问题场景

- querySource 类型为 any
- 长时间会话产生大量唯一 source 值
- Map 无限增长导致内存泄漏

#### 实现方案

```python
MAX_SOURCE_ENTRIES = 50

class CacheWarning:
    def __init__(self):
        self.cache_warning_state_by_source: dict[str, CacheWarningState] = {}

    def update(self, source: str, state: CacheWarningState):
        if len(self.cache_warning_state_by_source) >= MAX_SOURCE_ENTRIES:
            oldest_key = next(iter(self.cache_warning_state_by_source))
            del self.cache_warning_state_by_source[oldest_key]
        self.cache_warning_state_by_source[source] = state

    def reset_for_test(self):
        """测试隔离用"""
        self.cache_warning_state_by_source.clear()
```

#### 参考实现

- `src/utils/cacheWarning.ts` - cacheWarningStateBySource Map + MAX_SOURCE_ENTRIES = 50

---

## 五、不可替代组件

以下组件经过深入分析后被判定为不可替代：

| 组件 | 文件 | 不可替代原因 |
|------|------|-------------|
| Agent 执行循环 | `agent/run_agent.py` | 四级权限模型 (bubble/dontAsk/bypassPermissions/acceptEdits)、Subagent 隔离、消息完整性保证 |
| MCP 服务 | `services/mcp/` | 已完整实现 MCP 协议 (Stdio/HTTP/SSE/WebSocket)，替换成本过高 |
| Trust Boundary | `permissions/trust_boundary.py` | 环境变量安全白名单/黑名单是项目特定的信任模型 |
| Bridge/FlushGate | `services/bridge/` | 纯状态机，__slots__ 优化，无外部依赖 |

---

## 六、切换机制设计

每个适配器模块支持**运行时切换**底层实现，无需修改调用方代码。

### 设计模式

```
src/xxx/ (原有实现 - 作为回退)
    ↓ (可选)
src/xxx/_adapter.py (适配器层 - 支持切换)
    ↓
开源依赖 (如 tree-sitter-bash)
```

### 切换配置

每个适配器模块顶部的 `_USE_<ADAPTER>` 变量控制使用新实现还是原有实现：

```python
# src/permissions/_treesitter_adapter.py
_USE_TREESITTER = os.getenv("CLAW_USE_TREESITTER", "true").lower() in ("true", "1")

def parse_command(command: str):
    if _USE_TREESITTER:
        return _treesitter_parse(command)
    else:
        return _original_parse(command)  # 回退到原有实现
```

### 环境变量配置

| 适配器模块 | 环境变量 | 默认值 | 说明 |
|-----------|---------|-------|------|
| `_treesitter_adapter.py` | `CLAW_USE_TREESITTER` | `true` | 切换 Bash 解析器 |
| `_gitpython_adapter.py` | `CLAW_USE_GITPYTHON` | `true` | 切换 Git 操作 |
| `_pydantic_adapter.py` | `CLAW_USE_PYDANTIC_SETTINGS` | `true` | 切换配置系统 |
| `_frontmatter_adapter.py` | `CLAW_USE_FRONTMATTER_LIB` | `true` | 切换 frontmatter 解析 |
| `_litellm_adapter.py` | `CLAW_USE_LITELLM` | `false` | 切换 Provider 层 |
| `_pluggy_adapter.py` | `CLAW_USE_PLUGGY` | `false` | 切换 Hook 系统 |
| `_outlines_adapter.py` | `CLAW_USE_OUTLINES` | `false` | 切换结构化输出 |

### 切换原则

1. **已完成且验证稳定**: 默认使用新实现 (`true`)
2. **待完成或实验性**: 默认使用原有实现 (`false`)
3. **生产环境**: 可通过环境变量切换，便于快速回滚

### 验证流程

1. 新实现测试通过后，将对应环境变量设为 `true`
2. 发现问题时，将环境变量设为 `false` 切回原有实现
3. 修复后重新验证，验证通过后再切回新实现

---

## 七、里程碑

| 日期 | 里程碑 | 完成内容 |
|------|--------|----------|
| 2026-05-17 | Phase 1 完成 | Pydantic-settings, python-frontmatter, tree-sitter-bash, GitPython, Pluggy, Outlines 适配器完成 |
| 2026-05-17 | Orchestrator Phase 1-2 | 所有核心组件完成，CLI 集成待完成 |
| TBD | Phase 7 | Team 成员管理实现 |
| TBD | Phase 3-4 | Orchestrator 生产强化 + 可观测性 |

---

## 八、文档索引

| 文档 | 说明 |
|------|------|
| `docs/FEATURE_PLAN.md` | 特性规划总览 |
| `docs/PROGRESS.md` | 本文档 - 进度跟踪 |
| `docs/INTEGRATION.md` | Symphony 集成规范 |
| `docs/TEAM_MEMBERSHIP.md` | Team 成员扩展设计 |

---

## F-23: Skills System Extension（技能系统扩展层）

**状态**: ✅ 完成
**优先级**: P1
**目标**: 仿照 `tool_system_ext` 模式，构建独立的技能系统扩展层
**完成日期**: 2026-05-24

### 背景

当前 `src/skills/loader.py` 存在以下问题：
- 硬编码 clawcodex 特定路径（`~/.clawcodex/skills` 等）
- `get_all_skills()` 职责过于集中
- 难以独立更新上游

### 设计模式

| 组件 | Tool System | Skills System |
|------|-------------|---------------|
| 上游核心 | `tool_system/registry.py` | `skills/loader.py` |
| 扩展目录 | `tool_system_ext/` | `skills_ext/` (新) |
| 扩展包装类 | `ToolRegistryExt` | `SkillRegistryExt` (新) |
| Bundle机制 | `TOOL_BUNDLES` | `SKILL_BUNDLES` (新) |
| Agent配置 | `AgentToolConfig` | `AgentSkillConfig` (新) |

### 实现文件清单

| 文件路径 | 优先级 | 状态 | 说明 |
|---------|--------|------|------|
| `src/skills_ext/__init__.py` | P0 | ✅ 完成 | 扩展层入口 |
| `src/skills_ext/registry_ext.py` | P0 | ✅ 完成 | SkillRegistryExt 包装类 |
| `src/skills_ext/bundles.py` | P0 | ✅ 完成 | Skill Bundle 定义 |
| `src/skills_ext/agent_config.py` | P1 | ✅ 完成 | Agent Skill 配置 |
| `src/skills_ext/paths.py` | P1 | ✅ 完成 | clawcodex 特定路径解析 |
| `src/skills_ext/hooks.py` | P2 | ✅ 完成 | Skill 生命周期钩子 |
| `src/skills_ext/cache.py` | P2 | ✅ 完成 | 扩展层缓存管理 |

### 迁移策略

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | 创建 `src/skills_ext/` 目录和基础结构 | ✅ 完成 |
| 2 | 迁移 clawcodex 特定路径逻辑到 `skills_ext/paths.py` | ✅ 完成 |
| 3 | 添加 Bundle 机制和 AgentSkillConfig | ✅ 完成 |
| 4 | 添加 Hook 机制和回调系统 | ✅ 完成 |
| 5 | 更新 `get_all_skills()` 调用点使用 `SkillRegistryExt` | ✅ 完成 |

### 核心组件设计

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

---

*文档更新时间: 2026-05-24*

*版本 v1.4 更新：新增 F-22 Cron 系统执行引擎规划，对标 claude-code-best 生产级实现。*