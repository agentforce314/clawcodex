// Systematic feature tests: render the Ink App over a FakeTransport (the Direct
// Connect wire), drive input/messages, assert on the rendered frame (lastFrame =
// the exact terminal output). Run: npm test.
import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { render } from './ink-testing-shim.mjs'
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

const thinkingDelta = (t) => JSON.stringify({ type: 'stream_event', session_id: 's', event: { type: 'content_block_delta', delta: { type: 'thinking_delta', thinking: t } } }) + '\n'
const textDelta = (t) => JSON.stringify({ type: 'stream_event', session_id: 's', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: t } } }) + '\n'

test('live thinking shows a compact indicator, not the reasoning text', async () => {
  // The original shows a "thinking" status, not the live reasoning. We mirror that:
  // a static "∴ Thinking…" indicator, with the text never dumped to the screen.
  const { cb, lastFrame } = mount()
  await wait(150)
  cb().onData(thinkingDelta('secret reasoning steps'))
  await wait(120)
  const f = strip(lastFrame())
  assert.match(f, /∴\s*Thinking…/s)
  assert.doesNotMatch(f, /secret reasoning steps/)
})

test('reasoning commits collapsed (∴ Thinking, ctrl+o to expand)', async () => {
  // When the answer starts, the reasoning is committed as a COLLAPSED entry —
  // matching the original's AssistantThinkingMessage (hidden unless expanded).
  const { cb, lastFrame } = mount()
  await wait(150)
  cb().onData(thinkingDelta('hidden chain of thought'))
  await wait(60)
  cb().onData(textDelta('the answer')) // text start → commit reasoning, collapsed
  await wait(150)
  const f = strip(lastFrame())
  assert.match(f, /∴ Thinking \(ctrl\+o to expand\)/)
  assert.doesNotMatch(f, /hidden chain of thought/)
})

test('thinking-only turn still commits the reasoning at result', async () => {
  const { cb, lastFrame } = mount()
  await wait(150)
  cb().onData(thinkingDelta('lonely reasoning'))
  await wait(60)
  cb().onData(JSON.stringify({ type: 'result', subtype: 'success', session_id: 's', num_turns: 1, usage: {} }) + '\n')
  await wait(140)
  assert.match(strip(lastFrame()), /∴ Thinking \(ctrl\+o to expand\)/) // preserved, not discarded
})

test('multi-step turn: reasoning commits before the tool call (in order)', async () => {
  const { cb, lastFrame } = mount()
  await wait(150)
  cb().onData(thinkingDelta('REASONMARK'))
  await wait(60)
  cb().onData(JSON.stringify({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'Bash', input: { command: 'echo TOOLMARK' } }] } }) + '\n')
  await wait(160)
  const f = strip(lastFrame())
  const iThink = f.indexOf('∴ Thinking')
  const iTool = f.indexOf('TOOLMARK')
  assert.ok(iThink >= 0 && iTool >= 0 && iThink < iTool, `reasoning must precede the tool (think@${iThink} tool@${iTool})`)
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

test('RTL: the renderer shapes Hebrew runs to visual order natively', async () => {
  // The cell-diff renderer has built-in bidi, so RTL runs are reordered to visual
  // (reversed) order automatically — no manual /rtl toggle / shapeRtl needed.
  const heb = 'שלום'
  const { cb, lastFrame } = mount()
  await wait(150)
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

test('web search results collapse to a one-line summary (not a content dump)', async () => {
  const { cb, lastFrame } = mount()
  await wait(150)
  const huge = 'Links: ' + JSON.stringify(Array.from({ length: 40 }, (_, i) => ({ title: 'R' + i, url: 'https://e.com/' + i, content: 'Lorem ipsum '.repeat(20) })))
  const content = `Web search results for query: "obama achievements"\n\n${huge}\n\nREMINDER: include sources.`
  cb().onData(JSON.stringify({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content }] } }) + '\n')
  await wait(100)
  const f = strip(lastFrame())
  assert.match(f, /🔍 web search: obama achievements \(ctrl\+o to expand\)/)
  assert.ok(!f.includes('Lorem ipsum'), 'raw web-search content must not be dumped into the transcript')
})

test('slash menu is windowed (not a ~70-row dump) and closes after running a command', async () => {
  const { type, stdin, lastFrame } = mount()
  await wait(150)
  await type('/')
  await wait(60)
  const open = strip(lastFrame())
  assert.match(open, /\d+ more/, 'long command list should window with a "N more" indicator')
  const rows = (open.match(/^\s*›?\s*\/[a-z-]+/gim) || []).length
  assert.ok(rows > 0 && rows <= 12, `menu should cap visible rows, got ${rows}`)
  // running a command must close the menu (the pinned-menu bug)
  await type('vim')
  stdin.write('\r')
  await wait(60)
  assert.ok(!/\d+ more/.test(strip(lastFrame())), 'menu must close after a command is run')
})

test('/exit runs the quit handler (closes the connection and exits)', async () => {
  let closed = false
  const transport = { async start(c) { c.onOpen && c.onOpen(); c.onData(INIT) }, send() {}, close() { closed = true } }
  const r = render(React.createElement(App, { transport, serverLabel: 'test' }))
  await wait(150)
  for (const ch of '/exit') { r.stdin.write(ch); await wait(5) }
  r.stdin.write('\r')
  await wait(120)
  assert.ok(closed, '/exit should close the connection via the quit handler')
  try { r.unmount() } catch { /* exit() may have already torn down */ }
})

test('terminal resize is handled by the renderer (no crash, input still renders)', async () => {
  // The cell-diff renderer owns resize reflow — clawcodex no longer needs the
  // manual screen-clear workaround standard ink required (which left stacked input
  // boxes). A resize must not crash and the live input must remain on screen.
  const { stdout, lastFrame } = mount()
  await wait(150)
  stdout.emit('resize')
  await wait(120)
  assert.match(strip(lastFrame()), /Type a message|❯/)
})

test('file index pre-warms async so file search never blocks input', async () => {
  const { prewarmFileIndex, searchFiles } = await import('../dist/fileIndex.js')
  await prewarmFileIndex(process.cwd()) // async walk off the render path
  const t0 = performance.now()
  const files = searchFiles(process.cwd(), 'App', Date.now())
  const dt = performance.now() - t0
  assert.ok(files.length > 0, 'should find files after prewarm')
  assert.ok(dt < 50, `cached file search must be instant (no cold walk on the keystroke), took ${dt.toFixed(1)}ms`)
})

test('permission dialog supports arrow navigation + Enter to select', async () => {
  const responses = []
  let cbRef
  const transport = {
    async start(c) { cbRef = c; c.onOpen && c.onOpen(); c.onData(INIT) },
    send(d) { try { const m = JSON.parse(d); if (m.type === 'control_response' && m.response?.response?.behavior) responses.push(m.response.response) } catch { /* ignore */ } },
    close() {},
  }
  const r = render(React.createElement(App, { transport, serverLabel: 'test' }))
  await wait(150)
  cbRef.onData(JSON.stringify({ type: 'control_request', request_id: 'p1', request: { subtype: 'can_use_tool', tool_name: 'Bash', input: { command: 'ls -la' } } }) + '\n')
  await wait(100)
  assert.match(strip(r.lastFrame()), /❯\s*1\. Yes/, 'defaults to the Yes option')
  const DOWN = String.fromCharCode(27) + '[B'
  r.stdin.write(DOWN); await wait(25); r.stdin.write(DOWN); await wait(40)
  assert.match(strip(r.lastFrame()), /❯\s*3\. No/, 'down-arrow moves the highlight to No')
  r.stdin.write('\r'); await wait(120)
  assert.equal(responses.at(-1)?.behavior, 'deny', 'Enter selects the highlighted option (No → deny)')
  try { r.unmount() } catch { /* ignore */ }
})
