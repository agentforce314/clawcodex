import assert from 'node:assert/strict'

import { test } from 'vitest'

import { serveBackendArgs, sourceDeclaresServe } from './backend-command'

test('serveBackendArgs builds a headless serve invocation', () => {
  assert.deepEqual(serveBackendArgs(), ['serve', '--host', '127.0.0.1', '--port', '0'])
})

test('serveBackendArgs pins a profile when provided', () => {
  assert.deepEqual(serveBackendArgs('worker'), ['--profile', 'worker', 'serve', '--host', '127.0.0.1', '--port', '0'])
})

test('sourceDeclaresServe detects the serve route in the CLI sieve', () => {
  assert.equal(sourceDeclaresServe("if token == 'serve':\n    return run_serve_subcommand(rest)"), true)
  assert.equal(sourceDeclaresServe('if token == "serve":'), true)
  assert.equal(sourceDeclaresServe('if token  ==  "serve" :'), true)
})

test('sourceDeclaresServe does not false-positive on the substring "server"', () => {
  const oldSource = `
    if token == 'agent-server':
        from src.entrypoints.agent_server_cli import run_agent_server_subcommand
        return run_agent_server_subcommand(rest)  # web server
  `

  assert.equal(sourceDeclaresServe(oldSource), false)
})
