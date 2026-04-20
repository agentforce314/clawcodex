# Chapter 2: Tool System — The Agent's "Hands and Feet"

## Learning Objectives
1. Understand the complete Tool interface contract (purpose of each method)
2. Starting from the simplest GlobTool, master the standard tool structure
3. Understand the complete flow of tool registration, filtering, and assembly
4. Understand the tool execution lifecycle

---

## 2.1 Tool Interface Overview (Tool.ts)

**Building intuition first**: In Claude Code, a "tool" is any concrete action the AI can perform — reading a file, running a shell command, searching code, etc. Every tool must follow a unified "interface contract", like how all electrical appliances must use a standard plug. This contract defines: what the tool is called, what input it accepts, how it executes, what permissions it needs, and how to display results in the terminal.

`Tool.ts` (28KB, 793 lines) defines the interface all tools must follow. This is the "constitution" of the entire tool system.

### Core Interface Structure

The full Tool interface is shown below. There are many fields, but they're organized into 7 groups — you only need to focus on 1-2 core methods per group:

```typescript
export type Tool<Input, Output, Progress> = {
  // ========== Identity ==========
  // Every tool has a unique name, like 'Glob', 'Bash', 'Read'
  readonly name: string;
  aliases?: string[];             // Backward-compatible aliases after renaming
  searchHint?: string;            // Keywords for ToolSearch matching

  // ========== Schema Definition ==========
  // Defines what input the tool accepts and what output it produces
  readonly inputSchema: Input;    // Zod schema for input validation
  outputSchema?: z.ZodType;       // Output schema (optional)
  readonly inputJSONSchema?: object; // JSON Schema format for MCP tools

  // ========== Core Execution ==========
  // The main method — this is where the tool actually does its work
  call(args, context, canUseTool, parentMessage, onProgress?)
    : Promise<ToolResult<Output>>;

  // ========== Permissions & Security ==========
  // These methods control whether the tool is allowed to run
  validateInput?(input, context): Promise<ValidationResult>;
  checkPermissions(input, context): Promise<PermissionResult>;
  isReadOnly(input): boolean;           // Read-only? (affects concurrency)
  isDestructive?(input): boolean;       // Irreversible? (e.g. rm -rf)
  isEnabled(): boolean;                 // Is this tool currently active?
  isConcurrencySafe(input): boolean;    // Can run in parallel with others?

  // ========== Prompt Generation ==========
  // Tells the AI model what this tool does and how to use it
  description(input, options): Promise<string>;
  prompt(options): Promise<string>;

  // ========== UI Rendering ==========
  // How the tool's actions and results appear in the terminal
  renderToolUseMessage(input, options): React.ReactNode;
  renderToolResultMessage?(output, progress, options): React.ReactNode;
  renderToolUseProgressMessage?(progress, options): React.ReactNode;
  renderToolUseRejectedMessage?(input, options): React.ReactNode;
  renderToolUseErrorMessage?(result, options): React.ReactNode;

  // ========== Serialization ==========
  // Converts results to API-compatible format
  mapToolResultToToolResultBlockParam(output, toolUseID): ToolResultBlockParam;
  toAutoClassifierInput(input): unknown;

  // ========== Helper Methods ==========
  userFacingName(input): string;        // Display name for users
  getPath?(input): string;              // File path being operated on
  getToolUseSummary?(input): string;    // One-line summary for mobile UI
  getActivityDescription?(input): string; // Spinner text while running
  maxResultSizeChars: number;           // Max chars in result (prevents huge outputs)
};
```

## 2.2 buildTool() — The Tool Factory Function

**Building intuition first**: The Tool interface above has 20+ methods. If every tool had to implement all of them from scratch, that would be tedious. `buildTool()` is a "factory" — it provides safe defaults, so developers only need to override what they care about. Notably, all security-related defaults are **maximally conservative** (if unsure, don't parallelize; if unsure, assume it writes) — this ensures that even if a developer forgets to set a property, it won't cause security issues.

All tools are created through `buildTool()`, which provides safe defaults:

```typescript
// Defaults in Tool.ts
const TOOL_DEFAULTS = {
  isEnabled: () => true,           // Enabled by default
  isConcurrencySafe: () => false,  // Not safe by default (conservative)
  isReadOnly: () => false,         // Assume writes by default
  isDestructive: () => false,      // Non-destructive by default
  checkPermissions: (input) =>     // Allow by default (delegate to general permission system)
    Promise.resolve({ behavior: 'allow', updatedInput: input }),
  toAutoClassifierInput: () => '', // Skip classifier by default
  userFacingName: () => '',        // Empty by default
};

export function buildTool(def) {
  return {
    ...TOOL_DEFAULTS,
    userFacingName: () => def.name, // Default to tool name
    ...def,                         // User definitions override defaults
  };
}
```

**Design Philosophy: Fail-Closed (Security First)**
- `isConcurrencySafe` defaults to `false` → if unsure, don't parallelize
- `isReadOnly` defaults to `false` → if unsure, assume it writes
- This ensures new tools won't cause security issues even if these properties are forgotten

## 2.3 The Simplest Tool: GlobTool Complete Analysis

GlobTool is one of the simplest built-in tools, with only ~200 lines of code. Let's understand it line by line:

### File Structure
```
src/tools/GlobTool/
├── GlobTool.ts    # Core logic (199 lines)
├── prompt.ts      # Tool description (8 lines)
└── UI.tsx         # Terminal rendering component
```

### prompt.ts — Tool Description for the LLM
```typescript
export const GLOB_TOOL_NAME = 'Glob';

export const DESCRIPTION = `
- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple
  rounds of globbing and grepping, use the Agent tool instead
`;
```

**Note the last line!** It guides the LLM to use AgentTool instead of GlobTool for complex searches.
This is **prompt engineering applied to tool descriptions**.

### GlobTool.ts — Core Implementation Section by Section

#### Part 1: Input/Output Schema

```typescript
// Define input parameters using Zod
const inputSchema = lazySchema(() =>
  z.strictObject({
    pattern: z.string()
      .describe('The glob pattern to match files against'),
    path: z.string().optional()
      .describe('The directory to search in. If not specified, the current '
        + 'working directory will be used. IMPORTANT: Omit this field to use '
        + 'the default directory. DO NOT enter "undefined" or "null"'),
  }),
);

// Output Schema
const outputSchema = lazySchema(() =>
  z.object({
    durationMs: z.number(),           // Execution duration
    numFiles: z.number(),             // Number of files found
    filenames: z.array(z.string()),   // File path list
    truncated: z.boolean(),           // Whether results were truncated
  }),
);
```

**Key Details:**
- `lazySchema()` defers initialization to avoid circular dependencies during module loading
- `z.strictObject()` disallows extra fields (stricter than `z.object()`)
- `.describe()` text is sent to the LLM — it's part of the prompt!

#### Part 2: Tool Property Declarations

```typescript
export const GlobTool = buildTool({
  name: GLOB_TOOL_NAME,                    // 'Glob'
  searchHint: 'find files by name pattern or wildcard',
  maxResultSizeChars: 100_000,             // Max 100K chars for results

  // Property methods
  isConcurrencySafe() { return true; },    // Can execute concurrently
  isReadOnly() { return true; },           // Read-only operation

  isSearchOrReadCommand() {
    return { isSearch: true, isRead: false }; // Search operation (UI collapse)
  },

  // Get operation path
  getPath({ path }) {
    return path ? expandPath(path) : getCwd();
  },

  // Auto classifier input
  toAutoClassifierInput(input) {
    return input.pattern; // Only pass pattern, not path
  },
});
```

**Why does `isConcurrencySafe` return `true`?**
Because Glob only reads the filesystem without modifying any state — multiple Globs can safely run in parallel.

#### Part 3: Input Validation

```typescript
async validateInput({ path }): Promise<ValidationResult> {
  if (path) {
    const absolutePath = expandPath(path);

    // Security check: block UNC paths (prevent NTLM credential leaks)
    if (absolutePath.startsWith('\\\\') || absolutePath.startsWith('//')) {
      return { result: true }; // Silently skip, no error
    }

    // Check if path exists
    let stats;
    try {
      stats = await fs.stat(absolutePath);
    } catch (e) {
      if (isENOENT(e)) {
        // Path doesn't exist, try to suggest correct path
        const suggestion = await suggestPathUnderCwd(absolutePath);
        return {
          result: false,
          message: `Directory does not exist: ${path}. Did you mean ${suggestion}?`,
          errorCode: 1,
        };
      }
      throw e;
    }

    // Check if it's a directory
    if (!stats.isDirectory()) {
      return { result: false, message: `Path is not a directory: ${path}`, errorCode: 2 };
    }
  }
  return { result: true };
},
```

**Validation Flow:**
1. UNC path security check (Windows-specific security issue)
2. Path existence check
3. Path type check (must be a directory)
4. Smart suggestion ("Did you mean...?")

#### Part 4: Permission Check

```typescript
async checkPermissions(input, context): Promise<PermissionDecision> {
  const appState = context.getAppState();
  return checkReadPermissionForTool(
    GlobTool,
    input,
    appState.toolPermissionContext,
  );
},
```

GlobTool is a read-only tool, so it uses the generic `checkReadPermissionForTool()`.
This function checks:
- Whether the path is within allowed working directories
- Whether any deny/ask/allow rules match
- UNC path protection

#### Part 5: Core Execution Logic

```typescript
async call(input, { abortController, getAppState, globLimits }) {
  const start = Date.now();
  const appState = getAppState();
  const limit = globLimits?.maxResults ?? 100; // Default max 100 results

  // Execute glob search
  const { files, truncated } = await glob(
    input.pattern,
    GlobTool.getPath(input),
    { limit, offset: 0 },
    abortController.signal,          // Supports abort
    appState.toolPermissionContext,   // Permission context
  );

  // Convert to relative paths (saves tokens)
  const filenames = files.map(toRelativePath);

  return {
    data: {
      filenames,
      durationMs: Date.now() - start,
      numFiles: filenames.length,
      truncated,
    },
  };
},
```

**Key Design:**
- `abortController.signal` — user can abort at any time
- `toRelativePath` — converts absolute paths to relative, saving token consumption
- Results limited to 100 files to prevent token explosion

#### Part 6: Result Serialization

```typescript
mapToolResultToToolResultBlockParam(output, toolUseID) {
  // No results
  if (output.filenames.length === 0) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: 'No files found',
    };
  }
  // Has results: one filename per line
  return {
    tool_use_id: toolUseID,
    type: 'tool_result',
    content: [
      ...output.filenames,
      ...(output.truncated
        ? ['(Results are truncated. Consider using a more specific path or pattern.)']
        : []),
    ].join('\n'),
  };
},
```

This method converts tool output to Anthropic API's `tool_result` format.
Note the truncation hint — it guides the LLM to use more precise search criteria.

## 2.4 Tool Registry (tools.ts)

`tools.ts` (390 lines) is the registration center for all tools. It determines which tools are visible to the LLM.

### getAllBaseTools() — Get All Base Tools

```typescript
export function getAllBaseTools(): Tools {
  return [
    // Core tools (always available)
    AgentTool,            // Sub-Agent
    TaskOutputTool,       // Task output
    BashTool,             // Shell commands
    FileReadTool,         // Read files
    FileEditTool,         // Edit files
    FileWriteTool,        // Write files
    NotebookEditTool,     // Jupyter editing
    WebFetchTool,         // Fetch web pages
    TodoWriteTool,        // Todo management
    WebSearchTool,        // Web search
    TaskStopTool,         // Stop task
    AskUserQuestionTool,  // Ask user
    SkillTool,            // Skill invocation
    EnterPlanModeTool,    // Enter plan mode
    ExitPlanModeV2Tool,   // Exit plan mode
    BriefTool,            // Brief mode

    // Conditional tools (based on environment/feature flags)
    ...(hasEmbeddedSearchTools() ? [] : [GlobTool, GrepTool]),
    ...(isAgentSwarmsEnabled() ? [TeamCreateTool, TeamDeleteTool] : []),
    ...(isWorktreeModeEnabled() ? [EnterWorktreeTool, ExitWorktreeTool] : []),
    ...(isToolSearchEnabledOptimistic() ? [ToolSearchTool] : []),

    // Experimental tools (feature flag controlled)
    ...(WebBrowserTool ? [WebBrowserTool] : []),
    ...(SleepTool ? [SleepTool] : []),
    ...cronTools,

    // MCP resource tools
    ListMcpResourcesTool,
    ReadMcpResourceTool,
  ];
}
```

**Note the `hasEmbeddedSearchTools()` logic:**
Anthropic's internal build (ant build) embeds ripgrep and other tools into the Bun binary.
In that case, Glob/Grep tools aren't needed because BashTool's find/grep are aliased to fast tools.

### getTools() — Get Filtered Tools

```typescript
export const getTools = (permissionContext): Tools => {
  // Simple mode: only Bash + Read + Edit
  if (isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)) {
    return filterToolsByDenyRules([BashTool, FileReadTool, FileEditTool], permissionContext);
  }

  // Normal mode
  const tools = getAllBaseTools();

  // 1. Filter tools forbidden by deny rules
  let allowedTools = filterToolsByDenyRules(tools, permissionContext);

  // 2. Hide raw tools in REPL mode (they're available inside the VM)
  if (isReplModeEnabled()) {
    allowedTools = allowedTools.filter(tool => !REPL_ONLY_TOOLS.has(tool.name));
  }

  // 3. Filter disabled tools
  return allowedTools.filter(tool => tool.isEnabled());
};
```

### assembleToolPool() — Merge Built-in and MCP Tools

```typescript
export function assembleToolPool(permissionContext, mcpTools): Tools {
  const builtInTools = getTools(permissionContext);
  const allowedMcpTools = filterToolsByDenyRules(mcpTools, permissionContext);

  // Built-in tools come first (prompt cache stability)
  // Built-in tools take priority on name collision (uniqBy keeps first)
  return uniqBy(
    [...builtInTools].sort(byName)
      .concat(allowedMcpTools.sort(byName)),
    'name',
  );
}
```

**Why sort?**
Anthropic API has a prompt cache mechanism. Changes in tool list order cause cache misses.
Sorting ensures stable ordering, maximizing cache hit rate.

## 2.5 Tool Execution Lifecycle

When Claude returns a `tool_use` block, the execution flow is:

```
Claude API returns: { type: 'tool_use', name: 'Glob', input: { pattern: '**/*.ts' } }
  │
  ▼
1. Find tool: findToolByName(tools, 'Glob') → GlobTool
  │
  ▼
2. Parse input: tool.inputSchema.parse(input)
  │   └── Zod validates input format
  │
  ▼
3. Validate input: tool.validateInput(parsedInput, context)
  │   └── Check if path exists, is a directory, etc.
  │
  ▼
4. Permission check: hasPermissionsToUseTool(tool, input, context)
  │   ├── 4a. Check deny rules → forbidden?
  │   ├── 4b. Check ask rules → need to ask?
  │   ├── 4c. tool.checkPermissions() → tool-specific check
  │   ├── 4d. Check permission mode → bypass/plan/default?
  │   ├── 4e. Check allow rules → authorized?
  │   └── 4f. Default → show permission confirmation dialog
  │
  ▼
5. PreToolUse Hooks: execute pre-hooks
  │   └── User-defined hook scripts
  │
  ▼
6. Execute tool: tool.call(input, context, canUseTool, parentMessage, onProgress)
  │   └── Actually execute glob search
  │
  ▼
7. PostToolUse Hooks: execute post-hooks
  │
  ▼
8. Serialize result: tool.mapToolResultToToolResultBlockParam(output, toolUseID)
  │   └── Convert to API format tool_result
  │
  ▼
9. Append to message list, continue query loop
```

## 2.6 ToolUseContext — Tool Execution Context

Every tool's `call()` method receives a `ToolUseContext` containing everything needed for execution:

```typescript
type ToolUseContext = {
  // ========== Configuration ==========
  options: {
    commands: Command[];              // Available slash commands
    tools: Tools;                     // Available tool list
    mainLoopModel: string;            // Current model
    mcpClients: MCPServerConnection[]; // MCP connections
    isNonInteractiveSession: boolean; // Non-interactive mode?
    agentDefinitions: AgentDefinitionsResult; // Agent definitions
  };

  // ========== State Management ==========
  getAppState(): AppState;            // Get global state
  setAppState(f): void;               // Update global state
  abortController: AbortController;   // Abort controller
  messages: Message[];                // Current message history

  // ========== File Cache ==========
  readFileState: FileStateCache;      // File read cache (LRU)

  // ========== UI Callbacks ==========
  setToolJSX?: SetToolJSXFn;         // Set tool UI
  setInProgressToolUseIDs: (f) => void; // Mark in-progress tools
  setResponseLength: (f) => void;     // Update response length

  // ========== Agent Related ==========
  agentId?: AgentId;                  // Sub-Agent ID
  agentType?: string;                 // Agent type name
};
```

**Why `getAppState()` instead of passing state directly?**
Because state may be modified by other concurrent operations during tool execution.
Each call to `getAppState()` gets the latest state.

## 2.7 All Built-in Tools at a Glance

| Tool Name | File | Function | Read-Only | Concurrency Safe |
|-----------|------|----------|-----------|-----------------|
| **Bash** | `BashTool/` | Execute shell commands | | |
| **Read** | `FileReadTool/` | Read file contents | ✓ | ✓ |
| **Edit** | `FileEditTool/` | Edit files (diff) | | |
| **Write** | `FileWriteTool/` | Write/create files | | |
| **Glob** | `GlobTool/` | Filename pattern matching | ✓ | ✓ |
| **Grep** | `GrepTool/` | Text content search | ✓ | ✓ |
| **Agent** | `AgentTool/` | Launch sub-Agent | | |
| **WebSearch** | `WebSearchTool/` | Web search | ✓ | ✓ |
| **WebFetch** | `WebFetchTool/` | Fetch web content | ✓ | ✓ |
| **TodoWrite** | `TodoWriteTool/` | Manage Todo list | | |
| **Notebook** | `NotebookEditTool/` | Edit Jupyter | | |
| **Skill** | `SkillTool/` | Invoke skills | | |
| **AskUser** | `AskUserQuestionTool/` | Ask user | | |
| **TaskStop** | `TaskStopTool/` | Stop current task | | |
| **TaskOutput** | `TaskOutputTool/` | Output task result | | |
| **SendMessage** | `SendMessageTool/` | Inter-Agent communication | | |
| **ToolSearch** | `ToolSearchTool/` | Search available tools | ✓ | ✓ |
| **Brief** | `BriefTool/` | Toggle brief mode | | |

### Experimental Tools (Feature Flag Controlled)

| Tool Name | Flag | Function |
|-----------|------|----------|
| SleepTool | `PROACTIVE`/`KAIROS` | Wait for specified time |
| CronTools | `AGENT_TRIGGERS` | Scheduled task management |
| WebBrowserTool | `WEB_BROWSER_TOOL` | Browser automation |
| MonitorTool | `MONITOR_TOOL` | Monitoring tool |
| SnipTool | `HISTORY_SNIP` | History snipping |

## 2.8 ToolResult & contextModifier — How Tools Affect Subsequent Execution

A tool's `call()` method returns `ToolResult<T>`, which contains not just data but can also modify subsequent execution context:

```typescript
// Definition in Tool.ts (lines 259-272)
export type ToolResult<T> = {
  data: T;                    // Tool output data
  newMessages?: (             // Inject additional messages
    | UserMessage | AssistantMessage
    | AttachmentMessage | SystemMessage
  )[];
  // Only effective for non-concurrency-safe tools!
  contextModifier?: (context: ToolUseContext) => ToolUseContext;
  mcpMeta?: {                 // MCP protocol metadata passthrough
    _meta?: Record<string, unknown>;
    structuredContent?: Record<string, unknown>;
  };
};
```

**The Power of contextModifier:**
```typescript
// For example, FileEditTool can update file cache after editing
return {
  data: editResult,
  contextModifier: (ctx) => ({
    ...ctx,
    readFileState: ctx.readFileState.set(filePath, newContent),
  }),
};
```

**Important Limitation:** `contextModifier` only works for non-concurrency-safe tools.
Because concurrent tools execute simultaneously, the application order of modifiers cannot be guaranteed.
This is an intentional design constraint that avoids race conditions.

In `toolOrchestration.ts` you can see this logic:
```typescript
// Serial tools: apply contextModifier immediately
if (update.newContext) {
  currentContext = update.newContext;
}

// Parallel tools: defer until batch ends, then apply in order
if (update.contextModifier) {
  queuedContextModifiers[toolUseID].push(modifyContext);
}
// After batch ends:
for (const block of blocks) {
  for (const modifier of queuedContextModifiers[block.id]) {
    currentContext = modifier(currentContext);
  }
}
```

## 2.9 Feature Flag Conditional Loading — `feature()` and `require()` in Source

tools.ts heavily uses conditional loading patterns, leveraging Bun's tree-shaking mechanism:

```typescript
// Actual code in tools.ts
import { feature } from 'bun:bundle';

// feature() is replaced with true/false at compile time
// When false, the entire require() block is tree-shaken away
const SleepTool = feature('PROACTIVE') || feature('KAIROS')
  ? require('./tools/SleepTool/SleepTool.js').SleepTool
  : null;

const WebBrowserTool = feature('WEB_BROWSER_TOOL')
  ? require('./tools/WebBrowserTool/WebBrowserTool.js').WebBrowserTool
  : null;

// Ant-only tools (Anthropic internal build)
const REPLTool = process.env.USER_TYPE === 'ant'
  ? require('./tools/REPLTool/REPLTool.js').REPLTool
  : null;

// Lazy loading to break circular dependencies
const getTeamCreateTool = () =>
  require('./tools/TeamCreateTool/TeamCreateTool.js').TeamCreateTool;
```

**Three Conditional Loading Patterns:**

| Pattern | Use Case | Example |
|---------|----------|---------|
| `feature('FLAG')` | Compile-time Feature Flag | SleepTool, WebBrowserTool |
| `process.env.USER_TYPE === 'ant'` | Runtime environment variable | REPLTool, ConfigTool |
| `() => require(...)` | Lazy loading function | TeamCreateTool (breaks circular deps) |

**Known Feature Flags (extracted from source):**

| Flag | Function | Status |
|------|----------|--------|
| `PROACTIVE` | Proactive Agent (Sleep wait) | Experimental |
| `KAIROS` | Long-running Agent (cron, push notifications, GitHub Webhooks) | Experimental |
| `AGENT_TRIGGERS` | Agent triggers (Cron scheduled tasks) | Experimental |
| `AGENT_TRIGGERS_REMOTE` | Remote Agent triggers | Experimental |
| `COORDINATOR_MODE` | Coordinator mode (multi-Worker) | Experimental |
| `WEB_BROWSER_TOOL` | Browser automation tool | Experimental |
| `MONITOR_TOOL` | Monitoring tool | Experimental |
| `HISTORY_SNIP` | History snipping (SnipTool) | Experimental |
| `CONTEXT_COLLAPSE` | Context collapse | Experimental |
| `REACTIVE_COMPACT` | Reactive compression | Experimental |
| `TRANSCRIPT_CLASSIFIER` | Safety classifier (auto mode) | Experimental |
| `TOKEN_BUDGET` | Token budget management | Experimental |
| `CACHED_MICROCOMPACT` | Cached micro-compression | Experimental |
| `WORKFLOW_SCRIPTS` | Workflow scripts | Experimental |
| `UDS_INBOX` | Unix Domain Socket communication | Experimental |
| `OVERFLOW_TEST_TOOL` | Overflow test tool | Testing |
| `TERMINAL_PANEL` | Terminal panel capture | Experimental |
| `EXPERIMENTAL_SKILL_SEARCH` | Skill search | Experimental |
| `TEMPLATES` | Task templates | Experimental |

## 2.10 Prompt Engineering Techniques in Tools

Claude Code uses extensive prompt engineering techniques in tool descriptions:

### Technique 1: Guide LLM to Choose the Right Tool
```typescript
// GlobTool prompt.ts
'When you are doing an open ended search that may require multiple\n'
'rounds of globbing and grepping, use the Agent tool instead'
// → Guides LLM to use AgentTool for complex searches
```

### Technique 2: Embed Instructions in Zod describe
```typescript
// GlobTool inputSchema
path: z.string().optional().describe(
  'IMPORTANT: Omit this field to use the default directory. '
  + 'DO NOT enter \"undefined\" or \"null\"'
)
// → Prevents LLM from passing 'undefined' string
```

### Technique 3: Embed Guidance in Results
```typescript
// GlobTool truncation hint
'(Results are truncated. Consider using a more specific path or pattern.)'
// → Guides LLM to narrow search scope
```

### Technique 4: searchHint Optimizes ToolSearch
```typescript
// Each tool's searchHint helps ToolSearch find the right tool
GlobTool: searchHint: 'find files by name pattern or wildcard'
GrepTool: searchHint: 'search file contents by regex pattern'
WebFetchTool: searchHint: 'download web page content from URL'
// → When ToolSearch searches 'download', it finds WebFetchTool
```

### Technique 5: isSearchOrReadCommand Controls UI Collapse
```typescript
// Search/read operations auto-collapse in UI, reducing visual noise
isSearchOrReadCommand() {
  return { isSearch: true, isRead: false, isList: false };
}
// BashTool dynamically determines based on command content:
// 'grep ...' → isSearch: true
// 'cat ...'  → isRead: true
// 'ls ...'   → isList: true
// 'rm ...'   → all false (don't collapse)
```

## 2.11 maxResultSizeChars — Large Result Handling

When tool output exceeds `maxResultSizeChars`, results are persisted to disk:

```
Tool output (e.g., 200KB grep results)
  │
  ▼
resultText.length > tool.maxResultSizeChars ?
  │
  ├── YES → Save to ~/.claude/projects/{cwd}/tool-results/{uuid}.txt
  │         Return preview + file path to Claude
  │         "[Full output saved to: /path/to/file]\n"
  │         "Use Read tool to view the full output if needed."
  │
  └── NO  → Return complete result directly
```

**maxResultSizeChars Settings by Tool:**

| Tool | Limit | Reason |
|------|-------|--------|
| GlobTool | 100,000 | File lists can be very long |
| GrepTool | 100,000 | Search results can be numerous |
| BashTool | 30,000 | Command output can be large |
| FileReadTool | **Infinity** | No persistence! Avoids Read→file→Read loop |
| WebFetchTool | 50,000 | Web content can be large |

**Why is FileReadTool set to Infinity?**
If Read's results were saved to a file, Claude would use Read to read that file,
whose results would also be saved... forming an infinite loop. So Read controls its own output size.

**applyToolResultBudget — Aggregate Budget Control (in query.ts):**
```typescript
// Beyond individual tool maxResultSizeChars, there's a global aggregate budget
// Applied in each iteration of the query loop
messagesForQuery = await applyToolResultBudget(
  messagesForQuery,
  toolUseContext.contentReplacementState,
  persistReplacements ? records => void recordContentReplacement(...) : undefined,
  // Skip tools with maxResultSizeChars = Infinity
  new Set(tools.filter(t => !Number.isFinite(t.maxResultSizeChars)).map(t => t.name)),
);
```

## Chapter Summary

| Concept | Key Point |
|---------|-----------|
| Tool Interface | 28+ methods covering execution, permissions, UI, serialization |
| buildTool() | Factory function with safe defaults (fail-closed) |
| Tool Directory Structure | `XxxTool.ts` + `prompt.ts` + `UI.tsx` trio |
| Tool Registration | `getAllBaseTools()` → `getTools()` → `assembleToolPool()` |
| Execution Lifecycle | Find → Parse → Validate → Permission → Hook → Execute → Hook → Serialize |
| ToolUseContext | Complete execution context with state, config, callbacks |
| ToolResult | Returns data and can modify subsequent context via contextModifier |
| Feature Flags | 18+ experimental flags control conditional tool loading |
| Prompt Engineering | 5 techniques guide LLM to use tools correctly |
| Large Result Handling | Auto-persist to disk when exceeding maxResultSizeChars |

### Next Chapter Preview
Chapter 3 will dive deep into the **Permission & Security System** — how Claude Code prevents AI from executing dangerous operations.
