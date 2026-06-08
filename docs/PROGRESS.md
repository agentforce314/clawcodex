# ClawCodex 开发进度跟踪文档

> 文档路径: `docs/PROGRESS.md`
> 基于: `docs/open-source-replacement-progress.md`, `docs/FEATURE_PLAN.md`
> 版本: v3.0
> 更新日期: 2026-06-08
> 上游同步: 58ea488 (dev-decoupling-refactor)
>
> **v2.16 变更**：完成 CCB（claude-code-best）全面对标分析，识别 clawcodex 的 8 个重大特性缺口纳入规划管线。新增 F-60 Pipe IPC + LAN 群控（P0）、F-61 Computer Use 屏幕操控（P0）、F-62 Chrome 浏览器控制（P1）、F-63 Channels 频道通知（P1）、F-64 Voice Mode 语音输入（P2）、F-65 Langfuse Agent 可观测性（P1）、F-66 ACP 协议支持（P2）、F-67 Buddy / Proactive 模式（P2）。同时更新 §四 CCB 对标优势特性总表，明确 clawcodex 对比 CCB 的 5 项领先特性（Orchestrator 自动流水线、Verification Gate、SOP 编译器、LiteLLM Provider、Manager/Worker 增强通信）。所有 F-60~F-67 设置为 ⏳ 待开始状态，详见 §四。
>
> **v3.0 变更**：基于 FEATURE_PLAN.md v3.0 重构，对齐特性编号与章节体系。新增 F-50、F-69~F-74、F-83~F-88 共 14 个 F-Number 至功能模块表格；F-5/F-6/F-7/F-8 标记为已重定向（→F-64/F-61/F-82/F-66）；章节编号对齐 FEATURE_PLAN（二~七为各系统进度，八=CCB 对标，九=Python 生态，十=死代码）。
>
> **v2.17 变更**：全面更新 F-48 src/ 核心路径二开修改解耦方案。通过 `diff -w` 逐文件验证，纠正了早前版本"~61 个格式差异"的重大误判——实际仅 4 个文件是纯格式差异，67 个文件均有语义变更。新增 Phase 4-9 覆盖新发现的 57 个未解耦功能修改文件。目标调整：src/ 功能修改文件数从 67 → ~10-20，增加"每文件决策记录"机制。详见 §六 FEATURE_PLAN.md。
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
| F-5 | Voice Mode | P2 | ⏳ →F-64 | 已合并至 F-64（Voice Mode 语音输入） |
| F-6 | Computer Use | P0 | ⏳ →F-61 | 已合并至 F-61（Computer Use 屏幕操控） |
| F-7 | Remote Control | P2 | ⏳ →F-82 | 已合并至 F-82（Remote Control Server） |
| F-8 | ACP/Zed/Cursor 集成 | P2 | ⏳ →F-66 | 已合并至 F-66（ACP 协议支持） |
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
| F-19 | SOP 转化模式 | P2 | 🔄 进行中 | 三层映射（SOP、workflow→Skill、SDK→工具），SDK 解析 + Skill 分组 + Agent 构建 + 持久化已完成；**`clawcodex-dev pos convert` CLI 子命令待注册**（dispatch.py/subcommand_registry.py 中未实现），当前仅支持斜杠命令和 Python API 调用 |
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
| F-48 | src/ 核心路径二开修改解耦 | P0 | 📋 设计完成 | 将 `src/` 中 **29 个纯新增文件 + 10 个功能修改文件**全部解耦到 `clawcodex_ext/` 和 `extensions/`。新增 7 个外部库适配器统一迁移（F-48.1 子特性）、Orchestrator 配套工具 5 个、Provider 扩展 3 个、Auth 子系统 2 个、TUI 屏幕 2 个、服务/工具 5 个、工具编写系统 1 个。修改文件解耦沿用 Phase 1~3（注册表/Protocol 扩展 → 子类覆盖 → 入口点恢复）。目标：src/ 二开新增文件数从 29 → 0，功能修改文件数从 10 → 0 |
| F-49 | Issue 会话统一存储与实时介入协议 | P1 | 📋 设计完成 | 将 headless agent 的 `.event_logs/` 扁平 NDJSON 统一为 `SessionStorage` 的 `transcript.jsonl` 格式。核心收益：**`clawcodex --resume <run_id>` 可直接恢复 orchestrator run 的完整对话进入交互式 REPL**（operator 可继续对话、接管 agent、崩溃恢复），而非仅只读围观。设计扩展：**Phase 0.1 Message 转录映射规则**（TextDelta/ToolCallEvent/ToolResultEvent → AssistantMessage/UserMessage 精确分组+轮次对齐）、**Phase 0.2 --resume 会话恢复 + TailFollower 实时观察 + 问题追溯**、**Phase 0.3 大内容文件引用**（复用 SessionStorage._replace_large_content）。Phase 1 socket 控制 → Phase 2 attach TUI → Phase 3 session 恢复。详见 FEATURE_PLAN §3.1.11 Phase 0.1-0.3。 |
| F-50 | SOP 转换器固化 | P2 | 📋 设计完成 | 见 FEATURE_PLAN §4.2 |
| F-51 | AgentRunner 空转检测机制（no-op detection） | P0 | ✅ 完成 | 在 `extensions/orchestrator/agent_runner.py` 中添加连续 5 轮工作区文件无变更检测，防止 agent 在 issue deliverables 已存在的场景下陷入无限 busy-work 循环。对应 PR 检视意见自动修复闭环（F-37）中的已修复前置问题。|
| F-52 | Python SDK 方法注册为 Tool | P2 | 📋 规划中 | 将 SOP 生成的 ADF 方法（`detect_modality`、`load_dataset` 等）注册为真实的 `Tool` 对象，使 sub-agent 可直接调用而非通过 Bash 回退。`extensions/pos_converter/tool_registry.py` — `ToolWrapper` + `register_source_operations()`。依赖 F-50。 |
| F-53 | Tool 自动暴露为 CLI 斜杠命令 | P3 | 📋 规划中 | 已注册的 Tool 自动映射为 REPL/TUI 中的 `/tool-name` 命令（如 `/detect_modality --path /data/raw`），参数从 Tool schema 自动推导。`clawcodex_ext/cli/tool_cmd/`。依赖 F-52。 |
| F-54 | AgentRunner / QueryRunner 运行期可观测性 | P0 | 📋 设计完成 | 补齐 headless issue agent 从 provider request 到 `SessionComplete` 之间的 debug 观测点：`QueryRunner.stream()` heartbeat、`AgentRunner` turn/event counters、watchdog timeout snapshot、持久化 `debug.ndjson` 与 registry/CLI 诊断字段，用于定位 agent run 有请求但无文件改动/报告/commit 的 stuck-run。 |
| F-55 | SOP 分组策略增强 | P1 | ✅ 完成 | F-50 增强子特性，解决"模块多时 Agent 过多"问题，详见 FEATURE_PLAN §4.2.1 |
| F-60 | Pipe IPC + LAN 群控系统 | P0 | ⏳ 待开始 | 对标 CCB Pipe IPC 多实例协作 + LAN UDP Multicast 自动发现。支持同机 Unix Domain Socket 命名管道通信、跨机器零配置发现、消息广播路由、权限转发。预计 3-4 周。 |
| F-61 | Computer Use 屏幕操控 | P0 | ⏳ 待开始 | 对标 CCB Computer Use。支持跨平台截图（macOS screencapture / Windows PowerShell / Linux scrot）、跨平台键鼠模拟、应用/窗口管理、剪贴板读写。预计 2-3 周。 |
| F-62 | Chrome 浏览器自动化控制 | P1 | ⏳ 待开始 | 对标 CCB Chrome Use。Chrome MCP 扩展桥接，支持页面导航、点击、填表、截图、执行 JS。预计 1-2 周。 |
| F-63 | Channels 频道通知系统 | P1 | ⏳ 待开始 | 对标 CCB Channels。支持飞书、Slack、Discord、微信等渠道的通知推送与 MCP 服务器消息转发。预计 2 周。 |
| F-64 | Voice Mode 语音输入 | P2 | ⏳ 待开始 | 对标 CCB Voice Mode。ASR 语音识别（豆包 doubaoime-asr / Whisper）、Push-to-Talk 语音交互、音频流 WebSocket 传输。预计 1-2 周。 |
| F-65 | Langfuse Agent 可观测性 | P1 | ⏳ 待开始 | 对标 CCB Langfuse。OpenTelemetry + Langfuse SDK 集成，Agent Loop 级追踪，一键转化为训练数据集。预计 1 周。 |
| F-66 | ACP 协议支持 | P2 | ⏳ 待开始 | 对标 CCB ACP（Agent Client Protocol）。Zed/Cursor 等 IDE 集成协议支持，会话恢复与 Skills 桥接。预计 1-2 周。 |
| F-67 | Buddy 伴侣 / Proactive 自主模式 | P2 | ⏳ 待开始 | 对标 CCB Buddy 伴侣系统 + Proactive 自主模式。后台 AI 伴侣异步观察会话、主动提供调试建议、检测文件变更自动提出优化。预计 2 周。 |
| F-68 | Orchestrator CLI 运维操作界面 | P2 | ⏳ 待开始 | issue/wf 管理、状态查看、dashboard 渲染；见 FEATURE_PLAN §3.2（F-68） |
| F-69 | Budget/Poor Mode | P2 | ⏳ 待开始 | 见 FEATURE_PLAN §7.5 |
| F-70 | Plugin 系统 | P1 | ⏳ 待开始 | 见 FEATURE_PLAN §4.3 |
| F-71 | 内置工具补齐 | P1 | ⏳ 待开始 | 见 FEATURE_PLAN §7.6 |
| F-72 | Multi-API 适配器 | P1 | ⏳ 待开始 | 见 FEATURE_PLAN §7.2 |
| F-73 | CI/CD 流水线 | P0 | ⏳ 待开始 | 见 FEATURE_PLAN §7.6 |
| F-74 | Sandbox 沙箱 | P2 | ⏳ 待开始 | 见 FEATURE_PLAN §7.2 |
| F-75 | 工具/Skill 调用统计（跨会话） | P2 | ⏳ 待开始 | 跨会话工具使用统计与策略优化；见 FEATURE_PLAN §4.8（F-75） |
| F-78 | Issue 语义澄清流程（自主模式扩展） | P1 | ⏳ 待开始 | 三通道语义澄清（LLM/CLI/TUI），冲突裁决，离线澄清；见 FEATURE_PLAN §4.12（F-78） |
| F-80 | Agent 间自主观察与消息交互 | P2 | ⏳ 待开始 | Agent 间自主观察汇报、SendMessage 消息交互、Manager-Worker 协作增强；见 FEATURE_PLAN §4.14（F-80） |
| F-81 | Native 原生模块系统（Python） | P1 | ⏳ 待开始 | 对标 CCB Rust/NAPI 原生模块，用纯 Python 等价实现音频捕获(sounddevice)、图像差异对比(Pillow+NumPy)、URL Scheme注册(webbrowser+xdg)、修饰键检测。F-61/F-64 前置依赖。预计 1 周。 |
| F-82 | Remote Control Server 远程控制 | P1 | ⏳ 待开始 | 对标 CCB remote-control-server。FastAPI 实现：会话管理、Worker 调度/心跳/长轮询、SSE/WebSocket 事件流、ACP 中继、环境管理、Web 管理面板。预计 3-4 周。 |
| F-83 | Ultraplan 规划 | P2 | ⏳ 待开始 | 见 FEATURE_PLAN §7.5 |
| F-84 | Context Collapse | P2 | ⏳ 待开始 | 见 FEATURE_PLAN §7.5 |
| F-85 | Templates 模板 | P1 | ⏳ 待开始 | 见 FEATURE_PLAN §7.6 |
| F-86 | Kairos/Brief 调度 | P2 | ⏳ 待开始 | 见 FEATURE_PLAN §7.5 |
| F-87 | Workflow Scripts | P2 | ⏳ 待开始 | 见 FEATURE_PLAN §7.5 |
| F-88 | Explore/Plan Agent | P2 | ⏳ 待开始 | 见 FEATURE_PLAN §7.5 |

---


## 二、Orchestrator 系统进度

## F-34: CLI/TUI Frontend 解耦架构

**状态**: ✅ 已完成 Phase 1-3

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.3 F-34 CLI/TUI Frontend 解耦架构](./ARCHIVED_PROGRESS.md#五3-f-34-clitui-frontend-解耦架构已完成-phase-1-3)。


## F-36: LocalTracker 本地 Issue 文档源

**状态**: ✅ 完成

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.4 F-36 LocalTracker 本地 Issue 文档源](./ARCHIVED_PROGRESS.md#五4-f-36-localtracker-本地-issue-文档源)。


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


## 三、Agent 核心能力进度

> F-2、F-4、F-9~F-13、F-16、F-18~F-20、F-75、F-78、F-80 的详细设计见 FEATURE_PLAN 第二章各节。
> 已归档完成项：F-13（记忆作用域隔离）、F-20（进度汇报）、F-29/F-30（工具注册）。

## 四、CLI 与配置系统进度

> 注：F-44（检视闸门）、F-45（审计旁路）设计上属于 Orchestrator 系统（FEATURE_PLAN §1.3~1.4），其详细进度暂存本章。

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


## F-44: Orchestrator 人工检视闸门（Review Gate）

**状态**: ✅ 完成
**优先级**: P1

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.13 F-44 Orchestrator 人工检视闸门](./ARCHIVED_PROGRESS.md#五13-f-44-orchestrator-人工检视闸门review-gate)。

---


## F-45: Orchestrator tool-call 审计旁路（tool-events.ndjson + 报告登记）

**状态**: ✅ 已完成 (2026-06-02)

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.7 F-45 Orchestrator tool-call 审计旁路](./ARCHIVED_PROGRESS.md#五7-f-45-orchestrator-tool-call-审计旁路)。


## F-46: permission_mode enum 正交拆分

**状态**: ⏳ 规划中
**优先级**: P2
**规划文档**: `docs/FEATURE_PLAN.md` → `§5.2 permission_mode enum 正交拆分设计（F-46）`
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


## 五、Architecture & SDK 下沉进度

> 注：F-49（会话统一存储）、F-51（空转检测）、F-54（可观测性）设计上属于 Orchestrator 系统（FEATURE_PLAN §1.3~1.4），会话恢复增强（FEATURE_PLAN §6）已移至第七章。

## F-48: src/ 核心路径二开修改解耦

**状态**: 🏗️ 进行中（2026-06 批次：3 项已解耦）
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `§6.1 F-48: src/ 核心路径二开修改解耦方案`

### 目标

将 `src/` 中 **29 个纯二开新增文件 + 10 个功能修改文件**全部迁移到 `clawcodex_ext/` 和 `extensions/` 扩展路径，使 `src/` 与上游源码（`src/upstream/58ea488/`）仅剩格式/import 层面差异，消除所有功能性差异。

**关键量化指标**: src/ 二开新增文件数从 29 → 0，功能修改文件数从 10 → 0。

### 问题现状

通过 `diff -rq src/upstream/58ea488/ src/` 完整对比，发现三类差异：

| 差异类别 | 数量 | 说明 |
|---------|------|------|
| **A: 仅当前 src/ 有的文件（纯新增）** | **29 项** | 上游完全不存在的文件/目录 |
| **B: 两者都有但 `diff -w` 有输出的文件（功能修改）** | **67 个** | 有语义逻辑变化 |
| **C: 两者都有但 `diff -w` 全空的文件（纯格式差异）** | **4 个** | 仅行尾/空白差异，无需处理 |

> ⚠️ **勘误**：早前版本误以为"~61 个格式差异"，实际 `diff -w` 验证发现仅 **4 个**文件是纯格式差异，其余 67 个均有语义变更。

#### 类别 A：29 个二开新增文件

| 子类别 | # | 文件 | 目标位置 |
|-------|---|------|---------|
| **A1: 外部库适配器（F-48.1）** | 7 | `agent/_outlines_adapter.py`, `context_system/_gitpython_adapter.py`, `hooks/_pluggy_adapter.py`, `permissions/_treesitter_adapter.py`, `settings/pydantic_adapter.py`, `skills/_frontmatter_adapter.py`, `providers/_litellm_adapter.py`（后 1 已解耦） | `extensions/providers_ext/`, `clawcodex_ext/runtime/`, `extensions/hooks/`, `clawcodex_ext/permissions/`, `clawcodex_ext/settings/`, `extensions/skills_ext/` |
| **A2: Orchestrator 配套工具** | 5 | `ask_issue_author.py`, `progress_report.py`, `task_directives.py`, `task_inspect.py`, `create_agent_tool.py` | `extensions/orchestrator/tools/`, `extensions/tool_system_ext/` |
| **A3: Provider 扩展** | 3 | `codex_models.py`, `openai_codex_provider.py`, `runtime.py` | `extensions/providers_ext/`, `clawcodex_ext/runtime/` |
| **A4: Auth 子系统** | 2 | `codex_oauth.py`, `codex_store.py` | `clawcodex_ext/auth/` |
| **A5: TUI 屏幕** | 2 | `ask_user_question.py`, `permission_mode_picker.py` | `clawcodex_ext/tui/screens/` |
| **A6: 服务/工具** | 5 | `services/bridge/`, `tail_follower.py`, `background_escape.py`, `cache_warning.py`, `session_watcher.py` | `clawcodex_ext/services/`, `clawcodex_ext/repl/`, `clawcodex_ext/runtime/` |
| **A7: 已部分解耦** | 5 | `background_runner/state.py`（已有 ext re-export）, `tool_authoring/`, `orchestrator/`（已完成）, `entrypoints/orchestrator.py`（已完成） | `clawcodex_ext/agent/`, `extensions/tool_system_ext/` ✅ |

#### 类别 B：67 个功能修改文件

**Phase 1-3 已设计解耦（10 个）：**

| # | 文件 | 修改点 | diff 规模 | 解耦模式 |
|---|------|--------|----------|---------|
| 1 | `repl/core.py` | provider 构建改 `build_provider_from_config`；构造器新增 6 个参数 | ~200 行 | 子类覆盖+命令注册表 |
| 2 | `tui/app.py` | Ctrl+B/Fork-Continue、runtime_context、resume、permission cycling | ~250 行 | 子类覆盖（已大部分完成） |
| 3 | `tui/commands.py` | `/model` 改 `open_dialog`；移除命令；`/repl` 改信号 | ~30 行 | 命令注册表 |
| 4 | `entrypoints/tui.py` | provider/session/resume/runtime_context 注入 seam | ~80 行 | 前端注册表 |
| 5 | `entrypoints/headless.py` | provider/session/tool_registry 注入 | ~60 行 | 前端注册表 |
| 6 | `cli.py` | **已完全解耦**—纯 facade | ✅ 已完成 | Facade 模式 |
| 7 | `context_system/prompt_assembly.py` | `memory_scopes` 参数 + try-import 降级 | ~20 行 | 构建器注册表 |
| 8 | `permissions/cycle.py` | 新增 `dontAsk` 环节 | ~10 行 | 循环表注册表 |
| 9 | `command_system/types.py` | `CommandContext` 新增 3 字段 | ~15 行 | Protocol 扩展 |
| 10 | `command_system/engine.py` | `create_command_context` 3 参数透传 | ~10 行 | Protocol 扩展 |

**Phase 4-9 新发现未覆盖（57 个）：**

| 模块 | 数量 | 文件 | 处理阶段 |
|------|------|------|---------|
| bridge/ | 5 | `__init__.py`, `bridge_main.py`, `bridge_pointer.py`, `repl_bridge.py`, `worktree.py` | Phase 4 |
| buddy/ | 8 | `__init__.py`, `companion.py`, `feature.py`, `observer.py`, `prompt.py`, `soul.py`, `sprites.py`, `types.py` | Phase 5 |
| settings/ | 4 | `__init__.py`, `constants.py`, `types.py`, `validation.py` | Phase 6 |
| providers/ | 4 | `__init__.py`, `base.py`, `anthropic_provider.py`, `openai_compatible.py` | Phase 7 |
| transports/ | 3 | `hybrid_transport.py`, `serial_batch_event_uploader.py`, `websocket_transport.py` | Phase 8 |
| tui/（除已覆盖）| 12 | `state.py`, `keybindings.py`, `agent_bridge.py`, `messages.py`, `screens/*`, `widgets/*` | Phase 9 |
| query/ | 3 | `engine.py`, `query.py`, `agent_loop_compat.py` | Phase 9 |
| coordinator/ | 2 | `mode.py`, `prompt.py` | Phase 9 |
| tool_system/ | 4 | `tools/__init__.py`, `tools/agent.py`, `context.py`, `bash/bash_tool.py` | Phase 9 |
| command_system/ | 3 | `__init__.py`, `buddy_command.py`, `builtins.py` | Phase 9 |
| repl/（除已覆盖）| 2 | `__init__.py`, `live_status.py` | Phase 9 |
| 散在文件 | 7 | `agent/session.py`, `config.py`, `constants/xml.py`, `permissions/modes.py`, `memdir/memdir.py`, `skills/bundled/loop.py`, `utils/stream_watchdog.py` | Phase 9 |

### 已完成的解耦模式（可复用）

1. **Facade 模式**（`src/cli.py`）— src/ 只剩 `from clawcodex_ext.xxx import yyy; return yyy()`
2. **子类覆盖模式**（`clawcodex_ext/tui/app.py`）— `ClawCodexExtTUI(ClawCodexTUI)` 覆盖 hook 方法
3. **前端注册表模式**（`clawcodex_ext/frontend/`）— `@register_frontend` + `get_frontend()` 工厂

### 解耦方案

#### Phase 0: 纯新增文件移入 ext（29 项，无风险）

| 子阶段 | 内容 | 工作量 |
|--------|------|--------|
| **P0-A (F-48.1)** | 7 个 adapter 统一迁移到 ext（去 `_` 前缀，src/ 留 2-3 行 re-export） | 1-2 天 |
| **P0-B** | 5 个 orchestrator 工具移到 `extensions/orchestrator/tools/` | 1-2 天 |
| **P0-C** | 3 个 provider 扩展移到 `extensions/providers_ext/` + `clawcodex_ext/runtime/` | 0.5 天 |
| **P0-D** | 2 个 auth 文件移到 `clawcodex_ext/auth/` | 0.5 天 |
| **P0-E** | 2 个 TUI 屏幕移到 `clawcodex_ext/tui/screens/` | 0.5 天 |
| **P0-F** | 5 个服务/工具移到 `clawcodex_ext/services/`/`repl/`/`runtime/` | 1 天 |
| **P0-G** | `tool_authoring/` 移到 `extensions/tool_system_ext/` | 0.5 天 |

**优先原则**：新增文件迁移优先于修改文件解耦——消除 29 个"Only in src/"比消除 10 个功能修改更容易且立即减少 diff -rq 噪声。

#### Phase 1: 注册表/Protocol 扩展消除字段注入（低风险）

- `src/permissions/cycle.py` → 循环表注册表：`_CYCLE_TABLE` 默认上游循环，ext 注册 `dontAsk`
- `src/command_system/types.py` → `DownstreamCommandContext(Protocol)` 扩展注入
- `src/command_system/engine.py` → 同上 Protocol，`create_command_context` 保持上游签名
- `src/context_system/prompt_assembly.py` → `memory_section_builder` 回调注册表

#### Phase 2: 子类覆盖模式恢复上游构造器签名（中等风险）

- `src/repl/core.py` → `ClawCodexExtREPL(ClawcodexREPL)` 子类 + 命令注册表 + Provider 工厂注册表
- `src/tui/commands.py` → `register_tui_command()` 注入
- `src/tui/app.py` → 审计 `ClawCodexExtTUI` 是否完全覆盖

#### Phase 3: 入口点恢复上游逻辑（需谨慎）

- `src/entrypoints/tui.py` → `run_tui()` 恢复上游逻辑
- `src/entrypoints/headless.py` → `run_headless()` 恢复上游逻辑
- `src/entrypoints/repl.py` → ext 的 `REPLFrontend.run()` 负责组装

#### Phase 4: Bridge 文件回归（新增，中等风险）

| 文件 | 解耦方案 | 工作量 |
|------|---------|--------|
| `bridge/__init__.py` | 评审并还原导出列表 | 0.5天 |
| `bridge/bridge_main.py` | 确认 JWT refresh 删除等差异来源后还原 | 1天 |
| `bridge/repl_bridge.py` | 还原 docstring，功能差异逐行评审 | 1天 |
| `bridge/bridge_pointer.py`, `worktree.py` | 还原导出列表 | 0.5天 |

#### Phase 5: Buddy 文件回归（新增，低风险）
8 个 `buddy/` 文件主要为 docstring 差异 + 缓存行为变更，优先还原。0.5天。

#### Phase 6: Settings 文件回归（新增，低风险）
`settings/` 4 个文件差异来自 F-47 重构，保持现状。评审 `constants.py` 差异。0.5天。

### 2026-06 批次完成项（F-48.2）

| # | 源文件 | 解耦操作 | 新扩展位置 | 验证 |
|---|--------|---------|-----------|------|
| 1 | `tool_system/tools/__init__.py` | 移除 `ProgressReportTool`、`TaskDirectivesTool`、`TaskInspectTool` 注册。**与 upstream 完全一致** | `extensions/tool_system_ext/registration.py` | ✅ 256 tests passed |
| 2 | `providers/__init__.py` | `openai-codex` 的 `PROVIDER_INFO` 移至 `clawcodex_ext`（`get_provider_class` 因循环导入暂留） | `clawcodex_ext/providers/__init__.py` + `src/providers/runtime.py` facade 补充导出 | ✅ 256 tests passed |
| 3 | `agent/session.py` | `resume_with_tail()` 提取为独立函数 | `clawcodex_ext/agent/session_ext.py` | ✅ 256 tests passed |

### 剩余待解耦项

#### Phase 7: Provider 文件回归（新增，中等风险）

| 文件 | 解耦方案 | 工作量 |
|------|---------|--------|
| `providers/base.py` | `ThinkingChunkCallback` 评估是否可通过 Protocol 扩展到 ext | 1天 |
| `providers/__init__.py` | 保持现状（二开 provider 注册点） | 0天 |
| `providers/anthropic_provider.py` | 逐行评审差异 | 1天 |
| `providers/openai_compatible.py` | 逐行评审差异 | 1天 |

#### Phase 8: Transport 文件回归（新增，中等风险）
3 个 `transports/` 文件差异可能是 bridge 集成的必要修改，逐行评审。1.5天。

#### Phase 9: 其余散在文件回归（新增，高风险）

| 模块 | 文件数 | 工作量 | 备注 | 状态 |
|------|--------|--------|------|------|
| tui/* | 12 | 2-3天 | PendingAskUser、Ctrl+B、thinking toggle、permission mode 等 | ⏳ |
| query/* | 3 | 1天 | 查询引擎修改 | ⏳ |
| coordinator/* | 2 | 0.5天 | 轻量工具集注册 | ⏳ |
| tool_system/* | 1（已解耦 3/4） | 0.5天 | `context.py`+`tools/agent.py`+`bash/bash_tool.py` 待处理 | ✅ 3/4 已解耦 |
| command_system/* | 3 | 0.5天 | Buddy 命令注册 | ⏳ |
| agent/session.py | 0 | — | ✅ `resume_with_tail` 已解耦 | ✅ |
| 散在 6 个 | 6 | 1天 | config/modes/memdir 等 | ⏳ |

### 解耦前后效果对比

| 指标 | 解耦前 | 当前实际（2026-06） | 解耦后（乐观） |
|------|--------|-------------------|---------------|
| src/ 二开新增文件 | 29 项 | **0** ✅ | **0** ✅ |
| src/ 功能修改文件 | 67 个 | **~60**（3 项已完成） | **~10-20** |
| src/ 与上游 diff -rq 差异 | 71 修改 + 29 Only in | **68 修改**（3 项已消除） | **~10-20 核心修改** |
| 上游同步冲突 | 高（每次 820+ 行差异） | **降低约 30%** | **低** |
| decoupled/src 比例 | ~30% | **~92%** 🟢 | **~90%** |

### 验收标准

1. `diff -rq src/ src/upstream/58ea488/` 不再有"Only in src/"输出（29→0 ✅）
2. Phase 1-3 的 10 个功能修改文件 `diff -w` 返回空
3. Phase 4-9 覆盖的 57 个文件完成评审，输出 `docs/decisions/f48-modification-tracking.md`
4. 所有现有功能测试通过
5. REPL/TUI/Headless 三前端完整可用

### 风险与约束

| 风险 | 缓解措施 |
|------|---------|
| 57 个文件修改来源不明，误还原上游修复 | `git log` 逐行追溯，标注来源 |
| Provider 基类差异影响全局 LLM 调用 | Phase 7 优先；差异需团队审阅 |
| tui/ 子类覆盖方案未完全消除本体注入 | Phase 9 逐文件审计 |
| 57 文件全量追溯需 2-3 周 | Phase 4-9 分步执行，非全量冻结 |

### 设计决定

| # | 决定 | 理由 |
|---|------|------|
| 1 | **Phase 4-9 不追求 100% 还原** | bridge/buddy/transport 的差异可能是必要的二开功能 |
| 2 | **每文件记录决策理由** | 输出 `docs/decisions/f48-modification-tracking.md` |
| 3 | **新增文件迁移（Phase 0）优先执行** | 消除"Only in src/"后 diff 噪声骤降 |
| 4 | 格式差异（4 个文件）不处理 | `diff -w` 已确认无语义差异 |

### 依赖与协同

- **依赖**: F-34（前端注册表解耦）✅, F-35（二开特性切换）, F-47（settings/ 文件差异源头）
- **协同**: F-15（循环表注册表）, F-43（runtime_context Protocol）, F-28（background_runner 移 ext）, F-49（session.py SessionStorage 差异）, F-41（coordinator/ 差异）
- **先于**: F-35 的 584 文件还原

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

#### Phase 0 — 统一事件存储：Message 转录接入（1-2天）

统一 headless agent 和 REPL 会话的存储格式，使 headless agent 的每个 tool_use / tool_result / text_delta 以 **Message dict 格式**写入 `~/.clawcodex/sessions/{run_id}/transcript.jsonl`，替换现有的 `.event_logs/{issue_id}.ndjson` 扁平格式。

| 文件 | 改动 |
|------|------|
| `extensions/orchestrator/agent_runner.py` | `AgentSession` 增加 `session_storage: SessionStorage`；`run()` 中 `init_metadata(model, cwd, title)`；替换 `_write_event_log()` → `session_storage.write_raw(msg_dict)` + `flush()` |
| `extensions/orchestrator/agent_runner.py` | 删除 `_write_event_log()` 方法；删除 `.event_logs/` 目录创建逻辑 |
| `extensions/orchestrator/cli/issue.py` | `_run_tail` 改为读 `transcript.jsonl`（或保留兼容双读） |

**Phase 0.1 — Message 转录映射规则**（核心契约）

`QueryRunner.stream()` 产出的扁平事件必须按 LLM 响应轮次分组为 `assistant` / `user` Message：

```
一轮 LLM 响应（TurnComplete 为止）：
  TextDelta × N                          ─┐
  ToolCallEvent(tool_use_id=T1) × M      ─┤→ AssistantMessage
                                           │   content = [TextBlock, ToolUseBlock, …]
                                           │   按事件流顺序交替排列
                                           └→ SessionStorage.write_raw(assistant_msg_dict)

  ToolResultEvent(tool_use_id=T1) × M     ──→ UserMessage
                                                content = [ToolResultBlock, …]
                                                ↓ SessionStorage.write_raw(user_msg_dict)

  下一轮 prompt 写入                      ──→ UserMessage
                                                content = [TextBlock(text=continuation)]
                                                ↓ SessionStorage.write_raw(continuation_msg_dict)
```

| 事件序列 | Message 类型 | `content` 结构 |
|----------|-------------|----------------|
| 首个 turn 的 user prompt | `UserMessage` | `[TextBlock(text=prompt)]` |
| `TextDelta` × N + `ToolCallEvent` × 0 | `AssistantMessage` | `[TextBlock(text=concat(deltas))]` |
| `TextDelta` × N + `ToolCallEvent` × M | `AssistantMessage` | `[TextBlock, ToolUseBlock, ...]` 交替排列 |
| `ToolResultEvent` × M | `UserMessage` | `[ToolResultBlock(tool_use_id, content), ...]` |
| 后续 turn 的 continuation prompt | `UserMessage` | `[TextBlock(text=continuation_prompt)]` |
| `SessionComplete` | 不写 Message | 调用 `flush()` 确保缓冲落盘 |

关键约束：ToolResultEvent 可能乱序，需用 `dict[tool_use_id]` 累积；被 approval policy 拒绝的 tool call 也要写入 result（`is_error=True`）；TurnComplete 时才知道本轮 LLM 输出结束，此时一次性组装 assistant message。

**Phase 0.2 — CLI 介入：会话恢复（--resume）+ 实时观察 + 问题追溯**

统一格式后的核心收益：**`clawcodex --resume <run_id>` 可直接恢复 orchestrator headless agent run 的完整对话，进入交互式 REPL**，operator 可继续对话，新内容追加到同一 transcript。

| 场景 | 机制 | 代码来源 |
|------|------|---------|
| **完整会话恢复（核心）** | `clawcodex --resume <run_id>` → `Session.resume(run_id)` 读取 transcript + metadata，重建 Conversation，进入交互式 REPL | `src.agent.session.Session.resume()` — 完全复用，0 改动 |
| **TUI 实时增量观察** | `clawcodex --tui --resume <run_id>` → TailFollower 从 transcript 末尾输出增量 | `src.services.tail_follower.TailFollower` — 完全复用 |
| **接管 agent run** | operator 在 REPL 中直接输入指令替代 headless agent 的下一 turn；退出可选 detach / finish / re-orchestrate | `Session.resume()` + 前台 REPL |
| **崩溃恢复** | orchestrator 检测到 agent 进程退出后，用 `Session.resume()` 重建 context，在新的 `AgentRunner` 中继续 | `Session.resume()` → `session_resume.resume_session()` |
| **只读追溯** | `issue transcript --run <run_id>` 文本输出对话历史，适合管道处理 | 新增 `_run_transcript` 子命令 |

`--resume` 三种模式：

```
clawcodex --resume <run_id>               → 完整会话恢复，进入交互式 REPL
clawcodex --tui --resume <run_id>         → TUI 模式，TailFollower 增量显示 + 可输入
clawcodex --resume <run_id> --readonly    → 只读查看历史，不进入交互模式
```

并发安全：agent 已结束时正常恢复可写；agent 正在运行时 `--resume` 获得只读历史快照不干扰运行中 agent；需写入需通过 socket 先 pause。

**Phase 0.3 — 大内容文件引用**

复用 `SessionStorage._replace_large_content()` 内置行为，自动将大 tool result 替换为文件引用（存储于 `~/.clawcodex/sessions/<run_id>/content/`），AgentRunner 无需感知。

验收标准：headless agent 的每轮 tool_use / tool_result / text_delta 以 Message dict 格式写入 session JSONL，`TailFollower` 可直接 follow，`session_resume` 可直接重建 LLM context。整个 Phase 0 不修改 `src/services/session_storage.py` 一行代码。

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

*版本 v2.17 更新：全面更新 F-48 src/ 核心路径二开修改解耦方案。通过完整对比 `src/` 与 `src/upstream/58ea488/` 的 diff，将解耦范围从"10 个功能性修改文件"扩展到全部 **29 个纯新增文件 + 7 个外部库适配器 + 71 个功能修改文件**。新增按模块类别的七组解耦子方案（Adapter、Orchestrator Tools、Provider Extensions、Auth、TUI Screens、Services、Utilities），并单独设立 F-48.1 Adapter 文件统一解耦子特性。新增类别 B 修改文件解耦表格覆盖 10 个已识别功能修改点。目标：src/ 二开新增文件数从 29 降为 0，src/ 功能修改文件数从 10 降为 0，decoupled/src 比例从约 30% 提升至 100%。详见 §六 FEATURE_PLAN.md。*

*版本 v2.14 更新：新增 F-48 src/ 核心路径二开修改解耦方案（📋 设计完成）。通过对比 `src/` 与 `src/upstream/58ea488/`，识别出 10 个含真正功能修改的 src/ 文件，分 Phase 0~3 四阶段制定解耦方案。*

*版本 v2.13 更新：新增 F-45 / F-46 / F-47。F-45 P1 在 `agent_runner._handle_tool_call` 后加 NDJSON 旁路落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，与 permission_mode 解耦；扩展 `report_writer.RunReport.tool_events_path` 字段 + markdown 模板登记路径；终结 "bypass ≠ 无审计" 误读。F-46 P2 把 `permission_mode` enum 拆为 `interactive` / `default_decision` / `audit_log` 三个正交字段，F-46.0（v2.13）只拆 `audit_log`，依赖 F-45 落地后端到端验证；`permission_mode` 保留为 backward-compat shim 标 deprecated；F-46.1+ 拆其余两字段推到 v2.15+。*

*版本 v2.11 更新: F-42 Sequential Workspace 策略实现完成。`workspace.strategy: isolated | shared | sequential` 落地，sequential 强制单并发并使用顺序锁，共享 root 上的 integration branch 叠加 commit 链，commit 元数据（base/start SHA、sequence_index）写入 registry，sequential GitSync 本地 commit 不 push/PR，shared/sequential root 在 cleanup 时保留。19 个专项测试 + 245 个 orchestrator 回归全部通过。*

*版本 v2.10 更新: 新增 F-42 Orchestrator Shared / Sequential Workspace 策略设计。规划 `workspace.strategy: isolated | shared | sequential`，支持本地 feature-plan issue 在同一 working tree / integration branch 上按顺序叠加开发；保留旧 isolated 行为，并设计单并发校验、顺序锁、dirty tree guard、commit 链 registry 元数据、GitSync/cleanup preserve 语义与两 issue 端到端验收。*

*版本 v2.7 更新: 新增 F-41 Coordinator 轻量工具集。扩展 `_COORDINATOR_ALLOWED_TOOLS` 使 Coordinator 获得 Read / WebSearch / WebFetch 三个轻量工具，合计 6 个。写/执行工具仍隔离，强制委派给 Worker。提示词同步更新。231/231 orchestrator 测试通过。*

*版本 v2.6 更新: 修复 `progress_reporter` 死代码,phase completion 接入 ndjson event log (F-38 Sub-D 落地)。新增 F-40 ProgressReporter Sink 协议重构。


---


## F-50: SOP 转换器源码固化（SourceCodeParser + 增强 SkillGrouper + AgentMarkdownWriter）

**状态**: ✅ 完成
**优先级**: P1

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.14 F-50 SOP 转换器源码固化](./ARCHIVED_PROGRESS.md#五14-f-50-pos-转换器源码固化sourcecodeparser--增强-skillgrouper--agentmarkdownwriter)。

---


## F-51: AgentRunner 空转检测机制（no-op detection）

**状态**: ✅ 完成
**优先级**: P0

> 详细进度已归档至 [ARCHIVED_PROGRESS.md §五.12 F-51 AgentRunner 空转检测机制](./ARCHIVED_PROGRESS.md#五12-f-51-agentrunner-空转检测机制no-op-detection)。

---


## F-52: Python SDK 方法注册为 Tool

**状态**: 📋 规划中
**优先级**: P2
**规划文档**: `docs/FEATURE_PLAN.md` → `§6.3 Python SDK 方法注册为 Tool（F-52）`
**依赖**: F-50（SourceCodeParser 已输出 SourceOperation）

### 目标

将 SOP 转换解析出的 `SourceOperation`（如 `detect_modality`、`load_dataset`）注册为 clawcodex 可调用的 `Tool` 对象。使 sub-agent 的 `tools` 列表中的方法名从字符串占位符变为可执行的 Tool，不再需要通过 `Bash` subprocess 回退。

### 实现计划

| 组件 | 文件 | 说明 |
|------|------|------|
| `ToolWrapper` | `extensions/pos_converter/tool_registry.py` | 将 `SourceOperation` 包装为 `Tool` 对象，ParamSpec→JSON Schema |
| `register_source_operations` | `extensions/pos_converter/tool_registry.py` | 批量注册某 agent 的所有 operations 到 ToolRegistry |
| `AgentBuilder` 增量 | `extensions/pos_converter/agent_builder.py` | `build()` 在生成 markdown 后自动注册 tool |
| `agent_loader_hook.py` | `extensions/pos_converter/agent_loader_hook.py` | 加载 agent markdown 时按 `source_path` 字段自动注册 |

### 验收标准

1. `ToolWrapper(operation).to_tool().name == "detect_modality"`
2. 注册后 `registry.get_tool("detect_modality")` 返回有效 `Tool`
3. 无 Python 源文件时优雅降级
4. `python3 -m pytest tests/test_pos_converter_tool_registry.py -q` 通过

---


## F-53: Tool 自动暴露为 CLI 斜杠命令

**状态**: 📋 规划中
**优先级**: P3
**规划文档**: `docs/FEATURE_PLAN.md` → `§6.4 Tool 自动暴露为 CLI 斜杠命令（F-53）`
**依赖**: F-52（Tool 注册机制是前置条件）

### 目标

将已注册的 `Tool` 自动暴露为 REPL/TUI 中的 `/tool-name` 斜杠命令，使用户可在 CLI 直接输入 `/detect_modality --path /data/raw` 执行已注册的 ADF 方法。

### 实现计划

| 组件 | 路径 | 说明 |
|------|------|------|
| `DynamicCommandDiscovery` | `clawcodex_ext/cli/tool_cmd/discovery.py` | 扫描 ToolRegistry 中非核心工具，自动生成命令定义 |
| `DynamicToolCommand` | `clawcodex_ext/cli/tool_cmd/command.py` | tool→command 适配器，ParamSpec→argparse 参数 |
| 注册钩子 | `clawcodex_ext/cli/tool_cmd/hooks.py` | subcommand_registry 加载时注册 `/<name>` 命令 |

### 验收标准

1. 核心工具（Read/Write/Bash）不产生 `/read` 等命令
2. `/detect_modality --path /data/sample.mp4` 等价于 `Tool("detect_modality").execute({"path": "/data/sample.mp4"})`
3. TUI 斜杠补全包含已注册工具
4. `python3 -m pytest tests/test_tool_cmd*.py -q` 通过

---


## F-54: AgentRunner / QueryRunner 运行期可观测性

**状态**: 📋 设计完成
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `§3.1.13 AgentRunner / QueryRunner 运行期可观测性与 stuck-run debug（F-54）`
**依赖**: F-38（验证与报告闭环）、F-40（ProgressSink 事件扇出）、F-45（tool-events 审计旁路）、F-49（会话统一存储，长期目标）

### 背景

2026-06-04 本地 sequential orchestrator 运行 F-40 issue 时，daemon 日志显示 headless agent 已启动并向 MiniMax Anthropic-compatible endpoint 发起 provider request，但 issue 长时间停留在 `running`：workspace 无文件改动、`.event_logs/` 无有效事件、无 report、无 commit，registry 也没有终态更新。

已落地的 orchestrator-level watchdog 可以防止永久 `running`：`agent.run_timeout_ms` 超时后标记 `agent_timeout` 并进入 retry。但 watchdog 只能终止卡死 session，不能解释 agent 卡在 headless/query 的哪一层。

### 问题根因

当前 `QueryRunner.stream()` 只有在 `run_headless_session(...)` 返回后才把 stdout 作为 `TextDelta` 发出；如果 headless future 长时间 pending，且 `on_event` 没有桥接任何 tool event，`AgentRunner.run()` 看不到 `TextDelta` / `ToolCallEvent` / `ToolResultEvent` / `SessionComplete`，下游 `ProgressReporter`、event log、report writer、git sync 都没有可消费信号。

### 设计目标

1. 在不修改 `src/` 核心代码的前提下，为 `extensions/api/query.py` 与 `extensions/orchestrator/agent_runner.py` 的 headless issue 路径补齐 debug 观测点。
2. 能区分 stuck-run 卡在 provider 请求、headless future、事件桥接、AgentRunner 事件消费、workspace/git sync 哪一层。
3. watchdog timeout 时持久化最后一次可观测状态，避免只留下 `agent_timeout` 这一类粗粒度原因。
4. 将诊断摘要写入 registry / CLI 可见字段，让 `clawcodex-dev orchestrator issue list/status` 能直接解释“最后一次 agent 事件是什么”。
5. 与 F-49 长期会话统一存储兼容：短期写轻量 `debug.ndjson`，长期可并入 `SessionStorage` / attach socket。

### 观测点计划

| 层级 | 观测点 | 记录内容 |
|------|--------|----------|
| `QueryRunner.stream()` start | headless session 启动 | workspace、provider、model、permission_mode、max_turns、prompt length、run_id（若可获得） |
| `QueryRunner.on_event` | headless bridge 收到事件 | kind、tool_name、tool_use_id、event_count、seconds_since_start |
| `QueryRunner.stream()` heartbeat | future pending 期间周期性输出 debug | future done/pending、seconds_since_last_event、event counts、stdout length |
| `AgentRunner.run()` turn start/end | 每轮 turn 生命周期 | issue_id、run_id、turn、turn_has_tool_calls、turn_output_len、tool_count、workspace dirty 状态、no-op counter |
| `AgentRunner.run()` event receive | 每个 QueryEvent 被消费 | event type、tool name、text length、session_complete reason、last_event_at |
| `Orchestrator._run_issue()` timeout | watchdog 触发 | session status、turn_count、tool_count、last_event_type、last_tool_name、workspace_dirty、event_log_path、tool_events_path |

### 持久化格式

短期新增 per-run debug NDJSON，建议路径：

```text
{workspace}/.orchestrator_control/runs/{run_id}/debug.ndjson
```

示例行：

```json
{"ts": "2026-06-04T20:01:26Z", "stage": "agent_runner.start", "issue_id": "F-40-progress-sink", "run_id": "..."}
{"ts": "2026-06-04T20:01:27Z", "stage": "query_runner.start", "provider": "minimax", "permission_mode": "bypassPermissions", "prompt_len": 18420}
{"ts": "2026-06-04T20:03:27Z", "stage": "query_runner.heartbeat", "future_done": false, "seconds_since_last_event": 120, "stdout_len": 0, "tool_events": 0}
{"ts": "2026-06-04T20:29:57Z", "stage": "orchestrator.timeout", "turn_count": 0, "tool_count": 0, "last_event_type": null, "workspace_dirty": false}
```

### Registry / CLI 摘要字段

优先复用现有 `IssueRecord.verification_output` / `last_hook_error` 存放 timeout 摘要；若需要结构化查询，再新增字段：

| 字段 | 含义 |
|------|------|
| `run_id` | 当前或最后一次 agent run id |
| `last_agent_event_at` | AgentRunner 最近收到 QueryEvent 的时间 |
| `last_agent_event` | 最近事件类型，如 `ToolCallEvent:Read` / `TextDelta` / `SessionComplete:success` |
| `last_tool_name` | 最近 tool call / result 名称 |
| `turn_count` | 当前 session 已完成 turn 数 |
| `tool_count` | 当前 session 已消费 tool event 数 |
| `timeout_deadline_at` | watchdog 预计触发时间 |
| `debug_log_path` | `debug.ndjson` 路径 |

### 实施阶段

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | 新增轻量 debug writer，写入 `{workspace}/.orchestrator_control/runs/{run_id}/debug.ndjson`，并确保 sequential workspace 下不会进入 git commit | 📋 待开始 |
| 2 | 在 `QueryRunner.stream()` 增加 start / event / heartbeat 观测点；heartbeat 只写 debug log，不产生用户可见 `TextDelta`，避免污染 agent 输出 | 📋 待开始 |
| 3 | 在 `AgentRunner.run()` 增加 turn/event counters 与 last-event snapshot；每轮结束记录 workspace dirty/no-op 状态 | 📋 待开始 |
| 4 | watchdog timeout 时写 diagnostic snapshot，并把摘要同步到 registry 可见字段 | 📋 待开始 |
| 5 | CLI issue status/list 增加 debug 摘要展示，至少显示 run_id、last_agent_event、turn_count、tool_count、debug_log_path | 📋 待开始 |
| 6 | 增加 focused tests：hanging QueryRunner heartbeat、agent timeout snapshot、registry debug fields、debug log 不进入 git diff | 📋 待开始 |

### 验收标准

1. 当 headless future pending 且无 tool event 时，`debug.ndjson` 至少周期性出现 `query_runner.heartbeat`。
2. 当 `ToolCallEvent` / `ToolResultEvent` / `SessionComplete` 正常出现时，`AgentRunner` 记录 last event 与 counters，并在 registry/CLI 中可见。
3. watchdog timeout 后，registry 中的 failure reason 包含 run_id、turn_count、tool_count、last event、debug log path。
4. sequential workspace 下 debug 文件写入 `.orchestrator_control/` 或等价控制目录，且不会被 `git_sync` 提交。
5. F-49 落地后，`debug.ndjson` 可被迁移或双写到统一 `SessionStorage`，不形成第二套长期事件源。

### 依赖与协同

- 与 F-40 协同：F-40 解决 progress event 扇出与 session 结束落点；F-54 解决 QueryRunner/AgentRunner 在没有 progress event 时的诊断盲区。
- 与 F-45 协同：F-45 的 `tool-events.ndjson` 记录已通过 tool approval/handler 的事件；F-54 记录更早的 headless bridge 与 AgentRunner 消费状态。
- 与 F-49 协同：F-54 是低成本 debug 先行层；F-49 后续把长期 transcript、attach、resume 统一到 `SessionStorage`。
- 与 watchdog 协同：watchdog 负责 fail-closed 和 retry，F-54 负责解释为什么 watchdog 被触发。

---

## 会话恢复（Session Resume）增强（§6）

**状态**: 📋 设计完成
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `§六 会话恢复增强`
**依赖**: 无

### 目标

对标 claude-code-best（CCB）的会话恢复体验，补齐以下三个核心特性缺口：

1. **退出时打印 Resume Hint**（S-R1）：所有退出路径在 TTY 主缓冲区打印 `Resume this session with: clawcodex --tui --resume <sessionId>`
2. **Resume 后历史消息渲染完整**（S-R2）：`_replay_history()` 中 user 消息不应被跳过
3. **`--continue` CLI 快捷命令**（S-R3）：无需记忆 session ID，自动恢复最近会话

### 当前基线

ClawCodex 已有：
- `Session.resume()` — 核心恢复逻辑（`src/session/session_resume.py`）
- `_sync_conversation_from_transcript()` — 从 transcript 重建消息列表（`src/repl/core.py`）
- `ResumeConversation` — 交互式 session 浏览器（`src/session/resume_conversation.py`）
- `_replay_history()` — TUI 启动后重放历史（`src/tui/app.py` L1108-1161，但跳过 user 消息）
- `__FULL_EXIT__` 路径的打印 hint（`src/repl/core.py` L2143-2153，仅单个退出路径）

CCB 还具备但 ClawCodex 缺失的：
- `printResumeHint()` 在所有退出路径调用（`src/utils/gracefulShutdown.ts` L141-176）
- `loadConversationForResume(undefined)` 自动查找最新会话（`src/services/sessionManagement/sessionRestore.ts`）
- `launchRepl({initialMessages: loaded.messages})` 完整消息直通渲染
- `checkResumeConsistency()` 一致性检查
- `restoreCostStateForSession()` / `restoreSessionMetadata()` / `restoreAgentFromSession()` 状态恢复

### 实施阶段

#### Phase 0 — 退出路径统一打印 Resume Hint（S-R1，1-2天）

**目标**：所有退出路径都能在 TTY 主缓冲区打印 session ID。

| 任务 | 文件 | 说明 |
|------|------|------|
| 提取 `_print_resume_hint()` 工具函数 | `src/repl/core.py` 或新模块 | 判断 TTY + 交互 + 持久化启用，打印 hint |
| 在 `__FULL_EXIT__` 路径复用该函数 | `src/repl/core.py` | 去重，替换现有硬编码打印 |
| 在正常 `/exit` 退出路径调用 | `src/repl/core.py` `exit()` | 确保非 FULL_EXIT 也打印 |
| 在 `Ctrl+C` / `KeyboardInterrupt` 路径调用 | `src/tui/app.py` 或入口点 | 捕获 KeyboardInterrupt 后打印 |
| 在 SIGTERM handler 中调用 | 注册 signal handler | 可选（P1） |
| 确保退出 alt-screen 后再打印 | `src/entrypoints/tui.py` `run_tui()` | `app.run()` 返回后，打印到主缓冲区 |

**验收标准**：
- `/exit` 后终端可见 `Resume this session with:` 提示
- `Ctrl+C` 后同样可见（`KeyboardInterrupt` 捕获后打印）
- session ID 或自定义标题正确显示
- 仅 TTY + 交互 + 持久化启用时才打印

#### Phase 1 — Resume 后完整历史渲染（S-R2，0.5-1天）

**目标**：`--resume` 启动后所有消息（含 user 消息）完整渲染。

| 任务 | 文件 | 说明 |
|------|------|------|
| 移除 `_replay_history()` 中 `if role == "user": continue` | `src/tui/app.py` L1108-1161 | 改为渲染所有 role 的消息 |
| 确保 user 消息的文本格式与原始输入一致 | `src/tui/app.py` `_flatten_message_text()` | 验证消息内容提取逻辑 |
| 格式一致性检查：resume 渲染 vs 原始 exit 前显示 | 手动测试 | 确认无格式退化 |

**验收标准**：
- `--resume <sessionId>` 后历史完整，能看到自己之前的所有输入
- user + assistant + tool 消息交替显示，如同从未退出

#### Phase 2 — `--continue` CLI 支持（S-R3，2-3天）

**目标**：`-c` / `--continue` 自动恢复最近会话。

| 任务 | 文件 | 说明 |
|------|------|------|
| 在 CLI argument parser 注册 `-c` / `--continue` | `src/cli.py` | 布尔标志，与 `--resume` 互斥 |
| 实现 `session_resume.latest()` 查找最新 transcript | `src/session/session_resume.py` | 扫描 transcript 目录，按修改时间排序 |
| 在 `main()` 中处理 `--continue` 分支 | `src/main.py` / 入口点 | 等价于 `--resume <latest_session_id>` |
| 与 `--fork-session` 组合支持 | 后续扩展（P1） | 先做基础 `--continue` |

**验收标准**：
- `clawcodex --tui --continue` 恢复最近会话
- 无最近会话时给出清晰错误提示
- 交互式浏览器仍可用

#### Phase 3 — 元数据与状态恢复（S-R4，3-5天，P1-P2）

**目标**：resume 时恢复 cost、metadata、agent 设置等旁路状态。

| 子项 | 预计工作量 | 优先级 |
|------|:----------:|:------:|
| S-R4-C: 恢复 Cost 累计状态 | 1-2天 | P1 |
| S-R4-F: `--fork-session` 支持 | 1-2天 | P1 |
| S-R4-T: 按自定义标题恢复 | 1天 | P2 |
| S-R4-M: 恢复 session metadata | 1天 | P2 |
| S-R4-A: 恢复 Agent 设置 | 1-2天 | P2 |
| S-R4-CP: 交叉项目路径调整 | 1-2天 | P2 |
| S-R4-CK: 一致性检查 | 1天 | P2 |
| S-R4-AT: resume 到指定消息位置 | 2-3天 | P3 |

### 验收标准

- ✅ `/exit` 后终端可见 resume hint
- ✅ `Ctrl+C` 后同样可见 resume hint
- ✅ `--resume <sessionId>` 后历史完整渲染（含 user 消息）
- ✅ `-c` / `--continue` 自动恢复最近会话
- ✅ 恢复的 session 在 LLM context 中与原始退出时一致
- ⏳ Resume 后 cost 累计值正确（P1）
- ⏳ `--fork-session` 创建新 session ID（P1）
- ⏳ 按自定义标题查找并恢复 session（P2）
- ⏳ Resume 后 agent 设置恢复（P2）
- ⏳ 跨目录 resume 路径正确调整（P2）

### 风险与约束

- **alt-screen 生命周期**：Textual `inline=True` 已经规避了 alt-screen 擦除的问题，但 hint 必须在 `app.run()` 返回后（而非在 app 内部）打印，否则会被文本渲染覆盖。
- **`--continue` vs `--resume` 互斥**：同时在 CLI 层做校验，避免二义性。
- **Session ID 格式**：ClawCodex 的 session ID 格式与 CCB 可能不同，确认 transcript 路径解析兼容。
- **与 F-49 的边界**：F-49（Issue 会话统一存储）定义了 `SessionStorage` 协议和 `attach/resume` 流程。会话恢复增强（§6）专注 TUI 层 resume UX 细节（S-R1~S-R3），二者不冲突。

---

## 六、Cron 系统执行引擎进度

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
| | F22-R8 | CCB-compatible gate 命名与用户心智 | ⏳ 待确认 | 当前主要使用 `CLAWCODEX_DISABLE_CRON`；若用户从 CCB 迁移，建议兼容 `CLAUDE_CODE_DISABLE_CRON` 或在文档/CLI 中明确差异。 |
| | **G9** | **SDK daemon 模式（`dir`/`lockIdentity`）** | ⏳ 待设计 | scheduler 当前依赖 bootstrap session state；daemon/headless 独立运行需支持可选 `dir` 和 `lock_identity` 参数，无 session 时自动降级。详见 FEATURE_PLAN §4.11.11。 |
| | **G10** | **`cronToHuman(utc)` UTC 模式显示** | ⏳ 待设计 | `cron_to_human()` 无 UTC 参数；需增加 `utc=True` 时按本地时区偏移显示，远程 agent 场景使用。详见 FEATURE_PLAN §4.11.11。 |

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
| | **G8** | **inFlight 防重复触发** — 异步 IO 期间用 in_flight Set 防止同一任务二次触发 | ✅ 完成 |
| | **G9** | **SDK daemon 模式（`dir`/`lockIdentity`）** — 可选脱离 session state 独立运行 | ⏳ 待设计 |
| | **G10** | **`cronToHuman(utc)` UTC 模式** — `cron_to_human()` 增加 `utc` 参数 | ⏳ 待设计 |

---

**规划任务详情已归档至 [ARCHIVED_PROGRESS.md](./ARCHIVED_PROGRESS.md)**

---


## 七、会话恢复增强进度

> F-21、F-23、F-28、F-32 的详细设计见 FEATURE_PLAN 相应章节；会话恢复增强详见本文 §6 详细章节。

## 八、CCB 对标缺口补缺进度

> 本节跟踪 CCB（claude-code-best）对标发现的 clawcodex 特性缺口实施进度。
> F-60~F-67 均参照 CCB 对应功能设计，以确保功能完整对标为目标。

### F-60: Pipe IPC + LAN 群控系统

**状态**: ⏳ 待开始 | **优先级**: P0 | **对标**: CCB Pipe IPC + LAN Pipes

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P60-A | Unix Domain Socket 命名管道通信 | ⏳ 待开始 | 5-7天 |
| P60-B | 多实例主从编排 + 面板选择 | ⏳ 待开始 | 3-5天 |
| P60-C | LAN UDP Multicast 零配置自动发现 | ⏳ 待开始 | 5-7天 |
| P60-D | 消息广播路由与权限转发 | ⏳ 待开始 | 3-5天 |
| P60-E | 跨机器 Source/Destination 选择 | ⏳ 待开始 | 3-5天 |
| P60-F | `/pipes` 面板与 Shfit+↓ 面板切换 | ⏳ 待开始 | 5-7天 |

**估算总工时**: 3-4 周

### F-61: Computer Use 屏幕操控

**状态**: ⏳ 待开始 | **优先级**: P0 | **对标**: CCB Computer Use

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P61-A | 跨平台截图 (macOS/Windows/Linux) | ⏳ 待开始 | 3-5天 |
| P61-B | 跨平台键鼠模拟 (xdotool/CGEvent/SendInput) | ⏳ 待开始 | 5-7天 |
| P61-C | 应用/窗口管理 (打开/关闭/焦点/移动) | ⏳ 待开始 | 3-5天 |
| P61-D | 剪贴板读/写 (文本/图片/文件) | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2-3 周

### F-62: Chrome 浏览器自动化控制

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB Chrome Use

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P62-A | Chrome MCP 扩展桥接 | ⏳ 待开始 | 3-5天 |
| P62-B | 页面导航与元素交互 | ⏳ 待开始 | 2-3天 |
| P62-C | 截图与 JS 执行 | ⏳ 待开始 | 2-3天 |
| P62-D | 操作 GIF 录制 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 1-2 周

### F-63: Channels 频道通知系统

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB Channels

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P63-A | 飞书通知集成 | ⏳ 待开始 | 3-5天 |
| P63-B | Slack 通知集成 | ⏳ 待开始 | 2-3天 |
| P63-C | Discord 通知集成 | ⏳ 待开始 | 2-3天 |
| P63-D | 微信通知集成 | ⏳ 待开始 | 3-5天 |
| P63-E | MCP 服务器推送外部消息 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2 周

### F-64: Voice Mode 语音输入

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB Voice Mode

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P64-A | ASR 语音识别 (豆包 doubaoime-asr / Whisper) | ⏳ 待开始 | 3-5天 |
| P64-B | Push-to-Talk 语音交互 | ⏳ 待开始 | 3-5天 |
| P64-C | 音频流 WebSocket 传输 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 1-2 周

### F-65: Langfuse Agent 可观测性

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB Langfuse

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P65-A | OpenTelemetry + Langfuse SDK 集成 | ⏳ 待开始 | 3-5天 |
| P65-B | Agent Loop 级追踪 | ⏳ 待开始 | 2-3天 |
| P65-C | 一键转化为训练数据集 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 1 周

### F-66: ACP 协议支持

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB ACP (Agent Client Protocol)

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P66-A | ACP SDK 基础协议实现 | ⏳ 待开始 | 3-5天 |
| P66-B | Zed IDE 集成接入 | ⏳ 待开始 | 2-3天 |
| P66-C | Cursor IDE 集成接入 | ⏳ 待开始 | 2-3天 |
| P66-D | 会话恢复与 Skills 桥接 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 1-2 周

### F-67: Buddy 伴侣 / Proactive 自主模式

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB Buddy + Proactive

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P67-A | 后台 AI 伴侣异步观察会话 | ⏳ 待开始 | 3-5天 |
| P67-B | 主动提供调试建议 | ⏳ 待开始 | 2-3天 |
| P67-C | 文件变更自动检测与优化建议 | ⏳ 待开始 | 3-5天 |
| P67-D | Proactive 自主模式 (独立上下文) | ⏳ 待开始 | 3-5天 |

**估算总工时**: 2 周

### F-81: Native 原生模块系统（Python 可实现部分）

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB audio-capture-napi / color-diff-napi / image-processor-napi / url-handler-napi

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P81-A | Native 模块注册表与懒加载基础设施 | ⏳ 待开始 | 2-3天 |
| P81-B | 麦克风音频捕获（pyaudio, F-64 前置） | ⏳ 待开始 | 3-5天 |
| P81-C | 截图差异对比 + 图像处理（Pillow+NumPy, F-61 前置） | ⏳ 待开始 | 2-3天 |
| P81-D | OS URL Scheme 注册（webbrowser + xdg-utils） | ⏳ 待开始 | 2-3天 |
| P81-E | 键盘修饰键检测（pynput, 辅助 F-61） | ⏳ 待开始 | 2-3天 |

**依赖**: `pyaudio`, `Pillow`, `numpy`, `pynput`（均为可选依赖，缺失时降级）
**估算总工时**: 1 周

### F-82: Remote Control Server 远程控制服务

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB remote-control-server

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P82-A | FastAPI 核心基础设施 + 配置 + 生命周期 | ⏳ 待开始 | 3-5天 |
| P82-B | 认证系统（API Key / JWT / CORS 中间件） | ⏳ 待开始 | 2-3天 |
| P82-C | 会话管理 API（CRUD / List 路由） | ⏳ 待开始 | 3-5天 |
| P82-D | Worker 注册/心跳/长轮询工作分发 | ⏳ 待开始 | 5-7天 |
| P82-E | SSE/WebSocket 事件流推送 | ⏳ 待开始 | 3-5天 |
| P82-F | 环境管理与多机器部署 | ⏳ 待开始 | 3-5天 |
| P82-G | ACP 协议中继桥接 | ⏳ 待开始 | 3-5天 |
| P82-H | Web 管理面板（Jinja2 或 React） | ⏳ 待开始 | 5-7天 |

**依赖**: `fastapi` + `uvicorn`, `PyJWT`/`python-jose`, `sqlalchemy`/`aiosqlite`, `websockets`, `httpx`
**估算总工时**: 3-4 周

### F-83: Ultraplan 高级规划模式

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB FEATURE_ULTRAPLAN — `/ultraplan` 多步高级规划命令

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P83-A | Ultraplan 核心 prompt 与规划输出模板 | ⏳ 待开始 | 2-3天 |
| P83-B | `/ultraplan` CLI 斜杠命令注册与用户交互 | ⏳ 待开始 | 2-3天 |
| P83-C | 多步计划的分层执行与进度追踪 | ⏳ 待开始 | 3-5天 |
| P83-D | 执行中途动态调整计划 | ⏳ 待开始 | 2-3天 |
| P83-E | 计划完成后自动验证各步骤验收标准 | ⏳ 待开始 | 3-5天 |
| P83-F | 计划持久化到磁盘与 resume | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2-3 周

### F-84: Context Collapse 上下文折叠

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB FEATURE_CONTEXT_COLLAPSE — 上下文智能压缩引擎

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P84-A | Token 阈值检测与溢出预警（基于 tiktoken） | ⏳ 待开始 | 2-3天 |
| P84-B | LLM 驱动的旧消息摘要生成 | ⏳ 待开始 | 3-5天 |
| P84-C | 折叠后历史占位符注入 | ⏳ 待开始 | 2-3天 |
| P84-D | 折叠元数据持久化与会话恢复 | ⏳ 待开始 | 2-3天 |
| P84-E | 413 紧急折叠恢复 | ⏳ 待开始 | 2-3天 |
| P84-F | QueryEngine 全链路 5 层协作 | ⏳ 待开始 | 3-5天 |

**估算总工时**: 2-3 周

### F-85: Templates 模板系统

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB FEATURE_TEMPLATES — Agent 配置模板系统

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P85-A | 模板定义格式（YAML schema + agent 引用模板） | ⏳ 待开始 | 2-3天 |
| P85-B | 模板注册表（用户级 + 项目级） | ⏳ 待开始 | 2-3天 |
| P85-C | Agent 创建时模板解析与字段合并 | ⏳ 待开始 | 3-5天 |
| P85-D | CLI 管理命令（`/template list/create`） | ⏳ 待开始 | 2-3天 |
| P85-E | 内置默认模板 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 1-2 周

### F-86: Kairos / Brief 调度模式

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB FEATURE_KAIROS — Tick 驱动调度引擎 + 简报模式

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P86-A | Tick 调度核心（时基触发 + 周期性唤醒） | ⏳ 待开始 | 3-5天 |
| P86-B | SleepTool 工具 | ⏳ 待开始 | 2-3天 |
| P86-C | Brief 简报模式 | ⏳ 待开始 | 2-3天 |
| P86-D | Tick 消息注入对话流 | ⏳ 待开始 | 1-2天 |
| P86-E | 每日日志自动生成 | ⏳ 待开始 | 2-3天 |
| P86-F | CLI 控制命令 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2 周

### F-87: Workflow Scripts 工作流脚本

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB FEATURE_WORKFLOW_SCRIPTS — YAML/JSON 多步工作流

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P87-A | 工作流 YAML schema 定义与解析器 | ⏳ 待开始 | 2-3天 |
| P87-B | 工作流文件发现 | ⏳ 待开始 | 1-2天 |
| P87-C | 多步执行引擎 | ⏳ 待开始 | 3-5天 |
| P87-D | 内置捆绑工作流 | ⏳ 待开始 | 2-3天 |
| P87-E | CLI 命令与自动补全 | ⏳ 待开始 | 2-3天 |
| P87-F | 执行进度实时显示与错误恢复 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2 周

### F-88: Explore / Plan 内置 Agent

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB BUILTIN_EXPLORE_PLAN_AGENTS — 内置探索与规划 Agent

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P88-A | Explore Agent 定义 | ⏳ 待开始 | 1-2天 |
| P88-B | Plan Agent 定义 | ⏳ 待开始 | 1-2天 |
| P88-C | 自动路由逻辑 | ⏳ 待开始 | 2-3天 |
| P88-D | 探索报告与计划自动保存 | ⏳ 待开始 | 1-2天 |

**估算总工时**: 1 周

### CCB 对标实施总览

| 编号 | 特性 | 优先级 | 对标级别 | 状态 | 工时估算 |
|:----:|------|:------:|:--------:|:----:|:--------:|
| F-60 | Pipe IPC + LAN 群控 | P0 | 🔴 严重缺口 | ⏳ 待开始 | 3-4周 |
| F-61 | Computer Use 屏幕操控 | P0 | 🔴 严重缺口 | ⏳ 待开始 | 2-3周 |
| F-62 | Chrome 浏览器控制 | P1 | 🟡 重要缺口 | ⏳ 待开始 | 1-2周 |
| F-63 | Channels 频道通知 | P1 | 🟡 重要缺口 | ⏳ 待开始 | 2周 |
| F-64 | Voice Mode 语音输入 | P2 | 🟢 增强体验 | ⏳ 待开始 | 1-2周 |
| F-65 | Langfuse 可观测性 | P1 | 🟡 重要缺口 | ⏳ 待开始 | 1周 |
| F-66 | ACP 协议支持 | P2 | 🟢 增强体验 | ⏳ 待开始 | 1-2周 |
| F-67 | Buddy / Proactive | P2 | 🟢 增强体验 | ⏳ 待开始 | 2周 |
| F-81 | Native 原生模块（Python） | P1 | 🟡 重要缺口 | ⏳ 待开始 | 1周 |
| F-82 | Remote Control Server | P1 | 🟡 重要缺口 | ⏳ 待开始 | 3-4周 |
| **F-83** | **Ultraplan 高级规划模式** | **P1** | 🟡 重要缺口 | ⏳ 待开始 | 2-3周 |
| **F-84** | **Context Collapse 上下文折叠** | **P1** | 🟡 重要缺口 | ⏳ 待开始 | 2-3周 |
| **F-85** | **Templates 模板系统** | **P1** | 🟡 重要缺口 | ⏳ 待开始 | 1-2周 |
| **F-86** | **Kairos / Brief 调度模式** | **P2** | 🟢 增强体验 | ⏳ 待开始 | 2周 |
| **F-87** | **Workflow Scripts 工作流脚本** | **P2** | 🟢 增强体验 | ⏳ 待开始 | 2周 |
| **F-88** | **Explore / Plan 内置 Agent** | **P2** | 🟢 增强体验 | ⏳ 待开始 | 1周 |

### 实施建议顺序

```
F-60 (Pipe IPC) ──→ F-61 (Computer Use) ──→ F-63 (Channels) ──→ F-83 (Ultraplan) ──→ F-84 (ContextCollapse) ──→ F-85 (Templates)
   ↑ 架构基础          ↑ 高频交互              ↑ 团队协作               ↑ 高级规划             ↑ 上下文管理              ↑ Agent 模板
   P0                  P0                      P1                       P1                    P1                        P1

F-62 (Chrome) ──→ F-65 (Langfuse) ──→ F-81 (Native) ──→ F-82 (RCS) ──→ F-86 (Kairos/Brief) ──→ F-87 (Workflow) ──→ F-88 (Explore/Plan) ──→ F-64+F-66+F-67
   ↑ 自动化             ↑ 可观测性              ↑ F-61/F-64 前置          ↑ 远程管理             ↑ 定时调度               ↑ 工作流脚本             ↑ 内置 Agent              ↑ 体验增强
   P1                  P1                      P1                       P1                     P2                       P2                      P2                       P2
```

> **建议**: F-60（Pipe IPC）和 F-61（Computer Use）为 P0 级特性，建议优先实施。F-83（Ultraplan）和 F-84（Context Collapse）为 P1 级架构特性，建议紧随之后。
> F-81（Native 模块）是 F-61 和 F-64 的前置依赖，建议与 F-61 并行开发。
> F-85（Templates）依赖 F-68 Feature Gate 作为基础设施。F-86~F-88 为 P2 增强体验，可与 F-64/F-66/F-67 合并为长期迭代批次。

---

## 九、Python 生态特性补缺进度

> 本节跟踪 Python 生态适配角度发现的 clawcodex 特性缺口实施进度。
> F-68~F-74 均为 Python 标准库或成熟第三方库可实现的特性。

### F-68: Feature Gate 运行时特性开关系统

**状态**: ⏳ 待开始 | **优先级**: P1

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P68-A | FeatureRegistry 核心注册表 | ⏳ 待开始 | 3-5天 |
| P68-B | @feature_gated 装饰器 | ⏳ 待开始 | 2-3天 |
| P68-C | JSON/YAML 配置文件持久化 | ⏳ 待开始 | 1-2天 |
| P68-D | CLI 运行时切换 | ⏳ 待开始 | 1-2天 |
| P68-E | 环境变量覆盖 | ⏳ 待开始 | 1天 |
| P68-F | 依赖性解析与冲突检测 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 1-2 周

**详细设计**: `docs/FEATURE_PLAN.md` → `§十 F-68 Feature Gate 运行时特性开关系统`

### F-69: Budget / Poor Mode 资源节俭模式

**状态**: ⏳ 待开始 | **优先级**: P1

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P69-A | BudgetMode 配置模型（4级行为矩阵） | ⏳ 待开始 | 2-3天 |
| P69-B | Agent 循环节俭钩子（skip memory/verification） | ⏳ 待开始 | 3-5天 |
| P69-C | Tool 级别节俭策略（降级搜索深度/禁用高消耗工具） | ⏳ 待开始 | 2-3天 |
| P69-D | `/budget` CLI 斜杠命令 | ⏳ 待开始 | 2-3天 |
| P69-E | Token 用量实时统计与自动降级告警 | ⏳ 待开始 | 3-5天 |

**估算总工时**: 1-2 周

**详细设计**: `docs/FEATURE_PLAN.md` → `§十 F-69 Budget / Poor Mode 资源节俭模式`

### F-70: Plugin 插件系统基础框架

**状态**: ⏳ 待开始 | **优先级**: P1

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P70-A | BasePlugin 协议接口定义 | ⏳ 待开始 | 3-5天 |
| P70-B | Plugin 发现（entry_points + 目录扫描） | ⏳ 待开始 | 2-3天 |
| P70-C | Plugin 生命周期管理（install/uninstall/enable/disable） | ⏳ 待开始 | 5-7天 |
| P70-D | 子进程沙箱隔离 | ⏳ 待开始 | 5-7天 |
| P70-E | Plugin 清单格式（plugin.yaml / pyproject.toml 扩展） | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2-3 周

**详细设计**: `docs/FEATURE_PLAN.md` → `§十 F-70 Plugin 插件系统基础框架`

### F-71: 内置工具补齐（缺失工具批量实现）

**状态**: ⏳ 待开始 | **优先级**: P1

| 编号 | 子特性 | Python 依赖 | 状态 | 预计工作量 |
|:----:|--------|:-----------:|:----:|:----------:|
| P71-A | AgentTool 子 Agent 生成 | 无 | ⏳ 待开始 | 5-7天 |
| P71-B | WebBrowserTool 浏览器控制 | `playwright` | ⏳ 待开始 | 5-7天 |
| P71-C | CtxInspectTool 上下文检查 | 无 | ⏳ 待开始 | 2-3天 |
| P71-D | DiscoverSkillsTool 技能发现 | 无 | ⏳ 待开始 | 2-3天 |
| P71-E | VerifyPlanExecutionTool 计划验证 | 无 | ⏳ 待开始 | 3-5天 |
| P71-F | WorkflowTool 工作流执行 | 无 | ⏳ 待开始 | 3-5天 |
| P71-G | PushNotificationTool 桌面通知 | `plyer`/`notify-py` | ⏳ 待开始 | 2-3天 |
| P71-H | MonitorTool 健康监控 | 无 | ⏳ 待开始 | 2-3天 |
| P71-I | SendUserFileTool 文件传输 | 无 | ⏳ 待开始 | 2-3天 |
| P71-J | SubscribePRTool PR 订阅 | 无 | ⏳ 待开始 | 2-3天 |
| P71-K | TerminalCaptureTool 终端捕获 | `ptyprocess` | ⏳ 待开始 | 3-5天 |
| P71-L | ReviewArtifactTool Review 产物 | 无 | ⏳ 待开始 | 2-3天 |
| P71-M | ListPeersTool 对等节点列表 | 无 | ⏳ 待开始 | 1-2天 |
| P71-N | ExecuteTool 代理工具执行 | 无 | ⏳ 待开始 | 3-5天 |

**估算总工时**: 3-4 周（可分批并行推进，优先 P71-A/C/D/F/N）

**详细设计**: `docs/FEATURE_PLAN.md` → `§十 F-71 内置工具补齐`

### F-72: Multi-API 原生适配器扩展

**状态**: ⏳ 待开始 | **优先级**: P1

| 编号 | 子特性 | Python 依赖 | 状态 | 预计工作量 |
|:----:|--------|:-----------:|:----:|:----------:|
| P72-A | OpenAI 原生适配器（stream/structured output/function call） | `openai` | ⏳ 待开始 | 3-5天 |
| P72-B | Gemini 原生适配器（Safety/grounding 全能力） | `google-genai` | ⏳ 待开始 | 3-5天 |
| P72-C | Grok/xAI 原生适配器 | `requests` | ⏳ 待开始 | 2-3天 |
| P72-D | 原生适配器自动选择（provider → adapter → LiteLLM 回退） | 无 | ⏳ 待开始 | 2-3天 |
| P72-E | 平台专有特性映射表与能力标记 | 无 | ⏳ 待开始 | 3-5天 |

**估算总工时**: 2 周

**详细设计**: `docs/FEATURE_PLAN.md` → `§十 F-72 Multi-API 原生适配器扩展`

### F-73: CI/CD 质量门禁与 PyPI 发布流水线

**状态**: ⏳ 待开始 | **优先级**: P0

| 编号 | 子特性 | 工具链 | 状态 | 预计工作量 |
|:----:|--------|:------:|:----:|:----------:|
| P73-A | ruff lint/format CI | `ruff` | ⏳ 待开始 | 1-2天 |
| P73-B | pytest 测试流水线 | `pytest` | ⏳ 待开始 | 1-2天 |
| P73-C | pre-commit 本地钩子 | `pre-commit` | ⏳ 待开始 | 1天 |
| P73-D | PyPI 自动发布（tag push → build → twine → Release） | `build` + `twine` | ⏳ 待开始 | 2-3天 |
| P73-E | 测试覆盖率门禁 | `pytest-cov` + Codecov | ⏳ 待开始 | 1-2天 |
| P73-F | pyproject.toml 元数据规范 | 无 | ⏳ 待开始 | 1天 |
| P73-G | mypy 类型检查（可选） | `mypy` | ⏳ 待开始 | 2-3天 |

**估算总工时**: 1 周

**详细设计**: `docs/FEATURE_PLAN.md` → `§十 F-73 CI/CD 质量门禁与 PyPI 发布流水线`

### F-74: Sandbox / SSH Remote 沙箱远程执行

**状态**: ⏳ 待开始 | **优先级**: P2

| 编号 | 子特性 | Python 依赖 | 状态 | 预计工作量 |
|:----:|--------|:-----------:|:----:|:----------:|
| P74-A | SandboxExecutor 抽象接口 | 无 | ⏳ 待开始 | 3-5天 |
| P74-B | Docker 沙箱执行 | `docker-py` | ⏳ 待开始 | 3-5天 |
| P74-C | SSH 远程执行 | `asyncssh` | ⏳ 待开始 | 3-5天 |
| P74-D | `/sandbox` CLI 切换命令 | 无 | ⏳ 待开始 | 2-3天 |
| P74-E | 沙箱配置文件 | 无 | ⏳ 待开始 | 1-2天 |

**估算总工时**: 2 周

**详细设计**: `docs/FEATURE_PLAN.md` → `§十 F-74 Sandbox / SSH Remote 沙箱远程执行`

### Python 生态特性实施总览

| 编号 | 特性 | 优先级 | 状态 | 工时估算 |
|:----:|------|:------:|:----:|:--------:|
| F-68 | Feature Gate 运行时特性开关 | P1 | ⏳ 待开始 | 1-2周 |
| F-69 | Budget / Poor Mode 节俭模式 | P1 | ⏳ 待开始 | 1-2周 |
| F-70 | Plugin 插件系统基础框架 | P1 | ⏳ 待开始 | 2-3周 |
| F-71 | 内置工具补齐（14个工具） | P1 | ⏳ 待开始 | 3-4周 |
| F-72 | Multi-API 原生适配器 | P1 | ⏳ 待开始 | 2周 |
| F-73 | CI/CD 质量门禁与 PyPI 发布 | P0 | ⏳ 待开始 | 1周 |
| F-74 | Sandbox/SSH Remote 沙箱远程执行 | P2 | ⏳ 待开始 | 2周 |

### 实施建议顺序

```
F-73 (CI/CD) ──→ F-68 (Feature Gate) ──→ F-69 (Budget Mode) ──→ F-71 (Tool补齐)
   ↑ 基础质量          ↑ 架构基础              ↑ 用户感知              ↑ 功能完整
   P0                  P1                      P1                      P1

F-70 (Plugin 系统) ←── F-72 (API 适配器) ←── 可并行开发
   P1, 2-3周             P1, 2周

F-74 (Sandbox) ──→ 长期迭代（P2）
```

---

## 十、死代码排查记录

> 扫描时间: 2026-06-XX | 工具: vulture 2.16 | 对照基线: `src/upstream/58ea488/`

### 6.1 排查方法

1. 运行 `vulture src/ src/upstream/58ea488/` 对当前代码和上游基线分别扫描
2. 逐项对照：若死代码在上游同类文件中同样存在，则判定为 **继承性死代码（UPSTREAM）**，保留不动
3. 若死代码仅存在于本 fork 新实现文件中（`extensions/` 或 `src/services/bridge/` 等上游不存在的文件），则判定为 **新引入死代码（NEW）**，应清理

### 6.2 应清理项（NEW — 本 fork 新引入）

| # | 文件 | 行 | 类型 | 严重程度 | 说明 |
|---|------|----|------|---------|------|
| 1 | `src/services/bridge/transport.py` | 69-70 | 不可达代码 | 🔴 P0 | `receive()` 协程第69行有裸 `return`，之后第70行 `yield` 永远不可达。该文件整体在上游不存在，属新实现 stub，有缺陷 |
| 2 | `extensions/orchestrator/agent_runner.py` | 30 | 未使用的 import | 🟠 P1 | `is_quota_exhausted` 从 `src.services.api.errors` 导入，但仅在第237行文档字符串中被提及，从未实际调用 |
| 3 | `extensions/orchestrator/cli/dashboard.py` | 18 | 未使用的 import | 🟠 P1 | `deque` 从 `collections` 导入，全文无任何使用 |
| 4 | `extensions/orchestrator/cli/issue.py` | 1575 | 未使用的 import | 🟠 P1 | `from pathlib import Path as _Path` 局部导入，`_Path` 从未被实例化。模块级已在第34行有 `from pathlib import Path` |

### 6.3 继承性死代码（UPSTREAM — 保留不动）

上游 `src/upstream/58ea488/` 中存在相同死代码，按原则保留不做清理。涉及约 50 处，主要为以下模式：

| 模式 | 示例文件 | 数量 |
|------|---------|:----:|
| TYPE_CHECKING 块中导入但实际未用于类型注解 | `subagent_context.py`, `task_stop.py` | ~8 处 |
| 函数签名中定义但从未使用的参数（`exc_type`, `tb` 等） | `transcript.py:221`, `live_status.py:187`, `frame_metrics.py:163` | ~6 处 |
| 模块顶部导入但未被任何调用引用的 import | `messages.py`, `deep_link.py`, `advisor.py` | ~25 处 |
| 函数/变量定义后无调用方 | `prompt.py:55` `allow_fork`, `session_resume.py:181` `new_cwd` | ~5 处 |
| 被注释中的引用而非实际代码引用的 import | `repl_bridge_transport.py:31` | ~2 处 |

> 典型继承项：`src/utils/messages.py` 导入的 `ToolUseBlock`、`RedactedThinkingBlock`、`ImageBlock`、`MessageContent` 在上游同样未使用；`src/bridge/bridge_permission_callbacks.py` 和 `src/bridge/types.py` 的未用参数在上游完全一致。

### 6.4 误报项（已确认有使用）

| 文件 | vulture 报告项 | 实际使用说明 |
|------|--------------|-------------|
| `src/utils/advisor.py:37` `BaseProvider` | 未用 import | TYPE_CHECKING 导入 + 函数签名字符串注解中使用 |
| `src/services/tool_execution/tool_execution.py:53` `get_all_base_tools` | 未用 import | 懒导入，在函数体内被调用 |
| `src/services/api/errors.py:21` `RateLimitError` | 未导出 | 已在 `__init__.py` 中 re-export |

### 6.5 清理建议

| 优先级 | 文件 | 建议操作 |
|--------|------|---------|
| P0 | `src/services/bridge/transport.py:69-70` | 删除第69行的裸 `return`，使 `yield` 可达；或根据实际 WebSocket 传输需求重构 stub |
| P1 | `extensions/orchestrator/agent_runner.py:30` | 删除 `is_quota_exhausted` import |
| P1 | `extensions/orchestrator/cli/dashboard.py:18` | 删除 `from collections import deque` |
| P1 | `extensions/orchestrator/cli/issue.py:1575` | 删除 `from pathlib import Path as _Path`（模块级已有 `Path`） |*
