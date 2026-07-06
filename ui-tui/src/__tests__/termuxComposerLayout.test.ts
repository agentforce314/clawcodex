import { describe, expect, it } from 'vitest'

import { stableComposerColumns, transcriptBodyWidth } from '../lib/inputMetrics.js'
import { composerPromptText } from '../lib/prompt.js'

describe('Termux composer prompt + width guards', () => {
  it('uses a single-cell ASCII prompt marker in Termux mode at any width', () => {
    // No provider prefix at any width — the stats line names the provider.
    expect(composerPromptText('❯', false, true)).toBe('>')
  })

  it('reserves fewer columns for gutter on narrow Termux widths', () => {
    // 32 columns after prompt: desktop reserves 2 for transcript scrollbar,
    // Termux keeps those 2 columns for the active composer.
    expect(stableComposerColumns(40, 8, false)).toBe(28)
    expect(stableComposerColumns(40, 8, true)).toBe(30)

    // With ample room, Termux still reserves the gutter for alignment.
    expect(stableComposerColumns(60, 8, true)).toBe(48)
  })

  it('never over-allocates transcript body width on narrow panes', () => {
    // Old behavior hard-minned to 20 columns and overflowed narrow layouts.
    expect(transcriptBodyWidth(24, 'assistant', '>', true)).toBe(19)
    expect(transcriptBodyWidth(24, 'user', 'upstr >', true)).toBe(14)
    expect(transcriptBodyWidth(10, 'user', '>', true)).toBeGreaterThanOrEqual(1)
  })

  it('keeps legacy desktop floor outside Termux mode', () => {
    expect(transcriptBodyWidth(24, 'assistant', '>')).toBe(20)
    expect(transcriptBodyWidth(24, 'user', 'upstr >')).toBe(20)
  })
})
