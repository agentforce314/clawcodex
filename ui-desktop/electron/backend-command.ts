// Backend subcommand routing for the desktop-managed ClawCodex process.
//
// The desktop app launches its own headless backend via `clawcodex serve`
// (src/entrypoints/serve_cli.py in the backend repo). `serve` shipped after
// some managed installs were cloned, so a stale runtime may not know the
// subcommand. There is no legacy fallback form — a runtime without `serve`
// cannot back the desktop at all — so detection exists to fail the launch
// with an actionable "update the runtime" error instead of letting the CLI
// misparse `serve` as a free-form prompt.
//
// These helpers are pure so they can be unit-tested without Electron.

/**
 * Build the canonical headless backend argv (always `serve`).
 * @param {string} [profile] optional ClawCodex profile to pin via `--profile`.
 */
export function serveBackendArgs(profile?: string) {
  const head = profile ? ['--profile', profile] : []

  return [...head, 'serve', '--host', '127.0.0.1', '--port', '0']
}

/**
 * True when a runtime's `src/cli.py` source routes the `serve` subcommand.
 * Matches the subcommand sieve (`token == 'serve'` / `token == "serve"`)
 * specifically so substrings like "server" (e.g. `agent-server`,
 * "web server") never produce a false positive.
 */
export function sourceDeclaresServe(cliPySource) {
  return /token\s*==\s*["']serve["']/.test(String(cliPySource || ''))
}
