# ClawCodex ROADMAP

> 文档路径: `ROADMAP.md`
> 信息来源: `docs/ARCHIVED_FEATURES.md`、`docs/FEATURE_PLAN.md`
> 版本: v3.0
> 更新日期: 2026-06-03

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
场景特性: Orchestrator / Issue → PR / PR Review Follow-up / POS to Agent / Remote Attach
        ↓
未来规划特性: 开源社区观察 → 新特性雷达 → 自主规划 → 自主开发 → 验证发布 → 经验沉淀
```

长期闭环目标: ClawCodex 能定期收集当前最新 Agent 开源社区的新特性,结合自身架构和用户使用数据生成新特性规划,再通过 Orchestrator、Cron、远程启动、Agent2Agent 协作、POS to Agent 转换和验证报告系统开发 ClawCodex 自身,最终形成 Agent 自己升级/更新自己的能力循环。

---

## 2. 底层特性 (Infrastructure Features)

### 2.1 IR-1 Agent 可运行底座（→ FEATURE_PLAN §4.3~§4.11 各节）

**抽象需求**: Agent 应当作为可独立运行的最小软件单元存在,具备完整的会话生命周期、工具发现与执行、模型灵活切换、终端与远程交互、后台与恢复能力。

#### SR-1.1 会话与上下文管理（→ FEATURE_PLAN §4.3 结构化输出（F-4）、§4.5 F-13 记忆隔离、§4.6 /goal（F-9）、§4.10 sessionStorage（F-11）、§4.11 F-12 cacheWarning）

让 Agent 在任何时刻都能进入、继续、恢复、隔离、切换会话,且不丢失关键上下文。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-1.1.1 | Agent 多轮执行循环 | 主循环、模型调用、工具调度、权限检查 | 用户可以在 REPL/TUI/headless 中持续让 Agent 理解任务并执行工具 | ✅ 已完成 | 已完成 | py 代码、会话运行时、测试 |
| AR-1.1.2 | 会话 ID 与 SessionStorage | UUID v4 + 短哈希、内存索引、持久化 | 用户可以查看、引用和恢复任意历史会话 | ✅ 已完成 | 已完成 | py 代码、内存索引、索引文件 |
| AR-1.1.3 | JSONL Transcript 写入与解析 | 结构化对话 + 工具调用 + metadata | 用户可追溯 Agent 做过什么、调用过哪些工具 | ✅ 已完成 | 已完成 | JSONL transcript、py 解析逻辑 |
| AR-1.1.4 | Resume Agent 断点恢复 | 从已有 transcript 续跑、重连、恢复 | 用户可重连、恢复中断任务,不必重新描述上下文 | ✅ 已完成 | 已完成 | py 代码、session 状态文件 |
| AR-1.1.5 | Fork Subagent 上下文隔离 | 创建独立子 Agent、隔离上下文与任务 | 用户可把复杂工作拆给后台子 Agent 并继续主会话 | ✅ 已完成 | 已完成 | py 代码、Agent 工具、JSONL transcript |
| AR-1.1.6 | Foreground Promotion 状态切换 | 后台 Agent 提升到前台、状态合并 | 用户可把后台任务拉回当前窗口继续处理 | ✅ 已完成 | 已完成 | py 代码、CLI/TUI 行为 |
| AR-1.1.7 | Prompt 构建与组装 | 系统 Prompt、Agent 定义、记忆、工具描述组装 | 用户可通过不同 Agent 类型获得不同能力组合 | ✅ 已完成 | 已完成 | py 代码、Agent markdown/json 定义 |
| AR-1.1.8 | Agent 类型与定义系统 | Agent 类型、工具、配置、模型定义 schema | 用户可选择或配置专用 Agent | ✅ 已完成 | 已完成 | py 代码、Agent 配置文件 |
| AR-1.1.9 | 记忆作用域加载 | user / project / agent / team 作用域过滤 | 用户感知为 Agent 只带入相关长期背景,减少无关上下文 | ✅ 已完成 | 已完成 | py 代码、memory 文件、配置 |
| AR-1.1.10 | 历史会话索引持久化 | 跨重启可搜索和恢复的索引 | 用户重启 CLI 后能快速找到上次会话 | ✅ 已完成 | 已完成 | py 代码、JSON 索引文件 |
| AR-1.1.11 | 会话重命名与标签 | 人类可读的会话名、tag 字段 | 用户可以在 dashboard 中按名字定位会话 | 📋 规划中 | 0.5 周 | py 代码、UI 渲染、CLI 命令 |
| AR-1.1.12 | 多 session 并发切换 | 同一窗口/不同窗口并发活跃会话 | 用户可在多个 Agent 任务间快速切换 | ✅ 已完成 | 已完成 | py 代码、CLI/TUI 切换命令 |

#### SR-1.2 工具与技能执行（→ FEATURE_PLAN §4.4 MCP 扩展（F-3）、§4.7 F-10 ExecuteExtraTool、§6.3 F-52 Python SDK Tool）

让 Agent 拥有发现、加载、调用、内置与外部工具的能力,并支持技能扩展和按需上下文控制。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-1.2.1 | 内置基础文件与搜索工具 | Read、Write、Edit、Glob、Grep | 用户可以让 Agent 读写代码、搜索项目 | ✅ 已完成 | 已完成 | py 工具代码、schema |
| AR-1.2.2 | Bash 执行工具 | 命令白名单、危险命令拦截、cwd 管理 | 用户可以让 Agent 运行命令完成构建、测试、部署 | ✅ 已完成 | 已完成 | py 工具代码、schema |
| AR-1.2.3 | 任务管理工具 | TaskCreate/TaskUpdate、Agent 调度 | 用户可以让 Agent 拆解任务、调用子 Agent | ✅ 已完成 | 已完成 | py 工具代码、Task 队列 |
| AR-1.2.4 | Cron Fallback 工具 | 旧版 Cron 工具兼容入口 | 老用户能继续使用熟悉的 cron 工具调用方式 | ✅ 已完成 → F-22 | 已完成 | py 工具代码、fallback 路由 |
| AR-1.2.5 | MCP Stdio 客户端 | 标准输入输出 MCP 服务接入 | 用户可在终端启动外部 MCP 服务扩展工具 | ✅ 已完成 | 已完成 | py MCP 客户端、配置 |
| AR-1.2.6 | MCP HTTP/SSE 客户端 | 基于 HTTP 和 SSE 的 MCP 服务 | 用户可接入云端 MCP 服务 | ✅ 已完成 | 已完成 | py MCP 客户端、配置 |
| AR-1.2.7 | MCP WebSocket 客户端 | 基于 WebSocket 的 MCP 服务 | 用户可接入实时双向 MCP 服务 | ✅ 已完成 | 已完成 | py MCP 客户端、配置 |
| AR-1.2.8 | MCP OAuth 与硬化 | OAuth 流程、HTTPS、XSS 防护 | 用户可安全接入鉴权 MCP 服务 | ✅ 已完成 | 已完成 | py MCP 客户端、auth、配置 |
| AR-1.2.9 | 工具按需加载 (Bundle) | Bare / Default / ClawCodex / All 模式 | 用户感知为上下文更轻,工具列表更聚焦 | ✅ 已完成 → F-17 | 已完成 | py registry、bundle 配置 |
| AR-1.2.10 | ToolSearch TF-IDF 搜索 | 工具描述的语义搜索与匹配 | 用户可通过搜索发现可用工具 | ✅ 已完成 | 已完成 | py 搜索代码、索引 |
| AR-1.2.11 | ExecuteExtraTool 延迟执行 | 大量工具的按需发现与调用 | 用户可在工具很多时按需调用额外工具 | ✅ 已完成 → F-10 | 已完成 | py 工具、动态注册逻辑 |
| AR-1.2.12 | Skills System Extension | 下游技能扩展层、bundle、路径、hook、cache | 用户可安装和调用 ClawCodex 专属 skill,且不破坏上游同步 | ✅ 已完成 → F-23 | 已完成 | py 代码、skill bundle、配置 |
| AR-1.2.13 | MCP 资源缓存 | MCP resource 读取缓存与失效 | 用户感知为外部资源加载更快、重复请求更少 | 📋 规划中 → F-3 | 1 周 | py 缓存模块、测试 |
| AR-1.2.14 | MCP Batch 工具调用 | 批量执行 MCP 工具、并发控制 | 用户可让 Agent 更高效地处理多步外部调用 | 📋 规划中 → F-3 | 1.5 周 | py MCP 批处理代码、测试 |
| AR-1.2.15 | MCP Progress 通知 | MCP 长任务进度反馈、token 流 | 用户可看到外部长任务执行进度 | 📋 规划中 → F-3 | 1 周 | py 通知代码、TUI/REPL 渲染 |

#### SR-1.3 模型与 Provider 接入（→ FEATURE_PLAN §5.1 CLI 模型供应商与模型切换设计（F-43））

让 Agent 能在不同模型供应商之间灵活切换,统一接口、统一 Token 统计与配置。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-1.3.1 | Anthropic Provider | 原生 Anthropic API、流式、tool use | 用户可使用 Claude 全系列模型 | ✅ 已完成 | 已完成 | py provider 代码、配置 |
| AR-1.3.2 | OpenAI Provider | 原生 OpenAI API、function calling、JSON mode | 用户可使用 GPT 系列模型 | ✅ 已完成 | 已完成 | py provider 代码、配置 |
| AR-1.3.3 | GLM Provider | 智谱 GLM 接入 | 用户可使用国内合规模型 | ✅ 已完成 | 已完成 | py provider 代码、配置 |
| AR-1.3.4 | MiniMax Provider | MiniMax 模型接入 | 用户可使用 MiniMax 系列模型 | ✅ 已完成 | 已完成 | py provider 代码、配置 |
| AR-1.3.5 | DeepSeek Provider | DeepSeek 接入 | 用户可使用 DeepSeek 系列模型 | ✅ 已完成 | 已完成 | py provider 代码、配置 |
| AR-1.3.6 | OpenRouter Provider | OpenRouter 聚合接入 | 用户可通过统一入口访问多种第三方模型 | ✅ 已完成 | 已完成 | py provider 代码、配置 |
| AR-1.3.7 | LiteLLM 适配器 | 统一 100+ 模型接口的适配层 | 用户可通过统一配置切换更多模型 | ✅ 已完成 → R-7 | 已完成 | py 适配器、配置开关 |
| AR-1.3.8 | LiteLLM 替换重复 Provider | 用 LiteLLM 替代部分直连 provider 重复逻辑 | 用户感知为模型切换更一致,新增模型更快 | ✅ 已完成 → R-7 | 已完成 | py provider 代码、兼容测试 |
| AR-1.3.9 | `model` 子命令与解析优先级 | model list/set/current、provider 优先级 | 用户可通过命令查看、设置和切换模型 | ✅ 已完成 → F-43 | 已完成 | py CLI 代码、解析器 |
| AR-1.3.10 | `provider` 子命令 | provider list/set/test | 用户可独立管理 provider 配置 | ✅ 已完成 → F-43 | 已完成 | py CLI 代码、配置 |
| AR-1.3.11 | Provider 配置存储 | JSON 配置 + 加密敏感字段 | 用户可版本化管理 provider 设置 | ✅ 已完成 → F-43 | 已完成 | py 代码、JSON 配置 |
| AR-1.3.12 | Token 计数与追踪 | 统一 token 计数接口、prompt/cache/completion 分类 | 用户可看到更准确的上下文和成本提示 | ✅ 已完成 → F-43 | 已完成 | py 代码、状态显示 |

#### SR-1.4 权限与前端交互（→ FEATURE_PLAN §4.13 Auto 模式（F-16）、§5.2 F-46 permission_mode、§5.3 F-47 Permission Schema）

让用户能在不同自动化程度下与 Agent 交互,并保证权限边界清晰可审计。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-1.4.1 | prompt_toolkit + Rich REPL Core | REPL 主循环、补全、历史、语法高亮 | 用户可在终端中与 Agent 对话和执行命令 | ✅ 已完成 | 已完成 | py REPL 代码、命令注册 |
| AR-1.4.2 | Textual TUI 渲染 | TUI 主界面、组件、主题 | 用户可用更丰富的终端 UI 操作 Agent | ✅ 已完成 | 已完成 | py TUI 代码、组件 |
| AR-1.4.3 | TUI 状态区与权限选择器 | 权限弹窗、状态条、消息流 | 用户在 TUI 中可清晰看到 Agent 状态并按需授权 | ✅ 已完成 → F-31 | 已完成 | py TUI 代码、组件 |
| AR-1.4.4 | REPL/TUI 双向切换 | `/tui` 与 `/repl` 命令、状态保留 | 用户可在两种界面间无缝切换 | ✅ 已完成 | 已完成 | py frontend 代码 |
| AR-1.4.5 | Shift+Tab 权限循环 | default、acceptEdits、plan、bypass/dontAsk 模式 | 用户可快速调整自动化程度 | ✅ 已完成 → F-15 | 已完成 | py keybinding、UI 状态 |
| AR-1.4.6 | Permission Settings Schema 重构 | 权限配置 schema 正交化、allow/deny/ask 分类 | 用户感知为权限配置更清晰、更可审计 | ✅ 已完成 → F-47 | 已完成 | py schema、配置迁移 |
| AR-1.4.7 | Runtime Protocol 与 Frontend Registry | 前后端解耦、runtime 消息协议 | 用户感知为 REPL/TUI/headless 行为更一致 | ✅ 已完成 → F-34 | 已完成 | py runtime/frontend 代码 |
| AR-1.4.8 | 扩展钩子 (extension hooks) | 前端事件订阅、自定义渲染 | 用户可自定义前端组件和提示 | ✅ 已完成 → F-23 | 已完成 | py hook 代码、示例 |
| AR-1.4.9 | REPL Ctrl+B 后台运行 | REPL 中把当前任务后台化 | 用户可像 TUI 一样在 REPL 中把长任务放到后台 | 📋 规划中 → F-21 | 1 周 | py REPL 代码、快捷键、测试 |
| AR-1.4.10 | LLM Classifier Auto 权限模式 | LLM 分类器自动判断工具调用是否可执行 | 用户在长任务中减少重复确认,同时保留安全边界 | 📋 规划中 → F-16 | 4 周 | py classifier、cache、权限集成、测试 |
| AR-1.4.11 | Classifier Cache | 分类结果缓存、降级到 ask | 用户感知为 auto 模式延迟更低、稳定性更高 | 📋 规划中 → F-16 | 1 周 | py 缓存、cache key、TTL |
| AR-1.4.12 | Auto 模式危险动作 fallback ask | 危险命令、未知工具强制 ask | 用户不会被 auto 模式静默执行破坏性操作 | 📋 规划中 → F-16 | 1 周 | py 风险评估、UI 确认、审计 |

#### SR-1.5 后台、恢复与远程桥接（→ FEATURE_PLAN §八 F-55 会话恢复增强）

让用户可以在后台安全运行 Agent、跨进程恢复、并通过远程方式接入。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-1.5.1 | BackgroundState 进程级状态 | 进程级后台状态机、恢复句柄 | 用户可安全后台化和恢复任务 | ✅ 已完成 → F-21 | 已完成 | py 状态代码 |
| AR-1.5.2 | TailFollower JSONL 增量 tail | transcript 增量读取、断点记忆 | 用户恢复会话时只读取新增事件,速度更快 | ✅ 已完成 → F-21 | 已完成 | py tail 代码 |
| AR-1.5.3 | SessionWatcher 目录监控 | 目录变更监控、inotify/FSEvents/ReadDirectoryChangesW | 用户可在会话变化时得到及时更新 | ✅ 已完成 → F-21 | 已完成 | py watcher 代码 |
| AR-1.5.4 | 平台 fallback 监控 | Linux/macOS/Windows 不同监控实现降级 | 用户在不同 OS 上都能用 watcher | ✅ 已完成 → F-21 | 已完成 | py 平台抽象、fallback |
| AR-1.5.5 | TUI Ctrl+B 后台化 | TUI 当前任务后台运行 | 用户可从 TUI 释放当前界面继续其他操作 | ✅ 已完成 → F-21 | 已完成 | py TUI 行为 |
| AR-1.5.6 | Graceful Shutdown | SIGTSTP/SIGINT/SIGTERM 安全处理 | 用户中断后不易丢失会话状态 | ✅ 已完成 → F-23 | 已完成 | py 信号处理 |
| AR-1.5.7 | Bridge 多 Session Daemon | 多会话 daemon、HTTP server | 用户可管理多个长期运行 session | ✅ 已完成 → F-23 | 已完成 | py daemon、HTTP server |
| AR-1.5.8 | Daemon 轮询与桥接协议 | daemon 与客户端长轮询/websocket | 用户可实时看到 session 状态变化 | ✅ 已完成 → F-23 | 已完成 | py 协议、消息格式 |
| AR-1.5.9 | Remote Bridge Core | 远程会话生命周期、跨进程通信 | 用户可远程连接和控制 Agent 会话 | ✅ 已完成 → F-23 | 已完成 | py bridge 代码、API |
| AR-1.5.10 | 跨进程 HTTP client | 远程桥接的 HTTP 调用封装 | 用户可从客户端无侵入连接 daemon | ✅ 已完成 → F-23 | 已完成 | py HTTP client、retries |
| AR-1.5.11 | REPL Bridge 集成 | REPL 与 bridge 集成、attach 命令 | 用户可把本地交互接入桥接会话 | ✅ 已完成 → F-23 | 已完成 | py bridge 代码 |
| AR-1.5.12 | Remote Control WebUI Docker 镜像 | Docker 化 WebUI 镜像 | 用户可通过浏览器远程查看、启动、接管 Agent | 🔭 长期规划 → F-7 | 6-8 周 | Docker 镜像、build 脚本 |
| AR-1.5.13 | WebUI 远程控制 API | 状态查询、attach、注入、kill | 用户在 Web 端可执行所有 CLI 操作 | 🔭 长期规划 → F-7 | 2 周 | py API、JSON schema |
| AR-1.5.14 | WebUI 鉴权与安全 | token、CSRF、速率限制 | 远程 WebUI 不会被未授权访问 | 🔭 长期规划 → F-7 | 1 周 | py 鉴权、配置、审计 |

### 2.2 IR-2 可观测、可调度与可维护底座（→ FEATURE_PLAN §4.1 进度汇报（F-20）、§4.8 工具统计（F-75）、§七 F-22 定时任务、§3.1.10 F-45、§3.1.12 F-51、§3.1.13 F-54、§6.1 F-48）

**抽象需求**: Agent 系统应支持任务进度上报、定时任务调度、长期运行稳定,并具备工具使用统计与策略优化的能力,确保无人值守场景下不失控、不沉默失败。

#### SR-2.1 任务进度与可观测（→ FEATURE_PLAN §4.1 Agent 进度汇报（F-20）、§3.1.13 F-54 运行期可观测性）

让用户和 Manager Agent 能清晰看到 Worker 状态、长任务阶段产出,并支持审计与回溯。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-2.1.1 | ProgressReportTool 阶段性写入 | Agent 主动写入阶段进度 | 用户可看到长任务当前阶段和阶段产出 | ✅ 已完成 → F-20 | 已完成 | py 工具、任务 metadata |
| AR-2.1.2 | 任务 metadata 维护 | 任务 ID、阶段、状态、依赖 | 用户可查询任务依赖与完成情况 | ✅ 已完成 | 已完成 | py 代码、JSON metadata |
| AR-2.1.3 | TaskInspect Manager 查询 | Manager 查询 Worker 状态、输出、错误 | 用户可让 Manager 监督子任务进展 | ✅ 已完成 → F-29 | 已完成 | py 工具、状态读取 |
| AR-2.1.4 | TaskDirectives Manager 注入 | Manager 向 Worker 注入优先级消息 | 用户可让 Manager 动态纠正或重排 Worker 工作 | ✅ 已完成 → F-29 | 已完成 | py 工具、pending message 队列 |
| AR-2.1.5 | per-session progress sink | 每个会话独立的进度 sink | 用户在多会话时不串进度 | 📋 设计完成 → F-40 | 1 周 | py sink 协议、event log |
| AR-2.1.6 | CompositeProgressSink | 多 issue 进度汇聚、过滤 | 用户在多 issue 并发时看到正确进度 | 📋 设计完成 → F-40 | 1 周 | py sink、测试 |
| AR-2.1.7 | Progress event log | ndjson 事件流、阶段完成、错误、warning | 用户可通过 CLI tail 看到阶段进度 | ✅ 已完成 → F-38 | 已完成 | ndjson event log、CLI 渲染 |
| AR-2.1.8 | 工具 / Skill 调用统计 | 统一 JSONL 调用日志 | 用户可知道哪些工具常用、失败率如何 | 📋 规划中 → F-18 | 1 周 | py stats、JSONL 日志、查询命令 |
| AR-2.1.9 | transcript 聚合查询 | 按 session/工具/时间聚合 | 用户可生成个人/团队使用报告 | 📋 规划中 → F-18 | 1 周 | py 查询 CLI、JSON 输出 |
| AR-2.1.10 | 编排场景 ndjson 审计 | 编排场景下工具调用审计流 | 用户可审查无人值守任务实际操作 | ✅ 已完成 → F-45 | 已完成 | ndjson audit、py sink |

#### SR-2.2 定时任务与调度（→ FEATURE_PLAN §七 F-22 定时任务系统）

让用户可描述、持久化、触发、查询定时任务,并支持多 Agent 场景下任务路由到正确的 Agent。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-2.2.1 | 5 字段 cron 解析器 | 标准 cron 表达式解析 | 用户可用标准 cron 表达式描述定时任务 | 🟡 进行中 → F-22 | 已完成核心,待接线 | py 代码、parser、测试 |
| AR-2.2.2 | next run 计算 | 给定表达式和时间,计算下一次执行时间 | 用户可预知任务什么时候触发 | 🟡 进行中 | 0.5 周 | py 代码、测试 |
| AR-2.2.3 | human schedule 文本解析 | "every 5 minutes" 等自然语言解析 | 用户可用自然语言描述调度 | 📋 规划中 | 1 周 | py parser、grammar |
| AR-2.2.4 | 任务模型 (CronTask) | 任务 ID、表达式、prompt、目标 agent | 用户可结构化描述一个定时任务 | 🟡 进行中 → F-22 | 已完成核心,待接线 | py dataclass、JSON schema |
| AR-2.2.5 | Durable Task Store | `.claude/scheduled_tasks.json` | 用户重启 CLI 后任务仍然保留 | 🟡 进行中 → F-22 | 1 周 | py 存储代码、JSON 配置 |
| AR-2.2.6 | Session Task Store | 会话内内存任务存储 | 用户可使用仅本会话生效的临时任务 | 🟡 进行中 → F-22 | 0.5 周 | py 存储代码、内存索引 |
| AR-2.2.7 | Scheduler Lock 文件锁 | 单实例防重复触发 | 用户感知为多窗口不会重复执行定时任务 | 🟡 进行中 → F-22 | 0.5 周 | py lock 代码、stale 检测 |
| AR-2.2.8 | 确定性 jitter | 触发时间加入小范围偏移 | 多实例同时启动不会在同一毫秒触发 | 🟡 进行中 | 0.5 周 | py jitter 代码、配置 |
| AR-2.2.9 | REPL/TUI/headless Runtime 接线 | 真实路径使用扩展 Cron 工具和 scheduler | 用户创建 cron 后任务会真正按时进入执行队列 | 🟡 进行中 → F-22 | 2 周 | py runtime/frontend 代码、smoke 测试 |
| AR-2.2.10 | CronDispatchBridge | scheduled fire 进入真实 prompt 队列 | 用户可看到定时任务像普通输入一样被执行 | 📋 规划中 → F-22 | 1.5 周 | py dispatch bridge、运行记录 |
| AR-2.2.11 | Cron Run Store | queued/running/completed/failed/cancelled 生命周期 | 用户可查询每次定时任务运行结果 | 📋 规划中 → F-22 | 1 周 | py run store、JSONL/JSON 账本 |
| AR-2.2.12 | `/loop` Skill + CronCreate 集成 | interval prompt 循环执行,默认 10m 并立即执行一次 | 用户可一句话设置循环任务 | 🟡 进行中 → F-22 | 0.5 周 | skill 代码、CronCreate 集成 |
| AR-2.2.13 | `/cron-list` / `/cron-delete` Skill | 定时任务列表和删除命令 | 用户可管理当前和持久化 cron 任务 | 📋 规划中 → F-22 | 0.5 周 | skill 代码、表格输出 |
| AR-2.2.14 | Missed One-shot 安全确认 | 错过的一次性任务启动后询问是否补跑 | 用户不会因为离线期间错过任务而被静默执行敏感 prompt | 📋 规划中 → F-22 | 0.5 周 | py notification、UI 文案 |
| AR-2.2.15 | Teammate Ownership 路由 | cron 任务按 agent/team 归属过滤和路由 | 用户在多 Agent 场景中不会把任务发错 Agent | 📋 规划中 → F-22 | 1 周 | py ownership 字段、过滤逻辑 |

#### SR-2.3 稳定性与开放替代（→ FEATURE_PLAN §3.1.10 F-45 Tool-call 审计、§3.1.12 F-51 空转检测、§6.1 F-48: src/ 核心路径二开修改解耦方案）

让系统能长期稳定运行、避免 OOM 和内存泄漏,并通过架构解耦与成熟开源 SDK 替代降低维护成本。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-2.3.1 | sessionStorage LRU 限制 | LRU 限制 existingSessionFiles 数量 | 用户长期运行 daemon 不易 OOM | 📋 规划中 → F-11 | 2 天 | py LRU、测试 |
| AR-2.3.2 | cacheWarning source entries LRU | cacheWarning 容量限制 | 用户长期运行不易内存泄漏 | 📋 规划中 → F-12 | 2 天 | py LRU、测试 |
| AR-2.3.3 | Outlines Token 预算 | 结构化输出 token 预算控制 | 用户感知为 Agent 决策更稳定 | 📋 规划中 → F-4 | 0.5 周 | py adapter 集成、预算逻辑 |
| AR-2.3.4 | Outlines 工具决策结构化 | 工具调用决策走结构化 schema | 用户感知为 JSON 解析错误更少 | 📋 规划中 → F-4 | 1 周 | py adapter、Pydantic schema |
| AR-2.3.5 | Outlines 压缩策略 | 长 prompt 结构化压缩 | 用户感知为长任务下决策仍然稳定 | 📋 规划中 → F-4 | 0.5 周 | py adapter、压缩规则 |
| AR-2.3.6 | pydantic-settings 替代 | 配置加载统一到 pydantic-settings | 用户感知为系统更稳定,配置更规范 | ✅ 已完成 → R-1 | 已完成 | py 依赖、替换代码、测试 |
| AR-2.3.7 | python-frontmatter 替代 | LocalTracker frontmatter 解析 | 用户感知为本地 issue 解析更稳定 | ✅ 已完成 → R-2 | 已完成 | py 依赖、替换代码、测试 |
| AR-2.3.8 | tree-sitter-bash 替代 | Bash 命令 AST 解析 | 用户感知为命令识别与危险判定更准 | ✅ 已完成 → R-3 | 已完成 | py 依赖、替换代码、测试 |
| AR-2.3.9 | GitPython 替代 | git 操作统一封装 | 用户感知为 git 相关操作更稳 | ✅ 已完成 → R-4 | 已完成 | py 依赖、替换代码、测试 |
| AR-2.3.10 | Pluggy 替代 | 插件系统标准化 | 用户可更规范地编写扩展 | ✅ 已完成 → R-5 | 已完成 | py 依赖、替换代码、测试 |
| AR-2.3.11 | Outlines 集成 | 结构化输出统一框架 | 用户感知为 Agent 决策更稳 | 📋 规划中 → F-4 | 1 周 | py 依赖、adapter、测试 |
| AR-2.3.12 | Daemon Soak 测试 | 长期运行不 OOM、不丢事件 | 用户在长值守中不丢会话 | 📋 规划中 → F-22 | 1 周 | py soak 测试、监控 |

#### SR-2.4 工具使用统计与策略（→ FEATURE_PLAN §4.8 工具/Skill 调用统计（F-75））

让用户能根据真实使用数据优化工具集,减少上下文噪音,提升常用能力优先级。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-2.4.1 | 工具调用 JSONL 日志 | 每次工具调用的时间、参数、结果、延迟 | 用户可统计工具使用情况 | 📋 规划中 → F-18 | 0.5 周 | py 日志 writer |
| AR-2.4.2 | Skill 调用统计 | Skill 调用频率、成功率、错误 | 用户可知道哪些 skill 实际有用 | 📋 规划中 → F-18 | 0.5 周 | py 日志 writer |
| AR-2.4.3 | transcript 聚合查询 | 按时间/工具/会话聚合统计 | 用户可生成个人/团队使用报告 | 📋 规划中 → F-18 | 1 周 | py 查询 CLI、JSON 输出 |
| AR-2.4.4 | 工具使用频率统计 | 滑动窗口内的工具调用计数 | 用户可识别高频、低频工具 | 📋 规划中 → F-18 | 0.5 周 | py 统计、配置 |
| AR-2.4.5 | 低频工具识别 | 阈值 + 时间窗口过滤 | 用户可自动识别长期不用的工具 | 📋 规划中 → F-18 | 0.5 周 | py 阈值、规则 |
| AR-2.4.6 | 工具隐藏策略 | Bundle 中按频率隐藏低频工具 | 用户感知为上下文更轻、工具列表更干净 | 📋 规划中 → F-18 | 0.5 周 | py pruning、配置 |
| AR-2.4.7 | 工具建议 | 根据任务上下文推荐工具 | 用户感知为 Agent 选工具更准 | 📋 规划中 → F-18 | 1 周 | py 推荐、相似度 |
| AR-2.4.8 | 工具按需加载策略 | 通过 ToolSearch 按需发现 | 用户感知为默认工具列表更精简 | 📋 规划中 → F-18 | 1 周 | py 按需加载 |
| AR-2.4.9 | 工具裁剪配置 | 用户可配置裁剪规则 | 用户可自主决定保留哪些工具 | 📋 规划中 → F-18 | 0.5 周 | py 配置、CLI |
| AR-2.4.10 | 工具分类 (高频/低频/危险) | 工具分类元数据 | 用户可在 bundle 中显式分类 | ✅ 已完成 | 已完成 | py metadata、bundle |
| AR-2.4.11 | 工具使用报表 | 周期性生成使用报告 | 用户可审视工具使用趋势 | 📋 规划中 → F-18 | 1 周 | py 报表生成、Markdown |

---

## 3. 场景特性 (Scenario Features)

### 3.1 IR-3 研发自动化场景（→ FEATURE_PLAN §3.1.3~§3.2（F-68）、§4.2（F-2）、§4.12（F-78）、§4.14（F-80））

把用户真实研发流程(Issue 处理、PR 评审、多 Agent 协作)自动化,并保证自动化失败时可被用户接管、纠偏、追溯。

#### SR-3.1 Issue → PR 编排（→ FEATURE_PLAN §3.1.3 F-36 LocalTracker、§3.1.9 F-42 Workspace 策略、§3.1.14 F-44 人工检视闸门、§3.2 Orchestrator CLI（F-68））

让用户从不同 issue 源(Linear/GitHub/Gitee/GitCode/本地)拉取 issue,自动创建隔离工作区、运行 Agent、生成 PR,并支持重试、跳过、限频、运维。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-3.1.1 | Orchestrator 主循环 | 轮询 issue、领取、创建 workspace、运行 Agent | 用户可启动 daemon 自动处理 issue | ✅ 已完成 → F-1 | 已完成 | py orchestrator、CLI |
| AR-3.1.2 | WorkspaceManager 隔离策略 | 每 issue 隔离工作区、清理策略 | 用户可同时处理多个 issue,互不污染 | ✅ 已完成 → F-42 | 已完成 | py workspace、目录产物 |
| AR-3.1.3 | 多 Workspace 并发 | 并发 issue 数、队列 | 用户可配置 daemon 并发度 | ✅ 已完成 → F-42 | 已完成 | py 配置、并发控制 |
| AR-3.1.4 | Linear Tracker Adapter | Linear API issue 接入 | 用户可把 Linear 项目接入编排 | ✅ 已完成 → F-1 | 已完成 | py tracker adapter、配置 |
| AR-3.1.5 | GitHub Tracker Adapter | GitHub Issues API 接入 | 用户可把 GitHub 项目接入编排 | ✅ 已完成 → F-1 | 已完成 | py tracker adapter、配置 |
| AR-3.1.6 | Gitee Tracker Adapter | Gitee Issues API 接入 | 用户可把 Gitee 项目接入编排 | ✅ 已完成 → F-1 | 已完成 | py tracker adapter、配置 |
| AR-3.1.7 | GitCode Tracker Adapter | GitCode Issues API 接入 | 用户可把 GitCode 项目接入编排 | ✅ 已完成 → F-1 | 已完成 | py tracker adapter、配置 |
| AR-3.1.8 | LocalTracker md/json | 本地 issue 文件源 | 用户可不用远程平台,在本地文件夹中排队任务 | ✅ 已完成 → F-36 | 已完成 | py LocalTracker、md/json issue 文件 |
| AR-3.1.9 | LocalTracker frontmatter 状态写回 | 状态字段、字段约束 | 用户可看到 issue 状态自动写回文件 | ✅ 已完成 → F-36 | 已完成 | py 写回逻辑、frontmatter |
| AR-3.1.10 | Human Review Gate | pending_review、approve/reject CLI | 用户可先审查 diff,再决定是否接受本地 Agent 修改 | ✅ 已完成 → F-44 | 已完成 | py CLI、状态字段、diff 输出 |
| AR-3.1.11 | IssueRegistry | issue→branch→commit→PR→report 状态 | 用户重启 daemon 后不会重复处理已完成 issue | ✅ 已完成 → F-1 | 已完成 | JSON registry、py store |
| AR-3.1.12 | Retry / Backoff | 失败重试、指数退避、最大重试次数 | 用户感知为临时失败会自动恢复,持续失败会停下等待处理 | ✅ 已完成 → F-1 | 已完成 | py 调度代码、配置 |
| AR-3.1.13 | Issue State 前置检查 | launch 前重新确认 issue 是否 active | 用户关闭或取消 issue 后 Agent 不会继续误处理 | ✅ 已完成 → F-1 | 已完成 | py tracker 调用 |
| AR-3.1.14 | 已有 PR 跳过 | launch 前检测已有 PR | 用户不会因为 daemon 重启重复创建 PR | ✅ 已完成 → F-1 | 已完成 | py git sync/tracker 调用 |
| AR-3.1.15 | Orchestrator CLI 运维 | server/issue/dashboard noun-verb | 用户可查看、暂停、恢复、停止、接管、注入提示 | ✅ 已完成 → F-1 | 已完成 | py CLI、dashboard |
| AR-3.1.16 | Dashboard LiveView | issue 状态、tool call、LLM 摘要实时展示 | 用户可观察无人值守任务进展 | ✅ 已完成 → F-1 | 已完成 | py dashboard、event stream |

#### SR-3.2 澄清、重跑与人机协同（→ FEATURE_PLAN §3.1.6 F-39 Issue 重跑、§3.1.11 F-49 会话统一存储、§4.12 Issue 语义澄清流程（F-78））

让 Agent 在不确定时主动询问用户,支持多渠道回答冲突裁决;支持从 issue label/comment/CLI 三种入口重跑任务,并具备限频、角色校验与审计;支持运行中提示注入与人工接管。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-3.2.1 | Local Dashboard 通道 | 本地 dashboard 提问 UI | Agent 不确定时会询问用户 | ✅ 已完成 → F-1 | 已完成 | py UI、JSON 文件 |
| AR-3.2.2 | CLI Queue 通道 | CLI 排队回答命令 | 用户可在终端批量回答 Agent 提问 | ✅ 已完成 → F-1 | 已完成 | py CLI、queue |
| AR-3.2.3 | Tracker 评论通道 | 在 issue/comment 上写澄清 | Agent 不确定时会询问 issue 作者 | ✅ 已完成 → F-1 | 已完成 | py tracker 评论、bot 通知 |
| AR-3.2.4 | Clarification 队列 | 跨通道的澄清问题管理 | 用户可从多渠道回答,系统统一管理 | ✅ 已完成 → F-1 | 已完成 | py queue、JSON 文件 |
| AR-3.2.5 | Operator 优先裁决 | 内部操作者回答优先于外部 | 用户可从多渠道回答,系统自动裁决有效答案 | ✅ 已完成 → F-1 | 已完成 | py 状态机、配置 |
| AR-3.2.6 | 超时升级 | 超过时长升级到下一渠道 | 用户不会卡在等待回答的流程 | ✅ 已完成 → F-1 | 已完成 | py 状态机、配置 |
| AR-3.2.7 | 去重与过期拒绝 | 同一问题只接受一次有效回答 | 用户不会被重复问题骚扰 | ✅ 已完成 → F-1 | 已完成 | py 状态机、配置 |
| AR-3.2.8 | `agent:retry` label | 通过 label 触发重做 | 用户可通过标签表达重做 | ✅ 已完成 | 已完成,真实环境待继续验证 | py tracker、registry 字段 |
| AR-3.2.9 | `agent:follow-up` label | 触发追加修改 | 用户可通过标签表达追加修改 | ✅ 已完成 | 已完成,真实环境待继续验证 | py tracker、registry 字段 |
| AR-3.2.10 | `agent:blocked` label | 标记为永久跳过 | 用户可通过标签表达永久跳过 | ✅ 已完成 | 已完成,真实环境待继续验证 | py tracker、registry 字段 |
| AR-3.2.11 | `/agent retry` comment 命令 | 评论中触发重跑 | 外部协作者可在 issue 评论中触发 Agent 重跑意图 | ✅ 已完成 → F-39 | 已完成,真实环境待继续验证 | py comment parser、bot 确认 |
| AR-3.2.12 | `/agent follow-up` comment 命令 | 评论中触发追加修改 | 外部协作者可在 issue 评论中触发追加 | ✅ 已完成 → F-39 | 已完成,真实环境待继续验证 | py comment parser、bot 确认 |
| AR-3.2.13 | `/agent unblock` comment 命令 | 评论中触发解除阻塞 | 外部协作者可在 issue 评论中触发解阻 | ✅ 已完成 → F-39 | 已完成,真实环境待继续验证 | py comment parser、bot 确认 |
| AR-3.2.14 | comment parser 与 bot 确认评论 | 解析 + 回写确认 | 协作者知道命令已接收 | ✅ 已完成 → F-39 | 已完成 | py 解析器、bot 消息 |
| AR-3.2.15 | `orchestrator issue retry --mode` CLI | 本地操作者直接重置 | 本地操作者可不用改 registry 直接重置任务 | ✅ 已完成 → F-39 | 已完成 | py CLI、audit.jsonl |
| AR-3.2.16 | max retries 限频 | 防止恶意无限重试 | 用户不会被恶意评论无限触发重跑 | ✅ 已完成 → F-39 | 已完成 | py 限频代码 |
| AR-3.2.17 | maintainer/author 角色校验 | 校验评论人角色 | 用户不会被普通评论者触发重跑 | ✅ 已完成 → F-39 | 已完成 | py 权限检查 |
| AR-3.2.18 | audit.jsonl 审计 | 所有重跑/解阻/注入都留痕 | 用户可追溯每次操作 | ✅ 已完成 → F-39 | 已完成 | ndjson audit |
| AR-3.2.19 | Operator Hint 注入 | 运行中向 issue Agent 注入提示 | 用户可不中断 Agent 的情况下纠偏 | ✅ 已完成 → F-39 | 已完成 | py CLI、operator_hints 文件 |
| AR-3.2.20 | Takeover 接管 | 终止 Agent 并进入 REPL 接管 workspace | 用户可在自动化失控或复杂场景下手动接手 | ✅ 已完成 → F-49 | 已完成 | py CLI、REPL attach |

#### SR-3.3 验证、报告与 PR 质量（→ FEATURE_PLAN §3.1.4 F-37 PR 检视修复、§3.1.5 F-38 验证与报告、§3.1.7 F-40 ProgressReporter）

让 Agent 在 commit/push 前必须验证,生成结构化报告,把信息回写到 PR body / 评论 / event log;并支持对 PR review 反馈的自动 follow-up。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-3.3.1 | Verification Gate (test/build/lint/hooks) | commit/push 前运行验证 | 用户看到未验证通过的代码不会被自动推送 | ✅ 已完成 → F-38 | 已完成 | py git_sync、workflow 配置 |
| AR-3.3.2 | commit/push 前置检查 | 验证不通过则阻止 | 用户不会看到有问题的代码被推上去 | ✅ 已完成 → F-38 | 已完成 | py git_sync 检查 |
| AR-3.3.3 | `.reports/{id}.md` 报告 | Markdown 摘要 | 用户可阅读本次 Agent 修改摘要 | ✅ 已完成 | 已完成 | Markdown 报告、py writer |
| AR-3.3.4 | `.reports/{id}.json` 报告 | 结构化 JSON | 用户可机读报告、做二次分析 | ✅ 已完成 | 已完成 | JSON 报告、py writer |
| AR-3.3.5 | Markdown 报告生成 | 模板、字段填充 | 用户可阅读结构化修改摘要 | ✅ 已完成 → F-38 | 已完成 | py writer、模板 |
| AR-3.3.6 | JSON 报告生成 | 字段定义、schema | 用户可机读报告 | ✅ 已完成 → F-38 | 已完成 | py writer、schema |
| AR-3.3.7 | PR Body 模板 | Issue / Branch / Commit / Verification / Report 字段 | Reviewer 打开 PR 即可看到 Agent 工作产物 | ✅ 已完成 → F-38 | 已完成 | py tracker update、PR body 模板 |
| AR-3.3.8 | PR 汇总评论 | Run Complete + Git Sync 合并为一条 | 用户不会被多条重复 bot 评论干扰 | ✅ 已完成 → F-38 | 已完成 | py comment 逻辑 |
| AR-3.3.9 | PhaseComplete 写入 issue event log | ndjson 事件流 | 用户可通过 `issue tail` 看到阶段进度 | ✅ 已完成 → F-38 | 已完成 | ndjson event log |
| AR-3.3.10 | `issue tail` CLI 渲染 | 实时/历史事件流 | 用户可看到阶段进度 | ✅ 已完成 → F-38 | 已完成 | py CLI、渲染 |
| AR-3.3.11 | PullRequestFeedback 模型 | 规范化评论、inline、summary、CI | 用户可让 Agent 理解 reviewer 评论和 CI 失败 | 📋 规划中 → F-37 | 1 周 | py dataclass、tracker interface |
| AR-3.3.12 | GitHub review comments API | 拉取 review comments | 用户无需手动复制 PR 评论给 Agent | 📋 规划中 → F-37 | 0.5 周 | py repo client、API 映射 |
| AR-3.3.13 | Gitee review comments API | 拉取 review comments | 用户可接入 Gitee PR 评论 | 📋 规划中 → F-37 | 0.5 周 | py repo client、API 映射 |
| AR-3.3.14 | GitCode review comments API | 拉取 review comments | 用户可接入 GitCode PR 评论 | 📋 规划中 → F-37 | 0.5 周 | py repo client、API 映射 |
| AR-3.3.15 | CI checks / pipelines 解析 | 解析 CI 状态与日志 | 用户可让 Agent 处理 CI 失败 | 📋 规划中 → F-37 | 1 周 | py 解析器、错误分类 |
| AR-3.3.16 | Review Follow-up Poller | 周期性扫描 open PR 新反馈 | 用户感知为 PR 收到评论后 Agent 自动继续修 | 📋 规划中 → F-37 | 2 周 | py orchestrator poller、配置 |
| AR-3.3.17 | Review-fix Prompt Builder | 只处理 PR feedback,不扩大需求 | 用户看到 Agent 针对 review 做最小修改 | 📋 规划中 → F-37 | 1 周 | py prompt builder、模板 |
| AR-3.3.18 | 同 PR 分支 Follow-up Sync | 追加 commit + push 原分支 | Reviewer 在原 PR 看到新提交解决评论 | 📋 规划中 → F-37 | 1 周 | py git_sync mode、测试 |
| AR-3.3.19 | 追加 commit + push | 不创建新 PR | 用户不会被新 PR 干扰 | 📋 规划中 → F-37 | 0.5 周 | py git_sync、force-with-lease |
| AR-3.3.20 | Feedback 幂等 Store | 已处理 feedback/check id 记录 | 用户不会看到 Agent 对同一条评论反复修复 | 📋 规划中 → F-37 | 1 周 | JSON store、registry 字段 |
| AR-3.3.21 | 评论回复 | 自动回复哪些评论已处理 | 用户和 reviewer 可追踪 Agent 的处理边界 | 📋 规划中 → F-37 | 0.5 周 | py tracker reply、PR 评论 |
| AR-3.3.22 | 处理摘要 (处理/需人工确认) | 哪些自动处理、哪些需人工 | 用户和 reviewer 可看到哪些由 Agent 完成 | 📋 规划中 → F-37 | 0.5 周 | py 摘要、PR 评论 |

#### SR-3.4 多 Agent 编排与 A2A 协作（→ FEATURE_PLAN §3.1.8 F-41 Coordinator、§4.2 Team 管理（F-2）、§4.14 Agent 间交互（F-80））

让用户可启动团队、管理 Manager / Worker、传递权限、共享或隔离工作区;并最终把团队协作抽象为 Agent2Agent 协议,让本地/远程/第三方 Agent 互联。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-3.4.1 | TeamCreate | 创建团队 | 用户可启动一个由多个 Agent 组成的团队 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py Team 工具、team JSON |
| AR-3.4.2 | TeamDelete | 删除团队 | 用户可清理已结束的团队 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py Team 工具 |
| AR-3.4.3 | Team members 数组 schema | 团队成员定义、角色 | 用户可看到团队中有哪些 Agent | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | JSON team 文件 |
| AR-3.4.4 | Team 成员名称注册 | 唯一名称、命名空间 | 用户的 Agent 不会重名冲突 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py registry |
| AR-3.4.5 | Team 成员状态 | idle/busy/error 状态 | 用户可看到 Agent 工作状态 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py 状态机 |
| AR-3.4.6 | Manager / Worker 角色识别 | TaskInspect + TaskDirectives 组合识别 | 用户无需配置复杂角色即可获得管理型 Agent | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py 工具可见性逻辑 |
| AR-3.4.7 | TaskInspect 工具 | Manager 查询 Worker 状态和输出 | 用户可让 Manager 监督子任务进展 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py 工具、状态读取 |
| AR-3.4.8 | TaskDirectives 工具 | Manager 向 Worker 注入优先级消息 | 用户可让 Manager 动态纠正或重排 Worker 工作 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py 工具、pending message 队列 |
| AR-3.4.9 | 优先级消息队列 | 紧急消息不被普通消息淹没 | 用户的紧急指令不会被普通消息淹没 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py queue 逻辑 |
| AR-3.4.10 | queue/drain 按 priority 消费 | 高优先级优先消费 | 用户的紧急指令先被执行 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py queue 实现 |
| AR-3.4.11 | Manager 给 Worker 传递 permission mode | 权限透传 | 用户可在团队任务中控制 Worker 自动化边界 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py 权限传递代码 |
| AR-3.4.12 | Manager 给 Worker 传递 allow rules | allow/deny 规则透传 | 用户可精确控制 Worker 能做什么 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py 权限传递代码 |
| AR-3.4.13 | Coordinator 轻量工具集 | 编排场景的轻量协调工具 | 用户可用更小上下文进行任务分派与观察 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py 工具 bundle |
| AR-3.4.14 | Shared Workspace 策略 | 共享工作区,可见彼此修改 | 用户可让多 Agent 在同一仓协作 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py workspace 策略、配置 |
| AR-3.4.15 | Sequential Workspace 策略 | 顺序 workspace,逐个串行处理 | 用户可逐个串行处理 issue | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | py workspace 策略、配置 |
| AR-3.4.16 | Tool-call 审计旁路 | 编排场景下记录工具调用审计流 | 用户可审查无人值守任务实际操作 | ✅ 已完成 → F-41 | F-42 | F-45 | 已完成 | ndjson audit、py sink |
| AR-3.4.17 | A2A 协议化消息 | Manager/Worker 消息抽象为 Agent2Agent 协议 | 用户可连接本地、远程、第三方 Agent 进行协作 | 🔭 长期规划 → F-2 | 4-6 周 | py protocol、JSON schema |
| AR-3.4.18 | A2A message schema | 标准化消息格式 | 用户的 Agent 可与外部 Agent 互联 | 🔭 长期规划 → F-2 | 1 周 | JSON schema、validator |
| AR-3.4.19 | A2A bridge adapter | 协议到本地实现的适配 | 用户的 Agent 可通过 bridge 加入协议 | 🔭 长期规划 → F-2 | 2 周 | py adapter、示例 |
| AR-3.4.20 | A2A 能力发现 | Agent 发布自身工具、技能、权限和状态 | 用户可让 Manager 自动选择合适 Agent | 🔭 长期规划 → F-2 | 3-4 周 | py discovery、capability manifest |
| AR-3.4.21 | A2A capability manifest | 能力清单、版本、依赖 | 用户的 Manager 可根据 manifest 选 Agent | 🔭 长期规划 → F-2 | 1 周 | JSON manifest、validator |

### 3.2 IR-4 业务 Agent 与远程值守（→ FEATURE_PLAN §4.9（F-18）、§6.2、§6.3）

把专业系统(POS)和远程值守转成可长期运行、可被 ClawCodex 调度的业务 Agent,支持主 Agent 切换、daemon 模式与跨设备 attach。

#### SR-4.1 POS to Agent（→ FEATURE_PLAN §4.9 CreateAgentTool（F-18）、§6.2 F-50 POS 转换器固化、§6.3 F-52 Python SDK Tool）

把业务系统 SDK / OpenAPI 转成可被 Agent 调用的原子接口和 Skill,并组装为业务 Agent。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-4.1.1 | AgentToolSpec dataclass | 工具声明结构 | 用户可让 Agent 描述并创建新工具 | 📋 规划中 → F-18 | 0.5 周 | py dataclass、JSON schema |
| AR-4.1.2 | bash call_type | Bash 类型工具 | 用户可让 Agent 创建可执行命令的工具 | 📋 规划中 → F-18 | 0.5 周 | py call_type、handler |
| AR-4.1.3 | http call_type | HTTP 类型工具 | 用户可让 Agent 创建 HTTP API 工具 | 📋 规划中 → F-18 | 0.5 周 | py call_type、handler |
| AR-4.1.4 | python call_type | Python 函数类型工具 | 用户可让 Agent 创建 Python 函数工具 | 📋 规划中 → F-18 | 0.5 周 | py call_type、handler |
| AR-4.1.5 | 命令白名单 validator | bash 命令白名单 | 用户可安全使用 Agent 动态创建的工具 | 📋 规划中 → F-18 | 0.5 周 | py validators、测试 |
| AR-4.1.6 | HTTP 方法白名单 validator | GET/POST/PUT/DELETE 白名单 | 用户可避免 Agent 误调危险 HTTP 方法 | 📋 规划中 → F-18 | 0.3 周 | py validators、测试 |
| AR-4.1.7 | Python 函数白名单 validator | 函数/模块白名单 | 用户可避免 Agent 误调危险 Python 函数 | 📋 规划中 → F-18 | 0.3 周 | py validators、测试 |
| AR-4.1.8 | 防注入 validator | 参数、shell 注入防护 | 用户可避免 Agent 工具被注入攻击 | 📋 规划中 → F-18 | 0.4 周 | py validators、测试 |
| AR-4.1.9 | Factory 构造注册工具 | spec → 工具实例 | 用户创建工具后可立即调用 | 📋 规划中 → F-18 | 0.5 周 | py factory、call handlers |
| AR-4.1.10 | call handlers | bash / http / python 实际执行 | 用户的工具可真正运行 | 📋 规划中 → F-18 | 1 周 | py handlers、超时、错误处理 |
| AR-4.1.11 | `~/.clawcodex/agent-tools/{name}.json` 持久化 | 工具定义存储 | 用户重启后仍能使用 Agent 创建的工具 | 📋 规划中 → F-18 | 0.3 周 | JSON 工具定义、loader |
| AR-4.1.12 | Agent Tool loader | 启动时加载已保存工具 | 用户的工具在重启后自动可用 | 📋 规划中 → F-18 | 0.2 周 | py loader |
| AR-4.1.13 | CreateAgentTool 对外工具 | Agent 调用的工具入口 | 用户可让 Agent 根据 CLI/API 规范扩展自己 | 📋 规划中 → F-18 | 1 周 | py tool、schema、测试 |
| AR-4.1.14 | OpenAPI JSON 解析 | 从 OpenAPI spec 解析接口 | 用户可把业务系统 SDK 输入给 ClawCodex | 📋 规划中 → F-50 | 0.5 周 | py parser、JSON 输入 |
| AR-4.1.15 | OpenAPI URL 解析 | 从 URL 拉取 OpenAPI spec | 用户可输入一个 URL 自动解析 | 📋 规划中 → F-50 | 0.5 周 | py parser、URL 输入 |
| AR-4.1.16 | 方法列表解析 | 简单方法列表输入 | 用户可手动列出方法生成工具 | 📋 规划中 → F-50 | 0.5 周 | py parser、JSON 输入 |
| AR-4.1.17 | Skill Grouper 原子接口分组 | 按业务流程分组 | 用户看到专业系统被拆成可理解的步骤 | 📋 规划中 → F-50 | 1 周 | py grouper、mapping config |
| AR-4.1.18 | Agent Builder 生成 Agent 定义 | 根据 Skill 和工具生成 Agent | 用户可得到一个面向业务的专用 Agent | 📋 规划中 → F-50 | 1 周 | py builder、Agent JSON |
| AR-4.1.19 | `/convert-pos-to-agent` Skill | 一条命令转换专业系统 | 用户可把 CI/CD、数据分析、ML pipeline 等转成 Agent | 📋 规划中 → F-50 | 1 周 | skill 代码、模板、配置 |
| AR-4.1.20 | `~/.clawcodex/agents/<name>.json` 持久化 | 业务 Agent 定义存储 | 用户可长期保存和复用业务 Agent | 📋 规划中 → F-50 | 0.5 周 | Agent JSON、loader |
| AR-4.1.21 | `clawcodex --agent <name>` 主 Agent 指定 | CLI 参数指定主 Agent | 用户可直接进入专用 Agent 工作模式 | 📋 规划中 → F-50 | 0.3 周 | CLI 参数、设置 |
| AR-4.1.22 | default_agent 配置 | settings.json 默认 Agent | 用户可配置启动默认 Agent | 📋 规划中 → F-50 | 0.2 周 | settings JSON |
| AR-4.1.23 | `clawcodex --daemon --agent` 启动 | 守护进程模式 | 用户可长期运行业务 Agent | 📋 规划中 → F-50 | 1 周 | py daemon、attach 协议 |
| AR-4.1.24 | `clawcodex attach` 重连 | 客户端 attach 到 daemon | 用户可在新窗口重连 | 📋 规划中 → F-50 | 0.5 周 | py attach 客户端 |
| AR-4.1.25 | attach 协议 (socket/pipe) | unix socket / named pipe | 用户的 attach 可跨进程通信 | 📋 规划中 → F-50 | 0.5 周 | py 协议、安全 |

#### SR-4.2 远程启动与自动值守（→ FEATURE_PLAN 🔭 待补充设计）

让用户从外部系统或远端 cron 启动 Agent;支持值守期间的状态汇总、离开摘要和 Web 监督。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-4.2.1 | `orchestrator server start/status/stop` CLI | Daemon 启停查询 | 用户可让 ClawCodex 持续值守 issue 队列 | ✅ 已完成 → F-1 | 已完成 | py daemon CLI、状态文件 |
| AR-4.2.2 | Daemon 状态文件 | 运行/错误/重启计数 | 用户可看到 daemon 自身健康度 | ✅ 已完成 → F-1 | 已完成 | py 状态文件 |
| AR-4.2.3 | Cron 驱动 issue 巡检 | 定时检查新 issue | 用户可把重复巡检任务交给 Agent | 🟡 进行中 → F-22 | 1 周 | py cron runtime、issue 巡检 |
| AR-4.2.4 | Cron 驱动报告生成 | 定时生成报告 | 用户可定时拿到业务报告 | 🟡 进行中 → F-22 | 1 周 | py cron runtime、报告生成 |
| AR-4.2.5 | Cron 驱动社区扫描 | 定时扫描开源社区 | 用户可定期接收社区动态 | 🟡 进行中 → F-22 | 1 周 | py cron runtime、社区扫描 |
| AR-4.2.6 | RemoteTrigger 入口 | 远程触发本地或远端 Agent 任务 | 用户可从外部系统启动 ClawCodex 工作流 | 🔭 长期规划 → F-7 | 3-4 周 | py/API 入口、鉴权配置 |
| AR-4.2.7 | RemoteTrigger 鉴权配置 | token、签名、白名单 | 用户的远程启动不会被未授权访问 | 🔭 长期规划 → F-7 | 1 周 | py 鉴权、配置 |
| AR-4.2.8 | RemoteTrigger 审计日志 | 每次远程调用都留痕 | 用户可追溯所有远程操作 | 🔭 长期规划 → F-7 | 0.5 周 | ndjson audit |
| AR-4.2.9 | Remote Scheduled Agent | 远程 cron schedule 管理 | 用户可在远端配置定时 Agent | 🔭 长期规划 → F-22 | 4-6 周 | remote trigger 配置、server API |
| AR-4.2.10 | 远程 cron schedule 管理 | 远端增删改查 | 用户可在远端维护调度 | 🔭 长期规划 → F-22 | 1 周 | server API、JSON schedule |
| AR-4.2.11 | 远程 server API | 任务提交、状态查询 | 用户可通过 HTTP API 远程控制 | 🔭 长期规划 → F-7 | 2 周 | py API、OpenAPI schema |
| AR-4.2.12 | Away Summary 服务 | 终端失焦或长时间离开后生成摘要 | 用户回来时可快速知道 Agent 做了什么 | 📋 规划中 → F-26 | 1 周 | py service、状态采集 |
| AR-4.2.13 | 终端失焦检测 | TUI/REPL 焦点变化 | 用户离开时自动开始记录 | 📋 规划中 → F-26 | 0.5 周 | py 焦点检测、事件 |
| AR-4.2.14 | 长时间离开检测 | 配置时长 + idle | 用户离开超阈值自动开始记录 | 📋 规划中 → F-26 | 0.5 周 | py idle 检测、配置 |
| AR-4.2.15 | `/recap` Skill | 摘要命令 | 用户可一句话获取离开期间摘要 | 📋 规划中 | 1 周 | skill 代码、渲染 |
| AR-4.2.16 | Autonomy Status 汇总 | 汇总 cron、runs、orchestrator、team 状态 | 用户可用一个命令查看自动值守系统健康度 | 📋 规划中 → F-22 | 1 周 | py status、表格输出 |
| AR-4.2.17 | cron runs 状态查询 | 最近 N 次运行结果 | 用户可看到最近 cron 执行情况 | 📋 规划中 → F-22 | 0.5 周 | py 查询 CLI、表格 |
| AR-4.2.18 | orchestrator issue 状态查询 | 当前/历史 issue 处理状态 | 用户可看到 daemon 处理情况 | 📋 规划中 → F-22 | 0.3 周 | py 查询 CLI、表格 |
| AR-4.2.19 | team members 状态查询 | 团队成员状态 | 用户可看到团队工作状态 | 📋 规划中 → F-22 | 0.3 周 | py 查询 CLI、表格 |
| AR-4.2.20 | Remote Web Dashboard | Web 查看 issue、cron、team、runs | 用户可在浏览器中监督无人值守任务 | 🔭 长期规划 → F-22 | 6-8 周 | Web UI、Docker 镜像 |
| AR-4.2.21 | Web UI issue 视图 | 实时 issue 列表、详情 | 用户可看到 issue 状态实时变化 | 🔭 长期规划 → F-7 | 1 周 | Web UI、API 集成 |
| AR-4.2.22 | Web UI cron 视图 | cron 列表、运行历史 | 用户可看到定时任务状态 | 🔭 长期规划 → F-22 | 1 周 | Web UI、API 集成 |
| AR-4.2.23 | Web UI team 视图 | 团队成员、任务 | 用户可看到团队工作 | 🔭 长期规划 → F-7 | 1 周 | Web UI、API 集成 |
| AR-4.2.24 | Web UI runs 视图 | 运行历史、报告链接 | 用户可看到所有运行历史 | 🔭 长期规划 → F-7 | 1 周 | Web UI、API 集成 |
| AR-4.2.25 | Web Dashboard 鉴权 | token、会话 | 用户的 Web Dashboard 不会被未授权访问 | 🔭 长期规划 → F-7 | 0.5 周 | 鉴权中间件、配置 |

#### SR-4.3 业务 Agent 长期运行（→ FEATURE_PLAN 🔭 待补充设计）

让业务 Agent 能在后台持续运行、断线重连、并支持多个 Agent 同时被不同用户使用。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-4.3.1 | 多业务 Agent 并发 | 多个业务 Agent 同时运行 | 用户可同时运行多个专用 Agent | 📋 规划中 | 1 周 | py daemon、注册表 |
| AR-4.3.2 | 业务 Agent 命名空间 | 独立配置/记忆/工具 | 用户的多个 Agent 不会互相干扰 | 📋 规划中 | 1 周 | py 命名空间、配置 |
| AR-4.3.3 | 业务 Agent 状态查询 | 运行/暂停/错误 | 用户可看到每个 Agent 健康度 | 📋 规划中 | 0.5 周 | py 状态、CLI |
| AR-4.3.4 | 业务 Agent 暂停/恢复 | 用户可暂停某个 Agent | 用户可在不停止 daemon 的情况下暂停 Agent | 📋 规划中 | 0.5 周 | py 信号、状态机 |
| AR-4.3.5 | 业务 Agent 健康检查 | 心跳、自动重启 | 用户不会因 Agent 崩溃丢失任务 | 📋 规划中 | 1 周 | py 心跳、supervisor |
| AR-4.3.6 | 业务 Agent 升级 | 工具/skill/配置热更新 | 用户可在不重启的情况下升级 Agent | 📋 规划中 | 1 周 | py 升级、reload |
| AR-4.3.7 | 业务 Agent 日志隔离 | 每 Agent 独立日志 | 用户的 Agent 日志不会混淆 | 📋 规划中 | 0.5 周 | py 日志、路径 |
| AR-4.3.8 | 业务 Agent 配额 | 资源使用上限 | 用户的 Agent 不会耗尽资源 | 📋 规划中 | 1 周 | py 配额、监控 |
| AR-4.3.9 | 业务 Agent 用户权限 | 多用户隔离、ACL | 用户可多人共享 daemon 但权限隔离 | 📋 规划中 | 1.5 周 | py ACL、用户系统 |
| AR-4.3.10 | 业务 Agent 模板市场 | 共享/导入 Agent 模板 | 用户可一键启动标准业务 Agent | 🔭 长期规划 | 4 周 | 模板仓库、安装命令 |
| AR-4.3.11 | 业务 Agent 数据持久化 | Agent 数据/记忆持久化 | 用户重启 Agent 后上下文连续 | 📋 规划中 | 1 周 | py 持久化、storage |
| AR-4.3.12 | 业务 Agent 远程 attach | 跨设备 attach | 用户可在任何终端重连到自己的 Agent | 📋 规划中 | 2 周 | py attach、协议 |

---

## 4. 未来规划特性 (Future Features)

### 4.1 IR-5 自升级闭环（→ FEATURE_PLAN §九 CCB 对标）

ClawCodex 应能持续观察 Agent 开源社区、识别可迁移能力、自主规划、自主开发、自主验证并安全地更新自己,形成长期自我进化闭环。

#### SR-5.1 开源社区新特性雷达（→ FEATURE_PLAN 🔭 待补充设计）

持续抓取开源 Agent 项目(Claude Code、Aider、SWE-agent、OpenHands、AutoGen、CrewAI、LangGraph 等)的 release/commit/PR/issue,抽取候选特性并按分类与评分去重整理。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.1.1 | 源注册表 (Claude Code, Aider, SWE-agent, OpenHands, AutoGen, CrewAI, LangGraph) | 维护观察源 | 用户可配置 ClawCodex 关注哪些开源 Agent 项目 | 🔭 长期规划 | 0.5 周 | JSON/YAML 源配置 |
| AR-5.1.2 | JSON/YAML 源配置 | 源定义 schema | 用户可灵活添加/移除观察源 | 🔭 长期规划 | 0.3 周 | py 配置 schema |
| AR-5.1.3 | py source loader | 启动加载源配置 | 用户的源配置在启动时生效 | 🔭 长期规划 | 0.2 周 | py loader |
| AR-5.1.4 | Release notes fetcher | 抓取 release notes | 用户可收到"社区出现了哪些新 Agent 能力"的摘要 | 🔭 长期规划 | 0.5 周 | py fetcher、缓存 |
| AR-5.1.5 | Commit Watcher | 抓取 commit 列表 | 用户可看到社区的代码活动 | 🔭 长期规划 | 0.5 周 | py fetcher、缓存 |
| AR-5.1.6 | PR Watcher | 抓取 PR 列表 | 用户可看到社区的代码评审活动 | 🔭 长期规划 | 0.5 周 | py fetcher、缓存 |
| AR-5.1.7 | Issue Watcher | 抓取 issue 列表 | 用户可看到社区的讨论 | 🔭 长期规划 | 0.5 周 | py fetcher、缓存 |
| AR-5.1.8 | cron 配置 | 周期触发抓取 | 用户的抓取可定时运行 | 🔭 长期规划 → F-22 | 0.3 周 | cron 配置、集成 |
| AR-5.1.9 | 抓取缓存 | 已抓内容缓存与失效 | 用户不会重复抓取相同内容 | 🔭 长期规划 | 0.3 周 | py 缓存、TTL |
| AR-5.1.10 | Feature Extraction Pipeline | 从 release/PR/issue 抽取候选 | 用户看到结构化候选特性 | 🔭 长期规划 | 1 周 | py extractor、prompt |
| AR-5.1.11 | 候选特性抽取 | LLM/规则提取 feature | 用户可看到每个项目的特性列表 | 🔭 长期规划 | 1 周 | py 抽取、模板 |
| AR-5.1.12 | JSON feature records | 标准化候选记录 | 用户可机读候选特性 | 🔭 长期规划 | 0.5 周 | JSON schema |
| AR-5.1.13 | Feature Dedup | 跨项目/时间去重 | 用户可看到去重后的候选列表 | 🔭 长期规划 | 0.5 周 | py 去重、相似度 |
| AR-5.1.14 | Taxonomy 分类 | 工具/记忆/编排/权限/远程/UI/模型 | 用户可按能力类别浏览社区趋势 | 🔭 长期规划 | 0.5 周 | py classifier、taxonomy |
| AR-5.1.15 | classifier | LLM/规则分类器 | 用户可自动归类候选 | 🔭 长期规划 | 1 周 | py 分类器、prompt |
| AR-5.1.16 | Community Feature Digest 周报 | 周度报告 | 用户可快速了解本周社区变化 | 🔭 长期规划 | 0.5 周 | py 生成、模板 |
| AR-5.1.17 | Community Feature Digest 月报 | 月度报告 | 用户可回顾整月社区趋势 | 🔭 长期规划 | 0.5 周 | py 生成、模板 |
| AR-5.1.18 | Markdown 报告 | 可读的 Markdown 输出 | 用户可直接在文档中查看 | 🔭 长期规划 | 0.3 周 | py 渲染、模板 |
| AR-5.1.19 | 趋势评分模型 | 综合多维度评分 | 用户可看到哪些新能力值得 ClawCodex 优先吸收 | 🔭 长期规划 | 1 周 | py 评分、配置 |
| AR-5.1.20 | 热度评分 | star、引用、讨论 | 用户的评分包含热度 | 🔭 长期规划 | 0.3 周 | py 评分 |
| AR-5.1.21 | 成熟度评分 | 项目年龄、版本、用户数 | 用户的评分包含成熟度 | 🔭 长期规划 | 0.3 周 | py 评分 |
| AR-5.1.22 | 适配成本评分 | 与现有架构差距 | 用户的评分包含适配成本 | 🔭 长期规划 | 0.3 周 | py 评分 |
| AR-5.1.23 | 战略价值评分 | 与 ClawCodex 路线图契合度 | 用户的评分包含战略价值 | 🔭 长期规划 | 0.3 周 | py 评分 |
| AR-5.1.24 | 评分权重配置 | 评分权重用户可调 | 用户可自定义评分侧重点 | 🔭 长期规划 | 0.3 周 | py 配置 |

#### SR-5.2 自我规划与路线图生成（→ FEATURE_PLAN 🔭 待补充设计）

把候选特性与 ClawCodex 现有能力对比,生成符合架构边界的设计稿、依赖图、路线图草案,并保留用户审批权。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.2.1 | Capability Gap Analyzer | 对比 ClawCodex 现有能力与候选 | 用户可看到"我们缺什么、已有优势是什么" | 🔭 长期规划 | 1 周 | py analyzer、对比逻辑 |
| AR-5.2.2 | 现有能力对比 | 现有能力索引 | 用户的对比有据可依 | 🔭 长期规划 | 0.5 周 | py 索引 |
| AR-5.2.3 | 已有优势识别 | 已有能力识别 | 用户可知道 ClawCodex 已经领先的地方 | 🔭 长期规划 | 0.3 周 | py 识别、规则 |
| AR-5.2.4 | Markdown gap report | 可读的 gap 报告 | 用户可阅读能力差距 | 🔭 长期规划 | 0.3 周 | py 渲染 |
| AR-5.2.5 | Architecture Fit Checker | 检查候选是否符合 downstream 解耦边界 | 用户不用担心新特性破坏上游同步能力 | 🔭 长期规划 | 1 周 | py checker、规则 |
| AR-5.2.6 | downstream 解耦边界检查 | `clawcodex_ext/*` 范围检查 | 用户的自开发不污染上游 | 🔭 长期规划 | 0.5 周 | py 规则、检查器 |
| AR-5.2.7 | 边界规则配置 | 用户可调整规则 | 用户可灵活定义边界 | 🔭 长期规划 | 0.3 周 | py 配置 |
| AR-5.2.8 | Feature Proposal Generator | 自动生成 FEATURE_PLAN 风格设计稿 | 用户可直接审阅候选特性的设计方案 | 🔭 长期规划 | 1 周 | py 生成器、模板 |
| AR-5.2.9 | FEATURE_PLAN 风格设计稿 | 模板填充 | 用户得到统一格式的设计稿 | 🔭 长期规划 | 0.5 周 | py 模板、字段 |
| AR-5.2.10 | proposal JSON metadata | 机器可读元数据 | 用户的 proposal 可被后续步骤消费 | 🔭 长期规划 | 0.3 周 | JSON schema |
| AR-5.2.11 | Roadmap Auto-Updater | 根据评分和依赖更新 ROADMAP / FEATURE_PLAN 草案 | 用户可让 Agent 自动维护路线图草案 | 🔭 长期规划 | 0.5 周 | py doc updater |
| AR-5.2.12 | ROADMAP 草案更新 | 文档 diff | 用户的路线图被自动更新 | 🔭 长期规划 | 0.3 周 | py 文档编辑 |
| AR-5.2.13 | FEATURE_PLAN 草案更新 | 文档 diff | 用户的特性计划被自动更新 | 🔭 长期规划 | 0.3 周 | py 文档编辑 |
| AR-5.2.14 | Markdown diff | 可读的变更 | 用户可看到每次路线图变更 | 🔭 长期规划 | 0.2 周 | py diff 渲染 |
| AR-5.2.15 | Dependency Planner | 生成 IR/SR/AR 依赖图 | 用户可看到特性间依赖和推荐开发路径 | 🔭 长期规划 | 1 周 | py planner、图生成 |
| AR-5.2.16 | IR/SR/AR 依赖图 | 节点和边 | 用户的依赖关系可视化 | 🔭 长期规划 | 0.5 周 | JSON graph |
| AR-5.2.17 | 实施顺序生成 | 拓扑排序 | 用户的开发路径有最优顺序 | 🔭 长期规划 | 0.3 周 | py 拓扑 |
| AR-5.2.18 | Mermaid/Markdown 图 | 可视化输出 | 用户可在文档中直接看到 | 🔭 长期规划 | 0.3 周 | py 渲染、模板 |
| AR-5.2.19 | User Review Gate | 自规划结果必须经用户审批 | 用户保留路线图决策权,不被 Agent 自动改方向 | 🔭 长期规划 | 0.5 周 | CLI review 命令 |
| AR-5.2.20 | CLI review 命令 | approve / reject / modify | 用户可在 CLI 审批 proposal | 🔭 长期规划 | 0.3 周 | py CLI |
| AR-5.2.21 | approval JSON | 审批结果持久化 | 用户的审批可被审计 | 🔭 长期规划 | 0.2 周 | JSON store |

#### SR-5.3 自主开发 ClawCodex 自身（→ FEATURE_PLAN 🔭 待补充设计）

把已批准的 proposal 转成可执行任务,让 Orchestrator 处理 ClawCodex 自身的 issue,在隔离 workspace 中开发、测试、提交 PR,并由多 Agent 互审。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.3.1 | Self-Issue Generator | 将已批准 proposal 转成 issue | 用户批准后,Agent 自动生成可执行开发任务 | 🔭 长期规划 | 0.5 周 | Markdown issue、front matter |
| AR-5.3.2 | proposal 转 LocalTracker | 写入本地 issue | 用户的自开发可走本地流程 | 🔭 长期规划 | 0.3 周 | py 写入 |
| AR-5.3.3 | proposal 转 GitHub issue | 推送到远程 | 用户的自开发可走 GitHub 流程 | 🔭 长期规划 | 0.3 周 | py API 推送 |
| AR-5.3.4 | issue front matter | LocalTracker 元数据 | 用户的 issue 结构化 | 🔭 长期规划 | 0.2 周 | py frontmatter |
| AR-5.3.5 | tracker entry | 写入 tracker 注册表 | 用户的自开发可被追踪 | 🔭 长期规划 | 0.2 周 | JSON entry |
| AR-5.3.6 | Self-Orchestrator Runner | Orchestrator 处理 ClawCodex 自身 issue | 用户可让 ClawCodex 自动开发自己的功能分支 | 🔭 长期规划 | 0.5 周 | workflow 配置、daemon 任务 |
| AR-5.3.7 | 自开发 daemon 任务 | 复用 Orchestrator | 用户的自开发可被 daemon 处理 | 🔭 长期规划 | 0.3 周 | py daemon 集成 |
| AR-5.3.8 | 自开发 workflow 配置 | workflow YAML | 用户的自开发有明确流程 | 🔭 长期规划 | 0.2 周 | workflow YAML |
| AR-5.3.9 | Self-Workspace Isolation | 自开发任务使用独立 worktree/workspace | 用户本地工作区不被自升级任务污染 | 🔭 长期规划 | 0.5 周 | py workspace 策略、git worktree |
| AR-5.3.10 | git worktree 隔离 | 每自开发任务一个 worktree | 用户的自开发互不干扰 | 🔭 长期规划 | 0.3 周 | py worktree |
| AR-5.3.11 | workspace 策略 | 隔离清理、过期 | 用户的自开发工作区可自动管理 | 🔭 长期规划 | 0.3 周 | py 策略 |
| AR-5.3.12 | Self-Test Matrix | 根据特性类型选择测试、lint、typecheck、docs 检查 | 用户看到每次自升级都有验证矩阵 | 🔭 长期规划 | 1 周 | py test planner、workflow YAML |
| AR-5.3.13 | 测试规划器 | 根据改动类型选择测试 | 用户的测试合理 | 🔭 长期规划 | 0.5 周 | py planner |
| AR-5.3.14 | test/lint/typecheck/docs 检查矩阵 | 多维度验证 | 用户的自升级全维度验证 | 🔭 长期规划 | 0.5 周 | py 矩阵 |
| AR-5.3.15 | workflow YAML | 可配置的检查步骤 | 用户的检查流程可定制 | 🔭 长期规划 | 0.3 周 | YAML schema |
| AR-5.3.16 | Self-PR Generator | 自动提交分支并生成 PR | 用户可像 review 普通贡献一样 review Agent 自升级 PR | 🔭 长期规划 | 0.5 周 | py git_sync、PR 模板 |
| AR-5.3.17 | 自动提交分支 | git commit 自动化 | 用户的代码被自动提交 | 🔭 长期规划 | 0.2 周 | py git_sync |
| AR-5.3.18 | PR 模板 | 自升级 PR 模板 | 用户看到一致的 PR 格式 | 🔭 长期规划 | 0.2 周 | py 模板 |
| AR-5.3.19 | git_sync 集成 | 复用现有 git_sync | 用户的 PR 走同一套代码 | 🔭 长期规划 | 0.3 周 | py 集成 |
| AR-5.3.20 | Self-Review Agent Team | code reviewer、test analyzer、silent failure hunter、simplifier 等 Agent 互审 | 用户看到自升级 PR 经过多 Agent 检查 | 🔭 长期规划 | 1.5 周 | Agent configs、review reports |
| AR-5.3.21 | code reviewer agent | 业务逻辑审查 | 用户的 PR 业务逻辑被审查 | 🔭 长期规划 | 0.3 周 | Agent 配置 |
| AR-5.3.22 | test analyzer agent | 测试覆盖度分析 | 用户的 PR 测试质量被评估 | 🔭 长期规划 | 0.3 周 | Agent 配置 |
| AR-5.3.23 | silent failure hunter agent | 静默失败检测 | 用户的 PR 不会被静默失败 | 🔭 长期规划 | 0.3 周 | Agent 配置 |
| AR-5.3.24 | simplifier agent | 代码简化建议 | 用户的 PR 持续保持简洁 | 🔭 长期规划 | 0.3 周 | Agent 配置 |
| AR-5.3.25 | review reports | 多 Agent 输出汇总 | 用户可看到所有 Agent 的意见 | 🔭 长期规划 | 0.3 周 | py 汇总、报告 |
| AR-5.3.26 | Self-Fix Follow-up | 读取 PR review/CI 反馈并自动追加修复 commit | 用户只需 review,Agent 自动跟进反馈 | 🔭 长期规划 | 1 周集成 | py follow-up 配置、registry 状态 |
| AR-5.3.27 | PR review 反馈读取 | 复用 SR-3.3 follow-up | 用户的 review 反馈可被自升级消化 | 🔭 长期规划 | 0.3 周 | py 集成 |
| AR-5.3.28 | CI 反馈读取 | 复用 SR-3.3 follow-up | 用户的 CI 失败可被自升级消化 | 🔭 长期规划 | 0.3 周 | py 集成 |
| AR-5.3.29 | 修复 commit 追加 | 复用 SR-3.3 follow-up sync | 用户的修复在原 PR 上追加 | 🔭 长期规划 | 0.3 周 | py 集成 |

#### SR-5.4 自我更新、发布与回滚（→ FEATURE_PLAN 🔭 待补充设计）

把已验证的自升级版本登记为候选、构建为可安装包、分阶段发布、支持安全回滚,并自动生成发布说明。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.4.1 | Update Candidate Registry | 记录候选更新版本、PR、验证状态、风险等级 | 用户可看到哪些自升级版本可安装 | 🔭 长期规划 | 0.5 周 | JSON registry、CLI 输出 |
| AR-5.4.2 | 候选版本记录 | 版本号、PR、状态 | 用户的候选可被追踪 | 🔭 长期规划 | 0.3 周 | JSON record |
| AR-5.4.3 | PR 关联 | 候选 → PR 链接 | 用户的候选可追溯到 PR | 🔭 长期规划 | 0.2 周 | py 关联 |
| AR-5.4.4 | 验证状态 | 验证通过/失败 | 用户的候选有明确状态 | 🔭 长期规划 | 0.2 周 | py 状态机 |
| AR-5.4.5 | 风险等级 | low/medium/high | 用户可看到候选风险 | 🔭 长期规划 | 0.3 周 | py 评分 |
| AR-5.4.6 | Binary / Package Build | 构建 wheel、二进制包或镜像 | 用户可直接安装经过验证的 ClawCodex 包 | 🔭 长期规划 | 1.5 周 | wheel、binary、Docker 镜像 |
| AR-5.4.7 | wheel 构建 | Python wheel | 用户可用 pip 安装 | 🔭 长期规划 | 0.5 周 | py build、CI |
| AR-5.4.8 | 二进制包构建 | PyInstaller / Nuitka | 用户可下载单文件二进制 | 🔭 长期规划 | 1 周 | py build |
| AR-5.4.9 | Docker 镜像构建 | Docker build | 用户可拉取镜像运行 | 🔭 长期规划 | 0.5 周 | Dockerfile、CI |
| AR-5.4.10 | Staged Rollout | dev/canary/stable 分阶段启用 | 用户可先在隔离环境试用新能力 | 🔭 长期规划 | 1 周 | release channel 配置、feature flags |
| AR-5.4.11 | dev/canary/stable 渠道 | 渠道配置 | 用户可选择参与哪个渠道 | 🔭 长期规划 | 0.3 周 | py 配置 |
| AR-5.4.12 | feature flags | 细粒度功能开关 | 用户可独立启用每个新特性 | 🔭 长期规划 | 0.5 周 | py flags、配置 |
| AR-5.4.13 | `clawcodex update --candidate ...` 命令 | 安装指定候选 | 用户一条命令升级到指定候选版本 | 🔭 长期规划 | 1 周 | py CLI、安装脚本 |
| AR-5.4.14 | 安装脚本 | pip / 二进制替换 | 用户的安装可一键完成 | 🔭 长期规划 | 0.5 周 | py install |
| AR-5.4.15 | 签名校验 | 包签名、checksum | 用户的安装不被中间人攻击 | 🔭 长期规划 | 0.3 周 | py sigstore |
| AR-5.4.16 | Health Check After Update | 更新后自动运行 smoke、配置检查和回滚点创建 | 用户升级失败时不会陷入不可用状态 | 🔭 长期规划 | 1 周 | py health check、日志 |
| AR-5.4.17 | smoke 检查 | 启动基础流程 | 用户的升级不破坏核心功能 | 🔭 长期规划 | 0.3 周 | py smoke |
| AR-5.4.18 | 配置检查 | 旧配置兼容 | 用户的旧配置仍可用 | 🔭 长期规划 | 0.3 周 | py 配置迁移 |
| AR-5.4.19 | 回滚点创建 | 升级前快照 | 用户的升级可被回退 | 🔭 长期规划 | 0.3 周 | py snapshot |
| AR-5.4.20 | Safe Rollback | 保留上一版本并支持回滚 | 用户可快速撤销有问题的自升级 | 🔭 长期规划 | 1 周 | rollback metadata、CLI |
| AR-5.4.21 | 上一版本保留 | 旧版本不删除 | 用户的旧版本可被回退 | 🔭 长期规划 | 0.3 周 | py 保留策略 |
| AR-5.4.22 | rollback metadata | 版本元数据 | 用户的回滚有据可依 | 🔭 长期规划 | 0.3 周 | JSON metadata |
| AR-5.4.23 | rollback CLI | `clawcodex rollback` | 用户可一键回滚 | 🔭 长期规划 | 0.3 周 | py CLI |
| AR-5.4.24 | Release Notes Generator | 从 PR、报告、测试结果生成发布说明 | 用户可理解这次更新新增了什么、风险是什么 | 🔭 长期规划 | 0.5 周 | Markdown release notes、JSON manifest |
| AR-5.4.25 | PR 摘要生成 | 复用 SR-3.3 报告 | 用户的发布说明有 PR 摘要 | 🔭 长期规划 | 0.2 周 | py 复用 |
| AR-5.4.26 | 报告摘要生成 | 复用 SR-3.3 报告 | 用户的发布说明有运行报告 | 🔭 长期规划 | 0.2 周 | py 复用 |
| AR-5.4.27 | 测试结果摘要 | 复用 SR-5.3 测试矩阵 | 用户的发布说明有测试结果 | 🔭 长期规划 | 0.2 周 | py 复用 |
| AR-5.4.28 | Markdown release notes | 可读发布说明 | 用户可阅读发布说明 | 🔭 长期规划 | 0.3 周 | py 渲染、模板 |
| AR-5.4.29 | JSON manifest | 机读元数据 | 用户的发布说明可被工具消费 | 🔭 长期规划 | 0.2 周 | JSON schema |

#### SR-5.5 经验沉淀与策略优化（→ FEATURE_PLAN 🔭 待补充设计）

把自开发结果与失败模式沉淀为可审计的策略,持续优化 prompt、skill、工具集和路线图。

| AR 编号 | AR 名称 | 提供的组件能力 | 用户视角感知的功能 | 开发状态 | 开发工时 | 交付件 |
|---------|---------|----------------|--------------------|----------|----------|--------|
| AR-5.5.1 | Development Outcome Store | 记录每个自开发任务的成功、失败、返工原因 | 用户可追踪 Agent 自升级效率 | 🔭 长期规划 | 0.5 周 | JSONL outcome store |
| AR-5.5.2 | JSONL outcome store | 追加写、查询 | 用户的 outcome 可被审计 | 🔭 长期规划 | 0.3 周 | py store |
| AR-5.5.3 | 成功/失败/返工原因记录 | outcome schema | 用户的 outcome 有统一格式 | 🔭 长期规划 | 0.2 周 | JSON schema |
| AR-5.5.4 | outcome CLI query | 按时间/类型/Agent 查询 | 用户可查 outcome | 🔭 长期规划 | 0.3 周 | py CLI |
| AR-5.5.5 | Failure Pattern Miner | 从失败测试、review 评论、回滚中总结模式 | 用户看到 Agent 后续会避开重复错误 | 🔭 长期规划 | 1 周 | py miner、Markdown report |
| AR-5.5.6 | 失败测试模式总结 | 失败原因聚类 | 用户可看到常见失败 | 🔭 长期规划 | 0.3 周 | py 聚类 |
| AR-5.5.7 | review 评论模式总结 | 常见 review 评论 | 用户可看到常见 review 问题 | 🔭 长期规划 | 0.3 周 | py 总结 |
| AR-5.5.8 | 回滚模式总结 | 触发回滚的常见原因 | 用户可避免再次回滚 | 🔭 长期规划 | 0.3 周 | py 总结 |
| AR-5.5.9 | Markdown report | 可读模式报告 | 用户可阅读模式 | 🔭 长期规划 | 0.2 周 | py 渲染 |
| AR-5.5.10 | Strategy Memory Writer | 把稳定经验转成 memory/guide/rule 草案 | 用户可审批哪些经验进入长期策略 | 🔭 长期规划 | 1 周 | Markdown memory proposal |
| AR-5.5.11 | memory 草案生成 | 候选 memory | 用户的经验可被记忆 | 🔭 长期规划 | 0.3 周 | py 生成 |
| AR-5.5.12 | guide 草案生成 | 候选 guide | 用户的经验可被沉淀为指南 | 🔭 长期规划 | 0.3 周 | py 生成 |
| AR-5.5.13 | rule 草案生成 | 候选 rule | 用户的经验可被沉淀为规则 | 🔭 长期规划 | 0.3 周 | py 生成 |
| AR-5.5.14 | review gate | 用户审批后才生效 | 用户的策略变更可被审查 | 🔭 长期规划 | 0.3 周 | CLI review |
| AR-5.5.15 | Prompt / Skill Tuning Loop | 根据失败模式调整自开发 prompt 和 skill | 用户感知为 Agent 自开发越来越稳 | 🔭 长期规划 | 1.5 周 | prompt templates、skill configs |
| AR-5.5.16 | 失败模式→prompt 调整 | 自动生成 prompt 调整建议 | 用户的 prompt 持续优化 | 🔭 长期规划 | 0.5 周 | py 调整 |
| AR-5.5.17 | 失败模式→skill 调整 | 自动生成 skill 调整建议 | 用户的 skill 持续优化 | 🔭 长期规划 | 0.5 周 | py 调整 |
| AR-5.5.18 | prompt templates | 候选 prompt 模板 | 用户的 prompt 可被模板化 | 🔭 长期规划 | 0.3 周 | py 模板 |
| AR-5.5.19 | skill configs | 候选 skill 配置 | 用户的 skill 可被配置化 | 🔭 长期规划 | 0.3 周 | py 配置 |
| AR-5.5.20 | Tool Pruning Feedback Loop | 根据真实使用和成功率优化默认工具集 | 用户上下文更轻,常用能力更突出 | 🔭 长期规划 | 1 周集成 | py pruning policy、bundle config |
| AR-5.5.21 | 真实使用统计 | 复用 SR-2.4 统计 | 用户的工具集根据真实数据优化 | 🔭 长期规划 | 0.2 周 | py 复用 |
| AR-5.5.22 | 工具成功率 | 工具调用成功率 | 用户的低成功率工具被识别 | 🔭 长期规划 | 0.3 周 | py 统计 |
| AR-5.5.23 | 工具裁剪策略 | 根据成功率调整 bundle | 用户的工具集自动优化 | 🔭 长期规划 → F-18 | 0.3 周 | py 策略 |
| AR-5.5.24 | 工具 bundle 配置 | 自动调整 bundle | 用户的工具列表自动更新 | 🔭 长期规划 | 0.3 周 | py 配置 |
| AR-5.5.25 | Roadmap Retrospective | 每月自动生成路线图完成度和偏差报告 | 用户可审视 Agent 自规划是否可靠 | 🔭 长期规划 | 0.5 周 | Markdown retrospective |
| AR-5.5.26 | 月度完成度报告 | 计划 vs 实际 | 用户可看到自升级完成度 | 🔭 长期规划 | 0.3 周 | py 报告 |
| AR-5.5.27 | 偏差报告 | 计划偏差分析 | 用户可看到路线图偏差 | 🔭 长期规划 | 0.3 周 | py 报告 |
| AR-5.5.28 | metrics JSON | 机读指标 | 用户的指标可被下游消费 | 🔭 长期规划 | 0.2 周 | JSON schema |

---

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
    ├── SR-4.1 POS to Agent
    ├── SR-4.2 远程启动与自动值守
    └── SR-4.3 业务 Agent 长期运行
        ↓
未来规划特性:
  IR-5 自升级闭环
    ├── SR-5.1 开源社区新特性雷达
    ├── SR-5.2 自我规划与路线图生成
    ├── SR-5.3 自主开发 ClawCodex 自身
    ├── SR-5.4 自我更新、发布与回滚
    └── SR-5.5 经验沉淀与策略优化
```

### 5.1 跨层关键依赖

| 上游 SR | 解锁能力 | 说明 |
|---------|----------|------|
| SR-2.2 Cron 端到端调度 | SR-4.2 远程启动、SR-5.1 社区雷达、SR-5.2 周期自规划 | 没有真实调度,自升级只能手动触发 |
| SR-3.3 Verification Gate + Report | SR-3.3 follow-up 闭环、SR-5.3 自开发安全边界 | 自开发必须先能证明改动可验证 |
| SR-3.3 PR Review Follow-up | SR-5.3 Self-Fix Follow-up | 自升级 PR 需要自动处理 review 和 CI |
| SR-4.1 CreateAgentTool | SR-4.1 POS to Agent、SR-5.1 社区能力吸收 | 动态工具创建是把新 SDK/API 转成 Agent 能力的基础 |
| SR-4.1 POS to Agent | SR-4.3 业务 Agent 长期运行、SR-5.1 社区能力产品化 | 专业系统或外部工具可转为长期 Agent |
| SR-1.5 Remote Bridge + Attach | SR-4.2 远程值守、SR-5.4 自升级运行环境 | 自主运行不能依赖单个前台终端 |
| SR-3.4 A2A 协议 | SR-5.3 Self-Review Agent Team | 多 Agent 互审需要协议化协作 |
| SR-2.4 Usage Stats + SR-5.5 Outcome Store | SR-5.5 策略优化和工具裁剪 | 没有数据就无法自我优化 |

---

## 6. 时间节奏建议

### 6.1 近期:底层特性收敛与场景特性核心闭环

| 优先级 | 交付目标 | 涉及 SR/AR |
|--------|----------|------------|
| P0 | SR-2.2 Cron 端到端收敛 | AR-2.2.9、AR-2.2.10、AR-2.2.11、AR-2.2.12、AR-2.2.13 |
| P0 | SR-3.3 PR review follow-up 闭环 | AR-3.3.11 ~ AR-3.3.22 |
| P1 | SR-4.1 CreateAgentTool MVP | AR-4.1.1 ~ AR-4.1.13 |
| P1 | SR-1.4 REPL/TUI/headless 一致化 | AR-1.4.9、AR-2.2.9、SR-1.4 hook |
| P1 | SR-1.4 Auto 权限模式 | AR-1.4.10、AR-1.4.11、AR-1.4.12 |
| P2 | SR-2.3 稳定性补强 | AR-2.3.1、AR-2.3.2、AR-2.3.3 ~ AR-2.3.5、AR-2.3.12 |

### 6.2 中期:业务 Agent 与远程值守

| 优先级 | 交付目标 | 涉及 SR/AR |
|--------|----------|------------|
| P0 | SR-4.1 POS to Agent MVP | AR-4.1.14 ~ AR-4.1.25 |
| P1 | SR-4.3 业务 Agent 长期运行 | AR-4.3.1 ~ AR-4.3.12 |
| P1 | SR-4.2 Autonomy Status | AR-4.2.16 ~ AR-4.2.19 |
| P1 | SR-4.2 Away Summary | AR-4.2.12 ~ AR-4.2.15 |
| P2 | SR-3.4 A2A 协议雏形 | AR-3.4.17 ~ AR-3.4.21 |
| P2 | SR-4.2 Remote Trigger MVP | AR-4.2.6 ~ AR-4.2.11 |

### 6.3 长期:自升级闭环 

| 优先级 | 交付目标 | 涉及 SR/AR |
|--------|----------|------------|
| P0 | SR-5.1 Community Feature Radar | AR-5.1.1 ~ AR-5.1.24 |
| P0 | SR-5.2 Self-Planning Gate | AR-5.2.1 ~ AR-5.2.21 |
| P1 | SR-5.3 Self-Development Loop | AR-5.3.1 ~ AR-5.3.29 |
| P1 | SR-5.3 Self-Review Loop | AR-5.3.20 ~ AR-5.3.29 |
| P2 | SR-5.4 Self-Update | AR-5.4.1 ~ AR-5.4.29 |
| P2 | SR-5.5 Self-Learning | AR-5.5.1 ~ AR-5.5.28 |

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
| POS 能转换成可运行业务 Agent | SR-4.1 输入一个 OpenAPI 或方法列表,生成 Agent JSON、Skill、工具并完成一次真实调用 |
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
2. **启动 PR Review Feedback 闭环**:先实现统一 `PullRequestFeedback` 模型(AR-3.3.11)和 GitHub/Gitee/GitCode feedback API(AR-3.3.12~15),再接入 Orchestrator poller(AR-3.3.16)。
3. **并行推进 CreateAgentTool MVP**:先交付安全 spec/validator/factory/persistence(AR-4.1.1~13),为 SR-4.1 POS to Agent 和长期社区能力吸收打基础。
4. **补齐自动值守观测入口**:把 cron runs(AR-4.2.17)、orchestrator issue(AR-4.2.18)、team members(AR-4.2.19)、verification report 汇总到 SR-4.2 Autonomy Status 统一输出。
5. **为未来规划特性做最小闭环试点**:先以"每周生成 Agent 社区新特性 digest(AR-5.1.16) + 手动审批 proposal(AR-5.2.19~21)"为最小可用版本,不直接自动改代码。

---

## 附录:AR 数量统计

| 类别 | 抽象需求 (IR) | 系统需求 (SR) | 组件需求 (AR) |
|------|---------------|---------------|---------------|
| 底层特性 | 2 (IR-1, IR-2) | 9 (SR-1.1 ~ SR-1.5, SR-2.1 ~ SR-2.4) | 96 (会话 12 + 工具 15 + 模型 12 + 权限 12 + 后台 14 + 进度 10 + 调度 15 + 稳定 12 + 工具策略 11) |
| 场景特性 | 2 (IR-3, IR-4) | 7 (SR-3.1 ~ SR-3.4, SR-4.1 ~ SR-4.3) | 141 (Issue 16 + 协同 20 + 报告 22 + 多 Agent 21 + POS 25 + 远程 25 + 业务运行 12) |
| 未来规划特性 | 1 (IR-5) | 5 (SR-5.1 ~ SR-5.5) | 131 (雷达 24 + 规划 21 + 自开发 29 + 自更新 29 + 自学习 28) |
| **合计** | **5** | **21** | **368** |

每个 IR 下挂 4~5 个 SR,每个 SR 下挂 10+ 个 AR。

---
