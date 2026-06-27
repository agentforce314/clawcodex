/**
 * Ink TUI for the clawcodex Python agent-server — a Claude-Code-style thin
 * client. All agent logic (model, tools, permissions) runs in the Python
 * backend; this process renders the streamed transcript (markdown, tool calls,
 * results), a live token stream + working spinner, permission prompts (queued
 * so concurrent tool asks aren't dropped), a slash-command menu, and an input
 * line, over the Direct Connect protocol.
 */
import { Box, Static, Text, useApp, useInput } from 'ink'
import TextInput from 'ink-text-input'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import React, { useEffect, useRef, useState } from 'react'
import { DirectConnectClient } from './client.js'
import type { Transport } from './transport.js'
import { Message } from './components/Message.js'
import { PermissionDialog } from './components/PermissionDialog.js'
import { SlashMenu } from './components/SlashMenu.js'
import { FileMenu } from './components/FileMenu.js'
import { searchFiles } from './fileIndex.js'
import { Spinner } from './components/Spinner.js'
import { StatusBar } from './components/StatusBar.js'
import { LiveTools, type LiveGroup } from './components/LiveTools.js'
import { AgentProgressLine, type AgentLine } from './components/AgentProgressLine.js'
import { READ_LIKE, TOOL_VERB, toolActivityLabel } from './toolMeta.js'
import { messageToEntries, streamDeltaText, type TranscriptEntry } from './sdkMessageAdapter.js'
import { matchSlash, resolveSlash } from './slashCommands.js'
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
  const { exit } = useApp()
  const [entries, setEntries] = useState<TranscriptEntry[]>([])
  const [streaming, setStreaming] = useState('')
  const streamRef = useRef('') // source of truth for the live buffer (no stale closures)
  const [permissions, setPermissions] = useState<PendingPermission[]>([]) // FIFO queue
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [turnStartedAt, setTurnStartedAt] = useState(0)
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
  const [, setThemeVersion] = useState(0) // bumped on /theme to repaint the dynamic UI
  const [slashSel, setSlashSel] = useState(0)
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
    kind: 'mode' | 'theme' | 'model'
    title: string
    options: string[]
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

  /** Apply an interactive-picker selection (/mode, /theme, /model). */
  const applyPick = (kind: 'mode' | 'theme' | 'model', value: string): void => {
    if (!value) return
    if (kind === 'mode') {
      client?.sendControl('set_permission_mode', { mode: value })
      setMode(value)
      addEntry({ kind: 'system', text: `mode → ${value}` })
    } else if (kind === 'model') {
      client?.sendControl('set_model', { model: value })
      setModel(value)
      addEntry({ kind: 'system', text: `model → ${value}` })
    } else if (applyTheme(value)) {
      setThemeVersion((v) => v + 1)
      addEntry({ kind: 'system', text: `theme → ${value}` })
    }
  }

  const addEntry = (e: Omit<TranscriptEntry, 'id'>) =>
    setEntries((prev) => [...prev, { ...e, id: `l${localSeq.current++}` }])

  const setStream = (s: string) => {
    streamRef.current = s
    setStreaming(s)
  }
  const appendStream = (delta: string) => {
    streamRef.current += delta
    setStreaming(streamRef.current)
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
        addEntry({ kind: 'system', text: 'backend disconnected' })
      },
      onError: (err) => addEntry({ kind: 'error', text: String(err.message) }),
      onPermissionRequest: (req, requestId) =>
        setPermissions((q) => [
          ...q,
          {
            requestId,
            toolName: String((req as { tool_name?: string }).tool_name ?? 'tool'),
            input: (req as { input?: Record<string, unknown> }).input ?? {},
          },
        ]),
      onMessage: (msg) => {
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
        if (type === 'assistant') setStream('') // final assistant replaces the live stream
        if (type === 'result') {
          setBusy(false)
          setToolActivity(null)
          setAgentLines([]) // subagents are done when the turn ends
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
            addEntry({
              kind: 'banner',
              text: '',
              bannerData: {
                model: m.model ?? '?',
                mode: m.permission_mode ?? '?',
                tools: toolCount,
                cwd: m.cwd,
              },
            })
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
          if (toCommit.length) setEntries((prev) => [...prev, ...toCommit])
        }
      },
    })
    setClient(c)
    c.connect().catch(() => {}) // failures surface via onError / onDisconnected
    return () => c.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transport])

  useInput((ch, key) => {
    if (key.ctrl && ch === 'c') {
      client?.close()
      exit()
      return
    }
    // Interactive picker (/mode, /theme): arrow-navigate, Enter picks, Esc cancels.
    if (picker) {
      if (key.upArrow) {
        setPicker((p) => (p ? { ...p, sel: (p.sel - 1 + p.options.length) % p.options.length } : p))
        return
      }
      if (key.downArrow) {
        setPicker((p) => (p ? { ...p, sel: (p.sel + 1) % p.options.length } : p))
        return
      }
      if (key.return) {
        applyPick(picker.kind, picker.options[picker.sel] ?? '')
        setPicker(null)
        return
      }
      if (key.escape) {
        setPicker(null)
        return
      }
      return // picker swallows all other keys
    }
    // Ctrl+R: open reverse history search, or cycle to the next older match.
    if (key.ctrl && ch === 'r') {
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
    const head = permissions[0]
    if (head) {
      const c = ch.toLowerCase()
      if (c === 'y' || ch === '1') {
        client?.respondPermission(head.requestId, 'allow')
        setPermissions((q) => q.slice(1))
      } else if (c === 'n' || c === 'd' || ch === '2') {
        client?.respondPermission(head.requestId, 'deny', { message: 'denied by user' })
        setPermissions((q) => q.slice(1))
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
    // Input history recall with ↑/↓ when no menu is open (readline-style).
    if (!slashOpen && !atOpen) {
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
      case 'rewind': {
        const n = Math.max(1, parseInt(arg, 10) || 1)
        if (client) {
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
              addEntry({
                kind: 'error',
                text: `rewind failed: ${r && r['error'] ? String(r['error']) : 'no response'}`,
              })
            }
          })
        }
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
                addEntry({
                  kind: 'system',
                  text: `Compacted ${pre} → ${post} messages · saved ${sv} tokens`,
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
          client?.sendControl('set_permission_mode', { mode: arg })
          setMode(arg)
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
  const dispatchPrompt = (text: string): void => {
    if (!client) return
    client.sendPrompt(expandPastes(text)) // model gets the full paste; transcript shows the placeholder
    if (historyRef.current[historyRef.current.length - 1] !== text) historyRef.current.push(text)
    addEntry({ kind: 'user', text })
    setStream('')
    setBusy(true)
    turnToolCounts.current = {}
    setToolActivity(null)
    liveRef.current = []
    collapsedIds.current.clear()
    setLiveTools([])
    setAgentLines([])
    setTurnStartedAt(Date.now())
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

  const onSubmit = (value: string): void => {
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
    if (!client || !ready || permissions.length > 0) return
    setHistIdx(-1)
    draftRef.current = ''
    setInput('')
    setSlashSel(0)
    if (busy) {
      // Queue prompts typed while the agent is working (the original's queued
      // commands); the drain effect sends the next one when the turn ends.
      queuedRef.current.push(text)
      setQueued([...queuedRef.current])
      return
    }
    dispatchPrompt(text)
  }

  return (
    <Box flexDirection="column">
      <Static items={entries}>
        {(entry) => (
          <Box
            key={entry.id}
            marginTop={['tool', 'toolResult', 'banner'].includes(entry.kind) ? 0 : 1}
          >
            <Message entry={entry} />
          </Box>
        )}
      </Static>

      {streaming ? (
        <Box>
          <Box width={2}>
            <Text color={theme.accent}>⏺</Text>
          </Box>
          <Box flexGrow={1}>
            {/* The live stream is the only UNBOUNDED part of the dynamic (non-
                Static) region. If it overflows the viewport Ink can't erase the
                scrolled-off rows, so each re-render (the spinner ticks ~10×/s)
                leaves a stale copy in scrollback → the message appears dozens of
                times. Cap it to a viewport-fitting tail (plain text); the full
                markdown commits to <Static> when the assistant message lands. */}
            <Text>
              {streamTail(streaming, (process.stdout.columns ?? 80) - 4, (process.stdout.rows ?? 24) - 10)}
            </Text>
          </Box>
        </Box>
      ) : null}

      {liveTools.length > 0 ? <LiveTools groups={liveTools} /> : null}

      {agentLines.length > 0 ? (
        <Box flexDirection="column">
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

      {permission ? (
        <PermissionDialog toolName={permission.toolName} input={permission.input} />
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
            <Text color={ready ? theme.accent : theme.dim}>{busy ? '… ' : '❯ '}</Text>
            <TextInput
              value={input}
              onChange={(v) => {
                // Collapse a large paste to a placeholder so it doesn't flood
                // the input (the original's [Pasted text #N]). Normal typing
                // inserts one char at a time, so this only fires on paste.
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
              }}
              onSubmit={onSubmit}
              placeholder={ready ? 'Type a message, or / for commands…' : 'starting agent-server…'}
            />
          </Box>
        </>
      )}

      <StatusBar
        connected={connected}
        model={model}
        mode={mode}
        busy={busy}
        context={contextUsage}
        cost={sessionCost}
      />
    </Box>
  )
}
