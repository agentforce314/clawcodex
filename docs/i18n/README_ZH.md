<div align="center">

# ClawCodex DevMind

**`clawcodex` 的下游二开版本，把单个 agent 升级为一支可自主值守的工程团队 —— 编排器 + POS 转 Agent 编译器 + 定时任务 + 桥接守护进程 + LiteLLM。**

*构建于上游 Claude Code 的 Python 重构版本之上。本仓库新增了多 agent 编排、调度、LLM 路由等上游尚未提供的能力层。*

</div>

> 📍 **仓库地址:** [`https://gitcode.com/chadwweng/clawcodex`](https://gitcode.com/chadwweng/clawcodex) —— 项目**暂未开源**，所以公共搜索引擎 / GitHub 搜索都搜不到。请直接使用此 URL 克隆和浏览。

[English](../../README.md) · [中文](README_ZH.md) · [上游原始 README](../../README.md.raw)

---

## 为什么需要这个 fork？

上游 `clawcodex` 已经提供了一个忠实的 Claude Code Python 移植：agent 循环、工具系统、MCP、hooks、权限、记忆、多 provider 对话、TUI/REPL。**本 fork 在其之上加了一层 —— 把 agent 嵌入真实工程工作流所需的那些东西，从"交互式聊天"变成"长时间自主值守"。**

具体来说，本仓库新增：

- 🤖 **编排器（Orchestrator）** —— 一个守护进程，自动轮询工单系统、拉分支、跑 agent、开 PR，全程无需人工
- 🧩 **POS 转 Agent 编译器** —— 把任何 `workflow.md` 流程化规范编译成多 agent 协同系统
- ⏰ **定时任务系统（Cron System）** —— 分布式锁调度，带 jitter 和 NDJSON 运行历史
- 🌉 **桥接守护进程扩展** —— 多 session 桥接、远程运行时、REPL/headless 适配器
- 🔌 **LiteLLM Provider** —— 一个 `--provider litellm` 接口，路由到 100+ 个 LLM 后端
- 👥 **协调器 / 团队** —— `TeamCreate` / `TeamDelete` 工人群，`SendMessage` 同行私信
- 🩹 **PR 检视意见自动修复（F-37）** —— 读取评审意见 + CI 日志，在同一分支上迭代修复
- ✅ **验证门（F-38）** —— pre-commit / pre-push / post-sync 的 `pytest` 门禁，附 Markdown + JSON 报告
- 🔁 **Issue 重跑机制（F-39）** —— `agent:retry` / `agent:follow-up` / `agent:blocked` 三个标签驱动重跑

上游的 REPL、TUI、工具系统、MCP、hooks、记忆、权限、provider 层都原样保留 —— 本 fork 是接在它们之上，不替换它们。

---

## 演示

```text
$ clawcodex-dev orchestrator server start --workflow ./workflow.md
✓ orchestrator daemon started · pid 18432 · tracker=gitcode · repo=chadwweng/AgentSDK
✓ max_concurrent_agents=3 · permission_mode=bypassPermissions

$ clawcodex-dev orchestrator issue list
ID                STATUS      BRANCH                     ATTEMPTS  PR
gitcode/AGENTSDK-7   done     clawcodex/AGENTSDK-7     1         https://gitcode.com/.../pulls/7
gitcode/AGENTSDK-12  running  clawcodex/AGENTSDK-12    1         -
gitcode/AGENTSDK-15  paused   clawcodex/AGENTSDK-15    2         https://gitcode.com/.../pulls/15
linear/PROJ-128      running  clawcodex/PROJ-128       1         -

$ clawcodex-dev orchestrator issue tail --id gitcode/AGENTSDK-15
14:02:11  ◐ Read src/services/lock.py · 132 lines
14:02:13  ◐ Grep "asyncio.Lock" · 3 hits
14:02:18  ◐ Edit src/services/lock.py · +18 -4
14:02:24  ◐ Bash pytest tests/test_lock.py · 4 passed
14:02:24  ✓ Verification gate OK (pytest -x)
14:02:25  ◐ Git commit -m "fix: per-key lock granularity in flush_batch"
14:02:26  ◐ Git push origin clawcodex/AGENTSDK-15
14:02:31  ✓ PR opened · auto-review-loop subscribed

# 4 小时后，PR 评审意见落地
$ clawcodex-dev orchestrator issue inject --id gitcode/AGENTSDK-15 "处理评审意见"
✓ agent resumed · re-reading PR comments · pushing fix commits
```

---

## 快速开始

```bash
git clone https://gitcode.com/chadwweng/clawcodex.git
cd clawcodex

# 安装（推荐 uv；pip 也可以）
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"

# 配置 provider（一次性）
clawcodex-dev login

# 运行下游 CLI
clawcodex-dev                      # REPL（与上游一致，外加 orchestrator 子命令）
clawcodex-dev orchestrator --help  # 查看所有编排器命令
clawcodex-dev cron --help          # 查看定时任务子命令
clawcodex-dev pos --help           # 查看 POS 编译器子命令
```

需要 **Python 3.10+**（推荐 3.11）。Linux / macOS / WSL2。

> 上游的 CLI 入口（`python -m src.cli`）依然可用 —— 本 fork 新增了一个并行的 `clawcodex-dev` 入口，挂载下游子命令（`orchestrator`、`cron`、`pos` 等）。

---

## 二开特性

### 🤖 编排器（Orchestrator）—— 自主 Issue → PR 流水线

本 fork 的旗舰功能。一个长时间运行的守护进程，持续轮询工单系统，拉取 issue、创建 worktree、带着合适的工具和权限模式跑 agent、做验证、提交、推送、开 PR —— 每一步都支持操作员介入覆盖。

**3 分钟启动：**

```bash
# 1. 复制模板
cp extensions/orchestrator/templates/workflow.template.md ./workflow.md
$EDITOR workflow.md    # 设置 tracker、repo、branch_prefix、provider、permission_mode

# 2. 启动守护进程
clawcodex-dev orchestrator server start --workflow ./workflow.md

# 3. 观察
clawcodex-dev orchestrator issue list
clawcodex-dev orchestrator issue tail --id <id>
clawcodex-dev orchestrator dashboard                   # HTTP/SSE on :8080
```

**`extensions/orchestrator/` 内置模块：**

| 模块 | 作用 |
|---|---|
| `tracker.py` + `linear/`、`gitcode`、`gitee`、`github` 适配器 | 可插拔的工单来源（4 个 tracker） |
| `issue_registry.py` | JSON 持久化的映射：issue ↔ 分支 ↔ PR ↔ 尝试次数 |
| `clarification.py` + `clarification_queue.py` | 13 状态澄清队列，三通道求解器（交互式 / 文件 / @提及） |
| `agent_runner.py` | 在每个 issue 的 worktree 内启动 agent，支持重试、退避、验证门 |
| `git_sync.py` | pre-commit / pre-push / post-sync 钩子（F-38），PR 正文模板渲染 |
| `status_dashboard.py` + `cli/dashboard.py` | 8080 端口的 HTTP/SSE LiveView，内嵌 HTML/JS |
| `workspace.py` + `workspace_locator.py` | 每个 issue 的 worktree 生命周期 |
| `review_feedback.py` | 读取 PR 评审意见，驱动 `agent_runner` 在同一分支上修复（F-37） |
| `progress_reporter.py` | 阶段性进度事件，落 NDJSON |
| `approval_policy.py` | 工具级审批路由，headless 跑用 |
| `orchestrator.py` | 守护进程主循环 |
| `workflow.py` + `workflow_store.py` + `templates/workflow.template.md` | YAML frontmatter 配置 + Jinja 风格 agent prompt 模板 |

**子命令一览：**

```bash
# 服务端生命周期
clawcodex-dev orchestrator server {start,status,stop} --workflow <file>

# Issue 查询
clawcodex-dev orchestrator issue list [--status <state>] [--workspace <path>]
clawcodex-dev orchestrator issue show --id <id>
clawcodex-dev orchestrator issue tail --id <id>             # NDJSON 实时 tail

# Issue 生命周期
clawcodex-dev orchestrator issue stop    --id <id>          # 强制终止
clawcodex-dev orchestrator issue pause   --id <id> [--reason <text>]
clawcodex-dev orchestrator issue resume  --id <id>
clawcodex-dev orchestrator issue takeover --id <id>         # 停 agent + 在 worktree 启 REPL

# 操作员交互
clawcodex-dev orchestrator issue clarify --id <id> --answer <text> [--forward-to-author]
clawcodex-dev orchestrator issue inject  --id <id> [hint]   # 把 hint 注入 .operator_hints.md

# 工作区探查
clawcodex-dev orchestrator issue workspace --id <id> [--ls|--cat FILE|--edit FILE --with CONTENT]

# 仪表盘
clawcodex-dev orchestrator dashboard [--port 8080] [--host 127.0.0.1]
```

**Registry 跟踪的 issue 状态：** `pending` · `running` · `synced` · `completed` · `failed` · `abandoned`。

**基础编排器之上的 F-feature 增量：**

- **F-37 — PR 评审意见自动修复** —— PR 打开后，编排器订阅评审意见、inline 评审线程、CI 失败日志。一旦反馈到达，就在**同一分支**上重跑 agent（不新开 PR），持续推修复 commit，直到审阅者满意或达到最大迭代次数。
- **F-38 — 验证门** —— `git_sync` 在三个检查点运行 `test_command`（默认 `pytest -x`）：`pre_commit`、`pre_push`、`post_sync`。失败即阻塞推送。Markdown + JSON 报告自动插入 PR 正文，并作为一条汇总评论发布。
- **F-39 — Issue 重跑机制** —— 三个仓库标签驱动重跑：
  - `agent:retry` —— 重置本地状态、关闭旧 PR、从头重跑整个 issue
  - `agent:follow-up` —— 保留 PR，对新评论叠加 commit（F-37 路径）
  - `agent:blocked` —— 永久跳过该 issue
  - 也可以通过 `/agent retry` / `/agent follow-up` 评论命令触发（仅原作者 / maintainer，限频），CLI 兜底为 `clawcodex-dev orchestrator issue retry --id <id> --mode reset`。

---

### 🧩 POS 转 Agent 编译器

很多工程流程仍然以 `workflow.md` 形式记录 —— "X 发生则做 Y，然后通知 Z"。POS 编译器（`extensions/pos_converter/`）把这类规范转成多 agent 协同运行时。

```bash
clawcodex-dev pos convert examples/pos/order_processing.md \
    --out ./.clawcodex
```

产物：

- `.clawcodex/agents/pos-order-processing.yaml` —— agent 定义（每个角色一个）
- `.clawcodex/skills/pos-order-processing/SKILL.md` —— 入口 skill
- `.clawcodex/workflows/pos-order-processing.yaml` —— 编排图

运行时接入了上游的 `Coordinator` / `Team` 子系统，所以生成的 agent 之间可以互发 `SendMessage`，并通过上游的 task-notification 路由在崩溃后恢复。

**模块：**

- `sdk_parser.py` —— 解析 `workflow.md` 规范（frontmatter + 正文）
- `skill_grouper.py` —— 把步骤按角色聚合成 skill
- `agent_builder.py` —— 把每个角色物化为 `TeamCreate` agent
- `templates.py` —— 输出 YAML 的 Jinja 模板

---

### ⏰ 定时任务系统（Cron System）

一个独立的调度层（`clawcodex_ext/cron_system/`）—— 与 agent 循环解耦 —— 专门用于"按计划跑某个任务"的场景。

```bash
clawcodex-dev cron add "0 2 * * *"   "run nightly test suite"
clawcodex-dev cron list
clawcodex-dev cron status <task_id>
clawcodex-dev cron enable <task_id> | disable <task_id> | remove <task_id>
```

**功能矩阵：**

| 能力 | 详情 |
|---|---|
| Cron 表达式解析 | 标准 5 字段语法，外加 `@daily` / `@hourly` / `@reboot` 别名 |
| 分布式文件锁 | 多实例安全 —— 同时刻每个槽位只有一个调度者胜出 |
| Jitter | 随机偏移（可配），避免雷暴群拥 |
| NDJSON 运行历史 | `.cron_runs/{task_id}.ndjson` 每任务运行日志 |
| 通知 | 可选 webhook / 日志通知（成功 / 失败） |
| 状态命令 | `status`、`last_run`、`next_run`、`exit_code`、`duration_ms` |

编排器后台重试会用到它，同时也直接暴露给用户用于任意自动化。

---

### 🌉 桥接守护进程扩展

上游只搭了一个桥接骨架。本 fork 把它补完为一个能跑的多 session 守护进程，分五个阶段（`src/bridge/` + `src/remote/`）：

| 阶段 | 文件 | 作用 |
|---|---|---|
| 3 | `bridge_api.py` | HTTP 客户端（long-poll、SSE），用于远程控制 |
| 4 | `session_runner.py` | 每个 session 起一个子 CLI |
| 5 | `remote_bridge_core.py` | 远程运行时核心（exec、attach、detach） |
| 8 | `bridge_main.py` | 多 session 守护进程 —— 单进程复用 N 个 session |
| 11 | `repl_bridge.py` | 桥接到已有 REPL（编排器 `takeover` 用它） |

**典型用例：**

- 从 IDE 插件通过 HTTP/SSE 驱动 headless agent
- 把编排器挂到长时间运行的沙箱 VM
- 编排器 `takeover` —— 杀掉 agent，在同一 worktree 落入 REPL 做手工修补

---

### 🔌 LiteLLM Provider

只要一个 `--provider litellm`，就能对接 LiteLLM 支持的**任何** LLM 后端（Bedrock、Vertex、Azure、Together、Anyscale……），无需写新的 provider 类。

```bash
# 以下全部开箱即用
clawcodex-dev --provider litellm --model bedrock/anthropic.claude-3-5-sonnet -p "hi"
clawcodex-dev --provider litellm --model vertex_ai/gemini-1.5-pro         -p "hi"
clawcodex-dev --provider litellm --model azure/gpt-4o                     -p "hi"
clawcodex-dev --provider litellm --model openai/<your-finetune>           -p "hi"
```

实现：`extensions/providers_ext/litellm_provider.py`（在 `BaseProvider` 之上做了一层轻量适配）。

同时也解决了上游棘手的跨 provider 兼容：把 Anthropic 的 `image` / `document` 块翻译成 OpenAI 的 `image_url` / `file`，以便支持视觉能力的 OpenAI-兼容后端也能消费。

---

### 👥 协调器 / 团队工人

把上游的 team 原语暴露成可用的"工人蜂群"模型：

```text
clawcodex-dev coordinator team create --name build-team --members agent-1,agent-2,agent-3
clawcodex-dev coordinator team list
clawcodex-dev coordinator team delete --name build-team
```

- 在 agent 循环里暴露 `TeamCreate` / `TeamDelete` 工具
- 工人之间可以互发 `SendMessage`（同行私信）以及和管理员通信
- Task-notification XML 路由把工人的事件汇报回管理员
- POS 编译器和编排器都用它做并行 issue 处理

---

### 🛠 工具包（Tool Bundles）

上游启动时加载全部 30+ 个工具。本 fork 新增了**bundle 机制**用于冷启动加速和上下文瘦身（`extensions/tool_system_ext/`）：

| Bundle | 启动时加载 | 适用场景 |
|---|---|---|
| `bare` | Read, Write, Edit, Bash, Grep, Glob | Headless CI 跑 |
| `default` | + WebFetch, WebSearch, TodoWrite, AskUserQuestion | 普通 REPL 会话 |
| `clawcodex` | + Agent, Team, SendMessage, Cron, PlanMode, MCP, Skill | 完整 REPL 含团队工作流 |
| `all` | 注册表里所有 | 最大灵活性 |

切换方式：`clawcodex-dev --tool-bundle clawcodex`（或在 `~/.clawcodex/config.json` 的 `tool_bundles` 字段）。

基于 TF-IDF 的 `ToolSearch` 从上游继承下来 —— bundle 之上的语义工具发现仍然可用。

---

### 🖥 扩展 TUI 钩子

下游的 Textual TUI（`clawcodex_ext/tui/`）在上游 TUI 之上加了 8 个钩子点，用户可以定制布局 / 主题 / 快捷键而无需 fork 整个 TUI。通过 `~/.clawcodex/keybindings.json` 配置（keybinding-help skill 也会出现在斜杠菜单里）。

---

### 🔁 开源组件替代

本 fork 一个不那么显眼但杠杆极高贡献：**把上游手写的六处基础设施替换为成熟的开源库** —— 砍掉约 3,100 行手写代码，免费继承经过实战检验的行为、安全修复和社区维护。

| 上游手写代码 | 替换为 | 原因 | 行数变化 |
|---|---|---|---|
| 配置层（约 220 行 dataclass + 环境变量粘合代码） | **[Pydantic Settings](https://docs.pydantic-settings.dev/)** | 类型安全配置、环境变量解析、`.env` 支持、嵌套模型开箱即用 | **−220** |
| YAML frontmatter 解析器（SKILL.md、agent 文件、output styles） | **[python-frontmatter](https://python-frontmatter.readthedocs.io/)** | 通过 `parse_frontmatter()` 正确往返嵌套结构（`hooks:`、`shell:`）；静态站点生态广泛使用 | **−80** |
| 权限检查的 Bash 命令解析器 | **[tree-sitter-bash](https://github.com/tree-sitter/tree-sitter-bash)** | 真正的 AST 而非正则；能识别 `&&`、`\|`、重定向、子 shell、命令替换 —— 正则解析器漏过了一整类绕过 | **−1,400** |
| Git 操作（clone、branch、push、diff、status） | **[GitPython](https://gitpython.readthedocs.io/)** | 稳定的 `git(1)` 之上的 API，覆盖手写包装器没处理的边界情况（detached HEAD、shallow clone、submodule） | **−200** |
| Hook 系统（注册、执行、事件分发） | **[Pluggy](https://pluggy.readthedocs.io/)** | 事实标准的插件管理器（`pytest`、`tox`、`devpi` 同款）；给 hook 系统稳定的契约、hookspec 校验和懒加载 | **−1,000** |
| 结构化输出 / JSON-schema 强制 | **[Outlines](https://outlines-dev.github.io/outlines/)** | 感知 token 预算的结构化生成；在真实 token 预算下让 agent 决定工具调用，而不是事后用正则补救 | **−200** |

**合计：~3,100 行手写代码被移除**，替换为独立维护、安全审计过的、Python 生态广泛使用的库。

**为什么重要：**

- **更小的攻击面** —— 被替换的组件正是权限绕过（正则 Bash 解析器）和配置注入（手写环境变量粘合代码）最容易出现的地方。
- **更好的正确性** —— `tree-sitter-bash` 是真正的语法分析，不是正则；Pydantic Settings 加载时校验类型；Pluggy 强制 hookspec 契约。
- **更容易回馈上游** —— 替换是 drop-in 的，使用的还是同一套公共接口，所以这层可以合回上游 `clawcodex` 仓库，不破坏消费者。

可以在 `pyproject.toml` 的 `[project.dependencies]` 段下看到这些选择。上游专属子注释块保证了每个替换都能从包元数据中检索到。

---

## 下游 CLI 入口

`clawcodex-dev` 是上游 `python -m src.cli` 的并行入口。它注册了上游所有的子命令，**外加**：

```bash
clawcodex-dev orchestrator ...    # 自主 issue 处理（本 fork）
clawcodex-dev cron           ...   # 分布式定时任务（本 fork）
clawcodex-dev pos            ...   # POS 转 Agent 编译器（本 fork）
clawcodex-dev coordinator    ...   # 团队 / 工人原语（本 fork）
```

上游所有 flag（`-p`、`--tui`、`--provider`、`--model`、`--permission-mode`、`--dangerously-skip-permissions`、`--allow-dangerously-skip-permissions`、`--tool-bundle` ……）保持不变。

---

## 架构（仅本 fork）

```text
              ┌──────────────────────────────────────────────┐
              │   clawcodex_ext/cli（clawcodex-dev 入口）     │
              │   parser · dispatch · runners · permissions  │
              └──────────┬──────────────┬─────────────┬──────┘
                         │              │             │
              ┌──────────▼────┐  ┌──────▼─────┐  ┌────▼────────────┐
              │   编排器      │  │ 定时任务    │  │ POS 编译器      │
              │  + Dashboard  │  │ + Lock+    │  │ + SDK parser    │
              │  + LiveView   │  │   Jitter   │  │ + Agent builder │
              │  + Takeover   │  │ + Status   │  │ + Skill grouper │
              │  + Review FB  │  │ + Notify   │  │                 │
              └──────┬────────┘  └────────────┘  └─────────────────┘
                     │
       ┌─────────────┼─────────────┐
       │             │             │
┌──────▼─────┐ ┌─────▼──────┐ ┌────▼──────────┐
│  Trackers  │ │  Bridge    │ │  Coordinator  │
│ · Linear   │ │  Daemon    │ │  · TeamCreate │
│ · GitHub   │ │  Phases    │ │  · TeamDelete │
│ · Gitee    │ │  3,4,5,8,11│ │  · SendMessage│
│ · GitCode  │ │  + Remote  │ │  · Workers    │
└────────────┘ └────────────┘ └───────────────┘
                     │
                     ▼
       ┌─────────────────────────────────────┐
       │         上游 clawcodex              │
       │  query() · tool_system · providers  │
       │  TUI · REPL · MCP · Hooks · Memory  │
       │  （完整架构见 README.md.raw）        │
       └─────────────────────────────────────┘
```

---

## 仓库结构（仅本 fork）

```text
extensions/                          # 本 fork 全部新增都在这里
├── orchestrator/                    #   - 自主 issue 处理器
│   ├── orchestrator.py              #   - 守护进程主循环
│   ├── tracker.py                   #   - tracker 抽象基类
│   ├── linear/                      #   - Linear 适配器
│   ├── issue_registry.py            #   - JSON 注册表
│   ├── clarification.py             #   - 三通道求解器
│   ├── clarification_queue.py       #   - 13 状态队列
│   ├── agent_runner.py              #   - 单 issue 的 agent 执行
│   ├── git_sync.py                  #   - commit / push / sync + 验证门
│   ├── review_feedback.py           #   - F-37 PR 评审自动修复
│   ├── status_dashboard.py          #   - HTTP/SSE LiveView
│   ├── workspace.py                 #   - worktree 生命周期
│   ├── workspace_locator.py
│   ├── progress_reporter.py
│   ├── approval_policy.py
│   ├── workflow.py + workflow_store.py
│   ├── templates/workflow.template.md
│   └── cli/                         #   - server、issue、dashboard 子命令
├── pos_converter/                   #   - POS 转 Agent 编译器
│   ├── sdk_parser.py
│   ├── skill_grouper.py
│   ├── agent_builder.py
│   └── templates.py
├── providers_ext/
│   └── litellm_provider.py          #   - LiteLLM 兜底 provider
├── tool_system_ext/                 #   - 工具包 + 注册表扩展
│   ├── bundles.py
│   ├── registry_ext.py
│   └── agent_config.py
├── capabilities/                    #   - 横切协议
└── api/                             #   - 编排 + query 公开 API

clawcodex_ext/                       # 下游 CLI + 服务
├── cli/                             #   - clawcodex-dev 入口（parser、dispatch、runners）
├── cron_system/                     #   - 分布式 cron 调度器
├── frontend/                        #   - headless 前端
├── runtime/                         #   - RuntimeContext 工厂
└── tui/                             #   - 扩展 Textual TUI（8 个钩子点）
```

`src/` 全部归上游所有 —— 上游架构图见 [`README.md.raw`](https://gitcode.com/chadwweng/clawcodex/blob/main/README.md.raw) 和 [`docs/ARCHITECTURE.md`](https://gitcode.com/chadwweng/clawcodex/blob/main/docs/ARCHITECTURE.md)。

---

## 路线图（本 fork）

| F-id | 功能 | 状态 |
|---|---|---|
| F-34 | 下游 CLI / TUI / Runtime 拆分（`clawcodex_ext/`） | ✅ 阶段 1-3 完成 |
| F-37 | 同一分支上 PR 评审意见自动修复 | ✅ |
| F-38 | pre-commit / pre-push / post-sync 验证门 + 报告 | ✅ |
| F-39 | Issue 重跑标签（`agent:retry` / `agent:follow-up` / `agent:blocked`） | ✅ |
| — | 编排器守护进程 + 4 个 tracker + LiveView 仪表盘 | ✅ |
| — | POS 转 Agent 编译器 | ✅ |
| — | 定时任务系统（分布式锁 + jitter） | ✅ |
| — | LiteLLM provider | ✅ |
| — | 协调器 / TeamCreate / TeamDelete | ✅ |
| — | 工具包（`bare` / `default` / `clawcodex` / `all`） | ✅ |
| — | 桥接守护进程阶段 3、4、5、8、11 | ✅ |
| — | 8 个 TUI 扩展钩子 | ✅ |

完整 F-feature 待办清单和当前活跃路线图见 [`docs/FEATURE_PLAN.md`](https://gitcode.com/chadwweng/clawcodex/blob/main/docs/FEATURE_PLAN.md)。

---

## 开发

```bash
git clone https://gitcode.com/chadwweng/clawcodex.git
cd clawcodex
pip install -e ".[dev]"

# 只跑本 fork 自己的测试
pytest tests/test_orchestrator.py -v
pytest tests/test_cron_system.py -v
pytest tests/test_pos_converter.py -v
pytest tests/test_bridge.py -v

# 或者跑除上游集成测试外的全部
pytest tests/ -m "not integration" -v
```

[`CONTRIBUTING.md`](https://gitcode.com/chadwweng/clawcodex/blob/main/CONTRIBUTING.md) 涵盖 PR 规范。[`upstream_sync/`](https://gitcode.com/chadwweng/clawcodex/blob/main/upstream_sync) 提供了从上游 TypeScript 参考拉新章节的工具。

---

## 与上游同步

本 fork 跟踪上游 `clawcodex` 仓库。同步流水线在 `upstream_sync/`，设计文档在 [`docs/UPSTREAM_SYNC_DESIGN.md`](https://gitcode.com/chadwweng/clawcodex/blob/main/docs/UPSTREAM_SYNC_DESIGN.md)。上游有更新时跑：

```bash
python -m upstream_sync.pull --since 2026-05-20
python -m upstream_sync.verify
pytest tests/ -m "not integration" -v
```

---

## 许可证

[MIT](https://gitcode.com/chadwweng/clawcodex/blob/main/LICENSE) —— 与上游 `clawcodex` 相同。`extensions/` 和 `clawcodex_ext/` 内的下游新增也按相同的 MIT 条款发布。

这是一个独立项目，与 Anthropic 无关。基于公开记录的 Claude Code TypeScript 参考实现构建，由上游团队移植到 Python，再在本 fork 中扩展。

---

## 致谢

- **clawcodex** —— 本 fork 所基于的上游 Claude Code Python 移植
- **Claude Code**（Anthropic）—— 原始 TypeScript 架构
- **Aider** · **Cline** · **Continue** · **OpenHands** —— CLI / TUI 模式参考
- **LiteLLM** —— 兜底 provider 层

---

<div align="center">

**如果你觉得自主 issue 流水线有用，欢迎 Star ⭐ 支持本仓库。**

[⬆ 回到顶部](#clawcodex-devmind)

</div>
