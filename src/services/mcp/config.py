from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

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


def unwrap_ccr_proxy_url(url: str) -> str:
    """Extract the original vendor URL from a CCR-proxied URL.

    Phase 7 WI-7.5 (gap #23). Mirrors typescript/src/services/mcp/
    config.ts:unwrapCcrProxyUrl. CCR-proxied URLs encode the original
    vendor URL in an ``mcp_url=...`` query parameter (URL-encoded). We
    extract that param and return its value so dedup correctness holds:
    a server reachable directly at ``https://vendor.example/mcp`` and
    via ``https://ccr.proxy/mcp?mcp_url=...`` collapses to one entry.
    Returns the input unchanged if the URL is not CCR-proxied, if the
    wrapped value isn't a parseable URL, or if multiple ``mcp_url`` params
    are present (refuse to guess).
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        params = parse_qs(parsed.query, keep_blank_values=False)
        wrapped = params.get("mcp_url")
        if not wrapped:
            return url
        if len(wrapped) > 1:
            logger.warning(
                "MCP CCR-proxy unwrap: %d 'mcp_url' params present; refusing "
                "to guess. Keeping original URL: %s",
                len(wrapped), url,
            )
            return url
        candidate = unquote(wrapped[0])
        # Validate the unwrapped value is itself a URL with scheme + netloc;
        # otherwise treat it as garbage and keep the original.
        unwrapped_parsed = urlparse(candidate)
        if not unwrapped_parsed.scheme or not unwrapped_parsed.netloc:
            return url
        return candidate
    except (ValueError, TypeError):
        return url


def _normalize_url_for_signature(url: str) -> str:
    """Lowercase host + strip a single trailing ``/`` from path so that
    case-different / trailing-slash variants of the same URL produce the
    same signature.

    Per RFC 3986 §6.2.2, hostname comparison is case-insensitive; trailing
    slash is path-equivalent for many MCP servers. Doesn't touch query or
    fragment — those are already content-significant.
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return url
        host_lower = parsed.netloc.lower()
        path = parsed.path
        if path.endswith("/") and len(path) > 1:
            path = path[:-1]
        rebuilt = parsed._replace(netloc=host_lower, path=path)
        return rebuilt.geturl()
    except (ValueError, TypeError):
        return url


def get_mcp_server_signature(config: McpServerConfig) -> str | None:
    cmd = _get_server_command_array(config)
    if cmd:
        return f"stdio:{json.dumps(cmd)}"
    url = _get_server_url(config)
    if url:
        return f"url:{_normalize_url_for_signature(unwrap_ccr_proxy_url(url))}"
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
    """Load MCP server configs for a single scope.

    Scope semantics (Phase 7 WI-7.1, gap #9):
      - ``local``: ``.mcp.json`` in the current working directory only.
        Per the chapter, this scope requires user approval — currently
        loaded unconditionally; future work can gate behind a workspace-
        trust check (out of scope here).
      - ``project``: ``.mcp.json`` files in **parent** directories of the
        cwd, walked toward the filesystem root. Closest-parent-wins.
        The cwd itself is intentionally excluded so it can be tagged
        ``local`` with stricter approval semantics.
      - ``user``: ``~/.claude/config.json``'s ``mcpServers`` field.
      - ``enterprise``: ``/etc/claude/managed-mcp.json`` (or
        ``$CLAUDE_MANAGED_CONFIG_DIR/managed-mcp.json``).
      - ``managed`` / ``dynamic`` / ``claudeai``: see ``get_managed_mcp_configs``,
        ``get_dynamic_mcp_configs``, and the future Phase 7.3 claudeai loader.
    """
    if scope == "local":
        cwd = Path(_get_cwd())
        local_path = cwd / ".mcp.json"
        if not local_path.exists():
            return {}, []
        result = parse_mcp_config_from_file_path(
            str(local_path),
            expand_vars=True,
            scope="local",
        )
        if result.config is None:
            non_missing = [
                e for e in result.errors
                if not e.message.startswith("MCP config file not found")
            ]
            return {}, non_missing
        return _add_scope_to_servers(result.config, "local"), result.errors

    if scope == "project":
        all_servers: dict[str, ScopedMcpServerConfig] = {}
        all_errors: list[ValidationError] = []

        cwd = _get_cwd()
        current_dir = Path(cwd)
        # Skip cwd itself — that's tagged `local`. Walk parent dirs only.
        # Bound the walk at $HOME to match TS canonical behavior — without
        # this, a malicious ``/.mcp.json`` or ``/Users/.mcp.json`` would
        # silently inject configs into every user's project scope. Hard
        # cap at 16 levels as belt-and-braces (deep monorepos rarely
        # exceed 8 levels of parents).
        try:
            home_path = Path.home().resolve()
        except (RuntimeError, OSError):
            home_path = None
        parent_dirs: list[Path] = []
        cursor = current_dir.parent
        for _ in range(16):
            if cursor == cursor.parent:
                break
            parent_dirs.append(cursor)
            try:
                if home_path is not None and cursor.resolve() == home_path:
                    # Stop at $HOME boundary; ascend no further.
                    break
            except (OSError, RuntimeError):
                # If resolve() fails (broken symlink, permission denied),
                # stop the walk here rather than continuing into unknown
                # territory.
                break
            cursor = cursor.parent

        for d in reversed(parent_dirs):
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
    # Order: highest-trust → lowest-trust. Enterprise managed wins over
    # user, which wins over project, which wins over local.
    for scope in ("enterprise", "user", "project", "local"):
        servers, _ = get_mcp_configs_by_scope(scope)  # type: ignore[arg-type]
        if name in servers:
            return servers[name]
    # Also check managed (plugin) and dynamic (SDK-injected) servers.
    managed = get_managed_mcp_configs()
    if name in managed:
        return managed[name]
    dynamic = get_dynamic_mcp_configs()
    if name in dynamic:
        return dynamic[name]
    return None


# Module-level registry for SDK-injected ("dynamic") MCP servers. The SDK
# can register / unregister servers at runtime via add_dynamic_mcp_config /
# remove_dynamic_mcp_config; they appear in get_all_mcp_configs() under the
# ``dynamic`` scope (when no enterprise managed-mcp.json exists; with an
# enterprise file present, dynamic servers are dropped — match TS).
_dynamic_mcp_configs: dict[str, ScopedMcpServerConfig] = {}
_dynamic_mcp_lock = threading.Lock()


def add_dynamic_mcp_config(name: str, config: McpServerConfig) -> None:
    """Register an SDK-injected MCP server. Phase 7 WI-7.4 (gap #9 subset).

    Mirrors TS' ``dynamic`` scope semantics: the SDK passes config objects
    directly without going through the on-disk config files. Useful for
    embedded use cases where the host process owns server lifecycle.

    Thread-safe via ``_dynamic_mcp_lock`` so concurrent SDK callers cannot
    race the dict mutation. ``get_dynamic_mcp_configs`` snapshots under the
    same lock so a caller cannot observe a partially-mutated registry.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("Dynamic MCP server name must be a non-empty string")
    with _dynamic_mcp_lock:
        _dynamic_mcp_configs[name] = ScopedMcpServerConfig(
            config=config, scope="dynamic"
        )


def remove_dynamic_mcp_config(name: str) -> bool:
    with _dynamic_mcp_lock:
        return _dynamic_mcp_configs.pop(name, None) is not None


def get_dynamic_mcp_configs() -> dict[str, ScopedMcpServerConfig]:
    """Return a snapshot of the dynamic-scope server registry.

    Returns a fresh copy so callers cannot mutate the underlying registry
    by editing the returned dict. The copy is taken under the lock so the
    snapshot is consistent under concurrent add/remove.
    """
    with _dynamic_mcp_lock:
        return dict(_dynamic_mcp_configs)


def get_managed_mcp_configs() -> dict[str, ScopedMcpServerConfig]:
    """Return plugin-provided MCP server configs (``managed`` scope).

    Phase 7 WI-7.4 (gap #9 subset). The integration point with the plugin
    layer at ``src/plugins/mcp_integration.py`` is the
    ``McpPluginWrapper`` registry — but as of today, ``McpPluginWrapper``
    does not carry an ``McpServerConfig`` (only a ``server_name``,
    ``plugin``, ``tools``, ``connected``). There is therefore nothing to
    return: the wrapper holds tool metadata, not the launch config.

    Returning ``{}`` here keeps the merge surface stable: callers (the
    ``get_all_mcp_configs`` aggregator and ``get_mcp_config_by_name``)
    can ask for managed configs without crashing, and the moment the
    plugin layer is extended to carry an ``McpServerConfig`` per wrapper,
    this loader can read it via ``wrapper.config`` (or whatever the
    extended schema names it) and propagate.

    TODO(Phase 7 follow-up): extend ``McpPluginWrapper`` with a
    ``server_config: McpServerConfig`` field, set at registration time,
    and surface it here. Until that lands, plugin-provided MCP servers
    cannot participate in the per-name lookup or the merge.
    """
    return {}


def get_all_mcp_configs() -> tuple[dict[str, ScopedMcpServerConfig], list[ValidationError]]:
    """Aggregate MCP configs from every scope, then apply the policy filter.

    Order of operations:
      1. If an enterprise managed-mcp.json exists, that wins outright —
         return it (still passed through ``filter_mcp_servers_by_policy``
         so a ``disable_all_mcp`` policy still applies).
      2. Otherwise, merge managed → user → project → local → dynamic
         (last writer wins; dynamic ranks highest because SDK injection
         is explicit runtime intent).
      3. Apply ``filter_mcp_servers_by_policy`` so ``disable_all_mcp`` /
         ``allow_managed_only_mcp`` settings actually have a consumer
         (Phase 7 WI-7.2 / gap #10). Notices are appended as warning-
         severity ``ValidationError``\\s.
    """
    enterprise_servers, enterprise_errors = get_mcp_configs_by_scope("enterprise")

    if _does_enterprise_mcp_config_exist():
        filtered, notices = filter_mcp_servers_by_policy(enterprise_servers)
        return filtered, enterprise_errors + _notices_to_validation_errors(notices)

    # Lower-trust scopes merge in increasing-precedence order so closer-to-cwd
    # configs (local) override further-out ones (project / user).
    user_servers, user_errors = get_mcp_configs_by_scope("user")
    project_servers, project_errors = get_mcp_configs_by_scope("project")
    local_servers, local_errors = get_mcp_configs_by_scope("local")
    managed_servers = get_managed_mcp_configs()
    dynamic_servers = get_dynamic_mcp_configs()
    # Phase 7 WI-7.3: pick up any Claude.ai connectors that the agent's
    # async boot path warmed. This is a snapshot read — if the prefetch
    # hasn't completed yet, claudeai servers simply don't show up this
    # tick. The next merge cycle picks them up. Dedup against manual
    # entries (WI-7.5) happens below.
    from .claudeai import get_cached_claudeai_mcp_configs  # local import to avoid cycle
    claudeai_servers = get_cached_claudeai_mcp_configs()

    merged: dict[str, ScopedMcpServerConfig] = {}
    # Precedence: managed → claudeai → user → project → local → dynamic
    # (last writer wins; manual configs at any scope override the
    # claudeai web-configured ones since the operator who wrote the
    # local file expressed explicit intent).
    merged.update(managed_servers)
    merged.update(claudeai_servers)
    merged.update(user_servers)
    merged.update(project_servers)
    merged.update(local_servers)
    merged.update(dynamic_servers)

    # Apply the manual/claudeai dedup (gap #24): when both a manual
    # and claudeai entry resolve to the same URL signature, drop the
    # claudeai one so the operator's explicit config takes precedence.
    dedup_notice_strings: list[str] = []
    if claudeai_servers:
        manual_keys = (set(user_servers) | set(project_servers) | set(local_servers))
        manual_only_servers = {
            k: merged[k] for k in manual_keys if k in merged and merged[k].scope != "claudeai"
        }
        claudeai_only = {k: v for k, v in merged.items() if v.scope == "claudeai"}
        kept_claudeai, suppressed = dedup_claudeai_mcp_servers(
            claudeai_only,
            {k for k in manual_keys if not is_mcp_server_disabled(k)},
            manual_only_servers,
        )
        # Reassemble: non-claudeai entries + deduped claudeai
        merged = {
            **{k: v for k, v in merged.items() if v.scope != "claudeai"},
            **kept_claudeai,
        }
        for rec in suppressed:
            dedup_notice_strings.append(
                f"Claude.ai connector {rec.get('name')!r} suppressed; "
                f"duplicate of manual server {rec.get('duplicateOf')!r}."
            )

    filtered, notices = filter_mcp_servers_by_policy(merged)

    all_errors = (
        enterprise_errors + user_errors + project_errors + local_errors
        + _notices_to_validation_errors(notices)
        + _notices_to_validation_errors(dedup_notice_strings)
    )
    return filtered, all_errors


def _notices_to_validation_errors(notices: list[str]) -> list[ValidationError]:
    """Wrap policy notices as warning-severity ValidationErrors so they
    propagate through the existing ``(configs, errors)`` return shape."""
    return [
        ValidationError(
            path="",
            message=msg,
            file=None,
            severity="warning",
            scope=None,
            server_name=None,
        )
        for msg in notices
    ]


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
    """Apply enterprise policy gates to a merged MCP server config map.

    Phase 7 WI-7.2 (gap #10). Mirrors TS' policy cascade: settings can
    enforce ``disable_all_mcp`` (reject everything) or
    ``allow_managed_only_mcp`` (keep only ``enterprise`` and ``managed``
    scopes). Both flags read from the active settings; policy origin
    can be either user / project / enterprise depending on which level
    sets the flag, but enterprise wins when conflicts arise (handled by
    the standard settings cascade upstream).

    Returns ``(filtered_configs, notices)``. Notices is a list of
    user-facing strings describing why servers were dropped.
    """
    notices: list[str] = []
    settings_obj = _safe_load_settings()
    if settings_obj is None:
        return dict(configs), notices

    # ``disable_all_mcp`` / ``allow_managed_only_mcp`` are not declared
    # on ``SettingsSchema``, so ``SettingsSchema.from_dict`` routes them
    # to the ``extra`` dict. Check both surfaces — the dataclass field
    # (in case the schema is extended later) and the extras bag — and
    # accept both snake_case and camelCase spellings since settings JSON
    # files have used both in the wild.
    extra = getattr(settings_obj, "extra", None) or {}

    def _policy_flag(snake_name: str, camel_name: str) -> bool:
        if getattr(settings_obj, snake_name, False):
            return True
        return bool(extra.get(snake_name) or extra.get(camel_name))

    if _policy_flag("disable_all_mcp", "disableAllMcp"):
        if configs:
            notices.append(
                "All MCP servers disabled by policy (settings.disable_all_mcp)."
            )
        return {}, notices

    if _policy_flag("allow_managed_only_mcp", "allowManagedOnlyMcp"):
        kept = {
            name: cfg for name, cfg in configs.items()
            if cfg.scope in ("enterprise", "managed")
        }
        dropped = set(configs) - set(kept)
        if dropped:
            notices.append(
                "Only enterprise/managed MCP servers allowed by policy "
                f"(settings.allow_managed_only_mcp); dropped: {sorted(dropped)}"
            )
        return kept, notices

    return dict(configs), notices


def _safe_load_settings() -> Any:
    """Load settings without crashing on bootstrap-time use cases.

    The settings layer may not be fully initialized when called early
    (e.g., during plugin discovery before the settings file is parsed),
    so we degrade to ``None`` rather than raising — but loudly log the
    underlying exception. Silent fail-open on a security gate is the
    wrong default; if the policy filter ever observes ``None``, we want
    to know why.
    """
    try:
        from src.settings import get_settings  # local import to avoid cycles

        return get_settings()
    except Exception as exc:
        logger.warning(
            "MCP policy: settings load failed (%s: %s); the enterprise "
            "policy gate (disable_all_mcp / allow_managed_only_mcp) will "
            "be inactive for this config-load. Investigate the settings "
            "layer initialization order if this is unexpected.",
            type(exc).__name__,
            exc,
        )
        return None


def dedup_claudeai_mcp_servers(
    claudeai_servers: dict[str, ScopedMcpServerConfig],
    manual_enabled: set[str],
    manual_servers: dict[str, ScopedMcpServerConfig],
) -> tuple[dict[str, ScopedMcpServerConfig], list[dict[str, str]]]:
    """De-duplicate Claude.ai connector servers against manual entries.

    Phase 7 WI-7.5 (gap #24). Mirrors TS ``dedupClaudeAiMcpServers``:
    when a manual server (``user`` / ``project`` / ``local`` scope) is
    enabled and points at the same vendor URL as a Claude.ai connector,
    the manual entry wins and the Claude.ai entry is suppressed.

    Args:
      claudeai_servers: Servers from the ``claudeai`` scope (typically
        prefixed ``claude.ai `` per TS convention).
      manual_enabled: Names of manual servers currently enabled (from
        ``is_mcp_server_disabled`` complement).
      manual_servers: All manual configs (``user`` / ``project`` /
        ``local``), used to compute signatures.

    Returns ``(kept_claudeai_servers, suppressed_records)``.
    """
    manual_sigs: dict[str, str] = {}
    for name, scoped in manual_servers.items():
        if name not in manual_enabled:
            continue
        sig = get_mcp_server_signature(scoped.config)
        if sig and sig not in manual_sigs:
            manual_sigs[sig] = name

    kept: dict[str, ScopedMcpServerConfig] = {}
    suppressed: list[dict[str, str]] = []
    for name, scoped in claudeai_servers.items():
        sig = get_mcp_server_signature(scoped.config)
        if sig is None:
            kept[name] = scoped
            continue
        manual_dup = manual_sigs.get(sig)
        if manual_dup is not None:
            suppressed.append({"name": name, "duplicateOf": manual_dup})
            continue
        kept[name] = scoped

    return kept, suppressed


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
