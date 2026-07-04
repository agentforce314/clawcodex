"""Bundled ``/update-config`` skill — ADAPTED port of ``bundled/updateConfig.ts``.

updateConfig.ts is a settings-editing skill. It is NOT ported verbatim: its
whole job is telling the user which file + shape to write, and the port's
settings topology differs from TS. Per the SKILLS-2 scope review + an
empirical file-topology spike, the prompt is HAND-AUTHORED (like TS, which
deliberately hand-wrote examples rather than auto-generating schema docs) and
grounded in the port's REAL on-disk loaders:

* permissions + env + hooks → ``.clawcodex/settings.json`` (user
  ``~/.clawcodex/settings.json``, project ``<cwd>/.clawcodex/settings.json``,
  local ``.clawcodex/settings.local.json``) — NOT ``.claude/`` (the real
  Claude Code harness owns ``<project>/.claude/settings.json``).
* the model/provider "settings" block → ``~/.clawcodex/config.json``.
* MCP → ``.mcp.json`` + the approval flow (not a settings.json key).

The TS ``generateSettingsSchema()`` introspector is intentionally dropped (it
was aimed at the wrong artifact); the ``[hooks-only]`` mode is deferred (the
port's ``/init`` does not invoke it).
"""

from __future__ import annotations

from ..bundled_skills import BundledSkillDefinition, register_bundled_skill

UPDATE_CONFIG_PROMPT = """# Update Config: Edit clawcodex Configuration

Modify clawcodex configuration by editing its settings files. Read the target file first, apply a minimal JSON edit, and keep it valid JSON.

## Where settings live

clawcodex reads **two different files** — pick by what you are configuring.

### 1. Harness settings — `.clawcodex/settings.json`

Permissions, environment variables, and hooks. Three scopes, later overrides earlier:

| File | Scope | Git |
|------|-------|-----|
| `~/.clawcodex/settings.json` | Global (all projects) | N/A |
| `<project>/.clawcodex/settings.json` | Project | Commit |
| `<project>/.clawcodex/settings.local.json` | Personal, this project | Gitignore |

**Do NOT write to `<project>/.claude/settings.json`** — clawcodex deliberately does not own that path; the ambient Claude Code harness does. Use `.clawcodex/`.

### 2. Runtime config — `~/.clawcodex/config.json`

Model/provider selection and related runtime knobs live under the `"settings"` key of the global config file. Prefer the `/model`, `/advisor`, and `/config` commands over hand-editing this file.

## Permissions (`.clawcodex/settings.json`)

```json
{
  "permissions": {
    "allow": ["Bash(npm:*)", "Read", "Edit(src/**)"],
    "deny": ["Bash(rm -rf:*)"],
    "ask": ["Bash(git push:*)"]
  },
  "additionalWorkingDirectories": ["/extra/dir"]
}
```

- `allow` / `deny` / `ask` are arrays of **rule strings** — the enforced permission set (read at startup).
- Rule syntax: `Tool` alone (e.g. `Read`) matches all uses of that tool; `Tool(specifier)` scopes it. For `Bash`, `Bash(cmd:*)` is a prefix match (`Bash(git:*)` matches `git status`, `git commit`, …); `Bash(npm run test)` is exact. For file tools, `Edit(src/**)` / `Write(*.env)` match by path glob.
- `additionalWorkingDirectories` (a **top-level** list of path strings) grants access to paths outside the workspace root — this is the key the port reads at startup (NOT a `permissions.additionalDirectories` key).
- The permission MODE is `default`, `plan`, `acceptEdits`, `bypassPermissions`, or `dontAsk`, but set it via the `--permission-mode` flag or the `/mode` command — a `defaultMode` key is not yet read back at startup, so writing it to settings.json has no effect today.

## Environment variables (`.clawcodex/settings.json`)

```json
{
  "env": {
    "DEBUG": "true",
    "MY_API_KEY": "value"
  }
}
```

Top-level `env`; applied to the session's environment.

## Hooks (`.clawcodex/settings.json`)

Hooks run commands in response to lifecycle EVENTS. If the user wants something to happen automatically in response to an event, they need a hook — memory/preferences cannot trigger automated actions.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit",
        "hooks": [
          { "type": "command", "command": "jq -r '.tool_input.file_path // empty' | xargs -r eslint --fix 2>/dev/null || true" }
        ]
      }
    ]
  }
}
```

- Top-level `hooks` → `{ EventName: [ matcher-group, … ] }`.
- Each matcher-group is `{ "matcher": <tool-name or pattern>, "hooks": [ <hook>, … ] }`. Omit/blank `matcher` to match every invocation of the event.
- Common events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `StopFailure`, `SubagentStop`, `SessionStart`, `Notification`.
- **A `command` hook receives the event payload as JSON on STDIN** — `{ "session_id", "tool_name", "tool_input", "tool_response", … }` — so extract fields with `jq` reading stdin (the edited path is `.tool_input.file_path`). The port does NOT set a `$CLAUDE_FILE_PATHS` env var; it does export `CLAUDE_HOOK_EVENT`, `CLAUDE_PROJECT_DIR`, `CLAUDE_ENV_FILE`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_CONFIG_DIR`.
- Hook `type` is one of: `command` (run a shell command), `agent` (run a subagent — key `agentInstructions`), `http` (POST to a `url`), `prompt` (inject a prompt — key `promptText`). A hook may also carry `if` (a conditional command gate), `once` (run at most once), and `shell` (the shell to use).
- Example (agent hook): `{ "type": "agent", "matcher": "Edit", "agentInstructions": "Verify tests still pass" }`. Example (prompt hook): `{ "type": "prompt", "promptText": "Was that change safe?" }`.

## MCP servers

MCP servers are configured in a project-root `.mcp.json` file (not a settings.json key), and clawcodex prompts for approval before enabling a project's MCP servers on first use. To pre-approve all of a project's servers, set `"enableAllProjectMcpServers": true` in `.clawcodex/settings.json`.

## How to apply an edit

1. **Read** the target file first (create it as `{}` if absent).
2. Merge the minimal change into the existing JSON — do not clobber unrelated keys.
3. Write valid JSON (no comments, no trailing commas).
4. Tell the user which file you edited and whether a restart or `/config` reload is needed for it to take effect.
"""


def _get_prompt_for_command(args: str) -> str:
    prompt = UPDATE_CONFIG_PROMPT
    focus = args.strip()
    if focus:
        prompt += f"\n\n## User Request\n\n{focus}"
    return prompt


def register_update_config_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            name="update-config",
            description=(
                "Modify clawcodex configuration by editing its settings files "
                "(permissions, env, hooks in .clawcodex/settings.json)."
            ),
            when_to_use=(
                "Use when the user wants to change clawcodex configuration — "
                "permissions, environment variables, hooks, or model/provider "
                "settings."
            ),
            argument_hint="<what to configure>",
            user_invocable=True,
            # Mirror TS's allowedTools:['Read'] — auto-approve the read-before-
            # write step (SKILLS-2 N1).
            allowed_tools=["Read"],
            get_prompt_for_command=_get_prompt_for_command,
        )
    )
