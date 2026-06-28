/**
 * Local slash-command registry for the autocomplete menu. Some run entirely
 * client-side (clear/help/quit); model/mode map to control_request ops the
 * Python server already supports (set_model / set_permission_mode).
 */
export interface SlashCommand {
  name: string
  description: string
  /** how the command is handled when submitted. */
  kind:
    | 'clear'
    | 'help'
    | 'quit'
    | 'control'
    | 'context'
    | 'compact'
    | 'rewind'
    | 'resume'
    | 'branch'
    | 'rename'
    | 'theme'
    | 'vim'
    | 'mcp'
    | 'cost'
    | 'init'
    | 'permissions'
    | 'memory'
    | 'agents'
    | 'config'
    | 'skills'
    | 'files'
    | 'diff'
    | 'stats'
    | 'prompt'
    | 'export'
    | 'copy'
    | 'doctor'
    | 'send'
  /** for kind:'control' — the control_request subtype. */
  control?: string
  /** for kind:'prompt' — the LLM-visible text the command expands to. */
  promptText?: string
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { name: '/help', description: 'Show keys and commands', kind: 'help' },
  { name: '/clear', description: 'Clear the transcript', kind: 'clear' },
  { name: '/model', description: 'Switch model: /model <name>', kind: 'control', control: 'set_model' },
  {
    name: '/mode',
    description: 'Permission mode: /mode <default|acceptEdits|plan|…>',
    kind: 'control',
    control: 'set_permission_mode',
  },
  { name: '/context', description: 'Show context-window usage by category', kind: 'context' },
  { name: '/cost', description: 'Show session cost and token usage', kind: 'cost' },
  { name: '/usage', description: 'Show token usage and context window', kind: 'cost' },
  { name: '/compact', description: 'Summarize & compact the conversation: /compact [instructions]', kind: 'compact' },
  { name: '/rewind', description: 'Undo the last turn(s): /rewind [N]', kind: 'rewind' },
  { name: '/resume', description: 'Resume a saved session', kind: 'resume' },
  { name: '/branch', description: 'Fork the current session to a new one', kind: 'branch' },
  { name: '/rename', description: 'Name the current session: /rename <name>', kind: 'rename' },
  { name: '/theme', description: 'Switch color theme: /theme <dark|light>', kind: 'theme' },
  { name: '/vim', description: 'Toggle vim editing mode', kind: 'vim' },
  { name: '/export', description: 'Save the transcript to a markdown file', kind: 'export' },
  { name: '/copy', description: "Copy the last response to the clipboard", kind: 'copy' },
  { name: '/mcp', description: 'List connected MCP servers and their tools', kind: 'mcp' },
  { name: '/permissions', description: 'Show active permission mode and rules', kind: 'permissions' },
  { name: '/agents', description: 'List available subagent types', kind: 'agents' },
  { name: '/skills', description: 'List available skills', kind: 'skills' },
  { name: '/files', description: 'List files in the working directory', kind: 'files' },
  { name: '/config', description: 'Show model, mode, and available models', kind: 'config' },
  { name: '/diff', description: 'Show the working-tree git diff', kind: 'diff' },
  { name: '/stats', description: 'Show session statistics', kind: 'stats' },
  { name: '/init', description: 'Analyze the codebase and create/improve CLAUDE.md', kind: 'init' },
  {
    name: '/review',
    description: 'Review the current changes for bugs and quality',
    kind: 'prompt',
    promptText:
      'Review the current changes (run `git diff`) for bugs, edge cases, and quality issues. ' +
      'Be specific with file:line references and suggest concrete fixes.',
  },
  {
    name: '/security-review',
    description: 'Security review of the current changes',
    kind: 'prompt',
    promptText:
      'Perform a security review of the current changes (run `git diff`), focusing on injection, ' +
      'auth/authz, secrets, SSRF, and unsafe deserialization. Report findings with severity and fixes.',
  },
  {
    name: '/commit-message',
    description: 'Draft a commit message for the current changes',
    kind: 'prompt',
    promptText:
      'Run `git diff --staged` (fall back to `git diff`) and draft a concise Conventional Commits ' +
      'message for the changes. Output only the commit message.',
  },
  { name: '/memory', description: 'Show the loaded CLAUDE.md memory files', kind: 'memory' },
  { name: '/doctor', description: 'Show connection + session diagnostics', kind: 'doctor' },
  { name: '/quit', description: 'Exit the TUI', kind: 'quit' },
]

/** Commands whose name starts with the typed token (case-insensitive). */
export function matchSlash(input: string): SlashCommand[] {
  const tok = input.split(/\s/)[0]?.toLowerCase() ?? ''
  if (!tok.startsWith('/')) return []
  return SLASH_COMMANDS.filter((c) => c.name.startsWith(tok))
}

/** Resolve the full typed input to a command (exact name match on first token). */
export function resolveSlash(input: string): SlashCommand | undefined {
  const tok = input.trim().split(/\s/)[0]?.toLowerCase() ?? ''
  return SLASH_COMMANDS.find((c) => c.name === tok)
}
