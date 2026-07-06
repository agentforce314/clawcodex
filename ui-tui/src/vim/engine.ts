/**
 * Minimal vim editing engine for the ui-tui composer — C13 Part 1.
 *
 * Ports the SEMANTICS of the original vim engine (typescript/src/vim/:
 * motions.ts / operators.ts / transitions.ts) onto the composer's simple
 * `{ value: string; cursor: number }` text model, rather than the original's
 * 1554-line `Cursor` class (which ui-tui does not have). The cursor is a
 * character offset into `value` (code units — the composer's model).
 *
 * This is the PURE-LOGIC core: mode state, motions, operators, and the
 * NORMAL-mode key dispatcher, all side-effect-free and unit-testable. The
 * composer wiring (intercepting keys in NORMAL mode) is Part 2.
 */

export type VimMode = 'normal' | 'insert'
export type Operator = 'd' | 'c' // delete, change (yank omitted — no register yet)

export interface Buffer {
  value: string
  cursor: number
}

export interface VimState {
  mode: VimMode
  /** A pending operator awaiting a motion (e.g. `d` then `w`), else null. */
  pendingOperator: Operator | null
  /** The numeric count prefix being typed (e.g. `3` in `3w`), or 0 if none. */
  count: number
  /** True after a lone `g` is pressed, awaiting the second key (`gg`). */
  pendingG: boolean
}

export function initialVimState(): VimState {
  return { mode: 'normal', pendingOperator: null, count: 0, pendingG: false }
}

// ── character classes (vim word semantics) ───────────────────────────────────

type CharClass = 'blank' | 'word' | 'punct'

function classOf(ch: string): CharClass {
  if (ch === '' || /\s/.test(ch)) return 'blank'
  // vim "word" chars: letters, marks, digits, underscore (matches the
  // reference VIM_WORD_CHAR_REGEX — \p{M} keeps combining marks with their
  // base letter). Everything else is punct.
  if (/[\p{L}\p{M}\p{N}_]/u.test(ch)) return 'word'
  return 'punct'
}

const isBlank = (ch: string): boolean => classOf(ch) === 'blank'

/** Advance one CODE POINT right from `i` (never splits a surrogate pair). */
function nextCodePoint(value: string, i: number): number {
  if (i >= value.length) return value.length
  const cp = value.codePointAt(i)
  return i + (cp !== undefined && cp > 0xffff ? 2 : 1)
}

// ── line helpers (cursor is an offset into a possibly-multiline value) ────────

function lineStart(value: string, cursor: number): number {
  const nl = value.lastIndexOf('\n', cursor - 1)
  return nl === -1 ? 0 : nl + 1
}

function lineEnd(value: string, cursor: number): number {
  const nl = value.indexOf('\n', cursor)
  return nl === -1 ? value.length : nl
}

// ── motions: (value, cursor) → target offset ─────────────────────────────────

/** Move one char left, clamped to the start of the current logical line. */
function left(value: string, cursor: number): number {
  return Math.max(lineStart(value, cursor), cursor - 1)
}

/**
 * Move one char right. In NORMAL mode the cursor rests ON a character, so the
 * rightmost valid resting position is the last char of the line (`end - 1`),
 * not the newline. On an empty line the only position is the line start.
 */
function right(value: string, cursor: number): number {
  const start = lineStart(value, cursor)
  const end = lineEnd(value, cursor)
  // rest position of the LAST char = start of its code point (so `l` never
  // lands between a surrogate pair)
  const maxRest = end > start ? prevCodePointStart(value, end) : start
  return Math.min(nextCodePoint(value, cursor), maxRest)
}

/** Start offset of the code point ENDING at `end` (i.e. the last char's start). */
function prevCodePointStart(value: string, end: number): number {
  if (end <= 0) return 0
  const prev = end - 1
  // if value[prev] is a low surrogate, step back one more to its high surrogate
  const code = value.charCodeAt(prev)
  if (code >= 0xdc00 && code <= 0xdfff && prev > 0) return prev - 1
  return prev
}

function startOfLine(value: string, cursor: number): number {
  return lineStart(value, cursor)
}

function firstNonBlank(value: string, cursor: number): number {
  let i = lineStart(value, cursor)
  const end = lineEnd(value, cursor)
  while (i < end && isBlank(value[i]!)) i++
  return i
}

/** `$` — last char of the logical line (the resting position in NORMAL). */
function endOfLine(value: string, cursor: number): number {
  const end = lineEnd(value, cursor)
  const start = lineStart(value, cursor)
  return end > start ? end - 1 : start
}

/** `w` — start of the next word (word/punct run), skipping blanks. */
function nextWord(value: string, cursor: number): number {
  const n = value.length
  let i = cursor
  if (i >= n) return n
  const startClass = classOf(value[i]!)
  // move past the current run (of the same non-blank class)
  if (startClass !== 'blank') {
    while (i < n && classOf(value[i]!) === startClass) i++
  }
  // skip blanks to the next word start
  while (i < n && isBlank(value[i]!)) i++
  return i
}

/** `W` — start of the next WORD (whitespace-delimited). */
function nextWORD(value: string, cursor: number): number {
  const n = value.length
  let i = cursor
  while (i < n && !isBlank(value[i]!)) i++
  while (i < n && isBlank(value[i]!)) i++
  return i
}

/** `b` — start of the current/previous word. */
function prevWord(value: string, cursor: number): number {
  let i = cursor - 1
  while (i > 0 && isBlank(value[i]!)) i--
  if (i <= 0) return Math.max(0, i)
  const cls = classOf(value[i]!)
  while (i > 0 && classOf(value[i - 1]!) === cls) i--
  return Math.max(0, i)
}

/** `e` — end of the current/next word (inclusive target). */
function endOfWord(value: string, cursor: number): number {
  const n = value.length
  let i = cursor + 1
  while (i < n && isBlank(value[i]!)) i++
  if (i >= n) return Math.max(cursor, n - 1)
  const cls = classOf(value[i]!)
  while (i + 1 < n && classOf(value[i + 1]!) === cls) i++
  return i
}

/** `B` — start of the current/previous WORD (whitespace-delimited). */
function prevWORD(value: string, cursor: number): number {
  let i = cursor - 1
  while (i > 0 && isBlank(value[i]!)) i--
  while (i > 0 && !isBlank(value[i - 1]!)) i--
  return Math.max(0, i)
}

/** `E` — end of the current/next WORD (inclusive, whitespace-delimited). */
function endOfWORD(value: string, cursor: number): number {
  const n = value.length
  let i = cursor + 1
  while (i < n && isBlank(value[i]!)) i++
  if (i >= n) return Math.max(cursor, n - 1)
  while (i + 1 < n && !isBlank(value[i + 1]!)) i++
  return i
}

function downLine(value: string, cursor: number): number {
  const end = lineEnd(value, cursor)
  if (end >= value.length) return cursor // no line below
  const col = cursor - lineStart(value, cursor)
  const nextStart = end + 1
  const nextEnd = lineEnd(value, nextStart)
  return Math.min(nextStart + col, Math.max(nextStart, nextEnd - 1 + (nextEnd === nextStart ? 1 : 0)))
}

function upLine(value: string, cursor: number): number {
  const start = lineStart(value, cursor)
  if (start === 0) return cursor // no line above
  const col = cursor - start
  const prevEnd = start - 1
  const prevStart = lineStart(value, prevEnd)
  return Math.min(prevStart + col, Math.max(prevStart, prevEnd - 1 + (prevEnd === prevStart ? 1 : 0)))
}

const INCLUSIVE = new Set(['e', 'E', '$'])
const LINEWISE = new Set(['j', 'k', 'G', 'gg'])
// gg is NOT in this set: it's resolvable but reached via the two-key `g` prefix
// in dispatchNormal, not a single MOTION_KEYS entry.

export function isInclusiveMotion(key: string): boolean {
  return INCLUSIVE.has(key)
}
export function isLinewiseMotion(key: string): boolean {
  return LINEWISE.has(key)
}

/** Resolve a single motion key to a target offset (count applied by caller). */
export function resolveMotion(key: string, value: string, cursor: number): number {
  switch (key) {
    case 'h': return left(value, cursor)
    case 'l': return right(value, cursor)
    case 'j': return downLine(value, cursor)
    case 'k': return upLine(value, cursor)
    case 'w': return nextWord(value, cursor)
    case 'W': return nextWORD(value, cursor)
    case 'b': return prevWord(value, cursor)
    case 'B': return prevWORD(value, cursor)
    case 'e': return endOfWord(value, cursor)
    case 'E': return endOfWORD(value, cursor)
    case '0': return startOfLine(value, cursor)
    case '^': return firstNonBlank(value, cursor)
    case '$': return endOfLine(value, cursor)
    case 'G': return Math.max(0, value.length ? value.lastIndexOf('\n') + 1 : 0)
    case 'gg': return 0
    default: return cursor
  }
}

/** Apply a motion `count` times (stops early if it can't advance). */
export function applyMotion(key: string, value: string, cursor: number, count: number): number {
  let pos = cursor
  for (let i = 0; i < Math.max(1, count); i++) {
    const next = resolveMotion(key, value, pos)
    if (next === pos) break
    pos = next
  }
  return pos
}

// ── operators: (buffer, motion) → new buffer ─────────────────────────────────

/** The [start, end) span an operator+motion deletes. */
export function operatorSpan(
  key: string, value: string, cursor: number, count: number,
): { start: number; end: number } {
  if (isLinewiseMotion(key)) {
    // linewise: from the current line's start THROUGH the line the motion
    // lands on (dj = 2 lines, dG = to last line), inclusive, plus the trailing
    // newline. Ignoring the target was the bug — dj/dk/dG only ate one line.
    const target = applyMotion(key, value, cursor, count)
    let start = Math.min(lineStart(value, cursor), lineStart(value, target))
    let end = Math.max(lineEnd(value, cursor), lineEnd(value, target))
    if (end < value.length) end += 1 // eat the trailing newline
    else if (start > 0 && value[start - 1] === '\n') start -= 1 // at EOF, eat the preceding one
    return { start, end }
  }
  const target = applyMotion(key, value, cursor, count)
  let start = Math.min(cursor, target)
  let end = Math.max(cursor, target)
  if (isInclusiveMotion(key)) end += 1 // inclusive motions eat the target char
  return { start, end: Math.min(end, value.length) }
}

/** `dd` — delete the whole current line (linewise). */
export function deleteLine(value: string, cursor: number, count: number): Buffer {
  let start = lineStart(value, cursor)
  let end = start
  for (let i = 0; i < Math.max(1, count); i++) {
    end = lineEnd(value, end)
    if (end < value.length) end += 1
    else break
  }
  // If the deletion runs to EOF, also eat the PRECEDING newline so the last
  // line's removal doesn't leave a trailing empty line ("abc\ndef" -dd-> "abc",
  // not "abc\n"). Mirrors the reference executeLineOp guard.
  if (end >= value.length && start > 0 && value[start - 1] === '\n') start -= 1
  const newValue = value.slice(0, start) + value.slice(end)
  // cursor lands at the first non-blank of the line now at `start` (clamped)
  const cur = Math.min(start, Math.max(0, newValue.length - 1))
  return { value: newValue, cursor: clampNormal(newValue, cur) }
}

// ── NORMAL-mode dispatch ─────────────────────────────────────────────────────

/** Clamp a cursor to a valid NORMAL-mode resting offset (on a char, not past). */
export function clampNormal(value: string, cursor: number): number {
  if (value.length === 0) return 0
  const c = Math.max(0, Math.min(cursor, value.length))
  // don't rest on a newline or past end-of-line
  const ls = lineStart(value, c)
  const le = lineEnd(value, c)
  if (c >= le && le > ls) return le - 1
  return c
}

const MOTION_KEYS = new Set(['h', 'l', 'j', 'k', 'w', 'W', 'b', 'B', 'e', 'E', '0', '^', '$', 'G'])

export interface DispatchResult {
  state: VimState
  buffer: Buffer
  /** True if the key was consumed by the engine (don't pass to the composer). */
  handled: boolean
}

/**
 * Feed one key to the engine in NORMAL mode (or a pending-operator state).
 * Returns the new state+buffer and whether the key was handled. INSERT-mode
 * keys are NOT handled here (the composer edits directly); only `Escape`
 * returns to NORMAL.
 *
 * CONTRACT: the caller must only invoke this when `state.mode === 'normal'`
 * (Part-2 wiring gates on mode). It does not re-check the mode itself.
 *
 * LIMITATION (documented, acceptable for "minimal"): a stacked count like
 * `2d3w` is not multiplied (vim = 6 words); the digits after the operator
 * concatenate with the pre-operator count. Both single-count forms (`2dw`,
 * `d3w`) are correct — only the doubled form is off.
 */
export function dispatchNormal(state: VimState, buffer: Buffer, key: string): DispatchResult {
  // `g` prefix: a lone `g` arms pendingG; the next `g` is the `gg` motion
  // (top of buffer), honoring a pending operator (dgg = delete to top,
  // linewise). Any other key after `g` cancels the prefix.
  if (state.pendingG) {
    const cleared = { ...state, pendingG: false }
    if (key === 'g') {
      if (state.pendingOperator) {
        const op = state.pendingOperator
        const span = operatorSpan('gg', buffer.value, buffer.cursor, state.count || 1)
        const newValue = buffer.value.slice(0, span.start) + buffer.value.slice(span.end)
        return {
          state: { mode: op === 'c' ? 'insert' : 'normal', pendingOperator: null, count: 0, pendingG: false },
          buffer: op === 'c' ? { value: newValue, cursor: span.start }
            : { value: newValue, cursor: clampNormal(newValue, span.start) },
          handled: true,
        }
      }
      return {
        state: { ...cleared, count: 0 },
        buffer: { ...buffer, cursor: 0 },
        handled: true,
      }
    }
    // `g` + other → cancel the prefix and swallow (minimal: no other g-commands)
    return { state: { ...cleared, count: 0, pendingOperator: null }, buffer, handled: true }
  }
  if (key === 'g') {
    return { state: { ...state, pendingG: true }, buffer, handled: true }
  }

  // digit count prefix (1-9, or 0 only when a count is in progress)
  if (/^[1-9]$/.test(key) || (key === '0' && state.count > 0)) {
    return {
      state: { ...state, count: state.count * 10 + Number(key) },
      buffer,
      handled: true,
    }
  }
  const count = state.count || 1

  // pending operator: the next motion (or repeated operator) completes it
  if (state.pendingOperator) {
    const op = state.pendingOperator
    // `dd` / `cc` — doubled operator = linewise current line
    if (key === op || (op === 'c' && key === 'c') || (op === 'd' && key === 'd')) {
      const nb = deleteLine(buffer.value, buffer.cursor, count)
      return {
        state: { mode: op === 'c' ? 'insert' : 'normal', pendingOperator: null, count: 0, pendingG: false },
        buffer: nb,
        handled: true,
      }
    }
    if (MOTION_KEYS.has(key)) {
      // A FAILED vertical motion aborts the whole operator (vim no-op): `dj` on
      // the last line / `dk` on the first line (incl. a single-line buffer)
      // must NOT delete the current line — otherwise `dj` in a one-line chat
      // composer wipes the entire input. Mirrors executeOperatorMotion's
      // `if (target.equals(cursor)) return`. Scoped to j/k (dd/cc are the
      // doubled-operator path; dG/dgg are real jumps). Critic C13 re-review.
      if ((key === 'j' || key === 'k') && applyMotion(key, buffer.value, buffer.cursor, count) === buffer.cursor) {
        return { state: { ...state, pendingOperator: null, count: 0 }, buffer, handled: true }
      }
      // vim special case (:help cw): `cw`/`cW` on a word acts like `ce`/`cE` —
      // change to the END of the word, NOT the start of the next word (so it
      // doesn't swallow the trailing whitespace). Only when the cursor is on a
      // non-blank char; on whitespace, cw behaves like dw.
      let motionKey = key
      if (op === 'c' && (key === 'w' || key === 'W') && !isBlank(buffer.value[buffer.cursor] ?? '')) {
        motionKey = key === 'w' ? 'e' : 'E'
      }
      const span = operatorSpan(motionKey, buffer.value, buffer.cursor, count)
      const newValue = buffer.value.slice(0, span.start) + buffer.value.slice(span.end)
      const nb = { value: newValue, cursor: span.start }
      return {
        state: { mode: op === 'c' ? 'insert' : 'normal', pendingOperator: null, count: 0, pendingG: false },
        buffer: op === 'c' ? nb : { ...nb, cursor: clampNormal(newValue, nb.cursor) },
        handled: true,
      }
    }
    // invalid motion after operator → cancel the operator
    return { state: { ...state, pendingOperator: null, count: 0, pendingG: false }, buffer, handled: true }
  }

  // plain motion
  if (MOTION_KEYS.has(key)) {
    const cur = applyMotion(key, buffer.value, buffer.cursor, count)
    return {
      state: { ...state, count: 0 },
      buffer: { ...buffer, cursor: clampNormal(buffer.value, cur) },
      handled: true,
    }
  }

  switch (key) {
    case 'i': // insert before cursor
      return { state: { mode: 'insert', pendingOperator: null, count: 0, pendingG: false }, buffer, handled: true }
    case 'a': // insert after cursor
      return {
        state: { mode: 'insert', pendingOperator: null, count: 0, pendingG: false },
        buffer: { ...buffer, cursor: Math.min(buffer.cursor + 1, buffer.value.length) },
        handled: true,
      }
    case 'I': // insert at first non-blank
      return {
        state: { mode: 'insert', pendingOperator: null, count: 0, pendingG: false },
        buffer: { ...buffer, cursor: firstNonBlank(buffer.value, buffer.cursor) },
        handled: true,
      }
    case 'A': // insert at end of line
      return {
        state: { mode: 'insert', pendingOperator: null, count: 0, pendingG: false },
        buffer: { ...buffer, cursor: lineEnd(buffer.value, buffer.cursor) },
        handled: true,
      }
    case 'o': { // open a line below
      const le = lineEnd(buffer.value, buffer.cursor)
      const nv = buffer.value.slice(0, le) + '\n' + buffer.value.slice(le)
      return {
        state: { mode: 'insert', pendingOperator: null, count: 0, pendingG: false },
        buffer: { value: nv, cursor: le + 1 },
        handled: true,
      }
    }
    case 'O': { // open a line above
      const ls = lineStart(buffer.value, buffer.cursor)
      const nv = buffer.value.slice(0, ls) + '\n' + buffer.value.slice(ls)
      return {
        state: { mode: 'insert', pendingOperator: null, count: 0, pendingG: false },
        buffer: { value: nv, cursor: ls },
        handled: true,
      }
    }
    case 'x': { // delete char under cursor (by CODE POINT — never split a pair)
      if (buffer.cursor >= buffer.value.length) return { state: { ...state, count: 0 }, buffer, handled: true }
      let end = buffer.cursor
      for (let i = 0; i < count && end < buffer.value.length && buffer.value[end] !== '\n'; i++) {
        end = nextCodePoint(buffer.value, end)
      }
      const nv = buffer.value.slice(0, buffer.cursor) + buffer.value.slice(end)
      return {
        state: { ...state, count: 0 },
        buffer: { value: nv, cursor: clampNormal(nv, buffer.cursor) },
        handled: true,
      }
    }
    case 'd':
    case 'c':
      // PRESERVE the count into the pending-operator state so `2dw` deletes two
      // words (the count before an operator applies to the operator+motion).
      return { state: { ...state, pendingOperator: key as Operator, count: state.count }, buffer, handled: true }
    case 'D': { // delete to end of line
      const le = lineEnd(buffer.value, buffer.cursor)
      const nv = buffer.value.slice(0, buffer.cursor) + buffer.value.slice(le)
      return { state: { ...state, count: 0 }, buffer: { value: nv, cursor: clampNormal(nv, buffer.cursor) }, handled: true }
    }
    case 'C': { // change to end of line
      const le = lineEnd(buffer.value, buffer.cursor)
      const nv = buffer.value.slice(0, buffer.cursor) + buffer.value.slice(le)
      return { state: { mode: 'insert', pendingOperator: null, count: 0, pendingG: false }, buffer: { value: nv, cursor: buffer.cursor }, handled: true }
    }
    default:
      // unknown key in NORMAL: consume it (vim swallows unmapped keys), reset count
      return { state: { ...state, count: 0 }, buffer, handled: true }
  }
}
