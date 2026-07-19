/**
 * ProcessManager — spawns the clawcodex agent-server in --stdio mode and
 * manages the NDJSON stdin/stdout lifecycle.
 *
 * Usage:
 *   const pm = new ProcessManager({ command, cwd, env });
 *   pm.onMessage(msg => { ... });
 *   pm.onError(err => { ... });
 *   pm.onExit(code => { ... });
 *   pm.start();
 *   pm.sendUserMessage('Hello');
 *   await pm.sendControlRequest('resume', { session_id });  // RPC with reply
 *   pm.abort();          // 'interrupt' control (graceful, fire-and-forget)
 *   pm.kill();           // SIGTERM (hard)
 *   pm.dispose();
 */

const { spawn } = require('child_process');
const vscode = require('vscode');
const {
  parseStdoutLine,
  serializeStdinMessage,
  buildUserMessage,
  buildControlResponse,
  buildControlRequest,
  getControlResponseId,
  getControlResponsePayload,
} = require('./protocol');

const DEFAULT_RPC_TIMEOUT_MS = 15000;

// The agent-server logs diagnostics to stderr; only clearly severe lines are
// surfaced as chat errors. Substring match on purpose: `\berror\b` would miss
// `ValueError` / `KeyError` and friends.
const STDERR_SEVERE_RE = /error|traceback|exception|fatal|critical/i;

class ProcessManager {
  /**
   * @param {object} opts
   * @param {string} opts.command - The clawcodex binary (e.g. 'clawcodex')
   * @param {string} [opts.cwd] - Workspace root (passed as --workspace)
   * @param {Record<string,string>} [opts.env] - Extra env vars
   * @param {string} [opts.permissionMode] - default|acceptEdits|plan|bypassPermissions|auto
   * @param {string} [opts.provider] - Provider override (--provider)
   * @param {string} [opts.model] - Model override (--model)
   * @param {string[]} [opts.extraArgs] - Additional CLI flags
   */
  constructor(opts) {
    this._command = opts.command || 'clawcodex';
    this._cwd = opts.cwd || undefined;
    this._env = opts.env || {};
    this._permissionMode = opts.permissionMode || 'acceptEdits';
    this._provider = opts.provider || null;
    this._model = opts.model || null;
    this._extraArgs = opts.extraArgs || [];
    this._sessionId = null;
    this._process = null;
    this._buffer = '';
    this._disposed = false;
    /** @type {Map<string, { resolve: Function, reject: Function, timer: NodeJS.Timeout }>} */
    this._pendingRpcs = new Map();

    this._onMessageEmitter = new vscode.EventEmitter();
    this._onErrorEmitter = new vscode.EventEmitter();
    this._onExitEmitter = new vscode.EventEmitter();
    this.onMessage = this._onMessageEmitter.event;
    this.onError = this._onErrorEmitter.event;
    this.onExit = this._onExitEmitter.event;
  }

  get running() {
    return this._process !== null && !this._process.killed;
  }

  get sessionId() {
    return this._sessionId;
  }

  buildArgs() {
    const args = [
      'agent-server',
      '--stdio',
      '--permission-mode', this._permissionMode || 'acceptEdits',
    ];

    if (this._cwd) {
      args.push('--workspace', this._cwd);
    }
    if (this._provider) {
      args.push('--provider', this._provider);
    }
    if (this._model) {
      args.push('--model', this._model);
    }

    args.push(...this._extraArgs);
    return args;
  }

  start() {
    if (this._disposed) throw new Error('ProcessManager is disposed');
    if (this._process) throw new Error('Process already started');

    const args = this.buildArgs();
    const spawnEnv = { ...process.env, ...this._env };
    const isWin = process.platform === 'win32';

    if (isWin) {
      // On Windows, installer shims are often .cmd files that spawn()
      // cannot find without a shell. Build one command string so the
      // deprecation warning about unsanitised args does not fire.
      const cmdLine = [this._command, ...args].join(' ');
      this._process = spawn(cmdLine, [], {
        cwd: this._cwd,
        env: spawnEnv,
        stdio: ['pipe', 'pipe', 'pipe'],
        shell: true,
        windowsHide: true,
      });
    } else {
      this._process = spawn(this._command, args, {
        cwd: this._cwd,
        env: spawnEnv,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      });
    }

    this._process.stdout.setEncoding('utf8');
    this._process.stderr.setEncoding('utf8');

    this._process.stdout.on('data', (chunk) => this._onData(chunk));
    this._process.stderr.on('data', (chunk) => this._onStderr(chunk));
    this._process.on('error', (err) => this._onErrorEmitter.fire(err));
    this._process.on('close', (code, signal) => {
      this._process = null;
      this._rejectAllRpcs(new Error('agent-server exited'));
      this._onExitEmitter.fire({ code, signal });
    });
  }

  _onData(chunk) {
    this._buffer += chunk;
    const lines = this._buffer.split('\n');
    this._buffer = lines.pop() || '';

    for (const line of lines) {
      const msg = parseStdoutLine(line);
      if (!msg) continue;
      this._extractSessionId(msg);
      if (this._resolveRpc(msg)) continue;
      this._onMessageEmitter.fire(msg);
    }
  }

  _extractSessionId(msg) {
    if (msg.session_id && !this._sessionId) {
      this._sessionId = msg.session_id;
    }
  }

  /**
   * Resolve an inbound control_response against a pending client RPC.
   * Returns true when the message was consumed. Replies with unknown
   * request_ids fall through to onMessage (harmless).
   */
  _resolveRpc(msg) {
    const requestId = getControlResponseId(msg);
    if (!requestId) return false;
    const pending = this._pendingRpcs.get(requestId);
    if (!pending) return false;
    this._pendingRpcs.delete(requestId);
    clearTimeout(pending.timer);
    pending.resolve(getControlResponsePayload(msg));
    return true;
  }

  _rejectAllRpcs(err) {
    for (const pending of this._pendingRpcs.values()) {
      clearTimeout(pending.timer);
      pending.reject(err);
    }
    this._pendingRpcs.clear();
  }

  _onStderr(chunk) {
    for (const rawLine of String(chunk).split('\n')) {
      const trimmed = rawLine.trim();
      if (!trimmed) continue;
      // Suppress non-error noise (deprecation warnings, INFO logs, etc.)
      if (/^\(node:\d+\)|^DeprecationWarning|^ExperimentalWarning/i.test(trimmed)) continue;
      if (!STDERR_SEVERE_RE.test(trimmed)) continue;
      this._onErrorEmitter.fire(new Error(trimmed));
    }
  }

  sendUserMessage(text) {
    this._write(buildUserMessage(text));
  }

  sendControlResponse(requestId, result) {
    this._write(buildControlResponse(requestId, result));
  }

  /**
   * Fire a client → server RPC and await the server's control_response.
   * `interrupt` is fire-and-forget on the server side — use abort() for it.
   */
  sendControlRequest(subtype, payload, options = {}) {
    const envelope = buildControlRequest(subtype, payload);
    const requestId = envelope.request_id;
    const timeoutMs = options.timeoutMs || DEFAULT_RPC_TIMEOUT_MS;

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pendingRpcs.delete(requestId);
        reject(new Error(`${subtype} request timed out`));
      }, timeoutMs);
      this._pendingRpcs.set(requestId, { resolve, reject, timer });
      try {
        this._write(envelope);
      } catch (err) {
        this._pendingRpcs.delete(requestId);
        clearTimeout(timer);
        reject(err);
      }
    });
  }

  write(msg) {
    if (!this._process || !this._process.stdin.writable) {
      throw new Error('Process is not running');
    }
    this._process.stdin.write(serializeStdinMessage(msg));
  }

  _write(msg) {
    this.write(msg);
  }

  /**
   * Graceful turn abort: the `interrupt` control aborts the in-flight turn
   * (the server emits result/cancelled) while the session stays alive.
   * SIGINT would kill the whole server and lose session state.
   */
  abort() {
    if (!this.running) return;
    try {
      this._write(buildControlRequest('interrupt'));
    } catch (err) {
      this._onErrorEmitter.fire(err instanceof Error ? err : new Error(String(err)));
    }
  }

  kill() {
    if (this._process && !this._process.killed) {
      this._process.kill('SIGTERM');
    }
  }

  dispose() {
    this._disposed = true;
    this._rejectAllRpcs(new Error('ProcessManager disposed'));
    this.kill();
    this._onMessageEmitter.dispose();
    this._onErrorEmitter.dispose();
    this._onExitEmitter.dispose();
  }
}

module.exports = { ProcessManager, DEFAULT_RPC_TIMEOUT_MS, STDERR_SEVERE_RE };
