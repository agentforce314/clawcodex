// clawcodex's TUI renders through a vendored cell-diff renderer (hermes-ink) that
// writes only changed terminal cells (~tens of bytes/keystroke) instead of standard
// ink@5's whole-live-region rewrite (~1KB/keystroke) — which caused multi-second
// input lag on slower terminals (Terminal.app). This barrel is the single seam:
// every app import of "ink" goes through here.
export * from './tui-renderer/entry-exports.js'
