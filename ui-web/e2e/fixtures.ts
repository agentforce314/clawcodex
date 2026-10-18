import { test as base, expect } from '@playwright/test'
import { spawn } from 'node:child_process'
import { existsSync, appendFileSync } from 'node:fs'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { startMockServer, PREVIEW_SVG } from './model-server'

const repo = path.resolve(import.meta.dirname, '../..')
const python = process.env.CLAWCODEX_TEST_PYTHON ?? (existsSync(path.join(repo, '.venv/bin/python')) ? path.join(repo, '.venv/bin/python') : 'python3')

/** Real Python gateway and agent loop; only model inference is replaced. */
export const test = base.extend<{ app: { url: string; workspace: string; received: string[] } }>({
  app: async ({ page }, use, testInfo) => {
    const temp = await mkdtemp(path.join(tmpdir(), 'clawcodex-web-e2e-'))
    const config = path.join(temp, 'config')
    const workspace = path.join(temp, 'project')
    await mkdir(config)
    await mkdir(workspace)
    const mock = await startMockServer()
    await writeFile(path.join(config, 'config.json'), JSON.stringify({
      default_provider: 'deepseek',
      providers: { deepseek: { name: 'DeepSeek', base_url: `${mock.url}/v1`, api_mode: 'chat_completions',
        api_key: 'e2e-key', key_env: 'DEEPSEEK_API_KEY', default_model: 'deepseek-v4-flash', context_length: 32768,
        models: { 'mock-model': {}, 'mock-fast': {}, 'mock-pro': {}, 'mock-code': {}, 'mock-mini': {} } } },
    }))
    await writeFile(path.join(workspace, 'README.md'), '---\ntitle: Preview document\ntags: [web, port]\n---\n# Preview body\n\nA document served by the ClawCodex gateway.\n')
    await writeFile(path.join(workspace, 'example.txt'), 'A file from the real workspace.\n')
    await writeFile(path.join(workspace, 'preview.svg'), PREVIEW_SVG)
    const env: NodeJS.ProcessEnv = {
      PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, TMPDIR: process.env.TMPDIR,
      PYTHONPATH: repo, PYTHONUNBUFFERED: '1', CLAWCODEX_DISABLE_LEGACY_MIGRATION: '1',
      CLAWCODEX_CONFIG_DIR: config, CLAWCODEX_WEB_DIST: path.join(repo, 'ui-web/dist'), DEEPSEEK_API_KEY: 'e2e-key',
    }
    const child = spawn(python, ['-m', 'src.entrypoints.serve_cli', '--port', '0', '--token', 'web-e2e',
      '--workspace', workspace, '--provider', 'deepseek', '--model', 'deepseek-v4-flash', '--dangerously-skip-permissions', '--exit-on-parent'],
      { cwd: workspace, env, stdio: ['pipe', 'pipe', 'pipe'] })
    let logs = ''
    let startupError: Error | undefined
    child.on('error', error => { startupError = error })
    const record = (chunk: Buffer) => { logs += String(chunk); appendFileSync(testInfo.outputPath('backend-live.log'), chunk) }
    child.stdout.on('data', record)
    child.stderr.on('data', record)
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    try {
      await expect.poll(() => {
        if (startupError) throw startupError
        if (child.exitCode !== null) throw new Error(`Backend exited: ${logs}`)
        return /CLAWCODEX_BACKEND_READY port=(\d+)/.exec(logs)?.[1]
      }, { timeout: 60_000 }).toBeTruthy()
      const port = /CLAWCODEX_BACKEND_READY port=(\d+)/.exec(logs)![1]
      const url = `http://127.0.0.1:${port}`
      await page.goto(url)
      await expect(page.getByRole('textbox', { name: 'Message ClawCodex' })).toBeVisible()
      await expect(page.getByText('Connected', { exact: true })).toBeVisible()
      await use({ url, workspace, received: mock.received })
      expect(errors).toEqual([])
    } finally {
      await testInfo.attach('backend.log', { body: logs, contentType: 'text/plain' })
      child.stdin.end()
      if (child.exitCode === null) {
        await new Promise<void>(resolve => {
          const timer = setTimeout(() => { child.kill('SIGKILL'); resolve() }, 5000)
          child.once('exit', () => { clearTimeout(timer); resolve() })
        })
      }
      await mock.close()
      await rm(temp, { recursive: true, force: true })
    }
  },
})
export { expect }
