"""Print the raw byte sequence your terminal sends for any keystroke.

Run this in the *exact* terminal where the REPL misbehaves:

    python scripts/diagnose_keys.py

It puts stdin in raw mode, echoes every byte it receives as hex, and
quits on Ctrl+C. Press the key combinations you want to test — the
hex output tells you unambiguously what the REPL sees.

Interpreting the output
-----------------------

* ``0d``                         — plain Enter (CR)
* ``0a``                         — Ctrl+J / LF
* ``1b 0d``                      — Alt/Meta/Option+Enter (Escape + CR)
* ``5c 0d``                      — ``\`` then Enter
* ``1b 5b 31 33 3b 32 75``       — ``ESC[13;2u``  (Kitty CSI u
                                     Shift+Enter — Kitty, WezTerm,
                                     Ghostty, iTerm2 with CSI u mode)
* ``1b 5b 32 37 3b 32 3b 31 33 7e`` — ``ESC[27;2;13~`` (xterm
                                     ``modifyOtherKeys`` Shift+Enter)

If Shift+Enter prints the same bytes as plain Enter (``0d``), your
terminal is not disambiguating them — that's a terminal-config
problem, not a REPL problem, and you need to add a terminal
keybinding to send something distinct (see README or the
``multiline-input-wsl`` section of the docs).

If Alt+Enter prints NOTHING, the terminal is intercepting the key
before it reaches the app (e.g. Windows Terminal binds Alt+Enter to
``toggleFullscreen`` by default) — same story, fix in the terminal
config.
"""

from __future__ import annotations

import os
import sys

TIMEOUT_S = 60


def _read_raw_unix() -> int:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        banner = (
            "\r\n"
            "\x1b[1mKey diagnostic — press any key combo to see its bytes.\x1b[0m\r\n"
            "Press \x1b[33mCtrl+C\x1b[0m to quit.\r\n"
            "\r\n"
        )
        sys.stdout.write(banner)
        sys.stdout.flush()

        count = 0
        while True:
            ch = os.read(fd, 64)
            if not ch:
                continue
            # Ctrl+C -> 0x03
            if ch == b"\x03":
                sys.stdout.write("\r\n(exit)\r\n")
                sys.stdout.flush()
                return count
            hex_bytes = " ".join(f"{b:02x}" for b in ch)
            safe = ch.decode("latin-1").replace("\x1b", "⎋")
            sys.stdout.write(f"\x1b[36m  {hex_bytes:<42}\x1b[0m  {safe!r}\r\n")
            sys.stdout.flush()
            count += 1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_raw_win() -> int:
    # Windows has no tty/termios; msvcrt gives us getch().
    import msvcrt  # type: ignore[import]

    print("Key diagnostic — press any key combo to see its bytes.")
    print("Press Ctrl+C to quit.\n")
    count = 0
    try:
        while True:
            ch = msvcrt.getch()
            if ch == b"\x03":
                print("(exit)")
                return count
            hex_bytes = " ".join(f"{b:02x}" for b in ch)
            print(f"  {hex_bytes:<42}  {ch!r}")
            count += 1
    except KeyboardInterrupt:
        print("(exit)")
        return count


def main() -> int:
    if os.name == "nt":
        _read_raw_win()
    else:
        _read_raw_unix()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
