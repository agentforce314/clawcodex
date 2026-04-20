from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

VALID_PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
VALID_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$")

REQUIRED_MANIFEST_FIELDS = ("name",)


@dataclass
class ManifestValidationError:
    field: str
    message: str


def validate_manifest(manifest: dict[str, Any]) -> list[ManifestValidationError]:
    errors: list[ManifestValidationError] = []

    if not isinstance(manifest, dict):
        errors.append(ManifestValidationError("root", "Manifest must be a JSON object"))
        return errors

    for f in REQUIRED_MANIFEST_FIELDS:
        if f not in manifest:
            errors.append(ManifestValidationError(f, f"Required field '{f}' is missing"))

    name = manifest.get("name")
    if isinstance(name, str):
        if not VALID_PLUGIN_NAME_RE.match(name):
            errors.append(ManifestValidationError(
                "name",
                f"Plugin name '{name}' is invalid. "
                f"Must start with a letter, contain only [a-zA-Z0-9_-], max 64 chars.",
            ))
    elif name is not None:
        errors.append(ManifestValidationError("name", "Plugin name must be a string"))

    version = manifest.get("version")
    if version is not None:
        if not isinstance(version, str):
            errors.append(ManifestValidationError("version", "Version must be a string"))
        elif not VALID_VERSION_RE.match(version):
            errors.append(ManifestValidationError(
                "version", f"Version '{version}' is not valid semver (expected X.Y.Z)"
            ))

    description = manifest.get("description")
    if description is not None and not isinstance(description, str):
        errors.append(ManifestValidationError("description", "Description must be a string"))

    hooks = manifest.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        errors.append(ManifestValidationError("hooks", "Hooks must be a JSON object"))

    mcp_servers = manifest.get("mcp_servers")
    if mcp_servers is not None and not isinstance(mcp_servers, dict):
        errors.append(ManifestValidationError("mcp_servers", "mcp_servers must be a JSON object"))

    permissions = manifest.get("permissions")
    if permissions is not None:
        if not isinstance(permissions, list):
            errors.append(ManifestValidationError("permissions", "Permissions must be an array"))
        else:
            valid_perms = {"read", "write", "execute", "network", "mcp"}
            for perm in permissions:
                if perm not in valid_perms:
                    errors.append(ManifestValidationError(
                        "permissions", f"Unknown permission '{perm}'"
                    ))

    dependencies = manifest.get("dependencies")
    if dependencies is not None:
        if not isinstance(dependencies, dict):
            errors.append(ManifestValidationError(
                "dependencies", "Dependencies must be a JSON object"
            ))
        else:
            for dep_name, dep_version in dependencies.items():
                if not isinstance(dep_name, str) or not isinstance(dep_version, str):
                    errors.append(ManifestValidationError(
                        "dependencies",
                        f"Dependency '{dep_name}' must have string name and version",
                    ))

    return errors


def is_valid_manifest(manifest: dict[str, Any]) -> bool:
    return len(validate_manifest(manifest)) == 0
