# ClawCodex 已归档进度详情

> 文档路径: `docs/ARCHIVED_PROGRESS.md`
> 源文档: `docs/PROGRESS.md` 第二节 (已完成任务详情)
> 版本: v1.2
> 创建日期: 2026-05-30
> 最后更新: 2026-06-02
> 新增归档: F-13、F-34、F-36、F-38、F-39、F-41、F-42、F-43、F-45、F-47 已实现任务进度归档

---

## 一、开源替代组件 (R-1 ~ R-6)

### R-1: Pydantic-settings 替换配置系统

| 属性 | 值 |
|------|-----|
| 原始实现 | 手动 JSON 管理 (~220 行) |
| 替代方案 | Pydantic-settings |
| 适配器文件 | `src/settings/pydantic_adapter.py` |
| 代码减少 | ~220 行 |
| 优先级 | P0 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-17 |

### R-2: python-frontmatter 替换 frontmatter 解析

| 属性 | 值 |
|------|-----|
| 原始实现 | yaml.safe_load (~80 行) |
| 替代方案 | python-frontmatter |
| 适配器文件 | `src/skills/_frontmatter_adapter.py` |
| 代码减少 | ~80 行 |
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-17 |

### R-3: tree-sitter-bash 替换 Bash AST 解析器

| 属性 | 值 |
|------|-----|
| 原始实现 | 自建 ~1,500 行 |
| 替代方案 | tree-sitter-bash |
| 适配器文件 | `src/permissions/_treesitter_adapter.py` |
| 代码减少 | ~1,400 行 |
| 优先级 | P0 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-17 |

### R-4: GitPython 替换 Git 子进程调用

| 属性 | 值 |
|------|-----|
| 原始实现 | 6 个 subprocess.run() (~200 行) |
| 替代方案 | GitPython |
| 适配器文件 | `src/context_system/_gitpython_adapter.py` |
| 代码减少 | ~200 行 |
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-17 |

### R-5: Pluggy 替换 Hook 系统

| 属性 | 值 |
|------|-----|
| 原始实现 | 自建 ~1,200 行 |
| 替代方案 | Pluggy |
| 适配器文件 | `src/hooks/_pluggy_adapter.py` |
| 代码减少 | ~1,000 行 |
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-17 |

### R-6: Outlines 引入结构化输出

| 属性 | 值 |
|------|-----|
| 原始实现 | json.loads + 手动验证 (~200 行) |
| 替代方案 | Outlines |
| 适配器文件 | `src/agent/_outlines_adapter.py` |
| 代码减少 | ~200 行 |
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-17 |

**总计已减少代码**: ~3,100 行

### R-7: LiteLLM 替换 Provider 层

| 属性 | 值 |
|------|-----|
| 原始实现 | 多个 Provider 类 (~1,630 行) |
| 替代方案 | LiteLLM |
| 适配器文件 | `src/providers/_litellm_adapter.py` + `extensions/providers_ext/litellm_provider.py` |
| 代码减少 | ~1,430 行 |
| 优先级 | P0 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-30 |

**已完成清单**：
- [x] `src/providers/_litellm_adapter.py` 适配器文件已创建
- [x] `extensions/providers_ext/__init__.py` — 扩展包导出
- [x] `extensions/providers_ext/litellm_provider.py` — LiteLLM Provider 实现（含 `_get_litellm_model()` 提取）
- [x] `src/providers/__init__.py` — 工厂函数 `should_use_litellm()` / `create_provider()`
- [x] 集成到 Provider 注册系统
- [x] 移除硬编码的 anthropic/openai/zhipuai 必装依赖（通过 `CLAW_USE_LITELLM` 环境变量切换）
- [x] 端到端测试（49 个目标测试全部通过）
- [x] `src/entrypoints/headless.py` 与 `src/entrypoints/tui.py` 切换到 `create_provider()`
- [x] `pyproject.toml` 包发现包含 `extensions*`

**环境开关**：
- `CLAW_USE_LITELLM=false`（默认）— 使用原始 Provider 类
- `CLAW_USE_LITELLM=1|true|yes|on` — 使用 LiteLLM 统一 Provider

**注意事项**：
- LiteLLM 保留 `BaseProvider` 接口可回退
- 向后兼容：旧导入路径 `from src.providers._litellm_adapter import ...` 继续有效

**累计已减少代码**: ~4,530 行（与开源替代组件累计）

---

## 二、功能模块开发 (F-1 ~ F-32)

### F-1: Orchestrator 自主模式

| 属性 | 值 |
|------|-----|
| 优先级 | P0 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-20 |
| 目标 | 支持 `clawcodex --workflow WORKFLOW.md` 自主运行模式 |

### F-3: MCP 协议扩展

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 功能 | Stdio/HTTP/SSE/WS 传输支持 |

### F-14: 三层解耦架构（Layer Isolation）

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-20 |
| 说明 | upstream/capabilities/features 三层分离，零层违规 |

### F-15: 权限模式切换 (Shift+Tab)

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-21 |
| 功能 | REPL/LiveStatus/TUI 中支持 `default→acceptEdits→plan→bypassPermissions` 循环切换，状态栏显示当前模式，/permission 命令 |

### F-17: 工具系统按需加载（Tool System Extension）

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 功能 | 四种工具模式（bare/default/clawcodex/all），4 bundle 简化设计，bundle 引用前缀 ":"，与上游解耦 |

### F-19: POS to Agent 转化模式

| 属性 | 值 |
|------|-----|
| 优先级 | P2 |
| 状态 | ✅ 已归档 |
| 功能 | 三层映射（POS→Agent、workflow→Skill、SDK→工具），SDK 解析 + Skill 分组 + Agent 构建 + 持久化 |

### F-20: Agent 阶段性进度汇报

| 属性 | 值 |
|------|-----|
| 优先级 | P2 |
| 状态 | ✅ 已归档 |
| 功能 | 三组合方案：检查点触发 + ProgressReportTool + ToolContext.tasks 持久化；PhaseComplete 时双重调用 ProgressReportTool + TaskUpdateTool 更新 metadata |

### F-21: 后台运行 + 恢复同步

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 功能 | Ctrl+B 后台化 + TailFollower 实时同步 + SessionWatcher 多终端感知，补丁 0067-0074 |

### F-23: Bridge Phase 8-11 多 Session Daemon

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-25 |
| 功能 | 多会话桥接器完整实现，bridge_main/repl_bridge/remote_bridge_core/session_runner，Phase 1-11 全部完成 |

### F-24: Agent Loop Consolidation (Stage 4)

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-25 |
| 功能 | 删除 agent_loop.py (537 行)，新增 renderers.py (+257) 和 advisor.py (+125)，重构到 src/query/ |

### F-25: Advisor Token 计数与状态显示

| 属性 | 值 |
|------|-----|
| 优先级 | P2 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-25 |
| 功能 | max_history 100→2000，Provider token 追踪增强，client-side advisor mode |

### F-27: TUI 响应性修复（LLM 超时后 Ctrl+C/ESC 无响应）

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 功能 | StreamWatchdog 超时时触发 AbortSignal；Ctrl+C 先尝试取消 agent 再退出 |

### F-29: TaskInspect/TaskDirectives 工具注册

| 属性 | 值 |
|------|-----|
| 优先级 | P2 |
| 状态 | ✅ 已归档 |
| 提交 | `17e6d5b` feat(tui): 添加权限模式选择器和思考块功能 |
| 功能 | 将 TaskInspectTool 和 TaskDirectivesTool 注册到 ALL_STATIC_TOOLS，实现 Manager Agent 查询/指令 Worker |

### F-30: ProgressReportTool 工具注册

| 属性 | 值 |
|------|-----|
| 优先级 | P2 |
| 状态 | ✅ 已归档 |
| 提交 | `17e6d5b` feat(tui): 添加权限模式选择器和思考块功能 |
| 功能 | 将 ProgressReportTool 注册到 ALL_STATIC_TOOLS，Agent 可调用阶段性进度汇报 |

### F-31: TUI 权限模式选择器

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 提交 | `17e6d5b` feat(tui): 添加权限模式选择器和思考块功能 |
| 功能 | 模态对话框支持 5 种权限模式切换 (default/acceptEdits/plan/bypassPermissions/dontAsk) |

### F-32: 会话恢复浏览器 (Resume Conversation)

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 提交 | `740a2e8` feat(tui): 添加会话恢复浏览器和相关功能 |
| 功能 | 模糊搜索、实时过滤、会话元数据展示，支持 /resume 命令和 --tui --resume 启动选项 |

### F-23: Skills System Extension（技能系统扩展层）

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | ✅ 已归档 |
| 完成日期 | 2026-05-24 |
| 功能 | 仿照 `tool_system_ext` 模式，构建独立的技能系统扩展层，与上游 `skills/loader.py` 解耦 |

**迁移策略完成清单**：

- [x] 阶段 1：创建 `src/skills_ext/` 目录和基础结构
- [x] 阶段 2：迁移 clawcodex 特定路径逻辑到 `skills_ext/paths.py`
- [x] 阶段 3：添加 Bundle 机制和 AgentSkillConfig
- [x] 阶段 4：添加 Hook 机制和回调系统
- [x] 阶段 5：更新 `get_all_skills()` 调用点使用 `SkillRegistryExt`

**实现文件清单**：

| 文件路径 | 优先级 | 状态 |
|---------|--------|------|
| `src/skills_ext/__init__.py` | P0 | ✅ 完成 |
| `src/skills_ext/registry_ext.py` | P0 | ✅ 完成（`SkillRegistryExt` 包装类）|
| `src/skills_ext/bundles.py` | P0 | ✅ 完成（Skill Bundle 定义）|
| `src/skills_ext/agent_config.py` | P1 | ✅ 完成（Agent Skill 配置）|
| `src/skills_ext/paths.py` | P1 | ✅ 完成（clawcodex 特定路径解析）|
| `src/skills_ext/hooks.py` | P2 | ✅ 完成（Skill 生命周期钩子）|
| `src/skills_ext/cache.py` | P2 | ✅ 完成（扩展层缓存管理）|

**与 Tool System Ext 设计对齐**：

| 组件 | Tool System | Skills System |
|------|-------------|---------------|
| 上游核心 | `tool_system/registry.py` | `skills/loader.py` |
| 扩展目录 | `tool_system_ext/` | `skills_ext/` |
| 扩展包装类 | `ToolRegistryExt` | `SkillRegistryExt` |
| Bundle 机制 | `TOOL_BUNDLES` | `SKILL_BUNDLES` |
| Agent 配置 | `AgentToolConfig` | `AgentSkillConfig` |

### F-1.x: Orchestrator 自主模式（F-1 子特性全部完成）

| 子特性 | 描述 | 状态 |
|--------|------|------|
| **F-1.1** | 重试上限保护（`agent.max_retry_attempts=5`） | ✅ 已归档 |
| **F-1.2** | Issue State 前置检查（`tracker.fetch_issue_states_by_ids`） | ✅ 已归档 |
| **F-1.3** | 已有 PR 跳过后续处理（`tracker.find_pull_request`） | ✅ 已归档 |
| **F-1.4** | 本地 Issue 注册表（`IssueRegistry` 持久化 `~/.clawcodex/.../registry.json`） | ✅ 已归档 |
| **F-1.5~F-1.11** | Issue 语义澄清流程（三通道：StatusDashboard / ClarificationQueue / @mention）+ 冲突处理状态机 | ✅ 已归档 |
| **F-1.13** | Orchestrator CLI 运维操作界面（O1-O8 阶段） | ✅ 已归档 |

#### F-1.1 重试上限保护

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_schedule_retry` |
| 新增字段 | `workflow.agent.max_retry_attempts: int = 5` |
| 触发条件 | `attempt > max_retry_attempts` 时跳过调度 |
| 副作用 | 不写入 `completed`（需人工确认后手动关闭 issue） |

#### F-1.2 Issue State 前置检查

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_launch_issue` |
| 检查方式 | `tracker.fetch_issue_states_by_ids([issue.id])`，非 active 跳过 |
| 副作用 | 从 `claimed` 集合移除，不进入 `completed` |

#### F-1.3 已有 PR 跳过后续处理

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_launch_issue` |
| 检查方式 | `tracker.find_pull_request(head_branch, base_branch)` |
| 适用范围 | 仅 RepositoryTrackerAdapter（GitHub/Gitee/GitCode） |
| 副作用 | 标记 completed，重启后不重复处理 |

#### F-1.4 本地 Issue 注册表

| 项 | 值 |
|---|---|
| 文件位置 | `{workspace.root}/.clawcodex_issue_registry.json` |
| 实现文件 | `orchestrator/issue_registry.py:IssueRegistry` |
| 记录字段 | `issue_id / identifier / branch_name / commit_sha / pr_number / pr_url / status / attempt_count / clarification_status / question_history` |
| Status 枚举 | `PENDING → SYNCED → COMPLETED / FAILED / ABANDONED` |

#### F-1.5~F-1.11 Issue 三通道澄清

| 通道 | 实现 | 触发 | 降级 |
|------|------|------|------|
| 通道一 | `StatusDashboard` 交互提示 | 非 headless + 操作员在线 | 5 分钟无操作 |
| 通道二 | `ClarificationQueue` 文件队列 | 异步 CLI 应答 | 30 分钟 |
| 通道三 | `TrackerAdapter.create_clarification_comment()` | @mention Issue 作者 | 72 小时 |

**ClarificationStatus 枚举**：
`NONE → AWAITING_LOCAL → AWAITING_AUTHOR → RESOLVED_LOCAL / RESOLVED_AUTHOR / TIMED_OUT / EXHAUSTED`，并扩展 `DUPLICATE_REJECTED / STALE_REJECTED / CONFLICT_RESOLVED` 处理冲突。

**完成清单（Phase A-G）**：
- [x] Phase A: `ClarificationQueue` 文件队列 + 冲突处理状态机 + 超时告知
- [x] Phase B: StatusDashboard 交互提示组件
- [x] Phase C: `AskIssueAuthor` 工具 + `ClarificationResolver` 三通道降级
- [x] Phase D: CLI `clarify` 子命令
- [x] Phase E: `TrackerAdapter.fetch_issue_comments()` / `create_clarification_comment()` 接口 + GitHub/Gitee/GitCode 实现
- [x] Phase F: IssueRegistry 澄清字段持久化 + PromptBuilder 澄清内容注入
- [x] Phase G: escalation 策略实现（skip / mark_failed / notify）

#### F-1.13 Orchestrator CLI 运维操作界面

**CLI 命令全部完成**：

| 命令 | 状态 |
|------|------|
| `clawcodex orchestrator server start --workflow PATH` | ✅ 完成 |
| `clawcodex orchestrator server status` | ✅ 完成 |
| `clawcodex orchestrator server stop` | ✅ 完成 |
| `clawcodex orchestrator issue list [--status]` | ✅ 完成 |
| `clawcodex orchestrator issue tail --id <id>` | ✅ 完成 |
| `clawcodex orchestrator issue show --id <id>` | ✅ 完成 |
| `clawcodex orchestrator issue pause --id <id>` | ✅ 完成 |
| `clawcodex orchestrator issue resume --id <id>` | ✅ 完成 |
| `clawcodex orchestrator issue stop --id <id>` | ✅ 完成 |
| `clawcodex orchestrator issue inject --id <id> <hint>` | ✅ 完成 |
| `clawcodex orchestrator issue inject --id <id> --list` | ✅ 完成 |
| `clawcodex orchestrator issue inject --id <id> --remove <n>` | ✅ 完成 |
| `clawcodex orchestrator issue clarify --id <id> --answer <text>` | ✅ 完成 |
| `clawcodex orchestrator issue workspace --id <id> --ls` | ✅ 完成 |
| `clawcodex orchestrator issue workspace --id <id> --cat <file>` | ✅ 完成 |
| `clawcodex orchestrator issue workspace --id <id> --edit <file> --with <content>` | ✅ 完成 |
| `clawcodex orchestrator issue takeover --id <id>` | ✅ 完成 |
| `clawcodex orchestrator dashboard --port` | ✅ 完成 |

**实施阶段（O1-O8）**：
- [x] O1: CLI `orchestrator` group 框架（替代旧 `--workflow` 顶层 flag）
- [x] O2: pause/resume/stop + 状态机
- [x] O3: `issue tail` 流式 event stream + StatusDashboard 实时渲染
- [x] O4: `issue inject` Hint 注入（`.operator_hints.md` 机制）
- [x] O5: `issue workspace --ls/--cat/--edit`
- [x] O6: `issue takeover` 终止 + REPL 接管
- [x] O7: `issue clarify` 澄清应答
- [x] O8: Dashboard LiveView 增强（LLM 摘要 + tool calls 推送）

**不兼容变更**：
- `clawcodex --workflow` 已废弃，替换为 `clawcodex orchestrator server start --workflow PATH`
- 原有扁平子命令（`run`、`status`、`issues`、`pause`、`resume`、`stop`、`inject`、`clarify`、`workspace`、`takeover`）已移除
- 统一使用 noun-verb 结构：`server <verb>` / `issue <verb> --id <id>`

---

## 三、进行中任务进度 (F-13)

### F-13: Agent 记忆作用域隔离

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | 🔄 部分归档（核心 API 已就位，prompt 装配层待接入） |
| 规划日期 | 2026-05-19 |
| 归档更新 | 2026-06-01 |

**已完成**:
- [x] 添加 `load_memory_prompts()` 函数到 `src/memdir/memdir.py`
- [x] 添加 `_load_memory_prompt_for_scope()` 和 `_get_memory_path_for_scope()` 辅助函数
- [x] 导出 `load_memory_prompts` 到 `memdir/__init__.py`
- [x] 保持 `load_memory_prompt()` 向后兼容

**待完成**:
- [ ] 更新 `build_full_system_prompt()` 支持 `memory_scopes` 参数
- [ ] 更新 `build_full_system_prompt_blocks()` 支持 `memory_scopes` 参数
- [ ] 更新 `_build_memory_section()` 接受 `memory_scopes` 参数

---

<!-- archived-2026-06-02-progress -->

## 五2026-06-02 已实现任务进度归档

> 归档日期: 2026-06-02
> 来源: 本轮从活动规划/进度文档迁移的已实现条目。

### F-13: Agent 记忆作用域隔离

**状态**: ✅ 完成（2026-06-06）
**优先级**: P1
**规划日期**: 2026-05-19

> 通过 `clawcodex_ext/memory/` 扩展包 + `prompt_assembly.py` forwarding seam 实现。采用 try-import + 静默降级模式，零侵入原有 `memdir/` 模块。
>
> 核心文件：
> - `clawcodex_ext/memory/__init__.py` — 包声明
> - `clawcodex_ext/memory/scope_aware_prompt.py` — 核心 scope 感知 prompt 逻辑
> - `src/context_system/prompt_assembly.py` — 4 处 forwarding seam（`build_full_system_prompt`、`build_full_system_prompt_blocks`、`_build_memory_section` 参数透传 + `build_scope_aware_memory_prompt` 调用）
>
> 验证：✅ 231/231 orchestrator 测试通过（F-39 Sub-A~F 全部落地，含 153 个 F-39 专项用例）| ✅ 371/378 parity 测试通过 | ✅ F-38 E2E 全部通过

---

---

## 五8 F-43 CLI 模型供应商与模型切换

**状态**: ✅ 已完成 (2026-06-02)
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `3.15 CLI 模型供应商与模型切换设计`

### 目标

新增 `clawcodex provider` 与 `clawcodex model` 子命令族，让用户能在 CLI 内**查看、列出、切换**当前生效的 LLM 供应商与模型；并在 REPL/TUI 内部以 `/provider` 与 `/model` 斜杠命令提供运行期热切换。所有新代码落在 `clawcodex_ext/cli/` 下，遵守"src/* 不动"边界；持久化借道 `src.config`，不重写 I/O；错误文案统一英文。

### 子特性

| ID | 名称 | 状态 | 说明 |
|----|------|------|------|
| F-43.a | `provider` 子命令族（list/show/current/use/unset） | ✅ 完成 | fast-path 注册；`use NAME` 持久化默认供应商；`unset` 回退到 `anthropic` |
| F-43.b | `model` 子命令族（list/show/current/use） | ✅ 完成 | fast-path 注册；`use NAME [--provider NAME]` 持久化默认模型并保留现有 `api_key`/`base_url` |
| F-43.c | `ModelRegistry` / `ModelStore` / `Resolver` 核心 | ✅ 完成 | `ModelRegistry` 包装 `PROVIDER_INFO` + 校验；`ModelStore` 通过 `src.config` 持久化；`Resolver` 6 级优先级合并 CLI/env/cli-model/env-model/user/provider-default |
| F-43.d | `RuntimeContext.swap_provider` 热切换 | ✅ 完成 | 重建 provider + tool registry + cron 工具；保留 session / tool_context / workspace_root；更新 `options` 上的 provider/model 引用 |
| F-43.e | REPL `/provider` / `/model` 斜杠命令 | ✅ 完成 | 注册到全局 `CommandRegistry`；`execute_command_sync` 支持 `LocalCommand`；REPL `_sync_from_runtime_context` 同步本地 handle |
| F-43.f | TUI `/provider` / `/model` 斜杠命令 | ✅ 完成 | `TUIOptions.runtime_context` 透传；`AgentBridge.replace_runtime` 替换私有引用；保留 `/models` 作为旧版 `ModelPickerScreen` UI 入口 |
| F-43.g | `--scope project` 拒绝 + 后续规划 | ⏳ 规划中 | 当前 `ModelStore` 在 `scope != "user"` 时直接抛 `UnsupportedScopeError`；project scope 落入 G-1 后续议题 |

### 当前基线（实施前）

- 一次性覆盖：CLI 已支持 `--provider NAME` / `--model NAME`（`parser.py:88-99`），仅对本次调用生效；想换默认需要重跑 `login`
- 持久化入口耦合：仅 `runners.py:120-191` 的 `handle_login` 在配凭证时同步写 `default_model`，没有独立的"切换默认模型"命令
- 没有 `clawcodex model show` 这类查询入口，用户看不到当前生效的 provider / model
- REPL/TUI 运行期无法热切换：`RuntimeContext` 只在启动时构造一次
- 解析优先级在 `RuntimeContext.build` 中硬编码 "CLI flag > default_provider > provider default_model"，无法扩展环境变量 / 项目级 scope

### 实施进度

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | `clawcodex_ext/cli/subcommand_registry.py` 装饰器注册 + dispatch 改查表 | ✅ 完成 |
| 2 | `provider_cmd/commands.py`（list/show/current/use/unset）+ `errors.py` | ✅ 完成 |
| 3 | `model_cmd/registry.py` 包装 `PROVIDER_INFO` + 校验 | ✅ 完成 |
| 4 | `model_cmd/resolver.py` 6 级优先级 + source 标记 | ✅ 完成 |
| 5 | `model_cmd/store.py` 持久化（保留 `api_key`/`base_url`） | ✅ 完成 |
| 6 | `model_cmd/commands.py`（list/show/current/use） | ✅ 完成 |
| 7 | `RuntimeContext.build` 接入 Resolver；新增 `swap_provider` | ✅ 完成 |
| 8 | `CommandContext.runtime_context` seam + `create_command_context` 透传 | ✅ 完成 |
| 9 | `execute_command_sync` 支持注册到全局 registry 的 `LocalCommand` | ✅ 完成 |
| 10 | `clawcodex_ext/cli/runtime_commands.py` `/provider` / `/model` `LocalCommand` | ✅ 完成 |
| 11 | REPL `_sync_from_runtime_context` + `_init_command_system` 注册 | ✅ 完成 |
| 12 | `TUIOptions.runtime_context` + `_run_tui_with_app` 透传 | ✅ 完成 |
| 13 | `ClawCodexTUI._sync_from_runtime_context` + `AgentBridge.replace_runtime` | ✅ 完成 |
| 14 | 错误模型：英文 `UnknownProviderError` / `UnknownModelError` / `ProviderMismatchError` / `UnsupportedScopeError` | ✅ 完成 |
| 15 | 测试：model_registry / resolver / store / provider_model_commands / runtime_switching / slash_commands | ✅ 完成（20/20 通过） |
| 16 | `--scope project` 支持 | ⏳ 规划中（G-1） |

### 验收标准

- ✅ `clawcodex provider list|show [NAME]|current|use NAME|unset` 全部端到端跑通
- ✅ `clawcodex model list [--provider NAME]|show [NAME] [--provider NAME]|current|use NAME [--provider NAME]` 全部端到端跑通
- ✅ 所有新子命令走 fast-path，不触发 `run_pre_action`、不加载 TUI/REPL
- ✅ `--model provider` 不会误路由为子命令（保留原 `argv[1]` 路由行为）
- ✅ 显式 CLI/env model 严格校验；配置中无效 `default_model` 静默回退到 provider 内置默认
- ✅ `provider use` / `model use` 不破坏现有 `api_key` / `base_url`
- ✅ REPL `/provider <name>` / `/model <name>` 触发运行时切换，同一会话内立即生效
- ✅ TUI `/provider <name>` / `/model <name>` 触发运行时切换，`AgentBridge` 私有引用同步
- ✅ `--scope project` 拒绝并返回 `UnsupportedScopeError`
- ✅ 20/20 F-43 单元测试通过；orchestrator 回归 271/271 通过

### 风险与约束

- **写盘并发**：现有 `src.config` 没有文件锁；第一版接受"最后写者赢"，G-1 加 `fcntl` 锁
- **`--model` 与子命令 `model` 同名**：fast-path 只看 `argv[1]`，无歧义；未来 argparse 接管需重新审视
- **环境变量命名**：使用 `CLAWCODEX_PROVIDER` / `CLAWCODEX_MODEL`，与现有 `CLAW_USE_LITELLM` / `CLAUDE_CONFIG_DIR` 一致
- **`login` 仍可写 `default_model`**：保持原行为，文档化"用 `clawcodex model use` 更轻量"
- **REPL/TUI 热切换**：当前 `swap_provider` 重建 provider + tool registry；session / conversation 不重建；如果未来 `swap_provider` 影响 tool registry 行为，需单测覆盖

### 已拟定的设计决定

- **fast-path 注册表**：`clawcodex_ext/cli/subcommand_registry.py` 用 `@register("provider")` / `@register("model")` 装饰器，dispatch 在 `argv[1]` 路由阶段查表，避免 argparse 开销
- **scope 限制**：第一版只支持 `user`（全局）；`--scope project` 接受后立即抛 `UnsupportedScopeError`，避免 silently write 到 `project_root`
- **持久化借道 `src.config`**：`set_default_provider` + `set_api_key(default_model=X)` 保留其它字段；不重写 I/O
- **6 级优先级**：`cli_provider` > `env_provider` > `cli_model` 推断 > `env_model` 推断 > `default_provider` > `default_model`（user config）> `provider default_model`（fallback）
- **source 标记**：每次解析都记录 `provider_source` / `model_source`，`provider current` / `model current` 输出形如 `provider: glm [user]`
- **模块化复用**：`ModelRegistry` / `Resolver` / `Store` 三层分离；CLI 命令、REPL 斜杠命令、TUI 斜杠命令共享同一套核心
- **thin seams**：`src/*` 只追加 `CommandContext.runtime_context` 字段与 `TUIOptions.runtime_context` 字段；不引入项目专属逻辑

### 依赖与协同

- **依赖**：
  - `src.providers.PROVIDER_INFO`（只读）
  - `src.config.{get_default_provider,get_provider_config,set_default_provider,set_api_key}`（只读 + 有限写入）
  - `src.command_system.{types,engine,builtins,registry}` 已有 `LocalCommand` + `CommandContext` 通路
  - `clawcodex_ext.runtime.context.RuntimeContext` 作为下游统一 runtime 工厂
- **协同**：
  - 与 F-42（Orchestrator Shared / Sequential Workspace 策略）正交：F-43 不影响 workspace 行为
  - 与 F-45（tool-call 审计旁路）正交：F-43 切换 provider 后，审计落 `tool_events_path` 仍按 run_id 归档
  - 与 F-47（Permission Settings Schema）正交：F-43 不读 `settings.permissions`
- **先于**：
  - 无（无其它特性阻塞 F-43）
- **后续议题（G-1 / v2.14+）**：
  - `clawcodex provider use --scope project` 落入 `<project>/.clawcodex/config.local.json`
  - `clawcodex model use --scope project` 同上
  - 项目级 scope 的 resolver 优先级插在 user 之前
  - 多窗口并发写盘的 `fcntl` 文件锁

---

---

## 五3 F-34 CLI/TUI Frontend 解耦架构（已完成 Phase 1-3）

**状态**: ✅ 完成（Phase 1: CLI 所有权迁移；Phase 2: RuntimeContext + Frontend 协议；Phase 3: ClawCodexExtTUI 扩展钩子）

### 目标

将 CLI、TUI、Headless 三个入口点共用的 provider/registry/context/session 构造逻辑提取到统一的 `RuntimeContext`，定义 `Frontend` 协议实现前端插件化注册，消除三处重复代码并允许第三方 frontend（如 claude_repl、clawcodex_cli_integration）零修改接入。

### 问题分析

**三入口点各自重复构造**：

```python

# src/cli.py → _run_print_mode()
# src/cli.py → _run_tui_mode()
# src/cli.py → start_repl()

# 每处重复：
provider = get_default_provider()
provider_cfg = get_provider_config(provider)
provider_cls = get_provider_class(provider)
provider = provider_cls(...)
tool_registry = build_default_registry(provider=provider)
tool_context = ToolContext(workspace_root=...)
session = Session.create(provider, model)
```

**argparse 与 frontend 直接耦合**：`--tui` / `--legacy-repl` / `--no-tui` 硬编码，加新前端需改 argparse + dispatch。

**Agent 循环 ×2**：TUI 用 `AgentBridge`，REPL 用内联 agent 循环。

### 架构设计

#### 新增模块：`src/runtime/`

```
 src/runtime/
   ├── __init__.py           # 公共导出
   ├── context.py            # RuntimeOptions + RuntimeContext.build()
   ├── events.py             # TextChunkEvent, ToolUseEvent, ToolResultEvent ...
   ├── engine.py             # AgentEngine（统一 submit/cancel/event）
   ├── protocol.py           # Frontend Protocol
   └── registry.py           # FrontendRegistry（register / get / dispatch）
```

#### 核心数据流

```
CLI args
   ↓
 RuntimeOptions                 ← 从 argparse 提取
   ↓
 RuntimeContext.build()         ← 统一 factory（消除 ×3 重复）
   ↓
 RuntimeContext
   ├→ provider, model
   ├→ tool_registry, tool_context
   ├→ session
   └→ permission_mode, max_turns ...
   ↓
 Frontend.run(ctx)              ← 协议化调用
   ├── repl (prompt_toolkit)
   ├── tui (Textual)
   ├── headless (NDJSON)
   ├── claude-repl (第三方)
   └── cli-integration (第三方)
```

#### Frontend 协议

```python
class Frontend(Protocol):
    name: str
    display_name: str
    description: str

    def run(self, ctx: RuntimeContext) -> int: ...
    # 可选
    def on_start(self, ctx: RuntimeContext) -> None: ...
    def on_finish(self, exit_code: int) -> None: ...
    @classmethod
    def argparse_group(cls, parser: argparse.ArgumentParser) -> None: ...
```

#### 注册 + 调度

```python
# 注册
register("repl", ReplFrontend)
register("tui", TuiFrontend)
register("headless", HeadlessFrontend)

# cli.py 不再有 if-else dispatch
return registry.dispatch(args)
# 或
export CLAWCODEX_FRONTEND=claude-repl
clawcodex  # 自动使用 claude-repl
```

### 组件清单

| 组件 | 路径 | 说明 | 状态 |
|------|------|------|------|
| RuntimeContext | `clawcodex_ext/runtime/context.py` | 统一 factory，消除 3 处重复构造 | ✅ |
| Frontend 协议 | `clawcodex_ext/frontend/protocol.py` | 前端契约，实现即接入 | ✅ |
| FrontendRegistry | `clawcodex_ext/frontend/registry.py` | 插件式注册 + dispatch，单例实例 | ✅ |
| REPLFrontend | `clawcodex_ext/frontend/repl.py` | REPL 前端插件 | ✅ |
| TUIFrontend | `clawcodex_ext/frontend/tui.py` | TUI 前端插件 | ✅ |
| HeadlessFrontend | `clawcodex_ext/frontend/headless.py` | Headless 前端插件 | ✅ |
| ClawCodexExtTUI | `clawcodex_ext/tui/app.py` | 下游 TUI App，8 个扩展钩子 | ✅ |

### 修改文件

| 操作 | 文件 | Phase | 状态 |
|------|------|-------|------|
| 新增 | `clawcodex_ext/cli/parser.py` | Phase 1 | ✅ |
| 新增 | `clawcodex_ext/cli/permissions.py` | Phase 1 | ✅ |
| 新增 | `clawcodex_ext/cli/runners.py` | Phase 1 | ✅ |
| 新增 | `clawcodex_ext/cli/dispatch.py` | Phase 1 | ✅ |
| 新增 | `clawcodex_ext/runtime/context.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/protocol.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/registry.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/repl.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/tui.py` | Phase 2 | ✅ |
| 新增 | `clawcodex_ext/frontend/headless.py` | Phase 2 | ✅ |
| 修改 | `src/cli.py`（改为兼容外观层） | Phase 1 | ✅ |
| 修改 | `clawcodex_ext/cli/main.py`（delegates to dispatch） | Phase 1 | ✅ |
| 修改 | `clawcodex_ext/tui/app.py`（8 个扩展钩子） | Phase 3 | ✅ |

### 实施阶段

#### Phase 1: RuntimeContext（消除重复构造） ✅ 完成

| 里程碑 | 内容 | 工作量 | 状态 |
|--------|------|--------|------|
| P1-M1 | `clawcodex_ext/runtime/context.py`（RuntimeOptions + RuntimeContext.build()） | - | ✅ |
| P1-M2 | `clawcodex_ext/runtime/__init__.py` | - | ✅ |
| P1-M3 | CLI dispatch → 使用 RuntimeContext.build() | - | ✅ |
| P1-M4 | `src/cli.py` → 兼容外观层 | - | ✅ |
| P1-M5 | 验证：三入口点行为不变 | - | ✅ |

**Phase 1 输出**：`src/cli` 转为兼容外观，`clawcodex_ext/cli/` 拥有全部实现。

#### Phase 2: Frontend 协议 + 注册表（插件化） ✅ 完成

| 里程碑 | 内容 | 工作量 | 状态 |
|--------|------|--------|------|
| P2-M1 | `clawcodex_ext/frontend/protocol.py`（Frontend 协议 + FrontendPlugin ABC） | - | ✅ |
| P2-M2 | `clawcodex_ext/frontend/registry.py`（注册表 + dispatch，单例） | - | ✅ |
| P2-M3 | 实现 ReplFrontend / TuiFrontend / HeadlessFrontend | - | ✅ |
| P2-M4 | CLI dispatch → 使用 registry.dispatch() | - | ✅ |
| P2-M5 | 集成测试：TUI + REPL + headless | - | ✅ |

#### Phase 3: ClawCodexExtTUI 扩展钩子 ✅ 完成

| 里程碑 | 内容 | 工作量 | 状态 |
|--------|------|--------|------|
| P3-M1 | `clawcodex_ext/tui/app.py` 8 个 override 钩子 | - | ✅ |
| P3-M2 | 测试覆盖 | - | ✅ |

### 外部 Frontend 接入示例

```bash
# 直接使用已注册的第三方 frontend
clawcodex --frontend claude-repl -p "hello"
clawcodex --frontend cli-integration --tui

# 环境变量默认
export CLAWCODEX_FRONTEND=claude-repl
clawcodex  # 自动使用 claude-repl
```

### 风险与缓解

| 风险 | 缓解 | 状态 |
|------|------|------|
| RuntimeContext.build() 耦合具体 provider/registry | 可抽象 ProviderFactory / RegistryFactory | 后期 |
| AgentEngine 与现有 AgentBridge 行为差异 | 保留 AgentBridge 接口，内部委派，逐步替换 | 后期 |
| 重构破坏已有功能 | 每个 Phase 完成后执行完整集成测试套件 | ✅ 33 tests PASS |

---

*文档更新时间: 2026-05-30*

*版本 v1.7 更新：F-34 Phase 1-3 全部完成。CLI parser/dispatch 迁入 `clawcodex_ext/cli`；RuntimeContext 工厂 + Frontend 协议/注册表完成；`ClawCodexExtTUI` 8 个扩展钩子就绪。*

---

---

## 五4 F-36 LocalTracker 本地 Issue 文档源

**状态**: 📋 设计完成
**优先级**: P1
**依赖**: F-1 Orchestrator 自主模式、TrackerAdapter 协议、IssueRegistry

### 目标

允许用户在本地特定目录中新增 issue 文档，由 Orchestrator 扫描并处理，形成无需外部 Linear/GitHub/Gitee/GitCode 的本地闭环：

```text
本地 issue 文档目录
  ↓ LocalTrackerAdapter 扫描与解析
统一 Issue 模型
  ↓ Orchestrator 领取与运行
workspace.root 下创建 per-issue workspace
  ↓ Agent 修改代码
issue front matter + IssueRegistry 更新状态
```

### 配置设计

```yaml
tracker:
  kind: local
  issues_path: /tmp/clawcodex_local_issues
  active_states:
    - open
    - ready
  terminal_states:
    - completed
    - closed
    - cancelled

workspace:
  root: /tmp/clawcodex_orchestrator_test_workspaces
  repo_clone_url: /mnt/e/Nodel/ExerciseProject/clawcodex
```

`tracker.issues_path` 是任务来源目录；`workspace.root` 是运行工作区目录。二者保持分离，避免用户误以为在 workspace root 下手写 issue 会被自动消费。

### Issue 文档格式

首期采用 Markdown front matter：

```markdown

# 修复 dashboard workspace 解析

当前 dashboard 只读取默认 workspace 或 CLAWCODEX_WORKSPACE_ROOT。
希望它支持从 WORKFLOW.md 的 workspace.root 解析。
```

解析规则：
- front matter 的 `id` / `identifier` 标识本地 issue；缺失时可从文件名派生并在写回时固化。
- 第一个一级标题作为 `title`；剩余 Markdown 正文作为 `description`。
- `state` 在 `active_states` 内才进入候选列表；在 `terminal_states` 内则跳过。
- `branch_name` 可选；缺失时由 identifier 和 title 生成稳定 slug。

### Adapter 行为

LocalTracker 应实现既有 `TrackerAdapter` 协议：

| 接口 | 行为 |
|------|------|
| `fetch_candidate_issues()` | 扫描 `issues_path`，解析 `.md` / `.json`，过滤 active state |
| `fetch_issue_states_by_ids(ids)` | 重新读取本地文件 state，用于 launch 前前置检查 |
| `find_pull_request(head, base)` | 首期默认返回 `None`；若文档中已有 `pr_url` 可返回轻量结果 |
| `ensure_pull_request(...)` | 不创建远程 PR；写回 commit/branch/status 等本地字段 |
| `fetch_issue_comments(...)` | 可选读取 `<id>.comments.ndjson` 或 front matter comments |
| `create_clarification_comment(...)` | 写入本地 comments/clarification 文件，不访问外部服务 |

### 状态与持久化

本地 issue 文档负责表达用户可见任务状态，`IssueRegistry` 继续负责运行映射。建议状态流：

```text
open/ready → running → completed
                  ├── failed
                  └── abandoned
```

写回字段限制在 front matter 内，避免重排或覆盖用户手写正文：

```yaml
state: completed
updated_at: 2026-05-30T12:34:56Z
workspace_path: /tmp/clawcodex_orchestrator_test_workspaces/LOCAL-001
branch_name: local-001-fix-dashboard-workspace
commit_sha: abc123
last_error: null
```

### 并发与幂等

| 风险 | 设计约束 |
|------|----------|
| 多个 orchestrator 同时领取同一文件 | issue 文件旁 lock 或原子 rename；领取前二次读取 state |
| 用户运行中编辑 issue 文档 | 写回时校验 mtime/updated_at；冲突时保留正文并只合并 front matter |
| 重启后重复处理 | `IssueRegistry.is_completed()`、terminal state、commit/pr 字段共同判定 |
| 本地 tracker 与远程 tracker 分叉 | 主流程只依赖 `TrackerAdapter`，不在 Orchestrator 中加入 local 特判 |

### 实施切片

- [x] 配置 schema 增加 `tracker.issues_path`，允许 `tracker.kind: local`。
- [x] 新增 LocalTracker parser/client/adapter，解析 Markdown front matter 到统一 `Issue`。
- [x] 接入 tracker factory 和配置校验。
- [x] 实现状态写回、commit 字段写回和失败字段写回。
- [ ] 补充单元测试：解析、过滤、写回、文件锁、launch 前 state 检查。
- [ ] 增加本地 workflow 示例和 smoke test 文档。

### 本地任务看板 Human Review Gate

**状态**: ✅ 完成
**功能**: issue 处理完成后（git commit）进入 `pending_review` 状态，人类通过 CLI 审批或拒绝。

#### 新增状态

| 状态 | 说明 |
|------|------|
| `pending_review` | Agent 完成 git commit，等待人类 review |

#### 新增 CLI 命令

```bash
# 查看变更概览（包含 agent summary + 文件统计）
clawcodex orchestrator issue diff --id <issue_id>

# 仅显示文件统计
clawcodex orchestrator issue diff --id <issue_id> --stat

# 显示完整 diff
clawcodex orchestrator issue diff --id <issue_id> --full

# 审批通过
clawcodex orchestrator issue review --id <issue_id> --approve --comment "LGTM"

# 审批拒绝（触发 agent 重试）
clawcodex orchestrator issue review --id <issue_id> --reject --feedback "请修复单元测试"
```

#### 流程

```
Agent 完成 → git commit → pending_review
                              ↓
                    人类审查 diff
                              ↓
            ┌─────────────────┴─────────────────┐
            │                               │
       --approve                        --reject
            │                               │
            ↓                               ↓
    completed（工作目录保留）        反馈注入 ClarificationQueue
                                      ↓
                                  agent 重试
```

#### 实现文件

| 文件 | 说明 |
|------|------|
| `extensions/orchestrator/issue_registry.py` | 新增 `PENDING_REVIEW` 状态 + `mark_pending_review()` |
| `extensions/orchestrator/git_sync.py` | 新增 `pending_review` 字段，LocalTracker 提交后设为 True |
| `extensions/orchestrator/orchestrator.py` | 提交后标记 `pending_review`，新增 `pending_review` 集合处理 |
| `extensions/orchestrator/clarification_queue.py` | 新增 `inject_feedback()` 方法 |
| `extensions/orchestrator/cli/issue.py` | 新增 `review` + `diff` 子命令 |

---

*文档更新时间: 2026-06-01*

*版本 v2.2 更新：新增 LocalTracker Human Review Gate，支持 pending_review 状态、review 审批/拒绝命令、diff 变更命令（含 Agent Summary）。*

*版本 v2.3 更新：新增 F-38 Orchestrator 验证与报告闭环。Sub-A 在 `hooks` schema 新增 `pre_commit` / `pre_push` / `post_sync` 三个生命周期点，git_sync 在 commit/push 前后自动跑 verification gate（默认 pytest -x，用户可配 test_command）；Sub-B 新增 `IssueRecord.report_path` 字段、agent_runner 跑完生成 Markdown/JSON 报告、git_sync 据此改写 PR body；Sub-C 抽象 TrackerAdapter 增 `update_pull_request`，并实现 GitCode 客户端把报告回写到 PR；Sub-D 修复 `progress_reporter` 死代码，phase completion 接入 ndjson event log。*

*版本 v2.4 更新：新增 F-39 Orchestrator Issue 重跑入口。三种 label 表达重做意图：`agent:retry`（重置本地状态、关旧 PR、重跑整个 issue）、`agent:follow-up`（保留 PR、叠 commit、对应 F-37 follow-up）、`agent:blocked`（永久跳过）；comment 命令 `/agent retry` / `/agent follow-up` 由原作者或 maintainer 触发并限频；CLI 兜底 `issue retry --id 1 --mode reset`。Sub-A label 解析+意图分发，Sub-B 重置重跑，Sub-C follow-up 叠 commit，Sub-D comment 命令解析，Sub-E CLI 兜底，Sub-F 限频+角色校验。*

*版本 v2.7 更新：F-39 Orchestrator Issue 重跑入口落地（Sub-A~F 全部 ✅）。`tracker.py` 增 `Intent` str-Enum + `intent_from_label_set` 优先级助手 + `Command` enum + `parse_agent_command` 正则 + `CommandIntent` 数据类（携带 author_login/comment_id 用于 Sub-F 角色校验）+ `fetch_issue_command_intent` 默认实现；`issue_registry.py:IssueRecord` 增 `intent/retry_count/last_command/intent_source/command_cursor` 字段 + `mark_intent/clear_intent/reset_for_retry/increment_retry_count/unblock` 方法；`orchestrator.py` 在 `_poll_and_dispatch` 增加 Sub-F 角色校验（`allow_anyone_to_retry`/作者匹配/fail-closed）+ 限频（`max_retries_per_issue=3`）+ 拒绝评论与高优 audit 日志；`cli/issue.py` 增 `retry` 子命令（`--mode {reset,followup,unblock}` + `--force` + `--max-retries` + `--operator` + `--reason`）写 `~/.clawcodex/orchestrator/audit.jsonl`。新增 153 个 F-39 专项单测（`test_orchestrator_f39_{command,retry,retry_cli,followup,ratelimit,followup,retry_cli,intent}.py`），orchestrator 回归 231/231 通过。端到端 10-11 阶段（实际 GitCode/GitHub issue 联动）待真实环境验证。*

---

---
id: LOCAL-001
identifier: LOCAL-001
state: open
priority: 1
branch_name: local-001-fix-dashboard-workspace
labels:
  - orchestrator
---

---

## 五5 F-38 Orchestrator 验证与报告闭环

**状态**: ✅ 完成（2026-06-01，含一轮 E2E + 1 bug 修复）
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.5 验证与报告闭环设计`
**触发场景**: 2026-06-01 在 `chadwweng/AgentSDK` 跑 issue #1 时发现 `tools=0` 仍走 success、agent 凭空返回 SessionComplete 后 commit/push/PR 全程无验证；事后 GitCode 上 PR `#1` 收到 1 条 Git Sync 评论但无 Run Complete 汇总；PR body 是静态模板不含验证/产物信息；reviewer 找不到 diff 与 workspace 路径。

### 目标

把 `extensions/orchestrator` 的 issue 跟踪流程从「commit/push/PR 直通」补全为「commit 验证 → push 验证 → 报告生成 → PR 反馈」的端到端闭环：

1. 修改代码后，系统层强制在 commit/push 之前跑 verification gate，挡住 `tools=0` 这类空跑批。
2. agent 跑完生成结构化报告（盘留 + 持久化）。
3. GitCode 端用报告回写 PR body，并发**一条汇总评论**（取代现在两条独立评论）。
4. 修复 `progress_reporter` 死代码，让阶段进度真正被 orchestrator 主流程消费。

### 子特性

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| Sub-A | Verification Gate | commit/push 前自动跑 `test_command` | `config/schema.py` 的 `HooksConfig` 新增 `pre_commit` / `pre_push` / `post_sync`；`git_sync.py` 调 `run_pre_commit_hook` / `run_pre_push_hook`；verification 失败时不 commit/push，将 issue 标记为 `verification_failed` |
| Sub-B | 结构化报告 | agent 跑完写 Markdown/JSON 报告 | `issue_registry.py` 的 `IssueRecord` 新增 `report_path` 字段；`agent_runner.py` 跑完调 `report_writer.write(session, workspace)` 生成报告；`git_sync._build_pr_body` 改为模板插值报告字段（commit、diff stat、verification 结果、turns/tools 计数） |
| Sub-C | PR 报告回写 | 把报告作为 PR 描述回写到 GitCode | `tracker.py` 抽象基类新增 `update_pull_request(pr_number, body=None, state=None)`；`repo_tracker/client.py` 实现 GitCode 客户端的 `PATCH /repos/{owner}/{repo}/pulls/{id}`；`git_sync.sync()` 末尾在 PR 开完后调一次 `update_pull_request(body=...)` 把 Sub-B 的报告写入 PR body；将原 `_post_run_comment` + `_comment_sync_result` 两条评论合并为一条带报告链接的汇总评论 |
| Sub-D | ProgressReporter 接入 | 修死代码 | `orchestrator.py:329-336` 调 `agent_runner.run(...)` 时把 `progress_reporter` 显式传参；`progress_reporter.ProgressReporter` 把 phase completion 写入 `event_log_dir/1.ndjson`（与 agent_runner 现有 ndjson 通道合并），`issue tail` 可消费 |

### 当前基线

| 能力 | 当前状态 | 说明 |
|------|----------|------|
| commit 前自动跑测试 | ❌ 缺失 | `agent_runner.py` 跑完 LLM 直接 SessionComplete；`git_sync.py` 只 `git add/commit/push`；`workflow.md:110` 写「Run the existing test suite」仅是 LLM prompt 文本，系统不强制 |
| push 前自动跑测试 | ❌ 缺失 | 同上 |
| `HooksConfig` 生命周期点 | ⚠️ 不完整 | 现有 `after_create` / `before_run` / `after_run` / `before_remove` 四点；`config/schema.py:188-193`；缺 `pre_commit` / `pre_push` / `post_sync` |
| AgentConfig / CodexConfig verification 字段 | ❌ 缺失 | `config/schema.py:157-184` 无 `test_command` / `build_command` / `lint_command` |
| `IssueRecord` 报告字段 | ❌ 缺失 | `issue_registry.py:36-58` 字段为 `issue_id/branch_name/commit_sha/pr_number/pr_url/base_branch/status/attempt_count` + 几个 clarification 字段，无 `report_path` / `verification_result` / `test_output` |
| 结构化报告文件 | ❌ 缺失 | `agent_runner.py:440-486` 只写 `.event_logs/{id}.ndjson`（stream events）；`git_sync.py` 不写报告 |
| PR body 动态改写 | ❌ 缺失 | `git_sync.py:264-282 _build_pr_body` 写死静态文本；代码库 0 处 `update_pull_request` / `edit_pull_request` 调用；`tracker.py` 抽象基类未声明该方法 |
| 单条汇总评论 | ❌ 缺失 | `_post_run_comment` (agent_runner) + `_comment_sync_result` (git_sync) 是两条独立评论，reviewer 要跳两处 |
| `progress_reporter` 接入 | ❌ 死代码 | `orchestrator.py:329-336` 调 `agent_runner.run(...)` 不传 `progress_reporter`；模块仅 4 处引用且都是构造参数；PhaseComplete 写到 `ToolContext.tasks.metadata` 后无人读 |

### 实施进度

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | `config/schema.py` 扩展 `HooksConfig` 增 `pre_commit` / `pre_push` / `post_sync` 三点 + `AgentConfig` 增 `test_command` / `build_command` / `lint_command`（默认可空） | A | ✅ 完成 |
| 2 | `extensions/orchestrator/git_sync.py` 在 `git commit` 前调 `run_pre_commit_hook`、在 `git push` 前调 `run_pre_push_verification`；失败时抛 `VerificationFailed` / `HookFailedError`，orchestrator 捕获后 issue 标 `verification_failed` 不 push | A | ✅ 完成 |
| 3 | `orchestrator.py` 在 git_sync.sync() 末尾 `finally` 块里调 `run_post_sync_hook(session)`，并把 verification 状态写入 `IssueRecord` | A | ✅ 完成 |
| 4 | `issue_registry.py` 的 `IssueRecord` 新增 `report_path: str \| None` / `verification_status: str \| None` / `verification_output: str \| None` / `summary_comment_id: str \| None` 字段，旧 entry 加载兼容 | B | ✅ 完成 |
| 5 | 新增 `extensions/orchestrator/report_writer.py`，`write(...)` 同步写 `workspace/.reports/{run_id}.md` + `~/.clawcodex/reports/{tracker}/{owner}/{repo}/{issue_id}/{run_id}.{md,json}`，markdown 不含自身路径 | B | ✅ 完成 |
| 6 | `agent_runner.py` SessionComplete 时计算 `run_id = run-{attempt:02d}-{UTC_ts}`，F-39 follow-up 用 `run-N-followup-M-{UTC_ts}`；agent_runner 立刻发 `⏳` placeholder 评论并把 `comment_id` 写回 `session.summary_comment_id` | B/C | ✅ 完成 |
| 7 | `git_sync._build_pr_body` 改模板插值，插入 issue 摘要、branch、commit、diff stat、verification 状态、报告链接（`~/.clawcodex/reports/...` 持久化路径）；审计用 `<!-- metadata: report_path=... -->` HTML 注释单独存 | B | ✅ 完成 |
| 8 | `tracker.py:TrackerAdapter` 增抽象 `update_pull_request(pr_number, *, body=None, state=None) -> PullRequestRef \| None` 与 `update_comment(issue_id, comment_id, body) -> Comment \| None` | C | ✅ 完成 |
| 9 | `repo_tracker/client.py` 增 `RepositoryIssueClient.update_pull_request`（GitHub/Gitee/GitCode PATCH `/repos/{o}/{r}/pulls/{id}`）与 `update_comment`（PATCH `/repos/{o}/{r}/issues/comments/{id}`），Linear GraphQL `updateIssueComment` | C | ✅ 完成 |
| 10 | `git_sync.py` `ensure_pull_request` 拿到 `pr.number` 后调 `tracker.update_pull_request(body=...)` 把 Sub-B 报告回写 PR | C | ✅ 完成 |
| 11 | 合并 `agent_runner._post_run_comment` + `git_sync._comment_sync_result` 为单条 `## ClawCodex Run Summary` 汇总评论（git_sync.sync 末尾调 `tracker.update_comment(session.summary_comment_id, body=完整汇总)`，无 `summary_comment_id` 时 fallback 到 `create_comment`） | C | ✅ 完成 |
| 12 | `orchestrator.py:_run_issue` 显式构造共享 `ProgressReporter(ToolContext(workspace_root=workspace_root))` 并传入 `agent_runner.run(...)` | D | ✅ 完成 |
| 13 | `progress_reporter.py` 在 `PhaseComplete` 时写 `event_log_dir/{id}.ndjson` 追加 `{"type": "phase_complete", "phase": N}` 行（与 agent_runner 既有 schema 兼容，新增字段不替换） | D | ✅ 完成 |
| 14 | 单元测试：`schema.HooksConfig/AgentConfig` 解析新字段；`git_sync` 在 pre_push 失败时不 push；`report_writer` 产物包含必须字段；`update_pull_request` / `update_comment` mock 测被调一次；`LocalTracker.update_comment` 原子替换不残留 `.tmp` | A/B/C | ✅ 完成 |
| 15 | 端到端测试：临时 bare origin + WorkspaceManager 跑批，断言 (1) 报告文件存在 (2) PR body 含报告链接 (3) issue 收到单条汇总评论（含报告/verification/branch/commit） (4) `pre_push` 失败时 PR 不存在 (5) `pre_commit` 改文件后 commit 被 amend | A/B/C/D | ✅ 完成 |

### 验收标准

- agent 一次工具都没调（`tools=0`）时，verification gate 拦截 push，PR 不被创建，issue 标 `verification_failed`。
- `test_command` 默认值为空时该步骤跳过（不破坏已有无测试项目）。
- agent 跑完 issue registry 的 `report_path` 指向一个真实存在的文件；该文件包含 issue 摘要、commit SHA、verification 状态、diff stat。
- PR body 含「Issue / Branch / Commit / Verification / Report」五段，verification 段落根据结果渲染 ✅/❌。
- PR 开完后 issue 收到**一条**汇总评论（合并原 Run Complete + Git Sync 两条）。
- 完整代码库 0 处对 `tracker.update_pull_request` 之外的非 CRUD PR API 调用（保留可审计性）。
- `progress_reporter.ProgressReporter` 在主流程被构造；`issue tail --id N` 能看到 `{"type": "phase", ...}` 事件。

### 风险与约束

- verification gate 默认开在 `pre_push`，失败 = 不 push。需在 `workflow.md` 文档里强调，否则用户以为 push 失败是网络问题。
- `test_command` 跑长任务会拖慢 `max_turns=20` 的 issue 跑批，需提供 `verification.timeout_ms` 配置（默认 600s）。
- GitCode `PATCH /pulls` 的 body / state 字段是否被支持需先打一个 dry-run 验证；不支持则回退为「把报告写到 `workspace/.reports/{id}.md` + 在汇总评论里贴报告全文」。
- `_post_run_comment` 与 `_comment_sync_result` 合并时若平台限流，单条评论可能太长，需提供 `summary.max_comment_chars` 截断。
- `progress_reporter` 接入需不破坏 `event_log_dir/1.ndjson` 现有 schema，扩展字段而非替换。
- 与 F-37 的 PR review follow-up 闭环保持兼容：Sub-C 的 `update_pull_request` 应是 F-37 阶段 5/7（同 PR 分支 follow-up）的基础能力，先于 F-37 落地。

### 已拟定的设计决定（2026-06-01 设计稿审阅产出）

设计稿 7 个 Open Questions 的拟定方案。详细版见 `docs/FEATURE_PLAN.md` → `3.1.5 验证与报告闭环设计` → `拟定的设计决定`。实施时按依赖顺序落地，每完成一组更新本节。

| # | 问题 | 拟定方案 | 涉及 Sub |
|---|------|---------|---------|
| 1 | `ProgressReporter` 接口与设计目标错位（绑死 `ToolContext`） | 拆成「翻译层 + 通道层」：新增 `ProgressSink` 协议（`ToolContextSink` / `NdjsonSink` / `CompositeSink`），`ProgressReporter.__init__` 改收 `sinks: list[ProgressSink]`；orchestrator 根据 `workflow.observability.progress_sinks` 显式构造 | D |
| 2 | Hook 执行上下文未约定 | 固化「Hook Env Contract」表：`ISSUE_ID/IDENTIFIER/BRANCH` 必传；`pre_commit`/`pre_push`/`post_sync` 各自加 `BRANCH_NAME/COMMIT_SHA/PR_NUMBER/PR_URL/VERIFICATION_STATUS`；抽 `_run_named_hook` helper 统一 cwd/env/timeout | A |
| 3 | Hook 失败 vs 测试失败语义重叠 | 配置分层：verification 字段（typed）失败 = `VerificationFailed` + 标 `VERIFICATION_FAILED` 状态；hook 字段（opaque）失败 = `HookFailedError` + 走 `FAILED`/retry；新增 `IssueStatus.VERIFICATION_FAILED` 枚举值与 `mark_verification_failed` 方法；新增 `last_hook_error` 字段 | A |
| 4 | Hook 修改文件的副作用 | verification 字段默认只读（`VerificationCommand(cmd, write=False)`），写工作区后记 WARN；`pre_commit` hook 改文件后 git_sync 自动 `git add -A && git commit --amend`；`pre_push`/`post_sync` 改工作区直接报错 | A |
| 5 | 报告文件生命周期（cleanup 会清掉） | 双层存储：`report_writer.write()` 同步写 `workspace/.reports/{id}.md`（瞬态）+ `~/.clawcodex/reports/{tracker}/{owner}/{repo}/{issue_id}/run-{N}-{ts}.md`（持久）；复用 `before_remove` 钩子作为双写失败的 fallback 复制口；`workflow.reports.retention_days=90` | B |
| 6 | 「报告路径」字段循环引用 | 报告文件内部**不写自身路径**，只写摘要/计数/verification/commit/diff/run_id；路径由 PR body 与汇总评论外部引用；若审计需要可以 HTML 注释 `<!-- metadata: report_path -->` 单独存 | B/C |
| 7 | 配置示例具有误导性（`echo` 永远成功） | 替换为四组示例：典型 Python 项目（`pytest -x -q` + `ruff check .`）/ 无测试项目（空 = 跳过）/ hook 副作用（`black . && isort .` + auto amend）/ 显式 no-op（`"true"`）；所有字段留空等价于 3.1.5 之前行为 | A/B/C |

**实施顺序建议**：1 → (2 + 3) → 4 → 5 → 6 → 7。

### 第二轮审阅补遗（2026-06-01）

针对首轮 7 项之外 5 个未决项的补遗，详细版见 `docs/FEATURE_PLAN.md` → `3.1.5 验证与报告闭环设计（F-38）` → `第二轮审阅补遗（2026-06-01）`。

| # | 项 | 补遗内容 | 涉及 Sub |
|---|----|---------|---------|
| 1 | IssueStatus 枚举 | 新增 `VERIFICATION_FAILED` 枚举值 + `mark_verification_failed()` 方法 + `TERMINAL_STATUSES` 冻结集合（含 `COMPLETED/FAILED/ABANDONED/VERIFICATION_FAILED`）统一终态判断；F-39 `agent:retry` 触发时把 `VERIFICATION_FAILED` 也重置回 `PENDING` | A |
| 2 | 汇总评论时序（Option A） | `agent_runner.SessionComplete` 立刻发 placeholder 评论（含 `⏳ This summary is being prepared...`），把 comment_id 存到 `AgentSession.summary_comment_id`；git_sync.sync 末尾调 `tracker.update_comment(summary_comment_id, body=完整汇总)`；新增 `TrackerAdapter.update_comment` 抽象 + 4 平台实现（GitHub/Gitee/GitCode `PATCH /repos/{o}/{r}/issues/comments/{id}`，Linear GraphQL `updateIssueComment`，LocalTracker ndjson 临时文件 + `os.replace` 原子替换） | C |
| 3 | 重跑 run_id | `run_id` 由 agent_runner 显式构造并传入 `report_writer.write(session, workspace, run_id=...)`；格式 `run-{attempt_count:02d}-{UTC_ts}`；F-39 follow-up 用 `run-N-followup-M-{UTC_ts}` 避免冲突；持久化路径 `~/.clawcodex/reports/{tracker}/{owner}/{repo}/{issue_id}/{run_id}.{md,json}` | B |
| 4 | 文档 ID 一致性 | FEATURE_PLAN.md 节标题已加 `(F-38)` 标识；PROGRESS.md 「规划文档」列已写 `docs/FEATURE_PLAN.md → 3.1.5 验证与报告闭环设计`；设计文档（按主题编排）与跟踪文档（按 F-N 索引）的正常分层，不需要合并 ID 系统 | 文档 |
| 5 | test_command 触发器归属 | `agent.test_command` / `build_command` / `lint_command` 只在 pre_push 阶段跑（不在 pre_commit）；`hooks.pre_commit` 保留 Git 术语不重命名，跑改文件类副作用（formatter + auto amend）；`workflow.md` 注释里说明「pre_commit 改文件 / verification 跑 pre_push」分工；pre_commit amend 失败 → 抛 `HookFailedError("pre_commit", "amend failed: <reason>")` 标 FAILED | A |

**合并实施顺序**：首轮 1 → (首轮 2 + 3 + 补遗 1) → (首轮 4 + 补遗 5) → (首轮 5 + 补遗 3) → 补遗 2 → 首轮 6 → 7。

### 依赖与协同

- **依赖 F-1**：F-38 全部 Sub 都在 Orchestrator 主流程内，依赖现有 `git_sync` / `agent_runner` / `issue_registry`。
- **先于 F-37**：F-37 阶段 5 需要的「同 PR 分支 follow-up 修改」依赖 F-38 Sub-C 的 `update_pull_request` 能力。
- **与 F-36 兼容**：LocalTracker 走 `pending_review` 路径不创建 PR，F-38 Sub-C 在该路径下应跳过 PR body 改写。
- **不破坏 `progress_reporter` 现有 4 个引用点**：Sub-D 接入后，单元测试覆盖原参数接口。

---

---

## 五6 F-39 Orchestrator Issue 重跑入口（label + comment 命令双通道）

**状态**: ✅ 完成（Sub-A~F 全部落地；E2E 阶段 10-11 待真实环境验证）
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.6 Issue 重跑入口设计`
**触发场景**: 2026-06-01 在 `chadwweng/AgentSDK` 跑完 issue #1 后用户想「让 agent 重做」或「在同一 PR 上再改一版」,但当前 orchestrator 4 层防御(内存 `completed` set / IssueRegistry `is_completed` / `has_pr` / `find_pull_request`)只支持「PR 存在 = 已处理」,不支持「关 PR = 重做」语义。用户被迫改 registry.json 或重启 daemon,体验差且易污染主流程。

### 目标

在 `extensions/orchestrator` 引入「重做意图」显式表达通道,与现有 4 层防御并存而非替换:

1. **三种 label 表达重做意图**,orchestrator 轮询时按 label 决定走「重置重跑」还是「同 PR 叠 commit」还是「永久跳过」。
2. **comment 命令**作为 label 的实时替代(原作者/maintainer 触发),适合自动化流水线。
3. **CLI 兜底命令**作为本地调试 / label 不便时的紧急入口。
4. 与 F-37(PR 检视意见自动修复)、F-38(报告回写)对齐,提供「同 PR branch follow-up」入口。

### 三种重做意图的语义矩阵

| Label / 命令 | 语义 | 对本地 IssueRecord | 对远程 PR | 对远程 issue | 对 agent run |
|---|---|---|---|---|---|
| `agent:retry` | 重置 + 重跑整个 issue | 清空 `status` → `pending`,删 `commit_sha` / `pr_number` / `pr_url` / `report_path` | 关闭旧 PR(状态 `closed` `not merged`) | 加 `agent:retry` 自检注释(可选) | 新 workspace、新 agent run |
| `agent:follow-up` | 保留 PR,在同 PR branch 叠 commit | `status` 保持 `completed`,`pr_number` 不变,`attempt_count++` | 不动;`update_pull_request` 走 F-38 Sub-C 入口追加 commit | 不动 | 同 workspace 同 branch,prompt 强调「只处理 follow-up」 |
| `agent:blocked` | 永久跳过该 issue | `status` 写 `abandoned` | 不动 | 加 `agent:blocked` 自检注释 | 永不 launch |

`agent:retry` 与 `agent:follow-up` 互斥:同一 issue 上若同时存在两个 label,以更保守的 `agent:follow-up` 为准(保留 PR 改动证据);若同时存在 `agent:blocked`,直接视为「永久跳过」。

### 现状基线(2026-06-01)

| 能力 | 当前状态 | 说明 |
|---|---|---|
| 内存 `completed` set | ✅ 已实现 | `orchestrator.py:200` 拦截;只在进程生命周期内有效 |
| `IssueRegistry.is_completed` / `has_pr` | ✅ 已实现 | `orchestrator.py:205` 拦截;持久化到 `.clawcodex_issue_registry.json`,daemon 重启不丢 |
| `tracker.find_pull_request` 远程校验 | ✅ 已实现 | `orchestrator.py:265-281` 拦截;只看 PR 是否存在,不看 PR state(open/closed/merged) |
| Tracker 端 issue state 前置检查 | ✅ 已实现 | `orchestrator.py:247-262`;`active_states` 命中才 launch |
| Label 读取 | ❌ 缺失 | `RepositoryIssueClient.fetch_candidate_issues` 未把 labels 透传到 `Issue.labels` 之外的使用方;无 label 驱动的 dispatch 逻辑 |
| Comment 命令解析 | ❌ 缺失 | `RepositoryIssueClient.fetch_new_comments_since` 已实现但 orchestrator 未消费 |
| 重置 API | ❌ 缺失 | `IssueRegistry` 无 `reset_for_retry(issue_id)` / `mark_followup(issue_id)` 方法 |
| 远程 PR 关闭能力 | ❌ 缺失 | `tracker.py:TrackerAdapter` 无 `close_pull_request`;`repo_tracker/client.py` 0 处 `PATCH /pulls` 调用 |
| CLI 兜底命令 | ❌ 缺失 | `cli/issue.py` 有 `review` / `diff` / `inject` 但无 `retry` |
| 限频 / 角色校验 | ❌ 缺失 | comment 命令无 anti-replay / author 校验,易被 LLM 自触发 |

### 子特性拆分

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| A | Label 解析 + 意图分发 | 把 label 映射到「重置/follow-up/跳过」三态 | `tracker.py:TrackerAdapter` 增 `extract_intent_from_labels(labels) -> Intent` 抽象;`repo_tracker/client.py:RepositoryIssueClient.fetch_candidate_issues` 在返回前用 `_OPEN_STATE_ALIASES` 之外的「intent label」识别;`issue_registry.py:IssueRecord` 新增 `intent: Literal["none","retry","followup","blocked"]` + `retry_count: int`;`orchestrator.py:_poll_and_dispatch` 在 `has_pr` 判断之前先看 intent |
| B | 重置重跑 (`agent:retry`) | 清空本地状态 + 关闭远程 PR | 新增 `IssueRegistry.reset_for_retry(issue_id)` 方法;`tracker.py:TrackerAdapter.close_pull_request(pr_number) -> bool` 抽象;`repo_tracker/client.py:RepositoryIssueClient.close_pull_request` 实现 `PATCH /repos/{owner}/{repo}/pulls/{id}?state=closed`;`orchestrator.py` 在 launch 前若 intent=retry,先调 close_pull_request 再 launch |
| C | Follow-up 叠 commit (`agent:follow-up`) | 不开新 PR,复用原 branch | `orchestrator.py` 检测 intent=followup 时,跳过 workspace 创建(复用现有 branch),用上次 run 的报告作为上下文;`git_sync.py:GitSyncService.sync` 加 `mode="followup"` 分支,只 `git commit` + `git push`,不创建新 PR;`IssueRecord.attempt_count++`;依赖 F-38 Sub-C 写新 commit 到 PR body(等 F-38 落地) |
| D | Comment 命令解析 | `/agent retry` `/agent follow-up` 触发 | `tracker.py:TrackerAdapter` 增 `fetch_issue_command_intent(issue_id, since_comment_id) -> Intent | None`;`repo_tracker/client.py` 复用 `fetch_new_comments_since` 拉新评论,正则匹配 `^/agent\s+(retry|follow-up|unblock)`;orchestrator 在 launch 前调用,合并 label 意图与 command 意图(以更保守者为准);comment 触发后由 orchestrator 发 bot 确认评论 `## ClawCodex: 已受理 ${command},下一轮 poll 开始执行` |
| E | CLI 兜底命令 | `issue retry` 提供本地入口 | `cli/issue.py` 增 `add_retry_parser` 与 `_run_retry(registry, args)`;支持 `--mode {reset,followup,unblock}` + `--id` + `--reason`;`IssueRegistry` 增 `unblock(issue_id)` 方法(把 `abandoned` 状态回滚);命令发一条本地 audit 日志 `~/.clawcodex/orchestrator/audit.jsonl` 记录 `{ts, operator, issue_id, mode, reason}` 便于追溯 |
| F | 限频 + 角色校验 | 防滥用 | comment 命令默认要求「issue 作者」或「仓库 maintainer」才能触发;`IssueRecord.retry_count >= max_retries_per_issue(默认 3)` 时即使加 label 也拒绝重置(写一条 `agent:retry-rejected` label + 评论说明);`audit.jsonl` 记 limit 触发 |

### 实施进度

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | `tracker.py:TrackerAdapter` 增 `extract_intent_from_labels` / `close_pull_request` / `fetch_issue_command_intent` 三个抽象 | A/B/D | ✅ 完成 |
| 2 | `repo_tracker/client.py:RepositoryIssueClient` 实现上述三个方法(GitCode 优先,GitHub/Gitee 列 TODO) | A/B/D | ✅ 完成 |
| 3 | `issue_registry.py:IssueRecord` 增 `intent` / `retry_count` / `last_command` / `intent_source` / `command_cursor` 字段;新增 `mark_intent` / `clear_intent` / `reset_for_retry` / `increment_retry_count` / `unblock` 方法 | A/B/E | ✅ 完成 |
| 4 | `orchestrator.py:_poll_and_dispatch` 增 intent 前置判断:label 解析 + comment 命令解析 + 合并;launch 路径根据 intent 分流(reset / followup / skip) | A/C/D | ✅ 完成 |
| 5 | `orchestrator.py` 在 intent=retry 时调 `close_pull_request(pr_number)`,再 launch 新 run | B | ✅ 完成 |
| 6 | `git_sync.py:GitSyncService.sync` 加 `mode="followup"` 参数,走「只 commit/push,不开 PR」分支;orchestrator 把 `session.run_kind="agent_followup"` 走 `_prepare_intent_session` 复用 branch | C | ✅ 完成 |
| 7 | `cli/issue.py` 增 `retry` 子命令,实现 `_run_retry` + `_append_audit_log` + `_resolve_operator`;`~/.clawcodex/orchestrator/audit.jsonl` 写本地审计 | E | ✅ 完成 |
| 8 | `config/schema.py:AgentConfig` 增 `max_retries_per_issue=3` + `allow_anyone_to_retry=False`;`orchestrator.py` 实现 `_is_command_author_eligible` (fail-closed) + `_check_retry_rate_limit` + `_reject_unauthorized_command` + `_post_retry_rejection` + `_log_audit_event`;CLI `--force` 旁路并写高优 audit | F | ✅ 完成 |
| 9 | 单元测试 153 个(`tests/test_orchestrator_f39_{command,retry,followup,intent,retry_cli,ratelimit}.py`);orchestrator 回归 231/231 通过 | A/B/C/D/E/F | ✅ 完成 |
| 10 | 端到端:在 issue #1 上加 `agent:retry` label → 60s 内观察 daemon 日志确认走 retry 路径 → issue 重新 running → 完成后 PR 编号变化 | A/B/C | 📋 待真实环境验证 |
| 11 | 端到端:在 issue #1 上加 `agent:follow-up` label → daemon 检测到后不关 PR,在同 branch 叠 commit → PR 编号不变,commit 数 +1 | C | 📋 待真实环境验证 |

### 实际落地（2026-06-01）

| 维度 | 改动 |
|---|---|
| **核心抽象** | `extensions/orchestrator/tracker.py` 新增 `Intent` str-Enum（NONE/RETRY/FOLLOWUP/BLOCKED）、`Command` enum（RETRY/FOLLOWUP/UNBLOCK）、`CommandIntent` 数据类（带 author_login/comment_id/comment_body）、`DEFAULT_INTENT_LABELS`、`intent_from_label_set()`、`parse_agent_command()`、`command_to_intent()`、`merge_intents()`、`extract_intent_from_labels()` 默认实现、`close_pull_request()` 默认实现、`fetch_issue_command_intent()` 默认实现（返回 `CommandIntent \| None`） |
| **适配器** | `extensions/orchestrator/repo_tracker/{client,adapter}.py` 增 `close_pull_request`（`PATCH /repos/{owner}/{repo}/pulls/{number}` + `state=closed`，422 视为成功）+ `intent_labels` 参数 + `fetch_issue_command_intent` 委派到 `fetch_new_comments_since`；`local_tracker/adapter.py` 增 `close_pull_request` no-op + `fetch_issue_command_intent` 扫描本地 `*.comments.ndjson` + `intent_labels` 参数；`linear/adapter.py` 增 `intent_labels` 参数 + `extract_intent_from_labels` |
| **状态机** | `extensions/orchestrator/issue_registry.py:IssueRecord` 增 5 个字段（`intent/retry_count/last_command/intent_source/command_cursor`）+ 5 个方法（`mark_intent/clear_intent/reset_for_retry/increment_retry_count/unblock`）；`_load()` 过滤未知字段保证老 JSON 兼容；`unblock()` 把 ABANDONED 滚回 PENDING 且清 intent，`retry_count` 保留以便限频继续生效 |
| **调度逻辑** | `extensions/orchestrator/orchestrator.py` `_poll_and_dispatch` 增 `_resolve_intent()`（label+command 合并）、`_resolve_command_intent()`、`_post_command_acknowledgement()`（"已受理"评论 + cursor）、`_prepare_intent_reset()`（Sub-B 关 PR + reset）、`_prepare_intent_session()`（Sub-C 设 `run_kind=agent_followup` + branch 复用）、`_is_command_author_eligible()`（Sub-F fail-closed）、`_reject_unauthorized_command()`（Sub-F 拒绝评论 + audit）、`_check_retry_rate_limit()`（Sub-F 限频）、`_post_retry_rejection()`（Sub-F 拒绝评论 + 标签尝试）、`_log_audit_event()`（daemon-side 审计）。UNBLOCK 命令触发时把 ABANDONED 回滚到 PENDING 并清 intent |
| **Git 同步** | `extensions/orchestrator/git_sync.py:GitSyncService.sync()` 新增 `mode: str = "default"` 参数；`mode="followup"` 顶部短路要求 `session.pull_request` 存在（fail-fast），后续走现有 followup_pr 分支只 commit/push 不开新 PR |
| **配置** | `extensions/orchestrator/config/schema.py:AgentConfig` 新增 `max_retries_per_issue: int = 3` + `allow_anyone_to_retry: bool = False`；`WorkflowConfig.from_dict()` 加载两个新字段 |
| **CLI** | `extensions/orchestrator/cli/issue.py` 新增 `retry` 子命令（`--mode {reset,followup,unblock}` + `--id` + `--reason` + `--force` + `--max-retries` + `--operator` + `--workspace/--workflow`）+ `_run_retry()` + `_append_audit_log()`（写 `~/.clawcodex/orchestrator/audit.jsonl`）+ `_resolve_operator()`（`$USER` / `os.getlogin()` / "unknown"）；dispatch 在 `run()` 末尾 |
| **测试** | 新增 6 个测试文件 153 个用例：`test_orchestrator_f39_{intent,retry,followup,command,retry_cli,ratelimit}.py`；`Intent`/`Command`/`CommandIntent` 单元覆盖、`IssueRecord` JSON round-trip + 老 schema 兼容、`_run_retry` 三模式（reset/followup/unblock）+ `--force` 旁路 + `--max-retries` 覆盖 + rate-limit 拒绝（rc=3 不动 state）、`orchestrator._is_command_author_eligible` 7 种场景（allow_anyone/None/false/空/author 匹配/other/no record）、`_check_retry_rate_limit` at-limit 拒 + force 放、`_reject_unauthorized_command` 评论 + audit |
| **回归** | orchestrator 套件 231/231 通过（含 78 个原有用例 + 153 个 F-39 新增）；`tests/manual_e2e_f38.py` 不受影响（E2E 阶段 10-11 待真实 GitCode/GitHub issue 验证） |

### 设计决定（落地记录）

1. **`CommandIntent` 携带 author_login**（F-39 Sub-D→Sub-F 接口扩展）：早期 Sub-D 用 `Command | None` 返类型，Sub-F 角色校验需要 author_login，所以把返回类型升级为 `CommandIntent(command, author_login, comment_id, comment_body)` 数据类，向后兼容通过 `intent.command` 字段读取命令值。
2. **role check fail-closed**（LLM 自触发防护）：`author_login is None` / 空字符串直接拒绝（即使配 `allow_anyone_to_retry=True` 也会放行）；`author_login == "clawcodex"` 永远放行（bot 自己），其余需匹配 `IssueRecord.author_login`（澄清流填的作者）。
3. **`unblock()` 总是清 intent**（不是真 no-op）：docstring 写"非 ABANDONED 时不修改 status"，但 intent/intent_source/last_command 总是清零——保证下次 poll 重新走 `_resolve_intent()`；`retry_count` 不清以维持限频。
4. **CLI `--force` 高优 audit**：`audit.jsonl` 写 `{event: "retry", priority: "high", force: true, retry_count: N, max_retries_per_issue: M, rate_limited: false}`，与正常 retry 区分；`--force` 缺省时 rate-limit 命中写 `{event: "retry_rejected", priority: "high", rate_limited: true}`。
5. **限频边界**：`retry_count < max_retries_per_issue` 放行（默认 3 表示可重试 3 次）；`retry_count >= max` 拒（CLAUDE.md 验收标准 4 描述为"累计触发 4 次后拒绝"——其实是第 4 次触发时 retry_count 已经是 3，命中 3 >= 3 边界，与设计一致）。
6. **审计日志差异**：daemon `_log_audit_event` 与 CLI `_append_audit_log` 字段集略有不同（daemon 写更少字段，CLI 写 retry_count/max_retries/rate_limited），都满足设计文档的最小集 `{ts, operator, issue_id, mode, reason}`；后续可统一字段。
7. **审计日志路径**：`~/.clawcodex/orchestrator/audit.jsonl`（设计文档指定）；测试通过 `patch(_DEFAULT_AUDIT_LOG_PATH, ...)` 重定向到 tmpdir。

### 验收标准

- 用户在 GitCode issue #1 上加 `agent:retry` label 后,**60s 内**(下一轮 poll)daemon 日志输出 `Issue 1 retry intent detected`,issue 状态从 `completed` 回到 `running`,旧 PR 被关闭,新 PR 编号(原 PR 编号 + N)。
- 用户在 issue #1 上加 `agent:follow-up` label 后,daemon 在同 branch 上 commit + push,**不开新 PR**,原 PR 编号不变,commit 数 +1。
- 用户在 issue comment 发 `/agent retry`,且非原作者时,**daemon 拒绝执行**并发评论 `## ClawCodex: 仅 issue 作者或 maintainer 可触发 /agent retry`。
- `agent:retry` 累计触发 4 次(超过 `max_retries_per_issue=3`)后,daemon 拒绝再次 reset,issue 上自动加 `agent:retry-rejected` label,评论中说明「已达到最大重试次数,需人工处理」。
- `clawcodex orchestrator issue retry --id 1 --mode reset --reason "wrong approach"` 立即生效,等价于 label 触发的 reset 路径,audit.jsonl 有一行 `{ts, operator, issue_id, "reset", "wrong approach"}`。
- 重置不污染已有 issue_registry.json 旧 entry schema:加载老 JSON 时 `intent` / `retry_count` 默认值生效。
- 与 F-37 协同:`agent:follow-up` 触发的 follow-up run,行为与 F-37 阶段 6 的「review-fix prompt builder」一致(只改检视意见,不改 issue 范围)。
- 与 F-38 协同:`agent:follow-up` 触发的 follow-up run 完成后,F-38 Sub-C 调 `update_pull_request` 把新 commit / 新 diff stat / 新 verification 结果追加到 PR body 末尾(以 `## ClawCodex Follow-up #N` 段落追加,非覆盖)。

### 风险与约束

- **LLM 自触发风险**:comment 命令必须做 role 校验,否则 LLM 在自动响应里写 `/agent retry` 会自触发。
- **label 互斥冲突**:`agent:retry` + `agent:follow-up` 同时存在时需定义优先级;本期以「更保守 = follow-up」为准,后续可加 `intent_priority` 配置。
- **重置不删 git history**:reset 走「关 PR + 删本地 registry entry」,但 git remote 的 commit/branch 仍存在,这是预期行为(便于审计)。
- **限频与人工 bypass**:CLI 兜底命令的 `--force` 参数可绕过 `max_retries_per_issue` 限频,需写 `audit.jsonl` 高优条目。
- **与 F-37 耦合**:`agent:follow-up` 依赖 F-37 阶段 6 的「review-fix prompt builder」;F-37 未落地时,follow-up 路径退化为「同 branch agent run」(语义较弱的 follow-up)。
- **平台差异**:GitCode `PATCH /pulls?state=closed` 与 GitHub `PATCH /repos/{owner}/{repo}/pulls/{number}` 端点路径不同,需在 `repo_tracker/client.py` 平台分发处分别实现;Gitee / GitHub 暂列 TODO(同 F-38 Sub-C 的处理)。
- **comment 命令回放**:用户编辑老评论(非最新一条)发命令时,应只处理 `created_at > since_comment_id` 的新评论;`fetch_new_comments_since` 已实现该语义,直接复用。

### 配置示例

在 `extensions/orchestrator/workflow.md` front matter 增:

```yaml
agent:
  retry:
    enabled: true
    intent_labels:
      retry: "agent:retry"
      followup: "agent:follow-up"
      blocked: "agent:blocked"
    max_retries_per_issue: 3
    comment_command_enabled: true
    comment_command_required_role: "author_or_maintainer"
    audit_log_path: "~/.clawcodex/orchestrator/audit.jsonl"
```

### 依赖与协同

- **依赖 F-1、F-38 Sub-C**:`close_pull_request` 与 F-38 Sub-C 共享 `PATCH /pulls` 协议层(Sub-C 改 body,F-39 Sub-B 改 state);先于 F-38 落地要冗余实现一次,建议先做 F-38 Sub-C,F-39 复用。
- **与 F-37 强协同**:`agent:follow-up` 路径是 F-37「PR 检视意见自动修复」的 label 入口;F-37 未落地时 follow-up 退化为「同 branch 普通 agent run」。
- **不破坏 F-38 Sub-D**:`progress_reporter` 的 PhaseComplete 写 ndjson 逻辑在 retry 路径下应照常工作(每次新 run 是新的 session)。
- **不破坏 F-36 LocalTracker**:LocalTracker 无远程 PR 概念,`close_pull_request` 在该路径下应 no-op 并打 warning 日志;`issue_registry.unblock` 行为对 LocalTracker 等价(把 `pending_review` / `abandoned` 状态回滚到 `pending`)。
- **与 F-38 Sub-B 报告**:`agent:retry` 触发的重置会清空 `report_path`,F-38 Sub-B 报告不应被复用;`agent:follow-up` 不清空报告,新 report 追加为 `report_path_v{N+1}` 序列(便于历史回溯)。

---

---

## 五10 F-41 Coordinator 轻量工具集

**状态**: ✅ 已完成
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.8 Coordinator 轻量工具集（F-41）`
**落地版本**: v2.7

### 目标

给 Coordinator Agent 配置独立的轻量工具集（Read、WebSearch、WebFetch），使其可直接处理简单查询而非为每个请求创建 Worker，同时确保写操作类工具（Edit、Write、Bash、Grep、Glob）始终隔离。

### 变更清单

| 文件 | 改动 |
|------|------|
| `src/coordinator/mode.py` | `_COORDINATOR_ALLOWED_TOOLS` 新增 `Read` / `WebSearch` / `WebFetch` |
| `src/coordinator/prompt.py` | 提示词 §2 "Your Tools" 列出 Read、WebSearch、WebFetch 的用途说明 |
| `src/repl/core.py` | 注释同步更新 Coordinator 工具列表 |

### 验收标准验证

| # | 标准 | 结果 |
|---|------|------|
| 1 | Coordinator 可调用 `Read` 读取文件 | ✅ 通过 |
| 2 | Coordinator 可调用 `WebSearch` / `WebFetch` | ✅ 通过 |
| 3 | Coordinator **不能**调用 `Bash`、`Write`、`Edit`、`Grep`、`Glob` | ✅ 通过（6 工具精确匹配） |
| 4 | Worker Agent 不受影响 | ✅ 通过 |
| 5 | Prompt 列出正确的 6 个工具 | ✅ 通过 |
| 6 | `filter_coordinator_tools()` 正确过滤 | ✅ 通过 |
| 7 | 231/231 orchestrator 测试通过 | ✅ 通过 |

### 工具隔离矩阵

| 角色 | 可用工具 | 能力边界 |
|------|---------|---------|
| **Coordinator** | Agent / SendMessage / TaskStop / Read / WebSearch / WebFetch | 读文件、搜索、管理 Worker，**不可**执行代码或写文件 |
| **Worker** | 完整工具套件 | 完整的编码与调试能力 |

### 设计决定

1. **模糊名称匹配**：`filter_coordinator_tools` 用 `startswith` > `in` > `inverse in` 三级后备匹配，而不是精确集合成员判断；好处是 Coordinator 不感知工具实例 ID 变化，坏处是名称前缀重叠的工具可能误放行。
2. **提示词手动同步**：`prompt.py` 的 "Your Tools" 列表无自动校验机制保证与 `_COORDINATOR_ALLOWED_TOOLS` 一致，需人工 review。
3. **不涉及 Worker 工具变更**：Coordinator 工具过滤是独立的半透明白名单，与 Worker 的 `filter_worker_tools` 无关。

---

---

## 五11 F-42 Orchestrator Shared / Sequential Workspace 策略

**状态**: ✅ 完成
**优先级**: P0
**规划文档**: `docs/FEATURE_PLAN.md` → `3.1.9 Shared / Sequential Workspace 策略设计（F-42）`
**落地版本**: v2.11

### 目标

让 Orchestrator 支持在同一个 workspace working tree / integration branch 上顺序处理多个本地 issue。该能力用于“特性规划文档拆分 issue → LocalTracker 排序 → 每个 issue 完成后 commit → 全部 commit 人工检视后统一 PR”的开发流程。

### 当前缺口

当前 `WorkspaceManager` 是 per-issue isolated 语义：`create_for_issue(issue)` 会把 issue identifier 转成 `safe_id`，工作目录固定为 `workspace.root / safe_id`。这会导致每个 issue 使用独立 clone；即使多个 issue 配置同一个 branch name，前一个 issue 的未推送 commit 也不会自然出现在下一个 issue 的 workspace 中。

### 设计摘要

| 子项 | 设计结论 |
|------|----------|
| 配置入口 | 新增 `workspace.strategy: isolated | shared | sequential`，默认 `isolated` 保持向后兼容 |
| 路径策略 | `isolated` 使用 `root/safe_issue_id`；`shared` / `sequential` 使用 `root` 本身作为 repo working tree |
| 顺序约束 | `sequential` 强制 `agent.max_concurrent_agents == 1`，并要求 state-specific concurrency 不超过 1 |
| 分支策略 | 新增 `base_branch` / `integration_branch`；sequential 初始化或复用 integration branch，保留 issue 间 commit 序列 |
| 锁策略 | `sequential_lock: true` 时使用 `.clawcodex_workspace.lock` 防止多个 orchestrator 同时写同一 workspace |
| 清洁度检查 | `require_clean_start` / `require_clean_between_issues` 默认 true，dirty tree 时 fail-closed |
| Registry | 记录 `workspace_strategy`、`workspace_path`、`base_commit_sha`、`start_commit_sha`、`commit_sha`、`previous_issue_id`、`sequence_index` |
| Cleanup | `shared` / `sequential` 默认 `preserve_on_terminal: true`，不自动删除包含人工未推送 commit 的 working tree |

### 拆分计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| Sub-A | 扩展 `WorkspaceConfig` / schema，新增 strategy、branch、dirty guard、preserve、lock 字段 | ✅ 完成 |
| Sub-B | 改造 `WorkspaceManager` 路径选择与 clone/reuse 流程，保留 `create_for_issue(issue)` 外部 API | ✅ 完成 |
| Sub-C | 实现 sequential lock、clean tree 检查、integration branch 初始化 / checkout | ✅ 完成 |
| Sub-D | Orchestrator 初始化时校验 sequential 并发配置，并在 dashboard/event log 暴露 strategy、workspace path、start SHA | ✅ 完成 |
| Sub-E | 扩展 `IssueRecord` / registry，记录每个 issue 的 start/commit SHA 与 sequence metadata | ✅ 完成 |
| Sub-F | 调整 GitSync / cleanup：LocalTracker sequential 默认不 push、不 PR、不 merge，cleanup preserve shared root | ✅ 完成 |
| Sub-G | 补齐单元测试与两 issue 本地端到端测试 | ✅ 完成 |

### 验收标准

| # | 标准 | 期望结果 |
|---|------|----------|
| 1 | 未配置 `workspace.strategy` 的旧 workflow | 行为与当前 per-issue isolated 完全一致 |
| 2 | `strategy=sequential` 且 `max_concurrent_agents > 1` | 配置加载或 Orchestrator 初始化 fail-closed |
| 3 | 两个 LocalTracker issue 按 priority / identifier 排序 | issue 1 完成并 commit 后，issue 2 才启动 |
| 4 | issue 2 启动时执行 `git log` | 能看到 issue 1 的 commit |
| 5 | 每个 issue 完成后 | registry 记录对应 `start_commit_sha` / `commit_sha` / `sequence_index` |
| 6 | sequential workspace 有未提交改动 | 默认拒绝启动下一个 issue，并提示人工处理 |
| 7 | issue 全部完成 | integration branch 保留连续 commit，workflow 不自动 push / PR / merge / squash |
| 8 | cleanup 或 terminal state | shared/sequential root 不被删除 |

### 风险与后续决策

1. **F-39 retry 语义需单独收敛**：sequential 模式下 reset 当前 issue 可能影响后续 commit 链，默认应推荐 follow-up commit；若要 reset，应要求人工回滚到 `start_commit_sha`。
2. **shared 与 sequential 边界需保持清晰**：`shared` 可作为低约束共享工作树模式，但只要涉及“issue 间严格继承 commit”就应使用 `sequential`。
3. **失败现场默认保留**：dirty workspace 往往是问题诊断所需现场，不应被自动 reset 或 cleanup 删除。
4. **人工最终 PR 仍是显式步骤**：LocalTracker sequential workflow 的目标是产出可审查 commit 序列，不应自动推远端或创建 PR。

---

---

## 五7 F-45 Orchestrator tool-call 审计旁路

**状态**: ✅ 已完成 (2026-06-02)
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `§3.1.10 Tool-call 审计旁路设计（F-45）`
**触发场景**: 2026-06-02 F-38 落地后审阅发现，`extensions/orchestrator/report_writer.py:write()` 只持久化 `tool_count: int` 与末尾 4000 字符的 `output_excerpt`，**per-tool 决策流水不落盘**。`extensions/orchestrator/agent_runner.py:87-108` 的 `_handle_tool_call` 始终调 `ApprovalPolicy.evaluate()`，把 `_approved` / `_deny_reason` 写回 `ToolCallEvent` 内存对象 —— 进程崩溃即丢。在 orchestrator headless 场景下 `permission_mode` 走 auto-upgrade 到 `bypassPermissions`（`patches/upstream/58ea488/merged/0026.tui_app_py.patch:1287-1291`），TS 注释说 "no logging"，**但 Python 端 ApprovalPolicy 一直在跑**——审计数据其实有，只是没落盘。

### 目标

在 `_handle_tool_call` 之后追加一段 NDJSON 旁路落盘，**与 `permission_mode` 解耦**（`bypassPermissions` / `dontAsk` / `acceptEdits` / `default` 一视同仁全写），并扩展 `report_writer.RunReport` 字段 + markdown 模板，让审计员从 run 报告就能定位到 `~/.clawcodex/tool-events/{run_id}/events.ndjson` 完整 tool-call 流水。**终结 "bypass ≠ 无审计" 这个误读**——bypass 关闭的是 user-prompt audit 层，本特性补上 per-tool 决策 audit 层。

### 子特性

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| A | `_append_tool_event_log` 旁路方法 | 把 `_handle_tool_call` 的决策序列化到 NDJSON | 在 `agent_runner.py` 新增私有方法，接 `event: ToolCallEvent` + `session_context`，`json.dumps` 一行写 `~/.clawcodex/tool-events/{run_id}/events.ndjson`；不依赖 permission_mode |
| B | `ToolEventLog` 数据契约 | 明确每行 NDJSON 字段 | `ts: float` / `tool: str` / `params: dict` / `approved: bool` / `deny_reason: str \| None` / `permission_mode: str` / `turn: int` / `session_run_id: str` 共 8 字段 |
| C | `RunReport` 增 `tool_events_path` 字段 | 把旁路文件路径登记到报告 | `report_writer.py:RunReport` 加 `tool_events_path: str \| None`；`write()` 多接收一个 `tool_events_path` 参数；`_render_markdown` 加一行 `Tool events: <path>`；`_copy_with_fallback` 把 NDJSON 拷到 `~/.clawcodex/reports/.../{run_id}/` 持久化层 |
| D | `AgentRunner.run` 衔接 | 把 `run_id` 注入 `session_context` | `agent_runner.py:run()` 在创建 session 时把 `session.run_id` 写到 `session_context`；`_handle_tool_call` 闭包拿 `session_context.get("run_id", "unknown")` 当目录名 |
| E | 关闭策略与轮转 | 限制磁盘占用，避免长跑撑爆 | NDJSON 文件超过 50MB 时 rotate 为 `events.ndjson.1` 等；`tool-events/` 路径加入 `.gitignore` 默认 patterns；7 天清理任务可挂 cron（本期只做 rotate，清理推 v2.14） |
| F | 测试 | 端到端验证旁路 + 报告登记 | 单测：`_append_tool_event_log` 在 mock session 下写出合法 NDJSON，字段齐全；集成测试：`report_writer.write` 接收 `tool_events_path` 后 markdown 报告含该路径，NDJSON 拷到持久化层；回归：`bypassPermissions` / `dontAsk` / `acceptEdits` / `default` 四种 mode 下，tool-call 数量与 NDJSON 行数一致 |

### 当前基线

| 能力 | 当前状态 | 说明 |
|------|----------|------|
| `report_writer.RunReport.tool_count` 持久化 | ✅ 已实现 | `report_writer.py:85` 写单个 int，粗粒度 |
| `report_writer.RunReport.output_excerpt` 持久化 | ✅ 已实现 | `report_writer.py:88` + `_excerpt` 末尾 4000 字符 |
| `_handle_tool_call` 拦截每个 tool call | ✅ 已实现 | `agent_runner.py:121-142` 跑 `ApprovalPolicy.evaluate()`；本期同步修复了 run-loop 调用链（详见 实施记录） |
| Per-tool 决策持久化（NDJSON） | ✅ 已实现 | `agent_runner.py:144-218` `_append_tool_event_log` 写 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，8 字段契约见 `tool_event_log.py:42-58` |
| `~/.clawcodex/tool-events/` 目录 | ✅ 已实现 | `Path.home() / ".clawcodex" / "tool-events" / {run_id}`，`agent_runner.py:382-391` 启动时落定到 `session.tool_events_path` |
| `RunReport.tool_events_path` 字段 | ✅ 已实现 | `report_writer.py:42-45` 加在 dataclass 末尾默认 `None`（向前兼容），`write()` 接收并 dual-write NDJSON（`report_writer.py:128-132`） |
| NDJSON 落盘 + dual-write 模式 | ✅ 已实现 | 旁路走文件系统 NDJSON（`agent_runner.py`），与 F-40 进程内 `ToolContext.tasks` metadata 通道并存，职责分离 |

### 实施进度

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | `agent_runner.py:_append_tool_event_log` + `~/.clawcodex/tool-events/{run_id}/events.ndjson` 旁路 | A | ✅ 已完成 (2026-06-02) |
| 2 | `ToolEventLog` 字段契约 + JSON serializer（8 字段） | B | ✅ 已完成 (2026-06-02) |
| 3 | `report_writer.RunReport` 增 `tool_events_path` 字段，`write()` 多接收该参数，markdown 模板加一行，NDJSON 走 `_copy_with_fallback` 拷到 `~/.clawcodex/reports/.../{run_id}/` | C | ✅ 已完成 (2026-06-02) |
| 4 | `AgentRunner.run` 注入 `run_id` 到 `session_context` + 同步修复 `_handle_tool_call` 调用链 | D | ✅ 已完成 (2026-06-02) |
| 5 | NDJSON rotate 阈值 50MB + `.reports` 加入 `.gitignore` 默认 patterns | E | ✅ 已完成 (2026-06-02) |
| 6 | 单测（16 例）+ 集成测试 + 四种 mode 回归 | F | ✅ 已完成 (2026-06-02) |

### 实施记录 (2026-06-02)

实现过程中发现并同步修复了设计文档的一处隐藏缺口：

**问题**: `extensions/orchestrator/agent_runner.py:run()` 的 `ToolCallEvent` 分支（设计文档声称在 line 87-108）实际上**从未调用** `_handle_tool_call` —— 注释明确说 "the orchestrator's ApprovalPolicy (ToolCallEvent) is not consulted here"。如果按设计字面落地，NDJSON 的 `approved` 字段会永远是 `None`，审计数据无意义。

**修复**: 在 `agent_runner.py:505-509` 显式调用 `event = self._handle_tool_call(event, session_context)`，再追加 `_append_tool_event_log`，并把 `turn` 写回 `session_context`。改动后 `TestAgentRunnerWiresAuditBypass.test_run_writes_ndjson_row_and_sets_session_path` 端到端验证 `approved` 字段被真实填充。

**新增/修改文件**:
- `extensions/orchestrator/tool_event_log.py`（新增，~50 行）— `ToolEventLog` 8 字段 frozen dataclass + `to_dict()` / `to_json()`
- `extensions/orchestrator/agent_runner.py`（修改）— `_append_tool_event_log`（~75 行,带嵌套 try/except + 50MB rotate），常量 `_TOOL_EVENT_LOG_ROTATE_BYTES = 50 * 1024 * 1024`，`AgentSession.tool_events_path` 字段，`session_context` 注入 `run_id` / `permission_mode` / `turn`，ToolCallEvent 分支接 `_handle_tool_call`
- `extensions/orchestrator/report_writer.py`（修改）— `RunReport.tool_events_path` 字段（末尾，默认 `None`），`write()` 接收参数并 dual-write NDJSON，`_render_markdown` 追加 `Tool events: <path>` 行
- `extensions/orchestrator/git_sync.py`（修改）— `_write_report` 转发 `session.tool_events_path` 到 `report_writer.write()`
- `extensions/orchestrator/config/schema.py`（修改）— `WorkspaceConfig.gitignore_patterns` 默认 list 追加 `.reports`
- `tests/test_orchestrator_f45_audit_bypass.py`（新增，~400 行）— 16 个测试覆盖 7 个类

**测试结果**:
- `pytest tests/test_orchestrator_f45_audit_bypass.py -v` → **16/16 通过**
- `pytest tests/test_orchestrator_*.py -q` → **271/271 通过**（含 4 个 backward-compat 探针）
- `pytest tests/manual_e2e_f38.py -v` → **4/4 通过**（CLAUDE.md 强制 git_sync 修改时跑）

**与设计文档的两处偏差**（均已与用户确认）:
1. **同步修复 `_handle_tool_call` 调用链**（见上方"问题"段）—— 否则 `approved` 字段永远是 `None`。
2. **单文件 50MB rotate**：旧 `events.ndjson` 直接 rename 为 `events.ndjson.1`（覆盖），无多代轮转；7 天清理推 v2.14。

### 验收标准

- Orchestrator 跑完一个 issue 后，`~/.clawcodex/tool-events/{run_id}/events.ndjson` 存在，行数 == `RunReport.tool_count`。
- NDJSON 每行含 `ts` / `tool` / `params` / `approved` / `deny_reason` / `permission_mode` / `turn` / `session_run_id` 八个字段，deny_reason 在允许时为 `None`。
- `report_writer.write()` 接收 `tool_events_path` 后，生成的 `~/.clawcodex/reports/.../{run_id}/{run_id}.md` 报告含 `Tool events: <path>` 一行，`.json` 报告含 `tool_events_path` 字段。
- `~/.clawcodex/reports/.../{run_id}/events.ndjson` 与 workspace 内 NDJSON 内容一致（`_copy_with_fallback` 走 `.tmp` + `os.replace` 原子化）。
- `permission_mode=bypassPermissions` / `dontAsk` / `acceptEdits` / `default` 四种 mode 下，NDJSON 都生成，字段一致，仅 `permission_mode` 列值不同。
- 跑 `pytest tests/test_orchestrator_*.py -q` 与 `tests/manual_e2e_f38.py -v -s`，**无回归**。
- 端到端：在 `bypassPermissions` 模式下 review 一次 issue run，能从 `events.ndjson` 看到所有 tool call 的完整 params + approval 决策。

### 风险与约束

1. **磁盘占用**：NDJSON 不压缩，长跑（几百 turn）会撑大。Mitigation：rotate 阈值 50MB，7 天清理任务（v2.14 挂 cron）。
2. **写并发**：`events.ndjson` 多 session 同 run_id 时 append 冲突。Mitigation：单 run_id 单 session，`AgentRunner.run` 持有写锁；`fdopen` + 行级 `flush`，O_APPEND 原子写。
3. **敏感数据泄露**：`params` 字段含 agent 输入，可能含 token / 路径 / 代码片段。Mitigation：文档明示 "events.ndjson 在 `~/.clawcodex/` 目录，用户自管 ACL"；考虑后续加 `--redact` 字段（本期不做）。
4. **Failure 隔离**：`_append_tool_event_log` 抛异常不能阻塞 agent run。Mitigation：try/except 包住，异常 `logger.exception` 即可，不 raise。
5. **不动 `extensions/api/query.py`**：旁路挂在 `agent_runner.py:_handle_tool_call`，**不修改** `ToolCallEvent` 本身；不破坏 `extensions.api` 的 stream 协议。
6. **依赖 F-40 sink 互不重叠**：F-40 落 `PhaseComplete` / `TurnComplete` / `SessionComplete` 到 `ToolContext.tasks` metadata（进程内），本特性落 per-tool 决策到 NDJSON（文件系统）；两套审计通道并存，职责分离。

### 已拟定的设计决定

| # | 决定 | 理由 |
|---|------|------|
| 1 | 旁路挂 `agent_runner._handle_tool_call` 之后，不动 `ApprovalPolicy` 内部 | `ApprovalPolicy` 是策略层，不感知 run_id / session_context；在 `agent_runner` 层加旁路对策略零侵入 |
| 2 | 落盘用 NDJSON 而非 SQLite / Parquet | NDJSON 追加写 O(1)，`tail` / `grep` 友好，无需引入新依赖；审计场景 "看尾部" 占 90% |
| 3 | 落盘在 `~/.clawcodex/tool-events/` 而非 workspace 内 | workspace 内文件会被 `git_sync` 推到 PR，导致审计数据污染仓库；`~/.clawcodex/` 是用户私有目录 |
| 4 | 不修改 `permission_mode` 语义 | bypass / dontAsk / acceptEdits / default 各自行为不变；旁路只是**追加观察**，不改决策 |
| 5 | `RunReport.tool_events_path` 字段加在末尾，非破坏性 | 旧 run 报告 reader 不识别此字段就忽略，向前兼容 |
| 6 | rotate 阈值 50MB，清理推到 v2.14 | rotate 是单文件级别，清理是跨文件级别，降低本 PR 风险 |
| 7 | `params` 字段**不**做 redact | 与 TS upstream `dontAsk` "All allowed, logged" 行为对齐 |
| 8 | 不动 `extensions/api/query.py` 的 stream 协议 | `_handle_tool_call` 是 orchestrator 内部拦截，`api.query` 是 stream 出口，职责分离 |

### 依赖与协同

- **依赖**：
  - `extensions/orchestrator/agent_runner.py:_handle_tool_call`（line 121-142）作为挂点（本期同步修复了调用链）
  - `extensions/orchestrator/report_writer.py:RunReport`（line 23-46）+ `write()`（line 49-141）作为报告层
  - `extensions/orchestrator/orchestrator.py:AgentSession` 已有 `run_id: str | None` 字段（line 47），可直接用
  - **新文件** `extensions/orchestrator/tool_event_log.py`（~60 行）— `ToolEventLog` 8 字段契约 + serializer
- **协同**：
  - 与 F-38 互补：F-38 写的是 "run-level summary"（tool_count + output_excerpt），本特性写 "per-tool detail"
  - 与 F-40 互补：F-40 走 `ToolContext.tasks` 进程内 metadata；本特性走文件系统 NDJSON；两套审计通道并存
  - 与 F-22 cron 无关：本特性是 orchestrator 内部，cron 触发不直接走 audit
  - **先于 F-46 落地收益**：F-46 拆分 enum 时，`audit_log` 字段的实现就是 F-45 的 NDJSON 旁路；先做 F-45 减少 F-46 落地风险
- **不破坏 F-37 / F-39**：follow-up run / 重跑 run 走相同旁路路径

---

---

## 五9 F-47 Permission Settings Schema 重构

**状态**: 📋 设计完成
**优先级**: P1
**规划文档**: `docs/FEATURE_PLAN.md` → `§3.17 Permission Settings Schema 重构设计（F-47）`
**触发场景**: 2026-06-02 用户报告"配置 `~/.clawcodex/config.json` 的 `settings.permissions.allowBypassPermissionsMode: true` 后,REPL Shift+Tab 仍然只循环 3 档"。排查发现四层串联 bug 互相纠缠：(1) `SettingsSchema.permissions: list[PermissionRule]` 的 schema 形状与磁盘实际 dict 形态(`updates.py:291-343` / `setup.py:62-67` / `loader.py:14-30` 写入)不一致 —— dict 落进 known 字段,`isinstance(..., list)` 是 False 不转换,`allowBypassPermissionsMode` 既进不了 `extra` 也不被结构化读取；(2) `has_allow_bypass_permissions_mode()` 写死了 `settings.extra["permissions"]` 路径，永远读不到；(3) `clawcodex_ext/cli/permissions.py:36-39` 调 `initial_permission_mode_from_cli` 时没传 `settings_default_mode`，`settings.permissions.defaultMode` 形同虚设；(4) 顶层 `settings.permission_mode` 字段虽然存在但 `resolve_permission_state` 根本没读它。同时 `src/settings/types.py:13-20` 的 `PermissionRule`（带 `tool/allow/glob/regex/description/source` 字段）与运行时实际使用的 `src/permissions/types.py:80-84` frozen `PermissionRule`（带 `source/rule_behavior/rule_value`）不是同一个东西，且前者没有任何 caller —— 死代码。

### 目标

把 `SettingsSchema.permissions` 从 `list[PermissionRule]` 改为结构化 `PermissionsConfig` dataclass（dict 形态），与磁盘格式 + TS 上游契约对齐；让 `settings.permissions.defaultMode` / `settings.permissions.allowBypassPermissionsMode` 真正生效；`resolve_permission_state` 一次 plumb 到位；删除 settings 层"假" `PermissionRule` 死代码。后续 `permissions.*` 新增 sub-key 不需要改 schema —— 走 `PermissionsConfig.additional` 前向兼容包。

> **F-47.1 (2026-06-02)**：F-47 设计阶段曾在 `clawcodex_ext/cli/permissions.py` 保留顶层 `settings.permission_mode` 作为 back-compat 读取通道。F-47.1 在项目尚未发布的前提下直接删除该通道：`resolve_permission_state` 只读 `settings.permissions.default_mode`，磁盘上残留的 `settings.permission_mode` 字段在启动时被忽略。F-46.2 计划的"标 deprecated"步骤因此不再需要。

### 子特性

| Sub | 名称 | 目标 | 主要工作 |
|-----|------|------|----------|
| A | `PermissionsConfig` dataclass 定义（`src/settings/types.py`） | 把 `permissions` 从 `list[PermissionRule]` 改为结构化 dict 形态 | 新增 `PermissionsConfig` dataclass，含 `allow_bypass_permissions_mode: bool` / `default_mode: str \| None` / `rules: dict[str, list[str]]` / `additional_directories: list[str]` / `additional: dict[str, Any]` 字段；`from_dict()` / `to_dict()` 双向互转，未知 sub-key 进 `additional`；`SettingsSchema.permissions: PermissionsConfig = field(default_factory=PermissionsConfig)` |
| B | `SettingsSchema.from_dict` 加载改造（`src/settings/types.py:161-198`） | dict / list / None 形态都安全降级到 `PermissionsConfig` | 把 `if "permissions" in known and isinstance(known["permissions"], list): ...` 替换为 `if "permissions" in known: known["permissions"] = PermissionsConfig.from_dict(known["permissions"])`；保留 `default` 值（`field(default_factory=PermissionsConfig)`）兜底 |
| C | `has_allow_bypass_permissions_mode` 加 fallback（`src/permissions/modes.py:113-140`） | 读路径兼容结构化字段 + 旧 `extra["permissions"]` 旁路 | 新增私有 `_settings_perms(settings)` 聚合器：先 `settings.permissions.additional`，再 `to_dict()`，最后 `extra["permissions"]`；`has_allow_bypass_permissions_mode` 改读该聚合结果 |
| D | `resolve_permission_state` plumb（`clawcodex_ext/cli/permissions.py:36-39`） | 启动模式读 `permissions.defaultMode` | 加 `try: get_settings()` 块读 `settings.permissions.default_mode`，传给 `initial_permission_mode_from_cli(settings_default_mode=...)`；模块顶部加 `from src.settings.settings import get_settings`。**F-47.1 (2026-06-02) 已删除顶层 `settings.permission_mode` fallback 读取**，磁盘上残留的旧字段在启动时被忽略。 |
| E | `validate_settings` 重写（`src/settings/validation.py:32-38, 96-103`） | dict 形态不再被当成 list 遍历 | 改 `settings.permission_mode not in VALID_PERMISSION_MODES` 校验为：先取 `settings.permissions.default_mode`，空再取顶层 `settings.permission_mode`（空串视为未设置跳过校验）；删除 `for i, rule in enumerate(settings.permissions): ...` 整段，替换为对 `settings.permissions.rules["allow"/"deny"/"ask"]` 的字符串非空检查 |
| F | `DEFAULT_SETTINGS` 改造（`src/settings/constants.py:12-46`） | 默认 settings 也能跑新 schema | `permissions=[]` 改为 `permissions=PermissionsConfig()`；`permission_mode="default"` 留作兼容字段默认值（保持不变） |
| G | 单元测试 + e2e（`tests/test_permission_settings_schema.py` + `tests/manual_e2e_f38_permissions.py`） | 七条单元测试覆盖关键路径 | 详见"验收标准"节 |
| H | 死代码清理（`src/settings/types.py:13-20`） | 删 settings 层"假" `PermissionRule` | 删除 `PermissionRule` dataclass；`grep -r "from src.settings.types import PermissionRule" src/ tests/` 确认无引用（实际只有 `from_dict:176-179` 一处） |

### 当前基线

| 能力 | 当前状态 | 说明 |
|------|----------|------|
| 启动模式从 `settings.permissions.defaultMode` 读 | ❌ 缺 | `clawcodex_ext/cli/permissions.py:36-39` 没传 `settings_default_mode` |
| 启动模式从顶层 `settings.permission_mode` 读 | ❌ 缺 | 字段存在但 `resolve_permission_state` 不读。**F-47.1 (2026-06-02) 已直接删除该通道**：磁盘上残留的 `settings.permission_mode` 字段在启动时被忽略，详见 风险 #3 / 设计决定 #3 / F-47.1 备注。 |
| `settings.permissions.allowBypassPermissionsMode` 读到 | ❌ 缺 | dict 落进 known field,`extra["permissions"]` 永远 None |
| `SettingsSchema.permissions` 形状 | ❌ 错 | `list[PermissionRule]`，与磁盘 dict 形态、TS 上游契约都不一致 |
| `src/settings/types.py:13-20` `PermissionRule` | ❌ 死代码 | 唯一引用点是 `from_dict:176-179`，且永远走不到（磁盘不是 list） |
| `validation.py:97-102` `for i, rule in enumerate(settings.permissions)` | ❌ 隐式 bug | 一旦磁盘 dict 形态进来会抛 `TypeError: dict is not iterable`（被 `isinstance(..., list)` 短路掩盖） |
| 磁盘格式契约（`updates.py:persist_permission_update`） | ✅ 稳定 | dict 形态：`{allow: [...], deny: [...], ask: [...], defaultMode, additionalDirectories, allowBypassPermissionsMode}` |
| `setup_permissions` / `loader.py` 旁路读 dict | ✅ 兼容 | 直接 `settings["permissions"]` 当 dict 用，路径独立于 `SettingsSchema` |

### 实施进度

| 阶段 | 任务 | Sub | 状态 |
|------|------|-----|------|
| 1 | `PermissionsConfig` dataclass + `SettingsSchema.from_dict` 改造（自包含 schema 改动） | A, B | 📋 待开始 |
| 2 | `DEFAULT_SETTINGS` 改造 + 现有测试回归 | F | 📋 待开始 |
| 3 | `has_allow_bypass_permissions_mode` 聚合器 + `_settings_perms` 私有函数 | C | 📋 待开始 |
| 4 | `validate_settings` 重写（dict 形态 + 顶层字段 fallback） | E | 📋 待开始 |
| 5 | `resolve_permission_state` plumb（`get_settings()` 注入 `settings_default_mode`） | D | 📋 待开始 |
| 6 | `setup_permissions` 签名扩 `default_mode`（F-47 范围内可选） | — | 📋 规划中 |
| 7 | 删除 settings 层"假" `PermissionRule`（grep 确认无引用后落地） | H | 📋 待开始 |
| 8 | 七条单元测试 + 一条 e2e（`test_permission_settings_schema.py` + `manual_e2e_f38_permissions.py`） | G | 📋 待开始 |
| 9 | `pytest tests/test_orchestrator_*.py -q` + `tests/manual_e2e_f38.py -v -s` 无回归 | — | 📋 待开始 |

### 验收标准

- 用户 `~/.clawcodex/config.json` 写 `{settings: {permissions: {allowBypassPermissionsMode: true, defaultMode: "bypassPermissions"}}}` 启动 `python3 -m src.cli` 后，`args._resolved_permission_mode == "bypassPermissions"` 且 `args._resolved_is_bypass_available is True`。
- REPL 内 Shift+Tab 连续按能切到第四档 `Bypass`（cycle 路径 `default → acceptEdits → plan → bypassPermissions → default`）。
- 旧 binary 不炸：`settings.extra["permissions"] = {"allowBypassPermissionsMode": True}`（F-47 落地前已运行的实例）仍能被 `_settings_perms` 聚合到，`has_allow_bypass_permissions_mode` 返回 True。
- 顶层 back-compat（F-47.1 已删除）：`settings.permission_mode: "bypassPermissions"`（旧字段单独写、不嵌在 `permissions` 里）原本在 F-47 阶段作为回退读取通道被 `resolve_permission_state` 解析。**F-47.1 (2026-06-02) 已直接删除该通道**：磁盘上残留的 `settings.permission_mode` 字段在启动时被忽略；空串视为未设置、不触发 `validation.py` 的 enum 校验报错（保留后者是为了不影响后续字段写入）。
- 单元测试至少覆盖七条：
  1. `test_permissions_dict_loads_into_struct`：从 `{permissions: {allowBypassPermissionsMode: True}}` 加载后 `settings.permissions.allow_bypass_permissions_mode is True`。
  2. `test_default_mode_resolved_from_permissions_dict`：`initial_permission_mode_from_cli(settings_default_mode="bypassPermissions", ...)` 返回 `"bypassPermissions"`。
  3. `test_has_allow_bypass_true_after_settings_loaded`：`has_allow_bypass_permissions_mode()` 在 settings 注入后 True。
  4. `test_legacy_extra_permissions_fallback`：`settings.extra["permissions"] = {"allowBypassPermissionsMode": True}` 时仍 True。
  5. `test_legacy_top_level_permission_mode_is_ignored`（F-47.1 改名）：`settings.permission_mode="bypassPermissions"`（仅设旧字段、不嵌 `permissions`）时 `resolve_permission_state` 把 `_resolved_permission_mode` 解析为内置 `"default"` fallback；磁盘上的旧字段对启动 mode 不再有任何影响。`allowBypassPermissionsMode=True` 仍让 `_resolved_is_bypass_available` 为 True（这与 default-mode 解析是两条独立路径）。
  6. `test_unknown_subkey_preserved`：`{permissions: {myCustomFlag: 42}}` 加载后 `settings.permissions.additional["myCustomFlag"] == 42`。
  7. `test_dict_shape_no_longer_crashes_validation`：`validate_settings` 对 dict 形态 `permissions` 不抛。
- e2e：新增 `tests/manual_e2e_f38_permissions.py`，断言 "config.json 配 `defaultMode=bypassPermissions` + `allowBypassPermissionsMode=true` → 启动后 `is_bypass_available=True` 且首屏 `args._resolved_permission_mode == 'bypassPermissions'`"。
- `pytest tests/test_orchestrator_*.py -q` 与 `tests/manual_e2e_f38.py -v -s` 无回归；`tests/test_permission_updates.py`（走 `updates.py` 旁路、不经 SettingsSchema）继续通过。

### 风险与约束

1. **死代码清理**：`src/settings/types.py:13-20` 的 `PermissionRule` 删除前需 `grep -r "from src.settings.types import PermissionRule" src/ tests/` 确认唯一引用是 `from_dict:176-179`（本次同时改写），无第三处。`grep` 实际结果：仅 `src/settings/types.py:177` 一处自引用，可安全删。
2. **pydantic-settings 后端**：`src/config.py:27-33` 的 `CLAW_USE_PYDANTIC_SETTINGS=true` 旁路有独立的 schema 定义。本次重构集中在 `src/settings/types.py` 的 dataclass 后端；pydantic 后端需单独 review 是否仍把 `permissions` 声明为 list，若是，需同步改。本期可只覆盖 dataclass 后端，pydantic 路径补一个 TODO 在 F-47.1。
3. **顶层 `permission_mode` 字段 deprecation**：F-47 原计划保留读取、不标 deprecated，避免一次性引入太多变化；F-46 后续阶段会统一 deprecate enum 字段。**F-47.1 (2026-06-02) 已先一步直接删除读取通道**，deprecation 步骤不再需要。
4. **`extra` 字段语义迁移**：`SettingsSchema.extra` 原来是"未识别 sub-key 的兜底"。F-47 之后 `permissions` 已知 sub-key 不再溢出到 `extra`，但其它未知 sub-key 仍走 `extra`（行为不变）。F-47 在 `from_dict` 注释里写清楚这一点，避免后续维护者困惑。
5. **改动 6 个文件 + 1 个测试新建**：`src/settings/types.py` / `src/settings/validation.py` / `src/settings/constants.py` / `src/permissions/modes.py` / `src/permissions/setup.py`（可选）/ `clawcodex_ext/cli/permissions.py` / `tests/test_permission_settings_schema.py`（新建）。每个文件改动局部，git revert 风险可控。
6. **F-47 与 F-46 顺序无关**：F-47 修 schema 形状 + 启动模式 plumb；F-46 拆 `permission_mode` enum 为三字段。两者不耦合，可独立 PR、并行落地。F-47 落地后 `permissions.defaultMode` 字段自动成为 F-46.0 拆 `audit_log` 后的"启动默认模式"读路径。
7. **`validate_settings` 空 `permission_mode` 误报**：旧 `permission_mode: PermissionModeType = "default"` 默认值是 `"default"`，合法；F-47 改成 `permission_mode: str = ""` 后空串会被旧校验逻辑当成"非法 mode"误报。E 阶段把空串视为未设置并跳过校验。

### 已拟定的设计决定

| # | 决定 | 理由 |
|---|------|------|
| 1 | `permissions` 改 dict 形态（`PermissionsConfig` dataclass） | 对齐磁盘格式（`updates.py:persist_permission_update`）+ TS 上游契约（`modes.py:118-141` docstring 明确 TS 是 dict） |
| 2 | 用 dataclass 承载已知 sub-key，未知 sub-key 进 `additional` | 强类型 + 前向兼容；新增 sub-key 不需要改 schema |
| 3 | 顶层 `settings.permission_mode` 字段保留为 back-compat 读取通道 | 不引入一次性 breaking change；F-46 后续阶段会统一 deprecate。**F-47.1 (2026-06-02) 已直接删除该通道**（项目尚未发布、磁盘上没有需要迁移的旧配置），F-46 deprecate 步骤不再需要。 |
| 4 | 删除 settings 层"假" `PermissionRule` 死代码 | 与运行时 `PermissionRule` 同名异构，混淆读者；唯一引用点 `from_dict:176-179` 本次同时改写 |
| 5 | `has_allow_bypass_permissions_mode` 加 `_settings_perms` 聚合器，保留 `extra["permissions"]` fallback | F-47 落地前的旧 binary 不炸；同时支持过渡期调试（写 extra 也能读出） |
| 6 | `validate_settings` 对空 `permission_mode` 跳过校验 | 避免 F-47 改默认值为 `""` 后旧校验逻辑误报；F-47.1 删除 back-compat 读取通道后这条规则失去直接触发场景，但保留是为了让"磁盘上残留 `permission_mode=""`"的旧配置不报错（行为无副作用、不删除以避免引入额外变更面） |
| 7 | 阶段化落地：1→2→3→4→5→6（可选）→7→8→9 | 自包含 schema 改造先闭环（1+2），读路径 + 校验（3+4），启动模式 plumb（5），可选 setup 改造（6），最后清死代码（7）+ 测试（8+9）。每步独立可回滚 |
| 8 | `PermissionsConfig.rules` 用 `dict[str, list[str]]`（`{"allow": [...], "deny": [...], "ask": [...]}`） | 对齐磁盘格式与 `loader.py:settings_to_rules` 读路径；`PermissionRule` 字符串原样保留，不强转 dataclass |
| 9 | `PermissionsConfig` 不导出 `PermissionRule` dataclass（虽然内部 rules 存字符串） | 避免重新引入死代码；`PermissionRule` 字符串是磁盘原样，运行时 `permissions/rule_parser.py` 已有 `permission_rule_value_from_string` 解析路径 |
| 10 | `PermissionsConfig.additional_directories` 单独字段（不混在 `additional`） | 已知 sub-key 给类型化访问；`additional` 只装真正未知的 sub-key |

### 依赖与协同

- **依赖**：
  - `src/settings/settings.py:load_settings` 现成可用，不需要改
  - `src/permissions/modes.py:initial_permission_mode_from_cli` 签名已有 `settings_default_mode` 形参，本次只调通 plumb
  - `src/permissions/types.py:PERMISSION_MODES` 现成可用
- **协同**：
  - 与 F-15（Shift+Tab cycle）强协同：F-15 实现了 `default→acceptEdits→plan→bypassPermissions→default` cycle；F-47 让 cycle 真正能切到 `bypassPermissions`
  - 与 F-31（TUI 权限模式选择器）协同：TUI 模态对话框也是消费 `permissions.defaultMode` 字段
  - 与 F-46 弱相关：F-46 后续 `interactive` / `default_decision` 字段落地时，`PermissionsConfig` 是天然的承接结构
  - 与 F-40 无关：ProgressSink 重构不涉及 settings schema
- **先于**：
  - 无（无其它特性阻塞 F-47）
- **后续议题（v2.13+ / F-47.1）**：
  - pydantic-settings 后端的 `permissions` schema 同步改造
  - `setup_permissions` 签名扩 `default_mode`（F-47 范围可选 → 后续必做）
  - F-46.1 拆 `interactive` / `default_decision` 落到 `PermissionsConfig`
  - F-46.2 `permission_mode` 标 deprecated 时，back-compat 通道改成"打 warning 不读"。**F-47.1 已先一步直接删除读取通道**（无 deprecation 阶段），该议题 N/A。

---

*文档更新时间: 2026-06-02*

*版本 v2.13 更新：新增 F-45 / F-46 / F-47。F-45 P1 在 `agent_runner._handle_tool_call` 后加 NDJSON 旁路落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，与 permission_mode 解耦；扩展 `report_writer.RunReport.tool_events_path` 字段 + markdown 模板登记路径；终结 "bypass ≠ 无审计" 误读。F-46 P2 把 `permission_mode` enum 拆为 `interactive` / `default_decision` / `audit_log` 三个正交字段，F-46.0（v2.13）只拆 `audit_log`，依赖 F-45 落地后端到端验证；`permission_mode` 保留为 backward-compat shim 标 deprecated；F-46.1+ 拆其余两字段推到 v2.15+。F-47 P1 修 `SettingsSchema.permissions: list[PermissionRule]` 与磁盘 dict 形态不一致 / `has_allow_bypass_permissions_mode` 永远读不到 / `resolve_permission_state` 没传 `settings_default_mode` / 顶层 `settings.permission_mode` 字段未读 四个串联 bug；引入 `PermissionsConfig` dataclass 对齐磁盘 + TS 上游契约，让 `permissions.defaultMode` 与 `permissions.allowBypassPermissionsMode` 真正生效；删除 settings 层"假" `PermissionRule` 死代码。*

*F-47.1 (2026-06-02) v2.13 hotfix：在项目尚未发布、磁盘上没有需要迁移的旧配置的前提下，直接删除 F-47 原本保留的顶层 `settings.permission_mode` back-compat 读取通道（`clawcodex_ext/cli/permissions.py:resolve_permission_state` 不再 fallback 到 `s.permission_mode`）；`SettingsSchema.permission_mode` 字段保留为兼容形态但启动时不再被读，磁盘上残留的旧值被静默忽略。F-46.2 的 deprecation 步骤因此不再需要。*

*文档更新时间: 2026-06-02*

*版本 v2.13 更新：新增 F-45 / F-46。F-45 P1 在 `agent_runner._handle_tool_call` 后加 NDJSON 旁路落 `~/.clawcodex/tool-events/{run_id}/events.ndjson`，与 permission_mode 解耦；扩展 `report_writer.RunReport.tool_events_path` 字段 + markdown 模板登记路径；终结 "bypass ≠ 无审计" 误读。F-46 P2 把 `permission_mode` enum 拆为 `interactive` / `default_decision` / `audit_log` 三个正交字段，F-46.0（v2.13）只拆 `audit_log`，依赖 F-45 落地后端到端验证；`permission_mode` 保留为 backward-compat shim 标 deprecated；F-46.1+ 拆其余两字段推到 v2.15+。*

*版本 v2.11 更新: F-42 Sequential Workspace 策略实现完成。`workspace.strategy: isolated | shared | sequential` 落地，sequential 强制单并发并使用顺序锁，共享 root 上的 integration branch 叠加 commit 链，commit 元数据（base/start SHA、sequence_index）写入 registry，sequential GitSync 本地 commit 不 push/PR，shared/sequential root 在 cleanup 时保留。19 个专项测试 + 245 个 orchestrator 回归全部通过。*

*版本 v2.10 更新: 新增 F-42 Orchestrator Shared / Sequential Workspace 策略设计。规划 `workspace.strategy: isolated | shared | sequential`，支持本地 feature-plan issue 在同一 working tree / integration branch 上按顺序叠加开发；保留旧 isolated 行为，并设计单并发校验、顺序锁、dirty tree guard、commit 链 registry 元数据、GitSync/cleanup preserve 语义与两 issue 端到端验收。*

*版本 v2.7 更新: 新增 F-41 Coordinator 轻量工具集。扩展 `_COORDINATOR_ALLOWED_TOOLS` 使 Coordinator 获得 Read / WebSearch / WebFetch 三个轻量工具，合计 6 个。写/执行工具仍隔离，强制委派给 Worker。提示词同步更新。231/231 orchestrator 测试通过。*

*版本 v2.6 更新: 修复 `progress_reporter` 死代码,phase completion 接入 ndjson event log (F-38 Sub-D 落地)。新增 F-40 ProgressReporter Sink 协议重构。Sub-A 引入 `ProgressSink` 协议 + `CompositeProgressSink` 扇出;Sub-B `ToolContextProgressSink` 替代原 `ProgressReporter` 行为;Sub-C `AgentRunner` 三个事件全部转发,session 结束有进度落点;Sub-D `Orchestrator` 取消单例改为每 session 独立 sink;Sub-E `WorkflowConfig.phases` 做真实进度计算,淘汰 `phase_count * 25` 假数据;Sub-F `ProgressReporter` 降级为 shim 保持向后兼容;Sub-G 并发回归 + 三事件测试覆盖。

---

*版本 v2.7 更新: 新增 F-51 AgentRunner 空转检测、F-44 Orchestrator 人工检视闸门、F-50 POS 转换器源码固化等 3 项已实现任务进度归档。*

---

## 五.12 F-51 AgentRunner 空转检测机制（no-op detection）

**状态**: ✅ 完成
**优先级**: P0

### 问题现状

当 Orchestrator 处理某个 issue 时，如果该 issue 的 deliverables 已经在 base branch 中存在（例如通过上游 commit 预置），agent 会进入一个无意义循环：

1. Agent 读取 issue 描述 → 要求"新建"某功能
2. 搜索代码发现功能已存在 → 不知道该怎么办
3. 跑 `python3 --version` / `date` / `print("x")` 等 busy-work 命令
4. 每轮无文件变更 → 但 session.status 仍是 "continue"
5. 耗尽全部 max_turns（40轮，~150 次 API 调用）
6. session.status = "max_turns_exceeded" → Orchestrator 调度 retry
7. ↻ 无限循环，直到人工干预

### 故障链

| 层次 | 问题 | 修复 |
|------|------|------|
| Prompt | 无处理"已实现"的指令 | workflow.md Step 3.5：如果 deliverables 已存在且验证通过，直接完成 |
| Agent Loop | 无工作区变更检测 | `get_file_status(workspace)` 每轮检查，连续 5 轮 clean 则 force complete |
| Git Sync | 无文件变更时仍走完整流程 | `GitSyncService.changed=False` 正确跳过 commit/push |
| Registry | 无 "无变更但通过" 的状态 | 复用 `completed` + 日志记录 no-op 原因 |

### 实施

**文件**: `extensions/orchestrator/agent_runner.py`

| 修改 | 说明 |
|------|------|
| `import get_file_status` | 从 `src.utils.git` 导入工作区脏检测 |
| `_NOOP_DETECTION_MAX_TURNS = 5` | 连续 5 轮无文件变更即判定为空转 |
| `consecutive_clean_turns` 追踪 | 在 run() 的 continue 路径中累积计数器 |
| `if dirty: reset` / `else: increment & check` | 有变更清零，无变更累积，>=5 时 force-complete |
| `session.status = "completed"; return` | 直接退出 agent 循环，不触发 retry |
| `logger.warning("No-op detection triggered")` | 关键审计日志记录 |

### 验收

1. Agent 遇到已存在的 issue deliverables → 运行 ≤5 轮后自动完成
2. Agent 正在产出代码（有文件变更）→ 不受影响，空转计数器持续清零
3. 日志中出现 `No-op detection triggered issue_id=X` 记录
4. Orchestrator 不 retry，issue 标记为 completed
5. 增量轮次成本：每次 SessionComplete 读取一次 `get_file_status()`（<1ms）

---

## 五.13 F-44 Orchestrator 人工检视闸门（Review Gate）

**状态**: ✅ 完成
**优先级**: P1

### 目标

为 Orchestrator 自动开发流程添加可选的人工检视闸门，实现"自动开发 + 人工合并"的协作模式，对应选项 A 架构。

### 当前基线

- GitSyncService 已有 `pending_review` 状态位，但仅 `LocalTracker` 下触发
- `Orchestrator.run_issue()` 的 `finally` 块中 `mark_completed()` 会覆盖 `pending_review` 状态
- CLI 已有 `issue review --approve/--reject` 命令，但从未被触发
- 远程 tracker（GitHub/Gitee/GitCode）没有人工检视环节

### 实施进度

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 配置字段 | `schema.py` | ✅ 完成 | `AgentConfig.review_required: bool = False` + `from_dict` 解析 |
| 同步层 | `git_sync.py` | ✅ 完成 | `pending_review` 条件扩展为 `is_local_tracker or review_required` |
| 编排器 | `orchestrator.py` | ✅ 完成 | `finally` 块跳过 `mark_completed()` 当 `pending_review` 存在 |
| 工作流配置 | `workflow.md` | ✅ 完成 | `review_required: true` 示例 |
| 测试 | 全部测试 | ✅ 通过 | 82 个 orchestrator 测试无回归 |

### 文件变更

| 文件 | 改动 |
|------|------|
| `extensions/orchestrator/config/schema.py` | +6 行：新字段 + `from_dict` 解析 |
| `extensions/orchestrator/git_sync.py` | +1 行：`pending_review` 条件扩展 |
| `extensions/orchestrator/orchestrator.py` | +16 行：`finally` 块检测修复 |
| `workflow.md` | +1 注释：开启 `review_required: true` |

### 验收标准

1. `review_required: false` → 行为不变，不阻塞任何现有流程
2. `review_required: true` + 有代码变更 → 状态为 `PENDING_REVIEW`
3. `clawcodex-dev orchestrator issue review --id <id> --approve` → 状态变 `COMPLETED`
4. `clawcodex-dev orchestrator issue review --id <id> --reject --feedback "..."` → 自动 retry
5. Orchestrator 重启后 `PENDING_REVIEW` 状态持久化，CLI 可继续操作

---

## 五.14 F-50 POS 转换器源码固化（SourceCodeParser + 增强 SkillGrouper + AgentMarkdownWriter）

**状态**: ✅ 完成
**优先级**: P1

### 目标

将 AscendDataForge 实践中手工完成的 Python 源码 → Agent 转换逻辑固化为三个可复用模块，集成到现有 `extensions/pos_converter/` 中，使 `clawcodex-dev pos convert ./组件目录 --out .claude` 直接可工作。

| 模块 | 文件 | 说明 |
|------|------|------|
| `SourceCodeParser` | `extensions/pos_converter/source_parser.py` | Python 源码 AST 解析：类/方法/docstring/参数/依赖 |
| 增强 SkillGrouper 策略 | `extensions/pos_converter/skill_grouper.py` | 新增 `GroupStrategy`，支持组件级/IO 关联/LLM 分组 |
| `AgentMarkdownWriter` | `extensions/pos_converter/agent_md_writer.py` | 生成 `.claude/agents/*.md` + `.atomcode/skills/*/SKILL.md` |
| 总览 Agent | `extensions/pos_converter/agent_md_writer.py` | `write_overview_agent()` **始终**生成工作流总览入口 |
| 默认 Agent 替换机制 | `extensions/pos_converter/default_agent.py` | `resolve_default_agent()` 检测 `clawcodex-overview.md` 并替换默认 agent |

### 子特性

1. **SourceCodeParser** — `ast.parse()` 递归扫描 `.py` 文件，输出 `SourceComponent[]`
2. **GroupStrategy 枚举** — `KEYWORD_MATCH` / `COMPONENT_GROUP` / `IO_RELATION` / `LLM_SEMANTIC`
3. **AgentMarkdownWriter** — 生成 CLI 可加载的 agent markdown 文件（完整 frontmatter + 技能参考）
4. **总览 Agent（Overview Agent）** — `pos convert` **始终**生成工作流总览 agent，知晓所有子 agent 的职责和调用链
5. **默认 Agent 替换** — `clawcodex-overview.md` 命名约定 + `--agent` CLI 参数，启动时自动替换默认 `GENERAL_PURPOSE_AGENT`
6. **CLI 兼容增强** — `clawcodex-dev pos convert <dir> --out .claude --strategy component`
7. **测试** — `tests/test_pos_converter_source_parser.py` + 回归

### 当前基线

- `extensions/pos_converter/` 已有三层架构：`SdkParser` → `SkillGrouper` → `AgentBuilder`
- `clawcodex-dev pos convert` CLI 已注册，支持 OpenAPI / 逗号分隔方法列表
- `SdkParser` 仅有 `_parse_openapi()` 和 `_parse_simple_list()`，不支持 Python 源码
- `SkillGrouper` 仅有 `_static_group()`（MappingRule 关键字匹配），无组件级/IO 关联分组
- `AgentBuilder.write_agent_markdown()` 输出极简 YAML，缺少完整 frontmatter 和技能参考嵌入
- **缺少总览 Agent 生成** — 当前无任何总览/入口 agent 概念
- **缺少默认 Agent 替换机制** — `GENERAL_PURPOSE_AGENT` 硬编码

### 实施进度

| 组件 | 文件 | 状态 | 说明 |
|------|------|------|------|
| SourceComponent / SourceOperation / ParamSpec 数据类 | `source_parser.py` | ✅ 完成 | 从 `pos2agent_ascend_dataforge.py` 中提取 schema |
| ModuleWalker — 递归扫描 .py 文件 | `source_parser.py` | ✅ 完成 | `ast.parse()` + 文件发现 |
| ClassExtractor — 提取类/方法 | `source_parser.py` | ✅ 完成 | AST 类定义 + 方法签名 |
| DocstringParser — docstring 结构化提取 | `source_parser.py` | ✅ 完成 | Google/NumPy/reST 兼容 |
| DependencyAnalyzer — import 图分析 | `source_parser.py` | ✅ 完成 | import 语句 → 组件依赖 |
| GroupStrategy 枚举 + 组件级分组 | `skill_grouper.py` | ✅ 完成 | 增量修改，向后兼容 |
| IO 关联分组 | `skill_grouper.py` | ✅ 完成 | 参数类型匹配跨组件归组 |
| LLM 语义分组占位 | `skill_grouper.py` | ✅ 完成 | 填充 `group_with_llm()` |
| AgentMarkdownWriter — agent markdown 生成 | `agent_md_writer.py` | ✅ 完成 | `.claude/agents/*.md` 格式 |
| AgentMarkdownWriter — skill markdown 生成 | `agent_md_writer.py` | ✅ 完成 | `.atomcode/skills/*/SKILL.md` 格式 |
| AgentMarkdownWriter — WORKFLOW.md 生成 | `agent_md_writer.py` | ✅ 完成 | orchestrator 编排文件骨架 |
| CLI `--out` / `--skills` / `--strategy` 参数 | `commands.py` | ✅ 完成 | 增量修改 |
| CLI 源码目录 vs 方法名自动判断 | `commands.py` | ✅ 完成 | 目录存在检测 |
| AgentBuilder `format` 参数 | `agent_builder.py` | ✅ 完成 | `agent_definition` / `markdown` / `both` |
| 模板（agent / skill markdown） | `templates.py` | ✅ 完成 | Jinja2 模板 |
| AgentMarkdownWriter — 总览 Agent 生成 | `agent_md_writer.py` | ✅ 完成 | `write_overview_agent()` + 数据类 |
| AgentBuilder — 总览 Agent 自动调用 | `agent_builder.py` | ✅ 完成 | `build()` 检测多组件 → 自动生成 |
| `resolve_default_agent()` | `default_agent.py` | ✅ 完成 | 扫描 `.claude/agents/clawcodex-overview.md` |
| `--agent CLI` 参数 | `commands.py` + repl | ✅ 完成 | 启动时指定默认 agent |
| 启动 Agent 标识 Banner | `dispatch.py` | ✅ 完成 | `_resolve_startup_agent()` stderr 输出 |
| 单元测试 | `test_pos_converter_source_parser.py` | ✅ 完成 | 33 个测试覆盖提取/分组/生成 |
| E2E 验收 | — | ✅ 完成 | 33/33 测试通过，回归 271/271 通过 |

### 验收标准

1. `clawcodex-dev pos convert 组件/视频算子 --out .claude` 生成 `.claude/agents/video-ops-agent.md`，`load_agents_dir.py` 可解析加载
2. 多组件目录自动生成 `.claude/agents/clawcodex-overview.md`，包含工作流概述和子 Agent 委派指引
3. `SourceCodeParser` 正确提取 AscendDataForge 所有组件的类/方法/docstring/参数/依赖
4. 生成的 SKILL.md 包含完整操作源码片段和参数说明
5. 总览 Agent 的 system prompt 包含所有 `AgentComponentInfo` 和 `WorkflowStage` 描述
6. `resolve_default_agent()` 检测 `clawcodex-overview.md` 时返回对应 agent definition；未找到时返回 None，不改变启动行为
7. 所有新增测试通过：`python3 -m pytest tests/test_pos_converter*.py -q`
8. 现有 `extensions/pos_converter` 测试继续通过

---
