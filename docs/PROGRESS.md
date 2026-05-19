# ClawCodex 开发进度跟踪文档

> 文档路径: `docs/PROGRESS.md`
> 基于: `docs/open-source-replacement-progress.md`, `docs/FEATURE_PLAN.md`
> 版本: v1.0
> 更新日期: 2026-05-18

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
| F-1 | Orchestrator 自主模式 | P0 | 🔄 进行中 | Symphony 集成 |
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

---

## 二、已完成任务详情

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

**状态**: 🔄 进行中
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

#### 待完成

| 功能 | 优先级 | 说明 |
|------|--------|------|
| 多 Tracker 支持 | P2 | **前置依赖**: 需先实现 GitHub/Gitee/GitCode 等远程仓库适配器；TrackerAdapter 协议已抽象，可扩展新 Tracker |
| 重试队列 + 退避 | ✅ 已完成 | 失败任务自动重试 |
| CLI 集成 | ✅ 已完成 | `--workflow` flag 已集成到 cli.py |
| 重试队列 + 退避 | ✅ 已完成 | 指数退避重试机制已实现 |
| 可观测性集成 | P3 | Langfuse/Sentry 集成 |

#### 实施阶段

- [x] **Phase 1: Foundation (Week 1-2)** - 基础框架
- [x] **Phase 2: Agent Integration (Week 3-4)** - Agent 集成
- [ ] **Phase 3: Production Hardening (Week 5-6)** - 生产强化
- [ ] **Phase 4: Observability (Week 7-8)** - 可观测性

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

*文档更新时间: 2026-05-18*