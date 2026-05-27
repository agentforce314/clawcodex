# 新特性使用指南 (2026-05-27)

本文档介绍最近 48 小时内提交的新特性，帮助你在 CLI/TUI 中操作和测试。

---

## 1. 周期性任务 (`/loop` 命令)

### 功能说明
创建周期性任务，支持固定间隔和动态调度两种模式。

### 使用方式

```bash
# 动态维护模式 - 每 1 分钟~1 小时自动重试，维护当前分支
/loop

# 固定间隔维护模式 - 每 30 分钟执行维护任务
/loop 30m

# 固定间隔自定义 prompt - 每 5 分钟检查 CI 状态
/loop 5m 检查 CI 状态

# 带 every 语法 - 每 10 分钟巡逻一次
巡逻 every 10m

# 动态 reschedule 模式 - 每次执行完根据情况动态选择下次间隔
/loop 每小时巡逻
```

### 固定 vs 动态模式
| 模式 | 触发方式 | 间隔 |
|------|----------|------|
| `fixed-prompt` | cron 定时触发 | 用户指定 |
| `fixed-maintenance` | cron 定时触发 | 用户指定，维护 prompt |
| `dynamic-prompt` | 每次执行完动态安排 | 1分钟~1小时自适应 |
| `dynamic-maintenance` | 每次执行完动态安排 | 1分钟~1小时自适应 |

### 测试步骤
1. 输入 `/loop 10s 检查当前时间` 测试固定间隔
2. 输入 `/loop` 测试动态维护模式
3. 输入 `/loop 检查 PR 状态 every 15m` 测试 every 语法

---

## 2. Cron 任务管理工具

### 工具列表
| 工具 | 说明 |
|------|------|
| `CronCreate` | 创建新的定时任务 |
| `CronList` | 列出所有定时任务 |
| `CronDelete` | 删除指定的定时任务 |

### 使用方式
```bash
# 查看当前所有定时任务
CronList

# 创建定时任务 (通过 /loop 间接使用)
# ...

# 删除定时任务 (需要任务 ID)
CronDelete id=<任务ID>
```

### 任务过期机制
- 周期性任务默认 **7 天后自动过期**
- `DEFAULT_MAX_AGE_DAYS = 7` 可通过 GrowthBook 动态配置调整 jitter 参数

### 测试步骤
1. 输入 `/loop 1m 测试任务` 创建任务
2. 输入 `CronList` 查看任务列表
3. 记录任务 ID，输入 `CronDelete id=<ID>` 删除

---

## 3. Agent 阶段性进度汇报 (ProgressReportTool)

### 功能说明
Agent 在执行长任务时可以分阶段汇报进度，数据存储在 `TaskContext.tasks` 中。

### 工具输入
```json
{
  "taskId": "abc123",
  "stage": "implementation",
  "progress": 50,
  "summary": "Completed user registration endpoint",
  "nextAction": "Implement password reset flow",
  "metadata": {}
}
```

### 参数说明
| 参数 | 必填 | 说明 |
|------|------|------|
| `taskId` | 是 | 任务 ID |
| `stage` | 是 | 阶段名称 (如 analysis, implementation, testing) |
| `progress` | 否 | 进度百分比 0-100 |
| `summary` | 否 | 本阶段完成内容摘要 |
| `nextAction` | 否 | 下一步计划 |
| `metadata` | 否 | 自定义元数据 |

### 架构原理
```
Agent 执行到检查点 (方式一：检查点触发)
        ↓
调用 ProgressReportTool (方式二：专用工具)
        ↓
数据存入 ToolContext.tasks (方式三：持久化)
        ↓
StatusDashboard 消费显示
```

### 测试步骤
1. 启动一个长时间运行的任务
2. 观察 UI 是否显示进度信息 (tool_use_count, token_count)
3. 检查 recent_activities 是否显示最近 5 个工具调用

---

## 4. Manager/Worker Agent 消息交互

### 功能说明
Manager Agent 可以查询和指令 Worker Agent。

### TaskInspect 工具 (Manager 查询 Worker)
```json
{
  "targets": ["task-id-1", "task-id-2"],
  "fields": ["status", "progress", "pending_messages", "error"],
  "summary_only": false
}
```

**可用查询字段**: `status`, `progress`, `pending_messages`, `error`, `result_text`, `turn_count`

**使用场景**:
- 空 targets = 查询所有运行中的 Worker
- `summary_only=true` 时 pending_messages 仅显示数量

### TaskDirectives 工具 (Manager 指令 Worker)
```json
{
  "to": ["task-id-1", "task-id-2"],
  "priority": "high",
  "message": "[OBSERVE] 新需求变更请关注",
  "reason": "需求变更通知",
  "worker_permission_mode": "bypassPermissions",
  "always_allow_rules": [{"tool": "Bash", "pattern": "git *"}]
}
```

**优先级模式**:
| 优先级 | 行为 |
|--------|------|
| `critical` | prepend 到队列头部，Worker 立即处理 |
| `high` | prepend 到队列头部 |
| `normal` | append 到队列尾部 (FIFO) |

**权限模式**:
- `bypassPermissions`: 无需审批
- `bubble`: 人工审批
- `plan`: Manager 通过 plan_approval_response 审批
- `default`: 标准审批流程

**消息标签**: `[MANAGER]`, `[CRITICAL]`, `[HIGH]`, `[OBSERVE]`, `[INTERVENE]`, `[CORRECT]`

### 广播模式
```json
{"to": ["*"]}  // 广播给所有运行中的 Worker
```

### 测试步骤
1. 启动多 Agent 协作任务
2. Manager 通过 TaskInspect 查看 Worker 状态
3. Manager 通过 TaskDirectives 调整 Worker 优先级

---

## 5. POS to Agent 转化模式

### 功能说明
将专业系统 (POS) 的 SDK 接口转换为可复用的 Agent。

### 使用方式
```bash
# 基础用法
/convert-pos-to-agent <sdk_spec>

# 完整用法 (别名: /pos-to-agent)
/convert-pos-to-agent docker_build,docker_tag,docker_push,k8s_apply --requirements "CI/CD pipeline"
```

### 三层映射架构
```
POS (专业系统)  →  AgentDefinition
workflow 步骤    →  SkillSpec (SKILL.md)
SDK 接口         →  SdkMethod (原子工具)
```

### 内置映射规则
```python
# 部分预置规则
docker_build  → build_image
docker_push   → build_image
k8s_apply     → deploy_service
slack_send    → notify_team
s3_upload     → upload_artifact
spark_submit  → run_spark
train_model   → train_model
```

### 输出
- Agent 定义文件: `~/.clawcodex/agents/<name>.json`
- SKILL.md 文件: 每个 Skill 一个

### 测试步骤
1. 准备 SDK 定义或方法列表
2. 输入 `/convert-pos-to-agent docker,kubectl,k8s_apply --requirements "CI/CD"`
3. 验证生成的 Agent 定义和 Skill 文件

---

## 6. 后台运行模式 (Ctrl+B)

### 功能说明
通过 `Ctrl+B` 将应用切换到后台运行。

### 核心组件
| 组件 | 说明 |
|------|------|
| `background_state.py` | 进程级后台信号管理器 (单例) |
| `TailFollower` | tail -f 风格尾部追踪器 |
| `SessionWatcher` | 目录监控 (inotify/FSEvents/polling) |
| `graceful_shutdown.py` | SIGTSTP 信号处理 |

### 工作流程
```
TUI 捕获 Ctrl+B
        ↓
设置 background_signal Event
        ↓
foreground_promotion.run_with_background_escape 竞争获取信号
        ↓
set_backgrounded() 被调用
        ↓
is_backgrounded = True
```

### 测试步骤
1. 在 TUI 中按 `Ctrl+B`
2. 验证应用是否进入后台
3. 观察 `is_backgrounded` 标志是否变为 True

---

## 7. 错失任务通知

### 功能说明
当周期性任务因系统中断而错失时，通知用户确认是否执行。

### 特性
- 格式化错失任务列表
- 提示用户确认执行
- 结合 GrowthBook 支持动态 jitter 参数调整

### 测试步骤
1. 创建 `/loop 1m 巡逻`
2. 人为中断任务执行
3. 观察错失任务通知是否出现

---

## 8. Skills System Extension

### 功能说明
扩展的工具系统，支持 bundled skill 注册和管理。

### 注册的 Skills
| Skill | 说明 | 别名 |
|-------|------|------|
| `/convert-pos-to-agent` | POS to Agent 转化 | `/pos-to-agent` |

### 扩展模块结构
```
src/skills_ext/
├── bundled/
│   └── pos_to_agent.py    # POS 转化 skill
├── bundles.py              # 工具束定义
├── registry_ext.py         # 扩展注册表
└── ...
```

---

## 9. 权限模式选择器 (Permission Mode Picker)

### 功能说明
通过模态对话框切换权限模式，控制工具运行的审批流程。

### 权限模式列表
| 模式 | 说明 |
|------|------|
| `default` | 每个工具运行前询问 |
| `acceptEdits` | 自动批准文件编辑操作 |
| `plan` | Plan mode - 自动批准只读操作 |
| `bypassPermissions` | 运行所有工具不提示 |
| `dontAsk` | 从不提示，自动批准所有 |

### 使用场景
- 通过 TUI 菜单或快捷键呼出权限模式选择器
- 根据任务需要临时提升权限
- `bypassPermissions` 模式需开启 bypass 选项才可用

### 组件结构
```
src/tui/screens/permission_mode_picker.py  # 模态选择对话框
```

### 测试步骤
1. 在 TUI 中通过菜单或绑定键呼出权限模式选择器
2. 选择所需权限模式并确认
3. 验证所选模式是否生效

---

## 10. 会话恢复浏览器 (Resume Conversation)

### 功能说明
浏览和恢复历史会话，支持模糊搜索和实时过滤。

### 功能特性
- 模糊搜索 (fuzzy search)：支持输入过滤历史会话
- 实时计数显示：显示 "X / Y sessions" 过滤结果
- 会话元数据展示：标题、模型、消息数、时间戳

### 使用方式
| 方式 | 说明 |
|------|------|
| `clawcodex --tui --resume` | 启动时直接进入会话选择 |
| `/resume` 命令 | 从 REPL 呼出会话选择器 |
| Ctrl+B 后台后 | 用户选择会话重新附着 |

### 快捷键
| 快捷键 | 功能 |
|--------|------|
| `↑/↓` 或 `Ctrl+P/N` | 上/下选择会话 |
| `Enter` | 恢复选中的会话 |
| `Esc` | 取消，返回新会话 |

### SessionEntry 显示格式
```
2026-05-27 14:30 | 会话标题  model-name  12 msgs  id:abc12345
```

### 测试步骤
1. 输入 `/resume` 或 `clawcodex --tui --resume`
2. 在搜索框输入关键词过滤会话
3. 选择目标会话按 Enter 恢复
4. 观察右下角计数标签是否正确更新

---

## 11. 思考块功能 (Thinking Blocks)

### 功能说明
改进的思考块组件 (`assistant_thinking.py`)，支持更好地展示 AI 思考过程。

### 组件位置
```
src/tui/widgets/messages/assistant_thinking.py
```

### 功能改进
- 折叠/展开长思考内容
- 更好的语法高亮
- 悬停提示支持

---

## 12. Live Status 实时状态

### 功能说明
新增 `live_status.py` 模块，提供 REPL 实时状态显示。

### 组件位置
```
src/repl/live_status.py
```

### 功能特性
- 实时更新 token 使用量
- 工具调用计数
- 最近的工具调用活动列表 (recent_activities)

### 测试步骤
1. 启动 TUI 会话
2. 观察状态区域是否显示实时数据
3. 执行工具调用后检查计数器是否更新

---

## 快速测试清单

| 编号 | 功能 | 测试命令 | 预期结果 |
|------|------|----------|----------|
| 1 | /loop 固定间隔 | `/loop 30s 测试` | 任务立即执行，30秒后再次执行 |
| 2 | /loop 动态模式 | `/loop` | 进入维护模式，1分钟~1小时自动重试 |
| 3 | CronList | `CronList` | 显示当前所有定时任务 |
| 4 | CronDelete | `CronDelete id=<ID>` | 删除指定任务 |
| 5 | ProgressReport | 启动长任务 | 看到进度百分比更新 |
| 6 | TaskInspect | 多 Agent 场景 | 查看 Worker 状态 |
| 7 | TaskDirectives | 多 Agent 场景 | 向 Worker 发送指令 |
| 8 | /convert-pos-to-agent | `/convert-pos-to-agent docker --requirements "CI"` | 生成 Agent 定义 |
| 9 | Ctrl+B 后台 | TUI 中按键 | 应用进入后台 |
| 10 | 权限模式选择 | 呼出权限模式选择器 | 显示模式列表并可切换 |
| 11 | /resume 会话恢复 | `/resume` | 显示会话列表并支持搜索 |
| 12 | 思考块显示 | 启动会话观察思考 | 折叠/展开思考内容 |
| 13 | Live Status | TUI 状态区域 | 显示实时计数和数据 |

---

## 命令速查表

### /loop 命令
```
/loop                          # 动态维护模式
/loop 30m                      # 30分钟间隔维护
/loop 5m 检查CI                # 自定义 prompt
巡逻 every 10m                 # every 语法
/loop 每小时巡逻                # 动态 reschedule
```

### Cron 工具
```
CronList                       # 列出所有任务
CronDelete id=<ID>            # 删除任务
```

### ProgressReportTool
```
ProgressReport taskId=<ID> stage=<阶段名> progress=<0-100>
```

### TaskInspect (Manager)
```
TaskInspect targets=[<taskId1>,<taskId2>] fields=[status,progress]
TaskInspect targets=[]        # 查询所有 Worker
```

### TaskDirectives (Manager)
```
TaskDirectives to=[<taskId>] priority=high message=<消息内容>
TaskDirectives to=["*"] priority=critical message=<广播>
```

### POS 转化
```
/convert-pos-to-agent <SDK方法列表> --requirements "<业务需求>"
/pos-to-agent docker,kubectl --requirements "CI/CD"
```

---

## 注意事项

1. `/loop` 创建的任务默认 **7 天后过期**
2. 动态模式 `/loop` 的间隔在 **1 分钟到 1 小时**之间自适应
3. TaskInspect 和 TaskDirectives 仅限 **Manager Agent** 使用
4. Ctrl+B 后台化需要终端支持 **SIGTSTP** 信号
5. POS 转化功能支持 **OpenAPI URL**、**JSON spec** 或**逗号分隔方法列表**

---

*文档生成时间: 2026-05-27*
*对应提交: b306437 ~ 17e6d5b*