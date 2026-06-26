/**
 * Wire protocol shared with the Python agent-server
 * (src/server/agent_server.py + src/server/direct_connect_manager.py).
 *
 * Ported from typescript/src/server/directConnectManager.ts and the Python
 * server's emitted shapes. NDJSON over WebSocket; one JSON object per line.
 */

/** Highest protocol major this client understands. The server announces its
 * version in `system/init`; a mismatched major is a hard error. */
export const SUPPORTED_PROTOCOL_MAJOR = 0;

export interface ContentBlock {
  type: string;
  text?: string;
  // tool_use / tool_result blocks carry extra fields we pass through untyped.
  [k: string]: unknown;
}

export interface SystemInitMessage {
  type: 'system';
  subtype: 'init';
  session_id: string;
  protocol_version: string;
  model?: string;
  provider?: string;
  cwd?: string;
  permission_mode?: string;
  tools?: Array<{ name: string; description?: string; input_schema?: unknown }>;
}

export interface StreamEventMessage {
  type: 'stream_event';
  session_id?: string;
  event: {
    type: string;
    delta?: { type: string; text?: string };
  };
}

export interface AssistantMessage {
  type: 'assistant';
  uuid?: string;
  session_id?: string;
  message: { role: string; content: string | ContentBlock[] };
}

export interface UserMessage {
  type: 'user';
  uuid?: string;
  session_id?: string;
  message: { role: string; content: string | ContentBlock[] };
}

export interface ResultMessage {
  type: 'result';
  subtype: 'success' | 'error' | 'cancelled';
  session_id?: string;
  num_turns?: number;
  result?: string;
  duration_ms?: number;
  is_error?: boolean;
  usage?: Record<string, number> | null;
  error?: string;
}

export interface CanUseToolRequest {
  subtype: 'can_use_tool';
  tool_name: string;
  input?: Record<string, unknown>;
  tool_use_id?: string | null;
}

/** A server → client control request (only `can_use_tool` is acted on). */
export interface ControlRequestMessage {
  type: 'control_request';
  request_id: string;
  request: { subtype: string; [k: string]: unknown };
}

export type ServerMessage =
  | SystemInitMessage
  | StreamEventMessage
  | AssistantMessage
  | UserMessage
  | ResultMessage
  | ControlRequestMessage
  | { type: string; [k: string]: unknown };

/** Extract plain text from an assistant/user content (string or block list). */
export function blocksToText(content: string | ContentBlock[] | undefined): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter((b) => b && b.type === 'text' && typeof b.text === 'string')
      .map((b) => b.text as string)
      .join('');
  }
  return '';
}

export function parseProtocolMajor(version: string | undefined): number | null {
  if (!version) return null;
  const major = Number(version.split('.')[0]);
  return Number.isFinite(major) ? major : null;
}
