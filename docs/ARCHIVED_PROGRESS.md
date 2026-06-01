# ClawCodex 已归档进度详情

> 文档路径: `docs/ARCHIVED_PROGRESS.md`
> 源文档: `docs/PROGRESS.md` 第二节 (已完成任务详情)
> 版本: v1.1
> 创建日期: 2026-05-30
> 最后更新: 2026-06-01
> 新增归档: R-7 LiteLLM Provider、F-23 Skills System Extension、F-1 子特性归档（F-1.1~F-1.4 强化、F-1.5~F-1.11 三通道澄清、F-1.13 CLI 运维界面）

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

## 四、规划任务概览

以下任务状态为"规划中"、"设计完成"或"待开始"，未归档至本文档：

| ID | 任务 | 优先级 | 状态 |
|----|------|--------|------|
| F-2 | Team 成员管理 (Phase-7) | P1 | ⏳ 规划中 |
| F-4 | 结构化输出集成 | P2 | 🔄 进行中 |
| F-5 | Voice Mode | P3 | ⏳ 待开始 |
| F-6 | Computer Use | P3 | ⏳ 待开始 |
| F-7 | Remote Control | P2 | ⏳ 待开始 |
| F-8 | ACP/Zed/Cursor 集成 | P3 | ⏳ 待开始 |
| F-9 | /goal 命令 | P2 | ⏳ 待开始 |
| F-10 | ExecuteExtraTool 延迟工具系统 | P2 | ⏳ 待开始 |
| F-11 | sessionStorage 容量限制 | P2 | ⏳ 待开始 |
| F-12 | cacheWarning 容量限制 | P2 | ⏳ 待开始 |
| F-16 | Auto 模式 (TRANSCRIPT_CLASSIFIER) | P2 | ⏳ 待开始 |
| F-18 | CreateAgentTool 动态工具创建 | P2 | 🔄 规划中 |
| F-22 | Cron 系统执行引擎 | P0 | 🔄 进行中 |
| F-26 | Away-Summary（离开摘要） | P2 | 📋 规划中 |
| F-28 | Ctrl+B Agent 后台持续运行 + `--resume` 恢复会话 | P1 | 🔄 设计完成 |
| F-33 | REPL 模式 Ctrl+B 后台运行支持 | P2 | 📋 规划中 |
| F-36 | LocalTracker 本地 Issue 文档源 | P1 | 📋 设计完成 |
| F-37 | Orchestrator PR 检视意见自动修复闭环 | P0 | 📋 设计完成 |
| F-38 | Orchestrator 验证与报告闭环（verification + report → PR） | P0 | 📋 设计完成 |
| F-39 | Orchestrator Issue 重跑入口（label + comment 命令双通道） | P0 | 📋 设计完成 |
| R-8 | 工具语义搜索 (Qdrant) | P2 | ⏳ 待开始 |
| R-9 | 权限规则引擎 (Casbin) | P2 | ⏳ 待开始 |
| R-10 | 日志系统 (structlog) | P2 | ⏳ 待开始 |

---

*本文档由 `docs/PROGRESS.md` 第二节归档生成，最后更新于 2026-06-01*