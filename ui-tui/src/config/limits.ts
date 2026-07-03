export const LARGE_PASTE = { lines: 5 }

export const LIVE_RENDER_MAX_CHARS = 16_000
export const LIVE_RENDER_MAX_LINES = 240

// Persisted verbose tool-trail blocks (Args/Result embedded in a completed
// tool line) are kept for the WHOLE session in transcript Msg.tools[] and
// rendered expanded by default, so a render-node tree is built for every one
// of up to MAX_HISTORY messages at once. Capping these to the live-render
// budget (16KB) let a heavy browser/large-output session retain ~12MB of
// strings that exploded into a few hundred MB of Ink nodes and silently OOM-
// killed the Node parent (→ stdin EOF, gateway death; issue #34095). Verbose
// blocks were then an always-expanded persisted view, so the cap had to be a
// tiny glance (800/12). Today they render ONLY behind the explicit ctrl+o
// expand toggle and raw retention is already bounded upstream
// (RESULT_RAW_MAX_CHARS), so the render cap can be a real expansion while
// still bounding the worst case: 40KB-browser-snapshot × dozens of calls
// stays ~an order of magnitude under the #34095 blast.
export const VERBOSE_TRAIL_MAX_CHARS = 16_000
export const VERBOSE_TRAIL_MAX_LINES = 200

export const LONG_MSG = 300
export const MAX_HISTORY = 800
export const THINKING_COT_MAX = 160

// Rows per wheel event (pre-accel). 1 keeps Ink's DECSTBM fast path live
// (each scroll < viewport-1) and produces smooth motion. wheelAccel.ts
// ramps this on sustained scrolls.
export const WHEEL_SCROLL_STEP = 1
