# ClawCodex 特性规划与设计文档

> 文档路径: `docs/FEATURE_PLAN.md`
> 基于: `clawcodex-opensource-replacement-analysis-v2.md`, `clawcodex_vs_ccb_analysis-v3.md`, `INTEGRATION.md`, `TEAM_MEMBERSHIP.md`
> 版本: v2.16
> 更新日期: 2026-06-04
> 上游同步: 58ea488 (dev-decoupling-refactor)
>
> **v2.16 变更**：完成 CCB（claude-code-best）全面对标分析，识别 clawcodex 的 8 个重大特性缺口并纳入规划（见 §6 CCB 对标特性补缺规划）。新设 F-60（Pipe IPC 多实例群控 + LAN 发现）、F-61（Computer Use 屏幕操控）、F-62（Chrome 浏览器自动化控制）、F-63（Channels 频道通知）、F-64（Voice Mode 语音输入）、F-65（Langfuse Agent 可观测性）、F-66（ACP 协议支持）、F-67（Buddy 伴侣 / Proactive 自主模式）。同时识别出 clawcodex 对比 CCB 的 5 项领先优势（Orchestrator 自动流水线、Verification Gate、POS-to-Agent 编译器、LiteLLM Provider、Manager/Worker 增强通信）。缺口项目按 P0~P2 优先级排序纳入开发管线。
> >
> > **v2.15 变更**
>
> **v2.14 变更**：新增 §3.17 src/ 核心路径二开修改解耦方案（F-48，📋 设计完成）。通过对比 `src/` 与 `src/upstream/58ea488/`，识别出 10 个含真正功能修改的 src/ 文件（其余 600+ 为行尾/格式差异），分三优先级制定解耦方案：Phase 0（纯新增文件移入 ext）、Phase 1（注册表/Protocol 扩展消除字段注入）、Phase 2（子类覆盖恢复上游构造器签名）、Phase 3（入口点恢复上游逻辑）。复用已有 3 种解耦模式：Facade 模式（`src/cli.py` 已走通）、子类覆盖模式（`ClawCodexExtTUI` 已走通）、前端注册表模式（`@register_frontend` 已走通）。目标：src/ 有功能修改的文件数从 10+ 降为 0。
>
> **v2.13 变更**：F-43 CLI 模型供应商与模型切换落地完成（✅ 已完成）。新增 `clawcodex provider` / `clawcodex model` 子命令族（list/show/current/use/unset）+ REPL/TUI 内 `/provider` / `/model` 斜杠命令；fast-path 注册表 + `ModelRegistry` / `ModelStore` / `Resolver` + `RuntimeContext.swap_provider` 热切换；所有新代码落在 `clawcodex_ext/cli/`，`src/*` 仅追加 `CommandContext.runtime_context` seam 与 `TUIOptions.runtime_context` 透传。20/20 F-43 单元测试通过，orchestrator 回归 271/271 通过。`--scope project` 落入 G-1 后续规划。
>
> **v2.12 变更**：新增 §3.15 CLI 模型供应商与模型切换设计（F-43，📋 设计完成）。规划 `clawcodex provider` / `clawcodex model` 子命令族（list/show/current/use/unset）+ REPL/TUI 内 `/provider` / `/model` 斜杠命令，覆盖查看、列出、切换当前生效的 LLM 供应商与模型。所有新代码落在 `clawcodex_ext/cli/` 下，遵守 "src/* 不动" 边界；持久化借道 `src.config`，不重写 I/O；错误文案统一英文；`--scope project` 落入后续规划。
>
> **v2.11 变更**：F-42 Sequential Workspace 策略实现完成（✅ 完成）。`workspace.strategy: isolated | shared | sequential` 落地：isolated 保持原有 per-issue 行为；shared/sequential 复用 `workspace.root` 作为共享工作树；sequential 强制单并发、使用 `.clawcodex_workspace.lock` 顺序锁、在 integration branch 上累积 commit 链；commit 元数据（base/start SHA、sequence_index）写入 registry；GitSync sequential 模式本地 commit 不 push、不 PR；shared/sequential root 在 cleanup 时保留。19 个专项测试 + 245 个 orchestrator 回归全部通过。
>
> **v2.10 变更**：新增 §3.1.9 Shared / Sequential Workspace 策略设计（F-42，📋 设计完成）。为 Orchestrator 增加 `workspace.strategy: isolated | shared | sequential` 规划：保留现有 per-issue isolated 行为，同时支持多个本地 issue 在同一 shared/sequential working tree 上按顺序累积 commit；设计覆盖配置字段、WorkspaceManager 路径选择、顺序锁、dirty tree guard、issue registry commit 元数据、GitSync/cleanup 行为与端到端验收。
>
> **v2.9 变更**：补充 F-22 Cron 系统相对 `claude-code-best` 的最新缺口复核结论。`clawcodex_ext/cron_system/` 已覆盖 parser/storage/scheduler/jitter/lock/permanent/inFlight/基础 runs/status 等底层能力，G1~G8 不再作为主要缺口；剩余 P0 缺口集中在真实 REPL/TUI/headless 运行路径接线、scheduled fire 执行队列、run lifecycle finalize、`/cron-list`/`/cron-delete`/trigger detail/manual fire/autonomy status 用户入口、busy gate/filter/teammate ownership 与 durable 文件变更 reload 行为。
>
> **v2.8 变更**：新增 §3.1.8 Coordinator 轻量工具集（F-41，✅ 已完成）。给 Coordinator 配置独立的轻量工具集（Read、WebSearch、WebFetch），加上原有的 Agent、SendMessage、TaskStop，共 6 个工具。 Coordinator 可直接处理简单查询（搜网页、读文件），无需为每个请求创建 Worker。所有写操作工具（Write、Edit、Bash、Grep、Glob）仍隔离，强制委派复杂任务给 Worker。涉及 `src/coordinator/mode.py` 的 `_COORDINATOR_ALLOWED_TOOLS` 扩展 + `src/coordinator/prompt.py` 的 "Your Tools" 提示词更新 + `src/repl/core.py` 注释同步。231/231 orchestrator 测试通过。
>
> **v2.7 变更**：F-39 Orchestrator Issue 重跑入口落地（Sub-A~F 全部 ✅）。`tracker.py` 增 `Intent` str-Enum + `intent_from_label_set` 优先级助手 + `Command` enum + `parse_agent_command` 正则 + `CommandIntent` 数据类（携带 `author_login`/`comment_id` 用于 Sub-F 角色校验）+ `fetch_issue_command_intent` 默认实现；`issue_registry.py:IssueRecord` 增 `intent/retry_count/last_command/intent_source/command_cursor` 字段 + `mark_intent/clear_intent/reset_for_retry/increment_retry_count/unblock` 方法；`orchestrator.py` 在 `_poll_and_dispatch` 增加 Sub-F 角色校验（`allow_anyone_to_retry`/作者匹配/fail-closed）+ 限频（`max_retries_per_issue=3`）+ 拒绝评论与高优 audit 日志；`cli/issue.py` 增 `retry` 子命令（`--mode {reset,followup,unblock}` + `--force` + `--max-retries` + `--operator` + `--reason`）写 `~/.clawcodex/orchestrator/audit.jsonl`。新增 153 个 F-39 专项单测，orchestrator 回归 231/231 通过。端到端 10-11 阶段（实际 GitCode/GitHub issue 联动）待真实环境验证。

> **v2.6 变更**：新增 §3.1.7 ProgressReporter Sink 协议重构设计（F-40，📋 设计完成）。解决 F-38 Sub-D 落地时遗留的三个问题：(1) `Orchestrator` 上 `ProgressReporter` 单例的 `_current_task_id` / `_phase_count` 共享可变状态在并发 issue 下竞争；(2) `AgentRunner` 只转发 `PhaseComplete`，`_on_session_complete` 形同虚设，session 结束无进度落点；(3) `progress = phase_count * 25` 是假数据。设计引入 `ProgressSink` Protocol + `CompositeProgressSink` 扇出 + `ToolContextProgressSink` 默认实现 + `ProgressReporter` 降级为 shim；新增 `WorkflowConfig.phases` 用于真实进度计算。

> **v2.5 变更**：已完成归档的特性（§3.1 Orchestrator 核心组件、§3.1.1~§3.1.2、§3.2 Agent 阶段性进度汇报、§3.4 MCP、§3.11 Issue 语义澄清、§3.12 Orchestrator CLI、§3.14 Agent 间自主观察、§十 Skills System Extension）以单段概览 + 归档链接形式保留，详细设计归档至 [ARCHIVED_FEATURES.md](./ARCHIVED_FEATURES.md)。本文件仅保留仍处规划/设计阶段的详细设计稿。

---

*版本 v2.6 更新：新增 §3.1.7 ProgressReporter Sink 协议重构设计（F-40）。把 `Orchestrator` 上 `ProgressReporter` 单例拆为每 session 独立的 `ProgressSink` 实例；新增 `CompositeProgressSink` 扇出支持 F-37/F-39 零侵入接入；补全 `SessionComplete` / `TurnComplete` 转发；引入 `WorkflowConfig.phases` 做真实进度计算，淘汰 `phase_count * 25` 假数据。*

*版本 v2.4 更新：新增 3.1.6 Issue 重跑入口设计（F-39）。三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发，Sub-B 重置重跑，Sub-C follow-up 叠 commit，Sub-D comment 命令解析，Sub-E CLI 兜底，Sub-F 限频+角色校验。*

*版本 v2.3 更新：新增 3.1.5 Orchestrator 验证与报告闭环设计（F-38）。Sub-A 在 `HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点，git_sync 在 commit/push 前后自动跑 verification gate（默认 `pytest -x`，用户可配 `test_command`）；Sub-B 新增 `report_writer` 生成 Markdown/JSON 报告，`IssueRecord` 增 `report_path` 字段，`git_sync._build_pr_body` 改模板插值；Sub-C 抽象 `TrackerAdapter.update_pull_request`，GitCode 客户端实现 `PATCH /repos/{owner}/{repo}/pulls/{id}`，把报告回写到 PR body 并合并为单条汇总评论；Sub-D 修复 `progress_reporter` 死代码，PhaseComplete 接入 ndjson event log。*

*版本 v1.8 更新：新增 F-37 Orchestrator PR 检视意见自动修复产品化规划。目标是在 issue 自动实现并提交 PR 后，持续读取 PR 网页检视意见、inline comments 与 CI 失败日志，驱动 agent 在同一 PR 分支上自动修改、验证、提交和推送。*

*版本 v1.7 更新：F-34 Phase 1-3 全部完成。CLI parser/dispatch 迁入 `clawcodex_ext/cli`；RuntimeContext 工厂 + Frontend 协议/注册表完成；`ClawCodexExtTUI` 8 个扩展钩子就绪。*

---

## 项目级二开边界约束

> **约束层级**: 项目级（所有 downstream/custom 开发必须遵守）
> **约束目标**: 防止二开代码污染 `src/*` 上游形状兼容区，确保未来上游同步不产生大量本地补丁累积

### 核心约束

1. **默认路径**: 所有 downstream/custom 开发默认进入根级 `clawcodex_ext/*`；**不得**在 `src/*` 中直接添加项目专属逻辑。

2. **`src/*` 定位**: `src/*` 被视为上游形状/core 兼容区，除非文件被明确标注为项目自主拥有，否则只接受：
   - 从 `clawcodex_ext/*` 向上的 thin forwarding seams
   - 最小适配层（adapter/wrapper）
   - 上游同步带来的必要更新
   - 窄范围 bug fix

3. **明确接受 minimal patch 的文件**（仅限这些文件可接受 thin forwarding/adapter 改动）：
   - `src/cli.py`
   - `src/entrypoints/tui.py`
   - `src/repl/*`
   - `src/tui/*`
   - `src/runtime/*`（未来）

4. **`src/upstream/<rev>/*`**: 仅作为上游快照同步区，**不得**在此路径下添加任何 downstream 代码。

5. **二开功能目标路径**（示例）:
   - `clawcodex_ext/cli` — CLI parser/dispatch 下游实现
   - `clawcodex_ext/tui` — TUI 下游定制
   - `clawcodex_ext/frontend` — Frontend 协议/注册表
   - `clawcodex_ext/runtime` — Runtime context factory
   - `clawcodex_ext/skills` — 下游技能/hook 扩展

6. **新功能实现流程**: 新 downstream 特性、frontend 行为、runtime 接线、命令、UI 定制、provider/tool 编排变更应首先在 `clawcodex_ext/*` 实现；对 `src/*` 的改动仅限于 thin forwarding seams。

### 约束起源

此约束将 F-34/F-35 的 CLI/TUI 前端解耦边界推广至全项目级别。最初来源于 `CONTRIBUTING.md` 中的 CLI/TUI 二开边界规则，已不能满足多层次解耦架构（upstream-sync layer + capabilities layer + orchestrator/api layer + downstream extension layer）的需求。本约束确保 downstream 扩展开发默认在 `clawcodex_ext/*` 进行，而不是直接修改上游形状文件，从而在未来的上游快照同步中避免大量本地补丁累积。

F-34/F-35 中"CLI/TUI 新功能"的描述扩展为全项目范围：所有 frontend 行为、runtime 接线、命令、UI 定制、provider/tool 编排均受此约束约束。

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

## 二、已归档功能模块

> **已实现功能已归档至 [ARCHIVED_FEATURES.md](./ARCHIVED_FEATURES.md)**
>
> 以下列出的所有功能模块已在归档文档中详细记录（v2.5 范围）：
> - §一 核心 Agent 系统
> - §二 三层解耦架构（Layer Isolation）
> - §三 Provider 层（含 §3.3 LiteLLM Provider 替换，R-7）
> - §四 工具系统
> - §五 开源替代组件（R-1~R-6）
> - §六 后台运行 + 恢复同步
> - §七 Bridge Phase 8-11 多 Session Daemon 桥接器
> - §八 Agent Loop Consolidation (Stage 4)
> - §九 Advisor Token 计数与状态显示
> - §十 REPL 与 TUI 增强
> - §十一 TUI 响应性修复
> - §十二~十五 TaskInspect/TaskDirectives、ProgressReportTool、TUI 权限模式选择器、会话恢复浏览器
> - §十六 Orchestrator 自主模式（含 §16.4 生产强化 F-1.1~1.4、§16.5 三通道澄清 F-1.5~1.11、§16.6 CLI 运维界面 F-1.13）
> - §十七 MCP 协议扩展
> - §十八 Agent 间自主观察与消息交互
> - §十九 POS to Agent 转化模式
> - §二十 Skills System Extension（F-23）

---

---

## 三、规划功能模块

### 3.1 Orchestrator 自主模式（Symphony 集成）

**状态**: ✅ 完成（Symphony 集成）
**目标**: 支持 `clawcodex --workflow WORKFLOW.md` 自主运行模式

> 核心组件、生产强化（F-1.1~F-1.4）、Issue 语义澄清三通道（F-1.5~F-1.11）、Orchestrator CLI 运维界面（F-1.13）等子特性全部已归档。
> 详细架构、组件清单、配置形态与命令清单见 [ARCHIVED_FEATURES.md §16](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成)。
>
> 仍处规划/设计阶段、保留详细设计稿的子节如下：
> - §3.1.4 PR 检视意见自动修复闭环设计（F-37，📋 规划中）
> - §3.1.7 ProgressReporter Sink 协议重构设计（F-40，📋 设计完成）
> - §3.1.13 AgentRunner / QueryRunner 运行期可观测性与 stuck-run debug（F-54，📋 设计完成）
> - 已完成的 LocalTracker（F-36）、验证与报告闭环（F-38）、Issue 重跑入口（F-39）、Coordinator 轻量工具集（F-41）、Shared / Sequential Workspace（F-42）、Tool-call 审计旁路（F-45）、人工检视闸门（F-44）与 AgentRunner 空转检测（F-51）详见 [ARCHIVED_FEATURES.md §二十一](./ARCHIVED_FEATURES.md#二十一2026-06-02-已实现功能归档)。

#### 3.1.3 LocalTracker 本地 Issue 文档源设计（F-36）

**状态**: ✅ 完成

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.1 F-36 LocalTracker 本地 Issue 文档源](./ARCHIVED_FEATURES.md#二十一1-f-36-localtracker-本地-issue-文档源)。

#### 3.1.4 PR 检视意见自动修复闭环设计（F-37）

**状态**: 📋 规划中
**优先级**: P0
**目标**: 将“基于 PR 网页检视意见自动修改并更新 PR”的能力产品化到 `extensions/orchestrator`，形成 issue → implementation PR → review feedback → follow-up fix → push update 的自动闭环。

##### 背景与范围

当前 Orchestrator 已具备从 issue 自动实现、提交、推送并创建/复用 PR 的能力；`GitSyncService` 会在 agent run 完成后提交改动并调用 tracker 创建 PR。缺口在 PR 创建后的 follow-up 阶段：网页上的 PR 检视意见、inline comments、review summary 与 CI/pipeline 失败日志尚未被 Orchestrator 主循环读取，也不会自动转化为同一 PR 分支上的二次修改任务。

该特性应覆盖：
- PR conversation 普通评论中的修改建议。
- PR inline review comments 中带文件路径、diff hunk、行号的修改建议。
- PR review summary 中的整体变更要求。
- CI/pipeline 失败日志中的 lint、test、typecheck 等可修复问题。
- 已处理反馈的幂等记录，避免重复修同一条评论或同一条失败检查。

首期不做自动合并 PR，也不自动处理安全敏感或需求不明确的评论；遇到互相冲突、要求不明确或超出原 issue 范围的反馈时，应进入 clarification / operator hint 流程。

##### 架构扩展

新增能力应保持 TrackerAdapter 抽象边界，由各仓库平台适配器负责平台 API 差异，Orchestrator 主循环只消费规范化后的反馈模型。

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| PullRequestFeedback 数据模型 | `extensions/orchestrator/tracker.py` | 📋 规划 | 规范化 PR 评论、inline comment、review summary、CI 失败项 |
| TrackerAdapter PR feedback 接口 | `extensions/orchestrator/tracker.py` | 📋 规划 | 新增 `fetch_pull_request_feedback`、`reply_to_pull_request_feedback` 等可选接口 |
| RepositoryIssueClient PR API | `extensions/orchestrator/repo_tracker/client.py` | 📋 规划 | 接入 GitHub/Gitee/GitCode PR review comments、PR issue comments、checks/pipelines API |
| RepositoryTrackerAdapter 映射 | `extensions/orchestrator/repo_tracker/adapter.py` | 📋 规划 | 将平台原始响应转成统一 feedback 模型 |
| Review feedback registry | `extensions/orchestrator/issue_registry.py` 或独立 store | 📋 规划 | 记录已处理 feedback id、check run id、commit sha，保证幂等 |
| Review follow-up poller | `extensions/orchestrator/orchestrator.py` | 📋 规划 | 对已有 PR 的 issue 轮询新反馈并调度 follow-up agent run |
| Review prompt builder | `extensions/orchestrator/prompt_builder.py` | 📋 规划 | 构造专用 prompt，明确只处理检视意见并保持同一 PR 分支 |
| Git sync follow-up 模式 | `extensions/orchestrator/git_sync.py` | 📋 规划 | 已有 PR 时只提交并 push 同一分支，不创建新 PR |

##### 规范化反馈模型

```python
@dataclass(frozen=True)
class PullRequestFeedback:
    id: str
    source: Literal["conversation", "inline_review", "review_summary", "ci"]
    body: str
    author_login: str | None = None
    file_path: str | None = None
    line: int | None = None
    diff_hunk: str | None = None
    severity: Literal["info", "warning", "error"] | None = None
    status: Literal["open", "resolved", "outdated"] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    commit_sha: str | None = None
    url: str | None = None
```

反馈模型必须携带稳定 id，用于幂等去重；inline review comments 应尽量保留 `file_path`、`line`、`diff_hunk`，让 agent 能精确定位；CI 失败项应保留 job/check 名称、失败摘要和日志片段，但避免把超大日志原样塞入 prompt。

##### Follow-up 运行流程

```text
1. Orchestrator 完成 issue 首次实现并创建 PR
2. registry 保存 issue_id → branch_name → pr_number/pr_url → last_feedback_cursor
3. 周期性扫描已有 open PR 的新反馈和失败检查
4. 过滤 bot 自己发布的状态评论、已处理 feedback、已过时 inline comments
5. 将剩余反馈合并为一个 review-fix prompt
6. 在同一 workspace/branch 上运行 agent
7. agent 只处理 PR feedback，不扩展新功能范围
8. 运行项目测试/检查
9. commit + push 到原 PR branch
10. registry 标记 feedback 已处理，并可选回复评论说明处理结果
```

##### Prompt 约束

Review follow-up prompt 应明确：
- 当前任务是修复 PR 检视意见，不重新实现整个 issue。
- 优先处理有文件路径和行号的 inline comments。
- 对 CI 失败，应先定位最小失败原因，再做最小修改。
- 对评论中互相冲突或需求不明确的内容，应请求 clarification，不要猜测。
- 修改完成后运行相关测试；无法运行时必须记录原因。
- 不创建新分支、不创建新 PR，只更新当前 PR 分支。

##### 配置建议

```yaml
review_feedback:
  enabled: true
  poll_interval_ms: 60000
  max_feedback_items_per_run: 20
  include_ci_failures: true
  reply_to_comments: true
  ignore_authors:
    - clawcodex-bot
  max_log_chars_per_check: 12000
  max_followup_attempts_per_pr: 5
```

该配置可以作为 `WorkflowConfig` 的新段落，默认关闭或默认跟随 orchestrator 自主模式开启；生产环境建议限制单次处理反馈数量和单 PR follow-up 次数，防止评论噪声导致无限循环。

##### 幂等与安全边界

- `IssueRegistry` 或独立 feedback store 需要记录 `feedback_id → processed_commit_sha/status`。
- 对已 resolved/outdated 的 inline comments 不应触发修改。
- 对 bot 自己发布的评论必须过滤，防止自触发循环。
- 对 destructive 操作、force push、自动合并 PR 等动作默认禁止。
- 评论中出现外部 URL、脚本或凭据相关要求时，只作为需求文本处理，不自动执行未授权命令。
- CI 日志截断后应保留失败命令、错误摘要和相关文件路径，避免 prompt 过载。

##### 实施切片

1. 扩展 tracker 协议与数据模型：新增 `PullRequestFeedback`、PR feedback fetch/reply 可选接口。
2. 为 GitHub/Gitee/GitCode repository tracker 增加 PR comments、review comments、review summary 与 CI/pipeline 读取能力。
3. 扩展 registry，记录 PR feedback cursor、已处理 feedback id、follow-up attempt 次数。
4. 在 Orchestrator poll loop 中增加 review follow-up 阶段，扫描已有 PR 并调度同分支 agent run。
5. 增加 review-fix prompt builder，约束 agent 只处理检视意见和 CI 失败。
6. 调整 git sync，使 follow-up run 只 commit/push 原分支并复用已有 PR。
7. 增加回复评论/汇总评论能力，标记哪些 feedback 已处理、哪些需要人工确认。
8. 增加单元测试和端到端测试：评论去重、inline comment 映射、CI 日志截断、bot 评论过滤、重复 follow-up 上限。

---

#### 3.1.5 Orchestrator 验证与报告闭环设计（F-38）

**状态**: ✅ 完成

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.2 F-38 Orchestrator 验证与报告闭环](./ARCHIVED_FEATURES.md#二十一2-f-38-orchestrator-验证与报告闭环)。

#### 3.1.6 Issue 重跑入口设计（F-39，label + comment 命令双通道）

**状态**: ✅ 完成（Sub-A~F）

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.3 F-39 Orchestrator Issue 重跑入口](./ARCHIVED_FEATURES.md#二十一3-f-39-orchestrator-issue-重跑入口)。

#### 3.1.7 ProgressReporter Sink 协议重构设计（F-40）

**状态**: 📋 设计完成
**优先级**: P1
**跟踪文档**: `docs/PROGRESS.md` → `F-40: ProgressReporter Sink 协议重构`
**触发场景**: 2026-06-01 F-38 Sub-D 落地把 `ProgressReporter` 接到主流程后，审阅发现三个未解决的设计债:

1. **单例竞争**：`ProgressReporter` 在 `Orchestrator.__init__` 单例化（`orchestrator.py:125-126`），`_current_task_id` / `_phase_count` 是实例可变状态；F-39 多 issue 并发跑会竞争——Issue A `set_task_id("A")` 之后 Issue B `set_task_id("B")` 会覆盖 A 的状态，PhaseComplete 写到错误的 task_id。
2. **SessionComplete 形同虚设**：`progress_reporter.py:_on_session_complete` 定义但 `AgentRunner.run` 只在两处显式构造 `PhaseComplete` 并转发（`agent_runner.py:302-304, 362-364`），`SessionComplete` 事件从未实际派发到 reporter，会话结束无进度落点。
3. **进度数据假**：`progress_reporter.py:87` 写 `progress = min(self._phase_count * 25, 100)`——纯按 phase 计数线性插值，对下游 UI 无参考价值；用户看到 75% 误以为「工作完成 3/4」实则只是「phase 计数器到了 3」。

### 设计目标

把 `extensions/orchestrator/progress_reporter.py` 从「绑死 `ToolContext` 的单例」重构为「以 `ProgressSink` 协议为最小契约的多消费者可插拔架构」:

1. 每 session 持有独立 sink 实例，状态天然隔离，消除并发竞争。
2. `AgentRunner` 转发全部三类事件 (`PhaseComplete` / `TurnComplete` / `SessionComplete`)，session 结束一定有进度落点。
3. 进度计算改用 `WorkflowConfig.phases` 比例 + LLM 显式覆盖，淘汰 `phase_count * 25` 假数据。
4. 引入 `CompositeProgressSink` 扇出，让 F-37 (PR 检视意见自动修复) / F-39 (Issue 重跑) 后续可零侵入注册专用 sink。
5. 保留 `ProgressReporter` 名字为向后兼容 shim，既有测试与调用方不破。

### 架构对比

```
旧架构（F-38 落地后，2026-06-01）:
  Orchestrator.__init__ ── 一次 ──> ProgressReporter (单例)
                                          ↓
                                  共享 _current_task_id / _phase_count
                                          ↓
                          AgentRunner.run(progress_reporter=...)
                                          ↓
                                  progress_reporter.on_event(phase_event, session)
                                          ↓
                          _progress_report_call + _task_update_call
                                          ↓
                                  ToolContext.tasks[id].metadata.progress_stages

新架构（F-40 提案）:
  Orchestrator._dispatch_issue(issue):
      ↓
      task_id = self._allocate_task_id(issue)
      inner = ToolContextProgressSink(task_id, self._progress_context,
                                       workflow_phases=self.workflow.phases)
      sink = CompositeProgressSink([
          inner,
          # 未来: PRReviewAutoFixSink(task_id, registry, git_sync),  # F-37
          # 未来: RetryLabelSink(task_id, tracker),                  # F-39
      ])
      ↓
  await self.agent_runner.run(session, self.workflow, progress_sink=sink, ...)
      ↓
      sink.on_phase_complete(event, session)   # sink 实例独占 task_id
      sink.on_turn_complete(event, session)     # 三个事件全部转发
      sink.on_session_complete(event, session)  # session 结束必落点
```

### 关键组件

#### 1. ProgressSink 协议（extensions/orchestrator/progress_sink.py）

```python
class ProgressSink(Protocol):
    """A consumer of agent progress events for ONE task/session.

    每个 sink 实例独占一个 task_id 与私有计数，状态由实例承载
    （非线程安全 = 没问题，因为实例不会被并发访问）。
    """
    task_id: str

    def on_phase_complete(self, event: PhaseComplete, session: AgentSession) -> None: ...
    def on_turn_complete(self, event: TurnComplete, session: AgentSession) -> None: ...
    def on_session_complete(self, event: SessionComplete, session: AgentSession) -> None: ...
```

#### 2. CompositeProgressSink 扇出

```python
class CompositeProgressSink:
    def __init__(self, sinks: Iterable[ProgressSink]) -> None:
        self._sinks: list[ProgressSink] = list(sinks)

    def add(self, sink: ProgressSink) -> None:
        self._sinks.append(sink)

    def on_phase_complete(self, event, session):
        for s in self._sinks:
            try:
                s.on_phase_complete(event, session)
            except Exception:
                logger.exception("sink %s.on_phase_complete failed", s)
    # on_turn_complete / on_session_complete 同理
```

#### 3. ToolContextProgressSink 默认实现

```python
class ToolContextProgressSink:
    """默认实现：把事件落进 ToolContext.tasks（与原 ProgressReporter 行为等价）。"""

    def __init__(
        self,
        task_id: str,
        context: ToolContext,
        workflow_phases: list[str] | None = None,
        fallback_to_phase_step: bool = False,
    ) -> None:
        self.task_id = task_id
        self._context = context
        self._phase_count = 0
        self._workflow_phases = workflow_phases or []
        self._fallback_to_phase_step = fallback_to_phase_step

    def _named_phase(self, idx: int) -> str:
        if 1 <= idx <= len(self._workflow_phases):
            return self._workflow_phases[idx - 1]
        return f"phase_{idx}"

    def _phase_progress(self, idx: int) -> int | None:
        if self._workflow_phases:
            named = self._named_phase(idx)
            real_idx = self._workflow_phases.index(named)
            return int((real_idx + 1) / len(self._workflow_phases) * 100)
        if self._fallback_to_phase_step:
            return min(idx * 25, 100)
        return None  # 未知，让 LLM 显式报

    def on_phase_complete(self, event, session):
        if not self.task_id:
            return
        self._phase_count += 1
        phase_name = self._named_phase(self._phase_count)
        progress = self._phase_progress(self._phase_count)
        from src.tool_system.tools.progress_report import _progress_report_call
        from src.tool_system.tools.tasks_v2 import _task_update_call
        _progress_report_call({
            "taskId": self.task_id,
            "stage": phase_name,
            "progress": progress,  # 可能 None
            "summary": f"Completed phase {self._phase_count}",
            "metadata": {
                "turn_count": event.turn_count,
                "phase": event.phase,
                "auto": True,
            },
        }, self._context)
        _task_update_call({
            "taskId": self.task_id,
            "metadata": {
                "phase": event.phase,
                "turn_count": event.turn_count,
                "phase_name": phase_name,
                "phase_complete": True,
            },
        }, self._context)

    def on_turn_complete(self, event, session):
        # 不落 ToolContext（避免噪音），仅 debug 日志
        logger.debug("turn %d complete for task %s", event.turn, self.task_id)

    def on_session_complete(self, event, session):
        if not self.task_id:
            return
        from src.tool_system.tools.progress_report import _progress_report_call
        _progress_report_call({
            "taskId": self.task_id,
            "stage": f"session_{event.reason}",
            "progress": 100 if event.reason == "success" else None,
            "summary": f"Session ended: {event.reason}",
            "metadata": {
                "session_status": session.status,
                "turn_count": session.turn_count,
                "phase_count": self._phase_count,
            },
        }, self._context)
```

#### 4. ProgressReporter 兼容 shim

```python
class ProgressReporter:
    """已弃用。新代码请直接使用 ToolContextProgressSink。"""

    def __init__(self, context: ToolContext) -> None:
        self._context = context
        self._current_task_id: str | None = None
        self._phase_count = 0
        self._sink: ToolContextProgressSink | None = None

    def set_task_id(self, task_id: str) -> None:
        self._current_task_id = task_id
        self._phase_count = 0
        self._sink = ToolContextProgressSink(task_id, self._context)

    def on_event(self, event, session):
        # 老 API: 根据类型分发
        if not self._sink:
            return
        if isinstance(event, PhaseComplete):
            self._sink.on_phase_complete(event, session)
        elif isinstance(event, TurnComplete):
            self._sink.on_turn_complete(event, session)
        elif isinstance(event, SessionComplete):
            self._sink.on_session_complete(event, session)
```

### 改造点清单

| 文件 | 改动 | Sub |
|------|------|-----|
| `extensions/orchestrator/progress_sink.py` | **新建**：`ProgressSink` 协议 + `CompositeProgressSink` + `ToolContextProgressSink` | A/B |
| `src/orchestrator/config/schema.py` | `WorkflowConfig` 新增 `phases: list[str] = field(default_factory=list)` 字段 | E |
| `extensions/orchestrator/progress_reporter.py` | 改写为兼容 shim；`on_event` 走 `isinstance` 分发；`set_task_id` 创建新 sink；标记 `@deprecated` | F |
| `extensions/orchestrator/agent_runner.py` | 参数 `progress_reporter` → `progress_sink`；`SessionComplete` 分支与 `max_turns` 路径补 `sink.on_session_complete`；若有 `TurnComplete` 分支也补 `sink.on_turn_complete`；`_write_event_log` 行为不变 | C |
| `extensions/orchestrator/orchestrator.py` | 删除 `self._progress_reporter = ProgressReporter(...)`；`_dispatch_issue` / `_run_issue` 中为每个 session 新建 `ToolContextProgressSink` + `CompositeProgressSink`；保留 `_progress_context` 共享 | D |
| `src/tool_system/tools/progress_report.py` | `ProgressReportTool` prompt 增「建议显式传 `progress`」指引；`_progress_report_call` 接受 `progress=None`（已支持） | E |
| `tests/test_orchestrator_agent_runner.py` | 新增并发回归 + 三事件覆盖测试；保留现有 stub（走 `on_event` 老 API 兼容） | G |

### 进度计算决策表

| 来源 | 触发时机 | `progress` 值 | 优先级 |
|------|----------|---------------|--------|
| LLM 显式调 `ProgressReport` 工具 | LLM 主动汇报 | LLM 传入的 `progress` | 最高 (覆盖一切) |
| `WorkflowConfig.phases` + 自动 `on_phase_complete` | PhaseComplete 事件 | `(current_idx+1) / total * 100` | 中 |
| 兜底（均无） | PhaseComplete 事件 | `None` (UI 显示「未知」) | 最低 |
| `SessionComplete` 终态 | 会话结束 | `100` (reason=success) / `None` (其他) | 终态 |

`workflow.observability.progress.fallback_to_phase_step: bool = True` 时，中间档用 `phase_count * 25` 兜底（软迁移期），后续翻 `False` 强推 None。

### 并发正确性证明

| 时间 | 事件 | 旧实现（单例） | 新实现（每 session 独立 sink） |
|------|------|----------------|--------------------------------|
| t0 | Issue A 启动 → `set_task_id("A")` | `_current_task_id="A"` | 创建 `SinkA(task_id="A")` |
| t1 | Issue B 启动 → `set_task_id("B")` | `_current_task_id="B"` (覆盖) | 创建 `SinkB(task_id="B")` |
| t2 | A 触发 `PhaseComplete` | 写到 task **B** ❌ | 通过 `SinkA` 写到 task A ✓ |
| t3 | B 触发 `PhaseComplete` | 写到 task **B** ✓ | 通过 `SinkB` 写到 task B ✓ |

`AgentRunner.run` 当前是 `async`，每个 session 跑在独立 task 上；新架构下每个 task 持自己的 sink，无共享可变状态。

### 验收标准

- 并发跑两个 issue 时，每个 session 的 `ToolContext.tasks[id].metadata.progress_stages` 列表只含本 session 的事件，无串扰。
- `SessionComplete` 触发后，`ToolContext.tasks[id].metadata.current_stage` 含 `session_{reason}`、`metadata.progress` 在 `reason=success` 时为 100、其他情况为 `None`。
- `WorkflowConfig.phases=["analysis", "design", "impl", "test", "review"]` 配置下，完成第 2 个 phase 时 `progress=40`；LLM 显式调 `ProgressReport` 传 `progress=37` 时覆盖自动值。
- `WorkflowConfig.phases` 缺失或为空时，自动 `on_phase_complete` 写 `progress=None`，`StatusDashboard` 显示「Phase N (进度未知)」，而不是误导的 25/50/75/100。
- `ProgressReporter` 类的 `on_event(event, session)` 旧 API 仍可用，内部按 `isinstance(event, PhaseComplete / TurnComplete / SessionComplete)` 分发，现有 stub 测试不修改即可通过。
- `CompositeProgressSink` 内任一 sink 抛异常被独立捕获并 `logger.exception`，不影响其他 sink 接收事件。
- F-37 / F-39 后续接入时，只需在 `Orchestrator._dispatch_issue` 注册额外 sink（`PRReviewAutoFixSink` / `RetryLabelSink`），无需修改 `AgentRunner` 或 `progress_reporter.py`。

### 风险与约束

- **API 改名 breaking**：`AgentRunner.run` 的 `progress_reporter` kwarg 改 `progress_sink` 是字面量破坏，需同步改 `Orchestrator` 调用方与所有 stub 测试。Mitigation: `ProgressReporter` shim 仍可作为 `progress_sink` 传入（duck type，只要实现三个 `on_*` 方法即可）。
- **进度从假数据变 `None` 的 UI 退化**：默认配置下旧用户从「25/50/75/100」退到「未知」。Mitigation: 加 `workflow.observability.progress.fallback_to_phase_step: bool = True` 配置开关（默认保留旧行为），后续再翻 `False`。
- **每个 session 多一个 sink 对象**：内存增长可忽略（Python 单实例，几 KB），无 perf 风险。
- **事件总线语义变化**：`CompositeProgressSink` 是同步扇出，任意 sink 阻塞会卡住 `AgentRunner` 主循环。Mitigation: 每个 sink 内部 try/except + 短超时；慢消费者应自己 queue + 后台线程。
- **Import 顺序**：`progress_reporter.py` (shim) → `progress_sink.py` (默认实现) → `agent_runner.py` (调用方) 依赖链需保持单向，避免循环 import。建议 `progress_reporter.py` 用 `from .progress_sink import ToolContextProgressSink` 软引用，`TYPE_CHECKING` 保护。

### 实施阶段

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | 新建 `extensions/orchestrator/progress_sink.py`，定义 `ProgressSink` Protocol + `CompositeProgressSink` + `ToolContextProgressSink` | A/B | 📋 待开始 |
| 2 | `src/orchestrator/config/schema.py:WorkflowConfig` 新增 `phases: list[str] = field(default_factory=list)` 字段，旧 workflow.md 无 `phases` 时退化为「无 phase 权重，自动上报 `progress=None`」 | E | 📋 待开始 |
| 3 | `extensions/orchestrator/progress_reporter.py` 改写为 shim，内部维护 `ToolContextProgressSink`，`on_event` 走 `isinstance` 分发；`set_task_id` 创建新 sink；`from .progress_sink import ...` 软引用，避免循环 import | F | 📋 待开始 |
| 4 | `extensions/orchestrator/agent_runner.py`：参数 `progress_reporter` → `progress_sink`；`SessionComplete` 分支与 `max_turns` 路径补 `sink.on_session_complete`；若有 `TurnComplete` 分支也补 `sink.on_turn_complete`；`_write_event_log` 行为不变 | C | 📋 待开始 |
| 5 | `extensions/orchestrator/orchestrator.py:125-126` 删除单例；`_dispatch_issue` / `_run_issue` 中为每个 session 新建 `ToolContextProgressSink` + `CompositeProgressSink`；保留 `_progress_context` 共享 | D | 📋 待开始 |
| 6 | `src/tool_system/tools/progress_report.py` 的 `ProgressReportTool` prompt 增「建议显式传 `progress`」指引；`_progress_report_call` 接受 `progress=None`（已支持，无需改实现） | E | 📋 待开始 |
| 7 | 单元测试：`ToolContextProgressSink` 三个回调直接调；`CompositeProgressSink` 扇出且单 sink 异常不阻塞；`ProgressReporter` shim 的 `on_event` 类型分发；`WorkflowConfig.phases` 解析默认空 | A/B/E/F | 📋 待开始 |
| 8 | 回归测试：`asyncio.gather` 并发跑两个 session，断言各自的 `ToolContext.tasks` 写入互不串扰；`SessionComplete` 落点测试 | G | 📋 待开始 |
| 9 | 更新 `tests/test_orchestrator_agent_runner.py` 的 stub（若依赖 `progress_reporter` kwarg，改为 `progress_sink`）；运行 `pytest tests/test_orchestrator_*.py -q` 与 `tests/manual_e2e_f38.py -v -s` 确认不破 | G | 📋 待开始 |

### 依赖与协同

- **依赖 F-1、F-38 Sub-D**：F-38 已把 `ProgressReporter` 接到主流程，本特性在此基础上重构；不破坏 F-38 验收标准（`progress_reporter.ProgressReporter` 在主流程被构造 → 改为 `ToolContextProgressSink` 在主流程被构造）。
- **先于 F-37 落地收益**：F-37 (PR 检视意见自动修复) 后续可注册 `PRReviewAutoFixSink` 监听 `on_session_complete` 触发 follow-up run，无需改 `AgentRunner`。
- **先于 F-39 落地收益**：F-39 (Issue 重跑) 后续可注册 `RetryLabelSink` 监听 `on_session_complete` 更新 issue label，无需改 `AgentRunner`。
- **不破坏 F-36 LocalTracker**：LocalTracker 派发的 session 也走相同的 sink 构造路径，`ToolContextProgressSink` 行为对其等价（数据落 `ToolContext.tasks`，不访问远程）。
- **与 F-22 Cron 系统解耦**：Cron 触发的 prompt 不走 orchestrator，sink 链路不被影响。

---

#### 3.1.8 Coordinator 轻量工具集（F-41）

**状态**: ✅ 已完成

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.4 F-41 Coordinator 轻量工具集](./ARCHIVED_FEATURES.md#二十一4-f-41-coordinator-轻量工具集)。

#### 3.1.9 Shared / Sequential Workspace 策略设计（F-42）

**状态**: ✅ 完成

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.5 F-42 Shared / Sequential Workspace 策略](./ARCHIVED_FEATURES.md#二十一5-f-42-shared-sequential-workspace-策略)。

**2026-06-03 后修复**: `extensions/api/orchestration.py` 构造 `WorkspaceConfig` 时缺少 `strategy` 参数传递，导致 `workflow.md` 中配置的工作区策略被静默忽略，所有 issue 均使用默认 `isolated` 行为。已补齐 `strategy=...` 参数传递。Dashboard 的 `ISSUE_STATUSES` 集合补充 `queued` 状态。

#### 3.1.10 Tool-call 审计旁路设计（F-45）

**状态**: ✅ 已完成 (2026-06-02)

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.6 F-45 Orchestrator tool-call 审计旁路](./ARCHIVED_FEATURES.md#二十一6-f-45-orchestrator-tool-call-审计旁路)。

#### 3.1.11 Issue 会话统一存储与实时介入协议（F-49）

**状态**: 📋 设计完成
**优先级**: P1
**依赖**: F-21（后台运行 + 恢复同步）、F-38（验证与报告闭环）、F-40（ProgressReporter Sink 协议重构）

##### 问题现状：两条互不兼容的事件路径

当前系统存在**两套并行但不可互操作的事件记录系统**：

| 维度 | 路径 A：正常 REPL 会话（`SessionStorage`） | 路径 B：Headless Issue Agent（`_write_event_log`） |
|------|------|------|
| 存储位置 | `~/.clawcodex/sessions/{sid}/` | `{workspace}/.event_logs/{issue_id}.ndjson` |
| 格式 | `transcript.jsonl` — 每行一个 `Message` dict (`role`, `content` blocks, `tool_use_id`) | 每行扁平事件 `{timestamp, type, tool_name, params}` |
| 可读性 | `session_resume.py` → `list[Message]` | 仅 `_run_tail` CLI 命令显示 |
| 配套设施 | `TailFollower`、`Session.load/resume`、`SessionStorage.read_transcript()` | 无 |
| 可恢复性 | ✅ 可重建 LLM context | ❌ 不能用于 `--resume` |
| 控制通道 | `asyncio.Event` + Unix socket（F-21） | 文件轮询 `{.orchestrator_control/{cmd}.control}` |

核心矛盾：**Headless agent 写 `.event_logs/` 扁平 NDJSON，上游已完备的 `SessionStorage` + `TailFollower` + `session_resume` 基础设施完全无法消费**。Observe/tail/takeover/resume 每个功能都需要在两条路径上重复实现。

##### 目标

统一 headless agent 和 REPL 会话的存储格式，在此之上建立双向实时介入协议（Unix socket），使 operator 可以通过 `attach` CLI 观察、中断、接管、恢复 issue agent 的运行。

| 场景 | 当前状态 | 目标状态 |
|------|---------|---------|
| 实时观察 | `tail` CLI 读 `.event_logs/` | `attach` CLI 通过 socket 流式接收 `TextDelta` / `ToolCallEvent` / `ToolResultEvent` / `PhaseComplete` |
| Ctrl+C 中断 | ❌ 不支持（仅 `stop` 控制文件） | socket 发送 `pause` → agent 挂起等待 operator 输入 |
| 人工接管 | ❌ 不支持 | `pause` 后 operator 键入 hint，agent 恢复后消费 |
| `/resume` 恢复自动值守 | ❌ 不支持 | socket 发送 `resume`（可选附带 prompt）→ agent 继续 loop |
| Session 恢复崩溃 | ❌ `.event_logs/` 无法重建 LLM context | 统一使用 `SessionStorage` → `session_resume.resume_session()` |
| detach | ❌ 不支持 | socket `detach` → agent 继续运行，operator 断开 |

##### 核心设计

```
AgentRunner (headless)
  │
  ├── prompt → QueryRunner → LLM → events
  │                                  │
  │                                  ├── SessionStorage.write_raw(msg_dict)
  │                                  │    └── ~/.clawcodex/sessions/{run_id}/transcript.jsonl
  │                                  │         （同一格式，非 .event_logs/）
  │                                  │
  │                                  ├── event_bus (asyncio.Queue)
  │                                  │    └── ControlSocket → Unix socket
  │                                  │         └── attach CLI (TUI)
  │                                  │
  │                                  └── ProgressSink (F-40)
  │
  └── session.pause_resume_event (asyncio.Event)
       └── ControlSocket → "pause" / "resume" / "inject"
```

##### 改造点清单

**Phase 0 — 统一事件存储**（1-2天，核心基础）

| 文件 | 改动 |
|------|------|
| `extensions/orchestrator/agent_runner.py` | `AgentSession` 增加 `session_storage: SessionStorage`；`run()` 中 `init_metadata(model, cwd, title)`；替换 `_write_event_log()` → `session_storage.write_raw(msg_dict)` + `flush()` |
| `extensions/orchestrator/agent_runner.py` | 删除 `_write_event_log()` 方法；删除 `.event_logs/` 目录创建逻辑 |
| `extensions/orchestrator/cli/issue.py` | `_run_tail` 改为读 `transcript.jsonl`（或保留兼容双读） |
| `src/services/session_storage.py` | 无改动（复用现有 `SessionStorage`） |

统一后的效果：headless agent 的每个 tool_use / tool_result / text_delta **都以 Message dict 格式写入 session JSONL**，`TailFollower` 可以直接 follow，`session_resume` 可以直接重建 LLM context。

**Phase 1 — Unix Socket 控制通道**（2-3天）

| 新增文件 | 说明 |
|----------|------|
| `extensions/orchestrator/control_socket.py` | `ControlSocket` 类：在 `{workspace}/.run_control/{issue_id}.sock` 监听 Unix domain socket；暴露 `poll_commands() → AsyncIterator[ControlCommand]` 和 `send_events()` |
|  | `ControlCommand` dataclass：`cmd: Literal["pause", "resume", "inject", "stop", "detach", "takeover"]` + `payload: str` |
|  | `EventFrame` dataclass：事件序列化帧 `{type, data, ts}` 供 socket 客户端流式接收 |

关键接口：

```python
# control_socket.py
@dataclass
class ControlCommand:
    cmd: Literal["pause", "resume", "inject", "stop", "detach", "takeover"]
    payload: str = ""  # resume 时附带 prompt，inject 时附带 hint

class ControlSocket:
    """Bidirectional control via Unix domain socket."""
    def __init__(self, sock_path: str):
        self._path = Path(sock_path)
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._command_queue: asyncio.Queue[ControlCommand] = asyncio.Queue()
        self._event_queue: asyncio.Queue[dict] | None = None

    async def start(self):
        """Start listening on Unix socket."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client, str(self._path))

    async def poll_commands(self) -> AsyncIterator[ControlCommand]:
        while not self._stopped:
            cmd = await asyncio.wait_for(
                self._command_queue.get(), timeout=0.5)
            yield cmd

    async def send_event(self, event: dict):
        """Broadcast event to all connected clients."""
        frame = json.dumps(event) + "\n"
        for w in self._clients:
            try:
                w.write(frame.encode())
                await w.drain()
            except Exception:
                pass

    async def _handle_client(self, reader, writer):
        self._clients.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break  # EOF → disconnect
                cmd = ControlCommand(**json.loads(line))
                await self._command_queue.put(cmd)
        finally:
            self._clients.discard(writer)
            writer.close()

    async def stop(self):
        self._server.close()
        if self._path.exists():
            self._path.unlink()
```

**在 `AgentRunner.run()` 中的集成点**：

```python
# 启动时
session.control_socket = ControlSocket(
    workspace.path / ".run_control" / issue.id)
await session.control_socket.start()

# 事件循环内 — 发送事件给附加的客户端
await session.control_socket.send_event({
    "type": "tool_call",
    "tool_name": event.tool_name,
    "params": event.params,
    "ts": time.time(),
})

# 每轮 turn 开始前检查控制命令
async for cmd in session.control_socket.poll_commands():
    if cmd.cmd == "pause":
        session.paused = True
        session.pause_reason = "operator_interrupt"
        # 挂起直到 resume 或接管
        await session.pause_resume_event.wait()
        session.pause_resume_event.clear()
    elif cmd.cmd == "resume":
        if cmd.payload:
            session.prompt_override = cmd.payload
        session.paused = False
        session.pause_resume_event.set()
    elif cmd.cmd == "inject":
        # 注入 hint，不中断当前循环
        # 写入 operator_hints 文件
        ...
    elif cmd.cmd == "stop":
        return  # 退出 run()
    elif cmd.cmd == "takeover":
        # 停止 agent，operator 通过 CLI 直接交互
        return
```

**Phase 2 — `attach` CLI TUI**（2-3天）

| 新增/修改文件 | 说明 |
|---------------|------|
| `extensions/orchestrator/cli/attach.py` | `attach` 子命令：连接 Unix socket + 渲染实时事件流 |

交互设计：

```
$ clawcodex-dev orchestrator issue attach --id 003-F-12-cache-warning
┌─────────────────────────────────────────────────────┐
│ 🔗 Attached to issue 003-F-12 (running)             │
│ Session: 9a8b7c6d5e  |  Turn: 3/25  |  Tools: 7   │
├─────────────────────────────────────────────────────┤
│  Reading src/utils/cache.py...                       │
│  ✓ Found CacheWarning.__init__ at line 142           │
│  ✓ ...                                              │
│                                                      │
│ 📋 Pending tool call: Edit src/utils/cache.py        │
│   param: path = src/utils/cache.py                  │
│   param: old_string = "MAX_CACHE_SIZE = 1000"       │
│   param: new_string = "MAX_CACHE_SIZE = 500"        │
├─────────────────────────────────────────────────────┤
│ ⏸ Paused — press Ctrl+D to resume, Ctrl+C to       │
│   disconnect, or type a hint below:                 │
│ > 注意也检查一下 tests/ 中的对应的测试用例         │
│ (hint sent, agent will resume)                      │
└─────────────────────────────────────────────────────┘
```

| 交互 | 行为 |
|------|------|
| 连接 | 发送 `{"cmd": "attach"}` → 接收当前 session state + 最近事件 |
| 事件到达 | 实时渲染 `PhaseComplete` / `TextDelta` / `ToolCallEvent` / `ToolResultEvent` |
| **Ctrl+C** | 发送 `{"cmd": "pause"}` → 显示 `(Paused) >` 提示符 |
| `>` 输入普通文本 | 发送 `{"cmd": "inject", "payload": "..."}` |
| **`/resume`** | 发送 `{"cmd": "resume"}` |
| **`/resume 尝试用 pip install 解决依赖** | 发送 `{"cmd": "resume", "payload": "尝试用 pip install 解决依赖"}` |
| **`/inspect`** | 通过 `SessionStorage.read_transcript()` 读取消息历史 |
| **`/stop`** | 发送 `{"cmd": "stop"}` |
| **`/takeover`** | 发送 `{"cmd": "takeover"}` → 停掉 agent + 启动 REPL |
| **`/detach`** | 发送 `{"cmd": "detach"}` → 断开 socket，agent 继续运行 |
| Ctrl+D | detach（同 `/detach`） |

**Phase 3 — Session 恢复**（0.5天，Phase 0 的增量产出）

统一存储后，Session 恢复变为零额外工作：

```python
# 当 agent 崩溃或 operator 想从 checkpoint 恢复
session = Session.resume(issue_session_id)
# SessionStorage 已包含所有历史消息
# session_resume.resume_session() 重建 LLM context
# 新的 AgentRunner 可从此处继续
```

##### 实施阶段总结

| Phase | 工作量 | 交付物 | 验收标志 |
|-------|--------|--------|---------|
| **Phase 0** — 存储统一 | 1-2天 | `agent_runner.py` 使用 `SessionStorage`；`.event_logs/` 废弃 | `transcript.jsonl` 在 headless 和 REPL 路径格式一致 |
| **Phase 1** — Socket 控制 | 2-3天 | `control_socket.py`；AgentRunner 集成 pause/resume/inject/stop | 外部进程可通过 Unix socket 暂停/恢复 headless agent |
| **Phase 2** — attach CLI | 2-3天 | `cli/attach.py`；实时 TUI | `issue attach --id X` 可观察、打断、接管、恢复 |
| **Phase 3** — Session 恢复 | 0.5天 | 利用现有 `Session.resume()` | 崩溃后从 `SessionStorage` 重建 LLM context 继续 |

##### 与现有组件的集成

| 组件 | 协作方式 |
|------|---------|
| `SessionStorage` (services) | Phase 0 核心依赖 — headless agent 写入同格式 `transcript.jsonl` |
| `TailFollower` (services) | Phase 1 可选依赖 — socket 客户端可 fallback 到 tail JSONL |
| `session_resume.resume_session()` | Phase 3 — 从统一 JSONL 重建 `list[Message]` |
| `F-40 ProgressSink` | Phase 1 合并事件扇出 — `ControlSocket.send_event()` 可注册为额外 sink |
| `F-21 bg` / `--resume` | Phase 2 复用 Ctrl+B / 后台运行的行为语义 |
| `F-38 git_sync` | Phase 0 无影响 — git_sync 操作 workspace git，不改 session 存储 |
| `F-39 retry` | Phase 2 扩展 — retry 可携带 `--attach` 参数在新 run 上立即 attach |

##### 风险与约束

| 风险 | 缓解措施 |
|------|---------|
| `.event_logs/` 有存量用户 | Phase 0 保持向后兼容写入（双写），Phase 2 删除时发 deprecation warning |
| Unix socket 在 Windows 不可用 | 回退到 Named Pipe 或 TCP localhost socket；socket path 抽象为 `BindAddress` Protocol |
| Phase 1 pause 时 agent 在 tool call 中间 | 不中断正在执行的 tool call；tool call 返回后检查 `paused` flag 再挂起 |
| 多客户端 attach 冲突 | `ControlSocket` 广播 + 最后写入者优先（Last-write-wins） |
| 安全：任意本地进程可连接 socket | socket 设置 `umask 0077`（仅 owner）；`/takeover` 需额外的身份确认 |

##### 已拟定的设计决定

1. **Phase 0 优先于一切**。存储不统一，后面所有基础设施 `SessionStorage` / `TailFollower` / `session_resume` 全用不上。不完成 Phase 0 不开始 Phase 1。
2. **使用 Unix domain socket**（非文件轮询、非 SSE、非 gRPC）。轮询延迟高；SSE 单向；gRPC 依赖重。Unix socket 是 asyncio 原生支持的最轻量双向方案。
3. **`SessionStorage` 不改一行**。Phase 0 零改动上游文件 — `SessionStorage` 的 `write_raw()` 方法就是为这种场景设计的。
4. **`ControlSocket` 落地在 `extensions/orchestrator/`**，不入侵 `src/`。遵守 F-48 解耦约束。
5. **attach TUI 不需要 curses/textual**。用最简单的 `select.poll()` + `sys.stdin.read()` + `print()` 实现，避免新增依赖。

##### 依赖与协同

| 依赖 | 类型 | 说明 |
|------|------|------|
| F-21 bg + `--resume` | 行为参考 | Ctrl+B / TailFollower 的用户体验作为 F-49 attach 的设计基线 |
| F-38 git_sync | 无依赖 | git_sync 不改 session 存储，Phase 0 无影响 |
| F-40 ProgressSink | 可复用 | Phase 1 `ControlSocket` 可注册为 `ProgressSink` 消费事件 |
| F-48 解耦约束 | 架构约束 | 所有新代码落在 `extensions/orchestrator/` |
| F-39 retry | Phase 2 联动 | retry 新 run 可 `--attach` 即时观察 |
| `src/services/session_storage.py` | 硬依赖 | Phase 0 核心依赖，但零修改 |
| `src/services/tail_follower.py` | 可选 | Phase 1 可读 transcript.jsonl 替代 socket |

---

#### 3.1.12 AgentRunner 空转检测机制（F-51）

**状态**: ✅ 完成
**优先级**: P0

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.12 F-51 AgentRunner 空转检测机制](./ARCHIVED_FEATURES.md#二十一12-f-51-agentrunner-空转检测机制no-op-detection)。

---

#### 3.1.13 AgentRunner / QueryRunner 运行期可观测性与 stuck-run debug（F-54）

**状态**: 📋 设计完成
**优先级**: P0
**跟踪文档**: `docs/PROGRESS.md` → `F-54: AgentRunner / QueryRunner 运行期可观测性`
**触发场景**: 2026-06-04 本地 sequential orchestrator 执行 F-40 issue 时，daemon 日志显示 headless agent 已启动并持续产生 provider HTTP request，但 issue 长时间停留在 `running`，workspace 无文件改动、无 event log、无 report、无 commit。orchestrator-level watchdog 已能把永久 `running` 转换为 `agent_timeout` + retry，但仍缺少解释「卡在 QueryRunner / headless / AgentRunner / git sync 哪一层」的诊断信号。

##### 问题现状

当前 headless issue agent 的可观测链路存在断点：

1. `extensions/api/query.py:QueryRunner.stream()` 在 `run_headless_session(...)` 返回之前不会把 stdout 作为 `TextDelta` yield 给上游。
2. `run_headless_session(...)` 运行期间只有 `on_event` bridge 传出的 tool event 会被转换为 `ToolCallEvent` / `ToolResultEvent`。
3. 如果 headless future pending 且没有 tool event，`AgentRunner.run()` 看不到任何 QueryEvent，自然不会写 `.event_logs/`、不会推进 `ProgressReporter`、不会进入 report/git sync。
4. provider request 日志只能证明 LLM 请求发生过，不能证明 headless session 已完成、tool event 已桥接、AgentRunner 已消费事件。
5. watchdog timeout 只能给出 `agent_timeout`，没有 last event、turn count、tool count、stdout length、workspace dirty 等定位信息。

##### 设计目标

把 stuck-run 诊断拆成四层观测，而不是只依赖最终 timeout：

1. **QueryRunner 层**：确认 headless session 是否启动、future 是否 pending、stdout 是否增长、tool bridge 是否收到事件。
2. **AgentRunner 层**：确认 QueryEvent 是否被消费、当前 turn 是否推进、tool/text/session complete 事件计数是否变化。
3. **Orchestrator watchdog 层**：timeout 时持久化 session 快照，而不是只写失败原因。
4. **Registry / CLI 层**：让 `issue list/status` 能直接显示最后一次 agent 事件与 debug log 路径。

短期目标是轻量 debug 可观测性；长期与 F-49 会话统一存储汇合，避免形成第二套永久 transcript 系统。

##### 观测点设计

| 层级 | 触发点 | 记录字段 | 目的 |
|------|--------|----------|------|
| `QueryRunner.stream()` | stream start | workspace、provider、model、permission_mode、max_turns、prompt length、run_id（若可传入） | 判断 headless 是否真的启动 |
| `QueryRunner.on_event` | bridge 收到 headless event | kind、tool_name、tool_use_id、event_count、seconds_since_start | 判断事件是否从 headless 进入 Python bridge |
| `QueryRunner.stream()` | future pending heartbeat | future_done、seconds_since_last_event、event_counts、stdout_len | 判断是否卡在 provider/headless 内部 |
| `AgentRunner.run()` | turn start | issue_id、run_id、turn_number、attempt、workspace | 判断多轮循环是否推进 |
| `AgentRunner.run()` | QueryEvent consumed | event_type、tool_name、text_len、session_complete reason、last_event_at | 判断事件是否被 AgentRunner 消费 |
| `AgentRunner.run()` | turn end | turn_has_tool_calls、turn_output_len、tool_count、workspace_dirty、no-op counter | 判断 turn 是否产生可同步改动 |
| `Orchestrator._run_issue()` | watchdog timeout | status、turn_count、tool_count、last_event_type、last_tool_name、workspace_dirty、event_log_path、tool_events_path | timeout 后保留定位快照 |

##### Debug NDJSON

短期新增 per-run debug log，建议路径：

```text
{workspace}/.orchestrator_control/runs/{run_id}/debug.ndjson
```

该目录属于 orchestrator 控制文件，必须沿用 sequential workspace 的 ignore/exclude 策略，不能进入 issue commit。

示例：

```json
{"ts": "2026-06-04T20:01:26Z", "stage": "agent_runner.start", "issue_id": "F-40-progress-sink", "run_id": "..."}
{"ts": "2026-06-04T20:01:27Z", "stage": "query_runner.start", "provider": "minimax", "permission_mode": "bypassPermissions", "prompt_len": 18420}
{"ts": "2026-06-04T20:01:29Z", "stage": "headless.event", "kind": "tool_use", "tool": "Read", "tool_use_id": "..."}
{"ts": "2026-06-04T20:01:29Z", "stage": "agent_runner.event", "type": "ToolCallEvent", "tool": "Read", "turn": 1}
{"ts": "2026-06-04T20:03:27Z", "stage": "query_runner.heartbeat", "future_done": false, "seconds_since_last_event": 120, "stdout_len": 0, "tool_events": 0}
{"ts": "2026-06-04T20:29:57Z", "stage": "orchestrator.timeout", "turn_count": 0, "tool_count": 0, "last_event_type": null, "workspace_dirty": false}
```

##### Timeout diagnostic snapshot

watchdog 触发时，除现有 `mark_failed_with_reason(...)` 外，写入结构化摘要：

```json
{
  "issue_id": "F-40-progress-sink",
  "status": "agent_timeout",
  "run_id": "...",
  "turn_count": 0,
  "tool_count": 0,
  "output_text_len": 0,
  "last_event_at": null,
  "last_event_type": null,
  "last_tool_name": null,
  "workspace_dirty": false,
  "event_log_path": "/tmp/clawcodex-dev/.event_logs/F-40-progress-sink.ndjson",
  "tool_events_path": "~/.clawcodex/tool-events/.../events.ndjson",
  "debug_log_path": "/tmp/clawcodex-dev/.orchestrator_control/runs/.../debug.ndjson"
}
```

若 registry 暂不扩 schema，可把 compact JSON 摘要写入 `verification_output` 或 `last_hook_error`；如果 CLI 查询需要结构化字段，再扩展 `IssueRecord`。

##### Registry / CLI 可见字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `run_id` | `str | None` | 当前或最后一次 agent run id |
| `last_agent_event_at` | `float | None` | 最近一次 `AgentRunner` 消费 QueryEvent 的时间 |
| `last_agent_event` | `str | None` | 如 `ToolCallEvent:Read`、`TextDelta`、`SessionComplete:success` |
| `last_tool_name` | `str | None` | 最近 tool call/result 名称 |
| `turn_count` | `int` | 当前 session 已完成 turn 数 |
| `tool_count` | `int` | 当前 session 已消费 tool event 数 |
| `timeout_deadline_at` | `float | None` | watchdog 预计触发时间 |
| `debug_log_path` | `str | None` | per-run debug NDJSON 路径 |

CLI 展示优先做只读摘要，不引入新的控制语义：

```text
F-40-progress-sink  running  run=...  turn=0 tools=0 last_event=none deadline=2026-06-04T20:29:57Z
  debug: /tmp/clawcodex-dev/.orchestrator_control/runs/.../debug.ndjson
```

##### 实施阶段

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | 新增轻量 debug writer：append-only NDJSON、容错写入、不会因 debug I/O 失败中断 agent run | 📋 待开始 |
| 2 | `QueryRunner.stream()` 增加 start / on_event / heartbeat 观测点；heartbeat 只写 debug，不 yield `TextDelta`，避免污染 agent 输出 | 📋 待开始 |
| 3 | `AgentRunner.run()` 维护 per-session counters：last_event_at、last_event_type、last_tool_name、turn_count、tool_count、output_text_len | 📋 待开始 |
| 4 | `Orchestrator._run_issue()` watchdog timeout 时写 snapshot，并把 compact 摘要同步到 registry failure context | 📋 待开始 |
| 5 | `IssueRegistry` 按需扩展 debug 字段；CLI `issue list/status` 显示 run_id、last event、turn/tool counts、debug_log_path | 📋 待开始 |
| 6 | 测试覆盖 hanging QueryRunner heartbeat、event counter 更新、timeout snapshot、debug 控制目录不进入 git diff | 📋 待开始 |

##### 验收标准

1. 模拟 `QueryRunner` future pending 且无 tool event 时，debug log 持续出现 `query_runner.heartbeat`，并包含 `seconds_since_last_event` 与 `stdout_len`。
2. 模拟正常 tool event 时，debug log 同时出现 `headless.event` 与 `agent_runner.event`，registry/CLI 摘要显示最近 tool name。
3. watchdog timeout 后，registry failure context 包含 run_id、turn_count、tool_count、last_event、workspace_dirty 与 debug_log_path。
4. sequential workspace 下 `.orchestrator_control/runs/*/debug.ndjson` 不出现在 `git status --porcelain` 中，不会被 `GitSyncService` 提交。
5. F-49 落地后，F-54 debug log 可迁移/双写到统一 `SessionStorage`，不阻断 attach/resume 设计。

##### 风险与约束

- **日志噪音**：heartbeat 过密会导致 debug 文件膨胀。默认 heartbeat 周期建议不小于 30s，并只在 headless future pending 时写。
- **I/O 反向影响运行**：debug writer 必须 fail-open，写入失败只 `logger.debug/exception`，不能让 agent run 失败。
- **stdout 隐私/体积**：heartbeat 只记录 stdout length，不记录 stdout 内容；实际输出仍由原 `TextDelta` 路径处理。
- **解耦边界**：本特性落在 `extensions/api/query.py`、`extensions/orchestrator/*` 与测试；不修改 `src/` 核心代码。
- **与 F-49 重叠**：F-54 只解决 stuck-run debug，不提供 attach、pause/resume、session replay；这些仍属于 F-49。

##### 依赖与协同

- **依赖 watchdog**：orchestrator-level watchdog 提供 timeout 触发点；F-54 在该点补 diagnostic snapshot。
- **协同 F-40**：F-40 补齐 progress/session complete 事件扇出；F-54 补齐没有事件时的 debug 盲区。
- **协同 F-45**：F-45 记录 tool approval/handler 后的 `tool-events.ndjson`；F-54 记录 headless bridge 与 AgentRunner 消费前后的状态。
- **协同 F-49**：F-54 是低成本诊断先行项；F-49 后续统一 transcript、socket attach 与恢复能力。

---

#### 3.1.14 人工检视闸门设计（F-44）

**状态**: ✅ 完成
**优先级**: P1

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.11 F-44 Orchestrator 人工检视闸门](./ARCHIVED_FEATURES.md#二十一11-f-44-orchestrator-人工检视闸门review-gate)。

---

### 3.2 Agent 阶段性进度汇报

**状态**: ✅ 已完成（F-20）
**目标**: 在 Agent 编排中阶段性将结果汇报至任务看板，将任务看板提取为工具

> 三组合实现方案（检查点触发 + ProgressReportTool + ToolContext.tasks）、架构设计、工具 Schema、与现有组件集成点等已归档。
> 详见 [ARCHIVED_FEATURES.md §十六（Orchestrator 自主模式 16.x）](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成) 与对应进度归档 [ARCHIVED_PROGRESS.md F-20](./ARCHIVED_PROGRESS.md#f-20-agent-阶段性进度汇报)。

---

### 3.3 Team 成员管理（Phase-7）

**状态**: 规划中
**目标**: TeamCreate 扩展 `members` 数组，跟踪团队成员 Agent

#### 3.3.1 数据模型

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

#### 3.3.2 核心机制

| 机制 | 说明 |
|------|------|
| TeammateInit | `agent(run_in_background=true)` 时自动注册到 `members` |
| 状态同步 | TaskOutput 显示 completed/failed 时更新成员状态 |
| 名称注册 | Agent 名称冲突检测 `agent_name_registry` |
| 递归 Fork 保护 | Fork Agent 无法嵌套调用 Fork |

#### 3.3.3 实现文件

| 文件 | 状态 |
|------|------|
| `tool_system/tools/team.py` | ✅ 已实现基础 TeamCreate/TeamDelete |
| `tool_system/tools/agent.py` | ⚠️ 待集成 TeammateInit |
| `services/swarm/agent_name_registry.py` | ✅ 已实现名称注册表 |

#### 3.3.4 测试覆盖

| 测试文件 | 测试用例 |
|----------|----------|
| `test_team_file.py` | `test_team_file_created_with_members_array`, `test_team_file_schema_members_array`, `test_team_file_missing_members_tolerated` |
| `test_team_membership.py` | `test_is_team_lead_true_*`, `test_is_team_lead_false_*` |

---

### 3.4 结构化输出增强（Outlines）

**状态**: 适配器已完成，待集成
**目标**: 使用 Outlines 预生成约束替代 json.loads + 手动验证

#### 3.4.1 适用场景

| 场景 | 当前实现 | Outlines 方案 |
|------|---------|---------------|
| Token 预算分析 | 正则解析 | 结构化 `TokenBudgetAnalysis` |
| 工具调用决策 | json.loads 解析 | 结构化 `ToolCallDecision` |
| 压缩策略选择 | 手动判断 | 结构化 `CompactionStrategy` |
| Bash 命令分类 | 多个 validator | 结构化 `BashSafetyLevel` |

#### 3.4.2 数据模型

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

#### 3.4.3 实现文件

| 文件 | 状态 |
|------|------|
| `agent/_outlines_adapter.py` | ✅ 适配器已完成 |
| `tool_system/` 集成 | ⏳ 待进行 |

---

### 3.5 MCP 扩展功能

**状态**: 基础已完成（F-3），持续增强
**目标**: 完整的 MCP 协议支持

> 5 项基础传输与硬化能力（Stdio / HTTP+SSE / WebSocket / OAuth / HTTPS+XSS 硬化）已归档。
> 详见 [ARCHIVED_FEATURES.md §十七（MCP 协议扩展）](./ARCHIVED_FEATURES.md#十七mcp-协议扩展) 与对应进度归档 [ARCHIVED_PROGRESS.md F-3](./ARCHIVED_PROGRESS.md#f-3-mcp-协议扩展)。

#### 3.5.1 待增强

| 功能 | 优先级 | 说明 |
|------|--------|------|
| MCP 资源缓存 | P2 | 减少重复获取 |
| MCP Batch 工具调用 | P2 | 批量工具执行 |
| MCP Progress 通知 | P3 | 长任务进度报告 |

---

### 3.6 Agent 记忆作用域隔离（F-13）（已完成）

**状态**: ✅ 完成

> 详细设计与验证记录已归档至 [ARCHIVED_FEATURES.md §二十一.7 F-13 Agent 记忆作用域隔离](./ARCHIVED_FEATURES.md#二十一7-f-13-agent-记忆作用域隔离)。

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

**实现清单**:

| 文件 | 说明 |
|------|------|
| `src/pos_converter/__init__.py` | 模块入口 |
| `src/pos_converter/sdk_parser.py` | SDK 解析（支持 OpenAPI JSON / URL / 简单方法列表） |
| `src/pos_converter/skill_grouper.py` | Skill 分组（静态 MappingRule + LLM 辅助） |
| `src/pos_converter/agent_builder.py` | Agent 构建 + 持久化（`~/.clawcodex/agents/<name>.json`） |
| `src/pos_converter/convert_pos_skill.py` | `/convert-pos-to-agent` Skill 实现 |
| `src/pos_converter/templates.py` | 模板定义 |
| `src/skills_ext/bundled/pos_to_agent.py` | bundled skill 注册（解耦上游） |

**三层映射实现**:

```
SdkParser.parse()           → list[SdkMethod]  (原子工具)
SkillGrouper.group()       → list[SkillSpec]  (Skill 规范)
AgentBuilder.build()       → AgentDefinition (Agent 定义)
persist_converted_agent()   → ~/.clawcodex/agents/<name>.json
```

**使用方式**:

1. **斜杠命令**（REPL/TUI 中）:
   ```bash
   /convert-pos-to-agent docker_build,k8s_apply::CI/CD pipeline
   ```
   别名: `/pos-to-agent`

2. **CLI 子命令**（Linux/macOS shell）:
   ```bash
   clawcodex-dev pos convert <sdk_spec> [--out <output_dir>] [--requirements "<requirements>"] [--name <agent_name>]
   ```
   示例:
   ```bash
   clawcodex-dev pos convert docker_build,k8s_apply --out ./.clawcodex --requirements "CI/CD pipeline" --name cicd-agent
   ```
   支持从 `workflow.md` 文件解析前端元数据并输出 Agent/Workflow/Skill 定义文件。

3. **Python API**（编程调用）:
   ```python
   from extensions.pos_converter import convert_pos_to_agent
   result = convert_pos_to_agent(sdk_spec="docker_build,k8s_apply", requirements="CI/CD pipeline")
   ```

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

#### 3.10.1 功能说明

允许 Agent 分析第三方工具（CLI 命令或 HTTP API）的接口规范，然后动态创建一个可用的工具：

```
Agent 分析 CLI 规范 → 生成工具规范 → 调用 CreateAgentTool → 注册新工具 → 使用新工具
```

#### 3.10.2 架构设计

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

#### 3.10.3 工具规范（AgentToolSpec）

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

#### 3.10.4 三种 call_impl 安全限制

| call_type | call_impl 示例 | 安全级别 |
|-----------|---------------|---------|
| `bash` | `"git status --porcelain {path}"` | ✅ 占位符防注入，预定义命令白名单 |
| `http` | `{"method": "GET", "url": "https://api.github.com/{endpoint}"}` | ✅ 模板化，方法白名单 |
| `python` | `"fetch_data"` → 映射到预定义函数 | ⚠️ 仅白名单函数注册 |

**命令白名单（bash）**：`git`, `gh`, `glab`, `curl`, `wget`, `kubectl`, `docker`, `npm`, `pip`

**HTTP 方法白名单**：`GET`, `POST`, `PUT`, `DELETE`, `PATCH`

#### 3.10.5 CreateAgentTool 输入规范

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

#### 3.10.6 安全性约束

| 约束类型 | 实现位置 | 说明 |
|---------|---------|------|
| 命令白名单 | `validators.py:_validate_bash_impl` | 仅允许预定义命令 |
| HTTP 方法白名单 | `validators.py:_validate_http_impl` | 仅白名单方法 |
| Python 函数注册 | `validators.py:_validate_python_impl` | 仅白名单函数 |
| 无任意代码执行 | `factory.py` | call_impl 是模板/映射，非代码 |
| 参数化防注入 | `call_handlers/bash.py` | format 替换，无 shell 注入 |
| 超时保护 | `call_handlers/bash.py` | subprocess timeout=30 |

#### 3.10.7 持久化机制

Agent 创建的工具保存到 `~/.clawcodex/agent-tools/{name}.json`，重启后自动加载。

#### 3.10.8 与现有系统集成

| 现有组件 | 如何协作 |
|---------|---------|
| `build_tool()` | 作为工厂函数，CreateAgentTool 调用它 |
| `ToolRegistry` | 工具创建后调用 `registry.register(tool)` |
| `parse_agent_markdown` | 已有工具定义解析，可复用 schema 验证 |
| MCP 工具包装 | 参考 `tool_wrapper.py` 的声明式工具模式 |
| `resolve_agent_tools()` | 允许 `source="agent-created"` 的工具被解析 |

#### 3.10.9 实现文件

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

### 3.11 sessionStorage 容量限制

**状态**: ⏳ 待实现
**目标**: 防止长时间运行的 daemon/swarm 会话导致内存泄漏

#### 3.11.1 功能说明

为 `existingSessionFiles` Map 设置容量上限，防止无限增长：

```python
MAX_CACHED_SESSION_FILES = 200

def add_session_file(sessionId: UUID, filePath: str):
    if len(existingSessionFiles) >= MAX_CACHED_SESSION_FILES:
        oldest_key = next(iter(existingSessionFiles))
        del existingSessionFiles[oldest_key]
    existingSessionFiles[sessionId] = filePath
```

#### 3.11.2 问题场景

- daemon/swarm 模式下长时间运行
- sessionId 频繁创建销毁
- Map 无限增长导致 OOM

#### 3.11.3 实现文件

| 文件 | 位置 | 状态 |
|------|------|------|
| sessionStorage | `utils/sessionStorage.ts` → `utils/session_storage.py` | 待实现 |

---

### 3.12 cacheWarning 容量限制（F-12）

**状态**: ✅ 完成

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.14 cacheWarning 容量限制](./ARCHIVED_FEATURES.md#二十一14-cachewarning-容量限制f-12)。

---
### 3.13 Issue 语义澄清流程（自主模式扩展）

**状态**: ✅ 已完成（F-1.5~F-1.11，Phase A-G 全部完成）
**优先级**: P1
**目标**: 当 Issue 语义模糊时，通过**三通道优先机制**获取澄清——本地操作员（Dashboard/ClarificationQueue）优先，作者 @mention 兜底

> 三通道优先机制（Dashboard / ClarificationQueue / @mention）、平台能力对比、整体流程图、各通道详细设计、ClarificationStatus 枚举（含冲突处理 `DUPLICATE_REJECTED` / `STALE_REJECTED` / `CONFLICT_RESOLVED`）、多渠道冲突处理状态机、CLI `clarify` 命令、TrackerAdapter 评论接口与 GitHub/Gitee/GitCode 实现、IssueRegistry 澄清字段持久化、PromptBuilder 澄清内容注入、escalation 策略与配置等已归档。
> 详见 [ARCHIVED_FEATURES.md §16.5（Issue 语义澄清流程）](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成) 与对应进度归档 [ARCHIVED_PROGRESS.md F-1.x 子特性](./ARCHIVED_PROGRESS.md#f-1x-orchestrator-自主模式f-1-子特性全部完成)。

---


### 3.14 Auto 模式 (TRANSCRIPT_CLASSIFIER)

**状态**: ⏳ 待实现
**优先级**: P2
**目标**: 基于 LLM 的自动权限模式切换，减少交互疲劳

#### 3.14.1 功能说明

Auto 模式是一种智能权限模式，通过 LLM 分类器（TRANSCRIPT_CLASSIFIER）自动判断何时允许执行敏感操作。在长时间任务或重复性操作场景下，Auto 模式可以减少用户确认的交互频率。

#### 3.14.2 工作原理

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

#### 3.14.3 与手动模式的区别

| 模式 | 触发方式 | 确认频率 | 适用场景 |
|------|---------|---------|---------|
| `default` | 手动确认每个敏感操作 | 高 | 学习/审查模式 |
| `acceptEdits` | 手动确认写操作 | 中 | 代码迭代 |
| `plan` | 仅读取，编辑前分析 | 低 | 探索代码库 |
| `auto` | LLM 自动判断 | 自动调节 | 长任务/减少疲劳 |
| `bypassPermissions` | 无限制 | 无 | 隔离环境 |

#### 3.14.4 循环切换逻辑（已实现部分）

`Shift+Tab` 循环切换顺序：
```
default → acceptEdits → plan → bypassPermissions (如果可用) → default
```

注意：`auto` 模式不出现在手动循环中，需要通过 `--permission-mode auto` 启动或由分类器自动触发。

#### 3.14.5 待实现组件

| 组件 | 文件 | 说明 |
|------|------|------|
| TRANSCRIPT_CLASSIFIER | `permissions/classifier.py` | LLM 分类器核心 |
| canCycleToAuto | `permissions/cycle.py` | 判断是否可切换到 auto |
| Auto Mode 集成 | `agent/run_agent.py` | 在工具执行前调用分类器 |
| 分类结果缓存 | `permissions/cache.py` | 避免重复分类 |

#### 3.14.6 分类器 prompt 设计

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

#### 3.14.7 实施阶段

| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase A1 | TRANSCRIPT_CLASSIFIER 核心实现 | P2 | ⏳ 待开始 |
| Phase A2 | `canCycleToAuto()` 判断逻辑 | P2 | ⏳ 待开始 |
| Phase A3 | Auto Mode 工具执行前集成 | P2 | ⏳ 待开始 |
| Phase A4 | 分类结果缓存机制 | P3 | ⏳ 待开始 |

---

### 3.15 Agent 间自主观察与消息交互

**状态**: ✅ 已完成（Phase M1-M5 全部完成）
**优先级**: P1
**目标**: 实现 Manager Agent 全自动观察 Worker Agent 状态并注入指令，支持优先级队列和权限审批

> 角色定义（Manager / Worker 通过工具组合自动识别）、核心工具（`TaskInspect` + `TaskDirectives`）、优先级队列（`queue_pending_message` priority 字段 + `drain_pending_messages` 按优先级消费）、工具可见性过滤（仅 Manager 可调用）、权限规则传递与 Phase M1-M5 实施阶段已归档。
> 详见 [ARCHIVED_FEATURES.md §十八（Agent 间自主观察与消息交互）](./ARCHIVED_FEATURES.md#十八agent-间自主观察与消息交互) 与对应进度归档 [ARCHIVED_PROGRESS.md F-29（TaskInspect/TaskDirectives 工具注册）](./ARCHIVED_PROGRESS.md#f-29-taskinspecttaskdirectives-工具注册)。


### 3.16 Orchestrator CLI 运维操作界面

**状态**: ✅ 已完成（F-1.13，Phase O1-O8 全部完成）
**优先级**: P1
**目标**: 通过 `clawcodex orchestrator` 统一入口，实现运行期间的全程可视化监控与中途介入
---

### 3.17 CLI 模型供应商与模型切换设计（F-43）

**状态**: ✅ 已完成 (2026-06-02)

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.8 F-43 CLI 模型供应商与模型切换](./ARCHIVED_FEATURES.md#二十一8-f-43-cli-模型供应商与模型切换)。

**Sub-feature: 动态模型发现注册表 (post-archival, 2026-06)**

**状态**: ✅ 已完成 (2026-06-03)

> 已落地实现，14/14 F-43 单元测试 + 端到端验证通过。详见本节下方「验收」。

#### 动机
`available_models` 完全硬编码在 `src/providers/__init__.py` 中，新增模型（如 `gpt-5.5`）需要手动编辑上游文件，违背 F-48 解耦原则。动态模型发现注册表使扩展方通过一行 `register_discovery_hook()` 即可注入运行时模型发现，无需触碰 `src/`。

#### 实现设计

##### 1. 全局注册表 (`clawcodex_ext/cli/model_cmd/registry.py`)

```python
_DISCOVERY_HOOKS: dict[str, list[Callable[[], list[str]]]] = {}

def register_discovery_hook(
    provider: str, hook: Callable[[], list[str]]
) -> None:
    """注册一个 provider 的模型发现函数。幂等。"""
    _DISCOVERY_HOOKS.setdefault(provider, []).append(hook)
```

`ModelRegistry` 在 `__init__` 中接受 `discovery_hooks` 参数（默认引用全局 `_DISCOVERY_HOOKS`），`available_models()` 合并静态基线 + hooks 结果，去重且异常静默：

```python
class ModelRegistry:
    def __init__(self, provider_info=None, discovery_hooks=None):
        self.provider_info = provider_info or PROVIDER_INFO
        self._discovery_hooks = _DISCOVERY_HOOKS if discovery_hooks is None else discovery_hooks

    def available_models(self, provider: str) -> list[str]:
        baseline = list(self.provider_info[provider].get("available_models", []))
        for hook in self._discovery_hooks.get(provider, []):
            try:
                extra = hook()
                for m in extra:
                    if m not in baseline:
                        baseline.append(m)
            except Exception:
                pass  # best-effort — never fail the caller
        return baseline
```

`validate_model()` 和 `infer_provider_for_model()` 也通过 `available_models()` 读取，天然支持钩子发现。

##### 2. `openai-codex` API 发现钩子 (`clawcodex_ext/providers/hooks.py`)

```python
def _codex_api_discovery() -> list[str]:
    """通过 OAuth token 调用 Codex API 获取可用模型 ID。"""
    try:
        from src.auth.codex_oauth import get_codex_auth_status
        status = get_codex_auth_status()
        if not status.is_authenticated or not status.access_token:
            return []  # 无 token — 保持静态基线
        from src.providers.codex_models import get_codex_model_ids
        return get_codex_model_ids(status.access_token)
    except Exception:
        logger.debug("Codex API model discovery failed (non-fatal)", exc_info=True)
        return []
```

##### 3. 自动注册时机

`clawcodex_ext/providers/__init__.py` 在模块级别注册：

```python
from clawcodex_ext.providers.hooks import _codex_api_discovery
from clawcodex_ext.cli.model_cmd.registry import register_discovery_hook

register_discovery_hook("openai-codex", _codex_api_discovery)
```

由 `clawcodex_ext/__init__.py` 的 `from clawcodex_ext.providers import ...` 触发，在 `ModelRegistry` 首次实例化之前完成注册。

##### 4. `resolve()` 信任已保存配置 (`clawcodex_ext/cli/model_cmd/resolver.py`)

`validate_model` 失败时打印 warning 但信任已保存模型名（`model_source = "user-warn"`），确保 API 返回的新模型不因验证失败而被降级回默认：

```python
try:
    registry.validate_model(configured_model, provider)
    model = configured_model
    model_source = "user"
except Exception:
    print(f"Warning: model '{configured_model}' is not in the known list "
          f"for provider '{provider}' — using it anyway (saved config)",
          file=sys.stderr)
    model = configured_model
    model_source = "user-warn"
```

##### 5. 回归静态基线 (`src/providers/__init__.py`)

移除了 `gpt-5.5` 硬编码，`openai-codex` 的 `available_models` 仅保留 `gpt-5.3-codex` / `gpt-5.3-codex-spark` 两条静态基线，余量由 hooks 动态发现。

#### 文件变更

| 文件 | 变更说明 |
|------|----------|
| `clawcodex_ext/cli/model_cmd/registry.py` | 新增 `_DISCOVERY_HOOKS` 全局 dict、`register_discovery_hook()`、`ModelRegistry.__init__` 接受 `discovery_hooks`、`available_models()` 合并 hooks（去重/静默）、`validate_model()`/`infer_provider_for_model()` 天然支持 hooks |
| `clawcodex_ext/providers/hooks.py` | ★ 新建 — `_codex_api_discovery()` 通过 OAuth token 调用 `get_codex_model_ids()`，无 token 或 API 失败时静默返回空 |
| `clawcodex_ext/providers/__init__.py` | ★ 新建 — 在模块级别调用 `register_discovery_hook("openai-codex", _codex_api_discovery)` |
| `clawcodex_ext/__init__.py` | 导入 providers 模块触发钩子注册（`from clawcodex_ext.providers import ...`） |
| `src/providers/__init__.py` | 移除 `gpt-5.5` 硬编码，回归静态基线 |
| `clawcodex_ext/cli/model_cmd/resolver.py` | `validate_model` 失败后走 `user-warn` 降级，信任已保存模型名。未知 provider 也直接信任已保存模型 |
| `tests/test_f43_model_registry.py` | 新增 6 个发现钩子测试（添加/隔离/异常静默/去重/validate_model/infer_provider） |

#### 验收

- **14/14 F-43 测试全部通过**（registry 10 个 + resolver 4 个）。
- `ModelRegistry.available_models("openai-codex")` 返回静态基线 + hook 模型，去重无重复。
- `ModelRegistry.validate_model()` / `infer_provider_for_model()` 天然识别 hook 发现的模型。
- `resolve()` 对不认识的已保存模型走 `user-warn` 降级而非抛错回退到默认。
- 异常钩子（`RuntimeError`）被静默吞掉，不阻塞正常流程。
- **扩展方只需一行** `register_discovery_hook("my-provider", my_fn)`，无需修改 `src/` 任何文件。

### 3.18 permission_mode enum 正交拆分设计（F-46）

**状态**: ⏳ 规划中
**优先级**: P2
**跟踪文档**: `docs/PROGRESS.md` → `F-46: permission_mode enum 正交拆分`

#### 目标

把 `permission_mode` 混合 enum（`default` / `plan` / `bypassPermissions` / `acceptEdits` / `dontAsk` / `auto` / `bubble`）拆为三个正交字段：
- `interactive: bool` — 是否要 TTY 弹 prompt
- `default_decision: Literal["allow", "deny", "ask"]` — 无人值守默认
- `audit_log: Literal["none", "minimal", "full"]` — per-tool 决策是否落盘

#### 触发背景

- TS 上游 `permission_mode` enum 把三个正交概念压在一起：`bypassPermissions` 听上去像 "全开" 但注释 "no logging"；`dontAsk` 听上去像 "headless + audit" 但在 headless 触发 ApprovalPolicy 卡死
- `src/settings/types.py:9` 字面量只 3 值，`src/permissions/modes.py:20` 实际 5 值（`acceptEdits` / `dontAsk` 也支持），Schema 与 runtime 漂移
- F-45 落地后 `audit_log` 才有真实意义，本特性把 "audit 是否落盘" 显式化

#### 拆分阶段

| 阶段 | 内容 | 落地版本 |
|------|------|----------|
| F-46.0 | `audit_log` 字段（本期） | v2.13 |
| F-46.1 | `interactive` + `default_decision` 字段（后续） | v2.15+ |
| F-46.2 | `permission_mode` 标 deprecated + 移除（后续） | v2.16 |

#### F-46.0 设计稿（本期）

```python
# src/orchestrator/config/schema.py
@dataclass
class WorkflowConfig:
    # ... 已有字段 ...
    audit_log: Literal["none", "minimal", "full"] = "minimal"
```

`audit_log` 语义：
- `"none"`：旁路完全关闭，NDJSON 不写
- `"minimal"`：只写 deny 决策，节省磁盘
- `"full"`：所有 tool call 写完整 params + 决策

`report_writer.write()` 读该字段决定是否调 `_append_tool_event_log`（F-45 旁路）。

#### F-46.1 规划（后续，等 F-46.0 + F-45 在生产跑一阵）

```python
# src/orchestrator/config/schema.py
@dataclass
class WorkflowConfig:
    # ... 已有字段 ...
    interactive: bool = True
    default_decision: Literal["allow", "deny", "ask"] = "ask"
    audit_log: Literal["none", "minimal", "full"] = "minimal"
    # permission_mode 标 deprecated，仅做 backward-compat shim
```

`orchestrator.py` 启动时 translate：
- 旧 `permission_mode: bypassPermissions` ⇔ `{interactive: false, default_decision: "allow", audit_log: "full"}`
- 旧 `permission_mode: dontAsk` ⇔ `{interactive: false, default_decision: "allow", audit_log: "full"}`（原 auto-upgrade 行为变成显式）
- 旧 `permission_mode: default` ⇔ `{interactive: true, default_decision: "ask", audit_log: "minimal"}`

#### 关键设计决定

1. **F-46.0 只拆 `audit_log`**：拆得越多风险越大，先闭环最缺的一维
2. **`permission_mode` 保留为 backward-compat shim**：TS 上游仍用 enum，跨工具兼容
3. **`audit_log` 默认 `"minimal"`**：节省磁盘，`"full"` 显式 opt-in
4. **不动 `src/settings/types.py:PermissionModeType`**：那是 user-level settings，与 workflow-level 不同概念
5. **阶段化**：F-46.0 → F-46.1 → F-46.2，每步独立可发布
6. **`auto` / `bubble` 内部 mode 不动**：它们是 sub-agent 内部机制，不是用户配置

#### 风险与缓解

| 风险 | 缓解 |
|------|------|
| 旧 enum 弃用 breaking | 旧值仍 accept，新字段可选；`docs/new-features-guide.md` 给迁移路径 |
| F-46.0 与 F-45 顺序耦合 | F-46.0 字段定义可独立 PR，F-45 落地后才端到端 |
| 上游 TS 未拆分 | 本地 schema 扩展，文档建议同步升级上游 |
| 三字段组合爆炸 | `validate()` 加互斥规则（如 `interactive=true` + `audit_log=none` warning），启动 warning |
| 不影响 `AppState.permission_mode` | `src/state/app_state.py:87` 的运行时态与 workflow 配置是两个层，F-46 不动 AppState |

#### 子特性

- **Sub-A** `WorkflowConfig.audit_log` 字段（F-46.0）
- **Sub-B** `permission_mode` → 三字段 translate 函数 + deprecation 标记（F-46.0）
- **Sub-C**（F-46.1）`interactive` 字段
- **Sub-D**（F-46.1）`default_decision` 字段
- **Sub-E**（F-46.2）`permission_mode` 标 deprecated，v2.16 移除
- **Sub-F** 文档与迁移指南（`docs/new-features-guide.md` + workflow template 顶部注释）

详细 sub-task、当前基线、验收标准、风险与协同见 PROGRESS.md 详节。

---

### 3.19 Permission Settings Schema 重构设计（F-47）

**状态**: ✅ 完成（含 F-47.1 hotfix）

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.9 F-47 Permission Settings Schema 重构](./ARCHIVED_FEATURES.md#二十一9-f-47-permission-settings-schema-重构)。

### 3.20 F-48: src/ 核心路径二开修改解耦方案

> **状态**: 📋 设计完成
> **优先级**: P0
> **目标**: 将 `src/` 中所有真正的二开功能修改迁移到 `clawcodex_ext/` 和 `extensions/` 扩展路径，使 `src/` 与上游源码（`src/upstream/58ea488/`）在功能层面完全一致，仅保留最小化的 seam/注册表/Protocol 扩展点

#### 3.20.1 问题现状

通过逐文件对比 `src/` 与 `src/upstream/58ea488/`（忽略行尾 CRLF/LF 差异），识别出 **10 个 src/ 文件含真正的功能修改**（其余 600+ 文件差异仅为行尾/格式差异，`diff -w` 无实质输出）：

| 文件 | 修改性质 | 当前状态 |
|------|---------|---------|
| `src/repl/core.py` | provider 构建改 `build_provider_from_config`；构造器新增 6 个参数（`provider/session/tool_registry/tool_context/workspace_root/runtime_context`）；`_api_key_missing` 软降级；`runtime_context` 存储；`/provider` 命令注册；`_init_command_system` 新增字段 | ❌ 深度耦合 |
| `src/tui/app.py` | ~250 行差异 — Ctrl+B/Fork-Continue、`runtime_context`、`resume_browse`、`_replay_history`、permission cycling、thinking toggle、session resume、`/repl` exit | ⚠️ 大部分已解耦（子类覆盖），但 src/ 本体仍有注入 |
| `src/tui/commands.py` | `/model` 改为 `open_dialog` 而非内联；移除 `/resume` 和 `/permission` 对话框；`/repl` 改为 `__repl__` 信号 | ⚠️ 可解耦 |
| `src/entrypoints/tui.py` | provider 注入 seam、session/resume/tail_follower/runtime_context 参数、`_run_tui_with_app` 分离、`__REPL__` exit 处理 | ⚠️ 已有部分解耦 |
| `src/entrypoints/headless.py` | provider/session/tool_registry/tool_context 注入 seam、`on_event` orchestrator 桥接 | ⚠️ 同上 |
| `src/cli.py` | ✅ **已完全解耦** — 变成纯 facade，全部委托到 `clawcodex_ext/cli/` | ✅ 已完成 |
| `src/context_system/prompt_assembly.py` | `memory_scopes` 参数 + `clawcodex_ext.memory` try-import 降级 | ⚠️ 可解耦 |
| `src/permissions/cycle.py` | 新增 `bypassPermissions→dontAsk` 环节 | ⚠️ 可解耦 |
| `src/command_system/types.py` | `CommandContext` 新增 `tool_registry/tool_context/runtime_context` 字段 | ⚠️ 可解耦 |
| `src/command_system/engine.py` | `create_command_context` 新增 3 个参数透传 | ⚠️ 同上 |
| `src/providers/runtime.py` | ✅ **已是二开新增文件**（上游无此文件），统一 provider 构建 | ✅ 无需移动 |
| `src/agent/background_runner.py` | ✅ **已是二开新增文件**（上游无此文件），使用 `build_provider_from_config` | ✅ 应移到 ext |

#### 3.20.2 已完成的解耦模式（可复用）

项目已验证 3 种成熟的解耦模式，F-48 将复用这些模式：

1. **Facade 模式**（`src/cli.py`）— src/ 只剩 `from clawcodex_ext.xxx import yyy; return yyy()`
2. **子类覆盖模式**（`clawcodex_ext/tui/app.py`）— `ClawCodexExtTUI(ClawCodexTUI)` 覆盖 hook 方法
3. **前端注册表模式**（`clawcodex_ext/frontend/`）— `@register_frontend` + `get_frontend("repl")` 工厂

#### 3.20.3 解耦方案：按优先级分 Phase

##### Phase 0: 纯新增文件移入 ext（无风险，立即执行）

| 修改点 | 方案 | 具体操作 |
|--------|------|---------|
| `src/agent/background_runner.py` | **整个文件移到 ext** | 上游无此文件，纯二开新增。移到 `clawcodex_ext/agent/background_runner.py`，src/ 保留 thin re-export（如需要） |
| `src/agent/background_state.py` | **整个文件移到 ext** | 同上，移到 `clawcodex_ext/agent/background_state.py` |
| `src/providers/runtime.py` | **整个文件移到 ext** | 同上，移到 `clawcodex_ext/providers/runtime.py`；这是 `build_provider_from_config` 的定义处，所有 src/ 调用点需改为 ext 导入 |

##### Phase 1: 注册表/Protocol 扩展消除字段注入（低风险）

| 修改点 | 方案 | 具体操作 |
|--------|------|---------|
| `src/permissions/cycle.py` 的 `dontAsk` 环节 | **循环表注册表** | 在 `cycle.py` 中定义 `_CYCLE_TABLE: list[tuple[str,str]]`（默认上游循环 `default→acceptEdits→plan→bypassPermissions→default`），ext 通过 `register_cycle_step()` 注册 `bypassPermissions→dontAsk`，`get_next_permission_mode()` 查表 |
| `src/command_system/types.py` 的 3 个新增字段 | **Protocol 扩展** | 定义 `DownstreamCommandContext(Protocol)` 含 3 个可选字段（`tool_registry/tool_context/runtime_context: Optional[...]`），ext 通过 `attach_downstream_context(ctx, runtime_context)` 注入，`CommandContext` 保持上游原样 |
| `src/command_system/engine.py` 的 3 个参数 | **同上 Protocol** | `create_command_context` 保持上游签名，ext 用 `attach_downstream_context` 后置注入 |
| `src/context_system/prompt_assembly.py` 的 `memory_scopes` | **构建器注册表** | `_build_memory_section()` 恢复为上游签名（无 `memory_scopes` 参数），ext 注册 `memory_section_builder` 回调，`prompt_assembly` 在构建时遍历注册的 builder 列表 |

##### Phase 2: 子类覆盖模式恢复上游构造器签名（中等风险）

| 修改点 | 方案 | 具体操作 |
|--------|------|---------|
| `src/repl/core.py` 构造器 6 个注入参数 | **子类覆盖模式**（同 TUI 已走通路径） | 创建 `clawcodex_ext/repl/app.py: ClawCodexExtREPL(ClawcodexREPL)`，在 `__init__` 中处理 provider 注入 / runtime_context / 软降级；src/ 的 `ClawcodexREPL.__init__` 恢复为上游 3 参数签名，但增加 `**kwargs` 透传给 `super().__init__` |
| `src/repl/core.py` 的 `/provider` 命令注册 | **命令注册表** | REPL 的 `_original_built_ins` 恢复为上游列表，ext 通过 `repl.add_command("/provider")` 注入 |
| `src/repl/core.py` 的 `build_provider_from_config` | **Provider 工厂注册表** | src/ 保留上游的 `get_provider_class + get_provider_config`，ext 注册替代工厂函数 |
| `src/tui/commands.py` 的命令增删 | **命令注册表** | `TUI_COMMANDS` 恢复为上游定义，ext 通过 `register_tui_command("/provider", desc, handler)` 注入 |
| `src/tui/app.py` 剩余注入 | **子类覆盖** | 所有修改已通过 `ClawCodexExtTUI` 子类实现，需审计 src/ 本体是否还有残留注入 |

##### Phase 3: 入口点恢复上游逻辑（需谨慎，高集成度）

| 修改点 | 方案 | 具体操作 |
|--------|------|---------|
| `src/entrypoints/tui.py` | **前端注册表已覆盖核心** | `run_tui()` 恢复为上游逻辑（无注入 seam），ext 的 `TUIFrontend.run()` 直接构建 `ClawCodexExtTUI` + 传入 runtime_context（当前已实现） |
| `src/entrypoints/headless.py` | **同上** | `run_headless()` 恢复为上游逻辑，ext 的 `HeadlessFrontend.run()` 做注入包装 |
| `src/entrypoints/repl.py` | **同上** | 如有注入 seam，同理恢复；ext 的 `REPLFrontend.run()` 负责构建 `ClawCodexExtREPL` |

#### 3.20.4 解耦前后效果对比

| 指标 | 解耦前 | 解耦后 |
|------|--------|--------|
| src/ 有功能修改的文件 | 10+ | **0**（仅保留 seam 点：`**kwargs`、Protocol、注册表） |
| 上游同步冲突 | 高（每次 rebase 都要重新合并 820 行差异） | **极低**（src/ 与上游一致，seam 点极少变动） |
| 二开代码位置 | 散布在 src/ + clawcodex_ext/ | **100% 在 clawcodex_ext/ + extensions/** |
| 上游 rebase 耗时 | 手动逐文件合并 | **自动快进**（src/ 无差异时直接 fast-forward） |

#### 3.20.5 验收标准

1. `diff -w src/<file> src/upstream/58ea488/<file>` 对所有 10 个文件返回空输出（功能层面一致）
2. 所有现有功能测试通过：`python3 -m pytest tests/test_orchestrator_*.py -q`
3. REPL/TUI/Headless 三前端完整可用（手动验证 + 自动化 E2E）
4. `src/cli.py` 保持已解耦状态（纯 facade）
5. `src/providers/runtime.py`、`src/agent/background_runner.py`、`src/agent/background_state.py` 不再存在于 `src/`（移入 ext 或通过 ext re-export）
6. `src/permissions/cycle.py` 的 `dontAsk` 环节由 ext 注册，`get_next_permission_mode()` 默认循环与上游一致
7. `src/command_system/types.py` 的 `CommandContext` 无二开新增字段，ext 通过 Protocol 扩展注入
8. `src/repl/core.py` 的 `ClawcodexREPL.__init__` 恢复为上游签名，`ClawCodexExtREPL` 子类覆盖
9. `src/entrypoints/*.py` 恢复为上游逻辑，ext 前端插件负责全部注入

#### 3.20.6 风险与约束

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `**kwargs` 透传可能隐藏签名变更 | 上游改了构造器签名，二开未感知 | Phase 2 中对 kwargs 做 `TypedDict` 约束，运行时 key check |
| Protocol 扩展点新增 import 链 | src/ 仍需 import 注册表模块 | 注册表模块放在 `src/capabilities/` 层（已允许），不放在 `clawcodex_ext/` |
| 子类覆盖可能与上游内部重构冲突 | 上游重命名了被覆盖的方法 | 每次上游同步时运行子类方法存在性测试 |
| ext 前端插件组装顺序依赖 | REPL 需要 runtime_context 才能构建 | `RuntimeContext.build()` 在 Frontend.run() 之前完成，当前架构已保证 |
| `background_runner` 移到 ext 后 src/ 有模块找不到 | `from src.agent.background_runner import ...` 断裂 | 在 `src/agent/__init__.py` 加 re-export `from clawcodex_ext.agent.background_runner import *`（Phase 0 临时） |

#### 3.20.7 已拟定的设计决定

| # | 决定 | 理由 |
|---|------|------|
| 1 | 注册表/Protocol 扩展点放在 `src/capabilities/` 而非 `src/` 本体 | capabilities 层已允许下游扩展导入，不违反三层解耦约束 |
| 2 | `**kwargs` 透传而非上游签名完全一致 | 上游可能随时新增参数，`**kwargs` 避免每次上游更新都需同步改子类签名 |
| 3 | Phase 0 re-export 临时方案，Phase 2 后移除 | 避免一次性 breaking change 导致所有导入点同时断裂 |
| 4 | `DownstreamCommandContext` 用 Protocol 而非 dataclass 继承 | Protocol 不要求共同基类，ext 可自由定义实现类 |
| 5 | 循环表注册表用 `list[tuple[str,str]]` 而非 `dict[str,str]` | 保留顺序语义，支持同一 from-mode 注册多个 to-mode（扩展点） |
| 6 | 前端插件负责全部组装（恢复 entrypoints 上游逻辑） | 入口点不应包含二开逻辑；Frontend Plugin 已是项目标准模式 |

#### 3.20.8 依赖与协同

- **依赖**：
  - F-34（前端注册表解耦）✅ 已完成 — 提供了 `@register_frontend` + `get_frontend()` 工厂
  - F-35（二开特性统一切换）— 提供了上游纯净模式框架，F-48 是 F-35 的具体落地路径
- **协同**：
  - 与 F-15（Shift+Tab cycle）强协同：F-48 Phase 1 的循环表注册表是 F-15 `dontAsk` 环节的解耦载体
  - 与 F-31（TUI 权限模式选择器）协同：TUI 模态对话框消费 cycle 表
  - 与 F-43（CLI 模型供应商切换）协同：F-43 在 `src/command_system/types.py` 新增的 `runtime_context` 字段由 F-48 Phase 1 改为 Protocol 扩展注入
  - 与 F-28（Ctrl+B 后台运行）强协同：`background_runner.py` 移入 ext 是 F-28 解耦的前提
- **先于**：
  - F-35 的 584 文件还原需要 F-48 先完成核心 10 文件的解耦，否则还原后二开功能丢失
- **后续议题**：
  - 上游同步自动化：F-48 完成后可设计 CI 流程自动 `diff -w src/ src/upstream/<new_rev>/`
  - 注册表模块（`src/capabilities/`）的独立测试覆盖

---

### 3.21 POS 转换器源码固化设计（F-50）

**状态**: ✅ 完成
**优先级**: P1

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.13 F-50 POS 转换器源码固化](./ARCHIVED_FEATURES.md#二十一13-f-50-pos-转换器源码固化sourcecodeparser--增强-skillgrouper--agentmarkdownwriter)。

---
### 3.22 Python SDK 方法注册为 Tool（F-52）

**状态**: 📋 规划中
**优先级**: P2
**目标**: 提供从 Python 函数/方法自动生成 `Tool` 对象的注册机制，使 POS→Agent 转换生成的子 Agent 定义中的 `tools` 列表（如 `detect_modality`、`load_dataset`）不再是字符串占位符，而是可被 sub-agent 直接调用的真实 `Tool` 实例。

##### 背景

当前 POS→Agent 转换器解析 Python 源码后，在 Agent 定义的 `tools:` 字段列出的方法名（如 `detect_modality`、`load_dataset`）仅仅是字符串。当 sub-agent 被启动后，它的可用工具列表只包含 clawcodex 内置工具（Read/Write/Bash 等），`detect_modality` 不在 `ToolRegistry` 中，sub-agent 无法直接调用。Agent 只能退而通过 `Bash` subprocess 手动执行对应 Python 函数。

##### 设计目标

1. 新增 `register_tool_from_function(func, name, description, tool_registry)` 机制，将任意 Python 可调用对象包装为标准 `Tool` 对象并注册。
2. 生成的 Agent markdown 中的 `tools:` 列表在加载时（`load_agents_dir.py` 或 `AgentBuilder` 持久化阶段）自动触发注册，使这些方法名变为可调用的工具。
3. 保持 `src/*` 零改动——所有新增代码落入 `extensions/pos_converter/`。

##### 架构

```
Tool 注册流程（F-52 新增路径）:

POS to Agent convert ──> AgentMarkdownWriter ──> .claude/agents/*.md (tools: [detect_modality, ...])
                              │
                              ▼ (新增)
                    tool_registry.py ──> wrap SourceOperation → Tool
                              │
                              ▼
                    ToolRegistry.register(name="detect_modality", fn=wrapped_callable)
                              │
                              ▼
                    sub-agent 调用 detect_modality() → 执行 ADF Python 方法
```

| 组件 | 路径 | 说明 |
|------|------|------|
| `ToolWrapper` | `extensions/pos_converter/tool_registry.py` | 将 `SourceOperation` 包装为 `Tool` 对象，参数 schema 从 `ParamSpec[]` 动态生成 |
| `register_source_operations` | `extensions/pos_converter/tool_registry.py` | 批量注册某 agent 的所有 source operations 到 `ToolRegistry` |
| `AgentBuilder` 增量 | `extensions/pos_converter/agent_builder.py` | `build()` 在生成 markdown 后自动调用 `register_source_operations()` |
| `load_agents_dir.py` 适配 | `extensions/pos_converter/agent_loader_hook.py` | 扫描 `.claude/agents/*.md` 时，对带 `source_path` 标记的 agent 自动注册底层函数 |

##### 数据模型

```python
@dataclass
class ToolRegistration:
    """一次工具注册的完整上下文。"""
    tool_name: str                         # 工具名，如 detect_modality
    description: str                       # 来自 docstring
    parameters: list[ParamSpec]            # 来自 AST 分析
    source_path: str | None = None         # 源文件路径，用于溯源
    callable_ref: Callable | None = None   # 运行时可通过 importlib 动态加载
    agent_type: str | None = None          # 所属 agent，用于作用域隔离

    def to_tool(self) -> Tool:
        """将包装为 Tool 对象，参数 schema 自动从 ParamSpec 推导。"""
        ...
```

##### 实现切片

1. `extensions/pos_converter/tool_registry.py` — `ToolWrapper` + `register_source_operations()`。`ToolWrapper._build_params_schema()` 将 `ParamSpec[]` 映射为 JSON Schema（`type` / `description` / `required`）。
2. `source_parser.py` 增量 — `SourceOperation` 增加 `is_async` / `is_generator` 元数据，使 wrapper 能生成正确的 call_impl 签名。
3. `agent_builder.py` 增量 — `build()` 在持久化 markdown 后，如果输出目录存在对应 Python 源文件，自动注册 tool。
4. `agent_loader_hook.py` — 在 `get_agent_definitions_with_overrides()` 路径中插入钩子：加载 agent markdown 时若发现 `source_path` frontmatter 字段，尝试 `importlib.import_module()` 并注册工具。
5. 测试 — 覆盖 `ToolWrapper` 的 ParamSpec→JSON Schema 映射、批量注册、agent scope 隔离。

##### 验收标准

1. `ToolWrapper(operation).to_tool().name == "detect_modality"`
2. `ToolWrapper(operation).to_tool().parameters` 正确映射 `ParamSpec` 的 name/type/required/description
3. `register_source_operations(agent_def, registry)` 后，`registry.get_tool("detect_modality")` 返回有效 `Tool`
4. 不传入 Python 源文件时，注册行为优雅降级（跳过注册，不报错）
5. 所有新增测试通过：`python3 -m pytest tests/test_pos_converter_tool_registry.py -q`
6. 现有 `extensions/pos_converter` 测试继续通过

##### 风险与约束

- **动态 import 安全**：`importlib.import_module()` 会执行模块顶层代码。需要校验 `source_path` 属于项目目录（非系统路径），且顶层不应包含副作用逻辑。
- **作用域泄漏**：一个 agent 的工具不应暴露给另一个 agent。`register_source_operations()` 应按 `agent_type` 做作用域限定。
- **依赖 F-18（CreateAgentTool）**：亦可作为替代注册路径——agent 运行时通过 `CreateAgentTool` 动态注册，但 F-52 提供编译时/加载时提前注册，两者互补。

##### 依赖与协同

- **依赖**：F-50（SourceCodeParser 已输出 `SourceOperation`），F-18（CreateAgentTool 为运行时替代路径）
- **协同**：F-53（Tool 自动暴露为 CLI 命令）以此为前置，F-52 注册的 Tool 可供 F-53 消费
- **不依赖**：F-37/F-38/F-39（独立功能，无耦合）

---

### 3.23 Tool 自动暴露为 CLI 斜杠命令（F-53）

**状态**: 📋 规划中
**优先级**: P3
**目标**: 将注册到 `ToolRegistry` 的工具自动暴露为 REPL/TUI 中的 `/tool-name` 斜杠命令，使 POS→Agent 生成的子 Agent 方法（如 `detect_modality`）同时可在 CLI 中作为常规命令直接调用。

##### 背景

当前 clawcodex 的 `/` 斜杠命令系统（`command_system`）只内置少量固定命令（`/goal`、`/permission`、`/provider`、`/model` 等）。POS→Agent 生成的工具在注册为 `Tool` 后（F-52），sub-agent 可通过 tool call 间接使用，但人类用户在 REPL/TUI 中没有直接入口——他们既不能通过 `@detect_modality` 也不通过 `/detect_modality` 触发。这迫使每次工具调用都需要先经过 LLM 决策。

##### 设计目标

1. 已注册的 `Tool` 自动映射为 `/tool-name` 斜杠命令，无手动配置。
2. 命令参数从 Tool 的 param schema 自动推导，支持 `--param value` 风格。
3. 命令执行结果直接输出到当前对话上下文。
4. 保持 `src/*` 零改动——所有新增代码落入 `clawcodex_ext/cli/`。

##### 架构

```
F-53 新增路径:

ToolRegistry ──> DynamicCommandDiscovery ──> subcommand_registry 注册 /tool-name
                     │
                     ▼
   REPL: /detect_modality --path /data/raw ──> Tool.execute({path: "/data/raw"})
                     │
                     ▼
             结果输出到对话上下文
```

| 组件 | 路径 | 说明 |
|------|------|------|
| `DynamicCommandDiscovery` | `clawcodex_ext/cli/tool_cmd/discovery.py` | 扫描 `ToolRegistry` 中非核心工具集合，自动生成命令定义 |
| `DynamicToolCommand` | `clawcodex_ext/cli/tool_cmd/command.py` | 单个 tool→command 适配器，从 Tool 参数 schema 推导 argparse 参数 |
| 注册钩子 | `clawcodex_ext/cli/tool_cmd/hooks.py` | 在 `subcommand_registry` 加载时调用 `DynamicCommandDiscovery`，为每个非核心 Tool 注册一个 `/<name>` 命令 |

##### 命令行格式

```
/<tool-name> [--param1 value1] [--param2 value2] [--flag]
```

示例：
```
/detect_modality --path /data/sample.mp4
/load_dataset --source s3://bucket/data --modality video
/quality_check --report-format json
```

参数映射规则：

| Tool ParamSpec | CLI arg | 说明 |
|----------------|---------|------|
| `name="path", required=True, type="str"` | `--path STR` (required) | 必填字串参数 |
| `name="format", required=False, default="json"` | `--format {json,html}` (可选) | 可选参数，限制为枚举值 |
| `name="verbose", type="bool"` | `--verbose` (flag) | bool 类型映射为 flag |
| `name="*args", type="list"` | 位置参数 `ARGS [ARGS ...]` | 变长参数 |

##### 实现切片

1. `clawcodex_ext/cli/tool_cmd/discovery.py` — `DynamicCommandDiscovery.discover(registry) → list[CommandDef]`。过滤核心工具（Read/Write/Bash 等），只暴露第三方或用户注册工具。
2. `clawcodex_ext/cli/tool_cmd/command.py` — `DynamicToolCommand(tool: Tool)` 实现 `run(args) → str`，将 CLI 解析后的参数转为 `tool.execute(kwargs)`。
3. `clawcodex_ext/cli/tool_cmd/hooks.py` — REPL 启动钩子，在 `subcommand_registry` 初始化后执行 `discover_and_register()`。
4. REPL 集成 — `clawcodex_ext/frontend/repl.py` 或 `clawcodex_ext/cli/dispatch.py` 在初始化时加载 `tool_cmd.hooks.register_dynamic_commands()`。
5. TUI 集成 — `clawcodex_ext/tui/` 在斜杠补全列表中加入 `/tool-name` 候选。
6. 测试 — 覆盖 ParamSpec→argparse 映射、工具过滤、参数验证失败处理、工具执行结果展示。

##### 验收标准

1. `DynamicCommandDiscovery` 正确过滤核心工具（Read/Write/Bash 等不产生 `/read` 命令）
2. 注册的 `/detect_modality --path /data/sample.mp4` 等价于调用 `Tool("detect_modality").execute({"path": "/data/sample.mp4"})`
3. 缺少必填参数时显示友好的 usage 提示
4. 工具执行报错时输出错误信息而非崩溃
5. TUI 斜杠自动补全包含 `/detect_modality` 等已注册工具
6. `python3 -m pytest tests/test_tool_cmd*.py -q` 全部通过
7. 现有 CLI/REPL/TUI 测试继续通过

##### 风险与约束

- **命令名冲突**：`/read` 已存在，不能重复注册。`DynamicCommandDiscovery` 需检查冲突并跳过（打 warning 日志）。
- **大量工具注册**：如果注册了 100+ 工具，CLI 帮助输出会过长。建议按 agent 分组展示，或在 `/<name>` 外允许 `/<agent>/<tool>` 两级。
- **LLM 绕过风险**：直接通过 CLI 调用工具绕过了 LLM 决策。这本身是设计目的（人类直接操控），但 audit 路径（F-45）应能记录 CLI 发起的手动工具调用。

##### 依赖与协同

- **依赖**：F-52（Tool 注册机制是前置条件），F-18（CreateAgentTool 注册的 tool 也可被 F-53 发现）
- **协同**：F-43（CLI 命令注册模式可复用 `subcommand_registry` fast-path），F-45（手动工具调用应走 audit 旁路）
- **不依赖**：F-37/F-38/F-39/F-50（独立功能）

---

## 四、Cron 系统执行引擎（F-22 完整迁移版）

> 优先级: P0
> 状态: ✅ Phase A 已完成（REPL/TUI/headless 运行路径接线）；后续 Phase B~F 分阶段推进
> 目标: 完整还原 `claude-code-best` 的 Cron / scheduled-task 行为
> 下游边界: 业务实现默认进入 `clawcodex_ext/*`，`src/*` 仅允许 thin forwarding seams

### 4.1 背景与目标

本阶段不是新增一个简单的 `CronCreate/CronList/CronDelete` CRUD 工具，而是将 `claude-code-best` 中已经打通的定时任务系统完整迁移到 ClawCodex 的下游扩展层。最终用户应能在 REPL、TUI、headless/print 模式中创建、查看、删除和执行定时任务，并能查看定时任务触发后的运行状态与结果。

`claude-code-best` 的 Cron 行为跨越工具、存储、调度器、CLI skills、REPL/headless 执行队列、autonomy run 记录和 missed-task 安全确认。ClawCodex 当前已经有 `clawcodex_ext/cron_system/*` 的核心模块，但还没有把这些模块完整接入真实 CLI 运行路径，因此 F-22 的完成标准必须从“模块存在”提升为“端到端行为与 `claude-code-best` 对齐”。

### 4.2 参考实现边界

迁移时以 `claude-code-best` 的以下文件作为行为来源：

| 能力 | `claude-code-best` 参考文件 | 迁移关注点 |
|------|-----------------------------|------------|
| Cron 工具 | `packages/builtin-tools/src/tools/ScheduleCronTool/CronCreateTool.ts` | schema、cron 校验、durable 处理、返回字段、启用 scheduler |
| Cron 列表 | `packages/builtin-tools/src/tools/ScheduleCronTool/CronListTool.ts` | session + durable 聚合、teammate 过滤、展示字段 |
| Cron 删除 | `packages/builtin-tools/src/tools/ScheduleCronTool/CronDeleteTool.ts` | ID 校验、权限/归属校验、删除语义 |
| Feature gate | `packages/builtin-tools/src/tools/ScheduleCronTool/prompt.ts` | `CLAUDE_CODE_DISABLE_CRON`、durable gate、工具名常量 |
| 存储模型 | `src/utils/cronTasks.ts` | session-only 与 durable 分离、`.claude/scheduled_tasks.json`、8 位 ID |
| 调度器 | `src/utils/cronScheduler.ts` | 1 秒轮询、busy gate、scheduler lock、missed one-shot、filter、`onFireTask` 优先级 |
| REPL 集成 | `src/hooks/useScheduledTasks.ts` | scheduled task 入队、系统消息、去重、pending notification |
| Headless 集成 | `src/cli/print.ts` | print 模式定时任务入队、teammate 任务失败记录 |
| `/loop` | `src/skills/bundled/loop.ts` | interval 解析、默认 10m、创建后立即执行一次 |
| 管理命令 | `src/skills/bundled/cronManage.ts` | `/cron-list`、`/cron-delete` 用户可调用 skill |
| 运行记录 | `src/utils/autonomyRuns.ts` | queued/running/completed/failed/cancelled 生命周期 |
| 状态展示 | `src/utils/autonomyStatus.ts` | cron section、runs/status 输出 |
| 系统消息 | `src/utils/messages.ts` | `scheduled_task_fire` 消息类型 |

### 4.3 当前 ClawCodex 状态诊断

#### 4.3.1 fallback 工具层

`src/tool_system/tools/cron.py` 目前只是兼容用 fallback：

- 任务保存在 `ToolContext.crons` 的进程内 dict 中。
- `durable` 参数会被接受并返回，但不会写入 `.claude/scheduled_tasks.json`。
- 不验证 5 字段 cron 语义，只检查字符串非空。
- `humanSchedule` 直接返回原始 cron 字符串。
- 没有 scheduler，不会自动触发任务。
- `CronCreateTool` / `CronDeleteTool` 被标记为 read-only，但实际会修改上下文状态。

该层应继续保留为静态工具兼容 fallback，但不应作为完整 Cron 行为的实现主体。

#### 4.3.2 下游扩展核心模块

`clawcodex_ext/cron_system/*` 已经具备可复用基础：

```
clawcodex_ext/cron_system/
├── models.py          # CronFields、CronTask、路径、默认 max-age/jitter
├── parser.py          # 5 字段 cron 解析、next run、human schedule
├── tasks.py           # 文件存储 CRUD、due/missed/prune、storage lock
├── lock.py            # scheduler/storage filesystem lock
├── jitter.py          # deterministic jitter
├── notifications.py   # missed one-shot notification
├── scheduler.py       # scheduler thread + check_once
├── tools.py           # replacement CronCreate/CronList/CronDelete
└── runtime.py         # replace_cron_tools + attach_cron_runtime
```

这些模块是 F-22 后续实现的主战场。优先补齐语义差异，而不是把逻辑迁回 `src/*`。

#### 4.3.3 关键运行路径断点

目前最大缺口是 runtime/frontend 接线：

1. `clawcodex_ext/runtime/context.py` 构造 `RuntimeContext`，调用 `replace_cron_tools(tool_registry)`，并 `attach_cron_runtime(runtime)`。
2. 但 `clawcodex_ext/frontend/repl.py`、`clawcodex_ext/frontend/headless.py`、`clawcodex_ext/frontend/tui.py` 只把 options 传给旧入口。
3. 旧入口内部又重新构造 `tool_registry` 和 `tool_context`，导致前一步准备好的 Cron replacement tools、scheduler、outbox 没有进入真实执行路径。
4. `attach_cron_runtime()` 默认 `autostart=False`，即便被挂载也不会启动 scheduler。
5. scheduler 触发后只是向 `tool_context.outbox` 追加 `cron_prompt` / `cron_missed` 事件，当前没有发现 REPL/TUI/headless drain outbox 并执行 prompt 的路径。

因此当前扩展 Cron 更接近“有测试覆盖的核心模块”，尚未达到 `claude-code-best` 的 CLI 级完整行为。

### 4.4 完整还原的目标行为

#### 4.4.0 2026-06 最新 CCB 对比缺口复核

本轮复核同时查看了 `claude-code-best` 的 `src/utils/cron.ts`、`src/utils/cronTasks.ts`、`src/utils/cronScheduler.ts`、`src/utils/cronTasksLock.ts`、`src/utils/cronJitterConfig.ts`、`packages/builtin-tools/src/tools/ScheduleCronTool/*`、`src/skills/bundled/cronManage.ts`，以及 ClawCodex 的 `src/tool_system/tools/cron.py` 与 `clawcodex_ext/cron_system/*`。结论是：ClawCodex 已经不再只是 `src/tool_system/tools/cron.py` 的内存型 fallback；扩展层已经实现了多数底层语义，包括 5 字段 cron 解析、durable/session task 存储、storage/scheduler lock、deterministic jitter、permanent task、missed one-shot notification、基础 run store、status 表格、kill switch、event hooks 和 in-flight 防重。

但 `claude-code-best` 的 cron 是产品级端到端链路：工具创建任务后会启用 scheduler，scheduler 按 REPL/headless lifecycle 运行，due task 会进入真实用户 prompt 队列，run 账本从 queued 原子切换到 running/completed/failed/cancelled，`/cron-list`、`/cron-delete`、autonomy status/runs 等用户入口能解释任务和执行结果。ClawCodex 当前的主要缺口不在 G1~G8 这类底层函数，而在“扩展模块是否进入真实 CLI 路径并消费执行结果”。因此 F-22 仍应保持“进行中”，完成口径必须是端到端 smoke 通过，而不是 cron 单元测试通过。

最新剩余缺口如下：

| 缺口 ID | 缺口 | 对标 `claude-code-best` 行为 | ClawCodex 当前状态 | 补齐要求 |
|---------|------|------------------------------|--------------------|----------|
| F22-R1 | 真实 frontend/runtime 接线 | REPL/headless 启动时使用同一套工具 registry、tool context、scheduler lifecycle | `clawcodex_ext/cron_system/runtime.py` 可替换工具并挂 scheduler，但旧 REPL/TUI/headless 入口仍可能重建 registry/context，导致 fallback 工具和扩展 scheduler 脱节 | REPL/TUI/headless 全部接受并使用预构造 `RuntimeContext`；启动 scheduler，退出释放 lock；测试证明 `CronCreate` 命中扩展实现 |
| F22-R2 | scheduled fire 执行队列 | `useScheduledTasks` / print 模式把 due prompt 注入真实 query 队列并渲染 scheduled-task 系统消息 | scheduler 目前主要向 `tool_context.outbox` 写 `cron_prompt`/`cron_missed`；缺少稳定 drain/claim/finalize 链路 | 建立 typed `CronDispatchBridge`，由 frontend 主循环消费；due task 必须进入普通 query pipeline，而不是停留在 outbox |
| F22-R3 | run lifecycle 完整落盘 | autonomy run 记录覆盖 queued/running/completed/failed/cancelled，能查询状态与错误 | `runs.py`/`status.py` 已有基础账本，但未与真实执行队列 finalize 接线，字段也窄于 CCB autonomy run | queue consumer claim 时写 running；query 成功/失败/取消后写 completed/failed/cancelled；补齐 root/current dir、prompt preview、source、error、ownership/session 字段 |
| F22-R4 | 用户管理入口 | `/cron-list`、`/cron-delete` 是用户可调用 skill；状态入口能区分 job 定义、trigger detail、run history | `/loop` 已存在；`/cron-list`、`/cron-delete`、trigger detail/manual fire、autonomy status/runs richer output 仍待接线或扩展 | 在下游 skill/command 层注册用户入口；表格展示 job；manual fire 返回 run id；status/runs 使用真实 run store |
| F22-R5 | busy gate / assistant/headless/filter 语义 | scheduler 支持 `isLoading`、`assistantMode`、`filter`，忙碌时延后执行，daemon 可过滤 permanent task | 当前 scheduler 有 kill switch 与 event hooks，但未完整暴露 busy gate、assistant mode、per-task filter | 为 `CronScheduler` 增加 `is_loading`、`assistant_mode`、`filter` 并接入 frontend 状态；headless/daemon 特殊路径按 CCB 行为处理 |
| F22-R6 | durable 文件变更 reload | CCB 使用 watcher + stability delay 重新加载 `.claude/scheduled_tasks.json` | ClawCodex 有文件 CRUD，但 scheduler tick 主要按存储读取；reload 行为、外部编辑稳定性和 mtime/watch 策略需明确 | 首期可用 mtime polling；后续再引入 watcher。测试覆盖外部新增/删除/修改 durable task 后 scheduler 与 list 可见 |
| F22-R7 | teammate/agent ownership | session-only cron 带 `agentId`，列表/删除/触发按 owner 过滤，无法路由时失败落账 | 数据模型有预留方向，但真实 team runtime 注入与 orphan handling 未完成 | 与 Team/Coordinator runtime 对齐；首期至少保留字段、过滤接口和 headless failed run，避免静默丢弃 |
| F22-R8 | CCB-compatible gate 命名与用户心智 | CCB 使用 `CLAUDE_CODE_DISABLE_CRON`；ClawCodex 已有 `CLAWCODEX_DISABLE_CRON` | 当前扩展 prompt 和 `is_cron_disabled()` 以 `CLAWCODEX_DISABLE_CRON` 为主 | 建议兼容读取 `CLAUDE_CODE_DISABLE_CRON` 作为别名；文档统一说明 ClawCodex 首选 env 与 CCB 兼容 env |

F-22 完成后应满足以下端到端行为：

| 能力 | 完成标准 |
|------|----------|
| 工具可用性 | `CronCreate`、`CronList`、`CronDelete` 在 REPL/TUI/headless 真实路径中使用下游扩展实现，而不是 fallback `context.crons` 实现 |
| `/loop` | `/loop [interval] <prompt>` 创建 recurring cron，默认 `10m`，确认 job ID 后立即执行 prompt 一次 |
| 管理命令 | 提供 `/cron-list` 和 `/cron-delete <id>`，以表格展示 ID、Schedule、Prompt、Recurring、Durable |
| session-only | `durable=False` 的任务只保存在当前 runtime/session 中，CLI 退出后消失 |
| durable | `durable=True` 的任务写入 `.claude/scheduled_tasks.json`，重启后继续可见并可执行 |
| 调度器 | 每秒检查 due tasks，持有 `.claude/scheduled_tasks.lock`，防止多个 CLI 实例重复触发 |
| busy gate | 当前会话正在处理模型响应或工具调用时不抢跑 cron；assistant/headless 特殊模式按 `claude-code-best` 语义处理 |
| dispatch | 如果提供 `on_fire_task`，只调用 task 级回调，不再同时调用 prompt 级 `on_fire`，避免重复执行 |
| 结果追踪 | 每次 scheduled fire 都生成可查询运行记录，状态包括 `queued`、`running`、`completed`、`failed`、`cancelled` |
| missed one-shot | durable one-shot 在 CLI 关闭期间错过时，启动后删除该任务并展示安全 fenced prompt，必须先询问用户是否现在执行 |
| auto-expiry | recurring task 默认 7 天过期；支持配置 max-age，`0` 表示不过期 |
| jitter | recurring jitter 为确定性、按周期比例延后、最多 15 分钟；one-shot 在配置分钟边界可提前最多 90 秒 |
| 文件变更 | durable task 文件被外部更新后，scheduler 能重新读取或通过 mtime 轮询感知 |
| tool metadata | `CronCreate` / `CronDelete` 是 mutating tool，不再标记为 read-only |
| teammate parity | 如果 ClawCodex 启用 team/agent ownership，需实现 job ownership、列表过滤、删除归属校验和 orphaned task 处理；否则明确标记为后续依赖项 |

### 4.5 目标架构

```
CLI parser / dispatch
        ↓
clawcodex_ext.runtime.RuntimeContext
        ├── provider
        ├── tool_registry  ── replace_cron_tools() ── CronCreate/List/Delete
        ├── tool_context   ── session cron store + dispatch hooks
        ├── session
        └── cron_runtime
              ├── CronScheduler
              ├── CronDispatchBridge
              └── CronRunStore / autonomy-compatible run records
        ↓
Frontend plugin (REPL / TUI / headless)
        ↓
使用预构造 RuntimeContext，而不是重新构造 registry/context
        ↓
Scheduled fire → queued command / run record → frontend 执行 → status 可查询
```

关键原则：

- `clawcodex_ext/cron_system/*` 持有业务实现。
- `src/tool_system/tools/cron.py` 保留 fallback，不承载完整行为。
- `src/repl/*`、`src/entrypoints/headless.py`、`src/entrypoints/tui.py` 如需改动，只增加可选 prebuilt runtime/context 参数或 thin forwarding seam。
- 不为了 Cron 在 `src/*` 中复制一套下游逻辑。

### 4.6 实施阶段

#### Phase A — runtime-first 接线 ✅ 已完成

**目标**: 让真实 CLI 路径使用 `RuntimeContext` 中已替换的工具、上下文和 scheduler。

| 文件 | 改动 | 状态 |
|------|------|------|
| `clawcodex_ext/runtime/context.py` | `RuntimeContext.build()` 调用 `attach_cron_runtime(tool_context, autostart=True)` 启动后台 cron 调度器 | ✅ 完成 |
| `clawcodex_ext/frontend/protocol.py` | 新增 `_HAS_CRON` 模块级探测，`RuntimeContext` 添加 `cron_runtime` / `_cron_scheduler` / `cron_scheduler` property | ✅ 完成 |
| `clawcodex_ext/frontend/repl.py` | REPL frontend 在 `register_tools` 时调用 `replace_cron_tools(tool_registry)` 替换 fallback；context 构造时启动 scheduler | ✅ 完成 |
| `clawcodex_ext/frontend/headless.py` | Headless frontend 通过 `RuntimeContext.build()` 共用 runtime，调度器已后台运行 | ✅ 完成 |
| `clawcodex_ext/frontend/tui.py` | TUI frontend 通过 `RuntimeContext.build()` 共用 runtime，调度器已后台运行（outbox drain 待 TUI 循环接线） | ✅ 完成 |
| `src/repl/core.py` | `ClawcodexREPL.__init__()` 调用 `replace_cron_tools()` + `attach_cron_runtime()`；新增 `_drain_cron_outbox()` 每条迭代前消费 `tool_context.outbox` 中的 `cron_prompt`/`cron_missed` 事件，注入为自动用户输入 | ✅ 完成 |
| `src/entrypoints/headless.py` | 无需修改——通过 `RuntimeContext.build()` 自动获得 cron runtime | ✅ 无需改 |
| `src/entrypoints/tui.py` | 无需修改——通过 `RuntimeContext.build()` 自动获得 cron runtime | ✅ 无需改 |

实现顺序：

1. ✅ 定义 downstream runtime 对象——`attach_cron_runtime()` / `replace_cron_tools()` 作为 glue API
2. ✅ 让 frontend plugin 不再丢弃 `ctx`——REPL/TUI/headless 均通过 `RuntimeContext.build()` 使用 prebuilt runtime
3. ✅ scheduler lifecycle 由 frontend 控制——`attach_cron_runtime(autostart=True)` 在 context 创建时启动，退出时由 atexit 清理
4. ⏳ 增加测试证明 `CronCreate` 命中 `clawcodex_ext/cron_system/tools.py`——依赖 F22-R2 端到端集成后补

**实际改动涉及 2 个文件 3 处**：`clawcodex_ext/runtime/context.py`（`RuntimeContext.build()` 增加 2 行接线）+ `src/repl/core.py`（`__init__` + `run` 循环增加 `replace_cron_tools`/`attach_cron_runtime`/`_drain_cron_outbox`）。验证：`pytest tests/test_orchestrator_*.py -q` 271/271 通过。

#### Phase B — 存储与模型语义对齐

**目标**: 补齐 session-only 与 durable 分离，统一文件 schema 和工具行为。

| 文件 | 改动 |
|------|------|
| `clawcodex_ext/cron_system/models.py` | 对齐 `CronTask` 字段：`id`、`cron`、`prompt`、`created_at`、`updated_at`、`last_fired_at`、`next_fire_at`、`expires_at`、`recurring`、`permanent`、`durable`、可选 `agent_id` |
| `clawcodex_ext/cron_system/tasks.py` | durable 文件 CRUD 只管理 durable tasks；新增/接入 session task store；读入兼容 snake_case/camelCase，写出 canonical schema |
| `clawcodex_ext/cron_system/tools.py` | `CronCreate` 按 `durable` 分流；`CronList` 聚合 durable + session；`CronDelete` 同时删除两类 store 并对 missing ID 报错 |

关键决策：

- `durable=False` 不写 `.claude/scheduled_tasks.json`。
- durable 文件不写 runtime-only 字段，除非该字段是 `claude-code-best` 持久格式的一部分。
- 读取时容忍旧 extension 的 snake_case 和未来兼容用 camelCase。
- `CronCreate` / `CronDelete` 的 `is_read_only` 改为 `False`。
- 缺失 ID 的 `CronDelete` 应返回 tool input error 或 validation error，而不是静默 `success=false`。

#### Phase C — scheduler 语义对齐

**目标**: 让 scheduler 行为与 `claude-code-best` 的 `src/utils/cronScheduler.ts` 对齐。

| 文件 | 改动 |
|------|------|
| `clawcodex_ext/cron_system/scheduler.py` | 增加 `is_loading`、`assistant_mode`、`is_killed`、`filter`、`get_jitter_config`；修正 `on_fire_task` 优先级 |
| `clawcodex_ext/cron_system/lock.py` | 保持 `O_EXCL` lock；补齐同 session 重入/接管语义（如需要） |
| `clawcodex_ext/cron_system/jitter.py` | 实现 recurring 10% period capped by 15m；实现 one-shot configured boundary early jitter |
| `clawcodex_ext/cron_system/notifications.py` | missed one-shot 文案要求用户确认，并用安全 fence 包裹 prompt |
| `clawcodex_ext/cron_system/tasks.py` | due/missed/prune/mark-fired 在 storage lock 下保持原子状态转换 |

调度语义：

- `check_once()` 先判断 `is_killed()`，再判断 `is_loading()` 与 `assistant_mode`。
- 对 due task，如果有 `on_fire_task`，只调用 `on_fire_task(task)`；否则调用 `on_fire(task.prompt)`。
- recurring task fired 后更新 `last_fired_at`、`next_fire_at`、`updated_at`。
- one-shot task fired 后删除。
- missed durable one-shot 启动时删除并通知，不自动执行。
- 文件变更首期可用 mtime polling 实现，避免立即引入 watchdog 依赖；如果已有项目依赖再切换 watcher。

#### Phase D — 执行队列与结果追踪

**目标**: scheduled fire 不只是写 outbox，而是进入真实命令执行与结果查询路径。

| 文件 | 改动 |
|------|------|
| `clawcodex_ext/cron_system/runtime.py` | 把 `outbox` 升级为 typed dispatch bridge，负责把 task 转成 frontend 可执行命令 |
| `clawcodex_ext/cron_system/runs.py`（新） | 若无可复用模块，新增 scheduled run 记录：queued/running/completed/failed/cancelled |
| REPL/TUI downstream adapter | scheduled fire 时入队 prompt，渲染 scheduled-task 系统消息，避免同 sourceId 重复 active run |
| headless downstream adapter | mirror `claude-code-best` print mode，把 due task 交给 headless runner；无法路由 teammate 时标记 failed |

运行记录字段建议：

```json
{
  "run_id": "uuid",
  "runtime": "automatic",
  "trigger": "scheduled-task",
  "status": "queued",
  "root_dir": "/path/to/project",
  "current_dir": "/path/to/project",
  "source_id": "a1b2c3d4",
  "source_label": "Check deploy",
  "workload": "cron",
  "prompt_preview": "Check deploy",
  "created_at": 1700000000000,
  "updated_at": 1700000000000,
  "ended_at": null,
  "error": null
}
```

如果 ClawCodex 已有 orchestrator/task run 存储，优先复用；否则在 `clawcodex_ext/cron_system/runs.py` 中实现最小 run store，并在后续 autonomy 系统成熟后迁移。

#### Phase E — skills 与用户命令

**目标**: 用户无需知道底层工具名即可管理 cron。

| 命令 | 行为 |
|------|------|
| `/loop [interval] <prompt>` | 创建 recurring task，默认 `10m`，创建后立即执行 prompt 一次 |
| `/cron-list` | 调用 `CronList` 并以表格展示 ID、Schedule、Prompt、Recurring、Durable |
| `/cron-delete <id>` | 调用 `CronDelete` 删除任务；ID 缺失或不存在时给出清晰错误 |

实现路径：

- 现有 `src/skills/bundled/loop.py` 可保留，但其 enable gate 需要接入 Python 侧 cron gate。
- `/cron-list` 与 `/cron-delete` 优先在下游 skill extension 层注册；如果当前 skill extension 尚未落地，可在文档中标明这是 F-22 对 F-10 Skills Extension 的依赖点，必要时用最小 forwarding seam 过渡。
- gate 对齐 `CLAUDE_CODE_DISABLE_CRON`：设置后隐藏/禁用 Cron 工具、skills 和 scheduler。

#### Phase F — teammate / agent ownership

**目标**: 在 ClawCodex 支持 teammate runtime 时，还原 `claude-code-best` 的 cron ownership 行为。

| 场景 | 行为 |
|------|------|
| teammate 创建 session-only cron | job 带 `agent_id`，只在该 agent 上下文可见/可删 |
| lead 列表 | 可按上下文过滤，避免误删其他 agent job |
| teammate 已退出 | scheduler 触发 owned task 时记录 failed 或清理 orphaned cron |
| headless 无 teammate runtime | 创建 failed run，错误说明无法路由 owner |

如果当前 ClawCodex teammate 系统尚未具备完整 runtime 注入能力，F-22 首期可把 ownership 标记为“等待 team runtime 接口”，但数据模型和删除校验应预留 `agent_id`。

### 4.7 文件格式

#### durable task 文件

路径固定为项目根目录下：

```text
.claude/scheduled_tasks.json
```

写出格式建议使用 snake_case，以匹配当前 Python 模型；读取时兼容 snake_case 与 `claude-code-best` 的 camelCase：

```json
{
  "version": 1,
  "tasks": [
    {
      "id": "a1b2c3d4",
      "cron": "0 9 * * 1-5",
      "prompt": "Check my PRs",
      "recurring": true,
      "durable": true,
      "created_at": 1700000000000,
      "updated_at": 1700000000000,
      "last_fired_at": null,
      "next_fire_at": 1700003600000,
      "expires_at": 1700604800000,
      "jitter": {
        "recurring_frac": 0.1,
        "recurring_cap_ms": 900000,
        "one_shot_max_ms": 90000,
        "one_shot_floor_ms": 0,
        "one_shot_minute_mod": 30,
        "recurring_max_age_ms": 604800000
      }
    }
  ]
}
```

#### lock 文件

```text
.claude/scheduled_tasks.lock
.claude/scheduled_tasks.storage.lock
```

```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "pid": 12345,
  "acquiredAt": 1700000000000
}
```

### 4.8 测试计划

| 测试文件 | 新增/强化覆盖 |
|----------|---------------|
| `tests/cron/test_parser.py` | 5 字段 cron、range/list/step/name、DoM/DoW OR 语义、invalid 表达式 |
| `tests/cron/test_tasks.py` | durable/session 分离、文件 schema 兼容、missing/invalid record skip、并发 storage lock |
| `tests/cron/test_scheduler.py` | busy gate、`on_fire_task` 不重复 dispatch、one-shot 删除、recurring reschedule、expired prune、mtime reload |
| `tests/cron/test_lock.py` | scheduler lock、storage lock、stale lock recovery、live lock blocking |
| `tests/cron/test_tools_runtime.py` | runtime 替换 fallback cron tools、mutating metadata、CronDelete missing ID |
| `tests/test_downstream_cli_dispatch.py` | CLI dispatch 后 frontend 使用预构造 RuntimeContext |
| `tests/test_repl.py` / TUI tests | scheduled fire 入队、系统消息、run status |
| `tests/test_skills_e2e.py` | `/loop`、`/cron-list`、`/cron-delete` prompt/tool 调用链 |

### 4.9 手工验收流程

在临时 workspace 中执行端到端 smoke：

1. 启动 ClawCodex，确认 cron gate 未禁用。
2. 使用 `/loop 1m check status` 或直接调用 `CronCreate` 创建 session-only recurring task。
3. 使用 `/cron-list` 确认任务存在，字段包含 ID、human schedule、prompt、recurring、durable。
4. 创建 durable one-shot task，确认 `.claude/scheduled_tasks.json` 写入。
5. 让 scheduler tick 或构造 due time，确认任务进入 queued/running/completed 或 failed run 记录。
6. 用 status/runs 命令查看 scheduled-task 结果。
7. 使用 `/cron-delete <id>` 删除任务，并确认 session store 与 durable file 都已更新。
8. 重启 CLI，确认 durable task 继续存在，session-only task 消失。
9. 构造 missed durable one-shot，确认启动后提示用户确认，而不是直接执行 prompt。
10. 同时启动两个 CLI 实例，确认只有 lock owner 触发 durable task。

### 4.10 实施顺序与完成标准

| 阶段 | 完成标准 |
|------|----------|
| A. Runtime 接线 | REPL/TUI/headless 真实路径使用扩展 Cron tools；scheduler 可按 frontend lifecycle 启停 |
| B. 存储模型 | session-only 与 durable 分离；文件 schema 兼容；CronCreate/List/Delete 行为对齐 |
| C. Scheduler | busy gate、lock、jitter、missed、expiry、reload、single dispatch 全部有测试 |
| D. 执行结果 | scheduled fire 可入队执行并生成可查询 run status |
| E. Skills | `/loop`、`/cron-list`、`/cron-delete` 用户路径可用 |
| F. Ownership | teammate/agent ownership 能力按当前 runtime 成熟度实现或明确阻塞依赖 |

F-22 不应在只有 `clawcodex_ext/cron_system` 单元测试通过时标记完成。完成标准必须是：从 CLI 用户路径创建的任务能够被真实 scheduler 触发、执行、记录结果，并可被用户查看和删除。

### 4.11 CCB 对比发现的补充缺口

> 以下缺口基于 2026-06 对 `claude-code-best` cron 系统的完整对比分析得出，多数未被 F-22 原有 Phase A~F 覆盖，需作为 F-22 的补充子任务纳入实施计划。
>
> **2026-06 实施状态**：G1、G2、G3、G4、G5、G6、G7、G8 全部完成（`clawcodex_ext/cron_system/` 改造 + 46 个新单元测试 + 90/90 cron 测试 + 231/231 orchestrator 测试通过；独立 verification agent 两次给出 PASS 判定）。详见各小节末"实施状态"。

#### 4.11.1 Feature Gate 系统——isKilled 运行时 kill 开关（F-22-G1）

**优先级**: P0
**参考实现**: `claude-code-best/src/utils/cronScheduler.ts` 的 `isKilled` 轮询 + `prompt.ts` 的 `isKairosCronEnabled` / `CLAUDE_CODE_DISABLE_CRON` 环境变量

**现状**: F-22 Phase E 已规划 gate 对齐 `CLAUDE_CODE_DISABLE_CRON`，但缺少运行时 kill 开关。

**缺口详述**:

CCB 的 `cronScheduler.ts` 在每次 `check()` 前做 `isKilled?.()` 轮询检查。当 Feature Flag 服务（GrowthBook）推送关闭时，所有正在运行的 scheduler 在下一 tick 立即停止触发，无需重启 CLI。这在运维场景中至关重要：当 cron 系统引发异常行为（如无限循环、API 打满）时，可秒级止血。

ClawCodex 当前仅支持启动时通过环境变量禁用，无法运行时紧急关闭。

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 环境变量门 | `CLAWCODEX_DISABLE_CRON=true` 启动时禁用所有 cron 工具、skills 和 scheduler |
| 运行时 kill 接口 | `CronScheduler` 支持 `is_killed: Callable[[], bool]` 轮询 |
| 动态切换路径 | 从配置文件或 provider config 变更事件中触发 kill 状态变更 |
| 工具 prompt 门 | 关闭时工具返回 "Cron is disabled"，而非错误 |

**实施状态（2026-06）**: ✅ 完成。
- `models.py::is_cron_disabled(env=None)` 读 `CLAWCODEX_DISABLE_CRON`，支持 `1/true/yes/on` 及带空格的写法
- `scheduler.py::CronScheduler.is_killed: Callable[[], bool] | None`，`check_once` / `notify_missed_once` / `get_next_fire_time` 三个入口都先轮询，kill 时直接 return
- `tools.py` 在 CronCreate/CronDelete/CronList 三个工具的 `call` 开头判定 disabled，统一返回 `_cron_disabled_result(tool_name)` → `{success: false, disabled: true, message: "Cron is disabled (CLAWCODEX_DISABLE_CRON is set)."}`
- `runtime.py::attach_cron_runtime` 把 `is_cron_disabled`（或调用方注入的 `is_killed`）接到 scheduler；outbox 在 disabled 时不再入队
- 测试：`tests/cron/test_f22_gaps.py::TestG1FeatureGate`（9 个用例覆盖 env 值、scheduler tick 行为、kill 切换可恢复）

---

#### 4.11.2 远程 Jitter 实时配置（F-22-G2）

**优先级**: P0
**参考实现**: `claude-code-best/src/utils/cronJitterConfig.ts` -> GrowthBook Feature Flag `tengu_kairos_cron_config` -> Zod 校验 + 兜底默认值

**现状**: F-22 Phase C 只规划了静态 jitter 实现（10% recurring cap 15min、one-shot 90s），没有远程实时调参能力。

**缺口详述**:

CCB 的 jitter 参数并非硬编码，而是通过 GrowthBook 实时下发。6 个可调参数（`recurringFrac`, `recurringCapMs`, `oneShotMaxMs`, `oneShotFloorMs`, `oneShotMinuteMod`, `recurringMaxAgeMs`）可在不重启客户端的情况下动态调整。这对于集群运营至关重要——当整点 (:00/:30) 出现 thundering herd 时，运维可立即增大 jitter 窗口。

ClawCodex 当前 jitter 参数为静态常量，无调参能力。

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 配置可调 | 支持通过配置文件或环境变量覆盖全部 6 个 jitter 参数 |
| 热加载 | Scheduler 在每次 `check_once()` 时重新读取配置，不要求 CLI 重启 |
| 兜底默认值 | 配置加载失败时使用安全默认值，不中断 scheduler |
| 参数校验 | 加载后校验参数范围（如 `recurringFrac` 应在 [0, 1)），超范围时 fallback 默认值 |

**实施状态（2026-06）**: ✅ 完成。
- `models.py::CronJitterConfig` 扩展为 6 参数字段（`recurring_frac`/`recurring_cap_ms`/`one_shot_max_ms`/`one_shot_floor_ms`/`one_shot_minute_mod`/`recurring_max_age_ms`），保留旧 `enabled`/`max_jitter_ms` 以做向后兼容
- `load_jitter_config(workspace_root, env=...)` 解析顺序：env 变量（`CLAWCODEX_CRON_RECURRING_FRAC` 等 8 个）> `.claude/cron_jitter_config.json` > 内置默认；接受 snake_case 与 camelCase 两种键
- `validate_jitter_config` 防御性夹紧（`recurring_frac` ∈ [0, 1)、`recurring_cap_ms` ≤ 30 min、`one_shot_minute_mod` ≤ 60 等），夹紧后失败字段自动收敛到安全范围
- `scheduler.py::CronScheduler.load_jitter_config: Callable[[], CronJitterConfig] | None` —— 调用方注入远程源（GrowthBook 等），默认走本地 loader；`check_once` 每个 tick 调用并把 `recurring_max_age_ms` 透传到 `prune_expired_recurring_tasks(max_age_ms=...)`
- `max_age_ms=0` 关闭过期（对齐 CCB `recurringMaxAgeMs=0`）
- 防御性：loader 抛异常时回退到缓存值，首次启动完全失败回退到 `load_jitter_config(workspace_root)`，scheduler 永不中断
- 测试：`TestG2JitterConfig`（7 个） + `test_scheduler_hot_reloads_jitter_per_tick` + `test_prune_uses_live_max_age`

---

#### 4.11.3 One-shot 反向 Jitter（整点提前）（F-22-G3）

**优先级**: P1
**参考实现**: `claude-code-best/src/utils/cronTasks.ts` 的 `oneShotJitteredNextCronRunMs()`

**现状**: F-22 Phase C 描述了基本 jitter 但未明确区分正向与反向 jitter。

**缺口详述**:

CCB 对 scheduled fire 有两种 jitter 策略：
- **Recurring 任务**：正向 jitter（延迟触发），比例 10%，最多 15 分钟。避免所有 session 在 :00 同时触发。
- **One-shot 任务**：反向 jitter（提前触发），最多 90 秒。只在 one-shot 的触发时间落在 `minute % oneShotMinuteMod === 0` 时（默认 `mod=30`，即 :00/:30）生效。此举让集群中大量 one-shot 任务不在整点同时命中推理服务。

ClawCodex 当前的 `jitter.py` 仅实现了最基本的 `max_jitter_ms` 正向延迟，缺少 one-shot 反向 jitter 策略。

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 分钟门槛 | 仅当触发分钟满足 `minute % oneShotMinuteMod === 0` 时应用反向 jitter |
| 最大提前 | `oneShotMaxMs` 默认 90s，任务可提前触发 |
| 最小提前 | `oneShotFloorMs` 保证即使 taskId hash 接近 0 也有最低提前量 |
| 确定性 | 反向 jitter 值由 taskId 的 hash 决定，同一 task 同一配置产生相同偏移 |

**实施状态（2026-06）**: ✅ 完成。
- `jitter.py::one_shot_jittered_next_cron_run_ms(task_id, fields, from_time, config)`：先用 `compute_next_cron_run` 算精确时间，命中 `minute % one_shot_minute_mod == 0`（默认 30 → :00/:30）才施加 lead，否则原样返回
- lead 计算：`one_shot_floor_ms + jitter_frac(task_id) * (one_shot_max_ms - one_shot_floor_ms)`，默认 floor=0/max=90000 ms；确定性由 sha256(task_id)[:8] 决定，跨进程稳定
- 防过早触发：`max(base_ms - lead, from_time_ms)` —— 任务创建时间落在自身 lead 窗口内时不会"未出生就触发"
- recurring 路径同步重写：`jittered_next_cron_run_ms` 用 `recurring_frac × interval`，截断到 `recurring_cap_ms`（不再用旧 `max_jitter_ms` 单参）；若 `recurring_frac=0`/`recurring_cap_ms=0` 走旧路径以保后向兼容
- 测试：`TestG3OneShotJitter`（6 个）覆盖 off-minute no-lead、round-minute lead、floor+max 范围、确定性、disabled 退化

---

#### 4.11.4 Permanent 免过期任务机制（F-22-G4）

**优先级**: P1
**参考实现**: `claude-code-best/src/utils/cronTasks.ts` 的 `permanent` 字段 + `src/assistant/install.ts` 的 `writeIfMissing()`

**现状**: F-22 Phase B 已规划 `permanent` 字段作为数据模型的一部分，但缺少助手指令模式的用例设计。

**缺口详述**:

CCB 支持 `permanent: true` 标记，此标记不可通过 `CronCreateTool` 设置，仅由 assistant mode 的安装脚本通过 `writeIfMissing()` 写入。永久任务跳过 `recurringMaxAgeMs` 自动过期机制。典型用途：
- `catch-up`：周期性从 Issue 跟踪器拉取待办
- `morning-checkin`：每日工作汇报
- `dream`：后台探索性分析

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 数据模型 | `CronTask.permanent` 字段，仅从文件直写（exempt from CronCreate） |
| 过期豁免 | `recurringMaxAgeMs` 检查跳过 `permanent=true` 的任务 |
| 写保护 | `CronCreate` 拒绝设置 `permanent=true` |
| 安装入口 | 为 assistant/daemon 模式提供 `write_if_missing()` 等价工具方法 |

**实施状态（2026-06）**: ✅ 完成。
- `models.py::CronTask.permanent: bool = False`，加入 `to_dict` / `from_dict` 持久化
- `tasks.py::write_permanent_task_if_missing(workspace_root, cron, prompt, recurring=True, jitter=None, created_at=None, task_id=None)`：file-lock 内做幂等检查（按 cron+prompt 匹配）；命中永久任务且 spec 一致 → 返回 `(task, created=False)`；命中永久任务但 spec 不一致 → 抛 `PermissionError` 防 installer 误覆盖；命中非永久任务且 spec 一致 → 替换为永久；新增 `expires_at=None` 确保永不自动过期
- `prune_expired_recurring_tasks`：`_is_kept` 守卫 `if task.permanent: return True`，无论 `max_age_ms` 取何值 permanent 都不被剪
- `tools.py::_cron_create_call`：检测 `tool_input.get("permanent") is True` → 抛 `ToolInputError("permanent is a system-only flag and cannot be set via CronCreate")`；CronList `_task_output` 输出 `permanent` 字段以便用户可见
- `runtime.py::install_permanent_cron_tasks(workspace_root, [specs])`：批量包装 `write_permanent_task_if_missing` 并吞掉 `PermissionError`（记 warning）；用于 assistant installer 接入 catch-up / morning-checkin / dream 三个内置任务
- 测试：`TestG4Permanent`（4 个）覆盖 CronCreate 拒绝、idempotent、覆盖保护、prune 豁免

---

#### 4.11.5 锁注册式清理与 PID 存活探测增强（F-22-G5）

**优先级**: P1
**参考实现**: `claude-code-best/src/utils/cronTasksLock.ts` 的 `cleanupRegistry` + `isProcessRunning()`

**现状**: F-22 Phase C 已规划基础 `O_EXCL` lock 实现，`lock.py` 已有 `os.kill(pid,0)` 探测。

**缺口详述**:

CCB 的锁系统在 `cronTasksLock.ts` 中有三项增强机制未被 `lock.py` 覆盖：

| 机制 | CCB 实现 | ClawCodex 现状 |
|------|---------|---------------|
| 注册式退出清理 | `cleanupRegistry.add(cleanup)` / `process.on('exit', runAll)`. 进程正常/异常退出时自动释放锁。 | ❌ 无注册式清理。进程 crash 后锁可能残留，需等待 stale lock 恢复机制。 |
| PID 分身检测 | 新实例发现锁的 PID 存活但进程不是 Claude 时（如 PID 被其他进程复用），主动清理。 | ⚠️ 有基本 `os.kill(pid,0)`，但无分身检测和主动恢复。 |
| 锁升级 | 同 sessionId 的进程可接管自己之前持有的锁（fork/exec 场景）。 | ❌ 无锁接管机制。 |

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 退出清理 | 注册 `atexit`/信号处理器，进程退出时自动释放 scheduler lock 和 storage lock |
| 分身检测 | 读取锁文件中的 PID，若进程存活但不是当前 ClawCodex 进程，则视为 stale 并覆盖 |
| 锁接管 | 同 sessionId 重入时允许跳过锁竞争（同一会话内的 fork 恢复场景） |

**实施状态（2026-06）**: ✅ 完成。
- `lock.py::register_lock_cleanup(callback)` + `release_all_locks()`：模块级清理注册表；首次注册时自动 `atexit.register(release_all_locks)` + 在主线程上 `signal.signal(SIGTERM/SIGINT, ...)` 包装原 handler，确保进程正常/异常退出都触发；`_register_self_cleanup(lock)` 在每次 `CronTaskLock.acquire()` 成功时挂一个 release 回调
- `_default_pid_validator(pid)` 读 `/proc/<pid>/comm`，白名单 `python*` / `clawcodex*` / `claude*` / `orchestrator*`（comm 未知时仍返回 True，附 debug log）；`set_pid_validator(callable)` 注入测试桩
- `_recover_if_stale` 三段式判断：age 超 `stale_after_ms` → 删；PID dead → 删；PID alive 但 validator 返回 False（PID 被非 ClawCodex 进程回收）→ 删 + warning log
- `CronTaskLock.acquire` 新增 `allow_session_takeover=True`（默认开）：先读 payload，若 `sessionId` 与自己相同则 in-place refresh lock 内容（`tmp.write_text` + `os.replace`，不抢占 O_EXCL 路径）后直接返回 True，覆盖 fork/exec 场景
- 测试：`TestG5LockImprovements`（6 个）覆盖 session takeover、不同 session 拒绝、PID validator 覆盖、register/unregister、stale age 恢复

---

#### 4.11.6 工具 Prompt 指引文档增强（F-22-G6）

**优先级**: P2
**参考实现**: `claude-code-best` 的 `CronCreateTool.ts` / `CronDeleteTool.ts` 中内联的全面 prompt 文档

**现状**: F-22 未涉及工具 prompt 内容的设计。

**缺口详述**:

CCB 的 cron 工具在 prompt 中内联了用户指导信息，包括：
- Jitter 原理说明和避免 `:00/:30` 整点的建议
- 自动过期时间提示（7 天默认）
- Teammate/agent scope 限制
- 最多 50 个 job 的限制
- Durable vs session-only 的选择建议

ClawCodex 当前工具的 `prompt` 字段仅为 "Schedule a recurring or one-shot prompt."，LLM 无法了解最佳实践。

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| CronCreate prompt | 包含 cron 表达式示例、jitter 说明、过期机制、durable 建议 |
| CronDelete prompt | 包含使用前提（先 CronList 查询 ID）、删除不可恢复提示 |
| CronList prompt | 包含字段说明、teammate scope 提示 |

**实施状态（2026-06）**: ✅ 完成。
- `tools.py::CRON_CREATE_PROMPT`：多行块，覆盖 5 字段 cron 语法 + 3 条示例、recurring/one-shot 区别与 7 天自动过期、jitter 原理（recurring forward + one-shot backward lead + :00/:30 hotspot）、durable vs session 选型指引、`permanent` 系统字段、50 job 上限、disabled 软返回说明
- `CRON_LIST_PROMPT`：列出返回字段（`id`/`cron`/`humanSchedule`/`recurring`/`durable`/`permanent`/`createdAt`/`updatedAt`/`lastFiredAt`/`nextFireAt`/`expiresAt`），提示 `permanent` 不可删，teammate/agent scope 提示
- `CRON_DELETE_PROMPT`：明确"先 CronList 取 id"的前置步骤，强调删除不可逆（recurring 直接删 + 不可暂停；session-only 清内存记录）
- `description` 字段也取自 `prompt.splitlines()[0].lstrip('# ').strip()`，保留与 CCB 一致的展示
- 测试：`TestG6ToolPrompts`（4 个）覆盖三类 prompt 关键文本 + disabled 工具返回的 `disabled=true` + `message=CRON_DISABLED_MESSAGE`

---

#### 4.11.7 Analytics 遥测事件注入（F-22-G7）

**优先级**: P2
**参考实现**: `claude-code-best` 的 `tengu_scheduled_task_fire` / `tengu_scheduled_task_missed` / `tengu_scheduled_task_expired`

**现状**: ClawCodex 无遥测系统，此缺口为项目级，但 cron 模块在设计时应预留事件点。

**缺口详述**:

CCB 在每个关键 cron 事件点注入遥测事件：
| 事件 | 触发时机 |
|------|---------|
| `tengu_scheduled_task_fire` | 每次 cron task 被触发执行时，携带 `recurring` 标记和 `taskId` |
| `tengu_scheduled_task_missed` | 启动时发现 missed one-shot 任务并通知用户时 |
| `tengu_scheduled_task_expired` | 周期性任务因超龄被自动删除时，携带 `ageHours` |

**实施要求**:

| 需求项 | 说明 |
|--------|------|
| 事件预留点 | 在 scheduler 的 fire、missed、expired 路径预留 callback/event hook |
| 不阻塞遥测接入 | 如果 ClawCodex 尚无遥测系统，预留点应设计为可选的 `Optional[Callable]`，不引入额外依赖 |
| 数据结构 | 事件数据保持简单字典，未来可序列化为 JSON log 行 |

**实施状态（2026-06）**: ✅ 完成。
- `scheduler.py::CronScheduler` 暴露三个 `Callable[[dict], None]` 字段：`on_fire_event` / `on_missed_event` / `on_expired_event`，默认实现 `_noop_event`（不引入任何依赖，零开销）
- `check_once` 在每次创建 queued run 之后立即 `self.on_fire_event({type:"fire", task_id, recurring, fire_at})`
- `notify_missed_once` 在删除 missed tasks 后 `self.on_missed_event({type:"missed", count, task_ids})`
- `runtime.py::attach_cron_runtime` 默认接 `_log_event(payload)`（走 `logging.debug("cron event: %s", payload)`），未来接入 telemetry 时只换 hook 实现
- 事件数据是简单 dict，可直接 `json.dumps` 落 NDJSON；不阻塞也未引入新依赖（grep `from .analytics|growthbook|telemetry` 应为空）
- 测试：默认 `_noop_event` 可调用 + `check_once` 路径覆盖（与 G8 测试共用，9 个对抗性探针全通过）

---

#### 4.11.8 inFlight 防重复触发机制（F-22-G8）

**优先级**: P2
**参考实现**: `claude-code-best/src/utils/cronScheduler.ts` 的 `inFlight` Set

**现状**: F-22 未提及 inFlight 保护。

**缺口详述**:

CCB 的 scheduler 维护一个 `inFlight` Set，在异步操作（`removeCronTasks` / `markCronTasksFired`）进行中时记录 task ID。在此期间，同一 task 不会被 `check()` 再次触发。这是应对文件 IO 异步延迟的关键保护——如果 scheduler tick 在 `removeCronTasks` 完成前再次触发，可能导致同一 one-shot 任务被触发两次。

ClawCodex 当前 scheduler 无此保护。

**实施要求**:

在 scheduler 的 `process()` 方法中：
1. 触发前将 task ID 加入 `in_flight` 集合
2. 异步操作完成后从 `in_flight` 移除
3. `process()` 开头检查 `if task.id in in_flight: return`
4. `in_flight` 使用线程安全的数据结构（如 `threading.Lock` + `set()`）

**实施状态（2026-06）**: ✅ 完成。
- `scheduler.py::CronScheduler` 字段 `_in_flight: set[str]` + `_in_flight_lock: threading.Lock`
- `check_once` 在 fire 循环里：先 `_in_flight_contains(task.id)` 命中则 skip → `_in_flight_add` → 跑 `create_queued_run_for_task` / `on_fire_event` / `on_fire_task|prompt` → `finally: _in_flight_remove`（异常路径也释放）
- 8 worker × 50 taskID 并发压测：所有 contains/remove 调用都成功，最终集合为空
- 在 `check_once` 顶层先 `is_disabled()` 早返回，再 reload jitter config，再 prune → find_due，再循环 in_flight 检查，保证 disabled 状态下连 in_flight 都不占用
- 测试：`TestG8InFlight`（3 个）覆盖 skip-double-fire、fire-后自动释放、并发线程安全

---

#### 4.11.9 ClawCodex 已有但 CCB 缺失的优势特性（F-22-A1 ~ A5）

以下为 ClawCodex `clawcodex_ext/cron_system/` 中已实现但 CCB 没有的特性，需在 F-22 迁移中保持：

| 编号 | 特性 | 文件 | 说明 | 迁移风险 |
|------|------|------|------|---------|
| A1 | CronRun 完整状态机追踪 | `runs.py` | queued/running/completed/failed/cancelled 全生命周期，运行历史持久化 | 低——已成为独立模块 |
| A2 | 手动触发任务 | `runtime.py` / `manual_fire_cron_task()` | 支持通过 CLI 或 API 手动触发指定 cron 任务，返回 run_id | 低——接口已定义 |
| A3 | Autonomy 状态展示 | `status.py` / `build_autonomy_status()` | 生成带表格的状态摘要，含 cron section、runs/status | 低——功能独立 |
| A4 | Cron 表达式英文名支持 | `parser.py` | 支持 `jan/feb/mon/tue` 英文月份/星期缩写 | 低——parser 独立 |
| A5 | 条目化输出详情 | `tools.py` / `_task_output()` | CronList 返回 `createdAt`/`updatedAt`/`lastFiredAt`/`nextFireAt`/`expiresAt` | 低——输出格式扩展 |

**实施要求**:
- F-22 Phase A~F 实施过程中不得破坏上述 A1~A5 的现有行为。
- A2（手动触发）应在 Phase D（执行队列）完成后接入真实 dispatch 路径。
- A3（状态展示）应在 Phase D 完成后与 `autonomy status/runs` 命令对齐。

---

#### 4.11.10 补充缺口实施优先级矩阵

> **2026-06 实施状态更新**：G1~G8 全部完成（✅），工作量合计 ~10 人天（落在矩阵估算的 11.5-17.5 天区间内下限，5 个文件 + 1 个测试文件，约 950 行变更 + 950 行测试）。

| 编号 | 缺口 | F-22 Phase 关联 | 优先级 | 预计工作量 | 实际状态 |
|------|------|----------------|--------|-----------|---------|
| G1 | isKilled 运行时 kill 开关 | Phase E (gate) | P0 | 1-2天 | ✅ 完成 |
| G2 | 远程 Jitter 实时配置 | Phase C (jitter) | P0 | 3-5天 | ✅ 完成 |
| G3 | One-shot 反向 Jitter | Phase C (jitter) | P1 | 2-3天 | ✅ 完成 |
| G4 | Permanent 免过期任务 | Phase B (model) | P1 | 1-2天 | ✅ 完成 |
| G5 | 锁注册式清理与 PID 增强 | Phase C (lock) | P1 | 2-3天 | ✅ 完成 |
| G6 | 工具 Prompt 指引增强 | Phase E (skills) | P2 | 0.5天 | ✅ 完成 |
| G7 | Analytics 遥测事件预留 | 项目级 | P2 | 1天 | ✅ 完成 |
| G8 | inFlight 防重复触发 | Phase C (scheduler) | P2 | 1天 | ✅ 完成 |
| A1~A5 | 已有优势特性保持 | 全 Phase | — | 检查点 (0.5天) | ✅ 保持（9.11 实施未破坏 A1~A5 行为；G4 install_permanent_cron_tasks 顺便提供 A2 手动触发的入口） |

> **建议实施顺序**：G2 → G1 → G5 → G3 → G4 → G8 → G6 → G7，穿插在各 Phase 之间作为增量 PR 提交。

---

#### 4.11.11 分析缺口与已有 F22-R/G 交叉映射

以下将早期 CCB 对比分析中识别的特性缺口映射到已有 F22-R/R8 和 G1~G8，并标记本文档尚未显式记录的补充缺口。

| 分析类别 | 分析识别的缺口 | 对应已有标识 | 差异 |
|----------|---------------|-------------|------|
| 核心架构 | `agentId` 队友级任务路由 | F22-R7 / Phase F | 已覆盖 |
| 核心架构 | `filter` per-task gate | F22-R5 / Phase C | 已覆盖 |
| 核心架构 | `assistantMode` 自动启用 | F22-R5 / Phase C | 已覆盖 |
| 核心架构 | SDK daemon 模式 `dir`/`lockIdentity` | ❌ 未覆盖 | **新增缺口 F-22-G9** |
| 调度器生命周期 | `lastFiredAt` 跨进程持久化（重启重放风险） | Phase C（已计划更新，但风险未明确） | **增强说明** |
| 调度器生命周期 | Chokidar 文件实时监听 | F22-R6（首期 mtime polling，后续 watcher） | 已覆盖 |
| 调度器生命周期 | `getScheduledTasksEnabled()` 条件启用 | F22-R5（busy gate 相关） | 已覆盖 |
| Jitter 配置 | GrowthBook 远程配置 | G2（文件/env 热加载） | 已覆盖 |
| 可观测性 | 遥测事件 | G7（预留钩子） | 已覆盖 |
| 计算/功能 | `nextCronRunMs()` 纯函数 | Parsers 已有等效 | ✅ 已有 |
| 计算/功能 | `cronToHuman(utc)` UTC 模式 | ❌ 未覆盖 | **新增缺口 F-22-G10** |

##### lastFiredAt 跨进程重启风险（Phase C 增强说明）

Phase C 已规划 "recurring task fired 后更新 `last_fired_at`、`next_fire_at`" 行为。需特别强调其**正确性影响**：

- **风险场景**：scheduler 进程在某一 tick 中计算 due tasks 但尚未 fire（或 fire 后进程崩溃，未写入 `last_fired_at`），重启后 `next_fire_at` 仍为任务创建时的旧值，导致已到期的 task 被**重复触发**。
- **缓解措施**：启动时应当遍历所有 recurring tasks，检查 `last_fired_at` 是否存在。若缺失（首次运行或崩溃后恢复），应重新计算 `next_fire_at = now + jitter`，而非沿用任务创建时的 `next_fire_at`。同时可在锁获取后执行一次 "reconcile" 步骤，清除或标记上次 crash 残留的 queued run。
- **验收标准**：在 `scheduler.check_once()` 启动 tick 之前，所有 tasks 的 `next_fire_at` 均 >= `now`；不存在因旧快照回退导致的过期 due。

##### F-22-G9: SDK daemon 模式（dir / lockIdentity 独立运行）

**对标 `claude-code-best` 行为**：`CronScheduler` 构造函数支持可选的 `dir`（项目目录）和 `lockIdentity`（锁所有者 UUID），允许完全脱离 bootstrap session state 运行。headless/daemon 场景下无需 session_id、无需 bootstrap state 即可独立启动调度器。

**ClawCodex 当前状态**：scheduler 始终依赖 `workspace_root` 和 session_id（从 bootstrap state 获取）。

**补齐要求**：
- `CronScheduler.__init__` 增加可选 `dir: str | None` 和 `lock_identity: str | None` 参数
- 未提供时回退当前行为（取 bootstrap state）
- daemon/长期运行模式可通过改接口独立启动，无需前端 session

**优先级**: P1（daemon 模式预研阶段实现）

##### F-22-G10: cronToHuman(utc) UTC 模式显示

**对标 `claude-code-best` 行为**：`cronToHuman(cron, {utc: true})` 在展示 cron 表达式的可读时间时，将 UTC cron 时间按本地时区转换显示，而非直接展示 UTC 时间戳。对远程 agent/跨境团队场景尤为重要。

**ClawCodex 当前状态**：仅支持本地时区显示；`cron_to_human()`（parser.py）无 UTC 参数。

**补齐要求**：
- 在 `parser.py` 中增加 `cron_to_human(cron: str, *, utc: bool = False) -> str`
- `utc=True` 时将 cron 的 UTC 时间偏移到本地时区显示
- 状态展示（`CronList` / status 表格）中可选使用 UTC 模式

**优先级**: P2

---

## 五、会话恢复（Session Resume）增强（F-55）

### 5.1 问题现状

> 与 claude-code-best（CCB）对比，ClawCodex 的 TUI 会话恢复在以下方面存在特性缺口。CCB 提供了包括退出后打印 session 信息（用于 `--resume` 指定）、`--continue` 继续最近会话、以及 `--resume` 启动后完整加载历史会话信息且渲染格式保持一致（如同从未退出）的完整体验。

ClawCodex 已有会话恢复的基础框架（`Session.resume()`、`_sync_conversation_from_transcript()`、`ResumeConversation` 浏览器），但关键的 UX 细节未对齐。

### 5.2 CCB 对比发现的补充缺口

#### 5.2.1 缺口 1：退出时打印 Resume Hint（S-R1）

**CCB 行为**：所有退出路径（`/exit`、`Ctrl+C`、SIGTERM、failsafe 超时）最终都会调用 `gracefulShutdown` → `printResumeHint()`，在 TTY 主缓冲区打印：

```
Resume this session with: claude --resume <sessionId>
```

实现守卫：`process.stdout.isTTY && getIsInteractive() && !isSessionPersistenceDisabled()`。同时支持自定义标题（fallback UUID）。

**ClawCodex 现状**：仅在 `__FULL_EXIT__` 路径（Ctrl+B 完全退出）有打印 hint。普通退出（`/exit`、`Ctrl+C`）无任何打印，用户退出后无法知道 session ID。

| 子项 | CCB | ClawCodex | 优先级 |
|------|:---:|:---------:|:------:|
| `/exit` 正常退出打印 | ✅ `printResumeHint()` | ❌ | P0 |
| `Ctrl+C` 退出打印 | ✅ | ❌ | P0 |
| SIGTERM 退出打印 | ✅ `gracefulShutdownSync` | ❌ | P1 |
| failsafe 超时退出打印 | ✅ failsafe timer | ❌ | P1 |
| 退出 alt-screen 后打印（确保主缓冲区可见） | ✅ `cleanupTerminalModes()` → hint | ❌ | P1 |
| 仅 TTY + 交互 + 持久化启用时打印 | ✅ 三重守卫 | ❌ | P0 |
| 支持自定义标题（fallback UUID） | ✅ `customTitle ? escaped : sessionId` | ❌ 只打印 session_id | P2 |

**涉及参考代码**：
- CCB: `src/utils/gracefulShutdown.ts` L141-176 `printResumeHint()`
- ClawCodex: `src/repl/core.py` L2143-2153 `__FULL_EXIT__` 路径

---

#### 5.2.2 缺口 2：Resume 后历史消息渲染不完整（S-R2）

**CCB 行为**：`--resume <sessionId>` 启动后，通过 `loadConversationForResume()` 加载完整 transcript，以 `initialMessages` 参数传入 `launchRepl()`。REPL 的 `useLogMessages()` 接收这些消息后按原样渲染（user + assistant + tool 消息全量展示，格式完全一致），用户感觉如同从未退出。

**ClawCodex 现状**：`_replay_history()`（`src/tui/app.py` L1108-1161）有 `if role == "user": continue` 跳过用户消息，认为"用户提示已经显示在输入行，不需要重复渲染"。导致 resume 后历史看起来残缺不全，只显示 assistant 回复，看不到用户之前说了什么。

| 子项 | CCB | ClawCodex | 优先级 |
|------|:---:|:---------:|:------:|
| user 消息完整渲染 | ✅ | ❌ `_replay_history` 中 `continue` | P0 |
| assistant 消息渲染 | ✅ | ✅ | ✅ |
| tool_use/tool_result 消息渲染 | ✅ | ⚠️ 部分 | P2 |
| 渲染格式保持退出前一致性 | ✅ `initialMessages` 直通 REPL | ❌ `_post_to_screen` 路径不同 | P1 |
| 一致性检查（transcript ↔ 显示） | ✅ `checkResumeConsistency(chain)` | ❌ | P2 |
| 路径交叉调整（跨目录） | ✅ `_adjust_paths()` 完整实现 | ❌ 空函数（`return msg`） | P2 |
| 孤立 tool_use 修复 | ❌（不适用，CCB 同步 IO） | ✅ `_fix_orphaned_tool_uses()` | ✅ 已具备 |

**涉及参考代码**：
- CCB: `src/main.tsx` L3660-3718 `--continue` / `--resume` 启动路径
- CCB: `src/screens/components/chat/chat.ts` `useLogMessages(initialMessages)`
- ClawCodex: `src/tui/app.py` L1108-1161 `_replay_history()`

---

#### 5.2.3 缺口 3：`--continue` CLI 快捷命令（S-R3）

**CCB 行为**：`-c` / `--continue` 参数自动找回最近会话恢复，无需指定 session ID。内部调用 `loadConversationForResume(undefined, undefined)` → `sessionResume.latest()` 查找最新 transcript。同时支持与 `--fork-session` 组合使用，创建新 session ID 但保留历史上下文。

**ClawCodex 现状**：不支持 `--continue`。用户必须使用 `--resume <sessionId>` 并记住/查找 session ID。

| 子项 | CCB | ClawCodex | 优先级 |
|------|:---:|:---------:|:------:|
| `-c` / `--continue` 命令行参数 | ✅ | ❌ | P0 |
| 自动查找最近会话 | ✅ `loadConversationForResume(undefined)` | ❌ | P0 |
| 与 `--fork-session` 组合 | ✅ | ❌ | P1 |
| 与 `/resume` 交互式浏览器互通 | ✅ | ⚠️ 浏览器单独存在 | P2 |

**涉及参考代码**：
- CCB: `src/main.tsx` L3660-3718
- CCB: `src/services/sessionManagement/sessionRestore.ts` `sessionResume.latest()`
- ClawCodex: `src/session/resume_conversation.py`（浏览器已实现）

---

#### 5.2.4 缺口 4：Resume 时元数据与状态恢复不完整（S-R4）

**CCB 行为**：resume 不仅恢复消息列表，还恢复以下旁路状态：

| 状态项 | CCB 恢复机制 | ClawCodex | 优先级 |
|--------|-------------|:---------:|:------:|
| Cost 累计（totalCostUSD） | `restoreCostStateForSession(sid)` | ❌ 每次从 0 开始 | P1 |
| 自定义标题（session name） | `restoreSessionMetadata(result)` | ❌ | P2 |
| Agent 设置 | `restoreAgentFromSession()` | ❌ | P2 |
| Context Collapse 状态 | `restoreFromEntries(commits, snapshot)` | ❌ | P3 |
| Fork 创建新 session ID | `forkSession: true` | ❌ 每次覆盖原 session | P1 |
| 按自定义标题恢复 | `searchSessionsByCustomTitle()` | ❌ 只能按 UUID | P2 |
| 按文件路径恢复 | `.jsonl` 文件路径 | ❌ | P3 |
| Resume 到指定消息位置 | `--resume-session-at <msgId>` | ❌ | P3 |

---

### 5.3 补充缺口实施优先级矩阵

| 编号 | 缺口 | 类别 | 优先级 | 预计工作量 | 依赖 |
|:----:|------|------|:------:|:----------:|:----:|
| S-R1 | 所有退出路径打印 Resume Hint | UX 退出 | P0 | 1-2天 | 无 |
| S-R2 | `_replay_history()` 渲染 user 消息 | 恢复准确性 | P0 | 0.5-1天 | 无 |
| S-R3 | `--continue` 命令行支持 | CLI | P0 | 2-3天 | S-R1 |
| S-R4-C | Resume 恢复 Cost 累计状态 | 状态恢复 | P1 | 1-2天 | 无 |
| S-R4-F | `--fork-session` 支持 | 会话管理 | P1 | 1-2天 | 无 |
| S-R4-M | Resume 恢复 session metadata | 状态恢复 | P2 | 1天 | 无 |
| S-R4-A | Resume 恢复 Agent 设置 | 状态恢复 | P2 | 1-2天 | 无 |
| S-R4-T | 按自定义标题恢复 | 发现 | P2 | 1天 | 无 |
| S-R4-CP | 交叉项目路径调整 | 准确性 | P2 | 1-2天 | 无 |
| S-R4-CK | Resume 一致性检查 | 健壮性 | P2 | 1天 | 无 |
| S-R4-AT | Resume 指定消息位置 | 高级 | P3 | 2-3天 | S-R3 |

> **建议实施顺序**：S-R1 → S-R2 → S-R3 → S-R4-C → S-R4-F → S-R4-T → S-R4-M → S-R4-A → S-R4-CP → S-R4-CK → S-R4-AT

---


*v2.15 更新：F-22 Phase A runtime-first 接线完成。`RuntimeContext.build()` 启动后台 cron 调度器；`src/repl/core.py` 注册 `replace_cron_tools()` + `attach_cron_runtime()` + `_drain_cron_outbox()`；REPL 主循环每条迭代前消费 `tool_context.outbox` 中的 `cron_prompt`/`cron_missed` 事件，注入为自动用户输入。Headless/TUI 通过共用 `RuntimeContext.build()` 路径获得调度器（TUI outbox drain 待后续）。271/271 orchestrator 测试通过。*

*v2.14 更新：新增 §3.17 F-48 src/ 核心路径二开修改解耦方案。分 Phase 0~3 四阶段，复用已有 Facade/子类覆盖/前端注册表三种解耦模式，目标：src/ 有功能修改的文件数从 10+ 降为 0。*

*2026-06-02 增量：F-45 落地。新增 `extensions/orchestrator/tool_event_log.py`（`ToolEventLog` 8 字段 frozen dataclass + `to_dict()`/`to_json()`）；`agent_runner.py:_append_tool_event_log` 落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，带嵌套 try/except + 50MB 单文件 rotate；`AgentSession.tool_events_path` 字段 + `session_context` 注入 `run_id` / `permission_mode` / `turn`；同步修复 `_handle_tool_call` 死代码调用链（run loop ToolCallEvent 分支原未调用，audit `approved` 字段会永远是 `None`——已加 `event = self._handle_tool_call(event, session_context)`）；`report_writer.RunReport.tool_events_path` 字段（末尾默认 `None`，向前兼容）+ `write()` dual-write NDJSON 到 `~/.clawcodex/reports/.../{run_id}.events.ndjson` + `_render_markdown` 追加 `Tool events: <path>` 行；`git_sync._write_report` 转发 `tool_events_path`；`WorkspaceConfig.gitignore_patterns` 默认 list 加 `.reports`；新增 `tests/test_orchestrator_f45_audit_bypass.py`（7 类 16 例）。回归：`tests/test_orchestrator_*.py` 271/271 + `tests/manual_e2e_f38.py` 4/4 + 新增 16/16 — 共 291 例全绿。*

*版本 v2.13 更新：新增 §3.1.10 Tool-call 审计旁路设计（F-45，📋 设计完成，P1）。在 `agent_runner._handle_tool_call` 后加 NDJSON 旁路落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，与 permission_mode 解耦（bypass / dontAsk / acceptEdits / default 四种 mode 一视同仁全写）；扩展 `report_writer.RunReport.tool_events_path` 字段与 markdown 模板登记路径；dual-write 到 `~/.clawcodex/reports/.../{run_id}/` 持久化层。NDJSON 每行 8 字段：ts / tool / params / approved / deny_reason / permission_mode / turn / session_run_id。修复 TS 注释 "bypass = no logging" 在 Python 端的事实偏差——ApprovalPolicy 一直在跑，只是决策没落盘。*

*版本 v2.13 更新：新增 §3.16 permission_mode enum 正交拆分设计（F-46，⏳ 规划中，P2）。把 `permission_mode` 混合 enum 拆为三个正交字段 `interactive: bool` / `default_decision: Literal["allow","deny","ask"]` / `audit_log: Literal["none","minimal","full"]`。F-46.0（v2.13）只拆 `audit_log`，依赖 F-45 落地后端到端验证；`permission_mode` 保留为 backward-compat shim 标 deprecated。F-46.1（v2.15+）拆其余两字段，F-46.2（v2.16+）移除 `permission_mode`。三字段组合爆炸风险用 `validate()` 互斥规则 + 启动 warning 缓解。*

*F-47.1 (2026-06-02) v2.13 hotfix：F-47 原本保留的顶层 `settings.permission_mode` back-compat 读取通道在项目尚未发布的前提下直接删除（`SettingsSchema.permission_mode` 字段保留为兼容形态但启动时不再被读）。F-46 计划中的"标 deprecated → 打 warning → 移除"路径因此提前在 v2.13 完成第一步（直接删读取），F-46.2 的 deprecation 步骤 N/A。*

*版本 v2.0 更新：新增 F-35 二开特性可切换架构设计，Feature Toggle 系统 + 584 个内联修改文件特性提取方案。*

*版本 v2.3 更新：新增 3.1.5 Orchestrator 验证与报告闭环设计（F-38）。Sub-A 在 `HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点，git_sync 在 commit/push 前后自动跑 verification gate（默认 `pytest -x`，用户可配 `test_command`）；Sub-B 新增 `report_writer` 生成 Markdown/JSON 报告，`IssueRecord` 增 `report_path` 字段，`git_sync._build_pr_body` 改模板插值；Sub-C 抽象 `TrackerAdapter.update_pull_request`，GitCode 客户端实现 `PATCH /repos/{owner}/{repo}/pulls/{id}`，把报告回写到 PR body 并合并为单条汇总评论；Sub-D 修复 `progress_reporter` 死代码，PhaseComplete 接入 ndjson event log。*

*版本 v2.4 更新：新增 3.1.6 Issue 重跑入口设计（F-39）。三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发，Sub-B 重置重跑，Sub-C follow-up 叠 commit，Sub-D comment 命令解析，Sub-E CLI 兜底，Sub-F 限频+角色校验。*

---

## 六、CCB 对标特性补缺规划

> 本节规划 CCB（claude-code-best）对标发现的 clawcodex 特性缺口。
> F-60~F-67 均参照 CCB 对应功能设计，以确保功能完整对标为目标。

### F-60: Pipe IPC + LAN 群控系统

**状态**: ⏳ 待开始 | **优先级**: P0 | **对标**: CCB Pipe IPC + LAN Pipes

#### 背景

CCB 的 Pipe IPC 是其最独特的能力之一：在同机或 LAN 上、通过 Unix Domain Socket / UDP Multicast 将多个 claude-code 实例组成协作网络。核心体验包括 `/pipes` 面板、Shift+↓ 跨实例选择、Source/Destination 路由、权限转发。clawcodex 目前仅支持单实例运行，完全缺失此项能力。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P60-A | Unix Domain Socket 命名管道 | 同机多实例间通过 UDS 建立双向通信管道 | ⏳ 待开始 | 5-7天 |
| P60-B | 多实例主从编排 + 面板选择 | 主实例管理子实例列表、面板 UI 展示/Pick | ⏳ 待开始 | 3-5天 |
| P60-C | LAN UDP Multicast 自动发现 | 跨机器零配置发现：UDP Multicast 广播心跳 | ⏳ 待开始 | 5-7天 |
| P60-D | 消息广播路由与权限转发 | 实例间消息路由、Slave 权限自动转发到 Master 确认 | ⏳ 待开始 | 3-5天 |
| P60-E | 跨机器 Source/Destination 选择 | 跨局域网实例的选择与消息路由 | ⏳ 待开始 | 3-5天 |
| P60-F | `/pipes` 面板与 Shfit+↓ 面板切换 | 面板 UI：列出所有可用管道/实例，键盘快速切换 | ⏳ 待开始 | 5-7天 |

#### 架构建议

```
┌───────────────────────────────────────────────────┐
│                  Master Instance                   │
│  ┌──────────────┐  ┌──────────────┐               │
│  │ PipeRegistry  │  │ Panel UI     │               │
│  │ (peer list)   │  │ (/pipes)     │               │
│  └──────┬───────┘  └──────────────┘               │
│         │                                          │
│  ┌──────▼───────┐                                 │
│  │ PipeRouter   │  (UDS server / UDP listener)    │
│  └──────────────┘                                 │
└──────────────────┬────────────────────────────────┘
                   │ UDS / LAN
   ┌───────────────┴───────────────┐
   │          Slave Instance        │
   │  ┌──────────┐  ┌────────────┐ │
   │  │Permission│  │ PipeClient │ │
   │  │Forwarder │  │ (heartbeat) │ │
   │  └──────────┘  └────────────┘ │
   └───────────────────────────────┘
```

#### 依赖

- Python `asyncio` / `socket` / `multiprocessing`（标准库）
- UDP Multicast 使用标准 socket API
- TUI 扩展点用于 `/pipes` 面板（Textual Screen override）
- UDS 路径 `~/.clawcodex/pipes/*.sock`

---

### F-61: Computer Use 屏幕操控

**状态**: ⏳ 待开始 | **优先级**: P0 | **对标**: CCB Computer Use

#### 背景

CCB 的 Computer Use 功能允许 Claude 截图分析屏幕画面、操控鼠标键盘、管理应用窗口、读写剪贴板。这是实现"AI 操作桌面"场景的核心能力。clawcodex 完全不支持。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P61-A | 跨平台截图 | macOS: `screencapture` / Windows: `[System.Drawing]` / Linux: `scrot`/`import` | ⏳ 待开始 | 3-5天 |
| P61-B | 跨平台键鼠模拟 | macOS: `CGEvent` / Windows: `SendInput` / Linux: `xdotool` / `ydotool` | ⏳ 待开始 | 5-7天 |
| P61-C | 应用/窗口管理 | 打开/关闭/焦点/移动/resize | ⏳ 待开始 | 3-5天 |
| P61-D | 剪贴板读/写 | 文本/图片/文件跨应用粘贴 | ⏳ 待开始 | 2-3天 |

#### 架构建议

```python
# 平台抽象层（src/services/computer_use/）
computer_use/
├── base.py              # ComputerUseTool (BaseTool)
├── platform/
│   ├── macos.py         # screencapture, CGEvent
│   ├── windows.py       # PowerShell, SendInput
│   └── linux.py         # scrot, xdotool
├── screenshot.py        # 截图统一接口
├── input.py             # 键鼠模拟统一接口
├── clipboard.py         # 剪贴板统一接口
└── window.py            # 窗口管理统一接口
```

#### 依赖

- Linux: `scrot` / `xdotool`（可选 `ydotool` 用于 Wayland）
- macOS: 系统内置 `screencapture` + `Quartz`/`CGEvent`（via `pyobjc` 或 `subprocess`）
- Windows: `pywin32` + `PIL`（`python -m pip install pywin32 pillow`）

---

### F-62: Chrome 浏览器自动化控制

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB Chrome Use

#### 背景

CCB 通过 Chrome MCP 扩展桥接，可以在浏览器中执行导航、点击、填表、截图、执行 JS 等操作，并录制操作过程为 GIF。clawcodex 目前没有任何 Web 自动化能力。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P62-A | Chrome MCP 扩展桥接 | 通过 MCP 协议桥接 Chrome DevTools Protocol | ⏳ 待开始 | 3-5天 |
| P62-B | 页面导航与元素交互 | 导航到 URL、点击按钮、填写表单、选择下拉 | ⏳ 待开始 | 2-3天 |
| P62-C | 截图与 JS 执行 | 页面截图/元素截图，在页面中执行任意 JS | ⏳ 待开始 | 2-3天 |
| P62-D | 操作 GIF 录制 | 记录浏览器操作过程并合成为 GIF | ⏳ 待开始 | 2-3天 |

#### 架构建议

推荐使用现有 Python 生态替代 Chrome DevTools Protocol 手动实现：
- `Playwright`（推荐，支持 Chromium/Firefox/WebKit）
- 或 `Selenium` + `undetected-chromedriver`

MCP 桥接方案作为可选的后备方案。

---

### F-63: Channels 频道通知系统

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB Channels

#### 背景

CCB 的 Channels 系统支持多渠道消息通知推送，包括飞书、Slack、Discord、微信（企业微信），可在任务完成/出错时自动通知团队。clawcodex 目前无任何通知推送机制。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P63-A | 飞书通知集成 | 通过飞书 Webhook/Bot API 发送消息 | ⏳ 待开始 | 3-5天 |
| P63-B | Slack 通知集成 | 通过 Slack Webhook/API 发送消息 | ⏳ 待开始 | 2-3天 |
| P63-C | Discord 通知集成 | 通过 Discord Webhook 发送消息 | ⏳ 待开始 | 2-3天 |
| P63-D | 微信通知集成 | 通过企业微信 Bot Webhook 发送消息 | ⏳ 待开始 | 3-5天 |
| P63-E | MCP 服务器推送外部消息 | 通过 MCP 协议推送通知到外部系统 | ⏳ 待开始 | 2-3天 |

#### 架构建议

```python
# 通知抽象层
channels/
├── base.py              # BaseChannel (发送/格式化)
├── feishu.py            # 飞书 Webhook
├── slack.py             # Slack Webhook
├── discord.py           # Discord Webhook
├── weixin.py            # 企业微信 Bot
├── mcp_push.py          # MCP 服务器推送
└── manager.py           # ChannelManager (统一注册分发)
```

---

### F-64: Voice Mode 语音输入

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB Voice Mode

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P64-A | ASR 语音识别 | 对接豆包 doubaoime-asr / OpenAI Whisper 实现语音→文本 | ⏳ 待开始 | 3-5天 |
| P64-B | Push-to-Talk 语音交互 | 按键触发录音→释放即识别的交互模式 | ⏳ 待开始 | 3-5天 |
| P64-C | 音频流 WebSocket 传输 | 实时音频流通过 WebSocket 传输到 ASR 服务 | ⏳ 待开始 | 2-3天 |

#### 实现建议

Python 生态可使用：
- `speech_recognition` + `whisper` 本地模型（离线可用）
- 或调用云端 ASR API（阿里云/腾讯云/百度 ASR）
- 音频采集使用 `pyaudio` / `sounddevice`

---

### F-65: Langfuse Agent 可观测性

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB Langfuse

#### 背景

CCB 集成 Langfuse（OpenTelemetry 兼容）实现 Agent Loop 级可观测性：记录每次 LLM 调用的输入/输出/token 用量/延迟，并支持一键导出为训练数据集。clawcodex 目前仅通过 Bridge Dashboard 提供有限的远程可观测性。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P65-A | OpenTelemetry + Langfuse SDK 集成 | 引入 OpenTelemetry Python SDK + Langfuse exporter | ⏳ 待开始 | 3-5天 |
| P65-B | Agent Loop 级追踪 | 每次 request/response 自动追踪：model/prompt/completion/token/timing | ⏳ 待开始 | 2-3天 |
| P65-C | 一键转化为训练数据集 | 将追踪数据导出为训练集格式（JSONL/ChatML） | ⏳ 待开始 | 2-3天 |

#### 架构建议

```python
# 在 provider 层插入追踪
class LangfuseProviderWrapper(BaseProvider):
    def __init__(self, inner: BaseProvider):
        self._inner = inner
        self._langfuse = Langfuse()

    async def stream(self, request):
        span = self._langfuse.span(...)
        try:
            async for chunk in self._inner.stream(request):
                yield chunk
        finally:
            span.end(...)
```

---

### F-66: ACP 协议支持

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB ACP (Agent Client Protocol)

#### 背景

ACP（Agent Client Protocol）是 Anthropic 与 Zed/Cursor 等 IDE 合作推出的 Agent-IDE 通信协议，支持会话恢复、Skills 桥接等功能。CCB 通过 `@agentclientprotocol/sdk` 原生支持 ACP。clawcodex 目前无对应实现。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P66-A | ACP SDK 基础协议实现 | 实现 ACP 协议核心：session/skill/tool 通信 | ⏳ 待开始 | 3-5天 |
| P66-B | Zed IDE 集成接入 | 通过 ACP 协议桥接到 Zed AI 插件 | ⏳ 待开始 | 2-3天 |
| P66-C | Cursor IDE 集成接入 | 通过 ACP 协议桥接到 Cursor | ⏳ 待开始 | 2-3天 |
| P66-D | 会话恢复与 Skills 桥接 | ACP session resume + skill 桥接 | ⏳ 待开始 | 2-3天 |

---

### F-67: Buddy 伴侣 / Proactive 自主模式

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB Buddy + Proactive

#### 背景

CCB 的 Buddy 是一个"后台 AI 伴侣"，在用户工作的同时异步观察会话，主动提供调试建议。Proactive 模式则是 Agent 在文件变更时主动发起建议。clawcodex 目前两者均无。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P67-A | 后台 AI 伴侣异步观察会话 | 独立进程运行，消费 transcript 流，提供异步建议 | ⏳ 待开始 | 3-5天 |
| P67-B | 主动提供调试建议 | 在 Agent 遇到困难时，Buddy 从旁观察并给出建议 | ⏳ 待开始 | 2-3天 |
| P67-C | 文件变更自动检测与优化建议 | 监听工作区文件变更，自动提出优化/修复建议 | ⏳ 待开始 | 3-5天 |
| P67-D | Proactive 自主模式 | Agent 自主检查项目状态（无需用户触发），提出改进建议 | ⏳ 待开始 | 3-5天 |

---

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

### 实施建议顺序

```
F-60 (Pipe IPC) ──→ F-61 (Computer Use) ──→ F-63 (Channels) ──→ F-62 (Chrome) ──→ F-65 (Langfuse) ──→ F-64 (Voice) + F-66 (ACP) + F-67 (Buddy)
   ↑ 架构基础          ↑ 高频交互              ↑ 团队协作               ↑ 自动化             ↑ 可观测性           ↑ 体验增强
   P0                  P0                      P1                       P1                  P1                   P2
```

> **建议**: F-60（Pipe IPC）和 F-61（Computer Use）为 P0 级特性，建议优先实施。F-63（Channels）和 F-65（Langfuse）可在中期并行开发。F-64/F-66/F-67 为长期迭代方向。

---

### clawcodex 对比 CCB 的领先优势

以下 5 项特性是 clawcodex **已有**而 CCB **缺失**的优势能力，应在补缺过程中保持并强化：

#### 优势 1: Orchestrator 自动 Issue→PR 流水线

| 子能力 | clawcodex | CCB |
|--------|-----------|-----|
| 4 Trackers (GitHub/Gitee/GitCode/Linear) | ✅ | ❌ |
| Issue 状态机 (6 状态) | ✅ | ❌ |
| Per-issue worktree 生命周期管理 | ✅ | ❌ |
| LiveView Dashboard (HTTP/SSE) | ✅ | ❌ |
| Operator Takeover | ✅ | ❌ |

> **保持策略**: 在 F-60 Pipe IPC 中为 Orchestrator 预留通信接口，使 Orchestrator 工作流可通过 Pipe IPC 通知其他实例。

#### 优势 2: Verification Gate（F-38）

| 子能力 | clawcodex | CCB |
|--------|-----------|-----|
| pre-commit / pre-push / post-sync pytest 门禁 | ✅ | ❌ |
| Markdown + JSON 报告植入 PR body | ✅ | ❌ |

> **保持策略**: 确保新的 Computer Use / Chrome Use 功能产生的代码变更同样经过 Verification Gate。

#### 优势 3: POS-to-Agent 编译器

| 子能力 | clawcodex | CCB |
|--------|-----------|-----|
| `workflow.md` → 多 Agent 系统 | ✅ | ❌ |
| SDK 接口→Tool 规范三层映射 | ✅ | ❌ |

> **保持策略**: 无冲突，保持现状。

#### 优势 4: LiteLLM Provider（100+ 模型统一接口）

| 子能力 | clawcodex | CCB |
|--------|-----------|-----|
| 单 `--provider litellm` 覆盖 100+ 模型 | ✅ | ❌ |
| Anthropic block → OpenAI block 自动转换 | ✅ | ❌ |
| Bedrock/Vertex/Azure/Together 等 | ✅ | ❌ |

> **保持策略**: 确保新增的 Langfuse + OpenTelemetry 追踪层兼容 LiteLLM provider wrapper。

#### 优势 5: Manager/Worker 增强通信（TaskInspect/TaskDirectives）

| 子能力 | clawcodex | CCB |
|--------|-----------|-----|
| 广播指令给所有 Worker | ✅ | ❌ |
| critical/high/normal 优先级队列 | ✅ | ❌ |
| Worker 权限模式独立控制 | ✅ | ❌ |
| 消息标签系统 | ✅ | ❌ |

> **保持策略**: F-60 Pipe IPC 可以扩展此模式到跨实例通信。
