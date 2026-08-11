# Nuos/clauderuntime 当前项目诊断结果说明书

> **诊断对象**：`https://github.com/Nuos/clauderuntime`  
> **诊断基线**：`main`，HEAD `241d704480c0e4aa1bfb97c607a5e2e13e871e46`（2026-08-10 抽样核验）  
> **对照基线**：论文 *Dive into Claude Code: The Design Space of Today’s and Future AI Agent Systems*，Claude Code v2.1.88  
> **限定范围**：仅从七个核心功能组件进行诊断，不评价模型能力、品牌/UI相似度、商业生态或非七组件功能。  
> **结论性质**：源码静态审计 + 当前仓库维护文档/测试目录/最近提交的证据汇总；本次未在本地完整克隆并重新执行全部测试，因此“测试已绿”只在仓库提交说明或现有测试结构支持的范围内引用。

---

## 1. 诊断结论摘要

此前将该项目估计为约 40% 的“Agent 骨架复刻”已经不适用于当前 `main`。当前仓库已经具有大规模 Python 运行时、统一 Query 层、较完整的权限体系、五层上下文压缩、丰富工具框架、持久化、Hook、MCP、多入口、TUI/Desktop/VS Code 等工程实现。更准确的定位是：

> **clauderuntime / ClawCodex 已经从“Claude Code 架构骨架复刻”进入“高完成度、生产化导向的 Python 复刻/移植阶段”。**

从七组件口径估计，当前整体完成度约为 **85%–88%**；其中结构覆盖率更高，安全语义与少数行为一致性仍是决定能否继续逼近 Claude Code 的主要短板。

### 1.1 七组件评分

| 七组件 | 当前完成度 | 主要判断 | 当前主要缺口 |
|---|---:|---|---|
| User / Interfaces | **90/100** | CLI、Headless、Agent Server、TUI、Desktop、MCP 入口等已经形成多入口运行面 | 与 Claude Code Agent SDK/IDE 事件协议的逐字段语义一致性仍未完全证明；多 UI 状态还有已知失败测试 |
| Agent Loop | **92/100** | `src/query/query.py` 已是完整流式 Agent Loop，包含重试、fallback、stop hook、token budget、compaction、abort、tool round | 单文件过重；`max_cost_usd/settings.max_turns` 存在维护文档中明确的落地缺口；长对话/后台 housekeeping 仍有调度问题 |
| Permission System | **82/100** | `src/permissions/` 已是大型子系统，包含 mode、check、filesystem、Bash 安全、handler 等 | P0：workspace boundary E2E 阻断失败；默认 Full Access 与论文所述 Claude Code 默认 deny-first/ask 的产品安全姿态存在有意差异 |
| Tools | **93/100** | Registry、schema validation、ToolContext、ToolSearch、丰富工具与并发属性框架完整 | P0：Advisor 的 read-only/concurrency-safe 属性与 parity 期望不一致；工具元数据应继续收口为单一真相源 |
| State & Persistence | **86/100** | JSONL transcript、metadata、外置大结果、SessionStorage、恢复相关模块已存在 | 存储布局与 Claude Code 并非逐字等价；fork/rewind/permission-not-restored/sidechain 的端到端语义应继续做契约核验 |
| Context & Memory | **93/100** | 已实现与论文同构的五层 compression pipeline，另有 CLAWCODEX.md、memory prefetch、prompt cache | 层级行为复杂、可观测性不足；`microcompact` 默认关闭是有依据的实现差异；文档状态明显滞后于代码 |
| Execution Environment | **78/100** | Bash/process tree/cwd/Git/Windows 平台层已非常成熟，并有 worktree/remote/MCP 等执行能力 | 目前审阅路径中未确认与 Claude Code “独立 shell sandbox”同等的通用隔离边界；更关键的是 workspace boundary 仍有 P0 E2E 失败 |

**加权综合判断：约 86/100。** 这里将 Permission 与 Execution 的权重提高，因为对生产 coding agent 来说，“能执行”并不等于“能被安全地允许执行”。

### 1.2 四类成熟度

| 成熟度维度 | 估计 | 说明 |
|---|---:|---|
| 结构/模块覆盖 | **92%** | 七组件均已有实质实现，而非空壳 |
| 行为/流程复刻 | **86%** | Query、tool、context、session 等主干已高度接近论文描述 |
| 安全语义复刻 | **76%** | 权限子系统很强，但默认信任姿态和 workspace boundary P0 问题显著拉低评分 |
| 工程/生产成熟度 | **88%** | 跨平台、CI、Desktop、TUI、测试规模、成本/缓存优化均说明已进入生产化阶段 |

---

## 2. 证据基线与诊断方法

### 2.1 Claude Code 七组件对照模型

论文 Figure 1 将 Claude Code 高层系统拆成：

1. **User**
2. **Interfaces**
3. **Agent Loop**
4. **Permission System**
5. **Tools**
6. **State & Persistence**
7. **Execution Environment**

高层主数据流为：

```text
User
  ↓
Interfaces
  ↓
Agent Loop ──────────────┐
  ↓ proposed action     │ load / persist
Permission System       │
  ↓ allow/ask/deny      │
Tools                   │
  ↓                     │
Execution Environment  ←┘
  ↓ tool_result
Agent Loop
```

论文同时明确：**模型负责判断下一步做什么，Harness 负责执行、授权、状态、上下文、恢复和安全边界**。因此本诊断不会因为“有 while-loop + tool calling”就给高分，而是重点看外围 deterministic harness 是否实质存在。

### 2.2 当前仓库抽样证据

关键当前实现包括：

- `src/entrypoints/`：`headless.py`、`agent_server_cli.py`、`desktop_cli.py`、`tui_launcher.py`、`serve_cli.py`、MCP 入口等。
- `src/query/`：`query.py`、`engine.py`、`transitions.py`、`token_budget.py`、`stop_hooks.py`、`tool_failure_loop_guard.py`。
- `src/permissions/`：`check.py`、`modes.py`、`filesystem.py`、`handler.py`、Bash parser/security/validation 等。
- `src/tool_system/`：`registry.py`、`build_tool.py`、`context.py`、`schema_validation.py`、`tool_search.py` 与 `tools/`。
- `src/context_system/` + `src/services/compact/`：项目上下文、memory、prompt assembly、cache、五层 compression pipeline。
- `src/services/session_storage.py`：JSONL transcript、session metadata、外置大 tool result、list/cleanup。
- `src/utils/shell_platform.py`：POSIX/Windows shell 统一、Git Bash、process-tree kill、cwd tracking、Git 路径解析。
- `tests/parity/`：agent isolation、compression pipeline、context、concurrency、permission、query state、E2E read/edit/multi-tool/error-recovery 等 parity 测试。

### 2.3 证据等级

本文档使用三种判断：

- **确认（Confirmed）**：已直接看到当前仓库模块/实现或仓库维护的明确 TODO。
- **推断（Inferred）**：从结构与调用关系可合理推断，但未逐条执行运行验证。
- **未核验（Unverified）**：不能根据本次抽样证明，不作为“已完成”结论。

---

## 3. User / Interfaces 诊断

### 3.1 已确认能力

`src/entrypoints/` 已经不是单 CLI 入口，而包含 headless、agent-server、desktop、TUI、serve、MCP 等入口。README 当前还说明 CLI/TUI/Desktop 共用同一后端配置与 session store，并已有 VS Code integration 的项目记录。

这与论文“多个 surfaces 汇聚到共享核心运行路径”的方向一致。

### 3.2 与 Claude Code 的差异

Claude Code 的论文基线特别强调：Interactive CLI、Headless CLI、Agent SDK、IDE/Desktop/Browser 的差异主要在 rendering/interaction surface，而核心 query path 是共享的。

当前 clauderuntime 已经达到“多 surface 共用核心”的方向，但仍需继续证明：

- 所有入口是否真正使用同一 Query 生命周期与同一 permission/context/tool semantics；
- streaming event 是否具有统一 typed contract；
- cancellation、interrupt、tool progress、approval、resume 是否跨 surface 语义一致；
- Python 项目的 agent-server/bridge protocol 与 Claude Code Agent SDK 并非同一接口定义，属于**功能同构而非协议逐字复刻**。

### 3.3 当前缺陷

仓库 TODO 明确存在 TUI 8 个预存 vitest failures，分布于 event handler、cursor drift、status rule、config sync、virtual height 等。这不是核心 Agent 正确性失败，但意味着“多 surface 行为一致”尚未达到全绿基线。

**诊断：90/100。**

---

## 4. Agent Loop 诊断

### 4.1 当前实现已经不是简化 while-loop

`src/query/query.py` 当前约 137KB，`QueryParams` 已包含：

- messages / system prompt
- tool registry / ToolContext
- provider
- abort controller
- token budget
- max turns
- fallback model
- pipeline config
- text/thinking streaming callback
- thinking/effort 配置

并在同一 Query 层接入：

- 529 overload 特定重试；
- 通用 429/5xx/connection/timeout 重试；
- output-token escalation；
- prompt-too-long 处理；
- stop hooks；
- tool failure loop guard；
- compression pipeline；
- deferred tool discovery；
- prompt-cache 边界；
- abort/interrupt。

这已经非常接近论文中“核心 loop 简单，但外围 harness 很厚”的真实形态。

### 4.2 当前主要不足

1. **Query 单体过大**：`query.py` 已承担模型调用、缓存、工具轮、恢复、hook、上下文、provider 差异等过多职责。功能越接近 Claude Code，未来变更回归风险越高。
2. **预算治理未完全闭环**：TODOS 明确写出 `SettingsSchema.max_cost_usd` 和 `settings.max_turns` 定义/校验后没有在所有 Query/agent-server 路径真正执行；TUI 也不能完整转发 per-launch `--max-turns`。
3. **后台 housekeeping 与前台 turn 耦合**：AskUserQuestion 等长等待会阻断 worker idle-branch 的 scheduled/notification tick。
4. **compat 层仍存在**：`agent_loop_compat.py` 表明历史运行路径/兼容路径尚未完全收口。兼容层本身不是问题，但必须防止形成“双主循环”。

**诊断：92/100。**

---

## 5. Permission System 诊断

### 5.1 当前实现成熟度显著高于早期项目文档

`src/permissions/` 已包含大量实际模块：

- mode 与 level
- rule checking
- filesystem containment
- Bash command parsing/validation/security/suggestion
- dangerous safety
- handler
- plan transitions
- read-only command/table

因此不能再将它描述成“仅有 allow/deny 框架”。

### 5.2 关键 P0 问题

仓库当前 `TODOS.md` 明确记录：

> **Writes and reads outside the workspace root are not being blocked in the e2e flow tests.**

对应已知失败：

- `tests/parity/test_e2e_edit_flow.py::...test_write_outside_workspace_blocked`
- `tests/parity/test_e2e_file_read.py::...test_read_outside_workspace_blocked`

这是七组件中最需要优先处理的问题。原因不是“某个测试红了”，而是**权限系统的空间边界 invariant 没有在真实 E2E tool flow 中成立**。

### 5.3 与 Claude Code 的安全姿态差异

论文描述 Claude Code 默认是：

```text
deny > ask > allow
unmatched risk-bearing action → ask human
```

而当前项目 README/近期提交将 interactive/desktop 的默认体验描述为 **Full Access by default**，再通过 `/permissions` 降级。

这可以作为 ClawCodex 自己的产品选择，但若目标是“最大限度复刻 Claude Code”，它属于明确的**安全语义差异**，不能只看权限模块数量来忽略。

建议区分两个目标：

- **ClawCodex 产品模式**：保留 Full Access 默认，但清晰标注风险；
- **Claude Code Parity Mode**：默认 `default/ask`，完全遵循 deny-first + human escalation。

**诊断：82/100。**

---

## 6. Tools 诊断

### 6.1 当前完成度

当前工具框架具有：

- Tool Registry
- Tool build/factory
- ToolContext
- schema validation
- deferred ToolSearch
- tool renderers
- task manager
- 大量 concrete tools
- MCP tool 接入
- agent/worktree/skill 等 meta-tool

论文强调 Claude Code 的工具系统并不是“函数集合”，而是 tool schema、permission、orchestration、result normalization、context feed-back 的组合。clauderuntime 已经基本进入这一层级。

### 6.2 当前 P0 缺陷

TODOS 记录 Advisor tool 的：

- `is_read_only({}) == True`
- `is_concurrency_safe({}) == True`

与 parity suite 的默认属性期望冲突，并造成 advisor smoke-flow 失败。

这类错误的严重性高于看起来的“属性字段错误”，因为它会改变 Streaming/parallel tool executor 的调度语义。

### 6.3 结构风险

应继续将：

```text
schema
read_only
concurrency_safe
permission category
result budget
sandbox requirement
side-effect class
```

收口到统一 `ToolDescriptor/ToolPolicyMetadata`，避免每个 tool 独立散落判断。

**诊断：93/100。**

---

## 7. State & Persistence 诊断

### 7.1 当前确认能力

`src/services/session_storage.py` 已实现：

- per-session directory；
- `transcript.jsonl`；
- `metadata.json`；
- buffered append；
- metadata atomic replace；
- 大 tool result 独立 content 文件；
- typed message restore；
- session listing / retention cleanup。

这已经符合论文强调的“append-oriented durable state”基本思想。

### 7.2 与 Claude Code 的不同

Claude Code 论文基线的状态系统还强调：

- project-scoped transcript；
- compact boundary 作为 append event；
- resume / fork；
- rewind files；
- subagent sidechain；
- session-scoped permission 不随 resume 恢复。

clauderuntime 已有 session/recovery/agent resume 等实现迹象，但本次抽样没有逐条证明上述行为与 Claude Code 完全等价。因此建议把这些做成**明确的 persistence parity contract**，而不是继续靠目录/函数名推断。

### 7.3 风险

- transcript schema 演化与版本迁移；
- crash 发生在 tool side effect 已完成但 transcript 未 flush 时的重放语义；
- concurrent writer locking 的跨平台一致性；
- fork/rewind 后 permission state 是否误继承；
- content replacement 的可恢复性。

**诊断：86/100。**

---

## 8. Context & Memory 诊断

### 8.1 已实现与论文同构的五层 pipeline

当前 `src/services/compact/pipeline.py` 在文件头明确列出五层：

1. `apply_tool_result_budget`
2. `snip_compact`
3. `microcompact`
4. `context_collapse`
5. `autocompact`

这与论文 v2.1.88 的五层压缩结构是一一同构的。项目还拥有：

- `clawcodex_md.py`
- `memory_prefetch.py`
- `prompt_assembly.py`
- `system_prompt_cache.py`
- `workspace_snapshot.py`
- `git_context.py`
- DeepSeek stable/request prompt scope 优化
- `/eco` deterministic tool-output compression

因此 Context 已从早期“basic context builder”升级成当前项目最强的组件之一。

### 8.2 需要继续优化的方向

- `microcompact` 默认关闭是当前代码明确的 parity 选择，不应简单视为缺失；但必须记录“为何关闭、什么 feature flag 可启用、行为与 TS 哪一版对齐”。
- 五层压缩虽然存在，但用户/开发者很难快速知道“哪一层删了什么”。需要 context trace/diagnostic。
- Summary 的事实保真、约束保留、文件状态 restoration 需要固定的 semantic regression corpus，而不仅是 token saved。
- prompt caching 与动态 memory/CLA WCODEX.md 更新存在 ordering/caching 权衡，应继续用 prefix-cache probe 做回归门。

**诊断：93/100。**

---

## 9. Execution Environment 诊断

### 9.1 当前平台执行层已经很成熟

`src/utils/shell_platform.py` 不是简单 `subprocess.run()` 包装，而是明确解决：

- POSIX Bash vs Windows Git Bash；
- 避免 Windows WSL shim；
- process-tree kill；
- `CHERE_INVOKING`；
- persistent cwd tracking；
- Windows/POSIX path conversion；
- `find_git()` 及 GUI PATH 差异。

近期提交还针对 Windows 原生 CLI、Desktop 和 worktree lane 做了真实修复，说明 Execution 已经进入“多平台 runtime”阶段。

### 9.2 决定性缺口

论文把 Claude Code 的 Permission 和 Sandbox 视为两条独立轴：

```text
authorized?  → Permission System
isolated?    → Execution Sandbox
```

本次审阅没有从当前路径中确认一个与 Claude Code `shouldUseSandbox.ts` 同等级、可独立于 permission 生效的通用 Shell sandbox 层。更严重的是，项目自身 TODO 已确认 workspace root E2E 阻断失败。

所以当前 Execution 的问题不是“不会执行”，而是：

> **执行能力很强，但独立的最小权限/隔离边界仍需要继续收紧并形成可证明 invariant。**

**诊断：78/100。**

---

## 10. 当前项目的主要问题与缺陷分级

### P0 — 必须先修

1. **Workspace boundary 读/写 E2E 阻断失败**：直接影响安全边界。
2. **Advisor tool side-effect/concurrency metadata 不一致**：可能引入错误并行调度。
3. **TUI 8 个已知测试失败**：建立“零红 baseline”，否则后续无法区分新增回归与历史失败。

### P1 — 复刻完整度的主差距

4. **Permission 默认安全姿态与 Claude Code 不同**：建议增加 parity mode，而不是强制产品模式全改。
5. **Execution sandbox 未形成可明确核验的独立边界**。
6. **`max_cost_usd` / settings `max_turns` 未在所有入口闭环执行**。
7. **Query 核心文件过重，职责耦合风险上升**。
8. **Persistence 的 resume/fork/rewind/permission reset/sidechain 需要正式契约化，而非功能散落存在即可。**

### P2 — 长时可靠性/工程治理

9. AskUserQuestion 长等待会阻塞 worker housekeeping。
10. Context compaction 的可观测性与语义保真门不足。
11. Tool metadata / permission category / concurrency property 应单一真相源。
12. 多 surface event contract 需要自动化一致性测试。
13. FEATURE_LIST 等文档已明显滞后于当前代码，形成文档漂移。

### P3 — UX 与维护性

14. Ctrl+C prompt overlay 后 status line stale。
15. 逐步移除/封存兼容路径，防止双 runtime。
16. 为七组件建立持续的 parity scorecard，而不是阶段性人工判断。

---

## 11. 需要特别强调的“差异”而非“缺陷”

有些地方不是 bug，而是 ClawCodex 主动走了不同路线：

1. **多 Provider**：Claude Code 论文聚焦 Claude 模型；当前项目扩展 Anthropic/OpenAI/DeepSeek 等 provider，属于能力扩张。
2. **Full Access 默认**：这是产品选择；若目标是 Claude Code fidelity，则是差异；若目标是低摩擦 coding agent，则可能是选择。
3. **Python runtime**：模块布局不必逐文件复刻 TypeScript，只要七组件合同与行为 invariant 对齐。
4. **DeepSeek prefix cache 与 `/eco`**：属于 Python 复刻上的性能增强，不应为了“源码形似”删除。
5. **Windows Git Bash 平台层**：这是 Python/跨平台环境下的合理实现差异。

因此后续优化原则应该是：

> **优先复刻行为契约与系统不变量，而不是机械复制 TypeScript 文件名和代码形状。**

---

## 12. 最终诊断

当前 `Nuos/clauderuntime` 不再属于“最小 Agent + 工具”的阶段。它已经拥有 Claude Code 论文所强调的大部分 operational harness：多入口、Query Loop、复杂 Permission、Tool Registry/Tool Search、五层 Context Pipeline、JSONL Session、跨平台 Execution、Hook/MCP/Agent 等。

### 当前最准确的定位

```text
早期：模型 + Loop + Tools
             ↓
当前：模型 + 高密度 Harness + 多 Surface + Parity Tests
             ↓
下一阶段：安全不变量 + 行为契约 + 独立 Sandbox + 全绿 Baseline
```

如果目标是继续向 Claude Code v2.1.88 的七组件行为靠近，最重要的不是继续增加几十个功能，而是按以下顺序收口：

```text
P0 Safety Invariants
    ↓
Permission / Execution isolation
    ↓
Loop budgets + lifecycle
    ↓
Persistence contracts
    ↓
Context semantic observability
    ↓
Cross-surface parity
    ↓
Continuous parity scorecard
```

在修复 P0 workspace boundary、工具调度属性和测试 baseline 后，项目七组件完成度有条件稳定进入 **88%–90%**；完成独立 sandbox、session 恢复语义契约、全入口预算治理和跨 surface contract 后，才适合将目标设为 **90%+ 的高保真 Claude Code runtime 复刻**。

---

## 13. 主要参考路径

### 论文

- `original-paper-arxiv-2604.14228v2.pdf`
- Section 3：Architecture Overview
- Section 4：Agentic Query Loop
- Section 5：Permission / Safety
- Section 6：Extensibility
- Section 7：Context / Memory
- Section 8：Subagents
- Section 9：Session Persistence

### 当前项目关键源码

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
