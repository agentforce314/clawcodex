"""Fast-path ``clawcodex-dev pos`` CLI commands.

Usage::

    clawcodex-dev pos convert <sdk_spec> [--out <output_dir>]
        [--requirements "<requirements>"] [--name <agent_name>]
        [--strategy <strategy>] [--skills <skills_dir>]

    clawcodex-dev pos convert docker_build,k8s_apply \\
        --out ./.clawcodex --requirements "CI/CD pipeline" --name cicd-agent

    # Source directory auto-detection:
    clawcodex-dev pos convert ./src \\
        --out ./.clawcodex --strategy component --skills ./skills

Options:

    --strategy <strategy>   Grouping strategy (keyword|component|io|llm).
                            Only used when <sdk_spec> is a directory.
    --skills <skills_dir>   Output path for generated skill files.
"""

from __future__ import annotations

import sys
from pathlib import Path

from clawcodex_ext.cli.subcommand_registry import register


@register("pos")
def run_pos_command(args: list[str]) -> int:
    """Dispatch ``pos`` sub-subcommands (currently only ``convert``)."""
    if not args:
        print("usage: clawcodex pos convert <sdk_spec> [options]", file=sys.stderr)
        return 2

    command = args[0]
    rest = args[1:]

    if command == "convert":
        return _handle_convert(rest)

    print(f"Unknown pos command: {command}", file=sys.stderr)
    print("usage: clawcodex pos convert <sdk_spec> [options]", file=sys.stderr)
    return 2


def _parse_convert_args(args: list[str]) -> tuple[str, str, str, str, str, str]:
    """Parse ``pos convert`` arguments.

    Returns (sdk_spec, output_dir, requirements, agent_name, strategy, skills_dir).
    """
    if not args:
        print("error: missing <sdk_spec> argument", file=sys.stderr)
        print("usage: clawcodex pos convert <sdk_spec> [--out <dir>] [--requirements <req>] [--name <name>] [--strategy <strategy>] [--skills <skills_dir>]", file=sys.stderr)
        raise SystemExit(2)

    sdk_spec = args[0]
    output_dir = ""
    requirements = ""
    agent_name = ""
    strategy = ""
    skills_dir = ""

    i = 1
    while i < len(args):
        token = args[i]
        if token == "--out" and i + 1 < len(args):
            output_dir = args[i + 1]
            i += 2
        elif token == "--requirements" and i + 1 < len(args):
            requirements = args[i + 1]
            i += 2
        elif token == "--name" and i + 1 < len(args):
            agent_name = args[i + 1]
            i += 2
        elif token == "--strategy" and i + 1 < len(args):
            strategy = args[i + 1]
            i += 2
        elif token == "--skills" and i + 1 < len(args):
            skills_dir = args[i + 1]
            i += 2
        else:
            print(f"error: unknown argument: {token}", file=sys.stderr)
            raise SystemExit(2)

    return sdk_spec, output_dir, requirements, agent_name, strategy, skills_dir


def _handle_convert(args: list[str]) -> int:
    """Handle ``pos convert`` — convert a POS spec into an Agent."""
    try:
        sdk_spec, output_dir, requirements, agent_name, strategy, skills_dir = _parse_convert_args(args)
    except SystemExit:
        return 2

    sdk_path = Path(sdk_spec)

    # Auto-detection: if sdk_spec is an existing directory, use SourceCodeParser
    if sdk_path.is_dir():
        return _handle_convert_from_source(sdk_path, output_dir, requirements, agent_name, strategy, skills_dir)

    # Legacy path: sdk_spec is a comma-separated spec string
    from extensions.pos_converter.convert_pos_skill import convert_pos_to_agent

    result = convert_pos_to_agent(
        sdk_spec=sdk_spec,
        requirements=requirements,
        agent_name=agent_name,
    )

    if result["status"] == "error":
        print(f"error: {result.get('error', 'conversion failed')}", file=sys.stderr)
        return 2

    # Print summary to stdout
    print(f"✅ Converted POS to Agent: {result['agent_type']}")
    print(f"   Description: {result['agent_description']}")
    print(f"   Model: {result.get('model', 'default')}")
    print(f"   Skills: {len(result['skills'])}")
    for skill in result["skills"]:
        print(f"     - {skill['name']} ({', '.join(skill['tools'])})")
    print(f"   Tools: {len(result.get('tools', []))}")
    print(f"   Persistence: {result['persist_status']}")

    if result.get("warnings"):
        for w in result["warnings"]:
            print(f"   Warning: {w}", file=sys.stderr)

    # If --out is specified, write the output files to the target directory
    if output_dir:
        _write_output_files(output_dir, result, sdk_spec, requirements, agent_name)

    return 0


def _handle_convert_from_source(
    sdk_path: Path,
    output_dir: str,
    requirements: str,
    agent_name: str | None,
    strategy: str,
    skills_dir: str,
) -> int:
    """Convert a source code directory into an Agent using SourceCodeParser."""
    from extensions.pos_converter.source_parser import SourceCodeParser, SourceComponent
    from extensions.pos_converter.skill_grouper import GroupStrategy, group_source_components
    from extensions.pos_converter.agent_md_writer import AgentMarkdownWriter, AgentComponentInfo, WorkflowStage

    parser = SourceCodeParser(str(sdk_path))
    components = parser.parse()

    parsed_strategy = strategy.lower() if strategy else ""
    if parsed_strategy == "keyword":
        group_strategy = GroupStrategy.KEYWORD_MATCH
    elif parsed_strategy == "io":
        group_strategy = GroupStrategy.IO_RELATION
    elif parsed_strategy == "llm":
        group_strategy = GroupStrategy.LLM_SEMANTIC
    else:
        group_strategy = GroupStrategy.COMPONENT_GROUP

    group_result = group_source_components(components, strategy=group_strategy)

    # Build overview info for multi-component projects
    overview_info = []
    for comp in components:
        info = AgentComponentInfo(
            name=f"{comp.name}-agent",
            description=comp.description,
            capabilities=[op.name for op in comp.operations[:5]],
            input_types=list(comp.input_schema.keys()),
            output_types=list(comp.output_schema.keys()),
            invoke_pattern=f'@{comp.name}-agent {{task}}',
        )
        overview_info.append(info)

    writer = AgentMarkdownWriter()
    if output_dir:
        out_path = Path(output_dir)
        for component in components:
            agent_def = {
                "name": f"{component.name}-agent",
                "description": component.description,
                "tools": [op.name for op in component.operations],
                "skills": [],
            }
            writer.write_agent(agent_def, out_path)
        if len(overview_info) > 1:
            writer.write_overview_agent(
                name=agent_name or "clawcodex-overview",
                description=f"Overview agent for {agent_name or 'project'}",
                component_agents=overview_info,
                workflow_stages=[],
                output_dir=out_path,
            )

    # Also write skills if --skills was specified
    if skills_dir:
        skills_path = Path(skills_dir)
        skills_path.mkdir(parents=True, exist_ok=True)
        for comp in components:
            skill_file = skills_path / f"{comp.name}-skill.md"
            skill_lines = [
                "---",
                f"name: {comp.name}-skill",
                f"description: {comp.description}",
                "user-invocable: true",
                "allowed-tools:",
            ]
            for op in comp.operations:
                skill_lines.append(f"  - {op.name}")
            skill_lines.append("---")
            skill_lines.append("")
            skill_lines.append(f"# Skill: {comp.name}-skill")
            skill_lines.append("")
            skill_lines.append(comp.description)
            skill_lines.append("")
            skill_lines.append("## Included Tools")
            for op in comp.operations:
                skill_lines.append(f"- `{op.name}`")
            skill_file.write_text("\n".join(skill_lines), encoding="utf-8")
            print(f"   Skill file: {skill_file}")

    # Print summary
    print(f"✅ Converted source directory to Agent: {sdk_path}")
    print(f"   Components: {len(components)}")
    print(f"   Strategy: {group_strategy.name}")
    for i, comp in enumerate(components):
        print(f"     Component {i + 1}: {comp.name}")
        print(f"       Description: {comp.description}")
        print(f"       Operations: {len(comp.operations)}")
    if group_result.warnings:
        for w in group_result.warnings:
            print(f"   Warning: {w}", file=sys.stderr)

    return 0


def _write_output_files(
    out_dir: str,
    result: dict,
    sdk_spec: str,
    requirements: str,
    agent_name: str,
) -> None:
    """Write agent definition, skill, and workflow files to ``out_dir``."""
    base = Path(out_dir).resolve()
    agents_dir = base / "agents"
    skills_dir = base / "skills"
    workflows_dir = base / "workflows"
    agents_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    workflows_dir.mkdir(parents=True, exist_ok=True)

    name = result["agent_type"]
    skill_files = result.get("skill_files", [])

    # --- Agent definition (YAML-like markdown) ---
    agent_path = agents_dir / f"{name}.yaml"
    agent_lines = [
        f"name: {name}",
        f"description: {result['agent_description']}",
        f"model: {result.get('model', 'default')}",
        "tools:",
    ]
    for tool in result.get("tools", []):
        agent_lines.append(f"  - {tool}")
    agent_lines.append("skills:")
    for skill in result["skills"]:
        agent_lines.append(f"  - {skill['name']}")
    agent_lines.append("")
    agent_lines.append(f"# Converted from: {sdk_spec}")
    if requirements:
        agent_lines.append(f"# Requirements: {requirements}")
    agent_path.write_text("\n".join(agent_lines), encoding="utf-8")
    print(f"   Agent file: {agent_path}")

    # --- Skill SKILL.md for each skill ---
    for skill in result["skills"]:
        skill_name = skill["name"]
        skill_dir = skills_dir / f"pos-{name}-{skill_name}"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        lines = [
            "---",
            f"name: {skill_name}",
            f"description: {skill['description']}",
            "user-invocable: true",
            "allowed-tools:",
        ]
        for tool in skill["tools"]:
            lines.append(f"  - {tool}")
        lines.append("---")
        lines.append("")
        lines.append(f"# Skill: {skill_name}")
        lines.append("")
        lines.append(skill["description"])
        lines.append("")
        lines.append("## Included Tools")
        for tool in skill["tools"]:
            lines.append(f"- `{tool}`")
        skill_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"   Skill file: {skill_path}")

    # --- Workflow orchestration graph ---
    workflow_path = workflows_dir / f"pos-{name}.yaml"
    workflow_lines = [
        f"# Workflow: {name}",
        "# Auto-generated by clawcodex pos convert",
        "",
        "nodes:",
    ]
    for skill in result["skills"]:
        workflow_lines.append(f"  - id: {skill['name']}")
        workflow_lines.append(f"    agent: {name}")
        workflow_lines.append(f"    skill: {skill['name']}")
        workflow_lines.append(f"    tools: [{', '.join(skill['tools'])}]")
    workflow_path.write_text("\n".join(workflow_lines), encoding="utf-8")
    print(f"   Workflow file: {workflow_path}")

    # --- Also copy any skill files generated by the converter ---
    for src_path_str in skill_files:
        src_path = Path(src_path_str)
        if src_path.exists():
            dest = skills_dir / src_path.name
            if not dest.exists():
                dest.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")
