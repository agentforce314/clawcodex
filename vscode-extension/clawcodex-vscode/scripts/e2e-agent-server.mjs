#!/usr/bin/env node
/**
 * Live protocol e2e: drives the real `clawcodex agent-server --stdio` through
 * the extension's ProcessManager and asserts the wire contract the chat
 * depends on.
 *
 * Phases:
 *   1. init        — system/init handshake arrives
 *   2. rpc         — list_sessions control_request → correlated reply
 *   3. turn        — live mini-turn: stream deltas → assistant → result   [live]
 *   4. permission  — default-mode Write ask → control_request → deny → result [live]
 *   5. interrupt   — abort mid-turn → result/cancelled                    [live]
 *
 * Usage:
 *   node scripts/e2e-agent-server.mjs                  # full run (needs provider creds)
 *   node scripts/e2e-agent-server.mjs --protocol-only  # phases 1-2 only (no LLM calls)
 *
 * Env:
 *   CLAWCODEX_E2E_CMD    launcher override, e.g. "uv run python -m src.cli"
 *                        (multi-word commands are wrapped in a shim script)
 *   CLAWCODEX_E2E_MODEL  model override passed as --model
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
require(path.join(here, '..', 'test', 'register-vscode-stub.js'));
const { ProcessManager } = require(path.join(here, '..', 'src', 'chat', 'processManager.js'));

const PROTOCOL_ONLY = process.argv.includes('--protocol-only');
const MODEL = process.env.CLAWCODEX_E2E_MODEL || null;

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'clawcodex-vscode-e2e-'));
const cleanups = [() => fs.rmSync(tempRoot, { recursive: true, force: true })];

function resolveCommand() {
  const raw = (process.env.CLAWCODEX_E2E_CMD || 'clawcodex').trim();
  const words = raw.split(/\s+/);
  if (words.length === 1) return words[0];
  // ProcessManager spawns a single executable; wrap multi-word launchers.
  const shim = path.join(tempRoot, 'clawcodex-shim.sh');
  fs.writeFileSync(shim, `#!/bin/sh\nexec ${raw} "$@"\n`);
  fs.chmodSync(shim, 0o755);
  return shim;
}

function makeSession({ permissionMode, workspace }) {
  const pm = new ProcessManager({
    command: resolveCommand(),
    cwd: workspace,
    permissionMode,
    model: MODEL,
  });

  const state = {
    pm,
    events: [],
    errors: [],
    waiters: [],
  };
  pm.onMessage((msg) => {
    state.events.push(msg);
    for (const waiter of [...state.waiters]) {
      if (waiter.predicate(msg)) {
        state.waiters.splice(state.waiters.indexOf(waiter), 1);
        clearTimeout(waiter.timer);
        waiter.resolve(msg);
      }
    }
  });
  pm.onError((err) => state.errors.push(String(err.message || err)));
  pm.start();
  cleanups.push(() => pm.dispose());
  return state;
}

function waitFor(state, label, predicate, timeoutMs) {
  const already = state.events.find(predicate);
  if (already) return Promise.resolve(already);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      const idx = state.waiters.findIndex(w => w.resolve === resolve);
      if (idx >= 0) state.waiters.splice(idx, 1);
      reject(new Error(`timeout waiting for ${label} after ${timeoutMs}ms; saw types: ${state.events.map(e => e.type + (e.subtype ? '/' + e.subtype : '')).join(', ') || '(none)'}${state.errors.length ? `; stderr: ${state.errors.slice(-3).join(' | ')}` : ''}`));
    }, timeoutMs);
    state.waiters.push({ predicate, resolve, timer });
  });
}

let passed = 0;
function ok(label, detail = '') {
  passed += 1;
  console.log(`  ✔ ${label}${detail ? ` — ${detail}` : ''}`);
}

async function main() {
  const workspace = fs.mkdtempSync(path.join(tempRoot, 'ws-'));

  console.log(`Phase 1+2: handshake + RPC (workspace ${workspace})`);
  const proto = makeSession({ permissionMode: 'default', workspace });

  const init = await waitFor(proto, 'system/init', m => m.type === 'system' && m.subtype === 'init', 120000);
  if (!init.session_id) throw new Error('init carried no session_id');
  ok('system/init received', `session ${init.session_id}, model ${init.model}, mode ${init.permission_mode}`);
  if (init.permission_mode !== 'default') throw new Error(`expected permission_mode default, got ${init.permission_mode}`);
  ok('permission_mode honored at spawn');

  const sessions = await proto.pm.sendControlRequest('list_sessions', {}, { timeoutMs: 30000 });
  if (!Array.isArray(sessions.sessions)) throw new Error(`list_sessions reply malformed: ${JSON.stringify(sessions).slice(0, 200)}`);
  ok('list_sessions RPC round-trip', `${sessions.sessions.length} sessions`);

  if (PROTOCOL_ONLY) {
    proto.pm.dispose();
    console.log(`\nPROTOCOL-ONLY PASS (${passed} checks)`);
    return;
  }

  console.log('Phase 3: live mini-turn');
  proto.pm.sendUserMessage('Reply with exactly the word OK and nothing else. Do not use any tools.');
  const result1 = await waitFor(proto, 'result', m => m.type === 'result', 300000);
  if (result1.subtype !== 'success') throw new Error(`turn ended ${result1.subtype}: ${result1.error || result1.result}`);
  const sawDelta = proto.events.some(m => m.type === 'stream_event' && m.event?.type === 'content_block_delta');
  const assistant = proto.events.find(m => m.type === 'assistant');
  const text = assistant?.message?.content?.filter?.(b => b.type === 'text').map(b => b.text).join('') ?? '';
  if (!assistant) throw new Error('no assistant envelope before result');
  ok('live turn: deltas → assistant → result', `deltas=${sawDelta}, text=${JSON.stringify(text).slice(0, 40)}, usage=${JSON.stringify(result1.usage || {}).slice(0, 60)}`);

  console.log('Phase 4: permission round-trip (deny a Write in default mode)');
  proto.pm.sendUserMessage(
    'Use the Write tool to create a file named e2e_perm_test.txt in the workspace root containing the word hello. If the permission is denied, stop and reply DENIED.',
  );
  const permReq = await waitFor(
    proto,
    'control_request/can_use_tool',
    m => m.type === 'control_request' && m.request?.subtype === 'can_use_tool',
    300000,
  );
  ok('control_request/can_use_tool received', `tool ${permReq.request.tool_name}, suggestions=${(permReq.request.suggestions || []).length}, label=${JSON.stringify(permReq.request.session_label)}`);
  // Deny this and any retry asks for the rest of the turn.
  const denyAll = (m) => {
    if (m.type === 'control_request' && m.request?.subtype === 'can_use_tool') {
      proto.pm.sendControlResponse(m.request_id, { behavior: 'deny', message: 'User denied permission' });
    }
    return false;
  };
  proto.waiters.push({ predicate: denyAll, resolve: () => {}, timer: setTimeout(() => {}, 0) });
  proto.pm.sendControlResponse(permReq.request_id, { behavior: 'deny', message: 'User denied permission' });
  const result2 = await waitFor(
    proto,
    'result after deny',
    m => m.type === 'result' && proto.events.indexOf(m) > proto.events.indexOf(permReq),
    300000,
  );
  if (fs.existsSync(path.join(workspace, 'e2e_perm_test.txt'))) {
    throw new Error('denied Write still created the file');
  }
  ok('deny honored, turn completed', `result ${result2.subtype}, file not created`);

  console.log('Phase 5: interrupt mid-turn');
  proto.pm.sendUserMessage('Count from 1 to 500, one number per line. Do not use tools.');
  await waitFor(
    proto,
    'first delta of the long turn',
    m => m.type === 'stream_event' && m.event?.type === 'content_block_delta' && proto.events.indexOf(m) > proto.events.indexOf(result2),
    300000,
  );
  proto.pm.abort();
  const result3 = await waitFor(
    proto,
    'result/cancelled',
    m => m.type === 'result' && proto.events.indexOf(m) > proto.events.indexOf(result2),
    120000,
  );
  if (result3.subtype !== 'cancelled') throw new Error(`expected cancelled, got ${result3.subtype}`);
  ok('interrupt → result/cancelled');

  console.log('Phase 6: session usable after interrupt');
  proto.pm.sendUserMessage('Reply with exactly the word STILL-ALIVE and nothing else. Do not use tools.');
  const result4 = await waitFor(
    proto,
    'result after interrupt',
    m => m.type === 'result' && proto.events.indexOf(m) > proto.events.indexOf(result3),
    300000,
  );
  if (result4.subtype !== 'success') throw new Error(`post-interrupt turn ended ${result4.subtype}`);
  ok('post-interrupt turn completed', `result ${result4.subtype}`);

  proto.pm.dispose();
  console.log(`\nE2E PASS (${passed} checks)`);
}

main()
  .catch((err) => {
    console.error(`\nE2E FAIL after ${passed} checks: ${err.message}`);
    process.exitCode = 1;
  })
  .finally(() => {
    for (const fn of cleanups.reverse()) {
      try { fn(); } catch { /* best effort */ }
    }
  });
