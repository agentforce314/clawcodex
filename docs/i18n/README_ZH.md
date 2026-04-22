<div align="center">

[English](../../README.md) | **中文** | [Français](README_FR.md) | [Русский](README_RU.md) | [हिन्दी](README_HI.md) | [العربية](README_AR.md) | [Português](README_PT.md)

# ClawCodex

**面向真实使用的 Claude Code Python 重构版 — 真实架构、可运行的 CLI Agent**

*从 TypeScript 参考实现移植，并在 Python 侧扩展了完整的运行时能力*

***

[![GitHub stars](https://img.shields.io/github/stars/agentforce314/clawcodex?style=for-the-badge&logo=github&color=yellow)](https://github.com/agentforce314/clawcodex/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/agentforce314/clawcodex?style=for-the-badge&logo=github&color=blue)](https://github.com/agentforce314/clawcodex/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)

**🔥 活跃开发中 • 每周更新新功能 🔥**

## FLEXIBLE SKILL SYSTEMS

**基于 Markdown 的斜杠技能系统，支持参数替换、工具限制，以及项目级 / 用户级技能加载。**

</div>

***

## 🎯 为什么是 ClawCodex？

**ClawCodex** 是一个面向真实使用的 **Claude Code Python 重构版**：它基于**真实 TypeScript 架构**移植而来，并且交付的是一个**可运行的 CLI Agent**，而不只是源码镜像。

- **真实 Agent Runtime** — 具备工具调用循环、流式 REPL、会话历史与多轮执行能力
- **高保真移植** — 尽可能保留 Claude Code 的原始架构，同时做符合 Python 风格的实现
- **适合继续开发** — 代码可读、测试完善，并支持基于 Markdown 的技能扩展
- **多 LLM 提供商** — 相对上游最大的进展之一：Claude Code 主要面向 **Claude 系列模型**，而 ClawCodex 致力于接入**各大主流 LLM 提供商**，在保持 Agent 能力的前提下，为用户提供更**灵活**、更**具性价比**的 agentic 编程体验

**这是一个真正可跑的 Claude Code 风格 Python 终端工作流：能流式回答、调工具、抓外部上下文，并通过 skills 扩展行为。**

**🚀 立即试用！Fork 它、修改它、让它成为你的！欢迎提交 Pull Request！**

***

## ⭐ Star 历史

[在 star-history.com 查看 Star 历史图表](https://www.star-history.com/?repos=agentforce314%2Fclawcodex&type=date&legend=top-left)

## ✨ 特性

### Streaming Agent Experience

```text
>>> /stream on
>>> 解释 tests/test_agent_loop.py
[流式回答中...]
• Read (tests/test_agent_loop.py) running...
  ↳ lines 1-180
>>> /render-last
```

- 直接回答支持真实 API 流式输出，带工具的 agent loop 也具备更完整的流式体验
- 内置 `/stream` 开关用于实时输出，`/render-last` 可按需把上一条回答重新渲染为 Markdown
- 专门为终端演示优化：一边看回答流出，一边看到工具调用，并保留稳定回退路径

### 可编程 Skill Runtime

```md
---
description: 用类比 + 图示解释代码
allowed-tools:
  - Read
  - Grep
  - Glob
arguments: [path]
---

请解释 $path 的实现：先给一个类比，再画一个结构示意图。
```

- 基于 `SKILL.md` 的 Markdown 斜杠命令
- 支持项目级技能、用户级技能、命名参数替换与工具限制

### 多提供商支持

ClawCodex 的核心优势之一是**多模型 / 多提供商**：Claude Code 以 **Claude** 为主线，我们则希望在同一套 Agent 运行时上覆盖**各大主流 LLM 提供商**，便于按场景切换厂商、区域与价位，而不牺牲工具、技能与编码闭环——这也是让 agentic 编程在成本与灵活性上真正可用的基础。

```python
providers = ["anthropic", "openai", "glm", "minimax"]  # 可继续扩展
```

### 交互式 REPL（默认）与 Textual TUI（可选）

**默认**为 **prompt_toolkit + Rich** 行内 REPL（滚动区 + 状态行）。使用 **`clawcodex --tui`** 或 REPL 内的 **`/tui`** 可进入 **Textual** 全屏界面。

```text
>>> 你好！
Assistant: 嗨！我是 ClawCodex，一个 Python 重实现...

>>> /help          # 显示命令
>>> /tools         # 列出已注册工具
>>> /tui           # 切换到 Textual TUI
>>> /stream on     # 流式渲染开关
>>> /save          # 保存会话
>>> Tab            # 自动补全
>>> /explain-code qsort.py   # 运行 SKILL.md 技能（或 /skill …）

# 多行输入：Shift+Enter、Meta/Alt+Enter，或 `\` 后 Enter；单独 Enter 提交。
```

### 完整的 CLI

```bash
clawcodex                       # 行内 REPL（默认）
clawcodex --tui                 # Textual TUI
clawcodex --stream              # 开启实时渲染的 REPL
clawcodex login                 # 交互式配置 API
clawcodex config                # 查看配置
clawcodex --version             # 版本信息

# 非交互 / 脚本（管道、CI、自动化）
clawcodex -p "用中文总结 src/cli.py"
clawcodex -p "Hello" --output-format json

clawcodex --provider anthropic --model claude-sonnet-4-6 -p "Hi"
clawcodex --max-turns 10 --allowed-tools Read,Grep -p "查找 TODO"
```

***

## 📊 状态

| 组件    | 状态     | 数量     |
| ----- | ------ | ------ |
| REPL 命令 | ✅ 完成   | 内置命令 + `/tools`、`/stream`、`/context`、`/compact`、技能等 |
| 工具系统 | ✅ 完成   | 30+ 工具 |
| 自动化测试 | ✅ 已覆盖  | 工具、agent loop、providers、parity、REPL 等 |
| 文档    | ✅ 完成   | 指南、多语言 README、[FEATURE_LIST.md](../../FEATURE_LIST.md) |

### 核心系统

| 系统 | 状态 | 描述 |
|------|------|------|
| CLI 入口 | ✅ | `clawcodex`、`login`、`config`、`-p`、`--tui`、`--stream`、`--version` |
| 交互式 REPL | ✅ | 默认行内 REPL；可选 Textual；历史、Tab、多行输入 |
| 多提供商支持 | ✅ | Anthropic、OpenAI、智谱 GLM、Minimax |
| 会话持久化 | ✅ | 本地保存/加载会话 |
| Agent Loop | ✅ | 工具调用循环；支持流式与无头模式 |
| Skill 系统 | ✅ | SKILL.md 斜杠技能：参数与工具白名单 |
| 上下文构建 | 🟡 | workspace / git / `CLAUDE.md` 注入；更丰富的摘要与 memory 仍在演进 |
| 权限系统 | 🟡 | 框架与检查逻辑已有；全面集成进行中 |
| MCP | 🟡 | MCP 相关工具与接线已有；协议层/运行时仍在完善 |

### 工具系统（已实现 30+ 工具）

| 类别 | 工具 | 状态 |
|------|------|------|
| 文件操作 | Read, Write, Edit, Glob, Grep | ✅ 完成 |
| 系统 | Bash 执行 | ✅ 完成 |
| 网络 | WebFetch, WebSearch | ✅ 完成 |
| 交互 | AskUserQuestion, SendMessage | ✅ 完成 |
| 任务管理 | TodoWrite, TaskManager, TaskStop | ✅ 完成 |
| Agent 工具 | Agent, Brief, Team | ✅ 完成 |
| 配置 | Config, PlanMode, Cron | ✅ 完成 |
| MCP | MCP 工具与资源 | 🟡 工具已接线；完整 client/runtime 仍在演进 |
| 其他 | LSP, Worktree, Skill（SKILL.md）, ToolSearch | ✅ 完成 |

### 路线图进度

- ✅ **阶段 0**：可安装、可运行的 CLI
- ✅ **阶段 1**：Claude Code 核心 MVP 体验
- ✅ **阶段 2**：真实工具调用闭环
- 🟡 **阶段 3**：上下文深度、权限集成、类 `/resume` 的恢复能力（进行中）
- 🟡 **阶段 4**：MCP 运行时、插件与扩展（工具已有，平台能力持续推进）
- ⏳ **阶段 5**：Python 原生差异化特性

**详细功能状态和 PR 指南请查看 [FEATURE_LIST.md](../../FEATURE_LIST.md)。**

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex

# 创建虚拟环境（推荐使用 uv）
uv venv --python 3.11
source .venv/bin/activate

# 安装包与 console 入口（推荐）
uv pip install -e ".[dev]"

# 或：先装依赖再 editable
# uv pip install -r requirements.txt && uv pip install -e .
```

### 配置

#### 方式 1：交互式（推荐）

```bash
clawcodex login
# 或: python -m src.cli login
```

这个流程会：

1. 让你选择 provider：anthropic / openai / glm / minimax
2. 让你输入该 provider 的 API key
3. 可选：保存自定义 base URL
4. 可选：保存默认 model
5. 将该 provider 设为默认

配置文件会保存在 `~/.clawcodex/config.json`。示例结构：

```json
{
  "default_provider": "openai",
  "providers": {
    "anthropic": {
      "api_key": "base64-encoded-key",
      "base_url": "https://api.anthropic.com",
      "default_model": "claude-sonnet-4-6"
    },
    "openai": {
      "api_key": "base64-encoded-key",
      "base_url": "https://api.openai.com/v1",
      "default_model": "gpt-5.4"
    },
    "glm": {
      "api_key": "base64-encoded-key",
      "base_url": "https://open.bigmodel.cn/api/paas/v4",
      "default_model": "zai/glm-5"
    },
    "minimax": {
      "api_key": "base64-encoded-key",
      "base_url": "https://api.minimaxi.com/anthropic",
      "default_model": "MiniMax-M2.7"
    }
  }
}
```

### 运行

```bash
clawcodex                  # 启动行内 REPL（等同于 python -m src.cli）
clawcodex --help           # 含 --tui、-p、--provider、--model 等
```

**就这样！** 配置密钥后即可使用 CLI 或 REPL。

***

## 💡 使用

### REPL 命令

| 命令 | 描述 |
| --- | --- |
| `/` | 显示命令与技能 |
| `/help` | 帮助 |
| `/tools` | 列出已注册工具名 |
| `/tool <name> <json>` | 以 JSON 输入直接调用工具 |
| `/stream` | 流式渲染：`/stream on`、`off` 或 `toggle` |
| `/render-last` | 将上一条助手回复重新渲染为 Markdown |
| `/save`、`/load <id>` | 保存或加载会话 |
| `/clear` | 清空对话（亦支持 `/reset`、`/new`） |
| `/tui` | 进入 Textual TUI |
| `/skill` | 技能启动流程 |
| `/context` | 工作区 / 提示上下文（若可用） |
| `/compact` | 压缩或清空对话（不可用时回退为清空） |
| `/exit`、`/quit`、`/q` | 退出 |

### Skills（技能 / 斜杠命令）教程

技能是存放在 `.clawcodex/skills` 下的 Markdown 斜杠命令。每个技能对应一个目录，并且文件名固定为 `SKILL.md`。

**1）创建项目技能**

创建：

```text
<project-root>/.clawcodex/skills/<skill-name>/SKILL.md
```

示例：

```md
---
description: 用类比 + 图示解释代码
when_to_use: 当用户问“这段代码怎么工作？”时使用
allowed-tools:
  - Read
  - Grep
  - Glob
arguments: [path]
---

请解释 $path 的实现：先给一个类比，再画一个结构示意图。
```

**2）在 REPL 中使用**

```text
❯ /
❯ /<skill-name> <args>
```

示例：

```text
❯ /explain-code qsort.py
```

**补充说明**

- 用户级技能：`~/.clawcodex/skills/<skill-name>/SKILL.md`
- 工具限制：`allowed-tools` 用于限制技能允许调用的工具集合
- 参数替换：支持 `$ARGUMENTS`、`$0`、`$1`、以及命名参数（例如 `$path`，来自 `arguments`）
- 占位符写法：请使用 `$path`，不要写成 `${path}`

***

## 🎓 为什么选择 ClawCodex？

### 基于真实源码

- **不是克隆** — 从真实的 TypeScript 实现移植而来
- **架构保真** — 保持经过验证的设计模式
- **持续改进** — 更好的错误处理、更多测试、更清晰的代码

### 原生 Python

- **类型提示** — 完整的类型注解
- **现代 Python** — 使用 3.10+ 特性
- **符合习惯** — 干净的 Python 风格代码

### 以用户为中心

- **3 步设置** — 克隆、`clawcodex login` 配置、`clawcodex` 运行
- **交互式配置** — 选择 provider、Base URL、默认模型
- **行内或 TUI** — 默认终端原生 REPL；可选 Textual
- **可脚本化** — `-p`、JSON、NDJSON 便于自动化
- **会话持久化** — 保存与恢复对话

***

## 📦 项目结构

```text
clawcodex/
├── src/
│   ├── cli.py              # CLI 入口（控制台命令 clawcodex）
│   ├── entrypoints/        # 无头 (-p) 与 TUI 启动
│   ├── repl/               # 行内 REPL
│   ├── tui/                # Textual UI（--tui、/tui）
│   ├── providers/          # Anthropic、OpenAI、GLM、Minimax
│   ├── agent/              # 会话、对话、提示
│   ├── tool_system/        # Agent loop、工具与 schema
│   ├── skills/             # SKILL.md 与 Skill 工具
│   ├── services/           # MCP、compact、IDE、工具执行等
│   ├── context_system/     # 工作区 / git / CLAUDE.md
│   ├── permissions/        # 权限模式与 bash 解析
│   ├── hooks/              # Hook 类型与执行
│   └── command_system/     # 斜杠命令与参数替换
├── typescript/             # 参考 / 对等源码（运行 Python CLI 非必需）
├── tests/                  # pytest
├── docs/                   # 指南、多语言 README、重构笔记
├── .clawcodex/skills/      # 项目级技能（可选）
├── FEATURE_LIST.md         # 能力矩阵与路线图
└── pyproject.toml          # 包元数据与 clawcodex 入口
```

***

## 🤝 贡献

**我们欢迎贡献！**

```bash
# 快速开发设置
pip install -e .[dev]
python -m pytest tests/ -v
```

查看 [CONTRIBUTING.md](../../CONTRIBUTING.md) 了解指南。

***

## 📖 文档

- **[SETUP_GUIDE.md](../guide/SETUP_GUIDE.md)** — 详细安装说明
- **[CONTRIBUTING.md](../../CONTRIBUTING.md)** — 开发指南
- **[TESTING.md](../guide/TESTING.md)** — 测试指南
- **[CHANGELOG.md](../../CHANGELOG.md)** — 版本历史

***

## ⚡ 性能

- **启动时间**：< 1 秒
- **内存占用**：< 50MB
- **响应**：回合式输出，支持 Rich Markdown 渲染

***

## 🔒 安全

✅ **基础本地安全实践**

- Git 中无敏感数据
- API 密钥在配置中做了基础混淆
- `.env` 文件被忽略
- 适合本地开发工作流

***

## 📄 许可证

MIT 许可证 — 查看 [LICENSE](../../LICENSE)

***

## 🙏 致谢

- 基于 Claude Code TypeScript 源码
- 独立的教育项目
- 未隶属于 Anthropic

***

<div align="center">

### 🌟 支持我们

如果你觉得这个项目有用，请给个 **star** ⭐！

**用 ❤️ 制作 by ClawCodex 团队**

[⬆ 回到顶部](#clawcodex)

</div>
