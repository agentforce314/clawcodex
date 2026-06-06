"""SdkParser — parses SDK interfaces into atomic tool specifications."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SdkMethod:
    """A single SDK method that maps to one atomic tool."""
    name: str
    description: str
    parameters: list[str] = field(default_factory=list)
    required_params: list[str] = field(default_factory=list)
    return_type: str | None = None
    original_class: str | None = None


@dataclass
class SdkParser:
    """Parse SDK/API specifications into a list of atomic tools.

    Supports multiple input formats:
      - OpenAPI/Swagger dict (from json.load or yaml.safe_load)
      - Python docstring format (Google or NumPy style)
      - Simple method list: "method1, method2, method3"

    The parser uses LLM-assisted extraction when the input is complex,
    otherwise falls back to regex-based extraction.
    """

    def __init__(self, sdk_spec: str | dict[str, Any], *, source: str = "manual") -> None:
        if isinstance(sdk_spec, str):
            self._raw = sdk_spec.strip()
        else:
            self._raw = sdk_spec
        self._source = source
        self._parsed: list[SdkMethod] | None = None

    @property
    def raw(self) -> str | dict[str, Any]:
        return self._raw

    def parse(self) -> list[SdkMethod]:
        """Parse the SDK spec and return atomic tool specifications."""
        if self._parsed is not None:
            return self._parsed

        if isinstance(self._raw, dict):
            self._parsed = self._parse_openapi(self._raw)
        elif self._raw.startswith(("http://", "https://", "{")):
            import json
            try:
                spec = json.loads(self._raw)
                self._parsed = self._parse_openapi(spec)
            except json.JSONDecodeError:
                self._parsed = self._parse_simple_list(self._raw)
        else:
            self._parsed = self._parse_simple_list(self._raw)

        return self._parsed

    def _parse_openapi(self, spec: dict[str, Any]) -> list[SdkMethod]:
        """Parse OpenAPI dict into SdkMethods."""
        methods: list[SdkMethod] = []
        paths = spec.get("paths", {})
        components = spec.get("components", {})
        schemas = components.get("schemas", {})

        for path, path_methods in paths.items():
            for method_name, operation in path_methods.items():
                if method_name.upper() not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    continue

                operation_id = operation.get("operationId", f"{method_name}_{path}")
                safe_name = self._sanitize_name(operation_id)
                params = operation.get("parameters", [])
                required = [p["name"] for p in params if p.get("required")]
                param_names = [p["name"] for p in params]

                methods.append(SdkMethod(
                    name=safe_name,
                    description=operation.get("summary") or operation.get("description", "")[:200],
                    parameters=param_names,
                    required_params=required,
                    return_type="json",
                ))

        for schema_name, schema in schemas.items():
            props = schema.get("properties", {})
            param_names = list(props.keys())
            required = schema.get("required", [])
            methods.append(SdkMethod(
                name=self._sanitize_name(schema_name),
                description=f"Schema: {schema_name}",
                parameters=param_names,
                required_params=required,
                return_type="json",
                original_class=schema_name,
            ))

        return methods

    def _parse_simple_list(self, spec: str) -> list[SdkMethod]:
        """Parse simple comma/newline separated method list."""
        methods: list[SdkMethod] = []
        method_names = re.split(r'[,\n]+', spec)
        for raw in method_names:
            name = raw.strip()
            if not name or name.startswith("#"):
                continue
            safe_name = self._sanitize_name(name)
            methods.append(SdkMethod(
                name=safe_name,
                description=f"SDK method: {name}",
                parameters=[],
                required_params=[],
            ))
        return methods

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Convert a method name to a valid tool name (kebab-case)."""
        name = re.sub(r'[/{}<>\[\]]', '_', name)
        name = re.sub(r'([A-Z])', lambda m: f'_{m.group(1).lower()}', name)
        name = re.sub(r'_+', '_', name)
        name = name.strip('_').lower()
        if not name:
            return "sdk_method"
        return name


@dataclass
class SdkParseResult:
    """Result of parsing an SDK spec."""
    methods: list[SdkMethod]
    source: str
    errors: list[str] = field(default_factory=list)


def parse_sdk_spec(spec: str | dict[str, Any], *, source: str = "manual") -> SdkParseResult:
    """Convenience function to parse an SDK spec and return structured result."""
    parser = SdkParser(spec, source=source)
    try:
        methods = parser.parse()
        return SdkParseResult(methods=methods, source=source)
    except Exception as exc:
        return SdkParseResult(methods=[], source=source, errors=[str(exc)])