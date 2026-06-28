/**
 * Configurable keybindings (the original's keybindings/loadUserBindings, §8).
 * Maps named actions → key combos, overridable via ~/.clawcodex/keybindings.json
 * (e.g. {"external-editor": "ctrl+x"}). Unset actions fall back to defaults, so
 * behavior is unchanged when no config exists (regression-safe).
 */
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const DEFAULTS: Record<string, string> = {
  'external-editor': 'ctrl+g',
  redraw: 'ctrl+l',
  expand: 'ctrl+o',
  'transcript-find': 'ctrl+f',
  'history-search': 'ctrl+r',
  'jump-oldest': 'ctrl+e',
}

let _bindings: Record<string, string> | null = null

function load(): Record<string, string> {
  if (_bindings) return _bindings
  let custom: Record<string, unknown> = {}
  try {
    const raw = JSON.parse(readFileSync(join(homedir(), '.clawcodex', 'keybindings.json'), 'utf8'))
    if (raw && typeof raw === 'object') custom = raw as Record<string, unknown>
  } catch {
    /* no config — defaults only */
  }
  const merged: Record<string, string> = { ...DEFAULTS }
  for (const [k, v] of Object.entries(custom)) {
    if (typeof v === 'string' && v.trim()) merged[k] = v.trim().toLowerCase()
  }
  _bindings = merged
  return merged
}

interface KeyState {
  ctrl?: boolean
  shift?: boolean
  meta?: boolean
}

/** True if the pressed key (ch + modifiers) matches the combo bound to `action`. */
export function matchesBinding(action: string, ch: string, key: KeyState): boolean {
  const combo = load()[action]
  if (!combo) return false
  const parts = combo.split('+')
  const k = parts[parts.length - 1] ?? ''
  const needCtrl = parts.includes('ctrl')
  const needShift = parts.includes('shift')
  const needAlt = parts.includes('alt') || parts.includes('meta') || parts.includes('option')
  if (needCtrl !== !!key.ctrl) return false
  if (needShift !== !!key.shift) return false
  if (needAlt !== !!key.meta) return false
  return (ch || '').toLowerCase() === k
}

/** Test seam: reset the cached config (so a freshly-written file is re-read). */
export function _resetBindingsCache(): void {
  _bindings = null
}
