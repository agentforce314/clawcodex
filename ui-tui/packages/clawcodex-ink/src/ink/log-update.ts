import { type AnsiCode, ansiCodesToString, diffAnsiCodes } from '@alcalzone/ansi-tokenize'

import { logForDebugging } from '../utils/debug.js'

import type { Diff, FlickerReason, Frame } from './frame.js'
import type { Point } from './layout/geometry.js'
import {
  type Cell,
  cellAt,
  CellWidth,
  charInCellAt,
  diffEach,
  type Hyperlink,
  isEmptyCellAt,
  type Screen,
  shiftRows,
  type StylePool,
  visibleCellAtIndex
} from './screen.js'
import {
  scrollDown as csiScrollDown,
  scrollUp as csiScrollUp,
  CURSOR_HOME,
  RESET_SCROLL_REGION,
  setScrollRegion
} from './termio/csi.js'
import { LINK_END, link as oscLink } from './termio/osc.js'

type State = {
  previousOutput: string
}

type Options = {
  isTTY: boolean
  stylePool: StylePool
}

const CARRIAGE_RETURN = { type: 'carriageReturn' } as const
const NEWLINE = { type: 'stdout', content: '\n' } as const

export class LogUpdate {
  private state: State

  /**
   * Physical viewport row (0-based) where the frame-end cursor physically
   * sits, carried frame to frame (main screen only — alt screen re-anchors
   * with CSI H every frame).
   *
   * Inline sessions start BELOW pre-existing shell output, so the frame's
   * top row is NOT at the viewport top and content scrolls into scrollback
   * EARLIER than frame-height arithmetic alone can predict. Reachability
   * of a row therefore cannot be derived from screen height — it must come
   * from where the cursor physically is: rows more than `physCursorRow`
   * above the frame-end cursor are in scrollback and cannot be addressed
   * (CSI cursor-up clamps at the viewport top; a clamped move desyncs
   * every later relative move AND the parked-cursor basis of all future
   * frames — the classic "typing lands one row below the input box" bug).
   *
   * Seeded at 0 — an UNDER-estimate whenever shell output sits above the
   * frame. Under-estimation is safe: the scrolled-off guard becomes
   * conservative (skips repainting a few more top rows that are actually
   * visible until the estimate converges), and it converges to the exact
   * value the first time an LF pins at the bottom margin, which any
   * viewport-filling frame guarantees.
   */
  private physCursorRow = 0

  constructor(private readonly options: Options) {
    this.state = {
      previousOutput: ''
    }
  }

  /** Re-anchor the physical cursor tracker (e.g. after ERASE_SCREEN +
   *  CURSOR_HOME in forceRedraw, when the cursor is known to be at a
   *  specific viewport row). */
  resetAnchor(row: number): void {
    this.physCursorRow = row
  }

  /** Physical viewport row of the frame-end cursor — the basis ink.tsx
   *  uses to keep its own cursor-park moves inside the viewport. */
  physicalCursorRow(): number {
    return this.physCursorRow
  }

  renderPreviousOutput_DEPRECATED(prevFrame: Frame): Diff {
    if (!this.options.isTTY) {
      // Non-TTY output is no longer supported (string output was removed)
      return [NEWLINE]
    }

    return this.getRenderOpsForDone(prevFrame)
  }

  // Called when process resumes from suspension (SIGCONT) to prevent clobbering terminal content
  reset(): void {
    this.state.previousOutput = ''
    // Physical position is unknown after suspension — fall back to the
    // conservative under-estimate (see physCursorRow docs).
    this.physCursorRow = 0
  }

  private renderFullFrame(frame: Frame): Diff {
    const { screen } = frame
    const lines: string[] = []
    let currentStyles: AnsiCode[] = []
    let currentHyperlink: Hyperlink = undefined

    for (let y = 0; y < screen.height; y++) {
      let line = ''

      for (let x = 0; x < screen.width; x++) {
        const cell = cellAt(screen, x, y)

        if (cell && cell.width !== CellWidth.SpacerTail) {
          // Handle hyperlink transitions
          if (cell.hyperlink !== currentHyperlink) {
            if (currentHyperlink !== undefined) {
              line += LINK_END
            }

            if (cell.hyperlink !== undefined) {
              line += oscLink(cell.hyperlink)
            }

            currentHyperlink = cell.hyperlink
          }

          const cellStyles = this.options.stylePool.get(cell.styleId)
          const styleDiff = diffAnsiCodes(currentStyles, cellStyles)

          if (styleDiff.length > 0) {
            line += ansiCodesToString(styleDiff)
            currentStyles = cellStyles
          }

          line += cell.char
        }
      }

      // Close any open hyperlink before resetting styles
      if (currentHyperlink !== undefined) {
        line += LINK_END
        currentHyperlink = undefined
      }

      // Reset styles at end of line so trimEnd doesn't leave dangling codes
      const resetCodes = diffAnsiCodes(currentStyles, [])

      if (resetCodes.length > 0) {
        line += ansiCodesToString(resetCodes)
        currentStyles = []
      }

      lines.push(line.trimEnd())
    }

    if (lines.length === 0) {
      return []
    }

    return [{ type: 'stdout', content: lines.join('\n') }]
  }

  private getRenderOpsForDone(prev: Frame): Diff {
    this.state.previousOutput = ''

    if (!prev.cursor.visible) {
      return [{ type: 'cursorShow' }]
    }

    return []
  }

  render(prev: Frame, next: Frame, altScreen = false, decstbmSafe = true): Diff {
    if (!this.options.isTTY) {
      return this.renderFullFrame(next)
    }

    const startTime = performance.now()
    const stylePool = this.options.stylePool

    // Terminal hosts can reflow/preserve old cells on any resize, including
    // height-only growth. A partial diff can then leave stale transcript rows
    // or cut off bordered content even when our virtual scrollTop is correct.
    // Resizing is rare enough that a full repaint is the safer tradeoff.
    if (
      next.viewport.height !== prev.viewport.height ||
      (prev.viewport.width !== 0 && next.viewport.width !== prev.viewport.width)
    ) {
      return this.fullReset(next, 'resize', altScreen)
    }

    // DECSTBM scroll optimization: when a ScrollBox's scrollTop changed,
    // shift content with a hardware scroll (CSI top;bot r + CSI n S/T)
    // instead of rewriting the whole scroll region. The shiftRows on
    // prev.screen simulates the shift so the diff loop below naturally
    // finds only the rows that scrolled IN as diffs. prev.screen is
    // about to become backFrame (reused next render) so mutation is safe.
    // CURSOR_HOME after RESET_SCROLL_REGION is defensive — DECSTBM reset
    // homes cursor per spec but terminal implementations vary.
    //
    // decstbmSafe: caller passes false when the DECSTBM→diff sequence
    // can't be made atomic (no DEC 2026 / BSU/ESU). Without atomicity the
    // outer terminal renders the intermediate state — region scrolled,
    // edge rows not yet painted — a visible vertical jump on every frame
    // where scrollTop moves. Falling through to the diff loop writes all
    // shifted rows: more bytes, no intermediate state. next.screen from
    // render-node-to-output's blit+shift is correct either way.
    let scrollPatch: Diff = []

    if (altScreen && next.scrollHint && decstbmSafe) {
      const { top, bottom, delta } = next.scrollHint

      // Keep DECSTBM away from the terminal's last visible row. In alt-screen
      // layouts we reserve that lane for status/cursor parking, and scrolling
      // it can leave transient ghosting/bleed artifacts until a later repaint.
      if (top >= 0 && bottom < prev.screen.height - 1 && bottom < next.screen.height - 1) {
        shiftRows(prev.screen, top, bottom, delta)
        scrollPatch = [
          {
            type: 'stdout',
            content:
              setScrollRegion(top + 1, bottom + 1) +
              (delta > 0 ? csiScrollUp(delta) : csiScrollDown(-delta)) +
              RESET_SCROLL_REGION +
              CURSOR_HOME
          }
        ]
      }
    }

    // We have to use purely relative operations to manipulate the cursor since
    // we don't know its starting point.
    //
    // When content height >= viewport height AND cursor is at the bottom,
    // the cursor restore at the end of the previous frame caused terminal scroll.
    // viewportY tells us how many rows are in scrollback from content overflow.
    // Additionally, the cursor-restore scroll pushes 1 more row into scrollback.
    // We need fullReset if any changes are to rows that are now in scrollback.
    //
    // This early full-reset check only applies in "steady state" (not growing).
    // For growing, the viewportY calculation below (with cursorRestoreScroll)
    // catches unreachable scrollback rows in the diff loop instead.
    const cursorAtBottom = prev.cursor.y >= prev.screen.height
    const isGrowing = next.screen.height > prev.screen.height

    // Main screen: rows more than physCursorRow above the frame-end cursor
    // have scrolled into scrollback and cannot be addressed with relative
    // moves (CSI cursor-up clamps at the viewport top). This is EXACT —
    // unlike frame-height arithmetic, it stays correct when the frame
    // started below pre-existing shell output (inline mode), where content
    // scrolls earlier than height-vs-viewport comparison predicts.
    const scrolledOffRows = Math.max(0, prev.cursor.y - this.physCursorRow)

    const prevHadScrollback = altScreen
      ? cursorAtBottom && prev.screen.height >= prev.viewport.height
      : scrolledOffRows > 0

    const isShrinking = next.screen.height < prev.screen.height
    const nextFitsViewport = next.screen.height <= prev.viewport.height

    // When shrinking from above-viewport to at-or-below-viewport, content that
    // was in scrollback should now be visible. Terminal clear operations can't
    // bring scrollback content into view, so we need a full reset.
    // Use <= (not <) because even when next height equals viewport height, the
    // scrollback depth from the previous render differs from a fresh render.
    if (prevHadScrollback && nextFitsViewport && isShrinking) {
      logForDebugging(
        `Full reset (shrink->below): prevHeight=${prev.screen.height}, nextHeight=${next.screen.height}, viewport=${prev.viewport.height}`
      )

      return this.fullReset(next, 'offscreen', altScreen)
    }

    if (
      altScreen &&
      prev.screen.height >= prev.viewport.height &&
      prev.screen.height > 0 &&
      cursorAtBottom &&
      !isGrowing
    ) {
      // viewportY = rows in scrollback from content overflow
      // +1 for the row pushed by cursor-restore scroll
      const viewportY = prev.screen.height - prev.viewport.height
      const scrollbackRows = viewportY + 1

      let scrollbackChangeY = -1
      diffEach(prev.screen, next.screen, (_x, y) => {
        if (y < scrollbackRows) {
          scrollbackChangeY = y

          return true // early exit
        }
      })

      if (scrollbackChangeY >= 0) {
        const prevLine = readLine(prev.screen, scrollbackChangeY)
        const nextLine = readLine(next.screen, scrollbackChangeY)

        return this.fullReset(next, 'offscreen', altScreen, {
          triggerY: scrollbackChangeY,
          prevLine,
          nextLine
        })
      }
    }

    const screen = new VirtualScreen(
      prev.cursor,
      next.viewport.width,
      next.viewport.height,
      altScreen ? 0 : this.physCursorRow
    )

    // Treat empty screen as height 1 to avoid spurious adjustments on first render
    const heightDelta = Math.max(next.screen.height, 1) - Math.max(prev.screen.height, 1)

    const shrinking = heightDelta < 0
    const growing = heightDelta > 0

    // Handle shrinking: clear lines from the bottom
    if (shrinking) {
      const linesToClear = prev.screen.height - next.screen.height

      // eraseLines walks upward from the cursor row and can only erase rows
      // that are physically on screen — above the viewport top the walk
      // clamps. Main screen: the cursor sits at physCursorRow, so at most
      // physCursorRow + 1 rows are reachable. If more must go, full reset.
      const eraseReach = altScreen ? prev.viewport.height : this.physCursorRow + 1

      if (linesToClear > eraseReach) {
        return this.fullReset(next, 'offscreen', altScreen)
      }

      // clear(N) moves cursor UP by N-1 lines and to column 0
      // This puts us at line prev.screen.height - N = next.screen.height
      // But we want to be at next.screen.height - 1 (bottom of new screen)
      screen.txn(prev => [
        [
          { type: 'clear', count: linesToClear },
          { type: 'cursorMove', x: 0, y: -1 }
        ],
        { dx: -prev.x, dy: -linesToClear }
      ])
    }

    // viewportY = number of rows in scrollback (not visible on terminal).
    //
    // Main screen: exactly scrolledOffRows — derived from the tracked
    // physical cursor row, which absorbs BOTH content overflow and any
    // start offset below pre-existing shell output. The old height-based
    // heuristic under-counted in inline sessions, letting the diff loop
    // address rows that had physically scrolled away; the resulting
    // clamped cursor-up desynced every later write by the clamped amount
    // (typing landed one row below the input box).
    //
    // Alt screen keeps the height arithmetic: its buffer never scrolls and
    // prev.cursor is anchored to (0,0), so scrolledOffRows is meaningless
    // there. For shrinking: use max(prev, next) because terminal clears
    // don't scroll. For growing: use prev state because new rows haven't
    // scrolled old ones yet.
    const cursorRestoreScroll = prevHadScrollback ? 1 : 0

    const viewportY = altScreen
      ? growing
        ? Math.max(0, prev.screen.height - prev.viewport.height + cursorRestoreScroll)
        : Math.max(prev.screen.height, next.screen.height) - next.viewport.height + cursorRestoreScroll
      : scrolledOffRows

    let currentStyleId = stylePool.none
    let currentHyperlink: Hyperlink = undefined

    // First pass: render changes to existing rows (rows < prev.screen.height)
    let needsFullReset = false
    let resetTriggerY = -1
    diffEach(prev.screen, next.screen, (x, y, removed, added) => {
      // Skip new rows - we'll render them directly after
      if (growing && y >= prev.screen.height) {
        return
      }

      // Skip spacers during rendering because the terminal will automatically
      // advance 2 columns when we write the wide character itself.
      // SpacerTail: Second cell of a wide character
      // SpacerHead: Marks line-end position where wide char wraps to next line
      if (added && (added.width === CellWidth.SpacerTail || added.width === CellWidth.SpacerHead)) {
        return
      }

      if (removed && (removed.width === CellWidth.SpacerTail || removed.width === CellWidth.SpacerHead) && !added) {
        return
      }

      // Skip empty cells that don't need to overwrite existing content.
      // This prevents writing trailing spaces that would cause unnecessary
      // line wrapping at the edge of the screen.
      // Uses isEmptyCellAt to check if both packed words are zero (empty cell).
      if (added && isEmptyCellAt(next.screen, x, y) && !removed) {
        return
      }

      // If the cell outside the viewport range has changed, we need to reset
      // because we can't move the cursor there to draw. In main-screen mode,
      // those rows are already in terminal scrollback and invisible; resetting
      // on every scrollback-only update can loop when a resize changes the
      // physical buffer. Shrink-to-visible cases are handled above.
      if (y < viewportY) {
        if (!altScreen) {
          return
        }

        needsFullReset = true
        resetTriggerY = y

        return true // early exit
      }

      moveCursorTo(screen, x, y)

      if (added) {
        const targetHyperlink = added.hyperlink
        currentHyperlink = transitionHyperlink(screen.diff, currentHyperlink, targetHyperlink)
        const styleStr = stylePool.transition(currentStyleId, added.styleId)

        if (writeCellWithStyleStr(screen, added, styleStr)) {
          currentStyleId = added.styleId
        }
      } else if (removed) {
        // Cell was removed - clear it with a space
        // (This handles shrinking content)
        // Reset any active styles/hyperlinks first to avoid leaking into cleared cells
        const styleIdToReset = currentStyleId
        const hyperlinkToReset = currentHyperlink
        currentStyleId = stylePool.none
        currentHyperlink = undefined

        screen.txn(() => {
          const patches: Diff = []
          transitionStyle(patches, stylePool, styleIdToReset, stylePool.none)
          transitionHyperlink(patches, hyperlinkToReset, undefined)
          patches.push({ type: 'stdout', content: ' ' })

          return [patches, { dx: 1, dy: 0 }]
        })
      }
    })

    if (needsFullReset) {
      return this.fullReset(next, 'offscreen', altScreen, {
        triggerY: resetTriggerY,
        prevLine: readLine(prev.screen, resetTriggerY),
        nextLine: readLine(next.screen, resetTriggerY)
      })
    }

    // Reset styles before rendering new rows (they'll set their own styles)
    currentStyleId = transitionStyle(screen.diff, stylePool, currentStyleId, stylePool.none)
    currentHyperlink = transitionHyperlink(screen.diff, currentHyperlink, undefined)

    // Handle growth: render new rows directly (they naturally scroll the terminal)
    if (growing) {
      renderFrameSlice(screen, next, prev.screen.height, next.screen.height, stylePool)
    }

    // Restore cursor. Skipped in alt-screen: the cursor is hidden, its
    // position only matters as the starting point for the NEXT frame's
    // relative moves, and in alt-screen the next frame always begins with
    // CSI H (see ink.tsx onRender) which resets to (0,0) regardless. This
    // saves a CR + cursorMove round-trip (~6-10 bytes) every frame.
    //
    // Main screen: if cursor needs to be past the last line of content
    // (typical: cursor.y = screen.height), emit \n to create that line
    // since cursor movement can't create new lines.
    if (altScreen) {
      // no-op; next frame's CSI H anchors cursor
    } else if (next.cursor.y >= next.screen.height) {
      // Move to column 0 of current line, then emit newlines to reach target row
      screen.txn(prev => {
        const rowsToCreate = next.cursor.y - prev.y

        if (rowsToCreate > 0) {
          // Use CR to resolve pending wrap (if any) without advancing
          // to the next line, then LF to create each new row.
          const patches: Diff = new Array<Diff[number]>(1 + rowsToCreate)
          patches[0] = CARRIAGE_RETURN

          for (let i = 0; i < rowsToCreate; i++) {
            patches[1 + i] = NEWLINE
          }

          return [patches, { dx: -prev.x, dy: rowsToCreate, lf: rowsToCreate }]
        }

        // At or past target row - need to move cursor to correct position
        const dy = next.cursor.y - prev.y

        if (dy !== 0 || prev.x !== next.cursor.x) {
          // Use CR to clear pending wrap (if any), then cursor move
          const patches: Diff = [CARRIAGE_RETURN]
          patches.push({ type: 'cursorMove', x: next.cursor.x, y: dy })

          return [patches, { dx: next.cursor.x - prev.x, dy }]
        }

        return [[], { dx: 0, dy: 0 }]
      })
    } else {
      moveCursorTo(screen, next.cursor.x, next.cursor.y)
    }

    // Persist the tracked physical row for the next frame's reachability
    // guard. Alt screen skips this: its frames are CSI H-anchored and the
    // main-screen anchor must survive alt-screen excursions unchanged
    // (DECSET 1049 saves/restores the main-screen cursor).
    if (!altScreen) {
      this.physCursorRow = screen.phys
    }

    const elapsed = performance.now() - startTime

    if (elapsed > 50) {
      const damage = next.screen.damage

      const damageInfo = damage ? `${damage.width}x${damage.height} at (${damage.x},${damage.y})` : 'none'

      logForDebugging(
        `Slow render: ${elapsed.toFixed(1)}ms, screen: ${next.screen.height}x${next.screen.width}, damage: ${damageInfo}, changes: ${screen.diff.length}`
      )
    }

    return scrollPatch.length > 0 ? [...scrollPatch, ...screen.diff] : screen.diff
  }

  /**
   * Full clear + repaint. clearTerminal homes the cursor to the viewport
   * top, so the repaint's physical tracking re-anchors from row 0 — this is
   * what makes resize/offscreen resets self-healing for the main-screen
   * anchor as well.
   */
  private fullReset(
    frame: Frame,
    reason: FlickerReason,
    altScreen: boolean,
    debug?: { triggerY: number; prevLine: string; nextLine: string }
  ): Diff {
    // After clearTerminal, cursor is at (0, 0)
    const screen = new VirtualScreen({ x: 0, y: 0 }, frame.viewport.width, frame.viewport.height, 0)
    renderFrame(screen, frame, this.options.stylePool)

    if (!altScreen) {
      this.physCursorRow = screen.phys
    }

    return [{ type: 'clearTerminal', reason, debug }, ...screen.diff]
  }
}

function transitionHyperlink(diff: Diff, current: Hyperlink, target: Hyperlink): Hyperlink {
  if (current !== target) {
    diff.push({ type: 'hyperlink', uri: target ?? '' })

    return target
  }

  return current
}

function transitionStyle(diff: Diff, stylePool: StylePool, currentId: number, targetId: number): number {
  const str = stylePool.transition(currentId, targetId)

  if (str.length > 0) {
    diff.push({ type: 'styleStr', str })
  }

  return targetId
}

function readLine(screen: Screen, y: number): string {
  let line = ''

  for (let x = 0; x < screen.width; x++) {
    line += charInCellAt(screen, x, y) ?? ' '
  }

  return line.trimEnd()
}

function renderFrame(screen: VirtualScreen, frame: Frame, stylePool: StylePool): void {
  renderFrameSlice(screen, frame, 0, frame.screen.height, stylePool)
}

/**
 * Render a slice of rows from the frame's screen.
 * Each row is rendered followed by a newline. Cursor ends at (0, endY).
 */
function renderFrameSlice(
  screen: VirtualScreen,
  frame: Frame,
  startY: number,
  endY: number,
  stylePool: StylePool
): VirtualScreen {
  let currentStyleId = stylePool.none
  let currentHyperlink: Hyperlink = undefined
  // Track the styleId of the last rendered cell on this line (-1 if none).
  // Passed to visibleCellAtIndex to enable fg-only space optimization.
  let lastRenderedStyleId = -1

  const { width: screenWidth, cells, charPool, hyperlinkPool } = frame.screen

  let index = startY * screenWidth

  for (let y = startY; y < endY; y += 1) {
    // Advance cursor to this row using LF (not CSI CUD / cursor-down).
    // CSI CUD stops at the viewport bottom margin and cannot scroll,
    // but LF scrolls the viewport to create new lines. Without this,
    // when the cursor is at the viewport bottom, moveCursorTo's
    // cursor-down silently fails, creating a permanent off-by-one
    // between the virtual cursor and the real terminal cursor.
    if (screen.cursor.y < y) {
      const rowsToAdvance = y - screen.cursor.y
      screen.txn(prev => {
        const patches: Diff = new Array<Diff[number]>(1 + rowsToAdvance)
        patches[0] = CARRIAGE_RETURN

        for (let i = 0; i < rowsToAdvance; i++) {
          patches[1 + i] = NEWLINE
        }

        return [patches, { dx: -prev.x, dy: rowsToAdvance, lf: rowsToAdvance }]
      })
    }

    // Reset at start of each line — no cell rendered yet
    lastRenderedStyleId = -1

    for (let x = 0; x < screenWidth; x += 1, index += 1) {
      // Skip spacers, unstyled empty cells, and fg-only styled spaces that
      // match the last rendered style (since cursor-forward produces identical
      // visual result). visibleCellAtIndex handles the optimization internally
      // to avoid allocating Cell objects for skipped cells.
      const cell = visibleCellAtIndex(cells, charPool, hyperlinkPool, index, lastRenderedStyleId)

      if (!cell) {
        continue
      }

      moveCursorTo(screen, x, y)

      // Handle hyperlink
      const targetHyperlink = cell.hyperlink
      currentHyperlink = transitionHyperlink(screen.diff, currentHyperlink, targetHyperlink)

      // Style transition — cached string, zero allocations after warmup
      const styleStr = stylePool.transition(currentStyleId, cell.styleId)

      if (writeCellWithStyleStr(screen, cell, styleStr)) {
        currentStyleId = cell.styleId
        lastRenderedStyleId = cell.styleId
      }
    }

    // Reset styles/hyperlinks before newline so background color doesn't
    // bleed into the next line when the terminal scrolls. The old code
    // reset implicitly by writing trailing unstyled spaces; now that we
    // skip empty cells, we must reset explicitly.
    currentStyleId = transitionStyle(screen.diff, stylePool, currentStyleId, stylePool.none)
    currentHyperlink = transitionHyperlink(screen.diff, currentHyperlink, undefined)
    // CR+LF at end of row — \r resets to column 0, \n moves to next line.
    // Without \r, the terminal cursor stays at whatever column content ended
    // (since we skip trailing spaces, this can be mid-row).
    screen.txn(prev => [[CARRIAGE_RETURN, NEWLINE], { dx: -prev.x, dy: 1, lf: 1 }])
  }

  // Reset any open style/hyperlink at end of slice
  transitionStyle(screen.diff, stylePool, currentStyleId, stylePool.none)
  transitionHyperlink(screen.diff, currentHyperlink, undefined)

  return screen
}

type Delta = {
  dx: number
  dy: number
  /** How many of `dy`'s rows are LF-driven ('\n' bytes). LFs differ from
   *  CSI cursor-down at the bottom margin: the terminal scrolls instead of
   *  moving the cursor, so the physical row pins at viewport bottom while
   *  the virtual (content) row keeps counting. */
  lf?: number
}

/**
 * Write a cell with a pre-serialized style transition string (from
 * StylePool.transition). Inlines the txn logic to avoid closure/tuple/delta
 * allocations on every cell.
 *
 * Returns true if the cell was written, false if skipped (wide char at
 * viewport edge). Callers MUST gate currentStyleId updates on this — when
 * skipped, styleStr is never pushed and the terminal's style state is
 * unchanged. Updating the virtual tracker anyway desyncs it from the
 * terminal, and the next transition is computed from phantom state.
 */
function writeCellWithStyleStr(screen: VirtualScreen, cell: Cell, styleStr: string): boolean {
  const cellWidth = cell.width === CellWidth.Wide ? 2 : 1
  const px = screen.cursor.x
  const vw = screen.viewportWidth

  // Don't write wide chars that would cross the viewport edge.
  // Single-codepoint chars (CJK) at vw-2 are safe; multi-codepoint
  // graphemes (flags, ZWJ emoji) need stricter threshold.
  if (cellWidth === 2 && px < vw) {
    const threshold = cell.char.length > 2 ? vw : vw + 1

    if (px + 2 >= threshold) {
      return false
    }
  }

  const diff = screen.diff

  if (styleStr.length > 0) {
    diff.push({ type: 'styleStr', str: styleStr })
  }

  const needsCompensation = cellWidth === 2 && needsWidthCompensation(cell.char)

  // On terminals with old wcwidth tables, a compensated emoji only advances
  // the cursor 1 column, so the CHA below skips column x+1 without painting
  // it. Write a styled space there first — on correct terminals the emoji
  // glyph (width 2) overwrites it harmlessly; on old terminals it fills the
  // gap with the emoji's background. Also clears any stale content at x+1.
  // CHA is 1-based, so column px+1 (0-based) is CHA target px+2.
  if (needsCompensation && px + 1 < vw) {
    diff.push({ type: 'cursorTo', col: px + 2 })
    diff.push({ type: 'stdout', content: ' ' })
    diff.push({ type: 'cursorTo', col: px + 1 })
  }

  diff.push({ type: 'stdout', content: cell.char })

  // Force terminal cursor to correct column after the emoji.
  if (needsCompensation) {
    diff.push({ type: 'cursorTo', col: px + cellWidth + 1 })
  }

  // Update cursor — mutate in place to avoid Point allocation
  if (px >= vw) {
    screen.cursor.x = cellWidth
    screen.cursor.y++
    // Auto-wrap behaves like an LF: at the bottom margin the terminal
    // scrolls and the physical row pins instead of advancing.
    screen.physLinefeed()
  } else {
    screen.cursor.x = px + cellWidth
  }

  return true
}

function moveCursorTo(screen: VirtualScreen, targetX: number, targetY: number) {
  screen.txn(prev => {
    const dx = targetX - prev.x
    const dy = targetY - prev.y
    const inPendingWrap = prev.x >= screen.viewportWidth

    // If we're in pending wrap state (cursor.x >= width), use CR
    // to reset to column 0 on the current line without advancing
    // to the next line, then issue the cursor movement.
    if (inPendingWrap) {
      return [[CARRIAGE_RETURN, { type: 'cursorMove', x: targetX, y: dy }], { dx, dy }]
    }

    // When moving to a different line, use carriage return (\r) to reset to
    // column 0 first, then cursor move.
    if (dy !== 0) {
      return [[CARRIAGE_RETURN, { type: 'cursorMove', x: targetX, y: dy }], { dx, dy }]
    }

    // Standard same-line cursor move
    return [[{ type: 'cursorMove', x: dx, y: dy }], { dx, dy }]
  })
}

/**
 * Identify emoji where the terminal's wcwidth may disagree with Unicode.
 * On terminals with correct tables, the CHA we emit is a harmless no-op.
 *
 * Two categories:
 * 1. Newer emoji (Unicode 12.0+) missing from terminal wcwidth tables.
 * 2. Text-by-default emoji + VS16 (U+FE0F): the base codepoint is width 1
 *    in wcwidth, but VS16 triggers emoji presentation making it width 2.
 *    Examples: ⚔️ (U+2694), ☠️ (U+2620), ❤️ (U+2764).
 */
function needsWidthCompensation(char: string): boolean {
  const cp = char.codePointAt(0)

  if (cp === undefined) {
    return false
  }

  // U+1FA70-U+1FAFF: Symbols and Pictographs Extended-A (Unicode 12.0-15.0)
  // U+1FB00-U+1FBFF: Symbols for Legacy Computing (Unicode 13.0)
  if ((cp >= 0x1fa70 && cp <= 0x1faff) || (cp >= 0x1fb00 && cp <= 0x1fbff)) {
    return true
  }

  // Text-by-default emoji with VS16: scan for U+FE0F in multi-codepoint
  // graphemes. Single BMP chars (length 1) and surrogate pairs without VS16
  // skip this check. VS16 (0xFE0F) can't collide with surrogates (0xD800-0xDFFF).
  if (char.length >= 2) {
    for (let i = 0; i < char.length; i++) {
      if (char.charCodeAt(i) === 0xfe0f) {
        return true
      }
    }
  }

  return false
}

class VirtualScreen {
  // Public for direct mutation by writeCellWithStyleStr (avoids txn overhead).
  // File-private class — not exposed outside log-update.ts.
  cursor: Point
  diff: Diff = []
  /** Physical viewport row (0-based) of the cursor, tracked with terminal
   *  semantics: LF-driven advances pin at the bottom margin (the terminal
   *  scrolls instead of moving the cursor down). Relative cursor moves
   *  translate 1:1 — the reachability guard in render() must keep them
   *  inside the viewport, so they never clamp. */
  phys: number

  constructor(
    origin: Point,
    readonly viewportWidth: number,
    readonly viewportHeight: number,
    physStart: number
  ) {
    this.cursor = { ...origin }
    this.phys = physStart
  }

  /** Advance the physical row by an LF-driven step (pins at the bottom). */
  physLinefeed(): void {
    if (this.phys < this.viewportHeight - 1) {
      this.phys++
    }
  }

  txn(fn: (prev: Point) => [patches: Diff, next: Delta]): void {
    const [patches, next] = fn(this.cursor)

    for (const patch of patches) {
      this.diff.push(patch)
    }

    this.cursor.x += next.dx
    this.cursor.y += next.dy

    const lf = next.lf ?? 0

    for (let i = 0; i < lf; i++) {
      this.physLinefeed()
    }

    // Non-LF vertical movement (CSI cursor up/down) moves the physical
    // cursor exactly dy rows. The scrolled-off-row guard keeps targets
    // inside the viewport; clamp defensively so a slipped-through move
    // can't corrupt the tracker beyond the frame it happened in.
    const moveDy = next.dy - lf

    if (moveDy !== 0) {
      this.phys = Math.min(Math.max(this.phys + moveDy, 0), this.viewportHeight - 1)
    }
  }
}
