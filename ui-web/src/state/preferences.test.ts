import { afterEach, describe, expect, it } from 'vitest'
import { $reading, DEFAULT_READING, installReading, parseReading, READING_KEY, setReading } from './preferences.ts'

afterEach(() => { $reading.set({ ...DEFAULT_READING }); localStorage.removeItem(READING_KEY) })
describe('reading preferences', () => {
  it('validates storage and clamps each font axis independently', () => {
    expect(parseReading({ textSize: 999, codeSize: NaN, terminalSize: 2, workDetails: 'invalid' }))
      .toEqual({ ...DEFAULT_READING, textSize: 24, terminalSize: 10 })
    expect(parseReading(null)).toEqual(DEFAULT_READING)
  })
  it('persists choices and applies live typography without changing another axis', () => {
    const cleanup = installReading()
    setReading({ workDetails: 'standard', collapseTiming: 'next-input', codeSize: 17, codeFont: 'Fira Code, Menlo' })
    expect(JSON.parse(localStorage.getItem(READING_KEY)!)).toMatchObject({ codeSize: 17, textSize: 16, workDetails: 'standard' })
    expect(document.documentElement.style.getPropertyValue('--cc-code-size')).toBe('17px')
    expect(document.documentElement.style.getPropertyValue('--cc-font-family-code')).toContain('"Fira Code", "Menlo"')
    cleanup()
  })
})
