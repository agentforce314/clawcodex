# ClawCodex 上游同步与解耦架构设计方案

> 文档路径: `docs/UPSTREAM_SYNC_DESIGN.md`
> 版本: v1.0
> 更新日期: 2026-05-19
> 关联文档: [FEATURE_PLAN.md](FEATURE_PLAN.md), [PROGRESS.md](PROGRESS.md), [WORKFLOW.md](WORKFLOW.md), [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 目录

- [一、背景与问题诊断](#一背景与问题诊断)
- [二、总体设计原则](#二总体设计原则)
- [三、三层隔离架构模型](#三三层隔离架构模型)
  - [3.1 Layer 1: 上游兼容层](#31-layer-1-上游兼容层)
  - [3.2 Layer 2: 能力抽象层](#32-layer-2-能力抽象层)
  - [3.3 Layer 3: 差异化能力层](#33-layer-3-差异化能力层)
- [四、Git 工作流设计](#四git-工作流设计)
  - [4.1 分支模型](#41-分支模型)
  - [4.2 Patch Queue 机制](#42-patch-queue-机制)
  - [4.3 同步流程规范](#43-同步流程规范)
- [五、Code Agent 辅助方案](#五code-agent-辅助方案)
  - [5.1 自动化流水线](#51-自动化流水线)
  - [5.2 冲突解决上下文工程](#52-冲突解决上下文工程)
  - [5.3 Agent Prompt 模板](#53-agent-prompt-模板)
  - [5.4 渐进式自动化级别](#54-渐进式自动化级别)
- [六、立即执行的迁移步骤](#六立即执行的迁移步骤)
- [七、风险与回退策略](#七风险与回退策略)
- [附录 A: 术语表](#附录-a-术语表)
- [附录 B: 参考文档](#附录-b-参考文档)

---

## 一、背景与问题诊断

ClawCodex 是 Anthropic Claude Code 的 Python 移植版，在扩展多 Provider 支持、自主工作流等差异化能力的同时，不可避免地需要修改原项目源码。当前项目在新功能实现上已采用较为解耦的方案（如适配器模式替换自建模块），但仍面临以下核心问题：

**原项目持续更新导致 rebase 时出现大量代码冲突**，人工解决成本高、易出错、难以长期维护。

### 1.1 冲突来源分层

| 层级 | 包含内容 | 当前解耦状态 | 冲突影响 |
|------|---------|-------------|---------|
| **基础设施层** | 配置系统、Git 操作、Frontmatter 解析、Hook 系统 | 已解耦（通过 `_xxx_adapter.py` 适配器） | 低 |
| **扩展能力层** | Provider、Skills、MCP、Hooks | 半解耦（有新实现，但接口仍受原结构约束） | 中 |
| **核心逻辑层** | Agent Loop、Tool 调用协议、Context 构建、消息格式 | **未解耦**，直接移植/重写原 TS 逻辑 | **高** |

### 1.2 核心矛盾

核心逻辑层对原项目接口、数据流、消息格式的**硬编码依赖**导致：原项目任一内部改动（变量重命名、函数重构、消息格式调整）都会波及 `src/agent/`、`src/tool_system/`、`src/context_system/` 等大量文件，使得追踪上游更新变得不可持续。

### 1.3 设计目标

1. **隔离上游变化**：上游代码更新仅影响有限的兼容层，不扩散到差异化能力层。
2. **显式化管理差异**：所有对上游源码的必要修改以可追踪、可复现的方式管理。
3. **自动化同步**：利用 Code Agent（Claude Code / OpenClaw / Hermes Agent）辅助甚至自动完成大部分同步工作。
4. **渐进式迁移**：不必一次性完成全部解耦，按优先级分阶段实施。

---

## 二、总体设计原则

### 2.1 三层隔离

将代码库严格划分为三层，每层有明确的职责边界和依赖方向：

```
Layer 3 (差异化能力层)
    ^ 依赖
Layer 2 (能力抽象层)  ←── 核心：定义稳定契约，屏蔽上游变化
    ^ 依赖
Layer 1 (上游兼容层)  ←── 唯一允许直接跟踪上游的代码区域
```

**依赖规则（强制）**：
- Layer 3 只能依赖 Layer 2 的 Protocol / 接口，**禁止**直接导入 Layer 1 的具体实现。
- Layer 2 **不依赖** Layer 1，是独立的契约定义层。
- Layer 1 到 Layer 2 的桥接由显式的适配器/桥接模块完成。

### 2.2 Patch 显式化

所有对上游源码的必要修改必须提取为 `patches/` 目录下的 patch 文件，附带元数据描述修改原因和影响范围。禁止在 Layer 1 代码中直接手写差异化逻辑。

### 2.3 版本化锁定

Layer 1 代码按上游版本号组织（如 `v2025_04/`、`v2025_06/`），支持同时维护多个上游版本的兼容层，便于回退和灰度验证。

### 2.4 Agent 友好

所有设计决策需考虑 Code Agent 的自动化能力：目录结构清晰、上下文可机器解析、冲突解决有明确的决策树和边界约束。

---

## 三、三层隔离架构模型

### 3.1 Layer 1: 上游兼容层

**职责**：原项目 TypeScript 参考实现的 Python 镜像/翻译，尽量保持与原项目结构一一对应。

**目录结构**：

```
src/upstream/
├── v2025_04/                  # 首次移植锁定的参考版本
│   ├── agent_loop/
│   │   ├── __init__.py
│   │   └── ...               # 与原项目 agent loop 对应的 Python 翻译
│   ├── context_system/
│   ├── tool_system/
│   └── _bridge.py            # 此版本到 Layer 2 的桥接模块
├── v2025_06/                  # 下一次同步后的新版本（未来）
│   ├── agent_loop/
│   ├── context_system/
│   └── _bridge.py
└── __init__.py               # 根据配置选择激活的版本
```

**核心规则**：
- Layer 1 代码**尽量保持与原项目结构一一对应**，不做功能扩展。
- ClawCodex 的差异化功能**绝不直接修改** Layer 1 文件。
- 所有对 Layer 1 的必要修改必须通过 `patches/` 队列应用。

### 3.2 Layer 2: 能力抽象层

**职责**：将原项目中"隐式契约"显式化为稳定的 Python Protocol / 抽象基类。这是解决长期冲突的**核心层**。

**目录结构**：

```
src/capabilities/
├── __init__.py
├── agent_protocol.py         # Agent Loop 抽象契约
├── tool_protocol.py          # Tool 系统抽象契约
├── context_protocol.py       # Context 构建抽象契约
├── provider_protocol.py      # Provider 层抽象契约
└── events.py                 # 跨层事件定义
```

#### 3.2.1 Agent Protocol

```python
# src/capabilities/agent_protocol.py
from typing import Protocol, AsyncIterator, runtime_checkable
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTurn:
    """单次 Agent 回合的产出。"""
    messages: list[MessageBlock]
    tool_calls: list[ToolCall]
    stop_reason: StopReason


@runtime_checkable
class AgentLoop(Protocol):
    """Agent 执行循环的抽象契约。

    上游 Claude Code 的 Agent Loop 实现细节无论如何变化，
    ClawCodex 的扩展（如 Orchestrator、多 Provider 支持）
    只依赖此 Protocol。
    """

    async def run(
        self,
        session: SessionContext,
        tools: ToolRegistry,
        budget: TokenBudget,
    ) -> AsyncIterator[AgentEvent]:
        """运行 Agent 循环，产出流式事件。"""
        ...

    async def fork_subagent(
        self,
        parent: SessionContext,
        task: SubagentTask,
    ) -> SubagentHandle:
        """创建独立会话的子 Agent。"""
        ...


@runtime_checkable
class AgentBridge(Protocol):
    """Layer 1 具体实现到 Layer 2 Protocol 的桥接器。"""

    def adapt(self, upstream_impl: Any) -> AgentLoop:
        """将上游特定版本的实现包装为符合 Protocol 的对象。"""
        ...
```

#### 3.2.2 Tool Protocol

```python
# src/capabilities/tool_protocol.py
from typing import Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    """工具的抽象契约。"""

    name: str
    description: str
    input_schema: dict[str, Any]

    async def execute(self, input: dict, ctx: ToolContext) -> ToolResult:
        """执行工具。"""
        ...

    @property
    def permission_policy(self) -> PermissionPolicy:
        """权限策略，提供扩展点而非修改原代码。"""
        return PermissionPolicy.default()


class ToolRegistry(Protocol):
    """工具注册表的抽象契约。"""

    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def list_tools(self) -> list[Tool]: ...
```

#### 3.2.3 Context Protocol

```python
# src/capabilities/context_protocol.py
from typing import Protocol, runtime_checkable


@runtime_checkable
class ContextBuilder(Protocol):
    """上下文构建的抽象契约。

    上游的 Context System 实现变化时，
    只需重新实现此 Protocol，不影响调用方。
    """

    async def build(
        self,
        session: SessionContext,
        workspace: WorkspaceSnapshot,
    ) -> ContextPayload:
        """构建发送给模型的上下文载荷。"""
        ...
```

#### 3.2.4 Provider Protocol

```python
# src/capabilities/provider_protocol.py
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """LLM Provider 的抽象契约。

    ClawCodex 的多 Provider 支持在此 Protocol 之上构建，
    不依赖上游 Provider 的具体实现细节。
    """

    name: str

    async def stream_chat(
        self,
        messages: list[MessageBlock],
        tools: list[ToolDefinition] | None,
        config: ProviderConfig,
    ) -> AsyncIterator[StreamEvent]:
        ...

    def supports_images(self) -> bool: ...
    def supports_tools(self) -> bool: ...
```

### 3.3 Layer 3: 差异化能力层

**职责**：ClawCodex 完全自主的差异化功能，永不同步原项目，永不直接依赖 Layer 1。

**当前已在此层的模块**（需审计是否仍有对 Layer 1 的直接依赖）：

| 模块 | 说明 | 是否已完全隔离 Layer 1 |
|------|------|----------------------|
| `src/orchestrator/` | 自主工作流编排（Symphony 集成） | 需审计 |
| `src/providers/` | 多 Provider 支持 | 需审计 |
| `src/permissions/` | 权限与安全（含 Bash AST） | 需审计 |
| `src/hooks/` | 钩子系统 | 需审计 |
| `src/context_system/` | 上下文构建 | **未隔离**，需重构 |
| `src/agent/` | Agent 核心 | **未隔离**，需重构 |
| `src/tool_system/` | 工具系统 | **未隔离**，需重构 |

**审计方法**：运行 `scripts/audit_layer_violations.py`（待创建），检查 Layer 3 模块是否直接导入了 Layer 1 的路径。

---

## 四、Git 工作流设计

### 4.1 分支模型

```
upstream/vendor          ← 原项目代码的纯镜像（定期 fetch，禁止人工修改）
    │
    ├── upstream/v2025_04   ← 基于 vendor 切出的锁定版本分支（标签）
    │
    └── upstream/v2025_06   ← 未来新版本（标签）

main / dev-decoupling    ← ClawCodex 开发主干
    │
    ├── patches/            ← Patch Queue 目录
    │
    ├── src/capabilities/   ← Layer 2（能力抽象层）
    ├── src/upstream/       ← Layer 1（上游兼容层，含版本子目录）
    └── src/orchestrator/   ← Layer 3（差异化能力层示例）

sync/upstream-YYYYMMDD   ← Agent 自动同步生成的临时分支
```

**分支规则**：
- `upstream/vendor`：**只读镜像**，仅通过 `git fetch upstream` 更新，禁止任何人工 commit。
- `upstream/vYYYY_MM`：**版本锁定标签**，从 `upstream/vendor` 的特定 commit 切出，作为 Patch 应用的基础。
- `dev-decoupling` / `main`：**开发主干**，只包含 Layer 2 + Layer 3 + 已应用的 patches。
- `sync/upstream-YYYYMMDD`：**同步临时分支**，由 Agent 自动生成，用于人工审核后合并。

### 4.2 Patch Queue 机制

将当前对原项目的**必要修改**提取为可维护的 patch 队列。

**目录结构**：

```
patches/
├── 0001-port-to-python-asyncio.patch       # TS→Python 基础移植
├── 0002-add-provider-abstraction.patch     # 多 Provider 支持
├── 0003-extract-tool-registry.patch        # 工具注册表改造
├── 0004-subagent-lifecycle.patch           # 子 Agent 生命周期
├── 0005-context-system-pluggable.patch     # Context 系统可插拔化
├── ...
├── series                                   # patch 应用顺序清单
└── metadata/                                # 每个 patch 的元数据
    ├── 0001.json
    ├── 0002.json
    └── ...
```

**Patch 元数据格式**（`patches/metadata/0001.json`）：

```json
{
  "id": "0001",
  "title": "Port agent loop to Python asyncio",
  "upstream_version_range": ["2025.04.20", "2025.06.15"],
  "affected_modules": ["agent_loop"],
  "layer": 1,
  "reason": "Claude Code is TypeScript; ClawCodex needs Python async/await equivalent",
  "conflict_history": [
    {"upstream_version": "2025.05.10", "resolution": "renamed variable in upstream", "effort_minutes": 15}
  ],
  "owner": "agent-team",
  "automatable": true
}
```

**Patch 管理工具**：使用 `quilt` 或 `git rebase --onto` + 自定义脚本。

```bash
# 初始化 patch 队列
quilt init

# 创建新 patch
quilt new 0006-message-format-compat.patch

# 编辑文件（修改会自动记录到 patch）
quilt edit src/upstream/v2025_04/agent_loop/messages.py

# 刷新 patch
quilt refresh

# 查看当前 applied patches
quilt applied

# 全部弹出
quilt pop -a

# 全部应用
quilt push -a
```

### 4.3 同步流程规范

**同步原项目新版本时的标准流程**：

```
Step 1: 获取上游更新
    git fetch upstream
    git tag upstream/v2025_06 upstream/main^{commit}

Step 2: 分析变化范围
    python scripts/analyze_upstream_changes.py \
        --from upstream/v2025_04 \
        --to upstream/v2025_06 \
        --output .clawcodex/sync-report.json

Step 3: 应用 Patch Queue
    quilt push -a          # 或 git am patches/*.patch
    └── 如果全部成功 → Step 5
    └── 如果有冲突 → Step 4

Step 4: Agent 辅助解决冲突（详见第 5 节）
    clawcodex agent --task "resolve-upstream-sync" \
        --context .clawcodex/sync-report.json

Step 5: 验证契约完整性
    pytest tests/test_capability_contracts.py
    pytest tests/integration/test_upstream_compat.py

Step 6: 创建同步分支并提交审核
    git checkout -b sync/upstream-20250615
    git add patches/ src/upstream/v2025_06/
    git commit -m "sync: upstream v2025.06.15"
    gh pr create --draft --title "[SYNC] upstream v2025.06.15"

Step 7: 人工审核后合并到主分支
```

---

## 五、Code Agent 辅助方案

### 5.1 自动化流水线

设计一个可由 ClawCodex Orchestrator 执行的自动化同步工作流：

```yaml
# .clawcodex/upstream-sync.workflow
workflow:
  name: upstream-sync
  description: "自动检测上游更新并触发同步流程"

  trigger:
    cron: "0 6 * * 1"          # 每周一早上 6 点检查
    webhook: upstream-release   # 监听原项目 release 事件

  environment:
    UPSTREAM_REMOTE: "https://github.com/anthropics/claude-code.git"
    BASE_VERSION_TAG: "upstream/v2025_04"

  steps:
    - name: fetch-upstream
      run: |
        git remote add upstream $UPSTREAM_REMOTE 2>/dev/null || true
        git fetch upstream main
        NEW_COMMIT=$(git rev-parse upstream/main)
        echo "upstream_latest=$NEW_COMMIT" >> $GITHUB_ENV

    - name: detect-changes
      run: |
        python scripts/analyze_upstream_changes.py \
          --from $BASE_VERSION_TAG \
          --to upstream/main \
          --output .clawcodex/sync-report.json

    - name: check-impact
      run: |
        IMPACT=$(python -c "import json; d=json.load(open('.clawcodex/sync-report.json')); print(d['overall_impact'])")
        if [ "$IMPACT" = "low" ]; then
          echo "strategy=auto" >> $GITHUB_ENV
        elif [ "$IMPACT" = "medium" ]; then
          echo "strategy=agent-assisted" >> $GITHUB_ENV
        else
          echo "strategy=human-review" >> $GITHUB_ENV
        fi

    - name: agent-resolve
      if: env.strategy != 'human-review'
      run: |
        clawcodex agent \
          --task "应用上游同步补丁并解决冲突" \
          --context .clawcodex/sync-report.json \
          --context patches/series \
          --output-branch sync/upstream-$(date +%Y%m%d) \
          --timeout 3600

    - name: create-pr
      run: |
        gh pr create \
          --draft \
          --title "[SYNC] upstream $(date +%Y-%m-%d)" \
          --body-file .clawcodex/sync-report.md
```

### 5.2 冲突解决上下文工程

为了让 Agent 高效解决冲突，需要提供**结构化上下文**而非原始 diff。

**分析报告格式**（`.clawcodex/sync-report.json`）：

```json
{
  "upstream_version": "2025.06.15",
  "previous_version": "2025.04.20",
  "analysis_timestamp": "2026-05-19T08:00:00Z",
  "overall_impact": "medium",
  "statistics": {
    "total_upstream_commits": 47,
    "files_changed_upstream": 23,
    "patches_in_queue": 8,
    "patches_potentially_affected": 3
  },
  "affected_modules": [
    {
      "module": "agent_loop",
      "layer": 1,
      "upstream_changes": 12,
      "files_changed": ["agent_loop/runner.ts", "agent_loop/events.ts"],
      "local_patches_affected": [
        "0003-extract-tool-registry.patch",
        "0007-subagent-lifecycle.patch"
      ],
      "conflict_probability": "high",
      "recommended_strategy": "rebase-patches",
      "estimated_effort_minutes": 30
    },
    {
      "module": "context_system",
      "layer": 1,
      "upstream_changes": 3,
      "files_changed": ["context_system/builder.ts"],
      "local_patches_affected": [],
      "conflict_probability": "low",
      "recommended_strategy": "fast-forward",
      "estimated_effort_minutes": 5
    }
  ],
  "protocol_impacts": {
    "message_format": "unchanged",
    "tool_schema": "minor_addition",
    "agent_events": "unchanged",
    "breaking_changes": []
  },
  "action_items": [
    {
      "patch_id": "0003",
      "action": "review",
      "reason": "upstream renamed ToolRegistry to ToolManager"
    }
  ]
}
```

**分析报告脚本**（`scripts/analyze_upstream_changes.py`）职责：

1. 对比两个版本之间的上游 diff。
2. 识别受影响的 Layer 1 模块。
3. 交叉引用 `patches/metadata/` 判断哪些 patch 可能受影响。
4. 评估冲突概率（基于历史数据和变更类型）。
5. 检查是否涉及 Layer 2 Protocol 的契约变更。
6. 输出机器可读（JSON）和人类可读（Markdown）两份报告。

### 5.3 Agent Prompt 模板

用于指导 Code Agent 解决同步冲突的标准化 Prompt：

````markdown
# 角色：上游同步维护工程师

你是一个负责维护开源项目同步的代码智能体。当前任务是将上游项目
`anthropics/claude-code` 的更新合并到 ClawCodex 的 Python 移植版中。

## 已知上下文

- 上游版本：{upstream_version}
- 当前锁定版本：{previous_version}
- 受影响模块：{affected_modules}
- Layer 2 Protocol 变更需求：{protocol_changes}

## 你的工作范围（严格边界）

1. **只允许修改**：
   - `patches/` 目录中的 patch 文件
   - `src/upstream/` 中的 Layer 1 代码
   - `.clawcodex/sync-report.md` 中的进度记录

2. **绝对禁止修改**：
   - `src/capabilities/`（Layer 2）
   - `src/orchestrator/`、`src/providers/` 等 Layer 3 代码
   - 测试文件（除非是为了适配契约变更）

3. **如果上游变更违反了 Layer 2 Protocol**：
   - 在 `docs/protocol-changes.md` 中记录变更需求
   - 在 PR 描述中标记 `NEEDS_PROTOCOL_REVIEW`
   - 停止修改，等待人类审核

## 工作流程

1. 读取 `patches/series` 了解当前 patch 队列。
2. 对 `upstream/vendor` 尝试 `quilt push -a` 或 `git am`。
3. 对失败的 patch：
   a. 读取 `.rej` 文件和冲突上下文。
   b. 判断：是"上游重命名/重构"还是"语义变化"？
   c. 如果是重命名：更新 patch 中的文件名和符号名。
   d. 如果是语义变化：评估是否需要新增 Protocol 方法。
4. 更新 patch 后，运行以下验证：
   ```bash
   pytest tests/test_capability_contracts.py
   pytest tests/test_layer_isolation.py
   python scripts/audit_layer_violations.py
   ```
5. 所有验证通过后，提交到分支 `sync/upstream-{date}`。

## 决策树

```
patch 应用失败？
  ├─ 是 → 查看 .rej 文件
  │         ├─ 仅行号偏移/上下文偏移 → 自动刷新 patch
  │         ├─ 变量/函数重命名 → 更新 patch 中的符号名
  │         ├─ 文件移动/拆分 → 更新 patch 中的路径
  │         └─ 语义变化（逻辑修改）→ 标记 NEEDS_REVIEW，停止
  └─ 否 → 继续下一个 patch
```

## 禁止事项

- 不要直接修改 `src/agent/run_agent.py` 等核心文件来"绕过"冲突。
- 不要删除测试来让构建通过。
- 不要修改 `src/capabilities/` 中的 Protocol 定义，除非明确授权。
- 如果 patch 无法自动解决且涉及核心语义变更，标记为 `NEEDS_HUMAN_REVIEW` 并停止。

## 输出要求

完成后，在 PR 描述中提供以下信息：
1. 成功应用的 patches 列表
2. 需要人工审核的 patches 列表（含原因）
3. Layer 2 Protocol 影响评估
4. 测试运行结果摘要
````

### 5.4 渐进式自动化级别

| 级别 | 名称 | 能力 | 触发条件 | 成熟度目标 |
|------|------|------|---------|-----------|
| **L0** | 纯人工 | 所有同步由人工执行，Agent 仅提供 diff 预览 | 初始阶段 / 重大版本升级 / Protocol 契约变更 | 当前阶段 |
| **L1** | 辅助决策 | Agent 生成冲突报告 + 建议方案 + 预估工作量，人类执行具体操作 | Patch 冲突数 > 5 或涉及核心模块 | 2 周内 |
| **L2** | 半自动 | Agent 自动解决机械冲突（重命名、路径变更、行号偏移），人类审核语义冲突 | 冲突类型可被模式匹配（>80% 确定性） | 1 个月内 |
| **L3** | 全自动 | Agent 完成整个同步流程，仅在 `NEEDS_HUMAN_REVIEW` 时中断 | CI 全绿 + 历史同步成功率 > 90% | 3 个月内 |

**升级条件**：
- L0 → L1：Layer 2 Protocol 定义完成，`test_capability_contracts.py` 全绿。
- L1 → L2：连续 3 次同步中 Agent 建议方案被人类采纳率 > 80%。
- L2 → L3：连续 5 次同步 Agent 自动解决成功率 > 90%，无回退。

---

## 六、立即执行的迁移步骤

基于 ClawCodex 当前 `dev-decoupling` 分支状态，建议按以下顺序执行：

### Step 1: 建立 Layer 2 Protocol（预计 1-2 天）

```bash
mkdir -p src/capabilities/

# 创建以下文件：
# - src/capabilities/__init__.py
# - src/capabilities/agent_protocol.py
# - src/capabilities/tool_protocol.py
# - src/capabilities/context_protocol.py
# - src/capabilities/provider_protocol.py
# - src/capabilities/events.py
```

**验收标准**：`src/capabilities/` 目录包含完整的 Protocol 定义，可被 `runtime_checkable` 验证。

### Step 2: 提取现有"脏修改"为 Patch（预计 2-3 天）

```bash
# 1. 确定与原项目最后同步的 commit（假设为 BASE）
BASE=$(git log --all --oneline | grep -i "initial port\|sync upstream" | head -1 | cut -d' ' -f1)

# 2. 生成当前对核心模块的 diff
git diff $BASE..HEAD -- src/agent/ src/tool_system/ src/context_system/ > /tmp/all-changes.diff

# 3. 使用 scripts/split_diff_to_patches.py（待创建）按模块拆分为独立 patch
python scripts/split_diff_to_patches.py \
    --input /tmp/all-changes.diff \
    --output-dir patches/

# 4. 初始化 quilt
quilt init
quilt push -a
```

**验收标准**：`patches/` 目录包含所有对 Layer 1 的必要修改，且 `quilt push -a` 可将 `src/upstream/v2025_04/` 转换为当前 `src/agent/` 等目录的等效状态。

### Step 3: 设置 Vendor Branch（预计 半天）

```bash
git checkout -b upstream/vendor

# 此分支仅用于追踪原项目，禁止人工修改
git remote add upstream https://github.com/anthropics/claude-code.git 2>/dev/null || true
git fetch upstream

# 创建版本锁定标签
git tag upstream/v2025_04 upstream/main^{commit}
```

**验收标准**：`git log upstream/vendor --oneline` 显示原项目提交历史，`git show upstream/v2025_04` 可查看锁定版本。

### Step 4: 编写契约测试（预计 1 天）

```python
# tests/test_capability_contracts.py
# 验证 Layer 3 的调用不直接依赖 Layer 1 的具体实现

import ast
import importlib
from pathlib import Path


def test_orchestrator_uses_agent_protocol():
    """验证 Orchestrator 通过 Protocol 而非具体实现依赖 Agent。"""
    from src.orchestrator.agent_runner import AgentRunner
    from src.capabilities.agent_protocol import AgentLoop

    # AgentRunner 的构造函数应接受任何符合 AgentLoop Protocol 的对象
    sig = inspect.signature(AgentRunner.__init__)
    agent_param = sig.parameters.get("agent_loop")
    assert agent_param is not None
    assert agent_param.annotation == AgentLoop or "AgentLoop" in str(agent_param.annotation)


def test_no_layer_3_imports_layer_1():
    """验证 Layer 3 模块不直接导入 Layer 1 路径。"""
    layer_3_paths = ["src/orchestrator/", "src/providers/"]
    layer_1_patterns = ["from src.agent.", "from src.tool_system.", "from src.context_system."]

    violations = []
    for root in layer_3_paths:
        for py_file in Path(root).rglob("*.py"):
            source = py_file.read_text()
            for pattern in layer_1_patterns:
                if pattern in source:
                    violations.append(f"{py_file}: imports {pattern}")

    assert not violations, "Layer 3 must not directly import Layer 1:\n" + "\n".join(violations)


def test_tool_protocol_compliance():
    """验证所有内置工具符合 Tool Protocol。"""
    from src.capabilities.tool_protocol import Tool
    from src.tool_system.registry import ToolRegistry

    registry = ToolRegistry()
    for tool in registry.list_tools():
        assert isinstance(tool, Tool), f"{tool.name} does not implement Tool Protocol"
```

**验收标准**：`pytest tests/test_capability_contracts.py -v` 全绿。

### Step 5: 编写隔离审计脚本（预计 半天）

```python
# scripts/audit_layer_violations.py
# 定期运行，检测是否有新的 Layer 隔离违规
```

**验收标准**：脚本可检测并报告 Layer 3 → Layer 1 的直接导入违规。

### Step 6: 配置 CI 同步检测（预计 半天）

```yaml
# .github/workflows/upstream-detect.yml
name: Upstream Change Detection

on:
  schedule:
    - cron: "0 6 * * 1"  # 每周一早上 6 点
  workflow_dispatch:

jobs:
  detect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Fetch upstream
        run: |
          git remote add upstream https://github.com/anthropics/claude-code.git
          git fetch upstream main

      - name: Analyze changes
        run: |
          python scripts/analyze_upstream_changes.py \
            --from upstream/v2025_04 \
            --to upstream/main \
            --output sync-report.json

      - name: Create issue if changes detected
        if: env.HAS_CHANGES == 'true'
        run: |
          gh issue create \
            --title "[UPSTREAM] New changes detected $(date +%Y-%m-%d)" \
            --body-file sync-report.md \
            --label "upstream-sync"
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

**验收标准**：CI 每周自动检测上游更新，有变化时自动创建 Issue 并附带结构化报告。

---

## 七、风险与回退策略

| 风险 | 可能性 | 影响 | 缓解策略 |
|------|--------|------|---------|
| Layer 2 Protocol 设计过早抽象，导致后续频繁修改 | 中 | 高 | Protocol 设计遵循"最小可用"原则，仅提取当前差异化功能真正需要的契约；预留版本号字段（`protocol_version`）支持演进。 |
| Patch Queue 过大，管理成本超过收益 | 中 | 中 | 当 patch 数量 > 20 时触发"patch 合并审查"，将多个相关 patch 合并；持续推动 Layer 2 完善以减少对 Layer 1 的修改需求。 |
| Agent 自动解决冲突引入隐性 bug | 中 | 高 | L2/L3 级别必须经过人工审核后才能合并；`test_capability_contracts.py` 和集成测试作为强制门禁；保留 `git bisect` 友好的 commit 粒度。 |
| 上游进行架构级重构（如整个 Agent Loop 重写） | 低 | 极高 | 标记为 `ARCHITECTURE_BREAKING`，触发 L0 纯人工流程；同时评估是否需要新增 Layer 2 Protocol 版本。 |
| 团队成员不熟悉 quilt / patch 工作流 | 高 | 低 | 提供 `scripts/patch-helper.py` 封装常用操作；在 CONTRIBUTING.md 中添加 Patch 管理指南；CI 中集成 patch 格式检查。 |

**紧急回退**：如果某次同步导致主分支不稳定，可立即回退到上一个已验证的 `upstream/vYYYY_MM` 标签，重新从 Step 1 开始。

---

## 附录 A: 术语表

| 术语 | 定义 |
|------|------|
| **Layer 1 (上游兼容层)** | 原项目源码的 Python 翻译/镜像，按版本子目录组织，是唯一直接跟踪上游的代码区域。 |
| **Layer 2 (能力抽象层)** | 定义稳定的 Python Protocol / 抽象基类，将上游的"隐式契约"显式化，屏蔽上游实现细节变化。 |
| **Layer 3 (差异化能力层)** | ClawCodex 完全自主的功能模块，直接面向用户，永不同步原项目。 |
| **Patch Queue** | 以 `quilt` 或 `git am` 管理的 patch 文件序列，显式记录所有对 Layer 1 的必要修改。 |
| **Vendor Branch** | 仅用于镜像原项目代码的只读分支，禁止任何人工 commit。 |
| **契约测试** | 验证 Layer 3 是否通过 Layer 2 Protocol 间接使用 Layer 1，而非直接依赖具体实现的测试。 |
| **Agent 友好** | 指目录结构、错误信息、上下文格式等便于 Code Agent（LLM 驱动的代码智能体）理解和操作的设计。 |

## 附录 B: 参考文档

| 文档 | 路径 | 说明 |
|------|------|------|
| 功能规划 | [FEATURE_PLAN.md](FEATURE_PLAN.md) | ClawCodex 特性规划和模块状态 |
| 开发进度 | [PROGRESS.md](PROGRESS.md) | 开源替代组件和模块开发进度 |
| 工作流配置 | [WORKFLOW.md](WORKFLOW.md) | Orchestrator 自主模式的 WORKFLOW.md 配置指南 |
| 架构概览 | [ARCHITECTURE.md](ARCHITECTURE.md) | ClawCodex 整体架构文档 |
| quilt 文档 | https://savannah.nongnu.org/projects/quilt | Patch 队列管理工具官方文档 |
| Python Protocol | https://docs.python.org/3/library/typing.html#typing.Protocol | Python 结构化子类型官方文档 |
