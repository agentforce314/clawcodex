# upstream_sync/config.py
"""Pydantic configuration models for upstream-sync.

All project-specific information is externalised into a single YAML file;
this module contains zero hard-coded project knowledge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LayerConfig(BaseModel):
    """Layer configuration: the framework checks dependency direction only.

    It does **not** define what each layer contains — that is entirely up to
    the consuming project.
    """

    name: str = Field(..., description="Layer name, e.g. upstream, capabilities, features")
    paths: list[Path] = Field(..., description="Directories / files belonging to this layer")
    allowed_imports_from: list[str] = Field(
        default_factory=list,
        description="Module prefixes that this layer is allowed to import from",
    )
    forbidden_imports_from: list[str] = Field(
        default_factory=list,
        description="Module prefixes that this layer is forbidden to import from (takes precedence)",
    )


class UpstreamConfig(BaseModel):
    """Upstream repository settings."""

    remote_url: str = Field(..., description="Upstream repository URL")
    main_branch: str = "main"
    vendor_branch: str = "upstream/vendor"
    version_tag_format: str = "upstream/v{YYYY}_{MM}"
    source_subpath: str = Field(
        default="src",
        description="Sub-path within upstream repo to extract (e.g. 'src' extracts only the src/ directory)",
    )


class PatchConfig(BaseModel):
    """Patch queue configuration.

    Supports two organizational structures:

    1. **Flat** (legacy): All patches in a single directory.
       - directory: "patches"
       - series_file: "patches/series"

    2. **Per-commit subdirectory** (recommended): Each upstream commit has
       its own subdirectory with dedicated patches and series file.
       - directory: "patches/upstream"
       - series_file: "patches/upstream/{commit}/{commit}_series"
       - patch_subdir: "patches/upstream/{commit}"

    The ``{commit}`` placeholder is resolved from the upstream version_tag
    at apply time (e.g. "b125e16").
    """

    directory: Path = Path("patches")
    engine: Literal["quilt", "git-am", "custom"] = "quilt"
    custom_command: str | None = None  # used when engine == "custom"
    series_file: Path = Path("patches/series")
    metadata_dir: Path = Path("patches/metadata")
    # Optional: per-commit subdirectory pattern for patches
    # Example: "patches/upstream/{commit}" resolves to "patches/upstream/b125e16"
    patch_subdir: str | None = Field(
        default=None,
        description="Per-commit patch subdirectory pattern with {commit} placeholder. "
        "When set, patches are loaded from this subdirectory instead of directory.",
    )


class SyncConfig(BaseModel):
    """High-level sync strategy thresholds."""

    impact_threshold_auto: str = "low"      # below this → auto-resolve
    impact_threshold_agent: str = "medium"  # below this → agent-assisted
    report_formats: list[str] = ["json", "markdown"]


class ProjectConfig(BaseModel):
    """Complete per-project configuration loaded from upstream-sync.yaml."""

    project_name: str
    source_lang: str = "python"
    upstream: UpstreamConfig
    layers: list[LayerConfig]           # arbitrary number of layers
    patches: PatchConfig
    sync: SyncConfig
