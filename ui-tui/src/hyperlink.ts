/**
 * OSC 8 terminal hyperlinks (the original's clickable refs, inventory §8) with
 * capability detection — emit the escape only on terminals known to render it,
 * otherwise return the text unchanged so unsupported terminals show no garbage.
 */
const OSC = '\x1b]8;;'
const ST = '\x1b\\'

let _supported: boolean | null = null
export function supportsHyperlinks(): boolean {
  if (_supported !== null) return _supported
  const tp = process.env['TERM_PROGRAM'] ?? ''
  const term = process.env['TERM'] ?? ''
  _supported =
    ['iTerm.app', 'WezTerm', 'ghostty', 'vscode', 'Hyper', 'rio'].includes(tp) ||
    term === 'xterm-kitty' ||
    !!process.env['VTE_VERSION'] ||
    !!process.env['KITTY_WINDOW_ID']
  return _supported
}

/** Wrap `text` in an OSC 8 hyperlink to `url` (no-op on unsupported terminals). */
export function hyperlink(text: string, url: string): string {
  if (!url || !supportsHyperlinks()) return text
  return `${OSC}${url}${ST}${text}${OSC}${ST}`
}
