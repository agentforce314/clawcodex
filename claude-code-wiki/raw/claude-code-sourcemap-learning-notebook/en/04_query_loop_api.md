# Chapter 4: Query Loop and API Interaction (In-Depth Version)

> **This is the heart of Claude Code.** `query.ts` (67KB, 1730 lines) implements the complete loop from user input to AI response.
> This chapter will delve into every engineering ingenuity, allowing you to understand why it is designed this way.

## Learning Objectives
1. Understand the state machine design of the query loop (why use `while(true)` + State object)
2. Understand the concurrency control model of StreamingToolExecutor (sibling abort, progress wake-up)
3. Understand the multi-layer compression strategy (snip → microcompact → context collapse → autocompact)
4. Understand the hierarchical design of error recovery (escalate → multi-turn → reactive compact)
5. Understand the full-link design optimization of prompt cache
6. Understand the detection of diminishing returns of token budget
7. **New** Establish a holistic understanding through end-to-end request tracing
8. **New** Understand the comparison of schemes behind design decisions (why not use other schemes)
9. **New** Extract design patterns that can be transferred to your own Agent system

---

## 4.1 Architectural Overview: Why query.ts is a State Machine

### 4.1.1 Core Design Decision: while(true) + State Object

**Building intuition first**: Imagine you're having a conversation with an AI — you say something, the AI responds, and if the AI needs to perform an action (like reading a file or running a command), it goes and does it, then tells you the result and continues responding. This "talk → act → talk → act" cycle is what the query loop does, iteration after iteration.

The State object is this loop's **notebook** — it records where the conversation is at, what errors have occurred, and what recovery strategies have been tried. At the start of each iteration, the code reads from this notebook; at the end, it writes the new state back.

The core of query.ts is a `while(true)` loop, not recursive calls. This is a well-considered design. Below is the full State definition, with fields organized into three functional groups:

```typescript
type State = {
  // ===== Group 1: Core conversation state =====
  messages: Message[]           // The conversation history (all messages so far)
  toolUseContext: ToolUseContext // Shared context for tool execution (file cache, permissions, etc.)
  turnCount: number             // How many loop iterations have run

  // ===== Group 2: Compression & recovery tracking =====
  // These fields track error recovery attempts to prevent infinite retry loops
  autoCompactTracking: AutoCompactTrackingState | undefined  // Tracks auto-compression state (undefined = not started)
  maxOutputTokensRecoveryCount: number    // How many times we've retried after output truncation
  hasAttemptedReactiveCompact: boolean    // Whether we've already tried emergency compression
  maxOutputTokensOverride: number | undefined  // Upgraded output limit (undefined = use default)
  pendingToolUseSummary: Promise<ToolUseSummaryMessage | null> | undefined  // Async summary being generated in background

  // ===== Group 3: Flow control =====
  stopHookActive: boolean | undefined  // Whether stop hooks are currently running
  transition: Continue | undefined     // WHY the last iteration continued (undefined on first iteration)
  // The transition field is not just for debugging — it's a guard against recovery loops (see below)
}
```

> **Note for non-TS readers**: `X | undefined` means "this value might not exist", similar to `null`/`None` in other languages. `Promise<X>` means "an async operation running in the background that will eventually produce a result of type X".

**Ingenuity 1: transition field — preventing recovery death loops**

The cleverest field in State is `transition`. It records "why the last iteration chose to continue". This is not just for debugging — it's used to **prevent recovery loops**: if the last iteration already tried a certain recovery strategy and it failed, this iteration won't repeat it:

```typescript
// If the last one already did collapse_drain_retry, and this one is still 413,
// do not try to drain again, but fall through to reactive compact
if (state.transition?.reason !== 'collapse_drain_retry') {
  const drained = contextCollapse.recoverFromOverflow(...)
  // ...
}
```

**All possible transition reasons**:

| transition.reason | Meaning | Trigger Condition |
|---|---|---|
| `next_turn` | Normal next turn | Tool execution completed |
| `max_output_tokens_recovery` | Output truncated recovery | stop_reason = max_output_tokens |
| `max_output_tokens_escalate` | Upgrade output limit | Upgrade from 8k to 64k |
| `reactive_compact_retry` | Retry after reactive compression | prompt_too_long error |
| `collapse_drain_retry` | Retry after context collapse | prompt_too_long error |
| `stop_hook_blocking` | Stop Hook prevents stopping | Hook returns blocking error |
| `token_budget_continuation` | Token budget not exhausted | Still have budget to continue |

### 4.1.2 Why not use recursion?

The early version might have been recursive (there are still `query_recursive_call` checkpoints in the comments), but it was changed to a loop.

**Analogy**: Recursion is like Russian nesting dolls — each layer wraps the previous one, and 100 rounds of conversation means 100 layers of dolls, consuming ever more memory. A `while(true)` loop is like a person sitting at a desk doing repetitive work — they only use one sheet of paper (State) at a time, flipping to the next page when done, keeping memory usage constant.

```
Problems with recursion:
1. Each recursion creates a new closure → memory leak (long conversations may have 100+ rounds)
2. Recursion depth is limited by the call stack
3. State passing requires a large number of parameters
4. Unable to do GC in intermediate states

Advantages of while(true) + State:
1. Single closure, memory is constant
2. No stack depth limit
3. All states are concentrated in the State object
4. Continue sites can precisely control which states are reset
```

**Ingenuity2: State reset strategy at continue sites**

Each continue site precisely controls which states are reset and which are retained:

```typescript
// max_output_tokens recovery: retain hasAttemptedReactiveCompact
const next: State = {
  messages: [...messagesForQuery, ...assistantMessages, recoveryMessage],
  maxOutputTokensRecoveryCount: maxOutputTokensRecoveryCount + 1, // Increment
  hasAttemptedReactiveCompact, // ← Retain! Do not reset
  // ...
}

// Normal next turn: reset recovery counter
const next: State = {
  messages: [...messagesForQuery, ...assistantMessages, ...toolResults],
  maxOutputTokensRecoveryCount: 0, // ← Reset
  hasAttemptedReactiveCompact: false, // ← Reset
  // ...
}

// stop_hook_blocking: retain hasAttemptedReactiveCompact
// The comment explains why:
// "Preserve the reactive compact guard — if compact already ran and
// couldn't recover from prompt-too-long, retrying after a stop-hook
// blocking error will produce the same result. Resetting to false
// here caused an infinite loop: compact → still too long → error →
// stop hook blocking → compact → … burning thousands of API calls."
```

This comment reveals a **real production bug**: resetting `hasAttemptedReactiveCompact` caused an infinite loop,
burning thousands of API calls.

### 4.1.3 Complete Loop Flowchart

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ query() main loop │
├─────────────────────────────────────────────────────────────────────────────┤
│ │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ Phase 0: Preprocessing pipeline (each iteration) │ │
│ │ ├── applyToolResultBudget() → Persist large results to disk │ │
│ │ ├── snipCompact() → Trim old tool results │ │
│ │ ├── microcompact() → Compress intermediate tool calls │ │
│ │ ├── contextCollapse() → Collapse completed subtasks │ │
│ │ └── autocompact() → Full compression (if needed) │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ │ │
│ ▼ │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ Phase 1: API call │ │
│ │ ├── prependUserContext() → Inject CLAUDE.md, etc. │ │
│ │ ├── callModel() streaming return → Receive response block by block │ │
│ │ ├── StreamingToolExecutor → Execute tools while receiving │ │
│ │ └── Error handling → Fallback model / withhold │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ │ │
│ ┌─────────┴─────────┐ │
│ │ │ │
│ Has tool_use? No tool_use │
│ │ │ │
│ ▼ ▼ │
│ ┌─────────────────────┐ ┌──────────────────────────────────┐ │
│ │ Phase 2: Tool execution │ │ Phase 3: Stop judgment │ │
│ │ ├── Collect remaining results │ │ ├── 413 recovery (collapse/compact) │ │
│ │ ├── Attachment injection │ │ ├── max_output_tokens recovery │ │
│ │ ├── Memory prefetch consumption │ │ ├── Stop Hooks execution │ │
│ │ ├── Skill discovery consumption │ │ ├── Token Budget check │ │
│ │ └── Refresh tool list │ │ └── return Terminal │ │
│ └────────┬────────────┘ └──────────────────────────────────┘ │
│ │ │
│ ▼ │
│ ┌─────────────────────┐ │
│ │ Phase 4: Continue │ │
│ │ state = next │ ──── continue ────→ Back to Phase 0 │
│ └─────────────────────┘ │
│ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.1.4 End-to-End Request Tracing: Follow a Request Through to Completion

The best way to understand the query loop is not to look at modules separately, but to **follow a real request through to completion**.

Assume user input: `Help me fix the null pointer bug in src/auth.ts`

```
Timeline                     What happened                                    Data changes
─────────────────────────────────────────────────────────────────────────────────────
T+0ms    User presses Enter
         │
         ├── processUserInput()                                messages = [
         │   Wrap user input as Message                             { role: 'user',
         │   type: 'user', content: [{ type: 'text',               content: 'Help me fix...' }
         │   text: 'Help me fix the null pointer bug in src/auth.ts' }]       ]
         │
         ├── query() entry
         │   ├── buildQueryConfig() → Snapshot immutable configuration
         │   ├── startRelevantMemoryPrefetch() → Background memory prefetch
         │   └── Initialize State object (turnCount=0)
         │
         ▼ ═══ Enter while(true) loop ═══

T+1ms    Phase 0: Preprocessing pipeline
         │
         ├── applyToolResultBudget()  → No operation (no tool results in the first round)
         ├── snipCompact()            → No operation (too few messages)
         ├── microcompact()           → No operation
         ├── contextCollapse()        → No operation
         └── autocompact()            → No operation

T+2ms    Phase 1: Assemble API request
         │
         ├── prependUserContext()                                API payload = {
         │   Inject CLAUDE.md content into system prompt                  system: [System prompt + CLAUDE.md],
         │   Inject git status and other context                      tools: [Bash, Read, Edit, ...],
         │                                                         messages: [User message],
         ├── callModel()                                           model: 'claude-sonnet-4-...',
         │   Send HTTP POST to Anthropic API                       max_tokens: 8192
         │   Start receiving SSE stream                              }
         │
         ▼

T+500ms  Phase 1: Streaming reception (持续 3-8 秒)
         │
         │  ┌─ SSE chunk 1: thinking block (if extended thinking is enabled)
         │  ├─ SSE chunk 2: text "Let me take a look at this file..."
         │  ├─ SSE chunk 3: tool_use starts { name: 'Read', id: 'tu_001' }
         │  │   └── StreamingToolExecutor.addTool('Read', 'tu_001')
         │  │       canExecuteTool(true) → Start execution immediately!
         │  │       Read('src/auth.ts') runs in the background
         │  │
         │  ├─ SSE chunk 4: tool_use input complete { path: 'src/auth.ts' }
         │  ├─ SSE chunk 5: tool_use starts { name: 'Grep', id: 'tu_002' }
         │  │   └── StreamingToolExecutor.addTool('Grep', 'tu_002')
         │  │       canExecuteTool(true) → Read is ConcurrencySafe, parallel!
         │  │       Grep('null.*pointer', 'src/') runs in the background
         │  │
         │  └─ SSE chunk N: message_stop, stop_reason: 'tool_use'
         │
         ▼

T+4000ms Phase 2: Collect tool results
         │
         ├── getRemainingResults()                               messages = [
         │   Read completed → yield tool_result                       User message,
         │   Grep completed → yield tool_result                       Assistant(text + 2 tool_use),
         │                                                         user(2 tool_result)
         ├── Consume memoryPrefetch (if completed)                   ]
         ├── Consume skillDiscovery (if any)
         └── needsFollowUp = true (has tool_use)

         Phase 4: Continue
         state = { messages: [..., tool_results], turnCount: 1,
                   transition: { reason: 'next_turn' } }
         continue → Return to Phase 0

         ▼ ═══ Second Iteration ═══

T+4001ms Phase 0: Preprocessing pipeline
         ├── snipCompact() → May trim the first round of Read results (if the file is large)
         └── Others → No operation

T+4002ms Phase 1: Second API call
         │                                                       API payload = {
         │  messages now contain the full conversation history                         messages: [
         │  The prefix is the same as last time → prompt cache hit!                      user message,        ← Cache hit
         │  Only pay for the newly added tool_result                                    assistant(...),   ← Cache hit
         │                                                           user(tool_results) ← New
         │  Claude sees the file content, generates a repair plan                       ]
         │  Returns tool_use: Edit('src/auth.ts', ...)               }
         │
         ▼

T+8000ms Phase 2: Execute Edit
         │
         ├── Edit is not ConcurrencySafe → Exclusive execution
         ├── Permission check → Pop-up confirmation dialog (if needed)
         ├── User confirms → Perform edit
         └── tool_result: { success: true }

         Phase 4: Continue → Third iteration

         ▼ ═══ Third Iteration ═══

T+9000ms Phase 1: Third API call
         │
         │  Claude sees Edit success
         │  Returns text: "Fixed null pointer bug, added null check..."
         │  stop_reason: 'end_turn' (no tool_use)
         │
         ▼

T+12000ms Phase 3: Stop determination
          │
          ├── No 413 error → Skip
          ├── stop_reason != max_output_tokens → Skip
          ├── handleStopHooks()
          │   ├── Execute user-defined stop hooks (e.g., lint checks)
          │   ├── Hooks pass → Continue stop process
          │   └── Background: extractMemories(), promptSuggestion()
          ├── checkTokenBudget() → budget is null → stop
          └── return { reason: 'completed' }

          ═══ query() returns ═══

Total: 3 API calls, ~12 seconds, consumed ~5000 input tokens + ~2000 output tokens
Where prompt cache saved ~80% input token costs for the 2nd and 3rd calls
```

**Key Observations**:
- The Read and Grep of the first iteration **started executing while Claude was still outputting** (Value of StreamingToolExecutor)
- The prefix of the second API call is identical to the first → **prompt cache hit** (Only pay for incremental tokens)
- The Edit tool **executes exclusively** (not in parallel with other tools), as it modifies files
- The entire process is 3 `while(true)` iterations, not 3 recursive calls

---
## 4.2 StreamingToolExecutor — Streaming Concurrent Execution Engine

### 4.2.1 Core Problem: Why Do We Need Streaming Execution?

**Building intuition first**: The traditional approach is to wait for the AI to finish speaking, then execute all the actions it requested. It's like a boss writing an entire page of to-dos before handing it to you. Streaming execution is like the boss dictating tasks one by one — you start on the first one immediately, without waiting for the full list. This dramatically reduces total time.

Traditional approach: Wait for Claude's complete response → parse all tool_use → execute tools
```
Traditional method (serial):
  Claude response [████████████████] 5s
  Parse tool_use 0.1s
  Execute Tool A [████] 2s
  Execute Tool B [██████] 3s
  Execute Tool C [███] 1.5s
  Total: 5 + 0.1 + 2 + 3 + 1.5 = 11.6s

Streaming method (StreamingToolExecutor):
  Claude response [████████████████] 5s
  Tool A [████] ← Started while Claude is still outputting!
  Tool B [██████] ← Started before Tool A is completed!
  Tool C [███] ← Executed in parallel!
  Total: 5 + max(2,3,1.5) = 8s 31% saved
```

### 4.2.2 Concurrency Control Model: Why Not Actor or DAG?

**Building intuition first**: When the AI requests multiple actions simultaneously, which ones can run in parallel and which must queue? Claude Code's answer is simple — **read operations can run in parallel, write operations must be exclusive**. It's like a library: multiple people can read books at the same time, but only one person can annotate a book at a time.

Before diving into the implementation, let's understand **why this approach was chosen**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│ Comparison of schemes: Three paradigms for Agent tool orchestration                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│ Scheme A: Actor model (Erlang/Elixir style)                                │
│   Each tool has an Actor, communicating via message passing                                       │
│   Pros: Natural isolation, failures do not propagate                                            │
│   Cons: JS is single-threaded → Actor degenerates into Promise                             │
│         progress messages require additional mailbox mechanism                            │
│         sibling abort requires supervisor tree → over-engineering                    │
│   Conclusion: In JS, the Actor model has no real concurrency advantage and increases complexity    │
│                                                                         │
│ Scheme B: DAG scheduling (LangGraph style)                                      │
│   Define dependencies between tools in advance, execute with topological sorting                               │
│   Pros: Dependencies are explicit, optimal scheduling is possible                                    │
│   Cons: Claude decides which tools to call at runtime → cannot pre-build DAG               │
│         The dependencies between tools are implicit (Bash A's output may affect Bash B)          │
│         Have to wait for Claude's complete response to build the graph → loses the advantage of streaming execution     │
│   Conclusion: DAG is suitable for predefined workflows, not for LLM dynamic decision-making scenarios     │
│                                                                         │
│ Scheme C: Read-write lock + streaming addition (Claude Code's choice) ✓                       │
│   ConcurrencySafe = read lock, non-safe = write lock                                │
│   Pros: Simple and intuitive (reads can be parallel, writes must be exclusive)                                  │
│          Supports streaming addition (no need to wait for a complete response)                                 │
│         sibling abort naturally integrates (shared AbortController)                  │
│   Cons: Cannot express fine-grained dependencies (e.g., "Read A must be before Edit B")           │
│   Conclusion: In the LLM Agent scenario, simplicity > precision                               │
│                                                                         │
│ Core insight: LLM decides the order of tool calls, and LLM usually outputs in logical order            │
│ (Read first, then Edit), so a simple read-write lock is enough.                           │
│ If LLM outputs the wrong order, Bash's incorrect sibling abort will be the last resort.          │
└─────────────────────────────────────────────────────────────────────────┘
```

StreamingToolExecutor uses an ingenious concurrency control model:

```typescript
// Core judgment: Can this tool be executed in parallel with other tools?
private canExecuteTool(isConcurrencySafe: boolean): boolean {
  const executingTools = this.tools.filter(t => t.status === 'executing')
  return (
  executingTools.length === 0 || // No tools are being executed
  (isConcurrencySafe && executingTools.every(t => t.isConcurrencySafe))
  // Or: Itself is safe AND all being executed are safe
  )
}
```

This implements a **read-write lock** semantics:

```
┌─────────────────────────────────────────────────────────┐
│ Concurrency safety matrix │
├─────────────────────────────────────────────────────────┤
│ │
│  Being executed \ New tool ConcurrencySafe NOT Safe │
│ ───────────────────────────────────────────── │
│  None Execute immediately Execute immediately │
│  ConcurrencySafe Execute in parallel Wait │
│  NOT Safe Wait Wait │
│ │
│ Analogy: │
│ ConcurrencySafe = read lock (multiple reads can be parallel) │
│ NOT Safe = write lock (write must be exclusive) │
│ │
└─────────────────────────────────────────────────────────┘
```

### 4.2.3 Sibling Abort — Bash Error Cascading Cancellation

This is a very ingenious design. When multiple Bash commands are executed in parallel, if one fails,
the others usually make no sense anymore (since Bash commands often have implicit dependency chains):

```typescript
// Create a child AbortController that does not affect the parent
private siblingAbortController: AbortController

constructor(toolDefinitions, canUseTool, toolUseContext) {
  // siblingAbortController is a child controller of toolUseContext.abortController
  // Canceling it will not cancel the parent → query loop will not end this turn
  this.siblingAbortController = createChildAbortController(
  toolUseContext.abortController,
  )
}

// During tool execution:
if (isErrorResult) {
  // Only Bash errors cancel sibling tools!
  // Read/WebFetch, etc., are independent — one failure should not affect others
  if (tool.block.name === BASH_TOOL_NAME) {
    this.hasErrored = true
    this.erroredToolDescription = this.getToolDescription(tool)
    this.siblingAbortController.abort('sibling_error')
  }
}
```

**Ingenuity 3: Three-tier AbortController hierarchy**

```
toolUseContext.abortController (User presses Ctrl+C)
 └── siblingAbortController (Bash error cascading)
 ├── toolAbortController[0] (Tool A's independent controller)
 ├── toolAbortController[1] (Tool B's independent controller)
 └── toolAbortController[2] (Tool C's independent controller)

User Ctrl+C → All tools stop, query loop ends
Bash A fails → B, C stop, but query loop continues (collect error results)
Permission denied → The tool stops, and bubbles up to query loop (end turn)
```

Note the bubble-up logic in the abort event listener of toolAbortController:

```typescript
toolAbortController.signal.addEventListener('abort', () => {
  // If not sibling_error, and parent is not aborted, and not discarded
  // → Bubble up to parent (e.g., permission denial needs to end the entire turn)
  if (
  toolAbortController.signal.reason !== 'sibling_error' &&
  !this.toolUseContext.abortController.signal.aborted &&
  !this.discarded
  ) {
    this.toolUseContext.abortController.abort(
    toolAbortController.signal.reason,
    )
  }
}, { once: true })
```

### 4.2.4 Progress Wake-up Mechanism

During tool execution, progress messages are generated (e.g., real-time output of Bash).
These messages need to be **immediately** passed to the UI, not wait for the tool to complete:

```typescript
// Progress messages are stored in a separate queue
type TrackedTool = {
  // ...
  results?: Message[] // Final results (yielded after the tool is completed)
  pendingProgress: Message[] // Progress messages (immediately yielded)
}

// When there is a new progress message, wake up the waiting getRemainingResults
if (update.message.type === 'progress') {
  tool.pendingProgress.push(update.message)
  // Wake up!
  if (this.progressAvailableResolve) {
    this.progressAvailableResolve()
    this.progressAvailableResolve = undefined
  }
}

// Waiting logic in getRemainingResults:
async *getRemainingResults() {
  while (this.hasUnfinishedTools()) {
    // First yield completed results and progress
    for (const result of this.getCompletedResults()) {
      yield result
    }
  }
```
```plaintext
// If there are still executing tools, wait for any to complete OR progress to reach
if (this.hasExecutingTools() && !this.hasCompletedResults()) {
  const progressPromise = new Promise<void>(resolve => {
    this.progressAvailableResolve = resolve // Register wake-up callback
  })
  await Promise.race([...executingPromises, progressPromise])
}
}
}
```

**Clever Idea 4: Non-blocking Wait with Promise.race**

This is a classic "multiple signal waiting" pattern:
- Any tool completes → Wake up → Yield result
- Any progress arrives → Wake up → Yield progress
- Neither arrives → Keep waiting

### 4.2.5 Streaming Fallback Handling

When the API triggers a fallback (switching to a backup model) during streaming,
the tools that have already started executing need to be discarded:

```typescript
if (streamingFallbackOccured) {
  // 1. Send tombstone for assistant messages that have been yielded (remove from UI and transcript)
  for (const msg of assistantMessages) {
    yield { type: 'tombstone', message: msg }
  }

  // 2. Clear all states
  assistantMessages.length = 0
  toolResults.length = 0
  toolUseBlocks.length = 0
  needsFollowUp = false

  // 3. Discard old executor and create a new one
  if (streamingToolExecutor) {
    streamingToolExecutor.discard() // Mark as discarded
    streamingToolExecutor = new StreamingToolExecutor(...) // Brand new
  }
}
```

**Why Tombstone is Needed?**

Because partial messages (especially thinking blocks) have invalid signatures,
if retained in the message history, it will cause the API to return an "thinking blocks cannot be modified" error.

---
## 4.3 Multi-layer Compression Strategy — Swiss Army Knife of Context Management

Claude Code has a **5-layer** context compression strategy, arranged in the order of execution:

```
┌─────────────────────────────────────────────────────────────┐
│ Compression Pipeline (per iteration) │
├─────────────────────────────────────────────────────────────┤
│ │
│ 1. applyToolResultBudget() │
│ └── Huge tool_result → Persist to disk, replace with reference │
│ └── Run before microcompact (MC operates by tool_use_id) │
│ │
│ 2. snipCompact() [feature: HISTORY_SNIP] │
│ └── Trim old tool results (preserve structure, delete content) │
│ └── Return tokensFreed for autocompact reference │
│ │
│ 3. microcompact() │
│ └── Compress intermediate tool invocations (preserve beginning and end, compress middle) │
│ └── Support cached microcompact (leverage API cache for deletion) │
│ │
│ 4. contextCollapse() [feature: CONTEXT_COLLAPSE] │
│ └── Collapse completed subtasks into summaries │
│ └── Run before autocompact → If enough collapsing is done, no need for full quantity │
│ └── Is "read-time projection" rather than "write-time modification" │
│ │
│ 5. autocompact() │
│ └── Full quantity compression: Call Claude to generate summaries │
│ └── Last resort, highest cost │
│ │
└─────────────────────────────────────────────────────────────┘
```

**Clever Idea 5: Careful Design of Execution Order**

```
toolResultBudget → snip → microcompact → collapse → autocompact
  Cheap  Cheap  Moderate  Moderate  Expensive
  Local  Local  Local/API  Local  API call
```

Do the cheap ones first, if enough, no need for the expensive ones. Especially contextCollapse before autocompact:

```typescript
// Original comment:
// Runs BEFORE autocompact so that if collapse gets us under the
// autocompact threshold, autocompact is a no-op and we keep granular
// context instead of a single summary.
```

Collapsing retains a finer-grained context (summary of each subtask), while full quantity compression will compress everything into a large summary.

### 4.3.1 Context Collapse's "Read-time Projection" Design

This is a particularly elegant design pattern:

```typescript
// Original comment:
// Nothing is yielded — the collapsed view is a read-time projection
// over the REPL's full history. Summary messages live in the collapse
// store, not the REPL array. This is what makes collapses persist
// across turns: projectView() replays the commit log on every entry.
```

```
REPL Message Array (full history, never modified):
  [msg1, msg2, msg3, msg4, msg5, msg6, msg7, msg8]

Collapse Store (summary storage):
  commit1: { archived: [msg1, msg2, msg3], summary: "..." }
  commit2: { archived: [msg4, msg5], summary: "..." }

Output of projectView() (recalculated every iteration):
  [summary1, summary2, msg6, msg7, msg8]

Advantages:
  - REPL array is never modified → No loss of original data
  - Summaries can be regenerated at any time
  - Similar to Git's commit log → Can "replay" collapsed history
```

---
## 4.4 Hierarchical Design for Error Recovery

### 4.4.1 max_output_tokens Recovery (Three Stages)

When Claude's output is truncated, the recovery strategy is divided into three stages:

```
Stage 1: Escalate (Increase Limit)
  Conditions: Default 8k limit used && No user override
  Actions: Retry the same request, but max_output_tokens = 64k
  Features: No extra messages, no multi-turn overhead

Stage 2: Multi-turn Recovery
  Conditions: Still truncated after escalation || User override exists
  Actions: Inject recovery message to let Claude continue
  Message: "Output token limit hit. Resume directly — no apology,
  no recap. Pick up mid-thought if that is where the cut
  happened. Break remaining work into smaller pieces."
  Maximum: 3 times

Stage 3: Give Up
  Conditions: All 3 recovery attempts fail
  Actions: Display the truncated message
```

**Clever Idea 6: Careful Phrasing of Recovery Messages**

Note the careful phrasing of recovery messages:
- "no apology" — Prevent Claude from wasting tokens apologizing
- "no recap" — Prevent Claude from repeating what has been said
- "Pick up mid-thought" — Allow continuation from the middle of a sentence
- "Break remaining work into smaller pieces" — Guide Claude to output in chunks

### 4.4.2 prompt_too_long Recovery (Three Stages)

```
Stage 1: Context Collapse Drain
  Conditions: Pending collapse to be submitted && Last attempt was not collapse_drain_retry
  Actions: Submit all pending collapses
  Cost: Zero (pure local operation)

Stage 2: Reactive Compact
  Conditions: Drain is not enough || No collapse
  Actions: Call Claude to generate a summary
  Cost: One API call
  Features: Only try once (guarded by hasAttemptedReactiveCompact)

Stage 3: Give Up
  Conditions: Reactive compact is also not enough
  Actions: Display error message
  Key: Do not enter Stop Hooks!
  Reason: "hooks have nothing meaningful to evaluate. Running stop hooks
  on prompt-too-long creates a death spiral: error → hook blocking
  → retry → error → …"
```

### 4.4.3 Withhold Mode — Delayed Error Display

This is a key design pattern: In the streaming loop, recoverable errors are "withheld" instead of being immediately yielded:

```typescript
let withheld = false
if (contextCollapse?.isWithheldPromptTooLong(message, ...)) withheld = true
if (reactiveCompact?.isWithheldPromptTooLong(message)) withheld = true
if (reactiveCompact?.isWithheldMediaSizeError(message)) withheld = true
if (isWithheldMaxOutputTokens(message)) withheld = true
if (!withheld) yield yieldMessage // Only yield non-withheld messages
```

**Why Withhold?**

If error messages are yielded immediately, SDK consumers (such as Desktop App) will see the error and terminate the session.
But the recovery loop is still running — nobody is listening. Withhold gives the recovery a chance to succeed,
and only display the error when recovery fails.

**Clever Idea 7: Hoisted Gate Prevents Inconsistency Between Withhold/Recover**

```typescript
// Determine mediaRecoveryEnabled before the streaming loop
const mediaRecoveryEnabled =
  reactiveCompact?.isReactiveCompactEnabled() ?? false

// Why? Because CACHED_MAY_BE_STALE might flip during a 5-30s streaming transmission
// If it's true when withholding, but becomes false when recovering → Message loss!
```

---
## 4.5 Prompt Cache Full-Chain Optimization

### 4.5.1 Conditions for Cache Hit

The prompt cache key of the Anthropic API consists of the following parts:

```
cache_key = hash(
  system_prompt, ← Must be exactly the same
  tools, ← Must be exactly the same (including order)
  model, ← Must be exactly the same
  messages[0..N-1], ← Prefix must be exactly the same (byte-level)
  thinking_config, ← Must be exactly the same
)
```

### 4.5.2 Claude Code's Cache Optimization Methods

**Method 1: Stable Tool List Sorting**
```typescript
// Sort tools in tools.ts to ensure the tool definition prefix remains unchanged for each request
// Even if MCP tools are dynamically loaded, the sorting is deterministic
```

**Method 2: Do Not Modify Sent Messages**
```typescript
// backfillObservableInput operates only on the clone of the yielded
// The original message remains unchanged → The prefix bytes are the same for the next request
let yieldMessage: typeof message = message
if (addedFields) {
  clonedContent ??= [...message.message.content]
  clonedContent[i] = { ...block, input: inputCopy }
  yieldMessage = { ...message, message: { ...message.message, content: clonedContent } }
}
// Comment: "The original `message` is left untouched for assistantMessages.push
// below — it flows back to the API and mutating it would break prompt caching
// (byte mismatch)."
```

**Method 3: Fork Sub-Agent Shares Parent Cache**
```typescript
// CacheSafeParams — Parameters that must be exactly the same as the parent
export type CacheSafeParams = {
  systemPrompt: SystemPrompt
  userContext: { [k: string]: string }
  systemContext: { [k: string]: string }
  toolUseContext: ToolUseContext
  forkContextMessages: Message[] // Parent's message history
}

// Fork sub-agent uses parent's messages as prefix
// → Prefix is exactly the same → Cache hit
// → Fork is cheap (only pay for incremental token money)
```

**Method 4: contentReplacementState Cloning**
```typescript
// Original comment:
// Clone by default (not fresh): cache-sharing forks process parent
// messages containing parent tool_use_ids. A fresh state would see
// them as unseen and make divergent replacement decisions → wire
// prefix differs → cache miss.
```

**Method 5: dumpPromptsFetch Singleton**
```typescript
// Create only one fetch wrapper per query session
// Avoid creating a new closure for each iteration → Only retain the latest request body (~700KB)
// Instead of all request bodies (~500MB for long sessions)
const dumpPromptsFetch = config.gates.isAnt
  ? createDumpPromptsFetch(toolUseContext.agentId ?? config.sessionId)
  : undefined
```

---
## 4.6 Token Budget — Intelligent "Continue" Decisions

Token Budget is a mechanism that allows Claude to use more tokens in a single turn:

```typescript
export function checkTokenBudget(
tracker: BudgetTracker,
agentId: string | undefined,
budget: number | null,
globalTurnTokens: number,
): TokenBudgetDecision {
  // Sub-agents do not participate in budget (only main thread)
  if (agentId || budget === null || budget <= 0) {
    return { action: 'stop', completionEvent: null }
  }

  const pct = Math.round((turnTokens / budget) * 100)
  const deltaSinceLastCheck = globalTurnTokens - tracker.lastGlobalTurnTokens

  // Clever Idea 8: Diminishing Returns Detection
  const isDiminishing =
  tracker.continuationCount >= 3 && // At least continued 3 times
  deltaSinceLastCheck < DIMINISHING_THRESHOLD && // This increment < 500 tokens
  tracker.lastDeltaTokens < DIMINISHING_THRESHOLD // Last increment also < 500

  // If no diminishing and not reaching 90% → Continue
  if (!isDiminishing && turnTokens < budget * COMPLETION_THRESHOLD) {
    return { action: 'continue', nudgeMessage: '...' }
  }
```
```plaintext
// Otherwise, stop
return { action: 'stop', completionEvent: { diminishingReturns: isDiminishing } }
}
```

**Why is Diminishing Returns detection necessary?**

```
Scenario: The user sets a budget of 500k tokens

Without DR detection:
Turn 1: 50k tokens (useful work)
Turn 2: 30k tokens (useful work)
Turn 3: 20k tokens (useful work)
Turn 4: 200 tokens (Claude says "I think we're done")
Turn 5: 150 tokens (Claude says "Is there anything else?")
Turn 6: 100 tokens (Claude says "Let me know if you need more")
... Continue wasting tokens until 500k ...

With DR detection:
Turn 1-3: Same as above
Turn 4: 200 tokens → delta < 500 → record
Turn 5: 150 tokens → two consecutive deltas < 500 → Stop!
Save a large number of meaningless tokens
```

---
## 4.7 Message Queue and Interrupt Injection

### 4.7.1 Message Queue within Agent Scope

The message queue is a globally unique instance across the process, but each Agent only consumes messages that belong to itself:

```typescript
// The main thread only consumes messages with agentId === undefined
// Sub Agents only consume their own agentId's task-notification
const queuedCommandsSnapshot = getCommandsByMaxPriority(
sleepRan ? 'later' : 'next',
).filter(cmd => {
  if (isSlashCommand(cmd)) return false // Slash commands are not processed here
  if (isMainThread) return cmd.agentId === undefined
  // Sub Agents only consume task-notification, not user prompts
  return cmd.mode === 'task-notification' && cmd.agentId === currentAgentId
})
```

**Ingenious Thought 9: Sleep tool triggers deeper queue consumption**

```typescript
const sleepRan = toolUseBlocks.some(b => b.name === SLEEP_TOOL_NAME)
// sleepRan ? 'later' : 'next'
```

When Claude actively calls the Sleep tool, it waits for some asynchronous tasks to complete.
At this time, consume messages with 'later' priority (including completion notifications from other Agents),
and normally only consume messages with 'next' priority.

### 4.7.2 Memory Prefetch — Asynchronous Prefetching and Zero-Wait Consumption

```typescript
// Initiate prefetching at the query() entry point (triggered only once)
using pendingMemoryPrefetch = startRelevantMemoryPrefetch(
state.messages, state.toolUseContext,
)

// Attempt to consume after each iteration of tool execution
if (
pendingMemoryPrefetch &&
pendingMemoryPrefetch.settledAt !== null && // completed
pendingMemoryPrefetch.consumedOnIteration === -1 // not consumed yet
) {
  const memoryAttachments = filterDuplicateMemoryAttachments(
  await pendingMemoryPrefetch.promise,
  toolUseContext.readFileState, // filter out already read files
  )
  // ...
  pendingMemoryPrefetch.consumedOnIteration = turnCount - 1
}
```

**Design Points**:
- The `using` keyword ensures cleanup (dispose) on all exit paths
- Prefetching runs in parallel during the model streaming transmission
- If prefetching is not completed, skip (zero wait), try again on the next iteration
- `readFileState` filtering ensures that files Claude has already read are not injected

---
## 4.8 Stop Hooks — Programmable Stop Conditions

Stop Hooks are not just a simple "check if stop"; it is a complete programmable system:

```
Claude says "I'm done"
 │
 ▼
handleStopHooks()
 │
 ├── Save CacheSafeParams (for /btw and side_question use)
 │
 ├── Execute Stop Hooks (user-defined shell commands)
 │ ├── blocking error → Inject message, continue loop
 │ ├── preventContinuation → Force stop
 │ └── Pass → Continue checking
 │
 ├── If Teammate:
 │ ├── Execute TaskCompleted Hooks
 │ └── Execute TeammateIdle Hooks
 │
 ├── Background tasks (fire-and-forget):
 │ ├── executePromptSuggestion() → Generate follow-up question suggestions
 │ ├── executeExtractMemories() → Extract memories
 │ └── executeAutoDream() → Automatic dream (introspection)
 │
 └── Cleanup:
 └── cleanupComputerUseAfterTurn() → Release CU lock
```

**Ingenious Thought 10: Skip Stop Hooks on API errors**

```typescript
// If the last message is an API error message, skip Stop Hooks
if (lastMessage?.isApiErrorMessage) {
  void executeStopFailureHooks(lastMessage, toolUseContext)
  return { reason: 'completed' }
}
```

Why? Because Stop Hooks evaluate Claude's response. If Claude does not produce a valid response
(API error), evaluating Hooks will create a death spiral:
error → hook blocking → retry → error → …

---
## 4.9 Two Modes of Tool Orchestration

Claude Code has two modes of tool orchestration, switched through a feature gate:

### Mode 1: StreamingToolExecutor (New)
- Execute tools while receiving API responses
- Use addTool() to add one by one
- Automatically manage concurrency
- Support progress wake-up
- Support sibling abort

### Mode 2: runTools (Traditional)
- Execute in batches after API responses are completed
- Use partitionToolCalls() to batch

```typescript
// partitionToolCalls batching strategy:
// Consecutive ConcurrencySafe tools are batched and executed in parallel
// Non-safe tools are executed in a separate batch serially

// Input: [Glob, Grep, Read, Edit, Glob, Read]
// Batches: [[Glob, Grep, Read], [Edit], [Glob, Read]]
// ↑ Parallel execution ↑ Serial ↑ Parallel execution

function partitionToolCalls(toolUseMessages, toolUseContext): Batch[] {
  return toolUseMessages.reduce((acc, toolUse) => {
    const isConcurrencySafe = /* ... */
    // If the current tool is safe AND the last batch is also safe → Merge
    if (isConcurrencySafe && acc[acc.length - 1]?.isConcurrencySafe) {
      acc[acc.length - 1].blocks.push(toolUse)
    } else {
      acc.push({ isConcurrencySafe, blocks: [toolUse] })
    }
    return acc
  }, [])
}
```

**Concurrency Limit**:
```typescript
function getMaxToolUseConcurrency(): number {
  return parseInt(process.env.CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY || '', 10) || 10
}
```
Default is up to 10 tools in parallel, which can be overridden by environment variables.

---
## 4.10 QueryConfig — Immutable Configuration Snapshot

```typescript
// Snapshot once at the query() entry point, unchanged throughout the loop
const config = buildQueryConfig()

export type QueryConfig = {
  sessionId: SessionId
  gates: {
  streamingToolExecution: boolean // Whether to enable streaming tool execution
  emitToolUseSummaries: boolean // Whether to generate tool use summaries
  isAnt: boolean // Whether it is an Anthropic internal user
  fastModeEnabled: boolean // Whether to enable fast mode
  }
}
```

**Ingenious Thought 11: Why snapshot?**

The comment explains:
```typescript
// Immutable values snapshotted once at query() entry. Separating these from
// the per-iteration State struct and the mutable ToolUseContext makes future
// step() extraction tractable — a pure reducer can take (state, event, config)
// where config is plain data.
```

This is in preparation for future architectural refactoring:
- State = Mutable state
- Config = Immutable configuration
- Event = Input event
- → Pure function reducer: `(state, event, config) → newState`

This is the shadow of Redux/Elm architecture, indicating that the team is considering refactoring the query loop into a more testable pure function.

---
## 4.11 Tool Use Summary — Asynchronous Summary Generation

Tool use summaries are for mobile UI (mobile does not display complete tool calls):

```typescript
// After tool execution, asynchronously generate summaries (do not block the next API call)
nextPendingToolUseSummary = generateToolUseSummary({
  tools: toolInfoForSummary,
  signal: toolUseContext.abortController.signal,
  isNonInteractiveSession: ...,
  lastAssistantText,
}).then(summary => summary ? createToolUseSummaryMessage(summary, toolUseIds) : null)
.catch(() => null) // Silent failure

// Consume at the start of the next iteration (Haiku ~1s, model streaming 5-30s)
if (pendingToolUseSummary) {
  const summary = await pendingToolUseSummary
  if (summary) yield summary
}
```

**Design Points**:
- Use Haiku model (fast, cheap)
- Asynchronous execution, does not block the main loop
- Only generated on the main thread (sub Agents do not need)
- Consumed on the next iteration (Haiku is already completed by then)

---
## Summary of This Chapter: List of Ingenious Thoughts

| # | Ingenious Thought | Problem Solved |
|---|-------------------|----------------|
| 1 | transition field | Prevent recovery loops, make tests assertable |
| 2 | Precise reset of continue sites | Prevent infinite loops (real bug) |
| 3 | Three-tier AbortController | Bash cascading cancellation vs user interruption vs permission denial |
| 4 | Promise.race multi-signal waiting | Progress real-time transmission |
| 5 | 5-layer compression pipeline | Cheap first, expensive later |
| 6 | Recovery message wording | Prevent Claude from wasting tokens |
| 7 | Hoisted gate | Prevent inconsistency between withhold/recover |
| 8 | Diminishing Returns | Prevent wasting tokens on meaningless continuations |
| 9 | Sleep triggers deep queue consumption | Let waiting Agents receive notifications |
| 10 | API error skips Hooks | Prevent death spiral |
| 11 | Config snapshot | Prepare for pure function reducer refactoring |

---
## 4.12 Migratable Design Patterns — Take These, Use in Your Own Agent System

The above 11 ingenious thoughts are unique to Claude Code, but they have **universal design patterns** behind them,
which can be directly migrated to any Agent system:

### Pattern 1: Optimistic Recovery

**In Claude Code**: Withhold mode — Detain error messages, attempt recovery, expose after recovery fails.

**General form**:
```
Receive error → Do not propagate immediately → Attempt recovery strategy A → Fail → Attempt strategy B → Fail → Propagate error
```

**Applicable scenarios**:
- API gateway retry mechanisms (502 does not return to the client immediately, retry other backends first)
- Database connection pools (connection breaks do not report errors immediately, attempt to reconnect first)
- Distributed system leader election (leader disconnection does not inform clients immediately, elect a new leader first)

**Key constraint**: Withhold and recover must use the **same gate variable** (hoisted gate),
otherwise, condition flips between withhold and recover will cause message loss.

### Pattern 2: Layered Degradation

**In Claude Code**: 5-layer compression pipeline — Cheap first, expensive later.

**General form**:
```
Problem occurs → Attempt zero-cost solution → Not enough → Attempt low-cost solution → Not enough → Attempt high-cost solution
```

**Applicable scenarios**:
- CDN caching strategies (memory cache → disk cache → back to source)
- Search engine degradation (exact match → fuzzy match → semantic search)
- Load balancing (local instance → same region → cross-region)

**Key constraint**: The execution order of each layer must be fixed, and the results of the previous layer must be perceivable by the next layer
(e.g., snipCompact returns tokensFreed for autocompact reference).

### Pattern 3: State Machine with Transition Log

**In Claude Code**: `while(true)` + `transition` field — Record why continue, prevent recovery loops.

**General form**:
```
while (true) {
  state = process(state)
  state.transition = { reason, timestamp }
  if (shouldStop(state)) break
  if (isRecoveryLoop(state.transition, previousTransition)) break // Prevent infinite loops
}
```

**Applicable scenarios**:
- Workflow engines (record why each step transitions, prevent circular approvals)
- Game AI state machines (record reasons for state transitions, prevent behavior jitter)
- Compiler optimization passes (record the effects of each pass, prevent optimization loops)

**Key constraint**: Transition records are not just for debugging — they are **guards against infinite loops**.

### Pattern 4: Concurrency Control with Read-Write Lock Semantics

**In Claude Code**: ConcurrencySafe = Read lock, non-safe = Write lock.

**General form**:
```
Operations are divided into two categories:
- Read-only operations (parallelizable): Multiple read-only operations can be executed simultaneously
- Write operations (exclusive): When a write operation is executed, all other operations wait
```

**Applicable scenarios**:
- Database MVCC (reads do not block reads, writes block everything)
- File system flock (LOCK_SH vs LOCK_EX)
- Kubernetes admission webhooks (multiple mutating webhooks in series, validating in parallel)

**Key constraint**: In the LLM Agent scenario, the read-write attributes of tools are **statically declared** (determined at tool definition time),
not inferred at runtime. This is much simpler than databases but also means that fine-grained dependencies cannot be expressed.

### Pattern 5: Immutable Config Snapshot

**In Claude Code**: `buildQueryConfig()` snapshots once at the entry point, unchanged throughout the loop.

**General form**:
```
const config = snapshot(mutableGlobalConfig) // Snapshot at the entry point
for (const event of events) {
  state = reducer(state, event, config) // config is immutable
}
```

**Applicable Scenarios**:
- React/Redux store design (action → reducer → new state)
- Game engine frame updates (snapshot input state at the beginning of each frame)
- Microservice configuration hot updates (snapshot configuration at the start of a request, unchanged within the request)

**Key Constraints**: This is for **testability** — pure function `(state, event, config) → newState`
is much easier to test than closures capturing global variables.

### Pattern 6: Hierarchical Control of Cascade Cancellation

**In Claude Code**: Three-tier AbortController — user interruption cancels everything, Bash error only cancels siblings, permission denial bubbles up to turn.

**General Form**:
```
Root AbortController (highest level cancellation)
  └── Group AbortController (group-level cancellation)
      ├── Task A AbortController
      ├── Task B AbortController
      └── Task C AbortController

Cancellation propagation rules:
- Root cancellation → All Group and Task cancellations
- Group cancellation → All Tasks in that group are cancelled, Root is unaffected
- Task cancellation → Decide whether to bubble up to Group based on the reason
```

**Applicable Scenarios**:
- Microservice request cancellation propagation (gateway timeout → cancel all downstream calls)
- CI/CD job cancellation (cancel pipeline → cancel all stages → cancel all steps)
- Browser fetch cancellation (page navigation → cancel all ongoing requests)

**Key Constraints**: The cancellation reason (`abort reason`) must carry semantic information,
allowing the child level to decide whether to bubble up based on the reason. Not all cancellations should propagate to the parent level.

---

### Next Chapter Preview
Chapter 5 will delve into **Multi-Agent Systems** — understanding how AgentTool creates and manages sub-agents.