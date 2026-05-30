# ClawCodex 开发进度跟踪文档

> 文档路径: `docs/PROGRESS.md`
> 基于: `docs/open-source-replacement-progress.md`, `docs/FEATURE_PLAN.md`
> 版本: v1.7
> 更新日期: 2026-05-30
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
| R-7 | Provider 层 | 多个 Provider 类 (~1,630 行) | LiteLLM | ~1,430 行 | P0 | ✅ 完成 |
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
| F-15 | 权限模式切换 (Shift+Tab) | P1 | ✅ 完成 | REPL/LiveStatus/TUI 中支持 `default→acceptEdits→plan→bypassPermissions` 循环切换，状态栏显示当前模式，/permission 命令 |
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
| F-27 | TUI 响应性修复（LLM 超时后 Ctrl+C/ESC 无响应） | P1 | ✅ 完成 | StreamWatchdog 超时时触发 AbortSignal；Ctrl+C 先尝试取消 agent 再退出 |
| F-28 | Ctrl+B Agent 后台持续运行 + `--resume` 恢复会话 | P1 | 🔄 设计完成 | Fork-Continue 模式：Ctrl+B 后 fork 子进程继续运行 agent，--resume 通过 TailFollower 实时显示增量输出 |
| F-29 | TaskInspect/TaskDirectives 工具注册 | P2 | ✅ 完成 | 将 TaskInspectTool 和 TaskDirectivesTool 注册到 ALL_STATIC_TOOLS，实现 Manager Agent 查询/指令 Worker |
| F-30 | ProgressReportTool 工具注册 | P2 | ✅ 完成 | 将 ProgressReportTool 注册到 ALL_STATIC_TOOLS，Agent 可调用阶段性进度汇报 |
| F-31 | TUI 权限模式选择器 | P1 | ✅ 完成 | 模态对话框支持 5 种权限模式切换 (default/acceptEdits/plan/bypassPermissions/dontAsk) |
| F-32 | 会话恢复浏览器 (Resume Conversation) | P1 | ✅ 完成 | 模糊搜索、实时过滤、会话元数据展示，支持 /resume 命令和 --tui --resume 启动选项 |

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

**规划任务详情已归档至 [ARCHIVED_PROGRESS.md](./ARCHIVED_PROGRESS.md)**

---

## 二、已归档进度详情

> **已完成任务已归档至 [ARCHIVED_PROGRESS.md](./ARCHIVED_PROGRESS.md)**
>
> 以下列出的所有已完成任务详情已在归档文档中详细记录：开源替代组件(R-1~R-6)、功能模块开发(F-1, F-3, F-14, F-15, F-17, F-19, F-20, F-21, F-23, F-24, F-25, F-27, F-29, F-30, F-31, F-32)等的实现详情。

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

**状态**: ✅ 完成
**完成日期**: 2026-05-30
**优先级**: P0
**预计减少代码**: ~1,430 行

#### 背景
`src/providers/` 包含多个 Provider 实现 (~1,630 行)。

#### 实现方案
使用 `LiteLLM` 统一 Provider 层，支持 100+ 模型。

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

#### 进度
- [x] `src/providers/_litellm_adapter.py` 适配器文件已创建
- [x] 实现 `LiteLLMProvider` 类
- [x] 集成到 Provider 注册系统（`should_use_litellm()` + `create_provider()` 工厂）
- [x] 移除硬编码的 anthropic/openai/zhipuai 必装依赖（通过 `CLAW_USE_LITELLM` 环境变量切换）
- [x] 端到端测试（49 个目标测试全部通过）

#### 关键文件
- `extensions/providers_ext/__init__.py` — 扩展包导出
- `extensions/providers_ext/litellm_provider.py` — LiteLLM Provider 实现（含 `_get_litellm_model()` 提取）
- `src/providers/__init__.py` — 工厂函数 `should_use_litellm()` / `create_provider()`
- `src/providers/_litellm_adapter.py` — 兼容垫片（重新导出扩展包符号）
- `src/entrypoints/headless.py` — 使用 `create_provider()`
- `src/entrypoints/tui.py` — 使用 `create_provider()`
- `pyproject.toml` — 包发现包含 `extensions*`

#### 环境开关
- `CLAW_USE_LITELLM=false`（默认）— 使用原始 Provider 类
- `CLAW_USE_LITELLM=1|true|yes|on` — 使用 LiteLLM 统一 Provider

#### 注意事项
- LiteLLM 保留 `BaseProvider` 接口可回退
- 向后兼容：旧导入路径 `from src.providers._litellm_adapter import ...` 继续有效

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
| **Orchestrator CLI 统一入口** | ✅ 已完成 | `clawcodex orchestrator server start/status/stop` + `issue list/show/tail/stop/pause/resume/takeover/clarify/inject/workspace` |
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
| `clawcodex orchestrator server start --workflow PATH` | 启动 orchestrator daemon | P1 | ✅ 完成 |
| `clawcodex orchestrator server status` | 查看 daemon 运行状态（PID、uptime） | P1 | ✅ 完成 |
| `clawcodex orchestrator server stop` | 停止 orchestrator daemon | P1 | ✅ 完成 |
| `clawcodex orchestrator issue list [--status]` | 列出所有 issue 及状态 | P1 | ✅ 完成 |
| `clawcodex orchestrator issue tail --id <id>` | 实时 tail tool call 日志 | P1 | ✅ 完成 |
| `clawcodex orchestrator issue show --id <id>` | 查看 issue 详情（理解上下文、token 用量） | P1 | ✅ 完成 |
| `clawcodex orchestrator issue pause --id <id>` | 暂停 agent（停在当前 tool call 边界） | P1 | ✅ 完成 |
| `clawcodex orchestrator issue resume --id <id>` | 恢复暂停中的 agent | P1 | ✅ 完成 |
| `clawcodex orchestrator issue stop --id <id>` | 强制终止 agent | P1 | ✅ 完成 |
| `clawcodex orchestrator issue inject --id <id> <hint>` | 向运行中的 agent 注入提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator issue inject --id <id> --list` | 查看已注入的提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator issue inject --id <id> --remove <n>` | 删除某条提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator issue clarify --id <id> --answer <text>` | 操作员澄清应答 | P1 | ✅ 完成 |
| `clawcodex orchestrator issue workspace --id <id> --ls` | 列出 workspace 文件 | P1 | ✅ 完成 |
| `clawcodex orchestrator issue workspace --id <id> --cat <file>` | 查看文件内容 | P1 | ✅ 完成 |
| `clawcodex orchestrator issue workspace --id <id> --edit <file> --with <content>` | 修改文件 | P2 | ✅ 完成 |
| `clawcodex orchestrator issue takeover --id <id>` | 完全接管（终止 + REPL） | P2 | ✅ 完成 |
| `clawcodex orchestrator dashboard --port` | 独立 dashboard UI | P2 | ✅ 完成 |

**不兼容变更**：

> ⚠️ `clawcodex --workflow` 已废弃，替换为 `clawcodex orchestrator server start --workflow PATH`。
> 原有扁平子命令（`run`、`status`、`issues`、`pause`、`resume`、`stop`、`inject`、`clarify`、`workspace`、`takeover`）已移除，
> 统一使用 noun-verb 结构：`server <verb>` / `issue <verb> --id <id>`。
> ```bash
> # 新命令
> clawcodex orchestrator server start --workflow test_gitcode_workflow.md
> clawcodex orchestrator server status
> clawcodex orchestrator issue list
> clawcodex orchestrator issue pause --id 42
> clawcodex orchestrator issue inject --id 42 "hint text"
> ```

**实施检查清单**：

- [x] Phase O1: CLI `orchestrator` group 框架（`cli.py`：`clawcodex orchestrator`）
- [x] Phase O1: `orchestrator server start`（替代 `--workflow` 和 `run`）
- [x] Phase O1: `orchestrator server status` / `orchestrator issue list`
- [x] Phase O2: `orchestrator issue pause --id <id>` / `issue resume --id <id>` / `issue stop --id <id>`
- [x] Phase O2: Orchestrator pause/resume 状态支持（running → paused → running）
- [x] Phase O3: `orchestrator issue tail --id <id>`（AgentRunner event stream → 流式推送 tool calls）
- [x] Phase O3: StatusDashboard 实时渲染（event stream 消费）
- [x] Phase O4: `orchestrator issue inject --id <id>` Hint 注入（`.operator_hints.md` 机制）
- [x] Phase O5: `orchestrator issue workspace --id <id> --ls` / `--cat`（文件查看）
- [x] Phase O5: `orchestrator issue workspace --id <id> --edit`（文件修改，协作场景）
- [x] Phase O6: `orchestrator issue takeover --id <id>`（终止 + REPL 接管）
- [x] Phase O7: `orchestrator issue clarify --id <id>`（澄清应答）
- [x] Phase O8: Dashboard LiveView 增强（event stream 完整推送 LLM 摘要 + tool calls）

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

## F-28: Ctrl+B Agent 后台持续运行 + `--resume` 恢复会话

**状态**: 🔄 设计完成，待实现
**优先级**: P1
**目标**: Ctrl+B 后 Agent 在子进程中继续运行，用户可通过 `--resume` 重新连接并实时查看 Agent 进度

### 问题分析

当前 Ctrl+B 的实际行为是"保存退出"而非"后台运行"：

1. `action_agent_background()` 调用 `self.exit(result=("__FULL_EXIT__", sid))`，整个进程退出
2. Agent worker 线程（daemon thread）随进程死亡，无任何后台延续
3. `background_signal` 被设置但 `run_with_background_escape` 未在 TUI agent loop 路径中被调用
4. `--resume` 仅恢复 JSONL 快照，不会连接活跃的后台 agent

### 设计方案：Fork-Continue 模式

采用**父进程退出 + 子进程继续运行 agent** 的模式：

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

### 核心组件

#### 1. `src/agent/background_runner.py` — 后台 Runner（新增）

| 函数 | 说明 |
|------|------|
| `launch_background_runner(session, provider, tool_registry, tool_context, max_turns)` | Fork 子进程，在子进程中运行 headless agent loop |
| `_run_agent_headless(session, provider, tool_registry, tool_context, max_turns)` | 子进程入口：构建独立 asyncio loop，调用 `run_query_as_agent_loop`，通过 `SessionStorage.write_message` 持续写入 |
| `get_background_runner_status(session_id)` | 读取 `.background-runner.json`，检查子进程存活状态 |
| `wait_for_background_runner(session_id, timeout=None)` | 等待子进程完成（同步场景） |
| `cleanup_background_runner(session_id)` | 清理 marker 文件 |

**状态文件**：`~/.clawcodex/sessions/{session_id}/.background-runner.json`

```json
{
  "pid": 12345,
  "session_id": "abc123",
  "started_at": "2025-01-01T00:00:00",
  "status": "running"
}
```

**Fork 逻辑**：

```python
def launch_background_runner(session, provider, tool_registry, tool_context, max_turns):
    session.save()  # 确保 JSONL transcript 存在

    pid = os.fork()
    if pid > 0:
        _write_runner_marker(session.session_id, pid)
        return pid
    else:
        os.setsid()  # 新会话组，脱离父进程终端
        sys.stdin.close()
        log_path = _runner_log_path(session.session_id)
        sys.stdout = open(log_path, 'a')
        sys.stderr = open(log_path, 'a')
        _run_agent_headless(session, provider, tool_registry, tool_context, max_turns)
        os._exit(0)
```

**Headless Agent Loop**：

- 构建独立 `asyncio.new_event_loop()`
- 调用 `run_query_as_agent_loop()`，传入 `on_message` 回调写入 JSONL
- 权限模式切换为 `bypassPermissions`（后台无用户交互）
- 完成后写入 `{"role": "system", "content": "__background_complete__"}` 标记
- 更新 marker 文件状态为 `completed` 或 `failed`

#### 2. 现有模块修改

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/tui/app.py` | 修改 | `action_agent_background()` 改为调用 `launch_background_runner()` + 新退出标记 `__BACKGROUND_EXIT__` |
| `src/tui/agent_bridge.py` | 修改 | `_run_tail_follower` 添加 `__background_complete__` 完成标记检测 |
| `src/entrypoints/tui.py` | 修改 | 退出处理区分有/无后台 agent；resume 时检查 bg runner 状态 |
| `src/repl/core.py` | 修改 | `_handoff_to_textual_tui` 退出处理同步更新 |
| `src/agent/session.py` | 微调 | `resume_with_tail()` 添加 bg runner 状态检查 |
| `src/agent/background_state.py` | 微调 | 更新文档注释，说明 Fork-Continue 模式替代原始信号竞态设计 |

#### 3. 退出标记变更

```
旧: ("__FULL_EXIT__", session_id)
新: ("__BACKGROUND_EXIT__", session_id, has_bg_agent)
```

- `has_bg_agent=True`：Agent 在后台子进程中运行，打印绿色提示 + resume 命令
- `has_bg_agent=False`：仅保存退出，打印黄色提示 + resume 命令
- 旧 `__FULL_EXIT__` 标记保持向下兼容

### 并发安全保证

| 场景 | 保证 |
|------|------|
| JSONL 写入竞态 | 父进程退出后子进程独占写入，不存在并发写入；`SessionStorage._atomic_write` 保证原子性 |
| Fork 时序 | fork 前先 `session.save()` 确保状态落盘；fork 后子进程从头开始 agent loop |
| 权限处理 | 后台模式 `bypassPermissions`，Ctrl+B 是用户的显式授权 |
| 僵尸进程 | 子进程 `os.setsid()` 独立会话组；崩溃时 marker 记录 `failed` 状态 |

### 边界情况

| 场景 | 处理方式 |
|------|----------|
| Ctrl+B 时 agent 空闲 | 仅保存退出，无 fork |
| Ctrl+B 时 agent 正在请求权限 | 取消当前 run，fork 后重新运行（headless bypass） |
| Resume 时后台 agent 已完成 | TailFollower 读到 `__background_complete__` 后停止，进入交互模式 |
| Resume 时后台 agent 已崩溃 | marker 文件为 `failed`，提示错误并显示日志路径 |
| 多次 Ctrl+B | 检查 marker，若已有 running 则提示 |
| Windows（无 os.fork） | 回退到 subprocess.Popen 启动 headless runner |

### 里程碑

| 阶段 | 内容 | 状态 | 依赖 |
|------|------|------|------|
| M1 | `background_runner.py` 核心模块 + fork 逻辑 | ⏳ 待实现 | 无 |
| M2 | `action_agent_background()` 重构 + `__BACKGROUND_EXIT__` 标记 | ⏳ 待实现 | M1 |
| M3 | TailFollower 完成检测 + `__background_complete__` | ⏳ 待实现 | M1 |
| M4 | `tui.py` / `repl/core.py` 退出处理增强 | ⏳ 待实现 | M2 |
| M5 | `resume_with_tail()` bg runner 状态集成 | ⏳ 待实现 | M1, M3 |
| M6 | Windows subprocess 降级路径 | ⏳ 待实现 | M1 |
| M7 | 端到端测试 | ⏳ 待实现 | M1-M5 |

### 与 F-21 的关系

F-21（后台运行 + 恢复同步）是当前已有的基础设施层，提供了：

- `background_state.py` — 信号/标志管理
- `TailFollower` — JSONL 尾部追踪
- `SessionWatcher` — 目录变更监控
- `keybindings.py` — Ctrl+B 绑定
- `session.py` — `resume_with_tail()` 工厂方法
- `agent_bridge.py` — TailFollower 集成
- `graceful_shutdown.py` — SIGTSTP 处理

F-28 在 F-21 基础上补全了**关键缺失环节**：Agent 实际在后台继续运行的机制。F-21 提供了"传输管道"（TailFollower/SessionWatcher），F-28 提供了"数据源"（fork 子进程持续写入 JSONL）。

---

## F-33: REPL 模式 Ctrl+B 后台运行支持

**状态**: 📋 规划中
**优先级**: P2
**目标**: REPL（非 TUI）模式下按 Ctrl+B 触发 Agent 后台持续运行，与 TUI 的 `action_agent_background()` 行为对齐

### 问题分析

当前 Ctrl+B 仅在 TUI 模式下有效：

1. TUI 按 Ctrl+B → `action_agent_background()` → `launch_background_runner()` → fork 子进程继续运行 agent
2. REPL 按 Ctrl+B → **无响应**，LiveStatus 的按键绑定中不包含 Ctrl+B
3. REPL 的 `chat()` 方法中，ESC/Ctrl+C 走 `on_cancel` 回调（仅取消，不后台化）
4. REPL 退出（Ctrl+C/Ctrl+D）只打印 "Interrupted"，不会触发后台运行

### 解耦设计原则

1. **不修改 `LiveStatus` 内部逻辑**：Ctrl+B 绑定通过**外部注入**（`on_background` 回调参数）
2. **复用 `background_runner.py`**：fork/subprocess 逻辑已完整实现，REPL 只需调用 `launch_background_runner()`
3. **`BackgroundEscape` 异常作为信号边界**：LiveStatus 只负责检测按键和触发回调，`chat()` 捕获异常后决定是否 fork
4. **`on_background` 与 `on_cancel` 同构**：新增参数遵循已有回调模式，上游合并冲突最小

### 架构设计

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

### 核心组件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/repl/background_escape.py` | **新增** | `BackgroundEscape` 异常类，解耦信号 |
| `src/repl/live_status.py` | **微调** | 新增 `on_background` 参数 + Ctrl+B keybinding |
| `src/repl/core.py` | **修改** | chat() 两路径添加 `on_background` 回调；外层捕获 `BackgroundEscape`；空闲态 Ctrl+B 绑定 |
| `src/repl/__init__.py` | **微调** | 导出 `BackgroundEscape` |

**不修改的文件**（关键解耦点）：
- `src/agent/background_runner.py` — 完全复用
- `src/agent/session.py` — `save()` / `resume()` 已有
- `src/cli.py` — `start_repl()` 无需修改，`--resume` 路径已通
- `src/utils/abort_controller.py` — 取消逻辑不受影响

### 信号流对比

| 事件 | TUI 路径 | REPL 路径（F-33 新增） |
|------|---------|----------------------|
| Agent 空闲 + Ctrl+B | 无响应 | 保存退出，打印 resume 提示 |
| Agent 运行中 + Ctrl+B | `action_agent_background()` → fork → `__BACKGROUND_EXIT__` | `on_background` 回调 → `BackgroundEscape` → `_handle_background_escape()` → fork → `sys.exit(0)` |
| Agent 运行中 + ESC | 取消 agent run | 取消 agent run（现有行为不变） |
| `--resume` 恢复 | `Session.resume_with_tail()` + TailFollower | `Session.resume()` + 重新进入 REPL（现有行为） |

### 里程碑

| 阶段 | 内容 | 状态 | 依赖 |
|------|------|------|------|
| M1 | `background_escape.py` 异常类 | ⏳ 待实现 | 无 |
| M2 | `live_status.py` 新增 `on_background` 参数 + Ctrl+B 绑定 | ⏳ 待实现 | M1 |
| M3 | `core.py` chat() direct stream 路径 | ⏳ 待实现 | M1, M2 |
| M4 | `core.py` chat() engine 路径 | ⏳ 待实现 | M1, M2 |
| M5 | `core.py` 空闲态 Ctrl+B 绑定 + `_handle_background_escape()` | ⏳ 待实现 | M1 |
| M6 | 手动集成测试 | ⏳ 待实现 | M1-M5 |

---

## F-34: CLI/TUI Frontend 解耦架构（✅ 已完成 Phase 1-3）

**状态**: ✅ 完成（Phase 1: CLI 所有权迁移；Phase 2: RuntimeContext + Frontend 协议；Phase 3: ClawCodexExtTUI 扩展钩子）

### 目标

将 CLI、TUI、Headless 三个入口点共用的 provider/registry/context/session 构造逻辑提取到统一的 `RuntimeContext`，定义 `Frontend` 协议实现前端插件化注册，消除三处重复代码并允许第三方 frontend（如 claude_repl、clawcodex_cli_integration）零修改接入。

### 问题分析

**三入口点各自重复构造**：

```python
# src/cli.py → _run_print_mode()
# src/cli.py → _run_tui_mode()
# src/cli.py → start_repl()

# 每处重复：
provider = get_default_provider()
provider_cfg = get_provider_config(provider)
provider_cls = get_provider_class(provider)
provider = provider_cls(...)
tool_registry = build_default_registry(provider=provider)
tool_context = ToolContext(workspace_root=...)
session = Session.create(provider, model)
```

**argparse 与 frontend 直接耦合**：`--tui` / `--legacy-repl` / `--no-tui` 硬编码，加新前端需改 argparse + dispatch。

**Agent 循环 ×2**：TUI 用 `AgentBridge`，REPL 用内联 agent 循环。

### 架构设计

#### 新增模块：`src/runtime/`

```
 src/runtime/
   ├── __init__.py           # 公共导出
   ├── context.py            # RuntimeOptions + RuntimeContext.build()
   ├── events.py             # TextChunkEvent, ToolUseEvent, ToolResultEvent ...
   ├── engine.py             # AgentEngine（统一 submit/cancel/event）
   ├── protocol.py           # Frontend Protocol
   └── registry.py           # FrontendRegistry（register / get / dispatch）
```

#### 核心数据流

```
CLI args
   ↓
 RuntimeOptions                 ← 从 argparse 提取
   ↓
 RuntimeContext.build()         ← 统一 factory（消除 ×3 重复）
   ↓
 RuntimeContext
   ├→ provider, model
   ├→ tool_registry, tool_context
   ├→ session
   └→ permission_mode, max_turns ...
   ↓
 Frontend.run(ctx)              ← 协议化调用
   ├── repl (prompt_toolkit)
   ├── tui (Textual)
   ├── headless (NDJSON)
   ├── claude-repl (第三方)
   └── cli-integration (第三方)
```

#### Frontend 协议

```python
class Frontend(Protocol):
    name: str
    display_name: str
    description: str

    def run(self, ctx: RuntimeContext) -> int: ...
    # 可选
    def on_start(self, ctx: RuntimeContext) -> None: ...
    def on_finish(self, exit_code: int) -> None: ...
    @classmethod
    def argparse_group(cls, parser: argparse.ArgumentParser) -> None: ...
```

#### 注册 + 调度

```python
# 注册
register("repl", ReplFrontend)
register("tui", TuiFrontend)
register("headless", HeadlessFrontend)

# cli.py 不再有 if-else dispatch
return registry.dispatch(args)
# 或
export CLAWCODEX_FRONTEND=claude-repl
clawcodex  # 自动使用 claude-repl
```

### 组件清单

| 组件 | 路径 | 说明 | 状态 |
|------|------|------|------|
| RuntimeContext | `clawcodex_ext/runtime/context.py` | 统一 factory，消除 3 处重复构造 | ✅ |
| Frontend 协议 | `clawcodex_ext/frontend/protocol.py` | 前端契约，实现即接入 | ✅ |
| FrontendRegistry | `clawcodex_ext/frontend/registry.py` | 插件式注册 + dispatch，单例实例 | ✅ |
| REPLFrontend | `clawcodex_ext/frontend/repl.py` | REPL 前端插件 | ✅ |
| TUIFrontend | `clawcodex_ext/frontend/tui.py` | TUI 前端插件 | ✅ |
| HeadlessFrontend | `clawcodex_ext/frontend/headless.py` | Headless 前端插件 | ✅ |
| ClawCodexExtTUI | `clawcodex_ext/tui/app.py` | 下游 TUI App，8 个扩展钩子 | ✅ |

### 修改文件

| 操作 | 文件 | Phase | 状态 |
|------|------|-------|------|
| 新增 | `clawcodex_ext/cli/parser.py` | Phase 1 | ✅ |
| 新增 | `clawcodex_ext/cli/permissions.py` | Phase 1 | ✅ |
| 新增 | `clawcodex_ext/cli/runners.py` | Phase 1 | ✅ |
| 新增 | `clawcodex_ext/cli/dispatch.py` | Phase 1 | ✅ |
| 新增 | `clawcodex_ext/runtime/context.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/protocol.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/registry.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/repl.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/tui.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/headless.py` | Phase 2 | ✅ |
| 修改 | `src/cli.py`（改为兼容外观层） | Phase 1 | ✅ |
| 修改 | `clawcodex_ext/cli/main.py`（delegates to dispatch） | Phase 1 | ✅ |
| 修改 | `clawcodex_ext/tui/app.py`（8 个扩展钩子） | Phase 3 | ✅ |

### 实施阶段

#### Phase 1: RuntimeContext（消除重复构造） ✅ 完成

| 里程碑 | 内容 | 工作量 | 状态 |
|--------|------|--------|------|
| P1-M1 | `clawcodex_ext/runtime/context.py`（RuntimeOptions + RuntimeContext.build()） | - | ✅ |
| P1-M2 | `clawcodex_ext/runtime/__init__.py` | - | ✅ |
| P1-M3 | CLI dispatch → 使用 RuntimeContext.build() | - | ✅ |
| P1-M4 | `src/cli.py` → 兼容外观层 | - | ✅ |
| P1-M5 | 验证：三入口点行为不变 | - | ✅ |

**Phase 1 输出**：`src/cli` 转为兼容外观，`clawcodex_ext/cli/` 拥有全部实现。

#### Phase 2: Frontend 协议 + 注册表（插件化） ✅ 完成

| 里程碑 | 内容 | 工作量 | 状态 |
|--------|------|--------|------|
| P2-M1 | `clawcodex_ext/frontend/protocol.py`（Frontend 协议 + FrontendPlugin ABC） | - | ✅ |
| P2-M2 | `clawcodex_ext/frontend/registry.py`（注册表 + dispatch，单例） | - | ✅ |
| P2-M3 | 实现 ReplFrontend / TuiFrontend / HeadlessFrontend | - | ✅ |
| P2-M4 | CLI dispatch → 使用 registry.dispatch() | - | ✅ |
| P2-M5 | 集成测试：TUI + REPL + headless | - | ✅ |

#### Phase 3: ClawCodexExtTUI 扩展钩子 ✅ 完成

| 里程碑 | 内容 | 工作量 | 状态 |
|--------|------|--------|------|
| P3-M1 | `clawcodex_ext/tui/app.py` 8 个 override 钩子 | - | ✅ |
| P3-M2 | 测试覆盖 | - | ✅ |

### 外部 Frontend 接入示例

```bash
# 直接使用已注册的第三方 frontend
clawcodex --frontend claude-repl -p "hello"
clawcodex --frontend cli-integration --tui

# 环境变量默认
export CLAWCODEX_FRONTEND=claude-repl
clawcodex  # 自动使用 claude-repl
```

### 风险与缓解

| 风险 | 缓解 | 状态 |
|------|------|------|
| RuntimeContext.build() 耦合具体 provider/registry | 可抽象 ProviderFactory / RegistryFactory | 后期 |
| AgentEngine 与现有 AgentBridge 行为差异 | 保留 AgentBridge 接口，内部委派，逐步替换 | 后期 |
| 重构破坏已有功能 | 每个 Phase 完成后执行完整集成测试套件 | ✅ 33 tests PASS |

---

*文档更新时间: 2026-05-30*

*版本 v1.7 更新：F-34 Phase 1-3 全部完成。CLI parser/dispatch 迁入 `clawcodex_ext/cli`；RuntimeContext 工厂 + Frontend 协议/注册表完成；`ClawCodexExtTUI` 8 个扩展钩子就绪。*
*版本 v2.0 更新：新增 F-35 二开特性统一切换架构设计，Feature Toggle 系统 + 内联修改特性提取方案。*

---

## F-35: 二开特性统一切换（上游纯净模式开关）

**状态**: 📋 规划中
**优先级**: P1
**依赖**: F-34（前端的切换提供了入口点基础）

### 问题现状

F-34 解决了前端层的切换问题，但 `src/` 中还有大量二开特性与上游源码 58ea488 深度混合：

| 分类 | 数量 | 说明 |
|------|------|------|
| 二开新增文件（Only in src/） | 23 个 | 上游不存在，纯二开特性 |
| 二开修改文件（Files differ） | **584 个** | 上游源码被直接内联修改 |

这意味着：
- **不能直接切换回上游** — inline 修改无法单独关闭
- **上游升级困难** — 每次合入需手动 diff 584 个文件
- **特性边界不清** — 每个文件的改动用途不明

### 设计目标

1. **一个开关统一切换**：运行时通过 `CLAWCODEX_UPSTREAM_MODE=true` 决定加载上游版本还是二开版本
2. **零代码切换**：无需改 import、无需改代码，修改环境变量即可
3. **上游兼容**：上游模式开启时，行为与上游 58ea488 一致
4. **逐步迁移**：584 个文件不必一次全部提取，可以分批渐进

### 架构设计

```
src/features/
   ├── __init__.py           # 包入口 + is_upstream_mode()
   ├── resolver.py           # Import hook 模块解析器
   └── patches/              # 提取后的二开补丁（可选）
```

#### 启动时执行流程

```python
# src/features/__init__.py

def is_upstream_mode() -> bool:
    """检查是否以上游纯净模式运行"""
    return os.environ.get("CLAWCODEX_UPSTREAM_MODE", "0") in ("1", "true")

def init_features():
    """根据模式决定是否启用 import hook"""
    if is_upstream_mode():
        # 上游模式：注册 import hook，加载 src/upstream/58ea488/ 的原始模块
        sys.meta_path.insert(0, UpstreamResolver())
```

### 核心原理：文件级替换

```
上游模式（CLAWCODEX_UPSTREAM_MODE）:
  import repl.core
  → import hook 拦截，加载 src/upstream/58ea488/repl/core.py（纯上游版本）

二开模式（默认）:
  import repl.core
  → 正常加载 src/repl/core.py（二开版本，同当前行为不变）
```

不需要逐段标注 FTR、不需要 30 个独立开关。只需一个开关决定：加载哪个 `src/` 下的模块。

### 提取流程（584 个文件）

```
步骤 A: 补全上游快照
  → 确保 src/upstream/58ea488/ 包含所有被修改文件的原版

步骤 B: 分批还原
  P3: 高优先级文件还原（~20 个核心文件：repl/core.py, tui/app.py 等）
  P4: 中优先级文件还原（~100 个文件）
  P5: 低优先级文件还原（剩余~460 个文件）

步骤 C: 注册 import 映射
  → 在 resolver.py 中注册已还原文件的映射
  → 上游模式时加载 upstream 版本

步骤 D: 可选：二开补丁提取
  → 如果二开版本丢失了改动，需从 diff 提取补丁
  → 放到 patches/ 目录，启动时应用
```

### 使用方式

```bash
# 默认启动（二开模式，同当前行为不变）
clawcodex

# 上游纯净模式（所有二开特性关闭）
CLAWCODEX_UPSTREAM_MODE=1 clawcodex

# 通过环境变量
CLAWCODEX_UPSTREAM_MODE=1 clawcodex
CLAWCODEX_UPSTREAM_MODE=true clawcodex-tui

# 通过配置文件（settings.json）
# "upstream_mode": true
```

### 实施阶段

| Phase | 内容 | 工作量 | 交付物 |
|-------|------|--------|--------|
| **P1** | 基础设施：`features/__init__.py` + `resolver.py` + cli.py 初始化 | 1 天 | `__init__.py`, `resolver.py` |
| **P2** | 补全上游快照：确保 `src/upstream/58ea488/` 与原版完全一致 | 1 天 | 完整的上游源码快照 |
| **P3** | 高优先级文件提取 + 还原（~20 个核心文件） | 3 天 | repl/core.py, tui/app.py, cli.py 还原 |
| **P4** | 中优先级文件提取 + 还原（~100 个文件） | 1 周 | 按模块分批发 |
| **P5** | 低优先级文件提取 + 还原（剩余 ~460 个文件） | 2 周 | 批量脚本处理 |
| **P6** | 完整验证 | 2 天 | 上游模式 = 原始 58ea488；二开模式 = 当前行为一致 |

### 里程碑

| 里程碑 | 内容 | 预计完成 |
|--------|------|----------|
| M1 | ✅ Import hook 框架可用 | P1 完成后 |
| M2 | ✅ 高优先级核心文件可切换 | P3 完成后 |
| M3 | ✅ 全部 584 个文件可切换 | P5 完成后 |
| M4 | ✅ CLI 可一键切换纯上游模式 | P6 完成后 |

### 对比：简化前后

| 维度 | 之前（30 个独立 FTR） | 现在（一个全局开关） |
|------|----------------------|---------------------|
| 代码复杂度 | 需要 `toggles.py` 注册表、30+ env var 解析、依赖校验 | 只需 `is_upstream_mode()` + import hook |
| 配置量 | 30 个 `CLAWCODEX_FTR_*` 环境变量 | 仅 1 个 `CLAWCODEX_UPSTREAM_MODE` |
| 提取难度 | 需逐段标注 diff（行级标记 FTR-ID） | 整体文件提取即可 |
| 用户心智负担 | 高（需要知道每个 FTR 什么含义） | 极低（开关即模式切换） |

### 风险与缓解

| 风险 | 缓解 |
|------|------|
| Import hook 与现有模块系统冲突 | P1 充分测试；备选方案：直接 `sys.path` 操作 |
| 584 个文件还原时间过长 | 优先级分批进行，P1-P2 即可获得核心功能 |
| 上游源码升级后 diff 过大 | 保留完整文件的二开版本副本，二开模式用 diff apply |
| 还原后二开模式行为偏差 | 分步还原每个文件后立即验证 |

---

*文档更新时间: 2026-05-25*

*版本 v2.0 更新：新增 F-35 二开特性统一切换架构设计，一个全局开关（CLAWCODEX_UPSTREAM_MODE）控制所有二开特性，文件级 import hook 实现模块替换，分批还原 584 个内联修改文件。*