import { TERMUX_TUI_MODE } from '../config/env.js'
import { briefCallOfTrailLine, briefRuns, briefText, countBriefTools } from '../domain/toolBrief.js'
import type { Msg } from '../types.js'

import { transcriptBodyWidth, transcriptTrailWidth } from './inputMetrics.js'
import { parseToolTrailResultLine } from './text.js'

const hashText = (text: string) => {
  let h = 5381

  for (let i = 0; i < text.length; i++) {
    h = ((h << 5) + h) ^ text.charCodeAt(i)
  }

  return (h >>> 0).toString(36)
}

export const messageHeightKey = (msg: Msg) => {
  const todoSig = msg.todos?.map(t => `${t.status}:${t.content}`).join('\u0001') ?? ''

  const panelSig =
    msg.panelData?.sections
      .map(s => `${s.title ?? ''}:${s.text?.length ?? 0}:${s.items?.length ?? 0}:${s.rows?.length ?? 0}`)
      .join('\u0001') ?? ''

  const introSig = msg.kind === 'intro' ? (msg.info?.version ?? '') : ''

  return [
    msg.role,
    msg.kind ?? '',
    hashText(
      [
        msg.text,
        msg.thinking ?? '',
        msg.tools?.join('\n') ?? '',
        msg.toolsVerbose?.join('\n') ?? '',
        todoSig,
        panelSig,
        introSig
      ].join('\0')
    )
  ].join(':')
}

// Hard cap on rows the estimator will count. Each row above this is
// invisible to the estimator (gets clipped to MAX_ESTIMATE_LINES), but
// post-mount Yoga measurement converges to the real height on first
// render. Without this, a long assistant turn (10k+ chars) costs O(text)
// per offset rebuild × every uncached item — cold-mounting a 1000-row
// transcript becomes a multi-million-char wrap walk that blocks the UI.
//
// 800 covers any realistic assistant message (the prior history-clip
// ceiling was 16 lines, then full text — this is the sane middle).
const MAX_ESTIMATE_LINES = 800

export const wrappedLines = (text: string, width: number, maxLines: number = MAX_ESTIMATE_LINES) => {
  const w = Math.max(1, width)
  // Worst case: every cell is its own row at width=1, plus a small
  // slack for the trailing partial line. Walking past this byte budget
  // cannot increase n any further once n is already past maxLines, so
  // bail. Saves O(text) walks on multi-megabyte single-line messages.
  const budget = Math.min(text.length, maxLines * w + maxLines)
  let n = 0
  let start = 0

  for (let i = 0; i <= budget; i++) {
    if (i === text.length || i === budget || text.charCodeAt(i) === 10) {
      const rows = Math.max(1, Math.ceil((i - start) / w))
      n += rows >= maxLines - n ? maxLines - n : rows
      start = i + 1

      if (n >= maxLines) {
        return maxLines
      }
    }
  }

  return n
}

/**
 * One entry per trail line that actually paints a `⏺ Tool(args)` block, with
 * the rows it costs. Lines that produce no group — gateway meta notes, which
 * ToolTrail routes to the (hidden-by-default) activity panel — drop out here,
 * so both layouts below count blocks, never raw lines.
 */
interface TrailEntry {
  call: string
  error: boolean
  rows: number
}

// Gutters a `⏺ …` block reserves: 2 columns for the bullet on the call row,
// 5 for the `  ⎿  ` connector (and the matching pad on continuations) under it.
// The reasoning body's `  └─ ` rail lands on the same column 5.
//
// Known residual: wrappedLines is character math while Ink word-wraps, so a
// detail carrying one token longer than the content width (a deep file path)
// can paint one row more than this counts. Post-mount measurement corrects it;
// it is a ±1 on long paths, not a structural divergence.
const CALL_GUTTER = 2
const DETAIL_GUTTER = 5

const trailEntries = (msg: Msg, trailWidth: number, toolsExpanded: boolean): TrailEntry[] => {
  const entries: TrailEntry[] = []

  const detailRows = (detail: string) =>
    detail ? detail.split('\n').reduce((sum, row) => sum + wrappedLines(row, trailWidth - DETAIL_GUTTER), 0) : 0

  for (const [i, line] of (msg.tools ?? []).entries()) {
    // Read both the call and the drafting marker off the SAME line the
    // renderer will draw — the verbose sibling when one is being shown.
    const rendered = (toolsExpanded && msg.toolsVerbose?.[i]) || line
    const parsed = parseToolTrailResultLine(rendered)

    if (parsed) {
      entries.push({
        call: parsed.call,
        error: parsed.mark === '✗',
        // The `⏺` call row, then the `⎿` detail — both of which wrap: an Edit
        // detail carries a full path and is capped in lines, never columns.
        rows: wrappedLines(parsed.call, trailWidth - CALL_GUTTER) + detailRows(parsed.detail)
      })
    } else if (rendered.startsWith('drafting ')) {
      // Call row + the static "drafting..." detail row.
      entries.push({ call: briefCallOfTrailLine(rendered), error: false, rows: 2 })
    }

    // Anything else is a gateway meta note. ToolTrail routes those to the
    // activity panel (hidden by default), so they paint no row here. The one
    // transient it does fold onto the previous group, "analyzing tool
    // output…", is filtered by isTransientTrailLine before a line ever
    // reaches msg.tools, so it cannot appear in a settled trail.
  }

  return entries
}

/**
 * Rows a message's tool trail paints, matching ToolTrail's two layouts:
 *
 *   expanded (ctrl+o) — every call keeps its `⏺ …` + `⎿ …` block, verbose
 *     sibling when one exists.
 *   collapsed (default) — consecutive collapsible calls fold to one brief
 *     line; standalone calls (edits, delegations, questions) and failures
 *     keep their block.
 *
 * Both walk the same briefRuns() split the renderer uses, and both add the
 * blank line it opens between consecutive blocks.
 */
const trailRows = (msg: Msg, trailWidth: number, toolsExpanded: boolean) => {
  const entries = trailEntries(msg, trailWidth, toolsExpanded)

  if (toolsExpanded) {
    return entries.reduce((sum, entry) => sum + entry.rows, 0) + Math.max(0, entries.length - 1)
  }

  return briefRuns(
    entries,
    entry => entry.call,
    entry => entry.error
  ).reduce((sum, run, index) => {
    const gap = index > 0 ? 1 : 0

    if (run.kind === 'flat') {
      return sum + gap + run.items.reduce((rows, entry) => rows + entry.rows, 0)
    }

    // The brief sits under a 2-column gutter, so it wraps 2 narrower.
    const text = briefText(countBriefTools(run.items.map(entry => entry.call)))

    return sum + gap + (text ? wrappedLines(text, trailWidth - 2) : 0)
  }, 0)
}

export const estimatedMsgHeight = (
  msg: Msg,
  cols: number,
  {
    compact,
    details,
    leadGap = false,
    thinkingVisible = details,
    toolsExpanded = false,
    toolsVisible = details,
    userPrompt = '',
    withSeparator = false
  }: {
    compact: boolean
    details: boolean
    leadGap?: boolean
    thinkingVisible?: boolean
    toolsExpanded?: boolean
    toolsVisible?: boolean
    userPrompt?: string
    withSeparator?: boolean
  }
) => {
  if (msg.kind === 'intro') {
    return msg.info?.version ? 9 : 5
  }

  if (msg.kind === 'panel') {
    return Math.max(3, (msg.panelData?.sections.length ?? 1) * 2 + 1)
  }

  if (msg.kind === 'trail' && msg.todos?.length) {
    // Only DONE lists reach the transcript now (incomplete ones stay live in
    // the HUD), and the archive always sets todoCollapsedByDefault — so an
    // archived checklist estimates as its collapsed header row.
    return 2
  }

  const bodyWidth = transcriptBodyWidth(cols, msg.role, userPrompt, TERMUX_TUI_MODE)
  const text = msg.text
  // A `trail` block paints no text row at all (MessageLine hands it straight
  // to ToolTrail), so it must not be charged the one-row floor every prose
  // block gets — that alone doubled the estimate for a one-row brief.
  let h = msg.kind === 'trail' ? 0 : wrappedLines(text || ' ', bodyWidth)

  if (!compact && msg.role === 'assistant') {
    // Paragraph gaps add up to 6 extra rows of breathing room. Slice
    // first so the regex never walks more than the first ~16k chars of
    // a giant assistant message — post-mount Yoga measurement converges
    // to the real height regardless of how the estimate undercounts.
    const scan = text.length > 16_000 ? text.slice(0, 16_000) : text
    h += Math.min(6, (scan.match(/\n\s*\n/g) ?? []).length)
  }

  if (details) {
    const hasVisibleTools = toolsVisible && Boolean(msg.tools?.length)
    const hasVisibleThinking = thinkingVisible && /\S/.test(msg.thinking ?? '')
    const hasVisibleDetails = hasVisibleTools || hasVisibleThinking

    if (hasVisibleDetails) {
      const trailWidth = transcriptTrailWidth(cols, TERMUX_TUI_MODE)

      // Tool entries can carry multi-line details (Bash 3-line summaries,
      // 10-line error caps) — count rendered rows, not entries, or off-screen
      // estimates under-count and the scrollbar/topSpacer math jumps.
      const toolRows = hasVisibleTools ? trailRows(msg, trailWidth, toolsExpanded) : 0

      // The reasoning panel is its `∴ Thinking…` header row plus the body,
      // which sits under a `  └─ ` rail — content starts at column 5, so
      // DETAIL_GUTTER is the right offset here too. The header used to be paid
      // for by the one-row text floor above; trail blocks no longer get that
      // floor, so charge it here where it is actually true.
      // (Known gap, pre-dating the brief: `/details thinking collapsed` closes
      // the body while this still counts it. The panel is expanded by default,
      // so the common path is exact.)
      const thinkingRows = hasVisibleThinking ? 1 + wrappedLines(msg.thinking ?? '', trailWidth - DETAIL_GUTTER) : 0

      h += toolRows + thinkingRows

      // A prose row wraps its details in a Box with marginBottom={1}. Trail
      // blocks hand ToolTrail straight through, and the structured-diff branch
      // brings its own wrapper with ToolTrail as a direct child — neither pays
      // this. (The diff branch's estimate is off for older reasons too: it
      // still counts msg.text for a markdown fallback the structured path
      // never renders. Out of scope here; just don't add to it.)
      if (msg.kind !== 'trail' && msg.kind !== 'diff') {
        h++
      }

      // "Response" separator row + its own marginBottom.
      if (msg.role === 'assistant' && /\S/.test(msg.text)) {
        h += 2
      }
    }
  }

  if (msg.role === 'user' || msg.kind === 'diff') {
    // Top + bottom blank line.
    h += 2
  } else if (msg.kind === 'slash') {
    h++
  }

  // Group-boundary blank line owned by BlockSlot: model prose, reasoning/tool
  // trails, and notes/errors each start a new visual group when the block
  // above them is a different kind. The caller resolves the boundary against
  // the previous row (see domain/blockLayout.ts::hasLeadGap) and passes the
  // result here so the estimate matches the rendered marginTop before Yoga
  // remeasures. user / diff / slash never set this — they own their margins.
  if (leadGap) {
    h++
  }

  // Monochrome fallback: the `───` inter-turn separator above non-first user
  // rows (1 rule row + 1 top-margin row). Rendered only when color is
  // disabled — the band replaces it otherwise. The caller passes the
  // domain/blockLayout.ts::showsInterTurnSeparator result so this estimate
  // matches the appLayout.tsx render gate exactly.
  if (withSeparator) {
    h += 2
  }

  return Math.max(1, h)
}
