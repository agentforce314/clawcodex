import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { HOTKEYS } from '../content/hotkeys.js'

// Esc — not Ctrl+C — interrupts the running turn. Esc-interrupt matches the
// original's `escape → chat:cancel` binding (defaultBindings.ts /
// useCancelRequest.ts). NOTE: the original ALSO binds `ctrl+c →
// app:interrupt` (plus double-press exit); removing Ctrl+C's interrupt/exit
// roles here is a deliberate deviation per explicit user request ("use ESC
// for interrupt, not Control+C … use /exit to exit the app") — do not
// "restore" Ctrl+C-interrupt in a parity sweep.
//
// The dispatch lives inside a useInput closure and cannot be exercised
// without mounting the renderer + stdin, so — same convention as
// textInputCursorSourceOfTruth.test.ts — pin the contract at source level.
const HANDLERS_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', 'app', 'useInputHandlers.ts')
const source = readFileSync(HANDLERS_PATH, 'utf8')

// The unblocked main dispatch — everything after the isBlocked early-return
// block. The blocked branch legitimately keeps Ctrl+C as overlay dismissal.
const MAIN_ANCHOR = '// Escape-based voice bindings'
const mainDispatch = source.slice(source.indexOf(MAIN_ANCHOR))

it('main-dispatch anchor exists (guards every slice below from matching nothing)', () => {
  expect(source.indexOf(MAIN_ANCHOR)).toBeGreaterThan(-1)
})

describe('Esc interrupts the running turn', () => {
  it('dismisses an open completion menu first (original: autocomplete registers as an overlay so chat:cancel defers)', () => {
    expect(mainDispatch).toMatch(
      /if\s*\(key\.escape\s*&&\s*cState\.completions\.length\)\s*\{\s*return cActions\.dismissCompletions\(\)/
    )
  })

  it('has an escape branch calling turnController.interruptTurn gated on busy + sid', () => {
    expect(mainDispatch).toMatch(
      /if\s*\(key\.escape\s*&&\s*live\.busy\s*&&\s*live\.sid\)\s*\{\s*return turnController\.interruptTurn\(/
    )
  })

  it('orders the Esc handlers: precedence guards → menu dismissal → interrupt', () => {
    const voiceAt = mainDispatch.search(/key\.escape\s*&&\s*isVoiceToggleKey/)
    const queueEditAt = mainDispatch.search(/key\.escape\s*&&\s*cState\.queueEditIdx/)
    const selectionAt = mainDispatch.search(/key\.escape\s*&&\s*terminal\.hasSelection/)
    const dismissAt = mainDispatch.search(/key\.escape\s*&&\s*cState\.completions\.length/)
    const interruptAt = mainDispatch.search(/key\.escape\s*&&\s*live\.busy/)

    for (const at of [voiceAt, queueEditAt, selectionAt, dismissAt, interruptAt]) {
      expect(at).toBeGreaterThan(-1)
    }

    expect(voiceAt).toBeLessThan(dismissAt)
    expect(queueEditAt).toBeLessThan(dismissAt)
    expect(selectionAt).toBeLessThan(dismissAt)
    expect(dismissAt).toBeLessThan(interruptAt)
  })
})

describe('Ctrl+C neither interrupts nor exits', () => {
  const CTRL_C_ANCHOR = "if (key.ctrl && ch.toLowerCase() === 'c')"
  const END_ANCHOR = 'isAction'

  it('anchors exist (a reformat must fail here, not silently pass the slices below)', () => {
    expect(mainDispatch.indexOf(CTRL_C_ANCHOR)).toBeGreaterThan(-1)
    expect(mainDispatch.slice(mainDispatch.indexOf(CTRL_C_ANCHOR)).indexOf(END_ANCHOR)).toBeGreaterThan(-1)
  })

  const afterCtrlC = mainDispatch.slice(Math.max(0, mainDispatch.indexOf(CTRL_C_ANCHOR)))
  const ctrlCBlock = afterCtrlC.slice(0, Math.max(0, afterCtrlC.indexOf(END_ANCHOR)))

  it('no longer calls interruptTurn from Ctrl+C', () => {
    expect(ctrlCBlock).not.toContain('interruptTurn')
  })

  it('no longer exits from Ctrl+C — /exit owns exiting the app', () => {
    expect(ctrlCBlock).not.toContain('handleIdleHotkeyExit')
  })
})

describe('help content matches the bindings', () => {
  it('advertises Esc as the interrupt key', () => {
    expect(HOTKEYS.some(([k, v]) => k === 'Esc' && v.includes('interrupt'))).toBe(true)
  })

  it('does not advertise interrupt or exit on Ctrl+C', () => {
    for (const [k, v] of HOTKEYS) {
      if (k.includes('Ctrl+C')) {
        expect(v).not.toContain('interrupt')
        expect(v).not.toContain('exit')
      }
    }
  })
})
