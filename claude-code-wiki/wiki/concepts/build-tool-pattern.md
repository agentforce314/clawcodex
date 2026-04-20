---
title: buildTool Pattern
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [02_tool_system.md]
tags: [design-pattern, tools, factory]
---

# buildTool Pattern

The factory function used to construct all tools in [[claude-code]]. Provides fail-closed defaults and a consistent interface.

## Definition

`buildTool()` takes a partial tool specification and fills in safe defaults for unspecified fields. Key defaults: `isConcurrencySafe: false`, `isReadOnly: false`, `userFacingName()` returns `name`. Developers only need to implement the fields that differ from defaults.

## How It Appears Across Sources

- [[02-tool-system]]: introduced with [[glob-tool]] as a walkthrough example
- [[01-architecture-overview]]: mentioned as a key design pattern

## Key Design Decisions

- **Fail-closed**: missing fields default to the most restrictive option
- **Separation of concerns**: identity, schema, execution, permissions, prompt, UI, and serialization are distinct groups
- **Zod schema doubles as prompt**: parameter descriptions in Zod `.describe()` become part of the LLM-facing tool definition

## Related Concepts

- [[tool-system]]
- [[tool-execution-lifecycle]]
- [[defense-in-depth]]
