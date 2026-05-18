# ClawCodex

- [最新消息](#最新消息)
- [简介](#简介)
- [目录结构](#目录结构)
- [版本说明](#版本说明)
- [兼容性信息](#兼容性信息)
- [环境部署](#环境部署)
- [快速入门](#快速入门)
- [特性介绍](#特性介绍)
- [API参考](#api参考)
- [FAQ](#faq)
- [安全声明](#安全声明)
- [分支维护策略](#分支维护策略)
- [版本维护策略](#版本维护策略)
- [免责声明](#免责声明)
- [License](#license)
- [贡献声明](#贡献声明)
- [建议与交流](#建议与交流)

# 最新消息

- [2026-05-14]: 代码库统计 — Python文件总数: 837个; Python代码总行数: **167,034行**
- [2026-05-14]: ESC取消延迟修复 (#130) — 按ESC键现在可以在约50ms内取消正在执行的Bash命令和流式响应
- [2026-05-12]: 引导与架构文档 — 新架构概览见[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [2026-05-11 (v0.5.0)]: ClawCodex v0.5.0发布 — 品牌全面更名为ClawCodex；响应式状态子系统移植（信号、存储、会话上下文、成本追踪）
- [2026-05-10]: MCP子系统上线 — 完整的Model Context Protocol支持，包含OAuth连接、HTTPS、XSS加固和异步I/O
- [2026-05-08]: Hooks系统 — 基于快照的执行器，带工作区信任门控
- [2026-05-08]: 多智能体协作 — 类型化任务状态机、JSONL转录写入器、智能体任务生命周期
- [2026-05-07]: 自动记忆与并发 — 持久化自动记忆子系统移植；并发编排器和工具执行与TypeScript参考实现一致
- [2026-04-30]: Skills子系统对标 — Skills（项目级+用户级、命名参数、工具限制）与TypeScript参考实现对等
- [2026-04-25]: DeepSeek支持 — 直接支持DeepSeek provider（V4 Pro / Flash）
- [2026-04-20]: 初始公开版本 — 首个包含项目源码、文档、测试和构建配置的提交

# 简介

ClawCodex是Claude Code的**生产级Python重实现**——真实的架构、可靠的CLI智能体。

从TypeScript参考实现移植，并使用Python原生运行时扩展。ClawCodex保持了原始Claude Code架构，同时将其适配为符合Python习惯的代码风格。

更多详情请查看[架构文档](docs/ARCHITECTURE.md)。

## 开源引用

ClawCodex基于以下开源项目构建：

| 项目 | 说明 |
|------|------|
| [Claude Code (Anthropic)](https://github.com/anthropics/claude-code) | TypeScript参考实现，ClawCodex从其架构和设计模式中移植 |
| [prompt-toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) | REPL交互界面核心组件 |
| [Rich](https://github.com/Textualize/rich) | 富文本输出和终端美化 |
| [Textual](https://github.com/Textualize/textual) | TUI界面框架 |

<div align="center">

[![GitHub stars](https://img.shields.io/github/stars/agentforce314/clawcodex?style=for-the-badge&logo=github&color=yellow)](https://github.com/agentforce314/clawcodex/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/agentforce314/clawcodex?style=for-the-badge&logo=github&color=blue)](https://github.com/agentforce314/clawcodex/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)

</div>

# 目录结构

```
│  __init__.py
│
├─src
│  │  cli.py                  # CLI入口
│  │  config.py               # 配置管理
│  │  cost_tracker.py         # 成本追踪
│  │  init.py                 # 初始化
│  │  task_registry.py        # 任务注册
│  │  token_estimation.py     # Token估算
│  │
│  ├─agent                   # 对话、会话、提示词
│  ├─api                     # API层
│  ├─assistant               # 助手相关
│  ├─auth                    # 认证
│  ├─bootstrap               # 引导与状态初始化
│  ├─bridge                  # CCR远程桥接
│  ├─buddy                   # Buddy系统
│  ├─cli_core                # CLI核心逻辑
│  ├─command_system          # 斜杠命令系统
│  ├─compact_service         # 压缩服务
│  ├─components              # UI组件
│  ├─constants               # 常量
│  ├─context_system          # 工作区/git/CLAUDE.md上下文
│  ├─coordinator             # 多智能体协调器
│  ├─hooks                   # Hook类型和执行
│  ├─keybindings             # 键盘绑定
│  ├─migrations              # 数据库迁移
│  ├─models                  # 数据模型
│  ├─orchestrator            # 自主模式和工作流编排
│  ├─outputStyles            # 输出样式
│  ├─permissions             # 权限模式
│  ├─plugins                 # 插件系统
│  ├─prefetch                # 预取
│  ├─providers               # LLM providers (Anthropic, OpenAI, GLM, Minimax, OpenRouter, DeepSeek)
│  ├─query                   # 查询循环
│  ├─reference_data          # 参考数据
│  ├─remote                  # 远程会话
│  ├─repl                   # Inline REPL (prompt_toolkit + Rich)
│  ├─schemas                 # 数据模式
│  ├─screens                 # 屏幕
│  ├─server                  # 服务器
│  ├─services                # MCP、压缩、IDE桥接、工具执行
│  ├─settings                # 设置
│  ├─skills                  # SKILL.md加载和技能工具
│  ├─state                   # 状态管理
│  ├─tasks                   # 任务管理
│  ├─tool_system             # 工具系统、Agent循环
│  ├─transports              # 传输层
│  ├─tui                     # Textual UI
│  ├─types                   # 类型定义
│  ├─upstreamproxy           # 上游代理
│  ├─utils                   # 工具函数
│  └─vim                     # Vim模式
│
├─tests                      # pytest测试套件
├─docs                      # 文档、指南、i18n
├─demos                      # ClawCodex生成的演示应用
├── typescript               # 参考/对等源码
├── FEATURE_LIST.md          # 能力矩阵和路线图
├── CHANGELOG.md             # 版本历史
├── CONTRIBUTING.md          # 贡献指南
└─pyproject.toml             # 包元数据和clawcodex脚本
```

# 版本说明

ClawCodex版本详情请参考：[版本说明](CHANGELOG.md)。

# 兼容性信息

ClawCodex兼容信息：
- Python 3.10+
- 支持的LLM Providers: Anthropic, OpenAI, Zhipu GLM, Minimax, OpenRouter, DeepSeek

# 环境部署

ClawCodex可通过源码安装。详细步骤请遵循[安装指南](docs/guide/SETUP_GUIDE.md)。

## 快速安装

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex

# 创建虚拟环境
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10+

# 安装依赖
pip install -r requirements.txt

# 配置API密钥
python -m src.cli login   # 写入配置到 ~/.clawcodex/config.json

# 启动REPL
python -m src.cli
```

# 快速入门

通过运行一个完整的Agent循环示例开始使用ClawCodex，该示例演示了工具定义、Agent执行和轨迹观察。

```bash
clawcodex                    # 启动内联REPL
clawcodex -p "Summarize src/cli.py"  # 非交互模式
clawcodex --tui              # Textual TUI模式
```

- 实践教程请探索[设置指南](docs/guide/SETUP_GUIDE.md)。

# 特性介绍

## 开源组件引用

ClawCodex引用了以下开源组件：

| 组件 | 版本要求 | 用途 |
|------|----------|------|
| [anthropic](https://pypi.org/project/anthropic/) | - | Anthropic Claude API客户端 |
| [openai](https://pypi.org/project/openai/) | - | OpenAI API客户端 |
| [zhipuai](https://pypi.org/project/zhipuai/) | - | 智谱AI GLM API客户端 |
| [python-dotenv](https://pypi.org/project/python-dotenv/) | - | 环境变量管理 |
| [rich](https://pypi.org/project/rich/) | - | 富文本输出和美化 |
| [prompt-toolkit](https://pypi.org/project/prompt-toolkit/) | - | REPL交互界面 |
| [tiktoken](https://pypi.org/project/tiktoken/) | >=0.7.0 | Token计数 |
| [textual](https://pypi.org/project/textual/) | >=0.79 | TUI界面框架 |
| [PyYAML](https://pypi.org/project/PyYAML/) | >=6.0 | YAML解析（SKILL.md、agent文件、output styles） |
| [pathspec](https://pypi.org/project/pathspec/) | >=0.11 | 路径匹配（条件技能paths规则） |
| [websockets](https://pypi.org/project/websockets/) | >=14.0 | WebSocket客户端/服务器（CCR桥接） |
| [httpx-sse](https://pypi.org/project/httpx-sse/) | >=0.4 | SSE消费者 |
| [pydantic-settings](https://pypi.org/project/pydantic-settings/) | >=2.0.0 | 类型安全配置 |
| [python-frontmatter](https://pypi.org/project/python-frontmatter/) | >=1.0.0 | SKILL.md/agent文件解析 |
| [tree-sitter](https://pypi.org/project/tree-sitter/) | >=0.25.0 | Bash AST解析 |
| [tree-sitter-bash](https://pypi.org/project/tree-sitter-bash/) | >=0.25.0 | Bash语法树 |
| [GitPython](https://pypi.org/project/GitPython/) | >=3.1.0 | Git操作 |
| [litellm](https://pypi.org/project/litellm/) | >=1.0.0 | 统一Provider层 |
| [pluggy](https://pypi.org/project/pluggy/) | >=1.0.0 | Hook系统 |
| [outlines](https://pypi.org/project/outlines/) | >=0.0.80 | 结构化输出 |

## 核心功能

- **流式Agent体验** — 真正的API流式传输，实时输出
- **可编程Skill运行时** — 基于Markdown的SKILL.md斜杠命令
- **多Provider支持** — Anthropic、OpenAI、GLM、Minimax、OpenRouter、DeepSeek
- **交互式REPL** — 默认内联REPL，可选Textual TUI
- **完整CLI** — REPL、TUI、非交互模式

# API参考

API参考详见：[Python API](docs/zh/api_python.md) 与 [命令行 API](docs/zh/command_api.md)。

# FAQ

相关FAQ请参考项目文档或提交[issue](https://github.com/agentforce314/clawcodex/issues)。

# 安全声明

- API密钥在配置文件中加密存储
- `.env`文件被Git忽略
- 敏感数据不会提交到Git
- `--dangerously-skip-permissions`标志仅在沙箱环境中推荐使用

# 分支维护策略

版本分支遵循定义的维护阶段：

| 状态 | 时间 | 说明 |
|------|------|------|
| 开发 | 持续 | 新特性开发和问题修复 |
| 维护 | 3-12个月 | 常规分支维护；仅修复重大BUG，不加入新特性 |
| 生命周期终止（EOL） | N/A | 分支不再接受任何修改 |

# 版本维护策略

| 版本 | 维护策略 | 当前状态 | 发布日期 |
|------|----------|----------|----------|
| main | 开发 | 开发中 | 在研分支 |
| v0.5.0 | 常规分支 | 维护 | 2026-05-11 |

# 免责声明

- 本仓库代码包含多个开发分支，这些分支可能包含未完成、实验性或未测试的功能。在正式发布前，这些分支不应被应用于任何生产环境。
- 使用开发分支所导致的任何问题、损失或数据损坏，本项目及其贡献者概不负责。

# License

ClawCodex以MIT许可证许可，对应许可证文本可查阅[LICENSE](LICENSE)。

# 贡献声明

- 如果您遇到bug，请提交[issue](https://github.com/agentforce314/clawcodex/issues)。
- 如果您计划贡献bug-fixes，请提交Pull Requests，参见[贡献指南](CONTRIBUTING.md)。
- 如果您计划贡献新特性、功能，请先创建issue与我们讨论。
- 更详细的贡献流程，请参考[贡献指南](CONTRIBUTING.md)。

# 建议与交流

欢迎大家为社区做贡献。如果有任何疑问或建议，请提交[issue](https://github.com/agentforce314/clawcodex/issues)，我们会尽快回复。感谢您的支持。