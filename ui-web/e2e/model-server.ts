import http from 'node:http'

export const FINAL_REPLY = 'All done! Here is the complete summary of what I found.'
export const SIMPLE_REPLY = 'Hello from the mock inference server! The full boot chain is working.'

/** Deterministic OpenAI-compatible inference, with real Read tool execution by the agent. */
export async function startMockServer() {
  let toolIssued = false
  const server = http.createServer(async (request, response) => {
    if (request.url === '/v1/models') {
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({ data: [{ id: 'mock-model' }] }))
      return
    }
    let raw = ''
    for await (const chunk of request) raw += String(chunk)
    const body = JSON.parse(raw || '{}') as { stream?: boolean; tools?: { function: { name: string } }[]; messages?: { role: string; content: unknown }[] }
    const scripted = JSON.stringify(body.messages ?? []).includes('E2E_INTERIM_TRIGGER')
    const readTool = body.tools?.find(entry => ['Read', 'read_file'].includes(entry.function.name))
    const tool = scripted && !toolIssued && readTool !== undefined
    if (tool) toolIssued = true
    const text = tool ? 'I will read the project documentation.' : scripted ? FINAL_REPLY : SIMPLE_REPLY
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
  return { url: `http://127.0.0.1:${address.port}`, close: () => new Promise<void>((resolve, reject) => {
    server.closeAllConnections()
    server.close(error => error ? reject(error) : resolve())
  }) }
}
