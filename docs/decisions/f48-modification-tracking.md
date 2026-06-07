# F-48: src/ 核心路径二开修改解耦 — 修改追踪

> **文档状态**: 进行中
> **最后更新**: 2025-07-15
> **关联**: [FEATURE_PLAN.md §6.1](../FEATURE_PLAN.md#61-f-48-src-核心路径二开修改解耦方案)

---

## Phase 0: 纯新增文件移入 ext（30 项）

### 已迁移文件

| # | 原路径 (src/) | 目标路径 (clawcodex_ext/) | 决策 | 状态 |
|---|-------------|--------------------------|------|------|
| 1 | `agent/_outlines_adapter.py` | `clawcodex_ext/agent/_outlines_adapter.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 2 | `agent/background_runner.py` | `clawcodex_ext/agent/background_runner.py` | 已在 ext 中 | ✅ 已完成 |
| 3 | `agent/background_state.py` | `clawcodex_ext/agent/background_state.py` | 已在 ext 中 | ✅ 已完成 |
| 4 | `agent/tool_authoring/` (10 files) | `clawcodex_ext/agent/tool_authoring/` | 纯二开新增目录，移入 ext | ✅ 已完成 |
| 5 | `auth/codex_oauth.py` | `clawcodex_ext/auth/codex_oauth.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 6 | `auth/codex_store.py` | `clawcodex_ext/auth/codex_store.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 7 | `context_system/_gitpython_adapter.py` | `clawcodex_ext/context_system/_gitpython_adapter.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 8 | `entrypoints/orchestrator.py` | `clawcodex_ext/entrypoints/orchestrator.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 9 | `hooks/_pluggy_adapter.py` | `clawcodex_ext/hooks/_pluggy_adapter.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 10 | `orchestrator/` (19+ files) | `extensions/orchestrator/` | 独立子扩展，不入侵 src/ | ✅ 设计决定 #10 |
| 11 | `permissions/_treesitter_adapter.py` | `clawcodex_ext/permissions/_treesitter_adapter.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 12 | `providers/_litellm_adapter.py` | `clawcodex_ext/providers/_litellm_adapter.py` | 已在，已是 shim | ✅ 已完成 |
| 13 | `providers/codex_models.py` | `clawcodex_ext/providers/codex_models.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 14 | `providers/openai_codex_provider.py` | `clawcodex_ext/providers/openai_codex_provider.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 15 | `providers/runtime.py` | `clawcodex_ext/providers/runtime.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 16 | `repl/background_escape.py` | `clawcodex_ext/repl/background_escape.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 17 | `services/bridge/` (4 files) | `clawcodex_ext/services/bridge/` | 纯二开新增目录，移入 ext | ✅ 已完成 |
| 18 | `services/tail_follower.py` | `clawcodex_ext/services/tail_follower.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 19 | `settings/pydantic_adapter.py` | `clawcodex_ext/settings/pydantic_adapter.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 20 | `skills/_frontmatter_adapter.py` | `clawcodex_ext/skills/_frontmatter_adapter.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 21 | `tool_system/tools/ask_issue_author.py` | `clawcodex_ext/tool_system/tools/ask_issue_author.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 22 | `tool_system/tools/create_agent_tool.py` | `clawcodex_ext/tool_system/tools/create_agent_tool.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 23 | `tool_system/tools/progress_report.py` | `clawcodex_ext/tool_system/tools/progress_report.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 24 | `tool_system/tools/task_directives.py` | `clawcodex_ext/tool_system/tools/task_directives.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 25 | `tool_system/tools/task_inspect.py` | `clawcodex_ext/tool_system/tools/task_inspect.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 26 | `tui/screens/ask_user_question.py` | `clawcodex_ext/tui/screens/ask_user_question.py` | 纯二开新增，移入 ext | ✅ 已完成（注：`src.tui` 有预先存在的循环导入问题，不影响 ext 版本使用） |
| 27 | `tui/screens/permission_mode_picker.py` | `clawcodex_ext/tui/screens/permission_mode_picker.py` | 纯二开新增，移入 ext | ✅ 已完成（同上） |
| 28 | `utils/cache_warning.py` | `clawcodex_ext/utils/cache_warning.py` | 纯二开新增，移入 ext | ✅ 已完成 |
| 29 | `utils/session_watcher.py` | `clawcodex_ext/utils/session_watcher.py` | 纯二开新增，移入 ext | ✅ 已完成 |

### no-op 格式差异文件（类别 C，无需处理）

| # | 文件 | 说明 |
|---|------|------|
| 1 | `buddy/notification.py` | 仅行尾/空白差异，无语义变化 |
| 2 | `buddy/sprites.py` | 仅行尾/空白差异，无语义变化 |
| 3 | `buddy/types.py` | 仅行尾/空白差异，无语义变化 |
| 4 | `replLauncher.py` | 仅行尾/空白差异，无语义变化 |

### 上游有但 src/ 缺失的文件（类别 D）

| # | 文件 | 替代方案 |
|---|------|---------|
| 1 | `settings/permission_validation.py` | 被 `settings/pydantic_adapter.py` (现 `clawcodex_ext/settings/pydantic_adapter.py`) 替代 |

## Phase 1-3: 已设计解耦（10 个文件）

参见 FEATURE_PLAN.md Phase 1-3 表格 — 已通过 Facade/子类覆盖/注册表模式完成解耦。

## Phase 4-9: 功能修改文件 — 逐行评审

### Phase 4: Bridge 文件回归（6 文件，中等风险）

| 文件 | diff 大小 | 差异性质 | 决策 | 状态 |
|------|-----------|---------|------|------|
| `bridge/__init__.py` | +1 line | ADD: `'BridgeState'` 到 `__all__` | **KEEP** — 是必需的公开类型导出 | ✅ 无需改动 |
| `bridge/bridge_main.py` | ~300 lines | ADD + DEL: 大量重构，移除 JWT refresh、简化 daemon 逻辑 | **KEEP** — 是 Phase 8 二开有意简化的版本 | 📋 已记录 |
| `bridge/bridge_pointer.py` | +8 lines | ADD: `clear_pointer()` 函数 + `__all__` | **KEEP** — 必要的桥接管理功能 | ✅ 无需改动 |
| `bridge/repl_bridge.py` | ~649 lines | ADD + DEL: docstring 重写 + 行为修改 | **待评审** — 需逐行确认差异来源 | ⏳ |
| `bridge/repl_bridge_transport.py` | +33 lines | ADD: 新增行为 | **待评审** — 确认是否必要的桥接集成修改 | ⏳ |
| `bridge/worktree.py` | +7 lines | ADD: `__all__` + `remove_agent_worktree()` 完善 | **KEEP** — 必要的 worktree 管理功能 | ✅ 无需改动 |

> **注意**：Bridge 文件的差异可能是上游 58ea488→后续版本之间的官方更新被二开意外覆盖。已通过 `git log src/bridge/` 追溯，确认差异来源为二开新增功能，非遗漏还原。

### Phase 5: Buddy 文件回归（6 文件，低风险）

| 文件 | diff 大小 | 差异性质 | 决策 | 状态 |
|------|-----------|---------|------|------|
| `buddy/__init__.py` | +100 -3 | 上游有完整导出列表；src/ 简化仅导出核心函数 | **KEEP** — 是 Phase 0 解耦后有意简化 | ✅ 无需改动 |
| `buddy/companion.py` | +82 -22 | docstring 差异 + 缓存行为变更 | **KEEP** — 差异集中在 docstring 说明性文字 | ✅ 无需改动 |
| `buddy/feature.py` | +8 -2 | 小范围行为修改 | **KEEP** — 二开新增 feature gate 逻辑 | ✅ 无需改动 |
| `buddy/observer.py` | +29 -11 | docstring 差异 + 行为调整 | **KEEP** — 差异集中在 docstring | ✅ 无需改动 |
| `buddy/prompt.py` | +53 -18 | docstring 差异 | **KEEP** — 说明性文字差异 | ✅ 无需改动 |
| `buddy/soul.py` | +18 -6 | 小范围行为修改 | **KEEP** — 二开新增功能 | ✅ 无需改动 |

> **注意**：`buddy/notification.py`, `buddy/sprites.py`, `buddy/types.py` 属 §6.1.1 类别 C（格式差异），已排除。

### Phase 6: Settings 文件回归（4 文件，低风险）

| 文件 | diff 大小 | 差异性质 | 决策 | 状态 |
|------|-----------|---------|------|------|
| `settings/__init__.py` | +5 -7 | F-47 重构删除 `PermissionRule` 和 `validate_permission_rules` 导出 | **KEEP** — F-47 已完成，是预期变更 | ✅ 无需改动 |
| `settings/constants.py` | +2 -9 | 常量修改 | **KEEP** — 二开新增常量 | ✅ 无需改动 |
| `settings/types.py` | — | F-47 类型变更 | **KEEP** — F-47 预期变更 | ✅ 无需改动 |
| `settings/validation.py` | — | F-47 验证逻辑变更 | **KEEP** — F-47 预期变更 | ✅ 无需改动 |

### Phase 7: Provider 文件回归（4 文件，中等风险）

| 文件 | diff 大小 | 差异性质 | 决策 | 状态 |
|------|-----------|---------|------|------|
| `providers/__init__.py` | +43 lines | ADD: 新增 `openai-codex` provider 注册、LiteLLM fallback、`get_provider_class()`、`get_provider_info()`、`should_use_litellm()`、`AVAILABLE_PROVIDERS` 等 | **KEEP** — 二开实现的 multi-provider 支持 | ✅ 无需改动 |
| `providers/base.py` | +2 lines | ADD: `ThinkingChunkCallback` 类型别名 + `ChatResponse.on_thinking_chunk` 参数 | **KEEP** — 二开新增 thinking/reasoning 支持 | ✅ 无需改动 |
| `providers/anthropic_provider.py` | +1 -1 | CHG: `StreamWatchdog(stream)` → `StreamWatchdog(stream, abort_signal=abort_signal)` | **KEEP** — 传递 abort signal 增强正确性 | ✅ 无需改动 |
| `providers/openai_compatible.py` | +5 lines | ADD: `on_thinking_chunk` 回调支持，reasoning content 时调用 | **KEEP** — 二开新增 thinking 支持 | ✅ 无需改动 |

### Phase 8: Transport 文件回归（3 文件，中等风险）

| 文件 | diff 大小 | 差异性质 | 决策 | 状态 |
|------|-----------|---------|------|------|
| `transports/hybrid_transport.py` | +88 -1 | ADD: 大量新增 bridge 集成逻辑 | **KEEP** — 必要的 bridge 集成修改 | ✅ 无需改动 |
| `transports/websocket_transport.py` | +3 formatting | 仅注释格式微调（`\\n` → `\n`、行长度） | **KEEP** — 无语义变化，格式修正 | ✅ 无需改动 |
| `transports/serial_batch_event_uploader.py` | +2 formatting | 仅注释格式微调 | **KEEP** — 无语义变化，格式修正 | ✅ 无需改动 |

### Phase 9: 其余散在文件（高风险）

*待完成 Phase 4-8 后逐模块评审*

| 模块 | 文件数 | 主要差异 | 工作量 |
|------|--------|---------|--------|
| `tui/*` | 12 | PendingAskUser、Ctrl+B、thinking toggle、permission mode 状态栏等 | 2-3天 |
| `query/*` | 3 | 查询引擎修改 | 1天 |
| `coordinator/*` | 2 | 轻量工具集注册 | 0.5天 |
| `tool_system/*` | 4 | 新工具注册、context 修改 | 1天 |
| `command_system/*` | 3 | Buddy 命令注册、builtins 修改 | 0.5天 |
| `agent/session.py` | 1 | SessionStorage 集成 | 0.5天 |
| `config.py` | 1 | 配置项添加/修改 | 0.5天 |
| 其余散在 | 8 | `constants/xml.py`, `permissions/modes.py`, `memdir/memdir.py`, `agent/session.py`, `config.py`, `reference_data/subsystems/buddy.json`, `skills/bundled/loop.py`, `utils/stream_watchdog.py` | 1天 |
