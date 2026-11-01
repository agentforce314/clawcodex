import http from 'node:http'

export const FINAL_REPLY = 'All done! Here is the complete summary of what I found.'
export const SIMPLE_REPLY = 'Hello from the mock inference server! The full boot chain is working.'
export const PREVIEW_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="2000" height="1000"><rect width="2000" height="1000" fill="#e0ece9"/><path d="M0 1000L2000 0" stroke="#137c6e" stroke-width="40"/><text x="100" y="220" font-size="100" fill="#143b34">ClawCodex image preview</text></svg>'

/** Deterministic OpenAI-compatible inference, with real Read tool execution by the agent. */
export async function startMockServer() {
  let toolIssued = false
  let releaseSteering!: () => void
  const steeringGate = new Promise<void>(resolve => { releaseSteering = resolve })
  let heldSteering = false
  const received: string[] = []
  const server = http.createServer(async (request, response) => {
    if (request.url === '/preview.svg') {
      response.setHeader('Content-Type', 'image/svg+xml')
      response.end(PREVIEW_SVG)
      return
    }
    if (request.url === '/v1/models') {
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({ data: [{ id: 'mock-model' }] }))
      return
    }
    let raw = ''
    for await (const chunk of request) raw += String(chunk)
    const body = JSON.parse(raw || '{}') as { stream?: boolean; tools?: { function: { name: string } }[]; messages?: { role: string; content: unknown }[] }
    received.push(JSON.stringify(body.messages ?? []))
    const input = JSON.stringify(body.messages ?? [])
    const steering = input.includes('E2E_STEER_TRIGGER') || input.includes('E2E_STEER_TOOL_TRIGGER')
    // The test holds inference until the button's RPC is acknowledged. No
    // timers: the follow-up must arrive while the original turn is active.
    if (steering && body.stream && !heldSteering) {
      heldSteering = true
      await steeringGate
    }
    const scripted = input.includes('E2E_INTERIM_TRIGGER') || input.includes('E2E_STEER_TOOL_TRIGGER')
    const readTool = body.tools?.find(entry => ['Read', 'read_file'].includes(entry.function.name))
    const tool = scripted && !toolIssued && readTool !== undefined
    if (tool) toolIssued = true
    const imageUrl = `http://${request.headers.host}/preview.svg`
    const text = tool ? 'I will read the project documentation.' : steering ?
      (input.includes('complex functions') ? 'The original task and complex functions are handled together.' : 'Finishing the original step.') : scripted ? FINAL_REPLY :
      JSON.stringify(body.messages ?? []).includes('E2E_IMAGE_TRIGGER') ?
        `![Workspace image](${imageUrl})\n\n[Inspect linked image](${imageUrl})` : SIMPLE_REPLY
    const calls = tool ? [{ id: 'read-project', type: 'function', function: { name: readTool?.function.name, arguments: JSON.stringify({ file_path: 'README.md' }) } }] : undefined
    const finish = tool ? 'tool_calls' : 'stop'
    const usage = { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 }
    if (!body.stream) {
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({ id: 'web-test', object: 'chat.completion', choices: [{ index: 0, message: { role: 'assistant', content: text, tool_calls: calls }, finish_reason: finish }], usage }))
      return
    }
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
    const send = (delta: object, finish_reason: string | null = null) => response.write(`data: ${JSON.stringify({
      id: 'web-test', object: 'chat.completion.chunk', choices: [{ index: 0, delta, finish_reason }], ...(finish_reason ? { usage } : {}),
    })}\n\n`)
    send({ role: 'assistant', reasoning_content: tool ? 'Inspect the workspace before responding.' : undefined })
    for (const word of text.match(/\S+\s*/g) ?? []) send({ content: word })
    if (calls) send({ tool_calls: calls.map((call, index) => ({ index, ...call })) })
    send({}, finish)
    response.end('data: [DONE]\n\n')
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('No model server port')
  return { url: `http://127.0.0.1:${address.port}`, received, releaseSteering,
    isSteeringHeld: () => heldSteering, close: () => new Promise<void>((resolve, reject) => {
    releaseSteering()
    server.closeAllConnections()
    server.close(error => error ? reject(error) : resolve())
  }) }
}
