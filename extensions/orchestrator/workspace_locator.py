"""Shared workspace location resolution for orchestrator + CLI.

Provides unified workspace root resolution from:
1. CLAWCODEX_WORKSPACE_ROOT environment variable
2. Orchestrator metadata file (~/.clawcodex/orchestrator/{slug}/metadata.json)
3. --workflow parameter (parse WORKFLOW.md for workspace.root)
4. --workspace parameter (direct path)
5. CWD fallback
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config.schema import WorkflowConfig


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLAWCODEX_BASE = Path.home() / ".clawcodex"
ORCHESTRATOR_DIR = CLAWCODEX_BASE / "orchestrator"


def _slug_from_workspace(workspace_root: str | Path) -> str:
    """Generate a slug from workspace root for metadata directory naming."""
    path = str(workspace_root).strip().replace("/", "-").replace("\\", "-")
    # Use the last meaningful segment
    parts = [p for p in path.split("-") if p and p not in ("tmp", ".clawcodex", "~")]
    return "-".join(parts[-3:]) if parts else "default"


# ---------------------------------------------------------------------------
# Workspace root resolution
# ---------------------------------------------------------------------------

def get_workspace_root(
    workspace_arg: str | None = None,
    workflow_path: str | None = None,
) -> Path | None:
    """Resolve workspace root from multiple sources.

    Priority (highest to lowest):
    1. workspace_arg - explicit --workspace path
    2. CLAWCODEX_WORKSPACE_ROOT env var
    3. workflow_path - parse workspace.root from WORKFLOW.md
    4. orchestrator metadata file
    5. CWD fallback

    Args:
        workspace_arg: Direct --workspace path (highest priority)
        workflow_path: Path to WORKFLOW.md file (parse workspace.root from it)

    Returns:
        Resolved workspace root path, or None if not found
    """
    # 1. Explicit workspace path
    if workspace_arg:
        path = Path(workspace_arg).expanduser().resolve()
        if path.exists() or path.parent.exists():
            return path
        # Still return - may not exist yet for new orchestrator runs

    # 2. Environment variable
    env_path = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if env_path:
        return Path(env_path).expanduser().resolve()

    # 3. Parse from WORKFLOW.md
    if workflow_path:
        ws = _parse_workspace_from_workflow(workflow_path)
        if ws:
            return ws

    # 4. Check orchestrator metadata (latest for any project)
    metadata_path = _find_latest_metadata()
    if metadata_path:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        return Path(data["workspace_root"])

    # 5. CWD fallback
    cwd_registry = Path.cwd() / ".clawcodex_issue_registry.json"
    if cwd_registry.exists():
        return Path.cwd()

    # 6. Default
    default_ws = CLAWCODEX_BASE / "workspace"
    if default_ws.exists():
        return default_ws

    return None


def get_registry_path(
    workspace_arg: str | None = None,
    workflow_path: str | None = None,
) -> Path | None:
    """Get registry path from resolved workspace root."""
    root = get_workspace_root(workspace_arg=workspace_arg, workflow_path=workflow_path)
    if root:
        return root / ".clawcodex_issue_registry.json"

    # No workspace found
    return None


# ---------------------------------------------------------------------------
# Workflow parsing
# ---------------------------------------------------------------------------

def _parse_workspace_from_workflow(workflow_path: str | Path) -> Path | None:
    """Parse workspace.root from WORKFLOW.md YAML front matter."""
    try:
        import yaml

        content = Path(workflow_path).read_text(encoding="utf-8")
        # Split front matter from body
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                front_matter = yaml.safe_load(parts[1])
                ws_root = front_matter.get("workspace", {}).get("root")
                if ws_root:
                    return Path(os.path.expanduser(ws_root))
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Orchestrator metadata management
# ---------------------------------------------------------------------------

def _find_latest_metadata() -> Path | None:
    """Find the most recently modified orchestrator metadata file."""
    if not ORCHESTRATOR_DIR.exists():
        return None
    metadata_files = []
    for md in ORCHESTRATOR_DIR.iterdir():
        if md.is_dir():
            mf = md / "metadata.json"
            if mf.exists():
                metadata_files.append((mf.stat().st_mtime, mf))
    if not metadata_files:
        return None
    metadata_files.sort(key=lambda x: x[0], reverse=True)
    return metadata_files[0][1]


def write_orchestrator_metadata(
    workspace_root: str | Path,
    workflow_path: str | None = None,
    started_at: float | None = None,
) -> Path:
    """Write orchestrator metadata for later CLI discovery.

    Creates ~/.clawcodex/orchestrator/{slug}/metadata.json

    Args:
        workspace_root: The orchestrator's workspace root
        workflow_path: Optional path to WORKFLOW.md (for project identification)

    Returns:
        Path to the metadata file written
    """
    import time
    import hashlib

    ws_str = str(workspace_root)
    slug = _slug_from_workspace(ws_str)

    # Create metadata directory
    metadata_dir = ORCHESTRATOR_DIR / slug
    metadata_dir.mkdir(parents=True, exist_ok=True)

    metadata_file = metadata_dir / "metadata.json"

    # Determine project slug from workflow if available
    project_slug = None
    if workflow_path:
        try:
            import yaml
            content = Path(workflow_path).read_text(encoding="utf-8")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    fm = yaml.safe_load(parts[1])
                    tracker = fm.get("tracker", {})
                    owner = tracker.get("owner", "")
                    repo = tracker.get("repo", "")
                    if owner and repo:
                        project_slug = f"{owner}-{repo}"
        except Exception:
            pass

    data = {
        "workspace_root": ws_str,
        "pid": os.getpid(),
        "started_at": started_at if started_at is not None else time.time(),
        "project_slug": project_slug or slug,
        "workflow_path": str(workflow_path) if workflow_path else None,
    }

    metadata_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return metadata_file


def clear_orchestrator_metadata(workspace_root: str | Path) -> None:
    """Remove orchestrator metadata file."""
    slug = _slug_from_workspace(str(workspace_root))
    metadata_file = ORCHESTRATOR_DIR / slug / "metadata.json"
    if metadata_file.exists():
        metadata_file.unlink()


def list_orchestrator_projects() -> list[dict]:
    """List all known orchestrator projects from metadata files."""
    projects = []
    if not ORCHESTRATOR_DIR.exists():
        return projects

    for metadata_dir in ORCHESTRATOR_DIR.iterdir():
        if metadata_dir.is_dir():
            metadata_file = metadata_dir / "metadata.json"
            if metadata_file.exists():
                try:
                    data = json.loads(metadata_file.read_text(encoding="utf-8"))
                    projects.append(data)
                except Exception:
                    pass

    return projects


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def resolve_for_cli(
    workspace_arg: str | None,
    workflow_arg: str | None,
) -> tuple[Path | None, Path | None]:
    """Resolve workspace root and registry path for CLI commands.

    Returns:
        tuple of (workspace_root, registry_path)
    """
    root = get_workspace_root(workspace_arg=workspace_arg, workflow_path=workflow_arg)
    if root:
        registry = root / ".clawcodex_issue_registry.json"
        return root, registry

    return None, None


def print_workspace_info(workspace_root: Path | None, workflow_path: str | None = None) -> str:
    """Generate a human-readable workspace info string."""
    if workspace_root:
        parts = [f"workspace: {workspace_root}"]
    else:
        parts = ["workspace: (not found)"]

    if workflow_path:
        parts.append(f"workflow: {workflow_path}")

    return " | ".join(parts)