// Systematic feature tests: render the Ink App over a FakeTransport (the Direct
// Connect wire), drive input/messages, assert on the rendered frame (lastFrame =
// the exact terminal output). Run: npm test.
import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { render } from 'ink-testing-library'
import { App } from '../dist/App.js'

const INIT = JSON.stringify({ type: 'system', subtype: 'init', session_id: 's', protocol_version: '0.1.0', model: 'deepseek-v4-pro', permission_mode: 'default', tools: ['Bash', 'Edit'] }) + '\n'
const strip = (s) => (s || '').replace(/\x1b\[[0-9;]*m/g, '')
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

let _active = null
afterEach(() => { if (_active) { try { _active.unmount() } catch {} _active = null } })
function mount(onSend) {
  let cb
  const transport = { async start(c) { cb = c; c.onOpen && c.onOpen(); c.onData(INIT) }, send(d) { onSend && onSend(d, cb) }, close() {} }
  const r = render(React.createElement(App, { transport, serverLabel: 'test' }))
  _active = r
  return { ...r, cb: () => cb, type: async (s) => { for (const ch of s) { r.stdin.write(ch); await wait(5) } } }
}

test('startup banner renders the CLAWCODEX logo', async () => {
  const { lastFrame } = mount()
  await wait(160)
  assert.match(strip(lastFrame()), /█|clawcodex/)
})

test('slash menu lists commands after /', async () => {
  const { type, lastFrame } = mount()
  await wait(150); await type('/'); await wait(60)
  assert.match(strip(lastFrame()), /\/help|\/model|\/mcp/)
})

test('@ mention shows the file dropdown', async () => {
  const { type, lastFrame } = mount()
  await wait(150); await type('@src'); await wait(120)
  assert.match(strip(lastFrame()), /src\//)
})

test('Bash permission flags a destructive command', async () => {
  const { cb, lastFrame } = mount()
  await wait(150)
  cb().onData(JSON.stringify({ type: 'control_request', request_id: 'p', request: { subtype: 'can_use_tool', tool_name: 'Bash', input: { command: 'rm -rf /tmp/x' } } }) + '\n')
  await wait(100)
  assert.match(strip(lastFrame()), /looks destructive/)
})

test('MCP elicitation shows a form and accepts input', async () => {
  const responses = []
  const { cb, stdin, lastFrame } = mount((d, c) => { try { const m = JSON.parse(d); if (m.type === 'control_response') responses.push(m.response?.response) } catch {} })
  await wait(150)
  cb().onData(JSON.stringify({ type: 'control_request', request_id: 'e', request: { subtype: 'mcp_elicitation', params: { message: 'Name?', requestedSchema: { properties: { name: {} } } } } }) + '\n')
  await wait(80)
  assert.match(strip(lastFrame()), /MCP server requests input/)
  for (const ch of 'Ada') { stdin.write(ch); await wait(5) }
  stdin.write('\r'); await wait(60)
  assert.deepEqual(responses[0], { action: 'accept', content: { name: 'Ada' } })
})

test('live thinking streams a dim buffer', async () => {
  const { cb, lastFrame } = mount()
  await wait(150)
  cb().onData(JSON.stringify({ type: 'stream_event', session_id: 's', event: { type: 'content_block_delta', delta: { type: 'thinking_delta', thinking: 'reasoning here' } } }) + '\n')
  await wait(100)
  assert.match(strip(lastFrame()), /∴.*reasoning here/s)
})

test('queue priorities: next: jumps the queue', async () => {
  const { type, stdin, lastFrame } = mount()
  await wait(150)
  await type('firstturn'); stdin.write('\r'); await wait(50)
  await type('ZZlater'); stdin.write('\r'); await wait(40)
  await type('next: YYfront'); stdin.write('\r'); await wait(50)
  const f = strip(lastFrame())
  assert.ok(f.includes('YYfront') && f.includes('ZZlater') && f.indexOf('YYfront') < f.indexOf('ZZlater'))
})

test('RTL shaping reverses Hebrew runs', async () => {
  const heb = 'שלום'
  const { type, stdin, cb, lastFrame } = mount()
  await wait(150)
  await type('/rtl'); stdin.write('\r'); await wait(60)
  cb().onData(JSON.stringify({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'hi ' + heb + ' x' }] } }) + '\n')
  cb().onData(JSON.stringify({ type: 'result', subtype: 'success', session_id: 's', num_turns: 1, usage: {} }) + '\n')
  await wait(100)
  assert.match(strip(lastFrame()), new RegExp([...heb].reverse().join('')))
})

test('MCP multiselect toggles a server', async () => {
  let toggled = null
  const { type, stdin, lastFrame } = mount((d, c) => {
    try { const m = JSON.parse(d)
      if (m.request?.subtype === 'list_mcp') c.onData(JSON.stringify({ type: 'control_response', response: { request_id: m.request_id, subtype: 'success', response: { servers: [{ name: 'fs', enabled: true, tools: ['read'] }] } } }) + '\n')
      if (m.request?.subtype === 'set_mcp_enabled') { toggled = m.request.enabled; c.onData(JSON.stringify({ type: 'control_response', response: { request_id: m.request_id, subtype: 'success', response: { ok: true } } }) + '\n') }
    } catch {}
  })
  await wait(150); await type('/mcp'); stdin.write('\r'); await wait(100)
  assert.match(strip(lastFrame()), /✓ fs/)
  stdin.write(' '); await wait(60)
  assert.equal(toggled, false)
})

test('bypassPermissions requires confirmation', async () => {
  let modeSet = null
  const { type, stdin, lastFrame } = mount((d) => { try { const m = JSON.parse(d); if (m.request?.subtype === 'set_permission_mode') modeSet = m.request.mode } catch {} })
  await wait(150); await type('/mode bypassPermissions'); stdin.write('\r'); await wait(80)
  assert.match(strip(lastFrame()), /Enable bypass-permissions/)
  assert.equal(modeSet, null) // not set until confirmed
})

test('normal typing reaches the prompt', async () => {
  const { type, lastFrame } = mount()
  await wait(150); await type('hello world')
  await wait(40)
  assert.match(strip(lastFrame()), /hello world/)
})
