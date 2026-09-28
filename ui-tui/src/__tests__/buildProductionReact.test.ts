import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

// The shipped bundle must carry React's production build. The development
// build's performance tracks call `performance.measure()` on every commit and
// Node never evicts those entries, so a long session's heap grew by GBs —
// a spinner alone leaked ~290 MB/hour.
describe('dist bundle', () => {
  const root = resolve(__dirname, '../..')
  const dir = mkdtempSync(join(tmpdir(), 'tui-build-'))
  const outfile = join(dir, 'entry.js')
  let code = ''

  beforeAll(() => {
    execFileSync(process.execPath, [join(root, 'scripts/build.mjs'), `--outfile=${outfile}`], {
      cwd: root,
      env: { ...process.env, NODE_ENV: '' },
      stdio: 'pipe'
    })
    code = readFileSync(outfile, 'utf8')
  }, 120_000)

  afterAll(() => rmSync(dir, { force: true, recursive: true }))

  it('bundles the production React, not the development build', () => {
    expect(code).toContain('react-reconciler.production')
    expect(code).not.toContain('react-reconciler.development')
    expect(code).not.toContain('performance.measure(')
  })

  it('leaves no runtime NODE_ENV switch for a library to fall back on', () => {
    expect(code).not.toContain('process.env.NODE_ENV')
  })
})
