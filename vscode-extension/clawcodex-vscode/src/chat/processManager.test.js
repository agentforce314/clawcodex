const test = require('node:test');
const assert = require('node:assert/strict');

const { ProcessManager, STDERR_SEVERE_RE } = require('./processManager');

test('buildArgs spawns the agent-server with workspace and permission mode', () => {
  const pm = new ProcessManager({
    command: 'clawcodex',
    cwd: '/workspace/project',
    permissionMode: 'acceptEdits',
  });

  assert.deepEqual(pm.buildArgs(), [
    'agent-server',
    '--stdio',
    '--permission-mode', 'acceptEdits',
    '--workspace', '/workspace/project',
  ]);
});

test('buildArgs forwards provider, model, and extra args', () => {
  const pm = new ProcessManager({
    command: 'clawcodex',
    cwd: '/w',
    permissionMode: 'plan',
    provider: 'deepseek',
    model: 'deepseek-chat',
    extraArgs: ['--max-turns', '10'],
  });

  assert.deepEqual(pm.buildArgs(), [
    'agent-server',
    '--stdio',
    '--permission-mode', 'plan',
    '--workspace', '/w',
    '--provider', 'deepseek',
    '--model', 'deepseek-chat',
    '--max-turns', '10',
  ]);
});

test('stderr severity filter matches Python failure shapes and skips log noise', () => {
  for (const line of [
    'Traceback (most recent call last):',
    "ValueError: bad thing",
    'KeyError: missing',
    'RuntimeError: exploded',
    'CRITICAL something broke',
    'fatal: repository not found',
  ]) {
    assert.ok(STDERR_SEVERE_RE.test(line), `expected severe: ${line}`);
  }
  for (const line of [
    'INFO [agent-server] session started',
    'DEBUG loading tool registry',
    'listening for input',
  ]) {
    assert.ok(!STDERR_SEVERE_RE.test(line), `expected quiet: ${line}`);
  }
});

function startEchoServer(pm, script) {
  // A tiny stand-in agent-server: reads NDJSON lines from stdin and answers
  // per the script function, exercising the real spawn + framing path.
  pm._command = process.execPath;
  pm.buildArgs = () => ['-e', script];
  pm.start();
}

const RPC_ECHO_SCRIPT = `
  let buffer = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => {
    buffer += chunk;
    const lines = buffer.split('\\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (!line.trim()) continue;
      const msg = JSON.parse(line);
      if (msg.type === 'control_request' && msg.request.subtype === 'list_sessions') {
        process.stdout.write(JSON.stringify({
          type: 'control_response',
          response: {
            subtype: 'success',
            request_id: msg.request_id,
            response: { sessions: [{ session_id: 'fake' }] },
          },
        }) + '\\n');
      }
      if (msg.type === 'control_request' && msg.request.subtype === 'interrupt') {
        process.stdout.write(JSON.stringify({ type: 'result', subtype: 'cancelled', session_id: 's1', num_turns: 0, result: '' }) + '\\n');
      }
      if (msg.type === 'user') {
        process.stdout.write(JSON.stringify({ type: 'system', subtype: 'init', session_id: 's1' }) + '\\n');
      }
    }
  });
`;

test('sendControlRequest round-trips an RPC against a fake server', async () => {
  const pm = new ProcessManager({ command: 'unused' });
  startEchoServer(pm, RPC_ECHO_SCRIPT);

  try {
    const reply = await pm.sendControlRequest('list_sessions', {}, { timeoutMs: 5000 });
    assert.deepEqual(reply, { sessions: [{ session_id: 'fake' }] });
  } finally {
    pm.dispose();
  }
});

test('non-RPC messages still reach onMessage while RPC replies are consumed', async () => {
  const pm = new ProcessManager({ command: 'unused' });
  const seen = [];
  pm.onMessage(msg => seen.push(msg.type));
  startEchoServer(pm, RPC_ECHO_SCRIPT);

  try {
    pm.sendUserMessage('hello');
    // interrupt: fire-and-forget control that triggers a result frame
    pm.abort();
    await new Promise(resolve => setTimeout(resolve, 300));
    const rpc = await pm.sendControlRequest('list_sessions', {}, { timeoutMs: 5000 });
    assert.ok(rpc.sessions);
    assert.ok(seen.includes('system'), `system init should reach onMessage (saw ${seen})`);
    assert.ok(seen.includes('result'), `result should reach onMessage (saw ${seen})`);
    assert.ok(!seen.includes('control_response'), 'RPC replies must be consumed, not forwarded');
    assert.equal(pm.sessionId, 's1');
  } finally {
    pm.dispose();
  }
});

test('sendControlRequest rejects on timeout and on process exit', async () => {
  const pm = new ProcessManager({ command: 'unused' });
  startEchoServer(pm, 'setInterval(() => {}, 1000);'); // never answers

  await assert.rejects(
    pm.sendControlRequest('resume', { session_id: 'x' }, { timeoutMs: 100 }),
    /timed out/,
  );

  const pending = pm.sendControlRequest('resume', { session_id: 'y' }, { timeoutMs: 60000 });
  const rejection = assert.rejects(pending, /exited|disposed/);
  pm.dispose();
  await rejection;
});

test('write throws when the process is not running', () => {
  const pm = new ProcessManager({ command: 'unused' });
  assert.throws(() => pm.sendUserMessage('hi'), /not running/);
  pm.dispose();
});
