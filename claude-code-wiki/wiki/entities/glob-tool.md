---
title: GlobTool
type: entity
created: 2026-04-08
updated: 2026-04-08
sources: [02_tool_system.md]
tags: [tool, file-search, example]
---

# GlobTool

A built-in tool in [[claude-code]] for fast file pattern matching. Used as the canonical example in [[02-tool-system]] to illustrate the [[build-tool-pattern]].

## Key Facts

- Implements the full `Tool` interface via `buildTool()`
- Uses `fdir` + `picomatch` libraries for fast glob matching
- Marked as `isConcurrencySafe: true` and `isReadOnly: true` — can run in parallel
- Returns files sorted by modification time
- Prompt includes routing guidance: "use the Agent tool instead" for open-ended searches
- Has `searchHint` for [[tool-search]] lazy loading discoverability

## Relationships

- Built using [[build-tool-pattern]]
- Part of [[tool-system]]
- Discoverable via [[tool-search]]

## Appearances Across Sources

- [[02-tool-system]] — used as walkthrough example for tool construction
