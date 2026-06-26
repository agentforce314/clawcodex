/**
 * Ink TUI for the clawcodex Python agent-server — a Claude-Code-style thin
 * client. All agent logic (model, tools, permissions) runs in the Python
 * backend; this process renders the streamed transcript (markdown, tool calls,
 * results), a live token stream + working spinner, permission prompts, a
 * slash-command menu, and an input line, over the Direct Connect protocol.
 */
import { Box, Static, Text, useApp, useInput } from 'ink'
import TextInput from 'ink-text-input'
import React, { useEffect, useState } from 'react'
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
  const [permission, setPermission] = useState<PendingPermission | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [turnStartedAt, setTurnStartedAt] = useState(0)
  const [model, setModel] = useState('?')
  const [mode, setMode] = useState('?')
  const [connected, setConnected] = useState(false)
  const [client, setClient] = useState<DirectConnectClient | null>(null)
  const [slashSel, setSlashSel] = useState(0)

  const slashMatches = !input.includes(' ') ? matchSlash(input) : []
  const slashOpen = slashMatches.length > 0 && permission === null
  const sel = Math.min(slashSel, Math.max(0, slashMatches.length - 1))

  const addEntry = (e: Omit<TranscriptEntry, 'id'>) =>
    setEntries((prev) => [...prev, { ...e, id: `l${prev.length}` }])

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
        setPermission({
          requestId,
          toolName: String((req as { tool_name?: string }).tool_name ?? 'tool'),
          input: (req as { input?: Record<string, unknown> }).input ?? {},
        }),
      onMessage: (msg) => {
        const delta = streamDeltaText(msg)
        if (delta !== null) {
          setStreaming((s) => s + delta)
          return
        }
        const type = (msg as { type?: string }).type
        if (type === 'assistant') setStreaming('') // final replaces the live stream
        if (type === 'result') setBusy(false)
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
    c.connect().catch((err: Error) => addEntry({ kind: 'error', text: `connect failed: ${err.message}` }))
    return () => c.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info])

  useInput((ch, key) => {
    if (key.ctrl && ch === 'c') {
      client?.close()
      exit()
      return
    }
    if (permission) {
      if (ch === 'y') {
        client?.respondPermission(permission.requestId, 'allow')
        setPermission(null)
        setBusy(true)
      } else if (ch === 'n' || ch === 'd' || key.escape) {
        client?.respondPermission(permission.requestId, 'deny', { message: 'denied by user' })
        setPermission(null)
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
    if (!text || !client || permission) return
    if (text.startsWith('/') && runSlash(text)) return
    client.sendPrompt(text)
    addEntry({ kind: 'user', text })
    setStreaming('')
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
            <Text color={theme.user}>{busy ? '… ' : '❯ '}</Text>
            <TextInput
              value={input}
              onChange={(v) => {
                setInput(v)
                setSlashSel(0)
              }}
              onSubmit={onSubmit}
              placeholder="Type a message, or / for commands…"
            />
          </Box>
        </>
      )}

      <StatusBar connected={connected} serverLabel={serverLabel} model={model} mode={mode} busy={busy} />
    </Box>
  )
}
