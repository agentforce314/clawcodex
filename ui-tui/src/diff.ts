/**
 * Minimal line diff for Edit/Write tool calls, rendered Claude-Code style
 * (green added / red removed lines with a line-number gutter). Not a full Myers
 * diff — it trims the common prefix/suffix and shows the changed middle, which
 * is exactly right for an Edit's old_string→new_string region.
 */
export interface DiffLine {
  type: 'add' | 'del' | 'ctx'
  text: string
  oldNo?: number
  newNo?: number
}

export function lineDiff(oldStr: string, newStr: string, startLine = 1): DiffLine[] {
  const o = oldStr.split('\n')
  const n = newStr.split('\n')
  let p = 0
  while (p < o.length && p < n.length && o[p] === n[p]) p++
  let s = 0
  while (s < o.length - p && s < n.length - p && o[o.length - 1 - s] === n[n.length - 1 - s]) s++
  const out: DiffLine[] = []
  let oldNo = startLine
  let newNo = startLine
  for (let i = 0; i < p; i++) out.push({ type: 'ctx', text: o[i] ?? '', oldNo: oldNo++, newNo: newNo++ })
  for (let i = p; i < o.length - s; i++) out.push({ type: 'del', text: o[i] ?? '', oldNo: oldNo++ })
  for (let i = p; i < n.length - s; i++) out.push({ type: 'add', text: n[i] ?? '', newNo: newNo++ })
  for (let i = 0; i < s; i++) {
    out.push({ type: 'ctx', text: o[o.length - s + i] ?? '', oldNo: oldNo++, newNo: newNo++ })
  }
  return out
}

/** Build a diff for an Edit/Write/MultiEdit tool call from its raw input. */
export function toolDiff(toolName: string, input: Record<string, unknown>): DiffLine[] | null {
  if (toolName === 'Write') {
    const content = typeof input['content'] === 'string' ? (input['content'] as string) : ''
    if (!content) return null
    return content.split('\n').map((text, i) => ({ type: 'add', text, newNo: i + 1 }))
  }
  if (toolName === 'Edit') {
    const oldS = typeof input['old_string'] === 'string' ? (input['old_string'] as string) : ''
    const newS = typeof input['new_string'] === 'string' ? (input['new_string'] as string) : ''
    if (!oldS && !newS) return null
    return lineDiff(oldS, newS)
  }
  if (toolName === 'MultiEdit') {
    const edits = Array.isArray(input['edits']) ? (input['edits'] as Record<string, unknown>[]) : []
    const out: DiffLine[] = []
    for (const e of edits) {
      const oldS = typeof e['old_string'] === 'string' ? (e['old_string'] as string) : ''
      const newS = typeof e['new_string'] === 'string' ? (e['new_string'] as string) : ''
      out.push(...lineDiff(oldS, newS))
    }
    return out.length ? out : null
  }
  return null
}
