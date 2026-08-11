# ClaudeRuntime → Claude Code 高保真复刻开发规范与行动圣经

> **文档代号**：CCRP-BIBLE v1.0  
> **适用项目**：`Nuos/clauderuntime` / ClawCodex  
> **初始项目基线 B0**：`main @ 241d704480c0e4aa1bfb97c607a5e2e13e871e46`（2026-08-10）  
> **参考目标基线 R0**：*Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems* 对 Claude Code v2.1.88 的分析，以及对应参考源码快照/Source Map 资料  
> **上游基线文档**：`clauderuntime-current-diagnostic-2026-08-10.md`  
> **第一阶段开发计划**：`clauderuntime-optimization-development-plan-2026-08-10.md`  
> **文档定位**：本文件不是某一个阶段的任务清单，而是整个“ClaudeRuntime 高保真复刻工程”的**总开发规范、决策规则、行动流程、验收制度与长期治理圣经**。

---

# 0. 一句话总纲

ClaudeRuntime 的目标不是“做一个像 Claude Code 的 Agent”，也不是“把 Claude Code 的 TypeScript 逐文件翻译成 Python”，而是：

> **以参考源码与论文为事实基线，在不破坏 Python 合理工程优势的前提下，让 ClaudeRuntime 在框架、结构、核心功能组件、关键调用关系、状态转换、运行机制、辅助循环机制、安全边界、持久化语义和跨入口行为上，尽可能达到可映射、可验证、可持续追踪的高保真一致。**

最终任何“已经复刻”“基本一致”“完成度 95%”的说法，都必须能够落到：

```text
源码映射证据
  + 调用链证据
  + 状态/行为契约
  + 自动化测试
  + 差异登记
  + 可复现运行结果
```

没有上述证据链，不得把“看起来类似”“目录名称相似”“功能能跑”视为 parity 完成。

---

# 1. 本圣经与现有两份文档的关系

## 1.1 三层文档体系

项目从现在开始统一采用三层文档关系：

```text
CCRP-BIBLE（本文件）
│
├── B0 / Baseline
│   └── clauderuntime-current-diagnostic-2026-08-10.md
│
├── P1 / Phase-I Plan
│   └── clauderuntime-optimization-development-plan-2026-08-10.md
│
└── 后续专项计划
    ├── source-structure-parity
    ├── runtime-mechanism-parity
    ├── auxiliary-mechanism-parity
    └── continuous-parity
```

## 1.2 `current-diagnostic` 的角色

`current-diagnostic-2026-08-10.md` 是 **B0 当前状态快照**，回答：

- 当前 ClaudeRuntime 已经有什么；
- 七组件当前成熟度如何；
- 当前明确 P0/P1/P2/P3 缺陷是什么；
- 哪些结论已经确认，哪些只是推断；
- 当前代码主干离参考系统的大体距离在哪里。

它是“**我们现在在哪里**”的文档，不是长期总路线图。

## 1.3 `optimization-development-plan` 的角色

`optimization-development-plan-2026-08-10.md` 是 **Phase I：Stabilization & Behavioral Parity** 的第一阶段计划，回答：

- 如何消灭当前已知红线；
- 如何收口七组件行为；
- 如何修 Workspace Boundary、Permission × Execution、预算、恢复、Context、Surface Contract；
- 如何建立 0-red baseline 与系统不变量。

第一阶段主体**不推翻、不重写**。本圣经在其上增加更严格的 Source / Architecture / Runtime / Auxiliary parity 规范。

## 1.4 本文件的角色

本文件回答：

> “今后每一次开发、重构、复刻、测试、评审、发布，到底应该依据什么原则做？如何判断它是在逼近 Claude Code，而不是在制造另一个相似 Agent？”

---

# 2. 最终目标：五层高保真 + 三类语义一致

整个项目的目标拆成五层复刻对象。

## 2.1 L1 — Framework Parity：框架一致

需要回答：

- 系统的主要层次是否与参考系统相同；
- 各入口是否汇聚到共同运行核心；
- Agent Loop、Permission、Tools、State、Execution 是否保持参考系统的主要边界；
- 是否出现 Claude Code 中不存在的“第二主循环”“第二套权限系统”“Surface 自己执行工具”等结构漂移。

## 2.2 L2 — Structure Parity：结构一致

需要建立：

- reference package/module → Python module 的映射；
- reference symbol → Python symbol 的映射；
- reference subsystem → Python subsystem 的映射；
- reference call edge → Python call edge 的映射。

结构一致不要求路径和语言逐字符一致，但要求关键责任边界可解释地对应。

## 2.3 L3 — Core Component Parity：核心组件一致

七个核心组件必须逐一具备：

- 明确入口；
- 明确状态；
- 明确输出；
- 明确调用边界；
- 明确异常语义；
- 明确测试；
- 明确与参考实现的差异登记。

## 2.4 L4 — Runtime Mechanism Parity：运行机制一致

不能只看“模块存在”。必须证明主流程顺序、状态转换和结果反馈尽可能一致，例如：

```text
User Prompt
→ Interface normalization
→ Query/Agent Loop
→ Model stream
→ Tool Use
→ Permission decision
→ Tool execution
→ ToolResult normalization
→ append state
→ next model round
→ terminal
```

运行顺序、权限位置、状态落盘时机、Abort 行为、Retry/Fallback 位置，都属于 parity 对象。

## 2.5 L5 — Auxiliary Mechanism Parity：辅助机制一致

高保真复刻不能只研究主 while-loop。以下机制必须独立审计：

- Retry / backoff / fallback；
- Tool execution scheduling；
- Permission escalation；
- Hooks lifecycle；
- Context compaction；
- Subagent lifecycle；
- Background task lifecycle；
- Scheduler/Cron；
- MCP lifecycle；
- Resume/Fork/Rewind；
- Worktree / remote execution；
- Streaming / progress / interrupt；
- Session persistence / recovery；
- Long-output result budgeting。

这些机制仍归属于七组件，不另造“第八组件”，但必须有独立映射、独立 Runtime Path 和独立验收。

## 2.6 三类必须同时满足的语义一致

五层结构最终必须通过三类语义来验证：

### A. Behavioral Parity

同一输入、同一可控环境、同一工具结果下，关键行为和状态转换是否等价。

### B. Safety Parity

Allow / Ask / Deny、workspace containment、trust boundary、sandbox/isolation、权限恢复规则等是否保持相同安全不变量。

### C. State Parity

Session、Transcript、Compaction、Resume、Fork、Subagent sidechain、Terminal reason 是否保持可对应的状态语义。

---

# 3. 事实来源与证据优先级

## 3.1 Source of Truth 层级

任何 parity 判断统一使用以下证据优先级：

```text
S0 参考源码中的实际实现 / source map 恢复内容
 >
S1 Dive into Claude Code 论文中的源码级分析
 >
S2 可执行 reference 行为、官方可观察行为
 >
S3 ClaudeRuntime 当前源码与自动测试
 >
S4 仓库 README / FEATURE_LIST / TODOS / CHANGELOG
 >
S5 推断、经验、设计建议
```

当高优先级与低优先级冲突时，以高优先级为准。

## 3.2 禁止把文档声明当作实现事实

例如：

```text
README 写“已支持”      ≠ parity 已完成
Feature List 标 ✅      ≠ 调用链已验证
存在 permissions.py    ≠ 权限系统已闭环
存在 /compact          ≠ compaction 语义一致
存在 resume            ≠ permission/trust 恢复语义一致
```

必须继续追到源码、调用关系和测试。

## 3.3 每一条重要结论必须标证据等级

推荐使用：

- `CONFIRMED`：直接源码/测试证据；
- `BEHAVIOR_CONFIRMED`：可运行行为已验证；
- `INFERRED`：从调用关系合理推断；
- `UNVERIFIED`：尚未验证；
- `UNKNOWN`：参考系统本身证据不足。

禁止把 `INFERRED` 自动升级成 `CONFIRMED`。

---

# 4. 统一七组件口径：Reference-7 优先

## 4.1 论文/参考系统的 Canonical Reference-7

后续所有正式“Source Parity”报告，必须使用参考论文的顶层七组件：

1. **User**
2. **Interfaces**
3. **Agent Loop**
4. **Permission System**
5. **Tools**
6. **State & Persistence**
7. **Execution Environment**

## 4.2 关于现有 Phase-I 文档中的 Context & Memory

现有 `current-diagnostic` / `optimization-development-plan` 为工程治理便利，使用了：

- `User / Interfaces` 合并；
- `Context & Memory` 单列。

该口径可继续用于 Phase-I 工程 Scorecard，但不得误认为论文顶层七组件的原始定义。

从本圣经开始，统一采用双视图：

### Reference View

严格使用 Reference-7，用于：

- source map；
- architecture parity；
- reference runtime path；
- 最终复刻完成度。

### Engineering View

允许额外把 Context/Memory 做独立工程轴，用于：

- compaction；
- prompt assembly；
- memory；
- cache；
- context trace。

但其最终归属必须映射回 Agent Loop / State & Persistence 等参考组件。

---

# 5. 二维审计模型：七组件 × 辅助机制

只用“七组件评分”不足以覆盖系统复杂度。以后同时维护：

```text
纵轴：Reference-7
横轴：Auxiliary Mechanisms
```

示例：

| 辅助机制 | 主要归属 | 次级归属 | 必须独立验收 |
|---|---|---|---|
| Retry / Backoff / Fallback | Agent Loop | Interfaces | 是 |
| Tool Round Scheduler | Agent Loop | Tools | 是 |
| Permission Escalation | Permission | Interfaces | 是 |
| Pre/Post Tool Hooks | Tools | Permission / Loop | 是 |
| Auto Compact | Agent Loop | State | 是 |
| Session Resume | State | Loop / Permission | 是 |
| Fork / Rewind | State | Tools | 是 |
| Subagent Loop | Agent Loop | State / Tools | 是 |
| Background Agent | Agent Loop | Execution / State | 是 |
| MCP Lifecycle | Tools | Execution | 是 |
| Scheduler/Cron | Agent Loop | State | 是 |
| Worktree Lifecycle | Execution | State | 是 |
| Stream Event Loop | Interfaces | Agent Loop | 是 |
| Abort / Interrupt | Interfaces | Loop / Execution | 是 |
| Long-output Budget | Tools | State / Loop | 是 |

原则：

> **任何一个横向机制，只要跨两个以上组件，就必须有端到端 Runtime Path，而不能只靠若干孤立 unit tests 证明。**

---

# 6. Parity 状态分类：所有差异必须被命名

每个模块、symbol、runtime path、行为契约都必须处于以下一种状态：

| 状态 | 定义 |
|---|---|
| `EXACT` | 结构、行为和关键语义基本一一对应 |
| `SEMANTIC_EQUIVALENT` | 语言/实现不同，但行为、状态和边界等价 |
| `PYTHON_ADAPTATION` | 为 Python 运行时必要的实现适配，reference 语义保持 |
| `PRODUCT_EXTENSION` | ClaudeRuntime 自有增强，不属于参考复刻 |
| `INTENTIONAL_DIVERGENCE` | 明确知道与参考不同，并有理由保留 |
| `PARTIAL` | 有主体实现，但仍缺关键分支/边界/生命周期 |
| `MISSING` | 参考存在而项目未实现 |
| `UNKNOWN` | 参考证据或当前代码证据不足 |
| `DEPRECATED_COMPAT` | 仅为兼容历史路径，目标是最终收口 |

## 6.1 禁止使用模糊状态

禁止：

- “差不多”；
- “应该有”；
- “基本一样”；
- “可能不影响”；
- “以后再看”。

必须转换为上述枚举之一，并给出证据和下一步。

## 6.2 Product Extension 不得污染 Parity Core

例如：

- 多 provider；
- DeepSeek prefix cache；
- `/eco`；
- Windows Git Bash 适配；
- 自有 Desktop 扩展。

这些可以保留，但必须满足：

```text
Reference parity profile
      可独立运行
      可独立测试
      不被 extension 改写关键语义
```

---

# 7. 必须建立的八类主资产

后续工程不允许只靠散落 Markdown 维护 parity。必须建立机器可读资产。

## 7.1 `reference-package-map.yaml`

记录 reference package/module → Python package/module。

## 7.2 `reference-symbol-map.yaml`

记录关键函数、类、常量、状态结构的对应关系。

## 7.3 `reference-callgraph-map.yaml`

记录关键 call edge：

```text
reference A() → B()
python    A'() → B'()
```

## 7.4 `reference-runtime-path-map.yaml`

记录端到端主流程。

## 7.5 `reference-aux-loop-map.yaml`

记录 Retry、Compaction、Hook、Subagent、MCP 等辅助机制。

## 7.6 `known-divergences.yaml`

所有 `PYTHON_ADAPTATION / PRODUCT_EXTENSION / INTENTIONAL_DIVERGENCE` 必须集中登记。

## 7.7 `unmapped-reference-symbols.yaml`

参考系统中尚未映射的关键 symbol 不得“隐形消失”。

## 7.8 `parity-scorecard.json|yaml`

CI 每次生成，作为长期趋势数据。

推荐目录：

```text
docs/parity/
  baseline/
  source-map/
    reference-package-map.yaml
    reference-symbol-map.yaml
    reference-callgraph-map.yaml
    unmapped-reference-symbols.yaml
  runtime/
    reference-runtime-path-map.yaml
    reference-aux-loop-map.yaml
  divergences/
    known-divergences.yaml
  scorecards/
    latest.yaml
    history/
  reports/
```

---

# 8. 开发总 Workflow：从“证据”开始，而不是从“写代码”开始

所有高保真复刻工作统一执行以下 12 阶段。

```text
G0  Target Freeze
 ↓
G1  Reference Evidence Extraction
 ↓
G2  Current Implementation Trace
 ↓
G3  Gap Classification
 ↓
G4  Contract / Invariant Definition
 ↓
G5  Characterization Tests
 ↓
G6  Minimal Implementation / Refactor
 ↓
G7  Unit + Component Verification
 ↓
G8  Runtime Path / E2E Verification
 ↓
G9  Fault / Safety Verification
 ↓
G10 Mapping & Divergence Update
 ↓
G11 Release / Baseline Update
```

## G0 — Target Freeze

每个任务先固定：

- Reference version/commit/source snapshot；
- ClaudeRuntime base commit；
- 本次涉及的组件；
- 本次涉及的 runtime paths；
- 不允许顺手改变的边界。

## G1 — Reference Evidence Extraction

必须回答：

- reference 代码在哪；
- 入口 symbol 是什么；
- 调用了谁；
- 状态对象是什么；
- 退出条件是什么；
- 异常如何传播；
- 哪些 hook/permission/context 边界介入。

产出必须写入 source map，而不是只存在开发者脑中。

## G2 — Current Implementation Trace

追踪 ClaudeRuntime 当前同类路径，禁止仅按文件名类比。

输出：

```text
reference trace
vs
clauderuntime trace
```

## G3 — Gap Classification

Gap 必须分类：

- missing structure；
- wrong call order；
- wrong state transition；
- missing safety boundary；
- missing recovery path；
- missing surface behavior；
- Python adaptation；
- intentional product divergence；
- documentation drift；
- test coverage gap。

## G4 — Contract / Invariant Definition

先定义行为，再写代码。

例如：

```text
Invariant: permission deny always wins.
Invariant: resume must not restore stale session-scoped grants.
Invariant: write cannot escape canonical workspace root.
Invariant: tool_result belongs to exactly one tool_use.
Invariant: terminal reason is single and durable.
```

## G5 — Characterization Tests

重构现有代码前，先把当前可接受行为冻结。

没有 characterization test，不允许大拆 `query.py`、permission pipeline、session schema。

## G6 — Minimal Implementation / Refactor

原则：

> **一次 PR 尽量只闭合一个 parity gap，不同时“修 parity + 重写架构 + 加产品功能”。**

## G7 — Unit + Component Verification

验证局部行为。

## G8 — Runtime Path / E2E Verification

验证完整调用链。

## G9 — Fault / Safety Verification

故障与攻击边界必须单独测，尤其 Permission / Execution / Persistence。

## G10 — Mapping & Divergence Update

代码完成但 source map 未更新，任务视为未完成。

## G11 — Release / Baseline Update

只有通过 release gate 才能更新 Scorecard 和新 Baseline。

---

# 9. 三阶段总路线

## Phase I — Stabilization & Behavioral Parity

**直接执行现有 `optimization-development-plan-2026-08-10.md`。**

核心目标：

- 0-red baseline；
- Workspace Boundary P0；
- Agent Loop Budget / Lifecycle；
- Permission × Execution 双边界；
- Tool Contract；
- State Recovery；
- Context 可观测；
- Multi-Surface Contract。

完成条件不是“开发计划中的代码都写了”，而是 Phase-I DoD 全部通过。

## Phase II — Source / Architecture / Mechanism Parity

目标：把“高完成度 Agent”升级成“高保真可映射 runtime”。

工作主线：

```text
Package Map
→ Symbol Map
→ Call Graph
→ Runtime Path
→ Auxiliary Mechanism
→ Differential/Contract Tests
→ Divergence Registry
```

重点不再是功能数量，而是 reference 内部路径的系统覆盖。

## Phase III — Continuous Parity

当目标 reference 升级时：

```text
Reference N
 ↓ diff
Reference N+1
 ↓
Affected package/symbol/call-edge/runtime-path
 ↓
Generated parity backlog
 ↓
Implement + tests
 ↓
New baseline
```

最终项目必须具备“版本可追踪”能力，而不是每次重新人工做大审计。

---

# 10. Reference-7 逐组件开发规范

# 10.1 User

## 目标

User 在 reference 中不是 UI，而是：

- 提交 prompt；
- 提供 follow-up；
- 审批 permission；
- 回答 AskUserQuestion；
- interrupt/abort；
- review output。

## 必须保持的边界

User action 不得直接绕过 Interface/Runtime 修改内部 Loop 状态。

## 必须映射的行为

- prompt submit；
- permission response；
- question response；
- interrupt；
- session resume/fork selection。

## 测试方向

- 同一个 user action 在不同 surface 被标准化成同类 RuntimeEvent/RuntimeRequest；
- abort 时不会留下半个 permission/tool terminal；
- permission answer 必须绑定 request/tool_use identity。

## DoD

User 输入的所有关键动作都有统一 typed representation，并可跨 surface 重放。

---

# 10.2 Interfaces

## 目标结构

```text
Interactive CLI ─┐
Headless ─────────┤
TUI ──────────────┤
Desktop ──────────┤ → Runtime Adapter → Shared Core
IDE/VSCode ───────┤
Programmatic API ─┘
```

## 核心原则

Surface 只负责：

- input adaptation；
- rendering；
- user interaction；
- approval/question response；
- interrupt/cancel；
- session selection。

Surface **禁止拥有第二套**：

- Agent Loop；
- Permission policy；
- Tool dispatcher；
- Context pipeline；
- Session semantic interpreter。

## Runtime Event 最低要求

建议统一：

```text
request.start
model.delta.text
model.delta.thinking
model.complete
tool.start
tool.progress
tool.complete
permission.request
permission.resolved
question.request
question.resolved
compact.start
compact.complete
session.persisted
request.terminal
error
```

所有事件至少包含：

- `schema_version`
- `session_id`
- `request_id`
- `sequence`
- `timestamp`
- `source`
- `payload`

## 测试

建立 Surface Contract Fixtures：

同一 FakeProvider/FakeTool turn，比较 CLI/headless/server/TUI/Desktop 的 canonical event sequence。

## DoD

- 核心事件序列无 blocking mismatch；
- cancel/approval/resume 语义一致；
- Surface 不包含第二运行核心。

---

# 10.3 Agent Loop

## 目标

Agent Loop 必须是唯一主循环和唯一核心状态推进器。

## 参考语义必须覆盖

- model call；
- streaming；
- tool-use collection；
- tool execution scheduling；
- permission gate；
- tool-result feedback；
- stop condition；
- retry/fallback；
- token/output recovery；
- compaction/recovery；
- abort；
- terminal reason。

## 单主循环原则

禁止出现：

```text
CLI loop
TUI loop
agent_server loop
legacy compat loop
```

各自拥有不同 tool/permission semantics。

兼容层允许存在，但必须标 `DEPRECATED_COMPAT` 并有退出路线。

## Agent Loop 状态机

建议明确化：

```text
INIT
→ BUILD_CONTEXT
→ MODEL_STREAM
→ MODEL_COMPLETE
→ TOOL_PLAN
→ PERMISSION
→ TOOL_EXECUTE
→ TOOL_RESULTS
→ NEXT_ROUND
→ TERMINAL
```

异常分支：

```text
MODEL_ERROR
→ RETRY / FALLBACK / TERMINAL

CONTEXT_OVERFLOW
→ COMPACT / RECOVER / TERMINAL

ABORT
→ CANCEL_IN_FLIGHT
→ CLEANUP
→ TERMINAL
```

## 预算治理

`max_turns / max_cost / token_budget / retry_budget` 必须真正 enforce，而不是只定义 schema。

Budget 应覆盖：

- foreground model calls；
- retry；
- compact model calls；
- subagent model calls（按 reference policy）；
- tool side-effect rounds。

## Query 重构规则

若拆 `query.py`：

1. 先 characterisation；
2. 一次提取一个责任；
3. 不同时改变 event order；
4. 不同时改变 API payload；
5. 不同时改变 stop reason；
6. 不同时引入新规划框架。

## 测试

必须覆盖：

- no-tool terminal；
- single tool；
- multi tool；
- concurrent-safe read；
- serial write；
- permission ask；
- permission deny；
- model retry；
- fallback；
- prompt too long；
- compact；
- abort during model；
- abort during tool；
- max turns；
- max cost；
- stop hook intervention。

## DoD

所有 terminal paths 可枚举、可测试、可持久化；主循环只有一套权威实现。

---

# 10.4 Permission System

## 四个核心不变量

### P1 — Deny First

Deny 规则优先于 allow/ask。

### P2 — Unknown Risk Must Escalate

在 Claude parity profile 下，未知高风险动作必须 ask 或 deny，不得静默 allow。

### P3 — Workspace Containment

读写边界必须使用 canonical path，并抵抗：

- `..`；
- symlink；
- junction；
- case folding；
- Windows drive relative；
- UNC；
- path normalization differences。

### P4 — Permission ≠ Sandbox

Permission 决定“允不允许做”；Execution 决定“即使允许，能做到哪里”。

两者不得合并成一层。

## Permission Profile

建议至少：

```text
claude-parity     # reference-safe default
product-default   # ClawCodex 当前产品策略
managed           # 组织策略 ceiling
```

不得为了产品 Full Access 破坏 parity profile。

## 决策对象

每次 decision 建议持久化：

```text
request_id
tool_use_id
tool_name
normalized_target
mode
matched_rule
result: allow|ask|deny
reason
scope
source
```

## Pre-trust Gate

未信任 workspace 的：

- hooks；
- MCP config；
- project instructions；
- local extensions；

必须按 reference 证据建立加载边界。

## 测试

除 unit tests 外，必须有 property/fuzz：

- path escape；
- rule precedence；
- grant scope；
- case folding；
- symbolic links；
- resume trust reset。

## DoD

Workspace escape = 0；deny-first 恒成立；resume 不恢复过期 session-scoped trust。

---

# 10.5 Tools

## 核心原则

Tool 不是“Python 函数集合”。Tool 是统一 Contract：

```text
identity
schema
prompt/description
side-effect class
permission class
read-only
concurrency-safe
sandbox requirement
result schema
result budget
execution adapter
```

## ToolDescriptor 单一真相源

禁止以下信息分别散落：

- registry 说 read-only；
- scheduler 又维护一份；
- permission 再猜一次；
- UI 自己映射一次。

必须建立权威 descriptor。

## 调度语义

与 reference 对齐的基本方向：

```text
concurrency-safe reads → 可并发
write/shell/side-effect → 串行或按 reference 规则
```

任何 metadata 错误都可能改变调度和安全语义，因此视为 correctness bug。

## Tool Result Contract

结果必须区分：

- model-facing content；
- UI-facing display envelope；
- error status；
- recoverable full content reference；
- duration / metadata。

## MCP

MCP tool 不能绕过 builtin tool 的：

- permission；
- execution boundary；
- result normalization；
- audit；
- naming/collision policy。

## 测试

- descriptor parity；
- schema validation；
- concurrency stress；
- tool_use ↔ tool_result one-to-one；
- timeout/abort；
- result truncation recovery；
- MCP collision/malicious server。

## DoD

Scheduler、Permission、Executor、UI 使用同一 ToolDescriptor 事实源。

---

# 10.6 State & Persistence

## 目标

Session 持久化必须被视为 event/state protocol，而不是“把消息写 JSONL”。

## 必须映射的语义

- append-oriented transcript；
- metadata；
- compact boundary；
- resume；
- fork；
- rewind；
- subagent sidechain；
- terminal reason；
- large result externalization；
- permission/trust 不当恢复防护。

## Versioned Event Schema

建议：

```text
schema_version
event_id
session_id
parent_event_id
timestamp
type
payload
```

## Resume

必须定义：

- replay 哪些事件；
- 什么上下文重建；
- compact 如何恢复；
- 哪些 permission 不恢复；
- 哪些 transient state 清空；
- 哪些 provider/model state 保留。

## Fork

Fork 必须明确：

- fork point；
- parent relation；
- event identity；
- shared/detached file state；
- permission inheritance。

## Rewind

Rewind 文件历史与会话语义必须区分，不能把 generic state rewind 与 file checkpoint 混为一谈。

## Crash Consistency

必须测试：

- process kill；
- partial line；
- flush interruption；
- concurrent writer；
- disk full；
- externalized content missing。

## DoD

旧 session 可安全读；坏 session 不被静默修改；resume/fork/rewind 有固定 E2E contract。

---

# 10.7 Execution Environment

## 目标结构

```text
Authorized Action
     ↓
ExecutionRequest
     ↓
WorkspaceGuard
     ↓
Sandbox / Isolation Backend
     ↓
Env / Secret Policy
     ↓
Network Policy
     ↓
Process / FS / Remote Backend
```

## Defense in Depth

Permission 已批准，不代表 Execution backend 可以无限执行。

Workspace boundary 至少两处：

```text
Permission normalization/check
+
Execution backend second-check
```

## SandboxBackend

即便不同平台实现不同，也必须有统一抽象：

```text
LocalUnsandboxedBackend
LocalSandboxedBackend
WorktreeBackend
RemoteBackend
```

并明确 capability detection 和 fallback。

## Env / Secret

必须定义：

- 哪些 env 可传；
- 哪些 secret 自动剥离；
- 子进程继承边界；
- hook/MCP 的独立 env policy。

## Network

若 reference 存在独立网络限制语义，应映射到 execution policy，而不是只靠 Web 工具白名单。

## Process Lifecycle

必须统一：

- spawn；
- foreground/background；
- timeout；
- abort；
- process-tree cleanup；
- orphan detection；
- cross-platform semantics。

## DoD

即使 Permission 层漏判，Execution backend 对 workspace/secret/process 关键边界仍能 fail closed。

---

# 11. Context / Memory 专项规范（跨 Agent Loop + State）

Context 虽不是 Reference-7 顶层第八组件，但它是高保真复刻的核心工程轴，必须独立治理。

## 11.1 五层 Pipeline 不得只以“省 token”验收

当前项目已有五层压缩体系，必须继续保持固定顺序与可观察性。

每层至少记录：

```text
layer
trigger_reason
input_tokens
output_tokens
messages_before
messages_after
semantic_anchors_before
semantic_anchors_after
elapsed
```

## 11.2 Semantic Preservation

压缩后必须保留：

- 当前任务目标；
- 用户明确约束；
- 已读关键文件及必要状态；
- 未完成 TODO；
- 关键失败原因；
- 计划状态；
- 安全边界。

## 11.3 Durable Truth vs Projection

必须区分：

```text
Durable transcript      # 事实真值
Working context         # 当前模型视图
Compacted projection    # 压缩视图
Memory                   # 跨轮/跨会话知识
```

不得因为 projection 丢失就破坏 durable history。

## 11.4 Cache

Prompt cache 是性能优化，不得改变模型可见语义。

任何 cache optimization 必须具备：

- content conservation test；
- prefix stability test；
- ordering test；
- provider-specific regression test。

---

# 12. 辅助循环机制目录与验收规则

以下机制每一个都建立独立 `AUX-xx` 条目。

## AUX-01 Retry / Backoff / Fallback

必须定义：

- 哪些错误可 retry；
- 每类 retry 上限；
- Retry-After；
- overload 特殊通道；
- fallback model；
- abort 优先级；
- retry 是否计入预算。

## AUX-02 Tool Execution Loop

必须定义：

- 工具何时开始；
- streaming 中是否提前 dispatch；
- 并发分类；
- sibling failure/abort；
- result ordering。

## AUX-03 Permission Feedback Loop

必须定义：

- prefilter；
- rule eval；
- hook；
- ask；
- once/session/always grant；
- deny 回写模型的语义。

## AUX-04 Hook Lifecycle

建立 reference hook events map，并记录：

- 触发点；
- blocking/non-blocking；
- timeout；
- error policy；
- context injection；
- trust gate。

## AUX-05 Compaction Loop

必须验证：

- proactive/auto/reactive trigger；
- failure circuit breaker；
- compact 后上下文恢复；
- 继续 query 不重复 compact。

## AUX-06 Subagent Loop

必须验证：

- child context 构建；
- child tool set；
- permission；
- sidechain persistence；
- parent 只接收规定 summary/result；
- interrupt/cleanup。

## AUX-07 Background Task Loop

必须验证：

- background 生命周期；
- notification；
- worker isolation；
- long foreground wait 不阻塞 housekeeping。

## AUX-08 Scheduler / Cron

必须与 foreground turn 解耦，不能因为 AskUserQuestion/permission wait 长时间停摆。

## AUX-09 MCP Lifecycle

必须覆盖：

- config load；
- trust；
- connect；
- capability discovery；
- tool registration；
- permission；
- reconnect；
- disconnect；
- malicious server。

## AUX-10 Resume / Fork / Rewind

作为 State 主机制单独 E2E。

## AUX-11 Worktree / Remote

必须保证 cwd、git root、session identity、permission、execution boundary 一致。

## AUX-12 Surface Streaming / Interrupt

必须保证 event ordering、sequence、partial stream、cancel、terminal 一致。

---

# 13. 必须冻结的 Runtime Paths

每个 Runtime Path 都必须有 Reference Trace、Python Trace、Contract Tests。

## RP-01 Plain Answer

```text
User
→ Interface
→ Query
→ Model
→ Text stream
→ Assistant state
→ Terminal
→ Persist
```

## RP-02 File Read

```text
Prompt
→ Model ToolUse(Read)
→ Tool Registry
→ Permission
→ WorkspaceGuard
→ Read
→ ToolResult
→ Persist
→ Model next round
```

## RP-03 File Write/Edit

```text
ToolUse
→ schema
→ canonical path
→ permission
→ execution boundary
→ write/edit
→ file-history/update state
→ ToolResult
→ transcript
```

## RP-04 Bash

```text
ToolUse(Bash)
→ command parsing
→ permission
→ sandbox/execution backend
→ process spawn
→ stdout/stderr
→ timeout/abort
→ process cleanup
→ result normalize
```

## RP-05 Permission Ask

```text
Risk action
→ ask
→ surface approval event
→ user answer
→ scoped grant/deny
→ resume same tool_use
```

## RP-06 Auto Compact

```text
context pressure
→ trigger
→ compression pipeline
→ summary/projection
→ restore required attachments
→ continue same task
```

## RP-07 Resume

```text
session select
→ transcript replay
→ compact boundary reconstruction
→ transient-state reset
→ permission reset
→ context rebuild
→ continue
```

## RP-08 Subagent

```text
parent tool/action
→ child state
→ child query loop
→ child tools
→ child persistence
→ child result
→ parent context
```

## RP-09 MCP Tool

```text
MCP config
→ trust
→ connect
→ register tool
→ model tool_use
→ permission
→ MCP call
→ normalize result
→ model
```

## RP-10 Interrupt During Tool

```text
User Ctrl+C / cancel
→ surface event
→ AbortController
→ tool/process cancellation
→ cleanup
→ durable terminal state
```

## RP-11 Model Failure / Fallback

```text
model error
→ classify
→ retry budget
→ backoff
→ fallback or terminal
```

## RP-12 Worktree / Remote Session

```text
session context
→ execution target resolution
→ cwd/git/worktree mapping
→ tool action
→ persistence identity
```

新增关键机制时必须新增 RP，不得只加代码。

---

# 14. Source Map 开发方法

## 14.1 Package Mapping

每个参考模块记录：

```yaml
reference:
  path: src/query.ts
  version: 2.1.88
python:
  paths:
    - src/query/query.py
    - src/query/transitions.py
status: SEMANTIC_EQUIVALENT
confidence: CONFIRMED
notes:
  - Python split differs from TS packaging
```

## 14.2 Symbol Mapping

关键 symbol 包括：

- query entry；
- permission resolver；
- tool pool/registry；
- session writer；
- compaction trigger；
- hook dispatcher；
- execution/sandbox decision；
- subagent entry。

## 14.3 Call Edge Mapping

只映射“关键边界调用”，不要试图一开始覆盖每一个 helper。

优先级：

```text
P0 safety edges
P1 lifecycle edges
P1 state edges
P1 tool edges
P2 context edges
P2 surface edges
P3 utilities
```

## 14.4 Unmapped 不得隐形

任何 reference 关键 symbol 无 Python 对应时，必须进入 `unmapped-reference-symbols.yaml`。

---

# 15. 测试圣经

## 15.1 测试不是补充，而是 parity 证据的一部分

高保真复刻的每个完成项至少需要：

```text
Reference evidence
+ Characterization
+ Unit/Component
+ Runtime Path / E2E
```

安全/持久化类还必须有：

```text
Fault / adversarial tests
```

## 15.2 Unit Tests

适合纯函数和局部规则：

- path normalization；
- rule precedence；
- terminal transition；
- tool descriptor；
- token budget；
- retry classifier；
- event serialization。

## 15.3 Component Tests

单独测试：

- PermissionPipeline；
- ToolExecutor；
- ContextPipeline；
- SessionStorage；
- RuntimeEvent adapter；
- SandboxBackend。

## 15.4 Integration Tests

验证跨组件：

- Loop + Permission + Tool；
- Tool + Execution；
- State + Compact；
- Surface + Approval；
- MCP + Permission + Executor。

## 15.5 Parity Tests

Parity test 必须描述“对齐的 reference contract”，不能只叫 `test_xxx_parity`。

测试注释至少写：

```text
Reference behavior:
Reference evidence:
Expected Python behavior:
Allowed divergence:
```

## 15.6 Differential Tests

适合确定性部分：

- permission decision；
- tool schema；
- context ordering；
- transcript conversion；
- event sequence；
- path rules。

不要拿 LLM 随机文本做逐字 differential。

## 15.7 Fault Injection

至少覆盖：

- network drop；
- 429/5xx/529；
- stream break；
- disk full；
- process kill；
- permission timeout；
- corrupt transcript；
- MCP disconnect；
- sandbox unavailable。

## 15.8 Long-Horizon Tests

验证：

- 多轮 context；
- 多次 compact；
- resume 后继续任务；
- subagent 后主任务持续；
- background/scheduler 不饿死；
- cache optimization 不改变语义。

## 15.9 0-Red Rule

主干长期已知失败是禁止状态。

如必须 quarantine：

- owner；
- root cause；
- reason；
- expiration date；
- unblock condition。

到期自动重新变红。

---

# 16. 安全圣经

## 16.1 安全是独立验收轴

“功能能跑”不得抵消 safety regression。

## 16.2 双边界原则

```text
Permission Layer
  决定是否授权

Execution Layer
  限制可触达边界
```

两个组件分别 fail closed。

## 16.3 Workspace Boundary

任何写操作必须：

1. 输入 normalize；
2. canonicalize；
3. permission check；
4. executor second-check；
5. actual target post-resolution check。

## 16.4 Trust

未信任 workspace 的配置、hooks、MCP、instructions 不得自动获得等同用户配置的权力。

## 16.5 Resume

Resume 是安全敏感动作。会话历史可以恢复，临时授权不能无条件恢复。

## 16.6 Product Full Access

如果保留产品 Full Access，必须显式标记为 `PRODUCT_EXTENSION / INTENTIONAL_DIVERGENCE`，不能用于证明 Claude parity safety。

---

# 17. PR 行动规范

每个 PR 必须回答以下内容。

## 17.1 Scope

```text
Reference-7 component:
Engineering axis:
Aux mechanisms:
Runtime paths:
```

## 17.2 Reference Evidence

```text
Reference version:
Reference files/symbols:
Paper section if applicable:
```

## 17.3 Gap

```text
Current ClaudeRuntime behavior:
Reference behavior:
Gap class:
```

## 17.4 Contract / Invariant

写出本 PR 实际固定的规则。

## 17.5 Implementation

说明：

- 哪些文件改；
- 是否结构映射变化；
- 是否事件顺序变化；
- 是否 schema 变化；
- 是否需要 migration。

## 17.6 Tests

至少列出：

- characterization；
- unit/component；
- parity/E2E；
- fault/security（如适用）。

## 17.7 Divergence

必须选择：

```text
EXACT
SEMANTIC_EQUIVALENT
PYTHON_ADAPTATION
PRODUCT_EXTENSION
INTENTIONAL_DIVERGENCE
PARTIAL
```

## 17.8 Mapping Update

勾选：

- package map；
- symbol map；
- callgraph；
- runtime path；
- aux loop；
- divergence registry。

---

# 18. Review Gate：任何一个 Gate 失败，PR 不得宣称 parity 完成

## Gate A — Evidence

Reference 证据是否明确？

## Gate B — Architecture

是否破坏单主循环、单权限语义、单 ToolDescriptor 等核心边界？

## Gate C — Behavior

Contract tests 是否证明目标行为？

## Gate D — Safety

是否引入权限、workspace、secret、process regression？

## Gate E — State

是否改变 transcript/resume/compact 语义？若改变，是否 version/migration？

## Gate F — Surface

多入口是否仍共享核心？

## Gate G — Divergence

所有差异是否已登记？

## Gate H — Mapping

Source map 是否同步？

---

# 19. 禁止事项

以下行为原则上禁止：

1. 仅因文件名相似就宣布“已复刻”；
2. 仅因 unit test 通过就宣布 E2E parity；
3. 机械逐文件翻译 TS，而不理解 runtime contract；
4. 为了“Python 风格”随意改变关键调用顺序；
5. Surface 创建第二套 Agent Loop；
6. Tool 自己绕过统一 Permission/Execution；
7. MCP/Plugin/Hook 获得比 builtin 更宽的隐式权限；
8. 在重构 `query.py` 时同时改行为；
9. 在 persistence schema 改动时直接覆盖旧 session；
10. 用 README/FEATURE_LIST 的勾选代替源码验证；
11. 长期允许 known-red 主干；
12. 把 Product Extension 算入 Reference Parity 分数；
13. 为追求“95%”人为降低评分标准；
14. reference 证据不足时凭经验补齐后宣称一致；
15. 只测 happy path，不测 abort/retry/deny/crash；
16. 只比较工具数量，不比较 Tool Contract；
17. 只比较目录结构，不比较调用关系；
18. 只比较行为，不登记有意结构差异。

---

# 20. 决策优先级

发生冲突时按以下顺序裁决：

```text
1. Safety invariant
2. Reference semantics
3. State correctness / recoverability
4. Single-core architecture consistency
5. Behavioral parity
6. Cross-surface consistency
7. Performance
8. Python ergonomics
9. Product extension convenience
10. Cosmetic similarity
```

特殊规则：如果 reference 行为本身已知存在明显风险，而 ClaudeRuntime 选择更安全实现，可保留，但必须登记 `INTENTIONAL_DIVERGENCE`，并提供 `claude-parity` 行为兼容策略（如果合理）。

---

# 21. Backlog 优先级规则

## P0

- workspace escape；
- permission bypass；
- data corruption；
- unsafe execution；
- broken recovery；
- reference main-path blocker；
- 已知主干安全红线。

## P1

- Agent Loop lifecycle mismatch；
- State/Resume mismatch；
- Permission mode mismatch；
- Tool scheduling mismatch；
- critical runtime path missing；
- source-map critical symbol missing。

## P2

- context observability；
- auxiliary lifecycle completeness；
- surface contract；
- fault tolerance；
- performance parity。

## P3

- UI polish；
- noncritical naming；
- cosmetic source similarity；
- low-impact extension alignment。

---

# 22. Scorecard：以后不要只有一个“86%”

综合分只能作为摘要。必须至少输出以下指标。

```yaml
reference_target: claude-code-2.1.88
clauderuntime_commit: abc123

framework_parity: 0.94
structure_parity: 0.91
symbol_parity: 0.88
critical_call_edge_parity: 0.95
runtime_path_parity: 0.93
aux_mechanism_parity: 0.89
behavior_contract_parity: 0.94
state_parity: 0.92
safety_parity: 0.94
surface_contract_parity: 0.91

critical_missing: 2
unknown_critical: 1
intentional_divergences: 37
product_extensions: 22
known_red_tests: 0
workspace_escape_cases: 0
```

## 22.1 Score 不得“凭印象”生成

每一项必须由 machine-readable inventory 派生：

```text
passed mapped items / eligible mapped items
```

## 22.2 Unknown 必须单列

不能把 Unknown 当成 Pass，也不能简单当成 Fail；必须单独显示，避免虚假高完成度。

---

# 23. Definition of Done：四层完成门

一个功能从“写完”到“parity 完成”，必须通过四层。

## D1 — Implementation Done

代码存在，局部功能可运行。

## D2 — Contract Done

行为、状态、不变量有自动测试。

## D3 — Parity Done

Reference mapping、runtime path、差异分类全部完成。

## D4 — Production Done

E2E、fault、安全、平台、迁移、文档、CI gate 全部通过。

只有 D3 以上才能计入“Reference Parity 完成率”。

---

# 24. 阶段验收

## Phase I Exit Gate

必须达到：

- current plan 中 P0 全闭合；
- Python 主测试 0-red；
- TUI known-red 清零；
- workspace escape 0；
- max_turns / max_cost 真正 enforce；
- ToolDescriptor 收口；
- Resume/Compact 核心 contract；
- Context trace；
- Multi-Surface canonical events；
- 七组件行为 Scorecard ≥ 目标阈值。

## Phase II Exit Gate

必须达到：

- Reference critical package 100% mapped；
- critical symbol map ≥ 95%；
- critical call edge ≥ 95%；
- RP-01～RP-12 全部有 reference/Python trace；
- AUX-01～AUX-12 全部有状态；
- Unknown critical 接近 0；
- 所有 intentional divergence 有理由和测试；
- Source/Architecture parity 可以由脚本生成报告。

## Phase III Exit Gate

必须具备：

- reference version diff ingestion；
- affected mapping 自动标记；
- parity backlog 自动/半自动生成；
- historical scorecard；
- release baseline 可重现；
- 新版本升级不再依赖一次性人工大审计。

---

# 25. 每日/每 PR/每里程碑行动纪律

## 每日开发

开发前：

```text
Reference evidence → Current trace → Gap → Contract → Test
```

开发后：

```text
Code → Tests → Runtime path → Mapping → Divergence → Scorecard
```

## 每个 PR

至少关闭一个明确 gap，不允许只有“重构得更漂亮”。

## 每个里程碑

重新生成：

- baseline；
- parity scorecard；
- unresolved P0/P1；
- unmapped critical symbols；
- known divergences；
- runtime path status。

---

# 26. 推荐目录治理

在 Phase I 稳定后逐步形成：

```text
src/
  entrypoints/          # surfaces
  runtime/              # surface-facing runtime contract
  query/                # authoritative agent loop
  permissions/          # policy
  tool_system/          # tool descriptors/registry/executor
  execution/            # isolation/backends/policies
  context_system/       # prompt/context projection
  persistence/          # state schema/recovery/migrations
  services/             # supporting services

  # existing feature packages may remain,
  # 但必须能映射回上述核心边界。

tests/
  unit/
  component/
  parity/
  runtime_paths/
  security/
  fault/
  long_horizon/

docs/parity/
  baseline/
  source-map/
  runtime/
  divergences/
  scorecards/
```

目录形似不是目标，**责任和调用关系可映射**才是目标。

---

# 27. 模板：Source Symbol Mapping

```yaml
id: SYM-QUERY-001
reference:
  version: 2.1.88
  path: src/query.ts
  symbol: query
  evidence: CONFIRMED
clauderuntime:
  path: src/query/query.py
  symbol: query
status: SEMANTIC_EQUIVALENT
criticality: P0
contracts:
  - single authoritative loop
  - tool results feed next round
  - terminal reason is unique
runtime_paths:
  - RP-01
  - RP-02
  - RP-04
tests:
  - tests/parity/test_query_state_parity.py
divergences:
  - Python async implementation
notes: ""
```

---

# 28. 模板：Runtime Path Card

```yaml
id: RP-04
name: Bash execution
reference_entry:
  file: ...
  symbol: ...
python_entry:
  file: ...
  symbol: ...
steps:
  - model emits tool_use
  - registry resolves Bash
  - permission evaluates command
  - execution backend starts process
  - stdout/stderr collected
  - abort/timeout cleans tree
  - tool_result normalized
invariants:
  - deny before execution
  - no orphan process
  - result bound to exact tool_use
status: PARTIAL
gaps:
  - ...
tests:
  - ...
```

---

# 29. 模板：Divergence Record

```yaml
id: DIV-001
area: permission.default_mode
classification: INTENTIONAL_DIVERGENCE
reference_behavior: default ask/deny-first posture
project_behavior: Full Access by default in product profile
reason: lower-friction product UX
parity_profile_behavior: reference-compatible default
risk: high
owner: ...
tests:
  - product profile fixture
  - claude parity profile fixture
review_date: 2026-09-01
```

---

# 30. 模板：Parity Test Header

```python
"""
Parity target: RP-07 / Session Resume
Reference target: Claude Code 2.1.88
Reference evidence: <file/symbol/section>
Contract:
  - transcript replays
  - transient permission does not restore
  - compact state reconstructs
Allowed divergence:
  - Python storage layout may differ
"""
```

---

# 31. 模板：PR Checklist

```markdown
## Parity Scope
- Reference-7 component:
- Engineering axis:
- Runtime paths:
- Auxiliary mechanisms:

## Reference Evidence
- Target version:
- Files/symbols:

## Gap
- Current:
- Reference:
- Gap class:

## Contract / Invariant
- [ ] Defined before implementation

## Tests
- [ ] Characterization
- [ ] Unit/component
- [ ] Runtime-path/E2E
- [ ] Safety/fault if applicable

## Mapping
- [ ] Package map
- [ ] Symbol map
- [ ] Call edge
- [ ] Runtime path
- [ ] Aux map

## Divergence
- Classification:
- [ ] Registry updated

## Release Safety
- [ ] No known-red added
- [ ] No workspace escape regression
- [ ] No transcript migration risk unhandled
```

---

# 32. 当前立即执行顺序

本圣经生效后，不改变已经确定的第一阶段优先顺序：

```text
1. Workspace Boundary P0
2. Advisor metadata + TUI 0-red baseline
3. max_turns / max_cost runtime enforcement
4. Permission parity profile + pre-trust gate
5. ExecutionBoundary + SandboxBackend
6. Query responsibilities refactor
7. Persistence resume/fork/rewind contract
8. Context trace + semantic corpus
9. Cross-surface runtime event contract
10. Continuous scorecard
```

同时新增一条平行工作流：

```text
从第 1 个 PR 开始同步建立 Source Map，
而不是等 Phase I 结束后再补文档。
```

即：

> **Phase I 继续修系统；Source Mapping 从现在开始伴随开发。**

这样可以避免第一阶段结束后再次面对“功能都做了，但 reference 对应关系说不清”的二次审计成本。

---

# 33. 最终完成状态的定义

项目最终不是以“目录看起来像 Claude Code”作为完成，而是能稳定回答以下问题：

1. Claude Code 的某个关键模块，在 ClaudeRuntime 中对应哪里？
2. 某个 reference symbol 在 Python 中由谁承担？
3. 某条关键调用边，在 Python 中是否存在？
4. 某个 tool_use 从模型到执行环境经过了哪些相同边界？
5. Permission 在哪里介入，是否 deny-first？
6. Resume 后哪些状态恢复、哪些状态不恢复？
7. Compaction 是否保持关键语义？
8. Subagent 是否拥有对应 sidechain 和 parent boundary？
9. MCP 是否走统一 Permission/Execution？
10. Interrupt 是否能清理 model/tool/process 并留下稳定 terminal？
11. 哪些地方是 Python adaptation？
12. 哪些地方是产品扩展？
13. 哪些地方仍然 Unknown/Missing？
14. 完成度数字由哪些 machine-readable 条目计算得到？

当这些问题能够通过源码、映射、测试和 CI 报告自动或半自动回答时，ClaudeRuntime 才真正从：

```text
“高完成度 Claude Code 风格 Agent”
```

进入：

```text
“高保真、可验证、可持续跟踪的 Claude Code Python Runtime 复刻工程”
```

---

# 34. 参考基线

本行动圣经基于以下现有资料形成：

- `clauderuntime-current-diagnostic-2026-08-10.md`：当前 B0 诊断基线；
- `clauderuntime-optimization-development-plan-2026-08-10.md`：Phase-I 七组件优化计划；
- *Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems*，arXiv:2604.14228v2；
- Claude Code v2.1.88 对应 source-map / 参考源码分析资料；
- `Nuos/clauderuntime` 当前 `main` 的 Query、Permissions、Tool System、Context、Session、Execution、Entrypoints 与 parity tests 结构。

## 34.1 维护规则

每次 Reference target 或 ClaudeRuntime Baseline 更新时，本圣经原则本身不随 commit 重写，只更新：

- Baseline registry；
- Scorecard；
- Source Map；
- Runtime Path status；
- Divergence registry；
- Phase plan。

如果发现本圣经中的原则与更高优先级 reference 证据冲突，必须：

1. 建立 evidence issue；
2. 写明冲突；
3. 更新圣经版本号；
4. 记录决策；
5. 不得静默修改标准以适应现有实现。

---

# 35. 执行口令

项目后续所有 Claude Code 高保真复刻工作统一遵循：

> **先找参考证据，再追当前调用链；先定义不变量，再写代码；先冻结行为，再重构；每个差异必须命名，每个关键机制必须有 Runtime Path，每个完成度必须能被测试和映射证明。**

这条规则优先于“多做几个工具”“快速堆功能”“目录看起来更像”“README 先写完成”。
