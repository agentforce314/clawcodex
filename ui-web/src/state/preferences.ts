import { atom } from 'nanostores'

export const WORK_DETAIL_MODES = ['compact', 'standard', 'detailed', 'verbose'] as const
export type WorkDetailMode = typeof WORK_DETAIL_MODES[number]
export type CollapseTiming = 'completion' | 'next-input'

export interface ReadingPreferences {
  workDetails: WorkDetailMode
  collapseTiming: CollapseTiming
  textSize: number
  codeSize: number
  terminalSize: number
  textFont: string
  codeFont: string
  terminalFont: string
}

export const DEFAULT_READING: ReadingPreferences = {
  workDetails: 'detailed', collapseTiming: 'completion',
  textSize: 16, codeSize: 13, terminalSize: 13,
  textFont: '', codeFont: '', terminalFont: '',
}
export const READING_KEY = 'clawcodex.web.reading.v1'

/** Validate browser storage; a corrupt field does not discard the other preferences. */
export function parseReading(value: unknown): ReadingPreferences {
  const saved = typeof value === 'object' && value !== null ? value as Record<string, unknown> : {}
  const size = (key: 'textSize' | 'codeSize' | 'terminalSize', min: number, max: number) => {
    const n = saved[key]
    return typeof n === 'number' && Number.isFinite(n)
      ? Math.max(min, Math.min(max, Math.round(n))) : DEFAULT_READING[key]
  }
  const font = (key: 'textFont' | 'codeFont' | 'terminalFont') =>
    typeof saved[key] === 'string' ? saved[key].trim().slice(0, 200) : ''
  return {
    workDetails: WORK_DETAIL_MODES.includes(saved.workDetails as WorkDetailMode)
      ? saved.workDetails as WorkDetailMode : DEFAULT_READING.workDetails,
    collapseTiming: saved.collapseTiming === 'next-input' ? 'next-input' : 'completion',
    textSize: size('textSize', 12, 24), codeSize: size('codeSize', 10, 20),
    terminalSize: size('terminalSize', 10, 24),
    textFont: font('textFont'), codeFont: font('codeFont'), terminalFont: font('terminalFont'),
  }
}

function readPreferences(): ReadingPreferences {
  try { return parseReading(JSON.parse(localStorage.getItem(READING_KEY) ?? '{}')) }
  catch { return { ...DEFAULT_READING } }
}

export const $reading = atom<ReadingPreferences>(readPreferences())

export function setReading(patch: Partial<ReadingPreferences>): void {
  const next = parseReading({ ...$reading.get(), ...patch })
  $reading.set(next)
  try { localStorage.setItem(READING_KEY, JSON.stringify(next)) }
  catch { /* The preference still applies when browser storage is unavailable. */ }
}

/** Quote each family name so custom font input cannot introduce CSS syntax. */
function fontStack(value: string, fallback: string): string {
  const names = value.split(',').map(name => name.trim().replace(/^["']|["']$/g, '')).filter(Boolean)
  return [...names.map(name => JSON.stringify(name)), fallback].join(', ')
}

export function installReading(): () => void {
  const apply = (p: ReadingPreferences) => {
    const style = document.documentElement.style
    style.setProperty('--cc-text-size', `${p.textSize}px`)
    style.setProperty('--cc-code-size', `${p.codeSize}px`)
    style.setProperty('--cc-terminal-size', `${p.terminalSize}px`)
    style.setProperty('--cc-font-family', fontStack(p.textFont, 'var(--cc-font-family-default)'))
    style.setProperty('--cc-font-family-code', fontStack(p.codeFont, 'var(--cc-font-family-code-default)'))
    style.setProperty('--cc-font-family-terminal', fontStack(p.terminalFont, 'var(--cc-font-family-code)'))
  }
  const unlisten = $reading.subscribe(apply)
  const onStorage = (event: StorageEvent) => {
    if (event.key === READING_KEY || event.key === null) $reading.set(readPreferences())
  }
  window.addEventListener('storage', onStorage)
  return () => { unlisten(); window.removeEventListener('storage', onStorage) }
}
