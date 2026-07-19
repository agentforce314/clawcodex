const test = require('node:test');
const assert = require('node:assert/strict');

// `vscode` resolves to test/vscode-stub.js via the --require preload; its
// EventEmitter is functional, which these state-machine tests depend on.
const { ChatController } = require('./chatProvider');

function makeController() {
  const controller = new ChatController(null);
  const broadcasts = [];
  controller.registerWebview({ postMessage: (m) => broadcasts.push(m) });
  const states = [];
  controller.onDidChangeState((s) => states.push(s));
  return { controller, broadcasts, states };
}

const delta = (text) => ({
  type: 'stream_event',
  session_id: 's1',
  event: { type: 'content_block_delta', delta: { type: 'text_delta', text } },
});

const thinking = (text) => ({
  type: 'stream_event',
  session_id: 's1',
  event: { type: 'content_block_delta', delta: { type: 'thinking_delta', thinking: text } },
});

const result = (overrides = {}) => ({
  type: 'result',
  subtype: 'success',
  session_id: 's1',
  num_turns: 1,
  result: '',
  is_error: false,
  ...overrides,
});

test('first text delta starts the stream once and accumulates; result finalizes and goes idle', () => {
  const { controller, broadcasts, states } = makeController();

  controller._handleMessage(delta('Hel'));
  controller._handleMessage(delta('lo'));
  controller._handleMessage(result({ usage: { input_tokens: 3, output_tokens: 4 } }));

  const types = broadcasts.map(b => b.type);
  assert.deepEqual(types.filter(t => t === 'stream_start'), ['stream_start'], 'stream_start fires exactly once');
  const deltas = broadcasts.filter(b => b.type === 'stream_delta').map(b => b.text);
  assert.deepEqual(deltas, ['Hel', 'Hello'], 'deltas accumulate');
  const end = broadcasts.find(b => b.type === 'stream_end' && b.final);
  assert.equal(end.text, 'Hello');
  assert.deepEqual(end.usage, { input_tokens: 3, output_tokens: 4 });
  assert.deepEqual(states, ['streaming', 'idle'], 'status-bar spinner engages on first delta, idles on result');
  const status = broadcasts.filter(b => b.type === 'status').map(b => b.content);
  assert.ok(status.includes('Ready'), `num_turns=1 → Ready (saw ${status})`);
});

test('thinking deltas show the thinking block and text hides it', () => {
  const { controller, broadcasts } = makeController();

  controller._handleMessage(thinking('hmm'));
  controller._handleMessage(thinking('mmm'));
  controller._handleMessage(delta('Answer'));
  controller._handleMessage(result());

  const types = broadcasts.map(b => b.type);
  assert.deepEqual(types.filter(t => t === 'thinking_start'), ['thinking_start'], 'thinking_start fires once');
  const thinkingEndIdx = types.indexOf('thinking_end');
  const firstDeltaIdx = types.indexOf('stream_delta');
  assert.ok(thinkingEndIdx >= 0 && thinkingEndIdx < firstDeltaIdx, 'thinking hides before text renders');
  const meta = broadcasts.filter(b => b.type === 'thinking_delta');
  assert.equal(meta[meta.length - 1].tokens, 6, 'token estimate accumulates thinking length');
});

test('thinking still open at turn end is closed by the result', () => {
  const { controller, broadcasts } = makeController();

  controller._handleMessage(thinking('all thinking, no text'));
  controller._handleMessage(result());

  const types = broadcasts.map(b => b.type);
  assert.ok(types.indexOf('thinking_end') > types.indexOf('thinking_start'));
  assert.ok(types.indexOf('thinking_end') < types.indexOf('stream_end'));
});

test('assistant envelope emits tool cards before the bubble-finalizing stream_end', () => {
  const { controller, broadcasts } = makeController();

  controller._handleMessage({
    type: 'assistant',
    session_id: 's1',
    message: {
      role: 'assistant',
      content: [
        { type: 'text', text: 'Editing now.' },
        { type: 'tool_use', id: 'tu1', name: 'Edit', input: { file_path: '/a.py', old_string: 'x', new_string: 'y' } },
      ],
    },
  });

  const types = broadcasts.map(b => b.type);
  const toolIdx = types.indexOf('tool_use');
  const endIdx = types.indexOf('stream_end');
  assert.ok(toolIdx >= 0 && toolIdx < endIdx, 'tool cards land in the same bubble as the text');
  const tool = broadcasts[toolIdx].toolUse;
  assert.equal(tool.id, 'tu1');
  assert.equal(tool.displayName, 'Edit File');
  assert.equal(tool.status, 'running');
  const end = broadcasts[endIdx];
  assert.equal(end.text, 'Editing now.');
  assert.equal(end.final, false, 'assistant envelope is mid-turn; result finalizes');
  assert.deepEqual(controller.getMessages().at(-1).toolUses.map(t => t.id), ['tu1']);
});

test('user tool_result envelope maps to tool_result broadcasts', () => {
  const { controller, broadcasts } = makeController();

  controller._handleMessage({
    type: 'user',
    session_id: 's1',
    message: {
      role: 'user',
      content: [
        { type: 'tool_result', tool_use_id: 'tu1', content: 'file edited', is_error: false },
        { type: 'tool_result', tool_use_id: 'tu2', content: [{ type: 'text', text: 'boom' }], is_error: true },
      ],
    },
  });

  const results = broadcasts.filter(b => b.type === 'tool_result');
  assert.deepEqual(results.map(r => [r.toolUseId, r.content, r.isError]), [
    ['tu1', 'file edited', false],
    ['tu2', 'boom', true],
  ]);
  assert.ok(broadcasts.some(b => b.type === 'status' && b.content === 'Thinking...'));
});

test('permission request broadcasts card fields and the reply echoes suggestions as chosen_updates', () => {
  const { controller, broadcasts } = makeController();
  const sent = [];
  controller._process = { sendControlResponse: (id, res) => sent.push([id, res]) };

  const suggestions = [{ type: 'setMode', mode: 'acceptEdits', destination: 'session' }];
  controller._handleMessage({
    type: 'control_request',
    request_id: 'req-1',
    request: {
      subtype: 'can_use_tool',
      tool_name: 'Write',
      input: { file_path: '/x.txt', content: 'hi' },
      tool_use_id: null,
      suggestions,
      session_label: 'allow all edits during this session',
      warning: null,
    },
  });

  const card = broadcasts.find(b => b.type === 'permission_request');
  assert.equal(card.requestId, 'req-1');
  assert.equal(card.displayName, 'Write File');
  assert.equal(card.sessionLabel, 'allow all edits during this session');
  assert.equal(card.hasSuggestions, true);
  assert.equal(card.inputPreview, '/x.txt');

  controller.sendPermissionResponse('req-1', 'allow-session', null);
  assert.equal(sent.length, 1);
  const [requestId, reply] = sent[0];
  assert.equal(requestId, 'req-1');
  assert.equal(reply.behavior, 'allow');
  assert.deepEqual(reply.updatedInput, { file_path: '/x.txt', content: 'hi' });
  assert.deepEqual(reply.chosen_updates, suggestions);
});

test('deny reply carries the deny behavior and clears the pending entry', () => {
  const { controller } = makeController();
  const sent = [];
  controller._process = { sendControlResponse: (id, res) => sent.push([id, res]) };

  controller._handleMessage({
    type: 'control_request',
    request_id: 'req-2',
    request: { subtype: 'can_use_tool', tool_name: 'Bash', input: { command: 'rm -rf /tmp/x' }, suggestions: [] },
  });
  controller.sendPermissionResponse('req-2', 'deny', null);
  controller.sendPermissionResponse('req-2', 'deny', null); // pending already consumed

  assert.equal(sent[0][1].behavior, 'deny');
  assert.equal(sent.length, 2, 'second reply still sends (server ignores unknown ids)');
  assert.equal(controller._pendingPermissions.size, 0);
});

test('non-permission control_requests and unknown system subtypes are ignored', () => {
  const { controller, broadcasts } = makeController();

  controller._handleMessage({ type: 'control_request', request_id: 'r', request: { subtype: 'something_else' } });
  controller._handleMessage({ type: 'system', subtype: 'goal_status', session_id: 's1', message: 'goal carrier' });
  controller._handleMessage({ type: 'agent_progress', session_id: 's1' });

  assert.deepEqual(broadcasts, []);
});

test('system init and status frames map to system_info / status / error', () => {
  const { controller, broadcasts } = makeController();

  controller._handleMessage({ type: 'system', subtype: 'init', session_id: 'ds_1', model: 'claude-opus-4-6', permission_mode: 'default' });
  controller._handleMessage({ type: 'system', subtype: 'status', session_id: 'ds_1', level: 'info', message: 'saved' });
  controller._handleMessage({ type: 'system', subtype: 'status', session_id: 'ds_1', level: 'info', permission_mode: 'acceptEdits' });
  controller._handleMessage({ type: 'system', subtype: 'status', session_id: 'ds_1', level: 'error', message: 'provider down' });

  assert.deepEqual(broadcasts.map(b => b.type), ['system_info', 'status', 'error']);
  assert.equal(broadcasts[0].model, 'claude-opus-4-6');
  assert.equal(broadcasts[0].sessionId, 'ds_1');
  assert.equal(controller.sessionId, 'ds_1', 'first session_id is captured');
  assert.equal(broadcasts[1].content, 'saved');
  assert.equal(broadcasts[2].message, 'provider down');
});

test('result error and cancelled subtypes surface distinctly and reset streaming', () => {
  const { controller, broadcasts, states } = makeController();

  controller._handleMessage(delta('partial'));
  controller._handleMessage(result({ subtype: 'error', is_error: true, error: 'boom' }));
  assert.ok(broadcasts.some(b => b.type === 'error' && b.message === 'boom'));

  broadcasts.length = 0;
  controller._handleMessage(delta('again'));
  controller._handleMessage(result({ subtype: 'cancelled', num_turns: 0 }));
  assert.ok(broadcasts.some(b => b.type === 'status' && b.content === 'Interrupted'));
  assert.deepEqual(states, ['streaming', 'idle', 'streaming', 'idle']);
});

test('result session_id tracks the LIVE server session across resumes', () => {
  const { controller } = makeController();
  controller._currentSessionId = 'old-picked-session';

  controller._handleMessage(result({ session_id: 'new-live-session' }));

  assert.equal(controller.sessionId, 'new-live-session');
});
