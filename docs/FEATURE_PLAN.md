# ClawCodex 特性规划与设计文档

> 文档路径: `docs/FEATURE_PLAN.md`
> 版本: v3.0（目录重构版）
> 更新日期: 2026-06 | 上游同步: 58ea488 (dev-decoupling-refactor)
> 
> **v3.0 变更（目录重构）**：从目录视角合并同类项，原 10 章压缩为 **7 章 + 附录**。已完成特性降级为一行注记；F-40 被割裂的设计稿归入所属子节；§9(CCB 对标)与§10(Python 生态补缺)合并为单章并按子领域分组；跨章节重复概念去重。本文件保留 v2.17 所有内容，仅做结构重组。

---

## 目录

- [项目概述与边界约束](#项目概述与边界约束)
    - [项目定位](#项目定位)
    - [当前架构（三层解耦）](#当前架构)
    - [项目级二开边界约束](#项目级二开边界约束)
- [已归档功能模块](#已归档功能模块)
- [一、Orchestrator 系统](#一、orchestrator-系统)
    - [1.1.1 LocalTracker（F-36 ✅）](#1-1-1-localtracker)
    - [1.1.2 PR 检视意见自动修复闭环（F-37 📋）](#1-1-2-pr-检视意见自动修复闭环)
    - [1.1.3 验证与报告闭环（F-38 ✅）](#1-1-3-验证与报告闭环)
    - [1.1.4 Issue 重跑入口（F-39 ✅）](#1-1-4-issue-重跑入口)
    - [1.2.1 Shared/Sequential Workspace（F-42 ✅）](#1-2-1-shared-sequential-workspace)
    - [1.2.2 ProgressReporter Sink 重构（F-40 📋）](#1-2-2-progressreporter-sink-重构)
    - [1.3.1 AgentRunner 空转检测（F-51 ✅）](#1-3-1-agentrunner-空转检测)
    - [1.3.2 运行期可观测性与 stuck-run debug（F-54 📋）](#1-3-2-运行期可观测性与-stuck-run-debug)
    - [1.3.3 Tool-call 审计旁路（F-45 ✅）](#1-3-3-tool-call-审计旁路)
    - [1.3.4 Coordinator 轻量工具集（F-41 ✅）](#1-3-4-coordinator-轻量工具集)
    - [1.4.2 Issue 会话统一存储与实时介入（F-49 📋）](#1-4-2-issue-会话统一存储与实时介入)
- [二、Agent 核心能力](#二、agent-核心能力)
    - [2.1 Agent 阶段性进度汇报（F-20 ✅）](#2-1-agent-阶段性进度汇报)
    - [2.2 Team 成员管理（F-2 📋）](#2-2-team-成员管理)
    - [2.3 结构化输出增强（F-4）](#2-3-结构化输出增强)
    - [2.4 MCP 扩展功能（F-3 ✅）](#2-4-mcp-扩展功能)
    - [2.5 Agent 记忆作用域隔离（F-13 ✅）](#2-5-agent-记忆作用域隔离)
    - [2.6 /goal 命令（目标管理）（F-9）](#2-6-goal-命令目标管理)
    - [2.7 ExecuteExtraTool 延迟工具系统（F-10）](#2-7-executeextratool-延迟工具系统)
    - [2.8 工具/Skill 调用统计（F-75）](#2-8-工具skill-调用统计)
    - [2.9 CreateAgentTool 动态工具创建（F-18）](#2-9-createagenttool-动态工具创建)
    - [2.10 sessionStorage 容量限制（F-11）](#2-10-sessionstorage-容量限制)
    - [2.11 cacheWarning 容量限制（F-12）](#2-11-cachewarning-容量限制)
    - [2.12 Issue 语义澄清流程（F-78）](#2-12-issue-语义澄清流程)
    - [2.13 Auto 模式（F-16）](#2-13-auto-模式)
    - [2.14 Agent 间自主观察与消息交互（F-80）](#2-14-agent-间自主观察与消息交互)
- [三、CLI 与配置系统](#三、cli-与配置系统)
    - [3.1 CLI 模型供应商与模型切换（F-43 ✅）](#3-1-cli-模型供应商与模型切换设计)
    - [3.2 permission_mode 正交拆分（F-46 📋）](#3-2-permission-mode-enum-正交拆分设计)
    - [3.3 Permission Settings 重构（F-47 ✅）](#3-3-permission-settings-schema-重构设计)
- [四、Architecture & SDK 下沉](#四、architecture-sdk-下沉)
    - [4.1 src/ 核心路径解耦（F-48 📋）](#4-1-f-48-src-核心路径二开修改解耦方案)
    - [4.2 SOP 转换器固化（F-50）](#4-2-sop-转换器源码固化设计)
    - [4.2.1 分组策略增强（F-55）](#4-2-1-sop-转换器分组策略增强设计)
    - [4.3 SDK 方法→Tool（F-52）](#4-3-python-sdk-方法注册为-tool)
    - [4.4 Tool→CLI 命令映射（F-53）](#4-4-tool-自动暴露为-cli-斜杠命令)
- [五、Cron 系统执行引擎](#五、cron-系统执行引擎)
    - [5.1 背景与目标](#5-1-背景与目标)
    - [5.2 参考实现边界](#5-2-参考实现边界)
    - [5.3 当前状态诊断](#5-3-当前-clawcodex-状态诊断)
    - [5.4 完整还原的目标行为](#5-4-完整还原的目标行为)
    - [5.5 目标架构](#5-5-目标架构)
    - [5.6 实施阶段](#5-6-实施阶段)
    - [5.7 文件格式](#5-7-文件格式)
    - [5.8 测试计划](#5-8-测试计划)
    - [5.9 手工验收流程](#5-9-手工验收流程)
    - [5.10 实施顺序与完成标准](#5-10-实施顺序与完成标准)
    - [5.11 CCB 对比补充缺口](#5-11-ccb-对比发现的补充缺口)
- [六、会话恢复增强](#六、会话恢复增强)
    - [6.1 问题现状](#6-1-问题现状)
    - [6.2 CCB 对比补充缺口](#6-2-ccb-对比发现的补充缺口)
    - [6.3 补充缺口实施优先级矩阵](#6-3-补充缺口实施优先级矩阵)
- [七、CCB 对标缺口补缺](#七、ccb-对标缺口补缺)
    - [7.0 Python 生态特性补缺](#7-0-python-生态特性补缺规划合并来源原-十)
    - [7.1 进程间通信](#7-1-进程间通信)
    - [7.2 浏览器与桌面操控](#7-2-浏览器与桌面操控)
    - [7.3 通知与语音](#7-3-通知与语音)
    - [7.4 可观测性与协议](#7-4-可观测性与协议)
    - [7.5 高级 Agent 模式](#7-5-高级-agent-模式)
    - [7.6 模板系统](#7-6-模板系统)
- [附录：F-Number 快速索引](#附录-f-number-快速索引)

---

## 项目概述与边界约束

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

---

## 已归档功能模块

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
> - §十九 SOP 转化模式
> - §二十 Skills System Extension（F-23）

---

---

---

## 一、Orchestrator 系统


### Orchestrator 系统概述
**状态**: ✅ 完成（Symphony 集成）
**目标**: 支持 `clawcodex --workflow WORKFLOW.md` 自主运行模式

> 核心组件、生产强化（F-1.1~F-1.4）、Issue 语义澄清三通道（F-1.5~F-1.11）、Orchestrator CLI 运维界面（F-1.13）等子特性全部已归档。
> 详细架构、组件清单、配置形态与命令清单见 [ARCHIVED_FEATURES.md §16](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成)。
>
> 仍处规划/设计阶段、保留详细设计稿的子节如下：
> - §1.1.2 PR 检视意见自动修复闭环设计（F-37，📋 规划中）
> - §1.2.2 ProgressReporter Sink 协议重构设计（F-40，📋 设计完成）
> - §1.3.2 运行期可观测性与 stuck-run debug（F-54，📋 设计完成）
> - 已完成的 LocalTracker（F-36）、验证与报告闭环（F-38）、Issue 重跑入口（F-39）、Coordinator 轻量工具集（F-41）、Shared / Sequential Workspace（F-42）、Tool-call 审计旁路（F-45）、人工检视闸门（F-44）与 AgentRunner 空转检测（F-51）详见 [ARCHIVED_FEATURES.md §二十一](./ARCHIVED_FEATURES.md#二十一2026-06-02-已实现功能归档)。

#### 1.1.1 LocalTracker 本地 Issue 文档源设计（F-36）
**状态**: ✅ 完成

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.1](./ARCHIVED_FEATURES.md#二十一1-f-36-localtracker-本地-issue-文档源)。

#### 1.1.2 PR 检视意见自动修复闭环（F-37）
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

#### 1.1.3 验证与报告闭环（F-38）
**状态**: ✅ 完成

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.2](./ARCHIVED_FEATURES.md#二十一2-f-38-orchestrator-验证与报告闭环)。

#### 1.1.4 Issue 重跑入口（F-39）
**状态**: ✅ 完成（Sub-A~F）

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.3](./ARCHIVED_FEATURES.md#二十一3-f-39-orchestrator-issue-重跑入口)。

#### 1.2.1 Shared/Sequential Workspace（F-42）
**状态**: ✅ 完成

> `workspace.strategy: isolated | shared | sequential` 落地。详见 [ARCHIVED_FEATURES.md §二十一.5](./ARCHIVED_FEATURES.md#二十一5-f-42-sharedsequential-workspace-策略)。

#### 1.2.2 ProgressReporter Sink 重构（F-40）
#### 设计目标

把 `extensions/orchestrator/progress_reporter.py` 从「绑死 `ToolContext` 的单例」重构为「以 `ProgressSink` 协议为最小契约的多消费者可插拔架构」:

1. 每 session 持有独立 sink 实例，状态天然隔离，消除并发竞争。
2. `AgentRunner` 转发全部三类事件 (`PhaseComplete` / `TurnComplete` / `SessionComplete`)，session 结束一定有进度落点。
3. 进度计算改用 `WorkflowConfig.phases` 比例 + LLM 显式覆盖，淘汰 `phase_count * 25` 假数据。
4. 引入 `CompositeProgressSink` 扇出，让 F-37 (PR 检视意见自动修复) / F-39 (Issue 重跑) 后续可零侵入注册专用 sink。
5. 保留 `ProgressReporter` 名字为向后兼容 shim，既有测试与调用方不破。

#### 架构对比

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

#### 关键组件

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

#### 改造点清单

| 文件 | 改动 | Sub |
|------|------|-----|
| `extensions/orchestrator/progress_sink.py` | **新建**：`ProgressSink` 协议 + `CompositeProgressSink` + `ToolContextProgressSink` | A/B |
| `src/orchestrator/config/schema.py` | `WorkflowConfig` 新增 `phases: list[str] = field(default_factory=list)` 字段 | E |
| `extensions/orchestrator/progress_reporter.py` | 改写为兼容 shim；`on_event` 走 `isinstance` 分发；`set_task_id` 创建新 sink；标记 `@deprecated` | F |
| `extensions/orchestrator/agent_runner.py` | 参数 `progress_reporter` → `progress_sink`；`SessionComplete` 分支与 `max_turns` 路径补 `sink.on_session_complete`；若有 `TurnComplete` 分支也补 `sink.on_turn_complete`；`_write_event_log` 行为不变 | C |
| `extensions/orchestrator/orchestrator.py` | 删除 `self._progress_reporter = ProgressReporter(...)`；`_dispatch_issue` / `_run_issue` 中为每个 session 新建 `ToolContextProgressSink` + `CompositeProgressSink`；保留 `_progress_context` 共享 | D |
| `src/tool_system/tools/progress_report.py` | `ProgressReportTool` prompt 增「建议显式传 `progress`」指引；`_progress_report_call` 接受 `progress=None`（已支持） | E |
| `tests/test_orchestrator_agent_runner.py` | 新增并发回归 + 三事件覆盖测试；保留现有 stub（走 `on_event` 老 API 兼容） | G |

#### 进度计算决策表

| 来源 | 触发时机 | `progress` 值 | 优先级 |
|------|----------|---------------|--------|
| LLM 显式调 `ProgressReport` 工具 | LLM 主动汇报 | LLM 传入的 `progress` | 最高 (覆盖一切) |
| `WorkflowConfig.phases` + 自动 `on_phase_complete` | PhaseComplete 事件 | `(current_idx+1) / total * 100` | 中 |
| 兜底（均无） | PhaseComplete 事件 | `None` (UI 显示「未知」) | 最低 |
| `SessionComplete` 终态 | 会话结束 | `100` (reason=success) / `None` (其他) | 终态 |

`workflow.observability.progress.fallback_to_phase_step: bool = True` 时，中间档用 `phase_count * 25` 兜底（软迁移期），后续翻 `False` 强推 None。

#### 并发正确性证明

| 时间 | 事件 | 旧实现（单例） | 新实现（每 session 独立 sink） |
|------|------|----------------|--------------------------------|
| t0 | Issue A 启动 → `set_task_id("A")` | `_current_task_id="A"` | 创建 `SinkA(task_id="A")` |
| t1 | Issue B 启动 → `set_task_id("B")` | `_current_task_id="B"` (覆盖) | 创建 `SinkB(task_id="B")` |
| t2 | A 触发 `PhaseComplete` | 写到 task **B** ❌ | 通过 `SinkA` 写到 task A ✓ |
| t3 | B 触发 `PhaseComplete` | 写到 task **B** ✓ | 通过 `SinkB` 写到 task B ✓ |

`AgentRunner.run` 当前是 `async`，每个 session 跑在独立 task 上；新架构下每个 task 持自己的 sink，无共享可变状态。

#### 验收标准

- 并发跑两个 issue 时，每个 session 的 `ToolContext.tasks[id].metadata.progress_stages` 列表只含本 session 的事件，无串扰。
- `SessionComplete` 触发后，`ToolContext.tasks[id].metadata.current_stage` 含 `session_{reason}`、`metadata.progress` 在 `reason=success` 时为 100、其他情况为 `None`。
- `WorkflowConfig.phases=["analysis", "design", "impl", "test", "review"]` 配置下，完成第 2 个 phase 时 `progress=40`；LLM 显式调 `ProgressReport` 传 `progress=37` 时覆盖自动值。
- `WorkflowConfig.phases` 缺失或为空时，自动 `on_phase_complete` 写 `progress=None`，`StatusDashboard` 显示「Phase N (进度未知)」，而不是误导的 25/50/75/100。
- `ProgressReporter` 类的 `on_event(event, session)` 旧 API 仍可用，内部按 `isinstance(event, PhaseComplete / TurnComplete / SessionComplete)` 分发，现有 stub 测试不修改即可通过。
- `CompositeProgressSink` 内任一 sink 抛异常被独立捕获并 `logger.exception`，不影响其他 sink 接收事件。
- F-37 / F-39 后续接入时，只需在 `Orchestrator._dispatch_issue` 注册额外 sink（`PRReviewAutoFixSink` / `RetryLabelSink`），无需修改 `AgentRunner` 或 `progress_reporter.py`。

#### 风险与约束

- **API 改名 breaking**：`AgentRunner.run` 的 `progress_reporter` kwarg 改 `progress_sink` 是字面量破坏，需同步改 `Orchestrator` 调用方与所有 stub 测试。Mitigation: `ProgressReporter` shim 仍可作为 `progress_sink` 传入（duck type，只要实现三个 `on_*` 方法即可）。
- **进度从假数据变 `None` 的 UI 退化**：默认配置下旧用户从「25/50/75/100」退到「未知」。Mitigation: 加 `workflow.observability.progress.fallback_to_phase_step: bool = True` 配置开关（默认保留旧行为），后续再翻 `False`。
- **每个 session 多一个 sink 对象**：内存增长可忽略（Python 单实例，几 KB），无 perf 风险。
- **事件总线语义变化**：`CompositeProgressSink` 是同步扇出，任意 sink 阻塞会卡住 `AgentRunner` 主循环。Mitigation: 每个 sink 内部 try/except + 短超时；慢消费者应自己 queue + 后台线程。
- **Import 顺序**：`progress_reporter.py` (shim) → `progress_sink.py` (默认实现) → `agent_runner.py` (调用方) 依赖链需保持单向，避免循环 import。建议 `progress_reporter.py` 用 `from .progress_sink import ToolContextProgressSink` 软引用，`TYPE_CHECKING` 保护。

#### 实施阶段

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

#### 依赖与协同

- **依赖 F-1、F-38 Sub-D**：F-38 已把 `ProgressReporter` 接到主流程，本特性在此基础上重构；不破坏 F-38 验收标准（`progress_reporter.ProgressReporter` 在主流程被构造 → 改为 `ToolContextProgressSink` 在主流程被构造）。
- **先于 F-37 落地收益**：F-37 (PR 检视意见自动修复) 后续可注册 `PRReviewAutoFixSink` 监听 `on_session_complete` 触发 follow-up run，无需改 `AgentRunner`。
- **先于 F-39 落地收益**：F-39 (Issue 重跑) 后续可注册 `RetryLabelSink` 监听 `on_session_complete` 更新 issue label，无需改 `AgentRunner`。
- **不破坏 F-36 LocalTracker**：LocalTracker 派发的 session 也走相同的 sink 构造路径，`ToolContextProgressSink` 行为对其等价（数据落 `ToolContext.tasks`，不访问远程）。
- **与 F-22 Cron 系统解耦**：Cron 触发的 prompt 不走 orchestrator，sink 链路不被影响。

---



#### 1.3.1 AgentRunner 空转检测机制（F-51）
**状态**: ✅ 完成

> 内置空转检测逻辑。详见 [ARCHIVED_FEATURES.md §二十一.8](./ARCHIVED_FEATURES.md#二十一8-f-51-agentrunner-空转检测)。

#### 1.3.2 运行期可观测性与 stuck-run debug（F-54）

```
场景：headless agent 在 issue 开发中途陷入迷茫 / operator 想人工介入

1. 触发条件（任一）:
   a) orchestrator 检测到 agent 连续多轮无进展（F-51 空转检测）
   b) operator 通过 dashboard 看到 agent stuck
   c) operator 通过 F-49 Phase 1 的 socket 手动触发 pause

2. Operator 执行:
   $ clawcodex --resume <run_id>

3. 内部流程:
   a) Session.resume(run_id)
      → 读取 ~/.clawcodex/sessions/{run_id}/metadata.json
        (model="claude-sonnet-4-20250514", provider="anthropic", cwd="/workspace")
      → 读取 ~/.clawcodex/sessions/{run_id}/transcript.jsonl
      → 重建完整的 Conversation（UserMessage / AssistantMessage 交替列表）
      → 恢复到前台 REPL，LLM context 与 agent 中断时一致

   b) REPL 启动后显示:
      "Resumed session <run_id> from orchestrator run (issue: F-42-shared-workspace)"
      "Agent was at turn 5/20, last tool: Read(src/config/schema.py)"
      ┌─────────────────────────────────────────────┐
      │ 历史消息回放（最近 3 轮）                       │
      │ ... agent 的思考过程和工具调用结果全部可见 ...    │
      └─────────────────────────────────────────────┘
      

   c) operator 输入:
      > 这个 Read 结果不对，你应该看 src/config/__init__.py 的默认值

      → 这条输入作为新的 UserMessage 写入 transcript.jsonl
      → LLM 继续响应，新输出追加到 transcript
      → operator 可以多轮交互，完全接管 agent 的下一步

   d) 退出 REPL 时:
      - 选择 "detach"（保持 session 打开，headless 可继续）
      - 或 "agent_finished"（标记 run 完成）
      - 或 "re-orchestrate"（退出后自动启动新的 headless run 从当前状态继续）
```

**恢复后的对话完整性保证**：

```
Session.resume() 恢复的 transcript 内容:
┌─ turn 0 ──────────────────────────────────┐
│ UserMessage:    初始 prompt                │
│ AssistantMessage: 思考 + tool_use Read     │
│ UserMessage:    tool_result (文件内容)      │
├─ turn 1 ──────────────────────────────────┤
│ AssistantMessage: 思考 + tool_use Edit     │
│ UserMessage:    tool_result (编辑结果)      │
├─ ...                                      │
├─ turn N ──────────────────────────────────┤
│ AssistantMessage: 思考（stuck 时的最后输出） │
│ UserMessage:    (空 — operator 即将介入)    │
├─ operator 介入 ───────────────────────────┤
│ UserMessage:    "这个 Read 结果不对..."    │ ← 新写入
│ AssistantMessage: 新的 LLM 响应             │ ← 新写入
└───────────────────────────────────────────┘
```

**`--resume` 与正在运行的 headless agent 的并发安全**：

| 场景 | 行为 | 原理 |
|------|------|------|
| agent 已结束 | ✅ 正常恢复，进入交互 REPL | session 无其他人持有 |
| agent 正在运行中 | ✅ 恢复后获得"截至当前的历史快照"，不可写入（readonly），agent 继续运行不受影响 | `transcript.jsonl` 的文件锁 + `SessionStorage` 的 append-only 语义 |
| agent 正在运行 + operator 想接管 | socket 发送 `pause` → agent 挂起 → `--resume` 进入可写 REPL | 依赖 F-49 Phase 1 的 socket 控制通道 |
| 两个 operator 同时 `--resume` | 各自获得独立的历史快照，最后写入者胜 | 同 `SessionStorage` 的常规并发行为 |

**Phase 0.3 — 大内容文件引用**

`SessionStorage._replace_large_content()` 会将大 tool result 自动替换为 `{"type": "ref", "ref_id": "<uuid>"}`，实际内容存入 `~/.clawcodex/sessions/<run_id>/content/<uuid>`。这是 SessionStorage 内置行为，AgentRunner 无需感知。

但需要考虑：

- **workspace 相对路径**：如果 tool result 包含长文件内容（如 `Read` 工具读取了大型文件），文件引用路径不应硬编码为 `~/.clawcodex/` 绝对路径，否则跨机器恢复时路径失效。`metadata.json` 中的 `cwd` 字段用于辅助恢复时进行路径解析。
- **清理策略**：orchestrator 的 cleanup 策略（`retention_days`）与普通 session 一致，F-11（sessionStorage 容量限制）已覆盖此场景。

**Phase 0 改造前后的文件对比**：

```
改造前（当前）：
{workspace}/.event_logs/
  └── {issue_id}.ndjson                    ← 扁平事件，无法 resume

改造后：
~/.clawcodex/sessions/{run_id}.json        ← Session 快照
~/.clawcodex/sessions/{run_id}/
  ├── metadata.json                         ← 元数据
  ├── transcript.jsonl                      ← Message 对话转录（每行一个 Message dict）
  └── content/                              ← 大内容文件引用
{workspace}/.event_logs/
  └── {issue_id}.ndjson                    ← 可选保留（向后兼容），或删除
```

注意：`~/.clawcodex/sessions/{run_id}.json`（Session 快照）不是必选项 —— 它由 `Session.save()` 产生，包含 `conversation`、`cost`、`provider`、`model` 等完整元数据。orchestrator 若只写 `transcript.jsonl`，则 `session_resume.resume_session()` 也可工作（它会从 transcript 重建 message 列表 + 从 metadata 恢复 model/provider）。`Session.save()` 额外提供 `cost` 快照用于 resume 时恢复 token/费用计数，建议保留。

**改造后的事件流数据流向图**：

```
AgentRunner.run()
  │
  ├── run() 开始
  │     ├── SessionStorage(session_id=run_id)
  │     ├── .init_metadata(model, cwd, title)
  │     └── .write_raw(user_prompt_msg_dict)
  │
  ├── 循环 per turn:
  │     ├── 累积 TextDelta → text_buf list
  │     ├── 累积 ToolCallEvent → tool_use_buf list
  │     ├── 累积 ToolResultEvent → tool_result_buf dict[tool_use_id]
  │     │
  │     ├── TurnComplete:
  │     │     ├── 组装 AssistantMessage(text_buf + tool_use_buf)
  │     │     ├── .write_raw(assistant_msg_dict)
  │     │     ├── 组装 UserMessage(tool_result_buf.values())
  │     │     ├── .write_raw(user_msg_dict)
  │     │     ├── 清空 text_buf, tool_use_buf, tool_result_buf
  │     │     └── 若还有下一 turn → .write_raw(continuation_msg_dict)
  │     │
  │     └── F-45 逻辑独立并行：
  │           └── _append_tool_event_log(event)  ← 只写 events.ndjson，不干扰 Message 流
  │
  ├── SessionComplete:
  │     └── .flush()
  │
  └── 异常退出（agent crash / timeout / KeyboardInterrupt）:
        └── .flush()  ← 确保已累积但未 flush 的消息不丢失
```

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

---

#### 1.3.3 Tool-call 审计旁路设计（F-45）
**状态**: ✅ 完成

> 为工具调用增加审计日志旁路。详见 [ARCHIVED_FEATURES.md §二十一.6](./ARCHIVED_FEATURES.md#二十一6-f-45-tool-call-审计旁路)。

#### 1.3.4 Coordinator 轻量工具集（F-41）
**状态**: ✅ 已完成

> Coordinator 配置独立轻量工具集（Read、WebSearch、WebFetch）。详见 [ARCHIVED_FEATURES.md §二十一.4](./ARCHIVED_FEATURES.md#二十一4-f-41-coordinator-轻量工具集)。

#### 1.4.2 Issue 会话统一存储与实时介入协议（F-49）

**状态**: 📋 设计完成

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

**Phase 0.1 — Message 转录映射规则（F-49.0 核心契约）**

Phase 0 只说"用 Message dict 格式写"，但未定义 `QueryEvent` 流 → `Message` dict 的具体映射规则。headless agent 的 `QueryRunner.stream()` 产出的是一系列扁平事件（`TextDelta` / `ToolCallEvent` / `ToolResultEvent`），它们必须被正确分组为 `role="assistant"` 和 `role="user"` 的 Message 才能写入 `SessionStorage`。

**核心原则**：一次 LLM 响应（一个 agent turn）对应一个 `assistant` Message 和一个 `user` Message（含 tool results），遵循 `session_storage` 的 `write_message(Message)` 契约。

```
LLM 响应开始
  ├── TextDelta(n) × N
  ├── ToolCallEvent(tool_use_id=T1, tool_name="Read", params={...})
  ├── TextDelta(m) × N
  ├── ToolCallEvent(tool_use_id=T2, tool_name="Edit", params={...})
  │
  └── TurnComplete
        │
        ├── 组装成 AssistantMessage:
        │     role="assistant"
        │     content = [
        │       TextBlock(text=concat(TextDelta...)),
        │       ToolUseBlock(id=T1, name="Read", input={...}),
        │       ToolUseBlock(id=T2, name="Edit", input={...}),
        │     ]
        │     ↓ session_storage.write(msg_dict)
        │
        ├── 等待 ToolResultEvent(s) 返回
        │     ToolResultEvent(tool_use_id=T1, result={...})
        │     ToolResultEvent(tool_use_id=T2, result={...})
        │
        └── 组装成 UserMessage:
              role="user"
              content = [
                ToolResultBlock(tool_use_id=T1, content="..."),
                ToolResultBlock(tool_use_id=T2, content="..."),
              ]
              ↓ session_storage.write(msg_dict)
```

**具体映射表**：

| 事件序列 | Message 类型 | `content` 结构 |
|----------|-------------|----------------|
| 首个 turn 的 user prompt | `UserMessage` | `[TextBlock(text=prompt)]` — 在 `run()` 开始处写入 |
| `TextDelta` × N + `ToolCallEvent` × 0 | `AssistantMessage` | `[TextBlock(text=concat(all deltas))]` |
| `TextDelta` × N + `ToolCallEvent` × M | `AssistantMessage` | `[TextBlock(text=text_before_tool), ToolUseBlock(id=...), ...]` — 文本和 tool_use **交替排列**，按事件流顺序 |
| `ToolResultEvent(tool_use_id, result)` × M | `UserMessage` | `[ToolResultBlock(tool_use_id="T1", content=json.dumps(result)), ...]` |
| 后续 turn 的 continuation prompt | `UserMessage` | `[TextBlock(text=continuation_prompt)]` — 每轮 turn 开始处写入 |
| `SessionComplete` | 不写 Message | 调用 `session_storage.flush()` 确保缓冲区落盘 |

**关键实现约束**：

1. **ToolResultEvent 可能乱序到达** — 必须按 `tool_use_id` 配对等待，不一定与 ToolCallEvent 顺序一致。使用 `dict[tool_use_id, ToolResultEvent]` 累积，直到所有已发出的 tool_use 都有 result 才组装 UserMessage。
2. **TurnComplete 触发消息组装** — 不应在收到 ToolCallEvent 时就写 assistant message 的一半，而应在 TurnComplete 时才知道"这一轮 LLM 已输出结束"，此时组装完整的 assistant message 写入。
3. **ToolResult 可能被 approval policy 拒绝** — 被拒绝的 tool call，其 `ToolResultEvent` 的 `is_error=True`。拒绝结果也要写入 `ToolResultBlock(content={"error": "Permission denied"})`，保证转录的完整性。
4. **TextDelta 流中断情况** — 如果 LLM 在输出文本后响应突然中止（如连接断开），尚未收到 `TurnComplete`，当前累积的 `TextDelta` 内容不应丢失。应在下一个 turn 开始前或 `SessionComplete` 时强制 flush 一个残缺的 `AssistantMessage`。
5. **大内容替换** — `SessionStorage.write_message()` 内部有 `_replace_large_content()` 自动将大 tool result 替换为文件引用，无需 AgentRunner 层额外处理。

**与现有审计旁路（F-45 `events.ndjson`）的关系**：

```
AgentRunner.run() 事件循环
  │
  ├── ToolCallEvent: 写入 events.ndjson（F-45，8 字段，扁平审计）
  │                  └── 不写 Message（等到 TurnComplete 再组）
  │
  ├── ToolResultEvent: 写入 events.ndjson（可选扩展）
  │                    └── 暂存到 tool_result_buf[tool_use_id] ← 新增
  │
  ├── TurnComplete:
  │     ├── 组 AssistantMessage → SessionStorage.write_raw(msg_dict)
  │     ├── 组 UserMessage → SessionStorage.write_raw(msg_dict)  ← 依赖 tool_result_buf 已就绪
  │     └── 清空 tool_result_buf
  │
  └── SessionComplete:
        └── SessionStorage.flush()
```
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
---
## 二、Agent 核心能力

### 2.1 Agent 阶段性进度汇报（F-20）
**状态**: ✅ 已完成（F-20）
**目标**: 在 Agent 编排中阶段性将结果汇报至任务看板，将任务看板提取为工具

> 三组合实现方案（检查点触发 + ProgressReportTool + ToolContext.tasks）、架构设计、工具 Schema、与现有组件集成点等已归档。
> 详见 [ARCHIVED_FEATURES.md §十六（Orchestrator 自主模式 16.x）](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成) 与对应进度归档 [ARCHIVED_PROGRESS.md F-20](./ARCHIVED_PROGRESS.md#f-20-agent-阶段性进度汇报)。

---

### 2.2 Team 成员管理（Phase-7）（F-2）
**状态**: 规划中
**目标**: TeamCreate 扩展 `members` 数组，跟踪团队成员 Agent

#### 2.2.1 数据模型
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

#### 2.2.2 核心机制
| 机制 | 说明 |
|------|------|
| TeammateInit | `agent(run_in_background=true)` 时自动注册到 `members` |
| 状态同步 | TaskOutput 显示 completed/failed 时更新成员状态 |
| 名称注册 | Agent 名称冲突检测 `agent_name_registry` |
| 递归 Fork 保护 | Fork Agent 无法嵌套调用 Fork |

#### 2.2.3 实现文件
| 文件 | 状态 |
|------|------|
| `tool_system/tools/team.py` | ✅ 已实现基础 TeamCreate/TeamDelete |
| `tool_system/tools/agent.py` | ⚠️ 待集成 TeammateInit |
| `services/swarm/agent_name_registry.py` | ✅ 已实现名称注册表 |

#### 2.2.4 测试覆盖
| 测试文件 | 测试用例 |
|----------|----------|
| `test_team_file.py` | `test_team_file_created_with_members_array`, `test_team_file_schema_members_array`, `test_team_file_missing_members_tolerated` |
| `test_team_membership.py` | `test_is_team_lead_true_*`, `test_is_team_lead_false_*` |

---

### 2.3 结构化输出增强（Outlines）（F-4）
**状态**: 适配器已完成，待集成
**目标**: 使用 Outlines 预生成约束替代 json.loads + 手动验证

#### 2.3.1 适用场景
| 场景 | 当前实现 | Outlines 方案 |
|------|---------|---------------|
| Token 预算分析 | 正则解析 | 结构化 `TokenBudgetAnalysis` |
| 工具调用决策 | json.loads 解析 | 结构化 `ToolCallDecision` |
| 压缩策略选择 | 手动判断 | 结构化 `CompactionStrategy` |
| Bash 命令分类 | 多个 validator | 结构化 `BashSafetyLevel` |

#### 2.3.2 数据模型
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

#### 2.3.3 实现文件
| 文件 | 状态 |
|------|------|
| `agent/_outlines_adapter.py` | ✅ 适配器已完成 |
| `tool_system/` 集成 | ⏳ 待进行 |

---

### 2.4 MCP 扩展功能（F-3）
**状态**: 基础已完成（F-3），持续增强
**目标**: 完整的 MCP 协议支持

> 5 项基础传输与硬化能力（Stdio / HTTP+SSE / WebSocket / OAuth / HTTPS+XSS 硬化）已归档。
> 详见 [ARCHIVED_FEATURES.md §十七（MCP 协议扩展）](./ARCHIVED_FEATURES.md#十七mcp-协议扩展) 与对应进度归档 [ARCHIVED_PROGRESS.md F-3](./ARCHIVED_PROGRESS.md#f-3-mcp-协议扩展)。

#### 2.4.1 待增强
| 功能 | 优先级 | 说明 |
|------|--------|------|
| MCP 资源缓存 | P2 | 减少重复获取 |
| MCP Batch 工具调用 | P2 | 批量工具执行 |
| MCP Progress 通知 | P3 | 长任务进度报告 |

---

### 2.5 Agent 记忆作用域隔离（F-13）（已完成）
**状态**: ✅ 完成

> 详细设计与验证记录已归档至 [ARCHIVED_FEATURES.md §二十一.7 F-13 Agent 记忆作用域隔离](./ARCHIVED_FEATURES.md#二十一7-f-13-agent-记忆作用域隔离)。

### 2.6 /goal 命令（目标管理）（F-9）
**状态**: ⏳ 待实现
**目标**: 支持长时间运行任务的目标管理

#### 2.6.1 功能说明
支持长时间任务的目标状态管理与 token 用量追踪：

| 子命令 | 功能 |
|--------|------|
| `/goal set <goal>` | 设置当前任务目标 |
| `/goal clear` | 清除目标 |
| `/goal pause` | 暂停目标追踪 |
| `/goal resume` | 恢复目标追踪 |
| `/goal complete` | 标记目标完成 |

#### 2.6.2 核心机制
| 机制 | 说明 |
|------|------|
| Goal 状态机 | `active` / `paused` / `budget_limited` / `complete` |
| Token 用量追踪 | 自动追踪当前 session 的 token 消耗 |
| Continuation Prompt | 目标状态自动注入到 continuation prompt |
| session-scoped 隔离 | 按 sessionId 管理独立的目标状态 |

#### 2.6.3 实现文件
| 文件 | 位置 | 状态 |
|------|------|------|
| Goal 命令 | `commands/goal/goal.ts` | 待实现 |
| Goal 状态管理 | `services/goal/goalState.ts` | 待实现 |
| Goal 工具 | `packages/builtin-tools/src/tools/GoalTool/` | 待实现 |

#### 2.6.4 数据模型
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

### 2.7 ExecuteExtraTool 延迟工具系统（F-10）
**状态**: ⏳ 待实现
**目标**: 按需加载延迟工具，支持语义搜索

#### 2.7.1 功能说明
完整的延迟工具按需加载系统，支持子代理（Async Agent）执行：

| 组件 | 功能 |
|------|------|
| SearchExtraToolsTool | TF-IDF 工具索引语义搜索 |
| ExecuteExtraTool | 通过名称和参数执行延迟工具 |
| validateInput 校验 | 调用前校验防止崩溃 |
| ASYNC_AGENT_ALLOWED_TOOLS | 子代理可执行延迟工具 |

#### 2.7.2 核心机制
| 机制 | 说明 |
|------|------|
| 工具延迟加载 | 工具按名称和参数动态执行，非预加载 |
| 语义搜索 | TF-IDF 索引支持自然语言工具搜索 |
| 子代理执行 | Async Agent 可调用延迟工具 |
| 输入校验 | execute 前 validateInput 防止无效调用 |

#### 2.7.3 实现文件
| 文件 | 位置 | 状态 |
|------|------|------|
| ExecuteExtraTool | `packages/builtin-tools/src/tools/ExecuteTool/ExecuteTool.ts` | 待实现 |
| SearchExtraToolsTool | `packages/builtin-tools/src/tools/SearchExtraToolsTool/` | 待实现 |
| ASYNC_AGENT_ALLOWED_TOOLS | `constants/tools.ts` | 待配置 |
| 延迟工具提示 | `constants/prompts.ts` | 待配置 |

---

### 2.8 工具/Skill 调用统计（跨会话）（F-75）
**状态**: 🔄 规划中
**目标**: 通过追加日志（JSON Lines）实现轻量级跨会话工具和 Skill 调用统计，不支持实时查询

#### 2.8.1 背景
当前项目没有调用统计功能，无法了解工具和 Skill 使用分布情况。本特性解决跨会话数据持久化问题，工具和 Skill 共用同一日志 schema。

#### 2.8.2 日志格式
```
~/.clawcodex/tool_stats.jsonl
{"agent_id": "dev", "kind": "tool", "tool": "Read", "ts": 1748..., "dur_ms": 12.3, "ok": true}
{"agent_id": "dev", "kind": "skill", "skill": "code_review", "ts": 1748..., "dur_ms": 3200.0, "ok": true}
{"agent_id": "orchestrator-001", "kind": "tool", "tool": "Bash", "ts": 1748..., "dur_ms": 2300.0, "ok": false, "error": "timeout"}
```

#### 2.8.3 日志字段（统一 schema）
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

#### 2.8.4 性能特性
| 操作 | 性能影响 | 说明 |
|------|---------|------|
| 追加写入 | 极小 | 顺序追加是磁盘 I/O 最优模式 |
| 文件过大后查询 | 较大 | 全量扫描，数据量大时需预聚合 |
| 多进程并发写 | 中等 | 建议单进程内汇聚后批量写入 |

#### 2.8.5 架构设计
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

#### 2.8.6 查询示例
```bash
# 统计所有 skill 调用
grep '"kind":"skill"' ~/.clawcodex/tool_stats.jsonl | jq '.skill' | sort | uniq -c | sort -rn

# 统计工具 vs skill 调用比例
grep -E '"kind":"(tool|skill)"' ~/.clawcodex/tool_stats.jsonl | jq -s 'group_by(.kind) | map({kind: .[0].kind, count: length})'

# 统计某个 agent 的调用
grep '"agent_id":"orchestrator-001"' ~/.clawcodex/tool_stats.jsonl | jq -s 'group_by(.kind) | map({kind: .[0].kind, count: length, avg_ms: (map(.dur_ms) | add / length)})'
```

#### 2.8.7 数据清理
日志文件需定期归档或设置 TTL（建议保留最近 90 天数据）。

#### 2.8.8 实时查询
**不支持**。如需实时展示（如 TUI 状态栏），需另建汇总表预聚合。

#### 2.8.9 替代方案：基于 Transcript 的轻量级统计
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

#### 2.8.10 基于使用频率的工具/Skill 裁剪
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

#### 2.8.11 SOP 转化模式
将标准作业流程（SOP）拆解为 Agent 架构，实现工作流的可复用、可观测、可编排。

**三层映射关系**:

| 工作流组件 | Agent 架构 | 示例 |
|-----------|-----------|------|
| SOP (标准作业流程) | Agent | 数据分析 Agent、CI/CD Agent、ML Pipeline Agent |
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
    """将 SOP 转换为 Agent 的 Skill"""

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

#### 2.8.12 业务 Agent 长期使用（新窗口重连）
将 SOP 转化的 Agent 作为主 Agent 长期使用，并支持在新窗口中重新连接。

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

### 2.9 CreateAgentTool 动态工具创建（F-18）
**状态**: 🔄 规划中
**目标**: Agent 可根据三方 CLI/API 规范动态创建工具，实现"工具创建工具"的 Meta Tool 能力

#### 2.9.1 功能说明
允许 Agent 分析第三方工具（CLI 命令或 HTTP API）的接口规范，然后动态创建一个可用的工具：

```
Agent 分析 CLI 规范 → 生成工具规范 → 调用 CreateAgentTool → 注册新工具 → 使用新工具
```

#### 2.9.2 架构设计
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

#### 2.9.3 工具规范（AgentToolSpec）
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

#### 2.9.4 三种 call_impl 安全限制
| call_type | call_impl 示例 | 安全级别 |
|-----------|---------------|---------|
| `bash` | `"git status --porcelain {path}"` | ✅ 占位符防注入，预定义命令白名单 |
| `http` | `{"method": "GET", "url": "https://api.github.com/{endpoint}"}` | ✅ 模板化，方法白名单 |
| `python` | `"fetch_data"` → 映射到预定义函数 | ⚠️ 仅白名单函数注册 |

**命令白名单（bash）**：`git`, `gh`, `glab`, `curl`, `wget`, `kubectl`, `docker`, `npm`, `pip`

**HTTP 方法白名单**：`GET`, `POST`, `PUT`, `DELETE`, `PATCH`

#### 2.9.5 CreateAgentTool 输入规范
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

#### 2.9.6 安全性约束
| 约束类型 | 实现位置 | 说明 |
|---------|---------|------|
| 命令白名单 | `validators.py:_validate_bash_impl` | 仅允许预定义命令 |
| HTTP 方法白名单 | `validators.py:_validate_http_impl` | 仅白名单方法 |
| Python 函数注册 | `validators.py:_validate_python_impl` | 仅白名单函数 |
| 无任意代码执行 | `factory.py` | call_impl 是模板/映射，非代码 |
| 参数化防注入 | `call_handlers/bash.py` | format 替换，无 shell 注入 |
| 超时保护 | `call_handlers/bash.py` | subprocess timeout=30 |

#### 2.9.7 持久化机制
Agent 创建的工具保存到 `~/.clawcodex/agent-tools/{name}.json`，重启后自动加载。

#### 2.9.8 与现有系统集成
| 现有组件 | 如何协作 |
|---------|---------|
| `build_tool()` | 作为工厂函数，CreateAgentTool 调用它 |
| `ToolRegistry` | 工具创建后调用 `registry.register(tool)` |
| `parse_agent_markdown` | 已有工具定义解析，可复用 schema 验证 |
| MCP 工具包装 | 参考 `tool_wrapper.py` 的声明式工具模式 |
| `resolve_agent_tools()` | 允许 `source="agent-created"` 的工具被解析 |

#### 2.9.9 实现文件
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

### 2.10 sessionStorage 容量限制（F-11）
**状态**: ⏳ 待实现
**目标**: 防止长时间运行的 daemon/swarm 会话导致内存泄漏

#### 2.10.1 功能说明
为 `existingSessionFiles` Map 设置容量上限，防止无限增长：

```python
MAX_CACHED_SESSION_FILES = 200

def add_session_file(sessionId: UUID, filePath: str):
    if len(existingSessionFiles) >= MAX_CACHED_SESSION_FILES:
        oldest_key = next(iter(existingSessionFiles))
        del existingSessionFiles[oldest_key]
    existingSessionFiles[sessionId] = filePath
```

#### 2.10.2 问题场景
- daemon/swarm 模式下长时间运行
- sessionId 频繁创建销毁
- Map 无限增长导致 OOM

#### 2.10.3 实现文件
| 文件 | 位置 | 状态 |
|------|------|------|
| sessionStorage | `utils/sessionStorage.ts` → `utils/session_storage.py` | 待实现 |

---

### 2.11 cacheWarning 容量限制（F-12）
**状态**: ✅ 完成

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.14 cacheWarning 容量限制](./ARCHIVED_FEATURES.md#二十一14-cachewarning-容量限制f-12)。

---
### 2.12 Issue 语义澄清流程（自主模式扩展）（F-78）
**状态**: ✅ 已完成（F-1.5~F-1.11，Phase A-G 全部完成）
**优先级**: P1
**目标**: 当 Issue 语义模糊时，通过**三通道优先机制**获取澄清——本地操作员（Dashboard/ClarificationQueue）优先，作者 @mention 兜底

> 三通道优先机制（Dashboard / ClarificationQueue / @mention）、平台能力对比、整体流程图、各通道详细设计、ClarificationStatus 枚举（含冲突处理 `DUPLICATE_REJECTED` / `STALE_REJECTED` / `CONFLICT_RESOLVED`）、多渠道冲突处理状态机、CLI `clarify` 命令、TrackerAdapter 评论接口与 GitHub/Gitee/GitCode 实现、IssueRegistry 澄清字段持久化、PromptBuilder 澄清内容注入、escalation 策略与配置等已归档。
> 详见 [ARCHIVED_FEATURES.md §16.5（Issue 语义澄清流程）](./ARCHIVED_FEATURES.md#十六orchestrator-自主模式symphony-集成) 与对应进度归档 [ARCHIVED_PROGRESS.md F-1.x 子特性](./ARCHIVED_PROGRESS.md#f-1x-orchestrator-自主模式f-1-子特性全部完成)。

---


### 2.13 Auto 模式 (TRANSCRIPT_CLASSIFIER)（F-16）
**状态**: ⏳ 待实现
**优先级**: P2
**目标**: 基于 LLM 的自动权限模式切换，减少交互疲劳

#### 2.13.1 功能说明
Auto 模式是一种智能权限模式，通过 LLM 分类器（TRANSCRIPT_CLASSIFIER）自动判断何时允许执行敏感操作。在长时间任务或重复性操作场景下，Auto 模式可以减少用户确认的交互频率。

#### 2.13.2 工作原理
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

#### 2.13.3 与手动模式的区别
| 模式 | 触发方式 | 确认频率 | 适用场景 |
|------|---------|---------|---------|
| `default` | 手动确认每个敏感操作 | 高 | 学习/审查模式 |
| `acceptEdits` | 手动确认写操作 | 中 | 代码迭代 |
| `plan` | 仅读取，编辑前分析 | 低 | 探索代码库 |
| `auto` | LLM 自动判断 | 自动调节 | 长任务/减少疲劳 |
| `bypassPermissions` | 无限制 | 无 | 隔离环境 |

#### 2.13.4 循环切换逻辑（已实现部分）
`Shift+Tab` 循环切换顺序：
```
default → acceptEdits → plan → bypassPermissions (如果可用) → default
```

注意：`auto` 模式不出现在手动循环中，需要通过 `--permission-mode auto` 启动或由分类器自动触发。

#### 2.13.5 待实现组件
| 组件 | 文件 | 说明 |
|------|------|------|
| TRANSCRIPT_CLASSIFIER | `permissions/classifier.py` | LLM 分类器核心 |
| canCycleToAuto | `permissions/cycle.py` | 判断是否可切换到 auto |
| Auto Mode 集成 | `agent/run_agent.py` | 在工具执行前调用分类器 |
| 分类结果缓存 | `permissions/cache.py` | 避免重复分类 |

#### 2.13.6 分类器 prompt 设计
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

#### 2.13.7 实施阶段
| 阶段 | 内容 | 优先级 | 状态 |
|------|------|--------|------|
| Phase A1 | TRANSCRIPT_CLASSIFIER 核心实现 | P2 | ⏳ 待开始 |
| Phase A2 | `canCycleToAuto()` 判断逻辑 | P2 | ⏳ 待开始 |
| Phase A3 | Auto Mode 工具执行前集成 | P2 | ⏳ 待开始 |
| Phase A4 | 分类结果缓存机制 | P3 | ⏳ 待开始 |

---

### 2.14 Agent 间自主观察与消息交互（F-80）
**状态**: ✅ 已完成（Phase M1-M5 全部完成）
**优先级**: P1
**目标**: 实现 Manager Agent 全自动观察 Worker Agent 状态并注入指令，支持优先级队列和权限审批

> 角色定义（Manager / Worker 通过工具组合自动识别）、核心工具（`TaskInspect` + `TaskDirectives`）、优先级队列（`queue_pending_message` priority 字段 + `drain_pending_messages` 按优先级消费）、工具可见性过滤（仅 Manager 可调用）、权限规则传递与 Phase M1-M5 实施阶段已归档。
> 详见 [ARCHIVED_FEATURES.md §十八（Agent 间自主观察与消息交互）](./ARCHIVED_FEATURES.md#十八agent-间自主观察与消息交互) 与对应进度归档 [ARCHIVED_PROGRESS.md F-29（TaskInspect/TaskDirectives 工具注册）](./ARCHIVED_PROGRESS.md#f-29-taskinspecttaskdirectives-工具注册)。

---

## 三、CLI 与配置系统

### 3.1 CLI 模型供应商与模型切换设计（F-43）
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

### 3.2 permission_mode enum 正交拆分设计（F-46）
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

### 3.3 Permission Settings Schema 重构设计（F-47）
**状态**: ✅ 完成（含 F-47.1 hotfix）

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.9 F-47 Permission Settings Schema 重构](./ARCHIVED_FEATURES.md#二十一9-f-47-permission-settings-schema-重构)。

---

## 四、Architecture & SDK 下沉

### 4.1 F-48: src/ 核心路径二开修改解耦方案
> **状态**: 📋 设计完成
> **优先级**: P0
> **目标**: 将 `src/` 中所有二开新增功能文件 + 功能修改点全部迁移到 `clawcodex_ext/` 和 `extensions/` 扩展路径，使 `src/` 与上游源码（`src/upstream/58ea488/`）仅剩格式/import 层面差异，消除所有功能性差异。目标量化：src/ 二开新增文件数从 **30 → 0**（含 `src/orchestrator/` 顶层包），功能修改文件数从 10 → 0。

#### 4.1.1 问题现状

通过 `diff -rq src/upstream/58ea488/ src/` 逐文件对比，发现四类差异：

| 差异类别 | 数量 | 说明 |
|---------|------|------|
| **A: 仅当前 src/ 有的文件（纯新增）** | **30 项** | 上游不存在的文件/目录，纯二开新增（其中 `src/orchestrator/` 顶层包含 19+ Python 文件） |
| **B: 两者都有但 `diff -w` 有输出的文件（功能修改）** | **67 个** | 有语义逻辑变化的文件（71 个 `diff` 差异 − 4 个格式差异） |
| **C: 两者都有但 `diff -w` 全空的文件（纯格式差异）** | **4 个** | `buddy/notification.py`, `buddy/sprites.py`, `buddy/types.py`, `replLauncher.py` — 仅行尾/空白差异 |
| **D: 仅上游有但 src/ 缺失的文件** | **1 个** | `settings/permission_validation.py`（被 `settings/pydantic_adapter.py` 替代，需在 `f48-modification-tracking.md` 记录决策理由） |

> ⚠️ **勘误**：早前版本误以为"~61 个格式差异"，实际 `diff -w` 验证发现仅 **4 个**文件是纯格式差异，其余 67 个均有语义变更。

##### 类别 A：30 个纯二开新增文件
*[保留现有 29 项表格，下方追加 #30 项]*

| # | 路径 | 性质 | 落点建议 |
|---|------|------|---------|
| 1-29 | *（原 29 项，详见保留表格）* | 既有项 | 既有落点 |
| **30** | **`src/orchestrator/`**（顶层包，19+ Python 文件） | 全新二开子项目（多 Linear 集成、Repo 跟踪、Local Tracker、Status Dashboard、Workspace 协调等） | **`extensions/orchestrator/`**（独立子扩展，不入侵 `src/`）。`ControlSocket` 落地在 `extensions/orchestrator/`（参 §3.x F-XXX 既定约束） |

##### 类别 B：67 个功能修改文件（10 个已设计解耦 + 57 个新发现）

| 模块 | 文件数 | 文件清单 | 已覆盖？ |
|------|--------|---------|---------|
| **Phase 1-3 已设计解耦（10 个）** | | | |
| 核心入口点 | 3 | `entrypoints/tui.py`, `entrypoints/headless.py`, `cli.py`（已完成） | ✅ Phase 3 |
| REPL | 1 | `repl/core.py` | ✅ Phase 2 |
| TUI | 2 | `tui/app.py`, `tui/commands.py` | ✅ Phase 2 |
| 上下文系统 | 1 | `context_system/prompt_assembly.py` | ✅ Phase 1 |
| 权限系统 | 1 | `permissions/cycle.py` | ✅ Phase 1 |
| 命令系统 | 2 | `command_system/types.py`, `command_system/engine.py` | ✅ Phase 1 |
| **新发现：未覆盖的功能修改文件（57 个）** | | | |
| bridge/ | **6** | `__init__.py`, `bridge_main.py`, `bridge_pointer.py`, `repl_bridge.py`, **`repl_bridge_transport.py`**, `worktree.py` | ❌ 见 Phase 4 |
| buddy/ | 8 | `__init__.py`, `companion.py`, `feature.py`, `observer.py`, `prompt.py`, `soul.py`, `sprites.py`, `types.py`（注：`buddy/notification.py` 在 diff 中但属类别 C 格式差异，已排除） | ❌ 见 Phase 5 |
| settings/ | 4 | `__init__.py`, `constants.py`, `types.py`, `validation.py` | ❌ 见 Phase 6 |
| providers/ | 4 | `__init__.py`, `base.py`, `anthropic_provider.py`, `openai_compatible.py` | ❌ 见 Phase 7 |
| transports/ | 3 | `hybrid_transport.py`, `serial_batch_event_uploader.py`, `websocket_transport.py` | ❌ 见 Phase 8 |
| query/ | 3 | `engine.py`, `query.py`, `agent_loop_compat.py` | ❌ 见 Phase 9 |
| coordinator/ | 2 | `mode.py`, `prompt.py` | ❌ 见 Phase 9 |
| tool_system/ | 4 | `tools/__init__.py`, `tools/agent.py`, `context.py`, `tools/bash/bash_tool.py` | ❌ 见 Phase 9 |
| command_system/ | 3 | `__init__.py`, `buddy_command.py`, `builtins.py` | ❌ 见 Phase 9 |
| repl/ | 2 | `__init__.py`, `live_status.py` | ❌ 见 Phase 9 |
| tui/（除已覆盖）| 12 | `state.py`, `keybindings.py`, `agent_bridge.py`, `messages.py`, `screens/__init__.py`, `screens/repl.py`, `screens/resume_conversation.py`, `widgets/header.py`, `widgets/messages/assistant_thinking.py`, `widgets/prompt_input.py`, `widgets/status_line.py`, `widgets/transcript_view.py` | ❌ 见 Phase 9 |
| 散在文件 | **8** | `agent/session.py`, `config.py`, `constants/xml.py`, `permissions/modes.py`, `memdir/memdir.py`, `reference_data/subsystems/buddy.json`, `skills/bundled/loop.py`, `utils/stream_watchdog.py` | ❌ 见 Phase 9 |

#### 4.1.2 已完成的解耦模式（可复用）

项目已验证 3 种成熟的解耦模式，F-48 将复用这些模式：

1. **Facade 模式**（`src/cli.py`）— src/ 只剩 `from clawcodex_ext.xxx import yyy; return yyy()`
2. **子类覆盖模式**（`clawcodex_ext/tui/app.py`）— `ClawCodexExtTUI(ClawCodexTUI)` 覆盖 hook 方法
3. **前端注册表模式**（`clawcodex_ext/frontend/`）— `@register_frontend` + `get_frontend("repl")` 工厂

#### 4.1.3 解耦方案：按模块+优先级分 Phase

##### 🆕 F-48.2: 本批次完成的解耦项

以下 3 项解耦已在本批次（2026-06）完成：

| 文件 | 解耦操作 | 解耦模式 | 新扩展文件 |
|------|---------|---------|-----------|
| `tool_system/tools/__init__.py` | 移除 `ProgressReportTool`、`TaskDirectivesTool`、`TaskInspectTool` 注册。**现在与 upstream 完全一致** | Extension Hook + 注册表 | `extensions/tool_system_ext/registration.py`（新增）；`src/tool_system/defaults.py` 添加通用 EXTENSION_TOOLS 钩子 |
| `providers/__init__.py` | 新增 `register_provider()` / `register_provider_info()` API；`openai-codex` 的 `PROVIDER_INFO` 条目移至 `clawcodex_ext`（`get_provider_class` 因循环导入约束暂留 `src/`） | 注册 API 模式 | `clawcodex_ext/providers/__init__.py` 调用 `register_provider_info()`；`src/providers/runtime.py` 补充 facade 缺失导出 |
| `agent/session.py` | 移除 `resume_with_tail()` 类方法，提取为独立函数 | 独立函数模式 | `clawcodex_ext/agent/session_ext.py`（新增） |

##### Phase 0: 纯新增文件移入 ext（**30 项**，无风险，立即执行）

*[保留现有 29 项表格 + §6.1.1 类别 A #30 项 `src/orchestrator/` 顶层包，下方不再重复列出]*

##### F-48.1: Adapter 文件统一解耦子特性
*[不变，保留现有内容]*

##### Phase 1: 注册表/Protocol 扩展消除字段注入（低风险）
*[不变，保留现有内容]*

##### Phase 2: 子类覆盖模式恢复上游构造器签名（中等风险）
*[不变，保留现有内容]*

##### Phase 3: 入口点恢复上游逻辑（需谨慎，高集成度）
*[不变，保留现有内容]*

##### Phase 4: Bridge 文件回归（新增，中等风险）

| 文件 | 差异性质 | 解耦方案 | 工作量 |
|------|---------|---------|--------|
| `bridge/__init__.py` | 新增 `BridgeState` 导出 | 评估是否可直接还原导出列表 | 0.5天 |
| `bridge/bridge_main.py` | 移除 JWT refresh、`build_sdk_url`、`get_access_token` 参数 | 这些是二开新增？还是上游同步遗漏？需确认后选择还原或保留。**方法学**：JWT refresh → Facade（`clawcodex_ext/bridge/auth.py` 提供 `refresh_jwt_if_needed()`）；`build_sdk_url` / `get_access_token` 参数 → Protocol 扩展（`BridgeConfigProvider` Protocol）注入，避免破坏上游构造器签名 | 1天 |
| `bridge/repl_bridge.py` | 大幅 docstring 重写 + 行为修改 | 还原 docstring，功能差异需逐行评审 | 1天 |
| `bridge/repl_bridge_transport.py` | 新发现（小范围行为修改） | 还原上游签名，行为差异逐行评审 | 0.5天 |
| `bridge/bridge_pointer.py`, `worktree.py` | `__all__` 导出、小范围行为修改 | 还原导出列表，功能差异逐行评审 | 0.5天 |

> **注意**：Bridge 文件的差异可能是上游 58ea488→后续版本之间的官方更新被二开意外覆盖。需 `git log src/bridge/` 确认每个变化的来源。

##### Phase 5: Buddy 文件回归（新增，低风险）

| 文件 | 差异性质 | 解耦方案 | 工作量 |
|------|---------|---------|--------|
| `buddy/` 8 个文件（注：`buddy/notification.py` 在 diff 中但属 §6.1.1 类别 C 格式差异，已排除） | 主要为 docstring 差异 + 缓存行为变更 | **优先还原**：差异集中在 docstring 说明性文字，不影响行为。`companion.py` 的注释差异可还原 | 0.5天 |

##### Phase 6: Settings 文件回归（新增，低风险）

| 文件 | 差异性质 | 解耦方案 | 工作量 |
|------|---------|---------|--------|
| `settings/__init__.py` | F-47 重构删除 `PermissionRule` 和 `validate_permission_rules` 导出 | 保持现状（F-47 已完成，是预期变更） | 0天 |
| `settings/types.py` | F-47 类型变更 | 保持现状 | 0天 |
| `settings/validation.py` | F-47 验证逻辑变更 | 保持现状 | 0天 |
| `settings/constants.py` | 常量修改 | 评审差异来源 | 0.5天 |

##### Phase 7: Provider 文件回归（新增，中等风险）

| 文件 | 差异性质 | 解耦方案 | 工作量 | 状态 |
|------|---------|---------|--------|------|
| `providers/base.py` | 新增 `ThinkingChunkCallback` + `on_thinking_chunk` | 评估是否可通过 Protocol 扩展到 ext | 1天 | ⏳ 待执行 |
| `providers/__init__.py` | `openai-codex` 的 `PROVIDER_INFO` 已移入 `clawcodex_ext`；`get_provider_class` 因循环导入约束暂留 | **部分完成**—`register_provider_info` API 已可用 | 0天 | ✅ **F-48.2 完成** |
| `providers/anthropic_provider.py` | 行为修改 | 逐行评审差异 | 1天 | ⏳ 待执行 |
| `providers/openai_compatible.py` | 行为修改 | 逐行评审差异 | 1天 | ⏳ 待执行 |

##### Phase 8: Transport 文件回归（新增，中等风险）

| 文件 | 差异性质 | 解耦方案 | 工作量 |
|------|---------|---------|--------|
| `transports/hybrid_transport.py` | 行为修改 | 逐行评审，差异可能是 bridge 集成的必要修改 | 0.5天 |
| `transports/websocket_transport.py` | 行为修改 | 同上 | 0.5天 |
| `transports/serial_batch_event_uploader.py` | 行为修改 | 同上 | 0.5天 |

##### Phase 9: 其余散在文件回归（新增，高风险）

| 模块 | 文件数 | 主要差异 | 工作量 | 状态 |
|------|--------|---------|--------|------|
| `tui/*`（12个） | 12 | PendingAskUser、Ctrl+B、thinking toggle、permission mode 状态栏等 | 2-3天 | ⏳ 待执行 |
| `query/*` | 3 | 查询引擎修改 | 1天 | ⏳ 待执行 |
| `coordinator/*` | 2 | 轻量工具集注册 | 0.5天 | ⏳ 待执行 |
| `tool_system/*` | 4 → **1** | 新工具注册（3 个已通过 `EXTENSION_TOOLS` 钩子解耦，仅 `context.py`+`tools/agent.py`+`bash/bash_tool.py` 待处理） | 0.5天 | ✅ **F-48.2 完成 3/4** |
| `command_system/*` | 3 | Buddy 命令注册、builtins 修改 | 0.5天 | ⏳ 待执行 |
| `agent/session.py` | 1 → **0** | `resume_with_tail` 已提取至 `clawcodex_ext/agent/session_ext.py` | — | ✅ **F-48.2 完成** |
| `config.py` | 1 | 配置项添加/修改 | 0.5天 | ⏳ 待执行 |
| 其余散在 | **8** | `constants/xml.py`, `permissions/modes.py`, `memdir/memdir.py`, `reference_data/subsystems/buddy.json`, `skills/bundled/loop.py`, `utils/stream_watchdog.py` | 1天 | ⏳ 待执行 |

#### 4.1.4 解耦前后效果对比

| 指标 | 解耦前 | 解耦后（乐观） | 解耦后（现实） | 当前实际（2026-06） |
|------|--------|---------------|---------------|-------------------|
| src/ 二开新增文件 | 30 项 | **0** ✅ | **0** ✅ | **0** ✅（全部移至 ext） |
| src/ 功能修改文件 | 67 个 | **0** ❌（不可达） | **~10-20**（bridge/buddy/transport 等核心难以完全消除） | **~60**（3 项已完成解耦） |
| tool_system/tools/__init__.py 与 upstream 差异 | 3 个二开工具注册 | 0 | 0 | **0** ✅ **已消除** |
| agent/session.py 与 upstream 差异 | `resume_with_tail` + logging | 0 | 0 | **仅 _save_to_session_storage 残留**（背景 agent 持久化） |
| providers/__init__.py 与上游差异 | `openai-codex` 在 PROVIDER_INFO 和 get_provider_class | `PROVIDER_INFO` 可消除 | `get_provider_class` 因循环导入暂留 | **PROVIDER_INFO 已消除** 🟡 |
| 上游同步冲突 | 高（每次 820+ 行差异） | **极低** | **低**（核心模块仍可能有冲突） | **降低约 30%** |
| 二开代码位置 | 散布在 src/ + ext | **100% ext** | **~90% ext** | **~92% ext** 🟢 |

#### 4.1.5 验收标准

1. `diff -rq src/ src/upstream/58ea488/` 不再有"Only in src/"输出（**30→0** ✅，含 `src/orchestrator/` 顶层包移入 `extensions/orchestrator/`）
2. Phase 0 的 30 个新增文件全部移入 ext/extensions，src/ 原位置仅保留 thin re-export
3. Phase 1-3 的 10 个功能修改文件 `diff -w` 返回空（功能层面一致）
4. Phase 4-9 覆盖的 57 个文件完成评审：确认保留或还原，记录每文件决策理由
5. 所有现有功能测试通过：`python3 -m pytest tests/ -q`
6. REPL/TUI/Headless 三前端完整可用
7. `docs/decisions/f48-modification-tracking.md` 记录每文件决策（保留/还原/seam），含 4 个格式差异 no-op 项（决策 #9）+ `settings/permission_validation.py` 替代项（决策 #11）

#### 4.1.6 风险与约束

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Phase 4-9 的 57 个文件中部分修改来源不明 | 可能误将上游更新当作二开修改，还原后丢失官方修复 | 用 `git log src/bridge/` 等追溯每行修改来源，标注"来自上游"或"二开新增" |
| Buddy 模块（Phase 5）是新移植模块，diff 大 | 可能包含上游 58ea488 版本本身的注释修正 | `git diff cc-upstream-main...58ea488 -- src/buddy/` 检查上游版本间差异 |
| Provider 基础类（`base.py`, `anthropic_provider.py`）差异影响全局 | 修改 provider 基类会传播到所有 LLM 调用 | Phase 7 优先；差异需团队审阅确认 |
| `tui/*` 12 个文件的差异与已有 ClawCodexExtTUI 子类方案重叠 | 子类已解耦部分功能，但 tui/ 本体仍有注入 | Phase 9 需逐一审计，确保子类覆盖完整 |
| 57 个文件逐行追溯需大量人力 | 工作量从预估 5-7 天膨胀到 2-3 周 | Phase 4-9 按优先级分步执行，非全量冻结 |

#### 4.1.7 已拟定的设计决定

| # | 决定 | 理由 |
|---|------|------|
| 1 | 注册表/Protocol 扩展点放在 `src/capabilities/` 而非 `src/` 本体 | capabilities 层已允许下游扩展导入 |
| 2 | `**kwargs` 透传而非上游签名完全一致 | 避免每次上游更新都需同步改子类签名 |
| 3 | Phase 0 re-export 临时方案，Phase 4-9 后逐步移除 | 避免一次性 breaking change |
| 4 | **Phase 4-9 不追求 100% 还原** | bridge/buddy/transport 等核心模块的差异可能是必要的二开功能，强行还原会破坏系统 |
| 5 | **每文件需记录决策理由** | 输出 `docs/decisions/f48-modification-tracking.md`，标注每个 diff 的保留/还原/seam 决策 |
| 6 | 格式差异（4 个文件）不处理 | `diff -w` 已确认无语义差异 |
| 7 | **新增文件迁移（Phase 0）优先执行** | 消除"Only in src/"后 diff 噪声骤降，便于聚焦评审功能修改 |
| 8 | **Adapter 文件统一处理成 F-48.1 子特性** | 7 个 adapter 结构完全一致 |
| 9 | **4 个格式差异文件归档到 `f48-modification-tracking.md` 的 no-op 列表** | `buddy/notification.py`, `buddy/sprites.py`, `buddy/types.py`, `replLauncher.py` 经 `diff -w` 验证无语义差异；无需还原也无需评审，仅在追踪文档中登记以保持审计完整性 |
| 10 | **`src/orchestrator/` 顶层包作为独立子扩展落地** | 19+ 文件自成体系（Linear / Repo Tracker / Local Tracker / Status Dashboard / Workspace），不应混入 `clawcodex_ext/` 通用扩展；按既定约束（参 §3.x F-XXX）落地在 `extensions/orchestrator/`，`src/` 中仅保留 thin re-export 或完全无入口 |
| 11 | **`settings/permission_validation.py` 缺失需在追踪文档中显式记录** | 上游文件在 src/ 中不存在，被 `pydantic_adapter.py` 替代；需在 `f48-modification-tracking.md` 中以 "上游文件 → 替代方案" 形式记录，避免后续误以为是遗漏同步 |

#### 4.1.8 依赖与协同

- **依赖**：
  - F-34（前端注册表解耦）✅ 已完成
  - F-35（二开特性统一切换）— 提供了上游纯净模式框架
  - F-47（Permission Settings Schema 重构）— 已影响 settings/ 4 个文件
- **协同**：
  - 与 F-15（Shift+Tab cycle）强协同：循环表注册表是 `dontAsk` 解耦载体
  - 与 F-43（CLI 模型供应商切换）协同：`runtime_context` 字段由 Phase 1 Protocol 扩展注入
  - 与 F-28（Ctrl+B 后台运行）强协同：`background_runner.py` 移入 ext 是前提
  - 与 F-49（Session 统一存储）协同：`agent/session.py` 的 SessionStorage 差异
  - 与 F-41（Coordinator 工具集）协同：`coordinator/mode.py` 和 `prompt.py` 的差异
- **先于**：
  - F-35 的 584 文件还原需要 F-48 先完成核心解耦
- **F-35 启动 Gate Criterion**：
  - F-48 Phase 0 完成（`diff -rq` 不再出现 "Only in src/" 输出，30→0 全部归档到 ext/extensions）后，F-35 可启动第一批上游纯净模式（仅影响 `clawcodex_ext/` 与 `extensions/` 的还原测试）
  - F-48 Phase 1-3 完成（10 个核心入口点 `diff -w` 返回空）后，F-35 可启动第二批（涉及 `cli.py` / `tui/app.py` 等 10 个二开热点的纯净模式切换）
  - F-48 Phase 4-9 完成审计（67 个修改文件全部在 `f48-modification-tracking.md` 登记决策）后，F-35 才可启动第三批（涉及 bridge/buddy/transport 等核心模块的纯净模式）；审计未完成前 F-35 不得触碰这些模块
- **遗留问题**：
  - 57 个新增发现文件需逐行追溯来源方可知能否还原
  - 需要新增 `docs/decisions/f48-modification-tracking.md` 记录每文件决策（含 4 个格式差异 no-op 项 + `settings/permission_validation.py` 替代项）

---

### 4.2 SOP 转换器源码固化设计（F-50）
**状态**: ✅ 完成
**优先级**: P1

> 详细设计与落地记录已归档至 [ARCHIVED_FEATURES.md §二十一.13 F-50 SOP 转换器源码固化](./ARCHIVED_FEATURES.md#二十一13-f-50-pos-转换器源码固化sourcecodeparser--增强-skillgrouper--agentmarkdownwriter)。

---

#### 4.2.1 SOP 转换器分组策略增强设计（F-55）

**状态**: ✅ 已实现 | **优先级**: P1
**实现位置**: `extensions/pos_converter/skill_grouper.py`
**核心文件**: `skill_grouper.py`, `source_parser.py`, `agent_md_writer.py`, `clawcodex_ext/cli/pos_cmd/commands.py`

F-55 是 F-50 (SOP 转换器源码固化) 的增强子特性，解决 **"模块多时 Agent 过多"** 的核心问题。

##### 背景与问题

SOP 转换器的默认行为是将 `SourceCodeParser` 解析出的每个组件 (`SourceComponent`) 各自生成一个独立 Agent，然后额外生成一个总览 Agent (`clawcodex-overview`)。

假定一个含 N 个模块的视频处理 SDK：

| SDK 模块 | 默认转化行为 | Agent 数量 |
|----------|------------|:----------:|
| `audio.py`, `video.py`, `storage.py`, `detect.py`, ... | 每模块 1 个 Agent | N |
| 总览 Agent | 始终生成 | 1 |
| **合计** | — | **N + 1** |

N=50 时即生成 **51 个 Agent 文件**。尽管每个 Agent 仅是轻量 `.md` 文件（~几 KB），但过多的 Agent 会带来：
1. **用户心智负担**："/agent-list 出现几十个名字" 让人困惑该用哪个
2. **启动加载成本**：运行时注册表需要发现并解析所有 Agent 定义
3. **路由低效**：总览 Agent 的 `@component-agent` 指令集随 N 线性增长

##### 设计目标

1. **提供灵活的 Agent 聚合策略**，不再刚性按照模块拆分
2. **默认行为不破坏向后兼容**——`COMPONENT_GROUP` 仍是默认值，已有用户的 `pos convert` 输出不变
3. **所有策略输出均配合总览 Agent**，确保用户面向单一入口
4. **策略可在 CLI 一键切换**，无需修改代码

##### 四种分组策略

策略在 `GroupStrategy` 枚举 (`skill_grouper.py:17-21`) 定义：

| 策略 | 枚举值 | 源码方法 | 分组依据 | Agent 数量（50 模块） | 适用场景 |
|------|--------|---------|---------|:-------------------:|---------|
| `COMPONENT_GROUP` | `component_group` | `_component_group()` | 每个 `SourceComponent` 自成一个 Skill，每个 Skill 对应一个 Agent | 50 | 模块职责高度正交，每个模块对应一个完整业务领域 |
| `KEYWORD_MATCH` | `keyword_match` | `_static_group()` | 按预定义 `MappingRule` 的模式匹配（命名前缀/关键字） | 取决于规则数（通常 3-8） | SDK 方法命名约定良好（如 `docker_build`/`docker_push` → `build_image`） |
| `IO_RELATION` | `io_relation` | `_io_relation_group()` | 按方法参数类型签名聚类，共享参数类型的操作合并 | 通常 5-15 | SDK 模块内聚度低但参数类型体系清晰 |
| `LLM_SEMANTIC` | `llm_semantic` | `_group_with_llm()` (fallback → `_static_group`) | LLM 根据方法名称、docstring、业务需求语义聚类 | 通常在 3-8，由 LLM 决定 | 无固定命名约定，需要理解业务上下文 |

##### 策略决策流

```
SourceCodeParser.parse() → list[SourceComponent] (50 个组件)
                              │
                              ▼
         ┌── COMPONENT_GROUP ──→ 50 个 Skill → 50 个 Agent + Overview
         │
         ├── KEYWORD_MATCH ────→  5 个 Skill →  5 个 Agent + Overview
         │
group ───┼── IO_RELATION ──────→ 10 个 Skill → 10 个 Agent + Overview
         │
         └── LLM_SEMANTIC ─────→  4 个 Skill →  4 个 Agent + Overview
                                        │
                                        ▼
                              AgentMarkdownWriter
                              write_overview_agent()
                              write_agent() × N
```

**关键约束**：无论选择哪种策略，总览 Agent 始终生成，始终是用户的唯一入口。

##### CLI 接口设计

```bash
# 默认（COMPONENT_GROUP — 每个模块一个 Agent）
clawcodex pos convert ./sdk/ --out ./output

# 按关键字规则合并
clawcodex pos convert ./sdk/ --out ./output --strategy keyword

# 按 IO 参数类型合并
clawcodex pos convert ./sdk/ --out ./output --strategy io

# LLM 语义分组（需要 LLM 可用）
clawcodex pos convert ./sdk/ --out ./output --strategy llm

# 查看策略分组预览（不写文件）
clawcodex pos convert ./sdk/ --strategy io --preview
```

`--preview` 输出示例：
```
✅ 策略预览: IO_RELATION
   Source Components: 50
   Merged Skills: 7
   Agent file count: 8 (7 + 1 overview)
   Agent 缩减率: 86% (50 → 8)
   Skills:
     - io_group_3 (Operations with types: str, int): extract_frames, convert_format
     - io_group_5 (Operations with types: Path, dict): load_video, save_metadata
     - ...
```

##### 总览 Agent 对分组策略的自适应

总览 Agent (`clawcodex-overview.md`) 会根据实际分组结果自动调整：

```python
# agent_md_writer.py:write_overview_agent()
# 通过 component_agents 列表反映实际 Agent 数量
# 如果 strategy=io 将 50 个组件合并为 7 个 Agent，
# overview 的委派指令就只包含 7 个 @agent-xxx 引用
```

总览 Agent 的 `when_to_use` 和 `all_skills` 列表也会反映真实的分组结构，不会出现 "概述 50 个模块但实际只有 7 个 Agent" 的语义错位。

##### 实现架构

```
CLI (commands.py)
    │
    ▼
group_source_components(components, strategy=GroupStrategy.IO_RELATION)
    │
    ├── SkillGrouper._component_group()   ── 每个组件 → 一个 SkillSpec
    ├── SkillGrouper._static_group()      ── MappingRule 关键字匹配
    ├── SkillGrouper._io_relation_group() ── 参数类型聚类
    └── SkillGrouper._group_with_llm()    ── LLM 语义分组 (placeholder → _static_group)
    │
    ▼
AgentMarkdownWriter.write_agent() × N + write_overview_agent() × 1
```

##### 与其他特性的协同

| 特性 | 关系 |
|------|------|
| **F-50** | F-55 是 F-50 的增强子集，同一组文件（`skill_grouper.py` 新增 3 个策略方法，`commands.py` 新增 `--strategy` 参数） |
| **F-52 (SDK→Tool 注册)** | 如果 F-52 落地，工具注册到 `ToolRegistry` 后 Overview Agent 可直接调用工具，届时策略重要性下降（可从"必须合并"降级为"可选的性能优化"） |
| **总览 Agent 默认加载** | `default_agent.py` 的 `resolve_default_agent()` 自动加载 `clawcodex-overview.md`，所有策略的输出均兼容此机制 |

##### Agent 数量量化对比（参考值）

| SDK 规模 | 模块数 | COMPONENT | KEYWORD | IO_RELATION | LLM |
|---------|:-----:|:---------:|:-------:|:-----------:|:---:|
| 小型 SDK | 5 | 6 (5+1) | 3 (2+1) | 4 (3+1) | 2 (1+1) |
| 中型 SDK | 20 | 21 (20+1) | 5 (4+1) | 8 (7+1) | 4 (3+1) |
| 大型 SDK | 50 | 51 (50+1) | 6 (5+1) | 9 (8+1) | 5 (4+1) |
| 巨型 SDK | 200+ | 201+ | 10 (9+1) | 15 (14+1) | 8 (7+1) |

> 注：KEYWORD 数据假设预置了匹配规则的分类；IO_RELATION 假设 60% 的方法共享参数类；LLM 为预估上限。

##### 风险与约束

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `IO_RELATION` 分组名称机械（`io_group_1`） | 用户难以理解分组含义 | 使用参数类型签名作为 name 后缀，同时允许用户在 `--requirements` 中补充描述 |
| `LLM_SEMANTIC` 尚未接入真正 LLM | 行为等同 `_static_group`，产生误判 | `_group_with_llm` 标注 TODO；LLM 集成留到 F-52 Tool 注册阶段统一接入 |
| 策略切换后已生成的 Agent 定义不自动清理 | 残留旧 Agent 文件 | `pos convert --out` 先清空 `agents/` 目录再写入（当前 CLI 行为已覆盖） |
| `COMPONENT_GROUP` 的预期行为是每个模块 1 个 Agent，但 `SkillGrouper._component_group()` 不要求一对一 | 源码级的 `SourceComponent` 划分粒度不一定是模块，可能是类 | 文档明确：策略按 `SourceComponent` 操作，不是按文件 |

##### 已拟定的设计决定

| # | 决定 | 理由 |
|---|------|------|
| 1 | **COMPONENT_GROUP 为默认策略** | 向后兼容，现有 `pos convert` 用户不受影响 |
| 2 | **总览 Agent 始终生成** | 用户只需面对一个入口，底层 Agent 数量是内部实现细节 |
| 3 | **IO_RELATION 分组名加上类型签名** | 至少让人看出"这组操作为什么在一起" |
| 4 | **LLM_SEMANTIC 标注 TODO 暂不实现** | 真正依赖 LLM 的语义分类需要 F-52 的工具注册能力配合，目前用 `_static_group` fallback |
| 5 | **`--preview` 预览模式不属于核心能力** | 增加测试维护成本，用户可用 `--dry-run` (未来特性) 替代 |

##### 依赖与协同

- **依赖**：F-50（SourceCodeParser + AgentMarkdownWriter 是前置基础）
- **协同**：
  - F-52（Tool 注册 → 策略重要度柔性可调）
  - 总览 Agent 默认加载机制（`default_agent.py`）
- **不依赖**：F-37/F-38/F-39（独立功能）
- **启用条件**：`python3 -m pytest tests/misc/test_pos_converter_source_parser.py -q` 通过（已有测试覆盖）

---

### 4.3 Python SDK 方法注册为 Tool（F-52）

##### 背景

当前 SOP 转换器解析 Python 源码后，在 Agent 定义的 `tools:` 字段列出的方法名（如 `detect_modality`、`load_dataset`）仅仅是字符串。当 sub-agent 被启动后，它的可用工具列表只包含 clawcodex 内置工具（Read/Write/Bash 等），`detect_modality` 不在 `ToolRegistry` 中，sub-agent 无法直接调用。Agent 只能退而通过 `Bash` subprocess 手动执行对应 Python 函数。

##### 设计目标

1. 新增 `register_tool_from_function(func, name, description, tool_registry)` 机制，将任意 Python 可调用对象包装为标准 `Tool` 对象并注册。
2. 生成的 Agent markdown 中的 `tools:` 列表在加载时（`load_agents_dir.py` 或 `AgentBuilder` 持久化阶段）自动触发注册，使这些方法名变为可调用的工具。
3. 保持 `src/*` 零改动——所有新增代码落入 `extensions/pos_converter/`。

##### 架构

```
Tool 注册流程（F-52 新增路径）:

SOP convert ──> AgentMarkdownWriter ──> .claude/agents/*.md (tools: [detect_modality, ...])
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

### 4.4 Tool 自动暴露为 CLI 斜杠命令（F-53）
**状态**: 📋 规划中
**优先级**: P3
**目标**: 将注册到 `ToolRegistry` 的工具自动暴露为 REPL/TUI 中的 `/tool-name` 斜杠命令，使 SOP 生成的子 Agent 方法（如 `detect_modality`）同时可在 CLI 中作为常规命令直接调用。

##### 背景

当前 clawcodex 的 `/` 斜杠命令系统（`command_system`）只内置少量固定命令（`/goal`、`/permission`、`/provider`、`/model` 等）。SOP 生成的工具在注册为 `Tool` 后（F-52），sub-agent 可通过 tool call 间接使用，但人类用户在 REPL/TUI 中没有直接入口——他们既不能通过 `@detect_modality` 也不通过 `/detect_modality` 触发。这迫使每次工具调用都需要先经过 LLM 决策。

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

---

## 五、Cron 系统执行引擎


> 优先级: P0
> 状态: ✅ Phase A 已完成（REPL/TUI/headless 运行路径接线）；后续 Phase B~F 分阶段推进
> 目标: 完整还原 `claude-code-best` 的 Cron / scheduled-task 行为
> 下游边界: 业务实现默认进入 `clawcodex_ext/*`，`src/*` 仅允许 thin forwarding seams

### 5.1 背景与目标
本阶段不是新增一个简单的 `CronCreate/CronList/CronDelete` CRUD 工具，而是将 `claude-code-best` 中已经打通的定时任务系统完整迁移到 ClawCodex 的下游扩展层。最终用户应能在 REPL、TUI、headless/print 模式中创建、查看、删除和执行定时任务，并能查看定时任务触发后的运行状态与结果。

`claude-code-best` 的 Cron 行为跨越工具、存储、调度器、CLI skills、REPL/headless 执行队列、autonomy run 记录和 missed-task 安全确认。ClawCodex 当前已经有 `clawcodex_ext/cron_system/*` 的核心模块，但还没有把这些模块完整接入真实 CLI 运行路径，因此 F-22 的完成标准必须从“模块存在”提升为“端到端行为与 `claude-code-best` 对齐”。

### 5.2 参考实现边界
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

### 5.3 当前 ClawCodex 状态诊断
#### 5.3.1 fallback 工具层
`src/tool_system/tools/cron.py` 目前只是兼容用 fallback：

- 任务保存在 `ToolContext.crons` 的进程内 dict 中。
- `durable` 参数会被接受并返回，但不会写入 `.claude/scheduled_tasks.json`。
- 不验证 5 字段 cron 语义，只检查字符串非空。
- `humanSchedule` 直接返回原始 cron 字符串。
- 没有 scheduler，不会自动触发任务。
- `CronCreateTool` / `CronDeleteTool` 被标记为 read-only，但实际会修改上下文状态。

该层应继续保留为静态工具兼容 fallback，但不应作为完整 Cron 行为的实现主体。

#### 5.3.2 下游扩展核心模块
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

#### 5.3.3 关键运行路径断点
目前最大缺口是 runtime/frontend 接线：

1. `clawcodex_ext/runtime/context.py` 构造 `RuntimeContext`，调用 `replace_cron_tools(tool_registry)`，并 `attach_cron_runtime(runtime)`。
2. 但 `clawcodex_ext/frontend/repl.py`、`clawcodex_ext/frontend/headless.py`、`clawcodex_ext/frontend/tui.py` 只把 options 传给旧入口。
3. 旧入口内部又重新构造 `tool_registry` 和 `tool_context`，导致前一步准备好的 Cron replacement tools、scheduler、outbox 没有进入真实执行路径。
4. `attach_cron_runtime()` 默认 `autostart=False`，即便被挂载也不会启动 scheduler。
5. scheduler 触发后只是向 `tool_context.outbox` 追加 `cron_prompt` / `cron_missed` 事件，当前没有发现 REPL/TUI/headless drain outbox 并执行 prompt 的路径。

因此当前扩展 Cron 更接近“有测试覆盖的核心模块”，尚未达到 `claude-code-best` 的 CLI 级完整行为。

### 5.4 完整还原的目标行为
#### 5.4.0 2026-06 最新 CCB 对比缺口复核
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

### 5.5 目标架构
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

### 5.6 实施阶段
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

### 5.7 文件格式
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

### 5.8 测试计划
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

### 5.9 手工验收流程
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

### 5.10 实施顺序与完成标准
| 阶段 | 完成标准 |
|------|----------|
| A. Runtime 接线 | REPL/TUI/headless 真实路径使用扩展 Cron tools；scheduler 可按 frontend lifecycle 启停 |
| B. 存储模型 | session-only 与 durable 分离；文件 schema 兼容；CronCreate/List/Delete 行为对齐 |
| C. Scheduler | busy gate、lock、jitter、missed、expiry、reload、single dispatch 全部有测试 |
| D. 执行结果 | scheduled fire 可入队执行并生成可查询 run status |
| E. Skills | `/loop`、`/cron-list`、`/cron-delete` 用户路径可用 |
| F. Ownership | teammate/agent ownership 能力按当前 runtime 成熟度实现或明确阻塞依赖 |

F-22 不应在只有 `clawcodex_ext/cron_system` 单元测试通过时标记完成。完成标准必须是：从 CLI 用户路径创建的任务能够被真实 scheduler 触发、执行、记录结果，并可被用户查看和删除。

### 5.11 CCB 对比发现的补充缺口
> 以下缺口基于 2026-06 对 `claude-code-best` cron 系统的完整对比分析得出，多数未被 F-22 原有 Phase A~F 覆盖，需作为 F-22 的补充子任务纳入实施计划。
>
> **2026-06 实施状态**：G1、G2、G3、G4、G5、G6、G7、G8 全部完成（`clawcodex_ext/cron_system/` 改造 + 46 个新单元测试 + 90/90 cron 测试 + 231/231 orchestrator 测试通过；独立 verification agent 两次给出 PASS 判定）。详见各小节末"实施状态"。

#### 5.11.1 Feature Gate 系统——isKilled 运行时 kill 开关（F-22-G1）
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

#### 5.11.2 远程 Jitter 实时配置（F-22-G2）
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

#### 5.11.3 One-shot 反向 Jitter（整点提前）（F-22-G3）
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

#### 5.11.4 Permanent 免过期任务机制（F-22-G4）
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

#### 5.11.5 锁注册式清理与 PID 存活探测增强（F-22-G5）
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

#### 5.11.6 工具 Prompt 指引文档增强（F-22-G6）
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

#### 5.11.7 Analytics 遥测事件注入（F-22-G7）
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

#### 5.11.8 inFlight 防重复触发机制（F-22-G8）
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

#### 5.11.9 ClawCodex 已有但 CCB 缺失的优势特性（F-22-A1 ~ A5）
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

#### 5.11.10 补充缺口实施优先级矩阵
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

#### 5.11.11 分析缺口与已有 F22-R/G 交叉映射
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

---

## 六、会话恢复增强


### 6.1 问题现状
> 与 claude-code-best（CCB）对比，ClawCodex 的 TUI 会话恢复在以下方面存在特性缺口。CCB 提供了包括退出后打印 session 信息（用于 `--resume` 指定）、`--continue` 继续最近会话、以及 `--resume` 启动后完整加载历史会话信息且渲染格式保持一致（如同从未退出）的完整体验。

ClawCodex 已有会话恢复的基础框架（`Session.resume()`、`_sync_conversation_from_transcript()`、`ResumeConversation` 浏览器），但关键的 UX 细节未对齐。

### 6.2 CCB 对比发现的补充缺口
#### 6.2.1 缺口 1：退出时打印 Resume Hint（S-R1）
**CCB 行为**：所有退出路径（`/exit`、`Ctrl+C`、SIGTERM、failsafe 超时）最终都会调用 `gracefulShutdown` → `printResumeHint()`，在 TTY 主缓冲区打印：

```
Resume this session with: claude --resume <sessionId>
```

实现守卫：`process.stdout.isTTY && getIsInteractive() && !isSessionPersistenceDisabled()`。同时支持自定义标题（fallback UUID）。

**ClawCodex 现状**：~~仅在 `__FULL_EXIT__` 路径（Ctrl+B 完全退出）有打印 hint。普通退出（`/exit`、`Ctrl+C`）无任何打印，用户退出后无法知道 session ID。~~ ✅ 已修复（v2.16）：新增 `_print_resume_hint()` 方法（`clawcodex_ext/repl/core.py`），补充了所有退出路径的 hint 打印：REPL `/exit`（原已存在）、REPL `KeyboardInterrupt`、REPL `EOFError`、REPL Ctrl+B（`user_input is None`）、TUI `app.run()` 返回后（`clawcodex_ext/tui/entrypoint.py`、`clawcodex_ext/entrypoints/tui.py`）。通过 `register_cleanup`（`src/utils/graceful_shutdown.py`）注册 SIGTERM/SIGINT 的 session 保存 + hint 打印（`clawcodex_ext/frontend/repl_extensions.py`、`clawcodex_ext/tui/entrypoint.py`）。提示格式：`Resume this session with: clawcodex --resume <sessionId>`，仅 TTY 且有 session ID 时打印。

| 子项 | CCB | ClawCodex | 优先级 |
|------|:---:|:---------:|:------:|
| `/exit` 正常退出打印 | ✅ `printResumeHint()` | ✅ `handle_command` 路径 | P0 |
| `Ctrl+C` 退出打印 | ✅ | ✅ `KeyboardInterrupt` 路径 | P0 |
| SIGTERM 退出打印 | ✅ `gracefulShutdownSync` | ✅ `register_cleanup` + `_cleanup` | P1 |
| failsafe 超时退出打印 | ✅ failsafe timer | ❌ | P1 |
| 退出 alt-screen 后打印（确保主缓冲区可见） | ✅ `cleanupTerminalModes()` → hint | ✅ TUI entrypoint 后打印 | P1 |
| 仅 TTY + 交互 + 持久化启用时打印 | ✅ 三重守卫 | ✅ `isatty()` + session ID 守卫 | P0 |
| 支持自定义标题（fallback UUID） | ✅ `customTitle ? escaped : sessionId` | ❌ 只打印 session_id | P2 |

**涉及参考代码**：
- CCB: `src/utils/gracefulShutdown.ts` L141-176 `printResumeHint()`
- ClawCodex: `src/repl/core.py` L2143-2153 `__FULL_EXIT__` 路径

---

#### 6.2.2 缺口 2：Resume 后历史消息渲染不完整（S-R2）
**CCB 行为**：`--resume <sessionId>` 启动后，通过 `loadConversationForResume()` 加载完整 transcript，以 `initialMessages` 参数传入 `launchRepl()`。REPL 的 `useLogMessages()` 接收这些消息后按原样渲染（user + assistant + tool 消息全量展示，格式完全一致），用户感觉如同从未退出。

**ClawCodex 现状**：~~`_replay_history()`（`src/tui/app.py` L1108-1161）有 `if role == "user": continue` 跳过用户消息，认为"用户提示已经显示在输入行，不需要重复渲染"。导致 resume 后历史看起来残缺不全，只显示 assistant 回复，看不到用户之前说了什么。~~ ✅ 已修复（v2.16）：`_replay_history()` 改为通过 `self._repl_screen.transcript.append_user(text)` 渲染用户消息。REPL 路径（`ClawCodexExtREPL.__init__` + `ClawcodexREPL.run()`）本来就能正确渲染用户消息，无需修改。

| 子项 | CCB | ClawCodex | 优先级 |
|------|:---:|:---------:|:------:|
| user 消息完整渲染 | ✅ | ✅ `_replay_history` `append_user` | P0 |
| assistant 消息渲染 | ✅ | ✅ | ✅ |
| tool_use/tool_result 消息渲染 | ✅ | ⚠️ 部分 | P2 |
| 渲染格式保持退出前一致性 | ✅ `initialMessages` 直通 REPL | ⚠️ `_post_to_screen` 路径不同 | P1 |
| 一致性检查（transcript ↔ 显示） | ✅ `checkResumeConsistency(chain)` | ❌ | P2 |
| 路径交叉调整（跨目录） | ✅ `_adjust_paths()` 完整实现 | ❌ 空函数（`return msg`） | P2 |
| 孤立 tool_use 修复 | ❌（不适用，CCB 同步 IO） | ✅ `_fix_orphaned_tool_uses()` | ✅ 已具备 |

**涉及参考代码**：
- CCB: `src/main.tsx` L3660-3718 `--continue` / `--resume` 启动路径
- CCB: `src/screens/components/chat/chat.ts` `useLogMessages(initialMessages)`
- ClawCodex: `src/tui/app.py` L1108-1161 `_replay_history()`

---

#### 6.2.3 缺口 3：`--continue` CLI 快捷命令（S-R3）
**CCB 行为**：`-c` / `--continue` 参数自动找回最近会话恢复，无需指定 session ID。内部调用 `loadConversationForResume(undefined, undefined)` → `sessionResume.latest()` 查找最新 transcript。同时支持与 `--fork-session` 组合使用，创建新 session ID 但保留历史上下文。

**ClawCodex 现状**：~~不支持 `--continue`。用户必须使用 `--resume <sessionId>` 并记住/查找 session ID。~~ ✅ 已修复（v2.16）：`clawcodex_ext/cli/parser.py` 新增 `-c` / `--continue` 参数；`clawcodex_ext/cli/dispatch.py` 在 arg parse 后自动调用 `SessionStorage.list_sessions(limit=1)` 查找最近会话并设置 `args.resume`，后续复用 `--resume` 的完整会话恢复路径。

| 子项 | CCB | ClawCodex | 优先级 |
|------|:---:|:---------:|:------:|
| `-c` / `--continue` 命令行参数 | ✅ | ✅ `-c` / `--continue` | P0 |
| 自动查找最近会话 | ✅ `loadConversationForResume(undefined)` | ✅ `SessionStorage.list_sessions(limit=1)` | P0 |
| 与 `--fork-session` 组合 | ✅ | ✅ `--fork-session` 参数 | P1 |
| 与 `/resume` 交互式浏览器互通 | ✅ | ✅ REPL + TUI 均支持 | P2 |

---

#### 6.2.5 缺口 5：REPL 端会话浏览器（S-R5）
**CCB 行为**：`--resume`（无 session ID）在终端模式（非 TUI）下同样会展示交互式会话浏览器。

**ClawCodex 现状**：~~缺少 REPL 端会话浏览器，强制切换到 TUI 模式。~~ ✅ 已修复（v2.16）：新增 `clawcodex_ext/repl/session_browser.py`，基于 Rich table + 终端输入实现交互式会话列表。支持：
- 显示 #、Session ID（前缀）、时间、最后用户输入、模型、消息数
- `#<num>` 按编号选择
- 输入 session ID（或前缀）匹配
- `/search <text>` 搜索会话内容（加载 transcript 全文搜索）
- `/show <num>` 显示完整 session ID

**涉及参考代码**：
- `clawcodex_ext/repl/session_browser.py` — 新文件
- `clawcodex_ext/frontend/repl.py` — 接入浏览器

---

#### 6.2.6 缺口 6：`--fork-session` 支持（S-R6）
**CCB 行为**：`--fork-session <sessionId>` 创建一个新 session ID 但保留原始会话的完整对话历史。

**ClawCodex 现状**：~~不支持 `--fork-session`。~~ ✅ 已修复（v2.16）：`clawcodex_ext/cli/parser.py` 新增 `--fork-session` 参数；`clawcodex_ext/runtime/context.py` 的 `RuntimeContext.build()` 在指定 fork 时加载原始会话的 conversation.messages 并复制到全新 Session 实例。

**涉及参考代码**：
- `clawcodex_ext/cli/parser.py` — 参数定义
- `clawcodex_ext/runtime/context.py` — fork 逻辑
- `clawcodex_ext/cli/dispatch.py` — 传递 fork_session_id

**涉及参考代码**：
- CCB: `src/main.tsx` L3660-3718
- CCB: `src/services/sessionManagement/sessionRestore.ts` `sessionResume.latest()`
- ClawCodex: `src/session/resume_conversation.py`（浏览器已实现）

---

#### 6.2.4 缺口 4：Resume 时元数据与状态恢复不完整（S-R4）
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

### 6.3 补充缺口实施优先级矩阵
| 编号 | 缺口 | 类别 | 优先级 | 预计工作量 | 状态 |
|:----:|------|------|:------:|:----------:|:----:|
| S-R1 | 所有退出路径打印 Resume Hint | UX 退出 | P0 | 1-2天 | ✅ 已解决 (v2.16) |
| S-R2 | `_replay_history()` 渲染 user 消息 | 恢复准确性 | P0 | 0.5-1天 | ✅ 已解决 (v2.16) |
| S-R3 | `--continue` 命令行支持 | CLI | P0 | 2-3天 | ✅ 已解决 (v2.16) |
| S-R5 | REPL 端会话浏览器 | 发现 | P0 | 2-3天 | ✅ 已解决 (v2.16) |
| S-R4-C | Resume 恢复 Cost 累计状态 | 状态恢复 | P1 | 1-2天 | ✅ 已解决 (`Session.resume` 已调用 `restore_cost_state_for_session`) |
| S-R6 | `--fork-session` 支持 | 会话管理 | P1 | 1-2天 | ✅ 已解决 (v2.16) |
| S-R4-M | Resume 恢复 session metadata | 状态恢复 | P2 | 1天 | ✅ 已解决 (v2.16): `ClawCodexExtREPL._load_session_metadata()` 加载 title/cwd/model/agent_name；`save_to_session_storage` 持久化 title/last_user_input；`chat()` 覆盖跟踪最后用户输入 |
| S-R4-A | Resume 恢复 Agent 设置 | 状态恢复 | P2 | 1-2天 | ✅ 已解决 (v2.16): `SessionMetadata.agent_name` 字段 + `_update_metadata_agent()` 持久化 agent 名称 |
| S-R4-T | 按自定义标题恢复 | 发现 | P2 | 1天 | ✅ 已解决 (v2.16): `ResumeConversation`（TUI）和 `session_browser.py`（REPL）均支持按标题搜索 |
| S-R4-CP | 交叉项目路径调整 | 准确性 | P2 | 1-2天 | ✅ 已解决 (v2.16): `_adjust_paths()` 完整实现 — 重写 tool_use 参数中 `path`/`file_path`/`directory` 等键；重写 tool_result content 中的路径文本；fallback 全局字符串替换 |
| S-R4-CK | Resume 一致性检查 | 健壮性 | P2 | 1天 | ✅ 已解决 (v2.16): `_check_chain_consistency()` 检查消息顺序 (user→assistant)、空 content、连续同名角色、链首/链尾角色 |
| S-R4-AT | Resume 指定消息位置 | 高级 | P3 | 2-3天 | ✅ 已解决 (v2.16): `--resume-session-at <msgId>` 参数 — 截断 conversation.messages 到指定索引 |
| Context Collapse 状态 | 按文件路径恢复 | `.jsonl` 文件路径 | ❌ (已归档，低优先级) |

> **建议实施顺序**：~~S-R1 → S-R2 → S-R3 → S-R4-C → S-R4-F → S-R4-T → S-R4-M → S-R4-A → S-R4-CP → S-R4-CK → S-R4-AT~~ ✅ 所有 P0-P3 缺口已在 v2.16 全部解决

---


*v2.15 更新：F-22 Phase A runtime-first 接线完成。`RuntimeContext.build()` 启动后台 cron 调度器；`src/repl/core.py` 注册 `replace_cron_tools()` + `attach_cron_runtime()` + `_drain_cron_outbox()`；REPL 主循环每条迭代前消费 `tool_context.outbox` 中的 `cron_prompt`/`cron_missed` 事件，注入为自动用户输入。Headless/TUI 通过共用 `RuntimeContext.build()` 路径获得调度器（TUI outbox drain 待后续）。271/271 orchestrator 测试通过。*

*v2.14 更新：新增 §3.17 F-48 src/ 核心路径二开修改解耦方案。分 Phase 0~3 四阶段，复用已有 Facade/子类覆盖/前端注册表三种解耦模式，目标：src/ 有功能修改的文件数从 10+ 降为 0。*

*2026-06-02 增量：F-45 落地。新增 `extensions/orchestrator/tool_event_log.py`（`ToolEventLog` 8 字段 frozen dataclass + `to_dict()`/`to_json()`）；`agent_runner.py:_append_tool_event_log` 落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，带嵌套 try/except + 50MB 单文件 rotate；`AgentSession.tool_events_path` 字段 + `session_context` 注入 `run_id` / `permission_mode` / `turn`；同步修复 `_handle_tool_call` 死代码调用链（run loop ToolCallEvent 分支原未调用，audit `approved` 字段会永远是 `None`——已加 `event = self._handle_tool_call(event, session_context)`）；`report_writer.RunReport.tool_events_path` 字段（末尾默认 `None`，向前兼容）+ `write()` dual-write NDJSON 到 `~/.clawcodex/reports/.../{run_id}.events.ndjson` + `_render_markdown` 追加 `Tool events: <path>` 行；`git_sync._write_report` 转发 `tool_events_path`；`WorkspaceConfig.gitignore_patterns` 默认 list 加 `.reports`；新增 `tests/test_orchestrator_f45_audit_bypass.py`（7 类 16 例）。回归：`tests/test_orchestrator_*.py` 271/271 + `tests/manual_e2e_f38.py` 4/4 + 新增 16/16 — 共 291 例全绿。*

*版本 v2.13 更新：新增 §3.1.10 Tool-call 审计旁路设计（F-45，📋 设计完成，P1）。在 `agent_runner._handle_tool_call` 后加 NDJSON 旁路落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，与 permission_mode 解耦（bypass / dontAsk / acceptEdits / default 四种 mode 一视同仁全写）；扩展 `report_writer.RunReport.tool_events_path` 字段与 markdown 模板登记路径；dual-write 到 `~/.clawcodex/reports/.../{run_id}/` 持久化层。NDJSON 每行 8 字段：ts / tool / params / approved / deny_reason / permission_mode / turn / session_run_id。修复 TS 注释 "bypass = no logging" 在 Python 端的事实偏差——ApprovalPolicy 一直在跑，只是决策没落盘。*

*版本 v2.13 更新：新增 §5.2 permission_mode enum 正交拆分设计（F-46，⏳ 规划中，P2）。把 `permission_mode` 混合 enum 拆为三个正交字段 `interactive: bool` / `default_decision: Literal["allow","deny","ask"]` / `audit_log: Literal["none","minimal","full"]`。F-46.0（v2.13）只拆 `audit_log`，依赖 F-45 落地后端到端验证；`permission_mode` 保留为 backward-compat shim 标 deprecated。F-46.1（v2.15+）拆其余两字段，F-46.2（v2.16+）移除 `permission_mode`。三字段组合爆炸风险用 `validate()` 互斥规则 + 启动 warning 缓解。*

*F-47.1 (2026-06-02) v2.13 hotfix：F-47 原本保留的顶层 `settings.permission_mode` back-compat 读取通道在项目尚未发布的前提下直接删除（`SettingsSchema.permission_mode` 字段保留为兼容形态但启动时不再被读）。F-46 计划中的"标 deprecated → 打 warning → 移除"路径因此提前在 v2.13 完成第一步（直接删读取），F-46.2 的 deprecation 步骤 N/A。*

*版本 v2.0 更新：新增 F-35 二开特性可切换架构设计，Feature Toggle 系统 + 584 个内联修改文件特性提取方案。*

*版本 v2.3 更新：新增 3.1.5 Orchestrator 验证与报告闭环设计（F-38）。Sub-A 在 `HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点，git_sync 在 commit/push 前后自动跑 verification gate（默认 `pytest -x`，用户可配 `test_command`）；Sub-B 新增 `report_writer` 生成 Markdown/JSON 报告，`IssueRecord` 增 `report_path` 字段，`git_sync._build_pr_body` 改模板插值；Sub-C 抽象 `TrackerAdapter.update_pull_request`，GitCode 客户端实现 `PATCH /repos/{owner}/{repo}/pulls/{id}`，把报告回写到 PR body 并合并为单条汇总评论；Sub-D 修复 `progress_reporter` 死代码，PhaseComplete 接入 ndjson event log。*

*版本 v2.4 更新：新增 3.1.6 Issue 重跑入口设计（F-39）。三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发，Sub-B 重置重跑，Sub-C follow-up 叠 commit，Sub-D comment 命令解析，Sub-E CLI 兜底，Sub-F 限频+角色校验。*

---

---

## 七、CCB 对标缺口补缺

> **说明**：本章合并原 §九（CCB 对标特性补缺规划）与 §十（Python 生态特性补缺规划），按子领域分组呈现。原 §九 的 CCB 覆盖状态总表、实施建议顺序、clawcodex 领先优势等内容保留在本章末尾。

---
### 7.0 Python 生态特性补缺规划（合并来源：原 §十）


> 本节规划 CCB（claude-code-best）对标发现的 clawcodex 特性缺口。
> F-60~F-67 均参照 CCB 对应功能设计，以确保功能完整对标为目标。
>
> 注意：以下为 `CCB_MIGRATION_DESIGN.md`（CCB CLI/TUI → ClawCodex 迁移设计文档）各子系统在
> FEATURE_PLAN.md 中的覆盖状态评估，以及当前代码库的落地情况。

### CCB 子系统覆盖状态总览

根据对 `CCB_MIGRATION_DESIGN.md` 各子系统的逐一比对，以及代码库 `src/` 目录的实际实现检查，
以下表格反映当前覆盖状况（2026-06）：

| 子系统 | 迁移文档章节 | FEATURE_PLAN 条目 | 代码库状态 | 备注 |
|--------|-----------|-------------------|:----------:|------|
| **Bootstrap STATE 全局状态** | §3.1.1~3.1.5 | 无对应 F-number | ✅ 部分实现 | `src/state/app_state.py`, `cache_state.py`, `session_start.py`；mig 设计 8 子模块当前实装 3 个 |
| **Signal 事件通知** | §3.1.4 | 无对应 F-number | ✅ 已实现 | `src/utils/signal.py`（96 行，含 Signal dataclass + create_signal factory） |
| **两级状态架构** | §3.1.5 | 无对应 F-number | ✅ 已实现 | Bootstrap State (`src/state/`) + AppState Store (`src/state/app_state.py`) 已分离 |
| **AppState Store** | §3.1.6 | 无对应 F-number | ✅ 已实现 | `src/state/app_state.py` — `Store[AppState]` pub/sub + side-effect router |
| **Overlay/Escape 协调** | §3.1.7 | 无对应 F-number | ✅ 已实现 | Textual Screen 原生管理；`src/tui/app.py`（ext 中）处理 overlay 堆栈 |
| **命令系统框架** | §3.2.1~3.2.4 | 无对应 F-number | ⏳ 无独立 `src/commands/` | 斜杠命令通过 `src/command_system/` 路由，非迁移设计中的 Command dataclass 体系 |
| **Coordinator 系统** | §3.3.1~3.3.4 | **F-41 ✅ 已完成** | ✅ 已实现 | `src/coordinator/mode.py`, `prompt.py`, `worker_agent.py`；含 is_coordinator_mode、filter tools、worker agent 定义、system prompt |
| **TUI 屏幕层次** | §3.4.1 | 无对应 F-number | ✅ 已实现 | `src/tui/screens/` 含 14+ 个 Screen（repl, model_picker, theme_picker, permission_modal, diff_dialog, cost_threshold, history_search, resume_conversation 等）；`src/tui/state.py` 和 `app.py` 为 ext facade |
| **vim mode** | §3.4.3 | 无对应 F-number | ✅ 已实现 | `src/tui/vim.py` + 7 个 vim 辅助模块（buffer, find, operators, visual, state, persistent, text_objects） |
| **Provider Registry** | §3.5.1 | F-72（部分重叠） | ✅ 已实现 | Provider 系统在 `src/providers/` 中已有实现 |
| **MCP Client** | §3.5.2 | 无对应 F-number | ✅ 已实现 | `src/services/mcp/` 含 31 个文件，client, server, manager, transport, auth, telemetry 等 |
| **Auth 服务** | §3.5 | 无对应 F-number | ✅ 已实现 | `src/services/auth/` 含 auth.py, oauth 相关模块 |
| **Bridge 桥接** | §3.4.2 | 无对应 F-number | ✅ 已实现 | `src/services/bridge/` 含 auth, session, transport 模块 |
| **Swarm/Team 系统** | §3.3（协作） | 无对应 F-number | ✅ 已实现 | `src/services/swarm/` 含 mailbox, permissions, agent_name_registry, team_fi 等 |
| **Pipes IPC + LAN 群控** | §3.5.3 / §8.1 | **F-60 ⏳ 待开始** | ❌ 未实现 | 需要在 `src/services/pipes/` 或等效位置实现 |
| **Plugin 系统** | §3.5 | **F-70 ⏳ 待开始** | ❌ 未实现 | 规划在 §十 |
| **Computer Use** | §8.2 | **F-61 ⏳ 待开始** | ❌ 未实现 | 截屏/键鼠/窗口/剪贴板 |
| **Chrome 自动化** | §8.2 | **F-62 ⏳ 待开始** | ❌ 未实现 | 浏览器控制 |
| **Channels 通知** | — | **F-63 ⏳ 待开始** | ❌ 未实现 | 邮件/Slack/Discord/飞书通知 |
| **Voice Mode** | — | **F-64 ⏳ 待开始** | ❌ 未实现 | `src/services/voice/` 骨架存在，功能待实装 |
| **Langfuse** | — | **F-65 ⏳ 待开始** | ❌ 未实现 | `src/services/analytics/` 骨架存在 |
| **ACP 协议** | §8.3 | **F-66 ⏳ 待开始** | ❌ 未实现 | Agent Communication Protocol |
| **Buddy/Proactive** | — | **F-67 ⏳ 待开始** | ❌ 未实现 | 伴侣模式 |
| **Notifier + PreventSleep** | §8.3 | 无对应 F-number | ❌ 未实现 | 通知与防休眠服务 |
| **150+ CCB 特有工具** | §8.2 | **F-71 ⏳ 待开始**（需展开工具清单） | ⏳ 部分 | 见下方 F-71 子特性表 |


### 7.1 进程间通信与远程控制

#### F-60: Pipe IPC + LAN 群控系统

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

#### 核心数据模型

```python
# src/services/pipe_ipc/models.py
from dataclasses import dataclass, field
from enum import Enum
import uuid
from datetime import datetime

class PipeMessageType(Enum):
    HEARTBEAT = "heartbeat"               # 心跳
    COMMAND = "command"                    # 命令消息
    REPLY = "reply"                        # 回复
    BROADCAST = "broadcast"                # 广播
    PERMISSION_REQUEST = "permission_req"  # 权限请求（Slave→Master）
    PERMISSION_GRANT = "permission_grant"  # 权限授权（Master→Slave）
    PERMISSION_DENY = "permission_deny"    # 权限拒绝
    PEER_JOIN = "peer_join"                # 实例加入
    PEER_LEAVE = "peer_leave"              # 实例离开
    AGENT_STREAM = "agent_stream"          # Agent 输出流转发

@dataclass
class PipeMessage:
    """统一线缆格式（JSON 序列化）。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    type: PipeMessageType = PipeMessageType.COMMAND
    source_id: str = ""                    # 发送方实例 ID
    target_id: str = ""                    # 接收方实例 ID（空=广播）
    payload: dict = field(default_factory=dict)  # 消息体
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    ttl: int = 3                          # 跳数（防止广播环）
    permission_token: str | None = None    # 权限转发令牌

@dataclass
class PipePeer:
    """管道对端实例信息。"""
    instance_id: str
    hostname: str
    pid: int
    version: str
    addr: str                              # UDS path 或 IP:port
    transport: Literal["uds", "tcp", "udp"] = "uds"
    last_seen: float = 0.0                 # time.monotonic()
    is_master: bool = False
    capabilities: list[str] = field(default_factory=list)
```

#### 核心接口

```python
# src/services/pipe_ipc/base.py
from abc import ABC, abstractmethod

class PipeTransport(ABC):
    """管道传输层抽象（UDS / TCP / UDP）。"""

    @abstractmethod
    async def send(self, msg: PipeMessage) -> None:
        """发送消息到对端。"""

    @abstractmethod
    async def receive(self) -> PipeMessage | None:
        """接收消息（非阻塞或超时）。"""

    @abstractmethod
    async def broadcast(self, msg: PipeMessage) -> None:
        """广播消息到所有已连接对端。"""

    @abstractmethod
    async def close(self) -> None: ...


class PipeRegistry:
    """对端实例注册表（内存 + 本地持久化）。"""

    def __init__(self, data_dir: str = "~/.clawcodex/pipes"):
        self._peers: dict[str, PipePeer] = {}
        self._data_dir = Path(data_dir).expanduser()

    def register(self, peer: PipePeer) -> None:
        """注册（或更新）一个对端实例。"""
        self._peers[peer.instance_id] = peer
        self._persist()

    def unregister(self, instance_id: str) -> None:
        self._peers.pop(instance_id, None)
        self._persist()

    def get(self, instance_id: str) -> PipePeer | None:
        return self._peers.get(instance_id)

    def list_peers(self) -> list[PipePeer]:
        return list(self._peers.values())

    def _persist(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._data_dir / "peers.json"
        data = [dataclasses.asdict(p) for p in self._peers.values()]
        path.write_text(json.dumps(data, indent=2, default=str))


class PipePermissionForwarder:
    """权限转发器：Slave 请求→Master 确认→Slave 执行。"""

    def __init__(self, transport: PipeTransport, my_id: str, master_id: str):
        self._transport = transport
        self._my_id = my_id
        self._master_id = master_id
        self._pending: dict[str, asyncio.Future] = {}

    async def request_permission(self, action: str, **kwargs) -> bool:
        """请求 Master 授权执行敏感操作。

        向 Master 发送 PERMISSION_REQUEST，等待 PERMISSION_GRANT/DENY。
        """
        req = PipeMessage(
            type=PipeMessageType.PERMISSION_REQUEST,
            source_id=self._my_id,
            target_id=self._master_id,
            payload={"action": action, **kwargs},
        )
        fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._pending[req.id] = fut
        await self._transport.send(req)
        try:
            return await asyncio.wait_for(fut, timeout=30.0)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(req.id, None)

    async def handle_permission_response(self, msg: PipeMessage) -> None:
        fut = self._pending.get(msg.id)
        if fut and not fut.done():
            fut.set_result(msg.type == PipeMessageType.PERMISSION_GRANT)
```

#### 服务端示例（UDS）

```python
# src/services/pipe_ipc/server.py
import asyncio, json
from pathlib import Path

class UdsServer:
    """Unix Domain Socket 服务端（Master 实例）。"""

    def __init__(self, sock_path: str = "~/.clawcodex/pipes/master.sock"):
        self._sock_path = Path(sock_path).expanduser()
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self.registry = PipeRegistry()
        self._router = PipeRouter(self)

    async def start(self):
        self._sock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._sock_path.exists():
            self._sock_path.unlink()
        server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._sock_path),
        )
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader,
                               writer: asyncio.StreamWriter):
        """处理单个客户端连接（JSON 行协议）。"""
        peer_addr = writer.get_extra_info("peername")
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                msg = PipeMessage(**json.loads(data))
                await self._router.route(msg)
        finally:
            writer.close()
            await writer.wait_closed()

    def broadcast(self, msg: PipeMessage) -> None:
        """向所有连接的客户端广播。"""
        data = json.dumps(dataclasses.asdict(msg)) + "\n"
        for cid, w in list(self._clients.items()):
            try:
                w.write(data.encode())
            except Exception:
                self._clients.pop(cid, None)


class PipeRouter:
    """消息路由器：按类型和 target_id 分发。"""

    def __init__(self, server: UdsServer):
        self._server = server

    async def route(self, msg: PipeMessage) -> None:
        """路由消息到适当处理器。"""
        if msg.type == PipeMessageType.HEARTBEAT:
            await self._handle_heartbeat(msg)
        elif msg.type == PipeMessageType.PERMISSION_REQUEST:
            await self._handle_permission_request(msg)
        elif msg.type == PipeMessageType.COMMAND:
            await self._handle_command(msg)
        elif msg.type == PipeMessageType.AGENT_STREAM:
            await self._forward_agent_stream(msg)
```

#### 客户端示例

```python
# src/services/pipe_ipc/client.py
class PipeClient:
    """Pipe 客户端（Slave 实例或 Worker）。"""

    def __init__(self, instance_id: str, master_sock: str):
        self.instance_id = instance_id
        self._master_sock = Path(master_sock).expanduser()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self):
        """连接到 Master UDS。"""
        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self._master_sock),
        )
        # 立即发送 PEER_JOIN
        await self.send(PipeMessage(
            type=PipeMessageType.PEER_JOIN,
            source_id=self.instance_id,
            payload={"pid": os.getpid(), "hostname": socket.gethostname()},
        ))
        # 启动心跳
        asyncio.create_task(self._heartbeat_loop())

    async def send(self, msg: PipeMessage) -> None:
        data = json.dumps(dataclasses.asdict(msg)) + "\n"
        self._writer.write(data.encode())
        await self._writer.drain()

    async def _heartbeat_loop(self, interval: float = 5.0):
        while True:
            await asyncio.sleep(interval)
            await self.send(PipeMessage(
                type=PipeMessageType.HEARTBEAT,
                source_id=self.instance_id,
            ))

    async def listen(self):
        """持续读取 Master 分发过来的消息。"""
        while True:
            data = await self._reader.readline()
            if not data:
                break
            msg = PipeMessage(**json.loads(data))
            # 交由上层处理
            yield msg

    async def close(self):
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
```

#### 架构图

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

#### 使用模式

```python
# 在 Manager/Worker 通信中集成 Pipe IPC
# Manager 启动时：
#   server = UdsServer()
#   asyncio.create_task(server.start())

# Worker 启动时：
#   client = PipeClient(instance_id=worker_id, master_sock=...)
#   await client.connect()
#   async for msg in client.listen():
#       handle_message(msg)

# CLI /pipes 面板命令：
#   @app.command()
#   def pipes():
#       """显示所有已连接对端实例。"""
#       for peer in pipe_registry.list_peers():
#           echo(f"  {peer.instance_id} @ {peer.hostname}  [{peer.transport}]")
```

#### 依赖

- Python `asyncio` / `socket` / `multiprocessing`（标准库）
- UDP Multicast 使用标准 socket API
- TUI 扩展点用于 `/pipes` 面板（Textual Screen override）
- UDS 路径 `~/.clawcodex/pipes/*.sock`

---


### 7.2 浏览器与桌面操控

#### F-61: Computer Use 屏幕操控

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

#### 核心接口

```python
# src/services/computer_use/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ScreenRegion:
    """屏幕区域描述，用于定位元素。"""
    x: int = 0
    y: int = 0
    width: int = 1920
    height: int = 1080

class ScreenshotProvider(ABC):
    """跨平台截图统一接口。"""

    @abstractmethod
    def capture_fullscreen(self) -> bytes:
        """截取全屏，返回 PNG bytes。"""

    @abstractmethod
    def capture_region(self, region: ScreenRegion) -> bytes:
        """截取指定区域。"""

    @abstractmethod
    def capture_window(self, window_title: str) -> bytes | None:
        """按窗口标题截图，返回 None 表示窗口未找到。"""


class InputSimulator(ABC):
    """跨平台键鼠模拟统一接口。"""

    @abstractmethod
    def move_mouse(self, x: int, y: int) -> None: ...

    @abstractmethod
    def click(self, button: str = "left", x: int | None = None,
              y: int | None = None) -> None: ...

    @abstractmethod
    def double_click(self, x: int | None = None,
                     y: int | None = None) -> None: ...

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def press_key(self, key: str) -> None:
        """发送单个按键（如 'enter', 'escape', 'ctrl+c'）。"""

    @abstractmethod
    def scroll(self, dx: int = 0, dy: int = 1) -> None:
        """滚动鼠标滚轮。"""

    @abstractmethod
    def drag(self, start_x: int, start_y: int,
             end_x: int, end_y: int) -> None: ...


class ClipboardManager(ABC):
    """跨平台剪贴板统一接口。"""

    @abstractmethod
    def get_text(self) -> str: ...

    @abstractmethod
    def set_text(self, text: str) -> None: ...

    @abstractmethod
    def has_image(self) -> bool: ...


class WindowManager(ABC):
    """跨平台窗口管理统一接口。"""

    @abstractmethod
    def list_windows(self) -> list[dict]: ...

    @abstractmethod
    def focus_window(self, title: str) -> bool: ...

    @abstractmethod
    def resize_window(self, title: str,
                      width: int, height: int) -> bool: ...

    @abstractmethod
    def move_window(self, title: str, x: int, y: int) -> bool: ...

    @abstractmethod
    def close_window(self, title: str) -> bool: ...
```

#### Linux 实现示例（scrot + xdotool）

```python
# src/services/computer_use/platform/linux.py
import subprocess, tempfile
from pathlib import Path

class LinuxScreenshot(ScreenshotProvider):
    def capture_fullscreen(self) -> bytes:
        result = subprocess.run(["scrot", "-o", "-"], capture_output=True)
        return result.stdout  # PNG bytes

    def capture_region(self, region: ScreenRegion) -> bytes:
        result = subprocess.run(
            ["scrot", "-o", "-a", f"{region.x},{region.y},{region.width},{region.height}", "-"],
            capture_output=True,
        )
        return result.stdout

    def capture_window(self, window_title: str) -> bytes | None:
        # 使用 xdotool 查找窗口 ID
        wid = subprocess.run(
            ["xdotool", "search", "--name", window_title],
            capture_output=True, text=True,
        ).stdout.strip()
        if not wid:
            return None
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            subprocess.run(["import", "-window", wid, f.name], check=True)
            return Path(f.name).read_bytes()

class LinuxInput(InputSimulator):
    def move_mouse(self, x: int, y: int) -> None:
        subprocess.run(["xdotool", "mousemove", str(x), str(y)])

    def click(self, button: str = "left", x=None, y=None) -> None:
        btn = {"left": 1, "middle": 2, "right": 3}.get(button, "1")
        if x is not None and y is not None:
            subprocess.run(["xdotool", "mousemove", str(x), str(y)])
        subprocess.run(["xdotool", "click", str(btn)])

    def type_text(self, text: str) -> None:
        subprocess.run(["xdotool", "type", "--", text])

    def press_key(self, key: str) -> None:
        subprocess.run(["xdotool", "key", key])

    def scroll(self, dx=0, dy=1) -> None:
        subprocess.run(["xdotool", "click", "5" if dy > 0 else "4"])

    def drag(self, start_x, start_y, end_x, end_y):
        subprocess.run(["xdotool", "mousemove", str(start_x), str(start_y)])
        subprocess.run(["xdotool", "mousedown", "1"])
        subprocess.run(["xdotool", "mousemove", str(end_x), str(end_y)])
        subprocess.run(["xdotool", "mouseup", "1"])
```

#### macOS 实现示例（screencapture + pyobjc）

```python
# src/services/computer_use/platform/macos.py
import subprocess, Quartz

class MacOSScreenshot(ScreenshotProvider):
    def capture_fullscreen(self) -> bytes:
        result = subprocess.run(
            ["screencapture", "-c", "-x", "-t", "png", "-"],
            capture_output=True,
        )
        return result.stdout

    def capture_region(self, region: ScreenRegion) -> bytes:
        result = subprocess.run(
            ["screencapture", "-R", f"{region.x},{region.y},{region.width},{region.height}",
             "-x", "-t", "png", "-"],
            capture_output=True,
        )
        return result.stdout

    def capture_window(self, window_title: str) -> bytes | None:
        # 使用 pyobjc Quartz bindings
        options = Quartz.CGWindowListOptionOnScreenOnly
        window_list = Quartz.CGWindowListCopyWindowInfo(options, 0)
        for win in window_list:
            name = win.get("kCGWindowName", "") or ""
            if window_title in name:
                wid = win["kCGWindowNumber"]
                image = Quartz.CGWindowListCreateImage(
                    Quartz.CGRectNull,
                    Quartz.kCGWindowListOptionIncludingWindow,
                    wid,
                    Quartz.kCGWindowImageDefault,
                )
                # 转为 PNG bytes
                return self._cgimage_to_png(image)
        return None

class MacOSInput(InputSimulator):
    """使用 CGEvent（pyobjc）。"""
    def move_mouse(self, x: int, y: int) -> None:
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, (x, y), 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    # ...（其他方法类似）
```

#### 集成到 Tool 工厂

```python
# src/services/computer_use/__init__.py
def build_computer_use_tools() -> list[Tool]:
    """创建 Computer Use 工具集（由 build_tool() 调用）。"""
    import platform as pf
    system = pf.system().lower()
    if "linux" in system:
        screenshot = LinuxScreenshot()
        input_sim = LinuxInput()
    elif "darwin" in system:
        screenshot = MacOSScreenshot()
        input_sim = MacOSInput()
    elif "windows" in system:
        screenshot = WindowsScreenshot()   # 使用 pywin32 实现
        input_sim = WindowsInput()
    else:
        return []  # 不支持的平台

    return [
        Tool(name="computer_screenshot",
             description="截取桌面屏幕画面",
             parameters={...},  # region/window 参数
             call=lambda **kw: screenshot.capture_fullscreen()),
        Tool(name="computer_mouse",
             description="操控鼠标移动/点击",
             parameters={...},
             call=lambda **kw: input_sim.click(**kw)),
        Tool(name="computer_keyboard",
             description="键盘输入/按键",
             parameters={...},
             call=lambda **kw: input_sim.type_text(**kw)),
        Tool(name="computer_window",
             description="窗口管理（列表/聚焦/移动/关闭）",
             parameters={...},
             call=lambda **kw: WindowManager.focus_window(**kw)),
    ]
```

#### 依赖

- Linux: `scrot` / `xdotool`（可选 `ydotool` 用于 Wayland）
- macOS: 系统内置 `screencapture` + `Quartz`/`CGEvent`（via `pyobjc` 或 `subprocess`）
- Windows: `pywin32` + `PIL`（`python -m pip install pywin32 pillow`）

---

#### F-62: Chrome 浏览器自动化控制

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

#### 核心数据模型

```python
# src/services/chrome/models.py
from dataclasses import dataclass
from enum import Enum

class ChromeActionType(Enum):
    NAVIGATE = "navigate"           # 导航到 URL
    CLICK = "click"                 # 点击元素
    TYPE = "type"                   # 输入文本
    SELECT = "select"               # 选择下拉选项
    SCREENSHOT = "screenshot"       # 截图
    EVAL_JS = "eval_js"             # 执行 JavaScript
    GET_HTML = "get_html"           # 获取页面 HTML
    GET_TEXT = "get_text"           # 获取页面可见文本
    HOVER = "hover"                 # 悬停
    SCROLL = "scroll"               # 滚动

@dataclass
class ChromeActionResult:
    success: bool
    data: str | bytes | None = None   # 文本/截图 bytes/JSON
    error: str | None = None
    screenshot_path: str | None = None  # GIF 录制路径
```

#### 核心接口

```python
# src/services/chrome/base.py
from abc import ABC, abstractmethod

class ChromeController(ABC):
    """浏览器控制抽象接口（Playwright 实现）。"""

    @abstractmethod
    async def start(self, headless: bool = True) -> None:
        """启动浏览器实例。"""

    @abstractmethod
    async def stop(self) -> None:
        """关闭浏览器。"""

    @abstractmethod
    async def navigate(self, url: str) -> ChromeActionResult:
        """导航到指定 URL。"""

    @abstractmethod
    async def click(self, selector: str) -> ChromeActionResult:
        """点击页面中的 CSS 选择器元素。"""

    @abstractmethod
    async def type_text(self, selector: str, text: str,
                        clear_first: bool = True) -> ChromeActionResult:
        """在输入框中输入文本。"""

    @abstractmethod
    async def select_option(self, selector: str, value: str) -> ChromeActionResult:
        """选择下拉框选项。"""

    @abstractmethod
    async def screenshot(self, selector: str | None = None) -> ChromeActionResult:
        """截取页面/元素截图。"""

    @abstractmethod
    async def eval_js(self, script: str) -> ChromeActionResult:
        """在页面中执行 JavaScript 并返回结果。"""

    @abstractmethod
    async def get_visible_text(self) -> ChromeActionResult:
        """获取页面可见文本（用于 Agent 理解页面内容）。"""

    @abstractmethod
    async def start_recording(self, output_path: str) -> None:
        """开始录制页面操作为 GIF。"""

    @abstractmethod
    async def stop_recording(self) -> str:
        """停止录制并返回 GIF 文件路径。"""


class ChromeToolFactory:
    """创建 Agent 可用的 Chrome Tool 列表。"""

    @staticmethod
    def build_tools(controller: ChromeController) -> list[Tool]:
        """由 build_tool() 调用，注入已初始化的 ChromeController。"""
        return [
            Tool(name="chrome_navigate",
                 description="在浏览器中导航到指定 URL",
                 parameters={"url": {"type": "string", "description": "目标 URL"}},
                 call=lambda url: controller.navigate(url)),
            Tool(name="chrome_click",
                 description="点击页面中的对应元素",
                 parameters={"selector": {"type": "string"}},
                 call=lambda selector: controller.click(selector)),
            Tool(name="chrome_type",
                 description="在输入框中输入文本",
                 parameters={
                     "selector": {"type": "string"},
                     "text": {"type": "string"},
                     "clear_first": {"type": "boolean", "default": True},
                 },
                 call=lambda **kw: controller.type_text(**kw)),
            Tool(name="chrome_screenshot",
                 description="截取当前页面截图",
                 parameters={},
                 call=lambda: controller.screenshot()),
            Tool(name="chrome_eval_js",
                 description="在页面中执行自定义 JavaScript",
                 parameters={"script": {"type": "string"}},
                 call=lambda script: controller.eval_js(script)),
        ]
```

#### Playwright 实现示例

```python
# src/services/chrome/playwright_impl.py
from playwright.async_api import async_playwright, Page

class PlaywrightController(ChromeController):
    """基于 Playwright 的浏览器控制器。"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page: Page | None = None
        self._recording = False

    async def start(self, headless: bool = True) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=headless)
        self._page = await self._browser.new_page()

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def navigate(self, url: str) -> ChromeActionResult:
        try:
            await self._page.goto(url, wait_until="domcontentloaded")
            return ChromeActionResult(success=True, data=url)
        except Exception as e:
            return ChromeActionResult(success=False, error=str(e))

    async def click(self, selector: str) -> ChromeActionResult:
        try:
            await self._page.click(selector)
            return ChromeActionResult(success=True)
        except Exception as e:
            return ChromeActionResult(success=False, error=str(e))

    async def screenshot(self, selector: str | None = None) -> ChromeActionResult:
        try:
            if selector:
                el = await self._page.query_selector(selector)
                data = await el.screenshot() if el else None
            else:
                data = await self._page.screenshot(full_page=True)
            return ChromeActionResult(success=True, data=data)
        except Exception as e:
            return ChromeActionResult(success=False, error=str(e))

    async def eval_js(self, script: str) -> ChromeActionResult:
        try:
            result = await self._page.evaluate(script)
            return ChromeActionResult(success=True, data=str(result))
        except Exception as e:
            return ChromeActionResult(success=False, error=str(e))
```

#### 集成到 Tool 工厂

```python
# src/services/chrome/__init__.py
_chrome_controller: PlaywrightController | None = None

async def ensure_chrome():
    """延迟初始化 Chrome 控制器。"""
    global _chrome_controller
    if _chrome_controller is None:
        _chrome_controller = PlaywrightController()
        await _chrome_controller.start()
    return _chrome_controller

def build_chrome_tools() -> list[Tool]:
    """创建 Chrome Tool 集（由 build_tool() 调用）。"""
    return ChromeToolFactory.build_tools(ensure_chrome())
```

#### 依赖

- `playwright`（`pip install playwright && playwright install chromium`）
- `PIL` 用于 GIF 录制合成（`pip install pillow`）
- Chrome MCP 扩展作为可选备选方案

---


### 7.3 通知与语音

#### F-63: Channels 频道通知系统

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

#### 核心数据模型

```python
# src/services/channels/models.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class ChannelType(Enum):
    FEISHU = "feishu"
    SLACK = "slack"
    DISCORD = "discord"
    WECHAT = "wechat"
    MCP_PUSH = "mcp_push"

class MessageLevel(Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    SUCCESS = "success"

@dataclass
class ChannelMessage:
    text: str
    level: MessageLevel = MessageLevel.INFO
    title: str | None = None          # 可选消息标题
    markdown: bool = True              # 是否支持 Markdown
    attachments: list[dict[str, Any]] | None = None  # 可选附件/卡片
    metadata: dict[str, Any] | None = None

@dataclass
class ChannelConfig:
    type: ChannelType
    webhook_url: str
    name: str                         # 频道别名（如"线上报警"）
    enabled: bool = True
    extra: dict[str, Any] | None = None  # 渠道特定配置（如飞书签名密钥）
```

#### 抽象接口

```python
# src/services/channels/base.py
from abc import ABC, abstractmethod

class BaseChannel(ABC):
    """消息通道抽象基类。"""

    def __init__(self, config: ChannelConfig):
        self.config = config

    @abstractmethod
    async def send(self, message: ChannelMessage) -> bool:
        """发送消息到目标频道。返回是否成功。"""

    @abstractmethod
    async def validate(self) -> bool:
        """校验 Webhook 配置是否有效。"""

    @classmethod
    @abstractmethod
    def format_message(cls, message: ChannelMessage) -> Any:
        """将 ChannelMessage 格式化为目标平台消息体。"""


class ChannelManager:
    """统一注册和分发消息到所有活跃频道。"""

    def __init__(self):
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.config.name] = channel

    async def broadcast(self, message: ChannelMessage) -> dict[str, bool]:
        """向所有已注册频道广播消息。"""
        results = {}
        for name, ch in self._channels.items():
            if ch.config.enabled:
                results[name] = await ch.send(message)
        return results

    async def send_to(self, name: str, message: ChannelMessage) -> bool:
        """向指定频道发送消息。"""
        ch = self._channels.get(name)
        if ch and ch.config.enabled:
            return await ch.send(message)
        return False
```

#### 飞书实现示例

```python
# src/services/channels/feishu.py
import hashlib, base64, hmac, json, time

class FeishuChannel(BaseChannel):
    """飞书机器人 Webhook 通道。"""

    def format_message(self, message: ChannelMessage) -> dict:
        return {
            "msg_type": "interactive" if message.title else "text",
            "content": json.dumps({
                "title": message.title or "",
                "text": message.text,
            }) if message.title else {"text": message.text},
        }

    async def send(self, message: ChannelMessage) -> bool:
        body = self.format_message(message)
        timestamp = str(int(time.time()))
        sign = self._sign(timestamp) if self.config.extra.get("secret") else None
        if sign:
            body["timestamp"] = timestamp
            body["sign"] = sign
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.config.webhook_url, json=body)
            return resp.status_code == 200

    def _sign(self, timestamp: str) -> str:
        secret = self.config.extra["secret"]
        string_to_sign = f"{timestamp}\n{secret}"
        return base64.b64encode(
            hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
        ).decode()

    async def validate(self) -> bool:
        return await self.send(ChannelMessage(text="✅ 频道连接测试成功"))
```

#### 集成到 Tool 工厂

```python
# src/services/channels/__init__.py
from clawcodex.config import settings

def init_channels() -> ChannelManager:
    """从配置文件中读取 Channel 配置并初始化。"""
    manager = ChannelManager()
    for cfg_dict in settings.get("channels", []):
        config = ChannelConfig(**cfg_dict)
        channel = _build_channel(config)
        if channel:
            manager.register(channel)
    return manager

def _build_channel(config: ChannelConfig) -> BaseChannel | None:
    mapping = {
        ChannelType.FEISHU: FeishuChannel,
        ChannelType.SLACK: SlackChannel,
        ChannelType.DISCORD: DiscordChannel,
        ChannelType.WECHAT: WechatChannel,
    }
    cls = mapping.get(config.type)
    return cls(config) if cls else None

# Agent Tool 注册
def build_channel_tools(manager: ChannelManager) -> list[Tool]:
    return [
        Tool(name="channel_broadcast",
             description="向所有已注册的频道广播消息通知",
             parameters={
                 "text": {"type": "string", "description": "消息内容"},
                 "level": {"type": "string", "enum": ["info","warn","error","success"]},
             },
             call=lambda text, level="info":
                 manager.broadcast(ChannelMessage(text=text, level=MessageLevel(level)))),
        Tool(name="channel_send",
             description="向指定频道发送消息",
             parameters={
                 "channel": {"type": "string", "description": "频道名称"},
                 "text": {"type": "string"},
             },
             call=lambda channel, text: manager.send_to(channel, ChannelMessage(text=text))),
    ]
```

---

#### F-64: Voice Mode 语音输入

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB Voice Mode

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P64-A | ASR 语音识别 | 对接豆包 doubaoime-asr / OpenAI Whisper 实现语音→文本 | ⏳ 待开始 | 3-5天 |
| P64-B | Push-to-Talk 语音交互 | 按键触发录音→释放即识别的交互模式 | ⏳ 待开始 | 3-5天 |
| P64-C | 音频流 WebSocket 传输 | 实时音频流通过 WebSocket 传输到 ASR 服务 | ⏳ 待开始 | 2-3天 |

#### 核心数据模型

```python
# src/services/voice/models.py
from dataclasses import dataclass
from enum import Enum

class ASRProvider(Enum):
    WHISPER_LOCAL = "whisper_local"       # 本地 Whisper 模型
    WHISPER_OPENAI = "whisper_openai"     # OpenAI Whisper API
    DOUBAO_IME = "doubaoime_asr"          # 豆包即时语音识别
    ALIYUN_ASR = "aliyun_asr"             # 阿里云语音识别

class TTSProvider(Enum):
    EDGE_TTS = "edge_tts"                 # 免费 Edge TTS
    OPENAI_TTS = "openai_tts"             # OpenAI TTS API
    DOUBAO_TTS = "doubao_tts"             # 豆包 TTS

@dataclass
class VoiceConfig:
    asr_provider: ASRProvider = ASRProvider.WHISPER_LOCAL
    tts_provider: TTSProvider = TTSProvider.EDGE_TTS
    push_to_talk_key: str = "V"           # 按键录音键
    language: str = "zh"                  # 识别语言
    silence_timeout: float = 1.5          # 静音超时停止录音（秒）
    sample_rate: int = 16000              # 采样率
    device_index: int | None = None       # 音频设备索引

@dataclass
class VoiceCommand:
    """语音识别结果。"""
    raw_text: str
    confidence: float = 1.0
    language: str = "zh"
    is_final: bool = True                 # False = 中间结果（流式）
    duration_ms: int = 0                  # 音频时长
```

#### 核心接口

```python
# src/services/voice/base.py
from abc import ABC, abstractmethod

class ASREngine(ABC):
    """语音识别引擎抽象。"""

    @abstractmethod
    async def transcribe(self, audio_data: bytes) -> VoiceCommand:
        """将音频数据（16kHz WAV/PCM）转为文本。"""

    @abstractmethod
    async def transcribe_stream(self, audio_stream) -> AsyncIterator[VoiceCommand]:
        """流式识别（返回中间+最终结果）。"""

    @abstractmethod
    async def warmup(self) -> None:
        """预加载模型（本地 Whisper）。"""


class TTSEngine(ABC):
    """文字转语音引擎抽象。"""

    @abstractmethod
    async def speak(self, text: str, output_path: str) -> str:
        """将文本转为语音并保存到文件，返回文件路径。"""

    @abstractmethod
    async def speak_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式 TTS（用于实时播放）。"""


class VoiceInputController:
    """语音输入控制器（录音→ASR→Tool 调用）。"""

    def __init__(self, asr: ASREngine, config: VoiceConfig | None = None):
        self._asr = asr
        self._config = config or VoiceConfig()
        self._recording = False

    async def start_recording(self) -> None:
        """开始录音（Push-to-Talk 按下时调用）。"""
        self._recording = True
        # 启动后台录音线程/协程
        self._audio_buffer = bytearray()

    async def stop_recording(self) -> VoiceCommand:
        """停止录音并执行 ASR 识别。"""
        self._recording = False
        audio = bytes(self._audio_buffer)
        return await self._asr.transcribe(audio)

    def _audio_callback(self, in_data: bytes, ...) -> None:
        """音频设备回调（由 sounddevice/pyaudio 驱动）。"""
        if self._recording:
            self._audio_buffer.extend(in_data)
```

#### 本地 Whisper 实现示例

```python
# src/services/voice/whisper_asr.py
import numpy as np
import whisper

class WhisperASREngine(ASREngine):
    """基于本地 Whisper 模型的 ASR 引擎。"""

    def __init__(self, model_size: str = "base"):
        self._model_size = model_size
        self._model = None

    async def warmup(self) -> None:
        self._model = whisper.load_model(self._model_size)

    async def transcribe(self, audio_data: bytes) -> VoiceCommand:
        if self._model is None:
            await self.warmup()
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        result = self._model.transcribe(audio_np, language="zh")
        return VoiceCommand(
            raw_text=result["text"].strip(),
            confidence=result.get("confidence", 1.0),
            language=result.get("language", "zh"),
        )

    async def transcribe_stream(self, audio_stream) -> AsyncIterator[VoiceCommand]:
        # 流式模式下每次累积 1s 音频后增量识别
        buffer = bytearray()
        async for chunk in audio_stream:
            buffer.extend(chunk)
            if len(buffer) >= 32000:  # ~1s 的 16kHz PCM
                yield await self.transcribe(bytes(buffer))
                buffer.clear()
```

#### Edge TTS 实现示例

```python
# src/services/voice/edge_tts.py
import edge_tts

class EdgeTTSEngine(TTSEngine):
    """基于微软 Edge TTS（免费）的 TTS 引擎。"""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self._voice = voice

    async def speak(self, text: str, output_path: str = "/tmp/tts.mp3") -> str:
        communicate = edge_tts.Communicate(text, self._voice)
        await communicate.save(output_path)
        return output_path

    async def speak_stream(self, text: str) -> AsyncIterator[bytes]:
        communicate = edge_tts.Communicate(text, self._voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
```

#### 集成到 Tool 工厂

```python
# src/services/voice/__init__.py
from clawcodex.config import settings

_voice_controller: VoiceInputController | None = None

def init_voice(config: VoiceConfig | None = None) -> VoiceInputController:
    global _voice_controller
    if _voice_controller is None:
        cfg = config or VoiceConfig(**settings.get("voice", {}))
        if cfg.asr_provider == ASRProvider.WHISPER_LOCAL:
            asr = WhisperASREngine()
        elif cfg.asr_provider == ASRProvider.WHISPER_OPENAI:
            asr = OpenAIWhisperASREngine()
        else:
            asr = WhisperASREngine()
        _voice_controller = VoiceInputController(asr, cfg)
    return _voice_controller

def build_voice_tools(controller: VoiceInputController) -> list[Tool]:
    return [
        Tool(name="voice_start_recording",
             description="开始语音输入（Push-to-Talk）",
             parameters={},
             call=lambda: controller.start_recording()),
        Tool(name="voice_stop_recording",
             description="停止录音并执行语音识别，返回识别文本",
             parameters={},
             call=lambda: controller.stop_recording()),
        Tool(name="voice_speak",
             description="文字转语音输出",
             parameters={"text": {"type": "string"}},
             call=lambda text: asyncio.run(tts_engine.speak(text))),
    ]
```

#### 依赖

- `openai-whisper`（本地 ASR，需 GPU 加速）
- `edge-tts`（免费 TTS）
- `sounddevice` 或 `pyaudio`（音频采集）
- `numpy`（音频数据处理）

---


### 7.4 可观测性与协议

#### F-65: Langfuse Agent 可观测性

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB Langfuse

#### 背景

CCB 集成 Langfuse（OpenTelemetry 兼容）实现 Agent Loop 级可观测性：记录每次 LLM 调用的输入/输出/token 用量/延迟，并支持一键导出为训练数据集。clawcodex 目前仅通过 Bridge Dashboard 提供有限的远程可观测性。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P65-A | OpenTelemetry + Langfuse SDK 集成 | 引入 OpenTelemetry Python SDK + Langfuse exporter | ⏳ 待开始 | 3-5天 |
| P65-B | Agent Loop 级追踪 | 每次 request/response 自动追踪：model/prompt/completion/token/timing | ⏳ 待开始 | 2-3天 |
| P65-C | 一键转化为训练数据集 | 将追踪数据导出为训练集格式（JSONL/ChatML） | ⏳ 待开始 | 2-3天 |

#### 核心数据模型

```python
# src/services/langfuse/models.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

@dataclass
class TraceSpan:
    """一次 Agent Loop 调用追踪。"""
    trace_id: str
    name: str                                  # 如 "llm_call", "tool_execute"
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    model: str | None = None                   # 使用的模型名称
    token_input: int = 0
    token_output: int = 0
    duration_ms: float = 0.0
    tags: list[str] = field(default_factory=list)
    parent_span_id: str | None = None          # 父 Span（支持嵌套）
    metadata: dict[str, Any] = field(default_factory=dict)
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime | None = None

@dataclass
class TrainingSample:
    """从追踪导出的训练样本。"""
    messages: list[dict]                       # [{"role":"user","content":...}, ...]
    model: str
    token_count: int
    timestamp: str
    tags: list[str]
    metadata: dict[str, Any]
```

#### Langfuse Provider Wrapper

```python
# src/services/langfuse/wrapper.py
from langfuse import Langfuse
from langfuse.model import CreateSpan, CreateGeneration

class LangfuseProviderWrapper(BaseProvider):
    """在 Provider 层注入 Langfuse 追踪。

    包装原始 LLM Provider，自动记录每次 request/response 的：
    - model, prompt, completion
    - token 用量 (input/output)
    - 延迟 (duration_ms)
    """

    def __init__(self, inner: BaseProvider,
                 langfuse: Langfuse | None = None,
                 session_id: str | None = None):
        self._inner = inner
        self._langfuse = langfuse or Langfuse()
        self._session_id = session_id
        self._trace_map: dict[str, Any] = {}  # request_id → Langfuse trace

    async def stream(self, request: ProviderRequest) -> AsyncIterator[str]:
        """包装流式请求。

        记录输入 prompt，收集完整 completion，结束时写 span。
        """
        trace = self._langfuse.trace(
            name="llm_stream",
            session_id=self._session_id,
            input=request.messages,
            metadata={"model": request.model, "params": request.params},
        )
        generation = trace.generation(
            name=request.model or "unknown",
            model=request.model,
            input=request.messages,
        )

        start = datetime.utcnow()
        full_output: list[str] = []
        try:
            async for chunk in self._inner.stream(request):
                full_output.append(chunk)
                yield chunk
        except Exception as e:
            generation.end(
                output=str(e),
                level="ERROR",
                status_message=str(e),
            )
            raise
        finally:
            duration = (datetime.utcnow() - start).total_seconds() * 1000
            usage = self._estimate_tokens(request.messages, "".join(full_output))
            generation.end(
                output="".join(full_output),
                usage=usage,
                metadata={"duration_ms": duration},
            )

    def _estimate_tokens(self, messages: list, completion: str) -> dict:
        """粗略估算 token 用量（生产环境使用 tiktoken）。"""
        input_tokens = sum(len(m.get("content", "")) // 2 for m in messages)
        output_tokens = len(completion) // 2
        return {"input": input_tokens, "output": output_tokens}


class LangfuseToolWrapper:
    """包装 Tool 执行，记录调用链。"""

    def __init__(self, langfuse: Langfuse, trace: Any):
        self._langfuse = langfuse
        self._trace = trace

    def wrap(self, tool: Tool) -> Tool:
        original_call = tool.call
        async def traced_call(**kwargs):
            span = self._trace.span(
                name=f"tool_{tool.name}",
                input=kwargs,
            )
            start = datetime.utcnow()
            try:
                result = await original_call(**kwargs)
                span.end(output=str(result)[:500])
                return result
            except Exception as e:
                span.end(level="ERROR", status_message=str(e))
                raise
        tool.call = traced_call
        return tool
```

#### 导出为训练数据集

```python
# src/services/langfuse/exporter.py
import json
from pathlib import Path
from datetime import datetime

class TrainingDataExporter:
    """从 Langfuse 将追踪数据导出为训练集。"""

    def __init__(self, langfuse: Langfuse):
        self._langfuse = langfuse

    async def export_jsonl(self, trace_name: str = "llm_stream",
                           output_path: str = "./training_data.jsonl",
                           limit: int = 1000) -> int:
        """导出为 JSONL 格式（每行一个 TrainingSample）。"""
        traces = self._langfuse.fetch_traces(name=trace_name, limit=limit)
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for t in traces:
                sample = self._trace_to_sample(t)
                if sample:
                    f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    count += 1
        return count

    def _trace_to_sample(self, trace: Any) -> dict | None:
        generations = getattr(trace, "generations", [])
        if not generations:
            return None
        gen = generations[0]
        messages = gen.input if isinstance(gen.input, list) else []
        if gen.output:
            messages.append({"role": "assistant", "content": gen.output})
        return {
            "messages": messages,
            "model": gen.model or "unknown",
            "token_count": (gen.usage or {}).get("input", 0),
            "timestamp": trace.timestamp,
            "tags": trace.tags or [],
            "metadata": trace.metadata or {},
        }
```

#### 配置与初始化

```python
# src/services/langfuse/__init__.py
from langfuse import Langfuse

_langfuse: Langfuse | None = None

def init_langfuse(public_key: str | None = None,
                  secret_key: str | None = None,
                  host: str = "https://cloud.langfuse.com") -> Langfuse:
    """初始化 Langfuse 客户端（全局单例）。"""
    global _langfuse
    if _langfuse is None:
        _langfuse = Langfuse(
            public_key=public_key or os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=secret_key or os.environ["LANGFUSE_SECRET_KEY"],
            host=host,
        )
    return _langfuse

def wrap_provider(provider: BaseProvider) -> BaseProvider:
    """包装 Provider 使其自动上报追踪数据。"""
    lf = init_langfuse()
    return LangfuseProviderWrapper(provider, lf)

def build_langfuse_tools() -> list[Tool]:
    return [
        Tool(name="langfuse_export_training_data",
             description="将追踪数据导出为训练数据集（JSONL）",
             parameters={
                 "output_path": {"type": "string", "description": "输出文件路径"},
                 "limit": {"type": "integer", "default": 1000},
             },
             call=lambda output_path="./training_data.jsonl", limit=1000:
                 TrainingDataExporter(init_langfuse()).export_jsonl(output_path=output_path, limit=limit)),
    ]
```

#### 依赖

- `langfuse`（Python SDK，`pip install langfuse`）
- `opentelemetry-api`（可选，用于 OpenTelemetry 桥接）
- `tiktoken`（可选，精确 token 计数）
        finally:
            span.end(...)
```

---

#### F-66: ACP 协议支持

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

#### 核心数据模型

```python
# src/services/acp/models.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from datetime import datetime

class ACPMessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class ACPMessageType(Enum):
    SESSION_CREATE = "session/create"
    SESSION_RESUME = "session/resume"
    SESSION_END = "session/end"
    MESSAGE_SEND = "message/send"
    MESSAGE_STREAM = "message/stream"
    TOOL_CALL = "tool/call"
    TOOL_RESULT = "tool/result"
    SKILL_INVOKE = "skill/invoke"
    SKILL_RESULT = "skill/result"
    ERROR = "error"

@dataclass
class ACPMessage:
    """ACP 协议消息体（JSON-RPC over WebSocket/stdio）。"""
    type: ACPMessageType
    id: str = ""                          # 消息/请求 ID
    session_id: str = ""                  # 会话 ID
    role: ACPMessageRole = ACPMessageRole.USER
    content: str | dict | None = None
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class ACPSession:
    """ACP 会话信息。"""
    id: str
    created_at: str
    messages: list[ACPMessage] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    workspace_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
```

#### 核心接口

```python
# src/services/acp/base.py
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

class ACPTransport(ABC):
    """ACP 传输层抽象（stdio / WebSocket / TCP）。"""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send(self, msg: ACPMessage) -> None: ...

    @abstractmethod
    async def receive(self) -> ACPMessage | None: ...

    @abstractmethod
    async def close(self) -> None: ...


class ACPServer(ABC):
    """ACP 协议服务端（接收 IDE 发起的会话请求）。"""

    @abstractmethod
    async def handle_session(self, transport: ACPTransport) -> None:
        """处理单个会话生命周期（创建→消息交换→结束）。"""

    @abstractmethod
    async def create_session(self, workspace_path: str) -> ACPSession:
        """创建新会话。"""

    @abstractmethod
    async def resume_session(self, session_id: str) -> ACPSession | None:
        """根据 session_id 恢复历史会话。"""

    @abstractmethod
    async def process_message(self, msg: ACPMessage) -> AsyncIterator[ACPMessage]:
        """处理用户消息，流式返回 Assistant 回复。"""

    @abstractmethod
    async def invoke_skill(self, skill_name: str, params: dict) -> dict:
        """通过 ACP 调用 Skill。"""


class ACPClient(ABC):
    """ACP 协议客户端（IDE 侧连接 ClawCodex）。"""

    @abstractmethod
    async def connect_to_agent(self, endpoint: str) -> None:
        """连接到正在运行的 ClawCodex Agent。"""

    @abstractmethod
    async def send_user_message(self, content: str) -> AsyncIterator[ACPMessage]:
        """发送用户消息并流式接收回复。"""

    @abstractmethod
    async def resume_session(self, session_id: str) -> AsyncIterator[ACPMessage]:
        """恢复历史会话。"""
```

#### Stdio 传输实现示例

```python
# src/services/acp/stdio_transport.py
import json, sys

class StdioACPTransport(ACPTransport):
    """基于 stdin/stdout 的 ACP 传输（Zed/Cursor 插件使用）。"""

    def __init__(self):
        self._reader = asyncio.StreamReader()
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        self._reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(self._reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        self._writer = sys.stdout

    async def send(self, msg: ACPMessage) -> None:
        line = json.dumps(dataclasses.asdict(msg), default=str) + "\n"
        self._writer.write(line.encode())
        await self._writer.drain()

    async def receive(self) -> ACPMessage | None:
        data = await self._reader.readline()
        if not data:
            return None
        return ACPMessage(**json.loads(data))

    async def close(self) -> None:
        self._writer.close()
```

#### WebSocket 传输实现示例

```python
# src/services/acp/ws_transport.py
import json, asyncio
from aiohttp import web, ClientSession, WSMsgType

class WsACPTransport(ACPTransport):
    """基于 WebSocket 的 ACP 传输（远程 IDE 插件使用）。"""

    def __init__(self, ws=None):
        self._ws = ws

    async def connect(self, url: str) -> None:
        session = ClientSession()
        self._ws = await session.ws_connect(url)

    async def send(self, msg: ACPMessage) -> None:
        await self._ws.send_json(dataclasses.asdict(msg, default=str))

    async def receive(self) -> ACPMessage | None:
        msg = await self._ws.receive()
        if msg.type == WSMsgType.TEXT:
            return ACPMessage(**json.loads(msg.data))
        return None

    async def close(self) -> None:
        await self._ws.close()
```

#### ACP 服务端 WebSocket 入口

```python
# src/services/acp/server.py
from aiohttp import web

async def acp_ws_handler(request: web.Request) -> web.WebSocketResponse:
    """用于 aiohttp 路由的 WebSocket handler。"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    transport = WsACPTransport(ws)
    server = ClawCodexACPServer()
    await server.handle_session(transport)
    return ws

def build_acp_server() -> web.Application:
    app = web.Application()
    app.router.add_get("/acp/ws", acp_ws_handler)
    return app
```

#### 集成到 Tool 工厂

```python
# src/services/acp/__init__.py
def build_acp_tools(server: ACPServer) -> list[Tool]:
    return [
        Tool(name="acp_list_sessions",
             description="列出所有活跃 ACP 会话",
             parameters={},
             call=lambda: server.list_sessions()),
        Tool(name="acp_invoke_skill",
             description="通过 ACP 协议调用 Skill",
             parameters={
                 "skill_name": {"type": "string"},
                 "params": {"type": "object"},
             },
             call=lambda skill_name, params: server.invoke_skill(skill_name, params)),
    ]
```

#### 依赖

- `aiohttp`（WebSocket 服务端/客户端）
- 可选：Zed / Cursor IDE 插件 SDK

---


### 7.5 高级 Agent 模式

#### F-67: Buddy 伴侣 / Proactive 自主模式

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

#### 核心数据模型

```python
# src/services/buddy/models.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from datetime import datetime

class BuddySuggestionLevel(Enum):
    INFO = "info"           # 普通信息提示
    WARNING = "warning"     # 潜在问题警告
    SUGGESTION = "suggestion"  # 改进建议
    CRITICAL = "critical"   # 严重问题（如安全漏洞）

class BuddyEventType(Enum):
    SESSION_START = "session/start"
    SESSION_MESSAGE = "session/message"
    FILE_CHANGE = "file/change"
    FILE_SAVE = "file/save"
    TOOL_EXECUTE = "tool/execute"
    TOOL_ERROR = "tool/error"
    AGENT_STUCK = "agent/stuck"       # Agent 陷入循环/卡住
    AGENT_COMPLETE = "agent/complete"
    PROJECT_SCAN = "project/scan"     # 定时项目扫描
    USER_IDLE = "user/idle"           # 用户空闲

@dataclass
class BuddySuggestion:
    """Buddy 观察后生成的建议。"""
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    level: BuddySuggestionLevel = BuddySuggestionLevel.INFO
    title: str = ""
    detail: str = ""
    source_event: BuddyEventType | None = None
    source_file: str | None = None
    source_line: int | None = None
    code_snippet: str | None = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    dismissed: bool = False

@dataclass
class BuddyState:
    """Buddy 观察状态。"""
    session_id: str
    message_count: int = 0
    tool_error_count: int = 0
    consecutive_steps: int = 0          # 连续步骤数（检测卡住）
    last_message_at: str | None = None
    suggestions: list[BuddySuggestion] = field(default_factory=list)
    active: bool = True
```

#### 核心接口

```python
# src/services/buddy/base.py
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

class EventObserver(ABC):
    """事件观察者（Buddy 消费的 event stream）。"""

    @abstractmethod
    async def on_event(self, event: BuddyEvent) -> None:
        """消费一个事件。"""

    @abstractmethod
    async def start_observing(self, session_id: str) -> None:
        """开始观察指定会话。"""

    @abstractmethod
    async def stop_observing(self) -> None:
        """停止观察。"""


class SuggestionProvider(ABC):
    """建议提供者。"""

    @abstractmethod
    async def analyze(self, state: BuddyState) -> list[BuddySuggestion]:
        """分析当前状态，生成建议列表。"""

    @abstractmethod
    async def should_interrupt(self, suggestion: BuddySuggestion) -> bool:
        """判断是否应该打断用户（临界建议）。"""


class FileWatcher(ABC):
    """文件变更监听器。"""

    @abstractmethod
    async def watch(self, path: str) -> AsyncIterator[BuddyEvent]:
        """监听指定路径的文件变更事件流。"""

    @abstractmethod
    async def stop_watch(self) -> None:
        """停止监听。"""
```

#### 核心观察者实现示例

```python
# src/services/buddy/observer.py
import asyncio
from collections import deque

class ClawCodexBuddy(EventObserver):
    """ClawCodex Buddy 主实现。

    运行在独立协程中，消费会话事件流，通过启发式规则 + LLM 分析生成建议。
    """

    def __init__(self, llm_provider: BaseProvider | None = None):
        self._state: BuddyState | None = None
        self._providers: list[SuggestionProvider] = [
            StuckDetector(),
            ToolErrorAnalyzer(),
            ProjectHealthAnalyzer(),
        ]
        self._suggestion_queue: deque[BuddySuggestion] = deque(maxlen=50)
        self._llm = llm_provider
        self._file_watcher: FileWatcher | None = None

    async def start_observing(self, session_id: str) -> None:
        self._state = BuddyState(session_id=session_id)
        # 启动文件变更监听
        self._file_watcher = WatchdogFileWatcher()
        asyncio.create_task(self._consume_file_events())

    async def on_event(self, event: BuddyEvent) -> None:
        """处理每个事件。"""
        if self._state is None:
            return

        self._state.message_count += 1
        self._state.last_message_at = datetime.utcnow().isoformat()

        if event.type == BuddyEventType.TOOL_ERROR:
            self._state.tool_error_count += 1
        elif event.type == BuddyEventType.SESSION_MESSAGE:
            self._state.consecutive_steps += 1
        elif event.type == BuddyEventType.AGENT_STUCK:
            self._state.consecutive_steps = 999  # 触发卡住检测

        # 运行所有分析器
        for provider in self._providers:
            new_suggestions = await provider.analyze(self._state)
            for s in new_suggestions:
                self._suggestion_queue.append(s)

        # 如果有 LLM，对紧急建议进行 LLM 验证
        if self._llm and new_suggestions:
            await self._llm_validate_suggestions(new_suggestions)

    async def _consume_file_events(self) -> None:
        if self._state is None or not self._file_watcher:
            return
        async for event in self._file_watcher.watch("."):
            await self.on_event(event)

    async def _llm_validate_suggestions(self, suggestions: list) -> None:
        """用 LLM 验证建议是否值得展示。"""
        for s in suggestions:
            if s.level in (BuddySuggestionLevel.CRITICAL, BuddySuggestionLevel.WARNING):
                should = await self._providers[-1].should_interrupt(s)
                if should:
                    await self._notify_user(s)

    async def _notify_user(self, suggestion: BuddySuggestion) -> None:
        """通过通知渠道（stdout / Channels / GUI）展示建议。"""
        icon = {"info": "💡", "warning": "⚠️", "suggestion": "🔧", "critical": "🚨"}
        level_icon = icon.get(suggestion.level.value, "💡")
        print(f"\n[{level_icon} Buddy] {suggestion.title}")
        if suggestion.detail:
            print(f"  {suggestion.detail}")
```

#### 卡住检测器

```python
# src/services/buddy/detectors.py
class StuckDetector(SuggestionProvider):
    """检测 Agent 是否陷入循环（连续 8+ 步无有效输出）。"""

    MAX_CONSECUTIVE_STEPS = 8

    async def analyze(self, state: BuddyState) -> list[BuddySuggestion]:
        if state.consecutive_steps >= self.MAX_CONSECUTIVE_STEPS:
            return [BuddySuggestion(
                level=BuddySuggestionLevel.WARNING,
                title="Agent 似乎卡住了",
                detail=f"已连续执行 {state.consecutive_steps} 步未产生有效输出。"
                       "建议：终止当前任务并重新描述目标。",
                source_event=BuddyEventType.AGENT_STUCK,
            )]
        return []

    async def should_interrupt(self, suggestion: BuddySuggestion) -> bool:
        return suggestion.level == BuddySuggestionLevel.WARNING


class ToolErrorAnalyzer(SuggestionProvider):
    """分析工具调用错误，给出修复建议。"""

    ERROR_PATTERNS = {
        "ModuleNotFoundError": "缺少依赖模块，尝试 `pip install`",
        "FileNotFoundError": "文件路径不存在，检查工作目录",
        "PermissionError": "权限不足，尝试 `sudo`",
        "ConnectionError": "网络连接失败，检查代理配置",
    }

    async def analyze(self, state: BuddyState) -> list[BuddySuggestion]:
        if state.tool_error_count == 0:
            return []
        return [BuddySuggestion(
            level=BuddySuggestionLevel.WARNING,
            title=f"工具调用失败 {state.tool_error_count} 次",
            detail="检测到多次工具调用错误。常见原因与修复见 ERROR_PATTERNS。",
            source_event=BuddyEventType.TOOL_ERROR,
        )]

    async def should_interrupt(self, suggestion: BuddySuggestion) -> bool:
        return state.tool_error_count >= 3
```

#### Watchdog 文件变更监听

```python
# src/services/buddy/watcher.py
import asyncio
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class WatchdogFileWatcher(FileWatcher, FileSystemEventHandler):
    """基于 watchdog 的文件变更监听器。"""

    def __init__(self, debounce_sec: float = 2.0):
        self._observer = Observer()
        self._debounce = debounce_sec
        self._queue: asyncio.Queue[BuddyEvent] = asyncio.Queue()
        self._running = False

    def on_modified(self, event):
        if not event.is_directory:
            self._queue.put_nowait(BuddyEvent(
                type=BuddyEventType.FILE_CHANGE,
                data={"path": event.src_path},
            ))

    async def watch(self, path: str) -> AsyncIterator[BuddyEvent]:
        self._observer.schedule(self, path, recursive=True)
        self._observer.start()
        self._running = True
        while self._running:
            event = await self._queue.get()
            yield event

    async def stop_watch(self) -> None:
        self._running = False
        self._observer.stop()
        self._observer.join()
```

#### Proactive Scheduler（自主检查模式）

```python
# src/services/buddy/proactive.py
import asyncio
from datetime import datetime, timedelta

class ProactiveScheduler:
    """Proactive 自主模式调度器。

    按固定间隔自动执行项目健康检查，无需用户触发。
    """

    def __init__(self, buddy: ClawCodexBuddy, interval_minutes: int = 15):
        self._buddy = buddy
        self._interval = timedelta(minutes=interval_minutes)
        self._running = False

    async def start(self) -> None:
        """启动定期检查循环。"""
        self._running = True
        while self._running:
            await asyncio.sleep(self._interval.total_seconds())
            await self._run_health_check()

    async def stop(self) -> None:
        self._running = False

    async def _run_health_check(self) -> None:
        """执行项目健康检查。"""
        issues = await self._scan_project()
        if not issues:
            return
        for issue in issues:
            suggestion = BuddySuggestion(
                level=BuddySuggestionLevel.SUGGESTION,
                title=issue["title"],
                detail=issue["detail"],
                source_event=BuddyEventType.PROJECT_SCAN,
            )
            await self._buddy._notify_user(suggestion)

    async def _scan_project(self) -> list[dict]:
        """扫描项目状态，发现潜在问题。"""
        issues = []
        # 1. 检查依赖是否过期
        issues.extend(await self._check_dependencies())
        # 2. 检查未提交的变更
        issues.extend(await self._check_git_status())
        # 3. 检查未使用的导入/变量（快速扫描）
        issues.extend(await self._check_code_quality())
        return issues

    async def _check_dependencies(self) -> list[dict]:
        # 简单示例：读取 requirements.txt / pyproject.toml 检查已知漏洞
        return []

    async def _check_git_status(self) -> list[dict]:
        # 检查是否有未提交的变更
        return []

    async def _check_code_quality(self) -> list[dict]:
        # 快速代码质量扫描（长用时建议在后台异步运行）
        return []
```

#### 集成到 Tool 工厂

```python
# src/services/buddy/__init__.py
from clawcodex.config import settings

_buddy: ClawCodexBuddy | None = None

async def init_buddy(llm_provider: BaseProvider | None = None) -> ClawCodexBuddy:
    global _buddy
    if _buddy is None:
        _buddy = ClawCodexBuddy(llm_provider=llm_provider)
    return _buddy

def build_buddy_tools(buddy: ClawCodexBuddy) -> list[Tool]:
    return [
        Tool(name="buddy_get_suggestions",
             description="获取 Buddy 当前待处理的建议列表",
             parameters={},
             call=lambda: buddy.get_pending_suggestions()),
        Tool(name="buddy_dismiss_suggestion",
             description="忽略某条建议",
             parameters={"suggestion_id": {"type": "string"}},
             call=lambda suggestion_id: buddy.dismiss_suggestion(suggestion_id)),
        Tool(name="buddy_start_proactive",
             description="启动 Proactive 自主检查模式",
             parameters={"interval_minutes": {"type": "integer", "default": 15}},
             call=lambda interval_minutes=15:
                 ProactiveScheduler(buddy, interval_minutes).start()),
        Tool(name="buddy_stop_proactive",
             description="停止 Proactive 自主检查模式",
             parameters={},
             call=lambda: buddy.stop_proactive()),
    ]
```

#### 依赖

- `watchdog`（文件系统变更监听）
- 可选：`safety` / `bandit`（安全扫描，Proactive 模式用）

#### F-81: Native 原生模块系统（Python 可实现部分）

> **注意**: F-81 Native 原生模块系统已移至 §4.4（Architecture & SDK 下沉），此处保留用于 CCB 对标完整性参考。

#### 背景

CCB 使用 5 个 Rust/NAPI 原生模块处理性能敏感操作。clawcodex 作为 Python 项目，应在不引入 Rust 编译链的前提下，用纯 Python / C扩展 等价实现这些模块的核心功能。

| CCB 模块 | 原始语言 | Python 替代方案 | 可行性 |
|----------|:--------:|-----------------|:------:|
| `audio-capture-napi` | Rust/NAPI | `pyaudio` / `sounddevice` + `webrtcvad` VAD 检测 | ✅ 完全可行 |
| `color-diff-napi` | Rust/NAPI | `PIL.ImageChops.difference` + NumPy `mean_squared_error` | ✅ 完全可行 |
| `image-processor-napi` | Rust/NAPI | `Pillow` (crop/resize/encode/decode) | ✅ 完全可行 |
| `modifiers-napi` | Rust/NAPI | `pynput` / `evdev`（键盘修饰键状态检测） | ⚠️ 部分可行（Linux evdev 需 root） |
| `url-handler-napi` | Rust/NAPI | `webbrowser` + `xdg-open` / `desktop-entry` | ✅ 完全可行 |

#### 子特性分解

| 子特性 | 说明 | 优先级 |
|--------|------|:------:|
| F-81.1 | `clawcodex_ext/native/__init__.py` — 统一的原生模块注册表与懒加载基础设施 | P0 |
| F-81.2 | `clawcodex_ext/native/audio.py` — 麦克风音频捕获（前置 F-64 Voice Mode） | P0 |
| F-81.3 | `clawcodex_ext/native/image.py` — 截图差异对比与图像处理（前置 F-61 Computer Use） | P0 |
| F-81.4 | `clawcodex_ext/native/url_handler.py` — OS URL Scheme 注册（`clawcodex://`） | P1 |
| F-81.5 | `clawcodex_ext/native/modifiers.py` — 键盘修饰键检测（辅助 F-61） | P1 |
| F-81.6 | fallback 策略：当可选依赖缺失时降级为纯 Python 兜底 | P2 |

#### 架构设计

```
clawcodex_ext/native/
├── __init__.py          # NativeModuleRegistry + lazy loader
├── audio.py             # 音频捕获（pyaudio/sounddevice）
├── image.py             # 图像差异对比 + 处理（Pillow + NumPy）
├── url_handler.py       # URL Scheme 注册（webbrowser + xdg-utils）
└── modifiers.py         # 键盘修饰键检测（pynput/evdev）
```

```python
# clawcodex_ext/native/__init__.py
import importlib
from typing import Any, Protocol

class NativeModule(Protocol):
    name: str
    def is_available(self) -> bool: ...
    def get_version(self) -> str: ...

class NativeModuleRegistry:
    """统一的原生模块注册表，懒加载 + 降级检查。"""
    _modules: dict[str, type[NativeModule]] = {}

    @classmethod
    def register(cls, name: str, mod_cls: type[NativeModule]) -> None:
        cls._modules[name] = mod_cls

    @classmethod
    def load(cls, name: str) -> NativeModule | None:
        """加载目标模块，缺失依赖时返回 None（调用方降级）。"""
        mod_cls = cls._modules.get(name)
        if mod_cls is None:
            return None
        try:
            instance = mod_cls()
            if instance.is_available():
                return instance
        except ImportError:
            pass
        return None
```

#### 音频捕获模块

```python
# clawcodex_ext/native/audio.py
import io
import wave
from typing import AsyncIterator

class AudioCaptureModule:
    name = "audio_capture"

    def is_available(self) -> bool:
        try:
            import pyaudio  # noqa: F401
            return True
        except ImportError:
            return False

    def get_version(self) -> str:
        return "1.0 (pyaudio)"

    async def record(
        self,
        duration_sec: float = 5.0,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> bytes:
        """录制麦克风音频，返回 WAV 字节。"""
        import pyaudio
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            frames_per_buffer=1024,
        )
        frames = []
        for _ in range(int(sample_rate / 1024 * duration_sec)):
            data = stream.read(1024)
            frames.append(data)
        stream.stop_stream()
        stream.close()
        p.terminate()

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    async def stream(self) -> AsyncIterator[bytes]:
        """实时音频流（VAD 检测后输出片段）。"""
        import pyaudio
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=1024,
        )
        try:
            while True:
                data = stream.read(1024)
                yield data
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
```

#### 图像差异对比模块

```python
# clawcodex_ext/native/image.py
import numpy as np
from PIL import Image

class ImageProcessorModule:
    name = "image_processor"

    def is_available(self) -> bool:
        try:
            import PIL  # noqa: F401
            import numpy  # noqa: F401
            return True
        except ImportError:
            return False

    def get_version(self) -> str:
        return "1.0 (Pillow + NumPy)"

    def compute_diff(self, img1_path: str, img2_path: str) -> float:
        """计算两张截图的像素差异比率 (0.0 ~ 1.0)。"""
        im1 = Image.open(img1_path).convert("RGB")
        im2 = Image.open(img2_path).convert("RGB")
        arr1 = np.array(im1, dtype=np.float32)
        arr2 = np.array(im2, dtype=np.float32)
        diff = np.mean((arr1 - arr2) ** 2)
        return float(diff / (255.0 ** 2))

    def crop_and_resize(
        self, image_path: str, box: tuple[int, int, int, int],
        size: tuple[int, int] | None = None,
        output_path: str | None = None,
    ) -> bytes:
        """裁剪并缩放截图。"""
        im = Image.open(image_path)
        cropped = im.crop(box)
        if size:
            cropped = cropped.resize(size, Image.LANCZOS)
        if output_path:
            cropped.save(output_path, "JPEG", quality=85)
        buf = io.BytesIO()
        cropped.save(buf, "JPEG", quality=85)
        return buf.getvalue()
```

#### URL Handler 模块

```python
# clawcodex_ext/native/url_handler.py
import os
import shutil
import webbrowser
from pathlib import Path

class UrlHandlerModule:
    name = "url_handler"

    def is_available(self) -> bool:
        return True  # webbrowser 是标准库

    def get_version(self) -> str:
        return "1.0 (stdlib)"

    def register_protocol(self, protocol: str = "clawcodex") -> bool:
        """注册 clawcodex:// URL Scheme（按 OS 平台）。"""
        import sys
        if sys.platform == "linux":
            desktop_file = Path.home() / ".local/share/applications"
            desktop_file.mkdir(parents=True, exist_ok=True)
            desktop_entry = desktop_file / f"{protocol}-handler.desktop"
            desktop_entry.write_text(
                f"[Desktop Entry]\n"
                f"Type=Application\n"
                f"Name=ClawCodex\n"
                f"Exec=clawcodex %u\n"
                f"MimeType=x-scheme-handler/{protocol};\n"
            )
            os.system(f"xdg-mime default {protocol}-handler.desktop x-scheme-handler/{protocol}")
            return True
        elif sys.platform == "darwin":
            # macOS: use open -b or URL event registration
            return False  # 需要原生代码
        elif sys.platform == "win32":
            # Windows: reg add HKEY_CLASSES_ROOT\clawcodex
            return False  # 需要原生代码
        return False

    def open_url(self, url: str) -> bool:
        """打开 clawcodex:// URL（启动本地实例）。"""
        return webbrowser.open(url)
```

#### 依赖

- `pyaudio`（音频捕获，可选）
- `Pillow` + `numpy`（图像处理，可选）
- `pynput`（修饰键检测，可选，Linux 需 `evdev`）
- 均为 optional-dependencies，缺失时模块 `is_available()` 返回 False

---

#### F-82: Remote Control Server 远程控制服务

#### 背景

CCB 的 `remote-control-server` 是一个全功能 Web 服务 + Web 管理面板，提供远程会话管理、Worker 调度、环境管理、事件流推送和 ACP 协议中继。clawcodex 当前 `src/server/` 和 `src/remote/` 仅为空占位符。

#### 子特性分解

| 子特性 | 说明 | 优先级 |
|--------|------|:------:|
| F-82.1 | RCS 核心基础设施：FastAPI 应用 + asyncio 事件循环 + 配置加载 + 日志 | P0 |
| F-82.2 | 认证系统：API Key / JWT / CORS 中间件 | P0 |
| F-82.3 | 会话管理 API：会话 CRUD、List、详情 | P0 |
| F-82.4 | Worker 注册与调度：心跳检测、长轮询工作分发、断线检测 | P0 |
| F-82.5 | 事件流推送：SSE 流 + WebSocket 双通道 | P1 |
| F-82.6 | 环境管理：多机器部署、测试环境管理 | P1 |
| F-82.7 | ACP 协议中继：WebSocket/SSE 双向 ACP 桥接 | P1 |
| F-82.8 | 会话入口：从 RCS 远程发起新会话 | P1 |
| F-82.9 | Web 管理面板：React 前端或 Jinja2 简单面板 | P2 |

#### 架构设计

```
src/remote_control/
├── __init__.py            # 包初始化 + 版本
├── config.py              # 配置加载（端口、auth、数据库）
├── app.py                 # FastAPI 应用工厂 + 生命周期
├── auth/
│   ├── __init__.py
│   ├── api_key.py         # API Key 验证中间件
│   ├── jwt.py             # JWT 签发与验证
│   ├── cors.py            # CORS 配置
│   └── middleware.py      # 认证中间件（统一入口）
├── routes/
│   ├── __init__.py
│   ├── sessions.py        # 会话 CRUD (v1)
│   ├── workers.py         # Worker 注册/心跳/分发
│   ├── events.py          # SSE 事件流
│   ├── environments.py    # 环境管理
│   ├── session_ingress.py # 远程会话启动
│   └── web/               # Web 面板后端 API
│       ├── __init__.py
│       ├── control.py     # 控制台 API
│       ├── sessions.py    # 会话列表 API
│       └── auth.py        # 登录/登出
├── services/
│   ├── __init__.py
│   ├── work_dispatch.py   # Worker 工作分发逻辑
│   ├── store.py           # 内存/数据库存储抽象
│   └── automation_state.py# Worker 自动化状态跟踪
├── transport/
│   ├── __init__.py
│   ├── ws_handler.py      # WebSocket 处理器
│   ├── sse_writer.py      # SSE 写入器
│   ├── event_bus.py       # 内存事件总线（pub/sub）
│   └── acp_relay.py       # ACP 协议中继桥接
├── storage/
│   ├── __init__.py
│   ├── memory.py          # 内存存储（默认）
│   └── sqlite.py          # SQLite 持久化（可选）
└── web_frontend/          # Web 管理面板静态资源
    ├── index.html         # 简单 Jinja2 模板（P2 可替换为 React）
    └── static/
        ├── app.js
        └── style.css
```

#### 核心数据模型

```python
# src/remote_control/models.py
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

class WorkerStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"

@dataclass
class RemoteSession:
    id: str
    status: str  # "running" | "paused" | "completed" | "error"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    worker_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class Worker:
    id: str
    name: str
    status: WorkerStatus = WorkerStatus.OFFLINE
    last_heartbeat: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    current_session_id: str | None = None

@dataclass
class Environment:
    id: str
    name: str
    host: str
    port: int
    api_key: str
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
```

#### 认证中间件

```python
# src/remote_control/auth/middleware.py
import hmac
from fastapi import Request, HTTPException
from starlette.status import HTTP_401_UNAUTHORIZED

async def verify_api_key(request: Request, api_key: str) -> bool:
    """验证 API Key（恒定时间比较防时序攻击）。"""
    config = request.app.state.config
    stored = config.api_keys.get(api_key[:8])  # key_id 前缀
    if stored is None:
        return False
    return hmac.compare_digest(api_key, stored)

async def auth_middleware(request: Request, call_next):
    """统一认证中间件（API Key + JWT 双通道）。"""
    if request.url.path.startswith("/web/"):
        # Web 面板走 JWT Cookie
        token = request.cookies.get("access_token")
        if not token:
            raise HTTPException(status_code=HTTP_401_UNAUTHORIZED)
        payload = verify_jwt(token, request.app.state.config.jwt_secret)
        request.state.user = payload
    elif request.url.path.startswith("/api/"):
        # API 走 X-API-Key Header
        api_key = request.headers.get("X-API-Key")
        if not api_key or not await verify_api_key(request, api_key):
            raise HTTPException(status_code=HTTP_401_UNAUTHORIZED)
    return await call_next(request)
```

#### Worker 调度与长轮询

```python
# src/remote_control/services/work_dispatch.py
import asyncio
from datetime import datetime, timedelta

class WorkDispatcher:
    """Worker 工作分发引擎，支持长轮询。"""

    def __init__(self, store):
        self._store = store
        self._pending: dict[str, asyncio.Event] = {}  # worker_id → wait event

    async def register_worker(self, worker: Worker) -> None:
        """注册 Worker 并记录心跳。"""
        worker.status = WorkerStatus.ONLINE
        worker.last_heartbeat = datetime.utcnow()
        await self._store.save_worker(worker)

    async def wait_for_work(self, worker_id: str, timeout: int = 30):
        """长轮询等待分配工作（SSE 或轮询）。"""
        event = asyncio.Event()
        self._pending[worker_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None  # 超时返回空
        finally:
            self._pending.pop(worker_id, None)
        return await self._store.pop_pending_job(worker_id)

    async def dispatch_work(self, job: Job) -> str | None:
        """将工作分发给空闲 Worker。"""
        workers = await self._store.get_idle_workers(job.labels)
        if not workers:
            return None
        target = workers[0]
        await self._store.assign_job(job.id, target.id)
        # 唤醒长轮询
        event = self._pending.get(target.id)
        if event:
            event.set()
        return target.id

    async def check_heartbeats(self, timeout_sec: int = 60):
        """定期检查心跳，标记失联 Worker。"""
        threshold = datetime.utcnow() - timedelta(seconds=timeout_sec)
        for worker in await self._store.get_all_workers():
            if worker.last_heartbeat and worker.last_heartbeat < threshold:
                worker.status = WorkerStatus.OFFLINE
                await self._store.save_worker(worker)
```

#### FastAPI 应用工厂

```python
# src/remote_control/app.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动/关闭。"""
    # 启动后台心跳检查任务
    task = asyncio.create_task(
        app.state.dispatcher.check_heartbeats()
    )
    yield
    task.cancel()

def create_app(config: RCSConfig) -> FastAPI:
    app = FastAPI(title="ClawCodex RCS", lifespan=lifespan)
    app.state.config = config
    app.state.store = create_store(config)
    app.state.dispatcher = WorkDispatcher(app.state.store)
    app.state.event_bus = EventBus()

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 认证中间件
    app.middleware("http")(auth_middleware)

    # 注册路由
    from .routes import sessions, workers, events, environments
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(workers.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(environments.router, prefix="/api/v1")

    return app
```

#### 依赖

- `fastapi` + `uvicorn`（Web 框架）
- `PyJWT` / `python-jose`（JWT 认证）
- `sqlalchemy` / `aiosqlite`（持久化，可选）
- `websockets`（WebSocket 传输，可选）
- `httpx`（HTTP 客户端与 ACP 中继）

---

#### F-83: Ultraplan 高级规划模式

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB FEATURE_ULTRAPLAN — `/ultraplan` 多步高级规划命令

CCB 提供 `/ultraplan` 命令，让 AI 对复杂多步骤任务生成结构化的分层规划（目标 → 子任务 → 步骤 → 验收标准），并可在规划执行过程中动态调整。ClawCodex 当前无此功能。

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P83-A | Ultraplan 核心 prompt 与规划输出模板 | ⏳ 待开始 | 2-3天 |
| P83-B | `/ultraplan` CLI 斜杠命令注册与用户交互 | ⏳ 待开始 | 2-3天 |
| P83-C | 多步计划的分层执行与进度追踪 | ⏳ 待开始 | 3-5天 |
| P83-D | 执行中途动态调整计划（替换/添加/删除步骤） | ⏳ 待开始 | 2-3天 |
| P83-E | 计划完成后自动验证各步骤验收标准 | ⏳ 待开始 | 3-5天 |
| P83-F | 计划持久化到磁盘（`~/.clawcodex/plans/`）与 resume | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2-3 周

---

#### F-84: Context Collapse 上下文折叠

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB FEATURE_CONTEXT_COLLAPSE — 上下文智能压缩引擎

CCB 实现 5 层上下文清理流水线（toolResultBudget → snip → microcompact → contextCollapse → autocompact），在接近 token 限制时自动将旧消息折叠为压缩摘要。ClawCodex 已有 `src/services/context/collapse/` 基础骨架与 `ContextCollapseStore` 数据模型，但完整的折叠触发、LLM 摘要生成、持久化与恢复链路尚未实现为独立特性。

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P84-A | Token 阈值检测与溢出预警（基于 tiktoken） | ⏳ 待开始 | 2-3天 |
| P84-B | LLM 驱动的旧消息摘要生成（折叠核心） | ⏳ 待开始 | 3-5天 |
| P84-C | 折叠后历史占位符注入（ContextCollapseBoundary） | ⏳ 待开始 | 2-3天 |
| P84-D | 折叠元数据持久化与会话恢复时重建 | ⏳ 待开始 | 2-3天 |
| P84-E | 413 紧急折叠恢复（API 413 时自动触发） | ⏳ 待开始 | 2-3天 |
| P84-F | QueryEngine 集成与全链路 5 层协作（复用已有 Snip/compact） | ⏳ 待开始 | 3-5天 |

**估算总工时**: 2-3 周

**依赖**: F-68 Feature Gate（context_collapse feature flag 管理）、现有 `src/services/context/collapse/` 骨架

---


### 7.6 模板系统

#### F-85: Templates 模板系统

**状态**: ⏳ 待开始 | **优先级**: P1 | **对标**: CCB FEATURE_TEMPLATES — Agent 配置模板系统

CCB 的 Template 系统允许用户定义可复用的 Agent 配置模板（包含 tools、model、prompt、max_turns 等），在创建 Agent 时引用模板名快速构建。ClawCodex 当前使用 Agent 定义文件，但缺少模板化复用机制。

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P85-A | 模板定义格式（YAML/JSON schema + agent: template_name 引用） | ⏳ 待开始 | 2-3天 |
| P85-B | 模板注册表（`~/.clawcodex/templates/` + 项目级 `.clawcodex/templates/`） | ⏳ 待开始 | 2-3天 |
| P85-C | Agent 创建时模板解析与字段合并（template base + inline override） | ⏳ 待开始 | 3-5天 |
| P85-D | CLI 管理命令（`/template list`、`/template show`、`/template create`） | ⏳ 待开始 | 2-3天 |
| P85-E | 内置默认模板（general-purpose、explore、plan、fix、review 等） | ⏳ 待开始 | 2-3天 |

**估算总工时**: 1-2 周

---

#### F-86: Kairos / Brief 调度模式

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB FEATURE_KAIROS / FEATURE_KAIROS_BRIEF — Tick 驱动调度引擎 + 简报模式

CCB 的 Kairos 子系统提供定时唤醒 Agent 执行任务的调度能力（Tick 驱动），配合 Brief 模式提供轻量级状态简报。ClawCodex 代码中已有 KAIROS 注释（`bridge_main.py`、`memdir/paths.py`）但明确标注 deferred。此特性与 F-67 Proactive 模式有重叠，但 KAIROS 侧重于定时调度（周期性 Tick），Proactive 侧重于用户空闲时自主工作。

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P86-A | Tick 调度核心（时基触发 + 周期性唤醒） | ⏳ 待开始 | 3-5天 |
| P86-B | SleepTool 工具（Agent 控制休眠时长） | ⏳ 待开始 | 2-3天 |
| P86-C | Brief 简报模式（轻量级状态摘要输出） | ⏳ 待开始 | 2-3天 |
| P86-D | Tick 消息注入对话流（含本地时间戳） | ⏳ 待开始 | 1-2天 |
| P86-E | 每日日志自动生成（`logs/YYYY/MM/YYYY-MM-DD.md`） | ⏳ 待开始 | 2-3天 |
| P86-F | CLI 控制命令（`/tick on/off/status`、`/brief`） | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2 周

---

#### F-87: Workflow Scripts 工作流脚本

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB FEATURE_WORKFLOW_SCRIPTS — YAML/JSON 定义的多步自动化工作流

CCB 的 WorkflowScripts 允许用户创建 `.claude/workflows/*.yml` 工作流定义文件，声明多 step 执行序列（每个 step 可指定 tool、agent、prompt），通过 `/workflows` 命令管理和触发。ClawCodex 的 Orchestrator 已有类似功能（issue → agent run 流水线），但面向最终用户的声明式工作流文件系统尚未规划。

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P87-A | 工作流 YAML schema 定义与解析器 | ⏳ 待开始 | 2-3天 |
| P87-B | 工作流文件发现（`~/.clawcodex/workflows/` + `.clawcodex/workflows/`） | ⏳ 待开始 | 1-2天 |
| P87-C | 多步执行引擎（串联 agent + tool 调用序列） | ⏳ 待开始 | 3-5天 |
| P87-D | 内置捆绑工作流（代码审查、依赖更新、发布流程等） | ⏳ 待开始 | 2-3天 |
| P87-E | CLI 命令（`/workflows list/run/show`）与自动补全 | ⏳ 待开始 | 2-3天 |
| P87-F | 执行进度实时显示与错误恢复 | ⏳ 待开始 | 2-3天 |

**估算总工时**: 2 周

---

#### F-88: Explore / Plan 内置 Agent

**状态**: ⏳ 待开始 | **优先级**: P2 | **对标**: CCB BUILTIN_EXPLORE_PLAN_AGENTS — 内置探索与规划 Agent

CCB 内置 `explore`（代码库探索）和 `plan`（实施规划）两种专用 Agent 类型，分别用于理解代码结构和制定实施计划。ClawCodex 的 agent_definitions 中已定义多种 agent type，但缺少这两个 CCB 标配的专用 Agent。

| 编号 | 子特性 | 状态 | 预计工作量 |
|:----:|--------|:----:|:----------:|
| P88-A | Explore Agent 定义（工具集：Read/Grep/Glob/WebSearch/WebFetch） | ⏳ 待开始 | 1-2天 |
| P88-B | Plan Agent 定义（工具集：Read/Grep/Glob + 结构化 plan 输出 prompt） | ⏳ 待开始 | 1-2天 |
| P88-C | 自动路由逻辑：根据 user query 自动选择 explore/plan agent | ⏳ 待开始 | 2-3天 |
| P88-D | 探索报告与计划文档的自动保存 | ⏳ 待开始 | 1-2天 |

**估算总工时**: 1 周

---

### CCB 对标实施总览

> ⚠️ **重要**: 经过对 `CCB_MIGRATION_DESIGN.md` 子系统逐一比对和代码库 `src/` 的实地检查，
> 以下基础设施已在代码中实现，**不需额外 F-number**：Signal 事件通知、Bootstrap STATE 框架（含 AppState Store 两级架构）、
> Coordinator 系统（F-41 ✅ 已完成）、TUI 全屏层次（14+ Screen）、vim mode、Provider Registry、MCP Client、Auth 服务、
> Bridge 桥接、Swarm/Team 系统。真正的增量缺口集中于 **F-60~F-67 的 8 个用户可见特性** + F-71 的 4 个待实现工具，
> 以及 Notifier/Pipes 插件。v2.18 新增 **F-83~F-88 共 6 个新识别特性**。

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
| F-71 | 4 个未实现工具（Execute/RemoteTrigger/WebBrowser/Snip） | P1 | 🟡 重要缺口 | ⏳ 待开始 | 2周 |
| — | Notifier + PreventSleep 通知与防休眠服务 | P2 | 🟢 增强体验 | ⏳ 待开始 | 1周 |
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

F-62 (Chrome) ──→ F-65 (Langfuse) ──→ F-71 工具补齐 ──→ Notifier ──→ F-86 (Kairos/Brief) ──→ F-87 (Workflow) ──→ F-88 (Explore/Plan) ──→ F-64+F-66+F-67
   ↑ 自动化             ↑ 可观测性              ↑ 4 个缺失工具           ↑ 通知服务             ↑ 定时调度               ↑ 工作流脚本             ↑ 内置 Agent              ↑ 体验增强
   P1                  P1                      P1                     P2                     P2                       P2                      P2                       P2
```

> **建议**: F-60（Pipe IPC）和 F-61（Computer Use）为 P0 级特性，建议优先实施。F-83（Ultraplan）和 F-84（Context Collapse）为 P1 级架构特性，建议紧随之后。
> F-85（Templates）依赖 F-68 Feature Gate 作为基础设施。F-71 的 4 个待实现工具可与 F-61 并行开发。
> F-86~F-88 为 P2 增强体验，可与 F-64/F-66/F-67 合并为长期迭代批次。
> 已在代码中实现的基础设施（Signal、STATE、Coordinator、MCP、Auth、Bridge 等）无需重新规划 F-number。

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

#### 优势 3: SOP 编译器

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

---

> 本节规划从 Python 生态适配角度发现的 clawcodex 特性缺口。
> F-68~F-74 均为 Python 标准库或成熟第三方库可实现的特性，无需绑定特定平台 API。

#### F-68: Feature Gate 运行时特性开关系统

**状态**: ⏳ 待开始 | **优先级**: P1

#### 背景

CCB 通过 Bun 编译期 `-d FEATURE_*` macro define 实现 65+ 编译时特性标志（`FEATURE_AGENT_TOOL`、`FEATURE_VERIFICATION_AGENT` 等），支持编译级条件编译去除未启用特性代码。Python 无编译宏机制，但可以通过**运行时装饰器 + 注册表 + JSON/YAML 配置**实现等价的特性开关系统，支持热切换。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P68-A | FeatureRegistry 核心 | 全局注册表：注册/查询/枚举特性，支持依赖关系声明 | ⏳ 待开始 | 3-5天 |
| P68-B | @feature_gated 装饰器 | 工具函数/命令/前端组件的条件启用装饰器 | ⏳ 待开始 | 2-3天 |
| P68-C | JSON/YAML 配置文件 | `~/.clawcodex/features.json` 持久化特性开关配置 | ⏳ 待开始 | 1-2天 |
| P68-D | CLI 运行时切换 | `--enable-feature X --disable-feature Y` 命令行覆盖 | ⏳ 待开始 | 1-2天 |
| P68-E | 环境变量覆盖 | `CLAWCODEX_FEATURE_X=true` 环境变量级覆盖 | ⏳ 待开始 | 1天 |
| P68-F | 依赖性解析与冲突检测 | 自动检测特性依赖是否满足、互斥特性冲突 | ⏳ 待开始 | 2-3天 |

#### 架构建议

##### 包结构

```
src/services/feature_gate/
├── __init__.py           # 导出 FeatureRegistry 单例
├── registry.py           # FeatureRegistry 实现
├── decorators.py         # @feature_gated 装饰器
├── config.py             # JSON 配置加载/保存（复用 src/config.py）
├── cli.py                # CLI 命令绑定
└── types.py              # FeatureFlag dataclass
```

##### FeatureFlag 类型定义

```python
# src/services/feature_gate/types.py
from dataclasses import dataclass, field

@dataclass
class FeatureFlag:
    """单个特性标志的定义。"""
    name: str                              # 唯一标识，如 "FEATURE_AGENT_TOOL"
    default: bool = False                  # 默认启用状态
    deps: list[str] = field(default_factory=list)  # 依赖的特性列表
    mutex_with: list[str] = field(default_factory=list)  # 互斥特性列表
    description: str = ""                  # 特性说明
```

##### FeatureRegistry 实现

```python
# src/services/feature_gate/registry.py
import os
from .types import FeatureFlag

# 环境变量前缀
ENV_PREFIX = "CLAWCODEX_FEATURE_"

class FeatureRegistry:
    """全局特性注册表，单例。"""

    _features: dict[str, FeatureFlag] = {}
    _overrides: dict[str, bool] = {}          # CLI/env 运行时覆盖

    def register(self, name: str, default: bool = False,
                 deps: list[str] = None, mutex_with: list[str] = None,
                 description: str = "") -> None:
        if name in self._features:
            raise ValueError(f"Duplicate feature flag: {name}")
        self._features[name] = FeatureFlag(
            name=name, default=default,
            deps=deps or [], mutex_with=mutex_with or [],
            description=description,
        )

    def is_enabled(self, name: str) -> bool:
        """解析优先级：CLI arg > env var > config file > default"""
        # 1) CLI 运行时覆盖（最高优先级）
        if name in self._overrides:
            return self._overrides[name]
        # 2) 环境变量 CLAWCODEX_FEATURE_<NAME>=true/false
        env_val = os.environ.get(f"{ENV_PREFIX}{name}")
        if env_val is not None:
            return env_val.lower() in ("1", "true", "yes")
        # 3) 配置文件 ~/.clawcodex/features.json
        config_val = self._load_config().get(name)
        if config_val is not None:
            return config_val
        # 4) 默认值
        flag = self._features.get(name)
        return flag.default if flag else False

    def enable(self, name: str) -> None:
        self._overrides[name] = True

    def disable(self, name: str) -> None:
        self._overrides[name] = False

    def list_features(self) -> list[FeatureFlag]:
        return list(self._features.values())

    # ---- 配置加载 ----
    _config_cache: dict[str, bool] | None = None
    def _load_config(self) -> dict[str, bool]:
        if self._config_cache is None:
            import json
            path = Path.home() / ".clawcodex" / "features.json"
            if path.exists():
                with open(path) as f:
                    self._config_cache = json.load(f)
            else:
                self._config_cache = {}
        return self._config_cache

    def save_config(self) -> None:
        """将当前 overrides 持久化到 features.json"""
        import json
        path = Path.home() / ".clawcodex" / "features.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: flag.default for name, flag in self._features.items()}
        data.update(self._overrides)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self._config_cache = None  # 清除缓存

    def check_deps(self, name: str) -> list[str]:
        """检查特性的依赖是否满足，返回缺失的依赖列表。"""
        flag = self._features.get(name)
        if not flag or not flag.deps:
            return []
        return [dep for dep in flag.deps if not self.is_enabled(dep)]

    def check_mutex(self, name: str) -> list[str]:
        """检查是否与已启用的互斥特性冲突，返回冲突列表。"""
        flag = self._features.get(name)
        if not flag or not flag.mutex_with:
            return []
        return [m for m in flag.mutex_with if self.is_enabled(m)]
```

##### @feature_gated 装饰器实现

```python
# src/services/feature_gate/decorators.py
import functools
from .registry import FeatureRegistry  # 假定已初始化全局单例

_registry: FeatureRegistry | None = None

def get_registry() -> FeatureRegistry:
    global _registry
    if _registry is None:
        _registry = FeatureRegistry()
    return _registry

def feature_gated(feature_name: str, fallback=None):
    """条件启用装饰器。
    
    - 用于类：如果特性禁用，用 fallback 替代
    - 用于函数：如果特性禁用，返回 fallback 值或跳过执行
    """
    def decorator(obj):
        if not get_registry().is_enabled(feature_name):
            return fallback if fallback is not None else obj
        return obj
    return decorator

def feature_gated_class(name: str, fallback_cls=None):
    """类级别的条件注册辅助函数。"""
    def wrapper(cls):
        registry = get_registry()
        if registry.is_enabled(name):
            # 检查依赖和互斥
            missing = registry.check_deps(name)
            if missing:
                raise RuntimeError(
                    f"Feature '{name}' requires: {missing}"
                )
            conflict = registry.check_mutex(name)
            if conflict:
                raise RuntimeError(
                    f"Feature '{name}' conflicts with: {conflict}"
                )
            return cls
        return fallback_cls if fallback_cls else cls
    return wrapper
```

##### 条件注册用法

```python
# 在 build_default_registry() 中
from src.services.feature_gate.decorators import get_registry

registry = get_registry()
registry.register("FEATURE_AGENT_TOOL", default=True,
                  deps=[], description="子 Agent 生成工具")
registry.register("FEATURE_VERIFICATION_AGENT", default=True,
                  deps=["FEATURE_AGENT_TOOL"],
                  description="计划验证 Agent")

if registry.is_enabled("FEATURE_VERIFICATION_AGENT"):
    tool_registry.register(VerificationAgentTool)

# CLI 运行时切换
# clawcodex-dev --enable FEATURE_AGENT_TOOL --disable FEATURE_VERIFICATION_AGENT
```

##### 集成点

- **CLI 入口** (`src/cli.py`)：增加 `--enable` / `--disable` 参数，启动前调用 `registry.enable()` / `registry.disable()`
- **配置持久化**：复用 `src/config.py` 的 `~/.clawcodex/` 目录，新增 `features.json` 文件（独立于 `config.json`）
- **工具注册** (`src/tool_system/defaults.py`)：在 `build_default_registry()` 中加入 `feature_gated` 条件注册
- **Agent 循环** (`src/query/`)：关键决策点查询 `registry.is_enabled()` 判断是否启用 verification / memory 等步骤

#### 依赖

- Python `functools` / `inspect` / `os.environ`（标准库）
- 配置存储复用 `src/config.py` 的 `~/.clawcodex/` 目录 + `Path.home()`
- 无第三方依赖

---

#### F-69: Budget / Poor Mode 资源节俭模式

**状态**: ⏳ 待开始 | **优先级**: P1

#### 背景

CCB 的 `/poor` 命令开启「穷鬼模式」，跳过高消耗步骤（`extract_memories`、`verification_agent`），减小 context 窗口，减少 API token 消耗。clawcodex 当前无等价机制，用户无法在简单任务中自主降低资源消耗。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P69-A | BudgetMode 配置模型 | 定义节俭等级（off/light/medium/aggressive）、各等级行为矩阵 | ⏳ 待开始 | 2-3天 |
| P69-B | Agent 循环节俭钩子 | 在 query/agent loop 关键点插入节俭检查（跳过 memory recall、缩短思考预算等） | ⏳ 待开始 | 3-5天 |
| P69-C | Tool 级别节俭策略 | 降低搜索深度、禁用高消耗工具、减少结果条数 | ⏳ 待开始 | 2-3天 |
| P69-D | `/budget` CLI 斜杠命令 | 运行时切换节俭模式，查看当前消耗统计 | ⏳ 待开始 | 2-3天 |
| P69-E | Token 用量实时统计与告警 | 实时显示当前 session token 消耗，超阈值自动降级 | ⏳ 待开始 | 3-5天 |

#### 行为矩阵设计

| 行为 | off | light | medium | aggressive |
|------|:---:|:-----:|:------:|:----------:|
| extract_memories | ✅ | ✅ | ❌ | ❌ |
| verification_agent | ✅ | ❌ | ❌ | ❌ |
| search_depth | 10 | 5 | 3 | 1 |
| max_tool_calls/turn | 20 | 10 | 5 | 3 |
| context_window | max | 80% | 50% | 30% |
| 自动 Web 搜索 | ✅ | ✅ | ❌ | ❌ |

#### Agent 循环 Hook 点（具体集成位置）

```python
# src/query/query.py（或等价位置）—— Agent loop 主循环
class AgentLoop:
    def __init__(self, config: AgentConfig):
        self.budget = BudgetModeManager(config.budget_mode or "off")
        self.token_counter = TokenCounter()

    async def run(self, conversation):
        # ═══ Hook 点 1：Memory Recall（extract_memories）═══
        if self.budget.is_enabled("extract_memories"):
            memories = await self._extract_memories(conversation)
        else:
            memories = []
            logger.info("Budget mode: skipping extract_memories")

        # ═══ Hook 点 2：Agent loop 最大轮次限制 ═══
        max_turns = self.budget.get("max_tool_calls/turn")
        for turn in range(max_turns):
            # ═══ Hook 点 3：Verification Agent ═══
            if self.budget.is_enabled("verification_agent"):
                await self._run_verification(...)
            
            # ═══ Hook 点 4：Tool 调用消耗控制 ═══
            tool_result = await self._call_tool(...)
            self.token_counter.add(tool_result.token_usage)
            if self.token_counter.exceeds(self.budget.get("context_window")):
                logger.warning("Token budget exceeded, triggering auto-downgrade")
                current_level = self.budget.downgrade()
            
            # ═══ Hook 点 5：Web 搜索条件启用 ═══
            if tool_result.requires_web_search:
                if not self.budget.is_enabled("auto_web_search"):
                    continue  # 跳过 Web 搜索
```

#### 配置模型集成

```python
# src/models/configs.py 或 AgentConfig
@dataclass
class BudgetConfig:
    mode: str = "off"                     # off/light/medium/aggressive
    token_limit: int = 0                  # per-session token 阈值
    auto_downgrade: bool = False          # 超阈值自动降级
    downgrade_to: str = "medium"           # 降级目标

# 注入点：
# - src/query/config.py: QueryConfig 增加 budget 字段
# - src/cli.py: 增加 --budget light/medium/aggressive 参数
# - 斜杠命令注册: src/command_system/builtins.py 增加 /budget
```

#### 依赖

- 无第三方依赖
- 需集成到 `AgentConfig` / `SessionConfig` 中
- F-68 Feature Gate 可作为底层开关机制复用

---

#### F-70: Plugin 插件系统基础框架

**状态**: ⏳ 待开始 | **优先级**: P1

#### 背景

CCB 具备完整的 Plugin Marketplace 体系（安装/卸载/启用/禁用/浏览）。clawcodex 目前完全缺失插件化能力——所有扩展能力均通过硬编码集成或 `clawcodex_ext/` 二开目录实现。缺乏标准化的第三方插件安装与生命周期管理接口。

#### 子特性分解

| 编号 | 子特性 | 说明 | 状态 | 预计工作量 |
|:----:|--------|------|:----:|:----------:|
| P70-A | Plugin 协议/基类 | `BasePlugin` 接口定义（`on_load`/`on_unload`/`register_tools`/`register_commands`） | ⏳ 待开始 | 3-5天 |
| P70-B | Plugin 发现机制 | 扫描 `~/.clawcodex/plugins/` + `site-packages` entry_points | ⏳ 待开始 | 2-3天 |
| P70-C | Plugin 生命周期管理 | install/uninstall/enable/disable/upgrade 命令族 | ⏳ 待开始 | 5-7天 |
| P70-D | 沙箱隔离 | subprocess 隔离插件进程，通过 IPC 通信 | ⏳ 待开始 | 5-7天 |
| P70-E | Plugin 清单与元数据 | `plugin.yaml`/`pyproject.toml [tool.clawcodex.plugins]` 清单格式 | ⏳ 待开始 | 2-3天 |

#### BasePlugin 协议（精确接口）

```python
# src/services/plugin_system/base.py
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

class BasePlugin(ABC):
    """所有插件必须实现的基类。"""

    # 元数据（子类覆盖）
    name: str = ""
    version: str = "0.1.0"
    description: str = ""

    @abstractmethod
    async def on_load(self, context: "PluginContext") -> None:
        """插件加载时调用。
        
        PluginContext 包含：
        - registry: 工具注册表的引用，用于注册/注销工具
        - command_system: CLI 命令系统，用于注册斜杠命令
        - config: 插件配置字典
        - data_dir: 插件数据目录的 Path
        """
        ...

    @abstractmethod
    async def on_unload(self) -> None:
        """插件卸载时调用。清理资源（关闭连接、释放文件句柄等）。"""
        ...

    async def on_enable(self) -> None:
        """插件启用时调用（可选覆盖）。"""
        pass

    async def on_disable(self) -> None:
        """插件禁用时调用（可选覆盖）。"""
        pass

    def get_tools(self) -> list[Any]:
        """返回此插件提供的工具列表。
        
        返回的 Tool 实例（通过 build_tool() 创建）会在 on_load 后
        自动通过 registry.register() 注册。
        默认返回空列表，子类按需覆盖。
        """
        return []

    def get_commands(self) -> list[dict]:
        """返回此插件提供的斜杠命令列表。
        
        每个命令格式: {"name": str, "handler": callable, "description": str}
        默认返回空列表，子类按需覆盖。
        """
        return []


@dataclass
class PluginContext:
    """插件运行时上下文，通过 on_load() 注入。"""
    registry: "ToolRegistry"           # 工具注册表（用于 register/unregister）
    command_system: "CommandSystem"    # 命令系统（用于注册斜杠命令）
    config: dict[str, Any]             # 插件配置
    data_dir: Path                     # 插件数据持久化目录
```

#### Plugin 示例

```python
# ~/.clawcodex/plugins/todo_manager/__init__.py
from src.services.plugin_system.base import BasePlugin, PluginContext
from src.tool_system.build_tool import build_tool

class TodoPlugin(BasePlugin):
    name = "todo-manager"
    version = "1.0.0"
    description = "Manage todo lists"

    async def on_load(self, ctx: PluginContext):
        # 通过 PluginContext 访问框架能力
        self.data_file = ctx.data_dir / "todos.json"
        # ctx.registry 已在 base.py 中通过 get_tools() 自动注册
        # ctx.command_system 同理通过 get_commands() 自动注册

    async def on_unload(self):
        self.data_file = None

    def get_tools(self):
        return [
            build_tool(
                name="todo_add",
                input_schema={...},
                call=self._add_todo,
                description="添加待办事项",
            ),
        ]

    def get_commands(self):
        return [
            {"name": "todo", "handler": self._cmd_todo, "description": "Manage todos"},
        ]
```

#### 架构

```python
# src/services/plugin_system/
plugin_system/
├── base.py              # BasePlugin + PluginContext（协议类）
├── registry.py          # PluginRegistry（注册/发现/生命周期管理）
├── loader.py            # PluginLoader（importlib + entry_points 发现）
├── sandbox.py           # PluginSandbox（可选子进程隔离）
├── manager.py           # PluginManager（CLI 命令绑定）
└── schema.py            # PluginManifest（pydantic model 插件元数据）
```

#### 插件发现路径

```python
# 1. Python entry_points (pip 安装的包)
from importlib.metadata import entry_points
plugins = entry_points(group="clawcodex.plugins")

# 2. 用户目录手动安装
~/.clawcodex/plugins/
└── my-plugin/
    ├── plugin.yaml    # name, version, author
    └── __init__.py    # implements BasePlugin

# 3. 项目级插件 (repo 自带)
.clawcodex/plugins/
```

#### 依赖

- `importlib.metadata`（Python 3.8+ 标准库）
- `PyYAML`（yaml 配置解析，已有依赖）
- `pluggy`（可选，复用 pytest 插件框架，Python 纯实现）

---

#### F-71: 内置工具补齐（缺失工具批量实现）

**状态**: ⏳ 待开始 | **优先级**: P1

#### 背景

对比 CCB 的 60 个内置工具，clawcodex 当前 `tool_system/tools/` 仅约 46 个工具。缺失的约 14 个工具分布在 Agent 系统、Web 自动化、上下文检查、监控、通知等领域。多数工具可通过 Python 标准库或成熟第三方库直接实现。

#### 子特性分解

下表映射自 `CCB_MIGRATION_DESIGN.md §8.2` 的 15 个 CCB 特有工具，标注了代码库中现有实现状态：

| 编号 | 工具名(CCB) | CCB 来源 | clawcodex 实现 | 代码状态 |
|:----:|------------|---------|:----------------:|:--------:|
| P71-A | **AgentTool** | `@claude-code-best/builtin-tools` | ✅ `src/tool_system/tools/agent.py` | 已完成 |
| P71-B | **SkillTool** | builtin | ✅ `src/tool_system/tools/skill.py` | 已完成 |
| P71-C | **SendMessageTool** | builtin | ✅ `src/tool_system/tools/send_message.py` | 已完成 |
| P71-D | **TaskStopTool** | builtin | ✅ `src/tool_system/tools/task_stop.py` | 已完成 |
| P71-E | **TeamCreateTool** | builtin | ✅ `src/tool_system/tools/team.py` | 已完成 |
| P71-F | **TeamDeleteTool** | builtin | ✅ `src/tool_system/tools/team.py` | 已完成 |
| P71-G | **BriefTool** | builtin | ✅ `src/tool_system/tools/brief.py` | 已完成 |
| P71-H | **ExitPlanModeTool** | builtin | ✅ `src/tool_system/tools/plan_mode.py` | 已完成 |
| P71-I | **EnterPlanModeTool** | builtin | ✅ `src/tool_system/tools/plan_mode.py` | 已完成 |
| P71-J | **LSPTool** | builtin | ✅ `src/tool_system/tools/lsp.py` | 已完成 |
| P71-K | **ExecuteTool** | builtin | ⏳ 待实现 | 缺失 |
| P71-L | **CronCreate/Delete/ListTool** | builtin | ✅ `src/tool_system/tools/cron.py` | 已完成 |
| P71-M | **RemoteTriggerTool** | builtin | ❌ 待实现 | 缺失 |
| P71-N | **WebBrowserTool** | builtin | ⏳ 待实现 | 需 `playwright` |
| P71-O | **SnipTool** | builtin | ❌ 待实现 | 缺失 |

仅 **P71-K (ExecuteTool)**、**P71-M (RemoteTriggerTool)**、**P71-N (WebBrowserTool)**、**P71-O (SnipTool)** 4 个工具尚未实现。具体计划：

| 待实现工具 | 说明 | 依赖 | 预计工时 |
|-----------|------|:----:|:--------:|
| **ExecuteTool** | 代理工具调用执行，将另一个工具的调用委托给子 Agent | 无 | 3-5天 |
| **RemoteTriggerTool** | 远程触发工具，调用远程 clawcodex 实例上的操作 | `httpx` | 3-5天 |
| **WebBrowserTool** | 浏览器控制（打开 URL、点击、填表、截图） | `playwright` | 5-7天 |
| **SnipTool** | History snip — 截取历史消息片段用于上下文 | 无 | 2-3天 |

#### 实现模式（参考 `src/tool_system/build_tool.py`）

⚠️ **注意**: clawcodex 不使用 `BaseTool` 继承模式，而是使用 `Tool` dataclass + `build_tool()` 工厂函数模式。每个工具是一个通过 `build_tool()` 创建的 `Tool` 实例，核心字段如下：

| 字段 | 类型 | 说明 | 必需 |
|------|------|------|:----:|
| `name` | `str` | 工具名称（唯一标识） | ✅ |
| `input_schema` | `Mapping[str, Any]` | JSON Schema 格式的输入参数定义 | ✅ |
| `call` | `(dict, ToolContext) -> ToolResult` | 工具执行函数（同步或异步均可，框架自动适配） | ✅ |
| `description` | `str \| Callable` | 工具描述字符串或动态描述函数 | ✅ |
| `prompt` | `str \| Callable` | 工具在 system prompt 中的描述 | 推荐 |
| `is_enabled` | `Callable[[], bool]` | 是否启用（默认返回 True） | 可选 |
| `aliases` | `tuple[str, ...]` | 别名列表 | 可选 |
| `is_read_only` | `Callable[[dict], bool]` | 是否只读（影响权限检查） | 可选 |

```python
# ===== 正确实现示例 =====
# 文件位置：src/tool_system/tools/web_browser.py

from typing import Any
from src.tool_system.build_tool import build_tool, ToolResult

async def _web_browser_call(input: dict[str, Any], context: "ToolContext") -> ToolResult:
    """WebBrowserTool 的调用函数"""
    action = input.get("action")
    url = input.get("url")
    selector = input.get("selector")
    
    try:
        # 延迟导入 playwright，避免非必需环境安装
        from playwright.async_api import async_playwright
        
        if not hasattr(_web_browser_call, "_browser"):
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            _web_browser_call._browser = browser
            _web_browser_call._page = page
        
        page = _web_browser_call._page
        
        if action == "navigate":
            await page.goto(url, wait_until="networkidle")
            return ToolResult(name="web_browser", output={"status": "loaded", "url": url})
        elif action == "screenshot":
            bytes_data = await page.screenshot(full_page=True)
            return ToolResult(name="web_browser", output={"screenshot_size": len(bytes_data)})
        elif action == "click":
            await page.click(selector)
            return ToolResult(name="web_browser", output={"status": "clicked", "selector": selector})
        else:
            return ToolResult(name="web_browser", output={"error": f"Unknown action: {action}"}, is_error=True)
    except Exception as e:
        return ToolResult(name="web_browser", output={"error": str(e)}, is_error=True)


# 使用 build_tool 创建 Tool 实例
WebBrowserTool = build_tool(
    name="web_browser",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "click", "type", "screenshot"],
                "description": "要执行的操作类型",
            },
            "url": {
                "type": "string",
                "description": "目标 URL（仅在 navigate 时需要）",
            },
            "selector": {
                "type": "string",
                "description": "CSS 选择器（仅在 click/type 时需要）",
            },
            "text": {
                "type": "string",
                "description": "要输入的文本（仅在 type 时需要）",
            },
        },
        "required": ["action"],
    },
    call=_web_browser_call,
    description=lambda _input: "控制浏览器打开网页、点击、填表、截图（基于 Playwright）",
    prompt="""Web Browser 工具：打开 URL、点击元素、输入文本、截图。适用于需要浏览器渲染的内容。""",
    aliases=("browser", "chrome"),
    is_read_only=lambda _input: _input.get("action") == "screenshot",
    is_enabled=lambda: _check_playwright_available(),  # 动态检测 playwright 是否安装
)
```

#### 工具注册

所有新工具通过 `tool_registry.register()` 注册。可选通过 F-68 Feature Gate 控制启用：
```python
if registry.is_enabled("FEATURE_AGENT_TOOL"):
    tool_registry.register(AgentTool)
```

#### 依赖

- `playwright`（WebBrowserTool）
- `plyer` 或 `notify-py`（PushNotificationTool）
- `ptyprocess`（TerminalCaptureTool）
- 其余工具无第三方依赖

---

#### F-72: Multi-API 原生适配器扩展

**状态**: ⏳ 待开始 | **优先级**: P1

#### 背景

CCB 实现了 OpenAI/Gemini/Grok 三套独立 API 适配器（各有独立的 client 初始化、流式适配、模型映射表和错误处理）。clawcodex 通过 LiteLLM 间接支持 100+ 后端，但缺乏原生 SDK 适配器——这意味着某些 API 原生特性（如 Gemini 的 SafetySetting、OpenAI 的 structured output `response_format`、Grok 的 function calling 变体）可能无法通过 LiteLLM 泛化层完全暴露。

#### 子特性分解

| 编号 | 子特性 | 说明 | Python 依赖 | 预计工作量 |
|:----:|--------|------|:-----------:|:----------:|
| P72-A | OpenAI 原生适配器 | 使用 `openai` SDK 实现完整 API 调用链（stream/structured output/function call） | `openai` | 3-5天 |
| P72-B | Gemini 原生适配器 | 使用 `google-genai` SDK 实现 Gemini 完整调用（Safety/grounding/model 切换） | `google-genai` | 3-5天 |
| P72-C | Grok/xAI 原生适配器 | 使用 `openai` SDK（兼容接口）或 `requests` 实现 Grok 调用 | `requests` | 2-3天 |
| P72-D | 原生适配器自动选择 | 根据 `--provider` 自动选择原生适配器或回退到 LiteLLM | 无 | 2-3天 |
| P72-E | 平台专有特性映射 | 将各 API 专有能力（Safety/Grounding/TTS）映射为 Provider 能力标记 | 无 | 3-5天 |

#### 架构

```python
# src/providers/native/
native/
├── __init__.py           # 自动发现与注册 + NativeProviderFactory
├── base.py               # NativeProvider 基类（继承 BaseProvider）
├── capabilities.py       # 能力标记注册表
├── openai_adapter.py     # OpenAI 原生
├── gemini_adapter.py     # Gemini 原生
└── grok_adapter.py       # Grok 原生
```

##### NativeProvider 基类（继承现有关）

```python
# src/providers/native/base.py
from src.providers.base import BaseProvider, ChatResponse

class NativeProvider(BaseProvider):
    """原生 SDK 适配器的基类。
    
    继承自现有的 BaseProvider，保持 chat() / chat_stream() 接口不变。
    新增 capabilities 注册表用于标记平台专有能力。
    """

    # 平台专有能力标记（子类覆盖）
    capabilities: set[str] = set()

    @classmethod
    def check_capabilities(cls, required: set[str]) -> bool:
        """检查是否支持所需的平台专有能力。"""
        return required.issubset(cls.capabilities)

    @abstractmethod
    def get_provider_name(self) -> str:
        """返回 provider 标识，如 'openai' / 'gemini' / 'grok'"""
        ...

# src/providers/native/capabilities.py
# 能力常量定义
CAP_STRUCTURED_OUTPUT = "structured_output"   # response_format JSON Schema
CAP_STREAMING_TOOLS = "streaming_tools"        # 流式 function calling
CAP_VISION = "vision"                          # 图片理解
CAP_SAFETY_SETTINGS = "safety_settings"        # 安全设置（Gemini）
CAP_GROUNDING = "grounding"                    # 联网搜索（Gemini）
CAP_TTS = "tts"                                # 文本转语音
```

##### OpenAI 适配器示例

```python
# src/providers/native/openai_adapter.py
import os
from openai import OpenAI, Stream
from ..base import ChatResponse, ChatMessage
from .base import NativeProvider, CAP_STRUCTURED_OUTPUT, CAP_VISION

class OpenAIProvider(NativeProvider):
    capabilities = {CAP_STRUCTURED_OUTPUT, CAP_VISION}

    def __init__(self, api_key: str, base_url: str | None = None,
                 model: str | None = "gpt-4o"):
        super().__init__(api_key, base_url, model)
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = self._get_model()

    def get_provider_name(self) -> str:
        return "openai"

    def chat(self, messages, tools=None, **kwargs) -> ChatResponse:
        # 完整的 OpenAI SDK 调用，不经过 LiteLLM
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            **kwargs,
        )
        return ChatResponse(
            content=response.choices[0].message.content or "",
            model=response.model,
            usage=dict(response.usage or {}),
            finish_reason=response.choices[0].finish_reason or "",
        )

    def chat_stream(self, messages, tools=None, **kwargs):
        stream = self.client.chat.completions.create(
            model=self.model, messages=messages,
            tools=tools, stream=True, **kwargs,
        )
        for chunk in stream:
            yield chunk.choices[0].delta.content or ""
```

##### 自动选择与工厂

```python
# src/providers/native/__init__.py
from .openai_adapter import OpenAIProvider
from .gemini_adapter import GeminiProvider
from .grok_adapter import GrokProvider
from ..base import BaseProvider
from ..litellm_provider import LiteLLMProvider

# 注册原生适配器
_NATIVE_PROVIDERS: dict[str, type[NativeProvider]] = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "grok": GrokProvider,
}

def create_native_provider(provider_name: str,
                           config: dict) -> BaseProvider | None:
    """尝试创建原生适配器，失败返回 None（回退 LiteLLM）"""
    cls = _NATIVE_PROVIDERS.get(provider_name)
    if not cls:
        return None
    try:
        return cls(
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url"),
            model=config.get("default_model"),
        )
    except Exception:
        return None
```

#### 依赖

- `openai` SDK（pip install openai）
- `google-genai` SDK（pip install google-genai）
- `requests`（标准库替代也可，但 SDK 更可靠）

---

#### F-73: CI/CD 质量门禁与 PyPI 发布流水线

**状态**: ⏳ 待开始 | **优先级**: P0

#### 背景

CCB 配备完整的 CI/CD 基础设施：4 个 GitHub Actions（ci/publish/release/contributors）+ Codecov 覆盖率 + husky pre-commit 钩子。clawcodex 目前仅有一个 `upstream-detect.yml` workflow，**完全没有**代码质量门禁（lint/format/typecheck）、自动化测试和 PyPI 发布流水线。

#### 子特性分解

| 编号 | 子特性 | 说明 | 工具链 | 预计工作量 |
|:----:|--------|------|:------:|:----------:|
| P73-A | ruff lint/format CI | 在 push/PR 时自动运行 ruff lint + format 检查 | `ruff` | 1-2天 |
| P73-B | pytest 测试流水线 | 安装依赖 → 运行 orchestrator 测试 → 报告结果 | `pytest` | 1-2天 |
| P73-C | pre-commit 本地钩子 | ruff + 基础检查在 commit 前自动运行 | `pre-commit` | 1天 |
| P73-D | PyPI 自动发布 | tag push → build wheel → twine upload → 发布 GitHub Release | `build` + `twine` | 2-3天 |
| P73-E | 测试覆盖率门禁 | Codecov / coveralls 集成，覆盖率阈值保护 | `pytest-cov` | 1-2天 |
| P73-F | pyproject.toml 规范 | 完整声明 project metadata、entry_points、optional-dependencies、classifiers | 无 | 1天 |
| P73-G | mypy 类型检查（可选） | Python 3.10+ 类型标注验证 | `mypy` | 2-3天 |

#### CI 流水线设计

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install ruff && ruff check . && ruff format --check .

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - run: pytest tests/test_orchestrator_*.py -q --cov=src

  publish:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: [lint, test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install build twine
      - run: python -m build
      - run: twine upload dist/*
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_TOKEN }}
```

#### PyPI 发布配置

```toml
# pyproject.toml（已有扩展）
[project]
name = "clawcodex"
version = "0.x.y"
description = "Claude Code Python reimplementation with autonomous orchestration"
authors = [{name = "Chadwweng"}]
license = {text = "MIT"}
requires-python = ">=3.10"
dependencies = [...]

[project.scripts]
clawcodex-dev = "src.cli:main"
```

#### 本地 pre-commit 配置

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: ruff-lint
        name: ruff lint
        entry: ruff check --fix
        language: system
        types: [python]
      - id: ruff-format
        name: ruff format
        entry: ruff format
        language: system
        types: [python]
```

#### 依赖

- `ruff`（lint/format，纯 Rust 实现，毫秒级检查）
- `pytest` + `pytest-cov`（测试与覆盖率）
- `build` + `twine`（PyPI 发布）
- `pre-commit`（本地钩子）
- 所有工具均为 `[project.optional-dependencies] dev` 组

---

#### F-74: Sandbox / SSH Remote 沙箱远程执行

**状态**: ⏳ 待开始 | **优先级**: P2

#### 背景

CCB 支持 `sandbox-toggle` 命令将执行环境切换到沙箱模式，以及 SSH 远程执行命令。clawcodex 当前所有 Bash/Shell 执行均在本地，无沙箱隔离或远程执行能力。

#### 子特性分解

| 编号 | 子特性 | 说明 | Python 依赖 | 预计工作量 |
|:----:|--------|------|:-----------:|:----------:|
| P74-A | Sandbox 执行器抽象 | "Bash 沙箱"接口抽象：local/docker/ssh 三种后端 | 无 | 3-5天 |
| P74-B | Docker 沙箱执行 | 在 Docker 容器内执行 shell 命令（临时容器 or 常驻容器） | `docker-py` | 3-5天 |
| P74-C | SSH 远程执行 | 通过 SSH 在远程主机执行 shell 命令 | `asyncssh` | 3-5天 |
| P74-D | `/sandbox` CLI 命令 | 查看/切换当前 sandbox 模式 | 无 | 2-3天 |
| P74-E | 沙箱配置文件 | `~/.clawcodex/sandbox.json`：默认模式/超时/容器镜像/SSH 主机列表 | 无 | 1-2天 |

#### 架构

```python
# src/services/sandbox/
sandbox/
├── base.py              # SandboxExecutor（抽象基类）
├── local.py             # LocalExecutor（直接 subprocess，当前行为）
├── docker.py            # DockerExecutor（docker run 沙箱）
├── ssh.py               # SSHExecutor（asyncssh 远程执行）
├── manager.py           # SandboxManager（全局切换/状态）
└── config.py            # SandboxConfig（pydantic model）
```

#### SandboxExecutor 抽象接口

```python
# src/services/sandbox/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SandboxResult:
    """沙箱命令执行结果。"""
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int = 0
    error: str | None = None


@dataclass
class SandboxConfig:
    """沙箱配置（每个执行器有自己的子类扩展）。"""
    timeout: int = 30          # 单条命令超时秒数
    work_dir: str = "/tmp"     # 工作目录
    env_vars: dict[str, str] = field(default_factory=dict)


class SandboxExecutor(ABC):
    """沙箱执行器抽象基类。"""

    type: str = ""  # "local" / "docker" / "ssh"（子类覆盖）

    def __init__(self, config: SandboxConfig):
        self.config = config

    @abstractmethod
    async def execute(self, command: str) -> SandboxResult:
        """在沙箱环境中执行一条命令。"""
        ...

    @abstractmethod
    async def upload_file(self, local_path: str, remote_path: str) -> None:
        """将本地文件上传到沙箱环境。"""
        ...

    @abstractmethod
    async def download_file(self, remote_path: str, local_path: str) -> None:
        """从沙箱环境下载文件到本地。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """释放沙箱资源（关闭连接、停止容器等）。"""
        ...


class SandboxManager:
    """沙箱管理器（全局单例）。"""

    _current: SandboxExecutor | None = None

    @classmethod
    def get_current(cls) -> SandboxExecutor:
        """返回当前沙箱，默认返回 LocalExecutor。"""
        if cls._current is None:
            cls._current = LocalExecutor(SandboxConfig())
        return cls._current

    @classmethod
    def set_current(cls, executor: SandboxExecutor) -> None:
        """切换当前沙箱。"""
        if cls._current is not None:
            asyncio.ensure_future(cls._current.close())
        cls._current = executor
```

#### 本地执行器示例

```python
# src/services/sandbox/local.py
import asyncio
import os
import time
from .base import SandboxExecutor, SandboxResult

class LocalExecutor(SandboxExecutor):
    type = "local"

    async def execute(self, command: str) -> SandboxResult:
        """本地 subprocess 执行（直接当前行为）。"""
        start = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.work_dir,
            env={**dict(os.environ), **self.config.env_vars},  # 保留环境变量
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return SandboxResult(
                exit_code=-1, stdout="", stderr="",
                error=f"Command timed out after {self.config.timeout}s",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return SandboxResult(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else "",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    async def upload_file(self, local_path: str, remote_path: str) -> None:
        # Local：文件复制即可
        import shutil
        shutil.copy2(local_path, remote_path)

    async def download_file(self, remote_path: str, local_path: str) -> None:
        import shutil
        shutil.copy2(remote_path, local_path)

    async def close(self) -> None:
        pass  # Local 无需清理
```

#### Docker 执行器核心逻辑

```python
# src/services/sandbox/docker.py
class DockerExecutor(SandboxExecutor):
    type = "docker"

    def __init__(self, config: DockerSandboxConfig):
        super().__init__(config)
        import docker
        self.client = docker.from_env()
        self.container: docker.models.containers.Container | None = None

    async def ensure_container(self):
        """延迟创建容器（首次 execute 时创建）。"""
        if self.container is None:
            self.container = self.client.containers.create(
                image=self.config.image or "ubuntu:22.04",
                command=["sleep", "infinity"],
                detach=True,
                working_dir=self.config.work_dir,
                environment=self.config.env_vars,
            )
            self.container.start()

    async def execute(self, command: str) -> SandboxResult:
        await self.ensure_container()
        import time
        start = time.monotonic()
        exit_code, output = self.container.exec_run(
            cmd=["bash", "-c", command],
            timeout=self.config.timeout,
        )
        return SandboxResult(
            exit_code=exit_code,
            stdout=output.decode() if isinstance(output, bytes) else str(output),
            stderr="",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    # ... upload_file/download_file/close via docker cp / container.stop
```

#### BashTool 集成点

```python
# src/tool_system/tools/bash.py（修改点）
class BashTool:
    async def call(self, input: dict, context: ToolContext) -> ToolResult:
        from src.services.sandbox.manager import SandboxManager
        cmd = input.get("command", "")
        executor = SandboxManager.get_current()
        result = await executor.execute(cmd)
        return ToolResult(
            name="bash",
            output={
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
            },
            is_error=result.exit_code != 0,
        )
```

#### 使用模式

```bash
# CLI 切换
clawcodex-dev sandbox set docker --image python:3.11-slim
clawcodex-dev sandbox set ssh --host dev-server --user bot
clawcodex-dev sandbox status    # 查看当前模式

# BashTool 调用自动使用当前沙箱
# BashTool.call() → SandboxManager.current.execute(command)
```

#### 依赖

- `docker-py`（Docker SDK，可选）
- `asyncssh`（SSH 异步客户端，可选）
- `paramiko`（SSH 同步客户端，备选）
- 均为 optional-dependencies

---

### 实施总览

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

> **建议**: F-73（CI/CD 质量门禁）是**首个必须实施**的特性——无质量门禁就无法保证后续所有特性开发的质量。紧随其后的是 F-68（Feature Gate）作为架构基础设施。F-69（Budget Mode）和 F-71（工具补齐）可并行开发以快速提升用户体验和功能完整度。

---

## 附录：F-Number 快速索引

| F-Number | 名称 | 章节 | 状态 |
|----------|------|------|------|
| F-2 | Team 成员管理 | §2.2 | 📋 规划中 |
| F-3 | MCP 扩展功能 | §2.4 | ✅ 基础完成 |
| F-4 | 结构化输出增强 | §2.3 | 📋 适配器完成 |
| F-9 | /goal 目标管理 | §2.6 | ⏳ 待实现 |
| F-10 | ExecuteExtraTool | §2.7 | 🔄 规划中 |
| F-11 | sessionStorage 容量 | §2.10 | 🔄 规划中 |
| F-12 | cacheWarning 容量 | §2.11 | 🔄 规划中 |
| F-13 | 记忆作用域隔离 | §2.5 | ✅ 完成 |
| F-16 | Auto 模式 | §2.13 | 📋 规划中 |
| F-18 | CreateAgentTool | §2.9 | 🔄 规划中 |
| F-20 | Agent 进度汇报 | §2.1 | ✅ 完成 |
| F-22 | Cron 系统 | §五 | 🔄 进行中 |
| F-36 | LocalTracker | §1.1.1 | ✅ 完成 |
| F-37 | PR 检视意见自动修复 | §1.1.2 | 📋 规划中 |
| F-38 | 验证与报告闭环 | §1.1.3 | ✅ 完成 |
| F-39 | Issue 重跑入口 | §1.1.4 | ✅ 完成 |
| F-40 | ProgressReporter Sink | §1.2.2 | 📋 设计完成 |
| F-41 | Coordinator 工具集 | §1.3.4 | ✅ 完成 |
| F-42 | Workspace 策略 | §1.2.1 | ✅ 完成 |
| F-43 | CLI 模型切换 | §3.1 | ✅ 完成 |
| F-44 | 人工检视闸门 | §1.4.2 | ✅ 完成 |
| F-45 | Tool-call 审计 | §1.3.3 | ✅ 完成 |
| F-46 | permission_mode 拆分 | §3.2 | 📋 设计完成 |
| F-47 | Settings 重构 | §3.3 | ✅ 完成 |
| F-48 | src/ 解耦方案 | §4.1 | 📋 设计完成 |
| F-49 | 会话统一存储 | §1.4.2 | 📋 设计完成 |
| F-50 | SOP 转换器固化 | §4.2 | 📋 设计完成 |
| F-51 | AgentRunner 空转检测 | §1.3.1 | ✅ 完成 |
| F-52 | SDK→Tool 注册 | §4.3 | 📋 设计完成 |
| F-53 | Tool→CLI 命令映射 | §4.4 | 📋 设计完成 |
| F-54 | 运行期可观测性 | §1.3.2 | 📋 设计完成 |
| F-55 | SOP 分组策略增强 | §4.2.1 | ✅ 完成 |
| F-60 | Pipe IPC 群控 | §7.1 | ⏳ 待开始 |
| F-61 | Computer Use | §7.2 | ⏳ 待开始 |
| F-62 | Chrome 自动化 | §7.2 | ⏳ 待开始 |
| F-63 | Channels 通知 | §7.3 | ⏳ 待开始 |
| F-64 | Voice Mode | §7.3 | ⏳ 待开始 |
| F-65 | Langfuse 可观测 | §7.4 | ⏳ 待开始 |
| F-66 | ACP 协议 | §7.4 | ⏳ 待开始 |
| F-67 | Buddy/Proactive | §7.5 | ⏳ 待开始 |
| F-68 | Feature Gate | §7.6 | ⏳ 待开始 |
| F-69 | Budget/Poor Mode | §7.5 | ⏳ 待开始 |
| F-70 | Plugin 系统 | §4.3 | ⏳ 待开始 |
| F-71 | 内置工具补齐 | §7.6 | ⏳ 待开始 |
| F-72 | Multi-API 适配器 | §7.2 | ⏳ 待开始 |
| F-73 | CI/CD 流水线 | §7.6 | ⏳ 待开始 |
| F-74 | Sandbox 沙箱 | §7.2 | ⏳ 待开始 |
| F-75 | 工具调用统计 | §2.8 | 📋 设计完成 |
| F-78 | Issue 语义澄清 | §2.12 | 📋 规划中 |
| F-80 | Agent 间交互 | §2.14 | 📋 规划中 |
| F-81 | Native 模块系统 | §4.4 | ⏳ 待开始 |
| F-82 | Remote Control | §7.1 | ⏳ 待开始 |
| F-83 | Ultraplan 规划 | §7.5 | ⏳ 待开始 |
| F-84 | Context Collapse | §7.5 | ⏳ 待开始 |
| F-85 | Templates 模板 | §7.6 | ⏳ 待开始 |
| F-86 | Kairos/Brief 调度 | §7.5 | ⏳ 待开始 |
| F-87 | Workflow Scripts | §7.5 | ⏳ 待开始 |
| F-88 | Explore/Plan Agent | §7.5 | ⏳ 待开始 |