/**
 * Convert server SDK messages into flat transcript entries the Ink UI renders.
 *
 * Distilled from typescript/src/remote/sdkMessageAdapter.ts (which converts the
 * same SDKMessage stream into REPL message types). Streaming `stream_event`
 * deltas are handled in App.tsx (they mutate a live buffer); everything else
 * maps to zero-or-more finished entries here.
 */
import {
  blocksToText,
  type ContentBlock,
  type ServerMessage,
} from './protocol.js';

export type EntryKind = 'user' | 'assistant' | 'tool' | 'system' | 'result' | 'error';

export interface TranscriptEntry {
  id: string;
  kind: EntryKind;
  text: string;
}

let _seq = 0;
function nextId(): string {
  _seq += 1;
  return `e${_seq}`;
}

/** Pull the human-readable text out of a `stream_event` text delta, if any. */
export function streamDeltaText(msg: ServerMessage): string | null {
  const m = msg as { type?: string; event?: { delta?: { type?: string; text?: string } } };
  if (m.type !== 'stream_event') return null;
  const delta = m.event?.delta;
  if (delta && delta.type === 'text_delta' && typeof delta.text === 'string') {
    return delta.text;
  }
  return null;
}

function toolResultText(content: string | ContentBlock[] | undefined): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  const parts: string[] = [];
  for (const block of content) {
    if (block && block.type === 'tool_result') {
      const inner = block['content'];
      parts.push(typeof inner === 'string' ? inner : JSON.stringify(inner));
    } else if (block && block.type === 'text' && typeof block.text === 'string') {
      parts.push(block.text);
    }
  }
  return parts.join('\n');
}

/** Map one server message to finished transcript entries (excludes stream_event). */
export function messageToEntries(msg: ServerMessage): TranscriptEntry[] {
  const type = (msg as { type: string }).type;

  if (type === 'system') {
    const m = msg as {
      subtype?: string;
      model?: string;
      permission_mode?: string;
      protocol_version?: string;
      tools?: unknown[];
      message?: string;
      level?: string;
    };
    if (m.subtype === 'init') {
      const tools = Array.isArray(m.tools) ? m.tools.length : 0;
      return [
        {
          id: nextId(),
          kind: 'system',
          text: `connected · model ${m.model ?? '?'} · mode ${m.permission_mode ?? '?'} · ${tools} tools · protocol v${m.protocol_version ?? '?'}`,
        },
      ];
    }
    if (m.message) {
      return [{ id: nextId(), kind: m.level === 'error' ? 'error' : 'system', text: m.message }];
    }
    return [];
  }

  if (type === 'assistant') {
    const m = msg as { message: { content: string | ContentBlock[] } };
    const text = blocksToText(m.message?.content);
    return text ? [{ id: nextId(), kind: 'assistant', text }] : [];
  }

  if (type === 'user') {
    const m = msg as { message: { content: string | ContentBlock[] } };
    const text = toolResultText(m.message?.content);
    return text ? [{ id: nextId(), kind: 'tool', text }] : [];
  }

  if (type === 'result') {
    const m = msg as {
      subtype: string;
      num_turns?: number;
      usage?: Record<string, number> | null;
      error?: string;
    };
    if (m.subtype === 'error') {
      return [{ id: nextId(), kind: 'error', text: `error: ${m.error ?? 'unknown'}` }];
    }
    if (m.subtype === 'cancelled') {
      return [{ id: nextId(), kind: 'system', text: 'interrupted' }];
    }
    const usage = m.usage
      ? ` · ${Object.entries(m.usage)
          .map(([k, v]) => `${k}=${v}`)
          .join(' ')}`
      : '';
    return [{ id: nextId(), kind: 'result', text: `done (${m.num_turns ?? 0} turns)${usage}` }];
  }

  return [];
}
