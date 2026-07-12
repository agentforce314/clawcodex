import { spawnSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { basename, join, relative } from 'node:path'

import { resolveEditor } from './editor.js'

/**
 * /memory editor flow — port of openclaude's `commands/memory/memory.tsx`
 * (ensure-create + result message) and the spawn half of
 * `utils/promptEditor.ts#editFileInEditor`. The picker overlay hands a path
 * to `openMemoryFileInEditor`, which creates the file if missing, suspends
 * the TUI (alt-screen) around a blocking `$EDITOR` spawn, then reports the
 * TS-verbatim "Opened memory file at …" line and busts the backend's
 * memory-file cache so the next turn re-reads disk.
 */

/** TS `editFileInEditor`'s EDITOR_OVERRIDES — GUI editors that return before
 *  the file is closed unless told to wait. Keyed on the command basename
 *  (small deliberate superset of the TS exact-string match, so an absolute
 *  `$EDITOR=/usr/local/bin/code` also waits). Applied only to a bare command;
 *  user-supplied arguments are never second-guessed. */
const EDITOR_WAIT_FLAGS: Record<string, string> = {
  code: '-w',
  subl: '--wait'
}

/** Port of memory.tsx:23-41 — mkdir the config home when the target lives
 *  under it, then exclusive-create so existing content is preserved. */
export const ensureMemoryFile = (path: string, home: string = homedir()): void => {
  const configHome = join(home, '.clawcodex')

  if (path.startsWith(configHome)) {
    mkdirSync(configHome, { recursive: true })
  }

  try {
    writeFileSync(path, '', { encoding: 'utf8', flag: 'wx' })
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== 'EEXIST') {
      throw e
    }
  }
}

/** Port of memory.tsx:44-55 — which env var picked the editor, verbatim. */
export const editorHint = (env: NodeJS.ProcessEnv = process.env): string => {
  const source = env.VISUAL ? '$VISUAL' : env.EDITOR ? '$EDITOR' : ''
  const value = env.VISUAL || env.EDITOR || ''

  return source
    ? `> Using ${source}="${value}". To change editor, set $EDITOR or $VISUAL environment variable.`
    : '> To use a different editor, set the $EDITOR or $VISUAL environment variable.'
}

/** Port of `getRelativeMemoryPath` (MemoryUpdateNotification.tsx) — the
 *  shorter of the `~`- and `./`-relative spellings, else absolute. */
export const relativeMemoryPath = (path: string, cwd: string, home: string = homedir()): string => {
  const toHome = path.startsWith(home) ? `~${path.slice(home.length)}` : null
  const toCwd = path.startsWith(cwd) ? `./${relative(cwd, path)}` : null

  if (toHome && toCwd) {
    return toHome.length <= toCwd.length ? toHome : toCwd
  }

  return toHome ?? toCwd ?? path
}

/** Editor invocation argv for a memory file: `resolveEditor` plus the
 *  wait-flag override, with the file path appended. */
export const memoryEditorArgv = (path: string, env: NodeJS.ProcessEnv = process.env): string[] => {
  const argv = [...resolveEditor(env)]
  const wait = EDITOR_WAIT_FLAGS[basename(argv[0] ?? '')]

  if (wait && argv.length === 1) {
    argv.push(wait)
  }

  return [...argv, path]
}

export interface OpenMemoryFileDeps {
  cwd: string
  env?: NodeJS.ProcessEnv
  home?: string
  /** Post-edit backend sync (`memory.edited` RPC) — fire-and-forget. */
  notifyEdited: () => void
  spawn?: (cmd: string, args: string[]) => { error?: Error }
  suspend: (run: () => Promise<void>) => Promise<void>
  sys: (text: string) => void
}

export async function openMemoryFileInEditor(path: string, deps: OpenMemoryFileDeps): Promise<void> {
  const env = deps.env ?? process.env
  const home = deps.home ?? homedir()

  const run =
    deps.spawn ?? ((cmd: string, args: string[]) => spawnSync(cmd, args, { stdio: 'inherit' }))

  try {
    ensureMemoryFile(path, home)

    const [cmd, ...args] = memoryEditorArgv(path, env)
    let result: { error?: Error } = {}

    await deps.suspend(async () => {
      result = run(cmd!, args)
    })

    // A failed launch (ENOENT) means nothing was opened; a non-zero editor
    // exit is deliberately ignored, like memory.tsx ignoring EditorResult.
    if (result.error) {
      throw result.error
    }

    deps.notifyEdited()
    deps.sys(`Opened memory file at ${relativeMemoryPath(path, deps.cwd, home)}\n\n${editorHint(env)}`)
  } catch (error) {
    deps.sys(`Error opening memory file: ${error}`)
  }
}
