"""Fast-path ``clawcodex-dev pos`` CLI commands.

Usage::

    clawcodex-dev pos convert <sdk_spec> [--out <output_dir>]
        [--requirements "<requirements>"] [--name <agent_name>]

    clawcodex-dev pos convert docker_build,k8s_apply \\
        --out ./.clawcodex --requirements "CI/CD pipeline" --name cicd-agent
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


def _parse_convert_args(args: list[str]) -> tuple[str, str, str, str]:
    """Parse ``pos convert`` arguments.

    Returns (sdk_spec, output_dir, requirements, agent_name).
    """
    if not args:
        print("error: missing <sdk_spec> argument", file=sys.stderr)
        print("usage: clawcodex pos convert <sdk_spec> [--out <dir>] [--requirements <req>] [--name <name>]", file=sys.stderr)
        raise SystemExit(2)

    sdk_spec = args[0]
    output_dir = ""
    requirements = ""
    agent_name = ""

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
        else:
            print(f"error: unknown argument: {token}", file=sys.stderr)
            raise SystemExit(2)

    return sdk_spec, output_dir, requirements, agent_name


def _handle_convert(args: list[str]) -> int:
    """Handle ``pos convert`` — convert a POS spec into an Agent."""
    try:
        sdk_spec, output_dir, requirements, agent_name = _parse_convert_args(args)
    except SystemExit:
        return 2

    # Late import: only load the pos_converter when actually needed.
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
