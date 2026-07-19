const test = require('node:test');
const assert = require('node:assert/strict');
const {
  parseStdoutLine,
  serializeStdinMessage,
  buildUserMessage,
  buildControlResponse,
  buildControlRequest,
  isAssistantMessage,
  isStreamEvent,
  isContentBlockDelta,
  isResultMessage,
  isSystemInit,
  isSystemStatus,
  isPermissionRequest,
  isControlResponse,
  getControlResponseId,
  getControlResponsePayload,
  getTextContent,
  getToolUseBlocks,
} = require('./protocol');

test('parseStdoutLine parses NDJSON and rejects garbage', () => {
  assert.deepEqual(parseStdoutLine('{"type":"result"}'), { type: 'result' });
  assert.equal(parseStdoutLine('not json'), null);
  assert.equal(parseStdoutLine(''), null);
  assert.equal(parseStdoutLine('   '), null);
});

test('serializeStdinMessage appends a newline', () => {
  assert.equal(serializeStdinMessage({ a: 1 }), '{"a":1}\n');
});

test('buildUserMessage matches the agent-server inbound envelope', () => {
  assert.deepEqual(buildUserMessage('hello'), {
    type: 'user',
    message: { role: 'user', content: 'hello' },
    parent_tool_use_id: null,
  });
});

test('buildControlResponse wraps the result in the SDK envelope', () => {
  assert.deepEqual(buildControlResponse('req-1', { behavior: 'allow' }), {
    type: 'control_response',
    response: {
      subtype: 'success',
      request_id: 'req-1',
      response: { behavior: 'allow' },
    },
  });
});

test('buildControlRequest carries subtype, payload, and a unique request_id', () => {
  const first = buildControlRequest('resume', { session_id: 'abc' });
  const second = buildControlRequest('interrupt');

  assert.equal(first.type, 'control_request');
  assert.deepEqual(first.request, { subtype: 'resume', session_id: 'abc' });
  assert.ok(typeof first.request_id === 'string' && first.request_id.length > 0);
  assert.deepEqual(second.request, { subtype: 'interrupt' });
  assert.notEqual(first.request_id, second.request_id);
});

test('system guards distinguish init from status', () => {
  const init = { type: 'system', subtype: 'init', session_id: 's', model: 'm' };
  const status = { type: 'system', subtype: 'status', level: 'info', message: 'hi' };
  assert.ok(isSystemInit(init));
  assert.ok(!isSystemInit(status));
  assert.ok(isSystemStatus(status));
  assert.ok(!isSystemStatus(init));
});

test('stream event guards match content_block_delta envelopes', () => {
  const delta = {
    type: 'stream_event',
    session_id: 's',
    event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'hi' } },
  };
  assert.ok(isStreamEvent(delta));
  assert.ok(isContentBlockDelta(delta));
  assert.ok(!isStreamEvent({ type: 'stream_event' }));
});

test('permission request guard requires subtype can_use_tool', () => {
  const perm = {
    type: 'control_request',
    request_id: 'r1',
    request: { subtype: 'can_use_tool', tool_name: 'Write', input: {} },
  };
  const other = {
    type: 'control_request',
    request_id: 'r2',
    request: { subtype: 'something_else' },
  };
  assert.ok(isPermissionRequest(perm));
  assert.ok(!isPermissionRequest(other));
});

test('control response accessors correlate RPC replies', () => {
  const reply = {
    type: 'control_response',
    response: {
      subtype: 'success',
      request_id: 'vscode-1',
      response: { sessions: [{ session_id: 'a' }] },
    },
  };
  assert.ok(isControlResponse(reply));
  assert.equal(getControlResponseId(reply), 'vscode-1');
  assert.deepEqual(getControlResponsePayload(reply), { sessions: [{ session_id: 'a' }] });
  assert.equal(getControlResponseId({ type: 'control_response' }), null);
});

test('assistant and result guards plus content accessors', () => {
  const assistant = {
    type: 'assistant',
    session_id: 's',
    message: {
      role: 'assistant',
      content: [
        { type: 'text', text: 'Let me edit that.' },
        { type: 'tool_use', id: 'tu1', name: 'Edit', input: { file_path: '/a.txt' } },
      ],
    },
  };
  assert.ok(isAssistantMessage(assistant));
  assert.equal(getTextContent(assistant.message), 'Let me edit that.');
  assert.deepEqual(getToolUseBlocks(assistant.message).map(b => b.id), ['tu1']);
  assert.equal(getTextContent({ content: 'plain string' }), 'plain string');

  assert.ok(isResultMessage({ type: 'result', subtype: 'success' }));
  assert.ok(!isResultMessage(assistant));
});
