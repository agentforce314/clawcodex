# Chapter 4b: Context Management — Recall, Compression & Progressive Disclosure

> **Core source files:** `context.ts`, `compact.ts`, `autoCompact.ts`, `microCompact.ts`, `snipCompact.ts`, `contextCollapse/`, `attachments.ts`, `findRelevantMemories.ts`, `toolSearch.ts`
>
> **Reading time:** 40 minutes

---

## Learning Objectives

1. Understand Claude Code's **three-phase context lifecycle**: Injection → Management → Compression
2. Master the end-to-end flow and design tradeoffs of the 5-layer compression pipeline
3. Understand how **progressive disclosure** maximizes information density within token budgets
4. Understand the async Memory Prefetch recall mechanism

---

## 4b.1 Context Lifecycle Overview

**Building intuition first**: AI models have a limited "memory window" (context window) — they can only remember so much. Claude Code needs to pack the most valuable information into this limited window. The entire context management has three phases: **inject** necessary background at the start (project rules, git status, etc.), **progressively disclose** during runtime (only load what's needed), and **compress** old content when the conversation grows long (from cheap to expensive, layer by layer).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Context Lifecycle                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Phase 1: Injection (session start)                                     │
│  ├── getUserContext()  → CLAUDE.md + current date                       │
│  ├── getSystemContext() → git status + branch + recent commits          │
│  ├── Skills listing → frontmatter summaries of available skills         │
│  └── Memory prefetch → async pre-fetch of relevant memory files         │
│                                                                         │
│  Phase 2: Progressive Disclosure (runtime)                              │
│  ├── ToolSearch → lazy-load tool schemas                                │
│  ├── Relevant Memory → on-demand memory recall                          │
│  └── Dynamic Skills → on-demand skill content injection                 │
│                                                                         │
│  Phase 3: Compression (when context grows)                              │
│  ├── toolResultBudget → persist large results to disk                   │
│  ├── snipCompact → trim old tool results                                │
│  ├── microcompact → compress intermediate tool calls                    │
│  ├── contextCollapse → fold completed subtasks                          │
│  └── autocompact → full LLM summarization                              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4b.2 Phase 1: Context Injection

### 4b.2.1 context.ts — Session-level Context

**Building intuition first**: Every time you open Claude Code and start a new session, the system automatically collects two types of "background information" to inject into the AI: your project rules (CLAUDE.md files) and the current git status. This information is collected only once at session start (cached to avoid redundant computation) — like checking your calendar once when you arrive at work, not every minute.

At the start of each session, Claude Code collects two types of context, both cached with `memoize` (computed only once per session):

```typescript
// src/context.ts
export const getUserContext = memoize(async () => {
  // 1. Read CLAUDE.md files (project-level + user-level + .claude/rules/*.md)
  const claudeMd = getClaudeMds(filterInjectedMemoryFiles(await getMemoryFiles()));

  // 2. Current date
  const currentDate = `Today's date is ${getLocalISODate()}.`;

  return { claudeMd, currentDate };
});

export const getSystemContext = memoize(async () => {
  // Git status (branch, status, recent commits)
  // Note: skipped in CCR mode and when git instructions are disabled
  const gitStatus = isEnvTruthy(process.env.CLAUDE_CODE_REMOTE)
    || !shouldIncludeGitInstructions()
    ? null
    : await getGitStatus();

  return { ...(gitStatus && { gitStatus }) };
});
```

**Key Design Decisions:**

| Decision | Reason |
|----------|--------|
| `memoize` caching | Computed once per session, avoids repeated I/O |
| git status is a snapshot | Comment explicitly says "this status is a snapshot in time" |
| Cache cleared after compact | `runPostCompactCleanup()` calls `getUserContext.cache.clear?.()` |
| CLAUDE.md supports `@import` | Can reference external files, supports modular configuration |

### 4b.2.2 CLAUDE.md Multi-level Structure

```
CLAUDE.md search paths (highest to lowest priority):
├── ~/.claude/CLAUDE.md              (user-level - global preferences)
├── <project>/.claude/CLAUDE.md      (project-level - project rules)
├── <project>/CLAUDE.md              (project-level - legacy format)
├── <project>/.claude/rules/*.md     (project-level - modular rules)
└── --add-dir specified directories   (explicitly appended)
```

**Clever Design: `--bare` mode semantics**

```typescript
// --bare means "skip what I didn't ask for", not "ignore what I asked for"
const shouldDisableClaudeMd =
  isEnvTruthy(process.env.CLAUDE_CODE_DISABLE_CLAUDE_MDS) ||
  (isBareMode() && getAdditionalDirectoriesForClaudeMd().length === 0);
```

`--bare` skips auto-discovery (cwd directory walk), but still honors `--add-dir` explicitly specified directories.

### 4b.2.3 git status Careful Truncation

```typescript
// src/context.ts - getGitStatus()
const MAX_STATUS_CHARS = 2000;

const status = await execFileNoThrow(gitExe, ['status', '--short']);
const truncatedStatus = status.stdout.length > MAX_STATUS_CHARS
  ? status.stdout.slice(0, MAX_STATUS_CHARS) + '\n... (truncated)'
  : status.stdout;

return [
  `This is the git status at the start of the conversation.`,
  `Note that this status is a snapshot in time, and will not update.`,
  `Current branch: ${branch}`,
  `Main branch: ${mainBranch}`,
  `Status:\n${truncatedStatus || '(clean)'}`,
  `Recent commits:\n${log}`,
].join('\n\n');
```

---

## 4b.3 Phase 2: Progressive Disclosure

The core idea of progressive disclosure: **Don't stuff all information into context at once — load on demand.**

### 4b.3.1 ToolSearch — Lazy Loading Tool Schemas

When there are many tools (40+ built-in plus MCP tools), sending all schemas consumes massive tokens.
The ToolSearch mechanism implements lazy loading:

```
Initial request tool list sent to API:
  tools: [
    Bash          (full schema ~500 tokens),
    Read          (full schema ~300 tokens),
    ToolSearch    (full schema ~200 tokens),     ← search entry point
    mcp__db__query  (defer_loading: true),       ← name only, ~5 tokens
    mcp__api__fetch (defer_loading: true),       ← name only, ~5 tokens
    ... 50+ more deferred tools ...
  ]

  Savings: 50 tools × ~400 tokens/tool = ~20,000 tokens saved
```

**Deferral logic:**

```typescript
// src/tools/ToolSearchTool/prompt.ts
export function isDeferredTool(tool: Tool): boolean {
  // 1. Explicit opt-out: alwaysLoad = true tools are never deferred
  if (tool.alwaysLoad === true) return false;

  // 2. MCP tools are always deferred (workflow-specific, users may have dozens)
  if (tool.isMcp === true) return true;

  // 3. ToolSearch itself cannot be deferred (model needs it to load others)
  if (tool.name === TOOL_SEARCH_TOOL_NAME) return false;

  // 4. Agent tool not deferred in fork mode (needs to be available turn 1)
  if (feature('FORK_SUBAGENT') && tool.name === AGENT_TOOL_NAME) {
    if (isForkSubagentEnabled()) return false;
  }

  // 5. Other tools marked with shouldDefer
  return tool.shouldDefer === true;
}
```

### 4b.3.2 Memory Prefetch — Async Memory Recall

Claude Code has an elegant memory recall system that asynchronously pre-fetches relevant memories at the start of each user turn:

```typescript
// src/query.ts — fired once per user turn
using pendingMemoryPrefetch = startRelevantMemoryPrefetch(
  state.messages,
  state.toolUseContext,
);
```

**Complete recall flow:**

```
User input: "Fix the database connection timeout issue"
         │
         ▼
┌─────────────────────────────────────────────────┐
│ startRelevantMemoryPrefetch()                   │
│ ├── Extract last user message text              │
│ ├── Filter: single-word queries too short, skip │
│ ├── Check: total recalled bytes < budget        │
│ └── Start async sideQuery (non-blocking)        │
└─────────────────────────────────────────────────┘
         │ (async, parallel with main model streaming)
         ▼
┌─────────────────────────────────────────────────┐
│ findRelevantMemories()                          │
│ ├── scanMemoryFiles() → scan ~/.claude/memory/  │
│ │   └── Read each .md frontmatter (first 30 lines) │
│ ├── formatMemoryManifest() → build file manifest│
│ └── selectRelevantMemories() → Sonnet selects   │
│     ├── Input: user query + manifest + recent tools │
│     ├── Output: up to 5 most relevant filenames │
│     └── Rule: don't select reference docs for   │
│            recently-used tools (but DO select    │
│            warnings/gotchas)                     │
└─────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│ Consumed at collect point after tool execution  │
│ ├── settledAt !== null → completed, inject      │
│ └── settledAt === null → not done, retry next   │
│     (prefetch never blocks current turn)        │
└─────────────────────────────────────────────────┘
```

**Key Design: Disposable Pattern**

```typescript
// MemoryPrefetch implements Symbol.dispose
// query.ts binds with `using`, so on all generator exit paths
// (return, throw, .return() closure) it auto-aborts and logs telemetry
export type MemoryPrefetch = {
  promise: Promise<Attachment[]>;
  settledAt: number | null;      // Set when promise settles
  consumedOnIteration: number;   // Set at collect point
  [Symbol.dispose](): void;      // Auto-cleanup
};
```

### 4b.3.3 Skills Progressive Injection

The Skills system also uses progressive disclosure:

```
Initial injection: skill_listing attachment
  ├── Contains only frontmatter summaries (name + description)
  ├── Each skill ~50 tokens (vs full content ~2000 tokens)
  └── Model sees listing, loads full content via SkillTool on demand

On-demand loading: dynamic_skill attachment
  ├── Model calls SkillTool({ skill: "deploy-to-prod" })
  ├── Full skill content injected as attachment
  └── Available in subsequent turns
```

---

## 4b.4 Phase 3: 5-Layer Compression Pipeline

When conversations grow long, context expands. Claude Code has 5 compression layers, ordered from cheapest to most expensive:

```
Execution order and cost:
toolResultBudget → snip → microcompact → collapse → autocompact
     Cheap         Cheap    Moderate      Moderate    Expensive
     Local         Local    Local/API     Local       API call
     O(n)          O(n)     O(n)          O(n)        O($$)
```

### 4b.4.1 Layer 1: applyToolResultBudget — Large Results to Disk

```typescript
// When a single tool_result exceeds budget, persist to disk, replace with reference
// Runs BEFORE microcompact (MC operates by tool_use_id, never inspects content)
messagesForQuery = await applyToolResultBudget(
  messagesForQuery,
  toolUseContext.contentReplacementState,
  persistReplacements ? records => void recordContentReplacement(...) : undefined,
  new Set(tools.filter(t => !Number.isFinite(t.maxResultSizeChars)).map(t => t.name)),
);
```

### 4b.4.2 Layer 2: snipCompact — Trim Old Results

```typescript
// feature: HISTORY_SNIP
// Preserves message structure (tool_use + tool_result pairs), but clears old content
// Returns tokensFreed for autocompact reference
const snipResult = snipModule.snipCompactIfNeeded(messagesForQuery);
messagesForQuery = snipResult.messages;
snipTokensFreed = snipResult.tokensFreed;
```

### 4b.4.3 Layer 3: microcompact — Compress Intermediate Tool Calls

microcompact has two modes:

**Time-based trigger:**
```typescript
// If time since last assistant message exceeds threshold (server cache expired),
// clear old tool results entirely (cache is cold, prefix will be rewritten anyway)
const timeBasedResult = maybeTimeBasedMicrocompact(messages, querySource);
```

**Cached editing mode (Cached MC):**
```typescript
// Uses API's cache_edits to delete tool results without invalidating cached prefix
// Does NOT modify local message content — adds cache_reference and cache_edits at API layer
const cacheEdits = mod.createCacheEditsBlock(state, toolsToDelete);
```

### 4b.4.4 Layer 4: contextCollapse — Subtask Folding

The most elegant design — **read-time projection** rather than write-time modification:

```
REPL message array (full history, never modified):
  [msg1, msg2, msg3, msg4, msg5, msg6, msg7, msg8]

Collapse Store (summary storage):
  commit1: { archived: [msg1, msg2, msg3], summary: "..." }
  commit2: { archived: [msg4, msg5], summary: "..." }

projectView() output (recalculated every iteration):
  [summary1, summary2, msg6, msg7, msg8]

Advantages:
  ✓ Original history fully preserved (traceable)
  ✓ Summaries persist across turns
  ✓ Different views can have different folding strategies
```

**Why does collapse run before autocompact?**

```typescript
// Source comment:
// Runs BEFORE autocompact so that if collapse gets us under the
// autocompact threshold, autocompact is a no-op and we keep granular
// context instead of a single summary.
```

### 4b.4.5 Layer 5: autocompact — Full LLM Summarization

Last resort — calls Claude to generate a summary of the entire conversation:

```typescript
// src/services/compact/autoCompact.ts
export async function autoCompactIfNeeded(messages, toolUseContext, ...) {
  // 1. Environment variable disable check
  if (isEnvTruthy(process.env.DISABLE_COMPACT)) return { wasCompacted: false };

  // 2. Circuit breaker: stop retrying after N consecutive failures
  if (tracking?.consecutiveFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES) {
    return { wasCompacted: false };
  }

  // 3. Threshold check
  const shouldCompact = await shouldAutoCompact(messages, model, querySource, snipTokensFreed);
  if (!shouldCompact) return { wasCompacted: false };

  // 4. Try Session Memory compaction first (experimental)
  const sessionMemoryResult = await trySessionMemoryCompaction(...);

  // 5. Call Claude to generate summary
  const compactionResult = await compactConversation(messages, ...);

  // 6. Cleanup: reset microcompact state, context collapse, CLAUDE.md cache, etc.
  runPostCompactCleanup(querySource);

  return { wasCompacted: true, compactionResult };
}
```

**Circuit Breaker Design:**

```typescript
// Without circuit breaker, sessions where context is irrecoverably over limit
// would hammer the API with doomed compaction attempts on every turn
if (nextFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES) {
  logForDebugging(
    `autocompact: circuit breaker tripped after ${nextFailures} consecutive failures`
  );
}
```

### 4b.4.6 Reactive Compact — Passive Compression

Beyond proactive autocompact, there's reactive compact triggered when the API returns 413 (prompt too long):

```typescript
// src/query.ts — 413 error handling
if ((isWithheld413 || isWithheldMedia) && reactiveCompact) {
  const compacted = await reactiveCompact.tryReactiveCompact({
    hasAttempted: hasAttemptedReactiveCompact,  // Prevent infinite loop
    querySource,
    aborted: toolUseContext.abortController.signal.aborted,
    messages: messagesForQuery,
    cacheSafeParams: { ... },
  });

  if (compacted) {
    state = { ...state, messages: postCompactMessages, hasAttemptedReactiveCompact: true };
    continue;
  }
  // Compression failed — error out (don't enter stop hooks, prevent death spiral)
}
```

---

## 4b.5 Post-Compact Cleanup

After compaction, numerous states need resetting — an error-prone step:

```typescript
// src/services/compact/postCompactCleanup.ts
export function runPostCompactCleanup(querySource?: QuerySource): void {
  const isMainThreadCompact = querySource === undefined
    || querySource.startsWith('repl_main_thread')
    || querySource === 'sdk';

  resetMicrocompactState();

  if (feature('CONTEXT_COLLAPSE') && isMainThreadCompact) {
    resetContextCollapse();
  }

  if (isMainThreadCompact) {
    getUserContext.cache.clear?.();
    resetGetMemoryFilesCache('compact');
  }

  clearSystemPromptSections();
  clearClassifierApprovals();

  // Intentionally NOT resetting sentSkillNames:
  // Re-injecting full skill_listing (~4K tokens) post-compact is pure cache_creation
}
```

---

## 4b.6 Design Pattern Summary

### 5 Transferable Context Management Patterns

| # | Pattern | Claude Code Implementation | General Application |
|---|---------|--------------------------|-------------------|
| 1 | **Snapshot Injection** | `memoize` + collect at session start | Any system needing initial context |
| 2 | **Progressive Disclosure** | ToolSearch + Memory Prefetch | Large tool sets, knowledge bases |
| 3 | **Layered Compression** | 5-layer pipeline, cheapest first | Any token-limited LLM application |
| 4 | **Read-time Projection** | Context Collapse's projectView | Systems needing full history preservation |
| 5 | **Circuit Breaker** | autocompact stops after consecutive failures | Any automatable operation that can fail |

### Relationship to Chapter 4

This chapter is an **extension and supplement** to Chapter 4 Section 4.3 "Multi-layer Compression Strategy":
- Chapter 4 focuses on **where and when** the compression pipeline executes in the query loop
- This chapter focuses on the **complete lifecycle** of context: from injection to recall to compression
- Adds ToolSearch progressive disclosure, Memory Prefetch async recall, and other topics not covered in Chapter 4

---

## Chapter Summary

| Concept | Key File | One-line Summary |
|---------|----------|-----------------|
| Session context | `context.ts` | memoize-cached CLAUDE.md + git status snapshot |
| Memory recall | `findRelevantMemories.ts` | Sonnet selects top 5 most relevant memory files |
| Async prefetch | `attachments.ts` | Disposable pattern, never blocks main flow |
| Tool lazy loading | `toolSearch.ts` | defer_loading saves ~20K tokens |
| Large result persist | `toolResultBudget` | Oversized tool_result persisted to disk |
| Trim old results | `snipCompact.ts` | Preserve structure, clear content |
| Intermediate compress | `microCompact.ts` | Cache editing mode avoids cache invalidation |
| Subtask folding | `contextCollapse/` | Read-time projection, original history unmodified |
| Full compression | `autoCompact.ts` | Last resort, with circuit breaker |
| Passive compression | `reactiveCompact` | 413 error triggered, prevents infinite loop |
| Post-compact cleanup | `postCompactCleanup.ts` | 10+ state resets, distinguishes main thread/sub-agent |

### Next Chapter Preview
Chapter 5 will dive deep into the **Multi-Agent System** — how Coordinator orchestrates multiple sub-agents,
and how Fork mode enables prompt cache sharing.
