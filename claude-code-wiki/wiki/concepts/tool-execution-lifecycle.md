---
title: Tool Execution Lifecycle
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [02_tool_system.md]
tags: [tools, execution, lifecycle]
---

# Tool Execution Lifecycle

The 9-step process every tool call goes through in [[claude-code]].

## The 9 Steps

1. **Find** — locate tool by name in the tool pool
2. **Parse** — validate input against Zod schema
3. **Validate** — tool-specific input validation
4. **Permission** — check via [[permission-system]] (rules → mode → tool checks)
5. **PreToolUse Hook** — run user-configured [[hook-system]] hooks
6. **Execute** — call the tool's `call()` function
7. **PostToolUse Hook** — run post-execution hooks
8. **Serialize** — convert result to API-compatible format
9. **Append** — add result to conversation messages

## Related Concepts

- [[tool-system]]
- [[permission-system]]
- [[hook-system]]
- [[build-tool-pattern]]
- [[streaming-tool-executor]]
