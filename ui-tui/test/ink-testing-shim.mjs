// Minimal ink-testing-library replacement for the vendored cell-diff renderer.
// ink-testing-library@4 is hardwired to ink@5 internals, so we drive the new
// renderer directly: mount via renderSync over a FakeTty, deliver keystrokes by
// writing to a PassThrough stdin (the renderer reads via 'readable'+read()), and
// read the frame via the renderer's own lastFrameText() (plain-char screen dump).
// NOTE: lastFrame() returns plain text only — color/bold/dim are UNOBSERVABLE
// through this shim. Assert on content/layout, never on styling.
import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { renderSync } from '../dist/tui-renderer/entry-exports.js'

function makeStdout() {
  const s = new EventEmitter()
  s.isTTY = true
  s.columns = 100
  s.rows = 40
  s.write = (_chunk, cb) => {
    if (typeof cb === 'function') cb()
    return true
  }
  return s
}

function makeStdin() {
  // The renderer consumes stdin via 'readable' + read() (not 'data'), so the fake
  // stdin must be a real Readable: a PassThrough turns write(ch) into readable data.
  const s = new PassThrough()
  s.isTTY = true
  s.setRawMode = () => s
  s.ref = () => {}
  s.unref = () => {}
  return s
}

export function render(element) {
  const stdout = makeStdout()
  const stderr = makeStdout()
  const stdin = makeStdin()
  const instance = renderSync(element, {
    stdout,
    stderr,
    stdin,
    patchConsole: false,
    exitOnCtrlC: false,
  })
  return {
    lastFrame: () => instance.lastFrame(),
    frames: [], // not tracked; lastFrame() reads the live screen
    stdin,
    stdout,
    rerender: instance.rerender,
    unmount: () => instance.unmount(),
    cleanup: () => instance.cleanup?.(),
  }
}
