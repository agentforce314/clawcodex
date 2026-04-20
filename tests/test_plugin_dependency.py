import pytest

from src.plugins.dependency import (
    DependencyConflict,
    DependencyNode,
    DependencyResolutionResult,
    resolve_dependencies,
    topological_sort,
    version_satisfies,
)


class TestVersionSatisfies:
    def test_exact_match(self):
        assert version_satisfies("1.0.0", "1.0.0") is True

    def test_exact_no_match(self):
        assert version_satisfies("1.0.1", "1.0.0") is False

    def test_wildcard(self):
        assert version_satisfies("5.0.0", "*") is True

    def test_empty_required(self):
        assert version_satisfies("1.0.0", "") is True

    def test_gte(self):
        assert version_satisfies("2.0.0", ">=1.0.0") is True
        assert version_satisfies("1.0.0", ">=1.0.0") is True
        assert version_satisfies("0.9.0", ">=1.0.0") is False

    def test_gt(self):
        assert version_satisfies("1.0.1", ">1.0.0") is True
        assert version_satisfies("1.0.0", ">1.0.0") is False

    def test_lte(self):
        assert version_satisfies("1.0.0", "<=1.0.0") is True
        assert version_satisfies("0.9.0", "<=1.0.0") is True
        assert version_satisfies("1.0.1", "<=1.0.0") is False

    def test_lt(self):
        assert version_satisfies("0.9.0", "<1.0.0") is True
        assert version_satisfies("1.0.0", "<1.0.0") is False

    def test_caret(self):
        assert version_satisfies("1.2.3", "^1.0.0") is True
        assert version_satisfies("1.0.0", "^1.0.0") is True
        assert version_satisfies("2.0.0", "^1.0.0") is False
        assert version_satisfies("0.9.0", "^1.0.0") is False

    def test_tilde(self):
        assert version_satisfies("1.2.3", "~1.2.0") is True
        assert version_satisfies("1.2.0", "~1.2.0") is True
        assert version_satisfies("1.3.0", "~1.2.0") is False
        assert version_satisfies("1.1.0", "~1.2.0") is False

    def test_prerelease(self):
        assert version_satisfies("1.0.0-beta.1", "1.0.0") is True


class TestTopologicalSort:
    def test_no_deps(self):
        plugins = {
            "a": DependencyNode("a", "1.0.0"),
            "b": DependencyNode("b", "1.0.0"),
        }
        order = topological_sort(plugins)
        assert order is not None
        assert set(order) == {"a", "b"}

    def test_linear_deps(self):
        plugins = {
            "a": DependencyNode("a", "1.0.0"),
            "b": DependencyNode("b", "1.0.0", dependencies={"a": "^1.0.0"}),
            "c": DependencyNode("c", "1.0.0", dependencies={"b": "^1.0.0"}),
        }
        order = topological_sort(plugins)
        assert order is not None
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_diamond_deps(self):
        plugins = {
            "a": DependencyNode("a", "1.0.0"),
            "b": DependencyNode("b", "1.0.0", dependencies={"a": "^1.0.0"}),
            "c": DependencyNode("c", "1.0.0", dependencies={"a": "^1.0.0"}),
            "d": DependencyNode("d", "1.0.0", dependencies={"b": "^1.0.0", "c": "^1.0.0"}),
        }
        order = topological_sort(plugins)
        assert order is not None
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle(self):
        plugins = {
            "a": DependencyNode("a", "1.0.0", dependencies={"b": "^1.0.0"}),
            "b": DependencyNode("b", "1.0.0", dependencies={"a": "^1.0.0"}),
        }
        order = topological_sort(plugins)
        assert order is None

    def test_single(self):
        plugins = {"a": DependencyNode("a", "1.0.0")}
        order = topological_sort(plugins)
        assert order == ["a"]

    def test_empty(self):
        order = topological_sort({})
        assert order == []


class TestResolveDependencies:
    def test_clean(self):
        plugins = {
            "a": DependencyNode("a", "1.0.0"),
            "b": DependencyNode("b", "1.0.0", dependencies={"a": "^1.0.0"}),
        }
        result = resolve_dependencies(plugins)
        assert result.has_cycle is False
        assert result.conflicts == []
        assert result.missing == []
        assert len(result.order) == 2

    def test_missing_dependency(self):
        plugins = {
            "a": DependencyNode("a", "1.0.0", dependencies={"missing": "^1.0.0"}),
        }
        result = resolve_dependencies(plugins)
        assert len(result.missing) == 1
        assert result.missing[0] == ("a", "missing")

    def test_version_conflict(self):
        plugins = {
            "a": DependencyNode("a", "1.0.0"),
            "b": DependencyNode("b", "1.0.0", dependencies={"a": ">=2.0.0"}),
        }
        result = resolve_dependencies(plugins)
        assert len(result.conflicts) == 1
        assert result.conflicts[0].plugin_name == "b"
        assert result.conflicts[0].dependency_name == "a"

    def test_cycle_detection(self):
        plugins = {
            "a": DependencyNode("a", "1.0.0", dependencies={"b": "*"}),
            "b": DependencyNode("b", "1.0.0", dependencies={"c": "*"}),
            "c": DependencyNode("c", "1.0.0", dependencies={"a": "*"}),
        }
        result = resolve_dependencies(plugins)
        assert result.has_cycle is True
        assert len(result.cycle_path) > 0

    def test_no_plugins(self):
        result = resolve_dependencies({})
        assert result.order == []
        assert result.has_cycle is False
