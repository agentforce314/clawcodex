/**
 * Local slash-command registry for the autocomplete menu. Some run entirely
 * client-side (clear/help/quit); model/mode map to control_request ops the
 * Python server already supports (set_model / set_permission_mode).
 */
export interface SlashCommand {
  name: string
  description: string
  /** how the command is handled when submitted. */
  kind: 'clear' | 'help' | 'quit' | 'control' | 'context' | 'compact' | 'send'
  /** for kind:'control' — the control_request subtype. */
  control?: string
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
  { name: '/compact', description: 'Summarize & compact the conversation: /compact [instructions]', kind: 'compact' },
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
