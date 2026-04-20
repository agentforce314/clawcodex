from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .env_expansion import expand_env_vars_in_string
from .types import (
    ConfigScope,
    McpHTTPServerConfig,
    McpSSEServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
    McpWebSocketServerConfig,
    ScopedMcpServerConfig,
    parse_server_config,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    path: str
    message: str
    file: str | None = None
    suggestion: str | None = None
    scope: ConfigScope | None = None
    server_name: str | None = None
    severity: str = "fatal"


@dataclass
class ParsedMcpConfig:
    config: dict[str, McpServerConfig] | None = None
    errors: list[ValidationError] = field(default_factory=list)


def _get_cwd() -> str:
    return os.getcwd()


def _get_global_config_dir() -> Path:
    env_override = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path.home() / ".claude"


def _get_managed_file_path() -> Path:
    env_override = os.environ.get("CLAUDE_MANAGED_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path("/etc/claude")


def get_enterprise_mcp_file_path() -> str:
    return str(_get_managed_file_path() / "managed-mcp.json")


def _add_scope_to_servers(
    servers: dict[str, McpServerConfig] | None,
    scope: ConfigScope,
) -> dict[str, ScopedMcpServerConfig]:
    if not servers:
        return {}
    return {
        name: ScopedMcpServerConfig(config=config, scope=scope)
        for name, config in servers.items()
    }


def _get_server_command_array(config: McpServerConfig) -> list[str] | None:
    if isinstance(config, McpStdioServerConfig):
        return [config.command, *(config.args or [])]
    return None


def _get_server_url(config: McpServerConfig) -> str | None:
    if hasattr(config, "url"):
        return getattr(config, "url")
    return None


def get_mcp_server_signature(config: McpServerConfig) -> str | None:
    cmd = _get_server_command_array(config)
    if cmd:
        return f"stdio:{json.dumps(cmd)}"
    url = _get_server_url(config)
    if url:
        return f"url:{url}"
    return None


def _expand_env_vars(
    config: McpServerConfig,
) -> tuple[McpServerConfig, list[str]]:
    missing_vars: list[str] = []

    def _expand(s: str) -> str:
        result = expand_env_vars_in_string(s)
        missing_vars.extend(result.missing_vars)
        return result.expanded

    if isinstance(config, McpStdioServerConfig):
        return (
            McpStdioServerConfig(
                command=_expand(config.command),
                args=[_expand(a) for a in (config.args or [])],
                env={k: _expand(v) for k, v in config.env.items()} if config.env else None,
                type=config.type,
            ),
            list(set(missing_vars)),
        )
    elif isinstance(config, (McpSSEServerConfig, McpHTTPServerConfig, McpWebSocketServerConfig)):
        expanded_headers = (
            {k: _expand(v) for k, v in config.headers.items()}
            if config.headers
            else None
        )
        if isinstance(config, McpSSEServerConfig):
            return (
                McpSSEServerConfig(
                    url=_expand(config.url),
                    headers=expanded_headers,
                    headers_helper=config.headers_helper,
                ),
                list(set(missing_vars)),
            )
        elif isinstance(config, McpHTTPServerConfig):
            return (
                McpHTTPServerConfig(
                    url=_expand(config.url),
                    headers=expanded_headers,
                    headers_helper=config.headers_helper,
                ),
                list(set(missing_vars)),
            )
        else:
            return (
                McpWebSocketServerConfig(
                    url=_expand(config.url),
                    headers=expanded_headers,
                    headers_helper=config.headers_helper,
                ),
                list(set(missing_vars)),
            )
    return config, []


def parse_mcp_config(
    config_object: Any,
    *,
    expand_vars: bool = True,
    scope: ConfigScope = "project",
    file_path: str | None = None,
) -> ParsedMcpConfig:
    if not isinstance(config_object, dict):
        return ParsedMcpConfig(
            errors=[
                ValidationError(
                    path="",
                    message="MCP config must be a JSON object",
                    file=file_path,
                    scope=scope,
                )
            ]
        )

    mcp_servers_raw = config_object.get("mcpServers", {})
    if not isinstance(mcp_servers_raw, dict):
        return ParsedMcpConfig(
            errors=[
                ValidationError(
                    path="mcpServers",
                    message="mcpServers must be an object",
                    file=file_path,
                    scope=scope,
                )
            ]
        )

    errors: list[ValidationError] = []
    validated: dict[str, McpServerConfig] = {}

    for name, raw_config in mcp_servers_raw.items():
        if not isinstance(raw_config, dict):
            errors.append(
                ValidationError(
                    path=f"mcpServers.{name}",
                    message="Server config must be an object",
                    file=file_path,
                    scope=scope,
                    server_name=name,
                )
            )
            continue

        parsed = parse_server_config(raw_config)
        if parsed is None:
            errors.append(
                ValidationError(
                    path=f"mcpServers.{name}",
                    message="Invalid server configuration",
                    file=file_path,
                    scope=scope,
                    server_name=name,
                )
            )
            continue

        if expand_vars:
            expanded, missing = _expand_env_vars(parsed)
            if missing:
                errors.append(
                    ValidationError(
                        path=f"mcpServers.{name}",
                        message=f"Missing environment variables: {', '.join(missing)}",
                        suggestion=f"Set the following environment variables: {', '.join(missing)}",
                        file=file_path,
                        scope=scope,
                        server_name=name,
                        severity="warning",
                    )
                )
            validated[name] = expanded
        else:
            validated[name] = parsed

    return ParsedMcpConfig(config=validated, errors=errors)


def parse_mcp_config_from_file_path(
    file_path: str,
    *,
    expand_vars: bool = True,
    scope: ConfigScope = "project",
) -> ParsedMcpConfig:
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ParsedMcpConfig(
            errors=[
                ValidationError(
                    path="",
                    message=f"MCP config file not found: {file_path}",
                    suggestion="Check that the file path is correct",
                    file=file_path,
                    scope=scope,
                    severity="fatal",
                )
            ]
        )
    except OSError as e:
        return ParsedMcpConfig(
            errors=[
                ValidationError(
                    path="",
                    message=f"Failed to read file: {e}",
                    suggestion="Check file permissions and ensure the file exists",
                    file=file_path,
                    scope=scope,
                    severity="fatal",
                )
            ]
        )

    try:
        parsed_json = json.loads(content)
    except json.JSONDecodeError:
        return ParsedMcpConfig(
            errors=[
                ValidationError(
                    path="",
                    message="MCP config is not valid JSON",
                    suggestion="Fix the JSON syntax errors in the file",
                    file=file_path,
                    scope=scope,
                    severity="fatal",
                )
            ]
        )

    return parse_mcp_config(
        parsed_json,
        expand_vars=expand_vars,
        scope=scope,
        file_path=file_path,
    )


def get_mcp_configs_by_scope(
    scope: ConfigScope,
) -> tuple[dict[str, ScopedMcpServerConfig], list[ValidationError]]:
    if scope == "project":
        all_servers: dict[str, ScopedMcpServerConfig] = {}
        all_errors: list[ValidationError] = []

        cwd = _get_cwd()
        current_dir = Path(cwd)
        dirs: list[Path] = []
        while current_dir != current_dir.parent:
            dirs.append(current_dir)
            current_dir = current_dir.parent

        for d in reversed(dirs):
            mcp_json_path = d / ".mcp.json"
            result = parse_mcp_config_from_file_path(
                str(mcp_json_path),
                expand_vars=True,
                scope="project",
            )
            if result.config is None:
                non_missing = [
                    e for e in result.errors
                    if not e.message.startswith("MCP config file not found")
                ]
                if non_missing:
                    all_errors.extend(non_missing)
                continue
            all_servers.update(_add_scope_to_servers(result.config, "project"))
            if result.errors:
                all_errors.extend(result.errors)

        return all_servers, all_errors

    elif scope == "user":
        global_config_dir = _get_global_config_dir()
        config_file = global_config_dir / "config.json"
        if not config_file.exists():
            return {}, []
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}, []
        mcp_servers = data.get("mcpServers")
        if not mcp_servers:
            return {}, []
        result = parse_mcp_config(
            {"mcpServers": mcp_servers},
            expand_vars=True,
            scope="user",
        )
        return _add_scope_to_servers(result.config, "user"), result.errors

    elif scope == "enterprise":
        enterprise_path = get_enterprise_mcp_file_path()
        result = parse_mcp_config_from_file_path(
            enterprise_path,
            expand_vars=True,
            scope="enterprise",
        )
        if result.config is None:
            non_missing = [
                e for e in result.errors
                if not e.message.startswith("MCP config file not found")
            ]
            return {}, non_missing
        return _add_scope_to_servers(result.config, "enterprise"), result.errors

    return {}, []


def get_mcp_config_by_name(name: str) -> ScopedMcpServerConfig | None:
    for scope in ("enterprise", "user", "project"):
        servers, _ = get_mcp_configs_by_scope(scope)  # type: ignore[arg-type]
        if name in servers:
            return servers[name]
    return None


def get_all_mcp_configs() -> tuple[dict[str, ScopedMcpServerConfig], list[ValidationError]]:
    enterprise_servers, enterprise_errors = get_mcp_configs_by_scope("enterprise")

    if _does_enterprise_mcp_config_exist():
        return enterprise_servers, enterprise_errors

    user_servers, user_errors = get_mcp_configs_by_scope("user")
    project_servers, project_errors = get_mcp_configs_by_scope("project")

    merged: dict[str, ScopedMcpServerConfig] = {}
    merged.update(user_servers)
    merged.update(project_servers)

    all_errors = enterprise_errors + user_errors + project_errors
    return merged, all_errors


_enterprise_exists_cache: bool | None = None


def _does_enterprise_mcp_config_exist() -> bool:
    global _enterprise_exists_cache
    if _enterprise_exists_cache is not None:
        return _enterprise_exists_cache
    result = parse_mcp_config_from_file_path(
        get_enterprise_mcp_file_path(),
        expand_vars=True,
        scope="enterprise",
    )
    _enterprise_exists_cache = result.config is not None
    return _enterprise_exists_cache


def clear_enterprise_config_cache() -> None:
    global _enterprise_exists_cache
    _enterprise_exists_cache = None


_disabled_servers: set[str] = set()


def is_mcp_server_disabled(name: str) -> bool:
    return name in _disabled_servers


def set_mcp_server_enabled(name: str, enabled: bool) -> None:
    if enabled:
        _disabled_servers.discard(name)
    else:
        _disabled_servers.add(name)


def add_mcp_config(
    name: str,
    config: dict[str, Any],
    scope: ConfigScope,
) -> None:
    if re.search(r"[^a-zA-Z0-9_\-]", name):
        raise ValueError(
            f"Invalid name {name}. Names can only contain letters, numbers, hyphens, and underscores."
        )

    if scope not in ("project", "user"):
        raise ValueError(f"Cannot add MCP server to scope: {scope}")

    parsed = parse_server_config(config)
    if parsed is None:
        raise ValueError("Invalid configuration")

    if scope == "project":
        mcp_json_path = Path(_get_cwd()) / ".mcp.json"
        existing: dict[str, Any] = {}
        if mcp_json_path.exists():
            try:
                existing = json.loads(mcp_json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}
        servers = existing.get("mcpServers", {})
        if name in servers:
            raise ValueError(f"MCP server {name} already exists in .mcp.json")
        servers[name] = config
        existing["mcpServers"] = servers

        tmp_path = str(mcp_json_path) + f".tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
            os.replace(tmp_path, str(mcp_json_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    elif scope == "user":
        config_dir = _get_global_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        data: dict[str, Any] = {}
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        servers = data.get("mcpServers", {})
        if name in servers:
            raise ValueError(f"MCP server {name} already exists in user config")
        servers[name] = config
        data["mcpServers"] = servers
        config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def remove_mcp_config(name: str, scope: ConfigScope) -> None:
    if scope == "project":
        mcp_json_path = Path(_get_cwd()) / ".mcp.json"
        if not mcp_json_path.exists():
            raise ValueError(f"No MCP server found with name: {name} in .mcp.json")
        existing = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        servers = existing.get("mcpServers", {})
        if name not in servers:
            raise ValueError(f"No MCP server found with name: {name} in .mcp.json")
        del servers[name]
        existing["mcpServers"] = servers

        tmp_path = str(mcp_json_path) + f".tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
            os.replace(tmp_path, str(mcp_json_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    elif scope == "user":
        config_file = _get_global_config_dir() / "config.json"
        if not config_file.exists():
            raise ValueError(f"No user-scoped MCP server found with name: {name}")
        data = json.loads(config_file.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
        if name not in servers:
            raise ValueError(f"No user-scoped MCP server found with name: {name}")
        del servers[name]
        data["mcpServers"] = servers
        config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    else:
        raise ValueError(f"Cannot remove MCP server from scope: {scope}")


def get_claude_desktop_config_path() -> Path:
    import platform
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Claude" / "claude_desktop_config.json" if appdata else Path.home() / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def import_from_claude_desktop() -> tuple[dict[str, ScopedMcpServerConfig], list[ValidationError]]:
    config_path = get_claude_desktop_config_path()
    if not config_path.exists():
        return {}, []

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, [ValidationError(
            path="",
            message=f"Failed to parse Claude Desktop config: {config_path}",
            file=str(config_path),
            severity="warning",
        )]

    mcp_servers = data.get("mcpServers", {})
    if not mcp_servers:
        return {}, []

    result = parse_mcp_config(
        {"mcpServers": mcp_servers},
        expand_vars=True,
        scope="user",
        file_path=str(config_path),
    )

    return _add_scope_to_servers(result.config, "user"), result.errors


def discover_vscode_mcp_servers() -> tuple[dict[str, ScopedMcpServerConfig], list[ValidationError]]:
    vscode_settings_paths: list[Path] = []

    cwd = Path(_get_cwd())
    vscode_dir = cwd / ".vscode"
    if vscode_dir.is_dir():
        settings_file = vscode_dir / "settings.json"
        if settings_file.exists():
            vscode_settings_paths.append(settings_file)

        mcp_file = vscode_dir / "mcp.json"
        if mcp_file.exists():
            vscode_settings_paths.append(mcp_file)

    all_servers: dict[str, ScopedMcpServerConfig] = {}
    all_errors: list[ValidationError] = []

    for settings_path in vscode_settings_paths:
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if settings_path.name == "mcp.json":
            servers_raw = data.get("servers", data.get("mcpServers", {}))
        else:
            servers_raw = data.get("mcp.servers", data.get("mcpServers", {}))

        if not servers_raw or not isinstance(servers_raw, dict):
            continue

        result = parse_mcp_config(
            {"mcpServers": servers_raw},
            expand_vars=True,
            scope="project",
            file_path=str(settings_path),
        )

        all_servers.update(_add_scope_to_servers(result.config, "project"))
        all_errors.extend(result.errors)

    return all_servers, all_errors


def validate_server_connectivity(config: McpServerConfig) -> list[str]:
    issues: list[str] = []

    if isinstance(config, McpStdioServerConfig):
        import shutil
        cmd = config.command
        if cmd and not shutil.which(cmd):
            issues.append(f"Command '{cmd}' not found in PATH")

    elif isinstance(config, (McpSSEServerConfig, McpHTTPServerConfig)):
        url = config.url
        if url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.hostname:
                issues.append(f"Invalid URL: {url}")

    return issues


def filter_mcp_servers_by_policy(
    configs: dict[str, ScopedMcpServerConfig],
) -> tuple[dict[str, ScopedMcpServerConfig], list[str]]:
    return dict(configs), []


def dedup_plugin_mcp_servers(
    plugin_servers: dict[str, ScopedMcpServerConfig],
    manual_servers: dict[str, ScopedMcpServerConfig],
) -> tuple[dict[str, ScopedMcpServerConfig], list[dict[str, str]]]:
    manual_sigs: dict[str, str] = {}
    for name, scoped in manual_servers.items():
        sig = get_mcp_server_signature(scoped.config)
        if sig and sig not in manual_sigs:
            manual_sigs[sig] = name

    result: dict[str, ScopedMcpServerConfig] = {}
    suppressed: list[dict[str, str]] = []
    seen_plugin_sigs: dict[str, str] = {}

    for name, scoped in plugin_servers.items():
        sig = get_mcp_server_signature(scoped.config)
        if sig is None:
            result[name] = scoped
            continue
        manual_dup = manual_sigs.get(sig)
        if manual_dup is not None:
            suppressed.append({"name": name, "duplicateOf": manual_dup})
            continue
        plugin_dup = seen_plugin_sigs.get(sig)
        if plugin_dup is not None:
            suppressed.append({"name": name, "duplicateOf": plugin_dup})
            continue
        seen_plugin_sigs[sig] = name
        result[name] = scoped

    return result, suppressed
