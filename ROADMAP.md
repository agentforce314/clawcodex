# ClawCodex ROADMAP

> 文档路径: `ROADMAP.md`
> 信息来源: `docs/ARCHIVED_FEATURES.md`、`docs/FEATURE_PLAN.md`
> 版本: v4.0
> 更新日期: 2026-06-08

---

## 0. 路线图定义

### 0.1 三大特性类别

| 类别 | 名称 | 定位 | 核心问题                          |
|------|------|------|-------------------------------|
| 底层特性 | Infrastructure | 基础设施与可运行底座 | Agent 能否稳定运行、被编排、被恢复、被观察      |
| 场景特性 | Scenario | 场景化产品能力 | 用户能否把真实工作流程交给 ClawCodex 处理    |
| 未来规划特性 | Future | 自主进化与自升级闭环 | Agent 能否发现新能力、规划新特性、开发并验证自身更新 |

### 0.2 状态定义

| 状态 | 含义 |
|------|------|
| ✅ 已完成 | 已实现并在归档文档中记录 |
| 🟡 进行中 | 核心模块或设计已存在,但端到端链路尚未收敛 |
| 📋 规划中 | 已有设计或明确需求,待实施 |
| 🔭 长期规划 | 战略方向明确,仍需拆解设计 |

---

## 1. 总体产品主线

ClawCodex 的目标不是只做一个交互式编码 CLI,而是逐步形成"本地开发 Agent → 多 Agent 编排 → 远程自动值守 → 社区能力吸收 → 自我升级"的闭环系统。

```text
底层特性: Agent Loop / Tool / Skill / Provider / Memory / Session / Cron / Permission
        ↓
场景特性: Orchestrator / Issue → PR / PR Review Follow-up / SOP / Remote Attach
        ↓
未来规划特性: 开源社区观察 → 新特性雷达 → 自主规划 → 自主开发 → 验证发布 → 经验沉淀
```

长期闭环目标: ClawCodex 能定期收集当前最新 Agent 开源社区的新特性,结合自身架构和用户使用数据生成新特性规划,再通过 Orchestrator、Cron、远程启动、Agent2Agent 协作、SOP 转换和验证报告系统开发 ClawCodex 自身,最终形成 Agent 自己升级/更新自己的能力循环。

---

## 2. 底层特性 (Infrastructure Features)

### 2.1 IR-1 Agent 可运行底座（→ FEATURE_PLAN §2.1~§2.14 各节）

**抽象需求**: Agent 应当作为可独立运行的最小软件单元存在,具备完整的会话生命周期、工具发现与执行、模型灵活切换、终端与远程交互、后台与恢复能力。

#### SR-1.1 会话与上下文管理（→ FEATURE_PLAN §2.3 结构化输出（F-4）、§2.5 F-13 记忆隔离、§2.6 /goal（F-9）、§2.10 sessionStorage（F-11）、§2.11 F-12 cacheWarning）

让 Agent 在任何时刻都能进入、继续、恢复、隔离、切换会话,且不丢失关键上下文。

> **基础已完备**：多轮执行循环、会话 ID/SessionStorage、Transcript、Resume 断点恢复、Fork 隔离、Foreground Promotion、Prompt 构建、Agent 类型系统、历史会话索引持久化、多 session 并发切换——均已 ✅ 完成，不单独列 AR。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-13 | 记忆作用域隔离 | 四种作用域(user/project/agent/team)过滤加载 | Agent 只带入相关长期背景，减少无关上下文 | ✅ 已完成 | 已完成 | py 代码、memory 文件 |
| AR-F-11 | sessionStorage 容量限制 | LRU 限制 existingSessionFiles 数量 | 长期运行 daemon 不易 OOM | 📋 规划中→F-11 | 2 天 | py LRU、测试 |
| AR-F-12 | cacheWarning 容量限制 | source entries LRU 限制 | 长期运行不易内存泄漏 | 📋 规划中→F-12 | 2 天 | py LRU、测试 |
| AR-F-4  | 结构化输出增强 (Outlines) | Token 预算、工具决策结构化、压缩策略 | Agent 决策更稳，JSON 错误更少 | 📋 规划中→F-4 | 3 周 | py adapter、schema、测试 |
| AR-F-9  | /goal 命令（目标管理） | 目标管理、进度跟踪 | 用户可设置和管理 Agent 目标 | 📋 规划中→F-9 | — | CLI 命令、UI |

#### SR-1.2 工具与技能执行（→ FEATURE_PLAN §2.4 MCP 扩展（F-3）、§2.7 F-10 ExecuteExtraTool、§4.3 F-52 SDK→Tool 注册）

让 Agent 拥有发现、加载、调用、内置与外部工具的能力,并支持技能扩展和按需上下文控制。

> **基础已完备**：内置文件/搜索工具(Read/Write/Edit/Glob/Grep)、Bash 执行、任务管理(TaskCreate/TaskUpdate)、MCP Stdio/HTTP/SSE/WebSocket 客户端、MCP OAuth、ToolSearch TF-IDF 搜索——均已 ✅ 完成，不单独列 AR。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-17 | 工具按需加载 (Bundle) | Bare / Default / ClawCodex / All 模式 | 用户感知为上下文更轻,工具列表更聚焦 | ✅ 已完成 → F-17 | 已完成 | py registry、bundle 配置 |
| AR-F-10 | ExecuteExtraTool 延迟执行 | 大量工具的按需发现与调用 | 用户可在工具很多时按需调用额外工具 | 🔄 规划中 → F-10 | — | py 工具、动态注册逻辑 |
| AR-F-23 | Skills System Extension | 下游技能扩展层、bundle、路径、hook、cache | 用户可安装和调用 ClawCodex 专属 skill,且不破坏上游同步 | ✅ 已完成 → F-23 | 已完成 | py 代码、skill bundle、配置 |
| AR-F-22 | Cron Fallback 工具 | 旧版 Cron 工具兼容入口 | 老用户能继续使用熟悉的 cron 工具调用方式 | ✅ 已完成 → F-22（Cron Fallback 部分） | 已完成 | py 工具代码、fallback 路由 |
| AR-F-3  | MCP 扩展功能(缓存/批处理/进度) | MCP 资源缓存、Batch 调用、Progress 通知 | 用户感知为外部资源加载更快、MCP 调用更高效、长任务有进度反馈 | ✅ 基础完成 → F-3 | 已完成 | py 缓存模块、批处理代码、通知代码、测试 |
| AR-F-52 | SDK→Tool 注册 | Python SDK 方法注册为 Tool | 用户可让 Agent 调用 Python SDK 接口扩展能力 | 📋 设计完成 → F-52 | — | py SDK 工具、schema、测试 |

#### SR-1.3 模型与 Provider 接入（→ FEATURE_PLAN §3.1 CLI 模型供应商与模型切换设计（F-43））

让 Agent 能在不同模型供应商之间灵活切换,统一接口、统一 Token 统计与配置。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-43 | CLI 模型供应商与模型切换设计 | model list/set/current、provider list/set/test、配置存储(含加密)、Token 计数追踪 | 用户可通过命令查看、设置和切换模型与 provider，看到准确的上下文和成本提示 | ✅ 已完成 → F-43 | 已完成 | py CLI 代码、JSON 配置、Token 计数器 |
| AR-LT-7 | LiteLLM 适配器 | 统一 100+ 模型接口的适配层、替代重复 Provider | 用户可通过统一配置切换更多模型，新增模型更快 | ✅ 已完成 → R-7 | 已完成 | py 适配器、配置开关、兼容测试 |

#### SR-1.4 权限与前端交互（→ FEATURE_PLAN §2.13 Auto 模式（F-16）、§3.2 F-46 permission_mode、§3.3 F-47 Permission Schema）

让用户能在不同自动化程度下与 Agent 交互,并保证权限边界清晰可审计。

> **基础已完备**：REPL Core（prompt_toolkit + Rich）、TUI 渲染、REPL/TUI 双向切换——均已 ✅ 完成，不单独列 AR。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-31 | TUI 状态区与权限选择器 | 权限弹窗、状态条、消息流 | 用户在 TUI 中可清晰看到 Agent 状态并按需授权 | ✅ 已完成 → F-31 | 已完成 | py TUI 代码、组件 |
| AR-F-15 | Shift+Tab 权限循环 | default、acceptEdits、plan、bypass/dontAsk 模式 | 用户可快速调整自动化程度 | ✅ 已完成 → F-15 | 已完成 | py keybinding、UI 状态 |
| AR-F-47 | Permission Settings Schema 重构 | 权限配置 schema 正交化、allow/deny/ask 分类 | 用户感知为权限配置更清晰、更可审计 | ✅ 已完成 → F-47 | 已完成 | py schema、配置迁移 |
| AR-F-34 | Runtime Protocol 与 Frontend Registry | 前后端解耦、runtime 消息协议 | 用户感知为 REPL/TUI/headless 行为更一致 | ✅ 已完成 → F-34 | 已完成 | py runtime/frontend 代码 |
| AR-F-23 | 扩展钩子 (extension hooks) | 前端事件订阅、自定义渲染 | 用户可自定义前端组件和提示 | ✅ 已完成 → F-23 | 已完成 | py hook 代码、示例 |
| AR-F-21 | REPL Ctrl+B 后台运行 | REPL 中把当前任务后台化 | 用户可像 TUI 一样在 REPL 中把长任务放到后台 | 📋 规划中 → F-21 | 1 周 | py REPL 代码、快捷键、测试 |
| AR-F-16 | Auto 模式 (LLM Classifier) | LLM 分类器自动判断工具调用 + Cache + 危险动作 fallback | 用户在长任务中减少重复确认,同时保留安全边界 | 📋 规划中 → F-16 | 6 周 | py classifier、cache、权限集成、fallback、测试 |

#### SR-1.5 后台、恢复与远程桥接（→ FEATURE_PLAN §4.2.1 F-55 SOP 分组策略增强（已完成）、§6 会话恢复增强）

让用户可以在后台安全运行 Agent、跨进程恢复、并通过远程方式接入。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-21 | 进程级后台与恢复 | BackgroundState、TailFollower JSONL 增量 tail、SessionWatcher 目录监控、平台 fallback、TUI/repl Ctrl+B | 用户可安全后台化/恢复任务,跨平台不丢事件 | ✅ 已完成 → F-21 | 已完成 | py 状态代码、tail/watcher、平台抽象 |
| AR-F-23 | Bridge 多 Session 桥接 | Graceful Shutdown、多 Session Daemon、轮询/WS 协议、Remote Bridge、跨进程 HTTP client、REPL attach | 用户可管理多个长期运行 session,远程连接和控制 Agent | ✅ 已完成 → F-23 | 已完成 | py daemon/协议/bridge 代码、HTTP client |
| AR-F-7  | Remote WebUI 远程控制 | WebUI Docker 镜像、远程控制 API、鉴权与安全 | 用户可通过浏览器远程查看/启动/接管 Agent | 🔭 长期规划 → F-7 | 9-11 周 | Docker 镜像、py API、鉴权配置 |
| AR-F-55 | SOP 分组策略增强 | SOP 分组策略增强（已完成） | 用户可使用增强的 SOP 分组策略 | ✅ 已完成 → F-55 | 已完成 | py 分组策略、测试 |

### 2.2 IR-2 可观测、可调度与可维护底座（→ FEATURE_PLAN §2.1 进度汇报（F-20）、§2.8 工具统计（F-75）、§五 F-22 定时任务、§1.3.3 F-45、§1.3.1 F-51、§1.3.2 F-54、§4.1 F-48）

**抽象需求**: Agent 系统应支持任务进度上报、定时任务调度、长期运行稳定,并具备工具使用统计与策略优化的能力,确保无人值守场景下不失控、不沉默失败。

#### SR-2.1 任务进度与可观测（→ FEATURE_PLAN §2.1 Agent 进度汇报（F-20）、§1.3.2 F-54 运行期可观测性）

让用户和 Manager Agent 能清晰看到 Worker 状态、长任务阶段产出,并支持审计与回溯。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-20 | Agent 阶段性进度汇报 | ProgressReportTool 阶段性写入、任务 metadata 维护 | 用户可看到长任务当前阶段和阶段产出，可查询任务依赖与完成情况 | ✅ 已完成 → F-20 | 已完成 | py 工具、任务 metadata、JSON metadata |
| AR-F-29 | Manager 监督工具 | TaskInspect Worker 状态查询、TaskDirectives 优先级消息注入 | 用户可让 Manager 监督子任务进展并动态纠正或重排 Worker | ✅ 已完成 → F-29 | 已完成 | py 工具、状态读取、pending message 队列 |
| AR-F-40 | ProgressReporter Sink 协议重构 | per-session 独立 sink、CompositeProgressSink 多 issue 汇聚过滤 | 用户在多会话、多 issue 并发时看到正确的独立进度 | 📋 设计完成 → F-40 | 2 周 | py sink 协议、event log、测试 |
| AR-F-38 | Progress event log | ndjson 事件流、阶段完成/错误/warning | 用户可通过 CLI tail 看到阶段进度 | ✅ 已完成 → F-38 | 已完成 | ndjson event log、CLI 渲染 |
| AR-F-45 | 编排场景 ndjson 审计 | 编排场景下工具调用审计流 | 用户可审查无人值守任务实际操作 | ✅ 已完成 → F-45 | 已完成 | ndjson audit、py sink |
| AR-F-54 | 运行期可观测性 | stuck-run debug 诊断 | 用户可诊断 Agent 卡住原因 | 📋 规划中 → F-54 | — | py 诊断工具、测试 |

#### SR-2.2 定时任务与调度（→ FEATURE_PLAN §五 F-22 定时任务系统）

让用户可描述、持久化、触发、查询定时任务,并支持多 Agent 场景下任务路由到正确的 Agent。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-22 | 定时任务系统 | cron 表达式解析、human schedule 文本解析、CronTask 任务模型、Durable/Session Task Store、Scheduler Lock 防重复、确定性 jitter、REPL/TUI/headless Runtime 接线、CronDispatchBridge、Cron Run Store 生命周期、`/loop`/`/cron-list`/`/cron-delete` Skill 集成、Missed One-shot 安全确认、Teammate Ownership 路由 | 用户可用 cron/natural language 描述定时任务，重启后任务保留，多窗口不重复执行，任务像普通输入一样被执行，可查询每次运行结果，Team 场景下路由正确 | 🟡 进行中 → F-22 | 综合工时约 12 周 | py 解析器、dataclass、store、lock、jitter、runtime、dispatch bridge、run store、skill、通知、路由

#### SR-2.3 稳定性与开放替代（→ FEATURE_PLAN §1.3.3 F-45 Tool-call 审计、§1.3.1 F-51 空转检测、§4.1 F-48: src/ 核心路径二开修改解耦方案）

让系统能长期稳定运行、避免 OOM 和内存泄漏,并通过架构解耦与成熟开源 SDK 替代降低维护成本。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-11 | sessionStorage LRU 限制 | LRU 限制 existingSessionFiles 数量 | 用户长期运行 daemon 不易 OOM | 📋 规划中 → F-11 | 2 天 | py LRU、测试 |
| AR-F-12 | cacheWarning 容量限制 | cacheWarning source entries LRU | 用户长期运行不易内存泄漏 | 📋 规划中 → F-12 | 2 天 | py LRU、测试 |
| AR-F-4  | Outlines 集成与稳定性 | Token 预算、工具决策结构化、压缩策略 | 用户感知为 Agent 决策更稳定、JSON 解析错误更少 | 📋 规划中 → F-4 | 3 周 | py adapter、Pydantic schema、测试 |
| AR-F-45 | Tool-call 审计旁路 | 编排场景工具调用审计流 | 用户可审查无人值守任务实际操作 | ✅ 已完成 → F-45 | 已完成 | ndjson audit、py sink |
| AR-F-51 | AgentRunner 空转检测 | 空转循环检测与自动终止 | 用户不会因 Agent 空转浪费 token 和时间 | ✅ 已完成 → F-51 | 已完成 | py 检测代码、配置、测试 |
| AR-F-48 | src/ 核心路径二开修改解耦方案 | 架构解耦、二开边界隔离 | 用户可安全且更解耦二开扩展 | ✅ 已完成 → F-48 | 已完成 | py 解耦方案、文档 |
| AR-R-1~5 | 依赖库替代 (pydantic-settings / frontmatter / tree-sitter-bash / GitPython / Pluggy) | 配置加载、frontmatter 解析、Bash AST、git 操作、插件系统标准化 | 系统更稳定、配置更规范、命令识别更准、git 更稳、扩展更规范 | ✅ 已完成 → R-1~R-5 | 已完成 | py 依赖替换、兼容测试 |
| AR-F-22 | Daemon Soak 测试 | 长期运行不 OOM、不丢事件 | 用户在长值守中不丢会话 | 📋 规划中 → F-22 | 1 周 | py soak 测试、监控 |

#### SR-2.4 工具使用统计与策略（→ FEATURE_PLAN §2.8 工具/Skill 调用统计（F-75））

让用户能根据真实使用数据优化工具集,减少上下文噪音,提升常用能力优先级。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-75 | 工具/Skill 调用统计（跨会话） | JSONL 日志、频率统计、低频识别、报表生成 | 用户可统计工具使用情况、识别高频/低频工具、周期性获得使用报告 | 📋 规划中 → F-75 | 5 周 | py 日志 writer、统计、报表 CLI、Markdown 输出 |

---

## 3. 场景特性 (Scenario Features)

### 3.1 IR-3 研发自动化场景（→ FEATURE_PLAN §1.1~§1.4（F-36~F-44）、§2.2（F-2）、§2.12（F-78）、§2.14（F-80））

把用户真实研发流程(Issue 处理、PR 评审、多 Agent 协作)自动化,并保证自动化失败时可被用户接管、纠偏、追溯。

#### SR-3.1 Issue → PR 编排（→ FEATURE_PLAN §1.1.1 F-36 LocalTracker、§1.2.1 F-42 Workspace 策略、§1.4.2 F-44 人工检视闸门、§7.6 Feature Gate（F-68））

让用户从不同 issue 源(Linear/GitHub/Gitee/GitCode/本地)拉取 issue,自动创建隔离工作区、运行 Agent、生成 PR,并支持重试、跳过、限频、运维。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-1  | Orchestrator 主循环与适配器 | 轮询 issue、领取、workspace 创建、Agent 运行；4 个 Tracker Adapter(Linear/GitHub/Gitee/GitCode)；IssueRegistry；Retry/Backoff；Issue State 前置检查；已有 PR 跳过 | 用户可启动 daemon 自动处理来自多个平台的 issue，临时失败自动恢复，重启不重复处理 | ✅ 已完成 → F-1 | 已完成 | py orchestrator、CLI、tracker adapter 代码 |
| AR-F-36 | LocalTracker 本地 Issue 文档源 | md/json 本地 issue 文件源、frontmatter 状态写回 | 用户可不用远程平台，在本地文件夹中排队任务，状态自动写回文件 | ✅ 已完成 → F-36 | 已完成 | py LocalTracker、md/json issue 文件、写回逻辑 |
| AR-F-42 | WorkspaceManager 隔离与并发 | 每 issue 隔离工作区、清理策略、并发 issue 数/队列控制 | 用户可同时处理多个 issue，互不污染；可配置 daemon 并发度 | ✅ 已完成 → F-42 | 已完成 | py workspace、目录产物、配置 |
| AR-F-44 | 人工检视闸门 | pending_review、approve/reject CLI、diff 输出 | 用户可先审查 diff，再决定是否接受本地 Agent 修改 | ✅ 已完成 → F-44 | 已完成 | py CLI、状态字段、diff 输出 |
| AR-F-68 | Feature Gate 运行时特性开关系统 | 运行时特性开关系统、特性门控 | 用户可通过特性开关控制功能启用/禁用 | ⏳ 待开始 → F-68 | — | py feature gate、配置、测试 |

#### SR-3.2 澄清、重跑与人机协同（→ FEATURE_PLAN §1.1.4 F-39 Issue 重跑、§1.4.2 F-49 会话统一存储、§2.12 Issue 语义澄清流程（F-78））

让 Agent 在不确定时主动询问用户,支持多渠道回答冲突裁决;支持从 issue label/comment/CLI 三种入口重跑任务,并具备限频、角色校验与审计;支持运行中提示注入与人工接管。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-1  | 多渠道 Clarification 系统 | Local Dashboard 提问 UI、CLI Queue 通道、Tracker 评论通道、跨通道 Clarification 队列、Operator 优先裁决、超时升级、去重与过期拒绝 | Agent 不确定时主动询问用户，用户可从多渠道回答，系统统一管理、自动裁决、超时升级 | ✅ 已完成 → F-1 | 已完成 | py UI、CLI、tracker、queue、状态机、配置 |
| AR-F-39 | Issue 重跑系统（label/comment/CLI） | `agent:retry/follow-up/blocked` label、`/agent retry/follow-up/unblock` comment 命令、CLI `issue retry --mode`、comment parser + bot 确认、max retries 限频、maintainer/author 角色校验、audit.jsonl 审计、Operator Hint 注入 | 用户可通过 label/comment/CLI 三种入口重跑任务，具备限频、角色校验、审计和生产级安全措施 | ✅ 已完成 → F-39 | 已完成,真实环境待继续验证 | py tracker、registry、comment parser、bot、CLI、audit、限频、权限 |
| AR-F-49 | Takeover 接管 | 终止 Agent 并进入 REPL 接管 workspace | 用户可在自动化失控或复杂场景下手动接手 | ✅ 已完成 → F-49 | 已完成 | py CLI、REPL attach |
| AR-F-78 | Issue 语义澄清流程 | Agent 主动识别不明确 issue、生成澄清问题列表、等待用户回答后继续执行 | 用户可为不明确的 issue 提供澄清，Agent 根据回答调整执行 | 📋 规划中 → F-78 | — | py 澄清流程、问题生成、等待机制 |

#### SR-3.3 验证、报告与 PR 质量（→ FEATURE_PLAN §1.1.2 F-37 PR 检视修复、§1.1.3 F-38 验证与报告、§1.2.2 F-40 ProgressReporter）

让 Agent 在 commit/push 前必须验证,生成结构化报告,把信息回写到 PR body / 评论 / event log;并支持对 PR review 反馈的自动 follow-up。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-38 | Verification Gate 与报告系统 | commit/push 前 test/build/lint/hooks 验证、`.reports/{id}.md/.json` 双写、PR Body 模板(Issue/Branch/Commit/Verification/Report)、PR 汇总评论合并、PhaseComplete ndjson event log、`issue tail` CLI 渲染 | 用户看到未验证通过的代码不会被自动推送、可阅读/机读本次修改摘要、Reviewer 打开 PR 即可看到 Agent 工作产物、不会被多条重复 bot 评论干扰、可通过 CLI 查看阶段进度 | ✅ 已完成 → F-38 | 已完成 | py git_sync、workflow 配置、writer、模板、tracker update、comment 逻辑、ndjson event log、CLI 渲染 |
| AR-F-37 | PR Review 自动修复闭环 | PullRequestFeedback 模型(inline/summary/CI)、GitHub/Gitee/GitCode review comments API、CI checks/pipelines 解析、Review Follow-up Poller、Review-fix Prompt Builder(最小修改)、同 PR 分支 follow-up sync(追加 commit+push)、Feedback 幂等 Store、评论回复、处理摘要(自动处理/需人工确认) | 用户/Reviewer 看到 Agent 自动处理 review 评论和 CI 失败、在原 PR 追加修复 commit、对同一条评论不反复修复、可追踪处理边界 | 📋 规划中 → F-37 | 12 周 | py dataclass、repo client、解析器、poller、prompt builder、git_sync mode、JSON store、tracker reply、摘要 |

#### SR-3.4 多 Agent 编排与 A2A 协作（→ FEATURE_PLAN §1.3.4 F-41 Coordinator、§2.2 Team 管理（F-2）、§2.14 Agent 间交互（F-80））

让用户可启动团队、管理 Manager / Worker、传递权限、共享或隔离工作区;并最终把团队协作抽象为 Agent2Agent 协议,让本地/远程/第三方 Agent 互联。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-41 | Coordinator 团队编排工具集 | TeamCreate/Delete、members 数组、命名注册、状态管理(idle/busy/error)；Manager/Worker 角色识别、TaskInspect 工具、TaskDirectives 注入、优先级消息队列、drain 排序消费、permission mode 传递、allow rules 传递、Shared/Sequential Workspace 策略、Tool-call 审计旁路 | 用户可轻松创建和管理多 Agent 团队，让 Manager 监督 Worker 并传递权限、共享或隔离工作区，配合 Audit 实现无人值守透明度 | ✅ 已完成 → F-41 | 已完成 | py Team 工具、JSON team 文件、registry、状态机、工具 bundle、workspace 策略、ndjson audit |
| AR-F-2  | A2A 协议化 Agent 互联 | 协议化消息、标准化 message schema、bridge adapter 连接本地/远程 Agent、能力发现(工具/技能/权限/状态 manifest) | 用户可连接本地、远程和第三方 Agent 进行协作，Manager 可自动发现和选择合适的 Worker Agent | 🔭 长期规划 → F-2 | 4-6 周 | py protocol、JSON schema、adapter、discovery、capability manifest |

### 3.2 IR-4 业务 Agent 与远程值守（→ FEATURE_PLAN §2.9（F-18）、§4.2 F-50 SOP 固化、§4.3 F-52 SDK→Tool）

把标准作业流程(SOP)和远程值守转成可长期运行、可被 ClawCodex 调度的业务 Agent,支持主 Agent 切换、daemon 模式与跨设备 attach。

#### SR-4.1 SOP（→ FEATURE_PLAN §2.9 CreateAgentTool（F-18）、§4.2 F-50 SOP 转换器固化、§4.3 F-52 SDK→Tool 注册）

把业务系统 SDK / OpenAPI 转成可被 Agent 调用的原子接口和 Skill,并组装为业务 Agent。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-18 | CreateAgentTool 动态工具创建 | AgentToolSpec dataclass、bash/http/python 3 种 call_type、命令/HTTP/函数/防注入 4 种 validator、Factory 构造注册工具、call handlers、工具持久化与自动加载、CreateAgentTool 对外工具 | 用户可让 Agent 根据 CLI/API 规范动态创建和调用新工具，重启后工具仍可用 | 📋 规划中 → F-18 | 6.5 周 | py dataclass、call_type handler、validator、factory、loader、tool 入口 |
| AR-F-50 | SOP 转换器固化 | OpenAPI JSON/URL 解析、方法列表解析、Skill Grouper 原子接口分组、Agent Builder 生成 Agent 定义、`/convert-pos-to-agent` Skill、Agent 持久化、`--agent` CLI 参数指定、default_agent 配置、daemon 模式、attach 重连 | 用户可把 CI/CD/数据分析等专业系统一键转为可运行的业务 Agent，支持长期值守和重连 | 📋 规划中 → F-50 | 8 周 | py parser、grouper、builder、skill、Agent JSON、CLI、daemon、attach 协议 |
| AR-F-52 | SDK→Tool 注册 | Python SDK 方法注册为 Tool | 用户可让 Agent 调用 Python SDK 接口扩展能力 | 📋 设计完成 → F-52 | — | py SDK 工具、schema、测试 |
| AR-F-53 | Tool→CLI 命令映射 | Tool 自动暴露为 CLI 斜杠命令 | 用户可通过 CLI 斜杠命令调用注册的工具 | 📋 设计完成 → F-53 | — | py CLI 命令映射、schema、测试 |

#### SR-4.2 远程启动与自动值守（→ FEATURE_PLAN 🔭 待补充设计）

让用户从外部系统或远端 cron 启动 Agent;支持值守期间的状态汇总、离开摘要和 Web 监督。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-1  | Orchestrator Server 运维 | `orchestrator server start/status/stop` CLI、Daemon 状态文件 | 用户可让 ClawCodex 持续值守 issue 队列并看到自身健康度 | ✅ 已完成 → F-1 | 已完成 | py daemon CLI、状态文件 |
| AR-F-22 | Cron 驱动巡检与自治状态 | Cron 驱动(issue 巡检/报告生成/社区扫描)、Autonomy Status 汇总(cron runs/orchestrator issue/team members)、Remote Scheduled Agent、远程 cron schedule 管理、Remote Web Dashboard | 用户可定时巡检 issue、生成报告，用一个命令查看自动值守系统健康度，可通过浏览器监督无人值守任务 | 🟡 进行中 → F-22 | 综合约 4 周 | py cron runtime、status CLI、Web UI、API |
| AR-F-7  | RemoteTrigger 远程启动与 WebUI | RemoteTrigger 入口 + 鉴权 + 审计日志、远程 server API、Web Dashboard(issue/cron/team/runs 视图 + 鉴权) | 用户可从外部系统启动工作流，在浏览器中远程监督所有任务 | 🔭 长期规划 → F-7 | 综合约 3-4 周 | py API、鉴权配置、Web UI、Docker 镜像 |
| AR-F-26 | Away Summary 服务 | 终端失焦检测、长时间离开检测、配置 idle 阈值、`/recap` Skill | 用户离开后回来可快速知道 Agent 做了什么 | 📋 规划中 → F-26 | 1.5 周 | py service、焦点检测、skill 代码 |

#### SR-4.3 业务 Agent 长期运行（→ FEATURE_PLAN 🔭 待补充设计）

让业务 Agent 能在后台持续运行、断线重连、并支持多个 Agent 同时被不同用户使用。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-NS-1 | 多业务 Agent 运行基础设施 | 多 Agent 并发、命名空间(独立配置/记忆/工具)、状态查询(运行/暂停/错误)、暂停/恢复、健康检查(心跳/自动重启)、升级(工具/skill/配置热更新)、日志隔离、配额(资源上限) | 用户可同时运行多个专用 Agent，互不干扰，可查看健康度和暂停/恢复每个 Agent | 📋 规划中 | 综合约 7 周 | py daemon、命名空间、状态机、心跳、热更新、配额 |
| AR-NS-2 | 多用户权限与数据持久化 | 多用户隔离 ACL、Agent 模板市场(共享/导入)、数据/记忆持久化 | 用户可多人共享 daemon 权限隔离，一键启动标准 Agent，重启后上下文连续 | 📋 规划中 | 综合约 6.5 周 | py ACL、模板仓库、持久化 storage |
| AR-NS-3 | 业务 Agent 远程 attach | 跨设备 attach 协议 | 用户可在任何终端重连到自己的 Agent | 📋 规划中 | 2 周 | py attach、attach 协议 |

---

## 4. 未来规划特性 (Future Features)

### 4.1 IR-5 自升级闭环（→ FEATURE_PLAN §七 CCB 对标缺口补缺）

ClawCodex 应能持续观察 Agent 开源社区、识别可迁移能力、自主规划、自主开发、自主验证并安全地更新自己,形成长期自我进化闭环。

#### SR-5.1 开源社区新特性雷达（→ FEATURE_PLAN 🔭 待补充设计）

持续抓取开源 Agent 项目(Claude Code、Aider、SWE-agent、OpenHands、AutoGen、CrewAI、LangGraph 等)的 release/commit/PR/issue,抽取候选特性并按分类与评分去重整理。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.1.1 | 源注册表与抓取器 | 源配置(JSON/YAML)、loader、Release/Commit/PR/Issue fetcher、抓取缓存 | 用户可配置 ClawCodex 关注哪些开源 Agent 项目，系统自动抓取社区动态 | 🔭 长期规划 | 2 周 | JSON/YAML 配置、py loader、fetcher、缓存 |
| AR-5.1.2 | 候选特性抽取与分类 | Feature Extraction Pipeline、JSON feature records、跨项目去重、Taxonomy 分类 | 用户看到结构化候选特性，可从社区动态中自动抽取候选能力 | 🔭 长期规划 | 2 周 | py extractor、JSON schema、去重、分类器 |
| AR-5.1.3 | 评分与报告系统 | 趋势评分模型(热度/成熟度/适配成本/战略价值)、周报/月报 Community Digest、权重配置 | 用户可看到哪些新能力值得优先吸收，定期收到社区动态摘要 | 🔭 长期规划 | 2.5 周 | py 评分、权重配置、Markdown 报告 |
| AR-5.1.4 | cron 集成 | 周期触发抓取与报告生成 | 用户的社区雷达可定时运行 | 🔭 长期规划 → F-22 | 0.3 周 | cron 配置、集成 |

#### SR-5.2 自我规划与路线图生成（→ FEATURE_PLAN 🔭 待补充设计）

把候选特性与 ClawCodex 现有能力对比,生成符合架构边界的设计稿、依赖图、路线图草案,并保留用户审批权。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.2.1 | Capability Gap Analyzer | 对比 ClawCodex 现有能力与候选、能力索引、已有优势识别、Markdown gap report | 用户可看到"我们缺什么、已有优势是什么" | 🔭 长期规划 | 1.5 周 | py analyzer、索引、识别、报告 |
| AR-5.2.2 | Architecture Fit Checker | downstream 解耦边界检查、`clawcodex_ext/*` 范围检查、边界规则配置 | 用户不用担心新特性破坏上游同步能力 | 🔭 长期规划 | 1.5 周 | py checker、规则、配置 |
| AR-5.2.3 | Feature Proposal Generator | 自动生成 FEATURE_PLAN 风格设计稿、模板填充、proposal JSON metadata | 用户可直接审阅候选特性的设计方案 | 🔭 长期规划 | 1.5 周 | py 生成器、模板、JSON schema |
| AR-5.2.4 | Roadmap Auto-Updater 与 Dependency Planner | 根据评分和依赖更新 ROADMAP/FEATURE_PLAN 草案、Markdown diff、IR/SR/AR 依赖图、实施顺序拓扑排序、Mermaid/Markdown 可视化 | 用户可让 Agent 自动维护路线图草案，看到依赖关系和最优开发路径 | 🔭 长期规划 | 2 周 | py doc updater、planner、图生成、拓扑排序 |
| AR-5.2.5 | User Review Gate | CLI review 命令(approve/reject/modify)、approval JSON 持久化 | 用户保留路线图决策权，不被 Agent 自动改方向 | 🔭 长期规划 | 0.5 周 | CLI review 命令、JSON store |

#### SR-5.3 自主开发 ClawCodex 自身（→ FEATURE_PLAN 🔭 待补充设计）

把已批准的 proposal 转成可执行任务,让 Orchestrator 处理 ClawCodex 自身的 issue,在隔离 workspace 中开发、测试、提交 PR,并由多 Agent 互审。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.3.1 | Self-Issue Generator | 已批准 proposal 转 issue（LocalTracker/GitHub）、frontmatter 元数据、tracker entry | 用户批准后，Agent 自动生成可执行开发任务 | 🔭 长期规划 | 0.8 周 | Markdown issue、py frontmatter、JSON entry、py API |
| AR-5.3.2 | Self-Orchestrator Runner | Orchestrator 处理 ClawCodex 自身 issue、daemon 任务、workflow YAML 配置 | 用户可让 ClawCodex 自动开发自己的功能分支 | 🔭 长期规划 | 0.7 周 | workflow 配置、daemon 任务、py 集成 |
| AR-5.3.3 | Self-Workspace Isolation | git worktree 隔离、每任务独立 workspace、清理与过期策略 | 用户本地工作区不被自升级任务污染 | 🔭 长期规划 | 0.8 周 | py workspace 策略、git worktree |
| AR-5.3.4 | Self-Test Matrix | 测试规划器(按改动类型选择测试)、多维验证(test/lint/typecheck/docs)、workflow YAML 可配置 | 用户看到每次自升级都有验证矩阵 | 🔭 长期规划 | 1.2 周 | py test planner、workflow YAML、py 矩阵 |
| AR-5.3.5 | Self-PR Generator | 自动提交分支、PR 模板、git_sync 集成 | 用户可像 review 普通贡献一样 review Agent 自升级 PR | 🔭 长期规划 | 0.8 周 | py git_sync、PR 模板、py 集成 |
| AR-5.3.6 | Self-Review Agent Team | code reviewer / test analyzer / silent failure hunter / simplifier 多 Agent 互审、review reports 汇总 | 用户看到自升级 PR 经过多 Agent 检查 | 🔭 长期规划 | 1.5 周 | Agent configs、review reports |
| AR-5.3.7 | Self-Fix Follow-up | 读取 PR review/CI 反馈并自动追加修复 commit（复用 SR-3.3 follow-up） | 用户只需 review，Agent 自动跟进反馈 | 🔭 长期规划 | 1 周集成 | py follow-up 配置、registry 状态 |

#### SR-5.4 自我更新、发布与回滚（→ FEATURE_PLAN 🔭 待补充设计）

把已验证的自升级版本登记为候选、构建为可安装包、分阶段发布、支持安全回滚,并自动生成发布说明。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.4.1 | Update Candidate Registry | 记录候选更新版本、PR、验证状态、风险等级、CLI 输出 | 用户可看到哪些自升级版本可安装，候选可追踪回溯 | 🔭 长期规划 | 1 周 | JSON registry、CLI 输出 |
| AR-5.4.2 | Binary / Package Build | 构建 wheel、二进制包或 Docker 镜像 | 用户可直接安装经过验证的 ClawCodex 包 | 🔭 长期规划 | 2 周 | wheel、binary、Docker 镜像 |
| AR-5.4.3 | Staged Rollout | dev/canary/stable 分阶段启用、feature flags 细粒度功能开关 | 用户可先在隔离环境试用新能力，逐个启用每个新特性 | 🔭 长期规划 | 1.5 周 | release channel 配置、feature flags |
| AR-5.4.4 | Installer / Update CLI | `clawcodex update --candidate ...` CLI 命令、安装脚本、签名校验 | 用户一条命令升级到指定候选版本，安装不被中间人攻击 | 🔭 长期规划 | 1.5 周 | py CLI、安装脚本、py sigstore |
| AR-5.4.5 | Health Check & Safe Rollback | 更新后自动 smoke 检查、配置兼容检查、回滚点创建、版本保留、rollback CLI | 用户升级失败时不会陷入不可用状态，可一键回滚 | 🔭 长期规划 | 2 周 | py health check、snapshot、rollback CLI、metadata |
| AR-5.4.6 | Release Notes Generator | 从 PR/报告/测试结果生成发布说明、Markdown release notes、JSON manifest | 用户可理解这次更新新增了什么、风险是什么 | 🔭 长期规划 | 1 周 | Markdown release notes、JSON manifest |
#### SR-5.5 经验沉淀与策略优化（→ FEATURE_PLAN 🔭 待补充设计）

把自开发结果与失败模式沉淀为可审计的策略,持续优化 prompt、skill、工具集和路线图。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.5.1 | Development Outcome Store | JSONL outcome store、outcome schema(成功/失败/返工原因)、CLI query | 用户可追踪 Agent 自升级效率，outcome 可被审计和查询 | 🔭 长期规划 | 1 周 | JSONL store、JSON schema、py CLI |
| AR-5.5.2 | Failure Pattern Miner | 从失败测试/review 评论/回滚中聚类总结模式、Markdown report | 用户看到 Agent 后续会避开重复错误 | 🔭 长期规划 | 1.5 周 | py miner、聚类、Markdown report |
| AR-5.5.3 | Strategy Memory Writer | 把稳定经验转成 memory/guide/rule 草案、review gate 审批后才生效 | 用户可审批哪些经验进入长期策略，策略变更可被审查 | 🔭 长期规划 | 1.5 周 | Markdown memory proposal、CLI review |
| AR-5.5.4 | Prompt / Skill Tuning Loop | 根据失败模式自动调整自开发 prompt 和 skill、候选 prompt templates / skill configs | 用户感知为 Agent 自开发越来越稳 | 🔭 长期规划 | 2 周 | py 调整、prompt templates、skill configs |
| AR-5.5.5 | Tool Pruning Feedback Loop | 复用 SR-2.4 统计、工具成功率、裁剪策略调整 bundle | 用户上下文更轻，常用能力更突出，低成功率工具被自动裁剪 | 🔭 长期规划 | 1 周集成 | py pruning policy、bundle config |
| AR-5.5.6 | Roadmap Retrospective | 每月自动生成路线图完成度和偏差报告、metrics JSON | 用户可审视 Agent 自规划是否可靠，机读指标可被下游消费 | 🔭 长期规划 | 1 周 | Markdown retrospective、JSON metrics |

---

#### SR-5.6 CCB 对标缺口补缺（→ FEATURE_PLAN §七 CCB 对标缺口补缺）

把 Claude Code Benchmark 对标发现的缺失能力补齐，包括进程间通信、浏览器操控、通知语音、可观测协议、高级 Agent 模式和模板系统。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-F-60 | Pipe IPC 群控 | 进程间通信与 LAN 群控系统 | 用户可跨进程/跨设备控制 Agent | ⏳ 待开始 → F-60 | — | py IPC、群控协议 |
| AR-F-82 | Remote Control Server | 远程控制服务 | 用户可远程启动和控制 Agent | ⏳ 待开始 → F-82 | — | py 远程控制、API |
| AR-F-61 | Computer Use 屏幕操控 | 屏幕截图、点击、输入操控 | 用户可让 Agent 操作桌面应用 | ⏳ 待开始 → F-61 | — | py 屏幕操控 |
| AR-F-62 | Chrome 浏览器自动化 | Chrome DevTools Protocol 控制 | 用户可让 Agent 自动化浏览器操作 | ⏳ 待开始 → F-62 | — | py Chrome 自动化 |
| AR-F-72 | Multi-API 原生适配器 | 统一多模型 API 适配层 | 用户可无缝切换更多模型供应商 | ⏳ 待开始 → F-72 | — | py 适配器、配置 |
| AR-F-74 | Sandbox 沙箱远程执行 | 沙箱/Docker/SSH 远程执行 | 用户可在隔离环境中安全执行代码 | ⏳ 待开始 → F-74 | — | py 沙箱、远程执行 |
| AR-F-63 | Channels 频道通知 | 多频道通知系统（Slack/Discord/邮件等） | 用户可从多渠道接收 Agent 通知 | ⏳ 待开始 → F-63 | — | py 通知系统 |
| AR-F-64 | Voice Mode 语音输入 | 语音输入与输出 | 用户可用语音与 Agent 交互 | ⏳ 待开始 → F-64 | — | py 语音模块 |
| AR-F-65 | Langfuse 可观测 | Langfuse Agent 可观测性集成 | 用户可追踪 Agent 执行链路和性能 | ⏳ 待开始 → F-65 | — | py Langfuse 集成 |
| AR-F-66 | ACP 协议支持 | Agent Communication Protocol | 用户可让 Agent 通过标准协议通信 | ⏳ 待开始 → F-66 | — | py ACP 协议 |
| AR-F-67 | Buddy 伴侣/Proactive 自主 | 主动式 Agent 伴侣模式 | 用户可让 Agent 主动提供建议和帮助 | ⏳ 待开始 → F-67 | — | py Buddy 模式 |
| AR-F-69 | Budget/Poor Mode | 资源节俭模式 | 用户可在资源受限时仍有效使用 Agent | ⏳ 待开始 → F-69 | — | py 节俭模式 |
| AR-F-83 | Ultraplan 高级规划 | 高级规划模式 | 用户可让 Agent 进行深度规划和拆解 | ⏳ 待开始 → F-83 | — | py 规划模式 |
| AR-F-84 | Context Collapse 上下文折叠 | 上下文折叠与压缩 | 用户可让 Agent 处理更大上下文 | ⏳ 待开始 → F-84 | — | py 上下文折叠 |
| AR-F-86 | Kairos/Brief 调度 | Kairos/Brief 调度模式 | 用户可精细控制 Agent 调度节奏 | ⏳ 待开始 → F-86 | — | py 调度模式 |
| AR-F-87 | Workflow Scripts 工作流脚本 | 工作流脚本定义与执行 | 用户可定义和运行复杂工作流 | ⏳ 待开始 → F-87 | — | py 工作流引擎 |
| AR-F-88 | Explore/Plan 内置 Agent | 探索/规划内置 Agent | 用户可让 Agent 自主探索和规划 | ⏳ 待开始 → F-88 | — | py 内置 Agent |
| AR-F-68 | Feature Gate 运行时特性开关 | 运行时特性开关系统 | 用户可通过特性开关控制功能启用/禁用 | ⏳ 待开始 → F-68 | — | py feature gate |
| AR-F-71 | 内置工具补齐 | 缺失工具批量实现 | 用户可使用更多内置工具 | ⏳ 待开始 → F-71 | — | py 工具实现 |
| AR-F-73 | CI/CD 质量门禁 | CI/CD 流水线与 PyPI 发布 | 用户可自动化质量门禁和发布流程 | ⏳ 待开始 → F-73 | — | py CI/CD、发布 |
| AR-F-85 | Templates 模板系统 | 模板定义与实例化 | 用户可基于模板快速创建 Agent 配置 | ⏳ 待开始 → F-85 | — | py 模板系统 |
| AR-F-70 | Plugin 插件系统 | 插件系统基础框架 | 用户可安装和管理插件扩展能力 | ⏳ 待开始 → F-70 | — | py 插件框架 |
| AR-F-81 | Native 原生模块系统 | 原生模块系统（Python 可实现部分） | 用户可使用高性能原生模块 | ⏳ 待开始 → F-81 | — | py 原生模块 |

## 5. 分层依赖图

```text
底层特性:
  IR-1 Agent 可运行底座
    ├── SR-1.1 会话与上下文管理
    ├── SR-1.2 工具与技能执行
    ├── SR-1.3 模型与 Provider 接入
    ├── SR-1.4 权限与前端交互
    └── SR-1.5 后台、恢复与远程桥接
  IR-2 可观测、可调度与可维护底座
    ├── SR-2.1 任务进度与可观测
    ├── SR-2.2 定时任务与调度
    ├── SR-2.3 稳定性与开放替代
    └── SR-2.4 工具使用统计与策略
        ↓
场景特性:
  IR-3 研发自动化场景
    ├── SR-3.1 Issue → PR 编排
    ├── SR-3.2 澄清、重跑与人机协同
    ├── SR-3.3 验证、报告与 PR 质量
    └── SR-3.4 多 Agent 编排与 A2A 协作
  IR-4 业务 Agent 与远程值守
    ├── SR-4.1 SOP
    ├── SR-4.2 远程启动与自动值守
    └── SR-4.3 业务 Agent 长期运行
        ↓
未来规划特性:
  IR-5 自升级闭环
    ├── SR-5.1 开源社区新特性雷达
    ├── SR-5.2 自我规划与路线图生成
    ├── SR-5.3 自主开发 ClawCodex 自身
    ├── SR-5.4 自我更新、发布与回滚
    ├── SR-5.5 经验沉淀与策略优化
    └── SR-5.6 CCB 对标缺口补缺
```

### 5.1 跨层关键依赖

| 上游 SR | 解锁能力 | 说明 |
|---------|----------|------|
| SR-2.2 Cron 端到端调度 | SR-4.2 远程启动、SR-5.1 社区雷达、SR-5.2 周期自规划 | 没有真实调度,自升级只能手动触发 |
| SR-3.3 Verification Gate + Report | SR-3.3 follow-up 闭环、SR-5.3 自开发安全边界 | 自开发必须先能证明改动可验证 |
| SR-3.3 PR Review Follow-up | SR-5.3 Self-Fix Follow-up | 自升级 PR 需要自动处理 review 和 CI |
| SR-4.1 CreateAgentTool | SR-4.1 SOP、SR-5.1 社区能力吸收 | 动态工具创建是把新 SDK/API 转成 Agent 能力的基础 |
| SR-4.1 SOP | SR-4.3 业务 Agent 长期运行、SR-5.1 社区能力产品化 | 专业系统或外部工具可转为长期 Agent |
| SR-1.5 Remote Bridge + Attach | SR-4.2 远程值守、SR-5.4 自升级运行环境 | 自主运行不能依赖单个前台终端 |
| SR-3.4 A2A 协议 | SR-5.3 Self-Review Agent Team | 多 Agent 互审需要协议化协作 |
| SR-2.4 Usage Stats + SR-5.5 Outcome Store | SR-5.5 策略优化和工具裁剪 | 没有数据就无法自我优化 |

---

## 6. 时间节奏建议

### 6.1 近期:底层特性收敛与场景特性核心闭环

| 优先级 | 交付目标 | 涉及 SR/AR |
|--------|----------|------------|
| P0 | SR-2.2 Cron 端到端收敛 | AR-F-22（cron runtime 收敛/遗留功能补齐） |
| P0 | SR-3.3 PR review follow-up 闭环 | AR-F-37（PR Review 自动修复闭环） |
| P1 | SR-4.1 CreateAgentTool MVP | AR-F-18（AgentToolSpec / call_type / validator / Factory / 持久化/加载） |
| P1 | SR-1.4 REPL/TUI/headless 一致化 | AR-1.4.9（阶段间不重启 runtime）+ SR-1.4 hook |
| P1 | SR-1.4 Auto 权限模式 | AR-F-16（LLM Classifier + Cache + 危险动作 fallback） |
| P2 | SR-2.3 稳定性补强 | AR-F-11 / AR-F-12 / AR-F-4 / AR-F-51 |

### 6.2 中期:业务 Agent 与远程值守

| 优先级 | 交付目标 | 涉及 SR/AR |
|--------|----------|------------|
| P0 | SR-4.1 SOP MVP | AR-F-50（SOP 转换器固化：OpenAPI 解析/Skill Grouper/Agent Builder/CLI） |
| P1 | SR-4.3 业务 Agent 长期运行 | AR-NS-1 / AR-NS-2 / AR-NS-3 |
| P1 | SR-4.2 Autonomy Status | AR-F-22（Cron 驱动巡检与自治状态） |
| P1 | SR-4.2 Away Summary | AR-F-26（Away Summary 服务 + `/recap` Skill） |
| P2 | SR-3.4 A2A 协议雏形 | AR-F-2（A2A 协议化 Agent 互联） |
| P2 | SR-4.2 Remote Trigger MVP | AR-F-7（RemoteTrigger 远程启动与 WebUI） |

### 6.3 长期:自升级闭环 

| 优先级 | 交付目标 | 涉及 SR/AR |
|--------|----------|------------|
| P0 | SR-5.1 Community Feature Radar | AR-5.1.1 ~ AR-5.1.4 |
| P0 | SR-5.2 Self-Planning Gate | AR-5.2.1 ~ AR-5.2.5 |
| P1 | SR-5.3 Self-Development Loop | AR-5.3.1 ~ AR-5.3.7 |
| P2 | SR-5.4 Self-Update | AR-5.4.1 ~ AR-5.4.6 |
| P2 | SR-5.5 Self-Learning | AR-5.5.1 ~ AR-5.5.6 |

---

## 7. 验证标准

### 7.1 底层特性验证标准

| 标准 | 验收方式 |
|------|----------|
| REPL/TUI/headless 使用同一套 RuntimeContext,不重复构造工具和上下文 | CronCreate 在三种入口都命中 SR-2.2 扩展实现 |
| Cron 任务可以创建、持久化、触发、执行、查询结果 | 端到端 smoke:创建 durable recurring task,重启后继续触发并记录 completed run |
| 多 Agent 状态可观察、可注入、可审计 | Manager 能 inspect Worker、发送高优先级 directive,并在 SR-2.1 日志中可追溯 |
| 长期运行不出现明显内存无限增长 | SR-2.3 LRU 测试和 SR-1.5 daemon soak 测试通过 |
| 权限与模型选择可配置、可审计 | SR-1.4 settings、CLI、runtime 状态一致 |
| 工具使用统计可影响默认 bundle | SR-2.4 统计闭环 + 策略调整生效 |

### 7.2 场景特性验证标准

| 标准 | 验收方式 |
|------|----------|
| Issue → implementation → verification → PR → report 全链路自动完成 | 在 GitHub/Gitee/GitCode/LocalTracker 至少各跑一条真实 issue |
| PR review / CI 反馈能自动触发 follow-up commit | 人工发布 inline comment 或制造 CI 失败,SR-3.3 Agent 自动追加修复 commit |
| 用户可通过 label/comment/CLI 表达 retry/follow-up/blocked | 三种入口都能改变 SR-3.2 registry 和远程状态且有审计记录 |
| SOP 能转换成可运行业务 Agent | SR-4.1 输入一个 OpenAPI 或方法列表,生成 Agent JSON、Skill、工具并完成一次真实调用 |
| 业务 Agent 可长期运行并重连 | SR-4.3 daemon 启动后新窗口 attach,状态与 transcript 连续 |
| 多 Agent 协作可被 Manager 监督 | SR-3.4 Manager 能 inspect Worker、传递权限、注入 directive |

### 7.3 未来规划特性验证标准

| 标准 | 验收方式 |
|------|----------|
| ClawCodex 能定期输出社区 Agent 新特性摘要 | SR-5.1 Cron 触发 weekly digest,生成结构化 Markdown/JSON 报告 |
| ClawCodex 能自动提出符合自身架构边界的新特性设计 | SR-5.2 生成 FEATURE_PLAN 风格 proposal,并通过 Architecture Fit Checker |
| ClawCodex 能为自身创建 issue、开发、验证、提交 PR | SR-5.3 Self-Orchestrator 从 LocalTracker issue 生成 PR,含测试报告 |
| ClawCodex 能自动处理自升级 PR 的 review/CI feedback | SR-5.3 PR comment 或 CI failure 触发 follow-up 修复 commit |
| ClawCodex 能形成候选更新、发布说明和回滚点 | SR-5.4 生成 candidate registry、release notes、package/image,并支持 rollback dry-run |
| ClawCodex 能沉淀失败经验并改进策略 | SR-5.5 月度 retrospective 显示重复失败率下降,策略变更可审计 |

---

## 8. 风险与边界

| 风险 | 影响层级 | 说明 | 缓解策略 |
|------|----------|------|----------|
| Cron 端到端接线不完整 | 底层特性 / 场景特性 | 调度只停留在模块测试,无法支撑自动值守 | 以 SR-1.4 REPL/TUI/headless smoke 作为完成口径 |
| 自升级误改核心上游代码 | 未来规划特性 | 破坏 upstream sync 或引入难维护补丁 | 强制 SR-5.2 Architecture Fit Checker;默认写入 `clawcodex_ext/*`,`src/*` 仅 thin seam |
| PR feedback 自触发循环 | 场景特性 / 未来规划特性 | bot 评论触发自身反复修复 | SR-3.3 过滤 bot、幂等 store、max follow-up attempts |
| 自动权限误判 | 底层特性 / 场景特性 | SR-1.4 Auto mode 可能执行不应执行的命令 | classifier 三态输出,危险动作 fallback ask,审计所有 auto allow |
| 远程启动安全边界 | 场景特性 / 未来规划特性 | SR-4.2 RemoteTrigger 可能被滥用 | 默认关闭、强鉴权、最小权限、审计日志、速率限制 |
| 社区特性信息噪声 | 未来规划特性 | 过多无价值候选导致路线图漂移 | SR-5.1 趋势评分 + SR-5.2 User Review Gate,未审批不进入开发队列 |
| 自开发质量不稳定 | 未来规划特性 | Agent 生成 PR 质量不足 | SR-5.3 Self-Test Matrix + 多 Agent review + verification gate + rollback |
| 多 Agent 并发污染 workspace | 场景特性 / 未来规划特性 | SR-3.4 并发写同一工作区导致冲突 | SR-3.4 Shared/Sequential 策略、SR-5.3 worktree isolation、lock 与审计 |

---

## 9. 下一步行动

1. **优先收敛 Cron 端到端行为**:把 `clawcodex_ext/cron_system/*` 接入真实 REPL/TUI/headless runtime,完成 SR-2.2 中 scheduled fire → query pipeline → run store 的 smoke。
2. **启动 PR Review Feedback 闭环**:先实现统一 `PullRequestFeedback` 模型和 GitHub/Gitee/GitCode feedback API(AR-F-37 PR Review 自动修复闭环),再接入 Orchestrator poller。
3. **并行推进 CreateAgentTool MVP**:先交付安全 spec/validator/factory/persistence(AR-F-18),为 SR-4.1 SOP 和长期社区能力吸收打基础。
4. **补齐自动值守观测入口**:把 cron runs、orchestrator issue、team members、verification report 汇总到 SR-4.2 Autonomy Status(AR-F-22)统一输出。
5. **为未来规划特性做最小闭环试点**:先以"每周生成 Agent 社区新特性 digest(AR-5.1.1~3) + 手动审批 proposal(AR-5.2.5)"为最小可用版本,不直接自动改代码。

---

## 附录 A:AR 数量统计

> **注**: 以下 F-Number 在 ROADMAP 中引用但未出现在 FEATURE_PLAN 索引表中：
> F-1（Orchestrator 主循环，属于 §1 整体）、F-7（Remote WebUI，🔭 长期规划）、
> F-15（Shift+Tab 权限循环）、F-17（工具按需加载）、F-21（后台运行与恢复）、
> F-23（Skills System/Bridge）、F-26（Away Summary）、F-29（Manager 监督工具）、
> F-31（TUI 状态区）、F-34（Runtime Protocol）——均为早期已完成功能或长期规划，
> 纳入 ARCHIVED_FEATURES.md 归档记录。

| 类别 | 抽象需求 (IR) | 系统需求 (SR) | 组件需求 (AR) | 说明 |
|------|---------------|---------------|---------------|------|
| 底层特性 | 2 (IR-1, IR-2) | 9 (SR-1.1 ~ SR-1.5, SR-2.1 ~ SR-2.4) | ~96 | 保持原有细粒度分解（未合并重构） |
| 场景特性 | 2 (IR-3, IR-4) | 7 (SR-3.1 ~ SR-3.4, SR-4.1 ~ SR-4.3) | ~23 | 合并后每 AR 对应一个独立 F-N |
| 未来规划特性 | 1 (IR-5) | 5 (SR-5.1 ~ SR-5.5) | ~28 | 合并后每 AR 对应一个特性模块 |
| **合计** | **5** | **21** | **~147** | 较原 368 减少 ~60% |

每个 IR 下挂 4~5 个 SR,平均每 SR 下挂 3~7 个 AR（合并后）。
---
