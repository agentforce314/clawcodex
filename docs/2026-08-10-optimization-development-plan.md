# Nuos/clauderuntime 七组件优化开发方案

> **目标项目**：`Nuos/clauderuntime` / ClawCodex  
> **基线 commit**：`241d704480c0e4aa1bfb97c607a5e2e13e871e46`  
> **对照目标**：`Dive into Claude Code` 对 Claude Code v2.1.88 的七组件行为与设计原则  
> **目标原则**：不做机械 TypeScript 文件翻译；优先复刻 **行为契约、状态边界、安全不变量、数据流、故障恢复和上下文管理语义**。  
> **范围限制**：以下优化只围绕 User/Interfaces、Agent Loop、Permission、Tools、State & Persistence、Context & Memory、Execution Environment 七个组件；Hooks、MCP、Subagent、Worktree 等按其所依附组件纳入，不另立“第八组件”。

---

# 0. 开发目标与验收口径

## 0.1 当前基线

依据当前源码抽样，项目已达到约 **86/100** 的七组件综合成熟度。下一阶段不应以“继续堆工具/命令”为主要目标，而应转为：

1. **先修安全不变量**：workspace boundary、权限/工具属性；
2. **建立零红测试 baseline**；
3. **统一所有 surface 的 Query/Permission/State 合同**；
4. **把 Loop 的预算、恢复、调度从“可用”提升到“可证明”**；
5. **把 Permission 与 Execution isolation 分成两条独立防线**；
6. **把 persistence/resume/fork/rewind 变成明确状态协议**；
7. **把 context compaction 从“存在”提升到“可观察、可验证、可回归”**。

## 0.2 最终目标

建议将下一里程碑定义为：

> **Seven-Component Parity 90+：七组件主干全部具备可执行行为契约；P0 safety invariant 全绿；所有入口共享同一核心路径；五层 context pipeline 可观测；Session 恢复不携带陈旧信任；Tool/Permission/Execution 三者边界清晰。**

### 目标指标

| 指标 | 当前估计 | 下一目标 |
|---|---:|---:|
| 七组件结构覆盖 | ~92% | ≥96% |
| 行为 parity | ~86% | ≥92% |
| 安全语义 parity | ~76% | ≥90% |
| 核心 Python 测试失败数 | 需以 CI 冻结 | **0** |
| TUI 已知失败 | 8（TODOS 记录） | **0** |
| workspace boundary escape | 已知 P0 | **0 case** |
| 跨 surface 契约差异 | 未量化 | **0 blocking mismatch** |
| compaction 语义回归 | 未统一量化 | 建立固定 corpus + invariant |
| Query 单文件职责 | 高耦合 | 拆成稳定子模块，行为无变化 |

---

# 1. 总体 Workflow

建议不要直接在七个模块同时开工，而采用“安全门 → 核心门 → 状态门 → 体验门”的顺序。

```text
W0  基线冻结 / Evidence Freeze
 ↓
W1  零红测试基线 / Baseline Green
 ↓
W2  P0 Safety Invariant 修复
 ↓
W3  Agent Loop 预算与生命周期收口
 ↓
W4  Permission × Execution 双边界强化
 ↓
W5  Tool Contract 与并发属性收口
 ↓
W6  State/Persistence 恢复协议
 ↓
W7  Context/Memory 可观测与语义验证
 ↓
W8  Multi-Surface Contract 统一
 ↓
W9  Parity/Eval/Release Gate
```

## W0 — 基线冻结

**产出**：

- `docs/parity/current-baseline.md`
- `docs/parity/seven-component-scorecard.yaml`
- 当前 main commit SHA
- Python/Node/OS 版本矩阵
- 测试集列表与运行耗时
- 已知 failing test manifest
- 七组件 → 文件/测试映射

**验收门**：后续每个 PR 必须可以说明：

```text
改了哪个七组件？
改变了哪个 contract/invariant？
新增了什么 test？
是否改变 Claude Code parity？
是否是产品差异而非 parity bug？
```

## W1 — 零红基线

优先处理 `TODOS.md` 已明确的 TUI 8 failure、Advisor parity failure、workspace boundary failure。

**原则**：禁止接受“known failures 继续存在但新增功能已通过”的常态。只有确实无法短期修复的测试才能进入 quarantine，并必须写：owner、原因、到期时间、解除条件。

## W2 — P0 安全不变量

先修：

- workspace read/write boundary；
- symlink/junction/case-fold/drive-relative 路径；
- Advisor tool metadata；
- deny-first rule precedence；
- tool prefilter 与 runtime check 一致。

## W3 — Agent Loop 预算与生命周期

完成：

- `max_turns` 全入口统一；
- `max_cost_usd` 强制 backstop；
- retry/compaction/subagent 成本纳入预算；
- scheduler/notification 从长 turn 解耦；
- Query state machine 拆分但不改行为。

## W4 — Permission × Execution 双边界

目标：任何 action 要同时满足：

```text
Policy says YES
AND
Execution boundary contains it
```

而不是把路径边界全部寄托在 permission 逻辑。

## W5 — Tool Contract 收口

将 side-effect、read-only、concurrency-safe、permission class、result budget、sandbox requirement 等集中描述，防止 Advisor 类属性漂移。

## W6 — Persistence 恢复协议

明确 transcript event schema、resume/fork/rewind、sidechain、permission reset、crash consistency。

## W7 — Context 可观测性

五层 pipeline 每次运行输出结构化 trace；固定语义 corpus；确保压缩不是只追求“省 token”。

## W8 — Multi-Surface Contract

CLI / Headless / Agent Server / TUI / Desktop / VS Code 使用相同 typed event contract 和相同 Query/Permission semantics。

## W9 — Release Gate

发布必须同时满足：

- safety suite green；
- parity suite green；
- platform matrix green；
- long-horizon eval 无显著退化；
- context/token/cache 指标无重大退化；
- 文档 scorecard 更新。

---

# 2. 待优化列表（Backlog）

下面给出建议的工程 backlog。优先级采用 P0/P1/P2/P3。

| ID | 组件 | 优先级 | 待优化项 | 验收结果 |
|---|---|---:|---|---|
| IF-01 | Interfaces | P0 | 修复 8 个已知 TUI vitest failures | UI suite 0 failure |
| IF-02 | Interfaces | P1 | 统一 CLI/TUI/Desktop/VSCode event contract | 同一 fake turn 在四 surface 的核心 event 序列一致 |
| IF-03 | Interfaces | P2 | 统一 interrupt/cancel/approval/resume 语义 | contract tests 全绿 |
| IF-04 | Interfaces | P2 | 增加 SDK-like programmatic API 的稳定版本契约 | SemVer + schema fixtures |
| AL-01 | Agent Loop | P1 | `settings.max_turns` 真正接入所有 Query path | 所有入口均可硬终止 |
| AL-02 | Agent Loop | P1 | `max_cost_usd` 强制 backstop | 达到阈值无继续模型/工具副作用 |
| AL-03 | Agent Loop | P1 | 拆分巨型 `query.py` | 无行为差异，职责边界可测试 |
| AL-04 | Agent Loop | P1 | scheduler/notification 与 turn worker 解耦 | AskUserQuestion 30min 不阻塞 tick |
| AL-05 | Agent Loop | P2 | 统一 retry/fallback/abort 终态 | terminal reason 可枚举、可持久化 |
| AL-06 | Agent Loop | P2 | compat path 收口 | 不存在第二套隐式 loop |
| PM-01 | Permission | **P0** | workspace boundary E2E 阻断 | read/write outside root 全部 deny |
| PM-02 | Permission | P0 | 路径 canonicalization + symlink/junction 防逃逸 | property/fuzz suite 0 escape |
| PM-03 | Permission | P1 | 增加 Claude Code Parity Mode 默认 ask | parity mode 与产品 full-access mode 分离 |
| PM-04 | Permission | P1 | pre-trust extension/load gate | 未信任 workspace 不执行用户 hooks/MCP |
| PM-05 | Permission | P2 | PermissionDecision 结构化 reason/audit | 每次 allow/ask/deny 可追溯 |
| TL-01 | Tools | **P0** | Advisor read-only/concurrency-safe 属性修正 | parity + smoke tests green |
| TL-02 | Tools | P1 | Tool metadata 单一真相源 | scheduler/permission/schema 共用 descriptor |
| TL-03 | Tools | P1 | 并发 read / 串行 write 的 invariant 强化 | concurrency stress suite |
| TL-04 | Tools | P2 | 结果标准化与 content budget 一致 | 所有 provider/tool result contract 一致 |
| TL-05 | Tools | P2 | MCP/tool namespace collision 与 supply-chain policy | 冲突/恶意 server 可阻断 |
| ST-01 | State | P1 | transcript event schema versioning | 可迁移、可回放、可审计 |
| ST-02 | State | P1 | resume/fork/rewind contract | E2E 恢复测试固定 |
| ST-03 | State | P1 | resume 不恢复 session-scoped permission | 安全 invariant 固化 |
| ST-04 | State | P2 | sidechain 与 parent summary-only contract | subagent history 不污染 parent context |
| ST-05 | State | P2 | crash consistency / concurrent writer | kill -9 后 transcript 可恢复 |
| CT-01 | Context | P1 | 五层 pipeline trace/observability | 每层 tokens-before/after/reason 可见 |
| CT-02 | Context | P1 | semantic preservation corpus | compact 后核心约束/文件状态不丢 |
| CT-03 | Context | P1 | prompt-cache regression gate | stable prefix 不因无关动态字段失效 |
| CT-04 | Context | P2 | memory provenance / stale invalidation | memory 来源、时间、作用域可追踪 |
| CT-05 | Context | P2 | context collapse / auto-compact 可恢复性 | full transcript 始终可重建 |
| EX-01 | Execution | **P0/P1** | workspace boundary 下沉至执行后端再次校验 | Permission 漏判时 backend 仍拒绝 |
| EX-02 | Execution | P1 | 引入独立 SandboxBackend 接口 | authorize 与 isolate 分离 |
| EX-03 | Execution | P1 | FS/network/env 最小权限 | sandbox profile 可配置、可审计 |
| EX-04 | Execution | P2 | process cleanup / timeout / orphan 统一 | 子进程零泄漏 |
| EX-05 | Execution | P2 | worktree/remote execution parity | cwd、git、permission、transcript 一致 |
| X-01 | 跨组件 | P1 | 七组件 scorecard 自动生成 | 每个 release 有可比较报告 |
| X-02 | 跨组件 | P1 | 文档漂移检测 | FEATURE_LIST 与源码/测试状态同步 |
| X-03 | 跨组件 | P2 | fault injection suite | 网络/磁盘/流式/kill/满盘故障可恢复 |
| X-04 | 跨组件 | P2 | long-horizon eval | compaction/recovery 后任务完成率不显著下降 |

---

# 3. 当前项目问题、缺陷与不足

## 3.1 安全不变量存在“模块实现很强，但 E2E 没锁住”的问题

最典型是 workspace boundary：权限模块拥有大量 filesystem/path 逻辑，但 E2E read/write 仍能失败。这说明问题很可能不只是一个 matcher bug，而是**某条工具执行路径绕过了预期 gate，或不同 path canonicalization 语义不一致**。

优化时禁止只修测试表面，应追踪完整调用链：

```text
model tool_use
  → registry lookup
  → schema validate
  → permission input normalization
  → permission decision
  → workspace boundary
  → pre-tool hook
  → executor/backend
  → filesystem primitive
```

必须在 permission 层与 execution 层各有一道边界检查，形成 defense in depth。

## 3.2 “Full Access by default” 与 Claude Code parity 目标存在结构性冲突

当前产品可以继续保留 Full Access，但必须把“产品体验”与“Claude Code parity”拆开：

```text
Product profile:       full_access / low-friction
Claude parity profile: default ask / deny-first
Managed profile:       organization policy ceiling
```

否则项目一方面声称复刻 Claude Code 权限结构，一方面默认行为却不一致，后续 parity test 会不断产生模糊判断。

## 3.3 Agent Loop 已经太集中

`src/query/query.py` 体量已进入典型“God module”风险区。继续往里堆 cache/provider/hook/subagent/retry/context 功能，会导致：

- 修改一个 provider 影响 tool loop；
- compaction 改动影响 terminal state；
- abort/retry 与 hook 异常互相覆盖；
- 测试只能做大集成，难做纯状态机单元测试。

建议“拆职责，不改控制流”，先提取纯函数和状态机，不要同时重写架构。

## 3.4 Settings 有“定义了但不执行”的 configuration illusion

`max_cost_usd` / `settings.max_turns` 的问题属于高风险配置假象：用户看到配置存在，以为受保护，实际关键入口不执行。

所有安全/预算设置必须满足：

```text
Schema validation
  ≠ Done
Load
  ≠ Done
Apply to runtime
  ≠ Done
Enforce at boundary
  = Done
```

## 3.5 Context 系统成熟，但缺少可解释性

五层 pipeline 已经存在，下一步主要风险是：

- 哪一层触发？
- 丢了哪些内容？
- summary 是否保留了当前任务约束？
- 为什么 token 变少但任务质量下降？
- cache hit 为什么突然掉？

所以必须增加 context decision trace，而不是再增加第六种压缩策略。

## 3.6 Persistence 需要从“文件能保存”升级到“状态协议可证明”

JSONL 是基础，但 Claude Code 的价值在于：resume/fork/rewind 与 compaction/permission/subagent 的组合语义。建议把 transcript 视为 event log，而不仅是 message dump。

## 3.7 测试规模大，但“已知红 baseline”会削弱测试价值

项目当前已经有大量 parity/E2E 测试，这是优势。但只要主干长期允许已知失败存在，开发者会开始习惯忽略红色输出。应把“0 known red”视为开发基础设施，不是美观指标。

---

# 4. 七组件模块级优化方案与单元测试设计

# 4.1 User / Interfaces

## 目标结构

```text
CLI ───────┐
Headless ──┤
TUI ───────┤
Desktop ───┤ → RuntimeClient / EventProtocol → Query Runtime
VS Code ───┤
SDK/API ───┘
```

Surface 只负责：

- 输入转换；
- event render；
- permission/user answer；
- interrupt/cancel；
- session selector。

禁止 surface 自己实现：

- 第二套 tool loop；
- 第二套 permission policy；
- 第二套 context assembly；
- 自己解释 terminal reason。

## 优化方向

### IF-A. 定义统一 RuntimeEvent schema

建议最小事件：

```text
request.start
model.delta.text
model.delta.thinking
tool.start
tool.progress
tool.complete
permission.request
permission.resolved
compact.start
compact.complete
session.persisted
request.terminal
error
```

每个事件必须含：

- `session_id`
- `request_id`
- `sequence`
- `timestamp`
- `source`
- payload schema version

### IF-B. Surface Contract Test

使用同一个 FakeProvider + FakeTool，分别驱动 CLI/headless/agent-server/TUI gateway/Desktop gateway，比较“去掉纯渲染事件后的 canonical event sequence”。

### IF-C. 统一取消语义

Ctrl+C / ESC / API cancel 都必须映射到同一 `AbortController`/terminal reason，不能 surface 本地把状态改成“看起来取消”但后台仍在跑。

## 单元测试方向

- `test_event_sequence_monotonic.py`：sequence 严格单调；
- `test_cancel_maps_to_same_terminal.py`：多入口 cancellation 同终态；
- `test_permission_overlay_roundtrip.py`：ask → answer → loop resume；
- `test_stream_text_not_buffered.py`：chunk 及时转发；
- `test_surface_does_not_execute_tools.py`：UI layer 不直接调用 concrete executor；
- `test_resume_uses_same_runtime.py`：恢复 session 不开第二套 engine。

## 组件验收门

- 所有 surface canonical events 相同；
- UI 单测 0 failure；
- 不存在 surface-specific permission bypass。

---

# 4.2 Agent Loop

## 目标结构

保留一个核心 Query Loop，但拆分职责：

```text
src/query/
  query.py                 # public façade，薄
  loop.py                  # while/async generator control flow
  state.py                 # QueryState / immutable transitions
  model_call.py            # provider call + retry + fallback
  tool_round.py            # tool-use partition/dispatch/result collect
  recovery.py              # token cap / prompt-too-long / stream fallback
  budget.py                # turns/tokens/cost/time
  terminal.py              # stop reasons
  context_stage.py         # compression/context pre-model
```

## 优化方向

### AL-A. BudgetGuard

统一预算：

```python
BudgetGuard(
    max_turns,
    max_cost_usd,
    max_input_tokens,
    max_output_tokens,
    deadline,
)
```

检查点至少放在：

1. model call 前；
2. retry 前；
3. tool round 前；
4. subagent spawn 前；
5. compact 模型调用前。

### AL-B. Terminal reason 枚举

建议固定：

```text
completed_text
max_turns
max_cost
context_overflow
aborted
hook_stopped
model_error
tool_failure_guard
internal_error
```

禁止用任意字符串散落判断。

### AL-C. Scheduler 独立

将 scheduled tasks / background completion notifications 从 `_run_worker` 的 idle branch 移到独立 scheduler service/thread/task。前台等待用户不能阻塞后台时钟。

### AL-D. 保留单 Loop，不引入 planning graph

论文的核心设计是 minimal scaffolding；优化目标不是把 Query 改成 LangGraph，而是让现有 ReAct loop 更可靠。

## 单元测试方向

- max_turns 在 0/1/N 边界；
- max_cost 在最后一次 model call 后立即停止；
- retry 不绕过预算；
- fallback model 只切一次且 session-sticky 范围明确；
- prompt-too-long → compact → retry → terminal 的分支；
- abort 在 model streaming、tool execution、permission wait 三处；
- hook stop 不再进入下一轮；
- tool failure loop guard 连续相同失败；
- empty-turn continuation nudge 上限；
- scheduler 在 AskUserQuestion 持续等待时仍每秒 tick。

## 组件验收门

- 每个 stop condition 有独立测试；
- Query core coverage ≥95%；
- 配置项不存在“validated but unused”。

---

# 4.3 Permission System

## 目标不变量

### Invariant P1 — Deny-first

```text
DENY always wins over ASK and ALLOW
```

### Invariant P2 — Unknown risky action asks/denies

Parity profile 下：未知有副作用 action 不得 silent allow。

### Invariant P3 — Workspace containment

任何 FS read/write/edit/shell redirect 最终 canonical target 必须位于允许根内，除非存在显式授权。

### Invariant P4 — Permission ≠ Sandbox

Permission 通过不代表可越过 Execution isolation。

## 优化方向

### PM-A. 两阶段路径校验

```text
Phase 1: lexical normalization
Phase 2: filesystem resolution (realpath, symlink/junction)
```

Windows 还需：

- casefold；
- drive root；
- drive-relative `C:foo`；
- UNC；
- junction/reparse point。

POSIX 还需：

- symlink；
- `..`；
- bind mount 情况至少 fail-closed。

### PM-B. PermissionProfile

```text
parity_default
full_access
managed_locked
plan
accept_edits
auto
```

将“产品默认”与“Claude parity 默认”从代码分支改成 profile 配置。

### PM-C. Pre-trust gate

用户尚未信任 workspace 时：

- 不执行 project hooks；
- 不自动启动 project MCP；
- 不读取会导致执行的 project config；
- managed/user policy 可先加载。

### PM-D. 决策审计结构

`PermissionDecision` 应保存：

```text
decision: allow|ask|deny
matched_rule
mode
hook_effect
classifier_effect
canonical_target
reason
bypass_immune_checks
```

## 单元测试方向

### Path property tests

随机生成：

- `../`
- 多重 `..`
- symlink chain
- Windows mixed slash
- mixed case
- drive-relative
- UNC
- percent/quote/space
- shell redirect `>`, `>>`, `<`

断言：不允许 target 永远无法穿越 root。

### Rule precedence tests

- broad deny + narrow allow → deny；
- MCP server deny + tool allow → deny；
- hook allow + rule deny → deny；
- bypassPermissions 下 bypass-immune deny 仍生效。

### Resume tests

- session A grant 不自动进入 resumed session；
- managed deny 始终存在。

## 安全验收门

任何 workspace escape 都是 release blocker。

---

# 4.4 Tools

## 优化方向

### TL-A. ToolDescriptor 单一真相源

建议：

```python
@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    schema: dict
    read_only: bool
    concurrency_safe: bool
    side_effect_class: str
    permission_class: str
    result_budget: int | None
    sandbox_required: bool
    deferred: bool
```

Registry、permission、scheduler、ToolSearch、provider schema generation 全部读取同一 descriptor。

这直接防止 Advisor 属性漂移。

### TL-B. 并发模型固定为“并发安全读 + 排他写”

工具 partition 必须由 metadata 决定，并测试结果顺序保持 model tool_use 顺序，即使实际执行并行。

### TL-C. Tool Result Contract

规范：

```text
tool_use_id
name
ok/error
model_content
ui_metadata
attachments
full_output_ref
metrics(duration, bytes, tokens)
```

不要让 Desktop/TUI 各自猜字段。

### TL-D. MCP 视为外部供应链

MCP tool 必须经过同一：

- name collision；
- permission；
- schema validation；
- output validation；
- audit；
- context budget。

## 单元测试方向

- 每个 tool descriptor 默认值 parity；
- Advisor regression；
- duplicate tool names；
- builtin wins over MCP 或明确冲突策略；
- deferred tool 初始不进入完整 schema；
- ToolSearch 加载后可调用；
- concurrent-safe tools 真并行，writes 串行；
- 并行执行但 results emitted in request order；
- tool error normalization 跨 provider 一致；
- oversized result 自动 content ref。

---

# 4.5 State & Persistence

## 目标模型

把 JSONL 明确升级成 event log：

```text
SessionEvent
  message.user
  message.assistant
  tool.result
  compact.boundary
  content.replacement
  file.checkpoint
  permission.audit      # 可审计，但 session grant 不应恢复
  subagent.reference
  terminal
```

## 优化方向

### ST-A. Schema version

每行 event 包含：

```text
schema_version
uuid
parent_uuid
session_id
timestamp
type
payload
```

### ST-B. Resume

恢复流程：

```text
load transcript
→ validate/migrate events
→ rebuild message chain
→ patch compact boundaries
→ rebuild runtime state
→ rebuild permissions from current config only
→ resume
```

### ST-C. Fork

Fork 不能简单 copy 全目录：

- 新 session_id；
- 明确 parent/fork point；
- 不继承 session grants；
- content refs 可 copy-on-read 或引用只读源；
- worktree/file checkpoint 关系明确。

### ST-D. Rewind

区分：

- conversation rewind；
- file rewind；
- branch/fork。

避免 UI 一个“rewind”按钮隐式混合三种语义。

### ST-E. Crash consistency

写入策略要考虑：

```text
tool side effect committed
but transcript write not committed
```

可加入 tool-start/tool-complete event，resume 时检测 orphan/incomplete tool event，禁止盲目重放有副作用工具。

## 单元测试方向

- malformed/truncated 最后一行；
- concurrent append；
- kill process between tool start/complete；
- large content ref missing/corrupted；
- transcript schema migration；
- resume 不恢复 permission；
- fork parent chain 正确；
- compact boundary chain patch；
- retention cleanup 不删 active session；
- Windows file lock 与 POSIX lock parity。

---

# 4.6 Context & Memory

当前五层 pipeline 已经与论文同构，因此重点不是“补五层”，而是让每层成为可验证合同。

## 目标流水线

```text
Raw messages
  ↓ L1 Tool Result Budget
  ↓ L2 Snip
  ↓ L3 Microcompact (feature/profile gated)
  ↓ L4 Context Collapse projection
  ↓ L5 Auto-compact LLM summary
  ↓
Model context
```

## 优化方向

### CT-A. ContextTrace

每轮生成：

```json
{
  "tokens_before": 150000,
  "layers": [
    {"name":"tool_result_budget","saved":12000,"reason":"oversize"},
    {"name":"snip","saved":9000,"reason":"history_depth"}
  ],
  "tokens_after":129000,
  "autocompact":false,
  "cache_prefix_hash":"..."
}
```

### CT-B. Semantic preservation suite

固定 20–50 个长上下文 corpus，每个都包含：

- 当前任务目标；
- “不要修改 X”；
- 文件路径；
- 已运行失败测试；
- 用户偏好/项目指令；
- 子 agent summary；
- 大 tool output。

压缩后自动检查这些关键事实是否仍存在或可恢复。

### CT-C. Compaction source-of-truth

必须区分：

```text
Durable transcript = full history
Model view          = compressed projection
```

不可因为 context collapse/snip 就破坏 resume/audit 所需历史。

### CT-D. Memory provenance

memory entry 最少记录：

- source file/session；
- created_at；
- last_used；
- scope；
- confidence/curation state；
- invalidation key。

### CT-E. Cache regression gate

DeepSeek prefix cache 已经是项目差异化优势，必须有：

- stable prefix hash；
- request-scope size budget；
- mid-session memory change 测试；
- tool schema order 稳定测试。

## 单元测试方向

- 五层执行顺序；
- earlier layer 达到阈值时 early-exit；
- layer failure 不导致整轮崩溃；
- auto-compact 真实替换 working messages（已有历史 bug 应固定 regression）；
- compact 后 read-file/plan attachment restored；
- context collapse 不改 durable transcript；
- cache boundary marker 不泄漏到非 Anthropic provider；
- dynamic memory 变更只影响预期 request scope；
- compact summary 不把旧 system reminder 当新用户命令。

---

# 4.7 Execution Environment

这是下一阶段最值得投入的组件。

## 目标结构

```text
Tool
 ↓
PermissionDecision
 ↓
ExecutionRequest
 ↓
ExecutionBoundary
   ├─ WorkspaceGuard
   ├─ SandboxBackend
   ├─ EnvPolicy
   ├─ NetworkPolicy
   ├─ ProcessPolicy
   └─ Audit
 ↓
OS / Remote / Worktree
```

## 优化方向

### EX-A. WorkspaceGuard 下沉

即便 Permission 已检查路径，FileRead/FileWrite/Edit/Bash backend 在真正 `open()/write()/spawn()` 前仍需检查 canonical target。

目的：防止任何漏接 permission gate 的路径直接越权。

### EX-B. SandboxBackend 抽象

接口示例：

```python
class SandboxBackend(Protocol):
    def prepare(request, policy) -> SandboxInvocation: ...
    def run(invocation) -> ExecutionResult: ...
```

后端可以按平台逐步实现：

- `NoSandboxBackend`：显式标记未隔离；
- Linux：可选 bwrap/container/nsjail 类隔离；
- macOS：选择当前可维护的 sandbox/container 后端；
- Windows：Job Object/process-tree + 可选 AppContainer/容器策略；
- remote：远端 runtime 自己提供 isolation contract。

**关键不是一次性实现所有平台最强 sandbox，而是先建立“Permission 与 Sandbox 独立”的接口层。**

### EX-C. Env/Secret Policy

执行外部 tool/MCP/subprocess 时使用 allowlist/denylist：

- API keys 默认不注入不需要的 child；
- provider secret 与 tool secret 分域；
- debug log 不输出 secret；
- MCP stdio server 环境最小化。

### EX-D. Network Policy

至少分：

```text
none
loopback
allowlist
full
```

并作为 Execution policy，而不是仅工具 prompt 描述。

### EX-E. Process lifecycle

统一：

- timeout；
- abort；
- process tree kill；
- background ownership；
- orphan recovery；
- stdout/stderr budget。

现有 `shell_platform.py` 已打好跨平台基础，下一步是在其上建立更高层的 ExecutionBoundary。

## 单元/安全测试方向

### Filesystem escape

- symlink out of root；
- junction；
- rename race；
- `../`；
- Windows drive/UNC；
- shell redirect；
- temp-file swap。

### Environment

- child 看不到未授权 API key；
- MCP child env 最小；
- debug output secret redaction。

### Network

- allowlist 外 host denied；
- DNS/redirect 后仍执行目标校验；
- loopback policy。

### Process

- child spawns grandchild；abort 后全部死亡；
- timeout race；
- Windows taskkill 与 POSIX killpg parity。

### Worktree/Remote

- cwd 不漂移；
- permission root 与 worktree root 一致；
- remote result schema 与 local 一致。

---

# 5. 跨模块关键调用链优化

七组件不是独立孤岛。建议固定三条“黄金调用链”做 E2E contract。

## 5.1 读文件链

```text
User prompt
→ Interface
→ Query Loop
→ Read tool schema
→ Permission
→ WorkspaceGuard
→ Execution FS read
→ ToolResult
→ Transcript
→ Context
→ Model next turn
```

**验收**：对 root 内文件成功；root 外、symlink escape、Windows case trick 均阻断；结果可持久化和 compact。

## 5.2 Bash 修改链

```text
Model Bash tool_use
→ Tool metadata says side-effecting/exclusive
→ Permission ask/allow
→ Sandbox/Workspace/Env/Network policy
→ spawn process tree
→ stream progress
→ normalize result
→ PostTool hook
→ JSONL append
→ next model turn
```

**验收**：并发 scheduler 不把 Bash 当 read-only；abort 能杀整棵进程树；大输出按 budget 管理。

## 5.3 Resume + Compact + Subagent 链

```text
Long session
→ context pressure
→ five-layer compact
→ subagent spawn
→ sidechain transcript
→ summary-only return
→ session exit
→ resume
→ rebuild context
→ permission grants NOT restored
→ continue task
```

这是检验“真的复刻 operational harness”最有价值的一条综合链。

---

# 6. 单元测试、组件测试、集成测试、E2E 测试设计

## 6.1 测试金字塔

| 层 | 比例建议 | 主要对象 |
|---|---:|---|
| Unit | 55% | matcher、transition、budget、schema、path、compaction layer |
| Component | 20% | PermissionPipeline、ToolExecutor、SessionStorage、ContextPipeline |
| Integration | 15% | FakeProvider + tools + filesystem + transcript |
| E2E | 7% | CLI/TUI/Desktop/headless 完整任务 |
| Security/Fault | 3% | escape、kill、network、corruption、prompt injection |

## 6.2 Unit 测试原则

每个模块必须尽量将纯逻辑和 I/O 分离：

```text
Pure core → fast deterministic tests
Adapter   → mocked/temporary filesystem tests
E2E       → few but high-value flows
```

## 6.3 Component tests

### PermissionPipeline

输入一个标准 `ToolRequest`，输出一个结构化 `PermissionDecision`，不真正执行工具。

### ToolExecutor

输入已授权 `ExecutionRequest`，验证 concurrency/order/result contract。

### ContextPipeline

Fake summarizer + 固定 token estimator，验证五层顺序和保真 invariant。

### SessionStorage

tempdir 内验证 append/read/migration/corruption/recovery。

## 6.4 Integration tests

建议增加 `tests/integration/seven_components/`：

```text
test_read_flow.py
test_write_flow.py
test_bash_flow.py
test_permission_denied_reroute.py
test_compact_resume.py
test_subagent_sidechain.py
test_abort_mid_tool.py
test_cost_budget.py
```

FakeProvider 通过脚本化响应精确控制 tool_use 序列。

## 6.5 Security tests

单独使用 `tests/security/`，禁止混在普通 unit 测试里。任何 failure 都直接阻断 release。

重点：

- workspace path traversal；
- symlink/junction；
- shell quoting/redirect；
- MCP tool poisoning/namespace；
- pre-trust hooks；
- secret leak；
- resume stale permission；
- sandbox/network escape。

## 6.6 Fault injection

至少模拟：

- provider 429/500/529；
- stream 中断；
- tool subprocess hang；
- disk full；
- transcript 最后一行截断；
- content ref 丢失；
- permission UI 超时；
- scheduler thread crash；
- MCP disconnect。

---

# 7. 详细分阶段开发计划

## Phase A — Baseline & P0（预计 3–5 个 PR）

### A1. Freeze baseline

产出 scorecard、known failures manifest。

### A2. Workspace boundary

- 修 read/write E2E；
- 增 property tests；
- backend second-check。

### A3. Advisor metadata

- 修 descriptor；
- parity/smoke green。

### A4. TUI failures

按五个独立 root cause 修复，不一次大杂烩。

**Exit Gate A**：Python parity + TUI baseline 全绿；workspace escape 0。

---

## Phase B — Loop Governance（预计 3–4 个 PR）

### B1. BudgetGuard

统一 max_turns/max_cost。

### B2. Scheduler decouple

消除 AskUserQuestion stall。

### B3. Query refactor I

提取 `model_call.py`、`terminal.py`、`budget.py`。

### B4. Query refactor II

提取 `tool_round.py`、`recovery.py`。

**Exit Gate B**：所有 Query stop/retry/budget 路径均有 deterministic tests。

---

## Phase C — Permission × Execution（预计 4–6 个 PR）

### C1. Permission profile

引入 parity/full-access/managed 配置模型。

### C2. Pre-trust gate

hook/MCP/project config 不在信任前自动执行。

### C3. ExecutionBoundary

WorkspaceGuard/EnvPolicy/ProcessPolicy 接口。

### C4. SandboxBackend interface

先落 no-sandbox + platform capability detection，再逐平台增强。

### C5. Network/secret policy

形成最小权限 execution profile。

**Exit Gate C**：Permission 失误的故障注入场景下，Execution boundary 仍可阻断关键 escape。

---

## Phase D — State/Persistence（预计 3–5 个 PR）

### D1. Transcript schema version

### D2. Resume contract

### D3. Fork/rewind contract

### D4. Sidechain contract

### D5. Crash-consistency tests

**Exit Gate D**：长 session 在 compact/agent/abort/crash 后可安全 resume；权限不被错误继承。

---

## Phase E — Context/Memory（预计 3–4 个 PR）

### E1. ContextTrace

### E2. Semantic corpus

### E3. Cache regression gate

### E4. Memory provenance/invalidation

**Exit Gate E**：context token savings 和 semantic retention 同时有指标。

---

## Phase F — Surface & Release Parity（预计 3–5 个 PR）

### F1. RuntimeEvent schema

### F2. CLI/headless contract

### F3. TUI/Desktop/VSCode contract

### F4. Seven-component report generator

### F5. Release gate

**Exit Gate F**：同一 scripted agent turn 在所有 surface 的 canonical event/permission/terminal semantics 一致。

---

# 8. PR 组织规范

每个 PR 最好只解决一个 contract。

## PR 模板

```markdown
## Seven-component scope
- [ ] Interfaces
- [ ] Agent Loop
- [ ] Permission
- [ ] Tools
- [ ] State/Persistence
- [ ] Context/Memory
- [ ] Execution Environment

## Invariant / Contract changed
...

## Claude Code parity impact
- Same behavior / Deliberate divergence / Unknown

## Tests added
...

## Failure injection
...

## Backward compatibility
...
```

## 禁止事项

1. 同一 PR 同时“重构 query.py + 改 permission + 改 context summary prompt”；
2. 只补 mock 单测，不补真实 E2E 安全测试；
3. 修安全 bug 时把 failing test 删除/放宽；
4. 文档仍写“未实现”但代码已经实现数月；
5. 为追求源码文件形似，删除 ClawCodex 合理的 Python/多-provider 优势。

---

# 9. Definition of Done（逐模块）

## Interfaces DoD

- canonical RuntimeEvent contract；
- 多 surface contract tests；
- interrupt/permission/resume 一致；
- UI tests 0 known failure。

## Agent Loop DoD

- 单主循环；
- 预算全入口生效；
- stop/retry/abort terminal reason 完整；
- scheduler 不受长 turn 阻塞。

## Permission DoD

- deny-first invariant；
- workspace escape 0；
- parity profile 默认 ask；
- pre-trust gate；
- audit reason 可追踪。

## Tools DoD

- ToolDescriptor 单一真相源；
- read/write concurrency 分类正确；
- result schema 一致；
- MCP 与 builtin 共用 permission/executor。

## State DoD

- schema version；
- resume/fork/rewind 行为固定；
- session permission 不错误恢复；
- crash-consistent transcript。

## Context DoD

- 五层顺序固定；
- trace 可见；
- semantic corpus；
- durable history 不因 projection 丢失；
- cache regression gate。

## Execution DoD

- WorkspaceGuard backend second-check；
- SandboxBackend interface；
- env/network/process policy；
- cross-platform process cleanup；
- security fault tests green。

---

# 10. 建议的目录重构（渐进式）

不建议一次性搬全仓库，只对核心边界渐进收口：

```text
src/
  entrypoints/              # surface adapters
  runtime/
    events.py               # RuntimeEvent schemas
    client.py               # surface-facing runtime API
  query/
    query.py                # thin façade
    loop.py
    state.py
    terminal.py
    budget.py
    model_call.py
    tool_round.py
    recovery.py
  permissions/
    ... existing ...
    profiles.py
    decision.py
    audit.py
  tool_system/
    descriptor.py
    registry.py
    executor.py
    ... existing tools ...
  execution/
    request.py
    boundary.py
    workspace_guard.py
    sandbox.py
    env_policy.py
    network_policy.py
    process_policy.py
    backends/
      local.py
      worktree.py
      remote.py
  context_system/
    ... existing ...
    trace.py
    invariants.py
  services/
    compact/
    session_storage.py
  persistence/
    events.py
    migration.py
    recovery.py
```

注意：目录重构不是第一优先级；必须在 P0 修复和测试 baseline 后执行。

---

# 11. 指标与持续诊断

## 11.1 每次 CI 自动输出 Seven-Component Scorecard

示例：

```yaml
commit: abc123
interfaces:
  contract_tests: 142/142
agent_loop:
  stop_paths: 12/12
  budget_paths: 8/8
permission:
  security_tests: 356/356
  workspace_escape: 0
  parity_profile: pass
tools:
  descriptor_parity: 54/54
state:
  recovery_scenarios: 18/18
context:
  semantic_invariants: 40/40
  median_tokens_saved: 31.4%
execution:
  escape_tests: 120/120
  orphan_processes: 0
```

## 11.2 性能指标

不要只追踪 wall clock；至少记录：

- model calls/turn；
- tool calls/turn；
- retries；
- prompt cache hit/miss；
- context tokens before/after；
- compact time；
- permission prompt count；
- tool concurrency；
- session write latency；
- orphan process count。

## 11.3 质量指标

- Terminal-Bench/SWE-like task pass rate；
- long-horizon completion；
- duplicate/revisit files；
- compact 后约束保持率；
- resume 后任务连续性；
- permission deny 后是否能改走安全路径。

---

# 12. 风险与回滚策略

## 12.1 最大风险：为了“高保真”破坏当前已经有效的增强

不应删除：

- 多 provider；
- DeepSeek prefix cache；
- `/eco`；
- Windows Git Bash execution layer；
- Desktop/TUI/VSCode surfaces。

解决方式：引入 **Parity Profile**，把 Claude Code 语义作为可选择/可测试的行为 profile。

## 12.2 Query refactor 风险

先做 characterization tests，再移动代码。每次只提取一个责任，严格保持 event transcript 与 API payload fixtures 不变。

## 12.3 Sandbox 风险

跨平台 sandbox 很容易造成可用性退化。先定义接口与 capability detection，再逐平台启用；不能在没有 fallback/diagnostic 的情况下默认强制。

## 12.4 Persistence schema 风险

采用 versioned migration；旧 session 必须只读可打开，失败时不得直接修改原 transcript。

---

# 13. 最终优化优先级结论

如果只允许投入有限开发资源，建议严格按下面顺序：

```text
1. Workspace boundary P0
2. Advisor metadata + TUI red baseline
3. max_turns / max_cost runtime enforcement
4. Permission parity profile + pre-trust gate
5. ExecutionBoundary + SandboxBackend
6. Query职责拆分
7. Persistence resume/fork/rewind contract
8. Context trace + semantic corpus
9. Cross-surface event contract
10. Continuous seven-component parity scorecard
```

这套顺序的核心是：

> **从“功能数量”转向“系统不变量”。**

Claude Code 论文之所以把一个看起来很简单的 tool-use loop 分解为七组件，就是因为生产 Agent 的主要难度并不在模型决定调用哪个函数，而在：权限是否真的拦住、执行是否隔离、状态是否可恢复、上下文是否在长任务中保持一致、所有入口是否走同一条核心路径。

当前 clauderuntime 已经具备大部分“Agent Operating System”的主体。下一阶段要做的不是继续证明“它能调用更多工具”，而是证明：

```text
它在长时间、多工具、多入口、异常、恢复和不可信输入下，
仍然遵守同一组安全、状态、上下文和执行契约。
```

做到这一点，项目才真正从“高完成度复刻”进入“高保真、可验证的生产 runtime”。

---

# 14. 参考证据

## Claude Code 论文基线

- *Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems*, arXiv:2604.14228v2
- Figure 1：七组件高层结构
- Figure 3：五层 subsystem
- Sections 4–9：Loop、Permission、Extensibility、Context、Subagent、Persistence

## clauderuntime 当前仓库

- https://github.com/Nuos/clauderuntime
- https://github.com/Nuos/clauderuntime/tree/main/src/entrypoints
- https://github.com/Nuos/clauderuntime/tree/main/src/query
- https://github.com/Nuos/clauderuntime/tree/main/src/permissions
- https://github.com/Nuos/clauderuntime/tree/main/src/tool_system
- https://github.com/Nuos/clauderuntime/tree/main/src/context_system
- https://github.com/Nuos/clauderuntime/blob/main/src/services/compact/pipeline.py
- https://github.com/Nuos/clauderuntime/blob/main/src/services/session_storage.py
- https://github.com/Nuos/clauderuntime/blob/main/src/utils/shell_platform.py
- https://github.com/Nuos/clauderuntime/tree/main/tests/parity
- https://github.com/Nuos/clauderuntime/blob/main/TODOS.md
- https://github.com/Nuos/clauderuntime/blob/main/README.md
