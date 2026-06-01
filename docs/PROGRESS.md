# ClawCodex 开发进度跟踪文档

> 文档路径: `docs/PROGRESS.md`
> 基于: `docs/open-source-replacement-progress.md`, `docs/FEATURE_PLAN.md`
> 版本: v2.7
> 更新日期: 2026-06-01
> 上游同步: 68dc3c5 (Phase 11 bridge complete)
>
> **v2.7 变更**：新增 F-41 Coordinator 轻量工具集（✅ 已完成）。给 Coordinator 配置独立的轻量工具集（Read、WebSearch、WebFetch），加上原有的 Agent、SendMessage、TaskStop，共 6 个工具。Coordinator 可直接处理简单查询（搜网页、读文件），无需为每个请求创建 Worker。所有写操作工具（Write、Edit、Bash、Grep、Glob）仍隔离，强制委派复杂任务给 Worker。涉及 `src/coordinator/mode.py` 的 `_COORDINATOR_ALLOWED_TOOLS` 扩展 + `src/coordinator/prompt.py` 的 "Your Tools" 提示词更新 + `src/repl/core.py` 注释同步。231/231 orchestrator 测试通过。
>
> **v2.6 变更**：新增 F-40 ProgressReporter Sink 协议重构（📋 设计完成）。解决 F-38 Sub-D 落地时遗留的三个问题：(1) `Orchestrator` 上 `ProgressReporter` 单例的 `_current_task_id` / `_phase_count` 共享可变状态在并发 issue 下竞争；(2) `AgentRunner` 只转发 `PhaseComplete`，`_on_session_complete` 形同虚设，会话结束无进度落点；(3) `progress = phase_count * 25` 是假数据。设计引入 `ProgressSink` Protocol + `CompositeProgressSink` 扇出 + `ToolContextProgressSink` 默认实现 + `ProgressReporter` 降级为 shim；新增 `WorkflowConfig.phases` 用于真实进度计算。
>
> **v2.5 变更**：表格中所有 ✅ 已完成 / ✅ 基础完成的项（R-1~R-7、F-1、F-3、F-14、F-15、F-17、F-19、F-20、F-21、F-23、F-24、F-25、F-27、F-29、F-30、F-31、F-32）详细设计已归档至 [ARCHIVED_PROGRESS.md](./ARCHIVED_PROGRESS.md) 与 [ARCHIVED_FEATURES.md](./ARCHIVED_FEATURES.md)，本文件仅保留任务总览表与仍处规划/进行中任务的详细设计。

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

**总计已减少代码**: ~4,530 行
**预计全部完成后减少**: ~4,530+ 行（剩余 R-8~R-10 实施后达到完整目标）

### 1.2 功能模块开发

> 状态为 ✅ 完成 / ✅ 基础完成的项（F-1、F-3、F-14、F-15、F-17、F-19、F-20、F-21、F-23、F-24、F-25、F-27、F-29、F-30、F-31、F-32）详细设计已归档；本文仅保留概览与链接，详见 [ARCHIVED_PROGRESS.md](./ARCHIVED_PROGRESS.md) 与 [ARCHIVED_FEATURES.md](./ARCHIVED_FEATURES.md)。

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
| F-13 | Agent 记忆作用域隔离 | P1 | ✅ 完成 | 按需加载不同作用域记忆，clawcodex_ext try-import 降级模式 |
| F-14 | 三层解耦架构（Layer Isolation） | P1 | ✅ 完成 | upstream/capabilities/features 三层分离，零层违规 |
| F-15 | 权限模式切换 (Shift+Tab) | P1 | ✅ 完成 | REPL/LiveStatus/TUI 中支持 `default→acceptEdits→plan→bypassPermissions` 循环切换，状态栏显示当前模式，/permission 命令 |
| F-16 | Auto 模式 (TRANSCRIPT_CLASSIFIER) | P2 | ⏳ 待开始 | 基于 LLM 的自动权限模式切换，减少交互疲劳 |
| F-17 | 工具系统按需加载（Tool System Extension） | P1 | ✅ 完成 | 四种工具模式（bare/default/clawcodex/all），4 bundle 简化设计，bundle 引用前缀 ":"，与上游解耦 |
| F-18 | CreateAgentTool 动态工具创建 | P2 | 🔄 规划中 | Agent 可根据 CLI/API 规范动态创建工具，Meta Tool 能力，bash/http/python 三种 call_impl 安全限制 |
| F-19 | POS to Agent 转化模式 | P2 | ✅ 完成 | 三层映射（POS→Agent、workflow→Skill、SDK→工具），SDK 解析 + Skill 分组 + Agent 构建 + 持久化 |
| F-20 | Agent 阶段性进度汇报 | P2 | ✅ 完成 | 三组合方案：检查点触发 + ProgressReportTool + ToolContext.tasks 持久化；PhaseComplete 时双重调用 ProgressReportTool + TaskUpdateTool 更新 metadata |
| F-21 | 后台运行 + 恢复同步 | P1 | ✅ 完成 | Ctrl+B 后台化 + TailFollower 实时同步 + SessionWatcher 多终端感知，补丁 0067-0074 |
| F-22 | Cron 系统执行引擎 | P0 | 🔄 进行中 | 工具定义和/loop skill已完成；执行引擎还需补齐调度队列、run 账本与 /autonomy 状态查看链路 |
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
| F-36 | LocalTracker 本地 Issue 文档源 | P1 | 📋 设计完成 | 新增 `tracker.kind: local`，从本地 Markdown/JSON issue 文档读取待处理任务，支持离线测试与私有本地工作流 |
| F-37 | Orchestrator PR 检视意见自动修复闭环 | P0 | 📋 设计完成 | 将 PR 网页检视意见、inline comments、review summary 与 CI 失败日志转化为 follow-up agent run，自动修改同一 PR 分支并提交更新 |
| F-38 | Orchestrator 验证与报告闭环（verification + report → PR） | P0 | ✅ 完成 | commit/push 前自动跑 verification gate（pre_push hook + test_command），agent 跑完写结构化报告，git_sync 用报告改写 PR body 并合并为单条 issue 汇总评论；进度由 dead-code `progress_reporter` 接入主流程 |
| F-39 | Orchestrator Issue 重跑入口（label + comment 命令双通道） | P0 | ✅ 完成（Sub-A~F） | 三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发、Sub-B 重置重跑、Sub-C follow-up 叠 commit、Sub-D comment 命令解析、Sub-E CLI 兜底、Sub-F 限频+角色校验均已落地；端到端 10-11 阶段（实际 GitCode/GitHub issue 联动）待真实环境验证 |
| F-40 | ProgressReporter Sink 协议重构 | P1 | 📋 设计完成 | 把 `Orchestrator` 上 `ProgressReporter` 单例拆为每 session 独立的 `ProgressSink` 实例；新增 `CompositeProgressSink` 扇出支持 F-37/F-39 零侵入接入；补全 `SessionComplete` / `TurnComplete` 转发；引入 `WorkflowConfig.phases` 做真实进度计算，淘汰 `phase_count * 25` 假数据 |
| F-41 | Coordinator 轻量工具集 | P1 | ✅ 已完成 | 给 Coordinator 配置独立的轻量工具集（Read、WebSearch、WebFetch），加上原有的 Agent、SendMessage、TaskStop，共 6 个工具。Coordinator 可直接处理简单查询（搜网页、读文件），无需为每个请求创建 Worker。所有写操作工具（Write、Edit、Bash、Grep、Glob）仍隔离，强制委派复杂任务给 Worker。涉及 `src/coordinator/mode.py` 的 `_COORDINATOR_ALLOWED_TOOLS` 扩展 + `src/coordinator/prompt.py` 的 "Your Tools" 提示词更新 + `src/repl/core.py` 注释同步。231/231 orchestrator 测试通过 |

---

## F-37: Orchestrator PR 检视意见自动修复闭环

**状态**: 📋 设计完成
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.4 PR 检视意见自动修复闭环设计`

### 目标

将基于 PR 检视意见的自动修改能力产品化到 `extensions/orchestrator`：当 Orchestrator 已经根据 issue 自动实现、提交并创建 PR 后，后续网页上的 PR conversation 评论、inline review comments、review summary 和 CI/pipeline 失败日志应能被自动读取、去重、转化为 follow-up agent run，并在同一 PR 分支上完成修改、验证、提交和推送。

### 当前基线

| 能力 | 当前状态 | 说明 |
|------|----------|------|
| Issue 自动实现 | ✅ 已具备 | Orchestrator 可轮询 issue 并启动 agent run |
| 自动 commit/push/PR | ✅ 已具备 | `GitSyncService` 在 agent 完成后提交、推送并创建/复用 PR |
| Issue 评论读取 | ✅ 已具备 | TrackerAdapter 已有 issue comments 接口，主要服务 clarification 流程 |
| PR conversation 评论读取 | ❌ 待实现 | 需要读取 PR 对应 issue comments 或平台 PR comments API |
| PR inline review comments 读取 | ❌ 待实现 | 需要平台 API 支持文件路径、行号、diff hunk |
| Review summary 读取 | ❌ 待实现 | 需要读取 PR reviews / review notes |
| CI/pipeline 失败日志读取 | ❌ 待实现 | 需要读取 checks、jobs、pipeline logs 并做摘要/截断 |
| Feedback 幂等处理 | ❌ 待实现 | 需要记录已处理 feedback id/check id，避免重复修复 |
| 同 PR 分支 follow-up run | ❌ 待实现 | 需要新增 review-fix prompt 和复用原 PR 分支的 git sync 模式 |

### 实施进度

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | 扩展 tracker 协议，新增 `PullRequestFeedback` 数据模型和 PR feedback fetch/reply 接口 | 📋 待开始 |
| 2 | 扩展 GitHub/Gitee/GitCode repository client，读取 PR conversation、inline review comments、review summary | 📋 待开始 |
| 3 | 接入 CI/pipeline 失败日志读取与日志截断策略 | 📋 待开始 |
| 4 | 扩展 registry 或新增 feedback store，记录 feedback cursor、已处理 id、follow-up attempt 次数 | 📋 待开始 |
| 5 | 在 Orchestrator poll loop 增加 review follow-up 阶段，扫描已有 open PR 的新反馈 | 📋 待开始 |
| 6 | 新增 review-fix prompt builder，约束 agent 只处理 PR 检视意见与 CI 失败 | 📋 待开始 |
| 7 | 调整 git sync follow-up 模式，确保只 commit/push 原 PR 分支，不创建新 PR | 📋 待开始 |
| 8 | 增加评论回复/汇总能力，标记已处理、无法处理或需 clarification 的反馈 | 📋 待开始 |
| 9 | 增加单元测试和端到端测试：去重、bot 评论过滤、inline 映射、CI 日志截断、重试上限 | 📋 待开始 |

### 验收标准

- 已有 issue 首次处理链路不回退：仍能自动实现、提交、推送并创建/复用 PR。
- PR 上新增普通检视评论后，Orchestrator 能在下一轮 follow-up 中读取并触发同分支修改。
- PR inline comment 能以文件路径、行号、diff hunk 形式进入 prompt，agent 能定位并做最小修改。
- CI 失败日志能以摘要形式进入 prompt，单条日志受字符上限控制。
- 已处理的评论或 check 不会在后续轮询中重复触发。
- bot 自己发布的状态评论不会造成自触发循环。
- follow-up run 不创建新分支、不创建新 PR，只更新当前 PR 分支。
- 无法自动判断的反馈进入 clarification/operator hint 流程，而不是猜测修改。

### 风险与约束

- 不同平台的 PR review API 差异较大，GitHub/Gitee/GitCode 需要分别映射到统一反馈模型。
- CI 日志可能非常大，必须摘要和截断，避免 prompt 过载。
- 网页评论可能包含互相冲突的要求，首期应优先处理明确、可定位、可验证的反馈。
- 自动回复评论应避免刷屏，推荐按 run 汇总回复，或仅回复明确处理完成的 inline comments。
- 默认不做自动合并、force push、关闭 PR 等高风险动作。

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
**参考实现**: claude-code-best `src/utils/cron*.ts`, `src/hooks/useScheduledTasks.ts`, `src/utils/autonomyRuns.ts`, `src/utils/autonomyStatus.ts`, `src/commands/autonomy*.ts`, `src/cli/print.ts`

### 目标

将 claude-code-best 的生产级别 cron 执行引擎移植到 ClawCodex，实现：
1. 完整 cron 表达式解析（5字段标准语法）
2. 下次执行时间计算（本地时区）
3. 调度器执行引擎（1秒轮询）
4. 任务持久化（`.claude/scheduled_tasks.json`）
5. 分布式锁（防止多进程重复执行）
6. Jitter 抖动算法（避免雷鸣般群体效应）
7. 任务过期机制（周期性任务7天自动删除）
8. scheduled fire 进入真实 REPL/TUI/headless 队列，而不是只写 outbox
9. 每次定时触发生成可查询 run 记录，状态覆盖 `queued`、`running`、`completed`、`failed`、`cancelled`
10. 提供 `/autonomy status`、`/autonomy runs`、`/autonomy status --deep` 或 ClawCodex 等价命令查看执行状态
11. 同一 cron task 存在 active run 时去重，避免高频任务在上一轮未完成时堆积

### 执行结果/状态查看链路

`claude-code-best` 不把定时任务的完整回答写回 cron job 定义表。它在 cron task 到期时创建 scheduled-task queued prompt，同时在 `.claude/autonomy/runs.json` 中创建 run 账本记录；队列消费前将 run 从 `queued` 原子切到 `running`，普通 query pipeline 执行完后再落到 `completed` / `failed` / `cancelled`。此外，`/schedule get <id>` 的 detail 视图展示 trigger 的 status、schedule、agent、next run、last run、created 和 prompt，`/schedule run <id>` 手动触发后直接回显 run id。因此 ClawCodex 需要同时实现“cron job 管理视图”、“trigger detail/manual-fire 视图”和“scheduled-task run 生命周期视图”，用户才能回答“任务是否已配置、上次/下次何时执行、是否正在执行还是失败”。

当前 ClawCodex 已有基础 `clawcodex_ext/cron_system/runs.py` 与 `status.py`，可读取 `.claude/scheduled_task_runs.json` 并输出 status/runs 文本表格；缺口在于它们尚未接入真实 REPL/TUI/headless 执行队列，run schema 也比 `claude-code-best` 的 autonomy run 记录更窄，缺少来源、路径、预览和 ownership/session 等操作追溯字段。

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

关键要求：

- `CronList` / `/cron-list` 只展示 cron job 定义、schedule、durable/session、next fire，不承担执行结果历史。
- trigger detail 或等价命令展示单个任务的 status、schedule、agent、next run、last run、created 和 prompt；manual fire 或等价命令创建 queued run 并回显 run id。
- `/autonomy runs` 或等价命令展示最近 run 历史，包括 run id、source id、prompt preview、创建/开始/结束时间、状态和错误摘要。
- `/autonomy status` 汇总当前 queued/running/failed/completed 数量；`/autonomy status --deep` 额外显示 cron job section 与最近 run section。
- run store 至少持久化 `run_id`、`runtime`、`trigger`、`status`、`source_id`、`source_label`、`prompt_preview`、`created_at`、`started_at`、`ended_at`、`error`、`root_dir`、`current_dir`，并在支持 teammate/agent 后补齐 ownership/session 元数据。
- 创建 queued run 时按 `source_id=cron task id` 做 active-run 去重：上一轮仍处于 `queued` 或 `running` 时跳过本轮触发，防止每分钟任务堆积。
- headless 模式无法路由 teammate/agent-owned cron task 时必须把对应 run 标记为 `failed`，不能静默丢弃。

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
| skills.py | `src/cron_system/skills.py` 或 extension command adapter | ❌ 待实现 | /cron-list, /cron-delete 命令 |
| runs.py | `clawcodex_ext/cron_system/runs.py` | ⚠️ 基础完成，待扩展 | 已有 `.claude/scheduled_task_runs.json` 账本和 queued/running/completed/failed/cancelled 生命周期；缺少 autonomy-compatible 字段、真实执行队列 claim/finalize 接线、`.claude/autonomy/runs.json` 等价布局决策 |
| status.py | `clawcodex_ext/cron_system/status.py` | ⚠️ 基础完成，待扩展 | 已有 status/runs 文本表格；缺少 deep status 的 richer section、trigger detail、manual-fire run id outcome、错误摘要/路径/来源字段展示 |
| queue lifecycle | REPL/TUI/headless adapter | ❌ 待实现 | scheduled fire 入队、claim 为 running、执行后 finalize、active source 去重 |
| trigger detail / manual fire | command/skill adapter | ❌ 待实现 | 暴露等价 `/schedule get <id>` 与 `/schedule run <id>` 的用户路径，展示 last/next run、created、prompt，并在手动触发后返回 run id |
| autonomy commands | command/skill adapter | ⚠️ fast-path 存在，待接线 | `clawcodex_ext/cli/dispatch.py` 已有 `autonomy status/runs` 分发；仍需接入真实运行账本和 richer output，区分 cron job 定义、trigger detail 与 run 生命周期 |
| build_missed_task_notification | `src/cron_system/missed_task_notification.py` 或 extension notification adapter | ❌ 待实现 | 错失任务通知构建函数 |
| growthbook_config.py | `src/cron_system/growthbook_config.py` | ❌ 待实现 | Jitter 参数动态配置 |

**CCB 对比分析发现的补充子任务（2026-06）**:

| 子任务 | 文件 | 状态 | 说明 |
|--------|------|------|------|
| G1: isKilled 运行时 kill 开关 | `clawcodex_ext/cron_system/scheduler.py` + gate 机制 | ❌ 待实现 | 每 tick 轮询 `is_killed`，支持运行时紧急关闭 cron |
| G2: 远程 Jitter 实时配置 | `clawcodex_ext/cron_system/jitter.py` + config loader | ❌ 待实现 | 配置文件/env var 热加载 6 个 jitter 参数，每 tick 重新读取 |
| G3: One-shot 反向 Jitter | `clawcodex_ext/cron_system/jitter.py` | ❌ 待实现 | 整点 (:00/:30) 可提前最多 90s 触发，确定性 hash 偏移 |
| G4: Permanent 免过期机制 | `clawcodex_ext/cron_system/models.py` + tools.py | ❌ 待实现 | `permanent` 字段、过期豁免、CronCreate 写入保护、assistant 安装入口 |
| G5: 锁注册式清理与 PID 增强 | `clawcodex_ext/cron_system/lock.py` | ⚠️ 部分完成 | 有基础 O_EXCL 锁和 os.kill 探测；缺少 atexit 注册清理、PID 分身检测、同 session 锁接管 |
| G6: 工具 Prompt 指引增强 | `clawcodex_ext/cron_system/tools.py` | ❌ 待实现 | CronCreate/List/Delete 的 `prompt` 字段补充 jitter/过期/scope 说明 |
| G7: Analytics 遥测事件预留 | `clawcodex_ext/cron_system/scheduler.py` | ❌ 待实现 | fire/missed/expired 事件点预留 Optional[Callable] |
| G8: inFlight 防重复触发 | `clawcodex_ext/cron_system/scheduler.py` | ❌ 待实现 | 异步 IO 期间用 in_flight Set 防止同一任务二次触发 |
| A1~A5: 已有优势特性保持 | 全模块 | ✅ 已存在 | CronRun 追踪/手动触发/状态展示/英文名支持/详情输出——需在实施中保持 |

### 里程碑

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | cron_parser.py - 表达式解析与时间计算 | ⏳ 待开始 |
| 2 | cron_tasks.py - 任务存储 CRUD | ⏳ 待开始 |
| 3 | cron_tasks_lock.py - 分布式锁 | ⏳ 待开始 |
| 4 | cron_scheduler.py - 执行引擎核心 | ⏳ 待开始 |
| 5 | cron_jitter_config.py - 动态配置 | ⏳ 待开始 |
| 6 | skills.py / command adapter - CLI 命令 (/cron-list, /cron-delete) | ⏳ 待开始 |
| 7 | runs.py - scheduled-task run 账本扩展到 autonomy-compatible schema 与 active source 去重 | ⏳ 待扩展 |
| 8 | queue lifecycle - scheduled fire 入队、claim running、finalize completed/failed/cancelled | ⏳ 待开始 |
| 9 | trigger detail/manual fire - 单任务详情与手动触发 run id 回显 | ⏳ 待开始 |
| 10 | status.py / autonomy commands - `/autonomy status`, `/autonomy runs`, `/autonomy status --deep` 或等价命令的 richer output | ⏳ 待扩展 |
| 11 | 测试覆盖 - cron job 管理、trigger detail/manual fire、run 生命周期、状态查看、headless 失败记录 | ⏳ 待开始 |
| **G1** | **isKilled 运行时 kill 开关** - scheduler 每 tick 轮询 `is_killed()`，工具 prompt 门控 | ❌ 待实现 |
| **G2** | **远程 Jitter 实时配置** - 6 个参数可配置文件/env 热加载，每 tick 重新读取 | ❌ 待实现 |
| **G3** | **One-shot 反向 Jitter** - 整点 (:00/:30) 提前触发，确定性 hash 偏移，min/max 保护 | ❌ 待实现 |
| **G4** | **Permanent 免过期机制** - 字段/写保护/过期豁免/assistant 安装入口 | ❌ 待实现 |
| **G5** | **锁注册式清理与 PID 增强** - atexit 清理、PID 分身检测、同 session 锁接管 | ⚠️ 部分完成 |
| **G6** | **工具 Prompt 指引增强** - CronCreate/List/Delete 的 prompt 字段补充最佳实践说明 | ❌ 待实现 |
| **G7** | **Analytics 遥测事件预留** - fire/missed/expired 事件点预留 Optional[Callable] | ❌ 待实现 |
| **G8** | **inFlight 防重复触发** - 异步 IO 期间用 in_flight Set 防止同一任务二次触发 | ❌ 待实现 |

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

**状态**: ✅ 完成（2026-06-06）
**优先级**: P1
**规划日期**: 2026-05-19

> 通过 `clawcodex_ext/memory/` 扩展包 + `prompt_assembly.py` forwarding seam 实现。采用 try-import + 静默降级模式，零侵入原有 `memdir/` 模块。
>
> 核心文件：
> - `clawcodex_ext/memory/__init__.py` — 包声明
> - `clawcodex_ext/memory/scope_aware_prompt.py` — 核心 scope 感知 prompt 逻辑
> - `src/context_system/prompt_assembly.py` — 4 处 forwarding seam（`build_full_system_prompt`、`build_full_system_prompt_blocks`、`_build_memory_section` 参数透传 + `build_scope_aware_memory_prompt` 调用）
>
> 验证：✅ 231/231 orchestrator 测试通过（F-39 Sub-A~F 全部落地，含 153 个 F-39 专项用例）| ✅ 371/378 parity 测试通过 | ✅ F-38 E2E 全部通过

---

### R-7: LiteLLM 替换 Provider 层

**状态**: ✅ 完成（2026-05-30）
**优先级**: P0
**预计减少代码**: ~1,430 行

> 背景、架构图、关键文件清单、环境开关（`CLAW_USE_LITELLM`）、兼容性说明、49 个端到端测试通过等已归档。
> 详见 [ARCHIVED_PROGRESS.md R-7](./ARCHIVED_PROGRESS.md#r-7-litellm-替换-provider-层) 与 [ARCHIVED_FEATURES.md §3.3](./ARCHIVED_FEATURES.md#33-litellm-provider-替换开源替代组件-r-7)。

---

### F-1: Orchestrator 自主模式

**状态**: ✅ 完成
**完成日期**: 2026-05-20
**优先级**: P0

> 14 个核心组件（Orchestrator / WorkspaceManager / Linear / Tracker / IssueRegistry / ClarificationQueue / CLI group 等）、生产强化（F-1.1~F-1.4）、三通道澄清（F-1.5~F-1.11，Phase A-G）、Orchestrator CLI 运维界面（F-1.13，O1-O8 共 18 条命令）等已归档。
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

## F-36: LocalTracker 本地 Issue 文档源

**状态**: 📋 设计完成
**优先级**: P1
**依赖**: F-1 Orchestrator 自主模式、TrackerAdapter 协议、IssueRegistry

### 目标

允许用户在本地特定目录中新增 issue 文档，由 Orchestrator 扫描并处理，形成无需外部 Linear/GitHub/Gitee/GitCode 的本地闭环：

```text
本地 issue 文档目录
  ↓ LocalTrackerAdapter 扫描与解析
统一 Issue 模型
  ↓ Orchestrator 领取与运行
workspace.root 下创建 per-issue workspace
  ↓ Agent 修改代码
issue front matter + IssueRegistry 更新状态
```

### 配置设计

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

`tracker.issues_path` 是任务来源目录；`workspace.root` 是运行工作区目录。二者保持分离，避免用户误以为在 workspace root 下手写 issue 会被自动消费。

### Issue 文档格式

首期采用 Markdown front matter：

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
- front matter 的 `id` / `identifier` 标识本地 issue；缺失时可从文件名派生并在写回时固化。
- 第一个一级标题作为 `title`；剩余 Markdown 正文作为 `description`。
- `state` 在 `active_states` 内才进入候选列表；在 `terminal_states` 内则跳过。
- `branch_name` 可选；缺失时由 identifier 和 title 生成稳定 slug。

### Adapter 行为

LocalTracker 应实现既有 `TrackerAdapter` 协议：

| 接口 | 行为 |
|------|------|
| `fetch_candidate_issues()` | 扫描 `issues_path`，解析 `.md` / `.json`，过滤 active state |
| `fetch_issue_states_by_ids(ids)` | 重新读取本地文件 state，用于 launch 前前置检查 |
| `find_pull_request(head, base)` | 首期默认返回 `None`；若文档中已有 `pr_url` 可返回轻量结果 |
| `ensure_pull_request(...)` | 不创建远程 PR；写回 commit/branch/status 等本地字段 |
| `fetch_issue_comments(...)` | 可选读取 `<id>.comments.ndjson` 或 front matter comments |
| `create_clarification_comment(...)` | 写入本地 comments/clarification 文件，不访问外部服务 |

### 状态与持久化

本地 issue 文档负责表达用户可见任务状态，`IssueRegistry` 继续负责运行映射。建议状态流：

```text
open/ready → running → completed
                  ├── failed
                  └── abandoned
```

写回字段限制在 front matter 内，避免重排或覆盖用户手写正文：

```yaml
state: completed
updated_at: 2026-05-30T12:34:56Z
workspace_path: /tmp/clawcodex_orchestrator_test_workspaces/LOCAL-001
branch_name: local-001-fix-dashboard-workspace
commit_sha: abc123
last_error: null
```

### 并发与幂等

| 风险 | 设计约束 |
|------|----------|
| 多个 orchestrator 同时领取同一文件 | issue 文件旁 lock 或原子 rename；领取前二次读取 state |
| 用户运行中编辑 issue 文档 | 写回时校验 mtime/updated_at；冲突时保留正文并只合并 front matter |
| 重启后重复处理 | `IssueRegistry.is_completed()`、terminal state、commit/pr 字段共同判定 |
| 本地 tracker 与远程 tracker 分叉 | 主流程只依赖 `TrackerAdapter`，不在 Orchestrator 中加入 local 特判 |

### 实施切片

- [x] 配置 schema 增加 `tracker.issues_path`，允许 `tracker.kind: local`。
- [x] 新增 LocalTracker parser/client/adapter，解析 Markdown front matter 到统一 `Issue`。
- [x] 接入 tracker factory 和配置校验。
- [x] 实现状态写回、commit 字段写回和失败字段写回。
- [ ] 补充单元测试：解析、过滤、写回、文件锁、launch 前 state 检查。
- [ ] 增加本地 workflow 示例和 smoke test 文档。

### 本地任务看板 Human Review Gate

**状态**: ✅ 完成
**功能**: issue 处理完成后（git commit）进入 `pending_review` 状态，人类通过 CLI 审批或拒绝。

#### 新增状态

| 状态 | 说明 |
|------|------|
| `pending_review` | Agent 完成 git commit，等待人类 review |

#### 新增 CLI 命令

```bash
# 查看变更概览（包含 agent summary + 文件统计）
clawcodex orchestrator issue diff --id <issue_id>

# 仅显示文件统计
clawcodex orchestrator issue diff --id <issue_id> --stat

# 显示完整 diff
clawcodex orchestrator issue diff --id <issue_id> --full

# 审批通过
clawcodex orchestrator issue review --id <issue_id> --approve --comment "LGTM"

# 审批拒绝（触发 agent 重试）
clawcodex orchestrator issue review --id <issue_id> --reject --feedback "请修复单元测试"
```

#### 流程

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

#### 实现文件

| 文件 | 说明 |
|------|------|
| `extensions/orchestrator/issue_registry.py` | 新增 `PENDING_REVIEW` 状态 + `mark_pending_review()` |
| `extensions/orchestrator/git_sync.py` | 新增 `pending_review` 字段，LocalTracker 提交后设为 True |
| `extensions/orchestrator/orchestrator.py` | 提交后标记 `pending_review`，新增 `pending_review` 集合处理 |
| `extensions/orchestrator/clarification_queue.py` | 新增 `inject_feedback()` 方法 |
| `extensions/orchestrator/cli/issue.py` | 新增 `review` + `diff` 子命令 |

---

*文档更新时间: 2026-06-01*

*版本 v2.2 更新：新增 LocalTracker Human Review Gate，支持 pending_review 状态、review 审批/拒绝命令、diff 变更命令（含 Agent Summary）。*

*版本 v2.3 更新：新增 F-38 Orchestrator 验证与报告闭环。Sub-A 在 `hooks` schema 新增 `pre_commit` / `pre_push` / `post_sync` 三个生命周期点，git_sync 在 commit/push 前后自动跑 verification gate（默认 pytest -x，用户可配 test_command）；Sub-B 新增 `IssueRecord.report_path` 字段、agent_runner 跑完生成 Markdown/JSON 报告、git_sync 据此改写 PR body；Sub-C 抽象 TrackerAdapter 增 `update_pull_request`，并实现 GitCode 客户端把报告回写到 PR；Sub-D 修复 `progress_reporter` 死代码，phase completion 接入 ndjson event log。*

*版本 v2.4 更新：新增 F-39 Orchestrator Issue 重跑入口。三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发，Sub-B 重置重跑，Sub-C follow-up 叠 commit，Sub-D comment 命令解析，Sub-E CLI 兜底，Sub-F 限频+角色校验。*

*版本 v2.7 更新：F-39 Orchestrator Issue 重跑入口落地（Sub-A~F 全部 ✅）。`tracker.py` 增 `Intent` str-Enum + `intent_from_label_set` 优先级助手 + `Command` enum + `parse_agent_command` 正则 + `CommandIntent` 数据类（携带 author_login/comment_id 用于 Sub-F 角色校验）+ `fetch_issue_command_intent` 默认实现；`issue_registry.py:IssueRecord` 增 `intent/retry_count/last_command/intent_source/command_cursor` 字段 + `mark_intent/clear_intent/reset_for_retry/increment_retry_count/unblock` 方法；`orchestrator.py` 在 `_poll_and_dispatch` 增加 Sub-F 角色校验（`allow_anyone_to_retry`/作者匹配/fail-closed）+ 限频（`max_retries_per_issue=3`）+ 拒绝评论与高优 audit 日志；`cli/issue.py` 增 `retry` 子命令（`--mode {reset,followup,unblock}` + `--force` + `--max-retries` + `--operator` + `--reason`）写 `~/.clawcodex/orchestrator/audit.jsonl`。新增 153 个 F-39 专项单测（`test_orchestrator_f39_{command,retry,retry_cli,followup,ratelimit,followup,retry_cli,intent}.py`），orchestrator 回归 231/231 通过。端到端 10-11 阶段（实际 GitCode/GitHub issue 联动）待真实环境验证。*

---

## F-38: Orchestrator 验证与报告闭环

**状态**: ✅ 完成（2026-06-01，含一轮 E2E + 1 bug 修复）
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.5 验证与报告闭环设计`
**触发场景**: 2026-06-01 在 `chadwweng/AgentSDK` 跑 issue #1 时发现 `tools=0` 仍走 success、agent 凭空返回 SessionComplete 后 commit/push/PR 全程无验证；事后 GitCode 上 PR `#1` 收到 1 条 Git Sync 评论但无 Run Complete 汇总；PR body 是静态模板不含验证/产物信息；reviewer 找不到 diff 与 workspace 路径。

### 目标

把 `extensions/orchestrator` 的 issue 跟踪流程从「commit/push/PR 直通」补全为「commit 验证 → push 验证 → 报告生成 → PR 反馈」的端到端闭环：

1. 修改代码后，系统层强制在 commit/push 之前跑 verification gate，挡住 `tools=0` 这类空跑批。
2. agent 跑完生成结构化报告（盘留 + 持久化）。
3. GitCode 端用报告回写 PR body，并发**一条汇总评论**（取代现在两条独立评论）。
4. 修复 `progress_reporter` 死代码，让阶段进度真正被 orchestrator 主流程消费。

### 子特性

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| Sub-A | Verification Gate | commit/push 前自动跑 `test_command` | `config/schema.py` 的 `HooksConfig` 新增 `pre_commit` / `pre_push` / `post_sync`；`git_sync.py` 调 `run_pre_commit_hook` / `run_pre_push_hook`；verification 失败时不 commit/push，将 issue 标记为 `verification_failed` |
| Sub-B | 结构化报告 | agent 跑完写 Markdown/JSON 报告 | `issue_registry.py` 的 `IssueRecord` 新增 `report_path` 字段；`agent_runner.py` 跑完调 `report_writer.write(session, workspace)` 生成报告；`git_sync._build_pr_body` 改为模板插值报告字段（commit、diff stat、verification 结果、turns/tools 计数） |
| Sub-C | PR 报告回写 | 把报告作为 PR 描述回写到 GitCode | `tracker.py` 抽象基类新增 `update_pull_request(pr_number, body=None, state=None)`；`repo_tracker/client.py` 实现 GitCode 客户端的 `PATCH /repos/{owner}/{repo}/pulls/{id}`；`git_sync.sync()` 末尾在 PR 开完后调一次 `update_pull_request(body=...)` 把 Sub-B 的报告写入 PR body；将原 `_post_run_comment` + `_comment_sync_result` 两条评论合并为一条带报告链接的汇总评论 |
| Sub-D | ProgressReporter 接入 | 修死代码 | `orchestrator.py:329-336` 调 `agent_runner.run(...)` 时把 `progress_reporter` 显式传参；`progress_reporter.ProgressReporter` 把 phase completion 写入 `event_log_dir/1.ndjson`（与 agent_runner 现有 ndjson 通道合并），`issue tail` 可消费 |

### 当前基线

| 能力 | 当前状态 | 说明 |
|------|----------|------|
| commit 前自动跑测试 | ❌ 缺失 | `agent_runner.py` 跑完 LLM 直接 SessionComplete；`git_sync.py` 只 `git add/commit/push`；`workflow.md:110` 写「Run the existing test suite」仅是 LLM prompt 文本，系统不强制 |
| push 前自动跑测试 | ❌ 缺失 | 同上 |
| `HooksConfig` 生命周期点 | ⚠️ 不完整 | 现有 `after_create` / `before_run` / `after_run` / `before_remove` 四点；`config/schema.py:188-193`；缺 `pre_commit` / `pre_push` / `post_sync` |
| AgentConfig / CodexConfig verification 字段 | ❌ 缺失 | `config/schema.py:157-184` 无 `test_command` / `build_command` / `lint_command` |
| `IssueRecord` 报告字段 | ❌ 缺失 | `issue_registry.py:36-58` 字段为 `issue_id/branch_name/commit_sha/pr_number/pr_url/base_branch/status/attempt_count` + 几个 clarification 字段，无 `report_path` / `verification_result` / `test_output` |
| 结构化报告文件 | ❌ 缺失 | `agent_runner.py:440-486` 只写 `.event_logs/{id}.ndjson`（stream events）；`git_sync.py` 不写报告 |
| PR body 动态改写 | ❌ 缺失 | `git_sync.py:264-282 _build_pr_body` 写死静态文本；代码库 0 处 `update_pull_request` / `edit_pull_request` 调用；`tracker.py` 抽象基类未声明该方法 |
| 单条汇总评论 | ❌ 缺失 | `_post_run_comment` (agent_runner) + `_comment_sync_result` (git_sync) 是两条独立评论，reviewer 要跳两处 |
| `progress_reporter` 接入 | ❌ 死代码 | `orchestrator.py:329-336` 调 `agent_runner.run(...)` 不传 `progress_reporter`；模块仅 4 处引用且都是构造参数；PhaseComplete 写到 `ToolContext.tasks.metadata` 后无人读 |

### 实施进度

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | `config/schema.py` 扩展 `HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点 + `AgentConfig` 增 `test_command` / `build_command` / `lint_command`（默认可空） | A | ✅ 完成 |
| 2 | `extensions/orchestrator/git_sync.py` 在 `git commit` 前调 `run_pre_commit_hook`、在 `git push` 前调 `run_pre_push_verification`；失败时抛 `VerificationFailed` / `HookFailedError`，orchestrator 捕获后 issue 标 `verification_failed` 不 push | A | ✅ 完成 |
| 3 | `orchestrator.py` 在 git_sync.sync() 末尾 `finally` 块里调 `run_post_sync_hook(session)`，并把 verification 状态写入 `IssueRecord` | A | ✅ 完成 |
| 4 | `issue_registry.py` 的 `IssueRecord` 新增 `report_path: str \| None` / `verification_status: str \| None` / `verification_output: str \| None` / `summary_comment_id: str \| None` 字段，旧 entry 加载兼容 | B | ✅ 完成 |
| 5 | 新增 `extensions/orchestrator/report_writer.py`，`write(...)` 同步写 `workspace/.reports/{run_id}.md` + `~/.clawcodex/reports/{tracker}/{owner}/{repo}/{issue_id}/{run_id}.{md,json}`，markdown 不含自身路径 | B | ✅ 完成 |
| 6 | `agent_runner.py` SessionComplete 时计算 `run_id = run-{attempt:02d}-{UTC_ts}`，F-39 follow-up 用 `run-N-followup-M-{UTC_ts}`；agent_runner 立刻发 `⏳` placeholder 评论并把 `comment_id` 写回 `session.summary_comment_id` | B/C | ✅ 完成 |
| 7 | `git_sync._build_pr_body` 改模板插值，插入 issue 摘要、branch、commit、diff stat、verification 状态、报告链接（`~/.clawcodex/reports/...` 持久化路径）；审计用 `<!-- metadata: report_path=... -->` HTML 注释单独存 | B | ✅ 完成 |
| 8 | `tracker.py:TrackerAdapter` 增抽象 `update_pull_request(pr_number, *, body=None, state=None) -> PullRequestRef \| None` 与 `update_comment(issue_id, comment_id, body) -> Comment \| None` | C | ✅ 完成 |
| 9 | `repo_tracker/client.py` 增 `RepositoryIssueClient.update_pull_request`（GitHub/Gitee/GitCode PATCH `/repos/{o}/{r}/pulls/{id}`）与 `update_comment`（PATCH `/repos/{o}/{r}/issues/comments/{id}`），Linear GraphQL `updateIssueComment` | C | ✅ 完成 |
| 10 | `git_sync.py` `ensure_pull_request` 拿到 `pr.number` 后调 `tracker.update_pull_request(body=...)` 把 Sub-B 报告回写 PR | C | ✅ 完成 |
| 11 | 合并 `agent_runner._post_run_comment` + `git_sync._comment_sync_result` 为单条 `## ClawCodex Run Summary` 汇总评论（git_sync.sync 末尾调 `tracker.update_comment(session.summary_comment_id, body=完整汇总)`，无 `summary_comment_id` 时 fallback 到 `create_comment`） | C | ✅ 完成 |
| 12 | `orchestrator.py:_run_issue` 显式构造共享 `ProgressReporter(ToolContext(workspace_root=workspace_root))` 并传入 `agent_runner.run(...)` | D | ✅ 完成 |
| 13 | `progress_reporter.py` 在 `PhaseComplete` 时写 `event_log_dir/{id}.ndjson` 追加 `{"type": "phase_complete", "phase": N}` 行（与 agent_runner 既有 schema 兼容，新增字段不替换） | D | ✅ 完成 |
| 14 | 单元测试：`schema.HooksConfig/AgentConfig` 解析新字段；`git_sync` 在 pre_push 失败时不 push；`report_writer` 产物包含必须字段；`update_pull_request` / `update_comment` mock 测被调一次；`LocalTracker.update_comment` 原子替换不残留 `.tmp` | A/B/C | ✅ 完成 |
| 15 | 端到端测试：临时 bare origin + WorkspaceManager 跑批，断言 (1) 报告文件存在 (2) PR body 含报告链接 (3) issue 收到单条汇总评论（含报告/verification/branch/commit） (4) `pre_push` 失败时 PR 不存在 (5) `pre_commit` 改文件后 commit 被 amend | A/B/C/D | ✅ 完成 |

### 验收标准

- agent 一次工具都没调（`tools=0`）时，verification gate 拦截 push，PR 不被创建，issue 标 `verification_failed`。
- `test_command` 默认值为空时该步骤跳过（不破坏已有无测试项目）。
- agent 跑完 issue registry 的 `report_path` 指向一个真实存在的文件；该文件包含 issue 摘要、commit SHA、verification 状态、diff stat。
- PR body 含「Issue / Branch / Commit / Verification / Report」五段，verification 段落根据结果渲染 ✅/❌。
- PR 开完后 issue 收到**一条**汇总评论（合并原 Run Complete + Git Sync 两条）。
- 完整代码库 0 处对 `tracker.update_pull_request` 之外的非 CRUD PR API 调用（保留可审计性）。
- `progress_reporter.ProgressReporter` 在主流程被构造；`issue tail --id N` 能看到 `{"type": "phase", ...}` 事件。

### 风险与约束

- verification gate 默认开在 `pre_push`，失败 = 不 push。需在 `workflow.md` 文档里强调，否则用户以为 push 失败是网络问题。
- `test_command` 跑长任务会拖慢 `max_turns=20` 的 issue 跑批，需提供 `verification.timeout_ms` 配置（默认 600s）。
- GitCode `PATCH /pulls` 的 body / state 字段是否被支持需先打一个 dry-run 验证；不支持则回退为「把报告写到 `workspace/.reports/{id}.md` + 在汇总评论里贴报告全文」。
- `_post_run_comment` 与 `_comment_sync_result` 合并时若平台限流，单条评论可能太长，需提供 `summary.max_comment_chars` 截断。
- `progress_reporter` 接入需不破坏 `event_log_dir/1.ndjson` 现有 schema，扩展字段而非替换。
- 与 F-37 的 PR review follow-up 闭环保持兼容：Sub-C 的 `update_pull_request` 应是 F-37 阶段 5/7（同 PR 分支 follow-up）的基础能力，先于 F-37 落地。

### 已拟定的设计决定（2026-06-01 设计稿审阅产出）

设计稿 7 个 Open Questions 的拟定方案。详细版见 `docs/FEATURE_PLAN.md` → `3.1.5 验证与报告闭环设计` → `拟定的设计决定`。实施时按依赖顺序落地，每完成一组更新本节。

| # | 问题 | 拟定方案 | 涉及 Sub |
|---|------|---------|---------|
| 1 | `ProgressReporter` 接口与设计目标错位（绑死 `ToolContext`） | 拆成「翻译层 + 通道层」：新增 `ProgressSink` 协议（`ToolContextSink` / `NdjsonSink` / `CompositeSink`），`ProgressReporter.__init__` 改收 `sinks: list[ProgressSink]`；orchestrator 根据 `workflow.observability.progress_sinks` 显式构造 | D |
| 2 | Hook 执行上下文未约定 | 固化「Hook Env Contract」表：`ISSUE_ID/IDENTIFIER/BRANCH` 必传；`pre_commit`/`pre_push`/`post_sync` 各自加 `BRANCH_NAME/COMMIT_SHA/PR_NUMBER/PR_URL/VERIFICATION_STATUS`；抽 `_run_named_hook` helper 统一 cwd/env/timeout | A |
| 3 | Hook 失败 vs 测试失败语义重叠 | 配置分层：verification 字段（typed）失败 = `VerificationFailed` + 标 `VERIFICATION_FAILED` 状态；hook 字段（opaque）失败 = `HookFailedError` + 走 `FAILED`/retry；新增 `IssueStatus.VERIFICATION_FAILED` 枚举值与 `mark_verification_failed` 方法；新增 `last_hook_error` 字段 | A |
| 4 | Hook 修改文件的副作用 | verification 字段默认只读（`VerificationCommand(cmd, write=False)`），写工作区后记 WARN；`pre_commit` hook 改文件后 git_sync 自动 `git add -A && git commit --amend`；`pre_push`/`post_sync` 改工作区直接报错 | A |
| 5 | 报告文件生命周期（cleanup 会清掉） | 双层存储：`report_writer.write()` 同步写 `workspace/.reports/{id}.md`（瞬态）+ `~/.clawcodex/reports/{tracker}/{owner}/{repo}/{issue_id}/run-{N}-{ts}.md`（持久）；复用 `before_remove` 钩子作为双写失败的 fallback 复制口；`workflow.reports.retention_days=90` | B |
| 6 | 「报告路径」字段循环引用 | 报告文件内部**不写自身路径**，只写摘要/计数/verification/commit/diff/run_id；路径由 PR body 与汇总评论外部引用；若审计需要可以 HTML 注释 `<!-- metadata: report_path -->` 单独存 | B/C |
| 7 | 配置示例具有误导性（`echo` 永远成功） | 替换为四组示例：典型 Python 项目（`pytest -x -q` + `ruff check .`）/ 无测试项目（空 = 跳过）/ hook 副作用（`black . && isort .` + auto amend）/ 显式 no-op（`"true"`）；所有字段留空等价于 3.1.5 之前行为 | A/B/C |

**实施顺序建议**：1 → (2 + 3) → 4 → 5 → 6 → 7。

### 第二轮审阅补遗（2026-06-01）

针对首轮 7 项之外 5 个未决项的补遗，详细版见 `docs/FEATURE_PLAN.md` → `3.1.5 验证与报告闭环设计（F-38）` → `第二轮审阅补遗（2026-06-01）`。

| # | 项 | 补遗内容 | 涉及 Sub |
|---|----|---------|---------|
| 1 | IssueStatus 枚举 | 新增 `VERIFICATION_FAILED` 枚举值 + `mark_verification_failed()` 方法 + `TERMINAL_STATUSES` 冻结集合（含 `COMPLETED/FAILED/ABANDONED/VERIFICATION_FAILED`）统一终态判断；F-39 `agent:retry` 触发时把 `VERIFICATION_FAILED` 也重置回 `PENDING` | A |
| 2 | 汇总评论时序（Option A） | `agent_runner.SessionComplete` 立刻发 placeholder 评论（含 `⏳ This summary is being prepared...`），把 comment_id 存到 `AgentSession.summary_comment_id`；git_sync.sync 末尾调 `tracker.update_comment(summary_comment_id, body=完整汇总)`；新增 `TrackerAdapter.update_comment` 抽象 + 4 平台实现（GitHub/Gitee/GitCode `PATCH /repos/{o}/{r}/issues/comments/{id}`，Linear GraphQL `updateIssueComment`，LocalTracker ndjson 临时文件 + `os.replace` 原子替换） | C |
| 3 | 重跑 run_id | `run_id` 由 agent_runner 显式构造并传入 `report_writer.write(session, workspace, run_id=...)`；格式 `run-{attempt_count:02d}-{UTC_ts}`；F-39 follow-up 用 `run-N-followup-M-{UTC_ts}` 避免冲突；持久化路径 `~/.clawcodex/reports/{tracker}/{owner}/{repo}/{issue_id}/{run_id}.{md,json}` | B |
| 4 | 文档 ID 一致性 | FEATURE_PLAN.md 节标题已加 `(F-38)` 标识；PROGRESS.md 「规划文档」列已写 `docs/FEATURE_PLAN.md → 3.1.5 验证与报告闭环设计`；设计文档（按主题编排）与跟踪文档（按 F-N 索引）的正常分层，不需要合并 ID 系统 | 文档 |
| 5 | test_command 触发器归属 | `agent.test_command` / `build_command` / `lint_command` 只在 pre_push 阶段跑（不在 pre_commit）；`hooks.pre_commit` 保留 Git 术语不重命名，跑改文件类副作用（formatter + auto amend）；`workflow.md` 注释里说明「pre_commit 改文件 / verification 跑 pre_push」分工；pre_commit amend 失败 → 抛 `HookFailedError("pre_commit", "amend failed: <reason>")` 标 FAILED | A |

**合并实施顺序**：首轮 1 → (首轮 2 + 3 + 补遗 1) → (首轮 4 + 补遗 5) → (首轮 5 + 补遗 3) → 补遗 2 → 首轮 6 → 7。

### 依赖与协同

- **依赖 F-1**：F-38 全部 Sub 都在 Orchestrator 主流程内，依赖现有 `git_sync` / `agent_runner` / `issue_registry`。
- **先于 F-37**：F-37 阶段 5 需要的「同 PR 分支 follow-up 修改」依赖 F-38 Sub-C 的 `update_pull_request` 能力。
- **与 F-36 兼容**：LocalTracker 走 `pending_review` 路径不创建 PR，F-38 Sub-C 在该路径下应跳过 PR body 改写。
- **不破坏 `progress_reporter` 现有 4 个引用点**：Sub-D 接入后，单元测试覆盖原参数接口。

---

## F-39: Orchestrator Issue 重跑入口（label + comment 命令双通道）

**状态**: ✅ 完成（Sub-A~F 全部落地；E2E 阶段 10-11 待真实环境验证）
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.6 Issue 重跑入口设计`
**触发场景**: 2026-06-01 在 `chadwweng/AgentSDK` 跑完 issue #1 后用户想「让 agent 重做」或「在同一 PR 上再改一版」,但当前 orchestrator 4 层防御(内存 `completed` set / IssueRegistry `is_completed` / `has_pr` / `find_pull_request`)只支持「PR 存在 = 已处理」,不支持「关 PR = 重做」语义。用户被迫改 registry.json 或重启 daemon,体验差且易污染主流程。

### 目标

在 `extensions/orchestrator` 引入「重做意图」显式表达通道,与现有 4 层防御并存而非替换:

1. **三种 label 表达重做意图**,orchestrator 轮询时按 label 决定走「重置重跑」还是「同 PR 叠 commit」还是「永久跳过」。
2. **comment 命令**作为 label 的实时替代(原作者/maintainer 触发),适合自动化流水线。
3. **CLI 兜底命令**作为本地调试 / label 不便时的紧急入口。
4. 与 F-37(PR 检视意见自动修复)、F-38(报告回写)对齐,提供「同 PR branch follow-up」入口。

### 三种重做意图的语义矩阵

| Label / 命令 | 语义 | 对本地 IssueRecord | 对远程 PR | 对远程 issue | 对 agent run |
|---|---|---|---|---|---|
| `agent:retry` | 重置 + 重跑整个 issue | 清空 `status` → `pending`,删 `commit_sha` / `pr_number` / `pr_url` / `report_path` | 关闭旧 PR(状态 `closed` `not merged`) | 加 `agent:retry` 自检注释(可选) | 新 workspace、新 agent run |
| `agent:follow-up` | 保留 PR,在同 PR branch 叠 commit | `status` 保持 `completed`,`pr_number` 不变,`attempt_count++` | 不动;`update_pull_request` 走 F-38 Sub-C 入口追加 commit | 不动 | 同 workspace 同 branch,prompt 强调「只处理 follow-up」 |
| `agent:blocked` | 永久跳过该 issue | `status` 写 `abandoned` | 不动 | 加 `agent:blocked` 自检注释 | 永不 launch |

`agent:retry` 与 `agent:follow-up` 互斥:同一 issue 上若同时存在两个 label,以更保守的 `agent:follow-up` 为准(保留 PR 改动证据);若同时存在 `agent:blocked`,直接视为「永久跳过」。

### 现状基线(2026-06-01)

| 能力 | 当前状态 | 说明 |
|---|---|---|
| 内存 `completed` set | ✅ 已实现 | `orchestrator.py:200` 拦截;只在进程生命周期内有效 |
| `IssueRegistry.is_completed` / `has_pr` | ✅ 已实现 | `orchestrator.py:205` 拦截;持久化到 `.clawcodex_issue_registry.json`,daemon 重启不丢 |
| `tracker.find_pull_request` 远程校验 | ✅ 已实现 | `orchestrator.py:265-281` 拦截;只看 PR 是否存在,不看 PR state(open/closed/merged) |
| Tracker 端 issue state 前置检查 | ✅ 已实现 | `orchestrator.py:247-262`;`active_states` 命中才 launch |
| Label 读取 | ❌ 缺失 | `RepositoryIssueClient.fetch_candidate_issues` 未把 labels 透传到 `Issue.labels` 之外的使用方;无 label 驱动的 dispatch 逻辑 |
| Comment 命令解析 | ❌ 缺失 | `RepositoryIssueClient.fetch_new_comments_since` 已实现但 orchestrator 未消费 |
| 重置 API | ❌ 缺失 | `IssueRegistry` 无 `reset_for_retry(issue_id)` / `mark_followup(issue_id)` 方法 |
| 远程 PR 关闭能力 | ❌ 缺失 | `tracker.py:TrackerAdapter` 无 `close_pull_request`;`repo_tracker/client.py` 0 处 `PATCH /pulls` 调用 |
| CLI 兜底命令 | ❌ 缺失 | `cli/issue.py` 有 `review` / `diff` / `inject` 但无 `retry` |
| 限频 / 角色校验 | ❌ 缺失 | comment 命令无 anti-replay / author 校验,易被 LLM 自触发 |

### 子特性拆分

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| A | Label 解析 + 意图分发 | 把 label 映射到「重置/follow-up/跳过」三态 | `tracker.py:TrackerAdapter` 增 `extract_intent_from_labels(labels) -> Intent` 抽象;`repo_tracker/client.py:RepositoryIssueClient.fetch_candidate_issues` 在返回前用 `_OPEN_STATE_ALIASES` 之外的「intent label」识别;`issue_registry.py:IssueRecord` 新增 `intent: Literal["none","retry","followup","blocked"]` + `retry_count: int`;`orchestrator.py:_poll_and_dispatch` 在 `has_pr` 判断之前先看 intent |
| B | 重置重跑 (`agent:retry`) | 清空本地状态 + 关闭远程 PR | 新增 `IssueRegistry.reset_for_retry(issue_id)` 方法;`tracker.py:TrackerAdapter.close_pull_request(pr_number) -> bool` 抽象;`repo_tracker/client.py:RepositoryIssueClient.close_pull_request` 实现 `PATCH /repos/{owner}/{repo}/pulls/{id}?state=closed`;`orchestrator.py` 在 launch 前若 intent=retry,先调 close_pull_request 再 launch |
| C | Follow-up 叠 commit (`agent:follow-up`) | 不开新 PR,复用原 branch | `orchestrator.py` 检测 intent=followup 时,跳过 workspace 创建(复用现有 branch),用上次 run 的报告作为上下文;`git_sync.py:GitSyncService.sync` 加 `mode="followup"` 分支,只 `git commit` + `git push`,不创建新 PR;`IssueRecord.attempt_count++`;依赖 F-38 Sub-C 写新 commit 到 PR body(等 F-38 落地) |
| D | Comment 命令解析 | `/agent retry` `/agent follow-up` 触发 | `tracker.py:TrackerAdapter` 增 `fetch_issue_command_intent(issue_id, since_comment_id) -> Intent | None`;`repo_tracker/client.py` 复用 `fetch_new_comments_since` 拉新评论,正则匹配 `^/agent\s+(retry|follow-up|unblock)`;orchestrator 在 launch 前调用,合并 label 意图与 command 意图(以更保守者为准);comment 触发后由 orchestrator 发 bot 确认评论 `## ClawCodex: 已受理 ${command},下一轮 poll 开始执行` |
| E | CLI 兜底命令 | `issue retry` 提供本地入口 | `cli/issue.py` 增 `add_retry_parser` 与 `_run_retry(registry, args)`;支持 `--mode {reset,followup,unblock}` + `--id` + `--reason`;`IssueRegistry` 增 `unblock(issue_id)` 方法(把 `abandoned` 状态回滚);命令发一条本地 audit 日志 `~/.clawcodex/orchestrator/audit.jsonl` 记录 `{ts, operator, issue_id, mode, reason}` 便于追溯 |
| F | 限频 + 角色校验 | 防滥用 | comment 命令默认要求「issue 作者」或「仓库 maintainer」才能触发;`IssueRecord.retry_count >= max_retries_per_issue(默认 3)` 时即使加 label 也拒绝重置(写一条 `agent:retry-rejected` label + 评论说明);`audit.jsonl` 记 limit 触发 |

### 实施进度

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | `tracker.py:TrackerAdapter` 增 `extract_intent_from_labels` / `close_pull_request` / `fetch_issue_command_intent` 三个抽象 | A/B/D | ✅ 完成 |
| 2 | `repo_tracker/client.py:RepositoryIssueClient` 实现上述三个方法(GitCode 优先,GitHub/Gitee 列 TODO) | A/B/D | ✅ 完成 |
| 3 | `issue_registry.py:IssueRecord` 增 `intent` / `retry_count` / `last_command` / `intent_source` / `command_cursor` 字段;新增 `mark_intent` / `clear_intent` / `reset_for_retry` / `increment_retry_count` / `unblock` 方法 | A/B/E | ✅ 完成 |
| 4 | `orchestrator.py:_poll_and_dispatch` 增 intent 前置判断:label 解析 + comment 命令解析 + 合并;launch 路径根据 intent 分流(reset / followup / skip) | A/C/D | ✅ 完成 |
| 5 | `orchestrator.py` 在 intent=retry 时调 `close_pull_request(pr_number)`,再 launch 新 run | B | ✅ 完成 |
| 6 | `git_sync.py:GitSyncService.sync` 加 `mode="followup"` 参数,走「只 commit/push,不开 PR」分支;orchestrator 把 `session.run_kind="agent_followup"` 走 `_prepare_intent_session` 复用 branch | C | ✅ 完成 |
| 7 | `cli/issue.py` 增 `retry` 子命令,实现 `_run_retry` + `_append_audit_log` + `_resolve_operator`;`~/.clawcodex/orchestrator/audit.jsonl` 写本地审计 | E | ✅ 完成 |
| 8 | `config/schema.py:AgentConfig` 增 `max_retries_per_issue=3` + `allow_anyone_to_retry=False`;`orchestrator.py` 实现 `_is_command_author_eligible` (fail-closed) + `_check_retry_rate_limit` + `_reject_unauthorized_command` + `_post_retry_rejection` + `_log_audit_event`;CLI `--force` 旁路并写高优 audit | F | ✅ 完成 |
| 9 | 单元测试 153 个(`tests/test_orchestrator_f39_{command,retry,followup,intent,retry_cli,ratelimit}.py`);orchestrator 回归 231/231 通过 | A/B/C/D/E/F | ✅ 完成 |
| 10 | 端到端:在 issue #1 上加 `agent:retry` label → 60s 内观察 daemon 日志确认走 retry 路径 → issue 重新 running → 完成后 PR 编号变化 | A/B/C | 📋 待真实环境验证 |
| 11 | 端到端:在 issue #1 上加 `agent:follow-up` label → daemon 检测到后不关 PR,在同 branch 叠 commit → PR 编号不变,commit 数 +1 | C | 📋 待真实环境验证 |

### 实际落地（2026-06-01）

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

### 设计决定（落地记录）

1. **`CommandIntent` 携带 author_login**（F-39 Sub-D→Sub-F 接口扩展）：早期 Sub-D 用 `Command | None` 返类型，Sub-F 角色校验需要 author_login，所以把返回类型升级为 `CommandIntent(command, author_login, comment_id, comment_body)` 数据类，向后兼容通过 `intent.command` 字段读取命令值。
2. **role check fail-closed**（LLM 自触发防护）：`author_login is None` / 空字符串直接拒绝（即使配 `allow_anyone_to_retry=True` 也会放行）；`author_login == "clawcodex"` 永远放行（bot 自己），其余需匹配 `IssueRecord.author_login`（澄清流填的作者）。
3. **`unblock()` 总是清 intent**（不是真 no-op）：docstring 写"非 ABANDONED 时不修改 status"，但 intent/intent_source/last_command 总是清零——保证下次 poll 重新走 `_resolve_intent()`；`retry_count` 不清以维持限频。
4. **CLI `--force` 高优 audit**：`audit.jsonl` 写 `{event: "retry", priority: "high", force: true, retry_count: N, max_retries_per_issue: M, rate_limited: false}`，与正常 retry 区分；`--force` 缺省时 rate-limit 命中写 `{event: "retry_rejected", priority: "high", rate_limited: true}`。
5. **限频边界**：`retry_count < max_retries_per_issue` 放行（默认 3 表示可重试 3 次）；`retry_count >= max` 拒（CLAUDE.md 验收标准 4 描述为"累计触发 4 次后拒绝"——其实是第 4 次触发时 retry_count 已经是 3，命中 3 >= 3 边界，与设计一致）。
6. **审计日志差异**：daemon `_log_audit_event` 与 CLI `_append_audit_log` 字段集略有不同（daemon 写更少字段，CLI 写 retry_count/max_retries/rate_limited），都满足设计文档的最小集 `{ts, operator, issue_id, mode, reason}`；后续可统一字段。
7. **审计日志路径**：`~/.clawcodex/orchestrator/audit.jsonl`（设计文档指定）；测试通过 `patch(_DEFAULT_AUDIT_LOG_PATH, ...)` 重定向到 tmpdir。

### 验收标准

- 用户在 GitCode issue #1 上加 `agent:retry` label 后,**60s 内**(下一轮 poll)daemon 日志输出 `Issue 1 retry intent detected`,issue 状态从 `completed` 回到 `running`,旧 PR 被关闭,新 PR 编号(原 PR 编号 + N)。
- 用户在 issue #1 上加 `agent:follow-up` label 后,daemon 在同 branch 上 commit + push,**不开新 PR**,原 PR 编号不变,commit 数 +1。
- 用户在 issue comment 发 `/agent retry`,且非原作者时,**daemon 拒绝执行**并发评论 `## ClawCodex: 仅 issue 作者或 maintainer 可触发 /agent retry`。
- `agent:retry` 累计触发 4 次(超过 `max_retries_per_issue=3`)后,daemon 拒绝再次 reset,issue 上自动加 `agent:retry-rejected` label,评论中说明「已达到最大重试次数,需人工处理」。
- `clawcodex orchestrator issue retry --id 1 --mode reset --reason "wrong approach"` 立即生效,等价于 label 触发的 reset 路径,audit.jsonl 有一行 `{ts, operator, issue_id, "reset", "wrong approach"}`。
- 重置不污染已有 issue_registry.json 旧 entry schema:加载老 JSON 时 `intent` / `retry_count` 默认值生效。
- 与 F-37 协同:`agent:follow-up` 触发的 follow-up run,行为与 F-37 阶段 6 的「review-fix prompt builder」一致(只改检视意见,不改 issue 范围)。
- 与 F-38 协同:`agent:follow-up` 触发的 follow-up run 完成后,F-38 Sub-C 调 `update_pull_request` 把新 commit / 新 diff stat / 新 verification 结果追加到 PR body 末尾(以 `## ClawCodex Follow-up #N` 段落追加,非覆盖)。

### 风险与约束

- **LLM 自触发风险**:comment 命令必须做 role 校验,否则 LLM 在自动响应里写 `/agent retry` 会自触发。
- **label 互斥冲突**:`agent:retry` + `agent:follow-up` 同时存在时需定义优先级;本期以「更保守 = follow-up」为准,后续可加 `intent_priority` 配置。
- **重置不删 git history**:reset 走「关 PR + 删本地 registry entry」,但 git remote 的 commit/branch 仍存在,这是预期行为(便于审计)。
- **限频与人工 bypass**:CLI 兜底命令的 `--force` 参数可绕过 `max_retries_per_issue` 限频,需写 `audit.jsonl` 高优条目。
- **与 F-37 耦合**:`agent:follow-up` 依赖 F-37 阶段 6 的「review-fix prompt builder」;F-37 未落地时,follow-up 路径退化为「同 branch agent run」(语义较弱的 follow-up)。
- **平台差异**:GitCode `PATCH /pulls?state=closed` 与 GitHub `PATCH /repos/{owner}/{repo}/pulls/{number}` 端点路径不同,需在 `repo_tracker/client.py` 平台分发处分别实现;Gitee / GitHub 暂列 TODO(同 F-38 Sub-C 的处理)。
- **comment 命令回放**:用户编辑老评论(非最新一条)发命令时,应只处理 `created_at > since_comment_id` 的新评论;`fetch_new_comments_since` 已实现该语义,直接复用。

### 配置示例

在 `extensions/orchestrator/workflow.md` front matter 增:

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
    comment_command_required_role: "author_or_maintainer"
    audit_log_path: "~/.clawcodex/orchestrator/audit.jsonl"
```

### 依赖与协同

- **依赖 F-1、F-38 Sub-C**:`close_pull_request` 与 F-38 Sub-C 共享 `PATCH /pulls` 协议层(Sub-C 改 body,F-39 Sub-B 改 state);先于 F-38 落地要冗余实现一次,建议先做 F-38 Sub-C,F-39 复用。
- **与 F-37 强协同**:`agent:follow-up` 路径是 F-37「PR 检视意见自动修复」的 label 入口;F-37 未落地时 follow-up 退化为「同 branch 普通 agent run」。
- **不破坏 F-38 Sub-D**:`progress_reporter` 的 PhaseComplete 写 ndjson 逻辑在 retry 路径下应照常工作(每次新 run 是新的 session)。
- **不破坏 F-36 LocalTracker**:LocalTracker 无远程 PR 概念,`close_pull_request` 在该路径下应 no-op 并打 warning 日志;`issue_registry.unblock` 行为对 LocalTracker 等价(把 `pending_review` / `abandoned` 状态回滚到 `pending`)。
- **与 F-38 Sub-B 报告**:`agent:retry` 触发的重置会清空 `report_path`,F-38 Sub-B 报告不应被复用;`agent:follow-up` 不清空报告,新 report 追加为 `report_path_v{N+1}` 序列(便于历史回溯)。

---

## F-40: ProgressReporter Sink 协议重构

**状态**: 📋 设计完成
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.7 ProgressReporter Sink 协议重构设计`
**触发场景**: 2026-06-01 F-38 Sub-D 落地时把 `ProgressReporter` 接到主流程后,审阅发现三个未解决的设计债:(1) `ProgressReporter` 在 `Orchestrator.__init__` 单例化,`_current_task_id` / `_phase_count` 是实例可变状态,F-39 多 issue 并发跑会竞争;(2) `AgentRunner.run` 只在两处显式构造 `PhaseComplete` 并转发给 `progress_reporter.on_event`,`SessionComplete` 形同虚设,session 结束无进度落点;(3) `_on_phase_complete` 写 `progress = min(phase_count * 25, 100)`,对下游无参考价值。

### 目标

把 `extensions/orchestrator/progress_reporter.py` 从「绑死 `ToolContext` 的单例」重构为「以 `ProgressSink` 协议为最小契约的多消费者可插拔架构」:

1. 每 session 持有独立 sink 实例,状态天然隔离,消除并发竞争。
2. `AgentRunner` 转发全部三类事件 (`PhaseComplete` / `TurnComplete` / `SessionComplete`),sesssion 结束一定有进度落点。
3. 进度计算改用 `WorkflowConfig.phases` 比例 + LLM 显式覆盖,淘汰 `phase_count * 25` 假数据。
4. 引入 `CompositeProgressSink` 扇出,让 F-37 (PR 检视意见自动修复) / F-39 (Issue 重跑) 后续可零侵入注册专用 sink。
5. 保留 `ProgressReporter` 名字为向后兼容 shim,既有测试与调用方不破。

### 子特性

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| A | `ProgressSink` 协议 + 复合实现 | 用 Protocol 替代具体类,支持多消费者 | 新建 `extensions/orchestrator/progress_sink.py`,定义 `ProgressSink` 协议 + `CompositeProgressSink` 扇出类 + 三个回调 `on_phase_complete` / `on_turn_complete` / `on_session_complete` |
| B | `ToolContextProgressSink` 默认实现 | 替代原 `ProgressReporter` 行为,落到 `ToolContext.tasks` | 在 `progress_sink.py` 实现 `ToolContextProgressSink`,签名 `__init__(task_id, context, workflow_phases)`;私有 `_phase_count` 状态;`on_phase_complete` 调 `_progress_report_call` + `_task_update_call`;`on_session_complete` 写终态 (`progress=100` 当 `reason=success`,否则 `None`) |
| C | `AgentRunner` 事件转发改造 | 三个事件全部走 sink,SessionComplete 不再丢 | `agent_runner.py` 把参数 `progress_reporter: Any \| None` 改 `progress_sink: ProgressSink \| None`;在 `SessionComplete` 分支(原 line 293-304)追加 `sink.on_session_complete(event, session)`;在 `max_turns` 路径(line 357-364)同样转发;若有 `TurnComplete` 分支也补齐 |
| D | `Orchestrator` 派发改造 | 取消单例,每 session 独立 sink | `orchestrator.py:125-126` 删除 `self._progress_reporter = ProgressReporter(...)`;在 `_dispatch_issue` / `_run_issue` 路径里为每个 session 新建 `ToolContextProgressSink(task_id, self._progress_context, workflow_phases=self.workflow.phases)`,外层套 `CompositeProgressSink([inner])` 便于后续扩展 |
| E | `WorkflowConfig.phases` 真实进度计算 | 替代 `phase_count * 25` | `src/orchestrator/config/schema.py` 的 `WorkflowConfig` 新增 `phases: list[str] = field(default_factory=list)` 字段;`ToolContextProgressSink._phase_progress(idx)` 按 `(idx+1) / total * 100` 算;`ProgressReportTool` 的 prompt 鼓励 LLM 显式传 `progress` 覆盖自动值 |
| F | `ProgressReporter` shim | 保持向后兼容 | `progress_reporter.py` 改写为兼容层: `on_event(event, session)` 根据 `isinstance` 分发到内部 sink 的三个回调;`set_task_id(task_id)` 创建新 `ToolContextProgressSink`;标记 `@deprecated` |
| G | 测试与回归 | 并发安全 + 三事件覆盖 | 新增并发回归测试: `asyncio.gather` 跑两个 mock session,断言它们的 progress 写入互不串扰;新增 `SessionComplete` 落点测试;保留现有 `_ProgressReporter` stub 测试(stub 走 `on_event` 老 API,shim 兼容) |

### 当前基线

| 能力 | 当前状态 | 说明 |
|------|----------|------|
| 单 `ProgressReporter` 单例 | ⚠️ 存在竞争风险 | `orchestrator.py:125-126` 实例化一次,F-39 多 issue 并发跑 `_current_task_id` 会互相覆盖 |
| `AgentRunner` 转发 `PhaseComplete` | ✅ 已实现 | `agent_runner.py:302-304, 362-364` |
| `AgentRunner` 转发 `SessionComplete` | ❌ 死代码 | `progress_reporter.py:_on_session_complete` 定义但 `AgentRunner` 不发该事件 |
| `AgentRunner` 转发 `TurnComplete` | ❌ 死代码 | 同上 |
| `progress` 真实计算 | ❌ 假数据 | `progress_reporter.py:87` 写死 `min(phase_count * 25, 100)` |
| `WorkflowConfig.phases` | ❌ 缺失 | `config/schema.py` 无此字段 |
| 多 sink 扇出 | ❌ 缺失 | F-37 / F-39 想消费进度事件需要改 `ProgressReporter` |
| `ProgressReporter` 向后兼容 | N/A | 现状即唯一实现,无兼容问题 |

### 实施进度

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | 新建 `extensions/orchestrator/progress_sink.py`,定义 `ProgressSink` Protocol + `CompositeProgressSink` + `ToolContextProgressSink` | A/B | 📋 待开始 |
| 2 | `src/orchestrator/config/schema.py:WorkflowConfig` 新增 `phases: list[str] = field(default_factory=list)` 字段,旧 workflow.md 无 `phases` 时退化为「无 phase 权重,自动上报 `progress=None`」 | E | 📋 待开始 |
| 3 | `extensions/orchestrator/progress_reporter.py` 改写为 shim,内部维护 `ToolContextProgressSink`,`on_event` 走 `isinstance` 分发;`set_task_id` 创建新 sink;`from .progress_sink import ...` 软引用,避免循环 import | F | 📋 待开始 |
| 4 | `extensions/orchestrator/agent_runner.py`: 参数 `progress_reporter` → `progress_sink`;`SessionComplete` 分支与 `max_turns` 路径补 `sink.on_session_complete`;若有 `TurnComplete` 分支也补 `sink.on_turn_complete`;`_write_event_log` 行为不变 | C | 📋 待开始 |
| 5 | `extensions/orchestrator/orchestrator.py:125-126` 删除单例;`_dispatch_issue` / `_run_issue` 中为每个 session 新建 `ToolContextProgressSink` + `CompositeProgressSink`;保留 `_progress_context` 共享 | D | 📋 待开始 |
| 6 | `src/tool_system/tools/progress_report.py` 的 `ProgressReportTool` prompt 增「建议显式传 `progress`」指引;`_progress_report_call` 接受 `progress=None`(已支持,无需改实现) | E | 📋 待开始 |
| 7 | 单元测试: `ToolContextProgressSink` 三个回调直接调;`CompositeProgressSink` 扇出且单 sink 异常不阻塞;`ProgressReporter` shim 的 `on_event` 类型分发;`WorkflowConfig.phases` 解析默认空 | A/B/E/F | 📋 待开始 |
| 8 | 回归测试: `asyncio.gather` 并发跑两个 session,断言各自的 `ToolContext.tasks` 写入互不串扰;`SessionComplete` 落点测试 | G | 📋 待开始 |
| 9 | 更新 `tests/test_orchestrator_agent_runner.py` 的 stub(若依赖 `progress_reporter` kwarg,改为 `progress_sink`);运行 `pytest tests/test_orchestrator_*.py -q` 与 `tests/manual_e2e_f38.py -v -s` 确认不破 | G | 📋 待开始 |

### 验收标准

- 并发跑两个 issue 时,每个 session 的 `ToolContext.tasks[id].metadata.progress_stages` 列表只含本 session 的事件,无串扰。
- `SessionComplete` 触发后,`ToolContext.tasks[id].metadata.current_stage` 含 `session_{reason}`、`metadata.progress` 在 `reason=success` 时为 100、其他情况为 `None`。
- `WorkflowConfig.phases=["analysis", "design", "impl", "test", "review"]` 配置下,完成第 2 个 phase 时 `progress=40`;LLM 显式调 `ProgressReport` 传 `progress=37` 时覆盖自动值。
- `WorkflowConfig.phases` 缺失或为空时,自动 `on_phase_complete` 写 `progress=None`,`StatusDashboard` 显示「Phase N (进度未知)」,而不是误导的 25/50/75/100。
- `ProgressReporter` 类的 `on_event(event, session)` 旧 API 仍可用,内部按 `isinstance(event, PhaseComplete / TurnComplete / SessionComplete)` 分发,现有 stub 测试不修改即可通过。
- `CompositeProgressSink` 内任一 sink 抛异常被独立捕获并 `logger.exception`,不影响其他 sink 接收事件。
- F-37 / F-39 后续接入时,只需在 `Orchestrator._dispatch_issue` 注册额外 sink(`PRReviewAutoFixSink` / `RetryLabelSink`),无需修改 `AgentRunner` 或 `progress_reporter.py`。

### 风险与约束

- **API 改名 breaking**: `AgentRunner.run` 的 `progress_reporter` kwarg 改 `progress_sink` 是字面量破坏,需同步改 `Orchestrator` 调用方与所有 stub 测试。Mitigation: `ProgressReporter` shim 仍可作为 `progress_sink` 传入(只要实现 `on_phase/on_turn/on_session_complete` 三个方法,duck type 即可)。
- **进度从假数据变 `None` 的 UI 退化**: 默认配置下旧用户从「25/50/75/100」退到「未知」。Mitigation: 加 `workflow.observability.progress.fallback_to_phase_step: bool = True` 配置开关(默认保留旧行为),后续再翻 `False`。
- **每个 session 多一个 sink 对象**: 内存增长可忽略(Python 单实例,几 KB),无 perf 风险。
- **事件总线语义变化**: `CompositeProgressSink` 是同步扇出,任意 sink 阻塞会卡住 `AgentRunner` 主循环。Mitigation: 每个 sink 内部 try/except + 短超时;慢消费者应自己 queue + 后台线程。
- **Import 顺序**: `progress_reporter.py` (shim) → `progress_sink.py` (默认实现) → `agent_runner.py` (调用方) 依赖链需保持单向,避免循环 import。建议 `progress_reporter.py` 用 `from .progress_sink import ToolContextProgressSink` 软引用,`TYPE_CHECKING` 保护。

### 进度计算决策表(2026-06-01 设计稿)

| 来源 | 触发时机 | `progress` 值 | 优先级 |
|------|----------|---------------|--------|
| LLM 显式调 `ProgressReport` 工具 | LLM 主动汇报 | LLM 传入的 `progress` | 最高 (覆盖一切) |
| `WorkflowConfig.phases` + 自动 `on_phase_complete` | PhaseComplete 事件 | `(current_idx+1) / total * 100` | 中 |
| 兜底(均无) | PhaseComplete 事件 | `None` (UI 显示「未知」) | 最低 |
| `SessionComplete` 兜底 | 会话结束 | `100` (reason=success) / `None` (其他) | 终态 |

`workflow.observability.progress.fallback_to_phase_step: bool = True` 时,中间档用 `phase_count * 25` 兜底(软迁移期),后续翻 `False` 强推 None。

### 依赖与协同

- **依赖 F-1、F-38 Sub-D**: F-38 已把 `ProgressReporter` 接到主流程,本特性在此基础上重构;不破坏 F-38 验收标准 (`progress_reporter.ProgressReporter` 在主流程被构造 → 改为 `ToolContextProgressSink` 在主流程被构造)。
- **先于 F-37 落地收益**: F-37 (PR 检视意见自动修复) 后续可注册 `PRReviewAutoFixSink` 监听 `on_session_complete` 触发 follow-up run,无需改 `AgentRunner`。
- **先于 F-39 落地收益**: F-39 (Issue 重跑) 后续可注册 `RetryLabelSink` 监听 `on_session_complete` 更新 issue label,无需改 `AgentRunner`。
- **不破坏 F-36 LocalTracker**: LocalTracker 派发的 session 也走相同的 sink 构造路径,`ToolContextProgressSink` 行为对其等价(数据落 `ToolContext.tasks`,不访问远程)。
- **与 F-22 Cron 系统解耦**: Cron 触发的 prompt 不走 orchestrator,sink 链路不被影响。

---

## F-41: Coordinator 轻量工具集

**状态**: ✅ 已完成
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.8 Coordinator 轻量工具集（F-41）`
**落地版本**: v2.7

### 目标

给 Coordinator Agent 配置独立的轻量工具集（Read、WebSearch、WebFetch），使其可直接处理简单查询而非为每个请求创建 Worker，同时确保写操作类工具（Edit、Write、Bash、Grep、Glob）始终隔离。

### 变更清单

| 文件 | 改动 |
|------|------|
| `src/coordinator/mode.py` | `_COORDINATOR_ALLOWED_TOOLS` 新增 `Read` / `WebSearch` / `WebFetch` |
| `src/coordinator/prompt.py` | 提示词 §2 "Your Tools" 列出 Read、WebSearch、WebFetch 的用途说明 |
| `src/repl/core.py` | 注释同步更新 Coordinator 工具列表 |

### 验收标准验证

| # | 标准 | 结果 |
|---|------|------|
| 1 | Coordinator 可调用 `Read` 读取文件 | ✅ 通过 |
| 2 | Coordinator 可调用 `WebSearch` / `WebFetch` | ✅ 通过 |
| 3 | Coordinator **不能**调用 `Bash`、`Write`、`Edit`、`Grep`、`Glob` | ✅ 通过（6 工具精确匹配） |
| 4 | Worker Agent 不受影响 | ✅ 通过 |
| 5 | Prompt 列出正确的 6 个工具 | ✅ 通过 |
| 6 | `filter_coordinator_tools()` 正确过滤 | ✅ 通过 |
| 7 | 231/231 orchestrator 测试通过 | ✅ 通过 |

### 工具隔离矩阵

| 角色 | 可用工具 | 能力边界 |
|------|---------|---------|
| **Coordinator** | Agent / SendMessage / TaskStop / Read / WebSearch / WebFetch | 读文件、搜索、管理 Worker，**不可**执行代码或写文件 |
| **Worker** | 完整工具套件 | 完整的编码与调试能力 |

### 设计决定

1. **模糊名称匹配**：`filter_coordinator_tools` 用 `startswith` > `in` > `inverse in` 三级后备匹配，而不是精确集合成员判断；好处是 Coordinator 不感知工具实例 ID 变化，坏处是名称前缀重叠的工具可能误放行。
2. **提示词手动同步**：`prompt.py` 的 "Your Tools" 列表无自动校验机制保证与 `_COORDINATOR_ALLOWED_TOOLS` 一致，需人工 review。
3. **不涉及 Worker 工具变更**：Coordinator 工具过滤是独立的半透明白名单，与 Worker 的 `filter_worker_tools` 无关。

---

*文档更新时间: 2026-06-01*

*版本 v2.7 更新: 新增 F-41 Coordinator 轻量工具集。扩展 `_COORDINATOR_ALLOWED_TOOLS` 使 Coordinator 获得 Read / WebSearch / WebFetch 三个轻量工具，合计 6 个。写/执行工具仍隔离，强制委派给 Worker。提示词同步更新。231/231 orchestrator 测试通过。*

*版本 v2.6 更新: 修复 `progress_reporter` 死代码,phase completion 接入 ndjson event log (F-38 Sub-D 落地)。新增 F-40 ProgressReporter Sink 协议重构。Sub-A 引入 `ProgressSink` 协议 + `CompositeProgressSink` 扇出;Sub-B `ToolContextProgressSink` 替代原 `ProgressReporter` 行为;Sub-C `AgentRunner` 三个事件全部转发,session 结束有进度落点;Sub-D `Orchestrator` 取消单例改为每 session 独立 sink;Sub-E `WorkflowConfig.phases` 做真实进度计算,淘汰 `phase_count * 25` 假数据;Sub-F `ProgressReporter` 降级为 shim 保持向后兼容;Sub-G 并发回归 + 三事件测试覆盖。*