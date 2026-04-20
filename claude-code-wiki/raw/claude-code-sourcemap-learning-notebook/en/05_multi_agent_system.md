# Chapter 5: Multi-Agent Systems (In-Depth Version)

> **AgentTool is the most complex single file in Claude Code (228KB).** It implements a complete
> multi-agent runtime: in-process forking, git worktree isolation, tmux team collaboration, asynchronous lifecycle management.
> This chapter will delve into every engineering ingenuity.

## Learning Objectives
1. Understand the "default isolation, explicit sharing" design of Agent context isolation
2. Understand the prompt cache sharing mechanism of Forked child Agents
3. Understand the 12-step cleanup process of runAgent (why each step is indispensable)
4. Understand the inheritance and override rules of permission modes
5. Understand the lifecycle management of asynchronous Agents (spawn → progress → complete/kill)
6. Understand the multi-layer filtering strategy of toolkit parsing
7. **New** Establish practical cognition through a complete Coordinator orchestration case
8. **New** Understand the communication mechanism and message timing between Workers
9. **New** Extract design patterns that can be migrated to your own Agent system
---

## 5.1 Architectural Overview

**Building intuition first**: Sometimes a task is too big for one AI to handle alone. Claude Code's solution is "cloning" — the main AI can create child Agents to handle subtasks in parallel. Think of it like a project manager delegating work to team members: some tasks are handed off synchronously (like face-to-face assignments), some run asynchronously in the background (like email assignments), and some share context (like collaborating on the same document).

### 5.1.1 Three Operation Modes of Agent Systems

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Agent Operation Modes │
├─────────────────────────────────────────────────────────────────────────┤
│ │
│ Mode 1: Synchronous Child Agent (default) │
│ ┌──────────┐ ┌──────────┐ │
│ │ Main Agent│────▶│ Child Agent│ Share abortController │
│ │ (Waiting) │◀────│ (Executing)│ Share setAppState │
│ └──────────┘ └──────────┘ Can pop permission dialogs │
│ │
│ Mode 2: Asynchronous Background Agent │
│ ┌──────────┐ ┌──────────┐ │
│ │ Main Agent│ │ Background Agent│ Independent abortController │
│ │ (Continue working)│ │ (Independent execution)│ setAppState = no-op │
│ └──────────┘ └──────────┘ Cannot pop permission dialogs │
│ │
│ Mode 3: Forked Child Agent │
│ ┌──────────┐ ┌──────────┐ │
│ │ Main Agent│────▶│ Fork │ Inherit all parent tools │
│ │ (Waiting) │◀────│ (Executing)│ Inherit thinkingConfig │
│ └──────────┘ └──────────┘ Share prompt cache │
│ │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.1.2 Key File Map

```
AgentTool/
├── AgentTool.tsx (228KB) ← Main entry, input validation, scheduling logic
├── runAgent.ts (35KB) ← Agent lifecycle management
├── agentToolUtils.ts (22KB) ← Toolkit parsing, result processing, asynchronous lifecycle
├── loadAgentsDir.ts ← Agent definition loading
├── prompt.ts (16KB) ← Prompt for Agent scheduling
├── constants.ts ← Tool name constants
└── built-in/
 ├── generalPurposeAgent.ts ← General-purpose Agent
 └── exploreAgent.ts ← Exploration Agent (read-only)
```

---
## 5.2 createSubagentContext — "Default Isolation, Explicit Sharing"

**Building intuition first**: When the main AI creates a child Agent, the child gets its own independent "workspace" by default — its own file cache, its own memory, its own cancellation controller. If something needs to be shared (like letting the child pop up permission dialogs), it must be **explicitly declared**. It's like a company assigning new employees their own desk, but if they need access to the shared printer, they have to apply for permission separately.

This is the most core design decision of the entire Agent system. `createSubagentContext` creates the execution context for child Agents,
**default isolates all mutable states**, and callers must explicitly opt-in to share:

```typescript
export function createSubagentContext(
  parentContext: ToolUseContext,
  overrides?: SubagentContextOverrides,
): ToolUseContext {
  return {
  // ===== Default isolated states =====

  // File cache: clone (not shared)
  readFileState: cloneFileStateCache(
  overrides?.readFileState ?? parentContext.readFileState,
  ),

  // Brand new set (does not inherit from parent)
  nestedMemoryAttachmentTriggers: new Set<string>(),
  loadedNestedMemoryPaths: new Set<string>(),
  dynamicSkillDirTriggers: new Set<string>(),
  discoveredSkillNames: new Set<string>(),
  toolDecisions: undefined,

  // ===== Explicit opt-in sharing =====

  // setAppState: default no-op unless shareSetAppState = true
  setAppState: overrides?.shareSetAppState
  ? parentContext.setAppState
  : () => {},

  // setResponseLength: default no-op unless shareSetResponseLength = true
  setResponseLength: overrides?.shareSetResponseLength
  ? parentContext.setResponseLength
  : () => {},

  // abortController: default create sub-controller unless shareAbortController = true
  abortController: overrides?.abortController ??
  (overrides?.shareAbortController
  ? parentContext.abortController
  : createChildAbortController(parentContext.abortController)),

  // ===== Always no-op UI callbacks ====
  addNotification: undefined,
  setToolJSX: undefined,
  setStreamMode: undefined,
  setSDKStatus: undefined,
  openMessageSelector: undefined,

  // ===== Always shared ====
  // Task registration must reach the root store (even if setAppState is no-op)
  setAppStateForTasks:
  parentContext.setAppStateForTasks ?? parentContext.setAppState,
  // Attribution is a scoped functional callback, safely shared
  updateAttributionState: parentContext.updateAttributionState,
  }
}
```

**Ingenuity 1: setAppStateForTasks is always shared**

```typescript
// Original comment:
// Task registration/kill must always reach the root store, even when
// setAppState is a no-op — otherwise async agents' background bash tasks
// are never registered and never killed (PPID=1 zombie).
```

Even if the `setAppState` of asynchronous Agents is a no-op (does not affect the UI),
their background Bash tasks still need to be registered with the root store, otherwise:
- Tasks will not appear in `claude ps`
- Agents cannot kill these tasks when ending
- Processes exit and become PPID=1 zombie processes

**Ingenuity 2: contentReplacementState clones instead of creating new**

```typescript
// Original comment:
// Clone by default (not fresh): cache-sharing forks process parent
// messages containing parent tool_use_ids. A fresh state would see
// them as unseen and make divergent replacement decisions → wire
// prefix differs → cache miss. A clone makes identical decisions →
// cache hit.
contentReplacementState:
  overrides?.contentReplacementState ??
  (parentContext.contentReplacementState
  ? cloneContentReplacementState(parentContext.contentReplacementState)
  : undefined),
```

This is for prompt cache hits: Forked child Agents process messages from the parent,
if using a brand new state, they would make different replacement decisions for the same tool_use_id,
causing the bytes sent to the API to differ → cache miss.

---
## 5.3 Permission Mode Inheritance and Override

Agents have a set of fine-grained inheritance rules for permission modes:

```typescript
const agentGetAppState = () => {
  const state = toolUseContext.getAppState()
  let toolPermissionContext = state.toolPermissionContext

  // Rule 1: Agents can define their own permission modes
  // However! bypassPermissions, acceptEdits, auto modes cannot be overridden
  if (
  agentPermissionMode &&
  state.toolPermissionContext.mode !== 'bypassPermissions' &&
  state.toolPermissionContext.mode !== 'acceptEdits' &&
  !(feature('TRANSCRIPT_CLASSIFIER') &&
  state.toolPermissionContext.mode === 'auto')
  ) {
    toolPermissionContext = {
      ...toolPermissionContext,
      mode: agentPermissionMode,
    }
  }

  // Rule 2: Asynchronous Agents cannot pop permission dialogs
  // But bubble mode is an exception (bubbles up to the parent terminal)
  const shouldAvoidPrompts =
  canShowPermissionPrompts !== undefined
  ? !canShowPermissionPrompts
  : agentPermissionMode === 'bubble'
  ? false
  : isAsync

  // Rule 3: Asynchronous but can display prompt Agents
  // → Wait for automated checks (classifier, hooks), then pop dialogs
  if (isAsync && !shouldAvoidPrompts) {
    toolPermissionContext = {
      ...toolPermissionContext,
      awaitAutomatedChecksBeforeDialog: true,
    }
  }

  // Rule 4: Tool permission scope
  // Retain SDK level --allowedTools (cliArg)
  // But clear parent session level permissions
  if (allowedTools !== undefined) {
    toolPermissionContext = {
      ...toolPermissionContext,
      alwaysAllowRules: {
        cliArg: state.toolPermissionContext.alwaysAllowRules.cliArg,
        session: [...allowedTools],
      },
    }
  }
}
```

**Permission Inheritance Matrix**:

```
┌──────────────────────────────────────────────────────────────────────┐
│ Permission Mode Inheritance Rules │
├──────────────────────────────────────────────────────────────────────┤
│ │
│ Parent Mode \ Agent Defined Has permissionMode No │
│ ────────────────────────────────────────────── │
│ bypassPermissions Parent takes precedence Parent │
│ acceptEdits Parent takes precedence Parent │
│ auto (+ classifier) Parent takes precedence Parent │
│ plan Agent overrides ⬇ Parent │
│ default Agent overrides ⬇ Parent │
│ │
│ Principle: A more permissive parent mode cannot be tightened by a child Agent │
│ Reason: The user has already chosen a trust level, and child Agents should not downgrade │
│ │
└──────────────────────────────────────────────────────────────────────┘
```

---
## 5.4 Toolkit Parsing — Multi-layer Filtering

### 5.4.1 filterToolsForAgent — Basic Filtering

```typescript
export function filterToolsForAgent({
  tools, isBuiltIn, isAsync, permissionMode,
}): Tools {
  return tools.filter(tool => {
    // Rule 1: MCP tools are always allowed
    if (tool.name.startsWith('mcp__')) return true

    // Rule 2: plan mode Agents allow ExitPlanMode
    if (toolMatchesName(tool, EXIT_PLAN_MODE_V2_TOOL_NAME)
    && permissionMode === 'plan') return true

    // Rule 3: Tools disallowed by all Agents
    if (ALL_AGENT_DISALLOWED_TOOLS.has(tool.name)) return false
    // → AskUserQuestion, EnterPlanMode, ExitPlanMode, Brief

    // Rule 4: Additional tools disallowed by custom Agents
    if (!isBuiltIn && CUSTOM_AGENT_DISALLOWED_TOOLS.has(tool.name))
    return false

    // Rule 5: Asynchronous Agents only allow whitelisted tools
    if (isAsync && !ASYNC_AGENT_ALLOWED_TOOLS.has(tool.name)) {
      // Exception: in-process teammate in Swarm mode
      if (isAgentSwarmsEnabled() && isInProcessTeammate()) {
        if (toolMatchesName(tool, AGENT_TOOL_NAME)) return true
        if (IN_PROCESS_TEAMMATE_ALLOWED_TOOLS.has(tool.name)) return true
      }
      return false
    }
    return true
  })
}
```

### 5.4.2 resolveAgentTools — Agent Definition Level Filtering

```typescript
export function resolveAgentTools(
agentDefinition, availableTools, isAsync, isMainThread,
): ResolvedAgentTools {
  // Step 1: Basic filtering (filterToolsForAgent above)
  const filteredAvailableTools = isMainThread
  ? availableTools // Skip filtering for the main thread!
  : filterToolsForAgent({ ... })

  // Step 2: Remove disallowedTools
  const allowedAvailableTools = filteredAvailableTools.filter(
  tool => !disallowedToolSet.has(tool.name),
  )

  // Step 3: If tools is ['*'] or undefined → Allow all
  if (hasWildcard) return { resolvedTools: allowedAvailableTools }

  // Step 4: Precise match the Agent-defined tool list
  for (const toolSpec of agentTools) {
    const { toolName, ruleContent } = permissionRuleValueFromString(toolSpec)
  }
```plaintext
// Special: Agent tools can carry allowedAgentTypes metadata
// For example: "Agent(worker, researcher)" → Only these two sub Agents are allowed
if (toolName === AGENT_TOOL_NAME && ruleContent) {
 allowedAgentTypes = ruleContent.split(',').map(s => s.trim())
}

const tool = availableToolMap.get(toolName)
if (tool) validTools.push(toolSpec)
else invalidTools.push(toolSpec)
}
```

**Ingenuity3: Metadata in Tool Specifications**

```
Tool specifications are not just tool names, they can also carry metadata:

"Agent" → Allows Agent tools, all sub Agent types
"Agent(worker, researcher)" → Allows Agent tools, but only worker and researcher can be created
"Bash(npm test)" → Allows Bash tools, but only runs npm test
"Edit(src/**)" → Allows Edit tools, but only edits files under src/

This is parsed by permissionRuleValueFromString():
"Agent(worker, researcher)" → { toolName: "Agent", ruleContent: "worker, researcher" }
```

---
## 5.5 Fork Sub Agent — The Secret of Prompt Cache Sharing

Fork is the most ingenious pattern in the Agent system. Its core goal is to **share the parent's prompt cache**:

```
Parent API request:
 system_prompt + tools + messages[0..N] + user_message
 ├── Cache prefix ───────────────────────┤

Fork Sub Agent's API request:
 system_prompt + tools + messages[0..N] + fork_prompt
 ├── Same cache prefix ───────────────────┤
 → Cache hit! Only pay for the incremental token of fork_prompt
```

### 5.5.1 Why does Fork need useExactTools?

```typescript
// In runAgent.ts:
const resolvedTools = useExactTools
 ? availableTools // Fork: Directly use the parent's tool list
 : resolveAgentTools(...) // Normal: Filtered

const agentOptions = {
 // Fork: Inherit the parent's thinkingConfig
 thinkingConfig: useExactTools
 ? toolUseContext.options.thinkingConfig
 : { type: 'disabled' }, // Normal: Disable thinking (save tokens),

 // Fork: Inherit the parent's isNonInteractiveSession
 isNonInteractiveSession: useExactTools
 ? toolUseContext.options.isNonInteractiveSession
 : isAsync ? true : (toolUseContext.options.isNonInteractiveSession ?? false),
}
```

**Every difference can cause a cache miss**:
- Different tool lists → Cache miss
- Different thinkingConfig → Cache miss
- Different message prefixes → Cache miss

So Fork must exactly replicate all cache-critical parameters from the parent.

### 5.5.2 querySource Persistence

```typescript
// Fork needs to save querySource in options
// Because recursive fork guards check options.querySource === 'agent:builtin:fork'
// This check remains valid after autocompact (autocompact rewrites messages but not options)
...(useExactTools && { querySource }),
```

**Ingenuity4: Why not check message content?**

Because autocompact rewrites messages! If the recursive fork guard only checks for fork marks in messages,
the marks disappear after autocompact, the guard fails, potentially leading to infinite recursive forks.

---
## 5.6 The Complete Lifecycle of runAgent

### 5.6.1 Initialization Phase (12 Steps)

```
1. getAgentModel() → Parse model (Agent definition > parent > default)
2. createAgentId() → Generate unique ID
3. registerPerfettoAgent() → Register for performance tracing (hierarchical visualization)
4. filterIncompleteToolCalls() → Filter incomplete tool calls
5. getUserContext() / getSystemContext() → Get context
6. Parse userContext → Omit CLAUDE.md
7. Parse systemContext → Omit gitStatus
8. Build agentGetAppState → Permission mode override
9. resolveAgentTools() → Tool set resolution
10. getAgentSystemPrompt() → System prompt
11. executeSubagentStartHooks() → Start hooks
12. initializeAgentMcpServers() → MCP servers
```

### 5.6.2 filterIncompleteToolCalls — Prevent API Errors

When Fork Sub Agent inherits the parent's message history, it may contain incomplete tool calls
(tool_use without corresponding tool_result):

```typescript
export function filterIncompleteToolCalls(messages: Message[]): Message[] {
 // 1. Collect all tool_use_id with results
 const toolUseIdsWithResults = new Set<string>()
 for (const message of messages) {
 if (message?.type === 'user') {
 for (const block of message.message.content) {
 if (block.type === 'tool_result') {
 toolUseIdsWithResults.add(block.tool_use_id)
 }
 }
 }
 }

 // 2. Filter out assistant messages with incomplete tool_use
 return messages.filter(message => {
 if (message?.type === 'assistant') {
 const hasIncompleteToolCall = message.message.content.some(
 block => block.type === 'tool_use' && !toolUseIdsWithResults.has(block.id),
 )
 return !hasIncompleteToolCall
 }
 return true
 })
}
```

**Why are there incomplete tool calls?**

When a user presses Ctrl+C during tool execution, the assistant message already includes the tool_use block,
but the tool_result has not been generated. If these messages are passed to the Sub Agent, the API will return an error.

### 5.6.3 Context Optimization — Omit Unnecessary Information

```typescript
// Explore/Plan Agent omits CLAUDE.md
// Original comment:
// Read-only agents (Explore, Plan) don't act on commit/PR/lint rules from
// CLAUDE.md — the main agent has full context and interprets their output.
// Dropping claudeMd here saves ~5-15 Gtok/week across 34M+ Explore spawns.
const shouldOmitClaudeMd =
 agentDefinition.omitClaudeMd &&
 !override?.userContext &&
 getFeatureValue_CACHED_MAY_BE_STALE('tengu_slim_subagent_claudemd', true)

// Explore/Plan Agent omits gitStatus
// Original comment:
// Explore/Plan are read-only search agents — the parent-session-start
// gitStatus (up to 40KB, explicitly labeled stale) is dead weight. If they
// need git info they run `git status` themselves and get fresh data.
// Saves ~1-3 Gtok/week fleet-wide.
```

**Ingenuity5: Scalable Token Savings**

```
Size of CLAUDE.md: ~2-10KB (about 500-2500 tokens)
Explore Agent calls per week: 34M+ times
Savings per call: ~1000 tokens
Weekly savings: 34M × 1000 = 34 Gtok (34 billion tokens)

Size of gitStatus: up to 40KB
Weekly savings: ~1-3 Gtok

Total: Weekly savings of 5-15 Gtok
```

This is why the savings are precisely recorded in the comments — these optimizations have a significant impact at scale.

---
## 5.7 MCP Server Management — Shared vs. Dedicated

Agents can define their own MCP servers, in two ways:

```typescript
async function initializeAgentMcpServers(
 agentDefinition, parentClients,
) {
 for (const spec of agentDefinition.mcpServers) {
 if (typeof spec === 'string') {
 // Method 1: Reference existing server (shared connection)
 // Use memoized connectToServer → Reuse parent's connection
 // Do not clean up when Agent ends (since it's shared)
 name = spec
 config = getMcpConfigByName(spec)
 isNewlyCreated = false
 } else {
 // Method 2: Inline definition (Agent-specific)
 // Create new connection
 // Clean up when Agent ends
 const [serverName, serverConfig] = Object.entries(spec)[0]
 name = serverName
 config = { ...serverConfig, scope: 'dynamic' }
 isNewlyCreated = true
 }
 }

 // Clean up function only cleans up newly created connections
 const cleanup = async () => {
 for (const client of newlyCreatedClients) {
 if (client.type === 'connected') {
 await client.cleanup()
 }
 }
 }

 // Return merged client list
 return {
 clients: [...parentClients, ...agentClients],
 tools: agentTools,
 cleanup,
 }
}
```

**Ingenuity6: Trust Hierarchy under Plugin-only Policy**

```typescript
// When MCP is locked to plugin-only:
// - User-created Agents → Skip MCP servers
// - Plugin/built-in/policySettings Agents → Allow MCP
const agentIsAdminTrusted = isSourceAdminTrusted(agentDefinition.source)
if (isRestrictedToPluginOnly('mcp') && !agentIsAdminTrusted) {
 return { clients: parentClients, tools: [], cleanup: async () => {} }
}
```

This ensures that administrator-approved Agents (provided by plugins) can use MCP,
while user-customized Agents cannot bypass MCP restrictions.

---
## 5.8 Skill Preloading — Three-level Name Resolution

Agents can declare skills to preload in frontmatter:

```typescript
function resolveSkillName(
 skillName: string,
 allSkills: Command[],
 agentDefinition: AgentDefinition,
): string | null {
 // Strategy 1: Exact match (name, userFacingName, aliases)
 if (hasCommand(skillName, allSkills)) return skillName

 // Strategy 2: Complete with Agent's plugin prefix
 // For example: Agent type "my-plugin:my-agent", skill name "my-skill"
 // → Try "my-plugin:my-skill"
 const pluginPrefix = agentDefinition.agentType.split(':')[0]
 if (pluginPrefix) {
 const qualifiedName = `${pluginPrefix}:${skillName}`
 if (hasCommand(qualifiedName, allSkills)) return qualifiedName
 }

 // Strategy 3: Suffix match
 // Find any command ending with ":skillName"
 const suffix = `:${skillName}`
 const match = allSkills.find(cmd => cmd.name.endsWith(suffix))
 if (match) return match.name

 return null
}
```

**Ingenuity7: Progressive Name Resolution**

```
User writes: skills: ["my-skill"]

Resolution process:
 1. Exact match "my-skill" → Not found
 2. Complete as "my-plugin:my-skill" → Found!
 3. (If still not found) Suffix match ":my-skill" → May find "other-plugin:my-skill"

This allows Agent authors to reference skills with short names,
without writing the full namespace path.
```

---
## 5.9 Cleanup Process — Why Every Step is Essential

```typescript
try {
 for await (const message of query({ ... })) {
 // ... execute Agent ...
 }
} finally {
 // 1. Clean up Agent-specific MCP servers
 await mcpCleanup()
 // → Not cleaned: MCP process leaks, port occupation

 // 2. Clean up session hooks
 if (agentDefinition.hooks) {
 clearSessionHooks(rootSetAppState, agentId)
 }
 // → Not cleaned: Agent's hooks continue to trigger after Agent ends

 // 3. Clean up prompt cache tracking
 if (feature('PROMPT_CACHE_BREAK_DETECTION')) {
 cleanupAgentTracking(agentId)
 }
 // → Not cleaned: Memory leak in cache break detection

 // 4. Release file cache
 agentToolUseContext.readFileState.clear()
 // → Not cleaned: Cloned file cache occupies memory

 // 5. Release message array
 initialMessages.length = 0
 // → Not cleaned: Fork context messages occupy memory
 // Note: .length = 0 is better than = [] because it releases element references for GC to reclaim

 // 6. Release Perfetto tracing
 unregisterPerfettoAgent(agentId)
 // → Not cleaned: Performance tracing registry leaks

 // 7. Release transcript subdirectory mapping
 clearAgentTranscriptSubdir(agentId)
 // → Not cleaned: Transcript routing table leaks

 // 8. Release todos entries
 rootSetAppState(prev => {
 if (!(agentId in prev.todos)) return prev
 const { [agentId]: _removed, ...todos } = prev.todos
return { ...prev, todos }
})
// → No cleanup: Each Agent leaves an empty todos entry
// Comment original text: "Whale sessions spawn hundreds of agents; each orphaned
// key is a small leak that adds up."

// 9. Terminate background bash tasks
killShellTasksForAgent(agentId, ...)
// → No cleanup: Background shell processes become PPID=1 zombie processes

// 10. Terminate Monitor MCP tasks
if (feature('MONITOR_TOOL')) {
 mcpMod.killMonitorMcpTasksForAgent(agentId, ...)
}
// → No cleanup: Monitoring process leaks
}
```

**Ingenuity 8: The finally block ensures cleanup**

All cleanup is done in the `finally` block, ensuring that cleanup will be executed even if the Agent is interrupted (AbortError),
throws an exception, or completes normally. This is a prime example of defensive programming.

---
## 5.10 Asynchronous Agent Lifecycle

### 5.10.1 runAsyncAgentLifecycle — Comprehensive Background Management

```
spawn
 │
 ├── Create ProgressTracker
 ├── Optional: Start AgentSummarization (generate summaries periodically)
 │
 ▼
Execute loop
 │ for await (const message of makeStream()) {
 │ ├── Append to agentMessages
 │ ├── If UI holds tasks → Append in real-time to AppState
 │ ├── updateProgressFromMessage() → Update progress
 │ └── emitTaskProgress() → SDK progress event
 │ }
 │
 ├── Success path:
 │ ├── stopSummarization()
 │ ├── finalizeAgentTool() → Extract results
 │ ├── completeAsyncAgent() → Mark as completed (first!)
 │ ├── classifyHandoffIfNeeded() → Security check (later)
 │ ├── getWorktreeResult() → Worktree cleanup (later)
 │ └── enqueueAgentNotification() → Notification
 │
 ├── AbortError path (user kill):
 │ ├── killAsyncAgent() → Mark as killed
 │ ├── extractPartialResult() → Extract partial results
 │ └── enqueueAgentNotification(status: 'killed')
 │
 └── Other error paths:
 ├── failAsyncAgent() → Mark as failed
 └── enqueueAgentNotification(status: 'failed')
```

### 5.10.2 Handoff Security Classification

In auto mode, after the sub-Agent completes, a security review is required:

```typescript
export async function classifyHandoffIfNeeded({
 agentMessages, tools, toolPermissionContext, abortSignal, subagentType,
}) {
 if (toolPermissionContext.mode !== 'auto') return null

 // Build the classifier transcript
 const agentTranscript = buildTranscriptForClassifier(agentMessages, tools)

 // Call the YOLO classifier
 const classifierResult = await classifyYoloAction(
 agentMessages,
 {
 role: 'user',
 content: [{
 type: 'text',
 text: "Sub-agent has finished and is handing back control to the main agent. "
 + "Review the sub-agent's work based on the block rules and let the main "
 + "agent know if any file is dangerous.",
 }],
 },
 tools,
 toolPermissionContext,
 abortSignal,
 )

 if (classifierResult.shouldBlock) {
 if (classifierResult.unavailable) {
 // Classifier unavailable → Allow but with warning
 return `Note: The safety classifier was unavailable. Please carefully verify.`
 }
 // Classifier flagged as dangerous → With security warning
 return `SECURITY WARNING: This sub-agent performed actions that may violate "
 + "security policy. Reason: ${classifierResult.reason}.`
 }
 return null
}
```

**Design Points**:
- Only run in auto mode (other modes users have explicitly chosen trust levels)
- Do not block when the classifier is unavailable, but with a warning
- Warning injected into the results, allowing the main Agent to see and decide how to handle

---
## 5.11 Transcript Recording — Incremental Append

The execution process of the Agent is recorded into the sidechain transcript:

```typescript
// Record initial messages
void recordSidechainTranscript(initialMessages, agentId)

// Record each new message (incremental append, O(1))
let lastRecordedUuid: UUID | null = initialMessages.at(-1)?.uuid ?? null

for await (const message of query({ ... })) {
 if (isRecordableMessage(message)) {
 // Only record new messages, with parent UUID to maintain chain relationships
 await recordSidechainTranscript(
 [message],
 agentId,
 lastRecordedUuid, // parent chain
 )
 if (message.type !== 'progress') {
 lastRecordedUuid = message.uuid
 }
 }
}
```

**Ingenuity 10: progress messages do not update lastRecordedUuid**

Progress messages are temporary (like real-time output of Bash) and should not be part of the chain.
If progress updates lastRecordedUuid, subsequent assistant messages will take progress as the parent,
leading to incorrect chain relationships during resume.

### 5.11.1 API Metrics Forwarding

```typescript
// Forward API request metrics from sub-Agents to the parent
if (
 message.type === 'stream_event' &&
 message.event.type === 'message_start' &&
 message.ttftMs != null
) {
 toolUseContext.pushApiMetricsEntry?.(message.ttftMs)
 continue // Do not yield to the caller
}
```

This allows the parent's TTFT/OTPS display to be updated during the execution of the sub-Agent,
allowing users to see real-time performance metrics.

---
## 5.12 Cache Eviction Hint — Proactive Cache Release

```typescript
// In finalizeAgentTool:
const lastRequestId = lastAssistantMessage.requestId
if (lastRequestId) {
 logEvent('tengu_cache_eviction_hint', {
 scope: 'subagent_end',
 last_request_id: lastRequestId,
 })
}
```

**Ingenuity 11: Proactively tell the inference server to release cache**

After the sub-Agent ends, its prompt cache chain is no longer needed.
By sending an eviction hint, the inference server can proactively release this cache,
making room for other requests.

This is important in large-scale deployments — each sub-Agent may occupy 5-50MB of cache space,
a long session may generate dozens of sub-Agents.

---
## 5.13 localDenialTracking — Asynchronous Agent's Permission Denial Count

```typescript
// In createSubagentContext:
localDenialTracking: overrides?.shareSetAppState
 ? parentContext.localDenialTracking
 : createDenialTrackingState(),
```

**Why do asynchronous Agents need local denial tracking?**

Asynchronous Agent's `setAppState` is a no-op, so the global denial counter will not update.
Without local tracking, the Agent can retry denied operations indefinitely.

Local tracking ensures that even if setAppState is a no-op, the denial counter still accumulates locally,
and the Agent will stop retrying after reaching the threshold.

---
## 5.14 Coordinator Mode — Separation of Orchestrator and Executor

The Coordinator mode is an advanced form of multi-Agent systems. It transforms Claude Code from "one Agent does everything"
into an architecture of "one orchestrator + multiple Workers".

### 5.14.1 Mode Switching

```typescript
// coordinatorMode.ts
export function isCoordinatorMode(): boolean {
  if (feature('COORDINATOR_MODE')) {
    return isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)
  }
  return false
}
```

Enabled by the environment variable `CLAUDE_CODE_COORDINATOR_MODE=1`. Note that it is **read at runtime**
(without caching), which allows `matchSessionMode()` to dynamically switch modes when resuming sessions.

### 5.14.2 Session Mode Matching

```typescript
// When resuming a session, if the current mode does not match the mode stored in the session, switch modes automatically
export function matchSessionMode(
  sessionMode: 'coordinator' | 'normal' | undefined,
): string | undefined {
  const currentIsCoordinator = isCoordinatorMode()
  const sessionIsCoordinator = sessionMode === 'coordinator'

  if (currentIsCoordinator === sessionIsCoordinator) return undefined

  // Directly flip the environment variable
  if (sessionIsCoordinator) {
    process.env.CLAUDE_CODE_COORDINATOR_MODE = '1'
  } else {
    delete process.env.CLAUDE_CODE_COORDINATOR_MODE
  }
  return sessionIsCoordinator
    ? 'Entered coordinator mode to match resumed session.'
    : 'Exited coordinator mode to match resumed session.'
}
```

This solves a practical problem: users create sessions in coordinator mode, close them, and then resume in normal mode,
Worker's task-notification will not be processed correctly.

### 5.14.3 Coordinator's System Prompt Design

The Coordinator's system prompt is about ~250 lines long, defining the complete workflow:

```
Coordinator's role definition:
  1. You are the orchestrator, not the executor
  2. Answer questions you can answer directly, do not delegate simple tasks
  3. Worker results arrive in <task-notification> XML
  4. Never forge or predict Agent results

Available tools:
  - Agent (spawn worker)
  - SendMessage (continue worker)
  - TaskStop (stop worker)
  - subscribe/unsubscribe_pr_activity

Task workflow (4 stages):
  1. Research → Workers investigate in parallel
  2. Synthesis → Coordinator understands and synthesizes
  3. Implementation → Workers execute
  4. Verification → Workers verify
```

**Ingenuity 12: The Synthesis phase is the Coordinator's most important responsibility**

```
// Anti-pattern example in the system prompt:
// Bad: "Based on your findings, fix the auth bug"  ← Lazy delegation
// Good: "Fix the null pointer in src/auth/validate.ts:42.
//      The user field on Session is undefined when sessions expire
//      but the token remains cached. Add a null check before user.id
//      access — if null, return 401 with 'Session expired'."
```

The Coordinator must **understand** the research results of the Workers, and then write specific implementation specifications.
This is not a simple forwarding, but a real synthesis and analysis.

### 5.14.4 Worker Context Injection

```typescript
export function getCoordinatorUserContext(
  mcpClients: ReadonlyArray<{ name: string }>,
  scratchpadDir?: string,
): { [k: string]: string } {
  if (!isCoordinatorMode()) return {}

  // Tell the Coordinator which tools its Workers have
  const workerTools = Array.from(ASYNC_AGENT_ALLOWED_TOOLS)
    .filter(name => !INTERNAL_WORKER_TOOLS.has(name))
    .sort()
    .join(', ')

  let content = `Workers spawned via the ${AGENT_TOOL_NAME} tool have access to these tools: ${workerTools}`

  // MCP server information
  if (mcpClients.length > 0) {
    const serverNames = mcpClients.map(c => c.name).join(', ')
    content += `\n\nWorkers also have access to MCP tools from connected MCP servers: ${serverNames}`
  }

  // Scratchpad directory (cross-Worker shared knowledge)
  if (scratchpadDir && isScratchpadGateEnabled()) {
    content += `\n\nScratchpad directory: ${scratchpadDir}
Workers can read and write here without permission prompts.
Use this for durable cross-worker knowledge.`
  }

  return { workerToolsContext: content }
}
```

**Ingenuity 13: Scratchpad Directory**

Scratchpad is a key infrastructure of the Coordinator mode:
- Workers cannot communicate directly with each other
- But they can share files through the Scratchpad directory
- No permission prompts are needed (pre-authorized)
- The Coordinator can guide Workers to store intermediate results here

### 5.14.5 Continue vs Spawn Decision

The Coordinator's system prompt contains a carefully designed decision matrix:
```
| Scenario                           | Mechanism      | Reason                           |
|-----------------------------------|---------------|---------------------------------|
| The file being studied is exactly the one to be edited | Continue     | Worker already has file context    |
| The research scope is broad but the implementation scope is narrow | Spawn fresh  | To avoid dragging in exploration noise |
| Correcting failures or extending recent work | Continue     | Worker has error context           |
| Verifying code written by another Worker | Spawn fresh  | The verifier should use a fresh perspective |
| The first implementation used the wrong method | Spawn fresh  | The context of the wrong method will anchor retries |
| A completely unrelated task            | Spawn fresh  | No reusable context                |

```

Core principle: **High context overlap → Continue, low → Spawn fresh**.

### 5.14.6 Detailed Explanation of Worker Communication Mechanisms

Workers cannot communicate directly with each other; all communication is relayed through the Coordinator. Understanding the differences between these three communication mechanisms is crucial:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Comparison of Three Worker Communication Mechanisms                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│ 1. task-notification (asynchronous message)                                        │
│    Direction: Worker → Coordinator                                          │
│    Trigger: Worker completes/fails/is killed                                     │
│    Format: <task-notification>                                             │
│          <task_id>worker-123</task_id>                                   │
│          <status>completed</status>                                      │
│          <result>Fixed 3 files...</result>                              │
│          </task-notification>                                            │
│    Sequence: Arrives asynchronously through a message queue, Coordinator consumes during the next iteration │
│    Loss handling: Will not be lost, the queue is persisted in memory         │
│                                                                         │
│ 2. SendMessage (proactive message)                                               │
│    Direction: Coordinator → Worker                                          │
│    Trigger: Coordinator actively sends instructions or additional context      │
│    Purpose: To continue the work of an existing Worker (instead of spawning new) │
│    Sequence: Synchronously injected into the Worker's message queue, Worker sees during the next iteration │
│    Limitation: Can only be sent to Workers that are still alive                │
│                                                                         │
│ 3. Scratchpad file (shared storage)                                        │
│    Direction: Worker ↔ Worker (through the file system)                      │
│    Trigger: One Worker writes a file, another Worker reads it                  │
│    Purpose: To share research results, intermediate data, configuration files  │
│    Sequence: No guarantee! After Worker A writes, Worker B may not see it immediately │
│    Limitation: No locking mechanism, Coordinator needs to coordinate read-write order │
│                                                                         │
│ Practical principles:                                                              │
│ - Control flow uses task-notification + SendMessage                              │
│ - Data flow uses Scratchpad                                                   │
│ - Coordinator is responsible for coordinating sequence, not relying on Workers to synchronize themselves │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 5.14.7 Complete Orchestration Case: Fixing Authentication Bugs Across 3 Files

The following is an end-to-end Coordinator orchestration trace, demonstrating the actual task decomposition, assignment, and merging process:

```
User input: "After logging in, the session expires but the token is still cached, causing a null pointer crash"

═══ Phase 1: Research (Parallel Research) ═══

Coordinator thinks:
  "This involves session management, token caching, and authentication verification in three areas.
   I need to understand the code structure first, then formulate a repair plan."

T+0s     Coordinator calls Agent("Study the code structure of session lifecycle management")
         → spawn Worker-A (async)

T+0.1s   Coordinator calls Agent("Study the token caching mechanism and expiration handling logic")
         → spawn Worker-B (async)

T+0.1s   Coordinator calls Agent("Study the null value handling of the user field in authentication verification")
         → spawn Worker-C (async)

          Three Workers execute in parallel, Coordinator waits...

T+15s    <task-notification> Worker-A completes
         Result: "Session is defined in src/auth/session.ts,
                expire() method clears the session but does not clear tokenCache,
                user field of Session type becomes undefined after expiration"

T+20s    <task-notification> Worker-B completes
         Result: "tokenCache is in src/auth/tokenCache.ts,
                uses sessionId as key,
                does not listen to session expiration events,
                tokens of expired sessions will remain in the cache"

T+22s    <task-notification> Worker-C completes
         Result: "src/auth/validate.ts:42 line directly accesses user.id,
                no null check,
                when the session expires but the token is still cached,
                validate() uses the cached token to find the expired session,
                session.user is undefined → crash"

═══ Phase 2: Synthesis (Comprehensive Analysis) ═══

Coordinator thinks (this is the most critical phase):
  "Based on the research of the three Workers, I now understand the complete bug chain:
   1. session.expire() clears the session but does not notify tokenCache
   2. tokenCache retains the token of the expired session
   3. validate() uses the cached token to find the expired session, accessing user.id crashes

   Repair plan:
   - src/auth/session.ts: emit an event when expire()
   - src/auth/tokenCache.ts: listen for expiration events, clear corresponding tokens
   - src/auth/validate.ts: add null check as defensive programming"

═══ Phase 3: Implementation (Parallel Implementation) ═══

Decision: The file studied by Worker-C is exactly the one to be edited → Continue Worker-C
         The repair scope for the other two files is narrow → Spawn fresh

T+25s    Coordinator calls SendMessage(Worker-C,
           "Add null check in src/auth/validate.ts:42:
            if (!session?.user) return { status: 401, error: 'Session expired' }")
         → Continue Worker-C

T+25.1s  Coordinator calls Agent(
           "In src/auth/session.ts's expire() method,
            add this.emit('expired', this.id),
            and in src/auth/tokenCache.ts listen for this event,
            when received, call cache.delete(sessionId)")
         → spawn Worker-D (async)

T+35s    <task-notification> Worker-C completes (added null check)
T+40s    <task-notification> Worker-D completes (added event mechanism)

═══ Phase 4: Verification (Independent Verification) ═══

Decision: The verifier should use a fresh perspective → Spawn fresh

T+42s    Coordinator calls Agent(
           "Verify if the following repairs are complete:
            1. Run existing tests npm test
            2. Check if session.expire() correctly emits events
            3. Check if tokenCache correctly listens and clears
            4. Check if validate.ts's null check covers all paths")
         → spawn Worker-E (async, fresh perspective)

T+60s    <task-notification> Worker-E completes
         Result: "All tests pass, repairs are complete"

Coordinator replies to the user: "Fixed. The root cause is that the token cache is not cleared when the session expires..."

Total: 5 Workers, ~60 seconds, serial execution would take ~3 minutes
```

**Key decision points in the case**:

| Decision | Choice | Reason |
|------|------|------|
| How many Workers to use in the research phase? | 3 | Each focuses on one area to avoid context pollution |
| Worker-C Continue vs Spawn? | Continue | It has already read validate.ts, high context overlap |
| Why not continue with Worker-A for Worker-D? | Spawn fresh | The research scope is broad but the implementation scope is narrow, to avoid exploration noise |
| Why use a new Worker for verification? | Spawn fresh | The verifier should use a fresh perspective, not influenced by the implementer's thinking |
| What did the Coordinator do itself? | Synthesis | Understand the bug chain, formulate specific repair specifications, not lazy forwarding |

---
## Summary of This Chapter: Engineering Ingenuity Checklist

| # | Ingenuity | Problem Solved |
|---|------|------------|
| 1 | setAppStateForTasks always shared | Prevent background tasks from becoming zombie processes |
| 2 | contentReplacementState cloning | Ensure Fork's prompt cache hits |
| 3 | Metadata in tool specifications | Fine-grained control over sub-Agent tool permissions |
| 4 | querySource persistence | Prevent recursive fork guards from becoming invalid after autocompact |
| 5 | Scalable token savings | Omit CLAUDE.md to save 5-15 Gtok per week |
| 6 | Plugin-only trust levels | Admin Agents can use MCP, user Agents cannot |
| 7 | Three-level name resolution | Let Agent authors reference skills with short names |
| 8 | finally block cleanup | Ensure resources are cleaned up on all exit paths |
| 9 | complete before classify | Prevent slow operations from causing deadlocks |
| 10 | progress does not update parent chain | Prevent chain relationship errors during resume |
| 11 | Cache eviction hint | Proactively release sub-Agent's cache space |
| 12 | Synthesis phase | Coordinator must understand before delegating, cannot lazy forward |
| 13 | Scratchpad directory | Share knowledge across Workers without permission prompts |

---
## 5.15 Migratable Design Patterns — Take These, Use in Your Own Agent System

### Pattern 1: Capability-based Security (Capability-based Security Model)

**In Claude Code**: `createSubagentContext` — Default isolation of all states, caller must explicitly opt-in to share.

**General form**:
```
When creating a sub-component:
  Default: All capabilities = none (or no-op)
  Explicit authorization: Only give the sub-component the minimum set of capabilities it needs
```

**Applicable scenarios**:
- Browser sandboxes (iframes default to no permissions, explicitly authorized through `allow="camera"`)
- Docker containers (default to no network/file system access, authorized through `--cap-add`)
- Microservices' API Gateways (default to rejecting all requests, whitelisted to pass)

**Key constraints**: Some capabilities must always be shared (e.g., `setAppStateForTasks`),
otherwise, the side effects of the sub-component cannot be tracked by the parent, leading to resource leaks.

### Pattern 2: Cache-Friendly Forking (Cache-Friendly Forking)

**In Claude Code**: Forking a sub-Agent precisely copies all cache key parameters from the parent, ensuring prompt cache hits.

**General form**:
```
When creating a subprocess/subtask:
  Ensure the input prefix of the subtask is identical to the parent task
  → Share cache, only pay for the incremental part
```

**Applicable scenarios**:
- Linux's fork() + CoW (subprocesses share the parent's memory pages, copying only on write)
- CDN's tiered caching (edge nodes share the source station cache prefix)
- Git's packfile (new branches share base objects, only storing increments)

**Key constraints**: All parameters affecting the cache key must be precisely copied,
including less obvious parameters (e.g., `contentReplacementState`). A byte difference will cause a cache miss.

### Pattern 3: Deterministic Cleanup (Deterministic Cleanup)

**In Claude Code**: 12-step cleanup process in the `finally` block, ensuring resources are cleaned up on all exit paths.

**General form**:
```
try {
  resource = acquire()
  // ... use resource ...
} finally {
  // Each resource has a corresponding cleanup, and the cleanup order is the reverse of the acquisition order
  cleanup_N()  // The last acquired is cleaned up first
  cleanup_2()
  cleanup_1()
}
```

**Applicable scenarios**:
- Database connection pool (connections must be returned, otherwise the pool is exhausted)
- File handles (must be closed, otherwise there is fd leakage)
- Kubernetes Pod (must clean up sidecar, otherwise Pod is stuck in Terminating)

**Key Constraints**: Cleanup must be **idempotent** (the same result after multiple executions),
because it may be called repeatedly in exceptional scenarios.

### Pattern 4: Star Topology Orchestration

**In Claude Code**: Coordinator pattern — one orchestrator + multiple Workers, Workers do not communicate directly with each other.

**General Form**:
```
         Coordinator
        /    |    \
Worker-A  Worker-B  Worker-C
(No direct communication, relayed through Coordinator)
```

**Applicable Scenarios**:
- MapReduce (Master distributes tasks, Workers execute independently, Master merges results)
- Microservices' Saga pattern (Orchestrator coordinates transactions across multiple services)
- CI/CD's Pipeline (Controller schedules multiple Stages, Stages share data through artifacts)

**Key Constraints**: The Coordinator must perform **Synthesis** (comprehensive analysis), not just forwarding.
If the Coordinator merely splits user requests and forwards them directly to Workers,
then it is a worthless middle layer. The real value lies in:
- Conducting research and synthesizing analysis to formulate specific implementation specifications
- Deciding Continue vs Spawn based on the degree of context overlap
- Using a fresh perspective during verification to avoid confirmation bias

### Pattern 5: Monotonic Permission Narrowing

**In Claude Code**: Sub-agents can narrow permissions but cannot relax the permissions of the parent.

**General Form**:
```
Parent permissions = {read, write, execute}
Child permissions ⊆ Parent permissions  (Can only be a subset, not a superset)
```

**Applicable Scenarios**:
- Unix's setuid/setgid (child processes cannot obtain permissions that the parent process does not have)
- OAuth's scope inheritance (the scope of a refresh token cannot exceed the original token)
- AWS IAM's permission boundary (permissions of a child role cannot exceed the boundary)

**Key Constraints**: Looser parent modes (such as `bypassPermissions`) cannot be tightened by child levels,
because the user has explicitly chosen the level of trust. This is a subtle but important design decision.

---

### Next Chapter Preview
Chapter 6 will delve into **MCP Protocol and Extended Systems** — understanding how Claude Code expands capabilities through MCP and Skills.