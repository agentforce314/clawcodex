const TERMUX_SAFE_PROMPT = '>'

// The composer marker is JUST the glyph — no provider/profile prefix. The
// session-stats line under the composer already names the provider (and
// model/cwd), so prefixing it here would say the same thing twice one row
// apart. Shell mode keeps its `$`; Termux keeps a strictly single-cell ASCII
// marker (decorative glyphs render with ambiguous width there and leave
// stale arrow artifacts while typing).
export function composerPromptText(prompt: string, shellMode = false, termuxMode = false): string {
  if (shellMode) {
    return '$'
  }

  return termuxMode ? TERMUX_SAFE_PROMPT : prompt
}
