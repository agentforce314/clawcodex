/** The active-turn path carries text; uploads and commands need a full turn. */
export function steeringUnavailableReason(text: string): string | null {
  if (!text.trim()) return 'Write a message to send now.'
  if (text.trimStart().startsWith('/') || /\[(?:Image|File) #\d+\]/.test(text)) {
    return 'Queue attachments and commands for the next turn.'
  }
  return null
}
