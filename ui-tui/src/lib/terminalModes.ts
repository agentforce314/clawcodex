import { writeSync } from 'node:fs'

// Input/display modes a crashed-or-killed prior program may have left armed.
// These are safe to reset unconditionally — they don't touch the screen buffer.
const MODE_RESET_HEAD =
  "\x1b[0'z" + // DEC locator reporting
  "\x1b[0'{" + // selectable locator events
  '\x1b[?2029l' + // passive mouse
  '\x1b[?1016l' + // SGR-pixels mouse
  '\x1b[?1015l' + // urxvt decimal mouse
  '\x1b[?1006l' + // SGR mouse
  '\x1b[?1005l' + // UTF-8 extended mouse
  '\x1b[?1003l' + // any-motion mouse
  '\x1b[?1002l' + // button-motion mouse
  '\x1b[?1001l' + // highlight mouse
  '\x1b[?1000l' + // click mouse
  '\x1b[?9l' + // X10 mouse
  '\x1b[?1004l' + // focus events
  '\x1b[?2004l' // bracketed paste

// Leaving the alternate screen (rmcup). On Apple Terminal, rmcup WITHOUT a
// matching prior smcup homes the cursor to the top-left — which makes an inline
// (primary-screen) TUI paint its banner over existing scrollback on startup and
// drop the shell prompt onto the frame on exit. So this is gated: only emit it
// in fullscreen mode, where the renderer actually entered the alt screen.
const ALT_SCREEN_LEAVE = '\x1b[?1049l'

const MODE_RESET_TAIL =
  '\x1b[<u' + // kitty keyboard
  '\x1b[>4m' + // modifyOtherKeys
  '\x1b[0m' + // attributes
  '\x1b[?25h' // cursor visible

// The full reset (including the alt-screen leave), kept as the canonical
// fullscreen sequence and for back-compat with anything importing it.
export const TERMINAL_MODE_RESET = MODE_RESET_HEAD + ALT_SCREEN_LEAVE + MODE_RESET_TAIL

type ResettableStream = Pick<NodeJS.WriteStream, 'isTTY' | 'write'> & {
  fd?: number
}

/** Reset terminal input/display modes. Pass `leaveAltScreen` only in fullscreen
 *  mode — inline mode never entered the alt screen, and emitting rmcup there
 *  homes the cursor on Apple Terminal (causing startup/exit overlap). */
export function resetTerminalModes(stream: ResettableStream = process.stdout, leaveAltScreen = false): boolean {
  if (!stream.isTTY) {
    return false
  }

  const seq = MODE_RESET_HEAD + (leaveAltScreen ? ALT_SCREEN_LEAVE : '') + MODE_RESET_TAIL
  const fd = typeof stream.fd === 'number' ? stream.fd : stream === process.stdout ? 1 : undefined

  if (fd !== undefined) {
    try {
      writeSync(fd, seq)

      return true
    } catch {
      // Fall through to stream.write for mocked or unusual TTY streams.
    }
  }

  try {
    stream.write(seq)

    return true
  } catch {
    return false
  }
}
