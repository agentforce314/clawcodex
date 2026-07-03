import { relative } from 'node:path'

/**
 * Structured Edit/Write diff block — renders a tool patch the way the
 * original Claude Code transcript does (FileEditToolUpdatedMessage +
 * StructuredDiffList + MessageResponse, tools/FileWriteTool/UI.tsx):
 *
 *   ⎿  Added N lines, removed M lines          (update; bold counts)
 *      ... ColorDiff rows: dim right-aligned line numbers, +/- markers,
 *      add/remove backgrounds padded to width, word-level diff,
 *      syntax-highlighted context/added rows ...
 *      ...                                      (dim, between hunks)
 *
 *   ⎿  Wrote N lines to path                   (create; bold count/path)
 *      ... ColorFile rows: first 10 lines, syntax highlighted ...
 *      … +N lines                               (dim truncation hint)
 *
 * The ColorDiff/ColorFile output is pre-wrapped ANSI, handed to <RawAnsi>
 * exactly like the original's StructuredDiff (constant-time Yoga leaf; the
 * screen parser ingests the escapes). Rows are cached at module level keyed
 * on hunk identity — the virtualized transcript remounts rows on
 * resize/scroll and must not re-run highlighting + word diff each time
 * (mirrors the original's RENDER_CACHE, StructuredDiff.tsx).
 */
import { Box, NoSelect, RawAnsi, Text } from '@clawcodex/ink'
import { memo, type ReactNode, useSyncExternalStore } from 'react'

import { ColorDiff, ColorFile, highlighterReady, subscribeHighlighter } from '../lib/colorDiff.js'
import type { Theme } from '../theme.js'
import type { MsgDiffData } from '../types.js'

// Same layout numbers as the original: diff body width = terminal columns
// − 12 (floored so narrow terminals stay renderable), created-file previews
// show the first 10 lines.
const WIDTH_MARGIN = 12
const MIN_BODY_WIDTH = 20
const CREATE_PREVIEW_LINES = 10

// Dim separator between hunks (the original renders <Text dimColor>...</Text>;
// RawAnsi rows carry the styling inline).
const HUNK_SEPARATOR = '\x1b[0m\x1b[2m...\x1b[0m'

/**
 * True when ANSI diff rendering makes sense for this terminal. Under
 * NO_COLOR the raw escape rows would stay fully colored while the rest of
 * the UI goes monochrome — callers should fall back to the plain ```diff
 * markdown path instead. NO_COLOR is the only gate needed: ColorDiff's
 * detectColorMode degrades to 256-color escapes whenever COLORTERM isn't
 * truecolor (e.g. Apple Terminal, where forceTruecolor deliberately deletes
 * COLORTERM), so any color-capable terminal receives valid sequences — and a
 * terminal with no color support at all breaks the chalk-driven UI equally.
 */
export function structuredDiffSupported(): boolean {
  return !process.env.NO_COLOR
}

// ── Module-level render cache ────────────────────────────────────────────
// WeakMap on the hunk object (stable per segment — hunks are capped once at
// ingestion) → per-variant rows. Four variants cover the steady state
// (resize thrash beyond that just recomputes), mirroring the original.
type HunkLike = MsgDiffData['hunks'][number]
const RENDER_CACHE = new WeakMap<HunkLike, Map<string, string[]>>()

function renderHunk(hunk: HunkLike, diff: MsgDiffData, themeName: string, width: number, hlReady: boolean): string[] {
  const key = `${themeName}|${width}|${hlReady ? 1 : 0}|${diff.firstLine ?? ''}|${diff.filePath}`
  let perHunk = RENDER_CACHE.get(hunk)
  const hit = perHunk?.get(key)

  if (hit) {
    return hit
  }

  const rows = new ColorDiff(hunk, diff.firstLine ?? null, diff.filePath, null).render(themeName, width, false) ?? []

  if (!perHunk) {
    perHunk = new Map()
    RENDER_CACHE.set(hunk, perHunk)
  }

  if (perHunk.size >= 4) {
    perHunk.clear()
  }

  perHunk.set(key, rows)

  return rows
}

const countPatchLines = (hunks: HunkLike[]) => {
  let added = 0
  let removed = 0

  for (const hunk of hunks) {
    for (const line of hunk.lines) {
      if (line.startsWith('+')) {
        added++
      } else if (line.startsWith('-')) {
        removed++
      }
    }
  }

  return { added, removed }
}

/** "Added N lines, removed M lines" with the original's bold counts + casing. */
const UpdateSummary = ({ added, removed }: { added: number; removed: number }) => {
  if (added === 0 && removed === 0) {
    return null
  }

  return (
    <Text>
      {added > 0 && (
        <>
          Added <Text bold>{added}</Text> {added > 1 ? 'lines' : 'line'}
        </>
      )}
      {added > 0 && removed > 0 ? ', ' : null}
      {removed > 0 && (
        <>
          {added === 0 ? 'R' : 'r'}emoved <Text bold>{removed}</Text> {removed > 1 ? 'lines' : 'line'}
        </>
      )}
    </Text>
  )
}

const workspaceCwd = () => process.env.CLAWCODEX_WORKSPACE || process.env.CLAWCODEX_CWD || process.cwd()

export const DiffView = memo(function DiffView({ cols, diff, fallback = null, t }: DiffViewProps) {
  // Re-render once when highlight.js finishes registering grammars — rows
  // rendered before that are structurally identical, just untinted.
  const hlReady = useSyncExternalStore(subscribeHighlighter, highlighterReady, highlighterReady)
  const themeName = t.mode
  const width = Math.max(MIN_BODY_WIDTH, cols - WIDTH_MARGIN)

  let summary: null | ReactNode = null
  const rows: string[] = []
  let hint = ''

  try {
    // Branch on kind ALONE (original renderToolResultMessage switch): a
    // Write-created file ships an all-additions hunk but renders as the
    // 10-line content preview, never as diff rows.
    if (diff.kind === 'update') {
      const { added, removed } = countPatchLines(diff.hunks)
      summary = <UpdateSummary added={added} removed={removed} />

      diff.hunks.forEach((hunk, i) => {
        if (i > 0) {
          rows.push(HUNK_SEPARATOR)
        }

        rows.push(...renderHunk(hunk, diff, themeName, width, hlReady))
      })

      if (diff.truncatedLines) {
        hint = `… +${diff.truncatedLines} ${diff.truncatedLines === 1 ? 'line' : 'lines'}`
      }
    } else {
      // create: "Wrote N lines to path" + highlighted preview of the head of
      // the file (tools/FileWriteTool/UI.tsx).
      const all = (diff.content ?? '').split('\n')

      if (all.length > 0 && all.at(-1) === '') {
        all.pop()
      }

      summary = (
        <Text>
          Wrote <Text bold>{all.length}</Text> {all.length === 1 ? 'line' : 'lines'} to{' '}
          <Text bold>{relative(workspaceCwd(), diff.filePath) || diff.filePath}</Text>
        </Text>
      )

      const shown = all.slice(0, CREATE_PREVIEW_LINES)
      rows.push(...(new ColorFile(shown.join('\n'), diff.filePath).render(themeName, width, false) ?? []))
      // ColorFile drops a trailing empty line (Rust .lines() parity), so
      // count the hint from what actually rendered.
      const rendered = shown.length > 0 && shown.at(-1) === '' ? shown.length - 1 : shown.length
      const extra = all.length - rendered

      if (extra > 0) {
        hint = `… +${extra} ${extra === 1 ? 'line' : 'lines'}`
      }
    }
  } catch {
    // A renderer bug must never take down the transcript — degrade to the
    // fenced-text markdown path the caller supplies.
    return <>{fallback}</>
  }

  if (!rows.length && !summary) {
    return null
  }

  return (
    <Box flexDirection="row">
      <NoSelect flexShrink={0} fromLeftEdge>
        <Text color={t.color.muted}>{'  ⎿  '}</Text>
      </NoSelect>
      <Box flexDirection="column" flexGrow={1}>
        {summary}
        {rows.length > 0 && <RawAnsi lines={rows} width={width} />}
        {hint ? (
          <Text color={t.color.muted} dim>
            {hint}
          </Text>
        ) : null}
      </Box>
    </Box>
  )
})

interface DiffViewProps {
  /** Full terminal columns (the component applies the original's −12 margin). */
  cols: number
  diff: MsgDiffData
  /** Rendered when the ANSI pipeline throws (plain ```diff markdown path). */
  fallback?: ReactNode
  t: Theme
}
