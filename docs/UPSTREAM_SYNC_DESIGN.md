# 上游同步组件解耦设计文档

> 文档路径: `docs/UPSTREAM_SYNC_DESIGN-decoupling.md`
> 版本: v1.1
> 更新日期: 2026-05-19
> 关联文档: [UPSTREAM_SYNC_DESIGN.md](UPSTREAM_SYNC_DESIGN.md), [FEATURE_PLAN.md](FEATURE_PLAN.md), [PROGRESS.md](PROGRESS.md)

---

## 目录

- [一、解耦目标与原则](#一解耦目标与原则)
- [二、核心解耦策略](#二核心解耦策略)
- [三、通用组件架构 (upstream-sync)](#三通用组件架构-upstream-sync)
  - [3.1 组件目录结构](#31-组件目录结构)
  - [3.2 配置层 (config.py)](#32-配置层-configpy)
  - [3.3 Vendor 管理 (core/vendor.py)](#33-vendor-管理-corevendorpy)
  - [3.4 Patch 引擎 (core/patch_engine.py)](#34-patch-引擎-corepatch_enginepy)
  - [3.5 变化分析器 (core/change_analyzer.py)](#35-变化分析器-corechange_analyzerpy)
  - [3.6 层间审计 (core/layer_auditor.py)](#36-层间审计-corelayer_auditorpy)
  - [3.7 报告生成 (reporters/)](#37-报告生成-reporters)
  - [3.8 CLI (cli.py)](#38-cli-clipy)
- [四、ClawCodex 集成方式](#四clawcodex-集成方式)
  - [4.1 配置文件示例](#41-配置文件示例)
  - [4.2 保留在 ClawCodex 内的内容](#42-保留在-clawcodex-内的内容)
  - [4.3 调用关系](#43-调用关系)
- [五、迁移路径](#五迁移路径)
- [六、Agent 集成](#六agent-集成)
- [七、关键设计决策](#七关键设计决策)
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
│   ├── __init__.py
│   ├── cli.py                          # 统一 CLI 入口 (Typer)
│   ├── config.py                       # Pydantic 配置模型
│   ├── core/
│   │   ├── __init__.py
│   │   ├── vendor.py                   # Vendor Branch 管理
│   │   ├── patch_engine.py             # Patch 应用引擎抽象
│   │   ├── change_analyzer.py          # 上游 diff 分析器
│   │   ├── sync_orchestrator.py        # 同步流程编排
│   │   └── layer_auditor.py            # 层间依赖审计
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── quilt.py                    # Quilt 适配器
│   │   ├── git_am.py                   # Git am 适配器
│   │   └── custom.py                   # 自定义命令适配器
│   ├── reporters/
│   │   ├── __init__.py
│   │   ├── json_reporter.py            # 机器可读报告
│   │   └── markdown_reporter.py        # 人类可读报告
│   ├── templates/
│   │   └── agent_prompt.md.j2          # Agent Prompt Jinja2 模板
│   └── hooks/
│       └── base.py                     # 生命周期钩子基类
└── tests/
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


class PatchConfig(BaseModel):
    directory: Path = Path("patches")
    engine: Literal["quilt", "git-am", "custom"] = "quilt"
    custom_command: str | None = None   # engine=custom 时使用
    series_file: Path = Path("patches/series")
    metadata_dir: Path = Path("patches/metadata")
    # 可选：每个 upstream commit 对应一个子目录
    # 示例："patches/upstream/{commit}" 解析为 "patches/upstream/b125e16"
    patch_subdir: str | None = Field(
        default=None,
        description="每个 commit 的补丁子目录模式，支持 {commit} 占位符"
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

### 3.3 Vendor 管理 (core/vendor.py)

管理上游仓库镜像和版本标签。完全通用，不感知业务。

```python
# upstream_sync/core/vendor.py
import subprocess
from pathlib import Path


class VendorManager:
    def __init__(self, repo_root: Path, upstream: UpstreamConfig):
        self.repo_root = repo_root
        self.cfg = upstream

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

    def fetch(self) -> str:
        """拉取上游 main，返回最新 commit hash。"""
        subprocess.run(
            ["git", "fetch", "upstream", self.cfg.main_branch],
            cwd=self.repo_root, check=True
        )
        result = subprocess.run(
            ["git", "rev-parse", f"upstream/{self.cfg.main_branch}"],
            cwd=self.repo_root, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    def create_version_tag(self, version: str, commit: str) -> None:
        """创建版本锁定标签，如 upstream/v2025_06。"""
        from datetime import datetime
        dt = datetime.strptime(version, "%Y.%m.%d")
        tag = self.cfg.version_tag_format.format(YYYY=dt.year, MM=f"{dt.month:02d}")
        subprocess.run(
            ["git", "tag", tag, commit],
            cwd=self.repo_root, check=True
        )

    def checkout_vendor(self) -> None:
        """切换到 vendor 分支（只读镜像）。"""
        result = subprocess.run(
            ["git", "branch", "--list", self.cfg.vendor_branch],
            cwd=self.repo_root, capture_output=True, text=True
        )
        if not result.stdout.strip():
            subprocess.run(
                ["git", "checkout", "-b", self.cfg.vendor_branch],
                cwd=self.repo_root, check=True
            )
        subprocess.run(
            ["git", "checkout", self.cfg.vendor_branch],
            cwd=self.repo_root, check=True
        )
```

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

### 3.5 变化分析器 (core/change_analyzer.py)

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

### 3.6 层间审计 (core/layer_auditor.py)

审计层间依赖违规。层定义完全由配置决定。

```python
# upstream_sync/core/layer_auditor.py
import ast
from pathlib import Path
from dataclasses import dataclass


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
        violations = []
        for layer in self.layers:
            for path in layer.paths:
                if not path.exists():
                    continue
                for py_file in path.rglob("*.py"):
                    imports = self._extract_imports(py_file)
                    for forbidden in layer.forbidden_imports_from:
                        for imp, lineno in imports:
                            if imp.startswith(forbidden):
                                violations.append(Violation(
                                    file=py_file,
                                    forbidden_import=imp,
                                    layer=layer.name,
                                    line_number=lineno
                                ))
        return violations

    def _extract_imports(self, py_file: Path) -> list[tuple[str, int]]:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            return []

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.append((module, node.lineno))
        return imports

    def report(self, violations: list[Violation]) -> str:
        if not violations:
            return "No layer violations found."
        lines = [f"Found {len(violations)} layer violation(s):\n"]
        for v in violations:
            lines.append(f"  [{v.layer}] {v.file}:{v.line_number} imports '{v.forbidden_import}'")
        return "\n".join(lines)
```

### 3.7 报告生成 (reporters/)

```python
# upstream_sync/reporters/json_reporter.py
import json
from pathlib import Path
from upstream_sync.core.change_analyzer import ChangeReport


class JSONReporter:
    def emit(self, report: ChangeReport, path: Path) -> None:
        path.write_text(report.to_json(indent=2), encoding="utf-8")


# upstream_sync/reporters/markdown_reporter.py
from pathlib import Path
from upstream_sync.core.change_analyzer import ChangeReport


class MarkdownReporter:
    def emit(self, report: ChangeReport, path: Path) -> None:
        lines = [
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
            lines.extend([
                "## Action Items",
                "",
            ])
            for item in report.action_items:
                lines.append(f"- [{item['action'].upper()}] {item['module']}: {item['reason']}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
```

### 3.8 CLI (cli.py)

```python
# upstream_sync/cli.py
import typer
from pathlib import Path
from upstream_sync.config import ProjectConfig
from upstream_sync.core.vendor import VendorManager
from upstream_sync.core.patch_engine import create_engine
from upstream_sync.core.change_analyzer import ChangeAnalyzer
from upstream_sync.core.layer_auditor import LayerAuditor
from upstream_sync.reporters.json_reporter import JSONReporter
from upstream_sync.reporters.markdown_reporter import MarkdownReporter

app = typer.Typer(help="upstream-sync: Generic upstream code synchronization tool")


def load_config(path: Path) -> ProjectConfig:
    import yaml
    data = yaml.safe_load(path.read_text())
    return ProjectConfig(**data)


@app.command()
def init(
    template: str = typer.Option("blank", help="Template: blank, python-port, node-fork, rust-fork"),
    output: Path = typer.Option(Path("upstream-sync.yaml"), help="Output config path")
):
    """Initialize upstream-sync configuration for the current project."""
    templates = {
        "blank": _blank_template(),
        "python-port": _python_port_template(),
        "node-fork": _node_fork_template(),
        "rust-fork": _rust_fork_template(),
    }
    content = templates.get(template, templates["blank"])
    output.write_text(content, encoding="utf-8")
    typer.echo(f"Created {output} (template: {template})")


@app.command()
def fetch(
    config: Path = typer.Option(Path("upstream-sync.yaml"), help="Path to upstream-sync.yaml")
):
    """Fetch upstream latest code to vendor branch."""
    cfg = load_config(config)
    vendor = VendorManager(Path("."), cfg.upstream)
    vendor.ensure_remote()
    commit = vendor.fetch()
    typer.echo(f"Fetched upstream/{cfg.upstream.main_branch} at {commit}")


@app.command()
def analyze(
    from_ref: str = typer.Argument(..., help="Base ref/tag to compare from"),
    to_ref: str = typer.Argument(..., help="Target ref/tag to compare to"),
    config: Path = typer.Option(Path("upstream-sync.yaml"), help="Path to upstream-sync.yaml"),
    output_dir: Path = typer.Option(Path(".upstream-sync"), help="Directory to write reports")
):
    """Analyze upstream changes and generate impact reports."""
    cfg = load_config(config)
    analyzer = ChangeAnalyzer(Path("."), cfg)
    report = analyzer.analyze(from_ref, to_ref)

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


@app.command()
def apply(
    config: Path = typer.Option(Path("upstream-sync.yaml"), help="Path to upstream-sync.yaml")
):
    """Apply the patch queue."""
    cfg = load_config(config)
    engine = create_engine(cfg.patches)
    result = engine.apply_all(cfg.patches.directory, cfg.patches.series_file)
    typer.echo(f"Applied: {len(result.success)}, Failed: {len(result.failed)}, Needs Review: {len(result.needs_review)}")
    if result.failed:
        raise typer.Exit(1)


@app.command()
def audit(
    config: Path = typer.Option(Path("upstream-sync.yaml"), help="Path to upstream-sync.yaml")
):
    """Audit layer dependency violations."""
    cfg = load_config(config)
    auditor = LayerAuditor(cfg)
    violations = auditor.audit()
    print(auditor.report(violations))
    if violations:
        raise typer.Exit(1)


@app.command()
def sync(
    auto: bool = typer.Option(False, help="Auto-resolve low-impact changes"),
    config: Path = typer.Option(Path("upstream-sync.yaml"), help="Path to upstream-sync.yaml")
):
    """Run full sync pipeline: fetch -> analyze -> apply -> audit."""
    fetch(config)
    # TODO: detect from/to refs automatically or via config
    typer.echo("Sync pipeline complete.")


@app.command()
def agent_prompt(
    report: Path = typer.Argument(..., help="Path to sync-report.json"),
    config: Path = typer.Option(Path("upstream-sync.yaml"), help="Path to upstream-sync.yaml"),
    output: Path = typer.Option(Path("agent-instruction.md"), help="Output prompt file")
):
    """Generate a standardized agent prompt from the sync report."""
    import json
    from jinja2 import Template

    cfg = load_config(config)
    report_data = json.loads(report.read_text())

    template_text = (Path(__file__).parent / "templates" / "agent_prompt.md.j2").read_text()
    template = Template(template_text)

    rendered = template.render(
        project_name=cfg.project_name,
        upstream_url=cfg.upstream.remote_url,
        layers=cfg.layers,
        **report_data
    )
    output.write_text(rendered, encoding="utf-8")
    typer.echo(f"Agent prompt written to {output}")


def _blank_template() -> str:
    return """project_name: "my-project"
source_lang: "python"

upstream:
  remote_url: "https://github.com/original/repo.git"
  main_branch: "main"
  vendor_branch: "upstream/vendor"
  version_tag_format: "upstream/v{YYYY}_{MM}"

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

sync:
  impact_threshold_auto: "low"
  impact_threshold_agent: "medium"
  report_formats: ["json", "markdown"]
```

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
│   ├── upstream/                      # Layer 1 上游兼容层
│   │   └── v2025_04/
│   │       ├── agent_loop/
│   │       ├── tool_system/
│   │       └── _bridge.py
│   └── orchestrator/                  # Layer 3 差异化能力
├── patches/                           # 具体 Patch 内容
│   ├── 0001-port-to-python.patch
│   ├── 0002-add-provider-abstraction.patch
│   └── ...
├── tests/
│   ├── test_capability_contracts.py   # Protocol 契约测试
│   └── test_layer_isolation.py        # 层间隔离测试
└── upstream-sync.yaml                 # 通用组件配置文件
```

### 4.3 调用关系

```
ClawCodex CI / 开发者
    │
    ▼
upstream-sync <─── 零业务耦合，通过 upstream-sync.yaml 驱动
    │
    ├── fetch  → 管理 upstream/vendor 分支
    ├── analyze → 生成 .upstream-sync/sync-report.{json,md}
    ├── apply  → 应用 patches/ 队列
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

# 层定义（隔离架构的核心）
layers:
  - name: string                  # 层名称
    paths:                        # 该层包含的目录/文件路径列表
      - Path
    allowed_imports_from:         # 允许从此层导入的模块前缀列表（可选）
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
