/**
 * Direct Connect client — HTTP `POST /sessions` + NDJSON-over-WebSocket.
 *
 * Ported from typescript/src/server/directConnectManager.ts (and kept in lock
 * step with the Python port src/server/direct_connect_manager.py). Talks to the
 * Python agent-server started by `clawcodex agent-server`.
 */
import { randomUUID } from 'node:crypto';
import type { ControlRequestMessage, ServerMessage } from './protocol.js';
import type { Transport } from './transport.js';

export interface SessionInfo {
  sessionId: string;
  wsUrl: string;
  workDir?: string;
  authToken: string;
}

/** POST /sessions → create a session and learn the WS URL + token. */
export async function createSession(
  serverHttpUrl: string,
  cwd: string,
  token?: string,
): Promise<SessionInfo> {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (token) headers['authorization'] = `Bearer ${token}`;
  const res = await fetch(`${serverHttpUrl.replace(/\/$/, '')}/sessions`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ cwd }),
  });
  if (!res.ok) {
    throw new Error(`POST /sessions failed: ${res.status} ${res.statusText}`);
  }
  const j = (await res.json()) as {
    session_id: string;
    ws_url: string;
    work_dir?: string;
    auth_token: string;
  };
  return {
    sessionId: j.session_id,
    wsUrl: j.ws_url,
    workDir: j.work_dir,
    authToken: j.auth_token,
  };
}

export interface ClientCallbacks {
  onMessage: (msg: ServerMessage) => void;
  onPermissionRequest: (req: ControlRequestMessage['request'], requestId: string) => void;
  /** An MCP server requested user input (elicitation/create, §6). */
  onElicitation?: (params: Record<string, unknown>, requestId: string) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (err: Error) => void;
}

// Server-internal noise the client never surfaces (mirrors directConnectManager.ts).
const FILTERED = new Set([
  'control_response',
  'keep_alive',
  'control_cancel_request',
  'streamlined_text',
  'streamlined_tool_use_summary',
]);

export class DirectConnectClient {
  private closed = false;
  // request_id → resolver, for control pulls (get_context_usage, get_settings).
  private pending = new Map<string, (response: Record<string, unknown> | null) => void>();

  constructor(
    private readonly transport: Transport,
    private readonly cb: ClientCallbacks,
  ) {}

  connect(): Promise<void> {
    return this.transport.start({
      onData: (text) => this.onData(text),
      onOpen: () => this.cb.onConnected?.(),
      onClose: () => {
        if (!this.closed) this.cb.onDisconnected?.();
      },
      onError: (err) => this.cb.onError?.(err),
    });
  }

  private onData(text: string): void {
    // A single frame may contain several `\n`-delimited NDJSON messages.
    for (const rawLine of text.split('\n')) {
      const line = rawLine.trim();
      if (!line) continue;
      let msg: ServerMessage;
      try {
        msg = JSON.parse(line) as ServerMessage;
      } catch {
        continue;
      }
      if (typeof (msg as { type?: unknown }).type !== 'string') continue;
      this.dispatch(msg);
    }
  }

  private dispatch(msg: ServerMessage): void {
    const type = (msg as { type: string }).type;
    if (type === 'control_request') {
      const cr = msg as ControlRequestMessage;
      const subtype = cr.request?.subtype;
      if (subtype === 'can_use_tool' && typeof cr.request_id === 'string') {
        this.cb.onPermissionRequest(cr.request, cr.request_id);
      } else if (subtype === 'mcp_elicitation' && typeof cr.request_id === 'string' && this.cb.onElicitation) {
        const params = (cr.request as { params?: Record<string, unknown> })?.params ?? {};
        this.cb.onElicitation(params, cr.request_id);
      } else if (typeof cr.request_id === 'string') {
        this.sendErrorResponse(cr.request_id, `unsupported control subtype: ${subtype}`);
      }
      return;
    }
    if (type === 'control_response') {
      // Resolve a correlated control pull (get_context_usage, …). Unmatched
      // responses (e.g. permission acks) fall through and are ignored.
      const cr = msg as { response?: { request_id?: string; response?: unknown } };
      const rid = cr.response?.request_id;
      if (rid && this.pending.has(rid)) {
        const resolve = this.pending.get(rid) as (r: Record<string, unknown> | null) => void;
        this.pending.delete(rid);
        resolve((cr.response?.response as Record<string, unknown>) ?? null);
      }
      return;
    }
    if (type === 'system' && (msg as { subtype?: string }).subtype === 'post_turn_summary') {
      return;
    }
    if (FILTERED.has(type)) return;
    this.cb.onMessage(msg);
  }

  sendPrompt(content: string): void {
    this.send({
      type: 'user',
      message: { role: 'user', content },
      parent_tool_use_id: null,
      session_id: '',
    });
  }

  /** Send a side question (the original's /btw): answered with full context but
   *  not persisted to the conversation history (ephemeral). */
  sendEphemeralPrompt(content: string): void {
    this.send({
      type: 'user',
      message: { role: 'user', content },
      ephemeral: true,
      parent_tool_use_id: null,
      session_id: '',
    });
  }

  /** Send a user turn whose content is a block list (text + image blocks) for
   *  multimodal input. The server preserves non-text blocks (image-paste, §1). */
  sendPromptBlocks(content: Array<Record<string, unknown>>): void {
    this.send({
      type: 'user',
      message: { role: 'user', content },
      parent_tool_use_id: null,
      session_id: '',
    });
  }

  respondPermission(
    requestId: string,
    behavior: 'allow' | 'deny',
    opts: { updatedInput?: Record<string, unknown>; message?: string } = {},
  ): void {
    const response =
      behavior === 'allow'
        ? { behavior: 'allow', updatedInput: opts.updatedInput ?? {} }
        : { behavior: 'deny', message: opts.message ?? '' };
    this.send({
      type: 'control_response',
      response: { subtype: 'success', request_id: requestId, response },
    });
  }

  /** Send a raw control_response payload (e.g. an MCP elicitation result). */
  respondControl(requestId: string, response: Record<string, unknown>): void {
    this.send({
      type: 'control_response',
      response: { subtype: 'success', request_id: requestId, response },
    });
  }

  interrupt(): void {
    this.send({
      type: 'control_request',
      request_id: randomUUID(),
      request: { subtype: 'interrupt' },
    });
  }

  /** Fire a control_request (e.g. set_model, set_permission_mode). */
  sendControl(subtype: string, fields: Record<string, unknown> = {}): void {
    this.send({
      type: 'control_request',
      request_id: randomUUID(),
      request: { subtype, ...fields },
    });
  }

  /**
   * Fire a control_request and resolve with the server's control_response
   * payload (e.g. get_context_usage, get_settings). Resolves null on timeout so
   * a slow/closed link never hangs the caller.
   */
  requestControl(
    subtype: string,
    fields: Record<string, unknown> = {},
    timeoutMs = 5000,
  ): Promise<Record<string, unknown> | null> {
    const requestId = randomUUID();
    return new Promise((resolve) => {
      this.pending.set(requestId, resolve);
      this.send({ type: 'control_request', request_id: requestId, request: { subtype, ...fields } });
      setTimeout(() => {
        if (this.pending.has(requestId)) {
          this.pending.delete(requestId);
          resolve(null);
        }
      }, timeoutMs);
    });
  }

  private sendErrorResponse(requestId: string, error: string): void {
    this.send({
      type: 'control_response',
      response: { subtype: 'error', request_id: requestId, error },
    });
  }

  private send(obj: unknown): void {
    // Trailing newline = NDJSON frame boundary (required by the stdio transport;
    // harmless over WS, where the server already splits on '\n').
    this.transport.send(JSON.stringify(obj) + '\n');
  }

  close(): void {
    this.closed = true;
    this.transport.close();
  }
}
