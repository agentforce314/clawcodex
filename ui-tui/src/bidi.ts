/**
 * RTL run shaping (the original's bidi handling, inventory §8) — pure TS, no deps.
 *
 * Scope: reorders right-to-left runs (Hebrew/Arabic) into visual order within
 * LTR-base text, for terminals that don't apply the Unicode Bidirectional
 * Algorithm themselves. This covers the common case — RTL words embedded in
 * otherwise-LTR content — correctly and verifiably. It is intentionally NOT a
 * full UAX #9 implementation (no base-RTL paragraph resolution, no nested
 * embedding levels); it's opt-in (/rtl) precisely because terminals that DO
 * their own bidi would otherwise double-reorder.
 */

type CharType = 'R' | 'EN' | 'L' | 'N'

function classify(cp: number): CharType {
  // Right-to-left scripts (Hebrew, Arabic, and their presentation forms).
  if (
    (cp >= 0x0590 && cp <= 0x05ff) || // Hebrew
    (cp >= 0xfb1d && cp <= 0xfb4f) || // Hebrew presentation forms
    (cp >= 0x0600 && cp <= 0x06ff) || // Arabic
    (cp >= 0x0750 && cp <= 0x077f) || // Arabic supplement
    (cp >= 0x08a0 && cp <= 0x08ff) || // Arabic extended-A
    (cp >= 0xfb50 && cp <= 0xfdff) || // Arabic presentation forms-A
    (cp >= 0xfe70 && cp <= 0xfeff) //   Arabic presentation forms-B
  ) {
    return 'R'
  }
  if (cp >= 0x0030 && cp <= 0x0039) return 'EN' // ASCII digits (stay LTR inside RTL)
  if (cp >= 0x0660 && cp <= 0x0669) return 'EN' // Arabic-Indic digits (kept LTR-order)
  return 'L' // everything else treated as LTR/neutral for run boundaries
  // (N is folded into L here; interior-neutral handling is done in shapeRtl)
}

/** True if the string contains any RTL characters (cheap pre-check). */
export function hasRtl(s: string): boolean {
  for (const ch of s) {
    if (classify(ch.codePointAt(0) ?? 0) === 'R') return true
  }
  return false
}

/**
 * Reverse maximal RTL runs (including interior spaces/punctuation between two
 * RTL chars) so they read correctly on a non-bidi terminal. Digit sub-runs
 * inside an RTL run keep their logical order (numbers are read LTR).
 */
export function shapeRtl(s: string): string {
  if (!hasRtl(s)) return s
  const chars = [...s]
  const n = chars.length
  const isR = chars.map((c) => classify(c.codePointAt(0) ?? 0) === 'R')
  const isSpace = chars.map((c) => {
    const cp = c.codePointAt(0) ?? 0
    return cp === 0x20 || (cp >= 0x21 && cp <= 0x2f) || (cp >= 0x3a && cp <= 0x40) // space + ASCII punct
  })
  const isDigit = chars.map((c) => classify(c.codePointAt(0) ?? 0) === 'EN')
  const out = chars.slice()

  const reverse = (lo: number, hi: number) => {
    for (let a = lo, b = hi - 1; a < b; a++, b--) {
      const t = out[a]!
      out[a] = out[b]!
      out[b] = t
    }
  }

  let i = 0
  while (i < n) {
    if (!isR[i]) {
      i++
      continue
    }
    // Extend the run over RTL chars, interior spaces/punct, and digits.
    let j = i + 1
    while (j < n && (isR[j] || isDigit[j] || (isSpace[j] && j + 1 < n && (isR[j + 1] || isDigit[j + 1])))) {
      j++
    }
    reverse(i, j)
    // Restore logical order of digit sub-runs (they were reversed above).
    for (let k = i; k < j; ) {
      // after reversal, find digit sub-runs in `out` and un-reverse them
      const origIdx = i + (j - 1 - k)
      if (isDigit[origIdx]) {
        let m = k
        while (m < j) {
          const oi = i + (j - 1 - m)
          if (!isDigit[oi]) break
          m++
        }
        reverse(k, m)
        k = m
      } else {
        k++
      }
    }
    i = j
  }
  return out.join('')
}
