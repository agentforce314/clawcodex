# ClawCodex ROADMAP

> 文档路径: `ROADMAP.md`
> 信息来源: `docs/ARCHIVED_FEATURES.md`、`docs/FEATURE_PLAN.md`
> 版本: v2.0
> 更新日期: 2026-06-03
> 组织方式: IR / SR / AR 三层能力路线图

---

## 0. 路线图定义

ClawCodex 的路线图按能力成熟度而不是单纯按时间排序，分为三层：

| 层级 | 名称 | 定位 | 典型周期 | 核心问题 |
|------|------|------|----------|----------|
| IR | Infrastructure Roadmap | 基础设施与可运行底座 | 当前版本至近期收敛 | Agent 能否稳定运行、被编排、被恢复、被观察 |
| SR | Scenario Roadmap | 场景化产品能力 | 近期至中期交付 | 用户能否把真实研发流程交给 ClawCodex 处理 |
| AR | Autonomy Roadmap | 自主进化与自升级闭环 | 中长期演进 | Agent 能否发现新能力、规划新特性、开发并验证自身更新 |

每个最小特性均包含：特性名称、提供的特性、用户视角感知功能、开发状态、开发工时、交付件。

开发状态定义：

| 状态 | 含义 |
|------|------|
| ✅ 已完成 | 已实现并在归档文档中记录 |
| 🟡 进行中 | 核心模块或设计已存在，但端到端链路尚未收敛 |
| 📋 规划中 | 已有设计或明确需求，待实施 |
| 🔭 长期规划 | 战略方向明确，仍需拆解设计 |

---

## 1. 总体产品主线

ClawCodex 的目标不是只做一个交互式编码 CLI，而是逐步形成“本地开发 Agent → 多 Agent 编排 → 远程自动值守 → 社区能力吸收 → 自我升级”的闭环系统。

```text
IR: 可运行底座
  Agent Loop / Tool / Skill / Provider / Memory / Session / Cron / Permission
        ↓
SR: 场景化研发自动化
  Orchestrator / Issue → PR / PR Review Follow-up / POS to Agent / Remote Attach
        ↓
AR: 自主进化闭环
  开源社区观察 → 新特性雷达 → 自主规划 → 自主开发 → 验证发布 → 经验沉淀
```

长期闭环目标：ClawCodex 能定期收集当前最新 Agent 开源社区的新特性，结合自身架构和用户使用数据生成新特性规划，再通过 Orchestrator、Cron、远程启动、Agent2Agent 协作、POS to Agent 转换和验证报告系统开发 ClawCodex 自身，最终形成 Agent 自己升级/更新自己的能力循环。

---

## 2. IR — Infrastructure Roadmap

IR 层承载所有上层场景能力，重点是稳定、可恢复、可扩展、可观测和可被编排。

### 2.1 IR-1 Agent 核心运行底座

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Agent 执行循环 | 多轮模型调用、工具调用、权限检查、会话推进 | 用户可以在 REPL/TUI/headless 中持续让 Agent 理解任务并执行工具 | ✅ 已完成 | 已完成 | py 代码、会话运行时、测试 |
| Fork Subagent | 创建独立子 Agent 会话，隔离上下文与任务 | 用户可把复杂工作拆给后台子 Agent 并继续主会话 | ✅ 已完成 | 已完成 | py 代码、Agent 工具、JSONL transcript |
| Resume Agent | 从已有会话断点恢复执行 | 用户可重连、恢复中断任务，不必重新描述上下文 | ✅ 已完成 | 已完成 | py 代码、session 状态文件 |
| Foreground Promotion | 后台 Agent 提升到前台交互 | 用户可把后台任务拉回当前窗口继续处理 | ✅ 已完成 | 已完成 | py 代码、CLI/TUI 行为 |
| Session 管理 | 会话 ID、状态、历史、恢复索引 | 用户可以浏览和恢复历史对话 | ✅ 已完成 | 已完成 | py 代码、JSONL transcript、session 索引 |
| Transcript 管理 | 对话与工具调用结构化记录 | 用户可追溯 Agent 做过什么、调用过哪些工具 | ✅ 已完成 | 已完成 | JSONL transcript、py 解析逻辑 |
| Prompt 构建 | 系统 Prompt、Agent 定义、记忆、工具描述组装 | 用户可通过不同 Agent 类型获得不同能力组合 | ✅ 已完成 | 已完成 | py 代码、Agent markdown/json 定义 |
| Agent 定义系统 | Agent 类型、工具、配置、模型定义 | 用户可选择或配置专用 Agent | ✅ 已完成 | 已完成 | py 代码、Agent 配置文件 |
| Agent 记忆作用域 | 按 user/project/agent/team 等作用域加载记忆 | 用户感知为 Agent 只带入相关长期背景，减少无关上下文 | ✅ 已完成 | 已完成 | py 代码、memory 文件、配置 |

### 2.2 IR-2 Provider 与模型能力底座

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| 多 Provider 支持 | Anthropic、OpenAI、GLM、MiniMax、DeepSeek、OpenRouter 等模型接入 | 用户可按地域、成本、能力选择模型供应商 | ✅ 已完成 | 已完成 | py provider 代码、配置 |
| LiteLLM 适配器 | 统一 100+ 模型接口的适配层 | 用户可通过统一配置切换更多模型 | ✅ 已完成 | 已完成 | py 适配器、配置开关 |
| LiteLLM Provider 替换 | 用 LiteLLM 替代部分直连 provider 重复逻辑 | 用户感知为模型切换更一致，新增模型更快 | ✅ 已完成 | 已完成 | py provider 代码、兼容测试 |
| CLI 模型供应商切换 | `model` / `provider` 子命令、解析优先级、存储 | 用户可通过命令查看、设置和切换模型 | ✅ 已完成 | 已完成 | py CLI 代码、JSON 配置 |
| Provider Token 追踪 | 统一 token 计数接口 | 用户可看到更准确的上下文和成本提示 | ✅ 已完成 | 已完成 | py 代码、状态显示 |

### 2.3 IR-3 Tool / Skill 扩展底座

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| 内置工具集 | Read、Write、Edit、Glob、Grep、Bash、Task、Agent、MCP、Cron fallback 等工具 | 用户可以让 Agent 读写代码、运行命令、搜索项目和管理任务 | ✅ 已完成 | 已完成 | py 工具代码、schema |
| MCP 协议基础 | Stdio、HTTP/SSE、WebSocket、OAuth、HTTPS/XSS 硬化 | 用户可接入外部 MCP 服务扩展工具能力 | ✅ 已完成 | 已完成 | py MCP 客户端、配置 |
| 工具按需加载 | Bare/Default/ClawCodex/All 工具模式 | 用户感知为上下文更轻，工具列表更聚焦 | ✅ 已完成 | 已完成 | py registry、bundle 配置 |
| ToolSearch | TF-IDF 语义工具搜索 | 用户可通过搜索发现可用工具 | ✅ 已完成 | 已完成 | py 搜索代码、索引 |
| ExecuteExtraTool | 延迟工具执行机制 | 用户可在工具很多时按需调用额外工具 | ✅ 已完成 | 已完成 | py 工具、动态注册逻辑 |
| Skills System Extension | 下游技能扩展层、bundle、路径、hook、cache | 用户可安装和调用 ClawCodex 专属 skill，且不破坏上游同步 | ✅ 已完成 | 已完成 | py 代码、skill bundle、配置 |
| MCP 资源缓存 | MCP resource 读取缓存 | 用户感知为外部资源加载更快、重复请求更少 | 📋 规划中 | 1 周 | py 缓存模块、测试 |
| MCP Batch 工具调用 | 批量执行 MCP 工具 | 用户可让 Agent 更高效地处理多步外部调用 | 📋 规划中 | 1.5 周 | py MCP 批处理代码、测试 |
| MCP Progress 通知 | MCP 长任务进度反馈 | 用户可看到外部长任务执行进度 | 📋 规划中 | 1 周 | py 通知代码、TUI/REPL 渲染 |

### 2.4 IR-4 Frontend、权限与交互底座

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| REPL Core | prompt_toolkit + Rich 交互式命令行 | 用户可在终端中与 Agent 对话和执行命令 | ✅ 已完成 | 已完成 | py REPL 代码、命令注册 |
| Textual TUI | 图形化终端界面、状态区、权限选择器 | 用户可用更丰富的终端 UI 操作 Agent | ✅ 已完成 | 已完成 | py TUI 代码、组件 |
| REPL/TUI 双向切换 | `/tui` 与 `/repl` 状态保留切换 | 用户可在两种界面间无缝切换 | ✅ 已完成 | 已完成 | py frontend 代码 |
| Shift+Tab 权限循环 | default、acceptEdits、plan、bypass/dontAsk 权限切换 | 用户可快速调整自动化程度 | ✅ 已完成 | 已完成 | py keybinding、UI 状态 |
| Permission Settings Schema 重构 | 权限配置 schema 正交化 | 用户感知为权限配置更清晰、更可审计 | ✅ 已完成 | 已完成 | py schema、配置迁移 |
| CLI/TUI Frontend 解耦 | runtime protocol、frontend registry、扩展钩子 | 用户感知为 REPL/TUI/headless 行为更一致，后续扩展更稳 | ✅ 已完成 | 已完成 | py runtime/frontend 代码 |
| REPL Ctrl+B 后台运行 | REPL 中把当前任务后台化 | 用户可像 TUI 一样在 REPL 中把长任务放到后台 | 📋 规划中 | 1 周 | py REPL 代码、快捷键、测试 |
| Auto 权限模式 | LLM 分类器自动判断工具调用是否可执行 | 用户在长任务中减少重复确认，同时保留安全边界 | 📋 规划中 | 4 周 | py classifier、cache、权限集成、测试 |

### 2.5 IR-5 后台运行、恢复与远程桥接底座

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| BackgroundState | 进程级后台状态管理 | 用户可安全后台化和恢复任务 | ✅ 已完成 | 已完成 | py 状态代码 |
| TailFollower | JSONL 增量 tail | 用户恢复会话时只读取新增事件，速度更快 | ✅ 已完成 | 已完成 | py tail 代码 |
| SessionWatcher | 目录变更监控，支持平台 fallback | 用户可在会话变化时得到及时更新 | ✅ 已完成 | 已完成 | py watcher 代码 |
| TUI Ctrl+B 后台化 | TUI 当前任务后台运行 | 用户可从 TUI 释放当前界面继续其他操作 | ✅ 已完成 | 已完成 | py TUI 行为 |
| Graceful shutdown | SIGTSTP/SIGINT/SIGTERM 等安全处理 | 用户中断后不易丢失会话状态 | ✅ 已完成 | 已完成 | py 信号处理 |
| Bridge 多 Session Daemon | 多会话 daemon 轮询与桥接 | 用户可管理多个长期运行 session | ✅ 已完成 | 已完成 | py daemon、HTTP client |
| Remote Bridge Core | 远程会话生命周期和跨进程通信 | 用户可远程连接和控制 Agent 会话 | ✅ 已完成 | 已完成 | py bridge 代码、API |
| REPL Bridge | REPL 与 bridge 集成 | 用户可把本地交互接入桥接会话 | ✅ 已完成 | 已完成 | py bridge 代码 |
| Remote Control WebUI | Docker + WebUI 远程控制 | 用户可通过浏览器远程查看、启动、接管 Agent | 🔭 长期规划 | 6-8 周 | Docker 镜像、Web UI、py API、鉴权配置 |

### 2.6 IR-6 Cron、调度与自动值守底座

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Cron 核心模型与解析 | 5 字段 cron、next run、human schedule、任务模型 | 用户可用标准 cron 表达式描述定时任务 | 🟡 进行中 | 已完成核心，待接线 | py 代码、测试 |
| Durable / Session Task Store | `.claude/scheduled_tasks.json` 与会话内任务分离 | 用户可选择任务是否跨 CLI 重启保留 | 🟡 进行中 | 1 周 | py 存储代码、JSON 配置 |
| Scheduler Lock 与 Jitter | 文件锁、防重复触发、确定性抖动 | 用户感知为多窗口不会重复执行定时任务 | 🟡 进行中 | 1 周 | py lock/jitter 代码 |
| Cron Runtime 接线 | REPL/TUI/headless 真实路径使用扩展 Cron 工具和 scheduler | 用户创建 cron 后任务会真正按时进入执行队列 | 🟡 进行中 | 2 周 | py runtime/frontend 代码、smoke 测试 |
| CronDispatchBridge | scheduled fire 进入真实 prompt 执行队列 | 用户可看到定时任务像普通输入一样被执行 | 📋 规划中 | 1.5 周 | py dispatch bridge、运行记录 |
| Cron Run Store | queued/running/completed/failed/cancelled 生命周期落盘 | 用户可查询每次定时任务运行结果 | 📋 规划中 | 1 周 | py run store、JSONL/JSON 账本 |
| `/loop` Skill | interval prompt 循环执行，默认 10m 并立即执行一次 | 用户可一句话设置循环任务 | 🟡 进行中 | 0.5 周 | skill 代码、CronCreate 集成 |
| `/cron-list` / `/cron-delete` | 定时任务列表和删除命令 | 用户可管理当前和持久化 cron 任务 | 📋 规划中 | 0.5 周 | skill 代码、表格输出 |
| Missed One-shot 安全确认 | 错过的一次性任务启动后询问是否补跑 | 用户不会因为离线期间错过任务而被静默执行敏感 prompt | 📋 规划中 | 0.5 周 | py notification、UI 文案 |
| Teammate Ownership | cron 任务按 agent/team 归属过滤和路由 | 用户在多 Agent 场景中不会把任务发错 Agent | 📋 规划中 | 1 周 | py ownership 字段、过滤逻辑 |

### 2.7 IR-7 可观测性、稳定性与开放替代

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| ProgressReportTool | Agent 阶段性进度写入任务看板 | 用户可看到长任务当前阶段和阶段产出 | ✅ 已完成 | 已完成 | py 工具、任务 metadata |
| TaskInspect / TaskDirectives | Manager 查看 Worker 状态并注入指令 | 用户可让 Manager Agent 管理多个 Worker Agent | ✅ 已完成 | 已完成 | py 工具、队列逻辑 |
| ProgressReporter Sink 重构 | per-session progress sink、CompositeProgressSink | 用户在多 issue 并发时看到正确进度，不串任务 | 📋 设计完成 | 2 周 | py sink 协议、event log、测试 |
| 工具 / Skill 调用统计 | 统一 JSONL 调用日志或 transcript 聚合 | 用户可知道哪些工具常用、失败率如何 | 📋 规划中 | 1 周 | py stats、JSONL 日志、查询命令 |
| 使用频率工具裁剪 | 低频工具隐藏、建议或按需加载 | 用户感知为上下文更轻、工具列表更干净 | 📋 规划中 | 1 周 | py pruning、配置 |
| sessionStorage 容量限制 | LRU 限制 existingSessionFiles | 用户长期运行 daemon 不易 OOM | 📋 规划中 | 2 天 | py LRU、测试 |
| cacheWarning 容量限制 | cacheWarning source entries 限制 | 用户长期运行不易内存泄漏 | 📋 规划中 | 2 天 | py LRU、测试 |
| Outlines 结构化输出增强 | Token 预算、工具决策、压缩策略结构化 | 用户感知为 Agent 决策更稳定、JSON 解析错误更少 | 📋 规划中 | 2 周 | py adapter 集成、Pydantic schema |
| 开源替代组件 | pydantic-settings、python-frontmatter、tree-sitter-bash、GitPython、Pluggy、Outlines | 用户感知为系统更稳定，维护成本更低 | ✅ 已完成 | 已完成 | py 依赖、替换代码、测试 |

---

## 3. SR — Scenario Roadmap

SR 层把 IR 能力组合成用户可直接感知的研发自动化场景，重点是 Issue、PR、review、业务工作流、远程值守和多 Agent 协作。

### 3.1 SR-1 Issue 到 PR 的自主编排

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Orchestrator 主循环 | 轮询 issue、领取、创建 workspace、运行 Agent | 用户可启动 daemon 自动处理 issue | ✅ 已完成 | 已完成 | py orchestrator、CLI |
| WorkspaceManager | 每 issue 隔离工作区 | 用户可同时处理多个 issue，互不污染 | ✅ 已完成 | 已完成 | py workspace、目录产物 |
| 多 Tracker 支持 | Linear、GitHub、Gitee、GitCode、LocalTracker 抽象 | 用户可把不同平台 issue 接入同一自动化流程 | ✅ 已完成 | 已完成 | py tracker adapter、配置 |
| LocalTracker 本地 Issue 源 | Markdown/JSON 本地 issue、front matter 状态写回 | 用户可不用远程平台，在本地文件夹中排队任务 | ✅ 已完成 | 已完成 | py LocalTracker、md/json issue 文件 |
| Human Review Gate | 本地 tracker 完成后进入 pending_review，人工 approve/reject | 用户可先审查 diff，再决定是否接受本地 Agent 修改 | ✅ 已完成 | 已完成 | py CLI、状态字段、diff 输出 |
| IssueRegistry | issue→branch→commit→PR→report 状态持久化 | 用户重启 daemon 后不会重复处理已完成 issue | ✅ 已完成 | 已完成 | JSON registry、py store |
| Retry / Backoff | 失败重试、指数退避、最大重试次数 | 用户感知为临时失败会自动恢复，持续失败会停下等待处理 | ✅ 已完成 | 已完成 | py 调度代码、配置 |
| Issue State 前置检查 | launch 前重新确认 issue 是否 active | 用户关闭或取消 issue 后 Agent 不会继续误处理 | ✅ 已完成 | 已完成 | py tracker 调用 |
| 已有 PR 跳过 | launch 前检测已有 PR | 用户不会因为 daemon 重启重复创建 PR | ✅ 已完成 | 已完成 | py git sync/tracker 调用 |
| Orchestrator CLI 运维 | server/issue/dashboard noun-verb 命令集 | 用户可查看、暂停、恢复、停止、接管、注入提示 | ✅ 已完成 | 已完成 | py CLI、dashboard |
| Dashboard LiveView | issue 状态、tool call、LLM 摘要实时展示 | 用户可观察无人值守任务进展 | ✅ 已完成 | 已完成 | py dashboard、event stream |

### 3.2 SR-2 澄清、重跑与人机协同闭环

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| 三通道 ClarificationQueue | 本地 dashboard、CLI queue、tracker 评论三通道澄清 | Agent 不确定时会询问用户或 issue 作者，而不是猜测 | ✅ 已完成 | 已完成 | py queue、JSON 文件、tracker 评论 |
| Clarification 冲突处理 | operator 优先、超时升级、去重、过期拒绝 | 用户可从多渠道回答，系统自动裁决有效答案 | ✅ 已完成 | 已完成 | py 状态机、配置 |
| Issue 重跑 label 通道 | `agent:retry` / `agent:follow-up` / `agent:blocked` | 用户可通过标签表达重做、追加修改或永久跳过 | ✅ 已完成 | 已完成，真实环境待继续验证 | py tracker、registry 字段 |
| Issue 重跑 comment 命令 | `/agent retry` / `/agent follow-up` / `/agent unblock` | 外部协作者可在 issue 评论中触发 Agent 重跑意图 | ✅ 已完成 | 已完成，真实环境待继续验证 | py comment parser、bot 确认评论 |
| Issue 重跑 CLI 兜底 | `orchestrator issue retry --mode ...` | 本地操作者可不用改 registry 直接重置任务 | ✅ 已完成 | 已完成 | py CLI、audit.jsonl |
| 重跑限频与角色校验 | max retries、maintainer/author 检查、审计 | 用户不会被恶意评论无限触发重跑 | ✅ 已完成 | 已完成 | py 权限检查、audit 日志 |
| Operator Hint 注入 | 运行中向 issue Agent 注入提示 | 用户可不中断 Agent 的情况下纠偏 | ✅ 已完成 | 已完成 | py CLI、operator_hints 文件 |
| Takeover 接管 | 终止 Agent 并进入 REPL 接管 workspace | 用户可在自动化失控或复杂场景下手动接手 | ✅ 已完成 | 已完成 | py CLI、REPL attach |

### 3.3 SR-3 验证、报告与 PR 质量闭环

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Verification Gate | commit/push 前运行 test/build/lint 或 hooks | 用户看到未验证通过的代码不会被自动推送 | ✅ 已完成 | 已完成 | py git_sync、workflow 配置 |
| 结构化运行报告 | `.reports/{id}.md` 与 `.reports/{id}.json` | 用户可阅读本次 Agent 修改摘要、验证结果、diff stat | ✅ 已完成 | 已完成 | Markdown 报告、JSON 报告、py writer |
| PR Body 报告回写 | PR body 包含 Issue、Branch、Commit、Verification、Report | Reviewer 打开 PR 即可看到 Agent 工作产物 | ✅ 已完成 | 已完成 | py tracker update、PR body 模板 |
| PR 汇总评论 | Run Complete 与 Git Sync 合并为一条总结评论 | 用户不会被多条重复 bot 评论干扰 | ✅ 已完成 | 已完成 | py comment 逻辑 |
| Progress event log | PhaseComplete 写入 issue event log | 用户可通过 `issue tail` 看到阶段进度 | ✅ 已完成 | 已完成 | ndjson event log、CLI 渲染 |
| PR Review Feedback 模型 | PullRequestFeedback 规范化评论、inline、summary、CI | 用户可让 Agent 理解 reviewer 评论和 CI 失败 | 📋 规划中 | 1 周 | py dataclass、tracker interface |
| PR Feedback API 接入 | GitHub/Gitee/GitCode review comments 与 checks/pipelines | 用户无需手动复制 PR 评论给 Agent | 📋 规划中 | 2 周 | py repo client、API 映射 |
| Review Follow-up Poller | 周期性扫描 open PR 新反馈并调度 follow-up run | 用户感知为 PR 收到评论后 Agent 自动继续修 | 📋 规划中 | 2 周 | py orchestrator poller、配置 |
| Review-fix Prompt Builder | 只处理 PR feedback，不扩大需求范围 | 用户看到 Agent 针对 review 做最小修改 | 📋 规划中 | 1 周 | py prompt builder、模板 |
| 同 PR 分支 Follow-up Sync | 追加 commit + push 原分支，不创建新 PR | Reviewer 在原 PR 看到新提交解决评论 | 📋 规划中 | 1 周 | py git_sync mode、测试 |
| Feedback 幂等 Store | 已处理 feedback/check id 记录 | 用户不会看到 Agent 对同一条评论反复修复 | 📋 规划中 | 1 周 | JSON store、registry 字段 |
| 评论回复与处理摘要 | 自动回复哪些评论已处理、哪些需人工确认 | 用户和 reviewer 可追踪 Agent 的处理边界 | 📋 规划中 | 1 周 | py tracker reply、PR 评论 |

### 3.4 SR-4 多 Agent 编排与 Agent2Agent 协作

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| TeamCreate / TeamDelete | 团队创建和删除 | 用户可启动一个由多个 Agent 组成的团队 | ✅ 已完成 | 已完成 | py Team 工具、team JSON |
| Team members 数组 | 团队成员 schema、名称注册、成员状态 | 用户可看到团队中有哪些 Agent 正在工作 | ✅ 已完成 | 已完成 | JSON team 文件、py registry |
| Manager / Worker 角色识别 | 通过 TaskInspect + TaskDirectives 工具组合识别 Manager | 用户无需配置复杂角色即可获得管理型 Agent | ✅ 已完成 | 已完成 | py 工具可见性逻辑 |
| TaskInspect | Manager 查询 Worker 状态和输出 | 用户可让 Manager 监督子任务进展 | ✅ 已完成 | 已完成 | py 工具、状态读取 |
| TaskDirectives | Manager 向 Worker 注入优先级消息 | 用户可让 Manager 动态纠正或重排 Worker 工作 | ✅ 已完成 | 已完成 | py 工具、pending message 队列 |
| 优先级消息队列 | queue/drain 按 priority 消费 | 用户的紧急指令不会被普通消息淹没 | ✅ 已完成 | 已完成 | py queue 逻辑 |
| 权限规则传递 | Manager 给 Worker 传递 permission mode 与 allow rules | 用户可在团队任务中控制 Worker 自动化边界 | ✅ 已完成 | 已完成 | py 权限传递代码 |
| Coordinator 轻量工具集 | 编排场景的轻量协调工具 | 用户可用更小上下文进行任务分派与观察 | ✅ 已完成 | 已完成 | py 工具 bundle |
| Shared / Sequential Workspace | 共享或顺序 workspace 策略 | 用户可选择多 Agent 同仓协作或逐个串行处理 | ✅ 已完成 | 已完成 | py workspace 策略、配置 |
| Tool-call 审计旁路 | 编排场景下记录工具调用审计流 | 用户可审查无人值守任务实际操作 | ✅ 已完成 | 已完成 | ndjson audit、py sink |
| A2A 协议化消息 | 把当前 Manager/Worker 消息抽象为 Agent2Agent 协议 | 用户可连接本地、远程、第三方 Agent 进行协作 | 🔭 长期规划 | 4-6 周 | py protocol、JSON schema、bridge adapter |
| A2A 能力发现 | Agent 发布自身工具、技能、权限和状态 | 用户可让 Manager 自动选择合适 Agent | 🔭 长期规划 | 3-4 周 | py discovery、capability manifest |

### 3.5 SR-5 POS to Agent 与业务 Agent 产品化

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| CreateAgentTool Spec | AgentToolSpec、bash/http/python call_type | 用户可让 Agent 描述并创建新工具 | 📋 规划中 | 1 周 | py dataclass、JSON schema |
| CreateAgentTool Validators | 命令白名单、HTTP 方法白名单、Python 函数白名单、防注入 | 用户可安全使用 Agent 动态创建的工具 | 📋 规划中 | 1 周 | py validators、测试 |
| CreateAgentTool Factory | 将声明式 spec 构造成可注册工具 | 用户创建工具后可立即调用 | 📋 规划中 | 1.5 周 | py factory、call handlers |
| Agent Tool Persistence | `~/.clawcodex/agent-tools/{name}.json` | 用户重启后仍能使用 Agent 创建的工具 | 📋 规划中 | 0.5 周 | JSON 工具定义、loader |
| CreateAgentTool 工具入口 | `CreateAgentTool` 对外工具 | 用户可让 Agent 根据 CLI/API 规范扩展自己 | 📋 规划中 | 1 周 | py tool、schema、测试 |
| POS SDK Parser | OpenAPI JSON/URL/方法列表解析为原子接口 | 用户可把业务系统 SDK 输入给 ClawCodex | 📋 规划中 | 1.5 周 | py parser、JSON 输入 |
| Skill Grouper | 将原子接口按业务流程分组成 Skill | 用户看到专业系统被拆成可理解的步骤 | 📋 规划中 | 1 周 | py grouper、mapping config |
| Agent Builder | 根据 Skill 和工具生成 Agent 定义 | 用户可得到一个面向业务的专用 Agent | 📋 规划中 | 1 周 | py builder、Agent JSON |
| `/convert-pos-to-agent` Skill | 一条命令转换专业系统为 Agent | 用户可把 CI/CD、数据分析、ML pipeline 等 POS 转成 Agent | 📋 规划中 | 1 周 | skill 代码、模板、配置 |
| POS Agent 持久化 | `~/.clawcodex/agents/<name>.json` | 用户可长期保存和复用业务 Agent | 📋 规划中 | 1 周 | Agent JSON、loader |
| 主 Agent 指定 | `clawcodex --agent <name>` / default_agent 配置 | 用户可直接进入专用 Agent 工作模式 | 📋 规划中 | 0.5 周 | CLI 参数、settings JSON |
| Daemon + Attach | `clawcodex --daemon --agent` 与 `clawcodex attach` | 用户可长期运行业务 Agent，并在新窗口重连 | 📋 规划中 | 2 周 | py daemon、attach 协议、socket/pipe |

### 3.6 SR-6 自动值守与远程启动场景

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Orchestrator Daemon 值守 | `orchestrator server start/status/stop` | 用户可让 ClawCodex 持续值守 issue 队列 | ✅ 已完成 | 已完成 | py daemon CLI、状态文件 |
| Cron 驱动值守 | cron 定期触发检查、报告、社区扫描等任务 | 用户可把重复巡检任务交给 Agent | 🟡 进行中 | 2-3 周 | py cron runtime、scheduled tasks JSON |
| RemoteTrigger | 远程触发本地或远端 Agent 任务 | 用户可从外部系统启动 ClawCodex 工作流 | 🔭 长期规划 | 3-4 周 | py/API 入口、鉴权配置、审计日志 |
| Remote Scheduled Agent | 远程 cron schedule 管理 | 用户可在远端配置定时 Agent，无需本地终端常驻 | 🔭 长期规划 | 4-6 周 | remote trigger 配置、server API、JSON schedule |
| Away Summary | 终端失焦或长时间离开后生成摘要 | 用户回来时可快速知道 Agent 做了什么、卡在哪里 | 📋 规划中 | 3 周 | py service、REPL/TUI 渲染、`/recap` skill |
| Autonomy Status | 汇总 cron、runs、orchestrator、team 状态 | 用户可用一个命令查看自动值守系统健康度 | 📋 规划中 | 2 周 | py status、表格输出、JSON 输出 |
| Remote Web Dashboard | Web 查看 issue、cron、team、runs | 用户可在浏览器中监督无人值守任务 | 🔭 长期规划 | 6-8 周 | Web UI、Docker 镜像、py API |

---

## 4. AR — Autonomy Roadmap

AR 层是 ClawCodex 的长期差异化：让 Agent 不仅执行用户任务，还能持续观察 Agent 开源社区、识别可迁移能力、自主规划、自主开发、自主验证并更新自己。

### 4.1 AR-1 开源 Agent 社区新特性雷达

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Agent 项目源注册表 | 维护 Claude Code、Aider、SWE-agent、OpenHands、AutoGen、CrewAI、LangGraph 等观察源 | 用户可配置 ClawCodex 关注哪些开源 Agent 项目 | 🔭 长期规划 | 1 周 | JSON/YAML 源配置、py loader |
| Release / Commit Watcher | 定期抓取 release notes、commits、PR、issues | 用户可收到“社区出现了哪些新 Agent 能力”的摘要 | 🔭 长期规划 | 2 周 | py fetcher、cron 配置、缓存 |
| Feature Extraction Pipeline | 从 release/PR/issue 文本中抽取候选特性 | 用户看到结构化候选特性，而不是一堆链接 | 🔭 长期规划 | 2-3 周 | py extractor、JSON feature records |
| Feature Dedup / Taxonomy | 按工具、记忆、编排、权限、远程、UI、模型等分类去重 | 用户可按能力类别浏览社区趋势 | 🔭 长期规划 | 1.5 周 | py classifier、taxonomy JSON |
| Community Feature Digest | 周报/月报输出 | 用户可快速了解最新 Agent 开源社区变化 | 🔭 长期规划 | 1 周 | Markdown 报告、JSON 摘要 |
| 趋势评分模型 | 按热度、成熟度、适配成本、战略价值评分 | 用户可看到哪些新能力值得 ClawCodex 优先吸收 | 🔭 长期规划 | 2 周 | py scoring、配置权重 |

### 4.2 AR-2 自我规划与路线图生成

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Capability Gap Analyzer | 对比 ClawCodex 现有能力与社区候选特性 | 用户可看到“我们缺什么、已有优势是什么” | 🔭 长期规划 | 2 周 | py analyzer、Markdown gap report |
| Architecture Fit Checker | 检查候选特性是否符合 downstream 解耦边界 | 用户不用担心新特性破坏上游同步能力 | 🔭 长期规划 | 1.5 周 | py checker、规则配置 |
| Feature Proposal Generator | 自动生成 FEATURE_PLAN 风格设计稿 | 用户可直接审阅候选特性的设计方案 | 🔭 长期规划 | 2 周 | Markdown proposal、JSON metadata |
| Roadmap Auto-Updater | 根据评分和依赖更新 ROADMAP / FEATURE_PLAN 草案 | 用户可让 Agent 自动维护路线图草案 | 🔭 长期规划 | 1 周 | py doc updater、Markdown diff |
| Dependency Planner | 生成 IR/SR/AR 依赖图和实施顺序 | 用户可看到特性间依赖和推荐开发路径 | 🔭 长期规划 | 1.5 周 | JSON graph、Mermaid/Markdown 图 |
| User Review Gate | 自规划结果必须经用户审批后进入开发队列 | 用户保留路线图决策权，不被 Agent 自动改方向 | 🔭 长期规划 | 1 周 | CLI review 命令、approval JSON |

### 4.3 AR-3 自主开发 ClawCodex 自身

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Self-Issue Generator | 将已批准 proposal 转成 LocalTracker/GitHub issue | 用户批准后，Agent 自动生成可执行开发任务 | 🔭 长期规划 | 1 周 | Markdown issue、front matter、tracker entry |
| Self-Orchestrator Runner | Orchestrator 处理 ClawCodex 自身 issue | 用户可让 ClawCodex 自动开发自己的功能分支 | 🔭 长期规划 | 1 周 | workflow 配置、daemon 任务 |
| Self-Workspace Isolation | 自开发任务使用独立 worktree/workspace | 用户本地工作区不被自升级任务污染 | 🔭 长期规划 | 1 周 | py workspace 策略、git worktree 配置 |
| Self-Test Matrix | 根据特性类型选择测试、lint、typecheck、docs 检查 | 用户看到每次自升级都有验证矩阵 | 🔭 长期规划 | 2 周 | py test planner、workflow YAML |
| Self-PR Generator | 自动提交分支并生成 PR | 用户可像 review 普通贡献一样 review Agent 自升级 PR | 🔭 长期规划 | 1 周 | py git_sync、PR 模板 |
| Self-Review Agent Team | code reviewer、test analyzer、silent failure hunter、simplifier 等 Agent 互审 | 用户看到自升级 PR 经过多 Agent 检查 | 🔭 长期规划 | 2-3 周 | Agent configs、review reports |
| Self-Fix Follow-up | 读取 PR review/CI 反馈并自动追加修复 commit | 用户只需 review，Agent 自动跟进反馈 | 🔭 长期规划 | 依赖 SR-3，1 周集成 | py follow-up 配置、registry 状态 |

### 4.4 AR-4 自我更新、发布与回滚

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Update Candidate Registry | 记录候选更新版本、PR、验证状态、风险等级 | 用户可看到哪些自升级版本可安装 | 🔭 长期规划 | 1 周 | JSON registry、CLI 输出 |
| Binary / Package Build | 构建 wheel、二进制包或镜像 | 用户可直接安装经过验证的 ClawCodex 包 | 🔭 长期规划 | 2-3 周 | wheel、binary、Docker 镜像 |
| Staged Rollout | dev/canary/stable 分阶段启用 | 用户可先在隔离环境试用新能力 | 🔭 长期规划 | 2 周 | release channel 配置、feature flags |
| Self-Update Command | `clawcodex update --candidate ...` | 用户一条命令升级到指定候选版本 | 🔭 长期规划 | 2 周 | py CLI、安装脚本、签名校验 |
| Health Check After Update | 更新后自动运行 smoke、配置检查和回滚点创建 | 用户升级失败时不会陷入不可用状态 | 🔭 长期规划 | 1.5 周 | py health check、日志 |
| Safe Rollback | 保留上一版本并支持回滚 | 用户可快速撤销有问题的自升级 | 🔭 长期规划 | 2 周 | rollback metadata、CLI |
| Release Notes Generator | 从 PR、报告、测试结果生成发布说明 | 用户可理解这次更新新增了什么、风险是什么 | 🔭 长期规划 | 1 周 | Markdown release notes、JSON manifest |

### 4.5 AR-5 自主经验沉淀与策略优化

| 最小特性名称 | 提供的特性 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|--------------|------------|--------------------|----------|----------|--------|
| Development Outcome Store | 记录每个自开发任务的成功、失败、返工原因 | 用户可追踪 Agent 自升级效率 | 🔭 长期规划 | 1 周 | JSONL outcome store、CLI query |
| Failure Pattern Miner | 从失败测试、review 评论、回滚中总结模式 | 用户看到 Agent 后续会避开重复错误 | 🔭 长期规划 | 2 周 | py miner、Markdown report |
| Strategy Memory Writer | 把稳定经验转成 memory/guide/rule 草案 | 用户可审批哪些经验进入长期策略 | 🔭 长期规划 | 1.5 周 | Markdown memory proposal、review gate |
| Prompt / Skill Tuning Loop | 根据失败模式调整自开发 prompt 和 skill | 用户感知为 Agent 自开发越来越稳 | 🔭 长期规划 | 2-3 周 | prompt templates、skill configs |
| Tool Pruning Feedback Loop | 根据真实使用和成功率优化默认工具集 | 用户上下文更轻，常用能力更突出 | 🔭 长期规划 | 依赖 IR-7，1 周集成 | py pruning policy、bundle config |
| Roadmap Retrospective | 每月自动生成路线图完成度和偏差报告 | 用户可审视 Agent 自规划是否可靠 | 🔭 长期规划 | 1 周 | Markdown retrospective、metrics JSON |

---

## 5. 分层依赖图

```text
IR-1 Agent Core
  ├── IR-2 Provider
  ├── IR-3 Tool / Skill
  ├── IR-4 Frontend / Permission
  ├── IR-5 Background / Bridge
  ├── IR-6 Cron
  └── IR-7 Observability
        ↓
SR-1 Issue → PR Orchestrator
  ├── SR-2 Clarification / Retry
  ├── SR-3 Verification / PR Feedback
  ├── SR-4 Multi-Agent / A2A
  ├── SR-5 POS to Agent
  └── SR-6 Remote Autopilot
        ↓
AR-1 Community Radar
  → AR-2 Self-Planning
  → AR-3 Self-Development
  → AR-4 Self-Update / Release
  → AR-5 Self-Learning
```

关键依赖：

| 上游能力 | 解锁能力 | 说明 |
|----------|----------|------|
| Cron 端到端调度 | 自动值守、社区雷达、定期自规划 | 没有真实调度，自升级只能手动触发 |
| Verification Gate + Report | PR feedback follow-up、自开发安全边界 | 自开发必须先能证明改动可验证 |
| PR Review Follow-up | Self-Fix Follow-up | 自升级 PR 需要自动处理 review 和 CI |
| CreateAgentTool | POS to Agent、社区能力吸收 | 动态工具创建是把新 SDK/API 转成 Agent 能力的基础 |
| POS to Agent | 业务 Agent、社区能力产品化 | 专业系统或外部工具可转为长期 Agent |
| Remote Bridge + Attach | 远程值守、自升级运行环境 | 自主运行不能依赖单个前台终端 |
| A2A 协议 | Self-Review Agent Team | 多 Agent 互审需要协议化协作 |
| Usage Stats + Outcome Store | 策略优化和工具裁剪 | 没有数据就无法自我优化 |

---

## 6. 时间节奏建议

### 6.1 近期：IR 收敛与 SR 核心闭环（2026 Q2-Q3）

| 优先级 | 交付目标 | 包含特性 |
|--------|----------|----------|
| P0 | Cron 端到端收敛 | Runtime 接线、dispatch bridge、run store、`/loop`、`/cron-list`、`/cron-delete` |
| P0 | PR review follow-up 闭环 | feedback 模型、API 接入、poller、同 PR 分支追加 commit、幂等 store |
| P1 | CreateAgentTool MVP | spec、validator、factory、persistence、工具入口 |
| P1 | REPL/TUI/headless 一致化 | REPL Ctrl+B、Cron frontend 接线、runtime context 复用 |
| P1 | Auto 权限模式 | classifier、cache、工具执行前集成 |
| P2 | 稳定性补强 | session/cache 容量限制、MCP 增强、工具统计 |

### 6.2 中期：业务 Agent 与远程值守（2026 Q3-Q4）

| 优先级 | 交付目标 | 包含特性 |
|--------|----------|----------|
| P0 | POS to Agent MVP | SDK parser、skill grouper、agent builder、`/convert-pos-to-agent` |
| P1 | 业务 Agent 长期运行 | Agent 持久化、主 Agent 指定、daemon + attach |
| P1 | Autonomy Status | cron、orchestrator、team、runs 统一状态 |
| P1 | Away Summary | 离开摘要、`/recap`、长任务回看 |
| P2 | A2A 协议雏形 | message schema、capability manifest、bridge adapter |
| P2 | Remote Trigger MVP | 远程启动、审计、鉴权 |

### 6.3 长期：自升级闭环（2026 Q4 以后）

| 优先级 | 交付目标 | 包含特性 |
|--------|----------|----------|
| P0 | Community Feature Radar | 源注册表、release/PR watcher、feature extraction、digest |
| P0 | Self-Planning Gate | gap analyzer、proposal generator、roadmap updater、user review gate |
| P1 | Self-Development Loop | self issue、self orchestrator、isolated workspace、test matrix、self PR |
| P1 | Self-Review Loop | 多 Agent review、PR feedback follow-up、自修复 commit |
| P2 | Self-Update | candidate registry、package build、staged rollout、update/rollback command |
| P2 | Self-Learning | outcome store、failure miner、strategy memory、retrospective |

---

## 7. 成功标准

### 7.1 IR 成功标准

| 标准 | 验收方式 |
|------|----------|
| REPL/TUI/headless 使用同一套 RuntimeContext，不重复构造工具和上下文 | CronCreate 在三种入口都命中扩展实现 |
| Cron 任务可以创建、持久化、触发、执行、查询结果 | 端到端 smoke：创建 durable recurring task，重启后继续触发并记录 completed run |
| 多 Agent 状态可观察、可注入、可审计 | Manager 能 inspect Worker、发送高优先级 directive，并在日志中可追溯 |
| 长期运行不出现明显内存无限增长 | session/cache LRU 测试和 daemon soak 测试通过 |
| 权限与模型选择可配置、可审计 | settings、CLI、runtime 状态一致 |

### 7.2 SR 成功标准

| 标准 | 验收方式 |
|------|----------|
| Issue → implementation → verification → PR → report 全链路自动完成 | 在 GitHub/Gitee/GitCode/LocalTracker 至少各跑一条真实 issue |
| PR review / CI 反馈能自动触发 follow-up commit | 人工发布 inline comment 或制造 CI 失败，Agent 自动追加修复 commit |
| 用户可通过 label/comment/CLI 表达 retry/follow-up/blocked | 三种入口都能改变 registry 和远程状态且有审计记录 |
| POS 能转换成可运行业务 Agent | 输入一个 OpenAPI 或方法列表，生成 Agent JSON、Skill、工具并完成一次真实调用 |
| 业务 Agent 可长期运行并重连 | daemon 启动后新窗口 attach，状态与 transcript 连续 |

### 7.3 AR 成功标准

| 标准 | 验收方式 |
|------|----------|
| ClawCodex 能定期输出社区 Agent 新特性摘要 | Cron 触发 weekly digest，生成结构化 Markdown/JSON 报告 |
| ClawCodex 能自动提出符合自身架构边界的新特性设计 | 生成 FEATURE_PLAN 风格 proposal，并通过 downstream boundary checker |
| ClawCodex 能为自身创建 issue、开发、验证、提交 PR | Self-Orchestrator 从 LocalTracker issue 生成 PR，含测试报告 |
| ClawCodex 能自动处理自升级 PR 的 review/CI feedback | PR comment 或 CI failure 触发 follow-up 修复 commit |
| ClawCodex 能形成候选更新、发布说明和回滚点 | 生成 candidate registry、release notes、package/image，并支持 rollback dry-run |
| ClawCodex 能沉淀失败经验并改进策略 | 月度 retrospective 显示重复失败率下降，策略变更可审计 |

---

## 8. 风险与边界

| 风险 | 影响层级 | 说明 | 缓解策略 |
|------|----------|------|----------|
| Cron 端到端接线不完整 | IR/SR/AR | 调度只停留在模块测试，无法支撑自动值守 | 以 REPL/TUI/headless smoke 作为完成口径 |
| 自升级误改核心上游代码 | AR | 破坏 upstream sync 或引入难维护补丁 | 强制 Architecture Fit Checker；默认写入 `clawcodex_ext/*`，`src/*` 仅 thin seam |
| PR feedback 自触发循环 | SR/AR | bot 评论触发自身反复修复 | 过滤 bot、幂等 store、max follow-up attempts |
| 自动权限误判 | IR/SR | Auto mode 可能执行不应执行的命令 | classifier 三态输出，危险动作 fallback ask，审计所有 auto allow |
| 远程启动安全边界 | SR/AR | RemoteTrigger 可能被滥用 | 默认关闭、强鉴权、最小权限、审计日志、速率限制 |
| 社区特性信息噪声 | AR | 过多无价值候选导致路线图漂移 | 趋势评分 + 用户 Review Gate，未审批不进入开发队列 |
| 自开发质量不稳定 | AR | Agent 生成 PR 质量不足 | Self-Test Matrix + 多 Agent review + verification gate + rollback |
| 多 Agent 并发污染 workspace | SR/AR | 并发写同一工作区导致冲突 | Shared/Sequential 策略、worktree isolation、lock 与审计 |

---

## 9. 下一步行动

1. **优先收敛 Cron 端到端行为**：把 `clawcodex_ext/cron_system/*` 接入真实 REPL/TUI/headless runtime，完成 scheduled fire → query pipeline → run store 的 smoke。
2. **启动 PR Review Feedback 闭环**：先实现统一 `PullRequestFeedback` 模型和 GitHub/Gitee/GitCode feedback API，再接入 Orchestrator poller。
3. **并行推进 CreateAgentTool MVP**：先交付安全 spec/validator/factory/persistence，为 POS to Agent 和长期社区能力吸收打基础。
4. **补齐自动值守观测入口**：把 cron runs、orchestrator issue、team members、verification report 汇总到统一 status 输出。
5. **为 AR 做最小闭环试点**：先以“每周生成 Agent 社区新特性 digest + 手动审批 proposal”为最小可用版本，不直接自动改代码。

---

*本路线图由当前已实现归档能力、活动规划能力和长期自主进化目标整合而成。后续新增特性应继续按 IR/SR/AR 层级归档，并为每个最小特性保留用户感知、状态、工时和交付件字段。*
