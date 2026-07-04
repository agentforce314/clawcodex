import { EventEmitter } from 'events'

import React from 'react'
import { describe, expect, it } from 'vitest'

import Box from './components/Box.js'
import ScrollBox from './components/ScrollBox.js'
import Text from './components/Text.js'
import { useDeclaredCursor } from './hooks/use-declared-cursor.js'
import Ink from './ink.js'

/**
 * Regression suite for main-screen (inline mode) scrollback drift — the
 * "second-turn typing lands on the footer row" bug.
 *
 * Root cause: inline sessions start BELOW pre-existing shell output, so
 * frame rows scroll into scrollback EARLIER than frame-height arithmetic
 * predicts. LogUpdate's old reachability guard (viewportY derived from
 * screen height vs viewport height) under-counted the scrolled-off rows;
 * when a later frame repainted a row in that phantom band (e.g. transcript
 * virtualization swapping rows near the top), the emitted CSI cursor-up
 * clamped at the viewport top and every subsequent relative write — plus
 * the parked displayCursor that seeds all future frames — landed one row
 * too low, permanently. Fixed by tracking the frame-end cursor's physical
 * viewport row across frames (LogUpdate.physCursorRow, LF pins at the
 * bottom margin) and deriving reachability from it.
 *
 * Strategy: drive the REAL pipeline (React → renderer → log-update →
 * optimizer → writeDiffToTerminal) against a fake TTY, replay the captured
 * bytes through a strict VT emulator (LF scrolls at the bottom margin,
 * CUU/CUD clamp, pending-wrap semantics), and assert the physical screen
 * matches the user-visible contract after every frame of a full turn
 * lifecycle: type query 1 → submit → stream past the viewport → turn end
 * → idle repaints → type query 2.
 */

const COLS = 40
const ROWS = 10
const PROMPT = 'deepseek > '
const PROMPT_W = PROMPT.length // 11, same as the real composer prompt
const FOOTER_IDLE = '  ? for shortcuts'
const FOOTER_BUSY = '  esc to interrupt'

const ESC = ''
const BEL = ''

// ---------------------------------------------------------------------------
// Strict VT emulator: models exactly the semantics the inline renderer relies
// on. Throws on anything it does not model so nothing slips through unnoticed.
// ---------------------------------------------------------------------------
class Vt {
  grid: string[][] = []
  scrollback: string[] = []
  x = 0
  y = 0
  pendingWrap = false

  constructor(
    readonly cols: number,
    readonly rows: number
  ) {
    for (let r = 0; r < rows; r++) {
      this.grid.push(new Array<string>(cols).fill(' '))
    }
  }

  private scroll() {
    const top = this.grid.shift()!
    this.scrollback.push(top.join('').replace(/\s+$/, ''))
    this.grid.push(new Array<string>(this.cols).fill(' '))
  }

  private linefeed() {
    this.pendingWrap = false

    if (this.y === this.rows - 1) {
      this.scroll()
    } else {
      this.y++
    }
  }

  private putChar(ch: string) {
    if (this.pendingWrap) {
      this.x = 0
      this.linefeed()
    }

    this.grid[this.y]![this.x] = ch

    if (this.x === this.cols - 1) {
      this.pendingWrap = true
    } else {
      this.x++
    }
  }

  feed(data: string) {
    let i = 0

    while (i < data.length) {
      const ch = data[i]!

      if (ch === ESC) {
        const rest = data.slice(i + 1)

        // OSC (hyperlinks etc.) — swallow through BEL or ST
        const osc = new RegExp(`^\\]([^${BEL}${ESC}]*)(${BEL}|${ESC}\\\\)`).exec(rest)

        if (osc) {
          i += 1 + osc[0].length

          continue
        }

        const m = /^\[(\??)([0-9;]*)([A-Za-z@`~])/.exec(rest)

        if (!m) {
          throw new Error(`Vt: unhandled escape at ${JSON.stringify(rest.slice(0, 16))}`)
        }

        const [all, priv, paramStr, final] = m
        const p = paramStr!.length ? paramStr!.split(';').map(s => parseInt(s, 10)) : []
        const n = Math.max(1, p[0] ?? 1)

        if (priv === '?') {
          // DEC private modes (cursor show/hide, mouse, paste…) — no cursor motion
          i += 1 + all!.length

          continue
        }

        switch (final) {
          case 'A': // CUU — clamps at top, no scroll
            this.pendingWrap = false
            this.y = Math.max(0, this.y - n)

            break

          case 'B': // CUD — clamps at bottom, no scroll
            this.pendingWrap = false
            this.y = Math.min(this.rows - 1, this.y + n)

            break

          case 'C': // CUF
            this.pendingWrap = false
            this.x = Math.min(this.cols - 1, this.x + n)

            break

          case 'D': // CUB
            this.pendingWrap = false
            this.x = Math.max(0, this.x - n)

            break

          case 'G': // CHA (1-based)
            this.pendingWrap = false
            this.x = Math.min(this.cols - 1, Math.max(0, (p[0] ?? 1) - 1))

            break
          case 'H': {
            // CUP (1-based row;col)
            this.pendingWrap = false
            const row = (p[0] ?? 1) - 1
            const col = (p[1] ?? 1) - 1
            this.y = Math.min(this.rows - 1, Math.max(0, row))
            this.x = Math.min(this.cols - 1, Math.max(0, col))

            break
          }

          case 'J': {
            // ED
            const mode = p[0] ?? 0

            if (mode === 2) {
              for (const row of this.grid) {
                row.fill(' ')
              }
            } else if (mode === 3) {
              this.scrollback = []
            } else if (mode === 0) {
              this.grid[this.y]!.fill(' ', this.x)

              for (let r = this.y + 1; r < this.rows; r++) {
                this.grid[r]!.fill(' ')
              }
            } else {
              throw new Error(`Vt: ED mode ${mode} not modeled`)
            }

            break
          }

          case 'K': {
            // EL
            const mode = p[0] ?? 0

            if (mode === 2) {
              this.grid[this.y]!.fill(' ')
            } else if (mode === 0) {
              this.grid[this.y]!.fill(' ', this.x)
            } else {
              this.grid[this.y]!.fill(' ', 0, this.x + 1)
            }

            break
          }

          case 'm': // SGR — styling only
            break

          default:
            throw new Error(`Vt: unhandled CSI final ${JSON.stringify(final)} in ${JSON.stringify(all)}`)
        }

        i += 1 + all!.length

        continue
      }

      if (ch === '\r') {
        this.x = 0
        this.pendingWrap = false
      } else if (ch === '\n') {
        this.linefeed()
      } else if (ch === '\b') {
        this.x = Math.max(0, this.x - 1)
        this.pendingWrap = false
      } else if (ch === BEL) {
        // bell
      } else if (ch >= ' ') {
        this.putChar(ch)
      } else {
        throw new Error(`Vt: unhandled control char 0x${ch.charCodeAt(0).toString(16)}`)
      }

      i++
    }
  }

  row(r: number): string {
    return this.grid[r]!.join('').replace(/\s+$/, '')
  }

  dump(): string {
    return this.grid.map((_, r) => `${String(r).padStart(2)}|${this.row(r)}`).join('\n')
  }

  findRow(needle: string): number {
    for (let r = 0; r < this.rows; r++) {
      if (this.row(r).includes(needle)) {
        return r
      }
    }

    return -1
  }
}

// ---------------------------------------------------------------------------
// Fake TTY + Ink harness (same pattern as ink-cursor-advance.test.ts)
// ---------------------------------------------------------------------------
class FakeTty extends EventEmitter {
  chunks: string[] = []
  columns = COLS
  rows = ROWS
  isTTY = true

  write(chunk: string | Uint8Array, cb?: (err?: Error | null) => void): boolean {
    this.chunks.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'))
    cb?.()

    return true
  }
}

// Mirrors the composer input contract: nativeCursor mode renders the bare
// value (hardware cursor marks the caret) and declares the caret position.
function FakeInput({ value, columns }: { columns: number; value: string }) {
  const boxRef = useDeclaredCursor({ active: true, column: value.length, line: 0 })

  return React.createElement(
    Box,
    { ref: boxRef, width: columns },
    React.createElement(Text, { wrap: 'wrap' }, value || ' ')
  )
}

type HarnessState = {
  busy: boolean
  flash: boolean
  lines: string[]
  scrollbox: boolean
  value: string
}

// Transcript (plain column or ScrollBox) + busy line + bordered composer with
// an absolutely-positioned right-edge flash slot + busy/idle footer.
function Harness({ busy, flash, lines, scrollbox, value }: HarnessState) {
  const transcriptRows = lines.map((l, idx) => React.createElement(Text, { key: `l${idx}` }, l))

  const transcript = scrollbox
    ? React.createElement(
        ScrollBox,
        { flexDirection: 'column', flexGrow: 1, flexShrink: 1, key: 'transcript', stickyScroll: true },
        React.createElement(Box, { flexDirection: 'column' }, ...transcriptRows)
      )
    : React.createElement(Box, { flexDirection: 'column', key: 'transcript' }, ...transcriptRows)

  return React.createElement(
    Box,
    { flexDirection: 'column' },
    transcript,
    busy ? React.createElement(Text, { key: 'busy' }, '* thinking...') : null,
    React.createElement(
      Box,
      {
        borderBottom: true,
        borderLeft: false,
        borderRight: false,
        borderStyle: 'round',
        borderTop: true,
        flexDirection: 'column',
        key: 'composer'
      },
      React.createElement(
        Box,
        { key: 'inputRow', position: 'relative' },
        React.createElement(Box, { width: PROMPT_W }, React.createElement(Text, { bold: true }, PROMPT)),
        React.createElement(FakeInput, { columns: COLS - PROMPT_W - 2, value }),
        flash
          ? React.createElement(
              Box,
              { key: 'heart', position: 'absolute', right: 0 },
              React.createElement(Text, null, '<3')
            )
          : null
      )
    ),
    value === '' ? React.createElement(Text, { key: 'footer' }, busy ? FOOTER_BUSY : FOOTER_IDLE) : null
  )
}

// ---------------------------------------------------------------------------
// Scenario runner
// ---------------------------------------------------------------------------
type StepOpts = Partial<HarnessState> & {
  /** Invoke ink.forceRedraw() after rendering this step (the ctrl+L /
   *  /redraw recovery path — ERASE_SCREEN + CURSOR_HOME + full repaint). */
  forceRedraw?: boolean
  label: string
}

function runScenario(steps: Array<StepOpts & { assert?: boolean }>, scrollbox: boolean, shellRows = 0) {
  const stdout = new FakeTty()
  const stdin = new FakeTty()
  const stderr = new FakeTty()

  const ink = new Ink({
    exitOnCtrlC: false,
    patchConsole: false,
    stderr: stderr as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stdout: stdout as unknown as NodeJS.WriteStream
  })

  const vt = new Vt(COLS, ROWS)
  const frames: Array<{ bytes: string; label: string }> = []

  // Inline mode starts BELOW pre-existing shell output: the shell prompt,
  // the launch command, and entry.tsx's own leading "\n". The Ink frame's
  // content row 0 therefore sits at physical row `shellRows`, not 0 — the
  // exact situation the renderer's scrollback math must survive.
  for (let r = 0; r < shellRows; r++) {
    vt.feed(`shell history ${r}\r\n`)
  }

  const state: HarnessState = { busy: false, flash: false, lines: [], scrollbox, value: '' }

  const ctx = (label: string) =>
    `step: ${label}\n--- physical screen ---\n${vt.dump()}\n--- last frames ---\n${frames
      .slice(-4)
      .map(f => `${f.label}: ${JSON.stringify(f.bytes)}`)
      .join('\n')}`

  const assertComposerIntact = (label: string) => {
    const promptRow = vt.findRow('deepseek')
    expect(promptRow, `prompt row missing\n${ctx(label)}`).toBeGreaterThanOrEqual(0)

    const inputRowText = vt.row(promptRow)

    if (state.value) {
      // The typed value must be on the SAME physical row as the prompt.
      // (Right-trimmed — trailing-space keystrokes leave no visible cell.)
      expect(inputRowText, `value not on the input row\n${ctx(label)}`).toContain(
        (PROMPT + state.value).replace(/\s+$/, '')
      )

      // The parked hardware cursor must sit on the input row — this is the
      // user-visible caret from the bug screenshot.
      expect(vt.y, `hardware cursor not on the input row\n${ctx(label)}`).toBe(promptRow)

      // And the footer must be gone from the whole screen (unmounted).
      expect(vt.findRow('? for shortcuts'), `footer visible while typing\n${ctx(label)}`).toBe(-1)
    } else if (!state.busy) {
      const footerRow = vt.findRow('? for shortcuts')
      expect(footerRow, `footer missing when idle\n${ctx(label)}`).toBeGreaterThanOrEqual(0)
      // border-bottom row sits between input row and footer
      expect(footerRow, `footer not below the input row\n${ctx(label)}`).toBe(promptRow + 2)
    }
  }

  for (const { assert = true, forceRedraw = false, label, ...patch } of steps) {
    Object.assign(state, patch)
    const before = stdout.chunks.length
    ink.render(React.createElement(Harness, { ...state, lines: [...state.lines] }))
    ink.onRender()

    if (forceRedraw) {
      ink.forceRedraw()
    }

    const bytes = stdout.chunks.slice(before).join('')
    frames.push({ bytes, label })
    vt.feed(bytes)

    if (assert) {
      assertComposerIntact(label)
    }
  }

  ink.unmount()
}

// The full first-turn lifecycle from the bug report: idle → type query 1 →
// submit (input clears, busy mounts) → transcript streams past the viewport
// → turn end (busy unmounts, optional flash/reflow, optional virtualization
// window slide that repaints rows near the top of the frame) → type query 2.
function lifecycleSteps(opts: {
  flashOnEnd: boolean
  reflowOnEnd: boolean
  virtSlideOnIdle?: boolean
}): Array<StepOpts> {
  const lines: string[] = ['welcome to clawcodex']
  const steps: Array<StepOpts> = []

  steps.push({ label: 'idle-fresh', lines: [...lines] })

  // Type query 1, one keystroke at a time (footer unmounts on first char)
  const q1 = 'what?'

  for (let i = 1; i <= q1.length; i++) {
    steps.push({ label: `q1-type-${i}`, value: q1.slice(0, i) })
  }

  // Submit: input clears, user line lands in transcript, busy mounts
  lines.push('> what?')
  steps.push({ busy: true, label: 'q1-submit', lines: [...lines], value: '' })

  // Streaming: transcript grows well past the viewport height
  for (let batch = 0; batch < 6; batch++) {
    for (let k = 0; k < 3; k++) {
      lines.push(`assistant output line ${batch}-${k}`)
    }

    steps.push({ label: `stream-${batch}`, lines: [...lines] })
  }

  // Turn end: busy unmounts; optionally the streamed tail reflows into its
  // final shape (heights change) and the right-edge heart flashes.
  if (opts.reflowOnEnd) {
    lines.splice(-3, 3, 'final answer line A', 'final answer line B', 'final answer line C', 'final answer line D')
  }

  steps.push({ busy: false, flash: opts.flashOnEnd, label: 'turn-end', lines: [...lines] })

  if (opts.flashOnEnd) {
    steps.push({ flash: false, label: 'flash-off' })
  }

  // Transcript virtualization window slide: rows in the middle of the frame
  // get swapped for spacer cells after measurement settles. Some of those
  // rows sit in the band the renderer believes is still reachable but has
  // physically scrolled off (when the frame started below the viewport top).
  if (opts.virtSlideOnIdle) {
    for (let i = 15; i <= 19 && i < lines.length; i++) {
      lines[i] = `virtualized spacer ${i}`
    }

    steps.push({ label: 'virt-slide', lines: [...lines] })
  }

  // Type query 2, one keystroke at a time — the bug report's failing moment
  const q2 = 'do a'

  for (let i = 1; i <= q2.length; i++) {
    steps.push({ label: `q2-type-${i}`, value: q2.slice(0, i) })
  }

  return steps
}

describe('inline-mode physical screen stays in sync across a full turn', () => {
  it('A: plain transcript column', () => {
    runScenario(lifecycleSteps({ flashOnEnd: false, reflowOnEnd: false }), false)
  })

  it('B: transcript inside a sticky ScrollBox', () => {
    runScenario(lifecycleSteps({ flashOnEnd: false, reflowOnEnd: false }), true)
  })

  it('C: ScrollBox + right-edge flash on turn end', () => {
    runScenario(lifecycleSteps({ flashOnEnd: true, reflowOnEnd: false }), true)
  })

  it('D: ScrollBox + flash + streaming tail reflow on turn end', () => {
    runScenario(lifecycleSteps({ flashOnEnd: true, reflowOnEnd: true }), true)
  })

  it('E: frame starts below shell output (control — no top-band repaint)', () => {
    runScenario(lifecycleSteps({ flashOnEnd: false, reflowOnEnd: false }), false, 3)
  })

  it('F: frame starts below shell output + idle virtualization slide', () => {
    runScenario(lifecycleSteps({ flashOnEnd: false, reflowOnEnd: false, virtSlideOnIdle: true }), false, 3)
  })

  it('G: full realism — shell offset + ScrollBox + flash + reflow + virt slide', () => {
    runScenario(lifecycleSteps({ flashOnEnd: true, reflowOnEnd: true, virtSlideOnIdle: true }), true, 3)
  })

  it('H: ctrl+L mid-session re-anchors and typing stays correct after it', () => {
    const steps = lifecycleSteps({ flashOnEnd: false, reflowOnEnd: false, virtSlideOnIdle: true })
    const at = steps.findIndex(s => s.label === 'virt-slide')
    steps.splice(at + 1, 0, { forceRedraw: true, label: 'ctrl-l' })

    runScenario(steps, false, 3)
  })
})
