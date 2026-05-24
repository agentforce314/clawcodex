# upstream-sync

A generic, configuration-driven toolkit for managing upstream code synchronization in fork/port projects.

## Features

- **Zero hard-coded project knowledge** — everything driven by `upstream-sync.yaml`
- **Layer dependency auditing** via AST analysis of Python source
- **Pluggable patch engines** — quilt, git-am, or custom command
- **Upstream diff analysis** with conflict probability and effort estimation
- **Machine-readable + human-readable reports** (JSON + Markdown)
- **Standardized Agent prompts** via Jinja2 templates
- **Lifecycle hooks** for custom extension at each pipeline stage

## Installation

```bash
pip install upstream-sync
# or from source
pip install -e .
```

## Quick Start

```bash
# 1. Initialise a config for your project
upstream-sync init --template python-port

# 2. Configure upstream remote and layers in upstream-sync.yaml

# 3. Fetch upstream code
upstream-sync fetch

# 4. Analyse changes between two versions
upstream-sync analyze upstream/v2025_04 upstream/v2025_05

# 5. Apply your patch queue
upstream-sync apply

# 6. Audit layer dependencies
upstream-sync audit

# 7. Run full pipeline (auto-detect refs)
upstream-sync sync

# 8. Generate agent prompt from report
upstream-sync agent-prompt .upstream-sync/sync-report.json
```

## Configuration

All settings live in `upstream-sync.yaml`. Example:

```yaml
project_name: "my-project"
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

patches:
  directory: "patches"
  engine: "quilt"          # quilt | git-am | custom
  # Per-commit subdirectory structure (recommended for large patch sets)
  patch_subdir: "patches/upstream/{commit}"
  metadata_dir: "patches/metadata"

  # When patch_subdir is set:
  # - Patches are loaded from patches/upstream/{commit}/
  # - Series file: patches/upstream/{commit}/{commit}_series
  # - Individual patches: patches/upstream/{commit}/*.patch

sync:
  impact_threshold_auto: "low"
  impact_threshold_agent: "medium"
  report_formats: ["json", "markdown"]
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `init [--template TEMPLATE]` | Scaffold a new `upstream-sync.yaml` |
| `fetch` | Pull upstream code into vendor branch |
| `analyze FROM TO` | Compare two refs and write impact reports |
| `apply [--commit COMMIT]` | Apply the configured patch queue (auto-detects commit if omitted) |
| `audit` | Report layer import violations |
| `sync [--auto]` | Run fetch→analyze→apply→audit pipeline |
| `agent-prompt REPORT` | Render agent prompt from JSON report |

### Patch Directory Structure

Two structures are supported:

**1. Flat (legacy)**
```
patches/
├── series          # quilt series file
├── 0001.patch
├── 0002.patch
└── metadata/
```

**2. Per-commit subdirectory (recommended)**
```
patches/upstream/
├── b125e16/
│   ├── b125e16_series        # quilt series file
│   ├── 0001.src.init..py.patch
│   ├── 0002.src.cli.py.patch
│   └── ...
└── b125e17/
    ├── b125e17_series
    └── ...
```

The per-commit structure is recommended because:
- **Granularity**: Each file change is a separate patch, easy to review
- **Selective application**: Can apply patches file-by-file when needed
- **Conflict isolation**: Changes to different files don't conflict

### Workflow Notes

1. **Apply patches on a clean working tree** — `git am` refuses to apply when there are uncommitted changes. Use `git stash` or apply on the vendor branch.
2. **Patch application order** — Patches in the series file are applied in order. For new files, this matters less; for modifications, order can matter.
3. **Conflict handling** — If a patch fails to apply, use `git am --skip` to skip it or `git am --abort` to cancel the entire operation.

## Architecture

```
upstream-sync/
├── config.py           # Pydantic models (zero hard-coding)
├── cli.py              # Typer CLI (7 commands)
├── core/
│   ├── vendor.py       # Git remote / fetch / tag / branch
│   ├── patch_engine.py # Protocol + factory
│   ├── change_analyzer.py  # git diff + impact analysis
│   ├── layer_auditor.py    # AST-based import audit
│   └── sync_orchestrator.py  # Pipeline coordinator
├── adapters/           # quilt / git-am / custom backends
├── reporters/          # JSON + Markdown output
├── templates/          # Jinja2 agent prompt template
└── hooks/              # Lifecycle hook base class
```

## Development

```bash
# Run tests
pytest tests/upstream_sync/

# Type check
mypy src/upstream_sync/

# Install in dev mode
pip install -e ".[dev]"
```