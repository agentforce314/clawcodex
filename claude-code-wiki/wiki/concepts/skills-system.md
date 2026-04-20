---
title: Skills System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [06_mcp_extensions.md]
tags: [extension, skills, workflows]
---

# Skills System

Reusable workflows in [[claude-code]], organized in three layers: bundled (compiled), disk-based (Markdown), and MCP (dynamic).

## Definition

Skills are predefined prompt templates that can be invoked via `/command` syntax. Three layers:
1. **Bundled** — compiled into the CLI binary, registered via `registerBundledSkill()`
2. **Disk-based** — Markdown files with frontmatter in `~/.claude/skills/` (user), `.claude/skills/` (project), or `managed/.claude/skills/` (enterprise)
3. **MCP** — prompt templates from MCP servers, dynamically discovered

## Key Built-in Skills

| Skill | Function |
|-------|----------|
| /simplify | Three-agent parallel code review (reuse, quality, efficiency) |
| /skillify | 4-round interview to convert session → reusable skill |
| /remember | Memory hierarchy management (classify + promote + deduplicate) |
| /stuck | Internal diagnostic tool (Anthropic-only) |

## Key Design Decisions

- Frontmatter `context: fork` runs skill in isolated sub-agent
- `allowed-tools` supports parameterized permissions like `Bash(gh:*)`
- Token estimation only counts frontmatter, not full content (optimization)
- Skill deduplication uses `realpath` (not inode) for filesystem compatibility

## Related Concepts

- [[tool-system]]
- [[mcp-protocol]]
- [[plugins-system]]
- [[multi-agent-system]]
