# Chapter 1: Claude Code Global Architecture Overview

## Learning Objectives
After reading this chapter, you will understand:
1. What is Claude Code? What is its core value proposition?
2. What key steps does a user message go through from input to output?
3. The project's directory structure and each module's responsibilities
4. Core data flow and control flow

---

## 1.1 What is Claude Code?

**Building intuition first**: Claude Code is not just a chatbot — it's an AI that can directly read your files, run commands in your terminal, and edit your code. Think of it as a programming partner who sits next to you, has access to your entire project, and can actually *do* things, not just talk about them.

Claude Code is Anthropic's official **AI programming assistant CLI tool**. Its core philosophy:

> **Let the Claude model directly operate your filesystem and terminal, working like a true programming partner.**

### Tech Stack Overview

| Layer | Technology | Description |
|-------|-----------|-------------|
| **Language** | TypeScript | Entire project uses TS with strict types |
| **Runtime** | Bun | Replaces Node.js for faster startup and execution |
| **Terminal UI** | React + Ink | Uses React components to render terminal interfaces! |
| **CLI Framework** | Commander.js | Command-line argument parsing |
| **LLM API** | Anthropic SDK | Calls Claude models |
| **Extension Protocol** | MCP (Model Context Protocol) | Standardized tool extension protocol |
| **Build** | Bun bundler | Conditional compilation with feature flags |

### Project Scale
```
File count: 1,900+
Lines of code: 512,000+
Core source: src/ directory
```

## 1.2 Core Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                      User Terminal                              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ main.tsx (Entry Point)                                          │
│ ├── Commander.js parses CLI arguments                           │
│ ├── Init: auth, config, MCP, plugins, skills                   │
│ └── Launch React/Ink rendering → App.tsx                        │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ REPL Loop (components/REPL.tsx)                                 │
│ ├── Receive user input                                          │
│ ├── Handle /slash commands                                      │
│ └── Call QueryEngine                                            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ QueryEngine.ts (Core Engine)                                    │
│ ├── Build System Prompt (context.ts + prompts.ts)               │
│ ├── Call query() loop                                           │
│ │   ├── Send messages to Claude API                             │
│ │   ├── Stream receive response                                 │
│ │   ├── Parse tool_use blocks                                   │
│ │   ├── Permission check (permissions.ts)                       │
│ │   ├── Execute tools (toolExecution.ts)                        │
│ │   ├── Append tool_result to messages                          │
│ │   └── Loop until stop_reason = 'end_turn'                    │
│ └── Return final result                                         │
└─────────────────────────────────────────────────────────────────┘
```

## 1.3 Directory Structure

Note: the typescript directory is under $PROJECT_ROOT/typescript

```
src/
├── main.tsx              # CLI entry point (785KB! Contains all CLI arg definitions)
├── QueryEngine.ts        # Core engine - manages query lifecycle and session state
├── query.ts              # Query loop - LLM call → tool execution → re-call loop
├── Tool.ts               # Tool type definitions - interface contract for all tools
├── tools.ts              # Tool registry - assembles and filters tool pool
├── context.ts            # Context management - git status, CLAUDE.md, etc.
├── commands.ts           # /slash command registration
│
├── tools/                # All tool implementations
│   ├── BashTool/         # Execute shell commands
│   ├── FileReadTool/     # Read files
│   ├── FileEditTool/     # Edit files (diff/patch)
│   ├── FileWriteTool/    # Write files
│   ├── GlobTool/         # Filename pattern matching
│   ├── GrepTool/         # Text search
│   ├── AgentTool/        # Sub-Agent system
│   ├── WebSearchTool/    # Web search
│   ├── WebFetchTool/     # Fetch web content
│   ├── MCPTool/          # MCP tool wrapper
│   └── ...               # More tools
│
├── services/             # External service integrations
│   ├── api/              # Anthropic API calls
│   ├── mcp/              # MCP protocol client
│   ├── compact/          # Context compression (auto-compact)
│   ├── analytics/        # Telemetry and analytics
│   └── tools/            # Tool execution engine
│
├── hooks/                # Hook system
│   ├── useCanUseTool.ts  # Tool permission hook
│   └── toolPermission/   # Permission handlers
│
├── components/           # React/Ink UI components
│   ├── App.tsx           # Root component
│   ├── REPL.tsx          # REPL loop component
│   ├── Messages.tsx      # Message rendering
│   └── PromptInput/      # Input box
│
├── state/                # State management
│   ├── AppState.tsx      # Application state types
│   └── store.ts          # State store
│
├── coordinator/          # Multi-Agent coordination
├── bridge/               # IDE integration (VS Code, etc.)
├── skills/               # Reusable workflows
├── plugins/              # Plugin system
├── memdir/               # Memory system
├── entrypoints/          # Different launch modes
└── utils/                # Utility functions
    ├── permissions/      # Permission system core
    ├── model/            # Model management
    ├── messages.ts       # Message processing
    └── ...               # More utilities
```

## 1.4 Core Data Flow: A Message's Complete Journey

Let's trace the complete flow when a user inputs `"Read package.json for me"`:

```
Step 1: User Input
  └── REPL.tsx receives input text

Step 2: Message Construction
  └── processUserInput() converts text to Message object
      ├── Check if it's a /slash command
      ├── Process attachments (images, files, etc.)
      └── Build UserMessage { role: 'user', content: 'Read package.json for me' }

Step 3: QueryEngine.submitMessage()
  ├── Get System Prompt
  │   ├── Default system prompt (constants/prompts.ts)
  │   ├── User context: CLAUDE.md content + current date
  │   └── System context: git status + branch info
  ├── Register tool list (tools.ts → getAllBaseTools())
  └── Call query() to enter query loop

Step 4: query() Query Loop
  ├── 4a. Call Claude API
  │   ├── normalizeMessagesForAPI() format messages
  │   ├── prependUserContext() inject user context
  │   ├── appendSystemContext() inject system context
  │   └── Send HTTP request to Anthropic API
  │
  ├── 4b. Stream receive response
  │   ├── message_start → initialize
  │   ├── content_block_start → start receiving content blocks
  │   │   ├── type: 'text' → text response
  │   │   └── type: 'tool_use' → tool call request!
  │   │       { name: 'Read', input: { file_path: 'package.json' } }
  │   ├── content_block_delta → incremental content
  │   └── message_stop → message end
  │
  ├── 4c. Tool execution (when stop_reason = 'tool_use')
  │   ├── Permission check: hasPermissionsToUseTool()
  │   │   ├── Check deny rules → is it forbidden?
  │   │   ├── Check allow rules → is it authorized?
  │   │   ├── Check permission mode → bypass/plan/default?
  │   │   └── If needed → show permission confirmation dialog
  │   ├── Input validation: tool.validateInput()
  │   ├── Execute tool: tool.call()
  │   │   └── FileReadTool.call({ file_path: 'package.json' })
  │   │       → Read file content and return
  │   └── Build tool_result message
  │
  └── 4d. Continue loop
      ├── Append tool_result to message list
      ├── Call Claude API again (with tool results)
      ├── Claude generates final text response
      └── stop_reason = 'end_turn' → loop ends

Step 5: Result Return
  ├── Render Claude's text response to terminal
  ├── Record session to transcript
  └── Wait for next user input
```

## 1.5 Key File Overview

### main.tsx — CLI Entry (785KB)

This is the largest file in the entire project! It does the following:

```typescript
// 1. Performance optimization: start parallel preloading before all imports
profileCheckpoint('main_tsx_entry'); // Mark startup time
startMdmRawRead(); // Parallel read MDM config
startKeychainPrefetch(); // Parallel read keychain credentials

// 2. Define all CLI arguments using Commander.js
const program = new CommanderCommand()
  .option('-p, --print', 'Non-interactive mode')
  .option('--model <model>', 'Specify model')
  .option('--permission-mode <mode>', 'Permission mode')
  // ... dozens of arguments

// 3. Initialize subsystems
await init(); // Auth, config, telemetry
await initializeGrowthBook(); // Feature flags
await initBundledSkills(); // Built-in skills
await initBuiltinPlugins(); // Built-in plugins

// 4. Launch REPL or print mode
launchRepl(options); // Interactive mode
// or
ask({ prompt, tools, ... }); // Non-interactive mode
```

### QueryEngine.ts — Core Engine (45KB)

```typescript
export class QueryEngine {
  // One instance per session
  private mutableMessages: Message[]; // Message history
  private abortController: AbortController; // Abort control
  private totalUsage: NonNullableUsage; // Token usage tracking

  // Core method: submit message and get response stream
  async *submitMessage(prompt): AsyncGenerator<SDKMessage> {
    // 1. Build system prompt
    const { defaultSystemPrompt, userContext, systemContext } =
      await fetchSystemPromptParts({ tools, mainLoopModel, ... });

    // 2. Process user input (slash commands, etc.)
    const { messages, shouldQuery } = await processUserInput(...);

    // 3. Enter query loop
    for await (const message of query({ messages, systemPrompt, ... })) {
      // Handle various message types
      switch (message.type) {
        case 'assistant': yield* normalizeMessage(message); break;
        case 'user': yield* normalizeMessage(message); break;
        case 'stream_event': /* stream events */ break;
        case 'attachment': /* structured output */ break;
      }
    }

    // 4. Return final result
    yield { type: 'result', subtype: 'success', ... };
  }
}
```

## 1.6 Feature Flags System

Claude Code uses `bun:bundle`'s `feature()` function for **compile-time conditional compilation**:

```typescript
import { feature } from 'bun:bundle';

// Decide at compile time whether to include code
const coordinatorModeModule = feature('COORDINATOR_MODE')
  ? require('./coordinator/coordinatorMode.js')
  : null;

const SleepTool = feature('PROACTIVE') || feature('KAIROS')
  ? require('./tools/SleepTool/SleepTool.js').SleepTool
  : null;
```

### Known Feature Flags

| Flag | Function | Status |
|------|----------|--------|
| `COORDINATOR_MODE` | Multi-Agent coordination mode | Experimental |
| `KAIROS` | Assistant mode (long-running) | Experimental |
| `PROACTIVE` | Proactive Agent | Experimental |
| `AGENT_TRIGGERS` | Agent triggers (Cron) | Experimental |
| `HISTORY_SNIP` | History snipping | Experimental |
| `WEB_BROWSER_TOOL` | Browser tool | Experimental |
| `CONTEXT_COLLAPSE` | Context collapse | Experimental |
| `TOKEN_BUDGET` | Token budget management | Experimental |
| `REACTIVE_COMPACT` | Reactive compression | Experimental |
| `TRANSCRIPT_CLASSIFIER` | Auto mode classifier | Experimental |

These flags mark the cutting-edge directions Anthropic is exploring!

## 1.7 Message Type System

Claude Code's message system is the backbone of the entire architecture:

```typescript
// Core message types (src/types/message.ts)
type Message =
  | UserMessage       // User input
  | AssistantMessage   // Claude response
  | SystemMessage      // System messages (compact_boundary, api_error, etc.)
  | ProgressMessage    // Tool execution progress
  | AttachmentMessage  // Attachments (images, files, structured output)
  | StreamEvent        // Stream events (message_start, content_block_delta, etc.)

// UserMessage content can be:
// - string: plain text
// - ContentBlockParam[]: contains text, image, tool_result, etc.

// AssistantMessage content contains:
// - { type: 'text', text: '...' }      // Text response
// - { type: 'tool_use', name, input }  // Tool call
// - { type: 'thinking', thinking: '...' } // Thinking process
```

### Message Flow Diagram

```
User: "Read package.json for me"
  ↓
Assistant: [tool_use: { name: 'Read', input: { file_path: 'package.json' } }]
  ↓
User: [tool_result: { content: '{"name": "claude-code", ...}' }]
  ↓
Assistant: [text: "Here are the contents of package.json: ..."]
```

## 1.8 Key Design Patterns

### 1. AsyncGenerator Pattern
The entire query loop uses `async function*` (async generators), the most core design pattern:

```typescript
// QueryEngine.ts
async *submitMessage(prompt): AsyncGenerator<SDKMessage> {
  for await (const message of query({ ... })) {
    yield message; // Yield messages one by one
  }
}

// query.ts
async function* query(params): AsyncGenerator<Message> {
  while (true) {
    // Call API
    for await (const event of apiStream) {
      yield event; // Stream yield
    }
    // Execute tools
    yield* runTools(...);
    // Check if should stop
    if (shouldStop) break;
  }
}
```

**Why AsyncGenerator?**
- Supports streaming output (typewriter effect)
- Memory-friendly (no need to buffer all messages)
- Supports abort
- Naturally expresses "loop until done" semantics

### 2. buildTool Factory Pattern
All tools are created through `buildTool()`, which auto-fills defaults:

```typescript
export const GlobTool = buildTool({
  name: 'Glob',
  inputSchema: z.object({ pattern: z.string() }),
  async call(input, context) { ... },
  // The following are provided by buildTool defaults:
  // isEnabled: () => true
  // isReadOnly: () => false
  // isConcurrencySafe: () => false
  // checkPermissions: () => ({ behavior: 'allow' })
});
```

### 3. Dead Code Elimination
Compile-time code elimination via `feature()` + `require()`:

```typescript
// If COORDINATOR_MODE is false, the entire require and related code
// is completely removed at compile time, not appearing in the final bundle
const module = feature('COORDINATOR_MODE')
  ? require('./coordinator/coordinatorMode.js')
  : null;
```

## 1.9 context.ts — Context Management

At the start of each conversation, Claude Code collects two types of context:

### getUserContext() — User Context
```typescript
export const getUserContext = memoize(async () => {
  // 1. Read CLAUDE.md files (project-level + user-level)
  const claudeMd = getClaudeMds(await getMemoryFiles());

  // 2. Current date
  const currentDate = `Today's date is ${getLocalISODate()}.`;

  return { claudeMd, currentDate };
});
```

### getSystemContext() — System Context
```typescript
export const getSystemContext = memoize(async () => {
  // 1. Git status (branch, status, recent commits)
  const gitStatus = await getGitStatus();
  // Includes: current branch, main branch, git status --short, last 5 commits

  return { gitStatus };
});
```

**Key point:** Both functions use `memoize`, meaning they're computed only once per session.
This is why the git status comment says "this status is a snapshot in time".

## Chapter Summary

| Concept | Key File | One-line Summary |
|---------|----------|-----------------|
| CLI Entry | `main.tsx` | Commander.js parses args, initializes all subsystems |
| Core Engine | `QueryEngine.ts` | Manages session state, drives query loop |
| Query Loop | `query.ts` | LLM call → tool execution → re-call core loop |
| Tool Types | `Tool.ts` | Defines interface all tools must implement |
| Tool Registry | `tools.ts` | Assembles, filters, and merges tool pool |
| Context | `context.ts` | Collects CLAUDE.md and git information |

### Next Chapter Preview
Chapter 2 will dive deep into the **Tool System** — Claude's "hands and feet" —
starting from the simplest GlobTool to understand how tools are defined, registered, and executed.
