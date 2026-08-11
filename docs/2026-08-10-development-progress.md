# Nuos/clauderuntime 开发进度记录

> 创建日期：2026-08-10
>
> 开发基线：`main` / `241d704480c0e4aa1bfb97c607a5e2e13e871e46`
>
> 开发范围：`2026-08-10-optimization-development-plan.md` 的第一阶段（Phase A — Baseline & P0）
>
> 保护约束：不得修改 `docs/2026-08-10-diagnostic.md` 与 `docs/2026-08-10-optimization-development-plan.md`。

## 1. 总体进度

| 阶段 | 状态 | 完成度 | 说明 |
|---|---|---:|---|
| A1. Freeze baseline | 完成 | 100% | 已冻结提交、环境、测试清单/耗时、实际失败清单与七组件映射 |
| A2. Workspace boundary | 完成 | 100% | 当前基线已具有 gated-mode containment 与 tool backend 二次检查；完整 parity 通过，escape 为 0 |
| A3. Advisor metadata | 完成 | 100% | 当前基线已将 Advisor 明确登记为 ClawCodex-only 的 read-only/concurrency-safe override；parity 与 smoke 全绿 |
| A4. TUI failures | 完成 | 100% | 修复实际存在的 7 个历史失败，并限制测试 worker 以消除 CPU/定时抖动 |
| **Phase A 合计** | **完成** | **100%** | Exit Gate 达成：Python parity 与 TUI baseline 全绿、workspace escape 为 0 |

## 2. 输入文档读取记录

已完整阅读且保持只读：

| 文档 | SHA-256（开发前） | 用途 |
|---|---|---|
| `docs/2026-08-10-diagnostic.md` | `c84c51d5585a5f86a574636edf1b817e57b10e10f854dfd54441b710e8bce929` | 当前七组件诊断、P0/P1 缺口与证据基线 |
| `docs/2026-08-10-optimization-development-plan.md` | `1c8797e05b959220418b0ac3c28749d7e04fa36f9e4af1a7f6e167578638a74a` | 阶段计划、模块方案、测试与验收门 |

## 3. A1. Freeze baseline

### 3.1 环境基线

| 项目 | 当前值 |
|---|---|
| Git 分支 | `main` |
| Git HEAD | `241d704480c0e4aa1bfb97c607a5e2e13e871e46` |
| Python | `3.14.6` |
| Node.js | `v26.5.0` |
| npm | `11.17.0` |
| 操作系统 | macOS 26.5.1（Darwin 25.5.0, arm64） |
| Python 测试文件 | 605 个 `test_*.py` |
| TUI 测试文件 | 138 个（完整 Vitest 实测） |

### 3.2 已知失败清单（开发前）

| 模块 | 已知失败 | 来源 |
|---|---:|---|
| Advisor tool parity / smoke | 文档记录 4；实测 0 | `TODOS.md` + 定向 Pytest |
| Workspace boundary read/write E2E | 文档记录 2；实测 0 | `TODOS.md` + 定向 Pytest |
| TUI Vitest | 文档记录 8；实测 7 | `TODOS.md` + 完整 Vitest |

实际基线说明：Python 的 6 个历史 P0 用例在任何代码修改前即为 `6 passed`，说明 `TODOS.md` 中 A2/A3 条目已经滞后。TUI 的 cursor-drift 用例初次通过，因此实际为 7 个失败；修复后完整套件为零失败。

### 3.3 A2. Workspace boundary / A3. Advisor metadata 现有实现核验

- `ToolContext.ensure_allowed_path()` 与 `ensure_readable_path()` 会解析真实路径，并在非 `bypassPermissions` 模式下拒绝允许根之外的目标；Read/Write E2E 同时断言工具结果错误、无内容泄漏/无文件落盘以及 containment helper 抛出 `ToolPermissionError`。
- Advisor 只读取会话并把内容转发给 reviewer，`src/reference_data/ts_tool_properties.json` 已将其作为 ClawCodex-only override 明确登记为 `is_read_only=true`、`is_concurrency_safe=true`；这与当前实现和调度语义一致。
- 因 A2/A3 的实现与契约均已存在且测试全绿，本阶段未重复改写相关 Python 模块，避免制造无行为收益的改动。

### 3.4 七组件映射

| 组件 | 主要源码 | 主要测试 | Phase A 关联 |
|---|---|---|---|
| Interfaces | `src/entrypoints/`, `ui-tui/`, `ui-desktop/` | `ui-tui/src/__tests__/` | A4 |
| Agent Loop | `src/query/`, `src/server/` | `tests/parity/`, `tests/integration/` | A3 间接涉及调度 |
| Permission | `src/permissions/` | `tests/parity/test_e2e_*` | A2 |
| Tools | `src/tool_system/` | `tests/parity/test_tool_parity.py`, `tests/integration/test_advisor_smoke.py` | A3 |
| State & Persistence | `src/services/session_storage.py` | session/recovery 测试 | 本阶段仅回归保护 |
| Context & Memory | `src/context_system/`, `src/services/compact/` | context/compact 测试 | 本阶段仅回归保护 |
| Execution Environment | `src/utils/shell_platform.py` 及工具执行路径 | E2E read/write、process 测试 | A2 |

## 4. 修改记录

| 时间 | 模块/文件 | 修改内容 | 验证 |
|---|---|---|---|
| 2026-08-10 | `docs/2026-08-10-development-progress.md` | 新建开发进度记录，登记 Phase A 范围、基线和保护约束 | 文档创建完成 |
| 2026-08-10 | `ui-tui/src/app/turnController.ts` | inline diff 完成时保留 Args/Result 展开详情，紧凑行在无详情时继续使用 | 定向测试、完整 TUI 测试通过 |
| 2026-08-10 | `ui-tui/src/components/appChrome.tsx` | 补全 status segment 的 `cost` 契约及 96 列可见阈值 | statusRule 测试通过 |
| 2026-08-10 | `ui-tui/src/app/interfaces.ts` | 将缺省状态指示器恢复为配置/测试约定的 `kaomoji`；同时整理类型导入顺序 | config sync、ESLint、typecheck 通过 |
| 2026-08-10 | `ui-tui/src/lib/inputMetrics.ts` | 修正 transcript 水平保留列，使复合 user prompt 的实际宽度进入换行与高度估算 | virtualHeights 与完整 TUI 测试通过 |
| 2026-08-10 | `ui-tui/src/__tests__/createGatewayEventHandler.test.ts` | 将 Patch 标签断言对齐现行无引号 `formatToolCall` 契约 | 定向测试通过 |
| 2026-08-10 | `ui-tui/vitest.config.ts` | 限制 `maxWorkers=4`，避免全套测试过度并发导致 cursor/child-process 定时用例抖动 | 完整 TUI 测试零失败 |

## 5. 测试与验收记录

| 验证项 | 结果 | 耗时/备注 |
|---|---|---|
| 历史 6 个 Python P0 用例 | `6 passed` | 3.76s；修改前基线 |
| Python parity 全套 | `381 passed` | 14.62s；2 个既有 DeprecationWarning |
| Advisor integration 全文件 | `3 passed` | 0.82s |
| TUI 四个修复相关文件 | `136 passed` | 1.17s |
| TUI 完整套件 | `138 files passed`; `1693 passed`, `4 skipped` | 11.12s；零失败 |
| TypeScript typecheck | 通过 | `tsc --noEmit -p tsconfig.json` |
| 修改文件 ESLint | 通过 | 5 个 TS/TSX 源码与测试文件 |
| 补丁格式检查 | 通过 | `git diff --check` 无输出 |

### Exit Gate A

- [x] Python parity baseline 全绿。
- [x] Advisor smoke/parity 全绿。
- [x] Workspace read/write escape 用例为 0 escape。
- [x] TUI baseline 全绿，无 known red。
- [x] 两份输入 Markdown 的 SHA-256 与开发前一致。

## 6. 风险与备注

- 当前工作区在开发开始前已有多项 `docs/` 删除和未跟踪文件；这些均视为用户现有改动，不回滚、不覆盖。
- 代码知识图谱未索引当前仓库；已按项目规则先尝试图谱，确认不可用后才回退到文件搜索和测试定位。
- 两份输入文档在开发结束时再次计算 SHA-256，均与开发前一致，确认未修改。
- TUI 测试会尝试探测一个未安装的旧 `clawcodex_cli` Python 包并向 stderr 输出 `ModuleNotFoundError`，但相关测试采用回退路径且完整套件退出码为 0；本阶段未扩大范围处理该非阻断噪声。
- 完成时间：2026-08-10 21:01:51 PDT。

## 7. GitHub 提交记录

| 项目 | 内容 |
|---|---|
| 阶段 | `Phase A — Baseline & P0` |
| 分支 | `agent/phase-a-baseline-p0` |
| 提交说明 | `fix(tui): complete Phase A baseline and P0` |
| PR 目标 | `main`（Draft Pull Request） |
| 提交范围 | 本文档及 Phase A 的 6 个 TUI 修改文件 |
| 明确排除 | 两份只读输入 Markdown、HTML、`IDEA.md`、开发开始前已有的文档删除项 |
