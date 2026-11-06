# HOL Guard pre-tool adapter for ClawCodex (proposed)

This document describes the **unmerged, opt-in** adapter in [upstream PR #939](https://github.com/agentforce314/clawcodex/pull/939). It is a HOL-authored integration proposal, not an upstream-supported or independently approved release.

## What it does

The adapter handles ClawCodex `PreToolUse` events for the built-in `Bash` tool. It passes the exact pending command to an installed local `hol-guard` executable, rejects missing commands or ambiguous working directories, and fails closed on malformed output, subprocess exceptions, timeouts, and unexpected errors.

A command is passed by this adapter only when the command-inspection JSON reports **both** `classification.explicitly_benign: true` and `minimum_action: "allow"`, without a contradictory stop signal. Other responses block the hook. A separate ClawCodex permission layer remains authoritative.

**Important security boundary:** this adapter invokes `hol-guard command test --json -- <command>`. `command test` is a **side-effect-free command classifier**, **not Guard's final effective policy or approval evaluator**. Its response can say `policy_evaluation: "not_run"`. A classification-based pass does not establish that workspace policy, configured defaults, another agent's permissions, or Guard approvals authorized the command. The adapter provides neither the managed Guard approval queue nor native decision receipts. Do not present it as equivalent to a supported HOL Guard managed harness installation.

The hook covers ClawCodex `Bash` commands only; it does not automatically protect file-editing, MCP, plugin, browser, or other tool surfaces.

## Installation and execution prerequisites

- Install the genuine HOL Guard CLI independently of the repository and check `hol-guard --version`. Do not run a project-local `hol-guard` shim from an untrusted worktree.
- Preserve a trusted **absolute** executable path on `PATH`. The adapter blocks relative `PATH` entries and worktree-controlled executable paths; these cannot be treated as a configuration failure to bypass.
- Keep ClawCodex's normal permissions in force. The adapter does not replace the host permission system.
- The adapter receives an explicit absolute Bash `cwd` when supplied. Otherwise, `src/services/tool_execution/tool_hooks.py` carries the effective persisted context directory or workspace root into the hook before inspection. Relative `cwd` is rejected.

## Review remediation and verification

The branch addresses the two September 15 reviewer findings in source:

1. **Blocking subprocess failures:** `src/hooks/hol_guard_pretool.py` converts non-executable Guard, invalid UTF-8, subprocess argument failures, timeouts, malformed input, and unexpected adapter errors to the hook runner's blocking exit code **2**; exit **1** is not used to signal a Guard denial.
2. **Correct Bash working directory:** the pre-tool hook carries explicit or persisted Bash `cwd` to the Guard subprocess, rather than silently inspecting the launcher directory.

Regression cases are in `tests/test_hol_guard_pretool.py` and `tests/test_hol_guard_unexpected_evaluation.py`. With the repository's development dependencies available, run:

```bash
python -m pytest -q tests/test_hol_guard_pretool.py tests/test_hol_guard_unexpected_evaluation.py
```

The code and tests were inspected against the proposed branch, but this documentation change **does not claim a fresh test run, CI pass, native Guard-policy proof, maintainer approval, or an installable upstream release**. Those are separate review and validation steps.
