/**
 * Ink TUI for the clawcodex Python agent-server — a Claude-Code-style thin
 * client. All agent logic (model, tools, permissions) runs in the Python
 * backend; this process renders the streamed transcript (markdown, tool calls,
 * results), a live token stream + working spinner, permission prompts (queued
 * so concurrent tool asks aren't dropped), a slash-command menu, and an input
 * line, over the Direct Connect protocol.
 */
import { Box, Text, useApp, useInput } from './ink.js'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import React, { useEffect, useRef, useState } from 'react'
import { DirectConnectClient } from './client.js'
import type { Transport } from './transport.js'
import { Message } from './components/Message.js'
import { PermissionDialog } from './components/PermissionDialog.js'
import { SlashMenu } from './components/SlashMenu.js'
import { editInEditor } from './editor.js'
import { isTrusted, trustFolder, untrustFolder, isMcpTrusted, trustMcp } from './trust.js'
import { matchesBinding, bindingConflicts } from './keybindings.js'
import { configErrors } from './configCheck.js'
import { exec } from 'node:child_process'
import { readFileSync, existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { BUDDY_SPECIES, CompanionSprite } from './components/CompanionSprite.js'
import { FileMenu } from './components/FileMenu.js'
import { VimInput } from './components/VimInput.js'
import { searchFiles, prewarmFileIndex } from './fileIndex.js'
import { note as perfNote, bumpRender as perfBumpRender } from './perfDebug.js'
import { Spinner } from './components/Spinner.js'
import { StatusBar } from './components/StatusBar.js'
import { DevBar } from './components/DevBar.js'
import { LiveTools, type LiveGroup } from './components/LiveTools.js'
import { AgentProgressLine, type AgentLine } from './components/AgentProgressLine.js'
import { READ_LIKE, TOOL_VERB, toolActivityLabel } from './toolMeta.js'
import { messageToEntries, streamDeltaText, streamThinkingDelta, type TranscriptEntry } from './sdkMessageAdapter.js'
import { matchSlash, resolveSlash } from './slashCommands.js'
import { LOGO_PALETTES, setLogoPalette, getLogoPalette } from './components/Logo.js'
import { parseProtocolMajor, SUPPORTED_PROTOCOL_MAJOR } from './protocol.js'
import { applyTheme, currentThemeName, theme } from './theme.js'

interface Props {
  transport: Transport
  serverLabel: string
}

interface PendingPermission {
  requestId: string
  toolName: string
  input: Record<string, unknown>
}

const HELP = [
  'Commands:',
  '  /help   /clear   /quit',
  '  /model <m>   /mode <default|acceptEdits|plan|…>   /theme <dark|light>',
  '  /context   (window usage)        /compact [instructions]   (summarize)',
  'Keys:',
  '  enter — send        esc — interrupt        ^C / ^D — quit',
  '  ↑↓ — history        ^R — reverse-search    tab — complete',
  '  @ — file mention    / — command menu       (type while busy → queued)',
].join('\n')

/**
 * Tail of a live buffer that fits `maxLines` visual rows at `cols` width — hard-
 * wraps each logical line, then keeps the last `maxLines`. Keeps the streaming
 * (non-Static) region inside the viewport so Ink can erase it cleanly instead of
 * leaking re-rendered copies into scrollback.
 */
/** Serialize the transcript to readable Markdown (for /export). */
function transcriptToMarkdown(entries: TranscriptEntry[]): string {
  const out: string[] = ['# clawcodex transcript', '']
  for (const e of entries) {
    switch (e.kind) {
      case 'user':
        out.push(`### › ${e.text}`, '')
        break
      case 'assistant':
        out.push(e.text, '')
        break
      case 'thinking':
        out.push(`> ∴ _${e.text.replace(/\n/g, ' ')}_`, '')
        break
      case 'tool': {
        if (e.todos) {
          out.push('**Todos:**')
          for (const t of e.todos) out.push(`- [${t.status === 'completed' ? 'x' : ' '}] ${t.content}`)
          out.push('')
        } else if (e.agent) {
          out.push(`\`Task(${e.agent.description})\`${e.agent.subagentType ? ` · ${e.agent.subagentType}` : ''}`, '')
        } else {
          const name = e.diff ? e.diff.displayName : e.toolName
          out.push(`\`${name}(${e.argsText ?? ''})\``)
          if (e.diff) {
            out.push('```diff')
            if (e.diff.kind === 'write' && e.diff.content) out.push(...e.diff.content.split('\n').map((l) => `+${l}`))
            else for (const h of e.diff.hunks) out.push(...h.lines)
            out.push('```')
          }
          out.push('')
        }
        break
      }
      case 'toolResult':
        out.push('```', e.text, '```', '')
        break
      case 'result':
        out.push(`_${e.text}_`, '')
        break
      case 'error':
        out.push(`**Error:** ${e.text}`, '')
        break
      case 'system':
        out.push(`_${e.text}_`, '')
        break
      default:
        break
    }
  }
  return out.join('\n')
}

/** Compact relative age for the /resume list (updated_at in epoch seconds). */
function relAge(sec: number): string {
  if (!sec) return ''
  const diff = Date.now() / 1000 - sec
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

/** Opt-in fullscreen/alt-screen transcript (CLAWCODEX_FULLSCREEN=1). Default off
 *  keeps the proven inline <Static> path untouched. */
const FULLSCREEN = process.env['CLAWCODEX_FULLSCREEN'] === '1'

/** Searchable text of an entry (for fullscreen Ctrl+F find). */
export function entryText(e: TranscriptEntry): string {
  if (e.text) return e.text
  if (e.todos) return e.todos.map((t) => t.content).join(' ')
  if (e.agent) return e.agent.description
  return e.argsText ?? ''
}

/** Generous upper estimate of an entry's rendered rows (for fullscreen
 *  windowing — over-estimating under-fills the viewport, never overflows). */
export function estimateRows(e: TranscriptEntry, cols: number): number {
  const w = Math.max(20, cols - 2)
  const wrap = (s: string): number =>
    (s || '').split('\n').reduce((a, ln) => a + Math.max(1, Math.ceil((ln.length || 1) / w)), 0)
  const top = 1 // inter-entry marginTop
  switch (e.kind) {
    case 'banner':
      return 14
    case 'thinking':
      return top + Math.min(16, wrap(e.text)) + 1
    case 'tool': {
      if (e.todos) return top + e.todos.length + 1
      let r = top + 1
      if (e.diff) {
        r +=
          (e.diff.kind === 'write'
            ? Math.min(12, (e.diff.content || '').split('\n').length)
            : e.diff.hunks.reduce((a, h) => a + h.lines.length, 0)) + 3
      }
      return r
    }
    case 'toolResult':
      return top + Math.min(9, wrap(e.text))
    case 'context':
      return top + (e.contextData?.categories.length ?? 0) + 3
    default:
      return top + wrap(e.text)
  }
}

/** Collapse runs of consecutive Read (tool + result) pairs into a single
 *  "Read N files" line (the original's grouped-read, inventory §3). Sequential
 *  reads arrive as tool,result,tool,result, so adjacency detection suffices. */
export function groupReads(entries: TranscriptEntry[]): TranscriptEntry[] {
  const out: TranscriptEntry[] = []
  let i = 0
  while (i < entries.length) {
    let j = i
    let count = 0
    while (
      j + 1 < entries.length &&
      entries[j]?.kind === 'tool' &&
      entries[j]?.toolName === 'Read' &&
      entries[j + 1]?.kind === 'toolResult'
    ) {
      count++
      j += 2
    }
    if (count >= 2) {
      out.push({ id: `${entries[i]?.id}-rg`, kind: 'system', text: `⎿ Read ${count} files` })
      i = j
    } else {
      out.push(entries[i] as TranscriptEntry)
      i++
    }
  }
  return out
}

/** The tail window of entries that fits `viewportRows`, after hiding
 *  `scrollOffset` entries from the bottom. Pure — unit-tested. */
export function windowFromBottom(
  entries: TranscriptEntry[],
  cols: number,
  viewportRows: number,
  scrollOffset: number,
): TranscriptEntry[] {
  if (entries.length === 0) return []
  const end = Math.max(1, Math.min(entries.length, entries.length - scrollOffset))
  let used = 0
  let start = end
  for (let i = end - 1; i >= 0; i--) {
    const h = estimateRows(entries[i] as TranscriptEntry, cols)
    if (used + h > viewportRows && start < end) break
    used += h
    start = i
  }
  return entries.slice(start, end)
}

/** The chunk inserted between `oldV` and `newV` (common prefix/suffix diff). */
function diffInsert(oldV: string, newV: string): { ins: string; p: number; s: number } {
  let p = 0
  const max = Math.min(oldV.length, newV.length)
  while (p < max && oldV[p] === newV[p]) p++
  let s = 0
  while (s < oldV.length - p && s < newV.length - p && oldV[oldV.length - 1 - s] === newV[newV.length - 1 - s]) s++
  return { ins: newV.slice(p, newV.length - s), p, s }
}

function streamTail(text: string, cols: number, maxLines: number): string {
  if (maxLines < 1) maxLines = 1
  const width = cols < 8 ? 8 : cols
  const visual: string[] = []
  for (const ln of text.split('\n')) {
    if (ln.length <= width) visual.push(ln)
    else for (let i = 0; i < ln.length; i += width) visual.push(ln.slice(i, i + width))
  }
  return visual.slice(-maxLines).join('\n')
}


export function App({ transport, serverLabel }: Props): React.ReactElement {
  perfBumpRender() // perf diagnostics (CLAWCODEX_DEBUG_PERF=1): count renders
  const { exit } = useApp()
  const [entries, setEntries] = useState<TranscriptEntry[]>([])
  const [streaming, setStreaming] = useState('')
  const streamRef = useRef('') // source of truth for the live buffer (no stale closures)
  // Live-stream render cadence. The backend emits one stream_event per token
  // (very bursty — ~100 for one short reply). Rendering per delta means one
  // stdout write per token; on a slower terminal (Terminal.app) each write costs
  // ~50-100ms, which (a) floods the render pipeline so it backs up and drains for
  // seconds afterward — making the NEXT query's keystrokes lag — and (b) defeats a
  // simple time-throttle, because the slow writes themselves pace deltas past the
  // throttle window. So we DECOUPLE rendering from delta arrival: deltas only
  // append to streamRef + mark dirty; a fixed-cadence interval paints the latest
  // buffer at ~11fps regardless of how fast deltas arrive or how slow writes are.
  const streamFlushTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const streamDirty = useRef(false)
  const [thinkingStream, setThinkingStream] = useState('') // live reasoning deltas (§3)
  const thinkingRef = useRef('')
  const thinkingCommittedRef = useRef(false) // committed live reasoning this turn (dedup vs message thinking blocks)
  const [permissions, setPermissions] = useState<PendingPermission[]>([]) // FIFO queue
  // MCP elicitation form (a server requested user input, §6).
  const [elicit, setElicit] = useState<{ requestId: string; message: string; field: string; value: string } | null>(null)
  // MCP server multiselect (§6 MCPServerMultiselectDialog) — toggle servers on/off.
  const [mcpToggle, setMcpToggle] = useState<{ servers: { name: string; enabled: boolean; tools: string[] }[]; sel: number } | null>(null)
  // External CLAUDE.md imports confirm (§6 ClaudeMdExternalIncludesDialog).
  const [externalIncludes, setExternalIncludes] = useState<string[] | null>(null)
  // Message timestamps (§3) — opt-in via /timestamps.
  const [timestampsOn, setTimestampsOn] = useState<boolean>(false)
  // Mode-confirm dialog (§5): bypassPermissions always confirms (BypassPermissions-
  // ModeDialog); acceptEdits confirms once per session (AutoModeOptInDialog).
  const [pendingMode, setPendingMode] = useState<string | null>(null)
  const autoModeAckedRef = useRef(false)
  const alwaysAllowRef = useRef<Set<string>>(new Set()) // tools the user said "don't ask again"
  const bashAllowPrefixRef = useRef<Set<string>>(new Set()) // Bash command prefixes auto-allowed (granular)
  const pendingImageRef = useRef<{ data: string; media_type: string; name: string } | null>(null) // /image attachment
  const fastModeRef = useRef<string | null>(null) // /fast: prev model while fast mode is on
  const resumeSessionsRef = useRef<Record<string, unknown>[]>([]) // all sessions, for TagTabs filter
  const [resumeAll, setResumeAll] = useState(false) // TagTabs (§6): all-projects vs this-project
  const resumeFilterRef = useRef('') // LogSelector (§6): session search filter
  const [fastMode, setFastMode] = useState(false) // FastIcon (§7) — ⚡ in the footer
  const [effort, setEffort] = useState('') // EffortCallout (§7) — reasoning effort in the footer
  const [prBadge, setPrBadge] = useState('') // PrBadge (§7) — current branch's PR, via gh
  const [permFeedback, setPermFeedback] = useState<string | null>(null) // Tab-to-amend feedback field
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [turnStartedAt, setTurnStartedAt] = useState(0)
  const turnStartRef = useRef(0) // closure-safe turn-start time for the completion notification
  const focusedRef = useRef(true) // terminal focus (DECSET 1004) — for notify-when-unfocused
  const blurAtRef = useRef(0) // when the terminal lost focus — for the idle-return welcome-back
  const [model, setModel] = useState('?')
  const [mode, setMode] = useState('?')
  const [tools, setTools] = useState(0)
  const [connected, setConnected] = useState(false)
  const [ready, setReady] = useState(false) // system/init received — banner committed, submit allowed
  const [client, setClient] = useState<DirectConnectClient | null>(null)
  const [contextUsage, setContextUsage] = useState<{
    percentage: number
    totalTokens: number
    maxTokens: number
  } | null>(null)
  const [sessionCost, setSessionCost] = useState(0) // cumulative USD across turns
  const [statusCmd, setStatusCmd] = useState<string | null>(null) // /statusline shell command
  const [statusText, setStatusText] = useState('') // its latest rendered output
  const statusCmdRef = useRef<string | null>(null) // guards async output against a later clear
  const [, setThemeVersion] = useState(0) // bumped on /theme to repaint the dynamic UI
  const [scrollOffset, setScrollOffset] = useState(0) // fullscreen: entries hidden from the bottom
  const lastLenRef = useRef(0) // entries.length at last render — to anchor the scroll on new arrivals
  const [expanded, setExpanded] = useState(false) // Ctrl+O: expand collapsed tool results / thinking
  const [buddy, setBuddy] = useState<string | null>(() => {
    const b = process.env['CLAWCODEX_BUDDY']
    return b ? (BUDDY_SPECIES.includes(b) ? b : 'cat') : null
  }) // /buddy companion sprite (opt-in)
  const [txFind, setTxFind] = useState<string | null>(null) // fullscreen Ctrl+F find query (null = closed)
  const [vimMode, setVimMode] = useState(false) // /vim modal editing

  // Warm the @-mention / file index off the render path so the first @, /open, or
  // /files doesn't freeze input on a cold synchronous filesystem walk.
  useEffect(() => {
    void prewarmFileIndex(process.cwd())
  }, [])

  // Fullscreen uses the terminal's alternate screen so the bounded transcript
  // viewport renders in place (no scrollback spam). Enter on mount, restore on exit.
  useEffect(() => {
    if (!FULLSCREEN) return
    process.stdout.write('\x1b[?1049h')
    return () => {
      process.stdout.write('\x1b[?1049l')
    }
  }, [])

  // (Terminal-resize handling is owned by the cell-diff renderer — it reflows the
  // live region at the new width without the stacked-scrollback workaround standard
  // ink needed.)
  const [slashSel, setSlashSel] = useState(0)
  const [permSel, setPermSel] = useState(0) // permission dialog: highlighted option (↑/↓ + Enter)
  const [atSel, setAtSel] = useState(0)
  // Submitted-prompt history for ↑/↓ recall (readline-style; -1 = live draft).
  const historyRef = useRef<string[]>([])
  const [histIdx, setHistIdx] = useState(-1)
  const draftRef = useRef('')
  // Large pastes collapse to a `[Pasted text #N]` placeholder (token → real text).
  const pasteStore = useRef<Map<string, string>>(new Map())
  const pasteCounter = useRef(0)
  // Interactive select picker (the original's CustomSelect) for /mode, /theme.
  const [picker, setPicker] = useState<{
    kind:
      | 'mode'
      | 'theme'
      | 'model'
      | 'resume'
      | 'rewindpick'
      | 'historyrecall'
      | 'settings'
      | 'difffile'
      | 'outputstyle'
      | 'openfile'
      | 'logopalette'
    title: string
    options: string[]
    /** Optional value per option (e.g. session_id); falls back to the label. */
    values?: string[]
    sel: number
  } | null>(null)
  // Ctrl+R reverse history search.
  const [searchMode, setSearchMode] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchSel, setSearchSel] = useState(0)
  // Prompts typed while the agent is busy — queued, then sent at turn end.
  const queuedRef = useRef<string[]>([])
  const [queued, setQueued] = useState<string[]>([])
  const localSeq = useRef(0)
  const bannerAdded = useRef(false)
  const bannerDataRef = useRef<{ model: string; mode: string; tools: number; cwd?: string } | null>(
    null,
  ) // for /logo re-show
  const [toolActivity, setToolActivity] = useState<string | null>(null)
  const turnToolCounts = useRef<Record<string, number>>({})
  // Live, in-place tool-progress block: Read-like calls collapse here (not into
  // Static) until the round ends, then freeze into a committed summary.
  const [liveTools, setLiveTools] = useState<LiveGroup[]>([])
  const liveRef = useRef<LiveGroup[]>([])
  const collapsedIds = useRef<Set<string>>(new Set())
  // Live subagent progress lines, keyed by agent_id; cleared at turn end.
  const [agentLines, setAgentLines] = useState<AgentLine[]>([])

  const slashMatches = !input.includes(' ') ? matchSlash(input) : []
  const slashOpen = slashMatches.length > 0 && permissions.length === 0
  const sel = Math.min(slashSel, Math.max(0, slashMatches.length - 1))
  const permission = permissions[0] ?? null

  // Reset the permission dialog's highlighted option to "Yes" whenever a new
  // prompt becomes active (↑/↓ moves it, Enter selects).
  useEffect(() => {
    setPermSel(0)
  }, [permission?.requestId])

  // `@`-mention file autocomplete: an @token at the end of the input (after
  // start or whitespace) opens a file-suggestion dropdown (the original's
  // @-typeahead). Mutually exclusive with the slash menu.
  const atToken = (() => {
    if (slashOpen || input.startsWith('/')) return null
    const m = /(?:^|\s)@([^\s]*)$/.exec(input)
    return m ? (m[1] as string) : null
  })()
  const atMatches = atToken !== null ? searchFiles(process.cwd(), atToken, Date.now()) : []
  const atOpen = atMatches.length > 0 && permissions.length === 0
  const atSelClamped = Math.min(atSel, Math.max(0, atMatches.length - 1))

  // Ctrl+R reverse search: history entries containing the query, newest first.
  const searchMatches = searchMode
    ? historyRef.current
        .filter((h) => (searchQuery ? h.includes(searchQuery) : true))
        .reverse()
    : []
  const searchSelClamped = Math.min(searchSel, Math.max(0, searchMatches.length - 1))
  const searchMatch = searchMatches[searchSelClamped] ?? ''
  const completeAt = (pick: string): void => {
    setInput(input.replace(/@([^\s]*)$/, `@${pick} `))
    setAtSel(0)
  }

  /** Fullscreen Ctrl+F: scroll to a transcript match. `older`=true finds the
   *  next match above the current view; else the newest match. */
  const findJump = (query: string, older: boolean): void => {
    const q = query.trim().toLowerCase()
    if (!q) return
    const matches: number[] = []
    for (let i = 0; i < entries.length; i++) {
      if (entryText(entries[i] as TranscriptEntry).toLowerCase().includes(q)) matches.push(i)
    }
    if (!matches.length) return
    const curBottom = entries.length - 1 - scrollOffset
    let target = matches[matches.length - 1] as number // newest
    if (older) {
      const prev = [...matches].reverse().find((i) => i < curBottom)
      if (prev !== undefined) target = prev
    }
    setScrollOffset(Math.max(0, entries.length - 1 - target))
  }

  // Jump to the newest match as the find query changes (kept out of the key
  // handler to avoid a stale-closure read of txFind during fast typing).
  useEffect(() => {
    if (FULLSCREEN && txFind) findJump(txFind, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [txFind])

  /** Apply an interactive-picker selection (/mode, /theme, /model, /resume). */
  // Rewind the conversation by N prompt-turns (shared by /rewind <N> and the
  // /rewind restore-point picker — the original's MessageSelector).
  const requestRewind = (n: number): void => {
    if (!client || n < 1) return
    void client.requestControl('rewind', { turns: n }).then((r) => {
      if (r && r['ok']) {
        const removed = Number(r['removed']) || 0
        const count = Number(r['count']) || 0
        addEntry({
          kind: 'system',
          text: `↩ rewound — dropped ${removed} message${removed === 1 ? '' : 's'} (${count} remaining)`,
        })
        void client.requestControl('get_context_usage').then(applyContextUsage)
      } else {
        addEntry({ kind: 'error', text: `rewind failed: ${r && r['error'] ? String(r['error']) : 'no response'}` })
      }
    })
  }

  const applyPick = (
    kind:
      | 'mode'
      | 'theme'
      | 'model'
      | 'resume'
      | 'rewindpick'
      | 'historyrecall'
      | 'settings'
      | 'difffile'
      | 'outputstyle'
      | 'openfile'
      | 'logopalette',
    value: string,
  ): void => {
    if (!value) return
    if (kind === 'logopalette') {
      setLogoPalette(value)
      // Re-show the banner so the new gradient is visible immediately.
      addEntry({
        kind: 'banner',
        text: '',
        bannerData: bannerDataRef.current ?? { model, mode, tools: 0, cwd: process.cwd() },
      })
      addEntry({ kind: 'system', text: `logo palette → ${value}` })
      return
    }
    if (kind === 'openfile') {
      // Insert the chosen file as an @-mention (the original's QuickOpen).
      setInput((prev) => (prev ? `${prev}@${value} ` : `@${value} `))
      return
    }
    if (kind === 'outputstyle') {
      void client?.requestControl('set_output_style', { style: value }).then((r) => {
        if (r && r['ok']) {
          addEntry({ kind: 'system', text: `output style → ${value}` })
        } else {
          addEntry({ kind: 'error', text: `output style failed: ${r && r['error'] ? String(r['error']) : 'no response'}` })
        }
      })
      return
    }
    if (kind === 'rewindpick') {
      requestRewind(Number(value) || 1)
      return
    }
    if (kind === 'difffile') {
      runBang(`git --no-pager diff -- '${value.replace(/'/g, "'\\''")}'`)
      return
    }
    if (kind === 'historyrecall') {
      setInput(value)
      setHistIdx(-1)
      return
    }
    if (kind === 'settings') {
      runSlash(`/${value}`) // route to the chosen setter (/model, /mode, /theme, …)
      return
    }
    if (kind === 'mode') {
      setPermMode(value) // confirms bypassPermissions / first-time acceptEdits
    } else if (kind === 'model') {
      client?.sendControl('set_model', { model: value })
      setModel(value)
      addEntry({ kind: 'system', text: `model → ${value}` })
    } else if (kind === 'resume') {
      void client?.requestControl('resume', { session_id: value }).then((r) => {
        if (r && r['ok']) {
          addEntry({ kind: 'system', text: `↻ resumed session — ${Number(r['count']) || 0} messages restored` })
          void client?.requestControl('get_context_usage').then(applyContextUsage)
        } else {
          addEntry({ kind: 'error', text: `resume failed: ${r && r['error'] ? String(r['error']) : 'no response'}` })
        }
      })
    } else if (applyTheme(value)) {
      setThemeVersion((v) => v + 1)
      addEntry({ kind: 'system', text: `theme → ${value}` })
    }
  }

  const addEntry = (e: Omit<TranscriptEntry, 'id'>) =>
    setEntries((prev) => [...prev, { ts: Date.now(), ...e, id: `l${localSeq.current++}` }])

  // Set the permission mode, gating the risky ones behind a confirm:
  // bypassPermissions always (§5 BypassPermissionsModeDialog), acceptEdits once
  // per session (§5 AutoModeOptInDialog).
  const setPermMode = (mode: string): void => {
    if (mode === 'bypassPermissions' || (mode === 'acceptEdits' && !autoModeAckedRef.current)) {
      setPendingMode(mode)
      return
    }
    client?.sendControl('set_permission_mode', { mode })
    setMode(mode)
    addEntry({ kind: 'system', text: `mode → ${mode}` })
  }

  // TagTabs (§6) + LogSelector search (§6): (re)build the resume picker filtered to
  // all / this project and matching the search filter.
  const openResumePicker = (all: boolean): void => {
    const cwd = process.cwd()
    const q = resumeFilterRef.current.toLowerCase()
    const list = resumeSessionsRef.current
      .filter((s) => all || String(s['cwd'] || '') === cwd)
      .filter((s) => !q || String(s['name'] || s['preview'] || '').toLowerCase().includes(q))
    const options = list.map((s) => {
      const prev = String(s['name'] || s['preview'] || '(no preview)').slice(0, 50)
      const age = relAge(Number(s['updated_at']) || 0)
      return `${prev}  ·  ${Number(s['message_count']) || 0} msgs${age ? `  ·  ${age}` : ''}`
    })
    const values = list.map((s) => String(s['session_id']))
    const f = resumeFilterRef.current
    setPicker({
      kind: 'resume',
      title: `Resume — ${all ? 'all projects' : 'this project'}${f ? ` · "${f}"` : ''} (Tab toggles, type to search)`,
      options,
      values,
      sel: 0,
    })
  }

  // Stop the live-stream flush interval and clear pending-paint flags (turn end,
  // commit, disconnect, or unmount).
  const stopStreamFlush = () => {
    if (streamFlushTimer.current) {
      clearInterval(streamFlushTimer.current)
      streamFlushTimer.current = null
    }
    streamDirty.current = false
  }
  // Start the fixed-cadence flush if not already running. ~90ms (~11fps) keeps the
  // streamed ANSWER readable while bounding renders/writes so the pipeline can't
  // back up. (Reasoning deltas don't go through here — the live thinking view is a
  // static indicator, so they never render per token.)
  const startStreamFlush = () => {
    if (streamFlushTimer.current) return
    const t = setInterval(() => {
      if (streamDirty.current) {
        streamDirty.current = false
        setStreaming(streamRef.current)
      }
    }, 90)
    t.unref?.() // never keep the process alive on our account
    streamFlushTimer.current = t
  }
  const setStream = (s: string) => {
    // An explicit set (incl. the turn-end commit to '') stops the flush loop and
    // paints immediately, so a stale buffer can't repaint after the commit.
    stopStreamFlush()
    streamRef.current = s
    setStreaming(s)
  }
  const appendStream = (delta: string) => {
    // Text started → the model finished thinking; commit the reasoning as a
    // collapsed entry (preserved + expandable) before the answer paints.
    if (thinkingRef.current) commitThinking()
    streamRef.current += delta
    // First token of a stream paints immediately (snappy); the rest are coalesced
    // by the interval — decoupled from delta arrival and slow terminal writes.
    if (streamFlushTimer.current) {
      streamDirty.current = true
    } else {
      setStreaming(streamRef.current)
      startStreamFlush()
    }
  }
  const appendThinkingStream = (delta: string) => {
    // The live view is a static "∴ Thinking…" indicator (not the reasoning text),
    // so flip it on ONCE and just accumulate the buffer for the collapsed commit.
    // No per-delta render → reasoning streams can't lag input (the /thinking freeze).
    const first = thinkingRef.current === ''
    thinkingRef.current += delta
    if (first) setThinkingStream('…')
  }
  /** Commit the live reasoning buffer as a COLLAPSED 'thinking' entry, then clear.
   *  Preserves the reasoning so it can be expanded with ctrl+o (matches the
   *  original, and covers providers like deepseek that stream reasoning live but
   *  omit it from the final message). Sets a per-turn flag used defensively to
   *  skip a duplicate should a future backend ever serialize thinking into the
   *  message envelope (this one strips it — see the messageToEntries guard). */
  const commitThinking = () => {
    const t = thinkingRef.current
    if (t.trim()) {
      addEntry({ kind: 'thinking', text: t })
      thinkingCommittedRef.current = true
    }
    thinkingRef.current = ''
    setThinkingStream('')
  }
  /** Commit any leftover live buffer as a finished assistant entry, then clear. */
  const flushStream = () => {
    const s = streamRef.current
    if (s.trim()) addEntry({ kind: 'assistant', text: s })
    setStream('')
  }

  const syncLive = () => setLiveTools([...liveRef.current])
  const addLive = (name: string, args: string) => {
    const g = liveRef.current.find((x) => x.name === name)
    if (g) {
      g.count += 1
      g.current = args
    } else {
      liveRef.current.push({ name, count: 1, current: args })
    }
    syncLive()
  }
  /** Freeze the live read-groups into committed collapsed-summary entries. */
  const takeLive = (): TranscriptEntry[] => {
    const groups = liveRef.current
    if (!groups.length) return []
    liveRef.current = []
    collapsedIds.current.clear()
    syncLive()
    return groups.map((g) => ({
      id: `l${localSeq.current++}`,
      kind: 'tool' as const,
      text: '',
      toolName: g.name,
      argsText: g.current,
      count: g.count,
    }))
  }

  /** Apply a get_context_usage control-response payload to the status bar. */
  const applyContextUsage = (r: Record<string, unknown> | null): void => {
    if (r && typeof r['percentage'] === 'number') {
      setContextUsage({
        percentage: r['percentage'] as number,
        totalTokens: Number(r['total_tokens']) || 0,
        maxTokens: Number(r['max_tokens']) || 0,
      })
    }
  }

  useEffect(() => {
    const c = new DirectConnectClient(transport, {
      onConnected: () => setConnected(true),
      onDisconnected: () => {
        setConnected(false)
        setReady(false) // gate the input so submits don't vanish into a dead link
        setBusy(false)
        flushStream() // commit any partial + stop the flush loop (no result is coming)
        addEntry({ kind: 'system', text: 'backend disconnected' })
      },
      onError: (err) => {
        stopStreamFlush() // a turn-ending error may arrive without a result
        addEntry({ kind: 'error', text: String(err.message) })
      },
      onPermissionRequest: (req, requestId) =>
        setPermissions((q) => [
          ...q,
          {
            requestId,
            toolName: String((req as { tool_name?: string }).tool_name ?? 'tool'),
            input: (req as { input?: Record<string, unknown> }).input ?? {},
          },
        ]),
      onElicitation: (params, requestId) => {
        const message = String((params as { message?: unknown }).message ?? 'The MCP server requests input')
        const schema = (params as { requestedSchema?: { properties?: Record<string, unknown> } }).requestedSchema
        const field = schema?.properties ? Object.keys(schema.properties)[0] || 'value' : 'value'
        setElicit({ requestId, message, field, value: '' })
      },
      onMessage: (msg) => {
        perfNote(`msg:${(msg as { type?: string }).type ?? '?'}`)
        const think = streamThinkingDelta(msg)
        if (think !== null) {
          appendThinkingStream(think)
          return
        }
        const delta = streamDeltaText(msg)
        if (delta !== null) {
          appendStream(delta)
          return
        }
        const type = (msg as { type?: string }).type
        if (type === 'agent_progress') {
          // Live subagent progress (the original's AgentProgressLine). Upsert
          // by agent_id; lines clear at turn end.
          const m = msg as {
            agent_id?: string
            name?: string
            description?: string
            activity?: string
            tool_use_count?: number
            tokens?: number
          }
          if (m.agent_id) {
            const id = m.agent_id
            setAgentLines((prev) => [
              ...prev.filter((l) => l.agentId !== id),
              {
                agentId: id,
                name: m.name ?? '',
                description: m.description ?? '',
                activity: m.activity ?? '',
                toolUseCount: Number(m.tool_use_count) || 0,
                tokens: Number(m.tokens) || 0,
              },
            ])
          }
          return
        }
        if (type === 'assistant') {
          // Land any pending reasoning BEFORE this message's tool/text entries, so
          // in multi-step turns (think → tool → … → text) the thinking renders in
          // order and successive thinking phases stay separate (not merged). No-op
          // when text already triggered the commit. Also clears the live indicator.
          if (thinkingRef.current) commitThinking()
          setStream('') // final assistant replaces the live stream
        }
        if (type === 'result') {
          setBusy(false)
          setToolActivity(null)
          commitThinking() // preserve any leftover reasoning (thinking-only turn) as a collapsed entry
          setAgentLines([]) // subagents are done when the turn ends
          // Completion notification (the original's terminal notifications, §8):
          // ring the bell + OSC 9 desktop notice for long turns the user may have
          // stepped away from. Quick turns stay silent (threshold 10s).
          if (turnStartRef.current && (!focusedRef.current || Date.now() - turnStartRef.current > 10_000)) {
            try {
              process.stdout.write('\x07') // bell — flags the tab/taskbar
              process.stdout.write('\x1b]9;clawcodex — response ready\x07') // OSC 9 (iTerm/Ghostty/kitty)
            } catch {
              /* non-tty — ignore */
            }
          }
          turnStartRef.current = 0
          const turnCost = Number((msg as { total_cost_usd?: number }).total_cost_usd) || 0
          if (turnCost > 0) setSessionCost((c) => c + turnCost)
          flushStream() // commit a partial left over by interrupt/error (no-op on success)
          void c.requestControl('get_context_usage').then(applyContextUsage) // refresh after each turn
        }
        if (type === 'system' && (msg as { subtype?: string }).subtype === 'init') {
          const m = msg as {
            model?: string
            permission_mode?: string
            protocol_version?: string
            tools?: unknown[]
            cwd?: string
          }
          const toolCount = Array.isArray(m.tools) ? m.tools.length : 0
          setModel(m.model ?? '?')
          setMode(m.permission_mode ?? '?')
          setTools(toolCount)
          // Commit the welcome banner as the FIRST Static entry so it stays in
          // scrollback as the conversation grows (the original keeps its logo).
          // It must be APPENDED before any other entry — <Static> is append-only
          // and tracks by index, so prepending would skip the banner and
          // duplicate the next row. Submit is gated on `ready` (set here) so a
          // user message can never beat the banner into the list.
          setReady(true)
          void c.requestControl('get_context_usage').then(applyContextUsage) // seed the status bar
          if (!bannerAdded.current) {
            bannerAdded.current = true
            bannerDataRef.current = {
              model: m.model ?? '?',
              mode: m.permission_mode ?? '?',
              tools: toolCount,
              cwd: m.cwd,
            }
            addEntry({ kind: 'banner', text: '', bannerData: bannerDataRef.current })
          }
          const major = parseProtocolMajor(m.protocol_version)
          if (major !== null && major !== SUPPORTED_PROTOCOL_MAJOR) {
            addEntry({
              kind: 'error',
              text: `protocol major mismatch: server v${m.protocol_version}, client supports v${SUPPORTED_PROTOCOL_MAJOR}.x`,
            })
          }
        }
        const newEntries = messageToEntries(msg)
        if (newEntries.length) {
          const toCommit: TranscriptEntry[] = []
          for (const e of newEntries) {
            // Defensive (currently inert): this backend strips reasoning from the
            // message envelope, so messageToEntries never yields a 'thinking' entry.
            // If a future backend serializes thinking-into-content, this skips the
            // duplicate, since we already committed the live reasoning collapsed.
            if (e.kind === 'thinking' && thinkingCommittedRef.current) continue
            if (e.kind === 'tool') {
              const verb = (e.toolName && TOOL_VERB[e.toolName]?.verb) || e.toolName || 'tool'
              const n = (turnToolCounts.current[verb] = (turnToolCounts.current[verb] ?? 0) + 1)
              setToolActivity(toolActivityLabel(e.toolName, e.argsText, n))
            }
            if (e.kind === 'tool' && READ_LIKE.has(e.toolName ?? '')) {
              // Collapse into the live block (not Static); drop its result later.
              if (e.toolUseId) collapsedIds.current.add(e.toolUseId)
              addLive(e.toolName ?? 'tool', e.argsText ?? '')
            } else if (
              e.kind === 'toolResult' &&
              (e.forToolUseIds?.length ?? 0) > 0 &&
              (e.forToolUseIds ?? []).every((id) => collapsedIds.current.has(id))
            ) {
              // Result for collapsed reads → drop (kept collapsed, like the original).
            } else {
              // TodoWrite's "Todos modified" result is noise — the checklist IS
              // the output; collapse its result like a read.
              if (e.kind === 'tool' && e.toolName === 'TodoWrite' && e.toolUseId) {
                collapsedIds.current.add(e.toolUseId)
              }
              // Preserve order: freeze the live read-group before this entry.
              toCommit.push(...takeLive(), e)
            }
          }
          if (toCommit.length) {
            const stamped = toCommit.map((e) => (e.ts ? e : { ...e, ts: Date.now() }))
            setEntries((prev) => [...prev, ...stamped])
          }
        }
      },
    })
    setClient(c)
    c.connect().catch(() => {}) // failures surface via onError / onDisconnected
    return () => {
      c.close()
      stopStreamFlush() // don't leak the live-stream flush interval
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transport])

  useInput((ch, key) => {
    perfNote(`key:${key.backspace ? 'backspace' : key.delete ? 'delete' : key.return ? 'return' : ch ? JSON.stringify(ch).slice(0, 8) : 'special'}`)
    // Terminal focus events (DECSET 1004) arrive as "[I" / "[O" — track + swallow.
    if (ch === '[I') {
      focusedRef.current = true
      // Idle-return (the original's IdleReturnDialog, §9): returning after a long
      // absence surfaces a one-line welcome-back with a fresh-start hint.
      if (blurAtRef.current && Date.now() - blurAtRef.current > 600_000 && ready && !busy && permissions.length === 0) {
        addEntry({ kind: 'system', text: '↩ welcome back — continuing this session (/clear to start fresh)' })
      }
      blurAtRef.current = 0
      return
    }
    if (ch === '[O') {
      focusedRef.current = false
      blurAtRef.current = Date.now()
      return
    }
    // External CLAUDE.md imports (§6): y allows, anything else disables them.
    if (externalIncludes) {
      const approved = ch === 'y' || ch === 'Y'
      client?.requestControl('set_external_includes', { approved })
      addEntry({
        kind: 'system',
        text: approved ? 'external CLAUDE.md imports allowed' : 'external CLAUDE.md imports disabled',
      })
      setExternalIncludes(null)
      return
    }
    // MCP server multiselect (§6): ↑/↓ navigate, space toggles + persists, esc closes.
    if (mcpToggle) {
      if (key.escape || key.return) {
        setMcpToggle(null)
        return
      }
      if (key.upArrow) {
        setMcpToggle((p) => (p ? { ...p, sel: (p.sel - 1 + p.servers.length) % p.servers.length } : p))
        return
      }
      if (key.downArrow) {
        setMcpToggle((p) => (p ? { ...p, sel: (p.sel + 1) % p.servers.length } : p))
        return
      }
      if (ch === ' ') {
        const srv = mcpToggle.servers[mcpToggle.sel]
        if (srv) {
          const next = !srv.enabled
          client?.requestControl('set_mcp_enabled', { server: srv.name, enabled: next })
          setMcpToggle((p) =>
            p ? { ...p, servers: p.servers.map((s, i) => (i === p.sel ? { ...s, enabled: next } : s)) } : p,
          )
        }
        return
      }
      return // multiselect swallows other keys
    }
    // Mode confirm (§5): y enables the pending mode, anything else cancels.
    if (pendingMode) {
      if (ch === 'y' || ch === 'Y') {
        if (pendingMode === 'acceptEdits') autoModeAckedRef.current = true
        client?.sendControl('set_permission_mode', { mode: pendingMode })
        setMode(pendingMode)
        addEntry({
          kind: 'system',
          text:
            pendingMode === 'bypassPermissions'
              ? 'mode → bypassPermissions (all prompts disabled)'
              : 'mode → acceptEdits (edits auto-accepted)',
        })
      } else {
        addEntry({ kind: 'system', text: `${pendingMode} cancelled` })
      }
      setPendingMode(null)
      return
    }
    // MCP elicitation form: capture input → respond accept/decline (§6).
    if (elicit) {
      if (key.escape) {
        client?.respondControl(elicit.requestId, { action: 'decline' })
        setElicit(null)
        return
      }
      if (key.return) {
        client?.respondControl(elicit.requestId, {
          action: 'accept',
          content: { [elicit.field]: elicit.value },
        })
        setElicit(null)
        return
      }
      if (key.backspace || key.delete) {
        setElicit((e) => (e ? { ...e, value: e.value.slice(0, -1) } : e))
        return
      }
      if (ch && !key.ctrl && !key.meta && ch.length === 1 && ch >= ' ') {
        setElicit((e) => (e ? { ...e, value: e.value + ch } : e))
      }
      return // elicitation swallows other keys
    }
    if (key.ctrl && ch === 'c') {
      client?.close()
      exit()
      return
    }
    // Ctrl+O: toggle expand of collapsed tool results / thinking (fully re-renders
    // in fullscreen; affects subsequent entries in inline <Static> mode).
    if (matchesBinding('expand', ch, key)) {
      setExpanded((e) => !e)
      return
    }
    // Ctrl+L: redraw. In fullscreen, clear the alt-screen and force a repaint;
    // in inline mode a repaint is harmless (Static stays in scrollback).
    if (matchesBinding('redraw', ch, key)) {
      if (FULLSCREEN) process.stdout.write('\x1b[2J\x1b[3J\x1b[H')
      setThemeVersion((v) => v + 1) // force a re-render
      return
    }
    // Ctrl+G: edit the current prompt in $EDITOR (the original's external editor).
    if (matchesBinding('external-editor', ch, key)) {
      const sin = process.stdin as unknown as { setRawMode?: (m: boolean) => void }
      const canRaw = typeof sin.setRawMode === 'function'
      try {
        if (canRaw) sin.setRawMode?.(false)
        setInput(editInEditor(input))
      } catch (e) {
        addEntry({ kind: 'error', text: `editor failed: ${(e as Error).message}` })
      } finally {
        if (canRaw) sin.setRawMode?.(true)
        setThemeVersion((v) => v + 1) // repaint after the editor released the screen
      }
      return
    }
    // Shift+Tab: cycle permission mode (the original's mode cycle, §5/§8).
    if (key.tab && key.shift) {
      const modes = ['default', 'acceptEdits', 'plan', 'bypassPermissions']
      const next = modes[(modes.indexOf(mode) + 1) % modes.length] as string
      setPermMode(next) // confirms bypassPermissions / first-time acceptEdits
      return
    }
    // Fullscreen Ctrl+F transcript find.
    if (FULLSCREEN && txFind !== null) {
      if (key.escape || key.return) {
        setTxFind(null)
        return
      }
      if (key.ctrl && ch === 'f') {
        findJump(txFind, true) // next older match
        return
      }
      if (key.backspace || key.delete) {
        setTxFind((q) => (q ?? '').slice(0, -1)) // jump driven by the effect on txFind
        return
      }
      if (ch && !key.ctrl && !key.meta && ch.length === 1 && ch >= ' ') {
        setTxFind((q) => (q ?? '') + ch)
      }
      return // find mode swallows other keys
    }
    if (FULLSCREEN && matchesBinding('transcript-find', ch, key)) {
      setTxFind('')
      return
    }
    // Interactive picker (/mode, /theme): arrow-navigate, Enter picks, Esc cancels.
    if (picker) {
      if (picker.kind === 'resume' && key.tab) {
        // TagTabs (§6): toggle all-projects / this-project filter.
        const next = !resumeAll
        setResumeAll(next)
        openResumePicker(next)
        return
      }
      if (picker.kind === 'resume' && (key.backspace || key.delete)) {
        resumeFilterRef.current = resumeFilterRef.current.slice(0, -1) // LogSelector search
        openResumePicker(resumeAll)
        return
      }
      if (picker.kind === 'resume' && ch && !key.ctrl && !key.meta && ch.length === 1 && ch >= ' ') {
        resumeFilterRef.current += ch // LogSelector search: type to filter sessions
        openResumePicker(resumeAll)
        return
      }
      if (key.upArrow) {
        setPicker((p) => (p ? { ...p, sel: (p.sel - 1 + p.options.length) % p.options.length } : p))
        return
      }
      if (key.downArrow) {
        setPicker((p) => (p ? { ...p, sel: (p.sel + 1) % p.options.length } : p))
        return
      }
      if (key.return) {
        const picked = (picker.values ?? picker.options)[picker.sel] ?? ''
        setPicker(null) // close first so applyPick may open a nested picker (/settings)
        applyPick(picker.kind, picked)
        return
      }
      if (key.escape) {
        setPicker(null)
        return
      }
      return // picker swallows all other keys
    }
    // Ctrl+R: open reverse history search, or cycle to the next older match.
    if (matchesBinding('history-search', ch, key)) {
      if (!searchMode) {
        setSearchMode(true)
        setSearchQuery('')
        setSearchSel(0)
      } else {
        setSearchSel((s) => (searchMatches.length ? (s + 1) % searchMatches.length : 0))
      }
      return
    }
    if (searchMode) {
      if (key.escape) {
        setSearchMode(false)
        return
      }
      if (key.return) {
        if (searchMatch) setInput(searchMatch)
        setSearchMode(false)
        setHistIdx(-1)
        return
      }
      if (key.backspace || key.delete) {
        setSearchQuery((q) => q.slice(0, -1))
        setSearchSel(0)
        return
      }
      if (ch && !key.ctrl && !key.meta && ch.length === 1 && ch >= ' ') {
        setSearchQuery((q) => q + ch)
        setSearchSel(0)
      }
      return // search mode swallows all other keys
    }
    // Fullscreen transcript scroll (PgUp older / PgDn newer).
    if (FULLSCREEN && (key.pageUp || key.pageDown)) {
      if (key.pageUp) setScrollOffset((o) => Math.min(Math.max(0, entries.length - 1), o + 5))
      else setScrollOffset((o) => Math.max(0, o - 5))
      return
    }
    // Ctrl+E: jump to the oldest message (show-previous, the original's §8); a
    // second press (already at top) jumps back to the bottom.
    if (FULLSCREEN && matchesBinding('jump-oldest', ch, key)) {
      setScrollOffset((o) => (o >= Math.max(0, entries.length - 1) ? 0 : Math.max(0, entries.length - 1)))
      return
    }
    const head = permissions[0]
    if (head) {
      // Tab-to-amend feedback field: type why/what-to-do, Enter denies with it.
      if (permFeedback !== null) {
        if (key.escape) {
          setPermFeedback(null)
        } else if (key.return) {
          client?.respondPermission(head.requestId, 'deny', { message: permFeedback || 'denied by user' })
          setPermissions((q) => q.slice(1))
          setPermFeedback(null)
        } else if (key.backspace || key.delete) {
          setPermFeedback((f) => (f ?? '').slice(0, -1))
        } else if (ch && !key.ctrl && !key.meta && ch.length === 1 && ch >= ' ') {
          setPermFeedback((f) => (f ?? '') + ch)
        }
        return
      }
      const c = ch.toLowerCase()
      // The three options, in display order — shared by the number/letter
      // hotkeys AND ↑/↓+Enter navigation so both stay in sync.
      const applyChoice = (choice: 'allow' | 'always' | 'deny'): void => {
        if (choice === 'always') {
          // For Bash, remember the command's first word (granular: allow `git`,
          // still prompt `rm`); for other tools, the whole tool.
          if (head.toolName === 'Bash') {
            const pfx = String((head.input as { command?: string }).command ?? '').trim().split(/\s+/)[0] || ''
            if (pfx) bashAllowPrefixRef.current.add(pfx)
            addEntry({ kind: 'system', text: `always allowing \`${pfx}\` commands this session` })
          } else {
            alwaysAllowRef.current.add(head.toolName)
            addEntry({ kind: 'system', text: `always allowing ${head.toolName} this session` })
          }
          client?.respondPermission(head.requestId, 'allow')
        } else if (choice === 'allow') {
          client?.respondPermission(head.requestId, 'allow')
        } else {
          client?.respondPermission(head.requestId, 'deny', { message: 'denied by user' })
        }
        setPermissions((q) => q.slice(1))
      }
      const CHOICES = ['allow', 'always', 'deny'] as const
      if (key.upArrow) {
        setPermSel((s) => (s + CHOICES.length - 1) % CHOICES.length)
        return
      }
      if (key.downArrow) {
        setPermSel((s) => (s + 1) % CHOICES.length)
        return
      }
      if (key.return) {
        applyChoice(CHOICES[permSel] ?? 'allow')
        return
      }
      if (key.tab) {
        setPermFeedback('') // open the feedback field (the original's Tab-to-amend)
        return
      }
      if (c === 'y' || ch === '1') {
        applyChoice('allow')
      } else if (c === 'a' || ch === '2') {
        applyChoice('always')
      } else if (c === 'n' || c === 'd' || ch === '3') {
        applyChoice('deny')
      } else if (key.escape) {
        // esc at a permission prompt: interrupt — the server denies every
        // pending ask AND aborts the turn (agent_server §7).
        client?.interrupt()
        setPermissions([])
      }
      return
    }
    if (slashOpen) {
      if (key.upArrow) {
        setSlashSel((s) => (s - 1 + slashMatches.length) % slashMatches.length)
        return
      }
      if (key.downArrow) {
        setSlashSel((s) => (s + 1) % slashMatches.length)
        return
      }
      if (key.tab) {
        const pick = slashMatches[sel]
        if (pick) setInput(`${pick.name} `)
        return
      }
    }
    if (atOpen) {
      if (key.upArrow) {
        setAtSel((s) => (s - 1 + atMatches.length) % atMatches.length)
        return
      }
      if (key.downArrow) {
        setAtSel((s) => (s + 1) % atMatches.length)
        return
      }
      if (key.tab) {
        const pick = atMatches[atSelClamped]
        if (pick) completeAt(pick)
        return
      }
    }
    // Input history recall with ↑/↓ when no menu is open (vim owns its own keys).
    if (!slashOpen && !atOpen && !vimMode) {
      const h = historyRef.current
      if (key.upArrow && h.length > 0) {
        if (histIdx === -1) draftRef.current = input
        const ni = Math.min(histIdx + 1, h.length - 1)
        setHistIdx(ni)
        setInput(h[h.length - 1 - ni] ?? '')
        return
      }
      if (key.downArrow && histIdx !== -1) {
        const ni = histIdx - 1
        setHistIdx(ni)
        setInput(ni === -1 ? draftRef.current : (h[h.length - 1 - ni] ?? ''))
        return
      }
    }
    if (key.escape && busy) {
      client?.interrupt()
    }
  })

  const runSlash = (raw: string): boolean => {
    const cmd = resolveSlash(raw)
    if (!cmd) return false
    const arg = raw.trim().slice(cmd.name.length).trim()
    setInput('')
    setSlashSel(0)
    switch (cmd.kind) {
      case 'clear':
        // Static output is flushed to scrollback; reclaim the screen too.
        process.stdout.write('\x1b[2J\x1b[3J\x1b[H')
        setEntries([])
        setSessionCost(0)
        setContextUsage(null)
        client?.sendControl('clear') // reset the backend conversation, not just the screen
        return true
      case 'help':
        addEntry({ kind: 'system', text: HELP })
        return true
      case 'quit':
        client?.close()
        exit()
        return true
      case 'vim': {
        const next = !vimMode
        setVimMode(next)
        addEntry({ kind: 'system', text: `vim mode ${next ? 'on' : 'off'}` })
        return true
      }
      case 'rename': {
        if (!arg) {
          addEntry({ kind: 'system', text: 'usage: /rename <name>' })
        } else if (client) {
          void client.requestControl('rename', { name: arg }).then((r) => {
            addEntry({
              kind: r && r['ok'] ? 'system' : 'error',
              text: r && r['ok'] ? `session renamed → ${arg}` : 'rename failed',
            })
          })
        }
        return true
      }
      case 'memory': {
        const files = [
          { label: 'project', path: join(process.cwd(), 'CLAUDE.md') },
          { label: 'global', path: join(homedir(), '.claude', 'CLAUDE.md') },
        ]
        const lines = files.map((f) => {
          try {
            const n = readFileSync(f.path, 'utf8').split('\n').length
            return `${f.label}: ${f.path} (${n} lines)`
          } catch {
            return `${f.label}: ${f.path} (not found)`
          }
        })
        addEntry({ kind: 'system', text: `Memory files:\n${lines.join('\n')}` })
        return true
      }
      case 'prompt': {
        if (cmd.promptText) {
          if (client && ready && !busy && permissions.length === 0) {
            dispatchPrompt(cmd.promptText)
          } else {
            addEntry({ kind: 'system', text: `cannot run ${cmd.name} now (agent busy or not ready)` })
          }
        }
        return true
      }
      case 'init': {
        if (client && ready && !busy && permissions.length === 0) {
          dispatchPrompt(
            'Analyze this codebase and create a CLAUDE.md file capturing: the build, lint, and ' +
              'test commands; the high-level architecture; and any conventions a new contributor ' +
              'should know. If a CLAUDE.md already exists, improve it rather than duplicating it.',
          )
        } else {
          addEntry({ kind: 'system', text: 'cannot /init right now (agent busy or not ready)' })
        }
        return true
      }
      case 'cost': {
        const cost = `$${(sessionCost || 0).toFixed(4)}`
        const cu = contextUsage
        const ctxPart = cu
          ? ` · ${Math.round(cu.percentage)}% context (${cu.totalTokens.toLocaleString()}/${cu.maxTokens.toLocaleString()} tokens)`
          : ''
        const prompts = entries.filter((e) => e.kind === 'user').length
        addEntry({
          kind: 'system',
          text: `Session: ${prompts} prompt${prompts === 1 ? '' : 's'} · ${cost}${ctxPart}`,
        })
        return true
      }
      case 'timestamps': {
        setTimestampsOn((on) => {
          const next = !on
          addEntry({ kind: 'system', text: `message timestamps ${next ? 'on' : 'off'}` })
          return next
        })
        return true
      }
      case 'thinking': {
        const a = arg.trim().toLowerCase()
        const action = a === 'on' || a === 'off' ? a : 'toggle'
        if (client) {
          void client.requestControl('set_thinking', { action }).then((r) => {
            const on = !!(r && r['thinking'])
            addEntry({ kind: 'system', text: `extended thinking ${on ? 'on' : 'off'}` })
          })
        }
        return true
      }
      case 'debugToolCall': {
        // Inspect the last tool call (the original's debug-tool-call): raw input +
        // matching result, for debugging tool behavior.
        const tools = entries.filter((e) => e.kind === 'tool' && e.toolName)
        const last = tools[tools.length - 1]
        if (!last) {
          addEntry({ kind: 'system', text: 'no tool calls in this session yet' })
          return true
        }
        const res = entries.find((e) => e.kind === 'toolResult' && (e.forToolUseIds ?? []).includes(last.toolUseId ?? ''))
        const input = JSON.stringify(last.input ?? {}, null, 2)
        const result = res?.text ? res.text.slice(0, 600) : '(no result captured)'
        addEntry({ kind: 'system', text: `debug ${last.toolName}\ninput:\n${input}\nresult:\n${result}` })
        return true
      }
      case 'env': {
        // Curated runtime environment (the original's `env`, adapted — no secret
        // env vars, only non-sensitive runtime facts).
        const e = process.env
        const lines = [
          `platform   ${process.platform} (${process.arch})`,
          `node       ${process.version}`,
          `terminal   ${e['TERM_PROGRAM'] || e['TERM'] || 'unknown'}`,
          `shell      ${e['SHELL'] || 'unknown'}`,
          `locale     ${e['LANG'] || e['LC_ALL'] || 'unknown'}`,
          `cwd        ${process.cwd()}`,
        ]
        addEntry({ kind: 'system', text: `environment:\n  ${lines.join('\n  ')}` })
        return true
      }
      case 'diagnostics': {
        // DiagnosticsDisplay (§3), adapted to no-LSP: run the project's typecheck/
        // lint and show issues. Auto-detects the checker.
        const cwd = process.cwd()
        let cmd = ''
        try {
          if (existsSync(join(cwd, 'package.json'))) {
            const pkg = JSON.parse(readFileSync(join(cwd, 'package.json'), 'utf8')) as { scripts?: Record<string, string> }
            if (pkg.scripts?.['typecheck']) cmd = 'npm run typecheck'
            else if (existsSync(join(cwd, 'tsconfig.json'))) cmd = 'npx tsc --noEmit'
          } else if (existsSync(join(cwd, 'tsconfig.json'))) {
            cmd = 'npx tsc --noEmit'
          } else if (existsSync(join(cwd, 'pyproject.toml')) || existsSync(join(cwd, 'ruff.toml'))) {
            cmd = 'ruff check .'
          }
        } catch {
          /* detection best-effort */
        }
        if (!cmd) {
          addEntry({ kind: 'system', text: 'no typecheck/lint detected (need a package.json "typecheck" script, tsconfig.json, or ruff)' })
          return true
        }
        addEntry({ kind: 'system', text: `running diagnostics: ${cmd}…` })
        exec(cmd, { cwd, timeout: 120_000, maxBuffer: 1024 * 1024 }, (err, stdout, stderr) => {
          const out = `${stdout || ''}${stderr || ''}`.trim()
          addEntry({ kind: err ? 'error' : 'system', text: out || (err ? 'diagnostics failed' : 'no issues found ✓') })
        })
        return true
      }
      case 'prComments': {
        // Show the current branch's PR + comments via gh (the original's pr_comments).
        exec('gh pr view --comments', { cwd: process.cwd(), timeout: 15_000, maxBuffer: 512 * 1024 }, (err, stdout) => {
          const out = `${stdout || ''}`.trim()
          if (err || !out) {
            addEntry({ kind: 'system', text: 'no open PR for this branch (or gh not installed / not authenticated)' })
          } else {
            addEntry({ kind: 'system', text: out })
          }
        })
        return true
      }
      case 'diff': {
        // DiffDialog (§4): with >1 changed file, pick one to view; else dump.
        exec('git diff --name-only', { cwd: process.cwd(), timeout: 10_000, maxBuffer: 256 * 1024 }, (err, stdout) => {
          const files = `${stdout || ''}`.split('\n').map((s) => s.trim()).filter(Boolean)
          if (!files.length) {
            addEntry({ kind: 'system', text: 'no working-tree changes' })
          } else if (files.length === 1) {
            runBang(`git --no-pager diff -- '${(files[0] as string).replace(/'/g, "'\\''")}'`)
          } else {
            setPicker({ kind: 'difffile', title: `Changed files (${files.length})`, options: files, sel: 0 })
          }
        })
        return true
      }
      case 'stats': {
        const prompts = entries.filter((e) => e.kind === 'user').length
        const tools = entries.filter((e) => e.kind === 'tool').length
        const cu = contextUsage
        const lines = [
          `prompts: ${prompts}`,
          `tool calls: ${tools}`,
          `cost: $${(sessionCost || 0).toFixed(4)}`,
        ]
        if (cu) {
          lines.push(`context: ${Math.round(cu.percentage)}% (${cu.totalTokens.toLocaleString()}/${cu.maxTokens.toLocaleString()})`)
        }
        addEntry({ kind: 'system', text: `Session stats:\n${lines.join('\n')}` })
        return true
      }
      case 'config': {
        if (client) {
          void client.requestControl('get_settings').then((r) => {
            const m = String(r?.['model'] ?? model)
            const pm = String(r?.['permission_mode'] ?? mode)
            const avail = Array.isArray(r?.['available_models']) ? (r['available_models'] as string[]) : []
            const lines = [`model: ${m}`, `mode: ${pm}`, `server: ${serverLabel}`]
            if (avail.length) lines.push(`available models: ${avail.slice(0, 12).join(', ')}`)
            addEntry({ kind: 'system', text: `Config:\n${lines.join('\n')}` })
          })
        }
        return true
      }
      case 'statusline': {
        if (!arg || arg === 'clear' || arg === 'off') {
          statusCmdRef.current = null
          setStatusCmd(null)
          setStatusText('')
          addEntry({ kind: 'system', text: 'status line cleared' })
        } else {
          statusCmdRef.current = arg
          setStatusCmd(arg)
          runStatusline(arg)
          addEntry({ kind: 'system', text: `status line set: ${arg}` })
        }
        return true
      }
      case 'stickers': {
        addEntry({ kind: 'system', text: 'Claude Code stickers: https://www.stickermule.com/claudecode' })
        return true
      }
      case 'outputStyle': {
        setPicker({
          kind: 'outputstyle',
          title: 'Output style',
          options: ['default', 'concise', 'verbose', 'markdown'],
          sel: 0,
        })
        return true
      }
      case 'wiki': {
        const parts = arg.trim().split(/\s+/).filter(Boolean)
        const action = (parts[0] || 'status').toLowerCase()
        const path = parts.slice(1).join(' ')
        void client?.requestControl('wiki', { action, path }).then((r) => {
          if (!r || r['ok'] === false) {
            addEntry({ kind: 'error', text: `wiki: ${r && r['error'] ? String(r['error']) : 'no response'}` })
            return
          }
          if (action === 'init') {
            const created = (r['created_files'] as string[]) || []
            addEntry({
              kind: 'system',
              text: r['already_existed']
                ? `wiki already exists at ${String(r['root'])}`
                : `✓ wiki initialized at ${String(r['root'])} (${created.length} files)`,
            })
          } else if (action === 'ingest') {
            addEntry({ kind: 'system', text: `✓ ingested → ${String(r['dest'])}` })
          } else {
            addEntry({
              kind: 'system',
              text: r['initialized']
                ? `Wiki: initialized · ${Number(r['page_count']) || 0} pages, ${Number(r['source_count']) || 0} sources`
                : `Wiki not initialized — run /wiki init (${String(r['root'])})`,
            })
          }
        })
        return true
      }
      case 'knowledge': {
        const action = (arg || 'status').trim().toLowerCase()
        void client?.requestControl('knowledge', { action }).then((r) => {
          if (!r || !r['ok']) {
            addEntry({ kind: 'error', text: `knowledge failed: ${r && r['error'] ? String(r['error']) : 'no response'}` })
            return
          }
          const st = (r['stats'] as Record<string, number>) || {}
          const en = r['enabled'] ? 'enabled' : 'disabled'
          const mode = r['semantic'] ? ' · semantic' : ''
          const head = `Knowledge graph: ${en}${mode} · ${st['total'] || 0} entities (${st['file'] || 0} files, ${st['symbol'] || 0} symbols, ${st['url'] || 0} urls)`
          const ents = (r['entities'] as Array<{ name: string; type: string; count: number }>) || []
          const lines = ents.map((e) => `  ${e.type === 'file' ? '📄' : e.type === 'url' ? '🔗' : '◆'} ${e.name} ·${e.count}`)
          addEntry({ kind: 'system', text: lines.length ? `${head}\n${lines.join('\n')}` : head })
        })
        return true
      }
      case 'btw': {
        const q = arg.trim()
        if (!q) {
          addEntry({ kind: 'system', text: 'usage: /btw <question>' })
        } else if (busy) {
          addEntry({ kind: 'system', text: 'busy — wait for the current turn before a side question' })
        } else {
          dispatchPrompt(q, true) // ephemeral
        }
        return true
      }
      case 'lang': {
        const lang = arg.trim()
        if (client) {
          void client.requestControl('set_language', { language: lang === 'clear' ? '' : lang }).then((r) => {
            const set = r && r['language'] ? String(r['language']) : ''
            addEntry({ kind: 'system', text: set ? `responses will be in ${set}` : 'response language cleared' })
          })
        }
        return true
      }
      case 'trust': {
        const a = arg.trim().toLowerCase()
        const cwd = process.cwd()
        if (a === 'add' || a === 'yes' || a === '') {
          trustFolder(cwd)
          addEntry({ kind: 'system', text: `✓ folder trusted: ${cwd}` })
        } else if (a === 'remove' || a === 'no') {
          untrustFolder(cwd)
          addEntry({ kind: 'system', text: `folder untrusted: ${cwd}` })
        } else {
          addEntry({ kind: 'system', text: `folder ${isTrusted(cwd) ? 'is trusted' : 'is NOT trusted'}: ${cwd}` })
        }
        return true
      }
      case 'open': {
        const q = arg.trim()
        if (!q) {
          addEntry({ kind: 'system', text: 'usage: /open <query>' })
          return true
        }
        const files = searchFiles(process.cwd(), q, Date.now())
        if (!files.length) {
          addEntry({ kind: 'system', text: `no files match "${q}"` })
          return true
        }
        setPicker({ kind: 'openfile', title: `Open file (${files.length})`, options: files, sel: 0 })
        return true
      }
      case 'fast': {
        // Toggle a faster model (the original's /fast). Heuristic: pick a model
        // whose name reads "fast" from the provider's available list.
        void client?.requestControl('get_settings', {}).then((r) => {
          const models = ((r && (r['available_models'] as string[])) || []).filter(Boolean)
          const cur = String((r && r['model']) || model)
          if (fastModeRef.current) {
            const prev = fastModeRef.current
            fastModeRef.current = null
            setFastMode(false)
            client?.sendControl('set_model', { model: prev })
            setModel(prev)
            addEntry({ kind: 'system', text: `fast mode off — model → ${prev}` })
            return
          }
          const fast = models.find((m) => /haiku|mini|flash|turbo|lite|small|fast/i.test(m) && m !== cur)
          if (!fast) {
            addEntry({ kind: 'system', text: 'no faster model available in this provider' })
            return
          }
          fastModeRef.current = cur
          setFastMode(true)
          client?.sendControl('set_model', { model: fast })
          setModel(fast)
          addEntry({ kind: 'system', text: `⚡ fast mode on — model → ${fast}` })
        })
        return true
      }
      case 'plan': {
        const a = arg.trim()
        if (!a) {
          void client?.requestControl('plan', { action: 'view' }).then((r) => {
            const plan = String((r && r['plan']) || '')
            addEntry({ kind: 'system', text: plan ? `Current plan:\n${plan}` : 'no plan set — /plan <text> to set one' })
          })
          return true
        }
        if (a.toLowerCase() === 'clear') {
          void client?.requestControl('plan', { action: 'clear' }).then(() => addEntry({ kind: 'system', text: 'plan cleared' }))
          return true
        }
        if (a.toLowerCase() === 'edit') {
          void client?.requestControl('plan', { action: 'view' }).then((r) => {
            const cur = String((r && r['plan']) || '')
            const sin = process.stdin as unknown as { setRawMode?: (m: boolean) => void }
            const canRaw = typeof sin.setRawMode === 'function'
            let next = cur
            try {
              if (canRaw) sin.setRawMode?.(false)
              next = editInEditor(cur)
            } catch (e) {
              addEntry({ kind: 'error', text: `editor failed: ${(e as Error).message}` })
              return
            } finally {
              if (canRaw) sin.setRawMode?.(true)
              setThemeVersion((v) => v + 1)
            }
            void client
              ?.requestControl('plan', { action: 'set', text: next })
              .then(() => addEntry({ kind: 'system', text: 'plan updated' }))
          })
          return true
        }
        void client?.requestControl('plan', { action: 'set', text: a }).then((r) => {
          addEntry({ kind: 'system', text: r && r['ok'] ? 'plan set — the agent will follow it' : 'plan failed' })
        })
        return true
      }
      case 'insights': {
        addEntry({ kind: 'system', text: '∴ analyzing session…' })
        void client?.requestControl('insights', {}, 120_000).then((r) => {
          if (r && r['ok']) {
            addEntry({ kind: 'assistant', text: `**Session insights**\n\n${String(r['insights'] || '(none)')}` })
          } else {
            addEntry({ kind: 'error', text: `insights failed: ${r && r['error'] ? String(r['error']) : 'no response'}` })
          }
        })
        return true
      }
      case 'image': {
        const p = arg.trim()
        if (!p) {
          addEntry({ kind: 'system', text: 'usage: /image <path>' })
          return true
        }
        try {
          const buf = readFileSync(p)
          const ext = (p.toLowerCase().split('.').pop() || '').trim()
          const media =
            ext === 'png'
              ? 'image/png'
              : ext === 'gif'
                ? 'image/gif'
                : ext === 'webp'
                  ? 'image/webp'
                  : 'image/jpeg'
          const name = p.split('/').pop() || p
          pendingImageRef.current = { data: buf.toString('base64'), media_type: media, name }
          addEntry({
            kind: 'system',
            text: `📎 image attached: ${name} (${Math.round(buf.length / 1024)} KB) — sent with your next message`,
          })
        } catch (e) {
          addEntry({ kind: 'error', text: `image read failed: ${(e as Error).message}` })
        }
        return true
      }
      case 'bgAgent': {
        const p = arg.trim()
        if (!p) {
          addEntry({ kind: 'system', text: 'usage: /bg-agent <prompt>' })
          return true
        }
        void client?.requestControl('bg_agent', { command: p }).then((r) => {
          if (r && r['ok']) addEntry({ kind: 'system', text: `▶ background agent ${String(r['id'])}: ${p}` })
          else addEntry({ kind: 'error', text: `bg-agent failed: ${r && r['error'] ? String(r['error']) : 'no response'}` })
        })
        return true
      }
      case 'bg': {
        const a = arg.trim()
        if (a.toLowerCase().startsWith('kill ')) {
          const id = a.slice(5).trim()
          void client?.requestControl('bg_kill', { id }).then((r) => {
            addEntry({ kind: 'system', text: r && r['ok'] ? `killed bg task ${id}` : `no such task ${id}` })
          })
          return true
        }
        if (!a) {
          addEntry({ kind: 'system', text: 'usage: /bg <command>  (or /bg kill <id>)' })
          return true
        }
        void client?.requestControl('bg_run', { command: a }).then((r) => {
          if (r && r['ok']) addEntry({ kind: 'system', text: `▶ background task ${String(r['id'])}: ${a}` })
          else addEntry({ kind: 'error', text: `bg failed: ${r && r['error'] ? String(r['error']) : 'no response'}` })
        })
        return true
      }
      case 'tasks': {
        const lines: string[] = []
        if (busy) lines.push(`● running — ${toolActivity || 'agent turn in progress'}`)
        if (queued.length) lines.push(`⏳ ${queued.length} queued prompt${queued.length === 1 ? '' : 's'}`)
        void client?.requestControl('bg_list', {}).then((r) => {
          const tasks = (r && (r['tasks'] as Array<{ id: string; command: string; status: string }>)) || []
          for (const t of tasks) {
            const icon = t.status === 'running' ? '●' : t.status === 'done' ? '✓' : t.status === 'killed' ? '⊘' : '✗'
            lines.push(`${icon} bg ${t.id} [${t.status}] — ${t.command}`)
          }
          if (!lines.length) lines.push('no active tasks')
          addEntry({ kind: 'system', text: `Tasks:\n${lines.join('\n')}` })
        })
        return true
      }
      case 'settings': {
        const opts = ['model', 'mode', 'theme', 'effort', 'provider']
        setPicker({ kind: 'settings', title: 'Settings (select to change)', options: opts, sel: 0 })
        return true
      }
      case 'history': {
        const h = historyRef.current
        if (!h.length) {
          addEntry({ kind: 'system', text: 'no history yet' })
          return true
        }
        const opts: string[] = []
        const vals: string[] = []
        for (let i = h.length - 1; i >= 0; i--) {
          opts.push((h[i] || '').replace(/\s+/g, ' ').slice(0, 60))
          vals.push(h[i] as string)
        }
        setPicker({ kind: 'historyrecall', title: 'History (recall a prompt)', options: opts, values: vals, sel: 0 })
        return true
      }
      case 'search': {
        if (!arg) {
          addEntry({ kind: 'system', text: 'usage: /search <query>' })
          return true
        }
        const q = arg.replace(/'/g, "'\\''") // safe single-quote for the shell
        addEntry({ kind: 'system', text: `/search ${arg}` })
        exec(
          `rg -n -S --max-count=50 --max-columns=200 -- '${q}'`,
          { cwd: process.cwd(), timeout: 15_000, maxBuffer: 512 * 1024 },
          (err, stdout, stderr) => {
            const out = `${stdout || ''}`.trim()
            if (out) {
              addEntry({ kind: 'toolResult', text: out.split('\n').slice(0, 40).join('\n') })
            } else if (err && (err as { code?: number }).code === 1) {
              addEntry({ kind: 'system', text: 'no matches' })
            } else {
              addEntry({ kind: 'error', text: stderr ? String(stderr).split('\n')[0] : 'search failed' })
            }
          },
        )
        return true
      }
      case 'buddy': {
        if (arg === 'off') {
          setBuddy(null)
          addEntry({ kind: 'system', text: 'buddy off' })
        } else if (arg && BUDDY_SPECIES.includes(arg)) {
          setBuddy(arg)
          addEntry({ kind: 'system', text: `buddy → ${arg}` })
        } else {
          const next = buddy ? null : 'cat'
          setBuddy(next)
          addEntry({ kind: 'system', text: next ? `buddy → ${next} (try: ${BUDDY_SPECIES.join(' / ')})` : 'buddy off' })
        }
        return true
      }
      case 'reloadPlugins': {
        if (client) {
          void client.requestControl('reload_plugins').then((r) => {
            addEntry({ kind: 'system', text: `reloaded ${Number(r?.['count']) || 0} plugin(s)` })
          })
        }
        return true
      }
      case 'plugin': {
        if (client) {
          void client.requestControl('list_plugins').then((r) => {
            const plugins = Array.isArray(r?.['plugins']) ? (r['plugins'] as Record<string, unknown>[]) : []
            if (!plugins.length) {
              addEntry({ kind: 'system', text: 'no plugins installed (~/.claude/plugins or .claude/plugins)' })
              return
            }
            const lines = plugins.map((p) => `● ${String(p['name'])}${p['enabled'] ? '' : ' (disabled)'}`)
            addEntry({ kind: 'system', text: `Plugins (${plugins.length}):\n${lines.join('\n')}` })
          })
        }
        return true
      }
      case 'effort': {
        if (client) {
          void client.requestControl('set_effort', { effort: arg }).then((r) => {
            const lvl = r && r['effort'] ? String(r['effort']) : ''
            setEffort(lvl && lvl !== 'default' ? `effort:${lvl}` : '') // EffortCallout (§7)
            addEntry({ kind: 'system', text: `reasoning effort → ${lvl || 'default'}` })
          })
        }
        return true
      }
      case 'provider': {
        if (!arg) {
          addEntry({ kind: 'system', text: 'usage: /provider <name>' })
        } else if (client) {
          void client.requestControl('set_provider', { provider: arg }).then((r) => {
            if (r && r['ok']) {
              if (r['model']) setModel(String(r['model']))
              addEntry({ kind: 'system', text: `provider → ${String(r['provider'])}${r['model'] ? ` (${String(r['model'])})` : ''}` })
            } else {
              addEntry({ kind: 'error', text: `provider switch failed: ${r && r['error'] ? String(r['error']) : 'no response'}` })
            }
          })
        }
        return true
      }
      case 'hooks': {
        if (client) {
          void client.requestControl('list_hooks').then((r) => {
            const h = (r?.['hooks'] as Record<string, unknown>) || {}
            if (!Object.keys(h).length) {
              addEntry({ kind: 'system', text: 'no hook configuration' })
              return
            }
            addEntry({
              kind: 'system',
              text: `Hooks: ${h['enabled'] ? 'enabled' : 'disabled'} · timeout ${Number(h['timeout_ms']) || 0}ms · max ${Number(h['max_concurrent']) || 0} concurrent`,
            })
          })
        }
        return true
      }
      case 'upgrade': {
        addEntry({
          kind: 'system',
          text: 'Update clawcodex: re-run the install script (curl … | bash) or `pip install -U clawcodex`.',
        })
        return true
      }
      case 'addDir': {
        if (!arg) {
          addEntry({ kind: 'system', text: 'usage: /add-dir <path>' })
        } else if (client) {
          void client.requestControl('add_dir', { path: arg }).then((r) => {
            addEntry({
              kind: r && r['ok'] ? 'system' : 'error',
              text: r && r['ok'] ? `added working dir: ${String(r['path'])}` : `add-dir failed: ${r && r['error'] ? String(r['error']) : 'no response'}`,
            })
          })
        }
        return true
      }
      case 'releaseNotes': {
        addEntry({
          kind: 'system',
          text: 'Release notes are published on the clawcodex GitHub releases page.',
        })
        return true
      }
      case 'feedback': {
        addEntry({
          kind: 'system',
          text: 'Report bugs or share feedback on the clawcodex GitHub issues page.',
        })
        return true
      }
      case 'logo': {
        // LogoPicker (§6/§7): pick a gradient palette for the wordmark. Re-shows
        // the banner with the chosen palette on select.
        const names = Object.keys(LOGO_PALETTES)
        const cur = getLogoPalette()
        setPicker({
          kind: 'logopalette',
          title: `Logo palette (current: ${cur})`,
          options: names,
          sel: Math.max(0, names.indexOf(cur)),
        })
        return true
      }
      case 'keybindings': {
        const keys = [
          'Enter         submit',
          'Ctrl+C        interrupt / exit',
          'Ctrl+D        exit (empty input)',
          'Ctrl+R        reverse history search',
          '↑ / ↓         input history',
          'Ctrl+W/U/K    kill word / to start / to end',
          'Ctrl+A / E    line start / end',
          'Ctrl+Y        yank (paste killed text)',
          'Alt+← / →     word motion',
          'Tab           accept completion',
          '! …           bash mode',
          '@             file mention',
          '/             slash command',
          'Esc           cancel (vim: normal mode)',
          'PgUp / PgDn   scroll transcript (fullscreen)',
          'Ctrl+F        find in transcript (fullscreen)',
          '?             shortcuts overlay',
        ]
        addEntry({ kind: 'system', text: `Keybindings:\n${keys.map((k) => '  ' + k).join('\n')}` })
        return true
      }
      case 'files': {
        const files = searchFiles(process.cwd(), '', Date.now())
        if (!files.length) {
          addEntry({ kind: 'system', text: 'no files found' })
          return true
        }
        const shown = files.slice(0, 30)
        const more = files.length > shown.length ? ` (showing 30)` : ''
        addEntry({ kind: 'system', text: `Files${more}:\n${shown.join('\n')}` })
        return true
      }
      case 'skills': {
        if (client) {
          void client.requestControl('list_skills').then((r) => {
            const skills = Array.isArray(r?.['skills']) ? (r['skills'] as Record<string, unknown>[]) : []
            const total = Number(r?.['total']) || skills.length
            if (!total) {
              addEntry({ kind: 'system', text: 'no skills available' })
              return
            }
            const shown = skills.slice(0, 24).map((s) => `● ${String(s['name'])}`)
            const more = total > shown.length ? `\n…and ${total - shown.length} more` : ''
            addEntry({ kind: 'system', text: `Skills (${total}):\n${shown.join('\n')}${more}` })
          })
        }
        return true
      }
      case 'agents': {
        if (client) {
          void client.requestControl('list_agents').then((r) => {
            const agents = Array.isArray(r?.['agents']) ? (r['agents'] as Record<string, unknown>[]) : []
            if (!agents.length) {
              addEntry({ kind: 'system', text: 'no agents available' })
              return
            }
            const lines = agents.map((a) => {
              const when = String(a['when'] || '').replace(/\s+/g, ' ').slice(0, 60)
              return `● ${String(a['type'])} (${String(a['source'])})${when ? ` — ${when}` : ''}`
            })
            addEntry({ kind: 'system', text: `Agents (${agents.length}):\n${lines.join('\n')}` })
          })
        }
        return true
      }
      case 'permissions': {
        if (client) {
          void client.requestControl('list_permissions').then((r) => {
            const mode = String(r?.['mode'] ?? 'default')
            const allow = Array.isArray(r?.['allow']) ? (r['allow'] as string[]) : []
            const deny = Array.isArray(r?.['deny']) ? (r['deny'] as string[]) : []
            const lines = [`mode: ${mode}`]
            if (allow.length) lines.push(`allow: ${allow.join(', ')}`)
            if (deny.length) lines.push(`deny: ${deny.join(', ')}`)
            if (!allow.length && !deny.length) lines.push('(no explicit allow/deny rules)')
            addEntry({ kind: 'system', text: `Permissions:\n${lines.join('\n')}` })
          })
        }
        return true
      }
      case 'mcp': {
        if (client) {
          void client.requestControl('list_mcp').then((r) => {
            const servers = Array.isArray(r?.['servers']) ? (r['servers'] as Record<string, unknown>[]) : []
            if (!servers.length) {
              addEntry({ kind: 'system', text: 'no MCP servers connected (configure servers in .mcp.json / config.json)' })
              return
            }
            // Open the multiselect (§6 MCPServerMultiselectDialog): toggle servers on/off.
            setMcpToggle({
              servers: servers.map((s) => ({
                name: String(s['name']),
                enabled: s['enabled'] !== false,
                tools: Array.isArray(s['tools']) ? (s['tools'] as string[]) : [],
              })),
              sel: 0,
            })
          })
        }
        return true
      }
      case 'mcpTrust': {
        if (client) {
          void client.requestControl('list_mcp').then((r) => {
            const servers = Array.isArray(r?.['servers']) ? (r['servers'] as Record<string, unknown>[]) : []
            if (!servers.length) {
              addEntry({ kind: 'system', text: 'no MCP servers to approve' })
              return
            }
            const names = servers.map((s) => String(s['name']))
            names.forEach(trustMcp)
            addEntry({ kind: 'system', text: `✓ approved MCP server(s): ${names.join(', ')}` })
          })
        }
        return true
      }
      case 'branch': {
        if (client) {
          void client.requestControl('branch').then((r) => {
            addEntry({
              kind: r && r['ok'] ? 'system' : 'error',
              text:
                r && r['ok']
                  ? `⎇ branched to ${String(r['session_id'])} — /resume to switch`
                  : `branch failed: ${r && r['error'] ? String(r['error']) : 'no response'}`,
            })
          })
        }
        return true
      }
      case 'resume': {
        if (client) {
          void client.requestControl('list_sessions').then((r) => {
            const sessions = Array.isArray(r?.['sessions']) ? (r['sessions'] as Record<string, unknown>[]) : []
            if (!sessions.length) {
              addEntry({ kind: 'system', text: 'no saved sessions' })
              return
            }
            resumeSessionsRef.current = sessions
            resumeFilterRef.current = '' // fresh search each open
            // Default to this-project; fall back to all if none here (TagTabs, §6).
            const here = sessions.filter((s) => String(s['cwd'] || '') === process.cwd())
            const all = !here.length
            setResumeAll(all)
            openResumePicker(all)
          })
        }
        return true
      }
      case 'rewind': {
        if (arg) {
          requestRewind(Math.max(1, parseInt(arg, 10) || 1))
          return true
        }
        // No arg → MessageSelector-style picker of restore points (past prompts).
        const prompts = entries.filter((e) => e.kind === 'user')
        if (!prompts.length) {
          addEntry({ kind: 'system', text: 'nothing to rewind' })
          return true
        }
        const opts: string[] = []
        const vals: string[] = []
        for (let i = prompts.length - 1; i >= 0; i--) {
          const preview = String(prompts[i]?.text || '').replace(/\s+/g, ' ').slice(0, 50)
          opts.push(`before: ${preview}`)
          vals.push(String(prompts.length - i)) // turns to drop to reach before prompt i
        }
        setPicker({ kind: 'rewindpick', title: 'Rewind to before…', options: opts, values: vals, sel: 0 })
        return true
      }
      case 'doctor': {
        addEntry({
          kind: 'system',
          text: [
            'clawcodex diagnostics',
            `  connection  ${connected ? 'connected' : 'disconnected'}`,
            `  server      ${serverLabel}`,
            `  model       ${model}`,
            `  mode        ${mode}`,
            `  tools       ${tools}`,
            `  protocol    v${SUPPORTED_PROTOCOL_MAJOR}.x`,
            `  theme       ${currentThemeName()}`,
            `  memory      ${(process.memoryUsage().rss / 1048576).toFixed(0)} MB rss · uptime ${Math.floor(process.uptime())}s`,
            `  cwd         ${process.cwd()}`,
          ].join('\n'),
        })
        return true
      }
      case 'copy': {
        // Copy the last assistant response via OSC 52 (terminal clipboard; works
        // in iTerm2/kitty/wezterm/… without a clipboard library).
        const last = [...entries].reverse().find((e) => e.kind === 'assistant' && e.text.trim())
        if (last) {
          const b64 = Buffer.from(last.text, 'utf8').toString('base64')
          process.stdout.write(`\x1b]52;c;${b64}\x07`)
          addEntry({ kind: 'system', text: 'copied last response to clipboard' })
        } else {
          addEntry({ kind: 'system', text: 'nothing to copy' })
        }
        return true
      }
      case 'export': {
        try {
          const md = transcriptToMarkdown(entries)
          const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
          const file = join(process.cwd(), `clawcodex-transcript-${ts}.md`)
          writeFileSync(file, md, 'utf8')
          addEntry({ kind: 'system', text: `exported transcript → ${file}` })
        } catch (e) {
          addEntry({ kind: 'error', text: `export failed: ${String((e as Error).message)}` })
        }
        return true
      }
      case 'theme': {
        if (!arg) {
          const options = ['dark', 'light']
          setPicker({
            kind: 'theme',
            title: 'Select theme',
            options,
            sel: Math.max(0, options.indexOf(currentThemeName())),
          })
          return true
        }
        if (applyTheme(arg)) {
          setThemeVersion((v) => v + 1) // repaint dynamic UI; new output uses the theme
          addEntry({ kind: 'system', text: `theme → ${arg}` })
        } else {
          addEntry({ kind: 'system', text: `usage: ${cmd.name} <dark|light>` })
        }
        return true
      }
      case 'context': {
        // Pull a fresh usage snapshot, then render the category breakdown.
        if (client) {
          void client.requestControl('get_context_usage').then((r) => {
            applyContextUsage(r)
            if (r && typeof r['percentage'] === 'number') {
              addEntry({
                kind: 'context',
                text: '',
                contextData: {
                  percentage: r['percentage'] as number,
                  totalTokens: Number(r['total_tokens']) || 0,
                  maxTokens: Number(r['max_tokens']) || 0,
                  categories: Array.isArray(r['categories'])
                    ? (r['categories'] as { name?: unknown; tokens?: unknown }[]).map((c) => ({
                        name: String(c.name ?? ''),
                        tokens: Number(c.tokens) || 0,
                      }))
                    : [],
                },
              })
            } else {
              addEntry({ kind: 'system', text: 'context usage unavailable' })
            }
          })
        }
        return true
      }
      case 'compact': {
        if (client) {
          addEntry({ kind: 'system', text: 'Compacting conversation…' })
          void client
            .requestControl('compact', arg ? { instructions: arg } : {}, 120_000)
            .then((r) => {
              if (r && r['ok']) {
                const saved = Number(r['tokens_saved']) || 0
                const pre = Number(r['pre_compact_count']) || 0
                const post = Number(r['post_compact_count']) || 0
                const sv = saved >= 1000 ? `${(saved / 1000).toFixed(1)}k` : String(saved)
                // CompactSummary boundary marker (the original's §3) — a divider
                // showing the summarization point + count/direction.
                addEntry({
                  kind: 'system',
                  text: `── ✻ Summarized conversation · ${pre} → ${post} messages · saved ${sv} tokens ──`,
                })
                void client.requestControl('get_context_usage').then(applyContextUsage)
              } else {
                addEntry({
                  kind: 'error',
                  text: `compact failed: ${r && r['error'] ? String(r['error']) : 'no response'}`,
                })
              }
            })
        }
        return true
      }
      case 'control': {
        if (!arg) {
          if (cmd.control === 'set_permission_mode') {
            const options = ['default', 'acceptEdits', 'plan', 'bypassPermissions']
            setPicker({
              kind: 'mode',
              title: 'Select permission mode',
              options,
              sel: Math.max(0, options.indexOf(mode)),
            })
            return true
          }
          if (cmd.control === 'set_model' && client) {
            // Pull the provider's model list, then open the picker.
            void client.requestControl('get_settings').then((r) => {
              const models = Array.isArray(r?.['available_models'])
                ? (r['available_models'] as unknown[]).map(String).filter(Boolean)
                : []
              if (models.length) {
                setPicker({
                  kind: 'model',
                  title: 'Select model',
                  options: models,
                  sel: Math.max(0, models.indexOf(model)),
                })
              } else {
                addEntry({ kind: 'system', text: `usage: ${cmd.name} <name>  (no model list available)` })
              }
            })
            return true
          }
          addEntry({ kind: 'system', text: `usage: ${cmd.name} <value>` })
          return true
        }
        if (cmd.control === 'set_model') {
          client?.sendControl('set_model', { model: arg })
          setModel(arg)
        } else if (cmd.control === 'set_permission_mode') {
          setPermMode(arg) // confirms bypassPermissions / first-time acceptEdits
          return true
        }
        addEntry({ kind: 'system', text: `${cmd.name} → ${arg}` })
        return true
      }
      default:
        return false
    }
  }

  /** Replace `[Pasted text #N]` placeholders with their real text for the model. */
  const expandPastes = (t: string): string => {
    let e = t
    for (const [token, real] of pasteStore.current) {
      if (e.includes(token)) e = e.split(token).join(real)
    }
    return e
  }

  /** Send a prompt now and start a turn (shared by submit + queue drain). */
  const dispatchPrompt = (text: string, ephemeral = false): void => {
    if (!client) return
    const img = pendingImageRef.current
    if (ephemeral) {
      // Side question (/btw): answered with context but not saved to history.
      client.sendEphemeralPrompt(expandPastes(text))
    } else if (img) {
      // Multimodal: send text + image as a content-block list (inventory §1).
      pendingImageRef.current = null
      client.sendPromptBlocks([
        { type: 'text', text: expandPastes(text) },
        { type: 'image', source: { type: 'base64', media_type: img.media_type, data: img.data } },
      ])
    } else {
      client.sendPrompt(expandPastes(text)) // model gets the full paste; transcript shows the placeholder
    }
    if (historyRef.current[historyRef.current.length - 1] !== text) historyRef.current.push(text)
    if (ephemeral) addEntry({ kind: 'system', text: '↪ side question — not saved to history' })
    addEntry({ kind: 'user', text })
    setStream('')
    setBusy(true)
    turnToolCounts.current = {}
    setToolActivity(null)
    thinkingCommittedRef.current = false // fresh turn — allow committing this turn's reasoning
    thinkingRef.current = '' // drop any reasoning orphaned by a prior error/disconnect
    setThinkingStream('')
    liveRef.current = []
    collapsedIds.current.clear()
    setLiveTools([])
    setAgentLines([])
    setScrollOffset(0) // follow the tail on a new turn
    setTurnStartedAt(Date.now())
    turnStartRef.current = Date.now()
  }

  // Drain one queued prompt when the turn ends (busy → false).
  useEffect(() => {
    if (!busy && ready && permissions.length === 0 && queuedRef.current.length > 0) {
      const next = queuedRef.current.shift() as string
      setQueued([...queuedRef.current])
      dispatchPrompt(next)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, ready, permissions.length])

  // Refresh the custom status line (/statusline) when set and at each turn end.
  useEffect(() => {
    if (statusCmd && !busy) runStatusline(statusCmd)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, statusCmd])

  // Fullscreen scroll anchoring: when messages arrive while the user is scrolled
  // up, keep their view stable (bump the offset by the growth) instead of letting
  // it jump; the "↑ N newer" header then reflects the new arrivals. At the bottom
  // (offset 0) the view follows new messages as before.
  useEffect(() => {
    const growth = entries.length - lastLenRef.current
    lastLenRef.current = entries.length
    if (FULLSCREEN && growth > 0) {
      setScrollOffset((o) => (o > 0 ? Math.min(Math.max(0, entries.length - 1), o + growth) : 0))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries.length])

  // Terminal focus tracking (DECSET 1004, §8): enable focus reporting so the
  // completion notification can fire when the user is away (unfocused).
  useEffect(() => {
    try {
      process.stdout.write('\x1b[?1004h')
    } catch {
      /* non-tty */
    }
    return () => {
      try {
        process.stdout.write('\x1b[?1004l')
      } catch {
        /* ignore */
      }
    }
  }, [])

  // Folder-trust first-run notice (the original's TrustDialog, §6): once ready,
  // if this folder hasn't been acknowledged, surface a one-line notice. The
  // backend permission system still gates every action; /trust records consent.
  const trustNotedRef = useRef(false)
  useEffect(() => {
    if (ready && !trustNotedRef.current) {
      trustNotedRef.current = true
      if (!isTrusted(process.cwd())) {
        addEntry({ kind: 'system', text: '⚠ new folder — files/commands run here; /trust to acknowledge' })
      }
      // KeybindingWarnings (§8): flag combos bound to more than one action.
      const conflicts = bindingConflicts()
      if (conflicts.length) {
        addEntry({ kind: 'system', text: `⚠ keybinding conflict(s): ${conflicts.join('; ')}` })
      }
      // Config validation (§6): surface malformed config files (else silently ignored).
      const cfgErrs = configErrors()
      if (cfgErrs.length) {
        addEntry({ kind: 'error', text: `invalid config:\n  ${cfgErrs.join('\n  ')}` })
      }
      // External CLAUDE.md imports (§6 ClaudeMdExternalIncludesDialog): if any are
      // pending approval, prompt before they're loaded into the system prompt.
      void client?.requestControl('external_includes').then((r) => {
        const externals = Array.isArray(r?.['externals']) ? (r['externals'] as string[]) : []
        if (r && r['state'] === 'unset' && externals.length) setExternalIncludes(externals)
      })
      // PrBadge (§7): show the current branch's PR in the footer, via gh.
      exec('gh pr view --json number,state', { cwd: process.cwd(), timeout: 8000 }, (err, out) => {
        if (err) return
        try {
          const pr = JSON.parse(`${out}`) as { number?: number; state?: string }
          if (pr.number) setPrBadge(`#${pr.number}${pr.state && pr.state !== 'OPEN' ? ` ${pr.state.toLowerCase()}` : ''}`)
        } catch {
          /* no PR / bad json */
        }
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready])

  // Cost-threshold warning (the original's CostThresholdDialog, §7): surface a
  // notice once per threshold the cumulative session cost crosses.
  const costWarnedRef = useRef(0)
  useEffect(() => {
    const thresholds = [5, 10, 25, 50, 100]
    const crossed = thresholds.filter((t) => sessionCost >= t && t > costWarnedRef.current)
    if (crossed.length) {
      const top = Math.max(...crossed)
      costWarnedRef.current = top
      addEntry({ kind: 'system', text: `⚠ session cost crossed $${top} (now $${sessionCost.toFixed(2)})` })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionCost])

  // Auto-allow tools the user marked "don't ask again" (skip the prompt). Bash is
  // matched by command prefix (granular); other tools by name.
  useEffect(() => {
    const head = permissions[0]
    if (!head) return
    const bashPfx =
      head.toolName === 'Bash'
        ? String((head.input as { command?: string }).command ?? '').trim().split(/\s+/)[0] || ''
        : ''
    const auto =
      alwaysAllowRef.current.has(head.toolName) || (bashPfx !== '' && bashAllowPrefixRef.current.has(bashPfx))
    if (auto) {
      client?.respondPermission(head.requestId, 'allow')
      setPermissions((q) => q.slice(1))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [permissions])

  // Bash mode (`!cmd`): run a shell command client-side and show its output,
  // without involving the model. The TUI runs in the same cwd as the
  // agent-server, so this matches the agent's working directory (the original
  // runs `!` commands in-process too).
  const runBang = (cmd: string): void => {
    addEntry({ kind: 'system', text: `! ${cmd}` })
    try {
      exec(cmd, { cwd: process.cwd(), timeout: 30_000, maxBuffer: 1024 * 1024 }, (err, stdout, stderr) => {
        const out = `${stdout || ''}${stderr ? (stdout ? '\n' : '') + stderr : ''}`.replace(/\s+$/, '')
        if (err && !out) {
          addEntry({ kind: 'error', text: `(exit ${(err as { code?: number }).code ?? 1})` })
        } else {
          addEntry({ kind: err ? 'error' : 'toolResult', text: out || '(no output)' })
        }
      })
    } catch (e) {
      addEntry({ kind: 'error', text: `bash failed: ${String(e)}` })
    }
  }

  // /statusline: run a user shell command and show its first output line as a
  // custom status line (the original's statusLine.command). Re-runs each turn end.
  const runStatusline = (cmd: string): void => {
    try {
      exec(cmd, { cwd: process.cwd(), timeout: 5000, maxBuffer: 64 * 1024 }, (err, stdout, stderr) => {
        if (statusCmdRef.current !== cmd) return // a later clear/change supersedes this run
        const out =
          `${stdout || ''}`.split('\n')[0]?.trim() ||
          (stderr ? String(stderr).split('\n')[0]?.trim() : '') ||
          ''
        setStatusText(err && !out ? '' : out)
      })
    } catch {
      /* never break the UI on a bad statusline command */
    }
  }

  // Shared input onChange: collapse a large paste to a placeholder (the
  // original's [Pasted text #N]) and reset menu/history selection.
  const handleInputChange = (v: string): void => {
    const { ins, p, s } = diffInsert(input, v)
    const nLines = ins ? ins.split('\n').length : 0
    if (ins && (ins.length > 200 || nLines >= 4)) {
      const n = (pasteCounter.current += 1)
      const token = `[Pasted text #${n}${nLines > 1 ? ` +${nLines} lines` : ''}]`
      pasteStore.current.set(token, ins)
      setInput(v.slice(0, p) + token + (s ? v.slice(v.length - s) : ''))
    } else {
      setInput(v)
    }
    setSlashSel(0)
    setAtSel(0)
    setHistIdx(-1)
  }

  const onSubmit = (value: string): void => {
    // Trailing-backslash line continuation (the original's multiline input):
    // a line ending in `\` + Enter inserts a newline instead of submitting.
    if (value.endsWith('\\') && !slashOpen && !atOpen) {
      setInput(`${value.slice(0, -1)}\n`)
      return
    }
    const text = value.trim()
    if (!text) return
    // Enter while the @-file menu is open completes the highlighted path
    // instead of submitting.
    if (atOpen) {
      const pick = atMatches[atSelClamped]
      if (pick) completeAt(pick)
      return
    }
    // Never send a partial slash to the model: run an exact command, else
    // complete to the highlighted match.
    if (text.startsWith('/')) {
      if (resolveSlash(text)) {
        runSlash(text)
        return
      }
      if (slashOpen) {
        const pick = slashMatches[sel]
        if (pick) {
          setInput(`${pick.name} `)
          setSlashSel(0)
        }
        return
      }
    }
    // Bash mode: `!cmd` runs a shell command instead of prompting the model.
    if (text.startsWith('!')) {
      const cmd = text.slice(1).trim()
      setHistIdx(-1)
      draftRef.current = ''
      setInput('')
      setSlashSel(0)
      if (cmd) runBang(cmd)
      return
    }
    if (!client || !ready || permissions.length > 0) return
    setHistIdx(-1)
    draftRef.current = ''
    setInput('')
    setSlashSel(0)
    if (busy) {
      // Queue prompts typed while the agent is working (the original's queued
      // commands) with now/next/later priority (inventory §1): a leading "now:"
      // interrupts the current turn and runs next; "next:" jumps the queue;
      // otherwise it's appended (later). The drain effect sends the front item
      // when the turn ends.
      const pri = text.match(/^(now|next)\s*:\s*([\s\S]+)$/i)
      if (pri) {
        queuedRef.current.unshift(pri[2] as string) // front of the queue
        if ((pri[1] as string).toLowerCase() === 'now') client?.interrupt() // run it immediately
      } else {
        queuedRef.current.push(text) // later (default, FIFO)
      }
      setQueued([...queuedRef.current])
      return
    }
    dispatchPrompt(text)
  }

  const termRows = process.stdout.rows ?? 24
  // Fullscreen: collapse consecutive reads (unless expanded), then render the
  // height-aware tail window (newest at the bottom); overflow:hidden is just a
  // safety clip since heights are over-estimated.
  const fsEntries = FULLSCREEN && !expanded ? groupReads(entries) : entries
  const visibleEntries = FULLSCREEN
    ? windowFromBottom(fsEntries, process.stdout.columns ?? 80, Math.max(4, termRows - 4), scrollOffset)
    : entries
  // Bidi/RTL (Hebrew, Arabic) is handled natively by the cell-diff renderer, so no
  // manual text shaping is needed here (it would double-reverse and cancel out).
  const renderEntry = (entry: TranscriptEntry): React.ReactElement => (
    <Box key={entry.id} marginTop={['tool', 'toolResult', 'banner'].includes(entry.kind) ? 0 : 1}>
      <Message
        entry={entry}
        expanded={expanded}
        timestamp={timestampsOn && entry.ts ? new Date(entry.ts).toTimeString().slice(0, 5) : undefined}
      />
    </Box>
  )

  return (
    <Box flexDirection="column" {...(FULLSCREEN ? { height: termRows } : {})}>
      {FULLSCREEN ? (
        <Box flexGrow={1} flexDirection="column" overflow="hidden">
          {scrollOffset > 0 ? (
            <Text color={theme.dim} wrap="truncate-end">
              {(() => {
                const lastPrompt = [...entries].reverse().find((e) => e.kind === 'user')?.text
                return `┊ ${lastPrompt ? lastPrompt : ''}  · ↑ ${scrollOffset} newer — PgDn to follow`
              })()}
            </Text>
          ) : null}
          {visibleEntries.map(renderEntry)}
        </Box>
      ) : (
        // Inline mode: the transcript is part of the live, cell-diffed tree (the
        // renderer has no <Static>). Each row is a memoized <Message>, so unchanged
        // entries bail reconciliation → the per-keystroke diff touches only the
        // input, and rows that scroll off the top land in the terminal's native
        // scrollback (same as <Static> gave us, without the whole-frame rewrite).
        entries.map(renderEntry)
      )}

      {streaming ? (
        <Box>
          <Box width={2}>
            <Text color={theme.accent}>⏺</Text>
          </Box>
          <Box flexGrow={1}>
            {/* Cap the live stream to a viewport-fitting tail (plain text) so a
                long in-progress response can't grow the live region without bound;
                the full markdown is committed as an entry when the assistant
                message lands. */}
            <Text>
              {streamTail(streaming, (process.stdout.columns ?? 80) - 4, (process.stdout.rows ?? 24) - 10)}
            </Text>
          </Box>
        </Box>
      ) : null}

      {thinkingStream && !streaming ? (
        // Compact live indicator only (the original shows a "thinking" spinner, not
        // the reasoning text). The full reasoning commits as a collapsed entry at
        // turn end — expand with ctrl+o. Keeping this static also means reasoning
        // deltas never drive per-token writes, so they can't lag input.
        <Box>
          <Box width={2}>
            <Text color={theme.dim}>∴</Text>
          </Box>
          <Box flexGrow={1}>
            <Text color={theme.dim} italic>
              Thinking…
            </Text>
          </Box>
        </Box>
      ) : null}

      {liveTools.length > 0 ? <LiveTools groups={liveTools} /> : null}

      {agentLines.length > 0 ? (
        <Box flexDirection="column">
          {agentLines.length > 1 ? (
            // CoordinatorAgentStatus (§9): summary over concurrently-running subagents.
            <Text color={theme.accent}>
              {`⛬ coordinating ${agentLines.length} agents · ${agentLines.reduce((a, l) => a + (l.toolUseCount || 0), 0)} tools`}
            </Text>
          ) : null}
          {agentLines.map((l) => (
            <AgentProgressLine key={l.agentId} line={l} />
          ))}
        </Box>
      ) : null}

      {busy && permissions.length === 0 ? (
        <Box>
          {/* Spinner shows the activity for non-read tools; the live block above
              carries it while reads are collapsing. */}
          <Spinner startedAt={turnStartedAt} activity={liveTools.length ? null : toolActivity} />
        </Box>
      ) : null}

      {contextUsage && contextUsage.percentage >= 80 ? (
        <Box marginTop={1}>
          <Text color={contextUsage.percentage >= 90 ? theme.error : theme.warn}>
            {`⚠ Context ${Math.round(contextUsage.percentage)}% used — run /compact to free space`}
          </Text>
        </Box>
      ) : null}

      {buddy ? (
        <Box marginTop={1}>
          <CompanionSprite species={buddy} />
        </Box>
      ) : null}

      {externalIncludes ? (
        <Box flexDirection="column" borderStyle="round" borderColor={theme.error} borderLeft={false} borderRight={false} paddingX={1} marginTop={1}>
          <Text color={theme.error} bold>
            {'⚠ Allow external CLAUDE.md file imports?'}
          </Text>
          <Text color={theme.dim}>{'Your CLAUDE.md @-imports files outside this project:'}</Text>
          {externalIncludes.slice(0, 6).map((p) => (
            <Text key={p} color={theme.dim}>{`  • ${p}`}</Text>
          ))}
          <Text color={theme.dim}>{'  y to allow · any other key to disable'}</Text>
        </Box>
      ) : mcpToggle ? (
        <Box flexDirection="column" borderStyle="round" borderColor={theme.suggestion} borderLeft={false} borderRight={false} paddingX={1} marginTop={1}>
          <Text color={theme.dim}>{'MCP servers — space to toggle, esc to close'}</Text>
          {mcpToggle.servers.map((s, i) => (
            <Text key={s.name} color={i === mcpToggle.sel ? theme.suggestion : undefined} bold={i === mcpToggle.sel}>
              {`${i === mcpToggle.sel ? '❯ ' : '  '}${s.enabled ? '✓' : '☐'} ${s.name}  (${s.tools.length} tool${s.tools.length === 1 ? '' : 's'})`}
            </Text>
          ))}
        </Box>
      ) : pendingMode ? (
        <Box flexDirection="column" borderStyle="round" borderColor={theme.error} borderLeft={false} borderRight={false} paddingX={1} marginTop={1}>
          <Text color={theme.error} bold>
            {pendingMode === 'bypassPermissions' ? '⚠ Enable bypass-permissions mode?' : '⚠ Enable auto-accept-edits mode?'}
          </Text>
          <Text color={theme.dim}>
            {pendingMode === 'bypassPermissions'
              ? 'This disables ALL permission prompts — every tool runs without asking.'
              : 'Edits will be applied without asking (Bash and other tools still prompt).'}
          </Text>
          <Text color={theme.dim}>{'  y to enable · any other key to cancel'}</Text>
        </Box>
      ) : elicit ? (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor={theme.suggestion}
          borderLeft={false}
          borderRight={false}
          paddingX={1}
          marginTop={1}
        >
          <Text color={theme.accent} bold>
            {'⌯ MCP server requests input'}
          </Text>
          <Text>{elicit.message}</Text>
          <Box>
            <Text color={theme.dim}>{`${elicit.field}: `}</Text>
            <Text>{elicit.value}</Text>
            <Text inverse> </Text>
          </Box>
          <Text color={theme.dim}>{'  enter to send · esc to decline'}</Text>
        </Box>
      ) : permission ? (
        <Box flexDirection="column">
          <PermissionDialog toolName={permission.toolName} input={permission.input} selected={permSel} />
          {permFeedback !== null ? (
            <Box paddingX={1}>
              <Text color={theme.dim}>{'tell the agent: '}</Text>
              <Text>{permFeedback}</Text>
              <Text inverse> </Text>
            </Box>
          ) : (
            <Text color={theme.dim}>{'  tab to amend · esc to cancel'}</Text>
          )}
        </Box>
      ) : picker ? (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor={theme.suggestion}
          borderLeft={false}
          borderRight={false}
          paddingX={1}
          marginTop={1}
        >
          <Text color={theme.dim}>{picker.title}</Text>
          {picker.options.map((opt, i) =>
            i === picker.sel ? (
              <Text key={opt}>
                <Text color={theme.suggestion} bold>
                  {'❯ '}
                </Text>
                <Text bold>{opt}</Text>
              </Text>
            ) : (
              <Text key={opt} color={theme.dim}>{`  ${opt}`}</Text>
            ),
          )}
          <Text color={theme.dim}>↑↓ select · enter confirm · esc cancel</Text>
        </Box>
      ) : searchMode ? (
        <Box
          borderStyle="round"
          borderColor={theme.promptBorder}
          borderLeft={false}
          borderRight={false}
          paddingX={1}
          width="100%"
        >
          <Text color={theme.dim}>{`(reverse-i-search)\`${searchQuery}\`: `}</Text>
          <Text>{searchMatch}</Text>
        </Box>
      ) : FULLSCREEN && txFind !== null ? (
        <Box
          borderStyle="round"
          borderColor={theme.promptBorder}
          borderLeft={false}
          borderRight={false}
          paddingX={1}
          width="100%"
        >
          <Text color={theme.dim}>{`(find)\`${txFind}\`  ^F next · esc done`}</Text>
        </Box>
      ) : (
        <>
          {queued.length > 0 ? (
            <Box flexDirection="column" marginBottom={1}>
              {queued.map((q, i) => (
                <Text key={i} color={theme.dim}>
                  {`  ⏎ ${q.length > 72 ? `${q.slice(0, 71)}…` : q}`}
                </Text>
              ))}
            </Box>
          ) : null}
          {slashOpen ? <SlashMenu matches={slashMatches} selected={sel} /> : null}
          {atOpen ? <FileMenu matches={atMatches} selected={atSelClamped} /> : null}
          <Box
            borderStyle="round"
            borderColor={theme.promptBorder}
            borderLeft={false}
            borderRight={false}
            paddingX={1}
            width="100%"
          >
            {!vimMode ? (
              <Text color={input.startsWith('!') ? theme.error : ready ? theme.accent : theme.dim}>
                {busy ? '… ' : input.startsWith('!') ? '! ' : '❯ '}
              </Text>
            ) : null}
            {/* One controlled input for both modes: readline editing (Ctrl+A/E/W/U/K,
                arrows) always; /vim adds the normal/insert keymap. Stays active during
                slash/@ menus (it ignores ↑/↓/Tab — App handles nav — and routes Enter
                through onSubmit, which does menu completion). */}
            <VimInput
              value={input}
              vimEnabled={vimMode}
              onChange={handleInputChange}
              onSubmit={onSubmit}
              active={!elicit && !pendingMode && !mcpToggle && !externalIncludes}
              placeholder={ready ? 'Type a message, or / for commands…' : 'starting agent-server…'}
            />
            {/* Inline ghost-text completion for slash commands (inventory §1):
                dim suffix of the top match; Tab/Enter accepts via the menu. */}
            {(() => {
              const top = slashMatches[0]
              if (
                !vimMode &&
                top &&
                input.startsWith('/') &&
                !input.includes(' ') &&
                top.name.startsWith(input) &&
                top.name.length > input.length
              ) {
                return <Text color={theme.dim}>{top.name.slice(input.length)}</Text>
              }
              return null
            })()}
          </Box>
        </>
      )}

      {statusText ? <Text color={theme.dim}>{statusText}</Text> : null}
      <DevBar
        entries={entries.length}
        agents={agentLines.length}
        busy={busy}
        stream={streaming.length}
        scroll={scrollOffset}
        fullscreen={FULLSCREEN}
      />
      <StatusBar
        connected={connected}
        model={model}
        mode={mode}
        busy={busy}
        context={contextUsage}
        cost={sessionCost}
        fast={fastMode}
        effort={effort}
        prBadge={prBadge}
      />
    </Box>
  )
}
