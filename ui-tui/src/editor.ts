/**
 * External-editor support (the original's Ctrl+G / Ctrl+X Ctrl+E, inventory §8):
 * open the current prompt in $VISUAL/$EDITOR, then read the edited text back.
 * Synchronous (execSync) so the editor owns the terminal while it runs — callers
 * drop raw mode around the call. Kept as a pure helper so it's unit-testable with
 * a non-interactive $EDITOR script.
 */
import { execSync } from 'node:child_process'
import { writeFileSync, readFileSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export function editInEditor(text: string): string {
  const editor = process.env['VISUAL'] || process.env['EDITOR'] || 'vi'
  const file = join(tmpdir(), `clawcodex-edit-${process.pid}.txt`)
  writeFileSync(file, text, 'utf8')
  try {
    execSync(`${editor} ${JSON.stringify(file)}`, { stdio: 'inherit' })
    // Editors conventionally leave a trailing newline; drop a single one.
    return readFileSync(file, 'utf8').replace(/\n$/, '')
  } finally {
    try {
      unlinkSync(file)
    } catch {
      /* ignore */
    }
  }
}
