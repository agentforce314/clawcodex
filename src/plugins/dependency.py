from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DependencyNode:
    name: str
    version: str
    dependencies: dict[str, str] = field(default_factory=dict)


@dataclass
class DependencyConflict:
    plugin_name: str
    dependency_name: str
    required_version: str
    available_version: str | None


@dataclass
class DependencyResolutionResult:
    order: list[str] = field(default_factory=list)
    conflicts: list[DependencyConflict] = field(default_factory=list)
    missing: list[tuple[str, str]] = field(default_factory=list)
    has_cycle: bool = False
    cycle_path: list[str] = field(default_factory=list)


def resolve_dependencies(
    plugins: dict[str, DependencyNode],
) -> DependencyResolutionResult:
    result = DependencyResolutionResult()

    for name, node in plugins.items():
        for dep_name, dep_version in node.dependencies.items():
            if dep_name not in plugins:
                result.missing.append((name, dep_name))
                continue
            available = plugins[dep_name]
            if not version_satisfies(available.version, dep_version):
                result.conflicts.append(DependencyConflict(
                    plugin_name=name,
                    dependency_name=dep_name,
                    required_version=dep_version,
                    available_version=available.version,
                ))

    order = topological_sort(plugins)
    if order is None:
        result.has_cycle = True
        result.cycle_path = _find_cycle(plugins)
    else:
        result.order = order

    return result


def topological_sort(
    plugins: dict[str, DependencyNode],
) -> list[str] | None:
    in_degree: dict[str, int] = {name: 0 for name in plugins}
    adjacency: dict[str, list[str]] = {name: [] for name in plugins}

    for name, node in plugins.items():
        for dep_name in node.dependencies:
            if dep_name in plugins:
                adjacency[dep_name].append(name)
                in_degree[name] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    queue.sort()
    order: list[str] = []

    while queue:
        current = queue.pop(0)
        order.append(current)
        for neighbor in sorted(adjacency[current]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(plugins):
        return None
    return order


def _find_cycle(plugins: dict[str, DependencyNode]) -> list[str]:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in plugins}
    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        plugin = plugins[node]
        for dep_name in plugin.dependencies:
            if dep_name not in plugins:
                continue
            if color[dep_name] == GRAY:
                idx = path.index(dep_name)
                return path[idx:] + [dep_name]
            if color[dep_name] == WHITE:
                result = dfs(dep_name)
                if result:
                    return result
        path.pop()
        color[node] = BLACK
        return None

    for name in sorted(plugins):
        if color[name] == WHITE:
            cycle = dfs(name)
            if cycle:
                return cycle
    return []


def version_satisfies(available: str, required: str) -> bool:
    if required == "*" or required == "":
        return True

    if required.startswith(">="):
        return _compare_versions(available, required[2:].strip()) >= 0
    if required.startswith("<="):
        return _compare_versions(available, required[2:].strip()) <= 0
    if required.startswith(">"):
        return _compare_versions(available, required[1:].strip()) > 0
    if required.startswith("<"):
        return _compare_versions(available, required[1:].strip()) < 0
    if required.startswith("^"):
        target = required[1:].strip()
        return _caret_match(available, target)
    if required.startswith("~"):
        target = required[1:].strip()
        return _tilde_match(available, target)

    return _compare_versions(available, required) == 0


def _parse_version(v: str) -> tuple[int, ...]:
    clean = re.sub(r"-.*$", "", v.strip())
    parts = clean.split(".")
    result: list[int] = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            result.append(0)
    while len(result) < 3:
        result.append(0)
    return tuple(result)


def _compare_versions(a: str, b: str) -> int:
    va = _parse_version(a)
    vb = _parse_version(b)
    if va < vb:
        return -1
    if va > vb:
        return 1
    return 0


def _caret_match(available: str, target: str) -> bool:
    va = _parse_version(available)
    vt = _parse_version(target)
    if va[0] != vt[0]:
        return False
    return va >= vt


def _tilde_match(available: str, target: str) -> bool:
    va = _parse_version(available)
    vt = _parse_version(target)
    if va[0] != vt[0] or va[1] != vt[1]:
        return False
    return va >= vt
