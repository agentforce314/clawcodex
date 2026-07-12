<div align="center">

[English](../../README.md) | **中文** | [Français](README_FR.md) | [Русский](README_RU.md) | [हिन्दी](README_HI.md) | [العربية](README_AR.md) | [Português](README_PT.md)

# ClawCodex

**面向生产使用的 Claude Code Python 重写版 —— 真实架构、可靠的 CLI Agent**

*从 TypeScript 参考实现移植而来，并扩展了 Python 原生运行时*

***

[![GitHub stars](https://img.shields.io/github/stars/agentforce314/clawcodex?style=for-the-badge&logo=github&color=yellow)](https://github.com/agentforce314/clawcodex/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/agentforce314/clawcodex?style=for-the-badge&logo=github&color=blue)](https://github.com/agentforce314/clawcodex/network/members)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)


**🔥 活跃开发中 • 每周更新新功能 🔥**

![ClawCodex 截图](../../assets/clawcodex-screenshot-1.png)

</div>

***

## ⚡ 快速安装

```bash
git clone https://github.com/agentforce314/clawcodex.git
cd clawcodex
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10+
pip install -r requirements.txt

python -m src.cli login   # 配置写入 ~/.clawcodex/config.json

python -m src.cli --dangerously-skip-permissions   # 启动 REPL
```

配置文件保存在 `~/.clawcodex/config.json`。最小示例：

```json
{
  "default_provider": "deepseek",
  "providers": {
    "deepseek": {
      "api_key": "xxx-xxx",
      "base_url": "https://api.deepseek.com",
      "default_model": "deepseek-v4-pro"
    }
  },
  "env": {
    "TAVILY_API_KEY": "tvly-YOUR-TAVILY-API-KEY"
  }
}
```

> **注意：** WebSearch 工具需要 `TAVILY_API_KEY`——可在 [tavily.com](https://tavily.com) 获取密钥。

`session`、`settings` 和 `env` 块均为可选——省略时会使用合理的默认值。完整结构见 [配置](#配置)。

***

## 📰 新闻

- **2026-07-06（v1.0.0）：** **ClawCodex v1.0.0 —— 1.0 正式版：目标驱动的自主性、hooks 与 MCP 的生产级接线、强化的权限系统** —— 自 v0.7.0 以来的 86 个提交（#580–#668）完成了各大子系统的端到端接线，ClawCodex 正式升级到 1.0。**目标驱动的自主性：** `/goal` + `/subgoal` 完成条件循环让 agent 持续工作，直到 LLM 评审确认目标真正达成（#664）；新的 Monitor 工具以带背压的方式流式输出长时间运行的 shell 日志（#665）；后台 bash 完成通知（#663）；coordinator 模式在生产路径端到端接通（#634）；`/advisor` 省 token 的 worker/reviewer 搭档模式在 Ink TUI 上恢复（#668）。**Hooks 进入生产：** 配置的 hooks 现在真正生效 —— bootstrap Hooks 抽象（#583）、UserPromptSubmit（#597）、多作用域 + 生命周期 hooks（#595）、`if` 预过滤（#643）、PreToolUse `permissionDecision`（#655）、权限询问点的 PermissionRequest hooks（#637）、MCP elicitation hooks（#659），以及 teammate TaskCompleted / TeammateIdle 停止 hooks（#642）。**MCP 补全：** 通过 `/mcp` 流程完成 OAuth 服务器认证（#662）、`tools/list_changed` 实时刷新（#598、#604）、服务器说明注入系统提示词（#654），`clawcodex mcp serve` 将 ClawCodex 工具重新暴露为 MCP stdio 服务器（#635）。**权限强化：** 可读的批准框与可扩展的持久会话授权（#608–#611）、复合命令权限对齐（#622）、Bash 归一化强化（#626）、`disableBypassPermissionsMode` 锁定（#660）、诚实的拒绝启动无沙箱守卫（#658）、子进程密钥擦除（#650），以及 auto 模式下由 flag 控制的 LLM 安全分类器通道（#589）。**TUI 成熟度：** 忠实还原 Claude Code 的观感 —— diff 渲染、工具调用记录、任务列表、composer + 权限模式徽章、busy 行（#612–#616）——外加精简的 vim 编辑引擎（#667）、Esc 中断 + 去武装的 Ctrl+C（#625）、完全可编辑的多行输入（#621）、斜杠命令参数提示（#631）、常驻会话统计行（#657），以及恢复的 `/cost`、`/skills` 与 `/model`（#627、#629、#630）。**可靠性：** 生产压缩管线接通、auto-compact 真正应用其结果（#587、#607），完整重试通道 + 模型回退 + 消息历史缓存（#586），并行 Agent 扇出并修复并发上限死锁（#590），杀死后台 agent 会真正停止运行（#606），输出样式端到端可用（#640）。代码库统计：Python 文件 1,170 个，**256,909 行**（高于 2026-06-11 的 233,520 行）。
- **2026-06-30（v0.7.0）：** **ClawCodex v0.7.0 —— TUI 自动主题、忠实的内联渲染与 Claude Code 风格的工具轨迹** —— Ink TUI 启动时会探测终端背景色（OSC 11）并自动匹配明/暗主题，任何终端上文字都清晰可读、无需环境变量（#577）。内联模式像 Claude Code 一样*真正*内联渲染：启动不清屏，启动时不与之前的终端输出重叠、退出时不与返回的 shell 提示符重叠（#573、#575）。工具轨迹采用 Claude 风格 —— 工作区相对路径（`Read(src/foo.ts)`）、`Grep(pattern)` 标签与 `Read N lines` 结果折叠（#574）——横幅新增 🦞 吉祥物，暗色主题下的次要文字更亮（#576）。
- **2026-06-24（v0.6.0）：** **ClawCodex v0.6.0 —— 交互式 TUI REPL 对齐** —— 一批输入侧移植让 Python REPL 与 ink 参考实现对齐：可用的斜杠命令菜单（像 ink REPL 一样执行 / 补全 / 过滤）、带实时 token 数 + 已用时长忙碌行的星光 spinner、上下文感知的提示符底部提示（中断 / bash / 语法）、`?` 快捷键帮助面板、`@` 文件提及下拉框（原位拼接）、双击 Ctrl+C / Ctrl+D 退出、Ctrl+R 历史搜索 + 双击 Esc 清空草稿、`[Pasted text #N +K lines]` 大段粘贴占位符，以及完成的命令队列（排空排队的提示 + 暗色预览）。登录文档现在列出全部 25 个 provider（#383）。
- **2026-06-23：** **一键安装器** —— `curl -fsSL https://clawcodex.app/install.sh | bash` 自动安装 uv（无需 sudo）、准备 Python 3.10+、克隆到 `~/.clawcodex`、创建锁定版本的 venv，并把 `clawcodex` 注册到 PATH；附带 status / doctor / verify / update / uninstall 子命令，可安全重复运行，支持 macOS / Linux / WSL。
- **2026-06-21：** **新增 18 个 LLM provider —— 注册表从 7 增至 25（#377）** —— 数据驱动的 `ProviderSpec` 注册表在手写 provider 之外新增 18 个 OpenAI 兼容后端（nvidia-nim、fireworks、together、moonshot/Kimi、novita、siliconflow、deepinfra、stepfun、arcee、huggingface、volcengine、xiaomi-mimo、atlascloud、wanjie-ark，以及本地 ollama / vllm / sglang）；支持别名感知的配置解析、标准环境变量密钥回退（如 `TOGETHER_API_KEY`）与免密钥的本地服务器。
- **2026-06-18：** **DeepSeek 前缀缓存利用 —— 巨大的 token 成本优势（#363）** —— ClawCodex 现在让请求前缀在多轮之间保持**字节级稳定**，使 DeepSeek 的自动 prompt 前缀缓存覆盖整个 `system + tools + history` 区段。每请求可变的部分（env、可变的 `MEMORY.md` 正文、plan 模式等）被移到对话历史*之后*的尾部 `<system-reminder>`，即使 memory/env 变化也不会击穿缓存前缀。同时注册 DeepSeek 的 **1M token 上下文窗口**，把其 prompt 缓存用量映射到 Anthropic 的 `cache_read_input_tokens` 约定，并在 `/cost` 中展示每模型的**缓存命中率**与成本。**为什么意义重大 —— token 经济学：** Claude Fable 5 每 1M 输入/输出 token 收费 **$10 / $50**，而 **DeepSeek-V4-Pro 仅为 $0.435 / $0.87** —— 输入已**便宜约 23×**、输出**便宜约 57×**。由于**缓存命中的输入仅按正常输入价的 10% 计费**，agentic 编码实际产生的长上下文会话每 1M 输入 token 只需**约 $0.0435** —— 比 Fable 5 的输入**便宜约 230×**。ClawCodex 在这里解锁的 token 效率是**巨大的**。全部逻辑仅对 `deepseek` provider 生效 —— 其他 provider 的请求逐字节不变。后续修复：被截断的工具调用参数 JSON 现在会在共享的 OpenAI 兼容层尽力恢复，DeepSeek 流中断时保留部分工具参数而不是丢弃为 `{}`（#364）。
- **2026-06-16：** **Z.ai GLM-5.2 支持（#343）** —— 新增 `zai` provider，对接 Z.ai 的 OpenAI 兼容 GLM 编程套餐（`https://api.z.ai/api/coding/paas/v4`），提供 `GLM-5.1` 与 `GLM-5.2` 预览版；GLM-5.2 的编码能力可比肩 Claude Opus 4.7。首个用 GLM-5.2 端到端生成的应用——一个 [2026 世界杯介绍页](../../demos/wc26-intro/index.html)（动效首屏 + 实时倒计时、三个主办国、16 座球场、赛制说明与破纪录数据）。
- **2026-06-11：** **代码库统计** —— Python 文件总数：1,093 个；Python 代码总行数：**233,520 行**（高于 2026-05-29 的 213,777 行；新增约 1.97 万行，主要来自交互式命令系统批次、动态 workflow 引擎 + `/deep-research`，以及 Tavily 网络工具链更新）。
- **2026-06-10 至 2026-06-11：** **动态 workflow 引擎 + `/deep-research`（#262–#264、#266–#271）** —— Python workflow 引擎核心（`agent()`/`parallel()`/`pipeline()`/`phase()`、运行日志、断点恢复）完成端到端接线：Workflow 工具、`/workflows` TUI 对话框 + 状态栏指示、按 agent 重试、worktree 隔离、结果投递，以及注册为斜杠命令的内置 `/deep-research` 研究工作流。可靠性方面：LLM 读取超时统一应用到所有 openai 兼容 provider（#269），并行 agent 不再在事件循环上串行执行（#270），deep-research 的 synthesize 步骤禁用工具、避免报告撰写 agent 陷入循环（#271）。后续修复：workflow max-turns 上限修复（#272）、deep-research verdict 枚举修复（#273）、带阶段进度与各 agent 统计的 `/workflows` 实时监控（#287）。
- **2026-06-10：** **Web 工具链更新（#265）** —— 用基于 Tavily 的 WebSearch 取代已失效的 DuckDuckGo 抓取，并新增基于配置文件的密钥存储；WebFetch 重写为确定性的 markdown/text/html 提取（借鉴 opencode）。

📚 更早的条目已移至完整的 **[News 归档](../NEWS.md)**。

***

## 🎯 为什么是 ClawCodex？

**ClawCodex** 是一个**面向生产使用的 Claude Code Python 重写版**：从**真实的 TypeScript 架构**移植而来，并以**可用的 CLI Agent** 形式交付，而不只是一份源码镜像。

- **真实 Agent Runtime** —— 工具调用循环、流式 REPL、会话历史与多轮执行
- **高保真移植** —— 保留 Claude Code 的原始架构，同时做符合 Python 风格的实现
- **适合二次开发** —— 可读的 Python 代码、丰富的测试，以及基于 Markdown 的技能扩展
- **多 LLM 提供商** —— 相对上游最大的进展：Claude Code 仅围绕 Claude 系列模型构建，而 ClawCodex 致力于接入**所有主流 LLM 提供商**，让你为 agentic 编程选择最**灵活**、最**具性价比**的技术栈

**一个真正可跑的 Claude Code 风格 Python 终端工作流：流式回答、调用工具、抓取上下文，并通过 skills 扩展行为。**

**🚀 立即试用！Fork 它、修改它、让它成为你的！欢迎提交 Pull Request！**

***

## 🏆 SWE-bench Verified —— 相同模型下 `clawcodex` 超越 `openclaude`

![SWE-bench Verified —— clawcodex vs openclaude on Gemini 2.5 Pro](../../assets/swebench-verified-gemini.png)

在完整的 **SWE-bench Verified** 数据集（499 个实例，公开的 agent 编码榜单）上，两个 agent 均由 **Gemini 2.5 Pro** 驱动，运行在我们的标准化评测框架中：

| Agent | 已解决 | 未解决 | 错误 |
|---|---:|---:|---:|
| **clawcodex** | **291 / 499（58.2%）** | 124 | 84 |
| openclaude | 265 / 499（53.0%） | 144 | 90 |

- ✅ **两者都解决**：241 &nbsp;&nbsp; 🟢 **仅 clawcodex 解决**：50 &nbsp;&nbsp; 🔵 **仅 openclaude 解决**：24 &nbsp;&nbsp; ❌ **均未解决**：184

本地复现 —— 完整流程（累积分批、`--predict-workers N`、`--capture-traces`）见 [`eval/README.md`](../../eval/README.md)。

***

## ⭐ Star 历史

[在 star-history.com 查看 Star 历史图表](https://www.star-history.com/?repos=agentforce314%2Fclawcodex&type=date&legend=top-left)

## ✨ 特性

### 流式 Agent 体验

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

ClawCodex 的核心优势是**多提供商支持**：Claude Code 以 **Claude** 系列模型为目标，而我们希望在同一套 Agent 运行时之上支持**所有主流 LLM 提供商**——你可以自由切换厂商、区域与价位，而不必放弃工具、技能与编码闭环。正是这种灵活性，让 agentic 编程在规模化使用时真正可行。

```python
providers = [
    # 原生 / 专用协议
    "anthropic", "minimax", "deepseek", "zai", "openrouter", "openai", "gemini",
    # OpenAI 兼容厂商
    "nvidia-nim", "atlascloud", "wanjie-ark", "volcengine", "xiaomi-mimo",
    "novita", "fireworks", "siliconflow", "siliconflow-cn", "arcee", "moonshot",
    "huggingface", "together", "stepfun", "deepinfra",
    # 本地服务（无需 API Key）
    "ollama", "vllm", "sglang",
]  # 共 25 个 provider；nim、kimi、hf 等别名自动解析
```

新增任意 OpenAI 兼容厂商，只需在 `src/providers/openai_compatible_specs.py`
中加一行（base URL + 默认模型 + API Key 环境变量）。API Key 既可来自配置文件，
也可来自该 provider 的标准环境变量（如 `TOGETHER_API_KEY`、`MOONSHOT_API_KEY`），
因此大多数 provider 无需手动编辑 `config.json` 即可使用。

### 交互式界面（TypeScript Ink TUI）

交互界面为 **TypeScript Ink TUI** —— 一个终端客户端，它派生并托管一个 Python **agent-server** 子进程，通过管道（NDJSON）通信。直接运行 `clawcodex`（不带模式参数）即可启动；`clawcodex tui` 是其显式形式。（原先的进程内 Rich REPL 与 Textual TUI 已移除，统一改用这个更高保真度的客户端。）

```text
> 你好！
Assistant: 嗨！我是 ClawCodex，一个 Python 重实现...

> /help                       # 显示命令
> /theme dark                 # 切换配色主题
> @src/cli.py                 # @ 提及文件（模糊文件索引）
> /explain-code qsort.py      # 运行 SKILL.md 技能（或 /skill …）

# 需要 Node 18+ 与已构建的 ui-tui/dist（安装脚本会自动构建）；`clawcodex -p` 为无需 Node 的 headless 路径。
```

### 完整的 CLI

```bash
clawcodex                       # 交互式 Ink TUI（默认）
clawcodex tui                   # 交互式 Ink TUI（显式）
clawcodex login                 # 交互式配置 API key
clawcodex config                # 查看 ~/.clawcodex/config.json 中的配置
clawcodex --version             # 版本信息

# 非交互 / 脚本化（管道、CI、自动化 agent）
clawcodex -p "总结 src/cli.py"
clawcodex -p "Hello" --output-format json
clawcodex -p --output-format stream-json --input-format stream-json < events.ndjson

# 单次运行覆盖配置
clawcodex --provider anthropic --model claude-sonnet-4-6 -p "Hi"
clawcodex --max-turns 10 --allowed-tools Read,Grep -p "查找 TODO"

# 权限控制（REPL、TUI 与 -p 均生效）
clawcodex --permission-mode plan                       # plan / acceptEdits / dontAsk
clawcodex --dangerously-skip-permissions -p "ls"       # 跳过所有权限检查
clawcodex --allow-dangerously-skip-permissions         # 允许之后通过 /permission-mode 切换为 bypass
```

> **`--dangerously-skip-permissions`** 会在整个会话期间禁用所有工具权限检查。
> 仅建议在无互联网访问的沙箱容器/虚拟机中使用。当进程以 root/sudo
> 运行时该参数会被拒绝，除非设置了 `IS_SANDBOX=1` 或 `CLAUDE_CODE_BUBBLEWRAP=1`。

***

## 📊 状态

| 组件    | 状态     | 数量     |
| ----- | ------ | ------ |
| REPL 命令 | ✅ 完成   | 内置命令 + `/tools`、`/stream`、`/context`、`/compact`、技能等 |
| 工具系统 | ✅ 完成   | 30+ 工具 |
| 自动化测试 | ✅ 已覆盖  | 工具、agent loop、providers、parity、REPL、认证等 |
| 文档    | ✅ 完成   | 指南、多语言 README、[FEATURE_LIST.md](../../FEATURE_LIST.md) |

### 核心系统

| 系统 | 状态 | 描述 |
|------|------|------|
| CLI 入口 | ✅ | `clawcodex`、`clawcodex tui`、`login`、`config`、`-p` / `--print`、`--version` |
| 交互式界面 | ✅ | TypeScript Ink TUI（终端客户端 + Python agent-server 子进程）；斜杠命令、@ 文件提及、主题、权限对话框 |
| 多提供商支持 | ✅ | 25 个 provider —— Anthropic、OpenAI、Gemini、智谱 GLM、Minimax、OpenRouter、DeepSeek，外加 OpenAI 兼容 provider 注册表（NVIDIA NIM、Together、Novita、Fireworks、SiliconFlow、Moonshot/Kimi、DeepInfra、Hugging Face、火山引擎、StepFun、Arcee、AtlasCloud、小米 MiMo、万捷 Ark）以及本地服务（Ollama、vLLM、SGLang）。含 Anthropic→OpenAI 的 image / document 块转换，适配具备视觉能力的 OpenAI 兼容后端；每个 provider 均支持 API Key 环境变量回退 |
| 会话持久化 | ✅ | 本地保存/加载会话 |
| Agent Loop | ✅ | 工具调用循环；支持流式与无头模式 |
| Skill 系统 | ✅ | 基于 SKILL.md 的斜杠技能：参数与工具白名单 |
| 取消 / 中止 | ✅ | ESC 可在约 50ms 内中止进行中的 Bash、Grep/Glob 以及所有 provider 的流式 HTTP；子 agent 拥有隔离的 `AbortController`；`Bash` 的 `tool_result` 区分超时与 ESC 中止 |
| 图像处理 | ✅ | 与 TS 对齐的 Read 管线（魔数嗅探、按 API 限制缩放/压缩）；`@image.png` @-提及内联为 `ImageBlock`；`BaseProvider._prepare_messages` 中调用 API 前的 base64 大小校验；二进制 @-提及（PDF/zip/docx/…）转为 Read 工具提示而非乱码 |
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
| 其他 | LSP, Worktree, Skill, ToolSearch | ✅ 完成 |

### 路线图进度

- ✅ **阶段 0**：可安装、可运行的 CLI
- ✅ **阶段 1**：Claude Code 核心 MVP 体验
- ✅ **阶段 2**：真实工具调用闭环
- 🟡 **阶段 3**：上下文深度、权限集成、类 `/resume` 的恢复能力（进行中）
- 🟡 **阶段 4**：MCP 运行时深化、插件与可扩展性（工具已有，平台能力持续推进）
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

# 或：先装依赖再 editable 安装
# uv pip install -r requirements.txt && uv pip install -e .
```

### 配置

#### 方式 1：交互式（推荐）

```bash
clawcodex login
# 或: python -m src.cli login
```

这个流程会：

1. 让你选择 provider：anthropic / openai / gemini / zai / minimax / openrouter / deepseek，或任意 OpenAI 兼容厂商（together、novita、fireworks、moonshot、nvidia-nim、siliconflow、deepinfra、huggingface 等）以及本地服务（ollama / vllm / sglang）
2. 让你输入该 provider 的 API key
3. 可选：保存自定义 base URL
4. 可选：保存默认 model
5. 将该 provider 设为默认

配置文件保存在 `~/.clawcodex/config.json`。示例结构：

```json
{
  "default_provider": "deepseek",
  "providers": {
    "anthropic": {
      "api_key": "your-api-key",
      "base_url": "https://api.anthropic.com",
      "default_model": "claude-sonnet-4-6"
    },
    "openai": {
      "api_key": "your-api-key",
      "base_url": "https://api.openai.com/v1",
      "default_model": "gpt-5.4"
    },
    "zai": {
      "api_key": "your-api-key",
      "base_url": "https://api.z.ai/api/coding/paas/v4",
      "default_model": "glm-5.2"
    },
    "minimax": {
      "api_key": "your-api-key",
      "base_url": "https://api.minimax.io/anthropic",
      "default_model": "MiniMax-M3"
    },
    "openrouter": {
      "api_key": "your-api-key",
      "base_url": "https://openrouter.ai/api/v1",
      "default_model": "deepseek/deepseek-v4-pro"
    },
    "deepseek": {
      "api_key": "your-api-key",
      "base_url": "https://api.deepseek.com",
      "default_model": "deepseek-v4-pro"
    }
  },
  "session": {
    "auto_save": true,
    "max_history": 100
  },
  "settings": {
    "advisor_model": "claude-sonnet-4-6",
    "advisor_client_mode": false,
    "advisor_provider": "openai"
  },
  "env": {
    "TAVILY_API_KEY": "tvly-YOUR-TAVILY-API-KEY"
  }
}
```

内置 Minimax provider 会把 SDK base URL 传给 Anthropic SDK：全球区域使用
`https://api.minimax.io/anthropic`，中国区域使用
`https://api.minimaxi.com/anthropic`，SDK 会自动追加 `/v1/messages`。对应的原始
Messages API root 分别为 `https://api.minimax.io/anthropic/v1` 和
`https://api.minimaxi.com/anthropic/v1`。OpenAI 兼容 API root 分别为全球区域的
`https://api.minimax.io/v1` 和中国区域的 `https://api.minimaxi.com/v1`。

- **`session`** —— REPL 会话持久化：`auto_save` 自动保存每个会话；`max_history` 限制保留的对话轮数。
- **`settings`** —— 后台辅助功能所用的 advisor 模型（`advisor_provider` / `advisor_model`，以及控制是否经由客户端路由的 `advisor_client_mode`）。
- **`env`** —— 启动时注入的密钥与环境变量（例如用于 Web 搜索的 `TAVILY_API_KEY`）。通过 `clawcodex config` 管理；这里的键会被导出到进程环境，但不会覆盖你在 shell 中已设置的值。

### 运行

```bash
clawcodex                  # 启动交互式 Ink TUI（等同于 python -m src.cli）
clawcodex --help           # 全部参数：-p、--provider、--model 等
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
| `/skill` | 技能启动流程 |
| `/context` | 工作区 / 提示上下文（若可用） |
| `/compact` | 压缩或清空对话（不可用时回退为清空） |
| `/exit`、`/quit`、`/q` | 退出 |

### Skills（技能 / 斜杠命令）

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
when_to_use: 在解释代码如何工作时使用
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
- 参数替换：支持 `$ARGUMENTS`、`$0`、`$1`、以及命名参数（例如来自 `arguments` 的 `$path`）
- 占位符写法：请使用 `$path`，不要写成 `${path}`



***

## 🎨 演示

**[`demos/`](../../demos/) 目录下的每个应用都由 ClawCodex 自身端到端生成** —— 用的正是你刚安装的这个 CLI、同一个 agent loop、同一套工具。零人工修改 🙂

| 演示 | 技术栈 | 描述 |
| ---- | ----- | ----------- |
| [`demos/crm-app`](../../demos/crm-app) | React 18 + Vite + Vitest | 迷你 CRM：联系人、商机、仪表盘与完整测试套件 |
| [`demos/linkedin-app`](../../demos/linkedin-app) | React 18 + Vite + React Router | LinkedIn 风格信息流：个人主页、人脉、职位、私信 |
| [`demos/minecraft-app`](../../demos/minecraft-app) | React + three.js + @react-three/fiber | 浏览器体素沙盒：地形、挖掘、HUD 与玩家控制 |
| [`demos/wc26-intro`](../../demos/wc26-intro) | 纯静态 HTML/CSS/JS | 2026 世界杯介绍页——动效首屏、实时倒计时、主办国、16 座球场、赛制与破纪录数据；用全新的 Z.ai **GLM-5.2** 模型端到端生成 |

```bash
cd demos/crm-app   # 或 linkedin-app / minecraft-app
npm install
npm run dev        # vite 开发服务器
```

`demos/wc26-intro` 是单文件静态页面——直接在浏览器中打开 [`demos/wc26-intro/index.html`](../../demos/wc26-intro/index.html) 即可。

想看看它是怎么做到的？在任意空目录里打开 ClawCodex，让它构建点什么——上面这些都是这样生成的。

***

## 🎓 为什么选择 ClawCodex？

### 基于真实源码

- **不是克隆** —— 从真实的 TypeScript 实现移植而来
- **架构保真** —— 保持经过验证的设计模式
- **持续改进** —— 更好的错误处理、更多测试、更清晰的代码

### 原生 Python

- **类型提示** —— 完整的类型注解
- **现代 Python** —— 使用 3.10+ 特性
- **符合习惯** —— 干净的 Python 风格代码

### 以用户为中心

- **3 步设置** —— 克隆、配置（`clawcodex login`）、运行（`clawcodex`）
- **交互式配置** —— 一个流程内完成 provider、Base URL 与默认模型
- **Ink TUI** —— TypeScript 终端客户端 + Python agent-server 子进程
- **可脚本化** —— `-p` / JSON / NDJSON 便于自动化
- **会话持久化** —— 保存与恢复对话

***

## 架构

关于六大核心抽象（query loop、tools、tasks、两级 state、memory、hooks），以及从用户输入到模型输出的黄金路径，请见
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)。推荐新贡献者从这里入手。

原版 Claude Code 架构的参考资料位于
`claude-code-from-source/book/ch01-architecture.md`；逐章的移植差距分析与重构计划存放在
`my-docs/` 下。

***


## 📦 项目结构

```text
clawcodex/
├── src/
│   ├── cli.py              # CLI 入口（控制台命令 clawcodex）
│   ├── entrypoints/        # 无头（-p）、agent-server 与 Ink-TUI 启动器
│   ├── server/             # Direct Connect agent-server（Ink TUI 后端）
│   ├── providers/          # Anthropic、OpenAI、Gemini、智谱 GLM、Minimax、OpenRouter、DeepSeek + OpenAI 兼容注册表（openai_compatible_specs.py）
│   ├── agent/              # 对话、会话、提示词
│   ├── tool_system/        # Agent loop、工具与 schema
│   ├── skills/             # SKILL.md 加载与 Skill 工具
│   ├── services/           # MCP、compact、IDE 桥、工具执行等
│   ├── context_system/     # workspace / git / CLAUDE.md 上下文
│   ├── permissions/        # 权限模式与 bash 解析
│   ├── hooks/              # Hook 类型与执行辅助
│   └── command_system/     # 斜杠命令与参数替换
├── typescript/             # 参考 / 对等源码（运行 Python CLI 非必需）
├── tests/                  # pytest 测试套件
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

- **[SETUP_GUIDE.md](../guide/SETUP_GUIDE.md)** —— 详细安装说明
- **[CONTRIBUTING.md](../../CONTRIBUTING.md)** —— 开发指南
- **[TESTING.md](../guide/TESTING.md)** —— 测试指南
- **[CHANGELOG.md](../../CHANGELOG.md)** —— 版本历史
- **[TODOS.md](../../TODOS.md)** —— 已知差距与待办事项

***

## ⚡ 性能

- **启动时间**：< 1 秒
- **内存占用**：< 50MB
- **响应**：回合式助手输出，支持 Rich Markdown 渲染

***

## 🔒 安全

✅ **基础本地安全实践**

- Git 中无敏感数据
- API 密钥在配置中已做混淆
- `.env` 文件被忽略
- 适合本地开发工作流

***

## 📄 许可证

MIT 许可证 —— 查看 [LICENSE](../../LICENSE)

***

## 🙏 致谢

- 基于 Claude Code TypeScript 源码
- 独立的教育项目
- 未隶属于 Anthropic

***

<div align="center">

### 🌟 支持我们

如果你觉得这个项目有用，请给个 **star** ⭐！

**由 ClawCodex 团队用 ❤️ 打造**

[⬆ 回到顶部](#clawcodex)

</div>

***

***
