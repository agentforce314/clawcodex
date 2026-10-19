"""Nano system prompt — pi's shape, clawcodex's plumbing.

Builds the entire nano system prompt as a block list compatible with
``query()``'s cache machinery (same ``_cache_scope`` / ``cache_control``
conventions as ``build_full_system_prompt_blocks`` and the coordinator
branch of ``build_effective_system_prompt``).

Structure, mirroring pi (`packages/coding-agent/src/core/system-prompt.ts`,
162 lines for the whole builder):

  identity line
  Available tools:   (one line per tool)
  Guidelines:        (a handful of bullets)
  [non-interactive note]
  [project context — CLAWCODEX.md, falling back to AGENTS.md / CLAUDE.md]
  [skills listing — name/description/location; the Read tool is the loader]
  Current working directory + OS + date

Deliberately absent (the point of nano — see
my-docs/clawcodex-nano/pi-vs-clawcodex.md §3): the ~3,000-token base
sections, the ~3,300-token auto-memory doctrine, MCP sections, the git
status snapshot, and every per-turn reminder. Everything here is
session-stable, so the whole prompt sits in one cached prefix.
"""

from __future__ import annotations

import os

from pathlib import Path
from typing import Any

_IDENTITY = (
    "You are an expert software engineering agent operating inside "
    "clawcodex (nano mode). You help the user by reading files, executing "
    "commands, editing code, and writing new files."
)

# One line per tool, pi-style. Keyed by tool name so the listing tracks the
# registry: a tool filtered out via --disallowed-tools drops off the prompt.
NANO_TOOL_SNIPPETS: dict[str, str] = {
    "Read": (
        "read file contents (text, images, PDFs, notebooks); "
        "offset/limit page through large files"
    ),
    "Bash": "execute shell commands in the working directory",
    "Edit": "make precise file edits by exact text replacement",
    "Write": "create new files or fully overwrite existing ones",
    "Grep": "search file contents with regex (respects .gitignore)",
    "Glob": "find files by glob pattern",
    # Conditional tools — listed only when configured and registered
    # (see nano/registry.py).
    "vision_analyze": (
        "ask a configured vision model a question about a local image "
        "(screenshots, plots, photos, rendered output)"
    ),
    "WebSearch": "search the web (titles, URLs, snippets)",
}

# The three non-tool guidelines at the end are distilled from clawcodex's
# full "Doing tasks" / "Executing actions with care" sections, restored
# after the first TB 2.1 nano run showed exactly the failures they exist
# to prevent (my-docs/clawcodex-nano/: a recovery task lost because the
# first exploratory open of a database mutated it; tasks failed at the
# wall clock with no artifact in place; confident summaries the verifier
# rejected). Each is a universal engineering practice, kept to one line.
_GUIDELINES = (
    "- Use Read to examine files instead of cat/sed/head, and Grep/Glob "
    "instead of shell grep/find",
    "- Read a file before editing it; each old_string must match the file "
    "exactly, including whitespace",
    "- When changing multiple places in one file, use ONE Edit call with "
    "multiple entries in edits[]; keep each old_string as small as "
    "possible while still unique, and never overlap edits",
    "- Before probing or modifying state you might need intact later — "
    "databases, files a task asks you to repair or recover, anything "
    "one-of-a-kind — copy it somewhere safe first; some tools mutate "
    "files merely by opening them",
    "- Get a minimal working version of the requested deliverable in "
    "place early, then iterate to improve it",
    "- Run long builds, training runs, or downloads as ONE blocking bash "
    "command (tee output to a log) — never launch with nohup and poll in "
    "a loop",
    "- Before finishing, re-read the task and list every explicit "
    "requirement and constraint (exact formats, tolerances, numeric "
    "bounds, required sources or methods), then test your artifact "
    "against each one; plausible output is not verified output",
    "- Be concise in your responses",
    "- Show file paths clearly when working with files",
)

_NON_INTERACTIVE_NOTE = (
    "You are running non-interactively: no user is available to answer "
    "questions mid-task. Complete the task fully, verify your work with "
    "the narrowest sufficient check, then stop."
)

# Fallback context files when no CLAWCODEX.md exists, in pi's precedence
# order (resource-loader.ts:71). Root-level only — nano keeps discovery
# cheap; the CLAWCODEX.md path keeps its existing 3-level walk.
_FALLBACK_CONTEXT_FILES = ("AGENTS.override.md", "AGENTS.md", "CLAUDE.md")
_FALLBACK_CONTEXT_MAX_CHARS = 40_000


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _skills_section(cwd: str) -> str:
    """pi's progressive-disclosure skills listing (skills.ts:335-361).

    Name/description/location triples only; the Read tool is the loader.
    Best-effort: any failure yields no section, never a broken prompt.
    """
    try:
        from src.command_system import get_skill_tool_commands

        skills = get_skill_tool_commands(cwd)
    except Exception:
        return ""
    if not skills:
        return ""

    # pi's contract: a listed skill must carry a location the Read tool can
    # load — nano has no Skill tool, so a skill without a file path would be
    # advertised but unusable (and would only bloat the prompt). Drop them.
    entries: list[tuple[str, str, str]] = []
    for skill in skills:
        # ``paths`` is the conditional-activation glob list, not a file
        # location; the loadable file is ``<skill_root>/SKILL.md``.
        root = getattr(skill, "skill_root", None)
        if not root:
            continue
        entries.append((
            _xml_escape(str(getattr(skill, "name", ""))),
            _xml_escape(str(getattr(skill, "description", "") or "")),
            _xml_escape(os.path.join(str(root), "SKILL.md")),
        ))
    if not entries:
        return ""

    lines = [
        "The following skills provide specialized instructions for "
        "specific tasks.",
        "Use the Read tool to load a skill's file when the task matches "
        "its description.",
        "",
        "<available_skills>",
    ]
    for name, desc, location in entries:
        lines.append("  <skill>")
        lines.append(f"    <name>{name}</name>")
        lines.append(f"    <description>{desc}</description>")
        lines.append(f"    <location>{location}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def _load_fallback_context(workspace_root: str | Path) -> str:
    """AGENTS.md / CLAUDE.md fallback when no CLAWCODEX.md content exists.

    pi reads AGENTS.override.md > AGENTS.md > CLAUDE.md per directory;
    clawcodex reads only CLAWCODEX.md (clawcodex_md.py:14). Terminal-bench
    tasks and most external repos carry AGENTS.md/CLAUDE.md, so nano honors
    them — first match at the workspace root wins.
    """
    root = Path(workspace_root)
    for name in _FALLBACK_CONTEXT_FILES:
        candidate = root / name
        try:
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = text.strip()
        if not text:
            continue
        if len(text) > _FALLBACK_CONTEXT_MAX_CHARS:
            text = (
                text[:_FALLBACK_CONTEXT_MAX_CHARS]
                + "\n... [truncated: file exceeds nano context budget]"
            )
        return (
            f'<project_instructions path="{candidate}">\n{text}\n'
            "</project_instructions>"
        )
    return ""


def _nano_env_info(cwd: str) -> str:
    """Three cache-stable lines: cwd, OS, date-only stamp."""
    import platform

    from src.context_system.prompt_assembly import _get_session_start_date_iso

    return "\n".join((
        f"Current working directory: {cwd}",
        f"OS: {platform.system()} {platform.release()}",
        f"Date: {_get_session_start_date_iso()}",
    ))


def build_nano_prompt_text(
    *,
    cwd: str,
    tool_names: tuple[str, ...] | list[str] | None = None,
    non_interactive: bool = False,
    style_prompt: str = "",
) -> str:
    """The nano base prompt as a single string (no context files)."""
    names = list(tool_names) if tool_names else list(NANO_TOOL_SNIPPETS)
    tool_lines = [
        f"- {name}: {NANO_TOOL_SNIPPETS[name]}"
        for name in names
        if name in NANO_TOOL_SNIPPETS
    ]

    parts = [
        _IDENTITY,
        "",
        "Available tools:",
        *tool_lines,
        "",
        "Guidelines:",
        *_GUIDELINES,
    ]
    if non_interactive:
        parts += ["", _NON_INTERACTIVE_NOTE]
    if style_prompt.strip():
        parts += ["", style_prompt.strip()]

    skills = _skills_section(cwd)
    if skills:
        parts += ["", skills]

    # pi-lean env block: cwd + OS + memoized date-only stamp (cache-stable —
    # same `_get_session_start_date_iso` the default prompt uses). No
    # data-dir line, no shell/user lines: nano's surface has no
    # session-resume affordance and every line here is paid on every turn.
    parts += ["", _nano_env_info(cwd)]
    return "\n".join(parts)


def build_nano_prompt_blocks(
    *,
    cwd: str,
    workspace_root: str | Path | None = None,
    tool_names: tuple[str, ...] | list[str] | None = None,
    non_interactive: bool = False,
    style_prompt: str = "",
    query_source: str = "main",
) -> list[dict[str, Any]]:
    """Full nano system prompt as cache-scoped blocks.

    Block 1: the base prompt (SESSION scope). Block 2 (when present):
    project instructions — CLAWCODEX.md via the existing extractor, or the
    AGENTS.md/CLAUDE.md fallback (SESSION scope). The cache marker rides
    the last block so the entire prompt is one cached prefix; there is no
    REQUEST-scope (volatile) block at all — nano sends no git snapshot.
    """
    from src.context_system.system_prompt_cache import CacheScope
    from src.state.cache_state import should_1h_cache_ttl

    root = workspace_root or cwd

    blocks: list[dict[str, Any]] = [{
        "type": "text",
        "text": build_nano_prompt_text(
            cwd=cwd,
            tool_names=tool_names,
            non_interactive=non_interactive,
            style_prompt=style_prompt,
        ),
        "_cache_scope": CacheScope.SESSION.value,
    }]

    instructions = ""
    try:
        from src.context_system import build_context_prompt_parts

        _snapshot, instructions = build_context_prompt_parts(root, cwd=cwd)
    except Exception:
        instructions = ""
    if not instructions.strip():
        instructions = _load_fallback_context(root)
    if instructions.strip():
        blocks.append({
            "type": "text",
            "text": instructions.strip(),
            "_cache_scope": CacheScope.SESSION.value,
        })

    blocks[-1]["cache_control"] = {
        "type": "ephemeral",
        "ttl": "1h" if should_1h_cache_ttl(query_source) else "5m",
    }
    return blocks
