# ClawCodex 开发进度跟踪文档

> 文档路径: `docs/PROGRESS.md`
> 基于: `docs/open-source-replacement-progress.md`, `docs/FEATURE_PLAN.md`
> 版本: v2.15
> 更新日期: 2026-06-03
> 上游同步: 58ea488 (dev-decoupling-refactor)
>
> **v2.15 变更**：F-22 Cron 系统 Phase A runtime-first 接线完成（✅ 已完成）。三层打通：`clawcodex_ext/runtime/context.py` 中 `RuntimeContext.build()` 调用 `attach_cron_runtime(tool_context, autostart=True)` 启动后台 cron 调度器；`src/repl/core.py` 中 `ClawcodexREPL.__init__()` 注册 `replace_cron_tools()` 替换 fallback 工具 + `attach_cron_runtime()` 启动调度器；新增 `_drain_cron_outbox()` 每条迭代前从 `tool_context.outbox` 弹出 `cron_prompt`/`cron_missed` 事件，经 `_enqueue_prompt` 注入为自动用户输入提交 `chat()`。Headless/TUI 通过 `RuntimeContext.build()` 共用同一路径，调度器已在后台运行（TUI 循环的 outbox drain 尚未接线，属后续阶段）。F22-R1 标记为 ✅ 完成，其余 F22-R2~R8 保持进行中。271/271 orchestrator 测试全部通过。v2.9 的 "剩余 P0 缺口列表" 更新为反映 R1~R8 口径。
>
> **v2.14 变更**：新增 F-48 src/ 核心路径二开修改解耦方案（📋 设计完成）。通过对比 `src/` 与 `src/upstream/58ea488/`，识别出 10 个含真正功能修改的 src/ 文件（其余 600+ 为行尾/格式差异），分 Phase 0~3 四阶段制定解耦方案：Phase 0（纯新增文件移入 ext）、Phase 1（注册表/Protocol 扩展消除字段注入）、Phase 2（子类覆盖恢复上游构造器签名）、Phase 3（入口点恢复上游逻辑）。复用已有 Facade/子类覆盖/前端注册表三种解耦模式。目标：src/ 有功能修改的文件数从 10+ 降为 0。
>
> **v2.13 变更**：新增 F-43 实现 + F-45 / F-46 / F-47。F-43 P1 落地 `clawcodex provider` / `clawcodex model` 子命令族（list/show/current/use/unset）+ REPL/TUI 内 `/provider` / `/model` 斜杠命令；新增 fast-path `subcommand_registry` + `ModelRegistry` / `ModelStore` / `Resolver` + `RuntimeContext.swap_provider` 热切换；所有新代码落在 `clawcodex_ext/cli/`，`src/*` 仅追加 `CommandContext.runtime_context` seam 与 `TUIOptions.runtime_context` 透传。F-45 P1 在 `agent_runner._handle_tool_call` 后加 NDJSON 旁路落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，与 permission_mode 解耦；扩展 `report_writer.RunReport.tool_events_path` 字段 + markdown 模板登记路径；终结 "bypass ≠ 无审计" 误读。F-46 P2 把 `permission_mode` enum 拆为 `interactive` / `default_decision` / `audit_log` 三个正交字段，F-46.0（v2.13）只拆 `audit_log`，依赖 F-45 落地后端到端验证；`permission_mode` 保留为 backward-compat shim 标 deprecated；F-46.1+ 拆其余两字段推到 v2.15+。F-47 P1 修 `SettingsSchema.permissions` schema 形状（`list[PermissionRule]`）与磁盘 dict 形态不一致 / `has_allow_bypass_permissions_mode` 永远读不到 / `resolve_permission_state` 没传 `settings_default_mode` / 顶层 `settings.permission_mode` 字段未读 四个串联 bug；引入 `PermissionsConfig` dataclass 对齐磁盘 + TS 上游契约，让 `permissions.defaultMode` 与 `permissions.allowBypassPermissionsMode` 真正生效；删除 settings 层"假" `PermissionRule` 死代码。**F-47.1 (2026-06-02) v2.13 hotfix：在项目尚未发布的前提下直接删除 F-47 原本保留的顶层 `settings.permission_mode` back-compat 读取通道**——`SettingsSchema.permission_mode` 字段保留为兼容形态但启动时不再被读，F-46.2 的 deprecation 步骤因此 N/A。
>
> **v2.12 变更**：新增 F-43 CLI 模型供应商与模型切换（📋 设计完成）。规划 `clawcodex provider` / `clawcodex model` 子命令族（list/show/current/use/unset）+ REPL/TUI 内 `/provider` / `/model` 斜杠命令，覆盖查看、列出、切换当前生效的 LLM 供应商与模型。所有新代码落在 `clawcodex_ext/cli/` 下，遵守 "src/* 不动" 边界；持久化借道 `src.config`，不重写 I/O；错误文案统一英文。`--scope project` 落入后续规划。
>
> **v2.10 变更**：新增 F-42 Orchestrator Shared / Sequential Workspace 策略（📋 设计完成）。规划 `workspace.strategy: isolated | shared | sequential`，用于支持本地 feature-plan issue 在同一 working tree / integration branch 上按顺序叠加开发并保留每 issue 一个 commit；设计范围覆盖 WorkspaceManager、Orchestrator 并发校验、dirty tree guard、顺序锁、registry commit 链元数据、GitSync/cleanup 行为与端到端验收标准。
>
> **v2.9 变更**：补充 F-22 Cron 系统相对 `claude-code-best` 的最新缺口复核结论。`clawcodex_ext/cron_system/` 已覆盖 parser/storage/scheduler/jitter/lock/permanent/inFlight/基础 runs/status 等底层能力，历史 9.11 CCB 补充缺口 G1~G8 不再作为主要缺口；F-22 继续保持进行中，剩余 P0 缺口集中在真实 REPL/TUI/headless 运行路径接线、scheduled fire 执行队列、run lifecycle finalize、`/cron-list`/`/cron-delete`/trigger detail/manual fire/autonomy status 用户入口、busy gate/filter/teammate ownership 与 durable 文件变更 reload 行为。
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

> 状态为 ✅ 完成 / ✅ 基础完成的项（含 F-1、F-3、F-13、F-14、F-15、F-17、F-19、F-20、F-21、F-23、F-24、F-25、F-27、F-29、F-30、F-31、F-32、F-34、F-36、F-38、F-39、F-41、F-42、F-43、F-45、F-47）详细设计与进度已归档；本文仅保留概览与链接，详见 [ARCHIVED_PROGRESS.md](./ARCHIVED_PROGRESS.md) 与 [ARCHIVED_FEATURES.md](./ARCHIVED_FEATURES.md)。

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
| F-19 | POS to Agent 转化模式 | P2 | 🔄 进行中 | 三层映射（POS→Agent、workflow→Skill、SDK→工具），SDK 解析 + Skill 分组 + Agent 构建 + 持久化已完成；**`clawcodex-dev pos convert` CLI 子命令待注册**（dispatch.py/subcommand_registry.py 中未实现），当前仅支持斜杠命令和 Python API 调用 |
| F-20 | Agent 阶段性进度汇报 | P2 | ✅ 完成 | 三组合方案：检查点触发 + ProgressReportTool + ToolContext.tasks 持久化；PhaseComplete 时双重调用 ProgressReportTool + TaskUpdateTool 更新 metadata |
| F-21 | 后台运行 + 恢复同步 | P1 | ✅ 完成 | Ctrl+B 后台化 + TailFollower 实时同步 + SessionWatcher 多终端感知，补丁 0067-0074 |
| F-22 | Cron 系统执行引擎 | P0 | 🔄 进行中（Phase A ✅ 已完成） | `clawcodex_ext/cron_system/` 已补齐 parser/storage/scheduler/jitter/lock/permanent/inFlight/基础 runs/status；历史 G1~G8 已落地；Phase A runtime-first 接线完成——REPL/TUI/headless 均通过 `RuntimeContext.build()` 获得后台 cron 调度器，REPL 新增 `_drain_cron_outbox()` 将 `tool_context.outbox` 中的 `cron_prompt`/`cron_missed` 事件经 `_enqueue_prompt` 注入为自动用户输入。剩余缺口保持 F22-R2~R8：scheduled fire 执行队列、run lifecycle finalize、用户管理/status 入口、busy gate/filter、durable reload、teammate ownership 与 CCB env gate 兼容。 |
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
| F-36 | LocalTracker 本地 Issue 文档源 | P1 | ✅ 完成 | 新增 `tracker.kind: local`，从本地 Markdown/JSON issue 文档读取待处理任务，支持离线测试与私有本地工作流 |
| F-37 | Orchestrator PR 检视意见自动修复闭环 | P0 | 📋 设计完成 | 将 PR 网页检视意见、inline comments、review summary 与 CI 失败日志转化为 follow-up agent run，自动修改同一 PR 分支并提交更新 |
| F-38 | Orchestrator 验证与报告闭环（verification + report → PR） | P0 | ✅ 完成 | commit/push 前自动跑 verification gate（pre_push hook + test_command），agent 跑完写结构化报告，git_sync 用报告改写 PR body 并合并为单条 issue 汇总评论；进度由 dead-code `progress_reporter` 接入主流程 |
| F-39 | Orchestrator Issue 重跑入口（label + comment 命令双通道） | P0 | ✅ 完成（Sub-A~F） | 三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发、Sub-B 重置重跑、Sub-C follow-up 叠 commit、Sub-D comment 命令解析、Sub-E CLI 兜底、Sub-F 限频+角色校验均已落地；端到端 10-11 阶段（实际 GitCode/GitHub issue 联动）待真实环境验证 |
| F-40 | ProgressReporter Sink 协议重构 | P1 | 📋 设计完成 | 把 `Orchestrator` 上 `ProgressReporter` 单例拆为每 session 独立的 `ProgressSink` 实例；新增 `CompositeProgressSink` 扇出支持 F-37/F-39 零侵入接入；补全 `SessionComplete` / `TurnComplete` 转发；引入 `WorkflowConfig.phases` 做真实进度计算，淘汰 `phase_count * 25` 假数据 |
| F-41 | Coordinator 轻量工具集 | P1 | ✅ 已完成 | 给 Coordinator 配置独立的轻量工具集（Read、WebSearch、WebFetch），加上原有的 Agent、SendMessage、TaskStop，共 6 个工具。Coordinator 可直接处理简单查询（搜网页、读文件），无需为每个请求创建 Worker。所有写操作工具（Write、Edit、Bash、Grep、Glob）仍隔离，强制委派复杂任务给 Worker。涉及 `src/coordinator/mode.py` 的 `_COORDINATOR_ALLOWED_TOOLS` 扩展 + `src/coordinator/prompt.py` 的 "Your Tools" 提示词更新 + `src/repl/core.py` 注释同步。231/231 orchestrator 测试通过 |
| F-42 | Orchestrator Shared / Sequential Workspace 策略 | P0 | ✅ 完成 | 扩展 `workspace.strategy: isolated \| shared \| sequential`，保留现有 per-issue workspace，同时支持本地 feature-plan issue 在同一 working tree / integration branch 上串行叠加开发；包含单并发校验、顺序锁、dirty tree guard、每 issue commit 链元数据、shared cleanup preserve 与两 issue 端到端验收 |
| F-43 | CLI 模型供应商与模型切换 | P1 | ✅ 已完成 (2026-06-02) | 新增 `clawcodex provider` / `clawcodex model` 子命令族（list/show/current/use/unset）+ REPL/TUI 内 `/provider` / `/model` 斜杠命令；fast-path 注册表 + `ModelRegistry` / `ModelStore` / `Resolver` + `RuntimeContext.swap_provider` 热切换；所有新代码落在 `clawcodex_ext/cli/`，`src/*` 仅追加 `CommandContext.runtime_context` seam 与 `TUIOptions.runtime_context` 透传；持久化借道 `src.config` 不重写 I/O；错误文案统一英文；`--scope project` 落入后续规划 |
| F-44 | Orchestrator 人工检视闸门（Review Gate） | P1 | ✅ 完成 | 可选的人工检视闸门，`workflow.md` 中 `agent.review_required: true` 启用。sync 后有代码变更时标记 `PENDING_REVIEW` 而非直接 `COMPLETED`；CLI `issue review --approve/--reject` 审批或驳回；驳回自动触发 F-39 retry。向后兼容，默认关闭。 |
| F-45 | Orchestrator tool-call 审计旁路（tool-events.ndjson + 报告登记） | P1 | ✅ 已完成 (2026-06-02) | 在 `extensions/orchestrator/agent_runner.py:_handle_tool_call` 之后追加 NDJSON 旁路落盘到 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，与 `permission_mode` 解耦（`bypassPermissions` / `dontAsk` / `acceptEdits` / `default` 一视同仁全写）；扩展 `report_writer.RunReport` 加 `tool_events_path` 字段并在 markdown 模板登记路径，让审计员从 run 报告直接定位完整 per-tool 决策流水。修复 TS 注释 "bypass = no logging" 在 Python 端的事实偏差 —— `ApprovalPolicy` 一直在跑，只是决策没落盘。落地时同步修复了 `_handle_tool_call` 死代码调用链 + 加 50MB rotate + `.reports` 进默认 gitignore。16 个新测试 + 全 271 回归 + F-38 E2E 4/4 全绿。 |
| F-46 | permission_mode enum 正交拆分 | P2 | ⏳ 规划中 | 把 `permission_mode` 混合 enum（`default` / `plan` / `bypassPermissions` / `acceptEdits` / `dontAsk` / `auto` / `bubble`）拆为三个正交字段：`interactive: bool`（是否要 TTY 弹 prompt）、`default_decision: Literal["allow", "deny", "ask"]`（无人值守默认）、`audit_log: Literal["none", "minimal", "full"]`（per-tool 决策是否落盘）。F-46.0（v2.13）只拆 `audit_log`，**F-45 已落地**可消费 NDJSON 旁路做端到端验证；`permission_mode` 保留为 backward-compat shim 标 deprecated；F-46.1+ 拆其余两字段推到 v2.15+。 |
| F-47 | Permission Settings Schema 重构（`permissions` 改 dict 形态 + plumb 启动模式） | P1 | ✅ 完成（含 F-47.1 hotfix） | 修四层串联 bug：`SettingsSchema.permissions: list[PermissionRule]` 与磁盘实际 dict 形态不一致 → dict 落进 known 字段，`allowBypassPermissionsMode` 进不到 `extra` → `has_allow_bypass_permissions_mode` 永远 False → Shift+Tab cycle 看不到 Bypass；同时 `resolve_permission_state` 没把 `permissions.defaultMode` 喂给 `initial_permission_mode_from_cli`、顶层 `settings.permission_mode` 字段未读。引入 `PermissionsConfig` dataclass 对齐磁盘 + TS 上游契约；`has_allow_bypass_permissions_mode` 加 `extra["permissions"]` fallback；`resolve_permission_state` 真正 plumb `settings_default_mode`；删除 settings 层"假" `PermissionRule` 死代码。**F-47.1 (2026-06-02) hotfix**：项目尚未发布、磁盘上没有需要迁移的旧配置，直接删除原本保留的顶层 `settings.permission_mode` back-compat 读取通道——`SettingsSchema.permission_mode` 字段保留为兼容形态但启动时不再被读；详见 风险 #3 / 设计决定 #3 / F-47.1 备注。 |
| F-48 | src/ 核心路径二开修改解耦 | P0 | 📋 设计完成 | 将 `src/` 中 10 个含真正功能修改的文件解耦到 `clawcodex_ext/` 和 `extensions/`，使 `src/` 与上游源码（`src/upstream/58ea488/`）功能层面一致。分 Phase 0~3：Phase 0（纯新增文件移入 ext）、Phase 1（注册表/Protocol 扩展消除字段注入）、Phase 2（子类覆盖恢复上游构造器签名）、Phase 3（入口点恢复上游逻辑）。复用 Facade/子类覆盖/前端注册表三种解耦模式。目标：src/ 有功能修改的文件数从 10+ 降为 0 |
| F-49 | Issue 会话统一存储与实时介入协议 | P1 | 📋 设计完成 | 将 headless agent 的 `.event_logs/` 扁平 NDJSON 统一为 `SessionStorage` 的 `transcript.jsonl` 格式；在其上建立 Unix socket 双向控制协议，实现 `attach` CLI 观察/中断/接管/恢复；附带 session 恢复能力。Phase 0 存储统一 → Phase 1 socket 控制 → Phase 2 attach TUI → Phase 3 session 恢复 |
| F-51 | AgentRunner 空转检测机制（no-op detection） | P0 | ✅ 完成 | 在 `extensions/orchestrator/agent_runner.py` 中添加连续 5 轮工作区文件无变更检测，防止 agent 在 issue deliverables 已存在的场景下陷入无限 busy-work 循环。对应 PR 检视意见自动修复闭环（F-37）中的已修复前置问题。|

---



## F-22: Cron 系统执行引擎

**状态**: 🔄 进行中（Phase A runtime-first 接线 ✅ 已完成：REPL/TUI/headless 运行路径打通，调度器后台运行，REPL 主循环通过 `_drain_cron_outbox()` 消费 `cron_prompt`/`cron_missed` 事件；Phase B~F 分阶段推进）
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
| fallback Cron 工具定义 | `src/tool_system/tools/cron.py` | ✅ 保留 fallback | 仅提供内存型 CronCreate/CronList/CronDelete；真实 cron 产品化路径应由 extension runtime 替换为 `clawcodex_ext/cron_system/tools.py`。 |
| /loop Skill | `src/skills/bundled/loop.py` | ✅ 完成 | 4种模式（fixed-prompt/fixed-maintenance/dynamic-prompt/dynamic-maintenance）。 |
| cron parser / next-run | `clawcodex_ext/cron_system/parser.py` | ✅ 基础完成 | 已覆盖 5 字段 cron 解析、范围/步进/list、DOW Sunday alias、DOM/DOW OR 语义与 next fire 计算。 |
| cron tasks storage | `clawcodex_ext/cron_system/tasks.py` | ✅ 基础完成 | `.claude/scheduled_tasks.json` durable 存储、session store、CRUD、due/missed 查找、permanent 幂等安装与 fired 标记。 |
| cron task lock | `clawcodex_ext/cron_system/lock.py` | ✅ 基础完成 | `.claude/scheduled_tasks.lock` 调度锁、PID/session 检查、stale/corrupt recovery 与注册式清理。 |
| cron scheduler | `clawcodex_ext/cron_system/scheduler.py` | ✅ 基础完成，待接线 | 1 秒轮询、lock ownership、due/missed/expired、jitter、kill switch、inFlight 和事件 hook 已有；仍需接入真实 frontend queue 与 busy/filter 语义。 |
| cron jitter config | `clawcodex_ext/cron_system/{models,jitter}.py` | ✅ 基础完成 | 6 参数 jitter 配置、文件/env 热加载、recurring forward jitter、one-shot backward jitter 与过期 max-age。 |
| extension Cron tools | `clawcodex_ext/cron_system/tools.py` | ✅ 基础完成，待入口验证 | 替换版 CronCreate/List/Delete 已支持持久化、disabled 软返回、prompt 指引和 permanent 写保护；仍需端到端证明 REPL/TUI/headless 都命中该实现。 |
| runtime glue | `clawcodex_ext/cron_system/runtime.py` | ✅ 完成，已接线 | 可替换 fallback 工具、挂载 scheduler、写入 outbox；REPL 主循环通过 `_drain_cron_outbox()` 消费 outbox 并进入真实 query pipeline。 |
| runs.py | `clawcodex_ext/cron_system/runs.py` | ⚠️ 基础完成，待扩展 | 已有 `.claude/scheduled_task_runs.json` 账本和 queued/running/completed/failed/cancelled 生命周期；缺少 autonomy-compatible 字段、真实执行队列 claim/finalize 接线、`.claude/autonomy/runs.json` 等价布局决策。 |
| status.py | `clawcodex_ext/cron_system/status.py` | ⚠️ 基础完成，待扩展 | 已有 status/runs 文本表格；缺少 deep status 的 richer section、trigger detail、manual-fire run id outcome、错误摘要/路径/来源字段展示。 |
| queue lifecycle | REPL/TUI/headless adapter | ⚠️ 基础完成（REPL 已接线，TUI 待续） | REPL 通过 `_drain_cron_outbox()` 已接通 scheduled fire 入队路径；claim running、最终 finalize 与 active-source 去重依赖 F22-R2。 |
| trigger detail / manual fire | command/skill adapter | ❌ 待实现 | 暴露等价 `/schedule get <id>` 与 `/schedule run <id>` 的用户路径，展示 last/next run、created、prompt，并在手动触发后返回 run id。 |
| autonomy commands | command/skill adapter | ⚠️ fast-path 存在，待接线 | `clawcodex_ext/cli/dispatch.py` 已有 `autonomy status/runs` 分发；仍需接入真实运行账本和 richer output，区分 cron job 定义、trigger detail 与 run 生命周期。 |
| missed notification | extension notification adapter | ⚠️ 基础完成，待产品化 | scheduler/runtime 已能产生 missed notification outbox 事件；仍需前端展示与端到端验收。 |

**CCB 对比分析发现的补充子任务（2026-06）**:

| 子任务 | 文件 | 状态 | 说明 |
|--------|------|------|------|
| G1: isKilled 运行时 kill 开关 | `clawcodex_ext/cron_system/{models,scheduler,tools,runtime}.py` | ✅ 完成 | `is_cron_disabled()` + `CLAWCODEX_DISABLE_CRON` env；`CronScheduler.is_killed` 每 tick 轮询；工具层返回 `{disabled: true, message: "Cron is disabled"}`；runtime 接线 `is_killed=is_cron_disabled` |
| G2: 远程 Jitter 实时配置 | `clawcodex_ext/cron_system/{models,jitter,scheduler,tasks,runtime}.py` | ✅ 完成 | 6 参数 `CronJitterConfig`（recurring_frac/recurring_cap_ms/one_shot_max_ms/one_shot_floor_ms/one_shot_minute_mod/recurring_max_age_ms）；`load_jitter_config()` 支持 `.claude/cron_jitter_config.json` + `CLAWCODEX_CRON_*` env，热加载（env > 文件 > 默认）；`validate_jitter_config` 防御性夹紧；`CronScheduler.check_once` 每个 tick 调用 loader 并把 `recurring_max_age_ms` 传入 `prune_expired_recurring_tasks(max_age_ms=...)`；`max_age_ms=0` 关闭过期（对齐 CCB `recurringMaxAgeMs=0`） |
| G3: One-shot 反向 Jitter | `clawcodex_ext/cron_system/jitter.py` | ✅ 完成 | `one_shot_jittered_next_cron_run_ms` 走 `minute % one_shot_minute_mod == 0` 门槛（默认 30 → :00/:30）；落入门槛时 `lead = floor + frac * (max - floor)`（默认 floor=0, max=90s），由 `taskId` sha256 决定；非整点分钟直接返回精确时间；不会早于 `created_at`（`max(t1 - lead, fromMs)`） |
| G4: Permanent 免过期机制 | `clawcodex_ext/cron_system/{models,tasks,tools,runtime}.py` | ✅ 完成 | `CronTask.permanent` 字段；`write_permanent_task_if_missing(cron, prompt)` 幂等安装（同 spec 返回 existing，已存在异 spec 抛 `PermissionError`）；`prune_expired_recurring_tasks` 跳过 `permanent=True`；`CronCreate` 拒绝 `permanent=true` 并报 `ToolInputError`；runtime 暴露 `install_permanent_cron_tasks(workspace_root, [specs])` 供 assistant installer 接入 |
| G5: 锁注册式清理与 PID 增强 | `clawcodex_ext/cron_system/lock.py` | ✅ 完成 | `register_lock_cleanup(callback)` + `release_all_locks()` + atexit/SIGTERM/SIGINT 钩子；`_default_pid_validator` 读 `/proc/<pid>/comm` 识别 clawcodex/claude/python 进程，`set_pid_validator()` 测试覆盖；PID 存活但非 ClawCodex 进程时识别为 PID 回收并强制 unlink；`CronTaskLock.acquire` 支持同 `sessionId` 接管（refresh in place），不同 sessionId 被活锁挡回 |
| G6: 工具 Prompt 指引增强 | `clawcodex_ext/cron_system/tools.py` | ✅ 完成 | `CRON_CREATE_PROMPT` 覆盖 5 字段 cron 语法、jitter 原理（recurring forward / one-shot backward lead）、recurring/one-shot 区别、durable vs session、`permanent` 系统字段说明、50 job 上限、disabled 软返回；`CRON_LIST_PROMPT` 说明字段+permanent 提示；`CRON_DELETE_PROMPT` 提示先 `CronList` 取 id 并强调不可逆 |
| G7: Analytics 遥测事件预留 | `clawcodex_ext/cron_system/scheduler.py` + `runtime.py` | ✅ 完成 | `CronScheduler` 暴露 `on_fire_event` / `on_missed_event` / `on_expired_event` 三个 `Callable[[dict], None]` 钩子，默认 `_noop_event`；`check_once`/`notify_missed_once` 在 fire / missed 路径注入；runtime 默认接 `_log_event` 走 `logging.debug`；不引入新依赖 |
| G8: inFlight 防重复触发 | `clawcodex_ext/cron_system/scheduler.py` | ✅ 完成 | `_in_flight: set[str]` + `_in_flight_lock: threading.Lock`；`check_once` 在 fire 路径上 `add → create_queued_run → fire → remove`（finally 块保证异常路径也释放）；`process` 开头 `if self._in_flight_contains(task.id): continue` 防止 tick 重入时二次触发；并发 100 线程 x 50 taskID 验证无丢无重 |
| A1~A5: 已有优势特性保持 | 全模块 | ✅ 已存在 | CronRun 追踪/手动触发/状态展示/英文名支持/详情输出——9.11 实施未破坏既有行为 |

**最新 CCB 对比后仍需补齐的端到端缺口（2026-06）**：

| ID | 缺口 | 当前状态 | 进度口径 |
|----|------|----------|----------|
| F22-R1 | 真实 REPL/TUI/headless 运行路径接线 | ✅ 完成 | `attach_cron_runtime()` 所有前端路径已接线。REPL (`src/repl/core.py`)：`__init__` 注册 `replace_cron_tools()` + `attach_cron_runtime(autostart=True)`；`run()` 循环新增 `_drain_cron_outbox()` 消费 `tool_context.outbox` 中的 `cron_prompt`/`cron_missed` 事件，经 `_enqueue_prompt` 注入为自动用户输入。Headless/TUI：通过 `RuntimeContext.build()` 自动获得后台 cron 调度器。TUI 循环的 outbox drain 尚未接线，属后续阶段。 |
| F22-R2 | scheduled fire 执行队列 | ⏳ 待开始 | 到期任务需要创建 queued prompt/run，并由普通 query pipeline claim、执行、取消和失败收敛；不能只停留在 scheduler callback。 |
| F22-R3 | run lifecycle finalize 与更完整账本 | ⏳ 待扩展 | `runs.py` 已有基础状态，但还要补齐 started/ended/error/root/current/source/prompt preview/ownership 等字段，并把执行结果 finalize 到 completed/failed/cancelled。 |
| F22-R4 | 用户管理与状态入口 | ⏳ 待扩展 | 需要 `/cron-list`、`/cron-delete`、trigger detail、manual fire、`/autonomy status|runs|status --deep` 或等价命令接到真实账本，而不是只保留工具层或 fast-path。 |
| F22-R5 | busy gate / assistant/headless/filter 语义 | ⏳ 待开始 | 需要对齐 `claude-code-best` 的 `isLoading`、assistantMode、filter 语义，避免繁忙时重入、headless 无法路由时静默丢任务。 |
| F22-R6 | durable 文件 reload 行为 | ⏳ 待确认 | 已有文件存储和锁，但还需端到端验证多会话/外部编辑 `.claude/scheduled_tasks.json` 后 scheduler 热加载与稳定性。 |
| F22-R7 | teammate/agent ownership | ⏳ 待设计 | CCB task schema 有 `agentId`；ClawCodex 需要决定 coordinator/team cron ownership、可见性、路由和失败策略。 |
| F22-R8 | CCB-compatible gate 命名与用户心智 | ⏳ 待确认 | 当前主要使用 `CLAWCODEX_DISABLE_CRON`；若用户从 CCB 迁移，建议兼容 `CLAUDE_CODE_DISABLE_CRON` 或在文档/CLI 中明确差异。 |

### 里程碑

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | cron parser / next-run - 表达式解析与时间计算（extension 路径） | ✅ 基础完成 |
| 2 | cron tasks - durable/session 任务存储 CRUD（extension 路径） | ✅ 基础完成 |
| 3 | cron task lock - 多进程调度锁与清理（extension 路径） | ✅ 基础完成 |
| 4 | cron scheduler - 轮询、due/missed/expired、inFlight、jitter（extension 路径） | ✅ 基础完成 |
| 5 | cron jitter config - 文件/env 动态配置与每 tick reload（extension 路径） | ✅ 基础完成 |
| 6 | tools / command adapter - CronCreate/List/Delete 替换 fallback 工具，补齐 `/cron-list`、`/cron-delete` 用户入口 | ✅ 完成（工具替换已接线，用户入口待细节验证） |
| 7 | runs.py - scheduled-task run 账本扩展到 autonomy-compatible schema 与 active source 去重 | ⏳ 待扩展 |
| 8 | queue lifecycle - scheduled fire 入队、claim running、finalize completed/failed/cancelled | ⏳ 待开始 |
| 9 | trigger detail/manual fire - 单任务详情与手动触发 run id 回显 | ⏳ 待开始 |
| 10 | status.py / autonomy commands - `/autonomy status`, `/autonomy runs`, `/autonomy status --deep` 或等价命令的 richer output | ⏳ 待扩展 |
| 11 | busy gate/filter/headless routing - 繁忙门控、assistant/headless/filter 与无法路由失败记录 | ⏳ 待开始 |
| 12 | durable reload / ownership / env compatibility - 文件热加载验证、teammate/agent ownership、`CLAUDE_CODE_DISABLE_CRON` 兼容 | ⏳ 待设计 |
| 13 | 测试覆盖 - cron job 管理、trigger detail/manual fire、run 生命周期、状态查看、headless 失败记录、多会话 reload | ⏳ 待开始 |
| **G1** | **isKilled 运行时 kill 开关** - scheduler 每 tick 轮询 `is_killed()`，工具 prompt 门控 | ✅ 完成 |
| **G2** | **远程 Jitter 实时配置** - 6 个参数可配置文件/env 热加载，每 tick 重新读取 | ✅ 完成 |
| **G3** | **One-shot 反向 Jitter** - 整点 (:00/:30) 提前触发，确定性 hash 偏移，min/max 保护 | ✅ 完成 |
| **G4** | **Permanent 免过期机制** - 字段/写保护/过期豁免/assistant 安装入口 | ✅ 完成 |
| **G5** | **锁注册式清理与 PID 增强** - atexit 清理、PID 分身检测、同 session 锁接管 | ✅ 完成 |
| **G6** | **工具 Prompt 指引增强** - CronCreate/List/Delete 的 prompt 字段补充最佳实践说明 | ✅ 完成 |
| **G7** | **Analytics 遥测事件预留** - fire/missed/expired 事件点预留 Optional[Callable] | ✅ 完成 |
| **G8** | **inFlight 防重复触发** - 异步 IO 期间用 in_flight Set 防止同一任务二次触发 | ✅ 完成 |

---

**规划任务详情已归档至 [ARCHIVED_PROGRESS.md](./ARCHIVED_PROGRESS.md)**

---

## F-34: CLI/TUI Frontend 解耦架构

**状态**: ✅ 已完成 Phase 1-3

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.3 F-34 CLI/TUI Frontend 解耦架构](./ARCHIVED_PROGRESS.md#五3-f-34-clitui-frontend-解耦架构已完成-phase-1-3)。

## F-36: LocalTracker 本地 Issue 文档源

**状态**: ✅ 完成

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.4 F-36 LocalTracker 本地 Issue 文档源](./ARCHIVED_PROGRESS.md#五4-f-36-localtracker-本地-issue-文档源)。

## F-38: Orchestrator 验证与报告闭环

**状态**: ✅ 完成

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.5 F-38 Orchestrator 验证与报告闭环](./ARCHIVED_PROGRESS.md#五5-f-38-orchestrator-验证与报告闭环)。

## F-39: Orchestrator Issue 重跑入口（label + comment 命令双通道）

**状态**: ✅ 完成（Sub-A~F）

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.6 F-39 Orchestrator Issue 重跑入口](./ARCHIVED_PROGRESS.md#五6-f-39-orchestrator-issue-重跑入口label-comment-命令双通道)。

## F-41: Coordinator 轻量工具集

**状态**: ✅ 已完成

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.10 F-41 Coordinator 轻量工具集](./ARCHIVED_PROGRESS.md#五10-f-41-coordinator-轻量工具集)。

## F-42: Orchestrator Shared / Sequential Workspace 策略

**状态**: ✅ 完成

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.11 F-42 Orchestrator Shared / Sequential Workspace 策略](./ARCHIVED_PROGRESS.md#五11-f-42-orchestrator-shared-sequential-workspace-策略)。

**2026-06-03 后修复记录**:
- **`extensions/api/orchestration.py`** — `WorkspaceConfig(...)` 构造器缺少 `strategy=workflow_config.workspace.strategy` 参数传递。`workflow.md` 中配置的 `workspace.strategy`（`isolated | shared | sequential`）被静默丢弃，所有 issue 均使用默认 `isolated` 行为。已修复。
- **Dashboard `ISSUE_STATUSES`** — `ISSUE_STATUSES` 集合缺少 `queued` 状态，导致排队 issue 在 Dashboard 上显示为 `pending` 而非 `queued`。已补充。

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

## F-43: CLI 模型供应商与模型切换

**状态**: ✅ 已完成 (2026-06-02)

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.8 F-43 CLI 模型供应商与模型切换](./ARCHIVED_PROGRESS.md#五8-f-43-cli-模型供应商与模型切换)。

### Sub-feature: 动态模型发现注册表 (post-archival)

**状态**: ✅ 已完成 (2026-06-03)

| 任务 | 文件 | 状态 | 说明 |
|------|------|------|------|
| `register_discovery_hook()` 全局注册表 | `clawcodex_ext/cli/model_cmd/registry.py` | ✅ | `_DISCOVERY_HOOKS` dict + register 函数；`ModelRegistry.__init__` 接受 `discovery_hooks` 参数 |
| `available_models()` 合并 hook | `clawcodex_ext/cli/model_cmd/registry.py` | ✅ | 静态基线 + hook 结果去重合并，异常静默；`validate_model`/`infer_provider_for_model` 天然支持 |
| `openai-codex` API 发现钩子 | `clawcodex_ext/providers/hooks.py` | ✅ | 调用 `get_codex_model_ids(token)`，无 token 或 API 失败时静默返回空 |
| 自动注册 | `clawcodex_ext/providers/__init__.py` + `clawcodex_ext/__init__.py` | ✅ | import 时自动注册，在 ModelRegistry 首次实例化前完成 |
| `resolve()` 信任已保存配置 | `clawcodex_ext/cli/model_cmd/resolver.py` | ✅ | `validate_model` 失败时走 `user-warn`，不再降级回默认 |
| 移除 `gpt-5.5` 硬编码 | `src/providers/__init__.py` | ✅ | 回归静态基线，由 hook 动态发现 |
| 回归测试 | `tests/test_f43_model_registry.py` | ✅ | 新增 6 个发现钩子测试（添加/隔离/异常静默/去重/validate_model/infer_provider），14/14 F-43 全部通过 |
| **端到端验证** | 手动确认 | ✅ | 模拟第三方扩展注册 `my-llm` 钩子，`available_models`/`validate_model`/`infer_provider_for_model` 全链路通过 |

## F-45: Orchestrator tool-call 审计旁路（tool-events.ndjson + 报告登记）

**状态**: ✅ 已完成 (2026-06-02)

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.7 F-45 Orchestrator tool-call 审计旁路](./ARCHIVED_PROGRESS.md#五7-f-45-orchestrator-tool-call-审计旁路)。

## F-46: permission_mode enum 正交拆分

**状态**: ⏳ 规划中
**优先级**: P2
**规划文档**: `docs/FEATURE_PLAN.md` → `§3.16 permission_mode enum 正交拆分设计（F-46）`
**触发场景**: 2026-06-02 F-45 设计时发现，`permission_mode` 这个 enum（`default` / `plan` / `bypassPermissions` / `acceptEdits` / `dontAsk` / `auto` / `bubble`）把**三个正交概念压在一个字段里**：(1) 是否要 TTY 弹 prompt（`interactive`）；(2) 无人值守时的默认决策（`default_decision: allow|deny|ask`）；(3) per-tool 决策是否落盘（`audit_log: none|minimal|full`）。这种 "超级 enum" 导致设计债累积：`dontAsk` 听上去像 "headless + audit" 但实际在 orchestrator 会被 auto-upgrade 到 `bypassPermissions`；`bypassPermissions` 听上去像 "全开" 但 TS 注释说 "no logging"，而 Python 端其实有 ApprovalPolicy 拦截。schema 层把语义耦合在一起，下游所有 "我想加一种 mode" 的尝试都得新增一个 enum case。

### 目标

把 `permission_mode` 拆为三个正交字段：
- `interactive: bool` — 是否需要 TTY 弹 prompt
- `default_decision: Literal["allow", "deny", "ask"]` — 没 policy 命中时的默认
- `audit_log: Literal["none", "minimal", "full"]` — per-tool 决策是否落盘（由 F-45 的 `tool-events.ndjson` 旁路支撑）

实现分阶段：
- **F-46.0**（v2.13，本期）：仅在 `WorkflowConfig` 加 `audit_log` 字段；`permission_mode` 暂保留做 backward-compat shim
- **F-46.1**（v2.15+，后续）：把 `interactive` / `default_decision` 也显式化
- **F-46.2**（v2.16+，后续）：`permission_mode` 标 deprecated，v2.16 移除

### 子特性

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| A | `WorkflowConfig.audit_log` 字段（F-46.0） | 先把 audit_log 这一维拆出 | `src/orchestrator/config/schema.py:WorkflowConfig` 新增 `audit_log: Literal["none", "minimal", "full"] = "minimal"`；`report_writer` 读该字段决定是否调 `_append_tool_event_log`（F-45 旁路） |
| B | `permission_mode` → 三字段语义糖（F-46.0） | 兼容旧 workflow.yaml | `extensions/orchestrator/config/schema.py:AgentConfig` 的 `permission_mode` 仍保留，但在 docstring 标 deprecated；`orchestrator.py` 启动时把 `permission_mode` 自动 translate 为三字段，logger warning 一次 |
| C | `interactive` 字段（F-46.1，后续） | 显式化 "是否要 TTY 弹 prompt" | `WorkflowConfig.interactive: bool = True`；`extensions/api/query.py:29` 的 `permission_mode: str = "dontAsk"` 默认值迁移为 `interactive: bool = True` |
| D | `default_decision` 字段（F-46.1，后续） | 显式化 "无人值守默认决策" | `WorkflowConfig.default_decision: Literal["allow", "deny", "ask"] = "ask"`；`orchestrator.py` auto-upgrade 逻辑从 `if has_tracker: bypassPermissions` 改为 `if not interactive: default_decision = "allow"` |
| E | `permission_mode` 降级为 shim（F-46.2，后续） | 彻底摆脱 enum | `permission_mode` 字段在 v2.15 标 `@deprecated`，v2.16 移除；期间只 accept 已知值，未知值 `logger.warning` + fallback 到 `default` |
| F | 文档与迁移指南 | 让用户跟得上 | `docs/new-features-guide.md` 加一节 "permission 三字段迁移"；`extensions/orchestrator/templates/workflow.template.md` 顶部加注释指向新字段 |

### 当前基线

| 能力 | 当前状态 | 说明 |
|------|----------|------|
| `permission_mode: Literal["default", "plan", "bypassPermissions"]`（schema 声明） | ⚠️ 太窄 | `src/settings/types.py:9` 仅 3 值；`src/permissions/modes.py:20` 实际接受 5 值（`acceptEdits` / `dontAsk` 也支持），Schema 与 runtime 漂移 |
| `dontAsk` 模式触发 ApprovalPolicy headless 卡死 | ❌ 已知 | `test_gitcode_workflow.md:58-60` 描述；orchestrator `schema.py` 已做 auto-upgrade |
| TS 上游 `bypassPermissions` 注释 "no logging" | ❌ 误导 | Python 端 `ApprovalPolicy` 总跑，语义与 TS 不完全一致；F-45 旁路是修复点 |
| 三个正交概念合在一字段 | ❌ 设计债 | 任何新增需求都得扩 enum case |
| `WorkflowConfig.audit_log` 字段 | ❌ 缺失 | `src/orchestrator/config/schema.py:WorkflowConfig` 无此字段 |

### 实施进度

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | `WorkflowConfig.audit_log` 字段 + `report_writer` 读该字段决定是否写旁路（F-45 已落地，可端到端验证） | A | 📋 待开始 |
| 2 | `permission_mode` → 三字段 translate 函数 + docstring 标 deprecated | B | 📋 待开始 |
| 3 | （F-46.1）`interactive` 字段落 WorkflowConfig，`extensions/api/query.py` 默认值迁移 | C | 📋 规划中 |
| 4 | （F-46.1）`default_decision` 字段 + orchestrator auto-upgrade 改写 | D | 📋 规划中 |
| 5 | （F-46.2）`permission_mode` 标 deprecated，v2.16 移除 | E | 📋 规划中 |
| 6 | `docs/new-features-guide.md` "permission 三字段迁移" 章节 + `extensions/orchestrator/templates/workflow.template.md` 顶部注释 | F | 📋 待开始 |

### 验收标准

- F-46.0 落地后，`workflow.yaml` 写 `audit_log: full` 时，`tool-events.ndjson` 落盘；写 `audit_log: none` 时，旁路不写；写 `audit_log: minimal`（默认）时，只写 deny 决策（节省空间）。
- 旧 workflow.yaml 不写 `audit_log`，默认 `minimal` 行为不破坏现有 run。
- `permission_mode: bypassPermissions` + `audit_log: full` 的组合，与 F-45 的 NDJSON 一致。
- `permission_mode: dontAsk` 不再触发 ApprovalPolicy 卡死：auto-upgrade 在 orchestrator 启动时发生，即使 workflow 没显式写 `audit_log` 也至少走 `minimal`。
- `permission_mode` → 三字段 translate 启动时打一条 `logger.warning` 提示 "已废弃，请改用三字段"，不报错。
- `pytest tests/test_orchestrator_*.py -q` 与 `tests/manual_e2e_f38.py -v -s` 无回归。
- 端到端：用旧 workflow.yaml（只写 `permission_mode: bypassPermissions`）跑一个 issue，`tool-events.ndjson` 落盘且 `audit_log` 字段在 NDJSON 中正确标注。

### 风险与约束

1. **enum 拆分 breaking change**：旧 workflow.yaml 写 `permission_mode: dontAsk` 的人看到 "deprecated" 会慌。Mitigation：旧值仍 accept，新字段可选；`docs/new-features-guide.md` 给迁移路径。
2. **F-46.0 与 F-45 顺序**：F-46.0 的 `audit_log` 字段要消费 F-45 的 NDJSON 旁路；F-45 未落地时 F-46.0 只能写 "字段定义"，无法端到端验证。
3. **上游 TS 未拆分**：clawcodex 跟随 TS 上游；若 TS 仍用 enum，我们只能做本地 schema 扩展，跨工具兼容差。Mitigation：在 `extensions/orchestrator/templates/workflow.template.md` 加注释 "建议同步升级上游"。
4. **三字段组合爆炸**：理论上 `interactive` × `default_decision` × `audit_log` = 18 种组合，部分无意义（如 `interactive=true` + `audit_log=none` 让人 prompt 但不记，可能让用户误以为有 audit）。Mitigation：加 `validate()` 互斥规则，启动时报 warning。
5. **不影响 `app_state` / `AppState.permission_mode`**：`src/state/app_state.py:87` 的 `permission_mode: str = "default"` 是运行时态，与 workflow.yaml 配置是两个层；F-46 仅改 workflow 层，AppState 不动。

### 已拟定的设计决定

| # | 决定 | 理由 |
|---|------|------|
| 1 | F-46.0 只拆 `audit_log`，暂不拆 `interactive` / `default_decision` | 拆得越多，本 PR 风险越大；F-45 已证明 "audit_log" 这一维是真正缺口的，先闭环 |
| 2 | `permission_mode` 保留为 backward-compat shim，标 deprecated | TS 上游仍用 enum，跨工具兼容需要这个 shim |
| 3 | `audit_log` 默认 `"minimal"`（只记 deny） | 节省磁盘；`"full"` 是用户显式 opt-in |
| 4 | `WorkflowConfig.audit_log` 在 schema 顶层，不在 `agent` 子段 | audit 是 workflow 概念，跨 agent run 共享 |
| 5 | 不动 `src/settings/types.py:PermissionModeType` | 那是 user-level settings，跟 workflow-level 不同概念 |
| 6 | 阶段化：F-46.0 → F-46.1 → F-46.2 | 把 "风险高 + 受益晚" 的 `interactive` / `default_decision` 推到 F-46.1，等 F-45 + F-46.0 在生产中跑一阵后再说 |
| 7 | `auto` / `bubble` 内部 mode 不动 | 它们是 sub-agent 内部机制，不是用户配置；F-46 不影响 |

### 依赖与协同

- **依赖**：
  - F-45 落地后才有 `tool-events.ndjson` 旁路可消费
  - `extensions/orchestrator/config/schema.py:WorkflowConfig` 作为字段挂点
  - `extensions/orchestrator/report_writer.py` 作为 audit_log 字段 reader
- **协同**：
  - 与 F-45 强协同：F-46.0 的 `audit_log` 字段是 F-45 NDJSON 旁路的 "开关"
  - 与 F-40 弱相关：ProgressSink 走 `ToolContext.tasks` metadata，不与 `audit_log` 重叠
  - 与 F-37 / F-39 无关：follow-up / 重跑 run 都继承 workflow 的 `audit_log` 字段
- **先于**：`docs/new-features-guide.md` "permission 三字段迁移" 章节；`extensions/orchestrator/templates/workflow.template.md` 顶部注释
- **后续议题（G-1 / v2.15+）**：
  - `interactive` 字段落地 + `extensions/api/query.py` 默认值迁移
  - `default_decision` 字段落地 + orchestrator auto-upgrade 改写
  - `permission_mode` 标 deprecated，v2.16 移除

---

## F-47: Permission Settings Schema 重构（`permissions` 改 dict 形态 + plumb 启动模式）

**状态**: ✅ 完成（含 F-47.1 hotfix）

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.9 F-47 Permission Settings Schema 重构](./ARCHIVED_PROGRESS.md#五9-f-47-permission-settings-schema-重构)。

## F-48: src/ 核心路径二开修改解耦

**状态**: 📋 设计完成
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `3.17 F-48: src/ 核心路径二开修改解耦方案`

### 目标

将 `src/` 中所有真正的二开功能修改迁移到 `clawcodex_ext/` 和 `extensions/` 扩展路径，使 `src/` 与上游源码（`src/upstream/58ea488/`）在功能层面完全一致，仅保留最小化的 seam/注册表/Protocol 扩展点。

### 问题现状

通过逐文件对比 `src/` 与 `src/upstream/58ea488/`（忽略行尾 CRLF/LF 差异），识别出 **10 个 src/ 文件含真正的功能修改**（其余 600+ 文件差异仅为行尾/格式差异，`diff -w` 无实质输出）：

| 文件 | 修改性质 | 解耦状态 |
|------|---------|---------|
| `src/repl/core.py` | provider 构建改 `build_provider_from_config`；构造器新增 6 个参数；`_api_key_missing` 软降级；`runtime_context` 存储；`/provider` 命令注册 | ❌ 深度耦合 |
| `src/tui/app.py` | ~250 行差异 — Ctrl+B/Fork-Continue、`runtime_context`、resume、permission cycling、thinking toggle | ⚠️ 大部分已解耦（子类覆盖），本体仍有注入 |
| `src/tui/commands.py` | `/model` 改为 `open_dialog`；移除 `/resume` 和 `/permission` 对话框；`/repl` 改为 `__repl__` 信号 | ⚠️ 可解耦 |
| `src/entrypoints/tui.py` | provider 注入 seam、session/resume/tail_follower/runtime_context 参数 | ⚠️ 已有部分解耦 |
| `src/entrypoints/headless.py` | provider/session/tool_registry/tool_context 注入 seam、`on_event` 桥接 | ⚠️ 同上 |
| `src/cli.py` | ✅ **已完全解耦** — 变成纯 facade，全部委托到 `clawcodex_ext/cli/` | ✅ 已完成 |
| `src/context_system/prompt_assembly.py` | `memory_scopes` 参数 + `clawcodex_ext.memory` try-import 降级 | ⚠️ 可解耦 |
| `src/permissions/cycle.py` | 新增 `bypassPermissions→dontAsk` 环节 | ⚠️ 可解耦 |
| `src/command_system/types.py` | `CommandContext` 新增 `tool_registry/tool_context/runtime_context` 字段 | ⚠️ 可解耦 |
| `src/command_system/engine.py` | `create_command_context` 新增 3 个参数透传 | ⚠️ 同上 |
| `src/providers/runtime.py` | ✅ **已是二开新增文件**（上游无此文件） | ✅ 应移到 ext |
| `src/agent/background_runner.py` | ✅ **已是二开新增文件**（上游无此文件） | ✅ 应移到 ext |

### 已完成的解耦模式（可复用）

1. **Facade 模式**（`src/cli.py`）— src/ 只剩 `from clawcodex_ext.xxx import yyy; return yyy()`
2. **子类覆盖模式**（`clawcodex_ext/tui/app.py`）— `ClawCodexExtTUI(ClawCodexTUI)` 覆盖 hook 方法
3. **前端注册表模式**（`clawcodex_ext/frontend/`）— `@register_frontend` + `get_frontend("repl")` 工厂

### 解耦方案：按优先级分 Phase

#### Phase 0: 纯新增文件移入 ext（无风险，立即执行）

| 修改点 | 方案 | 具体操作 |
|--------|------|---------|
| `src/agent/background_runner.py` | 整个文件移到 ext | 移到 `clawcodex_ext/agent/background_runner.py`，src/ 保留 thin re-export |
| `src/agent/background_state.py` | 整个文件移到 ext | 移到 `clawcodex_ext/agent/background_state.py` |
| `src/providers/runtime.py` | 整个文件移到 ext | 移到 `clawcodex_ext/providers/runtime.py`；src/ 调用点改为 ext 导入 |

#### Phase 1: 注册表/Protocol 扩展消除字段注入（低风险）

| 修改点 | 方案 |
|--------|------|
| `src/permissions/cycle.py` 的 `dontAsk` 环节 | **循环表注册表**：`_CYCLE_TABLE` 默认上游循环，ext 通过 `register_cycle_step()` 注册 `bypassPermissions→dontAsk` |
| `src/command_system/types.py` 的 3 个新增字段 | **Protocol 扩展**：定义 `DownstreamCommandContext(Protocol)`，ext 通过 `attach_downstream_context(ctx, runtime_context)` 注入 |
| `src/command_system/engine.py` 的 3 个参数 | **同上 Protocol**：`create_command_context` 保持上游签名，ext 后置注入 |
| `src/context_system/prompt_assembly.py` 的 `memory_scopes` | **构建器注册表**：ext 注册 `memory_section_builder` 回调 |

#### Phase 2: 子类覆盖模式恢复上游构造器签名（中等风险）

| 修改点 | 方案 |
|--------|------|
| `src/repl/core.py` 构造器 6 个注入参数 | **子类覆盖模式**：创建 `ClawCodexExtREPL(ClawcodexREPL)`；src/ 恢复上游 3 参数签名 + `**kwargs` 透传 |
| `src/repl/core.py` 的 `/provider` 命令 | **命令注册表**：ext 通过 `repl.add_command("/provider")` 注入 |
| `src/repl/core.py` 的 `build_provider_from_config` | **Provider 工厂注册表**：ext 注册替代工厂函数 |
| `src/tui/commands.py` 的命令增删 | **命令注册表**：ext 通过 `register_tui_command()` 注入 |
| `src/tui/app.py` 剩余注入 | **子类覆盖**：审计 `ClawCodexExtTUI` 是否完全覆盖 |

#### Phase 3: 入口点恢复上游逻辑（需谨慎，高集成度）

| 修改点 | 方案 |
|--------|------|
| `src/entrypoints/tui.py` | `run_tui()` 恢复为上游逻辑，ext 的 `TUIFrontend.run()` 构建扩展 TUI |
| `src/entrypoints/headless.py` | `run_headless()` 恢复为上游逻辑，ext 做注入包装 |
| `src/entrypoints/repl.py` | 同理，ext 的 `REPLFrontend.run()` 负责构建扩展 REPL |

### 解耦前后效果对比

| 指标 | 解耦前 | 解耦后 |
|------|--------|--------|
| src/ 有功能修改的文件 | 10+ | **0** |
| 上游同步冲突 | 高（每次 rebase 合并 820 行差异） | **极低**（src/ 与上游一致） |
| 二开代码位置 | 散布在 src/ + clawcodex_ext/ | **100% 在 clawcodex_ext/ + extensions/** |
| 上游 rebase 耗时 | 手动逐文件合并 | **自动快进** |

### 验收标准

1. `diff -w src/<file> src/upstream/58ea488/<file>` 对所有 10 个文件返回空输出
2. 所有现有功能测试通过：`python3 -m pytest tests/test_orchestrator_*.py -q`
3. REPL/TUI/Headless 三前端完整可用
4. `src/cli.py` 保持已解耦状态（纯 facade）
5. `src/providers/runtime.py`、`src/agent/background_runner.py`、`src/agent/background_state.py` 不再存在于 `src/`
6. `src/permissions/cycle.py` 的 `dontAsk` 环节由 ext 注册
7. `src/command_system/types.py` 的 `CommandContext` 无二开新增字段
8. `src/repl/core.py` 的 `ClawcodexREPL.__init__` 恢复为上游签名
9. `src/entrypoints/*.py` 恢复为上游逻辑

### 风险与约束

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `**kwargs` 透传隐藏签名变更 | 上游改了构造器签名，二开未感知 | 对 kwargs 做 `TypedDict` 约束 |
| Protocol 扩展新增 import 链 | src/ 仍需 import 注册表模块 | 注册表模块放在 `src/capabilities/` 层 |
| 子类覆盖与上游内部重构冲突 | 上游重命名了被覆盖的方法 | 每次上游同步运行子类方法存在性测试 |
| `background_runner` 移到 ext 后 src/ 模块找不到 | `from src.agent.background_runner import ...` 断裂 | `src/agent/__init__.py` 加 re-export（Phase 0 临时） |

### 已拟定的设计决定

| # | 决定 | 理由 |
|---|------|------|
| 1 | 注册表/Protocol 扩展点放在 `src/capabilities/` 而非 `src/` 本体 | capabilities 层已允许下游扩展导入 |
| 2 | `**kwargs` 透传而非上游签名完全一致 | 避免每次上游更新都需同步改子类签名 |
| 3 | Phase 0 re-export 临时方案，Phase 2 后移除 | 避免一次性 breaking change |
| 4 | `DownstreamCommandContext` 用 Protocol 而非 dataclass 继承 | Protocol 不要求共同基类 |
| 5 | 循环表注册表用 `list[tuple[str,str]]` | 保留顺序语义，支持扩展点 |
| 6 | 前端插件负责全部组装 | 入口点不应包含二开逻辑 |

### 依赖与协同

- **依赖**：
  - F-34（前端注册表解耦）✅ 已完成 — 提供了 `@register_frontend` + `get_frontend()` 工厂
  - F-35（二开特性统一切换）— 提供了上游纯净模式框架，F-48 是 F-35 的具体落地路径
- **协同**：
  - 与 F-15（Shift+Tab cycle）强协同：F-48 Phase 1 的循环表注册表是 F-15 `dontAsk` 环节的解耦载体
  - 与 F-43（CLI 模型供应商切换）协同：F-43 新增的 `runtime_context` 字段由 F-48 Phase 1 改为 Protocol 扩展注入
  - 与 F-28（Ctrl+B 后台运行）强协同：`background_runner.py` 移入 ext 是 F-28 解耦的前提
- **先于**：
  - F-35 的 584 文件还原需要 F-48 先完成核心 10 文件的解耦

---

## F-49: Issue 会话统一存储与实时介入协议

**状态**: 📋 设计完成
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `§3.1.11 Issue 会话统一存储与实时介入协议（F-49）`
**依赖**: F-21（后台运行 + 恢复同步）、F-38（验证与报告闭环）、F-40（ProgressReporter Sink 协议重构）

### 问题现状

当前系统存在两套互不兼容的事件记录系统：

| 维度 | REPL 会话（`SessionStorage`） | Headless Issue Agent（`_write_event_log`） |
|------|------|------|
| 存储位置 | `~/.clawcodex/sessions/{sid}/transcript.jsonl` | `{workspace}/.event_logs/{issue_id}.ndjson` |
| 格式 | Message dict (role, content blocks, tool_use_id) | 扁平 `{timestamp, type, tool_name, params}` |
| 配套设施 | `TailFollower`、`Session.load/resume`、`session_resume.resume_session()` | 仅 `_run_tail` CLI |
| 可恢复性 | ✅ 可重建 LLM context | ❌ 不能用于 `--resume` |
| 控制通道 | asyncio.Event + Unix socket（F-21） | 文件轮询 `{.orchestrator_control/}` |

核心矛盾：headless agent 写 `.event_logs/` 扁平 NDJSON，上游已完备的 `SessionStorage` + `TailFollower` + `session_resume` 基础设施完全无法消费。Observe/tail/takeover/resume 每个功能都需要在两条路径上重复实现。

### 目标

统一 headless agent 和 REPL 会话的存储格式，在此之上建立 Unix socket 双向实时介入协议，使 operator 可通过 `attach` CLI 观察、中断、接管、恢复 issue agent 的运行。

| 场景 | 当前 | 目标 |
|------|------|------|
| 实时观察 | `tail` CLI 读 `.event_logs/` | `attach` CLI 通过 socket 流式接收事件 |
| Ctrl+C 中断 | ❌ 不支持 | socket `pause` → agent 挂起等待 operator |
| 人工接管 | ❌ 不支持 | pause 后 operator 键入 hint |
| `/resume` 恢复 | ❌ 不支持 | socket `resume`（可选附带 prompt） |
| Session 恢复 | ❌ `.event_logs/` 无法重建 | 统一 `SessionStorage` → `session_resume.resume_session()` |
| detach | ❌ 不支持 | socket `detach` → agent 继续运行 |

### 实施阶段

#### Phase 0 — 统一事件存储（1-2天）

| 文件 | 改动 |
|------|------|
| `extensions/orchestrator/agent_runner.py` | `AgentSession` 增加 `session_storage: SessionStorage`；`run()` 中 `init_metadata(model, cwd, title)`；替换 `_write_event_log()` → `session_storage.write_raw(msg_dict)` + `flush()` |
| `extensions/orchestrator/agent_runner.py` | 删除 `_write_event_log()` 方法；删除 `.event_logs/` 目录创建逻辑 |
| `extensions/orchestrator/cli/issue.py` | `_run_tail` 改为读 `transcript.jsonl`（或兼容双读） |

验收：headless agent 的每个 tool_use / tool_result / text_delta 以 Message dict 格式写入 session JSONL，`TailFollower` 可直接 follow，`session_resume` 可直接重建 LLM context。

#### Phase 1 — Unix Socket 控制通道（2-3天）

新增 `extensions/orchestrator/control_socket.py`：

```
ControlSocket
  ├── start()        → 监听 {workspace}/.run_control/{id}.sock
  ├── poll_commands() → AsyncIterator[ControlCommand]
  ├── send_event()   → 广播事件给所有客户端
  └── stop()         → 关闭 socket
```

`ControlCommand` 类型：

```python
@dataclass
class ControlCommand:
    cmd: Literal["pause", "resume", "inject", "stop", "detach", "takeover"]
    payload: str = ""
```

集成到 `AgentRunner.run()`：每轮 turn 前调用 `poll_commands()`；pause 时 await `pause_resume_event.wait()`；resume 时 set event + 可选覆盖 prompt。

#### Phase 2 — `attach` CLI TUI（2-3天）

新增 `extensions/orchestrator/cli/attach.py`，提供实时 TUI：

| 交互 | 动作 |
|------|------|
| 连接 | 发送 attach → 接收 session state + 最近事件 |
| Ctrl+C | 发送 pause → 显示 `(Paused) >` 提示符 |
| 普通文本 | 发送 inject hint |
| `/resume` | 发送 resume |
| `/resume ...` | resume + prompt payload |
| `/inspect` | 从 `SessionStorage.read_transcript()` 读取消息历史 |
| `/stop` | 停止 agent |
| `/takeover` | 停止 agent + 启动 REPL |
| `/detach` / Ctrl+D | 断开 socket，agent 继续运行 |

#### Phase 3 — Session 恢复（0.5天，Phase 0 增量产出）

统一存储后，Session 恢复变为零额外工作：

```python
session = Session.resume(issue_session_id)
# SessionStorage 已包含所有历史消息
# session_resume.resume_session() 重建 LLM context
# 新的 AgentRunner 可从此处继续
```

### 风险与约束

| 风险 | 缓解 |
|------|------|
| `.event_logs/` 存量用户 | Phase 0 向后兼容双写，Phase 2 发 deprecation warning |
| Windows 无 Unix socket | 回退 Named Pipe 或 TCP localhost；`BindAddress` Protocol |
| pause 时 agent 在 tool call 中间 | 不中断执行中 tool call，返回后检查 paused flag |
| 多客户端冲突 | `ControlSocket` 广播 + last-write-wins |
| 安全 | socket `umask 0077`；`/takeover` 需身份确认 |

### 设计决定

1. **Phase 0 优先于一切** — 存储不统一，后面所有基础设施用不上
2. **Unix domain socket** — 非文件轮询、非 SSE、非 gRPC；asyncio 原生最轻量双向方案
3. **`SessionStorage` 不改一行** — `write_raw()` 就是为此场景设计的
4. **`ControlSocket` 在 `extensions/orchestrator/`** — 遵守 F-48 解耦约束
5. **attach TUI 不需 curses/textual** — `select.poll()` + `sys.stdin.read()` + `print()` 避免新增依赖

---

*文档更新时间: 2026-06-03*

*版本 v2.14 更新：新增 F-48 src/ 核心路径二开修改解耦方案（📋 设计完成）。通过对比 `src/` 与 `src/upstream/58ea488/`，识别出 10 个含真正功能修改的 src/ 文件，分 Phase 0~3 四阶段制定解耦方案。*

*版本 v2.13 更新：新增 F-45 / F-46 / F-47。F-45 P1 在 `agent_runner._handle_tool_call` 后加 NDJSON 旁路落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，与 permission_mode 解耦；扩展 `report_writer.RunReport.tool_events_path` 字段 + markdown 模板登记路径；终结 "bypass ≠ 无审计" 误读。F-46 P2 把 `permission_mode` enum 拆为 `interactive` / `default_decision` / `audit_log` 三个正交字段，F-46.0（v2.13）只拆 `audit_log`，依赖 F-45 落地后端到端验证；`permission_mode` 保留为 backward-compat shim 标 deprecated；F-46.1+ 拆其余两字段推到 v2.15+。*

*版本 v2.11 更新: F-42 Sequential Workspace 策略实现完成。`workspace.strategy: isolated | shared | sequential` 落地，sequential 强制单并发并使用顺序锁，共享 root 上的 integration branch 叠加 commit 链，commit 元数据（base/start SHA、sequence_index）写入 registry，sequential GitSync 本地 commit 不 push/PR，shared/sequential root 在 cleanup 时保留。19 个专项测试 + 245 个 orchestrator 回归全部通过。*

*版本 v2.10 更新: 新增 F-42 Orchestrator Shared / Sequential Workspace 策略设计。规划 `workspace.strategy: isolated | shared | sequential`，支持本地 feature-plan issue 在同一 working tree / integration branch 上按顺序叠加开发；保留旧 isolated 行为，并设计单并发校验、顺序锁、dirty tree guard、commit 链 registry 元数据、GitSync/cleanup preserve 语义与两 issue 端到端验收。*

*版本 v2.7 更新: 新增 F-41 Coordinator 轻量工具集。扩展 `_COORDINATOR_ALLOWED_TOOLS` 使 Coordinator 获得 Read / WebSearch / WebFetch 三个轻量工具，合计 6 个。写/执行工具仍隔离，强制委派给 Worker。提示词同步更新。231/231 orchestrator 测试通过。*

*版本 v2.6 更新: 修复 `progress_reporter` 死代码,phase completion 接入 ndjson event log (F-38 Sub-D 落地)。新增 F-40 ProgressReporter Sink 协议重构。


---

## F-50: POS 转换器源码固化（SourceCodeParser + 增强 SkillGrouper + AgentMarkdownWriter）

**状态**: ✅ 完成
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `§3.18 POS 转换器源码固化设计（F-50）`
**依赖**: 无

### 目标

将 AscendDataForge 实践中手工完成的 Python 源码 → Agent 转换逻辑固化为三个可复用模块，集成到现有 `extensions/pos_converter/` 中，使 `clawcodex-dev pos convert ./组件目录 --out .claude` 直接可工作。

| 模块 | 文件 | 说明 |
|------|------|------|
| `SourceCodeParser` | `extensions/pos_converter/source_parser.py` | Python 源码 AST 解析：类/方法/docstring/参数/依赖 |
| 增强 SkillGrouper 策略 | `extensions/pos_converter/skill_grouper.py` | 新增 `GroupStrategy`，支持组件级/IO 关联/LLM 分组 |
| `AgentMarkdownWriter` | `extensions/pos_converter/agent_md_writer.py` | 生成 `.claude/agents/*.md` + `.atomcode/skills/*/SKILL.md` |
| 总览 Agent | `extensions/pos_converter/agent_md_writer.py` | `write_overview_agent()` **始终**生成工作流总览入口（无额外参数） |
| 默认 Agent 替换机制 | `extensions/pos_converter/default_agent.py` | `resolve_default_agent()` 检测 `clawcodex-overview.md` 并替换默认 agent |

### 子特性

1. **SourceCodeParser** — `ast.parse()` 递归扫描 `.py` 文件，输出 `SourceComponent[]`
2. **GroupStrategy 枚举** — `KEYWORD_MATCH` / `COMPONENT_GROUP` / `IO_RELATION` / `LLM_SEMANTIC`
3. **AgentMarkdownWriter** — 生成 CLI 可加载的 agent markdown 文件（完整 frontmatter + 技能参考）
4. **总览 Agent（Overview Agent）** — `pos convert` **始终**生成工作流总览 agent（`clawcodex-overview.md`），知晓所有子 agent 的职责和调用链
5. **默认 Agent 替换** — `clawcodex-overview.md` 命名约定 + `--agent` CLI 参数，启动时自动替换默认 `GENERAL_PURPOSE_AGENT`
6. **CLI 兼容增强** — `clawcodex-dev pos convert <dir> --out .claude --strategy component`
7. **测试** — `tests/test_pos_converter_source_parser.py` + 回归

### 当前基线

- `extensions/pos_converter/` 已有三层架构：`SdkParser` → `SkillGrouper` → `AgentBuilder`
- `clawcodex-dev pos convert` CLI 已注册，支持 OpenAPI / 逗号分隔方法列表
- `SdkParser` 仅有 `_parse_openapi()` 和 `_parse_simple_list()`，不支持 Python 源码
- `SkillGrouper` 仅有 `_static_group()`（MappingRule 关键字匹配），无组件级/IO 关联分组
- `AgentBuilder.write_agent_markdown()` 输出极简 YAML，缺少完整 frontmatter 和技能参考嵌入
- `pos2agent_ascend_dataforge.py` 脚本已手工完成一次完整转换（可作为验收基准）
- **缺少总览 Agent 生成** — 当前无任何总览/入口 agent 概念，用户需手动 `@agent-xxx` 调用
- **缺少默认 Agent 替换机制** — `GENERAL_PURPOSE_AGENT` 硬编码，没有 `clawcodex-overview.md` 或 `--agent` 参数

### 实施进度

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| SourceComponent / SourceOperation / ParamSpec 数据类 | `extensions/pos_converter/source_parser.py` | ✅ 完成 | 从 `pos2agent_ascend_dataforge.py` 中提取 schema |
| ModuleWalker — 递归扫描 .py 文件 | `extensions/pos_converter/source_parser.py` | ✅ 完成 | `ast.parse()` + 文件发现 |
| ClassExtractor — 提取类/方法 | `extensions/pos_converter/source_parser.py` | ✅ 完成 | AST 类定义 + 方法签名 |
| DocstringParser — docstring 结构化提取 | `extensions/pos_converter/source_parser.py` | ✅ 完成 | Google/NumPy/reST 兼容 |
| DependencyAnalyzer — import 图分析 | `extensions/pos_converter/source_parser.py` | ✅ 完成 | import 语句 → 组件依赖 |
| GroupStrategy 枚举 + 组件级分组 | `extensions/pos_converter/skill_grouper.py` | ✅ 完成 | 增量修改，向后兼容 |
| IO 关联分组 | `extensions/pos_converter/skill_grouper.py` | ✅ 完成 | 参数类型匹配跨组件归组 |
| LLM 语义分组占位 | `extensions/pos_converter/skill_grouper.py` | ✅ 完成 | 填充 `group_with_llm()` |
| AgentMarkdownWriter — agent markdown 生成 | `extensions/pos_converter/agent_md_writer.py` | ✅ 完成 | `.claude/agents/*.md` 格式 |
| AgentMarkdownWriter — skill markdown 生成 | `extensions/pos_converter/agent_md_writer.py` | ✅ 完成 | `.atomcode/skills/*/SKILL.md` 格式 |
| AgentMarkdownWriter — WORKFLOW.md 生成 | `extensions/pos_converter/agent_md_writer.py` | ✅ 完成 | orchestrator 编排文件骨架 |
| CLI `--out` / `--skills` / `--strategy` 参数 | `clawcodex_ext/cli/pos_cmd/commands.py` | ✅ 完成 | 增量修改 |
| CLI 源码目录 vs 方法名自动判断 | `clawcodex_ext/cli/pos_cmd/commands.py` | ✅ 完成 | 目录存在检测 |
| AgentBuilder `format` 参数 | `extensions/pos_converter/agent_builder.py` | ✅ 完成 | `agent_definition` / `markdown` / `both` |
| 模板（agent / skill markdown） | `extensions/pos_converter/templates.py` | ✅ 完成 | Jinja2 模板 |
| AgentMarkdownWriter — 总览 Agent 生成 | `extensions/pos_converter/agent_md_writer.py` | ✅ 完成 | `write_overview_agent()` + `AgentComponentInfo` / `WorkflowStage` 数据类 |
| AgentBuilder — 总览 Agent 自动调用 | `extensions/pos_converter/agent_builder.py` | ✅ 完成 | `build()` 检测多组件 → 自动生成 overview agent |
| `resolve_default_agent()` | `extensions/pos_converter/default_agent.py` | ✅ 完成 | 扫描 `.claude/agents/clawcodex-overview.md`，返回覆盖 prompt |
| `--agent CLI` 参数 | `clawcodex_ext/cli/pos_cmd/commands.py` + repl 启动路径 | ✅ 完成 | 启动时指定默认 agent |
| 启动 Agent 标识 Banner | `clawcodex_ext/cli/dispatch.py` | ✅ 完成 | `_resolve_startup_agent()` 在 stderr 输出 `⚡ Using agent: <name> (<n> sub-agents)` |
| 单元测试 | `tests/test_pos_converter_source_parser.py` | ✅ 完成 | 33 个测试覆盖提取/分组/生成 |
| E2E 验收 | — | ✅ 完成 | 33/33 测试通过，回归 271/271 通过 |

### 验收标准

1. `clawcodex-dev pos convert 组件/视频算子 --out .claude` 生成 `.claude/agents/video-ops-agent.md`，`load_agents_dir.py` 可解析加载
2. `clawcodex-dev pos convert 组件/`（多组件的上层目录）自动生成 `.claude/agents/clawcodex-overview.md`，包含工作流概述和子 Agent 委派指引
3. `SourceCodeParser` 正确提取 AscendDataForge 所有组件的类/方法/docstring/参数/依赖
4. 生成的 SKILL.md 包含完整操作源码片段和参数说明
5. 总览 Agent 的 system prompt 包含所有 `AgentComponentInfo` 和 `WorkflowStage` 描述
6. `resolve_default_agent()` 检测 `clawcodex-overview.md` 时返回对应 agent definition；未找到时返回 None，不改变启动行为
7. 所有新增测试通过：`python3 -m pytest tests/test_pos_converter*.py -q`
8. 现有 `extensions/pos_converter` 测试继续通过

---

## F-51: AgentRunner 空转检测机制（no-op detection）

**状态**: ✅ 完成
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `§3.1.12 AgentRunner 空转检测机制`
**依赖**: 无（独立的 agent_runner 修正）

### 问题现状

当 Orchestrator 处理某个 issue 时，如果该 issue 的 deliverables 已经在 base branch 中存在（例如通过上游 commit 预置），agent 会进入一个无意义循环：

1. Agent 读取 issue 描述 → 要求"新建"某功能
2. 搜索代码发现功能已存在 → 不知道该怎么办
3. 跑 `python3 --version` / `date` / `print("x")` 等 busy-work 命令
4. 每轮无文件变更 → 但 session.status 仍是 "continue"
5. 耗尽全部 max_turns（40轮，~150 次 API 调用）
6. session.status = "max_turns_exceeded" → Orchestrator 调度 retry
7. ↻ 无限循环，直到人工干预

### 故障链

| 层次 | 问题 | 修复 |
|------|------|------|
| Prompt | 无处理"已实现"的指令 | workflow.md Step 3.5：如果 deliverables 已存在且验证通过，直接完成 |
| Agent Loop | 无工作区变更检测 | `get_file_status(workspace)` 每轮检查，连续 5 轮 clean 则 force complete |
| Git Sync | 无文件变更时仍走完整流程 | `GitSyncService.changed=False` 正确跳过 commit/push |
| Registry | 无 "无变更但通过" 的状态 | 复用 `completed` + 日志记录 no-op 原因 |

### 实施

**文件**: `extensions/orchestrator/agent_runner.py`

| 修改 | 说明 |
|------|------|
| `import get_file_status` | 从 `src.utils.git` 导入工作区脏检测 |
| `_NOOP_DETECTION_MAX_TURNS = 5` | 连续 5 轮无文件变更即判定为空转 |
| `consecutive_clean_turns` 追踪 | 在 run() 的 continue 路径中累积计数器 |
| `if dirty: reset` / `else: increment & check` | 有变更清零，无变更累积，>=5 时 force-complete |
| `session.status = "completed"; return` | 直接退出 agent 循环，不触发 retry |
| `logger.warning("No-op detection triggered")` | 关键审计日志记录 |

### 验收

1. Agent 遇到已存在的 issue deliverables → 运行 ≤5 轮后自动完成
2. Agent 正在产出代码（有文件变更）→ 不受影响，空转计数器持续清零
3. 日志中出现 `No-op detection triggered issue_id=X` 记录
4. Orchestrator 不 retry，issue 标记为 completed
5. 增量轮次成本：每次 SessionComplete 读取一次 `get_file_status()`（<1ms）

---

## F-44: Orchestrator 人工检视闸门（Review Gate）

**状态**: ✅ 完成
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `§3.1.13 人工检视闸门设计（F-44）`
**依赖**: F-38（验证与报告闭环）、F-39（Issue 重跑入口）

### 目标

为 Orchestrator 自动开发流程添加可选的人工检视闸门，实现"自动开发 + 人工合并"的协作模式，对应选项 A 架构。

### 当前基线

- GitSyncService 已有 `pending_review` 状态位，但仅 `LocalTracker` 下触发
- `Orchestrator.run_issue()` 的 `finally` 块中 `mark_completed()` 会覆盖 `pending_review` 状态
- CLI 已有 `issue review --approve/--reject` 命令，但从未被触发
- 远程 tracker（GitHub/Gitee/GitCode）没有人工检视环节

### 实施进度

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 配置字段 | `schema.py` | ✅ 完成 | `AgentConfig.review_required: bool = False` + `from_dict` 解析 |
| 同步层 | `git_sync.py` | ✅ 完成 | `pending_review` 条件扩展为 `is_local_tracker or review_required` |
| 编排器 | `orchestrator.py` | ✅ 完成 | `finally` 块跳过 `mark_completed()` 当 `pending_review` 存在 |
| 工作流配置 | `workflow.md` | ✅ 完成 | `review_required: true` 示例 |
| 测试 | 全部测试 | ✅ 通过 | 82 个 orchestrator 测试无回归 |

### 文件变更

| 文件 | 改动 |
|------|------|
| `extensions/orchestrator/config/schema.py` | +6 行：新字段 + `from_dict` 解析 |
| `extensions/orchestrator/git_sync.py` | +1 行：`pending_review` 条件扩展 |
| `extensions/orchestrator/orchestrator.py` | +16 行：`finally` 块检测修复 |
| `workflow.md` | +1 注释：开启 `review_required: true` |

### 验收标准

1. `review_required: false` → 行为不变，不阻塞任何现有流程
2. `review_required: true` + 有代码变更 → 状态为 `PENDING_REVIEW`
3. `clawcodex-dev orchestrator issue review --id <id> --approve` → 状态变 `COMPLETED`
4. `clawcodex-dev orchestrator issue review --id <id> --reject --feedback "..."` → 自动 retry
5. Orchestrator 重启后 `PENDING_REVIEW` 状态持久化，CLI 可继续操作

---

## 九、死代码排查记录

> 扫描时间: 2026-06-XX | 工具: vulture 2.16 | 对照基线: `src/upstream/58ea488/`

### 9.1 排查方法

1. 运行 `vulture src/ src/upstream/58ea488/` 对当前代码和上游基线分别扫描
2. 逐项对照：若死代码在上游同类文件中同样存在，则判定为 **继承性死代码（UPSTREAM）**，保留不动
3. 若死代码仅存在于本 fork 新实现文件中（`extensions/` 或 `src/services/bridge/` 等上游不存在的文件），则判定为 **新引入死代码（NEW）**，应清理

### 9.2 应清理项（NEW — 本 fork 新引入）

| # | 文件 | 行 | 类型 | 严重程度 | 说明 |
|---|------|----|------|---------|------|
| 1 | `src/services/bridge/transport.py` | 69-70 | 不可达代码 | 🔴 P0 | `receive()` 协程第69行有裸 `return`，之后第70行 `yield` 永远不可达。该文件整体在上游不存在，属新实现 stub，有缺陷 |
| 2 | `extensions/orchestrator/agent_runner.py` | 30 | 未使用的 import | 🟠 P1 | `is_quota_exhausted` 从 `src.services.api.errors` 导入，但仅在第237行文档字符串中被提及，从未实际调用 |
| 3 | `extensions/orchestrator/cli/dashboard.py` | 18 | 未使用的 import | 🟠 P1 | `deque` 从 `collections` 导入，全文无任何使用 |
| 4 | `extensions/orchestrator/cli/issue.py` | 1575 | 未使用的 import | 🟠 P1 | `from pathlib import Path as _Path` 局部导入，`_Path` 从未被实例化。模块级已在第34行有 `from pathlib import Path` |

### 9.3 继承性死代码（UPSTREAM — 保留不动）

上游 `src/upstream/58ea488/` 中存在相同死代码，按原则保留不做清理。涉及约 50 处，主要为以下模式：

| 模式 | 示例文件 | 数量 |
|------|---------|:----:|
| TYPE_CHECKING 块中导入但实际未用于类型注解 | `subagent_context.py`, `task_stop.py` | ~8 处 |
| 函数签名中定义但从未使用的参数（`exc_type`, `tb` 等） | `transcript.py:221`, `live_status.py:187`, `frame_metrics.py:163` | ~6 处 |
| 模块顶部导入但未被任何调用引用的 import | `messages.py`, `deep_link.py`, `advisor.py` | ~25 处 |
| 函数/变量定义后无调用方 | `prompt.py:55` `allow_fork`, `session_resume.py:181` `new_cwd` | ~5 处 |
| 被注释中的引用而非实际代码引用的 import | `repl_bridge_transport.py:31` | ~2 处 |

> 典型继承项：`src/utils/messages.py` 导入的 `ToolUseBlock`、`RedactedThinkingBlock`、`ImageBlock`、`MessageContent` 在上游同样未使用；`src/bridge/bridge_permission_callbacks.py` 和 `src/bridge/types.py` 的未用参数在上游完全一致。

### 9.4 误报项（已确认有使用）

| 文件 | vulture 报告项 | 实际使用说明 |
|------|--------------|-------------|
| `src/utils/advisor.py:37` `BaseProvider` | 未用 import | TYPE_CHECKING 导入 + 函数签名字符串注解中使用 |
| `src/services/tool_execution/tool_execution.py:53` `get_all_base_tools` | 未用 import | 懒导入，在函数体内被调用 |
| `src/services/api/errors.py:21` `RateLimitError` | 未导出 | 已在 `__init__.py` 中 re-export |

### 9.5 清理建议

| 优先级 | 文件 | 建议操作 |
|--------|------|---------|
| P0 | `src/services/bridge/transport.py:69-70` | 删除第69行的裸 `return`，使 `yield` 可达；或根据实际 WebSocket 传输需求重构 stub |
| P1 | `extensions/orchestrator/agent_runner.py:30` | 删除 `is_quota_exhausted` import |
| P1 | `extensions/orchestrator/cli/dashboard.py:18` | 删除 `from collections import deque` |
| P1 | `extensions/orchestrator/cli/issue.py:1575` | 删除 `from pathlib import Path as _Path`（模块级已有 `Path`） |*
