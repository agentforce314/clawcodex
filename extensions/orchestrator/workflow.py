"""Loads workflow configuration and prompt from WORKFLOW.md.

Port of Symphony's Workflow module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .config.schema import WorkflowConfig

_WORKFLOW_FILE_NAME = "WORKFLOW.md"


class WorkflowParseError(Exception):
    """Raised when WORKFLOW.md parsing fails."""


class WorkflowLoader:
    """Load and parse a WORKFLOW.md file."""

    @staticmethod
    def load(path: str | Path) -> tuple[WorkflowConfig, str]:
        """Load workflow from disk.

        Returns (WorkflowConfig, prompt_template).
        """
        path = Path(path)
        if not path.exists():
            raise WorkflowParseError(f"Workflow file not found: {path}")

        content = path.read_text(encoding="utf-8")
        config, prompt = WorkflowLoader.parse(content)
        # Attach source path for orchestrator metadata
        config._source_path = str(path)
        return config, prompt

    @staticmethod
    def parse(content: str) -> tuple[WorkflowConfig, str]:
        """Parse WORKFLOW.md content.

        Returns (WorkflowConfig, prompt_template).
        """
        front_matter_lines, prompt_lines = _split_front_matter(content)

        front_matter = _parse_yaml(front_matter_lines)
        prompt = "\n".join(prompt_lines).strip()

        config = WorkflowConfig.from_dict(front_matter)
        return config, prompt

    @staticmethod
    def default_path() -> Path:
        env_path = os.environ.get("SYMPHONY_WORKFLOW_PATH")
        if env_path:
            return Path(env_path)
        return Path.cwd() / _WORKFLOW_FILE_NAME


def _split_front_matter(content: str) -> tuple[list[str], list[str]]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], lines

    front: list[str] = []
    prompt: list[str] = []
    in_front = True
    for line in lines[1:]:
        if in_front and line.strip() == "---":
            in_front = False
            continue
        if in_front:
            front.append(line)
        else:
            prompt.append(line)
    return front, prompt


def _parse_yaml(lines: list[str]) -> dict[str, Any]:
    text = "\n".join(lines)
    if not text.strip():
        return {}
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowParseError(f"Invalid YAML front matter: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise WorkflowParseError("Workflow front matter must be a YAML mapping")
    return parsed
