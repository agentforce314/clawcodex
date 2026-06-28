/**
 * Config validation (the original's InvalidConfigDialog / InvalidSettingsDialog /
 * ValidationErrorsList, §6): the per-feature loaders silently ignore malformed
 * JSON (so a typo'd config just stops working with no clue). This validates the
 * known config files up front and returns human-readable errors to surface.
 */
import { readFileSync, existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const CONFIG_FILES = ['keybindings.json', 'logo.json', 'trusted.json', 'trusted-mcp.json']

/** Parse errors for any present-but-malformed config file (empty = all valid). */
export function configErrors(): string[] {
  const errors: string[] = []
  for (const name of CONFIG_FILES) {
    const path = join(homedir(), '.clawcodex', name)
    if (!existsSync(path)) continue
    try {
      JSON.parse(readFileSync(path, 'utf8'))
    } catch (e) {
      errors.push(`${name}: ${(e as Error).message}`)
    }
  }
  return errors
}
