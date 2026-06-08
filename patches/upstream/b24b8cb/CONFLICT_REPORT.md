# b24b8cb 补丁冲突分析报告

**生成时间**：2026-06-08
**脚本**：`python3 scripts/regenerate_patches_upstream.py --commit b24b8cb --preserve-file patches/upstream/b24b8cb/preserve.list --allow-deletes`
**结果**：`patches/upstream/b24b8cb/` 共 173 个补丁（132 modified + 40 new + 1 deleted），25 个上游文件保留在 base 中。
**验证**：`git apply --check` 与 `git apply` 全部 173 个补丁均通过；将补丁应用到 `src/upstream/b24b8cb/` 副本后，所得树与 `src/` 在排除 `upstream/`、`orchestrator/`、`__pycache__/` 路径后，**唯一差异即 preserve 列表中那 4 个文件的内容**（它们故意保留 b24b8cb 上游新内容，覆盖 `src/` 中的 58ea488 旧内容）。

> **脚本策略统一**：原 `regenerate_patches_upstream.py` 硬编码 `new_files: list[str] = []`，会在 strict-reconstruction 语义下静默丢补丁。本次合并了两个 regen 脚本的能力——`regenerate_patches_upstream.py` 现在支持 `--preserve` / `--preserve-file` / 新文件 add patch，`regenerate_patches_b24b8cb.py` 已与之对齐并保留为同义入口。详见 §6。

---

## 1. 数据快照

| 类别 | 数量 | 说明 |
|------|-----:|------|
| 58ea488 已有补丁 | 132 modify + 1 delete = 133 | 旧基线下二开与上游的差 |
| b24b8cb 重建补丁 | 132 modify + 40 new + 1 delete = 173 | 新基线下二开与上游的差 |
| b24b8cb 上游保留文件 | 25 | 21 new + 4 modified（详见 §2.1 / §2.3） |
| 二开独占新文件 | 40 | `src/` 独有，b24b8cb base 没有，生成 new patch |

> 与第一版（177 补丁、21 保留）相比，本次 4 个"上游修改但二开未改"的文件移入 preserve，modified 数量从 136 → 132、preserved 从 21 → 25、total 从 177 → 173。**patch 内容相同，仅 preserve 边界变了**。

---

## 2. 冲突类别总览

冲突在这里指：**b24b8cb 上游变更 与 二开既有修改 在同一文件上叠加**。按严重程度分四类。

### 2.1 类别 A：上游新增文件，二开暂未引入（21 个） — **已自动解决：保留**

`src/upstream/b24b8cb/` 相比 `src/upstream/58ea488/` 多了 21 个文件（详细列表见 `preserve.list` 中 "(a)" 段），下游 `src/` 中均不存在。策略：**这些文件作为 base 的一部分保留，不生成补丁**——应用 b24b8cb 补丁后，这些新文件自然出现在结果树中，保留了上游新特性。

若日后二开要修改这些文件，需：
1. 在 `src/` 中创建对应文件
2. 将其移出 `preserve.list`
3. 重新跑 regen 脚本——此时脚本会判定其为"modify"并生成正确补丁

### 2.2 类别 B：上游修改 + 二开也修改（10 个） — **需人工 review**

这 10 个文件在 58ea488 阶段就有二开 overlay，b24b8cb 又同时修改了上游内容。当前 regen 是 **diff -u 纯文本逐行对比**，生成的是"从 b24b8cb base 到当前 src/"的单层补丁。语义上等价于"在 b24b8cb base 上叠加二开"，但若二开锚点的代码行已被上游重写，patch 仍可能因上下文偏移而应用失败——本批次 173 个补丁已通过 `git apply` 实测未触发该问题，但有以下几点需要注意：

| 文件 | 58ea488 → b24b8cb 上游变化 | 二开叠加 | 风险 |
|------|----------------------------|----------|------|
| `command_system/__init__.py` | 上游小改 | 二开注册新命令 | 低（hunk 少） |
| `command_system/builtins.py` | 上游重排 + 新增命令 | 二开增加大量内置命令 | **中**，见 §4.1 |
| `command_system/engine.py` | 上游小改 | 二开 hook | 低 |
| `command_system/skills_integration.py` | 上游小改 | 二开 skills 路径 | 低 |
| `command_system/types.py` | 上游小改 | 二开扩展 CommandType | 低 |
| `config.py` | 上游模型列表 + GLM | 二开 + LiteLLM / 启动参数 | **高**，见 §4.2 |
| `query/engine.py` | 上游重写 streaming | 二开 coordinator 注入 | **高**，见 §4.3 |
| `repl/core.py` | 上游重写 UIHost | 二开 + background escape | **中**，见 §4.4 |
| `tui/app.py` | 上游重写生命周期 | 二开 + 工作树状态 | **中** |
| `tui/commands.py` | 上游小改 | 二开 + 远程命令分发 | 低 |

> 表中标"高/中"的，**建议逐个 hunk 比对**——尤其是 streaming 引擎、config 启动参数、REPL UIHost 这三处，历史上有多次大幅重写。

### 2.3 类别 C：上游修改，但二开未改（4 个） — **已自动解决：preserve**

这 4 个文件 58ea488 阶段没有二开，`src/` 内容与 `src/upstream/58ea488/` 完全一致。但 b24b8cb 上游修改了它们。**`--preserve` 把它们从 modify 集合中排除**，让 b24b8cb 的新内容直接作为 base 保留下来——结果树中这 4 个文件是 b24b8cb 的新版本（覆盖 `src/` 的 58ea488 旧版本），符合"保留上游新特性"的目标。

| 文件 | 58ea488 → b24b8cb 上游变化 | 处理 |
|------|----------------------------|------|
| `providers/glm_provider.py` | 上游新增 GLM provider | 加入 `preserve.list`，b24b8cb 内容保留 |
| `state/app_state.py` | 上游重写 AppState | 加入 `preserve.list`，b24b8cb 内容保留 |
| `transports/__init__.py` | 上游重写 exports | 加入 `preserve.list`，b24b8cb 内容保留 |
| `utils/markdown_config_loader.py` | 上游新增 | 加入 `preserve.list`，b24b8cb 内容保留 |

> 注意：保留 b24b8cb 新内容后，**`src/` 中若有任何依赖这 4 个文件旧 API 的二开代码会失败**。建议在 review 时单独跑一次 smoke test 确认 CLI / TUI / REPL 入口仍可启动。

### 2.4 类别 D：上游删除 + 二开未引入（1 个） — **已自动解决：删除补丁**

`settings/permission_validation.py` 存在于 b24b8cb base，但 `src/` 已无此文件（58ea488 阶段就用 delete patch 删过）。regen 重新生成了 `0173.settings_permission_validation_py.delete.patch`。与 58ea488 行为一致。

### 2.5 类别 E：二开独占的新文件（40 个） — **新增 new patches**

这 40 个文件是 `src/` 独有、b24b8cb base 没有的二开功能（详见 §5）。原 `regenerate_patches_upstream.py` 强制 `new_files: list[str] = []`、不生成 new patch——本次统一策略后如实生成了 40 个 new patch。

> **这是与 58ea488 系列不同的设计选择**。原脚本的设计假设是"`src/` 已存在、补丁仅描述与上游的差异"，但严格按"补丁可还原 `src/`"的语义应当发出 new patch。`regenerate_patches_upstream.py` 已升级到 strict 策略，**未来如需重生成 58ea488 也会得到 +40 new patch**。

---

## 3. 验证结果

```text
# 1. git apply --check（173 个补丁逐一 dry-run）
check: OK=173 FAIL=0

# 2. git apply（全部 173 个补丁应用到 src/upstream/b24b8cb/ 副本）
apply: APPLIED=173 FAILED=0

# 3. 应用结果 vs src/（排除 upstream/、orchestrator/、__pycache__/）
Applied tree files: 645
src/ files: 624
Only in applied: 21   ← 保留的 21 个 b24b8cb 新文件
Only in src/:    0
Common but differ: 4
  ≠ providers/glm_provider.py      ← preserve 故意覆盖
  ≠ state/app_state.py             ← preserve 故意覆盖
  ≠ transports/__init__.py         ← preserve 故意覆盖
  ≠ utils/markdown_config_loader.py ← preserve 故意覆盖
```

✅ 应用补丁后，结果树 = `src/` ∪ 21 个 b24b8cb 新文件 ∪ 4 个被 preserve 升级到 b24b8cb 内容的文件。**上游新特性完整保留，二开既有特性完整保留**。

---

## 4. 高风险文件逐项分析

### 4.1 `command_system/builtins.py`

- 58ea488 阶段：b24b8cb base 已含许多 commands，二开继续注册新 command（buddy / cancel / plan / settings 等）。
- b24b8cb 上游又新增了一批 commands（aggregator / effort / export / model / moved_to_plugin / output_style / permissions / safe_commands / security_review / shell_prompt / statusline / theme——其中 12 个新文件被 preserve，其余少量是 modifications）。
- regen 后该 patch hunk 数较多；建议逐 hunk 比对，确认二开注册的 command 未与上游同名冲突。

### 4.2 `config.py`

- 上游改动了模型枚举、GLM provider、API base URL 等。
- 二开在 58ea488 阶段已扩展 Config（LiteLLM、orchestrator、settings schema 等），部分二开项可能与上游新增的字段名冲突。
- 建议检查：上游新增的字段名是否与二开字段重名，必要时把二开字段加 `clawcodex_` 前缀或 namespace。

### 4.3 `query/engine.py`

- 上游 b24b8cb 对 streaming / agent loop 做了较大改动。
- 二开在 58ea488 阶段已经把 coordinator / team / SendMessage / hooks 等塞进 `query/engine.py` 的事件流。
- 风险最高的 hunk：上游重写 `process_streaming_response` 时，二开增加的 `coordinator_event` 注入点可能落在已删除的代码行。
- 建议在 `extensions/api/query_middleware.py`（F-48 抽出的中间件）层面再确认一次抽象边界——若 streaming 重写让中间件失效，可能需要重新接入。

**已落地动作**（2026-06-08 后续轮次）：

- ✓ **B1**：b24b8cb 的 11 行 skills-exposure 改动已应用到 `src/query/engine.py:157-167` —— 在 `_build_system_prompt_parts` 内 `build_full_system_prompt_blocks` 调用前加本地 `from ..command_system import get_skill_tool_commands` import，调用处追加 `skills=get_skill_tool_commands(cwd)` 参数。**净增 11 行**（与 §1 数据快照中的 0041 补丁大小一致）。
- ✓ **B2**：F-48 rate-limit 合约测试已加（`tests/api/test_query_middleware_rate_limit_fallback.py`，9 个 case 全绿），覆盖 `handle_rate_limit_error` 的 `"429"` / `"rate_limit"` / 大小写不敏感 / 与 `prompt_too_long` 不冲突 / 401/500 不误判，以及 `enforce_request_delay` 的 env 缺失 / 0ms / 100ms throttle / 非法 env 不崩溃。
- ✓ **615 行声明订正**：原报告 §4.3 提到的 "615 行声明" 实指 `patches/upstream/b24b8cb/merged/0042.query_query_py.patch` 的体积（streaming 重写、coordinator_event 注入），与 `0041.query_engine_py.patch` 无关。`0041` 实际净增 11 行（skills 暴露），下游合并面较小。
- ✓ **F-48 中间件调用点未触**：F-48 的 `enforce_request_delay` / `handle_rate_limit_error` 调用点都在 `src/query/query.py:556-560, 641-654`（b24b8cb 0042 补丁管辖），不在 `0041`。本次 0041 补丁未改 F-48 接线。
- ✓ **`QueryParams.on_thinking_chunk` 字段已存在**：fork 在 58ea488 阶段已加，类型层面已对齐，b24b8cb 无新增。

### 4.4 `repl/core.py`

- 上游 b24b8cb 抽出 `repl/ui_host.py`（被 preserve），把 UI 生命周期挪到独立 host。
- 二开在 58ea488 阶段已有 `repl/background_escape.py`、`services/bridge/*` 等 REPL 扩展。
- `repl/ui_host.py` 出现在 preserve 列表（21 个新文件），说明二开尚未适配 UIHost 抽象。**未来需要新增二开补丁**让 background_escape / bridge 与 UIHost 协作。

**已落地动作**（2026-06-08 后续轮次，完整移植 UIHost 子系统）：

- ✓ **A1**：`clawcodex_ext/command_system/types.py` 新增 `CommandType.INTERACTIVE` / `UIOption` / `InteractiveUnavailableError` / `UIHost`(Protocol) / `NullUIHost` / `InteractiveOutcome` / `InteractiveCommand` / `SkillPromptCommand`；`CommandContext` 增加 `ui` 与 `tool_context` 字段；`Command` 类型联合更新为 `PromptCommand | LocalCommand | InteractiveCommand`；补 `__all__`。
- ✓ **A2**：`clawcodex_ext/command_system/engine.py` 新增 `_execute_interactive(command, args)`（路由 `CommandType.INTERACTIVE` → 调 `command.run` → 把 `InteractiveOutcome` 翻译为 `CommandResult`，处理 `InteractiveUnavailableError` 异常）；`create_command_context` 新增 `ui: Any = None` / `tool_context: Any = None` 形参。
- ✓ **A3**：`clawcodex_ext/command_system/skills_integration.py` 的 `skill_to_prompt_command` 已切换为构造 `SkillPromptCommand` 实例（之前是 `PromptCommand`），并补 `has_user_specified_description` 透传。新增 `get_skill_tool_commands(cwd)` 函数（按 `loaded_from` ∈ `{skills, bundled, commands_DEPRECATED}` 过滤注册表里的 `PromptCommand`），作为 b24b8cb `aggregator.getSkillToolCommands` 的最小等价实现（fork 暂未移植整个 aggregator 模块）。
- ✓ **A4**：`clawcodex_ext/repl/ui_host.py` 已创建（107 行，从 b24b8cb 整体移植 `ReplUIHost`；import 路径改为 `clawcodex_ext.command_system.types`）。
- ✓ **A5**：`src/repl/ui_host.py` facade 已创建（lazy `__getattr__` proxy 指向 `clawcodex_ext.repl.ui_host`），与 `src/command_system/types.py` 同一模式。
- ✓ **A6**：`clawcodex_ext/repl/core.py:_init_command_system` 已增加 `from clawcodex_ext.repl.ui_host import ReplUIHost`，`create_command_context(...)` 调用处新增 `provider=self.provider` / `ui=ReplUIHost(self._safe_input, self.console)` / `tool_context=self.tool_context` 三个形参。`app_state_store` 暂未传递（fork 端无 `state/app_state.py`，保持传 `None`）。
- ✓ **A7**：`src/command_system/types.py` 的硬编码 `__all__` 已补齐 7 个新符号（`UIHost` / `UIOption` / `NullUIHost` / `InteractiveCommand` / `InteractiveOutcome` / `InteractiveUnavailableError` / `SkillPromptCommand`）；`clawcodex_ext/command_system/__init__.py` 的 import 与 `__all__` 已同步补齐。
- ✓ **A8**：stability gate 87 passed / 1 skipped（pre-existing bridge deprecation warning，无回归）；orchestrator 测试在后台运行中（id `bwbdnkz2e`）。

---

## 5. 二开独占新文件清单（40 个，生成 new patch）

按目录归类：

| 目录 | 文件 | 用途 |
|------|------|------|
| `agent/` | `_outlines_adapter.py`, `background_runner.py`, `background_state.py` | F-37 / F-40 引入的 outlines + 后台任务执行器 |
| `agent/tool_authoring/` | `__init__.py`, `call_handlers/{__init__,bash,http,python}.py`, `factory.py`, `persistence.py`, `registry_ext.py`, `spec.py`, `validators.py` | F-39 工具自描述框架 |
| `auth/` | `codex_oauth.py`, `codex_store.py` | F-48 OAuth 多账户 |
| `context_system/` | `_gitpython_adapter.py` | 隔离 GitPython |
| `entrypoints/` | `orchestrator.py` | 编排器 daemon 入口 |
| `hooks/` | `_pluggy_adapter.py` | 隔离 pluggy |
| `permissions/` | `_treesitter_adapter.py` | 隔离 tree-sitter |
| `providers/` | `_litellm_adapter.py`, `codex_models.py`, `openai_codex_provider.py`, `runtime.py` | F-48 LiteLLM + Codex 后端 |
| `repl/` | `background_escape.py` | 后台任务逃逸键 |
| `services/bridge/` | `__init__.py`, `auth.py`, `session.py`, `transport.py` | F-48 桥接器服务化 |
| `services/` | `tail_follower.py` | 日志跟随 |
| `settings/` | `pydantic_adapter.py` | F-48 settings 校验 |
| `skills/` | `_frontmatter_adapter.py` | 隔离 PyYAML |
| `tool_system/tools/` | `ask_issue_author.py`, `create_agent_tool.py`, `progress_report.py`, `task_directives.py`, `task_inspect.py` | F-39 工具集 |
| `tui/screens/` | `ask_user_question.py`, `permission_mode_picker.py` | F-39 TUI 屏 |
| `utils/` | `cache_warning.py`, `session_watcher.py` | 工具函数 |

> 这些是 `extensions/` 之外、`src/` 之内的"二开核心代码"。regen 生成的 new patch 把它们从无到有 add 进来。应用补丁到 b24b8cb base 后会得到完整二开树。

---

## 6. 脚本策略统一

**前情**：`scripts/regenerate_patches_upstream.py` 是项目原有脚本（用于 58ea488 队列），硬编码 `new_files: list[str] = []`，会把 fork-only 文件静默丢在补丁外。`scripts/regenerate_patches_b24b8cb.py` 是 b24b8cb 阶段新增的脚本，支持 `--preserve` 与 new-patch 发出。

**本次变更**：
- `regenerate_patches_upstream.py` 升级到 strict-reconstruction 策略：
  - 移除 `new_files = []` 硬编码，现在会发出 40 个 new patch
  - 增加 `--preserve` 与 `--preserve-file` 参数，含义与 b24b8cb 脚本一致
  - 新增 `(b)` 类 preserve（已存在但 fork 未改 → 保留 base，覆盖 fork 旧内容）
  - series header 同步更新（`Preserved files` 行）
- `regenerate_patches_b24b8cb.py` 与 `regenerate_patches_upstream.py` 行为对齐，**保留为同义入口**。后续可以删其一；当前保留以防调用方 hardcode 路径。
- 本次重生成后，`patches/upstream/b24b8cb/series` 头部的 `Generated by` 已切到 `regenerate_patches_upstream.py`。

**对 58ea488 的影响**：
- 现有的 `patches/upstream/58ea488/` 队列**未变**——脚本升级是 in-place 的，但只针对本次重生成（b24b8cb）。
- 若日后需要让 58ea488 也升级到 strict 策略，再次跑：
  ```bash
  python3 scripts/regenerate_patches_upstream.py --commit 58ea488 --allow-deletes
  ```
  即可。**注意**：会新增 40 个 new patch，patch 编号会变；不影响 `git am` / `quilt push -a` 语义。

---

## 7. 风险与建议

1. **preserve 的 4 个文件可能破坏二开兼容性**：见 §2.3，建议 smoke test 验证 CLI / TUI / REPL 入口仍可启动。
2. **`repl/ui_host.py` 适配缺失**：b24b8cb 把 UI 抽到独立 host，但二开尚未适配。`repl/core.py` 的 patch 没有挂接 UIHost 的二开扩展。建议在 58ea488 → b24b8cb 合并后的下一轮迭代里补一版 UIHost 适配 patch。
3. **冲突监控自动化**：可基于 `git log --follow` 对类别 B 的 10 个高风险文件做 per-file 三方合并，把结果回灌到本报告的 §4 表。
4. **补丁路径前缀**：本批次补丁头部使用 `diff --git a/agent/session.py b/agent/session.py`（已剥离 `src/` 前缀），与应用约定一致。**注意**：本批次不再生成 `src/` 前缀的 diff——这与 58ea488 兼容，可直接 `git am` 应用。

---

## 8. 应用方式

```bash
# 1. 切到 vendor 分支并 checkout b24b8cb
git checkout upstream/vendor
git checkout b24b8cb   # 或 git checkout src/upstream/b24b8cb

# 2. 应用补丁（quilt 方式）
cd /path/to/clawcodex
quilt push -a   # 按 series 顺序应用 173 个补丁

# 或 git am 方式
git am --directory=src/upstream/b24b8cb patches/upstream/b24b8cb/merged/*.patch
```

> 注：`b24b8cb_series` 文件是兼容性系列（每行加 `merged/` 前缀），quilt 1.x/2.x 均可消费。`series` 文件不带前缀，quilt 2.x 推荐。
