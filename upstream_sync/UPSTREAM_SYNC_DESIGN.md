# 上游同步组件解耦设计文档

> 文档路径: `docs/UPSTREAM_SYNC_DESIGN-decoupling.md`
> 版本: v1.2
> 更新日期: 2026-06-02
> 关联文档: [UPSTREAM_SYNC_DESIGN.md](UPSTREAM_SYNC_DESIGN.md), [FEATURE_PLAN.md](FEATURE_PLAN.md), [PROGRESS.md](PROGRESS.md)

## v1.2 变更日志 (2026-06-02)

> 本次更新对齐 `upstream_sync/` 当前实现（commit 656c94f 起，至 522e4e5 / 183264c / 823946c 累积），相比 v1.1 (2026-05-19) 主要变化：

- **新增** `core/sync_orchestrator.py` —— 端到端 sync 流程编排器，支持 lifecycle hooks
- **新增** `core/patch_generator.py` —— 基于旧 patch 模式生成新上游 commit 的 patch
- **新增** `core/backup_manager.py` —— `src/` 目录的备份与恢复（升级前快照）
- **新增** `core/verifier.py` —— 验证新旧 patch 在不同上游版本间的功能等价性
- **新增** CLI 命令：`extract`、`generate-patch`、`backup`、`restore`、`backup-list`、`verify`、`upgrade`
- **扩展** `UpstreamConfig` 新增 `source_subpath` 字段（默认 `src`），用于按子路径提取上游代码
- **扩展** `PatchConfig.patch_subdir` 支持 per-commit 子目录结构（`patches/upstream/{commit}/`）
- **扩展** `VendorManager` 新增 `fetch_ref` / `extract_to_path` / `list_version_tags` / `reset_vendor_to_upstream` / `detect_sync_refs`
- **新增** `hooks/base.py` —— `SyncHooks` 基类，pre_/post_ 四阶段钩子
- **增强** `LayerAuditor` —— `_is_forbidden` 同时支持 forbidden 与 allowed 双向检查
- **增强** `apply` 命令支持 `--commit` 自动检测，per-commit subdir 自动解析 series 文件
- **增强** `sync` 命令通过 `SyncOrchestrator.run_full_sync` 执行，支持 `--auto` 条件应用
- **新增** 「八、升级工作流 (Upgrade Workflow)」章节，描述推荐的端到端 upgrade 流程

---

## 目录

- [一、解耦目标与原则](#一解耦目标与原则)
- [二、核心解耦策略](#二核心解耦策略)
- [三、通用组件架构 (upstream-sync)](#三通用组件架构-upstream-sync)
  - [3.1 组件目录结构](#31-组件目录结构)
  - [3.2 配置层 (config.py)](#32-配置层-configpy)
  - [3.3 Vendor 管理 (core/vendor.py)](#33-vendor-管理-corevendorpy)
  - [3.4 Patch 引擎 (core/patch_engine.py)](#34-patch-引擎-corepatch_enginepy)
  - [3.5 Patch 生成器 (core/patch_generator.py)](#35-patch-生成器-corepatch_generatorpy)
  - [3.6 同步编排器 (core/sync_orchestrator.py)](#36-同步编排器-coresync_orchestratorpy)
  - [3.7 备份管理 (core/backup_manager.py)](#37-备份管理-corebackup_managerpy)
  - [3.8 Patch 验证器 (core/verifier.py)](#38-patch-验证器-coreverifierpy)
  - [3.9 变化分析器 (core/change_analyzer.py)](#39-变化分析器-corechange_analyzerpy)
  - [3.10 层间审计 (core/layer_auditor.py)](#310-层间审计-corelayer_auditorpy)
  - [3.11 报告生成 (reporters/)](#311-报告生成-reporters)
  - [3.12 生命周期钩子 (hooks/)](#312-生命周期钩子-hooks)
  - [3.13 CLI (cli.py)](#313-cli-clipy)
- [四、ClawCodex 集成方式](#四clawcodex-集成方式)
  - [4.1 配置文件示例](#41-配置文件示例)
  - [4.2 保留在 ClawCodex 内的内容](#42-保留在-clawcodex-内的内容)
  - [4.3 调用关系](#43-调用关系)
- [五、迁移路径](#五迁移路径)
- [六、Agent 集成](#六agent-集成)
- [七、关键设计决策](#七关键设计决策)
- [八、升级工作流 (Upgrade Workflow)](#八升级工作流-upgrade-workflow)
- [附录 A: 配置完整参考](#附录-a-配置完整参考)
- [附录 B: 术语表](#附录-b-术语表)

---

## 一、解耦目标与原则

### 1.1 为什么要解耦

原 [UPSTREAM_SYNC_DESIGN.md](UPSTREAM_SYNC_DESIGN.md) 是一份面向 ClawCodex 自身需求的优秀架构设计，但存在以下耦合问题，导致无法直接复用于其他项目：

1. **硬编码项目名称**："ClawCodex"、"anthropics/claude-code" 遍布全文
2. **硬编码目录结构**：`src/upstream/`、`src/capabilities/` 等路径写死
3. **特定领域协议**：`AgentLoop`、`ToolRegistry`、`LLMProvider` 是 Claude Code 特有概念，不具通用性
4. **特定修改类型**：TS→Python 移植等 Patch 内容被内嵌到设计方案中
5. **Agent 绑定**：Prompt 模板完全围绕 ClawCodex 场景编写

### 1.2 解耦目标

将上游同步系统拆分为两个正交的部分：

| 部分 | 职责 | 复用范围 |
|------|------|---------|
| **upstream-sync (通用组件)** | 管理上游代码同步的通用机制 | 任何需要追踪上游代码的 Fork/移植项目 |
| **ClawCodex (业务项目)** | 定义自身特有的 Layer Protocol、Patch 内容、目录结构 | 仅 ClawCodex |

### 1.3 设计原则

1. **机制与策略分离**：组件只保留机制（Patch 管理、层间审计、变化分析），策略（Protocol 定义、Patch 内容、Agent Prompt 细节）留给使用者
2. **配置驱动**：零硬编码，所有项目特定的信息通过 `upstream-sync.yaml` 配置
3. **协议无关**：组件不定义任何业务 Protocol，只提供审计框架让使用者注入自己的验证逻辑
4. **Agent 友好**：输出标准化、可机器解析的上下文，不绑定特定 Agent
5. **层数不限**：支持任意数量的层，不限于固定的三层模型

---

## 二、核心解耦策略

### 2.1 识别机制与策略

| 文档中的内容 | 类型 | 解耦后归属 |
|---|---|---|
| 三层隔离依赖规则 | **机制** | 通用组件 (`layer_auditor.py`) |
| Patch 应用/管理/冲突检测 | **机制** | 通用组件 (`patch_engine.py`) |
| Vendor Branch + 版本锁定标签 | **机制** | 通用组件 (`vendor.py`) |
| 变化分析 + 影响报告 | **机制** | 通用组件 (`change_analyzer.py`) |
| Agent Prompt 模板生成 | **机制** | 通用组件（参数化模板） |
| `AgentLoop` / `ToolRegistry` Protocol | **策略** | ClawCodex (`src/capabilities/`) |
| `src/upstream/` `src/capabilities/` 目录结构 | **策略** | ClawCodex 配置 |
| `anthropics/claude-code.git` 上游地址 | **策略** | ClawCodex 配置 |
| TS→Python 移植 Patch | **策略** | ClawCodex `patches/` |

### 2.2 解耦后的依赖关系

```
upstream-sync (通用组件)
    │ 零业务耦合，通过 YAML 配置驱动
    ▼
upstream-sync.yaml (配置文件)
    │ 定义项目结构、层规则、上游地址
    ▼
ClawCodex (业务项目)
```

---

## 三、通用组件架构 (upstream-sync)

### 3.1 组件目录结构

```
upstream-sync/                          # 独立仓库 / PyPI 包
├── pyproject.toml
├── README.md
├── src/upstream_sync/
│   ├── __init__.py                     # 暴露 __version__ = "0.1.0"
│   ├── __main__.py                     # python -m upstream_sync 入口
│   ├── cli.py                          # 统一 CLI 入口 (Typer, 11 个命令)
│   ├── config.py                       # Pydantic 配置模型
│   ├── core/
│   │   ├── __init__.py
│   │   ├── vendor.py                   # Vendor Branch / ref 拉取 / 子路径提取
│   │   ├── patch_engine.py             # Patch 应用引擎抽象 + factory
│   │   ├── patch_generator.py          # 基于旧 patch 模式生成新 patch
│   │   ├── sync_orchestrator.py        # 端到端 sync 流程编排 + hooks
│   │   ├── backup_manager.py           # src/ 备份与恢复
│   │   ├── verifier.py                 # Patch 功能等价性验证
│   │   ├── change_analyzer.py          # 上游 diff 分析器
│   │   └── layer_auditor.py            # 层间依赖审计 (AST + 双向规则)
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── quilt.py                    # Quilt 适配器
│   │   ├── git_am.py                   # Git am 适配器 (支持 series 与 glob)
│   │   └── custom.py                   # 自定义命令适配器 (支持 JSON 输出)
│   ├── reporters/
│   │   ├── __init__.py
│   │   ├── json_reporter.py            # 机器可读报告
│   │   └── markdown_reporter.py        # 人类可读报告
│   ├── templates/
│   │   └── agent_prompt.md.j2          # Agent Prompt Jinja2 模板
│   └── hooks/
│       ├── __init__.py
│       └── base.py                     # SyncHooks 生命周期钩子基类
└── tests/
    ├── test_change_analyzer.py
    ├── test_cli.py
    ├── test_layer_auditor.py
    ├── test_patch_engine.py
    └── test_vendor.py
```

### 3.2 配置层 (config.py)

整个组件由单一配置文件驱动，零硬编码。

```python
# upstream_sync/config.py
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Literal


class LayerConfig(BaseModel):
    """层配置：通用组件只检查依赖方向，不定义层内容。"""
    name: str = Field(..., description="层名称，如 upstream, capabilities, features")
    paths: list[Path] = Field(..., description="该层包含的目录/文件路径")
    allowed_imports_from: list[str] = Field(default_factory=list,
        description="允许从此层导入的模块前缀列表")
    forbidden_imports_from: list[str] = Field(default_factory=list,
        description="禁止从此层导入的模块前缀列表（优先于 allowed）")


class UpstreamConfig(BaseModel):
    remote_url: str = Field(..., description="上游仓库 URL")
    main_branch: str = "main"
    vendor_branch: str = "upstream/vendor"
    version_tag_format: str = "upstream/v{YYYY}_{MM}"
    source_subpath: str = Field(
        default="src",
        description="上游仓库内要提取的子路径（如 'src' 表示只提取 src/ 目录）",
    )


class PatchConfig(BaseModel):
    """补丁队列配置。

    支持两种组织结构：

    1. **扁平结构（遗留）**：所有 patch 放在单一目录。
       - directory: "patches"
       - series_file: "patches/series"

    2. **per-commit 子目录（推荐）**：每个上游 commit 一个子目录，
       包含该 commit 的 patch 与 series 文件。
       - directory: "patches/upstream"
       - series_file: "patches/upstream/{commit}/{commit}_series"
       - patch_subdir: "patches/upstream/{commit}"

    ``{commit}`` 占位符在 apply 时从上游 version_tag 解析
    （如 "b125e16"）。
    """
    directory: Path = Path("patches")
    engine: Literal["quilt", "git-am", "custom"] = "quilt"
    custom_command: str | None = None   # engine=custom 时使用
    series_file: Path = Path("patches/series")
    metadata_dir: Path = Path("patches/metadata")
    # 可选：每个 upstream commit 对应一个子目录
    # 示例："patches/upstream/{commit}" 解析为 "patches/upstream/b125e16"
    patch_subdir: str | None = Field(
        default=None,
        description="per-commit 补丁子目录模式，使用 {commit} 占位符。"
                    "设置后，patch 从该子目录加载（而非 directory）。",
    )


class SyncConfig(BaseModel):
    impact_threshold_auto: str = "low"      # 低于此阈值自动处理
    impact_threshold_agent: str = "medium"  # 低于此阈值 Agent 辅助
    report_formats: list[str] = ["json", "markdown"]


class ProjectConfig(BaseModel):
    """使用者项目的完整配置。"""
    project_name: str
    source_lang: str = "python"
    upstream: UpstreamConfig
    layers: list[LayerConfig]           # 任意数量的层
    patches: PatchConfig
    sync: SyncConfig
```

> **新增说明（v1.2）**：`UpstreamConfig.source_subpath` 控制 `extract` / `generate-patch` / `upgrade` 命令只处理上游仓库内的指定子目录（默认 `src`），使 vendor 树保持干净。`PatchConfig.patch_subdir` 与 `directory` / `series_file` 配合实现 per-commit 隔离，详见 §3.4.1。

### 3.3 Vendor 管理 (core/vendor.py)

管理上游仓库镜像、版本标签、子路径提取与 sync ref 自动检测。完全通用，不感知业务。

```python
# upstream_sync/core/vendor.py
import io
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

from upstream_sync.config import UpstreamConfig


class VendorManager:
    def __init__(self, repo_root: Path, upstream: UpstreamConfig):
        self.repo_root = repo_root
        self.cfg = upstream

    # ----- Remote lifecycle -----

    def ensure_remote(self) -> None:
        """添加上游 remote（如不存在）。"""
        result = subprocess.run(
            ["git", "remote", "get-url", "upstream"],
            cwd=self.repo_root,
            capture_output=True, text=True
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "remote", "add", "upstream", self.cfg.remote_url],
                cwd=self.repo_root, check=True
            )

    # ----- Fetch -----

    def fetch(self) -> str:
        """拉取上游 main，返回最新 commit hash。"""
        subprocess.run(
            ["git", "fetch", "upstream", self.cfg.main_branch],
            cwd=self.repo_root, check=True
        )
        return self._rev_parse(f"upstream/{self.cfg.main_branch}")

    def fetch_ref(self, ref: str) -> str:
        """拉取指定的 ref（commit / tag / branch），返回 commit hash。

        与 ``fetch()`` 区别：``fetch`` 始终拉 main_branch；
        ``fetch_ref`` 可拉任意上游 ref，用于 upgrade/extract 流程。
        """
        subprocess.run(
            ["git", "fetch", "upstream", ref],
            cwd=self.repo_root, check=True
        )
        return self._rev_parse(f"upstream/{ref}")

    # ----- Sub-path extraction -----

    def extract_to_path(
        self,
        ref: str,
        subpath: str,
        target_path: Path,
        use_archive: bool = True,
    ) -> None:
        """将上游 ref 内 *subpath* 子目录的内容提取到 *target_path*。

        例如 ``extract_to_path("abc123", "src", Path("src/upstream/abc123"))``
        将 ``upstream/src/*`` 提取到 ``src/upstream/abc123/*``（不含 ``src/`` 前缀）。

        Args:
            ref: 上游 ref。
            subpath: 上游仓库内的子目录（如 ``"src"``）。
            target_path: 本地目标目录。提取后该目录内是 subpath 内的内容，
                         而不是 subpath 本身。
            use_archive: True 时使用 ``git archive``（高效，仅取元数据）；
                         False 时 fallback 到 checkout + copy。
        """
        target_path.mkdir(parents=True, exist_ok=True)
        if use_archive:
            proc = subprocess.run(
                ["git", "archive", "--prefix=", f"upstream/{ref}"],
                cwd=self.repo_root, capture_output=True, check=True,
            )
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
                for member in tar.getmembers():
                    if not member.name.startswith(f"{subpath}/"):
                        continue
                    member.name = member.name[len(subpath) + 1:]
                    if member.name:
                        tar.extract(member, target_path)

    # ----- Version tags -----

    def create_version_tag(self, version: str, commit: str) -> None:
        """创建版本锁定标签，如 upstream/v2025_06。"""
        from datetime import datetime
        dt = datetime.strptime(version, "%Y.%m.%d")
        tag = self.cfg.version_tag_format.format(
            YYYY=dt.year, MM=f"{dt.month:02d}"
        )
        subprocess.run(
            ["git", "tag", tag, commit],
            cwd=self.repo_root, check=True
        )

    def list_version_tags(self) -> list[str]:
        """列出本地所有 ``upstream/*`` 版本标签。"""
        result = subprocess.run(
            ["git", "tag", "--list", "upstream/*"],
            cwd=self.repo_root, capture_output=True, text=True,
        )
        return [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]

    # ----- Vendor branch -----

    def checkout_vendor(self) -> None:
        """切换到 vendor 分支（不存在则创建）。"""
        result = subprocess.run(
            ["git", "branch", "--list", self.cfg.vendor_branch],
            cwd=self.repo_root, capture_output=True, text=True
        )
        if not result.stdout.strip():
            subprocess.run(
                ["git", "checkout", "-b", self.cfg.vendor_branch],
                cwd=self.repo_root, check=True
            )
        else:
            subprocess.run(
                ["git", "checkout", self.cfg.vendor_branch],
                cwd=self.repo_root, check=True
            )

    def reset_vendor_to_upstream(self, commit: str | None = None) -> None:
        """硬重置 vendor 分支到指定上游 commit（默认 FETCH_HEAD）。"""
        ref = commit or f"upstream/{self.cfg.main_branch}"
        subprocess.run(
            ["git", "checkout", self.cfg.vendor_branch],
            cwd=self.repo_root, check=True
        )
        subprocess.run(
            ["git", "reset", "--hard", ref],
            cwd=self.repo_root, check=True
        )

    # ----- Auto-detection -----

    def detect_sync_refs(self) -> tuple[str, str]:
        """自动检测 sync 所需的 (previous_ref, latest_ref)。

        - latest_ref 固定为 ``upstream/<main_branch>``。
        - previous_ref 为最新的本地 ``upstream/v*`` tag；
          若不存在则回退到 vendor 分支 tip；都没有则 latest==previous
          （首次 sync 时所有文件视作新增）。

        Raises:
            RuntimeError: 上游 remote 从未 fetch 过。
        """
        upstream_ref = f"upstream/{self.cfg.main_branch}"
        result = subprocess.run(
            ["git", "rev-parse", upstream_ref],
            cwd=self.repo_root, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Upstream remote '{upstream_ref}' not found. "
                "Run 'upstream-sync fetch' first."
            )
        latest_ref = upstream_ref

        tags = self.list_version_tags()
        if tags:
            previous_ref = sorted(tags)[-1]   # tag 字典序对应 vYYYY_MM 时间序
        else:
            vb = self.cfg.vendor_branch
            result = subprocess.run(
                ["git", "rev-parse", vb],
                cwd=self.repo_root, capture_output=True, text=True,
            )
            previous_ref = vb if result.returncode == 0 else latest_ref

        return previous_ref, latest_ref
```

> **v1.2 新增要点**：
> - `fetch_ref` + `extract_to_path` 实现「按 ref 提取源码子目录」，是 `extract` / `upgrade` / `generate-patch` 的基础。
> - `list_version_tags` + `detect_sync_refs` 让 `sync` 命令不再需要手动指定 from/to ref。
> - `reset_vendor_to_upstream` 用于把 vendor 分支硬重置回上游，便于 CI 上从头同步。

### 3.4 Patch 引擎 (core/patch_engine.py)

可插拔的 Patch 应用引擎，支持 `quilt`、`git-am` 和自定义命令。

```python
# upstream_sync/core/patch_engine.py
from typing import Protocol, runtime_checkable
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ApplyResult:
    success: list[str]              # 成功应用的 patch
    failed: list[tuple[str, str]]   # (patch_name, reason)
    needs_review: list[str]         # 需要人工审核


@runtime_checkable
class PatchEngine(Protocol):
    """Patch 应用引擎协议。"""

    def apply_all(self, patch_dir: Path, series_file: Path) -> ApplyResult: ...
    def pop_all(self) -> None: ...
    def refresh(self, patch_name: str) -> None: ...
    def status(self) -> dict: ...


class QuiltEngine:
    """Quilt 实现。"""

    def apply_all(self, patch_dir: Path, series_file: Path) -> ApplyResult:
        import subprocess
        result = subprocess.run(
            ["quilt", "push", "-a"],
            cwd=patch_dir.parent,
            capture_output=True, text=True
        )
        # 解析 quilt 输出，构建 ApplyResult
        ...

    def pop_all(self) -> None:
        import subprocess
        subprocess.run(["quilt", "pop", "-a"], check=True)

    def refresh(self, patch_name: str) -> None:
        import subprocess
        subprocess.run(["quilt", "refresh", patch_name], check=True)

    def status(self) -> dict:
        import subprocess
        result = subprocess.run(
            ["quilt", "applied"], capture_output=True, text=True
        )
        return {"applied": result.stdout.strip().split("\n") if result.returncode == 0 else []}


class GitAmEngine:
    """git am 实现。"""

    def apply_all(self, patch_dir: Path, series_file: Path) -> ApplyResult:
        import subprocess
        patches = sorted(patch_dir.glob("*.patch"))
        result = subprocess.run(
            ["git", "am", "--3way"] + [str(p) for p in patches],
            capture_output=True, text=True
        )
        ...

    def pop_all(self) -> None:
        import subprocess
        subprocess.run(["git", "am", "--abort"], check=False)

    def refresh(self, patch_name: str) -> None:
        raise NotImplementedError("git-am engine does not support refresh")

    def status(self) -> dict:
        return {}


class CustomEngine:
    """调用使用者自定义脚本。"""

    def __init__(self, command: str):
        self.command = command

    def apply_all(self, patch_dir: Path, series_file: Path) -> ApplyResult:
        import subprocess
        result = subprocess.run(
            [self.command, "apply", str(patch_dir), str(series_file)],
            capture_output=True, text=True
        )
        ...

    def pop_all(self) -> None:
        subprocess.run([self.command, "pop"], check=False)

    def refresh(self, patch_name: str) -> None:
        subprocess.run([self.command, "refresh", patch_name], check=False)

    def status(self) -> dict:
        result = subprocess.run([self.command, "status"], capture_output=True, text=True)
        return {"raw": result.stdout}


def create_engine(config: PatchConfig) -> PatchEngine:
    """工厂函数，根据配置创建对应引擎。"""
    if config.engine == "quilt":
        return QuiltEngine()
    elif config.engine == "git-am":
        return GitAmEngine()
    elif config.engine == "custom":
        if not config.custom_command:
            raise ValueError("custom engine requires custom_command")
        return CustomEngine(config.custom_command)
    else:
        raise ValueError(f"Unknown patch engine: {config.engine}")
```

> **v1.2 适配器增强**：
> - `QuiltEngine.apply_all` 现在用正则精确解析 `Applied patch` / `Refusing to apply ... already applied` / `Can't re-apply ... already overlaps` 三类输出，区分 success 与 needs_review。
> - `GitAmEngine.apply_all` 优先读取 series 文件（一行一个 patch，支持 `#` 注释），series 不存在时回退到 `*.patch` 的字典序 glob。`status()` 通过 `.git/rebase-apply` / `.git/rebase-merge` 检测 in-progress 状态。
> - `CustomEngine.apply_all` / `status` 支持以 JSON 形式从 stdout 解析结构化结果（`{"success": [...], "failed": [...], "needs_review": [...]}`）。

### 3.5 Patch 生成器 (core/patch_generator.py) — v1.2 新增

基于旧 patch 的「转换模式」，自动为新上游 commit 生成 patch。是 `generate-patch` / `upgrade` 命令的核心。

**路径约定**：生成的 patch 头部路径为 `a/<rel> b/<rel>`，其中 `<rel>` 是相对 `source_subpath` 的相对路径（如 `bridge/__init__.py`，不是 `src/bridge/__init__.py`）。这样 patch 可直接应用到 `src/upstream/{commit_id}/` 下的提取内容（其本身已剥除了 `source_subpath` 前缀）。

```python
# upstream_sync/core/patch_generator.py
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PatchDiff:
    """两个版本间单个文件的 diff。"""
    path: str                       # 相对 source_subpath 的路径
    old_version: str
    new_version: str
    is_new: bool = False
    is_deleted: bool = False


@dataclass
class GeneratedPatch:
    """生成出来的 patch 与元数据。"""
    filename: str                   # 如 0001.bridge.__init__.py.patch
    content: str
    source_file: str                # 相对 source_subpath 的源文件路径
    patch_type: str                 # 'modify' | 'add' | 'delete'


class PatchGenerator:
    """依据 old_commit 的 patch 模式，为 new_commit 生成新 patch。"""

    def __init__(self, repo_root: Path, config: ProjectConfig) -> None:
        self.repo_root = repo_root
        self.cfg = config

    def generate_patches(
        self,
        new_commit: str,
        old_commit: str,
        patch_subdir: Path,
    ) -> list[GeneratedPatch]:
        """为 new_commit 生成 patch，参考 old_commit 时的 patch 模式。

        流程：
        1. `git diff old..new -- <source_subpath>` 拿到上游 diff；
           路径去掉 source_subpath 前缀。
        2. 扫描 old_commit 对应的 patch 目录，建立
           ``{源文件相对路径: patch_content}`` 的模式字典。
        3. 对每个上游 diff：
           - 若该文件在 old 模式中存在：套用旧 patch 模板；
           - 否则（新增文件）：生成 simple patch。
        4. 文件名格式 ``0001.<path.with.dots>.patch``，写盘到 patch_subdir。
        """
        upstream_diff = self._get_upstream_diff(old_commit, new_commit)
        if not upstream_diff:
            return []

        old_patches_dir = self._resolve_patch_dir(old_commit)
        old_patch_patterns = self._analyze_old_patches(old_patches_dir)

        generated: list[GeneratedPatch] = []
        patch_subdir.mkdir(parents=True, exist_ok=True)
        for diff in upstream_diff:
            if diff.is_deleted:
                continue
            pattern = old_patch_patterns.get(diff.path)
            content = (
                self._transform_patch(diff, pattern, old_commit, new_commit)
                if pattern else self._create_simple_patch(diff, new_commit)
            )
            if not content:
                continue
            filename = self._generate_patch_filename(diff, new_commit)
            (patch_subdir / filename).write_text(content, encoding="utf-8")
            generated.append(GeneratedPatch(
                filename=filename, content=content,
                source_file=diff.path,
                patch_type="add" if diff.is_new else "modify",
            ))
        return generated

    def create_series_file(
        self, patches: list[GeneratedPatch], output_path: Path
    ) -> None:
        """写出 ``{commit}_series`` 风格的 series 文件。"""
        with open(output_path, "w", encoding="utf-8") as f:
            for patch in patches:
                f.write(f"{patch.filename}\n")

    # ----- 私有方法 -----
    def _get_upstream_diff(self, old: str, new: str) -> list[PatchDiff]: ...
    def _analyze_old_patches(self, old_patches_dir: Path) -> dict[str, str]: ...
    def _transform_patch(self, diff, pattern, old, new) -> str | None: ...
    def _create_simple_patch(self, diff, commit) -> str: ...
    def _create_unified_patch(self, diff, commit) -> str: ...
    def _generate_patch_filename(self, diff, commit) -> str: ...
    def _resolve_patch_dir(self, commit: str) -> Path: ...
```

> **设计要点**：
> - **不感知业务语义**：仅做路径归一化 + 模式复用，不试图理解 patch 改了什么；语义升级由 Agent 后续处理（见 §6）。
> - **与 `extract` 配套**：`_get_upstream_diff` 在 `git diff` 中显式传入 `-- <source_subpath>`，从源头只关心业务子目录。
> - **可降级**：若 `cfg.patches.patch_subdir` 未配置，回退到 `cfg.patches.directory`（扁平结构兼容）。

### 3.6 同步编排器 (core/sync_orchestrator.py) — v1.2 新增

将 fetch → analyze → apply → audit 四个步骤封装为高层 Pipeline 协调器，并暴露 lifecycle hooks 钩子。

```python
# upstream_sync/core/sync_orchestrator.py
from typing import TYPE_CHECKING
from upstream_sync.config import ProjectConfig
from upstream_sync.core.change_analyzer import ChangeAnalyzer
from upstream_sync.core.layer_auditor import LayerAuditor
from upstream_sync.core.patch_engine import PatchEngine
from upstream_sync.core.vendor import VendorManager

if TYPE_CHECKING:
    from upstream_sync.hooks.base import SyncHooks


class SyncOrchestrator:
    """end-to-end sync pipeline 的高层协调器。"""

    def __init__(
        self,
        repo_root: Path,
        config: ProjectConfig,
        vendor: VendorManager | None = None,
        analyzer: ChangeAnalyzer | None = None,
        engine: PatchEngine | None = None,
        auditor: LayerAuditor | None = None,
        hooks: "SyncHooks | None" = None,
    ) -> None:
        # 依赖注入：每个组件都可替换，便于测试
        self.vendor = vendor or VendorManager(repo_root, config.upstream)
        self.analyzer = analyzer or ChangeAnalyzer(repo_root, config)
        self.engine = engine
        self.auditor = auditor or LayerAuditor(config)
        self.hooks = hooks

    # ----- 各阶段方法（均会触发 hooks）-----

    def run_fetch(self) -> str: ...
    def run_analyze(self, from_ref: str, to_ref: str) -> ChangeReport: ...
    def run_apply(self) -> dict: ...
    def run_audit(self) -> list: ...

    def detect_refs(self) -> tuple[str, str]:
        """转发到 ``VendorManager.detect_sync_refs``。"""
        return self.vendor.detect_sync_refs()

    def run_full_sync(
        self,
        from_ref: str | None = None,
        to_ref: str | None = None,
        auto: bool = False,
    ) -> dict:
        """完整流水线：

        1. ``from_ref`` / ``to_ref`` 为 None 时调用 ``detect_refs()`` 自动获取。
        2. ``run_fetch()`` → ``run_analyze(from, to)`` → （可选）``run_apply()`` → ``run_audit()``。
        3. ``auto=True`` 时仅当 ``report.overall_impact`` 命中
           ``cfg.sync.impact_threshold_auto`` 才执行 apply。

        Returns:
            ``{fetch_commit, from_ref, to_ref, report, applied, failed,
              needs_review, violations}`` 的汇总 dict。
        """
```

> **设计要点**：
> - **依赖注入**：构造时所有子组件（vendor/analyzer/engine/auditor/hooks）都可外部传入，方便测试和 CI 替换。
> - **Hooks 优先**：每个阶段方法内部都先调用 `self.hooks.pre_*` 再执行业务、最后 `post_*`。
> - **`auto` 守门**：`auto=True` 时，apply 仅在 `report.overall_impact` 命中阈值才执行，把高冲突变更留给人工。

### 3.7 备份管理 (core/backup_manager.py) — v1.2 新增

`src/` 的时间戳备份与恢复，供 `upgrade` 工作流在改造前自动快照。

```python
# upstream_sync/core/backup_manager.py
import shutil
from datetime import datetime
from pathlib import Path


class BackupManager:
    """管理 ``src/`` 的备份与恢复。"""

    def __init__(
        self,
        repo_root: Path,
        backup_root: Path | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.backup_root = backup_root or repo_root / "backup"

    def backup(
        self,
        src_path: Path | None = None,
        exclude_dirs: list[str] | None = None,
    ) -> Path:
        """备份 ``src/``（默认排除 upstream/.git/__pycache__/...）。

        Returns:
            新建备份目录的路径，格式为 ``backup/backup_YYYYMMDD_HHMMSS/``。
        """
        src_path = src_path or self.repo_root / "src"
        exclude_dirs = exclude_dirs or [
            "upstream", ".git", "__pycache__", "*.pyc", ".pytest_cache",
        ]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.backup_root / f"backup_{ts}"
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=True, exist_ok=True)
        self._copy_excluding(src_path, backup_dir, exclude_dirs)
        return backup_dir

    def restore(
        self,
        backup_dir: Path,
        target_path: Path | None = None,
        clear_first: bool = False,
    ) -> list[Path]:
        """从备份还原到 ``target_path``（默认 ``src/``）。"""
        target_path = target_path or self.repo_root / "src"
        if clear_first:
            self._clear_directory(target_path)
        restored: list[Path] = []
        for item in backup_dir.rglob("*"):
            if item.is_file():
                rel = item.relative_to(backup_dir)
                dst = target_path / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst)
                restored.append(dst)
        return restored

    def list_backups(self) -> list[dict]:
        """列出所有 backup 目录（按名字倒序）。"""
        ...

    def cleanup_old_backups(self, keep_count: int = 5) -> list[Path]:
        """只保留最近 N 个备份，返回被删除的路径列表。"""
        ...
```

> **设计要点**：
> - **默认排除 `upstream/`**：避免备份 `src/upstream/{commit_id}/` 这种动辄数百 MB 的纯镜像。
> - **可恢复性优先**：`_clear_directory` 提供「先清空再恢复」选项；不传则合并式恢复。
> - **配套使用**：`upgrade` 流程自动调用 `backup(src/)`，失败时建议 `restore --clear-first`。

### 3.8 Patch 验证器 (core/verifier.py) — v1.2 新增

验证新 patch 在新上游代码上应用后，与旧 patch 在旧上游代码上应用后**功能等价**。

```python
# upstream_sync/core/verifier.py
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VerificationResult:
    """验证结果。"""
    passed: bool
    message: str
    details: dict | None = None


class Verifier:
    """跨上游版本验证 patch 的功能等价性。"""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def verify_patches(
        self,
        old_patches_dir: Path,
        new_patches_dir: Path,
        old_upstream_dir: Path,
        new_upstream_dir: Path,
        backup_dir: Path,
    ) -> VerificationResult:
        """四步检查：

        1. 所有目录都存在；
        2. patch 结构（命名 ``*.patch``、数量非零）；
        3. 上游两个版本的 diff（文件级 hash）确实存在差异；
        4. 语义等价（新 patch 中 new_file 数 ≪ 旧 patch 的 2 倍）。

        任何一步失败均返回 ``passed=False`` 并在 ``details["issues"]`` 列出原因。
        """
        issues: list[str] = []
        # Check 1: 路径存在性
        for label, path in [
            ("old_patches", old_patches_dir),
            ("new_patches", new_patches_dir),
            ("old_upstream", old_upstream_dir),
            ("new_upstream", new_upstream_dir),
        ]:
            if not path.exists():
                issues.append(f"{label} directory not found: {path}")
        if issues:
            return VerificationResult(False, "Missing required directories",
                                     {"issues": issues})

        old_patches = sorted(old_patches_dir.glob("*.patch"))
        new_patches = sorted(new_patches_dir.glob("*.patch"))

        # Check 2: 结构
        struct = self._verify_patch_structure(old_patches, new_patches)
        if not struct.passed:
            issues.append(struct.message)

        # Check 3: upstream diff
        upstream = self._verify_upstream_diff(old_upstream_dir, new_upstream_dir)
        if not upstream.passed:
            issues.append(upstream.message)

        # Check 4: 语义等价
        sem = self._verify_semantic_equivalence(
            old_patches, new_patches, old_upstream_dir, new_upstream_dir
        )
        if not sem.passed:
            issues.append(sem.message)

        if issues:
            return VerificationResult(False, "Verification failed",
                                     {"issues": issues})
        return VerificationResult(True, "Patch verification passed",
            {"old_patches_count": len(old_patches),
             "new_patches_count": len(new_patches)})

    def generate_verification_report(
        self, result: VerificationResult, output_path: Path
    ) -> None:
        """写出 Markdown 格式的 verify report。"""
        ...
```

> **设计要点**：
> - **轻量启发式**：仅做路径/结构/文件 hash/类型分布的快速比对，**不**执行 patch 后再跑测试 —— 那是 Agent/CI 的职责。
> - **是「健全性检查」而非「正确性证明」**：核心目的是尽早发现 patch 数量级异常（如新 patch 突然多了 5 倍 new_file）。

### 3.9 变化分析器 (core/change_analyzer.py)

对比两个上游版本，生成结构化影响报告。

```python
# upstream_sync/core/change_analyzer.py
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import json


@dataclass
class ModuleImpact:
    module_name: str
    layer_name: str              # 由配置映射，如 "upstream"
    files_changed: list[str]
    patches_affected: list[str]
    conflict_probability: str    # low | medium | high
    estimated_effort_minutes: int
    recommended_strategy: str    # fast-forward | rebase-patches | human-review


@dataclass
class ChangeReport:
    upstream_version: str
    previous_version: str
    overall_impact: str          # low | medium | high
    statistics: dict
    module_impacts: list[ModuleImpact]
    action_items: list[dict]

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self, indent=indent, default=lambda o: o.__dict__)


class ChangeAnalyzer:
    def __init__(self, repo_root: Path, config: ProjectConfig):
        self.repo_root = repo_root
        self.cfg = config

    def analyze(self, from_ref: str, to_ref: str) -> ChangeReport:
        # 1. 获取 diff 统计
        result = subprocess.run(
            ["git", "diff", "--stat", f"{from_ref}..{to_ref}"],
            cwd=self.repo_root, capture_output=True, text=True, check=True
        )
        diff_stat = result.stdout

        # 2. 识别文件变更
        changed_files = self._parse_changed_files(diff_stat)

        # 3. 映射到层
        module_impacts = []
        for layer in self.cfg.layers:
            layer_files = [f for f in changed_files
                          if any(Path(f).is_relative_to(p) for p in layer.paths)]
            if layer_files:
                # 交叉引用 patches/metadata/
                affected_patches = self._find_affected_patches(layer_files)
                conflict_prob = self._assess_conflict_probability(layer_files, affected_patches)
                module_impacts.append(ModuleImpact(
                    module_name=layer.name,
                    layer_name=layer.name,
                    files_changed=layer_files,
                    patches_affected=affected_patches,
                    conflict_probability=conflict_prob,
                    estimated_effort_minutes=self._estimate_effort(conflict_prob, len(layer_files)),
                    recommended_strategy=self._recommend_strategy(conflict_prob)
                ))

        # 4. 评估总体影响
        overall = self._calculate_overall_impact(module_impacts)

        return ChangeReport(
            upstream_version=to_ref,
            previous_version=from_ref,
            overall_impact=overall,
            statistics={
                "files_changed_upstream": len(changed_files),
                "modules_affected": len(module_impacts),
            },
            module_impacts=module_impacts,
            action_items=self._generate_action_items(module_impacts)
        )

    def _parse_changed_files(self, diff_stat: str) -> list[str]:
        files = []
        for line in diff_stat.strip().split("\n"):
            parts = line.split("|")
            if len(parts) >= 2:
                filename = parts[0].strip()
                if filename and not filename.endswith("..."):
                    files.append(filename)
        return files

    def _find_affected_patches(self, files: list[str]) -> list[str]:
        affected = []
        meta_dir = self.cfg.patches.metadata_dir
        if not meta_dir.exists():
            return affected
        for meta_file in meta_dir.glob("*.json"):
            data = json.loads(meta_file.read_text())
            for mod in data.get("affected_modules", []):
                if any(f.startswith(mod) for f in files):
                    affected.append(data.get("id", meta_file.stem))
                    break
        return affected

    def _assess_conflict_probability(self, files: list[str], patches: list[str]) -> str:
        # 简单启发式：有 patch 影响的文件数 > 3 → high，有 patch → medium，否则 low
        if len(patches) > 0 and len(files) > 3:
            return "high"
        elif len(patches) > 0:
            return "medium"
        return "low"

    def _estimate_effort(self, prob: str, file_count: int) -> int:
        base = {"low": 5, "medium": 20, "high": 45}
        return base.get(prob, 30) + file_count * 2

    def _recommend_strategy(self, prob: str) -> str:
        return {"low": "fast-forward", "medium": "rebase-patches", "high": "human-review"}.get(prob, "human-review")

    def _calculate_overall_impact(self, impacts: list[ModuleImpact]) -> str:
        if any(i.conflict_probability == "high" for i in impacts):
            return "high"
        elif any(i.conflict_probability == "medium" for i in impacts):
            return "medium"
        return "low"

    def _generate_action_items(self, impacts: list[ModuleImpact]) -> list[dict]:
        items = []
        for imp in impacts:
            if imp.conflict_probability == "high":
                items.append({
                    "module": imp.module_name,
                    "action": "human-review",
                    "reason": f"High conflict probability with patches: {imp.patches_affected}"
                })
            elif imp.patches_affected:
                items.append({
                    "module": imp.module_name,
                    "action": "review-patches",
                    "reason": f"Affected patches: {imp.patches_affected}"
                })
        return items
```

### 3.10 层间审计 (core/layer_auditor.py)

审计层间依赖违规。层定义完全由配置决定，**支持 forbidden 与 allowed 双向规则**：

- 若 `forbidden_imports_from` 非空：命中任意前缀即违规（优先于 allowed）。
- 否则若 `allowed_imports_from` 非空：只允许命中其前缀的导入，其余全违规。
- 两者都为空：该层不限制。

```python
# upstream_sync/core/layer_auditor.py
import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    file: Path
    forbidden_import: str
    layer: str
    line_number: int


class LayerAuditor:
    def __init__(self, config: ProjectConfig):
        self.layers = config.layers

    def audit(self) -> list[Violation]:
        violations: list[Violation] = []
        for layer in self.layers:
            for path in layer.paths:
                if not path.exists():
                    continue
                for py_file in path.rglob("*.py"):
                    imports = self._extract_imports(py_file)
                    for imp, lineno in imports:
                        if self._is_forbidden(imp, layer):
                            violations.append(Violation(
                                file=py_file,
                                forbidden_import=imp,
                                layer=layer.name,
                                line_number=lineno,
                            ))
        return violations

    def report(self, violations: list[Violation]) -> str:
        if not violations:
            return "No layer violations found."
        lines = [f"Found {len(violations)} layer violation(s):\n"]
        for v in violations:
            lines.append(
                f"  [{v.layer}] {v.file}:{v.line_number} "
                f"imports '{v.forbidden_import}'"
            )
        return "\n".join(lines)

    # ----- 内部 -----

    def _is_forbidden(self, imp: str, layer: LayerConfig) -> bool:
        """双向规则：forbidden 优先；若未设置则看 allowed 列表。"""
        if layer.forbidden_imports_from:
            return any(imp.startswith(f) for f in layer.forbidden_imports_from)
        if layer.allowed_imports_from:
            return not any(imp.startswith(a) for a in layer.allowed_imports_from)
        return False

    def _extract_imports(self, py_file: Path) -> list[tuple[str, int]]:
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, OSError):
            return []
        imports: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module:
                    imports.append((module, node.lineno))
        return imports
```

> **v1.2 行为变化**：
> - `audit()` 不再在内部 `for forbidden in layer.forbidden_imports_from` 嵌套循环；改为先抽 imports 再统一调用 `_is_forbidden`。
> - `_is_forbidden` 是新增的内部方法，把规则统一收口；`allowed_imports_from` 现在真的有效果了（v1.1 中虽定义但未使用）。

### 3.11 报告生成 (reporters/)

```python
# upstream_sync/reporters/json_reporter.py
from pathlib import Path
from upstream_sync.core.change_analyzer import ChangeReport


class JSONReporter:
    def emit(self, report: ChangeReport, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.to_json(indent=2), encoding="utf-8")


# upstream_sync/reporters/markdown_reporter.py
from pathlib import Path
from upstream_sync.core.change_analyzer import ChangeReport


class MarkdownReporter:
    def emit(self, report: ChangeReport, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = [
            f"# Upstream Sync Report: {report.upstream_version}",
            "",
            f"- **Previous Version**: {report.previous_version}",
            f"- **Overall Impact**: {report.overall_impact}",
            f"- **Files Changed**: {report.statistics.get('files_changed_upstream', 'N/A')}",
            "",
            "## Module Impacts",
            "",
        ]
        for imp in report.module_impacts:
            lines.extend([
                f"### {imp.module_name}",
                "",
                f"- **Layer**: {imp.layer_name}",
                f"- **Conflict Probability**: {imp.conflict_probability}",
                f"- **Recommended Strategy**: {imp.recommended_strategy}",
                f"- **Estimated Effort**: {imp.estimated_effort_minutes} minutes",
                f"- **Files Changed**: {len(imp.files_changed)}",
                f"- **Patches Affected**: {', '.join(imp.patches_affected) or 'None'}",
                "",
            ])
        if report.action_items:
            lines.extend(["## Action Items", ""])
            for item in report.action_items:
                lines.append(
                    f"- [{item['action'].upper()}] "
                    f"{item['module']}: {item['reason']}"
                )
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
```

> **v1.2 行为变化**：
> - `JSONReporter.emit` 与 `MarkdownReporter.emit` 都先 `path.parent.mkdir(parents=True, exist_ok=True)`，再写文件 —— 调用方不再需要先 `mkdir -p`。
> - `change_analyzer.ChangeReport` 的 dataclass 字段均带 `default_factory` 默认值；`to_json` 仍用 `default=lambda o: o.__dict__`。

### 3.12 生命周期钩子 (hooks/) — v1.2 新增

业务项目通过继承 `SyncHooks` 在每个阶段前后注入自定义逻辑（如发 IM、刷新 dashboard），无需修改组件。

```python
# upstream_sync/hooks/base.py
from typing import Any
from pathlib import Path
from upstream_sync.config import ProjectConfig
from upstream_sync.core.change_analyzer import ChangeReport


class SyncHooks:
    """sync 流水线的生命周期钩子基类。

    所有方法默认 no-op；子类按需 override。每个方法都被对应
    ``SyncOrchestrator`` 阶段方法（``run_fetch`` / ``run_analyze`` /
    ``run_apply`` / ``run_audit``）自动调用。
    """

    def __init__(self, config: ProjectConfig) -> None:
        self.cfg = config

    # ----- Pre-stage -----
    def pre_fetch(self, repo_root: Path) -> None: ...
    def pre_analyze(self, from_ref: str, to_ref: str) -> None: ...
    def pre_apply(self, patch_dir: Path, series_file: Path) -> None: ...
    def pre_audit(self) -> None: ...

    # ----- Post-stage -----
    def post_fetch(self, commit_hash: str) -> None: ...
    def post_analyze(self, report: ChangeReport) -> None: ...
    def post_apply(self, results: dict[str, Any]) -> None: ...
    def post_audit(self, violations: list[Any]) -> None: ...
```

**调用时序**（以 `SyncOrchestrator.run_full_sync` 为例）：

```
pre_fetch → fetch → post_fetch
   ↓
pre_analyze(from, to) → analyze → post_analyze(report)
   ↓
(可选) pre_apply(dir, series) → apply → post_apply({success,failed,needs_review})
   ↓
pre_audit → audit → post_audit(violations)
```

**ClawCodex 集成示例**：

```python
# clawcodex/upstream_hooks.py
from upstream_sync.hooks.base import SyncHooks
from upstream_sync.core.change_analyzer import ChangeReport


class ClawCodexSyncHooks(SyncHooks):
    def post_fetch(self, commit_hash: str) -> None:
        notify_slack(f"upstream-sync fetched {commit_hash[:8]}")

    def post_analyze(self, report: ChangeReport) -> None:
        if report.overall_impact == "high":
            open_github_issue(
                title=f"[upstream-sync] high impact changes {report.upstream_version[:8]}",
                body=report.to_json(),
                labels=["upstream-sync", "needs-review"],
            )
```

### 3.13 CLI (cli.py)

v1.2 共 **11 个命令**。CLI 入口是 `typer.Typer`；配置加载统一走 `load_config()`，所有命令都接受 `--config` 覆盖。

```python
# upstream_sync/cli.py
from __future__ import annotations
from pathlib import Path
import typer

from upstream_sync.config import ProjectConfig
from upstream_sync.core.change_analyzer import ChangeAnalyzer
from upstream_sync.core.layer_auditor import LayerAuditor
from upstream_sync.core.patch_engine import create_engine
from upstream_sync.core.patch_generator import PatchGenerator
from upstream_sync.core.sync_orchestrator import SyncOrchestrator
from upstream_sync.core.vendor import VendorManager
from upstream_sync.core.backup_manager import BackupManager
from upstream_sync.core.verifier import Verifier
from upstream_sync.reporters.json_reporter import JSONReporter
from upstream_sync.reporters.markdown_reporter import MarkdownReporter

app = typer.Typer(help="upstream-sync: Generic upstream code synchronization tool")

DEFAULT_CONFIG = Path("upstream-sync.yaml")


def load_config(path: Path) -> ProjectConfig:
    """Load and validate ``upstream-sync.yaml``."""
    import yaml
    return ProjectConfig(**yaml.safe_load(path.read_text(encoding="utf-8")))


# 1) init ---------------------------------------------------------------
@app.command()
def init(
    template: str = typer.Option("blank", help="blank | python-port | node-fork | rust-fork"),
    output: Path = typer.Option(DEFAULT_CONFIG, help="Output config path"),
) -> None: ...


# 2) fetch --------------------------------------------------------------
@app.command()
def fetch(
    ref: str = typer.Option(
        None,
        help="Specific ref (commit hash, tag, or branch); default = main branch",
    ),
    config: Path = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Fetch upstream code (main branch by default, or a specific ref)."""
    cfg = load_config(config)
    vendor = VendorManager(Path("."), cfg.upstream)
    vendor.ensure_remote()
    if ref:
        commit = vendor.fetch_ref(ref)
        typer.echo(f"Fetched upstream/{ref} at {commit}")
    else:
        commit = vendor.fetch()
        typer.echo(f"Fetched upstream/{cfg.upstream.main_branch} at {commit}")


# 3) extract ------------------------------------------------------------
@app.command()
def extract(
    ref: str = typer.Argument(..., help="Upstream ref (commit, tag, branch)"),
    output: Path = typer.Option(
        None, help="Output dir (default: src/upstream/{short_ref})"
    ),
    config: Path = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Fetch *ref* and extract only the configured source_subpath.

    The contents of ``upstream/<source_subpath>/*`` are placed directly
    into ``output/`` (without the ``source_subpath/`` prefix).
    """
    cfg = load_config(config)
    vendor = VendorManager(Path("."), cfg.upstream)
    vendor.ensure_remote()
    commit = vendor.fetch_ref(ref)
    short_ref = commit[:8]
    target = output or Path("src") / "upstream" / short_ref
    vendor.extract_to_path(ref=ref, subpath=cfg.upstream.source_subpath,
                           target_path=target)
    typer.echo(f"Fetched upstream/{ref} at {commit}")
    typer.echo(f"Extracted {cfg.upstream.source_subpath}/ to {target}")


# 4) analyze ------------------------------------------------------------
@app.command()
def analyze(
    from_ref: str = typer.Argument(..., help="Base ref/tag to compare from"),
    to_ref:   str = typer.Argument(..., help="Target ref/tag to compare to"),
    config:   Path = typer.Option(DEFAULT_CONFIG),
    output_dir: Path = typer.Option(Path(".upstream-sync")),
) -> None:
    """Compare two refs and write JSON + Markdown impact reports."""
    cfg = load_config(config)
    report = ChangeAnalyzer(Path("."), cfg).analyze(from_ref, to_ref)
    output_dir.mkdir(exist_ok=True)
    if "json" in cfg.sync.report_formats:
        JSONReporter().emit(report, output_dir / "sync-report.json")
        typer.echo(f"JSON report: {output_dir / 'sync-report.json'}")
    if "markdown" in cfg.sync.report_formats:
        MarkdownReporter().emit(report, output_dir / "sync-report.md")
        typer.echo(f"Markdown report: {output_dir / 'sync-report.md'}")
    typer.echo(f"Overall impact: {report.overall_impact}")
    if report.action_items:
        typer.echo(f"Action items: {len(report.action_items)}")


# 5) apply --------------------------------------------------------------
def _resolve_commit_placeholder(path: Path, commit: str) -> Path:
    """Resolve ``{commit}`` in a path string."""
    s = str(path)
    return Path(s.format(commit=commit)) if "{commit}" in s else path


@app.command()
def apply(
    commit: str = typer.Option(
        None, help="Upstream commit hash (auto-detected if omitted)",
    ),
    config: Path = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Apply the configured patch queue (auto-detect or per-commit subdir)."""
    cfg = load_config(config)
    if commit is None:
        try:
            commit = VendorManager(Path("."), cfg.upstream).fetch()
        except Exception:
            typer.echo("Could not auto-detect upstream commit. Provide --commit.")
            raise typer.Exit(1)
    if cfg.patches.patch_subdir:
        # per-commit subdirectory structure
        patch_dir = _resolve_commit_placeholder(
            Path(cfg.patches.patch_subdir), commit,
        )
        series_file = patch_dir / f"{commit}_series"
    else:
        patch_dir = cfg.patches.directory
        series_file = cfg.patches.series_file
    engine = create_engine(cfg.patches)
    result = engine.apply_all(patch_dir, series_file)
    typer.echo(
        f"Applied: {len(result.success)}, "
        f"Failed: {len(result.failed)}, "
        f"Needs Review: {len(result.needs_review)}"
    )
    if result.failed:
        raise typer.Exit(1)


# 6) audit --------------------------------------------------------------
@app.command()
def audit(config: Path = typer.Option(DEFAULT_CONFIG)) -> None:
    """Audit layer dependency violations."""
    cfg = load_config(config)
    violations = LayerAuditor(cfg).audit()
    print(LayerAuditor(cfg).report(violations))
    if violations:
        raise typer.Exit(1)


# 7) sync (full pipeline) ----------------------------------------------
@app.command()
def sync(
    from_ref: str | None = typer.Argument(None, help="Base ref (auto-detected if omitted)"),
    to_ref:   str | None = typer.Argument(None, help="Target ref (auto-detected if omitted)"),
    auto:     bool = typer.Option(False, help="Auto-resolve low-impact changes"),
    config:   Path = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Full pipeline: fetch → analyze → (auto) apply → audit, via ``SyncOrchestrator``."""
    cfg = load_config(config)
    orch = SyncOrchestrator(Path("."), cfg)
    detected_from, detected_to = orch.detect_refs()
    from_ref = from_ref or detected_from
    to_ref   = to_ref   or detected_to
    typer.echo(f"Syncing: {from_ref} -> {to_ref}")
    results = orch.run_full_sync(from_ref=from_ref, to_ref=to_ref, auto=auto)
    report = results["report"]
    typer.echo(f"\nOverall impact: {report.overall_impact}")
    typer.echo(f"Files changed upstream: {report.statistics.get('files_changed_upstream', 0)}")
    typer.echo(f"Modules affected: {report.statistics.get('modules_affected', 0)}")
    if results["applied"]:     typer.echo(f"\nPatches applied: {len(results['applied'])}")
    if results["failed"]:      typer.echo(f" Patches failed: {len(results['failed'])}")
    if results["needs_review"]:typer.echo(f" Needs review: {len(results['needs_review'])}")
    if results["violations"]:
        typer.echo(f"\nLayer violations: {len(results['violations'])}")
        for v in results["violations"]:
            typer.echo(f"  [{v.layer}] {v.file}:{v.line_number} -> {v.forbidden_import}")
    typer.echo("\nSync pipeline complete.")


# 8) generate-patch -----------------------------------------------------
@app.command("generate-patch")
def generate_patch(
    new_commit: str = typer.Option(..., help="New upstream commit hash"),
    old_commit: str = typer.Option(..., help="Old upstream commit to reference"),
    config:     Path = typer.Option(DEFAULT_CONFIG),
    output:     Path | None = typer.Option(
        None, help="Output dir (default: patches/upstream/{new_commit})",
    ),
) -> None:
    """Generate new patches based on old patch patterns."""
    cfg = load_config(config)
    generator = PatchGenerator(Path("."), cfg)
    out = output
    if out is None:
        if cfg.patches.patch_subdir:
            out = Path(str(cfg.patches.patch_subdir).format(commit=new_commit))
        else:
            out = cfg.patches.directory
    typer.echo(f"Generating patches for {new_commit} based on {old_commit}...")
    patches = generator.generate_patches(new_commit, old_commit, out)
    if patches:
        series_file = out / f"{new_commit}_series"
        generator.create_series_file(patches, series_file)
        typer.echo(f"Generated {len(patches)} patches in {out}")
        typer.echo(f"Series file: {series_file}")
    else:
        typer.echo("No patches generated (no changes detected)")


# 9) backup / restore / backup-list -------------------------------------
@app.command()
def backup(
    backup_root: Path | None = typer.Option(None, help="Default: backup/"),
    config:      Path = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Backup ``src/`` (excludes ``src/upstream/`` by default)."""
    cfg = load_config(config)
    backup_path = BackupManager(Path("."), backup_root).backup(Path("src"))
    typer.echo(f"Backup created: {backup_path}")
    typer.echo(f"Total files backed up: {len(list(backup_path.rglob('*')))}")


@app.command()
def restore(
    backup_dir:  Path = typer.Argument(..., help="Backup directory"),
    clear_first: bool = typer.Option(False, help="Clear src/ before restoring"),
    config:      Path = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Restore files from a previous backup."""
    restored = BackupManager(Path(".")).restore(backup_dir, Path("src"),
                                                clear_first=clear_first)
    typer.echo(f"Restored {len(restored)} files from {backup_dir}")


@app.command("backup-list")
def backup_list(
    backup_root: Path | None = typer.Option(None, help="Default: backup/"),
) -> None:
    """List all available backups (newest first)."""
    backups = BackupManager(Path("."), backup_root).list_backups()
    if not backups:
        typer.echo("No backups found"); return
    typer.echo("Available backups:")
    typer.echo("-" * 50)
    for b in backups:
        typer.echo(f"  {b['path'].name} - {b['file_count']} files")


# 10) verify ------------------------------------------------------------
@app.command()
def verify(
    old_commit: str = typer.Option(..., help="Old upstream commit hash"),
    new_commit: str = typer.Option(..., help="New upstream commit hash"),
    output:     Path = typer.Option(Path(".upstream-sync/verify-report.md")),
    config:     Path = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Verify patch functional equivalence across upstream versions."""
    cfg = load_config(config)
    def _sub(commit: str) -> Path:
        return (
            Path(str(cfg.patches.patch_subdir).format(commit=commit))
            if cfg.patches.patch_subdir
            else cfg.patches.directory
        )
    verifier = Verifier(Path("."))
    result = verifier.verify_patches(
        old_patches_dir=_sub(old_commit),
        new_patches_dir=_sub(new_commit),
        old_upstream_dir=Path("src") / "upstream" / old_commit[:8],
        new_upstream_dir=Path("src") / "upstream" / new_commit[:8],
        backup_dir=Path("backup"),
    )
    verifier.generate_verification_report(result, output)
    typer.echo(f"Verification {'PASSED' if result.passed else 'FAILED'}")
    typer.echo(f"Report: {output}")
    if not result.passed:
        raise typer.Exit(1)


# 11) upgrade (recommended workflow) ------------------------------------
@app.command()
def upgrade(
    new_commit:   str = typer.Option(..., help="New upstream commit"),
    old_commit:   str = typer.Option(..., help="Current upstream commit"),
    extract_only: bool = typer.Option(
        False, help="Only extract new upstream; skip patch generation",
    ),
    config: Path = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Recommended upgrade workflow (sync current → fetch → extract →
    generate patches → verify). See §八 升级工作流."""
    # Step 1: refresh current patches from src/ vs old_upstream
    # Step 2: fetch new upstream
    # Step 3: extract new upstream to src/upstream/{new_commit[:8]}
    # Step 4: generate new patches under patches/upstream/{new_commit}
    # Step 5: verify new patches
    ...


# 12) agent-prompt ------------------------------------------------------
@app.command("agent-prompt")
def agent_prompt(
    report: Path = typer.Argument(..., help="Path to sync-report.json"),
    config: Path = typer.Option(DEFAULT_CONFIG),
    output: Path = typer.Option(Path("agent-instruction.md")),
) -> None:
    """Render an agent instruction from a sync report (Jinja2 template)."""
    import json
    from jinja2 import Template
    cfg = load_config(config)
    report_data = json.loads(report.read_text(encoding="utf-8"))
    template_text = (Path(__file__).parent / "templates" / "agent_prompt.md.j2").read_text(
        encoding="utf-8"
    )
    rendered = Template(template_text).render(
        project_name=cfg.project_name,
        upstream_url=cfg.upstream.remote_url,
        layers=[layer.model_dump(mode="json") for layer in cfg.layers],   # v1.2: dict 序列化
        **report_data,
    )
    output.write_text(rendered, encoding="utf-8")
    typer.echo(f"Agent prompt written to {output}")


if __name__ == "__main__":
    app()
```

#### 3.13.1 命令总览

| # | 命令 | 作用 | 关键参数 | v1.2 变化 |
|---|------|------|---------|-----------|
| 1 | `init` | 初始化配置 | `--template` | — |
| 2 | `fetch` | 拉上游 main 或指定 ref | `--ref` | 新增 `--ref` |
| 3 | `extract` | 拉 ref + 提取 source_subpath | `<ref>`, `--output` | **新增** |
| 4 | `analyze` | 对比两 ref 并出报告 | `<from_ref> <to_ref>` | — |
| 5 | `apply` | 应用 patch 队列 | `--commit` | 新增 `--commit`、per-commit subdir |
| 6 | `audit` | 审计层间违规 | — | — |
| 7 | `sync` | 完整流水线 | `[from] [to]`, `--auto` | 改走 `SyncOrchestrator`，自动 detect refs |
| 8 | `generate-patch` | 基于旧 patch 生成新 patch | `--new-commit --old-commit` | **新增** |
| 9 | `backup` / `restore` / `backup-list` | 备份与恢复 | — | **新增** |
| 10 | `verify` | 跨版本 patch 等价性验证 | `--old-commit --new-commit` | **新增** |
| 11 | `upgrade` | 推荐升级工作流 | `--new-commit --old-commit` | **新增** |
| 12 | `agent-prompt` | 渲染 agent 指令 | `<report>` | 改用 `layer.model_dump(mode="json")` |

#### 3.13.2 配置模板（v1.2 更新）

```python
def _blank_template() -> str:
    return """project_name: "my-project"
source_lang: "python"

upstream:
  remote_url: "https://github.com/original/repo.git"
  main_branch: "main"
  vendor_branch: "upstream/vendor"
  version_tag_format: "upstream/v{YYYY}_{MM}"
  source_subpath: "src"          # v1.2 新增：只提取该子路径

layers: []

patches:
  directory: "patches"
  engine: "quilt"
  series_file: "patches/series"
  metadata_dir: "patches/metadata"

sync:
  impact_threshold_auto: "low"
  impact_threshold_agent: "medium"
  report_formats: ["json", "markdown"]
"""


def _python_port_template() -> str:
    return """project_name: "my-python-port"
source_lang: "python"

upstream:
  remote_url: "https://github.com/original/repo.git"
  main_branch: "main"
  vendor_branch: "upstream/vendor"
  version_tag_format: "upstream/v{YYYY}_{MM}"
  source_subpath: "src"          # v1.2 新增

layers:
  - name: "upstream"
    paths: ["src/upstream"]
    forbidden_imports_from: []
  - name: "capabilities"
    paths: ["src/capabilities"]
    forbidden_imports_from: ["src.upstream"]
  - name: "features"
    paths: ["src/features"]
    forbidden_imports_from: ["src.upstream"]

patches:
  directory: "patches"
  engine: "quilt"
  series_file: "patches/series"
  metadata_dir: "patches/metadata"

sync:
  impact_threshold_auto: "low"
  impact_threshold_agent: "medium"
  report_formats: ["json", "markdown"]
"""


def _node_fork_template() -> str:
    return _python_port_template().replace(
        'source_lang: "python"', 'source_lang: "typescript"'
    )


def _rust_fork_template() -> str:
    return _python_port_template().replace(
        'source_lang: "python"', 'source_lang: "rust"'
    )
```
  report_formats: ["json", "markdown"]
"""


def _node_fork_template() -> str:
    return _python_port_template().replace("source_lang: \"python\"", "source_lang: \"typescript\"")


def _rust_fork_template() -> str:
    return _python_port_template().replace("source_lang: \"python\"", "source_lang: \"rust\"")


if __name__ == "__main__":
    app()
```

---

## 四、ClawCodex 集成方式

### 4.1 配置文件示例

在 ClawCodex 根目录创建 `upstream-sync.yaml`：

```yaml
project_name: "clawcodex"
source_lang: "python"

upstream:
  remote_url: "https://github.com/anthropics/claude-code.git"
  main_branch: "main"
  vendor_branch: "upstream/vendor"
  version_tag_format: "upstream/v{YYYY}_{MM}"
  source_subpath: "src"          # v1.2 新增：只提取 src/

layers:
  - name: "upstream"
    paths: ["src/upstream"]
    allowed_imports_from: []
    forbidden_imports_from: []

  - name: "capabilities"
    paths: ["src/capabilities"]
    allowed_imports_from: []
    forbidden_imports_from: ["src.upstream"]

  - name: "features"
    paths:
      - "src/orchestrator"
      - "src/providers"
      - "src/hooks"
      - "src/permissions"
    allowed_imports_from: ["src.capabilities"]
    forbidden_imports_from:
      - "src.upstream"
      - "src.agent"
      - "src.tool_system"
      - "src.context_system"

patches:
  directory: "patches"
  engine: "quilt"
  series_file: "patches/series"
  metadata_dir: "patches/metadata"
  # v1.2 新增：per-commit 子目录结构（推荐）
  patch_subdir: "patches/upstream/{commit}"

sync:
  impact_threshold_auto: "low"
  impact_threshold_agent: "medium"
  report_formats: ["json", "markdown"]
```

> **v1.2 配置变化**：
> - `upstream.source_subpath`：默认 `"src"`。`extract` / `upgrade` / `generate-patch` 都只看上游该子目录。
> - `patches.patch_subdir`：`"patches/upstream/{commit}"` 启用 per-commit 隔离。`apply` 与 `generate-patch` 在此模式下把 patch 写到对应 commit 子目录。

### 4.2 保留在 ClawCodex 内的内容

以下内容属于**业务策略**，不解耦到通用组件中：

```
clawcodex/
├── src/
│   ├── capabilities/                  # Layer 2 Protocol 定义
│   │   ├── agent_protocol.py          # ClawCodex 特有的 AgentLoop Protocol
│   │   ├── tool_protocol.py           # Tool 系统 Protocol
│   │   ├── context_protocol.py        # Context 构建 Protocol
│   │   ├── provider_protocol.py       # LLM Provider Protocol
│   │   └── events.py
│   ├── upstream/                      # Layer 1 上游兼容层（v1.2：per-commit 子目录）
│   │   ├── v2025_04/                  #   历史版本（已通过 upgrade 收编）
│   │   └── <commit_id>/               #   每个上游 commit 一个目录，由 extract 写入
│   ├── orchestrator/                  # Layer 3 差异化能力
│   └── …
├── patches/                           # 具体 Patch 内容（v1.2：per-commit 子目录）
│   ├── upstream/
│   │   ├── <old_commit>/
│   │   │   ├── <old_commit>_series
│   │   │   └── 0001.*.patch
│   │   └── <new_commit>/
│   │       ├── <new_commit>_series
│   │       └── 0001.*.patch
│   └── metadata/                      # patch 元数据（供 change_analyzer 交叉引用）
├── backup/                            # v1.2 新增：upgrade 前的 src/ 快照
│   └── backup_YYYYMMDD_HHMMSS/
├── .upstream-sync/                    # 默认输出目录
│   ├── sync-report.json
│   ├── sync-report.md
│   └── verify-report.md
├── tests/
│   ├── test_capability_contracts.py   # Protocol 契约测试
│   ├── test_layer_isolation.py        # 层间隔离测试
│   └── upstream_sync/                 # v1.2 新增：组件自身的单元测试
├── upstream_hooks.py                  # v1.2 新增：ClawCodexSyncHooks 实现
└── upstream-sync.yaml                 # 通用组件配置文件（含 source_subpath / patch_subdir）
```

### 4.3 调用关系

```
ClawCodex CI / 开发者
    │
    ▼
upstream-sync <─── 零业务耦合，通过 upstream-sync.yaml 驱动
    │
    ├── init          → 生成 upstream-sync.yaml
    ├── fetch [--ref] → 拉上游 main_branch 或指定 ref
    ├── extract <ref> → 拉 ref + 提取 source_subpath 到 src/upstream/{short_ref}
    ├── analyze A B   → 生成 .upstream-sync/sync-report.{json,md}
    ├── apply         → 应用 patches/ 队列（per-commit subdir 自动解析）
    ├── audit         → 检查层间依赖违规
    ├── sync [from] [to] [--auto] → 走 SyncOrchestrator，自动 detect refs
    ├── generate-patch --new-commit --old-commit → 基于旧 patch 模式生成新 patch
    ├── backup / restore / backup-list → src/ 快照与回滚
    ├── verify --old-commit --new-commit → 跨版本 patch 等价性检查
    ├── upgrade --new-commit --old-commit → 推荐端到端升级流程（见 §八）
    └── agent-prompt <report> → 生成 agent-instruction.md
```
    ├── audit  → 检查层间依赖违规
    └── agent-prompt → 生成 agent-instruction.md
```

---

## 五、迁移路径

基于 ClawCodex 当前 `dev-decoupling` 分支状态，按以下顺序实施：

### Step 1: 创建通用组件仓库（1-2 天）

在独立仓库（如 `clawcodex/upstream-sync`）中实现核心框架，完全不引用 ClawCodex：

1. `config.py` — Pydantic 配置模型
2. `core/vendor.py` — Git 封装（fetch、tag、branch）
3. `core/patch_engine.py` — PatchEngine 协议 + Quilt 实现
4. `core/change_analyzer.py` — `git diff` 分析 + 影响评估
5. `core/layer_auditor.py` — 层间依赖审计
6. `cli.py` — Click/Typer CLI
7. `reporters/` — JSON + Markdown 报告

**验收标准**：组件可作为独立 PyPI 包安装，`upstream-sync --help` 正常工作。

### Step 2: 在 ClawCodex 中接入验证（1 天）

1. 在 ClawCodex 根目录创建 `upstream-sync.yaml`
2. 安装组件：`pip install upstream-sync`
3. 验证各子命令：
   - `upstream-sync fetch`
   - `upstream-sync analyze upstream/v2025_04 upstream/main`
   - `upstream-sync audit`

### Step 3: 逐步迁移现有代码到三层架构（1-2 周）

1. 创建 `src/capabilities/` 目录，定义 Protocol
2. 创建 `src/upstream/v2025_04/` 目录，将当前 `src/agent/`、`src/tool_system/` 等核心逻辑逐步迁移为 Layer 1
3. 提取现有修改到 `patches/` 目录
4. 用 `upstream-sync audit` 持续检查层间隔离

### Step 4: CI 集成（0.5 天）

```yaml
# .github/workflows/upstream-detect.yml
name: Upstream Change Detection

on:
  schedule:
    - cron: "0 6 * * 1"
  workflow_dispatch:

jobs:
  detect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install upstream-sync
      - run: upstream-sync fetch
      - run: upstream-sync analyze upstream/v2025_04 upstream/main --output-dir .upstream-sync
      - name: Create issue if changes detected
        run: |
          gh issue create \
            --title "[UPSTREAM] New changes detected $(date +%Y-%m-%d)" \
            --body-file .upstream-sync/sync-report.md \
            --label "upstream-sync"
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## 六、Agent 集成

通用组件通过参数化模板实现 Agent 集成，不绑定任何特定 Agent。

### 6.1 模板文件 (`templates/agent_prompt.md.j2`)

```markdown
# 角色：上游同步维护工程师

你是一个负责维护开源项目同步的代码智能体。当前任务是将上游项目
`{{ upstream_url }}` 的更新合并到 `{{ project_name }}` 中。

## 已知上下文

- 上游版本：{{ upstream_version }}
- 当前锁定版本：{{ previous_version }}
- 总体影响：{{ overall_impact }}
- 受影响模块数：{{ module_impacts | length }}

## 层结构

{% for layer in layers %}
- **{{ layer.name }} 层**
  - 路径: {{ layer.paths | join(', ') }}
  - 允许导入: {{ layer.allowed_imports_from | join(', ') or '无限制' }}
  - 禁止导入: {{ layer.forbidden_imports_from | join(', ') or '无' }}
{% endfor %}

## 受影响模块详情

{% for imp in module_impacts %}
### {{ imp.module_name }}
- 冲突概率: {{ imp.conflict_probability }}
- 推荐策略: {{ imp.recommended_strategy }}
- 预估工作量: {{ imp.estimated_effort_minutes }} 分钟
- 受影响 Patch: {{ imp.patches_affected | join(', ') or '无' }}
{% endfor %}

## 决策树

```
patch 应用失败？
  ├─ 是 → 查看 .rej 文件
  │         ├─ 仅行号偏移/上下文偏移 → 自动刷新 patch
  │         ├─ 变量/函数重命名 → 更新 patch 中的符号名
  │         ├─ 文件移动/拆分 → 更新 patch 中的路径
  │         └─ 语义变化 → 标记 NEEDS_REVIEW，停止
  └─ 否 → 继续下一个 patch
```

## 禁止事项

- 不要直接修改核心文件绕过冲突
- 不要删除测试让构建通过
- 不要修改 Layer 2 Protocol 定义，除非明确授权
- 如果 patch 涉及核心语义变更，标记 NEEDS_HUMAN_REVIEW

## 输出要求

完成后提供：
1. 成功应用的 patches 列表
2. 需要人工审核的 patches 列表（含原因）
3. Layer 2 Protocol 影响评估
4. 测试运行结果摘要
```

### 6.2 生成 Agent Prompt

```bash
upstream-sync agent-prompt \
    .upstream-sync/sync-report.json \
    --output agent-instruction.md
```

任何 Agent（Claude Code、OpenClaw、Cursor 等）都可以消费 `agent-instruction.md`。

---

## 七、关键设计决策

| 问题 | 决策 | 理由 |
|---|---|---|
| Layer 2 Protocol 放在哪里 | **ClawCodex 项目内** | 业务领域概念，组件只提供审计框架 |
| Patch 内容管理 | **组件管理机制，ClawCodex 管理内容** | Patch 内容高度业务相关 |
| 目录结构是否写死 | **完全配置化** | 不同项目可能有不同的目录偏好 |
| Agent 绑定 | **不绑定任何 Agent** | 输出标准化上下文，通用消费 |
| 层数量是否固定为 3 | **任意数量层** | 有些项目可能需要 2 层或 4 层 |
| 语言支持 | **语言无关** | 通过配置指定文件扩展名和解析器 |
| 报告格式 | **JSON + Markdown** | 机器可读 + 人类可读双输出 |
| Patch 引擎 | **可插拔 (quilt / git-am / custom)** | 不同团队可能有不同偏好 |
| 升级时如何刷新 patch | **`upgrade` 工作流**：先 backup → 拉新 ref → extract → generate-patch → verify | 把"安全升级"封装成一个原子流程，避免误操作 |
| 是否需要 lifecycle hooks | **提供 `SyncHooks` 基类**（v1.2 新增） | 让业务方接入 IM/Issue/Dashboard 而不动组件 |
| patch 物理布局 | **per-commit 子目录**（v1.2 推荐）+ 扁平结构兼容 | 粒度细、冲突隔离、按 commit 审查 |
| 上游代码提取范围 | **`upstream.source_subpath`**（v1.2 新增，默认 `src`） | 上游仓库常含 docs/、examples/、scripts/，与业务无关 |
| sync 时如何决定 from→to | **`VendorManager.detect_sync_refs`**：取最新 `upstream/v*` tag 与 `upstream/main_branch` | 无需人工输入，CI 可直接 `sync --auto` |
| Patch 等价性如何保障 | **`Verifier`** 路径/结构/文件 hash/类型分布四步检查 | 轻量启发式，挡住"patch 数量级异常"，不替代 CI 测试 |

---

## 八、升级工作流 (Upgrade Workflow)

v1.2 起，**推荐用 `upgrade` 命令完成从旧上游 commit 到新上游 commit 的全部准备**。该命令是 `fetch` / `extract` / `generate-patch` / `verify` 的编排：

```bash
upstream-sync upgrade \
  --new-commit 68dc3c5a9f \
  --old-commit b125e16
```

### 8.1 步骤详解

| Step | 内部动作 | 失败回退 |
|------|---------|---------|
| **1. SYNC CURRENT** | 对比 `src/` 与 `src/upstream/{old_commit[:8]}`，**用当前 patch 模式刷新** `patches/upstream/{old_commit}/` | 若 `verify` 失败，停下检查旧 patch 是否有未提交修改 |
| **2. FETCH NEW** | `git fetch upstream {new_commit}` | 网络错误：重试；不存在 ref：确认 hash |
| **3. EXTRACT NEW** | 把 `upstream/{new_commit}/src/*` 提取到 `src/upstream/{new_commit[:8]}/` | 目录已存在：默认覆盖（先用 `backup` 兜底） |
| **4. GENERATE PATCHES** | `PatchGenerator.generate_patches(new, old)` 写到 `patches/upstream/{new_commit}/`；同时创建 `{new_commit}_series` | 无变更：返回空 list 不报错 |
| **5. VERIFY** | `Verifier.verify_patches(old_patches, new_patches, old_upstream, new_upstream, backup)` | failed：`upgrade` 仍继续，但打印 issues；用户决定是否 `restore` |

### 8.2 与其它命令的关系

```
   ┌─ upgrade --new-commit X --old-commit Y
   │
   │   ┌──► backup src/  ──► backup/backup_YYYYMMDD_HHMMSS/
   │   │
   │   ├──► generate-patch --new-commit Y --old-commit Y  (refresh current)
   │   │   └──► verify
   │   │
   │   ├──► fetch --ref X
   │   │
   │   ├──► extract X
   │   │   └──► vendor.extract_to_path → src/upstream/{X[:8]}/
   │   │
   │   ├──► generate-patch --new-commit X --old-commit Y
   │   │   └──► patches/upstream/{X}/ + {X}_series
   │   │
   │   └──► verify --old-commit Y --new-commit X
   │       └──► .upstream-sync/verify-report.md
   │
   ▼
(success) → apply --commit X
(failure) → restore backup/backup_YYYYMMDD_HHMMSS --clear-first
```

### 8.3 失败回退手册

| 症状 | 推荐操作 |
|------|---------|
| `verify` 报 "Too many new files" | 说明上游大改；用 `generate-patch` 改用更细粒度模式，或重写 patch |
| `apply --commit X` 失败 | `backup-list` 找到上一个 `backup_YYYYMMDD_HHMMSS`，`restore --clear-first` 回退 src/，再决定是升级 `upgrade --extract-only` 还是暂时维持旧 commit |
| 误把 `source_subpath` 写错 | `extract --ref X --output /tmp/xxx` 先验证；再修配置重跑 |
| `sync` 报 "Upstream remote not found" | 显式 `upstream-sync fetch` 一次 |

### 8.4 为什么需要 backup

`upgrade` 步骤 4 (generate-patches) 仅生成 patch 文件，**不**自动 `apply`。apply 之后可能与本地手改代码冲突。`backup src/` 是升级前的安全网；CI 上若用 `cleanup_old_backups(keep_count=5)` 自动轮转，磁盘压力可控。

---

## 附录 A: 配置完整参考

### `upstream-sync.yaml` 完整字段说明

```yaml
# 项目基本信息
project_name: string              # 项目名称，用于报告和 Agent Prompt
source_lang: string               # 源代码语言：python | typescript | rust | go | ...

# 上游仓库配置
upstream:
  remote_url: string              # 上游仓库 Git URL
  main_branch: string             # 上游主分支名（默认 main）
  vendor_branch: string           # 本地 vendor 镜像分支名（默认 upstream/vendor）
  version_tag_format: string      # 版本标签格式，支持 {YYYY} {MM} {DD}（默认 upstream/v{YYYY}_{MM}）
  source_subpath: string          # v1.2 新增：只提取上游该子目录（默认 src）

# 层定义（隔离架构的核心）
layers:
  - name: string                  # 层名称
    paths:                        # 该层包含的目录/文件路径列表
      - Path
    allowed_imports_from:         # 允许从此层导入的模块前缀列表（可选；v1.2 真正生效）
      - string
    forbidden_imports_from:       # 禁止从此层导入的模块前缀列表（可选，优先于 allowed）
      - string

# Patch 队列配置
patches:
  directory: Path                 # Patch 文件存放目录（默认 patches）
  engine: string                  # Patch 引擎：quilt | git-am | custom（默认 quilt）
  custom_command: string          # 自定义引擎命令（engine=custom 时必填）
  series_file: Path               # Patch 应用顺序文件（默认 patches/series）
  metadata_dir: Path              # Patch 元数据目录（默认 patches/metadata）
  patch_subdir: string            # v1.2 新增：per-commit 子目录模式，支持 {commit} 占位符

# 同步策略配置
sync:
  impact_threshold_auto: string   # 自动处理阈值：low | medium | high（默认 low）
  impact_threshold_agent: string  # Agent 辅助阈值：low | medium | high（默认 medium）
  report_formats:                 # 报告输出格式列表（默认 [json, markdown]）
    - string
```

---

## 附录 B: 术语表

| 术语 | 定义 |
|------|------|
| **upstream-sync** | 通用上游代码同步组件，不感知任何业务逻辑，通过配置驱动。 |
| **机制 (Mechanism)** | 上游同步的通用能力，如 Patch 管理、层间审计、变化分析等。 |
| **策略 (Policy)** | 项目特定的决策，如 Layer Protocol 定义、Patch 内容、目录结构等。 |
| **Layer** | 代码分层中的一层，由配置定义路径和导入规则。不固定为 3 层。 |
| **Vendor Branch** | 仅用于镜像上游代码的只读分支，禁止任何人工 commit。 |
| **Patch Queue** | 以 `quilt` 或 `git am` 管理的 patch 文件序列，显式记录对 Layer 1 的修改。 |
| **Agent Prompt 模板** | 参数化的 Jinja2 模板，用于生成标准化的 Agent 指令。 |
| **契约测试** | 验证各层是否遵守导入规则的测试（由使用者自定义，组件提供审计能力）。 |
| **source_subpath** (v1.2) | 上游仓库内要提取的子目录（如 `src`），由 `UpstreamConfig.source_subpath` 控制。 |
| **patch_subdir** (v1.2) | per-commit patch 子目录模式（如 `patches/upstream/{commit}`），由 `PatchConfig.patch_subdir` 控制。 |
| **SyncOrchestrator** (v1.2) | 端到端 sync 流水线协调器，封装 fetch/analyze/apply/audit + hooks。 |
| **SyncHooks** (v1.2) | 生命周期钩子基类，提供 pre_/post_ 四阶段（fetch/analyze/apply/audit）扩展点。 |
| **Upgrade Workflow** (v1.2) | `upgrade` 命令编排的端到端流程：sync current → fetch → extract → generate-patch → verify。 |
| **PatchGenerator** (v1.2) | 基于旧 patch 模式为新上游 commit 自动生成 patch 的组件。 |
| **BackupManager** (v1.2) | `src/` 目录的备份与恢复管理器，提供 `backup`/`restore`/`backup-list` 三个子命令。 |
| **Verifier** (v1.2) | 跨版本验证 patch 功能等价性的轻量启发式检查器。 |
