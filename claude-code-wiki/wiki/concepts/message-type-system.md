---
title: Message Type System
type: concept
created: 2026-04-08
updated: 2026-04-08
sources: [01_architecture_overview.md]
tags: [architecture, types, data-model]
---

# Message Type System

The typed message representation in [[claude-code]] that models every event in a conversation.

## Definition

All conversation events are represented as typed messages: user messages, assistant messages, tool use requests, tool results, system messages, and streaming events. This type system enables the [[query-loop]]'s state machine to handle each event deterministically and powers the UI rendering through React/Ink.

## Related Concepts

- [[query-loop]]
- [[context-management]]
- [[async-generator-pattern]]
