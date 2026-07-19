/**
 * sessionManager — reads saved clawcodex sessions from disk, lists them,
 * and provides message history for the session list / restore UI.
 *
 * Session files live as flat JSON documents under:
 *   $CLAWCODEX_CONFIG_DIR/sessions/<sessionId>.json   (default ~/.clawcodex)
 *
 * Each file carries {session_id, updated_at (epoch seconds, float), preview,
 * name, message_count, model, cwd, conversation: {messages: [{role, content}]}}
 * — the same store the agent-server's /resume reads. There is deliberately no
 * ~/.claude fallback: clawcodex never shares state with Claude Code.
 */

const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const os = require('os');

// Output cap only. The scan itself is unbounded, mirroring the server's own
// _list_saved_sessions: with a flat sessions/ dir (no per-project bucketing),
// any input-side cap can push every session of the CURRENT workspace out of
// the scanned window on a machine whose other projects were touched more
// recently — turning Resume into a silent "no sessions". listSessions runs
// async off the UI thread, so a full scan-parse of a few thousand files is
// affordable and correct.
const MAX_SESSIONS = 30;

function resolveConfigDir() {
  const envDir = process.env.CLAWCODEX_CONFIG_DIR;
  if (envDir) return envDir;
  return path.join(os.homedir(), '.clawcodex');
}

function getSessionsDir() {
  return path.join(resolveConfigDir(), 'sessions');
}

/**
 * Symlink-tolerant path identity. The agent-server stores a `.resolve()`d
 * cwd (e.g. /private/var/... on macOS) while VS Code hands out the
 * unresolved workspace path — realpath both sides before comparing.
 * Returns null when the path cannot be resolved (caller decides fallback).
 */
function tryRealpath(value) {
  if (!value) return null;
  try {
    return fs.realpathSync.native(String(value));
  } catch {
    return null;
  }
}

function normalizeForCompare(value) {
  if (!value) return '';
  const normalized = path.resolve(String(value));
  // Darwin and Windows default to case-insensitive filesystems.
  return process.platform === 'win32' || process.platform === 'darwin'
    ? normalized.toLowerCase()
    : normalized;
}

class SessionManager {
  constructor() {
    this._cwd = null;
  }

  setCwd(cwd) {
    this._cwd = cwd;
  }

  async listSessions() {
    const dir = getSessionsDir();
    let entries;
    try {
      entries = await fsp.readdir(dir);
    } catch {
      return [];
    }

    const sessions = [];
    for (const name of entries) {
      if (!name.endsWith('.json')) continue;
      const meta = await this._extractSessionMeta(path.join(dir, name));
      if (meta) sessions.push(meta);
    }

    const filtered = this._filterByWorkspace(sessions);
    filtered.sort((a, b) => b.timestamp - a.timestamp);
    return filtered.slice(0, MAX_SESSIONS);
  }

  /**
   * Keep sessions whose stored cwd is the current workspace (realpath both
   * sides). When the workspace path itself cannot be realpath'd, fall back
   * to the full list — a normalization failure must degrade to "show all",
   * never to an empty resume list. A workspace that resolves fine but has
   * no sessions yet stays honestly empty.
   */
  _filterByWorkspace(sessions) {
    if (!this._cwd) return sessions;
    const wsReal = tryRealpath(this._cwd);
    if (!wsReal) return sessions;
    const wsKey = normalizeForCompare(wsReal);
    return sessions.filter(s => {
      if (!s.cwd) return false;
      const sessionKey = normalizeForCompare(tryRealpath(s.cwd) || s.cwd);
      return sessionKey === wsKey;
    });
  }

  async _extractSessionMeta(filePath) {
    let data;
    try {
      data = JSON.parse(await fsp.readFile(filePath, 'utf8'));
    } catch {
      return null;
    }
    if (!data || typeof data !== 'object') return null;

    const sessionId = typeof data.session_id === 'string' && data.session_id
      ? data.session_id
      : path.basename(filePath, '.json');
    // updated_at is epoch SECONDS (float) in the store.
    const updatedAt = Number(data.updated_at);
    const timestamp = Number.isFinite(updatedAt) && updatedAt > 0
      ? updatedAt * 1000
      : (await fsp.stat(filePath).then(s => s.mtimeMs).catch(() => Date.now()));
    const preview = typeof data.preview === 'string' ? data.preview.slice(0, 120) : '';
    const name = typeof data.name === 'string' ? data.name : '';

    return {
      id: sessionId,
      title: name || preview.slice(0, 60) || 'Untitled session',
      preview,
      timestamp,
      timeLabel: formatRelativeTime(timestamp),
      model: typeof data.model === 'string' ? data.model : '',
      cwd: typeof data.cwd === 'string' ? data.cwd : '',
      messageCount: Number(data.message_count) || 0,
      filePath,
    };
  }

  async loadSession(sessionId) {
    if (typeof sessionId !== 'string' || !sessionId) return null;
    // Ids come from our own listing / the server, but harden anyway: a
    // separator or dot-segment must not escape the sessions dir.
    if (/[\\/]/.test(sessionId) || sessionId === '.' || sessionId === '..') return null;
    const filePath = path.join(getSessionsDir(), `${sessionId}.json`);
    let data;
    try {
      data = JSON.parse(await fsp.readFile(filePath, 'utf8'));
    } catch {
      return null;
    }
    const conversation = data && typeof data === 'object' ? data.conversation : null;
    const rawMessages = conversation && Array.isArray(conversation.messages)
      ? conversation.messages
      : [];
    return parseConversationMessages(rawMessages);
  }
}

/**
 * Shape stored conversation messages into the chat view model: user text
 * bubbles plus assistant bubbles with tool_use blocks paired to their
 * tool_result outputs. System-reminder-only user messages are dropped so a
 * restore looks like the original chat did.
 */
function parseConversationMessages(rawMessages) {
  const toolResults = new Map();

  for (const entry of rawMessages) {
    if (!entry || entry.role !== 'user' || !Array.isArray(entry.content)) continue;
    for (const block of entry.content) {
      if (block && block.type === 'tool_result' && block.tool_use_id) {
        toolResults.set(String(block.tool_use_id), {
          content: blockText(block.content).slice(0, 2000),
          isError: block.is_error || false,
        });
      }
    }
  }

  const messages = [];
  for (const entry of rawMessages) {
    if (!entry || typeof entry !== 'object') continue;
    const content = entry.content;

    if (entry.role === 'user') {
      if (Array.isArray(content) && content.some(b => b && b.type === 'tool_result')) {
        continue;
      }
      const text = typeof content === 'string' ? content : blockText(content);
      if (!text || isSystemReminderOnly(text)) continue;
      messages.push({ role: 'user', text });
    } else if (entry.role === 'assistant') {
      const text = typeof content === 'string' ? content : blockText(content);
      const toolUses = Array.isArray(content)
        ? content
            .filter(b => b && b.type === 'tool_use')
            .map(tu => {
              const result = toolResults.get(String(tu.id));
              return {
                id: tu.id,
                name: tu.name,
                input: tu.input || null,
                status: result ? (result.isError ? 'error' : 'complete') : 'complete',
                result: result ? result.content : null,
                isError: result ? result.isError : false,
              };
            })
        : [];
      if (!text && toolUses.length === 0) continue;
      messages.push({ role: 'assistant', text, toolUses });
    }
  }

  return messages;
}

function blockText(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter(b => b && b.type === 'text')
    .map(b => b.text || '')
    .join('');
}

function isSystemReminderOnly(text) {
  const trimmed = text.trim();
  return trimmed.startsWith('<system-reminder>') && trimmed.endsWith('</system-reminder>');
}

function formatRelativeTime(ts) {
  const now = Date.now();
  const diff = now - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const date = new Date(ts);
  return date.toLocaleDateString();
}

module.exports = {
  SessionManager,
  parseConversationMessages,
  resolveConfigDir,
  getSessionsDir,
};
