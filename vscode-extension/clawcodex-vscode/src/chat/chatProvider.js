/**
 * chatProvider — WebviewViewProvider (sidebar) and WebviewPanel manager
 * (editor tab) that wire ProcessManager events to the chat UI.
 */

const vscode = require('vscode');
const crypto = require('crypto');
const { ProcessManager } = require('./processManager');
const { buildPermissionControlResult } = require('./permissionResponse');
const { toolDisplayName, toolIcon, parseToolInput } = require('./messageParser');
const { renderChatHtml } = require('./chatRenderer');
const {
  isAssistantMessage, isStreamEvent, isResultMessage,
  isPermissionRequest, isControlRequest, isSystemInit, isSystemStatus,
  getTextContent, getToolUseBlocks,
} = require('./protocol');

async function openFileInEditor(filePath) {
  try {
    const uri = vscode.Uri.file(filePath);
    const doc = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(doc, { preview: false });
  } catch {
    vscode.window.showWarningMessage(`Could not open file: ${filePath}`);
  }
}

function getLaunchConfig() {
  const cfg = vscode.workspace.getConfiguration('clawcodex');
  const { getExecutableFromCommand } = require('../state');
  const launchCommand = cfg.get('launchCommand', 'clawcodex');
  const command = getExecutableFromCommand(launchCommand) || 'clawcodex';
  const permissionMode = cfg.get('permissionMode', 'acceptEdits');
  const provider = (cfg.get('provider', '') || '').trim() || null;
  const model = (cfg.get('model', '') || '').trim() || null;
  const folders = vscode.workspace.workspaceFolders;
  const cwd = folders && folders.length > 0 ? folders[0].uri.fsPath : undefined;
  return { command, cwd, env: {}, permissionMode, provider, model };
}

class ChatController {
  constructor(sessionManager) {
    this._sessionManager = sessionManager;
    this._process = null;
    this._webviews = new Set();
    this._accumulatedText = '';
    this._messages = [];
    this._currentSessionId = null;
    this._streaming = false;
    this._thinkingTokens = 0;
    this._thinkingStartTime = null;
    this._thinkingVisible = false;
    /** @type {Map<string, { input: Record<string, unknown>, suggestions: unknown[], toolUseId: string | null }>} */
    this._pendingPermissions = new Map();

    this._onDidChangeState = new vscode.EventEmitter();
    this.onDidChangeState = this._onDidChangeState.event;
  }

  get sessionId() { return this._currentSessionId; }
  get isStreaming() { return this._process && this._process.running; }
  get sessionManager() { return this._sessionManager; }

  registerWebview(webview) {
    this._webviews.add(webview);
    return { dispose: () => this._webviews.delete(webview) };
  }

  broadcast(msg) {
    for (const wv of this._webviews) {
      try { wv.postMessage(msg); } catch { /* webview might be disposed */ }
    }
  }

  _broadcast(msg) {
    this.broadcast(msg);
  }

  async startSession(opts = {}) {
    this.stopSession();
    this._accumulatedText = '';
    // Only clear messages if this is a brand new session (not a resume)
    if (!opts.sessionId) {
      this._messages = [];
    }
    this._currentSessionId = opts.sessionId || null;

    const { command, cwd, env, permissionMode, provider, model } = getLaunchConfig();

    this._process = new ProcessManager({
      command,
      cwd,
      env,
      permissionMode,
      provider,
      model: opts.model || model,
      extraArgs: opts.extraArgs || [],
    });

    this._readyResolve = null;
    this._readyPromise = new Promise(resolve => { this._readyResolve = resolve; });

    this._process.onMessage((msg) => {
      if (msg.type === 'system' && this._readyResolve) {
        this._readyResolve();
        this._readyResolve = null;
      }
      this._handleMessage(msg);
    });
    this._process.onError((err) => {
      this._broadcast({ type: 'error', message: err.message || String(err) });
    });
    this._process.onExit(({ code }) => {
      // Flush any remaining streamed text
      if (this._streaming && this._accumulatedText) {
        this._broadcast({ type: 'stream_end', text: this._accumulatedText, usage: null, final: true });
      } else if (this._streaming) {
        this._broadcast({ type: 'stream_end', text: '', usage: null, final: true });
      }
      this._streaming = false;
      this._accumulatedText = '';
      this._broadcast({
        type: 'connected',
        message: code === 0 || code === null ? 'Ready' : `Process exited (code ${code})`,
      });
      this._onDidChangeState.fire('idle');
    });

    try {
      this._process.start();
      this._broadcast({ type: 'connected', message: 'Connected' });
      this._onDidChangeState.fire('connected');
    } catch (err) {
      this._broadcast({ type: 'error', message: `Failed to start: ${err.message}` });
      return;
    }

    // Resuming: the webview restore renders from disk; this control reloads
    // the server-side conversation so the next turn continues the session.
    if (opts.sessionId) {
      await this._resumeOnServer(opts.sessionId);
    }
  }

  async _resumeOnServer(sessionId) {
    if (!this._process) return;
    if (this._readyPromise) {
      const grace = new Promise(resolve => setTimeout(resolve, 8000));
      await Promise.race([this._readyPromise, grace]);
      this._readyPromise = null;
    }
    try {
      const reply = await this._process.sendControlRequest('resume', { session_id: sessionId });
      if (reply && reply.ok === false) {
        this._broadcast({
          type: 'error',
          message: `Resume failed: ${reply.error || 'unknown error'}`,
        });
      }
    } catch (err) {
      this._broadcast({ type: 'error', message: `Resume failed: ${err.message}` });
    }
  }

  stopSession() {
    if (this._process) {
      this._process.dispose();
      this._process = null;
    }
    this._streaming = false;
    this._pendingPermissions.clear();
  }

  async sendMessage(text) {
    // Keep the process alive for multi-turn — just send directly.
    // The agent-server maintains full session state across turns.
    // Only start a new process if none exists or it died.
    if (!this._process || !this._process.running) {
      await this.startSession({
        sessionId: this._currentSessionId || undefined,
      });
    }
    await this._doSend(text);
  }

  async _doSend(text) {
    if (!this._process) return;
    // On first message after process start, wait for the server init.
    // Messages sent before init still queue server-side, so the grace
    // timeout only bounds the wait, it cannot lose input.
    if (this._readyPromise) {
      const grace = new Promise(resolve => setTimeout(resolve, 8000));
      await Promise.race([this._readyPromise, grace]);
      this._readyPromise = null;
    }
    this._accumulatedText = '';
    try {
      this._process.sendUserMessage(text);
      this._messages.push({ role: 'user', text });
    } catch (err) {
      this._broadcast({ type: 'error', message: err.message });
    }
  }

  abort() {
    if (this._process) {
      this._process.abort();
      // The server confirms with a result/cancelled message which finalizes
      // the stream; this immediate echo just makes the UI feel responsive.
      this._broadcast({ type: 'status', content: 'Interrupting...' });
    }
  }

  async listServerSessions() {
    if (!this._process || !this._process.running) return null;
    try {
      const reply = await this._process.sendControlRequest('list_sessions', {});
      return Array.isArray(reply?.sessions) ? reply.sessions : [];
    } catch {
      return null;
    }
  }

  sendPermissionResponse(requestId, action, toolUseId) {
    if (!this._process) return;
    const pending = this._pendingPermissions.get(requestId);
    this._pendingPermissions.delete(requestId);
    const result = buildPermissionControlResult(action, {
      input: pending?.input,
      toolUseId: toolUseId || pending?.toolUseId || null,
      permissionSuggestions: pending?.suggestions,
    });
    try {
      this._process.sendControlResponse(requestId, result);
    } catch (err) {
      this._broadcast({ type: 'error', message: err.message });
    }
  }

  getMessages() { return this._messages; }

  /**
   * Mark the turn as streaming (status bar spinner + typing indicator).
   * The agent-server wire has no message_start — the first activity of a
   * turn (text/thinking delta, tool use) flips the state instead.
   */
  _ensureStreaming() {
    if (this._streaming) return;
    this._streaming = true;
    this._broadcast({ type: 'stream_start' });
    this._onDidChangeState.fire('streaming');
  }

  _handleMessage(msg) {
    if (msg.session_id && !this._currentSessionId) {
      this._currentSessionId = msg.session_id;
    }

    // System init — extract model and session info
    if (isSystemInit(msg)) {
      this._broadcast({
        type: 'system_info',
        model: msg.model || null,
        sessionId: msg.session_id || null,
      });
      return;
    }

    // System status lines (info/error) from the server. Some status frames
    // are data-only carriers (e.g. a mid-turn permission-mode push has no
    // message) — don't blank the status pill for those.
    if (isSystemStatus(msg)) {
      if (msg.level === 'error') {
        this._broadcast({ type: 'error', message: msg.message || 'Server error' });
      } else if (typeof msg.message === 'string' && msg.message.trim()) {
        this._broadcast({ type: 'status', content: msg.message });
      }
      return;
    }

    // Other system subtypes (goal_status, ...) are TUI concerns — ignore.
    if (msg.type === 'system') {
      return;
    }

    // Permission ask (control_request subtype can_use_tool)
    if (isPermissionRequest(msg)) {
      const req = msg.request || {};
      const requestId = msg.request_id;
      const toolInput =
        req.input && typeof req.input === 'object' && !Array.isArray(req.input)
          ? req.input
          : {};
      const suggestions = Array.isArray(req.suggestions) ? req.suggestions : [];
      if (requestId) {
        this._pendingPermissions.set(requestId, {
          input: toolInput,
          suggestions,
          toolUseId: req.tool_use_id || null,
        });
      }
      this._broadcast({
        type: 'permission_request',
        requestId,
        toolName: req.tool_name || 'Unknown',
        displayName: toolDisplayName(req.tool_name),
        inputPreview: parseToolInput(req.input),
        toolUseId: req.tool_use_id || null,
        sessionLabel: typeof req.session_label === 'string' ? req.session_label : null,
        warning: typeof req.warning === 'string' ? req.warning : null,
        plan: typeof req.plan === 'string' ? req.plan : null,
        hasSuggestions: suggestions.length > 0,
      });
      return;
    }

    // Non-permission inbound control_requests: nothing for a chat pane to do.
    if (isControlRequest(msg)) {
      return;
    }

    // Anthropic-style stream events (text/thinking deltas)
    if (isStreamEvent(msg)) {
      this._handleStreamEvent(msg);
      return;
    }

    // Assistant message — mid-turn envelope; true completion comes from 'result'
    if (isAssistantMessage(msg)) {
      const inner = msg.message || msg;
      const text = getTextContent(inner);
      const toolBlocks = getToolUseBlocks(inner);
      this._ensureStreaming();
      this._hideThinking();
      const toolUseVms = toolBlocks.map(tu => ({
        id: tu.id,
        name: tu.name,
        displayName: toolDisplayName(tu.name),
        icon: toolIcon(tu.name),
        inputPreview: parseToolInput(tu.input),
        input: tu.input,
        status: 'running',
      }));
      this._messages.push({ role: 'assistant', text, toolUses: toolUseVms });

      // Tool cards go out BEFORE the stream_end that finalizes the bubble,
      // so text and its tool cards share one assistant bubble (the reference
      // got the same grouping via content_block_start during streaming).
      if (toolBlocks.length > 0) {
        for (const vm of toolUseVms) {
          this._broadcast({ type: 'tool_use', toolUse: vm });
        }
      }

      // Finalize current text bubble but stay streaming — true completion
      // is signaled by the 'result' message, not by the assistant message.
      this._broadcast({ type: 'stream_end', text, usage: null, final: false });
      this._accumulatedText = '';

      if (toolBlocks.length > 0) {
        this._broadcast({ type: 'status', content: 'Using tools...' });
      }
      return;
    }

    // User message with tool_result blocks — the tool output
    if (msg.type === 'user' && msg.message) {
      const content = msg.message.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (block.type === 'tool_result' && block.tool_use_id) {
            const resultText = typeof block.content === 'string'
              ? block.content
              : Array.isArray(block.content)
                ? block.content.map(b => b.text || '').join('')
                : '';
            this._broadcast({
              type: 'tool_result',
              toolUseId: block.tool_use_id,
              content: resultText.slice(0, 2000) || '(done)',
              isError: block.is_error || false,
            });
          }
        }
        if (content.some(b => b && b.type === 'tool_result')) {
          this._broadcast({ type: 'status', content: 'Thinking...' });
        }
      }
      return;
    }

    // Per-turn result — the turn is complete. Go idle; the process stays
    // alive for the next turn.
    if (isResultMessage(msg)) {
      // Track the LIVE session id: after a resume the server persists turns
      // under its own (new) id, so a later crash-restart must resume that
      // file, not the originally-picked one.
      if (msg.session_id) {
        this._currentSessionId = msg.session_id;
      }
      this._hideThinking();
      const text = this._accumulatedText || '';
      this._broadcast({ type: 'stream_end', text, usage: msg.usage || null, final: true });
      if (msg.subtype === 'error') {
        this._broadcast({ type: 'error', message: msg.error || msg.result || 'Turn failed' });
      } else if (msg.subtype === 'cancelled') {
        this._broadcast({ type: 'status', content: 'Interrupted' });
      } else if (msg.num_turns !== undefined) {
        this._broadcast({
          type: 'status',
          content: msg.num_turns > 1
            ? 'Completed (' + msg.num_turns + ' turns)'
            : 'Ready',
        });
      }
      this._accumulatedText = '';
      this._streaming = false;
      this._onDidChangeState.fire('idle');
      return;
    }

    // Everything else (agent_progress, task notifications, ...) is ignored.
  }

  _handleStreamEvent(msg) {
    const event = msg.event;
    if (!event || event.type !== 'content_block_delta' || !event.delta) return;

    if (event.delta.type === 'text_delta' && event.delta.text) {
      this._ensureStreaming();
      this._hideThinking();
      this._accumulatedText += event.delta.text;
      this._broadcast({ type: 'stream_delta', text: this._accumulatedText });
    } else if (event.delta.type === 'thinking_delta') {
      this._ensureStreaming();
      if (!this._thinkingVisible) {
        this._thinkingVisible = true;
        this._thinkingTokens = 0;
        this._thinkingStartTime = Date.now();
        this._broadcast({ type: 'thinking_start' });
      }
      this._thinkingTokens += (event.delta.thinking || '').length;
      const elapsed = Math.round((Date.now() - (this._thinkingStartTime || Date.now())) / 1000);
      this._broadcast({
        type: 'thinking_delta',
        tokens: this._thinkingTokens,
        elapsed,
      });
    }
  }

  _hideThinking() {
    if (this._thinkingVisible) {
      this._thinkingVisible = false;
      this._broadcast({ type: 'thinking_end' });
    }
  }

  dispose() {
    this.stopSession();
    this._onDidChangeState.dispose();
  }
}

function attachMessageHandler(webview, chatController, helpers) {
  webview.onDidReceiveMessage(async (msg) => {
    switch (msg.type) {
      case 'send_message':
        chatController.sendMessage(msg.text);
        break;
      case 'abort':
        chatController.abort();
        break;
      case 'new_session':
        chatController.stopSession();
        chatController._currentSessionId = null;
        chatController._messages = [];
        webview.postMessage({ type: 'session_cleared' });
        break;
      case 'resume_session':
        chatController.stopSession();
        chatController._currentSessionId = null;
        chatController._messages = [];
        webview.postMessage({ type: 'session_cleared' });
        await helpers.loadAndDisplaySession(webview, msg.sessionId);
        await chatController.startSession({ sessionId: msg.sessionId });
        break;
      case 'permission_response':
        chatController.sendPermissionResponse(msg.requestId, msg.action, msg.toolUseId);
        break;
      case 'copy_code':
        if (msg.text) await vscode.env.clipboard.writeText(msg.text);
        break;
      case 'open_file':
        if (msg.path) await openFileInEditor(msg.path);
        break;
      case 'request_sessions':
        await helpers.sendSessionList(webview);
        break;
      case 'restore_request':
        helpers.restoreMessages(webview);
        break;
      case 'webview_ready':
        break;
    }
  });
}

function makeWebviewHelpers(chatController) {
  return {
    async sendSessionList(webview) {
      if (!chatController.sessionManager) return;
      try {
        const sessions = await chatController.sessionManager.listSessions();
        webview.postMessage({ type: 'session_list', sessions });
      } catch {
        webview.postMessage({ type: 'session_list', sessions: [] });
      }
    },
    restoreMessages(webview) {
      const messages = chatController.getMessages();
      if (messages.length > 0) {
        webview.postMessage({ type: 'restore_messages', messages });
      }
    },
    async loadAndDisplaySession(webview, sessionId) {
      if (!chatController.sessionManager) return;
      try {
        const messages = await chatController.sessionManager.loadSession(sessionId);
        if (messages && messages.length > 0) {
          chatController._messages = messages;
          webview.postMessage({ type: 'restore_messages', messages });
        }
      } catch { /* session may not be loadable */ }
    },
  };
}

class ClawcodexChatViewProvider {
  constructor(chatController) {
    this._chatController = chatController;
    this._webviewView = null;
  }

  resolveWebviewView(webviewView, _context, _token) {
    this._webviewView = webviewView;
    const webview = webviewView.webview;
    webview.options = { enableScripts: true };

    const registration = this._chatController.registerWebview(webview);
    webviewView.onDidDispose(() => {
      registration.dispose();
      if (this._webviewView === webviewView) this._webviewView = null;
    });

    const nonce = crypto.randomBytes(16).toString('hex');
    webview.html = renderChatHtml({ nonce, platform: process.platform });
    attachMessageHandler(webview, this._chatController, makeWebviewHelpers(this._chatController));
  }
}

class ClawcodexChatPanelManager {
  constructor(chatController) {
    this._chatController = chatController;
    this._panel = null;
  }

  openPanel() {
    if (this._panel) {
      this._panel.reveal();
      return;
    }

    this._panel = vscode.window.createWebviewPanel(
      'clawcodex.chatPanel',
      'Clawcodex Chat',
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      },
    );

    const webview = this._panel.webview;
    const registration = this._chatController.registerWebview(webview);

    this._panel.onDidDispose(() => {
      registration.dispose();
      this._panel = null;
    });

    const nonce = crypto.randomBytes(16).toString('hex');
    webview.html = renderChatHtml({ nonce, platform: process.platform });
    attachMessageHandler(webview, this._chatController, makeWebviewHelpers(this._chatController));

    const messages = this._chatController.getMessages();
    if (messages.length > 0) {
      webview.postMessage({ type: 'restore_messages', messages });
    }
  }

  dispose() {
    if (this._panel) {
      this._panel.dispose();
      this._panel = null;
    }
  }
}

module.exports = {
  ChatController,
  ClawcodexChatViewProvider,
  ClawcodexChatPanelManager,
  getLaunchConfig,
};
