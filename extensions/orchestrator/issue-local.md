---
# =============================================================================
# ClawCodex Local Tracker — Issue Card Template
# =============================================================================
# 用于 extensions/orchestrator 的 LocalTrackerAdapter 场景。
# 把 <...> 占位符替换成实际值后存为 <ISSUES_PATH>/<FILE_STEM>.md。
#
# 字段说明（必读 frontmatter 由 extensions/orchestrator/local_tracker/parser.py 解析）：
#
#   id              内部 ID，文件内唯一。建议用 "<FEATURE>-<SUB>" 形式，
#                   例如 F-37.1 → "F-37.1-pr-auto-fix"
#   identifier      对外标识（branch 命名、Jinja 模板里 {{ issue.identifier }}），
#                   短而稳定，例如 "F-37.1"
#   title           一句话标题，issue 列表和 commit message 都会用
#   state           决定是否被轮询：
#                     - open / ready      活跃，orchestrator 会拉
#                     - in_progress       已被占，正在跑（可手填也可由 orchestrator 写回）
#                     - completed / closed / cancelled / failed / abandoned  终态，跳过
#                   详见 tracker.py: default_active_states_for_kind / terminal_states_for_kind
#   priority        数字越小越靠前（0/1 = P0/P1）。可选，留空则按 id 字典序
#   labels          标签列表。F-39 重用意图（写在 labels 里）：
#                     - agent:retry       重置 + 清旧 PR 字段、重跑
#                     - agent:follow-up   保留分支，追加 commit
#                     - agent:blocked     永久跳过（最高优先级，unblock 才会放开）
#                   也可放普通分类标签：feature / bug / refactor / docs ...
#   branch_name     期望的工作分支名。orchestrator 优先用这个；留空则按
#                   <branch_prefix>/<id>-<slug> 自动生成
#                   （见 git_sync.py:_default_branch_name）
#   base_branch     期望的基线分支，例如 dev-decoupling / main / master
#                   （见 git_sync.py:132，优先于 repo 默认分支）
#   assignee_id     负责人 / 团队，追踪用
#   url             原始链接（如果有上游 issue / 文档）
#   created_at / updated_at  ISO8601，例如 2026-06-01T10:00:00Z
#
# body 字段（自由 markdown，agent 会在工作目录里直接读）建议包含：
#   ## 背景 / 目标 / 验收标准 / 风险与约束 / 不要做 / 关联
# =============================================================================

id: <ID>                                # 例如 F-37.1-pr-auto-fix
identifier: <IDENTIFIER>                # 例如 F-37.1
title: <TITLE>                          # 一句话标题，例如 "实现 PR review 评论自动修复的最小闭环"
state: open                             # open | ready | in_progress | completed | closed | cancelled | failed | abandoned
priority: <0|1|2|3>                     # 可选，越小越靠前
labels:
  - feature                             # 至少一个分类标签
  - <CATEGORY_TAG>                      # 例如 review-auto-fix / docs / refactor
branch_name: <BRANCH_NAME>              # 例如 feature/f-37-pr-auto-fix；留空让 orchestrator 自动生成
base_branch: <BASE_BRANCH>              # 例如 dev-decoupling
assignee_id: <ASSIGNEE>
url: <UPSTREAM_URL>                     # 可选
created_at: <ISO8601>                   # 例如 2026-06-01T10:00:00Z
updated_at: <ISO8601>                   # 例如 2026-06-01T10:00:00Z
---

# <TITLE>

## 背景

为什么要做这件事？来源是 `docs/FEATURE_PLAN.md` 的哪一段？
有没有上游 issue / 用户反馈 / 性能数据支撑？

## 目标

一句话说清楚「完成后的可观测行为」。

## 子特性 / 任务拆分

- [ ] 子任务 1
- [ ] 子任务 2
- [ ] 子任务 3

## 验收标准

- 给出可执行的检查项
- 例如：单元测试 `pytest tests/test_xxx.py::test_yyy` 通过
- 例如：在 <具体场景> 下观察到 <具体行为>
- 例如：文档 / CHANGELOG 同步更新

## 风险与约束

- 兼容性影响（哪些调用方会受影响）
- 性能 / 资源开销
- 安全 / 权限边界
- 已知不能处理的边界情况

## 不要做

- 不要顺手改无关文件
- 不要重构 `<具体模块>`，留到独立 issue
- 不要修改 `extensions/orchestrator/tracker.py` 的 `TrackerAdapter` 接口
- 不要提交敏感信息（token、密钥、个人信息）

## 关联

- 上游设计文档：`docs/FEATURE_PLAN.md` 第 X 节
- 相关 issue：`<其他 issue 的 identifier>`
- 相关 PR：<链接>

## 备注

自由记录：参考资料、灵感、待办等。
