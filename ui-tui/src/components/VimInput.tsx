/**
 * Minimal vim modal input (the original's /vim), used in place of ink-text-input
 * only when vim mode is on — so the default input path is untouched. Edits the
 * shared `value` (so the slash/@ menus + history still work off it) and owns a
 * cursor + normal/insert mode via its own useInput (active only when no menu /
 * dialog is up, so App's menu navigation keeps working).
 *
 * Normal: h/l/0/$ move · w/b word · i/a/I/A insert · x delete · D kill-to-end ·
 *   Enter submit.  Insert: type · ←/→ · Backspace · Esc→normal · Enter submit.
 */
import { Text, useInput } from '../ink.js'
import React, { useEffect, useRef, useState } from 'react'
import { theme } from '../theme.js'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: (v: string) => void
  active: boolean
  placeholder?: string
  /** When false, this is the default readline input (insert-only): Esc does not
   *  enter normal mode and no [N]/[I] tag is shown. When true, /vim is on. */
  vimEnabled?: boolean
}

const WORD = /[\p{L}\p{N}_]/u
function nextWord(s: string, c: number): number {
  let i = c
  while (i < s.length && WORD.test(s[i] ?? '')) i++ // end current word
  while (i < s.length && !WORD.test(s[i] ?? '')) i++ // skip gap
  return i
}
function prevWord(s: string, c: number): number {
  let i = c - 1
  while (i > 0 && !WORD.test(s[i] ?? '')) i-- // skip gap
  while (i > 0 && WORD.test(s[i - 1] ?? '')) i-- // to word start
  return Math.max(0, i)
}
function endWord(s: string, c: number): number {
  let i = c + 1
  while (i < s.length && !WORD.test(s[i] ?? '')) i++ // skip gap
  while (i < s.length - 1 && WORD.test(s[i + 1] ?? '')) i++ // to word end
  return Math.min(Math.max(0, s.length - 1), Math.max(c, i))
}
/** Target index for a vim motion char (used by operators + the `.` recorder). */
function motionTarget(s: string, c: number, m: string): number | null {
  if (m === 'w') return nextWord(s, c)
  if (m === 'b') return prevWord(s, c)
  if (m === 'e') return endWord(s, c) + 1
  if (m === '$' || m === 'G') return s.length
  if (m === '0') return 0
  if (m === '^') {
    const f = s.search(/\S/)
    return f >= 0 ? f : 0
  }
  if (m === 'l') return c + 1
  if (m === 'h') return c - 1
  return null
}
const PAIRS: Record<string, string> = { '(': ')', '[': ']', '{': '}', '<': '>' }
/** Range [lo,hi) for a vim text object (iw/aw, i"/a", i(/a(, …). null if none. */
function textObjectRange(s: string, cur: number, around: boolean, obj: string): [number, number] | null {
  if (obj === 'w') {
    let lo = cur
    let hi = cur
    while (lo > 0 && WORD.test(s[lo - 1] ?? '')) lo--
    while (hi < s.length && WORD.test(s[hi] ?? '')) hi++
    if (around) while (hi < s.length && /\s/.test(s[hi] ?? '')) hi++
    return [lo, hi]
  }
  if (obj === '"' || obj === "'" || obj === '`') {
    const onQuote = s[cur] === obj
    const a = s.lastIndexOf(obj, onQuote ? cur - 1 : cur)
    const b = s.indexOf(obj, onQuote ? cur + 1 : cur)
    if (a >= 0 && b > a) return around ? [a, b + 1] : [a + 1, b]
    return null
  }
  const open = obj in PAIRS ? obj : (Object.keys(PAIRS).find((k) => PAIRS[k] === obj) ?? '')
  if (open) {
    const close = PAIRS[open] as string
    const a = s.lastIndexOf(open, cur)
    const b = s.indexOf(close, cur)
    if (a >= 0 && b > a) return around ? [a, b + 1] : [a + 1, b]
  }
  return null
}

export function VimInput({
  value,
  onChange,
  onSubmit,
  active,
  placeholder,
  vimEnabled = true,
}: Props): React.ReactElement {
  const [normal, setNormal] = useState(false) // start in insert (like the original entering /vim)
  const [cursor, setCursor] = useState(value.length)
  const [pendingOp, setPendingOp] = useState<string | null>(null) // operator-pending: d/c/y
  const [pendingFind, setPendingFind] = useState<string | null>(null) // f/F/t/T/r awaiting a char
  const [pendingTextObj, setPendingTextObj] = useState<{ op: string; around: boolean } | null>(null)
  const [count, setCount] = useState('') // numeric count prefix (e.g. 3 in 3w)
  const clamp = (c: number, max = value.length): number => Math.max(0, Math.min(max, c))

  // readline undo (Ctrl+_): track each value change as an undo step. The effect
  // snapshots the prior value on every change; undo pops it back. isUndoing
  // guards the undo's own onChange from being re-recorded.
  const undoStack = useRef<string[]>([])
  const prevValue = useRef(value)
  const isUndoing = useRef(false)
  useEffect(() => {
    if (isUndoing.current) {
      isUndoing.current = false
    } else if (value !== prevValue.current) {
      undoStack.current.push(prevValue.current)
      if (undoStack.current.length > 200) undoStack.current.shift()
    }
    prevValue.current = value
  }, [value])

  // vim unnamed register (populated by x/D/s/C; pasted by p/P).
  const vimReg = useRef('')
  // `.` repeat: last buffer change as a pure replay (current value/cursor → new).
  const lastChange = useRef<((v: string, c: number) => { value: string; cursor: number; reg?: string }) | null>(null)
  // Insert-session capture for `.`: the pre-delete (pure) + the live cursor where
  // typing begins. Set when a change enters insert; consumed on Esc.
  const insertPre = useRef<((v: string, c: number) => { value: string; cursor: number }) | null>(null)
  const insertStart = useRef(0)
  // readline kill-ring / line edits shared by both modes' insert state.
  const killRing = useRef('')
  // kill-ring history + last-yank state for Meta+Y (cycle to an older kill).
  const killHistory = useRef<string[]>([])
  const yankState = useRef<{ pos: number; len: number; idx: number } | null>(null)
  const yankActive = useRef(false) // was the immediately-previous key a yank?
  const pushKill = (s: string): void => {
    if (!s) return
    killRing.current = s
    killHistory.current.unshift(s)
    if (killHistory.current.length > 30) killHistory.current.pop()
  }
  const killWordBack = (): void => {
    const left = value.slice(0, cursor).replace(/\s+$/, '')
    const cut = Math.max(left.lastIndexOf(' '), left.lastIndexOf('/'), left.lastIndexOf('\t')) + 1
    pushKill(value.slice(cut, cursor))
    onChange(value.slice(0, cut) + value.slice(cursor))
    setCursor(cut)
  }
  const readlineEdit = (
    input: string,
    key: { ctrl?: boolean; meta?: boolean; leftArrow?: boolean; rightArrow?: boolean },
    wasYank = false,
  ): boolean => {
    // word-wise cursor movement: Ctrl/Alt + ←/→, or Alt+b / Alt+f
    if ((key.ctrl || key.meta) && key.leftArrow) return (setCursor(prevWord(value, cursor)), true)
    if ((key.ctrl || key.meta) && key.rightArrow) return (setCursor(nextWord(value, cursor)), true)
    if (key.meta && input === 'b') return (setCursor(prevWord(value, cursor)), true)
    if (key.meta && input === 'f') return (setCursor(nextWord(value, cursor)), true)
    // Meta+Y — cycle to an older kill, replacing the just-yanked text (readline).
    if (key.meta && input === 'y') {
      const ys = yankState.current
      if (wasYank && ys && killHistory.current.length > 1) {
        const nidx = (ys.idx + 1) % killHistory.current.length
        const repl = killHistory.current[nidx] ?? ''
        onChange(value.slice(0, ys.pos) + repl + value.slice(ys.pos + ys.len))
        setCursor(ys.pos + repl.length)
        yankState.current = { pos: ys.pos, len: repl.length, idx: nidx }
        yankActive.current = true
      }
      return true
    }
    // Ctrl+_ (\x1f) — undo the last edit (readline standard).
    if (input === '\x1f' || (key.ctrl && input === '_')) {
      const prev = undoStack.current.pop()
      if (prev !== undefined) {
        isUndoing.current = true
        onChange(prev)
        setCursor(prev.length)
      }
      return true
    }
    // Alt+D — kill word forward (readline).
    if (key.meta && input === 'd') {
      const end = nextWord(value, cursor)
      pushKill(value.slice(cursor, end))
      onChange(value.slice(0, cursor) + value.slice(end))
      return true
    }
    // Ctrl+T (\x14) — transpose the two chars around the cursor (readline).
    if (input === '\x14' || (key.ctrl && input === 't')) {
      const n = value.length
      if (n >= 2) {
        const i = cursor >= n ? n - 1 : cursor
        if (i >= 1) {
          onChange(value.slice(0, i - 1) + value[i] + value[i - 1] + value.slice(i + 1))
          setCursor(Math.min(n, i + 1))
        }
      }
      return true
    }
    if (!key.ctrl) return false
    if (input === 'a') return (setCursor(0), true)
    if (input === 'e') return (setCursor(value.length), true)
    if (input === 'w') return (killWordBack(), true)
    if (input === 'u') {
      pushKill(value.slice(0, cursor))
      onChange(value.slice(cursor))
      setCursor(0)
      return true
    }
    if (input === 'k') {
      pushKill(value.slice(cursor))
      onChange(value.slice(0, cursor))
      return true
    }
    if (input === 'y') {
      // yank the last killed text at the cursor; arm Meta+Y cycling.
      if (killRing.current) {
        onChange(value.slice(0, cursor) + killRing.current + value.slice(cursor))
        setCursor(cursor + killRing.current.length)
        yankState.current = { pos: cursor, len: killRing.current.length, idx: 0 }
        yankActive.current = true
      }
      return true
    }
    return false
  }

  // Enter insert mode, arming `.`-repeat capture: `pre` re-applies the change's
  // pre-delete on replay; `start` is the live index where typed text begins.
  const enterInsert = (pre: (v: string, c: number) => { value: string; cursor: number }, start: number): void => {
    insertPre.current = pre
    insertStart.current = start
    setNormal(false)
  }

  useInput(
    (input, key) => {
      if (!active) return
      if (input === '[I' || input === '[O') return // terminal focus events — ignore
      const wasYank = yankActive.current // was the previous key a yank? (for Meta+Y)
      yankActive.current = false
      if (readlineEdit(input, key, wasYank)) return // Ctrl+A/E/W/U/K/Y, Meta+Y in either mode
      if (normal) {
        // Text-object pending (after d/c/y + i/a): this key is the object (w/"/(/…).
        if (pendingTextObj) {
          const { op, around } = pendingTextObj
          setPendingTextObj(null)
          const range = textObjectRange(value, cursor, around, input)
          if (!range) return
          const [lo, hi] = range
          vimReg.current = value.slice(lo, hi)
          if (op !== 'y') {
            onChange(value.slice(0, lo) + value.slice(hi))
            setCursor(lo)
          } else {
            setCursor(lo)
          }
          if (op === 'c') {
            const ar = around
            const ob = input
            return enterInsert((v, c) => {
              const rg = textObjectRange(v, c, ar, ob)
              if (!rg) return { value: v, cursor: c }
              return { value: v.slice(0, rg[0]) + v.slice(rg[1]), cursor: rg[0] }
            }, lo)
          }
          if (op === 'd') {
            const ar = around
            const ob = input
            lastChange.current = (v, cc) => {
              const rg = textObjectRange(v, cc, ar, ob)
              if (!rg) return { value: v, cursor: cc }
              return { value: v.slice(0, rg[0]) + v.slice(rg[1]), cursor: rg[0], reg: v.slice(rg[0], rg[1]) }
            }
          }
          return
        }
        // Find/replace-pending (f/F/t/T/r): this key is the target character.
        if (pendingFind) {
          const fop = pendingFind
          setPendingFind(null)
          const tgt = input
          if (!tgt) return
          if (fop === 'r') {
            if (value && cursor < value.length) {
              onChange(value.slice(0, cursor) + tgt + value.slice(cursor + 1))
            }
            lastChange.current = (v, cc) => ({
              value: cc < v.length ? v.slice(0, cc) + tgt + v.slice(cc + 1) : v,
              cursor: cc,
            })
            return
          }
          if (fop === 'f') {
            const i = value.indexOf(tgt, cursor + 1)
            if (i >= 0) setCursor(i)
          } else if (fop === 'F') {
            const i = value.lastIndexOf(tgt, Math.max(0, cursor - 1))
            if (i >= 0) setCursor(i)
          } else if (fop === 't') {
            const i = value.indexOf(tgt, cursor + 1)
            if (i > 0) setCursor(i - 1)
          } else if (fop === 'T') {
            const i = value.lastIndexOf(tgt, Math.max(0, cursor - 1))
            if (i >= 0) setCursor(i + 1)
          }
          return
        }
        // Operator-pending (d/c/y): the previous key was an operator; this key is
        // its motion (or a repeat for the whole-line form dd/cc/yy).
        if (pendingOp) {
          const op = pendingOp
          setPendingOp(null)
          // d/c/y + i/a → text object (diw, ci", ya(, …)
          if (input === 'i' || input === 'a') {
            setPendingTextObj({ op, around: input === 'a' })
            return
          }
          if (input === op) {
            // dd / cc / yy → whole line
            vimReg.current = value
            if (op !== 'y') {
              onChange('')
              setCursor(0)
            }
            if (op === 'c') {
              enterInsert(() => ({ value: '', cursor: 0 }), 0)
              return
            }
            return
          }
          const target = motionTarget(value, cursor, input)
          if (target === null) return // unknown motion → cancel the operator
          const lo = Math.max(0, Math.min(cursor, target))
          const hi = Math.min(value.length, Math.max(cursor, target))
          vimReg.current = value.slice(lo, hi)
          if (op !== 'y') {
            onChange(value.slice(0, lo) + value.slice(hi))
            setCursor(lo)
          } else {
            setCursor(lo)
          }
          if (op === 'c') {
            const m = input
            return enterInsert((v, c) => {
              const t = motionTarget(v, c, m)
              if (t === null) return { value: v, cursor: c }
              const lo2 = Math.max(0, Math.min(c, t))
              const hi2 = Math.min(v.length, Math.max(c, t))
              return { value: v.slice(0, lo2) + v.slice(hi2), cursor: lo2 }
            }, lo)
          }
          if (op === 'd') {
            const m = input
            lastChange.current = (v, cc) => {
              const t = motionTarget(v, cc, m)
              if (t === null) return { value: v, cursor: cc }
              const lo2 = Math.max(0, Math.min(cc, t))
              const hi2 = Math.min(v.length, Math.max(cc, t))
              return { value: v.slice(0, lo2) + v.slice(hi2), cursor: lo2, reg: v.slice(lo2, hi2) }
            }
          }
          return
        }
        // Count prefix: accumulate digits (0 is a count digit only mid-count;
        // a leading 0 is the line-start motion).
        if (/^[0-9]$/.test(input) && (count !== '' || input !== '0')) {
          setCount(count + input)
          return
        }
        const n = Math.max(1, parseInt(count || '1', 10))
        if (count) setCount('') // consume the count for this command
        if (input === '.') {
          const fn = lastChange.current
          if (fn) {
            const r = fn(value, cursor)
            onChange(r.value)
            setCursor(clamp(r.cursor, Math.max(0, r.value.length - 1)))
            if (r.reg !== undefined) vimReg.current = r.reg
          }
          return
        }
        if (input === 'd' || input === 'c' || input === 'y') return setPendingOp(input)
        if (input === 'f' || input === 'F' || input === 't' || input === 'T' || input === 'r') {
          return setPendingFind(input)
        }
        if (input === 'i') return enterInsert((v, c) => ({ value: v, cursor: c }), cursor)
        if (input === 'a') {
          setCursor((c) => clamp(c + 1))
          return enterInsert((v, c) => ({ value: v, cursor: Math.min(c + 1, v.length) }), Math.min(cursor + 1, value.length))
        }
        if (input === 'A') {
          setCursor(value.length)
          return enterInsert((v) => ({ value: v, cursor: v.length }), value.length)
        }
        if (input === 'I') {
          setCursor(0)
          return enterInsert((v) => ({ value: v, cursor: 0 }), 0)
        }
        if (input === 'h' || key.leftArrow) return setCursor((c) => clamp(c - n))
        if (input === 'l' || key.rightArrow) return setCursor((c) => clamp(c + n))
        if (input === '0') return setCursor(0)
        if (input === '$') return setCursor(Math.max(0, value.length - 1))
        if (input === 'w') {
          let t = cursor
          for (let i = 0; i < n; i++) t = nextWord(value, t)
          return setCursor(t)
        }
        if (input === 'b') {
          let t = cursor
          for (let i = 0; i < n; i++) t = prevWord(value, t)
          return setCursor(t)
        }
        if (input === 'e') {
          let t = cursor
          for (let i = 0; i < n; i++) t = endWord(value, t)
          return setCursor(t)
        }
        if (input === '^') {
          const fnb = value.search(/\S/)
          return setCursor(fnb >= 0 ? fnb : 0)
        }
        if (input === 'G') return setCursor(Math.max(0, value.length - 1))
        if (input === 'x') {
          if (value) {
            vimReg.current = value.slice(cursor, cursor + n)
            onChange(value.slice(0, cursor) + value.slice(cursor + n))
            setCursor((c) => clamp(c, Math.max(0, value.length - n - 1)))
          }
          lastChange.current = (v, cc) => ({
            value: v.slice(0, cc) + v.slice(cc + n),
            cursor: Math.min(cc, Math.max(0, v.length - n - 1)),
            reg: v.slice(cc, cc + n),
          })
          return
        }
        if (input === '~') {
          if (value && cursor < value.length) {
            const c0 = value[cursor] ?? ''
            const tog = c0 === c0.toLowerCase() ? c0.toUpperCase() : c0.toLowerCase()
            onChange(value.slice(0, cursor) + tog + value.slice(cursor + 1))
            setCursor((c) => clamp(c + 1, Math.max(0, value.length - 1)))
          }
          lastChange.current = (v, cc) => {
            if (!v || cc >= v.length) return { value: v, cursor: cc }
            const c0 = v[cc] ?? ''
            const tog = c0 === c0.toLowerCase() ? c0.toUpperCase() : c0.toLowerCase()
            return { value: v.slice(0, cc) + tog + v.slice(cc + 1), cursor: Math.min(cc + 1, Math.max(0, v.length - 1)) }
          }
          return
        }
        if (input === 'D') {
          vimReg.current = value.slice(cursor)
          lastChange.current = (v, cc) => ({ value: v.slice(0, cc), cursor: Math.max(0, cc - 1), reg: v.slice(cc) })
          return onChange(value.slice(0, cursor))
        }
        if (input === 'C') {
          // change to end of line: kill to end + enter insert
          vimReg.current = value.slice(cursor)
          onChange(value.slice(0, cursor))
          return enterInsert((v, c) => ({ value: v.slice(0, c), cursor: c }), cursor)
        }
        if (input === 's') {
          // substitute char: delete char under cursor + enter insert
          if (value) {
            vimReg.current = value[cursor] ?? ''
            onChange(value.slice(0, cursor) + value.slice(cursor + 1))
          }
          return enterInsert((v, c) => ({ value: v.slice(0, c) + v.slice(c + 1), cursor: c }), cursor)
        }
        if (input === 'p' || input === 'P') {
          const before = input === 'P'
          if (vimReg.current) {
            const at = before ? cursor : clamp(cursor + 1)
            onChange(value.slice(0, at) + vimReg.current + value.slice(at))
            setCursor(clamp(at + vimReg.current.length - 1, value.length + vimReg.current.length - 1))
          }
          lastChange.current = (v, cc) => {
            const text = vimReg.current
            if (!text) return { value: v, cursor: cc }
            const at = before ? cc : Math.min(cc + 1, v.length)
            return { value: v.slice(0, at) + text + v.slice(at), cursor: at + text.length - 1 }
          }
          return
        }
        if (key.return) return onSubmit(value)
        return // normal mode swallows everything else
      }
      // insert mode
      if (key.escape) {
        if (vimEnabled) {
          // Record the insert session as a `.`-repeatable change: the typed text
          // between insertStart and the cursor, replayed after the pre-delete.
          if (insertPre.current) {
            const pre = insertPre.current
            const text = value.slice(insertStart.current, cursor)
            insertPre.current = null
            lastChange.current = (v, cc) => {
              const r = pre(v, cc)
              return {
                value: r.value.slice(0, r.cursor) + text + r.value.slice(r.cursor),
                cursor: r.cursor + Math.max(0, text.length - 1),
              }
            }
          }
          setNormal(true)
          setCursor((c) => Math.max(0, c - 1))
        }
        return // when vim is off, Esc is left for App (interrupt) — no normal mode
      }
      if (key.return) return onSubmit(value)
      if (key.leftArrow) return setCursor((c) => clamp(c - 1))
      if (key.rightArrow) return setCursor((c) => clamp(c + 1))
      if (key.backspace || key.delete) {
        if (cursor > 0) {
          onChange(value.slice(0, cursor - 1) + value.slice(cursor))
          setCursor((c) => c - 1)
        }
        return
      }
      if (input && !key.ctrl && !key.meta) {
        onChange(value.slice(0, cursor) + input + value.slice(cursor))
        setCursor((c) => c + input.length)
      }
    },
    { isActive: active },
  )

  // Render: mode tag + text with a block cursor (reverse video).
  const cur = clamp(cursor)
  const empty = value.length === 0
  const tag = vimEnabled ? (normal ? '[N] ' : '[I] ') : ''
  if (empty && placeholder) {
    return (
      <Text>
        <Text color={normal ? theme.warn : theme.accent}>{tag}</Text>
        <Text inverse> </Text>
        <Text color={theme.dim}>{placeholder}</Text>
      </Text>
    )
  }
  const before = value.slice(0, cur)
  const at = value.slice(cur, cur + 1) || ' '
  const after = value.slice(cur + 1)
  return (
    <Text>
      <Text color={normal ? theme.warn : theme.accent}>{tag}</Text>
      <Text>{before}</Text>
      <Text inverse>{at}</Text>
      <Text>{after}</Text>
    </Text>
  )
}
