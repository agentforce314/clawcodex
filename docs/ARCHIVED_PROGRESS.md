# ClawCodex 已归档进度详情

> 文档路径: `docs/ARCHIVED_PROGRESS.md`
> 源文档: `docs/PROGRESS.md` 第二节 (已完成任务详情)
> 版本: v1.0
> 创建日期: 2026-05-30

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

---

## 三、进行中任务进度 (F-13, F-20, R-7)

### F-13: Agent 记忆作用域隔离

| 属性 | 值 |
|------|-----|
| 优先级 | P1 |
| 状态 | 🔄 部分归档 |
| 规划日期 | 2026-05-19 |

**已完成**:
- [x] 添加 `load_memory_prompts()` 函数到 `src/memdir/memdir.py`
- [x] 添加 `_load_memory_prompt_for_scope()` 和 `_get_memory_path_for_scope()` 辅助函数
- [x] 导出 `load_memory_prompts` 到 `memdir/__init__.py`

**待完成**:
- [ ] 更新 `build_full_system_prompt()` 支持 `memory_scopes` 参数
- [ ] 更新 `build_full_system_prompt_blocks()` 支持 `memory_scopes` 参数
- [ ] 更新 `_build_memory_section()` 接受 `memory_scopes` 参数

### R-7: LiteLLM 替换 Provider 层

| 属性 | 值 |
|------|-----|
| 原始实现 | 多个 Provider 类 (~1,630 行) |
| 替代方案 | LiteLLM |
| 适配器文件 | `src/providers/_litellm_adapter.py` |
| 预计减少代码 | ~1,430 行 |
| 优先级 | P0 |
| 状态 | 🔄 进行中 |

**已完成**:
- [x] `src/providers/_litellm_adapter.py` 适配器文件已创建
- [x] 实现 `LiteLLMProvider` 类

**待完成**:
- [ ] 集成到 Provider 注册系统
- [ ] 移除硬编码的 anthropic/openai/zhipuai 必装依赖
- [ ] 端到端测试

---

## 四、规划任务概览

以下任务状态为"规划中"或"待开始"，未归档至本文档：

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
| R-8 | 工具语义搜索 (Qdrant) | P2 | ⏳ 待开始 |
| R-9 | 权限规则引擎 (Casbin) | P2 | ⏳ 待开始 |
| R-10 | 日志系统 (structlog) | P2 | ⏳ 待开始 |

---

*本文档由 `docs/PROGRESS.md` 第二节归档生成，最后更新于 2026-05-30*