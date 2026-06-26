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
import React, { useEffect, useRef, useState } from 'react'
import { DirectConnectClient, type SessionInfo } from './client.js'
import { Markdown } from './markdown.js'
import { Message } from './components/Message.js'
import { PermissionDialog } from './components/PermissionDialog.js'
import { SlashMenu } from './components/SlashMenu.js'
import { Spinner } from './components/Spinner.js'
import { StatusBar } from './components/StatusBar.js'
import { messageToEntries, streamDeltaText, type TranscriptEntry } from './sdkMessageAdapter.js'
import { matchSlash, resolveSlash } from './slashCommands.js'
import { parseProtocolMajor, SUPPORTED_PROTOCOL_MAJOR } from './protocol.js'
import { theme } from './theme.js'

interface Props {
  info: SessionInfo
  serverLabel: string
}

interface PendingPermission {
  requestId: string
  toolName: string
  input: Record<string, unknown>
}

const HELP = [
  'enter — send · esc — interrupt · ^C — quit',
  '/help /clear /model <m> /mode <m> /quit',
  '↑↓ — slash menu · tab — complete',
].join('\n')

export function App({ info, serverLabel }: Props): React.ReactElement {
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
  const [connected, setConnected] = useState(false)
  const [client, setClient] = useState<DirectConnectClient | null>(null)
  const [slashSel, setSlashSel] = useState(0)
  const localSeq = useRef(0)

  const slashMatches = !input.includes(' ') ? matchSlash(input) : []
  const slashOpen = slashMatches.length > 0 && permissions.length === 0
  const sel = Math.min(slashSel, Math.max(0, slashMatches.length - 1))
  const permission = permissions[0] ?? null

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

  useEffect(() => {
    const c = new DirectConnectClient(info, {
      onConnected: () => setConnected(true),
      onDisconnected: () => {
        setConnected(false)
        setBusy(false)
        addEntry({ kind: 'system', text: 'disconnected' })
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
        if (type === 'assistant') setStream('') // final assistant replaces the live stream
        if (type === 'result') {
          setBusy(false)
          flushStream() // commit a partial left over by interrupt/error (no-op on success)
        }
        if (type === 'system' && (msg as { subtype?: string }).subtype === 'init') {
          const m = msg as { model?: string; permission_mode?: string; protocol_version?: string }
          setModel(m.model ?? '?')
          setMode(m.permission_mode ?? '?')
          const major = parseProtocolMajor(m.protocol_version)
          if (major !== null && major !== SUPPORTED_PROTOCOL_MAJOR) {
            addEntry({
              kind: 'error',
              text: `protocol major mismatch: server v${m.protocol_version}, client supports v${SUPPORTED_PROTOCOL_MAJOR}.x`,
            })
          }
        }
        const newEntries = messageToEntries(msg)
        if (newEntries.length) setEntries((e) => [...e, ...newEntries])
      },
    })
    setClient(c)
    c.connect().catch(() => {}) // failures surface via onError / onDisconnected
    return () => c.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info])

  useInput((ch, key) => {
    if (key.ctrl && ch === 'c') {
      client?.close()
      exit()
      return
    }
    const head = permissions[0]
    if (head) {
      const c = ch.toLowerCase()
      if (c === 'y') {
        client?.respondPermission(head.requestId, 'allow')
        setPermissions((q) => q.slice(1))
      } else if (c === 'n' || c === 'd') {
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
        return true
      case 'help':
        addEntry({ kind: 'system', text: HELP })
        return true
      case 'quit':
        client?.close()
        exit()
        return true
      case 'control': {
        if (!arg) {
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

  const onSubmit = (value: string): void => {
    const text = value.trim()
    if (!text) return
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
    if (!client || !connected || busy || permissions.length > 0) return
    client.sendPrompt(text)
    addEntry({ kind: 'user', text })
    setStream('')
    setBusy(true)
    setTurnStartedAt(Date.now())
    setInput('')
    setSlashSel(0)
  }

  return (
    <Box flexDirection="column">
      <Static items={entries}>{(entry) => <Message key={entry.id} entry={entry} />}</Static>

      {streaming ? (
        <Box>
          <Box width={2}>
            <Text color={theme.accent}>⏺</Text>
          </Box>
          <Box flexDirection="column" flexGrow={1}>
            <Markdown text={streaming} />
          </Box>
        </Box>
      ) : null}

      {busy ? (
        <Box>
          <Spinner startedAt={turnStartedAt} />
        </Box>
      ) : null}

      {permission ? (
        <PermissionDialog toolName={permission.toolName} input={permission.input} />
      ) : (
        <>
          {slashOpen ? <SlashMenu matches={slashMatches} selected={sel} /> : null}
          <Box>
            <Text color={connected ? theme.user : theme.dim}>{busy ? '… ' : '❯ '}</Text>
            <TextInput
              value={input}
              onChange={(v) => {
                setInput(v)
                setSlashSel(0)
              }}
              onSubmit={onSubmit}
              placeholder={connected ? 'Type a message, or / for commands…' : 'connecting…'}
            />
          </Box>
        </>
      )}

      <StatusBar connected={connected} serverLabel={serverLabel} model={model} mode={mode} busy={busy} />
    </Box>
  )
}
