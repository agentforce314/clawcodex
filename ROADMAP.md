# ClawCodex 功能路线图

> 文档路径: `ROADMAP.md`
> 基于: `docs/FEATURE_PLAN.md` (v1.6, updated 2026-05-27)
> 版本: v1.0
> 创建日期: 2026-05-28

---

## 一、特性分类汇总

### 1.1 已完成特性（✅）

#### 核心系统

| 类别 | 特性 | 状态 | 说明 |
|------|------|------|------|
| Agent 系统 | Agent 执行循环 | ✅ | 四级权限模型、Subagent 隔离 |
| Agent 系统 | Fork Subagent | ✅ | 创建独立会话的 sub-agent |
| Agent 系统 | Resume Agent | ✅ | 从断点恢复 sub-agent |
| Agent 系统 | Foreground Promotion | ✅ | 后台 agent 提升到前台 |
| Agent 系统 | Session 管理 | ✅ | 会话状态管理 |
| Agent 系统 | Transcript | ✅ | 对话转录本管理 |
| Agent 系统 | Prompt 构建 | ✅ | 系统 Prompt 组装 |
| Agent 系统 | Agent 定义系统 | ✅ | Agent 类型、工具、配置定义 |
| Agent 系统 | Agent 记忆作用域 | ✅ | 按需加载不同作用域的记忆 |
| Provider 层 | Multi-Provider 支持 | ✅ | Anthropic/OpenAI/GLM/MiniMax/DeepSeek/OpenRouter + LiteLLM |

#### 工具系统

| 类别 | 特性 | 状态 | 说明 |
|------|------|------|------|
| 工具系统 | 42 个内置工具 | ✅ | Read/Write/Edit/Glob/Grep/Bash 等完整工具集 |
| 工具系统 | MCP 扩展 | ✅ | Stdio/HTTP/SSE/WebSocket/OAuth |
| 工具系统 | 工具按需加载 | ✅ | Bare/Default/ClawCodex/All 四种模式 |
| 工具系统 | ToolSearch | ✅ | TF-IDF 语义工具搜索 |
| 工具系统 | ExecuteExtraTool | ✅ | 延迟工具执行 |

#### 交互系统

| 类别 | 特性 | 状态 | 说明 |
|------|------|------|------|
| TUI/REPL | Textual TUI | ✅ | 完整的 TUI 界面 |
| TUI/REPL | REPL Core | ✅ | prompt_toolkit + Rich 实现 |
| TUI/REPL | 双向切换 | ✅ | /tui 和 /repl 命令 |
| TUI/REPL | Shift+Tab 权限循环 | ✅ | default→acceptEdits→plan→bypass |
| 权限系统 | 四级权限模型 | ✅ | default/acceptEdits/plan/bypassPermissions |
| 权限系统 | Bash AST 解析 | ✅ | tree-sitter-bash 替代自建解析器 |

#### Orchestrator 自主模式

| 类别 | 特性 | 状态 | 说明 |
|------|------|------|------|
| Orchestrator | 多 Tracker 支持 | ✅ | Linear/GitHub/Gitee/GitCode |
| Orchestrator | CLI 集成 | ✅ | `clawcodex orchestrator` 统一入口 |
| Orchestrator | 重试队列+退避 | ✅ | 指数退避重试 |
| Orchestrator | Issue State 前置检查 | ✅ | 非 active 状态跳过 |
| Orchestrator | 已有 PR 跳过后续 | ✅ | 避免重复处理 |
| Orchestrator | 本地 Issue 注册表 | ✅ | 持久化 issue→commit→PR 映射 |
| Orchestrator | Clarification 流程 | ✅ | 三通道ClarificationQueue |
| Orchestrator | CLI noun-verb 结构 | ✅ | Phase O1-O8 完成 |

#### 后台与恢复

| 类别 | 特性 | 状态 | 说明 |
|------|------|------|------|
| 后台运行 | BackgroundState | ✅ | 进程级后台信号管理 |
| 后台运行 | TailFollower | ✅ | 实时读取 JSONL 增量 |
| 后台运行 | SessionWatcher | ✅ | 目录变更监控 |
| 后台运行 | Ctrl+B 后台化 | ✅ | TUI 路径已完成 |
| 恢复 | Session.resume_with_tail() | ✅ | TailFollower 集成 |
| 恢复 | graceful_shutdown | ✅ | SIGTSTP 处理 |

#### Bridge 系统

| 类别 | 特性 | 状态 | 说明 |
|------|------|------|------|
| Bridge | Phase 8 多Session Daemon | ✅ | bridge_main.py 多会话轮询 |
| Bridge | Phase 5 远程桥接 | ✅ | remote_bridge_core.py |
| Bridge | Phase 4 子CLI生成 | ✅ | session_runner.py |
| Bridge | Phase 11 REPL桥接 | ✅ | repl_bridge.py |
| Bridge | Phase 3 HTTP客户端 | ✅ | bridge_api.py |

#### 开源替代

| 类别 | 特性 | 状态 | 说明 |
|------|------|------|------|
| 配置系统 | Pydantic-settings | ✅ | ~220 行代码减少 |
| Frontmatter | python-frontmatter | ✅ | ~80 行代码减少 |
| Bash AST | tree-sitter-bash | ✅ | ~1,400 行代码减少 |
| Git操作 | GitPython | ✅ | ~200 行代码减少 |
| Hook系统 | Pluggy | ✅ | ~1,000 行代码减少 |
| 结构化输出 | Outlines | ✅ | ~200 行代码减少 |

#### 增强功能

| 类别 | 特性 | 状态 | 说明 |
|------|------|------|------|
| Token计数 | Advisor增强 | ✅ | max_history: 100→2000 |
| Token计数 | Provider Token追踪 | ✅ | 统一 token 计数接口 |
| 进度汇报 | ProgressReportTool | ✅ | 阶段性进度汇报 |
| 团队管理 | TeamCreate/TeamDelete | ✅ | 基础团队工具 |
| 团队管理 | members数组支持 | ✅ | 测试覆盖完成 |
| Team工具 | TaskInspect | ✅ | Manager 查询 Worker |
| Team工具 | TaskDirectives | ✅ | Manager 指令 Worker |

---

### 1.2 待实施特性（⏳）

#### P0 优先级（关键基础设施）

| 特性 | 依赖 | 说明 |
|------|------|------|
| Cron System 执行引擎 | 无 | 完整 cron 调度系统，含分布式锁、Jitter |
| Provider 层 LiteLLM 集成 | 无 | 适配器已完成，待集成到工具系统 |
| Skills System Extension | 无 | 仿 tool_system_ext 模式构建独立技能层 |

#### P1 优先级（核心功能）

| 特性 | 依赖 | 说明 |
|------|------|------|
| Auto 模式 (TRANSCRIPT_CLASSIFIER) | 无 | LLM 自动判断权限模式 |
| Away-Summary（离开摘要） | 无 | 终端失焦5分钟后自动生成摘要 |
| REPL 模式 Ctrl+B 后台运行 | TUI Ctrl+B 已完成 | F-33，BackgroundEscape 信号机制 |
| CreateAgentTool 动态工具创建 | 无 | Agent 根据三方CLI/API规范动态创建工具 |
| POS to Agent 转化模式 | CreateAgentTool | 工作流拆解为 Agent 架构 |
| 业务 Agent 长期使用+重连 | POS to Agent | Daemon 模式 + attach 协议 |

#### P2 优先级（增强功能）

| 特性 | 依赖 | 说明 |
|------|------|------|
| MCP 资源缓存 | MCP 已完成 | 减少重复获取 |
| MCP Batch 工具调用 | MCP 已完成 | 批量工具执行 |
| MCP Progress 通知 | MCP 已完成 | 长任务进度报告 |
| 结构化输出增强 (Outlines) | Outlines 适配器 | Token预算分析、工具调用决策 |
| sessionStorage 容量限制 | 无 | 防止OOM |
| cacheWarning 容量限制 | 无 | 防止内存泄漏 |
| 工具/Skill 调用统计 | 无 | JSONL 追加日志 |
| 基于使用频率的工具裁剪 | 工具统计 | 低频工具自动隐藏 |

#### P3 优先级（高级功能）

| 特性 | 依赖 | 说明 |
|------|------|------|
| Voice Mode | 无 | 语音交互模式 |
| Computer Use | 无 | 计算机控制模式 |
| Chrome Use | 无 | 浏览器自动化 |
| Remote Control (Docker+WebUI) | 无 | 远程控制 |
| ACP/Zed/Cursor 集成 | 无 | IDE 集成 |
| Langfuse 监控 | 无 | 可观测性 |
| Feature Flags | 无 | 动态配置 |

---

## 二、特性关联分析

### 2.1 核心依赖关系图

```
                                    ┌─────────────────┐
                                    │  Provider Layer │
                                    │   (LiteLLM)     │
                                    └────────┬────────┘
                                             │
                            ┌────────────────┼────────────────┐
                            ▼                ▼                ▼
                     ┌────────────┐   ┌─────────────┐   ┌─────────────┐
                     │  Tool Sys  │   │ Skills Sys   │   │  Cron Sys   │
                     │ Extension  │   │ Extension    │   │  Engine     │
                     └─────┬──────┘   └──────┬──────┘   └──────┬──────┘
                           │                │                │
           ┌───────────────┼────────────────┼────────────────┘
           ▼               ▼                ▼
    ┌────────────────────────────────────────────────────┐
    │                  Agent System                      │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
    │  │  Core    │  │ Memory   │  │ Session  │          │
    │  │  Loop    │  │ Scopes   │  │ Mgmt     │          │
    │  └──────────┘  └──────────┘  └──────────┘          │
    └────────────────────────────────────────────────────┘
           │               │                │
           ▼               ▼                ▼
    ┌────────────────────────────────────────────────────┐
    │              Orchestrator Layer                     │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
    │  │  Linear  │  │ GitHub   │  │Clarify   │          │
    │  │  Adapter │  │ Adapter  │  │ Queue    │          │
    │  └──────────┘  └──────────┘  └──────────┘          │
    └────────────────────────────────────────────────────┘
           │
           ▼
    ┌────────────────────────────────────────────────────┐
    │              Interaction Layer                      │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
    │  │   TUI    │  │  REPL    │  │ Dashboard │          │
    │  └──────────┘  └──────────┘  └──────────┘          │
    └────────────────────────────────────────────────────┘
```

### 2.2 特性分组

#### 群组 A：基础设施（无外部依赖）

| 特性 | 说明 |
|------|------|
| Cron System 执行引擎 | 调度基础设施 |
| Skills System Extension | 技能系统解耦 |
| Provider LiteLLM 集成 | 多模型统一抽象 |

#### 群组 B：Agent 核心（依赖基础设施）

| 特性 | 依赖 |
|------|------|
| CreateAgentTool | Provider + Tool System |
| POS to Agent | CreateAgentTool |
| Auto Mode | Agent System + Permission |
| Away-Summary | Agent System + Session |
| Memory Scopes | Agent System |

#### 群组 C：编排层（依赖 Agent 核心）

| 特性 | 依赖 |
|------|------|
| Orchestrator 增强 | Agent + Tracker |
| Manager/Worker 通信 | Agent + TaskInspect/Directives |
| Clarification 三通道 | Orchestrator + Tracker |

#### 群组 D：交互层（依赖下层所有）

| 特性 | 依赖 |
|------|------|
| REPL Ctrl+B | Agent + Background Runner |
| TUI 增强 | Agent + REPL |
| Dashboard LiveView | Orchestrator + Agent |

### 2.3 特性关联矩阵

| 特性 A | 特性 B | 关系 | 说明 |
|--------|--------|------|------|
| Cron System | Skills Extension | 依赖 | /loop 命令依赖 cron |
| Cron System | Orchestrator | 依赖 | orchestrator 可使用 cron 调度 |
| Provider LiteLLM | CreateAgentTool | 依赖 | 动态创建工具需要统一模型接口 |
| Tool System Extension | Skills Extension | 平行 | 类似架构的不同组件 |
| CreateAgentTool | POS to Agent | 依赖 | POS 转化依赖工具创建能力 |
| Background Runner | REPL Ctrl+B | 依赖 | REPL 复用 background_runner |
| TaskInspect | TaskDirectives | 依赖 | Manager Agent 工具组合 |
| ClarificationQueue | TrackerAdapter | 依赖 | 评论接口需要 tracker |

---

## 三、行业对比分析

### 3.1 竞品特性对比

| 特性 | ClawCodex | Claude Code Best | Aider | SWE-agent |
|------|-----------|------------------|-------|-----------|
| 多Provider支持 | ✅ 7+ Provider | ❌ | ✅ | ❌ |
| Orchestrator 自主模式 | ✅ | ❌ | ❌ | ❌ |
| 多Session Daemon | ✅ Phase 8-11 | ❌ | ❌ | ❌ |
| 后台运行+恢复 | ✅ Fork-Continue | ✅ 类似 | ❌ | ❌ |
| Cron 调度引擎 | ⏳ P0 | ✅ | ❌ | ❌ |
| REPL Ctrl+B | ⏳ F-33 | ✅ | ❌ | ❌ |
| Away-Summary | ⏳ P1 | ✅ | ❌ | ❌ |
| Auto Mode | ⏳ P2 | ✅ | ❌ | ❌ |
| 动态工具创建 | ⏳ P1 | ✅ | ❌ | ❌ |
| Manager/Worker | ✅ TaskInspect/Directives | ❌ | ❌ | ❌ |
| 三通道 Clarification | ✅ | ❌ | ❌ | ❌ |
| Skills System Extension | ⏳ P0 | ✅ | ❌ | ❌ |
| MCP 完整支持 | ✅ | ✅ | 部分 | ❌ |
| Team members | ✅ | ✅ | ❌ | ❌ |
| Voice Mode | ❌ | ✅ | ❌ | ❌ |
| Computer Use | ❌ | ✅ | ❌ | ❌ |

### 3.2 关键发现

1. **ClawCodex 领先领域**:
   - 多 Provider 支持（国内特殊需求）
   - Orchestrator 自主模式（唯一实现工作流自动化的 CLI）
   - 多 Session Daemon 架构
   - 三通道 Clarification 机制
   - Manager/Worker 观察通信

2. **Claude Code Best 领先领域**:
   - Cron 系统执行引擎（生产级）
   - Away-Summary 贴心功能
   - Auto Mode 智能权限
   - Voice/Computer Use 高级特性

3. **行业趋势**:
   - 后台 Daemon 模式成为标配
   - Cron 调度是自动化核心
   - 多 Agent 协作（Manager/Worker）正在成为标准模式
   - 动态工具创建是 Meta Agent 能力的关键

### 3.3 竞争定位

| 维度 | ClawCodex 定位 |
|------|---------------|
| 目标用户 | 国内开发者，需要多 Provider 支持 |
| 核心优势 | Orchestrator 自主模式 + 多 Tracker 支持 |
| 差异化 | 三通道 Clarification + Manager/Worker 通信 |
| 短板 | Cron/Auto Mode/Away-Summary 等生产功能 |
| 机会 | 将 Claude Code Best 的功能移植到国内环境 |

---

## 四、实现优先级

### 4.1 优先级矩阵

```
        高价值 ────────────────────────────── 低价值
高优先级 │ P0: Cron系统  │ P0: LiteLLM集成 │ P1: Skills Ext  │
         │ P0: SkillsExt │                  │                │
─────────┼───────────────┼──────────────────┼────────────────┤
低优先级 │ P1: Auto Mode  │ P1: Away-Summary │ P1: REPL Ctrl+B│
         │ P1: CreateAgent│ P2: 结构化输出   │ P3: Voice Mode │
         │ P2: 容量限制   │ P2: 工具统计     │               │
```

### 4.2 优先级详细说明

#### P0 - 必须实现（基础设施）

| 特性 | 优先级 | 理由 |
|------|--------|------|
| Cron System 执行引擎 | P0 | 1. orchestrator 调度依赖<br>2. /loop 等命令依赖<br>3. 竞品标配功能<br>4. 避免多进程重复执行 |
| Skills System Extension | P0 | 1. 解耦上游更新<br>2. 仿 tool_system_ext 成功模式<br>3. 降低维护成本 |
| Provider LiteLLM 集成 | P0 | 1. 适配器已完成<br>2. 统一 100+ 模型<br>3. 减少重复代码 |

#### P1 - 核心功能（用户体验关键）

| 特性 | 优先级 | 理由 |
|------|--------|------|
| REPL Ctrl+B 后台运行 | P1 | 1. TUI 已完成，REPL 缺失<br>2. 用户期待对称体验<br>3. 实现复杂度低 |
| Away-Summary | P1 | 1. 贴心功能提升体验<br>2. 竞品有类似功能<br>3. 实现复杂度中等 |
| Auto Mode | P1 | 1. 减少交互疲劳<br>2. 长任务场景必需<br>3. 技术方案清晰 |
| CreateAgentTool | P1 | 1. Meta Tool 能力<br>2. POS to Agent 依赖<br>3. 工具生态扩展 |
| POS to Agent | P1 | 2. 依赖 CreateAgentTool<br>2. 高级工作流能力<br>3. 差异化功能 |
| Manager/Worker 通信 | P1 | 1. TaskInspect/Directives 已完成<br>2. 完整多 Agent 协作<br>3. Orchestrator 必需 |

#### P2 - 增强功能（稳定性+性能）

| 特性 | 优先级 | 理由 |
|------|--------|------|
| 结构化输出增强 | P2 | 1. 适配器已完成<br>2. Token 预算分析必需<br>3. 提升决策质量 |
| sessionStorage 容量限制 | P2 | 1. 防止 OOM<br>2. daemon 长期运行必需<br>3. 实现简单 |
| cacheWarning 容量限制 | P2 | 同上 |
| 工具/Skill 统计 | P2 | 1. 数据驱动优化<br>2. 使用频率裁剪依赖<br>3. 实现简单 |
| MCP 增强 | P2 | 1. 资源缓存减少 API 调用<br>2. Batch 调用提升效率<br>3. 成熟功能 |

#### P3 - 高级功能（未来方向）

| 特性 | 优先级 | 理由 |
|------|--------|------|
| Voice Mode | P3 | 1. 技术复杂度高<br>2. 用户群体有限<br>3. 可后期集成 |
| Computer Use | P3 | 同上 |
| Remote Control | P3 | 1. 安全性考虑<br>2. 实现复杂度高<br>3. Docker/WebUI 依赖 |
| Feature Flags | P3 | 1. 运维灵活性<br>2. GrowthBook 依赖<br>3. 非核心功能 |

---

## 五、路线图（2026 Q2-Q4）

### 5.1 阶段划分

```
Q2 2026 ─────────────────────────────► Q3 2026 ─────────────────────────────► Q4 2026
├── Phase 1: 基础设施补全              ├── Phase 2: 核心功能增强              ├── Phase 3: 高级功能落地
│   ├── Cron System (P0)              │   ├── Auto Mode (P1)                 │   ├── Voice Mode (P3)
│   ├── Skills Extension (P0)         │   ├── Away-Summary (P1)             │   ├── Computer Use (P3)
│   └── LiteLLM 集成 (P0)             │   ├── REPL Ctrl+B (P1)             │   └── Remote Control (P3)
│                                     │   ├── CreateAgentTool (P1)         │
│                                     │   └── POS to Agent (P1)           │
│                                     │
│                                     ├── Phase 2.5: 稳定性增强
│                                     │   ├── sessionStorage 容量 (P2)
│                                     │   ├── cacheWarning 容量 (P2)       │
│                                     │   └── MCP 增强 (P2)
│                                     │
└─────────────────────────────────────┴────────────────────────────────────┴────────────────
```

### 5.2 Phase 1: 基础设施补全（Q2 2026，6-8 周）

**目标**: 完成三个基础设施组件，为上层功能提供支撑

#### 1.1 Cron System 执行引擎

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M1.1 | cron_parser.py - 5字段表达式解析 | 1周 | 无 |
| M1.2 | cron_tasks.py - 任务存储 CRUD | 0.5周 | M1.1 |
| M1.3 | cron_tasks_lock.py - 分布式锁 | 0.5周 | M1.1 |
| M1.4 | cron_scheduler.py - 执行引擎 | 1.5周 | M1.2, M1.3 |
| M1.5 | skills.py - /loop 命令集成 | 0.5周 | M1.4 |
| M1.6 | autonomy_runs.py - 任务入队 | 1周 | M1.4 |
| M1.7 | 测试 + 集成 | 1周 | M1.1-M1.6 |

**交付物**: 完整 cron 调度系统，支持分布式锁、Jitter 抖动、任务过期

#### 1.2 Skills System Extension

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M2.1 | skills_ext/ 目录结构 | 0.5周 | 无 |
| M2.2 | registry_ext.py - SkillRegistryExt | 1周 | 无 |
| M2.3 | bundles.py - SKILL_BUNDLES | 0.5周 | M2.2 |
| M2.4 | agent_config.py - AgentSkillConfig | 0.5周 | M2.2 |
| M2.5 | paths.py - clawcodex 特定路径 | 0.5周 | M2.2 |
| M2.6 | 迁移上层调用点 | 1周 | M2.1-M2.5 |
| M2.7 | 测试 + 集成 | 1周 | M2.6 |

**交付物**: 独立于上游的技能系统扩展层，tool_system_ext 对称架构

#### 1.3 Provider LiteLLM 集成

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M3.1 | _litellm_adapter.py 集成到 provider 层 | 1周 | 适配器已完成 |
| M3.2 | 统一 tool_call 接口 | 0.5周 | M3.1 |
| M3.3 | 回退策略（LiteLLM 失败→直连） | 0.5周 | M3.1 |
| M3.4 | 测试（100+ 模型覆盖） | 1.5周 | M3.1-M3.3 |

**交付物**: 统一 100+ 模型支持，故障自动回退

---

### 5.3 Phase 2: 核心功能增强（Q3 2026，8-10 周）

**目标**: 完善核心用户体验，完成 Manager/Worker 通信

#### 2.1 REPL Ctrl+B 后台运行（F-33）

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M4.1 | background_escape.py - BackgroundEscape 异常 | 0.5天 | 无 |
| M4.2 | live_status.py - on_background 参数 | 1天 | M4.1 |
| M4.3 | core.py - chat() direct stream 路径 | 1天 | M4.2 |
| M4.4 | core.py - chat() engine 路径 | 1天 | M4.2 |
| M4.5 | core.py - 空闲态 Ctrl+B 绑定 | 0.5天 | M4.1 |
| M4.6 | 端到端测试 | 1天 | M4.1-M4.5 |

**交付物**: REPL 模式下 Ctrl+B 后台运行功能

#### 2.2 Away-Summary

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M5.1 | services/away_summary.py | 1周 | 无 |
| M5.2 | hooks/use_away_summary.py | 0.5周 | M5.1 |
| M5.3 | commands/recap.py - /recap 命令 | 0.5周 | M5.1 |
| M5.4 | 消息类型 + 渲染 | 0.5周 | M5.1-M5.3 |
| M5.5 | 测试 | 1周 | M5.1-M5.4 |

**交付物**: 终端失焦5分钟后自动生成摘要，/recap 命令

#### 2.3 Auto Mode (TRANSCRIPT_CLASSIFIER)

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M6.1 | permissions/classifier.py - TRANSCRIPT_CLASSIFIER | 1.5周 | 无 |
| M6.2 | permissions/cycle.py - canCycleToAuto | 0.5周 | M6.1 |
| M6.3 | agent/run_agent.py 集成 | 1周 | M6.1, M6.2 |
| M6.4 | 分类结果缓存 | 0.5周 | M6.3 |
| M6.5 | 测试 | 1周 | M6.1-M6.4 |

**交付物**: LLM 自动判断权限模式，减少交互疲劳

#### 2.4 CreateAgentTool 动态工具创建

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M7.1 | tool_authoring/spec.py - AgentToolSpec | 1周 | 无 |
| M7.2 | tool_authoring/validators.py | 1周 | M7.1 |
| M7.3 | call_handlers/ (bash/http/python) | 1.5周 | M7.2 |
| M7.4 | factory.py + registry_ext.py | 1周 | M7.3 |
| M7.5 | create_agent_tool.py | 1周 | M7.4 |
| M7.6 | 持久化机制 | 0.5周 | M7.5 |
| M7.7 | 测试 | 1.5周 | M7.1-M7.6 |

**交付物**: Agent 可根据三方 CLI/API 规范动态创建工具

#### 2.5 Manager/Worker 通信完整化

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M8.1 | queue_pending_message priority 参数 | 0.5周 | TaskInspect/Directives 已完成 |
| M8.2 | drain_pending_messages 按优先级消费 | 0.5周 | M8.1 |
| M8.3 | 工具可见性过滤（仅 Manager） | 0.5周 | M8.2 |
| M8.4 | always_allow_rules + worker_permission_mode | 1周 | M8.3 |
| M8.5 | Plan Mode 审批流程 | 1周 | M8.4 |
| M8.6 | 测试 + 联调 | 1.5周 | M8.1-M8.5 |

**交付物**: 完整的多 Agent 协作通信机制

---

### 5.4 Phase 2.5: 稳定性增强（Q3 2026 并行，3-4 周）

#### 2.5.1 sessionStorage 容量限制

| 里程碑 | 内容 | 工时 |
|--------|------|------|
| M9.1 | MAX_CACHED_SESSION_FILES = 200 | 0.5天 |
| M9.2 | LRU 淘汰逻辑 | 0.5天 |
| M9.3 | 测试 | 1天 |

#### 2.5.2 cacheWarning 容量限制

| 里程碑 | 内容 | 工时 |
|--------|------|------|
| M10.1 | MAX_SOURCE_ENTRIES = 50 | 0.5天 |
| M10.2 | LRU 淘汰逻辑 | 0.5天 |
| M10.3 | 测试 | 1天 |

#### 2.5.3 MCP 增强

| 里程碑 | 内容 | 工时 |
|--------|------|------|
| M11.1 | 资源缓存机制 | 1周 |
| M11.2 | Batch 工具调用 | 1.5周 |
| M11.3 | Progress 通知 | 1周 |
| M11.4 | 测试 | 1周 |

---

### 5.5 Phase 3: 高级功能（Q4 2026，6-8 周）

#### 3.1 POS to Agent 转化

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M12.1 | pos_converter/sdk_parser.py | 1.5周 | CreateAgentTool |
| M12.2 | pos_converter/skill_grouper.py | 1周 | M12.1 |
| M12.3 | pos_converter/agent_builder.py | 1周 | M12.2 |
| M12.4 | /convert-pos-to-agent skill | 1周 | M12.3 |
| M12.5 | 测试 | 1.5周 | M12.1-M12.4 |

#### 3.2 业务 Agent 长期使用 + 重连

| 里程碑 | 内容 | 工时 | 依赖 |
|--------|------|------|------|
| M13.1 | agent_persistence.py | 1周 | POS to Agent |
| M13.2 | agent_loader.py | 0.5周 | M13.1 |
| M13.3 | attach.py 协议 | 1周 | M13.2 |
| M13.4 | daemon 模式支持 | 1周 | M13.3 |
| M13.5 | 测试 | 1.5周 | M13.1-M13.4 |

#### 3.3 Voice Mode / Computer Use（可选）

根据 Phase 1-2 用户反馈决定是否继续

---

## 六、风险与依赖

### 6.1 技术风险

| 风险 | 影响 | 缓解方案 |
|------|------|----------|
| Cron System 分布式锁复杂度 | 高 | 使用已有 watchdog + 原子文件创建 |
| CreateAgentTool 安全验证 | 高 | 白名单机制 + 参数化模板 |
| REPL Ctrl+B 与上游合并冲突 | 中 | BackgroundEscape 异常解耦设计 |
| LiteLLM 故障回退延迟 | 中 | 异步回退 + 缓存 |

### 6.2 资源依赖

| 资源 | 依赖特性 | 说明 |
|------|---------|------|
| GrowthBook SDK | Feature Flags | 用于动态配置 |
| tree-sitter-bash | Bash AST | 已替代 |
| python-frontmatter | Frontmatter | 已替代 |
| GitPython | Git 操作 | 已替代 |
| Pluggy | Hook 系统 | 已替代 |
| Outlines | 结构化输出 | 已替代 |
| watchdog | Cron System | 新增依赖 |
| psutil | Cron System | 新增依赖 |

---

## 七、关键决策点

### 7.1 Phase 1 决策

**Q2 末评审**:
1. Cron System 是否需要 GrowthBook 动态配置？
2. Skills Extension 是否完全解耦还是渐进迁移？

### 7.2 Phase 2 决策

**Q3 初评审**:
1. Auto Mode 的 LLM 分类器是否使用专门的小模型？
2. CreateAgentTool 的 bash 命令白名单是否足够？

### 7.3 Phase 3 决策

**Q3 末评审**:
1. Voice Mode 技术方案（需要语音模型）
2. Computer Use 是否必要（浏览器自动化复杂度高）

---

## 八、总结

### 8.1 路线图概览

```
Phase 1 (Q2 2026):  基础设施补全
  └─ Cron System + Skills Extension + LiteLLM 集成

Phase 2 (Q3 2026):  核心功能增强
  ├─ REPL Ctrl+B + Away-Summary + Auto Mode
  ├─ CreateAgentTool + POS to Agent
  └─ Manager/Worker 通信完整化

Phase 2.5 (Q3 2026): 稳定性增强
  └─ 容量限制 + MCP 增强

Phase 3 (Q4 2026):  高级功能
  └─ POS to Agent + 业务 Agent 长期使用
```

### 8.2 成功标准

| 阶段 | 成功标准 |
|------|----------|
| Phase 1 | Cron 可调度任务，Skills 可扩展，LiteLLM 统一 100+ 模型 |
| Phase 2 | REPL/TUI 对称体验，Manager/Worker 协作完整 |
| Phase 3 | POS 工作流可转化，Agent 长期运行稳定 |

### 8.3 下一步行动

1. **立即启动**: Cron System 执行引擎（Phase 1 核心）
2. **并行准备**: Skills System Extension 设计评审
3. **资源协调**: 确定 Phase 1-2 开发和测试人力

---

*文档创建时间: 2026-05-28*
*基于 FEATURE_PLAN.md v1.6*