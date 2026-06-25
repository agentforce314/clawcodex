/**
 * Ink TUI for the clawcodex Python agent-server.
 *
 * Renders the streamed transcript, a live token stream, a permission prompt,
 * and an input line. This is the minimal "thin client" the redesign proposal
 * (my-docs/tui-interface-redesign/) calls Phase 3: all agent logic (model,
 * tools, permissions) runs in the Python backend; this process only renders +
 * collects input over the Direct Connect protocol.
 */
import { Box, Static, Text, useApp, useInput } from 'ink';
import TextInput from 'ink-text-input';
import React, { useEffect, useState } from 'react';
import { DirectConnectClient, type SessionInfo } from './client.js';
import {
  messageToEntries,
  streamDeltaText,
  type TranscriptEntry,
} from './sdkMessageAdapter.js';
import { parseProtocolMajor, SUPPORTED_PROTOCOL_MAJOR } from './protocol.js';

interface Props {
  info: SessionInfo;
  serverLabel: string;
}

interface PendingPermission {
  requestId: string;
  toolName: string;
  input: Record<string, unknown>;
}

const KIND_PREFIX: Record<TranscriptEntry['kind'], string> = {
  user: '› ',
  assistant: '',
  tool: '⚙ ',
  system: '· ',
  result: '✓ ',
  error: '✗ ',
};

const KIND_COLOR: Record<TranscriptEntry['kind'], string | undefined> = {
  user: 'cyan',
  assistant: undefined,
  tool: 'yellow',
  system: 'gray',
  result: 'green',
  error: 'red',
};

export function App({ info, serverLabel }: Props): React.ReactElement {
  const { exit } = useApp();
  const [entries, setEntries] = useState<TranscriptEntry[]>([]);
  const [streaming, setStreaming] = useState('');
  const [permission, setPermission] = useState<PendingPermission | null>(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState('?');
  const [mode, setMode] = useState('?');
  const [connected, setConnected] = useState(false);
  const [client, setClient] = useState<DirectConnectClient | null>(null);

  useEffect(() => {
    const c = new DirectConnectClient(info, {
      onConnected: () => setConnected(true),
      onDisconnected: () => {
        setConnected(false);
        setEntries((e) => [...e, { id: `d${e.length}`, kind: 'system', text: 'disconnected' }]);
      },
      onError: (err) =>
        setEntries((e) => [...e, { id: `er${e.length}`, kind: 'error', text: String(err.message) }]),
      onPermissionRequest: (req, requestId) =>
        setPermission({
          requestId,
          toolName: String((req as { tool_name?: string }).tool_name ?? 'tool'),
          input: ((req as { input?: Record<string, unknown> }).input ?? {}),
        }),
      onMessage: (msg) => {
        const delta = streamDeltaText(msg);
        if (delta !== null) {
          setStreaming((s) => s + delta);
          return;
        }
        const type = (msg as { type?: string }).type;
        if (type === 'assistant') setStreaming(''); // final replaces the live stream
        if (type === 'result') setBusy(false);
        if (type === 'system' && (msg as { subtype?: string }).subtype === 'init') {
          const m = msg as { model?: string; permission_mode?: string; protocol_version?: string };
          setModel(m.model ?? '?');
          setMode(m.permission_mode ?? '?');
          const major = parseProtocolMajor(m.protocol_version);
          if (major !== null && major !== SUPPORTED_PROTOCOL_MAJOR) {
            setEntries((e) => [
              ...e,
              {
                id: `pv${e.length}`,
                kind: 'error',
                text: `protocol major mismatch: server v${m.protocol_version}, client supports v${SUPPORTED_PROTOCOL_MAJOR}.x`,
              },
            ]);
          }
        }
        const newEntries = messageToEntries(msg);
        if (newEntries.length) setEntries((e) => [...e, ...newEntries]);
      },
    });
    setClient(c);
    c.connect().catch((err: Error) =>
      setEntries((e) => [...e, { id: 'cfail', kind: 'error', text: `connect failed: ${err.message}` }]),
    );
    return () => c.close();
  }, [info]);

  useInput((inputChar, key) => {
    if (key.ctrl && inputChar === 'c') {
      client?.close();
      exit();
      return;
    }
    if (permission) {
      if (inputChar === 'y' || inputChar === 'a') {
        client?.respondPermission(permission.requestId, 'allow');
        setPermission(null);
      } else if (inputChar === 'n' || inputChar === 'd' || key.escape) {
        client?.respondPermission(permission.requestId, 'deny', { message: 'denied by user' });
        setPermission(null);
      }
      return;
    }
    if (key.escape && busy) {
      client?.interrupt();
    }
  });

  const onSubmit = (value: string): void => {
    const text = value.trim();
    if (!text || !client || permission) return;
    client.sendPrompt(text);
    setEntries((e) => [...e, { id: `u${e.length}`, kind: 'user', text }]);
    setStreaming('');
    setBusy(true);
    setInput('');
  };

  return (
    <Box flexDirection="column">
      <Static items={entries}>
        {(entry) => (
          <Box key={entry.id}>
            <Text color={KIND_COLOR[entry.kind]}>
              {KIND_PREFIX[entry.kind]}
              {entry.text}
            </Text>
          </Box>
        )}
      </Static>

      {streaming ? <Text>{streaming}</Text> : null}

      {permission ? (
        <Box borderStyle="round" borderColor="yellow" paddingX={1} flexDirection="column">
          <Text color="yellow">
            Allow <Text bold>{permission.toolName}</Text>?
          </Text>
          <Text color="gray">{JSON.stringify(permission.input)}</Text>
          <Text>(y) allow · (n) deny</Text>
        </Box>
      ) : (
        <Box>
          <Text color="cyan">{busy ? '… ' : '› '}</Text>
          <TextInput value={input} onChange={setInput} onSubmit={onSubmit} placeholder="Type a message…" />
        </Box>
      )}

      <Box marginTop={1}>
        <Text color="gray">
          {connected ? '●' : '○'} {serverLabel} · {model} · {mode} · {busy ? 'working (Esc to interrupt)' : 'idle'} · Ctrl-C to quit
        </Text>
      </Box>
    </Box>
  );
}
