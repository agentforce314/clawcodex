/**
 * Lightweight file index for `@`-mention autocomplete (the original's
 * @-typeahead over files). A bounded recursive walk of the cwd, cached and
 * filtered in-memory so keystrokes stay responsive — heavy/vendor dirs are
 * skipped, results are capped.
 */
import { readdirSync } from 'node:fs'
import { join, relative } from 'node:path'

const IGNORE_DIRS = new Set([
  'node_modules',
  'dist',
  'build',
  'out',
  'coverage',
  'target',
  '__pycache__',
  '.next',
  '.cache',
  '.port_sessions',
  'venv',
  '.venv',
])
const MAX_FILES = 12_000
const STALE_MS = 15_000
const MAX_RESULTS = 10

interface Cache {
  cwd: string
  at: number
  files: string[]
}
let cache: Cache | null = null

function walk(root: string): string[] {
  const out: string[] = []
  const stack: string[] = [root]
  while (stack.length > 0 && out.length < MAX_FILES) {
    const dir = stack.pop() as string
    let entries
    try {
      entries = readdirSync(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const e of entries) {
      if (e.isDirectory()) {
        if (e.name.startsWith('.') || IGNORE_DIRS.has(e.name)) continue
        stack.push(join(dir, e.name))
      } else if (e.isFile()) {
        out.push(relative(root, join(dir, e.name)))
        if (out.length >= MAX_FILES) break
      }
    }
  }
  return out
}

function getFiles(cwd: string, now: number): string[] {
  if (cache && cache.cwd === cwd && now - cache.at < STALE_MS) return cache.files
  const files = walk(cwd).sort()
  cache = { cwd, at: now, files }
  return files
}

function basename(p: string): string {
  const i = p.lastIndexOf('/')
  return i >= 0 ? p.slice(i + 1) : p
}

/**
 * Files matching `query` (case-insensitive), ranked: basename prefix >
 * basename substring > path substring, then by path length (shorter first).
 * Empty query returns the first results in sorted order.
 *
 * `now` is passed in (callers stamp Date.now()) so this stays pure for tests.
 */
export function searchFiles(cwd: string, query: string, now: number): string[] {
  const files = getFiles(cwd, now)
  const q = query.toLowerCase()
  if (!q) return files.slice(0, MAX_RESULTS)
  const scored: { path: string; rank: number }[] = []
  for (const path of files) {
    const lp = path.toLowerCase()
    const base = basename(lp)
    let rank = -1
    if (base.startsWith(q)) rank = 0
    else if (base.includes(q)) rank = 1
    else if (lp.includes(q)) rank = 2
    if (rank >= 0) scored.push({ path, rank })
  }
  scored.sort((a, b) => a.rank - b.rank || a.path.length - b.path.length || a.path.localeCompare(b.path))
  return scored.slice(0, MAX_RESULTS).map((s) => s.path)
}
