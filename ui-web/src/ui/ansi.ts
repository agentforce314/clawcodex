/**
 * ANSI SGR → styled spans, for terminal output.
 *
 * Shell tools stream what a terminal would show, escapes included; rendering
 * them raw paints `␛[32m` glyph salad over every colored test run. This is the
 * small, deliberate subset a transcript needs: SGR styling (colors, bold, dim,
 * italic, underline), 256-color and truecolor forms, with every *other* escape
 * (cursor movement, OSC titles) stripped rather than shown. Parsing never
 * fails: unknown codes are ignored and the text always comes through.
 *
 * State carries across lines — `\x1b[31m` on line one colors every line until
 * its reset — so a whole output is parsed in one pass, not line by line.
 */

export interface AnsiStyle {
  /** 0–15 for the classic palette, or a CSS color for 256/truecolor forms. */
  bg?: number | string
  bold?: boolean
  dim?: boolean
  fg?: number | string
  italic?: boolean
  underline?: boolean
}

export interface AnsiSpan extends AnsiStyle {
  text: string
}

/** One output line as styled runs; empty lines yield an empty array. */
export type AnsiLine = AnsiSpan[]

// SGR only: `\x1b[…m`. Every other CSI final byte is movement/erase noise in a
// transcript, matched by the broader pattern below and dropped.
const CSI_SGR = /^\x1b\[([0-9;]*)m/
// Any CSI sequence (parameter, intermediate, final byte), OSC terminated by
// BEL or ST, and the stray two-byte escapes some tools emit.
const OTHER_ESCAPE = /^(?:\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_])/

/** The xterm 256-color cube/grayscale, resolved to CSS. 0–15 stay palette ids. */
function color256(index: number): number | string {
  if (index < 0) return 7
  if (index < 16) return index

  if (index < 232) {
    const cube = index - 16
    const step = (value: number) => (value === 0 ? 0 : 55 + value * 40)
    const r = step(Math.floor(cube / 36))
    const g = step(Math.floor(cube / 6) % 6)
    const b = step(cube % 6)

    return `rgb(${r}, ${g}, ${b})`
  }

  if (index < 256) {
    const gray = 8 + (index - 232) * 10

    return `rgb(${gray}, ${gray}, ${gray})`
  }

  return 7
}

/**
 * Apply one SGR parameter list to `style`, returning the next style.
 * An empty parameter list (`\x1b[m`) is a reset, per the standard.
 */
function applySgr(params: string, style: AnsiStyle): AnsiStyle {
  const next: AnsiStyle = { ...style }
  const codes = params === '' ? [0] : params.split(';').map(part => Number.parseInt(part || '0', 10))

  for (let i = 0; i < codes.length; i += 1) {
    const code = codes[i] ?? 0

    if (code === 0) {
      delete next.bg
      delete next.bold
      delete next.dim
      delete next.fg
      delete next.italic
      delete next.underline
    } else if (code === 1) next.bold = true
    else if (code === 2) next.dim = true
    else if (code === 3) next.italic = true
    else if (code === 4) next.underline = true
    else if (code === 21 || code === 22) {
      delete next.bold
      delete next.dim
    } else if (code === 23) delete next.italic
    else if (code === 24) delete next.underline
    else if (code >= 30 && code <= 37) next.fg = code - 30
    else if (code === 39) delete next.fg
    else if (code >= 40 && code <= 47) next.bg = code - 40
    else if (code === 49) delete next.bg
    else if (code >= 90 && code <= 97) next.fg = code - 90 + 8
    else if (code >= 100 && code <= 107) next.bg = code - 100 + 8
    else if (code === 38 || code === 48) {
      // Extended color: `38;5;N` (256) or `38;2;R;G;B` (truecolor). Both
      // consume their arguments even when the values are out of range, so a
      // malformed sequence cannot smear into the codes after it.
      const mode = codes[i + 1]

      if (mode === 5) {
        const value = color256(codes[i + 2] ?? 7)

        if (code === 38) next.fg = value
        else next.bg = value

        i += 2
      } else if (mode === 2) {
        const [r, g, b] = [codes[i + 2] ?? 0, codes[i + 3] ?? 0, codes[i + 4] ?? 0]
        const value = `rgb(${r}, ${g}, ${b})`

        if (code === 38) next.fg = value
        else next.bg = value

        i += 4
      }
    }
    // Anything else (blink, inverse, fonts…) is ignored on purpose.
  }

  return next
}

function isStyled(style: AnsiStyle): boolean {
  return (
    style.bg !== undefined ||
    style.bold === true ||
    style.dim === true ||
    style.fg !== undefined ||
    style.italic === true ||
    style.underline === true
  )
}

/**
 * Parse terminal output into lines of styled spans.
 *
 * `\r` is honored the way a terminal honors it: text after a carriage return
 * overwrites the line so far, which is what turns a progress bar's hundred
 * frames into its final state instead of one endless line. CRLF is plain
 * line ending, not an overwrite.
 */
export function parseAnsi(text: string): AnsiLine[] {
  const lines: AnsiLine[] = []
  let spans: AnsiSpan[] = []
  let style: AnsiStyle = {}
  let plain = ''

  const flushText = () => {
    if (plain === '') return

    spans.push(isStyled(style) ? { ...style, text: plain } : { text: plain })
    plain = ''
  }

  const endLine = () => {
    flushText()
    lines.push(spans)
    spans = []
  }

  const source = text.replace(/\r+\n/g, '\n').replace(/\n$/, '')
  let index = 0

  while (index < source.length) {
    const char = source[index] as string

    if (char === '\x1b') {
      const rest = source.slice(index)
      const sgr = CSI_SGR.exec(rest)

      if (sgr !== null) {
        flushText()
        style = applySgr(sgr[1] ?? '', style)
        index += sgr[0].length
        continue
      }

      const other = OTHER_ESCAPE.exec(rest)

      if (other !== null) {
        index += other[0].length
        continue
      }

      // A bare escape with no recognizable sequence: drop the byte alone.
      index += 1
      continue
    }

    if (char === '\n') {
      endLine()
      index += 1
      continue
    }

    if (char === '\r') {
      // Overwrite: everything accumulated on this line so far is what the
      // progress bar painted over.
      spans = []
      plain = ''
      index += 1
      continue
    }

    plain += char
    index += 1
  }

  endLine()

  return lines
}

/** True when the text carries any escape worth parsing. */
export function hasAnsi(text: string): boolean {
  return text.includes('\x1b') || text.includes('\r')
}

/** The text alone — what a copy affordance should yield. */
export function stripAnsi(text: string): string {
  return parseAnsi(text)
    .map(line => line.map(span => span.text).join(''))
    .join('\n')
}
