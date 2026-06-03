"""Tests for POS Converter F-50: SourceCodeParser + enhanced SkillGrouper + AgentMarkdownWriter."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from extensions.pos_converter.source_parser import (
    SourceCodeParser,
    SourceComponent,
    SourceOperation,
    ParamSpec,
)
from extensions.pos_converter.skill_grouper import (
    GroupStrategy,
    SkillGrouper,
    group_source_components,
    GroupResult,
    SkillSpec,
)
from extensions.pos_converter.agent_md_writer import (
    AgentMarkdownWriter,
    AgentComponentInfo,
    WorkflowStage,
)
from extensions.pos_converter.default_agent import (
    resolve_default_agent,
    resolve_agent_by_type,
    _parse_frontmatter,
)
from extensions.pos_converter.agent_builder import AgentBuilder, AgentBuildResult
from extensions.pos_converter.templates import (
    AGENT_MD_TEMPLATE,
    SKILL_MD_TEMPLATE_JINJA,
    OVERVIEW_AGENT_TEMPLATE,
)


# =========================================================================
# SourceCodeParser tests
# =========================================================================


class TestParamSpec:
    def test_default_required(self) -> None:
        p = ParamSpec(name="x")
        assert p.name == "x"
        assert p.required is True
        assert p.type_hint is None
        assert p.default is None

    def test_optional_param(self) -> None:
        p = ParamSpec(name="y", type_hint="str", default="hello", required=False)
        assert p.name == "y"
        assert p.type_hint == "str"
        assert p.default == "hello"
        assert p.required is False


class TestSourceOperation:
    def test_minimal(self) -> None:
        op = SourceOperation(name="do_stuff", description="Does stuff")
        assert op.name == "do_stuff"
        assert op.parameters == []
        assert op.return_type is None

    def test_full(self) -> None:
        params = [ParamSpec(name="x", type_hint="int")]
        op = SourceOperation(
            name="add", description="Add numbers", parameters=params,
            return_type="int", source_code="def add(x): pass",
        )
        assert op.name == "add"
        assert len(op.parameters) == 1
        assert op.return_type == "int"


class TestSourceComponent:
    def test_minimal(self) -> None:
        comp = SourceComponent(
            name="MathOps", file_path="math/ops.py", description="Math operations",
        )
        assert comp.name == "MathOps"
        assert comp.operations == []
        assert comp.dependencies == []
        assert comp.input_schema == {}

    def test_with_ops(self) -> None:
        ops = [SourceOperation(name="add", description="Add")]
        comp = SourceComponent(
            name="MathOps",
            file_path="math.py",
            description="Math",
            operations=ops,
            dependencies=["math_utils"],
        )
        assert len(comp.operations) == 1
        assert "math_utils" in comp.dependencies


class TestSourceCodeParser:
    """Tests for SourceCodeParser with sample Python source files."""

    def test_parse_single_class(self) -> None:
        """Parse a single Python file with a class and methods."""
        source = '''
class VideoProcessor:
    """Process video files with various operations."""

    def transcode(self, input_path: str, output_format: str = "mp4") -> bool:
        """Transcode a video file to the specified format.

        Args:
            input_path: Path to the input video file.
            output_format: Target output format (default: mp4).

        Returns:
            True if successful, False otherwise.
        """
        return True

    def get_metadata(self, file_path: str) -> dict:
        """Get video file metadata."""
        return {}
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            py_file = tmp / "video_processor.py"
            py_file.write_text(source)

            parser = SourceCodeParser(tmp)
            components = parser.parse()

        assert len(components) >= 1
        # Find our component
        comp = next((c for c in components if c.name == tmp.name), components[0])
        assert len(comp.operations) >= 1

        # Check transcode method
        transcode = next((op for op in comp.operations if op.name == "transcode"), None)
        assert transcode is not None, f"transcode not found in {[op.name for op in comp.operations]}"
        assert "transcode" in transcode.description.lower()
        assert transcode.return_type == "bool"

        # Check parameters
        assert len(transcode.parameters) >= 1
        param_names = {p.name for p in transcode.parameters}
        assert "input_path" in param_names
        assert "output_format" in param_names

        # Check type hints
        input_param = next(p for p in transcode.parameters if p.name == "input_path")
        assert "str" in (input_param.type_hint or "")

    def test_parse_top_level_functions(self) -> None:
        """Parse a Python file with module-level functions."""
        source = '''
"""Utility functions for data processing."""

import json
import os


def load_config(path: str) -> dict:
    """Load configuration from a JSON file.

    Args:
        path: Path to the config file.

    Returns:
        Parsed configuration dictionary.
    """
    with open(path) as f:
        return json.load(f)


def save_result(data: dict, output_path: str) -> None:
    """Save results to a JSON file.

    Args:
        data: The data to save.
        output_path: Path to save the file.
    """
    with open(output_path, "w") as f:
        json.dump(data, f)
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            py_file = tmp / "utils.py"
            py_file.write_text(source)

            parser = SourceCodeParser(tmp)
            components = parser.parse()

        assert len(components) >= 1
        comp = next((c for c in components if c.name == tmp.name), components[0])
        op_names = {op.name for op in comp.operations}
        assert "load_config" in op_names, f"load_config not in {op_names}"
        assert "save_result" in op_names, f"save_result not in {op_names}"

    def test_exclude_patterns(self) -> None:
        """Test that exclude_patterns filters out unwanted files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # Create a normal file
            (tmp / "normal.py").write_text("def foo(): pass\n")
            # Create an excluded file
            (tmp / "test_normal.py").write_text("def test_foo(): pass\n")

            parser = SourceCodeParser(tmp, exclude_patterns=["test_*"])
            components = parser.parse()

            # Should have found the normal file component
            comp_names = [c.name for c in components]
            assert len(comp_names) >= 1

    def test_empty_directory(self) -> None:
        """Parse an empty directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            parser = SourceCodeParser(tmpdir)
            components = parser.parse()
            assert len(components) == 0

    def test_parse_file_single(self) -> None:
        """Test parse_file() for a single file."""
        source = '''
def greet(name: str) -> str:
    """Greet someone.

    Args:
        name: The person's name.

    Returns:
        A greeting string.
    """
    return f"Hello, {name}!"
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            py_file = tmp / "greeter.py"
            py_file.write_text(source)

            parser = SourceCodeParser(tmp)
            operations = parser.parse_file(py_file)

        assert len(operations) == 1
        assert operations[0].name == "greet"
        assert operations[0].return_type == "str"


class TestDocstringParsing:
    """Test docstring parsing in various formats."""

    def test_google_style(self) -> None:
        source = '''
def func(a: int, b: str) -> bool:
    """Do something.

    Args:
        a: An integer value.
        b: A string value.

    Returns:
        True on success.
    """
    return True
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "mod.py").write_text(source)
            parser = SourceCodeParser(tmp)
            comps = parser.parse()
            ops = [op for comp in comps for op in comp.operations]
            op = next((o for o in ops if o.name == "func"), None)
            assert op is not None
            assert "Do something" in op.description

    def test_numpy_style(self) -> None:
        source = '''
def func(x: float) -> float:
    """Compute the square of a number.

    Parameters
    ----------
    x : float
        The input value.

    Returns
    -------
    float
        The square of x.
    """
    return x * x
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "mod.py").write_text(source)
            parser = SourceCodeParser(tmp)
            comps = parser.parse()
            ops = [op for comp in comps for op in comp.operations]
            op = next((o for o in ops if o.name == "func"), None)
            assert op is not None
            assert "square" in op.description.lower()

    def test_rest_style(self) -> None:
        source = '''
def func(name: str) -> str:
    """Say hello.

    :param name: The person to greet.
    :type name: str
    :returns: A greeting string.
    """
    return f"Hi {name}"
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "mod.py").write_text(source)
            parser = SourceCodeParser(tmp)
            comps = parser.parse()
            ops = [op for comp in comps for op in comp.operations]
            op = next((o for o in ops if o.name == "func"), None)
            assert op is not None
            assert "hello" in op.description.lower()


# =========================================================================
# SkillGrouper tests
# =========================================================================


class TestGroupStrategy:
    def test_enum_values(self) -> None:
        assert GroupStrategy.KEYWORD_MATCH.value == "keyword_match"
        assert GroupStrategy.COMPONENT_GROUP.value == "component_group"
        assert GroupStrategy.IO_RELATION.value == "io_relation"
        assert GroupStrategy.LLM_SEMANTIC.value == "llm_semantic"

    def test_strategy_dispatch_keyword(self) -> None:
        """KEYWORD_MATCH strategy falls back to _static_group()."""
        from extensions.pos_converter.sdk_parser import SdkMethod
        grouper = SkillGrouper(
            [SdkMethod(name="docker_build", description="Build image")],
            strategy=GroupStrategy.KEYWORD_MATCH,
        )
        skills = grouper.group()
        assert len(skills) > 0

    def test_component_group_strategy(self) -> None:
        """COMPONENT_GROUP strategy groups operations by component."""
        ops = [
            SourceOperation(name="encode", description="Encode video"),
            SourceOperation(name="decode", description="Decode video"),
        ]
        comp = SourceComponent(
            name="VideoCodec",
            file_path="codec.py",
            description="Video codec operations",
            operations=ops,
        )
        result = group_source_components([comp], strategy=GroupStrategy.COMPONENT_GROUP)
        assert len(result.skills) == 1
        assert result.skills[0].name == "VideoCodec"
        assert "encode" in result.skills[0].allowed_tools
        assert "decode" in result.skills[0].allowed_tools

    def test_io_relation_strategy(self) -> None:
        """IO_RELATION strategy groups operations by parameter types."""
        ops_a = [
            SourceOperation(
                name="read_file",
                description="Read a file",
                parameters=[ParamSpec(name="path", type_hint="str")],
            ),
        ]
        ops_b = [
            SourceOperation(
                name="write_file",
                description="Write a file",
                parameters=[ParamSpec(name="path", type_hint="str")],
            ),
        ]
        comp_a = SourceComponent(name="Reader", file_path="r.py", description="Reader", operations=ops_a)
        comp_b = SourceComponent(name="Writer", file_path="w.py", description="Writer", operations=ops_b)

        result = group_source_components([comp_a, comp_b], strategy=GroupStrategy.IO_RELATION)
        # read_file and write_file share "str" param type → same group
        assert len(result.skills) >= 1
        all_tools = {t for s in result.skills for t in s.allowed_tools}
        assert "read_file" in all_tools
        assert "write_file" in all_tools


# =========================================================================
# AgentMarkdownWriter tests
# =========================================================================


class TestAgentMarkdownWriter:
    def test_write_agent(self) -> None:
        """Write a single agent markdown file and verify frontmatter."""
        writer = AgentMarkdownWriter()
        agent_def = {
            "name": "test-agent",
            "description": "A test agent",
            "model": "claude-4",
            "tools": ["tool_a", "tool_b"],
            "skills": ["skill_x"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            path = writer.write_agent(agent_def, output_dir)

            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "name: test-agent" in content
            assert "description: A test agent" in content
            assert "model: claude-4" in content
            assert "tool_a" in content
            assert "skill_x" in content

    def test_write_skills(self) -> None:
        """Write skill markdown files with parameters."""
        writer = AgentMarkdownWriter()
        skills = [
            {
                "name": "transcode-video",
                "description": "Transcode video to target format",
                "allowed_tools": ["transcode"],
                "parameters": [
                    {"name": "input_path", "type_hint": "str", "required": True, "description": "Input file"}
                ],
                "source_code": "def transcode(path): pass",
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            paths = writer.write_skills(skills, output_dir)

            assert len(paths) >= 1
            skill_path = paths[0]
            assert skill_path.exists()
            content = skill_path.read_text(encoding="utf-8")
            assert "transcode-video" in content
            assert "input_path" in content

    def test_write_overview_agent(self) -> None:
        """Write overview agent for multi-component project."""
        writer = AgentMarkdownWriter()
        agents = [
            AgentComponentInfo(
                name="video-ops-agent",
                description="Video processing operations",
                capabilities=["transcode", "slice"],
                input_types=["mp4"],
                output_types=["hls"],
                invoke_pattern="@video-ops-agent transcode input.mp4",
            ),
            AgentComponentInfo(
                name="data-process-agent",
                description="Data processing operations",
                capabilities=["filter", "aggregate"],
                input_types=["csv"],
                output_types=["json"],
                invoke_pattern="@data-process-agent process data.csv",
            ),
        ]
        stages = [
            WorkflowStage(
                name="Video Processing",
                order=1,
                description="Process video files",
                responsible_agent="video-ops-agent",
                output_type="HLS segments",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            path = writer.write_overview_agent(
                name="clawcodex-overview",
                description="Overview agent for test",
                component_agents=agents,
                workflow_stages=stages,
                output_dir=output_dir,
            )

            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "clawcodex-overview" in content
            assert "video-ops-agent" in content
            assert "data-process-agent" in content
            assert "Video Processing" in content

    def test_write_workflow(self) -> None:
        """Write WORKFLOW.md for orchestrator."""
        writer = AgentMarkdownWriter()
        agents = [
            AgentComponentInfo(name="agent-a", description="Agent A", capabilities=["a"]),
        ]
        stages = [
            WorkflowStage(name="Stage 1", order=1, responsible_agent="agent-a", output_type="result"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = writer.write_workflow("test", "Test workflow", agents, stages, Path(tmpdir))
            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "WORKFLOW.md" in content or "test" in content


# =========================================================================
# Default Agent tests
# =========================================================================


class TestResolveDefaultAgent:
    def test_no_overview_file(self) -> None:
        """No overview file → return None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_default_agent(tmpdir)
            assert result is None

    def test_with_overview_file(self) -> None:
        """Overview file exists → return parsed dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = Path(tmpdir) / ".claude" / "agents"
            agents_dir.mkdir(parents=True)
            overview = agents_dir / "clawcodex-overview.md"
            overview.write_text("""\
---
name: clawcodex-overview
description: Overview agent
model: claude-4
tools:
  - "*"
skills:
  - skill-a
---

# Overview Agent

This is the overview agent.
""")
            result = resolve_default_agent(tmpdir)
            assert result is not None
            assert result["name"] == "clawcodex-overview"
            assert result["description"] == "Overview agent"
            assert result["model"] == "claude-4"

    def test_resolve_agent_by_type(self) -> None:
        """Find an agent by its frontmatter name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = Path(tmpdir) / ".claude" / "agents"
            agents_dir.mkdir(parents=True)
            (agents_dir / "my-agent.md").write_text("""\
---
name: my-agent
description: My custom agent
---

# My Agent

Custom agent body.
""")
            result = resolve_agent_by_type(tmpdir, "my-agent")
            assert result is not None
            assert result["name"] == "my-agent"
            assert "custom agent" in result.get("description", "").lower()


class TestParseFrontmatter:
    def test_simple_frontmatter(self) -> None:
        content = """\
---
name: test
description: Test
tools:
  - tool_a
  - tool_b
---

Body text
"""
        fm, body = _parse_frontmatter(content)
        assert fm["name"] == "test"
        assert fm["description"] == "Test"
        assert "tool_a" in fm.get("tools", [])
        assert "Body text" in body

    def test_no_frontmatter(self) -> None:
        content = "Just some text\nNo frontmatter here."
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert "Just some text" in body


# =========================================================================
# AgentBuilder tests
# =========================================================================


class TestAgentBuilder:
    def test_build_agent_definition_format(self) -> None:
        """Default format='agent_definition' still works."""
        skills = [SkillSpec(name="test-skill", description="Test", allowed_tools=["tool_a"])]
        builder = AgentBuilder(
            skills=skills,
            agent_name="test-agent",
            agent_description="A test agent",
        )
        result = builder.build()
        assert result.agent.agent_type == "test-agent"
        assert "tool_a" in (result.agent.tools or [])

    def test_build_with_markdown_format(self) -> None:
        """format='markdown' writes agent markdown files."""
        skills = [SkillSpec(name="test-skill", description="Test", allowed_tools=["tool_a"])]
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = AgentBuilder(
                skills=skills,
                agent_name="test-agent",
                agent_description="A test agent",
                output_dir=tmpdir,
            )
            result = builder.build(format="markdown")
            assert result.markdown_files is not None

    def test_invalid_format_raises(self) -> None:
        """Invalid format string raises ValueError."""
        builder = AgentBuilder(
            skills=[],
            agent_name="test",
            agent_description="test",
        )
        with pytest.raises(ValueError):
            builder.build(format="invalid")


# =========================================================================
# Template string tests
# =========================================================================


class TestTemplates:
    def test_agent_md_template_has_required_fields(self) -> None:
        assert "name:" in AGENT_MD_TEMPLATE
        assert "description:" in AGENT_MD_TEMPLATE
        assert "tools:" in AGENT_MD_TEMPLATE

    def test_skill_md_template_has_required_fields(self) -> None:
        assert "allowed-tools:" in SKILL_MD_TEMPLATE_JINJA
        assert "user-invocable:" in SKILL_MD_TEMPLATE_JINJA

    def test_overview_agent_template_has_required_fields(self) -> None:
        assert "总览 Agent" in OVERVIEW_AGENT_TEMPLATE
        assert "component_agents" in OVERVIEW_AGENT_TEMPLATE
        assert "workflow_stages" in OVERVIEW_AGENT_TEMPLATE
