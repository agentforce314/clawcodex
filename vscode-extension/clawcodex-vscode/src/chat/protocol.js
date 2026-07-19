/**
 * NDJSON protocol helpers and message type constants for the clawcodex
 * agent-server stream wire format.
 *
 * The extension spawns `clawcodex agent-server --stdio --workspace <cwd>` and
 * speaks NDJSON over stdin/stdout. Outbound (client → server): `user` message
 * envelopes, `control_response` (permission replies), and `control_request`
 * RPCs (interrupt / resume / list_sessions — replied to with an inbound
 * `control_response` correlated by request_id). Inbound (server → client):
 * `system` (init + status), `assistant` / `user` SDK envelopes, `stream_event`
 * (Anthropic-style content_block_delta with text_delta | thinking_delta),
 * `control_request` (subtype can_use_tool — permission prompts), and a
 * per-turn terminal `result`.
 *
 * This module provides lightweight parsing, serialization, and type guards so
 * the rest of the extension never touches raw JSON strings.
 */

const MESSAGE_TYPES = {
  ASSISTANT: 'assistant',
  USER: 'user',
  RESULT: 'result',
  SYSTEM: 'system',
  STREAM_EVENT: 'stream_event',
  CONTROL_REQUEST: 'control_request',
  CONTROL_RESPONSE: 'control_response',
  AGENT_PROGRESS: 'agent_progress',
};

let requestCounter = 0;

function nextRequestId() {
  requestCounter += 1;
  return `vscode-${Date.now().toString(36)}-${requestCounter}`;
}

function parseStdoutLine(line) {
  const trimmed = (line || '').trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function serializeStdinMessage(msg) {
  return JSON.stringify(msg) + '\n';
}

function buildUserMessage(text) {
  return {
    type: 'user',
    message: {
      role: 'user',
      content: text,
    },
    parent_tool_use_id: null,
  };
}

function buildControlResponse(requestId, result) {
  return {
    type: 'control_response',
    response: {
      subtype: 'success',
      request_id: requestId,
      response: result || {},
    },
  };
}

/**
 * Client → server RPC envelope. The server replies with a `control_response`
 * whose `response.request_id` matches (except fire-and-forget subtypes like
 * `interrupt`, which send no reply).
 */
function buildControlRequest(subtype, payload, requestId) {
  return {
    type: 'control_request',
    request_id: requestId || nextRequestId(),
    request: { subtype, ...(payload || {}) },
  };
}

function isAssistantMessage(msg) {
  return msg && msg.type === MESSAGE_TYPES.ASSISTANT;
}

function isStreamEvent(msg) {
  return Boolean(msg && msg.type === MESSAGE_TYPES.STREAM_EVENT && msg.event);
}

function isContentBlockDelta(msg) {
  return isStreamEvent(msg) && msg.event.type === 'content_block_delta';
}

function isResultMessage(msg) {
  return msg && msg.type === MESSAGE_TYPES.RESULT;
}

function isSystemInit(msg) {
  return msg && msg.type === MESSAGE_TYPES.SYSTEM && msg.subtype === 'init';
}

function isSystemStatus(msg) {
  return msg && msg.type === MESSAGE_TYPES.SYSTEM && msg.subtype === 'status';
}

/** Server → client permission ask (`request.subtype === 'can_use_tool'`). */
function isControlRequest(msg) {
  return msg && msg.type === MESSAGE_TYPES.CONTROL_REQUEST;
}

function isPermissionRequest(msg) {
  return (
    isControlRequest(msg) &&
    msg.request &&
    typeof msg.request === 'object' &&
    msg.request.subtype === 'can_use_tool'
  );
}

/** Server → client reply to a client-initiated control_request RPC. */
function isControlResponse(msg) {
  return msg && msg.type === MESSAGE_TYPES.CONTROL_RESPONSE;
}

function getControlResponseId(msg) {
  if (!isControlResponse(msg)) return null;
  const response = msg.response;
  if (!response || typeof response !== 'object') return null;
  return typeof response.request_id === 'string' ? response.request_id : null;
}

function getControlResponsePayload(msg) {
  if (!isControlResponse(msg)) return null;
  const response = msg.response;
  if (!response || typeof response !== 'object') return null;
  const inner = response.response;
  return inner && typeof inner === 'object' ? inner : {};
}

function isToolUse(block) {
  return block && block.type === 'tool_use';
}

function isTextBlock(block) {
  return block && block.type === 'text';
}

function getTextContent(message) {
  if (!message) return '';
  if (typeof message.content === 'string') return message.content;
  if (!Array.isArray(message.content)) return '';
  return message.content
    .filter(b => b && b.type === 'text')
    .map(b => b.text || '')
    .join('');
}

function getToolUseBlocks(message) {
  if (!message || !Array.isArray(message.content)) return [];
  return message.content.filter(b => b && b.type === 'tool_use');
}

module.exports = {
  MESSAGE_TYPES,
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
  isControlRequest,
  isPermissionRequest,
  isControlResponse,
  getControlResponseId,
  getControlResponsePayload,
  isToolUse,
  isTextBlock,
  getTextContent,
  getToolUseBlocks,
};
