# upstream_sync/cli.py
"""Unified CLI entry-point for upstream-sync.

All commands are configuration-driven via ``upstream-sync.yaml``.
"""

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

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ProjectConfig(**data)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init(
    template: str = typer.Option("blank", help="Template: blank, python-port, node-fork, rust-fork"),
    output: Path = typer.Option(DEFAULT_CONFIG, help="Output config path"),
) -> None:
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


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

@app.command()
def fetch(
    ref: str = typer.Option(
        None,
        help="Specific ref (commit hash, tag, or branch) to fetch (default: main branch)",
    ),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
) -> None:
    """Fetch upstream code to vendor branch.

    By default fetches the main branch. Use --ref to fetch a specific
    commit, tag, or branch.
    """
    cfg = load_config(config)
    vendor = VendorManager(Path("."), cfg.upstream)
    vendor.ensure_remote()
    if ref:
        commit = vendor.fetch_ref(ref)
        typer.echo(f"Fetched upstream/{ref} at {commit}")
    else:
        commit = vendor.fetch()
        typer.echo(f"Fetched upstream/{cfg.upstream.main_branch} at {commit}")


@app.command()
def extract(
    ref: str = typer.Argument(..., help="Upstream ref (commit, tag, or branch) to extract"),
    output: Path = typer.Option(
        None,
        help="Output directory (default: src/upstream/{short_ref})",
    ),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
) -> None:
    """Fetch a specific upstream ref and extract only the source sub-path.

    The source sub-path is defined by ``upstream.source_subpath`` in config
    (default: ``src``). Only that sub-directory is extracted to the output
    location, keeping the vendor tree clean.
    """
    cfg = load_config(config)
    vendor = VendorManager(Path("."), cfg.upstream)
    vendor.ensure_remote()

    # Fetch the ref first
    commit = vendor.fetch_ref(ref)
    typer.echo(f"Fetched upstream/{ref} at {commit}")

    # Determine output path
    short_ref = commit[:8]
    if output is None:
        output = Path("src") / "upstream" / short_ref

    # Extract the source subpath
    vendor.extract_to_path(
        ref=ref,
        subpath=cfg.upstream.source_subpath,
        target_path=output,
    )
    typer.echo(f"Extracted {cfg.upstream.source_subpath}/ to {output}")


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

@app.command()
def analyze(
    from_ref: str = typer.Argument(..., help="Base ref/tag to compare from"),
    to_ref: str = typer.Argument(..., help="Target ref/tag to compare to"),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
    output_dir: Path = typer.Option(Path(".upstream-sync"), help="Directory to write reports"),
) -> None:
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


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _resolve_commit_placeholder(path: Path, commit: str) -> Path:
    """Resolve {commit} placeholder in a path string."""
    path_str = str(path)
    if "{commit}" in path_str:
        return Path(path_str.format(commit=commit))
    return path


@app.command()
def apply(
    commit: str = typer.Option(
        None,
        help="Upstream commit hash to apply patches for (auto-detected if omitted)",
    ),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
) -> None:
    """Apply the patch queue.

    When ``patch_subdir`` is configured (recommended), patches are loaded from
    the per-commit subdirectory.  Otherwise, falls back to the flat patch
    directory.
    """
    cfg = load_config(config)

    # Auto-detect commit if not provided
    if commit is None:
        vendor = VendorManager(Path("."), cfg.upstream)
        try:
            commit = vendor.fetch()
        except Exception:
            typer.echo("Could not auto-detect upstream commit. Provide --commit explicitly.")
            raise typer.Exit(1)

    # Determine patch directory and series file
    if cfg.patches.patch_subdir:
        # Per-commit subdirectory structure: patches/upstream/{commit}/
        patch_dir = _resolve_commit_placeholder(
            Path(cfg.patches.patch_subdir), commit
        )
        series_file = patch_dir / f"{commit}_series"
    else:
        # Flat structure: patches/
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


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

@app.command()
def audit(
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
) -> None:
    """Audit layer dependency violations."""
    cfg = load_config(config)
    auditor = LayerAuditor(cfg)
    violations = auditor.audit()
    print(auditor.report(violations))
    if violations:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# sync (full pipeline)
# ---------------------------------------------------------------------------

@app.command()
def sync(
    from_ref: str | None = typer.Argument(None, help="Base ref/tag to compare from (auto-detected if omitted)"),
    to_ref: str | None = typer.Argument(None, help="Target ref/tag to compare to (auto-detected if omitted)"),
    auto: bool = typer.Option(False, help="Auto-resolve low-impact changes"),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
) -> None:
    """Run full sync pipeline: fetch -> analyze -> apply -> audit.

    Refs are auto-detected from local upstream/* version tags when not provided.
    """
    cfg = load_config(config)
    orchestrator = SyncOrchestrator(Path("."), cfg)

    detected_from, detected_to = orchestrator.detect_refs()
    from_ref = from_ref or detected_from
    to_ref = to_ref or detected_to

    typer.echo(f"Syncing: {from_ref} -> {to_ref}")
    results = orchestrator.run_full_sync(from_ref=from_ref, to_ref=to_ref, auto=auto)

    report = results["report"]
    typer.echo(f"\nOverall impact: {report.overall_impact}")
    typer.echo(f"Files changed upstream: {report.statistics.get('files_changed_upstream', 0)}")
    typer.echo(f"Modules affected: {report.statistics.get('modules_affected', 0)}")

    if results["applied"]:
        typer.echo(f"\nPatches applied: {len(results['applied'])}")
    if results["failed"]:
        typer.echo(f" Patches failed: {len(results['failed'])}")
    if results["needs_review"]:
        typer.echo(f" Needs review: {len(results['needs_review'])}")
    if results["violations"]:
        typer.echo(f"\nLayer violations: {len(results['violations'])}")
        for v in results["violations"]:
            typer.echo(f"  [{v.layer}] {v.file}:{v.line_number} -> {v.forbidden_import}")

    typer.echo("\nSync pipeline complete.")


# ---------------------------------------------------------------------------
# generate-patch
# ---------------------------------------------------------------------------

@app.command("generate-patch")
def generate_patch(
    new_commit: str = typer.Option(..., help="New upstream commit hash to generate patches for"),
    old_commit: str = typer.Option(..., help="Old upstream commit hash to reference for patch patterns"),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
    output: Path | None = typer.Option(None, help="Output directory (default: patches/upstream/{new_commit})"),
) -> None:
    """Generate new patches based on old patch patterns.

    This command analyzes old patches from old_commit and generates new patches
    for new_commit by understanding the transformation patterns.
    """
    cfg = load_config(config)
    generator = PatchGenerator(Path("."), cfg)

    if output is None:
        if cfg.patches.patch_subdir:
            output = Path(str(cfg.patches.patch_subdir).format(commit=new_commit))
        else:
            output = cfg.patches.directory

    typer.echo(f"Generating patches for {new_commit} based on {old_commit}...")
    patches = generator.generate_patches(new_commit, old_commit, output)

    if patches:
        # Create series file
        series_file = output / f"{new_commit}_series"
        generator.create_series_file(patches, series_file)
        typer.echo(f"Generated {len(patches)} patches in {output}")
        typer.echo(f"Series file: {series_file}")
    else:
        typer.echo("No patches generated (no changes detected)")


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------

@app.command()
def backup(
    backup_root: Path = typer.Option(None, help="Backup root directory (default: backup/)"),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
) -> None:
    """Backup src/ directory excluding src/upstream/.

    Creates a timestamped backup of the src/ directory, excluding upstream
    source code and other specified patterns.
    """
    cfg = load_config(config)

    backup_mgr = BackupManager(Path("."), backup_root)
    backup_path = backup_mgr.backup(Path("src"))

    typer.echo(f"Backup created: {backup_path}")
    typer.echo(f"Total files backed up: {len(list(backup_path.rglob('*')))}")


@app.command()
def restore(
    backup_dir: Path = typer.Argument(..., help="Backup directory to restore from"),
    clear_first: bool = typer.Option(False, help="Clear src/ before restoring"),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
) -> None:
    """Restore a backup to the src/ directory.

    Restores files from a previously created backup directory.
    Use 'backup-list' to see available backups.
    """
    backup_mgr = BackupManager(Path("."))
    restored = backup_mgr.restore(backup_dir, Path("src"), clear_first=clear_first)
    typer.echo(f"Restored {len(restored)} files from {backup_dir}")


@app.command("backup-list")
def backup_list(
    backup_root: Path = typer.Option(None, help="Backup root directory (default: backup/)"),
) -> None:
    """List all available backups."""
    backup_mgr = BackupManager(Path("."), backup_root)
    backups = backup_mgr.list_backups()

    if not backups:
        typer.echo("No backups found")
        return

    typer.echo("Available backups:")
    typer.echo("-" * 50)
    for b in backups:
        typer.echo(f"  {b['path'].name} - {b['file_count']} files")


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

@app.command()
def verify(
    old_commit: str = typer.Option(..., help="Old upstream commit hash"),
    new_commit: str = typer.Option(..., help="New upstream commit hash"),
    output: Path = typer.Option(Path(".upstream-sync/verify-report.md"), help="Output report path"),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
) -> None:
    """Verify patch functional equivalence between upstream versions.

    Validates that:
    1. New patches apply successfully to new upstream code
    2. The transformation preserves functional equivalence
    3. Patch structure matches expected patterns
    """
    cfg = load_config(config)

    old_patches_dir = Path(str(cfg.patches.patch_subdir).format(commit=old_commit)) if cfg.patches.patch_subdir else cfg.patches.directory
    new_patches_dir = Path(str(cfg.patches.patch_subdir).format(commit=new_commit)) if cfg.patches.patch_subdir else cfg.patches.directory
    old_upstream_dir = Path("src") / "upstream" / old_commit[:8]
    new_upstream_dir = Path("src") / "upstream" / new_commit[:8]
    backup_dir = Path("backup")

    verifier = Verifier(Path("."))
    result = verifier.verify_patches(
        old_patches_dir=old_patches_dir,
        new_patches_dir=new_patches_dir,
        old_upstream_dir=old_upstream_dir,
        new_upstream_dir=new_upstream_dir,
        backup_dir=backup_dir,
    )

    verifier.generate_verification_report(result, output)
    typer.echo(f"Verification {'PASSED' if result.passed else 'FAILED'}")
    typer.echo(f"Report: {output}")

    if result.details and "issues" in result.details:
        for issue in result.details["issues"]:
            typer.echo(f"  - {issue}")

    if not result.passed:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# agent-prompt
# ---------------------------------------------------------------------------

@app.command("agent-prompt")
def agent_prompt(
    report: Path = typer.Argument(..., help="Path to sync-report.json"),
    config: Path = typer.Option(DEFAULT_CONFIG, help="Path to upstream-sync.yaml"),
    output: Path = typer.Option(Path("agent-instruction.md"), help="Output prompt file"),
) -> None:
    """Generate a standardized agent prompt from the sync report."""
    import json

    from jinja2 import Template

    cfg = load_config(config)
    report_data = json.loads(report.read_text())

    template_text = (
        Path(__file__).parent / "templates" / "agent_prompt.md.j2"
    ).read_text()
    template = Template(template_text)

    rendered = template.render(
        project_name=cfg.project_name,
        upstream_url=cfg.upstream.remote_url,
        layers=[layer.model_dump(mode='json') for layer in cfg.layers],
        **report_data,
    )
    output.write_text(rendered, encoding="utf-8")
    typer.echo(f"Agent prompt written to {output}")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _blank_template() -> str:
    return """project_name: "my-project"
source_lang: "python"

upstream:
  remote_url: "https://github.com/original/repo.git"
  main_branch: "main"
  vendor_branch: "upstream/vendor"
  version_tag_format: "upstream/v{YYYY}_{MM}"
  source_subpath: "src"  # Only extract this sub-path from upstream (default: src)

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
  source_subpath: "src"  # Only extract this sub-path from upstream (default: src)

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
    return _python_port_template().replace('source_lang: "python"', 'source_lang: "typescript"')


def _rust_fork_template() -> str:
    return _python_port_template().replace('source_lang: "python"', 'source_lang: "rust"')


if __name__ == "__main__":
    app()
