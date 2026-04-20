# Chapter 6: MCP Protocol, Skills & Extension System

## Learning Objectives
1. Understand MCP (Model Context Protocol) core concepts and transport methods
2. Understand MCP tool wrapping, permission control & ToolSearch lazy loading
3. Understand the Skills system's three-layer architecture (bundled / disk-based / MCP)
4. Understand bundled skill registration mechanism, file extraction & security design
5. Understand key built-in skill prompt engineering (simplify, skillify, remember, stuck)
6. Understand the Plugins system's trust hierarchy

---

## 6.1 What is MCP?

**Building intuition first**: Claude Code comes with many built-in tools (read files, run commands, etc.), but you might need custom tools (like querying a database or calling an internal API). MCP is a "standard plug" protocol — anyone can write an MCP server in any programming language, and Claude Code will automatically recognize and use the tools it provides. Just like how USB ports let all kinds of devices connect to your computer.

**Model Context Protocol (MCP)** is an open protocol proposed by Anthropic to standardize AI model interactions with external tools and data sources.

```
┌──────────────┐  MCP Protocol  ┌──────────────┐
│  Claude Code  │ ◄──────────────────► │  MCP Server  │
│  (MCP Client) │   JSON-RPC 2.0      │  (any lang)   │
└──────────────┘                      └──────────────┘
                                        Can provide:
                                        - Tools
                                        - Resources
                                        - Prompts
```

### MCP's Three Capabilities

| Capability | Description | Example |
|-----------|-------------|---------|
| **Tools** | Executable operations | Database queries, API calls, file operations |
| **Resources** | Readable data | Documents, configs, real-time data |
| **Prompts** | Predefined prompt templates | Code review templates, analysis templates |

### Transport Methods

```typescript
// Claude Code supports multiple MCP transport methods
type McpTransport =
  | StdioClientTransport           // Standard I/O (most common)
  | SSEClientTransport             // Server-Sent Events
  | StreamableHTTPClientTransport  // HTTP streaming
  | WebSocketTransport             // WebSocket
  | SdkControlClientTransport     // SDK control channel
```

## 6.2 MCP Client Implementation

`services/mcp/client.ts` (116KB, 3349 lines) is the core MCP client implementation.

### Connection Management

```typescript
export async function connectToServer(
  name: string,
  config: ScopedMcpServerConfig
): Promise<MCPServerConnection> {
  // 1. Create transport layer based on config
  let transport: Transport;
  if (config.command) {
    // Stdio mode: launch subprocess
    transport = new StdioClientTransport({
      command: config.command,
      args: config.args,
      env: { ...subprocessEnv(), ...config.env },
    });
  } else if (config.url) {
    // HTTP/SSE/WebSocket mode
    transport = createRemoteTransport(config.url, config);
  }

  // 2. Create MCP Client and connect
  const client = new Client({ name: 'claude-code', version: '1.0.0' });
  await client.connect(transport);

  return { type: 'connected', name, client, cleanup: () => client.close() };
}
```

### Error Handling

```typescript
class McpAuthError extends Error {}        // OAuth token expired → re-authenticate
class McpSessionExpiredError extends Error {} // Session expired → reconnect
class McpToolCallError extends Error {}    // Tool call failed → return error to Claude
```

## 6.3 MCP Tool Wrapping

Tools provided by MCP servers are wrapped into Claude Code's built-in Tool format:

```typescript
function createMcpTool(serverName, toolDef): Tool {
  return buildTool({
    // Name format: mcp__serverName__toolName
    name: `mcp__${normalizeNameForMCP(serverName)}__${normalizeNameForMCP(toolDef.name)}`,
    inputJSONSchema: toolDef.inputSchema,
    isMcp: true,
    mcpInfo: { serverName, toolName: toolDef.name },

    async call(input, context) {
      const result = await client.callTool({ name: toolDef.name, arguments: input });
      return processToolResult(result);
    },

    async checkPermissions() {
      return { behavior: 'passthrough', message: 'MCPTool requires permission.' };
    },
  });
}
```

## 6.4 ToolSearch — Lazy Loading Tools

**Building intuition first**: If you have 100 tools and send all 100 full descriptions to the AI on every API call, the tool descriptions alone would consume massive tokens. ToolSearch's solution is "load on demand" — initially only send core tools, with other tools represented by just a name and keywords. When the AI needs a specific tool, its full description is loaded dynamically. Like a phone's App Store — you don't install every app upfront, you download them when needed.

When there are many tools (built-in + MCP), sending all to the API consumes significant tokens. ToolSearch enables lazy loading:

```
Initial request:
  tools: [
    Bash (full schema),
    Read (full schema),
    ToolSearch (full schema),          ← search tool
    mcp__db__query (defer_loading),    ← name only
    mcp__api__fetch (defer_loading),   ← name only
  ]

When Claude needs a lazy-loaded tool:
  1. Claude calls ToolSearch({ query: 'database query' })
  2. ToolSearch returns matching tool list
  3. Next request includes full tool schema
  4. Claude can call the tool normally
```

Some MCP tools can be marked as `alwaysLoad` (via `_meta['anthropic/alwaysLoad'] = true`), always including full schema without needing ToolSearch.

---

## 6.5 Skills System — Three-Layer Architecture

Skills are reusable workflows, essentially predefined prompt templates. Claude Code has three skill source layers:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Skills Three-Layer Architecture               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Layer 1: Bundled Skills (Built-in)                             │
│  ├── Compiled into CLI binary                                   │
│  ├── Available to all users                                     │
│  ├── Registered in src/skills/bundled/                          │
│  └── Via registerBundledSkill()                                 │
│                                                                 │
│  Layer 2: Disk-based Skills (Filesystem)                        │
│  ├── Markdown files + frontmatter                               │
│  ├── Three search paths:                                        │
│  │   ├── ~/.claude/skills/        (user-level)                  │
│  │   ├── .claude/skills/          (project-level)               │
│  │   └── managed/.claude/skills/  (enterprise policy)           │
│  └── Via loadSkillsDir()                                        │
│                                                                 │
│  Layer 3: MCP Skills                                            │
│  ├── Prompt templates from MCP servers                          │
│  ├── Via registerMCPSkillBuilders()                             │
│  └── Dynamically discovered, no pre-installation needed         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 6.5.1 Disk-based Skill Frontmatter Format

```markdown
<!-- .claude/skills/review-pr/SKILL.md -->
---
name: review-pr
description: Review a pull request
allowed-tools:
  - Bash(gh:*)
  - Read
  - Grep
when_to_use: >
  Use when the user wants to review a PR.
  Examples: 'review this PR', 'check the changes'
argument-hint: "[PR number or URL]"
arguments:
  - pr_ref
context: fork
---

# PR Review Skill

Review PR `$pr_ref`:

1. Run `gh pr diff $pr_ref` to see changes
2. Check for code quality issues and security vulnerabilities
3. Provide a summary with recommendations

**Success criteria**: A structured review with actionable feedback.
```

### 6.5.2 Complete Frontmatter Fields

```typescript
export function parseSkillFrontmatterFields(
  frontmatter: FrontmatterData,
  markdownContent: string,
  resolvedName: string,
): {
  displayName: string | undefined
  description: string
  hasUserSpecifiedDescription: boolean
  allowedTools: string[]
  argumentHint: string | undefined
  argumentNames: string[]
  whenToUse: string | undefined
  version: string | undefined
  model: ReturnType<typeof parseUserSpecifiedModel> | undefined
  disableModelInvocation: boolean
  userInvocable: boolean
  hooks: HooksSettings | undefined
  context: 'inline' | 'fork' | undefined
  agent: string | undefined
  effort: EffortValue | undefined
  shell: FrontmatterShell | undefined
}
```

Key field descriptions:

| Field | Purpose | Design Insight |
|-------|---------|---------------|
| `allowed-tools` | Restrict tools available to skill | Supports parameterized permissions like `Bash(gh:*)` |
| `when_to_use` | Tell Claude when to auto-invoke | Key to skill auto-discovery |
| `context` | `inline` or `fork` | fork runs in isolated sub-Agent, doesn't pollute main conversation |
| `agent` | Specify Agent type to run this skill | Can use custom Agent for execution |
| `arguments` | Parameter list | Supports `$arg_name` template substitution |
| `shell` | Specify shell environment | For shell command execution in prompts |

---

## 6.6 Bundled Skills — Built-in Skill Registration

### 6.6.1 Registration Flow

```typescript
// bundledSkills.ts
export type BundledSkillDefinition = {
  name: string
  description: string
  aliases?: string[]
  whenToUse?: string
  allowedTools?: string[]
  model?: string
  disableModelInvocation?: boolean
  userInvocable?: boolean
  isEnabled?: () => boolean
  hooks?: HooksSettings
  context?: 'inline' | 'fork'
  agent?: string
  files?: Record<string, string>  // Accompanying reference files
  getPromptForCommand: (args: string, context: ToolUseContext) => Promise<ContentBlockParam[]>
}

export function registerBundledSkill(definition: BundledSkillDefinition): void {
  // If there are accompanying files, wrap getPromptForCommand for lazy extraction
  if (files && Object.keys(files).length > 0) {
    skillRoot = getBundledSkillExtractDir(definition.name)
    let extractionPromise: Promise<string | null> | undefined
    const inner = definition.getPromptForCommand
    getPromptForCommand = async (args, ctx) => {
      // Lazy extraction: extract to disk on first call, reuse afterwards
      extractionPromise ??= extractBundledSkillFiles(definition.name, files)
      const extractedDir = await extractionPromise
      const blocks = await inner(args, ctx)
      if (extractedDir === null) return blocks
      return prependBaseDir(blocks, extractedDir)
    }
  }

  bundledSkills.push(command)
}
```

**Design Insight 1: Lazy Extraction + Promise Memoization**

```typescript
// The Promise is memoized (not the result), so concurrent callers wait for the same extraction
// rather than each initiating independent writes
extractionPromise ??= extractBundledSkillFiles(definition.name, files)
```

### 6.6.2 File Extraction Security Design

```typescript
// Safe write: O_NOFOLLOW | O_EXCL prevents symlink attacks
const SAFE_WRITE_FLAGS =
  process.platform === 'win32'
    ? 'wx'
    : fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | O_NOFOLLOW

async function safeWriteFile(p: string, content: string): Promise<void> {
  const fh = await open(p, SAFE_WRITE_FLAGS, 0o600)  // owner-only
  try {
    await fh.writeFile(content, 'utf8')
  } finally {
    await fh.close()
  }
}

// Path validation: prevent directory traversal
function resolveSkillFilePath(baseDir: string, relPath: string): string {
  const normalized = normalize(relPath)
  if (
    isAbsolute(normalized) ||
    normalized.split(pathSep).includes('..') ||
    normalized.split('/').includes('..')
  ) {
    throw new Error(`bundled skill file path escapes skill dir: ${relPath}`)
  }
  return join(baseDir, normalized)
}
```

**Design Insight 2: Multi-Layer Defense**

```
Defense Layer 1: getBundledSkillsRoot() uses process-level nonce → unpredictable path
Defense Layer 2: mkdir uses 0o700 → only owner can access
Defense Layer 3: O_NOFOLLOW → don't follow symlinks
Defense Layer 4: O_EXCL → fail if file exists (no overwrite)
Defense Layer 5: resolveSkillFilePath → reject .. and absolute paths
Defense Layer 6: No unlink+retry → avoid unlink following intermediate symlinks
```

### 6.6.3 Registration at Startup

```typescript
// bundled/index.ts
export function initBundledSkills(): void {
  // Unconditionally registered skills
  registerUpdateConfigSkill()
  registerKeybindingsSkill()
  registerVerifySkill()
  registerDebugSkill()
  registerLoremIpsumSkill()
  registerSkillifySkill()
  registerRememberSkill()
  registerSimplifySkill()
  registerBatchSkill()
  registerStuckSkill()

  // Feature-gated skills (loaded on demand)
  if (feature('KAIROS') || feature('KAIROS_DREAM')) {
    const { registerDreamSkill } = require('./dream.js')
    registerDreamSkill()
  }
  if (feature('AGENT_TRIGGERS')) {
    const { registerLoopSkill } = require('./loop.js')
    registerLoopSkill()
  }
  // ... more feature-gated skills
}
```

**Design Insight 3: require() instead of import**

Feature-gated skills use `require()` instead of `import` because:
- `import` is static — even inside `if` blocks, the bundler includes it
- `require()` is dynamic — only loaded when condition is met
- This reduces the external build's bundle size

---

## 6.7 Key Built-in Skills Deep Analysis

### 6.7.1 /simplify — Three-Agent Parallel Code Review

This is an exemplary skill showcasing multi-Agent collaboration:

```
/simplify execution flow:

Phase 1: Identify changes
  └── git diff (or git diff HEAD)

Phase 2: Launch three parallel Agents
  ├── Agent 1: Code Reuse Review
  │   └── Search existing utility functions, flag duplicate implementations
  ├── Agent 2: Code Quality Review
  │   └── Check redundant state, parameter bloat, copy-paste, leaky abstractions
  └── Agent 3: Efficiency Review
      └── Check unnecessary work, missing concurrency, hot path bloat

Phase 3: Aggregate and fix
  └── Combine findings from all three Agents, directly fix issues
```

Note the careful prompt design:
- "If a finding is a false positive, note it and move on — do not argue with the finding"
  → Prevents Claude from wasting tokens arguing
- Each Agent receives the complete diff → avoids insufficient context
- Three Agents launch in parallel → leverages Coordinator's concurrency

### 6.7.2 /skillify — Session-to-Skill Converter

The most complex built-in skill, capable of converting the current session's workflow into a reusable skill:

```typescript
const SKILLIFY_PROMPT = `# Skillify {{userDescriptionBlock}}

You are capturing this session's repeatable process as a reusable skill.

## Your Session Context

Here is the session memory summary:
<session_memory>
{{sessionMemory}}
</session_memory>

Here are the user's messages during this session:
<user_messages>
{{userMessages}}
</user_messages>
`
```

**Design Insight 4: Dynamic Context Injection**

```typescript
async getPromptForCommand(args, context) {
  // Get session memory (Claude's understanding of current session)
  const sessionMemory = (await getSessionMemoryContent()) ?? 'No session memory available.'

  // Extract user messages (only after compact boundary to avoid excessive length)
  const userMessages = extractUserMessages(
    getMessagesAfterCompactBoundary(context.messages),
  )

  // Template substitution
  const prompt = SKILLIFY_PROMPT
    .replace('{{sessionMemory}}', sessionMemory)
    .replace('{{userMessages}}', userMessages.join('\n\n---\n\n'))
    .replace('{{userDescriptionBlock}}', userDescriptionBlock)

  return [{ type: 'text', text: prompt }]
}
```

/skillify's prompt designs a 4-round interactive interview:
1. **Round 1**: Confirm name, description, goals
2. **Round 2**: Confirm steps, parameters, inline/fork, save location
3. **Round 3**: Deep dive into each step's details
4. **Round 4**: Confirm trigger conditions and caveats

### 6.7.3 /remember — Memory Hierarchy Management

```
/remember memory hierarchy:

┌─────────────────────────────────────────────┐
│  CLAUDE.md          (project-level, shared)  │
│  ├── Project conventions: use bun not npm    │
│  ├── Code style: prefer functional style     │
│  └── Test command: bun test                  │
├─────────────────────────────────────────────┤
│  CLAUDE.local.md    (personal, not in git)   │
│  ├── Personal prefs: I prefer concise output │
│  └── Workflow: don't auto-commit             │
├─────────────────────────────────────────────┤
│  Auto-memory        (auto-extracted notes)   │
│  ├── Patterns observed during sessions       │
│  └── Temporary context                       │
├─────────────────────────────────────────────┤
│  Team memory        (org-level, cross-repo)  │
│  └── Deploy process, staging URLs, etc.      │
└─────────────────────────────────────────────┘
```

/remember's core job is **classification and promotion**:
- Analyze each record in auto-memory
- Determine which level it should be promoted to
- Detect cross-level duplicates and conflicts
- Generate structured report, wait for user confirmation before modifying

### 6.7.4 /stuck — Diagnose Stuck Sessions (Internal Tool)

```typescript
export function registerStuckSkill(): void {
  // Only available to Anthropic internal users
  if (process.env.USER_TYPE !== 'ant') return

  registerBundledSkill({
    name: 'stuck',
    description: '[ANT-ONLY] Investigate frozen/stuck/slow Claude Code sessions...',
    // ...
  })
}
```

/stuck's prompt is a complete diagnostic manual:
- List all Claude Code processes (`ps -axo pid=,pcpu=,rss=,etime=,state=,comm=,command=`)
- Check CPU, memory, process state (D/T/Z)
- Check child processes (`pgrep -lP <pid>`)
- Optional: macOS `sample <pid> 3` for native stack sampling
- If issues found, auto-send to Slack #claude-code-feedback

**Design Insight 5: Two-Message Structure**

```
Slack message structure:
  1. Top-level message: one-line brief summary (hostname + version + symptom)
  2. Thread reply: complete diagnostic data

Reason: Keep channel scannable, detailed info in threads
```

---

## 6.8 Skill Loading & Deduplication

### 6.8.1 Filesystem Skill Loading

```typescript
// loadSkillsDir.ts — Load skills from multiple directories
// Search path priority:
//   1. policySettings (enterprise policy)
//   2. userSettings (user-level)
//   3. projectSettings (project-level)
//   4. plugin (plugin-provided)

// Dedup strategy: use realpath to resolve symlinks
async function getFileIdentity(filePath: string): Promise<string | null> {
  try {
    return await realpath(filePath)
  } catch {
    return null
  }
}
```

**Design Insight 6: Why realpath instead of inode?**

```
// Source comment:
// Uses realpath to resolve symlinks, which is filesystem-agnostic and avoids
// issues with filesystems that report unreliable inode values (e.g., inode 0 on
// some virtual/container/NFS filesystems, or precision loss on ExFAT).
```

Some filesystems (NFS, containers, ExFAT) have unreliable inode values (may return 0), causing different files to be incorrectly identified as the same. realpath deduplicates via path normalization, which is more reliable.

### 6.8.2 Skill Token Estimation

```typescript
// Only estimate frontmatter tokens (name + description + whenToUse)
// Don't estimate full content (content only loaded on invocation)
export function estimateSkillFrontmatterTokens(skill: Command): number {
  const frontmatterText = [skill.name, skill.description, skill.whenToUse]
    .filter(Boolean)
    .join(' ')
  return roughTokenCountEstimation(frontmatterText)
}
```

This is an important optimization: a skill's full prompt can be very long (/simplify has ~70 lines), but only frontmatter info needs to be sent in each API request (for Claude to decide whether to invoke).

---

## 6.9 Plugins System

Plugins are a more powerful extension mechanism, providing tools, skills, Agents, and more:

```typescript
type Plugin = {
  name: string
  version: string
  tools?: Tool[]              // Custom tools
  skills?: Skill[]            // Custom skills
  agents?: AgentDefinition[]  // Custom Agents
  commands?: Command[]        // Custom /slash commands
  hooks?: HookDefinition[]    // Custom hooks
}
```

### Plugin Trust Hierarchy

```typescript
type PluginSource =
  | 'builtin'         // Built-in (fully trusted)
  | 'plugin'          // Installed plugin (admin trusted)
  | 'policySettings'  // Enterprise policy (admin trusted)
  | 'user'            // User-defined (restricted trust)

// In strictPluginOnlyCustomization mode:
// - Only builtin/plugin/policySettings source extensions can load
// - User-defined MCP, hooks, agents are blocked
```

---

## 6.10 MCP Configuration Hierarchy

```json
// ~/.claude/settings.json (user-level)
{
  "mcpServers": {
    "my-database": {
      "command": "node",
      "args": ["db-server.js"],
      "env": { "DB_URL": "postgres://..." }
    }
  }
}

// .claude/settings.json (project-level)
{
  "mcpServers": {
    "project-tools": {
      "command": "python",
      "args": ["tools/mcp_server.py"]
    }
  }
}
```

Merge rules: project-level overrides user-level (on name collision), enterprise policy has highest priority.

---

## Chapter Summary

| Concept | Key Point |
|---------|-----------|
| MCP Protocol | Standardized AI-tool interaction protocol, supports Tools/Resources/Prompts |
| ToolSearch | Lazy loading mechanism, reduces initial token consumption |
| Skills Three Layers | bundled (compiled into binary) → disk-based (Markdown) → MCP (dynamic) |
| Bundled Skill | registerBundledSkill() registration, lazy file extraction, Promise memoization |
| File Security | O_NOFOLLOW + O_EXCL + nonce directory + path validation, 6-layer defense |
| /simplify | Three-Agent parallel review (reuse, quality, efficiency) |
| /skillify | 4-round interactive interview, converts session to reusable skill |
| /remember | Memory hierarchy management, classify + promote + deduplicate |
| Plugins | Complete extension packages, trust hierarchy (builtin > plugin > user) |

### Next Chapter Preview
Chapter 7 will dive deep into **Prompt Engineering** — understanding Claude Code's system prompt design philosophy.
