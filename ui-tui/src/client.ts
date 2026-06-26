/**
 * Direct Connect client — HTTP `POST /sessions` + NDJSON-over-WebSocket.
 *
 * Ported from typescript/src/server/directConnectManager.ts (and kept in lock
 * step with the Python port src/server/direct_connect_manager.py). Talks to the
 * Python agent-server started by `clawcodex agent-server`.
 */
import { randomUUID } from 'node:crypto';
import WebSocket from 'ws';
import type { ControlRequestMessage, ServerMessage } from './protocol.js';

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
  private ws: WebSocket | undefined;
  private closed = false;

  constructor(
    private readonly info: SessionInfo,
    private readonly cb: ClientCallbacks,
  ) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const headers: Record<string, string> = {};
      if (this.info.authToken) headers['authorization'] = `Bearer ${this.info.authToken}`;
      const ws = new WebSocket(this.info.wsUrl, { headers });
      this.ws = ws;
      ws.on('open', () => {
        this.cb.onConnected?.();
        resolve();
      });
      ws.on('message', (data: WebSocket.RawData) => this.onData(data.toString('utf8')));
      ws.on('close', () => {
        if (!this.closed) this.cb.onDisconnected?.();
      });
      ws.on('error', (err: Error) => {
        this.cb.onError?.(err);
        reject(err);
      });
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
      } else if (typeof cr.request_id === 'string') {
        this.sendErrorResponse(cr.request_id, `unsupported control subtype: ${subtype}`);
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

  interrupt(): void {
    this.send({
      type: 'control_request',
      request_id: randomUUID(),
      request: { subtype: 'interrupt' },
    });
  }

  private sendErrorResponse(requestId: string, error: string): void {
    this.send({
      type: 'control_response',
      response: { subtype: 'error', request_id: requestId, error },
    });
  }

  private send(obj: unknown): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}
