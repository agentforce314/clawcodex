---
description: Generate a conventional-commit message from staged changes.
allowed-tools: [Bash, Read]
arguments: [scope]
argument-hint: <scope>
---
# Commit Helper

Run `git diff --cached` and produce a conventional-commit message in `$scope` scope.
Skill base: ${CLAUDE_SKILL_DIR}
Session: ${CLAUDE_SESSION_ID}
