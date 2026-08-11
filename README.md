# ClaudeRuntime / ClawCodex Runtime

本仓库是面向 Claude Code parity 的 Python agent runtime 开发仓库，当前重点是把核心 Agent Loop、Permission、Tools、State、Context、Execution 等主链路收敛到可测试、可审计、可持续对齐的实现。

## 项目定位

- Python 实现的 CLI / Headless / TUI / Desktop agent runtime。
- 核心入口统一到 canonical Query / Tool execution / Permission pipeline。
- 以 `docs/2026-08-10-optimization-development-plan.md` 和 `docs/2026-08-10-clauderuntime-source-parity-action-bible-v1.0.md` 为当前开发规范。
- 当前开发优先级：主模块主体功能优先；外围预算、scheduler、兼容性与更细粒度约束按阶段后置。

## 关键模块

- `src/query/`：Agent Loop 主体、模型调用、工具轮次、终态、预算与恢复逻辑。
- `src/permissions/`：权限模式、权限规则、profile、pre-trust gate 与文件系统边界。
- `src/tool_system/`：内建工具定义、工具上下文、工具 schema 与执行适配。
- `src/services/tool_execution/`：工具执行编排、hook、结果持久化与 streaming executor。
- `src/services/mcp/`：MCP 配置、连接、工具包装与 runtime 注入。
- `src/entrypoints/`：headless、agent-server、serve、TUI launcher 等运行入口。
- `ui-tui/`、`ui-desktop/`：交互界面与桌面端。

## 文档

当前开发文档集中在 `docs/`：

- `docs/2026-08-10-diagnostic.md`：诊断基线。
- `docs/2026-08-10-optimization-development-plan.md`：阶段开发计划。
- `docs/2026-08-10-clauderuntime-source-parity-action-bible-v1.0.md`：source parity 总规范。
- `docs/2026-08-10-development-progress.md`：开发进度、完成度、修改模块与验证记录。
- `docs/parity/`：source map、runtime path、auxiliary map 等机器可读 parity 资产。

历史文档归档在 `docs/archive/`，该目录仅用于保留旧资料。

## 本地开发

要求 Python `>=3.10`。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .[dev]
```

常用入口：

```bash
python -m src.cli --help
python -m src.cli -p "hello"
python -m src.entrypoints.agent_server_cli --help
python -m src.entrypoints.serve_cli --help
```

安装为命令后：

```bash
clawcodex --help
clawcodex -p "hello"
```

## 测试

常用 Python 测试：

```bash
.venv/bin/python -m pytest -q
```

主链路开发建议至少覆盖：

```bash
.venv/bin/python -m pytest -q tests/test_query_*.py tests/parity/test_query_state_parity.py
.venv/bin/python -m pytest -q tests/test_permission_*.py tests/test_pre_trust_gate.py tests/test_trust_gate.py
.venv/bin/python -m pytest -q tests/test_mcp_*.py tests/integration/test_query_integration.py
```

TUI 相关修改在 `ui-tui/` 内运行：

```bash
npm test
npm run typecheck
```

## 当前阶段

已提交进度见 `docs/2026-08-10-development-progress.md`。当前分支已推进：

- `Phase A — Baseline & P0`
- `Phase B — Loop Governance（预计 3–4 个 PR）`
- `Phase C — Permission × Execution（预计 4–6 个 PR）`

后续继续严格使用开发文档中的阶段名称、阶段编号与阶段描述，不新增平行符号。

## 提交约束

- 不提交 `docs/` 下生成的 `.html` 文件。
- 不改动 `docs/archive/`。
- 文档与 PR 说明优先使用简体中文。
- 涉及权限、执行、状态恢复、MCP、Query 主循环的修改必须补充可重复的单元测试或 characterization tests。
