# ClawCodex 开发进度跟踪文档

> 文档路径: `docs/PROGRESS.md`
> 基于: `docs/open-source-replacement-progress.md`, `docs/FEATURE_PLAN.md`
> 版本: v1.3
> 更新日期: 2026-05-20

---

## 一、任务总览

### 1.1 开源替代组件

| ID | 组件 | 原始实现 | 替代方案 | 代码减少 | 优先级 | 状态 |
|----|------|---------|---------|----------|--------|------|
| R-1 | 配置系统 | 手动 JSON 管理 (~220 行) | Pydantic-settings | ~220 行 | P0 | ✅ 完成 |
| R-2 | Frontmatter 解析 | yaml.safe_load (~80 行) | python-frontmatter | ~80 行 | P1 | ✅ 完成 |
| R-3 | Bash AST 解析器 | 自建 ~1,500 行 | tree-sitter-bash | ~1,400 行 | P0 | ✅ 完成 |
| R-4 | Git 操作 | 6 个 subprocess.run() (~200 行) | GitPython | ~200 行 | P1 | ✅ 完成 |
| R-5 | Hook 系统 | 自建 ~1,200 行 | Pluggy | ~1,000 行 | P1 | ✅ 完成 |
| R-6 | 结构化输出 | json.loads + 手动验证 (~200 行) | Outlines | ~200 行 | P1 | ✅ 完成 |
| R-7 | Provider 层 | 多个 Provider 类 (~1,630 行) | LiteLLM | ~1,430 行 | P0 | 🔄 进行中 |
| R-8 | 工具语义搜索 | 手动实现 (~100 行) | Qdrant | ~100 行 | P2 | ⏳ 待开始 |
| R-9 | 权限规则引擎 | 手动实现 (~150 行) | Casbin | ~150 行 | P2 | ⏳ 待开始 |
| R-10 | 日志系统 | print/logging | structlog | - | P2 | ⏳ 待开始 |

**总计已减少代码**: ~3,100 行
**预计全部完成后减少**: ~4,530+ 行

### 1.2 功能模块开发

| ID | 模块 | 优先级 | 状态 | 备注 |
|----|------|--------|------|------|
| F-1 | Orchestrator 自主模式 | P0 | ✅ 完成 | Symphony 集成 |
| F-2 | Team 成员管理 (Phase-7) | P1 | ⏳ 规划中 | members 数组 |
| F-3 | MCP 协议扩展 | P1 | ✅ 基础完成 | Stdio/HTTP/SSE/WS |
| F-4 | 结构化输出集成 | P2 | 🔄 进行中 | Outlines 适配器已就绪 |
| F-5 | Voice Mode | P3 | ⏳ 待开始 | 对标 CCB |
| F-6 | Computer Use | P3 | ⏳ 待开始 | 对标 CCB |
| F-7 | Remote Control | P2 | ⏳ 待开始 | Docker + WebUI |
| F-8 | ACP/Zed/Cursor 集成 | P3 | ⏳ 待开始 | IDE 集成 |
| F-9 | /goal 命令 | P2 | ⏳ 待开始 | 长时间任务目标管理 |
| F-10 | ExecuteExtraTool 延迟工具系统 | P2 | ⏳ 待开始 | TF-IDF 工具搜索 + 子代理执行 |
| F-11 | sessionStorage 容量限制 | P2 | ⏳ 待开始 | 防止 daemon 会话内存泄漏 |
| F-12 | cacheWarning 容量限制 | P2 | ⏳ 待开始 | 防止 source 类型内存泄漏 |
| F-13 | Agent 记忆作用域隔离 | P1 | ✅ 完成 | 按需加载不同作用域记忆 |
| F-14 | 三层解耦架构（Layer Isolation） | P1 | ✅ 完成 | upstream/capabilities/features 三层分离，零层违规 |

---

## 二、已完成任务详情

### F-14: 三层解耦架构（Layer Isolation）

**状态**: ✅ 完成
**完成日期**: 2026-05-20
**优先级**: P1

#### 背景

`src/api/query.py` 在 `stream()` 方法内部直接导入 `src.entrypoints.headless` 和 `src.tool_system.agent_loop`，这违反了 layer isolation 约束——features 层（api）不能直接依赖 upstream。

#### 实现方案

```
src/api/query.py
    ↓ imports（在 stream() 方法内，运行时）
src/capabilities/headless_runner.py  ← 函数内部懒加载，不在模块路径上
    ↓                                   （upstream import 在 headless_runner.py 函数内部）
src/entrypoints/headless.py          ← 上游模块（运行时才加载）
```

#### 解耦实现

1. **新增 `src/capabilities/event_protocol.py`** — 定义 `ToolEventProtocol`，从 `src/tool_system/agent_loop.ToolEvent` 提取接口契约
2. **新增 `src/capabilities/headless_protocol.py`** — 定义 `HeadlessOptionsProtocol` 和 `HeadlessRunnerProtocol`
3. **新增 `src/capabilities/headless_runner.py`** — `HeadlessSessionOptions` + `run_headless_session()` 函数，通过环境变量 `CLAW_HEADLESS_BACKEND=stub` 可在测试中完全绕过上游
4. **更新 `src/api/query.py`** — 类型标注改用 Protocol 接口，运行时通过 `run_headless_session()` 分发，不再直接引用上游

#### upstream-sync 配置更新

- `src/api` 加入 `features` 层（与 `orchestrator` 同层）
- `upstream-sync audit` 验证：**零层违规**

#### 关键文件

- `src/capabilities/event_protocol.py` - ToolEvent 接口协议
- `src/capabilities/headless_protocol.py` - HeadlessOptions / HeadlessRunner 接口协议
- `src/capabilities/headless_runner.py` - 可插拔后端分发器
- `src/api/query.py` - 运行时零上游耦合
- `upstream-sync.yaml` - `src/api` 加入 features 层

#### 解耦结果

| 组件 | 上游直接引用 | 运行时耦合 |
|------|------------|-----------|
| `src/orchestrator/` | ❌ 无 | ✅ 通过 headless_runner 间接 |
| `src/api/query.py` | ❌ 无 | ✅ 通过 headless_runner 间接 |
| `src/api/orchestration.py` | ❌ 无 | ✅ 只调用 orchestrator 内部 |
| `src/capabilities/` | ❌ 无 | ✅ 只定义 Protocol，无实现 |

#### 测试验证

```
tests/test_layer_isolation.py: 12/12 通过
upstream-sync audit: 零层违规
CLAW_HEADLESS_BACKEND=stub: 不加载任何上游模块
```

---

### R-1: Pydantic-settings 替换配置系统

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
当前 `src/config.py` 使用手动 JSON 配置管理，包括三层配置层级 (global > project > local) 和手动深合并实现 (`_deep_merge`)。

#### 实现方案
引入 `pydantic-settings` 作为配置底层引擎，保留现有 ConfigManager API。

#### 解耦设计
```
src/config.py (保留 ConfigManager 接口)
    ↓
src/settings/pydantic_adapter.py (新 Pydantic Settings 适配器)
    ↓
pydantic-settings + python-dotenv (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/settings/pydantic_adapter.py` 适配器模块
- [x] 定义 `ClawCodexSettings(BaseSettings)` 类
- [x] 实现 `load_settings_from_config_manager()` 桥接函数
- [x] 添加 `pydantic-settings>=2.0.0` 到 pyproject.toml 依赖
- [x] 创建测试文件 `tests/test_pydantic_adapter.py`
- [x] 所有测试通过 (9 个新测试 + 14 个原配置测试 + 24 个设置测试)

#### 关键文件
- `src/settings/pydantic_adapter.py` - Pydantic Settings 适配器
- `tests/test_pydantic_adapter.py` - 适配器测试

#### 问题与解决方案
(无)

---

### R-2: python-frontmatter 替换 frontmatter 解析

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
当前 `src/skills/frontmatter.py` 使用 `yaml.safe_load` 手动解析 frontmatter。

#### 实现方案
使用 `python-frontmatter` 库替换手动解析逻辑。

#### 解耦设计
```
src/skills/frontmatter.py (保留 parse_frontmatter 接口)
    ↓
src/skills/_frontmatter_adapter.py (适配器层)
    ↓
python-frontmatter (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/skills/_frontmatter_adapter.py` 适配器模块
- [x] 实现 `parse_frontmatter_with_library()` 函数
- [x] 添加 `python-frontmatter>=1.0.0` 到 pyproject.toml 依赖
- [x] 创建测试文件 `tests/test_frontmatter_adapter.py`
- [x] 所有测试通过 (9 个测试)

#### 关键文件
- `src/skills/_frontmatter_adapter.py` - python-frontmatter 适配器
- `tests/test_frontmatter_adapter.py` - 适配器测试

---

### R-3: tree-sitter-bash 替换 Bash AST 解析器

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
`src/permissions/bash_parser/` 包含 ~1,500 行自建 Bash AST 解析器。

#### 实现方案
使用 `tree-sitter-bash` 替换自建解析器（最初使用 bashlex，但 bashlex 采用 GPL v3+ 许可证与 ClawCodex 的 MIT 许可证不兼容）。

#### 解耦设计
```
src/permissions/bash_parser/ (保留原有公共接口)
    ↓
src/permissions/_treesitter_adapter.py (适配器层)
    ↓
tree-sitter + tree-sitter-bash (开源依赖, MIT 许可证)
```

#### 完成的工作
- [x] 创建 `src/permissions/_treesitter_adapter.py` 适配器模块
- [x] 实现 `parse_command_with_bashlex()` 和 `classify_command_with_bashlex()` 函数
- [x] 添加 `tree-sitter>=0.25.0` 和 `tree-sitter-bash>=0.25.0` 到 pyproject.toml 依赖
- [x] 移除 bashlex 依赖 (GPL v3+ 许可证不兼容)
- [x] 所有测试通过 (16 个测试)

#### 关键文件
- `src/permissions/_treesitter_adapter.py` - tree-sitter-bash 适配器
- `tests/test_treesitter_adapter.py` - 适配器测试

#### 问题与解决方案
- **bashlex 许可证问题**: bashlex 使用 GPL v3+ 许可证，与 ClawCodex 的 MIT 许可证不兼容
- **解决方案**: 替换为 tree-sitter-bash，采用 MIT 许可证，完全兼容

---

### R-4: GitPython 替换 Git 子进程调用

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
`src/context_system/git_context.py` 使用 6 个并发 `subprocess.run()` 调用 Git 命令。

#### 实现方案
使用 `GitPython` 替换子进程调用。

#### 解耦设计
```
src/context_system/git_context.py (保留原有接口)
    ↓
src/context_system/_gitpython_adapter.py (适配器层)
    ↓
GitPython (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/context_system/_gitpython_adapter.py` 适配器模块
- [x] 实现 `GitPythonProvider` 类和 `collect_git_context_with_gitpython()` 函数
- [x] 添加 `GitPython>=3.1.0` 到 pyproject.toml 依赖
- [x] 创建测试文件 `tests/test_gitpython_adapter.py`
- [x] 所有测试通过 (9 个测试)

#### 关键文件
- `src/context_system/_gitpython_adapter.py` - GitPython 适配器
- `tests/test_gitpython_adapter.py` - 适配器测试

---

### R-5: Pluggy 替换 Hook 系统

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
`src/hooks/` 包含 ~1,200 行自建 Hook 系统（28 个事件）。

#### 实现方案
使用 `Pluggy` 替换自建 Hook 系统。

#### 解耦设计
```
src/hooks/ (保留原有 Hook 接口)
    ↓
src/hooks/_pluggy_adapter.py (适配器层)
    ↓
pluggy (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/hooks/_pluggy_adapter.py` 适配器模块
- [x] 实现 ClawCodexHooks 规范定义
- [x] 实现 HookManager 类
- [x] 所有测试通过

#### 关键文件
- `src/hooks/_pluggy_adapter.py` - Pluggy 适配器

---

### R-6: Outlines 引入结构化输出

**状态**: ✅ 完成
**完成日期**: 2026-05-17

#### 背景
项目中散落多处 `json.loads` + 手动验证代码。

#### 实现方案
引入 `Outlines` 用于结构化输出（预生成约束，非后验证）。

#### 解耦设计
```
src/agent/ (使用结构化输出的模块)
    ↓
src/agent/_outlines_adapter.py (适配器层)
    ↓
Outlines (开源依赖)
```

#### 完成的工作
- [x] 创建 `src/agent/_outlines_adapter.py` 适配器模块
- [x] 实现 `ToolCallDecision` 等结构化模型
- [x] 添加 `outlines` 到 pyproject.toml 依赖
- [x] 适配器就绪，待集成到各模块

#### 关键文件
- `src/agent/_outlines_adapter.py` - Outlines 适配器

---

## 三、进行中任务

### F-13: Agent 记忆作用域隔离

**状态**: ✅ 完成
**完成日期**: 2026-05-19
**优先级**: P1

#### 背景
在多 Agent 协作场景下，不同 Agent 可能需要访问不同范围的信息。传统的记忆系统是单例模式，所有 Agent 共享相同的记忆目录，无法满足按需隔离的需求。

#### 实现方案
在 `AgentDefinition` 中添加 `memory` 字段，支持指定 Agent 可访问的记忆作用域。核心 API `load_memory_prompts()` 支持传入作用域列表按需加载。

#### 支持的作用域
| 作用域 | 说明 |
|--------|------|
| `user` | 用户/私有记忆 |
| `project` | 项目上下文记忆 |
| `reference` | 外部系统指针 |
| `team` | 团队共享记忆 |
| `local` | 会话级本地记忆 |

#### 完成的工作
- [x] 添加 `load_memory_prompts()` 函数到 `memdir/memdir.py`
- [x] 添加 `_load_memory_prompt_for_scope()` 和 `_get_memory_path_for_scope()` 辅助函数
- [x] 导出 `load_memory_prompts` 到 `memdir/__init__.py`
- [x] 更新 `build_full_system_prompt()` 支持 `memory_scopes` 参数
- [x] 更新 `build_full_system_prompt_blocks()` 支持 `memory_scopes` 参数
- [x] 更新 `_build_memory_section()` 接受 `memory_scopes` 参数
- [x] 保持 `load_memory_prompt()` 向后兼容

#### 关键文件
- `src/memdir/memdir.py` - 核心 `load_memory_prompts()` 实现
- `src/memdir/memory_types.py` - 四种记忆类型定义
- `src/memdir/paths.py` - 记忆目录路径解析
- `src/context_system/prompt_assembly.py` - 支持 `memory_scopes` 参数
- `src/agent/agent_definitions.py` - `memory` 字段定义

#### API 使用方式
```python
# 按需加载特定作用域的记忆
memory_prompts = load_memory_prompts(['user', 'team'])

# 在 build_full_system_prompt 中使用
prompt = build_full_system_prompt(
    memory_scopes=['user', 'project'],  # Agent 按需指定
    ...
)
```

#### 问题与解决方案
(无)

---

### R-7: LiteLLM 替换 Provider 层

**状态**: 🔄 进行中
**优先级**: P0
**预计减少代码**: ~1,430 行

#### 背景
当前 `src/providers/` 包含多个 Provider 实现 (~1,630 行)。

#### 实现方案
使用 `LiteLLM` 统一 Provider 层，支持 100+ 模型。

#### 解耦设计
```
src/providers/base.py (保留 BaseProvider 抽象)
    ↓
src/providers/_litellm_adapter.py (LiteLLM 适配器)
    ↓
LiteLLM (开源依赖)
```

#### 进度
- [x] `src/providers/_litellm_adapter.py` 适配器文件已创建
- [x] 实现 `LiteLLMProvider` 类
- [ ] 集成到 Provider 注册系统
- [ ] 移除硬编码的 anthropic/openai/zhipuai 必装依赖
- [ ] 端到端测试

#### 注意事项
- LiteLLM 保留 `BaseProvider` 接口可回退
- Anthropic SDK 是可选依赖，仅在调用 Anthropic 模型时需要
- 当前 pyproject.toml 硬编码 `anthropic`、`openai`、`zhipuai` 三个 SDK

---

### F-1: Orchestrator 自主模式

**状态**: ✅ 完成
**完成日期**: 2026-05-20
**优先级**: P0

#### 目标
支持 `clawcodex --workflow WORKFLOW.md` 自主运行模式

#### 组件进度

| 组件 | 文件 | 状态 |
|------|------|------|
| Orchestrator | `orchestrator/orchestrator.py` | ✅ 完成 |
| WorkspaceManager | `orchestrator/workspace.py` | ✅ 完成 |
| LinearAdapter | `orchestrator/linear/adapter.py` | ✅ 完成 |
| LinearClient | `orchestrator/linear/client.py` | ✅ 完成 |
| Issue | `orchestrator/linear/issue.py` | ✅ 完成 |
| AgentRunner | `orchestrator/agent_runner.py` | ✅ 完成 |
| PromptBuilder | `orchestrator/prompt_builder.py` | ✅ 完成 |
| WorkflowLoader | `orchestrator/workflow.py` | ✅ 完成 |
| ApprovalPolicy | `orchestrator/approval_policy.py` | ✅ 完成 |
| StatusDashboard | `orchestrator/status_dashboard.py` | ✅ 完成 |
| TrackerAdapter | `orchestrator/tracker.py` | ✅ 完成 |
| GitSyncService | `orchestrator/git_sync.py` | ✅ 完成 |
| GitHub/Gitee/GitCode Adapter | `orchestrator/repo_tracker/adapter.py` | ✅ 完成 |
| Repository Issue Client | `orchestrator/repo_tracker/client.py` | ✅ 完成 |
| **重试上限保护** | `orchestrator/orchestrator.py` | ✅ 完成 |
| **Issue State 前置检查** | `orchestrator/orchestrator.py` | ✅ 完成 |
| **ClarificationQueue** | `orchestrator/clarification_queue.py` | ✅ 完成 |
| **TrackerAdapter 评论接口** | `orchestrator/tracker.py` + `repo_tracker/adapter.py` | ✅ 完成 |
| **Orchestrator CLI** | `orchestrator/cli/` | 🔄 进行中 |

#### 待完成

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 多 Tracker 支持 | ✅ 已完成 | GitHub/Gitee/GitCode REST 适配器已实现并通过实际测试（GitCode live test 完成 PR 创建） |
| 重试队列 + 退避 | ✅ 已完成 | 失败任务自动重试，指数退避机制已实现 |
| CLI 集成 | ✅ 已完成 | `--workflow` flag 已集成到 cli.py |
| **重试上限保护** | ✅ 已完成 | `_schedule_retry` 增加最大重试次数限制（建议默认 5 次），超过后停止自动重试 |
| **Issue State 前置检查** | ✅ 已完成 | `_launch_issue` 前调用 `tracker.fetch_issue_states_by_ids` 确认 issue 仍处于 active state，非 active 则跳过 |
| **已有 PR 跳过后续处理** | ✅ 已完成 | `_launch_issue` 前调用 `tracker.find_pull_request`，若存在已关联 PR 则标记 completed 并跳过 |
| **ClarificationQueue 文件队列** | ✅ 已完成 | `orchestrator/clarification_queue.py:ClarificationQueue` |
| **TrackerAdapter 评论接口** | ✅ 已完成 | `fetch_issue_comments()` / `fetch_new_comments_since()` / `create_clarification_comment()` |
| **Orchestrator CLI 统一入口** | 🔄 进行中 | `clawcodex orchestrator run/status/issues/clarify/pause/resume/stop/takeover` |
| **Issue Clarification 三通道** | 🔄 进行中 | Dashboard → ClarificationQueue → @mention 作者（Phase A-E） |
| **Orchestrator CLI 生命周期控制** | 🔄 进行中 | pause/resume/stop/takeover + `_process_control_commands()` |

#### Phase 3 生产强化详细设计

**F-1.1: 重试上限保护**

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_schedule_retry` |
| 新增字段 | `workflow.agent.max_retry_attempts: int = 5`（默认值 5） |
| 触发条件 | `attempt > max_retry_attempts` 时跳过调度，打印 warning 并从 `claimed` 集合移除 |
| 副作用 | 不写入 `completed`（需人工确认后手动关闭 issue） |

**F-1.2: Issue State 前置检查**

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_launch_issue`（创建 workspace 后、agent 运行前） |
| 检查方式 | 调用 `tracker.fetch_issue_states_by_ids([issue.id])`，若 state 不在 `active_states` 则跳过 |
| 副作用 | 从 `claimed` 集合移除，不进入 `completed` 集合 |

**F-1.3: 已有 PR 跳过后续处理**

| 项 | 值 |
|---|---|
| 实现位置 | `orchestrator/orchestrator.py:_launch_issue`（Issue State 检查之后） |
| 检查方式 | 调用 `tracker.find_pull_request(head_branch, base_branch)`，若存在 PR 则跳过 |
| 适用范围 | 仅 RepositoryTrackerAdapter（GitHub/Gitee/GitCode）；Linear 无 PR 概念，返回 None |
| 副作用 | 从 `claimed` 移除，写入 `completed`（重启后不重复处理） |

**F-1.4: 本地 Issue 注册表（持久化映射）**

| 项 | 值 |
|---|---|
| 文件位置 | `{workspace.root}/.clawcodex_issue_registry.json` |
| 实现文件 | `orchestrator/issue_registry.py:IssueRegistry` |
| 记录字段 | `issue_id / identifier / branch_name / commit_sha / pr_number / pr_url / status / attempt_count` |
| Status 枚举 | `PENDING → SYNCED → COMPLETED / FAILED / ABANDONED` |
| 启动时检查 | `_poll_and_dispatch` 遍历候选 issue 时跳过 `is_completed` 或 `has_pr` 的记录 |
| 注册时机 | `_launch_issue` workspace 创建后立即写入 PENDING |
| 更新时机 | `git_sync.sync()` 后写入 SYNCED + PR 信息；session 完成后写入 COMPLETED |
| Abandoned 时机 | 重试达到上限后标记为 ABANDONED |

#### 实施阶段

- [x] **Phase 1: Foundation (Week 1-2)** - 基础框架
- [x] **Phase 2: Agent Integration (Week 3-4)** - Agent 集成 + GitHub/Gitee/GitCode 支持
- [ ] **Phase 3: Production Hardening (Week 5-6)** - 重试上限保护 + Issue State 前置检查 + 冲突恢复
- [ ] **Phase 4: Issue Clarification (Week 7-8)** - 语义澄清流程
- [ ] **Phase 5: Observability (Week 9-10)** - 可观测性

#### Phase 4: Issue Clarification 详细设计

**F-1.5: Issue 语义澄清流程（三通道优先机制）**

| 项 | 值 |
|---|---|
| 核心思路 | **三通道优先机制**：本地操作员优先（Dashboard / ClarificationQueue），@mention 作者兜底 |
| 通道一 | StatusDashboard 交互提示（非 headless，操作员在线时即时响应） |
| 通道二 | ClarificationQueue 文件队列（~/.clawcodex/clarification_queue.json，操作员异步 CLI 应答） |
| 通道三 | @mention Issue 评论（操作员无响应后降级，完全异步等待作者回复） |
| 触发条件 | Agent 检测到 Issue 语义模糊，调用 AskIssueAuthor(question, context) 工具 |
| 降级时机 | 通道一 timeout（无 Dashboard 或 headless）→ 通道二 timeout（30min）→ 通道三（72h）→ escalation |
| 次数限制 | max_questions_per_issue（默认 3 次），超过后标记 EXHAUSTED |
| 状态机 | NONE → AWAITING_LOCAL → AWAITING_AUTHOR → RESOLVED / TIMED_OUT / EXHAUSTED |
| 持久化 | ClarificationQueue 文件 + IssueRegistry clarification_status + question_history |
| 平台约束 | GitHub/Gitee/GitCode 均无 DM/私信 API，外部通道唯一是 @mention 评论 |

**F-1.6: 三通道详细设计**

**通道一：StatusDashboard 交互提示**

| 项 | 值 |
|---|---|
| 文件 | `orchestrator/status_dashboard.py` |
| 触发条件 | 非 headless 模式 + 操作员在线 + `dashboard.interactive_clarification=true` |
| UI 形式 | 面板中内联选项列表（1-4 选项 + 跳过 + 转发给作者） |
| 优点 | 响应最快（即时），操作员可结合代码上下文判断 |
| 降级条件 | headless 模式或 timeout（默认 5 分钟无操作） |

**通道二：ClarificationQueue 文件队列**

| 项 | 值 |
|---|---|
| 文件 | `orchestrator/clarification_queue.py:ClarificationQueue` |
| 队列路径 | `~/.clawcodex/clarification_queue.json` |
| CLI 命令 | `clawcodex clarify --issue <id> --answer <text>` |
| 轮询机制 | Orchestrator 每轮 poll 检查队列（与 Issue 轮询同步） |
| 超时 | timeout_local_minutes（默认 30 分钟），过期后降级通道三 |
| 优点 | 完全异步，操作员无需盯屏，不阻塞 orchestrator |
| 降级条件 | timeout_local_minutes 内无应答则发 @mention |

**通道三：@mention 评论**

| 项 | 值 |
|---|---|
| 接口 | `TrackerAdapter.create_clarification_comment()` → Issue 下发评论 + @mention |
| 轮询机制 | Orchestrator 轮询检查 Issue 新评论（每 poll_interval_ms） |
| 超时 | timeout_author_hours（默认 72 小时） |
| escalation | 超时后 skip / mark_failed / notify |

**F-1.7: ClarificationStatus 枚举（扩展）**

```python
class ClarificationStatus(str, Enum):
    NONE = "none"
    AWAITING_LOCAL = "awaiting_local"        # 等待本地操作员
    AWAITING_AUTHOR = "awaiting_author"     # 已发 @mention，等待作者
    RECEIVED = "received"
    RESOLVED_LOCAL = "resolved_local"        # 来自本地操作员
    RESOLVED_AUTHOR = "resolved_author"     # 来自 @mention 作者
    TIMED_OUT_LOCAL = "timed_out_local"     # 本地超时，降级通道三
    TIMED_OUT_AUTHOR = "timed_out_author"   # 作者超时
    EXHAUSTED = "exhausted"
```

**F-1.8: TrackerAdapter 评论接口扩展**

| 接口 | 说明 |
|------|------|
| `fetch_issue_comments(issue_id)` | 获取 Issue 所有评论，返回 `list[Comment]` |
| `create_clarification_comment(issue_id, body, mentions)` | 发评论并 @mention，触发通知 |
| `find_clarification_replies(since_comment_id)` | 查找某条评论之后的新回复（用于轮询增量检查） |

**F-1.9: IssueRegistry 澄清字段**

```python
clarification_status: ClarificationStatus = ClarificationStatus.NONE
question_history: list[str] = field(default_factory=list)
author_login: str | None = None
awaiting_since: float | None = None
last_checked_comment_id: str | None = None
local_answer: str | None = None           # 本地操作员的回答
local_answer_source: str | None = None    # "dashboard" | "clarification_queue"
```

**F-1.10: 关键约束 & 风险**

| 风险 | 缓解措施 |
|------|----------|
| 操作员不在线 + 作者不回复 | escalation 策略（skip/mark_failed/notify）+ 双通道降级 |
| Agent 反复提问 | max_questions_per_issue（默认 3 次）上限 |
| @mention 噪音 | 通道一二优先消耗模糊 Issue；仅置信度 > threshold（0.7）时触发 |
| 作者回复无效/误解 | LLM 重判定 + 计入重试次数 |
| 评论顺序错乱 | in_reply_to_comment_id + 时间戳重建对话树 |
| 重启丢失上下文 | ClarificationQueue 文件持久化 + IssueRegistry clarification_status |
| 多操作员同时应答 | ClarificationQueue 加锁；resolved 后其他应答者收到提示 |
| Headless 无 Dashboard | headless 模式下自动跳过通道一直达 ClarificationQueue |

**F-1.11: 多渠道冲突处理方案**

**问题场景**:

| 场景 | 描述 |
|------|------|
| 同时多渠道应答 | 操作员和作者在同一时间窗口内同时回答 |
| 超时后迟到 | 通道二超时升级通道三后，操作员的本地回答才到达 |
| 重复提交 | 同一渠道内同一答案被多次提交 |
| 升级通知丢失 | 操作员在不知情的情况下回答了已升级的 Issue |

**核心原则**:

| 原则 | 说明 |
|------|------|
| 第一响应者优先 | 第一个被 Orchestrator 检测到的有效答案被采纳 |
| 操作员优先级 | 操作员答案始终比作者更可信（`operator_priority: true`） |
| 单向升级不可逆 | 通道二超时 → 通道三后，原通道迟来答案标记 STALE_REJECTED |
| 过期主动通知 | 所有被拒绝的答案都要通知对应应答者，避免无谓等待 |
| 去重幂等 | 同一答案重复提交第二次标记 DUPLICATE_REJECTED |

**ClarificationStatus 扩展（冲突处理相关）**:

```python
DUPLICATE_REJECTED = "duplicate_rejected"   # 重复提交，被去重丢弃
STALE_REJECTED = "stale_rejected"           # 超时升级后收到的过时答案
CONFLICT_RESOLVED = "conflict_resolved"    # 多渠道冲突已裁决
```

**冲突处理状态机**:

```
收到任意渠道答案
        ↓
本通道第一响应？ → 否 → DUPLICATE_REJECTED 丢弃
        ↓ 是
当前 status = AWAITING_LOCAL:
    LOCAL 答案 → RESOLVED_LOCAL
    AUTHOR 答案（在 AWAITING_LOCAL 期间）→ RESOLVED_AUTHOR
    → 操作员收到："作者已先回复，您的窗口已关闭"

当前 status = AWAITING_AUTHOR:
    AUTHOR 答案 → RESOLVED_AUTHOR
    LOCAL 答案（在 AWAITING_AUTHOR 期间）→ STALE_REJECTED
    → 操作员收到："通道二已超时，@mention 已发出，您的回答已过时"

当前 status = TIMED_OUT_LOCAL / TIMED_OUT_AUTHOR / EXHAUSTED:
    任何答案 → STALE_REJECTED
    → 通知应答者："Issue 已超时升级/结束"
```

**同时应答检测**:

```python
# Orchestrator 同轮 poll 中同时检查 ClarificationQueue 和 Issue 评论
candidates = []
if local_item.answer:
    candidates.append(("local", answer, local_item.answered_at))
if author_comments:
    candidates.append(("author", latest.body, latest.created_at))

if len(candidates) > 1:
    delta_ms = abs(candidates[0][2] - candidates[1][2]) * 1000
    if delta_ms < 5000 and operator_priority:
        winner, loser = 0, 1   # 操作员优先
    else:
        winner = min(range(len(candidates)), key=lambda i: candidates[i][2])
    self._notify_rejected(candidates[1-winner][0], issue_id)
```

**超时告知机制**:

| 升级事件 | 通知内容 |
|---------|---------|
| 通道二超时 → 通道三 | "您的本地回答窗口已关闭，@mention 已发给作者" |
| 通道三超时 → escalation | "Issue #42 澄清超时，最终处理：skip/mark_failed/notify" |
| 迟到操作员答案（通道三之后） | "您的回答已过时，@mention 已发出，作者回复已被采纳" |
| 迟到作者答案（escalation 之后） | 忽略，不更新状态 |

**冲突场景汇总**:

| 场景 | 处理结果 | 是否通知 |
|------|---------|---------|
| T4a < T3（操作员先答） | RESOLVED_LOCAL | 无（正常） |
| T3 < T4a（作者先回复） | RESOLVED_AUTHOR | ✅ 操作员超时通知 |
| T4a ≈ T4b（同时 < 5ms） | 操作员优先 RESOLVED_LOCAL | ✅ 双方均通知 |
| 通道三已升级后操作员才答 | STALE_REJECTED | ✅ "已超时升级" |
| 多操作员同时写队列 | 先写入者 RESOLVED | ✅ 落败方"已被抢先" |
| 同一答案重复提交 | DUPLICATE_REJECTED | ❌ 幂等，无需通知 |

**新增配置**:

```yaml
agent:
  clarification:
    operator_priority: true        # 操作员答案优先于作者（默认 true）
    stale_notification: "all"      # "all" | "operator_only" | "none"
    simultaneous_grace_ms: 5000   # 5ms 内视为同时，由 operator_priority 决胜
```

**F-1.12: 实施检查清单**

- [x] Phase A: `ClarificationQueue` 文件队列 + Orchestrator 轮询逻辑（`orchestrator/clarification_queue.py`）
- [x] Phase A: 冲突处理状态机（`orchestrator/clarification.py`）：DUPLICATE_REJECTED / STALE_REJECTED / CONFLICT_RESOLVED
- [x] Phase A: 超时告知机制（escalation_notified + stale_notification）
- [x] Phase A: 同时应答检测逻辑（simultaneous_grace_ms + operator_priority）
- [x] Phase B: StatusDashboard 交互提示组件（`orchestrator/status_dashboard.py`）
- [x] Phase C: `AskIssueAuthor` 工具（`tool_system/tools/ask_issue_author.py`）
- [x] Phase C: `ClarificationResolver` 三通道降级 + 冲突裁决
- [x] Phase D: CLI `clarify` 子命令（`orchestrator/cli/clarify.py`）
- [x] Phase E: `TrackerAdapter.fetch_issue_comments()` / `create_clarification_comment()` 接口
- [x] Phase E: `RepositoryTrackerAdapter` 实现（GitHub / Gitee / GitCode）
- [x] Phase F: IssueRegistry 澄清字段持久化（`clarification_status`、`local_answer`、`first_response_source`、`stale_answers`）
- [x] Phase F: PromptBuilder 澄清内容注入（`prompt_builder.py`：`build_clarification_context()` + AgentRunner 集成）
- [x] Phase G: escalation 策略实现（skip / mark_failed / notify）

**F-1.13: Orchestrator CLI 运维操作界面**

| 命令 | 说明 | 优先级 | 状态 |
|------|------|--------|------|
| `clawcodex orchestrator run` | 启动 orchestrator（替代 `--workflow`，**不兼容变更**） | P1 | ✅ 完成 |
| `clawcodex orchestrator status` | 全局 running/paused/completed/failed 状态 | P1 | ✅ 完成 |
| `clawcodex orchestrator issues list` | 列出所有 issue 及状态 | P1 | ✅ 完成 |
| `clawcodex orchestrator issues tail <id>` | 实时 tail tool call 日志 | P1 | ✅ 完成 |
| `clawcodex orchestrator issues show <id>` | 查看 issue 详情（理解上下文、token 用量） | P1 | ✅ 完成 |
| `clawcodex orchestrator pause <id>` | 暂停 agent（停在当前 tool call 边界） | P1 | ✅ 完成 |
| `clawcodex orchestrator resume <id>` | 恢复暂停中的 agent | P1 | ✅ 完成 |
| `clawcodex orchestrator stop <id>` | 强制终止 agent | P1 | ✅ 完成 |
| `clawcodex orchestrator inject <id> "text"` | 向运行中的 agent 注入提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator inject <id> --list` | 查看已注入的提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator inject <id> --remove <n>` | 删除某条提示 | P1 | ✅ 完成 |
| `clawcodex orchestrator clarify --issue <id> --answer <text>` | 操作员澄清应答 | P1 | ✅ 完成 |
| `clawcodex orchestrator workspace <id> --ls` | 列出 workspace 文件 | P1 | ✅ 完成 |
| `clawcodex orchestrator workspace <id> --cat <file>` | 查看文件内容 | P1 | ✅ 完成 |
| `clawcodex orchestrator workspace <id> --edit <file> --with <content>` | 修改文件 | P2 | ✅ 完成 |
| `clawcodex orchestrator takeover <id>` | 完全接管（终止 + REPL） | P2 | ✅ 完成 |
| `clawcodex orchestrator dashboard --port` | 独立 dashboard UI | P2 | ✅ 完成 |

**不兼容变更**：

> ⚠️ `clawcodex --workflow` 将在发布时废弃，替换为 `clawcodex orchestrator run`。
> 这是唯一的 CLI 不兼容变更。现有启动命令：
> ```bash
> # 旧（将废弃）
> clawcodex --workflow test_gitcode_workflow.md
> # 新
> clawcodex orchestrator run --workflow test_gitcode_workflow.md
> ```
> release note 中需特别说明。

**实施检查清单**：

- [x] Phase O1: CLI `orchestrator` group 框架（`cli.py`：`clawcodex orchestrator`）
- [x] Phase O1: `orchestrator run`（替代 `--workflow`）
- [x] Phase O1: `orchestrator status` / `orchestrator issues list`
- [x] Phase O2: `orchestrator pause <id>` / `orchestrator resume <id>` / `orchestrator stop <id>`
- [x] Phase O2: Orchestrator pause/resume 状态支持（running → paused → running）
- [x] Phase O3: `orchestrator issues tail <id>`（AgentRunner event stream → 流式推送 tool calls）
- [x] Phase O3: StatusDashboard 实时渲染（event stream 消费）
- [x] Phase O4: `orchestrator inject` Hint 注入（`.operator_hints.md` 机制）
- [x] Phase O5: `orchestrator workspace --ls` / `--cat`（文件查看）
- [x] Phase O5: `orchestrator workspace --edit`（文件修改，协作场景）
- [x] Phase O6: `orchestrator takeover <id>`（终止 + REPL 接管）
- [x] Phase O7: `orchestrator clarify`（澄清应答，与 Phase D/C 澄清流程合并）
- [x] Phase O8: Dashboard LiveView 增强（event stream 完整推送 LLM 摘要 + tool calls）

---

## 四、规划任务

### F-2: Team 成员管理 (Phase-7)

**状态**: ⏳ 规划中
**优先级**: P1
**WI**: WI-6.4

#### 目标
扩展 `TeamCreate` 工具，使其能够跟踪和管理团队中的成员 Agent。

#### 数据模型

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

#### 核心机制

| 机制 | 说明 |
|------|------|
| TeammateInit | `agent(run_in_background=true)` 时自动注册到 `members` |
| 状态同步 | TaskOutput 显示 completed/failed 时更新成员状态 |
| 名称注册 | Agent 名称冲突检测 `agent_name_registry` |
| 递归 Fork 保护 | Fork Agent 无法嵌套调用 Fork |

#### 实现文件

| 文件 | 状态 |
|------|------|
| `tool_system/tools/team.py` | ✅ 已实现 TeamCreate/TeamDelete，members 数组已支持 |
| `services/swarm/team_file.py` | ✅ 已实现 TeamFile、TeamMember 数据模型 |
| `services/swarm/team_membership.py` | ✅ 已实现 is_team_lead() 函数 |
| `services/swarm/agent_name_registry.py` | ✅ 已实现名称注册表 |
| `tool_system/tools/agent.py` | ✅ 基础完成，TeammateInit 机制就绪 |

#### 测试覆盖

| 测试文件 | 测试用例 |
|----------|----------|
| `test_team_file.py` | `test_team_file_created_with_members_array`, `test_team_file_schema_members_array`, `test_team_file_missing_members_tolerated` |
| `test_team_membership.py` | `test_is_team_lead_true_*`, `test_is_team_lead_false_*` |

---

### R-8 到 R-10: 其他开源替代

| 任务 | 替代方案 | 优先级 | 状态 |
|------|---------|--------|------|
| 工具语义搜索 | Qdrant | P2 | ⏳ 待开始 |
| 权限规则引擎 | Casbin | P2 | ⏳ 待开始 |
| 日志系统 | structlog | P2 | ⏳ 待开始 |

---

### F-9: /goal 命令（目标管理）

**状态**: ⏳ 待实现
**优先级**: P2

#### 目标
支持长时间运行任务的目标管理，包括 set/pause/resume/complete 子命令。

#### 功能说明

| 子命令 | 功能 |
|--------|------|
| `/goal set <goal>` | 设置当前任务目标 |
| `/goal clear` | 清除目标 |
| `/goal pause` | 暂停目标追踪 |
| `/goal resume` | 恢复目标追踪 |
| `/goal complete` | 标记目标完成 |

#### 核心机制

| 机制 | 说明 |
|------|------|
| Goal 状态机 | `active` / `paused` / `budget_limited` / `complete` |
| Token 用量追踪 | 自动追踪当前 session 的 token 消耗 |
| Continuation Prompt | 目标状态自动注入到 continuation prompt |
| session-scoped 隔离 | 按 sessionId 管理独立的目标状态 |

#### 参考实现

- `commands/goal/goal.ts` - /goal 斜杠命令
- `services/goal/goalState.ts` - Goal 状态管理
- `packages/builtin-tools/src/tools/GoalTool/GoalTool.ts` - Goal 工具

#### 数据模型

```python
class GoalState(BaseModel):
    session_id: UUID
    goal: str
    status: Literal["active", "paused", "budget_limited", "complete"]
    created_at: datetime
    updated_at: datetime
    token_usage: dict  # {current: int, threshold: int}
```

---

### F-10: ExecuteExtraTool 延迟工具系统

**状态**: ⏳ 待实现
**优先级**: P2

#### 目标
实现完整的延迟工具按需加载系统，支持 TF-IDF 语义搜索和子代理执行。

#### 功能说明

| 组件 | 功能 |
|------|------|
| SearchExtraToolsTool | TF-IDF 工具索引语义搜索 |
| ExecuteExtraTool | 通过名称和参数执行延迟工具 |
| validateInput 校验 | 调用前校验防止崩溃 |
| ASYNC_AGENT_ALLOWED_TOOLS | 子代理可执行延迟工具 |

#### 核心机制

| 机制 | 说明 |
|------|------|
| 工具延迟加载 | 工具按名称和参数动态执行，非预加载 |
| 语义搜索 | TF-IDF 索引支持自然语言工具搜索 |
| 子代理执行 | Async Agent 可调用延迟工具 |
| 输入校验 | execute 前 validateInput 防止无效调用 |

#### 参考实现

- `packages/builtin-tools/src/tools/ExecuteTool/ExecuteTool.ts`
- `packages/builtin-tools/src/tools/SearchExtraToolsTool/`
- `constants/tools.ts` - ASYNC_AGENT_ALLOWED_TOOLS

#### 现有基础

clawcodex 已有 `tool_system/tool_search.py` 工具搜索实现，需扩展为 SearchExtraToolsTool 语义搜索。

---

### F-11: sessionStorage 容量限制

**状态**: ⏳ 待实现
**优先级**: P2

#### 目标
为 `existingSessionFiles` Map 设置容量上限，防止 daemon/swarm 会话内存泄漏。

#### 问题场景

- daemon/swarm 模式下长时间运行
- sessionId 频繁创建销毁
- Map 无限增长导致 OOM

#### 实现方案

```python
MAX_CACHED_SESSION_FILES = 200

class SessionStorage:
    def __init__(self):
        self.existing_session_files: dict[UUID, str] = {}

    def add_session_file(self, session_id: UUID, file_path: str):
        if len(self.existing_session_files) >= MAX_CACHED_SESSION_FILES:
            oldest_key = next(iter(self.existing_session_files))
            del self.existing_session_files[oldest_key]
        self.existing_session_files[session_id] = file_path
```

#### 参考实现

- `src/utils/sessionStorage.ts` - existingSessionFiles Map + MAX_CACHED_SESSION_FILES = 200

---

### F-12: cacheWarning 容量限制

**状态**: ⏳ 待实现
**优先级**: P2

#### 目标
为 `cacheWarningStateBySource` Map 设置容量上限，防止 querySource 类型为 any 时内存泄漏。

#### 问题场景

- querySource 类型为 any
- 长时间会话产生大量唯一 source 值
- Map 无限增长导致内存泄漏

#### 实现方案

```python
MAX_SOURCE_ENTRIES = 50

class CacheWarning:
    def __init__(self):
        self.cache_warning_state_by_source: dict[str, CacheWarningState] = {}

    def update(self, source: str, state: CacheWarningState):
        if len(self.cache_warning_state_by_source) >= MAX_SOURCE_ENTRIES:
            oldest_key = next(iter(self.cache_warning_state_by_source))
            del self.cache_warning_state_by_source[oldest_key]
        self.cache_warning_state_by_source[source] = state

    def reset_for_test(self):
        """测试隔离用"""
        self.cache_warning_state_by_source.clear()
```

#### 参考实现

- `src/utils/cacheWarning.ts` - cacheWarningStateBySource Map + MAX_SOURCE_ENTRIES = 50

---

## 五、不可替代组件

以下组件经过深入分析后被判定为不可替代：

| 组件 | 文件 | 不可替代原因 |
|------|------|-------------|
| Agent 执行循环 | `agent/run_agent.py` | 四级权限模型 (bubble/dontAsk/bypassPermissions/acceptEdits)、Subagent 隔离、消息完整性保证 |
| MCP 服务 | `services/mcp/` | 已完整实现 MCP 协议 (Stdio/HTTP/SSE/WebSocket)，替换成本过高 |
| Trust Boundary | `permissions/trust_boundary.py` | 环境变量安全白名单/黑名单是项目特定的信任模型 |
| Bridge/FlushGate | `services/bridge/` | 纯状态机，__slots__ 优化，无外部依赖 |

---

## 六、切换机制设计

每个适配器模块支持**运行时切换**底层实现，无需修改调用方代码。

### 设计模式

```
src/xxx/ (原有实现 - 作为回退)
    ↓ (可选)
src/xxx/_adapter.py (适配器层 - 支持切换)
    ↓
开源依赖 (如 tree-sitter-bash)
```

### 切换配置

每个适配器模块顶部的 `_USE_<ADAPTER>` 变量控制使用新实现还是原有实现：

```python
# src/permissions/_treesitter_adapter.py
_USE_TREESITTER = os.getenv("CLAW_USE_TREESITTER", "true").lower() in ("true", "1")

def parse_command(command: str):
    if _USE_TREESITTER:
        return _treesitter_parse(command)
    else:
        return _original_parse(command)  # 回退到原有实现
```

### 环境变量配置

| 适配器模块 | 环境变量 | 默认值 | 说明 |
|-----------|---------|-------|------|
| `_treesitter_adapter.py` | `CLAW_USE_TREESITTER` | `true` | 切换 Bash 解析器 |
| `_gitpython_adapter.py` | `CLAW_USE_GITPYTHON` | `true` | 切换 Git 操作 |
| `_pydantic_adapter.py` | `CLAW_USE_PYDANTIC_SETTINGS` | `true` | 切换配置系统 |
| `_frontmatter_adapter.py` | `CLAW_USE_FRONTMATTER_LIB` | `true` | 切换 frontmatter 解析 |
| `_litellm_adapter.py` | `CLAW_USE_LITELLM` | `false` | 切换 Provider 层 |
| `_pluggy_adapter.py` | `CLAW_USE_PLUGGY` | `false` | 切换 Hook 系统 |
| `_outlines_adapter.py` | `CLAW_USE_OUTLINES` | `false` | 切换结构化输出 |

### 切换原则

1. **已完成且验证稳定**: 默认使用新实现 (`true`)
2. **待完成或实验性**: 默认使用原有实现 (`false`)
3. **生产环境**: 可通过环境变量切换，便于快速回滚

### 验证流程

1. 新实现测试通过后，将对应环境变量设为 `true`
2. 发现问题时，将环境变量设为 `false` 切回原有实现
3. 修复后重新验证，验证通过后再切回新实现

---

## 七、里程碑

| 日期 | 里程碑 | 完成内容 |
|------|--------|----------|
| 2026-05-17 | Phase 1 完成 | Pydantic-settings, python-frontmatter, tree-sitter-bash, GitPython, Pluggy, Outlines 适配器完成 |
| 2026-05-17 | Orchestrator Phase 1-2 | 所有核心组件完成，CLI 集成待完成 |
| TBD | Phase 7 | Team 成员管理实现 |
| TBD | Phase 3-4 | Orchestrator 生产强化 + 可观测性 |

---

## 八、文档索引

| 文档 | 说明 |
|------|------|
| `docs/FEATURE_PLAN.md` | 特性规划总览 |
| `docs/PROGRESS.md` | 本文档 - 进度跟踪 |
| `docs/INTEGRATION.md` | Symphony 集成规范 |
| `docs/TEAM_MEMBERSHIP.md` | Team 成员扩展设计 |

---

*文档更新时间: 2026-05-20*

*版本 v1.3 更新：新增 F-14 三层解耦架构（Layer Isolation），`src/api/` 加入 features 层，`src/api/query.py` 通过 `capabilities/headless_runner.py` 实现运行时零上游耦合，upstream-sync audit 零层违规验证通过。*