// Screenshot-verification gallery: render key TUI features via ink-testing-library
// and save each rendered frame (the terminal "screenshot") to gallery/<name>.txt.
import React from 'react'
import { render } from 'ink-testing-library'
import { writeFileSync } from 'node:fs'
import { App } from '../dist/App.js'

const init = JSON.stringify({ type:'system', subtype:'init', session_id:'s', protocol_version:'0.1.0', model:'deepseek-v4-pro', permission_mode:'default', tools:['Bash','Read','Edit'] }) + '\n'
const wait = (ms) => new Promise(r => setTimeout(r, ms))
const log = (...x) => process.stderr.write(x.join(' ') + '\n')

function mk(onSend) {
  let cb
  const t = { async start(c){ cb=c; c.onOpen&&c.onOpen(); c.onData(init) }, send(d){ onSend && onSend(d, cb) }, close(){} }
  return { t, get cb(){ return cb } }
}

async function shot(name, steps, onSend) {
  const h = mk(onSend)
  const { stdin, lastFrame } = render(React.createElement(App, { transport: h.t, serverLabel: 'deepseek' }))
  await wait(160)
  await steps({ stdin, cb: () => h.cb, type: async (s) => { for (const c of s) { stdin.write(c); await wait(5) } }, key: (k) => stdin.write(k), wait })
  const frame = lastFrame() || ''
  writeFileSync(`gallery/${name}.txt`, frame)
  const plain = frame.replace(/\x1b\[[0-9;]*m/g, '')
  log(`✓ ${name} (${plain.split('\n').length} rows)`)
}

const results = []
// 1. Startup banner + gradient logo
await shot('01-banner', async () => {})
// 2. Slash command menu
await shot('02-slash-menu', async ({ type }) => { await type('/') ; await wait(60) })
// 3. @ file mention dropdown
await shot('03-at-mention', async ({ type }) => { await type('@src') ; await wait(120) })
// 4. Permission prompt with destructive-command warning
await shot('04-permission-destructive', async ({ cb }) => {
  cb().onData(JSON.stringify({ type:'control_request', request_id:'p1', request:{ subtype:'can_use_tool', tool_name:'Bash', input:{ command:'rm -rf /tmp/cache', description:'clean cache' } } }) + '\n'); await wait(120)
})
// 5. MCP elicitation form
await shot('05-mcp-elicitation', async ({ cb, type }) => {
  cb().onData(JSON.stringify({ type:'control_request', request_id:'e1', request:{ subtype:'mcp_elicitation', params:{ message:'Enter your GitHub username', requestedSchema:{ properties:{ username:{ type:'string' } } } } } }) + '\n'); await wait(100); await type('octocat'); await wait(60)
})
// 6. Live-streaming thinking
await shot('06-live-thinking', async ({ cb }) => {
  cb().onData(JSON.stringify({ type:'stream_event', session_id:'s', event:{ type:'content_block_delta', delta:{ type:'thinking_delta', thinking:'Let me trace the data flow through the agent loop and the provider stream to find where deltas are dropped.' } } }) + '\n'); await wait(120)
})
// 7. Queue priorities (busy + queued prompts)
await shot('07-queue-priorities', async ({ cb, type, key }) => {
  await type('refactor the parser'); key('\r'); await wait(60)
  await type('next: also add tests'); key('\r'); await wait(40)
  await type('update the docs'); key('\r'); await wait(60)
}, () => {})
// 8. Folder-trust first-run notice + logo palette change already in banner
await shot('08-model-picker', async ({ type, key }) => { await type('/model'); await wait(40); key('\r'); await wait(100) },
  (d, cb) => { try { const m = JSON.parse(d); if (m.request?.subtype === 'get_settings') cb.onData(JSON.stringify({ type:'control_response', response:{ request_id:m.request_id, subtype:'success', response:{ model:'deepseek-v4-pro', available_models:['deepseek-v4-pro','claude-opus-4-8','gpt-5'] } } }) + '\n') } catch {} })
// 9. RTL shaping (Hebrew)
await shot('09-rtl', async ({ type, key, cb }) => {
  await type('/rtl'); key('\r'); await wait(60)
  cb().onData(JSON.stringify({ type:'assistant', message:{ role:'assistant', content:[{ type:'text', text:'Shalom — שלום עולם — means hello world' }] } }) + '\n')
  cb().onData(JSON.stringify({ type:'result', subtype:'success', session_id:'s', num_turns:1, usage:{} }) + '\n'); await wait(120)
})
log('GALLERY DONE')
process.exit(0)
