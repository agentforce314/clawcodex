# F-48 决策追踪（F48-decisions.md）

> **目的**：记录 F-48 src/ 核心路径二开修改解耦方案的架构层决策，每张 issue 卡必须先查本文档确认是否已决策。
> **状态**: 🟡 进行中（2026-06-07 起）
> **规划参考**: `docs/FEATURE_PLAN.md` §6.1 F-48
> **追踪配套**: `docs/decisions/f48-modification-tracking.md`（每文件 diff 决策，阶段 G 收尾时填充）

---

## 决策索引

| ID | 主题 | 状态 | 影响范围 | 决策日期 |
|---|---|---|---|---|
| D-1 | capabilities 层路径冲突 | ✅ 已裁决 | `upstream-sync.yaml` + 全部 Phase | 2026-06-07 |
| D-2 | F-35 Gate 三阶段同步 | ✅ 已裁决 | 全部 Phase 卡 | 2026-06-07 |
| D-3 | 决策文档分两份（架构 + 文件级） | ✅ 已裁决 | 文档结构 | 2026-06-07 |
| D-4 | F-44 人工 review 闸门保留 | ✅ 已裁决 | 每张 issue 卡 | 2026-06-07 |
| D-5 | settings/permission_validation.py 替代项登记 | ✅ 已裁决 | Phase 6 | 2026-06-07 |
| D-6 | 4 个 C 类纯格式差异 no-op 登记 | ✅ 已裁决 | Phase 5 + 审计 | 2026-06-07 |
| D-7 | 类别 B 文件决策模式（保留/还原/seam） | 🟡 占位 | Phase 4-9 | 阶段 G 收尾时填 |
| D-8 | 每 Phase 验收 Gate 字段 | 🟡 占位 | 全部 issue 卡 | 阶段 A 后续 |

---

## D-1: capabilities 层路径冲突 ✅

**问题**：F-48 决策 #1 写到"注册表/Protocol 扩展点放在 `src/capabilities/` 而非 `src/` 本体"，但实际代码（6 Protocol + 1 runner）全在 `extensions/capabilities/`，`src/capabilities` 物理不存在。`upstream-sync.yaml` 第 22-27 行的 `paths: ["src/capabilities"]` 是失效声明。

**裁决**：**保留 `extensions/capabilities/`，修 `upstream-sync.yaml`**

**理由**：
1. 实际代码全在 `extensions/capabilities/`，迁回 `src/` 反而违反"src/ 与 upstream 一致"目标（capabilities 是二开层，不属于上游）
2. `upstream-sync.yaml` 声明的 `forbidden_imports_from: ["src.upstream"]` 仍生效，保留解耦语义
3. F-34/F-35/F-47 既定实现都基于 `extensions/capabilities/`，不破坏向后兼容
4. `upstream-sync.yaml` 是声明文件，物理不存在则声明无效，**修 yaml 是修复声明与实现的不一致**

**实施**：
- `upstream-sync.yaml` line 24：`paths: ["src/capabilities"]` → `paths: ["extensions/capabilities"]`
- 验证：`grep -A3 "name: capabilities" upstream-sync.yaml` 显示 `extensions/capabilities`
- 在 issue 卡 001-f48-decisions-and-yaml 中作为首张卡完成

**How to apply**：
- 后续 issue 卡涉及 capabilities 层 import 时，路径统一为 `extensions.capabilities.*`
- 任何新增 Protocol 必须落在 `extensions/capabilities/<protocol>.py`

---

## D-2: F-35 Gate 三阶段同步 ✅

**裁决**：F-35 Gate Criterion 严格按三阶段触发，与 plan 一致

| Gate | 触发条件 | 解锁 F-35 范围 |
|---|---|---|
| **Gate #1** | Phase 0 完成（`diff -rq` 不再 "Only in src/") | 第一批：仅影响 ext/extensions 的还原测试 |
| **Gate #2** | Phase 1-3 完成（10 核心入口点 `diff -w` 返回空） | 第二批：cli.py / tui/app.py 等 10 个二开热点 |
| **Gate #3** | Phase 4-9 全部审计登记（67 个修改文件在追踪文档） | 第三批：bridge/buddy/transport 等核心模块 |

**理由**：避免 F-35 提前触碰未审计的核心模块导致 584 文件还原失控

**How to apply**：
- 每张 Phase 卡"完成"判定中增加 F-35 Gate 状态字段
- F-35 不能跳过 Gate 启动对应批次
- 阶段 G 审计完成 = Gate #3 触发

---

## D-3: 决策文档分两份 ✅

**裁决**：
- `docs/decisions/F48-decisions.md`（**本文件**）：架构/模式/路径决策，**阶段 A 先建**
- `docs/decisions/f48-modification-tracking.md`（**后续**）：每文件 diff 决策（保留/还原/seam/no-op），**阶段 G 收尾时填**

**理由**：
- 前者是"决策的元信息"，指导后续 issue；后者是"决策的实施记录"，审计用
- 提前建前者可避免后续 issue 重复回答 capabilities 路径、Adapter 模式等问题
- 后者依赖 22 张卡的 commit 落点，必须在最后

**How to apply**：
- 任何新架构层决策追加到本文件
- 每张卡完成时仅在后者追加 1-3 行（如 "card-014-f48-phase5a-buddy-format: 3 文件 no-op"）

---

## D-4: F-44 人工 review 闸门 ✅

**裁决**：每张 issue 卡 `review_required: true`，Phase 0 + Phase 2-3 强制"二次检视"

**理由**：F-48 涉及 87+ 文件改动，单次自动 review 不足以捕获所有回归

**How to apply**：
- `clawcodex-dev orchestrator issue review --id <id> --approve` 必须由人工执行
- Phase 0/2-3 在 reviewer 通过后还需第二次 smoke test 确认

---

## D-5: settings/permission_validation.py 替代项 ✅

**裁决**：在 `f48-modification-tracking.md` 显式登记，避免后续误以为遗漏同步

**细节**：
- `src/upstream/58ea488/settings/permission_validation.py` 存在
- `src/settings/permission_validation.py` 不存在
- 替代实现：`src/settings/pydantic_adapter.py`（F-47 引入）

**How to apply**：
- Phase 6 完成后在追踪文档加一行：
  ```
  settings/permission_validation.py → settings/pydantic_adapter.py（F-47 替代，D-5 登记）
  ```

---

## D-6: 4 个 C 类纯格式差异 no-op ✅

**裁决**：以下 4 个文件 `diff -w` 验证无语义差异，仅在追踪文档登记 no-op，**不**还原也不**评审**

| 文件 | 验证方式 | 决策 |
|---|---|---|
| `buddy/notification.py` | `diff -w src/upstream/58ea488/buddy/notification.py src/buddy/notification.py` | no-op |
| `buddy/sprites.py` | 同上 | no-op |
| `buddy/types.py` | 同上 | no-op |
| `replLauncher.py` | 同上 | no-op |

**How to apply**：
- Phase 5 完成后在追踪文档加 4 行 no-op 登记
- 任何后续 `diff -w` 仍返回空则保持 no-op 状态

---

## D-7: 类别 B 文件决策模式（占位）

**待 Phase 4-9 执行后填**：每文件决策三选一
- **保留 (keep)**：差异是必要二开功能，不还原
- **还原 (revert)**：差异是上游同步遗漏，git revert 到 src/upstream/.../ 状态
- **seam (扩展点)**：用 Protocol/Facade/子类覆盖包装差异，使 src/ 与 upstream 一致

**模板**（在 f48-modification-tracking.md 中用）：

```markdown
| 文件 | 差异行数 | 决策 | 理由 | Phase 卡 |
|---|---|---|---|---|
| src/bridge/bridge_main.py | +45 / -12 | seam | JWT refresh → clawcodex_ext/bridge/auth.py Facade | 012-f48-phase4-bridge |
```

---

## D-8: 每 Phase 验收 Gate 字段（占位）

**计划**：每张 issue 卡 frontmatter 添加 `f35_gate: <1|2|3|none>` 字段，标记本卡对 F-35 Gate 的推进

**模板**：
```yaml
f35_gate: 1  # 此卡推进 Gate #1
```

**状态**：待首张卡执行后由 orchestrator 验证机制补全

---

## 附录 A: src/ 二开文件总览（基线 2026-06-07）

### 类别 A：仅 src/ 有（19 单文件 + 4 目录）

**19 单文件**：
- `src/agent/_outlines_adapter.py` → `extensions/providers_ext/`
- `src/agent/background_runner.py` → `clawcodex_ext/agent/`（已有对应）
- `src/agent/background_state.py` → `clawcodex_ext/agent/`
- `src/auth/codex_oauth.py` → `extensions/auth/`
- `src/auth/codex_store.py` → `extensions/auth/`
- `src/context_system/_gitpython_adapter.py` → `clawcodex_ext/runtime/`
- `src/entrypoints/orchestrator.py` → `extensions/orchestrator/cli/`
- `src/hooks/_pluggy_adapter.py` → `extensions/hooks/`
- `src/permissions/_treesitter_adapter.py` → `clawcodex_ext/permissions/`
- `src/providers/_litellm_adapter.py` → `extensions/providers_ext/`（已示范）
- `src/providers/codex_models.py` → `extensions/providers_ext/`
- `src/providers/openai_codex_provider.py` → `extensions/providers_ext/`
- `src/providers/runtime.py` → `extensions/providers_ext/`
- `src/repl/background_escape.py` → `clawcodex_ext/repl/`
- `src/settings/pydantic_adapter.py` → `clawcodex_ext/settings/`
- `src/skills/_frontmatter_adapter.py` → `extensions/skills_ext/`
- `src/utils/cache_warning.py` → `clawcodex_ext/utils/`
- `src/utils/session_watcher.py` → `clawcodex_ext/utils/`
- 工具类 5 个：ask_issue_author, create_agent_tool, progress_report, task_directives, task_inspect → `extensions/tools_ext/`
- TUI 屏幕 2 个：ask_user_question, permission_mode_picker → `extensions/tui_ext/screens/`

**4 目录**：
- `src/agent/tool_authoring/` (6 py) → `extensions/agent_tool_authoring/`
- `src/orchestrator/` (14 py 剩余) → `extensions/orchestrator/`（已有 26 py，合并成 40）
- `src/services/bridge/` (4 py) → `extensions/services/bridge/`
- `src/upstream/` — **vendor 快照，不计入**（D-N/A）

### 类别 B：67 个功能修改文件

按方案 §6.1.1 类别 B 表分组：bridge 6 / buddy 8 / settings 4 / providers 4 / transports 3 / query 3 / coordinator 2 / tool_system 4 / command_system 3 / repl 2 / tui 12 / 散在 8

### 类别 C：4 个纯格式差异

- `buddy/notification.py`
- `buddy/sprites.py`
- `buddy/types.py`
- `replLauncher.py`

### 类别 D：1 个缺失文件

- `src/upstream/58ea488/settings/permission_validation.py` → 替代为 `src/settings/pydantic_adapter.py`（F-47）

---

## 附录 B: 22 张 issue 卡清单

详见 `/tmp/clawcodex-issues/002-f48-*.md` 到 `023-f48-audit.md`

| # | identifier | priority | Phase | f35_gate |
|---|---|---|---|---|
| 001 | 001-f48-decisions-and-yaml | 1 | A | none |
| 002 | 002-f48-phase0-orchestrator | 2 | B | 1 |
| 003 | 003-f48-phase0-bridge-services | 3 | B | 1 |
| 004 | 004-f48-phase0-agent-tool-authoring | 3 | B | 1 |
| 005 | 005a-f48-phase0-scattered-providers | 3 | B | 1 |
| 006 | 005b-f48-phase0-scattered-adapters | 3 | B | 1 |
| 007 | 006-f48-adapter-pattern | 3 | C | none |
| 008 | 007-f48-phase1-frontend-registry | 2 | D | none |
| 009 | 008-f48-phase1-capability-protocols | 2 | D | none |
| 010 | 009-f48-phase1-tool-provider-registry | 3 | D | none |
| 011 | 010-f48-phase2-tui-subclass | 2 | E | 2 |
| 012 | 011-f48-phase3-entrypoints | 2 | E | 2 |
| 013 | 012-f48-phase4-bridge | 2 | F | 3 |
| 014 | 013-f48-phase5a-buddy-format | 3 | F | 3 |
| 015 | 014-f48-phase5b-buddy-functional | 2 | F | 3 |
| 016 | 015-f48-phase6-settings | 3 | F | 3 |
| 017 | 016-f48-phase7-providers | 2 | F | 3 |
| 018 | 017-f48-phase8-transports | 3 | F | 3 |
| 019 | 018-f48-phase9-tui-12-files | 1 | F | 3 |
| 020 | 019-f48-phase9-query-3 | 2 | F | 3 |
| 021 | 020-f48-phase9-coordinator-tool-command-repl | 2 | F | 3 |
| 022 | 021-f48-phase9-scattered-8 | 2 | F | 3 |
| 023 | 022-f48-audit | 1 | G | end |

（实际生成时按 priority 升序排序，LocalTracker 先选 priority=1）
