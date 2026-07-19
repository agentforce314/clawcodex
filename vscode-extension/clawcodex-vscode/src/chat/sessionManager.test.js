const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { SessionManager, parseConversationMessages, getSessionsDir } = require('./sessionManager');

function withTempConfigDir(t) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'clawcodex-sessions-'));
  const previous = process.env.CLAWCODEX_CONFIG_DIR;
  process.env.CLAWCODEX_CONFIG_DIR = tempDir;
  fs.mkdirSync(path.join(tempDir, 'sessions'), { recursive: true });
  t.after(() => {
    if (previous === undefined) delete process.env.CLAWCODEX_CONFIG_DIR;
    else process.env.CLAWCODEX_CONFIG_DIR = previous;
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  return tempDir;
}

function writeSession(tempDir, id, doc) {
  fs.writeFileSync(
    path.join(tempDir, 'sessions', `${id}.json`),
    JSON.stringify({ session_id: id, ...doc }),
  );
}

test('getSessionsDir honors CLAWCODEX_CONFIG_DIR', t => {
  const tempDir = withTempConfigDir(t);
  assert.equal(getSessionsDir(), path.join(tempDir, 'sessions'));
});

test('listSessions reads the flat clawcodex format newest-first with null name coerced', async t => {
  const tempDir = withTempConfigDir(t);
  writeSession(tempDir, 'older', {
    updated_at: 1784000000.5,
    preview: 'first prompt',
    name: null,
    message_count: 2,
    model: 'claude-opus-4-6',
    cwd: '/some/project',
  });
  writeSession(tempDir, 'newer', {
    updated_at: 1784000100.25,
    preview: 'second prompt',
    name: 'named session',
    message_count: 4,
    model: 'deepseek-chat',
    cwd: '/some/project',
  });

  const manager = new SessionManager();
  const sessions = await manager.listSessions();

  assert.deepEqual(sessions.map(s => s.id), ['newer', 'older']);
  assert.equal(sessions[0].title, 'named session');
  assert.equal(sessions[1].title, 'first prompt');
  // updated_at (epoch seconds) is converted to ms for display grouping.
  assert.equal(sessions[0].timestamp, 1784000100.25 * 1000);
});

test('listSessions filters by workspace with symlink-tolerant comparison', async t => {
  const tempDir = withTempConfigDir(t);
  const realWorkspace = fs.mkdtempSync(path.join(os.tmpdir(), 'clawcodex-ws-'));
  const linkPath = path.join(tempDir, 'ws-link');
  fs.symlinkSync(realWorkspace, linkPath);
  t.after(() => fs.rmSync(realWorkspace, { recursive: true, force: true }));

  // Stored cwd is the RESOLVED path (as the agent-server writes it) while
  // the workspace handed to the extension is the symlink.
  writeSession(tempDir, 'mine', {
    updated_at: 1784000200,
    preview: 'in this workspace',
    cwd: fs.realpathSync.native(realWorkspace),
    conversation: { messages: [] },
  });
  writeSession(tempDir, 'other', {
    updated_at: 1784000300,
    preview: 'elsewhere',
    cwd: '/entirely/different/project',
    conversation: { messages: [] },
  });

  const manager = new SessionManager();
  manager.setCwd(linkPath);
  const sessions = await manager.listSessions();

  assert.deepEqual(sessions.map(s => s.id), ['mine']);
});

test('listSessions finds workspace sessions even when many other projects are newer', async t => {
  // Regression: with a flat sessions/ dir, an input-side scan cap could push
  // every session of the current workspace out of the scanned window when
  // other projects were touched more recently — Resume silently showed
  // nothing. The scan is unbounded (server parity); only output is capped.
  const tempDir = withTempConfigDir(t);
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'clawcodex-busy-'));
  t.after(() => fs.rmSync(workspace, { recursive: true, force: true }));

  // Written FIRST so it is the oldest by mtime AND updated_at, buried under
  // >200 newer files — the exact shape that emptied Resume under an
  // input-side scan cap.
  writeSession(tempDir, 'mine-old', {
    updated_at: 1784000000,
    preview: 'the one that matters',
    cwd: fs.realpathSync.native(workspace),
  });
  for (let i = 0; i < 210; i++) {
    writeSession(tempDir, `noise-${i}`, {
      updated_at: 1784100000 + i,
      preview: `other project ${i}`,
      cwd: `/other/project-${i}`,
    });
  }

  const manager = new SessionManager();
  manager.setCwd(workspace);
  const sessions = await manager.listSessions();

  assert.deepEqual(sessions.map(s => s.id), ['mine-old']);
});

test('listSessions keeps an honest empty list for a fresh resolvable workspace', async t => {
  const tempDir = withTempConfigDir(t);
  writeSession(tempDir, 'other', {
    updated_at: 1784000300,
    preview: 'elsewhere',
    cwd: '/entirely/different/project',
  });
  const freshWorkspace = fs.mkdtempSync(path.join(os.tmpdir(), 'clawcodex-fresh-'));
  t.after(() => fs.rmSync(freshWorkspace, { recursive: true, force: true }));

  const manager = new SessionManager();
  manager.setCwd(freshWorkspace);
  assert.deepEqual(await manager.listSessions(), []);
});

test('listSessions falls back to all sessions when the workspace cannot be resolved', async t => {
  const tempDir = withTempConfigDir(t);
  writeSession(tempDir, 'any', {
    updated_at: 1784000300,
    preview: 'somewhere',
    cwd: '/entirely/different/project',
  });

  const manager = new SessionManager();
  manager.setCwd(path.join(tempDir, 'does-not-exist'));
  const sessions = await manager.listSessions();
  assert.deepEqual(sessions.map(s => s.id), ['any']);
});

test('loadSession pairs tool uses with results and drops reminder-only messages', async t => {
  const tempDir = withTempConfigDir(t);
  writeSession(tempDir, 'convo', {
    updated_at: 1784000400,
    preview: 'fix the bug',
    cwd: '/p',
    conversation: {
      messages: [
        { role: 'user', content: 'fix the bug' },
        {
          role: 'user',
          content: '<system-reminder>\ninjected context\n</system-reminder>',
        },
        {
          role: 'assistant',
          content: [
            { type: 'text', text: 'Editing now.' },
            { type: 'tool_use', id: 'tu1', name: 'Edit', input: { file_path: '/p/a.py' } },
          ],
        },
        {
          role: 'user',
          content: [
            { type: 'tool_result', tool_use_id: 'tu1', content: 'ok', is_error: false },
          ],
        },
        { role: 'assistant', content: [{ type: 'text', text: 'Done.' }] },
      ],
    },
  });

  const manager = new SessionManager();
  const messages = await manager.loadSession('convo');

  assert.deepEqual(messages, [
    { role: 'user', text: 'fix the bug' },
    {
      role: 'assistant',
      text: 'Editing now.',
      toolUses: [
        {
          id: 'tu1',
          name: 'Edit',
          input: { file_path: '/p/a.py' },
          status: 'complete',
          result: 'ok',
          isError: false,
        },
      ],
    },
    { role: 'assistant', text: 'Done.', toolUses: [] },
  ]);
});

test('loadSession returns null for unknown or path-traversal ids', async t => {
  withTempConfigDir(t);
  const manager = new SessionManager();
  assert.equal(await manager.loadSession('missing'), null);
  assert.equal(await manager.loadSession(''), null);
  assert.equal(await manager.loadSession(null), null);
  assert.equal(await manager.loadSession('../escape'), null);
  assert.equal(await manager.loadSession('a/b'), null);
});

test('parseConversationMessages tolerates string content and error results', () => {
  const messages = parseConversationMessages([
    { role: 'user', content: 'run it' },
    {
      role: 'assistant',
      content: [{ type: 'tool_use', id: 'tu9', name: 'Bash', input: { command: 'false' } }],
    },
    {
      role: 'user',
      content: [{ type: 'tool_result', tool_use_id: 'tu9', content: [{ type: 'text', text: 'boom' }], is_error: true }],
    },
  ]);

  assert.equal(messages.length, 2);
  assert.equal(messages[1].toolUses[0].isError, true);
  assert.equal(messages[1].toolUses[0].result, 'boom');
  assert.equal(messages[1].toolUses[0].status, 'error');
});
