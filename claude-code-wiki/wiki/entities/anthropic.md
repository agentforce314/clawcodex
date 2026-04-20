---
title: Anthropic
type: entity
created: 2026-04-08
updated: 2026-04-08
sources: [00_index.md, 01_architecture_overview.md, 07_prompt_engineering.md, 08_voice_buddy.md]
tags: [organization, ai-company]
---

# Anthropic

AI safety company that builds the Claude model family and [[claude-code]].

## Key Facts

- Creator and maintainer of [[claude-code]]
- Proposed the [[mcp-protocol]] (Model Context Protocol) as an open standard
- Latest model family: Claude 4.5/4.6 (Opus, Sonnet, Haiku)
- Internal users identified by `USER_TYPE === 'ant'` and get access to additional features (e.g., /stuck skill, detailed output guides)
- Security team maintains `CYBER_RISK_INSTRUCTION` — changes require approval
- Uses GrowthBook for feature gating and kill-switches
- Build system checks for model codename leaks via `excluded-strings.txt`

## Appearances Across Sources

- [[00-index-architecture-overview]] — credited as creator
- [[01-architecture-overview]] — internal vs external feature flags, build system
- [[07-prompt-engineering]] — security team maintains prompts, internal users get richer output guidance
- [[08-voice-buddy]] — voice mode OAuth via api.anthropic.com, buddy teaser window

## Relationships

- Builds [[claude-code]]
- Created [[mcp-protocol]]
